"""docs/json_schema/v1.6.0/combined_response.json の検証テスト（Phase 3B 新設）。

仕様書「OpenCV・OCR仕様書v1.6.0（Claude.API）統合版」§1.2 / §1.3 / §6.5 の確定 JSON 構造
（8 ブロック・confidence high/mid/low・配列フィールド・enum 規格）と完全一致していること、
および ocr_pipeline._get_card_item_schema() / OcrService._load_schema() が
v1.6.0 のパスから読み込めることを検証する。
"""

import copy
import json
from pathlib import Path

import jsonschema
from django.conf import settings
from django.test import SimpleTestCase

from cards.tasks.ocr_pipeline import _get_card_item_schema
from cards.tasks.ocr_service import OcrService


# ----------------------------------------------------------------------
# 仕様書 §1.2 の最小 raw_json（全フィールド null + high）を組み立てる
# ----------------------------------------------------------------------

def _make_minimal_card():
    """8 ブロックすべて null + confidence='high'（配列は空 + high）の最小カード。"""
    return {
        "card_meta": {"is_business_card": True, "orientation": "normal"},
        "name": {
            "original_script":  {"value": None, "confidence": "high"},
            "last_name":        {"value": None, "confidence": "high"},
            "first_name":       {"value": None, "confidence": "high"},
            "other_name_parts": {"value": None, "confidence": "high"},
            "name_order":       {"value": None, "confidence": "high"},
            "salutation_name":  {"value": None, "confidence": "high"},
            "display_name":     {"value": None, "confidence": "high"},
            "phonetic_name":    {"value": None, "confidence": "high"},
            "alias_name":       {"value": None, "confidence": "high"},
        },
        "job": {
            "title":         {"value": None, "confidence": "high"},
            "qualification": {"value": None, "confidence": "high"},
            "department":    {"value": None, "confidence": "high"},
            "catchphrase":   {"value": None, "confidence": "high"},
        },
        "organization": {
            "org_name_full":              {"value": None, "confidence": "high"},
            "org_core_name":              {"value": None, "confidence": "high"},
            "legal_entity_type":          {"value": None, "confidence": "high"},
            "legal_entity_type_code":     {"value": None, "confidence": "high"},
            "legal_entity_type_position": {"value": None, "confidence": "high"},
            "org_domain_name":            {"value": None, "confidence": "high"},
            "branch":                     {"value": None, "confidence": "high"},
        },
        "address": {
            "full_address":    {"value": None, "confidence": "high"},
            "postal_code":     {"value": None, "confidence": "high"},
            "country":         {"value": None, "confidence": "high"},
            "region":          {"value": None, "confidence": "high"},
            "city":            {"value": None, "confidence": "high"},
            "rest_of_address": {"value": None, "confidence": "high"},
        },
        "contact": {
            "email":          {"value": None, "confidence": "high"},
            "mobile_phone":   {"value": None, "confidence": "high"},
            "personal_phone": {"value": [],   "confidence": "high"},
            "personal_fax":   {"value": [],   "confidence": "high"},
            "org_phone":      {"value": [],   "confidence": "high"},
            "org_fax":        {"value": [],   "confidence": "high"},
            "sns":            {"value": [],   "confidence": "high"},
        },
        "uncategorized_card_content": {
            "handwritten_text":   {"value": None, "confidence": "high"},
            "other_printed_text": {"value": None, "confidence": "high"},
        },
        "metadata": {
            "primary_lang":         {"value": None, "confidence": "high"},
            "language_composition": {"value": None, "confidence": "high"},
            "ai_analysis_notes":    {"value": None, "confidence": "high"},
        },
    }


def _load_full_schema():
    """v1.6.0 の combined_response.json をそのまま読み込んで返す。"""
    schema_path = (
        Path(settings.BASE_DIR)
        / "docs"
        / "json_schema"
        / "v1.6.0"
        / "combined_response.json"
    )
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


# ======================================================================
# 1. スキーマファイル自体の検証
# ======================================================================

class SchemaFileBasicsTests(SimpleTestCase):
    """combined_response.json のメタ情報・トップレベル構造。"""

    def test_schema_file_loads(self):
        schema = _load_full_schema()
        self.assertIsInstance(schema, dict)

    def test_schema_version_const_is_1_6_0(self):
        schema = _load_full_schema()
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.6.0")

    def test_confidence_enum_is_high_mid_low(self):
        """v1.6.0 で medium → mid に統一されていること。"""
        schema = _load_full_schema()
        self.assertEqual(
            schema["$defs"]["Confidence"]["enum"], ["high", "mid", "low"]
        )

    def test_orientation_enum_5_values(self):
        schema = _load_full_schema()
        orientation_enum = (
            schema["properties"]["cards"]["items"]["properties"]["card_meta"]
            ["properties"]["orientation"]["enum"]
        )
        self.assertEqual(
            set(orientation_enum),
            {"normal", "rotate_180", "rotate_90_cw", "rotate_90_ccw", "mirror"},
        )

    def test_top_level_required(self):
        schema = _load_full_schema()
        self.assertEqual(
            set(schema["required"]),
            {"schema_version", "ocr_meta", "api_response", "cards"},
        )


