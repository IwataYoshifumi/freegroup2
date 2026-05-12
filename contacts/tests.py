"""Contact モデル + 関連 View の単体テスト。"""

import inspect
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contacts.forms import (
    ContactAddAdditionalRoleForm,
    ContactBaseForm,
    ContactUpdateActiveForm,
    ContactUpdateForm,
)
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


class _ContactAjaxTestBase(TestCase):
    """AJAX テスト共通の setUp（D-3c）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="d3c_test_user", password="dummy"
        )
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

        # CFC: company medium、notes low、phone low（unconfirmed_count 検証用）
        self.cfc_company = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        self.cfc_notes = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="notes",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        self.cfc_phone = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )

        # invalidate 検証用：(A,B) pending DC
        self.person_b = Person.objects.create()
        self.contact_b = Contact.objects.create(
            person=self.person_b,
            status=Contact.Status.PRIMARY,
            full_name="B-name",
        )
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

        self.client = Client()
        self.client.force_login(self.user)

    def _post_json(self, url, payload, client=None):
        """JSON ボディで POST するヘルパー。"""
        c = client or self.client
        return c.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )


class ContactAjaxUpdateFieldViewTests(_ContactAjaxTestBase):
    """ContactAjaxUpdateFieldView の単体テスト（D-3c §5.1）。"""

    def _url(self, contact=None):
        return reverse(
            "contacts:ajax_update_field",
            kwargs={"pk": (contact or self.contact_a).pk},
        )

    # ---- 正常系 ----

    def test_n1_updates_duplicate_check_field(self):
        """N1: DUPLICATE_CHECK_FIELDS（company）修正 → 200 / 値更新 / CFC confirmed / DC invalidated。"""
        resp = self._post_json(
            self._url(),
            {"field_name": "company", "new_value": "A-company-new"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["field_name"], "company")
        self.assertEqual(body["updated_value"], "A-company-new")
        self.assertEqual(body["confidence_state"], "confirmed")

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.company, "A-company-new")

        self.cfc_company.refresh_from_db()
        self.assertIsNotNone(self.cfc_company.confirmed_at)

        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.review_status,
            DuplicateCandidate.ReviewStatus.INVALIDATED,
        )

    def test_n2_updates_non_duplicate_check_field(self):
        """N2: DUPLICATE_CHECK_FIELDS 外（notes）修正 → 200 / 値更新 / DC 不変。"""
        resp = self._post_json(
            self._url(),
            {"field_name": "notes", "new_value": "A-notes-new"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["updated_value"], "A-notes-new")

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.notes, "A-notes-new")

        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.review_status,
            DuplicateCandidate.ReviewStatus.PENDING,
        )

    def test_n3_no_value_diff(self):
        """N3: 差分なし → 200 / save 不発生。"""
        original_updated_at = self.contact_a.updated_at
        resp = self._post_json(
            self._url(),
            {"field_name": "notes", "new_value": "A-notes"},
        )
        self.assertEqual(resp.status_code, 200)

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.updated_at, original_updated_at)
        self.cfc_notes.refresh_from_db()
        self.assertIsNotNone(self.cfc_notes.confirmed_at)

    # ---- 異常系 ----

    def test_e1_contact_not_found(self):
        """E1: 存在しない Contact → 404。"""
        import uuid as _uuid

        url = reverse(
            "contacts:ajax_update_field", kwargs={"pk": _uuid.uuid4()}
        )
        resp = self._post_json(
            url, {"field_name": "company", "new_value": "x"}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["success"])

    def test_e2_inactive_contact_forbidden(self):
        """E2: inactive Contact → 403。"""
        self.contact_a.status = Contact.Status.INACTIVE
        self.contact_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(),
            {"field_name": "company", "new_value": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_e3_archived_person_forbidden(self):
        """E3: archived Person 配下 → 403。"""
        self.person_a.status = Person.Status.ARCHIVED
        self.person_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(),
            {"field_name": "company", "new_value": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_e3b_merged_person_forbidden(self):
        """E3': merged Person 配下 → 403（防御的、論点 4）。"""
        # mark_as_merged は Person 単独で動かないので status を直接書き換え
        self.person_a.status = Person.Status.MERGED
        self.person_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(),
            {"field_name": "company", "new_value": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_e4_unauthenticated_forbidden(self):
        """E4: 未ログイン → 403（論点 1、案 A）。"""
        c = Client()  # 未ログインクライアント
        resp = self._post_json(
            self._url(),
            {"field_name": "company", "new_value": "x"},
            client=c,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Authentication required", resp.json()["error"])

    def test_e5_invalid_field_name(self):
        """E5: UPDATABLE_FIELDS 外のフィールド → 400。"""
        resp = self._post_json(
            self._url(),
            {"field_name": "status", "new_value": "x"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid field name", resp.json()["error"])

    def test_e6_invalid_json(self):
        """E6: JSON パースエラー → 400。"""
        resp = self.client.post(
            self._url(),
            data="not-a-json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid JSON", resp.json()["error"])

    def test_e7_missing_csrf_token(self):
        """E7: CSRF トークンなし → 403（@csrf_exempt 未使用）。"""
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        resp = c.post(
            self._url(),
            data=json.dumps({"field_name": "company", "new_value": "x"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    # ---- レスポンス検証 ----

    def test_r1_updated_value_in_response(self):
        """R1: レスポンスの updated_value が DB の保存後の値と一致する。"""
        resp = self._post_json(
            self._url(),
            {"field_name": "full_name", "new_value": "A-name-renamed"},
        )
        self.contact_a.refresh_from_db()
        self.assertEqual(
            resp.json()["updated_value"], self.contact_a.full_name
        )

    def test_r2_unconfirmed_count_in_response(self):
        """R2: unconfirmed_count が正しく計算される。"""
        # 初期：company / notes / phone の 3 件 unconfirmed
        resp = self._post_json(
            self._url(),
            {"field_name": "company", "new_value": "A-company-new"},
        )
        # company が confirmed 化されたので残り 2 件
        self.assertEqual(resp.json()["unconfirmed_count"], 2)


class ContactAjaxConfirmFieldsViewTests(_ContactAjaxTestBase):
    """ContactAjaxConfirmFieldsView の単体テスト（D-3c §5.2）。"""

    def _url(self, contact=None):
        return reverse(
            "contacts:ajax_confirm_fields",
            kwargs={"pk": (contact or self.contact_a).pk},
        )

    # ---- 正常系 ----

    def test_n1_single_field(self):
        """N1: 単数フィールドの確認 → 200 / CFC confirmed。"""
        resp = self._post_json(
            self._url(), {"field_names": ["company"]}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["confirmed_field_names"], ["company"])
        self.assertEqual(body["unconfirmed_count"], 2)  # notes / phone 残り

        self.cfc_company.refresh_from_db()
        self.assertIsNotNone(self.cfc_company.confirmed_at)

    def test_n2_multiple_fields_bulk(self):
        """N2: 複数フィールドの確認（一括確定）→ 200 / 全 CFC confirmed。"""
        resp = self._post_json(
            self._url(),
            {"field_names": ["company", "notes", "phone"]},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            sorted(body["confirmed_field_names"]),
            ["company", "notes", "phone"],
        )
        self.assertEqual(body["unconfirmed_count"], 0)

        for cfc in (self.cfc_company, self.cfc_notes, self.cfc_phone):
            cfc.refresh_from_db()
            self.assertIsNotNone(cfc.confirmed_at)

    def test_n3_already_confirmed_idempotent(self):
        """N3: 既に confirmed 済みのフィールド再指定 → 冪等動作。"""
        # 1 回目
        self._post_json(self._url(), {"field_names": ["company"]})
        self.cfc_company.refresh_from_db()
        first_confirmed_at = self.cfc_company.confirmed_at
        self.assertIsNotNone(first_confirmed_at)

        # 2 回目（同じフィールド）
        resp = self._post_json(self._url(), {"field_names": ["company"]})
        self.assertEqual(resp.status_code, 200)
        self.cfc_company.refresh_from_db()
        # confirmed_at は更新される（mark_fields_as_confirmed の挙動）
        self.assertIsNotNone(self.cfc_company.confirmed_at)

    def test_n4_high_field_no_cfc_record(self):
        """N4: 疑似 high フィールド（CFC レコードなし）→ 200 / no-op、エラーなし。"""
        # full_name は CFC レコード未作成（疑似 high）
        cfc_count_before = ContactFieldConfidence.objects.filter(
            contact=self.contact_a
        ).count()
        resp = self._post_json(
            self._url(), {"field_names": ["full_name"]}
        )
        self.assertEqual(resp.status_code, 200)
        cfc_count_after = ContactFieldConfidence.objects.filter(
            contact=self.contact_a
        ).count()
        self.assertEqual(cfc_count_after, cfc_count_before)

    # ---- 異常系 ----

    def test_e1_contact_not_found(self):
        """E1: 存在しない Contact → 404。"""
        import uuid as _uuid

        url = reverse(
            "contacts:ajax_confirm_fields", kwargs={"pk": _uuid.uuid4()}
        )
        resp = self._post_json(url, {"field_names": ["company"]})
        self.assertEqual(resp.status_code, 404)

    def test_e2_inactive_contact_forbidden(self):
        """E2: inactive Contact → 403。"""
        self.contact_a.status = Contact.Status.INACTIVE
        self.contact_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(), {"field_names": ["company"]}
        )
        self.assertEqual(resp.status_code, 403)

    def test_e3_archived_person_forbidden(self):
        """E3: archived Person 配下 → 403。"""
        self.person_a.status = Person.Status.ARCHIVED
        self.person_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(), {"field_names": ["company"]}
        )
        self.assertEqual(resp.status_code, 403)

    def test_e4_invalid_field_name(self):
        """E4: UPDATABLE_FIELDS 外のフィールド名 → 400。"""
        resp = self._post_json(
            self._url(), {"field_names": ["status"]}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid field name", resp.json()["error"])

    def test_e5_empty_field_names_no_op(self):
        """E5: field_names が空配列 → 200 で no-op（論点 5）。"""
        resp = self._post_json(self._url(), {"field_names": []})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["confirmed_field_names"], [])
        # 初期 unconfirmed_count 3 件のまま
        self.assertEqual(body["unconfirmed_count"], 3)

        # CFC は触られない
        for cfc in (self.cfc_company, self.cfc_notes, self.cfc_phone):
            cfc.refresh_from_db()
            self.assertIsNone(cfc.confirmed_at)

    def test_e6_invalid_json(self):
        """E6: JSON パースエラー → 400。"""
        resp = self.client.post(
            self._url(),
            data="not-a-json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_e7_missing_csrf_token(self):
        """E7: CSRF トークンなし → 403。"""
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        resp = c.post(
            self._url(),
            data=json.dumps({"field_names": ["company"]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_e_unauthenticated_forbidden(self):
        """E: 未ログイン → 403（論点 1）。"""
        c = Client()
        resp = self._post_json(
            self._url(), {"field_names": ["company"]}, client=c
        )
        self.assertEqual(resp.status_code, 403)

    # ---- レスポンス検証 ----

    def test_r1_confirmed_field_names_in_response(self):
        """R1: confirmed_field_names がリクエストの field_names を反映。"""
        resp = self._post_json(
            self._url(), {"field_names": ["company", "phone"]}
        )
        self.assertEqual(
            sorted(resp.json()["confirmed_field_names"]),
            ["company", "phone"],
        )

    def test_r2_unconfirmed_count_in_response(self):
        """R2: unconfirmed_count が処理後の値で正しく返る。"""
        # company / notes / phone の 3 件 unconfirmed が初期状態
        # company を確認 → 残り 2 件
        resp = self._post_json(
            self._url(), {"field_names": ["company"]}
        )
        self.assertEqual(resp.json()["unconfirmed_count"], 2)


class ContactDetailViewTests(TestCase):
    """ContactDetailView の単体テスト（D-3b §8.1）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="d3b_test_user", password="dummy"
        )
        # primary Contact のセットアップ
        self.person_a = Person.objects.create()
        self.contact_a = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.PRIMARY,
            full_name="A-name",
            company="A-company",
        )
        self.person_a.primary_contact = self.contact_a
        self.person_a.save(update_fields=["primary_contact", "updated_at"])

        # CFC（mid/low）
        self.cfc_company = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )

        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, contact=None):
        return reverse(
            "contacts:contact_detail",
            kwargs={"pk": (contact or self.contact_a).pk},
        )

    # ---- 正常系 ----

    def test_n1_primary_contact(self):
        """N1: primary Contact → 200、編集可能モード。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertTrue(ctx["is_editable"])
        self.assertTrue(ctx["is_primary"])
        self.assertFalse(ctx["is_active"])
        self.assertFalse(ctx["is_inactive"])
        self.assertEqual(ctx["contact"].pk, self.contact_a.pk)

    def test_n2_active_contact(self):
        """N2: active Contact → 200、編集可能モード、別肩書追加ボタン非表示。"""
        active = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
        )
        resp = self.client.get(self._url(active))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertTrue(ctx["is_editable"])
        self.assertFalse(ctx["is_primary"])
        self.assertTrue(ctx["is_active"])
        # 別肩書追加ボタンは primary のみ。テンプレート側で is_primary=False なので
        # 別肩書ボタン HTML は描画されない
        self.assertNotIn("別肩書追加", resp.content.decode())

    def test_n3_inactive_contact(self):
        """N3: inactive Contact → 200、表示のみモード。"""
        # primary を別 Contact にして inactive を作る（partial unique constraint 回避）
        inactive_person = Person.objects.create()
        inactive_primary = Contact.objects.create(
            person=inactive_person,
            status=Contact.Status.PRIMARY,
            full_name="dummy-primary",
        )
        inactive_person.primary_contact = inactive_primary
        inactive_person.save(update_fields=["primary_contact", "updated_at"])

        inactive = Contact.objects.create(
            person=inactive_person,
            status=Contact.Status.INACTIVE,
            full_name="A-inactive",
        )

        resp = self.client.get(self._url(inactive))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertFalse(ctx["is_editable"])
        self.assertTrue(ctx["is_inactive"])
        self.assertIn("表示のみモード", resp.content.decode())

    def test_n4_archived_person_contact(self):
        """N4: archived Person 配下の Contact → 200、表示のみモード。"""
        self.person_a.status = Person.Status.ARCHIVED
        self.person_a.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_editable"])

    def test_n5_merged_person_contact(self):
        """N5: merged Person 配下の Contact → 200、表示のみモード。"""
        self.person_a.status = Person.Status.MERGED
        self.person_a.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_editable"])

    def test_n6_no_business_card(self):
        """N6: business_card なし → 名刺画像セクション非表示。"""
        resp = self.client.get(self._url())
        self.assertIsNone(resp.context["business_card"])
        # モーダル要素も描画されない
        self.assertNotIn("contactCardImageModal", resp.content.decode())

    def test_n7_other_active_contacts(self):
        """N7: 他の active Contact がある → other_active_contacts に含まれる。"""
        other_active = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="A-other-active",
        )
        resp = self.client.get(self._url())
        ctx_others = list(resp.context["other_active_contacts"])
        self.assertIn(other_active, ctx_others)

    def test_n7b_active_view_includes_primary(self):
        """N7': 自分が active なら他のアクティブコンタクトに primary が含まれる。"""
        active = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
        )
        resp = self.client.get(self._url(active))
        ctx_others = list(resp.context["other_active_contacts"])
        self.assertIn(self.contact_a, ctx_others)  # primary が含まれる
        self.assertNotIn(active, ctx_others)  # 自分は含まれない

    def test_n8_pending_duplicates(self):
        """N8: 重複候補がある → pending_duplicates に含まれる。"""
        person_b = Person.objects.create()
        Contact.objects.create(
            person=person_b,
            status=Contact.Status.PRIMARY,
            full_name="B-name",
        )
        if self.person_a.id < person_b.id:
            pa, pb = self.person_a, person_b
        else:
            pa, pb = person_b, self.person_a
        DuplicateCandidate.objects.create(
            person_a=pa,
            person_b=pb,
            score=120,
            rank=DuplicateCandidate.Rank.POSSIBLE_MID,
            review_status=DuplicateCandidate.ReviewStatus.PENDING,
            review_result=[],
        )
        resp = self.client.get(self._url())
        self.assertEqual(len(resp.context["pending_duplicates"]), 1)
        # マージ画面ボタンプレースホルダが描画される
        self.assertIn("マージ画面へ", resp.content.decode())

    def test_n9_previous_person(self):
        """N9: previous_person がある → context に含まれる。"""
        prev_person = Person.objects.create()
        self.contact_a.previous_person = prev_person
        self.contact_a.save(
            update_fields=["previous_person", "updated_at"]
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.context["previous_person"], prev_person)
        self.assertIn("マージ前の人物", resp.content.decode())

    def test_n10_no_cfc_records(self):
        """N10: CFC レコードなし（全 high）→ contact_confidence が「確認すべきフィールドなし」表示。"""
        # CFC を全削除
        ContactFieldConfidence.objects.filter(contact=self.contact_a).delete()
        resp = self.client.get(self._url())
        self.assertIn("確認すべきフィールドはありません", resp.content.decode())

    # ---- 異常系 ----

    def test_e1_contact_not_found(self):
        """E1: 存在しない Contact → 404。"""
        import uuid as _uuid

        url = reverse(
            "contacts:contact_detail", kwargs={"pk": _uuid.uuid4()}
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_e2_unauthenticated_returns_200(self):
        """E2: 未ログイン → 200（仮認証スタイル、論点 1）。"""
        c = Client()  # 未ログイン
        # スーパーユーザーが存在しないと get_current_user が None を返すが
        # このテストは仮認証「スタイル」（LoginRequiredMixin 未使用）の確認
        User.objects.create_superuser(username="su", password="dummy")
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 200)

    # ---- レスポンス検証 ----

    def test_r1_context_keys(self):
        """R1: context に必要なキーがすべて含まれる。"""
        resp = self.client.get(self._url())
        for key in (
            "contact",
            "field_confidences",
            "is_editable",
            "is_primary",
            "is_active",
            "is_inactive",
            "business_card",
            "other_active_contacts",
            "pending_duplicates",
            "merge_logs",
            "previous_person",
            "back",
        ):
            self.assertIn(key, resp.context, f"missing context key: {key}")

    def test_r2_template_rendered(self):
        """R2: テンプレートが正しくレンダリング、Contact 名が含まれる。"""
        resp = self.client.get(self._url())
        body = resp.content.decode()
        self.assertIn("A-name", body)
        self.assertIn("コンタクト詳細", body)

    def test_r3_edit_ui_in_editable_mode(self):
        """R3: 編集可能モードで修正 UI（ラジオ / 確定 / 修正フォーム）と data-confidence-state が出力。"""
        resp = self.client.get(self._url())
        body = resp.content.decode()
        # 修正 UI のフック
        self.assertIn("js-contact-field-action", body)
        self.assertIn("js-contact-field-confirm-btn", body)
        self.assertIn("js-contact-field-edit-form", body)
        self.assertIn("js-contact-field-edit-input", body)
        self.assertIn("js-contact-field-update-btn", body)
        self.assertIn("js-contact-field-cancel-btn", body)
        # confidence バッジ slot（D-3d-1 で追加、JS が innerHTML を差し替えるフック）
        self.assertIn("js-contact-field-badge-slot", body)
        # data-confidence-state（company は medium CFC → "mid"）
        self.assertIn('data-confidence-state="mid"', body)
        # 共通 toast 要素（base.html）
        self.assertIn('class="app-toast"', body)

    def test_r4_no_edit_ui_in_view_only_mode(self):
        """R4: 表示のみモード（archived Person 配下）では修正 UI が出力されない。"""
        self.person_a.status = Person.Status.ARCHIVED
        self.person_a.save(update_fields=["status", "updated_at"])
        resp = self.client.get(self._url())
        body = resp.content.decode()
        self.assertNotIn("js-contact-field-action", body)
        self.assertNotIn("js-contact-field-edit-form", body)
        # data-confidence-state も付かない
        self.assertNotIn("data-confidence-state", body)

    def _render_field(self, field_name, value, label="ラベル"):
        """_contact_field.html を直接レンダリングするヘルパー。"""
        tpl = Template('{% include "contacts/_contact_field.html" %}')
        ctx = Context(
            {
                "is_editable": True,
                "field_name": field_name,
                "label": label,
                "value": value,
                "contact": self.contact_a,
                "field_confidences": self.contact_a.get_field_confidences(),
            }
        )
        return tpl.render(ctx)

    def test_r5_high_field_no_edit_ui(self):
        """R5: 編集可能モードでも high フィールドには修正 UI が出力されない。

        ただし js-contact-field-row / data-confidence-state="high" /
        js-contact-field-badge-slot は引き続き出力される（JS フック維持）。
        """
        # full_name は CFC 未作成 → 疑似 high
        rendered = self._render_field("full_name", "Tester")
        # 行レベルのフックは維持
        self.assertIn("js-contact-field-row", rendered)
        self.assertIn('data-confidence-state="high"', rendered)
        self.assertIn("js-contact-field-badge-slot", rendered)
        # 修正 UI フックは出力されない
        self.assertNotIn("js-contact-field-action", rendered)
        self.assertNotIn("js-contact-field-confirm-btn", rendered)
        self.assertNotIn("js-contact-field-edit-form", rendered)
        self.assertNotIn("app-contact-field-actions", rendered)

    def test_r6_confirmed_field_no_edit_ui(self):
        """R6: 編集可能モードでも confirmed フィールドには修正 UI が出力されない。

        バッジは「確認済み」が描画され、行フックは維持される。
        """
        # email に confirmed_at セット済みの CFC を作成
        ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        rendered = self._render_field("email", "a@example.com")
        # 行レベルのフック + 確認済みバッジ
        self.assertIn("js-contact-field-row", rendered)
        self.assertIn('data-confidence-state="confirmed"', rendered)
        self.assertIn("app-status-badge--success", rendered)
        self.assertIn("確認済み", rendered)
        # 修正 UI フックは出力されない
        self.assertNotIn("js-contact-field-action", rendered)
        self.assertNotIn("js-contact-field-confirm-btn", rendered)
        self.assertNotIn("js-contact-field-edit-form", rendered)

    def test_r7_mid_low_field_has_edit_ui(self):
        """R7: mid / low フィールドには修正 UI が出力される（既存 R3 の補強）。"""
        # company は medium CFC（setUp で作成済み）
        rendered_mid = self._render_field("company", "A-company")
        self.assertIn('data-confidence-state="mid"', rendered_mid)
        self.assertIn("js-contact-field-action", rendered_mid)
        self.assertIn("js-contact-field-confirm-btn", rendered_mid)
        self.assertIn("js-contact-field-edit-form", rendered_mid)

        # phone に low CFC を作成
        ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        # field_confidences は再取得
        self.contact_a.refresh_from_db()
        rendered_low = self._render_field("phone", "03-1234")
        self.assertIn('data-confidence-state="low"', rendered_low)
        self.assertIn("js-contact-field-action", rendered_low)

    # ---- inactive Contact 履歴セクション（引継ぎ資料 v5 §4.2）----

    def test_inactive_contacts_displayed(self):
        """同一 Person 配下の inactive Contact が context に含まれ、画面に表示される。"""
        inactive = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="A-inactive-old",
        )
        resp = self.client.get(self._url())
        ids = [c.id for c in resp.context["inactive_contacts"]]
        self.assertIn(inactive.id, ids)
        body = resp.content.decode()
        self.assertIn("過去のコンタクト（inactive 履歴）", body)
        self.assertIn("A-inactive-old", body)

    def test_inactive_contacts_excludes_self(self):
        """自分自身が inactive Contact の時、自身は inactive_contacts から除外される。"""
        # primary は contact_a。別途同 Person 配下に inactive を 2 件作り、
        # その 1 件を ContactDetailView で開いて自分自身が除外されることを確認。
        inactive_self = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="self-inactive",
        )
        inactive_other = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="other-inactive",
        )
        resp = self.client.get(self._url(inactive_self))
        ids = [c.id for c in resp.context["inactive_contacts"]]
        self.assertNotIn(inactive_self.id, ids)
        self.assertIn(inactive_other.id, ids)

    def test_inactive_contacts_empty(self):
        """同一 Person 配下に inactive がない → 空 QuerySet、画面に「なし」表示。"""
        resp = self.client.get(self._url())
        self.assertEqual(list(resp.context["inactive_contacts"]), [])
        body = resp.content.decode()
        self.assertIn("過去のコンタクト（inactive 履歴）", body)
        self.assertIn("inactive Contact なし", body)

    def test_inactive_contacts_shown_in_archived_person(self):
        """archived Person 配下の Contact からも inactive 履歴が見える（表示のみモード）。"""
        inactive = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="archived-pp-inactive",
        )
        self.person_a.status = Person.Status.ARCHIVED
        self.person_a.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self._url())
        self.assertFalse(resp.context["is_editable"])
        ids = [c.id for c in resp.context["inactive_contacts"]]
        self.assertIn(inactive.id, ids)
        self.assertIn("archived-pp-inactive", resp.content.decode())

    def test_inactive_contacts_shown_in_merged_person(self):
        """merged Person 配下の Contact からも inactive 履歴が見える（表示のみモード）。"""
        inactive = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="merged-pp-inactive",
        )
        self.person_a.status = Person.Status.MERGED
        self.person_a.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self._url())
        self.assertFalse(resp.context["is_editable"])
        ids = [c.id for c in resp.context["inactive_contacts"]]
        self.assertIn(inactive.id, ids)
        self.assertIn("merged-pp-inactive", resp.content.decode())


