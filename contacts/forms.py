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
from django.core.exceptions import ValidationError
from django.forms.utils import ErrorList

from config.constants import PersonChangeReason

from .models import Contact, ContactSns
from .services.normalization import (
    normalize_department_title_branch,
    normalize_email,
    normalize_full_name,
    normalize_organization,
    normalize_phone_value,
    normalize_postal_code_by_country,
    normalize_rest_of_address_by_country,
)


class AppErrorList(ErrorList):
    """Django ErrorList の出力に既存 BEM クラス `app-form__error` を付与する。

    [性質] presentation 層クラス（DB 操作なし・副作用なし）

    Django デフォルトは `<ul class="errorlist">` を出力する。本クラスは super に
    `error_class="app-form__error"` を渡すことで `<ul class="errorlist app-form__error">`
    として出力させ、static/css/app.css の既存スタイル `.app-form__error`
    （赤系・小さい・強調）を `{{ form.<field>.errors }}` の自動出力に接続する。

    ContactBaseForm.error_class にセットすることで、ContactBaseForm を継承する
    全フォーム（ContactUpdateForm / ContactUpdateActiveForm / ContactCreateForm /
    ContactAddAdditionalRoleForm / MergeForm）に自動波及する。
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("error_class", "app-form__error")
        super().__init__(*args, **kwargs)


class ContactBaseForm(forms.ModelForm):
    """Contact フィールド定義を共通化する抽象基底クラス（仕様書 §11.6.2 / §11.6.4）。

    Contact のユーザー入力対象フィールド（Contact.UPDATABLE_FIELDS）のみを ModelForm の
    対象フィールドとする。UI 構造は持たない。

    直接インスタンス化せず、子 Form（ContactUpdateForm /
    ContactAddAdditionalRoleForm / ContactCreateForm / MergeForm）から継承して使う。

    [性質] presentation 層クラス（DB 操作なし・副作用なし、§11.6.3 設計原則）
    """

    # error_class は BaseForm.__init__ のデフォルト引数 error_class=ErrorList で
    # インスタンス属性として上書きされる。クラス変数だけでは波及しないので、
    # 下記の __init__ で kwargs に明示注入する（D-4d-1 第 3 弾 §2-5）。
    error_class = AppErrorList

    # 手動 Form 経路の正規化通し（仕様書 §11.9.5.1 / Phase D §3.4）。
    # 3 経路（OCR / 手動 Form / AJAX）が同じ normalization 純関数を共有し、入力経路に
    # よらず Contact に格納される値を一致させる。対象フィールドは Contact.UPDATABLE_FIELDS
    # のサブセット（Form に存在しないフィールドは正規化しない）。dict の値は素の関数
    # （クラス属性ではなく dict 値なので descriptor 束縛は起きず、そのまま呼べる）。
    _FIELD_NORMALIZERS = {
        "full_name": normalize_full_name,
        "organization": normalize_organization,
        "mobile_phone": normalize_phone_value,
        "personal_phone": normalize_phone_value,
        "personal_fax": normalize_phone_value,
        "org_phone": normalize_phone_value,
        "org_fax": normalize_phone_value,
        "email": normalize_email,
        "department": normalize_department_title_branch,
        "title": normalize_department_title_branch,
        "branch": normalize_department_title_branch,
    }

    # full_name 補助組み立て JS（Phase D §3.7）用の js- フック。氏名系 widget の class に
    # 付与し、static/js/app.js がこれらを検出して last_name/first_name/other_name_parts/
    # name_order の変更に追従して full_name を補助組み立てする。
    _NAME_COMPOSE_CLASSES = {
        "full_name": "js-name-full",
        "last_name": "js-name-last",
        "first_name": "js-name-first",
        "other_name_parts": "js-name-other",
        "name_order": "js-name-order",
    }

    class Meta:
        model = Contact
        fields = list(Contact.UPDATABLE_FIELDS)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("error_class", self.error_class)
        super().__init__(*args, **kwargs)
        # Phase E：address は UPDATABLE_FIELDS から除外され Form フィールドではなくなった。
        # 住所組み立ては Contact.save() が住所構成要素から自動で行う（§11.9.4 / Phase E §2）。
        self._tag_name_compose_widgets()

    def _tag_name_compose_widgets(self):
        """氏名系 widget に full_name 補助 JS 用の js- フッククラスを付与する（Phase D §3.7）。

        [性質] 副作用あり（widget.attrs['class'] を更新）。DB 操作なし

        既存クラス（app-input 等）に追記する。_apply_widget_classes より前に呼ばれても
        後で app-input が追記されるだけで衝突しない。
        """
        for field_name, js_class in self._NAME_COMPOSE_CLASSES.items():
            field = self.fields.get(field_name)
            if field is None:
                continue
            css = field.widget.attrs.get("class", "")
            if js_class not in css.split():
                field.widget.attrs["class"] = (css + " " + js_class).strip()

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
        confirmed_<field> にも付与される。チェックボックス・ラジオ・複数選択は
        付与すると <input> 側に当たって形が崩れるためスキップ（HIG v1.2 §4.1 の
        app-radio-toggle と整合）。
        """
        for field in self.fields.values():
            widget = field.widget
            if isinstance(
                widget,
                (
                    forms.CheckboxInput,
                    forms.RadioSelect,
                    forms.CheckboxSelectMultiple,
                ),
            ):
                continue
            css = widget.attrs.get("class", "")
            if "app-input" not in css.split():
                widget.attrs["class"] = (css + " app-input").strip()

    def clean(self):
        """フォーム値を normalization 純関数に通して正規化する（仕様書 §11.9.5.1 / Phase D §3.4）。

        [性質] presentation 層メソッド（DB 操作なし。cleaned_data を書き換える）

        Contact.UPDATABLE_FIELDS のうち ``_FIELD_NORMALIZERS`` に対応のあるフィールドのみ
        を正規化する。子 Form（ContactUpdateForm 等）が clean() をオーバーライドする場合も
        ``super().clean()`` 経由で本処理が先に走るため、正規化後の値に対して追加検証できる。
        """
        cleaned = super().clean()
        self._normalize_cleaned_fields(cleaned)
        self._normalize_address_by_country(cleaned)
        # address の組み立ては Contact.save() が住所構成要素から自動で行う（Phase E §3）。
        return cleaned

    def _normalize_address_by_country(self, cleaned):
        """postal_code / rest_of_address を country 別に正規化する（Phase D2）。

        [性質] 副作用あり（cleaned の postal_code / rest_of_address を更新）。DB 操作なし

        postal_code：JP/US＝数字のみ、_default（GB 等）＝英字保持（``normalize_postal_code_by_country``）。
        rest_of_address：JP＝全スペース除去、US/_default＝スペース保持（``normalize_rest_of_address_by_country``）。
        いずれも country 依存のため _FIELD_NORMALIZERS には載せず、country 確定後にここで処理する。
        空値はスキップ。
        """
        country = cleaned.get("country", "")
        if "postal_code" in self.fields and cleaned.get("postal_code"):
            cleaned["postal_code"] = normalize_postal_code_by_country(
                cleaned["postal_code"], country
            )
        if "rest_of_address" in self.fields and cleaned.get("rest_of_address"):
            cleaned["rest_of_address"] = normalize_rest_of_address_by_country(
                cleaned["rest_of_address"], country
            )

    def _normalize_cleaned_fields(self, cleaned):
        """cleaned_data の各フィールドを対応する normalization 純関数で正規化する。

        [性質] 副作用あり（cleaned dict を更新 / 検証失敗時 add_error）。DB 操作なし

        空値（None / ""）は正規化対象外（required は各フィールドの field 検証が担う）。
        normalize_full_name は空白のみ入力で ValidationError を送出するため、その場合は
        当該フィールドのエラーとして add_error に変換する（他の純関数は例外を投げない）。
        """
        for field_name, normalizer in self._FIELD_NORMALIZERS.items():
            if field_name not in self.fields or field_name not in cleaned:
                continue
            raw = cleaned.get(field_name)
            if raw in (None, ""):
                continue
            try:
                cleaned[field_name] = normalizer(raw)
            except ValidationError as exc:
                self.add_error(field_name, exc)

    def _require_salutation_name(self, cleaned):
        """salutation_name 必須化バリデーション（仕様書 §1.5.2 / Phase D §3.5）。

        [性質] 副作用あり（検証失敗時 add_error）。DB 操作なし

        手動入力 3 Form（Create / Update / UpdateActive）でのみ呼ぶ
        （ContactAddAdditionalRoleForm は対象外）。DB レベルは NULL 許容のまま
        （マイグレーション不要）、Form 層で空文字・空白のみ入力を禁止する。
        """
        value = (cleaned.get("salutation_name") or "").strip()
        if not value:
            self.add_error("salutation_name", "宛名は必須です。")


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

        # low/mid かつ未確認のフィールドにだけ確認トグルを動的追加（§11.6.2、HIG v1.2 §4.1）。
        # high 扱い（CFC レコードなしの疑似 high）と confirmed 済みは追加しない。
        # widget 差し替え型のトグル化：BooleanField のまま widget を RadioSelect に差し替えて
        # 「確認しました／未確認」の 2 ボタン表示。値は to_python が 'True'/'False' を bool に
        # 変換するので clean() の `if not cleaned.get(chk_name)` 判定はそのまま動く。
        confidences = self.target_contact.get_field_confidences()
        for field_name, conf in confidences.items():
            if conf.confidence in ("low", "mid") and conf.confirmed_at is None:
                self.fields[f"confirmed_{field_name}"] = forms.BooleanField(
                    required=False,
                    initial=False,
                    label=f"『{field_name}』フィールド",
                    widget=forms.RadioSelect(
                        choices=[(True, "確認しました"), (False, "未確認")],
                        attrs={"class": "app-radio-toggle"},
                    ),
                )

        self._apply_widget_classes()

    def clean(self):
        cleaned = super().clean()
        # salutation_name 必須化（仕様書 §1.5.2 / Phase D §3.5）。
        self._require_salutation_name(cleaned)
        # 動的追加した confirmed_<field> がすべて ON か検証（§11.7.1）。
        confidences = self.target_contact.get_field_confidences()
        for field_name, conf in confidences.items():
            if conf.confidence in ("low", "mid") and conf.confirmed_at is None:
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
        # 新規作成は国デフォルト JP（UI §2.2、未バインド表示用 initial）。
        self.fields["country"].initial = "JP"

    def clean(self):
        cleaned = super().clean()
        # salutation_name 必須化（仕様書 §1.5.2 / Phase D §3.5）。
        self._require_salutation_name(cleaned)
        return cleaned


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
        # 新規作成は国デフォルト JP（UI §2.2、未バインド表示用 initial）。
        self.fields["country"].initial = "JP"

    def clean(self):
        cleaned = super().clean()
        # salutation_name 必須化（仕様書 §1.5.2 / Phase F1 追加）。9 番別肩書追加で作る
        # active Contact もメール配信の宛名・敬称に使うため、Create/Update/UpdateActive と
        # 揃えて必須とする（Phase F1 で 12/13/10 番に続いて 9 番も対象化）。
        self._require_salutation_name(cleaned)
        return cleaned


