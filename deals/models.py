import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Stage(models.TextChoices):
    """案件ステージ（仕様書 v1.5 §2.2 / §2.9）。"""

    INITIAL_MEETING = "initial_meeting", _("初回商談")
    NEEDS_ANALYSIS = "needs_analysis", _("ヒアリング・課題整理")
    QUOTATION = "quotation", _("見積提示")
    UNDER_REVIEW = "under_review", _("検討中")
    INTERNAL_APPROVAL = "internal_approval", _("社内稟議中")
    NEGOTIATION = "negotiation", _("交渉")
    WON = "won", _("受注")
    LOST = "lost", _("失注")


class DealType(models.TextChoices):
    """案件の性質（仕様書 v1.5 §2.9.1）。"""

    NEW = "new", _("新規開拓")
    EXPANSION = "expansion", _("既存深耕")
    RENEWAL = "renewal", _("更新・継続")


class LeadSource(models.TextChoices):
    """案件発生源（仕様書 v1.5 §2.9.2）。"""

    EXHIBITION = "exhibition", _("展示会・イベント")
    REFERRAL = "referral", _("紹介（既存客・取引先・知人）")
    INBOUND_WEB = "inbound_web", _("Webサイトからの問い合わせ")
    INBOUND_DIRECT = "inbound_direct", _("電話・メールでの直接問い合わせ")
    CAMPAIGN = "campaign", _("メールキャンペーン")
    EXISTING_CUSTOMER = "existing_customer", _("既存客からの相談・追加依頼")
    OUTBOUND = "outbound", _("自社からの営業（テレアポ・訪問・DM）")
    OTHER = "other", _("その他")


class PersonRole(models.TextChoices):
    """社外の相手方の役割（仕様書 v1.5 §2.9.4）。"""

    DECISION_MAKER = "decision_maker", _("決裁者")
    CONTACT_WINDOW = "contact_window", _("窓口")
    TECHNICAL = "technical", _("技術担当")
    ATTENDEE = "attendee", _("同席者")
    OTHER = "other", _("その他")


class UserRole(models.TextChoices):
    """社内の担当者の役割（仕様書 v1.5 §2.9.5）。"""

    PRIMARY = "primary", _("主担当")
    SUPPORT = "support", _("協力担当")
    APPROVER = "approver", _("上長承認者")
    OBSERVER = "observer", _("閲覧者")
    OTHER = "other", _("その他")


class Deal(models.Model):
    """案件（仕様書 v1.5 第2章）。"""

    Stage = Stage
    DealType = DealType
    LeadSource = LeadSource

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    primary_person = models.ForeignKey(
        "persons.Person",
        on_delete=models.PROTECT,
        related_name="deals_as_primary",
    )
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deals",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deals_owned",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    stage = models.CharField(
        max_length=30,
        choices=Stage.choices,
        default=Stage.INITIAL_MEETING,
    )
    probability = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    deal_type = models.CharField(
        max_length=30,
        choices=DealType.choices,
        blank=True,
        default="",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=0,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    expected_close_date = models.DateField(null=True, blank=True)
    closed_at = models.DateField(null=True, blank=True)
    lead_source = models.CharField(
        max_length=30,
        choices=LeadSource.choices,
        blank=True,
        default="",
    )
    source_campaign = models.ForeignKey(
        "mailings.Campaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_deals",
    )
    lost_reason = models.CharField(max_length=255, blank=True, default="")
    memo = models.TextField(blank=True, default="")
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "案件"
        verbose_name_plural = "案件"
        indexes = [
            models.Index(fields=["owner", "stage", "is_archived"]),
            models.Index(fields=["company", "is_archived"]),
        ]
        permissions = [
            ("view_all_deals", "Can view all deals"),
            ("edit_all_deals", "Can edit all deals"),
        ]

    def clean(self):
        super().clean()
        if self.stage == Stage.WON and not self.closed_at:
            raise ValidationError({"closed_at": "受注時は成約確定日が必須です。"})
        if self.stage == Stage.LOST:
            if not self.closed_at:
                raise ValidationError({"closed_at": "失注時は成約確定日が必須です。"})
            if not self.lost_reason:
                raise ValidationError({"lost_reason": "失注時は失注理由が必須です。"})
        if self.closed_at and self.closed_at > timezone.localdate():
            raise ValidationError({"closed_at": "成約確定日に未来日は指定できません。"})
        if self.source_campaign_id and self.lead_source != LeadSource.CAMPAIGN:
            raise ValidationError(
                {
                    "lead_source": "発生源キャンペーンを指定する場合、案件発生源は「メールキャンペーン」にしてください。"
                }
            )

    @property
    def expected_value(self):
        """予想売上（金額 × 確度%）。未入力時は None。"""
        if self.amount is None or self.probability is None:
            return None
        return int(self.amount * self.probability / 100)

    def has_view_permission(self, user):
        from deals.permissions import can_view_deal

        return can_view_deal(user, self)

    def __str__(self):
        return self.name


class DealPerson(models.Model):
    """案件関係者（社外）（仕様書 v1.5 第5章）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deal = models.ForeignKey(
        "deals.Deal",
        on_delete=models.CASCADE,
        related_name="deal_persons",
    )
    person = models.ForeignKey(
        "persons.Person",
        on_delete=models.CASCADE,
        related_name="deal_persons",
    )
    role = models.CharField(max_length=30, choices=PersonRole.choices)
    memo = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "案件関係者（社外）"
        verbose_name_plural = "案件関係者（社外）"
        constraints = [
            models.UniqueConstraint(
                fields=["deal", "person"],
                name="unique_deal_person",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.deal_id
            and self.person_id
            and self.deal.primary_person_id == self.person_id
        ):
            raise ValidationError(
                "主担当者（相手方）と同じPersonをDealPersonに登録することはできません。"
            )

    def __str__(self):
        return f"{self.deal.name} - {self.person} ({self.get_role_display()})"


class DealUser(models.Model):
    """案件担当者（社内）（仕様書 v1.5 第5章）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deal = models.ForeignKey(
        "deals.Deal",
        on_delete=models.CASCADE,
        related_name="deal_users",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="deal_users",
    )
    role = models.CharField(max_length=30, choices=UserRole.choices)
    memo = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "案件担当者（社内）"
        verbose_name_plural = "案件担当者（社内）"
        constraints = [
            models.UniqueConstraint(
                fields=["deal", "user"],
                name="unique_deal_user",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.deal_id
            and self.user_id
            and self.deal.owner_id == self.user_id
        ):
            raise ValidationError(
                "現在のownerと同じUserをDealUserに登録することはできません。"
            )

    def __str__(self):
        return f"{self.deal.name} - {self.user} ({self.get_role_display()})"
