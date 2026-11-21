"""ログインフロー統合サービス（仕様書 v1.3.1 §4 順1〜11 / §14 #1, #2, #3, #7, #8, #10, #15, #18）。

本モジュールは認証（authenticate）とセッション確立（login）を呼ぶ唯一の入口である（§14 #8）。
注意：失敗記録がロールバックされないよう、呼び出し側が transaction.atomic() の中から呼ばないこと。
注意：CustomUser モデルを直接保存しない（Django 標準の login による last_login 更新を除く）。
"""

from dataclasses import dataclass
from enum import Enum

from django.conf import settings
from django.contrib.auth import authenticate, login as auth_login
from django.shortcuts import resolve_url
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import CustomUser, LoginAttempt

from .attempts import record_login_attempt
from .client_ip import get_client_ip
from .devices import (
    DeviceMatchResult,
    finalize_login_device,
    grant_device_trust,
    match_login_device,
    set_device_cookie,
)
from .failures import record_failure_and_notify
from .lockout import are_any_users_locked, is_ip_blocked, reset_failure_count
from .notifications import notify_login_success
from .policy import (
    SecondFactorRequirement,
    evaluate_second_factor_requirement,
    is_two_factor_required,
)
from .second_factor import (
    SecondFactorMethod,
    SecondFactorVerifyStatus,
    verify_second_factor,
)
from .totp import has_confirmed_totp
from .user_lookup import get_security_setting, resolve_login_users

PENDING_AUTH_SESSION_KEY = "_two_factor_pending_auth"
PENDING_AUTH_TTL_SECONDS = 300

COMMON_LOGIN_ERROR_MESSAGE = (
    "ログインできません。入力内容を確認するか、しばらく待ってから再度お試しください。"
)
USED_CODE_ERROR_MESSAGE = (
    "このコードは使用済みです。認証アプリに次のコードが表示されるまでお待ちください。"
)
INVALID_CODE_ERROR_MESSAGE = "コードが正しくありません。"
EXPIRED_PENDING_AUTH_MESSAGE = (
    "ログインの有効時間が切れました。もう一度ログインしてください。"
)


class LoginStepStatus(str, Enum):
    """ログインフロー各ステップの結果ステータス。"""

    COMPLETED = "completed"
    SECOND_FACTOR_REQUIRED = "second_factor_required"
    REJECTED = "rejected"
    INVALID_CREDENTIALS = "invalid_credentials"
    EXPIRED = "expired"
    CODE_USED = "code_used"
    CODE_INVALID = "code_invalid"


@dataclass(frozen=True)
class PendingAuthState:
    """仮認証（保留状態）から復元した情報。"""

    user: CustomUser
    backend: str
    started_at: float
    next_url: str


@dataclass(frozen=True)
class PendingAuthLoadResult:
    """仮認証の読み出し結果（不在と期限切れを区別する）。"""

    state: PendingAuthState | None
    expired: bool = False


@dataclass(frozen=True)
class LoginFlowResult:
    """第一要素・第二要素ログイン処理の共通結果オブジェクト。"""

    status: LoginStepStatus
    redirect_url: str = ""
    device_token: str | None = None
    second_factor: str = ""
    is_new_device: bool = False
    requires_two_factor_setup: bool = False
    error_message: str = ""

    @property
    def is_completed(self) -> bool:
        return self.status == LoginStepStatus.COMPLETED


def get_safe_redirect_url(request, next_url: str | None) -> str:
    """遷移先 URL が自サイト内であることを検証し、不正なら既定遷移先を返す。

    [性質] 純関数（request と settings を参照、副作用なし）
    [入力] request: HttpRequest, next_url: str | None
    [出力] str（検証済みの安全な遷移先 URL）
    """
    fallback = resolve_url(settings.LOGIN_REDIRECT_URL)
    candidate = (next_url or "").strip()
    if not candidate:
        return fallback
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def clear_pending_auth(request) -> None:
    """セッションから仮認証（保留状態）を破棄する。

    [性質] 副作用あり（request.session 更新）
    [入力] request: HttpRequest
    [出力] None
    """
    request.session.pop(PENDING_AUTH_SESSION_KEY, None)


