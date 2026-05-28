"""mailings アプリの View 層（仕様書 v1.6 §6.2.4 / §11.3 / §11.4 / §4.13）。

実装範囲（Phase 1b-γ）：
  - MailingList CRUD（一覧・作成・詳細・編集・論理削除）
  - 凍結 AJAX（freeze_members 呼び出し）
  - プレビュー AJAX（extract_persons_by_tags / count_persons_by_tags 呼び出し）
  - 対象外 AJAX（凍結後のメンバー個別物理削除、§11.7.2.1 増やす方向は実装しない）
  - MailingConfig 編集（シングルトン、get_or_create で初回自動作成）
"""

import json
import uuid

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from back_navigator.back_navigator import BackNavigator
from persons.models import Person
from tags.models import Tag, TagCategory

from persons.services.person_search import SEARCH_PARAMS, search_persons

from .forms import MailingConfigForm, MailingListForm
from .models import MailingList, MailingListMember
from .services.list_freeze import (
    freeze_members,
    get_or_create_singleton_mailing_config,
)
from .services.tag_extraction import (
    count_persons_by_tags,
    extract_persons_by_tag_conditions,
    extract_persons_by_tags,
)


def _archived_only(request):
    """[性質] 純関数。URL クエリ `?archived_only=1` の真偽を返す（Phase 1b-ε.5）。

    一覧画面の「アーカイブ済みのみを表示」トグルから渡される。tags 側の
    `tags.views._archived_only` と同じ流儀（apps 間 import 回避のため独立定義）。
    """
    return request.GET.get("archived_only", "").lower() in ("1", "true", "on", "yes")


# ======================================================================
# MailingList CRUD（§4.11 / §11.3）
# ======================================================================


class MailingListListView(LoginRequiredMixin, ListView):
    model = MailingList
    template_name = "mailings/mailing_list_list.html"
    context_object_name = "mailing_lists"
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Count

        qs = MailingList.objects.select_related("created_by").annotate(
            member_count=Count("members")
        )
        if _archived_only(self.request):
            qs = qs.filter(is_archived=True)
        else:
            qs = qs.filter(is_archived=False)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current("配信リスト一覧", ["page", "archived_only"])
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "archived_only": _archived_only(self.request),
            }
        )
        return context


class MailingListCreateView(LoginRequiredMixin, CreateView):
    """配信リスト新規作成（name / description のみ）。

    作成後はリスト編集画面（mailing_list_update）にリダイレクトし、そこでタグ選択・
    プレビュー・凍結を行う。Phase 1 のフロー（§11.4.1）の [1]〜[4] を 2 画面で実装。
    """

    model = MailingList
    form_class = MailingListForm
    template_name = "mailings/mailing_list_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("mailings:mailing_list_update", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "is_create": True,
            }
        )
        return context


class MailingListDetailView(LoginRequiredMixin, DetailView):
    model = MailingList
    template_name = "mailings/mailing_list_detail.html"
    context_object_name = "mailing_list"

    def get_queryset(self):
        # Phase 1b-δ：archived な MailingList も詳細表示できるよう is_archived フィルタを外す
        # （非アーカイブ化ボタンへの導線。一覧画面側は filter(is_archived=False) のまま）。
        return MailingList.objects.select_related("created_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # BackNavigator（詳細画面なので push_current は呼ばない、既存 contacts / cards / duplicates
        # 慣例に揃える。BackNavigator.push_current は keys=[] を DEBUG 時に ValueError として弾く
        # ため、詳細画面ではスタック生成だけ行い、一覧画面側で push_current 済みのスタックを参照する。
        # 詳細→削除→キャンセルで詳細に戻る挙動はテンプレ側で {% append_back_url %} を使って
        # back_stack に詳細 URL を追加することで実現する）。
        members_qs = (
            MailingListMember.objects.filter(mailing_list=self.object)
            .select_related("person", "person__primary_contact", "added_by")
        )
        # ?sort=...&dir=... があれば適用、無ければ name asc がデフォルト（UI 改善 要望1）
        members_qs = _apply_sort_to_members(members_qs, self.request.GET)
        sort_key, sort_dir = _resolve_sort(self.request.GET)
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "members": members_qs,
                "is_frozen": self.object.members_frozen_at is not None,
                "current_sort": sort_key,
                "current_dir": sort_dir,
            }
        )
        return context


class MailingListUpdateView(LoginRequiredMixin, UpdateView):
    """リスト編集画面（name/description）+ メンバー組成 UI（タグ選択・凍結 or 対象外）。

    凍結前（members_frozen_at が NULL）：タグ選択 + プレビュー + 凍結ボタン
    凍結後：メンバー一覧 + 対象外 AJAX（個別物理削除、§11.7.2.1 増やす方向は実装しない）
    """

    model = MailingList
    form_class = MailingListForm
    template_name = "mailings/mailing_list_form.html"

    def dispatch(self, request, *args, **kwargs):
        # Phase 1b-ε.5：アーカイブ済みは編集禁止（詳細画面に redirect + warning、tag 側流儀踏襲）。
        mailing_list = get_object_or_404(MailingList, pk=kwargs.get("pk"))
        if mailing_list.is_archived:
            from django.contrib import messages

            messages.warning(
                request,
                "アーカイブ済みは編集できません。非アーカイブ化してから編集してください。",
            )
            return redirect("mailings:mailing_list_detail", pk=mailing_list.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return MailingList.objects.filter(is_archived=False)

    def get_success_url(self):
        return reverse_lazy("mailings:mailing_list_update", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        is_frozen = self.object.members_frozen_at is not None
        # Phase 1b-ε.6 rev14.1：凍結後は閲覧のみ、未凍結時はメンバー追加・削除可能。
        # 既存メンバー一覧は凍結状態に関わらず常に渡す（未凍結時も削除 UI のため必要）。
        members = (
            MailingListMember.objects.filter(mailing_list=self.object)
            .select_related("person", "person__primary_contact", "added_by")
            .order_by("created_at")
        )
        existing_person_ids = list(members.values_list("person_id", flat=True))
        # 未凍結時のみ Person 検索結果を渡す（_search_form.html partial で searched=1 のとき）。
        search_results = None
        if not is_frozen and self.request.GET.get("searched") == "1":
            qs = search_persons(self.request.GET, default_statuses=("active",))
            # 既にメンバーに入っている Person は除外（UniqueConstraint 重複防止 + UX 簡素化）。
            search_results = qs.exclude(pk__in=existing_person_ids)[:100]
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "is_create": False,
                "is_frozen": is_frozen,
                "tags_by_category": _tags_grouped_by_category_for_picker(),
                "members": members,
                "search_results": search_results,
                # _search_form.html partial が参照するコンテキスト
                "selected_statuses": ["active"],
                "reset_url": reverse_lazy(
                    "mailings:mailing_list_update", args=[self.object.pk]
                ),
                "submit_label": "Person 検索",
            }
        )
        for key in SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        return context


