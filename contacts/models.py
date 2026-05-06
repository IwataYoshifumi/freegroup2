import uuid

from django.conf import settings
from django.db import models

from cards.models import BusinessCard
from persons.models import Person


class Contact(models.Model):
    """連絡先DB（仕様書 v1.1.0 §4.4）。1名刺=1Contact。配列フィールドは持たず、すべて単独 CharField。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_card = models.OneToOneField(
        BusinessCard,
        on_delete=models.CASCADE,
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
    )

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

    def __str__(self):
        return self.full_name or f"Contact {self.id}"


class ContactFieldConfidence(models.Model):
    """信頼度メタDB（仕様書 v1.1.0 §4.6、新規）。

    Contact のフィールドのうち、OCR の信頼度が low または medium のものだけをレコード生成する。
    high のフィールドはレコードを作らない（レコードがない=high と解釈）。
    confirmed_at / confirmed_by の値の埋め込みは v1.0.2 で実装予定（v1.1.0 では NULL のまま）。
    """

    CONFIDENCE_LOW = "low"
    CONFIDENCE_MEDIUM = "medium"
    CONFIDENCE_CHOICES = [
        (CONFIDENCE_LOW, "低"),
        (CONFIDENCE_MEDIUM, "中"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="confidences",
    )
    field_name = models.CharField(max_length=50)
    confidence = models.CharField(max_length=10, choices=CONFIDENCE_CHOICES)
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
            models.UniqueConstraint(
                fields=["contact", "field_name"],
                name="unique_contact_field_name",
            ),
        ]

    def __str__(self):
        return f"{self.contact_id} {self.field_name} ({self.confidence})"
