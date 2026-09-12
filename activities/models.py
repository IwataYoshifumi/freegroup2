import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from deals.models import PersonRole, UserRole


class ActivityType(models.TextChoices):
    """活動種別（仕様書 v1.5 §2.9.3）。"""

    PHONE = "phone", _("電話")
    VISIT = "visit", _("訪問")
    EMAIL = "email", _("メール")
    WEB_MEETING = "web_meeting", _("Web会議")
    OTHER = "other", _("その他")


class Direction(models.TextChoices):
    """方向（仕様書 v1.5 §2.9.3）。"""

    OUTGOING = "outgoing", _("発信")
    INCOMING = "incoming", _("受信")


class Activity(models.Model):
    """活動記録（仕様書 v1.5 第3章）。"""

    ActivityType = ActivityType
    Direction = Direction

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deal = models.ForeignKey(
        "deals.Deal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    campaign = models.ForeignKey(
        "mailings.Campaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    activity_type = models.CharField(max_length=30, choices=ActivityType.choices)
    direction = models.CharField(
        max_length=10, choices=Direction.choices, blank=True, default=""
    )
    occurred_at = models.DateTimeField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities_performed",
        verbose_name="実施者",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    place = models.CharField(max_length=255, blank=True, default="")
    memo = models.TextField(blank=True, default="")
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name = "活動記録"
        verbose_name_plural = "活動記録"
        indexes = [
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["deal", "occurred_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(deal__isnull=True, campaign__isnull=True)
                    | models.Q(deal__isnull=False, campaign__isnull=True)
                    | models.Q(deal__isnull=True, campaign__isnull=False)
                ),
                name="activity_at_most_one_target",
            ),
        ]
        permissions = [
            ("view_all_activities", "Can view all activities"),
            ("edit_all_activities", "Can edit all activities"),
        ]

    def clean(self):
        super().clean()
        if self.occurred_at and self.occurred_at > timezone.now():
            raise ValidationError({"occurred_at": "実施日時に未来の日時は指定できません。"})

    def has_view_permission(self, user):
        from activities.permissions import can_view_activity

        return can_view_activity(user, self)

    def __str__(self):
        date_str = timezone.localtime(self.occurred_at).strftime("%Y-%m-%d") if self.occurred_at else ""
        return f"{date_str} {self.get_activity_type_display()}"


class ActivityPerson(models.Model):
    """活動関係者（社外）（仕様書 v1.5 第3章 / 第5章）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(
        "activities.Activity",
        on_delete=models.CASCADE,
        related_name="activity_persons",
    )
    person = models.ForeignKey(
        "persons.Person",
        on_delete=models.CASCADE,
        related_name="activity_persons",
    )
    role = models.CharField(max_length=30, choices=PersonRole.choices)
    memo = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "活動関係者（社外）"
        verbose_name_plural = "活動関係者（社外）"
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "person"],
                name="unique_activity_person",
            ),
        ]

    def __str__(self):
        return f"{self.activity} - {self.person} ({self.get_role_display()})"


class ActivityUser(models.Model):
    """活動同席者（社内）（仕様書 v1.5 第3章 / 第5章）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(
        "activities.Activity",
        on_delete=models.CASCADE,
        related_name="activity_users",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="activity_users",
    )
    role = models.CharField(max_length=30, choices=UserRole.choices)
    memo = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "活動同席者（社内）"
        verbose_name_plural = "活動同席者（社内）"
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "user"],
                name="unique_activity_user",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.activity_id
            and self.user_id
            and self.activity.user_id == self.user_id
        ):
            raise ValidationError(
                "実施者と同じUserをActivityUserに登録することはできません。"
            )

    def __str__(self):
        return f"{self.activity} - {self.user} ({self.get_role_display()})"
