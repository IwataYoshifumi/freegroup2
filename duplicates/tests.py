"""duplicates アプリのテスト（View 層 / Form 層）。

DuplicateCandidateGroupListViewTests：15 番（一覧、絞り込み、D-4e）。
DuplicateCandidateGroupDetailViewTests：16 番（詳細、表示切替、D-4e）。
MergeFormInitTests / MergeFormCleanTests / MergeFormHelpersTests：
    17 番マージ画面用 Form（仕様書 §11.6.2 / §11.7.3、D-4a）。
DuplicateCandidateGroupUpdateViewGetTests / SessionTests / RedirectTests /
MergeFormInitTests：17 番レビュー画面 GET（仕様書 §11.5.2、D-4b）。
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contacts.models import Contact, ContactFieldConfidence, ContactSns
from actionlogs.models import ActionLog
from duplicates.forms import MergeForm, MergeUndoForm
from duplicates.models import DuplicateCandidate, PersonMergeLog
from duplicates.services.duplicate_score import (
    build_high_fields_map,
    get_matched_fields,
)
from persons.models import Person


User = get_user_model()


class _DuplicatesTestBase(TestCase):
    """重複候補テスト共通のヘルパー（Person + Contact + DuplicateCandidate の作成）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="dup_test_user", password="dummy"
        )
        self.other_user = User.objects.create_user(
            username="dup_other_user", password="dummy"
        )
        # Phase 7 段2-A：マージ系 View は persons.merge_person / undo_merge を要求する
        # （URL一覧表 rev20 No.15-17 / No.21 ★1）。既存テストの正常系は「認可済みの
        # マージ担当者」が操作する前提なので、共通ユーザーに両権限を付与する。
        from django.contrib.auth.models import Permission

        self.user.user_permissions.add(
            Permission.objects.get(
                codename="merge_person", content_type__app_label="persons"
            ),
            Permission.objects.get(
                codename="undo_merge", content_type__app_label="persons"
            ),
        )

        self.client = Client()
        self.client.force_login(self.user)

    def _make_person_with_primary(self, full_name, created_by=None):
        """active Person + primary Contact のセットを作る。"""
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            created_by=created_by,
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person, contact

    def _make_candidate(
        self,
        person_a,
        person_b,
        *,
        group_id=None,
        rank=None,
        review_status=None,
        score=120,
    ):
        """DuplicateCandidate を作る（person_a / person_b は ID 順に正規化、仕様書 §4.7）。"""
        if person_a.id > person_b.id:
            person_a, person_b = person_b, person_a
        return DuplicateCandidate.objects.create(
            person_a=person_a,
            person_b=person_b,
            score=score,
            rank=rank or DuplicateCandidate.Rank.POSSIBLE_MID,
            review_status=review_status
            or DuplicateCandidate.ReviewStatus.PENDING,
            group_id=group_id,
            review_result=[],
        )


