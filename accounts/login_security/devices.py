"""端末記録・信頼済み端末・端末識別クッキー管理（仕様書 v1.3.1 §6.4 / §6.6 / §7.2 / §9.3 / §14 #12, #14, #15）。

使い方（ログインフロー §4）：
  1. 順4（第二要素の要否判定）で `match_login_device(user, request=request)` を呼び、
     端末記録を更新する前の状態で「新しい端末か」「信頼済みか」を判定して結果オブジェクトを保持する。
  2. 順8（セッション確立後）で順4の照合結果を `finalize_login_device(user, match_result, ...)` に渡し、
     端末記録の更新または新規作成を行い、返された平文トークンを `set_device_cookie()` で応答に設定する。

注意（共有ブラウザの仕様上の限界）：
  同一ブラウザで複数ユーザーが交互にログインすると端末識別クッキーが上書きされるため、
  別ユーザーに切り替わるたびに「新しい端末」扱いとなる（仕様）。

注意：端末トークンの平文値はログ・操作ログ・例外メッセージに出力しない。ユーザーモデルは保存しない。
"""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import LoginDevice

DEVICE_COOKIE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60
DEVICE_TOKEN_CREATE_MAX_RETRIES = 5
DEFAULT_USER_AGENT_MAX_LENGTH = 512


@dataclass(frozen=True)
class DeviceMatchResult:
    """順4 の端末照合結果（更新前の状態）。"""

    device: LoginDevice | None
    is_new_device: bool
    is_trusted: bool
    plain_token: str | None = None


@dataclass(frozen=True)
class DeviceFinalizeResult:
    """順8 の端末記録確定結果。"""

    device: LoginDevice
    plain_token: str


@dataclass(frozen=True)
class LoginDeviceInfo:
    """端末一覧表示用の情報（端末記録と現在の信頼状態）。"""

    device: LoginDevice
    is_trusted: bool


def hash_device_token(plain_token: str) -> str:
    """平文の端末トークンから SHA-256 の 16 進ダイジェストを計算する（鍵なし）。

    [性質] 純関数（副作用なし）
    [入力] plain_token: str
    [出力] str（64文字の16進ダイジェスト）
    """
    return hashlib.sha256(plain_token.encode("utf-8")).hexdigest()


def generate_device_token() -> str:
    """暗号学的に安全なランダム端末トークンを生成する。

    [性質] 準純関数（CSPRNG 使用、副作用なし）
    [入力] なし
    [出力] str
    """
    return secrets.token_urlsafe(32)


def read_device_token_from_request(request) -> str | None:
    """リクエストから設定 DEVICE_COOKIE_NAME の端末識別クッキーを読む。

    [性質] 純関数（request.COOKIES と settings を参照、副作用なし）
    [入力] request: HttpRequest
    [出力] str | None（未設定または空文字なら None）
    """
    cookie_name = getattr(settings, "DEVICE_COOKIE_NAME", "freegroup2_device_8000")
    raw = request.COOKIES.get(cookie_name)
    if not raw or not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def is_device_trusted(device: LoginDevice | None, *, now=None) -> bool:
    """端末記録の二段階認証省略期限が現在時刻より後かを判定する。

    [性質] 純関数（受け取った device と時刻のみ参照、副作用なし）
    [入力] device: LoginDevice | None, now: datetime | None
    [出力] bool
    """
    if device is None or device.trusted_until is None:
        return False
    current = now or timezone.now()
    return device.trusted_until > current


def match_login_device(
    user,
    *,
    request=None,
    plain_token: str | None = None,
    now=None,
) -> DeviceMatchResult:
    """順4：端末記録を更新する前に、そのユーザーの端末記録と照合する（読み取り専用）。

    [性質] 準関数（DB 読み取りのみ、副作用なし）
    [入力] user: CustomUser
           request: HttpRequest | None
           plain_token: str | None（省略時は request のクッキーから取得）
           now: datetime | None
    [出力] DeviceMatchResult(device, is_new_device, is_trusted, plain_token)

    他ユーザーの端末記録と一致しても、このユーザーでは (None, is_new_device=True, is_trusted=False) となる。
    """
    token = (
        plain_token
        if plain_token is not None
        else (read_device_token_from_request(request) if request is not None else None)
    )
    if not token:
        return DeviceMatchResult(
            device=None,
            is_new_device=True,
            is_trusted=False,
            plain_token=None,
        )

    token_hash = hash_device_token(token)
    device = LoginDevice.objects.filter(user=user, token_hash=token_hash).first()
    if device is None:
        return DeviceMatchResult(
            device=None,
            is_new_device=True,
            is_trusted=False,
            plain_token=None,
        )

    return DeviceMatchResult(
        device=device,
        is_new_device=False,
        is_trusted=is_device_trusted(device, now=now),
        plain_token=token,
    )


def _truncate_user_agent(user_agent: str | None) -> str:
    max_len = (
        LoginDevice._meta.get_field("user_agent").max_length
        or DEFAULT_USER_AGENT_MAX_LENGTH
    )
    return (user_agent or "")[:max_len]


