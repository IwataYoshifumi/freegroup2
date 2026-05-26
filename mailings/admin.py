from django.contrib import admin

from .models import EmailTemplate, MailingConfig, MailingList, MailingListMember


@admin.register(MailingList)
class MailingListAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "created_by",
        "members_frozen_at",
        "is_archived",
        "created_at",
    )
    list_filter = ("is_archived",)
    search_fields = ("name", "description")
    readonly_fields = ("members_frozen_at",)
    list_select_related = ("created_by",)


@admin.register(MailingListMember)
class MailingListMemberAdmin(admin.ModelAdmin):
    list_display = ("mailing_list", "person", "added_by", "created_at")
    list_filter = ("mailing_list",)
    search_fields = ("mailing_list__name",)
    list_select_related = ("mailing_list", "person", "added_by")
    autocomplete_fields = ("mailing_list", "person")
    readonly_fields = ("created_at",)


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "subject", "created_by", "is_archived", "updated_at")
    list_filter = ("is_archived",)
    search_fields = ("name", "subject")
    list_select_related = ("created_by",)


@admin.register(MailingConfig)
class MailingConfigAdmin(admin.ModelAdmin):
    """シングルトン (id=1) の admin。

    Phase 1 では編集画面（/mailings/config/）が主経路、admin は緊急時の閲覧・修正用。
    シングルトン制約により追加・削除は禁止する。
    """

    list_display = ("id", "company_name", "updated_at")
    readonly_fields = ("id", "updated_at")

    def has_add_permission(self, request):
        # シングルトン：レコード 0 件なら admin から追加可、1 件以上あれば追加禁止
        return not MailingConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
