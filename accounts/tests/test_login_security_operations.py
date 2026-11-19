"""ログインセキュリティ Step 2 フェーズ F-2（管理操作 operations.py）の単体テスト。"""

from datetime import timedelta
import time
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ADMIN_ROLE_CODE, ActionLogAction
from accounts.login_security.devices import (
    finalize_login_device,
    grant_device_trust,
    match_login_device,
)
from accounts.login_security.operations import (
    DeviceRevokeMode,
    OperationStatus,
    begin_enrollment,
    complete_enrollment,
    detect_weakened_setting_changes,
    disable_two_factor,
    regenerate_recovery_codes,
    reset_two_factor_by_admin,
    revoke_own_device,
    unlock_account_by_admin,
    update_security_setting,
)
from accounts.login_security.recovery_codes import (
    get_remaining_recovery_code_count,
    issue_recovery_codes,
    verify_and_consume_recovery_code,
)
from accounts.login_security.second_factor import SecondFactorMethod
from accounts.login_security.totp import TotpVerifyStatus, verify_totp_code
from accounts.models import (
    AccountLock,
    CustomUser,
    LoginDevice,
    RecoveryCode,
    Role,
    SecuritySetting,
)
from accounts.services import apply_role
from actionlogs.models import ActionLog

User = get_user_model()


def _totp_code(device: TOTPDevice, *, offset_seconds: int = 0) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + offset_seconds
    return str(totp.token()).zfill(device.digits)


