"""二段階認証の強制誘導ミドルウェア（仕様書 v1.3.1 §6.5 / §13 / §14-20 / §14-23）。"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .login_security.policy import is_two_factor_required
from .login_security.totp import has_confirmed_totp
from .login_security.user_lookup import get_security_setting


class TwoFactorEnforcementMiddleware:
    """ログイン済みで二段階認証が必須なのに未登録のユーザーを登録画面へ強制誘導する。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def _is_exempt_path(self, path: str) -> bool:
        static_url = getattr(settings, "STATIC_URL", "") or ""
        if static_url and path.startswith(static_url):
            return True

        exempt_paths = (
            reverse("accounts:two_factor_setup"),
            reverse("accounts:two_factor_recovery_codes"),
            reverse("accounts:logout"),
            reverse("admin:logout"),
        )
        return path in exempt_paths

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        if self._is_exempt_path(request.path):
            return self.get_response(request)

        cfg = get_security_setting()
        if not is_two_factor_required(user, setting=cfg):
            return self.get_response(request)

        if not has_confirmed_totp(user):
            return redirect("accounts:two_factor_setup")

        return self.get_response(request)
