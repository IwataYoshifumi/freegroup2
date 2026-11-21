"""ログインセキュリティ Step 4-3（管理者による二段階認証リセット・ロック解除）のテスト。"""

from base64 import b32encode
from datetime import timedelta
from pathlib import Path
import re

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE, ActionLogAction
from accounts.login_security.devices import (
    finalize_login_device,
    grant_device_trust,
    match_login_device,
)
from accounts.login_security.recovery_codes import issue_recovery_codes
from accounts.models import AccountLock, CustomUser, RecoveryCode, Role
from actionlogs.models import ActionLog


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class TwoFactorResetAndUnlockViewTests(TestCase):
    """accounts:two_factor_reset と accounts:account_unlock の結合テスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.user_admin_group = Group.objects.get(name="user_admin")

        self.admin_operator = CustomUser.objects.create_user(
            username="rst_admin1",
            password="pw",
            email="rst_admin1@example.com",
            role=self.admin_role,
        )
        self.admin_operator.groups.add(self.user_admin_group)

        self.other_admin = CustomUser.objects.create_user(
            username="rst_admin2",
            password="pw",
            email="rst_admin2@example.com",
            is_superuser=True,
        )

        self.target = CustomUser.objects.create_user(
            username="rst_target",
            password="target-raw-password",
            email="rst_target@example.com",
            first_name="花子",
            last_name="鈴木",
        )
        self.plain_user = CustomUser.objects.create_user(
            username="rst_plain",
            password="pw",
            email="rst_plain@example.com",
        )

    def _reset_url(self, user_id: int) -> str:
        return reverse("accounts:two_factor_reset", kwargs={"user_id": user_id})

    def _unlock_url(self, user_id: int) -> str:
        return reverse("accounts:account_unlock", kwargs={"user_id": user_id})

    def _setup_target_state(self, user: CustomUser):
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
        codes = issue_recovery_codes(user)
        fin = finalize_login_device(user, match_login_device(user))
        grant_device_trust(fin.device, 30)
        AccountLock.objects.create(
            user=user,
            failure_count=5,
            last_failed_at=timezone.now(),
            locked_until=timezone.now() + timedelta(minutes=15),
        )
        return device, codes, fin

    def test_reset_permissions_404_and_self_rejection(self):
        self._setup_target_state(self.admin_operator)
        self._setup_target_state(self.target)

        # 未ログイン
        resp_anon = self.client.get(self._reset_url(self.target.pk))
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp_anon.url)

        # 権限なし → 403
        self.client.force_login(self.plain_user)
        self.assertEqual(
            self.client.get(self._reset_url(self.target.pk)).status_code, 403
        )
        self.assertEqual(
            self.client.post(self._reset_url(self.target.pk)).status_code, 403
        )

        # 存在しないユーザー → 404
        self.client.force_login(self.admin_operator)
        self.assertEqual(self.client.get(self._reset_url(999999)).status_code, 404)
        self.assertEqual(self.client.post(self._reset_url(999999)).status_code, 404)

        # 自分自身 → GET / POST とも 400 で拒否・設定変更なし
        r_self_get = self.client.get(self._reset_url(self.admin_operator.pk))
        self.assertEqual(r_self_get.status_code, 400)
        self.assertContains(
            r_self_get,
            "ご自身の二段階認証は、二段階認証の設定画面から再登録してください",
            status_code=400,
        )
        self.assertNotIn("リセットする", r_self_get.content.decode("utf-8"))

        r_self_post = self.client.post(self._reset_url(self.admin_operator.pk))
        self.assertEqual(r_self_post.status_code, 400)
        self.assertTrue(
            TOTPDevice.objects.filter(user=self.admin_operator, confirmed=True).exists()
        )
        self.assertEqual(ActionLog.objects.count(), 0)

    def test_reset_confirm_screen_notices_and_success_for_general_and_admin_target(
        self,
    ):
        # 管理者が別の管理者も対象にできることを含めて確認
        for label, victim in (("general", self.target), ("admin", self.other_admin)):
            with self.subTest(target_type=label):
                TOTPDevice.objects.filter(user=victim).delete()
                RecoveryCode.objects.filter(user=victim).delete()
                AccountLock.objects.filter(user=victim).delete()
                ActionLog.objects.all().delete()
                mail.outbox.clear()

                device, codes, fin = self._setup_target_state(victim)
                secret_b32 = b32encode(device.bin_key).decode("ascii")
                original_password_hash = victim.password

                victim_client = Client()
                victim_client.force_login(victim)
                victim_sess_key = victim_client.session.session_key

                other_client = Client()
                other_client.force_login(self.plain_user)
                other_sess_key = other_client.session.session_key

                self.client.force_login(self.admin_operator)

                # GET 確認画面に 2 つの必須注意が表示される
                resp_get = self.client.get(self._reset_url(victim.pk))
                self.assertEqual(resp_get.status_code, 200)
                self.assertIn("no-store", resp_get.headers.get("Cache-Control", ""))
                get_html = resp_get.content.decode("utf-8")
                self.assertIn("本人確認は、FG2 の外（電話・対面）で行ってください", get_html)
                self.assertIn(
                    "本人がすぐに登録操作を行える状態で実行してください。実行後は、本人に速やかに登録を完了してもらってください",
                    get_html,
                )

                # POST でリセット実行（長文理由メモでも ActionLog.note は TextField で上限なし）
                long_reason = "電話で本人確認済み。" + ("あ" * 400)
                with self.captureOnCommitCallbacks(execute=True):
                    resp_post = self.client.post(
                        self._reset_url(victim.pk),
                        {"reason_note": long_reason},
                    )
                self.assertRedirects(
                    resp_post,
                    reverse("accounts:user_detail", kwargs={"user_id": victim.pk}),
                    fetch_redirect_response=False,
                )

                self.assertFalse(TOTPDevice.objects.filter(user=victim).exists())
                self.assertFalse(RecoveryCode.objects.filter(user=victim).exists())
                fin.device.refresh_from_db()
                self.assertIsNone(fin.device.trusted_until)
                lock = AccountLock.objects.get(user=victim)
                self.assertEqual(lock.failure_count, 0)
                self.assertIsNone(lock.locked_until)

                victim.refresh_from_db()
                self.assertEqual(victim.password, original_password_hash)

                # 対象ユーザーの全セッション切断、他ユーザーは残存
                self.assertFalse(
                    Session.objects.filter(session_key=victim_sess_key).exists()
                )
                self.assertTrue(
                    Session.objects.filter(session_key=other_sess_key).exists()
                )

                # メール（本人宛＋管理者全員 BCC 1 通）
                self.assertEqual(len(mail.outbox), 2)
                user_mails = [m for m in mail.outbox if m.to == [victim.email]]
                admin_mails = [m for m in mail.outbox if m.to == ["noreply@example.com"]]
                self.assertEqual(len(user_mails), 1)
                self.assertEqual(len(admin_mails), 1)
                self.assertNotIn(long_reason, user_mails[0].body)
                self.assertIn(long_reason, admin_mails[0].body)
                self.assertNotIn(secret_b32, admin_mails[0].body)
                for rc in codes:
                    self.assertNotIn(rc, user_mails[0].body)
                    self.assertNotIn(rc, admin_mails[0].body)

                # 操作ログ（操作者・対象・理由メモ付き）
                log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_RESET)
                self.assertEqual(log.user, self.admin_operator)
                self.assertEqual(log.object_id, str(victim.pk))
                self.assertEqual(log.note, long_reason)
                self.assertNotIn(secret_b32, f"{log.object_repr} {log.data}")

    def test_unlock_permissions_methods_404_and_behavior(self):
        # 権限なし → 403
        self.client.force_login(self.plain_user)
        self.assertEqual(
            self.client.post(self._unlock_url(self.target.pk)).status_code, 403
        )

        self.client.force_login(self.admin_operator)

        # GET → 405
        self.assertEqual(
            self.client.get(self._unlock_url(self.target.pk)).status_code, 405
        )

        # 存在しないユーザー → 404
        self.assertEqual(self.client.post(self._unlock_url(999999)).status_code, 404)

        # ロックされていないユーザーに実行しても 500 にならず、操作ログも増えない
        resp_not_locked = self.client.post(self._unlock_url(self.target.pk))
        self.assertRedirects(
            resp_not_locked,
            reverse("accounts:user_detail", kwargs={"user_id": self.target.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(ActionLog.objects.count(), 0)

        # ロック中のユーザーを解除 → ロック解除・失敗回数 0・操作ログ 1 件
        AccountLock.objects.create(
            user=self.target,
            failure_count=5,
            last_failed_at=timezone.now(),
            locked_until=timezone.now() + timedelta(minutes=15),
        )
        resp_unlock = self.client.post(self._unlock_url(self.target.pk))
        self.assertRedirects(
            resp_unlock,
            reverse("accounts:user_detail", kwargs={"user_id": self.target.pk}),
            fetch_redirect_response=False,
        )
        lock = AccountLock.objects.get(user=self.target)
        self.assertEqual(lock.failure_count, 0)
        self.assertIsNone(lock.locked_until)

        log = ActionLog.objects.get(action=ActionLogAction.ACCOUNT_UNLOCK)
        self.assertEqual(log.user, self.admin_operator)
        self.assertEqual(log.object_id, str(self.target.pk))

    def test_reset_template_has_no_inline_styles(self):
        template_src = (
            Path(settings.BASE_DIR) / "templates" / "accounts" / "two_factor_reset.html"
        ).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'style="[^"]*"', template_src), [])

    def test_reset_reason_note_max_length_500_allowed_and_501_rejected(self):
        device, codes, fin = self._setup_target_state(self.target)
        target_client = Client()
        target_client.force_login(self.target)
        target_sess_key = target_client.session.session_key

        self.client.force_login(self.admin_operator)

        # 確認画面の入力欄に maxlength="500" が付いている
        resp_get = self.client.get(self._reset_url(self.target.pk))
        self.assertIn('maxlength="500"', resp_get.content.decode("utf-8"))

        # 501 文字 → 400 でリセット不実行・状態不変
        too_long_reason = "あ" * 501
        with self.captureOnCommitCallbacks(execute=True):
            resp_501 = self.client.post(
                self._reset_url(self.target.pk),
                {"reason_note": too_long_reason},
            )
        self.assertEqual(resp_501.status_code, 400)
        self.assertContains(
            resp_501,
            "理由メモは500文字以内で入力してください。",
            status_code=400,
        )
        self.assertTrue(
            TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists()
        )
        self.assertEqual(RecoveryCode.objects.filter(user=self.target).count(), 10)
        self.assertTrue(Session.objects.filter(session_key=target_sess_key).exists())
        lock = AccountLock.objects.get(user=self.target)
        self.assertEqual(lock.failure_count, 5)
        self.assertIsNotNone(lock.locked_until)
        self.assertEqual(ActionLog.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

        # 500 文字 → 実行成功
        exact_500_reason = "あ" * 500
        with self.captureOnCommitCallbacks(execute=True):
            resp_500 = self.client.post(
                self._reset_url(self.target.pk),
                {"reason_note": exact_500_reason},
            )
        self.assertRedirects(
            resp_500,
            reverse("accounts:user_detail", kwargs={"user_id": self.target.pk}),
            fetch_redirect_response=False,
        )
        self.assertFalse(TOTPDevice.objects.filter(user=self.target).exists())
        self.assertFalse(Session.objects.filter(session_key=target_sess_key).exists())
        log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_RESET)
        self.assertEqual(log.note, exact_500_reason)
