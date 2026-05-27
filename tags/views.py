"""tags アプリの View 層（仕様書 v1.6 §6.2.5 / §6.2.6 / §5.1 No.20〜26）。

実装範囲（Phase 1b-β）：
  - TagCategory CRUD（一覧・作成・編集・並び替え・論理削除）
  - Tag CRUD（一覧・作成・編集・論理削除）
  - TagAssign / TagUnassign AJAX（Person 詳細画面から呼ぶ、§5.1 No.25・No.26）
  - BulkTagging（検索結果一括タグ付け、§6.2.6）

認可：Phase 1b では LoginRequiredMixin のみ（仕様書 §14 の細粒度 Permission は Phase 7）。
ただし AJAX assign / unassign は perms.tags.assign_tag を要求（Phase 1a で登録済み）。
"""

import json

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from back_navigator.back_navigator import BackNavigator
from config.constants import BULK_TAGGING_MAX_PERSONS
from persons.models import Person
from persons.services.person_search import SEARCH_PARAMS, search_persons

from .forms import TagCategoryForm, TagForm
from .models import Tag, TagAssignment, TagCategory


# ======================================================================
# TagCategory CRUD（§6.2.5）
# ======================================================================


class TagCategoryListView(LoginRequiredMixin, ListView):
    model = TagCategory
    template_name = "tags/tag_category_list.html"
    context_object_name = "categories"
    paginate_by = 50

    def get_queryset(self):
        return TagCategory.objects.filter(is_archived=False).order_by(
            "sort_order", "name"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current("タグカテゴリ管理", ["page"])
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "tags:tag_category_list",
            }
        )
        return context


class TagCategoryCreateView(LoginRequiredMixin, CreateView):
    model = TagCategory
    form_class = TagCategoryForm
    template_name = "tags/tag_category_form.html"
    success_url = reverse_lazy("tags:tag_category_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "tags:tag_category_list",
                "is_create": True,
            }
        )
        return context


class TagCategoryUpdateView(LoginRequiredMixin, UpdateView):
    model = TagCategory
    form_class = TagCategoryForm
    template_name = "tags/tag_category_form.html"
    success_url = reverse_lazy("tags:tag_category_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "tags:tag_category_list",
                "is_create": False,
            }
        )
        return context


class TagCategoryDeleteView(LoginRequiredMixin, View):
    """論理削除（is_archived=True、§11.1.4）。物理削除はしない。"""

    def post(self, request, pk):
        category = get_object_or_404(TagCategory, pk=pk)
        category.is_archived = True
        category.save(update_fields=["is_archived", "updated_at"])
        return redirect("tags:tag_category_list")


@method_decorator(require_POST, name="dispatch")
class TagCategoryReorderView(LoginRequiredMixin, View):
    """並び替え AJAX：sort_order を一括更新する（§6.2.5）。

    POST: JSON body { "order": [pk1, pk2, ...] }
    レスポンス: {"ok": true}
    """

    def post(self, request):
        try:
            payload = json.loads(request.body.decode("utf-8"))
            order = payload.get("order") or []
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
        with transaction.atomic():
            for idx, pk in enumerate(order):
                TagCategory.objects.filter(pk=pk).update(sort_order=idx)
        return JsonResponse({"ok": True})


# ======================================================================
# Tag CRUD（§5.1 No.20〜24）
# ======================================================================


class TagListView(LoginRequiredMixin, ListView):
    model = Tag
    template_name = "tags/tag_list.html"
    context_object_name = "tags"
    paginate_by = 50

    def get_queryset(self):
        qs = Tag.objects.filter(is_archived=False).select_related(
            "category", "created_by"
        )
        category_id = self.request.GET.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)
        return qs.order_by("category__sort_order", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current("タグ管理", ["category", "page"])
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "tags:tag_list",
                "categories": TagCategory.objects.filter(is_archived=False).order_by(
                    "sort_order", "name"
                ),
                "selected_category": self.request.GET.get("category", ""),
            }
        )
        return context


class TagCreateView(LoginRequiredMixin, CreateView):
    model = Tag
    form_class = TagForm
    template_name = "tags/tag_form.html"
    success_url = reverse_lazy("tags:tag_list")

    def dispatch(self, request, *args, **kwargs):
        # カテゴリが 0 件なら作成できない（§4.9A 末尾、TagCategory 必須 FK）。
        # 0 件時はカテゴリ管理画面へ誘導する（発注書 §TagCategory 0 件の初期 UX）。
        if not TagCategory.objects.filter(is_archived=False).exists():
            return redirect("tags:tag_category_list")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "tags:tag_list",
                "is_create": True,
            }
        )
        return context


class TagUpdateView(LoginRequiredMixin, UpdateView):
    model = Tag
    form_class = TagForm
    template_name = "tags/tag_form.html"
    success_url = reverse_lazy("tags:tag_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "tags:tag_list",
                "is_create": False,
            }
        )
        return context


