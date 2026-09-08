from django.db import transaction

from actionlogs.models import ActionLog
from attachments.models import Attachment
from attachments.permissions import (
    can_delete_attachment,
    can_edit_attachment,
    can_view_attachment,
)


def delete_attachment(attachment: Attachment, user):
    """添付ファイルを物理削除する（仕様書 §4.4.2）。実ファイル削除は post_delete シグナルに委譲。"""
    if attachment.deal_id:
        target_type, target_id = "deal", str(attachment.deal_id)
        target_name = attachment.deal.name
    else:
        target_type, target_id = "activity", str(attachment.activity_id)
        target_name = str(attachment.activity)

    with transaction.atomic():
        ActionLog.record(
            user=user,
            action="attachment_deleted",
            content_object=attachment,
            object_repr=attachment.original_filename,
            data={
                "original_filename": attachment.original_filename,
                "target_type": target_type,
                "target_id": target_id,
                "target_name": target_name,
            },
        )
        attachment.delete()