def finalize_login_device(
    user,
    match_result: DeviceMatchResult,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> DeviceFinalizeResult:
    """順8：順4の照合結果を受け取り、端末記録を更新または新規作成する。

    [性質] 副作用あり（DB 書込：LoginDevice 更新または作成。CustomUser は保存しない）
    [入力] user: CustomUser
           match_result: DeviceMatchResult（順4の戻り値。再検索しない）
           ip_address: str | None
           user_agent: str | None
           now: datetime | None
    [出力] DeviceFinalizeResult(device, plain_token)
    """
    current = now or timezone.now()
    truncated_ua = _truncate_user_agent(user_agent)
    ip_value = ip_address or None

    if (
        not match_result.is_new_device
        and match_result.device is not None
        and match_result.plain_token
    ):
        updated = LoginDevice.objects.filter(
            pk=match_result.device.pk, user=user
        ).update(
            last_seen_at=current,
            last_ip=ip_value,
            user_agent=truncated_ua,
        )
        if updated == 1:
            match_result.device.refresh_from_db()
            return DeviceFinalizeResult(
                device=match_result.device,
                plain_token=match_result.plain_token,
            )

    for _ in range(DEVICE_TOKEN_CREATE_MAX_RETRIES):
        new_token = generate_device_token()
        token_hash = hash_device_token(new_token)
        try:
            with transaction.atomic():
                device = LoginDevice.objects.create(
                    user=user,
                    token_hash=token_hash,
                    first_seen_at=current,
                    last_seen_at=current,
                    last_ip=ip_value,
                    user_agent=truncated_ua,
                )
            return DeviceFinalizeResult(device=device, plain_token=new_token)
        except IntegrityError:
            continue

    raise IntegrityError("端末トークンの一意生成に失敗しました。")


def set_device_cookie(response, request, plain_token: str) -> None:
    """応答に端末識別クッキーを設定する（有効 1 年・HttpOnly・SameSite=Lax・https 時のみ Secure）。

    [性質] 副作用あり（response.cookies へ設定）
    [入力] response: HttpResponse, request: HttpRequest, plain_token: str
    [出力] None
    """
    cookie_name = getattr(settings, "DEVICE_COOKIE_NAME", "freegroup2_device_8000")
    response.set_cookie(
        key=cookie_name,
        value=plain_token,
        max_age=DEVICE_COOKIE_MAX_AGE_SECONDS,
        path="/",
        secure=bool(request.is_secure()),
        httponly=True,
        samesite="Lax",
    )


def grant_device_trust(device: LoginDevice, days: int, *, now=None) -> bool:
    """端末に信頼期間を付与する（days <= 0 のときは付与しない）。

    [性質] 副作用あり（DB 書込：LoginDevice.trusted_until 更新）
    [入力] device: LoginDevice, days: int, now: datetime | None
    [出力] bool（付与した場合 True、days <= 0 で何もしなかった場合 False）
    """
    if days <= 0:
        return False
    current = now or timezone.now()
    trusted_until = current + timedelta(days=days)
    LoginDevice.objects.filter(pk=device.pk).update(trusted_until=trusted_until)
    device.trusted_until = trusted_until
    return True


def revoke_device_trust(device: LoginDevice) -> None:
    """1 つの端末記録の信頼を取り消す（省略期限を空にする）。

    [性質] 副作用あり（DB 書込：LoginDevice.trusted_until 更新）
    [入力] device: LoginDevice
    [出力] None
    """
    LoginDevice.objects.filter(pk=device.pk).update(trusted_until=None)
    device.trusted_until = None


def revoke_user_device_trust(user) -> int:
    """指定ユーザーの全端末記録の信頼を一括無効にする。

    [性質] 副作用あり（DB 書込：LoginDevice.trusted_until 一括更新）
    [入力] user: CustomUser
    [出力] int（更新件数）
    """
    return LoginDevice.objects.filter(
        user=user, trusted_until__isnull=False
    ).update(trusted_until=None)


def revoke_all_devices_trust() -> int:
    """全ユーザーの端末記録の信頼を一括無効にする（信頼期間 0 への変更時用）。

    [性質] 副作用あり（DB 書込：LoginDevice.trusted_until 一括更新）
    [入力] なし
    [出力] int（更新件数）
    """
    return LoginDevice.objects.filter(trusted_until__isnull=False).update(
        trusted_until=None
    )


def list_user_devices(user, *, now=None) -> list[LoginDeviceInfo]:
    """ユーザーの端末記録一覧を最終ログイン日時の新しい順で返す。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] user: CustomUser, now: datetime | None
    [出力] list[LoginDeviceInfo(device, is_trusted)]
    """
    current = now or timezone.now()
    devices = LoginDevice.objects.filter(user=user).order_by("-last_seen_at", "-id")
    return [
        LoginDeviceInfo(
            device=device,
            is_trusted=is_device_trusted(device, now=current),
        )
        for device in devices
    ]


def delete_user_device(user, device_id: int) -> bool:
    """指定ユーザーの端末記録を 1 件削除する（他ユーザーの端末は削除しない）。

    [性質] 副作用あり（DB 書込：LoginDevice 削除）
    [入力] user: CustomUser, device_id: int
    [出力] bool（削除できた場合 True）
    """
    deleted_count, _ = LoginDevice.objects.filter(pk=device_id, user=user).delete()
    return deleted_count > 0
