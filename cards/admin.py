from django.contrib import admin

from .models import (
    BusinessCard,
    OriginalImage,
)


@admin.register(OriginalImage)
class OriginalImageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "detected_count", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "user__username", "error_message")
    # v1.5.0: OriginalImage.raw_json は読み出し廃止（BC.raw_json_1/2 を参照）。
    # フィールド自体は物理残置されているが admin の表示からは外す。
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(BusinessCard)
class BusinessCardAdmin(admin.ModelAdmin):
    list_display = (
        "id", "original_image", "card_index", "ocr_status", "ocr_result",
        "card_image", "created_at", "updated_at",
    )
    list_filter = ("ocr_status", "ocr_result", "created_at")
    search_fields = ("id", "original_image__id", "error_message")
    readonly_fields = (
        "id", "raw_json_1", "raw_json_2", "ocr_status", "claimed_at",
        "error_message", "created_at", "updated_at",
    )
    ordering = ("-created_at",)