# ======================================================================
# 2. 8 ブロック構造の検証
# ======================================================================

class CardItemBlocksTests(SimpleTestCase):
    """cards items が 8 ブロックすべてを required で持つこと。"""

    def test_card_item_has_8_blocks(self):
        schema = _load_full_schema()
        card_item = schema["properties"]["cards"]["items"]
        self.assertEqual(
            set(card_item["required"]),
            {
                "card_meta", "name", "job", "organization", "address",
                "contact", "uncategorized_card_content", "metadata",
            },
        )

    def test_additional_properties_false_on_card_item(self):
        """OCR が余計なフィールドを返したら schema 検証で弾かれる厳格運用。"""
        schema = _load_full_schema()
        card_item = schema["properties"]["cards"]["items"]
        self.assertFalse(card_item["additionalProperties"])

    def test_name_block_has_9_fields(self):
        """name ブロックは original_script + 構成要素・派生 8 フィールド = 9 件。
        full_name は OCR 出力に含めない（v1.6.0 §3.4）。"""
        schema = _load_full_schema()
        name_block = (
            schema["properties"]["cards"]["items"]["properties"]["name"]
        )
        self.assertEqual(
            set(name_block["required"]),
            {
                "original_script", "last_name", "first_name", "other_name_parts",
                "name_order", "salutation_name", "display_name", "phonetic_name",
                "alias_name",
            },
        )
        # full_name は含まれない
        self.assertNotIn("full_name", name_block["properties"])

    def test_contact_block_has_array_fields(self):
        """personal_phone / personal_fax / org_phone / org_fax / sns は配列形式
        （value が array）。"""
        schema = _load_full_schema()
        contact_block = (
            schema["properties"]["cards"]["items"]["properties"]["contact"]
        )
        for array_field in (
            "personal_phone", "personal_fax", "org_phone", "org_fax"
        ):
            ref = contact_block["properties"][array_field]["$ref"]
            self.assertEqual(ref, "#/$defs/StringArrayValue")
        # sns は別 $ref（type + id の object 配列）
        self.assertEqual(
            contact_block["properties"]["sns"]["$ref"],
            "#/$defs/SnsArrayValue",
        )

    def test_metadata_block_contains_ai_analysis_notes(self):
        """ai_analysis_notes は metadata ブロックに必須で含まれる（OCR 出力には残る。
        Contact 用辞書には入らないが、それは json_parser 側の責務）。"""
        schema = _load_full_schema()
        metadata_block = (
            schema["properties"]["cards"]["items"]["properties"]["metadata"]
        )
        self.assertIn("ai_analysis_notes", metadata_block["required"])


# ======================================================================
# 3. enum 規格の検証
# ======================================================================

class EnumValuesTests(SimpleTestCase):
    def test_name_order_enum(self):
        schema = _load_full_schema()
        enum = schema["$defs"]["NameOrderValue"]["properties"]["value"]["enum"]
        # null も許容
        self.assertIn(None, enum)
        for v in ("last_first", "first_last", "single", "other"):
            self.assertIn(v, enum)

    def test_legal_entity_type_code_enum(self):
        schema = _load_full_schema()
        enum = (
            schema["$defs"]["LegalEntityTypeCodeValue"]
            ["properties"]["value"]["enum"]
        )
        for v in ("CP", "LLP", "GOV", "NPO", "REL", "EDU", "MED", "PRO", "IND", "OTH"):
            self.assertIn(v, enum)
        self.assertIn(None, enum)

    def test_primary_lang_enum(self):
        schema = _load_full_schema()
        enum = schema["$defs"]["PrimaryLangValue"]["properties"]["value"]["enum"]
        self.assertEqual(set(v for v in enum if v is not None), {"ja", "en", "zh", "und"})
        self.assertIn(None, enum)

    def test_language_composition_enum(self):
        schema = _load_full_schema()
        enum = (
            schema["$defs"]["LanguageCompositionValue"]
            ["properties"]["value"]["enum"]
        )
        self.assertEqual(
            set(v for v in enum if v is not None),
            {"local_only", "english_only", "mix_bilingual", "other"},
        )
        self.assertIn(None, enum)


# ======================================================================
# 4. _get_card_item_schema() / OcrService._load_schema() のパス検証
# ======================================================================

