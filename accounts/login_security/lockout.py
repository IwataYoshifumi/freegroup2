"""ログイン失敗加算・アカウントロック・IP 遮断（仕様書 v1.3.1 §5.1〜§5.5 / §14 #3, #4, #15, #18）。"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Case, F, IntegerField, Q, Value, When
from django.utils import timezone

from accounts.constants import ActionLogAction
from accounts.models import AccountLock, CustomUser, LoginAttempt, SecuritySetting
from actionlogs.models import ActionLog

from .attempts import record_login_attempt
from .user_lookup import get_security_setting

IP_BLOCK_WINDOW_MINUTES = 15


@dataclass(frozen=True)
class FailureHandlingResult:
    """失敗記録とロック・IP 遮断判定の結果。"""

    attempt: LoginAttempt
    locked_users: list[CustomUser]
    ip_blocked_now: bool


@dataclass(frozen=True)
class PreSecondFactorCheckResult:
    """第二要素検証直前の再判定結果（仕様書 §4 順6）。"""

    allowed: bool
    ip_blocked: bool
    user_locked: bool


def _ensure_account_lock_row(user: CustomUser) -> None:
    """AccountLock 行が存在しなければ作成する（同時作成の競合は get_or_create に任せる）。"""
    AccountLock.objects.get_or_create(user=user, defaults={"failure_count": 0})


def is_user_locked(user: CustomUser, now=None) -> bool:
    """対象ユーザーが現在アカウントロック中か判定する。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] user: CustomUser, now: datetime | None
    [出力] bool（locked_until > now なら True。期限経過で自動解除扱いとなり False）
    """
    if user is None:
        return False
    current = now or timezone.now()
    return AccountLock.objects.filter(user=user, locked_until__gt=current).exists()


def are_any_users_locked(users: list[CustomUser], now=None) -> bool:
    """対象ユーザー群のうち 1 人でもアカウントロック中か判定する。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] users: list[CustomUser], now: datetime | None
    [出力] bool
    """
    if not users:
        return False
    current = now or timezone.now()
    return AccountLock.objects.filter(user__in=users, locked_until__gt=current).exists()


def reset_failure_count(user: CustomUser) -> None:
    """ログイン完全成立時に連続失敗回数を 0 に戻す。

    [性質] 副作用あり（DB 書込：AccountLock 更新）
    [入力] user: CustomUser
    [出力] None

    重要：この関数は「ログインが完全成立した時のみ」呼ぶこと（仕様書 §4 順7 / §14 #3）。
          第一要素（パスワード）の成功だけでは絶対に呼ばないこと。
    """
    AccountLock.objects.filter(user=user).update(
        failure_count=0,
        last_failed_at=None,
        locked_until=None,
    )


def unlock_user(user: CustomUser) -> bool:
    """管理者の手動操作等によりアカウントロックと失敗回数を解除する。

    [性質] 副作用あり（DB 書込：AccountLock 更新）
    [入力] user: CustomUser
    [出力] bool（既存の AccountLock 行を更新できた場合 True）
    """
    updated = AccountLock.objects.filter(user=user).update(
        failure_count=0,
        last_failed_at=None,
        locked_until=None,
    )
    return updated > 0


def record_user_failure_and_check_lock(
    user: CustomUser,
    *,
    setting: SecuritySetting | None = None,
    now=None,
) -> bool:
    """単一ユーザーの失敗回数を DB 側の加算式で更新し、今回ロックに入った瞬間かを返す。

    [性質] 副作用あり（DB 書込：AccountLock 作成・更新。CustomUser は保存しない）
    [入力] user: CustomUser
           setting: SecuritySetting | None
           now: datetime | None
    [出力] bool（今回の更新で新たにロックに入った瞬間なら True）

    注意：ロック中の試行は失敗回数にもロック時間の延長にも数えない。
    注意：呼び出し側は transaction.atomic() ブロックの中から本関数を呼ばないこと。
    """
    current = now or timezone.now()
    cfg = setting or get_security_setting()
    cutoff = current - timedelta(minutes=cfg.lockout_minutes)

    _ensure_account_lock_row(user)

    # 数え直し（前回失敗からロック時間以上経過で 1）と +1 加算を 1 回の更新にまとめる。
    # ロック中の行（locked_until > current）は更新しない。
    AccountLock.objects.filter(user=user).filter(
        Q(locked_until__isnull=True) | Q(locked_until__lte=current)
    ).update(
        failure_count=Case(
            When(last_failed_at__isnull=True, then=Value(1)),
            When(last_failed_at__lte=cutoff, then=Value(1)),
            default=F("failure_count") + 1,
            output_field=IntegerField(),
        ),
        last_failed_at=current,
    )

    # 条件付き更新で「ロックに入った瞬間」を判定する（更新件数 1 のみ True）
    locked_until = current + timedelta(minutes=cfg.lockout_minutes)
    updated = (
        AccountLock.objects.filter(
            user=user,
            failure_count__gte=cfg.lockout_threshold,
        )
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lte=current))
        .update(locked_until=locked_until)
    )
    return updated == 1


def is_ip_blocked(
    ip_address: str | None,
    *,
    setting: SecuritySetting | None = None,
    now=None,
) -> bool:
    """接続元 IP が現在 IP 遮断中か判定する（直近 15 分の失敗件数から都度計算）。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] ip_address: str | None, setting: SecuritySetting | None, now: datetime | None
    [出力] bool（ip_address が空の場合は False）
    """
    if not ip_address:
        return False
    current = now or timezone.now()
    cfg = setting or get_security_setting()
    cutoff = current - timedelta(minutes=IP_BLOCK_WINDOW_MINUTES)
    failure_count = LoginAttempt.objects.filter(
        ip_address=ip_address,
        created_at__gte=cutoff,
        result__in=[
            LoginAttempt.Result.PASSWORD_FAILED,
            LoginAttempt.Result.CODE_FAILED,
        ],
    ).count()
    return failure_count >= cfg.ip_block_threshold


def record_failure_and_evaluate(
    *,
    username_input: str | None,
    target_users: list[CustomUser],
    primary_user: CustomUser | None,
    ip_address: str | None,
    user_agent: str | None,
    result: str,
    second_factor: str = "",
    now=None,
) -> FailureHandlingResult:
    """失敗を記録し、失敗加算・アカウントロック・IP 遮断を判定して操作ログを残す。

    [性質] 副作用あり（DB 書込：LoginAttempt / AccountLock / ActionLog。CustomUser は保存しない）
    [入力] username_input: str | None
           target_users: list[CustomUser]（resolve_login_users の対象全員）
           primary_user: CustomUser | None（試行記録の user 欄に入れる 1 人）
           ip_address: str | None
           user_agent: str | None
           result: LoginAttempt.Result.PASSWORD_FAILED または CODE_FAILED
           second_factor: 第二要素まで進んだ場合の方法
           now: datetime | None
    [出力] FailureHandlingResult(attempt, locked_users, ip_blocked_now)

    注意：通知メールは送信しない（呼び出し側が戻り値を見て DB 確定後に送る）。
    注意：呼び出し側は transaction.atomic() ブロックの中から本関数を呼ばないこと。
    """
    current = now or timezone.now()
    cfg = get_security_setting()

    attempt = record_login_attempt(
        username_input=username_input,
        user=primary_user,
        ip_address=ip_address,
        user_agent=user_agent,
        result=result,
        second_factor=second_factor,
        created_at=current,
    )

    locked_users: list[CustomUser] = []
    for target_user in target_users:
        if record_user_failure_and_check_lock(target_user, setting=cfg, now=current):
            locked_users.append(target_user)
            ActionLog.record(
                user=None,
                action=ActionLogAction.ACCOUNT_LOCK,
                content_object=target_user,
                object_repr=target_user.username,
                data={"ip_address": ip_address or ""},
            )

    ip_blocked_now = False
    if ip_address and is_ip_blocked(ip_address, setting=cfg, now=current):
        cutoff = current - timedelta(minutes=IP_BLOCK_WINDOW_MINUTES)
        already_logged = ActionLog.objects.filter(
            action=ActionLogAction.IP_BLOCK,
            created_at__gte=cutoff,
            data__ip_address=ip_address,
        ).exists()
        if not already_logged:
            ActionLog.record(
                user=None,
                action=ActionLogAction.IP_BLOCK,
                object_repr=ip_address,
                data={"ip_address": ip_address},
            )
            ip_blocked_now = True

    return FailureHandlingResult(
        attempt=attempt,
        locked_users=locked_users,
        ip_blocked_now=ip_blocked_now,
    )


def check_pre_second_factor(
    user: CustomUser,
    ip_address: str | None,
    now=None,
) -> PreSecondFactorCheckResult:
    """第二要素の検証直前に IP 遮断とアカウントロックを再判定する（仕様書 §4 順6 / §14 #18）。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] user: CustomUser, ip_address: str | None, now: datetime | None
    [出力] PreSecondFactorCheckResult(allowed, ip_blocked, user_locked)
    """
    current = now or timezone.now()
    cfg = get_security_setting()
    ip_blocked = is_ip_blocked(ip_address, setting=cfg, now=current)
    user_locked = is_user_locked(user, now=current)
    return PreSecondFactorCheckResult(
        allowed=not ip_blocked and not user_locked,
        ip_blocked=ip_blocked,
        user_locked=user_locked,
    )
