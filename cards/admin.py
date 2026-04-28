from django.contrib import admin

from .models import (
    BusinessCard,
    Contact,
    ContactFieldConfidence,
    OriginalImage,
    Person,
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
        "email",
    )
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ContactFieldConfidence)
class ContactFieldConfidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "contact", "field_name", "confidence", "confirmed_at", "created_at")
    list_filter = ("confidence", "confirmed_at", "created_at")
    search_fields = ("id", "field_name", "contact__full_name")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
