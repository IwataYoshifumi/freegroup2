import uuid

from django.conf import settings
from django.db import models


class OriginalImage(models.Model):
    """原画像DB（仕様書 v1.2.1 §4.2）。

    ユーザーがアップロードした画像本体を保管する。OCR 結果 JSON 全体（cards 配列含む）
    も raw_json に集約して保存する（v1.2.0 で導入）。
    """

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_EXTRACTED = "extracted"
    STATUS_GARBAGE = "garbage"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "処理待ち"),
        (STATUS_PROCESSING, "処理中"),
        (STATUS_EXTRACTED, "完了"),
        (STATUS_GARBAGE, "無効画像"),
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
    claimed_at = models.DateTimeField(null=True, blank=True, default=None)
    raw_json = models.JSONField(null=True, blank=True)
    debug_json = models.JSONField(
        null=True,
        blank=True,
        help_text="OpenCV 検出のデバッグ情報（中間データ）。None なら次回 GET 時に再計算される",
    )
    error_message = models.TextField(blank=True, default="")
    detected_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.id} ({self.status})"


class BusinessCard(models.Model):
    """名刺DB（仕様書 v1.2.1 §4.3）。

    1 OriginalImage から検出された各名刺を表す。raw_json / ocr_status / error_message は
    持たず、OriginalImage.raw_json["cards"][card_index] の該当要素を参照する。
    切り抜き失敗時は card_image=null で作成する（v1.2.1）。
    """

    ORIENTATION_NORMAL = "normal"
    ORIENTATION_90_CW = "rotate_90_cw"
    ORIENTATION_90_CCW = "rotate_90_ccw"
    ORIENTATION_180 = "rotate_180"
    ORIENTATION_MIRROR = "mirror"
    ORIENTATION_CHOICES = [
        (ORIENTATION_NORMAL,  "正位"),
        (ORIENTATION_90_CW,   "時計回り90°"),
        (ORIENTATION_90_CCW,  "反時計回り90°"),
        (ORIENTATION_180,     "180°回転"),
        (ORIENTATION_MIRROR,  "鏡像"),
    ]

    class OcrResult(models.TextChoices):
        BUSINESS_CARD = "business_card", "名刺"
        NOT_BUSINESS_CARD = "not_business_card", "名刺ではない"
        INSUFFICIENT_INFO = "insufficient_info", "情報不足"
        OCR_FAILED = "ocr_failed", "OCR失敗"
        OTHERS = "others", "その他"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_image = models.ForeignKey(
        OriginalImage,
        on_delete=models.CASCADE,
    )
    card_image = models.ImageField(
        upload_to="cards/%Y/%m/%d/",
        null=True,
        blank=True,
    )
    card_index = models.IntegerField()
    orientation = models.CharField(
        max_length=20,
        choices=ORIENTATION_CHOICES,
        default=ORIENTATION_NORMAL,
    )
    ocr_result = models.CharField(
        max_length=20,
        choices=OcrResult.choices,
        default=OcrResult.BUSINESS_CARD,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["original_image", "card_index"],
                name="unique_original_image_card_index",
            ),
        ]

    def __str__(self):
        return f"{self.id} (card_index={self.card_index})"


class Person(models.Model):
    """人物DB（仕様書 v1.1.0 §4.5）。v1.1.0 では最小構成（id / created_at / updated_at のみ）。

    一覧表示の代表値は person.contact_set.order_by('-created_at').first() から取得する。
    display_name / primary_contact_id 等は v2.0.0 で追加予定（v1.1.0 では追加しない）。
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
