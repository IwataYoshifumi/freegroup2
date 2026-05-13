from django.contrib import admin

from .models import DuplicateCandidate, PersonMergeLog


@admin.register(DuplicateCandidate)
class DuplicateCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "person_a",
        "person_b",
        "rank",
        "review_status",
        "group_id",
        "score",
        "created_at",
    )
    list_filter = ("rank", "review_status", "created_at")
    search_fields = ("id", "person_a__id", "person_b__id", "group_id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(PersonMergeLog)
class PersonMergeLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "surviving_person",
        "merged_person",
        "status",
        "executed_at",
        "created_at",
    )
    list_filter = ("status", "executed_at", "created_at")
    search_fields = (
        "id",
        "surviving_person__id",
        "merged_person__id",
    )
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
