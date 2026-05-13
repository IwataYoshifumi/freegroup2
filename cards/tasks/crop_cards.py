"""OpenCV による名刺切り出しパイプライン（v1.5.0 / cron A）。

1 OriginalImage を引数に取り、OpenCV 検出 → 透視変換 → card_image 保存 →
BusinessCard 作成 → OriginalImage.status 遷移までを完結させる。
OCR は呼ばない（OCR cron 側で別途実行）。Contact / Person も作らない。

【トランザクション境界】card 単位（1 card の失敗で他 card に影響しない）。
【card_image 保存】同期書き（save_card_image による最終パスへの直接書き込み）。
"""

import logging
import os

from django.conf import settings
from django.db import transaction

from cards.models import BusinessCard, OriginalImage
from cards.services.detectors.opencv_detector import detect_cards_with_debug
from cards.services.opencv_debug_cache import save_debug_data
from cards.tasks.card_cropper import save_card_image

logger = logging.getLogger(__name__)


class Run_Crop_Cards_From_OriginalImage:
    """1 OriginalImage の OpenCV 切り出しを統括する（v1.5.0 / cron A）。

    [責務]
      OpenCV 検出 → debug 永続化 → card_image 同期書き → BusinessCard 作成 →
      OriginalImage.status 遷移までを完結させる。OCR / Contact / Person /
      ContactFieldConfidence には一切触れない。
    [前提]
      呼び出し時点で status=opencv_processing に遷移済みであること（process_opencv の CAS 後）。
    [遷移]
      正常終了 → cards_extracted（または検出 0 件なら garbage）
      想定外例外 → failed
      pending に戻る経路はない（差し戻しは stuck sweeper / retry_failed_ocr の責務）。
    """

    def __init__(self, original_image):
        self.original_image = original_image
        self.error_messages = []
        self.created_count = 0

    def run(self):
        """OpenCV 切り出し処理を実行する。

        [性質] 副作用あり（複合処理: DB 書き込み・ファイル書き込み）
        [入力] なし（self.original_image を使う）
        [出力] None（status / error_message / detected_count を OriginalImage に保存）
        [方針] 例外を外に漏らさない。失敗は status と error_message に集約する。
        """
        if self.original_image.status != OriginalImage.STATUS_OPENCV_PROCESSING:
            current_status = self.original_image.status
            logger.error(
                "Run_Crop_Cards_From_OriginalImage called with status=%s, "
                "expected opencv_processing. Aborting. OriginalImage %s",
                current_status,
                self.original_image.id,
            )
            self.original_image.status = OriginalImage.STATUS_FAILED
            self.original_image.error_message = (
                f"Run_Crop_Cards_From_OriginalImage が status={current_status} "
                "で呼ばれました。CAS が成立していない可能性があります。"
            )
            with transaction.atomic():
                self.original_image.save(
                    update_fields=["status", "error_message", "updated_at"]
                )
            return

        try:
            debug_result = detect_cards_with_debug(self.original_image.image_file.path)
            attempts = debug_result.get("attempts") or []
            last_attempt = attempts[-1] if attempts else {}
            detections = last_attempt.get("results") or []
            self.original_image.detected_count = len(detections)

            try:
                save_debug_data(self.original_image, debug_result)
            except Exception as e:
                logger.warning(
                    "save_debug_data failed for OriginalImage %s: %s",
                    self.original_image.id, e,
                )

            if not detections:
                self.original_image.status = OriginalImage.STATUS_GARBAGE
                return

            for card_index, detection in enumerate(detections):
                self._create_card(detection["warped_image"], card_index)

            self.original_image.status = OriginalImage.STATUS_CARDS_EXTRACTED

        except Exception as e:
            logger.exception(
                "Run_Crop_Cards_From_OriginalImage unexpected error for OriginalImage %s",
                self.original_image.id,
            )
            self.original_image.status = OriginalImage.STATUS_FAILED
            self.error_messages.append(f"想定外のエラー: {type(e).__name__}: {e}")
        finally:
            self.original_image.error_message = "\n".join(self.error_messages)
            with transaction.atomic():
                self.original_image.save(
                    update_fields=[
                        "status",
                        "error_message",
                        "detected_count",
                        "updated_at",
                    ]
                )

    def _create_card(self, warped_image, card_index):
        """[性質] 副作用あり（DB 書き込み・ファイル書き込み）/ 1 card 単位の同期書き＋BC 作成。

        失敗パターン：
          - 画像書き込み失敗 → BC を card_image=None で作成、error_message に追記
          - BC 作成失敗（書き込み済み画像あり）→ ファイル削除、error_message に追記
        """
        crop_success, final_rel, crop_error = save_card_image(
            warped_image, str(self.original_image.id), card_index,
        )

        if not crop_success:
            self.error_messages.append(
                f"card_index={card_index}: 切り抜き失敗 ({crop_error})"
            )
            try:
                with transaction.atomic():
                    BusinessCard.objects.create(
                        original_image=self.original_image,
                        card_image=None,
                        card_index=card_index,
                        ocr_status=BusinessCard.OcrStatus.PENDING,
                    )
                self.created_count += 1
            except Exception as e:
                self.error_messages.append(
                    f"card_index={card_index}: DB保存失敗 ({type(e).__name__}: {e})"
                )
            return

        try:
            with transaction.atomic():
                BusinessCard.objects.create(
                    original_image=self.original_image,
                    card_image=final_rel,
                    card_index=card_index,
                    ocr_status=BusinessCard.OcrStatus.PENDING,
                )
            self.created_count += 1
        except Exception as e:
            self.error_messages.append(
                f"card_index={card_index}: DB保存失敗 ({type(e).__name__}: {e})"
            )
            self._unlink_card_image(final_rel)

    def _unlink_card_image(self, relative_path):
        """[性質] 副作用あり（ファイル削除）/ ロールバック時のクリーンアップ。"""
        if not relative_path:
            return
        abs_path = os.path.join(settings.MEDIA_ROOT, relative_path)
        try:
            os.unlink(abs_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(
                "card_image cleanup failed for %s: %s", abs_path, e,
            )
