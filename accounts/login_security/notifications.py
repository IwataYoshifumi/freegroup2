"""ログインセキュリティ通知メール送信（仕様書 v1.3.1 §5.5 / §6.3 / §6.7 / §7.1〜§7.5 / §8.2 / §14 #11）。

共通ルール：
  - 専用接続 `get_connection(timeout=5)` を使用する（メール配信設定には影響させない）。
  - 送信は `transaction.on_commit` に登録して DB 確定後に実行する。
  - 送信例外はすべて捕捉してエラーログに残し、呼び出し元へ伝えない。
  - ログにはユーザー ID・通知種類・例外クラス名のみを出力し、メールアドレス・本文・秘密値は出力しない。
  - 件名にユーザー入力値（ユーザー名など）を入れない。本文にログイン用 URL リンクを入れない。
"""

from dataclasses import dataclass
import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.utils import timezone

from accounts.models import LoginAttempt, SecuritySetting

from .user_lookup import get_admin_notification_recipients, get_security_setting

logger = logging.getLogger("accounts")

NOTIFICATION_MAIL_TIMEOUT_SECONDS = 5
JST = ZoneInfo("Asia/Tokyo")

_SECOND_FACTOR_LABELS = {
    LoginAttempt.SecondFactor.TOTP: "認証アプリ",
    LoginAttempt.SecondFactor.RECOVERY_CODE: "リカバリーコード",
    LoginAttempt.SecondFactor.TRUSTED_DEVICE: "省略（信頼済み端末）",
    LoginAttempt.SecondFactor.NONE: "未設定",
}


@dataclass(frozen=True)
class WeakenedSettingChange:
    """セキュリティ設定を弱める変更の 1 項目。"""

    label: str
    old_value: str
    new_value: str


