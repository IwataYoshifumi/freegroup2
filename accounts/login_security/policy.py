"""二段階認証の必須対象判定・第二要素要否判定（仕様書 v1.3.1 §6.4 / §6.5）。"""

from enum import Enum

from accounts.models import SecuritySetting
from accounts.services import is_admin_user

from .totp import has_confirmed_totp
from .user_lookup import get_security_setting


class SecondFactorRequirement(str, Enum):
    """ログイン時の第二要素要否の判定結果（仕様書 §4 順4 / §6.4）。"""

    NOT_ENROLLED = "not_enrolled"
    TRUSTED_DEVICE = "trusted_device"
    REQUIRED = "required"


def is_two_factor_required(user, setting: SecuritySetting | None = None) -> bool:
    """強制レベルに基づき、対象ユーザーが二段階認証の必須対象かを判定する。

    [性質] 準関数（DB 読み取り：setting 省略時、副作用なし）
    [入力] user: CustomUser | AnonymousUser | None
           setting: SecuritySetting | None
    [出力] bool
    """
    if not user or not getattr(user, "is_active", False):
        return False

    cfg = setting or get_security_setting()
    enforcement = cfg.two_factor_enforcement

    if enforcement == SecuritySetting.TwoFactorEnforcement.ALL:
        return True
    if enforcement == SecuritySetting.TwoFactorEnforcement.ADMIN_ONLY:
        return is_admin_user(user) or bool(getattr(user, "is_staff", False))
    return False


def evaluate_second_factor_requirement(
    user,
    *,
    is_trusted_device: bool,
) -> SecondFactorRequirement:
    """ログイン時に第二要素が必要かを判定する（仕様書 §4 順4 / §6.4）。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] user: CustomUser, is_trusted_device: bool
    [出力] SecondFactorRequirement（NOT_ENROLLED / TRUSTED_DEVICE / REQUIRED）
    """
    if not has_confirmed_totp(user):
        return SecondFactorRequirement.NOT_ENROLLED
    if is_trusted_device:
        return SecondFactorRequirement.TRUSTED_DEVICE
    return SecondFactorRequirement.REQUIRED
