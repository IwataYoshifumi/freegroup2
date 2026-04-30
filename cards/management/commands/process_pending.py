"""status=pending の OriginalImage を順次処理する管理コマンド（仕様書 v1.2.2 §8.5.4）。

cron で 1〜5 分間隔で起動される想定。
多重起動対策に CAS 方式（filter+update で status を processing に遷移させて競合を排除）を採用。
1 回の起動で最大 N 件処理（暫定 N=10）。
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from cards.models import OriginalImage
from cards.tasks.pipeline_coordinator import PipelineCoordinator

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "status=pending の OriginalImage を順次処理する。"
        "cron 起動を想定し、CAS で多重起動を防ぐ。"
    )

    DEFAULT_LIMIT = 10

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=self.DEFAULT_LIMIT,
            help=f"1 回の起動で処理する最大件数（デフォルト: {self.DEFAULT_LIMIT}）",
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（複合処理: DB 書き込み・API 呼び出し）

        [入力] --limit: 最大処理件数
        [出力] None（処理結果は stdout に出力、status は OriginalImage に保存）
        """
        limit = max(1, options["limit"])

        # ── stuck sweeper ────────────────────────────────────────────────
        # processing のまま claimed_at がしきい値を超えたレコードは
        # プロセス異常終了等で放置されたと判断し pending に差し戻す。
        threshold_minutes = getattr(settings, "OCR_STUCK_THRESHOLD_MINUTES", 30)
        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)
        stuck_count = OriginalImage.objects.filter(
            status=OriginalImage.STATUS_PROCESSING,
            claimed_at__lt=cutoff,
        ).update(
            status=OriginalImage.STATUS_PENDING,
            claimed_at=None,
            updated_at=timezone.now(),
        )
        if stuck_count:
            logger.warning(
                "process_pending: stuck レコード %d 件を pending に差し戻しました（しきい値 %d 分）",
                stuck_count,
                threshold_minutes,
            )
        # ─────────────────────────────────────────────────────────────────

        target_ids = list(
            OriginalImage.objects.filter(status=OriginalImage.STATUS_PENDING)
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )

        if not target_ids:
            self.stdout.write("process_pending: pending なし")
            return

        self.stdout.write(f"process_pending: {len(target_ids)} 件を試行")

        processed = 0
        skipped = 0
        for target_id in target_ids:
            if not _claim_lock(target_id):
                skipped += 1
                self.stdout.write(self.style.WARNING(f"  skip (他 worker が取得済み): {target_id}"))
                continue

            try:
                original = OriginalImage.objects.get(id=target_id)
            except OriginalImage.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"  消失: {target_id}"))
                continue

            # _claim_lock 後の status は PROCESSING のはず。
            # そうでなければ DB 競合が起きているためスキップ。
            if original.status != OriginalImage.STATUS_PROCESSING:
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (status drift: {original.status}): {target_id}"
                ))
                continue

            try:
                PipelineCoordinator(original).run_pipeline()
            except Exception as e:
                # PipelineCoordinator は基本的に例外を漏らさないが防御的に捕捉
                self.stderr.write(self.style.ERROR(
                    f"  pipeline 例外 {target_id}: {type(e).__name__}: {e}"
                ))
                processed += 1
                continue

            original.refresh_from_db()
            self.stdout.write(f"  done {target_id}: status={original.status}")
            processed += 1

        self.stdout.write(self.style.SUCCESS(
            f"process_pending: targets={len(target_ids)}, processed={processed}, "
            f"skipped={skipped}"
        ))


def _claim_lock(target_id):
    """[性質] 副作用あり（DB 書き込み）/ CAS で processing に遷移させ排他を取得する。

    status=pending の行に対して status=processing・claimed_at=now を一発で書き込む。
    affected_rows が 1 なら自プロセスが排他を取得、0 なら他プロセスが先に取得済み。
    """
    updated = OriginalImage.objects.filter(
        id=target_id, status=OriginalImage.STATUS_PENDING
    ).update(
        status=OriginalImage.STATUS_PROCESSING,
        claimed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return updated == 1
