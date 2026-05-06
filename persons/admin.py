from django.contrib import admin

from .models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("id",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
