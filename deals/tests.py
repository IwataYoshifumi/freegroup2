import re
from datetime import timedelta
from decimal import Decimal
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Department, Role
from accounts.services import apply_role
from companies.models import Company
from contacts.models import Contact
from deals.admin import DealAdmin, DealPersonAdmin, DealUserAdmin
from deals.models import Deal, DealPerson, DealType, DealUser, PersonRole, Stage, UserRole
from persons.models import Person
from back_navigator.back_navigator import BackNavigator

User = get_user_model()


class DealModelValidationTests(TestCase):
    """Deal / DealPerson / DealUser のバリデーション・制約検証（仕様書 §2.1、§5.1）。"""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="password")
        self.user2 = User.objects.create_user(username="user2", password="password")
        self.primary_person = Person.objects.create()
        self.person2 = Person.objects.create()

    def test_closed_at_future_date_raises_validation_error(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        deal = Deal(
            name="Future Closed Deal",
            primary_person=self.primary_person,
            owner=self.owner,
            closed_at=tomorrow,
        )
        with self.assertRaises(ValidationError) as ctx:
            deal.full_clean()
        self.assertIn("closed_at", ctx.exception.message_dict)

    def test_closed_at_today_or_past_succeeds(self):
        today = timezone.localdate()
        deal = Deal(
            name="Today Closed Deal",
            primary_person=self.primary_person,
            owner=self.owner,
            closed_at=today,
        )
        deal.full_clean()  # should not raise

        yesterday = today - timedelta(days=1)
        deal.closed_at = yesterday
        deal.full_clean()  # should not raise

    def test_deal_person_cannot_be_primary_person(self):
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        deal_person = DealPerson(
            deal=deal,
            person=self.primary_person,
            role=PersonRole.ATTENDEE,
        )
        with self.assertRaises(ValidationError) as ctx:
            deal_person.full_clean()
        self.assertTrue(
            any("主担当者（相手方）と同じPerson" in msg for msg in ctx.exception.messages)
        )

    def test_deal_person_other_person_succeeds(self):
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        deal_person = DealPerson(
            deal=deal,
            person=self.person2,
            role=PersonRole.ATTENDEE,
        )
        deal_person.full_clean()  # should not raise
        deal_person.save()

    def test_deal_person_unique_constraint(self):
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        DealPerson.objects.create(deal=deal, person=self.person2, role=PersonRole.ATTENDEE)
        with self.assertRaises(IntegrityError):
            DealPerson.objects.create(deal=deal, person=self.person2, role=PersonRole.CONTACT_WINDOW)

    def test_deal_user_can_be_owner(self):
        """案件オーナー自身も DealUser として登録できることを検証（独自制限撤去）。"""
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        deal_user = DealUser(
            deal=deal,
            user=self.owner,
            role=UserRole.SUPPORT,
        )
        deal_user.full_clean()  # should not raise

    def test_deal_user_other_user_succeeds(self):
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        deal_user = DealUser(
            deal=deal,
            user=self.user2,
            role=UserRole.SUPPORT,
        )
        deal_user.full_clean()  # should not raise
        deal_user.save()

    def test_deal_user_unique_constraint(self):
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        DealUser.objects.create(deal=deal, user=self.user2, role=UserRole.SUPPORT)
        with self.assertRaises(IntegrityError):
            DealUser.objects.create(deal=deal, user=self.user2, role=UserRole.APPROVER)

    def test_deal_person_roles(self):
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        dp = DealPerson.objects.create(
            deal=deal,
            person=self.person2,
            role=PersonRole.DECISION_MAKER,
            memo="Key decision maker",
        )
        self.assertEqual(dp.role, PersonRole.DECISION_MAKER)

    def test_deal_user_can_edit_and_roles(self):
        deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.primary_person,
            owner=self.owner,
        )
        du = DealUser.objects.create(
            deal=deal,
            user=self.user2,
            role=UserRole.APPROVER,
            can_edit=False,
            memo="Approver with view only",
        )
        self.assertFalse(du.can_edit)
        self.assertEqual(du.role, UserRole.APPROVER)



class DealAdminTests(TestCase):
    """DealAdmin の保護制御検証（仕様書 §7.6.1）。"""

    def setUp(self):
        self.site = AdminSite()
        self.deal_admin = DealAdmin(Deal, self.site)
        self.deal_person_admin = DealPersonAdmin(DealPerson, self.site)
        self.deal_user_admin = DealUserAdmin(DealUser, self.site)
        self.user = User.objects.create_user(username="admin_user", is_staff=True)
        self.person = Person.objects.create()
        self.deal = Deal.objects.create(
            name="Admin Test Deal",
            primary_person=self.person,
            owner=self.user,
        )

    def test_deal_admin_readonly_fields_new_object(self):
        readonly = self.deal_admin.get_readonly_fields(request=None, obj=None)
        self.assertEqual(readonly, [])

    def test_deal_admin_readonly_fields_existing_object(self):
        readonly = self.deal_admin.get_readonly_fields(request=None, obj=self.deal)
        self.assertIn("owner", readonly)
        self.assertIn("primary_person", readonly)
        self.assertIn("is_archived", readonly)

    def test_deal_person_admin_autocomplete_fields(self):
        self.assertIn("deal", self.deal_person_admin.autocomplete_fields)
        self.assertIn("person", self.deal_person_admin.autocomplete_fields)

    def test_deal_user_admin_autocomplete_fields(self):
        self.assertIn("deal", self.deal_user_admin.autocomplete_fields)
        self.assertIn("user", self.deal_user_admin.autocomplete_fields)


