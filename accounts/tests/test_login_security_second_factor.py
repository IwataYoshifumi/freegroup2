"""ログインセキュリティ Step 2 フェーズ C（TOTP・リカバリーコード・要否判定）の単体テスト。"""

import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils.crypto import salted_hmac
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE
from accounts.login_security.policy import (
    SecondFactorRequirement,
    evaluate_second_factor_requirement,
    is_two_factor_required,
)
from accounts.login_security.recovery_codes import (
    RECOVERY_CODE_HMAC_SALT,
    get_remaining_recovery_code_count,
    hash_recovery_code,
    issue_recovery_codes,
    verify_and_consume_recovery_code,
)
from accounts.login_security.totp import (
    TotpVerifyStatus,
    confirm_totp_enrollment,
    has_confirmed_totp,
    start_totp_enrollment,
    verify_totp_code,
)
from accounts.models import RecoveryCode, Role, SecuritySetting

User = get_user_model()


def _current_totp_code(device: TOTPDevice) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


class TotpTests(TestCase):
    """TOTP 登録・検証・使用済みコード判別のテスト。"""

    def setUp(self):
        self.user = User.objects.create_user(username="totp_u", password="pw")

    @override_settings(OTP_TOTP_ISSUER="TestIssuer")
    def test_unconfirmed_device_not_treated_as_enrolled_and_qr_generated(self):
        enrollment = start_totp_enrollment(self.user)
        self.assertFalse(enrollment.device.confirmed)
        self.assertFalse(has_confirmed_totp(self.user))
        self.assertIn("TestIssuer", enrollment.config_url)
        self.assertTrue(len(enrollment.manual_secret) > 0)
        self.assertIn("<svg", enrollment.qr_svg)

        code = _current_totp_code(enrollment.device)
        # 未確定デバイスは検証対象にならない
        self.assertEqual(verify_totp_code(self.user, code), TotpVerifyStatus.INVALID)

        # 確認コードで確定できる
        self.assertEqual(
            confirm_totp_enrollment(enrollment.device, code), TotpVerifyStatus.VALID
        )
        self.assertTrue(has_confirmed_totp(self.user))

    def test_verify_valid_invalid_and_used_code_preserves_last_t(self):
        enrollment = start_totp_enrollment(self.user)
        device = enrollment.device
        device.confirmed = True
        device.save()

        code = _current_totp_code(device)
        self.assertEqual(verify_totp_code(self.user, code), TotpVerifyStatus.VALID)

        device.refresh_from_db()
        recorded_last_t = device.last_t
        self.assertGreaterEqual(recorded_last_t, 0)

        # 誤りコードは INVALID
        wrong = "000000" if code != "000000" else "999999"
        self.assertEqual(verify_totp_code(self.user, wrong), TotpVerifyStatus.INVALID)
        self.assertEqual(verify_totp_code(self.user, "abc"), TotpVerifyStatus.INVALID)

        # 同じコードの再利用は USED を返し、last_t を書き換えない
        self.assertEqual(verify_totp_code(self.user, code), TotpVerifyStatus.USED)
        device.refresh_from_db()
        self.assertEqual(device.last_t, recorded_last_t)

        # 判別のあとにもう一度送っても依然 USED であり、通らない
        self.assertEqual(verify_totp_code(self.user, code), TotpVerifyStatus.USED)

    def test_reenrollment_with_confirmed_and_unconfirmed_devices(self):
        old_dev = TOTPDevice.objects.create(user=self.user, name="old", confirmed=True)
        new_enroll = start_totp_enrollment(self.user)

        # 未確定の新しい登録が別レコードで存在しても、確定済みの旧登録で検証できる
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 2)
        old_code = _current_totp_code(old_dev)
        self.assertEqual(verify_totp_code(self.user, old_code), TotpVerifyStatus.VALID)

        # 未確定デバイスのコードは確定済み検索には引っかからない
        new_code = _current_totp_code(new_enroll.device)
        if new_code != old_code:
            self.assertEqual(
                verify_totp_code(self.user, new_code), TotpVerifyStatus.INVALID
            )

    def test_non_ascii_digits_and_invalid_inputs_return_invalid_without_exception(self):
        unconfirmed = start_totp_enrollment(self.user).device
        confirmed = TOTPDevice.objects.create(user=self.user, name="ok", confirmed=True)

        # isdigit() が True になる非 ASCII 数字（アラビア・インド数字）や桁数違い・上付き等
        bad_inputs = [
            "\u0661\u0662\u0663\u0664\u0665\u0666",
            "\u0967\u0968\u0969\u096a\u096b\u096c",
            "¹²³",
            "１２３４５",
            "12345a",
            "　---　",
        ]
        for bad in bad_inputs:
            with self.subTest(bad=bad):
                self.assertEqual(
                    verify_totp_code(self.user, bad), TotpVerifyStatus.INVALID
                )
                self.assertEqual(
                    confirm_totp_enrollment(unconfirmed, bad), TotpVerifyStatus.INVALID
                )
                self.assertFalse(unconfirmed.confirmed)
        self.assertTrue(confirmed.confirmed)

    def test_whitespace_and_fullwidth_digits_valid_code_succeeds(self):
        enroll = start_totp_enrollment(self.user)
        raw_code = _current_totp_code(enroll.device)
        fullwidth_trans = str.maketrans("0123456789", "０１２３４５６７８９")
        fullwidth_spaced = (
            raw_code[:3].translate(fullwidth_trans)
            + "\u3000"
            + raw_code[3:].translate(fullwidth_trans)
        )
        # confirm_totp_enrollment で全角数字＋全角空白の正しいコードが通る
        self.assertEqual(
            confirm_totp_enrollment(enroll.device, fullwidth_spaced),
            TotpVerifyStatus.VALID,
        )
        self.assertTrue(enroll.device.confirmed)

        # 別デバイスで途中に半角空白を入れたコードも verify_totp_code で通る
        dev2 = TOTPDevice.objects.create(user=self.user, name="dev2", confirmed=True)
        code2 = _current_totp_code(dev2)
        spaced_code2 = f" {code2[:3]} {code2[3:]} "
        self.assertEqual(verify_totp_code(self.user, spaced_code2), TotpVerifyStatus.VALID)


