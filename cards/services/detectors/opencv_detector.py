"""名刺領域検出・4隅座標取得・透視変換（OpenCV 実装・rev2 方式）。

3種のマスク（輝度差 diff・Canny edge・HSV彩度 sat）を **OR 合成せず**、
マスクごとにクロージング → findContours → minAreaRect まで回して候補を出し、
名刺らしい縦横比（1.6〜1.8）の候補だけを残す。マスク横断で中心座標＋IoU が
近い候補を1件に統合（重複除去）する。採否の主基準は縦横比のみで、サイズは
OCR 不能な極小を落とす緩い絶対下限としてのみ使う。

正本：docs/OpenCV/名刺画像前処理_設計方針_OpenCV_OSD_rev2.md 第1部。

透視変換で正立化した画像をそのまま返す（向き補正は行わない）。
"""

import logging

import cv2
import numpy as np
from django.conf import settings
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# ── マスク生成パラメータ ───────────────────────────────────
_ERODE_KERNEL = (20, 20)
_DIFF_THRESHOLD = 20
_CLOSE_KERNEL = (5, 5)
_CANNY_LOW = 50
_CANNY_HIGH = 150
_CANNY_DILATE_KERNEL = (3, 3)
_SAT_THRESHOLD = 40
# 診断用しきい値：mask_sat の白画素率がこれを超えたら「高彩度背景」と記録する（sat_fallback.triggered）。
# rev2 で OR 合成を廃止したため、この判定で sat を候補抽出から除外することはしない（診断情報のみ）。
_SAT_WHITE_RATIO_MAX = 0.50

# ── カードフィルタパラメータ ──────────────────────────────
# 採否の主基準は縦横比のみ（rev2 §1.2.3）。範囲 1.6〜1.8 は名刺という物の形そのもので
# 画像・解像度・撮影枚数によらない固定範囲＝確定値（日本標準 91×55≈1.65／欧米 89×51≈1.75）。
_ASPECT_MIN = 1.6
_ASPECT_MAX = 1.8

# ── 未確定パラメータ（settings から読む・仮値・評価基準で詰める） ──
# rev2 §1.2.4（絶対下限）／§1.4・§4.2（統合の IoU・中心距離）参照。ハードコードせず
# settings 値を優先し、settings 未設定時のみ下記フォールバック（= settings の仮値と同値）。
_DEFAULT_MIN_CARD_AREA_PX = 8000        # OCR 不能な極小候補を落とす緩い絶対下限[px^2]（仮値・未確定）
_DEFAULT_DEDUP_IOU_THRESHOLD = 0.30     # マスク横断 重複統合の IoU しきい値（仮値・未確定）
_DEFAULT_DEDUP_CENTER_DIST_RATIO = 0.50  # 中心距離 ≤ ratio×対角線 を IoU と併用（仮値・未確定）


def _min_card_area_px() -> float:
    """[性質] 準関数（settings 読取） / OCR 不能な極小候補を落とす緩い絶対下限[px^2]。"""
    return float(getattr(settings, "OPENCV_MIN_CARD_AREA_PX", _DEFAULT_MIN_CARD_AREA_PX))


def _dedup_iou_threshold() -> float:
    """[性質] 準関数（settings 読取） / マスク横断 重複統合の IoU しきい値（仮値・未確定）。"""
    return float(getattr(settings, "OPENCV_DEDUP_IOU_THRESHOLD", _DEFAULT_DEDUP_IOU_THRESHOLD))


def _dedup_center_dist_ratio() -> float:
    """[性質] 準関数（settings 読取） / 重複統合の中心距離しきい比（仮値・未確定）。"""
    return float(getattr(settings, "OPENCV_DEDUP_CENTER_DIST_RATIO", _DEFAULT_DEDUP_CENTER_DIST_RATIO))


# ── 透視変換パラメータ ────────────────────────────────────
_MIN_WARP_WIDTH = 100
_MIN_WARP_HEIGHT = 50

# ── 反転リトライ後 polygon の外側マージン拡張 ────────────
# 反転後マスクは「名刺の中身（白地）の連続領域」を拾うため、polygon が
# 名刺端の数 px 内側で確定しがち。通常検出と同等の余白を持たせるため
# 反転リトライ経路でのみ polygon を外側に拡張する（通常検出は不変）。
_INVERTED_POLYGON_EXPAND_RATIO = 0.02   # 対角線長に対する拡張比率
_INVERTED_POLYGON_EXPAND_MIN_PX = 10    # 拡張量の下限ピクセル（小さい polygon でも一定余白を確保）

