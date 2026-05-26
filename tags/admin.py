from django.contrib import admin

from .models import Tag, TagAssignment, TagCategory


@admin.register(TagCategory)
class TagCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_archived", "created_at")
    list_filter = ("is_archived",)
    search_fields = ("name", "description")
    ordering = ("sort_order", "name")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "created_by", "is_archived", "created_at")
    list_filter = ("category", "is_archived")
    search_fields = ("name", "description")
    list_select_related = ("category", "created_by")
    autocomplete_fields = ("category",)


@admin.register(TagAssignment)
class TagAssignmentAdmin(admin.ModelAdmin):
    list_display = ("tag", "person", "assigned_by", "created_at")
    list_filter = ("tag__category", "tag")
    search_fields = ("tag__name",)
    list_select_related = ("tag", "person", "assigned_by")
    autocomplete_fields = ("tag", "person")
    readonly_fields = ("created_at",)
