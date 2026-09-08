from django.contrib import admin

from .models import Activity, ActivityPerson, ActivityUser


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("activity_type", "direction", "occurred_at", "user", "deal", "campaign", "is_archived")
    list_filter = ("activity_type", "direction", "is_archived")
    search_fields = ("subject", "memo")
    autocomplete_fields = ("deal", "user", "created_by")

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["is_archived"]


@admin.register(ActivityPerson)
class ActivityPersonAdmin(admin.ModelAdmin):
    list_display = ("activity", "person", "role", "created_at")
    list_filter = ("role",)
    autocomplete_fields = ("activity", "person")


@admin.register(ActivityUser)
class ActivityUserAdmin(admin.ModelAdmin):
    list_display = ("activity", "user", "role", "created_at")
    list_filter = ("role",)
    autocomplete_fields = ("activity", "user")
