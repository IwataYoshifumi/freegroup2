"""管理者向けログインセキュリティ画面ビュー（仕様書 v1.3.1 §5.3 / §5.6 / §6.7 / §6.9 / §8 / §10 / §11）。"""

from datetime import date, datetime, time, timedelta
import ipaddress
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import ListView
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.forms import SecuritySettingForm
from accounts.login_security.lockout import is_user_locked
from accounts.login_security.notifications import (
    is_email_configured,
    summarize_user_agent,
)
from accounts.login_security.operations import (
    OperationStatus,
    reset_two_factor_by_admin,
    unlock_account_by_admin,
    update_security_setting,
)
from accounts.login_security.totp import has_confirmed_totp
from accounts.login_security.user_lookup import (
    get_admin_notification_recipients,
    get_security_setting,
)
from accounts.models import AccountLock, CustomUser, LoginAttempt, SecuritySetting
from accounts.security_status import count_active_admins
from back_navigator.back_navigator import BackNavigator

JST = ZoneInfo("Asia/Tokyo")
REASON_NOTE_MAX_LENGTH = 500
REASON_NOTE_TOO_LONG_MESSAGE = "理由メモは500文字以内で入力してください。"

_SECURITY_SETTING_FIELD_KEYS = (
    "two_factor_enforcement",
    "login_notification_level",
    "lockout_threshold",
    "lockout_minutes",
    "ip_block_threshold",
    "trusted_device_days",
    "notify_admins_on_account_lock",
    "notify_admins_on_ip_block",
)


def _security_setting_initial(setting: SecuritySetting) -> dict[str, object]:
    return {key: getattr(setting, key) for key in _SECURITY_SETTING_FIELD_KEYS}


def _build_security_status_context(setting: SecuritySetting) -> dict[str, object]:
    """セキュリティ設定画面に表示する運用状況と各種警告を固定数のクエリで集計する。"""
    now = timezone.now()
    active_user_count = CustomUser.objects.filter(is_active=True).count()
    enrolled_user_count = (
        TOTPDevice.objects.filter(confirmed=True, user__is_active=True)
        .values("user_id")
        .distinct()
        .count()
    )
    locked_user_count = AccountLock.objects.filter(locked_until__gt=now).count()
    admin_count = count_active_admins()
    admin_recipient_count = len(get_admin_notification_recipients())
    email_configured = is_email_configured()

    return {
        "active_user_count": active_user_count,
        "enrolled_user_count": enrolled_user_count,
        "locked_user_count": locked_user_count,
        "admin_count": admin_count,
        "admin_count_warning": admin_count <= 1,
        "admin_recipient_count": admin_recipient_count,
        "no_admin_recipient_warning": admin_recipient_count == 0,
        "smtp_unconfigured_warning": not email_configured,
        "updated_at": timezone.localtime(setting.updated_at, JST),
        "updated_by": setting.updated_by,
    }


@method_decorator(never_cache, name="dispatch")
class SecuritySettingView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """セキュリティ設定画面（accounts:security_setting、GET/POST、仕様書 §8 / §10 #10）。"""

    permission_required = "accounts.manage_security_setting"
    template_name = "accounts/security_setting.html"

    def _get_setting_with_user(self) -> SecuritySetting:
        setting = SecuritySetting.objects.select_related("updated_by").filter(id=1).first()
        if setting is not None:
            return setting
        return get_security_setting()

    def _render(self, request, form: SecuritySettingForm, setting: SecuritySetting):
        ctx = {
            "form": form,
            "setting": setting,
            "active_app": "user_mgmt",
            "active_menu": "accounts:security_setting",
            **_build_security_status_context(setting),
        }
        return render(request, self.template_name, ctx)

    def get(self, request):
        setting = self._get_setting_with_user()
        form = SecuritySettingForm(initial=_security_setting_initial(setting))
        return self._render(request, form, setting)

    def post(self, request):
        setting = self._get_setting_with_user()
        form = SecuritySettingForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, setting)

        updates = {key: form.cleaned_data[key] for key in _SECURITY_SETTING_FIELD_KEYS}
        res = update_security_setting(request.user, updates)

        if res.status == OperationStatus.PERMISSION_DENIED:
            raise PermissionDenied("セキュリティ設定を変更する権限がありません。")

        if res.status == OperationStatus.VALIDATION_ERROR:
            for field, msg in res.field_errors.items():
                if field in form.fields:
                    form.add_error(field, msg)
                else:
                    form.add_error(None, msg)
            return self._render(request, form, setting)

        if not res.changed_fields:
            messages.info(request, "セキュリティ設定に変更はありませんでした。")
        else:
            messages.success(request, "セキュリティ設定を保存しました。")
        return redirect("accounts:security_setting")


