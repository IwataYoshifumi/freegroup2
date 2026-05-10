"""Contact モデルの単体テスト。"""

import inspect
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from contacts.models import Contact, ContactFieldConfidence
from duplicates.models import DuplicateCandidate
from persons.models import Person


User = get_user_model()


class ContactUpdateFieldTests(TestCase):
    """Contact.update_field() の単体テスト（D-3a / §10.6.4 ケース 4）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="d3a_test_user", password="dummy"
        )
        # surviving 側 Person + Contact（編集対象）
        self.person_a = Person.objects.create()
        self.contact_a = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.PRIMARY,
            full_name="A-name",
            company="A-company",
            notes="A-notes",
        )
        self.person_a.primary_contact = self.contact_a
        self.person_a.save(update_fields=["primary_contact", "updated_at"])

        # 別 Person + (A,B) pending DC（§12.7 invalidate 確認用）
        self.person_b = Person.objects.create()
        self.contact_b = Contact.objects.create(
            person=self.person_b,
            status=Contact.Status.PRIMARY,
            full_name="B-name",
            company="B-company",
        )
        self.person_b.primary_contact = self.contact_b
        self.person_b.save(update_fields=["primary_contact", "updated_at"])

        if self.person_a.id < self.person_b.id:
            pa, pb = self.person_a, self.person_b
        else:
            pa, pb = self.person_b, self.person_a
        self.candidate = DuplicateCandidate.objects.create(
            person_a=pa,
            person_b=pb,
            score=120,
            rank=DuplicateCandidate.Rank.POSSIBLE_MID,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
            review_result=[],
        )

        # contact_a の company に medium CFC を作成（after-confirmed 検証用）
        self.cfc_company = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        # contact_a の notes に low CFC を作成（DUPLICATE_CHECK_FIELDS 外の confirmed 化検証用）
        self.cfc_notes = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="notes",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        # contact_a の duplicate_checked_at をセット（invalidate で NULL 化される検証用）
        from django.utils import timezone

        self.contact_a.duplicate_checked_at = timezone.now()
        self.contact_a.save(update_fields=["duplicate_checked_at", "updated_at"])

    # ------------------------------------------------------------------
    # 正常系
    # ------------------------------------------------------------------

    def test_updates_duplicate_check_field(self):
        """N1: DUPLICATE_CHECK_FIELDS のフィールド（company）を修正。"""
        self.contact_a.update_field("company", "A-company-new", self.user)

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.company, "A-company-new")

        # CFC が confirmed 化されている
        self.cfc_company.refresh_from_db()
        self.assertIsNotNone(self.cfc_company.confirmed_at)
        self.assertEqual(self.cfc_company.confirmed_by_id, self.user.id)

        # pending DC が invalidated 化されている
        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.review_status,
            DuplicateCandidate.ReviewStatus.INVALIDATED,
        )

        # contact_a.duplicate_checked_at が NULL 化されている
        self.assertIsNone(self.contact_a.duplicate_checked_at)

    def test_updates_non_duplicate_check_field(self):
        """N2: DUPLICATE_CHECK_FIELDS 外のフィールド（notes）を修正。"""
        # 検証用：duplicate_checked_at を保持
        original_dca = self.contact_a.duplicate_checked_at
        self.assertIsNotNone(original_dca)

        self.contact_a.update_field("notes", "A-notes-new", self.user)

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.notes, "A-notes-new")

        # CFC が confirmed 化される（notes も CFC レコードあり）
        self.cfc_notes.refresh_from_db()
        self.assertIsNotNone(self.cfc_notes.confirmed_at)

        # pending DC は影響を受けない
        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.review_status,
            DuplicateCandidate.ReviewStatus.PENDING,
        )

        # contact_a.duplicate_checked_at は変化しない
        self.assertEqual(self.contact_a.duplicate_checked_at, original_dca)

    def test_no_value_diff_skips_save(self):
        """N3: 差分なし（DUPLICATE_CHECK_FIELDS 外）→ self.save が呼ばれない。

        DUPLICATE_CHECK_FIELDS 外（notes）かつ差分なしのシナリオなら、
        invalidate_pending_candidates も呼ばれず Contact 自体への save は発生しない。
        DUPLICATE_CHECK_FIELDS 内かつ差分なしの場合は invalidate 経路で save される
        ため updated_at が更新される（仕様）。
        """
        original_updated_at = self.contact_a.updated_at

        # 同じ値を渡す（notes は DUPLICATE_CHECK_FIELDS 外）
        self.contact_a.update_field("notes", "A-notes", self.user)

        self.contact_a.refresh_from_db()
        # updated_at は変わらない（self.save も invalidate 経路の save も発生しない）
        self.assertEqual(self.contact_a.updated_at, original_updated_at)
        self.assertEqual(self.contact_a.notes, "A-notes")

        # CFC は confirmed 化される
        self.cfc_notes.refresh_from_db()
        self.assertIsNotNone(self.cfc_notes.confirmed_at)

        # DUPLICATE_CHECK_FIELDS 外なので invalidate は呼ばれない（pending のまま）
        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.review_status,
            DuplicateCandidate.ReviewStatus.PENDING,
        )

    def test_no_value_diff_with_duplicate_check_field_runs_invalidate(self):
        """N3': 差分なし + DUPLICATE_CHECK_FIELDS 内 → CFC confirmed 化と invalidate は実行。

        invalidate_pending_candidates が contact.duplicate_checked_at=None で save する
        ため updated_at は更新される（仕様）。self.save() は値差分がないので呼ばれない。
        """
        # 同じ値を渡す（company は DUPLICATE_CHECK_FIELDS 内）
        self.contact_a.update_field("company", "A-company", self.user)

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.company, "A-company")

        # CFC は confirmed 化される
        self.cfc_company.refresh_from_db()
        self.assertIsNotNone(self.cfc_company.confirmed_at)

        # DUPLICATE_CHECK_FIELDS なので invalidate が実行される
        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.review_status,
            DuplicateCandidate.ReviewStatus.INVALIDATED,
        )
        # invalidate により duplicate_checked_at が NULL 化される
        self.assertIsNone(self.contact_a.duplicate_checked_at)

    def test_high_field_no_cfc_record(self):
        """N4: low/mid CFC が無い疑似 high フィールド → CFC は新規作成されない。"""
        cfc_count_before = ContactFieldConfidence.objects.filter(
            contact=self.contact_a
        ).count()

        # full_name は CFC レコード未作成（疑似 high）
        self.contact_a.update_field("full_name", "A-name-new", self.user)

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.full_name, "A-name-new")

        # CFC レコードは新規作成されない
        cfc_count_after = ContactFieldConfidence.objects.filter(
            contact=self.contact_a
        ).count()
        self.assertEqual(cfc_count_after, cfc_count_before)

    # ------------------------------------------------------------------
    # 異常系
    # ------------------------------------------------------------------

    def test_invalid_field_name_raises_value_error(self):
        """E1: システム管理フィールド名 → ValueError。"""
        original_status = self.contact_a.status
        for forbidden in ("status", "previous_status", "created_by", "person"):
            with self.subTest(field=forbidden):
                with self.assertRaises(ValueError) as ctx:
                    self.contact_a.update_field(forbidden, "x", self.user)
                self.assertIn("is not an updatable field", str(ctx.exception))

        # DB 不変
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.status, original_status)

    def test_unsaved_contact_raises_value_error(self):
        """E2: 未保存の Contact インスタンス（_state.adding=True）→ ValueError。

        Contact.id は UUIDField(default=uuid.uuid4) のため、未保存でも pk は割当て
        済み。「保存済みかどうか」は Django 標準の `_state.adding` で判定する。
        """
        unsaved = Contact(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="unsaved",
        )
        self.assertTrue(unsaved._state.adding)
        with self.assertRaises(ValueError) as ctx:
            unsaved.update_field("company", "x", self.user)
        self.assertIn("saved Contact", str(ctx.exception))

    def test_transaction_rollback_on_exception(self):
        """E3: 内部処理で例外発生 → 全変更ロールバック。"""
        # mark_fields_as_confirmed を例外送出に差し替え
        with patch.object(
            ContactFieldConfidence,
            "mark_fields_as_confirmed",
            side_effect=RuntimeError("forced failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.contact_a.update_field(
                    "company", "A-company-new", self.user
                )

        # 値の更新がロールバックされている
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.company, "A-company")

    # ------------------------------------------------------------------
    # 境界
    # ------------------------------------------------------------------

    def test_updated_at_and_updated_by_set(self):
        """B1: updated_at と updated_by が user / 現在時刻で更新される。"""
        original_updated_at = self.contact_a.updated_at

        self.contact_a.update_field("company", "A-company-new", self.user)

        self.contact_a.refresh_from_db()
        self.assertGreater(self.contact_a.updated_at, original_updated_at)
        self.assertEqual(self.contact_a.updated_by_id, self.user.id)

    def test_other_fields_cfc_unchanged(self):
        """B2: 操作対象以外のフィールドの CFC は触られない。"""
        # company を編集 → notes の CFC は不変
        original_notes_confirmed_at = self.cfc_notes.confirmed_at
        original_notes_updated_at = self.cfc_notes.updated_at

        self.contact_a.update_field("company", "A-company-new", self.user)

        self.cfc_notes.refresh_from_db()
        self.assertEqual(self.cfc_notes.confirmed_at, original_notes_confirmed_at)
        self.assertEqual(self.cfc_notes.updated_at, original_notes_updated_at)

    # ------------------------------------------------------------------
    # シグネチャ
    # ------------------------------------------------------------------

    def test_signature(self):
        """update_field() のシグネチャは (self, field_name, new_value, user)。"""
        sig = inspect.signature(Contact.update_field)
        self.assertEqual(
            list(sig.parameters.keys()),
            ["self", "field_name", "new_value", "user"],
        )