class DealServiceTests(TestCase):
    """deals/services.py のコアロジック検証（仕様書 §2.5, §2.6）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="operator", password="password")
        self.owner = User.objects.create_user(username="owner", password="password")
        self.new_owner = User.objects.create_user(username="new_owner", password="password")
        self.primary_person = Person.objects.create()
        self.new_person = Person.objects.create()
        self.deal = Deal.objects.create(
            name="大型システム更改案件",
            stage=Deal.Stage.QUOTATION,
            primary_person=self.primary_person,
            owner=self.owner,
        )

    def test_close_deal_won_and_lost(self):
        from actionlogs.models import ActionLog
        from deals.services import close_deal

        # 受注へのクローズ
        closed_deal = close_deal(self.deal, Deal.Stage.WON, user=self.user)
        self.assertEqual(closed_deal.stage, Deal.Stage.WON)
        self.assertEqual(closed_deal.closed_at, timezone.localdate())
        self.assertEqual(closed_deal.lost_reason, "")
        self.assertEqual(closed_deal.updated_by, self.user)

        log = ActionLog.objects.filter(action="stage_changed", content_type__model="deal").latest("created_at")
        self.assertEqual(log.data["to_stage"], Deal.Stage.WON)

        # 失注へのクローズ（lost_reason 必須）
        closed_lost = close_deal(self.deal, Deal.Stage.LOST, user=self.user, lost_reason="価格競合で他社選定")
        self.assertEqual(closed_lost.stage, Deal.Stage.LOST)
        self.assertEqual(closed_lost.lost_reason, "価格競合で他社選定")

        # クローズ外ステージ指定は ValueError
        with self.assertRaises(ValueError):
            close_deal(self.deal, Deal.Stage.QUOTATION, user=self.user)

        # 失注時に lost_reason 未指定は ValidationError
        with self.assertRaises(ValidationError):
            close_deal(self.deal, Deal.Stage.LOST, user=self.user, lost_reason="")

    def test_reassign_deal_owner(self):
        from actionlogs.models import ActionLog
        from deals.services import reassign_deal_owner

        # new_owner を事前に DealUser（関係者）として登録しておく
        DealUser.objects.create(deal=self.deal, user=self.new_owner, role=UserRole.SUPPORT)
        self.assertTrue(DealUser.objects.filter(deal=self.deal, user=self.new_owner).exists())

        reassigned = reassign_deal_owner(self.deal, self.new_owner, user=self.user)
        self.assertEqual(reassigned.owner, self.new_owner)
        self.assertEqual(reassigned.updated_by, self.user)

        # 二重登録防止のため DealUser レコードが削除されていること
        self.assertFalse(DealUser.objects.filter(deal=self.deal, user=self.new_owner).exists())

        log = ActionLog.objects.filter(action="owner_changed").latest("created_at")
        self.assertEqual(log.data["new_owner_id"], str(self.new_owner.id))
        self.assertEqual(log.data["old_owner_id"], str(self.owner.id))

    def test_reassign_deal_primary_person(self):
        from actionlogs.models import ActionLog
        from deals.services import reassign_deal_primary_person

        # new_person を事前に DealPerson（関係者）として登録しておく
        DealPerson.objects.create(deal=self.deal, person=self.new_person, role=PersonRole.CONTACT_WINDOW)
        self.assertTrue(DealPerson.objects.filter(deal=self.deal, person=self.new_person).exists())

        reassigned = reassign_deal_primary_person(self.deal, self.new_person, user=self.user)
        self.assertEqual(reassigned.primary_person, self.new_person)
        self.assertEqual(reassigned.updated_by, self.user)

        # 二重登録防止のため DealPerson レコードが削除されていること
        self.assertFalse(DealPerson.objects.filter(deal=self.deal, person=self.new_person).exists())

        log = ActionLog.objects.filter(action="primary_person_changed").latest("created_at")
        self.assertEqual(log.data["new_primary_person_id"], str(self.new_person.id))
        self.assertEqual(log.data["old_primary_person_id"], str(self.primary_person.id))

    def test_archive_deal(self):
        from actionlogs.models import ActionLog
        from deals.services import archive_deal

        archived = archive_deal(self.deal, user=self.user)
        self.assertTrue(archived.is_archived)
        self.assertEqual(archived.updated_by, self.user)

        log = ActionLog.objects.filter(action="deal_archived", content_type__model="deal").latest("created_at")
        self.assertEqual(str(log.content_object.id), str(self.deal.id))


class DealPermissionTests(TestCase):
    """deals/permissions.py の認可述語検証（仕様書 §7.1）。"""

    def setUp(self):
        from django.contrib.auth.models import Permission
        self.owner = User.objects.create_user(username="owner", password="password")
        self.member = User.objects.create_user(username="member", password="password")
        self.outsider = User.objects.create_user(username="outsider", password="password")
        self.privileged_user = User.objects.create_user(username="privileged", password="password")

        # 権限の付与
        change_deal_perm = Permission.objects.get(codename="change_deal")
        view_all_perm = Permission.objects.get(codename="view_all_deals")
        edit_all_perm = Permission.objects.get(codename="edit_all_deals")

        self.owner.user_permissions.add(change_deal_perm)
        self.member.user_permissions.add(change_deal_perm)
        self.privileged_user.user_permissions.add(change_deal_perm, view_all_perm, edit_all_perm)

        self.person = Person.objects.create()
        self.deal = Deal.objects.create(
            name="認可テスト案件",
            primary_person=self.person,
            owner=self.owner,
        )
        DealUser.objects.create(deal=self.deal, user=self.member, role=UserRole.SUPPORT)

    def test_can_view_deal(self):
        from deals.permissions import can_view_deal

        self.assertTrue(can_view_deal(self.owner, self.deal))
        self.assertTrue(can_view_deal(self.member, self.deal))
        self.assertTrue(can_view_deal(self.privileged_user, self.deal))
        self.assertFalse(can_view_deal(self.outsider, self.deal))

    def test_can_edit_deal(self):
        from deals.permissions import can_edit_deal

        # change_deal 権限を持つ owner, member, privileged は可
        self.assertTrue(can_edit_deal(self.owner, self.deal))
        self.assertTrue(can_edit_deal(self.member, self.deal))
        self.assertTrue(can_edit_deal(self.privileged_user, self.deal))
        self.assertFalse(can_edit_deal(self.outsider, self.deal))

        # DealUser の can_edit が False の場合、change_deal 権限があっても編集不可
        deal_user = DealUser.objects.get(deal=self.deal, user=self.member)
        deal_user.can_edit = False
        deal_user.save()
        self.assertFalse(can_edit_deal(self.member, self.deal))
        # 復元
        deal_user.can_edit = True
        deal_user.save()

        # change_deal 権限を剥奪された場合は owner でも不可（仕様書 §7.1 の AND 条件厳守）
        from django.contrib.auth.models import Permission
        self.owner.user_permissions.remove(Permission.objects.get(codename="change_deal"))
        self.owner = User.objects.get(pk=self.owner.pk)
        self.assertFalse(can_edit_deal(self.owner, self.deal))

    def test_can_approve_deal(self):
        from deals.permissions import can_approve_deal

        # privileged_user は edit_all_deals を持つため承認可能
        self.assertTrue(can_approve_deal(self.privileged_user, self.deal))

        # member は role=SUPPORT のため承認不可
        self.assertFalse(can_approve_deal(self.member, self.deal))

        # member の role を APPROVER に更新すると承認可能
        deal_user = DealUser.objects.get(deal=self.deal, user=self.member)
        deal_user.role = UserRole.APPROVER
        deal_user.save()
        self.assertTrue(can_approve_deal(self.member, self.deal))

        # change_deal 権限がなくなると APPROVER でも不可
        from django.contrib.auth.models import Permission
        self.member.user_permissions.remove(Permission.objects.get(codename="change_deal"))
        self.member = User.objects.get(pk=self.member.pk)
        self.assertFalse(can_approve_deal(self.member, self.deal))


    def test_can_archive_deal(self):
        from deals.permissions import can_archive_deal

        # owner と edit_all_deals 保持者は可、DealUser（member）は不可
        self.assertTrue(can_archive_deal(self.owner, self.deal))
        self.assertTrue(can_archive_deal(self.privileged_user, self.deal))
        self.assertFalse(can_archive_deal(self.member, self.deal))
        self.assertFalse(can_archive_deal(self.outsider, self.deal))

    def test_can_reassign_deal_owner(self):
        from deals.permissions import can_reassign_deal_owner

        self.assertTrue(can_reassign_deal_owner(self.owner, self.deal))
        self.assertTrue(can_reassign_deal_owner(self.privileged_user, self.deal))
        self.assertFalse(can_reassign_deal_owner(self.member, self.deal))
        self.assertFalse(can_reassign_deal_owner(self.outsider, self.deal))

    def test_visible_deals_for(self):
        from deals.permissions import visible_deals_for

        deal2 = Deal.objects.create(name="部外者案件", primary_person=self.person, owner=self.outsider)

        # privileged_user は全件（2件）
        self.assertEqual(visible_deals_for(self.privileged_user).count(), 2)

        # owner は担当案件のみ（1件）
        qs_owner = visible_deals_for(self.owner)
        self.assertEqual(qs_owner.count(), 1)
        self.assertEqual(qs_owner.first(), self.deal)

        # member は DealUser として関与する案件のみ（1件）
        qs_member = visible_deals_for(self.member)
        self.assertEqual(qs_member.count(), 1)
        self.assertEqual(qs_member.first(), self.deal)

        # outsider は自身が owner の deal2 のみ（1件）
        qs_outsider = visible_deals_for(self.outsider)
        self.assertEqual(qs_outsider.count(), 1)
        self.assertEqual(qs_outsider.first(), deal2)


class DealViewTests(TestCase):
    """deals View層の認可・表示・画面遷移の検証（仕様書 §2.1, §2.5, §2.6, §7.1）。"""

    def setUp(self):
        self.owner = User.objects.create_user(username="deal_owner", password="password")
        self.editor = User.objects.create_user(username="deal_editor", password="password")
        self.outsider = User.objects.create_user(username="deal_outsider", password="password")
        self.perm_add = Permission.objects.get(codename="add_deal")
        self.perm_change = Permission.objects.get(codename="change_deal")
        self.perm_view_all = Permission.objects.get(codename="view_all_deals")
        self.perm_edit_all = Permission.objects.get(codename="edit_all_deals")

        self.owner.user_permissions.add(self.perm_add, self.perm_change)
        self.editor.user_permissions.add(self.perm_add, self.perm_change)

        self.person = Person.objects.create()
        self.company = Company.objects.create(organization="テスト株式会社")
        self.contact = Contact.objects.create(person=self.person, company=self.company, last_name="田中", first_name="太郎")
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact"])

        self.deal = Deal.objects.create(
            name="基幹システム導入",
            primary_person=self.person,
            company=self.company,
            owner=self.owner,
            stage=Stage.QUOTATION,
            amount=5000000,
            probability=80,
        )
        DealUser.objects.create(deal=self.deal, user=self.editor, role=UserRole.SUPPORT)

    def test_deal_list_view_anonymous_redirect(self):
        url = reverse("deals:deal_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_deal_list_view_authenticated(self):
        self.client.login(username="deal_owner", password="password")
        response = self.client.get(reverse("deals:deal_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "基幹システム導入")
        self.assertContains(response, "テスト株式会社")
        # HIG準拠レイアウト・表示（コンテナ幅、金額3桁カンマ区切り、確度%表示、ステージバッジ）
        self.assertContains(response, "max-width: 100%;")
        self.assertContains(response, "¥5,000,000")
        self.assertContains(response, "80%")
        self.assertContains(
            response,
            '<span class="app-badge" style="background-color: #ede9fe; color: #6d28d9; border: 1px solid #ddd6fe; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">見積提示</span>',
        )
        self.assertContains(response, 'text-align: right; font-variant-numeric: tabular-nums;')

    def test_deal_detail_view_permissions(self):
        # 権限あり（owner）
        self.client.login(username="deal_owner", password="password")
        response = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "基幹システム導入")

        # 権限あり（DealUser）
        self.client.login(username="deal_editor", password="password")
        response = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(response.status_code, 200)

        # 権限なし（outsider）-> 403
        self.client.login(username="deal_outsider", password="password")
        response = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(response.status_code, 403)

    def test_deal_create_view_and_post(self):
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        post_data = {
            "name": "新規クラウド移行案件",
            "company": str(self.company.id),
            "stage": Stage.INITIAL_MEETING,
            "probability": 50,
            "deal_type": DealType.NEW,
            "amount": 3000000,
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        new_deal = Deal.objects.get(name="新規クラウド移行案件")
        self.assertEqual(new_deal.owner, self.owner)
        self.assertEqual(new_deal.created_by, self.owner)
        self.assertEqual(new_deal.company, self.company)
        self.assertIsNone(new_deal.primary_person)

    def test_deal_create_view_with_source_campaign_and_back_navigator(self):
        from mailings.models import Campaign, EmailTemplate, MailingList
        from back_navigator.back_navigator import BackNavigator

        self.client.login(username="deal_owner", password="password")
        template = EmailTemplate.objects.create(name="T", subject="S", body="B", created_by=self.owner)
        ml = MailingList.objects.create(name="ML", created_by=self.owner)
        campaign = Campaign.objects.create(name="テストCP", template=template, mailing_list=ml, created_by=self.owner)

        nav = BackNavigator(self.client.get("/").wsgi_request)
        back_stack = nav._calc_encode_stack([{"url": f"/mailings/campaigns/{campaign.pk}/report/", "title": "配信レポート"}])

        url = f"{reverse('deals:deal_create')}?lead_source=campaign&source_campaign={campaign.pk}&back_stack={back_stack}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        form = response.context["form"]
        self.assertEqual(form.initial.get("lead_source"), "campaign")
        self.assertEqual(str(form.initial.get("source_campaign")), str(campaign.pk))

        content = response.content.decode("utf-8")
        self.assertIn(f'href="/mailings/campaigns/{campaign.pk}/report/"', content)

        post_data = {
            "name": "キャンペーン経由案件",
            "company": str(self.company.id),
            "stage": Stage.INITIAL_MEETING,
            "probability": 40,
            "deal_type": DealType.NEW,
            "lead_source": "campaign",
            "source_campaign": str(campaign.pk),
            "back_stack": back_stack,
        }
        post_resp = self.client.post(url, data=post_data)
        self.assertEqual(post_resp.status_code, 302)
        created_deal = Deal.objects.get(name="キャンペーン経由案件")
        expected_url = reverse("deals:deal_persons_manage", kwargs={"pk": created_deal.pk}) + f"?wizard=1&back_stack={back_stack}"
        self.assertEqual(post_resp.url, expected_url)
        self.assertEqual(created_deal.source_campaign, campaign)
        self.assertEqual(created_deal.lead_source, "campaign")

    def test_deal_create_view_with_company_person_and_campaign_creates_deal_person(self):
        """DealCreateView に company, person, source_campaign を渡して POST した際、
        案件と DealPerson が同時に作成されてウィザード画面へリダイレクトされること。
        """
        from mailings.models import Campaign, EmailTemplate, MailingList
        from back_navigator.back_navigator import BackNavigator

        self.client.login(username="deal_owner", password="password")
        template = EmailTemplate.objects.create(name="T", subject="S", body="B", created_by=self.owner)
        ml = MailingList.objects.create(name="ML", created_by=self.owner)
        campaign = Campaign.objects.create(name="クリックCP", template=template, mailing_list=ml, created_by=self.owner)

        clicked_list_url = f"/mailings/campaigns/{campaign.pk}/report/clicked/"
        nav = BackNavigator(self.client.get("/").wsgi_request)
        back_stack = nav._calc_encode_stack([{"url": clicked_list_url, "title": "クリック受信者一覧"}])

        url = (
            f"{reverse('deals:deal_create')}?"
            f"lead_source=campaign&source_campaign={campaign.pk}&company={self.company.pk}&person={self.person.pk}&back_stack={back_stack}"
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial.get("company"), str(self.company.pk))
        self.assertEqual(response.context["form"].initial.get("source_campaign"), str(campaign.pk))
        self.assertEqual(response.context["selected_person"], self.person)

        post_data = {
            "name": "クリック受信者からの案件",
            "company": str(self.company.id),
            "stage": Stage.INITIAL_MEETING,
            "probability": 50,
            "deal_type": DealType.NEW,
            "lead_source": "campaign",
            "source_campaign": str(campaign.pk),
            "person": str(self.person.pk),
            "back_stack": back_stack,
        }
        post_resp = self.client.post(url, data=post_data)
        self.assertEqual(post_resp.status_code, 302)
        created_deal = Deal.objects.get(name="クリック受信者からの案件")
        expected_url = reverse("deals:deal_persons_manage", kwargs={"pk": created_deal.pk}) + f"?wizard=1&back_stack={back_stack}"
        self.assertEqual(post_resp.url, expected_url)
        self.assertEqual(created_deal.company, self.company)
        self.assertEqual(created_deal.source_campaign, campaign)
        self.assertEqual(created_deal.lead_source, "campaign")

        # DealPerson が自動的に作成されていること
        deal_person = DealPerson.objects.filter(deal=created_deal, person=self.person).first()
        self.assertIsNotNone(deal_person)
        self.assertEqual(deal_person.role, PersonRole.CONTACT_WINDOW)

    def test_deal_update_view(self):
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_update", kwargs={"pk": self.deal.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url, data={
            "name": "基幹システム導入（変更後）",
            "company": str(self.company.id),
            "stage": Stage.UNDER_REVIEW,
            "probability": 70,
            "amount": 6000000,
        })
        self.assertEqual(response.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.name, "基幹システム導入（変更後）")
        self.assertEqual(self.deal.probability, 70)

    def test_deal_close_view(self):
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_close", kwargs={"pk": self.deal.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # 失注実行
        response = self.client.post(url, data={
            "stage": Stage.LOST,
            "closed_at": str(timezone.localdate()),
            "lost_reason": "予算超過のため見送り",
        })
        self.assertEqual(response.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.stage, Stage.LOST)
        self.assertEqual(self.deal.lost_reason, "予算超過のため見送り")

    def test_deal_reassign_owner_view(self):
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_reassign_owner", kwargs={"pk": self.deal.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # editor は DealUser にいる
        self.assertTrue(DealUser.objects.filter(deal=self.deal, user=self.editor).exists())

        response = self.client.post(url, data={"new_owner": str(self.editor.id)})
        self.assertEqual(response.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.owner, self.editor)
        # DealUser から自動除外されていること
        self.assertFalse(DealUser.objects.filter(deal=self.deal, user=self.editor).exists())

    def test_deal_detail_action_buttons_and_owner_toggle(self):
        """案件詳細のボタン高さ合わせ、編集・アーカイブのアイコン化、担当者トグル導線を検証。"""
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # 1. アクションボタン帯と戻るボタンが同一水平行（justify-content: space-between; align-items: center;）
        self.assertIn("display: flex; justify-content: space-between; align-items: center;", html)

        # 2. 編集ボタン・アーカイブボタンが基本情報カード内に配置されアイコン化（app-icon-btn, HIG準拠）
        self.assertRegex(
            html,
            r'<a\s+href="[^"]*edit[^"]*"\s+class="app-icon-btn"\s+title="案件を編集"\s+aria-label="案件を編集"[^>]*>\s*<i class="bi bi-pencil-fill"></i>\s*</a>',
        )
        self.assertRegex(
            html,
            r'<a\s+href="[^"]*archive[^"]*"\s+class="app-icon-btn"\s+title="アーカイブ"\s+aria-label="アーカイブ"[^>]*>\s*<i class="bi bi-archive-fill"></i>\s*</a>',
        )

        # 3. 上部アクション帯からテキストの「担当者変更」ボタンが撤去されていること
        self.assertNotIn('>担当者変更</a>', html)

        # 4. 基本情報カード内の担当者欄にボーダーレストグル（▼矢印）と横並び緑ボタン「担当者を変更」が存在すること
        self.assertIn("owner-reassign-details", html)
        self.assertIn("toggle-arrow", html)
        self.assertIn("▼", html)
        self.assertIn('class="app-btn app-btn--success app-btn--sm"', html)
        self.assertIn("担当者を変更", html)
        reassign_url = reverse("deals:deal_reassign_owner", kwargs={"pk": self.deal.pk})
        self.assertIn(reassign_url, html)

    def test_deal_detail_persons_users_icon_buttons_and_attachment_memo_ui(self):
        """案件詳細の関係者・担当者カードの編集アイコン化、および添付メモの非常時フォーム・編集トリガーを検証。"""
        from django.core.files.base import ContentFile
        from attachments.models import Attachment

        # 添付ファイル追加権限を付与
        perm_add_att = Permission.objects.get(codename="add_attachment")
        self.owner.user_permissions.add(perm_add_att)

        att = Attachment.objects.create(
            deal=self.deal,
            file=ContentFile(b"test file content", name="test_file.pdf"),
            original_filename="test_file.pdf",
            memo="既存のメモ内容",
            uploaded_by=self.owner,
        )

        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # 1. 「関係者を管理・追加」「担当者を管理・追加」テキストボタンが存在しないこと
        self.assertNotIn(">関係者を管理・追加</a>", html)
        self.assertNotIn(">担当者を管理・追加</a>", html)

        # 2. 社外関係者カードおよび社内担当者カードに app-icon-btn の編集アイコン（bi-pencil-fill）が存在すること
        self.assertRegex(
            html,
            r'<a\s+href="[^"]*persons/manage[^"]*"\s+class="app-icon-btn"\s+title="案件関係者を管理・追加"\s+aria-label="案件関係者を管理・追加"[^>]*>\s*<i class="bi bi-pencil-fill"></i>\s*</a>',
        )
        self.assertRegex(
            html,
            r'<a\s+href="[^"]*users/manage[^"]*"\s+class="app-icon-btn"\s+title="案件担当者を管理・追加"\s+aria-label="案件担当者を管理・追加"[^>]*>\s*<i class="bi bi-pencil-fill"></i>\s*</a>',
        )

        # 3. 添付ファイル一覧のメモが常時フォームではなくテキスト表示＋編集トリガー（memo-edit-btn）になっていること
        self.assertIn('class="attachment-memo-cell"', html)
        self.assertIn('class="memo-view-mode"', html)
        self.assertIn("既存のメモ内容", html)
        self.assertIn('class="app-icon-btn memo-edit-btn"', html)

        # 4. D&D アップロードゾーンが存在し、multiple属性が付与されていること
        self.assertIn('id="attachment-dropzone"', html)
        self.assertIn('id="attachment-file-input"', html)
        self.assertIn('multiple', html)
        self.assertRegex(html, r'<input[^>]*id="attachment-file-input"[^>]*multiple[^>]*>')
        self.assertIn('name="files"', html)
        self.assertIn("ファイルをここにドラッグ＆ドロップ、またはクリックして選択", html)



    def test_deal_reassign_owner_search_and_selection_ui(self):
        """案件担当者変更画面の検索UI、未検索時案内、候補テーブル、選択プレビュー、緑確定ボタン、POST確定処理を検証。"""
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_reassign_owner", kwargs={"pk": self.deal.pk})

        # 1. 未検索時の画面表示の検証
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # 未検索時は候補一覧リストを空として渡し、候補テーブルは非表示で案内文が表示されること
        self.assertEqual(resp.context["candidate_users"], [])
        self.assertNotIn("candidates-table", html)
        self.assertIn("氏名・ユーザー名・メールアドレスを入力して検索してください。", html)

        # 素の <select> ドロップダウンが廃止され、hidden input になっていること
        self.assertNotIn("<select", html)
        self.assertIn('type="hidden" name="new_owner"', html)

        # 確定ボタンが緑色（app-btn--success HIG 3.6準拠）で文言が「担当者を変更する」であること
        self.assertIn('class="app-btn app-btn--success"', html)
        self.assertIn("担当者を変更する", html)
        self.assertIn("submit-reassign-btn", html)

        # 2. 検索機能の検証（GET ?q=...）
        search_resp = self.client.get(f"{url}?q={self.editor.username}")
        self.assertEqual(search_resp.status_code, 200)
        search_html = search_resp.content.decode("utf-8")

        # 検索時は該当ユーザーが含まれ、候補一覧テーブルおよび「選択」ボタンが表示されること
        self.assertIn(self.editor, search_resp.context["candidate_users"])
        self.assertIn("candidates-table", search_html)
        self.assertIn("select-user-btn", search_html)

        # 3. POST で確定して担当者を変更
        post_resp = self.client.post(url, data={"new_owner": str(self.editor.id)})
        self.assertEqual(post_resp.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.owner, self.editor)


    def test_deal_archive_view(self):
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_archive", kwargs={"pk": self.deal.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.deal.refresh_from_db()
        self.assertTrue(self.deal.is_archived)

    def test_deal_form_does_not_contain_related_persons_ui_or_context(self):
        """案件新規・編集画面に関係者（同席・関連）UIやモーダル、相手方主担当、および関連コンテキストが存在しないことを検証。"""
        self.client.login(username="deal_owner", password="password")

        # 編集画面
        update_url = reverse("deals:deal_update", kwargs={"pk": self.deal.pk})
        update_res = self.client.get(update_url)
        self.assertEqual(update_res.status_code, 200)
        self.assertNotIn("candidate_persons", update_res.context)
        self.assertNotIn("initial_related_persons", update_res.context)
        self.assertNotContains(update_res, "related-persons-area")
        self.assertNotContains(update_res, "relatedPersonModal")
        self.assertNotContains(update_res, "相手方主担当（パーソン）")

        # 新規作成画面
        create_url = reverse("deals:deal_create")
        create_res = self.client.get(create_url)
        self.assertEqual(create_res.status_code, 200)
        self.assertNotIn("candidate_persons", create_res.context)
        self.assertNotIn("initial_related_persons", create_res.context)
        self.assertNotContains(create_res, "related-persons-area")
        self.assertNotContains(create_res, "relatedPersonModal")

    def test_company_search_api(self):
        """会社検索エンドポイント（JSON返却）の認可・検索機能を検証。"""
        url = reverse("deals:company_search")

        # 未ログインはリダイレクト
        res = self.client.get(url)
        self.assertEqual(res.status_code, 302)

        # ログイン
        self.client.login(username="deal_owner", password="password")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("results", data)
        self.assertTrue(any(c["id"] == str(self.company.id) for c in data["results"]))

        # キーワード検索
        c2 = Company.objects.create(organization="グローバル先端技術株式会社", domain="global-tech.example.com")
        res_q = self.client.get(f"{url}?q=先端技術")
        self.assertEqual(res_q.status_code, 200)
        data_q = res_q.json()["results"]
        self.assertEqual(len(data_q), 1)
        self.assertEqual(data_q[0]["id"], str(c2.id))

    def test_deal_form_company_modal_ui(self):
        """案件フォームに会社検索ダイアログとhidden inputが存在し正しくレンダリングされることを検証。"""
        self.client.login(username="deal_owner", password="password")

        # 編集画面
        update_url = reverse("deals:deal_update", kwargs={"pk": self.deal.pk})
        res = self.client.get(update_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'id="companySearchModal"')
        self.assertContains(res, 'id="id_company"')
        self.assertContains(res, 'id="company-search-input"')
        self.assertContains(res, 'id="company-search-initial-notice"')
        self.assertContains(res, 'id="company-search-table-wrapper"')
        self.assertContains(res, '会社名またはドメインを入力して検索してください')
        self.assertContains(res, self.company.organization)

        # 新規作成画面
        create_url = reverse("deals:deal_create")
        res_create = self.client.get(create_url)
        self.assertEqual(res_create.status_code, 200)
        self.assertContains(res_create, 'id="companySearchModal"')
        self.assertContains(res_create, 'id="id_company"')
        self.assertContains(res_create, 'id="company-search-initial-notice"')
        self.assertContains(res_create, 'id="company-search-table-wrapper"')
        self.assertContains(res_create, '会社名またはドメインを入力して検索してください')
        self.assertContains(res_create, '（未設定）')

    def test_deal_persons_manage_view_candidate_limit(self):
        """DealPersonManageView の candidate_persons が制限されることを検証。"""
        self.client.login(username="deal_owner", password="password")

        # 150件以上のパーソンを作成（一部は deal.company に所属、残りは他社または会社なし）
        other_company = Company.objects.create(organization="別会社")
        bulk_persons = [Person() for _ in range(160)]
        Person.objects.bulk_create(bulk_persons)

        created_persons = list(Person.objects.filter(id__in=[p.id for p in bulk_persons]))
        contacts = []
        for i, p in enumerate(created_persons):
            comp = self.company if i < 70 else other_company
            contacts.append(Contact(person=p, company=comp, last_name=f"姓{i}", first_name=f"名{i}"))
        Contact.objects.bulk_create(contacts)

        # primary_contact を Person に紐づけ
        saved_contacts = Contact.objects.filter(person__in=created_persons).select_related("person")
        for c in saved_contacts:
            c.person.primary_contact = c
        Person.objects.bulk_update([c.person for c in saved_contacts], ["primary_contact"])

        # 未検索時は候補リストが空であること
        res_unsearched = self.client.get(reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk}))
        self.assertEqual(res_unsearched.status_code, 200)
        self.assertEqual(list(res_unsearched.context["candidate_persons"]), [])
        self.assertContains(res_unsearched, "検索条件を入力して候補を検索してください")

        # 検索実行時（会社設定ありの案件での検証、最大50件制限）
        response = self.client.get(reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk}) + "?q=姓")
        self.assertEqual(response.status_code, 200)
        candidates = response.context["candidate_persons"]
        self.assertLessEqual(len(candidates), 50)
        self.assertGreater(len(candidates), 0)

        # 会社未設定の案件での検証
        deal_no_company = Deal.objects.create(
            name="会社未設定案件",
            primary_person=self.person,
            company=None,
            owner=self.owner,
        )
        response_no_comp = self.client.get(reverse("deals:deal_persons_manage", kwargs={"pk": deal_no_company.pk}) + "?q=姓")
        self.assertEqual(response_no_comp.status_code, 200)
        candidates_no_comp = response_no_comp.context["candidate_persons"]
        self.assertLessEqual(len(candidates_no_comp), 50)
        self.assertGreater(len(candidates_no_comp), 0)

    def test_deal_detail_view_has_manage_buttons(self):
        """案件詳細画面に管理画面への遷移ボタンが存在し、インライン検索入力が存在しないことを検証。"""
        self.client.login(username="deal_owner", password="password")
        response = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("関係者を管理・追加", html)
        self.assertIn("担当者を管理・追加", html)
        self.assertIn(reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk}), html)
        self.assertIn(reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk}), html)
        self.assertNotIn('id="person-search-input"', html)
        self.assertNotIn('id="user-search-input"', html)

    def test_deal_persons_manage_flow(self):
        """社外関係者管理画面での検索・追加・削除フローを検証。"""
        self.client.login(username="deal_owner", password="password")
        manage_url = reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk})

        # 1. 画面表示確認（未検索時はガイダンス表示、候補一覧テーブル非表示）
        response = self.client.get(manage_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "検索条件を入力して候補を検索してください")
        self.assertEqual(list(response.context["candidate_persons"]), [])

        # 2. 検索機能（検索実行時は結果テーブルと「追加」が表示されること）
        target_p = Person.objects.create()
        Contact.objects.create(person=target_p, last_name="特異名字テスト", first_name="太郎")
        target_p.primary_contact = Contact.objects.get(person=target_p)
        target_p.save()

        search_res = self.client.get(f"{manage_url}?q=特異名字テスト")
        self.assertEqual(search_res.status_code, 200)
        self.assertIn(target_p, search_res.context["candidate_persons"])

        # 3. 追加POST（nextパラメータ付きで管理画面へリダイレクト）
        add_url = reverse("deals:deal_add_person", kwargs={"pk": self.deal.pk})
        post_res = self.client.post(add_url, data={
            "person": str(target_p.id),
            "role": PersonRole.DECISION_MAKER,
            "memo": "決裁権限あり",
            "next": manage_url,
        })
        self.assertRedirects(post_res, manage_url)

        dp = DealPerson.objects.get(deal=self.deal, person=target_p)
        self.assertEqual(dp.role, PersonRole.DECISION_MAKER)
        self.assertEqual(dp.memo, "決裁権限あり")

        # 削除ボタンのスタイル・アイコン検証（app-icon-btn, bi-trash-fill）
        res_manage = self.client.get(manage_url)
        self.assertContains(res_manage, 'class="app-icon-btn"')
        self.assertContains(res_manage, 'bi bi-trash-fill')

        # 4. 削除POST（nextパラメータ付きで管理画面へリダイレクト）
        del_url = reverse("deals:deal_delete_person", kwargs={"pk": self.deal.pk, "person_rel_id": dp.id})
        del_res = self.client.post(del_url, data={"next": manage_url})
        self.assertRedirects(del_res, manage_url)
        self.assertFalse(DealPerson.objects.filter(id=dp.id).exists())

    def test_deal_users_manage_flow(self):
        """社内担当者管理画面での検索・追加・権限設定・削除フローを検証。"""
        self.client.login(username="deal_owner", password="password")
        manage_url = reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk})

        # 1. 画面表示確認（未検索時はガイダンス表示、候補一覧テーブル非表示）
        response = self.client.get(manage_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "検索条件を入力して候補を検索してください")
        self.assertEqual(list(response.context["candidate_users"]), [])

        # 2. 検索機能（ユーザー自身のフィールドおよび紐づくPersonの氏名検索）
        target_u = User.objects.create_user(username="unique_test_user", password="password", first_name="特異太郎")
        search_res = self.client.get(f"{manage_url}?q=unique_test_user")
        self.assertEqual(search_res.status_code, 200)
        self.assertIn(target_u, search_res.context["candidate_users"])

        # 紐づくPersonの漢字氏名による検索
        person_linked_user = User.objects.create_user(username="iwata_test_user", password="password", first_name="", last_name="")
        p = Person.objects.create()
        Contact.objects.create(person=p, last_name="岩田", first_name="太郎", full_name="岩田 太郎")
        p.primary_contact = Contact.objects.get(person=p)
        p.save()
        person_linked_user.person = p
        person_linked_user.save()

        search_person_res = self.client.get(f"{manage_url}?q=岩田")
        self.assertEqual(search_person_res.status_code, 200)
        self.assertIn(person_linked_user, search_person_res.context["candidate_users"])

        # UI要素の検証（戻るボタン、追加ボタン、ゴミ箱ボタン）
        html = search_person_res.content.decode("utf-8")
        self.assertNotIn("← 戻る", html)
        self.assertIn(">戻る</a>", html)
        self.assertIn(">追加</button>", html)
        self.assertIn(">追加</button>", html)

        # 3. 追加POST（役割・編集権限・メモ設定、nextパラメータ付き）
        add_url = reverse("deals:deal_add_user", kwargs={"pk": self.deal.pk})
        post_res = self.client.post(add_url, data={
            "user": str(target_u.id),
            "role": UserRole.SUPPORT,
            "can_edit": "on",
            "memo": "技術サポート担当",
            "next": manage_url,
        })
        self.assertRedirects(post_res, manage_url)

        du = DealUser.objects.get(deal=self.deal, user=target_u)
        self.assertEqual(du.role, UserRole.SUPPORT)
        self.assertTrue(du.can_edit)
        self.assertEqual(du.memo, "技術サポート担当")

        # 削除ボタンのスタイル・アイコン検証（app-icon-btn, bi-trash-fill）
        res_manage = self.client.get(manage_url)
        self.assertContains(res_manage, 'class="app-icon-btn"')
        self.assertContains(res_manage, 'bi bi-trash-fill')

        # 4. 削除POST（nextパラメータ付きで管理画面へリダイレクト）
        del_url = reverse("deals:deal_delete_user", kwargs={"pk": self.deal.pk, "user_rel_id": du.id})
        del_res = self.client.post(del_url, data={"next": manage_url})
        self.assertRedirects(del_res, manage_url)
        self.assertFalse(DealUser.objects.filter(id=du.id).exists())

    def test_deal_persons_one_click_add_and_batch_update(self):
        """社外関係者のワンクリック追加および一括更新（編集モード）のテスト。"""
        self.client.login(username="deal_owner", password="password")
        manage_url = reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk})

        # 1. 2名のパーソンを作成
        p1 = Person.objects.create()
        Contact.objects.create(person=p1, last_name="田中", first_name="一郎")
        p1.primary_contact = Contact.objects.get(person=p1)
        p1.save()

        p2 = Person.objects.create()
        Contact.objects.create(person=p2, last_name="佐藤", first_name="二郎")
        p2.primary_contact = Contact.objects.get(person=p2)
        p2.save()

        # ワンクリック追加 (p1: role=contact_window, memo="")
        add_url = reverse("deals:deal_add_person", kwargs={"pk": self.deal.pk})
        self.client.post(add_url, data={
            "person": str(p1.id),
            "role": "contact_window",
            "memo": "",
            "next": manage_url,
        })
        # ワンクリック追加 (p2: role=contact_window, memo="")
        self.client.post(add_url, data={
            "person": str(p2.id),
            "role": "contact_window",
            "memo": "",
            "next": manage_url,
        })

        dp1 = DealPerson.objects.get(deal=self.deal, person=p1)
        dp2 = DealPerson.objects.get(deal=self.deal, person=p2)
        self.assertEqual(dp1.role, PersonRole.CONTACT_WINDOW)
        self.assertEqual(dp2.role, PersonRole.CONTACT_WINDOW)

        # 2. 一括編集モード表示確認 (?edit=1)
        edit_res = self.client.get(f"{manage_url}?edit=1")
        self.assertEqual(edit_res.status_code, 200)
        self.assertTrue(edit_res.context["is_edit_mode"])
        edit_html = edit_res.content.decode("utf-8")
        self.assertIn(f'name="person_{dp1.id}_role"', edit_html)
        self.assertIn(f'name="person_{dp2.id}_role"', edit_html)
        self.assertIn("適用", edit_html)
        self.assertIn("キャンセル", edit_html)

        # 3. 一括更新POST (dp1: decision_maker, dp2: technical)
        update_res = self.client.post(manage_url, data={
            f"person_{dp1.id}_exists": "1",
            f"person_{dp1.id}_role": PersonRole.DECISION_MAKER,
            f"person_{dp1.id}_memo": "決裁者メモ",
            f"person_{dp2.id}_exists": "1",
            f"person_{dp2.id}_role": PersonRole.TECHNICAL,
            f"person_{dp2.id}_memo": "技術担当メモ",
        })
        self.assertRedirects(update_res, manage_url)

        dp1.refresh_from_db()
        dp2.refresh_from_db()
        self.assertEqual(dp1.role, PersonRole.DECISION_MAKER)
        self.assertEqual(dp1.memo, "決裁者メモ")
        self.assertEqual(dp2.role, PersonRole.TECHNICAL)
        self.assertEqual(dp2.memo, "技術担当メモ")

    def test_deal_users_one_click_add_and_batch_update(self):
        """社内担当者のワンクリック追加および一括更新（編集モード）のテスト。"""
        self.client.login(username="deal_owner", password="password")
        manage_url = reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk})

        u1 = User.objects.create_user(username="batch_u1", password="password")
        u2 = User.objects.create_user(username="batch_u2", password="password")

        # ワンクリック追加 (u1: role=support, can_edit=on, memo="")
        add_url = reverse("deals:deal_add_user", kwargs={"pk": self.deal.pk})
        self.client.post(add_url, data={
            "user": str(u1.id),
            "role": "support",
            "can_edit": "on",
            "memo": "",
            "next": manage_url,
        })
        # ワンクリック追加 (u2: role=support, can_edit=on, memo="")
        self.client.post(add_url, data={
            "user": str(u2.id),
            "role": "support",
            "can_edit": "on",
            "memo": "",
            "next": manage_url,
        })

        du1 = DealUser.objects.get(deal=self.deal, user=u1)
        du2 = DealUser.objects.get(deal=self.deal, user=u2)
        self.assertEqual(du1.role, UserRole.SUPPORT)
        self.assertTrue(du1.can_edit)
        self.assertEqual(du2.role, UserRole.SUPPORT)
        self.assertTrue(du2.can_edit)

        # 2. 一括編集モード表示確認 (?edit=1)
        edit_res = self.client.get(f"{manage_url}?edit=1")
        self.assertEqual(edit_res.status_code, 200)
        self.assertTrue(edit_res.context["is_edit_mode"])
        edit_html = edit_res.content.decode("utf-8")
        self.assertIn(f'name="user_{du1.id}_role"', edit_html)
        self.assertIn(f'name="user_{du2.id}_role"', edit_html)
        self.assertIn("適用", edit_html)
        self.assertIn("キャンセル", edit_html)

        # 3. 一括更新POST (du1: approver, can_edit=True; du2: viewer, can_edit=False)
        update_res = self.client.post(manage_url, data={
            f"user_{du1.id}_exists": "1",
            f"user_{du1.id}_role": UserRole.APPROVER,
            f"user_{du1.id}_can_edit": "1",
            f"user_{du1.id}_memo": "最終承認者",
            f"user_{du2.id}_exists": "1",
            f"user_{du2.id}_role": UserRole.OBSERVER,
            # can_editはチェックなし -> False
            f"user_{du2.id}_memo": "閲覧専用メンバー",
        })
        self.assertRedirects(update_res, manage_url)

        du1.refresh_from_db()
        du2.refresh_from_db()
        self.assertEqual(du1.role, UserRole.APPROVER)
        self.assertTrue(du1.can_edit)
        self.assertEqual(du1.memo, "最終承認者")
        self.assertEqual(du2.role, UserRole.OBSERVER)
        self.assertFalse(du2.can_edit)
        self.assertEqual(du2.memo, "閲覧専用メンバー")

    def test_deal_create_view_with_comma_amount(self):
        """カンマ付き金額（例: 10,500,000）をPOSTして正常に数値として作成されることを検証。"""
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_create")
        post_data = {
            "name": "カンマ付き金額案件",
            "company": str(self.company.id),
            "stage": Stage.INITIAL_MEETING,
            "probability": 50,
            "deal_type": DealType.NEW,
            "amount": "10,500,000",
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        created_deal = Deal.objects.get(name="カンマ付き金額案件")
        self.assertEqual(created_deal.amount, Decimal("10500000"))

    def test_deal_update_view_with_comma_amount(self):
        """カンマ付き金額（例: 7,800,000）をPOSTして正常に数値として更新されることを検証。"""
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_update", kwargs={"pk": self.deal.pk})
        post_data = {
            "name": "基幹システム導入（カンマ更新）",
            "company": str(self.company.id),
            "stage": Stage.UNDER_REVIEW,
            "probability": 75,
            "deal_type": DealType.EXPANSION,
            "amount": "7,800,000",
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.name, "基幹システム導入（カンマ更新）")
        self.assertEqual(self.deal.amount, Decimal("7800000"))

    def test_deal_forms_amount_clean(self):
        """DealCreateForm および DealUpdateForm がカンマ付き文字列をDecimalに正規化することを単体検証。"""
        from deals.forms import DealCreateForm, DealUpdateForm

        # DealCreateForm
        create_form = DealCreateForm(data={
            "name": "フォームテスト案件",
            "company": str(self.company.id),
            "stage": Stage.INITIAL_MEETING,
            "amount": "99,999,999",
        })
        self.assertTrue(create_form.is_valid(), create_form.errors)
        self.assertEqual(create_form.cleaned_data["amount"], Decimal("99999999"))

        # DealUpdateForm
        update_form = DealUpdateForm(instance=self.deal, data={
            "name": "フォームテスト案件2",
            "company": str(self.company.id),
            "stage": Stage.UNDER_REVIEW,
            "amount": "1,234,567",
        })
        self.assertTrue(update_form.is_valid(), update_form.errors)
        self.assertEqual(update_form.cleaned_data["amount"], Decimal("1234567"))

    def test_deal_form_amount_inputmode_and_layout(self):
        """案件フォームでamountにinputmode="numeric"が設定され、カンマ自動整形スクリプトが存在することを検証。"""
        self.client.login(username="deal_owner", password="password")

        create_res = self.client.get(reverse("deals:deal_create"))
        self.assertEqual(create_res.status_code, 200)
        self.assertContains(create_res, 'inputmode="numeric"')
        self.assertContains(create_res, 'toLocaleString')

        update_res = self.client.get(reverse("deals:deal_update", kwargs={"pk": self.deal.pk}))
        self.assertEqual(update_res.status_code, 200)
        self.assertContains(update_res, 'inputmode="numeric"')
        self.assertContains(update_res, 'toLocaleString')

    def test_custom_user_display_name_property(self):
        """CustomUser.display_name プロパティの動作検証（Person紐付き、get_full_name、username）。"""
        # 1. Person紐付き時 -> Personの表示名（姓・名）
        person = Person.objects.create()
        Contact.objects.create(person=person, last_name="山田", first_name="花子")
        user_with_person = User.objects.create_user(
            username="user_p", password="x", person=person
        )
        self.assertEqual(user_with_person.display_name, "山田 花子")

        # 2. Person未紐付きだが first_name/last_name あり -> get_full_name()
        user_with_name = User.objects.create_user(
            username="user_fn", password="x", first_name="次郎", last_name="佐藤"
        )
        self.assertEqual(user_with_name.display_name, user_with_name.get_full_name())

        # 3. どちらもなし -> username
        user_plain = User.objects.create_user(username="user_plain", password="x")
        self.assertEqual(user_plain.display_name, "user_plain")

    def test_deal_detail_layout_and_styles(self):
        """案件詳細画面のレイアウト・スタイル（max-width, 案件名通常色, カンマ表示, 管理情報見出し等）を検証。"""
        # owner に Person を紐づけて表示名を検証できるようにする
        owner_person = Person.objects.create()
        Contact.objects.create(person=owner_person, last_name="鈴木", first_name="一郎")
        self.owner.person = owner_person
        self.owner.save(update_fields=["person"])

        self.client.login(username="deal_owner", password="password")
        response = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        # 1. コンテナ最大幅の縮小・左寄せ
        self.assertIn("max-width: 1040px;", html)
        self.assertIn("margin-left: 0; margin-right: auto;", html)

        # 2. 「基本情報」タイトルの撤去
        self.assertNotIn("基本情報", html)

        # 3. 案件名のスタイル（青字ではないこと）
        self.assertNotIn("color:var(--app-color-primary, #1e40af);", html)
        self.assertIn("color:var(--app-text, #0f172a);", html)
        self.assertIn(self.deal.name, html)

        # 4. 会社（左側メインカードに存在）
        self.assertIn("会社:", html)
        self.assertIn(self.company.organization, html)

        # 5. 金額の3桁カンマ区切り表示（5000000 -> ¥5,000,000）
        self.assertIn("¥5,000,000", html)

        # 6. 「予想売上」が完全撤去されていること
        self.assertNotIn("予想売上", html)

        # 7. 管理情報カードの撤去、担当者の合流・Person表示名・メタ情報の存在確認（仕様書用語準拠）
        self.assertNotIn("管理情報", html)
        self.assertIn("担当者:", html)
        self.assertNotIn("社内主担当者:", html)
        self.assertIn("鈴木 一郎", html)
        self.assertIn("作成者:", html)
        self.assertIn("更新日時:", html)

        # 8. 仕様書正本準拠の用語
        self.assertIn("受注予定日:", html)
        self.assertIn("種別:", html)
        self.assertIn("案件発生源:", html)
        self.assertIn("発生源キャンペーン:", html)
        self.assertIn("概要メモ:", html)
        self.assertIn("案件関係者（社外）", html)
        self.assertIn("案件担当者（社内）", html)
        self.assertIn("実施日時", html)
        self.assertIn("実施者", html)
        self.assertIn("内容メモ", html)

        # 9. 項目の並び順（案件名 -> 会社 -> 担当者 -> 金額 -> 種別 -> 概要メモ -> 作成者）
        idx_name = html.index(self.deal.name)
        idx_company = html.index("会社:")
        idx_owner = html.index("担当者:")
        idx_amount = html.index("¥5,000,000")
        idx_type = html.index("種別:")
        idx_memo = html.index("概要メモ:")
        idx_creator = html.index("作成者:")
        self.assertTrue(idx_name < idx_company < idx_owner < idx_amount < idx_type < idx_memo < idx_creator)

        # 10. 活動履歴が案件関係者・案件担当者の下に配置されていること
        idx_persons = html.index("案件関係者（社外）")
        idx_users = html.index("案件担当者（社内）")
        idx_activities = html.index("活動履歴")
        self.assertTrue(idx_persons < idx_activities)
        self.assertTrue(idx_users < idx_activities)

    def test_deal_detail_memo_styling_and_modal(self):
        """案件詳細の概要メモ表示において、グレー背景枠が撤去され、モーダル構造が存在することを検証。"""
        self.deal.memo = "これは案件の概要メモです。\n複数行のテスト文章。\n3行目。\n4行目。\n5行目。"
        self.deal.save(update_fields=["memo"])

        self.client.login(username="deal_owner", password="password")
        res = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # 1. グレー背景枠の装飾が撤去されていること
        self.assertNotIn("background:#f8fafc;", html)
        self.assertNotIn("background: #f8fafc;", html)

        # 2. 概要メモ要素と「全て表示」ボタンラッパーが存在すること
        self.assertIn('id="deal-memo-text"', html)
        self.assertIn('id="deal-memo-more-btn-wrapper"', html)
        self.assertIn('id="deal-memo-more-btn"', html)

        # 3. モーダルおよびバックドロップが存在し、全文が表示されること
        self.assertIn('id="dealMemoModal"', html)
        self.assertIn('id="dealMemoModalBackdrop"', html)
        self.assertIn('data-action="close-memo-modal"', html)
        self.assertIn("これは案件の概要メモです。", html)

    def test_deal_detail_relations_tables_format(self):
        """案件詳細の社外関係者・社内担当者が app-table 形式で描画されることを検証。"""
        self.client.login(username="deal_owner", password="password")

        # 1. 関係者・担当者が空の状態
        DealUser.objects.filter(deal=self.deal).delete()
        res_empty = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(res_empty.status_code, 200)
        html_empty = res_empty.content.decode("utf-8")
        self.assertIn("関係者は登録されていません。", html_empty)
        self.assertIn("担当者は登録されていません。", html_empty)

        # 2. 関係者・担当者を登録して取得
        person = Person.objects.create()
        contact = Contact.objects.create(person=person, last_name="佐藤", first_name="次郎", organization="テスト株式会社", department="営業推進部")
        person.primary_contact = contact
        person.save()
        dp = DealPerson.objects.create(deal=self.deal, person=person, role=PersonRole.DECISION_MAKER)

        dept = Department.objects.create(name="開発本部")
        user_team = User.objects.create_user(username="team_user", password="password", department=dept)
        du = DealUser.objects.create(deal=self.deal, user=user_team, role=UserRole.PRIMARY, can_edit=True)

        res = self.client.get(reverse("deals:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # テーブル構成の確認（社外：会社名・部署・名前・権限、社内：部署名・名前・役割・編集可否）
        self.assertIn('<table class="app-table"', html)
        self.assertIn("会社名</th>", html)
        self.assertIn("部署</th>", html)
        self.assertIn("名前</th>", html)
        self.assertIn("権限</th>", html)
        self.assertIn("部署名</th>", html)
        self.assertIn("役割</th>", html)
        self.assertIn("編集可否</th>", html)

        # 案件関係者（社外）の確認
        self.assertIn(reverse("persons:person_detail", kwargs={"pk": person.pk}), html)
        self.assertIn("テスト株式会社", html)
        self.assertIn("営業推進部", html)
        self.assertIn("決裁者", html)

        # 案件担当者（社内）の確認
        self.assertIn("開発本部", html)
        self.assertIn(user_team.display_name, html)
        self.assertIn("主担当", html)
        self.assertIn("編集可", html)

    def test_deal_user_manage_search_and_add_owner(self):
        """社内担当者管理画面で q=iwata や漢字氏名で検索し、案件オーナー自身も DealUser として追加できることを検証。"""
        # iwata ユーザー作成、Person（岩田 好史）を紐付けて案件オーナーに設定
        person = Person.objects.create()
        contact = Contact.objects.create(person=person, last_name="岩田", first_name="好史")
        person.primary_contact = contact
        person.save(update_fields=["primary_contact"])
        user_iwata = User.objects.create_user(
            username="iwata", email="iwata@example.com", password="password", person=person
        )
        user_iwata.user_permissions.add(self.perm_change)
        self.deal.owner = user_iwata
        self.deal.save(update_fields=["owner"])

        self.client.login(username="iwata", password="password")
        url = reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk})

        # 1. q=iwata で検索
        res_username = self.client.get(f"{url}?q=iwata")
        self.assertEqual(res_username.status_code, 200)
        self.assertIn(user_iwata, res_username.context["candidate_users"])
        html_username = res_username.content.decode("utf-8")
        self.assertIn("岩田 好史", html_username)
        self.assertIn("@iwata", html_username)
        self.assertIn(">追加</button>", html_username)
        self.assertIn(">追加</button>", html_username)

        # 2. q=岩田 で検索（Personの氏名検索）
        res_kanji = self.client.get(f"{url}?q=岩田")
        self.assertEqual(res_kanji.status_code, 200)
        self.assertIn(user_iwata, res_kanji.context["candidate_users"])
        html_kanji = res_kanji.content.decode("utf-8")
        self.assertIn("岩田 好史", html_kanji)
        self.assertIn("@iwata", html_kanji)

        # 3. 案件オーナーを DealUser に実際に追加（POST）
        add_url = reverse("deals:deal_add_user", kwargs={"pk": self.deal.pk})
        post_res = self.client.post(add_url, data={
            "user": str(user_iwata.id),
            "role": UserRole.SUPPORT,
            "can_edit": "on",
            "memo": "オーナー兼サポート",
            "next": url,
        })
        self.assertRedirects(post_res, url)

        # DealUser レコードが作成され、full_clean も通ることを検証
        du = DealUser.objects.get(deal=self.deal, user=user_iwata)
        self.assertEqual(du.role, UserRole.SUPPORT)
        self.assertEqual(du.memo, "オーナー兼サポート")
        du.full_clean()  # バリデーションエラーが起きないこと

    def test_deal_users_and_persons_manage_batch_edit_buttons_and_actions(self):
        """社内担当者および社外関係者管理画面の一括編集で、上部アクションボタンが撤去され下部のみ存在し正常に動作することを検証。"""
        self.client.login(username="deal_owner", password="password")

        # 準備: DealUser & DealPerson 作成
        user_sub = User.objects.create_user(username="sub_user", password="password")
        du = DealUser.objects.create(deal=self.deal, user=user_sub, role=UserRole.SUPPORT, memo="初期メモ")

        person = Person.objects.create()
        Contact.objects.create(person=person, last_name="山田", first_name="太郎")
        dp = DealPerson.objects.create(deal=self.deal, person=person, role=PersonRole.CONTACT_WINDOW, memo="初期関係メモ")

        # 1. 社内担当者管理画面
        users_url = reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk})

        # 1-1. 通常モード: 一括編集ボタンが表示される
        res_normal_users = self.client.get(users_url)
        self.assertEqual(res_normal_users.status_code, 200)
        self.assertContains(res_normal_users, "一括編集")

        # 1-2. 編集モード (?edit=1): 上部ボタンが撤去され、ボタンは最下部1箇所のみ
        res_edit_users = self.client.get(f"{users_url}?edit=1")
        self.assertEqual(res_edit_users.status_code, 200)
        html_users = res_edit_users.content.decode("utf-8")
        # 「適用」ボタンが1つだけ存在すること（上部撤去、下部のみ）
        self.assertEqual(html_users.count(">適用</button>"), 1)
        # 上部の「form="batch-update-users-form"」が存在しないこと
        self.assertNotIn('form="batch-update-users-form"', html_users)

        # 1-3. 一括更新 POST が正常に動作すること
        post_users_res = self.client.post(users_url, data={
            f"user_{du.id}_exists": "1",
            f"user_{du.id}_role": UserRole.APPROVER,
            f"user_{du.id}_can_edit": "1",
            f"user_{du.id}_memo": "更新後担当メモ",
        })
        self.assertRedirects(post_users_res, users_url)
        du.refresh_from_db()
        self.assertEqual(du.role, UserRole.APPROVER)
        self.assertTrue(du.can_edit)
        self.assertEqual(du.memo, "更新後担当メモ")

        # 2. 社外関係者管理画面
        persons_url = reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk})

        # 2-1. 通常モード: 一括編集ボタンが表示される
        res_normal_persons = self.client.get(persons_url)
        self.assertEqual(res_normal_persons.status_code, 200)
        self.assertContains(res_normal_persons, "一括編集")

        # 2-2. 編集モード (?edit=1): 上部ボタンが撤去され、ボタンは最下部1箇所のみ
        res_edit_persons = self.client.get(f"{persons_url}?edit=1")
        self.assertEqual(res_edit_persons.status_code, 200)
        html_persons = res_edit_persons.content.decode("utf-8")
        # 「適用」ボタンが1つだけ存在すること（上部撤去、下部のみ）
        self.assertEqual(html_persons.count(">適用</button>"), 1)
        # 上部の「form="batch-update-persons-form"」が存在しないこと
        self.assertNotIn('form="batch-update-persons-form"', html_persons)

        # 2-3. 一括更新 POST が正常に動作すること
        post_persons_res = self.client.post(persons_url, data={
            f"person_{dp.id}_exists": "1",
            f"person_{dp.id}_role": PersonRole.DECISION_MAKER,
            f"person_{dp.id}_memo": "更新後関係メモ",
        })
        self.assertRedirects(post_persons_res, persons_url)
        dp.refresh_from_db()
        self.assertEqual(dp.role, PersonRole.DECISION_MAKER)
        self.assertEqual(dp.memo, "更新後関係メモ")

    def test_deal_manage_views_layout_and_back_button_normalization(self):
        """社内担当者および社外関係者管理画面の左寄せ、戻るボタンの重複・矢印撤去、案件詳細の幅設定を検証。"""
        self.client.login(username="deal_owner", password="password")

        # 1. 社内担当者管理画面
        users_url = reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk})
        res_users = self.client.get(users_url)
        self.assertEqual(res_users.status_code, 200)
        html_users = res_users.content.decode("utf-8")
        # 左寄せスタイル
        self.assertIn("margin-left: 0; margin-right: auto;", html_users)
        self.assertNotIn("margin:0 auto;", html_users)
        self.assertNotIn("margin: 0 auto;", html_users)
        # 手書きの矢印付き戻るボタンが撤去されていること
        self.assertNotIn("← 戻る", html_users)
        # 戻るボタンが1つだけ存在すること（フォールバック時）
        self.assertEqual(html_users.count(">戻る</a>"), 1)

        # 2. 社外関係者管理画面
        persons_url = reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk})
        res_persons = self.client.get(persons_url)
        self.assertEqual(res_persons.status_code, 200)
        html_persons = res_persons.content.decode("utf-8")
        # 左寄せスタイル
        self.assertIn("margin-left: 0; margin-right: auto;", html_persons)
        self.assertNotIn("margin:0 auto;", html_persons)
        self.assertNotIn("margin: 0 auto;", html_persons)
        # 手書きの矢印付き戻るボタンが撤去されていること
        self.assertNotIn("← 戻る", html_persons)
        # 戻るボタンが1つだけ存在すること（フォールバック時）
        self.assertEqual(html_persons.count(">戻る</a>"), 1)

        # 3. 案件詳細画面（コンテナ幅1040px・ラベル幅96px・テーブル列幅固定・ellipsis）
        detail_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        html_detail = res_detail.content.decode("utf-8")
        self.assertIn("deal-detail-container", html_detail)
        self.assertIn("max-width: 1040px; margin-left: 0; margin-right: auto; width: 100%;", html_detail)
        self.assertIn("min-width:96px; width:96px;", html_detail)
        self.assertIn('table-layout: fixed; width: 100%;', html_detail)
        self.assertIn('width: 30%;', html_detail)
        self.assertIn('width: 25%;', html_detail)
        self.assertIn('width: 20%;', html_detail)
        self.assertIn('overflow: hidden; text-overflow: ellipsis;', html_detail)

    def test_deal_detail_push_current_stack(self):
        """DealDetailView が push_current を呼び出し、子画面リンクに back_stack が付与され、子画面から戻れることを検証。"""
        self.client.login(username="deal_owner", password="password")

        # 1. 案件詳細画面を GET
        detail_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)

        # BackNavigator インスタンスの検証
        back = res_detail.context["back"]
        self.assertTrue(back._pushed)
        self.assertTrue(len(back.back_stack) > 0)
        top_entry = back.back_stack[-1]
        self.assertEqual(top_entry["title"], f"案件: {self.deal.name}")
        self.assertEqual(top_entry["url"], detail_url)

        # 2. 詳細画面内の担当者管理リンクに back_stack が含まれていること
        html_detail = res_detail.content.decode("utf-8")
        self.assertIn("back_stack=", html_detail)

        # 3. 付与されたリンクで担当者管理画面へアクセスした場合、戻るボタンが案件詳細を指すこと
        users_manage_url = reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk})
        res_child = self.client.get(f"{users_manage_url}?back_stack={back._encode_stack()}")
        self.assertEqual(res_child.status_code, 200)
        html_child = res_child.content.decode("utf-8")
        # 戻るボタンが存在し、案件詳細URLをhrefに持つこと
        self.assertIn(f'href="{detail_url}"', html_child)
        self.assertIn(">戻る</a>", html_child)

    def test_deal_detail_company_link_preserves_back_stack(self):
        """案件詳細画面の会社リンクに back_stack が付与され、会社詳細の戻るボタンが案件詳細を指すことを検証。"""
        import re

        from activities.models import Activity, ActivityType
        from mailings.models import Campaign, EmailTemplate, MailingList

        self.client.login(username="deal_owner", password="password")

        # 関連データ作成（発生源キャンペーン、活動、社外関係者）
        ml = MailingList.objects.create(name="テストリスト", created_by=self.owner)
        template = EmailTemplate.objects.create(
            name="テンプレート", subject="件名", body="本文", created_by=self.owner
        )
        campaign = Campaign.objects.create(
            name="春のプロモ", template=template, mailing_list=ml, created_by=self.owner
        )
        self.deal.source_campaign = campaign
        self.deal.lead_source = Deal.LeadSource.CAMPAIGN
        self.deal.save(update_fields=["source_campaign", "lead_source"])

        person2 = Person.objects.create()
        company2 = Company.objects.create(organization="関係先株式会社")
        contact2 = Contact.objects.create(person=person2, company=company2, last_name="佐藤", first_name="健")
        person2.primary_contact = contact2
        person2.save(update_fields=["primary_contact"])
        DealPerson.objects.create(deal=self.deal, person=person2, role=PersonRole.DECISION_MAKER)

        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.owner,
            memo="電話ヒアリング",
        )

        # 1. 案件詳細画面を GET
        detail_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        html_detail = res_detail.content.decode("utf-8")

        # 2. 会社詳細リンクに ?back_stack= が含まれていること
        base_company_url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        pattern = rf'href="({re.escape(base_company_url)}\?back_stack=[^"]+)"'
        match = re.search(pattern, html_detail)
        self.assertIsNotNone(match, f"会社リンク（{base_company_url}）に ?back_stack= が含まれていません。")
        company_link_with_back = match.group(1)

        # 3. 発生源キャンペーンリンクに ?back_stack= が含まれていること
        base_campaign_url = reverse("mailings:campaign_detail", kwargs={"pk": campaign.pk})
        self.assertIn(f'href="{base_campaign_url}?back_stack=', html_detail)

        # 4. 主担当パーソンリンクに ?back_stack= が含まれていること
        base_person_url = reverse("persons:person_detail", kwargs={"pk": self.person.pk})
        self.assertIn(f'href="{base_person_url}?back_stack=', html_detail)

        # 5. 社外関係者テーブルの会社・パーソンリンクに ?back_stack= が含まれていること
        base_comp2_url = reverse("companies:company_detail", kwargs={"pk": company2.pk})
        base_p2_url = reverse("persons:person_detail", kwargs={"pk": person2.pk})
        self.assertIn(f'href="{base_comp2_url}?back_stack=', html_detail)
        self.assertIn(f'href="{base_p2_url}?back_stack=', html_detail)

        # 6. 活動履歴の詳細リンクに ?back_stack= が含まれていること
        base_act_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        self.assertIn(f'href="{base_act_url}?back_stack=', html_detail)

        # 7. 案件詳細から生成されたリンクで会社詳細（company_detail）にアクセス
        res_company = self.client.get(company_link_with_back)
        self.assertEqual(res_company.status_code, 200)

        # 8. 会社詳細のコンテキスト/画面の戻る導線が当該案件詳細を指していること
        back = res_company.context["back"]
        self.assertTrue(back.back_exist)
        self.assertEqual(back.back_url, detail_url)

        html_company = res_company.content.decode("utf-8")
        self.assertIn(f'href="{detail_url}"', html_company)
        self.assertIn(">戻る</a>", html_company)

    def test_activity_detail_links_preserve_back_stack(self):
        """活動詳細画面の関連案件・キャンペーン・関係者リンクに back_stack が付与され、遷移先から戻れることを検証。"""
        import re

        from activities.models import Activity, ActivityPerson, ActivityType
        from mailings.models import Campaign, EmailTemplate, MailingList

        self.client.login(username="deal_owner", password="password")

        ml = MailingList.objects.create(name="テストリスト2", created_by=self.owner)
        template = EmailTemplate.objects.create(
            name="テンプレート2", subject="件名2", body="本文2", created_by=self.owner
        )
        campaign = Campaign.objects.create(
            name="夏キャンペーン", template=template, mailing_list=ml, created_by=self.owner
        )

        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.VISIT,
            occurred_at=timezone.now(),
            user=self.owner,
            memo="訪問商談",
        )

        activity_camp = Activity.objects.create(
            campaign=campaign,
            activity_type=ActivityType.EMAIL,
            occurred_at=timezone.now(),
            user=self.owner,
            memo="メール送信",
        )

        person3 = Person.objects.create()
        comp3 = Company.objects.create(organization="関係社3")
        c3 = Contact.objects.create(person=person3, company=comp3, last_name="高橋", first_name="修")
        person3.primary_contact = c3
        person3.save(update_fields=["primary_contact"])
        ActivityPerson.objects.create(activity=activity, person=person3)

        # 1. 案件紐付きの活動詳細画面を GET
        act_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        res_act = self.client.get(act_detail_url)
        self.assertEqual(res_act.status_code, 200)
        html_act = res_act.content.decode("utf-8")

        # 2. 関連案件リンクに ?back_stack= が含まれていること
        deal_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        pattern = rf'href="({re.escape(deal_url)}\?back_stack=[^"]+)"'
        match = re.search(pattern, html_act)
        self.assertIsNotNone(match, f"関連案件リンク（{deal_url}）に ?back_stack= が含まれていません。")
        deal_link_with_back = match.group(1)

        # 3. 相手方関係者の会社・パーソンリンクに ?back_stack= が含まれていること
        base_comp3_url = reverse("companies:company_detail", kwargs={"pk": comp3.pk})
        base_p3_url = reverse("persons:person_detail", kwargs={"pk": person3.pk})
        self.assertIn(f'href="{base_comp3_url}?back_stack=', html_act)
        self.assertIn(f'href="{base_p3_url}?back_stack=', html_act)

        # 4. キャンペーン紐付きの活動詳細画面で関連キャンペーンリンクに ?back_stack= が含まれていること
        res_camp_act = self.client.get(reverse("activities:activity_detail", kwargs={"pk": activity_camp.pk}))
        self.assertEqual(res_camp_act.status_code, 200)
        html_camp_act = res_camp_act.content.decode("utf-8")
        base_camp_url = reverse("mailings:campaign_detail", kwargs={"pk": campaign.pk})
        self.assertIn(f'href="{base_camp_url}?back_stack=', html_camp_act)

        # 5. 関連案件リンクで案件詳細に遷移した場合、戻るボタンが当該活動詳細を指していること
        res_deal = self.client.get(deal_link_with_back)
        self.assertEqual(res_deal.status_code, 200)
        back = res_deal.context["back"]
        self.assertTrue(back.back_exist)
        self.assertEqual(back.back_url, act_detail_url)

        html_deal = res_deal.content.decode("utf-8")
        self.assertIn(f'href="{act_detail_url}"', html_deal)
        self.assertIn(">戻る</a>", html_deal)
class DealCreateInitialParamTests(TestCase):
    """DealCreateView の ?company= および ?person= パラメータ連携テスト"""

    def setUp(self):
        self.user = User.objects.create_user(username="test_deal_user", password="password")
        perm_add = Permission.objects.get(codename="add_deal")
        perm_change = Permission.objects.get(codename="change_deal")
        self.user.user_permissions.add(perm_add, perm_change)
        self.client.login(username="test_deal_user", password="password")

        self.company = Company.objects.create(organization="株式会社テスト商事")
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            company=self.company,
            last_name="山田",
            first_name="花子",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact"])

    def test_get_deal_create_with_company_param(self):
        url = f"{reverse('deals:deal_create')}?company={self.company.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        form = response.context["form"]
        self.assertEqual(str(form.initial.get("company")), str(self.company.id))
        self.assertEqual(response.context["selected_company"], self.company)

        content = response.content.decode("utf-8")
        self.assertIn(self.company.organization, content)

    def test_get_deal_create_with_person_param(self):
        url = f"{reverse('deals:deal_create')}?person={self.person.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        form = response.context["form"]
        # パーソンの所属会社が company に初期セットされる
        self.assertEqual(str(form.initial.get("company")), str(self.company.id))
        self.assertEqual(response.context["selected_person"], self.person)
        self.assertEqual(response.context["selected_company"], self.company)
        self.assertEqual(form.initial.get("primary_person"), self.person.id)

    def test_post_deal_create_with_person_param_creates_deal_and_deal_person(self):
        url = f"{reverse('deals:deal_create')}?person={self.person.id}"
        post_data = {
            "name": "パーソン起点案件",
            "company": str(self.company.id),
            "stage": Stage.INITIAL_MEETING,
            "probability": 50,
            "deal_type": DealType.NEW,
            "amount": 1000000,
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)

        deal = Deal.objects.get(name="パーソン起点案件")
        self.assertEqual(deal.company, self.company)
        self.assertEqual(deal.primary_person, self.person)
        self.assertTrue(DealPerson.objects.filter(deal=deal, person=self.person).exists())


class DealWizardTests(TestCase):
    """案件新規作成ウィザード（基本情報 ➔ 関係者設定フロー）のテスト"""

    def setUp(self):
        self.user = User.objects.create_user(username="wizard_user", password="password")
        perm_add = Permission.objects.get(codename="add_deal")
        perm_change = Permission.objects.get(codename="change_deal")
        perm_view = Permission.objects.get(codename="view_deal")
        perm_att = Permission.objects.get(codename="add_attachment")
        self.user.user_permissions.add(perm_add, perm_change, perm_view, perm_att)
        self.company = Company.objects.create(organization="ウィザード社")
        self.person = Person.objects.create()
        Contact.objects.create(
            person=self.person,
            company=self.company,
            first_name="太郎",
            last_name="山田",
        )
        self.client.login(username="wizard_user", password="password")

    def test_deal_wizard_4step_flow_and_titles(self):
        """案件新規作成ウィザードの 4ステップ（Step 1 ➔ Step 2 ➔ Step 3 ➔ Step 4 ➔ 詳細）遷移および各画面のタイトル表記・ボタン・BackNavigator検証。"""
        # Step 1: 基本情報入力画面
        nav = BackNavigator(self.client.get("/").wsgi_request)
        back_stack = nav._calc_encode_stack([{"url": reverse("deals:deal_list"), "title": "案件一覧"}])
        res1 = self.client.get(f"{reverse('deals:deal_create')}?back_stack={back_stack}")
        self.assertEqual(res1.status_code, 200)
        content1 = res1.content.decode("utf-8")
        self.assertIn("案件新規作成 (1/4) 基本情報", content1)
        self.assertContains(res1, "次へ")
        self.assertNotContains(res1, "スキップ")
        # ステップインジケーター検証: (現在)が無く、シンプル化された表記であること
        self.assertNotContains(res1, "(現在)")
        self.assertContains(res1, "1. 基本情報")
        # 日時クイック入力ボタン（今日・昨日）およびOKボタン
        self.assertContains(res1, "今日")
        self.assertContains(res1, "昨日")
        self.assertContains(res1, "OK")

        # Step 1 POST -> Step 2 へリダイレクト
        post_data = {
            "name": "ウィザード4ステップ案件",
            "company": str(self.company.id),
            "stage": Stage.INITIAL_MEETING,
            "probability": 50,
            "deal_type": DealType.NEW,
            "amount": 1000000,
            "back_stack": back_stack,
        }
        res_post = self.client.post(f"{reverse('deals:deal_create')}?back_stack={back_stack}", data=post_data)
        self.assertEqual(res_post.status_code, 302)
        deal = Deal.objects.get(name="ウィザード4ステップ案件")
        self.assertTrue(res_post.url.startswith(reverse("deals:deal_persons_manage", kwargs={"pk": deal.pk})))
        self.assertIn("wizard=1", res_post.url)

        # Step 2: 社外関係者設定画面
        step2_url = res_post.url
        res2 = self.client.get(step2_url)
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.context.get("is_wizard"))
        content2 = res2.content.decode("utf-8")
        self.assertIn("案件関係者設定 (2/4) 社外関係者", content2)
        self.assertContains(res2, "次へ")
        self.assertNotContains(res2, "スキップ")
        self.assertNotContains(res2, "(現在)")
        self.assertContains(res2, "2. 社外関係者")
        # BackNavigator: 直前の Step 1 への戻るリンクが存在すること
        self.assertTrue(res2.context["back"].back_exist)

        # Step 3: 社内担当者設定画面
        step3_base = reverse("deals:deal_users_manage", kwargs={"pk": deal.pk}) + "?wizard=1"
        step3_url = res2.context["back"].append_url(step3_base)
        res3 = self.client.get(step3_url)
        self.assertEqual(res3.status_code, 200)
        self.assertTrue(res3.context.get("is_wizard"))
        content3 = res3.content.decode("utf-8")
        self.assertIn("案件関係者設定 (3/4) 社内担当者", content3)
        self.assertContains(res3, "次へ")
        self.assertNotContains(res3, "スキップ")
        self.assertNotContains(res3, "(現在)")
        self.assertContains(res3, "3. 社内担当者")
        # BackNavigator: 直前の Step 2 への戻るリンクが存在すること
        self.assertTrue(res3.context["back"].back_exist)

        # Step 4: 添付ファイル設定画面
        step4_base = reverse("deals:deal_attachments_manage", kwargs={"pk": deal.pk}) + "?wizard=1"
        step4_url = res3.context["back"].append_url(step4_base)
        res4 = self.client.get(step4_url)
        self.assertEqual(res4.status_code, 200)
        self.assertTrue(res4.context.get("is_wizard"))
        content4 = res4.content.decode("utf-8")
        self.assertIn("案件ファイル添付 (4/4) 添付ファイル", content4)
        self.assertContains(res4, "保存")
        self.assertNotContains(res4, "スキップ")
        self.assertNotContains(res4, "(現在)")
        self.assertContains(res4, "4. 添付ファイル")
        # BackNavigator: 直前の Step 3 への戻るリンクが存在すること
        self.assertTrue(res4.context["back"].back_exist)

        # 詳細画面へ遷移
        detail_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)

    def test_deal_wizard_step3_add_and_delete_user(self):
        """Step 3（社内担当者設定）においてユーザーの追加・削除が正常に動作し、wizard=1 が維持されること。"""
        deal = Deal.objects.create(
            name="Step3担当者検証案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
        )
        target_user = User.objects.create_user(username="team_member_1", password="password")

        step3_url = reverse("deals:deal_users_manage", kwargs={"pk": deal.pk}) + "?wizard=1"

        # 追加 POST
        add_url = reverse("deals:deal_add_user", kwargs={"pk": deal.pk})
        add_data = {
            "next": step3_url,
            "user": str(target_user.id),
            "role": "support",
            "can_edit": "on",
            "memo": "サポート担当",
        }
        add_res = self.client.post(add_url, data=add_data)
        self.assertEqual(add_res.status_code, 302)
        self.assertEqual(add_res.url, step3_url)

        deal_user = DealUser.objects.filter(deal=deal, user=target_user).first()
        self.assertIsNotNone(deal_user)
        self.assertEqual(deal_user.memo, "サポート担当")

        # 削除 POST
        del_url = reverse("deals:deal_delete_user", kwargs={"pk": deal.pk, "user_rel_id": deal_user.id})
        del_res = self.client.post(del_url, data={"next": step3_url})
        self.assertEqual(del_res.status_code, 302)
        self.assertEqual(del_res.url, step3_url)
        self.assertFalse(DealUser.objects.filter(deal=deal, user=target_user).exists())

    def test_deal_wizard_step4_upload_attachment(self):
        """Step 4（添付ファイル管理画面）においてファイルが正常にアップロード・紐付け保存され、詳細画面等へ遷移できること。"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from attachments.models import Attachment

        deal = Deal.objects.create(
            name="Step4添付検証案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
        )
        step4_url = reverse("deals:deal_attachments_manage", kwargs={"pk": deal.pk}) + "?wizard=1"
        detail_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})

        test_file = SimpleUploadedFile("quote_doc.pdf", b"sample content", content_type="application/pdf")
        post_data = {
            "deal_id": str(deal.id),
            "next": detail_url,
            "memo": "見積書",
            "files": test_file,
        }
        res = self.client.post(step4_url, data=post_data)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, detail_url)

        att = Attachment.objects.filter(deal=deal).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.original_filename, "quote_doc.pdf")
        self.assertEqual(att.memo, "見積書")
        self.assertEqual(att.uploaded_by, self.user)

        # ファイル未選択で保存した場合はエラーにならず詳細画面へリダイレクトされること
        empty_res = self.client.post(step4_url, data={"deal_id": str(deal.id), "next": detail_url})
        self.assertEqual(empty_res.status_code, 302)
        self.assertEqual(empty_res.url, detail_url)



