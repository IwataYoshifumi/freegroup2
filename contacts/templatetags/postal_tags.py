"""postal_code の国別表示整形テンプレートタグ（Phase D2 話2）。

DB には正規化済みの生値（数字列）を保存し、画面表示時にのみ国別整形する。
compose_full_address と同じ ``format_postal_by_country`` を共有する（contacts.services.normalization）。
"""

from django import template

from contacts.services.normalization import format_postal_by_country

register = template.Library()


@register.simple_tag
def format_postal(postal_code, country):
    """postal_code を country 別に表示整形して返す（Phase D2 話2）。

    使い方：
      インライン：{% format_postal contact.postal_code contact.country %}
      代入       ：{% format_postal value contact.country as display_postal %}

    JP の 7 桁は "123-4567"、US の 9 桁は "94103-1234"、それ以外・未対応国は生値のまま。
    DB の値は変更しない（表示専用）。
    """
    return format_postal_by_country(postal_code, country)
