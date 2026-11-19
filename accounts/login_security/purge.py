"""古いログイン関連データの定期削除（仕様書 v1.3.1 §5.6 / §6.2 / §9.3 / §12.2）。"""

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import LoginAttempt, LoginDevice

LOGIN_ATTEMPT_RETENTION_DAYS = 180
LOGIN_DEVICE_RETENTION_DAYS = 365
UNCONFIRMED_TOTP_RETENTION_HOURS = 24


@dataclass(frozen=True)
class PurgeLoginDataResult:
    """古いログイン関連データの削除件数。"""

    deleted_attempts: int
    deleted_devices: int
    deleted_unconfirmed_totp: int


def purge_old_login_data(now=None) -> PurgeLoginDataResult:
    """保存期間を超過したログイン試行記録・端末記録・未確定 TOTP 端末を削除する。

    [性質] 副作用あり（DB 書込：LoginAttempt / LoginDevice / 未確定 TOTPDevice の削除）
    [入力] now: datetime | None（省略時は現在時刻）
    [出力] PurgeLoginDataResult(deleted_attempts, deleted_devices, deleted_unconfirmed_totp)

    削除対象（境界値ちょうどは削除せず、厳密に超過したもの < cutoff を削除）：
      - LoginAttempt: created_at < now - 180日
      - LoginDevice: last_seen_at < now - 365日
      - TOTPDevice: confirmed=False かつ created_at < now - 24時間（確定済みは削除しない）
    """
    current = now or timezone.now()
    attempt_cutoff = current - timedelta(days=LOGIN_ATTEMPT_RETENTION_DAYS)
    device_cutoff = current - timedelta(days=LOGIN_DEVICE_RETENTION_DAYS)
    totp_cutoff = current - timedelta(hours=UNCONFIRMED_TOTP_RETENTION_HOURS)

    deleted_attempts, _ = LoginAttempt.objects.filter(
        created_at__lt=attempt_cutoff
    ).delete()
    deleted_devices, _ = LoginDevice.objects.filter(
        last_seen_at__lt=device_cutoff
    ).delete()
    deleted_unconfirmed_totp, _ = TOTPDevice.objects.filter(
        confirmed=False,
        created_at__lt=totp_cutoff,
    ).delete()

    return PurgeLoginDataResult(
        deleted_attempts=deleted_attempts,
        deleted_devices=deleted_devices,
        deleted_unconfirmed_totp=deleted_unconfirmed_totp,
    )
