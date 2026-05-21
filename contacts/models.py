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

    organization = models.CharField(max_length=255, blank=True, default="")
    department = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    qualification = models.CharField(max_length=500, blank=True, default="")
    catchphrase = models.CharField(max_length=500, blank=True, default="")
    branch = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")

    email = models.CharField(max_length=255, blank=True, default="")
    personal_phone = models.JSONField(default=list, blank=True)
    mobile_phone = models.CharField(max_length=50, blank=True, default="")
    personal_fax = models.JSONField(default=list, blank=True)
    website = models.CharField(max_length=500, blank=True, default="")

    twitter = models.CharField(max_length=255, blank=True, default="")
    linkedin = models.CharField(max_length=500, blank=True, default="")
    facebook = models.CharField(max_length=500, blank=True, default="")
    github = models.CharField(max_length=255, blank=True, default="")
    instagram = models.CharField(max_length=255, blank=True, default="")

    notes = models.TextField(blank=True, default="")

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
    UPDATABLE_FIELDS = (
        # 名前系
        "full_name",
        "last_name",
        "first_name",
        "salutation_name",
        # 会社系
        "organization",
        "department",
        "title",
        "qualification",
        "catchphrase",
        "branch",
        # 連絡先系
        "postal_code",
        "address",
        "email",
        "personal_phone",
        "mobile_phone",
        "personal_fax",
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

        対象フィールドは ``self.UPDATABLE_FIELDS`` (24 フィールド、仕様書 §11.6.2 の
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
