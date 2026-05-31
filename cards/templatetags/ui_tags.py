"""cards アプリの UI 用カスタムテンプレートタグ（仕様書 §11.8.2）。

Django simple_tag パターンで実装し、テンプレート側からは
{% load ui_tags %} で読み込んで {% タグ名 引数 %} の形式で呼び出す。

D-3b で追加：
  - {% confidence confidences field_name format %}
      Contact フィールド単位の confidence バッジ表示。
      format='badge' のみサポート。
  - {% contact_confidence contact format %}
      Contact 全体の集計サマリー表示。
      format='summary' のみサポート。

D-3d 準備で追加：
  - {% confidence_state confidences field_name %}
      data-confidence-state 属性用の状態文字列を返す。
      'high' / 'mid' / 'low' / 'confirmed' のいずれか（v1.6.0 で medium → mid 統一）。

v1.5.0（OCR パイプライン分離）で追加：
  - {% ocr_result_badge card %}
      BusinessCard.ocr_status / ocr_result に応じたバッジ表示。
"""

from django.template import Library
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from cards.models import BusinessCard
from contacts.models import ContactFieldConfidence


register = Library()


# ----------------------------------------------------------------------
# {% confidence %} : フィールド単位の confidence バッジ
# ----------------------------------------------------------------------

_CONFIDENCE_BADGE_VARIANT = {
    "low": ("error", "低"),
    "mid": ("warning", "中"),
}


@register.simple_tag
def confidence(confidences, field_name, format="badge"):
    """フィールド単位の confidence バッジを返す（仕様書 §11.8.2）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] confidences: dict[field_name -> ContactFieldConfidence]
                        （Contact.get_field_confidences() の戻り値）
           field_name: str（対象フィールド名）
           format: str（'badge' のみサポート、D-3b）
    [出力] SafeString
        - confidence='high'（疑似インスタンス）: 空文字（バッジを描画しない）
        - low/mid AND confirmed_at IS NULL: 低 / 中 バッジ
        - confirmed_at IS NOT NULL: 空文字（恒常表示しない、HIG v1.4 原則4）。
          確認直後の一時バッジは app.js の applyConfirmedState がクライアント挿入（原則5）。

    判定ロジック：
      ContactFieldConfidence は low/mid のみ DB レコードが存在する設計
      （§10.6 / §4.6.1）。get_field_confidences() は high のフィールドに対して
      confidence='high' / pk=None の疑似インスタンスを返す。
    """
    if format != "badge":
        # D-3b では badge のみサポート
        return mark_safe("")

    if not confidences or field_name not in confidences:
        return mark_safe("")

    cfc = confidences[field_name]
    if cfc is None or cfc.confidence == "high":
        # 疑似 high インスタンス → バッジなし
        return mark_safe("")

    if cfc.confirmed_at is not None:
        # 確認済みは恒常表示しない（HIG v1.4 原則4：正常状態にバッジを出さない）。
        # 確認直後の一時バッジは app.js の applyConfirmedState がクライアント挿入する
        # （原則5：操作には応答を返す）。リロード・離脱で消えるのが正しい状態。
        return mark_safe("")

    variant_label = _CONFIDENCE_BADGE_VARIANT.get(cfc.confidence)
    if variant_label is None:
        return mark_safe("")
    variant, label = variant_label
    return format_html(
        '<span class="app-status-badge app-status-badge--{}">{}</span>',
        variant,
        label,
    )


# ----------------------------------------------------------------------
# {% contact_confidence %} : Contact 全体の集計サマリー
# ----------------------------------------------------------------------


