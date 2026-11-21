"""二段階認証登録・リカバリーコード表示ビュー（仕様書 v1.3.1 §6.1〜§6.3 / §10 #4・#5 / §14）。"""

from base64 import b32encode
import io
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django_otp.plugins.otp_totp.models import TOTPDevice
import segno

from accounts.login_security.devices import list_user_devices, match_login_device
from accounts.login_security.notifications import summarize_user_agent
from accounts.login_security.operations import (
    DeviceRevokeMode,
    OperationStatus,
    begin_enrollment,
    complete_enrollment,
    revoke_own_device,
)
from accounts.login_security.recovery_codes import get_remaining_recovery_code_count
from accounts.login_security.totp import has_confirmed_totp
from accounts.models import LoginDevice
from back_navigator.back_navigator import BackNavigator

JST = ZoneInfo("Asia/Tokyo")

INVALID_CONFIRM_CODE_MESSAGE = (
    "コードが正しくありません。認証アプリに表示された最新の 6 桁コードを入力してください。"
)


def _build_qr_svg(config_url: str) -> str:
    """segno を用いてサーバー内で QR コードの SVG 文字列を生成する（§14-6）。"""
    qr = segno.make(config_url)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", xmldecl=False, scale=5)
    return buf.getvalue().decode("utf-8")


@method_decorator(never_cache, name="dispatch")
class TwoFactorSetupView(LoginRequiredMixin, View):
    """認証アプリ登録画面（accounts:two_factor_setup、初回登録、§6.2 / §10 #4）。

    業務ロール・権限グループを問わず、ログイン済みであれば誰でも開ける（§14-20）。
    """

    template_name = "accounts/two_factor_setup.html"
    recovery_template_name = "accounts/two_factor_recovery_codes.html"

    def _render_setup(
        self,
        request,
        *,
        already_enrolled: bool = False,
        device_id: int | None = None,
        manual_secret: str = "",
        qr_svg: str = "",
        error_message: str = "",
        status: int = 200,
    ):
        return render(
            request,
            self.template_name,
            {
                "already_enrolled": already_enrolled,
                "device_id": device_id,
                "manual_secret": manual_secret,
                "qr_svg": qr_svg,
                "error_message": error_message,
            },
            status=status,
        )

    def get(self, request):
        if has_confirmed_totp(request.user):
            return self._render_setup(request, already_enrolled=True)
        return self._render_setup(request, already_enrolled=False)

    def post(self, request):
        if has_confirmed_totp(request.user):
            return self._render_setup(
                request,
                already_enrolled=True,
                error_message="二段階認証はすでに登録済みです。",
                status=400,
            )

        action = (request.POST.get("action") or "").strip()

        if action == "start":
            res = begin_enrollment(request.user)
            if not res.is_success or res.enrollment is None:
                return self._render_setup(
                    request,
                    error_message="登録の開始に失敗しました。もう一度お試しください。",
                    status=400,
                )
            return self._render_setup(
                request,
                device_id=res.enrollment.device.pk,
                manual_secret=res.enrollment.manual_secret,
                qr_svg=res.enrollment.qr_svg,
            )

        if action == "confirm":
            raw_device_id = (request.POST.get("device_id") or "").strip()
            try:
                device_id = int(raw_device_id)
            except (TypeError, ValueError):
                return self._render_setup(
                    request,
                    error_message="登録情報が見つかりません。もう一度「登録を開始」からやり直してください。",
                    status=400,
                )

            pending_device = TOTPDevice.objects.filter(
                pk=device_id,
                user=request.user,
                confirmed=False,
            ).first()
            if pending_device is None:
                return self._render_setup(
                    request,
                    error_message="登録情報が見つかりません。もう一度「登録を開始」からやり直してください。",
                    status=400,
                )

            code = request.POST.get("code", "")
            res = complete_enrollment(
                request.user,
                device_id=pending_device.pk,
                code=code,
                keep_session_key=request.session.session_key,
            )
            if res.is_success:
                return render(
                    request,
                    self.recovery_template_name,
                    {
                        "recovery_codes": res.recovery_codes,
                        "just_enrolled": True,
                    },
                )

            if res.status in (
                OperationStatus.CODE_INVALID,
                OperationStatus.CODE_USED,
            ):
                manual_secret = b32encode(pending_device.bin_key).decode("ascii")
                qr_svg = _build_qr_svg(pending_device.config_url)
                return self._render_setup(
                    request,
                    device_id=pending_device.pk,
                    manual_secret=manual_secret,
                    qr_svg=qr_svg,
                    error_message=INVALID_CONFIRM_CODE_MESSAGE,
                )

            return self._render_setup(
                request,
                error_message="登録の確定に失敗しました。もう一度「登録を開始」からやり直してください。",
                status=400,
            )

        return self._render_setup(
            request,
            error_message="不正な操作です。もう一度「登録を開始」からやり直してください。",
            status=400,
        )