class DealPersonMergeTests(TestCase):
    """Personマージ時のDeal / DealPerson付け替え・重複解消および表示安全化テスト"""

    def setUp(self):
        self.user = User.objects.create_user(username="merge_deal_user", password="password")
        perm_add = Permission.objects.get(codename="add_deal")
        perm_change = Permission.objects.get(codename="change_deal")
        perm_view = Permission.objects.get(codename="view_deal")
        self.user.user_permissions.add(perm_add, perm_change, perm_view)
        self.client.login(username="merge_deal_user", password="password")

        self.company = Company.objects.create(organization="テスト商事")

        # マージ元（source）
        self.source_person = Person.objects.create()
        self.source_contact = Contact.objects.create(
            person=self.source_person,
            company=self.company,
            last_name="山田",
            first_name="太郎",
            full_name="山田 太郎",
            status=Contact.Status.PRIMARY,
        )
        self.source_person.primary_contact = self.source_contact
        self.source_person.save(update_fields=["primary_contact"])

        # マージ先（target / surviving）
        self.target_person = Person.objects.create()
        self.target_contact = Contact.objects.create(
            person=self.target_person,
            company=self.company,
            last_name="山田",
            first_name="太郎（本）",
            full_name="山田 太郎（本）",
            status=Contact.Status.PRIMARY,
        )
        self.target_person.primary_contact = self.target_contact
        self.target_person.save(update_fields=["primary_contact"])

    def test_deal_person_merge_transfer_and_deduplication(self):
        """マージ時に Deal.primary_person および DealPerson がマージ先へ移行し、重複が解消されること。"""
        # deal1: primary_person が source で、target の DealPerson が既に存在
        deal1 = Deal.objects.create(
            name="マージ案件1",
            company=self.company,
            primary_person=self.source_person,
            owner=self.user,
            created_by=self.user,
        )
        DealPerson.objects.create(
            deal=deal1,
            person=self.target_person,
            role=PersonRole.ATTENDEE,
        )

        # deal2: source と target 両方の DealPerson が存在（重複ケース）
        deal2 = Deal.objects.create(
            name="マージ案件2",
            company=self.company,
            owner=self.user,
            created_by=self.user,
        )
        DealPerson.objects.create(
            deal=deal2,
            person=self.source_person,
            role=PersonRole.CONTACT_WINDOW,
        )
        DealPerson.objects.create(
            deal=deal2,
            person=self.target_person,
            role=PersonRole.ATTENDEE,
        )

        # deal3: source の DealPerson のみ（通常移行ケース）
        deal3 = Deal.objects.create(
            name="マージ案件3",
            company=self.company,
            owner=self.user,
            created_by=self.user,
        )
        DealPerson.objects.create(
            deal=deal3,
            person=self.source_person,
            role=PersonRole.DECISION_MAKER,
        )

        # マージ実行
        from config.constants import DuplicateMergeReason
        self.source_person.transfer_contacts_to(
            self.target_person, [DuplicateMergeReason.SAME_CARD.value]
        )
        self.source_person.mark_as_merged(self.target_person)

        # 検証1: deal1 の primary_person が target になり、target の DealPerson は削除されること
        deal1.refresh_from_db()
        self.assertEqual(deal1.primary_person, self.target_person)
        self.assertFalse(deal1.deal_persons.filter(person=self.target_person).exists())

        # 検証2: deal2 の重複 DealPerson が解消され、target のみ 1件存在すること
        self.assertFalse(deal2.deal_persons.filter(person=self.source_person).exists())
        self.assertEqual(deal2.deal_persons.filter(person=self.target_person).count(), 1)

        # 検証3: deal3 の DealPerson が target に移行されていること
        self.assertFalse(deal3.deal_persons.filter(person=self.source_person).exists())
        self.assertEqual(deal3.deal_persons.filter(person=self.target_person).count(), 1)

        # 検証4: 案件詳細画面で「Person <UUID>」が生露出せず、「マージ済み」文字列も存在せず、関係者が正常に1件表示されること
        resp1 = self.client.get(reverse("deals:deal_detail", kwargs={"pk": deal1.pk}))
        self.assertEqual(resp1.status_code, 200)
        content1 = resp1.content.decode("utf-8")
        self.assertNotIn(f"Person {self.source_person.id}", content1)
        self.assertNotIn(f"Person {self.target_person.id}", content1)
        self.assertIn("山田 太郎（本）", content1)
        self.assertNotContains(resp1, "マージ済み")

        resp3 = self.client.get(reverse("deals:deal_detail", kwargs={"pk": deal3.pk}))
        self.assertEqual(resp3.status_code, 200)
        content3 = resp3.content.decode("utf-8")
        self.assertNotIn(f"Person {self.source_person.id}", content3)
        self.assertNotIn(f"Person {self.target_person.id}", content3)
        self.assertIn("山田 太郎（本）", content3)
        self.assertNotContains(resp3, "マージ済み")
        self.assertEqual(len(resp3.context["deal_persons"]), 1)

    def test_deal_detail_completely_excludes_merged_person_records(self):
        """中間テーブルに merged Person が残存している場合でも、DealDetailView がクエリセットから除外し、「マージ済み」文字列が表示されないこと。"""
        deal = Deal.objects.create(
            name="不整合残存案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
        )
        DealPerson.objects.create(
            deal=deal,
            person=self.target_person,
            role=PersonRole.CONTACT_WINDOW,
        )
        merged_person = Person.objects.create(status=Person.Status.MERGED, merged_into=self.target_person)
        DealPerson.objects.create(
            deal=deal,
            person=merged_person,
            role=PersonRole.ATTENDEE,
        )

        resp = self.client.get(reverse("deals:deal_detail", kwargs={"pk": deal.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "マージ済み")
        self.assertEqual(len(resp.context["deal_persons"]), 1)
        self.assertEqual(resp.context["deal_persons"][0].person, self.target_person)


class DealDetailAttachmentModalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="att_deal_user", password="password", first_name="添付", last_name="テスト")
        perm_change_deal = Permission.objects.get(codename="change_deal")
        perm_add_att = Permission.objects.get(codename="add_attachment")
        self.user.user_permissions.add(perm_change_deal, perm_add_att)
        self.company = Company.objects.create(organization="添付テスト企業", created_by=self.user)
        self.deal = Deal.objects.create(
            name="添付テスト案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
        )
        self.client.login(username="att_deal_user", password="password")

    def test_deal_detail_attachment_modal_structure(self):
        """案件詳細の添付ファイルカード内に常時表示フォームが存在せず、モーダルトリガーとモーダル内フォームが存在することを検証。"""
        url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # 1. カード内に常時表示のファイル入力フォームが存在しないこと
        card_match = re.search(r'<section class="app-card"[^>]*>(?:(?!<section)[\s\S])*?<h2[^>]*>添付ファイル</h2>[\s\S]*?</section>', html)
        self.assertIsNotNone(card_match)
        card_html = card_match.group(0)
        self.assertNotIn('id="attachment-upload-form"', card_html)
        self.assertNotIn('id="attachment-dropzone"', card_html)
        self.assertIn("添付ファイルはありません。", card_html)

        # 2. モーダルトリガーボタンが存在すること
        self.assertIn('data-action="open-modal"', card_html)
        self.assertIn('data-target="attachmentUploadModal"', card_html)
        self.assertIn("ファイルをアップロード", card_html)

        # 3. モーダル（#attachmentUploadModal）内にフォームと各要素が存在すること
        modal_match = re.search(r'<div class="app-modal" id="attachmentUploadModal"[\s\S]*?</form>\s*</div>\s*</div>', html)
        self.assertIsNotNone(modal_match)
        modal_html = modal_match.group(0)
        upload_url = reverse("attachments:attachment_upload")
        self.assertIn(f'action="{upload_url}"', modal_html)
        self.assertIn('enctype="multipart/form-data"', modal_html)
        self.assertIn('name="deal_id"', modal_html)
        self.assertIn(f'value="{self.deal.id}"', modal_html)
        self.assertIn('name="files"', modal_html)
        self.assertIn("multiple", modal_html)
        self.assertIn('name="memo"', modal_html)
        self.assertIn('id="attachment-dropzone"', modal_html)
        self.assertIn('id="attachment-upload-btn"', modal_html)
        self.assertIn('data-action="close-modal"', modal_html)


class DealKanbanViewTests(TestCase):
    """案件一覧画面のパイプライン（カンバン）表示およびステージ別集計の検証。"""

    def setUp(self):
        self.user = User.objects.create_user(username="kanban_user", password="password")
        self.client.login(username="kanban_user", password="password")
        self.company = Company.objects.create(organization="テスト株式会社", created_by=self.user)

        # 各ステージの案件を作成
        # 1. 初回商談: 2件 (¥1,000,000 + ¥2,500,000 = ¥3,500,000)
        self.d1 = Deal.objects.create(
            name="商談A",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.INITIAL_MEETING,
            amount=Decimal("1000000"),
            probability=20,
            expected_close_date=timezone.localdate() + timedelta(days=10),
        )
        self.d2 = Deal.objects.create(
            name="商談B",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.INITIAL_MEETING,
            amount=Decimal("2500000"),
            probability=30,
            expected_close_date=timezone.localdate() - timedelta(days=1),  # 期限超過
        )
        # 2. ヒアリング・課題整理: 1件 (¥1,500,000)
        self.d_needs = Deal.objects.create(
            name="ヒアリングX",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.NEEDS_ANALYSIS,
            amount=Decimal("1500000"),
            probability=40,
        )
        # 3. 提案・見積提示: 1件 (amount is None -> ¥0)
        self.d3 = Deal.objects.create(
            name="見積C",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.QUOTATION,
            amount=None,
            probability=50,
        )
        # 4. 社内稟議・検討中: 2件 (UNDER_REVIEW: ¥3,000,000 + INTERNAL_APPROVAL: ¥2,000,000 = ¥5,000,000)
        self.d4 = Deal.objects.create(
            name="稟議D",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.UNDER_REVIEW,
            amount=Decimal("3000000"),
            probability=70,
        )
        self.d5 = Deal.objects.create(
            name="検討E",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.INTERNAL_APPROVAL,
            amount=Decimal("2000000"),
            probability=80,
        )
        # 5. 最終交渉: 0件 (count=0, total_amount=0)

    def test_default_view_is_list(self):
        """view 未指定時は list モードになり、テーブルが表示されること。"""
        url = reverse("deals:deal_list")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["current_view"], "list")
        html = res.content.decode("utf-8")
        self.assertIn("app-table", html)
        self.assertNotIn("deal-kanban-board", html)

    def test_kanban_view_context_and_aggregation(self):
        """?view=kanban 指定時にカンバン表示となり、各ステージ（全5列）の件数・合計金額が集計されること。"""
        url = reverse("deals:deal_list") + "?view=kanban"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["current_view"], "kanban")

        columns = res.context["kanban_columns"]
        self.assertEqual(len(columns), 5)

        # カラム0: 初回商談
        col0 = columns[0]
        self.assertEqual(col0["key"], "initial_meeting")
        self.assertEqual(col0["title"], "初回商談")
        self.assertEqual(col0["count"], 2)
        self.assertEqual(col0["total_amount"], Decimal("3500000"))

        # カラム1: ヒアリング・課題整理
        col1 = columns[1]
        self.assertEqual(col1["key"], "needs_analysis")
        self.assertEqual(col1["title"], "ヒアリング・課題整理")
        self.assertEqual(col1["count"], 1)
        self.assertEqual(col1["total_amount"], Decimal("1500000"))

        # カラム2: 提案・見積提示
        col2 = columns[2]
        self.assertEqual(col2["key"], "quotation")
        self.assertEqual(col2["title"], "提案・見積提示")
        self.assertEqual(col2["count"], 1)
        self.assertEqual(col2["total_amount"], Decimal("0"))

        # カラム3: 社内稟議・検討中
        col3 = columns[3]
        self.assertEqual(col3["key"], "under_review")
        self.assertEqual(col3["title"], "社内稟議・検討中")
        self.assertEqual(col3["count"], 2)
        self.assertEqual(col3["total_amount"], Decimal("5000000"))

        # カラム4: 最終交渉
        col4 = columns[4]
        self.assertEqual(col4["key"], "negotiation")
        self.assertEqual(col4["title"], "最終交渉")
        self.assertEqual(col4["count"], 0)
        self.assertEqual(col4["total_amount"], Decimal("0"))

    def test_kanban_html_elements_and_overdue_highlight(self):
        """カンバン表示のHTML要素および期限超過ハイライト、切り替えボタングループの描画を検証。"""
        url = reverse("deals:deal_list") + "?view=kanban"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # カンバンコンテナおよびカード
        self.assertIn("deal-kanban-board", html)
        self.assertIn("deal-kanban-column", html)
        self.assertIn("deal-kanban-card", html)

        # 会社名リンク、案件名、金額、確度
        self.assertIn("テスト株式会社", html)
        self.assertIn("商談A", html)
        self.assertIn("¥1,000,000", html)
        self.assertIn("20%", html)
        self.assertIn("ヒアリング・課題整理", html)
        self.assertIn("ヒアリングX", html)
        self.assertIn("¥1,500,000", html)

        # 期限超過のスタイル適用（商談Bの予定日）
        self.assertIn("color: var(--app-color-danger, #ef4444)", html)

        # 切り替えボタンが検索フォーム（app-filter-form）内に存在し、アクティブ状態であること
        self.assertIn("app-filter-form", html)
        self.assertIn("パイプライン", html)
        self.assertIn("一覧", html)
        self.assertIn("app-btn--primary", html)
        form_start = html.find('class="app-filter-form"')
        form_end = html.find('</form>', form_start)
        filter_form_part = html[form_start:form_end]
        self.assertIn("app-btn-group", filter_form_part)
        self.assertIn("パイプライン", filter_form_part)



    def test_query_parameters_preserved_in_switch_urls(self):
        """検索条件が付与された状態で切り替えURLにクエリが維持されること。"""
        url = reverse("deals:deal_list") + "?view=list&q=商談&stage=initial_meeting"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # kanban_url に q と stage が維持され、view=kanban になっていること
        kanban_url = res.context["kanban_url"]
        self.assertIn("view=kanban", kanban_url)
        self.assertIn("q=%E5%95%86%E8%AB%87", kanban_url)
        self.assertIn("stage=initial_meeting", kanban_url)


