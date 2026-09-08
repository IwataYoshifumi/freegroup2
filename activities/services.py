from django.db import transaction

from actionlogs.models import ActionLog
from activities.models import Activity
from activities.permissions import (
    can_archive_activity,
    can_edit_activity,
    can_view_activity,
    visible_activities_for,
)
from persons.models import Person


def get_unfollowed_campaign_persons(campaign):
    """クリックしたが、まだこのキャンペーンに紐づく有効なActivityがない Person 一覧を返す（仕様書 §3.5.2）。"""
    clicked_persons = Person.objects.filter(
        tracking_links__campaign=campaign,
        tracking_links__click_logs__is_valid_click=True,
    ).select_related("merged_into").distinct()

    followed_persons = Person.objects.filter(
        activity_persons__activity__campaign=campaign,
        activity_persons__activity__is_archived=False,
    ).select_related("merged_into").distinct()

    followed_surviving_ids = {p.get_surviving_person().id for p in followed_persons}

    surviving_clicker_ids = {
        p.get_surviving_person().id for p in clicked_persons
    } - followed_surviving_ids

    return Person.objects.filter(
        id__in=surviving_clicker_ids
    ).select_related("primary_contact")


get_unfollowed_clickers = get_unfollowed_campaign_persons


def archive_activity(activity: Activity, user) -> Activity:
    """活動記録をアーカイブする（仕様書 §7.3）。"""
    with transaction.atomic():
        activity.is_archived = True
        activity.save(update_fields=["is_archived", "updated_at"])

        ActionLog.record(
            user=user,
            action="activity_archived",
            content_object=activity,
            object_repr=str(activity),
            data={"activity_id": str(activity.id)},
        )
        return activity