@register.simple_tag
def contact_confidence(contact, format="summary"):
    """Contact 全体の confidence 集計サマリーを返す（仕様書 §11.8.2）。

    [性質] 準関数（ContactFieldConfidence への DB 読み取り 1 クエリのみ）
    [入力] contact: Contact インスタンス
           format: str（'summary' のみサポート、D-3b）
    [出力] SafeString
        - 未確認件数 N、総 mid/low 数 M を「未確認 N 件 / 全 M 件中」テキストで返す
        - M=0（CFC レコードなし、すべて疑似 high）: 「確認すべきフィールドはありません」
        - N=0 AND M>0: 「全 M 件確認済み」

    集計ロジック：
      ContactFieldConfidence は low/mid のみ DB に存在するので（v1.6.0 で medium → mid 統一）、
      contact.confidences.count() = mid/low の総数、
      confirmed_at IS NULL の件数 = 未確認件数。
    """
    if format != "summary":
        return mark_safe("")

    if contact is None:
        return mark_safe("")

    qs = ContactFieldConfidence.objects.filter(contact=contact)
    total = qs.count()
    if total == 0:
        return mark_safe(
            '<span class="app-muted">確認すべきフィールドはありません</span>'
        )

    unconfirmed = qs.filter(confirmed_at__isnull=True).count()
    if unconfirmed == 0:
        return format_html(
            '<span class="app-status-badge app-status-badge--success">'
            "全 {} 件確認済み</span>",
            total,
        )

    return format_html(
        '未確認 <strong class="js-unconfirmed-count">{}</strong> 件 / 全 {} 件中',
        unconfirmed,
        total,
    )


# ----------------------------------------------------------------------
# {% confidence_state %} : data-confidence-state 属性用の状態文字列
# ----------------------------------------------------------------------


@register.simple_tag
def confidence_state(confidences, field_name):
    """data-confidence-state 属性に出力する状態文字列を返す（D-3d 準備）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] confidences: dict[field_name -> ContactFieldConfidence]
                        （Contact.get_field_confidences() の戻り値）
           field_name: str（対象フィールド名）
       [出力] str（'high' / 'mid' / 'low' / 'confirmed' のいずれか）

    判定ロジック：
      - 疑似 high インスタンス（CFC レコードなし）→ 'high'
      - confirmed_at IS NOT NULL → 'confirmed'
      - confidence='mid' AND 未確認 → 'mid'（v1.6.0 で medium → mid 統一）
      - confidence='low' AND 未確認 → 'low'

    JS 側はこの値で「修正 UI を出すかどうか」「未確認バッジを出すかどうか」を判定する。
    """
    if not confidences or field_name not in confidences:
        return "high"

    cfc = confidences[field_name]
    if cfc is None or cfc.confidence == "high":
        return "high"

    if cfc.confirmed_at is not None:
        return "confirmed"

    if cfc.confidence == "mid":
        return "mid"
    if cfc.confidence == "low":
        return "low"

    return "high"


# ----------------------------------------------------------------------
# {% ocr_result_badge %} : BusinessCard.ocr_status / ocr_result のバッジ
# ----------------------------------------------------------------------

_OCR_RESULT_BADGE_VARIANT = {
    BusinessCard.OcrResult.NOT_BUSINESS_CARD: "muted",
    BusinessCard.OcrResult.INSUFFICIENT_INFO: "warning",
    BusinessCard.OcrResult.OCR_FAILED: "error",
    BusinessCard.OcrResult.OTHERS: "muted",
}

# v1.5.0: ocr_status 由来のバッジ。ocr_status が pending / processing のとき優先表示。
_OCR_STATUS_BADGE = {
    BusinessCard.OcrStatus.PENDING:    ("muted", "OCR待ち"),
    BusinessCard.OcrStatus.PROCESSING: ("info",  "OCR中"),
}


@register.simple_tag
def ocr_result_badge(card):
    """BusinessCard の状態に応じたバッジ HTML を返す。

    [性質] 純関数（DB操作なし・副作用なし）
    [入力] card: BusinessCard インスタンス
    [出力] SafeString
        - ocr_status が pending → 「OCR待ち」バッジ（muted）
        - ocr_status が processing → 「OCR中」バッジ（info）
        - ocr_status が done / failed の場合は ocr_result 由来：
          - business_card → 空文字（バッジを描画しない）
          - not_business_card / insufficient_info / ocr_failed / others → 5 値バッジ
          - None など想定外 → 空文字
    """
    if card is None:
        return mark_safe("")

    status_badge = _OCR_STATUS_BADGE.get(card.ocr_status)
    if status_badge is not None:
        variant, label = status_badge
        return format_html(
            '<span class="app-status-badge app-status-badge--{}">{}</span>',
            variant,
            label,
        )

    value = card.ocr_result
    if value is None or value == BusinessCard.OcrResult.BUSINESS_CARD:
        return mark_safe("")
    variant = _OCR_RESULT_BADGE_VARIANT.get(value, "muted")
    label = card.get_ocr_result_display()
    return format_html(
        '<span class="app-status-badge app-status-badge--{}">{}</span>',
        variant,
        label,
    )
