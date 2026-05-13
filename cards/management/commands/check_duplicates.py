"""重複チェックを cron で実行する管理コマンド（仕様書 §12.1 / v0.1.5 §4.2）。

cron で 5 分間隔起動を想定。`Run_Generate_Duplicate_Candidates(limit)` を呼ぶだけの
オーケストレータ。実処理は duplicates/tasks/duplicate_check_runner.py 側に集約。

crontab 例：
    */5 * * * * cd /path/to/project && python manage.py check_duplicates
"""

import logging

from django.core.management.base import BaseCommand

from duplicates.tasks.duplicate_check_runner import (
    Run_Generate_Duplicate_Candidates,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "重複チェック（DuplicateCandidate 生成）を実行する。"
        "cron 起動を想定。--limit で 1 回の処理上限を指定可能（デフォルト 100）。"
    )

    DEFAULT_LIMIT = 100

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=self.DEFAULT_LIMIT,
            help=(
                f"1 回の起動で処理する最大件数（デフォルト: {self.DEFAULT_LIMIT}）。"
                "duplicate_checked_at が NULL の primary Contact のうち先頭から limit 件を処理する。"
            ),
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（複合処理：DB 書き込み + ActionLog 書き込み）

        [入力] --limit: 最大処理件数
        [出力] None（処理結果は ActionLog に集約され、ログには contact_id 別の失敗情報のみ）
        """
        limit = max(1, options["limit"])
        Run_Generate_Duplicate_Candidates(limit=limit)
