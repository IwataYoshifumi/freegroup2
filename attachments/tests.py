import os
import shutil
import tempfile
from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from activities.models import Activity
from attachments.admin import AttachmentAdmin
from attachments.models import Attachment
from deals.models import Deal
from persons.models import Person

User = get_user_model()


class AttachmentModelConstraintTests(TestCase):
    """Attachment の CheckConstraint 排他検証（仕様書 §4.4.1）。"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(PROTECTED_MEDIA_ROOT=self.temp_dir)
        self.settings_override.enable()

        self.user = User.objects.create_user(username="testuser", password="password")
        self.person = Person.objects.create()
        self.deal = Deal.objects.create(
            name="Test Deal",
            primary_person=self.person,
            owner=self.user,
        )
        self.activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_attachment_with_only_deal_succeeds(self):
        attachment = Attachment.objects.create(
            deal=self.deal,
            file=ContentFile(b"dummy data", name="deal_file.txt"),
            original_filename="deal_file.txt",
            uploaded_by=self.user,
        )
        self.assertIsNotNone(attachment.pk)
        self.assertEqual(attachment.deal, self.deal)
        self.assertIsNone(attachment.activity)

    def test_attachment_with_only_activity_succeeds(self):
        attachment = Attachment.objects.create(
            activity=self.activity,
            file=ContentFile(b"dummy data", name="activity_file.txt"),
            original_filename="activity_file.txt",
            uploaded_by=self.user,
        )
        self.assertIsNotNone(attachment.pk)
        self.assertEqual(attachment.activity, self.activity)
        self.assertIsNone(attachment.deal)

    def test_deal_and_activity_exclusive_constraint_both_set(self):
        with self.assertRaises(IntegrityError):
            Attachment.objects.create(
                deal=self.deal,
                activity=self.activity,
                file=ContentFile(b"dummy data", name="both.txt"),
                original_filename="both.txt",
            )

    def test_deal_and_activity_exclusive_constraint_neither_set(self):
        with self.assertRaises(IntegrityError):
            Attachment.objects.create(
                deal=None,
                activity=None,
                file=ContentFile(b"dummy data", name="none.txt"),
                original_filename="none.txt",
            )


class AttachmentStorageTests(TestCase):
    """callable ストレージ検証（仕様書 §4.4.1）。"""

    def test_file_storage_location(self):
        file_field = Attachment._meta.get_field("file")
        storage = file_field.storage
        self.assertEqual(
            os.path.normpath(storage.location),
            os.path.normpath(str(settings.PROTECTED_MEDIA_ROOT)),
        )

    def tearDown(self):
        super().tearDown()


class AttachmentDeletionSignalTests(TransactionTestCase):
    """Attachment 実ファイル削除シグナル検証（仕様書 §4.4.2、§0.19 確定事項）。"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(PROTECTED_MEDIA_ROOT=self.temp_dir)
        self.settings_override.enable()

        self.user = User.objects.create_user(username="testuser_sig", password="password")
        self.person = Person.objects.create()
        self.deal = Deal.objects.create(
            name="Test Deal Signal",
            primary_person=self.person,
            owner=self.user,
        )
        self.activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_deleted_on_attachment_delete(self):
        attachment = Attachment.objects.create(
            deal=self.deal,
            file=ContentFile(b"content for delete", name="to_delete.txt"),
            original_filename="to_delete.txt",
            uploaded_by=self.user,
        )
        storage = attachment.file.storage
        file_path = attachment.file.name
        self.assertTrue(storage.exists(file_path))

        # 削除実行
        attachment.delete()
        self.assertFalse(storage.exists(file_path))

    def test_file_not_deleted_if_transaction_rolled_back(self):
        file_path = None
        storage = None
        try:
            with transaction.atomic():
                attachment = Attachment.objects.create(
                    deal=self.deal,
                    file=ContentFile(b"content for rollback", name="rollback.txt"),
                    original_filename="rollback.txt",
                    uploaded_by=self.user,
                )
                storage = attachment.file.storage
                file_path = attachment.file.name
                self.assertTrue(storage.exists(file_path))
                attachment.delete()
                # ロールバックを意図的に発生
                raise RuntimeError("Simulated rollback")
        except RuntimeError:
            pass

        # ロールバックされたため、on_commit は発火せずファイルは残る
        self.assertTrue(storage.exists(file_path))

    def test_file_deleted_on_cascade_delete_from_deal(self):
        attachment = Attachment.objects.create(
            deal=self.deal,
            file=ContentFile(b"content cascade deal", name="cascade_deal.txt"),
            original_filename="cascade_deal.txt",
            uploaded_by=self.user,
        )
        storage = attachment.file.storage
        file_path = attachment.file.name
        self.assertTrue(storage.exists(file_path))

        # 親 Deal を削除（CASCADE で Attachment も削除）
        self.deal.delete()
        self.assertFalse(storage.exists(file_path))

    def test_file_deleted_on_cascade_delete_from_activity(self):
        attachment = Attachment.objects.create(
            activity=self.activity,
            file=ContentFile(b"content cascade activity", name="cascade_act.txt"),
            original_filename="cascade_act.txt",
            uploaded_by=self.user,
        )
        storage = attachment.file.storage
        file_path = attachment.file.name
        self.assertTrue(storage.exists(file_path))

        # 親 Activity を削除（CASCADE で Attachment も削除）
        self.activity.delete()
        self.assertFalse(storage.exists(file_path))


