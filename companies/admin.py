from django.contrib import admin

from .models import Company, CompanyDuplicateCandidate


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("organization", "domain", "status", "created_at")
    search_fields = ("organization", "domain")
    list_filter = ("status",)
    autocomplete_fields = ("created_by", "merged_into")

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["status", "merged_into"]


@admin.register(CompanyDuplicateCandidate)
class CompanyDuplicateCandidateAdmin(admin.ModelAdmin):
    list_display = ("company_a", "company_b", "score", "rank", "review_status", "created_at")
    list_filter = ("rank", "review_status")
    readonly_fields = (
        "company_a",
        "company_b",
        "score",
        "rank",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False
