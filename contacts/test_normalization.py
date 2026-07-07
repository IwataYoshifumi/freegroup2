"""contacts/services/normalization.py の純関数群 + Contact.save() オーバーライドの単体テスト。

Phase B（正規化基盤）。純関数は DB 不要のため SimpleTestCase、Contact.save() オーバー
ライドは実 DB を使うため TestCase を使う。
"""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from contacts.models import Contact, ContactFieldConfidence
from contacts.services.normalization import (
    check_name_consistency,
    compose_full_address,
    compute_full_name,
    compute_salutation_name,
    derive_org_core_name,
    derive_org_domain_name,
    format_jp_postal,
    format_postal_by_country,
    format_us_postal,
    is_generic_email_domain,
    normalize_department_title_branch,
    normalize_email,
    normalize_full_name,
    normalize_organization,
    normalize_original_script_for_full_name,
    normalize_phone_value,
    normalize_postal_code,
    normalize_postal_code_by_country,
    normalize_postal_code_intl,
    normalize_rest_of_address,
    normalize_rest_of_address_by_country,
    normalize_rest_of_address_intl,
)
from persons.models import Person


def _entry(value, confidence="high"):
    """name_block 用の {value, confidence} エントリを作るテストヘルパー。"""
    return {"value": value, "confidence": confidence}


class NormalizeFullNameTests(SimpleTestCase):
    def test_typical(self):
        self.assertEqual(normalize_full_name("山田太郎"), "山田太郎")

    def test_zenkaku_space_and_halfwidth_space_removed(self):
        # 全角空白→半角→半角空白除去で空白が全て消える（仕様書 §11.9.5.1）
        self.assertEqual(normalize_full_name("山田　太郎"), "山田太郎")
        self.assertEqual(normalize_full_name("山田 太郎"), "山田太郎")

    def test_zenkaku_alnum_to_hankaku(self):
        self.assertEqual(normalize_full_name("ＡＢＣ１２３"), "ABC123")

    def test_strip(self):
        self.assertEqual(normalize_full_name("  山田太郎  "), "山田太郎")

    def test_already_normalized(self):
        self.assertEqual(normalize_full_name("Taro"), "Taro")

    def test_empty_raises(self):
        with self.assertRaises(ValidationError):
            normalize_full_name("")

    def test_none_raises(self):
        with self.assertRaises(ValidationError):
            normalize_full_name(None)

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValidationError):
            normalize_full_name("　　  ")


class NormalizeOrganizationTests(SimpleTestCase):
    def test_abbrev_to_full(self):
        self.assertEqual(normalize_organization("㈱ネットワーク東海"), "株式会社ネットワーク東海")
        self.assertEqual(normalize_organization("(株)ABC"), "株式会社ABC")

    def test_position_not_absorbed(self):
        # 前株・後株は別表記のまま（位置を吸収しない）
        self.assertEqual(normalize_organization("ABC㈱"), "ABC株式会社")

    def test_spaces_removed(self):
        self.assertEqual(normalize_organization("ネット　ワーク 東海"), "ネットワーク東海")

    def test_zenkaku_alnum(self):
        self.assertEqual(normalize_organization("ＡＢＣ商事"), "ABC商事")

    def test_empty(self):
        self.assertEqual(normalize_organization(""), "")


class NormalizePhoneValueTests(SimpleTestCase):
    def test_hyphen_removed(self):
        self.assertEqual(normalize_phone_value("0565-12-3456"), "0565123456")

    def test_zenkaku_digits(self):
        self.assertEqual(normalize_phone_value("０９０１２３４５６７８"), "09012345678")

    def test_kanji_digits(self):
        self.assertEqual(normalize_phone_value("〇九〇一二三四"), "0901234")

    def test_strip_non_digit(self):
        self.assertEqual(normalize_phone_value("TEL: 03 (1234) 5678"), "0312345678")

    def test_empty(self):
        self.assertEqual(normalize_phone_value(""), "")


