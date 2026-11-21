"""ログインセキュリティ Step 4-4（既存画面への追加・サイドバー導線・共通ヘルパー）のテスト。"""

from base64 import b32encode
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE
from accounts.login_security.devices import hash_device_token
from accounts.login_security.recovery_codes import issue_recovery_codes
from accounts.login_security.user_lookup import get_security_setting
from accounts.models import AccountLock, CustomUser, LoginAttempt, LoginDevice, Role, SecuritySetting
from accounts.security_status import count_active_admins


def _grant_perms(user: CustomUser, *codenames: str) -> None:
    perms = list(
        Permission.objects.filter(
            content_type__app_label="accounts",
            codename__in=codenames,
        )
    )
    if len(perms) != len(codenames):
        found = {p.codename for p in perms}
        missing = sorted(set(codenames) - found)
        raise AssertionError(f"Missing permissions: {missing}")
    user.user_permissions.add(*perms)


class ProfileSecurityAdditionsTests(TestCase):
    """プロフィール画面（accounts:profile）の二段階認証・最近のログイン表示のテスト。"""

    def setUp(self):
        self.url = reverse("accounts:profile")
        self.two_factor_url = reverse("accounts:two_factor")
        self.user = CustomUser.objects.create_user(
            username="prof_user",
            password="pw",
            email="prof_user@example.com",
        )
        self.other = CustomUser.objects.create_user(
            username="prof_other",
            password="pw",
            email="prof_other@example.com",
        )

    def test_two_factor_status_and_required_badge_and_link(self):
        self.client.force_login(self.user)

        # 未登録・任意
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "二段階認証")
        self.assertContains(resp, "未登録")
        self.assertContains(resp, self.two_factor_url)
        self.assertNotContains(resp, ">必須<")

        # 未確定デバイスのみ → 引き続き「未登録」
        unconfirmed = TOTPDevice.objects.create(
            user=self.user, name="unconfirmed", confirmed=False
        )
        resp_unconf = self.client.get(self.url)
        self.assertEqual(resp_unconf.status_code, 200)
        self.assertContains(resp_unconf, "未登録")
        self.assertNotContains(resp_unconf, "登録済み")

        # 全員必須に設定 + 未登録 → ミドルウェアにより setup へリダイレクトされる
        setting = get_security_setting()
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        setting.save()
        resp_req_unreg = self.client.get(self.url)
        self.assertEqual(resp_req_unreg.status_code, 302)
        self.assertIn(reverse("accounts:two_factor_setup"), resp_req_unreg.url)

        # 確定デバイス作成 → 「登録済み」かつ「必須」バッジ表示
        unconfirmed.confirmed = True
        unconfirmed.save(update_fields=["confirmed"])
        resp_enrolled = self.client.get(self.url)
        self.assertEqual(resp_enrolled.status_code, 200)
        self.assertContains(resp_enrolled, "登録済み")
        self.assertContains(resp_enrolled, "必須")

    def test_recent_logins_empty_message(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "最近のログイン")
        self.assertContains(resp, "ログインの記録はありません")

    def test_recent_logins_filters_own_success_top_5_newest_first(self):
        now = timezone.now()

        # 他人の SUCCESS（表示されてはいけない）
        LoginAttempt.objects.create(
            user=self.other,
            username_input=self.other.username,
            ip_address="203.0.113.99",
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/125.0",
            result=LoginAttempt.Result.SUCCESS,
            second_factor=LoginAttempt.SecondFactor.TOTP,
            created_at=now,
        )

        # 本人の失敗・ロック拒否（表示されてはいけない）
        for idx, res in enumerate(
            [
                LoginAttempt.Result.PASSWORD_FAILED,
                LoginAttempt.Result.CODE_FAILED,
                LoginAttempt.Result.REJECTED_LOCKED,
                LoginAttempt.Result.REJECTED_IP_BLOCKED,
            ],
            start=1,
        ):
            LoginAttempt.objects.create(
                user=self.user,
                username_input=self.user.username,
                ip_address=f"198.51.100.{idx}",
                user_agent="Mozilla/5.0",
                result=res,
                second_factor="",
                created_at=now - timedelta(seconds=idx),
            )

        # 本人の SUCCESS を 7 件（新しい 5 件だけ表示、古い 2 件は除外）
        for idx in range(7):
            LoginAttempt.objects.create(
                user=self.user,
                username_input=self.user.username,
                ip_address=f"192.0.2.{10 + idx}",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0",
                result=LoginAttempt.Result.SUCCESS,
                second_factor=(
                    LoginAttempt.SecondFactor.TRUSTED_DEVICE
                    if idx == 0
                    else LoginAttempt.SecondFactor.TOTP
                ),
                is_new_device=(idx == 1),
                created_at=now - timedelta(minutes=idx),
            )

        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "ログインの記録はありません")
        self.assertNotContains(resp, "203.0.113.99")
        self.assertNotContains(resp, "198.51.100.")

        # 新しい 5 件 (192.0.2.10 .. 192.0.2.14) は表示、6件目以降 (.15, .16) は非表示
        for idx in range(5):
            self.assertContains(resp, f"192.0.2.{10 + idx}")
        self.assertNotContains(resp, "192.0.2.15")
        self.assertNotContains(resp, "192.0.2.16")

        # 並び順は新しい順 (.10 が .14 より前)
        html = resp.content.decode("utf-8")
        self.assertLess(html.index("192.0.2.10"), html.index("192.0.2.14"))
        self.assertContains(resp, "省略（信頼済み端末）")
        self.assertContains(resp, "Chrome")
        self.assertContains(resp, "Windows")

    def test_recent_logins_query_count_constant_1_vs_30(self):
        self.client.force_login(self.user)
        now = timezone.now()

        LoginAttempt.objects.create(
            user=self.user,
            username_input=self.user.username,
            ip_address="192.0.2.1",
            user_agent="Mozilla/5.0",
            result=LoginAttempt.Result.SUCCESS,
            second_factor=LoginAttempt.SecondFactor.NONE,
            created_at=now,
        )
        with CaptureQueriesContext(connection) as ctx_one:
            resp_one = self.client.get(self.url)
        self.assertEqual(resp_one.status_code, 200)

        LoginAttempt.objects.bulk_create(
            [
                LoginAttempt(
                    user=self.user,
                    username_input=self.user.username,
                    ip_address=f"192.0.2.{i + 2}",
                    user_agent="Mozilla/5.0",
                    result=LoginAttempt.Result.SUCCESS,
                    second_factor=LoginAttempt.SecondFactor.TOTP,
                    created_at=now - timedelta(minutes=i + 1),
                )
                for i in range(29)
            ]
        )
        with CaptureQueriesContext(connection) as ctx_many:
            resp_many = self.client.get(self.url)
        self.assertEqual(resp_many.status_code, 200)
        self.assertEqual(len(ctx_many.captured_queries), len(ctx_one.captured_queries))


