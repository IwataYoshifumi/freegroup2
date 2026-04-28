import uuid

from django.conf import settings
from django.db import models


class OriginalImage(models.Model):
    """原画像DB（仕様書 4.2）。ユーザーがアップロードした画像本体を保管する。"""

    STATUS_PENDING = "pending"
    STATUS_EXTRACTED = "extracted"
    STATUS_GARBAGE = "garbage"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "未処理"),
        (STATUS_EXTRACTED, "正常"),
        (STATUS_GARBAGE, "ゴミ画像"),
        (STATUS_FAILED, "処理失敗"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    image_file = models.ImageField(upload_to="originals/%Y/%m/%d/")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    error_message = models.TextField(blank=True, default="")
    detected_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.id} ({self.status})"


class BusinessCard(models.Model):
    """名刺DB（仕様書 4.3）。raw_json は不変データとして扱う。"""

    OCR_STATUS_PENDING = "pending"
    OCR_STATUS_PROCESSING = "processing"
    OCR_STATUS_SUCCESS = "success"
    OCR_STATUS_FAILED = "failed"
    OCR_STATUS_CHOICES = [
        (OCR_STATUS_PENDING, "未実行"),
        (OCR_STATUS_PROCESSING, "実行中"),
        (OCR_STATUS_SUCCESS, "OCR成功"),
        (OCR_STATUS_FAILED, "失敗"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_image = models.ForeignKey(
        OriginalImage,
        on_delete=models.CASCADE,
    )
    card_image = models.ImageField(upload_to="cards/%Y/%m/%d/")
    raw_json = models.JSONField()
    ocr_status = models.CharField(
        max_length=20,
        choices=OCR_STATUS_CHOICES,
        default=OCR_STATUS_PENDING,
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.id} ({self.ocr_status})"


class Person(models.Model):
    """人物DB（仕様書 4.5）。v1.0.1 では最小構成（id / created_at / updated_at のみ）。

    一覧表示の代表値は person.contact_set.order_by('-created_at').first() から取得する。
    display_name / primary_contact_id 等は v2.0.0 で追加予定（v1.0.1 では追加しない）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        contact = self.contact_set.order_by("-created_at").first()
        if contact and contact.full_name:
            return contact.full_name
        return f"Person {self.id}"


class Contact(models.Model):
    """連絡先DB（仕様書 4.4）。名刺ごとのスナップショット。raw_json を解析して展開した編集可能データ。"""

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
    additional_names = models.JSONField(default=list)
    alt_names = models.JSONField(default=list)

    company = models.CharField(max_length=255, blank=True, default="")
    department = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")

    emails = models.JSONField(default=list)
    phones = models.JSONField(default=list)
    mobiles = models.JSONField(default=list)
    fax = models.JSONField(default=list)
    websites = models.JSONField(default=list)
    social_media = models.JSONField(default=list)

    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.full_name or f"Contact {self.id}"
