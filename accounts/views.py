import urllib.parse

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView

from back_navigator.back_navigator import BackNavigator
from persons.models import Person

from .models import CustomUser
from .services import link_user_to_person, retire_user, unlink_user_from_person


class ProfileView(LoginRequiredMixin, View):
    """ログインユーザ自身のプロフィール画面（仕様書 §12.8）。"""

    template_name = "accounts/profile.html"

    def get(self, request):
        return render(request, self.template_name)


class LinkUserPersonConfirmView(LoginRequiredMixin, View):
    """User-Person 紐付け確認画面。GET: 確認表示 / POST: 紐付けまたは解除を実行。

    [性質] GET=準関数（DB読み取りのみ）/ POST=副作用あり（DB書込）
    [権限] LoginRequiredMixin のみ（本フローは常に request.user 自身への操作）
    """

    template_name = "accounts/link_user_person_confirm.html"

    def get(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        return render(request, self.template_name, {
            "person": person,
            "back": BackNavigator(request),
        })

    def post(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        action = request.POST.get("action")
        try:
            if action == "link":
                link_user_to_person(operator=request.user, user=request.user, person=person)
                messages.success(request, "紐付けました")
            elif action == "unlink":
                unlink_user_from_person(operator=request.user, user=request.user)
                messages.success(request, "紐付けを解除しました")
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect("accounts:link_user_person_confirm", person_id=person_id)
        return redirect("accounts:profile")


class StartLinkFlowView(LoginRequiredMixin, View):
    """「Person を探して紐付ける」フロー開始 View。

    [性質] 副作用なし（messages.info + redirect のみ）
    [権限] LoginRequiredMixin（本人フロー開始のみ）
    """

    def get(self, request):
        messages.info(
            request,
            "紐付け対象の Person を表示しています。リストから自分の Person を選んでください。",
        )
        params = urllib.parse.urlencode([
            ("email", request.user.email),
            ("searched", "1"),
            ("status", "active"),
        ])
        return redirect(f"{reverse('persons:person_list')}?{params}")


class LinkUserPersonView(LoginRequiredMixin, View):
    """User と Person を紐付ける View（仕様書 §12.7）。

    権限: 本人（request.user == target_user）または accounts.link_user_to_person Permission。
    POST のみ受け付ける。
    """

    def post(self, request, user_id, person_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        person = get_object_or_404(Person, pk=person_id)

        if request.user != target_user and not request.user.has_perm(
            "accounts.link_user_to_person"
        ):
            raise PermissionDenied("紐付けの権限がありません")

        try:
            link_user_to_person(
                operator=request.user, user=target_user, person=person
            )
            messages.success(request, "紐付けました")
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect("home")


class UnlinkUserPersonView(LoginRequiredMixin, View):
    """User と Person の紐付けを解除する View（仕様書 §12.7）。

    権限: 本人または accounts.link_user_to_person Permission。POST のみ。
    """

    def post(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)

        if request.user != target_user and not request.user.has_perm(
            "accounts.link_user_to_person"
        ):
            raise PermissionDenied("紐付け解除の権限がありません")

        try:
            unlink_user_from_person(operator=request.user, user=target_user)
            messages.success(request, "紐付けを解除しました")
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect("home")


class UserListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """ユーザ管理一覧 View（退職者表示切替対応）。"""

    model = CustomUser
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    permission_required = "accounts.retire_user"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.GET.get("show_retired") == "1":
            return qs
        return qs.filter(is_active=True)


class UserDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """ユーザ詳細 View。"""

    model = CustomUser
    template_name = "accounts/user_detail.html"
    pk_url_kwarg = "user_id"
    permission_required = "accounts.retire_user"


class RetireUserView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """退職処理の専用 View（管理者業務 UI、仕様書 §12.6 B）。

    GET: 後継者選択フォーム表示
    POST: retire_user() 実行 → ユーザ管理一覧にリダイレクト
    """

    permission_required = "accounts.retire_user"
    template_name = "accounts/retire_user.html"

    def get(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        successor_choices = CustomUser.objects.filter(is_active=True).exclude(pk=user_id)
        return render(request, self.template_name, {
            "target_user": target_user,
            "successor_choices": successor_choices,
        })

    def post(self, request, user_id):
        target_user = get_object_or_404(CustomUser, pk=user_id)
        successor_id = request.POST.get("successor")
        successor = get_object_or_404(CustomUser, pk=successor_id)
        try:
            retire_user(user=target_user, successor=successor)
            messages.success(request, f"{target_user.username} の退職処理を完了しました")
        except Exception as e:
            messages.error(request, f"退職処理に失敗しました: {e}")
        return redirect("accounts:user_list")
