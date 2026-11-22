"""パスワード変更・リセット Step 4（変更画面・完了画面・プロフィール導線・管理画面対応・強制誘導）の結合テスト。"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, get_user_model, views as auth_views
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.constants import ActionLogAction, AuthSource
from accounts.models import (
    AccountLock,
    CustomUser,
    LoginAttempt,
    SecuritySetting,
)
from accounts.views_password import PasswordChangeDoneView, PasswordChangeView
from actionlogs.models import ActionLog

User = get_user_model()

VALID_NEW_PASSWORD = "Step4Strong#Password901"


def _create_user_session(user) -> str:
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store.set_expiry(timedelta(days=7))
    store.save()
    return store.session_key


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    CLIENT_IP_SOURCE="x_real_ip",
)
class PasswordChangeViewIntegrationTests(TestCase):
    """パスワード変更画面・完了画面・検証順・ロック・強制誘導・保存フィールドの結合テスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.change_url = reverse("accounts:password_change")
        self.done_url = reverse("accounts:password_change_done")
        self.login_url = reverse("accounts:login")
        self.profile_url = reverse("accounts:profile")

        self.setting, _ = SecuritySetting.objects.get_or_create(id=1)
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.OPTIONAL
        self.setting.lockout_threshold = 3
        self.setting.save()

        self.user = User.objects.create_user(
            username="step4_u1",
            email="step4_u1@example.com",
            password="Current#Password123",
            first_name="次郎",
            last_name="高橋",
            auth_source=AuthSource.LOCAL,
            ldap_dn=None,
        )
        self.other_user = User.objects.create_user(
            username="step4_other",
            email="step4_other@example.com",
            password="Other#Password123",
        )

    def test_views_inherit_django_standard_password_change_views(self):
        self.assertTrue(issubclass(PasswordChangeView, auth_views.PasswordChangeView))
        self.assertTrue(issubclass(PasswordChangeDoneView, auth_views.PasswordChangeDoneView))

    def test_anonymous_redirects_to_login_on_change_and_done(self):
        for url in (self.change_url, self.done_url):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertTrue(resp.url.startswith(f"{self.login_url}?next="))

    def test_change_success_keeps_session_on_done_and_subsequent_pages_revokes_others_notifies_and_logs(self):
        self.assertTrue(
            self.client.login(username="step4_u1", password="Current#Password123")
        )
        old_session_key = self.client.session.session_key
        other_own_session = _create_user_session(self.user)
        third_party_session = _create_user_session(self.other_user)

        save_calls = []
        orig_save = CustomUser.save

        def spy_save(instance, *args, **kwargs):
            save_calls.append((instance.pk, kwargs.get("update_fields")))
            return orig_save(instance, *args, **kwargs)

        with patch.object(CustomUser, "save", autospec=True, side_effect=spy_save):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(
                    self.change_url,
                    {
                        "old_password": "Current#Password123",
                        "new_password1": VALID_NEW_PASSWORD,
                        "new_password2": VALID_NEW_PASSWORD,
                    },
                    HTTP_X_REAL_IP="203.0.113.45",
                    HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0.0.0",
                )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url.split("?")[0], self.done_url)

        # 完了画面を開いてもログアウトされておらず、続けて別の画面（プロフィール・ホーム）を開ける
        done_resp = self.client.get(resp.url)
        self.assertEqual(done_resp.status_code, 200)
        self.assertContains(done_resp, "パスワードを変更しました")

        profile_resp = self.client.get(self.profile_url)
        self.assertEqual(profile_resp.status_code, 200)
        self.assertContains(profile_resp, "step4_u1")

        # 操作中以外の本人セッションは切断され、他ユーザーのセッションは残存
        new_session_key = self.client.session.session_key
        self.assertNotEqual(new_session_key, old_session_key)
        self.assertTrue(Session.objects.filter(session_key=new_session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=other_own_session).exists())
        self.assertTrue(Session.objects.filter(session_key=third_party_session).exists())

        # 保存は update_fields=["password"] のみ・他フィールド不変・新パスワードでログイン可能
        self.assertEqual(save_calls, [(self.user.pk, ["password"])])
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "次郎")
        self.assertEqual(self.user.email, "step4_u1@example.com")

        fresh_client = Client()
        self.assertTrue(
            fresh_client.login(username="step4_u1", password=VALID_NEW_PASSWORD)
        )

        # 操作ログ・完了通知
        self.assertTrue(
            ActionLog.objects.filter(
                user=self.user,
                action=ActionLogAction.PASSWORD_CHANGE,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["step4_u1@example.com"])
        self.assertEqual(mail.outbox[0].subject, "【FreeGroup2】パスワードを変更しました")

    def test_validation_order_and_failure_counting(self):
        self.client.force_login(self.user)

        # (1) 2回一致しない（現在正しい）→ 拒否され、失敗には数えない
        resp_mismatch = self.client.post(
            self.change_url,
            {
                "old_password": "Current#Password123",
                "new_password1": VALID_NEW_PASSWORD,
                "new_password2": "Different#Password999",
            },
            HTTP_X_REAL_IP="203.0.113.50",
        )
        self.assertEqual(resp_mismatch.status_code, 200)
        self.assertContains(resp_mismatch, "一致しません")
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())
        self.assertEqual(LoginAttempt.objects.filter(user=self.user).count(), 0)
        self.assertNotContains(resp_mismatch, VALID_NEW_PASSWORD)

        # (2) 強度不足（現在正しい）→ 拒否され、失敗には数えない
        resp_weak = self.client.post(
            self.change_url,
            {
                "old_password": "Current#Password123",
                "new_password1": "123",
                "new_password2": "123",
            },
            HTTP_X_REAL_IP="203.0.113.50",
        )
        self.assertEqual(resp_weak.status_code, 200)
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())
        self.assertEqual(LoginAttempt.objects.filter(user=self.user).count(), 0)

        # (3) 現在と同じ（現在正しい）→ 拒否され、失敗には数えない
        resp_same = self.client.post(
            self.change_url,
            {
                "old_password": "Current#Password123",
                "new_password1": "Current#Password123",
                "new_password2": "Current#Password123",
            },
            HTTP_X_REAL_IP="203.0.113.50",
        )
        self.assertEqual(resp_same.status_code, 200)
        self.assertContains(resp_same, "現在のパスワードと異なるもの")
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())

        # (4) 現在のパスワードが空 → 必須チェックで止めずに失敗加算（パスワード失敗）
        resp_empty_old = self.client.post(
            self.change_url,
            {
                "old_password": "",
                "new_password1": "123",
                "new_password2": "456",
            },
            HTTP_X_REAL_IP="203.0.113.50",
        )
        self.assertEqual(resp_empty_old.status_code, 200)
        self.assertContains(resp_empty_old, "現在のパスワードが正しくありません。")
        self.assertNotContains(resp_empty_old, "一致しません")
        self.assertNotContains(resp_empty_old, "8 文字")
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)

        # (5) 現在のパスワードが誤りで、新パスワード2回も不一致 → 失敗加算され不一致・強度は出ない
        resp_wrong_mismatch = self.client.post(
            self.change_url,
            {
                "old_password": "WrongOldPassword",
                "new_password1": "123",
                "new_password2": "abc",
            },
            HTTP_X_REAL_IP="203.0.113.50",
        )
        self.assertEqual(resp_wrong_mismatch.status_code, 200)
        self.assertContains(resp_wrong_mismatch, "現在のパスワードが正しくありません。")
        self.assertNotContains(resp_wrong_mismatch, "一致しません")
        self.assertNotContains(resp_wrong_mismatch, "8 文字")
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 2)
        self.assertEqual(
            LoginAttempt.objects.filter(
                user=self.user,
                result=LoginAttempt.Result.PASSWORD_FAILED,
            ).count(),
            2,
        )

    def test_lockout_on_threshold_rejects_correct_password_and_keeps_session(self):
        self.assertTrue(
            self.client.login(username="step4_u1", password="Current#Password123")
        )
        sess_key = self.client.session.session_key

        with self.captureOnCommitCallbacks(execute=True):
            for _ in range(3):
                self.client.post(
                    self.change_url,
                    {
                        "old_password": "WrongPassword!",
                        "new_password1": VALID_NEW_PASSWORD,
                        "new_password2": VALID_NEW_PASSWORD,
                    },
                    HTTP_X_REAL_IP="203.0.113.60",
                )

        # ロック中でもログイン済みセッションは切れない
        self.assertTrue(Session.objects.filter(session_key=sess_key).exists())
        self.assertEqual(self.client.get(self.profile_url).status_code, 200)

        # ロック中は現在のパスワードが正しくても拒否される
        resp_locked = self.client.post(
            self.change_url,
            {
                "old_password": "Current#Password123",
                "new_password1": VALID_NEW_PASSWORD,
                "new_password2": VALID_NEW_PASSWORD,
            },
            HTTP_X_REAL_IP="203.0.113.60",
        )
        self.assertEqual(resp_locked.status_code, 200)
        self.assertContains(
            resp_locked,
            "現在この操作は行えません。しばらく待ってから再度お試しください。",
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Current#Password123"))

    def test_not_eligible_three_conditions_show_guidance_only_and_reject_direct_post(self):
        cases = [
            User.objects.create_user(
                username="s4_ldap_src",
                password="Current#Password123",
                auth_source=AuthSource.LDAP,
            ),
            User.objects.create_user(
                username="s4_ldap_dn",
                password="Current#Password123",
                auth_source=AuthSource.LOCAL,
                ldap_dn="CN=s4_ldap_dn,DC=example,DC=local",
            ),
            User.objects.create_user(
                username="s4_unusable",
                password=None,
                auth_source=AuthSource.LOCAL,
            ),
        ]
        for u in cases:
            with self.subTest(user=u.username):
                self.client.force_login(u)
                get_resp = self.client.get(self.change_url)
                self.assertEqual(get_resp.status_code, 200)
                self.assertContains(
                    get_resp,
                    "このアカウントでは、FreeGroup2 でパスワードを変更できません。",
                )
                self.assertNotContains(get_resp, "LDAP")
                self.assertNotContains(get_resp, 'name="old_password"')

                post_resp = self.client.post(
                    self.change_url,
                    {
                        "old_password": "Current#Password123",
                        "new_password1": VALID_NEW_PASSWORD,
                        "new_password2": VALID_NEW_PASSWORD,
                    },
                )
                self.assertEqual(post_resp.status_code, 403)
                u.refresh_from_db()
                self.assertFalse(u.check_password(VALID_NEW_PASSWORD))

    def test_two_factor_enforcement_redirects_to_setup_before_change_and_done(self):
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()

        self.client.force_login(self.user)
        setup_url = reverse("accounts:two_factor_setup")
        for url in (self.change_url, self.done_url):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertRedirects(resp, setup_url)


class ProfilePasswordLinkTests(TestCase):
    """プロフィール画面のパスワード変更導線テスト。"""

    def test_profile_always_shows_password_change_link(self):
        profile_url = reverse("accounts:profile")
        change_url = reverse("accounts:password_change")

        local_u = User.objects.create_user(username="prof_local", password="pw")
        ldap_u = User.objects.create_user(
            username="prof_ldap",
            password=None,
            auth_source=AuthSource.LDAP,
        )

        for u in (local_u, ldap_u):
            with self.subTest(user=u.username):
                self.client.force_login(u)
                resp = self.client.get(profile_url)
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, "パスワードを変更")
                self.assertContains(resp, f'href="{change_url}')


