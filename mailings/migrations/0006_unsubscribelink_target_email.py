"""rev15 §4.5A：UnsubscribeLink.target_email を NOT NULL かつ空文字禁止で追加。

開発 DB 削除 OK 運用（CLAUDE.md §4）であり既存行ゼロ前提。マイグレ実行時に AddField で
空文字 default を一時的に渡し、preserve_default=False で「マイグレ実行時のみ default を
使い、モデル state には default を反映しない」挙動を採る（手書き必須、Django の
makemigrations は preserve_default=False を自動生成しない）。

加えて AddConstraint で「target_email != ''」を強制する CheckConstraint を追加し、
「未指定で作成 → CharField デフォルトの空文字が入る → 仕様 §4.5A の『発行時に必ず
焼き込む』が破られる」経路を構造的に塞ぐ。
"""

from django.db import migrations, models
from django.db.models import CheckConstraint, Q


class Migration(migrations.Migration):

    dependencies = [
        ("mailings", "0005_alter_suppressedemail_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="unsubscribelink",
            name="target_email",
            field=models.EmailField(
                default="",
                help_text=(
                    "送信先メアドのスナップショット（発行時に焼き込み、rev15 §4.5A）。"
                    "配信停止判定（個人/代表）の正本。配信後に person.primary_contact が"
                    "差し替わっても判定が後天的にブレないよう、送信時の事実を保持する。"
                    "DeliveryHistory.to_email と同一値だが、配信停止フローでは本フィールドが正本。"
                ),
                max_length=254,
            ),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="unsubscribelink",
            constraint=CheckConstraint(
                condition=~Q(target_email=""),
                name="unsubscribelink_target_email_nonempty",
            ),
        ),
    ]