# 候補抽出を回すマスク名（順序固定）。
_MASK_NAMES = ("diff", "edge", "sat")


def results_from_debug_result(debug_result: dict) -> list[dict]:
    """detect_cards_with_debug() の戻りから最終 attempt の検出結果（results）を取り出す。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] debug_result: detect_cards_with_debug() の戻り値 dict
    [出力] list[dict]: 最終 attempt の results。attempts が無ければ空リスト。

    検出結果の在り処（attempts[-1]["results"]）を知る**唯一の窓口**。detect_cards() も
    pipeline_coordinator もこの関数を通して results を受け取り、「results がどこにあるか」を
    各所に重複させない。返り値構造を attempts[] へ変えたのに pipeline 側が旧トップレベル
    results を読み続けて全件 garbage 化したバグ（1f9712f）の再発防止。
    """
    attempts = debug_result.get("attempts") or []
    if not attempts:
        return []
    return attempts[-1].get("results") or []


def detect_cards(image_path: str) -> list[dict]:
    """画像から名刺を検出し、透視変換済み画像（向き補正なし）と4隅座標を返す。

    [性質] 純関数（ファイル読み取り・settings 読取のみ・DB 操作なし・副作用なし）
    [入力] image_path: 元画像のファイルパス
    [出力] CardDetectionResult のリスト。各要素は以下の形式：
      {
        "polygon": {
          "top_left":     {"x": float, "y": float},
          "top_right":    {"x": float, "y": float},
          "bottom_right": {"x": float, "y": float},
          "bottom_left":  {"x": float, "y": float}
        },
        "warped_image": PIL.Image.Image  # 透視変換済み画像（向き補正なし）
      }
      検出失敗時は空リストを返す（例外を外に漏らさない）。
    """
    try:
        return results_from_debug_result(_detect_with_debug(image_path))
    except Exception as e:
        logger.warning("detect_cards failed for %s: %s", image_path, e)
        return []


def detect_cards_with_debug(image_path: str) -> dict:
    """画像から名刺を検出し、各試行（通常／反転リトライ）の中間データを「全部入り」で返す。

    検出ロジックは detect_cards() と同一。試行回数（attempt_no）軸で対称構造に並べる。
    反転リトライが走らなかった場合は attempts 配列が 1 要素のみ。
    走った場合は 2 要素（attempt_no=1 が通常、attempt_no=2 が反転後）。

    rev2 方式では OR 合成を廃止し、各 attempt の中で diff/edge/sat を **マスク別**に
    保持する（masks[<name>]）。各マスクは生（raw_image）とクロージング後（closed_image）の
    両画像を持ち、候補フィルタ判定（candidates_filter）もマスク別に持つ。

    [性質] 純関数（ファイル読み取り・settings 読取のみ・DB 操作なし・副作用なし）
    [入力] image_path: 元画像のファイルパス
    [出力] dict:
      {
        "image_size": {"width": int, "height": int, "area": int},

        "attempts": [
            {
                "attempt_no": 1,            # 1=通常, 2=反転リトライ
                "type": "normal",           # "normal" | "inverted"
                "masks": {
                    "diff": {
                        "raw_image":    PIL.Image.Image,  # 生マスク
                        "closed_image": PIL.Image.Image,  # クロージング後マスク
                        "raw_white_ratio":    float,
                        "closed_white_ratio": float,
                        "excluded":           bool,       # 候補抽出から除外したか（sat 暴走時の sat 等）
                        "contours_count":     int,
                        "candidates_filter":  list[dict],  # このマスクの全輪郭フィルタ判定（後述）
                    },
                    "edge": {...},
                    "sat":  {...},
                },
                "candidates_dedup":  list[dict],  # マスク横断 重複除去判定（後述）
                "warp_failures":     list[dict],  # 透視変換サイズ未満候補（後述）
                "results": [
                    {"polygon": dict, "warped_image": PIL.Image.Image},
                    ...
                ],
            },
            # attempt_no=2 は反転リトライが走った場合のみ
        ],

        # masks[*].candidates_filter の各要素：
        #   {area, area_ratio, rect_center, rect_size, rect_angle,
        #    aspect_ratio, passed, reject_reason}
        #   reject_reason: "" | "too_small" | "zero_size" | "aspect_invalid"
        #   （rev2: area_too_small/area_too_large は廃止。サイズは絶対下限 too_small のみ）
        # candidates_dedup の各要素：
        #   {bbox: [x,y,w,h], area, mask, kept, overlap_with}
        #   mask: その候補がどのマスク由来か（"diff"|"edge"|"sat"）
        # warp_failures の各要素：
        #   {polygon, computed_width, computed_height, min_required: [100, 50]}

        "sat_fallback": {
            # 高彩度背景の診断判定（sat_white_ratio が threshold 超で triggered=True）。
            # rev2 では除外に使わない（診断情報のみ。OR 合成廃止に伴い除外動作は撤廃）。
            "triggered":       bool,
            "sat_white_ratio": float,
            "threshold":       float,
        },

        "or_inversion": {
            # 反転リトライ判定結果（OR 廃止後も反転リトライ自体は残るためキー名は踏襲）。
            "attempted":     bool,
            "passed_before": int,
            "passed_after":  int,
            "improved":      bool,
            "polygon_expand": {"ratio": float, "min_px": int},
        },

        "error_message": str,  # 失敗時のみ例外メッセージ。成功時は空文字列
      }

      検出処理で例外が発生した場合は次を返す（例外を外に漏らさない）：
        {"attempts": [], "error_message": str(例外)}
    """
    try:
        return _detect_with_debug(image_path)
    except Exception as e:
        logger.warning("detect_cards_with_debug failed for %s: %s", image_path, e)
        return {"attempts": [], "error_message": str(e)}


