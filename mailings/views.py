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
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
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

from .forms import CampaignForm, EmailTemplateForm, MailingConfigForm, MailingListForm
from .models import Campaign, EmailTemplate, MailingList, MailingListMember
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


class CampaignOwnerRequiredMixin:
    """Campaign の所有者詳細判定を View に乗せる共通 Mixin（Phase 7 ⑤、rev20 §14.5）。

    判定ロジックは持たず、URL kwargs の `pk` から Campaign を取り
    services.permissions.can_view_campaign(user, campaign) を呼ぶだけ（正本は④の関数、
    ここに二重に書かない）。所有者でなければ PermissionDenied(403)。

    MRO 上は LoginRequiredMixin → PermissionRequiredMixin の後・本体 View の前に置く
    （認証 → 粗い Permission → 所有者判定 → 本体 の順）。所有者判定が必要な全 View
    （Detail/Update/Schedule/CancelSchedule/TestSend/Report 系/CSV/Preview/Archive）は
    URL に `<uuid:pk>`（= Campaign）を持つため一様に適用できる。

    アーカイブ済みの可否や 404 セマンティクスは各 View 自身の get_object /
    get_object_or_404 に委ねる（本 Mixin は所有者判定のみを担う）。
    """

    def dispatch(self, request, *args, **kwargs):
        from .models import Campaign as _Campaign
        from .services.permissions import can_view_campaign

        campaign = get_object_or_404(_Campaign, pk=kwargs.get("pk"))
        if not can_view_campaign(request.user, campaign):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


# ======================================================================
# MailingList CRUD（§4.11 / §11.3）
# ======================================================================


class MailingListListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    # 認可（Phase 7 ⑤-B-1、rev19 No.70）：mailings.view_mailinglist（全員）。所有者判定なし。
    permission_required = "mailings.view_mailinglist"
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
        back.push_current("", ["page", "archived_only"])
        context.update(
            {
                "back": back,
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "archived_only": _archived_only(self.request),
            }
        )
        return context


class MailingListCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """配信リスト新規作成（name / description のみ）。

    作成後はリスト編集画面（mailing_list_update）にリダイレクトし、そこでタグ選択・
    プレビュー・凍結を行う。Phase 1 のフロー（§11.4.1）の [1]〜[4] を 2 画面で実装。

    認可（Phase 7 ⑤-B-1、rev19 No.71）：mailings.add_mailinglist。所有者判定なし。
    """

    model = MailingList
    form_class = MailingListForm
    template_name = "mailings/mailing_list_form.html"
    permission_required = "mailings.add_mailinglist"

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


class MailingListDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    # 認可（Phase 7 ⑤-B-1、rev19 No.72）：mailings.view_mailinglist（全員）。所有者判定なし。
    permission_required = "mailings.view_mailinglist"
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


class MailingListUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """リスト編集画面（name/description）+ メンバー組成 UI（タグ選択・凍結 or 対象外）。

    凍結前（members_frozen_at が NULL）：タグ選択 + プレビュー + 凍結ボタン
    凍結後：メンバー一覧 + 対象外 AJAX（個別物理削除、§11.7.2.1 増やす方向は実装しない）

    認可（Phase 7 ⑤-B-1、rev19 No.73）：mailings.change_mailinglist。所有者判定なし。
    """

    model = MailingList
    form_class = MailingListForm
    template_name = "mailings/mailing_list_form.html"
    permission_required = "mailings.change_mailinglist"

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


class MailingListDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """論理アーカイブ化（is_archived=True、§11.3.4）。GET で確認画面、POST で実行。

    物理削除しない理由：Campaign の mailing_list FK が PROTECT（§4.2）のため
    （Phase 2 以降で Campaign が存在し始めると物理削除すると履歴が壊れる）。

    Phase 1b-δ で UI ラベルは「削除」→「アーカイブ化」に統一したが、コード内表現
    （URL 名・View 名）は維持する方針（指示書準拠）。

    認可（Phase 7 ⑤-B-1、rev19 No.74）：mailings.delete_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.delete_mailinglist"

    def get(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk, is_archived=False)
        return _render_delete_confirm(request, mailing_list)

    def post(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk, is_archived=False)
        mailing_list.is_archived = True
        mailing_list.save(update_fields=["is_archived", "updated_at"])
        return redirect("mailings:mailing_list_list")


class MailingListUnarchiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """配信リスト非アーカイブ化（is_archived=False、Phase 1b-δ 追加）。POST 専用。

    archived な MailingList を元に戻す。リダイレクト先は呼び出し元
    （back スタックがあればそこへ、無ければ詳細画面）。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist（状態変更）。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"

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
class MailingListFreezeView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """リスト保存 AJAX（rev14.1 §11.4.3、Phase 1b-ε.6 追補で「凍結保存」→「保存」）。

    タグ ID リストを受け取り extract_persons_by_tags → freeze_members で
    MailingListMember を置き換え保存する。`members_frozen_at` は触らない（rev14.1）。

    凍結済み（members_frozen_at IS NOT NULL）リストは編集禁止（§11.3.6 dispatch ガード）。

    POST: form-encoded or JSON {"mailing_list_id": uuid, "tag_ids": [uuid, ...]}
    レスポンス成功: {"ok": true, "member_count": int}
    レスポンス凍結: HTTP 409 {"ok": false, "error": "frozen", "message": str}

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist（既存リストの状態変更）。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"

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
class MailingListUpdateMetaView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """リスト本体（name / description）の AJAX 自動保存（Phase 1b-ε.6 追補、修正 4）。

    rev14.1 §11.3.6：凍結後もリスト本体の編集は可能。本 View は archived のみ拒否し、
    frozen 時も保存を許可する。

    POST: form-encoded or JSON {"name": str, "description": str}
    レスポンス成功: {"ok": true, "name": str, "description": str, "updated_at": iso8601}
    レスポンス失敗: HTTP 400 {"ok": false, "errors": {field: [msg, ...]}}
    レスポンス archived: HTTP 404 {"ok": false, "error": "archived"}

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"

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
class MailingListPreviewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """プレビュー AJAX。タグ ID リストの抽出結果件数 + 先頭サンプル（10 件）を返す。

    POST: form-encoded or JSON {"tag_ids": [uuid, ...]}
    レスポンス: {"ok": true, "count": int, "samples": [{"id": uuid, "name": str, "org": str}, ...]}

    認可（Phase 7 ⑤-B-1）：mailings.view_mailinglist（読み取り専用プレビュー）。所有者判定なし。
    """

    permission_required = "mailings.view_mailinglist"

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
class MailingListAddMemberView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """メンバー追加 AJAX（rev14.1 §5.1 No.32、Phase 1b-ε.6 追加）。

    リスト編集中（未凍結）の手動メンバー追加。凍結済み（members_frozen_at IS NOT NULL）
    のときは HTTP 409 Conflict で弾く（§11.3.6 dispatch ガード）。

    POST: form-encoded or JSON {"person_ids": [uuid, ...]}
    レスポンス成功: {"ok": true, "created_count": int, "members": [{member_id, person_id, name, org, title, email}]}
    レスポンス凍結: HTTP 409 {"ok": false, "error": "frozen", "message": str}
    レスポンス archived: HTTP 404 {"ok": false, "error": "archived"}

    認可（Phase 7 ⑤-B-1、rev19 No.75）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"

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
class MailingListRemoveMemberView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """メンバー削除 AJAX（rev14.1 §5.1 No.33、Phase 1b-ε.6 追加）。

    リスト編集中（未凍結）の手動メンバー削除。凍結済みは HTTP 409 で弾く（§11.3.6）。

    POST: form-encoded or JSON {"member_id": uuid}
    レスポンス成功: {"ok": true, "removed_member_id": uuid}
    レスポンス凍結: HTTP 409 {"ok": false, "error": "frozen", "message": str}
    レスポンス archived: HTTP 404 {"ok": false, "error": "archived"}

    認可（Phase 7 ⑤-B-1、rev19 No.76）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"

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
class MailingListMemberRemoveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """対象外 AJAX。凍結後の MailingListMember を物理削除する（§11.7.2.1 / 過去発注書）。

    増やす方向は実装しない（凍結思想に反する）。members_frozen_at は更新しない。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。

    POST: form-encoded or JSON {"member_id": uuid}
    レスポンス: {"ok": true, "member_id": uuid}
    """

    permission_required = "mailings.change_mailinglist"

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


class _MemberSelectionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """個別追加・個別削除 選択画面の共通基底（仕様書 §3.2 / §3.4）。

    mode 属性で 'add' / 'remove' を分岐。母集合 SQL のみ mode で違い、それ以外
    （session / restore / ガード / レンダリング）は共通化。

    GET: 母集合の検索結果と選択状態（session または ?restore=1 で復元）を render。
         restore=1 以外の GET は対応 session を破棄して新規開始（§6.4）。
    POST: 選択された person_ids を session に保存し確認画面へ 302（PRG）。
    """

    # 認可（Phase 7 ⑤-B-1）：メンバー編集フローは mailings.change_mailinglist。所有者判定なし。
    permission_required = "mailings.change_mailinglist"
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


class _MemberConfirmView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """個別追加・個別削除 確認画面の共通基底（仕様書 §3.3 / §3.5、GET 専用）。

    session から snapshot を取り出して表示する。session 空時は対応する選択画面へ
    302 で差し戻し（§6.5、直叩き / 期限切れフォールバック）。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"
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
class _MemberCommitView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """個別追加・個別削除 確定エンドポイント（仕様書 §3.4 / §3.6、POST 専用）。

    session の snapshot を DB に反映し、session クリア後に詳細画面へ 302（PRG）。
    確定処理は atomic ブロック内で実行する（§6.8）。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"
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
# Phase 1c-β-3a タグで追加（仕様書 rev6 §4.6.1 / §4.6.2 / §10 #21〜#24）
# ======================================================================
#
# 既存リストに「タグで対象を抽出して追加」する 3 ステップフロー：
#   1-H: MemberAddByTagView         GET/POST  /lists/<pk>/members/add-by-tag/
#   1-K: MemberAddByTagConfirmView  GET       /lists/<pk>/members/add-by-tag/confirm/
#   確定: MemberAddByTagCommitView  POST      /lists/<pk>/members/confirm-add-by-tag/
#
# session キー：mailing_list_<pk>_add_by_tag_state（snapshot は新規追加対象のみ）
# snapshot は「抽出 − 既登録」（指示書 §2.2、退会者は含む）
# テンプレ：_tag_selection.html（mode='add_by_tag'）/ _member_confirmation.html（mode='add_by_tag'）
#
# 凍結チェックは既存 _guard_member_edit() を流用（409 + エラーページ、§6.2）。
# bulk_create + ignore_conflicts は個別追加 confirm-add と同じ作法（§3.6）。