class ConfidenceTagTests(TestCase):
    """{% confidence %} カスタムタグの単体テスト（D-3b §8.2 C1〜C4）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tag_test_user", password="dummy"
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="T",
        )

    def _render(self, contact, field_name, fmt="badge"):
        tpl = Template(
            "{% load ui_tags %}"
            "{% confidence confidences field_name fmt %}"
        )
        confidences = contact.get_field_confidences()
        return tpl.render(
            Context(
                {
                    "confidences": confidences,
                    "field_name": field_name,
                    "fmt": fmt,
                }
            )
        )

    def test_c1_high_field_shows_nothing(self):
        """C1: 疑似 high（CFC レコードなし）→ 何も表示されない。"""
        rendered = self._render(self.contact, "full_name")
        self.assertEqual(rendered.strip(), "")

    def test_c2_medium_unconfirmed_shows_badge(self):
        """C2: medium AND confirmed_at IS NULL → 中バッジ表示。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        rendered = self._render(self.contact, "company")
        self.assertIn("app-status-badge--warning", rendered)
        self.assertIn("中", rendered)

    def test_c3_low_unconfirmed_shows_badge(self):
        """C3: low AND confirmed_at IS NULL → 低バッジ表示。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        rendered = self._render(self.contact, "phone")
        self.assertIn("app-status-badge--error", rendered)
        self.assertIn("低", rendered)

    def test_c4_confirmed_shows_confirmed_badge(self):
        """C4: confirmed_at IS NOT NULL → 確認済みバッジ表示。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        rendered = self._render(self.contact, "email")
        self.assertIn("app-status-badge--success", rendered)
        self.assertIn("確認済み", rendered)


