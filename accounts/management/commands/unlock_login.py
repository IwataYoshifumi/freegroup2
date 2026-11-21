"""管理コマンド: 指定ユーザーのログインロックを手動解除する（仕様書 v1.3.1 §5.1 / §12.2）。"""

from django.core.management.base import BaseCommand, CommandError

from accounts.login_security.operations import unlock_account_by_admin
from accounts.management._helpers import find_user_exact_or_raise


class Command(BaseCommand):
    help = "指定ユーザーのログインロックと連続失敗回数を解除する。"

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            help="ロック解除対象のユーザー名（完全一致）。",
        )

    def handle(self, *args, **options):
        username = options["username"]
        target_user = find_user_exact_or_raise(username)

        result = unlock_account_by_admin(
            None,
            target_user,
            as_command=True,
        )
        if not result.is_success:
            raise CommandError(
                f"ログインロックの解除に失敗しました（状態: {result.status.value}）。"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"ユーザー '{target_user.username}' のログインロックを解除しました。"
            )
        )
