"""duplicates アプリの View 層（仕様書 §11.3 / §11.5）。

DuplicateCandidateGroupListView：重複候補グループ一覧（URL 15 番）。
  group_id 単位で集約表示、rank / progress / user で絞り込み。
DuplicateCandidateGroupDetailView：重複候補グループ詳細（URL 16 番）。
  pending / merged / different_person の集計と表示切替（§11.5.1）。
DuplicateCandidateGroupUpdateView：重複候補レビュー画面（URL 17 番、GET / POST）。
  仕様書 §11.5.2 の 5 ステップで次ペアを表示、POST で 3 値分岐サービス。
PersonMergeLogListView：マージログ一覧（URL 19 番、D-4f-1）。
  status / user で絞り込み、-executed_at 降順、20 件ページネーション。
PersonMergeLogDetailView：マージログ詳細（URL 20 番、D-4f-1）。
  実行情報 + status + 復元プレビュー（get_undo_preview）+ 復元ボタン（D-4f-2 で実装）。
PersonMergeLogConfirmUndoView：マージ復元確認 + 実行（URL 21 番、D-4f-2）。
  GET で確認画面、POST で MergeUndoForm 検証 → Execute_Merge_Undo 実行。
  is_undoable 競合検出と messages による UX フィードバック付き。
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
from config.constants import DifferentPersonReason, DuplicateMergeReason
from contacts.models import ContactSns
from contacts.services.normalization import format_postal_by_country

from .forms import MergeForm, MergeUndoForm
from .models import DuplicateCandidate, PersonMergeLog
from .services.merge_executor import (
    Execute_Merge_Only,
    Execute_Merge_Undo,
    Mark_as_Different_Person,
)


# 17 番マージレビュー画面の Contact フィールド表示用ラベル（仕様書 §11.5.5、D-4d）。
# Contact.UPDATABLE_FIELDS（v1.6.1 で 31 個に整理：v1.6.0 で 37 に拡張後、
# 個別 SNS 5 件を ContactSns 別テーブル化、Phase E で address を除外）のすべてに対応する
# エントリを持つ。
# 日本語ラベルは本編仕様書 別表 A.5 を正本に、既存ラベルの簡潔化方針（個人系は無修飾、
# 会社系は「会社」プレフィックス）に合わせて UI 表記。
# ContactSns の比較表示（sns_type 別グルーピング）は Phase F2 で _build_sns_comparison に
# 集約、§11.5.7。
FIELD_LABEL_JA = {
    "full_name": "氏名",
    "last_name": "姓",
    "first_name": "名",
    "salutation_name": "敬称付き氏名",
    "other_name_parts": "他の名前部分",
    "name_order": "氏名の順序",
    "display_name": "表示名",
    "phonetic_name": "読みがな",
    "alias_name": "通称・別名",
    "organization": "会社",
    "legal_entity_type": "法人格",
    "legal_entity_type_position": "法人格の位置",
    "branch": "支店・店舗",
    "department": "部署",
    "title": "役職",
    "qualification": "資格",
    "catchphrase": "キャッチコピー",
    "email": "メール",
    "mobile_phone": "携帯",
    "personal_phone": "電話",
    "personal_fax": "FAX",
    "org_phone": "会社電話",
    "org_fax": "会社 FAX",
    "website": "ウェブサイト",
    "postal_code": "郵便番号",
    "country": "国",
    "region": "中間行政区画",
    "city": "市区町村",
    "rest_of_address": "番地・建物名",
    "notes": "メモ",
    "lang": "言語",
}


# 17 番マージレビュー画面のフィールドグルーピング（仕様書 §11.5.5 /
# v1.6.1 で 31 件に整理：v1.6.0 で 37 に拡張後、個別 SNS 5 件を ContactSns 別テーブル化、
# Phase E で address を UPDATABLE_FIELDS から除外）。
# 既存「SNS」グループは廃止し 5 グループ（氏名 / 所属 / 連絡先 / 住所 / その他）構成へ。
# ContactSns の比較表示は Phase F2 で _build_sns_comparison が sns_type 別に生成する独立
# ブロックとして扱う（FIELD_GROUPS には含めない）。
# test_field_groups_total_fields_match_updatable_fields が Contact.UPDATABLE_FIELDS との
# 集合一致を検証している。
FIELD_GROUPS = (
    (
        "氏名",
        (
            "full_name",
            "last_name",
            "first_name",
            "salutation_name",
            "other_name_parts",
            "name_order",
            "display_name",
            "phonetic_name",
            "alias_name",
        ),
    ),
    (
        "所属",
        (
            "organization",
            "legal_entity_type",
            "legal_entity_type_position",
            "branch",
            "department",
            "title",
            "qualification",
            "catchphrase",
        ),
    ),
    (
        "連絡先",
        (
            "email",
            "mobile_phone",
            "personal_phone",
            "personal_fax",
            "org_phone",
            "org_fax",
            "website",
        ),
    ),
    (
        "住所",
        (
            "postal_code",
            "country",
            "region",
            "city",
            "rest_of_address",
        ),
    ),
    ("その他", ("notes", "lang")),
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

    def _build_field_groups(
        self, contact, peer_contact, hidden_field_names=()
    ):
        """両側比較で除外 / 修飾を反映した表示用フィールドグループ構造を返す。

        [性質] 純関数（contact / peer_contact から getattr のみ、DB 操作なし・副作用なし）
        [入力] contact: 自側 Contact（値を表示する側）
               peer_contact: 対面側 Contact（両側空判定 / is_diff 算出用）
               hidden_field_names: Iterable[str]（MergeForm.hidden_name_fields() の
                 結果。指定された field_name は表示から除外、D-4d-1 第 3 弾 §2-3）
        [出力] list[dict]（各要素：group_name / fields。fields は
               {field_name, label, value, is_diff} の list）

        FIELD_GROUPS の順序・グループ名・フィールド構成を踏襲しつつ、以下を適用：
          - hidden_field_names に含まれるフィールドは除外（§2-3）
          - 自側・対面側の値がともに空のフィールドは除外（§2-1）
          - 各フィールドに is_diff フラグを付与（自側 != 対面側、D-4d-1 第 3 弾 §2-1）
          - フィールドが全消えしたグループはグループ自体も除外（見出しだけ残るのを防止）
        """
        hidden_set = set(hidden_field_names)
        groups = []
        for group_name, field_names in FIELD_GROUPS:
            fields = []
            for fname in field_names:
                if fname in hidden_set:
                    continue
                self_value = getattr(contact, fname)
                peer_value = getattr(peer_contact, fname)
                if not self_value and not peer_value:
                    continue
                # postal_code は表示のみ国別整形（Phase D2 話2）。is_diff は生値比較のまま。
                display_value = self_value
                if fname == "postal_code":
                    display_value = format_postal_by_country(
                        self_value, contact.country
                    )
                fields.append(
                    {
                        "field_name": fname,
                        "label": FIELD_LABEL_JA[fname],
                        "value": display_value,
                        "is_diff": self_value != peer_value,
                    }
                )
            if fields:
                groups.append({"group_name": group_name, "fields": fields})
        return groups

    def _build_sns_comparison(self, contact, peer_contact):
        """ContactSns の sns_type 別比較データを返す（Phase F2 / 仕様書 §11.5.7）。

        [性質] 純関数（contact / peer_contact から sns_accounts を読むだけ、
               DB 操作なし・副作用なし。N+1 防止のため呼び出し側で prefetch_related
               'sns_accounts' しておくこと）
        [入力] contact: 自側 Contact（値を表示する側）
               peer_contact: 対面側 Contact（is_diff 算出用）
        [出力] list[dict]（各要素：{sns_type, sns_type_label, items}。
               items は {sns_id, is_diff} の list）

        sns_type の並び順は ContactSns.SnsType.choices 定義順
        （twitter / linkedin / facebook / instagram / github / blog / youtube / line）。
        両側で 1 件もない sns_type は除外（仕様書 §11.5.7 の「両側空フィールド非表示」
        原則を ContactSns に適用）。片側のみ持つ sns_type は両側に sns_type ヘッダを残し、
        空側は items=[] のグループとして返す（テンプレで「（なし）」を表示）。
        is_diff: self 側の sns_id が peer の同 sns_type に同 sns_id で存在しない場合 True。
        """
        self_by_type = {}
        for sa in contact.sns_accounts.all():
            self_by_type.setdefault(sa.sns_type, []).append(sa.sns_id)
        peer_by_type = {}
        for sa in peer_contact.sns_accounts.all():
            peer_by_type.setdefault(sa.sns_type, []).append(sa.sns_id)

        groups = []
        for sns_type, label in ContactSns.SnsType.choices:
            self_ids = self_by_type.get(sns_type, [])
            peer_ids = peer_by_type.get(sns_type, [])
            if not self_ids and not peer_ids:
                continue
            peer_id_set = set(peer_ids)
            items = [
                {"sns_id": sid, "is_diff": sid not in peer_id_set}
                for sid in self_ids
            ]
            groups.append({
                "sns_type": sns_type,
                "sns_type_label": label,
                "items": items,
            })
        return groups

    def _render_review_page(
        self, request, group_id, candidate, form,
        surviving_person, merged_person,
    ):
        """[性質] 副作用あり（BackNavigator 初期化 + HttpResponse 返却）"""
        back = BackNavigator(request)
        surviving_primary = surviving_person.primary_contact
        merged_primary = merged_person.primary_contact
        surviving_bc = surviving_primary.business_card
        merged_bc = merged_primary.business_card
        context = {
            "candidate": candidate,
            "group_id": group_id,
            "form": form,
            "surviving_person": surviving_person,
            "merged_person": merged_person,
            "surviving_card_image_url": (
                surviving_bc.get_card_image_url() if surviving_bc else ""
            ),
            "merged_card_image_url": (
                merged_bc.get_card_image_url() if merged_bc else ""
            ),
            "surviving_field_groups": self._build_field_groups(
                surviving_primary,
                merged_primary,
                form.hidden_name_fields(),
            ),
            "merged_field_groups": self._build_field_groups(
                merged_primary,
                surviving_primary,
                form.hidden_name_fields(),
            ),
            "surviving_sns_groups": self._build_sns_comparison(
                surviving_primary, merged_primary
            ),
            "merged_sns_groups": self._build_sns_comparison(
                merged_primary, surviving_primary
            ),
            "surviving_confidences": (
                surviving_primary.get_field_confidences()
            ),
            "merged_confidences": merged_primary.get_field_confidences(),
            "decision_choices": [
                ("merged", "同一人物"),
                ("additional_role", "同一人物（別肩書追加）"),
                ("different", "別人"),
            ],
            "merged_reason_choices": [
                (v, lbl)
                for v, lbl in DuplicateMergeReason.choices
                if v != DuplicateMergeReason.ADDITIONAL_ROLE.value
            ],
            "different_reason_choices": DifferentPersonReason.choices,
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
                .prefetch_related(
                    "person_a__primary_contact__sns_accounts",
                    "person_b__primary_contact__sns_accounts",
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
            .prefetch_related(
                "person_a__primary_contact__sns_accounts",
                "person_b__primary_contact__sns_accounts",
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


class PersonMergeLogListView(LoginRequiredMixin, ListView):
    """マージログ一覧画面（19 番、仕様書 §11.3 / D-4f-1）。

    PersonMergeLog を全件対象に -executed_at 降順で表示。絞り込み GET パラメータ：
      - status：3 値（undoable / undone / locked）の複数選択
      - user：'me' のとき executed_by=ログインユーザーに絞る
      - searched=1：絞り込み実行済みフラグ。未指定（初回）は status='undoable' のみ
        + user 絞り込みなし

    並び順：-executed_at（最新マージが上）。
    N+1 対策：surviving / merged の primary_contact、executed_by / undone_by を
    select_related。
    """

    template_name = "duplicates/merge_log_list.html"
    context_object_name = "merge_logs"
    paginate_by = 20

    _VALID_STATUSES = (
        PersonMergeLog.Status.UNDOABLE,
        PersonMergeLog.Status.UNDONE,
        PersonMergeLog.Status.LOCKED,
    )

    def _is_searched(self):
        return self.request.GET.get("searched") == "1"

    def _get_selected_statuses(self):
        """初回は undoable のみ、検索後はチェック値のみ（不正値は捨てる）。"""
        if not self._is_searched():
            return [PersonMergeLog.Status.UNDOABLE]
        return [
            s
            for s in self.request.GET.getlist("status")
            if s in self._VALID_STATUSES
        ]

    def _is_user_filter_on(self):
        return self.request.GET.get("user") == "me"

    def get_queryset(self):
        selected_statuses = self._get_selected_statuses()
        if not selected_statuses:
            return PersonMergeLog.objects.none()

        qs = (
            PersonMergeLog.objects.filter(status__in=selected_statuses)
            .select_related(
                "surviving_person__primary_contact",
                "merged_person__primary_contact",
                "executed_by",
                "undone_by",
            )
            .order_by("-executed_at")
        )

        if self._is_user_filter_on():
            qs = qs.filter(executed_by=self.request.user)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        back.push_current(
            "マージログ一覧",
            ["status", "user", "searched", "page"],
        )
        context["back"] = back
        context["valid_statuses"] = list(self._VALID_STATUSES)
        context["selected_statuses"] = self._get_selected_statuses()
        context["user_filter_on"] = self._is_user_filter_on()
        context["searched"] = self._is_searched()
        context["active_app"] = "duplicates"
        context["active_menu"] = "duplicates:merge_log_list"
        return context


class PersonMergeLogDetailView(LoginRequiredMixin, View):
    """マージログ詳細画面（20 番、仕様書 §11.3 / D-4f-1）。

    URL kwarg `pk`（UUID）で受け取り、PersonMergeLog を 1 件取得。存在しない pk は
    Http404。

    表示内容：実行情報 / status / surviving / merged / duplicate_candidate /
    undone 情報 / note / 復元プレビュー（get_undo_preview の dict）。

    復元ボタン：is_undoable=True のときテンプレ側で「準備中」グレーアウト表示
    （21 番 PersonMergeLogConfirmUndoView は D-4f-2 で別途実装）。
    """

    template_name = "duplicates/merge_log_detail.html"

    def get(self, request, pk):
        try:
            merge_log = (
                PersonMergeLog.objects.select_related(
                    "surviving_person__primary_contact",
                    "merged_person__primary_contact",
                    "duplicate_candidate",
                    "executed_by",
                    "undone_by",
                )
                .get(pk=pk)
            )
        except PersonMergeLog.DoesNotExist:
            raise Http404("PersonMergeLog not found.")

        back = BackNavigator(request)
        context = {
            "merge_log": merge_log,
            "surviving_person": merge_log.surviving_person,
            "merged_person": merge_log.merged_person,
            "is_undoable": merge_log.is_undoable(),
            "undo_preview": merge_log.get_undo_preview(),
            "back": back,
            "active_app": "duplicates",
            "active_menu": "duplicates:merge_log_list",
        }
        return render(request, self.template_name, context)


class PersonMergeLogConfirmUndoView(LoginRequiredMixin, View):
    """マージ復元の確認 + 実行画面（21 番、仕様書 §11.3 / D-4f-2）。

    GET：merge_log を取得 → is_undoable() False なら messages.error + 20 番リダイレクト
    （競合検出）。True なら MergeUndoForm を空で初期化して confirm-undo 画面を render。

    POST：merge_log を取得 → is_undoable() を再チェック（POST 中の競合保険）→
    MergeUndoForm 検証 → invalid なら画面再 render → valid なら Execute_Merge_Undo を
    呼ぶ。ValidationError キャッチ時は messages.error + 20 番リダイレクト。成功時は
    復元結果サマリの messages.success + 20 番リダイレクト。

    BackNavigator：push_current は呼ばない。20 番からの遷移時に append_back_url で
    back スタックが引き継がれる（merge_log_detail.html 側で対応）。
    """

    template_name = "duplicates/merge_log_confirm_undo.html"

    def _get_merge_log(self, pk):
        """[性質] 準関数（DB 読み取りのみ）"""
        try:
            return (
                PersonMergeLog.objects.select_related(
                    "surviving_person__primary_contact",
                    "merged_person__primary_contact",
                    "executed_by",
                )
                .get(pk=pk)
            )
        except PersonMergeLog.DoesNotExist:
            raise Http404("PersonMergeLog not found.")

    def _redirect_with_conflict(self, request, merge_log):
        """[性質] 副作用あり（messages + redirect）"""
        messages.error(
            request,
            f"このマージは復元できません（現在の状態：{merge_log.get_status_display()}）。",
        )
        return redirect("duplicates:merge_log_detail", pk=merge_log.pk)

    def _render_confirm_page(self, request, merge_log, form):
        """[性質] 副作用あり（BackNavigator 初期化 + HttpResponse 返却）"""
        back = BackNavigator(request)
        context = {
            "merge_log": merge_log,
            "surviving_person": merge_log.surviving_person,
            "merged_person": merge_log.merged_person,
            "undo_preview": merge_log.get_undo_preview(),
            "form": form,
            "back": back,
            "active_app": "duplicates",
            "active_menu": "duplicates:merge_log_list",
        }
        return render(request, self.template_name, context)

    def get(self, request, pk):
        merge_log = self._get_merge_log(pk)
        if not merge_log.is_undoable():
            return self._redirect_with_conflict(request, merge_log)
        form = MergeUndoForm()
        return self._render_confirm_page(request, merge_log, form)

    def post(self, request, pk):
        merge_log = self._get_merge_log(pk)
        if not merge_log.is_undoable():
            return self._redirect_with_conflict(request, merge_log)

        form = MergeUndoForm(request.POST)
        if not form.is_valid():
            return self._render_confirm_page(request, merge_log, form)

        # 復元実行前に preview の Contact 件数を取得（成功 message に使用）。
        # merged.primary_contact はマージ完了直後 None なので、復元すると ACTIVE に
        # 戻る Contact（contacts_to_restore のうち previous_status='primary' の 1 件）の
        # full_name を表示用に取得する。
        preview = merge_log.get_undo_preview()
        contacts_to_restore_count = preview["contacts_to_restore"].count()
        restored_primary = preview["contacts_to_restore"].filter(
            previous_status="primary"
        ).first()
        merged_person_name = (
            restored_primary.full_name if restored_primary else "(氏名なし)"
        )

        try:
            Execute_Merge_Undo(merge_log, form, request.user)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect("duplicates:merge_log_detail", pk=merge_log.pk)

        messages.success(
            request,
            (
                f"マージを復元しました。{merged_person_name} が active 状態に戻り、"
                f"{contacts_to_restore_count} 件の Contact が戻されました。"
            ),
        )
        return redirect("duplicates:merge_log_detail", pk=merge_log.pk)
