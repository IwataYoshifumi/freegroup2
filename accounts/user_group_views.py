"""ユーザーグループ（UserGroup）CRUD View（仕様書 v1.6 §2.3.2 No.10〜16）。"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import UserGroupForm
from accounts.models import UserGroup
from actionlogs.constants import (
    USER_GROUP_CREATED,
    USER_GROUP_DELETED,
    USER_GROUP_MEMBER_ADDED,
    USER_GROUP_MEMBER_REMOVED,
    USER_GROUP_UPDATED,
)
from actionlogs.models import ActionLog

User = get_user_model()


class UserGroupListView(LoginRequiredMixin, ListView):
    """ユーザーグループ一覧（仕様書 v1.6 §2.3.2 No.10）。"""

    model = UserGroup
    template_name = "accounts/user_group_list.html"
    context_object_name = "user_groups"

    def get_queryset(self):
        return UserGroup.objects.all().annotate(member_count=Count("members")).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_add"] = user.is_superuser or user.has_perm("accounts.add_usergroup")
        context["active_menu"] = "accounts:user_group_list"
        return context


class UserGroupCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """ユーザーグループ新規作成（仕様書 v1.6 §2.3.2 No.11）。"""

    permission_required = "accounts.add_usergroup"
    model = UserGroup
    form_class = UserGroupForm
    template_name = "accounts/user_group_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        with transaction.atomic():
            response = super().form_valid(form)
            ActionLog.record(
                user=self.request.user,
                action=USER_GROUP_CREATED,
                content_object=self.object,
                object_repr=str(self.object),
            )
            messages.success(self.request, f"ユーザーグループ「{self.object.name}」を作成しました。")
            return response

    def get_success_url(self):
        return reverse("user_groups:user_group_detail", kwargs={"pk": self.object.pk})


class UserGroupDetailView(LoginRequiredMixin, DetailView):
    """ユーザーグループ詳細・メンバー一覧（仕様書 v1.6 §2.3.2 No.12）。"""

    model = UserGroup
    template_name = "accounts/user_group_detail.html"
    context_object_name = "user_group"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_edit"] = user.is_superuser or user.has_perm("accounts.change_usergroup")
        context["can_delete"] = user.is_superuser or user.has_perm("accounts.delete_usergroup")
        context["members"] = self.object.members.filter(is_active=True).order_by("username")
        context["available_users"] = (
            User.objects.filter(is_active=True)
            .exclude(pk__in=self.object.members.all())
            .order_by("username")
        )
        return context


class UserGroupUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """ユーザーグループ編集（仕様書 v1.6 §2.3.2 No.13）。"""

    permission_required = "accounts.change_usergroup"
    model = UserGroup
    form_class = UserGroupForm
    template_name = "accounts/user_group_form.html"

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            ActionLog.record(
                user=self.request.user,
                action=USER_GROUP_UPDATED,
                content_object=self.object,
                object_repr=str(self.object),
            )
            messages.success(self.request, f"ユーザーグループ「{self.object.name}」を更新しました。")
            return response

    def get_success_url(self):
        return reverse("user_groups:user_group_detail", kwargs={"pk": self.object.pk})


class UserGroupDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ユーザーグループ削除（仕様書 v1.6 §2.3.2 No.14）。"""

    permission_required = "accounts.delete_usergroup"

    def post(self, request, pk):
        user_group = get_object_or_404(UserGroup, pk=pk)
        with transaction.atomic():
            name = user_group.name
            ActionLog.record(
                user=request.user,
                action=USER_GROUP_DELETED,
                object_repr=name,
            )
            user_group.delete()
            messages.success(request, f"ユーザーグループ「{name}」を削除しました。")
            return redirect("user_groups:user_group_list")


class UserGroupAddMemberView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ユーザーグループへのメンバー追加（仕様書 v1.6 §2.3.2 No.15）。"""

    permission_required = "accounts.change_usergroup"

    def post(self, request, pk):
        user_group = get_object_or_404(UserGroup, pk=pk)
        user_id = request.POST.get("user_id")
        target_user = get_object_or_404(User, pk=user_id)

        with transaction.atomic():
            user_group.members.add(target_user)
            ActionLog.record(
                user=request.user,
                action=USER_GROUP_MEMBER_ADDED,
                content_object=user_group,
                object_repr=str(user_group),
                data={"user_id": target_user.id, "username": target_user.username},
            )
            messages.success(request, f"「{target_user.username}」をグループに追加しました。")

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"status": "ok", "user_id": target_user.id, "username": target_user.username})
        return redirect("user_groups:user_group_detail", pk=pk)


class UserGroupRemoveMemberView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ユーザーグループからのメンバー解除（仕様書 v1.6 §2.3.2 No.16）。"""

    permission_required = "accounts.change_usergroup"

    def post(self, request, pk):
        user_group = get_object_or_404(UserGroup, pk=pk)
        user_id = request.POST.get("user_id")
        target_user = get_object_or_404(User, pk=user_id)

        with transaction.atomic():
            user_group.members.remove(target_user)
            ActionLog.record(
                user=request.user,
                action=USER_GROUP_MEMBER_REMOVED,
                content_object=user_group,
                object_repr=str(user_group),
                data={"user_id": target_user.id, "username": target_user.username},
            )
            messages.success(request, f"「{target_user.username}」をグループから解除しました。")

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"status": "ok", "user_id": target_user.id})
        return redirect("user_groups:user_group_detail", pk=pk)