class NormalizeEmailTests(SimpleTestCase):
    def test_lowercase_and_strip(self):
        self.assertEqual(normalize_email("  Taro@Example.COM "), "taro@example.com")

    def test_already_normalized(self):
        self.assertEqual(normalize_email("a@b.jp"), "a@b.jp")

    def test_empty(self):
        self.assertEqual(normalize_email(""), "")


class NormalizeRestOfAddressTests(SimpleTestCase):
    def test_chome_banchi_to_hyphen(self):
        self.assertEqual(normalize_rest_of_address("1丁目2番地3号"), "1-2-3")

    def test_kanji_digit(self):
        self.assertEqual(normalize_rest_of_address("三番地"), "3")

    def test_zenkaku_space_removed_and_alnum(self):
        self.assertEqual(normalize_rest_of_address("ＡＢ　ビル　１０１"), "ABビル101")

    def test_dash_unify(self):
        self.assertEqual(normalize_rest_of_address("1－2－3"), "1-2-3")

    def test_empty(self):
        self.assertEqual(normalize_rest_of_address(""), "")


class NormalizePostalCodeTests(SimpleTestCase):
    def test_hyphen_removed(self):
        self.assertEqual(normalize_postal_code("471-0001"), "4710001")

    def test_zenkaku_digits(self):
        self.assertEqual(normalize_postal_code("４７１０００１"), "4710001")

    def test_empty(self):
        self.assertEqual(normalize_postal_code(""), "")


class NormalizeDepartmentTitleBranchTests(SimpleTestCase):
    def test_spaces_removed(self):
        self.assertEqual(normalize_department_title_branch("営業 部"), "営業部")
        self.assertEqual(normalize_department_title_branch("営業　部"), "営業部")

    def test_zenkaku_alnum(self):
        self.assertEqual(normalize_department_title_branch("第１営業部"), "第1営業部")

    def test_empty(self):
        self.assertEqual(normalize_department_title_branch(""), "")


class ComposeFullAddressTests(SimpleTestCase):
    def test_ja_all_present(self):
        self.assertEqual(
            compose_full_address("4710001", "愛知県", "豊田市", "1-2-3", "JP", "ja"),
            "〒4710001 愛知県豊田市1-2-3",
        )

    def test_en_us_format(self):
        # Phase D2：US は英語式「{rest}, {city}, {region} {postal}, {country}」。
        # 5 桁 ZIP はそのまま。区切りはカンマ + スペース。
        self.assertEqual(
            compose_full_address("12345", "CA", "LA", "1st St", "US", "en"),
            "1st St, LA, CA 12345, US",
        )

    def test_postal_empty(self):
        self.assertEqual(
            compose_full_address("", "愛知県", "豊田市", "1-2-3", "JP", "ja"),
            "愛知県豊田市1-2-3",
        )

    def test_region_empty(self):
        self.assertEqual(
            compose_full_address("4710001", "", "豊田市", "1-2-3", "JP", "ja"),
            "〒4710001 豊田市1-2-3",
        )

    def test_rest_empty(self):
        self.assertEqual(
            compose_full_address("4710001", "愛知県", "豊田市", "", "JP", "ja"),
            "〒4710001 愛知県豊田市",
        )

    def test_all_empty(self):
        self.assertEqual(compose_full_address("", "", "", "", "", "ja"), "")


