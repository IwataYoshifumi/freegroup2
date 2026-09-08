from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from companies.admin import CompanyAdmin, CompanyDuplicateCandidateAdmin
from companies.models import Company, CompanyDuplicateCandidate

User = get_user_model()


class CompanyModelTests(TestCase):
    """Company モデルのステータス・マージ・自己参照 FK 検証（仕様書 §6.2）。"""

    def test_company_status_and_merged_into(self):
        parent_company = Company.objects.create(
            organization="Parent Corp",
            domain="parent.example.com",
            status=Company.Status.ACTIVE,
        )
        child_company = Company.objects.create(
            organization="Child Corp",
            domain="child.example.com",
            status=Company.Status.MERGED,
            merged_into=parent_company,
        )
        self.assertEqual(child_company.status, Company.Status.MERGED)
        self.assertEqual(child_company.merged_into, parent_company)
        self.assertIn(child_company, parent_company.merged_companies.all())


class CompanyDuplicateCandidateConstraintTests(TestCase):
    """CompanyDuplicateCandidate の部分ユニーク制約検証（仕様書 §6.5.3）。"""

    def setUp(self):
        self.company_a = Company.objects.create(organization="Company A", domain="comp-a.com")
        self.company_b = Company.objects.create(organization="Company B", domain="comp-b.com")

    def test_pending_pair_unique_constraint(self):
        CompanyDuplicateCandidate.objects.create(
            company_a=self.company_a,
            company_b=self.company_b,
            score=150,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )
        with self.assertRaises(IntegrityError):
            CompanyDuplicateCandidate.objects.create(
                company_a=self.company_a,
                company_b=self.company_b,
                score=160,
                rank=CompanyDuplicateCandidate.Rank.EXACT_MATCH,
                review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
            )

    def test_resolved_pair_can_coexist(self):
        # 1件目が different_company や merged の場合、再度 pending で作成可能
        CompanyDuplicateCandidate.objects.create(
            company_a=self.company_a,
            company_b=self.company_b,
            score=100,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_MID,
            review_status=CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY,
        )
        candidate2 = CompanyDuplicateCandidate.objects.create(
            company_a=self.company_a,
            company_b=self.company_b,
            score=150,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )
        self.assertIsNotNone(candidate2.pk)


class CompanyAdminTests(TestCase):
    """CompanyAdmin / CompanyDuplicateCandidateAdmin の保護制御検証（仕様書 §7.6.1）。"""

    def setUp(self):
        self.site = AdminSite()
        self.company_admin = CompanyAdmin(Company, self.site)
        self.candidate_admin = CompanyDuplicateCandidateAdmin(CompanyDuplicateCandidate, self.site)
        self.company = Company.objects.create(organization="Admin Test Company")

    def test_company_admin_readonly_fields_new_object(self):
        readonly = self.company_admin.get_readonly_fields(request=None, obj=None)
        self.assertEqual(readonly, [])

    def test_company_admin_readonly_fields_existing_object(self):
        readonly = self.company_admin.get_readonly_fields(request=None, obj=self.company)
        self.assertIn("status", readonly)
        self.assertIn("merged_into", readonly)

    def test_company_admin_autocomplete_fields(self):
        self.assertIn("created_by", self.company_admin.autocomplete_fields)
        self.assertIn("merged_into", self.company_admin.autocomplete_fields)

    def test_company_duplicate_candidate_admin_has_add_permission_false(self):
        self.assertFalse(self.candidate_admin.has_add_permission(request=None))

    def test_company_duplicate_candidate_admin_readonly_fields(self):
        expected_fields = [
            "company_a",
            "company_b",
            "score",
            "rank",
            "reviewed_by",
            "reviewed_at",
            "created_at",
        ]
        for field in expected_fields:
            self.assertIn(field, self.candidate_admin.readonly_fields)


