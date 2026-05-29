"""自社 SMTP 方式のバウンス取り込み（IMAP ポーリング、仕様書 v1.6 §10.1 / §10.1.1 / §10.2、rev14）。

cron 管理コマンド `poll_bounce_inbox` から呼ばれる。バウンス用メールボックスを IMAP で
接続して未読メールを取り込み、ヘッダーから email / bounce_type / reason を抽出して
`process_bounce()` に渡す。

【cron 内部処理パターン（§7.2.1 踏襲）】
  - 中間ステータスなし（IMAP メール側にフラグを付けず、処理した UID を返して
    呼び出し元の管理コマンドが Seen フラグを立てる）
  - 1 件 1 atomic（process_bounce 自体が atomic を切る）
  - 自然再処理（処理中に落ちても次回 cron が未読を再取得する）

【接続設定の持ち方（コード君判断、§10.1.1）】
  Django settings 経由（.env で値を渡す）。本ファイルでは settings から以下を読む：
    - BOUNCE_INBOX_HOST            : str（必須、未設定で raise）
    - BOUNCE_INBOX_PORT            : int（既定 993、IMAPS）
    - BOUNCE_INBOX_USER            : str（必須）
    - BOUNCE_INBOX_PASSWORD        : str（必須）
    - BOUNCE_INBOX_USE_SSL         : bool（既定 True）
    - BOUNCE_INBOX_MAILBOX         : str（既定 "INBOX"）
  Phase 5 では実 IMAP 接続コードを実装するが、テストではモック注入できる
  fetch_bounce_messages を経由させる構造にする（依存性逆転）。

【判定ロジック（最小限）】
  - 5xx → hard
  - 4xx → soft
  - その他 → soft（保守的）
  実運用で精度が必要になったら拡張する想定。Phase 5 では「枠だけ確実に動かす」。
"""

from __future__ import annotations

import email as email_mod
import imaplib
import logging
import re
from email.message import Message
from typing import Iterator, Optional, Tuple

from django.conf import settings

from mailings.services.bounce_processor import (
    BOUNCE_TYPE_HARD,
    BOUNCE_TYPE_SOFT,
    process_bounce,
)


logger = logging.getLogger(__name__)


# Failed-Recipients / Original-Recipient / To 等から推定する RE
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 5xx / 4xx の SMTP コード抽出
_RE_SMTP_5XX = re.compile(r"\b5\d\d\b")
_RE_SMTP_4XX = re.compile(r"\b4\d\d\b")


def parse_bounce_message(raw_email_bytes: bytes) -> Optional[Tuple[str, str, str]]:
    """生バウンスメールから (email, bounce_type, reason) を抽出する（純関数）。

    [性質] 純関数（DB 操作なし・I/O なし）
    [入力] raw_email_bytes: bytes（IMAP RFC822 取得結果）
    [出力] (email, bounce_type, reason) または None（抽出失敗時）

    最小実装：
      - DSN（RFC 3464）の Failed-Recipients / Original-Recipient ヘッダ優先
      - 無ければ本文中の最初のメアドを採用
      - reason は本文全体（先頭 2KB に切り詰め）
      - bounce_type は SMTP コードから判定（5xx → hard、4xx → soft、不明 → soft）
    """
    try:
        msg: Message = email_mod.message_from_bytes(raw_email_bytes)
    except Exception:
        return None

    body_text = _extract_text_payload(msg)
    if not body_text:
        return None

    # 1. メアド抽出（ヘッダ優先 → 本文）
    target_email = (
        _extract_from_header(msg, "Failed-Recipients")
        or _extract_from_header(msg, "Original-Recipient")
        or _extract_from_header(msg, "Final-Recipient")
        or _extract_email_from_text(body_text)
    )
    if not target_email:
        return None

    # 2. bounce_type 判定
    if _RE_SMTP_5XX.search(body_text):
        bounce_type = BOUNCE_TYPE_HARD
    elif _RE_SMTP_4XX.search(body_text):
        bounce_type = BOUNCE_TYPE_SOFT
    else:
        # コード抽出失敗時は保守的に soft
        bounce_type = BOUNCE_TYPE_SOFT

    reason = body_text[:2000]
    return target_email, bounce_type, reason


def _extract_text_payload(msg: Message) -> str:
    """multipart を辿って最初の text/plain ペイロードを文字列で返す。"""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""


def _extract_from_header(msg: Message, header_name: str) -> Optional[str]:
    value = msg.get(header_name, "")
    if not value:
        return None
    # "rfc822; foo@example.com" のような形式に対応
    if ";" in value:
        value = value.split(";", 1)[1]
    m = _RE_EMAIL.search(value)
    return m.group(0) if m else None


def _extract_email_from_text(text: str) -> Optional[str]:
    m = _RE_EMAIL.search(text)
    return m.group(0) if m else None


def fetch_bounce_messages(connection: imaplib.IMAP4) -> Iterator[Tuple[bytes, bytes]]:
    """IMAP 接続から未読メールを 1 件ずつ (uid, raw_bytes) で yield する。

    [性質] 副作用あり（IMAP I/O、Seen フラグ立て）
    [入力] connection: imaplib.IMAP4 互換オブジェクト（テストでは mock 可）
    [出力] Iterator[(uid: bytes, raw_bytes: bytes)]

    UID 検索で未読を取り、1 件ずつ fetch して yield。呼び出し元（管理コマンド）が
    process_bounce 成功後に Seen を立てる（自然再処理：エラー時は未読のまま残る）。
    """
    mailbox = getattr(settings, "BOUNCE_INBOX_MAILBOX", "INBOX")
    connection.select(mailbox)
    typ, data = connection.uid("SEARCH", None, "UNSEEN")
    if typ != "OK":
        return
    uids = data[0].split() if data and data[0] else []
    for uid in uids:
        typ, msg_data = connection.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
        yield uid, raw


def open_imap_connection() -> imaplib.IMAP4:
    """settings から IMAP 接続を開く。

    [性質] 副作用あり（ネットワーク I/O）
    [出力] imaplib.IMAP4 接続インスタンス（呼び出し元が close する責務）
    [例外] AttributeError（必須 settings 未設定）/ imaplib エラー
    """
    host = settings.BOUNCE_INBOX_HOST
    port = getattr(settings, "BOUNCE_INBOX_PORT", 993)
    user = settings.BOUNCE_INBOX_USER
    password = settings.BOUNCE_INBOX_PASSWORD
    use_ssl = getattr(settings, "BOUNCE_INBOX_USE_SSL", True)

    if use_ssl:
        conn = imaplib.IMAP4_SSL(host, port)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.login(user, password)
    return conn


def process_one_message(raw_bytes: bytes) -> bool:
    """1 件のバウンスメールを解析して反映する（§7.2.1 「1 件 1 atomic」相当）。

    [性質] 副作用あり（process_bounce 経由で DB 書込）
    [入力] raw_bytes: バウンスメールの RFC822 バイト列
    [出力] bool（解析・反映に成功したら True、抽出失敗時は False）

    抽出に失敗したメールは False を返し、呼び出し元（管理コマンド）が Seen を
    立てない → 次回 cron で再試行 or 人間が手動確認できる。
    """
    parsed = parse_bounce_message(raw_bytes)
    if parsed is None:
        return False
    target_email, bounce_type, reason = parsed
    process_bounce(target_email, bounce_type, reason)
    return True
