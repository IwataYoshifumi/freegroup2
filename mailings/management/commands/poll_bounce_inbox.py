"""自社 SMTP バウンス用メールボックスを IMAP で定期取り込みする管理コマンド
（仕様書 v1.6 §10.1 / §10.1.1 / §10.2、rev14）。

cron が 5〜15 分おき程度に起動する想定（実頻度は運用判断）。本コマンドは
オーケストレーション層に徹し、IMAP I/O・解析・反映は services 層に置く
（send_scheduled_campaigns 同様の方針、§7.2.1）。

【cron 内部処理（§7.2.1 / §10.1.1）】
  - 中間ステータスなし（IMAP の \\Seen フラグで「処理済み」を表現）
  - 1 件 1 atomic（process_bounce が内部で transaction を切る）
  - 自然再処理：例外で落ちた場合 Seen を立てないので次回 cron で再試行
  - 1 起動でメールボックスを枯らさない方が運用上安全だが、cron の起動間隔で
    回せばよいので明示的な上限は設けない（必要なら --limit で制限可）

【呼ばれる cron 例】
  */5 * * * * /path/to/venv/bin/python /path/to/freegroup2/manage.py poll_bounce_inbox
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from mailings.services.bounce_smtp_handler import (
    fetch_bounce_messages,
    open_imap_connection,
    process_one_message,
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "自社 SMTP バウンス用メールボックスを IMAP でポーリングして取り込み、"
        "BounceProcessor 経由で SuppressedEmail / SoftBounceCounter / DeliveryHistory "
        "に反映する（§10.1.1）。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="1 起動で処理する最大件数（0 = 制限なし）",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        try:
            conn = open_imap_connection()
        except AttributeError as exc:
            # 必須 settings 未設定（BOUNCE_INBOX_HOST 等）
            self.stderr.write(
                f"バウンス IMAP 設定が未構成のためスキップ: {exc}"
            )
            return

        success = 0
        failure = 0
        try:
            for idx, (uid, raw) in enumerate(fetch_bounce_messages(conn), start=1):
                try:
                    ok = process_one_message(raw)
                except Exception as exc:
                    # 1 件の失敗が他件を止めないようにする（§7.2.1 同方針）
                    logger.exception(
                        "poll_bounce_inbox: process_one_message failed (uid=%s): %s",
                        uid,
                        exc,
                    )
                    failure += 1
                    continue

                if ok:
                    # 解析・反映成功 → Seen フラグを立てて次回以降再処理しない
                    try:
                        conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                    except Exception as exc:
                        logger.warning(
                            "poll_bounce_inbox: Seen flag set failed (uid=%s): %s",
                            uid,
                            exc,
                        )
                    success += 1
                else:
                    # 解析失敗 → Seen を立てず未読のまま残し人間判断に委ねる
                    failure += 1

                if limit and idx >= limit:
                    break
        finally:
            try:
                conn.close()
                conn.logout()
            except Exception:
                pass

        self.stdout.write(
            f"poll_bounce_inbox: processed_success={success} failed={failure}"
        )
