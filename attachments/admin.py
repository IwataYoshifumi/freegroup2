from django.contrib import admin

from .models import Attachment


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "deal", "activity", "uploaded_by", "created_at")
    search_fields = ("original_filename", "memo")

    def has_delete_permission(self, request, obj=None):
        return False
