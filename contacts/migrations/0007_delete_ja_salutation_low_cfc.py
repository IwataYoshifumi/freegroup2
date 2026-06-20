"""lang=ja の salutation_name 自動補完由来 low CFC（未確認）を削除するデータマイグレーション。

背景（§1.8 改訂）：salutation_name の自動補完（姓＋様）は確定的な日本語敬称で、人が名刺と
見比べて直す性質ではない。よって lang=ja のとき自動補完時の low CFC 付与を廃止した
（Contact.save）。本マイグレーションは既存データに残っている同種 CFC を除去し、要確認帯
（撤去済み）や個別 confidence 表示に「意味不明な1件」が出ないようにする。

削除条件：field_name="salutation_name" かつ confidence="low" かつ confirmed_at IS NULL
かつ contact.lang="ja"。
- salutation_name の CFC は OCR 経路では作られない（json_parser は confidence_map から
  salutation_name を除外、§1.8）ため、対象は自動補完由来のみ。
- confirmed_at がある（人が確認済み）ものと、ja 以外（敬称の確度が落ちる言語）は触らない。
"""

from django.db import migrations


def delete_ja_salutation_low_cfc(apps, schema_editor):
    ContactFieldConfidence = apps.get_model("contacts", "ContactFieldConfidence")
    # ja 判定は Contact.save の抑止条件（小文字化 startswith("ja")）に揃える。
    ContactFieldConfidence.objects.filter(
        field_name="salutation_name",
        confidence="low",
        confirmed_at__isnull=True,
        contact__lang__istartswith="ja",
    ).delete()


def noop_reverse(apps, schema_editor):
    # 逆方向：削除した CFC は復元しない（自動補完で再付与もしない方針のため）。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0006_alter_contact_options"),
    ]

    operations = [
        migrations.RunPython(delete_ja_salutation_low_cfc, noop_reverse),
    ]
