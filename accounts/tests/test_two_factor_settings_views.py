"""ログインセキュリティ Step 4-2b-1（二段階認証の設定画面・端末一覧・信頼取り消し/削除）のテスト。"""

from datetime import timedelta
from pathlib import Path
import re

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ActionLogAction
from accounts.login_security.devices import hash_device_token
from accounts.login_security.recovery_codes import issue_recovery_codes
from accounts.models import CustomUser, LoginDevice, RecoveryCode, SecuritySetting
from actionlogs.models import ActionLog


class TwoFactorSettingsAndDeviceRevokeTests(TestCase):
    """accounts:two_factor と accounts:login_device_revoke の結合テスト。"""

    def setUp(self):
        self.settings_url = reverse("accounts:two_factor")
        self.setup_url = reverse("accounts:two_factor_setup")
        self.login_url = reverse("accounts:login")

        self.user = CustomUser.objects.create_user(
            username="tf_user",
            password="pw",
            email="tf_user@example.com",
        )
        self.other = CustomUser.objects.create_user(
            username="tf_other",
            password="pw",
            email="tf_other@example.com",
        )
        self.staff_user = CustomUser.objects.create_user(
            username="tf_staff",
            password="pw",
            email="tf_staff@example.com",
            is_staff=True,
        )

    def _revoke_url(self, device_id: int) -> str:
        return reverse("accounts:login_device_revoke", kwargs={"device_id": device_id})

    def test_anonymous_redirected_to_login_for_settings_and_revoke(self):
        dev = LoginDevice.objects.create(
            user=self.user,
            token_hash=hash_device_token("anon-token"),
        )
        resp_settings = self.client.get(self.settings_url)
        self.assertEqual(resp_settings.status_code, 302)
        self.assertIn(self.login_url, resp_settings.url)

        resp_revoke = self.client.post(
            self._revoke_url(dev.pk), {"action": "revoke"}
        )
        self.assertEqual(resp_revoke.status_code, 302)
        self.assertIn(self.login_url, resp_revoke.url)

    def test_staff_without_role_or_groups_can_open_settings_page(self):
        self.assertIsNone(self.staff_user.role)
        self.assertEqual(self.staff_user.groups.count(), 0)
        self.client.force_login(self.staff_user)

        resp = self.client.get(self.settings_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "未登録")

    def test_enrolled_vs_unconfirmed_vs_unregistered_status_display(self):
        self.client.force_login(self.user)

        # 未登録
        resp_none = self.client.get(self.settings_url)
        self.assertEqual(resp_none.status_code, 200)
        self.assertContains(resp_none, "未登録")
        self.assertContains(resp_none, self.setup_url)
        self.assertContains(resp_none, "登録を開始")

        # 未確定の登録のみ → 引き続き「未登録」
        TOTPDevice.objects.create(user=self.user, name="unconfirmed", confirmed=False)
        resp_unconf = self.client.get(self.settings_url)
        self.assertEqual(resp_unconf.status_code, 200)
        self.assertContains(resp_unconf, "未登録")
        self.assertNotContains(resp_unconf, "登録済み")

        # 確定済み登録あり → 「登録済み」
        TOTPDevice.objects.create(user=self.user, name="confirmed", confirmed=True)
        resp_conf = self.client.get(self.settings_url)
        self.assertEqual(resp_conf.status_code, 200)
        self.assertContains(resp_conf, "登録済み")
        self.assertNotContains(resp_conf, "登録を開始")

    def test_recovery_code_remaining_count_and_warning_threshold(self):
        TOTPDevice.objects.create(user=self.user, name="confirmed", confirmed=True)
        issue_recovery_codes(self.user)
        self.client.force_login(self.user)

        codes = list(RecoveryCode.objects.filter(user=self.user).order_by("id"))

        # 残り 4 個 → 警告なし
        RecoveryCode.objects.filter(pk__in=[c.pk for c in codes[:6]]).update(
            used_at=timezone.now()
        )
        resp_4 = self.client.get(self.settings_url)
        self.assertContains(resp_4, "残り 4 個")
        self.assertNotContains(resp_4, "app-alert--warning")

        # 残り 3 個 → 警告あり
        RecoveryCode.objects.filter(pk=codes[6].pk).update(used_at=timezone.now())
        resp_3 = self.client.get(self.settings_url)
        self.assertContains(resp_3, "残り 3 個")
        self.assertContains(resp_3, "app-alert--warning")

        # 残り 0 個 → 警告あり
        RecoveryCode.objects.filter(pk__in=[c.pk for c in codes[7:]]).update(
            used_at=timezone.now()
        )
        resp_0 = self.client.get(self.settings_url)
        self.assertContains(resp_0, "残り 0 個")
        self.assertContains(resp_0, "app-alert--warning")

    def test_device_list_shows_only_own_devices_marks_current_and_hides_token_hash(self):
        now = timezone.now()
        own_current_token = "own-current-secret-token"
        own_current_hash = hash_device_token(own_current_token)
        own_other_hash = hash_device_token("own-other-token")
        other_user_hash = hash_device_token("other-user-token")

        LoginDevice.objects.create(
            user=self.user,
            token_hash=own_current_hash,
            first_seen_at=now - timedelta(days=5),
            last_seen_at=now - timedelta(minutes=10),
            last_ip="203.0.113.10",
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0.0.0",
            trusted_until=now + timedelta(days=20),
        )
        LoginDevice.objects.create(
            user=self.user,
            token_hash=own_other_hash,
            first_seen_at=now - timedelta(days=10),
            last_seen_at=now - timedelta(days=1),
            last_ip="203.0.113.11",
            user_agent="Mozilla/5.0 (iPhone) Safari/604.1",
            trusted_until=None,
        )
        LoginDevice.objects.create(
            user=self.other,
            token_hash=other_user_hash,
            first_seen_at=now - timedelta(days=2),
            last_seen_at=now - timedelta(minutes=5),
            last_ip="198.51.100.77",
            user_agent="Mozilla/5.0 (Macintosh) Firefox/120.0",
            trusted_until=now + timedelta(days=10),
        )

        cookie_name = getattr(settings, "DEVICE_COOKIE_NAME", "freegroup2_device_8000")
        self.client.force_login(self.user)
        self.client.cookies[cookie_name] = own_current_token

        resp = self.client.get(self.settings_url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertIn("Chrome／Windows", html)
        self.assertIn("203.0.113.10", html)
        self.assertIn("この端末", html)
        self.assertIn("Safari／iOS", html)
        self.assertIn("203.0.113.11", html)
        self.assertIn("信頼中", html)
        self.assertIn("信頼なし", html)
        self.assertIn("信頼を取り消す", html)
        self.assertIn("記録を削除", html)

        # 他ユーザーの端末およびトークンハッシュが出ない
        self.assertNotIn("198.51.100.77", html)
        self.assertNotIn("Firefox／macOS", html)
        self.assertNotIn(own_current_hash, html)
        self.assertNotIn(own_other_hash, html)
        self.assertNotIn(other_user_hash, html)
        self.assertNotIn(own_current_token, html)

        # 4-2b-2 のボタンはまだ置かない
        self.assertNotIn("二段階認証を無効化", html)
        self.assertNotIn("リカバリーコードを再発行", html)

    def test_query_count_does_not_scale_with_number_of_devices(self):
        now = timezone.now()
        TOTPDevice.objects.create(user=self.user, name="confirmed", confirmed=True)
        issue_recovery_codes(self.user)
        LoginDevice.objects.create(
            user=self.user,
            token_hash=hash_device_token("q-tok-0"),
            last_seen_at=now,
            user_agent="Chrome/130.0",
        )
        # ミドルウェア用に SecuritySetting を事前に作成しておく
        SecuritySetting.objects.get_or_create(id=1)

        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as ctx_1:
            resp_1 = self.client.get(self.settings_url)
        self.assertEqual(resp_1.status_code, 200)

        for i in range(1, 5):
            LoginDevice.objects.create(
                user=self.user,
                token_hash=hash_device_token(f"q-tok-{i}"),
                last_seen_at=now - timedelta(minutes=i),
                user_agent="Chrome/130.0",
            )

        with CaptureQueriesContext(connection) as ctx_5:
            resp_5 = self.client.get(self.settings_url)
        self.assertEqual(resp_5.status_code, 200)
        self.assertEqual(len(ctx_1), len(ctx_5))

    def test_revoke_trust_clears_trusted_until_keeps_device_logs_and_redirects(self):
        now = timezone.now()
        dev = LoginDevice.objects.create(
            user=self.user,
            token_hash=hash_device_token("revoke-tok"),
            trusted_until=now + timedelta(days=30),
        )
        self.client.force_login(self.user)

        resp = self.client.post(self._revoke_url(dev.pk), {"action": "revoke"})
        self.assertRedirects(resp, self.settings_url)

        dev.refresh_from_db()
        self.assertIsNone(dev.trusted_until)

        log = ActionLog.objects.get(action=ActionLogAction.LOGIN_DEVICE_REVOKE)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.data.get("mode"), "revoke_trust")
        self.assertNotIn(dev.token_hash, f"{log.object_repr} {log.note} {log.data}")

    def test_delete_device_removes_record_even_for_current_device_and_logs(self):
        now = timezone.now()
        plain_token = "current-delete-token"
        dev = LoginDevice.objects.create(
            user=self.user,
            token_hash=hash_device_token(plain_token),
            trusted_until=now + timedelta(days=30),
        )
        cookie_name = getattr(settings, "DEVICE_COOKIE_NAME", "freegroup2_device_8000")
        self.client.force_login(self.user)
        self.client.cookies[cookie_name] = plain_token

        resp = self.client.post(self._revoke_url(dev.pk), {"action": "delete"})
        self.assertRedirects(resp, self.settings_url)

        self.assertFalse(LoginDevice.objects.filter(pk=dev.pk).exists())
        log = ActionLog.objects.get(action=ActionLogAction.LOGIN_DEVICE_REVOKE)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.data.get("mode"), "delete")
        self.assertNotIn(plain_token, f"{log.object_repr} {log.note} {log.data}")

    def test_revoke_other_users_device_returns_404_and_makes_no_changes(self):
        other_dev = LoginDevice.objects.create(
            user=self.other,
            token_hash=hash_device_token("other-tok"),
            trusted_until=timezone.now() + timedelta(days=15),
        )
        self.client.force_login(self.user)

        for action in ("revoke", "delete"):
            with self.subTest(action=action):
                resp = self.client.post(
                    self._revoke_url(other_dev.pk), {"action": action}
                )
                self.assertEqual(resp.status_code, 404)
                other_dev.refresh_from_db()
                self.assertIsNotNone(other_dev.trusted_until)
                self.assertEqual(ActionLog.objects.count(), 0)

    def test_revoke_nonexistent_device_get_405_and_invalid_action_400(self):
        own_dev = LoginDevice.objects.create(
            user=self.user,
            token_hash=hash_device_token("own-tok"),
            trusted_until=timezone.now() + timedelta(days=15),
        )
        self.client.force_login(self.user)

        # 存在しない端末 ID → 404
        resp_missing = self.client.post(
            self._revoke_url(999999), {"action": "revoke"}
        )
        self.assertEqual(resp_missing.status_code, 404)

        # GET → 405
        resp_get = self.client.get(self._revoke_url(own_dev.pk))
        self.assertEqual(resp_get.status_code, 405)

        # 不正な action → 400、変更なし
        for bad_action in ("", "invalid", "revoke_trust"):
            with self.subTest(action=bad_action):
                resp_bad = self.client.post(
                    self._revoke_url(own_dev.pk), {"action": bad_action}
                )
                self.assertEqual(resp_bad.status_code, 400)
                own_dev.refresh_from_db()
                self.assertIsNotNone(own_dev.trusted_until)
                self.assertEqual(ActionLog.objects.count(), 0)

    def test_settings_page_has_cache_control_no_store_and_no_inline_styles(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.settings_url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))

        template_path = (
            Path(settings.BASE_DIR) / "templates" / "accounts" / "two_factor_settings.html"
        )
        template_src = template_path.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'style="[^"]*"', template_src), [])
