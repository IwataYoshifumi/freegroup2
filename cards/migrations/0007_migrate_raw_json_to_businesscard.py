"""v1.5.0 フェーズ① データマイグレーション。

旧構造（OriginalImage.raw_json に cards 配列を集約・パターン A）から
新構造（BusinessCard.raw_json_1 に 1 BC 分の生 JSON を保持）へデータを移し替える。

挙動：
- すべての BusinessCard をループ
- original_image.raw_json が None / cards キー欠落 / card_index が範囲外なら該当 BC をスキップ
- 移し替え時の形式は OcrService.run_ocr() の戻り値を踏襲：
    {"cards": [<該当 card_data>], "api_response": <raw_json.get("api_response", {})>}
- BC.raw_json_2 は触らない（null のまま）
- BC.ocr_status = "done" を一律セット（既存 BC は OCR 完了済みのため）
- BC.ocr_result は維持（既存値をそのまま）
- 個別 BC の例外はスキップしてログ出力、他の BC 移行は続行

reverse は no-op。戻したい場合は別マイグレーションを書く。
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def migrate_raw_json_to_businesscard(apps, schema_editor):
    """OriginalImage.raw_json を BusinessCard.raw_json_1 へ移し替える。

    [性質] 副作用あり（DB 書き込み）
    """
    BusinessCard = apps.get_model("cards", "BusinessCard")

    migrated = 0
    skipped_no_raw_json = 0
    skipped_no_cards = 0
    skipped_index_out_of_range = 0
    skipped_exception = 0

    qs = BusinessCard.objects.select_related("original_image").all()
    for bc in qs.iterator():
        try:
            raw_json = bc.original_image.raw_json
            if not raw_json or not isinstance(raw_json, dict):
                skipped_no_raw_json += 1
                continue

            cards = raw_json.get("cards")
            if not isinstance(cards, list):
                skipped_no_cards += 1
                continue

            idx = bc.card_index
            if idx is None or idx < 0 or idx >= len(cards):
                skipped_index_out_of_range += 1
                continue

            card_data = cards[idx]
            api_response = raw_json.get("api_response", {})
            bc.raw_json_1 = {
                "cards": [card_data],
                "api_response": api_response,
            }
            bc.ocr_status = "done"
            bc.save(update_fields=["raw_json_1", "ocr_status", "updated_at"])
            migrated += 1
        except Exception as e:
            skipped_exception += 1
            logger.warning(
                "0011 data migration: BusinessCard %s skipped (%s: %s)",
                bc.id, type(e).__name__, e,
            )

    logger.info(
        "0011 data migration: migrated=%d, "
        "skipped_no_raw_json=%d, skipped_no_cards=%d, "
        "skipped_index_out_of_range=%d, skipped_exception=%d",
        migrated, skipped_no_raw_json, skipped_no_cards,
        skipped_index_out_of_range, skipped_exception,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0006_businesscard_claimed_at_businesscard_error_message_and_more"),
    ]

    operations = [
        migrations.RunPython(
            migrate_raw_json_to_businesscard,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
