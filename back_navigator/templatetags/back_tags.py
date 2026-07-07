from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def append_back_url(url, back):
    """URLにback_stackを付与して返す"""
    return back.append_url(url)


@register.simple_tag
def back_url(back):
    """直前の画面へ戻るURL文字列のみを返す。

    通常は {% back_link back %} を使う。本タグは URL を href 以外で使う
    （JS / リダイレクトテキスト等）特殊用途のみ。
    """
    return back.back_url


@register.simple_tag
def back_all_url(back):
    """最初の画面へ戻るURL文字列のみを返す。通常は {% back_all_link back %} を使う。"""
    return back.back_all_url


@register.simple_tag
def back_link(back, label=None):
    """戻り先があれば「戻る」リンク（<a>）を返す。なければ空文字。

    HIG 原則7 / §3.2 準拠：判定・URL・ラベル・出す出さないをすべてタグ内部に隠す。
    テンプレ側に {% if back.back_exist %} は書かない。
    ラベルは固定「戻る」（戻る統一の徹底。戻り先の画面名 back.back_title はラベルに
    使わない。href＝戻り先 URL は従来どおり back.back_url）。
    label を渡すとそのラベルを優先する（後方互換のため残置。明示指定時のみ上書き）。
    クラスは戻る・離脱導線の共通表記 app-btn app-btn--secondary（HIG 3.6）。
    """
    if not back.back_exist:
        return ""
    return format_html(
        '<a class="app-btn app-btn--secondary" href="{}">{}</a>',
        back.back_url,
        label or "戻る",
    )


@register.simple_tag
def back_all_link(back):
    """履歴が 2 階層以上あれば「最初に戻る」リンク（<a>）を返す。なければ空文字。

    HIG 原則7 / §3.2 準拠：「戻る」とセットで配置するが、表示判定はテンプレに書かない。
    ラベルは固定「最初に戻る」（戻る統一の徹底。起点の画面名 back.back_all_title は
    ラベルに使わない。href＝起点 URL は従来どおり back.back_all_url）。
    """
    if not back.back_all_exist:
        return ""
    return format_html(
        '<a class="app-btn app-btn--secondary" href="{}">{}</a>',
        back.back_all_url,
        "最初に戻る",
    )


@register.simple_tag
def hidden_back_field(back):
    """POSTフォーム用のhiddenフィールドを返す"""
    return back.hidden_fields()
