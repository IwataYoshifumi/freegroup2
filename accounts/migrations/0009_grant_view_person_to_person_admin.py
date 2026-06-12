"""person_admin グループへ persons.view_person を追加（初期データ是正）。

0008 で person_editor の「Person 一覧 403」穴は塞いだが、より上位の person_admin
（merge_person / undo_merge / link_user は持つが view_person を持たない）は見落とされて
おり、person_admin のみ保有のユーザーが /persons/（PersonListView / PersonDetailView、
要求権限 persons.view_person）で 403 になる。本 migration で person_admin に
persons.view_person を追加する。

【内容】person_admin へ persons.view_person を追加（forward）／除去（reverse）。
0008 の person_editor 追加処理（152-156 行）と同型の RunPython パターン
（forward 冒頭で create_permissions 先行呼び出し・get_or_create で冪等・reverse 実装）。

【スコープ】グループ権限紐付けのみ。モデル・Meta・View・他グループには触れない。
スキーマ変更ゼロ（RunPython データ Migration）。

【冪等】permissions.add（重複無害）。reverse は person_admin から view_person を外すのみ
（0008 適用前の person_admin は view_person を持たないため、これで適用前状態に戻る）。
"""

from django.db import migrations


# person_admin へ追加する権限（0008 の PERSON_EDITOR_ADD_PERMISSIONS と同型）。
PERSON_ADMIN_ADD_PERMISSIONS = [
    ("persons", "view_person"),
]


def _get_perm(Permission, app_label, codename):
    return Permission.objects.get(
        content_type__app_label=app_label, codename=codename
    )


def _find_perm(Permission, app_label, codename):
    return Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()


def forward(apps, schema_editor):
    """person_admin へ persons.view_person を追加する。

    post_migrate より前に走るため Permission レコードが未作成。0008 と同様に
    create_permissions を全 app_config に明示呼び出しして Permission / ContentType を
    物理化してから紐付ける。apps=apps で historical model レジストリを渡す。
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    person_admin = Group.objects.filter(name="person_admin").first()
    if person_admin is not None:
        for app_label, codename in PERSON_ADMIN_ADD_PERMISSIONS:
            person_admin.permissions.add(_get_perm(Permission, app_label, codename))


def reverse(apps, schema_editor):
    """person_admin から persons.view_person を外す（0008 適用後＝0009 適用前の状態へ）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    person_admin = Group.objects.filter(name="person_admin").first()
    if person_admin is not None:
        for app_label, codename in PERSON_ADMIN_ADD_PERMISSIONS:
            perm = _find_perm(Permission, app_label, codename)
            if perm is not None:
                person_admin.permissions.remove(perm)


class Migration(migrations.Migration):

    dependencies = [
        # グループ／ロール整合是正（person_editor への view_person 追加）の後に流す。
        ("accounts", "0008_align_role_group_permissions"),
        # persons.view_person（標準）を historical state に保証。
        ("persons", "0003_alter_person_status"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