class DealUpdateBackNavigatorTests(TestCase):
    """案件編集および操作ViewにおけるBackNavigator（back_stack）引き継ぎ検証。"""

    def setUp(self):
        self.user = User.objects.create_user(username="deal_edit_user", password="password")
        perm_change_deal = Permission.objects.get(codename="change_deal")
        self.user.user_permissions.add(perm_change_deal)
        self.client.login(username="deal_edit_user", password="password")

        self.company = Company.objects.create(organization="テスト企業", created_by=self.user)
        self.deal = Deal.objects.create(
            name="編集対象案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.INITIAL_MEETING,
        )

    def test_deal_update_preserves_back_stack_on_post(self):
        """DealUpdateView への POST 保存時、リダイレクト先に back_stack が引き継がれること。"""
        # 案件詳細にアクセスしてバックスタックを生成
        detail_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        back = res_detail.context["back"]
        encoded_stack = back._encode_stack()
        self.assertTrue(encoded_stack)

        # 案件編集URLにback_stack付きでPOST
        update_url = reverse("deals:deal_update", kwargs={"pk": self.deal.pk})
        post_data = {
            "name": "編集済み案件名",
            "company": self.company.pk,
            "stage": Stage.INITIAL_MEETING,
            BackNavigator.PARAM_NAME: encoded_stack,
        }
        res_post = self.client.post(update_url, data=post_data)
        self.assertEqual(res_post.status_code, 302)

        # リダイレクト先URLに BackNavigator.PARAM_NAME が付与されていること
        redirect_url = res_post.url
        self.assertIn(f"{BackNavigator.PARAM_NAME}=", redirect_url)

        # リダイレクト先（詳細画面）へアクセスした際、戻るボタンが存在すること
        res_follow = self.client.get(redirect_url)
        self.assertEqual(res_follow.status_code, 200)
        self.assertTrue(res_follow.context["back"].has_back)

    def test_deal_close_preserves_back_stack_on_post(self):
        """DealCloseView への POST 完了時、リダイレクト先に back_stack が引き継がれること。"""
        detail_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        res_detail = self.client.get(detail_url)
        encoded_stack = res_detail.context["back"]._encode_stack()

        close_url = reverse("deals:deal_close", kwargs={"pk": self.deal.pk})
        post_data = {
            "stage": Stage.WON,
            "closed_at": timezone.localdate(),
            BackNavigator.PARAM_NAME: encoded_stack,
        }
        res_post = self.client.post(close_url, data=post_data)
        self.assertEqual(res_post.status_code, 302)
        self.assertIn(f"{BackNavigator.PARAM_NAME}=", res_post.url)

    def test_deal_reassign_owner_preserves_back_stack_on_post(self):
        """DealReassignOwnerView への POST 完了時、リダイレクト先に back_stack が引き継がれること。"""
        new_user = User.objects.create_user(username="new_owner_user", password="password")
        detail_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        res_detail = self.client.get(detail_url)
        encoded_stack = res_detail.context["back"]._encode_stack()

        reassign_url = reverse("deals:deal_reassign_owner", kwargs={"pk": self.deal.pk})
        post_data = {
            "new_owner": new_user.pk,
            BackNavigator.PARAM_NAME: encoded_stack,
        }
        res_post = self.client.post(reassign_url, data=post_data)
        self.assertEqual(res_post.status_code, 302)
        self.assertIn(f"{BackNavigator.PARAM_NAME}=", res_post.url)


