# FreeGroup2 OCR 標準 JSON Schema v1.0.0

このディレクトリは、FreeGroup2 名刺管理機能の OCR 標準レスポンス JSON のスキーマ定義を保管する。

## ファイル構成

| ファイル | 役割 |
|---|---|
| standard_response.json | OCR 標準レスポンス全体のスキーマ |
| README.md | この説明ファイル |

## 用途

### 1. Claude API の Tool Use の input_schema

```python
import json
import anthropic

with open("docs/json_schema/v1.0.0/standard_response.json") as f:
    schema = json.load(f)

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=4096,
    tools=[{
        "name": "extract_business_cards",
        "description": "名刺画像からOCR情報を抽出する",
        "input_schema": schema,
    }],
    messages=[{"role": "user", "content": [...]}],
)
```

### 2. 受信した raw_json の検証

```python
from jsonschema import validate, ValidationError

try:
    validate(instance=raw_json, schema=schema)
    # スキーマに準拠
except ValidationError as e:
    # スキーマ違反、OriginalImage.error_message に記録
    original_image.error_message = f"スキーマ検証失敗: {e.message}"
    original_image.status = "failed"
```

### 3. プロンプトへの埋め込み

```python
prompt = f"""
名刺画像を OCR して、以下の JSON Schema に厳密に従った形式で返してください：

{json.dumps(schema, indent=2, ensure_ascii=False)}
"""
```

## スキーマのバージョン管理

- フォーマット変更時は新しいバージョンディレクトリを作成（例: v1.1.0/）
- schema_version は const で固定（v1.0.0 のスキーマは "1.0.0" のみ受理）
- 後方互換のため、旧バージョンのスキーマは削除しない

## 構造概要

```
{
  "schema_version": "1.0.0",
  "ocr_meta": {
    "engine": "claude-haiku-4-5",
    "version": "20251001",
    "timestamp": "2026-04-28T10:30:00+09:00",
    "raw_text": "..."
  },
  "cards": [
    {
      "card_meta": {
        "is_business_card": true,
        "bbox": { "x": 120, "y": 80, "width": 600, "height": 350 }
      },
      "fields": {
        "full_name": { "value": "山田太郎", "confidence": "high" },
        "company": { "value": "株式会社サンプル", "confidence": "high" }
      },
      "fields_array": {
        "emails": [
          { "value": "yamada@example.com", "confidence": "high" }
        ],
        "social_media": [
          { "type": "twitter", "value": "@yamada", "confidence": "medium" }
        ]
      }
    }
  ]
}
```

## 関連仕様書

- 名刺画像取り込みOCR仕様書 v1.2.1 第5章「OCR結果JSONの仕様」