def is_email_configured() -> bool:
    """通知メールの既定差出人（DEFAULT_FROM_EMAIL）が設定されているかを返す。

    [性質] 純関数（settings を参照、副作用なし）
    [入力] なし
    [出力] bool
    """
    return bool((getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip())


def _get_default_from_email() -> str:
    return (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()


def _format_jst(dt=None) -> str:
    target = dt or timezone.now()
    if timezone.is_naive(target):
        target = timezone.make_aware(target, ZoneInfo("UTC"))
    return target.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S (JST)")


def summarize_user_agent(user_agent: str | None) -> str:
    """ブラウザ情報（User-Agent）から「ブラウザ／OS」の簡易要約を返す。

    [性質] 純関数（副作用なし）
    [入力] user_agent: str | None
    [出力] str（例: "Chrome／Windows"、判別不能時は "不明"）
    """
    ua = (user_agent or "").strip()
    if not ua:
        return "不明"

    browser = "不明"
    if "Edg/" in ua or "EdgA/" in ua or "EdgiOS/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "Firefox/" in ua or "FxiOS/" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"

    os_name = "不明"
    if "Windows" in ua:
        os_name = "Windows"
    elif "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"
    elif "Android" in ua:
        os_name = "Android"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_name = "macOS"
    elif "Linux" in ua:
        os_name = "Linux"

    if browser == "不明" and os_name == "不明":
        return "不明"
    return f"{browser}／{os_name}"


def should_send_login_notification(
    login_notification_level: str,
    is_new_device: bool,
) -> bool:
    """通知レベルと新しい端末かどうかから、ログイン成功通知を送るべきか判定する。

    [性質] 純関数（副作用なし）
    [入力] login_notification_level: str, is_new_device: bool
    [出力] bool（新しい端末はレベルに関わらず常に True）
    """
    if is_new_device:
        return True
    return login_notification_level == SecuritySetting.LoginNotificationLevel.EVERY


def _schedule_email(
    *,
    subject: str,
    body: str,
    to: list[str],
    bcc: list[str] | None = None,
    user_id: int | None,
    notification_type: str,
) -> bool:
    """DEFAULT_FROM_EMAIL 確認の上、transaction.on_commit でメール送信を予約する。"""
    from_email = _get_default_from_email()
    if not from_email:
        logger.warning(
            "通知メール送信をスキップしました: DEFAULT_FROM_EMAIL 未設定 (user_id=%s, type=%s)",
            user_id,
            notification_type,
        )
        return False

    bcc_list = list(bcc or [])

    def _send():
        try:
            connection = get_connection(timeout=NOTIFICATION_MAIL_TIMEOUT_SECONDS)
            msg = EmailMessage(
                subject=subject,
                body=body,
                from_email=from_email,
                to=to,
                bcc=bcc_list,
                connection=connection,
            )
            msg.send(fail_silently=False)
        except Exception as exc:
            logger.error(
                "通知メール送信に失敗しました (user_id=%s, type=%s, error=%s)",
                user_id,
                notification_type,
                type(exc).__name__,
            )

    transaction.on_commit(_send)
    return True


def _get_admin_bcc_emails(
    *,
    exclude_email: str | None = None,
    notification_type: str,
) -> list[str]:
    exclude_lower = (exclude_email or "").strip().lower()
    seen: set[str] = set()
    result: list[str] = []
    for admin_user in get_admin_notification_recipients():
        email = (admin_user.email or "").strip()
        if not email:
            continue
        email_lower = email.lower()
        if exclude_lower and email_lower == exclude_lower:
            continue
        if email_lower in seen:
            continue
        seen.add(email_lower)
        result.append(email)

    if not result:
        logger.info(
            "管理者宛通知メールの宛先が 0 名のため送信をスキップしました (type=%s)",
            notification_type,
        )
    return result


def _send_to_admins(
    *,
    subject: str,
    body: str,
    notification_type: str,
    exclude_email: str | None = None,
    user_id: int | None = None,
) -> bool:
    from_email = _get_default_from_email()
    if not from_email:
        logger.warning(
            "管理者宛通知メール送信をスキップしました: DEFAULT_FROM_EMAIL 未設定 (type=%s)",
            notification_type,
        )
        return False

    bcc = _get_admin_bcc_emails(
        exclude_email=exclude_email,
        notification_type=notification_type,
    )
    if not bcc:
        return False

    return _schedule_email(
        subject=subject,
        body=body,
        to=[from_email],
        bcc=bcc,
        user_id=user_id,
        notification_type=notification_type,
    )


def notify_login_success(
    user,
    *,
    ip_address: str | None,
    user_agent: str | None,
    second_factor: str,
    is_new_device: bool,
    login_at=None,
    setting: SecuritySetting | None = None,
) -> bool:
    """ログイン成功通知メールを本人へ送信予約する（仕様書 §7.1 / §7.3）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録。CustomUser は保存しない）
    [入力] user: CustomUser, ip_address: str | None, user_agent: str | None,
           second_factor: str, is_new_device: bool, login_at: datetime | None,
           setting: SecuritySetting | None
    [出力] bool（送信予約した場合 True、対象外または宛先・差出人未設定でスキップ時 False）
    """
    cfg = setting or get_security_setting()
    if not should_send_login_notification(cfg.login_notification_level, is_new_device):
        return False

    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return False

    subject = (
        "【FreeGroup2】【要確認】新しい端末からのログイン"
        if is_new_device
        else "【FreeGroup2】ログイン通知"
    )
    ip_text = ip_address or "不明"
    ua_text = summarize_user_agent(user_agent)
    sf_text = _SECOND_FACTOR_LABELS.get(second_factor, "不明")
    time_text = _format_jst(login_at)

    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 へのログインがありました。\n\n"
        f"・ログイン日時: {time_text}\n"
        f"・ユーザー名: {user.username}\n"
        f"・IP アドレス: {ip_text}\n"
        f"・ブラウザ・OS: {ua_text}\n"
        f"・第二要素: {sf_text}\n\n"
        "このログインに心当たりがない場合は、直ちにパスワードを変更し、"
        "システム管理者へご連絡ください。\n"
    )
    return _schedule_email(
        subject=subject,
        body=body,
        to=[user_email],
        user_id=user.pk,
        notification_type="login_success",
    )


def notify_account_lock(
    user,
    *,
    ip_address: str | None,
    locked_until,
    setting: SecuritySetting | None = None,
) -> None:
    """アカウントロック発生通知を本人（常時）と管理者（設定による）へ送信予約する（仕様書 §5.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録。CustomUser は保存しない）
    [入力] user: CustomUser, ip_address: str | None, locked_until: datetime,
           setting: SecuritySetting | None
    [出力] None
    """
    cfg = setting or get_security_setting()
    unlock_text = _format_jst(locked_until)
    ip_text = ip_address or "不明"
    user_email = (getattr(user, "email", "") or "").strip()

    if user_email:
        user_body = (
            f"{user.username} 様\n\n"
            "ログインの連続失敗により、アカウントが一時的にロックされました。\n\n"
            f"・解除の目安時刻: {unlock_text}\n"
            f"・接続元 IP アドレス: {ip_text}\n\n"
            "心当たりがない場合は、パスワードの変更およびシステム管理者への連絡をご検討ください。\n"
        )
        _schedule_email(
            subject="【FreeGroup2】アカウントロックのお知らせ",
            body=user_body,
            to=[user_email],
            user_id=user.pk,
            notification_type="account_lock_user",
        )

    if cfg.notify_admins_on_account_lock:
        admin_body = (
            "管理者各位\n\n"
            "以下のユーザーでログイン連続失敗によるアカウントロックが発生しました。\n\n"
            f"・対象ユーザー名: {user.username}\n"
            f"・接続元 IP アドレス: {ip_text}\n"
            f"・解除の目安時刻: {unlock_text}\n"
        )
        _send_to_admins(
            subject="【FreeGroup2】アカウントロック発生通知",
            body=admin_body,
            notification_type="account_lock_admin",
            exclude_email=user_email,
            user_id=user.pk,
        )


def notify_ip_block(
    *,
    ip_address: str,
    setting: SecuritySetting | None = None,
) -> None:
    """IP 遮断発生通知を管理者（設定による）へ送信予約する（仕様書 §5.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] ip_address: str, setting: SecuritySetting | None
    [出力] None
    """
    cfg = setting or get_security_setting()
    if not cfg.notify_admins_on_ip_block:
        return

    body = (
        "管理者各位\n\n"
        "同一 IP アドレスからの連続失敗が閾値に達したため、ログイン試行を一時的に遮断しました。\n\n"
        f"・対象 IP アドレス: {ip_address}\n"
        f"・15分間の失敗回数閾値: {cfg.ip_block_threshold} 回\n"
    )
    _send_to_admins(
        subject="【FreeGroup2】IP 遮断発生通知",
        body=body,
        notification_type="ip_block_admin",
    )


def notify_two_factor_enabled(user, *, operated_at=None) -> None:
    """二段階認証有効化通知を本人へ送信予約する（仕様書 §6.2 / §7.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] user: CustomUser, operated_at: datetime | None
    [出力] None
    """
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return

    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 の二段階認証が有効化されました。\n\n"
        f"・操作日時: {_format_jst(operated_at)}\n\n"
        "この操作に心当たりがない場合は、直ちにシステム管理者へご連絡ください。\n"
    )
    _schedule_email(
        subject="【FreeGroup2】二段階認証を有効化しました",
        body=body,
        to=[user_email],
        user_id=user.pk,
        notification_type="two_factor_enabled",
    )


