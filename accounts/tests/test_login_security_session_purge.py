"""ログインセキュリティ Step 2 フェーズ F-1（セッション切断・古いデータの削除）の単体テスト。"""

from datetime import timedelta

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.login_security.purge import purge_old_login_data
from accounts.login_security.session_revoke import revoke_user_sessions
from accounts.models import LoginAttempt, LoginDevice

User = get_user_model()


def _create_user_session(user, *, expire_delta=timedelta(days=7)) -> str:
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store.set_expiry(expire_delta)
    store.save()
    return store.session_key


class SessionRevokeTests(TestCase):
    """session_revoke.revoke_user_sessions のテスト（観点 m〜o）。"""

    def setUp(self):
        self.user1 = User.objects.create_user(username="sess_u1", password="pw")
        self.user2 = User.objects.create_user(username="sess_u2", password="pw")

    def test_m_revokes_all_target_user_sessions_and_keeps_others(self):
        k1a = _create_user_session(self.user1)
        k1b = _create_user_session(self.user1)
        k2 = _create_user_session(self.user2)

        deleted = revoke_user_sessions(self.user1)
        self.assertEqual(deleted, 2)
        self.assertFalse(Session.objects.filter(session_key__in=[k1a, k1b]).exists())
        self.assertTrue(Session.objects.filter(session_key=k2).exists())

    def test_n_keeps_current_session_key_when_specified(self):
        k_keep = _create_user_session(self.user1)
        k_other = _create_user_session(self.user1)
        k2 = _create_user_session(self.user2)

        deleted = revoke_user_sessions(self.user1, keep_session_key=k_keep)
        self.assertEqual(deleted, 1)
        self.assertTrue(Session.objects.filter(session_key=k_keep).exists())
        self.assertFalse(Session.objects.filter(session_key=k_other).exists())
        self.assertTrue(Session.objects.filter(session_key=k2).exists())

    def test_o_skips_expired_and_corrupted_sessions_without_error(self):
        now = timezone.now()
        k_valid = _create_user_session(self.user1)
        # 期限切れセッション
        Session.objects.create(
            session_key="expired_key",
            session_data="corrupted_or_old",
            expire_date=now - timedelta(minutes=1),
        )
        # 復号できない壊れたセッション（有効期限内）
        Session.objects.create(
            session_key="corrupted_key",
            session_data="!!!not-base64-session-data!!!",
            expire_date=now + timedelta(days=1),
        )

        deleted = revoke_user_sessions(self.user1, now=now)
        self.assertEqual(deleted, 1)
        self.assertFalse(Session.objects.filter(session_key=k_valid).exists())
        self.assertTrue(Session.objects.filter(session_key="corrupted_key").exists())


class PurgeOldLoginDataTests(TestCase):
    """purge.purge_old_login_data のテスト（観点 p〜s）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="purge_u", password="pw")

    def test_p_purge_login_attempts_180_days_boundary(self):
        now = timezone.now()
        # 180日 + 1秒前 → 削除対象
        old = LoginAttempt.objects.create(
            username_input="old",
            result=LoginAttempt.Result.SUCCESS,
            created_at=now - timedelta(days=180, seconds=1),
        )
        # ちょうど180日前 → 残る
        exact = LoginAttempt.objects.create(
            username_input="exact",
            result=LoginAttempt.Result.SUCCESS,
            created_at=now - timedelta(days=180),
        )
        # 179日前 → 残る
        recent = LoginAttempt.objects.create(
            username_input="recent",
            result=LoginAttempt.Result.SUCCESS,
            created_at=now - timedelta(days=179),
        )

        res = purge_old_login_data(now=now)
        self.assertEqual(res.deleted_attempts, 1)
        self.assertFalse(LoginAttempt.objects.filter(pk=old.pk).exists())
        self.assertTrue(LoginAttempt.objects.filter(pk=exact.pk).exists())
        self.assertTrue(LoginAttempt.objects.filter(pk=recent.pk).exists())

    def test_q_purge_login_devices_365_days_boundary(self):
        now = timezone.now()
        old = LoginDevice.objects.create(
            user=self.user,
            token_hash="h_old",
            last_seen_at=now - timedelta(days=365, seconds=1),
        )
        exact = LoginDevice.objects.create(
            user=self.user,
            token_hash="h_exact",
            last_seen_at=now - timedelta(days=365),
        )

        res = purge_old_login_data(now=now)
        self.assertEqual(res.deleted_devices, 1)
        self.assertFalse(LoginDevice.objects.filter(pk=old.pk).exists())
        self.assertTrue(LoginDevice.objects.filter(pk=exact.pk).exists())

    def test_r_purge_unconfirmed_totp_24_hours_keeps_confirmed(self):
        now = timezone.now()
        old_unconfirmed = TOTPDevice.objects.create(
            user=self.user, name="old_unconf", confirmed=False
        )
        TOTPDevice.objects.filter(pk=old_unconfirmed.pk).update(
            created_at=now - timedelta(hours=24, seconds=1)
        )

        recent_unconfirmed = TOTPDevice.objects.create(
            user=self.user, name="recent_unconf", confirmed=False
        )
        TOTPDevice.objects.filter(pk=recent_unconfirmed.pk).update(
            created_at=now - timedelta(hours=23)
        )

        old_confirmed = TOTPDevice.objects.create(
            user=self.user, name="old_conf", confirmed=True
        )
        TOTPDevice.objects.filter(pk=old_confirmed.pk).update(
            created_at=now - timedelta(days=30)
        )

        res = purge_old_login_data(now=now)
        self.assertEqual(res.deleted_unconfirmed_totp, 1)
        self.assertFalse(TOTPDevice.objects.filter(pk=old_unconfirmed.pk).exists())
        self.assertTrue(TOTPDevice.objects.filter(pk=recent_unconfirmed.pk).exists())
        self.assertTrue(TOTPDevice.objects.filter(pk=old_confirmed.pk).exists())

    def test_s_purge_when_empty_returns_zeros(self):
        res = purge_old_login_data()
        self.assertEqual(res.deleted_attempts, 0)
        self.assertEqual(res.deleted_devices, 0)
        self.assertEqual(res.deleted_unconfirmed_totp, 0)