class DealFormStageValidationTests(TestCase):
    """DealCreateForm / DealUpdateForm のステージ選択肢適正化およびエラーハンドリングの検証。"""

    def setUp(self):
        self.user = User.objects.create_user(username="stage_user", password="password")
        perm_change_deal = Permission.objects.get(codename="change_deal")
        perm_add_deal = Permission.objects.get(codename="add_deal")
        self.user.user_permissions.add(perm_change_deal, perm_add_deal)
        self.client.login(username="stage_user", password="password")
        self.company = Company.objects.create(organization="ステージテスト企業", created_by=self.user)
        self.deal = Deal.objects.create(
            name="アクティブ案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.INITIAL_MEETING,
        )

    def test_deal_create_form_stage_choices_only_active(self):
        """DealCreateForm の stage 選択肢に won / lost が含まれず、アクティブステージ6種のみであること。"""
        from deals.forms import DealCreateForm
        form = DealCreateForm()
        choice_codes = [c[0] for c in form.fields["stage"].choices]
        self.assertNotIn(Stage.WON, choice_codes)
        self.assertNotIn(Stage.LOST, choice_codes)
        self.assertEqual(len(choice_codes), 6)
        self.assertIn(Stage.INITIAL_MEETING, choice_codes)
        self.assertIn(Stage.NEEDS_ANALYSIS, choice_codes)
        self.assertIn(Stage.QUOTATION, choice_codes)
        self.assertIn(Stage.UNDER_REVIEW, choice_codes)
        self.assertIn(Stage.INTERNAL_APPROVAL, choice_codes)
        self.assertIn(Stage.NEGOTIATION, choice_codes)

    def test_deal_update_form_stage_choices_only_active_for_active_deal(self):
        """アクティブな案件の DealUpdateForm の stage 選択肢に won / lost が含まれないこと。"""
        from deals.forms import DealUpdateForm
        form = DealUpdateForm(instance=self.deal)
        choice_codes = [c[0] for c in form.fields["stage"].choices]
        self.assertNotIn(Stage.WON, choice_codes)
        self.assertNotIn(Stage.LOST, choice_codes)
        self.assertEqual(len(choice_codes), 6)

    def test_deal_update_form_stage_choices_includes_won_for_closed_deal(self):
        """既に受注クローズ済みの案件では DealUpdateForm の stage 選択肢に won が残ること。"""
        from deals.forms import DealUpdateForm
        closed_deal = Deal.objects.create(
            name="受注済み案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.WON,
            closed_at=timezone.localdate(),
        )
        form = DealUpdateForm(instance=closed_deal)
        choice_codes = [c[0] for c in form.fields["stage"].choices]
        self.assertIn(Stage.WON, choice_codes)

    def test_deal_update_post_won_does_not_crash_and_returns_form_error(self):
        """DealUpdateView へ stage=won を POST した場合に ValueError でクラッシュせずフォームエラーとなること。"""
        url = reverse("deals:deal_update", kwargs={"pk": self.deal.pk})
        post_data = {
            "name": "不正更新案件",
            "company": self.company.pk,
            "stage": Stage.WON,  # クローズ画面を通さず直接 won を指定
        }
        res = self.client.post(url, data=post_data)
        # 500 エラーにならず、200 OK（再描画・エラー表示）が返ること
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["form"].is_valid())
        self.assertIn("stage", res.context["form"].errors)
        # DB上のステージが変更されていないこと
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.stage, Stage.INITIAL_MEETING)

    def test_deal_create_post_won_does_not_crash_and_returns_form_error(self):
        """DealCreateView へ stage=won を POST した場合に ValueError でクラッシュせずフォームエラーとなること。"""
        url = reverse("deals:deal_create")
        post_data = {
            "name": "新規不正案件",
            "company": self.company.pk,
            "owner": self.user.pk,
            "stage": Stage.WON,
        }
        res = self.client.post(url, data=post_data)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["form"].is_valid())
        self.assertIn("stage", res.context["form"].errors)


