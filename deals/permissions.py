from django.db import models

from deals.models import Deal


def can_view_deal(user, deal: Deal) -> bool:
    """案件の閲覧権限判定（仕様書 §7.1 / AccessList設計方針 v1.6 §8.1）。"""
    if not user or not user.is_authenticated:
        return False
    if user.has_perm("deals.view_all_deals") or user.is_superuser:
        return True
    if deal.owner_id == user.id:
        return True
    if deal.deal_users.filter(user=user).exists():
        return True
    from permissions.services import AccessListService
    if deal.deal_list_id in AccessListService.accessible_deal_list_ids(user):
        return True
    return False


def can_edit_deal(user, deal: Deal) -> bool:
    """案件の編集権限判定（仕様書 §7.1 / AccessList設計方針 v1.6 §5.3, §6.2）。change_deal 権限を AND 条件に含む。"""
    if not user or not user.is_authenticated:
        return False
    if user.has_perm("deals.edit_all_deals"):
        return True
    if not user.has_perm("deals.change_deal"):
        return False
    if deal.deal_users.filter(user=user, can_edit=False).exists():
        return False
    from permissions.services import AccessListService
    return AccessListService.can_edit_deal(user, deal)


def can_approve_deal(user, deal: Deal) -> bool:
    """案件の承認権限判定（DealUser.role == 'approver' または edit_all_deals 保持者）。"""
    if user.has_perm("deals.edit_all_deals"):
        return True
    if not user.has_perm("deals.change_deal"):
        return False
    from deals.models import UserRole
    return deal.deal_users.filter(user=user, role=UserRole.APPROVER).exists()


def can_archive_deal(user, deal: Deal) -> bool:
    """案件のアーカイブ権限判定（仕様書 §7.1）。DealUser は不可、owner 本人か edit_all_deals 保持者のみ。"""
    if user.has_perm("deals.edit_all_deals"):
        return True
    if not user.has_perm("deals.change_deal"):
        return False
    return deal.owner_id == user.id


def can_reassign_deal_owner(user, deal: Deal) -> bool:
    """案件担当者（owner）付け替えの判定（仕様書 §2.6）。"""
    if user.has_perm("deals.edit_all_deals"):
        return True
    if not user.has_perm("deals.change_deal"):
        return False
    return deal.owner_id == user.id


def can_reassign_deal_primary_person(user, deal: Deal) -> bool:
    """主担当パーソン（primary_person）付け替えの判定（仕様書 §2.6.1）。"""
    if user.has_perm("deals.edit_all_deals"):
        return True
    if not user.has_perm("deals.change_deal"):
        return False
    return deal.owner_id == user.id


def visible_deals_for(user):
    """ユーザーが閲覧可能な案件 QuerySet を返す（仕様書 §7.1 / AccessList設計方針 v1.6 §8.1）。"""
    return Deal.objects.visible_for(user)
