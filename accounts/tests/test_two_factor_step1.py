"""ログインセキュリティ強化 Step 1 の単体テスト。

モデル制約・並び順・削除時挙動・選択値・権限・初期レコード・設定値・
管理者判定関数・LDAP 分岐時の設定読み込みを検証する。
"""

from datetime import timedelta
import os
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.constants import ADMIN_ROLE_CODE, ActionLogAction
from accounts.models import (
    AccountLock,
    CustomUser,
    LoginAttempt,
    LoginDevice,
    RecoveryCode,
    Role,
    SecuritySetting,
)
from accounts.services import apply_role, is_admin_user

User = get_user_model()


def _eval_settings_module(env_overrides, mock_ldap=False):
    """config/settings.py を独立したモジュール名前空間で評価して返す。

    .env ファイルの影響を受けないよう dotenv.load_dotenv を無害化し、
    環境変数と（必要なら）ldap / django_auth_ldap モジュールをモックする。
    """
    settings_path = Path(settings.BASE_DIR) / "config" / "settings.py"
    source = settings_path.read_text(encoding="utf-8")

    clean_env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "SECRET_KEY_FALLBACKS",
            "OTP_TOTP_ISSUER",
            "DEVICE_COOKIE_NAME",
            "CLIENT_IP_SOURCE",
            "AUTH_BACKEND",
        )
    }
    clean_env.update(env_overrides)

    module_patches = {}
    if mock_ldap:
        fake_ldap = types.ModuleType("ldap")
        fake_ldap.SCOPE_SUBTREE = 2
        fake_auth_ldap = types.ModuleType("django_auth_ldap")
        fake_auth_ldap_config = types.ModuleType("django_auth_ldap.config")
        fake_auth_ldap_config.LDAPSearch = MagicMock()
        module_patches = {
            "ldap": fake_ldap,
            "django_auth_ldap": fake_auth_ldap,
            "django_auth_ldap.config": fake_auth_ldap_config,
        }

    ns = {"__file__": str(settings_path), "__name__": "test_isolated_settings"}
    with (
        patch.dict(os.environ, clean_env, clear=True),
        patch.dict(sys.modules, module_patches),
        patch("dotenv.load_dotenv"),
    ):
        exec(compile(source, str(settings_path), "exec"), ns)
    return ns


