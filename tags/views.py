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


def _archived_only(request):
    """[性質] 純関数。URL クエリ `?archived_only=1` の真偽を返す。

    一覧画面の「アーカイブ済みのみを表示」トグルから渡される。
    True/false/1/0 等を寛容に受け付ける（フォーム submit と直接 URL 編集の両対応）。
    True なら一覧は archived のみ、False（デフォルト）なら active のみ表示する。
    """
    return request.GET.get("archived_only", "").lower() in ("1", "true", "on", "yes")


# ----------------------------------------------------------------------
# タグ一覧の多段サーバー側ソート（HIG v1.5 第6章。persons 実装を複製）。
#
# 「画面でソートできる列＝許可リストにあるキー」を一致させ、それ以外は無視＝既定の並びに戻す。
# クエリは単一パラメータ sort に優先順つきカンマ区切り、降順は先頭 "-"。
#   例 ?sort=category,-name,updated_at （第1=カテゴリ昇順 / 第2=タグ名降順 / 第3=更新日時昇順）
# person_count は TagListView.get_queryset の annotate 済み集計をそのまま order_by する。
# ----------------------------------------------------------------------

TAG_LIST_SORT_FIELD_MAP = {
    "category": "category__sort_order",   # カテゴリ（運用者が決めた並び順）
    "name": "name",                       # タグ名
    "person_count": "person_count",       # 付与 Person 数（annotate 済み）
    "created_by": "created_by__username",  # 作成者
    "updated_at": "updated_at",            # 更新日時
}
# 多段ソートの最大段数（ソートコントロールの行数と一致）。
TAG_LIST_SORT_MAX_KEYS = 3


def _parse_tag_sort(params):
    """単一 sort パラメータ（例 "category,-name,updated_at"）を [(key, direction), ...] に解決する。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] params: QueryDict 様（request.GET）
    [出力] list[tuple[str, str]]（key は許可リスト内、direction は "asc" / "desc"）
        - 先頭 "-" は降順、無印は昇順。
        - 許可リスト外キー・空トークンは無視（不正値は黙って捨てる＝既定の並びに戻す）。
        - 同一キーの二重指定は先勝ちで除外。最大 TAG_LIST_SORT_MAX_KEYS 段まで。
    """
    raw = params.get("sort") or ""
    tokens = []
    seen = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("-"):
            direction = "desc"
            key = chunk[1:]
        else:
            direction = "asc"
            key = chunk
        if key not in TAG_LIST_SORT_FIELD_MAP or key in seen:
            continue
        seen.add(key)
        tokens.append((key, direction))
        if len(tokens) >= TAG_LIST_SORT_MAX_KEYS:
            break
    return tokens


def _apply_tag_list_sort(qs, params):
    """Tag QuerySet に多段ソート（?sort=key,-key,...）を適用する（純関数）。

    [性質] 純関数（QuerySet を加工して返すのみ・DB 操作なし）
    有効トークンが無ければ qs をそのまま返す（呼び出し側の既定並びを温存）。
    指定有りのときだけ許可リスト経由で order_by を多段で差し替える（末尾に pk で安定化）。
    """
    tokens = _parse_tag_sort(params)
    if not tokens:
        return qs
    order = []
    for key, direction in tokens:
        prefix = "-" if direction == "desc" else ""
        order.append(prefix + TAG_LIST_SORT_FIELD_MAP[key])
    order.append("pk")
    return qs.order_by(*order)


def _tag_sort_context(params):
    """ソート UI（絞り込みフォーム内の折りたたみ）の描画用 context を作る（純関数）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [出力] dict:
        sort_rows: TAG_LIST_SORT_MAX_KEYS 行分の [{"key", "dir"}]
                   （未指定行は key="" / dir="asc"）。各行が列ドロップダウン＋方向トグルに対応。
        sort_is_active: bool（有効トークンが 1 つでもあるか＝折りたたみの開閉初期状態の判定）。
        sort_value: 正規化済み sort 文字列（hidden の初期値・no-JS 再送信用）。
    """
    tokens = _parse_tag_sort(params)
    rows = [{"key": k, "dir": d} for (k, d) in tokens]
    while len(rows) < TAG_LIST_SORT_MAX_KEYS:
        rows.append({"key": "", "dir": "asc"})
    return {
        "sort_rows": rows,
        "sort_is_active": bool(tokens),
        "sort_value": ",".join(
            ("-" + k if d == "desc" else k) for k, d in tokens
        ),
    }