ADD_BY_TAG_SESSION_PREFIX = "mailing_list_"
ADD_BY_TAG_SESSION_SUFFIX = "_add_by_tag_state"


def _add_by_tag_session_key(mailing_list_pk):
    """[性質] 純関数。1-H / 1-K / commit で共有する session キー名を返す。"""
    return f"{ADD_BY_TAG_SESSION_PREFIX}{mailing_list_pk}{ADD_BY_TAG_SESSION_SUFFIX}"


class MemberAddByTagView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """1-H：タグで追加 画面（仕様書 §4.6.1）。

    GET：current_conditions を session から復元してテンプレ描画
         （初回 GET で session pop しない＝戻り時に状態保持、§7.1）。
    POST：conditions を受け取り extract_persons_by_tag_conditions で抽出 → 既登録
          を除外して snapshot 生成 → session 保存 → 1-K へ 302（PRG）。

    snapshot は「抽出 − 既登録」のみ。退会者は含める（is_unsubscribed フィルタは
    抽出層・本層で適用しない、§9.2-30）。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"
    template_name = "mailings/_tag_selection.html"

    def get(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        state = request.session.get(_add_by_tag_session_key(mailing_list.pk)) or {}
        current_conditions = state.get("conditions") or {
            "categories": [],
            "global_exclude_tag_ids": [],
        }
        return self._render(request, mailing_list, current_conditions)

    def post(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        conditions = _parse_conditions_from_post(request.POST)
        if _conditions_is_empty(conditions):
            messages.error(
                request,
                "タグ条件が指定されていません。1 つ以上の含むタグまたは横断除外タグを指定してください。",
            )
            return self._render(request, mailing_list, conditions, status=400)
        # 抽出（active のみ、is_unsubscribed 不問）
        extracted_qs = extract_persons_by_tag_conditions(conditions)
        extracted_ids = list(extracted_qs.values_list("pk", flat=True))
        # 既登録（同リストの現メンバー）を除外して「新規追加対象」を snapshot
        existing_ids = set(
            MailingListMember.objects.filter(
                mailing_list=mailing_list
            ).values_list("person_id", flat=True)
        )
        snapshot_ids = [str(pid) for pid in extracted_ids if pid not in existing_ids]
        request.session[_add_by_tag_session_key(mailing_list.pk)] = {
            "conditions": conditions,
            "snapshot_person_ids": snapshot_ids,
            "total_extracted": len(extracted_ids),
            "already_in_list_count": len(extracted_ids) - len(snapshot_ids),
        }
        request.session.modified = True
        target = reverse(
            "mailings:list_member_add_by_tag_confirm", args=[mailing_list.pk]
        )
        back = BackNavigator(request)
        if back.back_stack:
            target = back.append_url(target)
        return redirect(target)

    def _render(self, request, mailing_list, current_conditions, *, status=200):
        categories = _all_categories_with_tags()
        global_exclude_tags = _all_active_tags()
        current_member_count = MailingListMember.objects.filter(
            mailing_list=mailing_list
        ).count()
        return render(
            request,
            self.template_name,
            {
                "mode": "add_by_tag",
                "mailing_list": mailing_list,
                "current_member_count": current_member_count,
                "categories": categories,
                "global_exclude_tags": global_exclude_tags,
                "current_conditions": current_conditions,
                "preview_url": reverse("mailings:mailing_list_preview_v2"),
                "post_url": reverse(
                    "mailings:list_member_add_by_tag", args=[mailing_list.pk]
                ),
                "cancel_url": reverse(
                    "mailings:mailing_list_detail", args=[mailing_list.pk]
                ),
                "back": BackNavigator(request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
            },
            status=status,
        )


class MemberAddByTagConfirmView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """1-K：タグで追加 確認画面（仕様書 §4.6.2、GET 専用）。

    session が空なら 1-H に 302 差し戻し（§9.2-45）。
    snapshot の Person を一覧描画 + Before/After 件数 + 重複除外説明。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"
    template_name = "mailings/_member_confirmation.html"

    def get(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        state = request.session.get(_add_by_tag_session_key(mailing_list.pk)) or {}
        snapshot_ids = state.get("snapshot_person_ids") or []
        # snapshot 自体は空でも合法（抽出 0 件 or 全件既登録）。conditions が無ければ
        # ウィザード未開始 → 1-H に差し戻し。snapshot 空時は確認画面で 0 件表示する。
        if not state.get("conditions"):
            messages.info(
                request, "選択がリセットされました。もう一度タグを選択してください。"
            )
            return redirect(
                "mailings:list_member_add_by_tag", pk=mailing_list.pk
            )
        persons_qs = Person.objects.filter(pk__in=snapshot_ids).select_related(
            "primary_contact"
        )
        persons = list(_apply_sort_to_persons(persons_qs, request.GET))
        sort_key, sort_dir = _resolve_sort(request.GET)
        current_member_count = MailingListMember.objects.filter(
            mailing_list=mailing_list
        ).count()
        back = BackNavigator(request)
        back_to_selection = reverse(
            "mailings:list_member_add_by_tag", args=[mailing_list.pk]
        )
        if back.back_stack:
            back_to_selection = back.append_url(back_to_selection)
        commit_url = reverse(
            "mailings:list_member_commit_add_by_tag", args=[mailing_list.pk]
        )
        return render(
            request,
            self.template_name,
            {
                "mailing_list": mailing_list,
                "mode": "add_by_tag",
                "mode_label": "タグで追加",
                "persons": persons,
                "total_count": len(persons),
                "display_limit": 50,
                "current_sort": sort_key,
                "current_dir": sort_dir,
                # add_by_tag 固有：抽出 / 既登録 / 新規追加 と Before/After
                "total_extracted": state.get("total_extracted", 0),
                "already_in_list_count": state.get("already_in_list_count", 0),
                "new_count": len(snapshot_ids),
                "current_member_count": current_member_count,
                "after_count": current_member_count + len(snapshot_ids),
                "back": back,
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "back_to_selection_url": back_to_selection,
                "commit_url": commit_url,
            },
        )


@method_decorator(require_POST, name="dispatch")
class MemberAddByTagCommitView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """タグで追加 確定エンドポイント（仕様書 §4.6.2、POST 専用、PRG）。

    session の snapshot を bulk_create + ignore_conflicts で一括登録（個別追加
    confirm-add と同じ作法、§3.6）。確定後 session クリア → 詳細画面に 302。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"

    def post(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        key = _add_by_tag_session_key(mailing_list.pk)
        state = request.session.get(key) or {}
        snapshot_ids = state.get("snapshot_person_ids") or []
        if not state.get("conditions"):
            messages.warning(
                request, "セッションが切れています。もう一度タグを選択してください。"
            )
            return redirect("mailings:list_member_add_by_tag", pk=mailing_list.pk)
        with transaction.atomic():
            persons = list(Person.objects.filter(pk__in=snapshot_ids))
            to_create = [
                MailingListMember(
                    mailing_list=mailing_list, person=p, added_by=request.user
                )
                for p in persons
            ]
            if to_create:
                MailingListMember.objects.bulk_create(
                    to_create, ignore_conflicts=True
                )
        request.session.pop(key, None)
        request.session.modified = True
        target = reverse("mailings:mailing_list_detail", args=[mailing_list.pk])
        back = BackNavigator(request)
        if back.back_stack:
            target = back.append_url(target)
        return redirect(target)


# ======================================================================
# Phase 1c-β-3b タグで除外（仕様書 rev6 §4.6.5 / §10 #27〜#30 #32）
# ======================================================================
#
# 既存リストから「タグで対象を抽出して除外」する 3 ステップフロー：
#   1-L: MemberRemoveByTagView         GET/POST  /lists/<pk>/members/remove-by-tag/
#   1-M: MemberRemoveByTagConfirmView  GET       /lists/<pk>/members/remove-by-tag/confirm/
#   確定: MemberRemoveByTagCommitView  POST      /lists/<pk>/members/confirm-remove-by-tag/
#
# session キー：mailing_list_<pk>_remove_by_tag_state
# snapshot は「抽出 ∩ リスト内」（add とは逆、§3.2 / §4.6.5.2）。退会者は含む。
# 確定処理は bulk_delete（add の bulk_create と対）。
# テンプレ：_tag_selection.html / _member_confirmation.html（mode='remove_by_tag'）
#
# UI 誤操作防止（§4.6.5.4、§3.5）：①入口分離 ②赤バナー ③タブタイトル ④「除外対象 N 件」
# 赤太字 ⑤「外します」強調 ⑥「-N 件」赤マイナス ⑦「除外を確定」赤系 ⑧二重確認ダイアログ。

REMOVE_BY_TAG_SESSION_PREFIX = "mailing_list_"
REMOVE_BY_TAG_SESSION_SUFFIX = "_remove_by_tag_state"


def _remove_by_tag_session_key(mailing_list_pk):
    """[性質] 純関数。1-L / 1-M / commit で共有する session キー名を返す。"""
    return f"{REMOVE_BY_TAG_SESSION_PREFIX}{mailing_list_pk}{REMOVE_BY_TAG_SESSION_SUFFIX}"


class MemberRemoveByTagView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """1-L：タグで除外 画面（仕様書 §4.6.5.2）。

    GET：current_conditions を session から復元してテンプレ描画
         （初回 GET で session pop しない＝戻り時に状態保持、§7.1）。
    POST：conditions を受け取り extract_persons_by_tag_conditions で抽出 →
          リスト内メンバーと積集合をとって snapshot 生成 → session 保存 → 1-M。

    snapshot は「抽出 ∩ リスト内」（§3.2、add_by_tag の「抽出 − 既登録」と逆）。
    退会者も snapshot に含める（個別削除と整合、§9.2-30）。archived Person は
    preview-v2 が active のみ抽出するため、結果として snapshot に入らない
    （タグ経由では外せない、個別削除で対応、§4.6.5.5）。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"
    template_name = "mailings/_tag_selection.html"

    def get(self, request, pk):
        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        state = request.session.get(_remove_by_tag_session_key(mailing_list.pk)) or {}
        current_conditions = state.get("conditions") or {
            "categories": [],
            "global_exclude_tag_ids": [],
        }
        return self._render(request, mailing_list, current_conditions)

    def post(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        conditions = _parse_conditions_from_post(request.POST)
        if _conditions_is_empty(conditions):
            messages.error(
                request,
                "タグ条件が指定されていません。1 つ以上の含むタグまたは横断除外タグを指定してください。",
            )
            return self._render(request, mailing_list, conditions, status=400)
        # 抽出（active のみ、is_unsubscribed 不問）
        extracted_qs = extract_persons_by_tag_conditions(conditions)
        extracted_ids = set(str(pid) for pid in extracted_qs.values_list("pk", flat=True))
        # リスト内メンバーとの積集合 → 除外対象（§3.2、add とは逆）
        member_ids = set(
            str(pid)
            for pid in MailingListMember.objects.filter(
                mailing_list=mailing_list
            ).values_list("person_id", flat=True)
        )
        snapshot_ids = sorted(extracted_ids & member_ids)
        out_of_list_count = len(extracted_ids) - len(snapshot_ids)
        request.session[_remove_by_tag_session_key(mailing_list.pk)] = {
            "conditions": conditions,
            "snapshot_person_ids": snapshot_ids,
            "total_extracted": len(extracted_ids),
            "out_of_list_count": out_of_list_count,
        }
        request.session.modified = True
        target = reverse(
            "mailings:list_member_remove_by_tag_confirm", args=[mailing_list.pk]
        )
        back = BackNavigator(request)
        if back.back_stack:
            target = back.append_url(target)
        return redirect(target)

    def _render(self, request, mailing_list, current_conditions, *, status=200):
        categories = _all_categories_with_tags()
        global_exclude_tags = _all_active_tags()
        current_member_count = MailingListMember.objects.filter(
            mailing_list=mailing_list
        ).count()
        return render(
            request,
            self.template_name,
            {
                "mode": "remove_by_tag",
                "mailing_list": mailing_list,
                "current_member_count": current_member_count,
                "categories": categories,
                "global_exclude_tags": global_exclude_tags,
                "current_conditions": current_conditions,
                "preview_url": reverse("mailings:mailing_list_preview_v2"),
                "post_url": reverse(
                    "mailings:list_member_remove_by_tag", args=[mailing_list.pk]
                ),
                "cancel_url": reverse(
                    "mailings:mailing_list_detail", args=[mailing_list.pk]
                ),
                "back": BackNavigator(request),
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
            },
            status=status,
        )


class MemberRemoveByTagConfirmView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """1-M：タグで除外 確認画面（仕様書 §4.6.5.3、GET 専用）。

    session が空なら 1-L に 302 差し戻し（§9.2-45）。
    snapshot の Person を一覧描画 + Before/After「-N 件」+ 範囲説明。
    確定ボタン押下時の二重確認ダイアログは _member_confirmation.html 側で
    submit 時に confirm() を出す（§4.6.5.4-8）。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"
    template_name = "mailings/_member_confirmation.html"

    def get(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        state = request.session.get(_remove_by_tag_session_key(mailing_list.pk)) or {}
        snapshot_ids = state.get("snapshot_person_ids") or []
        if not state.get("conditions"):
            messages.info(
                request, "選択がリセットされました。もう一度タグを選択してください。"
            )
            return redirect(
                "mailings:list_member_remove_by_tag", pk=mailing_list.pk
            )
        persons_qs = Person.objects.filter(pk__in=snapshot_ids).select_related(
            "primary_contact"
        )
        persons = list(_apply_sort_to_persons(persons_qs, request.GET))
        sort_key, sort_dir = _resolve_sort(request.GET)
        current_member_count = MailingListMember.objects.filter(
            mailing_list=mailing_list
        ).count()
        back = BackNavigator(request)
        back_to_selection = reverse(
            "mailings:list_member_remove_by_tag", args=[mailing_list.pk]
        )
        if back.back_stack:
            back_to_selection = back.append_url(back_to_selection)
        commit_url = reverse(
            "mailings:list_member_commit_remove_by_tag", args=[mailing_list.pk]
        )
        removed_count = len(snapshot_ids)
        return render(
            request,
            self.template_name,
            {
                "mailing_list": mailing_list,
                "mode": "remove_by_tag",
                "mode_label": "タグで除外",
                "persons": persons,
                "total_count": len(persons),
                "display_limit": 50,
                "current_sort": sort_key,
                "current_dir": sort_dir,
                # remove_by_tag 固有：抽出 / リスト外 / 除外対象 と Before/After
                "total_extracted": state.get("total_extracted", 0),
                "out_of_list_count": state.get("out_of_list_count", 0),
                "removed_count": removed_count,
                "current_member_count": current_member_count,
                "after_count": current_member_count - removed_count,
                "back": back,
                "active_app": "mailings",
                "active_menu": "mailings:mailing_list_list",
                "back_to_selection_url": back_to_selection,
                "commit_url": commit_url,
            },
        )


@method_decorator(require_POST, name="dispatch")
class MemberRemoveByTagCommitView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """タグで除外 確定エンドポイント（仕様書 §4.6.5.3、POST 専用、PRG）。

    session の snapshot を bulk_delete で一括除外（add の bulk_create と対）。
    確定後 session クリア → 詳細画面に 302。

    認可（Phase 7 ⑤-B-1）：mailings.change_mailinglist。所有者判定なし。
    """

    permission_required = "mailings.change_mailinglist"

    def post(self, request, pk):
        from django.contrib import messages

        mailing_list = get_object_or_404(MailingList, pk=pk)
        guard = _guard_member_edit(request, mailing_list)
        if guard is not None:
            return guard
        key = _remove_by_tag_session_key(mailing_list.pk)
        state = request.session.get(key) or {}
        snapshot_ids = state.get("snapshot_person_ids") or []
        if not state.get("conditions"):
            messages.warning(
                request, "セッションが切れています。もう一度タグを選択してください。"
            )
            return redirect(
                "mailings:list_member_remove_by_tag", pk=mailing_list.pk
            )
        with transaction.atomic():
            if snapshot_ids:
                MailingListMember.objects.filter(
                    mailing_list=mailing_list, person_id__in=snapshot_ids
                ).delete()
        request.session.pop(key, None)
        request.session.modified = True
        target = reverse("mailings:mailing_list_detail", args=[mailing_list.pk])
        back = BackNavigator(request)
        if back.back_stack:
            target = back.append_url(target)
        return redirect(target)


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


class NewListMetaView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """1-B：リスト名・備考入力画面（仕様書 §4.5.2）。

    GET：session を初期化（step='meta'、name/description クリア）してフォーム表示。
    POST：name 必須検証 → name/description/step='tags' を session 保存 → 1-C へ 302。

    【作業5】 ?campaign=<uuid> でクエリパラメータが渡されたら、紐づけ対象の Campaign を
    検証して session に campaign_id を埋め込む。Commit 時に NewListCommitView が拾って
    Campaign.mailing_list を新リストに差し替え、Campaign 詳細画面へ戻る。

    認可（Phase 7 ⑤-B-1）：新規リスト作成フローのため mailings.add_mailinglist（入口で弾く）。
    """

    permission_required = "mailings.add_mailinglist"
    template_name = "mailings/new_list_meta.html"

    def get(self, request):
        # 入口：session を初期化（途中放棄後の再入場で新規開始、§4.5.1a）。
        _new_list_clear(request)
        _new_list_save(request, step="meta", name="", description="")
        # 【作業5】 ?campaign=<uuid> が渡されており、対象が draft / archived=False ならば
        # session に campaign_id を保存（commit 時に拾って差し替え + 詳細画面へ戻る）。
        campaign_id_param = request.GET.get("campaign", "")
        if campaign_id_param:
            target = Campaign.objects.filter(
                pk=campaign_id_param,
                is_archived=False,
                status=Campaign.Status.DRAFT,
            ).first()
            if target is not None:
                _new_list_save(request, campaign_id=str(target.pk))
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


class NewListTagSelectionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """1-C：タグ選択画面（新規作成モード）（仕様書 §4.5.3 / §4.4）。

    GET：ガード（name 必須）→ session 復元してテンプレ描画。
    POST：フォームから conditions 抽出 → サーバ側保険（実質空チェック）→
          extract_persons_by_tag_conditions で snapshot 生成 → session 保存 → 1-D へ 302。

    JS による動的更新（プレビュー AJAX・警告ダイアログ・備考プリセット）は β-2b
    でコード君B が実装する。β-2a はテンプレ骨格（カテゴリブロック・含む/除外/演算
    UI の DOM 構造と data-* / id フックポイント）まで完成させる。

    認可（Phase 7 ⑤-B-1）：新規リスト作成フローのため mailings.add_mailinglist。
    """

    permission_required = "mailings.add_mailinglist"
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


class NewListConfirmView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """1-D：新規作成確認画面（仕様書 §4.5.4、GET 専用）。

    ガード：name かつ snapshot_person_ids が無ければ 1-B / 1-C に 302（§9.2-45）。
    表示はリスト名・備考 + 「対象 N 件でリストを作成します」のみ（§9.2-42、Person
    一覧は出さない）。

    認可（Phase 7 ⑤-B-1）：新規リスト作成フローのため mailings.add_mailinglist。
    """

    permission_required = "mailings.add_mailinglist"
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
class NewListCommitView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """新規作成確定エンドポイント（仕様書 §4.5.4、POST 専用、PRG）。

    snapshot から MailingList + MailingListMember を一括作成し、session を
    クリアして詳細画面に 302（§9.2-27 PRG パターン）。

    認可（Phase 7 ⑤-B-1）：新規リスト作成フローのため mailings.add_mailinglist。
    """

    permission_required = "mailings.add_mailinglist"

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
        # 【作業5】 ウィザード入口で session に campaign_id が積まれていれば、
        # commit 時に新リスト作成と同 atomic で当該 Campaign の mailing_list を差し替える。
        # 差し替え条件は GET 時と同じ（draft / is_archived=False）を再確認（途中で status が
        # 変わった場合の保険）。差し替え失敗時は通常通り mailing_list_detail へ戻す。
        link_campaign_id = state.get("campaign_id")
        linked_campaign = None
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
            if link_campaign_id:
                linked_campaign = Campaign.objects.filter(
                    pk=link_campaign_id,
                    is_archived=False,
                    status=Campaign.Status.DRAFT,
                ).first()
                if linked_campaign is not None:
                    linked_campaign.mailing_list = mailing_list
                    linked_campaign.save(
                        update_fields=["mailing_list", "updated_at"]
                    )
        _new_list_clear(request)
        if linked_campaign is not None:
            from django.contrib import messages

            messages.success(
                request,
                f"新規リスト「{mailing_list.name}」を作成し、キャンペーンに紐づけました。",
            )
            return redirect(
                "mailings:campaign_detail", pk=linked_campaign.pk
            )
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
class PreviewV2View(LoginRequiredMixin, PermissionRequiredMixin, View):
    """新プレビュー API（仕様書 §4.3、Phase 1c-β-1）。

    認可（Phase 7 ⑤-B-1）：mailings.view_mailinglist（読み取り専用プレビュー）。所有者判定なし。

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

    permission_required = "mailings.view_mailinglist"

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


class MailingConfigEditView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """シングルトン MailingConfig の編集画面。

    初回アクセス時は get_or_create で id=1 のレコード自動作成（§3-3 / 1b-α list_freeze.py）。
    GET: フォーム表示。POST: 検証 → 保存 → 同画面リダイレクト（success_url なし、PRG パターン）。

    認可（Phase 7 ⑤）：mailings.change_mailingconfig（admin 専用、rev20 §14.1.2）。
    """

    permission_required = "mailings.change_mailingconfig"
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


# ======================================================================
# Phase 4：クリック中継ビュー（仕様書 v1.6 §8.1 / §8.2、rev14）
# ======================================================================


class TrackingRedirectView(View):
    """クリック中継ビュー（§8.1 / §5.1 No.34、URL: /t/<token>/）。

    ログイン不要・外部公開・CSRF 無関係（GET / HEAD のみ）。
    判定ロジックは純関数 detect_bot_status に分離（§2.2.2 / §8.2）。
    本ビューは「副作用」側：ClickLog 作成、TrackingLink の各カウンタ更新、302 リダイレクト。

    HEAD はメーラープリフェッチ用に 302 を返すが、有効クリックにはカウントしない
    （bot_reason="non_get"）。POST 等は現実にはほぼ来ないため 405 等の特別扱いは
    せず、http_method_names で GET/HEAD のみ受け付ける。

    同時クリック耐性：TrackingLink のカウンタ更新は QuerySet.update + F() で
    SQL レベルの "field = field + 1" として発行する（Python レベルの read-modify-write
    を避ける）。テストでも保証する。
    """

    http_method_names = ["get", "head"]

    def get(self, request, token):
        return self._handle(request, token)

    def head(self, request, token):
        # 順 1：non_get 扱いだが 302 は返す（メーラープリフェッチを正常終了させるため）。
        return self._handle(request, token)

    def _handle(self, request, token):
        from django.db.models import F
        from django.http import HttpResponseRedirect
        from django.shortcuts import get_object_or_404
        from django.utils import timezone

        from config.constants import (
            CLICK_PREFETCH_THRESHOLD_SECONDS,
            KNOWN_BOT_USER_AGENT_PATTERNS,
        )

        from .models import ClickLog, TrackingLink
        from .services.bot_detection import detect_bot_status
        from .services.ip_masking import mask_ip

        # 無効トークンは 404（ClickLog は tracking_link FK NOT NULL のため構造的に作れない）
        tracking_link = get_object_or_404(TrackingLink, token=token)

        http_method = request.method or ""
        user_agent = request.META.get("HTTP_USER_AGENT", "") or ""
        raw_ip = request.META.get("REMOTE_ADDR", "") or ""
        now = timezone.now()

        # duplicate 判定はサマリベース（TrackingLink.click_count > 0）。
        # 履歴ベース（is_valid_click=True の ClickLog 存在）と同じ結論になる
        # （click_count は is_valid_click=True 時のみ +1 される、§8.2）。
        has_existing_valid_click = tracking_link.click_count > 0

        is_valid_click, bot_reason = detect_bot_status(
            http_method=http_method,
            user_agent=user_agent,
            sent_at=tracking_link.created_at,
            now=now,
            threshold_seconds=CLICK_PREFETCH_THRESHOLD_SECONDS,
            bot_patterns=KNOWN_BOT_USER_AGENT_PATTERNS,
            has_existing_valid_click=has_existing_valid_click,
        )

        ClickLog.objects.create(
            tracking_link=tracking_link,
            ip_masked=mask_ip(raw_ip),
            user_agent=user_agent,
            http_method=http_method[:10],
            is_valid_click=is_valid_click,
            bot_reason=bot_reason,
        )

        # F() expression で race-safe にカウンタ更新（指示書「同時クリック耐性」）。
        # total_access_count はボット込みで毎回 +1（§8.2 末尾、§13.2）。
        # click_count / last_clicked_at は有効クリック時のみ。
        if is_valid_click:
            TrackingLink.objects.filter(pk=tracking_link.pk).update(
                total_access_count=F("total_access_count") + 1,
                click_count=F("click_count") + 1,
                last_clicked_at=now,
            )
        else:
            TrackingLink.objects.filter(pk=tracking_link.pk).update(
                total_access_count=F("total_access_count") + 1,
            )

        return HttpResponseRedirect(tracking_link.original_url)


# ======================================================================
# Phase 5：バウンス Webhook 受け口 + 配信停止 受信者 UI + 管理者代行・解除 UI +
# SuppressedEmail 管理 UI（仕様書 v1.6 §10.2 / §9 / §4.5A.2 / §4.8 / §6.2.7、rev14）
# ======================================================================


from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt


@method_decorator(csrf_exempt, name="dispatch")
class BounceWebhookView(View):
    """外部 MTA（SES 等）からの Webhook 受け口（§10.1 / §10.2、Phase 5 / rev15 是正）。

    【無防備な攻撃面にしないための多段防御（rev15 是正）】
    SuppressedEmail フィルタは OFF 不可・常時 ON 固定（§10.0）のため、無防備な
    Webhook 経由で任意メアドを `SuppressedEmail(bounce_hard)` に登録できると
    「生きている顧客に二度と配信できなくする」攻撃が外部から成立する。これを防ぐため：

      1. settings.BOUNCE_WEBHOOK_ENABLED が True でないと一切受け付けない（既定 False）
      2. settings.BOUNCE_WEBHOOK_SHARED_SECRET が設定されていないと一切受け付けない
      3. リクエストヘッダ X-Bounce-Webhook-Token が共有シークレットと一致しないと
         一切受け付けない

    上記いずれかで弾くときは 404 を返してエンドポイントの存在を隠す（攻撃者に情報を
    与えない）。FreeGroup2 のデフォルト運用は自社 SMTP（cron ポーリング）なので、
    大半の顧客で本エンドポイントは無効のまま安全に放置できる。

    【本 Phase の実装範囲】
    プロバイダ別 JSON 解析（SES Notification / EventBridge 形式）と正式な署名検証
    （HMAC 等）の作り込みは v1.6 後続 / 実運用後とする（コメントは維持）。本 View は
    「無防備な受け口を塞ぐ」ことに徹し、ペイロード形式は generic JSON で受ける。
    """

    http_method_names = ["post"]

    SECRET_HEADER = "HTTP_X_BOUNCE_WEBHOOK_TOKEN"

    def post(self, request):
        import json

        from django.conf import settings
        from django.http import Http404, JsonResponse

        from mailings.services.bounce_processor import (
            BOUNCE_TYPE_HARD,
            BOUNCE_TYPE_SOFT,
            process_bounce,
        )

        # 防御 1：settings フラグでデフォルト無効化（既定 False、§10.1 / rev15 是正）
        if not getattr(settings, "BOUNCE_WEBHOOK_ENABLED", False):
            raise Http404

        # 防御 2：共有シークレット未設定なら受け付けない（設定漏れで開きっぱなしを防ぐ）
        secret = getattr(settings, "BOUNCE_WEBHOOK_SHARED_SECRET", "") or ""
        if not secret:
            raise Http404

        # 防御 3：ヘッダのトークンが一致しないと受け付けない（不一致も 404 で存在を隠す）
        provided = request.META.get(self.SECRET_HEADER, "") or ""
        if provided != secret:
            raise Http404

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"error": "invalid json"}, status=400)

        # generic JSON フォーマット { "email": "...", "bounce_type": "hard|soft",
        # "reason": "..." }。プロバイダ別アダプタは v1.6 後続で追加（§10.1 末尾）。
        target_email = (payload.get("email") or "").strip()
        bounce_type = (payload.get("bounce_type") or "").strip().lower()
        reason = (payload.get("reason") or "")[:5000]

        if not target_email or bounce_type not in (BOUNCE_TYPE_HARD, BOUNCE_TYPE_SOFT):
            return JsonResponse({"error": "missing email or bounce_type"}, status=400)

        try:
            process_bounce(target_email, bounce_type, reason)
        except Exception as exc:
            # 5xx で外部 MTA に再送を促す挙動が必要なら status 500 を返すが、
            # 現状は受信側エラーで再送ループに陥らないよう 200 で握り潰す方針
            # （Phase 5 後続で適切な戦略を選ぶ）
            import logging

            logging.getLogger(__name__).exception(
                "BounceWebhookView: process_bounce raised: %s", exc
            )
            return JsonResponse({"status": "error", "detail": str(exc)}, status=200)

        return JsonResponse({"status": "ok"})


