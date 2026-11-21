"""DEBUG=True 環境での BackNavigator・各種画面レンダリング回帰防止テスト。"""

from datetime import timedelta

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.login_security.user_lookup import get_security_setting
from accounts.models import AccountLock, CustomUser, SecuritySetting


def _grant_perms(user: CustomUser, *codenames: str) -> None:
    perms = list(
        Permission.objects.filter(
            content_type__app_label="accounts",
            codename__in=codenames,
        )
    )
    if len(perms) != len(codenames):
        found = {p.codename for p in perms}
        missing = sorted(set(codenames) - found)
        raise AssertionError(f"Missing permissions: {missing}")
    user.user_permissions.add(*perms)


@override_settings(DEBUG=True)
class BackNavigatorDebugModeGetTests(TestCase):
    """DEBUG=True の状態で各画面の GET が 500 にならないことを確認する。"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="dbg_user",
            password="pw",
            email="dbg_user@example.com",
        )
        self.admin = CustomUser.objects.create_user(
            username="dbg_admin",
            password="pw",
            email="dbg_admin@example.com",
        )
        _grant_perms(
            self.admin,
            "retire_user",
            "manage_security_setting",
            "reset_two_factor",
            "view_loginattempt",
        )
        self.target = CustomUser.objects.create_user(
            username="dbg_target",
            password="pw",
            email="dbg_target@example.com",
        )

    def _enroll(self, user: CustomUser) -> TOTPDevice:
        return TOTPDevice.objects.create(user=user, name="default", confirmed=True)

    def test_two_factor_settings_get_in_debug_mode(self):
        url = reverse("accounts:two_factor")
        self.client.force_login(self.user)

        # 未登録
        resp_unreg = self.client.get(url)
        self.assertEqual(resp_unreg.status_code, 200)
        self.assertContains(resp_unreg, "未登録")

        # 登録済み
        self._enroll(self.user)
        resp_enr = self.client.get(url)
        self.assertEqual(resp_enr.status_code, 200)
        self.assertContains(resp_enr, "登録済み")

    def test_two_factor_setup_get_in_debug_mode(self):
        url = reverse("accounts:two_factor_setup")
        self.client.force_login(self.user)

        # 未登録
        resp_unreg = self.client.get(url)
        self.assertEqual(resp_unreg.status_code, 200)

        # 登録済み
        self._enroll(self.user)
        resp_enr = self.client.get(url)
        self.assertEqual(resp_enr.status_code, 200)

    def test_two_factor_recovery_codes_get_in_debug_mode(self):
        url = reverse("accounts:two_factor_recovery_codes")
        self.client.force_login(self.user)

        # 未登録
        resp_unreg = self.client.get(url)
        self.assertEqual(resp_unreg.status_code, 200)

        # 登録済み
        self._enroll(self.user)
        resp_enr = self.client.get(url)
        self.assertEqual(resp_enr.status_code, 200)

    def test_two_factor_disable_get_in_debug_mode(self):
        url = reverse("accounts:two_factor_disable")
        self.client.force_login(self.user)

        # 1) 未登録
        resp_unreg = self.client.get(url)
        self.assertEqual(resp_unreg.status_code, 200)

        # 2) 登録済み（任意）
        self._enroll(self.user)
        resp_enr = self.client.get(url)
        self.assertEqual(resp_enr.status_code, 200)

        # 3) 必須対象（全員必須設定＋登録済み）
        setting = get_security_setting()
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        setting.save()
        resp_req = self.client.get(url)
        self.assertEqual(resp_req.status_code, 200)

    def test_admin_and_existing_screens_get_in_debug_mode(self):
        self.client.force_login(self.admin)
        endpoints = [
            ("security", reverse("accounts:security_setting"), 200),
            (
                "reset_other",
                reverse("accounts:two_factor_reset", kwargs={"user_id": self.target.pk}),
                200,
            ),
            (
                "reset_self",
                reverse("accounts:two_factor_reset", kwargs={"user_id": self.admin.pk}),
                400,
            ),
            ("login_attempts", reverse("accounts:login_attempt_list"), 200),
            ("profile", reverse("accounts:profile"), 200),
            ("user_list", reverse("accounts:user_list"), 200),
            (
                "user_detail",
                reverse("accounts:user_detail", kwargs={"user_id": self.target.pk}),
                200,
            ),
        ]
        for label, url, expected_status in endpoints:
            with self.subTest(endpoint=label):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, expected_status)


@override_settings(DEBUG=True)
class BackNavigatorDebugModePostErrorTests(TestCase):
    """DEBUG=True の状態で POST のエラー再表示経路が 500 にならないことを確認する。"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="dbg_post_user",
            password="pw",
            email="dbg_post_user@example.com",
        )
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)

        self.admin = CustomUser.objects.create_user(
            username="dbg_post_admin",
            password="pw",
            email="dbg_post_admin@example.com",
        )
        _grant_perms(self.admin, "retire_user", "reset_two_factor")
        self.target = CustomUser.objects.create_user(
            username="dbg_post_target",
            password="pw",
            email="dbg_post_target@example.com",
        )

    def test_disable_post_error_rerenders_in_debug_mode(self):
        url = reverse("accounts:two_factor_disable")
        self.client.force_login(self.user)

        # 誤ったコード
        resp_bad_code = self.client.post(url, {"code": "000000"})
        self.assertEqual(resp_bad_code.status_code, 200)

        # ロック中
        AccountLock.objects.update_or_create(
            user=self.user,
            defaults={
                "failure_count": 5,
                "locked_until": timezone.now() + timedelta(minutes=15),
            },
        )
        resp_locked = self.client.post(url, {"code": "000000"})
        self.assertEqual(resp_locked.status_code, 403)

    def test_recovery_codes_regenerate_post_error_rerenders_in_debug_mode(self):
        url = reverse("accounts:two_factor_recovery_codes")
        self.client.force_login(self.user)

        # 誤ったコード
        resp_bad_code = self.client.post(
            url, {"action": "regenerate", "code": "000000"}
        )
        self.assertEqual(resp_bad_code.status_code, 200)

        # ロック中
        AccountLock.objects.update_or_create(
            user=self.user,
            defaults={
                "failure_count": 5,
                "locked_until": timezone.now() + timedelta(minutes=15),
            },
        )
        resp_locked = self.client.post(
            url, {"action": "regenerate", "code": "000000"}
        )
        self.assertEqual(resp_locked.status_code, 403)

    def test_reenroll_start_post_error_rerenders_in_debug_mode(self):
        url = reverse("accounts:two_factor_setup")
        self.client.force_login(self.user)

        # 誤ったコード
        resp_bad_code = self.client.post(
            url, {"action": "reenroll_start", "code": "000000"}
        )
        self.assertEqual(resp_bad_code.status_code, 200)

        # ロック中
        AccountLock.objects.update_or_create(
            user=self.user,
            defaults={
                "failure_count": 5,
                "locked_until": timezone.now() + timedelta(minutes=15),
            },
        )
        resp_locked = self.client.post(
            url, {"action": "reenroll_start", "code": "000000"}
        )
        self.assertEqual(resp_locked.status_code, 403)

    def test_setup_confirm_wrong_code_rerenders_in_debug_mode(self):
        url = reverse("accounts:two_factor_setup")

        # 初回登録時の確認コード誤り
        fresh_user = CustomUser.objects.create_user(
            username="dbg_fresh",
            password="pw",
            email="dbg_fresh@example.com",
        )
        pending_initial = TOTPDevice.objects.create(
            user=fresh_user, name="pending_init", confirmed=False
        )
        self.client.force_login(fresh_user)
        resp_init = self.client.post(
            url,
            {
                "action": "confirm",
                "device_id": str(pending_initial.pk),
                "code": "000000",
            },
        )
        self.assertEqual(resp_init.status_code, 200)

        # 再登録時の確認コード誤り
        pending_reenroll = TOTPDevice.objects.create(
            user=self.user, name="pending_reenroll", confirmed=False
        )
        self.client.force_login(self.user)
        resp_reenroll = self.client.post(
            url,
            {
                "action": "confirm",
                "device_id": str(pending_reenroll.pk),
                "code": "000000",
            },
        )
        self.assertEqual(resp_reenroll.status_code, 200)

    def test_two_factor_reset_post_501_chars_rerenders_in_debug_mode(self):
        url = reverse("accounts:two_factor_reset", kwargs={"user_id": self.target.pk})
        self.client.force_login(self.admin)
        resp = self.client.post(url, {"reason_note": "a" * 501})
        self.assertEqual(resp.status_code, 400)