@method_decorator(never_cache, name="dispatch")
class TwoFactorRecoveryCodesView(LoginRequiredMixin, View):
    """リカバリーコード表示／完了画面（accounts:two_factor_recovery_codes、§6.3 / §10 #5）。

    コードは発行直後の 1 回のみ表示し、セッション等には保持しないため、
    GET では再表示できない旨の案内とホームへの導線のみを表示する。
    """

    template_name = "accounts/two_factor_recovery_codes.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"recovery_codes": (), "just_enrolled": False},
        )

    def post(self, request):
        return redirect("home")


@method_decorator(never_cache, name="dispatch")
class TwoFactorSettingsView(LoginRequiredMixin, View):
    """二段階認証の設定画面（accounts:two_factor、GET のみ、仕様書 v1.3.1 §6.3 / §6.6 / §10 #3）。

    業務ロール・権限グループを問わず、ログイン済みであれば誰でも開ける（§10）。
    """

    http_method_names = ["get", "head", "options"]
    template_name = "accounts/two_factor_settings.html"

    def get(self, request):
        now = timezone.now()
        back = BackNavigator(request)
        back.push_current("二段階認証の設定", [])

        enrolled = has_confirmed_totp(request.user)
        remaining_recovery_codes = (
            get_remaining_recovery_code_count(request.user) if enrolled else None
        )
        low_recovery_warning = (
            remaining_recovery_codes is not None and remaining_recovery_codes <= 3
        )

        device_infos = list_user_devices(request.user, now=now)
        current_match = match_login_device(request.user, request=request, now=now)
        current_device_id = (
            current_match.device.pk if current_match.device is not None else None
        )

        device_rows = [
            {
                "id": info.device.pk,
                "browser_summary": summarize_user_agent(info.device.user_agent),
                "first_seen_at": timezone.localtime(info.device.first_seen_at, JST),
                "last_seen_at": timezone.localtime(info.device.last_seen_at, JST),
                "last_ip": info.device.last_ip,
                "is_trusted": info.is_trusted,
                "trusted_until": (
                    timezone.localtime(info.device.trusted_until, JST)
                    if info.is_trusted and info.device.trusted_until is not None
                    else None
                ),
                "is_current": (
                    current_device_id is not None
                    and info.device.pk == current_device_id
                ),
            }
            for info in device_infos
        ]

        return render(
            request,
            self.template_name,
            {
                "back": back,
                "enrolled": enrolled,
                "remaining_recovery_codes": remaining_recovery_codes,
                "low_recovery_warning": low_recovery_warning,
                "device_rows": device_rows,
            },
        )


@method_decorator(never_cache, name="dispatch")
class LoginDeviceRevokeView(LoginRequiredMixin, View):
    """端末の信頼取り消し・削除（accounts:login_device_revoke、POST のみ、仕様書 v1.3.1 §6.6 / §10 #7）。"""

    http_method_names = ["post", "options"]

    def post(self, request, device_id: int):
        device = get_object_or_404(LoginDevice, pk=device_id, user=request.user)

        action = (request.POST.get("action") or "").strip()
        if action == "revoke":
            mode = DeviceRevokeMode.REVOKE_TRUST
            success_message = "選択した端末の信頼を取り消しました。"
        elif action == "delete":
            mode = DeviceRevokeMode.DELETE
            success_message = "端末記録を削除しました。"
        else:
            return HttpResponseBadRequest("不正な操作です。")

        res = revoke_own_device(request.user, device.pk, mode=mode)
        if res.status == OperationStatus.NOT_FOUND:
            raise Http404("端末記録が見つかりません。")
        if not res.is_success:
            return HttpResponseBadRequest("端末操作に失敗しました。")

        messages.success(request, success_message)
        return redirect("accounts:two_factor")
