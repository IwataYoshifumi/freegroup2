"""第二要素コードの共通検証（ログイン時と重要操作で共用、仕様書 v1.3.1 §4 順6 / §6.1 / §6.11 / §14 #3, #18, #22, #24）。"""

from dataclasses import dataclass
from enum import Enum

from django.utils import timezone

from accounts.models import LoginAttempt

from .attempts import record_login_attempt
from .failures import record_failure_and_notify
from .lockout import check_pre_second_factor
from .notifications import notify_recovery_code_used
from .recovery_codes import (
    get_remaining_recovery_code_count,
    verify_and_consume_recovery_code,
)
from .totp import TotpVerifyStatus, verify_totp_code


class SecondFactorMethod(str, Enum):
    """第二要素検証で受け付ける方法。"""

    TOTP = LoginAttempt.SecondFactor.TOTP
    RECOVERY_CODE = LoginAttempt.SecondFactor.RECOVERY_CODE


class SecondFactorVerifyStatus(str, Enum):
    """第二要素検証の共通結果ステータス。"""

    SUCCESS = "success"
    USED = "used"
    INVALID = "invalid"
    REJECTED_LOCKED = "rejected_locked"
    REJECTED_IP_BLOCKED = "rejected_ip_blocked"


@dataclass(frozen=True)
class SecondFactorVerifyResult:
    """第二要素検証の戻り値。"""

    status: SecondFactorVerifyStatus
    method: str
    entered_lock: bool = False
    ip_blocked_now: bool = False

    @property
    def is_success(self) -> bool:
        return self.status == SecondFactorVerifyStatus.SUCCESS

    @property
    def is_rejected(self) -> bool:
        return self.status in (
            SecondFactorVerifyStatus.REJECTED_LOCKED,
            SecondFactorVerifyStatus.REJECTED_IP_BLOCKED,
        )


def verify_second_factor(
    user,
    code: str | None,
    *,
    method: SecondFactorMethod | str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> SecondFactorVerifyResult:
    """第二要素（認証アプリ／リカバリーコード）のコードを検証し、失敗・拒否を記録する。

    [性質] 副作用あり（DB 書込：拒否時・失敗時の LoginAttempt、失敗時の AccountLock / ActionLog、
            リカバリーコード成功時の使用日時更新と使用通知予約。CustomUser は保存しない）
    [入力] user: CustomUser
           code: str | None
           method: SecondFactorMethod | str（"totp" または "recovery_code"）
           ip_address: str | None
           user_agent: str | None
           now: datetime | None
    [出力] SecondFactorVerifyResult(status, method, entered_lock, ip_blocked_now)

    注意：
      - 成功しても連続失敗回数は 0 に戻さない（0 に戻すのはログイン完全成立時のみ、§14 #3）。
      - 成功のログイン試行記録はここでは残さない（ログインサービスが順9で残し、重要操作では不要）。
      - 失敗記録がロールバックされないよう、呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    method_value = method.value if isinstance(method, SecondFactorMethod) else str(method)
    if method_value not in (
        SecondFactorMethod.TOTP.value,
        SecondFactorMethod.RECOVERY_CODE.value,
    ):
        raise ValueError(f"不正な第二要素の方法です: {method!r}")

    # (1) 検証直前の IP 遮断・アカウントロック再判定（§4 順6 / §14 #18）
    pre = check_pre_second_factor(user, ip_address, now=current)
    if not pre.allowed:
        if pre.ip_blocked:
            record_login_attempt(
                username_input=user.username,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                result=LoginAttempt.Result.REJECTED_IP_BLOCKED,
                second_factor="",
                created_at=current,
            )
            return SecondFactorVerifyResult(
                status=SecondFactorVerifyStatus.REJECTED_IP_BLOCKED,
                method=method_value,
            )
        record_login_attempt(
            username_input=user.username,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            result=LoginAttempt.Result.REJECTED_LOCKED,
            second_factor="",
            created_at=current,
        )
        return SecondFactorVerifyResult(
            status=SecondFactorVerifyStatus.REJECTED_LOCKED,
            method=method_value,
        )

    # (2) 認証アプリ（TOTP）
    if method_value == SecondFactorMethod.TOTP.value:
        totp_status = verify_totp_code(user, code)
        if totp_status == TotpVerifyStatus.VALID:
            return SecondFactorVerifyResult(
                status=SecondFactorVerifyStatus.SUCCESS,
                method=method_value,
            )
        if totp_status == TotpVerifyStatus.USED:
            return SecondFactorVerifyResult(
                status=SecondFactorVerifyStatus.USED,
                method=method_value,
            )

    # (3) リカバリーコード
    elif method_value == SecondFactorMethod.RECOVERY_CODE.value:
        if verify_and_consume_recovery_code(user, code):
            remaining = get_remaining_recovery_code_count(user)
            notify_recovery_code_used(
                user,
                remaining_count=remaining,
                operated_at=current,
            )
            return SecondFactorVerifyResult(
                status=SecondFactorVerifyStatus.SUCCESS,
                method=method_value,
            )

    # (4) 失敗の記録と加算・通知
    failure_res = record_failure_and_notify(
        username_input=user.username,
        target_users=[user],
        primary_user=user,
        ip_address=ip_address,
        user_agent=user_agent,
        result=LoginAttempt.Result.CODE_FAILED,
        second_factor=method_value,
        now=current,
    )
    return SecondFactorVerifyResult(
        status=SecondFactorVerifyStatus.INVALID,
        method=method_value,
        entered_lock=(user in failure_res.locked_users),
        ip_blocked_now=failure_res.ip_blocked_now,
    )
