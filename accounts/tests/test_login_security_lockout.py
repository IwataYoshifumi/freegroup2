"""ログインセキュリティ Step 2 フェーズ B（試行記録・ロック・IP 遮断）の単体テスト。"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.constants import ActionLogAction
from accounts.login_security.attempts import record_login_attempt
from accounts.login_security.lockout import (
    are_any_users_locked,
    check_pre_second_factor,
    is_ip_blocked,
    is_user_locked,
    record_failure_and_evaluate,
    record_user_failure_and_check_lock,
    reset_failure_count,
    unlock_user,
)
from accounts.login_security.user_lookup import resolve_login_users
from accounts.models import AccountLock, CustomUser, LoginAttempt, SecuritySetting
from actionlogs.models import ActionLog

User = get_user_model()


class AttemptRecordTests(TestCase):
    """ログイン試行記録のテスト。"""

    def test_username_input_truncated_to_150_chars(self):
        long_name = "a" * 200
        attempt = record_login_attempt(
            username_input=long_name,
            user=None,
            ip_address="203.0.113.5",
            user_agent="TestUA",
            result=LoginAttempt.Result.PASSWORD_FAILED,
        )
        self.assertEqual(len(attempt.username_input), 150)
        self.assertEqual(attempt.username_input, "a" * 150)

    def test_second_factor_cleared_for_pre_second_factor_results(self):
        for res in (
            LoginAttempt.Result.PASSWORD_FAILED,
            LoginAttempt.Result.REJECTED_LOCKED,
            LoginAttempt.Result.REJECTED_IP_BLOCKED,
        ):
            attempt = record_login_attempt(
                username_input="u1",
                result=res,
                second_factor=LoginAttempt.SecondFactor.TOTP,
            )
            self.assertEqual(attempt.second_factor, "")

        ok_attempt = record_login_attempt(
            username_input="u1",
            result=LoginAttempt.Result.CODE_FAILED,
            second_factor=LoginAttempt.SecondFactor.TOTP,
        )
        self.assertEqual(ok_attempt.second_factor, LoginAttempt.SecondFactor.TOTP)


class AccountLockTests(TestCase):
    """アカウントロックのテスト。"""

    def setUp(self):
        self.user = User.objects.create_user(username="taro", password="pw")

    def test_lockout_threshold_and_no_extension_while_locked(self):
        base = timezone.now()
        for i in range(4):
            with patch("django.utils.timezone.now", return_value=base + timedelta(seconds=i)):
                lookup = resolve_login_users("  TARO ")
                res = record_failure_and_evaluate(
                    username_input="  TARO ",
                    target_users=lookup.target_users,
                    primary_user=lookup.primary_user,
                    ip_address="203.0.113.10",
                    user_agent="UA",
                    result=LoginAttempt.Result.PASSWORD_FAILED,
                    now=base + timedelta(seconds=i),
                )
                self.assertEqual(res.locked_users, [])
                self.assertFalse(is_user_locked(self.user, now=base + timedelta(seconds=i)))

        # 5 回目でロックに入る（1 回だけ）
        t5 = base + timedelta(seconds=5)
        with patch("django.utils.timezone.now", return_value=t5):
            lookup = resolve_login_users("taro")
            res5 = record_failure_and_evaluate(
                username_input="taro",
                target_users=lookup.target_users,
                primary_user=lookup.primary_user,
                ip_address="203.0.113.10",
                user_agent="UA",
                result=LoginAttempt.Result.CODE_FAILED,
                second_factor=LoginAttempt.SecondFactor.TOTP,
                now=t5,
            )
        self.assertEqual(res5.locked_users, [self.user])
        self.assertTrue(is_user_locked(self.user, now=t5))
        self.assertEqual(
            ActionLog.objects.filter(action=ActionLogAction.ACCOUNT_LOCK).count(),
            1,
        )

        lock = AccountLock.objects.get(user=self.user)
        original_locked_until = lock.locked_until
        self.assertEqual(lock.failure_count, 5)

        # ロック中の追加失敗は回数も期限も延長しない
        t6 = t5 + timedelta(minutes=2)
        entered = record_user_failure_and_check_lock(self.user, now=t6)
        self.assertFalse(entered)
        lock.refresh_from_db()
        self.assertEqual(lock.failure_count, 5)
        self.assertEqual(lock.locked_until, original_locked_until)

    def test_auto_unlock_and_recount_after_lockout_minutes(self):
        base = timezone.now()
        for i in range(5):
            record_user_failure_and_check_lock(
                self.user, now=base + timedelta(seconds=i)
            )
        t_lock = base + timedelta(seconds=4)
        self.assertTrue(is_user_locked(self.user, now=t_lock))

        # 15 分経過後は自動解除
        after_15m = t_lock + timedelta(minutes=15)
        self.assertFalse(is_user_locked(self.user, now=after_15m))

        # ロック時間以上あいた失敗は 1 から数え直し
        entered = record_user_failure_and_check_lock(self.user, now=after_15m)
        self.assertFalse(entered)
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 1)

    def test_case_insensitive_multiple_users_lock(self):
        u2 = User.objects.create_user(username="TARO", password="pw")
        base = timezone.now()
        # 完全一致しない入力 "Taro" では両方が対象になる
        lookup = resolve_login_users("Taro")
        self.assertEqual(lookup.target_users, [self.user, u2])
        for i in range(5):
            record_failure_and_evaluate(
                username_input="Taro",
                target_users=lookup.target_users,
                primary_user=lookup.primary_user,
                ip_address="203.0.113.11",
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=base + timedelta(seconds=i),
            )
        self.assertTrue(are_any_users_locked([self.user, u2], now=base + timedelta(seconds=5)))

    def test_reset_failure_count_and_unlock_user(self):
        base = timezone.now()
        for i in range(5):
            record_user_failure_and_check_lock(self.user, now=base + timedelta(seconds=i))
        self.assertTrue(is_user_locked(self.user, now=base + timedelta(seconds=5)))

        self.assertTrue(unlock_user(self.user))
        self.assertFalse(is_user_locked(self.user, now=base + timedelta(seconds=5)))
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 0)

        record_user_failure_and_check_lock(self.user, now=base + timedelta(seconds=10))
        reset_failure_count(self.user)
        lock.refresh_from_db()
        self.assertEqual(lock.failure_count, 0)
        self.assertIsNone(lock.last_failed_at)
        self.assertIsNone(lock.locked_until)

    def test_concurrent_simulation_and_get_or_create_reuse(self):
        base = timezone.now()
        # 既に AccountLock 行が存在する状態でも get_or_create で既存行を再利用して +1 される
        AccountLock.objects.create(user=self.user, failure_count=1, last_failed_at=base)
        entered = record_user_failure_and_check_lock(
            self.user, now=base + timedelta(seconds=1)
        )
        self.assertFalse(entered)
        lock = AccountLock.objects.get(user=self.user)
        self.assertEqual(lock.failure_count, 2)

        # 連続実行でも取りこぼしがないこと（2 -> 3 -> 4 -> 5 でロック）
        for s in (2, 3, 4):
            entered = record_user_failure_and_check_lock(
                self.user, now=base + timedelta(seconds=s)
            )
        self.assertTrue(entered)
        lock.refresh_from_db()
        self.assertEqual(lock.failure_count, 5)

    def test_check_pre_second_factor(self):
        base = timezone.now()
        res_ok = check_pre_second_factor(self.user, "203.0.113.20", now=base)
        self.assertTrue(res_ok.allowed)
        self.assertFalse(res_ok.ip_blocked)
        self.assertFalse(res_ok.user_locked)

        # ロック後は allowed=False
        for i in range(5):
            record_user_failure_and_check_lock(self.user, now=base + timedelta(seconds=i))
        res_locked = check_pre_second_factor(
            self.user, "203.0.113.20", now=base + timedelta(seconds=5)
        )
        self.assertFalse(res_locked.allowed)
        self.assertTrue(res_locked.user_locked)


class IpBlockTests(TestCase):
    """IP 遮断のテスト。"""

    def test_ip_block_threshold_unknown_users_and_15m_window(self):
        base = timezone.now()
        ip = "198.51.100.77"
        # 存在しないユーザー名への試行を 19 回
        for i in range(19):
            t = base + timedelta(seconds=i)
            with patch("django.utils.timezone.now", return_value=t):
                res = record_failure_and_evaluate(
                    username_input=f"no_such_{i}",
                    target_users=[],
                    primary_user=None,
                    ip_address=ip,
                    user_agent="UA",
                    result=LoginAttempt.Result.PASSWORD_FAILED,
                    now=t,
                )
            self.assertFalse(res.ip_blocked_now)
            self.assertFalse(is_ip_blocked(ip, now=t))

        # 20 回目で IP 遮断に到達（ip_blocked_now=True、操作ログ 1 回）
        t20 = base + timedelta(seconds=20)
        with patch("django.utils.timezone.now", return_value=t20):
            res20 = record_failure_and_evaluate(
                username_input="no_such_20",
                target_users=[],
                primary_user=None,
                ip_address=ip,
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=t20,
            )
        self.assertTrue(res20.ip_blocked_now)
        self.assertTrue(is_ip_blocked(ip, now=t20))
        self.assertEqual(
            ActionLog.objects.filter(action=ActionLogAction.IP_BLOCK).count(), 1
        )

        # 15 分以内の追加失敗では重複して ip_blocked_now=True にならない
        t21 = t20 + timedelta(minutes=5)
        with patch("django.utils.timezone.now", return_value=t21):
            res21 = record_failure_and_evaluate(
                username_input="no_such_21",
                target_users=[],
                primary_user=None,
                ip_address=ip,
                user_agent="UA",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=t21,
            )
        self.assertFalse(res21.ip_blocked_now)
        self.assertEqual(
            ActionLog.objects.filter(action=ActionLogAction.IP_BLOCK).count(), 1
        )

        # 15 分超経過で古い失敗が枠外になり自動解除
        after_15m = t21 + timedelta(minutes=16)
        self.assertFalse(is_ip_blocked(ip, now=after_15m))

    def test_empty_ip_and_rejected_attempts_not_counted(self):
        base = timezone.now()
        self.assertFalse(is_ip_blocked(None, now=base))
        self.assertFalse(is_ip_blocked("", now=base))

        # 拒否記録は IP 遮断件数に数えない
        ip = "198.51.100.88"
        for i in range(25):
            record_login_attempt(
                username_input="u",
                ip_address=ip,
                result=LoginAttempt.Result.REJECTED_IP_BLOCKED,
                created_at=base + timedelta(seconds=i),
            )
        self.assertFalse(is_ip_blocked(ip, now=base + timedelta(seconds=30)))


class NoUserSaveTests(TestCase):
    """失敗・ロック・遮断処理で CustomUser.save() が呼ばれないことを検証（仕様書 §14 #15）。"""

    def test_user_model_never_saved_on_failure_and_lock(self):
        user = User.objects.create_user(username="nosave", password="pw")
        base = timezone.now()
        with patch.object(CustomUser, "save", wraps=CustomUser.save) as mock_save:
            for i in range(5):
                t = base + timedelta(seconds=i)
                with patch("django.utils.timezone.now", return_value=t):
                    record_failure_and_evaluate(
                        username_input="nosave",
                        target_users=[user],
                        primary_user=user,
                        ip_address="203.0.113.99",
                        user_agent="UA",
                        result=LoginAttempt.Result.PASSWORD_FAILED,
                        now=t,
                    )
            unlock_user(user)
            reset_failure_count(user)
            self.assertEqual(mock_save.call_count, 0)
