"""Contact モデル + 関連 View の単体テスト。"""

import inspect
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

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
