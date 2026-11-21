"""ログインセキュリティ Step 4-1 管理コマンド（reset_two_factor / unlock_login / purge_login_attempts）の単体テスト。"""

from base64 import b32encode
from datetime import timedelta
import io
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE, ActionLogAction
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
    LoginDevice,
    RecoveryCode,
    Role,
)
from accounts.services import apply_role
from actionlogs.models import ActionLog

User = get_user_model()


def _create_user_session(user) -> str:
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store.set_expiry(timedelta(days=7))
    store.save()
    return store.session_key


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class ResetTwoFactorCommandTests(TestCase):
    """reset_two_factor 管理コマンドの検証（仕様書 §6.7 / §6.8 / §12.1 / §12.2）。"""

    def setUp(self):
        mail.outbox.clear()
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.sales_role = Role.objects.get(code="sales")

        self.admin1 = User.objects.create_user(
            username="cmd_admin1",
            password="pw",
            email="cmd_admin1@example.com",
        )
        apply_role(self.admin1, self.admin_role)
        self.admin1 = User.objects.get(pk=self.admin1.pk)

        self.admin2 = User.objects.create_user(
            username="cmd_admin2",
            password="pw",
            email="cmd_admin2@example.com",
            is_superuser=True,
        )

        self.target = User.objects.create_user(
            username="cmd_target",
            password="target-raw-password",
            email="cmd_target@example.com",
            first_name="太郎",
            last_name="山田",
        )
        apply_role(self.target, self.sales_role)
        self.target = User.objects.get(pk=self.target.pk)

        self.other = User.objects.create_user(
            username="cmd_other",
            password="pw",
            email="cmd_other@example.com",
        )

    def _setup_two_factor_state(self, user):
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
        plain_codes = issue_recovery_codes(user)
        fin = finalize_login_device(user, match_login_device(user))
        grant_device_trust(fin.device, 30)
        AccountLock.objects.create(
            user=user,
            failure_count=5,
            last_failed_at=timezone.now(),
            locked_until=timezone.now() + timedelta(minutes=15),
        )
        return device, plain_codes, fin

    def test_reset_with_yes_deletes_2fa_unlocks_revokes_sessions_keeps_password_logs_and_notifies(
        self,
    ):
        device, plain_codes, fin = self._setup_two_factor_state(self.target)
        secret_b32 = b32encode(device.bin_key).decode("ascii")
        original_password_hash = self.target.password

        target_sess_1 = _create_user_session(self.target)
        target_sess_2 = _create_user_session(self.target)
        other_sess = _create_user_session(self.other)

        stdout = io.StringIO()
        with (
            patch.object(CustomUser, "save", wraps=CustomUser.save) as mock_user_save,
            patch("builtins.input") as mock_input,
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command(
                "reset_two_factor",
                "cmd_target",
                "--reason",
                "端末紛失のため電話確認の上リセット",
                "--yes",
                stdout=stdout,
            )

        self.assertEqual(mock_user_save.call_count, 0)
        mock_input.assert_not_called()

        # 登録・リカバリーコード・端末信頼・ロックの解除
        self.assertFalse(TOTPDevice.objects.filter(user=self.target).exists())
        self.assertFalse(RecoveryCode.objects.filter(user=self.target).exists())
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)
        lock = AccountLock.objects.get(user=self.target)
        self.assertEqual(lock.failure_count, 0)
        self.assertIsNone(lock.locked_until)

        # セッション切断（対象のみ全切断・他ユーザー残存）
        self.assertFalse(
            Session.objects.filter(session_key__in=[target_sess_1, target_sess_2]).exists()
        )
        self.assertTrue(Session.objects.filter(session_key=other_sess).exists())

        # パスワード不変
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, original_password_hash)
        self.assertTrue(self.target.check_password("target-raw-password"))

        # 操作ログ（操作者なし・対象ユーザー・理由メモ付き）
        log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_RESET)
        self.assertIsNone(log.user)
        self.assertEqual(log.object_id, str(self.target.pk))
        self.assertEqual(log.object_repr, "cmd_target")
        self.assertEqual(log.note, "端末紛失のため電話確認の上リセット")

        # 本人宛 + 管理者全員宛（BCC 1通）の通知
        self.assertEqual(len(mail.outbox), 2)
        user_mails = [m for m in mail.outbox if m.to == ["cmd_target@example.com"]]
        admin_mails = [m for m in mail.outbox if m.to == ["noreply@example.com"]]
        self.assertEqual(len(user_mails), 1)
        self.assertEqual(len(admin_mails), 1)
        self.assertCountEqual(
            admin_mails[0].bcc,
            ["cmd_admin1@example.com", "cmd_admin2@example.com"],
        )

        # 標準出力と操作ログに秘密鍵・リカバリーコード・端末トークンが含まれない
        output_text = stdout.getvalue()
        log_dump = f"{log.object_repr} {log.note} {log.data}"
        for secret_text in (secret_b32, device.key, fin.plain_token, *plain_codes):
            self.assertNotIn(secret_text, output_text)
            self.assertNotIn(secret_text, log_dump)

    def test_reset_interactive_confirm_yes_executes_and_displays_target_info(self):
        self._setup_two_factor_state(self.target)
        stdout = io.StringIO()

        with (
            patch("builtins.input", return_value="はい") as mock_input,
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command("reset_two_factor", "cmd_target", stdout=stdout)

        mock_input.assert_called_once()
        output_text = stdout.getvalue()
        self.assertIn("cmd_target", output_text)
        self.assertIn("太郎 山田", output_text)
        self.assertIn("cmd_target@example.com", output_text)
        self.assertIn("管理者: いいえ", output_text)
        self.assertFalse(TOTPDevice.objects.filter(user=self.target).exists())

    def test_reset_interactive_confirm_non_yes_aborts_without_changes(self):
        self._setup_two_factor_state(self.target)
        sess_key = _create_user_session(self.target)

        for answer in ("いいえ", "yes", "y", ""):
            with self.subTest(answer=answer):
                stdout = io.StringIO()
                with (
                    patch("builtins.input", return_value=answer),
                    self.captureOnCommitCallbacks(execute=True),
                ):
                    with self.assertRaises(CommandError):
                        call_command("reset_two_factor", "cmd_target", stdout=stdout)

                self.assertTrue(
                    TOTPDevice.objects.filter(user=self.target, confirmed=True).exists()
                )
                self.assertEqual(RecoveryCode.objects.filter(user=self.target).count(), 10)
                self.assertTrue(Session.objects.filter(session_key=sess_key).exists())
                self.assertEqual(ActionLog.objects.count(), 0)
                self.assertEqual(len(mail.outbox), 0)

    def test_reset_missing_user_and_case_mismatch_shows_candidates_and_fails(self):
        self._setup_two_factor_state(self.target)

        # 大文字小文字違いの候補がある場合はエラーメッセージに候補を示す
        with self.assertRaises(CommandError) as ctx_case:
            call_command("reset_two_factor", "CMD_TARGET", "--yes")
        self.assertIn("cmd_target", str(ctx_case.exception))
        self.assertTrue(
            TOTPDevice.objects.filter(user=self.target, confirmed=True).exists()
        )

        # 完全に存在しないユーザー名
        with self.assertRaises(CommandError):
            call_command("reset_two_factor", "no_such_user", "--yes")
        self.assertEqual(ActionLog.objects.count(), 0)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class UnlockLoginCommandTests(TestCase):
    """unlock_login 管理コマンドの検証（仕様書 §5.1 / §7.5 / §12.1 / §12.2）。"""

    def setUp(self):
        mail.outbox.clear()
        self.target = User.objects.create_user(
            username="unlock_target",
            password="pw",
            email="unlock_target@example.com",
        )
        AccountLock.objects.create(
            user=self.target,
            failure_count=5,
            last_failed_at=timezone.now(),
            locked_until=timezone.now() + timedelta(minutes=15),
        )

    def test_unlock_resets_failure_count_logs_without_operator_and_sends_no_email(self):
        stdout = io.StringIO()
        with (
            patch.object(CustomUser, "save", wraps=CustomUser.save) as mock_user_save,
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command("unlock_login", "unlock_target", stdout=stdout)

        self.assertEqual(mock_user_save.call_count, 0)

        lock = AccountLock.objects.get(user=self.target)
        self.assertEqual(lock.failure_count, 0)
        self.assertIsNone(lock.last_failed_at)
        self.assertIsNone(lock.locked_until)

        log = ActionLog.objects.get(action=ActionLogAction.ACCOUNT_UNLOCK)
        self.assertIsNone(log.user)
        self.assertEqual(log.object_id, str(self.target.pk))
        self.assertEqual(log.object_repr, "unlock_target")

        # 仕様書 §7.5: ロック解除では通知を送らない
        self.assertEqual(len(mail.outbox), 0)

    def test_unlock_missing_user_and_case_mismatch_raises_command_error(self):
        with self.assertRaises(CommandError) as ctx_case:
            call_command("unlock_login", "UNLOCK_TARGET")
        self.assertIn("unlock_target", str(ctx_case.exception))

        with self.assertRaises(CommandError):
            call_command("unlock_login", "unknown_user")

        lock = AccountLock.objects.get(user=self.target)
        self.assertEqual(lock.failure_count, 5)
        self.assertIsNotNone(lock.locked_until)
        self.assertEqual(ActionLog.objects.count(), 0)


class PurgeLoginAttemptsCommandTests(TestCase):
    """purge_login_attempts 管理コマンドの検証（仕様書 §5.6 / §6.2 / §9.3 / §12.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="purge_cmd_u", password="pw")

    def test_purge_boundaries_keeps_confirmed_totp_and_outputs_counts(self):
        now = timezone.now()

        # LoginAttempt: 181日前(削除) / 179日前(残存)
        attempt_old = LoginAttempt.objects.create(
            username_input="old_attempt",
            result=LoginAttempt.Result.SUCCESS,
            created_at=now - timedelta(days=181),
        )
        attempt_recent = LoginAttempt.objects.create(
            username_input="recent_attempt",
            result=LoginAttempt.Result.SUCCESS,
            created_at=now - timedelta(days=179),
        )

        # LoginDevice: 1年+1日前(削除) / 1年-1日前(残存)
        device_old = LoginDevice.objects.create(
            user=self.user,
            token_hash="cmd_h_old",
            last_seen_at=now - timedelta(days=366),
        )
        device_recent = LoginDevice.objects.create(
            user=self.user,
            token_hash="cmd_h_recent",
            last_seen_at=now - timedelta(days=364),
        )

        # TOTPDevice: 25時間前未確定(削除) / 23時間前未確定(残存) / 30日前確定済み(残存)
        unconf_old = TOTPDevice.objects.create(
            user=self.user, name="unconf_old", confirmed=False
        )
        TOTPDevice.objects.filter(pk=unconf_old.pk).update(
            created_at=now - timedelta(hours=25)
        )

        unconf_recent = TOTPDevice.objects.create(
            user=self.user, name="unconf_recent", confirmed=False
        )
        TOTPDevice.objects.filter(pk=unconf_recent.pk).update(
            created_at=now - timedelta(hours=23)
        )

        conf_old = TOTPDevice.objects.create(
            user=self.user, name="conf_old", confirmed=True
        )
        TOTPDevice.objects.filter(pk=conf_old.pk).update(
            created_at=now - timedelta(days=30)
        )

        stdout = io.StringIO()
        with (
            patch.object(CustomUser, "save", wraps=CustomUser.save) as mock_user_save,
            patch("accounts.login_security.purge.timezone.now", return_value=now),
        ):
            call_command("purge_login_attempts", stdout=stdout)

        self.assertEqual(mock_user_save.call_count, 0)

        self.assertFalse(LoginAttempt.objects.filter(pk=attempt_old.pk).exists())
        self.assertTrue(LoginAttempt.objects.filter(pk=attempt_recent.pk).exists())

        self.assertFalse(LoginDevice.objects.filter(pk=device_old.pk).exists())
        self.assertTrue(LoginDevice.objects.filter(pk=device_recent.pk).exists())

        self.assertFalse(TOTPDevice.objects.filter(pk=unconf_old.pk).exists())
        self.assertTrue(TOTPDevice.objects.filter(pk=unconf_recent.pk).exists())
        self.assertTrue(TOTPDevice.objects.filter(pk=conf_old.pk).exists())

        output_text = stdout.getvalue()
        self.assertIn("ログイン試行記録=1件", output_text)
        self.assertIn("端末記録=1件", output_text)
        self.assertIn("未確定の認証アプリ登録=1件", output_text)
