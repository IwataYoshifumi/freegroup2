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
from cards.services.osd import (
    apply_upright,
    detect_osd_info,
    detect_osd_rotation,
    run_tesseract_ocr,
)
from cards.tasks.card_cropper import save_card_image

logger = logging.getLogger(__name__)

# BC.osd_json の構造バージョン（OSD 記録専用。OCR 本体の combined_response とは別系統）。
_OSD_JSON_SCHEMA_VERSION = "1.0.0"


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

    def _build_osd_record(self, warped_image, card_index):
        """[性質] 副作用あり（外部プロセス OSD/OCR 呼び出し）/ 正立画像と osd_json dict を返す。

        osd.py は例外送出のみで normal フォールバック・error 包みは呼び出し元の責務。
        本ヘルパーが OSD/OCR の全呼び出しを try/except で包み、何が起きても切り出しを
        止めない（例外は normal フォールバック＋error 記録で握りつぶす）。OSD 例外が
        _create_card の外へ伝播して切り出し全体を落とすことは無い。
        [入力] warped_image: 透視変換済みクロップ（PIL.Image）、card_index: int
        [出力] (upright_image: PIL.Image, osd_json: dict)
          upright_image: 正立化後の画像。OSD 失敗・回転不要（rotate=0）時は warped_image そのまま。
          osd_json: {"schema_version", "osd", "tesseract_ocr"}。osd は detect_osd_info の戻り、
                    失敗時 {"error": ...}。tesseract_ocr は run_tesseract_ocr の戻り
                    （本番 lang のみ／DEBUG 時 text/data）、失敗時 {"error": ...}。
        [方針] OSD は元クロップ、OCR は正立後画像に対して取る（第1段の対象画像の使い分けを踏襲）。
               detect_osd_rotation / detect_osd_info には timeout を設ける（ハング対策）。
        """
        timeout = float(getattr(settings, "OSD_TIMEOUT_SEC", 10))

        # ① 正立化（rotate 量のみ使用。例外は normal フォールバック、rotate=0 は元画像）。
        upright_image = warped_image
        try:
            rotate_deg = detect_osd_rotation(warped_image, timeout=timeout)
            if rotate_deg % 360:
                upright_image = apply_upright(warped_image, rotate_deg)
        except Exception as e:
            logger.info(
                "OSD 正立化失敗→normal フォールバック card_index=%s: %s: %s",
                card_index, type(e).__name__, e,
            )

        # ② osd ブロック（元クロップに対して取る）。例外は {"error": ...} に包む。
        try:
            osd_block = detect_osd_info(warped_image, timeout=timeout)
        except Exception as e:
            osd_block = {"error": f"{type(e).__name__}: {e}"}
            logger.info(
                "OSD info 取得失敗（記録のみ・無視）card_index=%s: %s: %s",
                card_index, type(e).__name__, e,
            )

        # ③ tesseract_ocr ブロック（正立後画像に対して取る）。例外は {"error": ...} に包む。
        try:
            tess_block = run_tesseract_ocr(upright_image, timeout=timeout)
        except Exception as e:
            tess_block = {"error": f"{type(e).__name__}: {e}"}
            logger.info(
                "Tesseract OCR 取得失敗（記録のみ・無視）card_index=%s: %s: %s",
                card_index, type(e).__name__, e,
            )

        osd_json = {
            "schema_version": _OSD_JSON_SCHEMA_VERSION,
            "osd": osd_block,
            "tesseract_ocr": tess_block,
        }
        return upright_image, osd_json

    def _create_card(self, warped_image, card_index):
        """[性質] 副作用あり（DB 書き込み・ファイル書き込み）/ 1 card 単位の同期書き＋BC 作成。

        warp と save の間で OSD 正立化＋記録を行い（_build_osd_record）、正立画像を
        card_image として保存し、osd_json を BC に書き込む。OSD は内部で全例外を握りつぶす
        ため、OSD が失敗しても切り出しは完走する。
        失敗パターン：
          - 画像書き込み失敗 → BC を card_image=None で作成、error_message に追記
          - BC 作成失敗（書き込み済み画像あり）→ ファイル削除、error_message に追記
        """
        # warp と save の間：OSD 正立化＋osd_json 組み立て（例外は内部で握りつぶす）。
        upright_image, osd_json = self._build_osd_record(warped_image, card_index)

        # 正立化後の画像を card_image として保存する（結線の主目的）。
        crop_success, final_rel, crop_error = save_card_image(
            upright_image, str(self.original_image.id), card_index,
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
                        osd_json=osd_json,
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
                    osd_json=osd_json,
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
