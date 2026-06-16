"""user_admin グループへ accounts.assign_role_to_user を付与（宿題F・ロール割り当て UI）。

本体 UI（AssignRoleView）でロール割り当てを行うための新権限 assign_role_to_user
（CustomUser.Meta.permissions に追加、0011 で options 化）を user_admin グループに紐付ける。
これにより業務ロール admin（user_admin を default_groups に持つ）が本体画面でロールを
割り当てられるようになる。

【スコープ】user_admin グループへの assign_role_to_user 紐付けのみ。tag_admin / 他グループ・
Role.default_groups・既存権限は触れない。スキーマ変更ゼロ（RunPython データ Migration）。

【流儀】accounts/0008・0010 と同じ RunPython パターン（forward 冒頭で create_permissions を
先行呼び出しして Permission / ContentType を物理化）。apply_role は呼ばない。

【冪等】permissions.add は重複に無害。reverse で本 migration が付けた紐付けのみ外す。
"""

from django.db import migrations


def _find_perm(Permission, app_label, codename):
    return Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()


def forward(apps, schema_editor):
    """user_admin グループへ assign_role_to_user を付与する。

    post_migrate より前に走るため Permission レコードが未作成。先に create_permissions を
    全 app_config に明示呼び出しして Permission / ContentType を物理化する（0011 で
    options 化した assign_role_to_user もここで物理化される）。apps=apps で historical
    model レジストリを渡す。
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
    perm = _find_perm(Permission, "accounts", "assign_role_to_user")
    if perm is not None:
        group.permissions.add(perm)


def reverse(apps, schema_editor):
    """本 migration が付けた assign_role_to_user 紐付けのみ外す（グループは削除しない）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.filter(name="user_admin").first()
    if group is None:
        return
    perm = _find_perm(Permission, "accounts", "assign_role_to_user")
    if perm is not None:
        group.permissions.remove(perm)


class Migration(migrations.Migration):

    dependencies = [
        # assign_role_to_user を Meta.permissions に options 化した migration の後に流す。
        ("accounts", "0011_alter_customuser_options"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
