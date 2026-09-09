import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from actionlogs.models import ActionLog
from activities.models import Activity
from back_navigator.back_navigator import BackNavigator
from deals.forms import (
    DealCloseForm,
    DealForm,
    DealPersonForm,
    DealReassignOwnerForm,
    DealReassignPrimaryPersonForm,
    DealUpdateForm,
    DealUserForm,
)
from deals.models import Deal, DealPerson, DealUser, PersonRole, Stage, UserRole
from deals.permissions import (
    can_archive_deal,
    can_edit_deal,
    can_reassign_deal_owner,
    can_reassign_deal_primary_person,
    can_view_deal,
    visible_deals_for,
)
from deals.services import (
    archive_deal,
    close_deal,
    reassign_deal_owner,
    reassign_deal_primary_person,
)
from persons.models import Person


class DealListView(LoginRequiredMixin, ListView):
    """案件一覧画面（仕様書 v1.5 §2.5, §7.1, §9.1）。"""

    model = Deal
    template_name = "deals/deal_list.html"
    context_object_name = "deals"
    paginate_by = 20

    def get_queryset(self):
        last_activity = (
            Activity.objects.filter(deal=OuterRef("pk"), is_archived=False)
            .order_by("-occurred_at")
            .values("occurred_at")[:1]
        )
        qs = visible_deals_for(self.request.user).annotate(
            last_contact_date=Subquery(
                last_activity, output_field=models.DateTimeField()
            )
        )

        status = self.request.GET.get("status", "active")
        if status == "active":
            qs = qs.filter(is_archived=False)
        elif status == "archived":
            qs = qs.filter(is_archived=True)

        stage = self.request.GET.get("stage")
        if stage:
            qs = qs.filter(stage=stage)

        q = self.request.GET.get("q")
        if q:
            q = q.strip()
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(company__organization__icontains=q)
                | Q(primary_person__primary_contact__last_name__icontains=q)
                | Q(primary_person__primary_contact__first_name__icontains=q)
            )

        return qs.select_related("company", "owner", "primary_person__primary_contact")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(title="案件一覧", keys=["q", "stage", "status", "page"])
        context["back"] = back
        context["stages"] = Stage.choices
        context["current_stage"] = self.request.GET.get("stage", "")
        context["current_status"] = self.request.GET.get("status", "active")
        context["current_q"] = self.request.GET.get("q", "")
        context["active_menu"] = "deals:deal_list"
        return context


class DealDetailView(LoginRequiredMixin, DetailView):
    """案件詳細画面（仕様書 v1.5 §2.5, §7.1）。"""

    model = Deal
    template_name = "deals/deal_detail.html"
    context_object_name = "deal"

    def get_queryset(self):
        return Deal.objects.select_related(
            "company",
            "owner",
            "primary_person__primary_contact",
            "source_campaign",
            "created_by",
            "updated_by",
        )

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        if not can_view_deal(self.request.user, obj):
            raise PermissionDenied
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deal = self.object
        context["activities"] = (
            deal.activities.filter(is_archived=False)
            .select_related("user")
            .prefetch_related("activity_persons__person__primary_contact")
            .order_by("-occurred_at")
        )
        context["deal_persons"] = deal.deal_persons.select_related(
            "person__primary_contact"
        ).all()
        context["deal_users"] = deal.deal_users.select_related("user").all()
        context["attachments"] = deal.attachments.select_related("uploaded_by").all()
        context["can_edit"] = can_edit_deal(self.request.user, deal)
        context["can_archive"] = can_archive_deal(self.request.user, deal)
        context["can_reassign_owner"] = can_reassign_deal_owner(self.request.user, deal)
        context["can_reassign_primary_person"] = can_reassign_deal_primary_person(
            self.request.user, deal
        )
        from attachments.forms import AttachmentUploadForm

        context["attachment_form"] = AttachmentUploadForm()
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        return context


