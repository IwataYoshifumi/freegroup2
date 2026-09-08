from datetime import timedelta
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from activities.admin import ActivityAdmin, ActivityPersonAdmin, ActivityUserAdmin
from activities.models import Activity, ActivityPerson, ActivityUser
from deals.models import Deal, PersonRole, UserRole
from persons.models import Person

User = get_user_model()


class ActivityModelValidationTests(TestCase):
    """Activity / ActivityUser / ActivityPerson のバリデーション・制約検証（仕様書 §3.1、§5.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="act_user", password="password")
        self.user2 = User.objects.create_user(username="act_user2", password="password")
        self.person = Person.objects.create()
        self.person2 = Person.objects.create()
        self.deal = Deal.objects.create(
            name="Activity Test Deal",
            primary_person=self.person,
            owner=self.user,
        )

    def test_occurred_at_future_datetime_raises_validation_error(self):
        future_time = timezone.now() + timedelta(hours=2)
        activity = Activity(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=future_time,
            user=self.user,
        )
        with self.assertRaises(ValidationError) as ctx:
            activity.full_clean()
        self.assertIn("occurred_at", ctx.exception.message_dict)

    def test_occurred_at_past_or_present_succeeds(self):
        past_time = timezone.now() - timedelta(hours=1)
        activity = Activity(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=past_time,
            user=self.user,
        )
        activity.full_clean()  # should not raise
        activity.save()

    def test_activity_user_cannot_be_activity_user(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        activity_user = ActivityUser(
            activity=activity,
            user=self.user,
            role=UserRole.SUPPORT,
        )
        with self.assertRaises(ValidationError) as ctx:
            activity_user.full_clean()
        self.assertTrue(
            any("実施者と同じUser" in msg for msg in ctx.exception.messages)
        )

    def test_activity_user_other_user_succeeds(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        activity_user = ActivityUser(
            activity=activity,
            user=self.user2,
            role=UserRole.SUPPORT,
        )
        activity_user.full_clean()  # should not raise
        activity_user.save()

    def test_activity_user_unique_constraint(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        ActivityUser.objects.create(activity=activity, user=self.user2, role=UserRole.SUPPORT)
        with self.assertRaises(IntegrityError):
            ActivityUser.objects.create(activity=activity, user=self.user2, role=UserRole.APPROVER)

    def test_activity_person_unique_constraint(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        ActivityPerson.objects.create(activity=activity, person=self.person, role=PersonRole.ATTENDEE)
        with self.assertRaises(IntegrityError):
            ActivityPerson.objects.create(activity=activity, person=self.person, role=PersonRole.CONTACT_WINDOW)


class ActivityAdminTests(TestCase):
    """ActivityAdmin の保護制御検証（仕様書 §7.6.1）。"""

    def setUp(self):
        self.site = AdminSite()
        self.activity_admin = ActivityAdmin(Activity, self.site)
        self.activity_person_admin = ActivityPersonAdmin(ActivityPerson, self.site)
        self.activity_user_admin = ActivityUserAdmin(ActivityUser, self.site)
        self.user = User.objects.create_user(username="admin_user", is_staff=True)
        self.person = Person.objects.create()
        self.deal = Deal.objects.create(
            name="Admin Test Deal",
            primary_person=self.person,
            owner=self.user,
        )
        self.activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )

    def test_activity_admin_readonly_fields_new_object(self):
        readonly = self.activity_admin.get_readonly_fields(request=None, obj=None)
        self.assertEqual(readonly, [])

    def test_activity_admin_readonly_fields_existing_object(self):
        readonly = self.activity_admin.get_readonly_fields(request=None, obj=self.activity)
        self.assertIn("is_archived", readonly)

    def test_activity_person_admin_autocomplete_fields(self):
        self.assertIn("activity", self.activity_person_admin.autocomplete_fields)
        self.assertIn("person", self.activity_person_admin.autocomplete_fields)

    def test_activity_user_admin_autocomplete_fields(self):
        self.assertIn("activity", self.activity_user_admin.autocomplete_fields)
        self.assertIn("user", self.activity_user_admin.autocomplete_fields)
