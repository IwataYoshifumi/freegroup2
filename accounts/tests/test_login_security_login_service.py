"""ログインセキュリティ Step 3 フェーズ B（login_service.py）の単体テスト。"""

from datetime import timedelta
import time
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.login_security.devices import (
    finalize_login_device,
    grant_device_trust,
    hash_device_token,
    match_login_device,
    revoke_user_device_trust,
)
from accounts.login_security.failures import record_failure_and_notify
from accounts.login_security.login_service import (
    COMMON_LOGIN_ERROR_MESSAGE,
    PENDING_AUTH_SESSION_KEY,
    USED_CODE_ERROR_MESSAGE,
    LoginStepStatus,
    apply_login_cookie,
    get_safe_redirect_url,
    load_pending_auth,
    submit_first_factor,
    submit_second_factor,
)
from accounts.login_security.recovery_codes import issue_recovery_codes
from accounts.login_security.second_factor import SecondFactorMethod
from accounts.models import AccountLock, CustomUser, LoginAttempt, LoginDevice, SecuritySetting

User = get_user_model()


class DummyLdapLikeBackend(ModelBackend):
    """LDAP 等、ModelBackend 以外の認証バックエンドを模したテスト用バックエンド。"""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username == "ldap_u" and password == "ldap_pw":
            return User.objects.filter(username="ldap_u").first()
        return None


def _totp_code(device: TOTPDevice) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


