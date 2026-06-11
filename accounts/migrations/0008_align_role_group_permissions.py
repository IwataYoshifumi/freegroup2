"""ロール／グループと Phase 7 要求権限の整合是正（初期データ）。

Phase 7（段1〜段3）で各 View に付与した Django 標準 CRUD 権限と、グループ初期データ
（accounts/0004・0005）の食い違いを是正する。superuser 以外（グループ経由のロール）でも
名刺・Contact 画面が要求権限を満たすようにする。0004/0005/0007 と同じ RunPython パターン
（forward 冒頭で create_permissions を先行呼び出し・get_or_create で冪等・reverse 実装）。

【内容】
  1. contacts グループ新設（contact_viewer / contact_editor / contact_admin）＋権限紐付け。
  2. card_* グループの標準 CRUD 張り替え（累積型・campaign_* と同型）。死蔵独自権限
     （create_card / edit_card / merge_card）のグループ紐付けを除去（Meta 定義は残す＝別宿題）。
  3. person_editor へ persons.view_person を追加（sales が Person 一覧で 403 になる穴を解消）。
  4. Role.default_groups へ contact_* / card_* を増分束ね（.add()。既存束ねは set() しない）。

【スコープ】グループ／権限紐付けと default_groups 増分のみ。モデル・Meta・View には触れない。
スキーマ変更ゼロ（RunPython データ Migration）。

【冪等】get_or_create ＋ permissions.add（重複無害）。reverse は 0005 時点の状態へ厳密復元
（contact_* 削除・card_* を 0005 セットへ set()・person_editor から view_person 除去）。
"""

from django.db import migrations


# 1. 新設 contacts グループ → (app_label, codename)。累積型（admin/editor 自身が view を含む）。
CONTACT_GROUP_PERMISSIONS = {
    "contact_viewer": [
        ("contacts", "view_contact"),
    ],
    "contact_editor": [
        ("contacts", "view_contact"),
        ("contacts", "add_contact"),
        ("contacts", "change_contact"),
    ],
    "contact_admin": [
        ("contacts", "view_contact"),
        ("contacts", "add_contact"),
        ("contacts", "change_contact"),
        ("contacts", "delete_contact"),
        # 横断編集は admin 相当のみ（editor には配らない）。
        ("contacts", "edit_all_contacts"),
    ],
}

# 2. card_* へ追加する標準 CRUD（累積型・campaign_* 0004 と同粒度）。
CARD_GROUP_ADD_PERMISSIONS = {
    "card_viewer": [
        ("cards", "view_businesscard"),
        ("cards", "view_originalimage"),
    ],
    "card_editor": [
        ("cards", "view_businesscard"),
        ("cards", "view_originalimage"),
        ("cards", "add_originalimage"),
        ("cards", "add_businesscard"),
        ("cards", "change_businesscard"),
        ("cards", "change_originalimage"),
    ],
    "card_admin": [
        ("cards", "view_businesscard"),
        ("cards", "view_originalimage"),
        ("cards", "add_originalimage"),
        ("cards", "add_businesscard"),
        ("cards", "change_businesscard"),
        ("cards", "change_originalimage"),
        ("cards", "delete_businesscard"),
        ("cards", "delete_originalimage"),
    ],
}

# card_* グループから除去する死蔵独自権限（Meta 定義は残す＝別宿題）。
CARD_DEAD_PERMISSIONS = [
    ("cards", "create_card"),
    ("cards", "edit_card"),
    ("cards", "merge_card"),
]

# reverse の復元先：accounts/0005 時点（0008 適用前）の card_* グループ権限。
CARD_GROUP_ORIGINAL_PERMISSIONS = {
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
        ("cards", "view_businesscard"),
    ],
}

# 3. person_editor へ追加する権限。
PERSON_EDITOR_ADD_PERMISSIONS = [
    ("persons", "view_person"),
]

