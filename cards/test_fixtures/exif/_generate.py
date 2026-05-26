"""Phase G テスト用 EXIF fixtures 画像を生成する（再生成用、テストランナーからは呼ばれない）。

リポジトリに 4 枚の JPEG（no_exif / with_exif / with_gps / with_odd_bytes）を再現する。
名刺管理アプリの実機テストではなく、cards/services/image_processor.extract_exif_to_json の
単体テスト fixture として使う。

実行方法：
    python cards/test_fixtures/exif/_generate.py
"""

from pathlib import Path

from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

HERE = Path(__file__).parent


def _make_blank():
    return Image.new("RGB", (16, 16), color="white")


def _save_no_exif():
    img = _make_blank()
    img.save(HERE / "no_exif.jpg", format="JPEG", quality=85)


def _save_with_exif():
    img = _make_blank()
    exif = img.getexif()
    exif[ExifTags.Base.Make] = "TestCameraCo"
    exif[ExifTags.Base.Model] = "TestPhone X"
    exif[ExifTags.Base.DateTimeOriginal] = "2026:05:25 10:30:00"
    exif[ExifTags.Base.Orientation] = 1
    img.save(HERE / "with_exif.jpg", format="JPEG", quality=85, exif=exif)


def _save_with_gps():
    img = _make_blank()
    exif = img.getexif()
    exif[ExifTags.Base.Make] = "TestCameraCo"
    exif[ExifTags.Base.Model] = "GPS Test Model"
    exif[ExifTags.Base.DateTimeOriginal] = "2026:05:25 11:00:00"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (IFDRational(35), IFDRational(40), IFDRational(0))
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (IFDRational(139), IFDRational(45), IFDRational(0))
    img.save(HERE / "with_gps.jpg", format="JPEG", quality=85, exif=exif)


def _save_with_odd_bytes():
    img = _make_blank()
    exif = img.getexif()
    exif[ExifTags.Base.Make] = "OddBytes Inc"
    # ExposureTime は IFDRational、JSON シリアライズ不能
    exif[ExifTags.Base.ExposureTime] = IFDRational(1, 250)
    # UserComment は bytes として書ける（NUL / 非 ASCII バイトを含む raw データを再現）
    exif[ExifTags.Base.UserComment] = b"\x00\xff\xfe\x01raw bytes"
    img.save(HERE / "with_odd_bytes.jpg", format="JPEG", quality=85, exif=exif)


def main():
    _save_no_exif()
    _save_with_exif()
    _save_with_gps()
    _save_with_odd_bytes()
    print("Generated fixtures:", sorted(p.name for p in HERE.glob("*.jpg")))


if __name__ == "__main__":
    main()
