"""PostgreSQL 同時実行確認スクリプトの安全ガード判定関数のテスト（DB 書込なし）。"""

from io import StringIO

from django.test import SimpleTestCase

from scripts.check_login_security_concurrency import (
    is_postgresql_engine,
    validate_execution_guard,
)


class ConcurrencyScriptGuardTests(SimpleTestCase):
    """SQLite 拒否（終了コード 2）と --yes 未指定時プレビュー停止の検証。"""

    def test_is_postgresql_engine(self):
        self.assertTrue(is_postgresql_engine("django.db.backends.postgresql"))
        self.assertFalse(is_postgresql_engine("django.db.backends.sqlite3"))
        self.assertFalse(is_postgresql_engine(""))

    def test_rejects_sqlite_with_exit_code_2(self):
        sqlite_settings = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": "db.sqlite3",
        }
        for argv in ([], ["--yes"]):
            with self.subTest(argv=" ".join(argv) or "(empty)"):
                out = StringIO()
                err = StringIO()
                code, _args = validate_execution_guard(
                    argv,
                    db_settings=sqlite_settings,
                    stdout=out,
                    stderr=err,
                )
                self.assertEqual(code, 2)
                self.assertIn("PostgreSQL", err.getvalue())
                self.assertIn("sqlite3", err.getvalue())

    def test_postgresql_without_yes_shows_preview_and_stops(self):
        pg_settings = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "freegroup2_prod",
            "HOST": "db",
            "PORT": "5432",
        }
        out = StringIO()
        err = StringIO()
        code, args = validate_execution_guard(
            [],
            db_settings=pg_settings,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(code, 0)
        self.assertFalse(args.yes)
        self.assertIn("freegroup2_prod", out.getvalue())
        self.assertIn("db", out.getvalue())
        self.assertIn("--yes", out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_postgresql_with_yes_passes_guard(self):
        pg_settings = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "freegroup2_prod",
            "HOST": "db",
            "PORT": "5432",
        }
        out = StringIO()
        err = StringIO()
        code, args = validate_execution_guard(
            ["--yes", "--repeat", "7"],
            db_settings=pg_settings,
            stdout=out,
            stderr=err,
        )
        self.assertIsNone(code)
        self.assertTrue(args.yes)
        self.assertEqual(args.repeat, 7)