def _attach_session(request) -> None:
    store = SessionStore()
    store.create()
    request.session = store


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    CLIENT_IP_SOURCE="x_real_ip",
)
class LoginServiceTests(TestCase):
    """login_service.py の単体テスト（観点 a〜q）。"""

    def setUp(self):
        mail.outbox.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="taro", password="secret", email="taro@example.com"
        )

    def _post_request(self, *, ip="203.0.113.10", cookie_token=None, secure=False):
        req = self.factory.post(
            "/accounts/login/",
            HTTP_X_REAL_IP=ip,
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0",
            secure=secure,
        )
        if cookie_token:
            req.COOKIES[settings.DEVICE_COOKIE_NAME] = cookie_token
        _attach_session(req)
        return req

    def test_a_first_factor_only_creates_pending_without_login(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        req = self._post_request()
        res = submit_first_factor(req, username="taro", password="secret")

        self.assertEqual(res.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)
        self.assertNotIn(SESSION_KEY, req.session)
        self.assertIn(PENDING_AUTH_SESSION_KEY, req.session)

    def test_b_ip_blocked_rejects_without_calling_authenticate(self):
        now = timezone.now()
        ip = "198.51.100.10"
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

        req = self._post_request(ip=ip)
        with patch("accounts.login_security.login_service.authenticate") as mock_auth:
            res = submit_first_factor(req, username="taro", password="secret", now=now)

        mock_auth.assert_not_called()
        self.assertEqual(res.status, LoginStepStatus.REJECTED)
        self.assertEqual(res.error_message, COMMON_LOGIN_ERROR_MESSAGE)
        latest = LoginAttempt.objects.first()
        self.assertEqual(latest.result, LoginAttempt.Result.REJECTED_IP_BLOCKED)
        self.assertEqual(latest.second_factor, "")

    def test_c_locked_user_rejected_without_authenticate_case_insensitive(self):
        now = timezone.now()
        AccountLock.objects.create(
            user=self.user,
            failure_count=5,
            last_failed_at=now,
            locked_until=now + timedelta(minutes=10),
        )
        for variant in ("taro", "  TARO "):
            with self.subTest(variant=variant):
                req = self._post_request()
                with patch(
                    "accounts.login_security.login_service.authenticate"
                ) as mock_auth:
                    res = submit_first_factor(
                        req, username=variant, password="secret", now=now
                    )
                mock_auth.assert_not_called()
                self.assertEqual(res.status, LoginStepStatus.REJECTED)
                self.assertEqual(res.error_message, COMMON_LOGIN_ERROR_MESSAGE)
                self.assertEqual(
                    LoginAttempt.objects.first().result,
                    LoginAttempt.Result.REJECTED_LOCKED,
                )

    def test_d_e_password_failure_counts_unknown_and_locks_with_mail(self):
        now = timezone.now()
        # 存在しないユーザー名でも記録され IP 遮断に数えられる
        req_unknown = self._post_request(ip="198.51.100.20")
        res_u = submit_first_factor(
            req_unknown, username="no_such_user", password="bad", now=now
        )
        self.assertEqual(res_u.status, LoginStepStatus.INVALID_CREDENTIALS)
        self.assertEqual(
            LoginAttempt.objects.first().result,
            LoginAttempt.Result.PASSWORD_FAILED,
        )

        # 既存ユーザー 5 回失敗でロックし本人宛メール送信
        for i in range(4):
            req = self._post_request()
            r = submit_first_factor(
                req,
                username="taro",
                password="bad",
                now=now + timedelta(seconds=i + 1),
            )
            self.assertEqual(r.status, LoginStepStatus.INVALID_CREDENTIALS)

        with self.captureOnCommitCallbacks(execute=True):
            req5 = self._post_request()
            r5 = submit_first_factor(
                req5,
                username="taro",
                password="bad",
                now=now + timedelta(seconds=10),
            )
        self.assertEqual(r5.status, LoginStepStatus.INVALID_CREDENTIALS)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 5)
        self.assertTrue(any(m.to == ["taro@example.com"] for m in mail.outbox))

    def test_f_not_enrolled_completes_login_with_none_second_factor(self):
        req = self._post_request()
        with self.captureOnCommitCallbacks(execute=True):
            res = submit_first_factor(req, username="taro", password="secret")

        self.assertTrue(res.is_completed)
        self.assertEqual(res.second_factor, LoginAttempt.SecondFactor.NONE)
        self.assertIn(SESSION_KEY, req.session)
        attempt = LoginAttempt.objects.get(result=LoginAttempt.Result.SUCCESS)
        self.assertEqual(attempt.second_factor, LoginAttempt.SecondFactor.NONE)

    def test_g_trusted_device_skips_second_factor_until_expired_or_revoked(self):
        now = timezone.now()
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        fin = finalize_login_device(
            self.user, match_login_device(self.user, now=now), now=now
        )
        grant_device_trust(fin.device, 30, now=now)

        # 期限内は第二要素省略
        req1 = self._post_request(cookie_token=fin.plain_token)
        res1 = submit_first_factor(
            req1, username="taro", password="secret", now=now + timedelta(days=10)
        )
        self.assertTrue(res1.is_completed)
        self.assertEqual(res1.second_factor, LoginAttempt.SecondFactor.TRUSTED_DEVICE)

        # 取り消し後は第二要素が必要
        revoke_user_device_trust(self.user)
        req2 = self._post_request(cookie_token=fin.plain_token)
        res2 = submit_first_factor(
            req2, username="taro", password="secret", now=now + timedelta(days=11)
        )
        self.assertEqual(res2.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)

        # 期限切れ後も第二要素が必要
        grant_device_trust(fin.device, 30, now=now)
        req3 = self._post_request(cookie_token=fin.plain_token)
        res3 = submit_first_factor(
            req3,
            username="taro",
            password="secret",
            now=now + timedelta(days=31),
        )
        self.assertEqual(res3.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)

    def test_h_pending_auth_has_only_4_keys_and_expires_in_5_minutes(self):
        now = timezone.now()
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        req = self._post_request()
        old_key = req.session.session_key

        res = submit_first_factor(
            req, username="taro", password="secret", next_url="/persons/", now=now
        )
        self.assertEqual(res.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)
        self.assertNotEqual(req.session.session_key, old_key)

        pending_raw = req.session[PENDING_AUTH_SESSION_KEY]
        self.assertEqual(
            set(pending_raw.keys()), {"user_id", "backend", "started_at", "next_url"}
        )

        # 5 分経過前は有効
        load_ok = load_pending_auth(req, now=now + timedelta(seconds=299))
        self.assertIsNotNone(load_ok.state)
        self.assertFalse(load_ok.expired)

        # 5 分経過後は失効し、破棄される
        res_exp = submit_second_factor(
            req,
            code="123456",
            method=SecondFactorMethod.TOTP,
            now=now + timedelta(seconds=301),
        )
        self.assertEqual(res_exp.status, LoginStepStatus.EXPIRED)
        self.assertNotIn(PENDING_AUTH_SESSION_KEY, req.session)

    def test_i_second_factor_success_resets_failures_for_local_and_ldap_backends(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        AccountLock.objects.create(
            user=self.user, failure_count=3, last_failed_at=timezone.now()
        )
        req = self._post_request()
        submit_first_factor(req, username="taro", password="secret")

        with self.captureOnCommitCallbacks(execute=True):
            res = submit_second_factor(
                req, code=_totp_code(dev), method=SecondFactorMethod.TOTP
            )
        self.assertTrue(res.is_completed)
        self.assertIn(SESSION_KEY, req.session)
        self.assertEqual(
            req.session["_auth_user_backend"],
            "django.contrib.auth.backends.ModelBackend",
        )
        self.assertNotIn(PENDING_AUTH_SESSION_KEY, req.session)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 0)
        self.assertEqual(
            LoginAttempt.objects.filter(result=LoginAttempt.Result.SUCCESS).count(), 1
        )

        # LDAP を模したバックエンドでもバックエンドが明示されて確立する
        ldap_u = User.objects.create_user(username="ldap_u", password="x")
        ldap_u.set_unusable_password()
        ldap_u.save()
        ldap_codes = issue_recovery_codes(ldap_u)
        TOTPDevice.objects.create(user=ldap_u, name="default", confirmed=True)

        with override_settings(
            AUTHENTICATION_BACKENDS=[
                "accounts.tests.test_login_security_login_service.DummyLdapLikeBackend",
                "django.contrib.auth.backends.ModelBackend",
            ]
        ):
            req_l = self._post_request()
            r1 = submit_first_factor(req_l, username="ldap_u", password="ldap_pw")
            self.assertEqual(r1.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)
            r2 = submit_second_factor(
                req_l,
                code=ldap_codes[0],
                method=SecondFactorMethod.RECOVERY_CODE,
            )
            self.assertTrue(r2.is_completed)
            self.assertEqual(
                req_l.session["_auth_user_backend"],
                "accounts.tests.test_login_security_login_service.DummyLdapLikeBackend",
            )

    def test_j_invalid_and_used_code_keep_pending_auth(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        code = _totp_code(dev)
        wrong = "000000" if code != "000000" else "999999"

        req = self._post_request()
        submit_first_factor(req, username="taro", password="secret")

        # 不正 → 加算されるが仮認証維持
        r_bad = submit_second_factor(req, code=wrong, method=SecondFactorMethod.TOTP)
        self.assertEqual(r_bad.status, LoginStepStatus.CODE_INVALID)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)
        self.assertIn(PENDING_AUTH_SESSION_KEY, req.session)

        # 別の場所で同コード使用済みにしたあと、仮認証のまま使用済みコードを送る → 加算されず仮認証維持
        req_other = self._post_request()
        submit_first_factor(req_other, username="taro", password="secret")
        submit_second_factor(req_other, code=code, method=SecondFactorMethod.TOTP)

        r_used = submit_second_factor(req, code=code, method=SecondFactorMethod.TOTP)
        self.assertEqual(r_used.status, LoginStepStatus.CODE_USED)
        self.assertEqual(r_used.error_message, USED_CODE_ERROR_MESSAGE)
        self.assertIn(PENDING_AUTH_SESSION_KEY, req.session)

    def test_k_lockout_or_ip_block_during_second_factor_destroys_pending(self):
        now = timezone.now()
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        AccountLock.objects.create(user=self.user, failure_count=4, last_failed_at=now)

        req = self._post_request()
        submit_first_factor(req, username="taro", password="secret", now=now)
        self.assertIn(PENDING_AUTH_SESSION_KEY, req.session)

        # 5 回目のコード失敗でロック → 仮認証破棄・REJECTED
        r_lock = submit_second_factor(
            req,
            code="000000",
            method=SecondFactorMethod.RECOVERY_CODE,
            now=now + timedelta(seconds=1),
        )
        self.assertEqual(r_lock.status, LoginStepStatus.REJECTED)
        self.assertNotIn(PENDING_AUTH_SESSION_KEY, req.session)

        # 仮認証中に別経路で IP 遮断に達した場合も検証されず仮認証破棄
        AccountLock.objects.filter(user=self.user).update(
            failure_count=0, locked_until=None
        )
        req2 = self._post_request(ip="198.51.100.33")
        submit_first_factor(req2, username="taro", password="secret", now=now)
        for i in range(20):
            record_failure_and_notify(
                username_input=f"x_{i}",
                target_users=[],
                primary_user=None,
                ip_address="198.51.100.33",
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=now + timedelta(seconds=i + 1),
            )
        with patch("accounts.login_security.totp.verify_totp_code") as mock_totp:
            r_ip = submit_second_factor(
                req2,
                code="123456",
                method=SecondFactorMethod.TOTP,
                now=now + timedelta(seconds=30),
            )
        mock_totp.assert_not_called()
        self.assertEqual(r_ip.status, LoginStepStatus.REJECTED)
        self.assertNotIn(PENDING_AUTH_SESSION_KEY, req2.session)

    def test_l_first_factor_success_alone_does_not_reset_failure_count(self):
        now = timezone.now()
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        AccountLock.objects.create(user=self.user, failure_count=3, last_failed_at=now)

        req = self._post_request()
        res = submit_first_factor(req, username="taro", password="secret", now=now)
        self.assertEqual(res.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 3)

    def test_m_trust_grant_requires_all_three_conditions(self):
        codes = issue_recovery_codes(self.user)
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)

        # 1. チェックなし → 付与されない
        req1 = self._post_request()
        submit_first_factor(req1, username="taro", password="secret")
        res1 = submit_second_factor(
            req1,
            code=codes[0],
            method=SecondFactorMethod.RECOVERY_CODE,
            trust_device=False,
        )
        self.assertIsNone(
            LoginDevice.objects.get(
                token_hash=hash_device_token(res1.device_token)
            ).trusted_until
        )

        # 2. 日数 0 → チェックありでも付与されない
        setting = SecuritySetting.objects.get(id=1)
        setting.trusted_device_days = 0
        setting.save()
        req2 = self._post_request()
        submit_first_factor(req2, username="taro", password="secret")
        res2 = submit_second_factor(
            req2,
            code=codes[1],
            method=SecondFactorMethod.RECOVERY_CODE,
            trust_device=True,
        )
        self.assertIsNone(
            LoginDevice.objects.get(
                token_hash=hash_device_token(res2.device_token)
            ).trusted_until
        )

        # 3. 3条件揃う（チェックあり＋日数>0＋第二要素通過）→ 付与される
        setting.trusted_device_days = 30
        setting.save()
        req3 = self._post_request()
        submit_first_factor(req3, username="taro", password="secret")
        res3 = submit_second_factor(
            req3,
            code=codes[2],
            method=SecondFactorMethod.RECOVERY_CODE,
            trust_device=True,
        )
        self.assertIsNotNone(
            LoginDevice.objects.get(
                token_hash=hash_device_token(res3.device_token)
            ).trusted_until
        )

    def test_n_device_record_created_updated_and_isolated_across_users(self):
        req1 = self._post_request()
        res1 = submit_first_factor(req1, username="taro", password="secret")
        self.assertTrue(res1.is_new_device)
        self.assertIsNotNone(res1.device_token)
        resp = HttpResponse()
        apply_login_cookie(resp, req1, res1)
        self.assertEqual(
            resp.cookies[settings.DEVICE_COOKIE_NAME].value, res1.device_token
        )

        # 同じトークンで再ログイン → 既知端末として更新
        req2 = self._post_request(cookie_token=res1.device_token)
        res2 = submit_first_factor(req2, username="taro", password="secret")
        self.assertFalse(res2.is_new_device)
        self.assertEqual(res2.device_token, res1.device_token)
        self.assertEqual(LoginDevice.objects.filter(user=self.user).count(), 1)

        # 他ユーザーが同じクッキーを送ると新しい端末になる
        other = User.objects.create_user(username="jiro", password="pw")
        req3 = self._post_request(cookie_token=res1.device_token)
        res3 = submit_first_factor(req3, username="jiro", password="pw")
        self.assertTrue(res3.is_new_device)
        self.assertNotEqual(res3.device_token, res1.device_token)
        self.assertEqual(LoginDevice.objects.filter(user=other).count(), 1)

    def test_o_login_notification_by_level_and_new_device(self):
        setting = SecuritySetting.objects.get(id=1)
        setting.login_notification_level = (
            SecuritySetting.LoginNotificationLevel.NEW_DEVICE_ONLY
        )
        setting.save()

        # 新しい端末 → 送信される
        with self.captureOnCommitCallbacks(execute=True):
            req1 = self._post_request()
            res1 = submit_first_factor(req1, username="taro", password="secret")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("新しい端末", mail.outbox[0].subject)

        # 既知の端末（NEW_DEVICE_ONLY）→ 送信されない
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            req2 = self._post_request(cookie_token=res1.device_token)
            submit_first_factor(req2, username="taro", password="secret")
        self.assertEqual(len(mail.outbox), 0)

        # 既知の端末（EVERY）→ 送信される
        setting.login_notification_level = SecuritySetting.LoginNotificationLevel.EVERY
        setting.save()
        with self.captureOnCommitCallbacks(execute=True):
            req3 = self._post_request(cookie_token=res1.device_token)
            submit_first_factor(req3, username="taro", password="secret")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "【FreeGroup2】ログイン通知")

    @override_settings(ALLOWED_HOSTS=["example.com", "testserver"])
    def test_p_safe_redirect_url_direct_and_across_pending_auth(self):
        req = self.factory.get("/", HTTP_HOST="example.com")
        self.assertEqual(get_safe_redirect_url(req, "/persons/"), "/persons/")
        self.assertEqual(get_safe_redirect_url(req, "https://evil.example/"), "/")
        self.assertEqual(get_safe_redirect_url(req, "//evil.example/path"), "/")

        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        req_p = self._post_request()
        submit_first_factor(
            req_p,
            username="taro",
            password="secret",
            next_url="https://evil.example/phish",
        )
        res = submit_second_factor(
            req_p, code=_totp_code(dev), method=SecondFactorMethod.TOTP
        )
        self.assertEqual(res.redirect_url, "/")

    def test_q_no_customuser_save_except_last_login(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        orig_save = CustomUser.save
        non_last_login_saves = []

        def tracked_save(instance, *args, **kwargs):
            update_fields = kwargs.get("update_fields")
            if update_fields != ["last_login"]:
                non_last_login_saves.append(update_fields)
            return orig_save(instance, *args, **kwargs)

        with patch.object(CustomUser, "save", autospec=True, side_effect=tracked_save):
            req = self._post_request()
            submit_first_factor(req, username="taro", password="secret")
            submit_second_factor(
                req, code=_totp_code(dev), method=SecondFactorMethod.TOTP
            )
            req_bad = self._post_request()
            submit_first_factor(req_bad, username="taro", password="wrong")

        self.assertEqual(non_last_login_saves, [])

    def test_r_requires_two_factor_setup_by_enforcement_and_enrollment(self):
        setting = SecuritySetting.objects.get(id=1)

        # 1. 強制レベルが任意で未登録 → False
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.OPTIONAL
        setting.save()
        req1 = self._post_request()
        res1 = submit_first_factor(req1, username="taro", password="secret")
        self.assertTrue(res1.is_completed)
        self.assertFalse(res1.requires_two_factor_setup)

        # 2. 必須対象（全員必須）で未登録 → True
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        setting.save()
        req2 = self._post_request()
        res2 = submit_first_factor(req2, username="taro", password="secret")
        self.assertTrue(res2.is_completed)
        self.assertTrue(res2.requires_two_factor_setup)

        # 3. 必須対象で確定済み TOTP がある場合（第二要素通過後）→ False
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        req3 = self._post_request()
        res3_first = submit_first_factor(req3, username="taro", password="secret")
        self.assertEqual(res3_first.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)
        res3_second = submit_second_factor(
            req3, code=_totp_code(dev), method=SecondFactorMethod.TOTP
        )
        self.assertTrue(res3_second.is_completed)
        self.assertFalse(res3_second.requires_two_factor_setup)

    def test_s_device_re_evaluated_before_login_completion(self):
        now = timezone.now()
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        codes = issue_recovery_codes(self.user)

        # 初めての端末は端末記録の更新前に新しい端末と判定される（§4 順4・§6.4）
        req_first = self._post_request()
        submit_first_factor(req_first, username="taro", password="secret", now=now)
        res_first = submit_second_factor(
            req_first,
            code=_totp_code(dev),
            method=SecondFactorMethod.TOTP,
            now=now + timedelta(seconds=1),
        )
        self.assertTrue(res_first.is_completed)
        self.assertTrue(res_first.is_new_device)
        first_token = res_first.device_token

        # 仮認証作成後、第二要素送信前に端末記録の状態が変わった場合（新規作成＋信頼付与）
        # 仮認証作成時点では DB にないトークンを使う → 順4時点では新規端末だが、
        # 第二要素送信前に同トークンで端末記録を作成・信頼付与すると再評価後に既知端末・信頼状態が反映される
        pending_token = "pending-created-token-value"
        req_re = self._post_request(cookie_token=pending_token)
        res_pending = submit_first_factor(
            req_re, username="taro", password="secret", now=now + timedelta(seconds=10)
        )
        self.assertEqual(res_pending.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)

        created_device = LoginDevice.objects.create(
            user=self.user,
            token_hash=hash_device_token(pending_token),
            first_seen_at=now + timedelta(seconds=12),
            last_seen_at=now + timedelta(seconds=12),
            last_ip="203.0.113.10",
            user_agent="UA",
        )
        grant_device_trust(created_device, 30, now=now + timedelta(seconds=12))

        res_done = submit_second_factor(
            req_re,
            code=codes[0],
            method=SecondFactorMethod.RECOVERY_CODE,
            trust_device=False,
            now=now + timedelta(seconds=15),
        )
        self.assertTrue(res_done.is_completed)
        self.assertFalse(res_done.is_new_device)
        self.assertEqual(res_done.device_token, pending_token)
        self.assertIsNotNone(
            LoginDevice.objects.get(pk=created_device.pk).trusted_until
        )

        # 逆に仮認証作成時点で既知端末（期限切れ）だったものが第二要素送信前に削除された場合は新規端末になる
        req_del = self._post_request(cookie_token=first_token)
        res_del_pending = submit_first_factor(
            req_del, username="taro", password="secret", now=now + timedelta(seconds=20)
        )
        self.assertEqual(res_del_pending.status, LoginStepStatus.SECOND_FACTOR_REQUIRED)
        LoginDevice.objects.filter(
            user=self.user, token_hash=hash_device_token(first_token)
        ).delete()

        res_del_done = submit_second_factor(
            req_del,
            code=codes[1],
            method=SecondFactorMethod.RECOVERY_CODE,
            now=now + timedelta(seconds=25),
        )
        self.assertTrue(res_del_done.is_completed)
        self.assertTrue(res_del_done.is_new_device)
        self.assertNotEqual(res_del_done.device_token, first_token)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    CLIENT_IP_SOURCE="x_real_ip",
)
class LoginServiceAtomicTransactionTests(TransactionTestCase):
    """失敗記録が transaction.atomic() の外で行われることを検証するテスト。"""

    def setUp(self):
        SecuritySetting.objects.get_or_create(id=1)
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="taro", password="secret", email="taro@example.com"
        )

    def _post_request(self, *, ip="203.0.113.10"):
        req = self.factory.post(
            "/accounts/login/",
            HTTP_X_REAL_IP=ip,
            HTTP_USER_AGENT="Mozilla/5.0",
        )
        _attach_session(req)
        return req

    def test_failure_record_runs_outside_atomic_and_persists_on_post_error(self):
        now = timezone.now()
        atomic_states = []
        orig_record = record_failure_and_notify

        def spy_record(*args, **kwargs):
            atomic_states.append(connection.in_atomic_block)
            return orig_record(*args, **kwargs)

        # 4 回失敗させておく
        for i in range(4):
            with patch(
                "accounts.login_security.login_service.record_failure_and_notify",
                side_effect=spy_record,
            ):
                res = submit_first_factor(
                    self._post_request(),
                    username="taro",
                    password="bad",
                    now=now + timedelta(seconds=i),
                )
            self.assertEqual(res.status, LoginStepStatus.INVALID_CREDENTIALS)

        self.assertEqual(atomic_states, [False, False, False, False])

        # 5 回目でロック到達後、後続の通知処理で例外が起きても試行記録とロック行は残る（§5.6）
        with patch(
            "accounts.login_security.failures.notify_account_lock",
            side_effect=RuntimeError("simulated notification failure"),
        ):
            with self.assertRaises(RuntimeError):
                submit_first_factor(
                    self._post_request(),
                    username="taro",
                    password="bad",
                    now=now + timedelta(seconds=10),
                )

        self.assertEqual(
            LoginAttempt.objects.filter(
                user=self.user, result=LoginAttempt.Result.PASSWORD_FAILED
            ).count(),
            5,
        )
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 5)
        self.assertIsNotNone(lock.locked_until)