def _create_session(user) -> str:
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store.set_expiry(timedelta(days=7))
    store.save()
    return store.session_key


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class OperationsTests(TestCase):
    """operations.py の全管理操作と二重防衛・atomic・通知・操作ログのテスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
        self.sales_role = Role.objects.get(code="sales")

        self.admin = User.objects.create_user(
            username="op_admin",
            password="pw",
            email="op_admin@example.com",
        )
        apply_role(self.admin, self.admin_role)
        self.admin = User.objects.get(pk=self.admin.pk)

        self.user = User.objects.create_user(
            username="op_user",
            password="pw",
            email="op_user@example.com",
        )
        apply_role(self.user, self.sales_role)
        self.user = User.objects.get(pk=self.user.pk)

        self.other = User.objects.create_user(
            username="op_other",
            password="pw",
            email="op_other@example.com",
        )

    def test_a_permission_denied_for_unauthorized_inactive_and_none_operator(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        AccountLock.objects.create(user=self.user, failure_count=5)
        inactive_admin = User.objects.create_user(
            username="inact_adm", password="pw", is_superuser=True, is_active=False
        )

        for operator in (self.user, inactive_admin, None):
            label = operator.username if operator is not None else "None"
            with self.subTest(operator=label):
                r1 = reset_two_factor_by_admin(operator, self.user)
                r2 = unlock_account_by_admin(operator, self.user)
                r3 = update_security_setting(operator, {"lockout_threshold": 6})
                self.assertEqual(r1.status, OperationStatus.PERMISSION_DENIED)
                self.assertEqual(r2.status, OperationStatus.PERMISSION_DENIED)
                self.assertEqual(r3.status, OperationStatus.PERMISSION_DENIED)

        self.assertTrue(TOTPDevice.objects.filter(user=self.user, confirmed=True).exists())
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 5)
        self.assertEqual(SecuritySetting.objects.get(id=1).lockout_threshold, 5)
        self.assertEqual(ActionLog.objects.count(), 0)

    def test_b_initial_enrollment_complete_logs_notifies_and_keeps_sessions(self):
        sess_key = _create_session(self.user)
        res_begin = begin_enrollment(self.user)
        self.assertTrue(res_begin.is_success)
        self.assertIsNotNone(res_begin.enrollment)
        self.assertFalse(res_begin.enrollment.device.confirmed)

        code = _totp_code(res_begin.enrollment.device)
        with self.captureOnCommitCallbacks(execute=True):
            res_done = complete_enrollment(
                self.user,
                device_id=res_begin.enrollment.device.pk,
                code=code,
                keep_session_key=sess_key,
            )

        self.assertTrue(res_done.is_success)
        self.assertFalse(res_done.is_reenroll)
        self.assertEqual(len(res_done.recovery_codes), 10)
        self.assertTrue(
            TOTPDevice.objects.filter(
                pk=res_begin.enrollment.device.pk, confirmed=True
            ).exists()
        )
        # 初回有効化ではセッションを切らない
        self.assertTrue(Session.objects.filter(session_key=sess_key).exists())
        log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_ENABLE)
        self.assertEqual(log.user, self.user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("有効化", mail.outbox[0].subject)

    def test_c_reenroll_begin_requires_code_and_handles_invalid_used_locked(self):
        dev = TOTPDevice.objects.create(user=self.user, name="old", confirmed=True)
        code = _totp_code(dev)
        wrong = "000000" if code != "000000" else "999999"

        # 不正コード → 加算され開始されない
        r_bad = begin_enrollment(
            self.user, code=wrong, method=SecondFactorMethod.TOTP, ip_address="203.0.113.1"
        )
        self.assertEqual(r_bad.status, OperationStatus.CODE_INVALID)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)
        self.assertFalse(TOTPDevice.objects.filter(user=self.user, confirmed=False).exists())

        # 正しいコードで一度使用済みにする → 使用済みコードは加算されない
        self.assertEqual(verify_totp_code(self.user, code), TotpVerifyStatus.VALID)
        r_used = begin_enrollment(
            self.user, code=code, method=SecondFactorMethod.TOTP, ip_address="203.0.113.1"
        )
        self.assertEqual(r_used.status, OperationStatus.CODE_USED)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)

        # ロック中は拒否
        AccountLock.objects.filter(user=self.user).update(
            failure_count=5, locked_until=timezone.now() + timedelta(minutes=10)
        )
        r_locked = begin_enrollment(
            self.user, code=code, method=SecondFactorMethod.TOTP, ip_address="203.0.113.1"
        )
        self.assertEqual(r_locked.status, OperationStatus.REJECTED)

    def test_d_e_reenroll_keeps_old_until_confirmed_then_replaces_and_revokes(self):
        old_dev = TOTPDevice.objects.create(user=self.user, name="old", confirmed=True)
        old_codes = issue_recovery_codes(self.user)
        fin = finalize_login_device(self.user, match_login_device(self.user))
        grant_device_trust(fin.device, 30)
        k_keep = _create_session(self.user)
        k_other = _create_session(self.user)

        # 再登録開始（旧確定済みデバイスと別タイムウィンドウでも通るよう +30s コードを使う等）
        start_code = _totp_code(old_dev)
        res_begin = begin_enrollment(
            self.user, code=start_code, method=SecondFactorMethod.TOTP
        )
        self.assertTrue(res_begin.is_success)
        new_dev = res_begin.enrollment.device

        # 確認コード不正 → 旧登録が残る
        new_code = _totp_code(new_dev)
        wrong_new = "000000" if new_code != "000000" else "999999"
        r_fail = complete_enrollment(
            self.user, device_id=new_dev.pk, code=wrong_new, keep_session_key=k_keep
        )
        self.assertEqual(r_fail.status, OperationStatus.CODE_INVALID)
        self.assertTrue(TOTPDevice.objects.filter(pk=old_dev.pk, confirmed=True).exists())

        # 未確定の間は旧登録のコードがまだ通る（+30秒枠のコードで再利用防止を回避して確認）
        next_old_code = _totp_code(old_dev, offset_seconds=30)
        self.assertEqual(verify_totp_code(self.user, next_old_code), TotpVerifyStatus.VALID)

        # 正しい確認コードで再登録確定
        with self.captureOnCommitCallbacks(execute=True):
            res_done = complete_enrollment(
                self.user, device_id=new_dev.pk, code=new_code, keep_session_key=k_keep
            )
        self.assertTrue(res_done.is_success)
        self.assertTrue(res_done.is_reenroll)

        # 旧登録は消え、旧登録のコードは通らなくなる
        self.assertFalse(TOTPDevice.objects.filter(pk=old_dev.pk).exists())
        self.assertEqual(
            verify_totp_code(self.user, _totp_code(old_dev, offset_seconds=60)),
            TotpVerifyStatus.INVALID,
        )
        # 旧リカバリーコード無効・端末信頼無効・操作中以外のセッション切断
        self.assertFalse(verify_and_consume_recovery_code(self.user, old_codes[0]))
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)
        self.assertTrue(Session.objects.filter(session_key=k_keep).exists())
        self.assertFalse(Session.objects.filter(session_key=k_other).exists())

        self.assertTrue(
            ActionLog.objects.filter(action=ActionLogAction.TWO_FACTOR_REENROLL).exists()
        )
        self.assertTrue(any("再登録" in m.subject for m in mail.outbox))

    def test_f_complete_enrollment_rolls_back_on_exception(self):
        old_dev = TOTPDevice.objects.create(user=self.user, name="old", confirmed=True)
        new_dev = TOTPDevice.objects.create(user=self.user, name="new", confirmed=False)
        code = _totp_code(new_dev)

        with patch(
            "accounts.login_security.operations.issue_recovery_codes",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                complete_enrollment(self.user, device_id=new_dev.pk, code=code)

        self.assertTrue(TOTPDevice.objects.filter(pk=old_dev.pk, confirmed=True).exists())
        new_dev.refresh_from_db()
        self.assertFalse(new_dev.confirmed)

    def test_g_complete_enrollment_rejects_other_users_device(self):
        other_dev = TOTPDevice.objects.create(user=self.other, name="x", confirmed=False)
        res = complete_enrollment(
            self.user, device_id=other_dev.pk, code=_totp_code(other_dev)
        )
        self.assertEqual(res.status, OperationStatus.NOT_FOUND)

    def test_h_disable_two_factor_rules_and_cleanup(self):
        # 未登録
        self.assertEqual(
            disable_two_factor(self.user, code="123456").status,
            OperationStatus.NOT_ENROLLED,
        )

        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        issue_recovery_codes(self.user)
        fin = finalize_login_device(self.user, match_login_device(self.user))
        grant_device_trust(fin.device, 30)
        k_keep = _create_session(self.user)
        k_other = _create_session(self.user)

        # 必須対象は不許可（失敗加算なし）
        setting = SecuritySetting.objects.get(id=1)
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        setting.save()
        r_req = disable_two_factor(self.user, code="000000")
        self.assertEqual(r_req.status, OperationStatus.NOT_ALLOWED)
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())

        # 任意に戻して不正コード → 加算
        setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.OPTIONAL
        setting.save()
        code = _totp_code(dev)
        wrong = "000000" if code != "000000" else "999999"
        r_bad = disable_two_factor(self.user, code=wrong)
        self.assertEqual(r_bad.status, OperationStatus.CODE_INVALID)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)

        # 正しいコードで無効化成功
        with self.captureOnCommitCallbacks(execute=True):
            r_ok = disable_two_factor(self.user, code=code, keep_session_key=k_keep)
        self.assertTrue(r_ok.is_success)
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertFalse(RecoveryCode.objects.filter(user=self.user).exists())
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)
        self.assertTrue(Session.objects.filter(session_key=k_keep).exists())
        self.assertFalse(Session.objects.filter(session_key=k_other).exists())
        self.assertTrue(
            ActionLog.objects.filter(action=ActionLogAction.TWO_FACTOR_DISABLE).exists()
        )
        self.assertIn("無効化", mail.outbox[0].subject)

    def test_i_regenerate_recovery_codes(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        old_codes = issue_recovery_codes(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            res = regenerate_recovery_codes(
                self.user, code=_totp_code(dev), method=SecondFactorMethod.TOTP
            )
        self.assertTrue(res.is_success)
        self.assertEqual(len(res.recovery_codes), 10)
        self.assertFalse(verify_and_consume_recovery_code(self.user, old_codes[0]))
        self.assertTrue(verify_and_consume_recovery_code(self.user, res.recovery_codes[0]))
        self.assertTrue(
            ActionLog.objects.filter(
                action=ActionLogAction.RECOVERY_CODES_REGENERATE
            ).exists()
        )
        self.assertIn("再発行", mail.outbox[0].subject)

    def test_j_k_reset_two_factor_by_admin_and_command(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        issue_recovery_codes(self.user)
        fin = finalize_login_device(self.user, match_login_device(self.user))
        grant_device_trust(fin.device, 30)
        AccountLock.objects.create(
            user=self.user,
            failure_count=5,
            locked_until=timezone.now() + timedelta(minutes=10),
        )
        k_user = _create_session(self.user)
        k_other = _create_session(self.other)
        old_pw_hash = self.user.password

        # 自分自身は対象にできない
        self.assertEqual(
            reset_two_factor_by_admin(self.admin, self.admin).status,
            OperationStatus.NOT_ALLOWED,
        )

        # 管理者リセット実行
        with self.captureOnCommitCallbacks(execute=True):
            res = reset_two_factor_by_admin(
                self.admin, self.user, reason_note="端末紛失"
            )
        self.assertTrue(res.is_success)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, old_pw_hash)
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertFalse(RecoveryCode.objects.filter(user=self.user).exists())
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)
        self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 0)
        self.assertFalse(Session.objects.filter(session_key=k_user).exists())
        self.assertTrue(Session.objects.filter(session_key=k_other).exists())

        log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_RESET)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.note, "端末紛失")
        self.assertEqual(len(mail.outbox), 2)
        self.assertNotIn("端末紛失", mail.outbox[0].body)
        self.assertIn("端末紛失", mail.outbox[1].body)

        # コマンド実行（operator=None, as_command=True）
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            res_cmd = reset_two_factor_by_admin(
                None, self.user, reason_note="CLI復旧", as_command=True
            )
        self.assertTrue(res_cmd.is_success)
        cmd_log = ActionLog.objects.filter(action=ActionLogAction.TWO_FACTOR_RESET).latest(
            "created_at"
        )
        self.assertIsNone(cmd_log.user)

    def test_l_unlock_account_by_admin_writes_single_log(self):
        AccountLock.objects.create(
            user=self.user,
            failure_count=5,
            locked_until=timezone.now() + timedelta(minutes=10),
        )
        res = unlock_account_by_admin(self.admin, self.user)
        self.assertTrue(res.is_success)
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 0)
        self.assertIsNone(lock.locked_until)
        self.assertEqual(
            ActionLog.objects.filter(action=ActionLogAction.ACCOUNT_UNLOCK).count(), 1
        )

    def test_m_update_security_setting_validation_boundaries(self):
        # 境界内（最小・最大）は通る
        r_min = update_security_setting(
            self.admin,
            {
                "lockout_threshold": 3,
                "lockout_minutes": 5,
                "ip_block_threshold": 10,
                "trusted_device_days": 0,
            },
        )
        self.assertTrue(r_min.is_success)

        r_max = update_security_setting(
            self.admin,
            {
                "lockout_threshold": 10,
                "lockout_minutes": 1440,
                "ip_block_threshold": 100,
                "trusted_device_days": 90,
            },
        )
        self.assertTrue(r_max.is_success)

        # 範囲外・型不正・未知項目は何も保存されない
        r_bad = update_security_setting(
            self.admin,
            {
                "lockout_threshold": 2,
                "lockout_minutes": 1441,
                "ip_block_threshold": 9,
                "trusted_device_days": 91,
                "two_factor_enforcement": "invalid",
                "notify_admins_on_account_lock": "yes",
            },
        )
        self.assertEqual(r_bad.status, OperationStatus.VALIDATION_ERROR)
        self.assertEqual(len(r_bad.field_errors), 6)
        self.assertEqual(SecuritySetting.objects.get(id=1).lockout_threshold, 10)

    def test_n_detect_weakened_setting_changes_pure(self):
        base = {
            "two_factor_enforcement": "admin_only",
            "login_notification_level": "every",
            "lockout_threshold": 5,
            "lockout_minutes": 15,
            "ip_block_threshold": 20,
            "trusted_device_days": 30,
            "notify_admins_on_account_lock": True,
            "notify_admins_on_ip_block": True,
        }
        # 変更なし → 空
        self.assertEqual(detect_weakened_setting_changes(base, dict(base)), [])

        # すべて強化方向 → 空
        stronger = {
            "two_factor_enforcement": "all",
            "login_notification_level": "every",
            "lockout_threshold": 3,
            "lockout_minutes": 30,
            "ip_block_threshold": 10,
            "trusted_device_days": 0,
            "notify_admins_on_account_lock": True,
            "notify_admins_on_ip_block": True,
        }
        self.assertEqual(detect_weakened_setting_changes(base, stronger), [])

        # 8条件すべて弱める方向 → 8件すべて検出
        weaker = {
            "two_factor_enforcement": "optional",
            "login_notification_level": "new_device_only",
            "lockout_threshold": 8,
            "lockout_minutes": 10,
            "ip_block_threshold": 50,
            "trusted_device_days": 60,
            "notify_admins_on_account_lock": False,
            "notify_admins_on_ip_block": False,
        }
        weakened = detect_weakened_setting_changes(base, weaker)
        self.assertEqual(len(weakened), 8)

    def test_o_p_q_update_security_setting_notifications_logs_and_trust_reset(self):
        fin1 = finalize_login_device(self.user, match_login_device(self.user))
        fin2 = finalize_login_device(self.other, match_login_device(self.other))
        grant_device_trust(fin1.device, 30)
        grant_device_trust(fin2.device, 30)

        # 変更なし → 操作ログ・通知なし
        with self.captureOnCommitCallbacks(execute=True):
            r_noop = update_security_setting(self.admin, {"lockout_threshold": 5})
        self.assertTrue(r_noop.is_success)
        self.assertEqual(ActionLog.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

        # 強化方向（信頼期間を 30 -> 10、通知設定を OFF ではなく別強化）→ 通知なし、信頼も残る
        with self.captureOnCommitCallbacks(execute=True):
            r_strong = update_security_setting(
                self.admin,
                {"two_factor_enforcement": "all", "trusted_device_days": 10},
            )
        self.assertTrue(r_strong.is_success)
        self.assertEqual(len(mail.outbox), 0)
        fin1.device.refresh_from_db()
        self.assertIsNotNone(fin1.device.trusted_until)

        # 信頼期間 0 へ変更（強化方向）→ 全ユーザー信頼無効化（通知なし）
        with self.captureOnCommitCallbacks(execute=True):
            r_zero = update_security_setting(self.admin, {"trusted_device_days": 0})
        self.assertTrue(r_zero.is_success)
        fin1.device.refresh_from_db()
        fin2.device.refresh_from_db()
        self.assertIsNone(fin1.device.trusted_until)
        self.assertIsNone(fin2.device.trusted_until)
        self.assertEqual(len(mail.outbox), 0)

        # 弱める変更（ロック通知・IP通知を False にしても弱める通知は必ず届く）
        with self.captureOnCommitCallbacks(execute=True):
            r_weak = update_security_setting(
                self.admin,
                {
                    "notify_admins_on_account_lock": False,
                    "notify_admins_on_ip_block": False,
                    "lockout_threshold": 8,
                },
            )
        self.assertTrue(r_weak.is_success)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ロック失敗回数: 5回 → 8回", mail.outbox[0].body)
        log = ActionLog.objects.filter(
            action=ActionLogAction.SECURITY_SETTING_UPDATE
        ).latest("created_at")
        self.assertEqual(
            log.data["changes"]["lockout_threshold"], {"before": 5, "after": 8}
        )

    def test_r_revoke_own_device_and_isolation(self):
        d_user = finalize_login_device(self.user, match_login_device(self.user)).device
        grant_device_trust(d_user, 30)
        d_other = finalize_login_device(self.other, match_login_device(self.other)).device

        # 他人の端末 ID は NOT_FOUND
        self.assertEqual(
            revoke_own_device(
                self.user, d_other.pk, mode=DeviceRevokeMode.DELETE
            ).status,
            OperationStatus.NOT_FOUND,
        )
        self.assertTrue(LoginDevice.objects.filter(pk=d_other.pk).exists())

        # 信頼取り消しのみ
        r_revoke = revoke_own_device(
            self.user, d_user.pk, mode=DeviceRevokeMode.REVOKE_TRUST
        )
        self.assertTrue(r_revoke.is_success)
        d_user.refresh_from_db()
        self.assertIsNone(d_user.trusted_until)

        # 削除
        r_del = revoke_own_device(self.user, d_user.pk, mode=DeviceRevokeMode.DELETE)
        self.assertTrue(r_del.is_success)
        self.assertFalse(LoginDevice.objects.filter(pk=d_user.pk).exists())
        self.assertEqual(
            ActionLog.objects.filter(action=ActionLogAction.LOGIN_DEVICE_REVOKE).count(),
            2,
        )

    def test_s_no_customuser_save_in_any_operation(self):
        with patch.object(CustomUser, "save", wraps=CustomUser.save) as mock_save:
            r_begin = begin_enrollment(self.user)
            dev = r_begin.enrollment.device
            complete_enrollment(self.user, device_id=dev.pk, code=_totp_code(dev))
            regenerate_recovery_codes(
                self.user, code=_totp_code(dev, offset_seconds=30)
            )
            d_user = finalize_login_device(
                self.user, match_login_device(self.user)
            ).device
            revoke_own_device(self.user, d_user.pk, mode=DeviceRevokeMode.DELETE)
            update_security_setting(self.admin, {"lockout_threshold": 6})
            unlock_account_by_admin(self.admin, self.user)
            reset_two_factor_by_admin(self.admin, self.user)
            self.assertEqual(mock_save.call_count, 0)
