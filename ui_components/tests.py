"""ui_components の汎用バッジタグ（badge_tags）のユニットテスト。

status_badge / bool_badge はいずれも純関数（DB 操作なし）。get_FIELD_display は
in-memory の choices を引くだけなので、未保存インスタンスで足り、SimpleTestCase で回す。
"""

from django.template import Context, Template
from django.test import SimpleTestCase

from cards.models import BusinessCard
from contacts.models import Contact
from mailings.models import Campaign
from persons.models import Person
from ui_components.templatetags.badge_tags import bool_badge, status_badge


def _render(template_str, context):
    return Template(template_str).render(Context(context)).strip()


class StatusBadgeTests(SimpleTestCase):
    def test_display_text_comes_from_model_label(self):
        """テキストは get_FIELD_display()（モデルラベル）から引く・英字値は出さない。"""
        html = status_badge(Contact(status="primary"))
        self.assertIn("主コンタクト", html)
        self.assertNotIn("primary", html)

    def test_variant_from_color_map(self):
        """色（variant）は色マップから引く（Contact.primary → success）。"""
        html = status_badge(Contact(status="primary"))
        self.assertIn("app-status-badge--success", html)

    def test_color_map_is_keyed_by_field_context(self):
        """同じ "active" でも (モデル, フィールド) 文脈で別の色に解決される。

        Contact.status="active"（副コンタクト）→ warning、
        Person.status="active"（通常）→ success。値だけのフラット辞書では混線する箇所。
        """
        contact_html = status_badge(Contact(status="active"))
        person_html = status_badge(Person(status="active"))

        self.assertIn("副コンタクト", contact_html)
        self.assertIn("app-status-badge--warning", contact_html)

        self.assertIn("通常", person_html)
        self.assertIn("app-status-badge--success", person_html)

    def test_person_and_campaign_values(self):
        """Person / Campaign の代表値も display 変換＋色マップ命中する。"""
        self.assertIn("統合済み", status_badge(Person(status="merged")))
        self.assertIn("app-status-badge--info", status_badge(Person(status="merged")))

        self.assertIn("配信失敗", status_badge(Campaign(status="failed")))
        self.assertIn("app-status-badge--error", status_badge(Campaign(status="failed")))

    def test_unregistered_field_falls_back_to_muted_and_display(self):
        """色マップ未登録の (モデル, フィールド) は muted に倒し、テキストは display を出す。

        BusinessCard.ocr_status は色マップに無い → variant=muted。それでも英字の生値ではなく
        get_ocr_status_display() のラベルを出す（安全側 fallback）。
        """
        card = BusinessCard(ocr_status="pending")
        html = status_badge(card, "ocr_status")
        self.assertIn("app-status-badge--muted", html)
        self.assertIn(card.get_ocr_status_display(), html)
        self.assertNotIn(">pending<", html)

    def test_none_instance_returns_empty(self):
        self.assertEqual(status_badge(None), "")

    def test_renders_through_template_load(self):
        """{% load badge_tags %}{% status_badge %} がテンプレート経由で解決される。"""
        html = _render(
            "{% load badge_tags %}{% status_badge c %}",
            {"c": Contact(status="inactive")},
        )
        self.assertIn("非アクティブ", html)
        self.assertIn("app-status-badge--muted", html)


class BoolBadgeTests(SimpleTestCase):
    def test_true_uses_true_label_and_variant(self):
        html = bool_badge(
            True,
            true_label="要確認",
            false_label="確認済み",
            true_variant="warning",
            false_variant="muted",
        )
        self.assertIn("要確認", html)
        self.assertIn("app-status-badge--warning", html)
        self.assertNotIn("確認済み", html)

    def test_false_uses_false_label_and_variant(self):
        html = bool_badge(
            False,
            true_label="要確認",
            false_label="確認済み",
            true_variant="warning",
            false_variant="muted",
        )
        self.assertIn("確認済み", html)
        self.assertIn("app-status-badge--muted", html)
        self.assertNotIn("要確認", html)

    def test_variants_default_when_omitted(self):
        """variant 省略時は既定（True=success / False=muted）。"""
        self.assertIn(
            "app-status-badge--success",
            bool_badge(True, true_label="オン", false_label="オフ"),
        )
        self.assertIn(
            "app-status-badge--muted",
            bool_badge(False, true_label="オン", false_label="オフ"),
        )

    def test_invalid_variant_is_clamped_to_muted(self):
        """5 種以外の variant は muted に丸める（安全側）。"""
        html = bool_badge(
            True, true_label="x", false_label="y", true_variant="rainbow"
        )
        self.assertIn("app-status-badge--muted", html)
        self.assertNotIn("rainbow", html)

    def test_renders_through_template_load_with_kwargs(self):
        """kwargs 形の呼び出しがテンプレート経由で解決される（FG2 初導入の kwargs タグ）。"""
        html = _render(
            '{% load badge_tags %}{% bool_badge v true_label="オン" '
            'false_label="オフ" true_variant="success" false_variant="muted" %}',
            {"v": True},
        )
        self.assertIn("オン", html)
        self.assertIn("app-status-badge--success", html)
