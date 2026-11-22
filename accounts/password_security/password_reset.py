"""メール経由パスワードリセット（申請判定・リンク発行と検証・完了処理）（仕様書 v1.0 §6 / §7 / §11.1 / §13）。

注意：
  - 判定・記録はすべて DB で行い、キャッシュを使わない。
  - 申請記録は判定の後に作成する（判定件数に自分自身を含めない）。
  - トークン・リンク・パスワードを操作ログ・エラーログ・申請記録に出力しない。
  - CustomUser の保存は update_fields=["password"] に限る。
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from accounts.constants import ActionLogAction
from accounts.login_security.client_ip import get_client_ip
from accounts.login_security.devices import revoke_user_device_trust
from accounts.login_security.lockout import is_ip_blocked
from accounts.login_security.session_revoke import revoke_user_sessions
from accounts.login_security.totp import has_confirmed_totp
from accounts.models import CustomUser, PasswordResetRequest
from actionlogs.models import ActionLog

from .eligibility import (
    is_password_self_service_eligible,
    validate_new_password_for_user,
)
from .notifications import (
    notify_password_reset_completed_to_admins,
    notify_password_reset_completed_to_user,
    notify_password_reset_requested,
)

RESET_IP_LIMIT_WINDOW_MINUTES = 15
RESET_IP_LIMIT_MAX_COUNT = 5
RESET_USER_LIMIT_WINDOW_HOURS = 1
RESET_USER_LIMIT_MAX_SENT = 3
EMAIL_INPUT_MAX_LENGTH = 254

IP_COUNTED_RESULTS = (
    PasswordResetRequest.Result.SENT,
    PasswordResetRequest.Result.NO_MATCH,
    PasswordResetRequest.Result.NOT_ELIGIBLE,
)

INVALID_RESET_LINK_MESSAGE = (
    "このリンクは無効か、有効期限が切れています。もう一度、パスワード再設定を申請してください。"
)


@dataclass(frozen=True)
class PasswordResetRequestEntry:
    """申請判定の 1 件分の記録結果。"""

    user: CustomUser | None
    result: str
    request_record: PasswordResetRequest
    mail_scheduled: bool = False


@dataclass(frozen=True)
class PasswordResetRequestOutcome:
    """パスワードリセット申請全体の処理結果。"""

    entries: tuple[PasswordResetRequestEntry, ...]
    sent_count: int
    ip_address: str | None


@dataclass(frozen=True)
class ResetTokenVerificationResult:
    """パスワードリセットリンクの検証結果。"""

    is_valid: bool
    user: CustomUser | None = None


class PasswordResetCompleteStatus(str, Enum):
    """パスワードリセット完了処理の結果ステータス。"""

    SUCCESS = "success"
    INVALID_LINK = "invalid_link"
    VALIDATION_ERROR = "validation_error"
    SAME_AS_CURRENT = "same_as_current"


@dataclass(frozen=True)
class PasswordResetCompleteResult:
    """パスワードリセット完了処理の結果。"""

    status: PasswordResetCompleteStatus
    errors: tuple[str, ...] = ()
    revoked_sessions: int = 0
    revoked_device_trusts: int = 0
    notified_admins: bool = False

    @property
    def is_success(self) -> bool:
        return self.status == PasswordResetCompleteStatus.SUCCESS


def build_password_reset_url(
    request,
    user: CustomUser,
    *,
    token: str | None = None,
) -> str:
    """リクエストのホスト名と Django 標準トークンから再設定用 URL を組み立てる（仕様書 §6.3）。"""
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    resolved_token = token or default_token_generator.make_token(user)
    try:
        path = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": uidb64, "token": resolved_token},
        )
    except NoReverseMatch:
        path = f"/accounts/password/reset/{uidb64}/{resolved_token}/"
    return request.build_absolute_uri(path)


def verify_password_reset_token(
    uidb64: str | None,
    token: str | None,
) -> ResetTokenVerificationResult:
    """再設定リンクの uidb64 とトークン、および対象ユーザーの有効性・適格性を検証する（仕様書 §6.3〜§6.4）。"""
    if not uidb64 or not token:
        return ResetTokenVerificationResult(is_valid=False, user=None)

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = CustomUser.objects.select_related("role").get(pk=uid)
    except (TypeError, ValueError, OverflowError, CustomUser.DoesNotExist, ValidationError):
        return ResetTokenVerificationResult(is_valid=False, user=None)

    if not user.is_active:
        return ResetTokenVerificationResult(is_valid=False, user=None)

    if not is_password_self_service_eligible(user):
        return ResetTokenVerificationResult(is_valid=False, user=None)

    if not default_token_generator.check_token(user, token):
        return ResetTokenVerificationResult(is_valid=False, user=None)

    return ResetTokenVerificationResult(is_valid=True, user=user)


def request_password_reset(
    email_input: str | None,
    *,
    request,
    ip_address: str | None = None,
    now=None,
) -> PasswordResetRequestOutcome:
    """パスワードリセット申請を §6.2 順1〜6 で判定し、記録と必要なメール送信・操作ログを行う。

    [性質] 副作用あり（DB 書込：PasswordResetRequest 作成、送信時のみ ActionLog ＋ on_commit 通知）
    [入力] email_input: str | None
           request: HttpRequest（ホスト名・接続元 IP 取得に使用）
           ip_address: str | None（省略時は get_client_ip(request)）
           now: datetime | None
    [出力] PasswordResetRequestOutcome
    """
    current = now or timezone.now()
    resolved_ip = ip_address if ip_address is not None else get_client_ip(request)
    raw_email = email_input if isinstance(email_input, str) else ""
    recorded_email = raw_email[:EMAIL_INPUT_MAX_LENGTH]
    normalized_email = raw_email.strip()

    # 順1：IP 遮断中か（既存仕組みの再利用）
    if is_ip_blocked(resolved_ip, now=current):
        rec = PasswordResetRequest.objects.create(
            created_at=current,
            email_input=recorded_email,
            user=None,
            ip_address=resolved_ip,
            result=PasswordResetRequest.Result.IP_BLOCKED,
        )
        return PasswordResetRequestOutcome(
            entries=(
                PasswordResetRequestEntry(
                    user=None,
                    result=PasswordResetRequest.Result.IP_BLOCKED,
                    request_record=rec,
                ),
            ),
            sent_count=0,
            ip_address=resolved_ip,
        )

    # 順2：同じ IP の申請制限（直近 15 分に 5 件以上。数えるのは送信・該当なし・対象外のみ）
    if resolved_ip:
        ip_cutoff = current - timedelta(minutes=RESET_IP_LIMIT_WINDOW_MINUTES)
        ip_count = PasswordResetRequest.objects.filter(
            ip_address=resolved_ip,
            created_at__gte=ip_cutoff,
            result__in=IP_COUNTED_RESULTS,
        ).count()
        if ip_count >= RESET_IP_LIMIT_MAX_COUNT:
            rec = PasswordResetRequest.objects.create(
                created_at=current,
                email_input=recorded_email,
                user=None,
                ip_address=resolved_ip,
                result=PasswordResetRequest.Result.RATE_LIMITED,
            )
            return PasswordResetRequestOutcome(
                entries=(
                    PasswordResetRequestEntry(
                        user=None,
                        result=PasswordResetRequest.Result.RATE_LIMITED,
                        request_record=rec,
                    ),
                ),
                sent_count=0,
                ip_address=resolved_ip,
            )

    # 順3：メールアドレス照合（空入力は照合前に除外し NO_MATCH）
    if not normalized_email:
        rec = PasswordResetRequest.objects.create(
            created_at=current,
            email_input=recorded_email,
            user=None,
            ip_address=resolved_ip,
            result=PasswordResetRequest.Result.NO_MATCH,
        )
        return PasswordResetRequestOutcome(
            entries=(
                PasswordResetRequestEntry(
                    user=None,
                    result=PasswordResetRequest.Result.NO_MATCH,
                    request_record=rec,
                ),
            ),
            sent_count=0,
            ip_address=resolved_ip,
        )

    matched_users = list(
        CustomUser.objects.filter(
            is_active=True,
            email__iexact=normalized_email,
        )
        .select_related("role")
        .order_by("id")
    )
    if not matched_users:
        rec = PasswordResetRequest.objects.create(
            created_at=current,
            email_input=recorded_email,
            user=None,
            ip_address=resolved_ip,
            result=PasswordResetRequest.Result.NO_MATCH,
        )
        return PasswordResetRequestOutcome(
            entries=(
                PasswordResetRequestEntry(
                    user=None,
                    result=PasswordResetRequest.Result.NO_MATCH,
                    request_record=rec,
                ),
            ),
            sent_count=0,
            ip_address=resolved_ip,
        )

    entries: list[PasswordResetRequestEntry] = []
    sent_count = 0
    user_cutoff = current - timedelta(hours=RESET_USER_LIMIT_WINDOW_HOURS)

    for target_user in matched_users:
        # 順4：対象外判定（§5.5 の 3 条件）
        if not is_password_self_service_eligible(target_user):
            rec = PasswordResetRequest.objects.create(
                created_at=current,
                email_input=recorded_email,
                user=target_user,
                ip_address=resolved_ip,
                result=PasswordResetRequest.Result.NOT_ELIGIBLE,
            )
            entries.append(
                PasswordResetRequestEntry(
                    user=target_user,
                    result=PasswordResetRequest.Result.NOT_ELIGIBLE,
                    request_record=rec,
                )
            )
            continue

        # 順5：同じユーザー宛の送信制限（直近 1 時間に送信済み 3 件以上）
        user_sent_count = PasswordResetRequest.objects.filter(
            user=target_user,
            created_at__gte=user_cutoff,
            result=PasswordResetRequest.Result.SENT,
        ).count()
        if user_sent_count >= RESET_USER_LIMIT_MAX_SENT:
            rec = PasswordResetRequest.objects.create(
                created_at=current,
                email_input=recorded_email,
                user=target_user,
                ip_address=resolved_ip,
                result=PasswordResetRequest.Result.RATE_LIMITED,
            )
            entries.append(
                PasswordResetRequestEntry(
                    user=target_user,
                    result=PasswordResetRequest.Result.RATE_LIMITED,
                    request_record=rec,
                )
            )
            continue

        # 順6：申請記録・操作ログ・リンク発行とメール送信予約（§11.1 により操作者は None）
        with transaction.atomic():
            rec = PasswordResetRequest.objects.create(
                created_at=current,
                email_input=recorded_email,
                user=target_user,
                ip_address=resolved_ip,
                result=PasswordResetRequest.Result.SENT,
            )
            ActionLog.record(
                user=None,
                action=ActionLogAction.PASSWORD_RESET_REQUEST,
                content_object=target_user,
                object_repr=target_user.username,
                data={"ip_address": resolved_ip or ""},
            )
            reset_url = build_password_reset_url(request, target_user)
            mail_scheduled = notify_password_reset_requested(
                target_user,
                reset_url=reset_url,
            )

        sent_count += 1
        entries.append(
            PasswordResetRequestEntry(
                user=target_user,
                result=PasswordResetRequest.Result.SENT,
                request_record=rec,
                mail_scheduled=mail_scheduled,
            )
        )

    return PasswordResetRequestOutcome(
        entries=tuple(entries),
        sent_count=sent_count,
        ip_address=resolved_ip,
    )


def complete_password_reset_for_user(
    user: CustomUser,
    *,
    new_password: str | None,
    request=None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> PasswordResetCompleteResult:
    """検証済みユーザーに対してパスワードリセット完了処理を実行する（仕様書 §6.4〜§6.5）。"""
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

    if user is None or not user.is_active or not is_password_self_service_eligible(user):
        return PasswordResetCompleteResult(
            status=PasswordResetCompleteStatus.INVALID_LINK,
            errors=(INVALID_RESET_LINK_MESSAGE,),
        )

    valid, err_kind, err_msgs = validate_new_password_for_user(user, new_password)
    if not valid:
        status = (
            PasswordResetCompleteStatus.SAME_AS_CURRENT
            if err_kind == "same_as_current"
            else PasswordResetCompleteStatus.VALIDATION_ERROR
        )
        return PasswordResetCompleteResult(status=status, errors=err_msgs)

    notified_admins = False
    with transaction.atomic():
        # 順1：パスワードのフィールドのみ保存
        user.set_password(new_password)
        user.save(update_fields=["password"])

        # 順2：対象ユーザーの全セッションを切断
        revoked_sessions = revoke_user_sessions(
            user,
            keep_session_key=None,
            now=current,
        )

        # 順3：端末の信頼をすべて取り消す（LoginDevice.trusted_until を空にする）
        revoked_device_trusts = revoke_user_device_trust(user)

        # 順4：操作ログ記録（操作者は対象ユーザー本人）
        ActionLog.record(
            user=user,
            action=ActionLogAction.PASSWORD_RESET_COMPLETE,
            content_object=user,
            object_repr=user.username,
            data={"ip_address": resolved_ip or ""},
        )

        # 順5：DB 確定後に本人宛（＋二段階認証未登録なら管理者全員 BCC 1通）を送信予約
        notify_password_reset_completed_to_user(
            user,
            ip_address=resolved_ip,
            user_agent=resolved_ua,
            operated_at=current,
        )
        if not has_confirmed_totp(user):
            notified_admins = notify_password_reset_completed_to_admins(
                user,
                ip_address=resolved_ip,
                user_agent=resolved_ua,
                operated_at=current,
            )

    return PasswordResetCompleteResult(
        status=PasswordResetCompleteStatus.SUCCESS,
        revoked_sessions=revoked_sessions,
        revoked_device_trusts=revoked_device_trusts,
        notified_admins=notified_admins,
    )


def complete_password_reset(
    *,
    uidb64: str | None,
    token: str | None,
    new_password: str | None,
    request=None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> PasswordResetCompleteResult:
    """リンクの uidb64/token を再検証した上で、パスワードリセット完了処理を実行する（仕様書 §6.4〜§6.5）。"""
    verification = verify_password_reset_token(uidb64, token)
    if not verification.is_valid or verification.user is None:
        return PasswordResetCompleteResult(
            status=PasswordResetCompleteStatus.INVALID_LINK,
            errors=(INVALID_RESET_LINK_MESSAGE,),
        )

    return complete_password_reset_for_user(
        verification.user,
        new_password=new_password,
        request=request,
        ip_address=ip_address,
        user_agent=user_agent,
        now=now,
    )
