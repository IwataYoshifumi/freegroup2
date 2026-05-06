import uuid

from django.db import models


class Person(models.Model):
    """人物DB（仕様書 v1.1.0 §4.5）。v1.1.0 では最小構成（id / created_at / updated_at のみ）。

    一覧表示の代表値は person.contact_set.order_by('-created_at').first() から取得する。
    display_name / primary_contact_id 等は v2.0.0 で追加予定（v1.1.0 では追加しない）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        contact = self.contact_set.order_by("-created_at").first()
        if contact and contact.full_name:
            return contact.full_name
        return f"Person {self.id}"