class AttachmentAdminTests(TestCase):
    """AttachmentAdmin の保護制御検証（仕様書 §7.6.1）。"""

    def setUp(self):
        self.site = AdminSite()
        self.admin = AttachmentAdmin(Attachment, self.site)
        self.user = User.objects.create_user(username="admin_user", is_staff=True)

    def test_has_delete_permission_is_false(self):
        # request や obj が渡されても常に False
        self.assertFalse(self.admin.has_delete_permission(request=None, obj=None))

    def test_autocomplete_fields(self):
        self.assertIn("deal", self.admin.autocomplete_fields)
        self.assertIn("activity", self.admin.autocomplete_fields)
        self.assertIn("uploaded_by", self.admin.autocomplete_fields)


class AttachmentServiceTests(TransactionTestCase):
    """attachments/services.py および permissions.py の検証（仕様書 §4.4.2, §7.4）。"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(PROTECTED_MEDIA_ROOT=self.temp_dir)
        self.settings_override.enable()

        self.owner = User.objects.create_user(username="deal_owner", password="password")
        self.attendee = User.objects.create_user(username="deal_attendee", password="password")
        self.uploader = User.objects.create_user(username="deal_uploader", password="password")
        self.outsider = User.objects.create_user(username="deal_outsider", password="password")
        self.privileged_user = User.objects.create_user(username="privileged_admin", password="password")

        from django.contrib.auth.models import Permission
        edit_all_deals_perm = Permission.objects.get(codename="edit_all_deals")
        self.privileged_user.user_permissions.add(edit_all_deals_perm)

        self.person = Person.objects.create()
        self.deal = Deal.objects.create(name="添付ファイルテスト案件", primary_person=self.person, owner=self.owner)
        from deals.models import DealUser, UserRole
        DealUser.objects.create(deal=self.deal, user=self.attendee, role=UserRole.SUPPORT)

        self.attachment = Attachment.objects.create(
            deal=self.deal,
            file=ContentFile(b"proposal PDF content", name="proposal.pdf"),
            original_filename="proposal.pdf",
            uploaded_by=self.uploader,
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_can_view_attachment(self):
        from attachments.permissions import can_view_attachment

        # 親 Deal の閲覧権限者に委譲される
        self.assertTrue(can_view_attachment(self.owner, self.attachment))
        self.assertTrue(can_view_attachment(self.attendee, self.attachment))
        self.assertFalse(can_view_attachment(self.outsider, self.attachment))

    def test_can_delete_attachment_permissions(self):
        from attachments.permissions import can_delete_attachment

        # アップロード者本人: 削除可
        self.assertTrue(can_delete_attachment(self.uploader, self.attachment))
        # 親 Deal の owner: 削除可
        self.assertTrue(can_delete_attachment(self.owner, self.attachment))
        # 特権保持者（edit_all_deals）: 削除可
        self.assertTrue(can_delete_attachment(self.privileged_user, self.attachment))
        # DealUser（同席者・関係者）: 削除不可（仕様書 §7.4 厳守）
        self.assertFalse(can_delete_attachment(self.attendee, self.attachment))
        # 部外者: 削除不可
        self.assertFalse(can_delete_attachment(self.outsider, self.attachment))

    def test_delete_attachment_service(self):
        from actionlogs.models import ActionLog
        from attachments.services import delete_attachment

        storage = self.attachment.file.storage
        file_path = self.attachment.file.name
        self.assertTrue(storage.exists(file_path))

        att_id = self.attachment.id
        delete_attachment(self.attachment, user=self.owner)

        # DB 上の物理削除
        self.assertFalse(Attachment.objects.filter(id=att_id).exists())

        # 実ファイル削除（post_delete シグナル連携）
        self.assertFalse(storage.exists(file_path))

        # 親 Deal のコンテキスト情報を含む ActionLog が記録されていること
        log = ActionLog.objects.filter(action="attachment_deleted").latest("created_at")
        self.assertEqual(log.data["original_filename"], "proposal.pdf")
        self.assertEqual(log.data["target_type"], "deal")
        self.assertEqual(log.data["target_id"], str(self.deal.id))
        self.assertEqual(log.data["target_name"], "添付ファイルテスト案件")


class AttachmentViewTests(TestCase):
    def setUp(self):
        os.makedirs(settings.PROTECTED_MEDIA_ROOT, exist_ok=True)
        self.owner = User.objects.create_user(username="att_owner", password="password")
        self.attendee = User.objects.create_user(username="att_attendee", password="password")
        self.outsider = User.objects.create_user(username="att_outsider", password="password")

        from django.contrib.auth.models import Permission
        for u in (self.owner, self.attendee):
            u.user_permissions.add(
                Permission.objects.get(codename="add_attachment"),
                Permission.objects.get(codename="change_attachment"),
                Permission.objects.get(codename="delete_attachment"),
                Permission.objects.get(codename="view_attachment"),
                Permission.objects.get(codename="change_deal"),
                Permission.objects.get(codename="add_deal"),
            )

        self.person = Person.objects.create()
        self.deal = Deal.objects.create(name="ViewTest案件", primary_person=self.person, owner=self.owner)
        from deals.models import DealUser, UserRole
        DealUser.objects.create(deal=self.deal, user=self.attendee, role=UserRole.SUPPORT)

        self.attachment = Attachment.objects.create(
            deal=self.deal,
            file=ContentFile(b"Test content for download", name="提案書_テスト.pdf"),
            original_filename="提案書_テスト.pdf",
            uploaded_by=self.owner,
        )

    def tearDown(self):
        super().tearDown()
        try:
            if self.attachment.file and self.attachment.file.storage.exists(self.attachment.file.name):
                self.attachment.file.delete(save=False)
        except Exception:
            pass

    def test_protected_file_download_view(self):
        url = reverse("attachments:attachment_download", kwargs={"pk": self.attachment.pk})

        # 未ログインはログインへ
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

        # 部外者は403
        self.client.login(username="att_outsider", password="password")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

        # 案件オーナーはダウンロード成功＆RFC 5987ヘッダー検証
        self.client.login(username="att_owner", password="password")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("filename*=UTF-8''", resp.headers.get("Content-Disposition", ""))
        resp.close()

    def test_attachment_upload_view(self):
        self.client.login(username="att_owner", password="password")
        url = reverse("attachments:attachment_upload")

        # 正常アップロード
        test_file = SimpleUploadedFile("spec.txt", b"spec details", content_type="text/plain")
        resp = self.client.post(
            url,
            {
                "deal_id": str(self.deal.id),
                "file": test_file,
                "memo": "仕様書メモ",
            },
        )
        self.assertEqual(resp.status_code, 302)
        new_att = Attachment.objects.filter(deal=self.deal, original_filename="spec.txt").first()
        self.assertIsNotNone(new_att)
        self.assertEqual(new_att.memo, "仕様書メモ")

        # 危険な拡張子（.exe）の拒否
        bad_file = SimpleUploadedFile("virus.exe", b"binary data", content_type="application/octet-stream")
        resp_bad = self.client.post(
            url,
            {
                "deal_id": str(self.deal.id),
                "file": bad_file,
            },
        )
        self.assertEqual(resp_bad.status_code, 302)
        self.assertFalse(Attachment.objects.filter(deal=self.deal, original_filename="virus.exe").exists())

    def test_attachment_multiple_upload_view(self):
        """複数ファイルの一括アップロードと共通メモ適用を検証。"""
        self.client.login(username="att_owner", password="password")
        url = reverse("attachments:attachment_upload")

        f1 = SimpleUploadedFile("file1.pdf", b"content 1", content_type="application/pdf")
        f2 = SimpleUploadedFile("file2.png", b"content 2", content_type="image/png")
        f3 = SimpleUploadedFile("file3.txt", b"content 3", content_type="text/plain")

        resp = self.client.post(
            url,
            {
                "deal_id": str(self.deal.id),
                "files": [f1, f2, f3],
                "memo": "一括共通メモ",
            },
        )
        self.assertEqual(resp.status_code, 302)

        atts = Attachment.objects.filter(deal=self.deal, memo="一括共通メモ").order_by("original_filename")
        self.assertEqual(atts.count(), 3)
        filenames = [att.original_filename for att in atts]
        self.assertEqual(filenames, ["file1.pdf", "file2.png", "file3.txt"])
        for att in atts:
            self.assertEqual(att.uploaded_by, self.owner)
            self.assertEqual(att.memo, "一括共通メモ")

    def test_attachment_multiple_upload_size_limit_error(self):
        """15MB超過ファイルが混ざっていた場合、エラーとなり1件も保存されないことを検証。"""
        self.client.login(username="att_owner", password="password")
        url = reverse("attachments:attachment_upload")

        good_file = SimpleUploadedFile("ok.pdf", b"ok content", content_type="application/pdf")
        # 16MBのダミーファイル
        large_content = b"x" * (16 * 1024 * 1024)
        large_file = SimpleUploadedFile("too_large.pdf", large_content, content_type="application/pdf")

        resp = self.client.post(
            url,
            {
                "deal_id": str(self.deal.id),
                "files": [good_file, large_file],
                "memo": "失敗テストメモ",
            },
        )
        self.assertEqual(resp.status_code, 302)
        # 1件も保存されていないこと
        self.assertFalse(Attachment.objects.filter(original_filename="ok.pdf").exists())
        self.assertFalse(Attachment.objects.filter(original_filename="too_large.pdf").exists())


    def test_attachment_delete_view_permissions(self):
        # 同席者は削除権限なし（403）
        self.client.login(username="att_attendee", password="password")
        url = reverse("attachments:attachment_delete", kwargs={"pk": self.attachment.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)

        # オーナーは削除成功
        self.client.login(username="att_owner", password="password")
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Attachment.objects.filter(pk=self.attachment.pk).exists())

    def test_attachment_memo_update_view(self):
        self.client.login(username="att_owner", password="password")
        url = reverse("attachments:attachment_update_memo", kwargs={"pk": self.attachment.pk})
        resp = self.client.post(url, {"memo": "更新されたメモ"})
        self.assertEqual(resp.status_code, 302)
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.memo, "更新されたメモ")

    def test_attachment_memo_update_ajax(self):
        """AJAXリクエストによるメモ更新でJSONレスポンスが返ることを検証。"""
        self.client.login(username="att_owner", password="password")
        url = reverse("attachments:attachment_update_memo", kwargs={"pk": self.attachment.pk})
        resp = self.client.post(
            url,
            {"memo": "AJAXで更新されたメモ"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("memo"), "AJAXで更新されたメモ")

        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.memo, "AJAXで更新されたメモ")


