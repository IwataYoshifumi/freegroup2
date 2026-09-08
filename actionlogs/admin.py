from django.contrib import admin

from .models import ActionLog


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "object_repr", "note")
    list_filter = ("action", "created_at")
    search_fields = ("note", "object_repr", "user__username", "user__email")
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "user",
        "action",
        "content_type",
        "object_id",
        "object_repr",
        "data",
        "note",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True
