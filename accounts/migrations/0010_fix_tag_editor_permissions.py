"""tag_editor（sales）の tags 権限を実態に合わせて是正（宿題F・tags）。

営業（sales＝tag_editor）の tags 権限のちぐはぐを是正する。方針：
「営業はタグの作成・付与まで。カテゴリ管理（作成・編集・削除）とタグの編集・削除は
tag_admin（admin）に寄せる」。ロールの新設・削除はしない（admin/sales/viewer の 3 ロール維持）。

【現状（0004 ＋ 0007 適用後）の tag_editor】
  view_tag, assign_tag（0004）
  ＋ view_tagcategory, add_tagcategory, change_tagcategory, delete_tagcategory（0007）
  ※ add_tag を持たない一方、tagcategory の作成・編集・削除を持つちぐはぐ。

【是正後の tag_editor 最終形】
  add_tag（新規付与）, assign_tag, view_tag, view_tagcategory
  → add_tagcategory / change_tagcategory / delete_tagcategory を除去（カテゴリ管理は admin へ）。
  → change_tag / delete_tag は元々持たない（営業は作成・付与まで）ので変更なし。

【スコープ】tag_editor グループの permission 紐付けのみ。tag_admin / tag_viewer・他グループ・
Role.default_groups・View には触れない。スキーマ変更ゼロ（RunPython データ Migration）。

【流儀】accounts/0004・0005・0007・0008 と同じ RunPython パターン（forward 冒頭で
create_permissions を先行呼び出し）。apply_role は呼ばず Group.permissions を直接 add/remove する
（historical model 制約のため）。

【冪等】permissions.add/remove は重複・不在に無害。reverse は 0007 適用後の tag_editor 状態へ
厳密復元（add_tag を外し、tagcategory 3 権限を戻す）。
"""

from django.db import migrations


# 是正で tag_editor へ「追加」する権限（営業のタグ作成を許可）。
TAG_EDITOR_ADD = [
    ("tags", "add_tag"),
]

# 是正で tag_editor から「除去」する権限（0007 が付けた tagcategory 管理系。admin に寄せる）。
TAG_EDITOR_REMOVE = [
    ("tags", "add_tagcategory"),
    ("tags", "change_tagcategory"),
    ("tags", "delete_tagcategory"),
]


def _find_perm(Permission, app_label, codename):
    return Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()


def forward(apps, schema_editor):
    """tag_editor に add_tag を付与し、tagcategory 管理系 3 権限を外す。

    post_migrate より前に走るため Permission レコードが未作成。先に create_permissions
    を全 app_config に明示呼び出しして Permission / ContentType を物理化する。
    apps=apps で historical model レジストリを渡す。
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.filter(name="tag_editor").first()
    if group is None:
        return
    for app_label, codename in TAG_EDITOR_ADD:
        perm = _find_perm(Permission, app_label, codename)
        if perm is not None:
            group.permissions.add(perm)
    for app_label, codename in TAG_EDITOR_REMOVE:
        perm = _find_perm(Permission, app_label, codename)
        if perm is not None:
            group.permissions.remove(perm)


def reverse(apps, schema_editor):
    """0007 適用後の tag_editor 状態へ戻す（add_tag を外し、tagcategory 3 権限を戻す）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.filter(name="tag_editor").first()
    if group is None:
        return
    for app_label, codename in TAG_EDITOR_ADD:
        perm = _find_perm(Permission, app_label, codename)
        if perm is not None:
            group.permissions.remove(perm)
    for app_label, codename in TAG_EDITOR_REMOVE:
        perm = _find_perm(Permission, app_label, codename)
        if perm is not None:
            group.permissions.add(perm)


class Migration(migrations.Migration):

    dependencies = [
        # グループ作成（0004）・tagcategory 紐付け（0007）の後に流す。
        ("accounts", "0009_grant_view_person_to_person_admin"),
        # tags の標準 Permission（add_tag / *_tagcategory）を historical state に保証。
        ("tags", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
