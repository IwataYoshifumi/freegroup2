"""共通のメール送信部品（common/mail.py）の単体テスト（仕様書 v1.3.4 §7.6 / §15）。"""

from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core import mail
from django.test import SimpleTestCase, override_settings

from common.mail import send_mail_safely


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    MAIL_SEND_TIMEOUT=10,
)
class SendMailSafelyTests(SimpleTestCase):
    """common.mail.send_mail_safely のテスト（実 SMTP 送信は行わない）。"""

    def setUp(self):
        mail.outbox.clear()

    def test_common_not_registered_as_django_app(self):
        self.assertNotIn("common", settings.INSTALLED_APPS)

    def test_send_success_plain_text_with_default_from_and_filtered_recipients(self):
        ok = send_mail_safely(
            to=["", "  ", "taro@example.com"],
            bcc=["", "admin@example.com", " "],
            subject="【FreeGroup2】テスト通知",
            body="テキスト本文です。",
        )

        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.from_email, "noreply@example.com")
        self.assertEqual(sent.to, ["taro@example.com"])
        self.assertEqual(sent.bcc, ["admin@example.com"])
        self.assertEqual(sent.subject, "【FreeGroup2】テスト通知")
        self.assertEqual(sent.body, "テキスト本文です。")
        self.assertEqual(getattr(sent, "alternatives", []), [])

    def test_send_with_explicit_from_email_and_bcc_only(self):
        ok = send_mail_safely(
            to=[],
            bcc=["manager@example.com"],
            subject="BCCのみ",
            body="管理者宛本文",
            from_email="custom-sender@example.com",
        )

        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, "custom-sender@example.com")
        self.assertEqual(mail.outbox[0].to, [])
        self.assertEqual(mail.outbox[0].bcc, ["manager@example.com"])

    def test_empty_to_and_bcc_returns_false_without_creating_connection(self):
        with patch("common.mail.get_connection") as mock_get_conn:
            self.assertFalse(
                send_mail_safely(
                    to=["", "   "],
                    bcc=["", None],
                    subject="送信されない件名",
                    body="送信されない本文",
                )
            )
            self.assertFalse(
                send_mail_safely(
                    to=None,
                    bcc=None,
                    subject="送信されない件名2",
                    body="送信されない本文2",
                )
            )

        mock_get_conn.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_timeout_uses_default_setting_when_omitted_and_explicit_when_given(self):
        mock_conn = MagicMock()
        mock_conn.send_messages.return_value = 1

        with patch("common.mail.get_connection", return_value=mock_conn) as mock_get_conn:
            ok_default = send_mail_safely(
                to=["user@example.com"],
                subject="既定タイムアウト",
                body="本文",
            )
        self.assertTrue(ok_default)
        mock_get_conn.assert_called_once_with(timeout=10)

        with patch("common.mail.get_connection", return_value=mock_conn) as mock_get_conn:
            ok_custom = send_mail_safely(
                to=["user@example.com"],
                subject="明示タイムアウト",
                body="本文",
                timeout=5,
            )
        self.assertTrue(ok_custom)
        mock_get_conn.assert_called_once_with(timeout=5)

    @override_settings(MAIL_SEND_TIMEOUT=25)
    def test_timeout_respects_overridden_setting_default(self):
        mock_conn = MagicMock()
        mock_conn.send_messages.return_value = 1

        with patch("common.mail.get_connection", return_value=mock_conn) as mock_get_conn:
            ok = send_mail_safely(
                to=["user@example.com"],
                subject="上書き既定タイムアウト",
                body="本文",
            )
        self.assertTrue(ok)
        mock_get_conn.assert_called_once_with(timeout=25)

    def test_attachments_are_included(self):
        ok = send_mail_safely(
            to=["customer@example.com"],
            subject="見積書送付",
            body="見積書を添付します。",
            attachments=[
                ("quote.pdf", b"%PDF-1.4 dummy", "application/pdf"),
                ("memo.txt", "添付メモ内容", "text/plain"),
            ],
        )

        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].attachments,
            [
                ("quote.pdf", b"%PDF-1.4 dummy", "application/pdf"),
                ("memo.txt", "添付メモ内容", "text/plain"),
            ],
        )

    def test_send_failure_returns_false_without_raising_and_logs_without_pii(self):
        secret_to = "sensitive-to@example.com"
        secret_bcc = "sensitive-bcc@example.com"
        secret_body = "SECRET_BODY_CONTENT_DO_NOT_LOG"
        secret_attachment = "SECRET_ATTACHMENT_DO_NOT_LOG"

        with (
            patch(
                "common.mail.EmailMessage.send",
                side_effect=OSError(f"SMTP error for {secret_to}"),
            ),
            self.assertLogs("common.mail", level="ERROR") as log_ctx,
        ):
            ok = send_mail_safely(
                to=[secret_to],
                bcc=[secret_bcc, "other-bcc@example.com"],
                subject="障害通知件名",
                body=secret_body,
                attachments=[("secret.txt", secret_attachment, "text/plain")],
            )

        self.assertFalse(ok)
        joined_logs = "\n".join(log_ctx.output)
        self.assertIn("3", joined_logs)
        self.assertIn("障害通知件名", joined_logs)
        self.assertIn("OSError", joined_logs)
        self.assertNotIn(secret_to, joined_logs)
        self.assertNotIn(secret_bcc, joined_logs)
        self.assertNotIn("other-bcc@example.com", joined_logs)
        self.assertNotIn(secret_body, joined_logs)
        self.assertNotIn(secret_attachment, joined_logs)

    def test_connection_failure_also_returns_false_and_logs_safely(self):
        with (
            patch("common.mail.get_connection", side_effect=TimeoutError("connect timed out")),
            self.assertLogs("common.mail", level="ERROR") as log_ctx,
        ):
            ok = send_mail_safely(
                to=["user@example.com"],
                subject="接続失敗テスト",
                body="SECRET_BODY_2",
            )

        self.assertFalse(ok)
        joined_logs = "\n".join(log_ctx.output)
        self.assertIn("1", joined_logs)
        self.assertIn("接続失敗テスト", joined_logs)
        self.assertIn("TimeoutError", joined_logs)
        self.assertNotIn("user@example.com", joined_logs)
        self.assertNotIn("SECRET_BODY_2", joined_logs)
