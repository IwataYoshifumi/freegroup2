"""パスワード変更・リセット通知メール送信（仕様書 v1.0 §7 / §13-6 / §13-7 / §13-18）。

仕様書付録A では accounts/notifications.py、v0.2 追記分では
accounts/login_security/notifications.py と記載があるが、既存の二段階認証通知
（accounts/login_security/notifications.py）に触れず Step 6 の載せ替えまで分離するため、
本パッケージ配下（accounts/password_security/notifications.py）に配置する。

厳守事項：
  - 送信は common/mail.py の send_mail_safely をキーワード引数のみで呼び出す。
  - タイムアウトは 5 秒（PASSWORD_NOTIFICATION_MAIL_TIMEOUT_SECONDS）を明示指定する。
  - DB 確定後に送る（transaction.on_commit に登録）。
  - 送信失敗しても呼び出し側の変更・リセット自体は成立させ、例外を外に出さない。
  - パスワード・トークン・リンクをログに出力しない（リンクメール本文を除く）。
"""

import logging

from django.conf import settings
from django.db import transaction

from accounts.login_security.notifications import (
    _format_jst,
    summarize_user_agent,
)
from accounts.login_security.user_lookup import get_admin_notification_recipients
from common.mail import send_mail_safely

logger = logging.getLogger("accounts")

PASSWORD_NOTIFICATION_MAIL_TIMEOUT_SECONDS = 5


def _format_expiry_label(timeout_seconds: int) -> str:
    if timeout_seconds % 3600 == 0 and timeout_seconds >= 3600:
        return f"{timeout_seconds // 3600}時間"
    if timeout_seconds % 60 == 0 and timeout_seconds >= 60:
        return f"{timeout_seconds // 60}分"
    return f"{timeout_seconds}秒"


def _collect_admin_bcc_emails() -> list[str]:
    seen: set[str] = set()
    emails: list[str] = []
    for admin_user in get_admin_notification_recipients():
        email = (admin_user.email or "").strip()
        if not email:
            continue
        email_lower = email.lower()
        if email_lower in seen:
            continue
        seen.add(email_lower)
        emails.append(email)
    return emails


def _schedule_send_mail(
    *,
    to: list[str],
    bcc: list[str],
    subject: str,
    body: str,
) -> None:
    def _send():
        send_mail_safely(
            to=to,
            bcc=bcc,
            subject=subject,
            body=body,
            timeout=PASSWORD_NOTIFICATION_MAIL_TIMEOUT_SECONDS,
        )

    transaction.on_commit(_send)


def notify_password_reset_requested(
    user,
    *,
    reset_url: str,
    expires_seconds: int | None = None,
) -> bool:
    """【1】パスワードリセット申請メール（再設定リンク）を本人へ送信予約する（仕様書 §7）。"""
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return False

    timeout_sec = (
        int(getattr(settings, "PASSWORD_RESET_TIMEOUT", 3600))
        if expires_seconds is None
        else int(expires_seconds)
    )
    expiry_text = _format_expiry_label(timeout_sec)

    subject = "【FreeGroup2】パスワード再設定のご案内"
    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 のパスワード再設定の申請を受け付けました。\n"
        "以下のリンクから新しいパスワードを設定してください。\n\n"
        f"{reset_url}\n\n"
        f"・有効期限: {expiry_text}\n\n"
        "このメールに心当たりがない場合は、このメールを無視してください。"
        "パスワードは変更されません。\n"
    )
    _schedule_send_mail(
        to=[user_email],
        bcc=[],
        subject=subject,
        body=body,
    )
    return True


def notify_password_changed(
    user,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    operated_at=None,
) -> bool:
    """【2】パスワード変更完了通知を本人へ送信予約する（仕様書 §7）。"""
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return False

    time_text = _format_jst(operated_at)
    ip_text = ip_address or "不明"
    ua_text = summarize_user_agent(user_agent)

    subject = "【FreeGroup2】パスワードを変更しました"
    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 のパスワードを変更しました。\n\n"
        f"・変更日時: {time_text}\n"
        f"・ユーザー名: {user.username}\n"
        f"・IP アドレス: {ip_text}\n"
        f"・ブラウザ・OS: {ua_text}\n\n"
        "この操作に心当たりがない場合は、直ちにシステム管理者へご連絡ください。\n"
    )
    _schedule_send_mail(
        to=[user_email],
        bcc=[],
        subject=subject,
        body=body,
    )
    return True


def notify_password_reset_completed_to_user(
    user,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    operated_at=None,
) -> bool:
    """【3】パスワードリセット完了通知を本人へ送信予約する（仕様書 §7）。"""
    user_email = (getattr(user, "email", "") or "").strip()
    if not user_email:
        return False

    time_text = _format_jst(operated_at)
    ip_text = ip_address or "不明"
    ua_text = summarize_user_agent(user_agent)

    subject = "【FreeGroup2】パスワードを再設定しました"
    body = (
        f"{user.username} 様\n\n"
        "FreeGroup2 のパスワードを再設定しました。\n\n"
        f"・再設定日時: {time_text}\n"
        f"・ユーザー名: {user.username}\n"
        f"・IP アドレス: {ip_text}\n"
        f"・ブラウザ・OS: {ua_text}\n\n"
        "この操作に心当たりがない場合は、直ちにシステム管理者へご連絡ください。\n"
    )
    _schedule_send_mail(
        to=[user_email],
        bcc=[],
        subject=subject,
        body=body,
    )
    return True


def notify_password_reset_completed_to_admins(
    user,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    operated_at=None,
) -> bool:
    """【4】二段階認証未登録ユーザーのリセット完了通知を管理者全員へ BCC 1通で送信予約する（仕様書 §7）。"""
    bcc_emails = _collect_admin_bcc_emails()
    if not bcc_emails:
        logger.error(
            "パスワードリセット完了の管理者宛通知を送信できませんでした: "
            "通知先の管理者が 0 名です (user_id=%s)",
            user.pk,
        )
        return False

    time_text = _format_jst(operated_at)
    ip_text = ip_address or "不明"
    ua_text = summarize_user_agent(user_agent)

    subject = (
        "【FreeGroup2】【要確認】二段階認証が未登録のユーザーがパスワードを再設定しました"
    )
    body = (
        "管理者各位\n\n"
        "二段階認証が未登録のユーザーが、メール経由でパスワードを再設定しました。\n"
        "本人の操作か確認してください。\n\n"
        f"・対象ユーザー名: {user.username}\n"
        f"・再設定日時: {time_text}\n"
        f"・IP アドレス: {ip_text}\n"
        f"・ブラウザ・OS: {ua_text}\n"
    )
    _schedule_send_mail(
        to=[],
        bcc=bcc_emails,
        subject=subject,
        body=body,
    )
    return True