class RecoveryCodeTests(TestCase):
    """リカバリーコードの発行・SHA-256 鍵付きハッシュ・ローテーションのテスト。"""

    def setUp(self):
        self.user1 = User.objects.create_user(username="rc_u1", password="pw")
        self.user2 = User.objects.create_user(username="rc_u2", password="pw")

    def test_issue_format_and_hmac_sha256_storage(self):
        codes = issue_recovery_codes(self.user1)
        self.assertEqual(len(codes), 10)
        self.assertEqual(get_remaining_recovery_code_count(self.user1), 10)

        forbidden = set("0O1lI")
        for c in codes:
            self.assertRegex(c, r"^[2-9A-HJ-NP-Z]{5}-[2-9A-HJ-NP-Z]{5}$")
            self.assertTrue(forbidden.isdisjoint(set(c)))

        # SHA-256 鍵付きハッシュで保存されていること
        first_normalized = codes[0].replace("-", "").upper()
        expected_hash = salted_hmac(
            RECOVERY_CODE_HMAC_SALT,
            first_normalized,
            algorithm="sha256",
        ).hexdigest()
        self.assertEqual(hash_recovery_code(codes[0]), expected_hash)
        self.assertTrue(
            RecoveryCode.objects.filter(
                user=self.user1, code_hash=expected_hash
            ).exists()
        )

    def test_verify_normalization_one_time_and_other_user_isolation(self):
        codes = issue_recovery_codes(self.user1)
        target = codes[0]

        # 他人のリカバリーコードでは通らない
        self.assertFalse(verify_and_consume_recovery_code(self.user2, target))

        # ハイフンなし・空白あり・小文字でも通る
        variant = f"  {target.replace('-', '').lower()}  "
        self.assertTrue(verify_and_consume_recovery_code(self.user1, variant))
        self.assertEqual(get_remaining_recovery_code_count(self.user1), 9)

        # 1 回限り：2 回目は拒否
        self.assertFalse(verify_and_consume_recovery_code(self.user1, target))

    def test_secret_key_fallback_rotation_and_reissue(self):
        with override_settings(SECRET_KEY="old-secret-key", SECRET_KEY_FALLBACKS=[]):
            old_codes = issue_recovery_codes(self.user1)

        # 新鍵へ入れ替え・旧鍵を FALLBACKS に残している間は通る
        with override_settings(
            SECRET_KEY="new-secret-key",
            SECRET_KEY_FALLBACKS=["old-secret-key"],
        ):
            self.assertTrue(
                verify_and_consume_recovery_code(self.user1, old_codes[0])
            )

        # 旧鍵を外すと残りの旧コードは無効になる
        with override_settings(
            SECRET_KEY="new-secret-key",
            SECRET_KEY_FALLBACKS=[],
        ):
            self.assertFalse(
                verify_and_consume_recovery_code(self.user1, old_codes[1])
            )

        # 再発行で旧コードはすべて無効になり新しい 10 個になる
        new_codes = issue_recovery_codes(self.user1)
        self.assertEqual(get_remaining_recovery_code_count(self.user1), 10)
        self.assertFalse(verify_and_consume_recovery_code(self.user1, old_codes[2]))
        self.assertTrue(verify_and_consume_recovery_code(self.user1, new_codes[0]))

    def test_fullwidth_lowercase_and_fullwidth_hyphen_accepted_once(self):
        codes = issue_recovery_codes(self.user1)
        target = codes[0]
        to_fullwidth = str.maketrans(
            "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-",
            "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ－",
        )
        # 小文字＋全角英数字＋全角ハイフン＋全角空白
        mangled = "\u3000" + target.lower().translate(to_fullwidth) + "\u3000"
        self.assertTrue(verify_and_consume_recovery_code(self.user1, mangled))
        self.assertEqual(get_remaining_recovery_code_count(self.user1), 9)
        # 1 回限り：半角正規形でも 2 回目は拒否
        self.assertFalse(verify_and_consume_recovery_code(self.user1, target))

    def test_issue_recovery_codes_rolls_back_on_bulk_create_error(self):
        old_codes = issue_recovery_codes(self.user1)
        with patch.object(
            RecoveryCode.objects, "bulk_create", side_effect=RuntimeError("db error")
        ):
            with self.assertRaises(RuntimeError):
                issue_recovery_codes(self.user1)

        # atomic により旧コード 10 個がそのまま残っている
        self.assertEqual(get_remaining_recovery_code_count(self.user1), 10)
        self.assertTrue(verify_and_consume_recovery_code(self.user1, old_codes[0]))


