from django.db import models

from activities.models import Activity
from deals.permissions import can_view_deal


def can_view_activity(user, activity: Activity) -> bool:
    """活動記録の閲覧権限判定（仕様書 §7.3）。"""
    if user.has_perm("activities.view_all_activities"):
        return True
    if activity.user_id == user.id:
        return True
    if activity.activity_users.filter(user=user).exists():
        return True
    if activity.deal_id is not None:
        return can_view_deal(user, activity.deal)
    if activity.campaign_id is not None and hasattr(activity.campaign, "has_view_permission"):
        return activity.campaign.has_view_permission(user)
    return False


def can_edit_activity(user, activity: Activity) -> bool:
    """活動記録の編集権限判定（仕様書 §7.3）。change_activity 権限を AND 条件に含む。"""
    if user.has_perm("activities.edit_all_activities"):
        return True
    if not user.has_perm("activities.change_activity"):
        return False
    if activity.user_id == user.id or activity.created_by_id == user.id:
        return True
    return activity.activity_users.filter(user=user).exists()


def can_archive_activity(user, activity: Activity) -> bool:
    """活動記録のアーカイブ権限判定（仕様書 §7.3）。ActivityUser は不可、実施者/作成者本人または edit_all_activities 保持者のみ。"""
    if user.has_perm("activities.edit_all_activities"):
        return True
    if not user.has_perm("activities.change_activity"):
        return False
    return activity.user_id == user.id or activity.created_by_id == user.id


def visible_activities_for(user):
    """ユーザーが閲覧可能な活動記録 QuerySet を返す（仕様書 §7.3）。"""
    if user.has_perm("activities.view_all_activities"):
        return Activity.objects.all()

    q = models.Q(user=user) | models.Q(activity_users__user=user)

    # Deal 経由の認可委譲
    if user.has_perm("deals.view_all_deals"):
        q |= models.Q(deal__isnull=False)
    else:
        q |= models.Q(deal__owner=user) | models.Q(deal__deal_users__user=user)

    # Campaign 経由の認可委譲
    if user.has_perm("mailings.view_all_campaigns"):
        q |= models.Q(campaign__isnull=False)
    else:
        q |= models.Q(campaign__created_by=user)

    return Activity.objects.filter(q).distinct()
