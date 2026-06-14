"""OpenCV による名刺切り出しパイプライン（v1.5.0 / cron A）。

1 OriginalImage を引数に取り、OpenCV 検出 → 透視変換 → card_image 保存 →
BusinessCard 作成 → OriginalImage.status 遷移までを完結させる。
OCR は呼ばない（OCR cron 側で別途実行）。Contact / Person も作らない。

【トランザクション境界】card 単位（1 card の失敗で他 card に影響しない）。
【card_image 保存】同期書き（save_card_image による最終パスへの直接書き込み）。
"""

import logging
import os
from typing import NamedTuple

from django.conf import settings
from django.db import transaction

from cards.models import BusinessCard, OriginalImage
from cards.services.detectors.opencv_detector import (
    detect_cards_with_debug,
    results_from_debug_result,
)
from cards.services.opencv_debug_cache import save_debug_data
from cards.orientation.correct import resolve_orientation
from cards.orientation.factory import get_orientation_backend
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
            # rev2/rev3：最終検出結果は通常・反転を横断統合した integrated_results。
            # 在り処を知る唯一の窓口 results_from_debug_result() を通す（attempts[-1].results を
            # 直読みすると反転 attempt だけを拾い件数が落ちる移植漏れ。43739da 由来）。
            detections = results_from_debug_result(debug_result)
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

        warp と save の間に向き正位化（ORIENTATION_BACKEND コンセント）を 1 箇所だけ挟む。
        判定器の例外は基底 detect() のフェイルセーフで握りつぶし（label0/score0/failed）、
        据え置きで切り出しを完走する。回転は transpose（rotate 禁止）。判定記録は
        orientation_correction / orientation_score（バックエンド共通）へ書く。osd_json は
        OSD 殻経路のときのみ従来どおり充填（既定 PP-LCNet / none では None）。
        save→BC 作成は save_and_create_business_card に委譲し、本メソッドは created_count /
        error_messages（インスタンス状態）の更新のみを担う。
        失敗パターン：
          - 画像書き込み失敗 → BC を card_image=None で作成、error_message に追記
          - BC 作成失敗（書き込み済み画像あり）→ ファイル削除、error_message に追記
        """
        # warp と save の間：向き判定（コンセント）→ 閾値分岐で transpose 正位化 or 据え置き。
        backend = get_orientation_backend()
        result = backend.detect(warped_image)  # (label, score, status)。例外は基底で捕捉。
        threshold = float(getattr(settings, "ORIENTATION_SCORE_THRESHOLD", 0.7))
        final_image, correction, score = resolve_orientation(
            warped_image, result, threshold,
        )
        osd_json = getattr(backend, "osd_json", None)  # OSD 殻のみ充填／既定は None

        save_result = save_and_create_business_card(
            final_image, self.original_image, card_index,
            osd_json=osd_json,
            orientation_correction=correction,
            orientation_score=score,
        )

        if not save_result.crop_success:
            self.error_messages.append(
                f"card_index={card_index}: 切り抜き失敗 ({save_result.crop_error})"
            )
        if save_result.business_card is not None:
            self.created_count += 1
        elif save_result.db_error is not None:
            self.error_messages.append(
                f"card_index={card_index}: DB保存失敗 ({save_result.db_error})"
            )


class SaveAndCreateResult(NamedTuple):
    """save_and_create_business_card の結果。

    呼び出し側が created_count / error_messages を更新するための情報を返す。
    [business_card] 作成された BusinessCard。BC 作成自体が失敗したときのみ None。
    [crop_success]  画像保存（save_card_image）が成功したか。
    [crop_error]    画像保存失敗の理由（成功時は ""）。
    [db_error]      BC 作成例外の "型名: メッセージ"（成功時は None）。
    """

    business_card: BusinessCard | None
    crop_success: bool
    crop_error: str
    db_error: str | None


def save_and_create_business_card(
    final_image, original_image, card_index, osd_json=None,
    orientation_correction=None, orientation_score=None,
):
    """回転適用済みの最終画像を card_image として保存し、BusinessCard を作成する。

    [性質] 副作用あり（ファイル書き込み・DB 書き込み・失敗時ファイル削除）
    [入力] final_image:    保存する最終画像（回転は呼び出し側で適用済み・本関数は回転しない）
           original_image: OriginalImage インスタンス（.id を保存パスと FK に使う）
           card_index:     int
           osd_json:       dict | None（BC.osd_json にそのまま格納）
           orientation_correction: str | None（BC.orientation_correction。none 経路は None）
           orientation_score:      float | None（BC.orientation_score）
    [出力] SaveAndCreateResult（created_count / error_messages の更新は呼び出し側の責務）
    [方針] 画像保存失敗時は card_image=None で BC を作成し、BC 作成が保存済み画像を残して
           失敗したときは書き込んだ画像を削除する（ロールバック）。
    """
    crop_success, final_rel, crop_error = save_card_image(
        final_image, str(original_image.id), card_index,
    )

    if not crop_success:
        try:
            with transaction.atomic():
                business_card = BusinessCard.objects.create(
                    original_image=original_image,
                    card_image=None,
                    card_index=card_index,
                    ocr_status=BusinessCard.OcrStatus.PENDING,
                    osd_json=osd_json,
                    orientation_correction=orientation_correction,
                    orientation_score=orientation_score,
                )
            return SaveAndCreateResult(business_card, False, crop_error, None)
        except Exception as e:
            return SaveAndCreateResult(None, False, crop_error, f"{type(e).__name__}: {e}")

    try:
        with transaction.atomic():
            business_card = BusinessCard.objects.create(
                original_image=original_image,
                card_image=final_rel,
                card_index=card_index,
                ocr_status=BusinessCard.OcrStatus.PENDING,
                osd_json=osd_json,
                orientation_correction=orientation_correction,
                orientation_score=orientation_score,
            )
        return SaveAndCreateResult(business_card, True, crop_error, None)
    except Exception as e:
        _unlink_card_image(final_rel)
        return SaveAndCreateResult(None, True, crop_error, f"{type(e).__name__}: {e}")


def _unlink_card_image(relative_path):
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
