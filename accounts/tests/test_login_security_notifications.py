"""ログインセキュリティ Step 2 フェーズ E（通知メール）の単体テスト。"""

from datetime import datetime, timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.constants import ADMIN_ROLE_CODE
from accounts.login_security.notifications import (
    WeakenedSettingChange,
    is_email_configured,
    notify_account_lock,
    notify_ip_block,
    notify_login_success,
    notify_recovery_code_used,
    notify_recovery_codes_regenerated,
    notify_security_setting_weakened,
    notify_two_factor_disabled,
    notify_two_factor_enabled,
    notify_two_factor_reenrolled,
    notify_two_factor_reset,
    should_send_login_notification,
    summarize_user_agent,
)
from accounts.models import LoginAttempt, Role, SecuritySetting

User = get_user_model()


class NotificationsPureTests(SimpleTestCase):
    """通知判定・UA 要約・設定確認など純関数のテスト。"""

    def test_a_should_send_login_notification_all_4_cases(self):
        self.assertTrue(
            should_send_login_notification(
                SecuritySetting.LoginNotificationLevel.EVERY, True
            )
        )
        self.assertTrue(
            should_send_login_notification(
                SecuritySetting.LoginNotificationLevel.EVERY, False
            )
        )
        self.assertTrue(
            should_send_login_notification(
                SecuritySetting.LoginNotificationLevel.NEW_DEVICE_ONLY, True
            )
        )
        self.assertFalse(
            should_send_login_notification(
                SecuritySetting.LoginNotificationLevel.NEW_DEVICE_ONLY, False
            )
        )

    def test_d_summarize_user_agent(self):
        self.assertEqual(
            summarize_user_agent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
            "Chrome／Windows",
        )
        self.assertEqual(
            summarize_user_agent(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
            ),
            "Safari／macOS",
        )
        self.assertEqual(
            summarize_user_agent(
                "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
            ),
            "Firefox／Linux",
        )
        self.assertEqual(summarize_user_agent(""), "不明")
        self.assertEqual(summarize_user_agent(None), "不明")
        self.assertEqual(summarize_user_agent("CustomBot/1.0"), "不明")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class NotificationsTests(TestCase):
    """通知メール送信の統合単体テスト（locmem + captureOnCommitCallbacks）。"""

    def setUp(self):
        mail.outbox.clear()
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.user = User.objects.create_user(
            username="taro", password="pw", email="taro@example.com"
        )

    def test_b_login_success_subject_distinction(self):
        with self.captureOnCommitCallbacks(execute=True):
            notify_login_success(
                self.user,
                ip_address="203.0.113.10",
                user_agent="Chrome Windows",
                second_factor=LoginAttempt.SecondFactor.TOTP,
                is_new_device=False,
            )
            notify_login_success(
                self.user,
                ip_address="203.0.113.10",
                user_agent="Chrome Windows",
                second_factor=LoginAttempt.SecondFactor.TOTP,
                is_new_device=True,
            )

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].subject, "【FreeGroup2】ログイン通知")
        self.assertEqual(
            mail.outbox[1].subject, "【FreeGroup2】【要確認】新しい端末からのログイン"
        )
        self.assertNotIn("taro", mail.outbox[0].subject)
        self.assertNotIn("taro", mail.outbox[1].subject)

    def test_c_login_success_body_items_jst_and_no_url(self):
        utc_dt = datetime(2026, 11, 18, 0, 30, 0, tzinfo=dt_timezone.utc)
        with self.captureOnCommitCallbacks(execute=True):
            notify_login_success(
                self.user,
                ip_address="203.0.113.10",
                user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0",
                second_factor=LoginAttempt.SecondFactor.TOTP,
                is_new_device=False,
                login_at=utc_dt,
            )

        msg = mail.outbox[0]
        self.assertIn("2026-11-18 09:30:00 (JST)", msg.body)
        self.assertIn("taro", msg.body)
        self.assertIn("203.0.113.10", msg.body)
        self.assertIn("Chrome／Windows", msg.body)
        self.assertIn("認証アプリ", msg.body)
        self.assertIn("パスワードを変更", msg.body)
        self.assertNotIn("http://", msg.body.lower())
        self.assertNotIn("https://", msg.body.lower())

    def test_e_send_exception_caught_and_logged_without_pii(self):
        with self.assertLogs("accounts", level="ERROR") as cm:
            with patch(
                "accounts.login_security.notifications.EmailMessage.send",
                side_effect=RuntimeError("smtp down"),
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    notify_login_success(
                        self.user,
                        ip_address="203.0.113.10",
                        user_agent="Chrome",
                        second_factor=LoginAttempt.SecondFactor.NONE,
                        is_new_device=True,
                    )

        log_text = "\n".join(cm.output)
        self.assertIn(str(self.user.pk), log_text)
        self.assertIn("login_success", log_text)
        self.assertIn("RuntimeError", log_text)
        self.assertNotIn("taro@example.com", log_text)
        self.assertNotIn("203.0.113.10", log_text)

    def test_f_uses_get_connection_with_timeout_5(self):
        mock_conn = MagicMock()
        with patch(
            "accounts.login_security.notifications.get_connection",
            return_value=mock_conn,
        ) as mock_get_conn, patch(
            "accounts.login_security.notifications.EmailMessage.send"
        ):
            with self.captureOnCommitCallbacks(execute=True):
                notify_two_factor_enabled(self.user)

        mock_get_conn.assert_called_once_with(timeout=5)

    def test_g_sent_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                notify_two_factor_disabled(self.user)
                # コミット前はまだ送信されない
                self.assertEqual(len(mail.outbox), 0)
        # コミット後に送信される
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject, "【FreeGroup2】二段階認証を無効化しました"
        )

    def test_h_admin_notification_single_email_bcc_and_superuser_only(self):
        su = User.objects.create_user(
            username="su_only",
            password="pw",
            email="su@example.com",
            is_superuser=True,
        )
        adm = User.objects.create_user(
            username="adm_role",
            password="pw",
            email="adm@example.com",
            role=self.admin_role,
        )
        User.objects.create_user(
            username="adm_nomail",
            password="pw",
            email="",
            role=self.admin_role,
        )

        with self.captureOnCommitCallbacks(execute=True):
            notify_ip_block(ip_address="198.51.100.1")

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["noreply@example.com"])
        self.assertEqual(set(msg.bcc), {su.email, adm.email})

    def test_i_zero_admin_recipients_skips_without_error(self):
        with self.captureOnCommitCallbacks(execute=True):
            notify_ip_block(ip_address="198.51.100.2")
        self.assertEqual(len(mail.outbox), 0)

    def test_j_account_lock_notification_respects_admin_toggle(self):
        User.objects.create_user(
            username="adm_j",
            password="pw",
            email="adm_j@example.com",
            role=self.admin_role,
        )
        setting = SecuritySetting.objects.get(id=1)
        setting.notify_admins_on_account_lock = False
        setting.save()

        locked_until = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            notify_account_lock(
                self.user,
                ip_address="203.0.113.30",
                locked_until=locked_until,
                setting=setting,
            )
        # 設定 OFF でも本人宛は届く
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["taro@example.com"])

        mail.outbox.clear()
        setting.notify_admins_on_account_lock = True
        setting.save()
        with self.captureOnCommitCallbacks(execute=True):
            notify_account_lock(
                self.user,
                ip_address="203.0.113.30",
                locked_until=locked_until,
                setting=setting,
            )
        # 設定 ON なら本人宛＋管理者宛の 2 通
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ["taro@example.com"])
        self.assertEqual(mail.outbox[1].bcc, ["adm_j@example.com"])

    def test_k_ip_block_notification_respects_admin_toggle(self):
        User.objects.create_user(
            username="adm_k",
            password="pw",
            email="adm_k@example.com",
            role=self.admin_role,
        )
        setting = SecuritySetting.objects.get(id=1)
        setting.notify_admins_on_ip_block = False
        setting.save()
        with self.captureOnCommitCallbacks(execute=True):
            notify_ip_block(ip_address="198.51.100.3", setting=setting)
        self.assertEqual(len(mail.outbox), 0)

        setting.notify_admins_on_ip_block = True
        setting.save()
        with self.captureOnCommitCallbacks(execute=True):
            notify_ip_block(ip_address="198.51.100.3", setting=setting)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("198.51.100.3", mail.outbox[0].body)
        self.assertIn("20", mail.outbox[0].body)

    def test_l_admin_reset_always_sent_excludes_self_from_bcc_and_hides_reason_from_user(self):
        target_admin = User.objects.create_user(
            username="target_adm",
            password="pw",
            email="TargetAdm@example.com",
            role=self.admin_role,
        )
        other_admin = User.objects.create_user(
            username="other_adm",
            password="pw",
            email="other_adm@example.com",
            role=self.admin_role,
        )
        # 通知 OFF 設定でもリセット通知は常に送られる
        setting = SecuritySetting.objects.get(id=1)
        setting.notify_admins_on_account_lock = False
        setting.notify_admins_on_ip_block = False
        setting.save()

        with self.captureOnCommitCallbacks(execute=True):
            notify_two_factor_reset(
                target_admin,
                operator=None,
                reason_note="端末紛失による緊急対応",
            )

        self.assertEqual(len(mail.outbox), 2)
        user_msg, admin_msg = mail.outbox[0], mail.outbox[1]
        self.assertEqual(user_msg.to, ["TargetAdm@example.com"])
        self.assertIn("サーバー管理コマンド", user_msg.body)
        self.assertIn("速やかに認証アプリの再登録", user_msg.body)
        self.assertNotIn("端末紛失による緊急対応", user_msg.body)
        self.assertNotIn("http", user_msg.body.lower())

        # 本人（大文字小文字違い）は Bcc から除外される
        self.assertEqual(admin_msg.bcc, ["other_adm@example.com"])
        self.assertIn("端末紛失による緊急対応", admin_msg.body)

    def test_m_weakened_setting_notification_always_sent(self):
        User.objects.create_user(
            username="adm_m",
            password="pw",
            email="adm_m@example.com",
            role=self.admin_role,
        )
        with self.captureOnCommitCallbacks(execute=True):
            notify_security_setting_weakened(
                operator=self.user,
                changes=[
                    WeakenedSettingChange("ロック失敗回数", "5", "10"),
                    WeakenedSettingChange("二段階認証強制レベル", "全員必須", "任意"),
                ],
            )
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("taro", body)
        self.assertIn("ロック失敗回数", body)
        self.assertIn("5 → 10", body)
        self.assertIn("全員必須 → 任意", body)

    def test_n_empty_user_email_or_empty_default_from_email_skips(self):
        self.assertTrue(is_email_configured())
        no_email_user = User.objects.create_user(
            username="nomail_u", password="pw", email=""
        )
        with self.captureOnCommitCallbacks(execute=True):
            sent = notify_login_success(
                no_email_user,
                ip_address="203.0.113.1",
                user_agent="Chrome",
                second_factor=LoginAttempt.SecondFactor.NONE,
                is_new_device=True,
            )
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

        with override_settings(DEFAULT_FROM_EMAIL=""):
            self.assertFalse(is_email_configured())
            with self.captureOnCommitCallbacks(execute=True):
                sent2 = notify_login_success(
                    self.user,
                    ip_address="203.0.113.1",
                    user_agent="Chrome",
                    second_factor=LoginAttempt.SecondFactor.NONE,
                    is_new_device=True,
                )
            self.assertFalse(sent2)
            self.assertEqual(len(mail.outbox), 0)

    def test_o_recovery_code_used_and_other_lifecycle_notifications(self):
        with self.captureOnCommitCallbacks(execute=True):
            notify_recovery_code_used(self.user, remaining_count=7)
            notify_recovery_codes_regenerated(self.user)
            notify_two_factor_reenrolled(self.user)

        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("7 個", mail.outbox[0].body)
        self.assertIn("再発行", mail.outbox[1].subject)
        self.assertIn("再登録", mail.outbox[2].subject)
