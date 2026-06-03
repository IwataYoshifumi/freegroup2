"""tag_* グループへ TagCategory 標準 Permission を紐付ける初期データ（rev20 §14.2.2、Phase 7 ⑤-B-2）。

TagCategory の標準 CRUD Permission（view/add/change/delete_tagcategory）を
tag_admin / tag_editor / tag_viewer に紐付ける。②③（accounts/0004・0005）と同じ
RunPython パターン（forward 冒頭で create_permissions を先行呼び出し）。

【スコープ】tag_* グループへの tagcategory 紐付けのみ。グループ/ロールの新規作成や
Role.default_groups の変更はしない（②③で確定済み）。スキーマ変更ゼロ。

【冪等】get_or_create でグループを取得し permissions.add（重複は無害）。reverse で
本 migration が付けた tagcategory 紐付けのみ外す。
"""

from django.db import migrations


# rev20 §14.2.2：tag_* グループ → tagcategory codename。
GROUP_TAGCATEGORY_PERMISSIONS = {
    "tag_admin": [
        "view_tagcategory",
        "add_tagcategory",
        "change_tagcategory",
        "delete_tagcategory",
    ],
    "tag_editor": [
        "view_tagcategory",
        "add_tagcategory",
        "change_tagcategory",
        "delete_tagcategory",
    ],
    "tag_viewer": [
        "view_tagcategory",
    ],
}


def forward(apps, schema_editor):
    """tag_* グループへ tagcategory 標準 Permission を紐付ける。

    post_migrate より前に走るため Permission レコードが未作成。先に create_permissions
    を全 app_config に明示呼び出しして Permission / ContentType を物理化する（呼ばないと
    Permission.objects.get が DoesNotExist で落ちる）。apps=apps で historical model
    レジストリを渡す。
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for group_name, codenames in GROUP_TAGCATEGORY_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for codename in codenames:
            perm = Permission.objects.get(
                content_type__app_label="tags",
                codename=codename,
            )
            group.permissions.add(perm)


def reverse(apps, schema_editor):
    """本 migration が付けた tagcategory 紐付けのみ外す（グループ自体は削除しない）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for group_name, codenames in GROUP_TAGCATEGORY_PERMISSIONS.items():
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            continue
        for codename in codenames:
            perm = Permission.objects.filter(
                content_type__app_label="tags",
                codename=codename,
            ).first()
            if perm is not None:
                group.permissions.remove(perm)


class Migration(migrations.Migration):

    dependencies = [
        # グループ作成（②③）の後に流す。
        ("accounts", "0006_assign_admin_role_to_superuser"),
        # TagCategory 標準 Permission を historical state に保証（TagCategory モデル宣言）。
        ("tags", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