def load_pending_auth(request, *, now=None) -> PendingAuthLoadResult:
    """セッションから仮認証を読み出し、有効期限（5分）と対象ユーザーの有効性を検証する。

    [性質] 準関数（DB からユーザーを再取得。失効・不整合時は仮認証を破棄）
    [入力] request: HttpRequest, now: datetime | None
    [出力] PendingAuthLoadResult(state, expired)
    """
    raw = request.session.get(PENDING_AUTH_SESSION_KEY)
    if not isinstance(raw, dict):
        return PendingAuthLoadResult(state=None, expired=False)

    expected_keys = {"user_id", "backend", "started_at", "next_url"}
    if set(raw.keys()) != expected_keys:
        clear_pending_auth(request)
        return PendingAuthLoadResult(state=None, expired=False)

    user_id = raw.get("user_id")
    backend = raw.get("backend")
    started_at = raw.get("started_at")
    next_url = raw.get("next_url")

    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or not isinstance(backend, str)
        or not backend
        or not isinstance(started_at, (int, float))
        or isinstance(started_at, bool)
        or not isinstance(next_url, str)
    ):
        clear_pending_auth(request)
        return PendingAuthLoadResult(state=None, expired=False)

    current = now or timezone.now()
    elapsed = current.timestamp() - float(started_at)
    if elapsed < 0 or elapsed > PENDING_AUTH_TTL_SECONDS:
        clear_pending_auth(request)
        return PendingAuthLoadResult(state=None, expired=True)

    user = (
        CustomUser.objects.filter(pk=user_id)
        .select_related("role")
        .first()
    )
    if user is None or not user.is_active:
        clear_pending_auth(request)
        return PendingAuthLoadResult(state=None, expired=False)

    return PendingAuthLoadResult(
        state=PendingAuthState(
            user=user,
            backend=backend,
            started_at=float(started_at),
            next_url=next_url,
        ),
        expired=False,
    )


def _store_pending_auth(
    request,
    *,
    user: CustomUser,
    backend_path: str,
    safe_next: str,
    now,
) -> None:
    """セッションキーを作り直し、仮認証の 4 項目だけをセッションへ保存する（§14 #2）。"""
    request.session.cycle_key()
    request.session[PENDING_AUTH_SESSION_KEY] = {
        "user_id": user.pk,
        "backend": backend_path,
        "started_at": now.timestamp(),
        "next_url": safe_next,
    }


def apply_login_cookie(response, request, result: LoginFlowResult) -> None:
    """ログイン完了結果に含まれる端末識別トークンを応答クッキーへ設定する。

    [性質] 副作用あり（response.cookies へ設定）
    [入力] response: HttpResponse, request: HttpRequest, result: LoginFlowResult
    [出力] None
    """
    if result.is_completed and result.device_token:
        set_device_cookie(response, request, result.device_token)


def _complete_login(
    request,
    *,
    user: CustomUser,
    backend_path: str,
    second_factor: str,
    safe_next: str,
    trust_device: bool,
    match_result: DeviceMatchResult | None,
    ip_address: str | None,
    user_agent: str,
    now,
) -> LoginFlowResult:
    """順7〜11：セッション確立・失敗回数リセット・端末確定・信頼付与・試行記録・通知予約。"""
    clear_pending_auth(request)
    auth_login(request, user, backend=backend_path)
    reset_failure_count(user)

    cfg = get_security_setting()
    resolved_match = (
        match_result
        if match_result is not None
        else match_login_device(user, request=request, now=now)
    )
    fin = finalize_login_device(
        user,
        resolved_match,
        ip_address=ip_address,
        user_agent=user_agent,
        now=now,
    )

    passed_second_factor = second_factor in (
        LoginAttempt.SecondFactor.TOTP,
        LoginAttempt.SecondFactor.RECOVERY_CODE,
    )
    if passed_second_factor and trust_device and cfg.trusted_device_days > 0:
        grant_device_trust(fin.device, cfg.trusted_device_days, now=now)

    record_login_attempt(
        username_input=user.username,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
        result=LoginAttempt.Result.SUCCESS,
        second_factor=second_factor,
        is_new_device=resolved_match.is_new_device,
        created_at=now,
    )

    notify_login_success(
        user,
        ip_address=ip_address,
        user_agent=user_agent,
        second_factor=second_factor,
        is_new_device=resolved_match.is_new_device,
        login_at=now,
        setting=cfg,
    )

    requires_setup = is_two_factor_required(user, setting=cfg) and not has_confirmed_totp(
        user
    )

    return LoginFlowResult(
        status=LoginStepStatus.COMPLETED,
        redirect_url=safe_next,
        device_token=fin.plain_token,
        second_factor=second_factor,
        is_new_device=resolved_match.is_new_device,
        requires_two_factor_setup=requires_setup,
    )


