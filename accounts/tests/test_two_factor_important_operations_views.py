"""ログインセキュリティ Step 4-2b-2（無効化・リカバリーコード再発行・再登録）のテスト。"""

from base64 import b32encode
from pathlib import Path
import re
import time

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ActionLogAction
from accounts.login_security.devices import (
    finalize_login_device,
    grant_device_trust,
    hash_device_token,
    match_login_device,
)
from accounts.login_security.login_service import (
    INVALID_CODE_ERROR_MESSAGE,
    USED_CODE_ERROR_MESSAGE,
)
from accounts.login_security.recovery_codes import (
    issue_recovery_codes,
    verify_and_consume_recovery_code,
)
from accounts.models import (
    AccountLock,
    CustomUser,
    LoginAttempt,
    LoginDevice,
    RecoveryCode,
    SecuritySetting,
)
from accounts.views_two_factor import LOCKED_OPERATION_ERROR_MESSAGE
from actionlogs.models import ActionLog


def _totp_code(device: TOTPDevice, *, offset_seconds: int = 0) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + offset_seconds
    return str(totp.token()).zfill(device.digits)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    OTP_TOTP_ISSUER="FreeGroup2TestIssuer",
    CLIENT_IP_SOURCE="x_real_ip",
)
class TwoFactorImportantOperationsViewTests(TestCase):
    """無効化・リカバリーコード再発行・再登録と設定画面ボタンの結合テスト。"""

    def setUp(self):
        mail.outbox.clear()
        self.settings_url = reverse("accounts:two_factor")
        self.setup_url = reverse("accounts:two_factor_setup")
        self.recovery_url = reverse("accounts:two_factor_recovery_codes")
        self.disable_url = reverse("accounts:two_factor_disable")
        self.login_url = reverse("accounts:login")

        self.setting, _ = SecuritySetting.objects.get_or_create(id=1)
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.OPTIONAL
        self.setting.lockout_threshold = 3
        self.setting.save()

        self.user = CustomUser.objects.create_user(
            username="op_view_user",
            password="pw",
            email="op_view_user@example.com",
        )
        self.other = CustomUser.objects.create_user(
            username="op_view_other",
            password="pw",
            email="op_view_other@example.com",
        )

    def _enroll_user(self, user=None):
        target = user or self.user
        device = TOTPDevice.objects.create(user=target, name="default", confirmed=True)
        codes = issue_recovery_codes(target)
        fin = finalize_login_device(target, match_login_device(target))
        grant_device_trust(fin.device, 30)
        return device, codes, fin

    def _post_important_op(self, op_key: str, code: str):
        if op_key == "disable":
            return self.client.post(
                self.disable_url,
                {"code": code},
                HTTP_X_REAL_IP="203.0.113.20",
            )
        if op_key == "regenerate":
            return self.client.post(
                self.recovery_url,
                {"action": "regenerate", "code": code},
                HTTP_X_REAL_IP="203.0.113.20",
            )
        if op_key == "reenroll_start":
            return self.client.post(
                self.setup_url,
                {"action": "reenroll_start", "code": code},
                HTTP_X_REAL_IP="203.0.113.20",
            )
        raise ValueError(op_key)

    # ------------------------------------------------------------------
    # 共通（無効化・再発行・再登録開始）
    # ------------------------------------------------------------------
    def test_anonymous_redirects_to_login_for_important_operations(self):
        for op_key, url in (
            ("disable", self.disable_url),
            ("recovery", self.recovery_url),
            ("setup", self.setup_url),
        ):
            with self.subTest(op=op_key):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertIn(self.login_url, resp.url)

    def test_common_invalid_empty_and_lockout_behavior_across_all_three_ops(self):
        for op_key in ("disable", "regenerate", "reenroll_start"):
            with self.subTest(op=op_key):
                TOTPDevice.objects.filter(user=self.user).delete()
                RecoveryCode.objects.filter(user=self.user).delete()
                AccountLock.objects.filter(user=self.user).delete()
                LoginAttempt.objects.all().delete()
                mail.outbox.clear()

                device, codes, _ = self._enroll_user()
                valid_code = _totp_code(device)
                wrong_code = "000000" if valid_code != "000000" else "999999"

                self.client.force_login(self.user)

                # 1回目: 誤コード → 失敗回数 1、コード失敗記録 1 件、操作未実行
                r1 = self._post_important_op(op_key, wrong_code)
                self.assertEqual(r1.status_code, 200)
                self.assertIn(INVALID_CODE_ERROR_MESSAGE, r1.content.decode("utf-8"))
                self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)
                self.assertEqual(
                    LoginAttempt.objects.filter(
                        user=self.user, result=LoginAttempt.Result.CODE_FAILED
                    ).count(),
                    1,
                )
                self.assertTrue(
                    TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists()
                )
                self.assertFalse(
                    TOTPDevice.objects.filter(user=self.user, confirmed=False).exists()
                )

                # 2回目: 空コード → 誤りと同じく失敗回数加算
                r2 = self._post_important_op(op_key, "")
                self.assertEqual(r2.status_code, 200)
                self.assertIn(INVALID_CODE_ERROR_MESSAGE, r2.content.decode("utf-8"))
                self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 2)

                # 3回目: 閾値到達でロック入り
                with self.captureOnCommitCallbacks(execute=True):
                    r3 = self._post_important_op(op_key, wrong_code)
                self.assertEqual(r3.status_code, 200)
                lock = AccountLock.objects.get(user=self.user)
                self.assertEqual(lock.failure_count, 3)
                self.assertIsNotNone(lock.locked_until)

                # ロック中は正しいコードでも 403 拒否・操作されない
                r_locked = self._post_important_op(op_key, valid_code)
                self.assertEqual(r_locked.status_code, 403)
                self.assertIn(
                    LOCKED_OPERATION_ERROR_MESSAGE, r_locked.content.decode("utf-8")
                )
                self.assertTrue(
                    TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists()
                )
                self.assertFalse(
                    TOTPDevice.objects.filter(user=self.user, confirmed=False).exists()
                )
                # 最初のコードはまだ使用済みになっていないため別テストに引きずらない
                self.assertTrue(
                    RecoveryCode.objects.filter(
                        user=self.user, used_at__isnull=True
                    ).count()
                    == 10
                )
                # リカバリーコードのハッシュが初期状態と同じであること
                self.assertTrue(verify_and_consume_recovery_code(self.user, codes[0]))

    def test_common_used_totp_code_shows_dedicated_message_without_incrementing_failures(
        self,
    ):
        for op_key in ("disable", "regenerate", "reenroll_start"):
            with self.subTest(op=op_key):
                TOTPDevice.objects.filter(user=self.user).delete()
                AccountLock.objects.filter(user=self.user).delete()

                device, _, _ = self._enroll_user()
                code = _totp_code(device)
                # 1回目で TOTP コードを使用済みにする
                self.assertTrue(device.verify_token(code))

                self.client.force_login(self.user)
                resp = self._post_important_op(op_key, code)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(USED_CODE_ERROR_MESSAGE, resp.content.decode("utf-8"))
                self.assertFalse(AccountLock.objects.filter(user=self.user).exists())
                self.assertTrue(
                    TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists()
                )
                self.assertFalse(
                    TOTPDevice.objects.filter(user=self.user, confirmed=False).exists()
                )

    def test_common_recovery_code_works_once_and_other_users_code_rejected(self):
        for op_key in ("disable", "regenerate", "reenroll_start"):
            with self.subTest(op=op_key):
                TOTPDevice.objects.filter(user=self.user).delete()
                TOTPDevice.objects.filter(user=self.other).delete()
                RecoveryCode.objects.all().delete()
                AccountLock.objects.all().delete()
                mail.outbox.clear()

                _, user_codes, _ = self._enroll_user(self.user)
                _, other_codes, _ = self._enroll_user(self.other)

                self.client.force_login(self.user)

                # 他人のリカバリーコードは拒否され失敗加算
                r_other = self._post_important_op(op_key, other_codes[0])
                self.assertEqual(r_other.status_code, 200)
                self.assertIn(
                    INVALID_CODE_ERROR_MESSAGE, r_other.content.decode("utf-8")
                )
                self.assertEqual(AccountLock.objects.get(user=self.user).failure_count, 1)

                # 本人のリカバリーコード 1 回目は成功
                with self.captureOnCommitCallbacks(execute=True):
                    r_ok = self._post_important_op(op_key, user_codes[0])
                if op_key == "disable":
                    self.assertRedirects(r_ok, self.settings_url)
                    self.assertFalse(
                        TOTPDevice.objects.filter(user=self.user, confirmed=True).exists()
                    )
                elif op_key == "regenerate":
                    self.assertEqual(r_ok.status_code, 200)
                    self.assertEqual(
                        len(
                            re.findall(
                                r"\b[2-9A-HJ-NP-Z]{5}-[2-9A-HJ-NP-Z]{5}\b",
                                r_ok.content.decode("utf-8"),
                            )
                        ),
                        10,
                    )
                else:
                    self.assertEqual(r_ok.status_code, 200)
                    self.assertTrue(
                        TOTPDevice.objects.filter(
                            user=self.user, confirmed=False
                        ).exists()
                    )

                # 同じリカバリーコードは 1 回限り（regenerate や reenroll_start 後も無効）
                if op_key != "disable":
                    r_reuse = self._post_important_op(op_key, user_codes[0])
                    self.assertEqual(r_reuse.status_code, 200)
                    self.assertIn(
                        INVALID_CODE_ERROR_MESSAGE, r_reuse.content.decode("utf-8")
                    )

    # ------------------------------------------------------------------
    # 無効化
    # ------------------------------------------------------------------
    def test_disable_success_clears_state_revokes_other_sessions_notifies_and_logs(
        self,
    ):
        device, codes, fin = self._enroll_user()
        secret_b32 = b32encode(device.bin_key).decode("ascii")

        # 操作中クライアント + 同ユーザーの別クライアント + 他ユーザーのクライアント
        self.client.force_login(self.user)
        current_sess_key = self.client.session.session_key

        other_session_client = Client()
        other_session_client.force_login(self.user)
        other_sess_key = other_session_client.session.session_key

        third_user_client = Client()
        third_user_client.force_login(self.other)
        third_sess_key = third_user_client.session.session_key

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                self.disable_url,
                {"code": _totp_code(device)},
                HTTP_X_REAL_IP="203.0.113.20",
            )
        self.assertRedirects(resp, self.settings_url)

        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertFalse(RecoveryCode.objects.filter(user=self.user).exists())
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)

        # 操作中のみ残り、同ユーザーの他セッションは切断、他ユーザーは残存
        self.assertTrue(Session.objects.filter(session_key=current_sess_key).exists())
        self.assertFalse(Session.objects.filter(session_key=other_sess_key).exists())
        self.assertTrue(Session.objects.filter(session_key=third_sess_key).exists())

        # メール 1 通、操作ログ 1 件（秘密値非含有）
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["op_view_user@example.com"])
        self.assertIn("無効化", mail.outbox[0].subject)
        self.assertNotIn(secret_b32, mail.outbox[0].body)
        for rc in codes:
            self.assertNotIn(rc, mail.outbox[0].body)

        log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_DISABLE)
        self.assertEqual(log.user, self.user)
        log_dump = f"{log.object_repr} {log.note} {log.data}"
        session_dump = repr(dict(self.client.session))
        self.assertNotIn(secret_b32, log_dump)
        self.assertNotIn(secret_b32, session_dump)
        for rc in codes:
            self.assertNotIn(rc, log_dump)
            self.assertNotIn(rc, session_dump)

    def test_disable_forbidden_when_required_and_bad_request_when_unenrolled(self):
        # 未登録時: GET は登録導線、POST は 400
        self.client.force_login(self.user)
        r_unreg_get = self.client.get(self.disable_url)
        self.assertEqual(r_unreg_get.status_code, 200)
        self.assertContains(r_unreg_get, "登録されていません")
        self.assertContains(r_unreg_get, self.setup_url)

        r_unreg_post = self.client.post(self.disable_url, {"code": "123456"})
        self.assertEqual(r_unreg_post.status_code, 400)

        # 必須対象: GET は入力欄なし、POST は 403 で失敗回数も変わらない
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()
        device, _, _ = self._enroll_user()

        r_req_get = self.client.get(self.disable_url)
        self.assertEqual(r_req_get.status_code, 200)
        req_html = r_req_get.content.decode("utf-8")
        self.assertIn("必須のため、無効化できません", req_html)
        self.assertNotIn('name="code"', req_html)

        r_req_post = self.client.post(self.disable_url, {"code": "wrong!"})
        self.assertEqual(r_req_post.status_code, 403)
        self.assertTrue(
            TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists()
        )
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())

    # ------------------------------------------------------------------
    # リカバリーコード再発行
    # ------------------------------------------------------------------
    def test_regenerate_recovery_codes_success_and_unenrolled_400(self):
        self.client.force_login(self.user)

        # 未登録での action=regenerate は 400
        r_unreg = self.client.post(
            self.recovery_url, {"action": "regenerate", "code": "123456"}
        )
        self.assertEqual(r_unreg.status_code, 400)

        device, old_codes, _ = self._enroll_user()
        secret_b32 = b32encode(device.bin_key).decode("ascii")

        # GET で再発行フォームが表示される
        r_get = self.client.get(self.recovery_url)
        self.assertEqual(r_get.status_code, 200)
        self.assertIn("再発行する", r_get.content.decode("utf-8"))

        # 正しいコードで再発行
        with self.captureOnCommitCallbacks(execute=True):
            r_post = self.client.post(
                self.recovery_url,
                {"action": "regenerate", "code": _totp_code(device)},
                HTTP_X_REAL_IP="203.0.113.20",
            )
        self.assertEqual(r_post.status_code, 200)
        html = r_post.content.decode("utf-8")
        new_codes = re.findall(r"\b[2-9A-HJ-NP-Z]{5}-[2-9A-HJ-NP-Z]{5}\b", html)
        self.assertEqual(len(new_codes), 10)
        self.assertEqual(set(new_codes) & set(old_codes), set())

        # 旧コードは無効、DB・セッションに平文なし
        self.assertFalse(verify_and_consume_recovery_code(self.user, old_codes[0]))
        session_dump = repr(dict(self.client.session))
        for nc in new_codes:
            self.assertNotIn(nc, session_dump)
            self.assertFalse(
                RecoveryCode.objects.filter(user=self.user, code_hash=nc).exists()
            )

        # 再発行後に再度 GET してもコードは出ない
        r_get_after = self.client.get(self.recovery_url)
        self.assertEqual(r_get_after.status_code, 200)
        self.assertEqual(
            re.findall(
                r"\b[2-9A-HJ-NP-Z]{5}-[2-9A-HJ-NP-Z]{5}\b",
                r_get_after.content.decode("utf-8"),
            ),
            [],
        )

        # メール・操作ログ（平文コード・秘密鍵非含有）
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("再発行", mail.outbox[0].subject)
        for nc in new_codes:
            self.assertNotIn(nc, mail.outbox[0].body)
        self.assertNotIn(secret_b32, mail.outbox[0].body)

        log = ActionLog.objects.get(action=ActionLogAction.RECOVERY_CODES_REGENERATE)
        self.assertEqual(log.user, self.user)
        log_dump = f"{log.object_repr} {log.note} {log.data}"
        for nc in new_codes:
            self.assertNotIn(nc, log_dump)

    # ------------------------------------------------------------------
    # 再登録
    # ------------------------------------------------------------------
    def test_reenroll_start_confirm_error_hides_qr_and_success_replaces_all(self):
        old_device, old_codes, fin = self._enroll_user()
        old_secret_b32 = b32encode(old_device.bin_key).decode("ascii")

        self.client.force_login(self.user)
        keep_sess_key = self.client.session.session_key

        other_session_client = Client()
        other_session_client.force_login(self.user)
        other_sess_key = other_session_client.session.session_key

        third_client = Client()
        third_client.force_login(self.other)
        third_sess_key = third_client.session.session_key

        # 確定済みユーザーが action=start を送ると 400
        r_start = self.client.post(self.setup_url, {"action": "start"})
        self.assertEqual(r_start.status_code, 400)
        self.assertFalse(
            TOTPDevice.objects.filter(user=self.user, confirmed=False).exists()
        )

        # 再登録開始（正しい旧コード）→ 未確定端末ができ、旧登録も有効なまま
        r_re_start = self.client.post(
            self.setup_url,
            {"action": "reenroll_start", "code": _totp_code(old_device)},
            HTTP_X_REAL_IP="203.0.113.20",
        )
        self.assertEqual(r_re_start.status_code, 200)
        new_device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        new_secret_b32 = b32encode(new_device.bin_key).decode("ascii")
        self.assertTrue(
            TOTPDevice.objects.filter(pk=old_device.pk, confirmed=True).exists()
        )
        re_start_html = r_re_start.content.decode("utf-8")
        self.assertIn("<svg", re_start_html)
        self.assertIn(new_secret_b32, re_start_html)

        # 確認コードが誤り（確定済みユーザー）→ 旧登録残り、QR・秘密鍵は再表示しない
        new_valid = _totp_code(new_device)
        new_wrong = "000000" if new_valid != "000000" else "999999"
        r_confirm_err = self.client.post(
            self.setup_url,
            {
                "action": "confirm",
                "device_id": str(new_device.pk),
                "code": new_wrong,
            },
        )
        self.assertEqual(r_confirm_err.status_code, 200)
        err_html = r_confirm_err.content.decode("utf-8")
        self.assertNotIn("<svg", err_html)
        self.assertNotIn(new_secret_b32, err_html)
        self.assertNotIn(old_secret_b32, err_html)
        self.assertIn(f'value="{new_device.pk}"', err_html)
        self.assertIn('name="code"', err_html)
        self.assertIn("最初からやり直す", err_html)
        self.assertFalse(AccountLock.objects.filter(user=self.user).exists())

        # 正しい確認コードで確定 → 旧登録・旧コード・信頼無効化、他セッション切断、新10個表示
        with self.captureOnCommitCallbacks(execute=True):
            r_confirm_ok = self.client.post(
                self.setup_url,
                {
                    "action": "confirm",
                    "device_id": str(new_device.pk),
                    "code": new_valid,
                },
            )
        self.assertEqual(r_confirm_ok.status_code, 200)
        ok_html = r_confirm_ok.content.decode("utf-8")
        new_codes = re.findall(r"\b[2-9A-HJ-NP-Z]{5}-[2-9A-HJ-NP-Z]{5}\b", ok_html)
        self.assertEqual(len(new_codes), 10)

        self.assertFalse(TOTPDevice.objects.filter(pk=old_device.pk).exists())
        self.assertTrue(
            TOTPDevice.objects.filter(pk=new_device.pk, confirmed=True).exists()
        )
        self.assertFalse(verify_and_consume_recovery_code(self.user, old_codes[0]))
        fin.device.refresh_from_db()
        self.assertIsNone(fin.device.trusted_until)

        self.assertTrue(Session.objects.filter(session_key=keep_sess_key).exists())
        self.assertFalse(Session.objects.filter(session_key=other_sess_key).exists())
        self.assertTrue(Session.objects.filter(session_key=third_sess_key).exists())

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("再登録", mail.outbox[0].subject)
        self.assertNotIn(new_secret_b32, mail.outbox[0].body)
        for nc in new_codes:
            self.assertNotIn(nc, mail.outbox[0].body)

        log = ActionLog.objects.get(action=ActionLogAction.TWO_FACTOR_REENROLL)
        self.assertEqual(log.user, self.user)
        log_dump = f"{log.object_repr} {log.note} {log.data}"
        session_dump = repr(dict(self.client.session))
        self.assertNotIn(new_secret_b32, log_dump)
        self.assertNotIn(new_secret_b32, session_dump)
        for nc in new_codes:
            self.assertNotIn(nc, log_dump)
            self.assertNotIn(nc, session_dump)

    def test_reenroll_confirm_with_other_or_confirmed_or_invalid_device_id_returns_400(
        self,
    ):
        own_confirmed, _, _ = self._enroll_user(self.user)
        other_unconfirmed = TOTPDevice.objects.create(
            user=self.other, name="other_unconf", confirmed=False
        )

        self.client.force_login(self.user)
        for label, bad_id in (
            ("other_unconfirmed", str(other_unconfirmed.pk)),
            ("own_confirmed", str(own_confirmed.pk)),
            ("missing", "999999"),
            ("non_numeric", "abc"),
        ):
            with self.subTest(case=label):
                resp = self.client.post(
                    self.setup_url,
                    {
                        "action": "confirm",
                        "device_id": bad_id,
                        "code": _totp_code(other_unconfirmed),
                    },
                )
                self.assertEqual(resp.status_code, 400)
                self.assertNotIn("<svg", resp.content.decode("utf-8"))

        self.assertFalse(other_unconfirmed.confirmed)
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)

    # ------------------------------------------------------------------
    # 設定画面ボタン・キャッシュ禁止・インラインスタイル
    # ------------------------------------------------------------------
    def test_settings_buttons_reflect_enrollment_and_requirement(self):
        self.client.force_login(self.user)

        # 未登録: 「登録を開始」のみ
        r_unreg = self.client.get(self.settings_url)
        unreg_html = r_unreg.content.decode("utf-8")
        self.assertIn("登録を開始", unreg_html)
        self.assertNotIn("再登録", unreg_html)
        self.assertNotIn("リカバリーコードの再発行", unreg_html)
        self.assertNotIn("無効化", unreg_html)

        # 登録済み（任意）: 3 ボタンすべて有効
        self._enroll_user()
        r_opt = self.client.get(self.settings_url)
        opt_html = r_opt.content.decode("utf-8")
        self.assertIn(self.setup_url, opt_html)
        self.assertIn(self.recovery_url, opt_html)
        self.assertIn(self.disable_url, opt_html)
        self.assertNotIn("disabled", opt_html)

        # 登録済み（必須）: 無効化が disabled になり理由表示
        self.setting.two_factor_enforcement = SecuritySetting.TwoFactorEnforcement.ALL
        self.setting.save()
        r_req = self.client.get(self.settings_url)
        req_html = r_req.content.decode("utf-8")
        self.assertIn("disabled", req_html)
        self.assertIn("必須のため無効化できません", req_html)
        self.assertNotIn(self.disable_url, req_html)

    def test_no_store_headers_and_inline_styles_only_on_code_inputs(self):
        self._enroll_user()
        self.client.force_login(self.user)

        for label, resp in (
            ("disable_get", self.client.get(self.disable_url)),
            ("recovery_get", self.client.get(self.recovery_url)),
            ("setup_get", self.client.get(self.setup_url)),
        ):
            with self.subTest(label=label):
                self.assertIn("no-store", resp.headers.get("Cache-Control", ""))

        disable_src = (
            Path(settings.BASE_DIR) / "templates" / "accounts" / "two_factor_disable.html"
        ).read_text(encoding="utf-8")
        styles = re.findall(r'style="([^"]*)"', disable_src)
        self.assertEqual(len(styles), 1)
        self.assertIn("monospace", styles[0])