# ----------------------------------------------------------------------
# 配信停止 受信者 UI（§9.2 / §4.5A.2、URL: /u/<token>/ 系）
# ----------------------------------------------------------------------


def _mask_email(email: str) -> str:
    """メアドの local 部を 3 文字 + *** で覆う表示用ヘルパー（§6.2.7）。

    [性質] 純関数
    [入力] email: str（例 "tan_tan@example.com"）
    [出力] str（例 "tan***@example.com"）。短すぎる local 部は全マスクで返す。
    """
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    if len(local) <= 3:
        return f"***@{domain}"
    return f"{local[:3]}***@{domain}"


def _is_generic_email(email: str) -> bool:
    """メアドの local 部が代表メアド扱いか判定する（§9.1、Phase 5）。

    [性質] 純関数
    [入力] email: str
    [出力] bool（代表メアド扱いなら True）

    DUPLICATE_GENERIC_EMAIL_LOCALPARTS（v1.4.2 §8.7）と完全一致照合。
    大文字小文字は区別しない。
    """
    from config.constants import DUPLICATE_GENERIC_EMAIL_LOCALPARTS

    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower()
    return local in DUPLICATE_GENERIC_EMAIL_LOCALPARTS


class UnsubscribePageView(View):
    """GET /u/<token>/：配信停止確認画面表示（§9.2 / §6.2.7、Phase 5 / rev15 是正）。

    【プリフェッチ事故防止】 GET では何も状態を変えない。具体的には
    UnsubscribeLink.accessed_at だけを更新（受信者が画面を開いた事実の記録）し、
    Unsubscribe / SuppressedEmail の作成・is_unsubscribed の変更は POST 確定まで行わない
    （§5.2.2 / §9.2、Gmail/Outlook のリンクプリフェッチ事故防止）。

    【rev15 是正】 画面表示するメアドは UnsubscribeLink.target_email（発行時に焼き込み）
    を使う。configent person.primary_contact から推定しない（配信後に primary_contact が
    差し替わると判定がブレるため、§4.5A / §9.5.4）。

    無効トークン時の遷移：トークン無し独立ルート `/u/` にリダイレクト
    （§4.5A.2.1 「無効トークン時の遷移はコード君判断」）。
    """

    http_method_names = ["get"]

    def get(self, request, token):
        from django.shortcuts import redirect, render
        from django.utils import timezone

        from .models import UnsubscribeLink

        try:
            unsub_link = UnsubscribeLink.objects.get(token=token)
        except UnsubscribeLink.DoesNotExist:
            # 無効トークン → 案内画面へ
            return redirect("mailings:unsubscribe_page_tokenless")

        # accessed_at 更新（GET の唯一の副作用、§4.5A.2）
        UnsubscribeLink.objects.filter(pk=unsub_link.pk).update(
            accessed_at=timezone.now()
        )

        return render(
            request,
            "mailings/unsubscribe_page.html",
            {
                "token": token,
                "masked_email": _mask_email(unsub_link.target_email),
            },
        )