@method_decorator(never_cache, name="dispatch")
class TwoFactorResetView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """管理者による二段階認証リセット画面（accounts:two_factor_reset、GET/POST、仕様書 §6.7 / §10 #8）。"""

    permission_required = "accounts.reset_two_factor"
    template_name = "accounts/two_factor_reset.html"

    def _get_target_user(self, user_id: int) -> CustomUser:
        return get_object_or_404(
            CustomUser.objects.select_related(
                "role", "person", "person__primary_contact"
            ),
            pk=user_id,
        )

    def _render_confirm(
        self,
        request,
        target_user: CustomUser,
        *,
        is_self: bool,
        reason_note: str = "",
        error_message: str = "",
        status: int = 200,
    ):
        return render(
            request,
            self.template_name,
            {
                "target_user": target_user,
                "is_self": is_self,
                "enrolled": has_confirmed_totp(target_user),
                "locked": is_user_locked(target_user),
                "reason_note": reason_note,
                "error_message": error_message,
                "back": BackNavigator(request),
                "active_app": "user_mgmt",
                "active_menu": "accounts:user_list",
            },
            status=status,
        )

    def get(self, request, user_id: int):
        target_user = self._get_target_user(user_id)
        is_self = target_user.pk == request.user.pk
        return self._render_confirm(
            request,
            target_user,
            is_self=is_self,
            status=400 if is_self else 200,
        )

    def post(self, request, user_id: int):
        target_user = self._get_target_user(user_id)
        reason_note = request.POST.get("reason_note", "")

        if target_user.pk == request.user.pk:
            return self._render_confirm(
                request,
                target_user,
                is_self=True,
                reason_note=reason_note,
                status=400,
            )

        if len(reason_note) > REASON_NOTE_MAX_LENGTH:
            return self._render_confirm(
                request,
                target_user,
                is_self=False,
                reason_note=reason_note,
                error_message=REASON_NOTE_TOO_LONG_MESSAGE,
                status=400,
            )

        res = reset_two_factor_by_admin(
            request.user,
            target_user,
            reason_note=reason_note,
        )
        if res.status == OperationStatus.PERMISSION_DENIED:
            raise PermissionDenied("二段階認証をリセットする権限がありません。")
        if res.status == OperationStatus.NOT_ALLOWED:
            return self._render_confirm(
                request,
                target_user,
                is_self=True,
                reason_note=reason_note,
                status=400,
            )

        messages.success(
            request,
            f"ユーザー「{target_user.username}」の二段階認証をリセットしました。",
        )
        return redirect("accounts:user_detail", user_id=target_user.pk)


@method_decorator(never_cache, name="dispatch")
class AccountUnlockView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """管理者によるアカウントロック手動解除（accounts:account_unlock、POST のみ、仕様書 §5.1 / §10 #9）。"""

    permission_required = "accounts.reset_two_factor"
    http_method_names = ["post", "options"]

    def post(self, request, user_id: int):
        target_user = get_object_or_404(CustomUser, pk=user_id)

        if not is_user_locked(target_user):
            messages.info(
                request,
                f"ユーザー「{target_user.username}」は現在ロックされていません。",
            )
            return redirect("accounts:user_detail", user_id=target_user.pk)

        res = unlock_account_by_admin(request.user, target_user)
        if res.status == OperationStatus.PERMISSION_DENIED:
            raise PermissionDenied("ロック解除を行う権限がありません。")

        messages.success(
            request,
            f"ユーザー「{target_user.username}」のアカウントロックを解除しました。",
        )
        return redirect("accounts:user_detail", user_id=target_user.pk)


