import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _


class AccessList(models.Model):
    """アクセスリスト（AccessList設計方針 v1.6 §4.1）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        permissions = [
            ("view_all_access_lists", "全てのアクセスリストを閲覧できる"),
        ]

    def __str__(self):
        return self.name


class ACLEntry(models.Model):
    """アクセスリスト内エントリ（AccessList設計方針 v1.6 §4.2）。"""

    class PermissionLevel(models.TextChoices):
        ADMIN = "admin", _("管理者")
        EDITOR = "editor", _("編集者")
        VIEWER = "viewer", _("閲覧者")
        NONE = "none", _("未設定")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_list = models.ForeignKey(
        AccessList,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    order = models.PositiveIntegerField()
    permission_level = models.CharField(
        max_length=20,
        choices=PermissionLevel.choices,
    )
    target_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
    )
    target_object_id = models.CharField(max_length=64)
    target = GenericForeignKey("target_content_type", "target_object_id")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "created_at", "id"]
        indexes = [
            models.Index(fields=["target_content_type", "target_object_id"]),
        ]

    def __str__(self):
        return f"{self.access_list.name} #{self.order} ({self.get_permission_level_display()})"


class AccessListUserRole(models.Model):
    """フラット化キャッシュ（AccessList設計方針 v1.6 §4.3）。"""

    class Role(models.TextChoices):
        ADMIN = "admin", _("管理者")
        EDITOR = "editor", _("編集者")
        VIEWER = "viewer", _("閲覧者")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_list = models.ForeignKey(
        AccessList,
        on_delete=models.CASCADE,
        related_name="user_roles",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_list_user_roles",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["access_list", "user"],
                name="unique_access_list_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "access_list"]),
        ]
        ordering = ["access_list", "user"]

    def __str__(self):
        return f"{self.access_list.name} - {self.user} ({self.get_role_display()})"