# ======================================================================
# TagCategory CRUD（§6.2.5）
# ======================================================================


class TagCategoryListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    # 認可（Phase 7 ⑤-B-2）：tags.view_tagcategory（全員）。所有者判定なし。
    permission_required = "tags.view_tagcategory"
    model = TagCategory
    template_name = "tags/tag_category_list.html"
    context_object_name = "categories"
    paginate_by = 50

    def get_queryset(self):
        qs = TagCategory.objects.all()
        if _archived_only(self.request):
            qs = qs.filter(is_archived=True)
        else:
            qs = qs.filter(is_archived=False)
        return qs.order_by("sort_order", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current("", ["page", "archived_only"])
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "tags:tag_category_list",
                "archived_only": _archived_only(self.request),
            }
        )
        return context


class TagCategoryCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    # 認可（Phase 7 ⑤-B-2）：tags.add_tagcategory。所有者判定なし。
    permission_required = "tags.add_tagcategory"
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


class TagCategoryUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    # 認可（Phase 7 ⑤-B-2）：tags.change_tagcategory。所有者判定なし。
    permission_required = "tags.change_tagcategory"
    model = TagCategory
    form_class = TagCategoryForm
    template_name = "tags/tag_category_form.html"
    success_url = reverse_lazy("tags:tag_category_list")

    def dispatch(self, request, *args, **kwargs):
        # Phase 1b-ε.1 追加修正：アーカイブ済みは編集禁止（詳細画面に redirect + warning）。
        category = get_object_or_404(TagCategory, pk=kwargs.get("pk"))
        if category.is_archived:
            from django.contrib import messages

            messages.warning(
                request,
                "アーカイブ済みは編集できません。非アーカイブ化してから編集してください。",
            )
            return redirect("tags:tag_category_detail", pk=category.pk)
        return super().dispatch(request, *args, **kwargs)

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


class TagCategoryDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """タグカテゴリ詳細（Phase 1b-ε.1 追加）。

    カテゴリ基本情報 + 配下タグ一覧（ページネーション 20 件/ページ）。
    archived カテゴリも閲覧可能（非アーカイブ化導線のため queryset は全件）。

    配下タグ一覧は include_archived 切替で archived タグ含む/含まないを制御。
    BackNavigator は contacts/cards 慣例どおり詳細画面では push_current を呼ばない。
    """

    # 認可（Phase 7 ⑤-B-2）：tags.view_tagcategory（全員）。所有者判定なし。
    permission_required = "tags.view_tagcategory"
    model = TagCategory
    template_name = "tags/tag_category_detail.html"
    context_object_name = "category"

    def get_queryset(self):
        return TagCategory.objects.all()

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        from django.db.models import Count, Q

        context = super().get_context_data(**kwargs)
        tag_qs = self.object.tags.all()
        if _archived_only(self.request):
            tag_qs = tag_qs.filter(is_archived=True)
        else:
            tag_qs = tag_qs.filter(is_archived=False)
        tag_qs = tag_qs.annotate(
            person_count=Count(
                "assignments",
                filter=Q(assignments__person__status=Person.Status.ACTIVE),
            )
        ).order_by("-updated_at", "-created_at")
        paginator = Paginator(tag_qs, 20)
        page_number = self.request.GET.get("page") or 1
        page_obj = paginator.get_page(page_number)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "tags:tag_category_list",
                "tag_total": tag_qs.count(),
                "page_obj": page_obj,
                "paginator": paginator,
                "is_paginated": page_obj.has_other_pages(),
                "archived_only": _archived_only(self.request),
            }
        )
        return context


class TagCategoryDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """論理アーカイブ化（is_archived=True、§11.1.4）。物理削除はしない。

    Phase 1b-δ で UI ラベルは「削除」→「アーカイブ化」に統一したが、コード内表現
    （URL 名・View 名）は維持する方針（指示書準拠）。

    認可（Phase 7 ⑤-B-2）：tags.delete_tagcategory。所有者判定なし。
    """

    permission_required = "tags.delete_tagcategory"

    def post(self, request, pk):
        category = get_object_or_404(TagCategory, pk=pk)
        category.is_archived = True
        category.save(update_fields=["is_archived", "updated_at"])
        return redirect("tags:tag_category_list")


@method_decorator(require_POST, name="dispatch")
class TagCategoryReorderView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """並び替え AJAX：sort_order を一括更新する（§6.2.5）。

    POST: JSON body { "order": [pk1, pk2, ...] }
    レスポンス: {"ok": true}

    認可（Phase 7 ⑤-B-2）：tags.change_tagcategory（並び替え＝編集）。所有者判定なし。
    """

    permission_required = "tags.change_tagcategory"

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


class TagListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    # 認可（Phase 7 ⑤、rev19 No.63）：tags.view_tag（全員）。所有者判定なし。
    permission_required = "tags.view_tag"
    model = Tag
    template_name = "tags/tag_list.html"
    context_object_name = "tags"
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Count, Q

        qs = Tag.objects.select_related("category", "created_by")
        # 状態（アクティブ／アーカイブ済み）トグルで絞り込み（§3、HIG 4.1）。
        selected_statuses = self._selected_statuses()
        if "active" in selected_statuses and "archived" not in selected_statuses:
            qs = qs.filter(is_archived=False)
        elif "archived" in selected_statuses and "active" not in selected_statuses:
            qs = qs.filter(is_archived=True)
        # 両方選択 or （searched で）全解除のときは is_archived で絞らない（全件）。
        # タグ名の部分一致（§3）。
        name = self.request.GET.get("name", "").strip()
        if name:
            qs = qs.filter(name__icontains=name)
        # タグカテゴリの複数選択トグル（§3。旧 単一 category セレクトを置換）。
        category_ids = [c for c in self.request.GET.getlist("category") if c]
        if category_ids:
            qs = qs.filter(category_id__in=category_ids)
        # 付与 Person 数（active のみ集計、§7.3 手順 3 / §11.7.1 と整合）。
        qs = qs.annotate(
            person_count=Count(
                "assignments",
                filter=Q(assignments__person__status=Person.Status.ACTIVE),
            )
        )
        # 既定並びは category__sort_order, name。?sort= があれば許可リスト経由の多段ソートへ。
        qs = qs.order_by("category__sort_order", "name")
        return _apply_tag_list_sort(qs, self.request.GET)

    def _selected_statuses(self):
        """状態トグルの選択値（active / archived）。初期表示は active のみ（§3）。

        [性質] 準関数寄り（request.GET を読むのみ）。`searched=1` が無い初回は ["active"]、
        有るときは選択値（許可値のみ）。
        """
        valid = {"active", "archived"}
        if self.request.GET.get("searched"):
            return [s for s in self.request.GET.getlist("status") if s in valid]
        return ["active"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        # HIG 6.1：並び替え・ページ・絞り込み状態を戻るで復元するため keys に含める。
        back.push_current(
            "", ["category", "page", "name", "status", "searched", "sort"]
        )
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "tags:tag_list",
                "categories": TagCategory.objects.filter(is_archived=False).order_by(
                    "sort_order", "name"
                ),
                "selected_category_ids": self.request.GET.getlist("category"),
                "search_name": self.request.GET.get("name", ""),
                "selected_statuses": self._selected_statuses(),
            }
        )
        # 絞り込みフォーム内ソートコントロール用（sort_rows / sort_is_active / sort_value）。
        context.update(_tag_sort_context(self.request.GET))
        return context


class TagCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    # 認可（Phase 7 ⑤、rev19 No.64）：tags.add_tag（admin/sales）。所有者判定なし。
    permission_required = "tags.add_tag"
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


class TagUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    # 認可（Phase 7 ⑤、rev19 No.66）：tags.change_tag（admin/sales）。所有者判定なし。
    permission_required = "tags.change_tag"
    model = Tag
    form_class = TagForm
    template_name = "tags/tag_form.html"
    success_url = reverse_lazy("tags:tag_list")

    def dispatch(self, request, *args, **kwargs):
        # Phase 1b-ε.1 追加修正：アーカイブ済みは編集禁止（詳細画面に redirect + warning）。
        tag = get_object_or_404(Tag, pk=kwargs.get("pk"))
        if tag.is_archived:
            from django.contrib import messages

            messages.warning(
                request,
                "アーカイブ済みは編集できません。非アーカイブ化してから編集してください。",
            )
            return redirect("tags:tag_detail", pk=tag.pk)
        return super().dispatch(request, *args, **kwargs)

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


class TagDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """タグ詳細（Phase 1b-δ 追加、§5.1 No.22）。

    タグ基本情報 + 付与 Person 一覧（ページネーション 20 件/ページ）を表示する。
    archived タグも閲覧可能（非アーカイブ化導線のため queryset は全件）。

    BackNavigator：本画面はページネーション（?page=）を持ち、付与 Person 一覧から
    人物詳細へ送り出す起点画面のため push_current を呼ぶ（HIG 3.2・付録 BackNavigator）。
    これにより人物詳細から「戻る」でこのタグ詳細（ページ位置つき）に戻れる。
    ※パラメータを持たない詳細（contacts/cards）は push_current 不要だが、本画面は該当しない。
    """

    model = Tag
    template_name = "tags/tag_detail.html"
    context_object_name = "tag"
    # 認可（Phase 7 ⑤、rev19 No.65）：tags.view_tag（全員）。所有者判定なし。
    permission_required = "tags.view_tag"

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
        # ページネーションを持つ起点画面なので push_current でスタックに積む。
        # ソート・検索条件は無いので page のみ（dir は HIG 廃止、sort も無し）。
        back = BackNavigator(self.request)
        back.push_current("", ["page"])
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "tags:tag_list",
                "person_total": person_qs.count(),
                "page_obj": page_obj,
                "paginator": paginator,
                "is_paginated": page_obj.has_other_pages(),
            }
        )
        return context


@method_decorator(
    permission_required("tags.delete_tag", raise_exception=True), name="dispatch"
)
class TagDeleteView(LoginRequiredMixin, View):
    """論理アーカイブ化（is_archived=True、§11.2.4）。物理削除はしない。

    Phase 1b-δ で UI ラベルを「削除」→「アーカイブ化」に統一したが、コード内表現
    （URL 名・View 名）は維持する方針（指示書準拠）。

    認可（Phase 7 ⑤、rev19 No.67）：tags.delete_tag（admin）。所有者判定なし。
    """

    def post(self, request, pk):
        tag = get_object_or_404(Tag, pk=pk)
        tag.is_archived = True
        tag.save(update_fields=["is_archived", "updated_at"])
        return redirect("tags:tag_list")


class TagUnarchiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """非アーカイブ化（is_archived=False、Phase 1b-δ 追加）。POST 専用。

    archived タグを元に戻す。リダイレクト先は呼び出し元（back スタックがあればそこへ、
    無ければタグ一覧）。

    認可（Phase 7 段2-C）：tags.change_tag（状態変更）。所有者判定なし。
    対になる TagCategoryUnarchiveView（change_tagcategory）と同方式。
    """

    permission_required = "tags.change_tag"

    def post(self, request, pk):
        tag = get_object_or_404(Tag, pk=pk)
        tag.is_archived = False
        tag.save(update_fields=["is_archived", "updated_at"])
        back = BackNavigator(request)
        if back.back_exist:
            return redirect(back.back_url)
        return redirect("tags:tag_detail", pk=tag.pk)


class TagCategoryUnarchiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """タグカテゴリ非アーカイブ化（is_archived=False、Phase 1b-δ 追加）。POST 専用。

    認可（Phase 7 ⑤-B-2）：tags.change_tagcategory（状態変更）。所有者判定なし。
    """

    permission_required = "tags.change_tagcategory"

    def post(self, request, pk):
        category = get_object_or_404(TagCategory, pk=pk)
        category.is_archived = False
        category.save(update_fields=["is_archived", "updated_at"])
        back = BackNavigator(request)
        if back.back_exist:
            return redirect(back.back_url)
        return redirect("tags:tag_category_detail", pk=category.pk)


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

    def _get_fixed_tag(self, **kwargs):
        """[性質] 準関数。固定モード時の対象 Tag を返す（pk なしなら None）。

        URL パスに pk があれば固定モード（タグ詳細「このタグを人に付与」からの遷移）。
        存在しない pk は 404（500 を返さない）。is_archived 問わず取得（表示用）。
        """
        pk = kwargs.get("pk")
        if pk is None:
            return None
        return get_object_or_404(Tag, pk=pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fixed_tag = self._get_fixed_tag(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(
            "",
            list(SEARCH_PARAMS) + ["status", "searched", "page"],
        )
        # 固定モードは検索往復・リセットでもタグを URL パスで維持する。
        if fixed_tag is not None:
            reset_url = reverse_lazy(
                "tags:bulk_tagging_for_tag", kwargs={"pk": fixed_tag.pk}
            )
        else:
            reset_url = reverse_lazy("tags:bulk_tagging")
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "tags:bulk_tagging",
                "tags_by_category": _tags_grouped_by_category(),
                "reset_url": reset_url,
                "submit_label": "検索",
                "bulk_tagging_max_persons": BULK_TAGGING_MAX_PERSONS,
                # Phase 1b-ε.1 追加修正：bulk_tagging のステータスフィルタを廃止し active 固定。
                # 仕様書 §7.3 手順 3 / §11.7.1（マージ後表示は active のみ）と整合。
                # merged / archived の TagAssignment 確認は Django admin（TagAdmin）で行う。
                "selected_statuses": ["active"],
                # 固定モード（タグ詳細からの遷移）。None なら従来モードで挙動不変。
                "fixed_tag": fixed_tag,
                "is_fixed_mode": fixed_tag is not None,
            }
        )
        # _search_form.html partial が参照するコンテキスト
        for key in SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        # 検索済みなら絞り込み済み Person 一覧を出す（status は active 強制）。
        if self.request.GET.get("searched") == "1":
            search_params = self.request.GET.copy()
            search_params.setlist("status", ["active"])
            context["result_persons"] = search_persons(search_params)
            context["has_results"] = True
        else:
            context["result_persons"] = Person.objects.none()
            context["has_results"] = False
        return context

    @method_decorator(login_required)
    @method_decorator(permission_required("tags.assign_tag", raise_exception=True))
    def post(self, request, *args, **kwargs):
        fixed_tag = self._get_fixed_tag(**kwargs)
        person_ids = request.POST.getlist("person_ids")
        # 固定モードは POST の tag_ids を信用せず、URL パスの固定タグ1件に強制する。
        if fixed_tag is not None:
            tag_ids = [str(fixed_tag.pk)]
        else:
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
        # 固定モードは付与後にタグ詳細へ戻す（success メッセージ付き）。
        if fixed_tag is not None:
            from django.contrib import messages

            messages.success(
                request,
                f"「{fixed_tag.name}」を {len(persons)} 人に付与しました"
                f"（新規 {created_count} 件、既存重複はスキップ）。",
            )
            return redirect("tags:tag_detail", pk=fixed_tag.pk)
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