@method_decorator(csrf_exempt, name="dispatch")
class UnsubscribeConfirmView(View):
    """POST /u/<token>/confirm/：配信停止確定（§9.2.1 / §9.5 / §4.5A.2、rev15 是正）。

    【rev15 是正】 個人/代表メアド判定の正本を `UnsubscribeLink.target_email`
    （発行時に焼き込み済み）に一本化。configent person.primary_contact から推定する
    fallback ロジックは撤去（target_email は build() で必ず焼き込まれるため不要、§9.5.4）。

    POST のみ。target_email のローカル部を DUPLICATE_GENERIC_EMAIL_LOCALPARTS と照合：
      - 個人 → unsubscribe_person(target_person=link.person, ...)：同一人物ユニット
        全員伝播（§9.5）。停止主語は `person` フィールド（target_email ではない）。
      - 代表 → SuppressedEmail に target_email を直接登録（判定と登録メアドを一致）。
    成功後、unsubscribed_at を更新して done 画面に 302 リダイレクト。

    CSRF 免除：受信メール内のフォームから POST されるため。トークンが推測困難な
    乱数（secrets.token_urlsafe(8)）なので CSRF の二重認証は不要（§4.5A）。
    """

    http_method_names = ["post"]

    def post(self, request, token):
        from django.shortcuts import redirect
        from django.utils import timezone

        from .models import SuppressedEmail, UnsubscribeLink
        from .services.unsubscribe import unsubscribe_person

        try:
            unsub_link = UnsubscribeLink.objects.get(token=token)
        except UnsubscribeLink.DoesNotExist:
            return redirect("mailings:unsubscribe_page_tokenless")

        target_email = unsub_link.target_email or ""

        if not target_email:
            # target_email は build() で必ず焼き込まれる前提。空の場合は仕様外データ
            # （rev15 より前に作成された UnsubscribeLink で default="" が入った等の
            # マイグレ過渡期データ）。受信者には完了と見せて WARN ログを残す。
            import logging

            logging.getLogger(__name__).warning(
                "UnsubscribeConfirmView: target_email is empty for token %s "
                "(legacy data prior to rev15?)",
                token,
            )
        elif _is_generic_email(target_email):
            # 代表メアド：SuppressedEmail に target_email を直接登録（§9.1 / §9.5.4）。
            # Person 単位の Unsubscribe は作らない（停止主語は会社単位のメアド）。
            with transaction.atomic():
                active = SuppressedEmail.objects.filter(
                    email=target_email, cancelled_at__isnull=True
                ).exists()
                if not active:
                    SuppressedEmail.objects.create(
                        email=target_email,
                        source=SuppressedEmail.Source.UNSUBSCRIBE_GENERIC,
                    )
        else:
            # 個人メアド：unsubscribe_person で同一人物ユニット全員伝播（§9.5）。
            # 停止主語は person フィールド（target_email ではない、§4.5A 補足）。
            unsubscribe_person(
                target_person=unsub_link.person,
                source="unsubscribe_link",
                source_email=target_email,
            )

        # UnsubscribeLink.unsubscribed_at 更新（§4.5A.2、§9.5.4 のマージ後経路でも同じ）
        UnsubscribeLink.objects.filter(pk=unsub_link.pk).update(
            unsubscribed_at=timezone.now()
        )

        return redirect("mailings:unsubscribe_done", token=token)


