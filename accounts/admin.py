from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group

from .models import CustomUser, Department, GroupProfile, LdapGroup, Role
from .services import apply_role


def _ldap_readonly_fields(model):
    """[性質] 純関数（モデルメタ参照のみ）／auth_source='ldap' 時に readonly にするフィールド名一覧。"""
    return [f.name for f in model._meta.fields if f.name != "id"]


class GroupProfileInline(admin.StackedInline):
    model = GroupProfile
    can_delete = False
    extra = 0

    def get_readonly_fields(self, request, obj=None):
        # obj は親 Group。新規作成中は obj is None（プロファイルもまだ無い）。
        # 既存 Group でも profile が無いケースは getattr で防御。
        profile = getattr(obj, "profile", None) if obj else None
        if profile and profile.auth_source == "ldap":
            return _ldap_readonly_fields(GroupProfile)
        return super().get_readonly_fields(request, obj)


class CustomGroupAdmin(GroupAdmin):
    inlines = [GroupProfileInline]


admin.site.unregister(Group)
admin.site.register(Group, CustomGroupAdmin)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "parent", "auth_source", "is_active")
    list_filter = ("auth_source", "is_active")
    search_fields = ("name", "code")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.auth_source == "ldap":
            return _ldap_readonly_fields(Department)
        return super().get_readonly_fields(request, obj)


@admin.register(LdapGroup)
class LdapGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "auth_source", "is_active")
    list_filter = ("auth_source", "is_active")
    search_fields = ("name",)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.auth_source == "ldap":
            return _ldap_readonly_fields(LdapGroup)
        return super().get_readonly_fields(request, obj)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "username",
        "email",
        "role",
        "department",
        "auth_source",
        "is_active",
        "last_login",
    )
    list_filter = ("auth_source", "role", "department", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name")
    list_select_related = ("role", "department")

    def get_fieldsets(self, request, obj=None):
        """user_permissions を fieldsets から動的に除外（仕様書 §4.1）。"""
        fieldsets = super().get_fieldsets(request, obj)
        cleaned = []
        for name, opts in fieldsets:
            if "fields" in opts and "user_permissions" in opts["fields"]:
                new_fields = tuple(f for f in opts["fields"] if f != "user_permissions")
                cleaned.append((name, {**opts, "fields": new_fields}))
            else:
                cleaned.append((name, opts))
        return tuple(cleaned)

    def get_form(self, request, obj=None, **kwargs):
        """user_permissions を form base_fields からも除外（URL 直叩き保護、仕様書 §4.1）。"""
        form = super().get_form(request, obj, **kwargs)
        form.base_fields.pop("user_permissions", None)
        return form

    def save_model(self, request, obj, form, change):
        """Role が変わった時のみ apply_role() を呼ぶガード（仕様書 §4.1 / §8）。"""
        old_role_id = None
        if change:
            old_role_id = (
                self.model.objects.filter(pk=obj.pk)
                .values_list("role_id", flat=True)
                .first()
            )
        super().save_model(request, obj, form, change)
        if not change or old_role_id != obj.role_id:
            apply_role(obj, obj.role)
