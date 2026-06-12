from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group
from django.shortcuts import render

from .models import CustomUser, Department, GroupProfile, LdapGroup, Role
from .services import apply_role, retire_user


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
    actions = ["retire_user_action"]
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

    # 変更フォームに追加する CustomUser 独自フィールドのセクション（宿題S-(2)）。
    # role を選んで保存すると save_model() の apply_role() が発火する経路の UI 入口。
    # person は含めない（紐付けは Service 層 link_user_to_person 経由が正本。admin 直編集は
    # 両側ガード・ActionLog を迂回するため禁止、仕様書 §12）。
    ROLE_FIELDSET = (
        "ロール・所属（v1.5）",
        {"fields": ("role", "department", "auth_source")},
    )

    # 新規作成フォーム（add_fieldsets）にも role を出す。新規作成時に Role を選んで保存すると
    # save_model() の `not change` 分岐で apply_role() が発火し default_groups が展開される。
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("ロール設定", {"classes": ("wide",), "fields": ("role",)}),
    )

    def get_fieldsets(self, request, obj=None):
        """user_permissions を fieldsets から動的に除外しつつ、CustomUser 独自フィールド
        （role / department / auth_source）の編集セクションを追加する（仕様書 §4.1 / 宿題S-(2)）。

        新規作成（obj is None）では super() が add_fieldsets を返し、そこに role を含めて
        いるため ROLE_FIELDSET は追加しない（二重追加・department/auth_source の早期要求を回避）。
        """
        fieldsets = super().get_fieldsets(request, obj)
        cleaned = []
        for name, opts in fieldsets:
            if "fields" in opts and "user_permissions" in opts["fields"]:
                new_fields = tuple(f for f in opts["fields"] if f != "user_permissions")
                cleaned.append((name, {**opts, "fields": new_fields}))
            else:
                cleaned.append((name, opts))
        if obj is not None:
            cleaned.append(self.ROLE_FIELDSET)
        return tuple(cleaned)

    def get_form(self, request, obj=None, **kwargs):
        """user_permissions を form base_fields からも除外（URL 直叩き保護、仕様書 §4.1）。"""
        form = super().get_form(request, obj, **kwargs)
        form.base_fields.pop("user_permissions", None)
        return form

    def retire_user_action(self, request, queryset):
        """選択されたユーザを退職処理する Admin アクション（インターメディエイト画面付き）。

        詳細仕様は §12.6 参照。
        """
        if "apply" in request.POST:
            successor_id = request.POST.get("successor")
            successor = CustomUser.objects.get(pk=successor_id)
            count = 0
            for user in queryset:
                try:
                    retire_user(user=user, successor=successor)
                    count += 1
                except Exception as e:
                    self.message_user(request, f"{user.username} の退職処理失敗: {e}", level="error")
            self.message_user(request, f"{count}名の退職処理が完了しました")
            return None

        successor_choices = CustomUser.objects.filter(is_active=True).exclude(
            pk__in=queryset.values_list("pk", flat=True)
        )
        return render(request, "admin/accounts/retire_user_intermediate.html", {
            "users": queryset,
            "successor_choices": successor_choices,
            "action": "retire_user_action",
            "select_across": request.POST.get("select_across", "0"),
        })

    retire_user_action.short_description = "退職処理（後継者選択あり）"

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
