"""contacts.services.json_parser のユニットテスト（仕様書 v1.6.0 §5.1 / §5.2 / §6.5 等）。

Phase 3A で全面書き直した json_parser（OCR 8 ブロック構造 → Contact 用辞書変換）を検証する。
純関数なので DB 不要（SimpleTestCase）。
"""

from django.test import SimpleTestCase

from contacts.services.json_parser import (
    calc_orientation_adjusted_confidence_map,
    normalize_to_contact_dict,
)


# ----------------------------------------------------------------------
# 共通ヘルパー：8 ブロックの最小スケルトンを生成
# ----------------------------------------------------------------------

def _make_raw_json(overrides=None):
    """v1.6.0 形式の最小 raw_json を生成する（全フィールド null + confidence='high' 既定）。

    overrides は cards[0] の部分 dict を渡すと該当ブロック / フィールドが上書きされる。
    """
    base_card = {
        "card_meta": {
            "is_business_card": True,
            "orientation": "normal",
        },
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
    if overrides:
        for block_key, block_value in overrides.items():
            if isinstance(block_value, dict) and block_key in base_card and isinstance(base_card[block_key], dict):
                base_card[block_key].update(block_value)
            else:
                base_card[block_key] = block_value
    return {"cards": [base_card]}


# ======================================================================
# 1. 基本構造・8 ブロック処理
# ======================================================================

class NormalizeToContactDictBasicTests(SimpleTestCase):
    """空 + high のフィールドはスキップ、value あれば格納、confidence は橋渡し後名で記録。"""

    def test_all_high_null_yields_empty_dict(self):
        """全フィールド null + high なら contact_dict は実質空（compose_full_address も空）。"""
        contact_dict, confidence_map = normalize_to_contact_dict(_make_raw_json(), 0)
        # full_name は original_script が null なので入らない
        self.assertNotIn("full_name", contact_dict)
        # compose_full_address は 4 要素が空なので空文字 → contact_dict["address"] には入らない
        self.assertNotIn("address", contact_dict)
        # confidence_map は全 high のため空
        self.assertEqual(confidence_map, {})

    def test_scalar_value_stored(self):
        """単一値フィールドは値あり時に格納、confidence が mid/low なら confidence_map に記録。"""
        raw = _make_raw_json({
            "name": {
                "last_name": {"value": "山田", "confidence": "high"},
                "first_name": {"value": "太郎", "confidence": "mid"},
            },
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("last_name"), "山田")
        self.assertEqual(contact_dict.get("first_name"), "太郎")
        # last_name は high なので confidence_map には記録される（_store_scalar は high も記録、
        # 後段で ContactFieldConfidence 側が high を弾く設計）
        self.assertEqual(confidence_map.get("last_name"), "high")
        self.assertEqual(confidence_map.get("first_name"), "mid")


# ======================================================================
# 2. 橋渡し 4 件（仕様書 §3.3 / §5.2）
# ======================================================================

class BridgeMappingTests(SimpleTestCase):
    """org_name_full→organization / full_address→address(compose 経由) / primary_lang→lang /
    original_script→full_name の 4 件の橋渡しが正しく行われるか。
    """

    def test_org_name_full_to_organization(self):
        """org_name_full の値が contact_dict["organization"] に入る。normalize_organization も適用。"""
        raw = _make_raw_json({
            "organization": {
                "org_name_full": {"value": "(株)サンプル", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        # 株式会社系の表記揺れが統一される
        self.assertEqual(contact_dict.get("organization"), "株式会社サンプル")
        # OCR キー名 "org_name_full" は contact_dict に入らない
        self.assertNotIn("org_name_full", contact_dict)

    def test_primary_lang_to_lang(self):
        """primary_lang の値が contact_dict["lang"] に入る。"""
        raw = _make_raw_json({
            "metadata": {
                "primary_lang": {"value": "ja", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("lang"), "ja")
        self.assertNotIn("primary_lang", contact_dict)

    def test_original_script_to_full_name_with_minimal_normalization(self):
        """original_script が full_name にコピーされ、最小限正規化（全角空白→半角、連続空白→1つ、
        前後空白除去）が適用される。大文字小文字は変えない。"""
        raw = _make_raw_json({
            "name": {
                # 全角空白 + 連続空白 + 前後空白
                "original_script": {"value": "  John　 Smith  ", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        # 全角空白 → 半角、連続空白 → 1 つ、前後空白除去。大文字小文字維持
        self.assertEqual(contact_dict.get("full_name"), "John Smith")

    def test_original_script_keeps_case_and_fullwidth_alnum(self):
        """OCR が title case 適用済み前提のため、json_parser は大文字小文字・全角英数字を
        変換しない（normalize_full_name とは別ルール、§3.3.1）。"""
        raw = _make_raw_json({
            "name": {
                "original_script": {"value": "ＪＯＨＮ ｓｍｉｔｈ", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        # 全角英数字 → 半角の強制変換は行わない
        self.assertEqual(contact_dict.get("full_name"), "ＪＯＨＮ ｓｍｉｔｈ")

    def test_full_address_not_used_compose_takes_over(self):
        """OCR の full_address は raw_json に残るが Contact.address には反映されない。
        4 要素から compose_full_address で組み立てられる（§6.4）。"""
        raw = _make_raw_json({
            "address": {
                # OCR full_address に値があっても使われない
                "full_address": {"value": "東京都千代田区丸の内1-1-1 サンプルビル", "confidence": "high"},
                "postal_code":  {"value": "100-0005", "confidence": "high"},
                "region":       {"value": "東京都", "confidence": "high"},
                "city":         {"value": "千代田区", "confidence": "high"},
                "rest_of_address": {"value": "丸の内1-1-1 サンプルビル", "confidence": "high"},
                "country":      {"value": "JP", "confidence": "high"},
            },
            "metadata": {
                "primary_lang": {"value": "ja", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        # ja なら 〒 前置
        self.assertIn("〒", contact_dict.get("address", ""))
        self.assertIn("東京都", contact_dict.get("address", ""))
        # OCR full_address そのままの文字列は使われていない
        self.assertNotEqual(
            contact_dict.get("address"),
            "東京都千代田区丸の内1-1-1 サンプルビル",
        )


# ======================================================================
# 3. 配列フィールド（仕様書 §6.5 末尾「配列のまま JSONField に格納」）
# ======================================================================

class ArrayFieldsTests(SimpleTestCase):
    """personal_phone / personal_fax / org_phone / org_fax は配列のまま格納、
    空要素は除外、空配列は contact_dict に入れない。"""

    def test_personal_phone_kept_as_list(self):
        raw = _make_raw_json({
            "contact": {
                "personal_phone": {
                    "value": ["+819011112222", "+819033334444"],
                    "confidence": "high",
                },
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(
            contact_dict.get("personal_phone"),
            ["+819011112222", "+819033334444"],
        )

    def test_phone_empty_elements_removed(self):
        """normalize_phone_value で空文字になる要素（数字も + もない）は除外。"""
        raw = _make_raw_json({
            "contact": {
                "personal_phone": {
                    "value": ["+819011112222", "", "abc"],
                    "confidence": "high",
                },
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        # "" → 空文字、"abc" → "" の 2 要素は除外。1 要素だけ残る
        self.assertEqual(contact_dict.get("personal_phone"), ["+819011112222"])

    def test_phone_all_empty_yields_no_field(self):
        """全要素が空文字になる場合は contact_dict に入れない（Contact 側 default=list）。"""
        raw = _make_raw_json({
            "contact": {
                "personal_phone": {"value": ["", "abc"], "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertNotIn("personal_phone", contact_dict)

    def test_org_phone_and_org_fax(self):
        """v1.6.0 新規フィールド org_phone / org_fax も同様に配列のまま格納。"""
        raw = _make_raw_json({
            "contact": {
                "org_phone": {"value": ["+81312345678"], "confidence": "mid"},
                "org_fax":   {"value": ["+81312349999"], "confidence": "low"},
            },
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("org_phone"), ["+81312345678"])
        self.assertEqual(contact_dict.get("org_fax"), ["+81312349999"])
        self.assertEqual(confidence_map.get("org_phone"), "mid")
        self.assertEqual(confidence_map.get("org_fax"), "low")

    def test_phone_normalize_applied_per_element(self):
        """normalize_phone_value は要素ごとに適用される。全角数字・ハイフン除去等。"""
        raw = _make_raw_json({
            "contact": {
                "personal_phone": {
                    "value": ["０９０-１１１１-２２２２", "03(1234)5678"],
                    "confidence": "high",
                },
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        # 全角数字 → 半角、ハイフン・カッコ除去
        self.assertEqual(
            contact_dict.get("personal_phone"),
            ["09011112222", "0312345678"],
        )


# ======================================================================
# 4. SNS 分配（論点 A：旧実装踏襲、小文字完全一致のみ採用）
# ======================================================================

class SnsDistributionTests(SimpleTestCase):
    def test_supported_types_distributed(self):
        """twitter / linkedin / facebook / github / instagram は分配される。"""
        raw = _make_raw_json({
            "contact": {
                "sns": {
                    "value": [
                        {"type": "twitter", "id": "@taro"},
                        {"type": "github", "id": "taro-yamada"},
                    ],
                    "confidence": "mid",
                },
            },
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("twitter"), "@taro")
        self.assertEqual(contact_dict.get("github"), "taro-yamada")
        # confidence は配列全体に 1 つ
        self.assertEqual(confidence_map.get("twitter"), "mid")
        self.assertEqual(confidence_map.get("github"), "mid")

    def test_capitalized_or_alias_type_ignored(self):
        """大文字始まり（LinkedIn）や別名（X）は無視される（論点 A：完全一致のみ採用）。"""
        raw = _make_raw_json({
            "contact": {
                "sns": {
                    "value": [
                        {"type": "LinkedIn", "id": "alice"},
                        {"type": "X", "id": "@alice"},
                        {"type": "WeChat", "id": "alice_chat"},
                    ],
                    "confidence": "high",
                },
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertNotIn("linkedin", contact_dict)
        self.assertNotIn("twitter", contact_dict)
        # WeChat 用フィールドは Contact にない

    def test_first_entry_wins_on_duplicate_type(self):
        """同一 type が複数あれば最初の 1 件を採用。"""
        raw = _make_raw_json({
            "contact": {
                "sns": {
                    "value": [
                        {"type": "twitter", "id": "first"},
                        {"type": "twitter", "id": "second"},
                    ],
                    "confidence": "high",
                },
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("twitter"), "first")

    def test_empty_id_ignored(self):
        raw = _make_raw_json({
            "contact": {
                "sns": {
                    "value": [
                        {"type": "twitter", "id": ""},
                        {"type": "github", "id": None},
                    ],
                    "confidence": "high",
                },
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertNotIn("twitter", contact_dict)
        self.assertNotIn("github", contact_dict)


# ======================================================================
# 5. ai_analysis_notes が Contact 用辞書に入らない（仕様書 §1.7）
# ======================================================================

class AiAnalysisNotesExclusionTests(SimpleTestCase):
    def test_ai_analysis_notes_not_mapped(self):
        """metadata.ai_analysis_notes に値があっても contact_dict に入らない。
        Contact 側 notes（ユーザーメモ）にも入らない。"""
        raw = _make_raw_json({
            "metadata": {
                "ai_analysis_notes": {
                    "value": "OCR が氏名の読み取りに迷いました。最有力候補を採用。",
                    "confidence": "low",
                },
            },
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        self.assertNotIn("ai_analysis_notes", contact_dict)
        # Contact 側 notes も別物として残らない
        self.assertNotIn("notes", contact_dict)
        # confidence_map にも入らない
        self.assertNotIn("ai_analysis_notes", confidence_map)
        self.assertNotIn("notes", confidence_map)


# ======================================================================
# 6. name 整合性チェック呼び出し（仕様書 §2.4.1）
# ======================================================================

class NameConsistencyCheckTests(SimpleTestCase):
    """check_name_consistency が呼ばれ、primary_lang を name_block に注入できているか。"""

    def test_name_order_first_last_with_ja_downgrades(self):
        """primary_lang=ja で name_order=first_last は不整合 → name_order が mid に下がる。"""
        raw = _make_raw_json({
            "name": {
                "last_name":  {"value": "山田", "confidence": "high"},
                "first_name": {"value": "太郎", "confidence": "high"},
                "name_order": {"value": "first_last", "confidence": "high"},
            },
            "metadata": {
                "primary_lang": {"value": "ja", "confidence": "high"},
            },
        })
        _, confidence_map = normalize_to_contact_dict(raw, 0)
        # check_name_consistency が mid に下げ、競合ルールで mid が採用される
        self.assertEqual(confidence_map.get("name_order"), "mid")

    def test_salutation_inconsistent_with_last_name_downgrades_to_low(self):
        """primary_lang=ja で salutation_name に last_name が含まれない → salutation_name は low に下がる。"""
        raw = _make_raw_json({
            "name": {
                "last_name":       {"value": "山田", "confidence": "high"},
                "salutation_name": {"value": "鈴木 様", "confidence": "high"},
            },
            "metadata": {
                "primary_lang": {"value": "ja", "confidence": "high"},
            },
        })
        _, confidence_map = normalize_to_contact_dict(raw, 0)
        self.assertEqual(confidence_map.get("salutation_name"), "low")


# ======================================================================
# 7. address 組み立て（compose_full_address）
# ======================================================================

class AddressCompositionTests(SimpleTestCase):
    def test_japanese_address_with_postal_marker(self):
        """ja で postal_code があれば 〒 前置。"""
        raw = _make_raw_json({
            "address": {
                "postal_code":     {"value": "1000005", "confidence": "high"},
                "region":          {"value": "東京都", "confidence": "high"},
                "city":            {"value": "千代田区", "confidence": "high"},
                "rest_of_address": {"value": "丸の内1-1-1", "confidence": "high"},
            },
            "metadata": {"primary_lang": {"value": "ja", "confidence": "high"}},
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(
            contact_dict.get("address"),
            "〒1000005 東京都千代田区丸の内1-1-1",
        )

    def test_non_japanese_address_without_postal_marker(self):
        """ja 以外なら 〒 前置なし。"""
        raw = _make_raw_json({
            "address": {
                "postal_code":     {"value": "10001", "confidence": "high"},
                "city":            {"value": "New York", "confidence": "high"},
                "rest_of_address": {"value": "5th Ave", "confidence": "high"},
            },
            "metadata": {"primary_lang": {"value": "en", "confidence": "high"}},
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertNotIn("〒", contact_dict.get("address", ""))


# ======================================================================
# 8. 派生フィールド org_core_name / org_domain_name（仕様書 §6.5 D カテゴリ）
# ======================================================================

class DerivedFieldsTests(SimpleTestCase):
    def test_org_core_name_derived_when_ocr_empty(self):
        """OCR が org_core_name を出していなければ derive_org_core_name で補完される。"""
        raw = _make_raw_json({
            "organization": {
                "org_name_full":    {"value": "株式会社サンプル", "confidence": "high"},
                "legal_entity_type": {"value": "株式会社", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("org_core_name"), "サンプル")

    def test_org_core_name_ocr_value_kept(self):
        """OCR が org_core_name を出していれば優先採用（derive で上書きしない）。"""
        raw = _make_raw_json({
            "organization": {
                "org_name_full":    {"value": "株式会社サンプル", "confidence": "high"},
                "org_core_name":    {"value": "OCR出力コア名", "confidence": "high"},
                "legal_entity_type": {"value": "株式会社", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("org_core_name"), "OCR出力コア名")

    def test_org_domain_derived_from_email(self):
        """OCR が org_domain_name を出していなければ email から導出される。"""
        raw = _make_raw_json({
            "contact": {"email": {"value": "alice@example.com", "confidence": "high"}},
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("org_domain_name"), "example.com")


# ======================================================================
# 9. salutation_name 二段構え（仕様書 §1.5）
# ======================================================================

class SalutationNameStageOneTests(SimpleTestCase):
    """第 1 段（json_parser）は OCR の salutation_name をそのまま格納、null なら何もしない。
    compute_salutation_name は json_parser から呼ばれない（呼ばれていれば last_name から
    自動組み立てされてしまうが、本テストで Contact.salutation_name が空のままになることを確認）。"""

    def test_ocr_salutation_stored_as_is(self):
        raw = _make_raw_json({
            "name": {
                "salutation_name": {"value": "山田 様", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("salutation_name"), "山田 様")

    def test_ocr_salutation_null_not_computed_in_parser(self):
        """OCR が salutation_name=null を返した場合、json_parser は contact_dict に入れない。
        姓があっても compute_salutation_name は呼ばないため自動組み立てはされない（第 2 段の
        Contact.save() オーバーライドに委ねる）。"""
        raw = _make_raw_json({
            "name": {
                "last_name":       {"value": "山田", "confidence": "high"},
                "salutation_name": {"value": None,  "confidence": "high"},
            },
            "metadata": {"primary_lang": {"value": "ja", "confidence": "high"}},
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        # last_name はある
        self.assertEqual(contact_dict.get("last_name"), "山田")
        # でも salutation_name は contact_dict に入らない（第 2 段が補完する）
        self.assertNotIn("salutation_name", contact_dict)


# ======================================================================
# 10. 防御実装（仕様書 §6.5）
# ======================================================================

class DefensiveImplementationTests(SimpleTestCase):
    """構造想定外でも例外を投げず confidence=low に倒す。"""

    def test_block_not_dict_falls_back(self):
        """name ブロックが dict でない場合、各フィールドは空文字で格納されるが
        confidence_map には記録されない（旧 json_normalizer 踏襲の挙動）。"""
        raw = {"cards": [{"name": "not a dict"}]}
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        # 空文字でフォールバック格納される（Contact 側 default="" と等価、害なし）
        self.assertEqual(contact_dict.get("last_name"), "")
        # full_name は original_script が空 → normalize_original_script_for_full_name で
        # 空文字を返し、if full_name_value: でスキップされるため入らない
        self.assertNotIn("full_name", contact_dict)
        # confidence_map には情報なし扱いで入らない（旧実装踏襲）
        self.assertNotIn("last_name", confidence_map)

    def test_value_field_not_dict_falls_back_to_low(self):
        """個別フィールドが {value, confidence} ペアでなく文字列のみ → low に倒す。"""
        raw = _make_raw_json({
            "name": {
                "last_name": "not a dict",  # 想定外：文字列直接
            },
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        # value は空文字に倒すが、confidence=low なので contact_dict に入る
        self.assertEqual(contact_dict.get("last_name"), "")
        self.assertEqual(confidence_map.get("last_name"), "low")

    def test_confidence_invalid_falls_back_to_low(self):
        """confidence が想定外の値（'medium' 等）→ low に倒す。"""
        raw = _make_raw_json({
            "name": {
                "last_name": {"value": "山田", "confidence": "medium"},
            },
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        self.assertEqual(contact_dict.get("last_name"), "山田")
        self.assertEqual(confidence_map.get("last_name"), "low")

    def test_card_not_dict_falls_back_safely(self):
        """cards[0] が dict でない場合、空 dict に倒して処理続行（例外を投げない）。
        各フィールドは空文字でフォールバック格納されるが confidence_map は空（旧実装踏襲）。"""
        raw = {"cards": ["not a dict"]}
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        # 例外を投げず、空文字フォールバックで全フィールド入る（害なし、Contact default="" 等価）
        # 重要なのは「confidence_map が空」（mid/low の汚染がない）こと
        self.assertEqual(confidence_map, {})
        # full_name は入らない（original_script が空のため）
        self.assertNotIn("full_name", contact_dict)
        # address は compose_full_address の組み立て結果が空のため入らない
        self.assertNotIn("address", contact_dict)

    def test_array_value_not_list_yields_no_field(self):
        """配列フィールドの value が list でない場合、フィールド未格納。"""
        raw = _make_raw_json({
            "contact": {
                "personal_phone": {"value": "not a list", "confidence": "high"},
            },
        })
        contact_dict, _ = normalize_to_contact_dict(raw, 0)
        self.assertNotIn("personal_phone", contact_dict)


# ======================================================================
# 11. card_index の ValueError（仕様書 §5.1 例外規則）
# ======================================================================

class CardIndexErrorTests(SimpleTestCase):
    def test_card_index_negative(self):
        raw = _make_raw_json()
        with self.assertRaises(ValueError):
            normalize_to_contact_dict(raw, -1)

    def test_card_index_out_of_range(self):
        raw = _make_raw_json()
        with self.assertRaises(ValueError):
            normalize_to_contact_dict(raw, 5)

    def test_cards_not_list(self):
        with self.assertRaises(ValueError):
            normalize_to_contact_dict({"cards": "not a list"}, 0)

    def test_raw_json_not_dict(self):
        with self.assertRaises(ValueError):
            normalize_to_contact_dict("not a dict", 0)

    def test_raw_json_none(self):
        with self.assertRaises(ValueError):
            normalize_to_contact_dict(None, 0)


# ======================================================================
# 12. calc_orientation_adjusted_confidence_map（仕様書 §5.3）
# ======================================================================

class OrientationAdjustmentTests(SimpleTestCase):
    def test_normal_no_adjustment(self):
        """normal なら confidence_map をコピーしてそのまま返す。"""
        result = calc_orientation_adjusted_confidence_map(
            {"last_name": "山田"}, {"last_name": "mid"}, "normal"
        )
        self.assertEqual(result, {"last_name": "mid"})

    def test_rotate_90_cw_high_to_mid(self):
        result = calc_orientation_adjusted_confidence_map(
            {"last_name": "山田"}, {"last_name": "high"}, "rotate_90_cw"
        )
        self.assertEqual(result.get("last_name"), "mid")

    def test_rotate_90_ccw_low_stays_low(self):
        result = calc_orientation_adjusted_confidence_map(
            {"last_name": "山田"}, {"last_name": "low"}, "rotate_90_ccw"
        )
        self.assertEqual(result.get("last_name"), "low")

    def test_rotate_180_high_to_low(self):
        result = calc_orientation_adjusted_confidence_map(
            {"last_name": "山田"}, {"last_name": "high"}, "rotate_180"
        )
        self.assertEqual(result.get("last_name"), "low")

    def test_mirror_mid_to_low(self):
        result = calc_orientation_adjusted_confidence_map(
            {"last_name": "山田"}, {"last_name": "mid"}, "mirror"
        )
        self.assertEqual(result.get("last_name"), "low")

    def test_empty_value_skipped(self):
        """値が空のフィールドは補正対象外。"""
        result = calc_orientation_adjusted_confidence_map(
            {"last_name": ""}, {"last_name": "high"}, "rotate_90_cw"
        )
        self.assertNotIn("last_name", result)

    def test_missing_confidence_treated_as_high(self):
        """confidence_map に該当キーがない場合は high 扱い → orientation 補正で下がる。"""
        result = calc_orientation_adjusted_confidence_map(
            {"last_name": "山田"}, {}, "rotate_90_cw"
        )
        # high → mid に下がるため記録される
        self.assertEqual(result.get("last_name"), "mid")


# ======================================================================
# 13. 競合ルール（仕様書 §5.3.1 累積で最も低い側に収束）
# ======================================================================

class ConflictRuleTests(SimpleTestCase):
    """name 整合性チェック補正と orientation 補正の両方が同一フィールドに適用される場合、
    最終的に最低 confidence に収束する。"""

    def test_consistency_low_then_orientation_180_stays_low(self):
        """整合性チェックで salutation_name が low に下がった後、orientation 補正で更に下がる
        余地がないため low のまま。"""
        raw = _make_raw_json({
            "name": {
                "last_name":       {"value": "山田", "confidence": "high"},
                "salutation_name": {"value": "鈴木 様", "confidence": "high"},
            },
            "metadata": {"primary_lang": {"value": "ja", "confidence": "high"}},
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        # 整合性チェックで salutation_name は low
        self.assertEqual(confidence_map.get("salutation_name"), "low")
        # orientation=rotate_180 を重ねても low のまま
        adjusted = calc_orientation_adjusted_confidence_map(
            contact_dict, confidence_map, "rotate_180"
        )
        self.assertEqual(adjusted.get("salutation_name"), "low")

    def test_orientation_180_then_high_field_falls_to_low(self):
        """整合性チェック対象外フィールド（例：title）でも、orientation 補正単体で
        high → low に下がる。"""
        raw = _make_raw_json({
            "job": {
                "title": {"value": "部長", "confidence": "high"},
            },
        })
        contact_dict, confidence_map = normalize_to_contact_dict(raw, 0)
        adjusted = calc_orientation_adjusted_confidence_map(
            contact_dict, confidence_map, "rotate_180"
        )
        self.assertEqual(adjusted.get("title"), "low")
