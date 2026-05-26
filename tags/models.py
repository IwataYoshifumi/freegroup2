"""tags アプリのモデル（仕様書 v1.6 §4.9 / §4.9A / §4.10、第11章）。

rev12 で TagCategory を新設、Tag.category を必須 FK 化。
抽出ロジック「同一カテゴリ内 OR・カテゴリ間 AND」は §11.1.3 / §11.2.3、
Phase 1b でサービス関数として実装。
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import UniqueConstraint

from persons.models import Person


class TagCategory(models.Model):
    """タグカテゴリ（仕様書 §4.9）。

    タグをグループ化する分類軸（業種・役職・決裁権限・展示会名等）。
    rev12.2：初期データは投入しない（空スタート・運用者が手動作成）。
    抽出時の役割は §11.1.3：同一カテゴリ内 OR、カテゴリ間 AND。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")
    sort_order = models.IntegerField(default=0)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "タグカテゴリ"
        verbose_name_plural = "タグカテゴリ"

    def __str__(self):
        return self.name


class Tag(models.Model):
    """タグ（仕様書 §4.9A）。

    rev12 で group_id を削除し category 必須 FK に置き換え。Person に複数付与可能。
    リスト作成時のタグ抽出のみに使う（配信時には評価しない、§11.0.1）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey(
        TagCategory,
        on_delete=models.PROTECT,
        related_name="tags",
        help_text=(
            "タグカテゴリ（必須）。カテゴリ削除でタグが連鎖削除されないよう PROTECT。"
            "カテゴリは論理削除運用（§4.9A）"
        ),
    )
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__sort_order", "name"]
        verbose_name = "タグ"
        verbose_name_plural = "タグ"
        permissions = [
            ("assign_tag", "Person にタグを付与・解除できる"),
        ]

    def __str__(self):
        return self.name


class TagAssignment(models.Model):
    """タグ付与（仕様書 §4.10）。

    Tag × Person の中間テーブル。
    rev12.3 確定：Person マージ時は merged_person → surviving_person への単純コピー
    （§9.4.5、ユニット再帰は使わない）。Phase 1 ではサービス関数は実装しない、Phase 9 で対応。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="assignments")
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="tag_assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["tag", "person"], name="unique_tag_person"),
        ]
        verbose_name = "タグ付与"
        verbose_name_plural = "タグ付与"

    def __str__(self):
        return f"{self.tag} → {self.person}"
