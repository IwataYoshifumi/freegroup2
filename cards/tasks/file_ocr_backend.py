"""ワー君JSON（media/ocr_json）を読む OCR バックエンド（OCR_BACKEND=worker_cowork、(2)正式版）。

OcrService（Claude API バックエンド）と同じ ``run_ocr(image, business_card)`` の口を持ち、
対象 BusinessCard の ocr_json ファイルから「裸8ブロック」JSON を読んで
``{"cards": [...], "api_response": {...}}`` 形式で返す。画像引数（image）は使わない
（ファイルが OCR 結果の正本）。

挙動：
  - ファイル未存在 : OcrSourceNotReady を raise（呼び出し側で pending 維持・エラー扱いにしない）
  - スキーマ違反   : FileOcrSchemaError を raise（呼び出し側で ocr_failed 終端・error 記録）
  - business_card=None : ValueError（FileOcrBackend は BC 必須）
"""

import json
import logging
from pathlib import Path

import jsonschema
from django.conf import settings

from cards.tasks.ocr_exceptions import FileOcrSchemaError, OcrSourceNotReady

logger = logging.getLogger(__name__)


class FileOcrBackend:
    """media/ocr_json/{original_image_id}-{card_index}.json を読む OCR バックエンド。

    OcrService と口を揃えるためクラスで実装（ファクトリが生成して process_ocr に渡す）。
    """

    def run_ocr(self, image, business_card=None) -> dict:
        """対象 BC の ocr_json を読み、OcrService.run_ocr と同形式の dict を返す。

        [性質] 副作用あり（ファイル読み込み）。image 引数は無視する（口を揃えるための引数）。
        [入力] image: 未使用（OcrService と同一シグネチャ用）
               business_card: BusinessCard（必須）。None なら ValueError
        [出力] dict: {"cards": [<裸8ブロック>], "api_response": {...}}
        [例外] ValueError（business_card=None）/ OcrSourceNotReady（ファイル無し）/
               FileOcrSchemaError（正本スキーマ違反）
        """
        if business_card is None:
            raise ValueError(
                "FileOcrBackend.run_ocr requires business_card (got None)"
            )

        path = self._json_path(business_card)
        if not path.is_file():
            # 未着はエラーではない。呼び出し側が pending 維持する合図として送出。
            raise OcrSourceNotReady(f"ocr_json 未出力: {path}")

        with open(path, encoding="utf-8") as f:
            bare_card = json.load(f)

        # 入口スキーマゲート（正本 v1.6.1 の card 単位スキーマ）。違反は取り込まない。
        # 後段（_determine_ocr_result）と同一の正本スキーマを使う（_get_card_item_schema）。
        # 遅延 import で循環依存（ocr_pipeline ⇄ file_ocr_backend）を避ける。
        from cards.tasks.ocr_pipeline import _get_card_item_schema

        try:
            jsonschema.validate(instance=bare_card, schema=_get_card_item_schema())
        except jsonschema.ValidationError as e:
            field_path = getattr(e, "json_path", "") or "$"
            raise FileOcrSchemaError(
                f"スキーマ違反 ({field_path}): {e.message}"
            )

        return {
            "cards": [bare_card],
            "api_response": {
                "model": "worker_cowork",
                "stop_reason": "",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }

    def _json_path(self, business_card) -> Path:
        """[性質] 純関数 / BC から ocr_json ファイルパスを導出する（項6の方式）。

        ファイル名は {original_image_id}-{card_index}.json。card_image を参照せず
        BC のフィールドから確定的に導出する（card_image=None の BC でも導出可）。
        """
        filename = f"{business_card.original_image_id}-{business_card.card_index}.json"
        return Path(settings.MEDIA_ROOT) / "ocr_json" / filename
