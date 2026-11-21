"""ログインセキュリティ Step 3 フェーズ D（強制誘導ミドルウェア・仮置きページ）のテスト。"""

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE
from accounts.models import CustomUser, Role, SecuritySetting


class TwoFactorEnforcementMiddlewareTests(TestCase):
    """TwoFactorEnforcementMiddleware と仮置きページのテスト。"""

    def setUp(self):
        self.setup_url = reverse("accounts:two_factor_setup")
        self.recovery_url = reverse("accounts:two_factor_recovery_codes")
        self.home_url = reverse("home")
        self.profile_url = reverse("accounts:profile")
        self.login_url = reverse("accounts:login")

        self.setting = SecuritySetting.objects.get(id=1)
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.OPTIONAL
        self.setting.save()

        self.admin_role, _ = Role.objects.get_or_create(
            code=ADMIN_ROLE_CODE,
            defaults={"name": "管理者"},
        )
        self.general_user = CustomUser.objects.create_user(
            username="general", password="pw", email="general@example.com"
        )
        self.role_admin_user = CustomUser.objects.create_user(
            username="role_admin",
            password="pw",
            email="role_admin@example.com",
            role=self.admin_role,
        )
        self.staff_user = CustomUser.objects.create_user(
            username="staff_only",
            password="pw",
            email="staff@example.com",
            is_staff=True,
        )
        self.super_user = CustomUser.objects.create_superuser(
            username="super_u",
            password="pw",
            email="super@example.com",
        )

    def test_middleware_is_placed_right_after_message_middleware(self):
        mw = list(settings.MIDDLEWARE)
        msg_idx = mw.index("django.contrib.messages.middleware.MessageMiddleware")
        self.assertEqual(
            mw[msg_idx + 1],
            "accounts.middleware.TwoFactorEnforcementMiddleware",
        )

    def test_optional_enforcement_allows_unregistered_user_everywhere(self):
        self.client.force_login(self.general_user)
        with patch_totp_check_counter() as calls:
            resp_home = self.client.get(self.home_url)
            resp_profile = self.client.get(self.profile_url)
        self.assertEqual(resp_home.status_code, 200)
        self.assertEqual(resp_profile.status_code, 200)
        self.assertEqual(len(calls), 0)

    def test_admin_only_redirects_admin_targets_on_get_post_ajax_and_allows_general(self):
        self.setting.two_factor_enforcement = (
            SecuritySetting.TwoFactorEnforcement.ADMIN_ONLY
        )
        self.setting.save()

        # 管理者ロール・スーパーユーザー・スタッフ権限の未登録者はすべて誘導される
        for username in ("role_admin", "super_u", "staff_only"):
            user = CustomUser.objects.get(username=username)
            self.client.force_login(user)

            with self.subTest(username=username, method="GET"):
                resp_get = self.client.get(self.profile_url)
                self.assertRedirects(
                    resp_get, self.setup_url, fetch_redirect_response=False
                )

            with self.subTest(username=username, method="POST"):
                resp_post = self.client.post(self.profile_url, {})
                self.assertRedirects(
                    resp_post, self.setup_url, fetch_redirect_response=False
                )

            with self.subTest(username=username, method="AJAX"):
                resp_ajax = self.client.get(
                    self.profile_url,
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )
                self.assertRedirects(
                    resp_ajax, self.setup_url, fetch_redirect_response=False
                )

        # 一般ユーザーは誘導されない
        self.client.force_login(self.general_user)
        resp_gen = self.client.get(self.home_url)
        self.assertEqual(resp_gen.status_code, 200)

    def test_all_enforcement_redirects_unregistered_and_unconfirmed_only(self):
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()

        # 1. 未登録 → 誘導される
        self.client.force_login(self.general_user)
        resp_unreg = self.client.get(self.home_url)
        self.assertRedirects(resp_unreg, self.setup_url, fetch_redirect_response=False)

        # 2. 未確定 TOTP のみ → 誘導される（§14-23）
        TOTPDevice.objects.create(
            user=self.general_user, name="unconfirmed", confirmed=False
        )
        resp_unconf = self.client.get(self.home_url)
        self.assertRedirects(resp_unconf, self.setup_url, fetch_redirect_response=False)

        # 3. 確定済み TOTP あり → 誘導されない
        TOTPDevice.objects.create(
            user=self.general_user, name="confirmed", confirmed=True
        )
        resp_conf = self.client.get(self.home_url)
        self.assertEqual(resp_conf.status_code, 200)

    def test_admin_site_access_redirects_before_permission_check(self):
        self.setting.two_factor_enforcement = (
            SecuritySetting.TwoFactorEnforcement.ADMIN_ONLY
        )
        self.setting.save()

        # 権限のないスタッフ権限ユーザーでも 403 ではなく setup へ誘導される
        self.client.force_login(self.staff_user)
        resp = self.client.get("/admin/accounts/customuser/")
        self.assertRedirects(resp, self.setup_url, fetch_redirect_response=False)

    def test_five_exempt_destinations_are_not_redirected(self):
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()
        self.client.force_login(self.general_user)

        # 1. accounts:two_factor_setup
        resp_setup = self.client.get(self.setup_url)
        self.assertEqual(resp_setup.status_code, 200)

        # 2. accounts:two_factor_recovery_codes
        resp_rec = self.client.get(self.recovery_url)
        self.assertEqual(resp_rec.status_code, 200)

        # 3. STATIC_URL 配下（誘導 302 にならない）
        static_url = settings.STATIC_URL
        resp_static = self.client.get(f"{static_url}css/app.css")
        self.assertNotEqual(resp_static.status_code, 302)

        # 4. /admin/logout/（POST でログアウト成功し、setup へ誘導されない）
        resp_admin_logout = self.client.post(reverse("admin:logout"))
        self.assertNotEqual(
            getattr(resp_admin_logout, "url", ""),
            self.setup_url,
        )

        # 5. accounts:logout（POST でログアウト成功）
        self.client.force_login(self.general_user)
        resp_logout = self.client.post(reverse("accounts:logout"))
        self.assertEqual(resp_logout.status_code, 302)
        self.assertNotEqual(resp_logout.url, self.setup_url)

    def test_staff_without_role_or_groups_can_open_placeholder_pages_without_loop(self):
        self.setting.two_factor_enforcement = (
            SecuritySetting.TwoFactorEnforcement.ADMIN_ONLY
        )
        self.setting.save()
        self.assertIsNone(self.staff_user.role)
        self.assertEqual(self.staff_user.groups.count(), 0)

        self.client.force_login(self.staff_user)
        resp_setup = self.client.get(self.setup_url)
        self.assertEqual(resp_setup.status_code, 200)
        self.assertContains(resp_setup, "準備中")

        resp_rec = self.client.get(self.recovery_url)
        self.assertEqual(resp_rec.status_code, 200)
        self.assertContains(resp_rec, "準備中")

    def test_enforcement_level_change_applies_to_logged_in_user_on_next_request(self):
        self.client.force_login(self.general_user)
        resp_before = self.client.get(self.home_url)
        self.assertEqual(resp_before.status_code, 200)

        # ログイン中に全員必須へ変更 → 次のリクエストで即誘導
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()

        resp_after = self.client.get(self.home_url)
        self.assertRedirects(resp_after, self.setup_url, fetch_redirect_response=False)

    def test_anonymous_requests_are_not_redirected_by_middleware(self):
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()

        # 未ログインでログイン画面は 200 のまま
        resp_login = self.client.get(self.login_url)
        self.assertEqual(resp_login.status_code, 200)

        # 未ログインで仮置きページは LOGIN_URL へリダイレクトされる
        resp_setup = self.client.get(self.setup_url)
        self.assertEqual(resp_setup.status_code, 302)
        self.assertIn(self.login_url, resp_setup.url)


def patch_totp_check_counter():
    """has_confirmed_totp が呼ばれた回数を記録するコンテキストマネージャ。"""
    from unittest.mock import patch

    calls = []
    from accounts.login_security.totp import has_confirmed_totp as orig

    def spy(user):
        calls.append(user.pk)
        return orig(user)

    class _Ctx:
        def __init__(self):
            self._p = patch("accounts.middleware.has_confirmed_totp", side_effect=spy)

        def __enter__(self):
            self._p.__enter__()
            return calls

        def __exit__(self, *args):
            return self._p.__exit__(*args)

    return _Ctx()