def _white_ratio(arr: np.ndarray) -> float:
    """[性質] 純関数 / 2値マスクの白画素率（>0 の比率）を返す。"""
    return float((arr > 0).sum() / arr.size) if arr.size else 0.0


def _detect_with_debug(image_path: str) -> dict:
    # ① Pillow で開く・EXIF 補正
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        np_rgb = np.array(img.convert("RGB"))

    bgr = cv2.cvtColor(np_rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    image_area = w * h
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # ② マスク生成（生マスク 3 枚 ＋ 高彩度背景の診断判定）。OR 合成はしない。
    mask_data = _build_masks(bgr, gray)
    raw_masks = mask_data["raw"]

    # ③ 通常 attempt：各マスク個別にクロージング → 候補抽出 → マスク横断重複除去 → 透視変換
    attempt_1 = _run_attempt(
        raw_masks, np_rgb, image_area,
        expand_ratio=0.0, expand_min_px=0,
    )
    attempts = [_attempt_dict(1, "normal", attempt_1)]

    # ④ 反転リトライ判定：通常で passed=0 のとき各生マスクを反転して再試行（新方式に通す）
    passed_before = attempt_1["passed_count_total"]
    or_inversion = {
        "attempted": False,
        "passed_before": passed_before,
        "passed_after": passed_before,
        "improved": False,
        "polygon_expand": {"ratio": 0.0, "min_px": 0},
    }

    if passed_before == 0:
        inv_masks = {name: cv2.bitwise_not(m) for name, m in raw_masks.items()}
        attempt_2 = _run_attempt(
            inv_masks, np_rgb, image_area,
            expand_ratio=_INVERTED_POLYGON_EXPAND_RATIO,
            expand_min_px=_INVERTED_POLYGON_EXPAND_MIN_PX,
        )
        passed_after = attempt_2["passed_count_total"]
        or_inversion = {
            "attempted": True,
            "passed_before": 0,
            "passed_after": passed_after,
            "improved": passed_after > 0,
            "polygon_expand": {
                "ratio":  _INVERTED_POLYGON_EXPAND_RATIO,
                "min_px": _INVERTED_POLYGON_EXPAND_MIN_PX,
            },
        }
        attempts.append(_attempt_dict(2, "inverted", attempt_2))

    return {
        "image_size": {"width": int(w), "height": int(h), "area": int(image_area)},
        "attempts": attempts,
        "sat_fallback": mask_data["sat_fallback"],
        "or_inversion": or_inversion,
        "error_message": "",
    }


def _attempt_dict(attempt_no: int, type_: str, attempt: dict) -> dict:
    """[性質] 純関数 / _run_attempt の戻り値を attempt メタ構造へ整える内部ヘルパー。"""
    return {
        "attempt_no": attempt_no,
        "type": type_,
        "masks": attempt["mask_results"],
        "candidates_dedup": attempt["candidates_dedup"],
        "warp_failures": attempt["warp_failures"],
        "results": attempt["results"],
    }


def _build_masks(bgr: np.ndarray, gray: np.ndarray) -> dict:
    """[性質] 純関数 / 生マスク 3 枚（diff/edge/sat）と高彩度背景の診断判定を返す。

    OR 合成・クロージングはここでは行わない（クロージングは attempt 内でマスク別に行う）。
    mask_sat の白画素率が _SAT_WHITE_RATIO_MAX を超えた場合は triggered=True を立てるが、
    rev2 では除外には使わない（高彩度背景の診断情報としてのみ記録する）。

    返却 dict：
      raw: {"diff": ndarray, "edge": ndarray, "sat": ndarray}  # uint8 single-channel 生マスク
      sat_fallback: {triggered: bool, sat_white_ratio: float, threshold: float}
    """
    # マスク1: グレースケール差分（白・黒など輝度差のあるカード）
    k_bg = cv2.getStructuringElement(cv2.MORPH_RECT, _ERODE_KERNEL)
    bg_min = cv2.erode(gray, k_bg, iterations=1)
    diff = cv2.absdiff(gray.astype(np.int16), bg_min.astype(np.int16)).astype(np.uint8)
    _, mask_diff = cv2.threshold(diff, _DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)

    # マスク2: Canny エッジ（輝度差が小さくてもエッジが出るカード）
    edges = cv2.Canny(gray, _CANNY_LOW, _CANNY_HIGH)
    k_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, _CANNY_DILATE_KERNEL)
    mask_edge = cv2.dilate(edges, k_dilate, iterations=2)

    # マスク3: HSV 彩度（黄・赤・青など有彩色カード）
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    _, mask_sat = cv2.threshold(sat, _SAT_THRESHOLD, 255, cv2.THRESH_BINARY)

    # 高彩度背景の診断判定（sat_white_ratio を記録。rev2 では除外には使わない）
    sat_white_ratio = (
        float(np.count_nonzero(mask_sat)) / float(mask_sat.size)
        if mask_sat.size else 0.0
    )
    sat_fallback_triggered = sat_white_ratio > _SAT_WHITE_RATIO_MAX

    return {
        "raw": {
            "diff": mask_diff,
            "edge": mask_edge,
            "sat":  mask_sat,
        },
        "sat_fallback": {
            "triggered": sat_fallback_triggered,
            "sat_white_ratio": sat_white_ratio,
            "threshold": _SAT_WHITE_RATIO_MAX,
        },
    }


