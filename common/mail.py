"""どの機能からも利用できる共通メール送信部品（仕様書 v1.3.4 §7.6 / §15 Step 1）。

注意：
  - モデル・トランザクションは扱わない（DB 確定後に呼ぶのは呼び出し側の責任）。
  - 呼び出しごとに指定タイムアウトの専用接続を作り、例外を外に出さず bool で返す。
  - 失敗時のエラーログには宛先件数・件名・例外の種類のみを出力し、
    宛先メールアドレス・本文・添付中身は一切出力しない。
"""

from __future__ import annotations

from collections.abc import Iterable
import logging

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

logger = logging.getLogger(__name__)

DEFAULT_MAIL_SEND_TIMEOUT_SECONDS = 10


def _clean_addresses(addresses: str | Iterable[str] | None) -> list[str]:
    """宛先リストから空文字・空白のみのアドレスを除外して返す。"""
    if addresses is None:
        return []
    items = [addresses] if isinstance(addresses, str) else addresses
    cleaned: list[str] = []
    for addr in items:
        if not isinstance(addr, str):
            continue
        stripped = addr.strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def send_mail_safely(
    *,
    to: str | Iterable[str] | None = None,
    bcc: str | Iterable[str] | None = None,
    subject: str,
    body: str,
    attachments: Iterable[tuple[str, bytes | str, str]] | None = None,
    from_email: str | None = None,
    timeout: int | float | None = None,
) -> bool:
    """例外を送出せず、テキストメールを専用接続で送信する。

    [性質] 副作用あり（SMTP 送信・失敗時エラーログ出力。DB・トランザクション操作なし）
    [入力] to: To 宛先メールアドレス群
           bcc: BCC 宛先メールアドレス群
           subject: 件名
           body: テキスト本文
           attachments: (filename, content, mimetype) の複数組（任意）
           from_email: 差出人（省略時は settings.DEFAULT_FROM_EMAIL）
           timeout: タイムアウト秒数（省略時は settings.MAIL_SEND_TIMEOUT、既定 10 秒）
    [出力] bool（送信成功 True / 宛先空または失敗 False）
    """
    clean_to = _clean_addresses(to)
    clean_bcc = _clean_addresses(bcc)
    if not clean_to and not clean_bcc:
        return False

    resolved_timeout = (
        getattr(settings, "MAIL_SEND_TIMEOUT", DEFAULT_MAIL_SEND_TIMEOUT_SECONDS)
        if timeout is None
        else timeout
    )
    resolved_from = (
        (from_email or "").strip()
        or (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    )
    recipient_count = len(clean_to) + len(clean_bcc)

    try:
        connection = get_connection(timeout=resolved_timeout)
        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=resolved_from,
            to=clean_to,
            bcc=clean_bcc,
            connection=connection,
        )
        if attachments:
            for filename, content, mimetype in attachments:
                msg.attach(filename, content, mimetype)
        msg.send(fail_silently=False)
        return True
    except Exception as exc:
        logger.error(
            "メール送信に失敗しました (recipients=%d, subject=%s, error=%s)",
            recipient_count,
            subject,
            type(exc).__name__,
        )
        return False