class AdminPasswordChangeRestrictionTests(TestCase):
    """管理画面のパスワード変更統合とユーザー別画面閉鎖のテスト（§12 / §13-5）。"""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="s4_admin",
            email="s4_admin@example.com",
            password="Admin#Password123",
        )
        self.target = User.objects.create_user(
            username="s4_target",
            email="s4_target@example.com",
            password="Target#Password123",
            auth_source=AuthSource.LOCAL,
        )

    def test_admin_password_change_and_done_redirect_to_common_views(self):
        self.client.force_login(self.superuser)

        r_chg = self.client.get(reverse("admin:password_change"))
        self.assertRedirects(r_chg, reverse("accounts:password_change"), fetch_redirect_response=False)

        r_done = self.client.get(reverse("admin:password_change_done"))
        self.assertRedirects(r_done, reverse("accounts:password_change_done"), fetch_redirect_response=False)

        # POST は許可しない（標準の変更処理を通さない）
        r_post = self.client.post(
            reverse("admin:password_change"),
            {
                "old_password": "Admin#Password123",
                "new_password1": VALID_NEW_PASSWORD,
                "new_password2": VALID_NEW_PASSWORD,
            },
        )
        self.assertEqual(r_post.status_code, 405)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.check_password("Admin#Password123"))

    def test_user_password_change_route_is_blocked_and_link_hidden_while_add_initial_password_works(self):
        self.client.force_login(self.superuser)

        # ユーザー編集画面に <id>/password/ リンクもパスワード変更案内も出ない
        user_change_url = reverse("admin:accounts_customuser_change", args=[self.target.pk])
        change_page = self.client.get(user_change_url)
        self.assertEqual(change_page.status_code, 200)
        self.assertNotContains(change_page, f"/admin/accounts/customuser/{self.target.pk}/password/")
        self.assertNotContains(change_page, "../password/")
        self.assertNotContains(change_page, "id_password")
        # 認証元の編集欄は引き続き維持されている
        self.assertContains(change_page, 'name="auth_source"')

        # <id>/password/ は GET・POST とも開けない（404）かつパスワードは変わらない
        per_user_pw_url = f"/admin/accounts/customuser/{self.target.pk}/password/"
        self.assertEqual(self.client.get(per_user_pw_url).status_code, 404)
        post_resp = self.client.post(
            per_user_pw_url,
            {
                "password1": VALID_NEW_PASSWORD,
                "password2": VALID_NEW_PASSWORD,
                "usable_password": "true",
            },
        )
        self.assertEqual(post_resp.status_code, 404)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password("Target#Password123"))

        # ユーザー追加画面では初期パスワード欄がそのまま使え、ログイン可能なユーザーを作成できる
        add_url = reverse("admin:accounts_customuser_add")
        add_page = self.client.get(add_url)
        self.assertEqual(add_page.status_code, 200)
        self.assertContains(add_page, 'name="password1"')
        self.assertContains(add_page, 'name="password2"')
        self.assertContains(add_page, 'name="usable_password"')

        create_resp = self.client.post(
            add_url,
            {
                "username": "created_from_admin",
                "usable_password": "true",
                "password1": "InitialAdmin#Pass99",
                "password2": "InitialAdmin#Pass99",
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        created = User.objects.get(username="created_from_admin")
        self.assertTrue(created.check_password("InitialAdmin#Pass99"))
