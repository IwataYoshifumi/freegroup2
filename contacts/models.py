import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import CheckConstraint, Q, UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from cards.models import BusinessCard
from config.constants import DUPLICATE_CHECK_FIELDS
from persons.models import Person


class Contact(models.Model):
    """連絡先DB（仕様書 v1.4.2 §4.4）。1 名刺 = 1 Contact のスナップショット設計（§4.4.0）。"""

    class Status(models.TextChoices):
        """Contact のステータス（仕様書 §4.4.2 / 別表 C.10）。"""

        PRIMARY = "primary", _("主コンタクト")
        ACTIVE = "active", _("副コンタクト")
        INACTIVE = "inactive", _("非アクティブ")

    class NameOrder(models.TextChoices):
        """氏名の構成順序（仕様書 v1.6.0 別表 A.5 / OCR プロンプト §2 name ブロック）。"""

        LAST_FIRST = "last_first", _("姓・名")
        FIRST_LAST = "first_last", _("名・姓")
        SINGLE = "single", _("単一名")
        OTHER = "other", _("その他")

    class LegalEntityTypeCode(models.TextChoices):
        """法人格コード（仕様書 v1.6.0 別表 A.5 / OCR organization ブロック、10 値固定）。"""

        CP = "CP", _("一般企業")
        LLP = "LLP", _("有限責任事業組合等")
        GOV = "GOV", _("政府機関・自治体")
        NPO = "NPO", _("NPO・NGO")
        REL = "REL", _("宗教法人")
        EDU = "EDU", _("学校・教育機関")
        MED = "MED", _("医療法人・病院")
        PRO = "PRO", _("士業法人")
        IND = "IND", _("個人事業主")
        OTH = "OTH", _("その他")

    class LegalEntityTypePosition(models.TextChoices):
        """法人格の位置（仕様書 v1.6.0 別表 A.5 / OCR organization ブロック）。"""

        PRE = "Pre", _("前")
        POST = "Post", _("後")
        MID = "Mid", _("中間")
        # Phase C 追加：json_parser の 3 段階防御 粒度 C（値単位）で範囲外を受け止める受け皿。
        OTHER = "other", _("その他")

    class LanguageComposition(models.TextChoices):
        """名刺の言語構成（仕様書 v1.6.0 別表 A.5 / OCR metadata ブロック）。"""

        LOCAL_ONLY = "local_only", _("現地語のみ")
        ENGLISH_ONLY = "english_only", _("英語のみ")
        MIX_BILINGUAL = "mix_bilingual", _("2 言語併記")
        OTHER = "other", _("その他")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_card = models.OneToOneField(
        BusinessCard,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
    )
    previous_person = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    previous_status = models.CharField(max_length=20, null=True, blank=True)
    duplicate_checked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contacts_managed",
    )
    lang = models.CharField(max_length=10, default="ja", blank=True)
    postal_code = models.CharField(max_length=20, blank=True, default="")

    full_name = models.CharField(max_length=255, blank=True, default="")
    last_name = models.CharField(max_length=255, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    salutation_name = models.CharField(max_length=255, blank=True, default="")
    # v1.6.0 新規（氏名サブ）
    other_name_parts = models.CharField(max_length=255, blank=True, default="")
    name_order = models.CharField(
        max_length=20, choices=NameOrder.choices, blank=True, default=""
    )
    display_name = models.CharField(max_length=255, blank=True, default="")
    phonetic_name = models.CharField(max_length=255, blank=True, default="")
    alias_name = models.CharField(max_length=255, blank=True, default="")
    # v1.6.0 新規（敬称手動入力フラグ、Contact.save() の自動再計算スキップ判定用、§11.9.7）
    salutation_name_is_manual = models.BooleanField(default=False)

    organization = models.CharField(max_length=255, blank=True, default="")
    # v1.6.0 新規（組織サブ）
    org_core_name = models.CharField(max_length=255, blank=True, default="")
    legal_entity_type = models.CharField(max_length=50, blank=True, default="")
    legal_entity_type_code = models.CharField(
        max_length=10, choices=LegalEntityTypeCode.choices, blank=True, default=""
    )
    legal_entity_type_position = models.CharField(
        max_length=10, choices=LegalEntityTypePosition.choices, blank=True, default=""
    )
    org_domain_name = models.CharField(max_length=255, blank=True, default="")

    department = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    qualification = models.CharField(max_length=500, blank=True, default="")
    catchphrase = models.CharField(max_length=500, blank=True, default="")
    branch = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")
    # v1.6.0 新規（住所サブ、address は §11.9.4 compose_full_address で組み立てる完成形）
    country = models.CharField(max_length=2, blank=True, default="")
    region = models.CharField(max_length=100, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    rest_of_address = models.CharField(max_length=500, blank=True, default="")

    email = models.CharField(max_length=255, blank=True, default="")
    personal_phone = models.CharField(max_length=50, blank=True, default="")
    mobile_phone = models.CharField(max_length=50, blank=True, default="")
    personal_fax = models.CharField(max_length=50, blank=True, default="")
    # v1.6.0 新規（会社代表電話・FAX、v1.6.1 で CharField(50) 単一値に巻き戻し）
    org_phone = models.CharField(max_length=50, blank=True, default="")
    org_fax = models.CharField(max_length=50, blank=True, default="")
    website = models.CharField(max_length=500, blank=True, default="")

    # v1.6.1 で個別 SNS 5 件（twitter / linkedin / facebook / instagram / github）を廃止し、
    # ContactSns 別テーブルへ統合（仕様書 §4.4.4 / 別表 A.5.1）。
    # 編集 UI は Phase F1 で InlineFormSet 実装、マージ画面比較は Phase F2 で実装。

    notes = models.TextField(blank=True, default="")
    # v1.6.0 新規（OCR メタデータ・未分類）
    language_composition = models.CharField(
        max_length=20, choices=LanguageComposition.choices, blank=True, default=""
    )
    handwritten_text = models.CharField(max_length=500, blank=True, default="")
    other_printed_text = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["person"],
                condition=Q(status="primary"),
                name="unique_primary_contact_per_person",
            ),
        ]

    # Contact のユーザー入力対象フィールドのマスター定義（仕様書 §11.6.2）。
    # ContactBaseForm.Meta.fields はこの集合を参照する。Contact 側がマスター、
    # Form 側が参照という方向で確定（D ブロック Form 実装時に決定）。
    # AJAX 経由（Contact 詳細画面、§10.6.4 ケース 4）の update_field() / Form 経由の
    # fix() の双方で、修正可能フィールドの集合として共通利用する。システム管理
    # フィールド（status / previous_* / created_* / updated_* / person / business_card
    # / duplicate_checked_at）は含めない。
    # 除外（仕様書 OCR 統合版 §3.5 の判断軸）：
    #   - org_core_name / org_domain_name / legal_entity_type_code：他フィールドからの導出物
    #     （Phase B で derive_* 関数群が更新、ユーザー入力対象外）
    #   - language_composition：OCR 判定の事実（推測しない）
    #   - handwritten_text / other_printed_text：事実保存用（OCR の素材）
    #   - salutation_name_is_manual：View 層が経路に応じて自動セットするフラグ
    UPDATABLE_FIELDS = (
        # 名前系
        "full_name",
        "last_name",
        "first_name",
        "salutation_name",
        "other_name_parts",
        "name_order",
        "display_name",
        "phonetic_name",
        "alias_name",
        # 会社系
        "organization",
        "legal_entity_type",
        "legal_entity_type_position",
        "department",
        "title",
        "qualification",
        "catchphrase",
        "branch",
        # 連絡先系（住所）
        "postal_code",
        "country",
        "region",
        "city",
        "rest_of_address",
        # address は UPDATABLE_FIELDS に含めない（Phase E で除外）。Contact.save() が
        # 住所構成要素から自動 compose する完成形のため、ユーザー編集対象にしない（§11.9.4）。
        # 連絡先系（電話・メール）
        "email",
        "personal_phone",
        "mobile_phone",
        "personal_fax",
        "org_phone",
        "org_fax",
        "website",
        # SNS（v1.6.1 で個別 SNS 5 件を ContactSns 別テーブル化、UPDATABLE_FIELDS からも削除。
        # ContactSns の編集は Phase F1 で InlineFormSet 方式に変更予定、§11.6.7）
        # メモ・言語
        "notes",
        "lang",
    )

    def __str__(self):
        return self.full_name or f"Contact {self.id}"

    # ------------------------------------------------------------------
    # salutation_name 自動再計算（仕様書 §11.9.7 / OCR 統合版 §1.5.3）
    # ------------------------------------------------------------------

    # salutation_name の再計算トリガーとなる姓系フィールド（§1.2.3 / §1.5.3）。
    # これらが save() で変更された場合、is_manual=False なら compute_salutation_name を
    # 強制再実行する。DB フィールドは増やさず、__init__ で保持する初期値スナップショット
    # との比較で変更を検知する（マイグレーション不要）。
    _SALUTATION_SOURCE_FIELDS = ("last_name", "full_name", "lang", "name_order")

    # address 自動 compose（仕様書 §11.9.4 / Phase E §2）のトリガーとなる住所構成要素。
    # これらが save() で変更された場合、compose_full_address を呼んで self.address を組み立て
    # 直す。salutation_name と同じスナップショット方式で変更検知する（マイグレーション不要）。
    _ADDRESS_SOURCE_FIELDS = (
        "postal_code",
        "region",
        "city",
        "rest_of_address",
        "country",
        "lang",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._store_salutation_source_snapshot()
        self._store_address_source_snapshot()

    def _store_salutation_source_snapshot(self):
        """[性質] 副作用あり（インスタンス属性 _salutation_source_snapshot を更新）。

        姓系フィールドの現在値を保持する。__init__ 直後と save() 完了後に呼び、次回 save()
        での変更検知の基準とする。
        """
        self._salutation_source_snapshot = {
            field: getattr(self, field, None)
            for field in self._SALUTATION_SOURCE_FIELDS
        }

    def _salutation_source_changed(self):
        """[性質] 準関数（インスタンス属性の比較のみ・DB 操作なし）。

        前回スナップショット以降に姓系フィールド（last_name / full_name / lang /
        name_order）のいずれかが変更されたかを返す。
        """
        snapshot = getattr(self, "_salutation_source_snapshot", {})
        return any(
            snapshot.get(field) != getattr(self, field, None)
            for field in self._SALUTATION_SOURCE_FIELDS
        )

    def _store_address_source_snapshot(self):
        """[性質] 副作用あり（インスタンス属性 _address_source_snapshot を更新）。

        住所構成要素の現在値を保持する。__init__ 直後と save() 完了後に呼び、次回 save()
        での変更検知の基準とする。欠損時のデフォルトは空文字。
        """
        self._address_source_snapshot = {
            field: getattr(self, field, "") for field in self._ADDRESS_SOURCE_FIELDS
        }

    def _address_source_changed(self):
        """[性質] 準関数（インスタンス属性の比較のみ・DB 操作なし）。

        前回スナップショット以降に住所構成要素（postal_code / region / city /
        rest_of_address / country / lang）のいずれかが変更されたかを返す。
        """
        snapshot = getattr(self, "_address_source_snapshot", {})
        return any(
            snapshot.get(field, "") != getattr(self, field, "")
            for field in self._ADDRESS_SOURCE_FIELDS
        )

    def save(self, *args, **kwargs):
        """salutation_name の自動再計算を行ってから保存する（§11.9.7 / §1.5.3）。

        [性質] 副作用あり（DB書込：自身のフィールド + 補完時の ContactFieldConfidence）
        [入力] Django Model.save() と同一（update_fields 等）
        [出力] None

        salutation_name_is_manual=False のときのみ、以下の条件で compute_salutation_name
        を呼んで self.salutation_name を更新する（§1.5.3 の条件表）：
          - salutation_name が空 → 補完生成
          - salutation_name が値あり かつ 姓系フィールド変更あり → 強制再計算で上書き
          - salutation_name が値あり かつ 姓系フィールド変更なし → 何もしない
        is_manual=True のときは一切再計算しない（手動入力を尊重）。

        Phase C 方針修正（§1.8）：補完が走った場合、super().save() の後で salutation_name の
        ContactFieldConfidence を low で記録する。3 経路（OCR / 手動 Form / AJAX）で姓修正に
        伴う再計算が走るため、CFC 作成挙動を Contact.save() に責務集約する。OCR 経路の他
        フィールド CFC は json_parser → create_for_contact が担い、salutation_name はここに
        一本化する（ocr_pipeline 側は confidence_map から salutation_name を除外、二重作成回避）。

        update_fields 指定の限定 save で salutation_name を書き換えた場合は、DB に反映
        されるよう update_fields に salutation_name を追加する。
        """
        # 循環 import 回避のため遅延 import（normalization は Contact を import しない）
        from contacts.services.normalization import (
            compose_full_address,
            compute_salutation_name,
        )

        salutation_was_computed = False
        if not self.salutation_name_is_manual:
            if not self.salutation_name or self._salutation_source_changed():
                new_value = compute_salutation_name(self)
                if new_value != self.salutation_name:
                    self.salutation_name = new_value
                    salutation_was_computed = True
                    update_fields = kwargs.get("update_fields")
                    if update_fields is not None:
                        update_fields = set(update_fields)
                        update_fields.add("salutation_name")
                        kwargs["update_fields"] = update_fields

        # address の自動 compose（§11.9.4 / Phase E §2）。住所構成要素が変更された場合、
        # または address が空（新規作成等）の場合に compose_full_address で組み立て直す。
        # salutation_name と同じスナップショット方式。
        if not self.address or self._address_source_changed():
            new_address = compose_full_address(
                self.postal_code,
                self.region,
                self.city,
                self.rest_of_address,
                self.country,
                self.lang,
            )
            if new_address != self.address:
                self.address = new_address
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    update_fields = set(update_fields)
                    update_fields.add("address")
                    kwargs["update_fields"] = update_fields

        super().save(*args, **kwargs)
        self._store_salutation_source_snapshot()
        self._store_address_source_snapshot()

        # 補完が走ったときのみ salutation_name の CFC を low で記録（§1.8）。
        # 再計算で既に CFC がある場合は再作成しない（UniqueConstraint 違反回避。
        # 値が変わっても confidence メタは low のまま据え置きで業務上問題ない）。
        if salutation_was_computed:
            already_recorded = ContactFieldConfidence.objects.filter(
                contact=self, field_name="salutation_name"
            ).exists()
            if not already_recorded:
                ContactFieldConfidence.create_for_contact(
                    self, {"salutation_name": "low"}
                )

    # ------------------------------------------------------------------
    # インスタンスメソッド（仕様書 §10.5.1 / §10.5.2 / §10.5.3）
    # ------------------------------------------------------------------

    def fix(self, form: "ContactUpdateForm", user):  # noqa: F821 - forward reference
        """フォーム値で自身のフィールドを上書きし、ContactFieldConfidence を全 confirmed 化（§10.5.2）。

        [性質] 副作用あり（DB書込：自身のフィールド + ContactFieldConfidence の confirmed_at）
        [入力] form: ContactUpdateForm（pk なしの新規 Contact を返す get_update_contact() を持つ）
               user: 確認者（confirmed_by に記録）
        [出力] None
        [例外] ValueError（self.pk が None の場合）

        対象フィールドは ``self.UPDATABLE_FIELDS`` (32 フィールド、仕様書 §11.6.2 の
        Meta.fields と整合）。fix は Contact のユーザー入力対象フィールドすべてを上書き
        対象とする（仕様書 §10.5.2）。
        """
        if self.pk is None:
            raise ValueError(
                "contact.fix() requires a saved Contact (self.pk must not be None)."
            )

        new_contact = form.get_update_contact()

        with transaction.atomic():
            # 差分のあるフィールドのみ更新、限定 save
            changed_fields = []
            for field_name in self.UPDATABLE_FIELDS:
                old_value = getattr(self, field_name)
                new_value = getattr(new_contact, field_name)
                if old_value != new_value:
                    setattr(self, field_name, new_value)
                    changed_fields.append(field_name)

            if changed_fields:
                self.save(update_fields=changed_fields + ["updated_at"])

            # 全 ContactFieldConfidence（DB 上の low/mid レコード）を confirmed 化
            low_mid_field_names = list(
                ContactFieldConfidence.objects.filter(contact=self).values_list(
                    "field_name", flat=True
                )
            )
            if low_mid_field_names:
                ContactFieldConfidence.mark_fields_as_confirmed(
                    self, low_mid_field_names, user
                )

    def update_field(self, field_name, new_value, user):
        """1 フィールドを修正し、当該フィールドの ContactFieldConfidence を確認済み化する
        （§10.6.4 ケース 4、D-3a）。

        [性質] 副作用あり（DB 書込：自身のフィールド + ContactFieldConfidence の確認済み化
               + DUPLICATE_CHECK_FIELDS のときは pending DC の invalidated 化）
        [入力] field_name: str（UPDATABLE_FIELDS のいずれか）
               new_value: 任意（field_name の値型）
               user: 確認者（updated_by および ContactFieldConfidence.confirmed_by に記録）
        [出力] None
        [例外] ValueError（self.pk が None、または field_name が UPDATABLE_FIELDS に
               含まれない場合）

        AJAX 経由の個別フィールド修正・確認用。Contact 詳細画面から呼ばれる想定
        （仕様書 §10.6.4 ケース 4）。

        処理内容（1 トランザクション）：
          1. 値に差分があれば self に反映して限定 save
             （updated_by = user / updated_at は auto_now で更新）
          2. 当該 1 フィールドの ContactFieldConfidence を confirmed 化
             （low/mid CFC レコードがあれば。high 扱い（CFC レコードなし）なら no-op、
             §10.6.1 既存挙動）
          3. field_name が DUPLICATE_CHECK_FIELDS に含まれる場合のみ
             invalidate_pending_candidates(self) を呼ぶ（§12.7）

        contact.fix() との責務の違い：
          - fix(): 全 low/mid フィールドを confirmed 化（フォーム送信時、ケース 2）
          - update_field(): 当該 1 フィールドのみ confirmed 化（AJAX、ケース 4）

        ガード（指示書 §3.8）：保存済み Contact のみ受け付ける。Contact.id は
        UUIDField(default=uuid.uuid4) のため pk チェックでは判定できないので、
        `_state.adding` で「これから INSERT する未保存インスタンス」を検出する。
        """
        if self._state.adding or self.pk is None:
            raise ValueError(
                "contact.update_field() requires a saved Contact "
                "(must already exist in DB)."
            )
        if field_name not in self.UPDATABLE_FIELDS:
            raise ValueError(
                f"'{field_name}' is not an updatable field"
            )

        # 循環 import を避けるため遅延 import
        from contacts.services.normalization import normalize_field
        from duplicates.services.merge_executor import (
            invalidate_pending_candidates,
        )

        # salutation_name の空文字ガード（仕様書 §1.5.2、AJAX 経路でも必須を担保）。
        # 引数エラー（ValueError）ではなくデータバリデーション失敗（ValidationError）。
        if field_name == "salutation_name":
            if not isinstance(new_value, str) or not new_value.strip():
                raise ValidationError("salutation_name cannot be empty")

        # 3 経路共有の正規化基盤を通す（§11.9.3 / Phase E §6）。normalize_field は
        # full_name 空入力等で ValidationError を投げうるが、View 側で捕捉するため
        # ここでは try/except しない。
        new_value = normalize_field(field_name, new_value, self)

        with transaction.atomic():
            # 1. 値の差分があれば save
            old_value = getattr(self, field_name)
            if old_value != new_value:
                setattr(self, field_name, new_value)
                self.updated_by = user
                update_fields = [field_name, "updated_by", "updated_at"]
                # 仕様書 §1.5.4：salutation_name を AJAX で更新したら手動入力フラグを立てる
                # （以後 Contact.save() の自動再計算で上書きされないようにする）。
                # salutation_name_is_manual は UPDATABLE_FIELDS 外で field_name 経由では
                # 来ないため、ここで明示的にセットする（直接書き換え経路ではない）。
                if field_name == "salutation_name":
                    self.salutation_name_is_manual = True
                    update_fields.append("salutation_name_is_manual")
                self.save(update_fields=update_fields)

            # 2. 当該フィールドの ContactFieldConfidence を confirmed 化
            #    （DB 上に CFC レコードがないフィールドは no-op、§10.6.1）
            ContactFieldConfidence.mark_fields_as_confirmed(
                self, [field_name], user
            )

            # 3. DUPLICATE_CHECK_FIELDS のときのみ §12.7 を発火
            if field_name in DUPLICATE_CHECK_FIELDS:
                invalidate_pending_candidates(self)

    def get_field_confidences(self):
        """全フィールド分の ContactFieldConfidence インスタンス dict を返す（§10.5.3）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] なし
        [出力] dict[field_name -> ContactFieldConfidence インスタンス]

        DUPLICATE_CHECK_FIELDS 全 9 フィールドのキーを含む（§6.3 / §9.1）。
        DB に存在する low/medium はそのまま、それ以外は confidence='high' / pk=None の疑似インスタンス。
        実装は ContactFieldConfidence.get_for_contact(self) に委譲（§10.5.3 の責務分離）。
        """
        return ContactFieldConfidence.get_for_contact(self)

    def get_high_fields(self):
        """実質 high なフィールド集合を返す（§10.5.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] なし
        [出力] set[field_name]

        判定：confidence='high'（疑似 high）または confirmed_at != None（確認済み low/mid）
        """
        confidences = self.get_field_confidences()
        high = set()
        for field_name, conf in confidences.items():
            if conf.confidence == "high":
                high.add(field_name)
            elif conf.confirmed_at is not None:
                high.add(field_name)
        return high

    def is_all_field_confidence_high(self, fields=None):
        """全 high 判定（§10.5.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] fields: list[field_name] または None（None なら DUPLICATE_CHECK_FIELDS 全部）
        [出力] bool
        """
        if fields is None:
            fields = DUPLICATE_CHECK_FIELDS
        high_fields = self.get_high_fields()
        return all(f in high_fields for f in fields)


class ContactSns(models.Model):
    """Contact 配下の SNS アカウント（v1.6.1 新設、仕様書 §4.4.4 / 別表 A.5.1）。

    Contact 個別 SNS フィールド 5 件（twitter / instagram / github / linkedin / facebook）を
    廃止し本テーブルへ統合。1 Contact に対し複数の SNS アカウントを持てる
    （YouTuber 複数チャンネル、Twitter 個人/会社両方等）。

    設計根拠（v1.6.1 改訂サマリー #2）：
      - type 拡張に DB 変更不要
      - 複数チャンネル対応
      - 検索ニーズ（Contact.objects.filter(sns_accounts__sns_type="line") 等）に JOIN で対応
      - ユーザーが直接編集する JSONField を増やさない

    json_parser での生成・正規化（_SNS_TYPE_ALIASES）は Phase C、編集 UI（InlineFormSet）は
    Phase F1、マージ画面比較表示は Phase F2。本フェーズはモデル定義のみ。
    """

    class SnsType(models.TextChoices):
        """SNS 種別（8 種固定・小文字統一、仕様書 §4.4.4 / OCR 統合版 §3.7.2 / 別表 C.x）。"""

        TWITTER = "twitter", "Twitter"
        LINKEDIN = "linkedin", "LinkedIn"
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"
        GITHUB = "github", "GitHub"
        BLOG = "blog", "Blog"
        YOUTUBE = "youtube", "YouTube"
        LINE = "line", "LINE"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="sns_accounts",
    )
    sns_type = models.CharField(max_length=50, choices=SnsType.choices)
    sns_id = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["contact", "sns_type", "sns_id"],
                name="unique_contact_sns",
            ),
        ]

    def __str__(self):
        return f"{self.sns_type}:{self.sns_id}"