class UserListSecurityAdditionsTests(TestCase):
    """ユーザー一覧画面（accounts:user_list）のバッジ・クエリ数・管理者数警告のテスト。"""

    def setUp(self):
        self.url = reverse("accounts:user_list")
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.viewer = CustomUser.objects.create_user(
            username="ul_viewer",
            password="pw",
            email="ul_viewer@example.com",
        )
        _grant_perms(self.viewer, "retire_user")

    def test_security_badges_totp_lock_and_no_email(self):
        now = timezone.now()

        # 1) 確定TOTPあり + メールあり + ロックなし
        u_enrolled = CustomUser.objects.create_user(
            username="u_enrolled",
            password="pw",
            email="enrolled@example.com",
        )
        TOTPDevice.objects.create(user=u_enrolled, name="main", confirmed=True)

        # 2) 未確定TOTPのみ + アクティブロックあり + メールなし
        u_locked_no_mail = CustomUser.objects.create_user(
            username="u_locked_nomail",
            password="pw",
            email="",
        )
        TOTPDevice.objects.create(user=u_locked_no_mail, name="pending", confirmed=False)
        AccountLock.objects.create(
            user=u_locked_no_mail,
            failure_count=5,
            locked_until=now + timedelta(minutes=10),
        )

        # 3) 期限切れロック（ロック中バッジが出ないこと）
        u_expired_lock = CustomUser.objects.create_user(
            username="u_expired",
            password="pw",
            email="expired@example.com",
        )
        AccountLock.objects.create(
            user=u_expired_lock,
            failure_count=5,
            locked_until=now - timedelta(minutes=1),
        )

        self.client.force_login(self.viewer)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        # ヘッダー列「セキュリティ」および既存列が存在すること
        for col in ("ユーザ名", "氏名", "ロール", "部署", "認証", "状態", "セキュリティ", "操作"):
            self.assertIn(col, html)

        # 各行を切り出して検証
        row_enrolled = self._extract_row(html, "u_enrolled")
        self.assertIn("登録済み", row_enrolled)
        self.assertNotIn("未登録", row_enrolled)
        self.assertNotIn("ロック中", row_enrolled)
        self.assertNotIn("通知先なし", row_enrolled)

        row_locked = self._extract_row(html, "u_locked_nomail")
        self.assertIn("未登録", row_locked)
        self.assertNotIn("登録済み", row_locked)
        self.assertIn("ロック中", row_locked)
        self.assertIn("通知先なし", row_locked)

        row_expired = self._extract_row(html, "u_expired")
        self.assertIn("未登録", row_expired)
        self.assertNotIn("ロック中", row_expired)
        self.assertNotIn("通知先なし", row_expired)

    def _extract_row(self, html: str, username: str) -> str:
        idx = html.index(f">{username}</a>")
        tr_start = html.rindex("<tr>", 0, idx)
        tr_end = html.index("</tr>", idx) + len("</tr>")
        return html[tr_start:tr_end]

    def test_query_count_constant_2_vs_12_users(self):
        self.client.force_login(self.viewer)
        now = timezone.now()

        # 初期状態（viewer + extra 1名 = 2名）
        CustomUser.objects.create_user(
            username="u_base_1", password="pw", email="base1@example.com"
        )
        with CaptureQueriesContext(connection) as ctx_few:
            resp_few = self.client.get(self.url)
        self.assertEqual(resp_few.status_code, 200)

        # 追加で 10 名作成（TOTP・ロック・メール有無混在）
        for i in range(10):
            u = CustomUser.objects.create_user(
                username=f"u_extra_{i:02d}",
                password="pw",
                email="" if i % 3 == 0 else f"extra{i}@example.com",
            )
            if i % 2 == 0:
                TOTPDevice.objects.create(user=u, name="main", confirmed=True)
            if i % 4 == 0:
                AccountLock.objects.create(
                    user=u,
                    failure_count=5,
                    locked_until=now + timedelta(minutes=15),
                )

        with CaptureQueriesContext(connection) as ctx_many:
            resp_many = self.client.get(self.url)
        self.assertEqual(resp_many.status_code, 200)
        self.assertEqual(len(ctx_many.captured_queries), len(ctx_few.captured_queries))

    def test_admin_count_warning_threshold_and_shared_helper(self):
        # 管理者 0 名 → 警告あり
        self.assertEqual(count_active_admins(), 0)
        self.client.force_login(self.viewer)
        resp_zero = self.client.get(self.url)
        self.assertContains(resp_zero, "管理者が0名です")

        # 有効な管理者 1 名（スーパーユーザー）→ 警告あり
        su = CustomUser.objects.create_superuser(
            username="ul_su", password="pw", email="ul_su@example.com"
        )
        self.assertEqual(count_active_admins(), 1)
        resp_one = self.client.get(self.url)
        self.assertContains(
            resp_one,
            "管理者が1名です。二段階認証の端末を紛失すると復旧できなくなるため、2名以上を推奨します",
        )

        # 有効な管理者 2 名目（admin ロール）→ 警告消滅
        admin2 = CustomUser.objects.create_user(
            username="ul_admin2",
            password="pw",
            email="ul_admin2@example.com",
            role=self.admin_role,
        )
        self.assertEqual(count_active_admins(), 2)
        resp_two = self.client.get(self.url)
        self.assertNotContains(resp_two, "2名以上を推奨します")

        # 2 名目を退職（is_active=False）にすると再び 1 名となり警告再表示
        admin2.is_active = False
        admin2.save(update_fields=["is_active"])
        self.assertEqual(count_active_admins(), 1)
        resp_retired = self.client.get(self.url)
        self.assertContains(resp_retired, "管理者が1名です")

        # 同一ヘルパーを SecuritySettingView と共有している確認
        _grant_perms(su, "manage_security_setting")
        self.client.force_login(su)
        resp_sec = self.client.get(reverse("accounts:security_setting"))
        self.assertEqual(resp_sec.status_code, 200)
        self.assertEqual(resp_sec.context["admin_count"], count_active_admins())
        self.assertTrue(resp_sec.context["admin_count_warning"])