class MailingListDeleteView(LoginRequiredMixin, View):
    """論理アーカイブ化（is_archived=True、§11.3.4）。GET で確認画面、POST で実行。

    物理削除しない理由：Campaign の mailing_list FK が PROTECT（§4.2）のため
    （Phase 2 以降で Campaign が存在し始めると物理削除すると履歴が壊れる）。

    Phase 1b-δ で UI ラベルは「削除」→「アーカイブ化」に統一したが、コード内表現
    （URL 名・View 名）は維持する方針（指示書準拠）。
    """

    def get(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk, is_archived=False)
        return _render_delete_confirm(request, mailing_list)

    def post(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk, is_archived=False)
        mailing_list.is_archived = True
        mailing_list.save(update_fields=["is_archived", "updated_at"])
        return redirect("mailings:mailing_list_list")


class MailingListUnarchiveView(LoginRequiredMixin, View):
    """配信リスト非アーカイブ化（is_archived=False、Phase 1b-δ 追加）。POST 専用。

    archived な MailingList を元に戻す。リダイレクト先は呼び出し元
    （back スタックがあればそこへ、無ければ詳細画面）。
    """

    def post(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk)
        mailing_list.is_archived = False
        mailing_list.save(update_fields=["is_archived", "updated_at"])
        back = BackNavigator(request)
        if back.back_exist:
            return redirect(back.back_url)
        return redirect("mailings:mailing_list_detail", pk=mailing_list.pk)


def _render_delete_confirm(request, mailing_list):
    """[性質] 副作用あり（HttpResponse 返却）。"""
    from django.shortcuts import render

    return render(
        request,
        "mailings/mailing_list_confirm_delete.html",
        {
            "mailing_list": mailing_list,
            "back": BackNavigator(request),
            "active_app": "mailings",
            "active_menu": "mailings:mailing_list_list",
        },
    )


# ======================================================================
# AJAX：凍結・プレビュー・対象外（§11.4 / §11.7.2.1）
# ======================================================================


@method_decorator(require_POST, name="dispatch")
class MailingListFreezeView(LoginRequiredMixin, View):
    """リスト保存 AJAX（rev14.1 §11.4.3、Phase 1b-ε.6 追補で「凍結保存」→「保存」）。

    タグ ID リストを受け取り extract_persons_by_tags → freeze_members で
    MailingListMember を置き換え保存する。`members_frozen_at` は触らない（rev14.1）。

    凍結済み（members_frozen_at IS NOT NULL）リストは編集禁止（§11.3.6 dispatch ガード）。

    POST: form-encoded or JSON {"mailing_list_id": uuid, "tag_ids": [uuid, ...]}
    レスポンス成功: {"ok": true, "member_count": int}
    レスポンス凍結: HTTP 409 {"ok": false, "error": "frozen", "message": str}
    """

    def post(self, request):
        mailing_list_id, tag_ids = _parse_freeze_payload(request)
        if not mailing_list_id:
            return JsonResponse(
                {"ok": False, "error": "missing_mailing_list_id"}, status=400
            )
        mailing_list = get_object_or_404(
            MailingList, pk=mailing_list_id, is_archived=False
        )
        if mailing_list.members_frozen_at is not None:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "frozen",
                    "message": "このリストは凍結済みのため、保存できません。",
                },
                status=409,
            )
        persons = extract_persons_by_tags(tag_ids or [])
        count = freeze_members(mailing_list, persons, request.user)
        return JsonResponse({"ok": True, "member_count": count})


@method_decorator(require_POST, name="dispatch")
class MailingListUpdateMetaView(LoginRequiredMixin, View):
    """リスト本体（name / description）の AJAX 自動保存（Phase 1b-ε.6 追補、修正 4）。

    rev14.1 §11.3.6：凍結後もリスト本体の編集は可能。本 View は archived のみ拒否し、
    frozen 時も保存を許可する。

    POST: form-encoded or JSON {"name": str, "description": str}
    レスポンス成功: {"ok": true, "name": str, "description": str, "updated_at": iso8601}
    レスポンス失敗: HTTP 400 {"ok": false, "errors": {field: [msg, ...]}}
    レスポンス archived: HTTP 404 {"ok": false, "error": "archived"}
    """

    def post(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk)
        if mailing_list.is_archived:
            return JsonResponse(
                {"ok": False, "error": "archived", "message": "アーカイブ済みリストは編集できません。"},
                status=404,
            )
        if request.content_type and request.content_type.startswith("application/json"):
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
        else:
            payload = {
                "name": request.POST.get("name", ""),
                "description": request.POST.get("description", ""),
            }
        form = MailingListForm(payload, instance=mailing_list)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors}, status=400)
        obj = form.save()
        return JsonResponse(
            {
                "ok": True,
                "name": obj.name,
                "description": obj.description,
                "updated_at": obj.updated_at.isoformat(),
            }
        )


@method_decorator(require_POST, name="dispatch")
class MailingListPreviewView(LoginRequiredMixin, View):
    """プレビュー AJAX。タグ ID リストの抽出結果件数 + 先頭サンプル（10 件）を返す。

    POST: form-encoded or JSON {"tag_ids": [uuid, ...]}
    レスポンス: {"ok": true, "count": int, "samples": [{"id": uuid, "name": str, "org": str}, ...]}
    """

    def post(self, request):
        _, tag_ids = _parse_freeze_payload(request)
        qs = extract_persons_by_tags(tag_ids or [])
        count = qs.count()
        samples = []
        for person in qs[:10]:
            primary = person.primary_contact
            samples.append(
                {
                    "id": str(person.id),
                    "name": (primary.full_name if primary else "") or "(氏名なし)",
                    "org": (primary.organization if primary else "") or "",
                    "title": (primary.title if primary else "") or "",
                    "department": (primary.department if primary else "") or "",
                    "address": (primary.address if primary else "") or "",
                    "email": (primary.email if primary else "") or "",
                }
            )
        return JsonResponse({"ok": True, "count": count, "samples": samples})


@method_decorator(require_POST, name="dispatch")
class MailingListAddMemberView(LoginRequiredMixin, View):
    """メンバー追加 AJAX（rev14.1 §5.1 No.32、Phase 1b-ε.6 追加）。

    リスト編集中（未凍結）の手動メンバー追加。凍結済み（members_frozen_at IS NOT NULL）
    のときは HTTP 409 Conflict で弾く（§11.3.6 dispatch ガード）。

    POST: form-encoded or JSON {"person_ids": [uuid, ...]}
    レスポンス成功: {"ok": true, "created_count": int, "members": [{member_id, person_id, name, org, title, email}]}
    レスポンス凍結: HTTP 409 {"ok": false, "error": "frozen", "message": str}
    レスポンス archived: HTTP 404 {"ok": false, "error": "archived"}
    """

    def post(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk)
        if mailing_list.is_archived:
            return JsonResponse(
                {"ok": False, "error": "archived", "message": "アーカイブ済みリストにメンバーは追加できません。"},
                status=404,
            )
        if mailing_list.members_frozen_at is not None:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "frozen",
                    "message": "このリストは凍結済みのため、メンバーの追加はできません。",
                },
                status=409,
            )
        person_ids = _parse_person_ids(request)
        if not person_ids:
            return JsonResponse({"ok": False, "error": "missing_person_ids"}, status=400)
        persons = list(Person.objects.filter(pk__in=person_ids).select_related("primary_contact"))
        to_create = [
            MailingListMember(mailing_list=mailing_list, person=p, added_by=request.user)
            for p in persons
        ]
        created = MailingListMember.objects.bulk_create(to_create, ignore_conflicts=True)
        # bulk_create + ignore_conflicts では created に DB の pk が入らない場合があるため
        # 改めて取得してレスポンス用に整形（重複は元から弾かれているので created_count は実数）。
        new_members = list(
            MailingListMember.objects.filter(
                mailing_list=mailing_list, person_id__in=person_ids
            ).select_related("person", "person__primary_contact")
        )
        return JsonResponse(
            {
                "ok": True,
                "created_count": len(created),
                "members": [
                    {
                        "member_id": str(m.id),
                        "person_id": str(m.person_id),
                        "name": (m.person.primary_contact.full_name if m.person.primary_contact else "") or "(氏名なし)",
                        "org": (m.person.primary_contact.organization if m.person.primary_contact else "") or "",
                        "title": (m.person.primary_contact.title if m.person.primary_contact else "") or "",
                        "department": (m.person.primary_contact.department if m.person.primary_contact else "") or "",
                        "address": (m.person.primary_contact.address if m.person.primary_contact else "") or "",
                        "email": (m.person.primary_contact.email if m.person.primary_contact else "") or "",
                    }
                    for m in new_members
                ],
            }
        )


