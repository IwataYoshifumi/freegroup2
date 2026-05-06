"""名刺領域検出・4隅座標取得・透視変換（OpenCV 実装）。

3種のマスク（輝度差・Canny・HSV彩度）を OR 合成して名刺候補を抽出し、
minAreaRect → boxPoints → 幾何ソートで4隅座標を取得する。
透視変換で正立化した画像をそのまま返す（向き補正は行わない）。
"""

import logging

import cv2
import numpy as np
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

# ── カードフィルタパラメータ ──────────────────────────────
_AREA_MIN_RATIO = 0.008
_AREA_MAX_RATIO = 0.45
_ASPECT_MIN = 1.1
_ASPECT_MAX = 4.5
_OVERLAP_THRESHOLD = 0.50

# ── 透視変換パラメータ ────────────────────────────────────
_MIN_WARP_WIDTH = 100
_MIN_WARP_HEIGHT = 50


def detect_cards(image_path: str) -> list[dict]:
    """画像から名刺を検出し、透視変換済み画像（向き補正なし）と4隅座標を返す。

    [性質] 純関数（ファイル読み取りのみ・DB 操作なし・副作用なし）
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
        return _detect_with_debug(image_path)["results"]
    except Exception as e:
        logger.warning("detect_cards failed for %s: %s", image_path, e)
        return []


def detect_cards_with_debug(image_path: str) -> dict:
    """画像から名刺を検出し、最終結果と中間データを「全部入り」で返す（デバッグ用）。

    検出ロジックは detect_cards() と同一。各段階の中間データを収集して返す。

    [性質] 純関数（ファイル読み取りのみ・DB 操作なし・副作用なし）
    [入力] image_path: 元画像のファイルパス
    [出力] dict:
      {
        "results": list[dict],
            # detect_cards() と同じ最終結果リスト。各要素：
            # {"polygon": <polygon dict>, "warped_image": PIL.Image.Image}

        "image_size": {"width": int, "height": int, "area": int},

        "masks": {
            "mask_diff":   PIL.Image.Image,  # 輝度差マスク
            "mask_edge":   PIL.Image.Image,  # Canny エッジマスク
            "mask_sat":    PIL.Image.Image,  # HSV 彩度マスク
            "mask_or":     PIL.Image.Image,  # 上記3種を OR 合成
            "mask_closed": PIL.Image.Image,  # クローズ処理後の最終マスク
        },

        "contours_count": int,  # findContours で取得した全輪郭数

        "candidates_filter": [
            # 全輪郭に対するフィルタ判定結果（通過/除外問わず全件）
            {
                "area": float,                 # 輪郭面積
                "area_ratio": float,           # area / image_area
                "rect_center": [float, float], # minAreaRect の中心 (cx, cy)
                "rect_size":   [float, float], # minAreaRect の (rw, rh)
                "rect_angle":  float,          # minAreaRect の回転角
                "aspect_ratio": float | None,  # max/min（min=0 のとき None）
                "passed": bool,
                "reject_reason": str,
                    # 通過時は ""
                    # 除外時は "area_too_small" / "area_too_large"
                    #         / "zero_size" / "aspect_invalid"
            },
            ...
        ],

        "candidates_dedup": [
            # フィルタ通過候補に対する重複除去判定（面積大きい順に処理）
            {
                "bbox": [int, int, int, int],  # boundingRect (bx, by, bw, bh)
                "area": float,
                "kept": bool,
                "overlap_with": int | None,
                    # 除外時は重複した kept のインデックス
                    # 保持時は None
            },
            ...
        ],

        "warp_failures": [
            # 透視変換でサイズ基準未満になった候補
            {
                "polygon": dict,             # _pts_to_polygon と同形式
                "computed_width":  int,
                "computed_height": int,
                "min_required": [100, 50],   # [_MIN_WARP_WIDTH, _MIN_WARP_HEIGHT]
            },
            ...
        ],

        "error_message": str,  # 失敗時のみ例外メッセージ。成功時は空文字列
      }

      検出処理で例外が発生した場合は次を返す（例外を外に漏らさない）：
        {"results": [], "error_message": str(例外)}
    """
    try:
        return _detect_with_debug(image_path)
    except Exception as e:
        logger.warning("detect_cards_with_debug failed for %s: %s", image_path, e)
        return {"results": [], "error_message": str(e)}


def _detect_with_debug(image_path: str) -> dict:
    # ① Pillow で開く・EXIF 補正
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        np_rgb = np.array(img.convert("RGB"))

    bgr = cv2.cvtColor(np_rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    image_area = w * h
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # ② マスク生成（中間マスクも収集）
    masks_np = _build_mask(bgr, gray)
    mask = masks_np["mask_closed"]

    # ③ 輪郭検出
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # ④ 候補抽出（面積・アスペクト比フィルタ／全件記録）
    candidates_filter: list[dict] = []
    candidates: list = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        area_ratio = (area / image_area) if image_area > 0 else 0.0

        hull = cv2.convexHull(cnt)
        rect = cv2.minAreaRect(hull)
        (cx, cy), (rw, rh), angle = rect

        passed = False
        reject_reason = ""
        aspect_ratio: float | None = None

        if area_ratio < _AREA_MIN_RATIO:
            reject_reason = "area_too_small"
        elif area_ratio > _AREA_MAX_RATIO:
            reject_reason = "area_too_large"
        elif min(rw, rh) == 0:
            reject_reason = "zero_size"
        else:
            aspect_ratio = max(rw, rh) / min(rw, rh)
            if not (_ASPECT_MIN <= aspect_ratio <= _ASPECT_MAX):
                reject_reason = "aspect_invalid"
            else:
                passed = True

        candidates_filter.append({
            "area": area,
            "area_ratio": float(area_ratio),
            "rect_center": [float(cx), float(cy)],
            "rect_size":   [float(rw), float(rh)],
            "rect_angle":  float(angle),
            "aspect_ratio": aspect_ratio,
            "passed": passed,
            "reject_reason": reject_reason,
        })

        if not passed:
            continue

        box = cv2.boxPoints(rect).astype(np.float32)
        bx, by, bw, bh = cv2.boundingRect(box.astype(np.int32))
        candidates.append((area, box, bx, by, bw, bh))

    # ⑤ 面積大きい順に重複除去（判定結果を全件記録）
    candidates.sort(key=lambda c: c[0], reverse=True)
    candidates_dedup: list[dict] = []
    kept = []
    kept_bboxes: list[tuple[int, int, int, int]] = []
    for area, box, bx, by, bw, bh in candidates:
        overlap_idx = _find_overlap_index(bx, by, bw, bh, kept_bboxes)
        if overlap_idx is None:
            candidates_dedup.append({
                "bbox": [int(bx), int(by), int(bw), int(bh)],
                "area": float(area),
                "kept": True,
                "overlap_with": None,
            })
            kept.append((box, bx, by))
            kept_bboxes.append((bx, by, bw, bh))
        else:
            candidates_dedup.append({
                "bbox": [int(bx), int(by), int(bw), int(bh)],
                "area": float(area),
                "kept": False,
                "overlap_with": overlap_idx,
            })

    # ⑥ top-left の y 優先・x 次でソート（card_index の順序確定）
    kept.sort(key=lambda r: (r[2], r[1]))

    # ⑦ 透視変換（サイズ基準未満は warp_failures に記録）
    results: list[dict] = []
    warp_failures: list[dict] = []
    for box, _bx, _by in kept:
        sorted_pts = _sort_corners(box)
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

    return {
        "results": results,
        "image_size": {"width": int(w), "height": int(h), "area": int(image_area)},
        "masks": {
            "mask_diff":   Image.fromarray(masks_np["mask_diff"]),
            "mask_edge":   Image.fromarray(masks_np["mask_edge"]),
            "mask_sat":    Image.fromarray(masks_np["mask_sat"]),
            "mask_or":     Image.fromarray(masks_np["mask_or"]),
            "mask_closed": Image.fromarray(masks_np["mask_closed"]),
        },
        "contours_count": len(contours),
        "candidates_filter": candidates_filter,
        "candidates_dedup": candidates_dedup,
        "warp_failures": warp_failures,
        "error_message": "",
    }


def _build_mask(bgr: np.ndarray, gray: np.ndarray) -> dict:
    """[性質] 純関数 / 3種マスクを OR 合成し、各中間マスクと最終マスクを dict で返す。

    返却 dict のキー：
      mask_diff   : 輝度差マスク（uint8 single-channel ndarray）
      mask_edge   : Canny エッジマスク
      mask_sat    : HSV 彩度マスク
      mask_or     : 上記3種を OR 合成したマスク
      mask_closed : クローズ処理後の最終マスク（呼び出し側はこれを使う）
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

    # OR 合成 → クローズで内部穴埋め・ノイズ除去
    mask_or = cv2.bitwise_or(mask_diff, mask_edge)
    mask_or = cv2.bitwise_or(mask_or, mask_sat)
    k_close = cv2.getStructuringElement(cv2.MORPH_RECT, _CLOSE_KERNEL)
    mask_closed = cv2.morphologyEx(mask_or, cv2.MORPH_CLOSE, k_close, iterations=2)

    return {
        "mask_diff":   mask_diff,
        "mask_edge":   mask_edge,
        "mask_sat":    mask_sat,
        "mask_or":     mask_or,
        "mask_closed": mask_closed,
    }


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


def _find_overlap_index(x1, y1, w1, h1, kept_list: list) -> int | None:
    """[性質] 純関数 / 新候補が保持済み矩形と 50% 以上重複するかを判定。

    重複した場合は kept_list 内の該当インデックスを返し、重複しない場合は None を返す。
    """
    a1 = w1 * h1
    for idx, (x2, y2, w2, h2) in enumerate(kept_list):
        iw = min(x1 + w1, x2 + w2) - max(x1, x2)
        ih = min(y1 + h1, y2 + h2) - max(y1, y2)
        if iw <= 0 or ih <= 0:
            continue
        min_area = min(a1, w2 * h2)
        if min_area > 0 and (iw * ih) / min_area >= _OVERLAP_THRESHOLD:
            return idx
    return None