class UserDetailSecurityAdditionsTests(TestCase):
    """ユーザー詳細画面（accounts:user_detail）の状態表示・操作ボタン制御のテスト。"""

    def setUp(self):
        self.viewer_only = CustomUser.objects.create_user(
            username="ud_viewer",
            password="pw",
            email="ud_viewer@example.com",
        )
        _grant_perms(self.viewer_only, "retire_user")

        self.reset_admin = CustomUser.objects.create_user(
            username="ud_reset_admin",
            password="pw",
            email="ud_reset_admin@example.com",
        )
        _grant_perms(self.reset_admin, "retire_user", "reset_two_factor")

        self.target = CustomUser.objects.create_user(
            username="ud_target",
            password="pw",
            email="ud_target@example.com",
        )

    def _detail_url(self, user_id: int) -> str:
        return reverse("accounts:user_detail", kwargs={"user_id": user_id})

    def test_two_factor_and_lock_status_display_with_jst_time(self):
        # 2026-11-21 15:30 JST = 2026-11-21 06:30 UTC
        locked_until = datetime(2026, 11, 21, 6, 30, tzinfo=ZoneInfo("UTC"))
        with self.settings(USE_TZ=True, TIME_ZONE="Asia/Tokyo"):
            # 未登録・ロックなし
            self.client.force_login(self.viewer_only)
            resp_plain = self.client.get(self._detail_url(self.target.pk))
            self.assertEqual(resp_plain.status_code, 200)
            self.assertContains(resp_plain, "二段階認証")
            self.assertContains(resp_plain, "未登録")
            self.assertContains(resp_plain, "ロック状態")
            self.assertContains(resp_plain, "なし")

            # 未確定デバイスのみ → まだ「未登録」
            unconf = TOTPDevice.objects.create(
                user=self.target, name="unconf", confirmed=False
            )
            resp_unconf = self.client.get(self._detail_url(self.target.pk))
            self.assertContains(resp_unconf, "未登録")
            self.assertNotContains(resp_unconf, "登録済み")

            # 確定デバイス + 未来のロック
            unconf.confirmed = True
            unconf.save(update_fields=["confirmed"])
            now = timezone.now()
            future_lock = (
                locked_until
                if locked_until > now
                else now + timedelta(hours=2)
            )
            expected_jst = timezone.localtime(
                future_lock, ZoneInfo("Asia/Tokyo")
            ).strftime("%Y-%m-%d %H:%M")
            AccountLock.objects.create(
                user=self.target,
                failure_count=5,
                locked_until=future_lock,
            )

            resp_locked = self.client.get(self._detail_url(self.target.pk))
            self.assertEqual(resp_locked.status_code, 200)
            self.assertContains(resp_locked, "登録済み")
            self.assertContains(resp_locked, f"ロック中（{expected_jst} まで）")

    def test_reset_and_unlock_buttons_permission_and_self_and_lock_gating(self):
        future_lock = timezone.now() + timedelta(minutes=20)
        lock = AccountLock.objects.create(
            user=self.target,
            failure_count=5,
            locked_until=future_lock,
        )
        reset_url = reverse("accounts:two_factor_reset", kwargs={"user_id": self.target.pk})
        unlock_url = reverse("accounts:account_unlock", kwargs={"user_id": self.target.pk})

        # 1) reset_two_factor なしの閲覧者 → ボタンも URL も出ない
        self.client.force_login(self.viewer_only)
        resp_no_perm = self.client.get(self._detail_url(self.target.pk))
        self.assertEqual(resp_no_perm.status_code, 200)
        self.assertNotContains(resp_no_perm, "二段階認証をリセット")
        self.assertNotContains(resp_no_perm, "ロックを解除")
        self.assertNotContains(resp_no_perm, reset_url)
        self.assertNotContains(resp_no_perm, unlock_url)

        # 2) reset_two_factor 保持者 + 他人（ロック中）→ 両方表示（unlock は POST フォーム）
        self.client.force_login(self.reset_admin)
        resp_with_perm = self.client.get(self._detail_url(self.target.pk))
        self.assertEqual(resp_with_perm.status_code, 200)
        self.assertContains(resp_with_perm, "二段階認証をリセット")
        self.assertContains(resp_with_perm, reset_url)
        self.assertContains(resp_with_perm, "ロックを解除")
        self.assertContains(resp_with_perm, f'action="{unlock_url}"')
        self.assertContains(resp_with_perm, 'method="post"')

        # 3) 対象がロック中でない（期限切れ含む）とき → リセットは出るが解除ボタンは非表示
        lock.locked_until = timezone.now() - timedelta(minutes=1)
        lock.save(update_fields=["locked_until"])
        resp_unlocked = self.client.get(self._detail_url(self.target.pk))
        self.assertContains(resp_unlocked, "二段階認証をリセット")
        self.assertNotContains(resp_unlocked, "ロックを解除")
        self.assertNotContains(resp_unlocked, unlock_url)

        # 4) 自分自身の詳細では「二段階認証をリセット」ボタンを出さない
        self_reset_url = reverse(
            "accounts:two_factor_reset", kwargs={"user_id": self.reset_admin.pk}
        )
        resp_self = self.client.get(self._detail_url(self.reset_admin.pk))
        self.assertEqual(resp_self.status_code, 200)
        self.assertNotContains(resp_self, "二段階認証をリセット")
        self.assertNotContains(resp_self, self_reset_url)


