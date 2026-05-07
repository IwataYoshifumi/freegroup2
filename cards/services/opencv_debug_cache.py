"""OpenCV 検出のデバッグ情報を OriginalImage に永続化する。

OriginalImage.debug_json には JSON 化可能な中間データを格納し、
masks（PIL.Image 群）は MEDIA_ROOT/debug_cache/<original_image.id>/ 以下に
mask_1.png 〜 mask_5.png として分離保存する。

masks 番号と detect_cards_with_debug() のキー対応は固定：
  mask_1.png ← mask_diff
  mask_2.png ← mask_edge
  mask_3.png ← mask_sat
  mask_4.png ← mask_or
  mask_5.png ← mask_closed

呼び出し元：
- OriginalDetailView.get   ：debug_json が None のとき save_debug_data() を呼ぶ
- RecalcDebugView.post     ：clear_debug_cache() を呼ぶ
"""

import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# この順序は変更禁止。ステップ1C-2 のテンプレートが mask_1.png〜mask_5.png の
# 番号→意味の対応を前提に表示する。順序を変えるとテンプレート側も同時修正が必要。
_MASK_FILE_ORDER = (
    "mask_diff",
    "mask_edge",
    "mask_sat",
    "mask_or",
    "mask_closed",
)


def get_debug_cache_dir(original_image) -> Path:
    """[性質] 純関数 / OriginalImage に紐付く debug_cache ディレクトリの絶対パスを返す。

    [入力] original_image: OriginalImage インスタンス
    [出力] Path（ディレクトリの存在は保証しない）
    """
    return Path(settings.MEDIA_ROOT) / "debug_cache" / str(original_image.id)


def save_debug_data(original_image, debug_result: dict) -> None:
    """detect_cards_with_debug() の結果を debug_json と debug_cache ファイル群に保存する。

    [性質] 副作用あり（DB 書込・ファイル書込）
    [入力]
      original_image: OriginalImage インスタンス（保存対象）
      debug_result: detect_cards_with_debug() の戻り値 dict
    [出力] None

    masks（PIL.Image 群）は debug_cache/<id>/mask_<n>.png に書き出し、
    JSON 化可能な中間データのみを original_image.debug_json に格納して save() する。
    例外時の戻り値（results=[], error_message=str）であっても可能な限り情報を残す。
    """
    cache_dir = get_debug_cache_dir(original_image)
    cache_dir.mkdir(parents=True, exist_ok=True)

    masks = debug_result.get("masks") or {}
    for idx, key in enumerate(_MASK_FILE_ORDER, start=1):
        img = masks.get(key)
        if img is None:
            continue
        img.save(cache_dir / f"mask_{idx}.png", format="PNG")

    original_image.debug_json = _build_debug_json(debug_result)
    original_image.save(update_fields=["debug_json"])
    logger.info("opencv-debug: saved debug_json for OriginalImage %s", original_image.id)


def clear_debug_cache(original_image) -> None:
    """OriginalImage に紐付く debug_cache ディレクトリと debug_json をクリアする。

    [性質] 副作用あり（ファイル削除・DB 書込）
    [入力] original_image: OriginalImage インスタンス
    [出力] None

    再計算 URL から呼ばれる。次回 GET 時に detect_cards_with_debug が再実行される。
    キャッシュディレクトリが存在しない場合はファイル削除をスキップする。
    """
    cache_dir = get_debug_cache_dir(original_image)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    original_image.debug_json = None
    original_image.save(update_fields=["debug_json"])
    logger.info("opencv-debug: cleared cache for OriginalImage %s", original_image.id)


def _build_debug_json(debug_result: dict) -> dict:
    """[性質] 純関数 / detect_cards_with_debug() の戻り値を JSON 化可能な構造に整形する。

    masks（PIL.Image 群）と results[*].warped_image は除外する。
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
        "mask_white_ratios": debug_result.get("mask_white_ratios") or {},
    }
