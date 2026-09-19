from django.contrib import admin

from .models import Deal, DealPerson, DealUser


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("name", "primary_person", "company", "owner", "stage", "amount", "is_archived")
    list_filter = ("stage", "is_archived", "deal_type", "lead_source")
    search_fields = ("name",)
    autocomplete_fields = (
        "primary_person",
        "company",
        "owner",
        "created_by",
        "updated_by",
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = []
        if obj is not None:
            readonly = ["owner", "primary_person", "is_archived"]
        if request and hasattr(request, "user") and not (request.user.is_superuser or request.user.has_perm("deals.change_deallist")):
            readonly.append("deal_list")
        return readonly


@admin.register(DealPerson)
class DealPersonAdmin(admin.ModelAdmin):
    list_display = ("deal", "person", "role", "created_at")
    list_filter = ("role",)
    autocomplete_fields = ("deal", "person")


@admin.register(DealUser)
class DealUserAdmin(admin.ModelAdmin):
    list_display = ("deal", "user", "role", "created_at")
    list_filter = ("role",)
    autocomplete_fields = ("deal", "user")
