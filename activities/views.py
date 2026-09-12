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
from deals.models import PersonRole, UserRole
from mailings.models import Campaign
from persons.models import Person


class ActivityListView(LoginRequiredMixin, ListView):
    """活動一覧画面（仕様書 v1.5 第3章, §7.3）。"""

    model = Activity
    template_name = "activities/activity_list.html"
    context_object_name = "activities"
    paginate_by = 20

    def get_queryset(self):
        qs = visible_activities_for(self.request.user)

        status = self.request.GET.get("status", "active")
        if status == "active":
            qs = qs.filter(is_archived=False)
        elif status == "archived":
            qs = qs.filter(is_archived=True)

        mode = self.request.GET.get("mode")
        date_str = self.request.GET.get("date")

        if mode == "daily" or (not self.request.GET and not date_str):
            # デフォルトで日報（本日分）を表示
            today = timezone.localdate()
            qs = qs.filter(occurred_at__date=today)
        elif date_str:
            try:
                qs = qs.filter(occurred_at__date=date_str)
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
                | Q(activity_persons__person__primary_contact__last_name__icontains=q)
                | Q(activity_persons__person__primary_contact__first_name__icontains=q)
            ).distinct()

        return qs.select_related("deal", "campaign", "user").prefetch_related(
            "activity_persons__person__primary_contact"
        ).order_by("-occurred_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(
            title="活動一覧",
            keys=["mode", "date", "activity_type", "direction", "q", "status", "page"],
        )
        context["back"] = back
        context["activity_types"] = ActivityType.choices
        context["directions"] = Direction.choices
        context["current_activity_type"] = self.request.GET.get("activity_type", "")
        context["current_direction"] = self.request.GET.get("direction", "")
        context["current_status"] = self.request.GET.get("status", "active")
        context["current_date"] = self.request.GET.get("date", "")
        context["current_mode"] = self.request.GET.get("mode", "daily" if not self.request.GET else "")
        context["current_q"] = self.request.GET.get("q", "")
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
        back = BackNavigator(request)
        back.push_current(title=f"活動: {self.object}", keys=["page"])
        context = self.get_context_data(object=self.object, back=back)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        activity = self.object
        context["activity_persons"] = activity.activity_persons.select_related(
            "person__primary_contact__company"
        ).all()
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

    def get_initial(self):
        initial = super().get_initial()
        deal_id = self.request.GET.get("deal_id") or self.request.GET.get("deal")
        if deal_id:
            initial["deal"] = deal_id
        campaign_id = self.request.GET.get("campaign") or self.request.GET.get("campaign_id")
        if campaign_id:
            initial["campaign"] = campaign_id
        return initial

    def get_success_url(self):
        back = BackNavigator(self.request)
        if back.back_exist:
            return back.back_url
        return reverse("activities:activity_detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.created_by = self.request.user
        activity = form.save()
        self.object = activity

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
        messages.success(self.request, "活動を記録しました。")
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = True
        context["person_id"] = (
            self.request.GET.get("person_id") or self.request.GET.get("person", "")
        )
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
        messages.success(self.request, "活動記録を更新しました。")
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
        person_qs = (
            Person.objects.exclude(status=Person.Status.MERGED)
            .exclude(id__in=existing_person_ids)
            .select_related("primary_contact__company")
        )

        if q:
            person_qs = person_qs.filter(
                Q(primary_contact__first_name__icontains=q)
                | Q(primary_contact__last_name__icontains=q)
                | Q(primary_contact__company__organization__icontains=q)
                | Q(primary_contact__organization__icontains=q)
                | Q(primary_contact__title__icontains=q)
                | Q(primary_contact__email__icontains=q)
            ).distinct()[:50]
        elif activity.deal_id:
            deal_person_ids = list(
                activity.deal.deal_persons.exclude(person_id__in=existing_person_ids).values_list("person_id", flat=True)
            )
            if (
                activity.deal.primary_person_id
                and activity.deal.primary_person_id not in existing_person_ids
                and activity.deal.primary_person_id not in deal_person_ids
            ):
                deal_person_ids.insert(0, activity.deal.primary_person_id)

            deal_persons_list = list(
                person_qs.filter(id__in=deal_person_ids).order_by("-created_at")[:30]
            )
            deal_person_id_set = {p.id for p in deal_persons_list}
            remaining_limit = 50 - len(deal_persons_list)
            other_persons = list(
                person_qs.exclude(id__in=deal_person_id_set).order_by("-created_at")[:remaining_limit]
            )
            person_qs = deal_persons_list + other_persons
        else:
            person_qs = list(person_qs.order_by("-created_at")[:50])

        back = BackNavigator(request)
        activity_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        back.push_current(title=f"関係者管理: {activity}", keys=["q"])

        is_edit_mode = request.GET.get("edit") == "1"

        return render(
            request,
            "activities/activity_persons_manage.html",
            {
                "activity": activity,
                "activity_persons": activity_persons,
                "candidate_persons": person_qs,
                "current_q": q,
                "is_edit_mode": is_edit_mode,
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

        messages.success(request, f"社外関係者情報を一括更新しました（{updated_count}件）。")
        redirect_url = reverse("activities:activity_persons_manage", kwargs={"pk": activity.pk})
        q = request.POST.get("q", "").strip()
        if q:
            redirect_url += f"?q={q}"
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
        user_qs = (
            User.objects.filter(is_active=True)
            .exclude(id__in=existing_user_ids)
            .select_related("person__primary_contact")
            .order_by("username")
        )

        q = request.GET.get("q", "").strip()
        if q:
            user_qs = user_qs.filter(
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
            ).distinct()[:50]
        else:
            user_qs = list(user_qs[:50])

        back = BackNavigator(request)
        activity_detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        back.push_current(title=f"同席者管理: {activity}", keys=["q"])

        is_edit_mode = request.GET.get("edit") == "1"

        return render(
            request,
            "activities/activity_users_manage.html",
            {
                "activity": activity,
                "activity_users": activity_users,
                "candidate_users": user_qs,
                "current_q": q,
                "is_edit_mode": is_edit_mode,
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

        messages.success(request, f"社内同席者情報を一括更新しました（{updated_count}件）。")
        redirect_url = reverse("activities:activity_users_manage", kwargs={"pk": activity.pk})
        q = request.POST.get("q", "").strip()
        if q:
            redirect_url += f"?q={q}"
        return redirect(redirect_url)