class SidebarSecurityNavigationTests(TestCase):
    """アカウント管理サイドバー（_sidebar.html）の権限制御と順序のテスト。"""

    def setUp(self):
        self.user_list_url = reverse("accounts:user_list")
        self.security_url = reverse("accounts:security_setting")
        self.attempts_url = reverse("accounts:login_attempt_list")

    def test_sidebar_items_permission_gating_and_order(self):
        # 1) retire_user のみ → 両方非表示
        u_view_only = CustomUser.objects.create_user(
            username="sb_view", password="pw", email="sb_view@example.com"
        )
        _grant_perms(u_view_only, "retire_user")
        self.client.force_login(u_view_only)
        resp_view = self.client.get(self.user_list_url)
        self.assertEqual(resp_view.status_code, 200)
        self.assertNotContains(resp_view, self.security_url)
        self.assertNotContains(resp_view, "セキュリティ設定")
        self.assertNotContains(resp_view, self.attempts_url)
        self.assertNotContains(resp_view, "ログイン試行記録")

        # 2) manage_security_setting のみ追加 → セキュリティ設定のみ表示
        u_sec = CustomUser.objects.create_user(
            username="sb_sec", password="pw", email="sb_sec@example.com"
        )
        _grant_perms(u_sec, "retire_user", "manage_security_setting")
        self.client.force_login(u_sec)
        resp_sec = self.client.get(self.user_list_url)
        self.assertContains(resp_sec, self.security_url)
        self.assertContains(resp_sec, "セキュリティ設定")
        self.assertNotContains(resp_sec, self.attempts_url)
        self.assertNotContains(resp_sec, "ログイン試行記録")

        # 3) view_loginattempt のみ追加 → ログイン試行記録のみ表示
        u_att = CustomUser.objects.create_user(
            username="sb_att", password="pw", email="sb_att@example.com"
        )
        _grant_perms(u_att, "retire_user", "view_loginattempt")
        self.client.force_login(u_att)
        resp_att = self.client.get(self.user_list_url)
        self.assertNotContains(resp_att, self.security_url)
        self.assertNotContains(resp_att, "セキュリティ設定")
        self.assertContains(resp_att, self.attempts_url)
        self.assertContains(resp_att, "ログイン試行記録")

        # 4) スーパーユーザー → 既存4項目の後に2項目が並ぶ
        su = CustomUser.objects.create_superuser(
            username="sb_su", password="pw", email="sb_su@example.com"
        )
        self.client.force_login(su)
        resp_su = self.client.get(self.user_list_url)
        html = resp_su.content.decode("utf-8")
        expected_order = [
            "ユーザ一覧",
            "ロール一覧",
            "グループ一覧",
            "パーミッション一覧",
            "セキュリティ設定",
            "ログイン試行記録",
        ]
        positions = [html.index(label) for label in expected_order]
        self.assertEqual(positions, sorted(positions))