class CompanyServiceTests(TestCase):
    """companies.services の各ロジック検証（仕様書 §6.5, §6.6）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="comp_service_user", password="password")

    def test_calculate_company_score_exact_match(self):
        from companies.services import calculate_company_score
        score, rank = calculate_company_score(
            "株式会社ネットワーク東海", "network-tokai.co.jp", "03-1234-5678", "東京都千代田区1-1", "https://network-tokai.co.jp",
            "㈱ネットワーク東海", "network-tokai.co.jp", "03-1234-5678", "東京都千代田区1-1", "https://network-tokai.co.jp/",
        )
        self.assertEqual(rank, CompanyDuplicateCandidate.Rank.EXACT_MATCH)
        self.assertGreaterEqual(score, 220)

    def test_calculate_company_score_generic_email_domain_excluded(self):
        from companies.services import calculate_company_score
        score, rank = calculate_company_score(
            "田中商店", "gmail.com", "", "", "",
            "田中商店", "gmail.com", "", "", "",
        )
        # ドメイン一致は加点されず、会社名のみ100点 -> possible_mid
        self.assertEqual(score, 100)
        self.assertEqual(rank, CompanyDuplicateCandidate.Rank.POSSIBLE_MID)

    def test_link_contact_to_company_exact_match(self):
        from companies.services import link_contact_to_company
        from contacts.models import Contact
        from persons.models import Person

        existing_comp = Company.objects.create(
            organization="株式会社テスト商事",
            domain="test-shoji.example.com",
        )
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            full_name="山田太郎",
            organization="㈱テスト商事",
            org_domain_name="test-shoji.example.com",
        )
        linked = link_contact_to_company(contact, user=self.user)
        self.assertEqual(linked, existing_comp)
        contact.refresh_from_db()
        self.assertEqual(contact.company, existing_comp)

    def test_link_contact_to_company_creates_new(self):
        from companies.services import link_contact_to_company
        from contacts.models import Contact
        from persons.models import Person

        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            full_name="佐藤花子",
            organization="新規株式会社ABC",
            org_domain_name="abc-new.example.com",
            address="東京都港区六本木1-2-3",
        )
        new_comp = link_contact_to_company(contact, user=self.user)
        self.assertIsNotNone(new_comp)
        self.assertEqual(new_comp.organization, "新規株式会社ABC")
        self.assertEqual(new_comp.domain, "abc-new.example.com")
        self.assertEqual(new_comp.address, "東京都港区六本木1-2-3")
        contact.refresh_from_db()
        self.assertEqual(contact.company, new_comp)

    def test_link_contact_to_company_different_company_excluded_from_pairing(self):
        from companies.services import (
            link_contact_to_company,
            mark_as_different_company,
            register_company_candidate,
        )
        from contacts.models import Contact
        from persons.models import Person

        # 2つの同名・同ドメイン Company が存在（古い方が代表）
        comp_a = Company.objects.create(organization="株式会社同名", domain="doumei.example.com")
        comp_b = Company.objects.create(organization="株式会社同名", domain="doumei.example.com")

        # 事前に comp_a と comp_b が different_company と判定されている
        candidate = register_company_candidate(comp_a, comp_b)
        mark_as_different_company(candidate.id, self.user)

        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            full_name="鈴木一郎",
            organization="株式会社同名",
            org_domain_name="doumei.example.com",
        )
        linked = link_contact_to_company(contact, user=self.user)
        self.assertEqual(linked, comp_a)

        # pending のペアは再生成されていないこと
        self.assertFalse(
            CompanyDuplicateCandidate.objects.filter(
                company_a=comp_a,
                company_b=comp_b,
                review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
            ).exists()
        )

    def test_execute_company_merge(self):
        from actionlogs.models import ActionLog
        from companies.services import execute_company_merge
        from contacts.models import Contact
        from deals.models import Deal
        from persons.models import Person

        surviving = Company.objects.create(organization="存続商事", domain="")
        target1 = Company.objects.create(organization="消滅商事1", domain="target1.example.com")
        target2 = Company.objects.create(organization="消滅商事2", domain="target2.example.com")
        other = Company.objects.create(organization="第三者商事")

        # 候補ペアの準備
        cand_internal = CompanyDuplicateCandidate.objects.create(
            company_a=surviving, company_b=target1, score=150, rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH
        )
        cand_target_pair = CompanyDuplicateCandidate.objects.create(
            company_a=target1, company_b=target2, score=150, rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH
        )
        cand_external = CompanyDuplicateCandidate.objects.create(
            company_a=target1, company_b=other, score=100, rank=CompanyDuplicateCandidate.Rank.POSSIBLE_MID
        )

        person = Person.objects.create()
        contact = Contact.objects.create(person=person, full_name="担当者", company=target1)
        deal = Deal.objects.create(name="案件1", company=target2, primary_person=person, owner=self.user)

        execute_company_merge(surviving, [target1, target2], user=self.user)

        contact.refresh_from_db()
        deal.refresh_from_db()
        self.assertEqual(contact.company, surviving)
        self.assertEqual(deal.company, surviving)

        target1.refresh_from_db()
        target2.refresh_from_db()
        self.assertEqual(target1.status, Company.Status.MERGED)
        self.assertEqual(target1.merged_into, surviving)
        self.assertEqual(target2.status, Company.Status.MERGED)
        self.assertEqual(target2.merged_into, surviving)

        # surviving の domain が引き継がれていること
        surviving.refresh_from_db()
        self.assertEqual(surviving.domain, "target1.example.com")

        # レビューキューの後始末
        cand_internal.refresh_from_db()
        cand_target_pair.refresh_from_db()
        cand_external.refresh_from_db()
        self.assertEqual(cand_internal.review_status, CompanyDuplicateCandidate.ReviewStatus.MERGED)
        self.assertEqual(cand_target_pair.review_status, CompanyDuplicateCandidate.ReviewStatus.MERGED)
        self.assertEqual(cand_external.review_status, CompanyDuplicateCandidate.ReviewStatus.INVALIDATED)

        # ActionLog の記録
        logs = ActionLog.objects.filter(action="company_merged")
        self.assertEqual(logs.count(), 2)

    def test_archive_company_if_orphaned(self):
        from companies.services import archive_company_if_orphaned
        from contacts.models import Contact
        from persons.models import Person

        comp = Company.objects.create(organization="孤児候補商事")
        person = Person.objects.create()
        contact = Contact.objects.create(person=person, full_name="連絡先", company=comp)

        # Contact があるときはアーカイブされない
        archived = archive_company_if_orphaned(comp)
        self.assertFalse(archived)
        comp.refresh_from_db()
        self.assertEqual(comp.status, Company.Status.ACTIVE)

        # Contact を別へ移動して孤児にする
        contact.company = None
        contact.save()

        archived = archive_company_if_orphaned(comp)
        self.assertTrue(archived)
        comp.refresh_from_db()
        self.assertEqual(comp.status, Company.Status.ARCHIVED)


