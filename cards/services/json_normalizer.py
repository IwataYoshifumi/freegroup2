"""raw_json から Contact 用辞書を生成する純関数（仕様書 v1.1.0 §8.4.2 / §5）。

DB 操作は一切行わない。重複チェックや Person マッチングもここでは行わない（後の Phase で別関数化）。
"""

# fields の単独値フィールド（§5.4）
_SCALAR_FIELDS = (
    "full_name",
    "last_name",
    "first_name",
    "salutation_name",
    "company",
    "department",
    "title",
    "address",
    "notes",
)

# fields_array の type なし配列 → Contact の単独フィールドへの対応（§5.5・§8.4.2）
_TYPELESS_ARRAY_TO_FIELD = {
    "emails": "email",
    "phones": "phone",
    "mobiles": "mobile",
    "fax": "fax",
    "websites": "website",
}

# social_media の type → Contact フィールド名（§5.5・§8.4.2）
_SOCIAL_MEDIA_TYPES = ("twitter", "linkedin", "facebook", "github", "instagram")


def normalize_to_contact_dict(raw_json):
    """raw_json を Contact フィールド辞書と confidence マップに変換する。

    [性質] 純関数（DB操作なし・副作用なし）
    [入力] raw_json: dict（Claude API の Tool Use 結果、仕様書 第5章の構造）
    [出力] tuple (contact_dict, confidence_map)
           - contact_dict: Contact のフィールド辞書
           - confidence_map: フィールド名 → confidence の辞書
    """
    contact_dict = {}
    confidence_map = {}

    fields = raw_json.get("fields", {}) or {}
    for name in _SCALAR_FIELDS:
        field = fields.get(name)
        contact_dict[name] = _extract_value(field)
        confidence = _extract_confidence(field)
        if confidence:
            confidence_map[name] = confidence

    fields_array = raw_json.get("fields_array", {}) or {}
    for arr_key, contact_field in _TYPELESS_ARRAY_TO_FIELD.items():
        arr = fields_array.get(arr_key) or []
        contact_dict[contact_field] = _extract_first_array_value(arr)
        confidence = _extract_first_array_confidence(arr)
        if confidence:
            confidence_map[contact_field] = confidence

    social_media = fields_array.get("social_media") or []
    for sns_type in _SOCIAL_MEDIA_TYPES:
        contact_dict[sns_type] = ""
    for entry in social_media:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type in _SOCIAL_MEDIA_TYPES:
            contact_dict[entry_type] = _extract_value(entry)
            confidence = _extract_confidence(entry)
            if confidence:
                confidence_map[entry_type] = confidence

    return contact_dict, confidence_map


def _extract_value(field_dict):
    """[性質] 純関数"""
    if not isinstance(field_dict, dict):
        return ""
    value = field_dict.get("value", "")
    if value is None:
        return ""
    return str(value).strip()


def _extract_confidence(field_dict):
    """[性質] 純関数"""
    if not isinstance(field_dict, dict):
        return ""
    return field_dict.get("confidence", "") or ""


def _extract_first_array_value(arr):
    """[性質] 純関数"""
    if not arr:
        return ""
    return _extract_value(arr[0])


def _extract_first_array_confidence(arr):
    """[性質] 純関数"""
    if not arr:
        return ""
    return _extract_confidence(arr[0])
