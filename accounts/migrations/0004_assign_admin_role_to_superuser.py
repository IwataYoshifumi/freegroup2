from django.db import migrations


def forward(apps, schema_editor):
    """既存 superuser に「管理者」Role を付与し、対応する Groups もセットする（仕様書 §8）。"""
    User = apps.get_model("accounts", "CustomUser")
    Role = apps.get_model("accounts", "Role")

    try:
        admin_role = Role.objects.get(code="admin")
    except Role.DoesNotExist:
        # admin Role がまだ作られていない → 初期データ Migration を先に流す前提
        # 何もしないで return（後続の初期データ Migration で対応）
        return

    admin_group_ids = list(admin_role.default_groups.values_list("id", flat=True))

    for su in User.objects.filter(is_superuser=True, is_active=True):
        if su.role_id is None:
            su.role = admin_role
            su.save()
            # M2M をセット（apply_role の atomic 部分を Migration 内で再現）
            su.groups.set(admin_group_ids)


def reverse(apps, schema_editor):
    """ロールバック時は role を None に戻す。"""
    User = apps.get_model("accounts", "CustomUser")
    Role = apps.get_model("accounts", "Role")
    try:
        admin_role = Role.objects.get(code="admin")
    except Role.DoesNotExist:
        return
    for su in User.objects.filter(role=admin_role, is_superuser=True):
        su.role = None
        su.groups.clear()
        su.save()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_create_initial_roles_and_groups"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
