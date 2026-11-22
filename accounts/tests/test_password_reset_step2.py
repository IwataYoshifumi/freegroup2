"""パスワード変更・リセット Step 2（モデル・定数・設定・管理画面・削除コマンド）の単体テスト。"""

from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.admin import PasswordResetRequestAdmin
from accounts.constants import ActionLogAction
from accounts.login_security.purge import (
    LOGIN_ATTEMPT_RETENTION_DAYS,
    purge_old_login_data,
)
from accounts.models import LoginAttempt, PasswordResetRequest

User = get_user_model()


class PasswordResetRequestModelTests(TestCase):
    """PasswordResetRequest モデルの単体テスト。"""

    def test_ordering_is_newest_first(self):
        now = timezone.now()
        older = PasswordResetRequest.objects.create(
            created_at=now - timedelta(hours=2),
            email_input="old@example.com",
            result=PasswordResetRequest.Result.NO_MATCH,
        )
        newer = PasswordResetRequest.objects.create(
            created_at=now - timedelta(hours=1),
            email_input="new@example.com",
            result=PasswordResetRequest.Result.SENT,
        )

        qs = list(PasswordResetRequest.objects.all())
        self.assertEqual(qs, [newer, older])

    def test_user_foreign_key_is_set_null_on_user_delete(self):
        user = User.objects.create_user(
            username="pr_del_user",
            email="pr_del@example.com",
            password="pw",
        )
        req = PasswordResetRequest.objects.create(
            email_input="pr_del@example.com",
            user=user,
            ip_address="203.0.113.10",
            result=PasswordResetRequest.Result.SENT,
        )

        user.delete()
        req.refresh_from_db()
        self.assertIsNone(req.user)
        self.assertEqual(req.email_input, "pr_del@example.com")
        self.assertEqual(req.ip_address, "203.0.113.10")

    def test_result_choices_and_nullable_fields(self):
        expected_choices = [
            ("sent", "送信"),
            ("no_match", "該当なし"),
            ("not_eligible", "対象外"),
            ("rate_limited", "制限超過"),
            ("ip_blocked", "IP遮断"),
        ]
        self.assertEqual(PasswordResetRequest.Result.choices, expected_choices)

        long_email = ("a" * 242) + "@example.com"  # 254文字
        req = PasswordResetRequest.objects.create(
            email_input=long_email,
            user=None,
            ip_address=None,
            result=PasswordResetRequest.Result.IP_BLOCKED,
        )
        self.assertEqual(len(req.email_input), 254)
        self.assertIsNone(req.ip_address)
        self.assertIn("ip_blocked", str(req))


class PasswordResetStep2ConfigAndConstantsTests(TestCase):
    """Step 2 で追加した操作ログ定数・設定値の単体テスト。"""

    def test_action_log_constants(self):
        self.assertEqual(ActionLogAction.PASSWORD_CHANGE, "password_change")
        self.assertEqual(
            ActionLogAction.PASSWORD_RESET_REQUEST,
            "password_reset_request",
        )
        self.assertEqual(
            ActionLogAction.PASSWORD_RESET_COMPLETE,
            "password_reset_complete",
        )

    def test_password_reset_timeout_default(self):
        self.assertIsInstance(settings.PASSWORD_RESET_TIMEOUT, int)
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 3600)


