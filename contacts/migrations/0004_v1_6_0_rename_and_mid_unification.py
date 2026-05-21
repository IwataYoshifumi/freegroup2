"""v1.6.0 Phase 1A：Contact フィールドのリネーム 4 件 + ContactFieldConfidence の mid 統一。

仕様書根拠：
- OpenCV_OCR仕様書v1_6_0_Claude_API_統合版 §3.1（既存フィールドのリネーム 4 件）
- OpenCV_OCR仕様書v1_6_0_Claude_API_統合版 §3.6（ContactFieldConfidence の confidence 値域 mid 統一）
- FreeGroup2本編仕様書_v1_6_0 §14.3.5（DUPLICATE_CHECK_FIELDS のリネーム反映）
- FreeGroup2本編仕様書_v1_6_0 別表 A.5（Contact フィールド一覧）
- FreeGroup2本編仕様書_v1_6_0 別表 C.3（ContactFieldConfidence.Confidence）

変更内容：
1. Contact.company → organization（リネームのみ、型変更なし）
2. Contact.mobile → mobile_phone（リネームのみ、型変更なし）
3. Contact.phone → personal_phone（リネーム + CharField(50) → JSONField(default=list)）
4. Contact.fax → personal_fax（リネーム + CharField(50) → JSONField(default=list)）
5. ContactFieldConfidence: CheckConstraint 名 / 値域 / Choices を medium → mid 化
6. ContactFieldConfidence: 既存レコード confidence='medium' を 'mid' に一括更新

phone / fax の CharField → JSONField 型変更時、既存データ（文字列）は実装環境（SQLite/PostgreSQL）の
ALTER COLUMN 挙動に従う。開発 DB は削除して再構築する前提（CLAUDE.md §4、指示書 §4.4）。
本番デプロイ時のデータ移行は別途検討。
"""

from django.db import migrations, models


def _forward_medium_to_mid(apps, schema_editor):
    """[性質] 副作用あり（DB書込：ContactFieldConfidence.confidence='medium' を 'mid' に一括更新）。"""
    ContactFieldConfidence = apps.get_model("contacts", "ContactFieldConfidence")
    ContactFieldConfidence.objects.filter(confidence="medium").update(confidence="mid")


def _reverse_mid_to_medium(apps, schema_editor):
    """[性質] 副作用あり（DB書込：マイグレーションを巻き戻すときに 'mid' を 'medium' に戻す）。"""
    ContactFieldConfidence = apps.get_model("contacts", "ContactFieldConfidence")
    ContactFieldConfidence.objects.filter(confidence="mid").update(confidence="medium")


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0003_contact_managed_by"),
    ]

    operations = [
        # ---- Contact フィールドのリネーム 4 件 ----
        migrations.RenameField(
            model_name="contact",
            old_name="company",
            new_name="organization",
        ),
        migrations.RenameField(
            model_name="contact",
            old_name="mobile",
            new_name="mobile_phone",
        ),
        migrations.RenameField(
            model_name="contact",
            old_name="phone",
            new_name="personal_phone",
        ),
        migrations.RenameField(
            model_name="contact",
            old_name="fax",
            new_name="personal_fax",
        ),
        # ---- personal_phone / personal_fax の CharField → JSONField 化 ----
        migrations.AlterField(
            model_name="contact",
            name="personal_phone",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="contact",
            name="personal_fax",
            field=models.JSONField(blank=True, default=list),
        ),
        # ---- ContactFieldConfidence: medium → mid 統一 ----
        # 順序は重要：
        # 1) 旧 CheckConstraint（low/medium のみ許可）を外す
        # 2) 既存 medium レコードを mid に書き換え（CheckConstraint なし状態）
        # 3) Choices を mid 化
        # 4) 新 CheckConstraint（low/mid のみ許可）を追加
        migrations.RemoveConstraint(
            model_name="contactfieldconfidence",
            name="confidence_low_or_medium",
        ),
        migrations.RunPython(
            _forward_medium_to_mid,
            _reverse_mid_to_medium,
        ),
        migrations.AlterField(
            model_name="contactfieldconfidence",
            name="confidence",
            field=models.CharField(
                choices=[("low", "低"), ("mid", "中")],
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="contactfieldconfidence",
            constraint=models.CheckConstraint(
                condition=models.Q(("confidence__in", ["low", "mid"])),
                name="confidence_low_or_mid",
            ),
        ),
    ]
