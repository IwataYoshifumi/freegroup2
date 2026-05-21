from django.contrib import admin

from .models import Contact, ContactFieldConfidence


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "organization", "department", "title", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "id",
        "full_name",
        "last_name",
        "first_name",
        "organization",
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