class DuplicateCandidateGroupListViewTests(_DuplicatesTestBase):
    """DuplicateCandidateGroupListView（15 番）の単体テスト。"""

    def setUp(self):
        super().setUp()
        self.url = reverse("duplicates:duplicate_group_list")

    def test_get_returns_200(self):
        """認証あり GET → 200。"""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_redirects(self):
        """LoginRequiredMixin → 未ログインは 302。"""
        c = Client()
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_default_shows_pending_only(self):
        """初回（searched なし）は pending のみ表示、completed（merged 等）は非表示。"""
        p1, _ = self._make_person_with_primary("X")
        p2, _ = self._make_person_with_primary("Y")
        p3, _ = self._make_person_with_primary("Z")
        p4, _ = self._make_person_with_primary("W")

        gid_pending = uuid.uuid4()
        gid_completed = uuid.uuid4()

        self._make_candidate(
            p1, p2,
            group_id=gid_pending,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        self._make_candidate(
            p3, p4,
            group_id=gid_completed,
            review_status=DuplicateCandidate.ReviewStatus.MERGED,
        )

        resp = self.client.get(self.url)
        group_ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertIn(gid_pending, group_ids)
        self.assertNotIn(gid_completed, group_ids)

    def test_rank_filter(self):
        """rank=exact_match で絞ると exact_match の group のみ。"""
        p1, _ = self._make_person_with_primary("X")
        p2, _ = self._make_person_with_primary("Y")
        p3, _ = self._make_person_with_primary("Z")
        p4, _ = self._make_person_with_primary("W")

        gid_exact = uuid.uuid4()
        gid_high = uuid.uuid4()

        self._make_candidate(
            p1, p2,
            group_id=gid_exact,
            rank=DuplicateCandidate.Rank.EXACT_MATCH,
        )
        self._make_candidate(
            p3, p4,
            group_id=gid_high,
            rank=DuplicateCandidate.Rank.POSSIBLE_HIGH,
        )

        resp = self.client.get(
            self.url,
            {
                "searched": "1",
                "rank": "exact_match",
                "progress": "pending",
            },
        )
        group_ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertIn(gid_exact, group_ids)
        self.assertNotIn(gid_high, group_ids)

    def test_progress_completed_filter(self):
        """progress=completed で pending_count=0 の group のみ表示。"""
        p1, _ = self._make_person_with_primary("X")
        p2, _ = self._make_person_with_primary("Y")
        p3, _ = self._make_person_with_primary("Z")
        p4, _ = self._make_person_with_primary("W")

        gid_pending = uuid.uuid4()
        gid_merged = uuid.uuid4()

        self._make_candidate(
            p1, p2,
            group_id=gid_pending,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        self._make_candidate(
            p3, p4,
            group_id=gid_merged,
            review_status=DuplicateCandidate.ReviewStatus.MERGED,
        )

        resp = self.client.get(
            self.url,
            {
                "searched": "1",
                "rank": [
                    "exact_match",
                    "possible_high",
                    "possible_mid",
                    "possible_low",
                ],
                "progress": "completed",
            },
        )
        group_ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertNotIn(gid_pending, group_ids)
        self.assertIn(gid_merged, group_ids)

    def test_user_filter(self):
        """user=me で person_a / person_b の primary_contact.created_by が
        ログインユーザーの group のみ。"""
        p_mine, _ = self._make_person_with_primary("Mine", created_by=self.user)
        p_their, _ = self._make_person_with_primary(
            "Their", created_by=self.other_user
        )
        p_other1, _ = self._make_person_with_primary(
            "Other1", created_by=self.other_user
        )
        p_other2, _ = self._make_person_with_primary(
            "Other2", created_by=self.other_user
        )

        gid_mine = uuid.uuid4()
        gid_unrelated = uuid.uuid4()

        self._make_candidate(p_mine, p_their, group_id=gid_mine)
        self._make_candidate(p_other1, p_other2, group_id=gid_unrelated)

        resp = self.client.get(
            self.url,
            {
                "searched": "1",
                "rank": "possible_mid",
                "progress": "pending",
                "user": "me",
            },
        )
        group_ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertIn(gid_mine, group_ids)
        self.assertNotIn(gid_unrelated, group_ids)

    def test_null_group_id_excluded(self):
        """group_id=NULL の candidate は集約対象外。"""
        p1, _ = self._make_person_with_primary("X")
        p2, _ = self._make_person_with_primary("Y")
        gid_valid = uuid.uuid4()

        self._make_candidate(p1, p2, group_id=gid_valid)

        p3, _ = self._make_person_with_primary("Z")
        p4, _ = self._make_person_with_primary("W")
        self._make_candidate(p3, p4, group_id=None)

        resp = self.client.get(self.url)
        group_ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertIn(gid_valid, group_ids)
        self.assertNotIn(None, group_ids)

    def test_pagination_51_records_split_to_2_pages(self):
        """既定表示件数 50（他一覧と整合）。51 件で 2 ページ目に分かれる。"""
        for i in range(51):
            p1, _ = self._make_person_with_primary(f"a-{i:02d}")
            p2, _ = self._make_person_with_primary(f"b-{i:02d}")
            self._make_candidate(p1, p2, group_id=uuid.uuid4())

        resp = self.client.get(self.url)
        self.assertTrue(resp.context["is_paginated"])
        self.assertEqual(len(list(resp.context["enriched_groups"])), 50)

        resp2 = self.client.get(self.url, {"page": "2"})
        self.assertEqual(len(list(resp2.context["enriched_groups"])), 1)

    def test_per_page_param_changes_page_size(self):
        """per_page=100 で 51 件が 1 ページに収まる。不正値は既定 50 にフォールバック。"""
        for i in range(51):
            p1, _ = self._make_person_with_primary(f"c-{i:02d}")
            p2, _ = self._make_person_with_primary(f"d-{i:02d}")
            self._make_candidate(p1, p2, group_id=uuid.uuid4())

        resp = self.client.get(self.url, {"per_page": "100"})
        self.assertFalse(resp.context["is_paginated"])
        self.assertEqual(len(list(resp.context["enriched_groups"])), 51)
        self.assertEqual(resp.context["per_page"], 100)

        # 不正値 → 既定 50（is_paginated True）
        resp2 = self.client.get(self.url, {"per_page": "37"})
        self.assertEqual(resp2.context["per_page"], 50)
        self.assertTrue(resp2.context["is_paginated"])

    def _make_ranked_group(self, label, rank):
        p1, _ = self._make_person_with_primary(f"{label}-a")
        p2, _ = self._make_person_with_primary(f"{label}-b")
        gid = uuid.uuid4()
        self._make_candidate(p1, p2, group_id=gid, rank=rank)
        return gid

    def test_sort_default_is_rank_priority(self):
        """既定ソートは rank 優先（完全一致→高→中→低）。"""
        g_low = self._make_ranked_group("low", DuplicateCandidate.Rank.POSSIBLE_LOW)
        g_exact = self._make_ranked_group("exact", DuplicateCandidate.Rank.EXACT_MATCH)
        g_mid = self._make_ranked_group("mid", DuplicateCandidate.Rank.POSSIBLE_MID)
        g_high = self._make_ranked_group("high", DuplicateCandidate.Rank.POSSIBLE_HIGH)

        resp = self.client.get(
            self.url,
            {"searched": "1", "rank": ["exact_match", "possible_high", "possible_mid", "possible_low"],
             "progress": "pending"},
        )
        ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertEqual(ids, [g_exact, g_high, g_mid, g_low])

    def test_sort_rank_desc(self):
        """?sort=-rank で確信度の低い順（低→中→高→完全一致）になる。"""
        g_low = self._make_ranked_group("low", DuplicateCandidate.Rank.POSSIBLE_LOW)
        g_exact = self._make_ranked_group("exact", DuplicateCandidate.Rank.EXACT_MATCH)
        g_mid = self._make_ranked_group("mid", DuplicateCandidate.Rank.POSSIBLE_MID)
        g_high = self._make_ranked_group("high", DuplicateCandidate.Rank.POSSIBLE_HIGH)

        resp = self.client.get(
            self.url,
            {"searched": "1", "rank": ["exact_match", "possible_high", "possible_mid", "possible_low"],
             "progress": "pending", "sort": "-rank"},
        )
        ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertEqual(ids, [g_low, g_mid, g_high, g_exact])

    def test_sort_pair_count_desc(self):
        """?sort=-pair_count でペア件数の多いグループが先。"""
        # group A：1 ペア
        pa1, _ = self._make_person_with_primary("pa1")
        pa2, _ = self._make_person_with_primary("pa2")
        g_one = uuid.uuid4()
        self._make_candidate(pa1, pa2, group_id=g_one)
        # group B：2 ペア（同 group_id）
        pb1, _ = self._make_person_with_primary("pb1")
        pb2, _ = self._make_person_with_primary("pb2")
        pb3, _ = self._make_person_with_primary("pb3")
        g_two = uuid.uuid4()
        self._make_candidate(pb1, pb2, group_id=g_two)
        self._make_candidate(pb1, pb3, group_id=g_two)

        resp = self.client.get(
            self.url,
            {"searched": "1", "rank": ["exact_match", "possible_high", "possible_mid", "possible_low"],
             "progress": "pending", "sort": "-pair_count"},
        )
        ids = [g["group_id"] for g in resp.context["enriched_groups"]]
        self.assertEqual(ids[0], g_two)
        self.assertIn(g_one, ids)

    def test_row_button_review_when_pending_detail_when_done(self):
        """未レビュー残あり→レビューURL、全件処理済み→詳細URL。"""
        # pending group
        p1, _ = self._make_person_with_primary("rb-p1")
        p2, _ = self._make_person_with_primary("rb-p2")
        g_pending = uuid.uuid4()
        self._make_candidate(p1, p2, group_id=g_pending,
                             review_status=DuplicateCandidate.ReviewStatus.PENDING)
        # done group（全件 merged）
        p3, _ = self._make_person_with_primary("rb-p3")
        p4, _ = self._make_person_with_primary("rb-p4")
        g_done = uuid.uuid4()
        self._make_candidate(p3, p4, group_id=g_done,
                             review_status=DuplicateCandidate.ReviewStatus.MERGED)

        resp = self.client.get(
            self.url,
            {"searched": "1", "rank": ["exact_match", "possible_high", "possible_mid", "possible_low"],
             "progress": ["pending", "completed"]},
        )
        review_url = reverse("duplicates:duplicate_group_review", kwargs={"group_id": g_pending})
        detail_url = reverse("duplicates:duplicate_group_detail", kwargs={"group_id": g_done})
        self.assertContains(resp, review_url)
        self.assertContains(resp, detail_url)

    # ------------------------------------------------------------------
    # v1.7：代表ペア列 → 主役 Person 名 + rank + 一致フィールドバッジ
    # ------------------------------------------------------------------

    _ALL_RANKS_QUERY = {
        "searched": "1",
        "rank": ["exact_match", "possible_high", "possible_mid", "possible_low"],
    }

    def test_lead_name_active_side_pending(self):
        """pending（両 active）で主役名が出て、ペア表記「↔」と「(氏名なし)」が消える。"""
        p1, _ = self._make_person_with_primary("一致太郎")
        p2, _ = self._make_person_with_primary("一致太郎")
        gid = uuid.uuid4()
        self._make_candidate(
            p1, p2, group_id=gid,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        resp = self.client.get(self.url)  # 既定 = pending のみ
        self.assertContains(resp, "一致太郎")
        self.assertNotContains(resp, "(氏名なし)")
        self.assertNotContains(resp, "↔")

    def test_lead_name_surviving_side_when_completed(self):
        """完了（merged）で active=surviving 側の名前が出て「(氏名なし)」が出ない。"""
        surviving, _ = self._make_person_with_primary("生存花子")
        merged, merged_pc = self._make_person_with_primary("吸収次郎")
        merged_pc.person = surviving
        merged_pc.previous_person = merged
        merged_pc.previous_status = Contact.Status.PRIMARY
        merged_pc.status = Contact.Status.INACTIVE
        merged_pc.save(update_fields=[
            "person", "previous_person", "previous_status",
            "status", "updated_at",
        ])
        merged.primary_contact = None
        merged.status = Person.Status.MERGED
        merged.merged_into = surviving
        merged.save(update_fields=[
            "primary_contact", "status", "merged_into", "updated_at",
        ])
        gid = uuid.uuid4()
        self._make_candidate(
            surviving, merged, group_id=gid,
            review_status=DuplicateCandidate.ReviewStatus.MERGED,
        )
        resp = self.client.get(
            self.url, {**self._ALL_RANKS_QUERY, "progress": ["completed"]}
        )
        self.assertContains(resp, "生存花子")
        self.assertNotContains(resp, "(氏名なし)")

    def test_matched_field_badges_japanese(self):
        """一致フィールドが日本語バッジ（氏名・会社・部署）で出る。"""
        p1, c1 = self._make_person_with_primary("同名")
        p2, c2 = self._make_person_with_primary("同名")
        for c in (c1, c2):
            c.organization = "テスト商事"
            c.department = "営業部"
            c.save(update_fields=["organization", "department", "updated_at"])
        gid = uuid.uuid4()
        self._make_candidate(
            p1, p2, group_id=gid,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        resp = self.client.get(self.url)
        self.assertContains(resp, "氏名")
        self.assertContains(resp, "会社")
        self.assertContains(resp, "部署")

    def test_rank_uses_rep_candidate_rank(self):
        """rank 列は group 集計値ではなく rep_candidate.rank で表示される。"""
        p1, _ = self._make_person_with_primary("r1")
        p2, _ = self._make_person_with_primary("r2")
        gid = uuid.uuid4()
        self._make_candidate(
            p1, p2, group_id=gid,
            rank=DuplicateCandidate.Rank.POSSIBLE_LOW,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        resp = self.client.get(self.url)
        g = resp.context["enriched_groups"][0]
        self.assertEqual(g["rep_rank"], g["rep_candidate"].rank)
        self.assertEqual(g["rep_rank"], DuplicateCandidate.Rank.POSSIBLE_LOW)
        self.assertContains(resp, "低確信度")

    def test_no_n_plus_one_on_group_rows(self):
        """グループ行数を増やしてもクエリ本数が増えない（N+1 回避）。"""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def make_pending_group():
            a, _ = self._make_person_with_primary("同名候補")
            b, _ = self._make_person_with_primary("同名候補")
            self._make_candidate(
                a, b, group_id=uuid.uuid4(),
                review_status=DuplicateCandidate.ReviewStatus.PENDING,
            )

        make_pending_group()
        self.client.get(self.url)  # ウォームアップ
        with CaptureQueriesContext(connection) as ctx1:
            self.client.get(self.url)

        for _ in range(3):
            make_pending_group()
        with CaptureQueriesContext(connection) as ctx2:
            self.client.get(self.url)

        self.assertEqual(
            len(ctx1.captured_queries), len(ctx2.captured_queries),
            "グループ行数の増加でクエリ本数が増えている（N+1 の疑い）",
        )


class GetMatchedFieldsTests(_DuplicatesTestBase):
    """get_matched_fields / build_high_fields_map の単体テスト（v1.7）。"""

    def _make_contact(self, **fields):
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, **fields
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return contact

    def test_matched_fields_pseudo_high(self):
        """confidence レコード無し（疑似 high）の一致フィールドを返す。空値は除外。"""
        ca = self._make_contact(full_name="山田太郎", organization="ABC社")
        cb = self._make_contact(full_name="山田太郎", organization="ABC社")
        hi = build_high_fields_map([ca.id, cb.id])
        matched = get_matched_fields(ca, cb, hi[ca.id], hi[cb.id])
        self.assertIn("full_name", matched)
        self.assertIn("organization", matched)
        self.assertNotIn("email", matched)  # 両側空 → 不一致扱い

    def test_branch_match_included_even_with_zero_score(self):
        """配点 0 の branch も、実際に一致していればリストに含む。"""
        ca = self._make_contact(full_name="A", branch="名古屋支店")
        cb = self._make_contact(full_name="B", branch="名古屋支店")
        hi = build_high_fields_map([ca.id, cb.id])
        matched = get_matched_fields(ca, cb, hi[ca.id], hi[cb.id])
        self.assertIn("branch", matched)
        self.assertNotIn("full_name", matched)  # 値が違う

    def test_non_high_field_excluded(self):
        """未確認 low（high でない）フィールドは一致していても含めない。"""
        ca = self._make_contact(full_name="同名", organization="同社")
        cb = self._make_contact(full_name="同名", organization="同社")
        ContactFieldConfidence.objects.create(
            contact=ca, field_name="organization", confidence="low"
        )
        hi = build_high_fields_map([ca.id, cb.id])
        matched = get_matched_fields(ca, cb, hi[ca.id], hi[cb.id])
        self.assertIn("full_name", matched)
        self.assertNotIn("organization", matched)

    def test_build_high_fields_map_rules(self):
        """high 判定：未確認 low=除外 / 確認済み=high / レコード無し=疑似 high。"""
        c = self._make_contact(full_name="x", title="部長", department="営業")
        ContactFieldConfidence.objects.create(
            contact=c, field_name="title", confidence="low"
        )
        cfc = ContactFieldConfidence.objects.create(
            contact=c, field_name="department", confidence="low"
        )
        cfc.confirmed_at = timezone.now()
        cfc.save(update_fields=["confirmed_at"])
        high = build_high_fields_map([c.id])[c.id]
        self.assertNotIn("title", high)      # 未確認 low
        self.assertIn("department", high)    # 確認済み
        self.assertIn("full_name", high)     # レコード無し=疑似 high
        self.assertIn("address", high)       # レコード無し=疑似 high

    def test_build_high_fields_map_empty(self):
        """空入力では空 dict（クエリも発行されないことは別 N+1 テストで担保）。"""
        self.assertEqual(build_high_fields_map([]), {})


class DuplicateCandidateGroupDetailViewTests(_DuplicatesTestBase):
    """DuplicateCandidateGroupDetailView（16 番）の単体テスト。"""

    def setUp(self):
        super().setUp()
        self.person_a, _ = self._make_person_with_primary("A")
        self.person_b, _ = self._make_person_with_primary("B")
        self.person_c, _ = self._make_person_with_primary("C")
        self.group_id = uuid.uuid4()

    def _url(self, group_id=None):
        return reverse(
            "duplicates:duplicate_group_detail",
            kwargs={"group_id": group_id or self.group_id},
        )

    def test_get_returns_200(self):
        """認証あり GET（group_id 存在）→ 200。"""
        self._make_candidate(
            self.person_a, self.person_b, group_id=self.group_id
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_redirects(self):
        """LoginRequiredMixin → 302。"""
        self._make_candidate(
            self.person_a, self.person_b, group_id=self.group_id
        )
        c = Client()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)

    def test_nonexistent_group_returns_404(self):
        """当該 group_id のレコードが存在しなければ 404。"""
        resp = self.client.get(self._url(group_id=uuid.uuid4()))
        self.assertEqual(resp.status_code, 404)

    def test_has_pending_true(self):
        """pending 1 件 → has_pending=True、pending_count=1。"""
        self._make_candidate(
            self.person_a,
            self.person_b,
            group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        resp = self.client.get(self._url())
        self.assertTrue(resp.context["has_pending"])
        self.assertEqual(resp.context["pending_count"], 1)
        self.assertEqual(resp.context["merged_count"], 0)
        self.assertEqual(resp.context["different_person_count"], 0)

    def test_has_pending_false(self):
        """すべて merged / different_person → has_pending=False、各カウントが正しい。"""
        self._make_candidate(
            self.person_a,
            self.person_b,
            group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.MERGED,
        )
        self._make_candidate(
            self.person_a,
            self.person_c,
            group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON,
        )
        resp = self.client.get(self._url())
        self.assertFalse(resp.context["has_pending"])
        self.assertEqual(resp.context["pending_count"], 0)
        self.assertEqual(resp.context["merged_count"], 1)
        self.assertEqual(resp.context["different_person_count"], 1)

    def test_invalidated_excluded_from_counts(self):
        """invalidated は merged_count / different_person_count に含まれない。"""
        self._make_candidate(
            self.person_a,
            self.person_b,
            group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.INVALIDATED,
        )
        self._make_candidate(
            self.person_a,
            self.person_c,
            group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.MERGED,
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.context["merged_count"], 1)
        self.assertEqual(resp.context["different_person_count"], 0)
        self.assertEqual(resp.context["pending_count"], 0)
        self.assertFalse(resp.context["has_pending"])

    # ------------------------------------------------------------------
    # v1.7 UI 改善（レビュー開始ボタン条件表示 / レビュー済み統合表 /
    # レビュー結果日本語化 / group_id DEBUG 限定）
    # ------------------------------------------------------------------

    def _make_reviewed(
        self, person_a, person_b, *, status, review_result,
        group_id=None, **kw
    ):
        """レビュー済み DC（reviewed_at / review_result セット済み）を作る。"""
        candidate = self._make_candidate(
            person_a, person_b, group_id=group_id or self.group_id,
            review_status=status, **kw,
        )
        candidate.review_result = review_result
        candidate.reviewed_at = timezone.now()
        candidate.save(update_fields=["review_result", "reviewed_at"])
        return candidate

    def _make_merged_scenario(
        self, *, log_status, group_id=None, absorbed_name="吸収太郎",
        recover_name=True,
    ):
        """マージ相当のデータ一式を作る（merged DC + PersonMergeLog + 吸収側の痕跡）。

        recover_name=True：吸収側の元 primary Contact を survivor 配下へ移し
          previous_person=吸収側 / previous_status='primary' を残す（氏名復元可）。
        recover_name=False：previous_person を別人で上書き済みにし、吸収側の
          氏名が辿れない状態（連鎖マージ相当、(氏名なし) フォールバック）を作る。
        """
        gid = group_id or self.group_id
        surviving, _ = self._make_person_with_primary("生存花子")
        merged, merged_primary = self._make_person_with_primary(absorbed_name)

        merged_primary.person = surviving
        merged_primary.previous_person = (
            merged if recover_name else surviving
        )
        merged_primary.previous_status = Contact.Status.PRIMARY
        merged_primary.status = Contact.Status.INACTIVE
        merged_primary.save(update_fields=[
            "person", "previous_person", "previous_status",
            "status", "updated_at",
        ])

        merged.primary_contact = None
        merged.status = Person.Status.MERGED
        merged.merged_into = surviving
        merged.save(update_fields=[
            "primary_contact", "status", "merged_into", "updated_at",
        ])

        candidate = self._make_reviewed(
            surviving, merged, group_id=gid,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        log = PersonMergeLog.objects.create(
            surviving_person=surviving,
            merged_person=merged,
            duplicate_candidate=candidate,
            status=log_status,
            executed_at=timezone.now(),
        )
        return candidate, surviving, merged, log

    def test_review_button_shown_when_pending(self):
        """pending > 0 のとき「レビューを開始」ボタンを表示する。"""
        self._make_candidate(
            self.person_a, self.person_b, group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        resp = self.client.get(self._url())
        review_url = reverse(
            "duplicates:duplicate_group_review",
            kwargs={"group_id": self.group_id},
        )
        self.assertContains(resp, "レビューを開始")
        self.assertContains(resp, review_url)

    def test_review_button_hidden_when_no_pending(self):
        """pending 0（完了済み）のとき「レビューを開始」ボタンを表示しない。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        resp = self.client.get(self._url())
        self.assertNotContains(resp, "レビューを開始")

    def test_reviewed_table_contains_both_statuses(self):
        """統合表 reviewed_candidates に merged と different_person の両方が入る。"""
        c_merged = self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        c_diff = self._make_reviewed(
            self.person_a, self.person_c,
            status=DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON,
            review_result=["same_name"],
        )
        resp = self.client.get(self._url())
        ids = {c.id for c in resp.context["reviewed_candidates"]}
        self.assertIn(c_merged.id, ids)
        self.assertIn(c_diff.id, ids)

    def test_reviewed_table_excludes_pending_and_invalidated(self):
        """統合表に pending / invalidated は含めない。"""
        c_pending = self._make_candidate(
            self.person_a, self.person_b, group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        c_inval = self._make_reviewed(
            self.person_a, self.person_c,
            status=DuplicateCandidate.ReviewStatus.INVALIDATED,
            review_result=[],
        )
        c_merged = self._make_reviewed(
            self.person_b, self.person_c,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        resp = self.client.get(self._url())
        ids = {c.id for c in resp.context["reviewed_candidates"]}
        self.assertEqual(ids, {c_merged.id})
        self.assertNotIn(c_pending.id, ids)
        self.assertNotIn(c_inval.id, ids)

    def test_reviewed_table_shows_status_score_rank(self):
        """統合表にレビューステータス（日本語）・score・rank バッジが出る。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
            score=137,
            rank=DuplicateCandidate.Rank.EXACT_MATCH,
        )
        resp = self.client.get(self._url())
        self.assertContains(resp, "マージ済み")       # get_review_status_display
        self.assertContains(resp, "137")               # score
        self.assertContains(resp, "完全一致")          # rank バッジ
        self.assertContains(resp, "レビューステータス")  # 列見出し

    def test_review_result_rendered_in_japanese(self):
        """レビュー結果が日本語ラベルで出る（英字 value が生で出ない）。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        self._make_reviewed(
            self.person_a, self.person_c,
            status=DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON,
            review_result=["same_name"],
        )
        resp = self.client.get(self._url())
        self.assertContains(resp, "同一名刺")      # same_card のラベル
        self.assertContains(resp, "同姓同名の別人")  # same_name のラベル
        self.assertNotContains(resp, "same_card")
        self.assertNotContains(resp, "same_name")

    def test_group_id_hidden_when_not_debug(self):
        """通常レンダリング（DEBUG=False）では group_id 生 UUID を表示しない。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        with self.settings(DEBUG=False):
            resp = self.client.get(self._url())
        self.assertNotContains(resp, str(self.group_id))
        self.assertNotContains(resp, "bi-bug-fill")
        self.assertNotContains(resp, "デバッグ表示")

    def test_group_id_shown_when_debug(self):
        """DEBUG=True かつ INTERNAL_IPS 一致時は group_id 生 UUID を表示する。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        with self.settings(DEBUG=True, INTERNAL_IPS=["127.0.0.1"]):
            resp = self.client.get(self._url(), REMOTE_ADDR="127.0.0.1")
        self.assertContains(resp, str(self.group_id))
        self.assertContains(resp, "bi-bug-fill")
        self.assertContains(resp, "デバッグ表示")

    def test_summary_heading_removed(self):
        """集計セクションの「集計」見出しは撤去され、1 行要約は残る。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        resp = self.client.get(self._url())
        self.assertNotContains(resp, "集計")
        self.assertContains(resp, "レビュー済み")

    # ------------------------------------------------------------------
    # ① 集計 1 行要約 / ② Person 氏名復元 / ③ 復元可否列
    # ------------------------------------------------------------------

    def test_summary_one_line_counts(self):
        """集計が全件・未レビュー・レビュー済み内訳を 1 行で持つ。"""
        self._make_candidate(
            self.person_a, self.person_b, group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
        )
        self._make_reviewed(
            self.person_a, self.person_c,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.context["total_count"], 2)
        self.assertEqual(resp.context["reviewed_count"], 1)
        self.assertEqual(resp.context["pending_count"], 1)
        self.assertContains(resp, "全")
        self.assertContains(resp, "レビュー済み")
        self.assertContains(resp, "マージ")
        self.assertContains(resp, "別人")

    def test_person_name_recovered_for_merged_side(self):
        """merged 行で吸収側の氏名が previous_person 経由で復元表示される。"""
        self._make_merged_scenario(
            log_status=PersonMergeLog.Status.UNDOABLE,
            absorbed_name="復元できる太郎",
            recover_name=True,
        )
        resp = self.client.get(self._url())
        self.assertContains(resp, "復元できる太郎")
        self.assertNotContains(resp, "(氏名なし)")

    def test_person_name_falls_back_when_not_recoverable(self):
        """previous_person が辿れない（連鎖マージ相当）なら (氏名なし)。"""
        self._make_merged_scenario(
            log_status=PersonMergeLog.Status.LOCKED,
            absorbed_name="辿れない次郎",
            recover_name=False,
        )
        resp = self.client.get(self._url())
        self.assertNotContains(resp, "辿れない次郎")
        self.assertContains(resp, "(氏名なし)")

    def test_restore_status_badges(self):
        """復元可否列が PersonMergeLog.status に応じたバッジで出る。"""
        cases = [
            (PersonMergeLog.Status.UNDOABLE, "復元可能"),
            (PersonMergeLog.Status.UNDONE, "復元済み"),
            (PersonMergeLog.Status.LOCKED, "復元不可"),
        ]
        for log_status, label in cases:
            with self.subTest(log_status=log_status):
                gid = uuid.uuid4()
                self._make_merged_scenario(log_status=log_status, group_id=gid)
                resp = self.client.get(self._url(group_id=gid))
                self.assertContains(resp, label)

    def test_restore_status_dash_for_different_person(self):
        """別人確定の行は MergeLog が無く復元可否が「—」。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON,
            review_result=["same_name"],
        )
        resp = self.client.get(self._url())
        self.assertContains(resp, "—")
        self.assertNotContains(resp, "復元可能")
        self.assertNotContains(resp, "復元済み")
        self.assertNotContains(resp, "復元不可")

    def test_restore_status_dash_when_no_merge_log(self):
        """merged だが MergeLog が無い行も復元可否は「—」。"""
        self._make_reviewed(
            self.person_a, self.person_b,
            status=DuplicateCandidate.ReviewStatus.MERGED,
            review_result=["same_card"],
        )
        resp = self.client.get(self._url())
        self.assertContains(resp, "—")

    def test_no_n_plus_one_on_reviewed_rows(self):
        """レビュー済み行数を増やしてもクエリ本数が増えない（N+1 回避）。"""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        gid1 = uuid.uuid4()
        self._make_merged_scenario(
            log_status=PersonMergeLog.Status.UNDOABLE, group_id=gid1
        )
        # ウォームアップ（ContentType / 権限などプロセスキャッシュを温める）。
        self.client.get(self._url(group_id=gid1))
        with CaptureQueriesContext(connection) as ctx1:
            self.client.get(self._url(group_id=gid1))

        gid3 = uuid.uuid4()
        for _ in range(3):
            self._make_merged_scenario(
                log_status=PersonMergeLog.Status.UNDOABLE, group_id=gid3
            )
        with CaptureQueriesContext(connection) as ctx3:
            self.client.get(self._url(group_id=gid3))

        self.assertEqual(
            len(ctx1.captured_queries), len(ctx3.captured_queries),
            "レビュー済み行数の増加でクエリ本数が増えている（N+1 の疑い）",
        )


class _MergeFormTestBase(_DuplicatesTestBase):
    """MergeForm 系テスト共通：surviving / merged の Person + Contact + DC を 1 セット用意。"""

    def setUp(self):
        super().setUp()
        self.surviving_person, self.surviving_primary = (
            self._make_person_with_primary("生存太郎", created_by=self.user)
        )
        self.merged_person, self.merged_primary = (
            self._make_person_with_primary("統合次郎", created_by=self.user)
        )
        self.candidate = self._make_candidate(
            self.surviving_person,
            self.merged_person,
        )

    def _set_cfc(
        self, contact, field_name, *, confidence="low", confirmed=False
    ):
        cfc = ContactFieldConfidence.objects.create(
            contact=contact,
            field_name=field_name,
            confidence=confidence,
        )
        if confirmed:
            cfc.confirmed_at = timezone.now()
            cfc.confirmed_by = self.user
            cfc.save()
        return cfc

    def _valid_data(self, **overrides):
        """正常系の最小フォームデータ（UPDATABLE_FIELDS の surviving 現在値 + 必須項目）。

        D-4d-1 第 4 弾で review_result を MultipleChoiceField に再変更。
        `review_result` は list[str] で渡す（MergeForm 直接インスタンス化時は
        dict.get 経由なので list でないと ValidationError）。
        """
        data = {
            f: getattr(self.surviving_primary, f)
            for f in Contact.UPDATABLE_FIELDS
        }
        data["review_decision"] = "merged"
        data["review_result"] = ["same_card"]
        data["surviving_person_choice"] = "person_a"
        data["note"] = ""
        data.update(overrides)
        return data

    def _make_form(self, data=None, **kwargs):
        return MergeForm(
            data=data,
            candidate=kwargs.get("candidate", self.candidate),
            surviving_person=kwargs.get(
                "surviving_person", self.surviving_person
            ),
            merged_person=kwargs.get(
                "merged_person", self.merged_person
            ),
        )


class MergeFormInitTests(_MergeFormTestBase):
    """MergeForm.__init__ のテスト（D-4a）。"""

    def test_missing_candidate_raises_type_error(self):
        with self.assertRaises(TypeError):
            MergeForm(
                surviving_person=self.surviving_person,
                merged_person=self.merged_person,
            )

    def test_missing_surviving_person_raises_type_error(self):
        with self.assertRaises(TypeError):
            MergeForm(
                candidate=self.candidate,
                merged_person=self.merged_person,
            )

    def test_missing_merged_person_raises_type_error(self):
        with self.assertRaises(TypeError):
            MergeForm(
                candidate=self.candidate,
                surviving_person=self.surviving_person,
            )

    def test_initial_filled_with_surviving_values(self):
        """initial が surviving 側 primary_contact の UPDATABLE_FIELDS 値で埋まる。"""
        self.surviving_primary.organization = "サバイブ社"
        self.surviving_primary.email = "alive@example.com"
        self.surviving_primary.save()

        form = self._make_form()
        self.assertEqual(form.initial["full_name"], "生存太郎")
        self.assertEqual(form.initial["organization"], "サバイブ社")
        self.assertEqual(form.initial["email"], "alive@example.com")

    def test_dynamic_confirm_checkboxes_added_for_low_mid_unconfirmed(self):
        """surviving 側 low/mid 未確認の DUPLICATE_CHECK_FIELDS に CB が動的追加される。"""
        self._set_cfc(self.surviving_primary, "full_name", confidence="low")
        self._set_cfc(self.surviving_primary, "email", confidence="mid")
        # confirmed 済みは追加対象外
        self._set_cfc(
            self.surviving_primary,
            "personal_phone",
            confidence="low",
            confirmed=True,
        )

        form = self._make_form()
        self.assertIn("confirmed_full_name", form.fields)
        self.assertIn("confirmed_email", form.fields)
        self.assertNotIn("confirmed_personal_phone", form.fields)
        # CFC レコードなし（疑似 high）にも追加されない
        self.assertNotIn("confirmed_organization", form.fields)

    def test_value_diff_and_match_classification(self):
        """DUPLICATE_CHECK_FIELDS で値違い / 値一致が正しく分類される。"""
        self.surviving_primary.organization = "A社"
        self.surviving_primary.email = "a@example.com"
        self.surviving_primary.save()

        self.merged_primary.organization = "A社"  # 一致
        self.merged_primary.email = "b@example.com"  # 不一致
        self.merged_primary.save()

        form = self._make_form()
        # setUp で full_name は surviving/merged で異なる
        self.assertIn("full_name", form.value_diff_fields())
        self.assertIn("email", form.value_diff_fields())
        self.assertIn("organization", form.value_match_fields())
        # 値違いと値一致は排他
        self.assertNotIn("email", form.value_match_fields())
        self.assertNotIn("organization", form.value_diff_fields())


class MergeFormCleanTests(_MergeFormTestBase):
    """MergeForm.clean() のバリデーションテスト（D-4a / D-4d-1 続きで再構成）。"""

    def test_review_result_empty_invalid(self):
        data = self._valid_data(review_result=[])
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_review_decision_merged_with_different_reason_invalid(self):
        """review_decision='merged' なのに別人系 value を含むと整合性エラー。"""
        data = self._valid_data(
            review_decision="merged",
            review_result=["same_name"],
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_review_decision_merged_with_mixed_reasons_invalid(self):
        """review_decision='merged' でマージ系 + 別人系の混在 invalid（D-4d-1 第 4 弾 §2-4-D）。"""
        data = self._valid_data(
            review_decision="merged",
            review_result=["same_card", "same_name"],
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_review_decision_merged_with_additional_role_invalid(self):
        """review_decision='merged' に additional_role を含めるのは invalid
        （3 値化版で additional_role は第 1 段階の別選択肢となったため）。
        """
        data = self._valid_data(
            review_decision="merged",
            review_result=["additional_role"],
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_review_decision_additional_role_with_empty_result_valid(self):
        """review_decision='additional_role' は review_result 空でも valid。

        cleaned_data['review_result'] は clean() で ['additional_role'] に整形される。
        """
        data = self._valid_data(
            review_decision="additional_role",
            review_result=[],
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["review_result"], ["additional_role"]
        )

    def test_review_decision_additional_role_with_different_reason_invalid(self):
        """review_decision='additional_role' で別人系 value 混在は invalid（防御）。"""
        data = self._valid_data(
            review_decision="additional_role",
            review_result=["same_name"],
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_review_decision_different_with_merged_reason_invalid(self):
        """review_decision='different' なのにマージ系 value を含むと整合性エラー。"""
        data = self._valid_data(
            review_decision="different",
            review_result=["same_card"],
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_review_result_multiple_merged_values_valid(self):
        """マージ系 value を複数選択した場合に valid（D-4d-1 第 4 弾 §6-A）。"""
        data = self._valid_data(
            review_decision="merged",
            review_result=["transfer", "promotion"],
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["review_result"], ["transfer", "promotion"]
        )

    def test_other_merged_without_note_invalid(self):
        data = self._valid_data(
            review_decision="merged",
            review_result=["other_merged"],
            note="",
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("note", form.errors)

    def test_other_merged_with_note_valid(self):
        data = self._valid_data(
            review_decision="merged",
            review_result=["other_merged"],
            note="その他のメモ",
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_other_different_without_note_invalid(self):
        data = self._valid_data(
            review_decision="different",
            review_result=["other_different"],
            note="",
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("note", form.errors)

    def test_low_mid_field_requires_confirmation(self):
        """surviving 側 low/mid 未確認 → 確認 CB OFF だと invalid。"""
        self._set_cfc(self.surviving_primary, "full_name", confidence="low")
        data = self._valid_data()  # confirmed_full_name なし
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed_full_name", form.errors)

    def test_low_mid_field_passes_when_confirmed(self):
        """surviving 側 low/mid + 確認 CB ON だと valid。"""
        self._set_cfc(self.surviving_primary, "full_name", confidence="low")
        data = self._valid_data(confirmed_full_name=True)
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_surviving_choice_required_for_merged(self):
        """review_decision='merged' で surviving_person_choice 空 → invalid（D-4d-1 第 7 弾 §2-1-B）。"""
        data = self._valid_data(
            review_decision="merged",
            review_result=["same_card"],
            surviving_person_choice="",
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("surviving_person_choice", form.errors)

    def test_surviving_choice_required_for_additional_role(self):
        """review_decision='additional_role' で surviving_person_choice 空 → invalid。"""
        data = self._valid_data(
            review_decision="additional_role",
            review_result=[],
            surviving_person_choice="",
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("surviving_person_choice", form.errors)

    def test_surviving_choice_optional_for_different(self):
        """review_decision='different' は surviving_person_choice 空でも valid（不要のため）。"""
        data = self._valid_data(
            review_decision="different",
            review_result=["same_name"],
            surviving_person_choice="",
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_low_mid_field_skipped_when_different_decision(self):
        """別人判定では surviving 側 low/mid 未確認 CB バリデーションがスキップされる。

        D-4d-1 第 5 弾 §2-1：別人判定時はテンプレ側で確認 CB が動的非表示のため
        ユーザが ON にできない。バリデーションも走らせず form.is_valid() が True。
        """
        self._set_cfc(self.surviving_primary, "full_name", confidence="low")
        data = self._valid_data(
            review_decision="different",
            review_result=["same_name"],
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_low_mid_field_requires_confirmation_for_additional_role(self):
        """別肩書追加判定では surviving 側 low/mid 未確認 CB バリデーションが走る。

        D-4d-1 第 5 弾 §2-1：additional_role はマージ系と同じく Execute_Merge_Only
        経路を通るため、surviving 側の CFC 確認は必須。
        """
        self._set_cfc(self.surviving_primary, "full_name", confidence="low")
        data = self._valid_data(
            review_decision="additional_role",
            review_result=[],
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed_full_name", form.errors)

    def test_valid_form_passes(self):
        """全項目正常 → valid。"""
        data = self._valid_data()
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_field_errors_use_app_form_error_class(self):
        """MergeForm の field エラー HTML に `app-form__error` クラスが付与される。

        D-4d-1 第 3 弾 §2-5：ContactBaseForm.error_class = AppErrorList の波及検証。
        """
        data = self._valid_data(review_result=[])
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn(
            'class="errorlist app-form__error"',
            str(form["review_result"].errors),
        )


class MergeFormHelpersTests(_MergeFormTestBase):
    """MergeForm のヘルパーメソッドテスト（D-4a）。"""

    def test_get_update_contact_returns_unsaved_contact(self):
        """get_update_contact() は pk なし（_state.adding=True）の Contact を返す。"""
        data = self._valid_data(full_name="新しい名前")
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

        contact = form.get_update_contact()
        self.assertTrue(contact._state.adding)
        self.assertEqual(contact.full_name, "新しい名前")

    def test_confirmed_field_names_includes_edited(self):
        """編集されたフィールドは confirmed_field_names に含まれる。"""
        data = self._valid_data(full_name="編集後の名前")
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("full_name", form.confirmed_field_names())

    def test_confirmed_field_names_includes_checked(self):
        """編集なしでも CB ON のフィールドは confirmed_field_names に含まれる。"""
        self._set_cfc(self.surviving_primary, "organization", confidence="low")
        data = self._valid_data(confirmed_organization=True)
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("organization", form.confirmed_field_names())

    def test_confirmed_field_names_empty_when_no_changes(self):
        """編集なし・CB なしのとき confirmed_field_names は空リスト。"""
        data = self._valid_data()
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.confirmed_field_names(), [])

    def test_has_field_updates_true_when_edited(self):
        data = self._valid_data(full_name="編集後の名前")
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.has_field_updates())

    def test_has_field_updates_false_when_no_edit(self):
        data = self._valid_data()
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.has_field_updates())

    def test_value_diff_fields_returns_diff_only(self):
        """value_diff_fields() は DUPLICATE_CHECK_FIELDS の値違いのみ返す。"""
        # setUp 時点で full_name のみ surviving/merged で異なる
        form = self._make_form()
        diff = form.value_diff_fields()
        self.assertIn("full_name", diff)
        # organization は空文字同士で一致
        self.assertNotIn("organization", diff)

    def test_value_match_fields_returns_match_only(self):
        """value_match_fields() は DUPLICATE_CHECK_FIELDS の値一致のみ返す。"""
        form = self._make_form()
        match = form.value_match_fields()
        # organization / email / personal_phone 等は空文字同士で一致
        self.assertIn("organization", match)
        self.assertIn("email", match)
        # full_name は不一致
        self.assertNotIn("full_name", match)

    def test_get_merge_reason_returns_list_for_merged_series_single(self):
        """review_result がマージ系単一 value のとき get_merge_reason() は len=1 list（D-4d-1 第 4 弾）。"""
        data = self._valid_data(
            review_decision="merged",
            review_result=["same_card"],
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.get_merge_reason(), ["same_card"])

    def test_get_merge_reason_returns_list_for_merged_series_multiple(self):
        """review_result がマージ系 value を複数選んだとき get_merge_reason() は当該 list（D-4d-1 第 4 弾）。"""
        data = self._valid_data(
            review_decision="merged",
            review_result=["transfer", "promotion"],
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.get_merge_reason(), ["transfer", "promotion"])

    def test_get_merge_reason_returns_empty_list_for_different_series(self):
        """review_result が別人系 value のとき get_merge_reason() は空リスト []（D-4d-1 第 4 弾）。"""
        data = self._valid_data(
            review_decision="different",
            review_result=["same_name"],
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.get_merge_reason(), [])

    def test_get_merge_reason_returns_additional_role_list_for_additional_role(self):
        """review_decision='additional_role' のとき get_merge_reason() は ['additional_role']
        を返す（clean() の cleaned_data 整形を経由、3 値化版）。
        """
        data = self._valid_data(
            review_decision="additional_role",
            review_result=[],
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.get_merge_reason(), ["additional_role"])

    def test_has_confirm_checkboxes_returns_true_when_unconfirmed_cfc(self):
        """surviving 側に low/mid 未確認 CFC があるとき has_confirm_checkboxes()=True。"""
        self._set_cfc(self.surviving_primary, "full_name", confidence="low")
        form = self._make_form()
        self.assertTrue(form.has_confirm_checkboxes())

    def test_has_confirm_checkboxes_returns_false_when_no_cfc(self):
        """surviving 側に未確認 CFC がないとき has_confirm_checkboxes()=False。"""
        form = self._make_form()
        self.assertFalse(form.has_confirm_checkboxes())

    def _set_name_fields(self, contact, full, last, first):
        contact.full_name = full
        contact.last_name = last
        contact.first_name = first
        contact.save()

    def test_hidden_name_fields_returns_last_first_when_all_match(self):
        """フルネーム一致 + 姓名一致 + full_name に部分一致 → ["last_name", "first_name"]。"""
        self._set_name_fields(
            self.surviving_primary, "山田太郎", "山田", "太郎"
        )
        self._set_name_fields(
            self.merged_primary, "山田太郎", "山田", "太郎"
        )
        form = self._make_form()
        self.assertEqual(
            form.hidden_name_fields(), ["last_name", "first_name"]
        )

    def test_hidden_name_fields_empty_when_full_name_differs(self):
        """full_name が左右で違うとき []。"""
        self._set_name_fields(
            self.surviving_primary, "山田太郎", "山田", "太郎"
        )
        self._set_name_fields(
            self.merged_primary, "山田次郎", "山田", "太郎"
        )
        form = self._make_form()
        self.assertEqual(form.hidden_name_fields(), [])

    def test_hidden_name_fields_empty_when_last_name_differs(self):
        """last_name が左右で違うとき []。"""
        self._set_name_fields(
            self.surviving_primary, "山田太郎", "山田", "太郎"
        )
        self._set_name_fields(
            self.merged_primary, "山田太郎", "佐藤", "太郎"
        )
        form = self._make_form()
        self.assertEqual(form.hidden_name_fields(), [])

    def test_hidden_name_fields_empty_when_last_name_blank(self):
        """last_name が空文字のとき []。"""
        self._set_name_fields(
            self.surviving_primary, "山田太郎", "", "太郎"
        )
        self._set_name_fields(self.merged_primary, "山田太郎", "", "太郎")
        form = self._make_form()
        self.assertEqual(form.hidden_name_fields(), [])

    def test_hidden_name_fields_empty_when_last_name_not_in_full_name(self):
        """last_name が full_name に含まれないとき []。"""
        self._set_name_fields(
            self.surviving_primary, "山田太郎", "YAMADA", "太郎"
        )
        self._set_name_fields(
            self.merged_primary, "山田太郎", "YAMADA", "太郎"
        )
        form = self._make_form()
        self.assertEqual(form.hidden_name_fields(), [])


class _DuplicateGroupUpdateViewTestBase(_DuplicatesTestBase):
    """17 番 View テスト共通：Person 3 体 + group_id を 1 つ用意。"""

    def setUp(self):
        super().setUp()
        self.group_id = uuid.uuid4()
        self.person_x, _ = self._make_person_with_primary(
            "X 太郎", created_by=self.user
        )
        self.person_y, _ = self._make_person_with_primary(
            "Y 次郎", created_by=self.user
        )
        self.person_z, _ = self._make_person_with_primary(
            "Z 三郎", created_by=self.user
        )

    def _url(self, group_id=None):
        return reverse(
            "duplicates:duplicate_group_review",
            kwargs={"group_id": group_id or self.group_id},
        )


class DuplicateCandidateGroupUpdateViewGetTests(
    _DuplicateGroupUpdateViewTestBase
):
    """17 番 View GET 単体テスト（D-4b E-1）。"""

    def test_unauthenticated_redirects(self):
        """LoginRequiredMixin → 未ログインは 302。"""
        c = Client()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)

    def test_nonexistent_group_redirects_to_list(self):
        """候補なし + reviewed_pair_ids 空 → 15 番リダイレクト（ステップ5）。"""
        resp = self.client.get(self._url(group_id=uuid.uuid4()))
        self.assertRedirects(
            resp,
            reverse("duplicates:duplicate_group_list"),
        )

    def test_pending_candidate_returns_200_with_context(self):
        """pending 候補あり → 200、context に candidate / form / surviving / merged。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["candidate"].pk, candidate.pk)
        self.assertEqual(resp.context["group_id"], self.group_id)
        self.assertEqual(
            resp.context["surviving_person"].pk,
            candidate.person_a.pk,
        )
        self.assertEqual(
            resp.context["merged_person"].pk,
            candidate.person_b.pk,
        )

    def test_compare_headings_use_business_terms_not_person_ab(self):
        """フィールド比較の左右見出しが英字 Person A/B でなく人物（左/右）であること
        （HIG v1.4 原則4）。フォーム挙動は不変。"""
        self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        # 業務語が出る
        self.assertContains(resp, "人物（左）")
        self.assertContains(resp, "人物（右）")
        self.assertContains(resp, "これをサバイブ側にする")
        # 英字内部語は出ない
        self.assertNotContains(resp, "Person A")
        self.assertNotContains(resp, "Person B")
        self.assertNotContains(resp, "これをサバイブにする</span>")

    def test_pair_ordering_score_desc_then_created_at_asc(self):
        """score 降順 → 同 score なら created_at 昇順で先頭 1 件取得（論点2 案C）。"""
        self._make_candidate(
            self.person_x, self.person_y,
            group_id=self.group_id,
            score=100,
        )
        c_high = self._make_candidate(
            self.person_x, self.person_z,
            group_id=self.group_id,
            score=200,
        )
        self._make_candidate(
            self.person_y, self.person_z,
            group_id=self.group_id,
            score=100,
        )

        resp = self.client.get(self._url())
        self.assertEqual(resp.context["candidate"].pk, c_high.pk)


class DuplicateCandidateGroupUpdateViewSessionTests(
    _DuplicateGroupUpdateViewTestBase
):
    """17 番 View GET の reviewed_pair_ids フィルタテスト（D-4b E-2、D-4c で改訂）。

    D-4c で reviewed_pair_ids への追加は POST 時のみに変更されたため、本クラスは
    「事前にセッションに値が入っていれば GET の filter が機能する」ことだけを検証する。
    POST 時の session 追加挙動は DuplicateCandidateGroupUpdateViewPostTests を参照。
    """

    def _set_session_reviewed(self, group_id, pair_ids):
        session = self.client.session
        session[f"reviewed_pair_ids:{group_id}"] = pair_ids
        session.save()

    def test_get_excludes_reviewed_pair_in_session(self):
        """事前に session に入った pair_id は filter から除外され、次のペアが返る。"""
        c1 = self._make_candidate(
            self.person_x, self.person_y,
            group_id=self.group_id,
            score=200,
        )
        c2 = self._make_candidate(
            self.person_x, self.person_z,
            group_id=self.group_id,
            score=100,
        )

        self._set_session_reviewed(self.group_id, [str(c1.pk)])
        resp = self.client.get(self._url())
        self.assertEqual(resp.context["candidate"].pk, c2.pk)

    def test_all_reviewed_redirects_to_detail_and_clears_session(self):
        """全ペアがセッションに含まれる状態の GET → 16 番リダイレクト + session クリア。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        self._set_session_reviewed(self.group_id, [str(candidate.pk)])
        resp = self.client.get(self._url())
        self.assertRedirects(
            resp,
            reverse(
                "duplicates:duplicate_group_detail",
                kwargs={"group_id": self.group_id},
            ),
        )
        session_key = f"reviewed_pair_ids:{self.group_id}"
        self.assertNotIn(session_key, self.client.session)

    def test_independent_session_keys_per_group(self):
        """別 group_id のセッションキーは独立（並行レビュー、論点1 案A）。"""
        group_b = uuid.uuid4()
        c_a = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        c_b = self._make_candidate(
            self.person_x, self.person_z, group_id=group_b
        )

        self._set_session_reviewed(self.group_id, [str(c_a.pk)])
        self._set_session_reviewed(group_b, [])

        # group_a：reviewed 済みなので候補なし → 16 番リダイレクト
        resp_a = self.client.get(self._url())
        self.assertEqual(resp_a.status_code, 302)

        # group_b：reviewed 空なので候補（c_b）表示
        resp_b = self.client.get(self._url(group_id=group_b))
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(resp_b.context["candidate"].pk, c_b.pk)

    def test_get_does_not_add_to_session(self):
        """GET は session に reviewed_pair_ids を追加しない（D-4c 仕様変更）。"""
        self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        self.client.get(self._url())
        session_key = f"reviewed_pair_ids:{self.group_id}"
        self.assertNotIn(session_key, self.client.session)


class DuplicateCandidateGroupUpdateViewRedirectTests(
    _DuplicateGroupUpdateViewTestBase
):
    """17 番 View PRG リダイレクトテスト（D-4b E-3）。"""

    def test_no_pair_and_empty_session_redirects_to_list(self):
        """ペアなし + reviewed_pair_ids 空 → 15 番リダイレクト（ステップ5）。"""
        resp = self.client.get(self._url())
        self.assertRedirects(
            resp,
            reverse("duplicates:duplicate_group_list"),
        )

    def test_no_pair_with_session_shows_completion_message(self):
        """ペアなし + reviewed_pair_ids あり → 16 番リダイレクト + 完了メッセージ。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        session = self.client.session
        session[f"reviewed_pair_ids:{self.group_id}"] = [str(candidate.pk)]
        session.save()

        resp = self.client.get(self._url(), follow=True)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertIn(
            "すべてのペアのレビューが完了しました",
            msgs,
        )


class DuplicateCandidateGroupUpdateViewMergeFormInitTests(
    _DuplicateGroupUpdateViewTestBase
):
    """17 番 View の MergeForm 初期化テスト（D-4b E-4）。"""

    def test_context_form_is_merge_form_instance(self):
        """context["form"] が MergeForm のインスタンス。"""
        self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        resp = self.client.get(self._url())
        self.assertIsInstance(resp.context["form"], MergeForm)

    def test_form_initialized_with_person_a_as_surviving(self):
        """form.surviving_person=candidate.person_a, merged=person_b（論点3 案A）。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        resp = self.client.get(self._url())
        form = resp.context["form"]
        self.assertEqual(form.candidate.pk, candidate.pk)
        self.assertEqual(form.surviving_person.pk, candidate.person_a.pk)
        self.assertEqual(form.merged_person.pk, candidate.person_b.pk)


class _DuplicateGroupUpdateViewPostTestBase(_DuplicateGroupUpdateViewTestBase):
    """17 番 View POST テスト共通：candidate 起点の POST データ生成ヘルパー（D-4c）。"""

    def _post_data(self, candidate, **overrides):
        """正常系 POST データ（merged_only 既定）。surviving=person_a。

        D-4d-1 第 4 弾で review_result を MultipleChoiceField に再変更。
        `review_result` は list[str] で渡す（test client の POST 経由で QueryDict 化）。
        """
        surviving_primary = candidate.person_a.primary_contact
        data = {
            f: getattr(surviving_primary, f)
            for f in Contact.UPDATABLE_FIELDS
        }
        data["pair_id"] = str(candidate.pk)
        data["review_decision"] = "merged"
        data["review_result"] = ["same_card"]
        data["surviving_person_choice"] = "person_a"
        data["note"] = ""
        data.update(overrides)
        return data


class DuplicateCandidateGroupUpdateViewPostTests(
    _DuplicateGroupUpdateViewPostTestBase
):
    """17 番 View POST 正常系テスト（D-4c C-2 / C-3）。"""

    def test_post_different_person_marks_as_different(self):
        """review_result=different 系 → Mark_as_Different_Person → status=different_person。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        data = self._post_data(
            candidate,
            review_decision="different",
            review_result=["same_name"],
        )
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 302)
        candidate.refresh_from_db()
        self.assertEqual(
            candidate.review_status,
            DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON,
        )

    def test_post_different_person_succeeds_despite_unconfirmed_cfc(self):
        """別人判定 + surviving 側 low 未確認 CFC + 確認 CB なし → 成功 → different_person。

        D-4d-1 第 5 弾 §2-1 致命的バグ修正：別人判定では確認 CB バリデーションが
        スキップされる（テンプレ側で確認 CB ブロックが動的非表示のため）。
        """
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        ContactFieldConfidence.objects.create(
            contact=candidate.person_a.primary_contact,
            field_name="full_name",
            confidence="low",
        )
        data = self._post_data(
            candidate,
            review_decision="different",
            review_result=["same_name"],
        )
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 302)
        candidate.refresh_from_db()
        self.assertEqual(
            candidate.review_status,
            DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON,
        )

    def test_post_merged_executes_merge_only(self):
        """review_result=merged 系 → Execute_Merge_Only → DC=merged + merged_person=merged。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        data = self._post_data(candidate)
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 302)
        candidate.refresh_from_db()
        self.assertEqual(
            candidate.review_status,
            DuplicateCandidate.ReviewStatus.MERGED,
        )
        merged_person = candidate.person_b
        merged_person.refresh_from_db()
        self.assertEqual(merged_person.status, Person.Status.MERGED)

    def test_post_additional_role_keeps_merged_primary_active(self):
        """review_decision='additional_role' でマージ成功 + 元 primary が ACTIVE のまま残る
        + DC.review_result に ['additional_role'] が保存される（D-4d-1 第 4 弾 3 値化）。
        """
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        merged_primary_pk = candidate.person_b.primary_contact.pk

        data = self._post_data(
            candidate,
            review_decision="additional_role",
            review_result=[],
        )
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 302)
        candidate.refresh_from_db()
        self.assertEqual(
            candidate.review_status,
            DuplicateCandidate.ReviewStatus.MERGED,
        )
        self.assertEqual(candidate.review_result, ["additional_role"])
        # 元 primary は person=surviving に移されつつ status=ACTIVE のまま残る
        former_primary = Contact.objects.get(pk=merged_primary_pk)
        self.assertEqual(former_primary.status, Contact.Status.ACTIVE)
        self.assertEqual(former_primary.person, candidate.person_a)

    def test_post_confirms_surviving_unconfirmed_cfc_on_merge(self):
        """surviving 側 primary に未確認 low/mid CFC + 確認 CB 全 ON → Execute_Merge_Only
        が atomic 冒頭で CFC を confirmed 化 + マージ成功（D-4d-1 第 3 弾 §2-4）。
        """
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        cfc = ContactFieldConfidence.objects.create(
            contact=candidate.person_a.primary_contact,
            field_name="full_name",
            confidence="low",
        )
        data = self._post_data(candidate, confirmed_full_name=True)
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 302)
        candidate.refresh_from_db()
        self.assertEqual(
            candidate.review_status,
            DuplicateCandidate.ReviewStatus.MERGED,
        )
        cfc.refresh_from_db()
        self.assertIsNotNone(cfc.confirmed_at)
        self.assertEqual(cfc.confirmed_by, self.user)

    def test_post_surviving_person_b_swaps_surviving_merged(self):
        """surviving_person_choice=person_b → surviving=person_b、merged=person_a が swap される。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        data = self._post_data(
            candidate, surviving_person_choice="person_b"
        )
        # surviving=person_b なので入力データを person_b の primary_contact 値に揃える
        merged_primary = candidate.person_b.primary_contact
        for f in Contact.UPDATABLE_FIELDS:
            data[f] = getattr(merged_primary, f)

        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 302)
        # person_b 側が surviving として active のまま、person_a が merged 化
        candidate.person_a.refresh_from_db()
        candidate.person_b.refresh_from_db()
        self.assertEqual(
            candidate.person_a.status, Person.Status.MERGED
        )
        self.assertEqual(
            candidate.person_b.status, Person.Status.ACTIVE
        )

    def test_post_success_redirects_to_review_url(self):
        """成功 POST → 同 URL の GET にリダイレクト（PRG パターン）。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        data = self._post_data(candidate)
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], self._url())

    def test_post_success_adds_pair_to_reviewed_session(self):
        """成功 POST → reviewed_pair_ids に pair_id 追加。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        data = self._post_data(candidate)
        self.client.post(self._url(), data)
        session_key = f"reviewed_pair_ids:{self.group_id}"
        self.assertIn(str(candidate.pk), self.client.session[session_key])

    def test_post_invalid_form_renders_review_page(self):
        """フォームバリデーション失敗 → 200 + form エラー + session 未追加 + DC 不変。"""
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        data = self._post_data(candidate, review_result=[])
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)
        self.assertTrue(resp.context["form"].errors)
        session_key = f"reviewed_pair_ids:{self.group_id}"
        self.assertNotIn(session_key, self.client.session)
        candidate.refresh_from_db()
        self.assertEqual(
            candidate.review_status,
            DuplicateCandidate.ReviewStatus.PENDING,
        )


class DuplicateCandidateGroupUpdateViewConflictTests(
    _DuplicateGroupUpdateViewPostTestBase
):
    """17 番 View 競合検出テスト（D-4c C-4 / C-5）。"""

    def test_post_already_processed_redirects_with_error(self):
        """review_status != pending → 競合エラー + GET リダイレクト + session 未追加。"""
        candidate = self._make_candidate(
            self.person_x,
            self.person_y,
            group_id=self.group_id,
            review_status=DuplicateCandidate.ReviewStatus.MERGED,
        )
        data = self._post_data(candidate)
        resp = self.client.post(self._url(), data, follow=True)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(
            any("既に他の操作で処理されました" in m for m in msgs)
        )
        session_key = f"reviewed_pair_ids:{self.group_id}"
        self.assertNotIn(session_key, self.client.session)

    def test_post_pair_id_not_in_group_redirects_with_error(self):
        """pair_id が URL group_id に属さない → 競合エラー + GET リダイレクト。"""
        other_group = uuid.uuid4()
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=other_group
        )
        data = self._post_data(candidate)
        resp = self.client.post(self._url(), data, follow=True)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(
            any("既に他の操作で処理されました" in m for m in msgs)
        )

    def test_post_invalid_pair_id_redirects_with_error(self):
        """無効な pair_id（malformed UUID）→ 競合エラー + GET リダイレクト。"""
        data = {
            "pair_id": "not-a-uuid",
            "review_decision": "merged",
            "review_result": ["same_card"],
            "surviving_person_choice": "person_a",
        }
        resp = self.client.post(self._url(), data, follow=True)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(
            any("既に他の操作で処理されました" in m for m in msgs)
        )

    def test_post_missing_pair_id_redirects_with_error(self):
        """POST に pair_id なし → 競合エラー + GET リダイレクト。"""
        data = {
            "review_decision": "merged",
            "review_result": ["same_card"],
            "surviving_person_choice": "person_a",
        }
        resp = self.client.post(self._url(), data, follow=True)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(
            any("既に他の操作で処理されました" in m for m in msgs)
        )


class DuplicateCandidateGroupUpdateViewServiceErrorTests(
    _DuplicateGroupUpdateViewPostTestBase
):
    """17 番 View サービス層 ValidationError キャッチテスト（D-4c C-6）。"""

    def test_post_service_validation_error_renders_review_page(self):
        """merged primary に未確認 CFC + additional_role → Execute_Merge_Only が
        ValidationError → レビュー画面再 render + form non_field_errors + DC 不変 +
        session 未追加。

        D-4d-1 第 3 弾 §2 修正項目 4 で surviving 側未確認は atomic 冒頭で confirmed
        化されるようになったため、ValidationError は merged 側 + additional_role 経由
        でのみ発生する。本テストはその経路の View キャッチ動作を担保する。
        """
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        ContactFieldConfidence.objects.create(
            contact=candidate.person_b.primary_contact,
            field_name="full_name",
            confidence="low",
        )

        data = self._post_data(
            candidate,
            review_decision="additional_role",
            review_result=[],
        )
        resp = self.client.post(self._url(), data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].non_field_errors())
        candidate.refresh_from_db()
        self.assertEqual(
            candidate.review_status,
            DuplicateCandidate.ReviewStatus.PENDING,
        )
        session_key = f"reviewed_pair_ids:{self.group_id}"
        self.assertNotIn(session_key, self.client.session)

    def test_additional_role_failure_keeps_merged_cfc_intact(self):
        """additional_role 失敗時 merged 側 CFC は touch されない（atomic rollback 動作）。

        D-4d-1 第 3 弾 §2-4 留意点：merged 側 CFC は触らない。
        ValidationError で rollback されるので surviving 側 CFC も含めて DB は不変。
        """
        candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )
        merged_cfc = ContactFieldConfidence.objects.create(
            contact=candidate.person_b.primary_contact,
            field_name="full_name",
            confidence="low",
        )
        data = self._post_data(
            candidate,
            review_decision="additional_role",
            review_result=[],
        )
        self.client.post(self._url(), data)
        merged_cfc.refresh_from_db()
        self.assertIsNone(merged_cfc.confirmed_at)