class SchemaLoaderPathTests(SimpleTestCase):
    """ocr_pipeline._get_card_item_schema() と OcrService._load_schema() が
    v1.6.0 スキーマを正しく読めること。"""

    def test_card_item_schema_loadable(self):
        """_get_card_item_schema は cards/items を 8 ブロック構造として返す。"""
        # キャッシュ汚染を避けるためモジュール変数をリセット
        import cards.tasks.ocr_pipeline as ocr_pipeline
        ocr_pipeline._card_item_schema_cache = None
        schema = _get_card_item_schema()
        self.assertEqual(
            set(schema["required"]),
            {
                "card_meta", "name", "job", "organization", "address",
                "contact", "uncategorized_card_content", "metadata",
            },
        )

    def test_ocr_service_load_schema_loadable(self):
        """OcrService._load_schema は v1.6.0 schema 全体を読み込める。"""
        svc = OcrService()
        # 直接 _load_schema を呼ぶ（_schema_cache はインスタンスごとに初期化済み）
        schema = svc._load_schema()
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.6.0")

    def test_ocr_service_tool_input_schema_loadable(self):
        """OcrService._load_tool_input_schema は cards 部分を抽出した形を返す。"""
        svc = OcrService()
        tool_schema = svc._load_tool_input_schema()
        self.assertEqual(tool_schema["type"], "object")
        self.assertEqual(tool_schema["required"], ["cards"])
        self.assertIn("cards", tool_schema["properties"])


# ======================================================================
# 5. 仕様書 §1.2 の最小 card データが v1.6.0 schema で validate を通る
# ======================================================================

class MinimalCardValidationTests(SimpleTestCase):
    """仕様書 §1.2 の確定 JSON 構造（全 null + high）が v1.6.0 schema で
    validate を通ることを検証。"""

    def setUp(self):
        # キャッシュ汚染を避ける
        import cards.tasks.ocr_pipeline as ocr_pipeline
        ocr_pipeline._card_item_schema_cache = None
        self.schema = _get_card_item_schema()

    def test_minimal_card_passes(self):
        card = _make_minimal_card()
        # 例外を投げずに通ればOK
        jsonschema.validate(instance=card, schema=self.schema)

    def test_filled_card_passes(self):
        """値が入った card も validate を通る。"""
        card = _make_minimal_card()
        card["name"]["original_script"] = {"value": "山田 太郎", "confidence": "high"}
        card["name"]["last_name"] = {"value": "山田", "confidence": "mid"}
        card["organization"]["org_name_full"] = {
            "value": "株式会社サンプル", "confidence": "high",
        }
        card["organization"]["legal_entity_type_code"] = {
            "value": "CP", "confidence": "high",
        }
        card["address"]["country"] = {"value": "JP", "confidence": "high"}
        card["contact"]["email"] = {
            "value": "taro@example.com", "confidence": "high",
        }
        card["contact"]["personal_phone"] = {
            "value": ["+819012345678"], "confidence": "high",
        }
        card["contact"]["sns"] = {
            "value": [{"type": "twitter", "id": "@taro"}], "confidence": "high",
        }
        card["metadata"]["primary_lang"] = {"value": "ja", "confidence": "high"}
        jsonschema.validate(instance=card, schema=self.schema)


# ======================================================================
# 6. 異常系：仕様書から逸脱したデータは弾かれる
# ======================================================================

class InvalidCardRejectionTests(SimpleTestCase):
    """v1.6.0 schema が想定外データを正しく弾くこと（厳格運用）。"""

    def setUp(self):
        import cards.tasks.ocr_pipeline as ocr_pipeline
        ocr_pipeline._card_item_schema_cache = None
        self.schema = _get_card_item_schema()

    def test_medium_confidence_rejected(self):
        """v1.4.x までの 'medium' は v1.6.0 で弾かれる（mid 統一）。"""
        card = _make_minimal_card()
        card["name"]["last_name"] = {"value": "山田", "confidence": "medium"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)

    def test_unknown_orientation_rejected(self):
        card = _make_minimal_card()
        card["card_meta"]["orientation"] = "upside_down"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)

    def test_extra_block_rejected(self):
        """additionalProperties: false により未知ブロックは弾かれる。"""
        card = _make_minimal_card()
        card["extra_block"] = {"foo": "bar"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)

    def test_missing_block_rejected(self):
        card = _make_minimal_card()
        del card["metadata"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)

    def test_personal_phone_string_value_rejected(self):
        """personal_phone は array、文字列単体は弾かれる（v1.6.0 で型変更）。"""
        card = _make_minimal_card()
        card["contact"]["personal_phone"] = {
            "value": "+819012345678", "confidence": "high",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)

    def test_invalid_legal_entity_type_code_rejected(self):
        card = _make_minimal_card()
        card["organization"]["legal_entity_type_code"] = {
            "value": "XYZ", "confidence": "high",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)

    def test_invalid_primary_lang_rejected(self):
        card = _make_minimal_card()
        card["metadata"]["primary_lang"] = {"value": "fr", "confidence": "high"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)

    def test_sns_item_missing_id_rejected(self):
        card = _make_minimal_card()
        card["contact"]["sns"] = {
            "value": [{"type": "twitter"}], "confidence": "high",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=card, schema=self.schema)


# ======================================================================
# 7. OcrService.SCHEMA_VERSION が v1.6.0 に更新されていること
# ======================================================================

class OcrServiceSchemaVersionTests(SimpleTestCase):
    def test_schema_version_constant(self):
        self.assertEqual(OcrService.SCHEMA_VERSION, "1.6.0")
