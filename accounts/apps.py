from django.apps import AppConfig
from django.conf import settings


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # v1.5.1: signals.py は django_auth_ldap を無条件 import するため、
        # AUTH_BACKEND=local 環境で python-ldap 未インストールでも起動できるよう、
        # signals の import 自体を AUTH_BACKEND in ("ldap", "both") に限定する。
        # signals.py 本体は触らない（外部依存の遮断は import 経路で行う）。
        if getattr(settings, "AUTH_BACKEND", "local") in ("ldap", "both"):
            from . import signals  # noqa: F401
