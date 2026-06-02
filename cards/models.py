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
    # 【検証用・一時的】このブランチ（feature/opencv-improvement）には認証が無く、
    # 正規アップロードUIでは user を埋められないため null 許容にしている。
    # main（認証あり＝user 必須の世界）と統合する際は null=True/blank=True を外して必須へ戻すこと。
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
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


def _debug_mask_upload_path(instance, filename):
    """[性質] 純関数 / DebugMask の ImageField の保存先を組み立てる。

    保存先：MEDIA_ROOT/debug_masks/<original_image_id>/<mask_type>_attempt<attempt_no>.png
    filename 引数は無視し、mask_type と attempt_no に基づいた固定名を使う。
    """
    return (
        f"debug_masks/{instance.original_image_id}/"
        f"{instance.mask_type}_attempt{instance.attempt_no}.png"
    )


class DebugMask(models.Model):
    """OpenCV 検出のデバッグ用マスク画像（rev2 方式・マスク別 6 種類）。

    OriginalImage に紐付き、検出時に save_debug_data() から書き込まれる。
    DBが1次ソース、mask_image の FS 実体は post_delete シグナル経由で削除される。
    rev2 では OR 合成を廃止し、diff/edge/sat それぞれの「生マスク」と「クロージング後マスク」を
    マスク別に保存する。白黒反転リトライが走った場合、attempt_no=2 に同 6 種が追加で保存される。
    """

    class MaskType(models.TextChoices):
        DIFF = "diff", "輝度差 生 (diff)"
        EDGE = "edge", "エッジ 生 (edge)"
        SAT = "sat", "彩度 生 (sat)"
        ADAPTIVE = "adaptive", "局所二値化 生 (adaptive)"
        DIFF_CLOSED = "diff_closed", "輝度差 クローズ (diff_closed)"
        EDGE_CLOSED = "edge_closed", "エッジ クローズ (edge_closed)"
        SAT_CLOSED = "sat_closed", "彩度 クローズ (sat_closed)"
        ADAPTIVE_CLOSED = "adaptive_closed", "局所二値化 クローズ (adaptive_closed)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_image = models.ForeignKey(
        OriginalImage,
        on_delete=models.CASCADE,
        related_name="debug_masks",
    )
    mask_type = models.CharField(max_length=16, choices=MaskType.choices)
    attempt_no = models.IntegerField(default=1)
    mask_image = models.ImageField(upload_to=_debug_mask_upload_path)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["original_image", "mask_type", "attempt_no"],
                name="unique_original_image_mask_type_attempt",
            ),
        ]

    def __str__(self):
        return f"{self.original_image_id} {self.mask_type} attempt{self.attempt_no}"


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