class ComposeFullAddressUsDefaultTests(SimpleTestCase):
    """Phase D2：US / _default（英語式）の compose・postal 整形・空要素掃除。"""

    def test_us_5digit(self):
        self.assertEqual(
            compose_full_address(
                "94103", "CA", "San Francisco", "123 Market St", "US", "en"
            ),
            "123 Market St, San Francisco, CA 94103, US",
        )

    def test_us_9digit_zip_plus4(self):
        self.assertEqual(
            compose_full_address(
                "941031234", "CA", "San Francisco", "123 Market St", "US", "en"
            ),
            "123 Market St, San Francisco, CA 94103-1234, US",
        )

    def test_default_fallback_gb_passthrough_postal(self):
        self.assertEqual(
            compose_full_address(
                "SW1A1AA", "England", "London", "10 Downing St", "GB", "en"
            ),
            "10 Downing St, London, England SW1A1AA, GB",
        )

    def test_empty_country_uses_default(self):
        # Q1 案ii：country="" → _default 英語式。空 country は末尾に出さない。
        self.assertEqual(
            compose_full_address("94103", "CA", "LA", "1st St", "", "en"),
            "1st St, LA, CA 94103",
        )

    def test_zz_country_uses_default(self):
        # Q2：country="ZZ"（OCR 判定不能）→ _default 英語式。
        self.assertEqual(
            compose_full_address("94103", "CA", "LA", "1st St", "ZZ", "en"),
            "1st St, LA, CA 94103, ZZ",
        )

    def test_empty_region_no_double_comma(self):
        self.assertEqual(
            compose_full_address(
                "94103", "", "San Francisco", "123 Market St", "US", "en"
            ),
            "123 Market St, San Francisco, 94103, US",
        )

    def test_empty_postal_and_region(self):
        self.assertEqual(
            compose_full_address(
                "", "", "San Francisco", "123 Market St", "US", "en"
            ),
            "123 Market St, San Francisco, US",
        )

    def test_all_empty_default(self):
        self.assertEqual(compose_full_address("", "", "", "", "US", "en"), "")


class FormatJpPostalTests(SimpleTestCase):
    """Phase D2：format_jp_postal（7 桁のみハイフン挿入）。"""

    def test_7_digits_inserts_hyphen(self):
        self.assertEqual(format_jp_postal("4710001"), "471-0001")

    def test_non_7_passthrough(self):
        self.assertEqual(format_jp_postal("471001"), "471001")  # 6 桁
        self.assertEqual(format_jp_postal("47100012"), "47100012")  # 8 桁
        self.assertEqual(format_jp_postal("471-0001"), "471-0001")  # 既ハイフン
        self.assertEqual(format_jp_postal(""), "")
        self.assertEqual(format_jp_postal(None), "")


class FormatUsPostalTests(SimpleTestCase):
    """Phase D2：format_us_postal（9 桁のみ ZIP+4 ハイフン挿入、5 桁はそのまま）。"""

    def test_5_digit_passthrough(self):
        self.assertEqual(format_us_postal("94103"), "94103")

    def test_9_digit_zip_plus4(self):
        self.assertEqual(format_us_postal("941031234"), "94103-1234")

    def test_other_passthrough(self):
        self.assertEqual(format_us_postal("9410"), "9410")  # 4 桁
        self.assertEqual(format_us_postal("9410312345"), "9410312345")  # 10 桁
        self.assertEqual(format_us_postal("94103-1234"), "94103-1234")
        self.assertEqual(format_us_postal(""), "")


class FormatPostalByCountryTests(SimpleTestCase):
    """Phase D2：format_postal_by_country（国別ディスパッチ・大小無視・未対応 passthrough）。"""

    def test_jp(self):
        self.assertEqual(format_postal_by_country("4710001", "JP"), "471-0001")

    def test_us(self):
        self.assertEqual(format_postal_by_country("941031234", "US"), "94103-1234")

    def test_lowercase_country_key(self):
        self.assertEqual(format_postal_by_country("4710001", "jp"), "471-0001")

    def test_default_passthrough(self):
        self.assertEqual(format_postal_by_country("12345", "GB"), "12345")
        self.assertEqual(format_postal_by_country("12345", ""), "12345")
        self.assertEqual(format_postal_by_country("12345", "ZZ"), "12345")


