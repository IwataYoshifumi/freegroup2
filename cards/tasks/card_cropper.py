"""名刺画像の切り抜き処理（仕様書 v1.2.1 §8.5.3 / v1.2.2 §8.5.3）。

Claude API が返した bbox を元に、元画像から名刺領域を切り抜いて MEDIA_ROOT 配下に
JPEG として保存する。例外は外に漏らさず、(success, file_path, error_message) を返す。
"""

import os
import uuid
from datetime import datetime

from django.conf import settings
from PIL import Image, UnidentifiedImageError

MARGIN_RATIO = 0.05
LONG_EDGE_MAX = 1200
JPEG_QUALITY = 90
MIN_WIDTH = 100
MIN_HEIGHT = 50


def create_card_image(original_image_path, bbox):
    """元画像から bbox の領域を切り抜き、JPEG として保存する。

    [性質] 副作用あり（ファイル書き込み）
    [入力] original_image_path: 元画像の絶対パス（str）
           bbox: dict {x, y, width, height}（数値、ピクセル単位）
    [出力] tuple (success: bool, file_path: str | None, error_message: str)
           - success=True 時: file_path は MEDIA_ROOT からの相対パス（POSIX 区切り）
           - success=False 時: file_path=None、error_message に理由
    [方針] 例外を外に漏らさない。失敗は (False, None, 理由) で表現する。
    """
    try:
        bbox_or_error = _parse_bbox(bbox)
        if isinstance(bbox_or_error, str):
            return False, None, bbox_or_error
        x, y, w, h = bbox_or_error

        with Image.open(original_image_path) as src:
            src.load()
            img_w, img_h = src.size
            crop_box = _calc_crop_box(x, y, w, h, img_w, img_h)
            if crop_box is None:
                return False, None, f"bbox 範囲外: x={x}, y={y}, width={w}, height={h}"

            cropped = src.crop(crop_box)
            cw, ch = cropped.size
            if cw < MIN_WIDTH or ch < MIN_HEIGHT:
                return False, None, "画像サイズが最低基準未満"

            cropped = _resize_long_edge(cropped, LONG_EDGE_MAX)
            if cropped.mode != "RGB":
                cropped = cropped.convert("RGB")

            relative_path, absolute_path = _build_save_path()
            os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
            cropped.save(absolute_path, format="JPEG", quality=JPEG_QUALITY)
            return True, relative_path, ""

    except FileNotFoundError as e:
        return False, None, f"元画像ファイルが見つかりません: {e}"
    except UnidentifiedImageError as e:
        return False, None, f"画像形式が不正です: {e}"
    except OSError as e:
        return False, None, f"ファイル I/O エラー: {e}"
    except Exception as e:
        return False, None, f"切り抜き失敗: {type(e).__name__}: {e}"


def _parse_bbox(bbox):
    """[性質] 純関数 / bbox を (x, y, w, h) に変換。失敗時はエラー文字列を返す。"""
    if not isinstance(bbox, dict):
        return f"bbox is not a dict (got {type(bbox).__name__})"
    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        w = float(bbox["width"])
        h = float(bbox["height"])
    except (KeyError, TypeError, ValueError) as e:
        return f"bbox parse failed: {e}"
    if w <= 0 or h <= 0:
        return f"bbox width/height が非正: width={w}, height={h}"
    return x, y, w, h


def _calc_crop_box(x, y, w, h, img_w, img_h):
    """[性質] 純関数 / 5% マージン付与・画像端クリップして (left, top, right, bottom) を返す。

    マージン適用後に画像範囲外で完全にはみ出す場合は None を返す。
    """
    margin_x = w * MARGIN_RATIO
    margin_y = h * MARGIN_RATIO
    left = max(0, int(x - margin_x))
    top = max(0, int(y - margin_y))
    right = min(img_w, int(x + w + margin_x))
    bottom = min(img_h, int(y + h + margin_y))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _resize_long_edge(image, long_edge_max):
    """[性質] 純関数 / 長辺が long_edge_max を超えていたら縮小した PIL Image を返す。"""
    w, h = image.size
    if w <= long_edge_max and h <= long_edge_max:
        return image
    if w >= h:
        new_w = long_edge_max
        new_h = max(1, int(h * long_edge_max / w))
    else:
        new_h = long_edge_max
        new_w = max(1, int(w * long_edge_max / h))
    return image.resize((new_w, new_h), Image.LANCZOS)


def _build_save_path():
    """[性質] 副作用あり（時刻取得）/ 保存パス（相対 / 絶対）のタプルを返す。"""
    now = datetime.now()
    relative_path = (
        f"cards/{now.strftime('%Y')}/{now.strftime('%m')}/"
        f"{now.strftime('%d')}/{uuid.uuid4()}.jpg"
    )
    absolute_path = os.path.join(settings.MEDIA_ROOT, relative_path)
    return relative_path, absolute_path
