"""業務ロール CRUD（アクセス管理UI (B)）の検証。

- 一覧/作成/編集/削除（manage_role 権限必須）。
- 論点1：割り当て中（退職者含む）の Role は削除不可（View / Service 両方）。
- 論点2：admin ロール（code=='admin'）は read-only（編集/削除導線なし・直URL拒否・自動採番で詐称不可）。
- 論点3：default_groups 変更で在職＋退職の保持者双方へ即再適用・ActionLog 1件。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.services import (
    apply_role,
    create_role,
    delete_role,
    update_role,
)
from actionlogs.models import ActionLog

User = get_user_model()


def _grant_manage_role(user):
    perm = Permission.objects.get(
        codename="manage_role", content_type__app_label="accounts"
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


class RoleCrudBaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role_admin = Role.objects.get(code="admin")
        cls.role_sales = Role.objects.get(code="sales")
        cls.role_viewer = Role.objects.get(code="viewer")

    def setUp(self):
        self.operator = _grant_manage_role(
            User.objects.create_user("role_op", password="x")
        )
        self.client = Client()
        self.client.force_login(self.operator)


class RolePermissionTests(RoleCrudBaseTest):
    def test_list_forbidden_without_perm(self):
        stranger = User.objects.create_user("nope", password="x")
        c = Client()
        c.force_login(stranger)
        self.assertEqual(c.get(reverse("accounts:role_list")).status_code, 403)

    def test_list_ok_with_perm(self):
        resp = self.client.get(reverse("accounts:role_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.role_sales.name)


class RoleListReadOnlyTests(RoleCrudBaseTest):
    def test_admin_row_has_no_edit_delete(self):
        resp = self.client.get(reverse("accounts:role_list"))
        admin_edit = reverse("accounts:role_update", kwargs={"pk": self.role_admin.pk})
        admin_delete = reverse("accounts:role_delete", kwargs={"pk": self.role_admin.pk})
        sales_edit = reverse("accounts:role_update", kwargs={"pk": self.role_sales.pk})
        self.assertNotContains(resp, admin_edit)
        self.assertNotContains(resp, admin_delete)
        self.assertContains(resp, sales_edit)


class RoleCreateTests(RoleCrudBaseTest):
    def test_create_english_name_slug_code(self):
        resp = self.client.post(
            reverse("accounts:role_create"), {"name": "Field Staff", "sort_order": 5}
        )
        self.assertEqual(resp.status_code, 302)
        role = Role.objects.get(name="Field Staff")
        self.assertEqual(role.code, "field-staff")
        self.assertEqual(role.sort_order, 5)

    def test_create_japanese_name_gets_fallback_code(self):
        resp = self.client.post(reverse("accounts:role_create"), {"name": "現場担当"})
        self.assertEqual(resp.status_code, 302)
        role = Role.objects.get(name="現場担当")
        self.assertTrue(role.code)  # 空でない
        self.assertNotEqual(role.code, "admin")

    def test_create_name_admin_does_not_yield_admin_code(self):
        # 表示名 "admin" でも code は予約語 admin を避ける（詐称防止）
        role = create_role(operator=self.operator, name="admin")
        self.assertNotEqual(role.code, "admin")

    def test_create_duplicate_name_rejected(self):
        self.client.post(reverse("accounts:role_create"), {"name": "Dup"})
        resp = self.client.post(reverse("accounts:role_create"), {"name": "Dup"})
        # フォーム/サービスで弾かれ、リダイレクトせず再表示（200）
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Role.objects.filter(name="Dup").count(), 1)


class RoleUpdateTests(RoleCrudBaseTest):
    def test_edit_name(self):
        resp = self.client.post(
            reverse("accounts:role_update", kwargs={"pk": self.role_sales.pk}),
            {"name": "営業（改）", "sort_order": self.role_sales.sort_order,
             "default_groups": list(
                 self.role_sales.default_groups.values_list("pk", flat=True))},
        )
        self.assertEqual(resp.status_code, 302)
        self.role_sales.refresh_from_db()
        self.assertEqual(self.role_sales.name, "営業（改）")

    def test_admin_edit_blocked_get(self):
        resp = self.client.get(
            reverse("accounts:role_update", kwargs={"pk": self.role_admin.pk})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("accounts:role_list"))

    def test_admin_edit_blocked_post(self):
        resp = self.client.post(
            reverse("accounts:role_update", kwargs={"pk": self.role_admin.pk}),
            {"name": "のっとり", "default_groups": []},
        )
        self.assertEqual(resp.status_code, 302)
        self.role_admin.refresh_from_db()
        self.assertEqual(self.role_admin.name, "管理者")

    def test_default_groups_reapply_active_and_retired(self):
        # 専用ロールを作り、在職・退職の2名へ付与（初期 default_groups = person_viewer）
        role = create_role(operator=self.operator, name="一時ロール")
        pv = Group.objects.get(name="person_viewer")
        role.default_groups.set([pv])
        active = User.objects.create_user("act_u", password="x", is_active=True)
        retired = User.objects.create_user("ret_u", password="x", is_active=False)
        apply_role(active, role)
        apply_role(retired, role)
        self.assertEqual(set(active.groups.values_list("name", flat=True)), {"person_viewer"})
        self.assertEqual(set(retired.groups.values_list("name", flat=True)), {"person_viewer"})

        ActionLog.objects.filter(action="role_update").delete()
        ce = Group.objects.get(name="card_editor")

        resp = self.client.post(
            reverse("accounts:role_update", kwargs={"pk": role.pk}),
            {"name": role.name, "default_groups": [ce.pk]},
        )
        self.assertEqual(resp.status_code, 302)

        active.refresh_from_db()
        retired.refresh_from_db()
        # 在職・退職とも新 default_groups（card_editor）へ揃う
        self.assertEqual(set(active.groups.values_list("name", flat=True)), {"card_editor"})
        self.assertEqual(set(retired.groups.values_list("name", flat=True)), {"card_editor"})

        logs = ActionLog.objects.filter(action="role_update")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().data["affected_user_count"], 2)

    def test_message_shows_affected_count_on_groups_change(self):
        role = create_role(operator=self.operator, name="人数表示ロール")
        pv = Group.objects.get(name="person_viewer")
        role.default_groups.set([pv])
        active = User.objects.create_user("msg_act", password="x", is_active=True)
        retired = User.objects.create_user("msg_ret", password="x", is_active=False)
        apply_role(active, role)
        apply_role(retired, role)
        ce = Group.objects.get(name="card_editor")

        resp = self.client.post(
            reverse("accounts:role_update", kwargs={"pk": role.pk}),
            {"name": role.name, "default_groups": [ce.pk]},
            follow=True,
        )
        # 在職＋退職で2人。反映人数が success メッセージに出る。
        self.assertContains(resp, "2 人（退職者含む）に反映")

    def test_message_no_count_when_groups_unchanged(self):
        # default_groups を変えず表示名だけ変更 → 人数文言を出さない
        current_groups = list(
            self.role_sales.default_groups.values_list("pk", flat=True)
        )
        resp = self.client.post(
            reverse("accounts:role_update", kwargs={"pk": self.role_sales.pk}),
            {"name": "営業v2", "sort_order": self.role_sales.sort_order,
             "default_groups": current_groups},
            follow=True,
        )
        self.assertContains(resp, "更新しました")
        self.assertNotContains(resp, "に反映しました")


class RoleDeleteTests(RoleCrudBaseTest):
    def test_delete_no_holders(self):
        role = create_role(operator=self.operator, name="捨てロール")
        resp = self.client.post(
            reverse("accounts:role_delete", kwargs={"pk": role.pk})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Role.objects.filter(pk=role.pk).exists())

    def test_delete_blocked_with_retired_only_holder(self):
        role = create_role(operator=self.operator, name="保持ロール")
        retired = User.objects.create_user("only_ret", password="x", is_active=False)
        apply_role(retired, role)
        resp = self.client.post(
            reverse("accounts:role_delete", kwargs={"pk": role.pk})
        )
        self.assertEqual(resp.status_code, 302)
        # 退職者のみの保持でも削除されない
        self.assertTrue(Role.objects.filter(pk=role.pk).exists())

    def test_delete_page_lists_holders_and_no_form(self):
        role = create_role(operator=self.operator, name="保持ロール2")
        holder = User.objects.create_user("hold_u", password="x")
        apply_role(holder, role)
        resp = self.client.get(
            reverse("accounts:role_delete", kwargs={"pk": role.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "hold_u")
        # 削除フォームの送信ボタンが出ない
        self.assertNotContains(resp, "削除する")

    def test_service_delete_rejects_with_holders(self):
        from django.core.exceptions import ValidationError

        role = create_role(operator=self.operator, name="svc保持")
        holder = User.objects.create_user("svc_hold", password="x")
        apply_role(holder, role)
        with self.assertRaises(ValidationError):
            delete_role(operator=self.operator, role=role)
        self.assertTrue(Role.objects.filter(pk=role.pk).exists())

    def test_admin_delete_blocked(self):
        resp = self.client.post(
            reverse("accounts:role_delete", kwargs={"pk": self.role_admin.pk})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Role.objects.filter(code="admin").exists())
