from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from urllib.parse import parse_qs, urlparse

from actionlogs.models import ActionLog
from activities.forms import (
    ActivityForm,
    ActivityPersonForm,
    ActivityUserForm,
)
from activities.models import Activity, ActivityPerson, ActivityType, ActivityUser, Direction
from activities.permissions import (
    can_archive_activity,
    can_edit_activity,
    can_view_activity,
    visible_activities_for,
)
from activities.services import archive_activity, get_unfollowed_campaign_persons
from back_navigator.back_navigator import BackNavigator
from companies.models import Company
from deals.models import PersonRole, UserRole
from mailings.models import Campaign
from persons.models import Person


def _is_wizard_request(request):
    """リクエストがウィザード進行中かどうか判定する。"""
    if request.POST.get("wizard") == "1" or request.GET.get("wizard") == "1":
        return True
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    return "wizard=1" in next_url


# ----------------------------------------------------------------------
# 活動一覧の多段ソート（HIG 第6章、パーソン一覧準拠）
# ----------------------------------------------------------------------
ACTIVITY_LIST_SORT_FIELD_MAP = {
    "occurred_at": "occurred_at",
    "activity_type": "activity_type",
    "direction": "direction",
    "created_at": "created_at",
    "updated_at": "updated_at",
}
ACTIVITY_LIST_SORT_CHOICES = [
    ("occurred_at", "実施日時"),
    ("activity_type", "活動種別"),
    ("direction", "方向"),
    ("created_at", "登録日時"),
    ("updated_at", "更新日時"),
]
ACTIVITY_LIST_SORT_MAX_KEYS = 2
PER_PAGE_CHOICES = [20, 50, 100]


def _parse_activity_sort(params):
    raw = (params.get("sort") or "").strip()
    if raw == "date_desc":
        return [("occurred_at", "desc")]
    if raw == "date_asc":
        return [("occurred_at", "asc")]

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
        if key not in ACTIVITY_LIST_SORT_FIELD_MAP or key in seen:
            continue
        seen.add(key)
        tokens.append((key, direction))
        if len(tokens) >= ACTIVITY_LIST_SORT_MAX_KEYS:
            break
    return tokens


def _apply_activity_list_sort(qs, params):
    tokens = _parse_activity_sort(params)
    if not tokens:
        return qs.order_by("-occurred_at", "-id")
    order = []
    for key, direction in tokens:
        prefix = "-" if direction == "desc" else ""
        order.append(prefix + ACTIVITY_LIST_SORT_FIELD_MAP[key])
    order.append("-id")
    return qs.order_by(*order)


def _activity_sort_context(params):
    tokens = _parse_activity_sort(params)
    rows = [{"key": k, "dir": d} for (k, d) in tokens]
    while len(rows) < ACTIVITY_LIST_SORT_MAX_KEYS:
        rows.append({"key": "", "dir": "asc"})

    raw = (params.get("sort") or "").strip()
    return {
        "sort_rows": rows,
        "sort_is_active": bool(tokens and raw),
        "sort_value": ",".join(
            ("" if d == "asc" else "-") + k for (k, d) in tokens
        ) if tokens else "",
        "per_page_choices": PER_PAGE_CHOICES,
        "sort_choices": ACTIVITY_LIST_SORT_CHOICES,
    }


