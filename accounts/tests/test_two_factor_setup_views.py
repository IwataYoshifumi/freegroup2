"""ログインセキュリティ Step 4-2a（認証アプリ初回登録・リカバリーコード表示ビュー）のテスト。"""

from base64 import b32encode
import logging
from pathlib import Path
import re
import time

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ActionLogAction
from accounts.models import AccountLock, CustomUser, RecoveryCode, SecuritySetting
from actionlogs.models import ActionLog


def _totp_code(device: TOTPDevice) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    OTP_TOTP_ISSUER="FreeGroup2TestIssuer",
)
class TwoFactorSetupAndRecoveryCodesViewTests(TestCase):
    """accounts:two_factor_setup と accounts:two_factor_recovery_codes の結合テスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.setup_url = reverse("accounts:two_factor_setup")
        self.recovery_url = reverse("accounts:two_factor_recovery_codes")
        self.login_url = reverse("accounts:login")
        self.home_url = reverse("home")

        self.user = CustomUser.objects.create_user(
            username="setup_user",
            password="pw",
            email="setup_user@example.com",
        )
        self.staff_user = CustomUser.objects.create_user(
            username="setup_staff",
            password="pw",
            email="setup_staff@example.com",
            is_staff=True,
        )

    def test_anonymous_users_redirected_to_login_url(self):
        for url_name, url in (
            ("two_factor_setup", self.setup_url),
            ("two_factor_recovery_codes", self.recovery_url),
        ):
            with self.subTest(url_name=url_name):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertIn(self.login_url, resp.url)

    def test_staff_without_role_or_groups_can_open_setup_get_without_secret_or_qr(self):
        self.assertIsNone(self.staff_user.role)
        self.assertEqual(self.staff_user.groups.count(), 0)
        # 過去に未確定の登録が残っていても GET では秘密鍵・QR を再表示しない
        unconfirmed = TOTPDevice.objects.create(
            user=self.staff_user, name="stale", confirmed=False
        )
        stale_secret = b32encode(unconfirmed.bin_key).decode("ascii")

        self.client.force_login(self.staff_user)
        resp = self.client.get(self.setup_url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("登録を開始", html)
        self.assertNotIn("<svg", html)
        self.assertNotIn(stale_secret, html)

    def test_start_post_creates_unconfirmed_device_with_inline_qr_issuer_and_no_external_urls(
        self,
    ):
        self.client.force_login(self.user)
        resp = self.client.post(self.setup_url, {"action": "start"})
        self.assertEqual(resp.status_code, 200)

        device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        self.assertIn("FreeGroup2TestIssuer", device.config_url)
        secret_b32 = b32encode(device.bin_key).decode("ascii")

        html = resp.content.decode("utf-8")
        self.assertIn("<svg", html)
        self.assertIn(secret_b32, html)
        self.assertIn('inputmode="numeric"', html)
        self.assertIn('autocomplete="one-time-code"', html)
        html_without_svg_ns = html.replace('xmlns="http://www.w3.org/2000/svg"', "")
        self.assertNotIn("http://", html_without_svg_ns)
        self.assertNotIn("https://", html_without_svg_ns)
        # 外部スクリプトが埋め込まれていないこと（base.html の app.js 静的参照のみ）
        script_srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
        self.assertTrue(all(src.startswith("/static/") for src in script_srcs))

    def test_valid_confirm_code_confirms_device_renders_recovery_codes_without_leaks(
        self,
    ):
        self.client.force_login(self.user)
        resp_start = self.client.post(self.setup_url, {"action": "start"})
        self.assertEqual(resp_start.status_code, 200)

        device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        secret_b32 = b32encode(device.bin_key).decode("ascii")
        valid_code = _totp_code(device)

        log_stream = []

        class _ListHandler(logging.Handler):
            def emit(self, record):
                log_stream.append(self.format(record))

        handler = _ListHandler()
        root_logger = logging.getLogger()
        old_level = root_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                resp_confirm = self.client.post(
                    self.setup_url,
                    {
                        "action": "confirm",
                        "device_id": str(device.pk),
                        "code": valid_code,
                    },
                )
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(old_level)

        # リダイレクトせず直接描画する
        self.assertEqual(resp_confirm.status_code, 200)
        html = resp_confirm.content.decode("utf-8")

        device.refresh_from_db()
        self.assertTrue(device.confirmed)

        # リカバリーコード 10 個が表示される
        displayed_codes = re.findall(r"\b[2-9A-HJ-NP-Z]{5}-[2-9A-HJ-NP-Z]{5}\b", html)
        self.assertEqual(len(displayed_codes), 10)
        self.assertEqual(RecoveryCode.objects.filter(user=self.user).count(), 10)

        # リカバリーコードの平文はセッション・DB に保持されない
        session_dump = repr(dict(self.client.session))
        for rc in displayed_codes:
            self.assertNotIn(rc, session_dump)
            self.assertNotIn(rc.replace("-", ""), session_dump)
            self.assertFalse(
                RecoveryCode.objects.filter(user=self.user, code_hash=rc).exists()
            )
            self.assertFalse(
                RecoveryCode.objects.filter(
                    user=self.user, code_hash=rc.replace("-", "")
                ).exists()
            )

        # 「保存しました」のチェック（required）と「完了」ボタンがある
        self.assertIn('name="saved"', html)
        self.assertIn("required", html)
        self.assertIn(f'action="{self.recovery_url}"', html)

        # 本人宛の有効化メールが 1 通だけ送られ、秘密鍵・リカバリーコードを含まない
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["setup_user@example.com"])
        self.assertIn("有効化", sent.subject)
        self.assertNotIn(secret_b32, sent.body)
        for rc in displayed_codes:
            self.assertNotIn(rc, sent.body)

        # 操作ログ・アプリログが記録され、秘密鍵・リカバリーコードを含まない
        log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_ENABLE)
        self.assertEqual(log.user, self.user)
        log_dump = f"{log.object_repr} {log.note} {log.data}"
        all_logs = "\n".join(log_stream)
        self.assertNotIn(secret_b32, log_dump)
        self.assertNotIn(secret_b32, all_logs)
        for rc in displayed_codes:
            self.assertNotIn(rc, log_dump)
            self.assertNotIn(rc, all_logs)

    def test_invalid_confirm_code_keeps_same_device_secret_qr_and_does_not_count_failure(
        self,
    ):
        self.client.force_login(self.user)
        resp_start = self.client.post(self.setup_url, {"action": "start"})
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        secret_b32 = b32encode(device.bin_key).decode("ascii")
        start_svg_match = re.search(r"<svg[^>]*>.*?</svg>", resp_start.content.decode("utf-8"), re.DOTALL)
        self.assertIsNotNone(start_svg_match)

        wrong_code = "000000" if _totp_code(device) != "000000" else "999999"
        resp_err = self.client.post(
            self.setup_url,
            {
                "action": "confirm",
                "device_id": str(device.pk),
                "code": wrong_code,
            },
        )
        self.assertEqual(resp_err.status_code, 200)
        err_html = resp_err.content.decode("utf-8")

        device.refresh_from_db()
        self.assertFalse(device.confirmed)
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)
        self.assertIn(secret_b32, err_html)
        self.assertIn(start_svg_match.group(0), err_html)
        self.assertIn(
            "コードが正しくありません。認証アプリに表示された最新の 6 桁コードを入力してください。",
            err_html,
        )
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())

    def test_already_enrolled_user_sees_enrolled_message_only_and_start_post_is_rejected(
        self,
    ):
        confirmed = TOTPDevice.objects.create(
            user=self.user, name="default", confirmed=True
        )
        secret_b32 = b32encode(confirmed.bin_key).decode("ascii")

        self.client.force_login(self.user)
        resp_get = self.client.get(self.setup_url)
        self.assertEqual(resp_get.status_code, 200)
        get_html = resp_get.content.decode("utf-8")
        self.assertIn("再登録を開始", get_html)
        self.assertIn('name="code"', get_html)
        self.assertNotIn("<svg", get_html)
        self.assertNotIn(secret_b32, get_html)

        resp_post = self.client.post(self.setup_url, {"action": "start"})
        self.assertEqual(resp_post.status_code, 400)
        post_html = resp_post.content.decode("utf-8")
        self.assertIn("二段階認証はすでに登録済みです", post_html)
        self.assertNotIn("<svg", post_html)
        self.assertNotIn(secret_b32, post_html)
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)
        self.assertTrue(
            TOTPDevice.objects.filter(pk=confirmed.pk, confirmed=True).exists()
        )

    def test_recovery_codes_get_shows_explanation_only_and_post_redirects_home(self):
        self.client.force_login(self.user)
        resp_get = self.client.get(self.recovery_url)
        self.assertEqual(resp_get.status_code, 200)
        get_html = resp_get.content.decode("utf-8")
        self.assertIn("再表示することはできません", get_html)
        self.assertEqual(
            re.findall(r"\b[2-9A-HJ-NP-Z]{5}-[2-9A-HJ-NP-Z]{5}\b", get_html),
            [],
        )

        resp_post = self.client.post(self.recovery_url, {"saved": "on"})
        self.assertRedirects(resp_post, self.home_url)

    def test_cache_control_no_store_header_on_both_views(self):
        self.client.force_login(self.user)

        for label, resp in (
            ("setup_get", self.client.get(self.setup_url)),
            ("setup_start", self.client.post(self.setup_url, {"action": "start"})),
            ("recovery_get", self.client.get(self.recovery_url)),
        ):
            with self.subTest(label=label):
                cache_control = resp.headers.get("Cache-Control", "")
                self.assertIn("no-store", cache_control)

    def test_mandatory_user_guided_to_setup_can_access_home_after_completion(self):
        setting = SecuritySetting.objects.get(id=1)
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        setting.save()

        self.client.force_login(self.user)

        # 未登録時は強制誘導される
        resp_blocked = self.client.get(self.home_url)
        self.assertRedirects(resp_blocked, self.setup_url, fetch_redirect_response=False)

        # 登録開始 → 正しいコードで確定
        self.client.post(self.setup_url, {"action": "start"})
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        with self.captureOnCommitCallbacks(execute=True):
            resp_confirm = self.client.post(
                self.setup_url,
                {
                    "action": "confirm",
                    "device_id": str(device.pk),
                    "code": _totp_code(device),
                },
            )
        self.assertEqual(resp_confirm.status_code, 200)

        # 完了後はホームへ入れる
        resp_done = self.client.post(self.recovery_url, {"saved": "on"}, follow=True)
        self.assertEqual(resp_done.status_code, 200)
        self.assertRedirects(resp_done, self.home_url)

    def test_inline_styles_only_in_three_allowed_places(self):
        base_dir = Path(settings.BASE_DIR) / "templates" / "accounts"
        setup_src = (base_dir / "two_factor_setup.html").read_text(encoding="utf-8")
        recovery_src = (base_dir / "two_factor_recovery_codes.html").read_text(
            encoding="utf-8"
        )

        setup_styles = re.findall(r'style="([^"]*)"', setup_src)
        recovery_styles = re.findall(r'style="([^"]*)"', recovery_src)

        # setup: QR サイズ・確認コード入力欄・再登録開始の現在コード入力欄 の 3 か所のみ
        self.assertEqual(len(setup_styles), 3)
        self.assertIn("width:", setup_styles[0])
        self.assertIn("monospace", setup_styles[1])
        self.assertIn("monospace", setup_styles[2])

        # recovery: リカバリーコード一覧表示・再発行の現在コード入力欄 の 2 か所のみ
        self.assertEqual(len(recovery_styles), 2)
        self.assertIn("monospace", recovery_styles[0])
        self.assertIn("monospace", recovery_styles[1])

    def test_confirm_post_with_other_users_unconfirmed_device_id_rejected(self):
        other_device = TOTPDevice.objects.create(
            user=self.staff_user, name="other_unconfirmed", confirmed=False
        )
        other_secret_b32 = b32encode(other_device.bin_key).decode("ascii")

        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                self.setup_url,
                {
                    "action": "confirm",
                    "device_id": str(other_device.pk),
                    "code": _totp_code(other_device),
                },
            )

        self.assertEqual(resp.status_code, 400)
        html = resp.content.decode("utf-8")
        self.assertIn("登録情報が見つかりません", html)
        self.assertIn("登録を開始", html)
        self.assertNotIn("<svg", html)
        self.assertNotIn(other_secret_b32, html)

        other_device.refresh_from_db()
        self.assertFalse(other_device.confirmed)
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 0)
        self.assertEqual(RecoveryCode.objects.count(), 0)
        self.assertEqual(ActionLog.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_post_with_own_confirmed_device_id_returns_400_without_secret_or_qr(
        self,
    ):
        confirmed_device = TOTPDevice.objects.create(
            user=self.user, name="confirmed", confirmed=True
        )
        confirmed_secret_b32 = b32encode(confirmed_device.bin_key).decode("ascii")

        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                self.setup_url,
                {
                    "action": "confirm",
                    "device_id": str(confirmed_device.pk),
                    "code": _totp_code(confirmed_device),
                },
            )

        self.assertEqual(resp.status_code, 400)
        html = resp.content.decode("utf-8")
        self.assertIn("登録情報が見つかりません", html)
        self.assertNotIn("<svg", html)
        self.assertNotIn(confirmed_secret_b32, html)

        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)
        self.assertEqual(RecoveryCode.objects.filter(user=self.user).count(), 0)
        self.assertEqual(ActionLog.objects.count(), 0)

    def test_confirm_post_with_nonexistent_or_non_numeric_device_id_returns_400_not_500(
        self,
    ):
        self.client.force_login(self.user)
        for bad_id in ("999999", "-1", "abc", "", "1.5"):
            with self.subTest(device_id=bad_id):
                resp = self.client.post(
                    self.setup_url,
                    {
                        "action": "confirm",
                        "device_id": bad_id,
                        "code": "123456",
                    },
                )
                self.assertEqual(resp.status_code, 400)
                html = resp.content.decode("utf-8")
                self.assertIn("登録情報が見つかりません", html)
                self.assertIn("登録を開始", html)
                self.assertNotIn("<svg", html)

        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 0)
        self.assertEqual(RecoveryCode.objects.count(), 0)
        self.assertEqual(ActionLog.objects.count(), 0)
