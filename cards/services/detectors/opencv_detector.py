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
_SAT_WHITE_RATIO_MAX = 0.50  # mask_sat の白画素率がこれを超えたらフォールバック発動（背景高彩度対策）

# ── カードフィルタパラメータ ──────────────────────────────
_AREA_MIN_RATIO = 0.008
_AREA_MAX_RATIO = 0.45
_ASPECT_MIN = 1.1
_ASPECT_MAX = 4.5
_OVERLAP_THRESHOLD = 0.50

# ── 透視変換パラメータ ────────────────────────────────────
_MIN_WARP_WIDTH = 100
_MIN_WARP_HEIGHT = 50

# ── 反転リトライ後 polygon の外側マージン拡張 ────────────
# 反転後マスクは「名刺の中身（白地）の連続領域」を拾うため、polygon が
# 名刺端の数 px 内側で確定しがち。通常検出と同等の余白を持たせるため
# 反転リトライ経路でのみ polygon を外側に拡張する（通常検出は不変）。
_INVERTED_POLYGON_EXPAND_RATIO = 0.02   # 対角線長に対する拡張比率
_INVERTED_POLYGON_EXPAND_MIN_PX = 10    # 拡張量の下限ピクセル（小さい polygon でも一定余白を確保）


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
        debug_result = _detect_with_debug(image_path)
        attempts = debug_result.get("attempts") or []
        if not attempts:
            return []
        return attempts[-1].get("results", [])
    except Exception as e:
        logger.warning("detect_cards failed for %s: %s", image_path, e)
        return []


