"""status=pending の OriginalImage を OpenCV 切り出しのみ処理する（v1.5.0 / cron A）。

cron で 1〜5 分間隔で起動される想定。
多重起動対策に CAS 方式（filter+update で status を opencv_processing に遷移させて競合を排除）。
1 回の起動で最大 N 件処理（暫定 N=10）。

OCR は別 cron（process_ocr）で実行する。
"""

import logging
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from cards.models import BusinessCard, OriginalImage
from cards.tasks.crop_cards import Run_Crop_Cards_From_OriginalImage

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "status=pending かつ BC 0 件の OriginalImage を OpenCV 切り出し処理する。"
        "cron 起動を想定し、CAS で多重起動を防ぐ。OCR は呼ばない。"
    )

    DEFAULT_LIMIT = 10

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=self.DEFAULT_LIMIT,
            help=f"1 回の起動で処理する最大件数（デフォルト: {self.DEFAULT_LIMIT}）",
        )
        parser.add_argument(
            "--id",
            type=str,
            default=None,
            help=(
                "対象 OriginalImage を UUID で 1 件指定する。"
                "status=pending かつ BC 0 件 の条件は維持される（--limit は無視）。"
            ),
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（複合処理: DB 書き込み・ファイル書き込み）

        [入力]
          --limit: 最大処理件数
          --id: 対象 OriginalImage の UUID（status=pending かつ BC 0 件 条件は維持）
        [出力] None（処理結果は stdout に出力、status は OriginalImage に保存）
        """
        # ── stuck sweeper ────────────────────────────────────────────────
        threshold_minutes = getattr(settings, "OCR_STUCK_THRESHOLD_MINUTES", 30)
        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)
        stuck_records = list(
            OriginalImage.objects.filter(
                status=OriginalImage.STATUS_OPENCV_PROCESSING,
                claimed_at__lt=cutoff,
            )
        )
        if stuck_records:
            cleaned = 0
            for stuck in stuck_records:
                if _cleanup_stuck_record(stuck):
                    cleaned += 1
            logger.warning(
                "process_opencv: stuck レコード %d 件をクリーンアップし pending に差し戻しました"
                "（しきい値 %d 分、対象 %d 件）",
                cleaned,
                threshold_minutes,
                len(stuck_records),
            )
        # ─────────────────────────────────────────────────────────────────

        target_ids = self._collect_target_ids(options)
        if not target_ids:
            self.stdout.write("process_opencv: 対象なし")
            return

        self.stdout.write(f"process_opencv: {len(target_ids)} 件を試行")

        processed = 0
        skipped = 0
        for target_id in target_ids:
            if BusinessCard.objects.filter(original_image_id=target_id).exists():
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (BC 既存・card_index 不変担保): {target_id}"
                ))
                continue

            if not _claim_lock(target_id):
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (他 worker が取得済み / status drift): {target_id}"
                ))
                continue

            try:
                original = OriginalImage.objects.get(id=target_id)
            except OriginalImage.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"  消失: {target_id}"))
                continue

            if original.status != OriginalImage.STATUS_OPENCV_PROCESSING:
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (status drift: {original.status}): {target_id}"
                ))
                continue

            try:
                Run_Crop_Cards_From_OriginalImage(original).run()
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f"  pipeline 例外 {target_id}: {type(e).__name__}: {e}"
                ))
                processed += 1
                continue

            original.refresh_from_db()
            self.stdout.write(f"  done {target_id}: status={original.status}")
            processed += 1

        self.stdout.write(self.style.SUCCESS(
            f"process_opencv: targets={len(target_ids)}, processed={processed}, "
            f"skipped={skipped}"
        ))

    def _collect_target_ids(self, options):
        """[性質] 準関数（DB 読み取り）/ 対象 OriginalImage.id のリストを返す。

        [入力] options: argparse 結果
        [出力] list[UUID]
        [挙動]
          - --id 指定時: その UUID 1 件（status=pending かつ BC 0 件 の条件は守る、--limit は無視）
          - --id 未指定時: status=pending を created_at 昇順で limit 件
        """
        if options["id"]:
            try:
                target_id = uuid.UUID(options["id"])
            except (ValueError, TypeError):
                raise CommandError(
                    f"--id の値が UUID 形式ではありません: {options['id']}"
                )
            exists = OriginalImage.objects.filter(
                id=target_id, status=OriginalImage.STATUS_PENDING
            ).exists()
            if not exists:
                self.stdout.write(self.style.WARNING(
                    f"  指定 id は status=pending ではない / 存在しない: {target_id}"
                ))
                return []
            return [target_id]

        limit = max(1, options["limit"])
        return list(
            OriginalImage.objects.filter(status=OriginalImage.STATUS_PENDING)
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )


def _cleanup_stuck_record(original_image):
    """stuck OriginalImage を pending に差し戻す（v1.5.0 / cron A 用）。

    [性質] 副作用あり（DB 書き込み）
    [入力] original_image: OriginalImage インスタンス（status=opencv_processing 期待値）
    [出力] bool（差し戻し成功 True / 防御的スキップ or 失敗 False）

    [方針]
      - BC が 1 件以上あれば card_index 不変担保のためスキップしてログ警告
      - リセット項目: status=pending, claimed_at=None, error_message="",
                     detected_count=0, raw_json=None（既存仕様維持）
      - 全操作 transaction.atomic
      - 例外は外に漏らさない（次回 sweeper で再試行）
    """
    try:
        with transaction.atomic():
            refreshed = OriginalImage.objects.get(id=original_image.id)
            if refreshed.status != OriginalImage.STATUS_OPENCV_PROCESSING:
                logger.info(
                    "stuck cleanup skipped (status=%s): OriginalImage %s",
                    refreshed.status,
                    original_image.id,
                )
                return False

            if BusinessCard.objects.filter(original_image=original_image).exists():
                logger.warning(
                    "stuck cleanup skipped (BC 既存): OriginalImage %s "
                    "card_index 不変担保のため自動差し戻しを行いません",
                    original_image.id,
                )
                return False

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
            return True
    except Exception as e:
        logger.error(
            "stuck cleanup failed for OriginalImage %s: %s",
            original_image.id, e,
        )
        return False


def _claim_lock(target_id):
    """[性質] 副作用あり（DB 書き込み）/ CAS で opencv_processing に遷移させ排他を取得する。

    status=pending の行に対して status=opencv_processing・claimed_at=now を一発で書き込む。
    affected_rows が 1 なら自プロセスが排他を取得、0 なら他プロセスが先に取得済み or status drift。
    BC 0 件絞り込みは _collect_target_ids 側で前処理しているため、ここでは status のみ確認する。
    """
    updated = OriginalImage.objects.filter(
        id=target_id, status=OriginalImage.STATUS_PENDING
    ).update(
        status=OriginalImage.STATUS_OPENCV_PROCESSING,
        claimed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return updated == 1