class TagDetailView(LoginRequiredMixin, DetailView):
    """タグ詳細（Phase 1b-δ 追加、§5.1 No.22）。

    タグ基本情報 + 付与 Person 一覧（ページネーション 20 件/ページ）を表示する。
    archived タグも閲覧可能（非アーカイブ化導線のため queryset は全件）。

    BackNavigator は contacts/cards 慣例どおり詳細画面では push_current を呼ばず、
    一覧画面で積まれたスタックをそのまま参照する。
    """

    model = Tag
    template_name = "tags/tag_detail.html"
    context_object_name = "tag"

    def get_queryset(self):
        return Tag.objects.select_related("category", "created_by")

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        person_qs = (
            Person.objects.filter(
                status=Person.Status.ACTIVE,
                tag_assignments__tag=self.object,
            )
            .select_related("primary_contact")
            .order_by("-updated_at", "-created_at")
            .distinct()
        )
        paginator = Paginator(person_qs, 20)
        page_number = self.request.GET.get("page") or 1
        page_obj = paginator.get_page(page_number)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "tags:tag_list",
                "person_total": person_qs.count(),
                "page_obj": page_obj,
                "paginator": paginator,
                "is_paginated": page_obj.has_other_pages(),
            }
        )
        return context


class TagDeleteView(LoginRequiredMixin, View):
    """論理アーカイブ化（is_archived=True、§11.2.4）。物理削除はしない。

    Phase 1b-δ で UI ラベルを「削除」→「アーカイブ化」に統一したが、コード内表現
    （URL 名・View 名）は維持する方針（指示書準拠）。
    """

    def post(self, request, pk):
        tag = get_object_or_404(Tag, pk=pk)
        tag.is_archived = True
        tag.save(update_fields=["is_archived", "updated_at"])
        return redirect("tags:tag_list")


class TagUnarchiveView(LoginRequiredMixin, View):
    """非アーカイブ化（is_archived=False、Phase 1b-δ 追加）。POST 専用。

    archived タグを元に戻す。リダイレクト先は呼び出し元（back スタックがあればそこへ、
    無ければタグ一覧）。
    """

    def post(self, request, pk):
        tag = get_object_or_404(Tag, pk=pk)
        tag.is_archived = False
        tag.save(update_fields=["is_archived", "updated_at"])
        back = BackNavigator(request)
        if back.back_exist:
            return redirect(back.back_url)
        return redirect("tags:tag_detail", pk=tag.pk)


class TagCategoryUnarchiveView(LoginRequiredMixin, View):
    """タグカテゴリ非アーカイブ化（is_archived=False、Phase 1b-δ 追加）。POST 専用。"""

    def post(self, request, pk):
        category = get_object_or_404(TagCategory, pk=pk)
        category.is_archived = False
        category.save(update_fields=["is_archived", "updated_at"])
        back = BackNavigator(request)
        if back.back_exist:
            return redirect(back.back_url)
        return redirect("tags:tag_category_update", pk=category.pk)


# ======================================================================
# AJAX：タグ付与・解除（§5.1 No.25・No.26）
# ======================================================================


@method_decorator(require_POST, name="dispatch")
class TagAssignView(PermissionRequiredMixin, View):
    """タグ付与 AJAX（§5.1 No.25）。

    POST: form-encoded or JSON {"person_id": uuid, "tag_id": uuid}
    レスポンス: {"ok": true, "assignment_id": uuid, "tag_name": str, "category_name": str}
    重複付与は UniqueConstraint で防がれるため、get_or_create で既存 assignment を返す。
    """

    permission_required = "tags.assign_tag"
    raise_exception = True

    def post(self, request):
        person_id, tag_id = _parse_assign_payload(request)
        if not person_id or not tag_id:
            return JsonResponse({"ok": False, "error": "missing_params"}, status=400)
        person = get_object_or_404(Person, pk=person_id)
        tag = get_object_or_404(Tag, pk=tag_id, is_archived=False)
        assignment, _created = TagAssignment.objects.get_or_create(
            tag=tag,
            person=person,
            defaults={"assigned_by": request.user},
        )
        return JsonResponse(
            {
                "ok": True,
                "assignment_id": str(assignment.id),
                "tag_id": str(tag.id),
                "tag_name": tag.name,
                "category_id": str(tag.category_id),
                "category_name": tag.category.name,
            }
        )