class NormalizePostalCodeIntlTests(SimpleTestCase):
    """Phase D2：normalize_postal_code_intl（英数混在 postal を破壊しない）。"""

    def test_gb_alphanumeric_preserved(self):
        self.assertEqual(normalize_postal_code_intl("SW1A1AA"), "SW1A1AA")

    def test_gb_with_inner_space_removed(self):
        self.assertEqual(normalize_postal_code_intl("SW1A 1AA"), "SW1A1AA")

    def test_ca_with_space(self):
        self.assertEqual(normalize_postal_code_intl("K1A 0B1"), "K1A0B1")

    def test_zenkaku_alnum_to_han(self):
        self.assertEqual(normalize_postal_code_intl("ＳＷ１Ａ１ＡＡ"), "SW1A1AA")

    def test_hyphen_preserved(self):
        self.assertEqual(normalize_postal_code_intl("1234-AB"), "1234-AB")

    def test_empty(self):
        self.assertEqual(normalize_postal_code_intl(""), "")


class NormalizePostalCodeByCountryTests(SimpleTestCase):
    """Phase D2：normalize_postal_code_by_country（JP/US=数字のみ / _default=英字保持）。"""

    def test_jp_digits_only(self):
        self.assertEqual(normalize_postal_code_by_country("471-0001", "JP"), "4710001")

    def test_us_digits_only(self):
        # US は ZIP+4 のハイフンも除去（数字のみ）
        self.assertEqual(
            normalize_postal_code_by_country("94103-1234", "US"), "941031234"
        )

    def test_gb_default_preserves_alpha(self):
        # バグ再現防止：SW1A1AA が "11" に破壊されないこと
        self.assertEqual(
            normalize_postal_code_by_country("SW1A1AA", "GB"), "SW1A1AA"
        )

    def test_empty_country_uses_default(self):
        self.assertEqual(
            normalize_postal_code_by_country("AB12CD", ""), "AB12CD"
        )

    def test_lowercase_country_key(self):
        self.assertEqual(normalize_postal_code_by_country("471-0001", "jp"), "4710001")


class NormalizeRestOfAddressByCountryTests(SimpleTestCase):
    """Phase D2：rest_of_address の country 別正規化（JP=除去 / US・_default=保持）。"""

    def test_jp_strips_spaces(self):
        self.assertEqual(
            normalize_rest_of_address_by_country("1丁目 2番地 3号", "JP"), "1-2-3"
        )

    def test_us_preserves_spaces(self):
        self.assertEqual(
            normalize_rest_of_address_by_country("123 Market St", "US"),
            "123 Market St",
        )

    def test_default_preserves_spaces(self):
        self.assertEqual(
            normalize_rest_of_address_by_country("10 Downing St", "GB"),
            "10 Downing St",
        )
        self.assertEqual(
            normalize_rest_of_address_by_country("10 Downing St", ""),
            "10 Downing St",
        )

    def test_empty(self):
        self.assertEqual(normalize_rest_of_address_by_country("", "US"), "")


class NormalizeRestOfAddressIntlTests(SimpleTestCase):
    """Phase D2：normalize_rest_of_address_intl（スペース保持版・軽い正規化）。"""

    def test_collapse_inner_spaces(self):
        self.assertEqual(
            normalize_rest_of_address_intl("123  Market   St"), "123 Market St"
        )

    def test_zenkaku_space_to_han(self):
        self.assertEqual(normalize_rest_of_address_intl("123　Market"), "123 Market")

    def test_zenkaku_alnum_to_han(self):
        self.assertEqual(normalize_rest_of_address_intl("１２３ Ｍ"), "123 M")

    def test_strip(self):
        self.assertEqual(
            normalize_rest_of_address_intl("  123 Market St  "), "123 Market St"
        )

    def test_empty(self):
        self.assertEqual(normalize_rest_of_address_intl(""), "")


