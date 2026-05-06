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
    """連絡先DB（仕様書 v1.4.2 §4.4）。1 名刺 = 1 Contact のスナップショット設計（§4.4.0）。"""

    class Status(models.TextChoices):
        """Contact のステータス（仕様書 §4.4.2 / 別表 C.10）。"""

        PRIMARY = "primary", _("主コンタクト")
        ACTIVE = "active", _("副コンタクト")
        INACTIVE = "inactive", _("非アクティブ")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_card = models.OneToOneField(
        BusinessCard,
        on_delete=models.CASCADE,
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
    lang = models.CharField(max_length=10, default="ja")
    postal_code = models.CharField(max_length=20, blank=True, default="")

    full_name = models.CharField(max_length=255, blank=True, default="")
    last_name = models.CharField(max_length=255, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    salutation_name = models.CharField(max_length=255, blank=True, default="")

    company = models.CharField(max_length=255, blank=True, default="")
    department = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    qualification = models.CharField(max_length=500, blank=True, default="")
    catchphrase = models.CharField(max_length=500, blank=True, default="")
    branch = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")

    email = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    mobile = models.CharField(max_length=50, blank=True, default="")
    fax = models.CharField(max_length=50, blank=True, default="")
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

        対象フィールドは DUPLICATE_CHECK_FIELDS（9 フィールド、§6.3 / §9.1）。
        D ブロック（Form 実装）で ContactBaseForm.Meta.fields に切り替える予定。
        """
        if self.pk is None:
            raise ValueError(
                "contact.fix() requires a saved Contact (self.pk must not be None)."
            )

        new_contact = form.get_update_contact()

        with transaction.atomic():
            # 差分のあるフィールドのみ更新、限定 save
            changed_fields = []
            for field_name in DUPLICATE_CHECK_FIELDS:
                old_value = getattr(self, field_name)
                new_value = getattr(new_contact, field_name)
                if old_value != new_value:
                    setattr(self, field_name, new_value)
                    changed_fields.append(field_name)

            if changed_fields:
                self.save(update_fields=changed_fields + ["updated_at"])

            # 全 ContactFieldConfidence（DB 上の low/medium レコード）を confirmed 化
            low_mid_field_names = list(
                ContactFieldConfidence.objects.filter(contact=self).values_list(
                    "field_name", flat=True
                )
            )
            if low_mid_field_names:
                ContactFieldConfidence.mark_fields_as_confirmed(
                    self, low_mid_field_names, user
                )

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

        判定：confidence='high'（疑似 high）または confirmed_at != None（確認済み low/medium）
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
    """信頼度メタDB（仕様書 v1.4.2 §4.6）。

    OCR で取り込まれた Contact のフィールドのうち、low / medium のものだけレコード化する。
    high は記録対象外（疑似インスタンスとしてのみ生成、DB 保存しない、§4.6.1）。
    """

    class Confidence(models.TextChoices):
        """信頼度（仕様書 §4.6 / 別表 C.3）。high は記録対象外。"""

        LOW = "low", _("低")
        MEDIUM = "medium", _("中")

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
                condition=Q(confidence__in=["low", "medium"]),
                name="confidence_low_or_medium",
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
        DB に存在する low/medium はそのまま返し、存在しないフィールドは confidence='high' /
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
        """OCR 結果の medium/low フィールドについて一括作成（§10.6.1 / §10.6.4 ケース 1）。

        [性質] 副作用あり（DB書込）
        [入力] contact: Contact（保存済み）
               confidence_map: dict[field_name -> 'low' / 'medium' / 'high']
        [出力] None

        confidence_map のうち 'low' / 'medium' のみ DB レコードを作成する。
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
            if confidence in ("low", "medium")
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

        confidence の値は変更しない（low / medium のまま、§6.2 / §8.5.4）。
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
