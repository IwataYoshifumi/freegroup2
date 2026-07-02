"""lang_label テンプレフィルタのユニットテスト（v1.7）。

一覧列の言語ラベル変換。lower().startswith() 判定・地域コード寄せ・未知値の生値保持を担保。
"""

from django.test import SimpleTestCase

from contacts.templatetags.lang_tags import lang_label


class LangLabelFilterTests(SimpleTestCase):
    def test_ja(self):
        self.assertEqual(lang_label("ja"), "日本語")

    def test_ja_region_code(self):
        # ja-JP も接頭辞で日本語に寄せる。
        self.assertEqual(lang_label("ja-JP"), "日本語")

    def test_ja_uppercase(self):
        # lower() 判定なので大文字も日本語扱い。
        self.assertEqual(lang_label("JA"), "日本語")

    def test_en(self):
        self.assertEqual(lang_label("en"), "英語")

    def test_en_region_code(self):
        self.assertEqual(lang_label("en-US"), "英語")

    def test_ko(self):
        self.assertEqual(lang_label("ko"), "韓国語")

    def test_zh(self):
        self.assertEqual(lang_label("zh"), "中国語")

    def test_zh_region_code(self):
        self.assertEqual(lang_label("zh-CN"), "中国語")

    def test_und(self):
        self.assertEqual(lang_label("und"), "未判定")

    def test_empty_string(self):
        self.assertEqual(lang_label(""), "未設定")

    def test_whitespace_only(self):
        # strip 後に空なら未設定。
        self.assertEqual(lang_label("   "), "未設定")

    def test_none(self):
        self.assertEqual(lang_label(None), "未設定")

    def test_unknown_value_is_returned_raw(self):
        # 未知値は握りつぶさず生値をそのまま表示。
        self.assertEqual(lang_label("xx"), "xx")

    def test_unknown_uppercase_kept_as_is(self):
        self.assertEqual(lang_label("XX-YY"), "XX-YY")
