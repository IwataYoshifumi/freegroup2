"""ワー君JSON 1件 → Contact 生成の実機確認用 最小 command（v1.7 MVP）。

完成済みの後段 _judge_and_persist_ocr_result を使い、ワー君が media/ocr_json に出力した
「裸8ブロック」JSON 1ファイルから既存 BusinessCard に raw_json を充て、Contact 生成までを
実機確認する go/no-go ゲート用コマンド。

【MVP の割り切り（本コマンドのスコープ外＝(2)正式版の責務）】
- JSON スキーマ検証は行わない
- OCR_BACKEND 切り替え・ファクトリ・FileOcrBackend は作らない
- BusinessCard は新規作成しない（既存 BC に充てる。見つからなければエラー終了）

【ファイル名規約】 <original_image_id(UUID)>-<card_index>.json
  例: 7d98e78e-4a4b-4475-9958-64ad87af1bb5-4.json
  stem を rpartition('-') し、UUID 部 + 末尾 card_index に分割して BC を一意特定する
  （BusinessCard は UniqueConstraint(original_image, card_index)）。
"""

import json
import os

from django.core.management.base import BaseCommand, CommandError

from cards.models import BusinessCard
from cards.tasks.ocr_pipeline import _judge_and_persist_ocr_result
from contacts.models import Contact


class Command(BaseCommand):
    help = (
        "ワー君JSON 1ファイル（裸8ブロック）を既存 BusinessCard に充て、"
        "_judge_and_persist_ocr_result 経由で Contact 生成までを実機確認する（MVP・実機ゲート用）。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "json_path",
            help="対象 JSON ファイルのパス（例: media/ocr_json/<uuid>-<index>.json）",
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（DB 書込：BC 更新・Contact/Person/CFC/SNS 生成）。"""
        json_path = options["json_path"]

        # --- ファイル名 → original_image_id / card_index ---
        if not os.path.isfile(json_path):
            raise CommandError(f"ファイルが存在しません: {json_path}")

        stem = os.path.splitext(os.path.basename(json_path))[0]
        original_image_id, sep, index_part = stem.rpartition("-")
        if not sep or not original_image_id or not index_part.isdigit():
            raise CommandError(
                f"ファイル名が <uuid>-<card_index>.json 形式ではありません: {stem}"
            )
        card_index = int(index_part)

        # --- 対象 BusinessCard を一意特定（新規作成はしない） ---
        try:
            business_card = BusinessCard.objects.get(
                original_image_id=original_image_id, card_index=card_index
            )
        except BusinessCard.DoesNotExist:
            raise CommandError(
                f"BusinessCard が見つかりません "
                f"(original_image_id={original_image_id}, card_index={card_index})"
            )

        # --- JSON 読み込み（裸8ブロック） ---
        with open(json_path, encoding="utf-8") as f:
            bare_card = json.load(f)

        # --- ラッパーに包む（index 0 固定で後段に渡す） ---
        adopted_raw_json = {"cards": [bare_card]}

        # --- orientation_detected を card_meta から取得（取れなければ警告して normal） ---
        card_meta = bare_card.get("card_meta") if isinstance(bare_card, dict) else None
        orientation_detected = None
        if isinstance(card_meta, dict):
            orientation_detected = card_meta.get("orientation")
        if not orientation_detected:
            self.stdout.write(
                self.style.WARNING(
                    "警告: card_meta.orientation を取得できませんでした。"
                    "'normal' として処理します（MVP はスキーマ検証を省略）。"
                )
            )
            orientation_detected = BusinessCard.ORIENTATION_NORMAL

        # --- 後段（判定 + Contact 生成）を実行 ---
        _judge_and_persist_ocr_result(
            business_card,
            adopted_raw_json,
            orientation_detected,
            adopted_raw_json,  # raw_json_1
            None,              # raw_json_2
            [],                # error_msgs
        )

        # --- 結果表示 ---
        business_card.refresh_from_db()
        contact = Contact.objects.filter(business_card=business_card).first()
        contact_id = contact.id if contact is not None else None

        self.stdout.write(self.style.SUCCESS("import_ocr_json_mvp 完了:"))
        self.stdout.write(f"  BusinessCard.id : {business_card.id}")
        self.stdout.write(f"  ocr_result      : {business_card.ocr_result}")
        self.stdout.write(f"  ocr_status      : {business_card.ocr_status}")
        self.stdout.write(f"  Contact.id      : {contact_id}")
