from django.contrib import admin

from .models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("id",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if request and hasattr(request, "user") and not (request.user.is_superuser or request.user.has_perm("persons.change_personlist")):
            readonly.append("person_list")
        return readonly
