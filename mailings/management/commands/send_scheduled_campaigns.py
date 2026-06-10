"""予約配信の cron 入口管理コマンド (I)（仕様書 v1.6 §4.2.1 / §7.2.1、rev14）。

cron が 1 分おきにこのコマンドを起動する。本コマンドはオーケストレーション層に徹し、
判定・送信ロジックはサービス層（mailings/services/campaign_send.py）に置く
（v1.4.2 重複チェック系の「管理コマンドはオーケストレーション層」方針の踏襲、§7.2.1）。

責務：
  1. Campaign.get_pending_scheduled() で対象キャンペーンを最古優先で取得
     （scheduled or sending、scheduled_at <= now()）
  2. 各キャンペーンに execute_campaign_send(batch_limit) を呼ぶ
  3. 結果ログ出力

EmailContext に触れない（サービス層に閉じる、§4.2.1）。

呼ばれる cron 例：
  * * * * * /path/to/venv/bin/python /path/to/freegroup2/manage.py send_scheduled_campaigns

【自然再処理方式】 中間ステータス・stuck sweeper を持たない（§7.2.1 / §7.2.4）。
1 起動でキャンペーンを送り切らず、未送信は次回 cron 起動が自然に拾う。
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from config.constants import CAMPAIGN_SEND_BATCH_LIMIT
from mailings.models import Campaign
from mailings.services.campaign_send import execute_campaign_send

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "予約配信の cron 入口。scheduled / sending 状態かつ scheduled_at <= now() の"
        "Campaign を最古優先で拾い、N 件枠で配信実行サービスを呼ぶ（§7.2.1）。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=CAMPAIGN_SEND_BATCH_LIMIT,
            help=(
                "1 起動で 1 キャンペーンあたり処理する未送信受信者の上限 N "
                f"（デフォルト {CAMPAIGN_SEND_BATCH_LIMIT}、§7.2.1）"
            ),
        )

    def handle(self, *args, **options):
        batch_limit = options["limit"]
        now = timezone.now()

        # scheduled / sending の両方を対象（sending は前回起動で途中まで進んだもの、§7.2.1）。
        # scheduled_at <= now で過去日時のみ。古い順で公平処理（最古優先）。
        targets = list(
            Campaign.objects.filter(
                Q(status=Campaign.Status.SCHEDULED)
                | Q(status=Campaign.Status.SENDING),
                scheduled_at__lte=now,
            ).order_by("scheduled_at", "created_at")
        )

        if not targets:
            self.stdout.write("対象キャンペーンなし")
            return

        for campaign in targets:
            try:
                summary = execute_campaign_send(
                    campaign, batch_limit=batch_limit, actor=None
                )
            except Exception as exc:
                # 1 キャンペーンの失敗が他キャンペーンを止めないようにする
                # （v1.4.2 重複チェック系のエラーハンドリングと同方針、§7.2.1）。
                logger.exception(
                    "Campaign %s execute_campaign_send failed: %s", campaign.pk, exc
                )
                self.stderr.write(
                    f"Campaign {campaign.pk} ({campaign.name}) 失敗: {exc}"
                )
                continue
            self.stdout.write(
                f"Campaign {campaign.pk} ({campaign.name}): "
                f"processed={summary['processed']} "
                f"sent={summary['sent']} failed={summary['failed']} "
                f"finally_failed={summary['finally_failed']} done={summary['done']}"
            )
