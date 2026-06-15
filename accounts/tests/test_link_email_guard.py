"""本人フロー User⇄Person 紐付けの メール一致ガード（宿題F・新規要件）。

仕様：本人フロー（operator == user == request.user）の紐付けは、User.email と
Person.primary_contact.email を strip + 小文字化して完全一致しないと不可。Person 側メール空
（primary_contact 無し / email 空）は不一致扱い。他人紐付け経路（operator != user）は対象外。
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.services import is_self_link_email_match, link_user_to_person
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


def _make_person(email):
    """primary_contact（email 付き）を持つ Person を作る。email="" なら primary_contact 自体は
    作るが email は空。"""
    person = Person.objects.create()
    contact = Contact.objects.create(
        person=person,
        status=Contact.Status.PRIMARY,
        full_name="対象太郎",
        email=email,
    )
    person.primary_contact = contact
    person.save(update_fields=["primary_contact", "updated_at"])
    return person


class SelfLinkEmailGuardServiceTests(TestCase):
    """Service 層 link_user_to_person の本人フロー メール一致ガード。"""

    def test_self_flow_match_links(self):
        user = User.objects.create_user("self_match", password="x", email="a@example.com")
        person = _make_person("a@example.com")
        link_user_to_person(operator=user, user=user, person=person)
        user.refresh_from_db()
        self.assertEqual(user.person_id, person.pk)

    def test_self_flow_mismatch_raises(self):
        user = User.objects.create_user("self_mis", password="x", email="a@example.com")
        person = _make_person("b@example.com")
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=person)
        user.refresh_from_db()
        self.assertIsNone(user.person_id)

    def test_self_flow_person_email_empty_raises_no_primary(self):
        """primary_contact 無し（メール空）→ 不一致扱いで紐付け不可。"""
        user = User.objects.create_user("self_np", password="x", email="a@example.com")
        person = Person.objects.create()
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=person)
        user.refresh_from_db()
        self.assertIsNone(user.person_id)

    def test_self_flow_person_email_blank_raises(self):
        """primary_contact はあるが email 空文字 → 不一致扱いで紐付け不可。"""
        user = User.objects.create_user("self_blank", password="x", email="a@example.com")
        person = _make_person("")
        with self.assertRaises(ValidationError):
            link_user_to_person(operator=user, user=user, person=person)
        user.refresh_from_db()
        self.assertIsNone(user.person_id)

    def test_self_flow_case_and_whitespace_only_diff_matches(self):
        """大文字小文字・前後空白だけの違いは正規化で一致扱い → 紐付け成功。"""
        user = User.objects.create_user(
            "self_norm", password="x", email="USER@Example.COM"
        )
        person = _make_person("  user@example.com  ")
        link_user_to_person(operator=user, user=user, person=person)
        user.refresh_from_db()
        self.assertEqual(user.person_id, person.pk)

    def test_other_flow_mismatch_links_unguarded(self):
        """他人紐付け（operator != user）は メール不一致でも従来どおり成功（ガード対象外）。"""
        operator = User.objects.create_user(
            "op_admin", password="x", email="admin@example.com"
        )
        target = User.objects.create_user("tgt", password="x", email="t@example.com")
        person = _make_person("different@example.com")  # target と不一致
        link_user_to_person(operator=operator, user=target, person=person)
        target.refresh_from_db()
        self.assertEqual(target.person_id, person.pk)


class IsSelfLinkEmailMatchTests(TestCase):
    """純関数 is_self_link_email_match の判定。"""

    def test_match(self):
        user = User.objects.create_user("m1", password="x", email="a@example.com")
        self.assertTrue(is_self_link_email_match(user, _make_person("a@example.com")))

    def test_mismatch(self):
        user = User.objects.create_user("m2", password="x", email="a@example.com")
        self.assertFalse(is_self_link_email_match(user, _make_person("b@example.com")))

    def test_no_primary_contact_is_mismatch(self):
        user = User.objects.create_user("m3", password="x", email="a@example.com")
        self.assertFalse(is_self_link_email_match(user, Person.objects.create()))


class LinkConfirmViewEmailGuardTests(TestCase):
    """確認画面（LinkUserPersonConfirmView）の link ボタン出し分け（本人フロー）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            "confirm_user", password="x", email="me@example.com"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, person):
        return reverse(
            "accounts:link_user_person_confirm", kwargs={"person_id": person.id}
        )

    def test_match_shows_link_button(self):
        person = _make_person("me@example.com")
        resp = self.client.get(self._url(person))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn('value="link"', body)
        self.assertIn("紐付ける", body)
        self.assertNotIn("メールアドレスが一致しないため紐づけできません。", body)

    def test_mismatch_hides_button_shows_message(self):
        person = _make_person("other@example.com")
        resp = self.client.get(self._url(person))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("メールアドレスが一致しないため紐づけできません。", body)
        self.assertNotIn('value="link"', body)

    def test_mismatch_direct_post_blocked_by_service(self):
        """直 URL で link を POST しても Service が弾く（user.person は None のまま）。"""
        person = _make_person("other@example.com")
        resp = self.client.post(self._url(person), {"action": "link"})
        self.assertEqual(resp.status_code, 302)  # 確認画面へ redirect
        self.user.refresh_from_db()
        self.assertIsNone(self.user.person_id)
