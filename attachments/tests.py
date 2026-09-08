import os
import shutil
import tempfile
from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
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
        if os.path.exists(settings.PROTECTED_MEDIA_ROOT):
            shutil.rmtree(settings.PROTECTED_MEDIA_ROOT, ignore_errors=True)


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
        if os.path.exists(settings.PROTECTED_MEDIA_ROOT):
            shutil.rmtree(settings.PROTECTED_MEDIA_ROOT, ignore_errors=True)

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

