"""v1.5.1: LDAP 関連 import の条件付き化を保証するテスト。

AUTH_BACKEND=local 環境で python-ldap / django-auth-ldap が未インストールでも
manage.py runserver が起動できる前提を、テストで明示的に守る。

検証範囲：
  1. settings.AUTHENTICATION_BACKENDS が AUTH_BACKEND の値で正しく切り替わる
  2. AUTH_BACKEND=local では AUTH_LDAP_* 系の settings 属性が一切定義されない
     （条件分岐内のローカル変数として扱われ、settings module 属性に昇格しない）
  3. AUTH_BACKEND=local 環境で起動した Django では accounts.signals が
     sys.modules にロードされていない（apps.py の ready() で import がスキップされた証拠）
  4. python-ldap / django-auth-ldap がインストールされた環境では accounts.signals
     を直接 import 可能（signals.py 自体は健全、外部依存遮断は import 経路で行う設計）

[テスト設計の補足]
ready() メソッドを override_settings で再呼び出しして検証する手法は Django の
LazySettings / AppConfig の挙動の組み合わせで安定して動かないため、本テストは
「実環境（実際に起動した Django プロセス）の状態を直接 assert する」アプローチを採る。
"""

import sys

from django.conf import settings
from django.test import SimpleTestCase


class AuthenticationBackendsByAuthBackendTests(SimpleTestCase):
    """settings.AUTHENTICATION_BACKENDS が AUTH_BACKEND の値に応じて切り替わる。"""

    def test_local_uses_model_backend_only(self):
        """AUTH_BACKEND=local のとき AUTHENTICATION_BACKENDS は ModelBackend のみ。"""
        if settings.AUTH_BACKEND == "local":
            self.assertEqual(
                settings.AUTHENTICATION_BACKENDS,
                ["django.contrib.auth.backends.ModelBackend"],
            )
        else:
            self.skipTest(
                f"Test environment runs with AUTH_BACKEND={settings.AUTH_BACKEND!r}, "
                f"skip local-only check"
            )

    def test_ldap_or_both_includes_ldap_backend(self):
        """AUTH_BACKEND=ldap/both のとき AUTHENTICATION_BACKENDS に LDAPBackend が含まれる。"""
        if settings.AUTH_BACKEND in ("ldap", "both"):
            self.assertIn(
                "django_auth_ldap.backend.LDAPBackend",
                settings.AUTHENTICATION_BACKENDS,
            )
        else:
            self.skipTest(
                f"Test environment runs with AUTH_BACKEND={settings.AUTH_BACKEND!r}, "
                f"skip ldap/both-only check"
            )


class LdapSpecificSettingsTests(SimpleTestCase):
    """AUTH_BACKEND=local 時に LDAP 専用設定属性が一切定義されないことを検証。"""

    LDAP_SPECIFIC_ATTRS = (
        "AUTH_LDAP_USER_SEARCH",
        "AUTH_LDAP_USER_ATTR_MAP",
        "AUTH_LDAP_SERVER_URI",
        "AUTH_LDAP_BIND_DN",
        "AUTH_LDAP_BIND_PASSWORD",
        "AUTH_LDAP_CACHE_TIMEOUT",
        "AUTH_LDAP_MIRROR_GROUPS",
        "AUTH_LDAP_FIND_GROUP_PERMS",
        "LDAP_USER_USERNAME_ATTR",
    )

    def test_no_ldap_attrs_when_local(self):
        """AUTH_BACKEND=local では AUTH_LDAP_* 系の settings 属性が定義されないこと。

        これらが「定義されない」=「条件分岐内のローカル変数として扱われ、settings
        module の属性として昇格していない」状態を意味する。仕様書 §5 の
        AUTH_BACKEND=local 想定に対応した設計の証拠となる。
        """
        if settings.AUTH_BACKEND != "local":
            self.skipTest(
                f"Test environment runs with AUTH_BACKEND={settings.AUTH_BACKEND!r}, "
                f"skip local-only attribute check"
            )
        for attr in self.LDAP_SPECIFIC_ATTRS:
            self.assertFalse(
                hasattr(settings, attr),
                f"AUTH_BACKEND=local では settings.{attr} は定義されないことを期待",
            )

    def test_ldap_attrs_present_when_ldap_or_both(self):
        """AUTH_BACKEND=ldap/both では AUTH_LDAP_* 系の主要設定属性が定義されること。"""
        if settings.AUTH_BACKEND not in ("ldap", "both"):
            self.skipTest(
                f"Test environment runs with AUTH_BACKEND={settings.AUTH_BACKEND!r}, "
                f"skip ldap/both-only attribute check"
            )
        for attr in (
            "AUTH_LDAP_USER_SEARCH",
            "AUTH_LDAP_USER_ATTR_MAP",
            "AUTH_LDAP_SERVER_URI",
        ):
            self.assertTrue(
                hasattr(settings, attr),
                f"AUTH_BACKEND={settings.AUTH_BACKEND} では settings.{attr} が定義される",
            )