def submit_first_factor(
    request,
    *,
    username: str | None,
    password: str | None,
    next_url: str | None = None,
    now=None,
) -> LoginFlowResult:
    """順1〜5：IP遮断判定・ロック判定・第一要素認証・第二要素要否判定・仮認証作成。

    [性質] 副作用あり（拒否／失敗時は LoginAttempt / AccountLock / ActionLog ／通知予約、
            第二要素不要時はセッション確立・端末確定・通知予約、必要時は仮認証作成）
    [入力] request: HttpRequest, username: str | None, password: str | None,
           next_url: str | None, now: datetime | None
    [出力] LoginFlowResult

    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    ip_address = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "") or ""
    raw_username = username or ""

    # 順1: IP 遮断判定
    if is_ip_blocked(ip_address, now=current):
        record_login_attempt(
            username_input=raw_username,
            user=None,
            ip_address=ip_address,
            user_agent=user_agent,
            result=LoginAttempt.Result.REJECTED_IP_BLOCKED,
            second_factor="",
            created_at=current,
        )
        return LoginFlowResult(
            status=LoginStepStatus.REJECTED,
            error_message=COMMON_LOGIN_ERROR_MESSAGE,
        )

    # 順2: ユーザー特定とアカウントロック判定（認証処理より前に行う）
    lookup = resolve_login_users(raw_username)
    if are_any_users_locked(lookup.target_users, now=current):
        record_login_attempt(
            username_input=raw_username,
            user=lookup.primary_user,
            ip_address=ip_address,
            user_agent=user_agent,
            result=LoginAttempt.Result.REJECTED_LOCKED,
            second_factor="",
            created_at=current,
        )
        return LoginFlowResult(
            status=LoginStepStatus.REJECTED,
            error_message=COMMON_LOGIN_ERROR_MESSAGE,
        )

    # 順3: 第一要素の認証
    user = authenticate(request, username=raw_username, password=password or "")
    if user is None or not user.is_active:
        record_failure_and_notify(
            username_input=raw_username,
            target_users=lookup.target_users,
            primary_user=lookup.primary_user,
            ip_address=ip_address,
            user_agent=user_agent,
            result=LoginAttempt.Result.PASSWORD_FAILED,
            now=current,
        )
        return LoginFlowResult(
            status=LoginStepStatus.INVALID_CREDENTIALS,
            error_message=COMMON_LOGIN_ERROR_MESSAGE,
        )

    # 順4: 端末照合と第二要素要否判定
    backend_path = getattr(user, "backend", "") or settings.AUTHENTICATION_BACKENDS[0]
    safe_next = get_safe_redirect_url(request, next_url)
    match_res = match_login_device(user, request=request, now=current)
    requirement = evaluate_second_factor_requirement(
        user, is_trusted_device=match_res.is_trusted
    )

    if requirement in (
        SecondFactorRequirement.NOT_ENROLLED,
        SecondFactorRequirement.TRUSTED_DEVICE,
    ):
        sf_value = (
            LoginAttempt.SecondFactor.NONE
            if requirement == SecondFactorRequirement.NOT_ENROLLED
            else LoginAttempt.SecondFactor.TRUSTED_DEVICE
        )
        return _complete_login(
            request,
            user=user,
            backend_path=backend_path,
            second_factor=sf_value,
            safe_next=safe_next,
            trust_device=False,
            match_result=match_res,
            ip_address=ip_address,
            user_agent=user_agent,
            now=current,
        )

    # 順5: 仮認証（保留状態）の作成
    _store_pending_auth(
        request,
        user=user,
        backend_path=backend_path,
        safe_next=safe_next,
        now=current,
    )
    return LoginFlowResult(status=LoginStepStatus.SECOND_FACTOR_REQUIRED)


def submit_second_factor(
    request,
    *,
    code: str | None,
    method: SecondFactorMethod | str,
    trust_device: bool = False,
    now=None,
) -> LoginFlowResult:
    """順6〜10：仮認証の読み出し・再判定とコード検証・ログイン完了処理。

    [性質] 副作用あり（拒否／失敗時は LoginAttempt / AccountLock / ActionLog / 通知予約、
            成功時はセッション確立・失敗回数リセット・端末確定・信頼付与・通知予約）
    [入力] request: HttpRequest, code: str | None, method: SecondFactorMethod | str,
           trust_device: bool, now: datetime | None
    [出力] LoginFlowResult

    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    load_res = load_pending_auth(request, now=current)
    if load_res.state is None:
        return LoginFlowResult(
            status=LoginStepStatus.EXPIRED,
            error_message=EXPIRED_PENDING_AUTH_MESSAGE if load_res.expired else "",
        )

    pending = load_res.state
    ip_address = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "") or ""

    # 順6: 再判定とコード検証
    verify_res = verify_second_factor(
        pending.user,
        code,
        method=method,
        ip_address=ip_address,
        user_agent=user_agent,
        now=current,
    )
    if verify_res.is_rejected or verify_res.entered_lock:
        clear_pending_auth(request)
        return LoginFlowResult(
            status=LoginStepStatus.REJECTED,
            error_message=COMMON_LOGIN_ERROR_MESSAGE,
        )
    if verify_res.status == SecondFactorVerifyStatus.USED:
        return LoginFlowResult(
            status=LoginStepStatus.CODE_USED,
            error_message=USED_CODE_ERROR_MESSAGE,
        )
    if not verify_res.is_success:
        return LoginFlowResult(
            status=LoginStepStatus.CODE_INVALID,
            error_message=INVALID_CODE_ERROR_MESSAGE,
        )

    # 順7〜11: 最終確定直前に端末照合を再実行してログイン完了処理へ
    match_res = match_login_device(pending.user, request=request, now=current)
    return _complete_login(
        request,
        user=pending.user,
        backend_path=pending.backend,
        second_factor=verify_res.method,
        safe_next=pending.next_url,
        trust_device=trust_device,
        match_result=match_res,
        ip_address=ip_address,
        user_agent=user_agent,
        now=current,
    )