class _RelatedPersonsSyncMixin:
    """相手方関係者（DealPerson）の同期および選択肢コンテキスト提供Mixin。"""

    def _get_candidate_persons(self):
        return (
            Person.objects.exclude(status=Person.Status.MERGED)
            .select_related("primary_contact__company")
            .order_by("-created_at")
        )

    def _sync_related_persons(self, deal):
        raw_ids = self.request.POST.getlist("related_person_ids")
        target_person_ids = set()
        for item in raw_ids:
            for part in str(item).split(","):
                part = part.strip()
                if part:
                    try:
                        target_person_ids.add(uuid.UUID(part))
                    except (ValueError, AttributeError):
                        pass

        # 主担当者（primary_person）と重複しているIDは除外（DealPerson.clean()ガード準拠）
        if deal.primary_person_id:
            target_person_ids.discard(deal.primary_person_id)

        # 解除されたDealPersonは削除
        DealPerson.objects.filter(deal=deal).exclude(
            person_id__in=target_person_ids
        ).delete()

        # 選択されたPersonをDealPersonとして登録
        if target_person_ids:
            valid_persons = Person.objects.filter(id__in=target_person_ids)
            for person in valid_persons:
                DealPerson.objects.get_or_create(
                    deal=deal,
                    person=person,
                    defaults={"role": PersonRole.ATTENDEE},
                )

    def _get_initial_related_persons(self, deal=None):
        if self.request.method == "POST":
            raw_ids = self.request.POST.getlist("related_person_ids")
            p_ids = []
            for item in raw_ids:
                for part in str(item).split(","):
                    part = part.strip()
                    if part and part not in p_ids:
                        try:
                            uuid.UUID(part)
                            p_ids.append(part)
                        except ValueError:
                            pass
            if p_ids:
                persons = Person.objects.filter(id__in=p_ids).select_related(
                    "primary_contact__company"
                )
                return [
                    {
                        "id": str(p.id),
                        "name": (
                            p.primary_contact.full_name
                            if p.primary_contact and p.primary_contact.full_name
                            else str(p)
                        ),
                        "company": (
                            p.primary_contact.company.organization
                            if p.primary_contact and p.primary_contact.company
                            else (
                                p.primary_contact.organization
                                if p.primary_contact
                                else ""
                            )
                        ),
                    }
                    for p in persons
                ]
            return []

        if deal and deal.pk:
            return [
                {
                    "id": str(dp.person_id),
                    "name": (
                        dp.person.primary_contact.full_name
                        if dp.person.primary_contact and dp.person.primary_contact.full_name
                        else str(dp.person)
                    ),
                    "company": (
                        dp.person.primary_contact.company.organization
                        if dp.person.primary_contact
                        and dp.person.primary_contact.company
                        else (
                            dp.person.primary_contact.organization
                            if dp.person.primary_contact
                            else ""
                        )
                    ),
                }
                for dp in deal.deal_persons.select_related(
                    "person__primary_contact__company"
                ).all()
            ]
        return []


