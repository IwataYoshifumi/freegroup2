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
        self.assertContains(resp, "company-list-container")
        self.assertContains(resp, "max-width: 1040px")

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
        self.assertContains(resp, "company-detail-container")
        self.assertContains(resp, "max-width: 1040px")

    def test_company_detail_hig_layout_and_actions(self):
        """会社詳細画面のHIG準拠レイアウト（無駄な状態表示の撤去、アイコン化、上部アクション、電話成型）を検証。"""
        # 国番号付き生電話番号の会社を用意
        formatted_co = Company.objects.create(
            organization="フォーマット電話会社",
            phone="81561424300",
            address="愛知県瀬戸市",
        )
        self.client.login(username="comp_user", password="password")
        resp = self.client.get(reverse("companies:company_detail", kwargs={"pk": formatted_co.pk}))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # 1. 正常状態「状態: 有効」の完全撤去
        self.assertNotContains(resp, "状態: 有効")
        self.assertNotContains(resp, "状態:")

        # 2. テキストの「編集」「アーカイブ」ベタ塗りボタンが撤去されていること
        self.assertNotIn("app-btn--success", html)
        self.assertNotIn("app-btn--warning", html)

        # 3. アイコンボタンの存在（鉛筆: bi-pencil-fill, アーカイブ箱: bi-archive-fill）
        self.assertContains(resp, "bi-pencil-fill")
        self.assertContains(resp, "bi-archive-fill")
        self.assertContains(resp, 'title="会社を編集"')
        self.assertContains(resp, 'title="アーカイブ"')

        # 4. 上部アクション導線（新規案件・活動記録）の存在
        self.assertContains(resp, "新規案件")
        self.assertContains(resp, "活動記録")

        # 5. 電話番号の成型フォーマット表示（81561424300 -> 0561-42-4300）
        self.assertContains(resp, "0561-42-4300")
        self.assertNotContains(resp, "81561424300")

    def test_company_update_view(self):
        self.client.login(username="comp_user", password="password")
        target_co = Company.objects.create(
            organization="新規設立株式会社",
            domain="newco.jp",
            phone="06-9999-8888",
            address="大阪府大阪市",
            website="https://newco.jp",
        )
        # 編集
        resp = self.client.post(
            reverse("companies:company_update", kwargs={"pk": target_co.pk}),
            {
                "organization": "新規設立株式会社改",
                "domain": "newco-kai.jp",
                "phone": "06-9999-8888",
                "address": "大阪府大阪市",
                "website": "https://newco-kai.jp",
            },
        )
        self.assertEqual(resp.status_code, 302)
        target_co.refresh_from_db()
        self.assertEqual(target_co.organization, "新規設立株式会社改")

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

    def test_company_candidate_list_ui_matches_and_diffs(self):
        self.client.login(username="comp_user", password="password")
        c1 = Company.objects.create(
            organization="株式会社アルファ",
            domain="alpha.co.jp",
            phone="03-1111-2222",
            address="東京都千代田区1-1-1",
        )
        c2 = Company.objects.create(
            organization="（株）アルファ",
            domain="alpha.co.jp",
            phone="03-9999-8888",
            address="東京都千代田区1-1-1",
        )
        CompanyDuplicateCandidate.objects.create(
            company_a=c1,
            company_b=c2,
            score=95,
            rank=CompanyDuplicateCandidate.Rank.EXACT_MATCH,
        )
        resp = self.client.get(reverse("companies:company_candidate_list"))
        self.assertEqual(resp.status_code, 200)
        # 一致項目ラベルバッジ
        self.assertContains(resp, "社名一致")
        self.assertContains(resp, "ドメイン一致")
        self.assertContains(resp, "住所一致")
        # 電話番号が異なるため差分ハイライトが適用されていること
        self.assertContains(resp, 'style="background-color: #fffbeb;"')
        # アクションボタンのクラス（黄色統一）およびボタン名
        self.assertContains(resp, "app-btn--warning")
        self.assertContains(resp, "マージ・別会社判定を実行")
        self.assertContains(resp, 'name="merge_company_ids"')
        self.assertContains(resp, 'name="different_company_ids"')
        # 判定ランクバッジの信号機カラー（可能性高＝緑）
        self.assertContains(resp, "background-color: #dcfce7;")

        # 未設定セル（値が空）の検証: 差分ハイライトされず薄文字ハイフンが表示されること
        c3 = Company.objects.create(organization="株式会社ガンマ", domain="gamma.co.jp")
        c4 = Company.objects.create(organization="株式会社ガンマ", domain="")
        CompanyDuplicateCandidate.objects.create(
            company_a=c3,
            company_b=c4,
            score=70,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_MID,
        )
        resp2 = self.client.get(reverse("companies:company_candidate_list"))
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, '<span class="app-muted">-</span>')
        # 判定ランクバッジの信号機カラー（可能性中＝黄）
        self.assertContains(resp2, "background-color: #fef9c3;")

    def test_company_merge_view_simultaneous_merge_and_different(self):
        """3社グループで1社存続、1社マージ、1社別会社判定を同時に実行できることの検証。"""
        self.client.login(username="comp_user", password="password")
        surviving = Company.objects.create(organization="存続親会社", domain="main.example.jp")
        to_merge = Company.objects.create(organization="マージ対象会社", domain="merge.example.jp")
        to_diff = Company.objects.create(organization="別会社対象会社", domain="diff.example.jp")

        cand_merge = CompanyDuplicateCandidate.objects.create(
            company_a=surviving,
            company_b=to_merge,
            score=90,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
        )
        cand_diff = CompanyDuplicateCandidate.objects.create(
            company_a=surviving,
            company_b=to_diff,
            score=80,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_MID,
        )

        resp = self.client.post(
            reverse("companies:company_merge"),
            {
                "surviving_company_id": str(surviving.id),
                "merge_company_ids": [str(to_merge.id)],
                "different_company_ids": [str(to_diff.id)],
                "candidate_ids": f"{cand_merge.id},{cand_diff.id}",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1社を統合し、1社を別会社として記録しました。")

        # マージ結果の検証
        to_merge.refresh_from_db()
        self.assertEqual(to_merge.status, Company.Status.MERGED)
        self.assertEqual(to_merge.merged_into, surviving)
        cand_merge.refresh_from_db()
        self.assertEqual(cand_merge.review_status, CompanyDuplicateCandidate.ReviewStatus.MERGED)

        # 別会社結果の検証
        to_diff.refresh_from_db()
        self.assertEqual(to_diff.status, Company.Status.ACTIVE)
        cand_diff.refresh_from_db()
        self.assertEqual(cand_diff.review_status, CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY)
        self.assertEqual(cand_diff.reviewed_by.username, "comp_user")

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
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
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
        self.assertContains(resp, "マージ・別会社判定を実行")
        self.assertContains(resp, f'name="surviving_company_{group["id"]}"')
        self.assertContains(resp, 'name="merge_company_ids"')
        self.assertContains(resp, 'name="different_company_ids"')

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

        # 2. 空リスト送信時のエラーバリデーション
        resp_empty = self.client.post(
            reverse("companies:company_merge"),
            {
                "surviving_company_id": str(s1.id),
                "merge_company_ids": [],
            },
            follow=True,
        )
        self.assertEqual(resp_empty.status_code, 200)
        messages_empty = [m.message for m in resp_empty.context["messages"]]
        self.assertIn("統合対象の会社が選択されていません。", messages_empty)
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


class CompanyListViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.login(username="testuser", password="password")

    def test_company_list_displays_website_url(self):
        from companies.models import Company

        Company.objects.create(
            organization="ウェブサイトあり会社",
            website="https://example.com/corporate",
        )
        Company.objects.create(
            organization="ウェブサイトなし会社",
            website="",
        )

        resp = self.client.get(reverse("companies:company_list"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("https://example.com/corporate", content)
        self.assertNotIn(">リンク</a>", content)
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener noreferrer"', content)
        self.assertNotIn("会社新規作成", content)

    def test_company_create_url_and_button_removed(self):
        """会社新規作成のURLルートが削除され、画面上にもボタンが存在しないこと。"""
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("companies:company_create")

        resp = self.client.get(reverse("companies:company_list"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertNotIn("会社新規作成", content)


class CompanyDetailActionButtonsTests(TestCase):
    """会社詳細画面の「新規案件」「活動記録」ボタンの表示・パラメータ連携を検証。"""

    def setUp(self):
        self.user = User.objects.create_user(username="comp_user", password="password")
        self.client.login(username="comp_user", password="password")
        self.company = Company.objects.create(organization="テスト連携株式会社")

    def test_company_detail_action_buttons_present_with_back_stack(self):
        """company_detail の HTML 内に ?company= および ?back_stack= を含む新規案件・活動記録ボタンが存在すること。"""
        import re

        url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # 1. 新規案件ボタンの存在および app-btn--primary クラス検証（「＋」なし）
        deal_create_base = reverse("deals:deal_create")
        pattern_deal = rf'href="({re.escape(deal_create_base)}\?company={self.company.pk}&amp;back_stack=[^"]+|{re.escape(deal_create_base)}\?company={self.company.pk}&back_stack=[^"]+)"\s+class="app-btn app-btn--primary app-btn--sm">\s*新規案件\s*</a>'
        self.assertRegex(html, pattern_deal)

        # 2. 活動記録ボタンの存在および app-btn--primary クラス検証（「＋」なし）
        act_create_base = reverse("activities:activity_create")
        pattern_act = rf'href="({re.escape(act_create_base)}\?company={self.company.pk}&amp;back_stack=[^"]+|{re.escape(act_create_base)}\?company={self.company.pk}&back_stack=[^"]+)"\s+class="app-btn app-btn--primary app-btn--sm">\s*活動記録\s*</a>'
        self.assertRegex(html, pattern_act)

    def test_company_detail_table_links_have_back_stack(self):
        """会社詳細テーブル内の案件・活動・コンタクトの各詳細リンクに back_stack が付与されていること。"""
        from deals.models import Deal
        from activities.models import Activity, ActivityType
        from contacts.models import Contact
        from persons.models import Person

        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            company=self.company,
            last_name="佐藤",
            first_name="花子",
        )
        deal = Deal.objects.create(
            name="テスト関連案件",
            company=self.company,
            owner=self.user,
        )
        activity = Activity.objects.create(
            deal=deal,
            activity_type=ActivityType.VISIT,
            occurred_at=self.company.created_at,
            user=self.user,
            memo="テスト活動メモ",
        )

        url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # 案件詳細リンク（案件名 & 詳細ボタン）に back_stack が含まれる
        deal_detail_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        self.assertIn(f'href="{deal_detail_url}?back_stack=', html)

        # 活動詳細リンク（詳細ボタン）に back_stack が含まれる
        act_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        self.assertIn(f'href="{act_detail_url}?back_stack=', html)

        # コンタクト詳細リンク（氏名 & 詳細ボタン）に back_stack が含まれる
        contact_detail_url = reverse("contacts:contact_detail", kwargs={"pk": contact.pk})
        self.assertIn(f'href="{contact_detail_url}?back_stack=', html)


class CompanyDetailActivityTitleTests(TestCase):
    """会社詳細画面の活動履歴テーブルにおけるタイトル表示検証。"""

    def setUp(self):
        from activities.models import Activity, ActivityType
        self.user = User.objects.create_user(username="comp_act_user", password="password")
        self.client.login(username="comp_act_user", password="password")
        self.company = Company.objects.create(organization="会社活動会社")
        self.activity = Activity.objects.create(
            title="会社詳細表示用タイトル",
            occurred_at=self.company.created_at,
            activity_type=ActivityType.VISIT,
            user=self.user,
        )
        self.activity.deal = None
        self.activity.save()

    def test_activity_title_displayed_in_company_detail(self):
        """会社詳細の活動履歴テーブルにタイトルが表示され、リンクが含まれていること。"""
        from deals.models import Deal
        # deal に紐づけ、または company の活動として表示
        deal = Deal.objects.create(
            name="会社紐づけ案件",
            company=self.company,
            owner=self.user,
        )
        self.activity.deal = deal
        self.activity.save()

        url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "会社詳細表示用タイトル")
        self.assertContains(response, f"/activities/{self.activity.id}/")


class CompanyDetailContactPhoneTests(TestCase):
    """会社詳細画面の所属コンタクト一覧における電話番号（org_phone / mobile_phone）表示検証。"""

    def setUp(self):
        self.user = User.objects.create_user(username="comp_phone_user", password="password")
        self.client.login(username="comp_phone_user", password="password")
        self.company = Company.objects.create(organization="電話番号テスト会社")
        self.person1 = Person.objects.create()
        self.person2 = Person.objects.create()
        self.person3 = Person.objects.create()

    def test_company_detail_displays_contact_org_phone_formatted(self):
        """所属コンタクトの org_phone が国内形式ハイフン付きで表示されること。"""
        Contact.objects.create(
            person=self.person1,
            company=self.company,
            last_name="山田",
            first_name="太郎",
            org_phone="+81312345678",
        )
        url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "03-1234-5678")

    def test_company_detail_displays_contact_mobile_phone_fallback(self):
        """org_phone が未設定で mobile_phone が設定されている場合、mobile_phone がハイフン付きで表示されること。"""
        Contact.objects.create(
            person=self.person2,
            company=self.company,
            last_name="鈴木",
            first_name="次郎",
            org_phone="",
            mobile_phone="+819012345678",
        )
        url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "090-1234-5678")

    def test_company_detail_displays_contact_without_phone_shows_dash(self):
        """電話番号が未設定の場合、ハイフン '-' が表示されること。"""
        import re

        Contact.objects.create(
            person=self.person3,
            company=self.company,
            last_name="佐藤",
            first_name="三郎",
            title="課長",
            department="営業部",
            email="sato@example.com",
            org_phone="",
            mobile_phone="",
        )
        url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "課長")
        self.assertContains(resp, "営業部")
        # 他列（役職・部署）の default="-" と重複誤判定しないよう、電話番号セルにハイフン「-」が出力されることを検証
        html = resp.content.decode("utf-8")
        pattern = r"<td>課長</td>\s*<td>営業部</td>\s*<td>sato@example\.com</td>\s*<td>\s*-\s*</td>"
        self.assertRegex(html, pattern)


class CompanyDuplicateDomainMatchTests(TestCase):
    """CATV・地域ISP等の汎用ドメインによる重複マッチング防止および候補一覧メッセージ表示テスト。"""

    def setUp(self):
        self.user = User.objects.create_user(username="cand_user", password="password")
        self.client.login(username="cand_user", password="password")

    def test_catv_isp_domain_not_counted_in_domain_match(self):
        """aitai.ne.jp 等の地域ISP/CATVドメイン同士では domain_match が False となり 100点加点されないこと。"""
        from companies.services import calculate_company_match

        # 異なる会社名、同一のCATVドメイン（サブドメイン付き含む）
        result1 = calculate_company_match(
            "ニッコウ電気", "hm7.aitai.ne.jp", "", "", "",
            "東海工務店", "hm7.aitai.ne.jp", "", "", "",
        )
        self.assertFalse(result1.domain_match)
        self.assertEqual(result1.score, 0)
        self.assertEqual(result1.rank, "")

        # キャッチネットワーク（katch.ne.jp）
        result2 = calculate_company_match(
            "三河商事", "katch.ne.jp", "", "", "",
            "安城製作所", "katch.ne.jp", "", "", "",
        )
        self.assertFalse(result2.domain_match)
        self.assertEqual(result2.score, 0)

        # 独自ドメインの場合は domain_match=True（100点加点）
        result3 = calculate_company_match(
            "野場電工", "noba.co.jp", "", "", "",
            "株式会社野場電工", "noba.co.jp", "", "", "",
        )
        self.assertTrue(result3.domain_match)
        self.assertGreaterEqual(result3.score, 100)

    def test_company_candidate_list_messages_rendered_once(self):
        """重複会社候補一覧において、メッセージが二重表示されず1度だけ描画されること。"""
        from django.contrib.auth.models import Permission

        self.user.user_permissions.add(Permission.objects.get(codename="merge_company"))
        self.user = User.objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

        # 統合対象未選択でPOSTし、エラーメッセージを伴って candidate_list にリダイレクト・追従
        resp = self.client.post(reverse("companies:company_merge"), data={}, follow=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # メッセージ本文が二重表示されず、1回だけ出現することを検証
        self.assertIn("存続会社が選択されていません。", html)
        self.assertEqual(html.count("存続会社が選択されていません。"), 1)

    def test_company_candidate_list_shows_website_column_and_url_match_label(self):
        """重複候補一覧にHP列とwebsiteリンクが表示され、URL一致ラベルが正しく付与されること。"""
        from django.contrib.auth.models import Permission
        from companies.models import Company, CompanyDuplicateCandidate

        self.user.user_permissions.add(Permission.objects.get(codename="merge_company"))
        self.user = User.objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

        c1 = Company.objects.create(
            organization="テスト株式会社A",
            domain="test-corp.jp",
            website="https://example.com/corporate",
        )
        c2 = Company.objects.create(
            organization="テスト株式会社A",
            domain="test-corp.jp",
            website="http://www.example.com/corporate/",
        )
        CompanyDuplicateCandidate.objects.create(
            company_a=c1,
            company_b=c2,
            score=200,
            rank=CompanyDuplicateCandidate.Rank.EXACT_MATCH,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )

        resp = self.client.get(reverse("companies:company_candidate_list"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # 1. ヘッダーにHP列が存在すること
        self.assertIn("<th>HP</th>", html)
        # 2. 一致ラベルに「URL一致」が含まれること
        self.assertIn("URL一致", html)
        # 3. リンクとして描画されていること
        self.assertIn('href="https://example.com/corporate"', html)
        self.assertIn('href="http://www.example.com/corporate/"', html)

    def test_company_candidate_list_website_diff_highlight_and_empty(self):
        """異なるwebsiteを持つ候補会社のセルがハイライトされ、未設定時はハイフン表示されること。"""
        from django.contrib.auth.models import Permission
        from companies.models import Company, CompanyDuplicateCandidate

        self.user.user_permissions.add(Permission.objects.get(codename="merge_company"))
        self.user = User.objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

        c1 = Company.objects.create(
            organization="テスト差分株式会社",
            domain="diff-test.jp",
            website="https://company-a.example.com",
        )
        c2 = Company.objects.create(
            organization="テスト差分株式会社",
            domain="diff-test.jp",
            website="https://company-b.example.com",
        )
        c3 = Company.objects.create(
            organization="テスト差分株式会社",
            domain="diff-test.jp",
            website="",
        )
        CompanyDuplicateCandidate.objects.create(
            company_a=c1,
            company_b=c2,
            score=100,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_LOW,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )
        CompanyDuplicateCandidate.objects.create(
            company_a=c1,
            company_b=c3,
            score=100,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_LOW,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )

        resp = self.client.get(reverse("companies:company_candidate_list"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # c2 の行の website セルに背景色ハイライトが付与されていること
        self.assertIn('background-color: #fffbeb;" title="https://company-b.example.com"', html)
        # c3 の website は未設定のため "-" が表示されること
        self.assertIn('<span class="app-muted">-</span>', html)

    def test_rank_separated_duplicate_groups(self):
        """社名・ドメイン一致（possible_high）とドメイン一致のみ（possible_low）が分離してグルーピングされること。"""
        from companies.views import _build_duplicate_groups
        from companies.models import Company, CompanyDuplicateCandidate

        # 会社A, B: 同一社名・同一ドメイン（possible_high）
        comp_a = Company.objects.create(organization="共通株式会社", domain="shared-domain.co.jp")
        comp_b = Company.objects.create(organization="共通株式会社", domain="shared-domain.co.jp")
        # 会社C: 別社名・同一ドメイン（possible_low）
        comp_c = Company.objects.create(organization="別会社株式会社", domain="shared-domain.co.jp")

        cand_high = CompanyDuplicateCandidate.objects.create(
            company_a=comp_a,
            company_b=comp_b,
            score=200,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )
        cand_low = CompanyDuplicateCandidate.objects.create(
            company_a=comp_a,
            company_b=comp_c,
            score=100,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_LOW,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )

        groups = _build_duplicate_groups()

        # comp_a, comp_b を含むグループを取得
        high_groups = [g for g in groups if g["rank"] == CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH and any(c.id in (comp_a.id, comp_b.id) for c in g["companies"])]
        self.assertEqual(len(high_groups), 1)
        high_group = high_groups[0]
        # possible_high グループには comp_c（別会社）が混入せず、comp_a, comp_b の2社のみであること
        high_comp_ids = {c.id for c in high_group["companies"]}
        self.assertEqual(high_comp_ids, {comp_a.id, comp_b.id})
        self.assertNotIn(comp_c.id, high_comp_ids)
        self.assertEqual(high_group["max_score"], 200)

        # comp_c を含む low グループを取得
        low_groups = [g for g in groups if g["rank"] == CompanyDuplicateCandidate.Rank.POSSIBLE_LOW and any(c.id == comp_c.id for c in g["companies"])]
        self.assertEqual(len(low_groups), 1)
        low_group = low_groups[0]
        self.assertEqual(low_group["max_score"], 100)

    def test_noba_and_nox_separated_into_different_rank_groups(self):
        """ノックス電子（possible_low）が野場電工（possible_high）の同一マージカードに巻き込まれないこと。"""
        from companies.views import _build_duplicate_groups
        from companies.models import Company, CompanyDuplicateCandidate

        noba1 = Company.objects.create(organization="野場電工株式会社", domain="noba.co.jp")
        noba2 = Company.objects.create(organization="野場電工株式会社", domain="noba.co.jp")
        nox = Company.objects.create(
            organization="ノックス電子株式会社",
            domain="noba.co.jp",
            website="https://nox-elec.co.jp/",
        )

        # 野場電工同士の possible_high ペア
        CompanyDuplicateCandidate.objects.create(
            company_a=noba1,
            company_b=noba2,
            score=200,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )
        # 野場電工とノックス電子の possible_low ペア
        CompanyDuplicateCandidate.objects.create(
            company_a=noba1,
            company_b=nox,
            score=100,
            rank=CompanyDuplicateCandidate.Rank.POSSIBLE_LOW,
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        )

        groups = _build_duplicate_groups()

        # 野場電工の possible_high グループを検証
        noba_high_groups = [
            g for g in groups
            if g["rank"] == CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH
            and any(c.id == noba1.id for c in g["companies"])
        ]
        self.assertEqual(len(noba_high_groups), 1)
        high_group = noba_high_groups[0]
        high_cids = {c.id for c in high_group["companies"]}
        # ノックス電子が含まれていないことを検証
        self.assertNotIn(nox.id, high_cids)
        self.assertEqual(high_cids, {noba1.id, noba2.id})
        self.assertEqual(high_group["company_count"], 2)


class CompanyDuplicateRankingV115Tests(TestCase):
    """仕様書 v1.15 準拠の会社重複スコア・ランク判定単体テスト。"""

    def test_domain_only_match_is_culled(self):
        """社名が異なり、ドメインのみ一致（100点）のペアが None（候補外）になること。"""
        from companies.services import calculate_company_match, determine_company_rank

        result = calculate_company_match(
            "ニッコウ電気株式会社", "shared-factory.jp", "", "", "",
            "三河商事株式会社", "shared-factory.jp", "", "", "",
        )
        self.assertFalse(result.name_match)
        self.assertTrue(result.domain_match)
        self.assertEqual(result.score, 100)
        self.assertFalse(result.url_mismatch)
        # ランクは None（空文字列）となること
        self.assertEqual(result.rank, "")
        self.assertIsNone(
            determine_company_rank(
                score=result.score,
                name_match=result.name_match,
                domain_match=result.domain_match,
                url_match=result.url_match,
                url_mismatch=result.url_mismatch,
                phone_match=result.phone_match,
                address_match=result.address_match,
            )
        )

    def test_possible_low_with_backing(self):
        """ドメイン一致＋電話一致（120点）または社名一致＋電話/住所一致（120点）が possible_low になること。"""
        from companies.models import CompanyDuplicateCandidate
        from companies.services import calculate_company_match

        # 1. ドメイン一致（100点）+ 電話一致（20点）= 120点
        res_dom_phone = calculate_company_match(
            "社名変更前株式会社", "renamed-company.co.jp", "03-1111-2222", "", "",
            "社名変更後株式会社", "renamed-company.co.jp", "0311112222", "", "",
        )
        self.assertFalse(res_dom_phone.name_match)
        self.assertTrue(res_dom_phone.domain_match)
        self.assertTrue(res_dom_phone.phone_match)
        self.assertEqual(res_dom_phone.score, 120)
        self.assertEqual(res_dom_phone.rank, CompanyDuplicateCandidate.Rank.POSSIBLE_LOW)

        # 2. 社名一致（100点）+ 電話一致（20点）= 120点
        res_name_phone = calculate_company_match(
            "株式会社テスト工業", "", "052-123-4567", "", "",
            "（株）テスト工業", "", "0521234567", "", "",
        )
        self.assertTrue(res_name_phone.name_match)
        self.assertFalse(res_name_phone.domain_match)
        self.assertTrue(res_name_phone.phone_match)
        self.assertEqual(res_name_phone.score, 120)
        self.assertEqual(res_name_phone.rank, CompanyDuplicateCandidate.Rank.POSSIBLE_LOW)

        # 3. 社名一致（100点）+ 住所前方一致（20点）= 120点
        res_name_addr = calculate_company_match(
            "三河電工株式会社", "", "", "愛知県豊田市西町2-18-2", "",
            "三河電工株式会社", "", "", "愛知県豊田市西町2-18-2 豊田ビル3F", "",
        )
        self.assertTrue(res_name_addr.name_match)
        self.assertTrue(res_name_addr.address_match)
        self.assertEqual(res_name_addr.score, 120)
        self.assertEqual(res_name_addr.rank, CompanyDuplicateCandidate.Rank.POSSIBLE_LOW)

    def test_url_mismatch_culls_even_with_name_and_domain_match(self):
        """双方が異なるWebサイトドメインを持つ場合、社名やドメインが一致していても None になること。"""
        from companies.services import calculate_company_match

        result = calculate_company_match(
            "株式会社田中製作所", "shared-isp.jp", "", "", "https://tanaka-tokyo.co.jp/",
            "株式会社田中製作所", "shared-isp.jp", "", "", "https://tanaka-osaka.co.jp/",
        )
        self.assertTrue(result.name_match)
        self.assertTrue(result.domain_match)
        self.assertTrue(result.url_mismatch)
        # スコアは200点だが url_mismatch により足切りされて None（空文字列）
        self.assertEqual(result.score, 200)
        self.assertEqual(result.rank, "")

    def test_url_different_paths_same_domain_does_not_trigger_url_mismatch(self):
        """トップページと /products/ のような同一ドメイン内下層ページ違いでは url_mismatch が誤発動しないこと。"""
        from companies.models import CompanyDuplicateCandidate
        from companies.services import calculate_company_match

        result = calculate_company_match(
            "株式会社エグザンプル", "example.co.jp", "", "", "https://example.co.jp/",
            "株式会社エグザンプル", "example.co.jp", "", "", "https://example.co.jp/products/details",
        )
        self.assertTrue(result.name_match)
        self.assertTrue(result.domain_match)
        self.assertFalse(result.url_match)
        self.assertFalse(result.url_mismatch)
        self.assertEqual(result.score, 200)
        # url_mismatch が誤発動せず、社名+ドメイン一致（200点）により possible_high になること
        self.assertEqual(result.rank, CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH)

    def test_url_normalization_helpers(self):
        """_normalize_url_string, _extract_netloc, normalize_website の仕様書準拠テスト。"""
        from companies.services import _extract_netloc, _normalize_url_string, normalize_website

        # 1. _normalize_url_string
        self.assertEqual(_normalize_url_string(""), "")
        self.assertEqual(_normalize_url_string(None), "")
        self.assertEqual(_normalize_url_string("  Example.COM/Path  "), "//example.com/path")
        self.assertEqual(_normalize_url_string("HTTP://EXAMPLE.COM"), "http://example.com")

        # 2. _extract_netloc
        self.assertEqual(_extract_netloc(""), "")
        self.assertEqual(_extract_netloc("https://www.example.co.jp/about"), "example.co.jp")
        self.assertEqual(_extract_netloc("http://example.com:8080/"), "example.com:8080")

        # 3. normalize_website（トップページファイル名除外とパス第1セグメント保持、小文字化）
        self.assertEqual(normalize_website("https://www.example.co.jp/index.html"), "example.co.jp")
        self.assertEqual(normalize_website("https://www.example.co.jp/index.php"), "example.co.jp")
        self.assertEqual(normalize_website("https://example.com/companyA/"), "example.com/companya")
        self.assertEqual(normalize_website("https://example.com/companyA/products"), "example.com/companya")












