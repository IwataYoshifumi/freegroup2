"""ログインセキュリティ Step 4-3（セキュリティ設定画面）のテスト。"""

from datetime import timedelta
from pathlib import Path
import re

from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE, ActionLogAction
from accounts.login_security.devices import hash_device_token
from accounts.models import (
    AccountLock,
    CustomUser,
    LoginDevice,
    Role,
    SecuritySetting,
)
from actionlogs.models import ActionLog


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class SecuritySettingViewTests(TestCase):
    """accounts:security_setting のテスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.url = reverse("accounts:security_setting")
        self.login_url = reverse("accounts:login")
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.user_admin_group = Group.objects.get(name="user_admin")

        self.admin_operator = CustomUser.objects.create_user(
            username="sec_admin",
            password="pw",
            email="sec_admin@example.com",
            role=self.admin_role,
        )
        self.admin_operator.groups.add(self.user_admin_group)

        self.second_admin = CustomUser.objects.create_user(
            username="sec_admin2",
            password="pw",
            email="sec_admin2@example.com",
            is_superuser=True,
        )

        self.plain_user = CustomUser.objects.create_user(
            username="sec_plain",
            password="pw",
            email="sec_plain@example.com",
        )

        self.setting = SecuritySetting.objects.get(id=1)

    def _base_post_data(self, **overrides):
        data = {
            "two_factor_enforcement": self.setting.two_factor_enforcement,
            "login_notification_level": self.setting.login_notification_level,
            "lockout_threshold": str(self.setting.lockout_threshold),
            "lockout_minutes": str(self.setting.lockout_minutes),
            "ip_block_threshold": str(self.setting.ip_block_threshold),
            "trusted_device_days": str(self.setting.trusted_device_days),
        }
        if self.setting.notify_admins_on_account_lock:
            data["notify_admins_on_account_lock"] = "on"
        if self.setting.notify_admins_on_ip_block:
            data["notify_admins_on_ip_block"] = "on"
        data.update(overrides)
        return data

    def test_permissions_anonymous_403_and_user_admin_access(self):
        resp_anon = self.client.get(self.url)
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn(self.login_url, resp_anon.url)

        self.client.force_login(self.plain_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.post(self.url, self._base_post_data()).status_code, 403
        )

        self.client.force_login(self.admin_operator)
        resp_ok = self.client.get(self.url)
        self.assertEqual(resp_ok.status_code, 200)
        self.assertIn("no-store", resp_ok.headers.get("Cache-Control", ""))
        self.assertContains(
            resp_ok, "ロック失敗回数は、AD 側のロック閾値以下に設定してください"
        )
        self.assertContains(
            resp_ok,
            "一度もログインしていない LDAP ユーザーには、アカウントロックが効きません",
        )

    def test_save_valid_updates_and_no_op_save(self):
        TOTPDevice.objects.create(
            user=self.admin_operator, name="default", confirmed=True
        )
        self.client.force_login(self.admin_operator)

        # 変更なし保存 → ログ・メールなし
        resp_noop = self.client.post(self.url, self._base_post_data())
        self.assertRedirects(resp_noop, self.url)
        self.assertEqual(ActionLog.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

        # 変更あり保存（強化方向）→ DB 更新・操作ログ・通知メールなし
        with self.captureOnCommitCallbacks(execute=True):
            resp_ok = self.client.post(
                self.url,
                self._base_post_data(
                    two_factor_enforcement="all",
                    lockout_threshold="3",
                    lockout_minutes="30",
                    ip_block_threshold="10",
                    trusted_device_days="14",
                ),
            )
        self.assertRedirects(resp_ok, self.url)
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.two_factor_enforcement, "all")
        self.assertEqual(self.setting.lockout_threshold, 3)
        self.assertEqual(self.setting.lockout_minutes, 30)
        self.assertEqual(self.setting.ip_block_threshold, 10)
        self.assertEqual(self.setting.trusted_device_days, 14)
        self.assertEqual(self.setting.updated_by, self.admin_operator)

        log = ActionLog.objects.get(action=ActionLogAction.SECURITY_SETTING_UPDATE)
        self.assertEqual(log.user, self.admin_operator)
        self.assertEqual(log.data["changes"]["lockout_threshold"], {"before": 5, "after": 3})
        self.assertEqual(len(mail.outbox), 0)

    def test_out_of_range_and_non_numeric_values_rejected_without_500(self):
        self.client.force_login(self.admin_operator)
        bad_cases = (
            ("lockout_low", {"lockout_threshold": "2"}),
            ("lockout_high", {"lockout_threshold": "11"}),
            ("minutes_low", {"lockout_minutes": "4"}),
            ("minutes_high", {"lockout_minutes": "1441"}),
            ("ip_low", {"ip_block_threshold": "9"}),
            ("ip_high", {"ip_block_threshold": "101"}),
            ("days_low", {"trusted_device_days": "-1"}),
            ("days_high", {"trusted_device_days": "91"}),
            ("non_numeric", {"lockout_threshold": "abc"}),
        )
        for label, override in bad_cases:
            with self.subTest(case=label):
                resp = self.client.post(self.url, self._base_post_data(**override))
                self.assertEqual(resp.status_code, 200)
                self.setting.refresh_from_db()
                self.assertEqual(self.setting.lockout_threshold, 5)
                self.assertEqual(self.setting.lockout_minutes, 15)
                self.assertEqual(self.setting.ip_block_threshold, 20)
                self.assertEqual(self.setting.trusted_device_days, 30)
                self.assertEqual(ActionLog.objects.count(), 0)

    def test_weakening_changes_send_single_bcc_mail_and_strengthening_does_not(self):
        TOTPDevice.objects.create(
            user=self.admin_operator, name="default", confirmed=True
        )
        self.client.force_login(self.admin_operator)

        # 初期状態を中間値にしておく
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.login_notification_level = (
            SecuritySetting.LoginNotificationLevel.EVERY
        )
        self.setting.lockout_threshold = 5
        self.setting.lockout_minutes = 30
        self.setting.ip_block_threshold = 20
        self.setting.trusted_device_days = 15
        self.setting.notify_admins_on_account_lock = True
        self.setting.notify_admins_on_ip_block = True
        self.setting.save()

        weakening_cases = (
            ("enforcement_down", {"two_factor_enforcement": "admin_only"}),
            ("notif_down", {"login_notification_level": "new_device_only"}),
            ("lockout_up", {"lockout_threshold": "6"}),
            ("minutes_down", {"lockout_minutes": "20"}),
            ("ip_up", {"ip_block_threshold": "25"}),
            ("days_up", {"trusted_device_days": "30"}),
            ("lock_mail_off", {"notify_admins_on_account_lock": None}),
            ("ip_mail_off", {"notify_admins_on_ip_block": None}),
        )
        for label, override in weakening_cases:
            with self.subTest(weakening=label):
                # 毎回中間値に戻す
                SecuritySetting.objects.filter(id=1).update(
                    two_factor_enforcement="all",
                    login_notification_level="every",
                    lockout_threshold=5,
                    lockout_minutes=30,
                    ip_block_threshold=20,
                    trusted_device_days=15,
                    notify_admins_on_account_lock=True,
                    notify_admins_on_ip_block=True,
                )
                self.setting.refresh_from_db()
                mail.outbox.clear()

                payload = self._base_post_data()
                for k, v in override.items():
                    if v is None:
                        payload.pop(k, None)
                    else:
                        payload[k] = v

                with self.captureOnCommitCallbacks(execute=True):
                    resp = self.client.post(self.url, payload)
                self.assertRedirects(resp, self.url)
                self.assertEqual(len(mail.outbox), 1)
                self.assertEqual(mail.outbox[0].to, ["noreply@example.com"])
                self.assertCountEqual(
                    mail.outbox[0].bcc,
                    ["sec_admin@example.com", "sec_admin2@example.com"],
                )

        # 弱めない変更（厳格化）ではメールは送られない
        SecuritySetting.objects.filter(id=1).update(
            two_factor_enforcement="optional",
            login_notification_level="new_device_only",
            lockout_threshold=6,
            lockout_minutes=15,
            ip_block_threshold=30,
            trusted_device_days=30,
            notify_admins_on_account_lock=False,
            notify_admins_on_ip_block=False,
        )
        self.setting.refresh_from_db()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            resp_strict = self.client.post(
                self.url,
                {
                    "two_factor_enforcement": "all",
                    "login_notification_level": "every",
                    "lockout_threshold": "4",
                    "lockout_minutes": "30",
                    "ip_block_threshold": "15",
                    "trusted_device_days": "10",
                    "notify_admins_on_account_lock": "on",
                    "notify_admins_on_ip_block": "on",
                },
            )
        self.assertRedirects(resp_strict, self.url)
        self.assertEqual(len(mail.outbox), 0)

    def test_checkbox_unchecked_sets_false_and_trusted_days_zero_revokes_all_trust(
        self,
    ):
        dev = LoginDevice.objects.create(
            user=self.plain_user,
            token_hash=hash_device_token("trust-tok"),
            trusted_until=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.admin_operator)

        payload = self._base_post_data(trusted_device_days="0")
        payload.pop("notify_admins_on_account_lock", None)
        payload.pop("notify_admins_on_ip_block", None)

        resp = self.client.post(self.url, payload)
        self.assertRedirects(resp, self.url)

        self.setting.refresh_from_db()
        self.assertFalse(self.setting.notify_admins_on_account_lock)
        self.assertFalse(self.setting.notify_admins_on_ip_block)
        self.assertEqual(self.setting.trusted_device_days, 0)

        dev.refresh_from_db()
        self.assertIsNone(dev.trusted_until)

        # チェックありで送信すると True に戻る
        payload_on = self._base_post_data(
            notify_admins_on_account_lock="on",
            notify_admins_on_ip_block="on",
        )
        resp_on = self.client.post(self.url, payload_on)
        self.assertRedirects(resp_on, self.url)
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.notify_admins_on_account_lock)
        self.assertTrue(self.setting.notify_admins_on_ip_block)

    def test_status_counts_and_warnings(self):
        TOTPDevice.objects.create(user=self.admin_operator, name="c1", confirmed=True)
        TOTPDevice.objects.create(user=self.plain_user, name="u1", confirmed=False)
        AccountLock.objects.create(
            user=self.plain_user,
            failure_count=5,
            locked_until=timezone.now() + timedelta(minutes=10),
        )

        self.client.force_login(self.admin_operator)

        # 管理者 2 名・通知先 2 名・SMTP 設定あり → 3 警告なし
        resp_ok = self.client.get(self.url)
        html_ok = resp_ok.content.decode("utf-8")
        self.assertIn("1 名 / 3 名", html_ok)
        self.assertIn("1 名", html_ok)
        self.assertNotIn("2名以上を推奨します", html_ok)
        self.assertNotIn("通知を受け取れる管理者がいません", html_ok)
        self.assertNotIn("SMTP", html_ok)

        # 2 人目管理者を無効化 → 管理者 1 名警告
        self.second_admin.is_active = False
        self.second_admin.save()
        resp_one = self.client.get(self.url)
        self.assertContains(
            resp_one,
            "管理者が1名です。二段階認証の端末を紛失すると復旧できなくなるため、2名以上を推奨します",
        )

        # 1 人目管理者のメールも空にし、DEFAULT_FROM_EMAIL も空にする → 両方の警告が出る
        self.admin_operator.email = ""
        self.admin_operator.save()
        with override_settings(DEFAULT_FROM_EMAIL=""):
            resp_warn = self.client.get(self.url)
        self.assertContains(resp_warn, "通知を受け取れる管理者がいません")
        self.assertContains(resp_warn, "SMTP（既定差出人メールアドレス）が未設定")

    def test_status_query_count_constant_regardless_of_user_count_and_no_inline_styles(
        self,
    ):
        self.client.force_login(self.admin_operator)
        with CaptureQueriesContext(connection) as ctx_small:
            r1 = self.client.get(self.url)
        self.assertEqual(r1.status_code, 200)

        for i in range(8):
            u = CustomUser.objects.create_user(username=f"extra_u_{i}", password="pw")
            TOTPDevice.objects.create(user=u, name="d", confirmed=True)

        with CaptureQueriesContext(connection) as ctx_large:
            r2 = self.client.get(self.url)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(ctx_small), len(ctx_large))

        template_src = (
            Path(settings.BASE_DIR) / "templates" / "accounts" / "security_setting.html"
        ).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'style="[^"]*"', template_src), [])
