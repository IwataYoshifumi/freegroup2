"""CustomUserAdmin のロール設定欄表示と apply_role 発火経路の検証（宿題S-(2)）。

回帰防止対象：admin の変更/作成フォームに role 等が表示されず、save_model() の
apply_role() 発火経路が UI から到達不能だった問題（get_fieldsets が独自フィールドを
fieldsets に追加していなかった）。
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase

from accounts.admin import CustomUserAdmin
from accounts.models import CustomUser, Role
from accounts.services import apply_role

User = get_user_model()


class _CustomUserAdminTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="admin_root", password="x", email="root@example.com"
        )
        cls.role_admin = Role.objects.get(code="admin")
        cls.role_viewer = Role.objects.get(code="viewer")

    def setUp(self):
        self.ma = CustomUserAdmin(CustomUser, AdminSite())

    def _request(self):
        req = RequestFactory().post("/")
        req.user = self.superuser
        return req

    def _admin_groups(self):
        return set(self.role_admin.default_groups.values_list("name", flat=True))

    def _viewer_groups(self):
        return set(self.role_viewer.default_groups.values_list("name", flat=True))


class FormFieldVisibilityTests(_CustomUserAdminTestBase):
    """フォームに独自フィールドが出る／person・user_permissions は出ないこと。"""

    def test_change_form_shows_role_department_auth_source(self):
        change_form = self.ma.get_form(self._request(), obj=self.superuser)
        for f in ("role", "department", "auth_source"):
            self.assertIn(f, change_form.base_fields, f"change form に {f} が無い")

    def test_add_form_shows_role(self):
        add_form = self.ma.get_form(self._request(), obj=None)
        self.assertIn("role", add_form.base_fields, "add form に role が無い")

    def test_person_not_in_change_or_add_form(self):
        change_form = self.ma.get_form(self._request(), obj=self.superuser)
        add_form = self.ma.get_form(self._request(), obj=None)
        self.assertNotIn("person", change_form.base_fields)
        self.assertNotIn("person", add_form.base_fields)

    def test_user_permissions_still_excluded(self):
        change_form = self.ma.get_form(self._request(), obj=self.superuser)
        self.assertNotIn("user_permissions", change_form.base_fields)

    def test_change_fieldsets_contain_role_section(self):
        fieldsets = self.ma.get_fieldsets(self._request(), obj=self.superuser)
        all_fields = tuple(
            f for _, opts in fieldsets for f in opts.get("fields", ())
        )
        self.assertIn("role", all_fields)
        self.assertIn("department", all_fields)
        self.assertIn("auth_source", all_fields)
        self.assertNotIn("person", all_fields)

    def test_add_fieldsets_contain_role(self):
        fieldsets = self.ma.get_fieldsets(self._request(), obj=None)
        all_fields = tuple(
            f for _, opts in fieldsets for f in opts.get("fields", ())
        )
        self.assertIn("role", all_fields)
        # 新規作成では department/auth_source/person は早期要求しない。
        self.assertNotIn("person", all_fields)


class ApplyRoleViaAdminTests(_CustomUserAdminTestBase):
    """admin の作成/保存経路で apply_role() の発火・不発火が期待どおりであること。"""

    def test_create_with_role_expands_default_groups(self):
        """新規作成フォームで role 指定 → save_model で apply_role 発火 → groups 展開。"""
        req = self._request()
        AddForm = self.ma.get_form(req, obj=None)
        form = AddForm(
            data={
                "username": "newbie",
                "password1": "Str0ng!Pass123",
                "password2": "Str0ng!Pass123",
                "usable_password": "true",
                "role": self.role_admin.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.ma.save_model(req, obj, form, change=False)

        obj.refresh_from_db()
        self.assertEqual(obj.role_id, self.role_admin.pk)
        self.assertEqual(
            set(obj.groups.values_list("name", flat=True)), self._admin_groups()
        )
        self.assertTrue(self._admin_groups(), "admin ロールの default_groups が空でない前提")

    def test_change_role_replaces_groups(self):
        """role を viewer→admin に変更して保存 → groups が新 role の default_groups に置換。"""
        user = User.objects.create_user(username="changer", password="x")
        apply_role(user, self.role_viewer)
        self.assertEqual(
            set(user.groups.values_list("name", flat=True)), self._viewer_groups()
        )

        # admin の変更保存をシミュレート（role を差し替えて save_model 呼び出し）。
        obj = CustomUser.objects.get(pk=user.pk)
        obj.role = self.role_admin
        self.ma.save_model(self._request(), obj, form=None, change=True)

        obj.refresh_from_db()
        self.assertEqual(
            set(obj.groups.values_list("name", flat=True)), self._admin_groups()
        )

    def test_save_without_role_change_keeps_groups(self):
        """role を変えずに保存 → apply_role 不発火 → 手動追加分含め groups 不変。"""
        user = User.objects.create_user(username="stable", password="x")
        apply_role(user, self.role_viewer)
        extra = Group.objects.create(name="extra_manual_group")
        user.groups.add(extra)
        expected = set(user.groups.values_list("name", flat=True))

        # role 以外を変更して保存（role_id は不変）。
        obj = CustomUser.objects.get(pk=user.pk)
        obj.email = "stable_changed@example.com"
        self.ma.save_model(self._request(), obj, form=None, change=True)

        obj.refresh_from_db()
        self.assertEqual(set(obj.groups.values_list("name", flat=True)), expected)
        self.assertIn("extra_manual_group", expected)  # 手動グループが消えていない