def detect_cards_with_debug(image_path: str) -> dict:
    """画像から名刺を検出し、各試行（通常／反転リトライ）の中間データを「全部入り」で返す。

    検出ロジックは detect_cards() と同一。試行回数（attempt_no）軸で対称構造に並べる。
    反転リトライが走らなかった場合は attempts 配列が 1 要素のみ。
    走った場合は 2 要素（attempt_no=1 が通常、attempt_no=2 が反転後）。

    [性質] 純関数（ファイル読み取りのみ・DB 操作なし・副作用なし）
    [入力] image_path: 元画像のファイルパス
    [出力] dict:
      {
        "image_size": {"width": int, "height": int, "area": int},

        # 共通マスク（反転処理の影響を受けない 3 枚）
        "masks": {
            "diff": PIL.Image.Image,  # 輝度差マスク
            "edge": PIL.Image.Image,  # Canny エッジマスク
            "sat":  PIL.Image.Image,  # HSV 彩度マスク
        },
        "mask_white_ratios": {
            "diff": float, "edge": float, "sat": float,  # mask>0 の比率
        },

        # 各試行ごとの中間データ
        "attempts": [
            {
                "attempt_no": 1,            # 1=通常, 2=反転リトライ
                "type": "normal",           # "normal" | "inverted"
                "masks": {
                    "or":     PIL.Image.Image,  # OR 合成マスク
                    "closed": PIL.Image.Image,  # クローズ処理後の最終マスク
                },
                "mask_white_ratios": {"or": float, "closed": float},
                "contours_count":    int,
                "candidates_filter": list[dict],  # 全輪郭フィルタ判定結果（後述）
                "candidates_dedup":  list[dict],  # 重複除去判定結果（後述）
                "warp_failures":     list[dict],  # 透視変換サイズ未満候補（後述）
                "results": [
                    {"polygon": dict, "warped_image": PIL.Image.Image},
                    ...
                ],
            },
            # attempt_no=2 は反転リトライが走った場合のみ
        ],

        # candidates_filter の各要素：
        #   {area, area_ratio, rect_center, rect_size, rect_angle,
        #    aspect_ratio, passed, reject_reason}
        #   reject_reason: "" | "area_too_small" | "area_too_large"
        #                | "zero_size" | "aspect_invalid"
        # candidates_dedup の各要素：
        #   {bbox: [x,y,w,h], area, kept, overlap_with}
        # warp_failures の各要素：
        #   {polygon, computed_width, computed_height, min_required: [100, 50]}

        "sat_fallback": {
            # mask_sat 暴走時のフォールバック判定結果（背景高彩度対策）。
            "triggered":       bool,
            "sat_white_ratio": float,
            "threshold":       float,
        },

        "or_inversion": {
            # 反転リトライ判定結果。
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

    # ② マスク生成（中間マスクも収集）
    masks_np = _build_mask(bgr, gray)

    # ③〜⑦ 通常マスクで候補抽出・重複除去・透視変換（attempt_no=1）
    pipeline_1 = _extract_from_closed_mask(masks_np["mask_closed"], np_rgb, image_area)
    attempt_1 = {
        "attempt_no": 1,
        "type": "normal",
        "masks": {
            "or":     Image.fromarray(masks_np["mask_or"]),
            "closed": Image.fromarray(masks_np["mask_closed"]),
        },
        "mask_white_ratios": {
            "or":     _white_ratio(masks_np["mask_or"]),
            "closed": _white_ratio(masks_np["mask_closed"]),
        },
        "contours_count":    pipeline_1["contours_count"],
        "candidates_filter": pipeline_1["candidates_filter"],
        "candidates_dedup":  pipeline_1["candidates_dedup"],
        "warp_failures":     pipeline_1["warp_failures"],
        "results":           pipeline_1["results"],
    }
    attempts = [attempt_1]

    # ⑧ 反転リトライ判定：通常マスクで passed=0 のとき mask_or を反転して再試行
    passed_before = sum(1 for c in pipeline_1["candidates_filter"] if c["passed"])
    or_inversion = {
        "attempted": False,
        "passed_before": passed_before,
        "passed_after": passed_before,
        "improved": False,
        "polygon_expand": {"ratio": 0.0, "min_px": 0},
    }

    if passed_before == 0:
        mask_or_inv = cv2.bitwise_not(masks_np["mask_or"])
        k_close = cv2.getStructuringElement(cv2.MORPH_RECT, _CLOSE_KERNEL)
        mask_closed_inv = cv2.morphologyEx(
            mask_or_inv, cv2.MORPH_CLOSE, k_close, iterations=2
        )
        pipeline_2 = _extract_from_closed_mask(
            mask_closed_inv, np_rgb, image_area,
            expand_ratio=_INVERTED_POLYGON_EXPAND_RATIO,
            expand_min_px=_INVERTED_POLYGON_EXPAND_MIN_PX,
        )
        passed_after = sum(1 for c in pipeline_2["candidates_filter"] if c["passed"])

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

        attempts.append({
            "attempt_no": 2,
            "type": "inverted",
            "masks": {
                "or":     Image.fromarray(mask_or_inv),
                "closed": Image.fromarray(mask_closed_inv),
            },
            "mask_white_ratios": {
                "or":     _white_ratio(mask_or_inv),
                "closed": _white_ratio(mask_closed_inv),
            },
            "contours_count":    pipeline_2["contours_count"],
            "candidates_filter": pipeline_2["candidates_filter"],
            "candidates_dedup":  pipeline_2["candidates_dedup"],
            "warp_failures":     pipeline_2["warp_failures"],
            "results":           pipeline_2["results"],
        })

    return {
        "image_size": {"width": int(w), "height": int(h), "area": int(image_area)},
        "masks": {
            "diff": Image.fromarray(masks_np["mask_diff"]),
            "edge": Image.fromarray(masks_np["mask_edge"]),
            "sat":  Image.fromarray(masks_np["mask_sat"]),
        },
        "mask_white_ratios": {
            "diff": _white_ratio(masks_np["mask_diff"]),
            "edge": _white_ratio(masks_np["mask_edge"]),
            "sat":  _white_ratio(masks_np["mask_sat"]),
        },
        "attempts": attempts,
        "sat_fallback": masks_np["sat_fallback"],
        "or_inversion": or_inversion,
        "error_message": "",
    }


def _extract_from_closed_mask(
    mask_closed: np.ndarray, np_rgb: np.ndarray, image_area: int,
    expand_ratio: float = 0.0, expand_min_px: int = 0,
) -> dict:
    """[性質] 純関数 / クローズ済みマスクから候補抽出・重複除去・透視変換を実行する。

    通常マスク用の経路と反転リトライ経路で同じ処理を共有させるための内部ヘルパー。
    フィルタ／重複除去／透視変換のロジック・パラメータは共通。

    [入力]
      mask_closed   : クローズ済み 2 値マスク (uint8 ndarray)
      np_rgb        : 元画像 (RGB ndarray) — 透視変換のソース
      image_area    : 画像面積 (w * h)
      expand_ratio  : 0 より大なら透視変換直前に polygon を中心から外側へ拡張する。
                      拡張量 = max(対角線長 × expand_ratio, expand_min_px) ピクセル。
                      通常検出経路は 0.0（拡張なし）、反転リトライ経路は 0.02 を渡す。
      expand_min_px : 拡張量の下限ピクセル（expand_ratio>0 のときのみ参照）。
    [出力] dict:
      {
        "contours_count":    int,
        "candidates_filter": list[dict],
        "candidates_dedup":  list[dict],
        "warp_failures":     list[dict],
        "results":           list[dict],   # {"polygon", "warped_image"}
      }
    """
    # ③ 輪郭検出
    contours, _ = cv2.findContours(
        mask_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

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
    # expand_ratio>0（反転リトライ経路）のときは polygon を外側に拡張してから warp
    h_img, w_img = np_rgb.shape[:2]
    results: list[dict] = []
    warp_failures: list[dict] = []
    for box, _bx, _by in kept:
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

    return {
        "contours_count": len(contours),
        "candidates_filter": candidates_filter,
        "candidates_dedup": candidates_dedup,
        "warp_failures": warp_failures,
        "results": results,
    }


def _build_mask(bgr: np.ndarray, gray: np.ndarray) -> dict:
    """[性質] 純関数 / 3種マスクを OR 合成し、各中間マスクと最終マスクを dict で返す。

    mask_sat の白画素率が _SAT_WHITE_RATIO_MAX を超えた場合、mask_sat を OR 合成から
    除外し mask_diff + mask_edge の 2 マスクで mask_or を構築する（背景高彩度対策）。

    返却 dict のキー：
      mask_diff   : 輝度差マスク（uint8 single-channel ndarray）
      mask_edge   : Canny エッジマスク
      mask_sat    : HSV 彩度マスク
      mask_or     : OR 合成マスク（フォールバック発動時は mask_sat を除外した 2 マスクの OR）
      mask_closed : クローズ処理後の最終マスク（呼び出し側はこれを使う）
      sat_fallback: dict {triggered: bool, sat_white_ratio: float, threshold: float}
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

    # mask_sat 暴走時のフォールバック判定（背景高彩度対策）
    sat_white_ratio = (
        float(np.count_nonzero(mask_sat)) / float(mask_sat.size)
        if mask_sat.size else 0.0
    )
    sat_fallback_triggered = sat_white_ratio > _SAT_WHITE_RATIO_MAX

    # OR 合成 → クローズで内部穴埋め・ノイズ除去
    mask_or = cv2.bitwise_or(mask_diff, mask_edge)
    if not sat_fallback_triggered:
        mask_or = cv2.bitwise_or(mask_or, mask_sat)
    k_close = cv2.getStructuringElement(cv2.MORPH_RECT, _CLOSE_KERNEL)
    mask_closed = cv2.morphologyEx(mask_or, cv2.MORPH_CLOSE, k_close, iterations=2)

    return {
        "mask_diff":   mask_diff,
        "mask_edge":   mask_edge,
        "mask_sat":    mask_sat,
        "mask_or":     mask_or,
        "mask_closed": mask_closed,
        "sat_fallback": {
            "triggered": sat_fallback_triggered,
            "sat_white_ratio": sat_white_ratio,
            "threshold": _SAT_WHITE_RATIO_MAX,
        },
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
