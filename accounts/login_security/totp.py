"""認証アプリ（TOTP）の検証・登録開始・確定・QR 生成（仕様書 v1.3.1 §6.1 / §6.2 / §14 #6, #13, #23, #24）。"""

from base64 import b32encode
from dataclasses import dataclass
from enum import Enum
import io
import re
import time
import unicodedata

from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
import segno


class TotpVerifyStatus(str, Enum):
    """TOTP コード検証結果。"""

    VALID = "valid"
    USED = "used"
    INVALID = "invalid"


@dataclass(frozen=True)
class TotpEnrollmentData:
    """TOTP 登録開始時の返却データ（未確定デバイス・設定 URL・手入力用秘密鍵・QR SVG）。"""

    device: TOTPDevice
    config_url: str
    manual_secret: str
    qr_svg: str


def has_confirmed_totp(user) -> bool:
    """ユーザーが確定済みの認証アプリ登録を持つか判定する。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] user: CustomUser
    [出力] bool
    """
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def _is_used_totp_token(device: TOTPDevice, token_int: int) -> bool:
    """再利用防止の条件（last_t）を外して照合し、使用済みコードかを判別する。

    device.last_t および DB の状態は一切変更しない。
    """
    totp = TOTP(
        device.bin_key,
        device.step,
        device.t0,
        device.digits,
        device.drift,
    )
    totp.time = time.time()
    return totp.verify(token_int, device.tolerance)


def _normalize_totp_code(code: str | None) -> str:
    """入力された TOTP コードを NFKC 正規化し、空白とハイフンを除去する。"""
    normalized = unicodedata.normalize("NFKC", code or "")
    return re.sub(r"[\s\-\u2010-\u2015\u2212\uff0d]+", "", normalized)


def _verify_against_device(device: TOTPDevice, token_str: str) -> TotpVerifyStatus:
    """単一デバイスに対して正規化済みコードを検証し、VALID / USED / INVALID を返す。"""
    if (
        not token_str.isascii()
        or not token_str.isdigit()
        or len(token_str) != device.digits
    ):
        return TotpVerifyStatus.INVALID
    if device.verify_token(token_str):
        return TotpVerifyStatus.VALID
    if _is_used_totp_token(device, int(token_str)):
        return TotpVerifyStatus.USED
    return TotpVerifyStatus.INVALID


def verify_totp_code(user, code: str | None) -> TotpVerifyStatus:
    """確定済み TOTP デバイスに対してコードを検証する。

    [性質] 副作用あり（VALID 時は TOTPDevice.last_t / last_used_at 等を更新。USED 時は更新しない）
    [入力] user: CustomUser, code: str | None
    [出力] TotpVerifyStatus（VALID / USED / INVALID）

    注意：django-otp は最後に使った位置を『読んでから保存』で更新する。
          同一コードの同時2リクエストが両方通る余地は理論上残る（既知の制限。最終報告の 9 番に記載する）。
    """
    token_str = _normalize_totp_code(code)
    if not token_str:
        return TotpVerifyStatus.INVALID

    devices = list(TOTPDevice.objects.filter(user=user, confirmed=True).order_by("id"))
    if not devices:
        return TotpVerifyStatus.INVALID

    saw_used = False
    for device in devices:
        status = _verify_against_device(device, token_str)
        if status == TotpVerifyStatus.VALID:
            return TotpVerifyStatus.VALID
        if status == TotpVerifyStatus.USED:
            saw_used = True

    return TotpVerifyStatus.USED if saw_used else TotpVerifyStatus.INVALID


def _build_qr_svg(config_url: str) -> str:
    """segno を使いサーバー内で QR コードの SVG 文字列を生成する（外部アクセス禁止）。"""
    qr = segno.make(config_url)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", xmldecl=False, scale=5)
    return buf.getvalue().decode("utf-8")


def start_totp_enrollment(user) -> TotpEnrollmentData:
    """未確定の TOTP 登録を作成し、設定 URL・手入力用秘密鍵・QR SVG を返す。

    [性質] 副作用あり（DB 書込：未確定の TOTPDevice を作成。確定済みデバイスは削除しない）
    [入力] user: CustomUser
    [出力] TotpEnrollmentData(device, config_url, manual_secret, qr_svg)

    注意：再登録中も確定済みの旧登録は維持し、未確定デバイスを別レコードで作成する（§14 #19）。
    注意：秘密鍵（manual_secret / device.key）はログに出力しないこと。
    """
    TOTPDevice.objects.filter(user=user, confirmed=False).delete()
    device = TOTPDevice.objects.create(
        user=user,
        name="default",
        confirmed=False,
    )
    config_url = device.config_url
    manual_secret = b32encode(device.bin_key).decode("ascii")
    qr_svg = _build_qr_svg(config_url)
    return TotpEnrollmentData(
        device=device,
        config_url=config_url,
        manual_secret=manual_secret,
        qr_svg=qr_svg,
    )


def confirm_totp_enrollment(device: TOTPDevice, code: str | None) -> TotpVerifyStatus:
    """未確定の TOTP デバイスを確認コードで検証し、正しければ確定済みにする。

    [性質] 副作用あり（VALID 時は confirmed=True に更新）
    [入力] device: TOTPDevice（confirmed=False）, code: str | None
    [出力] TotpVerifyStatus（VALID / USED / INVALID）

    注意：旧い確定済み登録・旧リカバリーコード・端末の信頼の無効化は、この関数では行わない。
          フェーズ F の再登録処理が、確定と同じひとまとまりの処理（transaction.atomic）の中で行う
          （仕様書 §6.2・§14 #19）。
    """
    token_str = _normalize_totp_code(code)
    if not token_str:
        return TotpVerifyStatus.INVALID

    status = _verify_against_device(device, token_str)
    if status == TotpVerifyStatus.VALID and not device.confirmed:
        device.confirmed = True
        device.save(update_fields=["confirmed"])
    return status
