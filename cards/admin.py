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
    readonly_fields = ("id", "raw_json", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(BusinessCard)
class BusinessCardAdmin(admin.ModelAdmin):
    list_display = ("id", "original_image", "card_index", "card_image", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("id", "original_image__id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
