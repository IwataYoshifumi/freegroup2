from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from actionlogs.models import ActionLog
from back_navigator.back_navigator import BackNavigator
from companies.forms import CompanyForm, CompanyMergeConfirmForm
from companies.models import Company, CompanyDuplicateCandidate
from companies.services import execute_company_merge, mark_as_different_company


class CompanyListView(LoginRequiredMixin, ListView):
    """会社一覧画面（仕様書 v1.5 第6章）。"""

    model = Company
    template_name = "companies/company_list.html"
    context_object_name = "companies"
    paginate_by = 20

    def get_queryset(self):
        qs = Company.objects.all()

        status = self.request.GET.get("status", "active")
        if status == "active":
            qs = qs.filter(status=Company.Status.ACTIVE)
        elif status == "archived":
            qs = qs.filter(status=Company.Status.ARCHIVED)
        elif status == "merged":
            qs = qs.filter(status=Company.Status.MERGED)

        q = self.request.GET.get("q")
        if q:
            q = q.strip()
            qs = qs.filter(
                Q(organization__icontains=q)
                | Q(domain__icontains=q)
                | Q(phone__icontains=q)
                | Q(address__icontains=q)
            )

        return qs.order_by("organization", "-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(title="会社一覧", keys=["q", "status", "page"])
        context["back"] = back
        context["statuses"] = Company.Status.choices
        context["current_status"] = self.request.GET.get("status", "active")
        context["current_q"] = self.request.GET.get("q", "")
        context["active_menu"] = "companies:company_list"
        return context


class CompanyDetailView(LoginRequiredMixin, DetailView):
    """会社詳細画面（仕様書 v1.5 第6章）。"""

    model = Company
    template_name = "companies/company_detail.html"
    context_object_name = "company"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.object
        context["contacts"] = company.contacts.order_by(
            "last_name", "first_name"
        )
        context["deals"] = company.deals.filter(is_archived=False).order_by(
            "-created_at"
        )
        context["can_edit"] = self.request.user.has_perm("companies.change_company")
        context["can_archive"] = self.request.user.has_perm("companies.change_company")
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "companies:company_list"
        return context


class CompanyCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """会社新規作成画面（仕様書 v1.5 第6章）。"""

    model = Company
    form_class = CompanyForm
    template_name = "companies/company_form.html"
    permission_required = "companies.add_company"

    def form_valid(self, form):
        company = form.save(commit=False)
        company.created_by = self.request.user
        company.save()
        ActionLog.record(
            user=self.request.user,
            action="company_created",
            content_object=company,
            data={"organization": company.organization, "company_id": str(company.id)},
        )
        messages.success(self.request, f"会社「{company.organization}」を作成しました。")
        return redirect("companies:company_detail", pk=company.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = True
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "companies:company_list"
        return context


class CompanyUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """会社編集画面（仕様書 v1.5 第6章）。"""

    model = Company
    form_class = CompanyForm
    template_name = "companies/company_form.html"
    permission_required = "companies.change_company"

    def form_valid(self, form):
        company = form.save()
        ActionLog.record(
            user=self.request.user,
            action="company_updated",
            content_object=company,
            data={"organization": company.organization, "company_id": str(company.id)},
        )
        messages.success(self.request, f"会社「{company.organization}」を更新しました。")
        return redirect("companies:company_detail", pk=company.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_create"] = False
        context["back"] = BackNavigator(self.request)
        context["active_menu"] = "companies:company_list"
        return context


class CompanyArchiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """会社手動アーカイブ画面（仕様書 v1.5 §6.6）。"""

    permission_required = "companies.change_company"

    def dispatch(self, request, *args, **kwargs):
        self.company = get_object_or_404(Company, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "companies/company_confirm_archive.html",
            {
                "company": self.company,
                "back": BackNavigator(request),
                "active_menu": "companies:company_list",
            },
        )

    def post(self, request, *args, **kwargs):
        self.company.status = Company.Status.ARCHIVED
        self.company.save(update_fields=["status", "updated_at"])
        ActionLog.record(
            user=request.user,
            action="company_archived",
            content_object=self.company,
            data={"organization": self.company.organization, "company_id": str(self.company.id)},
        )
        messages.success(request, f"会社「{self.company.organization}」をアーカイブしました。")
        return redirect("companies:company_list")


class CompanyDuplicateCandidateListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """重複会社候補レビュー一覧画面（仕様書 v1.5 §6.5.3, §7.5）。"""

    model = CompanyDuplicateCandidate
    template_name = "companies/company_candidate_list.html"
    context_object_name = "candidates"
    permission_required = "companies.merge_company"
    paginate_by = 30

    def get_queryset(self):
        return (
            CompanyDuplicateCandidate.objects.filter(
                review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING
            )
            .select_related("company_a", "company_b")
            .order_by("-score", "-created_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(title="重複会社候補", keys=["page"])
        context["back"] = back
        context["active_menu"] = "companies:company_candidate_list"
        return context


class CompanyMergeView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """会社統合（マージ）画面（仕様書 v1.5 §6.5.5, §7.5）。"""

    permission_required = "companies.merge_company"

    def get(self, request, *args, **kwargs):
        surviving_id = request.GET.get("surviving_id")
        target_ids_raw = request.GET.get("target_ids", "")
        candidate_id = request.GET.get("candidate_id")

        if candidate_id and not surviving_id:
            candidate = get_object_or_404(CompanyDuplicateCandidate, pk=candidate_id)
            # 作成日時が古い方を存続会社候補とする
            if candidate.company_a.created_at <= candidate.company_b.created_at:
                surviving_company = candidate.company_a
                target_companies = [candidate.company_b]
            else:
                surviving_company = candidate.company_b
                target_companies = [candidate.company_a]
        else:
            surviving_company = get_object_or_404(Company, pk=surviving_id)
            target_ids = [i.strip() for i in target_ids_raw.split(",") if i.strip()]
            target_companies = list(Company.objects.filter(id__in=target_ids))

        target_ids_str = ",".join(str(c.id) for c in target_companies)
        form = CompanyMergeConfirmForm(
            initial={
                "surviving_company_id": surviving_company.id,
                "target_company_ids": target_ids_str,
            }
        )

        return render(
            request,
            "companies/company_merge_confirm.html",
            {
                "surviving_company": surviving_company,
                "target_companies": target_companies,
                "form": form,
                "candidate_id": candidate_id,
                "back": BackNavigator(request),
                "active_menu": "companies:company_candidate_list",
            },
        )

    def post(self, request, *args, **kwargs):
        form = CompanyMergeConfirmForm(request.POST)
        if form.is_valid():
            surviving_company_id = form.cleaned_data["surviving_company_id"]
            target_company_ids = form.cleaned_data["target_company_ids"]
            surviving_company = get_object_or_404(Company, pk=surviving_company_id)
            target_companies = list(Company.objects.filter(id__in=target_company_ids))

            try:
                execute_company_merge(
                    surviving_company=surviving_company,
                    target_companies=target_companies,
                    user=request.user,
                )
                messages.success(
                    request,
                    f"会社「{surviving_company.organization}」への統合が完了しました。",
                )
                return redirect("companies:company_detail", pk=surviving_company.pk)
            except Exception as e:
                messages.error(request, f"統合処理中にエラーが発生しました: {e}")
                return redirect("companies:company_candidate_list")
        else:
            messages.error(request, "入力内容に不備があります。")
            return redirect("companies:company_candidate_list")


class CompanyMarkDifferentView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """重複会社候補「別会社」判定処理（仕様書 v1.5 §6.5.5.1, §7.5）。"""

    permission_required = "companies.merge_company"

    def post(self, request, pk):
        candidate = mark_as_different_company(candidate_id=pk, user=request.user)
        messages.success(
            request,
            f"「{candidate.company_a.organization}」と「{candidate.company_b.organization}」を別会社として記録しました。",
        )
        return redirect("companies:company_candidate_list")
