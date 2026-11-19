"""ログインセキュリティ Step 2 フェーズ A（基盤）の単体テスト。"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from accounts.constants import ADMIN_ROLE_CODE
from accounts.login_security.checks import check_client_ip_source
from accounts.login_security.client_ip import get_client_ip
from accounts.login_security.user_lookup import (
    get_admin_notification_recipients,
    get_security_setting,
    resolve_login_users,
)
from accounts.models import Role, SecuritySetting
from accounts.services import is_admin_user

User = get_user_model()


class ClientIpTests(SimpleTestCase):
    """接続元 IP 取得関数とシステムチェックの検証（仕様書 §15）。"""

    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(CLIENT_IP_SOURCE="x_real_ip")
    def test_x_real_ip_reads_only_x_real_ip(self):
        req = self.factory.get(
            "/",
            REMOTE_ADDR="172.18.0.2",
            HTTP_X_REAL_IP="203.0.113.10",
            HTTP_CF_CONNECTING_IP="198.51.100.99",
            HTTP_X_FORWARDED_FOR="192.0.2.50, 10.0.0.1",
        )
        self.assertEqual(get_client_ip(req), "203.0.113.10")

    @override_settings(CLIENT_IP_SOURCE="x_real_ip")
    def test_x_real_ip_ignores_cf_and_xff_when_missing(self):
        req = self.factory.get(
            "/",
            REMOTE_ADDR="172.18.0.2",
            HTTP_CF_CONNECTING_IP="198.51.100.99",
            HTTP_X_FORWARDED_FOR="192.0.2.50",
        )
        self.assertIsNone(get_client_ip(req))

    @override_settings(CLIENT_IP_SOURCE="remote_addr")
    def test_remote_addr_source_uses_remote_addr(self):
        req = self.factory.get(
            "/",
            REMOTE_ADDR="192.168.1.25",
            HTTP_X_REAL_IP="203.0.113.10",
            HTTP_X_FORWARDED_FOR="192.0.2.50",
        )
        self.assertEqual(get_client_ip(req), "192.168.1.25")

    @override_settings(CLIENT_IP_SOURCE="x_real_ip")
    def test_ipv6_and_invalid_values(self):
        req_v6 = self.factory.get("/", HTTP_X_REAL_IP=" 2001:0db8::1 ")
        self.assertEqual(get_client_ip(req_v6), "2001:db8::1")

        for bad in ("", "   ", "not-an-ip", "203.0.113.1, 203.0.113.2"):
            with self.subTest(bad=bad):
                req_bad = self.factory.get("/", HTTP_X_REAL_IP=bad)
                self.assertIsNone(get_client_ip(req_bad))

    @override_settings(DEBUG=False, CLIENT_IP_SOURCE="remote_addr")
    def test_system_check_warns_in_production_with_remote_addr(self):
        warnings = check_client_ip_source(None)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].id, "accounts.W001")

    @override_settings(DEBUG=False, CLIENT_IP_SOURCE="x_real_ip")
    def test_system_check_ok_in_production_with_x_real_ip(self):
        self.assertEqual(check_client_ip_source(None), [])

    @override_settings(DEBUG=True, CLIENT_IP_SOURCE="remote_addr")
    def test_system_check_ok_in_debug_with_remote_addr(self):
        self.assertEqual(check_client_ip_source(None), [])

    @override_settings(DEBUG=True, CLIENT_IP_SOURCE="invalid_source")
    def test_system_check_warns_on_invalid_client_ip_source(self):
        warnings = check_client_ip_source(None)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].id, "accounts.W002")


class UserLookupTests(TestCase):
    """ユーザー特定規則・設定取得・管理者宛先の検証。"""

    def setUp(self):
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.sales_role = Role.objects.get(code="sales")

    def test_resolve_login_users_exact_and_case_insensitive(self):
        u_lower = User.objects.create_user(username="taro", password="pw")
        u_upper = User.objects.create_user(username="TARO", password="pw")

        # 完全一致があればそれを優先
        res_exact = resolve_login_users("  taro  ")
        self.assertEqual(res_exact.target_users, [u_lower])
        self.assertEqual(res_exact.primary_user, u_lower)

        # 完全一致がなく大文字小文字違いが複数あれば全員が対象、primary は ID 最小
        res_mixed = resolve_login_users("Taro")
        self.assertEqual(res_mixed.target_users, [u_lower, u_upper])
        self.assertEqual(res_mixed.primary_user, u_lower)

    def test_resolve_login_users_not_found_and_empty(self):
        self.assertEqual(resolve_login_users("unknown").target_users, [])
        self.assertIsNone(resolve_login_users("unknown").primary_user)
        self.assertEqual(resolve_login_users("   ").target_users, [])
        self.assertIsNone(resolve_login_users(None).primary_user)

    def test_get_security_setting(self):
        setting = get_security_setting()
        self.assertIsInstance(setting, SecuritySetting)
        self.assertEqual(setting.id, 1)

    def test_get_admin_notification_recipients_matches_is_admin_user(self):
        # スーパーユーザーのみ（ロールなし）でも宛先になる
        su = User.objects.create_user(
            username="su",
            password="pw",
            email="su@example.com",
            is_superuser=True,
            is_active=True,
        )
        adm = User.objects.create_user(
            username="adm",
            password="pw",
            email="adm@example.com",
            role=self.admin_role,
            is_active=True,
        )
        # メール空・空白のみは除外
        User.objects.create_user(
            username="adm_no_mail",
            password="pw",
            email="",
            role=self.admin_role,
            is_active=True,
        )
        User.objects.create_user(
            username="su_blank_mail",
            password="pw",
            email="   ",
            is_superuser=True,
            is_active=True,
        )
        # 無効ユーザーは除外
        User.objects.create_user(
            username="adm_inactive",
            password="pw",
            email="inactive@example.com",
            role=self.admin_role,
            is_active=False,
        )
        # スタッフのみ・営業ロールは除外
        User.objects.create_user(
            username="staff_only",
            password="pw",
            email="staff@example.com",
            is_staff=True,
            is_active=True,
        )
        User.objects.create_user(
            username="sales_u",
            password="pw",
            email="sales@example.com",
            role=self.sales_role,
            is_active=True,
        )

        recipients = get_admin_notification_recipients()
        self.assertEqual(recipients, [su, adm])

        # is_admin_user() との定義一致を全ユーザーで確認
        expected = [
            u
            for u in User.objects.order_by("id")
            if is_admin_user(u) and bool(u.email and u.email.strip())
        ]
        self.assertEqual(recipients, expected)
