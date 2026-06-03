"""v1.5.0 認可基盤（グループ/ロール）の初期データ ＋ rev20 §14.3 の v1.6 グループ束ね。

v1.5.0 §13.8 のコード例を実装に落とす。person_*/card_*/user_admin の 7 グループと
admin/sales/viewer の 3 ロールを作成し、各ロールの default_groups を
「v1.5.0 分 ＋ v1.6 分（campaign_*/tag_*）」の最終形（rev20 §14.3）で set する（③吸収）。

【スコープ】グループ/ロール作成と default_groups 設定のみ。所有者判定メソッド・View Mixin
（④⑤）には踏み込まない。スキーマ変更ゼロ（RunPython データ Migration）。

【0004 との関係】campaign_*/tag_* は 0004 で作成済みのため、本 migration では get_or_create
で取得するだけ（重複作成しない・冪等）。reverse は本 migration が作る v1.5.0 グループと
3 ロールのみ削除（campaign_*/tag_* は 0004 管轄なので触らない）。
"""

from django.db import migrations


# §13.8：v1.5.0 グループ → (app_label, codename)。view_* は Django 標準（自動生成）。
V1_5_GROUP_PERMISSIONS = {
    "person_admin": [
        ("persons", "undo_merge"),
        ("persons", "merge_person"),
        ("persons", "link_user"),
    ],
    "person_editor": [
        ("persons", "merge_person"),
        ("persons", "link_user"),
    ],
    "person_viewer": [
        ("persons", "view_person"),  # Django 標準
    ],
    "user_admin": [
        ("accounts", "link_user_to_person"),
        ("accounts", "retire_user"),
    ],
    "card_admin": [
        ("cards", "create_card"),
        ("cards", "edit_card"),
        ("cards", "merge_card"),
    ],
    "card_editor": [
        ("cards", "create_card"),
        ("cards", "edit_card"),
    ],
    "card_viewer": [
        ("cards", "view_businesscard"),  # Django 標準（モデル名 BusinessCard）
    ],
}

# §13.8：3 ロール（code をキー、name / sort_order を defaults）。
V1_5_ROLES = [
    ("admin", "管理者", 1),
    ("sales", "営業", 2),
    ("viewer", "閲覧者", 3),
]

# rev20 §14.3：各ロールの default_groups 最終形（v1.5.0 分 ＋ v1.6 分）。
ROLE_DEFAULT_GROUPS = {
    "admin": ["person_admin", "user_admin", "card_admin", "campaign_admin", "tag_admin"],
    "sales": ["person_editor", "card_editor", "campaign_editor", "tag_editor"],
    "viewer": ["person_viewer", "card_viewer", "campaign_viewer", "tag_viewer"],
}


def forward(apps, schema_editor):
    """v1.5.0 グループ/ロールを作成し、default_groups を最終形（§14.3）で set する。

    post_migrate より前に走るため Permission レコードが未作成。先に create_permissions
    を全 app_config に明示呼び出しして標準・例外 Permission を物理化する（呼ばないと
    Permission.objects.get が DoesNotExist で落ちる）。apps=apps で historical model
    レジストリを渡す。
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")

    # ---- 1. v1.5.0 グループを作成し権限を紐付け（冪等） ----
    for group_name, perms in V1_5_GROUP_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for app_label, codename in perms:
            perm = Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
            group.permissions.add(perm)

    # ---- 2. 3 ロールを作成（冪等） ----
    for code, name, sort_order in V1_5_ROLES:
        Role.objects.get_or_create(
            code=code,
            defaults={"name": name, "sort_order": sort_order},
        )

    # ---- 3. default_groups を最終形で set（campaign_*/tag_* は 0004 作成済みを取得） ----
    for code, group_names in ROLE_DEFAULT_GROUPS.items():
        role = Role.objects.get(code=code)
        groups = [Group.objects.get_or_create(name=n)[0] for n in group_names]
        role.default_groups.set(groups)


def reverse(apps, schema_editor):
    """本 migration が作った v1.5.0 グループと 3 ロールを削除（campaign_*/tag_* は触らない）。"""
    Group = apps.get_model("auth", "Group")
    Role = apps.get_model("accounts", "Role")

    Role.objects.filter(code__in=[code for code, _, _ in V1_5_ROLES]).delete()
    Group.objects.filter(name__in=list(V1_5_GROUP_PERMISSIONS.keys())).delete()


class Migration(migrations.Migration):

    dependencies = [
        # campaign_*/tag_* グループ取得 ＋ Role モデル ＋ CustomUser 例外 Perm を historical
        # state に保証。
        ("accounts", "0004_create_v1_6_groups"),
        # persons 例外 Perm（undo_merge 等）を historical state に保証（最新に張る）。
        ("persons", "0003_alter_person_status"),
        # cards 例外 Perm（create_card 等）を historical state に保証（最新に張る）。
        ("cards", "0002_originalimage_exif_json"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