@method_decorator(require_POST, name="dispatch")
class MailingListRemoveMemberView(LoginRequiredMixin, View):
    """メンバー削除 AJAX（rev14.1 §5.1 No.33、Phase 1b-ε.6 追加）。

    リスト編集中（未凍結）の手動メンバー削除。凍結済みは HTTP 409 で弾く（§11.3.6）。

    POST: form-encoded or JSON {"member_id": uuid}
    レスポンス成功: {"ok": true, "removed_member_id": uuid}
    レスポンス凍結: HTTP 409 {"ok": false, "error": "frozen", "message": str}
    レスポンス archived: HTTP 404 {"ok": false, "error": "archived"}
    """

    def post(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk)
        if mailing_list.is_archived:
            return JsonResponse(
                {"ok": False, "error": "archived", "message": "アーカイブ済みリストのメンバーは削除できません。"},
                status=404,
            )
        if mailing_list.members_frozen_at is not None:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "frozen",
                    "message": "このリストは凍結済みのため、メンバーの削除はできません。",
                },
                status=409,
            )
        member_id = _parse_member_id(request)
        if not member_id:
            return JsonResponse({"ok": False, "error": "missing_member_id"}, status=400)
        deleted, _ = MailingListMember.objects.filter(
            pk=member_id, mailing_list=mailing_list
        ).delete()
        if deleted == 0:
            return JsonResponse({"ok": False, "error": "member_not_found"}, status=404)
        return JsonResponse({"ok": True, "removed_member_id": member_id})


@method_decorator(require_POST, name="dispatch")
class MailingListMemberRemoveView(LoginRequiredMixin, View):
    """対象外 AJAX。凍結後の MailingListMember を物理削除する（§11.7.2.1 / 過去発注書）。

    増やす方向は実装しない（凍結思想に反する）。members_frozen_at は更新しない。

    POST: form-encoded or JSON {"member_id": uuid}
    レスポンス: {"ok": true, "member_id": uuid}
    """

    def post(self, request):
        member_id = _parse_member_id(request)
        if not member_id:
            return JsonResponse({"ok": False, "error": "missing_member_id"}, status=400)
        MailingListMember.objects.filter(pk=member_id).delete()
        return JsonResponse({"ok": True, "member_id": member_id})


def _parse_freeze_payload(request):
    """[性質] 純関数。POST request から mailing_list_id と tag_ids を取り出す。

    JSON / form-encoded 両対応。両方無ければ (None, []) を返す。
    """
    if request.content_type and request.content_type.startswith("application/json"):
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, []
        return payload.get("mailing_list_id"), payload.get("tag_ids") or []
    return request.POST.get("mailing_list_id"), request.POST.getlist("tag_ids")


def _parse_member_id(request):
    """[性質] 純関数。POST request から member_id を取り出す（form / JSON 両対応）。"""
    if request.content_type and request.content_type.startswith("application/json"):
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload.get("member_id")
    return request.POST.get("member_id")


def _parse_person_ids(request):
    """[性質] 純関数。POST request から person_ids（複数）を取り出す（form / JSON 両対応）。"""
    if request.content_type and request.content_type.startswith("application/json"):
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []
        ids = payload.get("person_ids") or []
        return list(ids) if isinstance(ids, list) else []
    return request.POST.getlist("person_ids")


def _tags_grouped_by_category_for_picker():
    """[性質] 準関数。リスト編集画面のタグ選択 UI 用、カテゴリ別タグ dict を返す。"""
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


# ======================================================================
# 個別追加・個別削除（Phase 1c-α、仕様書 §3.1〜§3.9）
# ======================================================================
#
# snapshot 方式（§6.3）：選択画面 POST で確定された Person ID 集合を session に保存し、
# 確認画面では再抽出せず session の中身をそのまま表示・確定する。タグや status の
# 変化があっても確認画面で見えた顔ぶれが確定処理に渡る。
#
# PRG パターン：選択画面 POST → session 保存 → 302 確認画面 GET、
#               確定処理 POST → DB 反映 → session クリア → 302 詳細画面 GET。
# 確認画面は GET 専用（リロードで再送信警告を出さない）。
#
# session キー：mailing_list_<pk>_add_selection / mailing_list_<pk>_remove_selection。
# ?restore=1 ルール（§6.4）：選択画面 GET が restore=1 付きなら session から復元、
# 付いていなければ session を破棄して新規開始。
#
# 確認画面 session 空フォールバック（§6.5）：snapshot が無い状態で確認画面に来たら
# 選択画面へ 302 で差し戻す（直叩き / 期限切れ対策）。


def _is_session_truthy(seq):
    """[性質] 純関数。session の person_ids が実体として持っているかを判定。"""
    return bool(seq)


# ----------------------------------------------------------------------
# 列ソート（Phase 1c-α UI 改善 要望1、サーバサイド order_by）
# ----------------------------------------------------------------------
# URL クエリ ?sort=<key>&dir=<asc|desc>。不正値はデフォルト（name asc）に戻す。
# 詳細・選択・確認の 5 画面で共通の鍵を使う。Person QuerySet と
# MailingListMember QuerySet で関連パスが異なるため適用ヘルパーを 2 種用意。

SORT_FIELD_MAP = {
    "name": "primary_contact__full_name",
    "company": "primary_contact__organization",
    "department": "primary_contact__department",
    "title": "primary_contact__title",
    "address": "primary_contact__address",
    "email": "primary_contact__email",
}
SORT_DEFAULT_KEY = "name"
SORT_DEFAULT_DIR = "asc"
SORT_ALLOWED_DIRS = ("asc", "desc")


def _resolve_sort(params):
    """[性質] 純関数。GET から (sort_key, direction) を取り出し、不正値はデフォルトに戻す。"""
    sort_key = params.get("sort") or SORT_DEFAULT_KEY
    direction = params.get("dir") or SORT_DEFAULT_DIR
    if sort_key not in SORT_FIELD_MAP:
        sort_key = SORT_DEFAULT_KEY
    if direction not in SORT_ALLOWED_DIRS:
        direction = SORT_DEFAULT_DIR
    return sort_key, direction


def _apply_sort_to_persons(qs, params):
    """[性質] 純関数。Person QuerySet に ?sort=...&dir=... を適用。"""
    sort_key, direction = _resolve_sort(params)
    field = SORT_FIELD_MAP[sort_key]
    prefix = "" if direction == "asc" else "-"
    return qs.order_by(prefix + field, "pk")


