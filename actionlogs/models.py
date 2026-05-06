"""業務イベントの汎用ログ（仕様書 v1.4.2 §4.10）。

ActionLog はマージ実行・別人判定・cron 実行・OCR 処理結果等を記録する汎用ログ。
GenericForeignKey で全モデル横断の参照を持つため、独立アプリ（actionlogs）に配置する
（A-1c 指示書 §7.2 のたんたん判断）。
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ActionLog(models.Model):
    """アクションログDB（仕様書 v1.4.2 §4.10）。

    action は TextChoices ではなく自由文字列（別表 C.12 は参考値）。
    システム実行（cron 等）では user / content_type / object_id が NULL になる。
    不変履歴ログのため updated_at は持たない（仕様書 §4.10）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    action = models.CharField(max_length=50)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    object_id = models.CharField(max_length=255, null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    object_repr = models.CharField(max_length=255, blank=True, default="")
    diff = models.JSONField(null=True, blank=True)
    extra = models.JSONField(default=dict)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} {self.object_repr} ({self.created_at:%Y-%m-%d %H:%M})"