class DealCreateView(LoginRequiredMixin, PermissionRequiredMixin, _RelatedPersonsSyncMixin, CreateView):
    """案件新規作成画面（仕様書 v1.5 §2.1）。"""

    model = Deal
    form_class = DealForm
    template_name = "deals/deal_form.html"
    permission_required = "deals.add_deal"

    def get_initial(self):
        initial = super().get_initial()
        initial["owner"] = self.request.user
        person_id = self.request.GET.get("person_id")
        if person_id:
            initial["primary_person"] = person_id
        company_id = self.request.GET.get("company_id")
        if company_id:
            initial["company"] = company_id
        return initial

    @transaction.atomic
    def form_valid(self, form):
        deal = form.save(commit=False)
        deal.created_by = self.request.user
        deal.updated_by = self.request.user
        if not deal.owner_id:
            deal.owner = self.request.user
        if not deal.company_id and deal.primary_person:
            primary_contact = getattr(deal.primary_person, "primary_contact", None)
            if primary_contact and primary_contact.company:
                deal.company = primary_contact.company
        deal.save()
        self._sync_related_persons(deal)
        ActionLog.record(
            user=self.request.user,
            action="deal_created",
            content_object=deal,
            data={"name": deal.name, "deal_id": str(deal.id)},
        )
        messages.success(self.request, f"案件「{deal.name}」を作成しました。")
        return redirect("deals:deal_detail", pk=deal.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = True
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        context["candidate_persons"] = self._get_candidate_persons()
        context["initial_related_persons"] = self._get_initial_related_persons()
        return context


class DealUpdateView(LoginRequiredMixin, PermissionRequiredMixin, _RelatedPersonsSyncMixin, UpdateView):
    """案件通常編集画面（仕様書 v1.5 §2.6, §0.15）。"""

    model = Deal
    form_class = DealUpdateForm
    template_name = "deals/deal_form.html"
    permission_required = "deals.change_deal"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        if not can_edit_deal(self.request.user, obj):
            raise PermissionDenied
        return obj

    @transaction.atomic
    def form_valid(self, form):
        deal = form.save(commit=False)
        deal.updated_by = self.request.user
        deal.save()
        self._sync_related_persons(deal)
        ActionLog.record(
            user=self.request.user,
            action="deal_updated",
            content_object=deal,
            data={"name": deal.name, "deal_id": str(deal.id)},
        )
        messages.success(self.request, f"案件「{deal.name}」を更新しました。")
        return redirect("deals:deal_detail", pk=deal.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = False
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        context["candidate_persons"] = self._get_candidate_persons()
        context["initial_related_persons"] = self._get_initial_related_persons(deal=self.object)
        return context


class DealCloseView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    """案件クローズ（受注/失注）画面（仕様書 v1.5 §2.5）。"""

    form_class = DealCloseForm
    template_name = "deals/deal_close.html"
    permission_required = "deals.change_deal"

    def dispatch(self, request, *args, **kwargs):
        self.deal = get_object_or_404(Deal, pk=kwargs["pk"])
        if not can_edit_deal(request.user, self.deal):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        stage = form.cleaned_data["stage"]
        closed_at = form.cleaned_data["closed_at"]
        lost_reason = form.cleaned_data["lost_reason"]
        close_deal(
            deal=self.deal,
            stage=stage,
            user=self.request.user,
            closed_at=closed_at,
            lost_reason=lost_reason,
        )
        messages.success(
            self.request,
            f"案件「{self.deal.name}」を「{self.deal.get_stage_display()}」に更新しました。",
        )
        return redirect("deals:deal_detail", pk=self.deal.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["deal"] = self.deal
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        return context


class DealReassignOwnerView(LoginRequiredMixin, FormView):
    """案件担当者（owner）付け替え画面（仕様書 v1.5 §2.6）。"""

    form_class = DealReassignOwnerForm
    template_name = "deals/deal_reassign_owner.html"

    def dispatch(self, request, *args, **kwargs):
        self.deal = get_object_or_404(Deal, pk=kwargs["pk"])
        if not can_reassign_deal_owner(request.user, self.deal):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        if self.deal.owner:
            initial["new_owner"] = self.deal.owner
        return initial

    def form_valid(self, form):
        new_owner = form.cleaned_data["new_owner"]
        reassign_deal_owner(
            deal=self.deal,
            new_owner=new_owner,
            user=self.request.user,
        )
        messages.success(
            self.request,
            f"案件「{self.deal.name}」の担当者を「{new_owner}」に変更しました。",
        )
        return redirect("deals:deal_detail", pk=self.deal.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["deal"] = self.deal
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        return context


class DealReassignPrimaryPersonView(LoginRequiredMixin, FormView):
    """案件主担当者（相手方Person）付け替え画面（仕様書 v1.5 §2.6.1）。"""

    form_class = DealReassignPrimaryPersonForm
    template_name = "deals/deal_reassign_primary_person.html"

    def dispatch(self, request, *args, **kwargs):
        self.deal = get_object_or_404(Deal, pk=kwargs["pk"])
        if not can_reassign_deal_primary_person(request.user, self.deal):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        if self.deal.primary_person:
            initial["new_person"] = self.deal.primary_person
        return initial

    def form_valid(self, form):
        new_person = form.cleaned_data["new_person"]
        reassign_deal_primary_person(
            deal=self.deal,
            new_person=new_person,
            user=self.request.user,
        )
        messages.success(
            self.request,
            f"案件「{self.deal.name}」の相手方主担当者を「{new_person}」に変更しました。",
        )
        return redirect("deals:deal_detail", pk=self.deal.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["deal"] = self.deal
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        return context


class DealArchiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """案件アーカイブ画面（仕様書 v1.5 §2.8, §7.1）。"""

    permission_required = "deals.change_deal"

    def dispatch(self, request, *args, **kwargs):
        self.deal = get_object_or_404(Deal, pk=kwargs["pk"])
        if not can_archive_deal(request.user, self.deal):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from django.shortcuts import render

        return render(
            request,
            "deals/deal_confirm_archive.html",
            {
                "deal": self.deal,
                "back": BackNavigator(request),
                "active_menu": "deals:deal_list",
            },
        )

    def post(self, request, *args, **kwargs):
        archive_deal(self.deal, user=request.user)
        messages.success(request, f"案件「{self.deal.name}」をアーカイブしました。")
        return redirect("deals:deal_list")


class DealAddPersonView(LoginRequiredMixin, View):
    """案件関係者（社外パーソン）追加。"""

    def post(self, request, pk):
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied
        form = DealPersonForm(request.POST)
        if form.is_valid():
            person_rel = form.save(commit=False)
            person_rel.deal = deal
            try:
                person_rel.full_clean()
                person_rel.save()
                messages.success(request, f"関係者「{person_rel.person}」を追加しました。")
            except Exception as e:
                messages.error(request, f"関係者の追加に失敗しました: {e}")
        else:
            messages.error(request, "入力内容をご確認ください。")
        next_url = request.POST.get("next") or reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        return redirect(next_url)


class DealDeletePersonView(LoginRequiredMixin, View):
    """案件関係者（社外パーソン）削除。"""

    def post(self, request, pk, person_rel_id):
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied
        rel = get_object_or_404(DealPerson, pk=person_rel_id, deal=deal)
        rel.delete()
        messages.success(request, "関係者を解除しました。")
        next_url = request.POST.get("next") or reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        return redirect(next_url)


class DealAddUserView(LoginRequiredMixin, View):
    """案件担当者（社内ユーザー）追加。"""

    def post(self, request, pk):
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied
        form = DealUserForm(request.POST)
        if form.is_valid():
            user_rel = form.save(commit=False)
            user_rel.deal = deal
            try:
                user_rel.full_clean()
                user_rel.save()
                messages.success(request, f"社内担当者「{user_rel.user}」を追加しました。")
            except Exception as e:
                messages.error(request, f"社内担当者の追加に失敗しました: {e}")
        else:
            messages.error(request, "入力内容をご確認ください。")
        next_url = request.POST.get("next") or reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        return redirect(next_url)


class DealDeleteUserView(LoginRequiredMixin, View):
    """案件担当者（社内ユーザー）削除。"""

    def post(self, request, pk, user_rel_id):
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied
        rel = get_object_or_404(DealUser, pk=user_rel_id, deal=deal)
        rel.delete()
        messages.success(request, "社内担当者を解除しました。")
        next_url = request.POST.get("next") or reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        return redirect(next_url)


class DealPersonManageView(LoginRequiredMixin, View):
    """社外関係者管理画面（指示書 v1.7）。"""

    def get(self, request, pk):
        deal = get_object_or_404(
            Deal.objects.select_related("company", "owner", "primary_person__primary_contact"),
            pk=pk,
        )
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied

        deal_persons = deal.deal_persons.select_related(
            "person__primary_contact__company",
        ).all()

        existing_person_ids = set(deal_persons.values_list("person_id", flat=True))
        if deal.primary_person_id:
            existing_person_ids.add(deal.primary_person_id)

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
        elif deal.company_id:
            company_persons = list(
                person_qs.filter(primary_contact__company_id=deal.company_id).order_by("-created_at")[:30]
            )
            company_person_ids = {p.id for p in company_persons}
            remaining_limit = 50 - len(company_persons)
            other_persons = list(
                person_qs.exclude(id__in=company_person_ids).order_by("-created_at")[:remaining_limit]
            )
            person_qs = company_persons + other_persons
        else:
            person_qs = list(person_qs.order_by("-created_at")[:50])

        back = BackNavigator(request)
        deal_detail_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        back.push_current(title=f"関係者管理: {deal.name}", keys=["q"])

        is_edit_mode = request.GET.get("edit") == "1"

        return render(
            request,
            "deals/deal_persons_manage.html",
            {
                "deal": deal,
                "deal_persons": deal_persons,
                "candidate_persons": person_qs,
                "current_q": q,
                "is_edit_mode": is_edit_mode,
                "person_roles": PersonRole.choices,
                "back": back,
                "deal_detail_url": deal_detail_url,
                "active_menu": "deals:deal_list",
            },
        )

    def post(self, request, pk):
        """社外関係者の一括更新（バッチアップデート）。"""
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied

        deal_persons = deal.deal_persons.all()
        updated_count = 0
        with transaction.atomic():
            for dp in deal_persons:
                exists_key = f"person_{dp.id}_exists"
                if exists_key not in request.POST:
                    continue
                role_key = f"person_{dp.id}_role"
                memo_key = f"person_{dp.id}_memo"

                if role_key in request.POST:
                    new_role = request.POST.get(role_key)
                    if new_role in dict(PersonRole.choices):
                        dp.role = new_role

                if memo_key in request.POST:
                    dp.memo = request.POST.get(memo_key, "").strip()

                dp.full_clean()
                dp.save()
                updated_count += 1

        messages.success(request, f"社外関係者情報を一括更新しました（{updated_count}件）。")
        redirect_url = reverse("deals:deal_persons_manage", kwargs={"pk": deal.pk})
        q = request.POST.get("q", "").strip()
        if q:
            redirect_url += f"?q={q}"
        return redirect(redirect_url)


class DealUserManageView(LoginRequiredMixin, View):
    """社内担当者管理画面（指示書 v1.7）。"""

    def get(self, request, pk):
        deal = get_object_or_404(
            Deal.objects.select_related("company", "owner"),
            pk=pk,
        )
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied

        deal_users = deal.deal_users.select_related("user", "user__person__primary_contact").all()

        existing_user_ids = set(deal_users.values_list("user_id", flat=True))
        if deal.owner_id:
            existing_user_ids.add(deal.owner_id)

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
        deal_detail_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})
        back.push_current(title=f"担当者管理: {deal.name}", keys=["q"])

        is_edit_mode = request.GET.get("edit") == "1"

        return render(
            request,
            "deals/deal_users_manage.html",
            {
                "deal": deal,
                "deal_users": deal_users,
                "candidate_users": user_qs,
                "current_q": q,
                "is_edit_mode": is_edit_mode,
                "user_roles": UserRole.choices,
                "back": back,
                "deal_detail_url": deal_detail_url,
                "active_menu": "deals:deal_list",
            },
        )

    def post(self, request, pk):
        """社内担当者の一括更新（バッチアップデート）。"""
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied

        deal_users = deal.deal_users.all()
        updated_count = 0
        with transaction.atomic():
            for du in deal_users:
                exists_key = f"user_{du.id}_exists"
                if exists_key not in request.POST:
                    continue
                role_key = f"user_{du.id}_role"
                can_edit_key = f"user_{du.id}_can_edit"
                memo_key = f"user_{du.id}_memo"

                if role_key in request.POST:
                    new_role = request.POST.get(role_key)
                    if new_role in dict(UserRole.choices):
                        du.role = new_role

                du.can_edit = (can_edit_key in request.POST)

                if memo_key in request.POST:
                    du.memo = request.POST.get(memo_key, "").strip()

                du.full_clean()
                du.save()
                updated_count += 1

        messages.success(request, f"社内担当者情報を一括更新しました（{updated_count}件）。")
        redirect_url = reverse("deals:deal_users_manage", kwargs={"pk": deal.pk})
        q = request.POST.get("q", "").strip()
        if q:
            redirect_url += f"?q={q}"
        return redirect(redirect_url)