class DuplicateCandidateGroupUpdateViewContextTests(
    _DuplicateGroupUpdateViewTestBase
):
    """17 番 View GET の context 拡張テスト（D-4d / D-4d-1 続き §6-C）。

    マージレビュー画面の UI 強化で追加された context キーと field_groups の整形ロジック
    （両側空フィールド除外 / グループ全消え除外 / 選択肢 3 キー）を検証する。
    """

    def setUp(self):
        super().setUp()
        self.candidate = self._make_candidate(
            self.person_x, self.person_y, group_id=self.group_id
        )

    def _fill_all_fields(self, contact, prefix="filled-"):
        """contact の UPDATABLE_FIELDS を全て埋める（両側空除外を回避するためのヘルパー）。"""
        for field_name in Contact.UPDATABLE_FIELDS:
            setattr(contact, field_name, f"{prefix}{field_name}")
        contact.save()

    def test_context_includes_six_new_keys(self):
        """GET の context に前回 D-4d-1 で追加した 6 キーが含まれる。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        for key in (
            "surviving_card_image_url",
            "merged_card_image_url",
            "surviving_field_groups",
            "merged_field_groups",
            "surviving_confidences",
            "merged_confidences",
        ):
            self.assertIn(key, resp.context, msg=f"context key '{key}' missing")

    def test_context_includes_choice_keys(self):
        """GET の context に D-4d-1 続き §5 で追加した 3 キーが含まれる。"""
        resp = self.client.get(self._url())
        for key in (
            "decision_choices",
            "merged_reason_choices",
            "different_reason_choices",
        ):
            self.assertIn(key, resp.context, msg=f"context key '{key}' missing")

    def test_decision_choices_shape(self):
        """decision_choices は merged / additional_role / different の 3 要素（D-4d-1 第 4 弾 3 値化）。"""
        resp = self.client.get(self._url())
        choices = resp.context["decision_choices"]
        values = [v for v, _ in choices]
        self.assertEqual(
            set(values), {"merged", "additional_role", "different"}
        )
        self.assertEqual(len(choices), 3)

    def test_reason_choices_have_expected_lengths(self):
        """マージ系 UI 6（additional_role 除外）/ 別人系 3。"""
        resp = self.client.get(self._url())
        self.assertEqual(len(resp.context["merged_reason_choices"]), 6)
        self.assertEqual(len(resp.context["different_reason_choices"]), 3)

    def test_merged_reason_choices_excludes_additional_role(self):
        """merged_reason_choices に additional_role が含まれない（3 値化で第 1 段階へ昇格）。"""
        resp = self.client.get(self._url())
        values = [v for v, _ in resp.context["merged_reason_choices"]]
        self.assertNotIn("additional_role", values)

    def test_survivor_block_fixed_layout(self):
        """サバイブ選択ボタンがフィールド比較テーブル内の各 app-form__group 末尾に直配置
        される構造（D-4d-1 第 8 弾 §2-1）。4 種の動的見出しは field-grid 直前に集約。
        """
        resp = self.client.get(self._url())
        # 動的見出し 4 種が HTML 上に常駐（CSS の :has() で 1 つだけ表示）
        self.assertContains(resp, "app-section--survivor-unselected-label")
        self.assertContains(resp, "app-section--survivor-only")
        self.assertContains(resp, "app-section--primary-role-only")
        self.assertContains(resp, "app-section--survivor-disabled-label")
        # 各 app-form__group 内に配置された radio button（左右独立）
        self.assertContains(resp, 'class="app-merge-survivor__btn"', count=2)
        self.assertContains(resp, 'name="surviving_person_choice" value="person_a"')
        self.assertContains(resp, 'name="surviving_person_choice" value="person_b"')
        # ボタン内のラベル動的切替（2 種 × 2 カラム = 4 個）
        self.assertContains(
            resp, "app-merge-survivor__label--survivor", count=2
        )
        self.assertContains(
            resp, "app-merge-survivor__label--primary-role", count=2
        )

    def test_card_image_url_empty_when_no_business_card(self):
        """business_card 未紐付け Contact では空文字を返す。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.context["surviving_card_image_url"], "")
        self.assertEqual(resp.context["merged_card_image_url"], "")

    def test_field_groups_have_five_groups_when_filled(self):
        """両側ともフィールドが埋まっていれば 5 グループすべて出る。

        v1.6.1 で SNS グループは ContactSns 別テーブル化に伴い廃止し
        FIELD_GROUPS から削除（6 → 5 グループ）。
        """
        self._fill_all_fields(self.candidate.person_a.primary_contact)
        self._fill_all_fields(self.candidate.person_b.primary_contact)
        resp = self.client.get(self._url())
        self.assertEqual(len(resp.context["surviving_field_groups"]), 5)
        self.assertEqual(len(resp.context["merged_field_groups"]), 5)

    def test_field_groups_total_fields_match_updatable_fields(self):
        """両側ともフィールドが埋まっていれば Contact.UPDATABLE_FIELDS の全件が出る（順序は問わず）。

        v1.6.1 で UPDATABLE_FIELDS は 31 件に整理（v1.6.0 で 37 に拡張後、個別 SNS 5 件を
        ContactSns 別テーブル化、Phase E で address を除外）。集合一致は set 比較で検証する
        ため、件数自体はマスター側の変動に追従する。
        """
        self._fill_all_fields(self.candidate.person_a.primary_contact)
        self._fill_all_fields(self.candidate.person_b.primary_contact)
        resp = self.client.get(self._url())
        for side_key in ("surviving_field_groups", "merged_field_groups"):
            collected = []
            for group in resp.context[side_key]:
                for field in group["fields"]:
                    collected.append(field["field_name"])
            self.assertEqual(
                set(collected),
                set(Contact.UPDATABLE_FIELDS),
                msg=f"{side_key} field set mismatch",
            )
            self.assertEqual(
                len(collected),
                len(Contact.UPDATABLE_FIELDS),
                msg=f"{side_key} duplicate or missing fields",
            )

    def test_field_groups_field_element_shape(self):
        """各 field 要素は field_name / label / value / is_diff の 4 キーを持つ。"""
        self._fill_all_fields(self.candidate.person_a.primary_contact)
        self._fill_all_fields(self.candidate.person_b.primary_contact)
        resp = self.client.get(self._url())
        for group in resp.context["surviving_field_groups"]:
            self.assertIn("group_name", group)
            self.assertIn("fields", group)
            for field in group["fields"]:
                self.assertIn("field_name", field)
                self.assertIn("label", field)
                self.assertIn("value", field)
                self.assertIn("is_diff", field)

    def test_field_groups_value_reflects_contact(self):
        """value には Contact の現在値（full_name 等）が反映される。"""
        resp = self.client.get(self._url())
        surviving_primary = self.candidate.person_a.primary_contact
        full_name_value = None
        for group in resp.context["surviving_field_groups"]:
            for field in group["fields"]:
                if field["field_name"] == "full_name":
                    full_name_value = field["value"]
                    break
        self.assertEqual(full_name_value, surviving_primary.full_name)

    def test_field_groups_excludes_both_empty_field(self):
        """両側とも空のフィールドは行ごと除外される（D-4d-1 続き §2 修正項目 1）。"""
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        surviving_primary.email = "a@example.com"
        surviving_primary.save()
        merged_primary.email = "b@example.com"
        merged_primary.save()

        resp = self.client.get(self._url())
        surviving_field_names = [
            f["field_name"]
            for g in resp.context["surviving_field_groups"]
            for f in g["fields"]
        ]
        # email は両側に値あり → 出る
        self.assertIn("email", surviving_field_names)
        # organization は両側空 → 出ない
        self.assertNotIn("organization", surviving_field_names)
        # 個別 SNS フィールドは v1.6.1 で ContactSns 別テーブル化に伴い廃止済（Phase A3）。
        # SNS 比較表示の検証は Phase F2 で sns_type 別グルーピングとして別途実装する。

    def test_field_groups_keeps_when_only_one_side_filled(self):
        """片側だけ値がある場合、自側が空でも当該フィールドは表示される。"""
        merged_primary = self.candidate.person_b.primary_contact
        merged_primary.organization = "B社"
        merged_primary.save()

        resp = self.client.get(self._url())
        surviving_field_names = [
            f["field_name"]
            for g in resp.context["surviving_field_groups"]
            for f in g["fields"]
        ]
        merged_field_names = [
            f["field_name"]
            for g in resp.context["merged_field_groups"]
            for f in g["fields"]
        ]
        # 片側に値があれば両側の表示に含まれる
        self.assertIn("organization", surviving_field_names)
        self.assertIn("organization", merged_field_names)

    def test_field_groups_excludes_empty_group(self):
        """グループ内の全フィールドが両側空のときグループ自体が除外される。"""
        # setUp の状態は full_name のみ値あり、lang は両側ともデフォルト "ja"。
        # 「その他」グループを完全空にして除外を確認するため lang を両側で空に上書き。
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        surviving_primary.lang = ""
        surviving_primary.save()
        merged_primary.lang = ""
        merged_primary.save()

        resp = self.client.get(self._url())
        group_names = [
            g["group_name"] for g in resp.context["surviving_field_groups"]
        ]
        # 氏名グループは full_name 値ありで残る
        self.assertIn("氏名", group_names)
        # SNS グループは両側全フィールド空 → 除外
        self.assertNotIn("SNS", group_names)
        # 所属 / 連絡先 / 住所 / その他 も両側空 → 除外
        self.assertNotIn("所属", group_names)
        self.assertNotIn("連絡先", group_names)
        self.assertNotIn("住所", group_names)
        self.assertNotIn("その他", group_names)

    def test_field_groups_is_diff_true_for_different_values(self):
        """左右で値が違うフィールドは is_diff=True（D-4d-1 第 3 弾 §2-1）。"""
        # setUp で full_name は surviving='X 太郎' / merged='Y 次郎' で異なる。
        resp = self.client.get(self._url())
        full_name_item = None
        for group in resp.context["surviving_field_groups"]:
            for field in group["fields"]:
                if field["field_name"] == "full_name":
                    full_name_item = field
                    break
        self.assertIsNotNone(full_name_item)
        self.assertTrue(full_name_item["is_diff"])

    def test_field_groups_is_diff_false_for_matching_values(self):
        """左右で値が一致するフィールドは is_diff=False。"""
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        surviving_primary.email = "same@example.com"
        surviving_primary.save()
        merged_primary.email = "same@example.com"
        merged_primary.save()

        resp = self.client.get(self._url())
        email_item = None
        for group in resp.context["surviving_field_groups"]:
            for field in group["fields"]:
                if field["field_name"] == "email":
                    email_item = field
                    break
        self.assertIsNotNone(email_item)
        self.assertFalse(email_item["is_diff"])

    def test_field_groups_excludes_hidden_name_fields_when_full_match(self):
        """フルネーム一致 + 姓名一致 + 部分一致のとき last_name / first_name が
        field_groups から除外される（D-4d-1 第 3 弾 §2-3）。
        """
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        for primary in (surviving_primary, merged_primary):
            primary.full_name = "山田太郎"
            primary.last_name = "山田"
            primary.first_name = "太郎"
            primary.save()

        resp = self.client.get(self._url())
        names = set()
        for group in resp.context["surviving_field_groups"]:
            for field in group["fields"]:
                names.add(field["field_name"])
        self.assertIn("full_name", names)
        self.assertNotIn("last_name", names)
        self.assertNotIn("first_name", names)

    def test_field_groups_includes_last_name_when_no_hidden(self):
        """hidden_name_fields 条件を満たさないとき last_name は表示される。"""
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        # 姓だけ違うので hidden 条件不成立
        surviving_primary.full_name = "山田太郎"
        surviving_primary.last_name = "山田"
        surviving_primary.first_name = "太郎"
        surviving_primary.save()
        merged_primary.full_name = "山田太郎"
        merged_primary.last_name = "佐藤"
        merged_primary.first_name = "太郎"
        merged_primary.save()

        resp = self.client.get(self._url())
        names = set()
        for group in resp.context["surviving_field_groups"]:
            for field in group["fields"]:
                names.add(field["field_name"])
        self.assertIn("last_name", names)
        self.assertIn("first_name", names)

    # ------------------------------------------------------------------
    # Phase F2: ContactSns 比較表示（仕様書 §11.5.7）
    # ------------------------------------------------------------------

    def _add_sns(self, contact, sns_type, sns_id):
        return ContactSns.objects.create(
            contact=contact, sns_type=sns_type, sns_id=sns_id
        )

    def test_sns_groups_in_context(self):
        """context に surviving_sns_groups / merged_sns_groups が含まれる。"""
        resp = self.client.get(self._url())
        self.assertIn("surviving_sns_groups", resp.context)
        self.assertIn("merged_sns_groups", resp.context)

    def test_sns_groups_empty_when_no_sns(self):
        """両側に ContactSns がゼロ件のとき surviving_sns_groups / merged_sns_groups は空リスト。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.context["surviving_sns_groups"], [])
        self.assertEqual(resp.context["merged_sns_groups"], [])

    def test_sns_groups_excludes_both_empty_sns_type(self):
        """両側で 1 件もない sns_type は除外される（twitter だけ持たせて facebook は両側 0 件）。"""
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        self._add_sns(surviving_primary, "twitter", "@a")
        self._add_sns(merged_primary, "twitter", "@b")

        resp = self.client.get(self._url())
        for side_key in ("surviving_sns_groups", "merged_sns_groups"):
            sns_types = [g["sns_type"] for g in resp.context[side_key]]
            self.assertIn("twitter", sns_types)
            self.assertNotIn("facebook", sns_types)
            self.assertNotIn("linkedin", sns_types)

    def test_sns_groups_keeps_when_only_one_side_has_sns_type(self):
        """片側のみ持つ sns_type は両側に sns_type ヘッダを残す（空側は items=[]）。"""
        surviving_primary = self.candidate.person_a.primary_contact
        self._add_sns(surviving_primary, "twitter", "@a")

        resp = self.client.get(self._url())
        surviving_groups = resp.context["surviving_sns_groups"]
        merged_groups = resp.context["merged_sns_groups"]

        self.assertEqual([g["sns_type"] for g in surviving_groups], ["twitter"])
        self.assertEqual([g["sns_type"] for g in merged_groups], ["twitter"])
        # 自側は 1 件、対面側は items=[]（テンプレで「（なし）」を出すため）
        self.assertEqual(len(surviving_groups[0]["items"]), 1)
        self.assertEqual(merged_groups[0]["items"], [])

    def test_sns_groups_is_diff_true_for_unique_sns_id(self):
        """片側のみ持つ sns_id は is_diff=True、両側で同じ (sns_type, sns_id) は is_diff=False。"""
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        self._add_sns(surviving_primary, "twitter", "@common")
        self._add_sns(surviving_primary, "twitter", "@unique_to_a")
        self._add_sns(merged_primary, "twitter", "@common")
        self._add_sns(merged_primary, "twitter", "@unique_to_b")

        resp = self.client.get(self._url())
        surviving_items = resp.context["surviving_sns_groups"][0]["items"]
        diff_map = {item["sns_id"]: item["is_diff"] for item in surviving_items}
        self.assertEqual(diff_map["@common"], False)
        self.assertEqual(diff_map["@unique_to_a"], True)

    def test_sns_groups_is_diff_false_for_matching_sns_id(self):
        """両側で同じ (sns_type, sns_id) を持つ行は is_diff=False（自側からも対面側からも見て）。"""
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        self._add_sns(surviving_primary, "linkedin", "/in/jdoe")
        self._add_sns(merged_primary, "linkedin", "/in/jdoe")

        resp = self.client.get(self._url())
        for side_key in ("surviving_sns_groups", "merged_sns_groups"):
            groups = resp.context[side_key]
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["sns_type"], "linkedin")
            self.assertEqual(groups[0]["items"][0]["is_diff"], False)

    def test_sns_groups_sorted_by_sns_type_choices(self):
        """sns_type の並び順は ContactSns.SnsType.choices 定義順を保つ。"""
        surviving_primary = self.candidate.person_a.primary_contact
        # 投入順は choices 順と逆にして並び替えが効いていることを確認
        self._add_sns(surviving_primary, "line", "line_id")
        self._add_sns(surviving_primary, "facebook", "fb_id")
        self._add_sns(surviving_primary, "twitter", "@a")

        resp = self.client.get(self._url())
        sns_types = [g["sns_type"] for g in resp.context["surviving_sns_groups"]]
        # ContactSns.SnsType.choices 定義順：twitter, linkedin, facebook, instagram,
        # github, blog, youtube, line のうち、登録済みのものだけがこの順序で並ぶ。
        self.assertEqual(sns_types, ["twitter", "facebook", "line"])

    def test_template_renders_sns_groups(self):
        """レンダリング時に SNS 比較ブロック見出しと sns_type ラベルが表示される。"""
        surviving_primary = self.candidate.person_a.primary_contact
        self._add_sns(surviving_primary, "twitter", "@displayed")

        resp = self.client.get(self._url())
        self.assertContains(resp, "SNS 比較")
        self.assertContains(resp, "Twitter")
        self.assertContains(resp, "@displayed")
        # 対面側は items=[] なので「（なし）」が出る
        self.assertContains(resp, "（なし）")

    def test_template_renders_diff_class_for_diff_sns(self):
        """is_diff の行に app-sns-compare__item--diff クラスが付く。一致行には付かない。"""
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        self._add_sns(surviving_primary, "twitter", "@same")
        self._add_sns(surviving_primary, "twitter", "@unique_a")
        self._add_sns(merged_primary, "twitter", "@same")
        self._add_sns(merged_primary, "twitter", "@unique_b")

        resp = self.client.get(self._url())
        # 片側のみ持つ sns_id 2 件（@unique_a, @unique_b）に diff クラスが付く
        self.assertContains(resp, "app-sns-compare__item--diff", count=2)
        # @same は両側に出るので合計 2 回（左右）レンダリングされるが、diff クラスなし
        self.assertContains(resp, "@same")
        self.assertContains(resp, "@unique_a")
        self.assertContains(resp, "@unique_b")

    def test_sns_accounts_prefetch_avoids_n_plus_one(self):
        """sns_accounts を prefetch しているため、SNS 件数を増やしてもクエリ数は一定。

        ContactSns prefetch は 2 クエリ固定（surviving / merged の sns_accounts を
        それぞれ 1 回ずつ IN 句で取得）。15 件追加してもこの 2 クエリで完結する。
        prefetch_related を外すと sns_accounts.all() がテンプレループ内で発行され
        クエリ数が +10〜20 に膨らむため、その回帰を検出する。
        """
        surviving_primary = self.candidate.person_a.primary_contact
        merged_primary = self.candidate.person_b.primary_contact
        for i in range(5):
            self._add_sns(surviving_primary, "twitter", f"@a{i}")
            self._add_sns(merged_primary, "twitter", f"@b{i}")
            self._add_sns(surviving_primary, "facebook", f"fb_a_{i}")

        with self.assertNumQueries(10):
            self.client.get(self._url())


class _PersonMergeLogViewTestBase(_DuplicatesTestBase):
    """マージログ View 共通：surviving / merged Person + Contact + PersonMergeLog を作る。"""

    def setUp(self):
        super().setUp()
        self.surviving, self.surviving_contact = self._make_person_with_primary(
            "サバイブ太郎", created_by=self.user
        )
        self.merged, self.merged_contact = self._make_person_with_primary(
            "統合元次郎", created_by=self.user
        )
        # merged Contact を surviving 配下に付け替え（復元プレビューの
        # contacts_to_restore に乗るよう previous_person=merged をセット）。
        self.merged_contact.person = self.surviving
        self.merged_contact.previous_person = self.merged
        self.merged_contact.previous_status = Contact.Status.PRIMARY
        self.merged_contact.status = Contact.Status.INACTIVE
        self.merged_contact.save()

    def _make_log(self, *, user=None, status=None, executed_at=None):
        log = PersonMergeLog.create(self.surviving, self.merged, user or self.user)
        if status is not None and status != PersonMergeLog.Status.UNDOABLE:
            log.status = status
            log.save(update_fields=["status", "updated_at"])
        if executed_at is not None:
            log.executed_at = executed_at
            log.save(update_fields=["executed_at", "updated_at"])
        return log


class PersonMergeLogListViewTests(_PersonMergeLogViewTestBase):
    """19 番 PersonMergeLogListView の単体テスト（D-4f-1）。"""

    def _url(self):
        return reverse("duplicates:merge_log_list")

    def test_unauthenticated_redirects(self):
        c = Client()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)

    def test_initial_shows_only_undoable(self):
        """初回アクセス（searched なし）は undoable のみ表示。"""
        self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        self._make_log(status=PersonMergeLog.Status.UNDONE)
        self._make_log(status=PersonMergeLog.Status.LOCKED)

        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        statuses = {log.status for log in resp.context["merge_logs"]}
        self.assertEqual(statuses, {PersonMergeLog.Status.UNDOABLE})

    def test_searched_with_multiple_statuses(self):
        """searched=1 + status=undoable&status=undone → 両方表示。"""
        self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        self._make_log(status=PersonMergeLog.Status.UNDONE)
        self._make_log(status=PersonMergeLog.Status.LOCKED)

        resp = self.client.get(
            self._url() + "?searched=1&status=undoable&status=undone"
        )
        statuses = {log.status for log in resp.context["merge_logs"]}
        self.assertEqual(
            statuses,
            {PersonMergeLog.Status.UNDOABLE, PersonMergeLog.Status.UNDONE},
        )

    def test_user_filter_me(self):
        """user=me → executed_by=ログインユーザーのみ。"""
        my_log = self._make_log(user=self.user)
        self._make_log(user=self.other_user)

        resp = self.client.get(self._url() + "?searched=1&status=undoable&user=me")
        log_ids = [log.id for log in resp.context["merge_logs"]]
        self.assertIn(my_log.id, log_ids)
        self.assertEqual(len(log_ids), 1)

    def test_ordering_by_executed_at_desc(self):
        """-executed_at 降順（最新マージが上）。"""
        old = self._make_log(executed_at=timezone.now() - timedelta(days=2))
        new = self._make_log(executed_at=timezone.now() - timedelta(hours=1))

        resp = self.client.get(self._url())
        log_ids = [log.id for log in resp.context["merge_logs"]]
        self.assertEqual(log_ids[0], new.id)
        self.assertEqual(log_ids[1], old.id)

    def test_html_contains_person_names(self):
        """HTML に surviving / merged の primary_contact.full_name が含まれる。"""
        self._make_log()
        resp = self.client.get(self._url())
        self.assertContains(resp, "サバイブ太郎")
        self.assertContains(resp, "統合元次郎")

    def test_pagination(self):
        """20 件超で次ページに分かれる。"""
        for _ in range(21):
            self._make_log()
        resp = self.client.get(self._url())
        self.assertTrue(resp.context["is_paginated"])
        self.assertEqual(len(resp.context["merge_logs"]), 20)


class PersonMergeLogDetailViewTests(_PersonMergeLogViewTestBase):
    """20 番 PersonMergeLogDetailView の単体テスト（D-4f-1）。"""

    def _url(self, pk):
        return reverse("duplicates:merge_log_detail", kwargs={"pk": pk})

    def test_unauthenticated_redirects(self):
        log = self._make_log()
        c = Client()
        resp = c.get(self._url(log.pk))
        self.assertEqual(resp.status_code, 302)

    def test_nonexistent_pk_returns_404(self):
        resp = self.client.get(self._url(uuid.uuid4()))
        self.assertEqual(resp.status_code, 404)

    def test_context_basic_keys(self):
        log = self._make_log()
        resp = self.client.get(self._url(log.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["merge_log"].pk, log.pk)
        self.assertEqual(resp.context["surviving_person"].pk, self.surviving.pk)
        self.assertEqual(resp.context["merged_person"].pk, self.merged.pk)
        self.assertTrue(resp.context["is_undoable"])
        self.assertIn("undo_preview", resp.context)

    def test_html_shows_names_and_status(self):
        """HTML に surviving / merged 氏名 + status バッジが含まれる。"""
        log = self._make_log()
        resp = self.client.get(self._url(log.pk))
        self.assertContains(resp, "サバイブ太郎")
        self.assertContains(resp, "統合元次郎")
        self.assertContains(resp, "復元可能")

    def test_labels_use_business_terms_not_internal(self):
        """見出し・復元注記が業務語で、内部語（merged_person/active/Contact 英字）を出さない
        （HIG v1.4 原則4/7）。"""
        log = self._make_log()
        resp = self.client.get(self._url(log.pk))
        # 業務語が出る
        self.assertContains(resp, "マージド側")
        self.assertContains(resp, "復元するとマージド側が有効状態に戻ります。")
        self.assertContains(resp, "に戻る連絡先")
        self.assertContains(resp, "サバイブ側に残る連絡先")
        # 内部語は出ない
        self.assertNotContains(resp, "merged_person")
        self.assertNotContains(resp, "active 状態")
        self.assertNotContains(resp, "に戻る Contact")
        self.assertNotContains(resp, "残る Contact")

    def test_undo_preview_shows_contacts_to_restore(self):
        """get_undo_preview の contacts_to_restore が画面に出る。"""
        log = self._make_log()
        resp = self.client.get(self._url(log.pk))
        # setUp で merged_contact を surviving 配下に移動 + previous_person=merged
        # なので contacts_to_restore に乗る
        preview = resp.context["undo_preview"]
        self.assertEqual(
            list(preview["contacts_to_restore"].values_list("pk", flat=True)),
            [self.merged_contact.pk],
        )

    def test_undo_button_shown_when_undoable(self):
        """is_undoable=True のとき復元ボタン関連 HTML が表示される。"""
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        resp = self.client.get(self._url(log.pk))
        self.assertContains(resp, "このマージを復元する")

    def test_undo_button_hidden_when_undone(self):
        """undone（is_undoable=False）のとき復元ボタンは表示されない。"""
        log = self._make_log(status=PersonMergeLog.Status.UNDONE)
        resp = self.client.get(self._url(log.pk))
        self.assertNotContains(resp, "このマージを復元する")

    def test_undo_button_hidden_when_locked(self):
        """locked（is_undoable=False）のとき復元ボタンは表示されない。"""
        log = self._make_log(status=PersonMergeLog.Status.LOCKED)
        resp = self.client.get(self._url(log.pk))
        self.assertNotContains(resp, "このマージを復元する")


class MergeUndoFormTests(TestCase):
    """MergeUndoForm の単体テスト（D-4f-2 §6-A）。"""

    def test_valid_with_confirmed_only(self):
        form = MergeUndoForm(data={"confirmed": "on"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["note"], "")

    def test_valid_with_confirmed_and_note(self):
        form = MergeUndoForm(data={"confirmed": "on", "note": "誤マージ"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["note"], "誤マージ")

    def test_invalid_without_confirmed(self):
        form = MergeUndoForm(data={"note": "理由"})
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed", form.errors)

    def test_invalid_when_all_empty(self):
        form = MergeUndoForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed", form.errors)

    def test_field_errors_use_app_form_error_class(self):
        """AppErrorList が効いて errorlist app-form__error が付与される。"""
        form = MergeUndoForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn(
            'class="errorlist app-form__error"',
            str(form["confirmed"].errors),
        )


class PersonMergeLogConfirmUndoViewTests(_PersonMergeLogViewTestBase):
    """21 番 PersonMergeLogConfirmUndoView の単体テスト（D-4f-2 §6-B / §6-C）。"""

    def _url(self, pk):
        return reverse("duplicates:merge_log_confirm_undo", kwargs={"pk": pk})

    def _detail_url(self, pk):
        return reverse("duplicates:merge_log_detail", kwargs={"pk": pk})

    def _prepare_merged_state(self):
        """Execute_Merge_Undo 実行可能なマージ後状態に merged Person を整える
        （mark_as_active() のガード `status in (MERGED, ARCHIVED)` を通すため）。
        """
        self.merged.status = Person.Status.MERGED
        self.merged.merged_into = self.surviving
        self.merged.primary_contact = None
        self.merged.save()

    # ---- GET ----
    def test_get_unauthenticated_redirects(self):
        log = self._make_log()
        c = Client()
        resp = c.get(self._url(log.pk))
        self.assertEqual(resp.status_code, 302)

    def test_get_nonexistent_pk_returns_404(self):
        resp = self.client.get(self._url(uuid.uuid4()))
        self.assertEqual(resp.status_code, 404)

    def test_get_undoable_shows_confirm_page(self):
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        resp = self.client.get(self._url(log.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.context["form"], MergeUndoForm)
        self.assertIn("undo_preview", resp.context)

    def test_confirm_page_uses_business_terms_not_internal(self):
        """確認本文・見出しが業務語で、内部語（active/Contact 英字）を出さない
        （HIG v1.4 原則4/7）。「取り消せません」明示は維持。"""
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        resp = self.client.get(self._url(log.pk))
        self.assertEqual(resp.status_code, 200)
        # 業務語が出る
        self.assertContains(resp, "マージド側")
        self.assertContains(resp, "有効状態に戻し")
        self.assertContains(resp, "件の連絡先が")
        self.assertContains(resp, "この操作は取り消せません。")
        # 内部語は出ない
        self.assertNotContains(resp, "active 状態")
        self.assertNotContains(resp, "件の Contact が")
        self.assertNotContains(resp, "に戻る Contact")

    def test_get_undone_redirects_with_error(self):
        log = self._make_log(status=PersonMergeLog.Status.UNDONE)
        resp = self.client.get(self._url(log.pk), follow=True)
        self.assertRedirects(resp, self._detail_url(log.pk))
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("復元できません" in m for m in msgs))

    def test_get_locked_redirects_with_error(self):
        log = self._make_log(status=PersonMergeLog.Status.LOCKED)
        resp = self.client.get(self._url(log.pk), follow=True)
        self.assertRedirects(resp, self._detail_url(log.pk))

    # ---- POST ----
    def test_post_valid_executes_undo(self):
        """valid POST → Execute_Merge_Undo 実行 → status=undone + 成功 message + 20 番リダイレクト。"""
        self._prepare_merged_state()
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        resp = self.client.post(
            self._url(log.pk),
            {"confirmed": "on", "note": "誤マージのため復元"},
            follow=True,
        )
        self.assertRedirects(resp, self._detail_url(log.pk))
        log.refresh_from_db()
        self.assertEqual(log.status, PersonMergeLog.Status.UNDONE)
        self.assertEqual(log.undone_by, self.user)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("マージを復元しました" in m for m in msgs))

    def test_post_valid_records_action_log_with_note(self):
        """valid POST → ActionLog に action='undone' + data={"note": ...} 記録。"""
        self._prepare_merged_state()
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        self.client.post(
            self._url(log.pk),
            {"confirmed": "on", "note": "復元理由テスト"},
        )
        action = ActionLog.objects.filter(action="undone").last()
        self.assertIsNotNone(action)
        self.assertEqual(action.data, {"note": "復元理由テスト"})

    def test_post_valid_does_not_change_merge_log_note(self):
        """復元 note は merge_log.note に書かれない（マージ時 note と分離）。"""
        self._prepare_merged_state()
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        log.note = "マージ時のメモ"
        log.save(update_fields=["note", "updated_at"])
        self.client.post(
            self._url(log.pk),
            {"confirmed": "on", "note": "復元 note"},
        )
        log.refresh_from_db()
        self.assertEqual(log.note, "マージ時のメモ")

    def test_post_invalid_renders_confirm_page(self):
        """invalid（confirmed なし）→ 200 + form エラー + DB 不変。"""
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        resp = self.client.post(self._url(log.pk), {"note": "理由のみ"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors)
        log.refresh_from_db()
        self.assertEqual(log.status, PersonMergeLog.Status.UNDOABLE)

    def test_post_conflict_when_status_changed(self):
        """POST 時に既に undone（競合）→ 復元実行されず 20 番リダイレクト + error。"""
        log = self._make_log(status=PersonMergeLog.Status.UNDONE)
        resp = self.client.post(
            self._url(log.pk),
            {"confirmed": "on"},
            follow=True,
        )
        self.assertRedirects(resp, self._detail_url(log.pk))
        log.refresh_from_db()
        self.assertEqual(log.status, PersonMergeLog.Status.UNDONE)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("復元できません" in m for m in msgs))


class PersonMergeLogRecordUndoActionTests(_PersonMergeLogViewTestBase):
    """PersonMergeLog.record_undo_action() の単体テスト（D-4f-2 §6-D）。"""

    def test_record_with_default_empty_note(self):
        """デフォルト note='' → data={"note": ""}。"""
        log = self._make_log()
        log.record_undo_action(self.user)
        action = ActionLog.objects.filter(action="undone").last()
        self.assertEqual(action.data, {"note": ""})

    def test_record_with_note(self):
        """note 指定 → data={"note": <値>}。"""
        log = self._make_log()
        log.record_undo_action(self.user, note="復元理由 X")
        action = ActionLog.objects.filter(action="undone").last()
        self.assertEqual(action.data, {"note": "復元理由 X"})


class PersonMergeLogDetailUndoLinkTests(_PersonMergeLogViewTestBase):
    """20 番テンプレの復元リンクが 21 番 confirm-undo を指すこと（D-4f-2 §6-E）。"""

    def _url(self, pk):
        return reverse("duplicates:merge_log_detail", kwargs={"pk": pk})

    def test_undo_link_points_to_confirm_undo(self):
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        resp = self.client.get(self._url(log.pk))
        confirm_undo_url = reverse(
            "duplicates:merge_log_confirm_undo", kwargs={"pk": log.pk}
        )
        # append_back_url で ?back=... が付くため、URL の先頭部分のみで照合
        self.assertContains(resp, confirm_undo_url)
        self.assertContains(resp, "このマージを復元する")


class Phase7DuplicatesViewAuthTests(_PersonMergeLogViewTestBase):
    """Phase 7 段2-A：マージ系 View の Permission ガード（URL一覧表 rev20 ★1）。

    No.15-17（一覧/詳細/レビュー）= persons.merge_person、No.21（復元）= persons.undo_merge。
    所有者判定なし（duplicates は owner が一意に定まらないため権限ベースで割り切り）。
    破壊的 2 View（レビュー POST=マージ実行 / 復元 POST）は 403 時に DB が不変であることまで検証。
    """

    def setUp(self):
        super().setUp()
        # 権限なしユーザー（merge_person も undo_merge も持たない）
        self.noperm = User.objects.create_user(
            username="dup_noperm", password="dummy"
        )
        self.noperm_client = Client()
        self.noperm_client.force_login(self.noperm)
        # 一覧/詳細/レビュー用の pending 候補（group_id 付き、未マージの新規 Person 2 体で作る。
        # base の surviving/merged は merge_log 用に Contact を付け替え済みなので流用しない）。
        self.group_id = uuid.uuid4()
        self.pa, _ = self._make_person_with_primary("候補甲", created_by=self.user)
        self.pb, _ = self._make_person_with_primary("候補乙", created_by=self.user)
        self.candidate = self._make_candidate(
            self.pa, self.pb, group_id=self.group_id
        )

    # ---- No.15 一覧: persons.merge_person ----
    def test_list_requires_merge_person(self):
        url = reverse("duplicates:duplicate_group_list")
        self.assertEqual(self.noperm_client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)

    # ---- No.16 詳細: persons.merge_person ----
    def test_detail_requires_merge_person(self):
        url = reverse(
            "duplicates:duplicate_group_detail", kwargs={"group_id": self.group_id}
        )
        self.assertEqual(self.noperm_client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)

    # ---- No.17 レビュー GET: persons.merge_person ----
    def test_review_get_requires_merge_person(self):
        url = reverse(
            "duplicates:duplicate_group_review", kwargs={"group_id": self.group_id}
        )
        self.assertEqual(self.noperm_client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)

    # ---- No.17 レビュー POST（破壊的＝マージ実行）: 403 時にマージ未実行 ----
    def test_review_post_forbidden_does_not_merge(self):
        url = reverse(
            "duplicates:duplicate_group_review", kwargs={"group_id": self.group_id}
        )
        resp = self.noperm_client.post(url, data={})
        self.assertEqual(resp.status_code, 403)
        # 候補は PENDING のまま・マージは実行されていない（DB 状態不変）
        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.review_status, DuplicateCandidate.ReviewStatus.PENDING
        )
        self.pa.refresh_from_db()
        self.pb.refresh_from_db()
        self.assertEqual(self.pa.status, Person.Status.ACTIVE)
        self.assertEqual(self.pb.status, Person.Status.ACTIVE)

    # ---- No.21 復元 GET: persons.undo_merge ----
    def test_confirm_undo_get_requires_undo_merge(self):
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        url = reverse(
            "duplicates:merge_log_confirm_undo", kwargs={"pk": log.pk}
        )
        self.assertEqual(self.noperm_client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)

    # ---- No.21 復元 POST（破壊的＝復元実行）: 403 時に復元未実行 ----
    def test_confirm_undo_post_forbidden_does_not_undo(self):
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        url = reverse(
            "duplicates:merge_log_confirm_undo", kwargs={"pk": log.pk}
        )
        resp = self.noperm_client.post(url, data={})
        self.assertEqual(resp.status_code, 403)
        # ログは UNDOABLE のまま・復元は実行されていない（DB 状態不変）
        log.refresh_from_db()
        self.assertEqual(log.status, PersonMergeLog.Status.UNDOABLE)

    # ---- No.19 マージログ一覧: persons.merge_person（Phase 7 段3-2）----
    def test_merge_log_list_requires_merge_person(self):
        url = reverse("duplicates:merge_log_list")
        self.assertEqual(self.noperm_client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)

    # ---- No.20 マージログ詳細: persons.merge_person（Phase 7 段3-2）----
    def test_merge_log_detail_requires_merge_person(self):
        log = self._make_log(status=PersonMergeLog.Status.UNDOABLE)
        url = reverse("duplicates:merge_log_detail", kwargs={"pk": log.pk})
        self.assertEqual(self.noperm_client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)
