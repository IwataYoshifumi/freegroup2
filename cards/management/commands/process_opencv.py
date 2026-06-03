"""status=pending の OriginalImage を OpenCV 検出＋OSD 正立化のみ処理する（第1段・OCR/課金なし）。

第1段（process_opencv）：検出 → クロップ → OSD 正立化 → card_image に正立後画像を格納 →
BC 作成 → osd を raw_json["cards"][*]["osd"] に記録。run_ocr（Claude API）は呼ばないので課金ゼロ。
OCR 課金の手前で、OSD 正立化結果を名刺詳細画面で目視確認するための経路。

cron で多重起動されても重複しないよう、process_pending と同じ CAS 方式（_claim_lock）で排他を取る。
1 回の起動で最大 N 件処理（暫定 N=10）。

※第2段（OCR・課金）は別経路（main の process_ocr 系）で行う。本コマンドでは実行しない。
※status は EXTRACTED に遷移する（このブランチでは「OpenCV＋OSD 完了・未OCR」の意味で使う）。
"""

import logging

from django.core.management.base import BaseCommand

from cards.management.commands.process_pending import _claim_lock
from cards.models import OriginalImage
from cards.tasks.pipeline_coordinator import PipelineCoordinator

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "status=pending の OriginalImage を OpenCV 検出＋OSD 正立化のみ処理する"
        "（OCR/課金なし・第1段）。CAS で多重起動を防ぐ。"
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
            help="対象 OriginalImage を UUID で 1 件指定する（status=pending のものに限る・--limit は無視）。",
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（複合処理: DB 書き込み・ファイル書き込み・外部プロセス OSD）。

        run_ocr は呼ばない（課金ゼロ）。各対象を CAS で processing に遷移させ run_opencv_stage を実行する。
        """
        limit = max(1, options["limit"])
        target_id = options["id"]

        if target_id:
            target_ids = [target_id]
        else:
            target_ids = list(
                OriginalImage.objects.filter(status=OriginalImage.STATUS_PENDING)
                .order_by("created_at")
                .values_list("id", flat=True)[:limit]
            )

        if not target_ids:
            self.stdout.write("process_opencv: pending なし")
            return

        self.stdout.write(f"process_opencv: {len(target_ids)} 件を試行（OCR なし・課金ゼロ）")

        processed = 0
        skipped = 0
        for tid in target_ids:
            if not _claim_lock(tid):
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (pending でない / 他 worker 取得済み): {tid}"
                ))
                continue

            try:
                original = OriginalImage.objects.get(id=tid)
            except OriginalImage.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"  消失: {tid}"))
                continue

            if original.status != OriginalImage.STATUS_PROCESSING:
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (status drift: {original.status}): {tid}"
                ))
                continue

            try:
                PipelineCoordinator(original).run_opencv_stage()
            except Exception as e:
                # run_opencv_stage は基本的に例外を漏らさないが防御的に捕捉
                self.stderr.write(self.style.ERROR(
                    f"  opencv 例外 {tid}: {type(e).__name__}: {e}"
                ))
                processed += 1
                continue

            original.refresh_from_db()
            self.stdout.write(
                f"  done {tid}: status={original.status}, detected={original.detected_count}"
            )
            processed += 1

        self.stdout.write(self.style.SUCCESS(
            f"process_opencv: targets={len(target_ids)}, processed={processed}, skipped={skipped}"
        ))
