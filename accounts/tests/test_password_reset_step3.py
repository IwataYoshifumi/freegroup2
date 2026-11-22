"""パスワード変更・リセット Step 3（サービス層・通知・単体テスト）の検証テスト。"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.core import mail
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE, ActionLogAction, AuthSource
from accounts.login_security.devices import (
    finalize_login_device,
    grant_device_trust,
    match_login_device,
)
from accounts.login_security.lockout import is_user_locked
from accounts.login_security.recovery_codes import issue_recovery_codes
from accounts.models import (
    AccountLock,
    CustomUser,
    LoginAttempt,
    LoginDevice,
    PasswordResetRequest,
    RecoveryCode,
    Role,
    SecuritySetting,
)
from accounts.password_security.eligibility import is_password_self_service_eligible
from accounts.password_security.notifications import (
    notify_password_changed,
    notify_password_reset_completed_to_admins,
    notify_password_reset_completed_to_user,
    notify_password_reset_requested,
)
from accounts.password_security.password_change import (
    PasswordChangeStatus,
    change_password,
)
from accounts.password_security.password_reset import (
    PasswordResetCompleteStatus,
    build_password_reset_url,
    complete_password_reset,
    request_password_reset,
    verify_password_reset_token,
)
from accounts.services import apply_role
from actionlogs.models import ActionLog

User = get_user_model()

VALID_NEW_PASSWORD = "NewStrong#Password991"


def _create_user_session(user) -> str:
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store.set_expiry(timedelta(days=7))
    store.save()
    return store.session_key


def _make_request_with_middlewares(
    factory: RequestFactory,
    method: str = "POST",
    path: str = "/dummy/",
    *,
    host: str = "testserver",
    ip_address: str = "203.0.113.10",
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0.0.0",
    session_key: str | None = None,
):
    req_fn = factory.post if method == "POST" else factory.get
    req = req_fn(
        path,
        HTTP_HOST=host,
        HTTP_X_REAL_IP=ip_address,
        HTTP_USER_AGENT=user_agent,
    )
    if session_key:
        req.COOKIES[settings.SESSION_COOKIE_NAME] = session_key
    SessionMiddleware(lambda r: HttpResponse("ok")).process_request(req)
    AuthenticationMiddleware(lambda r: HttpResponse("ok")).process_request(req)
    return req


class EligibilityTests(TestCase):
    """対象外判定（§5.5 / §13-9）の単体テスト。"""

    def test_three_conditions_individually_and_local_user_eligible(self):
        # 1. 認証元が LDAP 単独で対象外（識別名空・使用可能パスワードありでも不可）
        u_ldap_source = User.objects.create_user(
            username="el_ldap_src",
            password="Initial#Password1",
            auth_source=AuthSource.LDAP,
            ldap_dn=None,
        )
        self.assertTrue(u_ldap_source.has_usable_password())
        self.assertFalse(is_password_self_service_eligible(u_ldap_source))

        # 2. LDAP の識別名がある単独で対象外（auth_source が local に書き換えられても不可）
        u_ldap_dn = User.objects.create_user(
            username="el_ldap_dn",
            password="Initial#Password1",
            auth_source=AuthSource.LOCAL,
            ldap_dn="CN=el_ldap_dn,DC=example,DC=local",
        )
        self.assertTrue(u_ldap_dn.has_usable_password())
        self.assertFalse(is_password_self_service_eligible(u_ldap_dn))

        # 3. パスワードが使用不可単独で対象外
        u_unusable = User.objects.create_user(
            username="el_unusable",
            password=None,
            auth_source=AuthSource.LOCAL,
            ldap_dn=None,
        )
        self.assertFalse(u_unusable.has_usable_password())
        self.assertFalse(is_password_self_service_eligible(u_unusable))

        # すべてに該当しないローカルユーザーは対象
        u_local = User.objects.create_user(
            username="el_local_ok",
            password="Initial#Password1",
            auth_source=AuthSource.LOCAL,
            ldap_dn=None,
        )
        self.assertTrue(is_password_self_service_eligible(u_local))
        self.assertFalse(is_password_self_service_eligible(None))


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    CLIENT_IP_SOURCE="x_real_ip",
    ALLOWED_HOSTS=["testserver", "fg2.example.local"],
)
class PasswordChangeServiceTests(TestCase):
    """パスワード変更サービス（§5.2〜§5.5 / §13 / §14）の単体テスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="pwchg_u1",
            email="pwchg_u1@example.com",
            password="Current#Password123",
            first_name="花子",
            last_name="鈴木",
            auth_source=AuthSource.LOCAL,
            ldap_dn=None,
        )
        self.other_user = User.objects.create_user(
            username="pwchg_other",
            email="pwchg_other@example.com",
            password="Other#Password123",
        )

    def test_change_success_maintains_current_session_revokes_others_logs_and_notifies(self):
        # 操作中セッション（client.login で本物ログイン）＋別端末セッション＋他ユーザーセッション
        self.assertTrue(
            self.client.login(username="pwchg_u1", password="Current#Password123")
        )
        old_keep_key = self.client.session.session_key
        other_user_own_sess = _create_user_session(self.user)
        third_party_sess = _create_user_session(self.other_user)

        # 信頼済み端末を作っておき、変更後も信頼が消えないことを確認
        match_res = match_login_device(self.user)
        fin = finalize_login_device(self.user, match_res, ip_address="203.0.113.10")
        grant_device_trust(fin.device, days=30)

        req = _make_request_with_middlewares(
            self.factory,
            "POST",
            "/accounts/password/change/",
            session_key=old_keep_key,
            ip_address="203.0.113.55",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Edg/130.0",
        )

        # update_fields=["password"] のみ保存されることを検証
        save_calls = []
        orig_save = CustomUser.save

        def spy_save(instance, *args, **kwargs):
            save_calls.append((instance.pk, kwargs.get("update_fields")))
            return orig_save(instance, *args, **kwargs)

        def _view(request):
            result = change_password(
                request.user,
                current_password="Current#Password123",
                new_password=VALID_NEW_PASSWORD,
                request=request,
            )
            self._last_result = result
            return HttpResponse("done")

        with patch.object(CustomUser, "save", autospec=True, side_effect=spy_save):
            with self.captureOnCommitCallbacks(execute=True):
                sm = SessionMiddleware(_view)
                resp = sm(req)

        self.assertEqual(resp.status_code, 200)
        res = self._last_result
        self.assertTrue(res.is_success)
        # 認証ハッシュ更新でセッションキーが切り替わり、新しいキーが渡されたため維持されている
        self.assertIsNotNone(res.keep_session_key)
        self.assertNotEqual(res.keep_session_key, old_keep_key)
        self.assertTrue(Session.objects.filter(session_key=res.keep_session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=other_user_own_sess).exists())
        self.assertTrue(Session.objects.filter(session_key=third_party_sess).exists())

        # 変更直後のレスポンスCookieを使った次のリクエストでも認証済みとして扱われる
        new_cookie = resp.cookies[settings.SESSION_COOKIE_NAME].value
        next_req = _make_request_with_middlewares(
            self.factory,
            "GET",
            "/accounts/profile/",
            session_key=new_cookie,
        )
        self.assertTrue(next_req.user.is_authenticated)
        self.assertEqual(next_req.user.pk, self.user.pk)

        # 新パスワードで再ログインできる・他フィールド不変・update_fields=["password"]
        self.assertEqual(save_calls, [(self.user.pk, ["password"])])
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "花子")
        self.assertEqual(self.user.email, "pwchg_u1@example.com")
        self.assertTrue(
            self.client.login(username="pwchg_u1", password=VALID_NEW_PASSWORD)
        )

        # 端末の信頼は維持されている
        fin.device.refresh_from_db()
        self.assertIsNotNone(fin.device.trusted_until)

        # 操作ログ記録（パスワード文字列が出ない）
        log = ActionLog.objects.get(action=ActionLogAction.PASSWORD_CHANGE)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.object_id, str(self.user.pk))
        self.assertNotIn("Current#Password123", f"{log.object_repr}{log.data}{log.note}")
        self.assertNotIn(VALID_NEW_PASSWORD, f"{log.object_repr}{log.data}{log.note}")

        # 本人宛完了通知（リンクなし・JST日時・IP・ブラウザOS）
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["pwchg_u1@example.com"])
        self.assertEqual(sent.subject, "【FreeGroup2】パスワードを変更しました")
        self.assertIn("203.0.113.55", sent.body)
        self.assertIn("Edge／Windows", sent.body)
        self.assertIn("(JST)", sent.body)
        self.assertNotIn("http", sent.body.lower())
        self.assertNotIn(VALID_NEW_PASSWORD, sent.body)

    def test_validation_and_failure_counting_rules(self):
        # 強度不足（短い）では失敗回数もログイン試行記録も増えない
        r_weak = change_password(
            self.user,
            current_password="Current#Password123",
            new_password="123",
            ip_address="203.0.113.20",
        )
        self.assertEqual(r_weak.status, PasswordChangeStatus.VALIDATION_ERROR)
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())
        self.assertEqual(LoginAttempt.objects.filter(user=self.user).count(), 0)

        # 現在と同じパスワードでも失敗に数えない
        r_same = change_password(
            self.user,
            current_password="Current#Password123",
            new_password="Current#Password123",
            ip_address="203.0.113.20",
        )
        self.assertEqual(r_same.status, PasswordChangeStatus.SAME_AS_CURRENT)
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())

        # 現在のパスワード誤りのときは、新パスワードが弱くても強度検証結果を返さず失敗加算する
        with self.captureOnCommitCallbacks(execute=True):
            r_wrong = change_password(
                self.user,
                current_password="Wrong#Password",
                new_password="123",
                ip_address="203.0.113.20",
            )
        self.assertEqual(r_wrong.status, PasswordChangeStatus.INVALID_CURRENT_PASSWORD)
        self.assertEqual(r_wrong.errors, ("現在のパスワードが正しくありません。",))
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 1)

        # 空の現在のパスワードも必須エラーにせず失敗加算する（§5.2 順3 / §13-17）
        r_empty = change_password(
            self.user,
            current_password="",
            new_password=VALID_NEW_PASSWORD,
            ip_address="203.0.113.20",
        )
        self.assertEqual(r_empty.status, PasswordChangeStatus.INVALID_CURRENT_PASSWORD)
        lock.refresh_from_db()
        self.assertEqual(lock.failure_count, 2)
        self.assertEqual(
            LoginAttempt.objects.filter(
                user=self.user,
                result=LoginAttempt.Result.PASSWORD_FAILED,
            ).count(),
            2,
        )

    def test_lockout_threshold_notifies_once_rejects_even_correct_pw_and_keeps_session(self):
        admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        admin_user = User.objects.create_user(
            username="pwchg_admin",
            email="pwchg_admin@example.com",
            password="pw",
            role=admin_role,
        )
        setting = SecuritySetting.objects.get(id=1)
        setting.lockout_threshold = 3
        setting.notify_admins_on_account_lock = True
        setting.save()

        logged_in_sess = _create_user_session(self.user)
        now = timezone.now()

        with self.captureOnCommitCallbacks(execute=True):
            for idx in range(3):
                res = change_password(
                    self.user,
                    current_password="wrong-secret-password",
                    new_password=VALID_NEW_PASSWORD,
                    ip_address="203.0.113.77",
                    now=now + timedelta(seconds=idx),
                )
        self.assertTrue(res.entered_lock)
        self.assertTrue(is_user_locked(self.user, now=now + timedelta(seconds=5)))
        # ロック発生通知は本人宛1＋管理者宛1（再試行しても増えない）
        lock_mails_before = len(mail.outbox)
        self.assertEqual(lock_mails_before, 2)

        # ロック中は現在のパスワードが正しくても拒否
        with self.captureOnCommitCallbacks(execute=True):
            r_locked = change_password(
                self.user,
                current_password="Current#Password123",
                new_password=VALID_NEW_PASSWORD,
                ip_address="203.0.113.77",
                now=now + timedelta(seconds=10),
            )
        self.assertEqual(r_locked.status, PasswordChangeStatus.LOCKED)
        self.assertEqual(len(mail.outbox), lock_mails_before)

        # ログイン中のセッションは切れない・ログに秘密値が出ない
        self.assertTrue(Session.objects.filter(session_key=logged_in_sess).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Current#Password123"))
        for attempt in LoginAttempt.objects.filter(user=self.user):
            self.assertNotIn("wrong-secret-password", f"{attempt.username_input}{attempt.user_agent}")
            self.assertNotIn(VALID_NEW_PASSWORD, f"{attempt.username_input}{attempt.user_agent}")

    def test_not_eligible_users_are_rejected_without_saving_password(self):
        cases = [
            User.objects.create_user(
                username="chg_ldap_src",
                password="Current#Password123",
                auth_source=AuthSource.LDAP,
            ),
            User.objects.create_user(
                username="chg_ldap_dn",
                password="Current#Password123",
                auth_source=AuthSource.LOCAL,
                ldap_dn="CN=chg,DC=example,DC=local",
            ),
            User.objects.create_user(
                username="chg_unusable",
                password=None,
                auth_source=AuthSource.LOCAL,
            ),
        ]
        for u in cases:
            r = change_password(
                u,
                current_password="Current#Password123",
                new_password=VALID_NEW_PASSWORD,
            )
            self.assertEqual(r.status, PasswordChangeStatus.NOT_ELIGIBLE)
            u.refresh_from_db()
            self.assertFalse(u.check_password(VALID_NEW_PASSWORD))


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    CLIENT_IP_SOURCE="x_real_ip",
    ALLOWED_HOSTS=["testserver", "portal.example.local"],
    PASSWORD_RESET_TIMEOUT=3600,
)
class PasswordResetServiceTests(TestCase):
    """パスワードリセット申請判定・制限・リンク検証・完了・通知（§6 / §7 / §13 / §14）のテスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="reset_u1",
            email="Reset_U1@example.com",
            password="Old#Password123",
            first_name="太郎",
            last_name="佐藤",
            auth_source=AuthSource.LOCAL,
            ldap_dn=None,
        )
        self.other_user = User.objects.create_user(
            username="reset_other",
            email="other@example.com",
            password="Other#Password123",
        )

    def _req(self, *, host="portal.example.local", ip="198.51.100.10", ua="Firefox/130.0 Linux"):
        return self.factory.post(
            "/accounts/password/reset/",
            HTTP_HOST=host,
            HTTP_X_REAL_IP=ip,
            HTTP_USER_AGENT=ua,
        )

    def test_request_all_results_case_whitespace_multi_inactive_empty_and_254_truncation(self):
        # 同メールの複数有効ユーザー・対象外・無効・空メールユーザーを用意
        u_dup = User.objects.create_user(
            username="reset_u1_dup",
            email="reset_u1@example.com",
            password="Old#Password123",
            auth_source=AuthSource.LOCAL,
        )
        u_dup_ldap = User.objects.create_user(
            username="reset_u1_ldap",
            email="reset_u1@example.com",
            password="Old#Password123",
            auth_source=AuthSource.LOCAL,
            ldap_dn="CN=reset_u1_ldap,DC=example,DC=local",
        )
        User.objects.create_user(
            username="reset_u1_inactive",
            email="reset_u1@example.com",
            password="Old#Password123",
            is_active=False,
        )
        User.objects.create_user(
            username="empty_mail_user",
            email="",
            password="Old#Password123",
        )

        # (1) 空入力（空白のみ）は email="" ユーザーに一致させず NO_MATCH・メール送信なし
        with self.captureOnCommitCallbacks(execute=True):
            out_empty = request_password_reset("   ", request=self._req(ip="198.51.100.11"))
        self.assertEqual(out_empty.sent_count, 0)
        self.assertEqual(out_empty.entries[0].result, PasswordResetRequest.Result.NO_MATCH)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ActionLog.objects.filter(action=ActionLogAction.PASSWORD_RESET_REQUEST).count(), 0)

        # (2) 該当なし＋254文字超入力の切り捨て
        long_input = ("x" * 250) + "@example.com"  # 262文字
        out_long = request_password_reset(long_input, request=self._req(ip="198.51.100.12"))
        self.assertEqual(out_long.entries[0].result, PasswordResetRequest.Result.NO_MATCH)
        self.assertEqual(len(out_long.entries[0].request_record.email_input), 254)
        self.assertEqual(out_long.entries[0].request_record.email_input, long_input[:254])

        # (3) 前後空白＋大文字小文字違い → 有効2名に送信、LDAP識別名あり1名は NOT_ELIGIBLE、無効ユーザーは除外
        with self.captureOnCommitCallbacks(execute=True):
            out_multi = request_password_reset(
                "  RESET_U1@Example.COM  ",
                request=self._req(ip="198.51.100.13", host="portal.example.local"),
            )
        self.assertEqual(out_multi.sent_count, 2)
        results_by_user = {e.user.username: e.result for e in out_multi.entries}
        self.assertEqual(
            results_by_user,
            {
                "reset_u1": PasswordResetRequest.Result.SENT,
                "reset_u1_dup": PasswordResetRequest.Result.SENT,
                "reset_u1_ldap": PasswordResetRequest.Result.NOT_ELIGIBLE,
            },
        )
        self.assertEqual(len(mail.outbox), 2)
        for msg in mail.outbox:
            self.assertEqual(msg.subject, "【FreeGroup2】パスワード再設定のご案内")
            self.assertIn("http://portal.example.local/accounts/password/reset/", msg.body)
            self.assertIn("1時間", msg.body)
            self.assertIn("心当たりがない場合は、このメールを無視してください", msg.body)

        # 操作ログは送信した2名分だけ、操作者 None、IP入り、トークン/リンクなし
        logs = ActionLog.objects.filter(action=ActionLogAction.PASSWORD_RESET_REQUEST)
        self.assertEqual(logs.count(), 2)
        for log in logs:
            self.assertIsNone(log.user)
            self.assertEqual(log.data.get("ip_address"), "198.51.100.13")
            self.assertNotIn("/accounts/password/reset/", f"{log.data}{log.note}{log.object_repr}")

    def test_rate_limits_ip_blocked_and_recovery_within_15_minutes(self):
        t0 = timezone.now()
        same_ip = "198.51.100.20"

        # 同じ IP から 5 件（該当なし・送信・対象外を混在させて5件通る、自分自身をカウントしない）
        u_unusable = User.objects.create_user(
            username="rl_unusable",
            email="rl_unusable@example.com",
            password=None,
        )
        with self.captureOnCommitCallbacks(execute=True):
            for idx in range(5):
                target = "missing@example.com" if idx % 2 == 0 else "rl_unusable@example.com"
                res = request_password_reset(
                    target,
                    request=self._req(ip=same_ip),
                    now=t0 + timedelta(seconds=idx * 10),
                )
                self.assertIn(
                    res.entries[0].result,
                    (PasswordResetRequest.Result.NO_MATCH, PasswordResetRequest.Result.NOT_ELIGIBLE),
                )

        # 6 件目・7 件目は制限超過（RATE_LIMITED）かつメール送信なし
        with self.captureOnCommitCallbacks(execute=True):
            r6 = request_password_reset(
                "Reset_U1@example.com",
                request=self._req(ip=same_ip),
                now=t0 + timedelta(minutes=5),
            )
            r7 = request_password_reset(
                "Reset_U1@example.com",
                request=self._req(ip=same_ip),
                now=t0 + timedelta(minutes=10),
            )
        self.assertEqual(r6.entries[0].result, PasswordResetRequest.Result.RATE_LIMITED)
        self.assertEqual(r7.entries[0].result, PasswordResetRequest.Result.RATE_LIMITED)
        self.assertEqual(len(mail.outbox), 0)

        # 制限中に申請（r6/r7）が続いていても RATE_LIMITED は件数に数えないため、
        # t0 から 15 分 + 1 秒進めば直近15分以内のカウント対象は 0〜1 件へ減り再び送信可能になる
        with self.captureOnCommitCallbacks(execute=True):
            r_recovered = request_password_reset(
                "Reset_U1@example.com",
                request=self._req(ip=same_ip),
                now=t0 + timedelta(minutes=15, seconds=1),
            )
        self.assertEqual(r_recovered.sent_count, 1)
        self.assertEqual(r_recovered.entries[0].result, PasswordResetRequest.Result.SENT)
        self.assertEqual(len(mail.outbox), 1)

        # 同じユーザー宛は IP を変えても 1 時間に 3 通まで（すでに r_recovered で 1 通送信済み → あと 2 通通り 4 通目は RATE_LIMITED）
        with self.captureOnCommitCallbacks(execute=True):
            r_u2 = request_password_reset(
                "Reset_U1@example.com",
                request=self._req(ip="198.51.100.21"),
                now=t0 + timedelta(minutes=16),
            )
            r_u3 = request_password_reset(
                "Reset_U1@example.com",
                request=self._req(ip="198.51.100.22"),
                now=t0 + timedelta(minutes=17),
            )
            r_u4 = request_password_reset(
                "Reset_U1@example.com",
                request=self._req(ip="198.51.100.23"),
                now=t0 + timedelta(minutes=18),
            )
        self.assertEqual(r_u2.sent_count, 1)
        self.assertEqual(r_u3.sent_count, 1)
        self.assertEqual(r_u4.sent_count, 0)
        self.assertEqual(r_u4.entries[0].result, PasswordResetRequest.Result.RATE_LIMITED)

        # IP 遮断中の場合は IP_BLOCKED（IP遮断記録も制限件数には数えない）
        setting = SecuritySetting.objects.get(id=1)
        setting.ip_block_threshold = 3
        setting.save()
        blocked_ip = "198.51.100.99"
        for idx in range(3):
            LoginAttempt.objects.create(
                created_at=t0 + timedelta(minutes=18),
                username_input="x",
                ip_address=blocked_ip,
                result=LoginAttempt.Result.PASSWORD_FAILED,
            )
        r_ip_blk = request_password_reset(
            "Reset_U1@example.com",
            request=self._req(ip=blocked_ip),
            now=t0 + timedelta(minutes=19),
        )
        self.assertEqual(r_ip_blk.sent_count, 0)
        self.assertEqual(r_ip_blk.entries[0].result, PasswordResetRequest.Result.IP_BLOCKED)

    def test_token_expiry_single_use_login_invalidation_and_runtime_eligibility(self):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        issued_at = datetime(2026, 11, 22, 12, 0, 0)

        with patch.object(default_token_generator, "_now", return_value=issued_at):
            token = default_token_generator.make_token(self.user)
            url = build_password_reset_url(
                self._req(host="portal.example.local"),
                self.user,
                token=token,
            )
        self.assertIn(f"http://portal.example.local/accounts/password/reset/{uidb64}/{token}/", url)

        # 3599秒後は有効、3601秒後は有効期限（1時間）超過で無効
        with patch.object(
            default_token_generator,
            "_now",
            return_value=issued_at + timedelta(seconds=3599),
        ):
            self.assertTrue(verify_password_reset_token(uidb64, token).is_valid)

        with patch.object(
            default_token_generator,
            "_now",
            return_value=issued_at + timedelta(seconds=3601),
        ):
            self.assertFalse(verify_password_reset_token(uidb64, token).is_valid)
            r_exp = complete_password_reset(
                uidb64=uidb64,
                token=token,
                new_password=VALID_NEW_PASSWORD,
            )
            self.assertEqual(r_exp.status, PasswordResetCompleteStatus.INVALID_LINK)

        # 無効化条件：(a) リンク発行後にログインが成立すると無効
        u_login = User.objects.create_user(
            username="reset_after_login",
            email="reset_after_login@example.com",
            password="Old#Password123",
        )
        uid_login = urlsafe_base64_encode(force_bytes(u_login.pk))
        tok_login = default_token_generator.make_token(u_login)
        self.assertTrue(verify_password_reset_token(uid_login, tok_login).is_valid)
        self.assertTrue(self.client.login(username="reset_after_login", password="Old#Password123"))
        self.assertFalse(verify_password_reset_token(uid_login, tok_login).is_valid)

        # 無効化条件：(b) 設定時に対象外3条件（auth_source / ldap_dn / unusable）または is_active=False に変化
        for field_patch in (
            {"is_active": False},
            {"auth_source": AuthSource.LDAP},
            {"ldap_dn": "CN=changed,DC=example,DC=local"},
            {"unusable": True},
        ):
            tmp = User.objects.create_user(
                username=f"tmp_{list(field_patch.keys())[0]}",
                email="tmp@example.com",
                password="Old#Password123",
            )
            tmp_uid = urlsafe_base64_encode(force_bytes(tmp.pk))
            tmp_tok = default_token_generator.make_token(tmp)
            self.assertTrue(verify_password_reset_token(tmp_uid, tmp_tok).is_valid)

            if field_patch.get("unusable"):
                tmp.set_unusable_password()
                tmp.save()
            else:
                for k, v in field_patch.items():
                    setattr(tmp, k, v)
                tmp.save()

            res_bad = complete_password_reset(
                uidb64=tmp_uid,
                token=tmp_tok,
                new_password=VALID_NEW_PASSWORD,
            )
            self.assertEqual(res_bad.status, PasswordResetCompleteStatus.INVALID_LINK)
            tmp.refresh_from_db()
            self.assertFalse(tmp.check_password(VALID_NEW_PASSWORD))

        # 無効なリンクはパスワードを変えない・完了後に同じリンクは再利用不可
        r_invalid_tok = complete_password_reset(
            uidb64=uidb64,
            token="invalid-token-value",
            new_password=VALID_NEW_PASSWORD,
        )
        self.assertEqual(r_invalid_tok.status, PasswordResetCompleteStatus.INVALID_LINK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Old#Password123"))

        # 有効期限内なら強度不足・現在と同じパスワードは拒否され、正常パスワードで完了・再設定後は再利用不可
        with patch.object(
            default_token_generator,
            "_now",
            return_value=issued_at + timedelta(seconds=60),
        ):
            r_weak = complete_password_reset(
                uidb64=uidb64,
                token=token,
                new_password="123",
            )
            self.assertEqual(r_weak.status, PasswordResetCompleteStatus.VALIDATION_ERROR)

            r_same = complete_password_reset(
                uidb64=uidb64,
                token=token,
                new_password="Old#Password123",
            )
            self.assertEqual(r_same.status, PasswordResetCompleteStatus.SAME_AS_CURRENT)

            anon_req = _make_request_with_middlewares(self.factory, "POST", "/accounts/password/reset/confirm/")
            r_complete = complete_password_reset(
                uidb64=uidb64,
                token=token,
                new_password=VALID_NEW_PASSWORD,
                request=anon_req,
            )
            self.assertTrue(r_complete.is_success)
            self.assertFalse(anon_req.user.is_authenticated)

            r_reuse = complete_password_reset(
                uidb64=uidb64,
                token=token,
                new_password="AnotherStrong#Password992",
            )
            self.assertEqual(r_reuse.status, PasswordResetCompleteStatus.INVALID_LINK)

    def test_complete_reset_revokes_all_sessions_trusts_preserves_2fa_and_lock_and_notifies_admins_only_when_unregistered(self):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        admin1 = User.objects.create_user(
            username="pr_adm1",
            email="pr_adm1@example.com",
            password="pw",
            role=admin_role,
        )
        User.objects.create_user(
            username="pr_adm2_su",
            email="pr_adm2_su@example.com",
            password="pw",
            is_superuser=True,
        )
        User.objects.create_user(
            username="pr_adm_inactive",
            email="inactive_adm@example.com",
            password="pw",
            role=admin_role,
            is_active=False,
        )
        User.objects.create_user(
            username="pr_adm_nomail",
            email="",
            password="pw",
            role=admin_role,
        )

        # (A) 二段階認証【未登録】ユーザーでリセット完了
        sess1 = _create_user_session(self.user)
        sess2 = _create_user_session(self.user)
        other_sess = _create_user_session(self.other_user)
        fin = finalize_login_device(self.user, match_login_device(self.user))
        grant_device_trust(fin.device, days=30)

        now = timezone.now()
        AccountLock.objects.create(
            user=self.user,
            failure_count=4,
            last_failed_at=now,
            locked_until=now + timedelta(minutes=10),
        )

        uid_a = urlsafe_base64_encode(force_bytes(self.user.pk))
        tok_a = default_token_generator.make_token(self.user)

        save_calls = []
        orig_save = CustomUser.save

        def spy_save(instance, *args, **kwargs):
            save_calls.append((instance.pk, kwargs.get("update_fields")))
            return orig_save(instance, *args, **kwargs)

        with patch.object(CustomUser, "save", autospec=True, side_effect=spy_save):
            with self.captureOnCommitCallbacks(execute=True):
                res_a = complete_password_reset(
                    uidb64=uid_a,
                    token=tok_a,
                    new_password=VALID_NEW_PASSWORD,
                    ip_address="198.51.100.88",
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X) Chrome/130.0",
                )

        self.assertTrue(res_a.is_success)
        self.assertTrue(res_a.notified_admins)
        self.assertEqual(save_calls, [(self.user.pk, ["password"])])
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "太郎")
        self.assertTrue(self.user.check_password(VALID_NEW_PASSWORD))

        # 全セッション切断・他ユーザーセッション維持
        self.assertFalse(Session.objects.filter(session_key__in=[sess1, sess2]).exists())
        self.assertTrue(Session.objects.filter(session_key=other_sess).exists())

        # 端末の信頼のみ取り消し（LoginDevice レコード自体は消さない）
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)

        # アカウントロックおよび連続失敗回数は維持される
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 4)
        self.assertIsNotNone(lock.locked_until)

        # 操作ログ（操作者は対象ユーザー本人・パスワード等含まない）
        log = ActionLog.objects.get(action=ActionLogAction.PASSWORD_RESET_COMPLETE)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.object_id, str(self.user.pk))
        self.assertNotIn(VALID_NEW_PASSWORD, f"{log.data}{log.note}{log.object_repr}")
        self.assertNotIn(tok_a, f"{log.data}{log.note}{log.object_repr}")

        # 通知：本人宛 1通（リンクなし）＋ 管理者全員 BCC 1通（【要確認】付き・リンクなし）
        self.assertEqual(len(mail.outbox), 2)
        user_mail, admin_mail = mail.outbox[0], mail.outbox[1]
        self.assertEqual(user_mail.to, ["Reset_U1@example.com"])
        self.assertEqual(user_mail.subject, "【FreeGroup2】パスワードを再設定しました")
        self.assertNotIn("http", user_mail.body.lower())
        self.assertNotIn(VALID_NEW_PASSWORD, user_mail.body)
        self.assertIn("Chrome／macOS", user_mail.body)

        self.assertEqual(admin_mail.to, [])
        self.assertCountEqual(admin_mail.bcc, ["pr_adm1@example.com", "pr_adm2_su@example.com"])
        self.assertIn("【要確認】", admin_mail.subject)
        self.assertIn("reset_u1", admin_mail.body)
        self.assertNotIn("http", admin_mail.body.lower())

        # (B) 二段階認証【登録済み】ユーザーでリセット完了 → 2FA・リカバリーコード維持、管理者宛通知なし
        mail.outbox.clear()
        totp = TOTPDevice.objects.create(user=self.other_user, name="default", confirmed=True)
        codes = issue_recovery_codes(self.other_user)
        self.assertEqual(RecoveryCode.objects.filter(user=self.other_user).count(), len(codes))

        uid_b = urlsafe_base64_encode(force_bytes(self.other_user.pk))
        tok_b = default_token_generator.make_token(self.other_user)
        with self.captureOnCommitCallbacks(execute=True):
            res_b = complete_password_reset(
                uidb64=uid_b,
                token=tok_b,
                new_password=VALID_NEW_PASSWORD,
                ip_address="198.51.100.89",
            )
        self.assertTrue(res_b.is_success)
        self.assertFalse(res_b.notified_admins)
        self.assertTrue(TOTPDevice.objects.filter(pk=totp.pk, confirmed=True).exists())
        self.assertEqual(RecoveryCode.objects.filter(user=self.other_user).count(), len(codes))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["other@example.com"])

    def test_send_mail_safely_called_with_keyword_args_and_timeout_5_and_handles_failures_and_zero_admins(self):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        # (1) 全4通知が send_mail_safely をキーワード引数のみ＋timeout=5 で呼ぶこと
        admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        User.objects.create_user(
            username="kw_adm",
            email="kw_adm@example.com",
            password="pw",
            role=admin_role,
        )
        with patch("accounts.password_security.notifications.send_mail_safely", return_value=True) as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                notify_password_reset_requested(self.user, reset_url="https://fg2.example.local/r")
                notify_password_changed(self.user, ip_address="1.2.3.4")
                notify_password_reset_completed_to_user(self.user, ip_address="1.2.3.4")
                notify_password_reset_completed_to_admins(self.user, ip_address="1.2.3.4")

        self.assertEqual(mock_send.call_count, 4)
        for call in mock_send.call_args_list:
            self.assertEqual(call.args, ())
            self.assertEqual(call.kwargs.get("timeout"), 5)

        # (2) 管理者宛0名 → エラーログを出力し、リセット完了自体は成功＋ActionLog 記録あり
        CustomUser.objects.filter(role=admin_role).delete()
        CustomUser.objects.filter(is_superuser=True).delete()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        tok = default_token_generator.make_token(self.user)
        with self.assertLogs("accounts", level="ERROR") as log_ctx:
            with self.captureOnCommitCallbacks(execute=True):
                res_no_adm = complete_password_reset(
                    uidb64=uid,
                    token=tok,
                    new_password=VALID_NEW_PASSWORD,
                )
        self.assertTrue(res_no_adm.is_success)
        self.assertFalse(res_no_adm.notified_admins)
        self.assertTrue(ActionLog.objects.filter(action=ActionLogAction.PASSWORD_RESET_COMPLETE, user=self.user).exists())
        self.assertTrue(any("0 名" in msg for msg in log_ctx.output))
        self.assertFalse(any(VALID_NEW_PASSWORD in msg or tok in msg for msg in log_ctx.output))

        # (3) 送信失敗時（get_connection 例外で send_mail_safely が False を返しエラーログ出力）でも
        #     変更・リセット・申請は成立し例外が漏れない
        mail.outbox.clear()
        self.user.refresh_from_db()
        with patch("common.mail.get_connection", side_effect=OSError("smtp down")):
            with self.assertLogs("common.mail", level="ERROR") as mail_logs:
                with self.captureOnCommitCallbacks(execute=True):
                    req_out = request_password_reset(
                        "Reset_U1@example.com",
                        request=self._req(ip="198.51.100.70"),
                    )
                    chg_res = change_password(
                        self.user,
                        current_password=VALID_NEW_PASSWORD,
                        new_password="EvenStronger#Password777",
                        ip_address="198.51.100.70",
                    )
        self.assertEqual(req_out.sent_count, 1)
        self.assertEqual(req_out.entries[0].result, PasswordResetRequest.Result.SENT)
        self.assertTrue(chg_res.is_success)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("EvenStronger#Password777"))
        self.assertTrue(any("OSError" in m for m in mail_logs.output))
        self.assertFalse(any("EvenStronger#Password777" in m or "/accounts/password/reset/" in m for m in mail_logs.output))
