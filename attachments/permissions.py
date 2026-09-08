from activities.permissions import can_edit_activity, can_view_activity
from attachments.models import Attachment
from deals.permissions import can_edit_deal, can_view_deal


def can_view_attachment(user, attachment: Attachment) -> bool:
    """添付ファイルの閲覧権限判定（仕様書 §7.4）。親オブジェクトに委譲。"""
    if attachment.deal_id is not None:
        return can_view_deal(user, attachment.deal)
    return can_view_activity(user, attachment.activity)


def can_edit_attachment(user, attachment: Attachment) -> bool:
    """添付ファイル（メモ等）の編集権限判定（仕様書 §7.4）。親オブジェクトに委譲。"""
    if attachment.deal_id is not None:
        return can_edit_deal(user, attachment.deal)
    return can_edit_activity(user, attachment.activity)


def can_delete_attachment(user, attachment: Attachment) -> bool:
    """添付ファイルの削除権限判定（仕様書 §7.4）。アップロード者本人、親の所有者/作成者、edit_all_* 保持者のみ。"""
    if attachment.uploaded_by_id == user.id:
        return True
    if attachment.deal_id is not None:
        if user.has_perm("deals.edit_all_deals"):
            return True
        return attachment.deal.owner_id == user.id
    if user.has_perm("activities.edit_all_activities"):
        return True
    return (
        attachment.activity.user_id == user.id
        or attachment.activity.created_by_id == user.id
    )
