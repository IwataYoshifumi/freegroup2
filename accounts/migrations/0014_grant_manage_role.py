"""user_admin グループへ accounts.manage_role を付与（アクセス管理UI (B)・業務ロール CRUD）。

業務UI（RoleListView 等）で業務ロールの作成・編集・削除・default_groups 編集を行うための
新権限 manage_role（CustomUser.Meta.permissions に追加、0013 で options 化）を user_admin
グループに紐付ける。これにより業務ロール admin（user_admin を default_groups に持つ）が本体
画面で業務ロールを管理できるようになる。

【スコープ】user_admin グループへの manage_role 紐付けのみ。他グループ・Role.default_groups・
既存権限は触れない。スキーマ変更ゼロ（RunPython データ Migration）。

【流儀】accounts/0008・0010・0012 と同じ RunPython パターン（forward 冒頭で create_permissions
を先行呼び出しして Permission / ContentType を物理化）。apply_role は呼ばない。

【冪等】permissions.add は重複に無害。reverse で本 migration が付けた紐付けのみ外す。
"""

from django.db import migrations


def _find_perm(Permission, app_label, codename):
    return Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()


def forward(apps, schema_editor):
    """user_admin グループへ manage_role を付与する。

    post_migrate より前に走るため Permission レコードが未作成。先に create_permissions を
    全 app_config に明示呼び出しして Permission / ContentType を物理化する（0013 で
    options 化した manage_role もここで物理化される）。apps=apps で historical model
    レジストリを渡す。
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.filter(name="user_admin").first()
    if group is None:
        return
    perm = _find_perm(Permission, "accounts", "manage_role")
    if perm is not None:
        group.permissions.add(perm)


def reverse(apps, schema_editor):
    """本 migration が付けた manage_role 紐付けのみ外す（グループは削除しない）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.filter(name="user_admin").first()
    if group is None:
        return
    perm = _find_perm(Permission, "accounts", "manage_role")
    if perm is not None:
        group.permissions.remove(perm)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_alter_customuser_options_manage_role"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
