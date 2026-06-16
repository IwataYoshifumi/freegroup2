"""パーミッション一覧（read-only・逆引き）の検証（アクセス管理UI (D)）。

- ドメイン app の permission が出る／フレームワーク app（admin/auth/...）は除外／
  app・モデル単位でグルーピング。
- 逆引き：permission に対し、それを含むグループ名が表示される。
- 検索：codename / name / app で部分一致絞り込み・クリアで戻る。
- read-only：編集 URL/ボタンが無い・POST 不可（405）。
- 権限：manage_role 無しでは到達できない（403）。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

User = get_user_model()


def _grant_manage_role(user):
    perm = Permission.objects.get(
        codename="manage_role", content_type__app_label="accounts"
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


class PermissionListPermTests(TestCase):
    def test_forbidden_without_perm(self):
        u = User.objects.create_user("nope", password="x")
        c = Client()
        c.force_login(u)
        self.assertEqual(c.get(reverse("accounts:permission_list")).status_code, 403)


class PermissionListTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.op = _grant_manage_role(User.objects.create_user("perm_op", password="x"))
        self.client.force_login(self.op)
        self.url = reverse("accounts:permission_list")

    def test_lists_domain_perms_grouped(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        sections = resp.context["perm_sections"]
        # cards の businesscard セクションが出る
        self.assertTrue(any(s.startswith("cards /") for s in sections))
        # 業務 permission の codename が出る
        self.assertContains(resp, "edit_card")

    def test_framework_apps_excluded(self):
        resp = self.client.get(self.url)
        sections = resp.context["perm_sections"]
        for s in sections:
            app = s.split(" / ")[0]
            self.assertNotIn(app, {"admin", "auth", "contenttypes", "sessions"})
        # session/contenttype 等の codename が出ない
        self.assertNotContains(resp, "add_session")

    def test_reverse_lookup_groups_shown(self):
        # change_businesscard permission は card_editor / card_admin に含まれる（逆引き表示）。
        # ※ カスタム権限 edit_card はシード上どのグループにも未付与なので、実際に付与
        #   されている標準 CRUD 権限で逆引きを検証する。
        resp = self.client.get(self.url, {"q": "change_businesscard"})
        self.assertContains(resp, "card_editor")
        self.assertContains(resp, "card_admin")

    def test_search_by_codename(self):
        resp = self.client.get(self.url, {"q": "merge_person"})
        self.assertContains(resp, "merge_person")
        self.assertNotContains(resp, "edit_card")

    def test_search_by_app(self):
        resp = self.client.get(self.url, {"q": "tags"})
        sections = resp.context["perm_sections"]
        self.assertTrue(all(s.startswith("tags /") for s in sections))

    def test_clear_returns_full_list(self):
        resp = self.client.get(self.url)
        # クリア時（q なし）は複数 app のセクションが出る
        apps = {s.split(" / ")[0] for s in resp.context["perm_sections"]}
        self.assertGreater(len(apps), 1)


class PermissionListReadOnlyTests(TestCase):
    def test_no_edit_routes_exist(self):
        for name in ("permission_create", "permission_update", "permission_delete"):
            with self.assertRaises(NoReverseMatch):
                reverse(f"accounts:{name}")

    def test_get_only(self):
        op = _grant_manage_role(User.objects.create_user("perm_ro", password="x"))
        c = Client()
        c.force_login(op)
        resp = c.post(reverse("accounts:permission_list"))
        self.assertEqual(resp.status_code, 405)
