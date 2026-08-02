"""電話番号・FAX表示の国際対応フォーマット化テスト。"""

from django.test import TestCase
from contacts.services.normalization import format_phone_number_display
from contacts.templatetags.phone_tags import format_phone


class PhoneFormattingTests(TestCase):
    def test_jp_landline_formatting(self):
        """日本の固定電話番号が国内形式に変換される。"""
        self.assertEqual(format_phone_number_display("81565356826"), "0565-35-6826")
        self.assertEqual(format_phone_number_display("+81565356826"), "0565-35-6826")

    def test_jp_mobile_formatting(self):
        """日本の携帯電話番号が国内形式に変換される。"""
        self.assertEqual(format_phone_number_display("818036103605"), "080-3610-3605")
        self.assertEqual(format_phone_number_display("+818036103605"), "080-3610-3605")

    def test_international_formatting(self):
        """日本以外の電話番号が国際形式に変換される。"""
        self.assertEqual(format_phone_number_display("12065550100"), "+1 206-555-0100")
        self.assertEqual(format_phone_number_display("442079460912"), "+44 20 7946 0912")

    def test_fallback_on_invalid_or_empty(self):
        """パース不能な値や空値はそのままフォールバックする。"""
        self.assertEqual(format_phone_number_display(""), "")
        self.assertEqual(format_phone_number_display(None), "")
        self.assertEqual(format_phone_number_display("invalid"), "invalid")

    def test_template_tag_format_phone(self):
        """templatetag format_phone が正常動作する。"""
        self.assertEqual(format_phone("81565356826"), "0565-35-6826")
