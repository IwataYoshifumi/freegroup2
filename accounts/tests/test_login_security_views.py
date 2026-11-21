"""ログインセキュリティ Step 3 フェーズ C（ログインビュー・第二要素入力ビュー）のテスト。"""

from datetime import timedelta
import re
import time
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.login_security.failures import record_failure_and_notify
from accounts.login_security.login_service import (
    COMMON_LOGIN_ERROR_MESSAGE,
    EXPIRED_PENDING_AUTH_MESSAGE,
    PENDING_AUTH_SESSION_KEY,
    PENDING_AUTH_TTL_SECONDS,
    USED_CODE_ERROR_MESSAGE,
)
from accounts.login_security.recovery_codes import issue_recovery_codes
from accounts.models import AccountLock, CustomUser, LoginAttempt, SecuritySetting


def _totp_code(device: TOTPDevice) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    CLIENT_IP_SOURCE="x_real_ip",
)
class LoginAndVerifyViewTests(TestCase):
    """ログインビュー（accounts:login）と第二要素入力ビュー（accounts:login_verify）のテスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.login_url = reverse("accounts:login")
        self.verify_url = reverse("accounts:login_verify")
        self.user = CustomUser.objects.create_user(
            username="taro",
            password="secret",
            email="taro@example.com",
        )

    def test_login_get_returns_200_without_authentication_form_fixed_text(self):
        resp = self.client.get(self.login_url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertNotIn("ユーザ名またはパスワードが正しくありません。", html)
        self.assertIn('name="csrfmiddlewaretoken"', html)

    def test_login_failures_and_rejections_show_same_common_error_message(self):
        # 1. パスワード誤り
        resp_wrong = self.client.post(
            self.login_url,
            {"username": "taro", "password": "wrong"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertEqual(resp_wrong.status_code, 200)
        self.assertContains(resp_wrong, COMMON_LOGIN_ERROR_MESSAGE)

        # 2. 不明ユーザー名
        resp_unknown = self.client.post(
            self.login_url,
            {"username": "no_such_user", "password": "wrong"},
            HTTP_X_REAL_IP="203.0.113.11",
        )
        self.assertEqual(resp_unknown.status_code, 200)
        self.assertContains(resp_unknown, COMMON_LOGIN_ERROR_MESSAGE)

        # 3. ロック中
        now = timezone.now()
        AccountLock.objects.update_or_create(
            user=self.user,
            defaults={
                "failure_count": 5,
                "last_failed_at": now,
                "locked_until": now + timedelta(minutes=15),
            },
        )
        resp_locked = self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.12",
        )
        self.assertEqual(resp_locked.status_code, 200)
        self.assertContains(resp_locked, COMMON_LOGIN_ERROR_MESSAGE)

        # 4. IP 遮断中
        ip_blocked = "198.51.100.77"
        for i in range(20):
            record_failure_and_notify(
                username_input=f"u_{i}",
                target_users=[],
                primary_user=None,
                ip_address=ip_blocked,
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=now - timedelta(seconds=20 - i),
            )
        resp_ip = self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP=ip_blocked,
        )
        self.assertEqual(resp_ip.status_code, 200)
        self.assertContains(resp_ip, COMMON_LOGIN_ERROR_MESSAGE)

    def test_first_factor_only_cannot_access_protected_pages_and_redirects_to_verify(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        resp = self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertRedirects(resp, self.verify_url)
        self.assertIn(PENDING_AUTH_SESSION_KEY, self.client.session)

        # 仮認証のまま他画面（ホーム・プロフィール）には入れない
        for protected_url in (reverse("home"), reverse("accounts:profile")):
            with self.subTest(url=protected_url):
                r = self.client.get(protected_url)
                self.assertEqual(r.status_code, 302)
                self.assertIn(self.login_url, r.url)

    def test_verify_redirects_to_login_when_pending_missing_or_expired(self):
        # 仮認証なし
        resp_missing = self.client.get(self.verify_url)
        self.assertRedirects(resp_missing, self.login_url)

        # 仮認証作成後に失効させる
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        session = self.client.session
        pending = dict(session[PENDING_AUTH_SESSION_KEY])
        pending["started_at"] = (
            timezone.now() - timedelta(seconds=PENDING_AUTH_TTL_SECONDS + 10)
        ).timestamp()
        session[PENDING_AUTH_SESSION_KEY] = pending
        session.save()

        resp_expired = self.client.get(self.verify_url, follow=True)
        self.assertRedirects(resp_expired, self.login_url)
        self.assertContains(resp_expired, EXPIRED_PENDING_AUTH_MESSAGE)

    def test_totp_and_recovery_code_success_complete_login_and_redirect(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        codes = issue_recovery_codes(self.user)
        target_next = reverse("accounts:profile")

        # 1. 認証アプリ成功
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret", "next": target_next},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        with self.captureOnCommitCallbacks(execute=True):
            resp_totp = self.client.post(
                self.verify_url,
                {"method": "totp", "code": _totp_code(dev)},
                HTTP_X_REAL_IP="203.0.113.10",
            )
        self.assertRedirects(resp_totp, target_next)
        self.client.logout()

        # 2. リカバリーコード成功
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret", "next": target_next},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        with self.captureOnCommitCallbacks(execute=True):
            resp_rec = self.client.post(
                self.verify_url,
                {"method": "recovery_code", "code": codes[0]},
                HTTP_X_REAL_IP="203.0.113.10",
            )
        self.assertRedirects(resp_rec, target_next)

    def test_used_code_shows_dedicated_message_keeps_pending_and_no_failure_count(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        code = _totp_code(dev)
        # 1 回目で使用済みにする
        dev.verify_token(code)

        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        resp = self.client.post(
            self.verify_url,
            {"method": "totp", "code": code},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, USED_CODE_ERROR_MESSAGE)
        self.assertIn(PENDING_AUTH_SESSION_KEY, self.client.session)
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())

    def test_invalid_code_shows_common_error_and_increments_failure_count(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        wrong = "000000" if _totp_code(dev) != "000000" else "999999"

        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        resp = self.client.post(
            self.verify_url,
            {"method": "totp", "code": wrong},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, COMMON_LOGIN_ERROR_MESSAGE)
        self.assertIn(PENDING_AUTH_SESSION_KEY, self.client.session)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)

    def test_lock_or_ip_block_during_verify_redirects_to_login_and_destroys_pending(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        now = timezone.now()

        # ロック中に入る
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        AccountLock.objects.create(
            user=self.user,
            failure_count=5,
            last_failed_at=now,
            locked_until=now + timedelta(minutes=15),
        )
        resp_lock = self.client.post(
            self.verify_url,
            {"method": "totp", "code": "123456"},
            HTTP_X_REAL_IP="203.0.113.10",
            follow=True,
        )
        self.assertRedirects(resp_lock, self.login_url)
        self.assertContains(resp_lock, COMMON_LOGIN_ERROR_MESSAGE)
        self.assertNotIn(PENDING_AUTH_SESSION_KEY, self.client.session)

        # IP 遮断中に入る
        AccountLock.objects.filter(user=self.user).delete()
        ip_block = "198.51.100.88"
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP=ip_block,
        )
        for i in range(20):
            record_failure_and_notify(
                username_input=f"b_{i}",
                target_users=[],
                primary_user=None,
                ip_address=ip_block,
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=now - timedelta(seconds=20 - i),
            )
        resp_ip = self.client.post(
            self.verify_url,
            {"method": "totp", "code": "123456"},
            HTTP_X_REAL_IP=ip_block,
            follow=True,
        )
        self.assertRedirects(resp_ip, self.login_url)
        self.assertContains(resp_ip, COMMON_LOGIN_ERROR_MESSAGE)
        self.assertNotIn(PENDING_AUTH_SESSION_KEY, self.client.session)

    def test_trusted_device_checkbox_and_zero_days_hiding(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        codes = issue_recovery_codes(self.user)

        # 信頼期間 0 のときはチェックボックスが表示されない
        setting = SecuritySetting.objects.get(id=1)
        setting.trusted_device_days = 0
        setting.save()
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        resp_zero = self.client.get(self.verify_url)
        self.assertNotContains(resp_zero, 'name="trust_device"')

        # 信頼期間 30 に戻し、チェックを付けて通過すると次回は第二要素省略
        setting.trusted_device_days = 30
        setting.save()
        resp_with_check = self.client.get(self.verify_url)
        self.assertContains(resp_with_check, 'name="trust_device"')

        resp_done = self.client.post(
            self.verify_url,
            {"method": "recovery_code", "code": codes[0], "trust_device": "on"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertRedirects(resp_done, reverse("home"))
        self.assertIn(settings.DEVICE_COOKIE_NAME, resp_done.cookies)
        self.client.post(reverse("accounts:logout"))

        # 次回ログイン時は verify を通らずホームへ直接完了
        resp_next = self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertRedirects(resp_next, reverse("home"))

    def test_unsafe_next_url_ignored_directly_and_across_pending_auth(self):
        home_url = reverse("home")
        # 1. 第二要素なし（直接完了）
        resp1 = self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret", "next": "https://evil.example/"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertRedirects(resp1, home_url)
        self.client.logout()

        # 2. 仮認証をまたぐ場合
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        codes = issue_recovery_codes(self.user)
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret", "next": "//evil.example/phish"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        resp2 = self.client.post(
            self.verify_url,
            {"method": "recovery_code", "code": codes[0]},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        self.assertRedirects(resp2, home_url)

    def test_device_cookie_set_on_completion_and_secure_only_on_https(self):
        # HTTP 時は Secure なし
        resp_http = self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )
        cookie_http = resp_http.cookies[settings.DEVICE_COOKIE_NAME]
        self.assertTrue(cookie_http.value)
        self.assertTrue(cookie_http["httponly"])
        self.assertFalse(cookie_http["secure"])
        self.client.logout()

        # HTTPS 時は Secure あり
        resp_https = self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
            secure=True,
        )
        cookie_https = resp_https.cookies[settings.DEVICE_COOKIE_NAME]
        self.assertTrue(cookie_https["secure"])

    def test_login_succeeds_even_when_notification_mail_sending_fails(self):
        with patch(
            "accounts.login_security.notifications.get_connection",
            side_effect=OSError("SMTP connection failed"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(
                    self.login_url,
                    {"username": "taro", "password": "secret"},
                    HTTP_X_REAL_IP="203.0.113.10",
                )
        self.assertRedirects(resp, reverse("home"))
        self.assertIn("_auth_user_id", self.client.session)

    def test_verify_template_input_attributes_and_single_inline_style(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        self.client.post(
            self.login_url,
            {"username": "taro", "password": "secret"},
            HTTP_X_REAL_IP="203.0.113.10",
        )

        # 1. 認証アプリモード
        resp_totp = self.client.get(self.verify_url)
        html_totp = resp_totp.content.decode("utf-8")
        self.assertIn('inputmode="numeric"', html_totp)
        self.assertIn('autocomplete="one-time-code"', html_totp)
        code_input_match = re.search(r'<input[^>]*id="id_code"[^>]*>', html_totp)
        self.assertIsNotNone(code_input_match)
        code_tag_totp = code_input_match.group(0)
        self.assertIn("font-family: monospace", code_tag_totp)
        self.assertIn("font-size:", code_tag_totp)
        self.assertIn("font-weight: bold", code_tag_totp)
        self.assertIn("color: #000", code_tag_totp)

        # 2. リカバリーコードモード
        resp_rec = self.client.get(f"{self.verify_url}?method=recovery_code")
        html_rec = resp_rec.content.decode("utf-8")
        self.assertNotIn("inputmode=", html_rec)
        self.assertNotIn('autocomplete="one-time-code"', html_rec)
        rec_input_match = re.search(r'<input[^>]*id="id_code"[^>]*>', html_rec)
        self.assertIsNotNone(rec_input_match)
        code_tag_rec = rec_input_match.group(0)
        self.assertIn("font-family: monospace", code_tag_rec)
        self.assertIn("font-size:", code_tag_rec)
        self.assertIn("font-weight: bold", code_tag_rec)
        self.assertIn("color: #000", code_tag_rec)
