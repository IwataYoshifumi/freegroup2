"""cards アプリの UI 用カスタムテンプレートタグ。

Django simple_tag パターンで実装し、テンプレート側からは
{% load ui_tags %} で読み込んで {% タグ名 引数 %} の形式で呼び出す。
"""

from django.template import Library
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from cards.models import BusinessCard

register = Library()


_OCR_RESULT_BADGE_VARIANT = {
    BusinessCard.OcrResult.NOT_BUSINESS_CARD: "muted",
    BusinessCard.OcrResult.INSUFFICIENT_INFO: "warning",
    BusinessCard.OcrResult.OCR_FAILED: "error",
    BusinessCard.OcrResult.OTHERS: "muted",
}


@register.simple_tag
def ocr_result_badge(card):
    """BusinessCard.ocr_result の値に応じたバッジ HTML を返す。

    [性質] 純関数（DB操作なし・副作用なし）
    [入力] card: BusinessCard インスタンス
    [出力] SafeString
        - ocr_result が business_card のとき: 空文字（バッジを描画しない）
        - それ以外（not_business_card / insufficient_info / ocr_failed / others）:
          app-status-badge + バリアントクラスのバッジを描画
    """
    if card is None:
        return mark_safe("")
    value = card.ocr_result
    if value == BusinessCard.OcrResult.BUSINESS_CARD:
        return mark_safe("")
    variant = _OCR_RESULT_BADGE_VARIANT.get(value, "muted")
    label = card.get_ocr_result_display()
    return format_html(
        '<span class="app-status-badge app-status-badge--{}">{}</span>',
        variant,
        label,
    )
