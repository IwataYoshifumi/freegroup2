"""管理コマンド: 指定ユーザーの二段階認証をリセットする（仕様書 v1.3.1 §6.7 / §6.8 / §12.2）。"""

from django.core.management.base import BaseCommand, CommandError

from accounts.login_security.operations import reset_two_factor_by_admin
from accounts.management._helpers import find_user_exact_or_raise
from accounts.services import is_admin_user


class Command(BaseCommand):
    help = "指定ユーザーの二段階認証（TOTP・リカバリーコード・端末信頼・ロック・セッション）をリセットする。"

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            help="リセット対象のユーザー名（完全一致）。",
        )
        parser.add_argument(
            "--reason",
            default="",
            help="リセット理由のメモ（操作ログに記録）。",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            default=False,
            help="実行前の確認プロンプトを省略する。",
        )

    def handle(self, *args, **options):
        username = options["username"]
        reason_note = options["reason"]
        skip_confirm = options["yes"]

        target_user = find_user_exact_or_raise(username)

        admin_label = "はい" if is_admin_user(target_user) else "いいえ"
        email_label = target_user.email or "（未設定）"
        self.stdout.write("以下のユーザーの二段階認証をリセットします。")
        self.stdout.write(f"  ユーザー名: {target_user.username}")
        self.stdout.write(f"  氏名: {target_user.display_name}")
        self.stdout.write(f"  メールアドレス: {email_label}")
        self.stdout.write(f"  管理者: {admin_label}")

        if not skip_confirm:
            try:
                answer = input("実行してよいですか？ [はい]: ")
            except EOFError:
                answer = ""
            if answer.strip() != "はい":
                raise CommandError("処理を中止しました。")

        result = reset_two_factor_by_admin(
            None,
            target_user,
            reason_note=reason_note,
            as_command=True,
        )
        if not result.is_success:
            raise CommandError(
                f"二段階認証のリセットに失敗しました（状態: {result.status.value}）。"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"ユーザー '{target_user.username}' の二段階認証をリセットしました。"
            )
        )
