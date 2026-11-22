"""パスワード変更画面・完了画面・管理画面リダイレクトビュー（仕様書 v1.0 §5 / §9 / §10 / §12 / §13）。"""

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import FormView

from back_navigator.back_navigator import BackNavigator

from .forms import (
    SelfPasswordChangeForm,
    SelfPasswordResetConfirmForm,
    SelfPasswordResetRequestForm,
)
from .password_security.eligibility import is_password_self_service_eligible
from .password_security.password_change import (
    NOT_ELIGIBLE_MESSAGE,
    PasswordChangeStatus,
    change_password,
)
from .password_security.password_reset import (
    INVALID_RESET_LINK_MESSAGE,
    PasswordResetCompleteStatus,
    complete_password_reset,
    request_password_reset,
    verify_password_reset_token,
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


@method_decorator([login_not_required, csrf_protect], name="dispatch")
class PasswordResetView(auth_views.PasswordResetView):
    """パスワードリセット申請画面（accounts:password_reset、仕様書 §6.2 / §9 / §13-2・16）。

    標準の auth_views.PasswordResetView は画面の枠組みのみ継承し、
    判定・申請記録・操作ログ・メール送信は Step 3 の request_password_reset() に委譲する。
    結果（送信・該当なし・対象外・制限超過・IP遮断・メール送信失敗）にかかわらず
    同じ受付画面（accounts:password_reset_done）へ遷移する。
    """

    form_class = SelfPasswordResetRequestForm
    template_name = "accounts/password_reset_form.html"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        request_password_reset(
            form.cleaned_data.get("email", ""),
            request=self.request,
        )
        return HttpResponseRedirect(self.get_success_url())


@method_decorator(login_not_required, name="dispatch")
class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    """パスワードリセット申請受付画面（accounts:password_reset_done、仕様書 §6.2 / §9）。"""

    template_name = "accounts/password_reset_done.html"


@method_decorator(
    [login_not_required, sensitive_post_parameters(), never_cache],
    name="dispatch",
)
class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """新パスワード設定画面（accounts:password_reset_confirm、仕様書 §6.3〜§6.5 / §9 / §13-1・7）。

    標準のセッション退避→トークンなし URL へのリダイレクトの枠組みを維持しつつ、
    トークン検証・完了処理は Step 3 の verify_password_reset_token() /
    complete_password_reset() に委譲する（自動ログインは必ず無効）。
    """

    form_class = SelfPasswordResetConfirmForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")
    post_reset_login = False
    reset_url_token = "set-password"

    def dispatch(self, request, *args, **kwargs):
        response = self._dispatch_confirm(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    def _dispatch_confirm(self, request, *args, **kwargs):
        if "uidb64" not in kwargs or "token" not in kwargs:
            raise ImproperlyConfigured(
                "The URL path must contain 'uidb64' and 'token' parameters."
            )

        self.validlink = False
        self.user = None
        self._actual_reset_token = None

        uidb64 = kwargs["uidb64"]
        url_token = kwargs["token"]

        if url_token == self.reset_url_token:
            session_token = request.session.get(INTERNAL_RESET_SESSION_TOKEN)
            verification = verify_password_reset_token(uidb64, session_token)
            if verification.is_valid and verification.user is not None:
                self.validlink = True
                self.user = verification.user
                self._actual_reset_token = session_token
                return FormView.dispatch(self, request, *args, **kwargs)
        else:
            verification = verify_password_reset_token(uidb64, url_token)
            if verification.is_valid and verification.user is not None:
                request.session[INTERNAL_RESET_SESSION_TOKEN] = url_token
                redirect_url = reverse(
                    "accounts:password_reset_confirm",
                    kwargs={"uidb64": uidb64, "token": self.reset_url_token},
                )
                return HttpResponseRedirect(redirect_url)

        return self.render_to_response(self.get_context_data())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invalid_link_message"] = INVALID_RESET_LINK_MESSAGE
        return context

    def form_valid(self, form):
        uidb64 = self.kwargs["uidb64"]
        actual_token = getattr(
            self, "_actual_reset_token", None
        ) or self.request.session.get(INTERNAL_RESET_SESSION_TOKEN)

        # 検証順：1. リンクの有効性 → 2. 2回一致 → 3. 強度・現在と同じでない
        verification = verify_password_reset_token(uidb64, actual_token)
        if not verification.is_valid or verification.user is None:
            self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN, None)
            self.validlink = False
            self.user = None
            return self.render_to_response(self.get_context_data())

        new_password1 = form.cleaned_data.get("new_password1", "")
        new_password2 = form.cleaned_data.get("new_password2", "")
        if new_password1 != new_password2:
            form.add_error("new_password2", PASSWORD_MISMATCH_MESSAGE)
            return self.form_invalid(form)

        res = complete_password_reset(
            uidb64=uidb64,
            token=actual_token,
            new_password=new_password1,
            request=self.request,
        )
        if res.status == PasswordResetCompleteStatus.INVALID_LINK:
            self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN, None)
            self.validlink = False
            self.user = None
            return self.render_to_response(self.get_context_data())

        if res.status in (
            PasswordResetCompleteStatus.VALIDATION_ERROR,
            PasswordResetCompleteStatus.SAME_AS_CURRENT,
        ):
            for msg in res.errors:
                form.add_error("new_password1", msg)
            return self.form_invalid(form)

        self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN, None)
        return HttpResponseRedirect(self.get_success_url())


@method_decorator(login_not_required, name="dispatch")
class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    """パスワードリセット完了画面（accounts:password_reset_complete、仕様書 §6.1 / §6.5 / §9）。"""

    template_name = "accounts/password_reset_complete.html"
