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

from accounts.models import Department
from companies.models import Company
from contacts.models import Contact
from deals.admin import DealAdmin, DealPersonAdmin, DealUserAdmin
from deals.models import Deal, DealPerson, DealType, DealUser, PersonRole, Stage, UserRole
from persons.models import Person

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
        self.assertContains(response, "見積提示")

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
        self.assertEqual(post_resp.url, f"/mailings/campaigns/{campaign.pk}/report/")

        created_deal = Deal.objects.get(name="キャンペーン経由案件")
        self.assertEqual(created_deal.source_campaign, campaign)
        self.assertEqual(created_deal.lead_source, "campaign")

    def test_deal_create_view_with_company_person_and_campaign_creates_deal_person(self):
        """DealCreateView に company, person, source_campaign を渡して POST した際、
        案件と DealPerson が同時に作成されて直前の画面へリダイレクトされること。
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
        self.assertEqual(post_resp.url, clicked_list_url)

        created_deal = Deal.objects.get(name="クリック受信者からの案件")
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

        # 会社設定ありの案件での検証
        response = self.client.get(reverse("deals:deal_persons_manage", kwargs={"pk": self.deal.pk}))
        self.assertEqual(response.status_code, 200)
        candidates = response.context["candidate_persons"]
        self.assertLessEqual(len(candidates), 50)
        # 当該会社のパーソンは最大30件
        company_candidates = [
            p for p in candidates
            if p.primary_contact and p.primary_contact.company_id == self.deal.company_id
        ]
        self.assertLessEqual(len(company_candidates), 30)

        # 会社未設定の案件での検証
        deal_no_company = Deal.objects.create(
            name="会社未設定案件",
            primary_person=self.person,
            company=None,
            owner=self.owner,
        )
        response_no_comp = self.client.get(reverse("deals:deal_persons_manage", kwargs={"pk": deal_no_company.pk}))
        self.assertEqual(response_no_comp.status_code, 200)
        candidates_no_comp = response_no_comp.context["candidate_persons"]
        self.assertLessEqual(len(candidates_no_comp), 50)

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

        # 1. 画面表示確認
        response = self.client.get(manage_url)
        self.assertEqual(response.status_code, 200)

        # 2. 検索機能
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

        # 4. 削除POST（nextパラメータ付きで管理画面へリダイレクト）
        del_url = reverse("deals:deal_delete_person", kwargs={"pk": self.deal.pk, "person_rel_id": dp.id})
        del_res = self.client.post(del_url, data={"next": manage_url})
        self.assertRedirects(del_res, manage_url)
        self.assertFalse(DealPerson.objects.filter(id=dp.id).exists())

    def test_deal_users_manage_flow(self):
        """社内担当者管理画面での検索・追加・権限設定・削除フローを検証。"""
        self.client.login(username="deal_owner", password="password")
        manage_url = reverse("deals:deal_users_manage", kwargs={"pk": self.deal.pk})

        # 1. 画面表示確認
        response = self.client.get(manage_url)
        self.assertEqual(response.status_code, 200)

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
        self.assertNotIn("＋ 追加", html)
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
        self.assertNotIn("＋ 追加", html_username)
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
        self.assertIn('style="width: 30%;"', html_detail)
        self.assertIn('style="width: 25%;"', html_detail)
        self.assertIn('style="width: 20%;"', html_detail)
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


