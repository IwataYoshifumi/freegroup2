"""ログインセキュリティ強化（Step 1）の権限付与とセキュリティ設定初期レコード作成。

1. user_admin グループへ権限 3 種（accounts.manage_security_setting /
   accounts.reset_two_factor / accounts.view_loginattempt）を付与する。
2. 既存の管理者ロール（admin）保持者の groups に、default_groups を同期反映する。
3. SecuritySetting の初期レコード（id=1、仕様書 §8.1 の初期値）を作成する。
"""

from django.apps import apps as django_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

TWO_FACTOR_PERMISSIONS = [
    ("accounts", "manage_security_setting"),
    ("accounts", "reset_two_factor"),
    ("accounts", "view_loginattempt"),
]


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
    CustomUser = apps.get_model("accounts", "CustomUser")
    SecuritySetting = apps.get_model("accounts", "SecuritySetting")

    # 1. user_admin グループへ権限 3 種を付与（冪等）
    user_admin_group, _ = Group.objects.get_or_create(name="user_admin")
    for app_label, codename in TWO_FACTOR_PERMISSIONS:
        perm = Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )
        user_admin_group.permissions.add(perm)

    # 2. 既存の管理者ロール保持者へ反映（accounts/0017 と同手順）
    admin_role = Role.objects.filter(code="admin").first()
    if admin_role is not None:
        if not admin_role.default_groups.filter(pk=user_admin_group.pk).exists():
            admin_role.default_groups.add(user_admin_group)
        default_groups = list(admin_role.default_groups.all())
        for user in CustomUser.objects.filter(role=admin_role):
            user.groups.add(*default_groups)

    # 3. SecuritySetting 初期レコード（id=1、仕様書 §8.1 の初期値）を作成
    SecuritySetting.objects.get_or_create(id=1)


def reverse(apps, schema_editor):
    """本 migration が付与した権限の紐付けと初期レコードのみ戻す。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    SecuritySetting = apps.get_model("accounts", "SecuritySetting")

    user_admin_group = Group.objects.filter(name="user_admin").first()
    if user_admin_group is not None:
        for app_label, codename in TWO_FACTOR_PERMISSIONS:
            perm = Permission.objects.filter(
                content_type__app_label=app_label,
                codename=codename,
            ).first()
            if perm is not None:
                user_admin_group.permissions.remove(perm)

    SecuritySetting.objects.filter(id=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_alter_customuser_options_accountlock_logindevice_and_more"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
