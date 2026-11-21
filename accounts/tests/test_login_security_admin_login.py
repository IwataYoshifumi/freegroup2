"""ログインセキュリティ Step 3 フェーズ E（/admin/login/ 共通ログイン誘導）のテスト。"""

from urllib.parse import parse_qs, urlparse

from django.contrib.auth import SESSION_KEY
from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser


class AdminLoginRedirectTests(TestCase):
    """/admin/login/ の共通ログイン画面誘導と管理画面動線のテスト。"""

    def setUp(self):
        self.admin_login_url = reverse("admin:login")
        self.admin_index_url = reverse("admin:index")
        self.login_url = reverse("accounts:login")
        self.superuser = CustomUser.objects.create_superuser(
            username="admin_u",
            password="admin_pw",
            email="admin@example.com",
        )
        self.general_user = CustomUser.objects.create_user(
            username="general_u",
            password="general_pw",
            email="general@example.com",
        )

    def test_admin_login_get_and_head_redirect_with_safe_next(self):
        self.assertEqual(self.admin_login_url, "/admin/login/")

        resp = self.client.get(
            f"{self.admin_login_url}?next=/admin/accounts/customuser/"
        )
        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp.url)
        self.assertEqual(parsed.path, self.login_url)
        self.assertEqual(
            parse_qs(parsed.query).get("next"), ["/admin/accounts/customuser/"]
        )

        resp_head = self.client.head(
            f"{self.admin_login_url}?next=/admin/accounts/customuser/"
        )
        self.assertEqual(resp_head.status_code, 302)

    def test_anonymous_admin_access_redirects_via_admin_login_to_accounts_login(self):
        target = "/admin/accounts/customuser/"
        resp = self.client.get(target, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.redirect_chain), 2)
        self.assertIn(self.admin_login_url, resp.redirect_chain[0][0])
        final_url = resp.redirect_chain[1][0]
        parsed = urlparse(final_url)
        self.assertEqual(parsed.path, self.login_url)
        self.assertEqual(parse_qs(parsed.query).get("next"), [target])

    def test_unsafe_or_missing_next_falls_back_to_admin_index(self):
        bad_cases = [
            ("missing", ""),
            ("external_https", "https://evil.example/admin/"),
            ("protocol_relative", "//example.com/admin/"),
            ("javascript_scheme", "javascript:alert(1)"),
        ]
        for label, bad_next in bad_cases:
            with self.subTest(case=label):
                url = (
                    f"{self.admin_login_url}?next={bad_next}"
                    if bad_next
                    else self.admin_login_url
                )
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                parsed = urlparse(resp.url)
                self.assertEqual(parsed.path, self.login_url)
                self.assertEqual(
                    parse_qs(parsed.query).get("next"), [self.admin_index_url]
                )

    def test_admin_login_post_returns_405_and_does_not_establish_session(self):
        resp = self.client.post(
            self.admin_login_url,
            {"username": "admin_u", "password": "admin_pw"},
        )
        self.assertEqual(resp.status_code, 405)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_superuser_logs_in_via_accounts_login_and_reaches_admin_index(self):
        resp = self.client.post(
            self.login_url,
            {
                "username": "admin_u",
                "password": "admin_pw",
                "next": self.admin_index_url,
            },
            HTTP_X_REAL_IP="203.0.113.10",
            follow=True,
        )
        self.assertRedirects(resp, self.admin_index_url)
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_non_staff_user_does_not_loop_on_admin(self):
        self.client.force_login(self.general_user)
        resp = self.client.get(self.admin_index_url, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.redirect_chain), 2)
        self.assertIn(self.admin_login_url, resp.redirect_chain[0][0])
        self.assertEqual(urlparse(resp.redirect_chain[1][0]).path, self.login_url)

    def test_admin_logout_continues_to_work(self):
        self.client.force_login(self.superuser)
        self.assertIn(SESSION_KEY, self.client.session)

        resp = self.client.post(reverse("admin:logout"))
        self.assertIn(resp.status_code, (200, 302))
        self.assertNotIn(SESSION_KEY, self.client.session)
