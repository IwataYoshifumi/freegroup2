"""ログインセキュリティ Step 3 フェーズ F（管理画面登録の是正）のテスト。"""

from django.contrib import admin
from django.test import TestCase
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import (
    AccountLock,
    CustomUser,
    LoginAttempt,
    LoginDevice,
    RecoveryCode,
)


class LoginSecurityAdminRegistryTests(TestCase):
    """TOTPDevice 等が管理画面に登録されていないことを確認するテスト（仕様書 §13）。"""

    def test_sensitive_and_security_models_are_not_registered_in_admin(self):
        for model in (
            TOTPDevice,
            RecoveryCode,
            LoginDevice,
            AccountLock,
            LoginAttempt,
        ):
            with self.subTest(model=model.__name__):
                self.assertNotIn(model, admin.site._registry)

    def test_superuser_gets_404_on_totp_device_admin_urls(self):
        superuser = CustomUser.objects.create_superuser(
            username="admin_u",
            password="admin_pw",
            email="admin@example.com",
        )
        device = TOTPDevice.objects.create(
            user=superuser,
            name="default",
            confirmed=True,
        )
        self.client.force_login(superuser)

        for url in (
            "/admin/otp_totp/totpdevice/",
            f"/admin/otp_totp/totpdevice/{device.pk}/change/",
        ):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 404)
