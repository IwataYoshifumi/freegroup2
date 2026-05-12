"""duplicates アプリの View 層（仕様書 §11.3 / §11.5）。

DuplicateCandidateGroupListView：重複候補グループ一覧（URL 15 番）。
  group_id 単位で集約表示、rank / progress / user で絞り込み。
DuplicateCandidateGroupDetailView：重複候補グループ詳細（URL 16 番）。
  pending / merged / different_person の集計と表示切替（§11.5.1）。
DuplicateCandidateGroupUpdateView：重複候補レビュー画面（URL 17 番、GET のみ、D-4b）。
  仕様書 §11.5.2 の 5 ステップで次ペアを表示、POST は D-4c で別途実装。
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import (
    Case,
    Count,
    IntegerField,
    Min,
    Q,
    Value,
    When,
)
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.generic import ListView, View

from back_navigator.back_navigator import BackNavigator

from .forms import MergeForm
from .models import DuplicateCandidate


class DuplicateCandidateGroupListView(LoginRequiredMixin, ListView):
    """重複候補グループ一覧画面（15 番、仕様書 §11.3 / §11.5）。

    DuplicateCandidate を group_id 単位で集約表示。各 group の rank（全件同一の前提）、
    ペア件数 pair_count、未レビュー件数 pending_count を集計。

    絞り込み GET パラメータ（仕様書 §11.5 + v9 セッション、たんたん判断）：
      - rank：4 値（exact_match / possible_high / possible_mid / possible_low）の複数選択
      - progress：「pending」「completed」のいずれか or 両方
      - user：'me' のとき person_a または person_b の primary_contact.created_by が
              ログインユーザーに一致する group のみ
      - searched=1：絞り込み実行済みフラグ。未指定（初回）はデフォルトで全 rank + pending のみ

    並び順：未レビュー優先（pending_count > 0 を先）→ rank 順（exact_match を先）→ group_id 順。

    group_id IS NULL のレコードは集約対象外（指示書 §1 確認事項、null は単発候補で
    本画面の対象外）。
    """

    template_name = "duplicates/duplicate_group_list.html"
    context_object_name = "groups"
    paginate_by = 20

    _VALID_RANKS = (
        DuplicateCandidate.Rank.EXACT_MATCH,
        DuplicateCandidate.Rank.POSSIBLE_HIGH,
        DuplicateCandidate.Rank.POSSIBLE_MID,
        DuplicateCandidate.Rank.POSSIBLE_LOW,
    )
    _VALID_PROGRESS = ("pending", "completed")

    def _is_searched(self):
        return self.request.GET.get("searched") == "1"

    def _get_selected_ranks(self):
        """初回は全 rank、検索後はチェックされた値のみ（不正値は捨てる）。"""
        if not self._is_searched():
            return list(self._VALID_RANKS)
        return [
            r
            for r in self.request.GET.getlist("rank")
            if r in self._VALID_RANKS
        ]

    def _get_selected_progress(self):
        """初回は pending のみ、検索後はチェックされた値のみ。"""
        if not self._is_searched():
            return ["pending"]
        return [
            p
            for p in self.request.GET.getlist("progress")
            if p in self._VALID_PROGRESS
        ]

    def _is_user_filter_on(self):
        return self.request.GET.get("user") == "me"

    def get_queryset(self):
        selected_ranks = self._get_selected_ranks()
        selected_progress = self._get_selected_progress()

        # rank または progress が空チェックなら結果 0 件（全チェック外しの自然な挙動）
        if not selected_ranks or not selected_progress:
            return DuplicateCandidate.objects.none()

        qs = DuplicateCandidate.objects.filter(
            group_id__isnull=False,
            rank__in=selected_ranks,
        )

        if self._is_user_filter_on():
            user = self.request.user
            qs = qs.filter(
                Q(person_a__primary_contact__created_by=user)
                | Q(person_b__primary_contact__created_by=user)
            )

        groups = qs.values("group_id").annotate(
            rank=Min("rank"),
            pair_count=Count("id"),
            pending_count=Count(
                "id",
                filter=Q(
                    review_status=DuplicateCandidate.ReviewStatus.PENDING
                ),
            ),
        )

        # progress フィルタ：annotate 後の has_pending で絞り込み
        if "pending" in selected_progress and "completed" not in selected_progress:
            groups = groups.filter(pending_count__gt=0)
        elif "completed" in selected_progress and "pending" not in selected_progress:
            groups = groups.filter(pending_count=0)

        rank_order = Case(
            When(rank=DuplicateCandidate.Rank.EXACT_MATCH, then=Value(0)),
            When(rank=DuplicateCandidate.Rank.POSSIBLE_HIGH, then=Value(1)),
            When(rank=DuplicateCandidate.Rank.POSSIBLE_MID, then=Value(2)),
            When(rank=DuplicateCandidate.Rank.POSSIBLE_LOW, then=Value(3)),
            default=Value(99),
            output_field=IntegerField(),
        )
        pending_order = Case(
            When(pending_count__gt=0, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
        return groups.annotate(
            rank_order=rank_order,
            pending_order=pending_order,
        ).order_by("pending_order", "rank_order", "group_id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 表示中の group_id 群について、各 group の代表 candidate を 1 件ずつ取得
        # （N+1 回避、テンプレートで person_a / person_b の氏名を表示するため）。
        object_list = context.get(self.context_object_name) or []
        visible_group_ids = [g["group_id"] for g in object_list]
        rep_qs = (
            DuplicateCandidate.objects.filter(group_id__in=visible_group_ids)
            .select_related(
                "person_a__primary_contact",
                "person_b__primary_contact",
            )
            .order_by("group_id", "id")
        )
        rep_by_group = {}
        for c in rep_qs:
            if c.group_id not in rep_by_group:
                rep_by_group[c.group_id] = c

        enriched = []
        for g in object_list:
            rep = rep_by_group.get(g["group_id"])
            enriched.append({**dict(g), "rep_candidate": rep})
        context["enriched_groups"] = enriched

        back = BackNavigator(self.request)
        back.push_current(
            "重複候補グループ一覧",
            ["rank", "progress", "user", "searched", "page"],
        )
        context["back"] = back

        context["valid_ranks"] = list(self._VALID_RANKS)
        context["selected_ranks"] = self._get_selected_ranks()
        context["selected_progress"] = self._get_selected_progress()
        context["user_filter_on"] = self._is_user_filter_on()
        context["searched"] = self._is_searched()

        context["active_app"] = "duplicates"
        context["active_menu"] = "duplicates:duplicate_group_list"
        return context


class DuplicateCandidateGroupDetailView(LoginRequiredMixin, View):
    """重複候補グループ詳細画面（16 番、仕様書 §11.3 / §11.5.1）。

    URL kwarg `group_id`（UUID）で受け取り、当該 group の候補を集計表示。
    pending / merged / different_person の件数を集計、`has_pending` でテンプレート側の
    レイアウト分岐（未レビューあり：候補ペア一覧 + レビューボタン / 完了：完了サマリー）を
    切り替える。invalidated は集計から除外。

    存在しない group_id は Http404（DuplicateCandidate が 1 件もない group_id）。
    BackNavigator は push_current を呼ばず、テンプレート側で {% back_url back %} を使う
    （contact_detail / card_detail と同パターン、A-2-追加 で確立）。
    """

    template_name = "duplicates/duplicate_group_detail.html"

    def get(self, request, group_id):
        candidates = DuplicateCandidate.objects.filter(
            group_id=group_id
        ).select_related("person_a", "person_b")
        if not candidates.exists():
            raise Http404("Duplicate candidate group not found.")

        pending_candidates = candidates.filter(
            review_status=DuplicateCandidate.ReviewStatus.PENDING
        )
        merged_candidates = candidates.filter(
            review_status=DuplicateCandidate.ReviewStatus.MERGED
        )
        different_person_candidates = candidates.filter(
            review_status=DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON
        )

        pending_count = pending_candidates.count()
        merged_count = merged_candidates.count()
        different_person_count = different_person_candidates.count()

        back = BackNavigator(request)
        context = {
            "group_id": group_id,
            "pending_count": pending_count,
            "merged_count": merged_count,
            "different_person_count": different_person_count,
            "has_pending": pending_count > 0,
            "pending_candidates": pending_candidates,
            "merged_candidates": merged_candidates,
            "different_person_candidates": different_person_candidates,
            "back": back,
            "active_app": "duplicates",
            "active_menu": "duplicates:duplicate_group_list",
        }
        return render(request, self.template_name, context)


class DuplicateCandidateGroupUpdateView(LoginRequiredMixin, View):
    """重複候補レビュー画面（17 番、仕様書 §11.3 / §11.5.2 / §11.5.5、D-4b）。

    GET：仕様書 §11.5.2 の 5 ステップで次のペアを表示する。
    POST：MergeForm 検証 → 3 サービス分岐（Mark_as_Different_Person /
    Execute_Merge_Only / Execute_Merge_with_Updates）。POST は D-4c で別途実装、
    本タスクでは GET のみ。

    セッションキー：reviewed_pair_ids:<group_id>（D-4b 論点1 案A、group_id ごと独立）。
    仕様書 §11.5.2 / §11.5.3 の shown_pair_ids は本実装で reviewed_pair_ids に
    リネーム（v9 セッション、たんたん判断、ストック #39 候補）。

    ペア表示順序：score 降順 → 同 score なら created_at 昇順（D-4b 論点2 案C）。
    MergeForm 初期化：candidate.person_a を surviving、candidate.person_b を merged
    でデフォルト初期化（D-4b 論点3 案A、仕様書 §11.5.5「デフォルト：左側」と整合）。
    """

    template_name = "duplicates/duplicate_group_review.html"
    _SESSION_KEY_PREFIX = "reviewed_pair_ids:"

    def _session_key(self, group_id):
        """[性質] 純関数（文字列組み立てのみ）"""
        return f"{self._SESSION_KEY_PREFIX}{group_id}"

    def get(self, request, group_id):
        session_key = self._session_key(group_id)
        reviewed_pair_ids = request.session.get(session_key, [])

        candidate = (
            DuplicateCandidate.objects.filter(
                group_id=group_id,
                review_status=DuplicateCandidate.ReviewStatus.PENDING,
            )
            .exclude(pk__in=reviewed_pair_ids)
            .order_by("-score", "created_at")
            .select_related(
                "person_a__primary_contact",
                "person_b__primary_contact",
            )
            .first()
        )

        if candidate is not None:
            reviewed_pair_ids.append(str(candidate.pk))
            request.session[session_key] = reviewed_pair_ids
            request.session.modified = True

            form = MergeForm(
                candidate=candidate,
                surviving_person=candidate.person_a,
                merged_person=candidate.person_b,
            )

            back = BackNavigator(request)
            context = {
                "candidate": candidate,
                "group_id": group_id,
                "form": form,
                "surviving_person": candidate.person_a,
                "merged_person": candidate.person_b,
                "back": back,
                "active_app": "duplicates",
                "active_menu": "duplicates:duplicate_group_list",
            }
            return render(request, self.template_name, context)

        if reviewed_pair_ids:
            del request.session[session_key]
            request.session.modified = True
            messages.success(
                request, "すべてのペアのレビューが完了しました"
            )
            return redirect(
                "duplicates:duplicate_group_detail", group_id=group_id
            )

        return redirect("duplicates:duplicate_group_list")
