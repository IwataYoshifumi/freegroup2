"""権限グループ閲覧専用ビュー（アクセス管理UI (C-2)）の検証。

- 一覧：16グループが出る／admin 系の表示／紐づく Role 表示。
- 詳細：permission を app/モデル単位でグルーピング表示／フレームワーク app は非表示。
- 権限：manage_role 無しでは一覧/詳細に到達できない（403）。
- read-only：編集系の URL/POST 経路が存在しない。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

User = get_user_model()


def _grant_manage_role(user):
    perm = Permission.objects.get(
        codename="manage_role", content_type__app_label="accounts"
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


class GroupViewPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_list_forbidden_without_perm(self):
        u = User.objects.create_user("nope", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("accounts:group_list")).status_code, 403)

    def test_detail_forbidden_without_perm(self):
        u = User.objects.create_user("nope2", password="x")
        self.client.force_login(u)
        g = Group.objects.first()
        self.assertEqual(
            self.client.get(reverse("accounts:group_detail", kwargs={"pk": g.pk})).status_code,
            403,
        )


class GroupListTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.op = _grant_manage_role(User.objects.create_user("gop", password="x"))
        self.client.force_login(self.op)

    def test_lists_all_16_groups(self):
        resp = self.client.get(reverse("accounts:group_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["groups_info"]), 16)
        self.assertContains(resp, "person_admin")
        self.assertContains(resp, "card_viewer")

    def test_admin_group_flagged(self):
        resp = self.client.get(reverse("accounts:group_list"))
        info = {i["group"].name: i for i in resp.context["groups_info"]}
        self.assertTrue(info["user_admin"]["is_admin"])
        self.assertTrue(info["person_admin"]["is_admin"])
        self.assertFalse(info["person_viewer"]["is_admin"])
        self.assertFalse(info["card_editor"]["is_admin"])

    def test_roles_shown_for_group(self):
        # admin ロールは user_admin を default_groups に持つ
        resp = self.client.get(reverse("accounts:group_list"))
        info = {i["group"].name: i for i in resp.context["groups_info"]}
        role_names = {r.name for r in info["user_admin"]["roles"]}
        self.assertIn("管理者", role_names)


class GroupDetailTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.op = _grant_manage_role(User.objects.create_user("gop2", password="x"))
        self.client.force_login(self.op)

    def _detail(self, name):
        g = Group.objects.get(name=name)
        return self.client.get(reverse("accounts:group_detail", kwargs={"pk": g.pk}))

    def test_detail_shows_permissions_grouped(self):
        resp = self._detail("person_admin")
        self.assertEqual(resp.status_code, 200)
        sections = resp.context["perm_sections"]
        # person_admin は persons app の permission を持つ
        self.assertTrue(any(s.startswith("persons /") for s in sections))
        # 業務 permission（merge_person 等）の codename が出る
        self.assertContains(resp, "merge_person")

    def test_framework_apps_excluded(self):
        # auth/contenttypes 等の app セクションは出ない
        resp = self._detail("person_admin")
        sections = resp.context["perm_sections"]
        for s in sections:
            app = s.split(" / ")[0]
            self.assertNotIn(app, {"admin", "auth", "contenttypes", "sessions"})


class GroupReadOnlyTests(TestCase):
    def test_no_group_edit_routes_exist(self):
        # 編集系 URL 名は存在しない（read-only）
        for name in ("group_create", "group_update", "group_delete", "group_edit"):
            with self.assertRaises(NoReverseMatch):
                reverse(f"accounts:{name}")

    def test_detail_get_only(self):
        op = _grant_manage_role(User.objects.create_user("gop3", password="x"))
        c = Client()
        c.force_login(op)
        g = Group.objects.first()
        # POST は許可されない（View に post 無し → 405）
        resp = c.post(reverse("accounts:group_detail", kwargs={"pk": g.pk}))
        self.assertEqual(resp.status_code, 405)
