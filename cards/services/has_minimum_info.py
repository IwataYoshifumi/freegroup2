"""Contact データの最低限情報判定（仕様書 v1.2.1 §8.5.3 / v1.2.2 §8.6.2）。

BusinessCard 作成可否を判定するための純関数。

判定ロジック：
- full_name が必須（strip 後に非空）
- かつ company / email / phone / mobile のいずれか1つ以上が strip 後に非空

strip 処理ルール（v1.2.2 §8.6.2）：
- None → 空文字扱い
- "   " → strip 後に空文字
- "\\n" → strip 後に空文字

「最低限の情報があるか」の判定であり、メールアドレスの形式妥当性などはここの責務外。
"""

_REQUIRED_FIELD = "full_name"
_AT_LEAST_ONE_OF = ("company", "email", "phone", "mobile")


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
    """[性質] 純関数"""
    if value is None:
        return ""
    return str(value).strip()
