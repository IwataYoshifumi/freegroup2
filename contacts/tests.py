"""Contact モデル + 関連 View の単体テスト。"""

import inspect
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contacts.forms import (
    AppErrorList,
    ContactAddAdditionalRoleForm,
    ContactBaseForm,
    ContactUpdateActiveForm,
    ContactUpdateForm,
    build_contact_sns_formset,
)
from contacts.models import Contact, ContactFieldConfidence, ContactSns
from duplicates.models import DuplicateCandidate
from persons.models import Person


User = get_user_model()


def _grant_contact_perms(user):
    """Phase 7 段3-2：Contact List/Detail/Create/Update/Preview に標準 CRUD 権限ガードが
    入った（rev20 No.10-14/23 ★2）。これらの View を叩く既存テストの正常系を保つため、
    view/add/change/delete_contact を一括付与する補正ヘルパー。

    宿題F（v1.7+）：Update 系フォーム経路にも owner ガード（can_edit_contact）が入った。
    既存テストの Contact は created_by/managed_by 未設定（所有者なし）のため、横断権限
    edit_all_contacts も併せて付与して正常系を保つ（owner ガード自体の検証は
    ContactFormOwnerGuardTests で別途行う）。"""
    from django.contrib.auth.models import Permission

    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="contacts",
            codename__in=[
                "view_contact",
                "add_contact",
                "change_contact",
                "delete_contact",
                "edit_all_contacts",
            ],
        )
    )


