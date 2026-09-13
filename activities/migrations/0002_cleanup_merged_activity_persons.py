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


def cleanup_merged_activity_persons(apps, schema_editor):
    ActivityPerson = apps.get_model("activities", "ActivityPerson")

    # person.status == 'merged' の ActivityPerson を抽出
    merged_aps = ActivityPerson.objects.filter(person__status="merged").select_related(
        "person__merged_into", "activity"
    )
    for ap in list(merged_aps):
        surviving = get_surviving_person(ap.person)
        if surviving.id == ap.person_id:
            continue

        act = ap.activity
        existing = ActivityPerson.objects.filter(activity=act, person=surviving).first()
        if existing:
            if not existing.memo and ap.memo:
                existing.memo = ap.memo
                existing.save(update_fields=["memo"])
            ap.delete()
        else:
            ap.person = surviving
            ap.save(update_fields=["person"])


class Migration(migrations.Migration):

    dependencies = [
        ("activities", "0001_initial"),
        ("persons", "0003_alter_person_status"),
    ]

    operations = [
        migrations.RunPython(cleanup_merged_activity_persons, migrations.RunPython.noop),
    ]
