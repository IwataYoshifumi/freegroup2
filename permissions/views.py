import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.models import Department, UserGroup
from actionlogs.constants import (
    ACCESS_LIST_CREATED,
    ACCESS_LIST_DELETED,
    ACCESS_LIST_UPDATED,
    ACL_ENTRY_CREATED,
    ACL_ENTRY_DELETED,
    ACL_ENTRY_REORDERED,
    ACL_ENTRY_UPDATED,
)
from actionlogs.models import ActionLog
from permissions.forms import AccessListForm, ACLEntryForm
from permissions.models import AccessList, ACLEntry, AccessListUserRole
from permissions.services import AccessListService


def _build_form_context(request, access_list=None):
    """インラインエントリ編集用のマスターデータおよび初期エントリを構築。"""
    User = get_user_model()
    users_data = [
        {
            "id": str(u.pk),
            "name": f"{u.display_name} ({u.username})" if u.display_name != u.username else u.username,
        }
        for u in User.objects.filter(is_active=True).order_by("username")
    ]

    departments = Department.objects.filter(is_active=True).select_related("parent").order_by("sort_order", "name")
    dept_map = {d.pk: d for d in departments}

    def get_dept_hierarchy_name(d):
        parts = []
        curr = d
        visited = set()
        while curr and curr.pk not in visited:
            visited.add(curr.pk)
            parts.append(curr.name)
            curr = dept_map.get(curr.parent_id)
        return " > ".join(reversed(parts))

    departments_data = [
        {"id": str(d.pk), "name": get_dept_hierarchy_name(d)}
        for d in departments
    ]

    user_groups_data = [
        {"id": str(ug.pk), "name": ug.name}
        for ug in UserGroup.objects.all().order_by("name")
    ]

    initial_entries = []
    if request.method == "POST" and "entries_json" in request.POST:
        try:
            initial_entries = json.loads(request.POST["entries_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    elif access_list and access_list.pk:
        for entry in access_list.entries.all().order_by("order", "created_at", "id"):
            model_name = entry.target_content_type.model.lower()
            if model_name == "customuser":
                ttype = "user"
            elif model_name == "department":
                ttype = "department"
            elif model_name == "usergroup":
                ttype = "user_group"
            else:
                ttype = "user"
            initial_entries.append({
                "id": str(entry.pk),
                "order": entry.order,
                "permission_level": entry.permission_level,
                "target_type": ttype,
                "target_id": str(entry.target_object_id),
            })

    return {
        "users_data": users_data,
        "departments_data": departments_data,
        "user_groups_data": user_groups_data,
        "initial_entries": initial_entries,
    }


class AccessListListView(LoginRequiredMixin, ListView):
    """アクセスリスト一覧（仕様書 v1.6 §2.3.1 No.1）。"""

    model = AccessList
    template_name = "permissions/access_list_list.html"
    context_object_name = "access_lists"

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.has_perm("permissions.view_all_access_lists"):
            return AccessList.objects.all().order_by("name")
        accessible_ids = AccessListService.accessible_access_list_ids(user)
        return AccessList.objects.filter(id__in=accessible_ids).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_add"] = user.is_superuser or user.has_perm("permissions.add_accesslist")
        context["active_menu"] = "permissions:access_list_list"
        return context


class AccessListCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """アクセスリスト新規作成（仕様書 v1.6 §2.3.1 No.2、旧FG準拠インライン編集）。"""

    permission_required = "permissions.add_accesslist"
    model = AccessList
    form_class = AccessListForm
    template_name = "permissions/access_list_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_build_form_context(self.request, None))
        context["active_menu"] = "permissions:access_list_list"
        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        self.object = form.save(user=self.request.user)
        user_role = AccessListUserRole.objects.filter(access_list=self.object, user=self.request.user).first()
        if user_role and user_role.role == ACLEntry.PermissionLevel.ADMIN:
            messages.success(self.request, f"アクセスリスト「{self.object.name}」を作成しました。")
        else:
            messages.warning(self.request, "設定を保存しました。注意: あなたはこのアクセスリストの管理者ではなくなりました。")
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        return reverse("permissions:access_list_detail", kwargs={"pk": self.object.pk})


class AccessListDetailView(LoginRequiredMixin, DetailView):
    """アクセスリスト詳細（仕様書 v1.6 §2.3.1 No.3、旧FG準拠ロール別表示・設定先表示）。"""

    model = AccessList
    template_name = "permissions/access_list_detail.html"
    context_object_name = "access_list"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["active_menu"] = "permissions:access_list_list"

        # 1. 定義エントリ一覧
        context["entries"] = self.object.entries.select_related("target_content_type").order_by(
            "order", "created_at", "id"
        )

        # 2. 確定権限ユーザー（評価キャッシュ）のロール別グループ化
        user_roles = (
            self.object.user_roles.select_related(
                "user", "user__department", "user__person", "user__person__primary_contact"
            ).order_by("user__username")
        )
        admin_roles = []
        editor_roles = []
        viewer_roles = []
        for ur in user_roles:
            if ur.role == "admin":
                admin_roles.append(ur)
            elif ur.role == "editor":
                editor_roles.append(ur)
            elif ur.role == "viewer":
                viewer_roles.append(ur)

        context["admin_roles"] = admin_roles
        context["editor_roles"] = editor_roles
        context["viewer_roles"] = viewer_roles
        context["total_role_users_count"] = len(user_roles)

        # 3. 権限フラグ
        context["can_edit"] = user.is_superuser or user.has_perm("permissions.change_accesslist")
        context["can_delete"] = user.is_superuser or user.has_perm("permissions.delete_accesslist")

        # 4. 設定先（参照先リスト）一覧
        context["deal_lists"] = self.object.deal_lists.select_related("created_by").order_by("name")
        context["person_lists"] = self.object.person_lists.select_related("created_by").order_by("name")
        context["deal_lists_count"] = context["deal_lists"].count()
        context["person_lists_count"] = context["person_lists"].count()

        return context


class AccessListUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """アクセスリスト編集（仕様書 v1.6 §2.3.1 No.4、旧FG準拠インライン編集）。"""

    permission_required = "permissions.change_accesslist"
    model = AccessList
    form_class = AccessListForm
    template_name = "permissions/access_list_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_build_form_context(self.request, self.object))
        context["active_menu"] = "permissions:access_list_list"
        return context

    def form_valid(self, form):
        self.object = form.save(user=self.request.user)
        user_role = AccessListUserRole.objects.filter(access_list=self.object, user=self.request.user).first()
        if user_role and user_role.role == ACLEntry.PermissionLevel.ADMIN:
            messages.success(self.request, f"アクセスリスト「{self.object.name}」を更新しました。")
        else:
            messages.warning(self.request, "設定を保存しました。注意: あなたはこのアクセスリストの管理者ではなくなりました。")
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        return reverse("permissions:access_list_detail", kwargs={"pk": self.object.pk})


class AccessListDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """アクセスリスト削除（仕様書 v1.6 §2.3.1 No.5）。
    DealList／PersonListからPROTECT参照されている間は削除不可。
    """

    permission_required = "permissions.delete_accesslist"

    def post(self, request, pk):
        access_list = get_object_or_404(AccessList, pk=pk)
        deal_count = access_list.deal_lists.count()
        person_count = access_list.person_lists.count()

        if deal_count > 0 or person_count > 0:
            messages.error(
                request,
                f"このアクセスリストは案件リスト（{deal_count}件）またはパーソンリスト（{person_count}件）から参照されているため削除できません。",
            )
            return redirect("permissions:access_list_detail", pk=pk)

        with transaction.atomic():
            name = access_list.name
            ActionLog.record(
                user=request.user,
                action=ACCESS_LIST_DELETED,
                object_repr=name,
            )
            access_list.delete()
            messages.success(request, f"アクセスリスト「{name}」を削除しました。")
            return redirect("permissions:access_list_list")


class ACLEntryCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ACLEntry新規追加（仕様書 v1.6 §2.3.1 No.6）。"""

    permission_required = "permissions.change_accesslist"

    def get(self, request, pk):
        access_list = get_object_or_404(AccessList, pk=pk)
        form = ACLEntryForm(access_list=access_list)
        return render(
            request,
            "permissions/acl_entry_form.html",
            {"form": form, "access_list": access_list, "is_new": True},
        )

    def post(self, request, pk):
        access_list = get_object_or_404(AccessList, pk=pk)
        form = ACLEntryForm(request.POST, access_list=access_list)
        if form.is_valid():
            with transaction.atomic():
                entry = form.save()
                ActionLog.record(
                    user=request.user,
                    action=ACL_ENTRY_CREATED,
                    content_object=entry,
                    object_repr=str(entry),
                )
                messages.success(request, "エントリを追加しました。")
                return redirect("permissions:access_list_detail", pk=pk)
        return render(
            request,
            "permissions/acl_entry_form.html",
            {"form": form, "access_list": access_list, "is_new": True},
        )


class ACLEntryUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ACLEntry編集（仕様書 v1.6 §2.3.1 No.7）。"""

    permission_required = "permissions.change_accesslist"

    def get(self, request, pk, entry_pk):
        access_list = get_object_or_404(AccessList, pk=pk)
        entry = get_object_or_404(ACLEntry, pk=entry_pk, access_list=access_list)
        form = ACLEntryForm(instance=entry, access_list=access_list)
        return render(
            request,
            "permissions/acl_entry_form.html",
            {"form": form, "access_list": access_list, "entry": entry, "is_new": False},
        )

    def post(self, request, pk, entry_pk):
        access_list = get_object_or_404(AccessList, pk=pk)
        entry = get_object_or_404(ACLEntry, pk=entry_pk, access_list=access_list)
        form = ACLEntryForm(request.POST, instance=entry, access_list=access_list)
        if form.is_valid():
            with transaction.atomic():
                entry = form.save()
                ActionLog.record(
                    user=request.user,
                    action=ACL_ENTRY_UPDATED,
                    content_object=entry,
                    object_repr=str(entry),
                )
                messages.success(request, "エントリを更新しました。")
                return redirect("permissions:access_list_detail", pk=pk)
        return render(
            request,
            "permissions/acl_entry_form.html",
            {"form": form, "access_list": access_list, "entry": entry, "is_new": False},
        )


class ACLEntryDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ACLEntry削除（仕様書 v1.6 §2.3.1 No.8）。"""

    permission_required = "permissions.change_accesslist"

    def post(self, request, pk, entry_pk):
        access_list = get_object_or_404(AccessList, pk=pk)
        entry = get_object_or_404(ACLEntry, pk=entry_pk, access_list=access_list)
        with transaction.atomic():
            repr_str = str(entry)
            ActionLog.record(
                user=request.user,
                action=ACL_ENTRY_DELETED,
                object_repr=repr_str,
            )
            entry.delete()
            messages.success(request, "エントリを削除しました。")
        return redirect("permissions:access_list_detail", pk=pk)


class ACLEntryReorderView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """ACLEntry並び順変更（仕様書 v1.6 §2.3.1 No.9）。
    第5.1節「先勝ちルール」の要。AJAXおよび通常POST対応。
    """

    permission_required = "permissions.change_accesslist"

    def post(self, request, pk):
        access_list = get_object_or_404(AccessList, pk=pk)

        # 方式 1: 上下移動 (direction='up'/'down', entry_id=...)
        entry_id = request.POST.get("entry_id")
        direction = request.POST.get("direction")

        if entry_id and direction in ["up", "down"]:
            target_entry = get_object_or_404(ACLEntry, pk=entry_id, access_list=access_list)
            entries = list(access_list.entries.all().order_by("order", "created_at", "id"))
            idx = next((i for i, e in enumerate(entries) if e.pk == target_entry.pk), None)

            if idx is not None:
                swap_idx = idx - 1 if direction == "up" else idx + 1
                if 0 <= swap_idx < len(entries):
                    with transaction.atomic():
                        other_entry = entries[swap_idx]
                        target_order, other_order = target_entry.order, other_entry.order
                        if target_order == other_order:
                            # 順序が同値の場合は再ナンバリング
                            for i, e in enumerate(entries):
                                e.order = (i + 1) * 10
                                e.save(update_fields=["order"])
                            target_entry.refresh_from_db()
                            other_entry.refresh_from_db()
                            target_order, other_order = target_entry.order, other_entry.order

                        target_entry.order = other_order
                        other_entry.order = target_order
                        target_entry.save(update_fields=["order"])
                        other_entry.save(update_fields=["order"])

                        ActionLog.record(
                            user=request.user,
                            action=ACL_ENTRY_REORDERED,
                            content_object=access_list,
                            object_repr=str(access_list),
                            data={"target_entry_id": str(target_entry.pk), "direction": direction},
                        )
                        AccessListService.rebuild_for_access_list(access_list)

                    if request.headers.get("x-requested-with") == "XMLHttpRequest":
                        return JsonResponse({"status": "ok"})
                    messages.success(request, "並び順を変更しました。")
                    return redirect("permissions:access_list_detail", pk=pk)

        # 方式 2: 一括順序指定 (JSON or entry_ids リスト)
        entry_ids = []
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body)
                entry_ids = data.get("entry_ids", [])
            except json.JSONDecodeError:
                pass
        else:
            entry_ids = request.POST.getlist("entry_ids")

        if entry_ids:
            with transaction.atomic():
                for new_order, eid in enumerate(entry_ids, start=1):
                    access_list.entries.filter(pk=eid).update(order=new_order)
                ActionLog.record(
                    user=request.user,
                    action=ACL_ENTRY_REORDERED,
                    content_object=access_list,
                    object_repr=str(access_list),
                    data={"entry_ids": [str(e) for e in entry_ids]},
                )
                AccessListService.rebuild_for_access_list(access_list)

            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
                return JsonResponse({"status": "ok"})
            messages.success(request, "並び順を一括保存しました。")

        return redirect("permissions:access_list_detail", pk=pk)