def _empty_sns_management_form(prefix="sns"):
    """ContactSns InlineFormSet の空 management_form（POST テスト用、Phase F1 §11.6.7）。"""
    return {
        f"{prefix}-TOTAL_FORMS": "0",
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def _sns_management_form(total, initial=0, prefix="sns"):
    """ContactSns InlineFormSet の management_form（行ありテスト用、Phase F1 §11.6.7）。"""
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


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
            organization="A-organization",
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
            organization="B-organization",
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

        # contact_a の organization に mid CFC を作成（after-confirmed 検証用）
        self.cfc_organization = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
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
        """N1: DUPLICATE_CHECK_FIELDS のフィールド（organization）を修正。"""
        self.contact_a.update_field("organization", "A-organization-new", self.user)

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.organization, "A-organization-new")

        # CFC が confirmed 化されている
        self.cfc_organization.refresh_from_db()
        self.assertIsNotNone(self.cfc_organization.confirmed_at)
        self.assertEqual(self.cfc_organization.confirmed_by_id, self.user.id)

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
        # 同じ値を渡す（organization は DUPLICATE_CHECK_FIELDS 内）
        self.contact_a.update_field("organization", "A-organization", self.user)

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.organization, "A-organization")

        # CFC は confirmed 化される
        self.cfc_organization.refresh_from_db()
        self.assertIsNotNone(self.cfc_organization.confirmed_at)

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
            unsaved.update_field("organization", "x", self.user)
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
                    "organization", "A-organization-new", self.user
                )

        # 値の更新がロールバックされている
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.organization, "A-organization")

    # ------------------------------------------------------------------
    # 境界
    # ------------------------------------------------------------------

    def test_updated_at_and_updated_by_set(self):
        """B1: updated_at と updated_by が user / 現在時刻で更新される。"""
        original_updated_at = self.contact_a.updated_at

        self.contact_a.update_field("organization", "A-organization-new", self.user)

        self.contact_a.refresh_from_db()
        self.assertGreater(self.contact_a.updated_at, original_updated_at)
        self.assertEqual(self.contact_a.updated_by_id, self.user.id)

    def test_other_fields_cfc_unchanged(self):
        """B2: 操作対象以外のフィールドの CFC は触られない。"""
        # organization を編集 → notes の CFC は不変
        original_notes_confirmed_at = self.cfc_notes.confirmed_at
        original_notes_updated_at = self.cfc_notes.updated_at

        self.contact_a.update_field("organization", "A-organization-new", self.user)

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
            organization="A-organization",
            notes="A-notes",
            # salutation_name を埋め is_manual=True とし、Contact.save() の自動補完を抑止する
            # （補完されると salutation_name の CFC が増えてカウントがずれるため）。
            salutation_name="テスト 様",
            salutation_name_is_manual=True,
            # Phase 7 段2-B：所有者ガード導入後、正常系はログインユーザーが所有者である前提。
            created_by=self.user,
        )
        self.person_a.primary_contact = self.contact_a
        self.person_a.save(update_fields=["primary_contact", "updated_at"])

        # CFC: organization mid、notes low、personal_phone low（unconfirmed_count 検証用）
        self.cfc_organization = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
        )
        self.cfc_notes = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="notes",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        self.cfc_personal_phone = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="personal_phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )

        # invalidate 検証用：(A,B) pending DC
        self.person_b = Person.objects.create()
        self.contact_b = Contact.objects.create(
            person=self.person_b,
            status=Contact.Status.PRIMARY,
            full_name="B-name",
            salutation_name="テスト 様",
            salutation_name_is_manual=True,
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
        """N1: DUPLICATE_CHECK_FIELDS（organization）修正 → 200 / 値更新 / CFC confirmed / DC invalidated。"""
        resp = self._post_json(
            self._url(),
            {"field_name": "organization", "new_value": "A-organization-new"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["field_name"], "organization")
        self.assertEqual(body["updated_value"], "A-organization-new")
        self.assertEqual(body["confidence_state"], "confirmed")

        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.organization, "A-organization-new")

        self.cfc_organization.refresh_from_db()
        self.assertIsNotNone(self.cfc_organization.confirmed_at)

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
            url, {"field_name": "organization", "new_value": "x"}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["success"])

    def test_e2_inactive_contact_forbidden(self):
        """E2: inactive Contact → 403。"""
        self.contact_a.status = Contact.Status.INACTIVE
        self.contact_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(),
            {"field_name": "organization", "new_value": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_e3_archived_person_forbidden(self):
        """E3: archived Person 配下 → 403。"""
        self.person_a.status = Person.Status.ARCHIVED
        self.person_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(),
            {"field_name": "organization", "new_value": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_e3b_merged_person_forbidden(self):
        """E3': merged Person 配下 → 403（防御的、論点 4）。"""
        # mark_as_merged は Person 単独で動かないので status を直接書き換え
        self.person_a.status = Person.Status.MERGED
        self.person_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(),
            {"field_name": "organization", "new_value": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_e4_unauthenticated_forbidden(self):
        """E4: 未ログイン → 403（論点 1、案 A）。"""
        c = Client()  # 未ログインクライアント
        resp = self._post_json(
            self._url(),
            {"field_name": "organization", "new_value": "x"},
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
            data=json.dumps({"field_name": "organization", "new_value": "x"}),
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
        # 初期：organization / notes / personal_phone の 3 件 unconfirmed
        resp = self._post_json(
            self._url(),
            {"field_name": "organization", "new_value": "A-organization-new"},
        )
        # organization が confirmed 化されたので残り 2 件
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
            self._url(), {"field_names": ["organization"]}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["confirmed_field_names"], ["organization"])
        self.assertEqual(body["unconfirmed_count"], 2)  # notes / personal_phone 残り

        self.cfc_organization.refresh_from_db()
        self.assertIsNotNone(self.cfc_organization.confirmed_at)

    def test_n2_multiple_fields_bulk(self):
        """N2: 複数フィールドの確認（一括確定）→ 200 / 全 CFC confirmed。"""
        resp = self._post_json(
            self._url(),
            {"field_names": ["organization", "notes", "personal_phone"]},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            sorted(body["confirmed_field_names"]),
            ["notes", "organization", "personal_phone"],
        )
        self.assertEqual(body["unconfirmed_count"], 0)

        for cfc in (self.cfc_organization, self.cfc_notes, self.cfc_personal_phone):
            cfc.refresh_from_db()
            self.assertIsNotNone(cfc.confirmed_at)

    def test_n3_already_confirmed_idempotent(self):
        """N3: 既に confirmed 済みのフィールド再指定 → 冪等動作。"""
        # 1 回目
        self._post_json(self._url(), {"field_names": ["organization"]})
        self.cfc_organization.refresh_from_db()
        first_confirmed_at = self.cfc_organization.confirmed_at
        self.assertIsNotNone(first_confirmed_at)

        # 2 回目（同じフィールド）
        resp = self._post_json(self._url(), {"field_names": ["organization"]})
        self.assertEqual(resp.status_code, 200)
        self.cfc_organization.refresh_from_db()
        # confirmed_at は更新される（mark_fields_as_confirmed の挙動）
        self.assertIsNotNone(self.cfc_organization.confirmed_at)

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
        resp = self._post_json(url, {"field_names": ["organization"]})
        self.assertEqual(resp.status_code, 404)

    def test_e2_inactive_contact_forbidden(self):
        """E2: inactive Contact → 403。"""
        self.contact_a.status = Contact.Status.INACTIVE
        self.contact_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(), {"field_names": ["organization"]}
        )
        self.assertEqual(resp.status_code, 403)

    def test_e3_archived_person_forbidden(self):
        """E3: archived Person 配下 → 403。"""
        self.person_a.status = Person.Status.ARCHIVED
        self.person_a.save(update_fields=["status", "updated_at"])
        resp = self._post_json(
            self._url(), {"field_names": ["organization"]}
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
        for cfc in (self.cfc_organization, self.cfc_notes, self.cfc_personal_phone):
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
            data=json.dumps({"field_names": ["organization"]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_e_unauthenticated_forbidden(self):
        """E: 未ログイン → 403（論点 1）。"""
        c = Client()
        resp = self._post_json(
            self._url(), {"field_names": ["organization"]}, client=c
        )
        self.assertEqual(resp.status_code, 403)

    # ---- レスポンス検証 ----

    def test_r1_confirmed_field_names_in_response(self):
        """R1: confirmed_field_names がリクエストの field_names を反映。"""
        resp = self._post_json(
            self._url(), {"field_names": ["organization", "personal_phone"]}
        )
        self.assertEqual(
            sorted(resp.json()["confirmed_field_names"]),
            ["organization", "personal_phone"],
        )

    def test_r2_unconfirmed_count_in_response(self):
        """R2: unconfirmed_count が処理後の値で正しく返る。"""
        # organization / notes / personal_phone の 3 件 unconfirmed が初期状態
        # organization を確認 → 残り 2 件
        resp = self._post_json(
            self._url(), {"field_names": ["organization"]}
        )
        self.assertEqual(resp.json()["unconfirmed_count"], 2)


class CanEditContactTests(TestCase):
    """can_edit_contact の単体テスト（Phase 7 段2-B、所有者ガードの正本）。

    判定 4 ケース：created_by 本人 / managed_by 本人 / 横断権限保持者 / 他人。
    """

    def setUp(self):
        from django.contrib.auth.models import Permission

        self.owner = User.objects.create_user(username="ce_owner", password="x")
        self.manager = User.objects.create_user(username="ce_manager", password="x")
        self.privileged = User.objects.create_user(username="ce_priv", password="x")
        self.stranger = User.objects.create_user(username="ce_stranger", password="x")

        # privileged に横断権限 contacts.edit_all_contacts を付与
        perm = Permission.objects.get(
            codename="edit_all_contacts", content_type__app_label="contacts"
        )
        self.privileged.user_permissions.add(perm)
        # has_perm のキャッシュを避けるため取り直す
        self.privileged = User.objects.get(pk=self.privileged.pk)

        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="Owner-name",
            created_by=self.owner,
            managed_by=self.manager,
        )

    def test_created_by_owner_can_edit(self):
        """created_by 本人は編集可。"""
        from contacts.services.permissions import can_edit_contact

        self.assertTrue(can_edit_contact(self.owner, self.contact))

    def test_managed_by_user_can_edit(self):
        """managed_by 本人は編集可。"""
        from contacts.services.permissions import can_edit_contact

        self.assertTrue(can_edit_contact(self.manager, self.contact))

    def test_cross_cutting_permission_can_edit(self):
        """横断権限 contacts.edit_all_contacts 保持者は編集可。"""
        from contacts.services.permissions import can_edit_contact

        self.assertTrue(can_edit_contact(self.privileged, self.contact))

    def test_stranger_cannot_edit(self):
        """所有者でも管理者でも横断権限保持者でもない他人は編集不可。"""
        from contacts.services.permissions import can_edit_contact

        self.assertFalse(can_edit_contact(self.stranger, self.contact))


class ContactAjaxOwnerGuardTests(_ContactAjaxTestBase):
    """AJAX 2 View の所有者ガード結合テスト（Phase 7 段2-B）。

    _ContactAjaxTestBase の contact_a は created_by=self.user。別ユーザーで
    ログインした場合に編集・確認が 403 で弾かれること、本人なら通ることを実証する。
    """

    def setUp(self):
        super().setUp()
        self.stranger = User.objects.create_user(
            username="d3c_stranger", password="dummy"
        )
        self.stranger_client = Client()
        self.stranger_client.force_login(self.stranger)

    def _update_url(self):
        return reverse(
            "contacts:ajax_update_field", kwargs={"pk": self.contact_a.pk}
        )

    def _confirm_url(self):
        return reverse(
            "contacts:ajax_confirm_fields", kwargs={"pk": self.contact_a.pk}
        )

    def test_update_field_by_stranger_forbidden(self):
        """他人による update_field → 403、値は変わらない。"""
        before = self.contact_a.organization
        resp = self._post_json(
            self._update_url(),
            {"field_name": "organization", "new_value": "HACKED"},
            client=self.stranger_client,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["success"])
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.organization, before)

    def test_update_field_by_owner_ok(self):
        """本人（created_by）による update_field → 200。"""
        resp = self._post_json(
            self._update_url(),
            {"field_name": "organization", "new_value": "owner-edit"},
        )
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.organization, "owner-edit")

    def test_confirm_fields_by_stranger_forbidden(self):
        """他人による confirm_fields → 403。"""
        resp = self._post_json(
            self._confirm_url(),
            {"field_names": ["organization"]},
            client=self.stranger_client,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["success"])

    def test_confirm_fields_by_owner_ok(self):
        """本人（created_by）による confirm_fields → 200。"""
        resp = self._post_json(
            self._confirm_url(),
            {"field_names": ["organization"]},
        )
        self.assertEqual(resp.status_code, 200)


class ContactDetailViewTests(TestCase):
    """ContactDetailView の単体テスト（D-3b §8.1）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="d3b_test_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        # primary Contact のセットアップ
        self.person_a = Person.objects.create()
        self.contact_a = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.PRIMARY,
            full_name="A-name",
            organization="A-organization",
        )
        self.person_a.primary_contact = self.contact_a
        self.person_a.save(update_fields=["primary_contact", "updated_at"])

        # CFC（mid/low）
        self.cfc_organization = ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
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

    def test_n10_unconfirmed_band_removed(self):
        """N10: 要確認帯（未確認件数サマリー＋一括確定ボタン）は撤去済み。

        未確認 CFC が 1 件以上あっても（setUp の cfc_organization=mid）、帯・一括ボタン・
        件数サマリーは出さない。個別フィールドの確認 UI（確認OK/要修正）は別途残る。
        """
        resp = self.client.get(self._url())
        body = resp.content.decode()
        # 帯は常に無い：一括確定ボタン・件数サマリー文言・summary タグ出力のいずれも出ない。
        self.assertNotIn("js-bulk-confirm-btn", body)
        self.assertNotIn("一括確定", body)
        self.assertNotIn("確認すべきフィールドはありません", body)
        # context にも帯用キーは投入しない。
        self.assertNotIn("unconfirmed_count", resp.context)
        # 個別フィールドの確認 UI（app.js 配線済み）は残る。
        self.assertIn("js-contact-field-action", body)

    # ---- 異常系 ----

    def test_e1_contact_not_found(self):
        """E1: 存在しない Contact → 404。"""
        import uuid as _uuid

        url = reverse(
            "contacts:contact_detail", kwargs={"pk": _uuid.uuid4()}
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_e2_unauthenticated_redirects_to_login(self):
        """E2: 未ログイン → 302（LoginRequiredMixin でログインへリダイレクト、Phase 7 段1）。"""
        c = Client()  # 未ログイン
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith(reverse("accounts:login")))

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

    def test_r5_push_current_makes_detail_a_back_target(self):
        """R5: 起点ハブ化（案A）。push_current で back_stack に「人物詳細」が積まれ、
        子画面の戻り先になる。keys=["page"] は GET に無いので path-only で積まれる。"""
        resp = self.client.get(self._url())
        back = resp.context["back"]
        titles = [e.get("title") for e in back.back_stack]
        urls = [e.get("url") for e in back.back_stack]
        self.assertIn("人物詳細", titles)
        # path-only（クエリは付かない）。
        self.assertIn(self._url(), urls)

    def test_r6_push_current_no_double_push(self):
        """R6: 二重 push 防止。back_stack の先頭が既に人物詳細（同一 view_name+kwargs）なら
        重複チェックで再 push されない（リロード・子からの戻り想定）。"""
        from back_navigator.back_navigator import BackNavigator

        # 1 回目：人物詳細を積んだ状態の back_stack をエンコード。
        resp1 = self.client.get(self._url())
        back1 = resp1.context["back"]
        encoded = back1._calc_encode_stack(back1.back_stack)

        # 2 回目：その back_stack を付けて同じ人物詳細へ。重複チェックで二重に積まれない。
        resp2 = self.client.get(
            self._url(), {BackNavigator.PARAM_NAME: encoded}
        )
        back2 = resp2.context["back"]
        self.assertEqual(
            [e.get("title") for e in back2.back_stack].count("人物詳細"), 1
        )

    def test_r2_template_rendered(self):
        """R2: テンプレートが正しくレンダリング、Contact 名とタイトル「人物詳細」が含まれる。"""
        resp = self.client.get(self._url())
        body = resp.content.decode()
        self.assertIn("A-name", body)
        # タイトルは集約設計に合わせ「人物詳細」（HIG 原則4）。
        self.assertIn("人物詳細", body)

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
        # data-confidence-state（organization は mid CFC → "mid"）
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

        確認済みバッジは恒常表示しない（HIG v1.4 原則4）。行フック・
        data-confidence-state="confirmed"・バッジスロットは維持されるが、
        緑バッジ・「確認済み」テキストはサーバー描画されない
        （確認直後の一時表示は app.js の applyConfirmedState が担う）。
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
        # 行レベルのフック・状態属性・バッジスロットは維持
        self.assertIn("js-contact-field-row", rendered)
        self.assertIn('data-confidence-state="confirmed"', rendered)
        self.assertIn("js-contact-field-badge-slot", rendered)
        # 確認済みの恒常バッジは描画しない（原則4）
        self.assertNotIn("app-status-badge--success", rendered)
        self.assertNotIn("確認済み", rendered)
        # 修正 UI フックは出力されない
        self.assertNotIn("js-contact-field-action", rendered)
        self.assertNotIn("js-contact-field-confirm-btn", rendered)
        self.assertNotIn("js-contact-field-edit-form", rendered)

    def test_r7_mid_low_field_has_edit_ui(self):
        """R7: mid / low フィールドには修正 UI が出力される（既存 R3 の補強）。"""
        # organization は mid CFC（setUp で作成済み）
        rendered_mid = self._render_field("organization", "A-organization")
        self.assertIn('data-confidence-state="mid"', rendered_mid)
        self.assertIn("js-contact-field-action", rendered_mid)
        self.assertIn("js-contact-field-confirm-btn", rendered_mid)
        self.assertIn("js-contact-field-edit-form", rendered_mid)

        # personal_phone に low CFC を作成
        ContactFieldConfidence.objects.create(
            contact=self.contact_a,
            field_name="personal_phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        # field_confidences は再取得
        self.contact_a.refresh_from_db()
        rendered_low = self._render_field("personal_phone", "03-1234")
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


class ContactDetailSelfLinkButtonTests(TestCase):
    """contact_detail「このユーザーで紐付ける」ボタン（self-link 専用）の表示条件が
    出口ガード（email_match＋person_active）に揃うことの検証。"""

    LABEL = "このユーザーで紐付ける"

    def setUp(self):
        self.user = User.objects.create_user(
            username="selflink_user", password="dummy", email="me@example.com"
        )
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)

    def _make_primary(self, email, person_status=Person.Status.ACTIVE,
                      full_name="対象", linked_user=None):
        person = Person.objects.create(status=person_status)
        c = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY,
            full_name=full_name, email=email,
        )
        person.primary_contact = c
        person.save(update_fields=["primary_contact", "updated_at"])
        if linked_user is not None:
            linked_user.person = person
            linked_user.save(update_fields=["person"])
        return person, c

    def _url(self, c):
        return reverse("contacts:contact_detail", kwargs={"pk": c.pk})

    def test_shown_when_email_match_and_active(self):
        _, c = self._make_primary("me@example.com")
        resp = self.client.get(self._url(c))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["email_match"])
        self.assertTrue(resp.context["person_active"])
        self.assertContains(resp, self.LABEL)

    def test_hidden_when_email_mismatch(self):
        _, c = self._make_primary("other@example.com")
        resp = self.client.get(self._url(c))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["email_match"])
        self.assertNotContains(resp, self.LABEL)

    def test_hidden_when_person_not_active(self):
        # 非active（archived）Person の primary はメール一致でもボタンを出さない。
        _, c = self._make_primary("me@example.com",
                                  person_status=Person.Status.ARCHIVED)
        resp = self.client.get(self._url(c))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["person_active"])
        self.assertNotContains(resp, self.LABEL)

    def test_linked_to_self_shows_unlink_not_selflink(self):
        _, c = self._make_primary("me@example.com", linked_user=self.user)
        resp = self.client.get(self._url(c))
        self.assertEqual(resp.status_code, 200)
        # 本人紐付け済み → 解除導線。self-link ボタンは出ない。
        self.assertContains(resp, "紐付け済み（解除）")
        self.assertNotContains(resp, self.LABEL)


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

    def test_c2_mid_unconfirmed_shows_badge(self):
        """C2: mid AND confirmed_at IS NULL → 中バッジ表示。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
        )
        rendered = self._render(self.contact, "organization")
        self.assertIn("app-status-badge--warning", rendered)
        self.assertIn("中", rendered)

    def test_c3_low_unconfirmed_shows_badge(self):
        """C3: low AND confirmed_at IS NULL → 低バッジ表示。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="personal_phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        rendered = self._render(self.contact, "personal_phone")
        self.assertIn("app-status-badge--error", rendered)
        self.assertIn("低", rendered)

    def test_c4_confirmed_shows_nothing(self):
        """C4: confirmed_at IS NOT NULL → 恒常バッジを描画しない（HIG v1.4 原則4）。

        確認済みは恒常表示しない。確認直後の一時バッジは app.js が
        クライアント挿入する（原則5）。サーバー描画は空文字。
        """
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        rendered = self._render(self.contact, "email")
        self.assertEqual(rendered.strip(), "")
        self.assertNotIn("確認済み", rendered)
        self.assertNotIn("app-status-badge--success", rendered)


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
            # salutation_name を埋め is_manual=True とし、Contact.save() の自動補完を抑止する
            # （補完されると salutation_name の CFC が増えて表示件数がずれるため）。
            salutation_name="テスト 様",
            salutation_name_is_manual=True,
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
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="personal_phone",
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
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
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

    7 フィールド検索（tel は personal_phone/mobile_phone/personal_fax の OR）、include_inactive、
    person.status="active" 絞り込み、updated_at 降順、ページネーション、
    未認証 200 を検証する。
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="contact_list_test_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.person_a = Person.objects.create()
        self.contact_a = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.PRIMARY,
            full_name="Alice Smith",
            organization="Acme Corp",
            department="Sales",
            title="Manager",
            email="alice@acme.example",
            personal_phone="03-1234-5678",
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

    def test_search_and_name_organization(self):
        """name と organization を同時指定で AND 検索（初回 primary フィルタ下）。"""
        c1 = self._make_primary(
            full_name="Alice Tanaka", organization="Wonder Corp"
        )
        c2 = self._make_primary(
            full_name="Bob Smith", organization="Acme Industries"
        )
        c3 = self._make_primary(
            full_name="Alice Brown", organization="Acme Group"
        )

        resp = self.client.get(self.url, {"name": "Alice", "organization": "Acme"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(self.contact_a.id, ids)
        self.assertIn(c3.id, ids)
        self.assertNotIn(c1.id, ids)
        self.assertNotIn(c2.id, ids)

    def test_search_tel_or_personal_phone_mobile_phone_personal_fax(self):
        """tel は personal_phone / mobile_phone / personal_fax の OR 一致。"""
        c_mobile_phone = self._make_primary(
            full_name="MobOnly", mobile_phone="090-1111-2222"
        )
        c_personal_fax = self._make_primary(full_name="FaxOnly", personal_fax="06-9999-8888")
        # setUp の contact_a は personal_phone="03-1234-5678"

        resp = self.client.get(self.url, {"tel": "1234"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(self.contact_a.id, ids)
        self.assertNotIn(c_mobile_phone.id, ids)
        self.assertNotIn(c_personal_fax.id, ids)

        resp = self.client.get(self.url, {"tel": "090-1111"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(c_mobile_phone.id, ids)
        self.assertNotIn(self.contact_a.id, ids)
        self.assertNotIn(c_personal_fax.id, ids)

        resp = self.client.get(self.url, {"tel": "06-9999"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertIn(c_personal_fax.id, ids)
        self.assertNotIn(self.contact_a.id, ids)
        self.assertNotIn(c_mobile_phone.id, ids)

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

    def test_unauthenticated_redirects_to_login(self):
        """未ログイン → 302（LoginRequiredMixin でログインへリダイレクト、ContactDetailView と同じ）。"""
        c = Client()
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith(reverse("accounts:login")))

    # ---- HIG v1.5 §6.2：多段サーバー側ソート（単一 sort パラメータ、例 ?sort=company,-title,name）----

    def test_single_key_sort_asc_and_desc(self):
        """?sort=company（昇順）/ ?sort=-company（降順）で会社順に並ぶ。"""
        c_c = self._make_primary(full_name="C", organization="Cccorp")
        c_a = self._make_primary(full_name="A", organization="Aaacorp")
        c_b = self._make_primary(full_name="B", organization="Bbbcorp")

        resp = self.client.get(self.url, {"sort": "company"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertLess(ids.index(c_a.id), ids.index(c_b.id))
        self.assertLess(ids.index(c_b.id), ids.index(c_c.id))
        self.assertTrue(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "asc"})

        resp = self.client.get(self.url, {"sort": "-company"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertLess(ids.index(c_c.id), ids.index(c_b.id))
        self.assertLess(ids.index(c_b.id), ids.index(c_a.id))
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "desc"})

    def test_multi_key_sort_priority(self):
        """?sort=company,-name で 会社昇順 → 同社内は氏名（読み）降順（多段・全件）。"""
        # 同じ会社 "Same" の 2 人。氏名ソートは phonetic_name 順なので読みを付ける。
        c_same_z = self._make_primary(
            full_name="Zoe", organization="Same", phonetic_name="ゾエ"
        )
        c_same_a = self._make_primary(
            full_name="Amy", organization="Same", phonetic_name="エイミー"
        )
        c_other = self._make_primary(
            full_name="Mike", organization="Zzcorp", phonetic_name="マイク"
        )
        resp = self.client.get(self.url, {"sort": "company,-name"})
        ids = [c.id for c in resp.context["contacts"]]
        # 会社 "Same" グループが "Zzcorp" より前、グループ内は読み降順 ゾエ→エイミー
        self.assertLess(ids.index(c_same_z.id), ids.index(c_same_a.id))
        self.assertLess(ids.index(c_same_a.id), ids.index(c_other.id))
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "asc"})
        self.assertEqual(resp.context["sort_rows"][1], {"key": "name", "dir": "desc"})

    def test_name_sort_uses_phonetic_name(self):
        """氏名ソート（?sort=name）は漢字コード順でなく phonetic_name（カタカナ読み）の五十音順。"""
        # 漢字順なら 佐(U+4F50) < 田(U+7530) < 鈴(U+9234) で sato→tanaka→suzuki になり、
        # 読み順（sato→suzuki→tanaka）と食い違うため、読みで並んでいることを区別できる。
        c_tanaka = self._make_primary(full_name="田中", phonetic_name="タナカ")
        c_suzuki = self._make_primary(full_name="鈴木", phonetic_name="スズキ")
        c_sato = self._make_primary(full_name="佐藤", phonetic_name="サトウ")

        resp = self.client.get(self.url, {"sort": "name"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertLess(ids.index(c_sato.id), ids.index(c_suzuki.id))
        self.assertLess(ids.index(c_suzuki.id), ids.index(c_tanaka.id))

        resp = self.client.get(self.url, {"sort": "-name"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertLess(ids.index(c_tanaka.id), ids.index(c_suzuki.id))
        self.assertLess(ids.index(c_suzuki.id), ids.index(c_sato.id))

    def test_invalid_keys_ignored_in_multi(self):
        """許可リスト外キー（department/address）は無視され、有効キーだけ適用される。"""
        c_z = self._make_primary(full_name="Z", organization="Zzcorp")
        c_a = self._make_primary(full_name="A", organization="Aaacorp")
        resp = self.client.get(self.url, {"sort": "department,company,address"})
        ids = [c.id for c in resp.context["contacts"]]
        self.assertLess(ids.index(c_a.id), ids.index(c_z.id))
        self.assertTrue(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "asc"})

    def test_all_invalid_or_empty_sort_keeps_default(self):
        """全キーが不正（or 空）なら sort 無効＝既定の並び（-updated_at,-created_at）を維持。"""
        resp = self.client.get(self.url, {"sort": "department,address"})
        self.assertFalse(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_value"], "")
        self.assertTrue(all(r["key"] == "" for r in resp.context["sort_rows"]))

        resp_empty = self.client.get(self.url, {"sort": ""})
        self.assertFalse(resp_empty.context["sort_is_active"])

    def test_no_sort_param_keeps_default_order(self):
        """sort 未指定なら既定並び（updated_at 降順）を維持し、折りたたみは閉じ判定。"""
        from datetime import timedelta

        c_old = self._make_primary(full_name="Old", organization="ZZZ")
        c_new = self._make_primary(full_name="New", organization="AAA")
        now = timezone.now()
        Contact.objects.filter(pk=c_old.pk).update(updated_at=now - timedelta(hours=1))
        Contact.objects.filter(pk=c_new.pk).update(updated_at=now)

        resp = self.client.get(self.url)
        ids = [c.id for c in resp.context["contacts"]]
        # 会社順ではなく updated_at 降順（new が old より前）
        self.assertLess(ids.index(c_new.id), ids.index(c_old.id))
        self.assertFalse(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_value"], "")

    def test_duplicate_key_first_wins(self):
        """同一キーの二重指定は先勝ち（?sort=-company,company → company 降順のみ）。"""
        resp = self.client.get(self.url, {"sort": "-company,company"})
        rows = resp.context["sort_rows"]
        self.assertEqual(rows[0], {"key": "company", "dir": "desc"})
        self.assertEqual(rows[1]["key"], "")

    def test_sort_control_and_page_sort_markers_rendered(self):
        """検索フォーム内ソートコントロール＋補助JSソート＋列切替のマーカーが描画される。"""
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        # 検索フォーム内ソートコントロール（共有 _sort_control.html）
        self.assertIn("js-person-sort-control", body)
        self.assertIn('name="sort"', body)
        for label in ("指定なし", "氏名", "会社", "役職", "連絡先"):
            self.assertIn(label, body)
        # 補助JSソート（contacts 用フック）＋氏名セルの読みキー
        self.assertIn("js-contact-page-sort", body)
        self.assertIn("data-sort-col", body)
        self.assertIn("data-sort-key", body)
        # 列切替（contacts 専用 localStorage キー）
        self.assertIn("data-col-key", body)
        self.assertIn("contact_list_visible_columns", body)

    def test_pagination_preserves_sort_query(self):
        """ソート状態でページ送りしても sort が失われない（共有 _pagination.html）。"""
        for i in range(25):
            self._make_primary(full_name=f"pp-{i:02d}", organization=f"org{i:02d}")
        resp = self.client.get(self.url, {"sort": "-company"})
        self.assertTrue(resp.context["is_paginated"])
        body = resp.content.decode("utf-8")
        self.assertTrue("sort=-company" in body or "sort=%2Dcompany" in body)

    def test_push_current_captures_sort(self):
        """戻る復元用に push_current が sort を取り込む（HIG §6.1）。"""
        resp = self.client.get(self.url, {"sort": "company,-title"})
        back = resp.context["back"]
        urls = " ".join(entry.get("url", "") for entry in back.back_stack)
        self.assertIn("sort=", urls)
        self.assertIn("company", urls)


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

    def test_error_class_is_app_error_list(self):
        """ContactBaseForm の error_class が AppErrorList。

        D-4d-1 第 3 弾 §2-5-B。子フォームへの自動波及の起点。
        """
        self.assertIs(ContactBaseForm.error_class, AppErrorList)


class AppErrorListTests(TestCase):
    """AppErrorList の出力検証（D-4d-1 第 3 弾 §2 修正項目 5）。"""

    def test_str_output_includes_app_form_error_class(self):
        """AppErrorList を直接 str 化した出力に `app-form__error` クラスが含まれる。"""
        el = AppErrorList(["dummy error"])
        self.assertIn("app-form__error", str(el))
        self.assertIn("errorlist", str(el))

    def test_contact_update_form_field_errors_use_app_form_error(self):
        """ContactUpdateForm の field エラー HTML に `app-form__error` クラスが付与される。"""
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            full_name="A",
        )
        # change_reason は ChoiceField (required=True) なので空文字で invalid 化
        data = {f: getattr(contact, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["change_reason"] = ""
        data["note"] = ""
        form = ContactUpdateForm(data=data, target_contact=contact)
        self.assertFalse(form.is_valid())
        self.assertIn(
            'class="errorlist app-form__error"',
            str(form["change_reason"].errors),
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
            organization="A-organization",
            email="a@example.com",
        )
        # organization: mid / email: low / personal_phone: low + confirmed 済み
        self.cfc_organization = ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
        )
        self.cfc_email = ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="personal_phone",
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
        self.assertIn("confirmed_organization", form.fields)  # mid
        self.assertIn("confirmed_email", form.fields)    # low
        # personal_phone は confirmed 済み → 追加されない
        self.assertNotIn("confirmed_personal_phone", form.fields)
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
        data["confirmed_organization"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_clean_fails_when_checkbox_off(self):
        """確認チェックが 1 つでも OFF → is_valid() False、当該フィールドにエラー。"""
        data = self._base_data()
        # confirmed_organization は OFF（キー自体を入れない）
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed_organization", form.errors)
        self.assertNotIn("confirmed_email", form.errors)

    def test_change_reason_is_required(self):
        """change_reason 必須 → 未指定で is_valid() False。"""
        data = self._base_data(include_change_reason=False)
        data["confirmed_organization"] = "on"
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
        data["confirmed_organization"] = "on"
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
        data["confirmed_organization"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)
        names = form.confirmed_field_names()
        self.assertIn("organization", names)
        self.assertIn("email", names)

    def test_confirmed_field_names_with_edited_field(self):
        """編集された high フィールドも confirmed_field_names() に含まれる。"""
        data = self._base_data()
        # full_name を編集（CFC なし＝high なので、編集だけで confirmed 扱い）
        data["full_name"] = "違う名前"
        data["confirmed_organization"] = "on"
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
        self.assertIn("confirmed_organization", form.fields)
        self.assertIn("confirmed_email", form.fields)

    def test_clean_passes_without_change_reason(self):
        """change_reason 不要、確認チェック ON で is_valid() True。"""
        data = self._base_data(include_change_reason=False)
        data["confirmed_organization"] = "on"
        data["confirmed_email"] = "on"
        form = ContactUpdateActiveForm(data=data, target_contact=self.contact)
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_clean_fails_when_checkbox_off(self):
        """親同様、確認チェック OFF でエラー。"""
        data = self._base_data(include_change_reason=False)
        data["confirmed_email"] = "on"
        form = ContactUpdateActiveForm(data=data, target_contact=self.contact)
        self.assertFalse(form.is_valid())
        self.assertIn("confirmed_organization", form.errors)


class UpdateActiveContactViewTests(TestCase):
    """UpdateActiveContactView（13 番、仕様書 §11.6 / §11.7）の単体テスト。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="update_active_user", password="dummy"
        )
        _grant_contact_perms(self.user)
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
            organization="A-active-co",
            email="active@example.com",
        )
        self.cfc_organization = ContactFieldConfidence.objects.create(
            contact=self.active,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
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
        data.update(_empty_sns_management_form())
        return data

    # ---- GET ----

    def test_get_active_returns_200(self):
        """active Contact → 200、change_reason フィールド非搭載・confirmed_organization 搭載。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertNotIn("change_reason", form.fields)
        self.assertIn("confirmed_organization", form.fields)

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
        data["organization"] = "A-active-co-new"
        data["confirmed_organization"] = "on"

        resp = self.client.post(self._url(), data=data)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse(
                "contacts:contact_detail", kwargs={"pk": self.active.pk}
            ),
        )

        self.active.refresh_from_db()
        self.assertEqual(self.active.organization, "A-active-co-new")

        self.cfc_organization.refresh_from_db()
        self.assertIsNotNone(self.cfc_organization.confirmed_at)
        self.assertEqual(self.cfc_organization.confirmed_by_id, self.user.id)

    def test_post_with_checkbox_off_shows_form_error(self):
        """確認チェック OFF → フォーム再表示、エラー含む、fix() は呼ばれない。"""
        data = self._base_post_data(self.active)
        data["organization"] = "A-active-co-changed"
        # confirmed_organization は OFF

        resp = self.client.post(self._url(), data=data)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("confirmed_organization", resp.context["form"].errors)

        # fix() は呼ばれていない → 値もCFCも変更なし
        self.active.refresh_from_db()
        self.assertEqual(self.active.organization, "A-active-co")
        self.cfc_organization.refresh_from_db()
        self.assertIsNone(self.cfc_organization.confirmed_at)


# ======================================================================
# D-Form ステップ4：UpdatePrimaryContactView（12 番）のテスト
# ======================================================================


class UpdatePrimaryContactViewTests(TestCase):
    """UpdatePrimaryContactView（12 番、§11.3 / §11.4.1 / §11.4.2 / §10.5.2）の単体テスト。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="update_primary_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P-name",
            organization="P-co",
            email="p@example.com",
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])

        # organization に mid CFC（fix で confirmed 化される検証用、
        # 同時に確認 CB バリデーションの対象）
        self.cfc_organization = ContactFieldConfidence.objects.create(
            contact=self.primary,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
        )

        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, contact=None):
        return reverse(
            "contacts:contact_update_primary",
            kwargs={"pk": (contact or self.primary).pk},
        )

    def _base_post_data(self, contact, change_reason="fix"):
        data = {f: getattr(contact, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["change_reason"] = change_reason
        data["note"] = ""
        data.update(_empty_sns_management_form())
        return data

    # ---- GET ----

    def test_get_primary_returns_200(self):
        """primary Contact → 200、ContactUpdateForm（change_reason あり）が context に。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertIn("change_reason", form.fields)
        self.assertIn("note", form.fields)
        # low/mid CFC（organization）の確認 CB が動的追加されている
        self.assertIn("confirmed_organization", form.fields)

    def test_get_active_returns_404(self):
        """active Contact → 404（このViewはprimary専用）。"""
        active_person = Person.objects.create()
        active_primary = Contact.objects.create(
            person=active_person,
            status=Contact.Status.PRIMARY,
            full_name="A-primary",
        )
        active_person.primary_contact = active_primary
        active_person.save(
            update_fields=["primary_contact", "updated_at"]
        )
        active_contact = Contact.objects.create(
            person=active_person,
            status=Contact.Status.ACTIVE,
            full_name="A-active",
        )
        resp = self.client.get(self._url(active_contact))
        self.assertEqual(resp.status_code, 404)

    def test_get_inactive_returns_404(self):
        """inactive Contact → 404。"""
        inactive_person = Person.objects.create()
        inactive_primary = Contact.objects.create(
            person=inactive_person,
            status=Contact.Status.PRIMARY,
            full_name="I-primary",
        )
        inactive_person.primary_contact = inactive_primary
        inactive_person.save(
            update_fields=["primary_contact", "updated_at"]
        )
        inactive_contact = Contact.objects.create(
            person=inactive_person,
            status=Contact.Status.INACTIVE,
            full_name="I-inactive",
        )
        resp = self.client.get(self._url(inactive_contact))
        self.assertEqual(resp.status_code, 404)

    def test_get_nonexistent_returns_404(self):
        """存在しない pk → 404。"""
        import uuid as _uuid

        url = reverse(
            "contacts:contact_update_primary",
            kwargs={"pk": _uuid.uuid4()},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_redirects(self):
        """LoginRequiredMixin → 未ログインは login にリダイレクト（302）。"""
        c = Client()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)

    # ---- POST fix ----

    def test_post_fix_updates_contact_fields(self):
        """fix で Contact フィールドが上書き、Person.primary_contact 変わらず、
        CFC が confirmed 化、Person 詳細画面へリダイレクト（仕様書 §10.5.2）。"""
        data = self._base_post_data(self.primary, change_reason="fix")
        data["organization"] = "P-co-new"
        data["confirmed_organization"] = "on"

        resp = self.client.post(self._url(), data=data)

        self.assertEqual(resp.status_code, 302)
        expected_url = reverse(
            "persons:person_detail", kwargs={"pk": self.person.pk}
        )
        self.assertEqual(resp.url, expected_url)

        # Contact フィールドが更新されている
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.organization, "P-co-new")

        # Person.primary_contact は同じ Contact のまま
        self.person.refresh_from_db()
        self.assertEqual(self.person.primary_contact_id, self.primary.pk)

        # CFC が confirmed 化されている
        self.cfc_organization.refresh_from_db()
        self.assertIsNotNone(self.cfc_organization.confirmed_at)
        self.assertEqual(self.cfc_organization.confirmed_by_id, self.user.id)

    # ---- POST transfer 系（4 値）----

    def test_post_transfer_series_creates_new_primary_and_inactivates_old(self):
        """transfer / promotion / job_change / name_change の 4 値で、
        新規 Contact が primary に昇格、旧 primary が inactive 化、CFC は新規未作成・旧側保持
        （仕様書 §11.4.1 / §11.4.2 / §10.6.4 ケース1）。"""
        for reason in ["transfer", "promotion", "job_change", "name_change"]:
            with self.subTest(reason=reason):
                # 各反復ごとに独立 Person で primary 1 件をセットアップ
                person = Person.objects.create()
                old_primary = Contact.objects.create(
                    person=person,
                    status=Contact.Status.PRIMARY,
                    full_name=f"old-{reason}",
                    organization=f"old-co-{reason}",
                )
                person.primary_contact = old_primary
                person.save(
                    update_fields=["primary_contact", "updated_at"]
                )
                # 旧 primary に mid CFC（後で保持されているかを検証）
                old_cfc = ContactFieldConfidence.objects.create(
                    contact=old_primary,
                    field_name="organization",
                    confidence=ContactFieldConfidence.Confidence.MID,
                )

                url = reverse(
                    "contacts:contact_update_primary",
                    kwargs={"pk": old_primary.pk},
                )
                data = self._base_post_data(old_primary, change_reason=reason)
                data["full_name"] = f"new-{reason}"
                data["organization"] = f"new-co-{reason}"
                # ContactUpdateForm の clean は low/mid CFC の確認 CB を要求するので ON
                data["confirmed_organization"] = "on"

                resp = self.client.post(url, data=data)

                # 成功 → Person 詳細画面へリダイレクト
                self.assertEqual(resp.status_code, 302)
                expected_url = reverse(
                    "persons:person_detail", kwargs={"pk": person.pk}
                )
                self.assertEqual(resp.url, expected_url)

                # 新規 Contact が primary になっている（partial unique 制約により Person 配下に 1 件）
                person.refresh_from_db()
                new_primary = person.primary_contact
                self.assertIsNotNone(new_primary)
                self.assertNotEqual(new_primary.pk, old_primary.pk)
                self.assertEqual(new_primary.status, Contact.Status.PRIMARY)
                self.assertEqual(new_primary.full_name, f"new-{reason}")
                self.assertEqual(new_primary.organization, f"new-co-{reason}")

                # 旧 primary が inactive 化されている
                old_primary.refresh_from_db()
                self.assertEqual(old_primary.status, Contact.Status.INACTIVE)

                # 新規 Contact には CFC レコードが作成されていない（§10.6.4 ケース1）
                self.assertEqual(
                    ContactFieldConfidence.objects.filter(
                        contact=new_primary
                    ).count(),
                    0,
                )

                # 旧 primary の既存 CFC レコードは保持されている
                self.assertTrue(
                    ContactFieldConfidence.objects.filter(
                        pk=old_cfc.pk
                    ).exists()
                )

    # ---- 確認 CB バリデーション ----

    def test_post_with_unconfirmed_low_mid_field_returns_error(self):
        """low/mid CFC のフィールドの確認 CB 未 ON → フォーム検証エラー、Contact 不変。"""
        data = self._base_post_data(self.primary, change_reason="fix")
        data["organization"] = "P-co-attempted-change"
        # confirmed_organization を意図的に含めない（OFF）

        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("confirmed_organization", resp.context["form"].errors)

        # Contact フィールド・CFC ともに変わっていない
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.organization, "P-co")
        self.cfc_organization.refresh_from_db()
        self.assertIsNone(self.cfc_organization.confirmed_at)


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
        data["organization"] = "別会社"
        data["salutation_name"] = "別肩書 様"  # Phase F1 で 9 番も salutation 必須化
        form = ContactAddAdditionalRoleForm(data=data, person=self.person)
        self.assertTrue(form.is_valid(), msg=form.errors)
        new_contact = form.get_update_contact()
        # 未保存（DB に存在しない）
        self.assertTrue(new_contact._state.adding)
        # 値は反映（full_name は §3.4 正規化で空白除去："別肩書 太郎" → "別肩書太郎"）
        self.assertEqual(new_contact.full_name, "別肩書太郎")
        self.assertEqual(new_contact.organization, "別会社")
        # status / person は未設定（View 側責務、§10.12）
        self.assertEqual(new_contact.status, "")
        self.assertIsNone(new_contact.person_id)


# ======================================================================
# D-Form ステップ3a：10 番 ContactCreateView + 14 番 PreviewContactView
# ======================================================================


class ContactCreateViewTests(TestCase):
    """ContactCreateView（10 番、仕様書 §11.4.4 / §11.6.5）の単体テスト。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="contact_create_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("contacts:contact_create")

    def _base_post_data(self):
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data.update(_empty_sns_management_form())
        return data

    # ---- GET ----

    def test_get_returns_200_with_form(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)
        self.assertTemplateUsed(resp, "contacts/contact_create.html")

    def test_get_unauthenticated_redirects(self):
        """LoginRequiredMixin → 未ログインは login にリダイレクト（302）。"""
        c = Client()
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 302)

    # ---- POST 検証 NG ----

    def test_post_invalid_redisplays_form(self):
        """max_length 違反データ → 200 でフォーム再表示、Contact 未作成。"""
        data = self._base_post_data()
        data["full_name"] = "x" * 300  # max_length=255 違反
        person_count_before = Person.objects.count()
        contact_count_before = Contact.objects.count()

        resp = self.client.post(self.url, data=data)

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "contacts/contact_create.html")
        self.assertIn("full_name", resp.context["form"].errors)
        self.assertEqual(Person.objects.count(), person_count_before)
        self.assertEqual(Contact.objects.count(), contact_count_before)

    # ---- POST 検証 OK + 候補なし ----

    def test_post_valid_no_duplicates_creates_and_redirects(self):
        """重複候補なし → Person + primary Contact 作成、Contact 詳細画面リダイレクト。"""
        data = self._base_post_data()
        data["full_name"] = "新規 太郎"
        data["organization"] = "新会社"
        data["salutation_name"] = "新規 様"  # Phase D §3.5 で必須化

        resp = self.client.post(self.url, data=data)

        self.assertEqual(resp.status_code, 302)

        # full_name は §3.4 正規化で空白除去される（"新規 太郎" → "新規太郎"）
        new_contact = Contact.objects.filter(full_name="新規太郎").first()
        self.assertIsNotNone(new_contact)
        self.assertEqual(new_contact.status, Contact.Status.PRIMARY)
        self.assertEqual(new_contact.created_by_id, self.user.id)
        self.assertEqual(new_contact.updated_by_id, self.user.id)

        new_person = new_contact.person
        self.assertEqual(new_person.status, Person.Status.ACTIVE)
        self.assertEqual(new_person.primary_contact_id, new_contact.pk)

        self.assertEqual(
            resp.url,
            reverse(
                "contacts:contact_detail", kwargs={"pk": new_contact.pk}
            ),
        )

    def test_post_does_not_create_field_confidence_records(self):
        """新規作成では ContactFieldConfidence は作成されない（仕様書 §10.6.4）。"""
        cfc_before = ContactFieldConfidence.objects.count()
        data = self._base_post_data()
        data["full_name"] = "CFC 不要"
        # salutation_name を埋めて Contact.save() の自動補完を回避する（補完されると
        # salutation_name の CFC が 1 件作られる）。Phase D で Form が必須化 + is_manual セット。
        data["salutation_name"] = "テスト 様"
        resp = self.client.post(self.url, data=data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ContactFieldConfidence.objects.count(), cfc_before)

    # ---- POST 検証 OK + 候補あり ----

    def test_post_with_duplicate_shows_duplicates_screen(self):
        """possible_high 以上の候補あり → 確認画面表示、Person / Contact 未作成。"""
        # full_name(40) + email(80) + mobile_phone(80) = 200 → POSSIBLE_HIGH 以上を狙う
        # 既存側は ORM 直作成で正規化を経ないため、POST 側の正規化後の値（full_name="既存太郎" /
        # mobile_phone="09012345678"）に揃えて重複判定が成立するようにする（§3.4 正規化との整合）。
        existing_person = Person.objects.create()
        existing = Contact.objects.create(
            person=existing_person,
            status=Contact.Status.PRIMARY,
            full_name="既存太郎",
            email="taro@example.com",
            mobile_phone="09012345678",
        )
        existing_person.primary_contact = existing
        existing_person.save(update_fields=["primary_contact", "updated_at"])

        person_count_before = Person.objects.count()
        contact_count_before = Contact.objects.count()

        data = self._base_post_data()
        data["full_name"] = "既存 太郎"
        data["email"] = "taro@example.com"
        data["mobile_phone"] = "090-1234-5678"
        data["salutation_name"] = "既存 様"  # Phase D §3.5 で必須化

        resp = self.client.post(self.url, data=data)

        # 確認画面（duplicates_template）が表示される
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(
            resp, "contacts/contact_create_duplicates.html"
        )
        self.assertIn("top5", resp.context)
        self.assertEqual(len(resp.context["top5"]), 1)
        candidate_ids = [c.pk for c, _, _ in resp.context["top5"]]
        self.assertIn(existing.pk, candidate_ids)

        # Person / Contact は未作成（候補確認段階）
        self.assertEqual(Person.objects.count(), person_count_before)
        self.assertEqual(Contact.objects.count(), contact_count_before)

    # ---- ステップ3b：強制作成 ----

    def test_post_force_create_skips_duplicate_check_and_creates(self):
        """強制作成 POST → 重複候補があっても検出スキップ、Person + Contact 作成、詳細画面リダイレクト。"""
        # 既存 Contact（重複候補になる、full_name + email + mobile_phone 一致で possible_high 以上）
        # 既存側は ORM 直作成で正規化を経ないため、POST 側の正規化後 full_name（"既存太郎"）に揃える。
        existing_person = Person.objects.create()
        existing = Contact.objects.create(
            person=existing_person,
            status=Contact.Status.PRIMARY,
            full_name="既存太郎",
            email="taro@example.com",
            mobile_phone="090-1234-5678",
        )
        existing_person.primary_contact = existing
        existing_person.save(update_fields=["primary_contact", "updated_at"])

        person_count_before = Person.objects.count()
        contact_count_before = Contact.objects.count()

        # 同じ値 + force_create=1 で POST
        data = self._base_post_data()
        data["full_name"] = "既存 太郎"
        data["email"] = "taro@example.com"
        data["mobile_phone"] = "090-1234-5678"
        data["salutation_name"] = "既存 様"  # Phase D §3.5 で必須化
        data["force_create"] = "1"

        resp = self.client.post(self.url, data=data)

        # 強制作成により Person + Contact 各 1 件追加、Contact 詳細画面へリダイレクト
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Person.objects.count(), person_count_before + 1)
        self.assertEqual(Contact.objects.count(), contact_count_before + 1)

        # full_name は §3.4 正規化で空白除去される（"既存 太郎" → "既存太郎"）
        new_contact = (
            Contact.objects.filter(full_name="既存太郎")
            .exclude(pk=existing.pk)
            .first()
        )
        self.assertIsNotNone(new_contact)
        self.assertEqual(new_contact.status, Contact.Status.PRIMARY)
        self.assertNotEqual(new_contact.person_id, existing_person.pk)

        self.assertEqual(
            resp.url,
            reverse(
                "contacts:contact_detail", kwargs={"pk": new_contact.pk}
            ),
        )

    def test_post_force_create_with_invalid_form_redisplays(self):
        """force_create + 検証 NG → 通常通りフォーム再表示、Contact 未作成。"""
        data = self._base_post_data()
        data["full_name"] = "x" * 300  # max_length=255 違反
        data["force_create"] = "1"
        contact_count_before = Contact.objects.count()

        resp = self.client.post(self.url, data=data)

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "contacts/contact_create.html")
        self.assertEqual(Contact.objects.count(), contact_count_before)