class TwoFactorModelsTests(TestCase):
    """Step 1 で追加した 5 モデルと定数の検証。"""

    def setUp(self):
        self.user1 = User.objects.create_user(username="u1", password="pw")
        self.user2 = User.objects.create_user(username="u2", password="pw")

    def test_recovery_code_unique_per_user(self):
        RecoveryCode.objects.create(user=self.user1, code_hash="hash_a")
        # 別ユーザーなら同じハッシュでも登録できる
        RecoveryCode.objects.create(user=self.user2, code_hash="hash_a")
        # 同一ユーザーで同じハッシュは不可
        with self.assertRaises(IntegrityError):
            RecoveryCode.objects.create(user=self.user1, code_hash="hash_a")

    def test_login_device_token_hash_unique(self):
        LoginDevice.objects.create(user=self.user1, token_hash="tok_1")
        with self.assertRaises(IntegrityError):
            LoginDevice.objects.create(user=self.user2, token_hash="tok_1")

    def test_account_lock_one_to_one(self):
        AccountLock.objects.create(user=self.user1, failure_count=1)
        with self.assertRaises(IntegrityError):
            AccountLock.objects.create(user=self.user1, failure_count=2)

    def test_security_setting_singleton(self):
        # 初期レコードを一度削除した状態で id=99 を指定して save() しても id=1 になる
        SecuritySetting.objects.all().delete()
        setting = SecuritySetting(id=99, lockout_threshold=7)
        setting.save()
        self.assertEqual(setting.id, 1)
        self.assertEqual(SecuritySetting.objects.count(), 1)

        # DB 制約（CheckConstraint）により id=1 以外は bulk_create でも挿入できない
        with self.assertRaises(IntegrityError):
            SecuritySetting.objects.bulk_create([SecuritySetting(id=2)])

    def test_model_ordering(self):
        self.assertEqual(SecuritySetting._meta.ordering, ["id"])
        self.assertEqual(RecoveryCode._meta.ordering, ["created_at", "id"])
        self.assertEqual(LoginDevice._meta.ordering, ["-last_seen_at"])
        self.assertEqual(AccountLock._meta.ordering, ["user"])
        self.assertEqual(LoginAttempt._meta.ordering, ["-created_at"])

        now = timezone.now()
        rc1 = RecoveryCode.objects.create(user=self.user1, code_hash="h1")
        rc2 = RecoveryCode.objects.create(user=self.user1, code_hash="h2")
        self.assertEqual(list(RecoveryCode.objects.all()), [rc1, rc2])

        d_old = LoginDevice.objects.create(
            user=self.user1,
            token_hash="t_old",
            last_seen_at=now - timedelta(hours=2),
        )
        d_new = LoginDevice.objects.create(
            user=self.user1,
            token_hash="t_new",
            last_seen_at=now,
        )
        self.assertEqual(list(LoginDevice.objects.all()), [d_new, d_old])

        a_old = LoginAttempt.objects.create(
            username_input="u1",
            result=LoginAttempt.Result.PASSWORD_FAILED,
            created_at=now - timedelta(minutes=10),
        )
        a_new = LoginAttempt.objects.create(
            username_input="u1",
            result=LoginAttempt.Result.SUCCESS,
            created_at=now,
        )
        self.assertEqual(list(LoginAttempt.objects.all()), [a_new, a_old])

    def test_user_deletion_cascade_and_set_null(self):
        RecoveryCode.objects.create(user=self.user1, code_hash="rc_del")
        LoginDevice.objects.create(user=self.user1, token_hash="dev_del")
        AccountLock.objects.create(user=self.user1, failure_count=2)
        attempt = LoginAttempt.objects.create(
            user=self.user1,
            username_input="u1",
            result=LoginAttempt.Result.SUCCESS,
        )
        setting = SecuritySetting.objects.get(id=1)
        setting.updated_by = self.user1
        setting.save()

        self.user1.delete()

        self.assertFalse(RecoveryCode.objects.filter(code_hash="rc_del").exists())
        self.assertFalse(LoginDevice.objects.filter(token_hash="dev_del").exists())
        self.assertFalse(AccountLock.objects.filter(failure_count=2).exists())

        attempt.refresh_from_db()
        self.assertIsNone(attempt.user)
        setting.refresh_from_db()
        self.assertIsNone(setting.updated_by)

    def test_choices_and_action_log_constants(self):
        self.assertEqual(
            [c[0] for c in SecuritySetting.TwoFactorEnforcement.choices],
            ["optional", "admin_only", "all"],
        )
        self.assertEqual(
            [c[0] for c in SecuritySetting.LoginNotificationLevel.choices],
            ["every", "new_device_only"],
        )
        self.assertEqual(
            [c[0] for c in LoginAttempt.Result.choices],
            [
                "success",
                "password_failed",
                "code_failed",
                "rejected_locked",
                "rejected_ip_blocked",
            ],
        )
        self.assertEqual(
            [c[0] for c in LoginAttempt.SecondFactor.choices],
            ["totp", "recovery_code", "trusted_device", "none"],
        )

        expected_actions = {
            "SECURITY_SETTING_UPDATE": "security_setting_update",
            "TWO_FACTOR_ENABLE": "two_factor_enable",
            "TWO_FACTOR_DISABLE": "two_factor_disable",
            "TWO_FACTOR_REENROLL": "two_factor_reenroll",
            "RECOVERY_CODES_REGENERATE": "recovery_codes_regenerate",
            "TWO_FACTOR_RESET": "two_factor_reset",
            "ACCOUNT_LOCK": "account_lock",
            "ACCOUNT_UNLOCK": "account_unlock",
            "IP_BLOCK": "ip_block",
            "LOGIN_DEVICE_REVOKE": "login_device_revoke",
        }
        for attr, value in expected_actions.items():
            self.assertEqual(getattr(ActionLogAction, attr), value)


