"""duplicates アプリのテスト（View 層 / Form 層）。

DuplicateCandidateGroupListViewTests：15 番（一覧、絞り込み、D-4e）。
DuplicateCandidateGroupDetailViewTests：16 番（詳細、表示切替、D-4e）。
MergeFormInitTests / MergeFormCleanTests / MergeFormHelpersTests：
    17 番マージ画面用 Form（仕様書 §11.6.2 / §11.7.3、D-4a）。
DuplicateCandidateGroupUpdateViewGetTests / SessionTests / RedirectTests /
MergeFormInitTests：17 番レビュー画面 GET（仕様書 §11.5.2、D-4b）。
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

    def test_field_groups_have_six_groups_when_filled(self):
        """両側ともフィールドが埋まっていれば 6 グループすべて出る。"""
        self._fill_all_fields(self.candidate.person_a.primary_contact)
        self._fill_all_fields(self.candidate.person_b.primary_contact)
        resp = self.client.get(self._url())
        self.assertEqual(len(resp.context["surviving_field_groups"]), 6)
        self.assertEqual(len(resp.context["merged_field_groups"]), 6)

    def test_field_groups_total_fields_match_updatable_fields(self):
        """両側ともフィールドが埋まっていれば全 24 フィールドが出る（順序は問わず）。"""
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
        # company は両側空 → 出ない
        self.assertNotIn("company", surviving_field_names)
        # twitter / instagram などの SNS も両側空 → 出ない
        self.assertNotIn("twitter", surviving_field_names)

    def test_field_groups_keeps_when_only_one_side_filled(self):
        """片側だけ値がある場合、自側が空でも当該フィールドは表示される。"""
        merged_primary = self.candidate.person_b.primary_contact
        merged_primary.company = "B社"
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
        self.assertIn("company", surviving_field_names)
        self.assertIn("company", merged_field_names)

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
