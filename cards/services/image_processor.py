"""画像入口処理（仕様書 v1.1.0 §8.4.1）。

検証（validate_image）と JPEG 変換（convert_to_jpeg）を入口処理として統合した。
"""

import io
import os

from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png"]
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
JPEG_QUALITY = 90


def validate_image(uploaded_file):
    """アップロードされた画像ファイルを検証する。

    [性質] 副作用あり（ValidationError を raise）
    [入力] uploaded_file: Django の UploadedFile オブジェクト
    [出力] None（検証成功時）
    [例外] django.core.exceptions.ValidationError
    """
    ext = os.path.splitext(uploaded_file.name)[1].lstrip(".").lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "対応していないファイル形式です。JPEGまたはPNG形式の画像をアップロードしてください。"
        )

    if uploaded_file.size > MAX_FILE_SIZE:
        raise ValidationError("ファイルサイズが5MBを超えています。")


def convert_to_jpeg(uploaded_file):
    """画像を JPEG 形式に変換する（PNG 入力時のみ変換、JPEG はそのまま）。

    [性質] 副作用あり（画像変換）
    [入力] uploaded_file: Django の UploadedFile オブジェクト
    [出力] bytes（JPEG 形式の画像バイト列）
    """
    ext = os.path.splitext(uploaded_file.name)[1].lstrip(".").lower()

    if ext in ("jpg", "jpeg"):
        uploaded_file.seek(0)
        return uploaded_file.read()

    uploaded_file.seek(0)
    image = Image.open(uploaded_file)
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()
