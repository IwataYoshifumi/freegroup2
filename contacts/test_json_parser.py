"""contacts/services/json_parser.py の単体テスト（Phase C、8 ブロック構造）。

純関数（normalize_to_contact_dict / calc_orientation_adjusted_confidence_map /
extract_sns_entries）は SimpleTestCase、ContactSns 作成（create_sns_records_for_contact）は
実 DB を使うため TestCase。実 OCR 出力ではなくテストフィクスチャの 8 ブロック JSON を使う。
"""

from django.test import SimpleTestCase, TestCase

from contacts.models import Contact, ContactSns
from contacts.services.json_parser import (
    calc_orientation_adjusted_confidence_map,
    create_sns_records_for_contact,
    extract_sns_entries,
    normalize_to_contact_dict,
)
from persons.models import Person


def E(value, conf="high"):
    """{value, confidence} エントリを作るテストヘルパー。"""
    return {"value": value, "confidence": conf}


_NAME_KEYS = (
    "original_script",
    "last_name",
    "first_name",
    "other_name_parts",
    "name_order",
    "salutation_name",
    "display_name",
    "phonetic_name",
    "alias_name",
)
_JOB_KEYS = ("title", "qualification", "department", "catchphrase")
_ORG_KEYS = (
    "org_name_full",
    "org_core_name",
    "legal_entity_type",
    "legal_entity_type_code",
    "legal_entity_type_position",
    "org_domain_name",
    "branch",
    "website",
)
_ADDR_KEYS = ("full_address", "postal_code", "country", "region", "city", "rest_of_address")
_CONTACT_KEYS = (
    "email",
    "mobile_phone",
    "personal_phone",
    "personal_fax",
    "org_phone",
    "org_fax",
)
_UNCAT_KEYS = ("handwritten_text", "other_printed_text")
_META_KEYS = ("primary_lang", "language_composition", "ai_analysis_notes")


def build_card(**overrides):
    """全フィールド null/high の 8 ブロック card を作り、ブロック名キーの dict で上書きする。

    overrides 例：build_card(name={"last_name": E("山田")}, metadata={"primary_lang": E("ja")})
    """
    card = {
        "card_meta": {"is_business_card": E(True), "orientation": E("normal")},
        "name": {k: E(None) for k in _NAME_KEYS},
        "job": {k: E(None) for k in _JOB_KEYS},
        "organization": {k: E(None) for k in _ORG_KEYS},
        "address": {k: E(None) for k in _ADDR_KEYS},
        "contact": {**{k: E(None) for k in _CONTACT_KEYS}, "sns": E([])},
        "uncategorized_card_content": {k: E(None) for k in _UNCAT_KEYS},
        "metadata": {k: E(None) for k in _META_KEYS},
    }
    for block_name, override in overrides.items():
        card[block_name].update(override)
    return card


def wrap(card):
    return {"cards": [card], "api_response": {}}


class NormalizeTypicalJapaneseTests(SimpleTestCase):
    def setUp(self):
        self.card = build_card(
            name={
                "original_script": E("山田 太郎"),
                "last_name": E("山田"),
                "first_name": E("太郎"),
                "name_order": E("last_first"),
                "salutation_name": E("山田 様"),
            },
            organization={
                "org_name_full": E("㈱ネットワーク東海"),
                "legal_entity_type": E("株式会社"),
                "legal_entity_type_code": E("CP"),
                "legal_entity_type_position": E("Pre"),
            },
            address={
                "postal_code": E("471-0001"),
                "country": E("JP"),
                "region": E("愛知県"),
                "city": E("豊田市"),
                "rest_of_address": E("1丁目2番地3号"),
            },
            contact={
                "email": E("taro@example.co.jp"),
                "mobile_phone": E("090-1234-5678"),
            },
            metadata={"primary_lang": E("ja"), "language_composition": E("local_only")},
        )
        self.cd, self.cm = normalize_to_contact_dict(wrap(self.card), 0)

    def test_full_name_copied_from_original_script(self):
        self.assertEqual(self.cd["full_name"], "山田 太郎")

    def test_name_components(self):
        self.assertEqual(self.cd["last_name"], "山田")
        self.assertEqual(self.cd["first_name"], "太郎")
        self.assertEqual(self.cd["name_order"], "last_first")
        self.assertEqual(self.cd["salutation_name"], "山田 様")

    def test_organization_normalized(self):
        self.assertEqual(self.cd["organization"], "株式会社ネットワーク東海")
        self.assertEqual(self.cd["org_core_name"], "ネットワーク東海")

    def test_address_composed(self):
        self.assertEqual(self.cd["postal_code"], "4710001")
        self.assertEqual(self.cd["rest_of_address"], "1-2-3")
        self.assertEqual(self.cd["country"], "JP")
        self.assertEqual(self.cd["address"], "〒4710001 愛知県豊田市1-2-3")

    def test_contact_normalized_and_derived(self):
        self.assertEqual(self.cd["email"], "taro@example.co.jp")
        self.assertEqual(self.cd["mobile_phone"], "09012345678")
        self.assertEqual(self.cd["org_domain_name"], "example.co.jp")

    def test_lang_and_all_high_means_empty_confidence_map(self):
        self.assertEqual(self.cd["lang"], "ja")
        self.assertEqual(self.cm, {})