def _close_mask(mask: np.ndarray) -> np.ndarray:
    """[性質] 純関数 / マスクをクロージング（同一マスク内で割れた塊を繋ぐ・rev2 §1.2.5）。"""
    k_close = cv2.getStructuringElement(cv2.MORPH_RECT, _CLOSE_KERNEL)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close, iterations=2)


def _run_attempt(
    raw_masks: dict, np_rgb: np.ndarray, image_area: int,
    expand_ratio: float, expand_min_px: int,
) -> dict:
    """[性質] 純関数 / 1 試行ぶんの「各マスク個別抽出 → マスク横断重複除去 → 透視変換」を実行。

    通常 attempt と反転リトライ attempt で共有する。各マスク（diff/edge/sat）について
    クロージング → 候補抽出を独立に行い、合格候補をマスク横断で重複除去してから warp する。
    rev2 では全マスクを常に候補抽出する（sat 除外フォールバックは撤廃。各 mask の
    "excluded" は常に False）。

    [入力]
      raw_masks     : {"diff", "edge", "sat"} の生マスク dict（反転 attempt では反転済み）
      np_rgb        : 元画像 (RGB ndarray) — 透視変換のソース
      image_area    : 画像面積 (w * h)
      expand_ratio  : 0 より大なら透視変換直前に polygon を外側へ拡張（反転リトライ経路）
      expand_min_px : 拡張量の下限ピクセル（expand_ratio>0 のときのみ参照）
    [出力] dict:
      {
        "mask_results": {<name>: {raw_image, closed_image, raw_white_ratio,
                                  closed_white_ratio, excluded, contours_count,
                                  candidates_filter}},
        "candidates_dedup": list[dict],
        "warp_failures":    list[dict],
        "results":          list[dict],
        "passed_count_total": int,   # 全マスク合算の passed 件数（反転リトライ判定に使用）
      }
    """
    mask_results: dict = {}
    passed_all: list = []  # (mask_name, area, box, bx, by, bw, bh)
    passed_count_total = 0

    for name in _MASK_NAMES:
        raw = raw_masks[name]
        closed = _close_mask(raw)
        # rev2: OR 合成廃止に伴い sat 除外フォールバックは撤廃。全マスクを常に候補抽出する
        # （高彩度背景での sat 暴走＝机全面の巨大塊は縦横比フィルタ §1.2.3 が落とす）。
        contours_count, candidates_filter, passed = _extract_candidates_from_mask(
            closed, image_area
        )

        mask_results[name] = {
            "raw_image":    Image.fromarray(raw),
            "closed_image": Image.fromarray(closed),
            "raw_white_ratio":    _white_ratio(raw),
            "closed_white_ratio": _white_ratio(closed),
            "excluded":           False,
            "contours_count":     contours_count,
            "candidates_filter":  candidates_filter,
        }
        passed_count_total += len(passed)
        for p in passed:
            passed_all.append((name, *p))

    candidates_dedup, kept = _dedup_candidates(passed_all)
    results, warp_failures = _warp_kept(kept, np_rgb, expand_ratio, expand_min_px)

    return {
        "mask_results": mask_results,
        "candidates_dedup": candidates_dedup,
        "warp_failures": warp_failures,
        "results": results,
        "passed_count_total": passed_count_total,
    }