class UnsubscribeDoneView(View):
    """GET /u/<token>/done/：配信停止完了画面（§4.5A.2、Phase 5）。

    UnsubscribeLink は読まない（仕様書 §4.5A.2 末尾「done は UnsubscribeLink を読まない」）。
    トークン値は URL パスから受け取るが状態確認には使わない（ブックマーク再訪・
    リロード耐性のため UnsubscribeLink を引かない設計）。
    """

    http_method_names = ["get"]

    def get(self, request, token):
        from django.shortcuts import render

        return render(request, "mailings/unsubscribe_done.html", {})


class UnsubscribeTokenlessView(View):
    """GET /u/：トークン無し独立ルート、案内表示のみ（§4.5A.2.1 / §5.1 No.41）。

    【メアド手入力 UI は設けない】 第三者が他人のメアドを入力して勝手に配信停止できる
    悪用を構造的に断つ（§4.5A.2.1）。
    用途：
      (a) テスト配信・プレビューの {% unsubscribe_link %} がトークン無しで /u/ に変換
      (b) 本番メールの /u/<token>/ で無効トークン時のフォールバック（UnsubscribePageView
          が redirect する）
    """

    http_method_names = ["get"]

    def get(self, request):
        from django.shortcuts import render

        return render(request, "mailings/unsubscribe_tokenless.html", {})


# ----------------------------------------------------------------------
# 管理者代行・解除 UI（§9.8 / §9.3.1、Person 詳細から呼ぶ）
# ----------------------------------------------------------------------


@method_decorator(
    permission_required(
        "mailings.manage_suppressed_email", raise_exception=True
    ),
    name="dispatch",
)
class PersonUnsubscribeByAdminView(LoginRequiredMixin, View):
    """POST /mailings/persons/<uuid:pk>/unsubscribe-by-admin/（§9.8、Phase 5）。

    Person 詳細画面の「配信停止する」ボタンから呼ばれる。`Person.set_unsubscribed_by_admin`
    が内部で `unsubscribe_person(source='manual', ...)` + ActionLog 記録を実行。
    完了後は Person 詳細画面に PRG で戻る（messages.success メッセージ付き）。

    権限：`mailings.manage_suppressed_email` 持ち（管理者代行枠）。
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        from django.contrib import messages
        from django.shortcuts import get_object_or_404, redirect

        from persons.models import Person

        person = get_object_or_404(Person, pk=pk)
        note = request.POST.get("note", "").strip()
        try:
            person.set_unsubscribed_by_admin(user=request.user, note=note)
        except ValueError as exc:
            messages.error(request, f"配信停止できません: {exc}")
        else:
            messages.success(request, "配信停止を受け付けました（同一人物ユニット全員）。")
        return redirect("persons:person_detail", pk=pk)


@method_decorator(
    permission_required(
        "mailings.manage_suppressed_email", raise_exception=True
    ),
    name="dispatch",
)
class PersonCancelUnsubscribeView(LoginRequiredMixin, View):
    """POST /mailings/persons/<uuid:pk>/cancel-unsubscribe/（§9.3.1、Phase 5）。

    管理者による配信停止解除。`cancel_unsubscribe` サービス関数 + ActionLog 記録。
    完了後 Person 詳細画面に PRG で戻る。
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        from django.contrib import messages
        from django.shortcuts import get_object_or_404, redirect

        from persons.models import Person

        from .services.audit import record_cancel_unsubscribe_action
        from .services.unsubscribe import cancel_unsubscribe

        person = get_object_or_404(Person, pk=pk)
        note = request.POST.get("note", "").strip()
        cancel_unsubscribe(target_person=person, user=request.user, note=note)
        record_cancel_unsubscribe_action(user=request.user, person=person, note=note)
        messages.success(request, "配信停止を解除しました（同一人物ユニット全員）。")
        return redirect("persons:person_detail", pk=pk)