@method_decorator(never_cache, name="dispatch")
class LoginAttemptListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """ログイン試行記録一覧（accounts:login_attempt_list、GET のみ、仕様書 §5.6 / §10 #11）。"""

    permission_required = "accounts.view_loginattempt"
    model = LoginAttempt
    template_name = "accounts/login_attempt_list.html"
    context_object_name = "attempts"
    paginate_by = 20

    def get_queryset(self):
        qs = LoginAttempt.objects.select_related("user").order_by("-created_at", "-id")
        self.filter_errors = []

        result = (self.request.GET.get("result") or "").strip()
        if result:
            if result not in LoginAttempt.Result.values:
                self.filter_errors.append("結果の指定が不正です。")
                return qs.none()
            qs = qs.filter(result=result)

        username = (self.request.GET.get("username") or "").strip()
        if username:
            qs = qs.filter(username_input__icontains=username)

        ip_raw = (self.request.GET.get("ip") or "").strip()
        if ip_raw:
            try:
                normalized_ip = str(ipaddress.ip_address(ip_raw))
            except ValueError:
                self.filter_errors.append("IP アドレスの形式が不正です。")
                return qs.none()
            qs = qs.filter(ip_address=normalized_ip)

        date_from_raw = (self.request.GET.get("date_from") or "").strip()
        if date_from_raw:
            try:
                d_from = date.fromisoformat(date_from_raw)
            except ValueError:
                self.filter_errors.append("開始日の形式が不正です（YYYY-MM-DD）。")
                return qs.none()
            start_dt = datetime.combine(d_from, time.min, tzinfo=JST)
            qs = qs.filter(created_at__gte=start_dt)

        date_to_raw = (self.request.GET.get("date_to") or "").strip()
        if date_to_raw:
            try:
                d_to = date.fromisoformat(date_to_raw)
            except ValueError:
                self.filter_errors.append("終了日の形式が不正です（YYYY-MM-DD）。")
                return qs.none()
            end_dt_exclusive = datetime.combine(
                d_to + timedelta(days=1), time.min, tzinfo=JST
            )
            qs = qs.filter(created_at__lt=end_dt_exclusive)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = [
            {
                "created_at": timezone.localtime(attempt.created_at, JST),
                "username_input": attempt.username_input,
                "username": attempt.user.username if attempt.user else None,
                "ip_address": attempt.ip_address,
                "browser_summary": summarize_user_agent(attempt.user_agent),
                "result": attempt.result,
                "result_label": attempt.get_result_display(),
                "second_factor_label": (
                    attempt.get_second_factor_display() if attempt.second_factor else "-"
                ),
                "is_new_device_label": (
                    ("はい" if attempt.is_new_device else "いいえ")
                    if attempt.result == LoginAttempt.Result.SUCCESS
                    else "-"
                ),
            }
            for attempt in context["attempts"]
        ]
        context["attempt_rows"] = rows
        context["result_choices"] = LoginAttempt.Result.choices
        context["filter_result"] = (self.request.GET.get("result") or "").strip()
        context["filter_username"] = (self.request.GET.get("username") or "").strip()
        context["filter_ip"] = (self.request.GET.get("ip") or "").strip()
        context["filter_date_from"] = (self.request.GET.get("date_from") or "").strip()
        context["filter_date_to"] = (self.request.GET.get("date_to") or "").strip()
        context["filter_errors"] = getattr(self, "filter_errors", [])
        context["active_app"] = "user_mgmt"
        context["active_menu"] = "accounts:login_attempt_list"
        return context
