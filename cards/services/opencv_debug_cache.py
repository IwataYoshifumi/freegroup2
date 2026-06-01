"""OpenCV 検出のデバッグ情報を OriginalImage と DebugMask に永続化する。

JSON 化可能な中間データは OriginalImage.debug_json に格納し、
masks（PIL.Image 群）は DebugMask レコードとして保存する。
反転リトライが走った場合は attempt_no=1（通常）と attempt_no=2（反転後）の 2 セット分が
debug_json と DebugMask の両方で保持される。
DebugMask.mask_image の FS 実体は post_delete シグナル経由で削除される。

呼び出し元：
- pipeline_coordinator.run_pipeline                 ：detect_cards_with_debug の結果を save_debug_data() で保存
- RecalcDebugView.post                              ：recalc_opencv_debug() 経由
- process_pending --opencv-only 管理コマンド        ：recalc_opencv_debug() 経由
"""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from cards.models import DebugMask
from cards.services.detectors.opencv_detector import detect_cards_with_debug

logger = logging.getLogger(__name__)

# rev2 方式：OR 合成を廃止し、マスク別に「生」「クロージング後」を保存する。
# attempt ごと（attempt_no を付けて）にマスク名 → MaskType を引く。
# mask 名は detect_cards_with_debug の attempt["masks"] のキー（diff/edge/sat）。
_MASK_TYPE_RAW = {
    "diff": DebugMask.MaskType.DIFF,
    "edge": DebugMask.MaskType.EDGE,
    "sat":  DebugMask.MaskType.SAT,
}
_MASK_TYPE_CLOSED = {
    "diff": DebugMask.MaskType.DIFF_CLOSED,
    "edge": DebugMask.MaskType.EDGE_CLOSED,
    "sat":  DebugMask.MaskType.SAT_CLOSED,
}
_MASK_NAMES = ("diff", "edge", "sat")


def save_debug_data(original_image, debug_result: dict) -> None:
    """detect_cards_with_debug() の結果を debug_json と DebugMask 群に保存する。

    [性質] 副作用あり（DB 書込・ファイル書込）
    [入力]
      original_image: OriginalImage インスタンス
      debug_result  : detect_cards_with_debug() の戻り値 dict
    [出力] None

    既存の DebugMask は一度削除してから新規作成する（idempotent）。
    削除時に post_delete シグナルで mask_image の FS 実体も削除される。

    保存件数（rev2 方式：マスク別 生＋クローズ）：
      - 反転リトライなし：6 件（attempt_no=1: diff/edge/sat × 生・クローズ）
      - 反転リトライあり：12 件（上記 + attempt_no=2 の同 6 件）
    """
    # 既存の DebugMask を削除（FS 実体は post_delete でクリーンアップ）
    original_image.debug_masks.all().delete()

    for attempt in debug_result.get("attempts") or []:
        attempt_no = attempt.get("attempt_no") or 1
        attempt_masks = attempt.get("masks") or {}
        for name in _MASK_NAMES:
            mr = attempt_masks.get(name) or {}
            raw_img = mr.get("raw_image")
            if raw_img is not None:
                _create_debug_mask(
                    original_image, _MASK_TYPE_RAW[name], attempt_no=attempt_no,
                    image=raw_img, white_ratio=mr.get("raw_white_ratio"),
                )
            closed_img = mr.get("closed_image")
            if closed_img is not None:
                _create_debug_mask(
                    original_image, _MASK_TYPE_CLOSED[name], attempt_no=attempt_no,
                    image=closed_img, white_ratio=mr.get("closed_white_ratio"),
                )

    original_image.debug_json = _build_debug_json(debug_result)
    original_image.save(update_fields=["debug_json"])
    logger.info("opencv-debug: saved debug_json for OriginalImage %s", original_image.id)


def recalc_opencv_debug(original_image) -> None:
    """OpenCV 検出を再実行し、debug_json と DebugMask を最新化する（idempotent）。

    [性質] 副作用あり（DB 書込・ファイル書込）
    [入力] original_image: OriginalImage インスタンス
    [出力] None

    実行内容（3 ステップ）：
      1. clear_debug_cache(original_image)      ：既存の debug_json と DebugMask を削除
      2. detect_cards_with_debug(<image_path>)  ：OpenCV 検出を再実行
      3. save_debug_data(original_image, ...)   ：debug_json と DebugMask を再生成

    OriginalImage.status / BusinessCard / raw_json / Contact / Person /
    ContactFieldConfidence は一切触らない（OCR 結果由来のレコードは不変）。
    """
    clear_debug_cache(original_image)
    result = detect_cards_with_debug(original_image.image_file.path)
    save_debug_data(original_image, result)


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


def _create_debug_mask(original_image, mask_type, attempt_no, image, white_ratio):
    """[性質] 副作用あり / 1 件の DebugMask を作成する（内部ヘルパー）。"""
    metadata = {"white_ratio": white_ratio} if white_ratio is not None else {}
    buf = BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    DebugMask.objects.create(
        original_image=original_image,
        mask_type=mask_type,
        attempt_no=attempt_no,
        mask_image=ContentFile(
            buf.read(), name=f"{mask_type}_attempt{attempt_no}.png"
        ),
        metadata=metadata,
    )


def _build_debug_json(debug_result: dict) -> dict:
    """[性質] 純関数 / detect_cards_with_debug() の戻り値を JSON 化可能な構造に整形する。

    rev2 方式：各 attempt の masks（PIL.Image 群）を除外し、マスク別（diff/edge/sat）に
    candidates_filter・contours_count・excluded を残す。各 candidates_filter には
    マスク内 cross-reference 用の "index" を付与する。
    results は card_index と polygon のみのメタ情報に縮約する（warped 画像本体は
    BusinessCard.card_image 経由で参照可能）。candidates_dedup は mask タグ付きで転記する。
    sat_fallback / or_inversion はそのまま転記する。
    """
    attempts_meta = []
    for attempt in debug_result.get("attempts") or []:
        attempt_masks = attempt.get("masks") or {}
        masks_meta = {}
        for name in _MASK_NAMES:
            mr = attempt_masks.get(name) or {}
            candidates_filter = [
                {"index": i, **c}
                for i, c in enumerate(mr.get("candidates_filter") or [])
            ]
            masks_meta[name] = {
                "excluded":          bool(mr.get("excluded")),
                "contours_count":    mr.get("contours_count", 0),
                "candidates_filter": candidates_filter,
            }
        results_meta = [
            {"card_index": card_index, "polygon": r.get("polygon")}
            for card_index, r in enumerate(attempt.get("results") or [])
        ]
        attempts_meta.append({
            "attempt_no":       attempt.get("attempt_no"),
            "type":             attempt.get("type"),
            "masks":            masks_meta,
            "candidates_dedup": list(attempt.get("candidates_dedup") or []),
            "warp_failures":    list(attempt.get("warp_failures") or []),
            "results":          results_meta,
        })

    return {
        "image_size": debug_result.get("image_size"),
        "attempts": attempts_meta,
        "sat_fallback": debug_result.get("sat_fallback"),
        "or_inversion": debug_result.get("or_inversion"),
        "error_message": debug_result.get("error_message", ""),
        "computed_at": timezone.now().isoformat(),
    }
