"""lang（言語）生値を一覧列用の日本語ラベルへ変換するテンプレフィルタ（v1.7）。

コンタクト一覧・パーソン一覧の「言語」列で使う。ラベルの単一ソースは
``ContactBaseForm._LANG_CHOICES``（ja/en/ko/zh/und）。フォームの選択肢ラベルは
「日本語 (ja)」のようにコード併記だが、一覧列はコード無しの純ラベルを出すため、
" (code)" サフィックスを剥がして導出する（文言を二重管理しない）。

値の判定は他所の lang 判定（effective_lang / compute_salutation_name 等の
lower().startswith()）と一貫させる。
"""

from django import template

from contacts.forms import ContactBaseForm

register = template.Library()

# _LANG_CHOICES（"ja" → "日本語 (ja)" 形式）から純ラベル（"日本語"）を導出。
_LANG_PURE_LABELS = {
    code: label.rsplit(" (", 1)[0] for code, label in ContactBaseForm._LANG_CHOICES
}


@register.filter
def lang_label(value):
    """lang 生値を日本語ラベルへ変換する（一覧列表示用）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] value: str/None（Contact.lang の生値。ja / en / ko / zh / und / ja-JP / 空 等）
    [出力] str（日本語ラベル）
        - lower().startswith('ja'/'en'/'ko'/'zh') → 日本語 / 英語 / 韓国語 / 中国語
          （ja-JP・zh-CN 等の地域コード付きも接頭辞で寄せる）
        - 'und' → 未判定
        - 空文字・None → 未設定
        - 上記いずれにも当たらない未知値 → 生値をそのまま表示（握りつぶさない）
    """
    s = "" if value is None else str(value).strip()
    if s == "":
        return "未設定"
    low = s.lower()
    for code in ("ja", "en", "ko", "zh"):
        if low.startswith(code):
            return _LANG_PURE_LABELS[code]
    if low == "und":
        return _LANG_PURE_LABELS["und"]
    return value
