"""画像入口処理（仕様書 v1.1.0 §8.4.1 / v1.2.x 補正）。

検証（validate_image）と JPEG 変換（convert_to_jpeg）を入口処理として統合する。
v1.2.x で EXIF orientation の物理的補正と Pillow 経由の純粋 JPEG 再保存を行う方針に変更
（仕様書 v1.1.0 §6 では v2.0.0 で対応予定とされていたが、iPhone 撮影画像が
主流のため v1.2.x で先行対応。仕様書改訂は v1.2.3 で別途実施予定）。
"""

import io
import os

from django.core.exceptions import ValidationError
from PIL import Image, ImageOps

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
    """画像を純粋な JPEG 形式（EXIF orientation 適用済み）に変換する。

    [性質] 副作用あり（画像変換）
    [入力] uploaded_file: Django の UploadedFile オブジェクト
    [出力] bytes（JPEG 形式の画像バイト列）

    すべての入力を Pillow で再デコードして純粋 JPEG として再保存する。これにより：
      - MPO（iPhone HDR JPEG）形式 → 純粋 JPEG
      - EXIF orientation タグ → 物理的なピクセル回転（ImageOps.exif_transpose）
      - PNG → JPEG
    Claude API は画像のピクセルデータをそのまま処理し EXIF タグは見ないため、
    回転を物理的に適用しないと OCR で「横倒しの画像」を読まされて誤読が発生する。
    """
    uploaded_file.seek(0)
    image = Image.open(uploaded_file)
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()