class FormatPostalTemplateTagTests(SimpleTestCase):
    """Phase D2 話2：{% format_postal %} テンプレートタグ（表示整形）。"""

    def _render(self, postal, country):
        from django.template import Context, Template

        tpl = Template("{% load postal_tags %}{% format_postal p c %}")
        return tpl.render(Context({"p": postal, "c": country}))

    def test_jp_7digit(self):
        self.assertEqual(self._render("4710001", "JP"), "471-0001")

    def test_us_9digit(self):
        self.assertEqual(self._render("941031234", "US"), "94103-1234")

    def test_default_passthrough(self):
        self.assertEqual(self._render("12345", "GB"), "12345")

    def test_as_assignment_form(self):
        from django.template import Context, Template

        tpl = Template(
            "{% load postal_tags %}{% format_postal p c as x %}[{{ x }}]"
        )
        self.assertEqual(
            tpl.render(Context({"p": "4710001", "c": "JP"})), "[471-0001]"
        )


class DeriveOrgCoreNameTests(SimpleTestCase):
    def test_legal_type_included(self):
        self.assertEqual(
            derive_org_core_name("株式会社ネットワーク東海", "株式会社"),
            "ネットワーク東海",
        )

    def test_legal_type_not_included(self):
        self.assertEqual(
            derive_org_core_name("ネットワーク東海", "株式会社"),
            "ネットワーク東海",
        )

    def test_empty(self):
        self.assertEqual(derive_org_core_name("", "株式会社"), "")


class DeriveOrgDomainNameTests(SimpleTestCase):
    def test_normal(self):
        self.assertEqual(derive_org_domain_name("taro@example.co.jp"), "example.co.jp")

    def test_generic_domain_not_emptied(self):
        # 汎用ドメインでも名刺どおり残す（空にしない、§11.9.6）
        self.assertEqual(derive_org_domain_name("taro@gmail.com"), "gmail.com")

    def test_no_at(self):
        self.assertEqual(derive_org_domain_name("not-an-email"), "")

    def test_empty(self):
        self.assertEqual(derive_org_domain_name(""), "")


class IsGenericEmailDomainTests(SimpleTestCase):
    def test_generic_true(self):
        self.assertTrue(is_generic_email_domain("gmail.com"))
        self.assertTrue(is_generic_email_domain("YAHOO.CO.JP"))

    def test_normal_false(self):
        self.assertFalse(is_generic_email_domain("example.co.jp"))

    def test_empty_false(self):
        self.assertFalse(is_generic_email_domain(""))


class CheckNameConsistencyTests(SimpleTestCase):
    def test_all_consistent_returns_empty(self):
        name_block = {
            "original_script": _entry("山田太郎"),
            "last_name": _entry("山田"),
            "first_name": _entry("太郎"),
            "other_name_parts": _entry(""),
            "name_order": _entry("last_first"),
            "salutation_name": _entry("山田 様"),
            "primary_lang": "ja",
        }
        self.assertEqual(check_name_consistency(name_block), {})

    def test_coverage_violation(self):
        # original_script に構成要素でカバーされない「太」がある
        name_block = {
            "original_script": _entry("山田太郎"),
            "last_name": _entry("山田"),
            "first_name": _entry("次郎"),
            "name_order": _entry("last_first"),
            "primary_lang": "ja",
        }
        result = check_name_consistency(name_block)
        self.assertEqual(result.get("last_name"), "mid")
        self.assertEqual(result.get("first_name"), "mid")
        self.assertEqual(result.get("other_name_parts"), "mid")

    def test_name_order_single_but_both_present(self):
        name_block = {
            "original_script": _entry("山田太郎"),
            "last_name": _entry("山田"),
            "first_name": _entry("太郎"),
            "name_order": _entry("single"),
            "primary_lang": "ja",
        }
        result = check_name_consistency(name_block)
        self.assertEqual(result.get("name_order"), "mid")

    def test_primary_lang_name_order_mismatch(self):
        name_block = {
            "original_script": _entry("山田太郎"),
            "last_name": _entry("山田"),
            "first_name": _entry("太郎"),
            "name_order": _entry("first_last"),
            "primary_lang": "ja",
        }
        result = check_name_consistency(name_block)
        self.assertEqual(result.get("name_order"), "mid")

    def test_salutation_name_mismatch(self):
        name_block = {
            "original_script": _entry("山田太郎"),
            "last_name": _entry("山田"),
            "first_name": _entry("太郎"),
            "name_order": _entry("last_first"),
            "salutation_name": _entry("鈴木 様"),
            "primary_lang": "ja",
        }
        result = check_name_consistency(name_block)
        self.assertEqual(result.get("salutation_name"), "mid")

    def test_downgrade_from_mid_to_low(self):
        # 既に mid の name_order が違反検出で low に下がる
        name_block = {
            "original_script": _entry("山田太郎"),
            "last_name": _entry("山田"),
            "first_name": _entry("太郎"),
            "name_order": _entry("first_last", confidence="mid"),
            "primary_lang": "ja",
        }
        result = check_name_consistency(name_block)
        self.assertEqual(result.get("name_order"), "low")


