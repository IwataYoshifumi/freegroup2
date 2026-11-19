"""ログイン試行記録の書き込み（仕様書 v1.3.1 §5.6）。"""

from django.utils import timezone

from accounts.models import LoginAttempt


def record_login_attempt(
    *,
    username_input: str | None,
    user=None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    result: str,
    second_factor: str = "",
    is_new_device: bool = False,
    created_at=None,
) -> LoginAttempt:
    """ログイン試行記録を 1 件作成する。

    [性質] 副作用あり（DB 書込：LoginAttempt 作成）
    [入力] username_input: str | None（先頭 150 文字に切り捨てて保存）
           user: CustomUser | None
           ip_address: str | None
           user_agent: str | None
           result: LoginAttempt.Result の値
           second_factor: LoginAttempt.SecondFactor の値または ""
           is_new_device: bool
           created_at: datetime | None（省略時は現在時刻）
    [出力] LoginAttempt（作成済みインスタンス）

    注意：パスワードは受け取らない。
    注意：第二要素まで進んでいない結果（password_failed / rejected_locked /
          rejected_ip_blocked）では second_factor を空文字にする。
    注意：呼び出し側は transaction.atomic() ブロックの中から本関数を呼ばないこと
          （後続処理の例外で失敗・拒否の記録がロールバックされないようにするため）。
    """
    normalized_input = (username_input or "")[:150]
    if result in (
        LoginAttempt.Result.PASSWORD_FAILED,
        LoginAttempt.Result.REJECTED_LOCKED,
        LoginAttempt.Result.REJECTED_IP_BLOCKED,
    ):
        effective_second_factor = ""
    else:
        effective_second_factor = second_factor or ""

    return LoginAttempt.objects.create(
        created_at=created_at or timezone.now(),
        username_input=normalized_input,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent or "",
        result=result,
        second_factor=effective_second_factor,
        is_new_device=bool(is_new_device),
    )