class PasswordResetRequestAdminTests(TestCase):
    """PasswordResetRequestAdmin の閲覧専用登録テスト。"""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="pr_admin_su",
            email="pr_admin_su@example.com",
            password="pw",
        )
        self.staff_user = User.objects.create_user(
            username="pr_admin_staff",
            email="pr_admin_staff@example.com",
            password="pw",
            is_staff=True,
        )
        self.record = PasswordResetRequest.objects.create(
            email_input="target@example.com",
            user=self.staff_user,
            ip_address="198.51.100.5",
            result=PasswordResetRequest.Result.NOT_ELIGIBLE,
        )
        self.factory = RequestFactory()

    def test_admin_registered_and_read_only_permissions(self):
        self.assertIn(PasswordResetRequest, admin.site._registry)
        model_admin = admin.site._registry[PasswordResetRequest]
        self.assertIsInstance(model_admin, PasswordResetRequestAdmin)

        model_field_names = {f.name for f in PasswordResetRequest._meta.fields}
        self.assertEqual(set(model_admin.readonly_fields), model_field_names)

        req = self.factory.get("/admin/")
        req.user = self.superuser
        self.assertFalse(model_admin.has_add_permission(req))
        self.assertFalse(model_admin.has_change_permission(req, self.record))
        self.assertFalse(model_admin.has_delete_permission(req, self.record))
        self.assertTrue(model_admin.has_view_permission(req, self.record))

        req.user = self.staff_user
        self.assertTrue(model_admin.has_view_permission(req, self.record))

    def test_superuser_admin_views_are_strictly_read_only(self):
        self.client.force_login(self.superuser)

        changelist_url = reverse("admin:accounts_passwordresetrequest_changelist")
        add_url = reverse("admin:accounts_passwordresetrequest_add")
        change_url = reverse(
            "admin:accounts_passwordresetrequest_change",
            args=[self.record.pk],
        )
        delete_url = reverse(
            "admin:accounts_passwordresetrequest_delete",
            args=[self.record.pk],
        )

        changelist_resp = self.client.get(changelist_url)
        self.assertEqual(changelist_resp.status_code, 200)
        self.assertContains(changelist_resp, "target@example.com")
        self.assertNotContains(changelist_resp, add_url)

        change_resp = self.client.get(change_url)
        self.assertEqual(change_resp.status_code, 200)
        self.assertNotContains(change_resp, 'name="_save"')
        self.assertNotContains(change_resp, 'class="deletelink"')

        self.assertEqual(self.client.get(add_url).status_code, 403)
        self.assertEqual(self.client.post(delete_url).status_code, 403)
        self.assertTrue(PasswordResetRequest.objects.filter(pk=self.record.pk).exists())


class PasswordResetRequestPurgeTests(TestCase):
    """purge_old_login_data および purge_login_attempts による申請記録削除のテスト。"""

    def test_purge_old_login_data_respects_180_day_boundary(self):
        self.assertEqual(LOGIN_ATTEMPT_RETENTION_DAYS, 180)
        now = timezone.now()

        expired = PasswordResetRequest.objects.create(
            created_at=now - timedelta(days=180, seconds=1),
            email_input="expired@example.com",
            result=PasswordResetRequest.Result.SENT,
        )
        boundary = PasswordResetRequest.objects.create(
            created_at=now - timedelta(days=180),
            email_input="boundary@example.com",
            result=PasswordResetRequest.Result.NO_MATCH,
        )
        recent = PasswordResetRequest.objects.create(
            created_at=now - timedelta(days=179),
            email_input="recent@example.com",
            result=PasswordResetRequest.Result.RATE_LIMITED,
        )
        expired_attempt = LoginAttempt.objects.create(
            created_at=now - timedelta(days=181),
            username_input="old_u",
            result=LoginAttempt.Result.PASSWORD_FAILED,
        )

        res = purge_old_login_data(now=now)
        self.assertEqual(res.deleted_password_reset_requests, 1)
        self.assertEqual(res.deleted_attempts, 1)
        self.assertFalse(PasswordResetRequest.objects.filter(pk=expired.pk).exists())
        self.assertTrue(PasswordResetRequest.objects.filter(pk=boundary.pk).exists())
        self.assertTrue(PasswordResetRequest.objects.filter(pk=recent.pk).exists())
        self.assertFalse(LoginAttempt.objects.filter(pk=expired_attempt.pk).exists())

    def test_management_command_deletes_expired_requests_and_reports_count(self):
        old_time = timezone.now() - timedelta(days=181)
        PasswordResetRequest.objects.create(
            created_at=old_time,
            email_input="cmd_old@example.com",
            result=PasswordResetRequest.Result.SENT,
        )
        kept = PasswordResetRequest.objects.create(
            created_at=timezone.now() - timedelta(days=10),
            email_input="cmd_new@example.com",
            result=PasswordResetRequest.Result.SENT,
        )

        out = StringIO()
        call_command("purge_login_attempts", stdout=out)
        output = out.getvalue()

        self.assertIn("パスワードリセット申請記録=1件", output)
        self.assertIn("ログイン試行記録=0件", output)
        self.assertIn("端末記録=0件", output)
        self.assertEqual(list(PasswordResetRequest.objects.all()), [kept])