class DealStageBadgeStyleTests(TestCase):
    """案件ステージごとのバッジスタイルおよび表示テスト。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="badge_test_user",
            password="password",
        )
        perm_view = Permission.objects.get(codename="view_deal")
        perm_view_all = Permission.objects.get(codename="view_all_deals")
        self.user.user_permissions.add(perm_view, perm_view_all)
        self.company = Company.objects.create(
            organization="バッジテスト株式会社",
            created_by=self.user,
        )

    def test_stage_badge_style_mapping(self):
        """各ステージおよび未知の値で期待通りのインラインスタイルが返されること。"""
        deal = Deal(name="テスト案件", company=self.company)

        expected_styles = {
            Stage.INITIAL_MEETING: "background-color: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd;",
            Stage.NEEDS_ANALYSIS: "background-color: #cffafe; color: #0e7490; border: 1px solid #a5f3fc;",
            Stage.QUOTATION: "background-color: #ede9fe; color: #6d28d9; border: 1px solid #ddd6fe;",
            Stage.UNDER_REVIEW: "background-color: #fef3c7; color: #b45309; border: 1px solid #fde68a;",
            Stage.INTERNAL_APPROVAL: "background-color: #fef9c3; color: #a16207; border: 1px solid #fde047;",
            Stage.NEGOTIATION: "background-color: #ffedd5; color: #c2410c; border: 1px solid #fed7aa;",
            Stage.WON: "background-color: #dcfce7; color: #15803d; border: 1px solid #86efac;",
            Stage.LOST: "background-color: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0;",
        }

        for stage, expected_style in expected_styles.items():
            deal.stage = stage
            self.assertEqual(deal.stage_badge_style, expected_style)

        # 未知/その他/未設定
        deal.stage = "unknown_stage"
        self.assertEqual(
            deal.stage_badge_style,
            "background-color: #f1f5f9; color: #475569; border: 1px solid #cbd5e1;",
        )

    def test_stage_badge_rendered_in_list_and_detail(self):
        """一覧画面および詳細画面でステージバッジが正しく描画されること。"""
        deal = Deal.objects.create(
            name="商談中案件",
            company=self.company,
            owner=self.user,
            created_by=self.user,
            stage=Stage.NEGOTIATION,
        )

        self.client.login(username="badge_test_user", password="password")

        # 一覧画面での描画検証
        list_url = reverse("deals:deal_list")
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)
        expected_badge_html = (
            f'<span class="app-badge" style="{deal.stage_badge_style} '
            'font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 9999px;">交渉</span>'
        )
        self.assertContains(res_list, expected_badge_html)

        # 詳細画面での描画検証
        detail_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, expected_badge_html)


class SalesRoleDealCreatePermissionTests(TestCase):
    """営業（sales）ロールを持つ一般ユーザーの案件起票権限（deals.add_deal）検証。"""

    def setUp(self):
        self.sales_role = Role.objects.get(code="sales")
        self.sales_user = User.objects.create_user(username="sales_rep", password="password")
        apply_role(self.sales_user, self.sales_role)
        self.company = Company.objects.create(organization="テスト商事")

    def test_sales_role_user_can_access_deal_create_view(self):
        """営業ロールを持つユーザーが GET /deals/create/ にアクセスした際、403 Forbidden にならず 200 OK で表示されること。"""
        self.client.login(username="sales_rep", password="password")
        url = reverse("deals:deal_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "案件新規作成")

    def test_sales_role_user_can_create_deal(self):
        """営業ロールを持つユーザーが POST /deals/create/ で案件を正常に起票できること。"""
        self.client.login(username="sales_rep", password="password")
        url = reverse("deals:deal_create")
        post_data = {
            "name": "営業担当起票案件",
            "company": str(self.company.pk),
            "stage": Stage.INITIAL_MEETING,
            "probability": 20,
            "deal_type": DealType.NEW,
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        created_deal = Deal.objects.filter(name="営業担当起票案件").first()
        self.assertIsNotNone(created_deal)
        self.assertEqual(created_deal.company, self.company)
        self.assertEqual(created_deal.owner, self.sales_user)


class DealWizardBackNavigationTests(TestCase):
    """新規案件ウィザードの各ステップ間のBackNavigator戻り先検証。"""

    def setUp(self):
        from django.contrib.auth.models import Permission

        self.user = User.objects.create_user(username="wizard_deal_user", password="password")
        self.user.user_permissions.add(
            Permission.objects.get(codename="add_deal"),
            Permission.objects.get(codename="change_deal"),
            Permission.objects.get(codename="view_deal"),
        )
        self.client.login(username="wizard_deal_user", password="password")
        self.company = Company.objects.create(organization="案件ウィザード会社")

    def test_step1_to_step2_back_link_points_to_step1_edit(self):
        """Step 1（新規作成）から Step 2（社外関係者設定）へ遷移した際、Step 2 画面の戻るリンク先が Step 1（編集画面 ?wizard=1 付き）を指していること。"""
        origin_url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        back = BackNavigator(self.client.get(origin_url).wsgi_request)
        back.push_current(title="会社詳細", keys=["page"])
        create_url = back.append_url(reverse("deals:deal_create"))

        post_data = {
            "name": "ウィザードテスト案件",
            "company": str(self.company.pk),
            "stage": Stage.INITIAL_MEETING,
            "probability": 20,
            "deal_type": DealType.NEW,
            BackNavigator.PARAM_NAME: back._encode_stack(),
        }
        res_step1 = self.client.post(create_url, data=post_data)
        self.assertEqual(res_step1.status_code, 302)

        step2_url = res_step1.url
        deal = Deal.objects.filter(name="ウィザードテスト案件").first()
        self.assertIsNotNone(deal)
        expected_step2_base = reverse("deals:deal_persons_manage", kwargs={"pk": deal.pk})
        self.assertIn(expected_step2_base, step2_url)
        self.assertIn("wizard=1", step2_url)

        res_step2 = self.client.get(step2_url)
        self.assertEqual(res_step2.status_code, 200)
        html_step2 = res_step2.content.decode("utf-8")

        step1_edit_base = reverse("deals:deal_update", kwargs={"pk": deal.pk})
        import re
        pattern = rf'<a[^>]+href="({re.escape(step1_edit_base)}\?[^"]*wizard=1[^"]*)"[^>]*>\s*戻る\s*</a>'
        match = re.search(pattern, html_step2)
        self.assertIsNotNone(match, f"Step 2 画面の戻るリンク先に Step 1 編集画面（{step1_edit_base}?wizard=1）が見つかりません。")

    def test_step2_back_to_step1_and_proceed_again(self):
        """Step 2 から Step 1 編集画面へ引き返し、再度「次へ」を押して Step 2 に進んだ場合も戻り先が Step 1 を維持すること。"""
        deal = Deal.objects.create(
            name="再進行テスト案件",
            company=self.company,
            stage=Stage.INITIAL_MEETING,
            owner=self.user,
        )
        origin_url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        back = BackNavigator(self.client.get(origin_url).wsgi_request)
        back.push_current(title="会社詳細", keys=["page"])

        step1_edit_url = reverse("deals:deal_update", kwargs={"pk": deal.pk}) + "?wizard=1"
        step1_with_back = back.append_url(step1_edit_url)

        post_data = {
            "name": "再進行テスト案件（更新後）",
            "company": str(self.company.pk),
            "stage": Stage.QUOTATION,
            "probability": 40,
            "deal_type": DealType.NEW,
            "wizard": "1",
            BackNavigator.PARAM_NAME: back._encode_stack(),
        }
        res_step1_post = self.client.post(step1_with_back, data=post_data)
        self.assertEqual(res_step1_post.status_code, 302)

        res_step2 = self.client.get(res_step1_post.url)
        self.assertEqual(res_step2.status_code, 200)
        html_step2 = res_step2.content.decode("utf-8")

        step1_edit_base = reverse("deals:deal_update", kwargs={"pk": deal.pk})
        import re
        pattern = rf'<a[^>]+href="({re.escape(step1_edit_base)}\?[^"]*wizard=1[^"]*)"[^>]*>\s*戻る\s*</a>'
        self.assertRegex(html_step2, pattern)

    def test_step1_create_back_link_points_to_origin(self):
        """Step 1（新規作成画面）の時点で「戻る」リンクが起点画面を指していること。"""
        origin_url = reverse("companies:company_detail", kwargs={"pk": self.company.pk})
        back = BackNavigator(self.client.get(origin_url).wsgi_request)
        back.push_current(title="会社詳細", keys=["page"])
        create_url = back.append_url(reverse("deals:deal_create"))

        res = self.client.get(create_url)
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")
        import re
        pattern = rf'<a class="app-btn app-btn--secondary" href="({re.escape(origin_url)})">\s*戻る\s*</a>'
        self.assertRegex(html, pattern)


class DealDetailActivityTitleTests(TestCase):
    """案件詳細画面の活動履歴テーブルにおけるタイトル表示検証。"""

    def setUp(self):
        from activities.models import Activity, ActivityType
        self.user = User.objects.create_user(username="deal_act_user", password="password")
        self.client.login(username="deal_act_user", password="password")
        self.company = Company.objects.create(organization="案件活動会社")
        self.deal = Deal.objects.create(
            name="活動タイトルテスト案件",
            company=self.company,
            owner=self.user,
        )
        self.activity = Activity.objects.create(
            deal=self.deal,
            title="案件紐づけ活動タイトル",
            occurred_at=timezone.now(),
            activity_type=ActivityType.VISIT,
            user=self.user,
        )

    def test_activity_title_displayed_in_deal_detail(self):
        """案件詳細の活動履歴テーブルにタイトルが表示され、リンクが含まれていること。"""
        url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "案件紐づけ活動タイトル")
        self.assertContains(response, f"/activities/{self.activity.id}/")


class SafeDealModelFormMixinTests(TestCase):
    """SafeDealModelFormMixin のエラー付け替え処理（_post_clean）検証。"""

    def setUp(self):
        self.user = User.objects.create_user(username="deal_mixin_user", password="password")
        self.company = Company.objects.create(organization="防護ネットテスト会社")
        self.deal = Deal.objects.create(
            name="防護ネット検証案件",
            company=self.company,
            owner=self.user,
            stage=Stage.INITIAL_MEETING,
        )

    def test_deal_update_form_closed_at_future_date_remapped_without_crash(self):
        """DealUpdateForm（closed_at を含まないフォーム）で closed_at に未来日が直接セットされている場合、
        ValueError でクラッシュせず正常にバリデーションエラーとなり、stage またはノンフィールドエラーに付け替えられること。
        """
        from deals.forms import DealUpdateForm

        # 案件の closed_at に未来日（Model.clean() バリデーション違反）を直接セット
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.deal.closed_at = tomorrow

        form_data = {
            "name": "更新後案件名",
            "company": str(self.company.pk),
            "stage": Stage.INITIAL_MEETING,
        }
        form = DealUpdateForm(data=form_data, instance=self.deal)

        # クラッシュ（ValueError）せず、is_valid() が False となること
        self.assertFalse(form.is_valid())

        # エラーが stage またはノンフィールドエラーに付け替えられていることを検証
        stage_or_all_errors = form.errors.get("stage", []) + form.non_field_errors()
        self.assertTrue(
            any("成約確定日に未来日は指定できません。" in str(msg) for msg in stage_or_all_errors),
            f"Expected error message not found in stage/non-field errors: {form.errors}",
        )







