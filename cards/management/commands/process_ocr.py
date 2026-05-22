"""ocr_status=pending の BusinessCard を OCR 処理する管理コマンド（v1.6.0 / cron B）。

cron で 1〜5 分間隔で起動される想定。実体は cards/tasks/ocr_pipeline.py の
Run_Process_CardImages_With_OCR が担う（仕様書 v1.6.0 本編 §13.4.1）。
本コマンドは引数解析と Run_*_With_OCR 呼び出しのみの薄いラッパー。
"""

from django.core.management.base import BaseCommand, CommandError

from cards.tasks.ocr_pipeline import Run_Process_CardImages_With_OCR


class Command(BaseCommand):
    help = (
        "ocr_status=pending の BusinessCard を OCR 処理する。"
        "cron 起動を想定し、CAS で多重起動を防ぐ。実体は "
        "cards.tasks.ocr_pipeline.Run_Process_CardImages_With_OCR。"
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
        try:
            Run_Process_CardImages_With_OCR(
                limit=options["limit"],
                target_id=options["id"],
            )
        except ValueError as exc:
            # target_id の UUID パース失敗
            raise CommandError(str(exc)) from exc