# 4. Role.default_groups への増分束ね（.add()。既存の person_*/campaign_*/tag_*/card_* は不変）。
ROLE_DEFAULT_GROUPS_ADD = {
    "admin": ["contact_admin", "card_admin"],
    "sales": ["contact_editor", "card_editor"],
    "viewer": ["contact_viewer", "card_viewer"],
}


def _get_perm(Permission, app_label, codename):
    return Permission.objects.get(
        content_type__app_label=app_label, codename=codename
    )


def _find_perm(Permission, app_label, codename):
    return Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()


def forward(apps, schema_editor):
    """整合是正を適用する。

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
    Role = apps.get_model("accounts", "Role")

    # ---- 1. contacts グループ新設＋紐付け（冪等） ----
    for group_name, perms in CONTACT_GROUP_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for app_label, codename in perms:
            group.permissions.add(_get_perm(Permission, app_label, codename))

    # ---- 2. card_* に標準 CRUD を追加し、死蔵権限の紐付けを除去 ----
    for group_name, perms in CARD_GROUP_ADD_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for app_label, codename in perms:
            group.permissions.add(_get_perm(Permission, app_label, codename))
        for app_label, codename in CARD_DEAD_PERMISSIONS:
            dead = _find_perm(Permission, app_label, codename)
            if dead is not None:
                group.permissions.remove(dead)

    # ---- 3. person_editor へ view_person を追加 ----
    person_editor = Group.objects.filter(name="person_editor").first()
    if person_editor is not None:
        for app_label, codename in PERSON_EDITOR_ADD_PERMISSIONS:
            person_editor.permissions.add(_get_perm(Permission, app_label, codename))

    # ---- 4. Role.default_groups に増分束ね（既存は維持） ----
    for code, group_names in ROLE_DEFAULT_GROUPS_ADD.items():
        role = Role.objects.filter(code=code).first()
        if role is None:
            continue
        groups = [Group.objects.get_or_create(name=n)[0] for n in group_names]
        role.default_groups.add(*groups)


def reverse(apps, schema_editor):
    """0005 時点の状態へ厳密に戻す。

    - person_editor から view_person を外す。
    - card_* グループの権限を 0005 セットへ set() で復元（標準 CRUD を外し死蔵を戻す）。
    - contact_* グループを削除（権限紐付け・default_groups M2M は CASCADE で消える）。
    既存の person_*/campaign_*/tag_* と card_* の default_groups 束ね（0005 管轄）は触らない。
    """
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    # person_editor から view_person を外す。
    person_editor = Group.objects.filter(name="person_editor").first()
    if person_editor is not None:
        for app_label, codename in PERSON_EDITOR_ADD_PERMISSIONS:
            perm = _find_perm(Permission, app_label, codename)
            if perm is not None:
                person_editor.permissions.remove(perm)

    # card_* を 0005 セットへ復元（set で正確に戻す）。
    for group_name, perms in CARD_GROUP_ORIGINAL_PERMISSIONS.items():
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            continue
        perm_objs = []
        for app_label, codename in perms:
            perm = _find_perm(Permission, app_label, codename)
            if perm is not None:
                perm_objs.append(perm)
        group.permissions.set(perm_objs)

    # contact_* グループ削除（CASCADE で権限・default_groups M2M も消える）。
    Group.objects.filter(name__in=CONTACT_GROUP_PERMISSIONS.keys()).delete()


class Migration(migrations.Migration):

    dependencies = [
        # グループ／ロール作成（0004・0005）＋ tagcategory 紐付け（0007）の後に流す。
        ("accounts", "0007_grant_tagcategory_to_tag_groups"),
        # BusinessCard / OriginalImage の標準・死蔵 Permission を historical state に保証。
        ("cards", "0003_businesscard_osd_json_alter_debugmask_mask_type"),
        # contacts.edit_all_contacts（Meta 例外 Permission）を historical state に保証。
        ("contacts", "0006_alter_contact_options"),
        # persons.view_person（標準）を historical state に保証。
        ("persons", "0003_alter_person_status"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
