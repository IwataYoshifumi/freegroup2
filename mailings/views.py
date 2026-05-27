"""mailings アプリの View 層（仕様書 v1.6 §6.2.4 / §11.3 / §11.4 / §4.13）。

実装範囲（Phase 1b-γ）：
  - MailingList CRUD（一覧・作成・詳細・編集・論理削除）
  - 凍結 AJAX（freeze_members 呼び出し）
  - プレビュー AJAX（extract_persons_by_tags / count_persons_by_tags 呼び出し）
  - 対象外 AJAX（凍結後のメンバー個別物理削除、§11.7.2.1 増やす方向は実装しない）
  - MailingConfig 編集（シングルトン、get_or_create で初回自動作成）
"""

import json

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
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
from tags.models import TagCategory

from persons.services.person_search import SEARCH_PARAMS, search_persons

from .forms import MailingConfigForm, MailingListForm
from .models import MailingList, MailingListMember
from .services.list_freeze import (
    freeze_members,
    get_or_create_singleton_mailing_config,
)
from .services.tag_extraction import count_persons_by_tags, extract_persons_by_tags


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
        context.update(
            {
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "members": (
                    MailingListMember.objects.filter(mailing_list=self.object)
                    .select_related("person", "person__primary_contact", "added_by")
                    .order_by("created_at")
                ),
                "is_frozen": self.object.members_frozen_at is not None,
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
    """凍結 AJAX。タグ ID リストを受け取り extract_persons_by_tags → freeze_members。

    POST: form-encoded or JSON {"mailing_list_id": uuid, "tag_ids": [uuid, ...]}
    レスポンス: {"ok": true, "member_count": int, "members_frozen_at": iso8601}
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
        persons = extract_persons_by_tags(tag_ids or [])
        count = freeze_members(mailing_list, persons, request.user)
        mailing_list.refresh_from_db(fields=["members_frozen_at"])
        return JsonResponse(
            {
                "ok": True,
                "member_count": count,
                "members_frozen_at": (
                    mailing_list.members_frozen_at.isoformat()
                    if mailing_list.members_frozen_at
                    else None
                ),
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