class NormalizeTypicalForeignTests(SimpleTestCase):
    def test_foreign_card(self):
        card = build_card(
            name={
                "original_script": E("John Smith"),
                "last_name": E("Smith"),
                "first_name": E("John"),
                "name_order": E("first_last"),
                "salutation_name": E("Dear John Smith,"),
            },
            contact={"email": E("john@example.com")},
            metadata={"primary_lang": E("en")},
        )
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cd["full_name"], "John Smith")
        self.assertEqual(cd["lang"], "en")
        self.assertEqual(cd["name_order"], "first_last")
        self.assertEqual(cd["org_domain_name"], "example.com")
        self.assertEqual(cm, {})


class DefenseGranularityTests(SimpleTestCase):
    def test_granularity_a_missing_block(self):
        # name ブロックごと欠落 → 全 name フィールド空 + low
        card = build_card()
        del card["name"]
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cd["last_name"], "")
        self.assertEqual(cm["last_name"], "low")
        self.assertEqual(cm["full_name"], "low")

    def test_granularity_b_non_pair_field(self):
        # フィールドが {value,confidence} ペアでない → 空 + low
        card = build_card(name={"last_name": "山田"})  # dict でなく素の文字列
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cd["last_name"], "")
        self.assertEqual(cm["last_name"], "low")

    def test_granularity_c_enum_out_of_range(self):
        card = build_card(
            name={"name_order": E("weird_value")},
            organization={
                "legal_entity_type_code": E("ZZZ"),
                "legal_entity_type_position": E("sideways"),
            },
            metadata={
                "primary_lang": E("xx"),
                "language_composition": E("nonsense"),
            },
        )
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cd["name_order"], "other")
        self.assertEqual(cm["name_order"], "low")
        self.assertEqual(cd["legal_entity_type_code"], "OTH")
        self.assertEqual(cm["legal_entity_type_code"], "low")
        self.assertEqual(cd["legal_entity_type_position"], "other")
        self.assertEqual(cm["legal_entity_type_position"], "low")
        self.assertEqual(cd["lang"], "und")
        self.assertEqual(cm["lang"], "low")
        self.assertEqual(cd["language_composition"], "other")
        self.assertEqual(cm["language_composition"], "low")


class CountryResolutionTests(SimpleTestCase):
    def test_null_country_becomes_zz_high(self):
        card = build_card(address={"country": E(None)})
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cd["country"], "ZZ")
        self.assertNotIn("country", cm)  # high は記録しない

    def test_invalid_country_becomes_zz_low(self):
        card = build_card(address={"country": E("XX")})
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cd["country"], "ZZ")
        self.assertEqual(cm["country"], "low")

    def test_valid_country_kept(self):
        card = build_card(address={"country": E("us")})  # 小文字も大文字化して許容
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cd["country"], "US")


