from django.contrib import admin

from .models import Tag, TagAssignment, TagCategory


@admin.register(TagCategory)
class TagCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_archived", "created_at")
    list_filter = ("is_archived",)
    search_fields = ("name", "description")
    ordering = ("sort_order", "name")


class TagAssignmentInline(admin.TabularInline):
    """TagAdmin から配下 TagAssignment を全ステータス（active / merged / archived）含めて
    一覧確認するためのインライン（Phase 1b-ε.1 追加修正、デバッグ用）。

    業務画面（TagDetailView）は active 固定のため、マージ・アーカイブ後の TagAssignment
    残存状況（§7.3 手順 3 / §11.7.1）はここで確認する。
    """

    model = TagAssignment
    extra = 0
    fields = ("person", "person_status", "assigned_by", "created_at")
    readonly_fields = ("person", "person_status", "assigned_by", "created_at")
    ordering = ("-created_at",)
    can_delete = False

    def person_status(self, obj):
        return obj.person.status if obj.person_id else "-"

    person_status.short_description = "Person status"

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("person", "assigned_by")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "created_by", "is_archived", "created_at")
    list_filter = ("category", "is_archived")
    search_fields = ("name", "description")
    list_select_related = ("category", "created_by")
    autocomplete_fields = ("category",)
    inlines = [TagAssignmentInline]


@admin.register(TagAssignment)
class TagAssignmentAdmin(admin.ModelAdmin):
    list_display = ("tag", "person", "person_status", "assigned_by", "created_at")
    list_filter = ("tag__category", "tag")
    search_fields = ("tag__name",)
    list_select_related = ("tag", "person", "assigned_by")
    autocomplete_fields = ("tag", "person")
    readonly_fields = ("created_at",)

    def person_status(self, obj):
        return obj.person.status if obj.person_id else "-"

    person_status.short_description = "Person status"
