"""パーミッション一覧（read-only・逆引き）の検証（アクセス管理UI (D)）。

- ドメイン app の permission が1つの表に出る（app/モデル順→codename順）／フレームワーク app
  （admin/auth/...）は除外。
- 逆引き：permission に対し、それを含むグループ名が表示される。
- 絞り込みはクライアント JS 即時フィルタへ移行（サーバサイド q 廃止）。本テストでは DOM 構造
  （行の data 属性・対象モデル▼チェックボックス）を担保する（JS の即時挙動は test client 対象外）。
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

    FRAMEWORK = {"admin", "auth", "contenttypes", "sessions"}

    def test_single_table_lists_all_perms_including_framework_ordered(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        # 1つの表に統合（id=permTable は1つ）。
        self.assertContains(resp, 'id="permTable"', count=1)
        rows = resp.context["perm_rows"]
        # 除外を廃止＝全 permission（フレームワーク app 含む）が1リストで出る。
        self.assertEqual(len(rows), Permission.objects.count())
        # app/モデル順→codename順。section の出現順（distinct）が昇順＝app/モデル順を担保。
        cts = resp.context["content_types"]
        self.assertEqual(cts, sorted(cts))
        # cards の businesscard セクションと業務 permission の codename が出る。
        self.assertTrue(any(s.startswith("cards /") for s in cts))
        self.assertContains(resp, "edit_card")
        # app/モデル列の見出しが追加されている。
        self.assertContains(resp, "<th>app/モデル</th>")

    def test_framework_apps_now_included(self):
        # 除外廃止：フレームワーク app の permission も一覧（perm_rows）に含まれる。
        resp = self.client.get(self.url)
        apps = {r["perm"].content_type.app_label for r in resp.context["perm_rows"]}
        self.assertTrue(self.FRAMEWORK.issubset(apps))
        # sessions / contenttypes 等の codename も描画される。
        self.assertContains(resp, "add_session")

    def test_app_and_framework_context(self):
        resp = self.client.get(self.url)
        # アプリ一覧が出現順 distinct で渡る。
        app_labels = resp.context["app_labels"]
        self.assertEqual(app_labels, list(dict.fromkeys(app_labels)))
        self.assertIn("cards", app_labels)
        self.assertIn("admin", app_labels)
        # framework_apps が4 app ちょうど。
        self.assertEqual(set(resp.context["framework_apps"]), self.FRAMEWORK)

    def test_header_and_intro_text(self):
        resp = self.client.get(self.url)
        # 閲覧専用の補足が出る（位置は案内文の上・見出しの下）。
        self.assertContains(resp, "閲覧専用（permission はコード定義・編集は Django 管理サイト）")
        # 案内文は実態に合わせた新文言（除外ではなく初期非表示＋アプリ絞り込みで表示）。
        self.assertContains(resp, "由来は初期表示では隠しています。「アプリ」の絞り込みから表示できます。")
        self.assertNotContains(resp, "由来は除外しています")

    def test_reverse_lookup_groups_shown(self):
        # change_businesscard permission は card_editor / card_admin に含まれる（逆引き表示）。
        # 全件1ページ描画なので q なしでそのまま含まれる。
        resp = self.client.get(self.url)
        self.assertContains(resp, "change_businesscard")
        self.assertContains(resp, "card_editor")
        self.assertContains(resp, "card_admin")

    def test_rows_have_filter_data_attributes(self):
        resp = self.client.get(self.url)
        # 各行に JS フィルタ用の data 属性（data-app・data-ct・data-search）が付く。
        self.assertContains(resp, 'class="js-perm-row"')
        self.assertContains(resp, 'data-app="cards"')
        self.assertContains(resp, 'data-ct="cards / businesscard"')
        # data-search は codename・name を小文字連結。
        self.assertContains(resp, 'data-search="edit_card ')

    def test_content_type_dropdown_checkboxes_initial_checked(self):
        resp = self.client.get(self.url)
        cts = resp.context["content_types"]
        # 対象モデル▼ボタンと、content_type 数ぶんのチェックボックス（初期は全チェック）。
        self.assertContains(resp, "js-perm-ct-toggle")
        self.assertContains(resp, 'class="js-perm-ct-check"', count=len(cts))
        # value は app/モデルラベル、初期は checked。
        self.assertContains(resp, 'value="cards / businesscard" checked')

    def test_app_dropdown_checkboxes_business_on_framework_off(self):
        resp = self.client.get(self.url)
        app_labels = resp.context["app_labels"]
        # アプリ▼ボタンと、app 数ぶんのチェックボックス。
        self.assertContains(resp, "js-perm-app-toggle")
        self.assertContains(resp, 'class="js-perm-app-check"', count=len(app_labels))
        # 業務app（cards）は初期ON＝checked。
        self.assertContains(resp, 'value="cards" checked')
        # フレームワークapp（admin）は初期OFF＝checked が付かない。
        self.assertContains(resp, 'value="admin"')
        self.assertNotContains(resp, 'value="admin" checked')


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
