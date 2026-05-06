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