class SecurityTemplateHygieneTests(TestCase):
    """プロフィール・一覧・詳細の HTML に秘密鍵・端末トークン・リカバリーコードが漏れないこと。"""

    def test_no_secrets_leaked_in_html(self):
        su = CustomUser.objects.create_superuser(
            username="hyg_su", password="pw", email="hyg_su@example.com"
        )
        device = TOTPDevice.objects.create(user=su, name="main", confirmed=True)
        b32_secret = b32encode(device.bin_key).decode("ascii").rstrip("=")
        raw_token = "raw-trusted-device-token-secret-123456"
        token_hash = hash_device_token(raw_token)
        LoginDevice.objects.create(
            user=su,
            token_hash=token_hash,
            user_agent="My Laptop",
            trusted_until=timezone.now() + timedelta(days=30),
        )
        plain_codes = issue_recovery_codes(su)

        self.client.force_login(su)
        urls = [
            reverse("accounts:profile"),
            reverse("accounts:user_list"),
            reverse("accounts:user_detail", kwargs={"user_id": su.pk}),
        ]
        for url in urls:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                html = resp.content.decode("utf-8")
                self.assertNotIn(device.key, html)
                self.assertNotIn(b32_secret, html)
                self.assertNotIn(raw_token, html)
                self.assertNotIn(token_hash, html)
                for code in plain_codes:
                    self.assertNotIn(code, html)
