"""UserListView の検索＋一覧からのロール割り当て導線の検証（宿題F・(A)）。

- キーワード検索（username / 氏名 / メール / Person 名）で一覧が絞り込まれる。
- 一覧の各行のロール変更導線は、assign_role_to_user 保持かつ本人以外のときだけ出る。
- 非保持者には導線が出ない（一覧表示自体は retire_user で可能）。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.services import apply_role
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


def _grant(user, *codenames):
    perms = Permission.objects.filter(
        codename__in=codenames, content_type__app_label="accounts"
    )
    user.user_permissions.add(*perms)
    # has_perm キャッシュ回避のため取り直す
    return User.objects.get(pk=user.pk)


class UserListSearchTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user("op_admin", password="x")
        self.operator = _grant(self.operator, "retire_user", "assign_role_to_user")
        self.client = Client()
        self.client.force_login(self.operator)
        self.url = reverse("accounts:user_list")

        self.alice = User.objects.create_user(
            "alice", password="x", first_name="Alice", last_name="Smith",
            email="alice@example.com",
        )
        self.bob = User.objects.create_user(
            "bob", password="x", first_name="Bob", last_name="Jones",
            email="bob@example.com",
        )

    def _ids(self, resp):
        return [u.id for u in resp.context["users"]]

    def test_no_query_lists_all_active(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        ids = self._ids(resp)
        self.assertIn(self.alice.id, ids)
        self.assertIn(self.bob.id, ids)

    def test_search_by_username(self):
        resp = self.client.get(self.url, {"q": "alice"})
        ids = self._ids(resp)
        self.assertIn(self.alice.id, ids)
        self.assertNotIn(self.bob.id, ids)

    def test_search_by_full_name(self):
        resp = self.client.get(self.url, {"q": "Jones"})
        ids = self._ids(resp)
        self.assertIn(self.bob.id, ids)
        self.assertNotIn(self.alice.id, ids)

    def test_search_by_email(self):
        resp = self.client.get(self.url, {"q": "bob@example"})
        ids = self._ids(resp)
        self.assertIn(self.bob.id, ids)
        self.assertNotIn(self.alice.id, ids)

    def test_search_by_person_name(self):
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, full_name="Carol Nakamura"
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        self.alice.person = person
        self.alice.save(update_fields=["person"])

        resp = self.client.get(self.url, {"q": "Nakamura"})
        ids = self._ids(resp)
        self.assertIn(self.alice.id, ids)
        self.assertNotIn(self.bob.id, ids)


class UserListAssignLinkVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse("accounts:user_list")
        self.target = User.objects.create_user("target_u", password="x")

    def _assign_url(self, user):
        return reverse("accounts:assign_role", kwargs={"user_id": user.pk})

    def test_link_visible_for_holder_on_other_user(self):
        operator = User.objects.create_user("holder", password="x")
        operator = _grant(operator, "retire_user", "assign_role_to_user")
        self.client.force_login(operator)
        resp = self.client.get(self.url)
        self.assertContains(resp, self._assign_url(self.target))

    def test_link_hidden_for_self(self):
        operator = User.objects.create_user("holder2", password="x")
        operator = _grant(operator, "retire_user", "assign_role_to_user")
        self.client.force_login(operator)
        resp = self.client.get(self.url)
        # 自分自身の行には割り当て導線を出さない
        self.assertNotContains(resp, self._assign_url(operator))

    def test_link_hidden_without_assign_perm(self):
        # retire_user だけ持つ → 一覧は見えるが割り当て導線は出ない
        viewer = User.objects.create_user("list_viewer", password="x")
        viewer = _grant(viewer, "retire_user")
        self.client.force_login(viewer)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, self._assign_url(self.target))

    def test_link_hidden_for_admin_target_row(self):
        # 現ロール admin のユーザ行には割り当て導線を出さない（admin 両方向ガード）
        admin_user = User.objects.create_user("admin_target", password="x")
        apply_role(admin_user, Role.objects.get(code="admin"))
        operator = User.objects.create_user("holder3", password="x")
        operator = _grant(operator, "retire_user", "assign_role_to_user")
        self.client.force_login(operator)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, self._assign_url(admin_user))
        # 非 admin の対象には引き続き出る
        self.assertContains(resp, self._assign_url(self.target))


class UserDetailAssignButtonTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user("dholder", password="x")
        self.operator = _grant(self.operator, "retire_user", "assign_role_to_user")
        self.client.force_login(self.operator)

    def _detail_url(self, user):
        return reverse("accounts:user_detail", kwargs={"user_id": user.pk})

    def _assign_url(self, user):
        return reverse("accounts:assign_role", kwargs={"user_id": user.pk})

    def test_button_shown_for_non_admin(self):
        target = User.objects.create_user("d_target", password="x")
        resp = self.client.get(self._detail_url(target))
        self.assertContains(resp, self._assign_url(target))

    def test_button_hidden_for_admin_target(self):
        admin_user = User.objects.create_user("d_admin", password="x")
        apply_role(admin_user, Role.objects.get(code="admin"))
        resp = self.client.get(self._detail_url(admin_user))
        self.assertNotContains(resp, self._assign_url(admin_user))
