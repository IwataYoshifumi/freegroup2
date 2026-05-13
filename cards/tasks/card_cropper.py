"""名刺画像の保存。

透視変換済み PIL Image を MEDIA_ROOT 配下に JPEG として保存する。
透視変換は card_detector が担うため、このモジュールは保存のみを担う。
"""

import os
from datetime import datetime

from django.conf import settings
from PIL import Image

JPEG_QUALITY = 95

def save_card_image(
    warped_image: Image.Image,
    original_image_uuid: str,
    card_index: int,
) -> tuple[bool, str | None, str]:
    """透視変換済み PIL Image を JPEG として MEDIA_ROOT 配下に保存する。

    [性質] 副作用あり（ファイル書き込み）
    [入力] warped_image: 透視変換済みの PIL Image
           original_image_uuid: 元画像の UUID（文字列）
           card_index: 名刺のインデックス番号
    [出力] (success, file_path_or_none, error_message)
    [方針] 例外は外に漏らさない。失敗時は (False, None, 理由) を返す
    """
    try:
        img = warped_image if warped_image.mode == "RGB" else warped_image.convert("RGB")
        relative_path, absolute_path = _build_save_path(original_image_uuid, card_index)
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        img.save(absolute_path, format="JPEG", quality=JPEG_QUALITY)
        return True, relative_path, ""
    except OSError as e:
        return False, None, f"ファイル I/O エラー: {e}"
    except Exception as e:
        return False, None, f"保存失敗: {type(e).__name__}: {e}"


def _build_save_path(original_image_uuid: str, card_index: int):
    """[性質] 副作用あり（時刻取得）/ 保存パス（相対 / 絶対）のタプルを返す。"""
    now = datetime.now()
    relative_path = (
        f"cards/{now.strftime('%Y')}/{now.strftime('%m')}/"
        f"{now.strftime('%d')}/{original_image_uuid}-{card_index}.jpg"
    )
    absolute_path = os.path.join(settings.MEDIA_ROOT, relative_path)
    return relative_path, absolute_path
