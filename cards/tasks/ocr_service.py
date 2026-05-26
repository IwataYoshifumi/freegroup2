"""OCR バックエンド連携（フィールド抽出専用方式）。

Claude API の Tool Use で透視変換済み名刺画像から
フィールドを1リクエストで取得する。
API 送信前に画像を MAX_LONG_EDGE px 以内にリサイズする。
"""

import base64
import io
import json
import os

from django.conf import settings
from PIL import Image


class OcrApiError(Exception):
    """OCR API 通信失敗・応答抽出失敗を表す例外。"""


class OcrService:
    """Claude API を介して粗切り抜き画像から combined JSON を取得するサービス。

    クラスとして実装している理由：v2.0.0 で他バックエンド追加時に
    実装の差し替え範囲を限定するため。
    """

    DEFAULT_MODEL = "claude-sonnet-4-6"
    DEFAULT_MAX_TOKENS = 4096
    MAX_LONG_EDGE = 1568
    SCHEMA_VERSION = "1.6.1"
    TOOL_NAME = "extract_business_card"

    def __init__(self, api_key=None, model=None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        self._client = None
        self._schema_cache = None
        self._tool_input_schema_cache = None
        self._prompt_cache = None

    def run_ocr(self, image: Image.Image) -> dict:
        """粗切り抜き PIL Image を Claude API に送信し、OCR 結果（dict）を返す。

        [性質] 副作用あり（API 呼び出し）
        [入力] image: 粗切り抜き済みの PIL Image オブジェクト（EXIF 補正済みであること）
        [出力] dict: {"cards": [...], "api_response": {"model": ..., "stop_reason": ..., "usage": {...}}}
        [例外] OcrApiError: API 通信失敗、応答抽出失敗、設定不正のいずれか
        """
        if not self._api_key:
            raise OcrApiError("ANTHROPIC_API_KEY が未設定です")

        image_b64 = self._prepare_image(image)
        tool_input_schema = self._load_tool_input_schema()
        prompt = self._load_prompt()

        try:
            from anthropic import Anthropic, APIError
        except ImportError as e:
            raise OcrApiError(f"anthropic SDK のインポートに失敗: {e}")

        if self._client is None:
            self._client = Anthropic(api_key=self._api_key, timeout=60.0)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self.DEFAULT_MAX_TOKENS,
                tools=[
                    {
                        "name": self.TOOL_NAME,
                        "description": "透視変換済み名刺画像からフィールドを抽出して返す",
                        "input_schema": tool_input_schema,
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

        tool_input = self._extract_tool_use_input(response)
        api_response = self._extract_api_response(response)
        return {
            "cards": tool_input.get("cards", []),
            "api_response": api_response,
        }

    # ----- 内部ヘルパー -----

    def _prepare_image(self, pil_image: Image.Image) -> str:
        """[性質] 副作用なし（PIL Image をリサイズして base64 化）

        API 送信前に長辺を MAX_LONG_EDGE 以内にリサイズし、base64 文字列を返す。
        [出力] image_b64: str
        """
        try:
            img = pil_image.convert("RGB")
            orig_w, orig_h = img.size
            long_edge = max(orig_w, orig_h)
            if long_edge > self.MAX_LONG_EDGE:
                ratio = self.MAX_LONG_EDGE / long_edge
                new_w = max(1, int(orig_w * ratio))
                new_h = max(1, int(orig_h * ratio))
                img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as e:
            raise OcrApiError(f"画像処理失敗: {e}")

    def _load_schema(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）"""
        if self._schema_cache is None:
            schema_path = (
                settings.BASE_DIR
                / "docs"
                / "json_schema"
                / f"v{self.SCHEMA_VERSION}"
                / "combined_response.json"
            )
            try:
                with open(schema_path, encoding="utf-8") as f:
                    self._schema_cache = json.load(f)
            except OSError as e:
                raise OcrApiError(f"JSON Schema 読み込み失敗 ({schema_path}): {e}")
        return self._schema_cache

    def _load_tool_input_schema(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）

        Tool Use input_schema 用に combined_response.json の cards 部分のみを抽出する。
        Claude に返させる内容は cards のみ。
        """
        if self._tool_input_schema_cache is None:
            full = self._load_schema()
            self._tool_input_schema_cache = {
                "type": "object",
                "required": ["cards"],
                "properties": {
                    "cards": full["properties"]["cards"]
                },
                "$defs": full.get("$defs", {}),
            }
        return self._tool_input_schema_cache

    def _load_prompt(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）"""
        if self._prompt_cache is None:
            prompt_path = (
                settings.BASE_DIR / "cards" / "prompts" / "extract_combined.txt"
            )
            try:
                with open(prompt_path, encoding="utf-8") as f:
                    self._prompt_cache = f.read()
            except OSError as e:
                raise OcrApiError(f"プロンプト読み込み失敗 ({prompt_path}): {e}")
        return self._prompt_cache

    def _extract_tool_use_input(self, response) -> dict:
        """[性質] 純関数 / API 応答から Tool Use の input dict を取り出す"""
        for block in getattr(response, "content", []) or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == self.TOOL_NAME
            ):
                tool_input = getattr(block, "input", None)
                if isinstance(tool_input, dict):
                    return tool_input
        raise OcrApiError("Tool Use レスポンスが見つかりません")

    def _extract_api_response(self, response) -> dict:
        """[性質] 純関数 / API レスポンスから model / stop_reason / usage を取り出す"""
        usage = getattr(response, "usage", None)
        return {
            "model": getattr(response, "model", self._model),
            "stop_reason": getattr(response, "stop_reason", ""),
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            },
        }
