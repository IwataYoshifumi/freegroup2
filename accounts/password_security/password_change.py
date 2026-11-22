"""本人によるパスワード変更サービス（仕様書 v1.0 §5.2〜§5.5 / §11.1 / §13-3, 4, 8, 9, 10, 17）。

注意：
  - 現在のパスワード誤り（空を含む）の記録がロールバックされないよう、
    change_password 関数全体を transaction.atomic() で包まない。
  - CustomUser の保存は update_fields=["password"] に限る。
  - パスワードを操作ログ・エラーログ・ログイン試行記録に出力しない。
"""

from dataclasses import dataclass
from enum import Enum

from django.contrib.auth import update_session_auth_hash
from django.db import transaction
from django.utils import timezone

from accounts.constants import ActionLogAction
from accounts.login_security.client_ip import get_client_ip
from accounts.login_security.failures import record_failure_and_notify
from accounts.login_security.lockout import is_user_locked
from accounts.login_security.session_revoke import revoke_user_sessions
from accounts.models import LoginAttempt
from actionlogs.models import ActionLog

from .eligibility import (
    is_password_self_service_eligible,
    validate_new_password_for_user,
)
from .notifications import notify_password_changed

NOT_ELIGIBLE_MESSAGE = (
    "このアカウントでは、FreeGroup2 でパスワードを変更できません。"
    "管理者にお問い合わせください。二段階認証の設定は、プロフィールからできます。"
)
LOCKED_MESSAGE = "現在この操作は行えません。しばらく待ってから再度お試しください。"
INVALID_CURRENT_PASSWORD_MESSAGE = "現在のパスワードが正しくありません。"


class PasswordChangeStatus(str, Enum):
    """パスワード変更処理の結果ステータス。"""

    SUCCESS = "success"
    NOT_ELIGIBLE = "not_eligible"
    LOCKED = "locked"
    INVALID_CURRENT_PASSWORD = "invalid_current_password"
    VALIDATION_ERROR = "validation_error"
    SAME_AS_CURRENT = "same_as_current"


@dataclass(frozen=True)
class PasswordChangeResult:
    """パスワード変更処理の結果。"""

    status: PasswordChangeStatus
    errors: tuple[str, ...] = ()
    entered_lock: bool = False
    ip_blocked_now: bool = False
    revoked_sessions: int = 0
    keep_session_key: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == PasswordChangeStatus.SUCCESS


def change_password(
    user,
    *,
    current_password: str | None,
    new_password: str | None,
    request=None,
    keep_session_key: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> PasswordChangeResult:
    """本人のパスワードを変更する（検証順 §5.2 順1〜3, 5, 6 → 成功時処理 §5.4 順1〜5）。

    [性質] 副作用あり（DB 書込：失敗時は LoginAttempt/AccountLock/ActionLog ＋ロック通知、
           成功時は CustomUser.password 更新・他セッション切断・ActionLog ＋完了通知）
    [入力] user: CustomUser
           current_password: str | None（空入力も失敗として数える）
           new_password: str | None
           request: HttpRequest | None（認証ハッシュ更新・セッションキー再取得・IP/UA 取得に使用）
           keep_session_key: str | None（request 未使用時用）
           ip_address: str | None
           user_agent: str | None
           now: datetime | None
    [出力] PasswordChangeResult
    """
    current = now or timezone.now()
    resolved_ip = (
        ip_address
        if ip_address is not None
        else (get_client_ip(request) if request is not None else None)
    )
    resolved_ua = (
        user_agent
        if user_agent is not None
        else (request.META.get("HTTP_USER_AGENT", "") if request is not None else "")
    )

    # 順1：対象外判定（§5.5）
    if not is_password_self_service_eligible(user):
        return PasswordChangeResult(
            status=PasswordChangeStatus.NOT_ELIGIBLE,
            errors=(NOT_ELIGIBLE_MESSAGE,),
        )

    # 順2：アカウントロック判定
    if is_user_locked(user, now=current):
        return PasswordChangeResult(
            status=PasswordChangeStatus.LOCKED,
            errors=(LOCKED_MESSAGE,),
        )

    # 順3：現在のパスワード検証（空を含む誤りはトランザクション外で失敗加算）
    if not current_password or not user.check_password(current_password):
        failure_res = record_failure_and_notify(
            username_input=user.username,
            target_users=[user],
            primary_user=user,
            ip_address=resolved_ip,
            user_agent=resolved_ua,
            result=LoginAttempt.Result.PASSWORD_FAILED,
            second_factor="",
            now=current,
        )
        return PasswordChangeResult(
            status=PasswordChangeStatus.INVALID_CURRENT_PASSWORD,
            errors=(INVALID_CURRENT_PASSWORD_MESSAGE,),
            entered_lock=(user in failure_res.locked_users),
            ip_blocked_now=failure_res.ip_blocked_now,
        )

    # 順5・6：新パスワードの強度検証・現在と同じでないか（失敗加算しない）
    valid, err_kind, err_msgs = validate_new_password_for_user(user, new_password)
    if not valid:
        status = (
            PasswordChangeStatus.SAME_AS_CURRENT
            if err_kind == "same_as_current"
            else PasswordChangeStatus.VALIDATION_ERROR
        )
        return PasswordChangeResult(status=status, errors=err_msgs)

    # 成功時の処理（§5.4 順1〜5：端末の信頼は取り消さない。失敗回数は戻さない）
    with transaction.atomic():
        # 順1：パスワードのフィールドのみ保存
        user.set_password(new_password)
        user.save(update_fields=["password"])

        # 順2：認証ハッシュを更新し、更新後のセッションキーを取り直す
        if request is not None and hasattr(request, "session"):
            update_session_auth_hash(request, user)
            effective_keep_session_key = request.session.session_key
        else:
            effective_keep_session_key = keep_session_key

        # 順3：操作中以外のセッションを切断
        revoked_sessions = revoke_user_sessions(
            user,
            keep_session_key=effective_keep_session_key,
            now=current,
        )

        # 順4：操作ログ記録（本人 → 本人）
        ActionLog.record(
            user=user,
            action=ActionLogAction.PASSWORD_CHANGE,
            content_object=user,
            object_repr=user.username,
            data={"ip_address": resolved_ip or ""},
        )

        # 順5：DB 確定後に本人へ通知
        notify_password_changed(
            user,
            ip_address=resolved_ip,
            user_agent=resolved_ua,
            operated_at=current,
        )

    return PasswordChangeResult(
        status=PasswordChangeStatus.SUCCESS,
        revoked_sessions=revoked_sessions,
        keep_session_key=effective_keep_session_key,
    )
