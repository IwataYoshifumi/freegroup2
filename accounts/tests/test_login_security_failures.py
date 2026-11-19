"""ログインセキュリティ Step 2 フェーズ F-1（失敗の記録と通知の結線）の単体テスト。"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.constants import ADMIN_ROLE_CODE
from accounts.login_security.failures import record_failure_and_notify
from accounts.models import LoginAttempt, Role, SecuritySetting

User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class FailuresNotificationTests(TestCase):
    """failures.record_failure_and_notify のテスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.user = User.objects.create_user(
            username="fail_u", password="pw", email="fail_u@example.com"
        )
        self.admin = User.objects.create_user(
            username="fail_adm",
            password="pw",
            email="fail_adm@example.com",
            role=self.admin_role,
        )

    def test_a_lockout_sends_user_email_and_respects_admin_setting(self):
        base = timezone.now()
        setting = SecuritySetting.objects.get(id=1)
        setting.notify_admins_on_account_lock = False
        setting.save()

        for i in range(5):
            with self.captureOnCommitCallbacks(execute=True):
                res = record_failure_and_notify(
                    username_input="fail_u",
                    target_users=[self.user],
                    primary_user=self.user,
                    ip_address="203.0.113.10",
                    user_agent="UA",
                    result=LoginAttempt.Result.PASSWORD_FAILED,
                    now=base + timedelta(seconds=i),
                )
        self.assertEqual(res.locked_users, [self.user])
        # 設定 OFF なので本人宛のみ 1 通
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["fail_u@example.com"])

    def test_b_ip_block_sends_once_and_does_not_resend(self):
        base = timezone.now()
        ip = "198.51.100.40"
        for i in range(19):
            with self.captureOnCommitCallbacks(execute=True):
                res = record_failure_and_notify(
                    username_input=f"unknown_{i}",
                    target_users=[],
                    primary_user=None,
                    ip_address=ip,
                    user_agent="UA",
                    result=LoginAttempt.Result.PASSWORD_FAILED,
                    now=base + timedelta(seconds=i),
                )
            self.assertFalse(res.ip_blocked_now)
        self.assertEqual(len(mail.outbox), 0)

        # 20 回目で IP 遮断通知が管理者宛 1 通だけ飛ぶ
        with self.captureOnCommitCallbacks(execute=True):
            res20 = record_failure_and_notify(
                username_input="unknown_20",
                target_users=[],
                primary_user=None,
                ip_address=ip,
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=base + timedelta(seconds=20),
            )
        self.assertTrue(res20.ip_blocked_now)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].bcc, ["fail_adm@example.com"])

        # 続けて失敗しても再送されない
        with self.captureOnCommitCallbacks(execute=True):
            res21 = record_failure_and_notify(
                username_input="unknown_21",
                target_users=[],
                primary_user=None,
                ip_address=ip,
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=base + timedelta(seconds=21),
            )
        self.assertFalse(res21.ip_blocked_now)
        self.assertEqual(len(mail.outbox), 1)

    def test_c_non_lockout_failure_sends_no_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = record_failure_and_notify(
                username_input="fail_u",
                target_users=[self.user],
                primary_user=self.user,
                ip_address="203.0.113.11",
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
            )
        self.assertEqual(res.locked_users, [])
        self.assertFalse(res.ip_blocked_now)
        self.assertEqual(len(mail.outbox), 0)
