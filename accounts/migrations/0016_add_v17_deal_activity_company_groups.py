"""v1.7 案件管理・活動記録・会社機能の PermissionGroup 初期データ（仕様書 v1.5 §7.6）。

新設9グループ（deal_admin/editor/viewer, activity_admin/editor/viewer, company_admin/editor/viewer）を
作成し、仕様書 §7.6 に準拠した権限を付与する。
既存 Role（admin, sales, viewer）の default_groups に各グループを増分追加する。
"""

from django.apps import apps as django_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

GROUP_PERMISSIONS = {
    "deal_admin": [
        ("deals", "view_deal"),
        ("deals", "add_deal"),
        ("deals", "change_deal"),
        ("deals", "view_all_deals"),
        ("deals", "edit_all_deals"),
        ("attachments", "view_attachment"),
        ("attachments", "add_attachment"),
        ("attachments", "change_attachment"),
        ("attachments", "delete_attachment"),
    ],
    "deal_editor": [
        ("deals", "view_deal"),
        ("deals", "add_deal"),
        ("deals", "change_deal"),
        ("attachments", "view_attachment"),
        ("attachments", "add_attachment"),
        ("attachments", "change_attachment"),
        ("attachments", "delete_attachment"),
    ],
    "deal_viewer": [
        ("deals", "view_deal"),
        ("attachments", "view_attachment"),
    ],
    "activity_admin": [
        ("activities", "view_activity"),
        ("activities", "add_activity"),
        ("activities", "change_activity"),
        ("activities", "view_all_activities"),
        ("activities", "edit_all_activities"),
        ("attachments", "view_attachment"),
        ("attachments", "add_attachment"),
        ("attachments", "change_attachment"),
        ("attachments", "delete_attachment"),
    ],
    "activity_editor": [
        ("activities", "view_activity"),
        ("activities", "add_activity"),
        ("activities", "change_activity"),
        ("attachments", "view_attachment"),
        ("attachments", "add_attachment"),
        ("attachments", "change_attachment"),
        ("attachments", "delete_attachment"),
    ],
    "activity_viewer": [
        ("activities", "view_activity"),
        ("attachments", "view_attachment"),
    ],
    "company_admin": [
        ("companies", "view_company"),
        ("companies", "add_company"),
        ("companies", "change_company"),
        ("companies", "merge_company"),
    ],
    "company_editor": [
        ("companies", "view_company"),
        ("companies", "add_company"),
        ("companies", "change_company"),
        ("companies", "merge_company"),
    ],
    "company_viewer": [
        ("companies", "view_company"),
    ],
}

ROLE_DEFAULT_GROUPS_ADD = {
    "admin": ["deal_admin", "activity_admin", "company_admin"],
    "sales": ["deal_editor", "activity_editor", "company_editor"],
    "viewer": ["deal_viewer", "activity_viewer", "company_viewer"],
}


def ensure_permissions(apps, schema_editor):
    """post_migrate を待たずに Permission レコードを先に生成する。"""
    for app_config in django_apps.get_app_configs():
        create_permissions(
            app_config,
            apps=apps,
            using=schema_editor.connection.alias,
            verbosity=0,
        )


def forward(apps, schema_editor):
    ensure_permissions(apps, schema_editor)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")

    for group_name, perms in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for app_label, codename in perms:
            perm = Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
            group.permissions.add(perm)

    for code, group_names in ROLE_DEFAULT_GROUPS_ADD.items():
        role = Role.objects.filter(code=code).first()
        if role is None:
            continue
        groups = [Group.objects.get_or_create(name=n)[0] for n in group_names]
        role.default_groups.add(*groups)


def reverse(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Role = apps.get_model("accounts", "Role")

    for code, group_names in ROLE_DEFAULT_GROUPS_ADD.items():
        role = Role.objects.filter(code=code).first()
        if role is not None:
            groups = Group.objects.filter(name__in=group_names)
            role.default_groups.remove(*groups)

    Group.objects.filter(name__in=GROUP_PERMISSIONS.keys()).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_grant_export_import_contact"),
        ("companies", "0001_initial"),
        ("deals", "0001_initial"),
        ("activities", "0001_initial"),
        ("attachments", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
