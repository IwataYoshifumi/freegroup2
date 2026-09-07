"""Group への export_contact / import_contact 権限付与。

仕様書 v1.6 §2 に基づき、新設された以下の権限を対象グループへ配分する。
- contacts.export_contact: contact_admin, contact_editor, campaign_admin, campaign_editor
- contacts.import_contact: contact_admin, contact_editor
contact_viewer, campaign_viewer には一切付与しない。
"""

from django.apps import apps as django_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


def ensure_permissions(apps, schema_editor):
    """post_migrate シグナル前に Permission / ContentType を物理化する。"""
    for app_config in django_apps.get_app_configs():
        create_permissions(
            app_config,
            apps=apps,
            using=schema_editor.connection.alias,
            verbosity=0,
        )


def grant(apps, schema_editor):
    ensure_permissions(apps, schema_editor)
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    export_perm = Permission.objects.get(
        content_type__app_label="contacts", codename="export_contact"
    )
    import_perm = Permission.objects.get(
        content_type__app_label="contacts", codename="import_contact"
    )

    for name in ("contact_admin", "contact_editor"):
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.add(export_perm, import_perm)

    for name in ("campaign_admin", "campaign_editor"):
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.add(export_perm)


def reverse(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    export_perm = Permission.objects.filter(
        content_type__app_label="contacts", codename="export_contact"
    ).first()
    import_perm = Permission.objects.filter(
        content_type__app_label="contacts", codename="import_contact"
    ).first()
    for name in ("contact_admin", "contact_editor", "campaign_admin", "campaign_editor"):
        group = Group.objects.filter(name=name).first()
        if group:
            if export_perm:
                group.permissions.remove(export_perm)
            if import_perm:
                group.permissions.remove(import_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_grant_manage_role"),
        ("contacts", "0010_alter_contact_options"),
    ]

    operations = [
        migrations.RunPython(grant, reverse),
    ]
