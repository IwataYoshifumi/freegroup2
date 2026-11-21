"""管理コマンド: 保存期間を超過したログイン関連データを削除する（仕様書 v1.3.1 §5.6 / §6.2 / §9.3 / §12.2）。"""

from django.core.management.base import BaseCommand

from accounts.login_security.purge import purge_old_login_data


class Command(BaseCommand):
    help = (
        "180日超のログイン試行記録、最終ログインから1年超の端末記録、"
        "作成から24時間超の未確定TOTP登録を削除する。"
    )

    def handle(self, *args, **options):
        result = purge_old_login_data()
        self.stdout.write(
            "古いログイン関連データを削除しました: "
            f"ログイン試行記録={result.deleted_attempts}件, "
            f"端末記録={result.deleted_devices}件, "
            f"未確定の認証アプリ登録={result.deleted_unconfirmed_totp}件"
        )
