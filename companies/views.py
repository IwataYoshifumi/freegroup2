from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, FormView, ListView, UpdateView

from actionlogs.models import ActionLog
from back_navigator.back_navigator import BackNavigator
from companies.forms import CompanyForm, CompanyMergeConfirmForm
from companies.models import Company, CompanyDuplicateCandidate
from companies.services import execute_company_merge, mark_as_different_company
from contacts.services.normalization import normalize_organization, normalize_phone_value


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
        deals = company.deals.filter(is_archived=False).order_by("-created_at")
        context["deals"] = deals
        from activities.models import Activity
        context["activities"] = (
            Activity.objects.filter(deal__in=deals, is_archived=False)
            .select_related("deal", "user")
            .order_by("-occurred_at")
        )
        context["can_edit"] = self.request.user.has_perm("companies.change_company")
        context["can_archive"] = self.request.user.has_perm("companies.change_company")
        back = BackNavigator(self.request)
        back.push_current("会社詳細", ["page"])
        context["back"] = back
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


def _build_duplicate_groups():
    """pending 状態の候補ペアから、連結している会社群をグルーピングして返す。"""
    pending_candidates = list(
        CompanyDuplicateCandidate.objects.filter(
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING
        ).select_related("company_a", "company_b")
    )
    if not pending_candidates:
        return []

    # 1. Union-Find で連結成分を算出
    parent = {}

    def find(i):
        path = []
        while parent.get(i, i) != i:
            path.append(i)
            i = parent[i]
        for node in path:
            parent[node] = i
        return i

    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    company_ids = set()
    for cand in pending_candidates:
        cid_a = cand.company_a_id
        cid_b = cand.company_b_id
        company_ids.add(cid_a)
        company_ids.add(cid_b)
        if cid_a not in parent:
            parent[cid_a] = cid_a
        if cid_b not in parent:
            parent[cid_b] = cid_b
        union(cid_a, cid_b)

    groups_dict = defaultdict(list)
    for cid in company_ids:
        groups_dict[find(cid)].append(cid)

    # 2. 会社情報の一括取得とコンタクト件数注釈（N+1防止）
    companies_qs = (
        Company.objects.filter(id__in=company_ids)
        .annotate(contacts_count=Count("contacts", distinct=True))
    )
    company_map = {c.id: c for c in companies_qs}

    group_candidates_map = defaultdict(list)
    for cand in pending_candidates:
        root = find(cand.company_a_id)
        group_candidates_map[root].append(cand)

    RANK_PRIORITY = {
        CompanyDuplicateCandidate.Rank.EXACT_MATCH: 4,
        CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH: 3,
        CompanyDuplicateCandidate.Rank.POSSIBLE_MID: 2,
        CompanyDuplicateCandidate.Rank.POSSIBLE_LOW: 1,
    }
    rank_choices = dict(CompanyDuplicateCandidate.Rank.choices)

    def company_sort_key(c):
        richness = 0
        if c.domain:
            richness += 2
        if c.phone:
            richness += 1
        if c.address:
            richness += 1
        if c.website:
            richness += 1
        richness += getattr(c, "contacts_count", 0) or 0
        created_ts = c.created_at.timestamp() if c.created_at else 0
        return (-richness, created_ts)

    result_groups = []
    for idx, (root, cids) in enumerate(groups_dict.items(), start=1):
        comps = [company_map[cid] for cid in cids if cid in company_map]
        if not comps:
            continue
        comps.sort(key=company_sort_key)
        cand_pairs = group_candidates_map.get(root, [])

        best_prio = 0
        best_rank = CompanyDuplicateCandidate.Rank.POSSIBLE_LOW
        max_score = 0
        for cand in cand_pairs:
            prio = RANK_PRIORITY.get(cand.rank, 0)
            if prio > best_prio:
                best_prio = prio
                best_rank = cand.rank
            if cand.score > max_score:
                max_score = cand.score

        # 一致項目ラベル判定（全社共通項目）
        match_labels = []
        if len(comps) >= 2:
            raw_orgs = [
                c.organization.strip()
                for c in comps
                if c.organization and c.organization.strip()
            ]
            norm_orgs = [
                normalize_organization(c.organization)
                for c in comps
                if c.organization and normalize_organization(c.organization)
            ]
            name_match = (
                (len(raw_orgs) == len(comps) and len(set(raw_orgs)) == 1)
                or (len(norm_orgs) == len(comps) and len(set(norm_orgs)) == 1)
            )
            if name_match:
                match_labels.append("社名一致")

            domains = [
                c.domain.strip().lower()
                for c in comps
                if c.domain and c.domain.strip()
            ]
            if len(domains) == len(comps) and len(set(domains)) == 1:
                match_labels.append("ドメイン一致")

            raw_phones = [
                c.phone.strip()
                for c in comps
                if c.phone and c.phone.strip()
            ]
            norm_phones = [
                normalize_phone_value(c.phone)
                for c in comps
                if c.phone and normalize_phone_value(c.phone)
            ]
            phone_match = (
                (len(raw_phones) == len(comps) and len(set(raw_phones)) == 1)
                or (len(norm_phones) == len(comps) and len(set(norm_phones)) == 1)
            )
            if phone_match:
                match_labels.append("電話一致")

            addrs = [
                c.address.replace(" ", "").replace("　", "").strip()
                for c in comps
                if c.address and c.address.strip()
            ]
            if len(addrs) == len(comps) and len(set(addrs)) == 1:
                match_labels.append("住所一致")

        # 先頭行（1行目）を基準とした各行の差分判定
        base_comp = comps[0]
        base_org = (base_comp.organization or "").strip()
        base_domain = (base_comp.domain or "").strip().lower()
        base_phone = (base_comp.phone or "").strip()
        base_addr = (
            base_comp.address.replace(" ", "").replace("　", "").strip()
            if base_comp.address
            else ""
        )

        for i, comp in enumerate(comps):
            if i == 0:
                comp.diff_org = False
                comp.diff_domain = False
                comp.diff_phone = False
                comp.diff_address = False
            else:
                comp_org = (comp.organization or "").strip()
                comp.diff_org = bool(comp_org) and (comp_org != base_org)

                comp_domain = (comp.domain or "").strip().lower()
                comp.diff_domain = bool(comp_domain) and (comp_domain != base_domain)

                comp_phone = (comp.phone or "").strip()
                comp.diff_phone = bool(comp_phone) and (comp_phone != base_phone)

                comp_addr = (
                    comp.address.replace(" ", "").replace("　", "").strip()
                    if comp.address
                    else ""
                )
                comp.diff_address = bool(comp_addr) and (comp_addr != base_addr)

        group_obj = {
            "id": f"grp_{idx}",
            "name": comps[0].organization,
            "rank": best_rank,
            "rank_display": rank_choices.get(best_rank, best_rank),
            "max_score": max_score,
            "company_count": len(comps),
            "companies": comps,
            "match_labels": match_labels,
            "candidate_ids": [str(c.id) for c in cand_pairs],
            "candidate_ids_str": ",".join(str(c.id) for c in cand_pairs),
            "all_company_ids_str": ",".join(str(c.id) for c in comps),
        }
        result_groups.append(group_obj)

    result_groups.sort(
        key=lambda g: (RANK_PRIORITY.get(g["rank"], 0), g["max_score"]),
        reverse=True,
    )
    return result_groups


class CompanyDuplicateCandidateListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """重複会社候補レビュー一覧画面（仕様書 v1.5 §6.5.3, §7.5）。
    同一ランク・同一社名・連結ペアを持つ会社群を1つのグループとして集約。
    """

    template_name = "companies/company_candidate_list.html"
    context_object_name = "groups"
    permission_required = "companies.merge_company"
    paginate_by = 15

    def get_queryset(self):
        return _build_duplicate_groups()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["candidates"] = context["groups"]
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
        # 1. 存続会社IDの取得（surviving_company_id または surviving_company_{group_id}）
        surviving_company_id = request.POST.get("surviving_company_id")
        if not surviving_company_id:
            group_id = request.POST.get("group_id")
            if group_id and f"surviving_company_{group_id}" in request.POST:
                surviving_company_id = request.POST.get(f"surviving_company_{group_id}")
            else:
                for key, val in request.POST.items():
                    if key.startswith("surviving_company_") and val:
                        surviving_company_id = val
                        break

        # 2. マージ対象会社IDリストの取得（merge_company_ids または target_company_ids）
        raw_merge_ids = request.POST.getlist("merge_company_ids")
        if not raw_merge_ids:
            raw_merge_ids = request.POST.getlist("target_company_ids")
        if not raw_merge_ids:
            raw_merge_str = request.POST.get("merge_company_ids") or request.POST.get("target_company_ids", "")
            raw_merge_ids = [i.strip() for i in raw_merge_str.split(",") if i.strip()]
        else:
            expanded = []
            for item in raw_merge_ids:
                expanded.extend([i.strip() for i in item.split(",") if i.strip()])
            raw_merge_ids = expanded

        # 3. 別会社判定会社IDリストの取得
        raw_diff_ids = request.POST.getlist("different_company_ids")
        if not raw_diff_ids:
            raw_diff_str = request.POST.get("different_company_ids", "")
            raw_diff_ids = [i.strip() for i in raw_diff_str.split(",") if i.strip()]
        else:
            expanded = []
            for item in raw_diff_ids:
                expanded.extend([i.strip() for i in item.split(",") if i.strip()])
            raw_diff_ids = expanded

        # 4. 候補IDリストの取得
        candidate_ids_raw = request.POST.getlist("candidate_ids") or request.POST.get("candidate_ids", "")
        if isinstance(candidate_ids_raw, str):
            candidate_ids = [c.strip() for c in candidate_ids_raw.split(",") if c.strip()]
        else:
            candidate_ids = []
            for item in candidate_ids_raw:
                candidate_ids.extend([c.strip() for c in item.split(",") if c.strip()])

        if not surviving_company_id:
            messages.error(request, "存続会社が選択されていません。")
            return redirect("companies:company_candidate_list")

        # 存続会社IDをマージ対象・別会社から除外
        target_merge_ids = [cid for cid in raw_merge_ids if str(cid) != str(surviving_company_id)]
        target_diff_ids = [cid for cid in raw_diff_ids if str(cid) != str(surviving_company_id)]

        if not target_merge_ids and not target_diff_ids:
            messages.error(request, "統合対象の会社が選択されていません。")
            return redirect("companies:company_candidate_list")

        surviving_company = get_object_or_404(Company, pk=surviving_company_id)

        try:
            with transaction.atomic():
                # A. 別会社判定処理（マージによる INVALIDATED への巻き込みを防ぐため先に実行）
                diff_count = 0
                if target_diff_ids:
                    diff_companies = list(Company.objects.filter(id__in=target_diff_ids))
                    diff_cids_set = {c.id for c in diff_companies}
                    if diff_cids_set:
                        pending_diff_qs = CompanyDuplicateCandidate.objects.filter(
                            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
                        ).filter(
                            Q(company_a_id__in=diff_cids_set) | Q(company_b_id__in=diff_cids_set)
                        )
                        if candidate_ids:
                            pending_diff_qs = pending_diff_qs.filter(id__in=candidate_ids)
                        else:
                            pending_diff_qs = pending_diff_qs.filter(
                                Q(company_a_id=surviving_company.id) | Q(company_b_id=surviving_company.id)
                            )

                        for cand in pending_diff_qs:
                            mark_as_different_company(candidate_id=cand.id, user=request.user)
                        diff_count = len(diff_companies)

                # B. マージ処理
                merged_count = 0
                if target_merge_ids:
                    target_companies = list(Company.objects.filter(id__in=target_merge_ids))
                    if target_companies:
                        execute_company_merge(
                            surviving_company=surviving_company,
                            target_companies=target_companies,
                            user=request.user,
                        )
                        merged_count = len(target_companies)

                if merged_count > 0 and diff_count > 0:
                    messages.success(
                        request,
                        f"{merged_count}社を統合し、{diff_count}社を別会社として記録しました。",
                    )
                elif merged_count > 0:
                    messages.success(
                        request,
                        f"会社「{surviving_company.organization}」への統合が完了しました（{merged_count}社を統合）。",
                    )
                elif diff_count > 0:
                    messages.success(
                        request,
                        f"{diff_count}社を別会社として記録しました。",
                    )
                else:
                    messages.error(request, "有効な処理対象会社が見つかりませんでした。")
                    return redirect("companies:company_candidate_list")

                next_url = request.POST.get("next")
                if next_url:
                    return redirect(next_url)
                return redirect("companies:company_candidate_list")
        except Exception as e:
            messages.error(request, f"処理中にエラーが発生しました: {e}")
            return redirect("companies:company_candidate_list")


class CompanyMarkDifferentView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """重複会社候補「別会社」判定処理（仕様書 v1.5 §6.5.5.1, §7.5）。"""

    permission_required = "companies.merge_company"

    def post(self, request, pk=None):
        candidate_ids_raw = request.POST.getlist("candidate_ids") or request.POST.get("candidate_ids", "")
        if isinstance(candidate_ids_raw, str):
            candidate_ids = [c.strip() for c in candidate_ids_raw.split(",") if c.strip()]
        else:
            candidate_ids = []
            for item in candidate_ids_raw:
                candidate_ids.extend([c.strip() for c in item.split(",") if c.strip()])

        if pk:
            candidate_ids.append(str(pk))

        if not candidate_ids:
            messages.error(request, "対象の候補が指定されていません。")
            return redirect("companies:company_candidate_list")

        count = 0
        for cid in set(candidate_ids):
            try:
                mark_as_different_company(candidate_id=cid, user=request.user)
                count += 1
            except Exception:
                pass

        messages.success(
            request,
            f"{count} 件の候補ペアを別会社として記録しました。",
        )
        return redirect("companies:company_candidate_list")