@method_decorator(require_POST, name="dispatch")
class TagUnassignView(PermissionRequiredMixin, View):
    """タグ解除 AJAX（§5.1 No.26）。

    POST: form-encoded or JSON {"person_id": uuid, "tag_id": uuid}
    レスポンス: {"ok": true, "tag_id": uuid}
    """

    permission_required = "tags.assign_tag"
    raise_exception = True

    def post(self, request):
        person_id, tag_id = _parse_assign_payload(request)
        if not person_id or not tag_id:
            return JsonResponse({"ok": False, "error": "missing_params"}, status=400)
        TagAssignment.objects.filter(person_id=person_id, tag_id=tag_id).delete()
        return JsonResponse({"ok": True, "tag_id": tag_id})


def _parse_assign_payload(request):
    """[性質] 純関数。POST request から person_id と tag_id を取り出す。

    form-encoded / JSON のどちらでも対応。両方無ければ (None, None) を返す。
    """
    if request.content_type and request.content_type.startswith("application/json"):
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, None
        return payload.get("person_id"), payload.get("tag_id")
    return request.POST.get("person_id"), request.POST.get("tag_id")


# ======================================================================
# 検索結果一括タグ付け（§6.2.6）
# ======================================================================


class BulkTaggingView(LoginRequiredMixin, TemplateView):
    """検索結果一括タグ付け画面（§6.2.6）。

    GET: 検索フォーム + 結果一覧（_search_form.html partial を include、A-1 / B-1 再利用）
    POST: 選択された Person 群に対し、選択タグを bulk_create で一括付与

    既存 PersonListView の UI/View には触れない（発注書 §4-4 通り、別ページ新設）。
    検索ロジックは persons.services.person_search.search_persons() を再利用。
    """

    template_name = "tags/bulk_tagging.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(
            "検索結果一括タグ付け",
            list(SEARCH_PARAMS) + ["status", "searched", "page"],
        )
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "tags:bulk_tagging",
                "tags_by_category": _tags_grouped_by_category(),
                "reset_url": reverse_lazy("tags:bulk_tagging"),
                "submit_label": "検索",
                "bulk_tagging_max_persons": BULK_TAGGING_MAX_PERSONS,
            }
        )
        # _search_form.html partial が参照するコンテキスト
        for key in SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        if self.request.GET.get("searched") != "1":
            context["selected_statuses"] = ["active"]
        else:
            context["selected_statuses"] = [
                s
                for s in self.request.GET.getlist("status")
                if s in ("active", "merged", "archived")
            ]
        # 検索済みなら絞り込み済み Person 一覧を出す
        if self.request.GET.get("searched") == "1":
            context["result_persons"] = search_persons(self.request.GET)
            context["has_results"] = True
        else:
            context["result_persons"] = Person.objects.none()
            context["has_results"] = False
        return context

    @method_decorator(login_required)
    @method_decorator(permission_required("tags.assign_tag", raise_exception=True))
    def post(self, request, *args, **kwargs):
        person_ids = request.POST.getlist("person_ids")
        tag_ids = request.POST.getlist("tag_ids")
        if not person_ids or not tag_ids:
            return self.get(request, *args, **kwargs)
        # 上限チェック（config.constants.BULK_TAGGING_MAX_PERSONS = 500、誤操作で全 Person ×
        # 全タグの TagAssignment 爆発を防ぐ防御線）。
        if len(person_ids) > BULK_TAGGING_MAX_PERSONS:
            context = self.get_context_data(**kwargs)
            context["bulk_error"] = (
                f"一度に処理できる Person は最大 {BULK_TAGGING_MAX_PERSONS} 件です"
                f"（選択された件数: {len(person_ids)}）。"
                f"検索条件で絞り込んでから操作してください。"
            )
            return self.render_to_response(context)
        persons = list(Person.objects.filter(pk__in=person_ids))
        tags = list(Tag.objects.filter(pk__in=tag_ids, is_archived=False))
        created_count = _bulk_assign_tags(persons, tags, request.user)
        context = self.get_context_data(**kwargs)
        context["bulk_result"] = {
            "person_count": len(persons),
            "tag_count": len(tags),
            "created_count": created_count,
        }
        return self.render_to_response(context)


def _tags_grouped_by_category():
    """[性質] 準関数。カテゴリ別タグ list を返す（テンプレ表示用）。

    戻り値：list[(TagCategory, list[Tag])]、sort_order / name 順。
    """
    categories = (
        TagCategory.objects.filter(is_archived=False)
        .prefetch_related("tags")
        .order_by("sort_order", "name")
    )
    result = []
    for cat in categories:
        tags = list(cat.tags.filter(is_archived=False).order_by("name"))
        result.append((cat, tags))
    return result


def _bulk_assign_tags(persons, tags, user):
    """[性質] 副作用あり（DB 書込：bulk_create）。重複は ignore_conflicts で回避。"""
    to_create = []
    for person in persons:
        for tag in tags:
            to_create.append(
                TagAssignment(tag=tag, person=person, assigned_by=user)
            )
    if not to_create:
        return 0
    created = TagAssignment.objects.bulk_create(to_create, ignore_conflicts=True)
    return len(created)
