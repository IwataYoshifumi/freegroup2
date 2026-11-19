"""失敗の記録・ロック／IP 遮断判定・通知の結線（仕様書 v1.3.1 §5.1〜§5.6）。"""

from accounts.models import AccountLock

from .lockout import FailureHandlingResult, record_failure_and_evaluate
from .notifications import notify_account_lock, notify_ip_block


def record_failure_and_notify(
    *,
    username_input: str | None,
    target_users: list,
    primary_user,
    ip_address: str | None,
    user_agent: str | None,
    result: str,
    second_factor: str = "",
    now=None,
) -> FailureHandlingResult:
    """失敗を記録・加算・ロック／IP 遮断判定し、必要な通知メールを予約する。

    [性質] 副作用あり（DB 書込：LoginAttempt / AccountLock / ActionLog ＋ on_commit で通知メール予約）
    [入力] username_input: str | None
           target_users: list[CustomUser]
           primary_user: CustomUser | None
           ip_address: str | None
           user_agent: str | None
           result: LoginAttempt.Result.PASSWORD_FAILED または CODE_FAILED
           second_factor: 第二要素まで進んだ場合の方法
           now: datetime | None
    [出力] FailureHandlingResult(attempt, locked_users, ip_blocked_now)

    注意：パスワード失敗・コード失敗を記録する唯一の入口。
    注意：呼び出し側は transaction.atomic() ブロックの中から本関数を呼ばないこと
          （後続処理の例外で失敗・拒否の記録がロールバックされないようにするため）。
    """
    res = record_failure_and_evaluate(
        username_input=username_input,
        target_users=target_users,
        primary_user=primary_user,
        ip_address=ip_address,
        user_agent=user_agent,
        result=result,
        second_factor=second_factor,
        now=now,
    )

    for locked_user in res.locked_users:
        locked_until = (
            AccountLock.objects.filter(user=locked_user)
            .values_list("locked_until", flat=True)
            .first()
        )
        notify_account_lock(
            locked_user,
            ip_address=ip_address,
            locked_until=locked_until,
        )

    if res.ip_blocked_now and ip_address:
        notify_ip_block(ip_address=ip_address)

    return res
