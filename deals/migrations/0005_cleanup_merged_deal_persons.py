from django.db import migrations


def get_surviving_person(person):
    visited = set()
    root = person
    while root.merged_into is not None:
        if root.id in visited:
            break
        visited.add(root.id)
        root = root.merged_into
    return root


def cleanup_merged_deal_persons(apps, schema_editor):
    Deal = apps.get_model("deals", "Deal")
    DealPerson = apps.get_model("deals", "DealPerson")

    # 1. Deal.primary_person が merged の場合、surviving に付け替え
    deals_with_merged_primary = Deal.objects.filter(
        primary_person__status="merged"
    ).select_related("primary_person__merged_into")
    for deal in list(deals_with_merged_primary):
        surviving = get_surviving_person(deal.primary_person)
        if surviving.id != deal.primary_person_id:
            deal.primary_person = surviving
            deal.save(update_fields=["primary_person"])
            # primary_person と DealPerson の重複削除
            DealPerson.objects.filter(deal=deal, person=surviving).delete()

    # 2. DealPerson が merged の場合、surviving に付け替え・重複削除
    merged_dps = DealPerson.objects.filter(person__status="merged").select_related(
        "person__merged_into", "deal"
    )
    for dp in list(merged_dps):
        surviving = get_surviving_person(dp.person)
        if surviving.id == dp.person_id:
            continue

        deal = dp.deal
        if deal.primary_person_id == surviving.id:
            dp.delete()
            continue

        existing = DealPerson.objects.filter(deal=deal, person=surviving).first()
        if existing:
            dp.delete()
        else:
            dp.person = surviving
            dp.save(update_fields=["person"])


class Migration(migrations.Migration):

    dependencies = [
        ("deals", "0004_alter_deal_primary_person"),
        ("persons", "0003_alter_person_status"),
    ]

    operations = [
        migrations.RunPython(cleanup_merged_deal_persons, migrations.RunPython.noop),
    ]
