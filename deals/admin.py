from django.contrib import admin

from .models import Deal, DealPerson, DealUser


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("name", "primary_person", "company", "owner", "stage", "amount", "is_archived")
    list_filter = ("stage", "is_archived", "deal_type", "lead_source")
    search_fields = ("name",)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["owner", "primary_person", "is_archived"]


@admin.register(DealPerson)
class DealPersonAdmin(admin.ModelAdmin):
    list_display = ("deal", "person", "role", "created_at")
    list_filter = ("role",)


@admin.register(DealUser)
class DealUserAdmin(admin.ModelAdmin):
    list_display = ("deal", "user", "role", "created_at")
    list_filter = ("role",)
