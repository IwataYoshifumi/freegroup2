"""OpenCV 検出のデバッグ情報を OriginalImage と DebugMask に永続化する。

JSON 化可能な中間データは OriginalImage.debug_json に格納し、
masks（PIL.Image 群）は DebugMask レコードとして保存する。
反転リトライが走った場合は attempt_no=1（通常）と attempt_no=2（反転後）の 2 セット分が
debug_json と DebugMask の両方で保持される。
DebugMask.mask_image の FS 実体は post_delete シグナル経由で削除される。

呼び出し元：
- process_opencv 管理コマンド → crop_cards.Run_Crop_Cards_From_OriginalImage.run() ：detect_cards_with_debug の結果を save_debug_data() で保存
- RecalcDebugView.post                                                              ：recalc_opencv_debug() 経由
"""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from cards.models import DebugMask
from cards.services.detectors.opencv_detector import detect_cards_with_debug

logger = logging.getLogger(__name__)

# 反転処理の影響を受けない共通マスク。常に attempt_no=1 で 1 件だけ保存する。
# (api_key, mask_type) のタプル列挙。api_key は detect_cards_with_debug の masks dict のキー。
_COMMON_MASKS = (
    ("diff", DebugMask.MaskType.DIFF),
    ("edge", DebugMask.MaskType.EDGE),
    ("sat",  DebugMask.MaskType.SAT),
)

# 各 attempt ごとに保存するマスク（attempt_no を付けて保存）。
_PER_ATTEMPT_MASKS = (
    ("or",     DebugMask.MaskType.OR),
    ("closed", DebugMask.MaskType.CLOSED),
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

    保存件数：
      - 反転リトライなし：5 件（attempt_no=1: diff/edge/sat/or/closed）
      - 反転リトライあり：7 件（上記 + attempt_no=2: or/closed）
    """
    # 既存の DebugMask を削除（FS 実体は post_delete でクリーンアップ）
    original_image.debug_masks.all().delete()

    common_masks = debug_result.get("masks") or {}
    common_ratios = debug_result.get("mask_white_ratios") or {}
    for api_key, mask_type in _COMMON_MASKS:
        img = common_masks.get(api_key)
        if img is None:
            continue
        _create_debug_mask(
            original_image, mask_type, attempt_no=1, image=img,
            white_ratio=common_ratios.get(api_key),
        )

    for attempt in debug_result.get("attempts") or []:
        attempt_no = attempt.get("attempt_no") or 1
        attempt_masks = attempt.get("masks") or {}
        attempt_ratios = attempt.get("mask_white_ratios") or {}
        for api_key, mask_type in _PER_ATTEMPT_MASKS:
            img = attempt_masks.get(api_key)
            if img is None:
                continue
            _create_debug_mask(
                original_image, mask_type, attempt_no=attempt_no, image=img,
                white_ratio=attempt_ratios.get(api_key),
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

    masks（PIL.Image 群）と results[*].warped_image は除外する。
    mask_white_ratios は DebugMask.metadata に格納するため debug_json からは除外する。
    各 attempt の candidates_filter には cross-reference 用の "index" を付与する。
    results は card_index と polygon のみのメタ情報に縮約する（warped 画像本体は
    BusinessCard.card_image 経由で参照可能）。
    sat_fallback / or_inversion はそのまま転記する。
    """
    attempts_meta = []
    for attempt in debug_result.get("attempts") or []:
        candidates_filter = [
            {"index": i, **c}
            for i, c in enumerate(attempt.get("candidates_filter") or [])
        ]
        results_meta = [
            {"card_index": card_index, "polygon": r.get("polygon")}
            for card_index, r in enumerate(attempt.get("results") or [])
        ]
        attempts_meta.append({
            "attempt_no":        attempt.get("attempt_no"),
            "type":              attempt.get("type"),
            "contours_count":    attempt.get("contours_count", 0),
            "candidates_filter": candidates_filter,
            "candidates_dedup":  list(attempt.get("candidates_dedup") or []),
            "warp_failures":     list(attempt.get("warp_failures") or []),
            "results":           results_meta,
        })

    return {
        "image_size": debug_result.get("image_size"),
        "attempts": attempts_meta,
        "sat_fallback": debug_result.get("sat_fallback"),
        "or_inversion": debug_result.get("or_inversion"),
        "error_message": debug_result.get("error_message", ""),
        "computed_at": timezone.now().isoformat(),
    }
