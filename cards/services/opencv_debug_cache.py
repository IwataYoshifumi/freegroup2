"""OpenCV 検出のデバッグ情報を OriginalImage と DebugMask に永続化する。

JSON 化可能な中間データは OriginalImage.debug_json に格納し、
masks（PIL.Image 群）は DebugMask レコードとして保存する。
DebugMask.mask_image の FS 実体は post_delete シグナル経由で削除される。

呼び出し元：
- pipeline_coordinator.run_pipeline ：detect_cards_with_debug の結果を save_debug_data() で保存
- RecalcDebugView.post              ：clear_debug_cache() → detect_cards_with_debug() → save_debug_data()
"""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from cards.models import DebugMask

logger = logging.getLogger(__name__)

# (api_key, mask_type, ratio_key) のタプル列挙。
# api_key      : detect_cards_with_debug の masks dict のキー
# mask_type    : DebugMask.MaskType の値
# ratio_key    : detect_cards_with_debug の mask_white_ratios dict のキー
_MASK_DEFINITIONS = (
    ("mask_diff",   DebugMask.MaskType.DIFF,   "mask_1"),
    ("mask_edge",   DebugMask.MaskType.EDGE,   "mask_2"),
    ("mask_sat",    DebugMask.MaskType.SAT,    "mask_3"),
    ("mask_or",     DebugMask.MaskType.OR,     "mask_4"),
    ("mask_closed", DebugMask.MaskType.CLOSED, "mask_5"),
)


def save_debug_data(original_image, debug_result: dict) -> None:
    """detect_cards_with_debug() の結果を debug_json と DebugMask 群に保存する。

    [性質] 副作用あり（DB 書込・ファイル書込）
    [入力]
      original_image: OriginalImage インスタンス
      debug_result  : detect_cards_with_debug() の戻り値 dict
    [出力] None

    既存の DebugMask は一度削除してから新規作成する（idempotent）。
    削除時に post_delete シグナルで mask_image の FS 実体も削除される。
    """
    # 既存の DebugMask を削除（FS 実体は post_delete でクリーンアップ）
    original_image.debug_masks.all().delete()

    masks = debug_result.get("masks") or {}
    white_ratios = debug_result.get("mask_white_ratios") or {}
    for api_key, mask_type, ratio_key in _MASK_DEFINITIONS:
        img = masks.get(api_key)
        if img is None:
            continue
        white_ratio = white_ratios.get(ratio_key)
        metadata = {"white_ratio": white_ratio} if white_ratio is not None else {}

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        DebugMask.objects.create(
            original_image=original_image,
            mask_type=mask_type,
            mask_image=ContentFile(buf.read(), name=f"{mask_type}.png"),
            metadata=metadata,
        )

    original_image.debug_json = _build_debug_json(debug_result)
    original_image.save(update_fields=["debug_json"])
    logger.info("opencv-debug: saved debug_json for OriginalImage %s", original_image.id)


def clear_debug_cache(original_image) -> None:
    """OriginalImage に紐付く DebugMask レコードと debug_json をクリアする。

    [性質] 副作用あり（DB 書込・ファイル書込）
    [入力] original_image: OriginalImage インスタンス
    [出力] None

    DebugMask.delete() の post_delete シグナルで mask_image の FS 実体も削除される。
    """
    original_image.debug_masks.all().delete()
    original_image.debug_json = None
    original_image.save(update_fields=["debug_json"])
    logger.info("opencv-debug: cleared cache for OriginalImage %s", original_image.id)


def _build_debug_json(debug_result: dict) -> dict:
    """[性質] 純関数 / detect_cards_with_debug() の戻り値を JSON 化可能な構造に整形する。

    masks（PIL.Image 群）と results[*].warped_image は除外する。
    mask_white_ratios も除外（DebugMask.metadata に格納するため）。
    candidates_filter には cross-reference 用の "index" を付与する。
    results は card_index と polygon のみのメタ情報に縮約する（warped 画像本体は
    BusinessCard.card_image 経由で参照可能）。
    """
    candidates_filter = []
    for i, c in enumerate(debug_result.get("candidates_filter") or []):
        candidates_filter.append({"index": i, **c})

    candidates_dedup = list(debug_result.get("candidates_dedup") or [])
    warp_failures = list(debug_result.get("warp_failures") or [])

    results_meta = []
    for card_index, r in enumerate(debug_result.get("results") or []):
        results_meta.append({
            "card_index": card_index,
            "polygon": r.get("polygon"),
        })

    return {
        "image_size": debug_result.get("image_size"),
        "contours_count": debug_result.get("contours_count"),
        "candidates_filter": candidates_filter,
        "candidates_dedup": candidates_dedup,
        "warp_failures": warp_failures,
        "results": results_meta,
        "error_message": debug_result.get("error_message", ""),
        "computed_at": timezone.now().isoformat(),
    }
