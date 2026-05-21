"""Contact データの最低限情報判定（仕様書 v1.6.0 §7.3 / 旧 v1.2.2 §8.6.2 系）。

BusinessCard 作成可否を判定するための純関数。

判定ロジック：
- full_name が必須（strip 後に非空）
- かつ organization / email / personal_phone / mobile_phone のいずれか1つ以上が strip 後に非空

personal_phone は v1.6.0 から JSONField(default=list)。リスト・文字列いずれも判定で
非空扱いするように _stripped を JSON/list 対応に拡張する（純関数のまま、判定は最低限）。

strip 処理ルール：
- None → 空文字扱い
- "   " → strip 後に空文字
- "\\n" → strip 後に空文字
- [] / [""] / ["   "] → strip 後に空文字扱い
- ["090-..."] → 非空扱い

「最低限の情報があるか」の判定であり、メールアドレスの形式妥当性などはここの責務外。
"""

_REQUIRED_FIELD = "full_name"
_AT_LEAST_ONE_OF = ("organization", "email", "personal_phone", "mobile_phone")


def has_minimum_info(contact_dict):
    """Contact として最低限の情報があるかを返す。

    [性質] 純関数（DB操作なし・副作用なし）
    [入力] contact_dict: dict（normalize_to_contact_dict の出力）
    [出力] bool（True なら BusinessCard / Contact を作成可）
    """
    if not isinstance(contact_dict, dict):
        return False
    full_name = _stripped(contact_dict.get(_REQUIRED_FIELD))
    if not full_name:
        return False
    for field in _AT_LEAST_ONE_OF:
        if _stripped(contact_dict.get(field)):
            return True
    return False


def _stripped(value):
    """[性質] 純関数

    list（personal_phone / personal_fax の JSONField）は要素を strip して非空のものが
    1 つでもあれば連結文字列として返す。それ以外は str(value).strip()。
    """
    if value is None:
        return ""
    if isinstance(value, list):
        non_empty = [str(v).strip() for v in value if v is not None]
        non_empty = [v for v in non_empty if v]
        return " ".join(non_empty)
    return str(value).strip()
