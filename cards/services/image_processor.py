"""画像入口処理（仕様書 v1.1.0 §8.4.1 / v1.2.x 補正 / v1.6.1 §7.2 EXIF 抽出）。

検証（validate_image）と JPEG 変換（convert_to_jpeg）を入口処理として統合する。
v1.2.x で EXIF orientation の物理的補正と Pillow 経由の純粋 JPEG 再保存を行う方針に変更
（仕様書 v1.1.0 §6 では v2.0.0 で対応予定とされていたが、iPhone 撮影画像が
主流のため v1.2.x で先行対応。仕様書改訂は v1.2.3 で別途実施予定）。

v1.6.1 Phase G で `extract_exif_to_json` を追加。EXIF は convert_to_jpeg の
exif_transpose で再エンコードする前のタイミングで取り出す必要がある
（再エンコード後は EXIF が失われる、v1.6.1 統合版 §7.2）。
"""

import base64
import io
import logging
import os

from django.core.exceptions import ValidationError
from PIL import ExifTags, Image, ImageOps

ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png"]
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
JPEG_QUALITY = 90

logger = logging.getLogger(__name__)


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


def extract_exif_to_json(uploaded_file):
    """アップロード受信直後の生バイト列から EXIF を抽出し JSON 化可能な dict として返す。

    [性質] 副作用あり（uploaded_file の seek 位置を変更する）。DB 操作なし。
    [入力] uploaded_file: Django の UploadedFile（または read/seek を持つ file-like）
    [出力] dict | None（EXIF が存在しない、または Pillow で開けない場合は None）

    convert_to_jpeg の exif_transpose で再エンコードされると EXIF が失われるため、
    本関数は convert_to_jpeg を呼ぶ前のタイミングで呼ぶこと（仕様書 v1.6.1 統合版 §7.2）。
    呼び出し後は uploaded_file.seek(0) に戻すので、続けて convert_to_jpeg を呼べる。

    GPSInfo は別 IFD として exif.get_ifd(IFD.GPSInfo) で取り出し、GPSTAGS で名前化した
    dict として "GPSInfo" キーに格納する。BusinessCard.orientation には流し込まない
    （EXIF Orientation と BusinessCard.orientation は別物、仕様書 §7.2 末尾）。
    """
    uploaded_file.seek(0)
    try:
        image = Image.open(uploaded_file)
        exif = image.getexif()
    except Exception:
        logger.warning("extract_exif_to_json: Pillow が画像を開けないため EXIF 抽出をスキップ")
        uploaded_file.seek(0)
        return None
    uploaded_file.seek(0)

    if not exif:
        return None
    return _serialize_exif(exif)


def _serialize_exif(exif):
    """[性質] 純関数。Pillow の Exif オブジェクトを JSON 化可能な dict に変換する。

    タグキー（int）は TAGS の名前で文字列化、値は _coerce_json_value で JSON 化可能な
    形に変換する。GPSInfo は別 IFD なので IFD.GPSInfo で取り出し、GPSTAGS で名前化する。
    """
    result = {}
    for tag_id, value in exif.items():
        tag = ExifTags.TAGS.get(tag_id, str(tag_id))
        result[tag] = _coerce_json_value(value)

    try:
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except (AttributeError, KeyError, ValueError):
        gps_ifd = None
    if gps_ifd:
        gps = {}
        for tag_id, value in gps_ifd.items():
            tag = ExifTags.GPSTAGS.get(tag_id, str(tag_id))
            gps[tag] = _coerce_json_value(value)
        result["GPSInfo"] = gps

    return result


def _coerce_json_value(value):
    """[性質] 純関数。EXIF 値を JSON シリアライズ可能な値に変換する。

    変換ルール：
      - bytes → ASCII デコード（不能文字は \\ufffd、NUL を末尾除去）。decode 自体が
        失敗するケースは base64 文字列にフォールバック
      - IFDRational (Fraction 系) → float。分母 0 のときは None
      - tuple / list → 各要素を再帰変換した list
      - dict → 各値を再帰変換した dict（キーは str 化）
      - int / float / str / bool / None → そのまま
      - その他 → str(value)
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("ascii", errors="replace").rstrip("\x00")
        except Exception:
            return base64.b64encode(value).decode("ascii")
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        if value.denominator == 0:
            return None
        try:
            return float(value)
        except (ZeroDivisionError, ValueError):
            return None
    if isinstance(value, (list, tuple)):
        return [_coerce_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_json_value(v) for k, v in value.items()}
    if isinstance(value, (int, float, str)) or value is None:
        return value
    return str(value)