class ConfidenceCompositionTests(SimpleTestCase):
    def test_name_consistency_lowers_confidence(self):
        # primary_lang=ja で salutation が姓と不整合 → check_name_consistency が salutation を下げる
        card = build_card(
            name={
                "original_script": E("山田太郎"),
                "last_name": E("山田"),
                "first_name": E("太郎"),
                "name_order": E("last_first"),
                "salutation_name": E("鈴木 様"),
            },
            metadata={"primary_lang": E("ja")},
        )
        _, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cm["salutation_name"], "mid")  # high→mid

    def test_orientation_and_name_consistency_take_lower(self):
        # name 整合性で salutation_name=mid、orientation 180 で high→low。低い側 low を採用
        card = build_card(
            name={
                "original_script": E("山田太郎"),
                "last_name": E("山田"),
                "first_name": E("太郎"),
                "name_order": E("last_first"),
                "salutation_name": E("鈴木 様"),
            },
            metadata={"primary_lang": E("ja")},
        )
        cd, cm = normalize_to_contact_dict(wrap(card), 0)
        self.assertEqual(cm["salutation_name"], "mid")
        adjusted = calc_orientation_adjusted_confidence_map(cd, cm, "rotate_180")
        self.assertEqual(adjusted["salutation_name"], "low")

    def test_orientation_normal_no_change(self):
        cm = {"last_name": "mid"}
        cd = {"last_name": "山田"}
        self.assertEqual(
            calc_orientation_adjusted_confidence_map(cd, cm, "normal"), {"last_name": "mid"}
        )

    def test_orientation_90_downgrades_high(self):
        cd = {"last_name": "山田"}
        cm = {}  # last_name は high（map になし）
        adjusted = calc_orientation_adjusted_confidence_map(cd, cm, "rotate_90_cw")
        self.assertEqual(adjusted["last_name"], "mid")

    def test_orientation_empty_value_skipped(self):
        cd = {"last_name": ""}  # 空フィールドは補正対象外
        cm = {}
        adjusted = calc_orientation_adjusted_confidence_map(cd, cm, "rotate_180")
        self.assertNotIn("last_name", adjusted)


class CardIndexOutOfRangeTests(SimpleTestCase):
    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            normalize_to_contact_dict(wrap(build_card()), 5)

    def test_cards_not_list_raises(self):
        with self.assertRaises(ValueError):
            normalize_to_contact_dict({"cards": "nope"}, 0)


class ExtractSnsEntriesTests(SimpleTestCase):
    def test_type_normalization_x_to_twitter(self):
        card = build_card(
            contact={"sns": E([{"type": "X", "id": "@taro"}])}
        )
        self.assertEqual(extract_sns_entries(wrap(card), 0), [("twitter", "@taro")])

    def test_unsupported_type_silently_ignored(self):
        card = build_card(
            contact={
                "sns": E(
                    [
                        {"type": "mastodon", "id": "@taro@example"},
                        {"type": "line", "id": "taro-line"},
                    ]
                )
            }
        )
        self.assertEqual(extract_sns_entries(wrap(card), 0), [("line", "taro-line")])

    def test_empty_id_ignored(self):
        card = build_card(
            contact={"sns": E([{"type": "github", "id": ""}])}
        )
        self.assertEqual(extract_sns_entries(wrap(card), 0), [])

    def test_multiple_same_type_kept(self):
        card = build_card(
            contact={
                "sns": E(
                    [
                        {"type": "twitter", "id": "@personal"},
                        {"type": "twitter", "id": "@company"},
                    ]
                )
            }
        )
        self.assertEqual(
            extract_sns_entries(wrap(card), 0),
            [("twitter", "@personal"), ("twitter", "@company")],
        )

    def test_no_sns_block(self):
        card = build_card()
        del card["contact"]
        self.assertEqual(extract_sns_entries(wrap(card), 0), [])


class CreateSnsRecordsTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
        )

    def test_creates_records(self):
        create_sns_records_for_contact(
            self.contact, [("twitter", "@taro"), ("line", "taro-line")]
        )
        self.assertEqual(self.contact.sns_accounts.count(), 2)

    def test_unique_constraint_suppressed_by_get_or_create(self):
        entries = [("twitter", "@taro"), ("twitter", "@taro")]
        create_sns_records_for_contact(self.contact, entries)
        # 同一 (contact, type, id) は 1 件のみ
        self.assertEqual(
            self.contact.sns_accounts.filter(sns_type="twitter", sns_id="@taro").count(),
            1,
        )

    def test_rerun_idempotent(self):
        create_sns_records_for_contact(self.contact, [("github", "taro")])
        create_sns_records_for_contact(self.contact, [("github", "taro")])
        self.assertEqual(self.contact.sns_accounts.count(), 1)
