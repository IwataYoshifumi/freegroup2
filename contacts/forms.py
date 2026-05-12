"""contacts アプリの Form 層（仕様書 v1.4.2 §11.6 / §11.7）。

Form クラス階層（仕様書 §11.6.1）：

    ContactBaseForm（抽象）
      ├── ContactUpdateForm
      │     └── ContactUpdateActiveForm
      ├── ContactAddAdditionalRoleForm
      ├── ContactCreateForm
      └── MergeForm（未実装）

ContactBaseForm は Contact.UPDATABLE_FIELDS を Meta.fields として共通参照する
ModelForm 基底クラス。UI 構造を持たず、子 Form から継承して使う。

[性質] presentation 層モジュール（DB 操作なし・副作用なし、§11.6.3 設計原則）
"""

from django import forms

from config.constants import PersonChangeReason

from .models import Contact


class ContactBaseForm(forms.ModelForm):
    """Contact フィールド定義を共通化する抽象基底クラス（仕様書 §11.6.2 / §11.6.4）。

    Contact のユーザー入力対象フィールド（Contact.UPDATABLE_FIELDS）のみを ModelForm の
    対象フィールドとする。UI 構造は持たない。

    直接インスタンス化せず、子 Form（ContactUpdateForm /
    ContactAddAdditionalRoleForm / ContactCreateForm / MergeForm）から継承して使う。

    [性質] presentation 層クラス（DB 操作なし・副作用なし、§11.6.3 設計原則）
    """

    class Meta:
        model = Contact
        fields = list(Contact.UPDATABLE_FIELDS)

    def get_update_contact(self):
        """フォーム値を反映した未保存の Contact インスタンスを返す（仕様書 §11.6.5）。

        [性質] 純関数（DB 操作なし・副作用なし）
        [入力] なし（self.cleaned_data から読み取り）
        [出力] Contact（_state.adding=True / status・person 未設定 / 値はメモリ上のみ）

        Contact.UPDATABLE_FIELDS のキーのみ拾い `Contact(**data)` で新規インスタンスを返す。
        全派生 Form（ContactUpdateForm / ContactAddAdditionalRoleForm / ContactCreateForm /
        MergeForm）から共通利用される。呼び出し側（Contact.fix / View 直書き）の責務分離：
          - 値の反映 ＝ 本メソッドが返す Contact から読む
          - status / person / save ＝ 呼び出し側
        """
        data = {
            f: self.cleaned_data[f]
            for f in Contact.UPDATABLE_FIELDS
            if f in self.cleaned_data
        }
        return Contact(**data)

    def _apply_widget_classes(self):
        """app.css の app-input クラスを各 widget に付与（CLAUDE.md §7、UI 共通化）。

        [性質] 副作用あり（self.fields の widget.attrs を変更、DB 操作なし）

        Form の動的フィールド追加後に呼ぶことで change_reason / note /
        confirmed_<field> にも付与される。チェックボックスには付けない。
        """
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            css = widget.attrs.get("class", "")
            if "app-input" not in css.split():
                widget.attrs["class"] = (css + " app-input").strip()