class ComputeSalutationNameTests(SimpleTestCase):
    class _FakeContact:
        def __init__(self, lang="", last_name="", full_name=""):
            self.lang = lang
            self.last_name = last_name
            self.full_name = full_name

    def test_ja(self):
        c = self._FakeContact(lang="ja", last_name="山田", full_name="山田太郎")
        self.assertEqual(compute_salutation_name(c), "山田 様")

    def test_ja_fallback_to_full_name(self):
        c = self._FakeContact(lang="ja", last_name="", full_name="山田太郎")
        self.assertEqual(compute_salutation_name(c), "山田太郎 様")

    def test_ko(self):
        c = self._FakeContact(lang="ko", last_name="김", full_name="김철수")
        self.assertEqual(compute_salutation_name(c), "김 님")

    def test_zh_full_name_only(self):
        c = self._FakeContact(lang="zh", last_name="王", full_name="王伟")
        self.assertEqual(compute_salutation_name(c), "王伟")

    def test_en(self):
        c = self._FakeContact(lang="en", last_name="Smith", full_name="John Smith")
        self.assertEqual(compute_salutation_name(c), "Dear John Smith,")

    def test_und(self):
        c = self._FakeContact(lang="und", last_name="", full_name="Pat")
        self.assertEqual(compute_salutation_name(c), "Dear Pat,")

    def test_both_empty_returns_empty(self):
        c = self._FakeContact(lang="ja", last_name="", full_name="")
        self.assertEqual(compute_salutation_name(c), "")


class NormalizeOriginalScriptForFullNameTests(SimpleTestCase):
    def test_zenkaku_space(self):
        self.assertEqual(
            normalize_original_script_for_full_name("Yamada　Taro"), "Yamada Taro"
        )

    def test_consecutive_spaces(self):
        self.assertEqual(
            normalize_original_script_for_full_name("Yamada   Taro"), "Yamada Taro"
        )

    def test_strip(self):
        self.assertEqual(
            normalize_original_script_for_full_name("  Yamada Taro  "), "Yamada Taro"
        )

    def test_case_and_zenkaku_alnum_preserved(self):
        # 大文字小文字・全角英数字はそのまま（重い正規化を入れない、§3.3.1）
        self.assertEqual(
            normalize_original_script_for_full_name("van der Berg"), "van der Berg"
        )
        self.assertEqual(
            normalize_original_script_for_full_name("ＡＢＣ"), "ＡＢＣ"
        )

    def test_already_clean(self):
        self.assertEqual(
            normalize_original_script_for_full_name("Yamada Taro"), "Yamada Taro"
        )

    def test_empty(self):
        self.assertEqual(normalize_original_script_for_full_name(""), "")