# ======================================================================
# ContactSns 編集 UI（InlineFormSet）（v1.6.1 Phase F1、仕様書 §11.6.7）
# ======================================================================


class ContactSnsForm(forms.ModelForm):
    """ContactSns 1 レコードの編集フォーム（InlineFormSet の構成要素、§11.6.7）。

    [性質] presentation 層クラス（DB 操作なし・副作用なし）

    sns_type（choices）と sns_id（CharField、Django 標準で前後空白を strip）のみを編集
    対象とする。widget に既存 BEM クラス app-input を付与する（CLAUDE.md §7）。
    """

    class Meta:
        model = ContactSns
        fields = ["sns_type", "sns_id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("sns_type", "sns_id"):
            widget = self.fields[name].widget
            css = widget.attrs.get("class", "")
            if "app-input" not in css.split():
                widget.attrs["class"] = (css + " app-input").strip()
        self.fields["sns_id"].widget.attrs.setdefault(
            "placeholder", "ID・URL・アカウント名など"
        )


class _BaseContactSnsFormSet(forms.BaseInlineFormSet):
    """ContactSns InlineFormSet の基底。各子フォームに AppErrorList を配る（§11.6.6）。

    [性質] presentation 層クラス（DB 操作なし）

    BaseFormSet が _construct_form で error_class を各子フォームに渡すため、ここで
    error_class の既定を AppErrorList にすることで、行ごとのフィールドエラーにも
    app-form__error クラスが自動付与され、Contact 本体 Form と表示が統一される。
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("error_class", AppErrorList)
        super().__init__(*args, **kwargs)

    def validate_unique(self):
        """Django 標準の英語 unique エラーを抑止する（Phase F1 follow-up 不具合③）。

        [性質] presentation 層メソッド（DB 操作なし）

        BaseModelFormSet.clean() は validate_unique() を呼び、(sns_type, sns_id) の重複時に
        「sns_type と sns_id の重複したデータを…」という英語フィールド名混じりの定型
        メッセージを raise する。これが super().clean() 経由で先に発火し、本クラスの
        clean() の日本語メッセージへ到達しなかった。本メソッドを no-op 化して標準検証を
        止め、重複検証を clean() の日本語メッセージに一本化する（DB の UniqueConstraint は維持）。
        """
        return

    def clean(self):
        """送信行のうち非削除・有効な (sns_type, sns_id) の重複を弾く（§3.7.4 / §11.6.7）。

        [性質] presentation 層メソッド（DB 操作なし）

        同一 Contact 内の (sns_type, sns_id) 重複を本 clean で日本語メッセージとして検証する
        （DB の UniqueConstraint と二重で守る）。Django 標準の英語 unique 検証は
        validate_unique() の no-op 化で抑止済み。
        """
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            cleaned = getattr(form, "cleaned_data", None)
            if not cleaned or cleaned.get("DELETE"):
                continue
            sns_type = cleaned.get("sns_type")
            sns_id = (cleaned.get("sns_id") or "").strip()
            if not sns_type or not sns_id:
                continue
            key = (sns_type, sns_id)
            if key in seen:
                raise ValidationError(
                    "同じ種別・同じ ID の SNS が重複しています。"
                )
            seen.add(key)


# 既定の InlineFormSet クラス（extra=0）。初期表示行数を変える 9 番別肩書追加では
# build_contact_sns_formset() で extra を動的指定する。
ContactSnsFormSet = forms.inlineformset_factory(
    Contact,
    ContactSns,
    form=ContactSnsForm,
    formset=_BaseContactSnsFormSet,
    fields=["sns_type", "sns_id"],
    extra=0,
    can_delete=True,
    max_num=None,
)


def build_contact_sns_formset(*, data=None, instance=None, initial=None, prefix="sns"):
    """ContactSns InlineFormSet インスタンスを生成する（§11.6.7）。

    [性質] presentation 層関数（DB 操作なし。queryset 評価・保存は呼び出し側の責務）
    [入力] data: POST 辞書 or None / instance: 親 Contact or None /
           initial: list[dict]（GET 初期表示行、9 番別肩書で primary の SNS を引き継ぐ用途）/
           prefix: フォーム接頭辞（既定 "sns"）
    [出力] _BaseContactSnsFormSet インスタンス

    initial を渡すと、その件数だけ extra 行を確保して未保存の初期表示行とする
    （instance が未保存のため queryset は空 → initial が extra フォームに反映される）。
    error_class は _BaseContactSnsFormSet が AppErrorList を配る。
    """
    extra = len(initial) if initial else 0
    formset_cls = forms.inlineformset_factory(
        Contact,
        ContactSns,
        form=ContactSnsForm,
        formset=_BaseContactSnsFormSet,
        fields=["sns_type", "sns_id"],
        extra=extra,
        can_delete=True,
        max_num=None,
    )
    return formset_cls(data, instance=instance, initial=initial, prefix=prefix)