def _extract_candidates_from_mask(mask_closed: np.ndarray, image_area: int) -> tuple:
    """[性質] 純関数 / クローズ済み 1 マスクから候補を抽出し、フィルタ判定を全件記録する。

    採否の主基準は縦横比（_ASPECT_MIN〜_ASPECT_MAX）のみ。サイズは OCR 不能な極小を
    落とす緩い絶対下限（_min_card_area_px）としてのみ使い、面積上限・面積比は使わない。
    票数ルールは設けない（1 マスクで出ても比が範囲内なら合格・rev2 §1.2 の3）。

    [出力] (contours_count, candidates_filter, passed)
      candidates_filter: 全輪郭のフィルタ判定 dict のリスト
      passed: 合格候補 (area, box, bx, by, bw, bh) のリスト
    """
    contours, _ = cv2.findContours(
        mask_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    min_area = _min_card_area_px()
    candidates_filter: list = []
    passed: list = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        area_ratio = (area / image_area) if image_area > 0 else 0.0

        hull = cv2.convexHull(cnt)
        rect = cv2.minAreaRect(hull)
        (cx, cy), (rw, rh), angle = rect

        passed_flag = False
        reject_reason = ""
        aspect_ratio: float | None = None

        if min(rw, rh) == 0:
            reject_reason = "zero_size"
        elif area < min_area:
            # OCR 不能な極小候補を落とす緩い絶対下限のみ（採否の主基準ではない・rev2 §1.2.4）
            reject_reason = "too_small"
        else:
            aspect_ratio = max(rw, rh) / min(rw, rh)
            if not (_ASPECT_MIN <= aspect_ratio <= _ASPECT_MAX):
                reject_reason = "aspect_invalid"
            else:
                passed_flag = True

        candidates_filter.append({
            "area": area,
            "area_ratio": float(area_ratio),
            "rect_center": [float(cx), float(cy)],
            "rect_size":   [float(rw), float(rh)],
            "rect_angle":  float(angle),
            "aspect_ratio": aspect_ratio,
            "passed": passed_flag,
            "reject_reason": reject_reason,
        })

        if not passed_flag:
            continue

        box = cv2.boxPoints(rect).astype(np.float32)
        bx, by, bw, bh = cv2.boundingRect(box.astype(np.int32))
        passed.append((area, box, bx, by, bw, bh))

    return len(contours), candidates_filter, passed


def _dedup_candidates(passed_all: list) -> tuple:
    """[性質] 純関数 / マスク横断で重複候補を中心座標＋IoU で1件に統合する（rev2 §1.2 の4）。

    面積降順で走査し、既採用矩形と「IoU がしきい値以上 かつ 中心距離が対角線比以内」の
    候補を重複とみなして落とす。採用座標は当面「面積最大の候補」を採る（仮・rev2 §1.4 で未確定）。

    [入力] passed_all: (mask_name, area, box, bx, by, bw, bh) のリスト
    [出力] (candidates_dedup, kept)
      candidates_dedup: {bbox, area, mask, kept, overlap_with} のリスト（全件記録）
      kept: 採用候補 (mask_name, box, bx, by) のリスト（card_index 順にソート済み）
    """
    # 面積降順 → 最初に採用される＝面積最大の候補（採用座標の仮ルール・rev2 §1.4）
    ordered = sorted(passed_all, key=lambda c: c[1], reverse=True)

    iou_threshold = _dedup_iou_threshold()
    center_dist_ratio = _dedup_center_dist_ratio()

    candidates_dedup: list = []
    kept: list = []           # (mask_name, box, bx, by)
    kept_bboxes: list = []    # (bx, by, bw, bh)
    for mask_name, area, box, bx, by, bw, bh in ordered:
        dup_idx = _find_duplicate_index(
            bx, by, bw, bh, kept_bboxes, iou_threshold, center_dist_ratio
        )
        if dup_idx is None:
            candidates_dedup.append({
                "bbox": [int(bx), int(by), int(bw), int(bh)],
                "area": float(area),
                "mask": mask_name,
                "kept": True,
                "overlap_with": None,
            })
            kept.append((mask_name, box, bx, by))
            kept_bboxes.append((bx, by, bw, bh))
        else:
            candidates_dedup.append({
                "bbox": [int(bx), int(by), int(bw), int(bh)],
                "area": float(area),
                "mask": mask_name,
                "kept": False,
                "overlap_with": dup_idx,
            })

    # top-left の y 優先・x 次でソート（card_index の順序確定）
    kept.sort(key=lambda r: (r[3], r[2]))  # r = (mask, box, bx, by) → (by, bx)
    return candidates_dedup, kept


def _warp_kept(
    kept: list, np_rgb: np.ndarray, expand_ratio: float, expand_min_px: int
) -> tuple:
    """[性質] 純関数 / 採用候補を透視変換し、(results, warp_failures) を返す。

    expand_ratio>0（反転リトライ経路）のときは polygon を外側に拡張してから warp する。
    サイズ基準未満（_MIN_WARP_WIDTH / _MIN_WARP_HEIGHT）は warp_failures に記録する。
    """
    h_img, w_img = np_rgb.shape[:2]
    results: list = []
    warp_failures: list = []
    for _mask_name, box, _bx, _by in kept:
        sorted_pts = _sort_corners(box)
        if expand_ratio > 0.0:
            sorted_pts = _expand_polygon(
                sorted_pts, expand_ratio, expand_min_px, w_img, h_img
            )
        warped_rgb, ww, hh = _warp_card(np_rgb, sorted_pts)
        if warped_rgb is None:
            warp_failures.append({
                "polygon": _pts_to_polygon(sorted_pts),
                "computed_width":  ww,
                "computed_height": hh,
                "min_required": [_MIN_WARP_WIDTH, _MIN_WARP_HEIGHT],
            })
            continue
        results.append({
            "polygon": _pts_to_polygon(sorted_pts),
            "warped_image": Image.fromarray(warped_rgb),
        })
    return results, warp_failures


def _sort_corners(pts: np.ndarray) -> np.ndarray:
    """[性質] 純関数 / 4点を top_left → top_right → bottom_right → bottom_left に整列。

    pts: shape (4, 2) の float32 配列
    返却: shape (4, 2) の float32 配列（同順）
    整列基準: s = x+y, d = y-x
      top_left     = argmin(s)  … x+y 最小
      top_right    = argmin(d)  … x-y 最大（= y-x 最小）
      bottom_right = argmax(s)  … x+y 最大
      bottom_left  = argmax(d)  … x-y 最小（= y-x 最大）
    """
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).flatten()   # d[i] = y_i - x_i
    return np.array([
        pts[np.argmin(s)],
        pts[np.argmin(d)],
        pts[np.argmax(s)],
        pts[np.argmax(d)],
    ], dtype=np.float32)


