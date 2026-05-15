from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


def forward(apps, schema_editor):
    """Group / Role / 紐付けを初期データとして作成（仕様書 §13.8）。

    Django の post_migrate より前に走るため、Permission レコードが未作成。
    先に create_permissions を明示的に呼んで Permission / ContentType を埋める
    （Django 公式 API、仕様書 §13.8 の補完）。
    """
    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")

    # ---- 1. Group を作成 ----
    person_admin, _ = Group.objects.get_or_create(name="person_admin")
    person_editor, _ = Group.objects.get_or_create(name="person_editor")
    person_viewer, _ = Group.objects.get_or_create(name="person_viewer")
    user_admin, _ = Group.objects.get_or_create(name="user_admin")
    card_admin, _ = Group.objects.get_or_create(name="card_admin")
    card_editor, _ = Group.objects.get_or_create(name="card_editor")
    card_viewer, _ = Group.objects.get_or_create(name="card_viewer")

    # ---- 2. Permission を Group に紐付け ----
    def add_perm(group, app_label, codename):
        perm = Permission.objects.get(
            content_type__app_label=app_label, codename=codename
        )
        group.permissions.add(perm)

    # person_admin
    add_perm(person_admin, "persons", "undo_merge")
    add_perm(person_admin, "persons", "merge_person")
    add_perm(person_admin, "persons", "link_user")

    # person_editor
    add_perm(person_editor, "persons", "merge_person")
    add_perm(person_editor, "persons", "link_user")

    # person_viewer
    add_perm(person_viewer, "persons", "view_person")  # Django 標準

    # user_admin
    add_perm(user_admin, "accounts", "link_user_to_person")
    add_perm(user_admin, "accounts", "retire_user")

    # card_admin
    add_perm(card_admin, "cards", "create_card")
    add_perm(card_admin, "cards", "edit_card")
    add_perm(card_admin, "cards", "merge_card")

    # card_editor
    add_perm(card_editor, "cards", "create_card")
    add_perm(card_editor, "cards", "edit_card")

    # card_viewer
    add_perm(card_viewer, "cards", "view_businesscard")  # Django 標準（モデル名 BusinessCard）

    # ---- 3. Role を作成 ----
    admin_role, _ = Role.objects.get_or_create(
        code="admin",
        defaults={"name": "管理者", "sort_order": 1},
    )
    sales_role, _ = Role.objects.get_or_create(
        code="sales",
        defaults={"name": "営業", "sort_order": 2},
    )
    viewer_role, _ = Role.objects.get_or_create(
        code="viewer",
        defaults={"name": "閲覧者", "sort_order": 3},
    )

    # ---- 4. Role の default_groups を設定 ----
    admin_role.default_groups.set([person_admin, user_admin, card_admin])
    sales_role.default_groups.set([person_editor, card_editor])
    viewer_role.default_groups.set([person_viewer, card_viewer])


def reverse(apps, schema_editor):
    """ロールバック時は作成した Role と Group を削除。"""
    Role = apps.get_model("accounts", "Role")
    Group = apps.get_model("auth", "Group")

    Role.objects.filter(code__in=["admin", "sales", "viewer"]).delete()
    Group.objects.filter(
        name__in=[
            "person_admin",
            "person_editor",
            "person_viewer",
            "user_admin",
            "card_admin",
            "card_editor",
            "card_viewer",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_groupprofile_ldapgroup_alter_customuser_options_and_more"),
        ("persons", "0002_alter_person_options_person_managed_by"),
        ("cards", "0002_alter_businesscard_options"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
