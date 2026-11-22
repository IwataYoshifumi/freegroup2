"""パスワード変更・リセット Step 5-A（リセット 4 画面・ログイン導線・暫定処理削除・結合検証）のテスト。"""

from datetime import datetime, timedelta
import re
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE, ActionLogAction, AuthSource
from accounts.login_security.devices import (
    finalize_login_device,
    grant_device_trust,
    match_login_device,
)
from accounts.login_security.recovery_codes import issue_recovery_codes
from accounts.models import (
    AccountLock,
    CustomUser,
    LoginAttempt,
    PasswordResetRequest,
    RecoveryCode,
    Role,
    SecuritySetting,
)
from accounts.password_security.password_reset import (
    INVALID_RESET_LINK_MESSAGE,
    build_password_reset_url,
)
from actionlogs.models import ActionLog

User = get_user_model()

VALID_NEW_PASSWORD = "Step5Strong#Password902"


def _create_user_session(user) -> str:
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store.set_expiry(timedelta(days=7))
    store.save()
    return store.session_key


def _extract_reset_url(mail_body: str) -> str:
    match = re.search(r"https?://[^\s]+/accounts/password/reset/[^\s]+/", mail_body)
    assert match is not None, f"リセット URL がメール本文に見つかりません: {mail_body}"
    return match.group(0)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    CLIENT_IP_SOURCE="x_real_ip",
    ALLOWED_HOSTS=["testserver", "reset.example.local"],
    PASSWORD_RESET_TIMEOUT=3600,
)
class PasswordResetViewsIntegrationTests(TestCase):
    """パスワードリセット 4 画面・ログイン導線・結合フローのテスト（§14 リセット系）。"""

    def setUp(self):
        mail.outbox.clear()
        self.login_url = reverse("accounts:login")
        self.reset_url = reverse("accounts:password_reset")
        self.done_url = reverse("accounts:password_reset_done")
        self.complete_url = reverse("accounts:password_reset_complete")

        self.setting, _ = SecuritySetting.objects.get_or_create(id=1)
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.OPTIONAL
        self.setting.ip_block_threshold = 3
        self.setting.save()

        self.user = User.objects.create_user(
            username="step5_u1",
            email="Step5_U1@example.com",
            password="Old#Password123",
            first_name="三郎",
            last_name="伊藤",
            auth_source=AuthSource.LOCAL,
            ldap_dn=None,
        )
        self.other_user = User.objects.create_user(
            username="step5_other",
            email="step5_other@example.com",
            password="Other#Password123",
        )

    def test_login_page_has_forgot_password_link_and_four_pages_open_anonymously(self):
        login_resp = self.client.get(self.login_url)
        self.assertEqual(login_resp.status_code, 200)
        self.assertContains(login_resp, "パスワードを忘れた場合")
        self.assertContains(login_resp, f'href="{self.reset_url}"')

        dummy_confirm_url = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": "invalid", "token": "invalid-tok"},
        )
        for url in (self.reset_url, self.done_url, dummy_confirm_url, self.complete_url):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertNotIn(self.login_url, getattr(resp, "url", ""))

    def test_build_password_reset_url_uses_reverse_and_request_host_without_fallback(self):
        req = RequestFactory().get(
            "/accounts/password/reset/",
            HTTP_HOST="reset.example.local",
        )
        token = default_token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        expected_path = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": uidb64, "token": token},
        )
        generated = build_password_reset_url(req, self.user, token=token)
        self.assertEqual(generated, f"http://reset.example.local{expected_path}")

    def test_request_screen_returns_identical_response_across_all_outcomes_and_mail_failure(self):
        # 対象外・IP遮断を準備
        User.objects.create_user(
            username="step5_ldap",
            email="step5_ldap@example.com",
            password="Old#Password123",
            auth_source=AuthSource.LDAP,
        )
        blocked_ip = "198.51.100.200"
        for _ in range(3):
            LoginAttempt.objects.create(
                username_input="x",
                ip_address=blocked_ip,
                result=LoginAttempt.Result.PASSWORD_FAILED,
            )

        # (1) 該当あり（送信 1通）
        with self.captureOnCommitCallbacks(execute=True):
            r_sent = self.client.post(
                self.reset_url,
                {"email": "  step5_u1@example.com  "},
                HTTP_HOST="reset.example.local",
                HTTP_X_REAL_IP="198.51.100.10",
            )
        self.assertEqual(len(mail.outbox), 1)
        mail.outbox.clear()

        # (2) 該当なし・空入力・対象外・IP遮断・同一IP制限超過（6件目）→ メールなし
        r_no_match = self.client.post(
            self.reset_url,
            {"email": "nobody@example.com"},
            HTTP_X_REAL_IP="198.51.100.11",
        )
        r_empty = self.client.post(
            self.reset_url,
            {"email": "   "},
            HTTP_X_REAL_IP="198.51.100.12",
        )
        r_ldap = self.client.post(
            self.reset_url,
            {"email": "step5_ldap@example.com"},
            HTTP_X_REAL_IP="198.51.100.13",
        )
        r_ip_blocked = self.client.post(
            self.reset_url,
            {"email": "step5_u1@example.com"},
            HTTP_X_REAL_IP=blocked_ip,
        )
        for _ in range(5):
            self.client.post(
                self.reset_url,
                {"email": "missing@example.com"},
                HTTP_X_REAL_IP="198.51.100.14",
            )
        r_rate_limited = self.client.post(
            self.reset_url,
            {"email": "step5_u1@example.com"},
            HTTP_X_REAL_IP="198.51.100.14",
        )
        self.assertEqual(len(mail.outbox), 0)

        # (3) 該当ありでメール送信失敗しても同じ受付画面へ遷移し例外にならない
        with patch("common.mail.get_connection", side_effect=OSError("smtp down")):
            with self.assertLogs("common.mail", level="ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    r_mail_err = self.client.post(
                        self.reset_url,
                        {"email": "step5_u1@example.com"},
                        HTTP_X_REAL_IP="198.51.100.15",
                    )

        responses = [r_sent, r_no_match, r_empty, r_ldap, r_ip_blocked, r_rate_limited, r_mail_err]
        bodies = []
        for resp in responses:
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.url, self.done_url)
            done_page = self.client.get(resp.url)
            self.assertEqual(done_page.status_code, 200)
            self.assertContains(
                done_page,
                "入力されたメールアドレスに該当するアカウントがある場合、パスワード再設定の案内を送信しました。",
            )
            bodies.append(done_page.content)

        # HTML 内容が完全に同一であること
        for body in bodies[1:]:
            self.assertEqual(body, bodies[0])

    def test_confirm_screen_redirects_token_to_session_sets_security_headers_validates_and_completes(self):
        admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        User.objects.create_user(
            username="step5_adm1",
            email="step5_adm1@example.com",
            password="pw",
            role=admin_role,
        )
        User.objects.create_user(
            username="step5_adm2_su",
            email="step5_adm2_su@example.com",
            password="pw",
            is_superuser=True,
        )

        # 既存セッション・他ユーザーセッション・信頼端末・アカウントロックを準備
        u_sess1 = _create_user_session(self.user)
        u_sess2 = _create_user_session(self.user)
        other_sess = _create_user_session(self.other_user)
        fin = finalize_login_device(self.user, match_login_device(self.user))
        grant_device_trust(fin.device, days=30)
        AccountLock.objects.create(
            user=self.user,
            failure_count=2,
            last_failed_at=timezone.now(),
            locked_until=timezone.now() + timedelta(minutes=10),
        )

        # 申請してリンク取得
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.reset_url,
                {"email": "Step5_U1@example.com"},
                HTTP_HOST="reset.example.local",
                HTTP_X_REAL_IP="198.51.100.30",
            )
        self.assertEqual(len(mail.outbox), 1)
        reset_link = _extract_reset_url(mail.outbox[0].body)
        mail.outbox.clear()

        # トークン付き URL を GET → トークンをセッションへ移し、トークンなし (set-password) URL へリダイレクト
        path_only = "/" + reset_link.split("reset.example.local/")[1]
        r_init = self.client.get(path_only)
        self.assertEqual(r_init.status_code, 302)
        self.assertIn("/set-password/", r_init.url)
        self.assertEqual(r_init.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("no-store", r_init.headers.get("Cache-Control", ""))
        self.assertIn(INTERNAL_RESET_SESSION_TOKEN, self.client.session)

        set_pw_url = r_init.url
        r_form = self.client.get(set_pw_url)
        self.assertEqual(r_form.status_code, 200)
        self.assertEqual(r_form.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("no-store", r_form.headers.get("Cache-Control", ""))

        # 不一致・強度不足・現在と同じパスワードは拒否され、入力値は画面に戻らない
        for bad1, bad2 in (
            (VALID_NEW_PASSWORD, "Different#Pass123"),
            ("123", "123"),
            ("Old#Password123", "Old#Password123"),
        ):
            r_err = self.client.post(
                set_pw_url,
                {"new_password1": bad1, "new_password2": bad2},
            )
            self.assertEqual(r_err.status_code, 200)
            self.assertEqual(r_err.headers.get("Referrer-Policy"), "no-referrer")
            self.assertIn("no-store", r_err.headers.get("Cache-Control", ""))
            self.assertNotContains(r_err, bad1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Old#Password123"))

        # 正常入力で完了 → 完了画面へ遷移・自動ログインなし
        with self.captureOnCommitCallbacks(execute=True):
            r_ok = self.client.post(
                set_pw_url,
                {
                    "new_password1": VALID_NEW_PASSWORD,
                    "new_password2": VALID_NEW_PASSWORD,
                },
                HTTP_X_REAL_IP="198.51.100.31",
                HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0.0.0",
            )
        self.assertEqual(r_ok.status_code, 302)
        self.assertEqual(r_ok.url, self.complete_url)
        self.assertNotIn(SESSION_KEY, self.client.session)

        r_comp = self.client.get(self.complete_url)
        self.assertEqual(r_comp.status_code, 200)
        self.assertContains(r_comp, "パスワードを再設定しました。ログイン画面からログインしてください。")
        self.assertContains(r_comp, "ログインできない場合は、しばらく待ってから再度お試しください。")
        self.assertContains(r_comp, f'href="{self.login_url}"')

        # 全セッション切断・他ユーザー維持・端末信頼取消・ロック/失敗回数維持・パスワード変更
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(VALID_NEW_PASSWORD))
        self.assertEqual(self.user.first_name, "三郎")
        self.assertFalse(Session.objects.filter(session_key__in=[u_sess1, u_sess2]).exists())
        self.assertTrue(Session.objects.filter(session_key=other_sess).exists())
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 2)
        self.assertIsNotNone(lock.locked_until)

        # 二段階認証未登録ユーザーのため 本人宛 + 管理者全員 BCC 1通
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ["Step5_U1@example.com"])
        self.assertEqual(mail.outbox[1].to, [])
        self.assertCountEqual(mail.outbox[1].bcc, ["step5_adm1@example.com", "step5_adm2_su@example.com"])
        self.assertIn("【要確認】", mail.outbox[1].subject)

        # 使用済みリンクの再利用は不可（案内と申請リンクのみ、フォームなし）
        r_reuse = self.client.get(path_only)
        self.assertEqual(r_reuse.status_code, 200)
        self.assertContains(r_reuse, INVALID_RESET_LINK_MESSAGE)
        self.assertContains(r_reuse, f'href="{self.reset_url}"')
        self.assertNotContains(r_reuse, 'name="new_password1"')

        # ログ（操作ログ・申請記録）に新パスワード・トークン・リンクが出ない
        raw_token = path_only.rstrip("/").split("/")[-1]
        for log in ActionLog.objects.all():
            dump = f"{log.object_repr}{log.data}{log.note}"
            self.assertNotIn(VALID_NEW_PASSWORD, dump)
            self.assertNotIn(raw_token, dump)
            self.assertNotIn(reset_link, dump)
        for req_rec in PasswordResetRequest.objects.all():
            self.assertNotIn(VALID_NEW_PASSWORD, req_rec.email_input)
            self.assertNotIn(raw_token, req_rec.email_input)

    def test_invalid_expired_logged_in_and_ineligible_or_inactive_links_are_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        issued_at = datetime(2026, 11, 22, 12, 0, 0)
        with patch.object(default_token_generator, "_now", return_value=issued_at):
            tok = default_token_generator.make_token(self.user)

        confirm_url = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": uid, "token": tok},
        )
        # (1) 有効期限切れ
        with patch.object(
            default_token_generator,
            "_now",
            return_value=issued_at + timedelta(seconds=3601),
        ):
            r_exp = self.client.get(confirm_url)
            self.assertEqual(r_exp.status_code, 200)
            self.assertEqual(r_exp.headers.get("Referrer-Policy"), "no-referrer")
            self.assertContains(r_exp, INVALID_RESET_LINK_MESSAGE)
            self.assertNotContains(r_exp, 'name="new_password1"')

        # (2) リンク発行後にログイン成立すると無効化
        u2 = User.objects.create_user(
            username="step5_login_inv",
            email="step5_login_inv@example.com",
            password="Old#Password123",
        )
        uid2 = urlsafe_base64_encode(force_bytes(u2.pk))
        tok2 = default_token_generator.make_token(u2)
        confirm_url2 = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": uid2, "token": tok2},
        )
        self.assertTrue(self.client.login(username="step5_login_inv", password="Old#Password123"))
        self.client.logout()
        r_after_login = self.client.get(confirm_url2)
        self.assertEqual(r_after_login.status_code, 200)
        self.assertContains(r_after_login, INVALID_RESET_LINK_MESSAGE)
        self.assertNotContains(r_after_login, 'name="new_password1"')

        # (3) 対象外・無効化変化後（GET してもフォームが出ず、パスワードも変わらない）
        for patch_fn in (
            lambda u: setattr(u, "is_active", False),
            lambda u: setattr(u, "auth_source", AuthSource.LDAP),
            lambda u: setattr(u, "ldap_dn", "CN=changed,DC=example,DC=local"),
            lambda u: u.set_unusable_password(),
        ):
            tmp = User.objects.create_user(
                username=f"s5_rt_{User.objects.count()}",
                email="s5_rt@example.com",
                password="Old#Password123",
            )
            t_uid = urlsafe_base64_encode(force_bytes(tmp.pk))
            t_tok = default_token_generator.make_token(tmp)
            t_url = reverse(
                "accounts:password_reset_confirm",
                kwargs={"uidb64": t_uid, "token": t_tok},
            )
            patch_fn(tmp)
            tmp.save()
            r_rt = self.client.get(t_url)
            self.assertEqual(r_rt.status_code, 200)
            self.assertContains(r_rt, INVALID_RESET_LINK_MESSAGE)
            self.assertNotContains(r_rt, 'name="new_password1"')

    def test_post_reset_login_respects_two_factor_enrolled_and_required_enforcement(self):
        admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        User.objects.create_user(
            username="step5_adm",
            email="step5_adm@example.com",
            password="pw",
            role=admin_role,
        )

        # (A) 二段階認証登録済みユーザー → リセットでは管理者通知せず、2FA・リカバリーコード維持、
        #     リセット後のログインでは第二要素入力画面へ誘導される
        totp = TOTPDevice.objects.create(user=self.other_user, name="default", confirmed=True)
        codes = issue_recovery_codes(self.other_user)
        uid_a = urlsafe_base64_encode(force_bytes(self.other_user.pk))
        tok_a = default_token_generator.make_token(self.other_user)

        r_a_get = self.client.get(
            reverse("accounts:password_reset_confirm", kwargs={"uidb64": uid_a, "token": tok_a}),
            follow=True,
        )
        self.assertEqual(r_a_get.status_code, 200)
        with self.captureOnCommitCallbacks(execute=True):
            r_a_post = self.client.post(
                reverse(
                    "accounts:password_reset_confirm",
                    kwargs={"uidb64": uid_a, "token": "set-password"},
                ),
                {"new_password1": VALID_NEW_PASSWORD, "new_password2": VALID_NEW_PASSWORD},
            )
        self.assertRedirects(r_a_post, self.complete_url)
        # 登録済みのため管理者通知は出ない（本人宛 1通のみ）
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["step5_other@example.com"])
        self.assertTrue(TOTPDevice.objects.filter(pk=totp.pk, confirmed=True).exists())
        self.assertEqual(RecoveryCode.objects.filter(user=self.other_user).count(), len(codes))

        # 新パスワードでログインすると、二段階認証入力画面へ遷移する（パスワードだけではログイン完了しない）
        r_login_2fa = self.client.post(
            self.login_url,
            {"username": "step5_other", "password": VALID_NEW_PASSWORD},
            HTTP_X_REAL_IP="198.51.100.40",
        )
        self.assertRedirects(r_login_2fa, reverse("accounts:login_verify"))
        self.assertNotIn(SESSION_KEY, self.client.session)

        # (B) 必須対象で未登録のユーザー → リセット後、新パスワードでログインすると登録画面へ誘導される
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()
        uid_b = urlsafe_base64_encode(force_bytes(self.user.pk))
        tok_b = default_token_generator.make_token(self.user)
        self.client.get(
            reverse("accounts:password_reset_confirm", kwargs={"uidb64": uid_b, "token": tok_b})
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse(
                    "accounts:password_reset_confirm",
                    kwargs={"uidb64": uid_b, "token": "set-password"},
                ),
                {"new_password1": VALID_NEW_PASSWORD, "new_password2": VALID_NEW_PASSWORD},
            )
        r_login_enforced = self.client.post(
            self.login_url,
            {"username": "step5_u1", "password": VALID_NEW_PASSWORD},
            HTTP_X_REAL_IP="198.51.100.41",
            follow=True,
        )
        self.assertRedirects(r_login_enforced, reverse("accounts:two_factor_setup"))
