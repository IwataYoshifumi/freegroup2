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