def _pts_to_polygon(pts: np.ndarray) -> dict:
    """[性質] 純関数 / shape(4,2) 配列を polygon dict に変換。"""
    keys = ("top_left", "top_right", "bottom_right", "bottom_left")
    return {k: {"x": float(pts[i][0]), "y": float(pts[i][1])} for i, k in enumerate(keys)}


def _warp_card(np_rgb: np.ndarray, src_pts: np.ndarray) -> tuple[np.ndarray | None, int, int]:
    """[性質] 純関数 / 4点透視変換を行い (warped_rgb, computed_w, computed_h) を返す。

    出力サイズは polygon の実ピクセル辺長から算出する（人工拡大しない）。
    サイズ基準未満（_MIN_WARP_WIDTH / _MIN_WARP_HEIGHT）の場合は warped_rgb=None を返すが、
    computed_w / computed_h は warp_failures 記録用に必ず返す。
    """
    tl, tr, br, bl = src_pts
    w = int(max(
        np.linalg.norm(tr - tl),
        np.linalg.norm(br - bl),
    ))
    h = int(max(
        np.linalg.norm(bl - tl),
        np.linalg.norm(br - tr),
    ))
    if w < _MIN_WARP_WIDTH or h < _MIN_WARP_HEIGHT:
        return None, w, h
    dst_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped = cv2.warpPerspective(np_rgb, M, (w, h), flags=cv2.INTER_LANCZOS4)
    return warped, w, h