# ----------------------------------------------------------------------
# SuppressedEmail 管理 UI（§5.1 No.17/18/19、§4.8）
# ----------------------------------------------------------------------


class SuppressedEmailListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """GET /mailings/suppressed/：配信拒否メアド一覧（rev19 No.57、Phase 5）。

    認可（Phase 7 ⑤）：mailings.view_suppressedemail（全員＝閲覧専用ロールも可）。
    """

    template_name = "mailings/suppressed_list.html"
    context_object_name = "suppressed_emails"
    paginate_by = 50
    permission_required = "mailings.view_suppressedemail"
    def get_queryset(self):
        from .models import SuppressedEmail

        # active を先に、解除済みは後ろ。created_at 降順
        return SuppressedEmail.objects.all().order_by(
            "cancelled_at", "-created_at"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["back"] = BackNavigator(self.request)
        ctx["active_app"] = "mailings"
        return ctx


class SuppressedEmailDetailView(LoginRequiredMixin, View):
    """GET/POST /mailings/suppressed/<uuid:pk>/：詳細表示＋解除（rev19 No.58、Phase 5）。

    POST で cancelled_at = now() / cancelled_by = request.user に更新（論理削除、§4.8.1）。
    解除後、同一メアド宛配信が解禁される。

    認可（Phase 7 ⑤、rev19 No.58）：GET=mailings.view_suppressedemail（全員）／
        POST=mailings.manage_suppressed_email（admin）。GET と POST で粗い Permission が
        異なるため dispatch 一括ではなくメソッド単位で付与する。
    """

    @method_decorator(
        permission_required("mailings.view_suppressedemail", raise_exception=True)
    )
    def get(self, request, pk):
        from django.shortcuts import get_object_or_404, render

        from .models import SuppressedEmail

        sup = get_object_or_404(SuppressedEmail, pk=pk)
        return render(
            request,
            "mailings/suppressed_detail.html",
            {
                "suppressed": sup,
                "back": BackNavigator(request),
                "active_app": "mailings",
            },
        )

    @method_decorator(
        permission_required("mailings.manage_suppressed_email", raise_exception=True)
    )
    def post(self, request, pk):
        from django.contrib import messages
        from django.shortcuts import get_object_or_404, redirect
        from django.utils import timezone

        from .models import SuppressedEmail

        sup = get_object_or_404(SuppressedEmail, pk=pk)
        if sup.cancelled_at is None:
            sup.cancelled_at = timezone.now()
            sup.cancelled_by = request.user
            sup.save(update_fields=["cancelled_at", "cancelled_by", "updated_at"])
            messages.success(request, "配信拒否を解除しました。")
        else:
            messages.info(request, "既に解除済みです。")
        return redirect("mailings:suppressed_detail", pk=pk)


@method_decorator(
    permission_required(
        "mailings.manage_suppressed_email", raise_exception=True
    ),
    name="dispatch",
)
class SuppressedEmailCreateView(LoginRequiredMixin, View):
    """GET/POST /mailings/suppressed/add/：管理者手動登録（§5.1 No.19、Phase 5）。

    POST 時は source='manual' で SuppressedEmail を作成。既に active な同 email
    レコードがある場合はメッセージ表示のみ（重複作成しない、§4.8.1）。
    """

    def get(self, request):
        from django.shortcuts import render

        return render(
            request,
            "mailings/suppressed_create.html",
            {
                "back": BackNavigator(request),
                "active_app": "mailings",
            },
        )

    def post(self, request):
        from django.contrib import messages
        from django.shortcuts import redirect, render

        from .models import SuppressedEmail

        email = (request.POST.get("email", "") or "").strip()
        note = (request.POST.get("note", "") or "").strip()
        if not email:
            messages.error(request, "メアドを入力してください。")
            return render(
                request,
                "mailings/suppressed_create.html",
                {
                    "back": BackNavigator(request),
                    "active_app": "mailings",
                },
            )

        with transaction.atomic():
            already_active = SuppressedEmail.objects.filter(
                email=email, cancelled_at__isnull=True
            ).exists()
            if already_active:
                messages.info(request, f"{email} は既に登録済みです。")
            else:
                SuppressedEmail.objects.create(
                    email=email,
                    source=SuppressedEmail.Source.MANUAL,
                    note=note,
                )
                messages.success(request, f"{email} を配信拒否に登録しました。")
        return redirect("mailings:suppressed_list")


# ======================================================================
# Phase 6：配信レポート画面 + CSV ダウンロード（仕様書 v1.6 第13章 / §6.2.2 /
# §6.2.3 / §13.2、rev15）
# ======================================================================


class CampaignListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """GET /mailings/campaigns/（rev19 No.46、Phase 6 新規）。

    集計値カードを付したキャンペーン一覧。view_all_campaigns 持ちは全件、それ以外は
    自分のキャンペーンのみ。各行に Campaign.total_count / sent_count（フィールド参照）
    と TrackingLink/DeliveryHistory/UnsubscribeLink を都度集計したサマリを表示。

    N+1 対策：QuerySet レベルで campaign を取得し、テンプレート側で
    get_campaign_summary を呼ぶ際に毎回 3 クエリ発生する素朴実装は避け、View 側で
    一括集計してから渡す。

    認可（Phase 7 ⑤）：粗い Permission は mailings.view_campaign。表示範囲の絞り込みは
        ④ services.permissions.visible_campaigns_for に委譲（view_all_campaigns 持ちは
        全件、でなければ作成者本人のみ。QuerySet レベルで絞り N+1 を作らない）。
    """

    template_name = "mailings/campaign_list.html"
    context_object_name = "campaigns"
    paginate_by = 50
    permission_required = "mailings.view_campaign"
    def get_queryset(self):
        from .models import Campaign
        from .services.permissions import visible_campaigns_for

        qs = Campaign.objects.select_related("template", "mailing_list").order_by(
            "-created_at"
        )
        return visible_campaigns_for(self.request.user, qs)

    def get_context_data(self, **kwargs):
        from mailings.services.report_aggregation import get_campaign_summary

        ctx = super().get_context_data(**kwargs)
        # 表示中のページ分のみ集計（paginator 適用後の object_list）。
        # テンプレートでの dict キーアクセス困難回避のため、campaign + summary の組を渡す。
        ctx["campaigns_with_summary"] = [
            {"campaign": c, "summary": get_campaign_summary(c)}
            for c in ctx["campaigns"]
        ]
        back = BackNavigator(self.request)
        # 一覧自身を戻り先候補としてスタックに積む（詳細→戻る で一覧に戻れるように）。
        # title は "" にして back_link / back_all_link がデフォルト「戻る」「最初に戻る」を表示する（HIG v1.2 §3.2）。
        back.push_current("", ["page"])
        ctx["back"] = back
        ctx["active_app"] = "mailings"
        ctx["active_menu"] = "mailings:campaign_list"
        return ctx


class CampaignReportView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, View
):
    """GET /mailings/campaigns/<uuid:pk>/report/（rev19 No.52、Phase 6 新規）。

    配信レポート詳細：基本情報 + 集計値カード + リンク別表 + 一覧導線 + CSV リンク
    （§6.2.2）。

    認可（Phase 7 ⑤）：mailings.view_campaign ＋ 所有者判定（CampaignOwnerRequiredMixin
        が④ can_view_campaign を呼ぶ。本人 or view_all_campaigns 持ち）。
    """

    permission_required = "mailings.view_campaign"
    def get(self, request, pk):
        from django.shortcuts import get_object_or_404, render

        from mailings.services.report_aggregation import (
            get_campaign_summary,
            get_link_aggregates,
        )

        from .models import Campaign as _Campaign

        campaign = get_object_or_404(
            _Campaign.objects.select_related("template", "mailing_list", "created_by"),
            pk=pk,
        )
        return render(
            request,
            "mailings/campaign_report.html",
            {
                "campaign": campaign,
                "summary": get_campaign_summary(campaign),
                "link_aggregates": get_link_aggregates(campaign),
                "back": BackNavigator(request),
                "active_app": "mailings",
            },
        )


class _CampaignReportFilteredListBaseView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, View
):
    """3 つの絞り込み一覧（クリック / バウンス / 配信停止）の共通基底（§6.2.3）。

    サブクラスが `template_name` と `aggregator` を提供する。aggregator は
    campaign を受けて List[dict(person, last_action_at)] を返す関数。

    認可（Phase 7 ⑤、rev19 No.53-55）：mailings.view_campaign ＋ 所有者判定
        （CampaignOwnerRequiredMixin）。3 サブクラス共通のため基底に集約する。
    """

    template_name: str = ""
    aggregator = None  # 型は Callable[[Campaign], List[dict]]
    page_title: str = ""
    permission_required = "mailings.view_campaign"
    def get(self, request, pk):
        from django.shortcuts import get_object_or_404, render

        from .models import Campaign as _Campaign

        campaign = get_object_or_404(_Campaign.objects.select_related("template"), pk=pk)
        rows = self.__class__.aggregator(campaign)
        return render(
            request,
            self.template_name,
            {
                "campaign": campaign,
                "rows": rows,
                "page_title": self.page_title,
                "back": BackNavigator(request),
                "active_app": "mailings",
            },
        )


class CampaignReportClickedListView(_CampaignReportFilteredListBaseView):
    """GET /mailings/campaigns/<uuid:pk>/report/clicked/（§5.1 No.13、§6.2.3）。"""

    template_name = "mailings/campaign_report_clicked.html"
    page_title = "クリックした受信者"

    @staticmethod
    def aggregator(campaign):
        from mailings.services.report_aggregation import get_clicked_persons

        return get_clicked_persons(campaign)


class CampaignReportBouncedListView(_CampaignReportFilteredListBaseView):
    """GET /mailings/campaigns/<uuid:pk>/report/bounced/（§5.1 No.14、§6.2.3）。"""

    template_name = "mailings/campaign_report_bounced.html"
    page_title = "バウンスした受信者"

    @staticmethod
    def aggregator(campaign):
        from mailings.services.report_aggregation import get_bounced_persons

        return get_bounced_persons(campaign)