class PreviewContactViewTests(TestCase):
    """PreviewContactView（14 番、仕様書 §11.3 / §11.4.4）の単体テスト。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="preview_test_user", password="dummy"
        )
        _grant_contact_perms(self.user)
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P-name",
            organization="P-co",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, contact=None):
        return reverse(
            "contacts:contact_preview",
            kwargs={"pk": (contact or self.contact).pk},
        )

    # ---- 正常系 ----

    def test_get_returns_200_with_fragment(self):
        """認証あり GET → 200、_preview_modal_body.html、contact が context に。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "contacts/_preview_modal_body.html")
        self.assertEqual(resp.context["contact"].pk, self.contact.pk)
        self.assertIn("field_confidences", resp.context)
        # body 内に氏名が表示される
        self.assertIn("P-name", resp.content.decode())

    def test_archived_person_inactive_contact_returns_200(self):
        """archived Person 配下の inactive Contact → 200（ガードなし、§3.5）。"""
        archived_person = Person.objects.create(status=Person.Status.ARCHIVED)
        Contact.objects.create(
            person=archived_person,
            status=Contact.Status.PRIMARY,
            full_name="dummy-primary",
        )
        inactive = Contact.objects.create(
            person=archived_person,
            status=Contact.Status.INACTIVE,
            full_name="archived-inactive",
        )
        resp = self.client.get(self._url(inactive))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("archived-inactive", resp.content.decode())

    def test_merged_person_contact_returns_200(self):
        """merged Person 配下の Contact → 200（ガードなし、§3.5）。"""
        self.person.status = Person.Status.MERGED
        self.person.save(update_fields=["status", "updated_at"])
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)

    # ---- 異常系 ----

    def test_unauthenticated_redirects(self):
        c = Client()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)

    def test_nonexistent_returns_404(self):
        import uuid as _uuid

        url = reverse(
            "contacts:contact_preview", kwargs={"pk": _uuid.uuid4()}
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


class ContactDetailDebugUidTests(TestCase):
    """DEBUG=True 時の UID コピペ表示テスト（D-4d-1 第 6 弾 §2-1）。

    Django テストランナーは settings.DEBUG=False を強制するため、本クラスは
    @override_settings(DEBUG=True) で覆い、django.template.context_processors.debug
    が REMOTE_ADDR=127.0.0.1（test client デフォルト）+ INTERNAL_IPS で debug=True を
    context 注入する経路を検証する。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="debug_uid_user", password="dummy")
        _grant_contact_perms(self.user)
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="dbg",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self):
        return reverse("contacts:contact_detail", kwargs={"pk": self.contact.pk})

    @override_settings(DEBUG=True)
    def test_contact_and_person_uid_shown_in_debug_mode(self):
        resp = self.client.get(self._url())
        self.assertContains(resp, 'class="app-debug-uid"')
        self.assertContains(resp, "Contact UID:")
        self.assertContains(resp, "Person UID:")
        self.assertContains(resp, str(self.contact.id))
        self.assertContains(resp, str(self.person.id))

    @override_settings(DEBUG=False)
    def test_uid_hidden_when_debug_false(self):
        resp = self.client.get(self._url())
        self.assertNotContains(resp, "app-debug-uid")
        self.assertNotContains(resp, "Contact UID:")


# ======================================================================
# Phase E：AJAX 経路の正規化通し / salutation / 例外 / セキュリティ（§9.1-9.6）
# ======================================================================


class ContactAjaxNormalizationTests(_ContactAjaxTestBase):
    """Phase E §9.1/§9.2/§9.4-9.6：AJAX 経路の正規化・salutation・例外・address 弾き。"""

    def _url(self):
        return reverse(
            "contacts:ajax_update_field", kwargs={"pk": self.contact_a.pk}
        )

    def _update(self, field_name, new_value):
        return self._post_json(
            self._url(), {"field_name": field_name, "new_value": new_value}
        )

    def _set_country(self, country):
        self.contact_a.country = country
        self.contact_a.save(update_fields=["country", "updated_at"])

    # ---- §9.1 正規化通し ----

    def test_ajax_normalize_full_name(self):
        resp = self._update("full_name", "山田　太郎")  # 全角スペース
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.full_name, "山田太郎")

    def test_ajax_normalize_organization(self):
        resp = self._update("organization", "㈱テスト")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.organization, "株式会社テスト")

    def test_ajax_normalize_phone(self):
        resp = self._update("mobile_phone", "０９０-1234-5678")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.mobile_phone, "09012345678")

    def test_ajax_normalize_email(self):
        resp = self._update("email", "  TARO@Example.COM ")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.email, "taro@example.com")

    def test_ajax_normalize_postal_code_jp(self):
        self._set_country("JP")
        resp = self._update("postal_code", "471-0001")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.postal_code, "4710001")

    def test_ajax_normalize_postal_code_intl(self):
        # GB の英数混在 postal が "11" に破壊されず保護される（country 別正規化が AJAX でも効く）
        self._set_country("GB")
        resp = self._update("postal_code", "SW1A 1AA")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.postal_code, "SW1A1AA")

    def test_ajax_normalize_rest_of_address_jp(self):
        self._set_country("JP")
        resp = self._update("rest_of_address", "1丁目2番地3号")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.rest_of_address, "1-2-3")

    def test_ajax_normalize_rest_of_address_intl(self):
        self._set_country("US")
        resp = self._update("rest_of_address", "123  Market   St")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.rest_of_address, "123 Market St")

    # ---- §9.2 salutation_name ----

    def test_ajax_salutation_empty_string_400(self):
        resp = self._update("salutation_name", "")
        self.assertEqual(resp.status_code, 400)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.salutation_name, "テスト 様")  # 不変

    def test_ajax_salutation_whitespace_only_400(self):
        resp = self._update("salutation_name", "   ")
        self.assertEqual(resp.status_code, 400)

    def test_ajax_salutation_sets_is_manual_true(self):
        # is_manual=False から AJAX 更新 → True になる
        self.contact_a.salutation_name_is_manual = False
        self.contact_a.save(
            update_fields=["salutation_name_is_manual", "updated_at"]
        )
        resp = self._update("salutation_name", "山田 会長")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertTrue(self.contact_a.salutation_name_is_manual)
        self.assertEqual(self.contact_a.salutation_name, "山田 会長")

    def test_ajax_last_name_change_after_manual_salutation_keeps_value(self):
        # salutation を AJAX 更新（is_manual=True 固定）後、last_name を変更しても salutation 維持
        self._update("salutation_name", "山田 会長")
        resp = self._update("last_name", "佐藤")
        self.assertEqual(resp.status_code, 200)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.salutation_name, "山田 会長")

    # ---- §9.4 address 弾き ----

    def test_ajax_address_field_400(self):
        resp = self._update("address", "anything")
        self.assertEqual(resp.status_code, 400)

    # ---- §9.5 ValidationError ハンドリング ----

    def test_ajax_normalize_validation_error_returns_400(self):
        # full_name を空白のみ → normalize_full_name が ValidationError → 400
        resp = self._update("full_name", "　 ")
        self.assertEqual(resp.status_code, 400)

    def test_ajax_validation_error_message_in_response(self):
        resp = self._update("salutation_name", "")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertIn("salutation_name", body["error"])

    # ---- §9.6 セキュリティ ----

    def test_ajax_address_injection_attempt(self):
        # address は UPDATABLE_FIELDS 外（Phase E で除外）→ 直接送信は弾かれる
        resp = self._update("address", "<script>alert(1)</script>")
        self.assertEqual(resp.status_code, 400)

    def test_ajax_salutation_is_manual_not_directly_writable(self):
        # is_manual フラグは UPDATABLE_FIELDS 外 → field_name 経由で書き換え不可
        resp = self._update("salutation_name_is_manual", True)
        self.assertEqual(resp.status_code, 400)


class ContactSaveAddressComposeTests(TestCase):
    """Phase E §9.3：Contact.save() が住所構成要素変更時に address を自動 compose する。"""

    def _make(self, **kwargs):
        person = Person.objects.create()
        defaults = dict(
            status=Contact.Status.PRIMARY,
            full_name="住所太郎",
            salutation_name="住所 様",
            salutation_name_is_manual=True,
        )
        defaults.update(kwargs)
        return Contact.objects.create(person=person, **defaults)

    def test_save_composes_address_when_postal_code_changes(self):
        c = self._make(
            country="JP", region="愛知県", city="豊田市", rest_of_address="1-2-3"
        )
        self.assertEqual(c.address, "愛知県豊田市1-2-3")  # 作成時に compose 済み
        c.postal_code = "4710001"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.address, "〒4710001 愛知県豊田市1-2-3")

    def test_save_composes_address_when_region_changes(self):
        c = self._make(
            country="JP", region="愛知県", city="豊田市", rest_of_address="1-2-3"
        )
        c.region = "東京都"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.address, "東京都豊田市1-2-3")

    def test_save_composes_address_when_city_changes(self):
        c = self._make(
            country="JP", region="愛知県", city="豊田市", rest_of_address="1-2-3"
        )
        c.city = "名古屋市"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.address, "愛知県名古屋市1-2-3")

    def test_save_composes_address_when_rest_changes(self):
        c = self._make(
            country="JP", region="愛知県", city="豊田市", rest_of_address="1-2-3"
        )
        c.rest_of_address = "9-9-9"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.address, "愛知県豊田市9-9-9")

    def test_save_composes_address_when_country_changes(self):
        c = self._make(
            country="JP",
            postal_code="94103",
            region="CA",
            city="San Francisco",
            rest_of_address="123 Market St",
        )
        self.assertEqual(c.address, "〒94103 CASan Francisco123 Market St")
        c.country = "US"
        c.save()
        c.refresh_from_db()
        self.assertEqual(
            c.address, "123 Market St, San Francisco, CA 94103, US"
        )

    def test_save_does_not_compose_when_no_source_changes(self):
        c = self._make(
            country="JP", region="愛知県", city="豊田市", rest_of_address="1-2-3"
        )
        # save() を経由せず address を書き換え、source 以外（notes）変更で save → 維持
        Contact.objects.filter(pk=c.pk).update(address="MANUAL")
        c2 = Contact.objects.get(pk=c.pk)
        c2.notes = "changed"
        c2.save()
        c2.refresh_from_db()
        self.assertEqual(c2.address, "MANUAL")

    def test_save_jp_address_format(self):
        c = self._make(
            country="JP",
            postal_code="4710001",
            region="愛知県",
            city="豊田市",
            rest_of_address="1-2-3",
        )
        self.assertEqual(c.address, "〒4710001 愛知県豊田市1-2-3")

    def test_save_us_address_format(self):
        c = self._make(
            country="US",
            postal_code="94103",
            region="CA",
            city="San Francisco",
            rest_of_address="123 Market St",
        )
        self.assertEqual(
            c.address, "123 Market St, San Francisco, CA 94103, US"
        )


class UpdateFieldAddressRejectionTests(TestCase):
    """Phase E §9.4：Contact.update_field("address", ...) は ValueError（UPDATABLE_FIELDS 外）。"""

    def test_update_field_address_rejected(self):
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            full_name="x",
            salutation_name="x 様",
            salutation_name_is_manual=True,
        )
        user = User.objects.create_user(username="rej_user", password="dummy")
        with self.assertRaises(ValueError):
            contact.update_field("address", "anything", user)


# ======================================================================
# Phase F1：ContactSns InlineFormSet（仕様書 §11.6.7）
# ======================================================================


class ContactSnsFormSetTests(TestCase):
    """ContactSnsFormSet（build_contact_sns_formset）の単体テスト（§11.6.7 / §1.8.1）。"""

    def setUp(self):
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="C",
        )

    def _data(self, rows, initial=0):
        data = _sns_management_form(len(rows), initial=initial)
        for i, row in enumerate(rows):
            for key, value in row.items():
                data[f"sns-{i}-{key}"] = value
        return data

    def test_empty_formset_creates_nothing(self):
        formset = build_contact_sns_formset(
            data=_empty_sns_management_form(), instance=self.contact, prefix="sns"
        )
        self.assertTrue(formset.is_valid())
        formset.save()
        self.assertEqual(self.contact.sns_accounts.count(), 0)

    def test_single_row_creates_record(self):
        data = self._data([{"sns_type": "twitter", "sns_id": "@taro"}])
        formset = build_contact_sns_formset(
            data=data, instance=self.contact, prefix="sns"
        )
        self.assertTrue(formset.is_valid())
        formset.save()
        self.assertEqual(self.contact.sns_accounts.count(), 1)
        sns = self.contact.sns_accounts.first()
        self.assertEqual(sns.sns_type, "twitter")
        self.assertEqual(sns.sns_id, "@taro")

    def test_duplicate_rows_invalid(self):
        data = self._data(
            [
                {"sns_type": "github", "sns_id": "taro"},
                {"sns_type": "github", "sns_id": "taro"},
            ]
        )
        formset = build_contact_sns_formset(
            data=data, instance=self.contact, prefix="sns"
        )
        self.assertFalse(formset.is_valid())
        msg = str(formset.non_form_errors())
        # 日本語メッセージで出る（Phase F1 follow-up 不具合③）
        self.assertIn("同じ種別・同じ ID の SNS が重複しています。", msg)
        # Django 標準の英語フィールド名混じりメッセージが出ない
        self.assertNotIn("sns_type", msg)
        self.assertNotIn("sns_id", msg)

    def test_delete_existing_removes_record(self):
        sns = ContactSns.objects.create(
            contact=self.contact, sns_type="line", sns_id="line-id"
        )
        data = self._data(
            [
                {
                    "id": str(sns.id),
                    "sns_type": "line",
                    "sns_id": "line-id",
                    "DELETE": "on",
                }
            ],
            initial=1,
        )
        formset = build_contact_sns_formset(
            data=data, instance=self.contact, prefix="sns"
        )
        self.assertTrue(formset.is_valid())
        formset.save()
        self.assertEqual(self.contact.sns_accounts.count(), 0)

    def test_partial_row_invalid(self):
        """sns_type だけ・sns_id 空 → 標準バリデーションエラー（片方だけ入力禁止）。"""
        data = self._data([{"sns_type": "twitter", "sns_id": ""}])
        formset = build_contact_sns_formset(
            data=data, instance=self.contact, prefix="sns"
        )
        self.assertFalse(formset.is_valid())

    def test_invalid_sns_type_choice(self):
        """choices 外の sns_type → ChoiceField バリデーションエラー。"""
        data = self._data([{"sns_type": "myspace", "sns_id": "x"}])
        formset = build_contact_sns_formset(
            data=data, instance=self.contact, prefix="sns"
        )
        self.assertFalse(formset.is_valid())

    def test_sns_id_is_stripped(self):
        data = self._data([{"sns_type": "blog", "sns_id": "  https://b.example  "}])
        formset = build_contact_sns_formset(
            data=data, instance=self.contact, prefix="sns"
        )
        self.assertTrue(formset.is_valid())
        formset.save()
        self.assertEqual(
            self.contact.sns_accounts.first().sns_id, "https://b.example"
        )

    def test_get_formset_shows_existing_rows(self):
        ContactSns.objects.create(
            contact=self.contact, sns_type="twitter", sns_id="@a"
        )
        ContactSns.objects.create(
            contact=self.contact, sns_type="github", sns_id="a"
        )
        formset = build_contact_sns_formset(instance=self.contact, prefix="sns")
        self.assertEqual(formset.initial_form_count(), 2)

    def test_initial_prefills_extra_rows(self):
        """initial を渡すと新規 Contact 用に extra 行で初期表示される（9 番別肩書用）。"""
        initial = [
            {"sns_type": "twitter", "sns_id": "@a"},
            {"sns_type": "line", "sns_id": "l"},
        ]
        formset = build_contact_sns_formset(
            instance=None, initial=initial, prefix="sns"
        )
        self.assertEqual(formset.total_form_count(), 2)
        self.assertEqual(formset.forms[0].initial["sns_type"], "twitter")


class UpdatePrimaryContactSnsTests(TestCase):
    """12 番 UpdatePrimaryContactView の ContactSns 連携（§11.6.7 / §1.8.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="up_sns_user", password="x")
        _grant_contact_perms(self.user)
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P",
            salutation_name="P 様",
            salutation_name_is_manual=True,
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self):
        return reverse(
            "contacts:contact_update_primary", kwargs={"pk": self.primary.pk}
        )

    def _base(self, change_reason="fix"):
        data = {f: getattr(self.primary, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["change_reason"] = change_reason
        data["note"] = ""
        return data

    def test_get_renders_sns_formset(self):
        ContactSns.objects.create(
            contact=self.primary, sns_type="twitter", sns_id="@p"
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("sns_formset", resp.context)
        self.assertContains(resp, "js-sns-formset-container")
        self.assertContains(resp, "@p")

    def test_fix_adds_sns(self):
        data = self._base("fix")
        data.update(_sns_management_form(1, initial=0))
        data["sns-0-sns_type"] = "github"
        data["sns-0-sns_id"] = "p-gh"
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.primary.sns_accounts.count(), 1)
        self.assertEqual(self.primary.sns_accounts.first().sns_type, "github")

    def test_fix_deletes_sns(self):
        sns = ContactSns.objects.create(
            contact=self.primary, sns_type="line", sns_id="l"
        )
        data = self._base("fix")
        data.update(_sns_management_form(1, initial=1))
        data["sns-0-id"] = str(sns.id)
        data["sns-0-sns_type"] = "line"
        data["sns-0-sns_id"] = "l"
        data["sns-0-DELETE"] = "on"
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.primary.sns_accounts.count(), 0)

    def test_transfer_copies_sns_to_new_contact(self):
        """transfer 系：旧 primary の SNS は維持され、新 primary に submit 内容が作られる。"""
        sns = ContactSns.objects.create(
            contact=self.primary, sns_type="twitter", sns_id="@old"
        )
        data = self._base("transfer")
        # ブラウザ実機と同じく、GET で旧 primary の既存行（pk 付き）が描画され、それを
        # そのまま submit する。既存 1 行（id 付き・INITIAL=1）+ 追加 1 行（id なし）。
        data.update(_sns_management_form(2, initial=1))
        data["sns-0-id"] = str(sns.id)
        data["sns-0-sns_type"] = "twitter"
        data["sns-0-sns_id"] = "@old"
        data["sns-1-sns_type"] = "line"
        data["sns-1-sns_id"] = "new-line"
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 302)
        self.person.refresh_from_db()
        new_primary = self.person.primary_contact
        self.assertNotEqual(new_primary.pk, self.primary.pk)
        self.assertEqual(new_primary.sns_accounts.count(), 2)
        # 旧 primary（inactive 化）の SNS は時点スナップショットとして残る
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.sns_accounts.count(), 1)


