"""contacts.services.normalization のユニットテスト + Contact.save() オーバーライドのテスト。

Phase 2 で実装した純関数群（仕様書 v1.6.0 本編 §11.9 / OCR 統合版 §6）と、
Contact.save() オーバーライドによる salutation_name 補完ロジック（§1.5.3 / §11.9.7）を検証する。

純関数テストは DB 不要（SimpleTestCase）、Contact.save() のテストは実 DB を使う（TestCase）。
"""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from contacts.models import Contact
from contacts.services.normalization import (
    check_name_consistency,
    compose_full_address,
    compute_salutation_name,
    derive_org_core_name,
    derive_org_domain_name,
    is_generic_email_domain,
    normalize_department_title_branch,
    normalize_email,
    normalize_full_name,
    normalize_organization,
    normalize_original_script_for_full_name,
    normalize_phone_value,
    normalize_postal_code,
    normalize_rest_of_address,
)
from persons.models import Person


# ======================================================================
# 正規化純関数群（仕様書 §11.9.5.1）
# ======================================================================

class NormalizeFullNameTests(SimpleTestCase):
    """normalize_full_name：全角空白→半角・半角空白除去・全角英数字→半角・前後空白除去・空 ValidationError。"""

    def test_typical_japanese(self):
        self.assertEqual(normalize_full_name("山田 太郎"), "山田太郎")

    def test_fullwidth_space_and_alnum(self):
        # 全角空白・全角英数字を含む
        self.assertEqual(normalize_full_name("Ｊｏｈｎ　Ｄｏｅ"), "JohnDoe")

    def test_trailing_whitespace(self):
        self.assertEqual(normalize_full_name("  Alice  "), "Alice")

    def test_already_normalized(self):
        self.assertEqual(normalize_full_name("AliceBrown"), "AliceBrown")

    def test_empty_raises(self):
        with self.assertRaises(ValidationError):
            normalize_full_name("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValidationError):
            normalize_full_name("   　 ")

    def test_none_raises(self):
        with self.assertRaises(ValidationError):
            normalize_full_name(None)


class NormalizeOrganizationTests(SimpleTestCase):
    """normalize_organization：株式会社系統一・前後位置差不吸収・空白除去・全角英数字→半角。"""

    def test_kabushiki_kaisha_alias_unified(self):
        self.assertEqual(normalize_organization("(株)サンプル"), "株式会社サンプル")
        self.assertEqual(normalize_organization("㈱サンプル"), "株式会社サンプル")
        self.assertEqual(normalize_organization("㍿サンプル"), "株式会社サンプル")

    def test_yugen_kaisha_alias(self):
        self.assertEqual(normalize_organization("(有)テック"), "有限会社テック")

    def test_position_not_absorbed(self):
        """前株・後株は別表記として保持（位置移動しない）。"""
        self.assertEqual(normalize_organization("株式会社A"), "株式会社A")
        self.assertEqual(normalize_organization("A株式会社"), "A株式会社")

    def test_fullwidth_alnum_normalized(self):
        self.assertEqual(normalize_organization("Ａｐｐｌｅ"), "Apple")

    def test_whitespace_removed(self):
        self.assertEqual(normalize_organization("株式 会社 サンプル"), "株式会社サンプル")

    def test_empty(self):
        self.assertEqual(normalize_organization(""), "")
        self.assertEqual(normalize_organization(None), "")


class NormalizePhoneValueTests(SimpleTestCase):
    """normalize_phone_value：数字と「+」以外を除去、漢数字→半角、全角数字→半角。"""

    def test_strip_hyphens_and_spaces(self):
        self.assertEqual(normalize_phone_value("03-1234-5678"), "0312345678")

    def test_strip_parens(self):
        self.assertEqual(normalize_phone_value("(03) 1234-5678"), "0312345678")

    def test_e164_plus_preserved(self):
        self.assertEqual(normalize_phone_value("+81-3-1234-5678"), "+81312345678")

    def test_fullwidth_digits(self):
        self.assertEqual(normalize_phone_value("０３-１２３４-５６７８"), "0312345678")

    def test_kanji_digits(self):
        self.assertEqual(normalize_phone_value("〇九〇-一二三四"), "0901234")

    def test_already_normalized(self):
        self.assertEqual(normalize_phone_value("0312345678"), "0312345678")

    def test_empty(self):
        self.assertEqual(normalize_phone_value(""), "")
        self.assertEqual(normalize_phone_value(None), "")


class NormalizeEmailTests(SimpleTestCase):
    """normalize_email：小文字化 + 前後空白除去。"""

    def test_lowercase(self):
        self.assertEqual(normalize_email("Alice@Example.COM"), "alice@example.com")

    def test_trailing_whitespace(self):
        self.assertEqual(normalize_email("  bob@example.com  "), "bob@example.com")

    def test_already_normalized(self):
        self.assertEqual(normalize_email("foo@bar.com"), "foo@bar.com")

    def test_empty(self):
        self.assertEqual(normalize_email(""), "")
        self.assertEqual(normalize_email(None), "")


class NormalizeRestOfAddressTests(SimpleTestCase):
    """normalize_rest_of_address：全角半角空白除去・全角英数字→半角・漢数字→半角・丁目番地号→「-」・ハイフン統一。"""

    def test_chome_banchi_go(self):
        self.assertEqual(
            normalize_rest_of_address("1丁目2番地3号"), "1-2-3-"
        )

    def test_kanji_digits(self):
        self.assertEqual(normalize_rest_of_address("一丁目二番三号"), "1-2-3-")

    def test_hyphen_variants_unified(self):
        # ダッシュ・全角ハイフンを「-」に統一
        self.assertEqual(normalize_rest_of_address("1—2―3‐4"), "1-2-3-4")

    def test_whitespace_removed(self):
        self.assertEqual(normalize_rest_of_address(" 1-2-3 　ABC ビル "), "1-2-3ABCビル")

    def test_fullwidth_alnum(self):
        self.assertEqual(normalize_rest_of_address("１２３Ａ"), "123A")

    def test_empty(self):
        self.assertEqual(normalize_rest_of_address(""), "")
        self.assertEqual(normalize_rest_of_address(None), "")


class NormalizePostalCodeTests(SimpleTestCase):
    """normalize_postal_code：数字のみ（ハイフン除去）・全角数字→半角。"""

    def test_hyphen_stripped(self):
        self.assertEqual(normalize_postal_code("100-0005"), "1000005")

    def test_fullwidth(self):
        self.assertEqual(normalize_postal_code("１００ー０００５"), "1000005")

    def test_already_normalized(self):
        self.assertEqual(normalize_postal_code("1000005"), "1000005")

    def test_empty(self):
        self.assertEqual(normalize_postal_code(""), "")
        self.assertEqual(normalize_postal_code(None), "")


class NormalizeDepartmentTitleBranchTests(SimpleTestCase):
    """normalize_department_title_branch：全角半角空白除去・全角英数字→半角・前後空白除去。"""

    def test_internal_whitespace_removed(self):
        self.assertEqual(normalize_department_title_branch("営業 部 "), "営業部")

    def test_fullwidth_alnum(self):
        self.assertEqual(normalize_department_title_branch("Ｓａｌｅｓ Ｄｉｖ"), "SalesDiv")

    def test_empty(self):
        self.assertEqual(normalize_department_title_branch(""), "")
        self.assertEqual(normalize_department_title_branch(None), "")


class NormalizeOriginalScriptForFullNameTests(SimpleTestCase):
    """normalize_original_script_for_full_name：最小限正規化（仕様書 §3.3.1）。

    全角空白→半角 / 連続空白を 1 つに統一 / 前後空白除去。
    大文字小文字変換・全角英数字→半角は行わない（OCR 側で title case 済み前提）。
    """

    def test_fullwidth_space_to_half(self):
        self.assertEqual(
            normalize_original_script_for_full_name("山田　太郎"), "山田 太郎"
        )

    def test_consecutive_spaces_unified(self):
        self.assertEqual(
            normalize_original_script_for_full_name("Alice  Brown"), "Alice Brown"
        )

    def test_leading_trailing_whitespace(self):
        self.assertEqual(
            normalize_original_script_for_full_name("  John Doe  "), "John Doe"
        )

    def test_case_preserved(self):
        # title case を変えないことを確認
        self.assertEqual(
            normalize_original_script_for_full_name("Van Der Saar"), "Van Der Saar"
        )

    def test_fullwidth_alnum_not_normalized(self):
        # 全角英数字は変換しないことを確認（normalize_full_name と挙動が違う）
        self.assertEqual(
            normalize_original_script_for_full_name("Ａｂｃ"), "Ａｂｃ"
        )

    def test_already_normalized(self):
        self.assertEqual(
            normalize_original_script_for_full_name("Alice Brown"), "Alice Brown"
        )

    def test_empty(self):
        self.assertEqual(normalize_original_script_for_full_name(""), "")
        self.assertEqual(normalize_original_script_for_full_name(None), "")


# ======================================================================
# 派生・組み立て純関数群（仕様書 §11.9.4 / §11.9.6）
# ======================================================================

class ComposeFullAddressTests(SimpleTestCase):
    """compose_full_address：日本式の組み立て（〒記号は ja のみ）。"""

    def test_typical_ja(self):
        result = compose_full_address(
            "1000005", "東京都", "千代田区", "丸の内1-2-3", "JP", "ja",
        )
        self.assertEqual(result, "〒1000005 東京都千代田区丸の内1-2-3")

    def test_postal_code_empty(self):
        result = compose_full_address(
            "", "東京都", "千代田区", "丸の内1-2-3", "JP", "ja",
        )
        self.assertEqual(result, "東京都千代田区丸の内1-2-3")

    def test_region_empty(self):
        result = compose_full_address(
            "1000005", "", "千代田区", "丸の内1-2-3", "JP", "ja",
        )
        self.assertEqual(result, "〒1000005 千代田区丸の内1-2-3")

    def test_rest_empty(self):
        result = compose_full_address(
            "1000005", "東京都", "千代田区", "", "JP", "ja",
        )
        self.assertEqual(result, "〒1000005 東京都千代田区")

    def test_all_empty(self):
        result = compose_full_address("", "", "", "", "", "ja")
        self.assertEqual(result, "")

    def test_en_lang_no_postal_prefix(self):
        """lang='en' のときは「〒」を付けず、postal_code をそのまま前置する。"""
        result = compose_full_address(
            "12345", "California", "San Francisco", "Market St 100", "US", "en",
        )
        self.assertEqual(result, "12345 CaliforniaSan FranciscoMarket St 100")

    def test_none_args_safe(self):
        # None 引数で落ちないこと
        result = compose_full_address(None, None, None, None, None, "ja")
        self.assertEqual(result, "")


class DeriveOrgCoreNameTests(SimpleTestCase):
    """derive_org_core_name：org_name_full から legal_entity_type を除去。"""

    def test_pre_position(self):
        self.assertEqual(
            derive_org_core_name("株式会社サンプル", "株式会社"), "サンプル"
        )

    def test_post_position(self):
        self.assertEqual(
            derive_org_core_name("サンプル株式会社", "株式会社"), "サンプル"
        )

    def test_legal_entity_not_in_name(self):
        self.assertEqual(
            derive_org_core_name("サンプル", "株式会社"), "サンプル"
        )

    def test_org_name_empty(self):
        self.assertEqual(derive_org_core_name("", "株式会社"), "")
        self.assertEqual(derive_org_core_name(None, "株式会社"), "")

    def test_legal_entity_empty(self):
        self.assertEqual(
            derive_org_core_name("株式会社サンプル", ""), "株式会社サンプル"
        )


class DeriveOrgDomainNameTests(SimpleTestCase):
    """derive_org_domain_name：@ 以降のドメインを返す。汎用ドメインでも空にしない。"""

    def test_typical(self):
        self.assertEqual(
            derive_org_domain_name("alice@example.com"), "example.com"
        )

    def test_generic_domain_not_emptied(self):
        # 汎用ドメインでも値はそのまま返す（仕様書 §11.9.6）
        self.assertEqual(
            derive_org_domain_name("alice@gmail.com"), "gmail.com"
        )

    def test_uppercase_lowered(self):
        self.assertEqual(
            derive_org_domain_name("Alice@EXAMPLE.COM"), "example.com"
        )

    def test_no_at_sign(self):
        self.assertEqual(derive_org_domain_name("invalid"), "")

    def test_empty(self):
        self.assertEqual(derive_org_domain_name(""), "")
        self.assertEqual(derive_org_domain_name(None), "")


class IsGenericEmailDomainTests(SimpleTestCase):
    """is_generic_email_domain：汎用ドメインの判定。"""

    def test_gmail(self):
        self.assertTrue(is_generic_email_domain("gmail.com"))

    def test_yahoo_jp(self):
        self.assertTrue(is_generic_email_domain("yahoo.co.jp"))

    def test_normal_domain(self):
        self.assertFalse(is_generic_email_domain("example.com"))
        self.assertFalse(is_generic_email_domain("meiji-tech.co.jp"))

    def test_case_insensitive(self):
        self.assertTrue(is_generic_email_domain("GMAIL.COM"))

    def test_empty(self):
        self.assertFalse(is_generic_email_domain(""))
        self.assertFalse(is_generic_email_domain(None))


# ======================================================================
# check_name_consistency（仕様書 §2.4.1）
# ======================================================================

class CheckNameConsistencyTests(SimpleTestCase):
    """check_name_consistency：4 種類の整合性チェック、補正は下げる方向のみ。"""

    def _block(self, **kv):
        """簡易ブロック組み立て（value/confidence dict 形式）。"""
        defaults = {
            "original_script": {"value": "", "confidence": "high"},
            "last_name": {"value": "", "confidence": "high"},
            "first_name": {"value": "", "confidence": "high"},
            "other_name_parts": {"value": "", "confidence": "high"},
            "name_order": {"value": "", "confidence": "high"},
            "salutation_name": {"value": "", "confidence": "high"},
            "primary_lang": {"value": "ja", "confidence": "high"},
        }
        for k, v in kv.items():
            if isinstance(v, dict):
                defaults[k] = v
            else:
                defaults[k] = {"value": v, "confidence": "high"}
        return defaults

    def test_full_consistency_no_downgrade(self):
        """整合性 OK なら confidence は変えない。"""
        block = self._block(
            original_script="山田太郎",
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            salutation_name="山田 様",
            primary_lang="ja",
        )
        result = check_name_consistency(block)
        for k in ("last_name", "first_name", "other_name_parts", "name_order", "salutation_name"):
            self.assertEqual(result[k], "high", msg=f"{k} should remain high")

    def test_low_coverage_downgrades_name_parts(self):
        """文字カバー率不足 → last_name / first_name / other_name_parts を mid に。"""
        block = self._block(
            original_script="山田太郎佐藤花子",  # 8 文字
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            salutation_name="山田 様",
            primary_lang="ja",
        )
        result = check_name_consistency(block)
        # 山田太郎(4)に対し original_script(8)、カバー率 = 4/8 = 50%
        self.assertEqual(result["last_name"], "mid")
        self.assertEqual(result["first_name"], "mid")
        self.assertEqual(result["other_name_parts"], "mid")

    def test_name_order_single_with_both_parts_downgrades(self):
        """name_order=single なのに last_name と first_name 両方値あり → name_order を low に。"""
        block = self._block(
            original_script="Cher",
            last_name="A",
            first_name="B",
            name_order="single",
            primary_lang="en",
        )
        result = check_name_consistency(block)
        self.assertEqual(result["name_order"], "low")

    def test_name_order_last_first_missing_part_downgrades(self):
        """name_order=last_first なのに first_name 空 → name_order を low に。"""
        block = self._block(
            original_script="山田",
            last_name="山田",
            first_name="",
            name_order="last_first",
            primary_lang="ja",
        )
        result = check_name_consistency(block)
        self.assertEqual(result["name_order"], "low")

    def test_primary_lang_ja_with_first_last_downgrades(self):
        """primary_lang=ja で name_order=first_last → name_order を mid に。"""
        block = self._block(
            original_script="山田太郎",
            last_name="山田",
            first_name="太郎",
            name_order="first_last",
            primary_lang="ja",
        )
        result = check_name_consistency(block)
        self.assertEqual(result["name_order"], "mid")

    def test_salutation_does_not_contain_last_name_downgrades(self):
        """primary_lang=ja で salutation_name に last_name 不含 → salutation_name を low に。"""
        block = self._block(
            original_script="山田太郎",
            last_name="山田",
            first_name="太郎",
            name_order="last_first",
            salutation_name="佐藤 様",
            primary_lang="ja",
        )
        result = check_name_consistency(block)
        self.assertEqual(result["salutation_name"], "low")

    def test_does_not_upgrade(self):
        """元 confidence が low なら整合性 OK でも low のまま（上げない方向）。"""
        block = self._block(
            original_script="山田太郎",
            last_name={"value": "山田", "confidence": "low"},
            first_name="太郎",
            name_order="last_first",
            primary_lang="ja",
        )
        result = check_name_consistency(block)
        self.assertEqual(result["last_name"], "low")  # high 化しない


# ======================================================================
# compute_salutation_name（仕様書 §1.5.1 / §1.5.5）
# ======================================================================

class _StubContact:
    """compute_salutation_name の純関数テスト用スタブ（Contact 実体を起こさない）。"""

    def __init__(self, lang="", last_name="", full_name=""):
        self.lang = lang
        self.last_name = last_name
        self.full_name = full_name


class ComputeSalutationNameTests(SimpleTestCase):
    """compute_salutation_name：文化別の組み立てルール（仕様書 §1.5.1）。"""

    def test_ja_with_last_name(self):
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="ja", last_name="山田", full_name="山田太郎")),
            "山田 様",
        )

    def test_ja_fallback_to_full_name(self):
        # last_name 空 → full_name にフォールバック
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="ja", last_name="", full_name="山田太郎")),
            "山田太郎 様",
        )

    def test_ko_with_last_name(self):
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="ko", last_name="金", full_name="金東石")),
            "金 님",
        )

    def test_zh_uses_full_name_only(self):
        # 本フェーズでは性別敬称なし、full_name のみ（v1.7+ で確定実装）
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="zh", last_name="李", full_name="李明")),
            "李明",
        )

    def test_en_dear_full_name(self):
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="en", last_name="Doe", full_name="John Doe")),
            "Dear John Doe,",
        )

    def test_und_dear_full_name(self):
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="und", last_name="", full_name="Anonymous")),
            "Dear Anonymous,",
        )

    def test_other_lang_dear_full_name(self):
        # その他言語も Dear 形式（仕様書 §1.5.1）
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="fr", last_name="Dupont", full_name="Jean Dupont")),
            "Dear Jean Dupont,",
        )

    def test_both_empty_returns_empty(self):
        """last_name も full_name も両方空 → 空文字を返す（呼び出し元の save() で何もしない）。"""
        self.assertEqual(compute_salutation_name(_StubContact(lang="ja")), "")

    def test_lang_case_insensitive(self):
        self.assertEqual(
            compute_salutation_name(_StubContact(lang="JA", last_name="山田")),
            "山田 様",
        )


