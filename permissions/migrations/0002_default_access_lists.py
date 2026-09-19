import logging
from django.db import migrations

logger = logging.getLogger(__name__)


def forward(apps, schema_editor):
    AccessList = apps.get_model("permissions", "AccessList")
    ACLEntry = apps.get_model("permissions", "ACLEntry")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Department = apps.get_model("accounts", "Department")
    DealList = apps.get_model("deals", "DealList")
    Deal = apps.get_model("deals", "Deal")
    PersonList = apps.get_model("persons", "PersonList")
    Person = apps.get_model("persons", "Person")
    User = apps.get_model("accounts", "CustomUser")

    admin_user = (
        User.objects.filter(is_active=True, is_superuser=True).first()
        or User.objects.filter(is_active=True).first()
        or User.objects.first()
    )
    if not admin_user:
        admin_user = User.objects.create(
            username="system_migration_admin",
            is_active=True,
            is_superuser=True,
        )

    # 1. デフォルトAccessList
    default_al, _ = AccessList.objects.get_or_create(
        name="デフォルトアクセスリスト",
        defaults={
            "description": "システム自動生成のデフォルトアクセスリスト",
            "created_by": admin_user,
        },
    )

    # ルート部署ACLEntryの作成（防御策必須: 存在しない場合はスキップ）
    root_dept = Department.objects.filter(parent__isnull=True).first()
    if root_dept:
        dept_ct = ContentType.objects.get_for_model(Department)
        if not ACLEntry.objects.filter(
            access_list=default_al,
            target_content_type=dept_ct,
            target_object_id=str(root_dept.pk),
        ).exists():
            ACLEntry.objects.create(
                access_list=default_al,
                order=1,
                permission_level="editor",
                target_content_type=dept_ct,
                target_object_id=str(root_dept.pk),
            )
    else:
        logger.warning(
            "ルート部署（Department parent__isnull=True）が存在しないため、"
            "デフォルトAccessListのACLEntry作成をスキップしました。"
        )

    # 2. デフォルトDealList / デフォルトPersonList
    default_dl, _ = DealList.objects.get_or_create(
        name="デフォルト案件リスト",
        defaults={
            "description": "システム自動生成のデフォルト案件リスト",
            "access_list": default_al,
            "edit_scope": "all_editors",
            "created_by": admin_user,
        },
    )

    default_pl, _ = PersonList.objects.get_or_create(
        name="デフォルトパーソンリスト",
        defaults={
            "description": "システム自動生成のデフォルトパーソンリスト",
            "access_list": default_al,
            "edit_scope": "all_editors",
            "created_by": admin_user,
        },
    )

    # 3. 既存レコードの埋め戻し
    Deal.objects.filter(deal_list__isnull=True).update(deal_list=default_dl)
    Person.objects.filter(person_list__isnull=True).update(person_list=default_pl)


def reverse(apps, schema_editor):
    Deal = apps.get_model("deals", "Deal")
    Person = apps.get_model("persons", "Person")
    DealList = apps.get_model("deals", "DealList")
    PersonList = apps.get_model("persons", "PersonList")
    AccessList = apps.get_model("permissions", "AccessList")

    Deal.objects.filter(deal_list__name="デフォルト案件リスト").update(deal_list=None)
    Person.objects.filter(person_list__name="デフォルトパーソンリスト").update(person_list=None)

    DealList.objects.filter(name="デフォルト案件リスト").delete()
    PersonList.objects.filter(name="デフォルトパーソンリスト").delete()
    AccessList.objects.filter(name="デフォルトアクセスリスト").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("permissions", "0001_initial"),
        ("deals", "0006_deallist_deal_deal_list"),
        ("persons", "0004_personlist_person_person_list"),
        ("accounts", "0018_usergroup"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