class ContactSaveSalutationTests(TestCase):
    def _make_person(self):
        return Person.objects.create()

    def test_is_manual_true_does_nothing(self):
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="",
            salutation_name_is_manual=True,
        )
        self.assertEqual(c.salutation_name, "")

    def test_is_manual_false_empty_is_filled(self):
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(c.salutation_name, "山田 様")

    def test_is_manual_false_surname_change_recomputes(self):
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(c.salutation_name, "山田 様")
        c.last_name = "佐藤"
        c.save()
        self.assertEqual(c.salutation_name, "佐藤 様")

    def test_is_manual_false_no_surname_change_keeps_value(self):
        # 値あり + 姓系変更なし → 既存値（OCR 直接出力相当）を尊重
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="特別な宛名",
            salutation_name_is_manual=False,
        )
        self.assertEqual(c.salutation_name, "特別な宛名")
        c.title = "部長"  # 姓系でないフィールドを変更
        c.save()
        self.assertEqual(c.salutation_name, "特別な宛名")

    def test_reload_from_db_then_recompute(self):
        # DB から読み直したインスタンスでも __init__ スナップショットが効く
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        reloaded = Contact.objects.get(pk=c.pk)
        self.assertEqual(reloaded.salutation_name, "山田 様")
        reloaded.last_name = "佐藤"
        reloaded.save()
        reloaded.refresh_from_db()
        self.assertEqual(reloaded.salutation_name, "佐藤 様")


class ComputeFullNameTests(SimpleTestCase):
    """compute_full_name の純関数テスト（js-name-full と同じ並び）。"""

    def test_last_first_excludes_middle(self):
        # last_first は [姓, 名]（ミドルは含めない、JS と同じ）
        self.assertEqual(
            compute_full_name("山田", "太郎", "ミドル", "last_first"), "山田 太郎"
        )

    def test_first_last_includes_middle(self):
        # first_last は [名, ミドル, 姓]
        self.assertEqual(
            compute_full_name("Yamada", "Taro", "M", "first_last"), "Taro M Yamada"
        )

    def test_single_uses_last_or_first(self):
        self.assertEqual(compute_full_name("山田", "", "", "single"), "山田")
        self.assertEqual(compute_full_name("", "太郎", "", "single"), "太郎")

    def test_other_or_blank_returns_none(self):
        # other / 未選択は自動組み立てしない（None＝呼び出し側は full_name 据え置き）
        self.assertIsNone(compute_full_name("山田", "太郎", "", "other"))
        self.assertIsNone(compute_full_name("山田", "太郎", "", ""))

    def test_empty_parts_are_filtered(self):
        # 空要素はスペース結合から除外（filter(Boolean) 相当）
        self.assertEqual(compute_full_name("山田", "", "", "last_first"), "山田")


class ContactSaveFullNameTests(TestCase):
    """Contact.save() の full_name 自動組み立て（salutation_name_is_manual と同型）。"""

    def _make_person(self):
        return Person.objects.create()

    def test_is_manual_true_does_nothing(self):
        # full_name_is_manual=True なら原本変更でも再計算しない（手入力尊重）
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="手動氏名",
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            lang="ja",
            full_name_is_manual=True,
        )
        self.assertEqual(c.full_name, "手動氏名")
        c.last_name = "佐藤"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.full_name, "手動氏名")

    def test_is_manual_false_empty_is_filled(self):
        # full_name 空 → 原本から補完
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="",
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            lang="ja",
            full_name_is_manual=False,
        )
        self.assertEqual(c.full_name, "山田 太郎")

    def test_is_manual_false_source_change_recomputes(self):
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="",
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            lang="ja",
            full_name_is_manual=False,
        )
        self.assertEqual(c.full_name, "山田 太郎")
        c.last_name = "佐藤"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.full_name, "佐藤 太郎")

    def test_reload_from_db_then_recompute(self):
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="",
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            lang="ja",
            full_name_is_manual=False,
        )
        reloaded = Contact.objects.get(pk=c.pk)
        self.assertEqual(reloaded.full_name, "山田 太郎")
        reloaded.first_name = "次郎"
        reloaded.save()
        reloaded.refresh_from_db()
        self.assertEqual(reloaded.full_name, "山田 次郎")


