"""duplicates アプリのテンプレートフィルタ（v1.7 UI 改善、§11.5.1）。

review_result（JSONField = list[str]）は get_..._display が効かないため、
choices 定義の (value, label) から value→label 変換表を作って日本語表示する。
変換表は独自の訳語を作らず、DuplicateMergeReason / DifferentPersonReason の
choices ラベルをそのまま使う（value 衝突は両 enum 間に無い）。
"""

from django import template

from config.constants import DifferentPersonReason, DuplicateMergeReason

register = template.Library()


# value→日本語ラベルの統合変換表（マージ系 7 値 + 別人確定系 3 値）。
# str() で gettext_lazy を確定文字列化する（import 時に 1 回だけ構築）。
_REVIEW_RESULT_LABELS = {
    **{value: str(label) for value, label in DuplicateMergeReason.choices},
    **{value: str(label) for value, label in DifferentPersonReason.choices},
}


@register.filter
def review_result_ja(value):
    """review_result（list[str]）を日本語ラベルの読点連結に変換する。

    [性質] 純関数（変換表 lookup のみ、DB 操作なし・副作用なし）
    [入力] value: list[str] | None（DuplicateCandidate.review_result）
    [出力] str（日本語ラベルを「、」連結。未知 value はそのまま残す。空なら空文字）
    """
    if not value:
        return ""
    return "、".join(_REVIEW_RESULT_LABELS.get(v, v) for v in value)
