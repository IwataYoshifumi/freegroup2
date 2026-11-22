"""パスワード変更画面・完了画面・管理画面リダイレクトビュー（仕様書 v1.0 §5 / §9 / §10 / §12 / §13）。"""

from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from back_navigator.back_navigator import BackNavigator

from .forms import SelfPasswordChangeForm
from .password_security.eligibility import is_password_self_service_eligible
from .password_security.password_change import (
    NOT_ELIGIBLE_MESSAGE,
    PasswordChangeStatus,
    change_password,
)

PASSWORD_MISMATCH_MESSAGE = "新しいパスワードと確認用パスワードが一致しません。"


@method_decorator(never_cache, name="dispatch")
class PasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    """本人によるパスワード変更画面（accounts:password_change、仕様書 §5 / §9 / §13-15）。

    標準の auth_views.PasswordChangeView は画面・デコレータ（LoginRequired /
    sensitive_post_parameters / csrf_protect）の枠組みのみ継承し、
    検証・保存・セッション維持/切断・操作ログ・通知メールはすべて Step 3 の
    change_password() に委譲する（form.save() は一切呼ばない）。
    """

    form_class = SelfPasswordChangeForm
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")

    def _render_not_eligible(self, request, *, status: int = 200):
        return render(
            request,
            self.template_name,
            {
                "eligible": False,
                "not_eligible_message": NOT_ELIGIBLE_MESSAGE,
                "back": BackNavigator(request),
            },
            status=status,
        )

    def get(self, request, *args, **kwargs):
        if not is_password_self_service_eligible(request.user):
            return self._render_not_eligible(request, status=200)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not is_password_self_service_eligible(request.user):
            return self._render_not_eligible(request, status=403)
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["eligible"] = is_password_self_service_eligible(self.request.user)
        context["not_eligible_message"] = NOT_ELIGIBLE_MESSAGE
        context["back"] = BackNavigator(self.request)
        return context

    def get_success_url(self):
        return BackNavigator(self.request).append_url(str(self.success_url))

    def form_valid(self, form):
        old_password = form.cleaned_data.get("old_password", "")
        new_password1 = form.cleaned_data.get("new_password1", "")
        new_password2 = form.cleaned_data.get("new_password2", "")
        passwords_match = new_password1 == new_password2

        # 検証順（§5.2 順1〜3 → 順4 2回一致 → 順5 強度 → 順6 現在と同じでない）を
        # Step 3 サービス（change_password）を変更せずに満たすため、
        # 2回一致しない場合も一旦 new_password="" を渡して順1〜3（対象外→ロック→
        # 現在のパスワード誤り/空の失敗計上）を先に評価・記録させる。
        res = change_password(
            self.request.user,
            current_password=old_password,
            new_password=new_password1 if passwords_match else "",
            request=self.request,
        )

        if res.status == PasswordChangeStatus.NOT_ELIGIBLE:
            return self._render_not_eligible(self.request, status=403)
        if res.status == PasswordChangeStatus.LOCKED:
            form.add_error(None, res.errors[0])
            return self.form_invalid(form)
        if res.status == PasswordChangeStatus.INVALID_CURRENT_PASSWORD:
            form.add_error("old_password", res.errors[0])
            return self.form_invalid(form)

        # 順1〜3 を通過した後に、順4（2回一致）を評価する
        if not passwords_match:
            form.add_error("new_password2", PASSWORD_MISMATCH_MESSAGE)
            return self.form_invalid(form)

        # 順5・順6（強度不足・現在と同じ）
        if res.status in (
            PasswordChangeStatus.VALIDATION_ERROR,
            PasswordChangeStatus.SAME_AS_CURRENT,
        ):
            for msg in res.errors:
                form.add_error("new_password1", msg)
            return self.form_invalid(form)

        return redirect(self.get_success_url())


@method_decorator(never_cache, name="dispatch")
class PasswordChangeDoneView(LoginRequiredMixin, auth_views.PasswordChangeDoneView):
    """本人によるパスワード変更完了画面（accounts:password_change_done、仕様書 §5.1 / §9）。"""

    template_name = "accounts/password_change_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back"] = BackNavigator(self.request)
        return context


class AdminPasswordChangeRedirectView(View):
    """管理画面 /admin/password_change/ を共通変更画面へ誘導する（仕様書 §12 / §13-5）。"""

    http_method_names = ["get", "head"]

    def get(self, request):
        return redirect("accounts:password_change")


class AdminPasswordChangeDoneRedirectView(View):
    """管理画面 /admin/password_change/done/ を共通変更完了画面へ誘導する（仕様書 §12）。"""

    http_method_names = ["get", "head"]

    def get(self, request):
        return redirect("accounts:password_change_done")
