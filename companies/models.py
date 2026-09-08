import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Company(models.Model):
    """会社エンティティ（仕様書 v1.5 §6.2）。"""

    class Status(models.TextChoices):
        ACTIVE = "active", _("有効")
        MERGED = "merged", _("統合済み")
        ARCHIVED = "archived", _("アーカイブ")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.CharField(max_length=255, db_index=True)
    domain = models.CharField(max_length=255, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_companies",
    )
    phone = models.CharField(max_length=50, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")
    website = models.CharField(max_length=500, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization"]
        verbose_name = "会社"
        verbose_name_plural = "会社"
        permissions = [
            ("merge_company", "Can merge companies"),
        ]

    def __str__(self):
        return self.organization


class CompanyDuplicateCandidate(models.Model):
    """会社重複候補（仕様書 v1.5 §6.5.3）。"""

    class Rank(models.TextChoices):
        EXACT_MATCH = "exact_match", _("完全一致")
        POSSIBLE_HIGH = "possible_high", _("可能性高")
        POSSIBLE_MID = "possible_mid", _("可能性中")
        POSSIBLE_LOW = "possible_low", _("可能性低")

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", _("未処理")
        MERGED = "merged", _("統合済み")
        DIFFERENT_COMPANY = "different_company", _("別会社と判断")
        INVALIDATED = "invalidated", _("無効化")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_a = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="duplicate_candidates_as_a",
    )
    company_b = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="duplicate_candidates_as_b",
    )
    score = models.IntegerField()
    rank = models.CharField(max_length=20, choices=Rank.choices)
    review_status = models.CharField(
        max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
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
        ordering = ["-score", "-created_at"]
        verbose_name = "会社重複候補"
        verbose_name_plural = "会社重複候補"
        constraints = [
            models.UniqueConstraint(
                fields=["company_a", "company_b"],
                condition=models.Q(review_status="pending"),
                name="unique_pending_company_pair",
            ),
        ]

    def __str__(self):
        return f"{self.company_a_id} ↔ {self.company_b_id} ({self.get_rank_display()}/{self.get_review_status_display()})"
