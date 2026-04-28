"""raw_json から Contact 用辞書を生成する純関数（仕様書 v1.2.1 §8.5.2 / §5）。

DB 操作は一切行わない。重複チェックや Person マッチングはここでは行わない（後の Phase で別関数化）。

【防御的実装方針（v1.2.1）】
- 例外を raise するのは card_index が cards 配列の範囲外の場合のみ（ValueError）
- 構造想定外、value=null、サポート外 SNS type は confidence=low / 無視 で処理続行
- 仕様書に明記された「ベストエフォートで処理」を優先し、データを最大限取り出す
"""

# fields の単独値フィールド（JSON Schema の Fields）
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

# fields_array の type なし配列 → Contact の単独フィールドへの対応
_TYPELESS_ARRAY_TO_FIELD = {
    "emails": "email",
    "phones": "phone",
    "mobiles": "mobile",
    "fax": "fax",
    "websites": "website",
}

# サポートする social_media の type → そのまま Contact フィールド名として使う
_SOCIAL_MEDIA_TYPES = ("twitter", "linkedin", "facebook", "github", "instagram")

# 防御的処理の既定 confidence
_FALLBACK_CONFIDENCE = "low"
_VALID_CONFIDENCES = ("high", "medium", "low")


def normalize_to_contact_dict(raw_json, card_index):
    """raw_json["cards"][card_index] を Contact フィールド辞書に変換する。

    [性質] 純関数（DB操作なし・副作用なし）
    [入力] raw_json: dict（OriginalImage.raw_json）、card_index: int（cards 配列のインデックス）
    [出力] tuple (contact_dict, confidence_map)
           - contact_dict: Contact のフィールド辞書（key: Contact のフィールド名、value: 文字列）
           - confidence_map: フィールド名 → 'high'/'medium'/'low' の辞書
    [例外] ValueError: card_index が cards 配列の範囲外の場合のみ
    [方針] raw_json の構造想定外でも例外を投げず、ベストエフォートで処理（confidence=low に倒す）
    """
    cards = (raw_json or {}).get("cards") if isinstance(raw_json, dict) else None
    if not isinstance(cards, list):
        raise ValueError(
            f"raw_json['cards'] is not a list (got {type(cards).__name__})"
        )
    if card_index < 0 or card_index >= len(cards):
        raise ValueError(
            f"card_index={card_index} is out of range (cards length={len(cards)})"
        )

    card = cards[card_index]
    if not isinstance(card, dict):
        card = {}

    contact_dict = {}
    confidence_map = {}

    fields = card.get("fields")
    if not isinstance(fields, dict):
        fields = {}
    for name in _SCALAR_FIELDS:
        value, confidence = _extract_value_and_confidence(fields.get(name))
        contact_dict[name] = value
        if confidence is not None:
            confidence_map[name] = confidence

    fields_array = card.get("fields_array")
    if not isinstance(fields_array, dict):
        fields_array = {}

    for arr_key, contact_field in _TYPELESS_ARRAY_TO_FIELD.items():
        contact_dict[contact_field] = ""
        arr = fields_array.get(arr_key)
        if not isinstance(arr, list) or not arr:
            continue
        value, confidence = _extract_value_and_confidence(arr[0])
        contact_dict[contact_field] = value
        if confidence is not None:
            confidence_map[contact_field] = confidence

    for sns_type in _SOCIAL_MEDIA_TYPES:
        contact_dict[sns_type] = ""

    social_media = fields_array.get("social_media")
    if isinstance(social_media, list):
        for entry in social_media:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("type")
            if entry_type not in _SOCIAL_MEDIA_TYPES:
                # サポート外 SNS type は無視（仕様書 v1.2.1 §8.5.2 防御的処理）
                continue
            if contact_dict[entry_type]:
                # 同一 type が複数ある場合は最初の1つを採用（後勝ちにしない）
                continue
            value, confidence = _extract_value_and_confidence(entry)
            contact_dict[entry_type] = value
            if confidence is not None:
                confidence_map[entry_type] = confidence

    return contact_dict, confidence_map


def _extract_value_and_confidence(field):
    """[性質] 純関数

    フィールド辞書から (value: str, confidence: str | None) を取り出す。
    - field が None（キーごと存在しない）：("", None) を返す。情報なしとして
      呼び出し側で confidence_map に記録しない。
    - field が dict 以外（None 以外）：("", "low") を返す。構造想定外として
      防御的に低信頼で記録する。
    - field が dict で value=null / value キー欠落：("", "low") を返す。
    - field が dict で confidence が想定外の値：(value.strip(), "low") を返す。
    - field が dict で正常：(value.strip(), confidence) を返す。
    """
    if field is None:
        return "", None
    if not isinstance(field, dict):
        return "", _FALLBACK_CONFIDENCE
    raw_value = field.get("value")
    if raw_value is None:
        return "", _FALLBACK_CONFIDENCE
    value = str(raw_value).strip()
    raw_confidence = field.get("confidence")
    if raw_confidence in _VALID_CONFIDENCES:
        return value, raw_confidence
    return value, _FALLBACK_CONFIDENCE
