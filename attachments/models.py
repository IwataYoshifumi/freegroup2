import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone


def get_protected_storage():
    """storageにインスタンスではなく関数を渡すことで、マイグレーションに
    環境依存の絶対パスが焼き込まれるのを防ぐ（実行時にsettingsから解決される）。"""
    return FileSystemStorage(location=settings.PROTECTED_MEDIA_ROOT)


def attachment_upload_to(instance, filename):
    """保存パスをUUID化する。元のファイル名は original_filename が保持する。

    ディスク上に元のファイル名・日本語・推測可能な名前を一切残さないため、
    拡張子のみ引き継いで UUID にリネームする。
    """
    ext = Path(filename).suffix.lower()
    return f"attachments/{timezone.now():%Y/%m}/{uuid.uuid4()}{ext}"


class Attachment(models.Model):
    """添付ファイル（仕様書 v1.5 第4章）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deal = models.ForeignKey(
        "deals.Deal",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
    )
    activity = models.ForeignKey(
        "activities.Activity",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
    )
    file = models.FileField(
        storage=get_protected_storage,
        upload_to=attachment_upload_to,
    )
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    memo = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "添付ファイル"
        verbose_name_plural = "添付ファイル"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(deal__isnull=False, activity__isnull=True)
                    | models.Q(deal__isnull=True, activity__isnull=False)
                ),
                name="attachment_exactly_one_target",
            ),
        ]

    def __str__(self):
        return self.original_filename
