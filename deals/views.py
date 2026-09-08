from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect
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
from deals.models import Deal, DealPerson, DealUser, Stage
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
        context["deal_person_form"] = DealPersonForm()
        context["deal_user_form"] = DealUserForm()
        from attachments.forms import AttachmentUploadForm

        context["attachment_form"] = AttachmentUploadForm()
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "deals:deal_list"
        return context


class DealCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
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
        return redirect("deals:deal_detail", pk=deal.pk)


class DealDeletePersonView(LoginRequiredMixin, View):
    """案件関係者（社外パーソン）削除。"""

    def post(self, request, pk, person_rel_id):
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied
        rel = get_object_or_404(DealPerson, pk=person_rel_id, deal=deal)
        rel.delete()
        messages.success(request, "関係者を解除しました。")
        return redirect("deals:deal_detail", pk=deal.pk)


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
        return redirect("deals:deal_detail", pk=deal.pk)


class DealDeleteUserView(LoginRequiredMixin, View):
    """案件担当者（社内ユーザー）削除。"""

    def post(self, request, pk, user_rel_id):
        deal = get_object_or_404(Deal, pk=pk)
        if not can_edit_deal(request.user, deal):
            raise PermissionDenied
        rel = get_object_or_404(DealUser, pk=user_rel_id, deal=deal)
        rel.delete()
        messages.success(request, "社内担当者を解除しました。")
        return redirect("deals:deal_detail", pk=deal.pk)
