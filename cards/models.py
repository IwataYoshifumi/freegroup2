import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


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

    # ------------------------------------------------------------------
    # クラスメソッド（仕様書 §10.10.1）
    # ------------------------------------------------------------------

    @classmethod
    def get_pending(cls, limit):
        """pending な OriginalImage を limit 件取得（仕様書 §10.10.1）。cron 用。

        [性質] 準関数（DB 読み取りのみ）
        [入力] limit: int（取得上限件数）
        [出力] QuerySet[OriginalImage]（status='pending' のもの、created_at の古い順、
               最大 limit 件）

        FIFO 処理：先にアップロードされたものを先に処理する。ロックは取らない（タスク層側で
        `select_for_update(skip_locked=True)` を使う前提、指示書 §4.5）。
        """
        return cls.objects.filter(status=cls.STATUS_PENDING).order_by(
            "created_at"
        )[:limit]

    @classmethod
    def release_stuck_locks(cls, threshold_minutes):
        """stuck な processing レコードを pending に戻す（仕様書 §10.10.1 / §19.2 / §20.1）。

        [性質] 副作用あり（DB 書込：自モデル集合を一括 update）
        [入力] threshold_minutes: int（このぶんより古い claimed_at を stuck と判定）
        [出力] None（QuerySet.update() の戻り値は捨てる）

        `status='processing'` かつ `claimed_at` が threshold_minutes 分前より古いレコードを
        `status='pending'` に戻し、`claimed_at` も NULL に戻す（次回 CAS で再 claim される
        まで付随情報を残さない）。一括 update でロック取得不要。

        他ステータス（pending / extracted / garbage / failed）のレコードは影響を受けない
        （フィルタ条件 `status='processing'`）。
        """
        threshold = timezone.now() - timedelta(minutes=threshold_minutes)
        cls.objects.filter(
            status=cls.STATUS_PROCESSING,
            claimed_at__lt=threshold,
        ).update(status=cls.STATUS_PENDING, claimed_at=None)

    # ------------------------------------------------------------------
    # インスタンスメソッド（仕様書 §10.10.2）
    # ------------------------------------------------------------------

    def get_image_url(self):
        """サムネイル用 URL を返す（仕様書 §10.10.2）。

        [性質] 純関数（DB 操作なし、image_file の url プロパティを返すだけ）
        [入力] なし
        [出力] str（image_file.url、または image_file が空なら空文字列）

        v1.4.2 ではサムネイル化は CSS 側で対応するため、`image_file.url` をそのまま返す
        シンプル実装（指示書 §4.4）。get_image_url_full() と同じ URL を返す。サムネイル
        生成方式を将来変更する余地を残すためにメソッドを2つに分けて維持する。
        """
        return self.image_file.url if self.image_file else ""

    def get_image_url_full(self):
        """フルサイズ用 URL を返す（仕様書 §10.10.2）。

        [性質] 純関数（DB 操作なし、image_file の url プロパティを返すだけ）
        [入力] なし
        [出力] str（image_file.url、または image_file が空なら空文字列）

        指示書 §4.4 の通り、`image_file.url` をそのまま返す。get_image_url() と同じ URL。
        """
        return self.image_file.url if self.image_file else ""


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

    # ------------------------------------------------------------------
    # インスタンスメソッド（仕様書 §10.10.3）
    # ------------------------------------------------------------------

    def get_card_image_url(self):
        """サムネイル用 URL を返す（仕様書 §10.10.3）。

        [性質] 純関数（DB 操作なし、card_image の url プロパティを返すだけ）
        [入力] なし
        [出力] str（card_image.url、または card_image が空なら空文字列）

        v1.4.2 ではサムネイル化は CSS 側で対応するため、`card_image.url` をそのまま返す
        シンプル実装（指示書 §4.4）。card_image は v1.2.1 で null 許容化されており、
        切り抜き失敗時は空文字列を返す。
        """
        return self.card_image.url if self.card_image else ""

    def get_card_image_url_full(self):
        """フルサイズ用 URL を返す（仕様書 §10.10.3）。

        [性質] 純関数（DB 操作なし、card_image の url プロパティを返すだけ）
        [入力] なし
        [出力] str（card_image.url、または card_image が空なら空文字列）

        指示書 §4.4 の通り、`card_image.url` をそのまま返す。get_card_image_url() と
        同じ URL。
        """
        return self.card_image.url if self.card_image else ""
