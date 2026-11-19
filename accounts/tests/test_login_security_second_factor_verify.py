"""ログインセキュリティ Step 2 フェーズ F-1（第二要素共通検証）の単体テスト。"""

from datetime import timedelta
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.login_security.failures import record_failure_and_notify
from accounts.login_security.recovery_codes import (
    get_remaining_recovery_code_count,
    issue_recovery_codes,
)
from accounts.login_security.second_factor import (
    SecondFactorMethod,
    SecondFactorVerifyStatus,
    verify_second_factor,
)
from accounts.models import AccountLock, CustomUser, LoginAttempt

User = get_user_model()


def _current_totp_code(device: TOTPDevice) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class SecondFactorVerifyTests(TestCase):
    """second_factor.verify_second_factor のテスト（観点 d〜l）。"""

    def setUp(self):
        mail.outbox.clear()
        self.user = User.objects.create_user(
            username="sf_u", password="pw", email="sf_u@example.com"
        )
        self.other = User.objects.create_user(
            username="sf_other", password="pw", email="sf_other@example.com"
        )
        self.device = TOTPDevice.objects.create(
            user=self.user, name="default", confirmed=True
        )

    def test_d_valid_totp_succeeds_without_changing_failure_count(self):
        AccountLock.objects.create(
            user=self.user, failure_count=3, last_failed_at=timezone.now()
        )
        code = _current_totp_code(self.device)
        res = verify_second_factor(
            self.user,
            code,
            method=SecondFactorMethod.TOTP,
            ip_address="203.0.113.10",
            user_agent="UA",
        )
        self.assertTrue(res.is_success)
        self.assertEqual(res.status, SecondFactorVerifyStatus.SUCCESS)
        self.assertEqual(res.method, "totp")
        self.assertFalse(res.entered_lock)
        # 成功しても失敗回数は増えも減りもしない
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 3)
        self.assertEqual(LoginAttempt.objects.count(), 0)

    def test_e_invalid_totp_increments_failure_and_records_attempt(self):
        code = _current_totp_code(self.device)
        wrong = "000000" if code != "000000" else "999999"
        res = verify_second_factor(
            self.user,
            wrong,
            method=SecondFactorMethod.TOTP,
            ip_address="203.0.113.10",
            user_agent="UA",
        )
        self.assertEqual(res.status, SecondFactorVerifyStatus.INVALID)
        self.assertFalse(res.entered_lock)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)
        attempt = LoginAttempt.objects.get()
        self.assertEqual(attempt.result, LoginAttempt.Result.CODE_FAILED)
        self.assertEqual(attempt.second_factor, LoginAttempt.SecondFactor.TOTP)
        self.assertEqual(attempt.user, self.user)

    def test_f_used_totp_returns_used_without_incrementing_or_recording(self):
        code = _current_totp_code(self.device)
        res1 = verify_second_factor(
            self.user, code, method=SecondFactorMethod.TOTP, ip_address="203.0.113.10"
        )
        self.assertTrue(res1.is_success)

        res2 = verify_second_factor(
            self.user, code, method=SecondFactorMethod.TOTP, ip_address="203.0.113.10"
        )
        self.assertEqual(res2.status, SecondFactorVerifyStatus.USED)
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())
        self.assertEqual(LoginAttempt.objects.count(), 0)

    def test_g_locked_user_rejected_without_calling_verifier(self):
        now = timezone.now()
        AccountLock.objects.create(
            user=self.user,
            failure_count=5,
            last_failed_at=now,
            locked_until=now + timedelta(minutes=10),
        )
        with patch(
            "accounts.login_security.second_factor.verify_totp_code"
        ) as mock_verify:
            res = verify_second_factor(
                self.user,
                "123456",
                method=SecondFactorMethod.TOTP,
                ip_address="203.0.113.10",
                now=now,
            )
        mock_verify.assert_not_called()
        self.assertTrue(res.is_rejected)
        self.assertEqual(res.status, SecondFactorVerifyStatus.REJECTED_LOCKED)
        attempt = LoginAttempt.objects.get()
        self.assertEqual(attempt.result, LoginAttempt.Result.REJECTED_LOCKED)
        self.assertEqual(attempt.second_factor, "")

    def test_h_ip_blocked_rejected_and_recorded(self):
        now = timezone.now()
        ip = "198.51.100.55"
        for i in range(20):
            record_failure_and_notify(
                username_input=f"u_{i}",
                target_users=[],
                primary_user=None,
                ip_address=ip,
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=now - timedelta(seconds=20 - i),
            )

        res = verify_second_factor(
            self.user,
            "123456",
            method=SecondFactorMethod.TOTP,
            ip_address=ip,
            now=now,
        )
        self.assertTrue(res.is_rejected)
        self.assertEqual(res.status, SecondFactorVerifyStatus.REJECTED_IP_BLOCKED)
        latest = LoginAttempt.objects.first()
        self.assertEqual(latest.result, LoginAttempt.Result.REJECTED_IP_BLOCKED)
        self.assertEqual(latest.second_factor, "")

    def test_i_recovery_code_success_one_time_and_sends_notification(self):
        codes = issue_recovery_codes(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            res1 = verify_second_factor(
                self.user,
                codes[0],
                method=SecondFactorMethod.RECOVERY_CODE,
                ip_address="203.0.113.10",
            )
        self.assertTrue(res1.is_success)
        self.assertEqual(get_remaining_recovery_code_count(self.user), 9)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("9 個", mail.outbox[0].body)
        self.assertNotIn(codes[0], mail.outbox[0].body)

        # 2 回目は不正
        res2 = verify_second_factor(
            self.user,
            codes[0],
            method=SecondFactorMethod.RECOVERY_CODE,
            ip_address="203.0.113.10",
        )
        self.assertEqual(res2.status, SecondFactorVerifyStatus.INVALID)

    def test_j_code_failures_accumulate_to_lock_and_notify(self):
        base = timezone.now()
        for i in range(4):
            res = verify_second_factor(
                self.user,
                "000000",
                method=SecondFactorMethod.RECOVERY_CODE,
                ip_address="203.0.113.10",
                now=base + timedelta(seconds=i),
            )
            self.assertFalse(res.entered_lock)

        with self.captureOnCommitCallbacks(execute=True):
            res5 = verify_second_factor(
                self.user,
                "000000",
                method=SecondFactorMethod.RECOVERY_CODE,
                ip_address="203.0.113.10",
                now=base + timedelta(seconds=5),
            )
        self.assertEqual(res5.status, SecondFactorVerifyStatus.INVALID)
        self.assertTrue(res5.entered_lock)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sf_u@example.com"])

    def test_k_other_users_recovery_code_fails_and_increments_target_user(self):
        other_codes = issue_recovery_codes(self.other)
        res = verify_second_factor(
            self.user,
            other_codes[0],
            method=SecondFactorMethod.RECOVERY_CODE,
            ip_address="203.0.113.10",
        )
        self.assertEqual(res.status, SecondFactorVerifyStatus.INVALID)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)
        self.assertFalse(AccountLock.objects.filter(user=self.other).exists())
        self.assertEqual(get_remaining_recovery_code_count(self.other), 10)

    def test_l_no_customuser_save_during_second_factor_verify(self):
        codes = issue_recovery_codes(self.user)
        with patch.object(CustomUser, "save", wraps=CustomUser.save) as mock_save:
            verify_second_factor(
                self.user,
                "000000",
                method=SecondFactorMethod.TOTP,
                ip_address="203.0.113.10",
            )
            with self.captureOnCommitCallbacks(execute=True):
                verify_second_factor(
                    self.user,
                    codes[0],
                    method=SecondFactorMethod.RECOVERY_CODE,
                    ip_address="203.0.113.10",
                )
            self.assertEqual(mock_save.call_count, 0)