class ContactUpdateForm(ContactBaseForm):
    """primary Contact 修正用 Form（仕様書 §11.6.2 / §11.7.1）。

    UpdatePrimaryContactView（12 番、未実装）と Execute_Merge_with_Updates（§9.4）
    で使う基底。Contact フィールド + change_reason + note + 動的 confirmed_<field>
    チェックボックスを束ねる。

    [性質] presentation 層クラス
    [入力] target_contact: Contact（必須、kwarg）
    """

    change_reason = forms.ChoiceField(
        choices=PersonChangeReason.choices,
        label="修正理由",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea,
        label="備考",
    )

    def __init__(self, *args, target_contact=None, **kwargs):
        if target_contact is None:
            raise TypeError(
                "ContactUpdateForm requires 'target_contact' keyword argument."
            )
        self.target_contact = target_contact

        # GET 時のフォーム表示用に target_contact の現在値を initial に埋める。
        # instance はあえて target_contact に揃えない（揃えると is_valid() の
        # _post_clean() で target_contact のメモリ上フィールドが cleaned_data で
        # 上書きされ、confirmed_field_names() の edited 判定と Contact.fix() の
        # 差分検出が両方とも壊れる）。Contact.fix() が target_contact を直接
        # 更新する責務なので、ModelForm の save() は使わない設計。
        merged_initial = {
            f: getattr(target_contact, f) for f in Contact.UPDATABLE_FIELDS
        }
        merged_initial.update(kwargs.pop("initial", {}) or {})
        kwargs["initial"] = merged_initial
        # 念のため、呼び出し側が instance を渡してきても受け取らない（新規 instance で起動）。
        kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)

        # low/mid かつ未確認のフィールドにだけ確認チェックボックスを動的追加（§11.6.2）。
        # high 扱い（CFC レコードなしの疑似 high）と confirmed 済みは追加しない。
        confidences = self.target_contact.get_field_confidences()
        for field_name, conf in confidences.items():
            if conf.confidence in ("low", "medium") and conf.confirmed_at is None:
                self.fields[f"confirmed_{field_name}"] = forms.BooleanField(
                    required=False,
                    label=f"『{field_name}』フィールドを確認しました",
                )

        self._apply_widget_classes()

    def clean(self):
        cleaned = super().clean()
        # 動的追加した confirmed_<field> がすべて ON か検証（§11.7.1）。
        confidences = self.target_contact.get_field_confidences()
        for field_name, conf in confidences.items():
            if conf.confidence in ("low", "medium") and conf.confirmed_at is None:
                chk_name = f"confirmed_{field_name}"
                if not cleaned.get(chk_name):
                    self.add_error(
                        chk_name,
                        f"『{field_name}』フィールドの確認チェックを ON にしてください",
                    )
        return cleaned

    def confirmed_field_names(self):
        """ユーザーが確認・編集したフィールド名リスト（仕様書 §11.6.2 / §9.4）。

        [性質] 純関数（DB 操作なし・副作用なし）
        [入力] なし（self.cleaned_data / self.target_contact から読み取り）
        [出力] list[str]（Contact.UPDATABLE_FIELDS のサブセット）

        判定基準（OR）：
          (1) confirmed_<field> チェックボックスが ON
          (2) フォーム送信値が target_contact の現在値と異なる（編集された）

        Execute_Merge_with_Updates で「ユーザーが触ったフィールドだけ confirmed 化」を
        行うため（§9.4）、この戻り値が mark_fields_as_confirmed に渡される想定。
        """
        confirmed = []
        for field_name in Contact.UPDATABLE_FIELDS:
            chk_on = bool(self.cleaned_data.get(f"confirmed_{field_name}"))
            current_value = getattr(self.target_contact, field_name)
            submitted_value = self.cleaned_data.get(field_name)
            edited = submitted_value != current_value
            if chk_on or edited:
                confirmed.append(field_name)
        return confirmed


class ContactUpdateActiveForm(ContactUpdateForm):
    """active Contact 修正用 Form（仕様書 §11.6.2）。

    UpdateActiveContactView（13 番）専用。primary 修正と異なり change_reason を
    持たない（active は別肩書等の付帯情報のため、人格的変化を伴わない、§11.6.2）。

    確認チェックボックスの動的追加 / clean / get_update_contact / confirmed_field_names
    の振る舞いは親クラスをそのまま継承する。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["change_reason"]


class ContactCreateForm(ContactBaseForm):
    """手動 Contact 新規作成画面用 Form（仕様書 §11.6.2 / §11.4.4、10 番 ContactCreateView）。

    OCR を経由しないユーザー直接入力のため、ContactFieldConfidence は作らない
    （全フィールド high 扱い、§10.6.4）。新規 Person 生成 + status / person FK の
    設定 + save() は View 側（ContactCreateView）の責務。本フォームは値の検証と
    get_update_contact() による未保存 Contact 生成までを担う（§11.6.5）。

    ContactAddAdditionalRoleForm との違い：
      - person を取らない（新規 Person を View 側で作るため）
      - 重複検出（find_duplicate_contacts）は View 側で実施

    [性質] presentation 層クラス（DB 操作なし・副作用なし、§11.6.3 設計原則）
    """

    def __init__(self, *args, **kwargs):
        # 新規 Contact 生成用なので instance は持たせない（View 側で
        # get_update_contact() の戻り値を加工して save する責務分離、§11.4.4）。
        kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        self._apply_widget_classes()


class ContactAddAdditionalRoleForm(ContactBaseForm):
    """別肩書追加用 Form（仕様書 §11.6.2、9 番 PersonAddAdditionalRoleView）。

    既存 active Person 配下に新規 active Contact を追加するための入力フォーム。
    Contact フィールド（UPDATABLE_FIELDS）のみを束ね、付帯フィールド
    （change_reason / note / confirmed_<field>）は持たない（§11.6.2）。

    新規 Contact は OCR を経由しないユーザー直接入力のため、全フィールドが
    high 信頼度として扱われる（ContactFieldConfidence は生成しない、§10.12 / §10.6.4）。
    status / person FK の設定と save() は View 側（PersonAddAdditionalRoleView.form_valid）
    の責務。本フォームは値の検証と get_update_contact() による未保存 Contact 生成までを担う。

    [性質] presentation 層クラス（DB 操作なし・副作用なし、§11.6.3 設計原則）
    [入力] person: Person（必須、kwarg。所属先の active Person、参照情報として保持）
    """

    def __init__(self, *args, person=None, **kwargs):
        if person is None:
            raise TypeError(
                "ContactAddAdditionalRoleForm requires 'person' keyword argument."
            )
        self.person = person
        # 新規 Contact 生成用なので instance は持たせない（View 側で
        # get_update_contact() の戻り値を加工して save する責務分離、§10.12）。
        kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        self._apply_widget_classes()