class TwoFactorPermissionsAndSeedTests(TestCase):
    """権限 3 種・グループ付与・SecuritySetting 初期レコードの検証。"""

    def test_customuser_meta_permissions_contains_custom_two(self):
        codenames = [code for code, _ in CustomUser._meta.permissions]
        self.assertIn("manage_security_setting", codenames)
        self.assertIn("reset_two_factor", codenames)
        self.assertNotIn("view_loginattempt", codenames)

    def test_standard_view_loginattempt_permission_exists(self):
        self.assertTrue(
            Permission.objects.filter(
                content_type__app_label="accounts",
                codename="view_loginattempt",
            ).exists()
        )

    def test_user_admin_group_has_three_permissions(self):
        group = Group.objects.get(name="user_admin")
        perms = set(
            group.permissions.values_list("content_type__app_label", "codename")
        )
        self.assertTrue(
            {
                ("accounts", "manage_security_setting"),
                ("accounts", "reset_two_factor"),
                ("accounts", "view_loginattempt"),
            }.issubset(perms)
        )

    def test_admin_role_user_has_three_permissions(self):
        admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        admin_user = User.objects.create_user(username="admin_u", password="pw")
        apply_role(admin_user, admin_role)

        # キャッシュを避けて再取得
        admin_user = User.objects.get(pk=admin_user.pk)
        self.assertTrue(admin_user.has_perm("accounts.manage_security_setting"))
        self.assertTrue(admin_user.has_perm("accounts.reset_two_factor"))
        self.assertTrue(admin_user.has_perm("accounts.view_loginattempt"))

    def test_security_setting_initial_record(self):
        setting = SecuritySetting.objects.get(id=1)
        self.assertEqual(setting.two_factor_enforcement, "optional")
        self.assertEqual(setting.login_notification_level, "every")
        self.assertEqual(setting.lockout_threshold, 5)
        self.assertEqual(setting.lockout_minutes, 15)
        self.assertEqual(setting.ip_block_threshold, 20)
        self.assertEqual(setting.trusted_device_days, 30)
        self.assertTrue(setting.notify_admins_on_account_lock)
        self.assertTrue(setting.notify_admins_on_ip_block)


class TwoFactorSettingsTests(SimpleTestCase):
    """django-otp 設定・既定値・LDAP 分岐の検証。"""

    def test_runtime_settings(self):
        self.assertEqual(settings.OTP_TOTP_THROTTLE_FACTOR, 0)
        self.assertNotIn("django_otp.middleware.OTPMiddleware", settings.MIDDLEWARE)
        self.assertIn("django_otp", settings.INSTALLED_APPS)
        self.assertIn("django_otp.plugins.otp_totp", settings.INSTALLED_APPS)

    def test_default_settings_isolated_local_and_ldap(self):
        for auth_backend, mock_ldap in [("local", False), ("ldap", True)]:
            with self.subTest(auth_backend=auth_backend):
                ns = _eval_settings_module(
                    {"AUTH_BACKEND": auth_backend},
                    mock_ldap=mock_ldap,
                )
                self.assertEqual(ns["OTP_TOTP_THROTTLE_FACTOR"], 0)
                self.assertEqual(ns["OTP_TOTP_ISSUER"], "FreeGroup2")
                self.assertEqual(ns["DEVICE_COOKIE_NAME"], "freegroup2_device_8000")
                self.assertEqual(ns["CLIENT_IP_SOURCE"], "x_real_ip")
                self.assertEqual(ns["SECRET_KEY_FALLBACKS"], [])
                self.assertIn("django_otp", ns["INSTALLED_APPS"])
                self.assertIn("django_otp.plugins.otp_totp", ns["INSTALLED_APPS"])
                self.assertNotIn(
                    "django_otp.middleware.OTPMiddleware", ns["MIDDLEWARE"]
                )


class IsAdminUserTests(TestCase):
    """is_admin_user() の全パターン検証。"""

    def setUp(self):
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.sales_role = Role.objects.get(code="sales")

    def test_superuser_active_and_inactive(self):
        su_active = User.objects.create_user(
            username="su_active", password="pw", is_superuser=True, is_active=True
        )
        su_inactive = User.objects.create_user(
            username="su_inactive", password="pw", is_superuser=True, is_active=False
        )
        self.assertTrue(is_admin_user(su_active))
        self.assertFalse(is_admin_user(su_inactive))

    def test_admin_role_active_and_inactive(self):
        u_active = User.objects.create_user(
            username="adm_active", password="pw", role=self.admin_role, is_active=True
        )
        u_inactive = User.objects.create_user(
            username="adm_inactive",
            password="pw",
            role=self.admin_role,
            is_active=False,
        )
        self.assertTrue(is_admin_user(u_active))
        self.assertFalse(is_admin_user(u_inactive))

    def test_other_role_no_role_anonymous_none(self):
        u_sales = User.objects.create_user(
            username="sales_u", password="pw", role=self.sales_role, is_active=True
        )
        u_none = User.objects.create_user(
            username="norole_u", password="pw", role=None, is_active=True
        )
        self.assertFalse(is_admin_user(u_sales))
        self.assertFalse(is_admin_user(u_none))
        self.assertFalse(is_admin_user(AnonymousUser()))
        self.assertFalse(is_admin_user(None))