class UpdateActiveContactSnsTests(TestCase):
    """13 番 UpdateActiveContactView の ContactSns 連携（§11.6.7 / §1.8.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="ua_sns_user", password="x")
        _grant_contact_perms(self.user)
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person, status=Contact.Status.PRIMARY, full_name="P"
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.active = Contact.objects.create(
            person=self.person,
            status=Contact.Status.ACTIVE,
            full_name="A",
            salutation_name="A 様",
            salutation_name_is_manual=True,
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self):
        return reverse(
            "contacts:contact_update_active", kwargs={"pk": self.active.pk}
        )

    def test_fix_adds_sns(self):
        data = {f: getattr(self.active, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        data.update(_sns_management_form(1, initial=0))
        data["sns-0-sns_type"] = "instagram"
        data["sns-0-sns_id"] = "a-ig"
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.active.sns_accounts.count(), 1)
        self.assertEqual(self.active.sns_accounts.first().sns_type, "instagram")


class ContactCreateSnsTests(TestCase):
    """10 番 ContactCreateView の ContactSns 連携（§11.6.7 / §1.8.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="cc_sns_user", password="x")
        _grant_contact_perms(self.user)
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("contacts:contact_create")

    def test_create_with_sns(self):
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "新規太郎"
        data["salutation_name"] = "新規太郎 様"
        data.update(_sns_management_form(1, initial=0))
        data["sns-0-sns_type"] = "youtube"
        data["sns-0-sns_id"] = "ch-1"
        resp = self.client.post(self.url, data=data)
        self.assertEqual(resp.status_code, 302)
        contact = Contact.objects.get(full_name="新規太郎")
        self.assertEqual(contact.sns_accounts.count(), 1)
        self.assertEqual(contact.sns_accounts.first().sns_type, "youtube")

    def test_get_renders_empty_sns_formset(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("sns_formset", resp.context)
        self.assertContains(resp, "js-sns-formset-container")


class ContactSnsTemplateRenderTests(TestCase):
    """ContactSns formset コンテナの描画確認（§1.8.3）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="render_sns_user", password="x")
        _grant_contact_perms(self.user)
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person, status=Contact.Status.PRIMARY, full_name="P"
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.active = Contact.objects.create(
            person=self.person, status=Contact.Status.ACTIVE, full_name="A"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_primary_template_has_sns_container(self):
        url = reverse(
            "contacts:contact_update_primary", kwargs={"pk": self.primary.pk}
        )
        resp = self.client.get(url)
        self.assertContains(resp, "js-sns-formset-container")
        self.assertContains(resp, "js-sns-add-btn")

    def test_active_template_has_sns_container(self):
        url = reverse(
            "contacts:contact_update_active", kwargs={"pk": self.active.pk}
        )
        resp = self.client.get(url)
        self.assertContains(resp, "js-sns-formset-container")

    def test_create_template_has_sns_container(self):
        resp = self.client.get(reverse("contacts:contact_create"))
        self.assertContains(resp, "js-sns-formset-container")


class CommentLeakRegressionTests(TestCase):
    """Phase F1 follow-up 不具合②：複数行 {# #} コメント本文の画面露出が無いこと。"""

    def setUp(self):
        self.user = User.objects.create_user(username="leak_user", password="x")
        _grant_contact_perms(self.user)
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="L",
            organization="L-co",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.client = Client()
        self.client.force_login(self.user)

    def test_contact_detail_no_leaked_comment(self):
        """11 番 Contact 詳細画面でコメント本文が漏れない。"""
        url = reverse("contacts:contact_detail", kwargs={"pk": self.contact.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "SNS グループは v1.6.1 で")
        self.assertNotContains(resp, "別途実装する")

    def test_preview_no_leaked_comment(self):
        """14 番プレビュー（_preview_modal_body.html）でコメント本文が漏れない。"""
        url = reverse("contacts:contact_preview", kwargs={"pk": self.contact.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "SNS グループは v1.6.1 で")
        self.assertNotContains(resp, "別途実装する")


class SnsFormsetHiddenRowCssTests(TestCase):
    """Phase F1 follow-up 不具合①：既存行 [hidden] を隠す CSS ルールが app.css にあること。

    CSS の実表示はクローム君の視覚確認に委ねるが、ルールの存在だけは回帰防止で固定する。
    """

    def test_hidden_row_css_rule_present(self):
        from pathlib import Path

        from django.conf import settings

        css = Path(settings.BASE_DIR, "static", "css", "app.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".app-sns-formset__row[hidden]", css)
        self.assertIn("display: none !important", css)


class Phase7ContactsViewAuthTests(TestCase):
    """Phase 7 段3-2：contacts 6 View の Django 標準 CRUD 権限ガード（rev20 No.10-14/23 ★2）。

    view_contact / add_contact / change_contact の各粒度で「権限なし→403・権限あり→正常」を
    検証する。Update 系フォーム経路は宿題F（v1.7+）で owner ガード（can_edit_contact）が
    追加されたため、change_contact だけでは不十分になった。本クラスの update 系テストは
    所有者なし Contact を横断権限 edit_all_contacts 保持者が編集できることで「権限あり→正常」を
    検証する（owner ガード自体の網羅は ContactFormOwnerGuardTests）。
    """

    def setUp(self):
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person, status=Contact.Status.PRIMARY, full_name="主名義"
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.active = Contact.objects.create(
            person=self.person, status=Contact.Status.ACTIVE, full_name="役名義"
        )

    def _user_with(self, *codenames):
        import uuid as _uuid

        from django.contrib.auth.models import Permission

        u = User.objects.create_user(
            username=f"ct_auth_{_uuid.uuid4().hex[:8]}", password="x"
        )
        for cn in codenames:
            u.user_permissions.add(
                Permission.objects.get(
                    codename=cn, content_type__app_label="contacts"
                )
            )
        return u

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_list_requires_view_contact(self):
        url = reverse("contacts:contact_list")
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(
            self._client(self._user_with("view_contact")).get(url).status_code, 200
        )

    def test_detail_requires_view_contact(self):
        url = reverse("contacts:contact_detail", kwargs={"pk": self.primary.pk})
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(
            self._client(self._user_with("view_contact")).get(url).status_code, 200
        )

    def test_preview_requires_view_contact(self):
        url = reverse("contacts:contact_preview", kwargs={"pk": self.primary.pk})
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(
            self._client(self._user_with("view_contact")).get(url).status_code, 200
        )

    def test_create_requires_add_contact(self):
        url = reverse("contacts:contact_create")
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(
            self._client(self._user_with("add_contact")).get(url).status_code, 200
        )

    def test_update_primary_requires_change_contact(self):
        url = reverse(
            "contacts:contact_update_primary", kwargs={"pk": self.primary.pk}
        )
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        # 宿題F：所有者なし Contact のため change_contact 単独では owner ガードで 403。
        # 横断権限を併せ持てば正常（権限ガード自体が効いていることの確認）。
        self.assertEqual(
            self._client(self._user_with("change_contact")).get(url).status_code, 403
        )
        self.assertEqual(
            self._client(
                self._user_with("change_contact", "edit_all_contacts")
            ).get(url).status_code,
            200,
        )

    def test_update_active_requires_change_contact(self):
        url = reverse(
            "contacts:contact_update_active", kwargs={"pk": self.active.pk}
        )
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        # 宿題F：所有者なし Contact のため change_contact 単独では owner ガードで 403。
        self.assertEqual(
            self._client(self._user_with("change_contact")).get(url).status_code, 403
        )
        self.assertEqual(
            self._client(
                self._user_with("change_contact", "edit_all_contacts")
            ).get(url).status_code,
            200,
        )


class ContactFormOwnerGuardTests(TestCase):
    """宿題F：UpdatePrimary/UpdateActiveContactView の owner ガード結合テスト。

    AJAX 経路（ContactAjaxOwnerGuardTests）と同じ can_edit_contact 判定をフォーム経路にも
    効かせたことを検証する。全ユーザーに change_contact を付与して PermissionRequiredMixin を
    通過させ、owner ガード単独の効きを切り分ける。created_by 本人 / managed_by 本人 /
    edit_all_contacts 保持者は GET/POST でき、いずれでもない他人は GET/POST とも 403
    （PermissionDenied）になることを確認する。
    """

    def setUp(self):
        from django.contrib.auth.models import Permission

        change_perm = Permission.objects.get(
            codename="change_contact", content_type__app_label="contacts"
        )
        edit_all_perm = Permission.objects.get(
            codename="edit_all_contacts", content_type__app_label="contacts"
        )

        self.owner = User.objects.create_user(username="fog_owner", password="x")
        self.manager = User.objects.create_user(username="fog_manager", password="x")
        self.privileged = User.objects.create_user(username="fog_priv", password="x")
        self.stranger = User.objects.create_user(username="fog_stranger", password="x")
        # 全員に change_contact を付与（PermissionRequiredMixin は通過させ、403 が
        # owner ガード由来であることを保証する）。
        for u in (self.owner, self.manager, self.privileged, self.stranger):
            u.user_permissions.add(change_perm)
        self.privileged.user_permissions.add(edit_all_perm)
        # has_perm のキャッシュを避けるため取り直す
        self.privileged = User.objects.get(pk=self.privileged.pk)

        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="FOG-primary",
            organization="FOG-co",
            created_by=self.owner,
            managed_by=self.manager,
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.active = Contact.objects.create(
            person=self.person,
            status=Contact.Status.ACTIVE,
            full_name="FOG-active",
            organization="FOG-active-co",
            created_by=self.owner,
            managed_by=self.manager,
        )

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def _primary_url(self):
        return reverse(
            "contacts:contact_update_primary", kwargs={"pk": self.primary.pk}
        )

    def _active_url(self):
        return reverse(
            "contacts:contact_update_active", kwargs={"pk": self.active.pk}
        )

    def _primary_post_data(self):
        data = {f: getattr(self.primary, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["change_reason"] = "fix"
        data["note"] = ""
        data.update(_empty_sns_management_form())
        return data

    def _active_post_data(self):
        data = {f: getattr(self.active, f) or "" for f in Contact.UPDATABLE_FIELDS}
        data["note"] = ""
        data.update(_empty_sns_management_form())
        return data

    # ---- 他人（change_contact のみ・非所有・非横断）は GET / POST とも 403 ----

    def test_stranger_get_primary_forbidden(self):
        resp = self._client(self.stranger).get(self._primary_url())
        self.assertEqual(resp.status_code, 403)

    def test_stranger_post_primary_forbidden(self):
        resp = self._client(self.stranger).post(
            self._primary_url(), data=self._primary_post_data()
        )
        self.assertEqual(resp.status_code, 403)

    def test_stranger_get_active_forbidden(self):
        resp = self._client(self.stranger).get(self._active_url())
        self.assertEqual(resp.status_code, 403)

    def test_stranger_post_active_forbidden(self):
        resp = self._client(self.stranger).post(
            self._active_url(), data=self._active_post_data()
        )
        self.assertEqual(resp.status_code, 403)

    # ---- created_by 本人 / managed_by 本人 / 横断権限保持者は編集できる ----
    # GET 200 は owner ガードを通過し編集画面に到達したことを示す。owner ガードは
    # get_object にあり GET/POST 共通経路のため、GET 通過は POST 通過も担保する。

    def test_owner_get_primary_ok(self):
        resp = self._client(self.owner).get(self._primary_url())
        self.assertEqual(resp.status_code, 200)

    def test_manager_get_primary_ok(self):
        resp = self._client(self.manager).get(self._primary_url())
        self.assertEqual(resp.status_code, 200)

    def test_privileged_get_primary_ok(self):
        resp = self._client(self.privileged).get(self._primary_url())
        self.assertEqual(resp.status_code, 200)

    def test_owner_get_active_ok(self):
        resp = self._client(self.owner).get(self._active_url())
        self.assertEqual(resp.status_code, 200)

    def test_manager_get_active_ok(self):
        resp = self._client(self.manager).get(self._active_url())
        self.assertEqual(resp.status_code, 200)

    def test_privileged_get_active_ok(self):
        resp = self._client(self.privileged).get(self._active_url())
        self.assertEqual(resp.status_code, 200)

    # POST も owner ガードを通過し、実際に編集（リダイレクト）まで到達することを実証。

    def test_owner_post_primary_edits(self):
        resp = self._client(self.owner).post(
            self._primary_url(), data=self._primary_post_data()
        )
        self.assertEqual(resp.status_code, 302)

    def test_owner_post_active_edits(self):
        resp = self._client(self.owner).post(
            self._active_url(), data=self._active_post_data()
        )
        self.assertEqual(resp.status_code, 302)