def _apply_sort_to_members(qs, params):
    """[性質] 純関数。MailingListMember QuerySet に sort を適用（person__ プレフィクス経由）。"""
    sort_key, direction = _resolve_sort(params)
    field = "person__" + SORT_FIELD_MAP[sort_key]
    prefix = "" if direction == "asc" else "-"
    return qs.order_by(prefix + field, "pk")


def _apply_person_text_filters(qs, params):
    """SEARCH_PARAMS の 7 項目を Person QuerySet に icontains で適用する（status は触らない）。

    [性質] 純関数（QuerySet を加工して返すのみ、DB 操作なし）
    [入力] qs: QuerySet[Person]、params: QueryDict 様
    [出力] QuerySet[Person]

    個別削除の母集合は status / is_unsubscribed を問わないため search_persons は
    使わずに本ヘルパーで text 7 項目のみ絞り込む。search_persons は status を
    一緒に扱うため remove のセマンティクスと合わない。
    """
    name = (params.get("name") or "").strip()
    if name:
        qs = qs.filter(primary_contact__full_name__icontains=name)
    organization = (params.get("organization") or "").strip()
    if organization:
        qs = qs.filter(primary_contact__organization__icontains=organization)
    department = (params.get("department") or "").strip()
    if department:
        qs = qs.filter(primary_contact__department__icontains=department)
    title = (params.get("title") or "").strip()
    if title:
        qs = qs.filter(primary_contact__title__icontains=title)
    email = (params.get("email") or "").strip()
    if email:
        qs = qs.filter(primary_contact__email__icontains=email)
    tel = (params.get("tel") or "").strip()
    if tel:
        qs = qs.filter(
            Q(primary_contact__personal_phone__icontains=tel)
            | Q(primary_contact__mobile_phone__icontains=tel)
            | Q(primary_contact__personal_fax__icontains=tel)
        )
    address = (params.get("address") or "").strip()
    if address:
        qs = qs.filter(primary_contact__address__icontains=address)
    return qs


def _sanitize_uuid_list(values):
    """[性質] 純関数。文字列リストから UUID 形式のものだけ取り出す（重複は除去）。"""
    clean = []
    seen = set()
    for v in values or []:
        try:
            u = str(uuid.UUID(str(v)))
        except (ValueError, TypeError, AttributeError):
            continue
        if u in seen:
            continue
        seen.add(u)
        clean.append(u)
    return clean


def _render_member_edit_error(request, mailing_list, message, *, status):
    """[性質] 副作用あり（HttpResponse 返却）。凍結 / archived 時のエラーページ。"""
    return render(
        request,
        "mailings/_member_edit_error.html",
        {
            "mailing_list": mailing_list,
            "error_message": message,
            "status_code": status,
            "back": BackNavigator(request),
            "active_app": "mailings",
            "active_menu": "mailings:mailing_list_list",
        },
        status=status,
    )


def _guard_member_edit(request, mailing_list):
    """[性質] 副作用あり（HttpResponse 返却 or None）。archived 404 / frozen 409 ガード。"""
    if mailing_list.is_archived:
        return _render_member_edit_error(
            request,
            mailing_list,
            "アーカイブ済みリストはメンバーを編集できません。",
            status=404,
        )
    if mailing_list.members_frozen_at is not None:
        return _render_member_edit_error(
            request,
            mailing_list,
            "凍結中のため編集できません。",
            status=409,
        )
    return None


def _selection_session_key(mailing_list_pk, mode):
    """[性質] 純関数。session キー名を返す。"""
    return f"mailing_list_{mailing_list_pk}_{mode}_selection"


def _selection_url_name(mode):
    return "mailings:list_member_add" if mode == "add" else "mailings:list_member_remove"


def _confirm_url_name(mode):
    return "mailings:list_member_add_confirm" if mode == "add" else "mailings:list_member_remove_confirm"


def _commit_url_name(mode):
    return "mailings:list_member_commit_add" if mode == "add" else "mailings:list_member_commit_remove"


