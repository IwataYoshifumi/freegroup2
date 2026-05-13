"""status=pending の OriginalImage を順次処理する管理コマンド（仕様書 v1.2.2 §8.5.4）。

cron で 1〜5 分間隔で起動される想定。
多重起動対策に CAS 方式（filter+update で status を processing に遷移させて競合を排除）を採用。
1 回の起動で最大 N 件処理（暫定 N=10）。
"""

import logging
import os
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cards.models import BusinessCard, OriginalImage
from cards.tasks.pipeline_coordinator import PipelineCoordinator
from contacts.models import Contact
from persons.models import Person

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
        # プロセス異常終了等で放置されたと判断し、BusinessCard を削除して
        # クリーンスタートできる状態で pending に差し戻す。
        threshold_minutes = getattr(settings, "OCR_STUCK_THRESHOLD_MINUTES", 30)
        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)
        stuck_records = list(
            OriginalImage.objects.filter(
                status=OriginalImage.STATUS_PROCESSING,
                claimed_at__lt=cutoff,
            )
        )
        if stuck_records:
            cleaned = 0
            for stuck in stuck_records:
                _cleanup_stuck_record(stuck)
                cleaned += 1
            logger.warning(
                "process_pending: stuck レコード %d 件をクリーンアップし pending に差し戻しました"
                "（しきい値 %d 分）",
                cleaned,
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


def _cleanup_stuck_record(original_image):
    """stuck OriginalImage を再処理可能な状態にクリーンアップして pending に差し戻す。

    [性質] 副作用あり（DB 書き込み・ファイル削除）
    [入力] original_image: OriginalImage インスタンス（status=processing が期待値）
    [出力] None（例外は外に漏らさない）
    [方針] 全操作を transaction.atomic() で保護する。
           途中失敗は logger.error に記録して処理続行（次回 sweeper で再試行される）。
    """
    try:
        with transaction.atomic():
            # atomic 内で status を再確認（他 worker が先に完了済みなら何もしない）
            refreshed = OriginalImage.objects.get(id=original_image.id)
            if refreshed.status != OriginalImage.STATUS_PROCESSING:
                logger.info(
                    "stuck cleanup skipped (status=%s): OriginalImage %s",
                    refreshed.status,
                    original_image.id,
                )
                return

            # Person 削除候補の person_id を収集しながら BusinessCard を削除
            person_ids_to_check = set()
            for bc in BusinessCard.objects.filter(original_image=original_image):
                # Contact から person_id を収集（BusinessCard 削除前に取得）
                for c in Contact.objects.filter(business_card=bc):
                    if c.person_id is not None:
                        person_ids_to_check.add(c.person_id)
                # card_image ファイルを削除（FileNotFoundError は無視）
                if bc.card_image:
                    try:
                        os.unlink(bc.card_image.path)
                    except FileNotFoundError:
                        pass
                    except OSError as e:
                        logger.warning(
                            "stuck cleanup: file unlink failed %s: %s",
                            bc.card_image.path, e,
                        )
                # BusinessCard 削除（CASCADE で Contact / ContactFieldConfidence も削除）
                bc.delete()

            # 孤立 Person を削除（BusinessCard 削除後にチェック）
            deleted_persons = 0
            for person_id in person_ids_to_check:
                if not Contact.objects.filter(person_id=person_id).exists():
                    Person.objects.filter(id=person_id).delete()
                    deleted_persons += 1
            if deleted_persons > 0:
                logger.info("orphan Person deleted: %d records", deleted_persons)

            # OriginalImage をクリーンな pending 状態にリセット
            original_image.raw_json = None
            original_image.error_message = ""
            original_image.status = OriginalImage.STATUS_PENDING
            original_image.claimed_at = None
            original_image.detected_count = 0
            original_image.save(
                update_fields=[
                    "status", "claimed_at", "raw_json",
                    "error_message", "detected_count", "updated_at",
                ]
            )
    except Exception as e:
        logger.error(
            "stuck cleanup failed for OriginalImage %s: %s",
            original_image.id,
            e,
        )


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
