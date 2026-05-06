import uuid

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint
from django.utils.translation import gettext_lazy as _

from cards.models import BusinessCard
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
