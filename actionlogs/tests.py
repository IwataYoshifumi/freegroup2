from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .admin import ActionLogAdmin
from .models import ActionLog

User = get_user_model()


class ActionLogAdminTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = ActionLogAdmin(ActionLog, admin.site)
        self.superuser = User.objects.create_superuser(
            username="admin_user", password="password", email="admin@example.com"
        )
        self.log = ActionLog.record(
            user=self.superuser,
            action="test_action",
            object_repr="Test Object",
            note="テスト用ログ",
            data={"key": "value"},
        )

    def test_admin_is_registered(self):
        self.assertIn(ActionLog, admin.site._registry)
        self.assertIsInstance(admin.site._registry[ActionLog], ActionLogAdmin)

    def test_permissions_are_strictly_read_only(self):
        request = self.factory.get("/admin/actionlogs/actionlog/")
        request.user = self.superuser

        self.assertFalse(self.admin.has_add_permission(request))
        self.assertFalse(self.admin.has_change_permission(request, self.log))
        self.assertFalse(self.admin.has_delete_permission(request, self.log))
        self.assertTrue(self.admin.has_view_permission(request, self.log))

    def test_admin_changelist_view(self):
        self.client.force_login(self.superuser)
        url = reverse("admin:actionlogs_actionlog_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "test_action")
        self.assertContains(resp, "Test Object")
        self.assertContains(resp, "テスト用ログ")
        # 追加URLが存在しないこと
        add_url = reverse("admin:actionlogs_actionlog_add")
        self.assertNotContains(resp, add_url)

    def test_admin_change_view_is_view_only(self):
        self.client.force_login(self.superuser)
        url = reverse("admin:actionlogs_actionlog_change", args=[self.log.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "テスト用ログ")
        # 削除ボタン・保存ボタンが存在しないこと
        self.assertNotContains(resp, 'name="_save"')
        self.assertNotContains(resp, "deletelink")