class CampaignReportUnsubscribedListView(_CampaignReportFilteredListBaseView):
    """GET /mailings/campaigns/<uuid:pk>/report/unsubscribed/（§5.1 No.15、§6.2.3）。"""

    template_name = "mailings/campaign_report_unsubscribed.html"
    page_title = "配信停止した受信者"

    @staticmethod
    def aggregator(campaign):
        from mailings.services.report_aggregation import get_unsubscribed_persons

        return get_unsubscribed_persons(campaign)


class CampaignReportCSVView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, View
):
    """GET/POST /mailings/campaigns/<uuid:pk>/report/csv/（rev19 No.56 / §13.2、Phase 6）。

    GET：項目選択フォームを表示（チェックボックス）。
    POST：選択された項目で CSV を生成しダウンロードを返す（UTF-8 BOM 付き、§13.2）。

    認可（Phase 7 ⑤）：mailings.export_report ＋ 所有者判定（CampaignOwnerRequiredMixin）。
        GET / POST ともに dispatch で一括判定される。
    """

    http_method_names = ["get", "post"]
    permission_required = "mailings.export_report"
    def get(self, request, pk):
        from django.shortcuts import get_object_or_404, render

        from mailings.services.report_aggregation import CSV_AVAILABLE_FIELDS

        from .models import Campaign as _Campaign

        campaign = get_object_or_404(_Campaign, pk=pk)
        return render(
            request,
            "mailings/campaign_report_csv_form.html",
            {
                "campaign": campaign,
                "fields": CSV_AVAILABLE_FIELDS,
                "back": BackNavigator(request),
                "active_app": "mailings",
            },
        )

    def post(self, request, pk):
        import csv
        import io

        from django.http import HttpResponse
        from django.shortcuts import get_object_or_404

        from mailings.services.report_aggregation import (
            CSV_AVAILABLE_KEYS,
            csv_rows,
        )

        from .models import Campaign as _Campaign

        campaign = get_object_or_404(_Campaign, pk=pk)
        selected = [f for f in request.POST.getlist("fields") if f in CSV_AVAILABLE_KEYS]
        if not selected:
            from django.contrib import messages

            messages.error(request, "1 つ以上の項目を選択してください。")
            return self.get(request, pk)

        # UTF-8 BOM 付き（Excel 文字化け回避、§13.2）。
        # StringIO で CSV を組み立て、最後に BOM 付きでバイト列化する。
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in csv_rows(campaign, selected):
            writer.writerow(row)

        response = HttpResponse(
            "﻿" + buf.getvalue(),
            content_type="text/csv; charset=utf-8",
        )
        filename = f"campaign_report_{campaign.pk}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ======================================================================
# (c) EmailTemplate 編集画面 + プレビュー View
# 仕様書 §4.3 / §6.1 No.44 / §6.2.1.1 / §7.4 / §7.7.1 / §12.2 / §12.3、
# URL一覧表 rev17 No.44 / No.82。
# rev17 で 1:1 運用確定（Campaign 1 つに EmailTemplate 1 つ）、rev18 で
# OneToOneField により DB レベル 1:1 を担保（emailtemplate.campaign 逆引き可能）。
# ======================================================================


def _highlight_unrendered_merge_tags(html_text):
    """[性質] 純関数（DB なし・副作用なし）。

    プレビュー HTML 内の差し込み未展開 {{...}} を警告色用 span で wrap する。
    差し込みが失敗した（=値が空で記法が残った、§7.4.5 Sansan 互換）箇所だけを
    表示層で目立たせる、仕様書 §7.7.1.3 確定の「表示層が記法残存を拾って色付け」。
    prepare の出力自体には手を入れない（§7.7.1.3）。

    [入力] html_text: str（prepare 出力の subject_rendered または body_html）
    [出力] str（{{...}} 残存箇所が <span class="app-preview__missing">{{...}}</span>
            に置換された HTML 安全文字列。呼び出し側は mark_safe を付けてテンプレに渡す）
    """
    import html as _html
    import re

    def _wrap(m):
        return (
            '<span class="app-preview__missing">'
            + _html.escape(m.group(0))
            + "</span>"
        )

    # `{{` `}}` 自体は HTML 特殊文字ではないため、body_html（既に escape 済み）
    # にもそのまま残っている。subject_rendered（escape 未適用、プレーン）にも
    # 同じ正規表現が効く。
    return re.sub(r"\{\{[^}]+\}\}", _wrap, html_text)


class EmailTemplateUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """EmailTemplate 編集画面（仕様書 §4.3 / §6.1 No.44 / §12.2、URL一覧 rev19 No.44）。

    指示書 (c) §3.2 で確定した 2 ボタン方式：
      - 「保存して戻る」（name="action" value="save_return"）→ DB 保存 →
        Campaign 詳細画面（mailings:campaign_detail）へリダイレクト
      - 「プレビュー」（name="action" value="preview"）→ DB 保存 →
        編集画面に `?preview=1` 付きでリダイレクト → テンプレ JS が既存
        `openPreviewModal` 機構（app.js）を起動して No.82 プレビュー View
        の HTML 断片を Ajax 取得・モーダル表示。POST プレビュー API は作らない
        （指示書 §3.2 / §7、prepare に文字列直渡しを禁止）

    Campaign の特定：rev18 OneToOneField の逆引き `self.object.campaign` で取得。

    認可（Phase 7 ⑤）：mailings.manage_template のみ。権限者は誰のテンプレートでも
        編集可（rev20 §14.4.2）。所有者判定は付けない。
    """

    model = EmailTemplate
    form_class = EmailTemplateForm
    template_name = "mailings/email_template_update.html"
    permission_required = "mailings.manage_template"
    def form_valid(self, form):
        # ModelForm の save をまず実行（updated_at 含めて DB 反映）。
        self.object = form.save()
        action = self.request.POST.get("action", "save_return")
        if action == "preview":
            # ?preview=1 リダイレクト → 再描画した編集画面の JS が openPreviewModal を起動
            return redirect(
                reverse("mailings:template_update", kwargs={"pk": self.object.pk})
                + "?preview=1"
            )
        # save_return（既定）：Campaign 詳細へ戻す（rev18 OneToOne 逆引きで Campaign 取得）。
        return redirect("mailings:campaign_detail", pk=self.object.campaign.pk)

    def get_success_url(self):
        # form_valid 内で明示 redirect を返すため通常到達しないが、Django UpdateView の
        # 規約上未定義だと SuspiciousOperation になり得るためフォールバック（campaign_detail）。
        return reverse(
            "mailings:campaign_detail", kwargs={"pk": self.object.campaign.pk}
        )

    def get_context_data(self, **kwargs):
        from .services.recipient_extraction import extract_recipients

        context = super().get_context_data(**kwargs)
        campaign = self.object.campaign  # rev18 OneToOne 逆引き（related_name="campaign"）
        # プレビュー対象 = 抽出済み配信対象集合の代表 Person（仕様書 §7.7.1.2、
        # §7.7 テスト配信と同思想）。先頭 1 件を採用。空集合なら None。
        preview_person = extract_recipients(campaign).first()
        context.update(
            {
                "campaign": campaign,
                "preview_person_id": (
                    str(preview_person.id) if preview_person else None
                ),
                "preview_requested": self.request.GET.get("preview") == "1",
                "back": BackNavigator(self.request),
                "active_app": "mailings",
            }
        )
        return context


class CampaignPreviewView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, View
):
    """キャンペーンプレビュー View（仕様書 §7.7.1、URL一覧 rev19 No.82）。

    Ajax モーダルに差し込む HTML 断片を返す。`EmailContext.prepare(mode=PREVIEW)`
    を直接呼び、3 処理単位（cron 入口・オーケストレーション・1 受信者処理）を
    通らない。送信も DB 書き込みも行わない（TrackingLink/UnsubscribeLink の
    bulk_create は PRODUCTION モードでのみ走る、mode 分岐は email_context.py 側）。

    色付けは表示層責務（§7.7.1.3）：subject_rendered / body_html を
    `_highlight_unrendered_merge_tags` で wrap して `mark_safe` でテンプレに渡す。
    prepare の出力には手を入れない（rev9 §7.4.1.3）。

    呼び出し元（編集画面・ステップ画面）が増えても URL/引数/prepare 呼び出しは
    同一（仕様書 §7.7.1.1 rev17 拡張）。

    認可（Phase 7 ⑤、rev19 No.82）：mailings.view_campaign ＋ 所有者判定
        （CampaignOwnerRequiredMixin が URL の <uuid:pk> から Campaign を取り判定）。
    """

    permission_required = "mailings.view_campaign"
    def get(self, request, pk, person_id):
        from django.utils.safestring import mark_safe

        from .models import Campaign
        from .services.email_context import EmailContext, EmailMode

        campaign = get_object_or_404(Campaign, pk=pk)
        person = get_object_or_404(Person, pk=person_id)
        ctx = EmailContext.prepare(campaign, person, EmailMode.PREVIEW)
        subject_html = mark_safe(_highlight_unrendered_merge_tags(ctx.subject_rendered))
        body_html = mark_safe(_highlight_unrendered_merge_tags(ctx.body_html))
        return render(
            request,
            "mailings/_campaign_preview_modal_body.html",
            {
                "campaign": campaign,
                "person": person,
                "from_name": ctx.from_name,
                "from_email": ctx.from_email,
                "to_email": ctx.to_email,
                "subject_html": subject_html,
                "body_html": body_html,
            },
        )


# ======================================================================
# (b) Campaign UI 全般（仕様書 §4.2 / §6.1 No.7-11 / §6.2.1 / §6.2.1.1 / §7.2.5
# / §7.7 / §7.8 / §12.4、URL一覧表 rev17 No.7-11）。
# 詳細画面をハブにした単一ページ完結フロー。ステップ画面化は v1.6 後半/v1.7+（指示書 §3.3）。
# Phase 7 ⑤ で各 View に PermissionRequiredMixin（粗い Permission）＋所有者判定
# （CampaignOwnerRequiredMixin、rev20 §14.5）を付与済み。
# ======================================================================


class CampaignCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """Campaign 新規作成（URL一覧 rev19 No.47、(b) §5.1）。

    create_campaign_with_template で Campaign + 空 EmailTemplate を atomic 生成。
    成功時は詳細画面へリダイレクト（PRG）。

    認可（Phase 7 ⑤）：mailings.add_campaign（新規作成に所有者判定はなし）。
    """

    model = Campaign
    form_class = CampaignForm
    template_name = "mailings/campaign_form.html"
    permission_required = "mailings.add_campaign"
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # 新規作成画面は name のみ表示する（CampaignForm 側の出し分け）。
        kwargs["is_create"] = True
        return kwargs

    def form_valid(self, form):
        from .services.campaign_actions import create_campaign_with_template

        from django.core.exceptions import ValidationError

        try:
            campaign = create_campaign_with_template(form, self.request.user)
        except ValidationError as e:
            form.add_error(None, e)
            return self.form_invalid(form)
        return redirect("mailings:campaign_detail", pk=campaign.pk)

    def get_context_data(self, **kwargs):
        # クエリパラメータを持たない画面なので push_current は呼ばない。
        # 戻り先は呼び出し元テンプレ側で append_back_url を使うことで back スタックに積む。
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "is_create": True,
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "mailings:campaign_list",
            }
        )
        return context


class CampaignDetailView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, DetailView
):
    """Campaign 詳細画面（ハブ、URL一覧 rev19 No.48、(b) §5.2）。

    1 画面に基本情報・メール内容・宛先サマリー・テスト送信・status 別アクションを集約。
    ステップ画面化（仕様書 §6.2.1）は v1.6 後半/v1.7+（指示書 §3.3）。

    認可（Phase 7 ⑤）：mailings.view_campaign ＋ 所有者判定（CampaignOwnerRequiredMixin
        が④ can_view_campaign を呼ぶ。本人 or view_all_campaigns 持ち）。
    """

    model = Campaign
    template_name = "mailings/campaign_detail.html"
    context_object_name = "campaign"
    permission_required = "mailings.view_campaign"
    def get_queryset(self):
        # アーカイブ済み Campaign は詳細画面に出さない（is_archived=True は 404）
        return Campaign.objects.select_related(
            "template", "mailing_list", "created_by"
        ).filter(is_archived=False)

    def get_context_data(self, **kwargs):
        from mailings.services.recipient_extraction import extract_recipients
        from mailings.services.report_aggregation import get_campaign_summary

        context = super().get_context_data(**kwargs)
        campaign = self.object
        # 宛先リスト未設定の draft は extract_recipients を呼べないので 0 件として扱う。
        if campaign.mailing_list_id is None:
            recipient_count = 0
            sample_recipients = []
        else:
            recipients = extract_recipients(campaign).select_related("primary_contact")
            recipient_count = recipients.count()
            sample_recipients = list(recipients[:10])
        # 詳細画面はクエリパラメータを持たないので push_current は呼ばない。
        # 一覧→詳細→編集 等の経路はテンプレ側 append_back_url で back スタックに積む。
        context.update(
            {
                "template": campaign.template,
                "summary": get_campaign_summary(campaign),
                "recipient_count": recipient_count,
                "sample_recipients": sample_recipients,
                "test_recipient_email": self.request.user.email,
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "mailings:campaign_list",
            }
        )
        return context


class CampaignUpdateView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, UpdateView
):
    """Campaign 編集画面（基本情報のみ、URL一覧 rev19 No.49、(b) §5.3）。

    draft のみ編集可。それ以外は詳細画面へ redirect + warning。
    件名・本文は本画面では編集しない（(c) の EmailTemplateUpdateView で行う、
    指示書 Q3 確定の動線 2 分割）。

    認可（Phase 7 ⑤）：mailings.change_campaign ＋ 所有者判定（CampaignOwnerRequiredMixin）。
    """

    model = Campaign
    form_class = CampaignForm
    template_name = "mailings/campaign_form.html"
    permission_required = "mailings.change_campaign"
    def _redirect_if_not_draft(self):
        # draft 以外は編集不可。詳細画面へ warning 付きでリダイレクト。
        # 認可（Login→Permission→Owner）を先に通すため dispatch ではなく get/post で
        # 評価する（未認証・無権限・非所有者を status 判定より先に弾く）。
        campaign = self.get_object()
        if campaign.status != Campaign.Status.DRAFT:
            from django.contrib import messages

            messages.warning(
                self.request,
                "下書き状態のキャンペーンのみ編集できます。",
            )
            return redirect("mailings:campaign_detail", pk=campaign.pk)
        return None

    def get(self, request, *args, **kwargs):
        guard = self._redirect_if_not_draft()
        return guard if guard is not None else super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        guard = self._redirect_if_not_draft()
        return guard if guard is not None else super().post(request, *args, **kwargs)

    def get_queryset(self):
        return Campaign.objects.filter(is_archived=False)

    def get_success_url(self):
        return reverse("mailings:campaign_detail", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        # 編集画面はクエリパラメータを持たないので push_current は呼ばない。
        # 詳細画面側の「基本情報を編集」リンクで append_back_url を使うことで戻り先を積む。
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "is_create": False,
                "back": BackNavigator(self.request),
                "active_app": "mailings",
                "active_menu": "mailings:campaign_list",
            }
        )
        return context


class _CampaignActionBaseView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, View
):
    """schedule / cancel_schedule / test_send 共通基底：POST のみ・status 検査・PRG。

    エラー時は django.contrib.messages.error でフラッシュして詳細画面へ戻す。

    認可（Phase 7 ⑤）：所有者判定（CampaignOwnerRequiredMixin）は共通。粗い Permission
        はアクションごとに異なるため各サブクラスが permission_required を設定する
        （schedule / cancel_schedule = change_campaign、test_send = send_campaign）。
    """

    def get(self, request, pk):
        from django.http import HttpResponseNotAllowed

        return HttpResponseNotAllowed(["POST"])

    def _redirect_detail(self, pk):
        return redirect("mailings:campaign_detail", pk=pk)


class CampaignScheduleView(_CampaignActionBaseView):
    """予約アクション：DRAFT → SCHEDULED（URL一覧 rev19 No.50、(b) §5.4）。

    認可（Phase 7 ⑤）：mailings.change_campaign ＋ 所有者判定（基底 Mixin）。
    """

    permission_required = "mailings.change_campaign"

    def post(self, request, pk):
        from django.contrib import messages
        from django.core.exceptions import ValidationError

        from .services.campaign_actions import schedule_campaign

        campaign = get_object_or_404(Campaign, pk=pk, is_archived=False)
        try:
            schedule_campaign(campaign)
        except ValidationError as e:
            for field, errs in (e.message_dict.items() if hasattr(e, "message_dict") else [(None, e.messages)]):
                for err in errs:
                    messages.error(request, f"{field}: {err}" if field else err)
            return self._redirect_detail(pk)
        messages.success(request, "配信を予約しました。")
        return self._redirect_detail(pk)


class CampaignCancelScheduleView(_CampaignActionBaseView):
    """予約取消アクション：SCHEDULED → DRAFT 復帰、scheduled_at 維持（(b) §5.5、§7.2.5）。

    認可（Phase 7 ⑤）：mailings.change_campaign ＋ 所有者判定（基底 Mixin）。状態遷移
        であり送信ではないため change_campaign（send_campaign ではない、rev20 CRUD 区分）。
    """

    permission_required = "mailings.change_campaign"

    def post(self, request, pk):
        from django.contrib import messages
        from django.core.exceptions import ValidationError

        from .services.campaign_actions import cancel_scheduled_campaign

        campaign = get_object_or_404(Campaign, pk=pk, is_archived=False)
        try:
            cancel_scheduled_campaign(campaign)
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return self._redirect_detail(pk)
        messages.success(request, "予約を取り消して下書きに戻しました。")
        return self._redirect_detail(pk)


class CampaignArchiveView(
    LoginRequiredMixin, PermissionRequiredMixin, CampaignOwnerRequiredMixin, View
):
    """論理削除（(b) §5.6、仕様書 §12.4）。

    GET = 確認画面、POST = 実行（draft のみ可、Campaign + EmailTemplate を同一 atomic で
    is_archived=True）。MailingListDeleteView 流儀踏襲（指示書 Q4 確定）。

    認可（Phase 7 ⑤）：mailings.delete_campaign ＋ 所有者判定（CampaignOwnerRequiredMixin）。
        論理削除＝rev20 の delete 区分。
    """

    permission_required = "mailings.delete_campaign"
    def get(self, request, pk):
        campaign = get_object_or_404(Campaign, pk=pk, is_archived=False)
        return render(
            request,
            "mailings/campaign_confirm_delete.html",
            {
                "campaign": campaign,
                "back": BackNavigator(request),
                "active_app": "mailings",
            },
        )

    def post(self, request, pk):
        from django.contrib import messages
        from django.core.exceptions import ValidationError

        from .services.campaign_actions import archive_campaign

        campaign = get_object_or_404(Campaign, pk=pk, is_archived=False)
        try:
            archive_campaign(campaign)
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect("mailings:campaign_detail", pk=pk)
        messages.success(request, "キャンペーンを削除しました。")
        return redirect("mailings:campaign_list")


class CampaignTestSendView(_CampaignActionBaseView):
    """テスト送信（URL一覧 rev17 No.11、(b) §5.7、仕様書 §7.7）。

    送信先は request.user.email 固定（指示書 §3.2、第三者悪用経路の構造的防止）。
    POST のみ、PRG で詳細画面へ戻して結果メッセージをフラッシュ表示。

    認可（Phase 7 ⑤）：mailings.send_campaign ＋ 所有者判定（基底 Mixin）。
    """

    permission_required = "mailings.send_campaign"

    def post(self, request, pk):
        from django.contrib import messages

        from .services.campaign_actions import run_test_send

        campaign = get_object_or_404(Campaign, pk=pk, is_archived=False)
        try:
            run_test_send(campaign, request.user.email)
        except ValueError as e:
            messages.error(request, str(e))
            return self._redirect_detail(pk)
        except Exception as e:
            messages.error(request, f"テスト送信に失敗しました: {type(e).__name__}: {e}")
            return self._redirect_detail(pk)
        messages.success(
            request, f"{request.user.email} 宛にテスト送信しました。"
        )
        return self._redirect_detail(pk)
