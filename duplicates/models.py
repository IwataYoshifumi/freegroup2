"""重複検出・人物統合の補助レコード（仕様書 v1.4.2 §4.7 / §4.8）。

DuplicateCandidate：重複候補ペアの DB レコード。
PersonMergeLog：マージ実行・復元の履歴ログ（status='undoable'/'undone'/'locked'）。
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils.translation import gettext_lazy as _


class DuplicateCandidate(models.Model):
    """重複候補DB（仕様書 v1.4.2 §4.7）。

    2 つの Person の組み合わせを記録する。person_a / person_b は ID 順で正規化される。
    review_status='pending' の場合のみ partial unique constraint が効く（再マージ可能のため）。
    match_reason / matched_fields は v1.4.2 で削除（A-1c 指示書 §7.1）。
    """

    class Rank(models.TextChoices):
        """ランク（仕様書 §14.4 / 別表 C.4）。"""

        EXACT_MATCH = "exact_match", _("完全一致")
        POSSIBLE_HIGH = "possible_high", _("高確信度")
        POSSIBLE_MID = "possible_mid", _("中確信度")
        POSSIBLE_LOW = "possible_low", _("低確信度")
        NONE = "none", _("該当なし")

    class ReviewStatus(models.TextChoices):
        """レビュー状態（仕様書 §14.4 / 別表 C.5）。"""

        PENDING = "pending", _("判定待ち")
        MERGED = "merged", _("マージ済み")
        DIFFERENT_PERSON = "different_person", _("別人確定")
        INVALIDATED = "invalidated", _("無効化")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group_id = models.UUIDField(null=True, blank=True)
    person_a = models.ForeignKey(
        "persons.Person",
        on_delete=models.CASCADE,
        related_name="+",
    )
    person_b = models.ForeignKey(
        "persons.Person",
        on_delete=models.CASCADE,
        related_name="+",
    )
    score = models.IntegerField()
    rank = models.CharField(max_length=20, choices=Rank.choices)
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    review_result = models.JSONField(default=list)
    note = models.TextField(blank=True, default="")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["person_a", "person_b"],
                condition=Q(review_status="pending"),
                name="unique_pending_person_pair",
            ),
        ]

    def __str__(self):
        return f"{self.person_a_id} ↔ {self.person_b_id} ({self.rank}/{self.review_status})"


class PersonMergeLog(models.Model):
    """マージ履歴DB（仕様書 v1.4.2 §4.8）。

    surviving_person / merged_person は PROTECT。マージログから過去状態を確実に追跡できるよう
    Person の物理削除をブロックする。
    """

    class Status(models.TextChoices):
        """ログ状態（仕様書 §14.4 / 別表 C.6）。"""

        UNDOABLE = "undoable", _("復元可能")
        UNDONE = "undone", _("復元済み")
        LOCKED = "locked", _("復元不可")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    surviving_person = models.ForeignKey(
        "persons.Person",
        on_delete=models.PROTECT,
        related_name="merge_logs_as_surviving",
    )
    merged_person = models.ForeignKey(
        "persons.Person",
        on_delete=models.PROTECT,
        related_name="merge_logs_as_merged",
    )
    duplicate_candidate = models.ForeignKey(
        DuplicateCandidate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNDOABLE,
    )
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    executed_at = models.DateTimeField()
    undone_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    undone_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.merged_person_id} → {self.surviving_person_id} ({self.status})"
