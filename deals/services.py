from django.db import transaction
from django.utils import timezone

from actionlogs.models import ActionLog
from deals.models import Deal
from deals.permissions import (
    can_archive_deal,
    can_edit_deal,
    can_reassign_deal_owner,
    can_reassign_deal_primary_person,
    can_view_deal,
    visible_deals_for,
)

DEAL_CLOSED_STAGE_VALUES = {Deal.Stage.WON, Deal.Stage.LOST}


def close_deal(deal: Deal, stage: str, user, closed_at=None, lost_reason="") -> Deal:
    """案件をクローズ（受注/失注）状態へ遷移させる（仕様書 §2.5）。"""
    if stage not in DEAL_CLOSED_STAGE_VALUES:
        raise ValueError("close_deal() は won/lost への遷移専用です。")

    if closed_at is None:
        closed_at = timezone.localdate()

    with transaction.atomic():
        old_stage = deal.stage
        deal.stage = stage
        deal.closed_at = closed_at
        deal.lost_reason = lost_reason if stage == Deal.Stage.LOST else ""
        deal.updated_by = user
        deal.clean()
        deal.save(update_fields=["stage", "closed_at", "lost_reason", "updated_by", "updated_at"])

        ActionLog.record(
            user=user,
            action="stage_changed",
            content_object=deal,
            object_repr=deal.name,
            data={"from_stage": old_stage, "to_stage": stage},
        )
        return deal


def reassign_deal_owner(deal: Deal, new_owner, user) -> Deal:
    """案件担当者（owner）を変更する（仕様書 §2.6）。"""
    with transaction.atomic():
        old_owner_id = deal.owner_id

        # new_owner が既存の DealUser なら重複防止のため削除
        deal.deal_users.filter(user=new_owner).delete()

        deal.owner = new_owner
        deal.updated_by = user
        deal.save(update_fields=["owner", "updated_by", "updated_at"])

        ActionLog.record(
            user=user,
            action="owner_changed",
            content_object=deal,
            object_repr=deal.name,
            data={
                "old_owner_id": str(old_owner_id) if old_owner_id else None,
                "new_owner_id": str(new_owner.id),
            },
        )
        return deal


def reassign_deal_primary_person(deal: Deal, new_person, user) -> Deal:
    """主担当パーソン（primary_person）を変更する（仕様書 §2.6.1）。"""
    with transaction.atomic():
        old_primary_person_id = deal.primary_person_id

        # new_person が既存の DealPerson なら重複防止のため削除
        deal.deal_persons.filter(person=new_person).delete()

        deal.primary_person = new_person
        deal.updated_by = user
        deal.save(update_fields=["primary_person", "updated_by", "updated_at"])

        ActionLog.record(
            user=user,
            action="primary_person_changed",
            content_object=deal,
            object_repr=deal.name,
            data={
                "old_primary_person_id": str(old_primary_person_id) if old_primary_person_id else None,
                "new_primary_person_id": str(new_person.id),
            },
        )
        return deal


def archive_deal(deal: Deal, user) -> Deal:
    """案件をアーカイブする（仕様書 §7.1）。"""
    with transaction.atomic():
        deal.is_archived = True
        deal.updated_by = user
        deal.save(update_fields=["is_archived", "updated_by", "updated_at"])

        ActionLog.record(
            user=user,
            action="deal_archived",
            content_object=deal,
            object_repr=deal.name,
            data={"deal_id": str(deal.id)},
        )
        return deal
