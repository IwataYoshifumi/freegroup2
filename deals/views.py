import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.db.models import OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from actionlogs.models import ActionLog
from activities.models import Activity
from back_navigator.back_navigator import BackNavigator
from companies.models import Company
from deals.forms import (
    DealCloseForm,
    DealCreateForm,
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
        back = BackNavigator(self.request)
        back.push_current(title=f"案件: {deal.name}", keys=["page"])
        context["back"] = back
        context["active_menu"] = "deals:deal_list"
        return context


class DealCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """案件新規作成画面（仕様書 v1.5 §2.1）。"""

    model = Deal
    form_class = DealCreateForm
    template_name = "deals/deal_form.html"
    permission_required = "deals.add_deal"

    def get_initial(self):
        initial = super().get_initial()
        initial["owner"] = self.request.user
        company_id = self.request.GET.get("company_id") or self.request.GET.get("company")
        if company_id:
            initial["company"] = company_id
        lead_source = self.request.GET.get("lead_source")
        if lead_source:
            initial["lead_source"] = lead_source
        source_campaign = self.request.GET.get("source_campaign") or self.request.GET.get("campaign")
        if source_campaign:
            initial["source_campaign"] = source_campaign
            if not lead_source:
                initial["lead_source"] = Deal.LeadSource.CAMPAIGN
        return initial

    def get_success_url(self):
        back = BackNavigator(self.request)
        if back.back_exist:
            return back.back_url
        return reverse("deals:deal_detail", kwargs={"pk": self.object.pk})

    @transaction.atomic
    def form_valid(self, form):
        deal = form.save(commit=False)
        deal.created_by = self.request.user
        deal.updated_by = self.request.user
        if not deal.owner_id:
            deal.owner = self.request.user
        deal.save()
        self.object = deal

        # 関連 Person (DealPerson) の自動紐付け
        person_id = self.request.POST.get("person") or self.request.GET.get("person") or self.request.GET.get("person_id")
        if person_id:
            try:
                person_obj = Person.objects.filter(pk=person_id).first()
                if person_obj:
                    defaults = {"role": PersonRole.CONTACT_WINDOW}
                    if hasattr(DealPerson, "is_primary"):
                        defaults["is_primary"] = True
                    DealPerson.objects.get_or_create(
                        deal=deal,
                        person=person_obj,
                        defaults=defaults,
                    )
            except Exception:
                pass

        ActionLog.record(
            user=self.request.user,
            action="deal_created",
            content_object=deal,
            data={"name": deal.name, "deal_id": str(deal.id)},
        )
        messages.success(self.request, f"案件「{deal.name}」を作成しました。")
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = True
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        company_val = self.request.POST.get("company") or self.request.GET.get("company") or self.request.GET.get("company_id")
        if company_val:
            try:
                context["selected_company"] = Company.objects.filter(pk=company_val).first()
            except Exception:
                pass
        person_val = self.request.POST.get("person") or self.request.GET.get("person") or self.request.GET.get("person_id")
        if person_val:
            try:
                context["selected_person"] = Person.objects.select_related("primary_contact").filter(pk=person_val).first()
            except Exception:
                pass
        return context


class DealUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
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
        company_val = self.request.POST.get("company")
        if company_val:
            try:
                context["selected_company"] = Company.objects.filter(pk=company_val).first()
            except Exception:
                pass
        elif self.object and self.object.company:
            context["selected_company"] = self.object.company
        return context


class CompanySearchView(LoginRequiredMixin, View):
    """案件フォーム用会社検索エンドポイント（JSON返却）。"""

    def get(self, request, *args, **kwargs):
        q = request.GET.get("q", "").strip()
        qs = Company.objects.exclude(status=Company.Status.MERGED)
        if q:
            qs = qs.filter(
                Q(organization__icontains=q)
                | Q(domain__icontains=q)
                | Q(address__icontains=q)
            )
        qs = qs.order_by("organization")[:30]
        results = [
            {
                "id": str(c.id),
                "organization": c.organization,
                "domain": c.domain or "",
                "address": c.address or "",
            }
            for c in qs
        ]
        return JsonResponse({"results": results})


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