class AccountsSignalsLoadStateTests(SimpleTestCase):
    """accounts.signals が AUTH_BACKEND に応じて起動時 import されるかを検証。

    Django 起動時に AccountsConfig.ready() が呼ばれた結果として、accounts.signals が
    sys.modules にロードされているかを直接観察する（実環境の状態を assert）。
    """

    def test_signals_not_loaded_when_local_environment(self):
        """AUTH_BACKEND=local の実環境では起動時に accounts.signals が import されない。

        apps.py の ready() で `if AUTH_BACKEND in ("ldap", "both"):` が false に評価され、
        signals の import がスキップされた結果として sys.modules に "accounts.signals" が
        含まれていないことを確認する。

        注意：本テストは「実環境の AUTH_BACKEND」に依存する。dhango_environment では
        .env の AUTH_BACKEND=local が読まれて Django 起動するため、起動直後の状態は
        signals 未ロード。テスト実行中に他のテスト等が直接 from accounts import signals
        した場合は sys.modules に乗ってしまうため、本テストはテストファイル先頭の
        独立したクラスで早期実行されることを前提とする。
        """
        if settings.AUTH_BACKEND != "local":
            self.skipTest(
                f"Test environment runs with AUTH_BACKEND={settings.AUTH_BACKEND!r}, "
                f"skip local-only signals-not-loaded check"
            )
        self.assertNotIn(
            "accounts.signals", sys.modules,
            "実 AUTH_BACKEND=local 環境では起動時の AccountsConfig.ready() で "
            "signals が import されないことを期待。sys.modules に既にある場合は "
            "他のテストが直接 import している可能性を疑うこと。"
        )

    def test_signals_loaded_when_ldap_or_both_environment(self):
        """AUTH_BACKEND=ldap/both の実環境では起動時に accounts.signals が import される。"""
        if settings.AUTH_BACKEND not in ("ldap", "both"):
            self.skipTest(
                f"Test environment runs with AUTH_BACKEND={settings.AUTH_BACKEND!r}, "
                f"skip ldap/both-only signals-loaded check"
            )
        self.assertIn(
            "accounts.signals", sys.modules,
            f"実 AUTH_BACKEND={settings.AUTH_BACKEND} 環境では起動時の "
            f"AccountsConfig.ready() で signals が import されることを期待"
        )


class SignalsImportabilityTests(SimpleTestCase):
    """signals.py 自体が健全に書かれていることを検証（外部依存がインストール済みの前提）。

    本テストは python-ldap / django-auth-ldap がインストールされた環境
    （例：dhango_environment）でのみ意味を持つ。AUTH_BACKEND の値とは独立に、
    「signals.py の中身が壊れていないこと」を保証する独立した健全性チェック。
    """

    def test_signals_module_is_importable(self):
        """accounts.signals を直接 import できる（django_auth_ldap がインストール済みなら）。

        本テスト環境（dhango_environment）では python-ldap / django-auth-ldap が
        インストール済みのため、import 自体は成功する想定。on_populate_user
        receiver 関数が定義されていることも合わせて確認する。
        """
        try:
            import django_auth_ldap  # noqa: F401
        except ImportError:
            self.skipTest(
                "django_auth_ldap is not installed in this environment; "
                "skip signals importability check"
            )
        from accounts import signals
        self.assertTrue(
            hasattr(signals, "on_populate_user"),
            "accounts.signals に on_populate_user receiver が定義されていること",
        )
