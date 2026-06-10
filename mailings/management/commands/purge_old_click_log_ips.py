"""ClickLog の ip_masked を期限切れ後 NULL 上書きする管理コマンド（仕様書 v1.6 §8.3.2）。

GDPR・個人情報保護法対応の「マスキング + 期限付き削除」二重保護のうち削除側を担う。
マスキングは ClickLog 作成時に services/ip_masking.py で実施済み（§8.3.1）。

cron 起動：1 日 1 回（深夜帯）を想定。`send_scheduled_campaigns` 同様に「管理コマンドは
オーケストレーション層」方針（v1.4.2 重複チェック系の踏襲）で、本コマンドは判定・SQL の
組み立てに徹し、トランザクション管理を込みで 1 ステートメントで完結させる。

User-Agent は本コマンドの対象外（ボット判定の透明性確保のため恒久保持、§8.3.2 末尾）。
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from config.constants import CLICK_LOG_IP_RETENTION_DAYS
from mailings.models import ClickLog


class Command(BaseCommand):
    help = (
        "ClickLog の ip_masked を期限切れ後 NULL に上書きする（§8.3.2）。"
        "保持日数は config.constants.CLICK_LOG_IP_RETENTION_DAYS（既定 90 日）。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=CLICK_LOG_IP_RETENTION_DAYS,
            help=(
                "保持日数の上書き（テスト・運用調整用）。"
                f"未指定時は CLICK_LOG_IP_RETENTION_DAYS（既定 {CLICK_LOG_IP_RETENTION_DAYS}）"
            ),
        )

    def handle(self, *args, **options):
        days = options["days"]
        cutoff = timezone.now() - timedelta(days=days)

        # ip_masked__isnull=False 条件で「既に NULL のレコード」を SQL 段で除外し、
        # update 件数 = 今回新たに NULL 化したレコード数となる（運用ログでの差分検知用）。
        updated = ClickLog.objects.filter(
            created_at__lt=cutoff,
            ip_masked__isnull=False,
        ).update(ip_masked=None)

        self.stdout.write(
            f"ClickLog.ip_masked を NULL 化: {updated} 件（保持日数={days}日、"
            f"カットオフ={cutoff.isoformat()}）"
        )
