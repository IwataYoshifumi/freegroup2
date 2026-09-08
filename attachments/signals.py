from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Attachment


@receiver(post_delete, sender=Attachment)
def delete_attachment_file(sender, instance, **kwargs):
    """Attachmentレコード削除時に実ファイルを削除する（仕様書 v1.5 §4.4.2）。"""
    if not instance.file:
        return
    file_storage = instance.file.storage
    file_name = instance.file.name

    def on_commit_delete():
        if file_name and file_storage.exists(file_name):
            file_storage.delete(file_name)

    transaction.on_commit(on_commit_delete)
