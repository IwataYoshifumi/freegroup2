"""案件リスト（DealList）CRUD View（仕様書 v1.6 §2.3.3 No.17〜20）。"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from actionlogs.constants import DEAL_LIST_CREATED, DEAL_LIST_DELETED, DEAL_LIST_UPDATED
from actionlogs.models import ActionLog
from deals.forms import DealListForm
from deals.models import DealList
from permissions.services import AccessListService


class DealListListView(LoginRequiredMixin, ListView):
    """案件リスト一覧（仕様書 v1.6 §2.3.3 No.17）。"""

    model = DealList
    template_name = "deals/deal_list_list.html"
    context_object_name = "deal_lists"

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.has_perm("deals.view_all_deals"):
            return DealList.objects.all().order_by("name")
        accessible_ids = AccessListService.accessible_deal_list_ids(user)
        return DealList.objects.filter(id__in=accessible_ids).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_add"] = user.is_superuser or user.has_perm("deals.add_deallist")
        context["active_menu"] = "deals:deal_list_list"
        return context


class DealListCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """案件リスト新規作成（仕様書 v1.6 §2.3.3 No.18）。"""

    permission_required = "deals.add_deallist"
    model = DealList
    form_class = DealListForm
    template_name = "deals/deal_list_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        with transaction.atomic():
            response = super().form_valid(form)
            ActionLog.record(
                user=self.request.user,
                action=DEAL_LIST_CREATED,
                content_object=self.object,
                object_repr=str(self.object),
            )
            messages.success(self.request, f"案件リスト「{self.object.name}」を作成しました。")
            return response

    def get_success_url(self):
        return reverse("deal_lists:deal_list_detail", kwargs={"pk": self.object.pk})


class DealListDetailView(LoginRequiredMixin, DetailView):
    """案件リスト詳細（仕様書 v1.6 §2.3.3 No.17/詳細）。"""

    model = DealList
    template_name = "deals/deal_list_detail.html"
    context_object_name = "deal_list"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_edit"] = user.is_superuser or user.has_perm("deals.change_deallist")
        context["can_delete"] = user.is_superuser or user.has_perm("deals.delete_deallist")
        context["deals"] = self.object.deals.visible_for(user).select_related("owner", "company")[:50]
        context["deals_count"] = self.object.deals.count()
        return context


class DealListUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """案件リスト編集（仕様書 v1.6 §2.3.3 No.19）。
    アクセスリスト選択肢の絞り込み、および現在値保護（v1.6）を内包。
    """

    permission_required = "deals.change_deallist"
    model = DealList
    form_class = DealListForm
    template_name = "deals/deal_list_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            ActionLog.record(
                user=self.request.user,
                action=DEAL_LIST_UPDATED,
                content_object=self.object,
                object_repr=str(self.object),
            )
            messages.success(self.request, f"案件リスト「{self.object.name}」を更新しました。")
            return response

    def get_success_url(self):
        return reverse("deal_lists:deal_list_detail", kwargs={"pk": self.object.pk})


class DealListDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """案件リスト削除（仕様書 v1.6 §2.3.3 No.20）。
    所属Dealが存在する間はPROTECTにより削除不可。
    """

    permission_required = "deals.delete_deallist"

    def post(self, request, pk):
        deal_list = get_object_or_404(DealList, pk=pk)
        deal_count = deal_list.deals.count()
        if deal_count > 0:
            messages.error(
                request,
                f"この案件リストには案件が {deal_count} 件登録されているため削除できません。",
            )
            return redirect("deal_lists:deal_list_detail", pk=pk)

        with transaction.atomic():
            name = deal_list.name
            ActionLog.record(
                user=request.user,
                action=DEAL_LIST_DELETED,
                object_repr=name,
            )
            deal_list.delete()
            messages.success(request, f"案件リスト「{name}」を削除しました。")
            return redirect("deal_lists:deal_list_list")
