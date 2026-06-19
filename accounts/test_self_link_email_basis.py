"""本人紐付け（self-link）の email 照合基準が「代表名刺 primary_contact のメール一致のみ」で
入口・出口とも統一されていることの検証。

入口＝ホーム抽出（accounts.services.self_link_candidate_contacts／HomeView）、
出口＝確認画面ガード（accounts.services.is_self_link_email_match）＋実行（link_user_to_person）。
本人同定の根拠は primary_contact（status=primary）のメール一致のみ（Lower(Trim) 正規化）。
副(active)Contact 一致は自動同定の根拠にしない（管理者の手動紐づけ operator≠user で救済）。
加えて出口は person.status=active 限定（非active への直叩き紐付けを弾く）。
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.constants import PersonLinkStatus
from accounts.services import (
    is_self_link_email_match,
    link_user_to_person,
    self_link_candidate_contacts,
)
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class SelfLinkEmailBasisTests(TestCase):
    def _make_person(self, contacts, primary_idx=None, status=Person.Status.ACTIVE):
        """contacts: [(Contact.Status, email), ...] の Person を作る。primary_idx で primary_contact を設定。"""
        person = Person.objects.create(status=status)
        objs = []
        for cstatus, email in contacts:
            objs.append(
                Contact.objects.create(
                    person=person, status=cstatus, email=email, full_name="氏名"
                )
            )
        if primary_idx is not None:
            person.primary_contact = objs[primary_idx]
            person.save(update_fields=["primary_contact", "updated_at"])
        return person, objs

    def _user(self, email):
        return User.objects.create_user(username="u_" + email.split("@")[0], password="d", email=email)

    def _person_ids_in_candidates(self, user):
        return {c.person_id for c in self_link_candidate_contacts(user)}

    # 1. primary_contact のメール一致 → 入口で候補化・出口で紐づけ可（本筋）
    def test_primary_contact_match_links(self):
        user = self._user("me@example.com")
        person, _ = self._make_person(
            [(Contact.Status.PRIMARY, "me@example.com")], primary_idx=0
        )
        self.assertIn(person.id, self._person_ids_in_candidates(user))  # 入口
        self.assertTrue(is_self_link_email_match(user, person))         # 出口ガード
        link_user_to_person(operator=user, user=user, person=person)    # 実行
        user.refresh_from_db()
        self.assertEqual(user.person_id, person.id)

    # 2. active(副) Contact だけが一致（primary は別メール）→ 新方針では紐づけ不可
    def test_active_only_match_excluded_and_rejected(self):
        user = self._user("me@example.com")
        person, _ = self._make_person(
            [(Contact.Status.PRIMARY, "primary@example.com"), (Contact.Status.ACTIVE, "me@example.com")],
            primary_idx=0,
        )
        self.assertNotIn(person.id, self._person_ids_in_candidates(user))  # 入口で出ない
        self.assertFalse(is_self_link_email_match(user, person))           # 出口で False
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=person)

    # 3. primary_contact が null かつ副 Contact 一致 → 新方針では紐づけ不可
    def test_primary_null_active_match_excluded_and_rejected(self):
        user = self._user("me@example.com")
        person, _ = self._make_person([(Contact.Status.ACTIVE, "me@example.com")], primary_idx=None)
        self.assertIsNone(person.primary_contact_id)
        self.assertNotIn(person.id, self._person_ids_in_candidates(user))
        self.assertFalse(is_self_link_email_match(user, person))
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=person)

    # 4. inactive Contact のみ一致 → 入口・出口とも拾わない／弾く
    def test_inactive_only_match_excluded_and_rejected(self):
        user = self._user("me@example.com")
        person, _ = self._make_person(
            [(Contact.Status.PRIMARY, "other@example.com"), (Contact.Status.INACTIVE, "me@example.com")],
            primary_idx=0,
        )
        self.assertNotIn(person.id, self._person_ids_in_candidates(user))  # 入口で出ない
        self.assertFalse(is_self_link_email_match(user, person))           # 出口で False
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=person)

    # 5. 大文字小文字・前後空白違いの primary メール → 入口・出口とも一致扱い（正規化維持）
    def test_case_and_whitespace_normalized_match(self):
        user = self._user("me@example.com")
        person, _ = self._make_person(
            [(Contact.Status.PRIMARY, "  ME@Example.COM  ")], primary_idx=0
        )
        self.assertIn(person.id, self._person_ids_in_candidates(user))
        self.assertTrue(is_self_link_email_match(user, person))
        # 逆向き（user 側に大文字・空白）でも一致する。
        user2 = self._user("spaced@example.com")
        user2.email = "  SPACED@Example.com "
        user2.save(update_fields=["email"])
        person2, _ = self._make_person([(Contact.Status.PRIMARY, "spaced@example.com")], primary_idx=0)
        self.assertIn(person2.id, self._person_ids_in_candidates(user2))
        self.assertTrue(is_self_link_email_match(user2, person2))

    # 6a. 既存ガード：自分が既に別 Person に紐づけ済み → 弾かれる
    def test_user_already_linked_rejected(self):
        user = self._user("me@example.com")
        other_person, _ = self._make_person([(Contact.Status.PRIMARY, "me@example.com")], primary_idx=0)
        user.person = other_person
        user.save(update_fields=["person"])
        target, _ = self._make_person([(Contact.Status.PRIMARY, "me@example.com")], primary_idx=0)
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=target)

    # 6b. 既存ガード：対象 Person が他 User に紐づき済み → 弾かれる・入口にも出ない
    def test_person_linked_to_other_user_rejected(self):
        owner = self._user("owner@example.com")
        person, _ = self._make_person([(Contact.Status.PRIMARY, "me@example.com")], primary_idx=0)
        owner.person = person
        owner.save(update_fields=["person"])
        user = self._user("me@example.com")
        # 入口（person__user__isnull=True）でも除外される。
        self.assertNotIn(person.id, self._person_ids_in_candidates(user))
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=person)

    # 入口の統合確認：primary_contact 一致の人物がホーム単一候補として出る。
    def test_home_alert_includes_primary_match(self):
        user = self._user("me@example.com")
        person, _ = self._make_person(
            [(Contact.Status.PRIMARY, "me@example.com")], primary_idx=0
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["person_link_status"], PersonLinkStatus.SINGLE_CANDIDATE)

    # 入口の統合確認（反転）：副一致のみの人物はホーム候補として出ない。
    def test_home_alert_excludes_active_only_match(self):
        user = self._user("me@example.com")
        self._make_person(
            [(Contact.Status.PRIMARY, "primary@example.com"), (Contact.Status.ACTIVE, "me@example.com")],
            primary_idx=0,
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context.get("person_link_status"))

    # ===== 出口 person.status=active ガード =====

    # 7. 非active（MERGED/ARCHIVED）Person に直叩き confirm GET → 「紐付ける」ボタン非表示。
    def test_inactive_person_confirm_get_hides_button(self):
        for status in (Person.Status.MERGED, Person.Status.ARCHIVED):
            with self.subTest(status=status):
                user = User.objects.create_user(
                    username="get_%s" % status, password="d", email="me@example.com"
                )
                person, _ = self._make_person(
                    [(Contact.Status.PRIMARY, "me@example.com")], primary_idx=0, status=status
                )
                self.client.force_login(user)
                url = reverse(
                    "accounts:link_user_person_confirm", kwargs={"person_id": person.id}
                )
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertNotContains(resp, "紐付ける")  # ボタン非表示（タイトルは「紐付け確認」で別語）
                self.assertContains(resp, "通常状態（active）ではないため")

    # 8. 非active Person への実行 → ValidationError で弾かれる。
    def test_inactive_person_link_rejected(self):
        for status in (Person.Status.MERGED, Person.Status.ARCHIVED):
            with self.subTest(status=status):
                user = User.objects.create_user(
                    username="post_%s" % status, password="d", email="me@example.com"
                )
                person, _ = self._make_person(
                    [(Contact.Status.PRIMARY, "me@example.com")], primary_idx=0, status=status
                )
                with self.assertRaises(ValidationError):
                    link_user_to_person(operator=user, user=user, person=person)

    # 9a. active Person の confirm GET → ボタン表示（リグレッションなし）。
    def test_active_person_confirm_get_shows_button(self):
        user = self._user("active@example.com")
        person, _ = self._make_person(
            [(Contact.Status.PRIMARY, "active@example.com")], primary_idx=0
        )
        self.client.force_login(user)
        url = reverse("accounts:link_user_person_confirm", kwargs={"person_id": person.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "紐付ける")

    # 9b. active Person への実行 → 従来どおり成立。
    def test_active_person_link_succeeds(self):
        user = self._user("ok@example.com")
        person, _ = self._make_person([(Contact.Status.PRIMARY, "ok@example.com")], primary_idx=0)
        link_user_to_person(operator=user, user=user, person=person)
        user.refresh_from_db()
        self.assertEqual(user.person_id, person.id)
