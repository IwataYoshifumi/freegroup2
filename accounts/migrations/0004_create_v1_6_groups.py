"""v1.6 メール配信・タグ機能の PermissionGroup 初期データ（rev20 §14.2）。

campaign_* / tag_* の 6 グループを作成し、rev20 §14.2.1 / §14.2.2 の権限を紐付ける。
v1.5.0 §13.8 の初期データ Migration パターン（create_permissions 先行呼び出し）を踏襲。

【スコープ】グループ作成と権限紐付けのみ（②）。Role.default_groups の拡張（§14.3＝③）
には一切触れない。スキーマ変更はゼロ（RunPython データ Migration）。

【change_mailingconfig】rev20 §14.1.2 のとおり codename は Django 標準の change 権限
（MailingConfig モデルに自動生成される change_mailingconfig）をそのまま使う。
Meta.permissions への手書きはしない（auth.E005 builtin 衝突を避けるため）。
"""

from django.db import migrations


# rev20 §14.2.1 / §14.2.2 の Group → (app_label, codename) リスト。
# 標準 Permission（add/change/delete/view）も例外 Permission も同じ仕組みで紐付ける。
GROUP_PERMISSIONS = {
    "campaign_admin": [
        ("mailings", "view_campaign"),
        ("mailings", "add_campaign"),
        ("mailings", "change_campaign"),
        ("mailings", "delete_campaign"),
        ("mailings", "send_campaign"),
        ("mailings", "view_all_campaigns"),
        ("mailings", "export_report"),
        ("mailings", "manage_template"),
        ("mailings", "view_suppressedemail"),
        ("mailings", "manage_suppressed_email"),
        ("mailings", "view_mailinglist"),
        ("mailings", "add_mailinglist"),
        ("mailings", "change_mailinglist"),
        ("mailings", "delete_mailinglist"),
        ("mailings", "change_mailingconfig"),
    ],
    "campaign_editor": [
        ("mailings", "view_campaign"),
        ("mailings", "add_campaign"),
        ("mailings", "change_campaign"),
        ("mailings", "delete_campaign"),
        ("mailings", "send_campaign"),
        ("mailings", "export_report"),
        ("mailings", "manage_template"),
        ("mailings", "view_mailinglist"),
        ("mailings", "add_mailinglist"),
        ("mailings", "change_mailinglist"),
        ("mailings", "delete_mailinglist"),
        ("mailings", "view_suppressedemail"),
    ],
    "campaign_viewer": [
        ("mailings", "view_campaign"),
        ("mailings", "view_mailinglist"),
        ("mailings", "view_suppressedemail"),
    ],
    "tag_admin": [
        ("tags", "view_tag"),
        ("tags", "add_tag"),
        ("tags", "change_tag"),
        ("tags", "delete_tag"),
        ("tags", "assign_tag"),
    ],
    "tag_editor": [
        ("tags", "view_tag"),
        ("tags", "assign_tag"),
    ],
    "tag_viewer": [
        ("tags", "view_tag"),
    ],
}


def forward(apps, schema_editor):
    """6 グループを作成し、rev20 §14.2 の権限を紐付ける。

    Django の post_migrate より前に走るため、この時点では Permission レコードが
    未作成。先に create_permissions を全 app_config に対し明示呼び出しして
    Permission / ContentType を物理化する（呼ばないと Permission.objects.get が
    DoesNotExist で落ちる）。apps=apps で historical model レジストリを渡す。
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, apps=apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for group_name, perms in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for app_label, codename in perms:
            perm = Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
            group.permissions.add(perm)


def reverse(apps, schema_editor):
    """作成した 6 グループを削除する（権限紐付けも CASCADE で消える）。"""
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GROUP_PERMISSIONS.keys()).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_customuser_signature"),
        # ① で生成した Campaign 例外 Permission（send_campaign / export_report）を
        # 載せる migration。実番号で固定し、② が① より先に走らないことを保証する
        # （先に走ると create_permissions がそれらを物理化できず get が落ちる）。
        ("mailings", "0011_alter_campaign_options"),
        # assign_tag を載せる migration（tags は① で新規 migration 不発のため 0001）。
        ("tags", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
