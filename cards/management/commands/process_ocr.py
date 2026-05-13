"""ocr_status=pending の BusinessCard を OCR 処理する（v1.5.0 / cron B）。

cron で 1〜5 分間隔で起動される想定。
多重起動対策に CAS 方式（BC 単位の filter+update で ocr_status=pending → processing）を採用。
1 回の起動で最大 N 件処理（暫定 N=10）。

OcrService は Command.handle で 1 インスタンスだけ生成し、各 BC ループに渡す
（cron 1 起動 = 1 インスタンス使い回しでプロンプト/スキーマのファイル I/O を節約）。
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cards.models import BusinessCard
from cards.tasks.ocr_pipeline import process_cardimage_with_ocr
from cards.tasks.ocr_recovery import reset_bc_to_pending
from cards.tasks.ocr_service import OcrService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "ocr_status=pending の BusinessCard を OCR 処理する。"
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
        parser.add_argument(
            "--id",
            type=str,
            default=None,
            help=(
                "対象 BusinessCard を UUID で 1 件指定する。"
                "ocr_status=pending の条件は維持される（--limit は無視）。"
            ),
        )

    def handle(self, *args, **options):
        # ── stuck sweeper（BC 単位） ───────────────────────────────
        threshold_minutes = getattr(settings, "OCR_STUCK_THRESHOLD_MINUTES", 30)
        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)
        stuck_bcs = list(
            BusinessCard.objects.filter(
                ocr_status=BusinessCard.OcrStatus.PROCESSING,
                claimed_at__lt=cutoff,
            )
        )
        if stuck_bcs:
            cleaned = 0
            for bc in stuck_bcs:
                if reset_bc_to_pending(
                    bc, expected_statuses=(BusinessCard.OcrStatus.PROCESSING,)
                ):
                    cleaned += 1
            logger.warning(
                "process_ocr: stuck BC %d 件をクリーンアップし pending に戻しました"
                "（しきい値 %d 分、対象 %d 件）",
                cleaned, threshold_minutes, len(stuck_bcs),
            )
        # ─────────────────────────────────────────────────────────

        target_ids = self._collect_target_ids(options)
        if not target_ids:
            self.stdout.write("process_ocr: 対象なし")
            return

        self.stdout.write(f"process_ocr: {len(target_ids)} 件を試行")

        # OcrService は 1 起動 = 1 インスタンス（プロンプト/スキーマキャッシュ共有）
        ocr_service = OcrService()

        processed = 0
        skipped = 0
        for bc_id in target_ids:
            if not _claim_lock(bc_id):
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (他 worker が取得済み / status drift): {bc_id}"
                ))
                continue

            try:
                bc = BusinessCard.objects.get(id=bc_id)
            except BusinessCard.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"  消失: {bc_id}"))
                continue

            try:
                process_cardimage_with_ocr(bc, ocr_service)
            except Exception as e:
                # process_cardimage_with_ocr は基本的に例外を漏らさない設計だが防御的に
                self.stderr.write(self.style.ERROR(
                    f"  pipeline 例外 {bc_id}: {type(e).__name__}: {e}"
                ))
                processed += 1
                continue

            bc.refresh_from_db()
            self.stdout.write(f"  done {bc_id}: ocr_status={bc.ocr_status}")
            processed += 1

        self.stdout.write(self.style.SUCCESS(
            f"process_ocr: targets={len(target_ids)}, processed={processed}, "
            f"skipped={skipped}"
        ))

    def _collect_target_ids(self, options):
        """[性質] 準関数（DB 読み取り）/ 対象 BC.id のリストを返す。

        並び順: original_image__created_at 昇順 → 同一 OriginalImage 内では card_index 昇順。
        --id 指定時は status=pending 維持の 1 件のみ（--limit 無視）。
        """
        if options["id"]:
            try:
                target_id = uuid.UUID(options["id"])
            except (ValueError, TypeError):
                raise CommandError(
                    f"--id の値が UUID 形式ではありません: {options['id']}"
                )
            exists = BusinessCard.objects.filter(
                id=target_id, ocr_status=BusinessCard.OcrStatus.PENDING
            ).exists()
            if not exists:
                self.stdout.write(self.style.WARNING(
                    f"  指定 id は ocr_status=pending ではない / 存在しない: {target_id}"
                ))
                return []
            return [target_id]

        limit = max(1, options["limit"])
        return list(
            BusinessCard.objects.filter(ocr_status=BusinessCard.OcrStatus.PENDING)
            .order_by("original_image__created_at", "card_index")
            .values_list("id", flat=True)[:limit]
        )


def _claim_lock(business_card_id):
    """[性質] 副作用あり（DB 書込）/ CAS で BC を processing に遷移させ排他を取得する。"""
    updated = BusinessCard.objects.filter(
        id=business_card_id,
        ocr_status=BusinessCard.OcrStatus.PENDING,
    ).update(
        ocr_status=BusinessCard.OcrStatus.PROCESSING,
        claimed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return updated == 1
