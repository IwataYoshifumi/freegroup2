"""電話の二重検算（仕様書 §2.4）のテスト（Phase C-追加）。

validate_phone_against_country の純関数ユニットテストと、ocr_pipeline._determine_ocr_result
経由で confidence 補正が行われる統合テスト。いずれも DB 非接触のため SimpleTestCase。
"""

from django.test import SimpleTestCase

from cards.models import BusinessCard
from cards.tasks.ocr_pipeline import _determine_ocr_result
from contacts.services.normalization import validate_phone_against_country


class ValidatePhoneAgainstCountryTests(SimpleTestCase):
    def test_match_jp_e164(self):
        self.assertTrue(validate_phone_against_country("+819012345678", "JP"))

    def test_match_jp_digits_only(self):
        # normalize_phone_value 後（+ 除去済み）でも前方一致で成立
        self.assertTrue(validate_phone_against_country("819012345678", "JP"))

    def test_mismatch_us_phone_jp_country(self):
        self.assertFalse(validate_phone_against_country("+12025550123", "JP"))

    def test_skip_country_zz(self):
        self.assertTrue(validate_phone_against_country("+819012345678", "ZZ"))

    def test_skip_phone_empty(self):
        self.assertTrue(validate_phone_against_country("", "JP"))
        self.assertTrue(validate_phone_against_country(None, "JP"))

    def test_skip_country_empty(self):
        self.assertTrue(validate_phone_against_country("+819012345678", ""))
        self.assertTrue(validate_phone_against_country("+819012345678", None))

    def test_unknown_calling_code_is_mismatch(self):
        # 国番号 999 等は JP(81) と前方一致しない → 不一致
        self.assertFalse(validate_phone_against_country("+9991234567", "JP"))

    def test_country_not_in_mapping_skips(self):
        # マッピング外の country は検算不能扱い → True
        self.assertTrue(validate_phone_against_country("+819012345678", "XK"))

    def test_no_digits_skips(self):
        self.assertTrue(validate_phone_against_country("+++", "JP"))


def _E(value, conf="high"):
    return {"value": value, "confidence": conf}


def _build_card(*, country="JP", contact_override=None, primary_lang="ja"):
    """二重検算統合テスト用の妥当な 8 ブロック card を作る。"""
    contact = {
        "email": _E("taro@sample.co.jp"),
        "mobile_phone": _E(None),
        "personal_phone": _E(None),
        "personal_fax": _E(None),
        "org_phone": _E(None),
        "org_fax": _E(None),
        "sns": _E([]),
    }
    if contact_override:
        contact.update(contact_override)
    return {
        "card_meta": {"is_business_card": True, "orientation": "normal"},
        "name": {
            "original_script": _E("山田 太郎"),
            "last_name": _E("山田"),
            "first_name": _E("太郎"),
            "other_name_parts": _E(None),
            "name_order": _E("last_first"),
            "salutation_name": _E(None),
            "display_name": _E("山田 太郎"),
            "phonetic_name": _E(None),
            "alias_name": _E(None),
        },
        "job": {k: _E(None) for k in ("title", "qualification", "department", "catchphrase")},
        "organization": {
            "org_name_full": _E("株式会社サンプル"),
            "org_core_name": _E(None),
            "legal_entity_type": _E(None),
            "legal_entity_type_code": _E(None),
            "legal_entity_type_position": _E(None),
            "org_domain_name": _E(None),
            "branch": _E(None),
            "website": _E(None),
        },
        "address": {
            "full_address": _E(None),
            "postal_code": _E(None),
            "country": _E(country),
            "region": _E(None),
            "city": _E(None),
            "rest_of_address": _E(None),
        },
        "contact": contact,
        "uncategorized_card_content": {"handwritten_text": _E(None), "other_printed_text": _E(None)},
        "metadata": {
            "primary_lang": _E(primary_lang),
            "language_composition": _E("local_only"),
            "ai_analysis_notes": _E(None),
        },
    }


class PhoneDualCheckPipelineTests(SimpleTestCase):
    """_determine_ocr_result 経由で電話二重検算が confidence_map を補正することの統合テスト。"""

    def _run(self, card):
        raw = {"cards": [card], "api_response": {}}
        return _determine_ocr_result(raw, "normal")

    def test_us_phone_jp_country_downgraded_to_low(self):
        card = _build_card(country="JP", contact_override={"mobile_phone": _E("+12025550123")})
        result, cd, cm, _ = self._run(card)
        self.assertEqual(result, BusinessCard.OcrResult.BUSINESS_CARD)
        self.assertEqual(cm.get("mobile_phone"), "low")

    def test_jp_phone_jp_country_not_corrected(self):
        # 一致 → 補正なし。OCR high は confidence_map に載らない（low/mid のみ記録）
        card = _build_card(country="JP", contact_override={"mobile_phone": _E("+819012345678")})
        _, _, cm, _ = self._run(card)
        self.assertNotEqual(cm.get("mobile_phone"), "low")
        self.assertNotIn("mobile_phone", cm)

    def test_match_does_not_upgrade_existing_low(self):
        # 一致でも OCR が low を付けていれば low のまま（補正は下げ方向のみ・上げない）
        card = _build_card(
            country="JP", contact_override={"mobile_phone": _E("+819012345678", "low")}
        )
        _, _, cm, _ = self._run(card)
        self.assertEqual(cm.get("mobile_phone"), "low")

    def test_country_zz_skips_dualcheck(self):
        # country=ZZ なら検算スキップ → US 電話でも補正されない（high のまま＝map に載らない）
        card = _build_card(country="ZZ", contact_override={"mobile_phone": _E("+12025550123")})
        _, _, cm, _ = self._run(card)
        self.assertNotIn("mobile_phone", cm)

    def test_per_field_only_mismatched_field_downgraded(self):
        # org_phone は一致(JP)、personal_phone は不一致(US) → personal_phone だけ low
        card = _build_card(
            country="JP",
            contact_override={
                "org_phone": _E("+81565321033"),
                "personal_phone": _E("+12025550123"),
            },
        )
        _, _, cm, _ = self._run(card)
        self.assertEqual(cm.get("personal_phone"), "low")
        self.assertNotIn("org_phone", cm)  # 一致フィールドは波及しない
