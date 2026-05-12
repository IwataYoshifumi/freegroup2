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
from django.core.exceptions import ValidationError
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
from config.constants import DifferentPersonReason

from .forms import MergeForm
from .models import DuplicateCandidate
from .services.merge_executor import (
    Execute_Merge_Only,
    Mark_as_Different_Person,
)


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
    """重複候補レビュー画面（17 番、仕様書 §11.3 / §11.5.2 / §11.5.3 / §11.5.5）。

    GET（D-4b）：仕様書 §11.5.2 の 5 ステップで次のペアを表示する。
    POST（D-4c）：MergeForm 検証 → 2 サービス分岐（Mark_as_Different_Person /
    Execute_Merge_Only）→ reviewed_pair_ids 追加 → PRG リダイレクト。

    セッションキー：reviewed_pair_ids:<group_id>（D-4b 論点1 案A、group_id ごと独立）。
    仕様書 §11.5.2 / §11.5.3 の shown_pair_ids は本実装で reviewed_pair_ids に
    リネーム（v9 セッション、たんたん判断、ストック #39 候補）。

    reviewed_pair_ids への追加タイミング：仕様書 §11.5.2 では GET 時の追加を記載するが、
    本実装は POST 時のみ追加（D-4c、ユーザーの意思表明をもって「処理済み」確定。画面を
    見ただけで後戻り不可となる UX 問題の回避、新ストック候補で仕様書改訂予定）。

    ペア表示順序：score 降順 → 同 score なら created_at 昇順（D-4b 論点2 案C）。
    MergeForm 初期化（GET）：candidate.person_a を surviving、candidate.person_b を
    merged でデフォルト初期化（D-4b 論点3 案A、仕様書 §11.5.5「デフォルト：左側」）。

    サービス分岐（仕様書 §11.4.6、2026-05-10 設計変更で 3→2 分岐）：
      - DifferentPersonReason のいずれかが review_result に含まれる
        → Mark_as_Different_Person
      - DuplicateMergeReason のみ → Execute_Merge_Only
    Execute_Merge_with_Updates は廃止（マージ画面で値修正すると
    duplicate_checked_at=NULL になり DC 全 invalidated 化で再マージ不可となる問題のため）。
    フィールド修正は Contact 詳細画面 AJAX に分離（§10.6.4 ケース 4）。
    """

    template_name = "duplicates/duplicate_group_review.html"
    _SESSION_KEY_PREFIX = "reviewed_pair_ids:"
    _CONFLICT_MESSAGE = (
        "このペアは既に他の操作で処理されました。次のペアを表示します。"
    )

    def _session_key(self, group_id):
        """[性質] 純関数（文字列組み立てのみ）"""
        return f"{self._SESSION_KEY_PREFIX}{group_id}"

    def _render_review_page(
        self, request, group_id, candidate, form,
        surviving_person, merged_person,
    ):
        """[性質] 副作用あり（BackNavigator 初期化 + HttpResponse 返却）"""
        back = BackNavigator(request)
        context = {
            "candidate": candidate,
            "group_id": group_id,
            "form": form,
            "surviving_person": surviving_person,
            "merged_person": merged_person,
            "back": back,
            "active_app": "duplicates",
            "active_menu": "duplicates:duplicate_group_list",
        }
        return render(request, self.template_name, context)

    def _get_pending_candidate(self, group_id, pair_id):
        """指定 group_id・pair_id・pending な DC を取得（POST 検証用）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] group_id: UUID（URL から）/ pair_id: str | None（POST から、形式不正可）
        [出力] DuplicateCandidate | None（pair_id 不正 / 不存在 / 競合いずれも None）
        """
        if not pair_id:
            return None
        try:
            return (
                DuplicateCandidate.objects.filter(
                    pk=pair_id,
                    group_id=group_id,
                    review_status=DuplicateCandidate.ReviewStatus.PENDING,
                )
                .select_related(
                    "person_a__primary_contact",
                    "person_b__primary_contact",
                )
                .first()
            )
        except (ValueError, ValidationError):
            return None

    def _resolve_surviving_merged(self, candidate, surviving_person_choice):
        """surviving_person_choice から surviving / merged の Person タプルを返す。

        [性質] 純関数（DB 操作なし・副作用なし）
        [入力] candidate: DuplicateCandidate / surviving_person_choice: str
        [出力] (surviving: Person, merged: Person)

        D-4b 論点3 案A：デフォルト（"person_a" もしくは不正値）では
        surviving=candidate.person_a、merged=candidate.person_b（左側）。
        """
        if surviving_person_choice == "person_b":
            return candidate.person_b, candidate.person_a
        return candidate.person_a, candidate.person_b

    def _dispatch_service(
        self, candidate, surviving_person, merged_person, form, user,
    ):
        """review_result の値で 2 サービス関数のいずれかを呼ぶ（仕様書 §11.4.6）。

        [性質] 副作用あり（merge_executor.py の 2 関数のいずれかを呼び DB 更新）
        [入力] candidate / surviving_person / merged_person / form / user
        [出力] None
        [例外] ValidationError（サービス層が raise する場合あり、§11.4.6）
        """
        review_result = form.cleaned_data["review_result"]
        different_values = set(DifferentPersonReason.values)
        is_different = any(v in different_values for v in review_result)

        if is_different:
            Mark_as_Different_Person(candidate, form, user)
        else:
            Execute_Merge_Only(
                candidate, surviving_person, merged_person, form, user
            )

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
            form = MergeForm(
                candidate=candidate,
                surviving_person=candidate.person_a,
                merged_person=candidate.person_b,
            )
            return self._render_review_page(
                request, group_id, candidate, form,
                candidate.person_a, candidate.person_b,
            )

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

    def post(self, request, group_id):
        pair_id = request.POST.get("pair_id")
        candidate = self._get_pending_candidate(group_id, pair_id)
        if candidate is None:
            messages.error(request, self._CONFLICT_MESSAGE)
            return redirect(
                "duplicates:duplicate_group_review", group_id=group_id
            )

        surviving_person, merged_person = self._resolve_surviving_merged(
            candidate,
            request.POST.get("surviving_person_choice", "person_a"),
        )

        form = MergeForm(
            request.POST,
            candidate=candidate,
            surviving_person=surviving_person,
            merged_person=merged_person,
        )

        if not form.is_valid():
            return self._render_review_page(
                request, group_id, candidate, form,
                surviving_person, merged_person,
            )

        try:
            self._dispatch_service(
                candidate, surviving_person, merged_person,
                form, request.user,
            )
        except ValidationError as e:
            form.add_error(None, e)
            return self._render_review_page(
                request, group_id, candidate, form,
                surviving_person, merged_person,
            )

        session_key = self._session_key(group_id)
        reviewed_pair_ids = request.session.get(session_key, [])
        pair_id_str = str(candidate.pk)
        if pair_id_str not in reviewed_pair_ids:
            reviewed_pair_ids.append(pair_id_str)
            request.session[session_key] = reviewed_pair_ids
            request.session.modified = True

        return redirect(
            "duplicates:duplicate_group_review", group_id=group_id
        )
