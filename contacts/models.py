import uuid

from django.conf import settings
from django.db import models, transaction
from django.db.models import CheckConstraint, Q, UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from cards.models import BusinessCard
from config.constants import DUPLICATE_CHECK_FIELDS
from persons.models import Person


class Contact(models.Model):
    """連絡先DB（仕様書 v1.6.0 §4.4 / 別表 A.5）。1 名刺 = 1 Contact のスナップショット設計（§4.4.0）。"""

    class Status(models.TextChoices):
        """Contact のステータス（仕様書 §4.4.2 / 別表 C.10）。"""

        PRIMARY = "primary", _("主コンタクト")
        ACTIVE = "active", _("副コンタクト")
        INACTIVE = "inactive", _("非アクティブ")

    class NameOrder(models.TextChoices):
        """姓名の並び順（仕様書 v1.6.0 別表 A.5 / OCR 統合版 §3.2 #1）。"""

        LAST_FIRST = "last_first", _("姓・名")
        FIRST_LAST = "first_last", _("名・姓")
        SINGLE = "single", _("単一名")
        OTHER = "other", _("その他")

    class LegalEntityTypeCode(models.TextChoices):
        """法人格コード（仕様書 v1.6.0 別表 A.5 / OCR 統合版 §3.2 #8）。
        legal_entity_type（自由文字列）から導出される分類コード。"""

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
        """法人格の表記位置（仕様書 v1.6.0 別表 A.5 / OCR 統合版 §3.2 #9）。"""

        PRE = "Pre", _("前")
        POST = "Post", _("後")
        MID = "Mid", _("中間")

    class LanguageComposition(models.TextChoices):
        """名刺の言語構成（仕様書 v1.6.0 別表 A.5 / OCR 統合版 §3.2 #15）。"""

        LOCAL_ONLY = "local_only", _("現地語のみ")
        ENGLISH_ONLY = "english_only", _("英語のみ")
        MIX_BILINGUAL = "mix_bilingual", _("2言語併記")
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

    # ---- 名前系 ----
    full_name = models.CharField(max_length=255, blank=True, default="")
    last_name = models.CharField(max_length=255, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    salutation_name = models.CharField(max_length=255, blank=True, default="")
    # v1.6.0 新規（仕様書 別表 A.5 / OCR 統合版 §3.2）
    name_order = models.CharField(
        max_length=20, choices=NameOrder.choices, blank=True, default=""
    )
    other_name_parts = models.CharField(max_length=255, blank=True, default="")
    display_name = models.CharField(max_length=255, blank=True, default="")
    phonetic_name = models.CharField(max_length=255, blank=True, default="")
    alias_name = models.CharField(max_length=255, blank=True, default="")
    # salutation_name 手動入力フラグ。OCR 経路では False のまま。
    # True のとき Contact.save() の自動再計算で salutation_name を上書きしない
    # （仕様書 §11.9.7、Phase 2 で save() オーバーライド時に活用）。
    salutation_name_is_manual = models.BooleanField(default=False)

    # ---- 会社系 ----
    organization = models.CharField(max_length=255, blank=True, default="")
    # v1.6.0 新規（仕様書 別表 A.5 / OCR 統合版 §3.2）
    # 法人格関連は org_core_name / legal_entity_type / *_code / *_position が連動して
    # organization を表す（Phase 2 の derive_org_core_name 等で整合維持）。
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

    # ---- 住所系 ----
    address = models.CharField(max_length=500, blank=True, default="")
    # v1.6.0 新規（仕様書 別表 A.5 / OCR 統合版 §3.2）
    # address は 4 要素（country / region / city / rest_of_address）から
    # Phase 2 の compose_full_address が組み立てた結果を格納する設計。
    country = models.CharField(max_length=2, blank=True, default="")  # ISO 3166-1 alpha-2
    region = models.CharField(max_length=100, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    rest_of_address = models.CharField(max_length=500, blank=True, default="")

    # ---- 連絡先系 ----
    email = models.CharField(max_length=255, blank=True, default="")
    personal_phone = models.JSONField(default=list, blank=True)
    mobile_phone = models.CharField(max_length=50, blank=True, default="")
    personal_fax = models.JSONField(default=list, blank=True)
    # v1.6.0 新規（仕様書 別表 A.5 / OCR 統合版 §3.2）：会社代表・部署電話 / FAX の E.164 配列。
    # DUPLICATE_CHECK_FIELDS には含めない（同一会社内の複数人で同値、別人誤マージ防止、§7.1）。
    org_phone = models.JSONField(default=list, blank=True)
    org_fax = models.JSONField(default=list, blank=True)
    website = models.CharField(max_length=500, blank=True, default="")

    # ---- SNS ----
    twitter = models.CharField(max_length=255, blank=True, default="")
    linkedin = models.CharField(max_length=500, blank=True, default="")
    facebook = models.CharField(max_length=500, blank=True, default="")
    github = models.CharField(max_length=255, blank=True, default="")
    instagram = models.CharField(max_length=255, blank=True, default="")

    # ---- メモ・分類（事実保存系含む） ----
    notes = models.TextField(blank=True, default="")
    # v1.6.0 新規（仕様書 別表 A.5 / OCR 統合版 §3.2）
    # OCR が判定する名刺の言語構成（事実）。
    language_composition = models.CharField(
        max_length=20, choices=LanguageComposition.choices, blank=True, default=""
    )
    # 名刺上の手書きメモ / catchphrase 以外の印字漏れテキスト。事実保存用、UI 編集対象外。
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

    # Contact のユーザー入力対象フィールドのマスター定義（仕様書 §11.6.2 / v1.6.0 OCR 統合版 §3.5）。
    # ContactBaseForm.Meta.fields はこの集合を参照する。Contact 側がマスター、
    # Form 側が参照という方向で確定（D ブロック Form 実装時に決定）。
    # AJAX 経由（Contact 詳細画面、§10.6.4 ケース 4）の update_field() / Form 経由の
    # fix() の双方で、修正可能フィールドの集合として共通利用する。システム管理
    # フィールド（status / previous_* / created_* / updated_* / person / business_card
    # / duplicate_checked_at）は含めない。
    #
    # v1.6.0 判断軸（仕様書 OCR 統合版 §3.5）：
    # 「他フィールドから生成される導出物は入れない（原文・構成要素を直せば追従）」
    # 入れない（導出・派生・事実保存・フラグ）：
    #   - org_core_name（org_name_full から導出、Phase 2 で derive_org_core_name が更新）
    #   - org_domain_name（email から導出、Phase 2 で derive_org_domain_name が更新）
    #   - legal_entity_type_code（legal_entity_type + position から導出）
    #   - language_composition（OCR 判定の事実）
    #   - handwritten_text / other_printed_text（事実保存用）
    #   - salutation_name_is_manual（View 層が経路に応じて自動セット、画面から直接編集させない）
    # ※ address は v1.6.0 仕様 §3.5 では「入れない」リストにあるが、Phase 1A 時点で
    #   既存 UPDATABLE に残っており、外すと Form/View 経由の編集 UI が壊れる。
    #   Phase 4（Form/View の JSONField 整合）と一緒に外す論点として持ち越し。
    UPDATABLE_FIELDS = (
        # 名前系
        "full_name",
        "last_name",
        "first_name",
        "salutation_name",
        "name_order",          # v1.6.0 新規
        "other_name_parts",    # v1.6.0 新規
        "display_name",        # v1.6.0 新規
        "phonetic_name",       # v1.6.0 新規
        "alias_name",          # v1.6.0 新規
        # 会社系
        "organization",
        "legal_entity_type",              # v1.6.0 新規
        "legal_entity_type_position",     # v1.6.0 新規
        "department",
        "title",
        "qualification",
        "catchphrase",
        "branch",
        # 住所系（address は導出物扱いだが Phase 1A 時点の挙動継続、Phase 4 で再検討）
        "postal_code",
        "address",
        "country",             # v1.6.0 新規
        "region",              # v1.6.0 新規
        "city",                # v1.6.0 新規
        "rest_of_address",     # v1.6.0 新規
        # 連絡先系
        "email",
        "personal_phone",
        "mobile_phone",
        "personal_fax",
        "org_phone",           # v1.6.0 新規
        "org_fax",             # v1.6.0 新規
        "website",
        # SNS
        "twitter",
        "linkedin",
        "facebook",
        "github",
        "instagram",
        # メモ・言語
        "notes",
        "lang",
    )

    # compute_salutation_name の自動再計算トリガーとなる姓系フィールド集合
    # （仕様書 v1.6.0 OCR 統合版 §1.5.3「姓修正への追従」/ 本編 §11.9.7）。
    # save() 時に __init__ で記録した初期値と現在値を比較し、差分があれば
    # is_manual=False の場合に compute_salutation_name を強制再計算する。
    _SALUTATION_TRIGGER_FIELDS = ("last_name", "full_name", "lang", "name_order")

    def __init__(self, *args, **kwargs):
        """[性質] 副作用あり（インスタンス初期化）

        Contact.save() オーバーライドで「姓系フィールドが変更されたか」を判定するために、
        ロード時の初期値を `_initial_salutation_trigger_values` に保持する。
        Django の通常パターン（モデルインスタンスの初期値スナップショット）。
        """
        super().__init__(*args, **kwargs)
        self._initial_salutation_trigger_values = {
            f: getattr(self, f, None) for f in self._SALUTATION_TRIGGER_FIELDS
        }

    def save(self, *args, **kwargs):
        """[性質] 副作用あり（DB書込）

        v1.6.0 オーバーライド（仕様書 §1.5.3 / §11.9.7）。
        salutation_name_is_manual と姓系フィールド変更状況に応じて、
        compute_salutation_name で salutation_name を補完・再計算する。

        分岐：
          1) is_manual=True：何もしない（手動入力を尊重）
          2) is_manual=False かつ salutation_name が空：compute で生成
          3) is_manual=False かつ salutation_name が値あり：
             - 姓系フィールド（_SALUTATION_TRIGGER_FIELDS）に変更あり → compute で再計算
             - 姓系フィールド変更なし → 何もしない（前回 compute の結果を尊重）

        ContactFieldConfidence への記録ロジックは本メソッドに組み込まない。
        OCR 経路（Phase 3）の json_parser、Form/AJAX 経路（Phase 4）の View 層で
        各経路側が記録する責務分担とする。
        """
        # 循環 import 回避：関数内で遅延 import
        from contacts.services.normalization import compute_salutation_name

        if not self.salutation_name_is_manual:
            current_salutation = (self.salutation_name or "").strip()
            if not current_salutation:
                # ケース 2：空なら生成（compute が空文字を返した場合はそのまま空のまま）
                self.salutation_name = compute_salutation_name(self)
            else:
                # ケース 3：値ありなら姓系フィールド変更を検知
                trigger_changed = any(
                    getattr(self, f, None)
                    != self._initial_salutation_trigger_values.get(f)
                    for f in self._SALUTATION_TRIGGER_FIELDS
                )
                if trigger_changed:
                    self.salutation_name = compute_salutation_name(self)

        super().save(*args, **kwargs)

        # 保存後に初期値スナップショットを更新（次回 save() に備える）。
        self._initial_salutation_trigger_values = {
            f: getattr(self, f, None) for f in self._SALUTATION_TRIGGER_FIELDS
        }

    def __str__(self):
        return self.full_name or f"Contact {self.id}"

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

        対象フィールドは ``self.UPDATABLE_FIELDS`` （v1.6.0 で新規 13 件追加、計 37 フィールド、
        仕様書 §11.6.2 の Meta.fields と整合）。fix は Contact のユーザー入力対象フィールドすべてを上書き
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

        UPDATABLE_FIELDS 内の personal_phone / personal_fax / org_phone / org_fax は
        JSONField(default=list) のため、new_value は list を想定する（呼び出し側責務）。
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
             §10.6.1 既存挙動）。confidence の値域は v1.6.0 で medium → mid に統一済み。
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
        from duplicates.services.merge_executor import (
            invalidate_pending_candidates,
        )

        with transaction.atomic():
            # 1. 値の差分があれば save
            old_value = getattr(self, field_name)
            if old_value != new_value:
                setattr(self, field_name, new_value)
                self.updated_by = user
                self.save(
                    update_fields=[field_name, "updated_by", "updated_at"]
                )

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
        DB に存在する low/mid はそのまま、それ以外は confidence='high' / pk=None の疑似インスタンス。
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


class ContactFieldConfidence(models.Model):
    """信頼度メタDB（仕様書 v1.6.0 §4.6 / 別表 C.3）。

    OCR で取り込まれた Contact のフィールドのうち、low / mid のものだけレコード化する。
    high は記録対象外（疑似インスタンスとしてのみ生成、DB 保存しない、§4.6.1）。
    """

    class Confidence(models.TextChoices):
        """信頼度（仕様書 §4.6 / 別表 C.3）。high は記録対象外。v1.6.0 で medium → mid 統一。"""

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
