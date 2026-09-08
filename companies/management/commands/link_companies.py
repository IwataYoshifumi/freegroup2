"""既存 Contact レコードに対して Company への自動リンクおよび重複会社候補の生成を一括適用する管理コマンド（仕様書 §6.5.4）。"""

import logging

from django.core.management.base import BaseCommand

from companies.models import Company, CompanyDuplicateCandidate
from companies.services import archive_company_if_orphaned, link_contact_to_company
from contacts.models import Contact
from persons.models import Person

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "既存 Contact レコードに対して Company への自動リンクおよび重複候補生成を一括適用する。"
        "デフォルトでは company 未紐付けの Contact のみを対象とし、--all で全有効 Contact を再照合する。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            default=False,
            help="既に company が紐づいている Contact も含め、有効な全 Contact を再照合する（デフォルトは未紐付けのみ）。",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="処理件数の上限（デフォルト指定なし/全件）。",
        )

    def handle(self, *args, **options):
        is_all = options.get("all", False)
        limit = options.get("limit")

        # 1. 対象 Contact の抽出
        qs = Contact.objects.exclude(organization="").exclude(organization__isnull=True)
        if any(f.name == "is_archived" for f in Contact._meta.fields):
            qs = qs.filter(is_archived=False)
        else:
            qs = qs.exclude(person__status=Person.Status.ARCHIVED)

        if not is_all:
            qs = qs.filter(company__isnull=True)

        qs = qs.order_by("created_at")

        if limit is not None and limit > 0:
            contacts = list(qs[:limit])
        else:
            contacts = list(qs)

        total = len(contacts)

        self.stdout.write(self.style.NOTICE(f"会社リンク処理を開始します（対象件数: {total} 件, --all={is_all}, --limit={limit}）"))

        companies_count_before = Company.objects.count()
        candidates_count_before = CompanyDuplicateCandidate.objects.count()

        newly_linked_count = 0
        error_count = 0

        for i, contact in enumerate(contacts, 1):
            was_null = contact.company_id is None
            self.stdout.write(f"[{i}/{total}] 会社リンク中: Contact {contact.id} ({contact.organization})...")

            try:
                old_company = contact.company
                company = link_contact_to_company(contact)
                if was_null and company is not None:
                    newly_linked_count += 1
                if is_all and old_company and company and old_company != company:
                    archive_company_if_orphaned(old_company)
            except Exception as e:
                error_count += 1
                logger.exception("Contact %s の会社リンク中にエラーが発生しました: %s", contact.id, e)
                self.stderr.write(
                    self.style.ERROR(f"[{i}/{total}] Contact {contact.id} でエラー発生: {e}")
                )

        companies_count_after = Company.objects.count()
        candidates_count_after = CompanyDuplicateCandidate.objects.count()

        new_companies_created = max(0, companies_count_after - companies_count_before)
        new_candidates_created = max(0, candidates_count_after - candidates_count_before)

        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("会社リンク処理が完了しました。"))
        self.stdout.write(f"  処理対象件数: {total} 件")
        self.stdout.write(f"  新規リンク成功数: {newly_linked_count} 件")
        self.stdout.write(f"  新規会社作成数: {new_companies_created} 件")
        self.stdout.write(f"  重複候補（CompanyDuplicateCandidate）生成数: {new_candidates_created} 件")
        self.stdout.write(f"  エラー件数: {error_count} 件")
        self.stdout.write(self.style.SUCCESS("=" * 60))
