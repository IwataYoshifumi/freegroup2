"""全 AccessList のキャッシュ（AccessListUserRole）を強制再構築する自己修復管理コマンド (§6.3, §7.8)。"""

from django.core.management.base import BaseCommand
from permissions.models import AccessList
from permissions.services import AccessListService


class Command(BaseCommand):
    help = "システム内の全 AccessList のキャッシュ (AccessListUserRole) を完全再構築します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="実際の再計算を行わず、対象件数のみを表示します。",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        access_lists = AccessList.objects.all().order_by("name")
        total = access_lists.count()

        self.stdout.write(f"AccessList 再構築処理を開始します (対象: {total} 件)")

        if dry_run:
            self.stdout.write(self.style.WARNING("ドライランモード: 再計算は実行されませんでした。"))
            return

        success_count = 0
        error_count = 0

        for acl in access_lists:
            try:
                AccessListService.rebuild_for_access_list(acl)
                success_count += 1
                self.stdout.write(f"  [OK] AccessList: {acl.name} ({acl.id})")
            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  [ERROR] AccessList: {acl.name} ({acl.id}) - {e}")
                )

        self.stdout.write("=" * 60)
        self.stdout.write(
            f"再構築完了: 合計 {total} 件, 成功 {success_count} 件, エラー {error_count} 件"
        )
        if error_count > 0:
            self.stdout.write(self.style.WARNING("一部の AccessList でエラーが発生しました。"))
        else:
            self.stdout.write(self.style.SUCCESS("すべての AccessList の再構築が正常に完了しました。"))