class ActivityListView(LoginRequiredMixin, ListView):
    """活動一覧画面（仕様書 v1.5 第3章, §7.3）。"""

    model = Activity
    template_name = "activities/activity_list.html"
    context_object_name = "activities"
    paginate_by = 20

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get("per_page")
        if per_page in ("20", "50", "100"):
            return int(per_page)
        return self.paginate_by

    def _get_selected_user_ids(self):
        """実施者（user_ids）の選択リストを取得する。"""
        has_filter = ("user_ids" in self.request.GET) or ("user_id" in self.request.GET)
        if not has_filter:
            if not self.request.GET or not self.request.GET.get("mode"):
                return [str(self.request.user.id)]
            return []

        user_ids_list = self.request.GET.getlist("user_ids")
        selected = []
        for u in user_ids_list:
            for part in u.split(","):
                p = part.strip()
                if p and p not in selected:
                    selected.append(p)
        if not selected and "user_id" in self.request.GET:
            u = self.request.GET.get("user_id", "").strip()
            if u:
                selected.append(u)
        return selected

    def _get_selected_attendee_ids(self):
        """同席者（attendee_user_ids）の選択リストを取得する。"""
        attendee_list = self.request.GET.getlist("attendee_user_ids")
        selected = []
        for a in attendee_list:
            for part in a.split(","):
                p = part.strip()
                if p and p not in selected:
                    selected.append(p)
        return selected

    def get_queryset(self):
        qs = visible_activities_for(self.request.user)

        status = self.request.GET.get("status", "active")
        if status == "active":
            qs = qs.filter(is_archived=False)
        elif status == "archived":
            qs = qs.filter(is_archived=True)

        mode = self.request.GET.get("mode")
        date_str = self.request.GET.get("date") or self.request.GET.get("occurred_at")
        occurred_after = self.request.GET.get("occurred_after")
        occurred_before = self.request.GET.get("occurred_before")

        if occurred_after or occurred_before:
            if occurred_after:
                try:
                    qs = qs.filter(occurred_at__date__gte=occurred_after)
                except Exception:
                    pass
            if occurred_before:
                try:
                    qs = qs.filter(occurred_at__date__lte=occurred_before)
                except Exception:
                    pass
        elif date_str:
            try:
                qs = qs.filter(occurred_at__date=date_str)
            except Exception:
                pass
        elif mode == "daily" or (not self.request.GET and not mode):
            # デフォルトで日報（本日分）を表示
            today = timezone.localdate()
            qs = qs.filter(occurred_at__date=today)

        # 実施者（複数対応）
        selected_user_ids = self._get_selected_user_ids()
        if selected_user_ids:
            try:
                qs = qs.filter(user_id__in=selected_user_ids)
            except Exception:
                pass

        # 同席者（複数対応）
        selected_attendee_ids = self._get_selected_attendee_ids()
        if selected_attendee_ids:
            try:
                qs = qs.filter(activity_users__user_id__in=selected_attendee_ids).distinct()
            except Exception:
                pass

        activity_type = self.request.GET.get("activity_type")
        if activity_type:
            qs = qs.filter(activity_type=activity_type)

        direction = self.request.GET.get("direction")
        if direction:
            qs = qs.filter(direction=direction)

        q = self.request.GET.get("q")
        if q:
            q = q.strip()
            qs = qs.filter(
                Q(memo__icontains=q)
                | Q(place__icontains=q)
                | Q(deal__name__icontains=q)
                | Q(deal__company__organization__icontains=q)
                | Q(activity_persons__person__primary_contact__last_name__icontains=q)
                | Q(activity_persons__person__primary_contact__first_name__icontains=q)
                | Q(activity_persons__person__primary_contact__company__organization__icontains=q)
                | Q(activity_persons__person__primary_contact__organization__icontains=q)
                | Q(user__username__icontains=q)
                | Q(user__last_name__icontains=q)
                | Q(user__first_name__icontains=q)
                | Q(activity_users__user__username__icontains=q)
                | Q(activity_users__user__last_name__icontains=q)
                | Q(activity_users__user__first_name__icontains=q)
            ).distinct()

        qs = _apply_activity_list_sort(qs, self.request.GET)

        return qs.select_related("deal", "campaign", "user").prefetch_related(
            "activity_persons__person__primary_contact"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(
            title="活動一覧",
            keys=[
                "mode",
                "date",
                "occurred_at",
                "occurred_after",
                "occurred_before",
                "activity_type",
                "direction",
                "q",
                "status",
                "user_id",
                "user_ids",
                "attendee_user_ids",
                "sort",
                "per_page",
                "page",
            ],
        )
        context["back"] = back
        context["activity_types"] = ActivityType.choices
        context["directions"] = Direction.choices
        context["current_activity_type"] = self.request.GET.get("activity_type", "")
        context["current_direction"] = self.request.GET.get("direction", "")
        context["current_status"] = self.request.GET.get("status", "active")
        context["current_date"] = self.request.GET.get("date", "")
        context["occurred_after"] = self.request.GET.get("occurred_after", "")
        context["occurred_before"] = self.request.GET.get("occurred_before", "")
        context["current_mode"] = self.request.GET.get("mode", "daily" if not self.request.GET else "")
        context["current_q"] = self.request.GET.get("q", "")

        # ユーザー一覧および複数選択状態
        selected_user_ids = self._get_selected_user_ids()
        selected_attendee_ids = self._get_selected_attendee_ids()
        all_users = list(get_user_model().objects.filter(is_active=True).order_by("username"))

        context["selected_user_ids"] = selected_user_ids
        context["selected_user_ids_str"] = ",".join(selected_user_ids)
        context["selected_users"] = [u for u in all_users if str(u.id) in selected_user_ids]
        context["current_user_id"] = selected_user_ids[0] if selected_user_ids else ""

        context["selected_attendee_ids"] = selected_attendee_ids
        context["selected_attendee_ids_str"] = ",".join(selected_attendee_ids)
        context["selected_attendee_users"] = [u for u in all_users if str(u.id) in selected_attendee_ids]

        context["all_users"] = all_users
        context["users"] = all_users

        # ソートおよび表示件数（パーソン一覧準拠）
        sort_ctx = _activity_sort_context(self.request.GET)
        context.update(sort_ctx)
        context["current_sort"] = self.request.GET.get("sort", "date_desc")

        per_page = self.request.GET.get("per_page", "20")
        if per_page not in ("20", "50", "100"):
            per_page = "20"
        context["current_per_page"] = per_page
        context["per_page"] = int(per_page)

        # ページネーション用クエリパラメータ
        params = self.request.GET.copy()
        if "page" in params:
            params.pop("page")
        context["query_params"] = params.urlencode()

        context["active_menu"] = "activities:activity_list"
        return context


class ActivityDetailView(LoginRequiredMixin, DetailView):
    """活動詳細画面（仕様書 v1.5 第3章, §7.3）。"""

    model = Activity
    template_name = "activities/activity_detail.html"
    context_object_name = "activity"

    def get_queryset(self):
        return Activity.objects.select_related(
            "deal", "campaign", "user", "created_by"
        )

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        if not can_view_activity(self.request.user, obj):
            raise PermissionDenied
        return obj

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.GET.get("wizard_completed") == "1":
            messages.success(request, "活動を記録しました。")
        back = BackNavigator(request)
        back.push_current(title=f"活動: {self.object}", keys=["page"])
        context = self.get_context_data(object=self.object, back=back)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        activity = self.object
        context["activity_persons"] = (
            activity.activity_persons.filter(person__status=Person.Status.ACTIVE)
            .select_related("person__primary_contact__company")
            .all()
        )
        context["activity_users"] = activity.activity_users.select_related("user").all()
        context["attachments"] = activity.attachments.select_related("uploaded_by").all()
        context["can_edit"] = can_edit_activity(self.request.user, activity)
        context["can_archive"] = can_archive_activity(self.request.user, activity)
        context["activity_person_form"] = ActivityPersonForm()
        context["activity_user_form"] = ActivityUserForm()
        from attachments.forms import AttachmentUploadForm

        context["attachment_form"] = AttachmentUploadForm()
        if "back" not in context:
            back = BackNavigator(self.request)
            back.push_current(title=f"活動: {activity}", keys=["page"])
            context["back"] = back
        context["active_menu"] = "activities:activity_list"
        return context


class ActivityCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """活動新規作成画面（仕様書 v1.5 第3章）。"""

    model = Activity
    form_class = ActivityForm
    template_name = "activities/activity_form.html"
    permission_required = "activities.add_activity"

    def _get_target_company(self):
        company_id = (
            self.request.POST.get("company_id")
            or self.request.GET.get("company_id")
            or self.request.GET.get("company")
        )
        if company_id:
            try:
                return Company.objects.filter(pk=company_id).first()
            except Exception:
                pass
        person_id = (
            self.request.POST.get("person_id")
            or self.request.GET.get("person_id")
            or self.request.GET.get("person")
        )
        if person_id:
            try:
                person = Person.objects.select_related("primary_contact__company").filter(pk=person_id).first()
                if person and person.primary_contact and person.primary_contact.company:
                    return person.primary_contact.company
            except Exception:
                pass
        return None

    def get_initial(self):
        initial = super().get_initial()
        deal_id = self.request.GET.get("deal_id") or self.request.GET.get("deal")
        if deal_id:
            initial["deal"] = deal_id
        campaign_id = self.request.GET.get("campaign") or self.request.GET.get("campaign_id")
        if campaign_id:
            initial["campaign"] = campaign_id
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        company = self._get_target_company()
        if company:
            kwargs["company"] = company
        return kwargs

    def get_success_url(self):
        url = reverse("activities:activity_persons_manage", kwargs={"pk": self.object.pk}) + "?wizard=1"
        raw_back = self.request.GET.get(BackNavigator.PARAM_NAME) or self.request.POST.get(BackNavigator.PARAM_NAME)
        if raw_back:
            from urllib.parse import quote
            url += f"&{BackNavigator.PARAM_NAME}={quote(raw_back)}"
        return url

    @transaction.atomic
    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.created_by = self.request.user
        activity = form.save()
        self.object = activity

        # 案件に紐づく場合の初期自動引き継ぎ
        if activity.deal:
            deal = activity.deal
            # 相手方パーソン
            if deal.primary_person and deal.primary_person.status == Person.Status.ACTIVE:
                ActivityPerson.objects.get_or_create(
                    activity=activity,
                    person=deal.primary_person,
                    defaults={"role": PersonRole.CONTACT_WINDOW},
                )
            for dp in deal.deal_persons.filter(person__status=Person.Status.ACTIVE):
                ActivityPerson.objects.get_or_create(
                    activity=activity,
                    person=dp.person,
                    defaults={"role": dp.role, "memo": dp.memo},
                )

            # 社内同席者
            if deal.owner:
                ActivityUser.objects.get_or_create(
                    activity=activity,
                    user=deal.owner,
                    defaults={"role": UserRole.PRIMARY},
                )
            for du in deal.deal_users.all():
                ActivityUser.objects.get_or_create(
                    activity=activity,
                    user=du.user,
                    defaults={"role": du.role, "memo": du.memo},
                )

        person_id = (
            self.request.POST.get("person_id")
            or self.request.GET.get("person_id")
            or self.request.GET.get("person")
        )
        if person_id:
            try:
                person = Person.objects.get(pk=person_id)
                ActivityPerson.objects.get_or_create(
                    activity=activity,
                    person=person,
                    defaults={"role": PersonRole.ATTENDEE},
                )
            except (Person.DoesNotExist, ValueError):
                pass

        ActionLog.record(
            user=self.request.user,
            action="activity_created",
            content_object=activity,
            data={
                "activity_id": str(activity.id),
                "activity_type": activity.activity_type,
            },
        )
        # ウィザード進行中は都度メッセージを抑制（最終ステップ完了時に詳細画面で発行）
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = True
        person_id = (
            self.request.POST.get("person_id")
            or self.request.GET.get("person_id")
            or self.request.GET.get("person", "")
        )
        context["person_id"] = person_id
        if person_id:
            try:
                context["selected_person"] = Person.objects.select_related("primary_contact__company").filter(pk=person_id).first()
            except Exception:
                pass

        company = self._get_target_company()
        if company:
            context["selected_company"] = company
            context["company_id"] = str(company.id)

        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "activities:activity_list"
        return context


class ActivityUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """活動編集画面（仕様書 v1.5 第3章, §7.3）。"""

    model = Activity
    form_class = ActivityForm
    template_name = "activities/activity_form.html"
    permission_required = "activities.change_activity"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        if not can_edit_activity(self.request.user, obj):
            raise PermissionDenied
        return obj

    def form_valid(self, form):
        activity = form.save()
        ActionLog.record(
            user=self.request.user,
            action="activity_updated",
            content_object=activity,
            data={
                "activity_id": str(activity.id),
                "activity_type": activity.activity_type,
            },
        )
        is_wizard = (self.request.GET.get("wizard") == "1" or self.request.POST.get("wizard") == "1")
        if not is_wizard:
            messages.success(self.request, "活動記録を更新しました。")
        if is_wizard:
            url = reverse("activities:activity_persons_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
            raw_back = self.request.POST.get(BackNavigator.PARAM_NAME) or self.request.GET.get(BackNavigator.PARAM_NAME)
            if raw_back:
                from urllib.parse import quote
                url += f"&{BackNavigator.PARAM_NAME}={quote(raw_back)}"
            return redirect(url)
        return redirect("activities:activity_detail", pk=activity.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = False
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "activities:activity_list"
        return context


class ActivityArchiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """活動アーカイブ画面（仕様書 v1.5 第3章, §7.3）。"""

    permission_required = "activities.change_activity"

    def dispatch(self, request, *args, **kwargs):
        self.activity = get_object_or_404(Activity, pk=kwargs["pk"])
        if not can_archive_activity(request.user, self.activity):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "activities/activity_confirm_archive.html",
            {
                "activity": self.activity,
                "back": BackNavigator(request),
                "active_menu": "activities:activity_list",
            },
        )

    def post(self, request, *args, **kwargs):
        archive_activity(self.activity, user=request.user)
        messages.success(request, "活動をアーカイブしました。")
        return redirect("activities:activity_list")


class CampaignUnfollowedListView(LoginRequiredMixin, ListView):
    """メールキャンペーン未フォローパーソン一覧画面（仕様書 v1.5 §3.5.2）。"""

    template_name = "activities/campaign_unfollowed_list.html"
    context_object_name = "persons"
    paginate_by = 50

    def get_queryset(self):
        campaign_id = self.kwargs.get("campaign_id")
        self.campaign = get_object_or_404(Campaign, pk=campaign_id)
        return get_unfollowed_campaign_persons(self.campaign).order_by("id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(title="未フォローパーソン一覧", keys=["page"])
        context["campaign"] = self.campaign
        context["back"] = back
        context["active_menu"] = "activities:activity_list"
        return context


class ActivityAddPersonView(LoginRequiredMixin, View):
    """活動関係者（社外パーソン）追加。"""

    def post(self, request, pk):
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied
        form = ActivityPersonForm(request.POST)
        if form.is_valid():
            rel = form.save(commit=False)
            rel.activity = activity
            try:
                rel.full_clean()
                rel.save()
                if not _is_wizard_request(request):
                    messages.success(request, f"関係者「{rel.person}」を追加しました。")
            except Exception as e:
                messages.error(request, f"関係者の追加に失敗しました: {e}")
        else:
            messages.error(request, "入力内容をご確認ください。")
        next_url = request.POST.get("next") or reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        return redirect(next_url)


class ActivityDeletePersonView(LoginRequiredMixin, View):
    """活動関係者（社外パーソン）削除。"""

    def post(self, request, pk, person_rel_id):
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied
        rel = get_object_or_404(ActivityPerson, pk=person_rel_id, activity=activity)
        rel.delete()
        if not _is_wizard_request(request):
            messages.success(request, "関係者を解除しました。")
        next_url = request.POST.get("next") or reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        return redirect(next_url)


class ActivityAddUserView(LoginRequiredMixin, View):
    """活動同席者（社内ユーザー）追加。"""

    def post(self, request, pk):
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied
        form = ActivityUserForm(request.POST)
        if form.is_valid():
            rel = form.save(commit=False)
            rel.activity = activity
            if activity.user_id and rel.user_id == activity.user_id:
                messages.error(request, "実施者本人を同席者に追加することはできません。")
            else:
                try:
                    rel.full_clean()
                    rel.save()
                    if not _is_wizard_request(request):
                        messages.success(request, f"同席者「{rel.user}」を追加しました。")
                except Exception as e:
                    messages.error(request, f"同席者の追加に失敗しました: {e}")
        else:
            messages.error(request, "入力内容をご確認ください。")
        next_url = request.POST.get("next") or reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        return redirect(next_url)


class ActivityDeleteUserView(LoginRequiredMixin, View):
    """活動同席者（社内ユーザー）削除。"""

    def post(self, request, pk, user_rel_id):
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied
        rel = get_object_or_404(ActivityUser, pk=user_rel_id, activity=activity)
        rel.delete()
        if not _is_wizard_request(request):
            messages.success(request, "同席者を解除しました。")
        next_url = request.POST.get("next") or reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        return redirect(next_url)


class ActivityPersonManageView(LoginRequiredMixin, View):
    """活動社外関係者管理画面。"""

    def get(self, request, pk):
        activity = get_object_or_404(
            Activity.objects.select_related("deal__company", "user"),
            pk=pk,
        )
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        activity_persons = activity.activity_persons.select_related(
            "person__primary_contact__company",
        ).all()

        existing_person_ids = set(activity_persons.values_list("person_id", flat=True))

        q = request.GET.get("q", "").strip()
        is_searched = bool(q)
        if is_searched:
            person_qs = (
                Person.objects.exclude(status=Person.Status.MERGED)
                .exclude(id__in=existing_person_ids)
                .select_related("primary_contact__company")
                .filter(
                    Q(primary_contact__first_name__icontains=q)
                    | Q(primary_contact__last_name__icontains=q)
                    | Q(primary_contact__company__organization__icontains=q)
                    | Q(primary_contact__organization__icontains=q)
                    | Q(primary_contact__title__icontains=q)
                    | Q(primary_contact__email__icontains=q)
                )
                .distinct()[:50]
            )
        else:
            person_qs = Person.objects.none()

        back = BackNavigator(request)
        activity_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        is_wizard = request.GET.get("wizard") == "1"
        if not is_wizard:
            back.push_current(title=f"関係者管理: {activity}", keys=["q"])
        else:
            back.push_current(title=f"相手方関係者: {activity}", keys=["wizard", "q"])

        is_edit_mode = request.GET.get("edit") == "1"

        return render(
            request,
            "activities/activity_persons_manage.html",
            {
                "activity": activity,
                "activity_persons": activity_persons,
                "candidate_persons": person_qs,
                "current_q": q,
                "q": q,
                "is_searched": is_searched,
                "is_edit_mode": is_edit_mode,
                "is_wizard": is_wizard,
                "person_roles": PersonRole.choices,
                "back": back,
                "activity_detail_url": activity_detail_url,
                "active_menu": "activities:activity_list",
            },
        )

    def post(self, request, pk):
        """活動社外関係者の一括更新。"""
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        activity_persons = activity.activity_persons.all()
        updated_count = 0
        with transaction.atomic():
            for ap in activity_persons:
                exists_key = f"person_{ap.id}_exists"
                if exists_key not in request.POST:
                    continue
                role_key = f"person_{ap.id}_role"
                memo_key = f"person_{ap.id}_memo"

                if role_key in request.POST:
                    new_role = request.POST.get(role_key)
                    if new_role in dict(PersonRole.choices):
                        ap.role = new_role

                if memo_key in request.POST:
                    ap.memo = request.POST.get(memo_key, "").strip()

                ap.full_clean()
                ap.save()
                updated_count += 1

        if not _is_wizard_request(request):
            messages.success(request, f"社外関係者情報を一括更新しました（{updated_count}件）。")
        redirect_url = reverse("activities:activity_persons_manage", kwargs={"pk": activity.pk})
        params = []
        if request.POST.get("wizard") == "1" or request.GET.get("wizard") == "1":
            params.append("wizard=1")
        q = request.POST.get("q", "").strip()
        if q:
            params.append(f"q={q}")
        raw_back = request.POST.get(BackNavigator.PARAM_NAME) or request.GET.get(BackNavigator.PARAM_NAME)
        if raw_back:
            from urllib.parse import quote
            params.append(f"{BackNavigator.PARAM_NAME}={quote(raw_back)}")
        if params:
            redirect_url += "?" + "&".join(params)
        return redirect(redirect_url)


class ActivityUserManageView(LoginRequiredMixin, View):
    """活動社内同席者管理画面。"""

    def get(self, request, pk):
        activity = get_object_or_404(
            Activity.objects.select_related("user"),
            pk=pk,
        )
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        activity_users = activity.activity_users.select_related(
            "user", "user__person__primary_contact"
        ).all()
        existing_user_ids = set(activity_users.values_list("user_id", flat=True))
        if activity.user_id:
            existing_user_ids.add(activity.user_id)

        User = get_user_model()

        q = request.GET.get("q", "").strip()
        is_searched = bool(q)
        if is_searched:
            user_qs = (
                User.objects.filter(is_active=True)
                .exclude(id__in=existing_user_ids)
                .select_related("person__primary_contact")
                .order_by("username")
                .filter(
                    Q(username__icontains=q)
                    | Q(first_name__icontains=q)
                    | Q(last_name__icontains=q)
                    | Q(email__icontains=q)
                    | Q(person__primary_contact__full_name__icontains=q)
                    | Q(person__primary_contact__last_name__icontains=q)
                    | Q(person__primary_contact__first_name__icontains=q)
                    | Q(person__contact__full_name__icontains=q)
                    | Q(person__contact__last_name__icontains=q)
                    | Q(person__contact__first_name__icontains=q)
                )
                .distinct()[:50]
            )
        else:
            user_qs = User.objects.none()

        back = BackNavigator(request)
        activity_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        is_wizard = request.GET.get("wizard") == "1"
        if not is_wizard:
            back.push_current(title=f"同席者管理: {activity}", keys=["q"])
        else:
            back.push_current(title=f"社内同席者: {activity}", keys=["wizard", "q"])

        is_edit_mode = request.GET.get("edit") == "1"

        return render(
            request,
            "activities/activity_users_manage.html",
            {
                "activity": activity,
                "activity_users": activity_users,
                "candidate_users": user_qs,
                "current_q": q,
                "q": q,
                "is_searched": is_searched,
                "is_edit_mode": is_edit_mode,
                "is_wizard": is_wizard,
                "user_roles": UserRole.choices,
                "back": back,
                "activity_detail_url": activity_detail_url,
                "active_menu": "activities:activity_list",
            },
        )

    def post(self, request, pk):
        """活動社内同席者の一括更新。"""
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        activity_users = activity.activity_users.all()
        updated_count = 0
        with transaction.atomic():
            for au in activity_users:
                exists_key = f"user_{au.id}_exists"
                if exists_key not in request.POST:
                    continue
                role_key = f"user_{au.id}_role"
                memo_key = f"user_{au.id}_memo"

                if role_key in request.POST:
                    new_role = request.POST.get(role_key)
                    if new_role in dict(UserRole.choices):
                        au.role = new_role

                if memo_key in request.POST:
                    au.memo = request.POST.get(memo_key, "").strip()

                au.full_clean()
                au.save()
                updated_count += 1

        if not _is_wizard_request(request):
            messages.success(request, f"社内同席者情報を一括更新しました（{updated_count}件）。")
        redirect_url = reverse("activities:activity_users_manage", kwargs={"pk": activity.pk})
        params = []
        if request.POST.get("wizard") == "1" or request.GET.get("wizard") == "1":
            params.append("wizard=1")
        q = request.POST.get("q", "").strip()
        if q:
            params.append(f"q={q}")
        raw_back = request.POST.get(BackNavigator.PARAM_NAME) or request.GET.get(BackNavigator.PARAM_NAME)
        if raw_back:
            from urllib.parse import quote
            params.append(f"{BackNavigator.PARAM_NAME}={quote(raw_back)}")
        if params:
            redirect_url += "?" + "&".join(params)
        return redirect(redirect_url)


class ActivityMembersView(LoginRequiredMixin, View):
    """活動参加者設定画面（ウィザード対応）。"""

    def get(self, request, pk):
        activity = get_object_or_404(
            Activity.objects.select_related("deal__company", "user"),
            pk=pk,
        )
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        activity_persons = activity.activity_persons.select_related(
            "person__primary_contact__company",
        ).all()

        existing_person_ids = set(activity_persons.values_list("person_id", flat=True))

        q = request.GET.get("q", "").strip()
        is_searched = bool(q)
        if is_searched:
            person_qs = (
                Person.objects.exclude(status=Person.Status.MERGED)
                .exclude(id__in=existing_person_ids)
                .select_related("primary_contact__company")
                .filter(
                    Q(primary_contact__first_name__icontains=q)
                    | Q(primary_contact__last_name__icontains=q)
                    | Q(primary_contact__company__organization__icontains=q)
                    | Q(primary_contact__organization__icontains=q)
                    | Q(primary_contact__title__icontains=q)
                    | Q(primary_contact__email__icontains=q)
                )
                .distinct()[:50]
            )
        else:
            person_qs = Person.objects.none()

        back = BackNavigator(request)
        activity_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        is_wizard = request.GET.get("wizard") == "1"
        if not is_wizard:
            back.push_current(title=f"参加者管理: {activity}", keys=["q"])

        is_edit_mode = request.GET.get("edit") == "1"

        return render(
            request,
            "activities/activity_members.html",
            {
                "activity": activity,
                "activity_persons": activity_persons,
                "candidate_persons": person_qs,
                "current_q": q,
                "q": q,
                "is_searched": is_searched,
                "is_edit_mode": is_edit_mode,
                "is_wizard": is_wizard,
                "person_roles": PersonRole.choices,
                "back": back,
                "activity_detail_url": activity_detail_url,
                "active_menu": "activities:activity_list",
            },
        )

    def post(self, request, pk):
        """活動社外参加者の一括更新。"""
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        activity_persons = activity.activity_persons.all()
        updated_count = 0
        with transaction.atomic():
            for ap in activity_persons:
                exists_key = f"person_{ap.id}_exists"
                if exists_key not in request.POST:
                    continue
                role_key = f"person_{ap.id}_role"
                memo_key = f"person_{ap.id}_memo"

                if role_key in request.POST:
                    new_role = request.POST.get(role_key)
                    if new_role in dict(PersonRole.choices):
                        ap.role = new_role

                if memo_key in request.POST:
                    ap.memo = request.POST.get(memo_key, "").strip()

                ap.full_clean()
                ap.save()
                updated_count += 1

        if not _is_wizard_request(request):
            messages.success(request, f"参加者情報を一括更新しました（{updated_count}件）。")
        redirect_url = reverse("activities:activity_members", kwargs={"pk": activity.pk})
        params = []
        if request.POST.get("wizard") == "1" or request.GET.get("wizard") == "1":
            params.append("wizard=1")
        q = request.POST.get("q", "").strip()
        if q:
            params.append(f"q={q}")
        raw_back = request.POST.get(BackNavigator.PARAM_NAME) or request.GET.get(BackNavigator.PARAM_NAME)
        if raw_back:
            from urllib.parse import quote
            params.append(f"{BackNavigator.PARAM_NAME}={quote(raw_back)}")
        if params:
            redirect_url += "?" + "&".join(params)
        return redirect(redirect_url)


def _is_wizard_entry(entry):
    """スタック要素がウィザード画面かどうか判定する"""
    if not isinstance(entry, dict):
        return False
    url = entry.get("url", "")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return "wizard" in qs


def _calc_wizard_finish_url(request, activity):
    """ウィザード完了時（Step 4 保存）の戻り先（活動詳細画面）URLを生成する。
    ウィザード画面以外の直前のスタック状態（案件詳細や活動一覧等）を維持して付与する。
    """
    back = BackNavigator(request)
    activity_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
    pre_wizard_stack = [
        entry for entry in back.back_stack if not _is_wizard_entry(entry)
    ]
    if pre_wizard_stack:
        encoded = back._calc_encode_stack(pre_wizard_stack)
        return f"{activity_detail_url}?{BackNavigator.PARAM_NAME}={encoded}&wizard_completed=1"
    return f"{activity_detail_url}?wizard_completed=1"


class ActivityAttachmentManageView(LoginRequiredMixin, View):
    """活動添付ファイル管理画面（ウィザード Step 4 対応）。"""

    def get(self, request, pk):
        activity = get_object_or_404(
            Activity.objects.select_related("deal", "campaign", "user"),
            pk=pk,
        )
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        attachments = activity.attachments.select_related("uploaded_by").all()
        can_edit = can_edit_activity(request.user, activity)

        back = BackNavigator(request)
        activity_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        is_wizard = request.GET.get("wizard") == "1"
        if not is_wizard:
            back.push_current(title=f"添付ファイル: {activity}", keys=["wizard"])
        else:
            back.push_current(title=f"添付ファイル: {activity}", keys=["wizard"])

        finish_url = _calc_wizard_finish_url(request, activity) if is_wizard else activity_detail_url

        return render(
            request,
            "activities/activity_attachments_manage.html",
            {
                "activity": activity,
                "attachments": attachments,
                "can_edit": can_edit,
                "is_wizard": is_wizard,
                "back": back,
                "activity_detail_url": activity_detail_url,
                "finish_url": finish_url,
                "active_menu": "activities:activity_list",
            },
        )

    def post(self, request, pk):
        """活動添付ファイルのアップロード（ウィザード Step 4 対応）。"""
        activity = get_object_or_404(Activity, pk=pk)
        if not can_edit_activity(request.user, activity):
            raise PermissionDenied

        files = request.FILES.getlist("files") or request.FILES.getlist("file")
        next_url = request.POST.get("next") or request.GET.get("next")
        default_redirect = _calc_wizard_finish_url(request, activity)
        redirect_url = next_url or default_redirect

        # ファイルが選択されていない場合はそのまま完了・リダイレクト
        if not files:
            return redirect(redirect_url)

        if not request.user.has_perm("attachments.add_attachment"):
            raise PermissionDenied

        memo = request.POST.get("memo", "").strip()
        from attachments.views import MAX_ATTACHMENT_SIZE, BLOCKED_EXTENSIONS
        from attachments.models import Attachment
        from actionlogs.models import ActionLog
        from pathlib import Path

        errors = []
        for f in files:
            if f.size > MAX_ATTACHMENT_SIZE:
                errors.append(f"「{f.name}」: ファイルサイズは15MB以下にしてください。")
            ext = Path(f.name).suffix.lower()
            if ext in BLOCKED_EXTENSIONS:
                errors.append(f"「{f.name}」: このファイル形式はセキュリティ上の理由によりアップロードできません。")

        if errors:
            for error in errors:
                messages.error(request, error)
            step4_url = reverse("activities:activity_attachments_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
            return redirect(step4_url)

        created_attachments = []
        try:
            with transaction.atomic():
                for f in files:
                    attachment = Attachment(
                        file=f,
                        memo=memo,
                        uploaded_by=request.user,
                        original_filename=f.name,
                        activity=activity,
                    )
                    attachment.save()
                    created_attachments.append(attachment)

                    ActionLog.record(
                        user=request.user,
                        action="attachment_uploaded",
                        content_object=attachment,
                        data={
                            "original_filename": attachment.original_filename,
                            "target_type": "activity",
                            "target_id": str(activity.id),
                        },
                    )
        except Exception as e:
            messages.error(request, f"ファイルのアップロード中にエラーが発生しました: {e}")
            step4_url = reverse("activities:activity_attachments_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
            return redirect(step4_url)

        if len(created_attachments) == 1:
            messages.success(request, "ファイルをアップロードしました。")
        else:
            messages.success(request, f"{len(created_attachments)}件のファイルをアップロードしました。")

        return redirect(redirect_url)