class _MemberSelectionView(LoginRequiredMixin, View):
    """個別追加・個別削除 選択画面の共通基底（仕様書 §3.2 / §3.4）。

    mode 属性で 'add' / 'remove' を分岐。母集合 SQL のみ mode で違い、それ以外
    （session / restore / ガード / レンダリング）は共通化。

    GET: 母集合の検索結果と選択状態（session または ?restore=1 で復元）を render。
         restore=1 以外の GET は対応 session を破棄して新規開始（§6.4）。
    POST: 選択された person_ids を session に保存し確認画面へ 302（PRG）。
    """

    mode = None  # 'add' or 'remove'
    template_name = "mailings/_member_selection.html"

    def get(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        key = _selection_session_key(mailing_list.pk, self.mode)
        if request.GET.get("restore") != "1":
            request.session.pop(key, None)
        selected_ids = set(str(x) for x in (request.session.get(key) or []))
        candidates_qs = self._build_candidates(mailing_list, request.GET)
        total = candidates_qs.count()
        # 安全上限：通常は 50 件 + 展開で十分。大量データでも 1,000 件で頭打ち。
        candidates = list(candidates_qs[:1000])
        context = self._build_context(
            request, mailing_list, candidates, total, selected_ids
        )
        return render(request, self.template_name, context)

    def post(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        person_ids = _sanitize_uuid_list(request.POST.getlist("person_ids"))
        if not person_ids:
            messages.warning(request, "Person を 1 件以上選択してください。")
            return redirect(_selection_url_name(self.mode), pk=mailing_list.pk)
        # snapshot を session に保存（§3.7 / §6.3）。
        request.session[_selection_session_key(mailing_list.pk, self.mode)] = person_ids
        request.session.modified = True
        target = reverse(_confirm_url_name(self.mode), args=[mailing_list.pk])
        back = BackNavigator(request)
        if back.back_stack:
            target = back.append_url(target)
        return redirect(target)

    # ------------------------------------------------------------------
    # mode 別の母集合 SQL
    # ------------------------------------------------------------------

    def _build_candidates(self, mailing_list, params):
        existing_member_ids = list(
            MailingListMember.objects.filter(mailing_list=mailing_list).values_list(
                "person_id", flat=True
            )
        )
        if self.mode == "add":
            # 母集合：このリストに未所属の status='active' な Person
            # （is_unsubscribed は問わない、§6.7）。
            # filter(status='active') は URL クエリで status=archived 等を直手入力された
            # ケースでも active 限定を担保するための二重防衛。
            qs = search_persons(params, default_statuses=("active",))
            qs = qs.filter(status="active").exclude(pk__in=existing_member_ids)
            return _apply_sort_to_persons(qs, params)
        # remove: 母集合：このリストの現メンバー全件（status / is_unsubscribed は問わない、§6.7）。
        qs = Person.objects.filter(pk__in=existing_member_ids).select_related(
            "primary_contact"
        )
        qs = _apply_person_text_filters(qs, params)
        return _apply_sort_to_persons(qs, params)

    def _build_context(self, request, mailing_list, candidates, total, selected_ids):
        current_member_count = MailingListMember.objects.filter(
            mailing_list=mailing_list
        ).count()
        sort_key, sort_dir = _resolve_sort(request.GET)
        context = {
            "mailing_list": mailing_list,
            "mode": self.mode,
            "mode_label": "メンバーを追加" if self.mode == "add" else "メンバーを削除",
            "current_member_count": current_member_count,
            "candidates": candidates,
            "total_count": total,
            "display_limit": 50,
            "selected_ids": selected_ids,
            "current_sort": sort_key,
            "current_dir": sort_dir,
            "back": BackNavigator(request),
            "active_app": "mailings",
            "active_menu": "mailings:mailing_list_list",
            # _search_form.html partial 用 context
            "show_status_filter": False,
            "selected_statuses": ["active"],
            "reset_url": reverse(_selection_url_name(self.mode), args=[mailing_list.pk]),
            "submit_label": "検索",
            "self_url": reverse(_selection_url_name(self.mode), args=[mailing_list.pk]),
            "detail_url": reverse(
                "mailings:mailing_list_detail", args=[mailing_list.pk]
            ),
        }
        for key in SEARCH_PARAMS:
            context[key] = request.GET.get(key, "")
        return context


class MemberAddView(_MemberSelectionView):
    mode = "add"


class MemberRemoveView(_MemberSelectionView):
    mode = "remove"


class _MemberConfirmView(LoginRequiredMixin, View):
    """個別追加・個別削除 確認画面の共通基底（仕様書 §3.3 / §3.5、GET 専用）。

    session から snapshot を取り出して表示する。session 空時は対応する選択画面へ
    302 で差し戻し（§6.5、直叩き / 期限切れフォールバック）。
    """

    mode = None
    template_name = "mailings/_member_confirmation.html"

    def get(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        key = _selection_session_key(mailing_list.pk, self.mode)
        person_ids = request.session.get(key) or []
        if not _is_session_truthy(person_ids):
            messages.info(
                request, "選択がリセットされました。もう一度選択してください。"
            )
            return redirect(_selection_url_name(self.mode), pk=mailing_list.pk)
        persons_qs = Person.objects.filter(pk__in=person_ids).select_related(
            "primary_contact"
        )
        persons = list(_apply_sort_to_persons(persons_qs, request.GET))
        sort_key, sort_dir = _resolve_sort(request.GET)
        back = BackNavigator(request)
        back_to_selection = reverse(
            _selection_url_name(self.mode), args=[mailing_list.pk]
        ) + "?restore=1"
        if back.back_stack:
            back_to_selection = back.append_url(back_to_selection)
        commit_url = reverse(_commit_url_name(self.mode), args=[mailing_list.pk])
        context = {
            "mailing_list": mailing_list,
            "mode": self.mode,
            "mode_label": "メンバーを追加" if self.mode == "add" else "メンバーを削除",
            "persons": persons,
            "total_count": len(persons),
            "display_limit": 50,
            "current_sort": sort_key,
            "current_dir": sort_dir,
            "back": back,
            "active_app": "mailings",
            "active_menu": "mailings:mailing_list_list",
            "back_to_selection_url": back_to_selection,
            "commit_url": commit_url,
        }
        return render(request, self.template_name, context)


class MemberAddConfirmView(_MemberConfirmView):
    mode = "add"


class MemberRemoveConfirmView(_MemberConfirmView):
    mode = "remove"


@method_decorator(require_POST, name="dispatch")
class _MemberCommitView(LoginRequiredMixin, View):
    """個別追加・個別削除 確定エンドポイント（仕様書 §3.4 / §3.6、POST 専用）。

    session の snapshot を DB に反映し、session クリア後に詳細画面へ 302（PRG）。
    確定処理は atomic ブロック内で実行する（§6.8）。
    """

    mode = None

    def post(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        key = _selection_session_key(mailing_list.pk, self.mode)
        person_ids = request.session.get(key) or []
        if not _is_session_truthy(person_ids):
            messages.warning(
                request, "セッションが切れています。もう一度選択してください。"
            )
            return redirect(_selection_url_name(self.mode), pk=mailing_list.pk)
        with transaction.atomic():
            if self.mode == "add":
                persons = list(Person.objects.filter(pk__in=person_ids))
                to_create = [
                    MailingListMember(
                        mailing_list=mailing_list, person=p, added_by=request.user
                    )
                    for p in persons
                ]
                MailingListMember.objects.bulk_create(
                    to_create, ignore_conflicts=True
                )
            else:
                MailingListMember.objects.filter(
                    mailing_list=mailing_list, person__in=person_ids
                ).delete()
        request.session.pop(key, None)
        request.session.modified = True
        target = reverse("mailings:mailing_list_detail", args=[mailing_list.pk])
        back = BackNavigator(request)
        if back.back_stack:
            target = back.append_url(target)
        return redirect(target)


class MemberAddCommitView(_MemberCommitView):
    mode = "add"


class MemberRemoveCommitView(_MemberCommitView):
    mode = "remove"


# ======================================================================
# Phase 1c-β-2a 新規作成ウィザード（仕様書 rev6 §4.5、§10 #17〜#20 #25）
# ======================================================================
#
# 1-B → 1-C → 1-D → 確定 の 4 ステップで MailingList + MailingListMember を
# snapshot 方式で一括作成する。session 'mailing_list_new_create_state' を
# 唯一の真実として保持し、各 GET 冒頭でステップ進行ガードする（§4.5.1a）。
#
# 既存 MailingListCreateView（リスト本体のみ作成 → 編集画面でタグ抽出 → 凍結）
# とは別フローとして併存。旧フローは β-3 完成まで触らない。
#
# β-2a スコープ：バックエンド（view / URL / セッション / テンプレ骨格）まで。
# §4.4 タグ選択 UI の動的挙動（プレビュー AJAX 発火・警告ダイアログ・備考
# プリセット等）は β-2b でコード君B が担当する。

NEW_LIST_SESSION_KEY = "mailing_list_new_create_state"


def _new_list_state(request):
    """[性質] 純関数。session からウィザード状態 dict を取り出す（無ければ空 dict）。"""
    return request.session.get(NEW_LIST_SESSION_KEY) or {}


def _new_list_save(request, **patch):
    """[性質] 副作用あり（session 書き込み）。state を patch して保存する。"""
    state = _new_list_state(request)
    state.update(patch)
    request.session[NEW_LIST_SESSION_KEY] = state
    request.session.modified = True
    return state


def _new_list_clear(request):
    """[性質] 副作用あり（session 削除）。確定 / キャンセル時の session クリア。"""
    if NEW_LIST_SESSION_KEY in request.session:
        del request.session[NEW_LIST_SESSION_KEY]
        request.session.modified = True


def _new_list_guard(request, *, required):
    """[性質] 副作用あり（HttpResponse 返却 or None）。

    required: tuple[str, ...] 必須 session キー（順序保持）
    どれか欠けていれば「揃っている最後のステップ」へ 302 差し戻し（§4.5.1a / §9.2-44）。
    すべて揃っていれば None を返し、呼び出し元は通常処理を続行。
    """
    state = _new_list_state(request)
    if all(key in state and state[key] for key in required):
        return None
    # snapshot_person_ids は空リストも falsy になるため、上の all() で空リストは
    # 「欠落扱い」になる。これは 1-D の前提（1-C POST で必ず非空保存）と整合。

    # どこまで揃っているかで差し戻し先を決定
    if "name" not in state or not state.get("name"):
        return redirect("mailings:new_list_meta")
    # name はあるが snapshot 等が無い → 1-C へ
    return redirect("mailings:new_list_tag_selection")


def _all_categories_with_tags():
    """[性質] 準関数。1-C 描画用：全アクティブカテゴリ + 各カテゴリのアクティブタグ。

    順序：TagCategory.Meta.ordering (sort_order, name)、Tag.Meta.ordering
    (category__sort_order, name)。
    """
    cats = (
        TagCategory.objects.filter(is_archived=False)
        .prefetch_related("tags")
        .order_by("sort_order", "name")
    )
    result = []
    for cat in cats:
        tags = list(cat.tags.filter(is_archived=False).order_by("name"))
        result.append({"category": cat, "tags": tags})
    return result


def _all_active_tags():
    """[性質] 準関数。横断除外 UI の候補：全アクティブカテゴリ × 全アクティブタグ。

    β-2a では JS 検索ダイアログを実装しないため、横断除外用の単純 multi-select
    リストとして全件を渡す。β-2b で JS 検索化される予定。
    """
    return list(
        Tag.objects.filter(
            is_archived=False, category__is_archived=False
        )
        .select_related("category")
        .order_by("category__sort_order", "category__name", "name")
    )


def _parse_conditions_from_post(post):
    """[性質] 純関数。1-C POST から conditions dict を組み立てる（§4.1.4）。

    フォームの規約：
      - 含むタグ：name="include_<category_id>"（multiple checkbox、tag_id 値）
      - 除外タグ：name="exclude_<category_id>"（multiple checkbox）
      - 演算切替：name="operator_<category_id>"（select、"OR" or "AND"、既定 "OR"）
      - 横断除外：name="global_exclude_tag_ids"（multiple checkbox）
    """
    categories = []
    seen_cat_ids = set()
    for key in post.keys():
        if not key.startswith("include_"):
            continue
        cat_id = key[len("include_"):]
        if cat_id in seen_cat_ids:
            continue
        seen_cat_ids.add(cat_id)
        include_ids = post.getlist(key)
        exclude_ids = post.getlist(f"exclude_{cat_id}")
        operator = post.get(f"operator_{cat_id}") or "OR"
        if operator not in ("OR", "AND"):
            operator = "OR"
        categories.append(
            {
                "category_id": cat_id,
                "operator": operator,
                "include_tag_ids": list(include_ids),
                "exclude_tag_ids": list(exclude_ids),
            }
        )
    global_exclude_ids = post.getlist("global_exclude_tag_ids")
    return {
        "categories": categories,
        "global_exclude_tag_ids": list(global_exclude_ids),
    }


def _conditions_is_empty(conditions):
    """[性質] 純関数。conditions が「実質空」かを判定（§3.5 サーバ側保険）。

    有効カテゴリ（include 非空）が 1 つもなく、横断除外も空なら True。
    """
    has_effective = any(
        bool((c or {}).get("include_tag_ids"))
        for c in (conditions or {}).get("categories", [])
    )
    has_global = bool((conditions or {}).get("global_exclude_tag_ids"))
    return not has_effective and not has_global


class NewListMetaView(LoginRequiredMixin, View):
    """1-B：リスト名・備考入力画面（仕様書 §4.5.2）。

    GET：session を初期化（step='meta'、name/description クリア）してフォーム表示。
    POST：name 必須検証 → name/description/step='tags' を session 保存 → 1-C へ 302。
    """

    template_name = "mailings/new_list_meta.html"

    def get(self, request):
        # 入口：session を初期化（途中放棄後の再入場で新規開始、§4.5.1a）。
        _new_list_clear(request)
        _new_list_save(request, step="meta", name="", description="")
        form = MailingListForm()
        return self._render(request, form)

    def post(self, request):
        form = MailingListForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)
        # リスト名重複は警告のみ、許可（ε.6 と同じ方針、エラーにしない）
        name = form.cleaned_data["name"]
        description = form.cleaned_data.get("description", "") or ""
        if MailingList.objects.filter(name=name, is_archived=False).exists():
            from django.contrib import messages

            messages.warning(
                request,
                f"同名のリスト「{name}」が既に存在します。問題なければそのまま進んでください。",
            )
        _new_list_save(
            request, step="tags", name=name, description=description
        )
        return redirect("mailings:new_list_tag_selection")

    def _render(self, request, form, *, status=200):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "back": BackNavigator(request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "list_url": reverse("mailings:mailing_list_list"),
            },
            status=status,
        )


class NewListTagSelectionView(LoginRequiredMixin, View):
    """1-C：タグ選択画面（新規作成モード）（仕様書 §4.5.3 / §4.4）。

    GET：ガード（name 必須）→ session 復元してテンプレ描画。
    POST：フォームから conditions 抽出 → サーバ側保険（実質空チェック）→
          extract_persons_by_tag_conditions で snapshot 生成 → session 保存 → 1-D へ 302。

    JS による動的更新（プレビュー AJAX・警告ダイアログ・備考プリセット）は β-2b
    でコード君B が実装する。β-2a はテンプレ骨格（カテゴリブロック・含む/除外/演算
    UI の DOM 構造と data-* / id フックポイント）まで完成させる。
    """

    template_name = "mailings/_tag_selection.html"

    def get(self, request):
        guard = _new_list_guard(request, required=("name",))
        if guard is not None:
            return guard
        state = _new_list_state(request)
        return self._render(request, state)

    def post(self, request):
        guard = _new_list_guard(request, required=("name",))
        if guard is not None:
            return guard
        conditions = _parse_conditions_from_post(request.POST)
        if _conditions_is_empty(conditions):
            from django.contrib import messages

            messages.error(
                request,
                "タグ条件が指定されていません。1 つ以上の含むタグまたは横断除外タグを指定してください。",
            )
            # 入力済み状態を session に書き戻して再描画用に保持
            state = _new_list_state(request)
            state["conditions"] = conditions
            request.session[NEW_LIST_SESSION_KEY] = state
            request.session.modified = True
            return self._render(request, state, status=400)
        # snapshot を生成（§9.2-26、確定時に再抽出しないため必須）
        qs = extract_persons_by_tag_conditions(conditions)
        snapshot_ids = [str(pk) for pk in qs.values_list("pk", flat=True)]
        _new_list_save(
            request,
            step="confirm",
            conditions=conditions,
            snapshot_person_ids=snapshot_ids,
        )
        return redirect("mailings:new_list_confirm")

    def _render(self, request, state, *, status=200):
        categories = _all_categories_with_tags()
        global_exclude_tags = _all_active_tags()
        return render(
            request,
            self.template_name,
            {
                "mode": "create",
                "list_name": state.get("name", ""),
                "list_description": state.get("description", "") or "",
                "categories": categories,
                "global_exclude_tags": global_exclude_tags,
                "current_conditions": state.get("conditions")
                or {"categories": [], "global_exclude_tag_ids": []},
                "preview_url": reverse("mailings:mailing_list_preview_v2"),
                "post_url": reverse("mailings:new_list_tag_selection"),
                "back_url": reverse("mailings:new_list_meta"),
                "cancel_url": reverse("mailings:mailing_list_list"),
                "back": BackNavigator(request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
            },
            status=status,
        )


class NewListConfirmView(LoginRequiredMixin, View):
    """1-D：新規作成確認画面（仕様書 §4.5.4、GET 専用）。

    ガード：name かつ snapshot_person_ids が無ければ 1-B / 1-C に 302（§9.2-45）。
    表示はリスト名・備考 + 「対象 N 件でリストを作成します」のみ（§9.2-42、Person
    一覧は出さない）。
    """

    template_name = "mailings/_new_list_confirmation.html"

    def get(self, request):
        guard = _new_list_guard(
            request, required=("name", "snapshot_person_ids")
        )
        if guard is not None:
            return guard
        state = _new_list_state(request)
        return render(
            request,
            self.template_name,
            {
                "list_name": state["name"],
                "list_description": state.get("description", "") or "",
                "snapshot_count": len(state.get("snapshot_person_ids") or []),
                "commit_url": reverse("mailings:new_list_commit"),
                "back_url": reverse("mailings:new_list_tag_selection"),
                "cancel_url": reverse("mailings:mailing_list_list"),
                "back": BackNavigator(request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
            },
        )


@method_decorator(require_POST, name="dispatch")
class NewListCommitView(LoginRequiredMixin, View):
    """新規作成確定エンドポイント（仕様書 §4.5.4、POST 専用、PRG）。

    snapshot から MailingList + MailingListMember を一括作成し、session を
    クリアして詳細画面に 302（§9.2-27 PRG パターン）。
    """

    def post(self, request):
        guard = _new_list_guard(
            request, required=("name", "snapshot_person_ids")
        )
        if guard is not None:
            return guard
        state = _new_list_state(request)
        name = state["name"]
        description = state.get("description", "") or ""
        snapshot_ids = state.get("snapshot_person_ids") or []
        with transaction.atomic():
            mailing_list = MailingList.objects.create(
                name=name, description=description, created_by=request.user
            )
            # Person 実体を取り直して MailingListMember を bulk_create
            # （snapshot から落ちている Person は ignore_conflicts ではなく
            # filter で自然に消える。snapshot 後に Person が CASCADE 削除されたら
            # その分だけ件数が減る。1-D 表示件数とのズレは詳細画面の実カウントが正、§9.2-38）。
            persons = Person.objects.filter(pk__in=snapshot_ids)
            to_create = [
                MailingListMember(
                    mailing_list=mailing_list,
                    person=p,
                    added_by=request.user,
                )
                for p in persons
            ]
            MailingListMember.objects.bulk_create(
                to_create, ignore_conflicts=True
            )
        _new_list_clear(request)
        return redirect("mailings:mailing_list_detail", pk=mailing_list.pk)


# ======================================================================
# Phase 1c-β-1 新プレビュー API（仕様書 rev5 §4.3）
# ======================================================================
#
# POST /mailings/lists/preview-v2/
# 拡張集合演算（カテゴリ内 OR/AND/NOT + カテゴリ間 AND/NOT）に対応した
# プレビュー API。既存 /mailings/lists/preview/（POST）は β で呼び出し元が
# 消えるが、v1.7+ まで残置（§9.2-21）。
#
# エラー形式：既存 AJAX（update-meta / add-member / remove-member）に揃え
# {"ok": false, "error": "<code>", "message": "..."} 形式を採用。
# 仕様書 §4.3.5 の {"code": ..., "message": ...} から code → error に変更。

_PREVIEW_V2_SAMPLE_LIMIT = 10


def _normalize_uuid_str(value):
    """[性質] 純関数。UUID 文字列を 36 文字ハイフン有り正規形に正規化。

    ハイフンなし 32 文字や bytes は受け付けない（§4.3.5、invalid_uuid 扱い）。
    None / 空文字は ValueError、不正形式も ValueError。
    """
    if not isinstance(value, str):
        raise ValueError("not a string")
    # ハイフンなし 32 文字を弾く（仕様書 §4.3.5 で明示）。
    if "-" not in value or len(value) != 36:
        raise ValueError("must be 36-char hyphenated UUID")
    return str(uuid.UUID(value))


def _validate_uuid_list(values, *, field_name):
    """[性質] 純関数。UUID 文字列リストを検証し、正規化済みリストを返す。

    [例外] ValueError（invalid_uuid 扱い、message に field_name を含む）
    """
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    out = []
    for v in values:
        try:
            out.append(_normalize_uuid_str(v))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"{field_name} contains invalid uuid: {v!r}")
    return out


def _preview_v2_error(error_code, message, *, status):
    """[性質] 副作用あり（JsonResponse 返却）。既存 AJAX 形式のエラーを返す。"""
    return JsonResponse(
        {"ok": False, "error": error_code, "message": message},
        status=status,
    )


@method_decorator(require_POST, name="dispatch")
class PreviewV2View(LoginRequiredMixin, View):
    """新プレビュー API（仕様書 §4.3、Phase 1c-β-1）。

    POST /mailings/lists/preview-v2/（application/json）

    リクエスト：仕様書 §4.3.2 参照（list_id 任意、conditions 必須）。
    レスポンス：仕様書 §4.3.3 参照。
      total_count / new_count / already_in_list と、
      中立別名 out_of_list_count (== new_count) / in_list_count (== already_in_list) を
      同じ計算結果から組み立てて返す（§4.3.3 二重計算禁止）。
    samples：10 件固定、id 昇順、address 含む。

    存在しない / archived な tag_id / category_id は silently ignore し、
    レスポンスの invalid_tag_ids / invalid_category_ids で通知（§4.3.5）。
    """

    def post(self, request):
        # ---------- リクエスト body 解析 ----------
        if not request.body:
            return _preview_v2_error(
                "invalid_json", "リクエスト body が空です。", status=400
            )
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _preview_v2_error(
                "invalid_json",
                "リクエスト body が JSON として不正です。",
                status=400,
            )
        if not isinstance(payload, dict):
            return _preview_v2_error(
                "invalid_json",
                "リクエスト body は JSON オブジェクトである必要があります。",
                status=400,
            )

        # ---------- list_id 検証 ----------
        list_id_raw = payload.get("list_id", None)
        mailing_list = None
        if list_id_raw is not None:
            try:
                list_id = _normalize_uuid_str(list_id_raw)
            except (ValueError, AttributeError, TypeError):
                return _preview_v2_error(
                    "invalid_uuid",
                    "list_id は 36 文字ハイフン有り UUID 形式である必要があります。",
                    status=400,
                )
            try:
                mailing_list = MailingList.objects.get(pk=list_id)
            except MailingList.DoesNotExist:
                return _preview_v2_error(
                    "list_not_found",
                    "指定された list_id の MailingList が存在しません。",
                    status=404,
                )

        # ---------- conditions 検証 ----------
        conditions = payload.get("conditions")
        if not isinstance(conditions, dict):
            return _preview_v2_error(
                "missing_field",
                "conditions は object である必要があります。",
                status=400,
            )
        if "categories" not in conditions:
            return _preview_v2_error(
                "missing_field",
                "conditions.categories は必須です。",
                status=400,
            )
        if "global_exclude_tag_ids" not in conditions:
            return _preview_v2_error(
                "missing_field",
                "conditions.global_exclude_tag_ids は必須です（空配列で表現してください）。",
                status=400,
            )
        categories_raw = conditions["categories"]
        if not isinstance(categories_raw, list):
            return _preview_v2_error(
                "missing_field",
                "conditions.categories は配列である必要があります。",
                status=400,
            )

        # global_exclude_tag_ids 検証
        try:
            global_exclude_raw = _validate_uuid_list(
                conditions["global_exclude_tag_ids"],
                field_name="conditions.global_exclude_tag_ids",
            )
        except ValueError as e:
            return _preview_v2_error("invalid_uuid", str(e), status=400)

        # categories 各要素の検証
        seen_category_ids = set()
        category_specs = []
        for i, cat in enumerate(categories_raw):
            if not isinstance(cat, dict):
                return _preview_v2_error(
                    "missing_field",
                    f"conditions.categories[{i}] は object である必要があります。",
                    status=400,
                )
            for key in ("category_id", "include_tag_ids", "exclude_tag_ids"):
                if key not in cat:
                    return _preview_v2_error(
                        "missing_field",
                        f"conditions.categories[{i}].{key} は必須です。",
                        status=400,
                    )
            try:
                cat_id = _normalize_uuid_str(cat["category_id"])
            except (ValueError, AttributeError, TypeError):
                return _preview_v2_error(
                    "invalid_uuid",
                    f"conditions.categories[{i}].category_id は 36 文字ハイフン有り UUID 形式である必要があります。",
                    status=400,
                )
            if cat_id in seen_category_ids:
                return _preview_v2_error(
                    "duplicate_category",
                    f"category_id {cat_id} が categories 配列内で重複しています。",
                    status=400,
                )
            seen_category_ids.add(cat_id)
            operator = cat.get("operator", "OR")
            if operator not in ("OR", "AND"):
                return _preview_v2_error(
                    "invalid_operator",
                    f"conditions.categories[{i}].operator は 'OR' または 'AND' である必要があります。",
                    status=400,
                )
            try:
                include_ids_raw = _validate_uuid_list(
                    cat["include_tag_ids"],
                    field_name=f"conditions.categories[{i}].include_tag_ids",
                )
                exclude_ids_raw = _validate_uuid_list(
                    cat["exclude_tag_ids"],
                    field_name=f"conditions.categories[{i}].exclude_tag_ids",
                )
            except ValueError as e:
                return _preview_v2_error("invalid_uuid", str(e), status=400)
            category_specs.append(
                {
                    "category_id": cat_id,
                    "operator": operator,
                    "include_tag_ids": include_ids_raw,
                    "exclude_tag_ids": exclude_ids_raw,
                }
            )

        # ---------- silently ignore：存在 / archived チェック ----------
        all_tag_ids = set()
        for spec in category_specs:
            all_tag_ids.update(spec["include_tag_ids"])
            all_tag_ids.update(spec["exclude_tag_ids"])
        all_tag_ids.update(global_exclude_raw)
        valid_tag_ids = set(
            str(t) for t in Tag.objects.filter(
                pk__in=all_tag_ids, is_archived=False
            ).values_list("pk", flat=True)
        )
        invalid_tag_ids = sorted(all_tag_ids - valid_tag_ids)

        all_category_ids = set(spec["category_id"] for spec in category_specs)
        valid_category_ids = set(
            str(c) for c in TagCategory.objects.filter(
                pk__in=all_category_ids, is_archived=False
            ).values_list("pk", flat=True)
        )
        invalid_category_ids = sorted(all_category_ids - valid_category_ids)

        # 不正カテゴリは丸ごと無視、不正タグは個別に剝がす（残った include_tag_ids が空に
        # なれば、そのカテゴリは判定ルール §4.1.4 で「無視」される自然帰結）。
        filtered_specs = []
        for spec in category_specs:
            if spec["category_id"] not in valid_category_ids:
                continue
            filtered_specs.append(
                {
                    "category_id": spec["category_id"],
                    "operator": spec["operator"],
                    "include_tag_ids": [
                        t for t in spec["include_tag_ids"] if t in valid_tag_ids
                    ],
                    "exclude_tag_ids": [
                        t for t in spec["exclude_tag_ids"] if t in valid_tag_ids
                    ],
                }
            )
        filtered_global_exclude = [
            t for t in global_exclude_raw if t in valid_tag_ids
        ]

        # ---------- 抽出 + count 計算（1 回だけ） ----------
        qs = extract_persons_by_tag_conditions(
            {
                "categories": filtered_specs,
                "global_exclude_tag_ids": filtered_global_exclude,
            }
        )
        total_count = qs.count()

        new_count = None
        already_in_list_count = None
        if mailing_list is not None:
            member_person_ids = MailingListMember.objects.filter(
                mailing_list=mailing_list
            ).values("person_id")
            already_in_list_count = qs.filter(pk__in=member_person_ids).count()
            new_count = total_count - already_in_list_count
            member_person_id_set = set(
                str(p) for p in MailingListMember.objects.filter(
                    mailing_list=mailing_list
                ).values_list("person_id", flat=True)
            )
        else:
            member_person_id_set = set()

        # ---------- samples（10 件、id 昇順、address 含む、N+1 回避） ----------
        sample_qs = qs.select_related("primary_contact").order_by("pk")[
            :_PREVIEW_V2_SAMPLE_LIMIT
        ]
        samples = []
        for person in sample_qs:
            primary = person.primary_contact
            samples.append(
                {
                    "id": str(person.id),
                    "name": (primary.full_name if primary else "") or "(氏名なし)",
                    "company": (primary.organization if primary else "") or "",
                    "department": (primary.department if primary else "") or "",
                    "title": (primary.title if primary else "") or "",
                    "address": (primary.address if primary else "") or "",
                    "email": (primary.email if primary else "") or "",
                    "is_unsubscribed": bool(person.is_unsubscribed),
                    "already_in_list": (
                        str(person.id) in member_person_id_set
                        if mailing_list is not None
                        else False
                    ),
                }
            )

        # ---------- レスポンス組み立て（エイリアスは同じ値を別名で詰めるだけ） ----------
        return JsonResponse(
            {
                "total_count": total_count,
                "new_count": new_count,
                "already_in_list": already_in_list_count,
                "out_of_list_count": new_count,
                "in_list_count": already_in_list_count,
                "invalid_tag_ids": invalid_tag_ids,
                "invalid_category_ids": invalid_category_ids,
                "samples": samples,
            }
        )


# ======================================================================
# MailingConfig 編集（§4.13、シングルトン）
# ======================================================================


class MailingConfigEditView(LoginRequiredMixin, View):
    """シングルトン MailingConfig の編集画面。

    初回アクセス時は get_or_create で id=1 のレコード自動作成（§3-3 / 1b-α list_freeze.py）。
    GET: フォーム表示。POST: 検証 → 保存 → 同画面リダイレクト（success_url なし、PRG パターン）。
    """

    template_name = "mailings/mailing_config_form.html"

    def get(self, request):
        config = get_or_create_singleton_mailing_config()
        form = MailingConfigForm(instance=config)
        return self._render(request, form, config)

    def post(self, request):
        config = get_or_create_singleton_mailing_config()
        form = MailingConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            from django.contrib import messages

            messages.success(request, "配信設定を保存しました。")
            return redirect("mailings:config_edit")
        return self._render(request, form, config)

    def _render(self, request, form, config):
        from django.shortcuts import render

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "config": config,
                "back": BackNavigator(request),
                "active_app": "mailings",
                "active_menu": "mailings:config_edit",
            },
        )
