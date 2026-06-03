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


@register.simple_tag
def osd_debug_badge(card):
    """OSD 向き判定（rotate / conf / 失敗）のデバッグ用バッジ HTML を返す。

    [性質] 純関数（DB操作なし・副作用なし。original_image は select_related 済み前提）
    [入力] card: BusinessCard インスタンス
    [出力] SafeString
        - osd が無い（未処理 / 記録なし）: 空文字
        - osd.error あり: 「OSD失敗」バッジ + 理由文字列をそのまま表示
          （"Too few characters" 等の特定文字列で決め打ちせず、error が入っていれば失敗とする）
        - それ以外: rotate（0/90/180/270）バッジ + orientation_conf の生値
    [注意] script は出さない（判定が不安定なため。詳細画面と同じ思想）。
           DEBUG 表示専用（テンプレ側 {% if debug %} でゲートする）。
           引き方は詳細画面と同じ：raw_json["cards"][card.card_index]["osd"]。
    """
    if card is None:
        return mark_safe("")
    raw = getattr(card.original_image, "raw_json", None)
    if not raw:
        return mark_safe("")
    cards = raw.get("cards") or []
    idx = card.card_index
    if not (0 <= idx < len(cards)):
        return mark_safe("")
    entry = cards[idx]
    osd = entry.get("osd") if isinstance(entry, dict) else None
    if not isinstance(osd, dict):
        return mark_safe("")

    error = osd.get("error")
    if error:
        return format_html(
            '<span class="app-status-badge app-status-badge--error">OSD失敗</span> '
            '<span class="app-muted">{}</span>',
            error,
        )

    rotate = osd.get("rotate")
    conf = osd.get("orientation_conf")
    rotate_disp = "—" if rotate is None else "{}°".format(rotate)
    conf_disp = "—" if conf is None else conf
    return format_html(
        '<span class="app-status-badge app-status-badge--info">OSD rot {}</span> '
        '<span class="app-muted">conf {}</span>',
        rotate_disp,
        conf_disp,
    )
