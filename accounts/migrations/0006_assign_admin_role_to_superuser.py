"""既存 superuser に admin ロールを付与する初期データ Migration（v1.5.0 §8 末尾）。

apply_role() は historical model 制約で Migration からは呼べないため、§8 末尾の
パターンどおり user.groups.set() を直接呼んで apply_role の atomic 部分を再現する。
admin ロール・default_groups は 0005 で作成済み（dependency で先行を保証）。
admin ロールが未作成、または superuser が居ない場合は何もせず return（§8 末尾のガード）。

スキーマ変更ゼロ（RunPython データ Migration）。
"""

from django.db import migrations


def forward(apps, schema_editor):
    """is_superuser=True かつ role 未設定の有効ユーザに admin ロールと Groups を付与。"""
    User = apps.get_model("accounts", "CustomUser")
    Role = apps.get_model("accounts", "Role")

    try:
        admin_role = Role.objects.get(code="admin")
    except Role.DoesNotExist:
        # admin ロール未作成（0005 未適用）→ 何もせず return（§8 末尾のガード）。
        return

    admin_group_ids = list(admin_role.default_groups.values_list("id", flat=True))

    for su in User.objects.filter(is_superuser=True, is_active=True):
        if su.role_id is None:
            su.role = admin_role
            su.save()
            # M2M をセット（apply_role の atomic 部分を Migration 内で再現）。
            su.groups.set(admin_group_ids)


def reverse(apps, schema_editor):
    """ロールバック時は admin を付けた superuser の role を None に戻し groups をクリア。"""
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
        # admin ロール / default_groups の作成（0005）の後に流す。
        ("accounts", "0005_create_v1_5_groups_roles"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