def notify_two_factor_disabled(user, *, operated_at=None) -> None:
    """二段階認証無効化通知を本人へ送信予約する（仕様書 §6.5 / §7.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] user: CustomUser, operated_at: datetime | None
    [出力] None
    """
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return

    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 の二段階認証が無効化されました。\n\n"
        f"・操作日時: {_format_jst(operated_at)}\n\n"
        "この操作に心当たりがない場合は、直ちにパスワードを変更し、"
        "システム管理者へご連絡ください。\n"
    )
    _schedule_email(
        subject="【FreeGroup2】二段階認証を無効化しました",
        body=body,
        to=[user_email],
        user_id=user.pk,
        notification_type="two_factor_disabled",
    )


def notify_two_factor_reenrolled(user, *, operated_at=None) -> None:
    """二段階認証再登録完了通知を本人へ送信予約する（仕様書 §6.2 / §7.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] user: CustomUser, operated_at: datetime | None
    [出力] None
    """
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return

    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 の二段階認証が再登録されました。\n"
        "以前の認証アプリ設定・リカバリーコード・信頼済み端末は無効化されています。\n\n"
        f"・操作日時: {_format_jst(operated_at)}\n\n"
        "この操作に心当たりがない場合は、直ちにパスワードを変更し、"
        "システム管理者へご連絡ください。\n"
    )
    _schedule_email(
        subject="【FreeGroup2】二段階認証を再登録しました",
        body=body,
        to=[user_email],
        user_id=user.pk,
        notification_type="two_factor_reenrolled",
    )


