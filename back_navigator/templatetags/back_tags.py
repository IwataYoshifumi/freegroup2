from django import template

register = template.Library()


@register.simple_tag
def append_back_url(url, back):
    """URLにback_stackを付与して返す"""
    return back.append_url(url)


@register.simple_tag
def back_url(back):
    """直前の画面へ戻るURLを返す"""
    return back.back_url


@register.simple_tag
def back_all_url(back):
    """最初の画面へ戻るURLを返す"""
    return back.back_all_url


@register.simple_tag
def hidden_back_field(back):
    """POSTフォーム用のhiddenフィールドを返す"""
    return back.hidden_fields()
