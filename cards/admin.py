from django.contrib import admin

from .models import BusinessCard, Contact, OriginalImage, Person


@admin.register(OriginalImage)
class OriginalImageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "detected_count", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "user__username", "error_message")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(BusinessCard)
class BusinessCardAdmin(admin.ModelAdmin):
    list_display = ("id", "original_image", "ocr_status", "created_at", "updated_at")
    list_filter = ("ocr_status", "created_at")
    search_fields = ("id", "original_image__id", "error_message")
    readonly_fields = ("id", "raw_json", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("id",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "company", "department", "title", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "id",
        "full_name",
        "last_name",
        "first_name",
        "company",
        "department",
        "title",
    )
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
