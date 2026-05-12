"""duplicates アプリのテスト（View 層 / Form 層）。

DuplicateCandidateGroupListViewTests：15 番（一覧、絞り込み、D-4e）。
DuplicateCandidateGroupDetailViewTests：16 番（詳細、表示切替、D-4e）。
MergeFormInitTests / MergeFormCleanTests / MergeFormHelpersTests：
    17 番マージ画面用 Form（仕様書 §11.6.2 / §11.7.3、D-4a）。
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contacts.models import Contact, ContactFieldConfidence
from duplicates.forms import MergeForm
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
        """正常系の最小フォームデータ（UPDATABLE_FIELDS の surviving 現在値 + 必須項目）。"""
        data = {
            f: getattr(self.surviving_primary, f)
            for f in Contact.UPDATABLE_FIELDS
        }
        data["review_result"] = ["same_card"]
        data["merge_reason"] = "same_card"
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
        self.surviving_primary.company = "サバイブ社"
        self.surviving_primary.email = "alive@example.com"
        self.surviving_primary.save()

        form = self._make_form()
        self.assertEqual(form.initial["full_name"], "生存太郎")
        self.assertEqual(form.initial["company"], "サバイブ社")
        self.assertEqual(form.initial["email"], "alive@example.com")

    def test_dynamic_confirm_checkboxes_added_for_low_mid_unconfirmed(self):
        """surviving 側 low/mid 未確認の DUPLICATE_CHECK_FIELDS に CB が動的追加される。"""
        self._set_cfc(self.surviving_primary, "full_name", confidence="low")
        self._set_cfc(self.surviving_primary, "email", confidence="medium")
        # confirmed 済みは追加対象外
        self._set_cfc(
            self.surviving_primary,
            "phone",
            confidence="low",
            confirmed=True,
        )

        form = self._make_form()
        self.assertIn("confirmed_full_name", form.fields)
        self.assertIn("confirmed_email", form.fields)
        self.assertNotIn("confirmed_phone", form.fields)
        # CFC レコードなし（疑似 high）にも追加されない
        self.assertNotIn("confirmed_company", form.fields)

    def test_value_diff_and_match_classification(self):
        """DUPLICATE_CHECK_FIELDS で値違い / 値一致が正しく分類される。"""
        self.surviving_primary.company = "A社"
        self.surviving_primary.email = "a@example.com"
        self.surviving_primary.save()

        self.merged_primary.company = "A社"  # 一致
        self.merged_primary.email = "b@example.com"  # 不一致
        self.merged_primary.save()

        form = self._make_form()
        # setUp で full_name は surviving/merged で異なる
        self.assertIn("full_name", form.value_diff_fields())
        self.assertIn("email", form.value_diff_fields())
        self.assertIn("company", form.value_match_fields())
        # 値違いと値一致は排他
        self.assertNotIn("email", form.value_match_fields())
        self.assertNotIn("company", form.value_diff_fields())


class MergeFormCleanTests(_MergeFormTestBase):
    """MergeForm.clean() の 6 項目バリデーションテスト（D-4a）。"""

    def test_review_result_empty_invalid(self):
        data = self._valid_data(review_result=[])
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_review_result_mixed_merged_and_different_invalid(self):
        data = self._valid_data(review_result=["same_card", "same_name"])
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("review_result", form.errors)

    def test_other_merged_without_note_invalid(self):
        data = self._valid_data(
            review_result=["other_merged"],
            merge_reason="other_merged",
            note="",
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("note", form.errors)

    def test_other_merged_with_note_valid(self):
        data = self._valid_data(
            review_result=["other_merged"],
            merge_reason="other_merged",
            note="その他のメモ",
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_other_different_without_note_invalid(self):
        data = self._valid_data(
            review_result=["other_different"],
            merge_reason="",  # different 系なので不要
            note="",
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("note", form.errors)

    def test_merge_reason_required_when_merged_series(self):
        """merged 系のみ選択時に merge_reason 未指定 → invalid。"""
        data = self._valid_data(
            review_result=["same_card"],
            merge_reason="",
        )
        form = self._make_form(data)
        self.assertFalse(form.is_valid())
        self.assertIn("merge_reason", form.errors)

    def test_merge_reason_not_required_when_different_series(self):
        """different 系のみ選択時は merge_reason 不要。"""
        data = self._valid_data(
            review_result=["same_name"],
            merge_reason="",
        )
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)

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

    def test_valid_form_passes(self):
        """全項目正常 → valid。"""
        data = self._valid_data()
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)


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
        self._set_cfc(self.surviving_primary, "company", confidence="low")
        data = self._valid_data(confirmed_company=True)
        form = self._make_form(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("company", form.confirmed_field_names())

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
        # company は空文字同士で一致
        self.assertNotIn("company", diff)

    def test_value_match_fields_returns_match_only(self):
        """value_match_fields() は DUPLICATE_CHECK_FIELDS の値一致のみ返す。"""
        form = self._make_form()
        match = form.value_match_fields()
        # company / email / phone 等は空文字同士で一致
        self.assertIn("company", match)
        self.assertIn("email", match)
        # full_name は不一致
        self.assertNotIn("full_name", match)
