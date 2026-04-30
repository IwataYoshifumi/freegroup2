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
        return _detect(image_path)
    except Exception as e:
        logger.warning("detect_cards failed for %s: %s", image_path, e)
        return []


def _detect(image_path: str) -> list[dict]:
    # ① Pillow で開く・EXIF 補正
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        np_rgb = np.array(img.convert("RGB"))

    bgr = cv2.cvtColor(np_rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    image_area = w * h
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # ② マスク生成（3種 OR）
    mask = _build_mask(bgr, gray)

    # ③ 輪郭検出
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # ④ 候補抽出（面積・アスペクト比フィルタ）
    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if not (_AREA_MIN_RATIO <= area / image_area <= _AREA_MAX_RATIO):
            continue
        hull = cv2.convexHull(cnt)
        rect = cv2.minAreaRect(hull)
        rw, rh = rect[1]
        if min(rw, rh) == 0:
            continue
        ratio = max(rw, rh) / min(rw, rh)
        if not (_ASPECT_MIN <= ratio <= _ASPECT_MAX):
            continue
        box = cv2.boxPoints(rect).astype(np.float32)
        bx, by, bw, bh = cv2.boundingRect(box.astype(np.int32))
        candidates.append((area, box, bx, by, bw, bh))

    # ⑤ 面積大きい順に重複除去
    candidates.sort(key=lambda c: c[0], reverse=True)
    kept = []
    kept_bboxes = []
    for area, box, bx, by, bw, bh in candidates:
        if not _is_overlapping(bx, by, bw, bh, kept_bboxes):
            kept.append((box, bx, by))
            kept_bboxes.append((bx, by, bw, bh))

    # ⑥ top-left の y 優先・x 次でソート（card_index の順序確定）
    kept.sort(key=lambda r: (r[2], r[1]))

    # ⑦ 透視変換
    results = []
    for box, _bx, _by in kept:
        sorted_pts = _sort_corners(box)
        warped_rgb = _warp_card(np_rgb, sorted_pts)
        if warped_rgb is None:
            continue
        pil_warped = Image.fromarray(warped_rgb)
        results.append({
            "polygon": _pts_to_polygon(sorted_pts),
            "warped_image": pil_warped,
        })

    return results


def _build_mask(bgr: np.ndarray, gray: np.ndarray) -> np.ndarray:
    """[性質] 純関数 / 3種マスクを OR 合成してノイズ除去済みマスクを返す。"""
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
    mask = cv2.bitwise_or(mask_diff, mask_edge)
    mask = cv2.bitwise_or(mask, mask_sat)
    k_close = cv2.getStructuringElement(cv2.MORPH_RECT, _CLOSE_KERNEL)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close, iterations=2)
    return mask


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


def _warp_card(np_rgb: np.ndarray, src_pts: np.ndarray) -> np.ndarray | None:
    """[性質] 純関数 / 4点透視変換を行い RGB ndarray を返す。サイズ基準未満なら None。

    出力サイズは polygon の実ピクセル辺長から算出する（人工拡大しない）。
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
        return None
    dst_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    return cv2.warpPerspective(np_rgb, M, (w, h), flags=cv2.INTER_LANCZOS4)


def _is_overlapping(x1, y1, w1, h1, kept_list: list) -> bool:
    """[性質] 純関数 / 新候補が保持済み矩形と 50% 以上重複するか判定。"""
    a1 = w1 * h1
    for x2, y2, w2, h2 in kept_list:
        iw = min(x1 + w1, x2 + w2) - max(x1, x2)
        ih = min(y1 + h1, y2 + h2) - max(y1, y2)
        if iw <= 0 or ih <= 0:
            continue
        min_area = min(a1, w2 * h2)
        if min_area > 0 and (iw * ih) / min_area >= _OVERLAP_THRESHOLD:
            return True
    return False
