import io
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import IntegrityError, models
from django.test import TestCase
from django.urls import reverse

from companies.admin import CompanyAdmin, CompanyDuplicateCandidateAdmin
from companies.models import Company, CompanyDuplicateCandidate
from companies.services import link_contact_to_company
from contacts.models import Contact
from persons.models import Person

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
        # ドメイン一致は加点されず、会社名単独一致（100点）の場合は同名別会社誤結合防止のため候補外（""）
        self.assertEqual(score, 100)
        self.assertEqual(rank, "")

        # 追加の裏付け（電話等）がある場合は候補となる
        score_phone, rank_phone = calculate_company_score(
            "田中商店", "gmail.com", "03-1234-5678", "", "",
            "田中商店", "gmail.com", "03-1234-5678", "", "",
        )
        self.assertEqual(score_phone, 120)  # 100 + 20
        self.assertEqual(rank_phone, CompanyDuplicateCandidate.Rank.POSSIBLE_LOW)

    def test_mark_as_different_company_records_action_log(self):
        from actionlogs.models import ActionLog
        from companies.services import mark_as_different_company, register_company_candidate

        comp_a = Company.objects.create(organization="別会社テストA", domain="diff-a.example.jp")
        comp_b = Company.objects.create(organization="別会社テストB", domain="diff-b.example.jp")
        cand = CompanyDuplicateCandidate.objects.create(
            company_a=comp_a,
            company_b=comp_b,
            score=140,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_MID,
        )

        resolved = mark_as_different_company(cand.id, self.user, note="別法人であることを確認")
        self.assertEqual(resolved.review_status, CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY)
        self.assertEqual(resolved.reviewed_by, self.user)
        self.assertIsNotNone(resolved.reviewed_at)

        # ActionLog の記録検証（仕様書 §6.5.5.1）
        log = ActionLog.objects.filter(
            action="company_different_company",
            user=self.user,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.data.get("company_a_id"), str(comp_a.id))
        self.assertEqual(log.data.get("company_b_id"), str(comp_b.id))
        self.assertEqual(log.note, "別法人であることを確認")

    def test_link_contact_to_company_exact_match(self):
        from companies.services import link_contact_to_company
        from contacts.models import Contact
        from persons.models import Person

        existing_comp = Company.objects.create(
            organization="株式会社テスト商事",
            domain="test-shoji.example.com",
            phone="03-1234-5678",
        )
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            full_name="山田太郎",
            organization="㈱テスト商事",
            org_domain_name="test-shoji.example.com",
            org_phone="03-1234-5678",
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

        # 2つの同名・同ドメイン・同電話 Company が存在（古い方が代表、exact_match 220点）
        comp_a = Company.objects.create(
            organization="株式会社同名",
            domain="doumei.example.com",
            phone="03-0000-1111",
        )
        comp_b = Company.objects.create(
            organization="株式会社同名",
            domain="doumei.example.com",
            phone="03-0000-1111",
        )

        # 事前に comp_a と comp_b が different_company と判定されている
        candidate = register_company_candidate(comp_a, comp_b)
        mark_as_different_company(candidate.id, self.user)

        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            full_name="鈴木一郎",
            organization="株式会社同名",
            org_domain_name="doumei.example.com",
            org_phone="03-0000-1111",
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


class CompanyViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="comp_user",
            password="password",
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="add_company"),
            Permission.objects.get(codename="change_company"),
            Permission.objects.get(codename="delete_company"),
            Permission.objects.get(codename="merge_company"),
        )
        self.company = Company.objects.create(
            organization="テスト株式会社",
            domain="example.jp",
            phone="03-1111-2222",
            address="東京都千代田区",
            website="https://example.jp",
        )

    def test_company_list_view(self):
        # 未ログインはログインへ
        resp = self.client.get(reverse("companies:company_list"))
        self.assertEqual(resp.status_code, 302)

        self.client.login(username="comp_user", password="password")
        resp = self.client.get(reverse("companies:company_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "テスト株式会社")
        self.assertContains(resp, "有効")  # get_status_display

        # 検索
        resp_search = self.client.get(reverse("companies:company_list"), {"q": "存在しない会社"})
        self.assertEqual(resp_search.status_code, 200)
        self.assertNotContains(resp_search, "テスト株式会社")

    def test_company_detail_view(self):
        self.client.login(username="comp_user", password="password")
        resp = self.client.get(reverse("companies:company_detail", kwargs={"pk": self.company.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "テスト株式会社")
        self.assertContains(resp, "03-1111-2222")

    def test_company_create_and_update_view(self):
        self.client.login(username="comp_user", password="password")
        # 作成
        resp = self.client.post(
            reverse("companies:company_create"),
            {
                "organization": "新規設立株式会社",
                "domain": "newco.jp",
                "phone": "06-9999-8888",
                "address": "大阪府大阪市",
                "website": "https://newco.jp",
            },
        )
        self.assertEqual(resp.status_code, 302)
        new_co = Company.objects.get(organization="新規設立株式会社")
        self.assertEqual(new_co.domain, "newco.jp")

        # 編集
        resp = self.client.post(
            reverse("companies:company_update", kwargs={"pk": new_co.pk}),
            {
                "organization": "新規設立株式会社改",
                "domain": "newco-kai.jp",
                "phone": "06-9999-8888",
                "address": "大阪府大阪市",
                "website": "https://newco-kai.jp",
            },
        )
        self.assertEqual(resp.status_code, 302)
        new_co.refresh_from_db()
        self.assertEqual(new_co.organization, "新規設立株式会社改")

    def test_company_archive_view(self):
        self.client.login(username="comp_user", password="password")
        resp = self.client.post(reverse("companies:company_archive", kwargs={"pk": self.company.pk}))
        self.assertEqual(resp.status_code, 302)
        self.company.refresh_from_db()
        self.assertEqual(self.company.status, Company.Status.ARCHIVED)

    def test_company_duplicate_candidate_list_and_permissions(self):
        # 権限なしユーザー
        no_perm_user = get_user_model().objects.create_user(
            username="no_perm_user", password="password"
        )
        self.client.login(username="no_perm_user", password="password")
        resp = self.client.get(reverse("companies:company_candidate_list"))
        self.assertEqual(resp.status_code, 403)

        # 権限ありユーザー
        self.client.login(username="comp_user", password="password")
        target = Company.objects.create(organization="テスト株式会社類似", domain="example.jp")
        candidate = CompanyDuplicateCandidate.objects.create(
            company_a=self.company,
            company_b=target,
            score=90,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
        )
        resp = self.client.get(reverse("companies:company_candidate_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "テスト株式会社")
        self.assertContains(resp, "テスト株式会社類似")

    def test_company_merge_view_and_mark_different(self):
        self.client.login(username="comp_user", password="password")
        target = Company.objects.create(organization="テスト株式会社ターゲット", domain="target.jp")
        cand = CompanyDuplicateCandidate.objects.create(
            company_a=self.company,
            company_b=target,
            score=85,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
        )

        # 別会社としてマーク
        resp = self.client.post(reverse("companies:company_mark_different", kwargs={"pk": cand.id}))
        self.assertEqual(resp.status_code, 302)
        cand.refresh_from_db()
        self.assertEqual(cand.review_status, CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY)

        # マージテスト用会社と候補作成
        target_merge = Company.objects.create(organization="テストマージ対象株式会社", domain="target-merge.jp")
        cand2 = CompanyDuplicateCandidate.objects.create(
            company_a=self.company,
            company_b=target_merge,
            score=85,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
        )
        resp = self.client.post(
            reverse("companies:company_merge"),
            {
                "surviving_company_id": str(self.company.id),
                "target_company_ids": str(target_merge.id),
            },
        )
        self.assertEqual(resp.status_code, 302)
        target_merge.refresh_from_db()
        self.assertEqual(target_merge.status, Company.Status.MERGED)
        self.assertEqual(target_merge.merged_into, self.company)

    def test_company_duplicate_candidate_grouping_view(self):
        self.client.login(username="comp_user", password="password")
        # 3社を作成し、A-B, B-C の2ペアを登録（1つのグループに集約される）
        comp_b = Company.objects.create(organization="テスト株式会社 分社B", domain="b.example.jp")
        comp_c = Company.objects.create(organization="テスト株式会社 分社C", domain="c.example.jp")
        cand1 = CompanyDuplicateCandidate.objects.create(
            company_a=self.company,
            company_b=comp_b,
            score=120,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
        )
        cand2 = CompanyDuplicateCandidate.objects.create(
            company_a=comp_b,
            company_b=comp_c,
            score=95,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_MID,
        )

        resp = self.client.get(reverse("companies:company_candidate_list"))
        self.assertEqual(resp.status_code, 200)
        # グループ化されたカードが1つ存在し、3社すべてが含まれること
        groups = resp.context["groups"]
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["company_count"], 3)
        self.assertEqual(group["max_score"], 120)
        self.assertEqual(group["rank"], CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH)
        # HTML内に各社情報とラジオボタン、統合対象チェックボックス、統合ボタンが表示されていること
        self.assertContains(resp, "テスト株式会社")
        self.assertContains(resp, "テスト株式会社 分社B")
        self.assertContains(resp, "テスト株式会社 分社C")
        self.assertContains(resp, "チェックした会社を統合")
        self.assertContains(resp, "別会社として判定")
        self.assertContains(resp, f'name="surviving_company_{group["id"]}"')
        self.assertContains(resp, 'name="target_company_ids"')

    def test_group_batch_merge_and_mark_different(self):
        self.client.login(username="comp_user", password="password")
        # 3社のグループを作成
        c1 = Company.objects.create(organization="グループ統合社1", domain="g1.jp")
        c2 = Company.objects.create(organization="グループ統合社2", domain="g2.jp")
        c3 = Company.objects.create(organization="グループ統合社3", domain="g3.jp")
        cand1 = CompanyDuplicateCandidate.objects.create(
            company_a=c1,
            company_b=c2,
            score=100,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
        )
        cand2 = CompanyDuplicateCandidate.objects.create(
            company_a=c2,
            company_b=c3,
            score=90,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
        )

        # 1. バッチ「別会社」判定テスト
        resp_diff = self.client.post(
            reverse("companies:company_mark_different_batch"),
            {"candidate_ids": f"{cand1.id},{cand2.id}"},
        )
        self.assertEqual(resp_diff.status_code, 302)
        cand1.refresh_from_db()
        cand2.refresh_from_db()
        self.assertEqual(cand1.review_status, CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY)
        self.assertEqual(cand2.review_status, CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY)

        # 2. 一括統合テスト（m1をsurvivingとし、m2, m3をマージ）
        m1 = Company.objects.create(organization="マージ元社1", domain="m1.jp")
        m2 = Company.objects.create(organization="マージ元社2", domain="m2.jp")
        m3 = Company.objects.create(organization="マージ元社3", domain="m3.jp")
        mcand1 = CompanyDuplicateCandidate.objects.create(
            company_a=m1, company_b=m2, score=100, rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH
        )
        mcand2 = CompanyDuplicateCandidate.objects.create(
            company_a=m2, company_b=m3, score=100, rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH
        )

        resp_merge = self.client.post(
            reverse("companies:company_merge"),
            {
                "group_id": "grp_test",
                "surviving_company_grp_test": str(m1.id),
                "target_company_ids": [str(m2.id), str(m3.id)],
                "next": reverse("companies:company_candidate_list"),
            },
        )
        self.assertEqual(resp_merge.status_code, 302)
        self.assertRedirects(resp_merge, reverse("companies:company_candidate_list"))
        m2.refresh_from_db()
        m3.refresh_from_db()
        self.assertEqual(m2.status, Company.Status.MERGED)
        self.assertEqual(m2.merged_into, m1)
        self.assertEqual(m3.status, Company.Status.MERGED)
        self.assertEqual(m3.merged_into, m1)
        mcand1.refresh_from_db()
        mcand2.refresh_from_db()
        self.assertEqual(mcand1.review_status, CompanyDuplicateCandidate.ReviewStatus.MERGED)
        self.assertEqual(mcand2.review_status, CompanyDuplicateCandidate.ReviewStatus.MERGED)

    def test_group_selective_merge(self):
        """チェックボックスによる選別マージ（チェックした会社のみ統合、外した会社は残る）を検証。"""
        self.client.login(username="comp_user", password="password")
        s1 = Company.objects.create(organization="選別テスト存続社", domain="s1.example.jp")
        s2 = Company.objects.create(organization="選別テスト統合対象社", domain="s2.example.jp")
        s3 = Company.objects.create(organization="選別テスト除外社", domain="s3.example.jp")
        cand12 = CompanyDuplicateCandidate.objects.create(
            company_a=s1, company_b=s2, score=100, rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH
        )
        cand13 = CompanyDuplicateCandidate.objects.create(
            company_a=s1, company_b=s3, score=100, rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH
        )

        # 1. チェックボックス全解除（HTML仕様により target_company_ids キー自体が POST されない場合）
        resp_unchecked = self.client.post(
            reverse("companies:company_merge"),
            {
                "group_id": "grp_selective",
                "surviving_company_id": str(s1.id),
                # target_company_ids は一切送信されない
            },
            follow=True,
        )
        self.assertEqual(resp_unchecked.status_code, 200)
        messages_unchecked = [m.message for m in resp_unchecked.context["messages"]]
        self.assertIn("統合対象の会社が選択されていません。", messages_unchecked)
        s2.refresh_from_db()
        s3.refresh_from_db()
        self.assertEqual(s2.status, Company.Status.ACTIVE)
        self.assertEqual(s3.status, Company.Status.ACTIVE)

        # 2. 仮に旧 hidden (merge_company_ids) が送られても、新UIからの送信時はフォールバックしないことを検証
        resp_no_fallback = self.client.post(
            reverse("companies:company_merge"),
            {
                "group_id": "grp_selective",
                "surviving_company_id": str(s1.id),
                "merge_company_ids": f"{s1.id},{s2.id},{s3.id}",
                # チェック全解除のため target_company_ids は未送信
            },
            follow=True,
        )
        self.assertEqual(resp_no_fallback.status_code, 200)
        messages_no_fallback = [m.message for m in resp_no_fallback.context["messages"]]
        self.assertIn("統合対象の会社が選択されていません。", messages_no_fallback)
        s2.refresh_from_db()
        s3.refresh_from_db()
        self.assertEqual(s2.status, Company.Status.ACTIVE)
        self.assertEqual(s3.status, Company.Status.ACTIVE)

        # 3. 明示的な空リスト送信時のエラーバリデーション
        resp_empty = self.client.post(
            reverse("companies:company_merge"),
            {
                "surviving_company_id": str(s1.id),
                "target_company_ids": [],
            },
        )
        self.assertEqual(resp_empty.status_code, 302)
        s2.refresh_from_db()
        s3.refresh_from_db()
        self.assertEqual(s2.status, Company.Status.ACTIVE)
        self.assertEqual(s3.status, Company.Status.ACTIVE)

        # 4. s2 のみ選択してマージ実行（s3 はチェックを外して除外）
        resp_selective = self.client.post(
            reverse("companies:company_merge"),
            {
                "surviving_company_id": str(s1.id),
                "target_company_ids": [str(s2.id)],
            },
        )
        self.assertEqual(resp_selective.status_code, 302)
        s2.refresh_from_db()
        s3.refresh_from_db()
        # s2 は s1 に統合される
        self.assertEqual(s2.status, Company.Status.MERGED)
        self.assertEqual(s2.merged_into, s1)
        # s3 は統合されず ACTIVE のまま残る
        self.assertEqual(s3.status, Company.Status.ACTIVE)
        self.assertIsNone(s3.merged_into)


class LinkCompaniesCommandTests(TestCase):
    """link_companies 管理コマンドの検証（仕様書 §6.5.4）。"""

    def setUp(self):
        self.person = Person.objects.create()

    def test_link_companies_unlinked_contact_creates_and_links_company(self):
        contact = Contact.objects.create(
            person=self.person,
            organization="株式会社アルファ自動化",
            org_domain_name="alpha-auto.co.jp",
        )
        self.assertIsNone(contact.company)

        out = io.StringIO()
        call_command("link_companies", stdout=out)
        contact.refresh_from_db()

        self.assertIsNotNone(contact.company)
        self.assertEqual(contact.company.organization, "株式会社アルファ自動化")
        self.assertEqual(contact.company.domain, "alpha-auto.co.jp")
        output_str = out.getvalue()
        self.assertIn("会社リンク処理が完了しました。", output_str)
        self.assertIn("新規リンク成功数: 1 件", output_str)
        self.assertIn("新規会社作成数: 1 件", output_str)

    def test_link_companies_links_to_existing_exact_match(self):
        existing_comp = Company.objects.create(
            organization="株式会社ベータ既存",
            domain="beta-existing.co.jp",
            phone="03-5555-6666",
        )
        contact = Contact.objects.create(
            person=self.person,
            organization="株式会社ベータ既存",
            org_domain_name="beta-existing.co.jp",
            org_phone="03-5555-6666",
        )

        out = io.StringIO()
        call_command("link_companies", stdout=out)
        contact.refresh_from_db()

        self.assertEqual(contact.company, existing_comp)
        self.assertEqual(Company.objects.filter(domain="beta-existing.co.jp").count(), 1)
        output_str = out.getvalue()
        self.assertIn("新規リンク成功数: 1 件", output_str)
        self.assertIn("新規会社作成数: 0 件", output_str)

    def test_link_companies_generates_duplicate_candidate(self):
        existing_comp = Company.objects.create(
            organization="ガンマ工業株式会社",
            domain="gamma-kougyo.co.jp",
            phone="03-9999-1111",
            address="東京都港区赤坂1-2-3",
        )
        # ドメイン違いだが同一名称＋同一電話＋同一住所（POSSIBLE_HIGH 候補）
        contact = Contact.objects.create(
            person=self.person,
            organization="ガンマ工業株式会社",
            org_domain_name="gamma-branch.co.jp",
            org_phone="03-9999-1111",
            address="東京都港区赤坂1-2-3",
        )

        out = io.StringIO()
        call_command("link_companies", stdout=out)
        contact.refresh_from_db()

        self.assertIsNotNone(contact.company)
        self.assertNotEqual(contact.company, existing_comp)

        # 重複候補が登録されたことを検証
        candidate = CompanyDuplicateCandidate.objects.filter(
            models.Q(company_a=existing_comp, company_b=contact.company)
            | models.Q(company_a=contact.company, company_b=existing_comp)
        ).first()
        self.assertIsNotNone(candidate)
        output_str = out.getvalue()
        self.assertIn("重複候補（CompanyDuplicateCandidate）生成数: 1 件", output_str)

    def test_link_companies_limit_and_all_flags(self):
        c1 = Contact.objects.create(person=self.person, organization="会社1", org_domain_name="c1.com")
        c2 = Contact.objects.create(person=self.person, organization="会社2", org_domain_name="c2.com")

        out = io.StringIO()
        call_command("link_companies", limit=1, stdout=out)
        c1.refresh_from_db()
        c2.refresh_from_db()

        # limit=1 により c1 のみリンクされる
        self.assertIsNotNone(c1.company)
        self.assertIsNone(c2.company)

        # --all なしでは既にリンク済みの c1 はスキップされ、c2 のみ処理される
        out2 = io.StringIO()
        call_command("link_companies", stdout=out2)
        c2.refresh_from_db()
        self.assertIsNotNone(c2.company)
        self.assertIn("処理対象件数: 1 件", out2.getvalue())

    def test_link_companies_error_resilience(self):
        c1 = Contact.objects.create(person=self.person, organization="例外テスト1")
        c2 = Contact.objects.create(person=self.person, organization="正常テスト2")

        original_link = link_contact_to_company

        def faulty_link(contact, user=None):
            if contact.id == c1.id:
                raise RuntimeError("擬似障害エラー")
            return original_link(contact, user=user)

        with patch("companies.management.commands.link_companies.link_contact_to_company", side_effect=faulty_link):
            out = io.StringIO()
            err = io.StringIO()
            call_command("link_companies", stdout=out, stderr=err)

        c1.refresh_from_db()
        c2.refresh_from_db()

        self.assertIsNone(c1.company)
        self.assertIsNotNone(c2.company)
        self.assertIn("エラー件数: 1 件", out.getvalue())
        self.assertIn("擬似障害エラー", err.getvalue())



