"""ログインセキュリティ Step 2 フェーズ D（端末記録・信頼済み端末）の単体テスト。"""

from datetime import timedelta
import hashlib
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts.login_security.devices import (
    DEFAULT_USER_AGENT_MAX_LENGTH,
    DEVICE_COOKIE_MAX_AGE_SECONDS,
    delete_user_device,
    finalize_login_device,
    generate_device_token,
    grant_device_trust,
    hash_device_token,
    list_user_devices,
    match_login_device,
    revoke_all_devices_trust,
    revoke_device_trust,
    revoke_user_device_trust,
    set_device_cookie,
)
from accounts.models import CustomUser, LoginDevice

User = get_user_model()


class DevicesTests(TestCase):
    """端末トークン・照合・確定・信頼・クッキー・一覧削除のテスト。"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user1 = User.objects.create_user(username="dev_u1", password="pw")
        self.user2 = User.objects.create_user(username="dev_u2", password="pw")

    def test_a_token_hash_and_generation(self):
        self.assertEqual(
            hash_device_token("sample-token"),
            hashlib.sha256(b"sample-token").hexdigest(),
        )
        self.assertEqual(hash_device_token("abc"), hash_device_token("abc"))
        t1 = generate_device_token()
        t2 = generate_device_token()
        self.assertNotEqual(t1, t2)
        self.assertGreaterEqual(len(t1), 32)

    def test_b_match_without_cookie_or_unknown_is_read_only(self):
        req_empty = self.factory.get("/")
        res1 = match_login_device(self.user1, request=req_empty)
        self.assertIsNone(res1.device)
        self.assertTrue(res1.is_new_device)
        self.assertFalse(res1.is_trusted)
        self.assertEqual(LoginDevice.objects.count(), 0)

        req_unknown = self.factory.get("/")
        req_unknown.COOKIES[settings.DEVICE_COOKIE_NAME] = "unknown-token"
        res2 = match_login_device(self.user1, request=req_unknown)
        self.assertIsNone(res2.device)
        self.assertTrue(res2.is_new_device)
        self.assertFalse(res2.is_trusted)
        self.assertEqual(LoginDevice.objects.count(), 0)

    def test_c_finalize_new_device_stores_hash_and_subsequent_match_is_known(self):
        base = timezone.now()
        req = self.factory.get("/")
        match1 = match_login_device(self.user1, request=req, now=base)
        self.assertTrue(match1.is_new_device)

        fin = finalize_login_device(
            self.user1,
            match1,
            ip_address="203.0.113.10",
            user_agent="Mozilla/5.0",
            now=base,
        )
        self.assertNotEqual(fin.device.token_hash, fin.plain_token)
        self.assertEqual(fin.device.token_hash, hash_device_token(fin.plain_token))
        self.assertEqual(fin.device.first_seen_at, base)
        self.assertEqual(fin.device.last_seen_at, base)

        req2 = self.factory.get("/")
        req2.COOKIES[settings.DEVICE_COOKIE_NAME] = fin.plain_token
        match2 = match_login_device(self.user1, request=req2, now=base + timedelta(minutes=5))
        self.assertFalse(match2.is_new_device)
        self.assertEqual(match2.device.pk, fin.device.pk)

    def test_d_finalize_known_device_updates_last_seen_but_keeps_first_seen(self):
        t1 = timezone.now()
        t2 = t1 + timedelta(hours=3)
        match1 = match_login_device(self.user1, plain_token=None, now=t1)
        fin1 = finalize_login_device(
            self.user1, match1, ip_address="203.0.113.1", user_agent="Chrome", now=t1
        )

        match2 = match_login_device(self.user1, plain_token=fin1.plain_token, now=t2)
        fin2 = finalize_login_device(
            self.user1, match2, ip_address="203.0.113.2", user_agent="Firefox", now=t2
        )
        self.assertEqual(fin2.device.pk, fin1.device.pk)
        self.assertEqual(fin2.plain_token, fin1.plain_token)
        self.assertEqual(fin2.device.first_seen_at, t1)
        self.assertEqual(fin2.device.last_seen_at, t2)
        self.assertEqual(fin2.device.last_ip, "203.0.113.2")
        self.assertEqual(fin2.device.user_agent, "Firefox")

    def test_e_other_users_cookie_treated_as_new_and_isolated(self):
        t1 = timezone.now()
        match_u1 = match_login_device(self.user1, plain_token=None, now=t1)
        fin_u1 = finalize_login_device(
            self.user1, match_u1, ip_address="203.0.113.10", user_agent="U1-UA", now=t1
        )

        # user1 のクッキーを持って user2 が照合・確定する
        req = self.factory.get("/")
        req.COOKIES[settings.DEVICE_COOKIE_NAME] = fin_u1.plain_token
        t2 = t1 + timedelta(minutes=10)
        match_u2 = match_login_device(self.user2, request=req, now=t2)
        self.assertTrue(match_u2.is_new_device)
        self.assertIsNone(match_u2.device)

        fin_u2 = finalize_login_device(
            self.user2, match_u2, ip_address="203.0.113.20", user_agent="U2-UA", now=t2
        )
        self.assertNotEqual(fin_u2.plain_token, fin_u1.plain_token)
        self.assertNotEqual(fin_u2.device.pk, fin_u1.device.pk)

        fin_u1.device.refresh_from_db()
        self.assertEqual(fin_u1.device.last_ip, "203.0.113.10")
        self.assertEqual(fin_u1.device.user_agent, "U1-UA")

    def test_f_grant_trust_expiration_and_zero_days(self):
        base = timezone.now()
        fin = finalize_login_device(
            self.user1, match_login_device(self.user1, now=base), now=base
        )
        # 0 日では付与されない
        self.assertFalse(grant_device_trust(fin.device, 0, now=base))
        match_zero = match_login_device(
            self.user1, plain_token=fin.plain_token, now=base
        )
        self.assertFalse(match_zero.is_trusted)

        # 30 日付与 → 期限内は信頼済み、30 日経過後は非信頼
        self.assertTrue(grant_device_trust(fin.device, 30, now=base))
        match_in = match_login_device(
            self.user1,
            plain_token=fin.plain_token,
            now=base + timedelta(days=29, hours=23),
        )
        self.assertTrue(match_in.is_trusted)

        match_expired = match_login_device(
            self.user1,
            plain_token=fin.plain_token,
            now=base + timedelta(days=30, seconds=1),
        )
        self.assertFalse(match_expired.is_trusted)

    def test_g_revoke_trust_single_user_and_all(self):
        base = timezone.now()
        d1a = finalize_login_device(
            self.user1, match_login_device(self.user1, now=base), now=base
        ).device
        d1b = finalize_login_device(
            self.user1, match_login_device(self.user1, now=base), now=base
        ).device
        d2 = finalize_login_device(
            self.user2, match_login_device(self.user2, now=base), now=base
        ).device
        for d in (d1a, d1b, d2):
            grant_device_trust(d, 30, now=base)

        # 1 件取り消し
        revoke_device_trust(d1a)
        d1a.refresh_from_db()
        d1b.refresh_from_db()
        self.assertIsNone(d1a.trusted_until)
        self.assertIsNotNone(d1b.trusted_until)

        # ユーザー単位一括取り消し（他ユーザーは影響なし）
        revoke_user_device_trust(self.user1)
        d1b.refresh_from_db()
        d2.refresh_from_db()
        self.assertIsNone(d1b.trusted_until)
        self.assertIsNotNone(d2.trusted_until)

        # 全ユーザー一括取り消し
        revoke_all_devices_trust()
        d2.refresh_from_db()
        self.assertIsNone(d2.trusted_until)

    def test_h_set_device_cookie_attributes_http_and_https(self):
        req_http = self.factory.get("/", secure=False)
        resp_http = HttpResponse()
        set_device_cookie(resp_http, req_http, "tok_http")
        cookie_http = resp_http.cookies[settings.DEVICE_COOKIE_NAME]
        self.assertFalse(cookie_http["secure"])
        self.assertTrue(cookie_http["httponly"])
        self.assertEqual(cookie_http["samesite"], "Lax")
        self.assertEqual(cookie_http["max-age"], DEVICE_COOKIE_MAX_AGE_SECONDS)
        self.assertEqual(cookie_http["path"], "/")

        req_https = self.factory.get("/", secure=True)
        resp_https = HttpResponse()
        set_device_cookie(resp_https, req_https, "tok_https")
        cookie_https = resp_https.cookies[settings.DEVICE_COOKIE_NAME]
        self.assertTrue(cookie_https["secure"])
        self.assertTrue(cookie_https["httponly"])
        self.assertEqual(cookie_https["samesite"], "Lax")
        self.assertEqual(cookie_https["max-age"], DEVICE_COOKIE_MAX_AGE_SECONDS)

    def test_i_delete_user_device_only_own(self):
        d1 = finalize_login_device(
            self.user1, match_login_device(self.user1)
        ).device
        d2 = finalize_login_device(
            self.user2, match_login_device(self.user2)
        ).device

        # 他人の端末 ID では削除されない
        self.assertFalse(delete_user_device(self.user1, d2.pk))
        self.assertTrue(LoginDevice.objects.filter(pk=d2.pk).exists())

        # 自分の端末は削除できる
        self.assertTrue(delete_user_device(self.user1, d1.pk))
        self.assertFalse(LoginDevice.objects.filter(pk=d1.pk).exists())

    def test_j_list_user_devices_ordered_and_trusted_flag(self):
        base = timezone.now()
        d_old = finalize_login_device(
            self.user1,
            match_login_device(self.user1, now=base - timedelta(hours=2)),
            now=base - timedelta(hours=2),
        ).device
        d_new = finalize_login_device(
            self.user1,
            match_login_device(self.user1, now=base),
            now=base,
        ).device
        grant_device_trust(d_new, 30, now=base)

        items = list_user_devices(self.user1, now=base)
        self.assertEqual([item.device.pk for item in items], [d_new.pk, d_old.pk])
        self.assertTrue(items[0].is_trusted)
        self.assertFalse(items[1].is_trusted)

    def test_k_finalize_retries_on_token_hash_collision(self):
        existing = finalize_login_device(
            self.user1, match_login_device(self.user1)
        )
        # 1 回目は既存と同じトークンを返し、2 回目は新しいトークンを返す
        with patch(
            "accounts.login_security.devices.generate_device_token",
            side_effect=[existing.plain_token, "unique-new-token"],
        ):
            fin = finalize_login_device(
                self.user2, match_login_device(self.user2)
            )
        self.assertEqual(fin.plain_token, "unique-new-token")
        self.assertEqual(fin.device.token_hash, hash_device_token("unique-new-token"))

    def test_l_user_agent_truncated_ip_none_and_no_user_save(self):
        long_ua = "X" * (DEFAULT_USER_AGENT_MAX_LENGTH + 100)
        with patch.object(CustomUser, "save", wraps=CustomUser.save) as mock_save:
            fin = finalize_login_device(
                self.user1,
                match_login_device(self.user1),
                ip_address=None,
                user_agent=long_ua,
            )
            grant_device_trust(fin.device, 30)
            revoke_device_trust(fin.device)
            self.assertEqual(mock_save.call_count, 0)

        self.assertEqual(len(fin.device.user_agent), DEFAULT_USER_AGENT_MAX_LENGTH)
        self.assertIsNone(fin.device.last_ip)

    def test_m_finalize_recreates_when_known_device_deleted_before_finalize(self):
        t1 = timezone.now()
        fin1 = finalize_login_device(
            self.user1, match_login_device(self.user1, now=t1), now=t1
        )
        match = match_login_device(self.user1, plain_token=fin1.plain_token, now=t1)
        self.assertFalse(match.is_new_device)

        # 順4の後・順8の前に別タブで端末記録が削除されたケース
        LoginDevice.objects.filter(pk=fin1.device.pk).delete()

        t2 = t1 + timedelta(seconds=10)
        fin2 = finalize_login_device(
            self.user1, match, ip_address="203.0.113.50", user_agent="Chrome", now=t2
        )
        self.assertNotEqual(fin2.device.pk, fin1.device.pk)
        self.assertNotEqual(fin2.plain_token, fin1.plain_token)
        self.assertEqual(fin2.device.last_ip, "203.0.113.50")
