"""アクセス管理サイドバー（accounts/_sidebar.html）の検証（cards 二段ナビ作法に準拠）。

- 各アクセス管理画面でサイドバーが描画され、ユーザ/ロール/グループのリンクが出る。
- active_menu が画面ごとに正しい（role 系は role_list、group 系は group_list、
  assign_role はユーザ起点なので user_list）。
- 権限出し分け（retire_user でユーザ項目、manage_role でロール/グループ項目）。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role

User = get_user_model()


def _grant(user, *codenames):
    perms = Permission.objects.filter(
        codename__in=codenames, content_type__app_label="accounts"
    )
    user.user_permissions.add(*perms)
    return User.objects.get(pk=user.pk)


class SidebarActiveMenuTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role_sales = Role.objects.get(code="sales")

    def setUp(self):
        self.client = Client()
        self.op = _grant(
            User.objects.create_user("side_op", password="x"),
            "retire_user", "manage_role", "assign_role_to_user",
        )
        self.client.force_login(self.op)
        self.user_list = reverse("accounts:user_list")
        self.role_list = reverse("accounts:role_list")
        self.group_list = reverse("accounts:group_list")
        self.permission_list = reverse("accounts:permission_list")

    def _assert_sidebar_links(self, resp):
        # 4項目すべてのリンクがサイドバーに出る（全権限保持時）
        self.assertContains(resp, self.user_list)
        self.assertContains(resp, self.role_list)
        self.assertContains(resp, self.group_list)
        self.assertContains(resp, self.permission_list)

    def test_user_list_active(self):
        resp = self.client.get(self.user_list)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["active_menu"], "accounts:user_list")
        self._assert_sidebar_links(resp)
        self.assertContains(resp, f'href="{self.user_list}" class="is-active"')

    def test_role_list_active(self):
        resp = self.client.get(self.role_list)
        self.assertEqual(resp.context["active_menu"], "accounts:role_list")
        self.assertContains(resp, f'href="{self.role_list}" class="is-active"')

    def test_role_create_active_role(self):
        resp = self.client.get(reverse("accounts:role_create"))
        self.assertEqual(resp.context["active_menu"], "accounts:role_list")

    def test_role_update_active_role(self):
        resp = self.client.get(
            reverse("accounts:role_update", kwargs={"pk": self.role_sales.pk})
        )
        self.assertEqual(resp.context["active_menu"], "accounts:role_list")

    def test_role_delete_active_role(self):
        resp = self.client.get(
            reverse("accounts:role_delete", kwargs={"pk": self.role_sales.pk})
        )
        self.assertEqual(resp.context["active_menu"], "accounts:role_list")

    def test_group_list_active(self):
        resp = self.client.get(self.group_list)
        self.assertEqual(resp.context["active_menu"], "accounts:group_list")
        self.assertContains(resp, f'href="{self.group_list}" class="is-active"')

    def test_group_detail_active_group(self):
        from django.contrib.auth.models import Group

        g = Group.objects.first()
        resp = self.client.get(reverse("accounts:group_detail", kwargs={"pk": g.pk}))
        self.assertEqual(resp.context["active_menu"], "accounts:group_list")

    def test_permission_list_active(self):
        resp = self.client.get(self.permission_list)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["active_menu"], "accounts:permission_list")
        self._assert_sidebar_links(resp)
        self.assertContains(resp, f'href="{self.permission_list}" class="is-active"')

    def test_assign_role_active_user(self):
        target = User.objects.create_user("side_target", password="x")
        resp = self.client.get(
            reverse("accounts:assign_role", kwargs={"user_id": target.pk})
        )
        self.assertEqual(resp.context["active_menu"], "accounts:user_list")

    def test_user_detail_active_user(self):
        target = User.objects.create_user("side_detail", password="x")
        resp = self.client.get(
            reverse("accounts:user_detail", kwargs={"user_id": target.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["active_menu"], "accounts:user_list")
        self._assert_sidebar_links(resp)
        self.assertContains(resp, f'href="{self.user_list}" class="is-active"')

    def test_retire_user_active_user(self):
        target = User.objects.create_user("side_retire", password="x")
        resp = self.client.get(
            reverse("accounts:retire_user", kwargs={"user_id": target.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["active_menu"], "accounts:user_list")
        self._assert_sidebar_links(resp)
        self.assertContains(resp, f'href="{self.user_list}" class="is-active"')


class SidebarPermissionGateTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_retire_user_only_sees_user_item_not_role_group(self):
        op = _grant(User.objects.create_user("ru_only", password="x"), "retire_user")
        self.client.force_login(op)
        resp = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("accounts:user_list"))
        self.assertNotContains(resp, reverse("accounts:role_list"))
        self.assertNotContains(resp, reverse("accounts:group_list"))

    def test_manage_role_only_sees_role_group_not_user(self):
        op = _grant(User.objects.create_user("mr_only", password="x"), "manage_role")
        self.client.force_login(op)
        # manage_role だけでは user_list に入れない（retire_user 必須）→ role_list で確認
        resp = self.client.get(reverse("accounts:role_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("accounts:role_list"))
        self.assertContains(resp, reverse("accounts:group_list"))
        self.assertNotContains(resp, reverse("accounts:user_list"))