# ======================================================================
# Contact.save() オーバーライド（仕様書 §1.5.3 / §11.9.7）
# ======================================================================

class ContactSaveOverrideTests(TestCase):
    """Contact.save() オーバーライド：is_manual / 姓系変更検知で compute_salutation_name 呼出。"""

    def setUp(self):
        self.person = Person.objects.create()

    def _create_contact(self, **kwargs):
        defaults = {
            "person": self.person,
            "status": Contact.Status.PRIMARY,
            "full_name": "山田太郎",
            "last_name": "山田",
            "first_name": "太郎",
            "lang": "ja",
        }
        defaults.update(kwargs)
        return Contact.objects.create(**defaults)

    def test_is_manual_true_does_nothing(self):
        """is_manual=True：salutation_name が空でも何もしない（手動入力を尊重）。"""
        c = self._create_contact(
            salutation_name="",
            salutation_name_is_manual=True,
        )
        self.assertEqual(c.salutation_name, "")  # 補完されない

    def test_is_manual_false_empty_salutation_gets_filled(self):
        """is_manual=False かつ salutation_name 空 → compute で補完。"""
        c = self._create_contact(
            salutation_name="",
            salutation_name_is_manual=False,
        )
        self.assertEqual(c.salutation_name, "山田 様")

    def test_is_manual_false_existing_salutation_no_trigger_change(self):
        """is_manual=False かつ salutation_name 値あり・姓系未変更 → 何もしない。"""
        c = self._create_contact(
            salutation_name="旧 山田 様",
            salutation_name_is_manual=False,
        )
        # 初期保存時、空でないので compute は呼ばれない
        self.assertEqual(c.salutation_name, "旧 山田 様")

        # 姓系を変えずに別フィールドだけ変更 → salutation_name は変わらない
        c.title = "課長"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.salutation_name, "旧 山田 様")

    def test_is_manual_false_last_name_change_triggers_recompute(self):
        """is_manual=False かつ last_name 変更 → compute を強制再実行。"""
        c = self._create_contact(
            salutation_name="山田 様",
            salutation_name_is_manual=False,
        )
        c.last_name = "佐藤"
        c.full_name = "佐藤太郎"
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.salutation_name, "佐藤 様")

    def test_is_manual_false_lang_change_triggers_recompute(self):
        """lang 変更でも姓系変更扱い → compute 再実行。"""
        c = self._create_contact(
            salutation_name="山田 様",
            salutation_name_is_manual=False,
        )
        c.lang = "en"
        c.save()
        c.refresh_from_db()
        # lang=en では Dear {full_name},
        self.assertEqual(c.salutation_name, "Dear 山田太郎,")

    def test_empty_material_no_save_failure(self):
        """姓系材料が全く無いケース：salutation_name は空文字のまま、保存自体は通る。"""
        c = self._create_contact(
            full_name="",  # 空にする（NOT NULL 制約だが default='' なので OK）
            last_name="",
            first_name="",
            salutation_name="",
            salutation_name_is_manual=False,
        )
        # 両方空なら compute は空文字を返す → salutation_name は空のまま
        self.assertEqual(c.salutation_name, "")

    def test_existing_salutation_recomputed_after_name_order_change(self):
        """name_order 変更も姓系変更扱い → compute 再実行。"""
        c = self._create_contact(
            salutation_name="山田 様",
            salutation_name_is_manual=False,
            name_order=Contact.NameOrder.LAST_FIRST,
        )
        # name_order を変えると _SALUTATION_TRIGGER_FIELDS に含まれるので再計算が走る
        c.name_order = Contact.NameOrder.SINGLE
        c.save()
        c.refresh_from_db()
        # compute_salutation_name は lang/last_name/full_name で決まるので、
        # name_order 自体は出力に影響しないが、再計算は走り同じ結果になる
        self.assertEqual(c.salutation_name, "山田 様")