class ContactConfidenceTagTests(TestCase):
    """{% contact_confidence %} カスタムタグの単体テスト（D-3b §8.2 CC1〜CC2）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="cctag_test_user", password="dummy"
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="T",
        )

    def _render(self, contact, fmt="summary"):
        tpl = Template(
            "{% load ui_tags %}"
            "{% contact_confidence contact fmt %}"
        )
        return tpl.render(Context({"contact": contact, "fmt": fmt}))

    def test_cc1_summary_with_unconfirmed(self):
        """CC1: 未確認あり → 「未確認 N 件 / 全 M 件中」表示。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        rendered = self._render(self.contact)
        self.assertIn("未確認", rendered)
        self.assertIn("1", rendered)  # 未確認 1 件
        self.assertIn("2", rendered)  # 全 2 件中

    def test_cc2_summary_all_confirmed(self):
        """CC2: 全確認済み → 「全 M 件確認済み」表示。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        rendered = self._render(self.contact)
        self.assertIn("全 1 件確認済み", rendered)

    def test_cc3_summary_no_cfc_records(self):
        """CC3: CFC レコードなし → 「確認すべきフィールドはありません」。"""
        rendered = self._render(self.contact)
        self.assertIn("確認すべきフィールドはありません", rendered)


class ContactListViewTests(TestCase):
    """ContactListView の単体テスト（v1.4.2 仕様変更追加）。

    7 フィールド検索（tel は phone/mobile/fax の OR）、include_inactive、
    person.status="active" 絞り込み、updated_at 降順、ページネーション、
    未認証 200 を検証する。
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="contact_list_test_user", password="dummy"
        )
        self.person_a = Person.objects.create()
        self.contact_a = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.PRIMARY,
            full_name="Alice Smith",
            company="Acme Corp",
            department="Sales",
            title="Manager",
            email="alice@acme.example",
            phone="03-1234-5678",
            address="Tokyo",
        )
        self.person_a.primary_contact = self.contact_a
        self.person_a.save(update_fields=["primary_contact", "updated_at"])

        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("contacts:contact_list")

    def _make_primary(self, **kwargs):
        """別 Person 配下に primary Contact を作るヘルパー。"""
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, **kwargs
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return contact

    def test_default_shows_primary_only(self):
        """初回アクセス（searched なし）→ primary のみ、active / inactive は非表示。"""
        active = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
        )
        inactive = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="A-inactive",
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(self.contact_a.id, ids)
        self.assertNotIn(active.id, ids)
        self.assertNotIn(inactive.id, ids)
        self.assertEqual(resp.context["selected_statuses"], ["primary"])
        self.assertFalse(resp.context["searched"])

    def test_status_filter_primary_active(self):
        """searched=1 + status=[primary, active] → primary + active 表示、inactive 非表示。"""
        active = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
        )
        inactive = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="A-inactive",
        )
        resp = self.client.get(
            self.url, {"searched": "1", "status": ["primary", "active"]}
        )
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(self.contact_a.id, ids)
        self.assertIn(active.id, ids)
        self.assertNotIn(inactive.id, ids)
        self.assertTrue(resp.context["searched"])
        self.assertEqual(
            sorted(resp.context["selected_statuses"]), ["active", "primary"]
        )

    def test_status_filter_inactive_only(self):
        """searched=1 + status=inactive → inactive のみ表示、primary/active 非表示。"""
        active = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
        )
        inactive = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="A-inactive",
        )
        resp = self.client.get(
            self.url, {"searched": "1", "status": "inactive"}
        )
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(inactive.id, ids)
        self.assertNotIn(self.contact_a.id, ids)
        self.assertNotIn(active.id, ids)

    def test_status_filter_all_unchecked(self):
        """searched=1 + status なし → 0 件（全チェック外しの自然な結果）。"""
        Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
        )
        Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.INACTIVE,
            full_name="A-inactive",
        )
        resp = self.client.get(self.url, {"searched": "1"})
        self.assertEqual(list(resp.context["contacts"]), [])
        self.assertEqual(resp.context["selected_statuses"], [])

    def test_merged_person_excluded_regardless_of_status(self):
        """merged Person 配下の Contact は status を何選んでも非表示（regression）。"""
        merged_person = Person.objects.create(status=Person.Status.MERGED)
        merged_primary = Contact.objects.create(
            person=merged_person,
            status=Contact.Status.PRIMARY,
            full_name="merged-primary",
        )
        merged_inactive = Contact.objects.create(
            person=merged_person,
            status=Contact.Status.INACTIVE,
            full_name="merged-inactive",
        )

        # 初回（primary フィルタ）→ merged primary も非表示
        resp = self.client.get(self.url)
        self.assertNotIn(
            merged_primary.id, [c.id for c in resp.context["contacts"]]
        )

        # 3 status 全選択 → merged 配下はいずれも非表示
        resp = self.client.get(
            self.url,
            {
                "searched": "1",
                "status": ["primary", "active", "inactive"],
            },
        )
        ids = [c.id for c in resp.context["contacts"]]
        self.assertNotIn(merged_primary.id, ids)
        self.assertNotIn(merged_inactive.id, ids)

    def test_search_and_name_company(self):
        """name と company を同時指定で AND 検索（初回 primary フィルタ下）。"""
        c1 = self._make_primary(
            full_name="Alice Tanaka", company="Wonder Corp"
        )
        c2 = self._make_primary(
            full_name="Bob Smith", company="Acme Industries"
        )
        c3 = self._make_primary(
            full_name="Alice Brown", company="Acme Group"
        )

        resp = self.client.get(self.url, {"name": "Alice", "company": "Acme"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(self.contact_a.id, ids)
        self.assertIn(c3.id, ids)
        self.assertNotIn(c1.id, ids)
        self.assertNotIn(c2.id, ids)

    def test_search_tel_or_phone_mobile_fax(self):
        """tel は phone / mobile / fax の OR 一致。"""
        c_mobile = self._make_primary(
            full_name="MobOnly", mobile="090-1111-2222"
        )
        c_fax = self._make_primary(full_name="FaxOnly", fax="06-9999-8888")
        # setUp の contact_a は phone="03-1234-5678"

        resp = self.client.get(self.url, {"tel": "1234"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(self.contact_a.id, ids)
        self.assertNotIn(c_mobile.id, ids)
        self.assertNotIn(c_fax.id, ids)

        resp = self.client.get(self.url, {"tel": "090-1111"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(c_mobile.id, ids)
        self.assertNotIn(self.contact_a.id, ids)
        self.assertNotIn(c_fax.id, ids)

        resp = self.client.get(self.url, {"tel": "06-9999"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(c_fax.id, ids)
        self.assertNotIn(self.contact_a.id, ids)
        self.assertNotIn(c_mobile.id, ids)

    def test_order_by_updated_at_desc(self):
        """並び順は updated_at 降順。timing fragility を避けるため update() で明示設定。"""
        from datetime import timedelta

        c_old = self._make_primary(full_name="Old")
        c_new = self._make_primary(full_name="New")

        now = timezone.now()
        Contact.objects.filter(pk=self.contact_a.pk).update(
            updated_at=now - timedelta(hours=2)
        )
        Contact.objects.filter(pk=c_old.pk).update(
            updated_at=now - timedelta(hours=1)
        )
        Contact.objects.filter(pk=c_new.pk).update(updated_at=now)

        resp = self.client.get(self.url)
        ids = [c.id for c in resp.context["contacts"]]
        self.assertEqual(ids.index(c_new.id), 0)
        self.assertEqual(ids.index(c_old.id), 1)
        self.assertEqual(ids.index(self.contact_a.id), 2)

    def test_pagination_21_records_split_to_2_pages(self):
        """21 件以上で 2 ページ目に分かれる（paginate_by=20）。"""
        for i in range(20):
            self._make_primary(full_name=f"page-test-{i:02d}")

        resp = self.client.get(self.url)
        self.assertTrue(resp.context["is_paginated"])
        self.assertEqual(len(list(resp.context["contacts"])), 20)

        resp2 = self.client.get(self.url, {"page": "2"})
        self.assertEqual(len(list(resp2.context["contacts"])), 1)

    def test_unauthenticated_returns_200(self):
        """未ログイン → 200（仮認証スタイル、ContactDetailView と同じ）。"""
        # スーパーユーザーがいなくても ContactListView は user フィルタしないので 200
        c = Client()
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 200)


# ======================================================================
# D-Form ステップ1：ContactBaseForm / ContactUpdateForm /
# ContactUpdateActiveForm / UpdateActiveContactView のテスト
# ======================================================================


class ContactBaseFormTests(TestCase):
    """ContactBaseForm（仕様書 §11.6.2 / §11.6.4）の単体テスト。"""

    def test_meta_fields_match_updatable_fields(self):
        """Meta.fields が Contact.UPDATABLE_FIELDS と一致する。"""
        self.assertEqual(
            ContactBaseForm.Meta.fields, list(Contact.UPDATABLE_FIELDS)
        )


class _ContactUpdateFormTestBase(TestCase):
    """ContactUpdateForm / ContactUpdateActiveForm 共通の setUp。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="form_test_user", password="dummy"
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="A-name",
            company="A-company",
            email="a@example.com",
        )
        # company: medium / email: low / phone: low + confirmed 済み
        self.cfc_company = ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        self.cfc_email = ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )

    def _base_data(self, *, include_change_reason=True):
        """POST 用ベース data（UPDATABLE_FIELDS を現在値で埋める）。"""
        data = {f: getattr(self.contact, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        if include_change_reason:
            data["change_reason"] = "fix"
        return data


class ContactUpdateFormTests(_ContactUpdateFormTestBase):
    """ContactUpdateForm（仕様書 §11.6.2 / §11.7.1）の単体テスト。"""

    def test_requires_target_contact(self):
        """target_contact 未指定 → TypeError。"""
        with self.assertRaises(TypeError):
            ContactUpdateForm(data={})

    def test_adds_confirmed_checkbox_for_low_mid_unconfirmed(self):
        """low/mid かつ未確認のフィールドに対応する確認チェックボックスが追加される。"""
        form = ContactUpdateForm(target_contact=self.contact)
        self.assertIn("confirmed_company", form.fields)  # medium
        self.assertIn("confirmed_email", form.fields)    # low
        # phone は confirmed 済み → 追加されない
        self.assertNotIn("confirmed_phone", form.fields)
        # full_name は CFC なし（高信頼度）→ 追加されない
        self.assertNotIn("confirmed_full_name", form.fields)

    def test_no_checkboxes_when_all_high(self):
        """全 high（CFC レコードなし）の Contact では確認チェックボックス追加なし。"""
        ContactFieldConfidence.objects.filter(contact=self.contact).delete()
        form = ContactUpdateForm(target_contact=self.contact)
        for field_name in Contact.UPDATABLE_FIELDS:
            self.assertNotIn(f"confirmed_{field_name}", form.fields)

    def test_clean_passes_with_all_checkboxes_on(self):
        """確認チェックがすべて ON → is_valid() True。"""
        data = self._base_data()
        data["confirmed_company"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_clean_fails_when_checkbox_off(self):
        """確認チェックが 1 つでも OFF → is_valid() False、当該フィールドにエラー。"""
        data = self._base_data()
        # confirmed_company は OFF（キー自体を入れない）
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed_company", form.errors)
        self.assertNotIn("confirmed_email", form.errors)

    def test_change_reason_is_required(self):
        """change_reason 必須 → 未指定で is_valid() False。"""
        data = self._base_data(include_change_reason=False)
        data["confirmed_company"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertFalse(form.is_valid())
        self.assertIn("change_reason", form.errors)

    def test_get_update_contact_returns_pkless_new_contact(self):
        """get_update_contact() は未保存の新規 Contact を返す（status / person 未設定）。

        Contact.id は UUIDField(default=uuid.uuid4) のため、Contact() の時点で UUID は
        割り当てられる。「未保存」は _state.adding == True で判定する（仕様書 §11.6.5）。
        """
        data = self._base_data()
        data["full_name"] = "新しい名前"
        data["confirmed_company"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)
        new_contact = form.get_update_contact()
        # 未保存（DB に存在しない）
        self.assertTrue(new_contact._state.adding)
        # 値は反映されている
        self.assertEqual(new_contact.full_name, "新しい名前")
        # status / person は未設定（fix() の責務外）
        self.assertEqual(new_contact.status, "")
        self.assertIsNone(new_contact.person_id)

    def test_confirmed_field_names_with_checkboxes_on(self):
        """confirmed_field_names() に確認チェック ON のフィールドが含まれる。"""
        data = self._base_data()
        data["confirmed_company"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)
        names = form.confirmed_field_names()
        self.assertIn("company", names)
        self.assertIn("email", names)

    def test_confirmed_field_names_with_edited_field(self):
        """編集された high フィールドも confirmed_field_names() に含まれる。"""
        data = self._base_data()
        # full_name を編集（CFC なし＝high なので、編集だけで confirmed 扱い）
        data["full_name"] = "違う名前"
        data["confirmed_company"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)
        names = form.confirmed_field_names()
        self.assertIn("full_name", names)


class ContactUpdateActiveFormTests(_ContactUpdateFormTestBase):
    """ContactUpdateActiveForm（仕様書 §11.6.2）の単体テスト。"""

    def test_no_change_reason_field(self):
        """change_reason フィールドが存在しない。"""
        form = ContactUpdateActiveForm(target_contact=self.contact)
        self.assertNotIn("change_reason", form.fields)

    def test_confirmed_checkboxes_added_like_parent(self):
        """親と同様、low/mid 未確認フィールドにチェックボックスが追加される。"""
        form = ContactUpdateActiveForm(target_contact=self.contact)
        self.assertIn("confirmed_company", form.fields)
        self.assertIn("confirmed_email", form.fields)

    def test_clean_passes_without_change_reason(self):
        """change_reason 不要、確認チェック ON で is_valid() True。"""
        data = self._base_data(include_change_reason=False)
        data["confirmed_company"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateActiveForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_clean_fails_when_checkbox_off(self):
        """親同様、確認チェック OFF でエラー。"""
        data = self._base_data(include_change_reason=False)
        data["confirmed_email"] = "on"
        form = ContactUpdateActiveForm(data=data, target_contact=self.contact)
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed_company", form.errors)


class UpdateActiveContactViewTests(TestCase):
    """UpdateActiveContactView（13 番、仕様書 §11.6 / §11.7）の単体テスト。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="update_active_user", password="dummy"
        )
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="A-primary",
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.active = Contact.objects.create(
            person=self.person,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
            company="A-active-co",
            email="active@example.com",
        )
        self.cfc_company = ContactFieldConfidence.objects.create(
            contact=self.active,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )

        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, contact=None):
        return reverse(
            "contacts:contact_update_active",
            kwargs={"pk": (contact or self.active).pk},
        )

    def _base_post_data(self, contact):
        data = {f: getattr(contact, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        return data

    # ---- GET ----

    def test_get_active_returns_200(self):
        """active Contact → 200、change_reason フィールド非搭載・confirmed_company 搭載。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertNotIn("change_reason", form.fields)
        self.assertIn("confirmed_company", form.fields)

    def test_get_primary_returns_404(self):
        """primary Contact → 404（このViewはactive専用）。"""
        resp = self.client.get(self._url(self.primary))
        self.assertEqual(resp.status_code, 404)

    def test_get_inactive_returns_404(self):
        """inactive Contact → 404。"""
        inactive_person = Person.objects.create()
        inactive_primary = Contact.objects.create(
            person=inactive_person,
            status=Contact.Status.PRIMARY,
            full_name="dummy-primary",
        )
        inactive_person.primary_contact = inactive_primary
        inactive_person.save(
            update_fields=["primary_contact", "updated_at"]
        )
        inactive = Contact.objects.create(
            person=inactive_person,
            status=Contact.Status.INACTIVE,
            full_name="A-inactive",
        )
        resp = self.client.get(self._url(inactive))
        self.assertEqual(resp.status_code, 404)

    def test_get_nonexistent_returns_404(self):
        """存在しない Contact → 404。"""
        import uuid as _uuid

        url = reverse(
            "contacts:contact_update_active", kwargs={"pk": _uuid.uuid4()}
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_redirects(self):
        """未ログイン → 302（LoginRequiredMixin、login URL へリダイレクト）。"""
        c = Client()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)

    # ---- POST ----

    def test_post_valid_calls_fix_and_redirects(self):
        """有効な POST → Contact.fix() が呼ばれ、Contact 詳細画面へリダイレクト。"""
        data = self._base_post_data(self.active)
        data["company"] = "A-active-co-new"
        data["confirmed_company"] = "on"

        resp = self.client.post(self._url(), data=data)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse(
                "contacts:contact_detail", kwargs={"pk": self.active.pk}
            ),
        )

        self.active.refresh_from_db()
        self.assertEqual(self.active.company, "A-active-co-new")

        self.cfc_company.refresh_from_db()
        self.assertIsNotNone(self.cfc_company.confirmed_at)
        self.assertEqual(self.cfc_company.confirmed_by_id, self.user.id)

    def test_post_with_checkbox_off_shows_form_error(self):
        """確認チェック OFF → フォーム再表示、エラー含む、fix() は呼ばれない。"""
        data = self._base_post_data(self.active)
        data["company"] = "A-active-co-changed"
        # confirmed_company は OFF

        resp = self.client.post(self._url(), data=data)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("confirmed_company", resp.context["form"].errors)

        # fix() は呼ばれていない → 値もCFCも変更なし
        self.active.refresh_from_db()
        self.assertEqual(self.active.company, "A-active-co")
        self.cfc_company.refresh_from_db()
        self.assertIsNone(self.cfc_company.confirmed_at)


# ======================================================================
# D-Form ステップ2：ContactAddAdditionalRoleForm のテスト
# ======================================================================


class ContactAddAdditionalRoleFormTests(TestCase):
    """ContactAddAdditionalRoleForm（仕様書 §11.6.2、9 番）の単体テスト。"""

    def setUp(self):
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P-name",
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])

    def _base_data(self):
        """POST 用ベース data（UPDATABLE_FIELDS を空文字で埋める）。"""
        return {f: "" for f in Contact.UPDATABLE_FIELDS}

    def test_requires_person(self):
        """person 未指定 → TypeError。"""
        with self.assertRaises(TypeError):
            ContactAddAdditionalRoleForm(data={})

    def test_meta_fields_inherited_from_base(self):
        """Meta.fields は ContactBaseForm から継承される（UPDATABLE_FIELDS と一致）。"""
        self.assertEqual(
            ContactAddAdditionalRoleForm.Meta.fields,
            list(Contact.UPDATABLE_FIELDS),
        )

    def test_no_change_reason_or_note_fields(self):
        """change_reason / note / confirmed_<field> フィールドは存在しない。"""
        form = ContactAddAdditionalRoleForm(person=self.person)
        self.assertNotIn("change_reason", form.fields)
        self.assertNotIn("note", form.fields)
        for f in Contact.UPDATABLE_FIELDS:
            self.assertNotIn(f"confirmed_{f}", form.fields)

    def test_get_update_contact_returns_unsaved_new_contact(self):
        """get_update_contact() は status / person 未設定の未保存 Contact を返す。"""
        data = self._base_data()
        data["full_name"] = "別肩書 太郎"
        data["company"] = "別会社"
        form = ContactAddAdditionalRoleForm(data=data, person=self.person)
        self.assertTrue(form.is_valid(), msg=form.errors)
        new_contact = form.get_update_contact()
        # 未保存（DB に存在しない）
        self.assertTrue(new_contact._state.adding)
        # 値は反映
        self.assertEqual(new_contact.full_name, "別肩書 太郎")
        self.assertEqual(new_contact.company, "別会社")
        # status / person は未設定（View 側責務、§10.12）
        self.assertEqual(new_contact.status, "")
        self.assertIsNone(new_contact.person_id)
