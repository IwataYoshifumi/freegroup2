"""電話・FAX番号の表示用フォーマット化テンプレートタグ（自動リロード更新）。

DB には正規化済みの生値（E.164）を保存し、画面表示時にのみ国別（国内/国際）整形する。
"""

from django import template

from contacts.services.normalization import format_phone_number_display

register = template.Library()


@register.simple_tag
def format_phone(phone_value):
    """電話・FAX番号を国際対応フォーマットして返す表示用テンプレートタグ。

    使い方：
      インライン: {% format_phone contact.mobile_phone %}
      代入:      {% format_phone value as field_display %}
    """
    return format_phone_number_display(phone_value)
