"""duplicates アプリの View 層テスト（仕様書 §11.3 / §11.5、D-4e）。

DuplicateCandidateGroupListViewTests：15 番（一覧、絞り込み）。
DuplicateCandidateGroupDetailViewTests：16 番（詳細、表示切替）。
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from contacts.models import Contact
from duplicates.models import DuplicateCandidate
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

    def test_pagination_21_records_split_to_2_pages(self):
        """21 件以上で 2 ページ目に分かれる（paginate_by=20）。"""
        for i in range(21):
            p1, _ = self._make_person_with_primary(f"a-{i:02d}")
            p2, _ = self._make_person_with_primary(f"b-{i:02d}")
            self._make_candidate(p1, p2, group_id=uuid.uuid4())

        resp = self.client.get(self.url)
        self.assertTrue(resp.context["is_paginated"])
        self.assertEqual(
            len(list(resp.context["enriched_groups"])), 20
        )

        resp2 = self.client.get(self.url, {"page": "2"})
        self.assertEqual(
            len(list(resp2.context["enriched_groups"])), 1
        )


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
