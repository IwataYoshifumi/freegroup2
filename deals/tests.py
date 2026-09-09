from datetime import timedelta
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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

    def test_deal_user_cannot_be_owner(self):
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
        with self.assertRaises(ValidationError) as ctx:
            deal_user.full_clean()
        self.assertTrue(
            any("現在のownerと同じUser" in msg for msg in ctx.exception.messages)
        )

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
            "primary_person": str(self.person.id),
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
        # primary_person から company が自動補完されたか検証
        self.assertEqual(new_deal.company, self.company)

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

    def test_deal_archive_view(self):
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_archive", kwargs={"pk": self.deal.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.deal.refresh_from_db()
        self.assertTrue(self.deal.is_archived)

    def test_deal_create_view_with_related_persons(self):
        self.client.login(username="deal_owner", password="password")
        url = reverse("deals:deal_create")
        p2 = Person.objects.create()
        p3 = Person.objects.create()

        # primary_person を含めて送信しても除外され、p2, p3 のみが登録されること
        post_data = {
            "name": "関係者付き新規案件",
            "primary_person": str(self.person.id),
            "stage": Stage.INITIAL_MEETING,
            "probability": 50,
            "deal_type": DealType.NEW,
            "amount": 2000000,
            "related_person_ids": f"{p2.id},{p3.id},{self.person.id}",
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        new_deal = Deal.objects.get(name="関係者付き新規案件")

        # DealPerson の検証
        deal_persons = DealPerson.objects.filter(deal=new_deal)
        self.assertEqual(deal_persons.count(), 2)
        person_ids = set(deal_persons.values_list("person_id", flat=True))
        self.assertIn(p2.id, person_ids)
        self.assertIn(p3.id, person_ids)
        self.assertNotIn(self.person.id, person_ids)
        for dp in deal_persons:
            self.assertEqual(dp.role, PersonRole.ATTENDEE)

    def test_deal_update_view_with_related_persons_sync(self):
        self.client.login(username="deal_owner", password="password")
        p2 = Person.objects.create()
        p3 = Person.objects.create()

        # 事前に p2 を関係者として登録
        DealPerson.objects.create(deal=self.deal, person=p2, role=PersonRole.ATTENDEE)
        self.assertEqual(DealPerson.objects.filter(deal=self.deal).count(), 1)

        url = reverse("deals:deal_update", kwargs={"pk": self.deal.pk})
        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)
        self.assertIn("candidate_persons", get_response.context)
        self.assertIn("initial_related_persons", get_response.context)
        initial_ids = [item["id"] for item in get_response.context["initial_related_persons"]]
        self.assertIn(str(p2.id), initial_ids)

        # p2 を解除し、p3 を新規追加
        response = self.client.post(url, data={
            "name": self.deal.name,
            "company": str(self.company.id),
            "stage": self.deal.stage,
            "probability": self.deal.probability,
            "amount": self.deal.amount,
            "related_person_ids": str(p3.id),
        })
        self.assertEqual(response.status_code, 302)

        deal_persons = DealPerson.objects.filter(deal=self.deal)
        self.assertEqual(deal_persons.count(), 1)
        self.assertEqual(deal_persons.first().person, p3)

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
        self.assertIn("← 戻る", html)
        self.assertIn("＋ 追加", html)

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




