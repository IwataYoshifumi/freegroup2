"""accounts/0008 ロール／グループ整合是正の検証。

migrate 後の初期データ（グループ存在・権限紐付け・Role.default_groups）と、各ロール相当
ユーザー（グループ経由）の has_perm 判定が Phase 7 要求と整合していることを確認する。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from accounts.models import Role

User = get_user_model()


def _group_perms(name):
    """グループの権限を {(app_label, codename)} 集合で返す。"""
    group = Group.objects.get(name=name)
    return set(
        group.permissions.values_list("content_type__app_label", "codename")
    )


class GroupExistenceTests(TestCase):
    """13 既存グループ ＋ contact_* 3 が存在すること。"""

    EXISTING = [
        "campaign_admin", "campaign_editor", "campaign_viewer",
        "tag_admin", "tag_editor", "tag_viewer",
        "person_admin", "person_editor", "person_viewer",
        "user_admin",
        "card_admin", "card_editor", "card_viewer",
    ]
    CONTACT = ["contact_viewer", "contact_editor", "contact_admin"]

    def test_existing_13_groups_present(self):
        for name in self.EXISTING:
            self.assertTrue(
                Group.objects.filter(name=name).exists(), f"missing group {name}"
            )
        self.assertEqual(len(self.EXISTING), 13)

    def test_contact_groups_created(self):
        for name in self.CONTACT:
            self.assertTrue(
                Group.objects.filter(name=name).exists(), f"missing group {name}"
            )


class ContactGroupPermissionTests(TestCase):
    def test_contact_viewer(self):
        self.assertEqual(_group_perms("contact_viewer"), {("contacts", "view_contact")})

    def test_contact_editor(self):
        self.assertEqual(
            _group_perms("contact_editor"),
            {
                ("contacts", "view_contact"),
                ("contacts", "add_contact"),
                ("contacts", "change_contact"),
            },
        )

    def test_contact_admin(self):
        self.assertEqual(
            _group_perms("contact_admin"),
            {
                ("contacts", "view_contact"),
                ("contacts", "add_contact"),
                ("contacts", "change_contact"),
                ("contacts", "delete_contact"),
                ("contacts", "edit_all_contacts"),
            },
        )

    def test_edit_all_contacts_only_in_admin(self):
        self.assertNotIn(("contacts", "edit_all_contacts"), _group_perms("contact_editor"))
        self.assertNotIn(("contacts", "edit_all_contacts"), _group_perms("contact_viewer"))


class CardGroupPermissionTests(TestCase):
    """card_* は標準 CRUD に張り替わり、死蔵権限の紐付けが消えていること。"""

    DEAD = {("cards", "create_card"), ("cards", "edit_card"), ("cards", "merge_card")}

    def test_card_viewer(self):
        self.assertEqual(
            _group_perms("card_viewer"),
            {("cards", "view_businesscard"), ("cards", "view_originalimage")},
        )

    def test_card_editor(self):
        self.assertEqual(
            _group_perms("card_editor"),
            {
                ("cards", "view_businesscard"),
                ("cards", "view_originalimage"),
                ("cards", "add_originalimage"),
                ("cards", "add_businesscard"),
                ("cards", "change_businesscard"),
                ("cards", "change_originalimage"),
            },
        )

    def test_card_admin(self):
        self.assertEqual(
            _group_perms("card_admin"),
            {
                ("cards", "view_businesscard"),
                ("cards", "view_originalimage"),
                ("cards", "add_originalimage"),
                ("cards", "add_businesscard"),
                ("cards", "change_businesscard"),
                ("cards", "change_originalimage"),
                ("cards", "delete_businesscard"),
                ("cards", "delete_originalimage"),
            },
        )

    def test_dead_permissions_unbound(self):
        for name in ("card_viewer", "card_editor", "card_admin"):
            self.assertEqual(
                _group_perms(name) & self.DEAD, set(), f"dead perm bound in {name}"
            )


class PersonEditorViewPersonTests(TestCase):
    def test_person_editor_has_view_person(self):
        self.assertIn(("persons", "view_person"), _group_perms("person_editor"))


class RoleDefaultGroupsTests(TestCase):
    """default_groups の最終形。

    注：§4（admin←contact_admin,card_admin / sales←contact_editor,card_editor /
    viewer←contact_viewer,card_viewer・既存は set しない）どおり実装すると、card_* は
    0005 で既に束ねられているため純増は contact_* のみ。結果 admin=6・sales=5・viewer=5。
    （依頼書テスト欄の 6/6/6 は 0005 で card_* が既に束ねられている点の見落としと判断し、
    実装どおりの 6/5/5 を正とする。完了報告で要確認事項として明記。）
    """

    def _names(self, code):
        return set(Role.objects.get(code=code).default_groups.values_list("name", flat=True))

    def test_admin_groups(self):
        names = self._names("admin")
        self.assertEqual(len(names), 6)
        self.assertIn("contact_admin", names)
        self.assertIn("card_admin", names)

    def test_sales_groups(self):
        names = self._names("sales")
        self.assertEqual(len(names), 5)
        self.assertIn("contact_editor", names)
        self.assertIn("card_editor", names)

    def test_viewer_groups(self):
        names = self._names("viewer")
        self.assertEqual(len(names), 5)
        self.assertIn("contact_viewer", names)
        self.assertIn("card_viewer", names)

    def test_existing_bundles_unchanged(self):
        # 既存束ね（person_*/campaign_*/tag_*/user_admin）が維持されていること。
        self.assertIn("person_admin", self._names("admin"))
        self.assertIn("user_admin", self._names("admin"))
        self.assertIn("campaign_admin", self._names("admin"))
        self.assertIn("tag_admin", self._names("admin"))
        self.assertIn("campaign_editor", self._names("sales"))
        self.assertIn("tag_viewer", self._names("viewer"))


class ForwardIdempotencyTests(TestCase):
    """forward を再実行しても状態が壊れない（get_or_create / add の冪等性）。"""

    ALL_GROUPS = [
        "contact_viewer", "contact_editor", "contact_admin",
        "card_viewer", "card_editor", "card_admin", "person_editor",
    ]

    def _snapshot(self):
        groups = {n: _group_perms(n) for n in self.ALL_GROUPS}
        roles = {
            c: set(Role.objects.get(code=c).default_groups.values_list("name", flat=True))
            for c in ("admin", "sales", "viewer")
        }
        return groups, roles

    def test_forward_twice_is_stable(self):
        from importlib import import_module

        from django.apps import apps as global_apps

        before = self._snapshot()
        migration = import_module(
            "accounts.migrations.0008_align_role_group_permissions"
        )
        # 2 回目の forward を流す（apply 済みの状態に対する再実行）。
        migration.forward(global_apps, None)
        after = self._snapshot()
        self.assertEqual(before, after)


class RoleHasPermTests(TestCase):
    """各ロール相当ユーザー（グループ経由）の has_perm 判定。"""

    @classmethod
    def setUpTestData(cls):
        def make(username, role_code):
            u = User.objects.create_user(username=username, password="x")
            role = Role.objects.get(code=role_code)
            u.groups.set(role.default_groups.all())
            return User.objects.get(pk=u.pk)  # perm cache を持たない再取得

        cls.admin = make("rg_admin", "admin")
        cls.sales = make("rg_sales", "sales")
        cls.viewer = make("rg_viewer", "viewer")

    def test_admin_full_card_and_contact(self):
        for p in (
            "cards.view_businesscard", "cards.add_businesscard",
            "cards.change_businesscard", "cards.delete_businesscard",
            "cards.view_originalimage", "cards.add_originalimage",
            "cards.change_originalimage", "cards.delete_originalimage",
            "contacts.view_contact", "contacts.add_contact",
            "contacts.change_contact", "contacts.delete_contact",
            "contacts.edit_all_contacts",
        ):
            self.assertTrue(self.admin.has_perm(p), f"admin should have {p}")

    def test_sales_view_add_change_not_delete(self):
        for p in (
            "cards.view_businesscard", "cards.add_businesscard",
            "cards.change_businesscard",
            "contacts.view_contact", "contacts.add_contact", "contacts.change_contact",
            "persons.view_person",
        ):
            self.assertTrue(self.sales.has_perm(p), f"sales should have {p}")
        for p in (
            "cards.delete_businesscard", "cards.delete_originalimage",
            "contacts.delete_contact", "contacts.edit_all_contacts",
        ):
            self.assertFalse(self.sales.has_perm(p), f"sales should NOT have {p}")

    def test_viewer_view_only(self):
        for p in ("cards.view_businesscard", "cards.view_originalimage", "contacts.view_contact"):
            self.assertTrue(self.viewer.has_perm(p), f"viewer should have {p}")
        for p in (
            "cards.add_businesscard", "cards.change_businesscard",
            "cards.delete_businesscard",
            "contacts.add_contact", "contacts.change_contact",
            "contacts.delete_contact", "contacts.edit_all_contacts",
        ):
            self.assertFalse(self.viewer.has_perm(p), f"viewer should NOT have {p}")