def notify_recovery_code_used(
    user,
    *,
    remaining_count: int,
    operated_at=None,
) -> None:
    """リカバリーコード使用通知を本人へ送信予約する（仕様書 §6.3 / §7.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] user: CustomUser, remaining_count: int, operated_at: datetime | None
    [出力] None
    """
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return

    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 でリカバリーコードが使用されました。\n\n"
        f"・使用日時: {_format_jst(operated_at)}\n"
        f"・残りのリカバリーコード数: {remaining_count} 個\n\n"
        "この操作に心当たりがない場合は、直ちにパスワードを変更し、"
        "システム管理者へご連絡ください。\n"
    )
    _schedule_email(
        subject="【FreeGroup2】リカバリーコードが使用されました",
        body=body,
        to=[user_email],
        user_id=user.pk,
        notification_type="recovery_code_used",
    )


def notify_recovery_codes_regenerated(user, *, operated_at=None) -> None:
    """リカバリーコード再発行通知を本人へ送信予約する（仕様書 §6.3 / §7.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] user: CustomUser, operated_at: datetime | None
    [出力] None
    """
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return

    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 のリカバリーコードが再発行されました。\n"
        "以前のリカバリーコードはすべて無効になっています。\n\n"
        f"・操作日時: {_format_jst(operated_at)}\n\n"
        "この操作に心当たりがない場合は、直ちにパスワードを変更し、"
        "システム管理者へご連絡ください。\n"
    )
    _schedule_email(
        subject="【FreeGroup2】リカバリーコードを再発行しました",
        body=body,
        to=[user_email],
        user_id=user.pk,
        notification_type="recovery_codes_regenerated",
    )


def notify_two_factor_reset(
    target_user,
    *,
    operator=None,
    reason_note: str = "",
    operated_at=None,
) -> None:
    """管理者による二段階認証リセット通知を本人と管理者全員へ常時送信予約する（仕様書 §6.7 / §7.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] target_user: CustomUser, operator: CustomUser | None,
           reason_note: str, operated_at: datetime | None
    [出力] None

    理由メモは管理者宛のみに記載し、本人宛には記載しない。
    """
    operator_name = operator.username if operator is not None else "サーバー管理コマンド"
    time_text = _format_jst(operated_at)
    target_email = (getattr(target_user, "email", "") or "").strip()

    if target_email:
        user_body = (
            f"{target_user.username} 様\n\n"
            "管理者操作により、あなたの二段階認証設定がリセットされました。\n\n"
            f"・対象ユーザー名: {target_user.username}\n"
            f"・操作者: {operator_name}\n"
            f"・実行日時: {time_text}\n\n"
            "次回ログイン後、速やかに認証アプリの再登録を行ってください。\n"
            "この操作に心当たりがない場合は、直ちにシステム管理者へご連絡ください。\n"
        )
        _schedule_email(
            subject="【FreeGroup2】二段階認証がリセットされました",
            body=user_body,
            to=[target_email],
            user_id=target_user.pk,
            notification_type="two_factor_reset_user",
        )

    reason_line = f"・理由メモ: {reason_note.strip()}\n" if reason_note.strip() else ""
    admin_body = (
        "管理者各位\n\n"
        "二段階認証のリセットが実行されました。\n\n"
        f"・対象ユーザー名: {target_user.username}\n"
        f"・操作者: {operator_name}\n"
        f"・実行日時: {time_text}\n"
        f"{reason_line}"
    )
    _send_to_admins(
        subject="【FreeGroup2】二段階認証リセット通知",
        body=admin_body,
        notification_type="two_factor_reset_admin",
        exclude_email=target_email,
        user_id=target_user.pk,
    )


def notify_security_setting_weakened(
    *,
    operator,
    changes: list[WeakenedSettingChange],
    operated_at=None,
) -> None:
    """セキュリティ設定を弱める変更の通知を管理者全員へ常時送信予約する（仕様書 §8.2 / §7.5）。

    [性質] 副作用あり（transaction.on_commit に送信処理を登録）
    [入力] operator: CustomUser, changes: list[WeakenedSettingChange],
           operated_at: datetime | None
    [出力] None
    """
    if not changes:
        return

    operator_name = operator.username if operator is not None else "システム"
    lines = "\n".join(
        f"・{c.label}: {c.old_value} → {c.new_value}" for c in changes
    )
    body = (
        "管理者各位\n\n"
        "ログインセキュリティ設定において、制限を緩める方向の変更が行われました。\n\n"
        f"・操作者: {operator_name}\n"
        f"・変更日時: {_format_jst(operated_at)}\n"
        "・変更内容:\n"
        f"{lines}\n"
    )
    _send_to_admins(
        subject="【FreeGroup2】【要確認】セキュリティ設定の変更通知",
        body=body,
        notification_type="security_setting_weakened",
        user_id=operator.pk if operator is not None else None,
    )