class ContactFieldConfidence(models.Model):
    """信頼度メタDB（仕様書 v1.4.2 §4.6 / v1.6.0 mid 統一）。

    OCR で取り込まれた Contact のフィールドのうち、low / mid のものだけレコード化する。
    high は記録対象外（疑似インスタンスとしてのみ生成、DB 保存しない、§4.6.1）。
    """

    class Confidence(models.TextChoices):
        """信頼度（仕様書 §4.6 / 別表 C.3）。high は記録対象外。v1.6.0 で medium → mid に統一。"""

        LOW = "low", _("低")
        MID = "mid", _("中")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="confidences",
    )
    field_name = models.CharField(max_length=50)
    confidence = models.CharField(max_length=10, choices=Confidence.choices)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["contact", "field_name"],
                name="unique_contact_field_name",
            ),
            CheckConstraint(
                condition=Q(confidence__in=["low", "mid"]),
                name="confidence_low_or_mid",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.confidence == "high":
            raise ValueError(
                "ContactFieldConfidence with confidence='high' must not be saved. "
                "high values are pseudo-instances only."
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.contact_id} {self.field_name} ({self.confidence})"

    # ------------------------------------------------------------------
    # クラスメソッド（仕様書 §10.6.1 / §10.6.2 / §10.6.3）
    # ------------------------------------------------------------------

    @classmethod
    def get_for_contact(cls, contact):
        """全フィールド分の ContactFieldConfidence インスタンス dict を返す（§10.5.3 / §10.6.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] contact: Contact
        [出力] dict[field_name -> ContactFieldConfidence インスタンス]

        DUPLICATE_CHECK_FIELDS 全 9 フィールドのキーを含む（§6.3 / §9.1）。
        DB に存在する low/mid はそのまま返し、存在しないフィールドは confidence='high' /
        pk=None の疑似インスタンスを生成する（保存しないこと、§4.6.1 / §10.6.2）。
        N+1 を避けるため DB クエリは 1 回のみ。
        """
        db_records = {
            cfc.field_name: cfc
            for cfc in cls.objects.filter(contact=contact)
        }
        result = {}
        for field_name in DUPLICATE_CHECK_FIELDS:
            if field_name in db_records:
                result[field_name] = db_records[field_name]
            else:
                # 疑似インスタンス：DB 保存しない、id を None に固定
                result[field_name] = cls(
                    id=None,
                    contact=contact,
                    field_name=field_name,
                    confidence="high",
                )
        return result

    @classmethod
    def create_for_contact(cls, contact, confidence_map):
        """OCR 結果の mid/low フィールドについて一括作成（§10.6.1 / §10.6.4 ケース 1）。

        [性質] 副作用あり（DB書込）
        [入力] contact: Contact（保存済み）
               confidence_map: dict[field_name -> 'low' / 'mid' / 'high']
        [出力] None

        confidence_map のうち 'low' / 'mid' のみ DB レコードを作成する。
        'high' は記録対象外（§4.6.1）、それ以外の値もスキップ。
        bulk_create を使うため save() オーバーライドはバイパスされるが、事前に high を除外して
        いるため CheckConstraint 違反は発生しない。
        """
        targets = [
            cls(
                contact=contact,
                field_name=field_name,
                confidence=confidence,
            )
            for field_name, confidence in confidence_map.items()
            if confidence in ("low", "mid")
        ]
        if targets:
            cls.objects.bulk_create(targets)

    @classmethod
    def mark_fields_as_confirmed(cls, contact, field_names, user):
        """指定フィールドを確認済み化（§10.6.1 / §10.6.4 ケース 2 / 3）。

        [性質] 副作用あり（DB書込：confirmed_at / confirmed_by の更新のみ）
        [入力] contact: Contact
               field_names: list[field_name]（更新対象、空リスト/None なら何もしない）
               user: 確認者
        [出力] None

        confidence の値は変更しない（low / mid のまま、§6.2 / §8.5.4）。
        既に confirmed 済みのレコードも上書き更新する。auto_now が QuerySet.update() で
        効かないため updated_at を明示更新する。
        """
        if not field_names:
            return
        now = timezone.now()
        cls.objects.filter(
            contact=contact,
            field_name__in=field_names,
        ).update(
            confirmed_at=now,
            confirmed_by=user,
            updated_at=now,
        )
