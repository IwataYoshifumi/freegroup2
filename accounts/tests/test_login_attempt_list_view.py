"""ログインセキュリティ Step 4-3（ログイン試行記録一覧）のテスト。"""

from datetime import datetime, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, LoginAttempt

JST = ZoneInfo("Asia/Tokyo")


class LoginAttemptListViewTests(TestCase):
    """accounts:login_attempt_list のテスト。"""

    def setUp(self):
        self.url = reverse("accounts:login_attempt_list")
        self.login_url = reverse("accounts:login")
        self.user_admin_group = Group.objects.get(name="user_admin")

        self.viewer = CustomUser.objects.create_user(
            username="att_viewer",
            password="pw",
            email="att_viewer@example.com",
        )
        self.viewer.groups.add(self.user_admin_group)

        self.plain = CustomUser.objects.create_user(
            username="att_plain",
            password="pw",
            email="att_plain@example.com",
        )
        self.taro = CustomUser.objects.create_user(username="taro", password="pw")

    def test_permissions_anonymous_403_and_view_loginattempt(self):
        resp_anon = self.client.get(self.url)
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn(self.login_url, resp_anon.url)

        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        self.client.force_login(self.viewer)
        resp_ok = self.client.get(self.url)
        self.assertEqual(resp_ok.status_code, 200)
        self.assertIn("no-store", resp_ok.headers.get("Cache-Control", ""))
        html = resp_ok.content.decode("utf-8")
        self.assertIn(
            "利用者が欄を取り違えて入力したパスワードが残っている場合があります",
            html,
        )
        main_match = re.search(r"<main class=\"app-main\">(.*?)</main>", html, re.DOTALL)
        self.assertIsNotNone(main_match)
        main_html = main_match.group(1)
        self.assertNotIn("編集", main_html)
        self.assertNotIn("削除", main_html)
        self.assertNotIn('method="post"', main_html.lower())

    def test_ordering_newest_first_and_pagination(self):
        base = timezone.now()
        for i in range(25):
            LoginAttempt.objects.create(
                created_at=base - timedelta(minutes=25 - i),
                username_input=f"user_{i:02d}",
                user=self.taro,
                ip_address="203.0.113.10",
                user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0",
                result=LoginAttempt.Result.SUCCESS,
                second_factor=LoginAttempt.SecondFactor.TOTP,
                is_new_device=(i == 24),
            )

        self.client.force_login(self.viewer)
        r_p1 = self.client.get(self.url)
        self.assertEqual(r_p1.status_code, 200)
        self.assertEqual(len(r_p1.context["attempt_rows"]), 20)
        # 最新 user_24 が先頭
        self.assertEqual(r_p1.context["attempt_rows"][0]["username_input"], "user_24")
        self.assertEqual(r_p1.context["attempt_rows"][0]["browser_summary"], "Chrome／Windows")

        r_p2 = self.client.get(self.url, {"page": "2"})
        self.assertEqual(r_p2.status_code, 200)
        self.assertEqual(len(r_p2.context["attempt_rows"]), 5)
        self.assertEqual(r_p2.context["attempt_rows"][-1]["username_input"], "user_00")

    def test_filters_and_jst_date_boundaries_and_invalid_inputs_no_500(self):
        # 2026-11-10 23:59:59 JST と 2026-11-11 00:00:00 JST の境界を含めて作成
        dt_in = datetime(2026, 11, 10, 23, 59, 59, tzinfo=JST)
        dt_after = datetime(2026, 11, 11, 0, 0, 0, tzinfo=JST)
        dt_before = datetime(2026, 11, 9, 23, 59, 59, tzinfo=JST)

        a_match = LoginAttempt.objects.create(
            created_at=dt_in,
            username_input="Taro_Admin",
            user=self.taro,
            ip_address="203.0.113.50",
            result=LoginAttempt.Result.PASSWORD_FAILED,
        )
        LoginAttempt.objects.create(
            created_at=dt_after,
            username_input="Taro_Admin",
            user=self.taro,
            ip_address="203.0.113.50",
            result=LoginAttempt.Result.PASSWORD_FAILED,
        )
        LoginAttempt.objects.create(
            created_at=dt_before,
            username_input="Taro_Admin",
            user=self.taro,
            ip_address="203.0.113.50",
            result=LoginAttempt.Result.PASSWORD_FAILED,
        )
        LoginAttempt.objects.create(
            created_at=dt_in,
            username_input="hanako",
            ip_address="198.51.100.1",
            result=LoginAttempt.Result.SUCCESS,
            second_factor=LoginAttempt.SecondFactor.NONE,
        )

        self.client.force_login(self.viewer)

        # 複合絞り込み（結果・部分一致大小非区別・IP・期間（終了日当日 JST を含む））
        resp = self.client.get(
            self.url,
            {
                "result": "password_failed",
                "username": "taro_",
                "ip": "203.0.113.50",
                "date_from": "2026-11-10",
                "date_to": "2026-11-10",
            },
        )
        self.assertEqual(resp.status_code, 200)
        rows = resp.context["attempt_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username_input"], a_match.username_input)
        self.assertEqual(rows[0]["created_at"].strftime("%Y-%m-%d %H:%M:%S"), "2026-11-10 23:59:59")

        # 不正な IP は絞り込みなしにせずエラー表示＋0件
        r_bad_ip = self.client.get(self.url, {"ip": "not-an-ip"})
        self.assertEqual(r_bad_ip.status_code, 200)
        self.assertEqual(len(r_bad_ip.context["attempt_rows"]), 0)
        self.assertContains(r_bad_ip, "IP アドレスの形式が不正です")

        # 不正な日付・不正な結果も 500 にならずエラー表示＋0件
        for key, bad_val in (
            ("date_from", "2026-99-99"),
            ("date_to", "invalid-date"),
            ("result", "unknown_result"),
        ):
            with self.subTest(param=key):
                r_bad = self.client.get(self.url, {key: bad_val})
                self.assertEqual(r_bad.status_code, 200)
                self.assertEqual(len(r_bad.context["attempt_rows"]), 0)

    def test_query_count_constant_regardless_of_row_count_and_no_inline_styles(self):
        base = timezone.now()
        for i in range(2):
            u = CustomUser.objects.create_user(username=f"q_u_{i}", password="pw")
            LoginAttempt.objects.create(
                created_at=base - timedelta(seconds=i),
                username_input=u.username,
                user=u,
                result=LoginAttempt.Result.SUCCESS,
                second_factor=LoginAttempt.SecondFactor.NONE,
            )

        self.client.force_login(self.viewer)
        with CaptureQueriesContext(connection) as ctx_2:
            r1 = self.client.get(self.url)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(len(r1.context["attempt_rows"]), 2)

        for i in range(2, 15):
            u = CustomUser.objects.create_user(username=f"q_u_{i}", password="pw")
            LoginAttempt.objects.create(
                created_at=base - timedelta(seconds=i),
                username_input=u.username,
                user=u,
                result=LoginAttempt.Result.SUCCESS,
                second_factor=LoginAttempt.SecondFactor.NONE,
            )

        with CaptureQueriesContext(connection) as ctx_15:
            r2 = self.client.get(self.url)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.context["attempt_rows"]), 15)
        self.assertEqual(len(ctx_2), len(ctx_15))

        template_src = (
            Path(settings.BASE_DIR) / "templates" / "accounts" / "login_attempt_list.html"
        ).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'style="[^"]*"', template_src), [])
