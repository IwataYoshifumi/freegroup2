"""AssignRoleView（ロール割り当て UI）の検証（宿題F・ロール割り当て UI）。

- assign_role_to_user 保持者は他人のロールを変更でき、apply_role で groups が差し替わる。
- 非保持者は 403。
- 自分自身のロール変更は GET/POST とも弾かれる（自爆防止）。
- 同一 role の保存では apply_role を走らせない（手動追加 Group を消さない・ActionLog も残さない）。
- role 変更時に ActionLog（assign_role）が記録される。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.services import apply_role
from actionlogs.models import ActionLog

User = get_user_model()


class AssignRoleViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # ロール3種は accounts の data migration で test DB に投入済み。
        cls.role_admin = Role.objects.get(code="admin")
        cls.role_sales = Role.objects.get(code="sales")
        cls.role_viewer = Role.objects.get(code="viewer")

    def setUp(self):
        self.operator = User.objects.create_user("op_admin", password="x")
        perm = Permission.objects.get(
            codename="assign_role_to_user", content_type__app_label="accounts"
        )
        self.operator.user_permissions.add(perm)
        # has_perm キャッシュを避けるため取り直す
        self.operator = User.objects.get(pk=self.operator.pk)

        self.target = User.objects.create_user("target_u", password="x")
        # 対象に初期ロール viewer を付与（groups が viewer 既定で埋まる）。
        apply_role(self.target, self.role_viewer)

        self.client = Client()
        self.client.force_login(self.operator)

    def _url(self, user=None):
        return reverse(
            "accounts:assign_role", kwargs={"user_id": (user or self.target).pk}
        )

    # ---- 正常系：保持者が他人のロールを変更し groups が差し替わる ----

    def test_get_renders_role_choices(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        # 変更先 admin は本体 UI で選ばせない（select から除外）。
        self.assertNotContains(resp, self.role_admin.name)
        self.assertContains(resp, self.role_sales.name)
        self.assertContains(resp, self.role_viewer.name)

    def test_holder_changes_role_and_groups_swap(self):
        resp = self.client.post(self._url(), {"role": self.role_sales.pk})
        self.assertEqual(resp.status_code, 302)

        self.target.refresh_from_db()
        self.assertEqual(self.target.role_id, self.role_sales.pk)
        # groups が新ロール（sales）の default_groups に差し替わっている
        self.assertEqual(
            set(self.target.groups.values_list("name", flat=True)),
            set(self.role_sales.default_groups.values_list("name", flat=True)),
        )

    # ---- 認可：非保持者は 403 ----

    def test_non_holder_forbidden(self):
        stranger = User.objects.create_user("no_perm_u", password="x")
        c = Client()
        c.force_login(stranger)
        self.assertEqual(c.get(self._url()).status_code, 403)
        self.assertEqual(
            c.post(self._url(), {"role": self.role_admin.pk}).status_code, 403
        )

    # ---- 自爆防止：本人のロール変更は GET/POST とも弾く ----

    def test_self_change_blocked_get(self):
        url_self = reverse(
            "accounts:assign_role", kwargs={"user_id": self.operator.pk}
        )
        resp = self.client.get(url_self)
        self.assertEqual(resp.status_code, 302)  # user_detail へ redirect（変更画面は出さない）
        self.operator.refresh_from_db()
        self.assertIsNone(self.operator.role_id)

    def test_self_change_blocked_post(self):
        url_self = reverse(
            "accounts:assign_role", kwargs={"user_id": self.operator.pk}
        )
        resp = self.client.post(url_self, {"role": self.role_admin.pk})
        self.assertEqual(resp.status_code, 302)
        self.operator.refresh_from_db()
        # 本人 POST は弾かれ role は変わらない（自爆防止）
        self.assertIsNone(self.operator.role_id)

    # ---- 同一 role：apply_role を走らせず手動 Group を保持・ActionLog も残さない ----

    def test_same_role_preserves_manual_group_and_no_log(self):
        extra = Group.objects.create(name="manual_extra_grp")
        self.target.groups.add(extra)
        before = set(self.target.groups.values_list("name", flat=True))
        ActionLog.objects.filter(action="assign_role").delete()

        resp = self.client.post(self._url(), {"role": self.role_viewer.pk})
        self.assertEqual(resp.status_code, 302)

        self.target.refresh_from_db()
        after = set(self.target.groups.values_list("name", flat=True))
        # 手動追加 Group が消えず、groups 全体も不変
        self.assertIn("manual_extra_grp", after)
        self.assertEqual(before, after)
        # 変更なしなので ActionLog も残らない
        self.assertEqual(
            ActionLog.objects.filter(action="assign_role").count(), 0
        )

    # ---- ActionLog 記録：role 変更時のみ ----

    def test_actionlog_recorded_on_change(self):
        ActionLog.objects.filter(action="assign_role").delete()
        self.client.post(self._url(), {"role": self.role_sales.pk})

        logs = ActionLog.objects.filter(action="assign_role")
        self.assertEqual(logs.count(), 1)
        log = logs.first()
        self.assertEqual(log.user_id, self.operator.pk)
        self.assertEqual(log.data["old_role_code"], "viewer")
        self.assertEqual(log.data["new_role_code"], "sales")

    # ---- 一覧からの割り当て：next で一覧へ戻る ----

    def test_assign_redirects_to_safe_next(self):
        list_url = reverse("accounts:user_list")
        resp = self.client.post(
            self._url(), {"role": self.role_sales.pk, "next": list_url}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, list_url)

    def test_assign_ignores_unsafe_next(self):
        # オープンリダイレクトを試みる next は無視し、user_detail へ戻す。
        resp = self.client.post(
            self._url(),
            {"role": self.role_sales.pk, "next": "https://evil.example.com/"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse("accounts:user_detail", kwargs={"user_id": self.target.pk}),
        )

    # ---- admin 両方向ガード（UI 層）----

    def test_admin_excluded_from_role_select(self):
        resp = self.client.get(self._url())
        # select の option に admin の pk が出ない
        self.assertNotContains(resp, f'value="{self.role_admin.pk}"')

    def test_get_for_admin_target_redirects(self):
        # 対象の現ロールが admin → 変更画面を出さず user_detail へ
        apply_role(self.target, self.role_admin)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse("accounts:user_detail", kwargs={"user_id": self.target.pk}),
        )

    # ---- admin 両方向ガード（View 経由・最終砦）----

    def test_post_for_admin_target_blocked(self):
        # 現ロール admin の対象への直 URL POST も弾く（role は変わらない）
        apply_role(self.target, self.role_admin)
        resp = self.client.post(self._url(), {"role": self.role_sales.pk})
        self.assertEqual(resp.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.role_id, self.role_admin.pk)

    def test_post_to_admin_role_blocked(self):
        # 変更先 admin への直 URL POST も弾く（viewer のまま）
        resp = self.client.post(self._url(), {"role": self.role_admin.pk})
        self.assertEqual(resp.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.role_id, self.role_viewer.pk)


class AssignRoleServiceGuardTests(TestCase):
    """services.assign_role_to_user の admin 両方向ガード（最終砦）の単体検証。"""

    @classmethod
    def setUpTestData(cls):
        cls.role_admin = Role.objects.get(code="admin")
        cls.role_sales = Role.objects.get(code="sales")
        cls.role_viewer = Role.objects.get(code="viewer")

    def setUp(self):
        self.operator = User.objects.create_user("svc_op", password="x")
        self.target = User.objects.create_user("svc_target", password="x")

    def test_current_admin_rejected(self):
        from django.core.exceptions import ValidationError

        from accounts.services import assign_role_to_user

        apply_role(self.target, self.role_admin)
        with self.assertRaises(ValidationError):
            assign_role_to_user(
                operator=self.operator, user=self.target, role=self.role_sales
            )
        self.target.refresh_from_db()
        self.assertEqual(self.target.role_id, self.role_admin.pk)

    def test_target_role_admin_rejected(self):
        from django.core.exceptions import ValidationError

        from accounts.services import assign_role_to_user

        apply_role(self.target, self.role_viewer)
        with self.assertRaises(ValidationError):
            assign_role_to_user(
                operator=self.operator, user=self.target, role=self.role_admin
            )
        self.target.refresh_from_db()
        self.assertEqual(self.target.role_id, self.role_viewer.pk)

    def test_non_admin_to_non_admin_succeeds(self):
        from accounts.services import assign_role_to_user

        apply_role(self.target, self.role_viewer)
        changed = assign_role_to_user(
            operator=self.operator, user=self.target, role=self.role_sales
        )
        self.assertTrue(changed)
        self.target.refresh_from_db()
        self.assertEqual(self.target.role_id, self.role_sales.pk)