class PolicyTests(TestCase):
    """強制レベルによる必須対象判定と第二要素要否判定のテスト。"""

    def setUp(self):
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.sales_role = Role.objects.get(code="sales")
        self.u_su = User.objects.create_user(
            username="p_su", password="pw", is_superuser=True
        )
        self.u_admin = User.objects.create_user(
            username="p_adm", password="pw", role=self.admin_role
        )
        self.u_staff = User.objects.create_user(
            username="p_staff", password="pw", is_staff=True
        )
        self.u_sales = User.objects.create_user(
            username="p_sales", password="pw", role=self.sales_role
        )
        self.u_inactive = User.objects.create_user(
            username="p_inact", password="pw", is_superuser=True, is_active=False
        )

    def test_is_two_factor_required_by_enforcement_level(self):
        setting = SecuritySetting.objects.get(id=1)

        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.OPTIONAL
        setting.save()
        for u in (self.u_su, self.u_admin, self.u_staff, self.u_sales, self.u_inactive):
            self.assertFalse(is_two_factor_required(u, setting))

        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ADMIN_ONLY
        setting.save()
        self.assertTrue(is_two_factor_required(self.u_su, setting))
        self.assertTrue(is_two_factor_required(self.u_admin, setting))
        self.assertTrue(is_two_factor_required(self.u_staff, setting))
        self.assertFalse(is_two_factor_required(self.u_sales, setting))
        self.assertFalse(is_two_factor_required(self.u_inactive, setting))

        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        setting.save()
        self.assertTrue(is_two_factor_required(self.u_su, setting))
        self.assertTrue(is_two_factor_required(self.u_sales, setting))
        self.assertFalse(is_two_factor_required(self.u_inactive, setting))
        self.assertFalse(is_two_factor_required(None, setting))

    def test_evaluate_second_factor_requirement(self):
        self.assertEqual(
            evaluate_second_factor_requirement(self.u_sales, is_trusted_device=False),
            SecondFactorRequirement.NOT_ENROLLED,
        )
        self.assertEqual(
            evaluate_second_factor_requirement(self.u_sales, is_trusted_device=True),
            SecondFactorRequirement.NOT_ENROLLED,
        )

        TOTPDevice.objects.create(user=self.u_sales, name="default", confirmed=True)
        self.assertEqual(
            evaluate_second_factor_requirement(self.u_sales, is_trusted_device=True),
            SecondFactorRequirement.TRUSTED_DEVICE,
        )
        self.assertEqual(
            evaluate_second_factor_requirement(self.u_sales, is_trusted_device=False),
            SecondFactorRequirement.REQUIRED,
        )
