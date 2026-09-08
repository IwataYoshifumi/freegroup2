from datetime import timedelta
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from deals.admin import DealAdmin, DealPersonAdmin, DealUserAdmin
from deals.models import Deal, DealPerson, DealUser, PersonRole, UserRole
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

        # change_deal 権限を剥奪された場合は owner でも不可（仕様書 §7.1 の AND 条件厳守）
        from django.contrib.auth.models import Permission
        self.owner.user_permissions.remove(Permission.objects.get(codename="change_deal"))
        self.owner = User.objects.get(pk=self.owner.pk)
        self.assertFalse(can_edit_deal(self.owner, self.deal))

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

