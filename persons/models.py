import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class Person(models.Model):
    """人物DB（仕様書 v1.4.2 §4.5）。

    Person.primary_contact が代表 Contact の正本、Contact.status='primary' が派生情報
    （二重管理の設計趣旨は §4.5.2 参照）。
    """

    class Status(models.TextChoices):
        """Person のステータス（仕様書 §4.5.1 / 別表 C.11）。"""

        ACTIVE = "active", _("通常")
        MERGED = "merged", _("統合済み")
        ARCHIVED = "archived", _("アーカイブ")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    primary_contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_from_set",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        contact = self.contact_set.order_by("-created_at").first()
        if contact and contact.full_name:
            return contact.full_name
        return f"Person {self.id}"