def _expand_polygon(
    pts: np.ndarray, ratio: float, min_px: int, max_w: int, max_h: int
) -> np.ndarray:
    """[性質] 純関数 / polygon (4頂点) を中心から外側に拡張し、画像範囲内にクリップ。

    各頂点を「中心からその頂点への単位ベクトル」方向に margin だけシフトする。
    margin = max(対角線長 × ratio, min_px) ピクセル。
    シフト後は np.clip で画像範囲 [0, max_w-1] × [0, max_h-1] に押し込める。

    pts: shape (4, 2) の float32 配列。順序は _sort_corners 整列後を想定（TL/TR/BR/BL）。
    返却: shape (4, 2) の float32 配列（同順）。
    """
    cx = float(pts[:, 0].mean())
    cy = float(pts[:, 1].mean())
    diag = float(np.linalg.norm(pts[2] - pts[0]))  # TL→BR
    margin = max(diag * ratio, float(min_px))

    expanded = np.zeros_like(pts)
    for i in range(4):
        x, y = float(pts[i][0]), float(pts[i][1])
        dx, dy = x - cx, y - cy
        d = (dx * dx + dy * dy) ** 0.5
        if d > 0.0:
            scale = (d + margin) / d
            expanded[i] = (cx + dx * scale, cy + dy * scale)
        else:
            expanded[i] = (x, y)

    expanded[:, 0] = np.clip(expanded[:, 0], 0, max_w - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, max_h - 1)
    return expanded


def _find_duplicate_index(
    x1, y1, w1, h1, kept_bboxes: list, iou_threshold: float, center_dist_ratio: float
) -> int | None:
    """[性質] 純関数 / 新候補が既採用矩形と「同一名刺」かを中心座標＋IoU で判定する。

    判定条件（rev2 §1.2 の4・§1.3.2 #3。AND 併用は保守的＝過統合を避ける。しきい値・
    AND/OR の別は未確定で評価基準で詰める・§1.4/§4.2）：
      IoU ≥ iou_threshold  かつ  中心距離 ≤ center_dist_ratio × 既採用矩形の対角線長
    一致した場合は kept_bboxes 内の該当インデックスを、しなければ None を返す。
    """
    a1 = w1 * h1
    c1x, c1y = x1 + w1 / 2.0, y1 + h1 / 2.0
    for idx, (x2, y2, w2, h2) in enumerate(kept_bboxes):
        iw = min(x1 + w1, x2 + w2) - max(x1, x2)
        ih = min(y1 + h1, y2 + h2) - max(y1, y2)
        inter = (iw * ih) if (iw > 0 and ih > 0) else 0.0
        union = a1 + w2 * h2 - inter
        iou = (inter / union) if union > 0 else 0.0

        c2x, c2y = x2 + w2 / 2.0, y2 + h2 / 2.0
        center_dist = ((c1x - c2x) ** 2 + (c1y - c2y) ** 2) ** 0.5
        diag2 = (w2 * w2 + h2 * h2) ** 0.5
        center_near = center_dist <= center_dist_ratio * diag2

        if iou >= iou_threshold and center_near:
            return idx
    return None
