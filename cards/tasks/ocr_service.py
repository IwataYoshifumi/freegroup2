"""OCR バックエンド連携（仕様書 v1.2.2 §8.5.2）。

Claude API の Tool Use で標準 JSON 形式（schema_version / ocr_meta / cards）の
OCR 結果を取得する。v1.2.x では Claude Haiku 4.5 のみ対応。
"""

import base64
import json
import os
from datetime import datetime, timezone

from django.conf import settings


class OcrApiError(Exception):
    """OCR API 通信失敗・応答抽出失敗を表す例外。"""


class OcrService:
    """Claude API を介して名刺画像から標準 JSON を取得するサービス（v1.2.2 §8.5.2）。

    クラスとして実装している理由：v2.0.0 で他バックエンド追加時に
    実装の差し替え範囲を限定するため（v1.2.x では Claude Haiku 4.5 のみ）。
    """

    DEFAULT_MODEL = "claude-haiku-4-5-20251001"
    DEFAULT_ENGINE = "claude-haiku-4-5"
    DEFAULT_MODEL_VERSION = "20251001"
    DEFAULT_MAX_TOKENS = 4096
    TOOL_NAME = "extract_business_cards"

    def __init__(self, api_key=None, model=None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        self._client = None
        self._schema_cache = None
        self._prompt_cache = None

    def run_ocr(self, image_path):
        """画像を Claude API に送信し、標準 JSON（dict）を返す。

        [性質] 副作用あり（API 呼び出し・ファイル読み込み）
        [入力] image_path: 元画像の絶対パス（JPEG）
        [出力] dict（標準 JSON: schema_version / ocr_meta / cards）
        [例外] OcrApiError: API 通信失敗、応答抽出失敗、設定不正のいずれか
        """
        if not self._api_key:
            raise OcrApiError("ANTHROPIC_API_KEY が未設定です")

        image_b64 = self._read_image_as_base64(image_path)
        schema = self._load_schema()
        prompt = self._load_prompt()

        try:
            from anthropic import Anthropic, APIError
        except ImportError as e:
            raise OcrApiError(f"anthropic SDK のインポートに失敗: {e}")

        if self._client is None:
            self._client = Anthropic(api_key=self._api_key)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self.DEFAULT_MAX_TOKENS,
                tools=[
                    {
                        "name": self.TOOL_NAME,
                        "description": "名刺画像から OCR 情報を抽出する",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": self.TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except APIError as e:
            raise OcrApiError(f"Claude API 通信失敗: {e}")

        result = self._extract_tool_use_input(response)
        self._overlay_ocr_meta(result)
        return result

    # ----- 内部ヘルパー -----

    def _read_image_as_base64(self, image_path):
        """[性質] 副作用あり（ファイル読み込み）"""
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            raise OcrApiError(f"画像ファイル読み込み失敗 ({image_path}): {e}")

    def _load_schema(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）"""
        if self._schema_cache is None:
            schema_path = (
                settings.BASE_DIR
                / "docs"
                / "json_schema"
                / "v1.0.0"
                / "standard_response.json"
            )
            try:
                with open(schema_path, encoding="utf-8") as f:
                    self._schema_cache = json.load(f)
            except OSError as e:
                raise OcrApiError(f"JSON Schema 読み込み失敗 ({schema_path}): {e}")
        return self._schema_cache

    def _load_prompt(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）"""
        if self._prompt_cache is None:
            prompt_path = (
                settings.BASE_DIR / "cards" / "prompts" / "extract_card.txt"
            )
            try:
                with open(prompt_path, encoding="utf-8") as f:
                    self._prompt_cache = f.read()
            except OSError as e:
                raise OcrApiError(f"プロンプト読み込み失敗 ({prompt_path}): {e}")
        return self._prompt_cache

    def _extract_tool_use_input(self, response):
        """[性質] 純関数 / API 応答から Tool Use の input dict を取り出す"""
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == self.TOOL_NAME:
                tool_input = getattr(block, "input", None)
                if isinstance(tool_input, dict):
                    return tool_input
        raise OcrApiError("Tool Use レスポンスが見つかりません")

    def _overlay_ocr_meta(self, result):
        """[性質] 副作用あり（result 辞書を書き換え）

        ocr_meta の engine / version / timestamp を OcrService 側の正確な値で
        上書きする（Claude の自己申告に頼らない）。schema_version も補完する。
        """
        if not isinstance(result, dict):
            return
        result.setdefault("schema_version", "1.0.0")
        meta = result.get("ocr_meta")
        if not isinstance(meta, dict):
            meta = {}
            result["ocr_meta"] = meta
        meta["engine"] = self.DEFAULT_ENGINE
        meta["version"] = self.DEFAULT_MODEL_VERSION
        meta["timestamp"] = datetime.now(timezone.utc).isoformat()
        meta.setdefault("raw_text", "")