class ContactSaveDisplayNameTests(TestCase):
    """Contact.save() の display_name 自動追従（display_name_is_manual と同型）。"""

    def _make_person(self):
        return Person.objects.create()

    def test_auto_follows_full_name(self):
        # display_name_is_manual=False → full_name に追従
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            name_order="other",  # full_name 自動組み立て対象外＝full_name 据え置き
            lang="ja",
            display_name="",
            display_name_is_manual=False,
        )
        self.assertEqual(c.display_name, "山田太郎")

    def test_is_manual_true_keeps_value(self):
        # display_name_is_manual=True → full_name と異なっても上書きしない
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            name_order="other",
            lang="ja",
            display_name="営業の山田",
            display_name_is_manual=True,
        )
        self.assertEqual(c.display_name, "営業の山田")
        c.title = "部長"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.display_name, "営業の山田")

    def test_follows_full_name_recompute(self):
        # 原本変更 → full_name 再計算 → display_name も追従（依存順の確認）
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="",
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            lang="ja",
            full_name_is_manual=False,
            display_name_is_manual=False,
        )
        self.assertEqual(c.full_name, "山田 太郎")
        self.assertEqual(c.display_name, "山田 太郎")
        c.last_name = "佐藤"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.full_name, "佐藤 太郎")
        self.assertEqual(c.display_name, "佐藤 太郎")


class ContactSaveSalutationCfcTests(TestCase):
    """Contact.save() オーバーライドの ContactFieldConfidence 作成挙動（Phase C §1.8）。"""

    def _make_person(self):
        return Person.objects.create()

    def _salutation_cfc_count(self, contact):
        return ContactFieldConfidence.objects.filter(
            contact=contact, field_name="salutation_name"
        ).count()

    def test_ja_computed_creates_no_cfc(self):
        # §1.8 改訂：lang=ja は補完で値は作るが、自明な日本語敬称のため low CFC は作らない。
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(c.salutation_name, "山田 様")  # 値は作られる
        self.assertEqual(self._salutation_cfc_count(c), 0)  # CFC は作らない

    def test_non_ja_computed_creates_low_cfc(self):
        # ja 以外（en）は従来どおり low（未確認）CFC を作る。
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="John Smith",
            last_name="Smith",
            lang="en",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(c.salutation_name, "Dear John Smith,")
        cfc = ContactFieldConfidence.objects.get(
            contact=c, field_name="salutation_name"
        )
        self.assertEqual(cfc.confidence, "low")

    def test_is_manual_true_creates_no_cfc(self):
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="",
            salutation_name_is_manual=True,
        )
        self.assertEqual(self._salutation_cfc_count(c), 0)

    def test_no_computation_creates_no_cfc(self):
        # OCR 直接出力相当（値あり・is_manual=False）→ 補完が走らない → CFC なし
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="山田太郎",
            last_name="山田",
            lang="ja",
            salutation_name="特別な宛名",
            salutation_name_is_manual=False,
        )
        self.assertEqual(self._salutation_cfc_count(c), 0)

    def test_recompute_does_not_duplicate_cfc(self):
        # 非 ja（en・CFC あり）→ 氏名修正で再計算 → CFC は重複作成されず 1 件のまま。
        # （ja は CFC を作らないため、重複検証は CFC が付く非 ja で行う。）
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="John Smith",
            last_name="Smith",
            lang="en",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(self._salutation_cfc_count(c), 1)
        c.full_name = "John Brown"
        c.save()
        self.assertEqual(c.salutation_name, "Dear John Brown,")
        self.assertEqual(self._salutation_cfc_count(c), 1)

    def test_both_empty_no_change_no_cfc(self):
        # lang=ja で last_name も full_name も空 → compute は "" を返す → 値変化なし → CFC なし
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="",
            last_name="",
            lang="ja",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(c.salutation_name, "")
        self.assertEqual(self._salutation_cfc_count(c), 0)

    def test_ja_jp_variant_no_cfc(self):
        # ja-JP / 大文字等も日本語扱い（lower().startswith("ja")）で CFC を作らない（§1.8 改訂）。
        c = Contact.objects.create(
            person=self._make_person(),
            status=Contact.Status.PRIMARY,
            full_name="鈴木花子",
            last_name="鈴木",
            lang="ja-JP",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(self._salutation_cfc_count(c), 0)
