from django.contrib import admin

from .models import (
    EmailTemplate,
    MailingConfig,
    MailingList,
    MailingListMember,
    SuppressedEmail,
    Unsubscribe,
)


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


@admin.register(Unsubscribe)
class UnsubscribeAdmin(admin.ModelAdmin):
    """配信拒否履歴 (Unsubscribe) の Admin 表示。

    [注意] 解除操作 (cancelled_at の更新) 等は本来サービス関数 (cancel_unsubscribe)
    経由が正となります。Admin からの直接修正は緊急時・デバッグ用途限定。
    """

    list_display = ("person", "source", "source_email", "cancelled_at", "created_at")
    list_filter = ("source", ("cancelled_at", admin.EmptyFieldListFilter))
    search_fields = ("source_email", "person__primary_contact__full_name")
    readonly_fields = ("id", "person", "source", "source_email", "created_at")
    list_select_related = ("person", "person__primary_contact")
    ordering = ("-created_at",)


@admin.register(SuppressedEmail)
class SuppressedEmailAdmin(admin.ModelAdmin):
    """メール不達先リスト (SuppressedEmail) の Admin 表示。

    [注意] 解除操作 (cancelled_at の更新) 等は本来サービス関数経由が正となります。
    Admin からの直接修正は緊急時・デバッグ用途限定。
    """

    list_display = ("email", "source", "bounce_reason", "cancelled_at", "created_at")
    list_filter = ("source", ("cancelled_at", admin.EmptyFieldListFilter))
    search_fields = ("email", "bounce_reason", "note")
    readonly_fields = ("id", "email", "source", "bounce_reason", "created_at")
    ordering = ("-created_at",)
