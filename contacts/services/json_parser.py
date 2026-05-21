"""OCR の raw_json を Contact 用辞書に変換する純関数（仕様書 v1.6.0 §5.1 / §5.2 / §6.5）。

[Phase 3A 全面改修]
旧 v1.3.0 の fields / fields_array 2 階層フラット構造から、v1.6.0 の 8 ブロック構造
（card_meta / name / job / organization / address / contact / uncategorized_card_content /
metadata）へ全面書き直し。Phase 2 で実装済みの contacts.services.normalization の純関数
群を呼び出して各フィールドを正規化し、name ブロック整合性チェック（§2.4.1）と
orientation 補正（§5.3）を適用する。両補正は §5.3.1 に従い「より低い側を採用」する累積方式
で適用する。

[防御方針（§6.5）]
構造想定外（dict でない・value=null・配列でない等）でも例外を投げずベストエフォートで
処理し、confidence を low に倒す。例外を投げるのは card_index が範囲外（または cards が
list として読めない）の ValueError のみ。

[salutation_name の二段構え（§1.5）]
本モジュール（第 1 段）は OCR の salutation_name をそのまま contact_dict に格納する。
OCR が null を返した場合は contact_dict に入れず、第 2 段（Contact.save() オーバーライド
の compute_salutation_name）に委ねる。compute_salutation_name は本モジュールから呼ばない。

[ai_analysis_notes の扱い（§1.7）]
metadata.ai_analysis_notes は raw_json に残るだけで Contact 用辞書には一切入れない。
Contact 側既存 notes（ユーザーメモ）とは無関係。

[orig / org の混同注意]
original_script（個人の氏名原文、name ブロック）と org_name_full（会社名フル、
organization ブロック）は頭 3 文字が同じだが別物。本モジュールでも処理ブロックを明確に
分けている。
"""

from __future__ import annotations

from contacts.services.normalization import (
    check_name_consistency,
    compose_full_address,
    derive_org_core_name,
    derive_org_domain_name,
    normalize_department_title_branch,
    normalize_email,
    normalize_organization,
    normalize_original_script_for_full_name,
    normalize_phone_value,
    normalize_postal_code,
    normalize_rest_of_address,
)


# ----------------------------------------------------------------------
# 定数
# ----------------------------------------------------------------------

_VALID_CONFIDENCES = ("high", "mid", "low")
_FALLBACK_CONFIDENCE = "low"

# 信頼度のランク（low=1, mid=2, high=3）。§5.3.1 競合ルール「より低い側」用。
_CONFIDENCE_RANK = {"high": 3, "mid": 2, "low": 1}

# orientation 補正テーブル（§5.3）。
_CONFIDENCE_DOWNGRADE_90 = {"high": "mid", "mid": "mid", "low": "low"}
_CONFIDENCE_DOWNGRADE_180 = {"high": "low", "mid": "low", "low": "low"}

# サポートする SNS の type → Contact フィールド名は同名（小文字完全一致）。
# OCR プロンプト側で大文字始まりの type 例（LinkedIn / X / WeChat 等）が示されているが、
# 本フェーズは旧 json_normalizer の挙動を踏襲し小文字完全一致のみ採用、他は無視する。
# 大小マッピング・別名（X → twitter 等）対応は Phase 3B 以降で検討。
_SNS_TYPES = ("twitter", "linkedin", "facebook", "github", "instagram")


# ----------------------------------------------------------------------
# 内部ヘルパー（純関数）
# ----------------------------------------------------------------------

def _extract_value_and_confidence(field):
    """[性質] 純関数

    OCR の value/confidence ペア dict から (value: str, confidence: str | None) を取り出す。
    §6.5 防御方針に従い、構造想定外でも例外を投げず confidence を low に倒す。

    - field が None：("", None) を返す。情報なし扱い（呼び出し側で confidence_map に記録しない）。
    - field が dict 以外：("", "low") を返す。構造想定外。
    - field が dict で value=null：("", confidence)。confidence が想定外なら ("", "low")。
    - field が dict で正常：(str(value).strip(), confidence)。confidence が想定外なら "low"。
    """
    if field is None:
        return "", None
    if not isinstance(field, dict):
        return "", _FALLBACK_CONFIDENCE
    raw_value = field.get("value")
    raw_confidence = field.get("confidence")
    if raw_value is None:
        if raw_confidence in _VALID_CONFIDENCES:
            return "", raw_confidence
        return "", _FALLBACK_CONFIDENCE
    value = str(raw_value).strip()
    if raw_confidence in _VALID_CONFIDENCES:
        return value, raw_confidence
    return value, _FALLBACK_CONFIDENCE


def _extract_array_value_and_confidence(field):
    """[性質] 純関数

    OCR の配列フィールド（personal_phone / personal_fax / org_phone / org_fax / sns）の
    value/confidence ペア dict から (values: list, confidence: str | None) を取り出す。
    §6.5 防御方針：構造想定外は ([], "low") に倒す。

    - field が None：([], None)。情報なし扱い。
    - field が dict 以外：([], "low")。
    - field が dict で value が list 以外：([], confidence_or_low)。
    - field が dict で value が list：(value, confidence_or_low)。要素の中身は呼び出し側で処理。
    """
    if field is None:
        return [], None
    if not isinstance(field, dict):
        return [], _FALLBACK_CONFIDENCE
    raw_value = field.get("value")
    raw_confidence = field.get("confidence")
    if raw_confidence not in _VALID_CONFIDENCES:
        confidence = _FALLBACK_CONFIDENCE if raw_value is not None else None
    else:
        confidence = raw_confidence
    if not isinstance(raw_value, list):
        return [], confidence
    return raw_value, confidence


def _min_confidence(a, b):
    """[性質] 純関数

    a と b（'high'/'mid'/'low'）のうちより低い側（より厳しい側）を返す。
    §5.3.1 競合ルール（両補正の累積で最も低い結果に収束）用。
    どちらかが想定外の値なら他方を返す。両方想定外なら fallback ('low') を返す。
    """
    a_valid = a in _CONFIDENCE_RANK
    b_valid = b in _CONFIDENCE_RANK
    if a_valid and b_valid:
        return a if _CONFIDENCE_RANK[a] <= _CONFIDENCE_RANK[b] else b
    if a_valid:
        return a
    if b_valid:
        return b
    return _FALLBACK_CONFIDENCE


def _store_scalar(
    block, ocr_key, contact_field, contact_dict, confidence_map, normalize_fn=None
):
    """[性質] 純関数（contact_dict / confidence_map を mutate する破壊的操作）

    OCR の単一値フィールドを取り出し、必要なら normalize_fn を通して
    contact_dict / confidence_map に格納する補助。空 + confidence='high' のフィールドは
    contact_dict にも confidence_map にも入れない（Contact 側のデフォルト値に委ねる）。

    confidence_map のキーは橋渡し後の Contact フィールド名（contact_field）で揃える（§1.7）。
    """
    value, confidence = _extract_value_and_confidence(block.get(ocr_key))
    if value == "" and confidence == "high":
        return
    if normalize_fn is not None and value:
        value = normalize_fn(value)
    contact_dict[contact_field] = value
    if confidence is not None:
        confidence_map[contact_field] = confidence


def _store_phone_array(block, ocr_key, contact_dict, confidence_map):
    """[性質] 純関数（contact_dict / confidence_map を mutate）

    電話/FAX 系の配列フィールド（personal_phone / personal_fax / org_phone / org_fax）を
    配列のまま contact_dict に格納する（§6.5 末尾、旧「先頭 1 件のみ採用」は廃止）。
    各要素に normalize_phone_value を適用し、空文字になった要素は除外する。
    空配列になった場合は contact_dict / confidence_map に入れない（Contact 側 default=list）。
    """
    raw_values, confidence = _extract_array_value_and_confidence(block.get(ocr_key))
    normalized = [normalize_phone_value(v) for v in raw_values]
    normalized = [v for v in normalized if v]
    if not normalized:
        return
    contact_dict[ocr_key] = normalized
    if confidence is not None:
        confidence_map[ocr_key] = confidence


def _store_sns(contact_block, contact_dict, confidence_map):
    """[性質] 純関数（contact_dict / confidence_map を mutate）

    OCR の contact.sns 配列（[{"type": ..., "id": ...}, ...]）を type ベースで
    Contact の SNS フィールド（twitter / linkedin / facebook / github / instagram）に分配する。
    旧 json_normalizer の挙動を踏襲して以下のルールで処理する：

    - type が _SNS_TYPES の小文字完全一致でない要素は無視（大文字始まり・別名称・未サポート type）
    - 同一 type が複数ある場合は最初の 1 件を採用（後勝ちにしない）
    - id が空文字や None の要素は無視
    - confidence は配列全体に 1 つ（仕様書 §1.4）。要素ごとに分けない
    """
    raw_list, confidence = _extract_array_value_and_confidence(contact_block.get("sns"))
    if not raw_list:
        return
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type not in _SNS_TYPES:
            continue
        if entry_type in contact_dict:
            continue
        entry_id = entry.get("id")
        if entry_id is None:
            continue
        id_value = str(entry_id).strip()
        if not id_value:
            continue
        contact_dict[entry_type] = id_value
        if confidence is not None:
            confidence_map[entry_type] = confidence


# ----------------------------------------------------------------------
# 公開関数
# ----------------------------------------------------------------------

def normalize_to_contact_dict(raw_json, card_index):
    """raw_json["cards"][card_index] を Contact 用辞書に変換する（仕様書 v1.6.0 §5.1 / §5.2）。

    [性質] 純関数（DB 操作なし・副作用なし。check_name_consistency 内部の
            logger.info はサーバーログ仕様、§2.4.1 末尾）
    [入力] raw_json: dict（BC.raw_json_1 / raw_json_2）
           card_index: int（cards 配列のインデックス）
    [出力] tuple (contact_dict, confidence_map)
           - contact_dict: Contact のフィールド辞書（key: Contact フィールド名（橋渡し後）、
             value: str / list）
           - confidence_map: Contact フィールド名 → 'high' / 'mid' / 'low' の辞書。
             空フィールドや high のみのフィールドは記録しない（mid/low のみ実質意味あり）。
    [例外] ValueError: card_index が cards 配列の範囲外、または cards が list として読めない場合
    [防御] それ以外の構造想定外は例外を投げず confidence=low に倒す（§6.5）

    [処理の流れ]
      1. cards[card_index] を取り出す（範囲外なら ValueError）
      2. name ブロック：original_script → full_name コピー（§3.3.1）＋ 残り 8 フィールド同名格納
      3. name 整合性チェック（§2.4.1）：primary_lang を注入して check_name_consistency を呼び、
         5 フィールド分の confidence を §5.3.1 競合ルール（より低い側を採用）で更新
      4. job / organization / address / contact / uncategorized / metadata ブロックを順次格納
         （橋渡し 4 件：org_name_full → organization、full_address → address、
         primary_lang → lang、original_script → full_name）
      5. address は 4 要素（postal_code / region / city / rest_of_address / country / lang）から
         compose_full_address で組み立て直す（§6.4、OCR の full_address は raw_json にのみ残る）
      6. 派生フィールド org_core_name / org_domain_name は OCR が空の場合のみ derive で補完
      7. ai_analysis_notes は contact_dict に入れない（§1.7）
    """
    if isinstance(raw_json, dict):
        cards = raw_json.get("cards")
    else:
        cards = None
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

    name_block = card.get("name") if isinstance(card.get("name"), dict) else {}
    job_block = card.get("job") if isinstance(card.get("job"), dict) else {}
    organization_block = (
        card.get("organization") if isinstance(card.get("organization"), dict) else {}
    )
    address_block = card.get("address") if isinstance(card.get("address"), dict) else {}
    contact_block = card.get("contact") if isinstance(card.get("contact"), dict) else {}
    uncategorized_block = (
        card.get("uncategorized_card_content")
        if isinstance(card.get("uncategorized_card_content"), dict)
        else {}
    )
    metadata_block = (
        card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    )

    # ----- name ブロック -----
    # original_script → full_name コピー処理（§3.3 / §3.3.1 / §3.4）。
    # 最小限正規化は Phase 2 実装の normalize_original_script_for_full_name に集約済み
    # （全角空白→半角・連続空白→1つ・前後空白除去）。
    os_value, os_confidence = _extract_value_and_confidence(
        name_block.get("original_script")
    )
    full_name_value = normalize_original_script_for_full_name(os_value)
    if full_name_value:
        contact_dict["full_name"] = full_name_value
        if os_confidence is not None:
            confidence_map["full_name"] = os_confidence

    # name ブロックの残り 8 フィールド（同名格納、カテゴリ A：軽い整えは strip のみ）
    for ocr_key in (
        "last_name",
        "first_name",
        "other_name_parts",
        "name_order",
        "salutation_name",
        "display_name",
        "phonetic_name",
        "alias_name",
    ):
        _store_scalar(name_block, ocr_key, ocr_key, contact_dict, confidence_map)

    # ----- name 整合性チェック（§2.4.1） -----
    # check_name_consistency は OCR の name ブロック原型（{"value":..., "confidence":...}）を
    # 期待するため、name_block をコピーし primary_lang（metadata 配下）を注入してから渡す。
    name_block_with_lang = dict(name_block)
    name_block_with_lang["primary_lang"] = metadata_block.get("primary_lang")
    consistency_corrections = check_name_consistency(name_block_with_lang)

    # 補正 5 フィールド（last_name / first_name / other_name_parts / name_order /
    # salutation_name）について、§5.3.1 競合ルールで「より低い側」を採用する。
    # contact_dict にそのフィールドがない（空 + high でスキップされた）場合は補正対象外。
    for field_name, corrected in consistency_corrections.items():
        if corrected is None:
            continue
        if field_name not in contact_dict:
            continue
        current = confidence_map.get(field_name, "high")
        merged = _min_confidence(current, corrected)
        if merged != "high":
            confidence_map[field_name] = merged

    # ----- job ブロック -----
    _store_scalar(
        job_block, "title", "title", contact_dict, confidence_map,
        normalize_fn=normalize_department_title_branch,
    )
    _store_scalar(job_block, "qualification", "qualification", contact_dict, confidence_map)
    _store_scalar(
        job_block, "department", "department", contact_dict, confidence_map,
        normalize_fn=normalize_department_title_branch,
    )
    _store_scalar(job_block, "catchphrase", "catchphrase", contact_dict, confidence_map)

    # ----- organization ブロック -----
    # 橋渡し：org_name_full → organization（normalize_organization 適用）
    _store_scalar(
        organization_block, "org_name_full", "organization",
        contact_dict, confidence_map, normalize_fn=normalize_organization,
    )
    _store_scalar(
        organization_block, "org_core_name", "org_core_name",
        contact_dict, confidence_map, normalize_fn=normalize_organization,
    )
    _store_scalar(
        organization_block, "legal_entity_type", "legal_entity_type",
        contact_dict, confidence_map,
    )
    _store_scalar(
        organization_block, "legal_entity_type_code", "legal_entity_type_code",
        contact_dict, confidence_map,
    )
    _store_scalar(
        organization_block, "legal_entity_type_position", "legal_entity_type_position",
        contact_dict, confidence_map,
    )
    _store_scalar(
        organization_block, "org_domain_name", "org_domain_name",
        contact_dict, confidence_map,
    )
    _store_scalar(
        organization_block, "branch", "branch", contact_dict, confidence_map,
        normalize_fn=normalize_department_title_branch,
    )

    # ----- address ブロック -----
    # 橋渡し：full_address → address だが、§6.4 の方針により最終的に Contact.address は
    # compose_full_address で 4 要素から組み立てる。OCR の full_address は raw_json に残る
    # だけで Contact には反映しない。
    _store_scalar(
        address_block, "postal_code", "postal_code",
        contact_dict, confidence_map, normalize_fn=normalize_postal_code,
    )
    _store_scalar(address_block, "country", "country", contact_dict, confidence_map)
    _store_scalar(address_block, "region", "region", contact_dict, confidence_map)
    _store_scalar(address_block, "city", "city", contact_dict, confidence_map)
    _store_scalar(
        address_block, "rest_of_address", "rest_of_address",
        contact_dict, confidence_map, normalize_fn=normalize_rest_of_address,
    )

    # ----- contact ブロック -----
    _store_scalar(
        contact_block, "email", "email", contact_dict, confidence_map,
        normalize_fn=normalize_email,
    )
    _store_scalar(
        contact_block, "mobile_phone", "mobile_phone", contact_dict, confidence_map,
        normalize_fn=normalize_phone_value,
    )
    # 配列 4 件（§6.5 末尾「配列のまま JSONField に格納、旧『先頭 1 件のみ採用』は廃止」）
    _store_phone_array(contact_block, "personal_phone", contact_dict, confidence_map)
    _store_phone_array(contact_block, "personal_fax", contact_dict, confidence_map)
    _store_phone_array(contact_block, "org_phone", contact_dict, confidence_map)
    _store_phone_array(contact_block, "org_fax", contact_dict, confidence_map)
    # sns（type ベース分配）
    _store_sns(contact_block, contact_dict, confidence_map)

    # ----- uncategorized_card_content ブロック -----
    _store_scalar(
        uncategorized_block, "handwritten_text", "handwritten_text",
        contact_dict, confidence_map,
    )
    _store_scalar(
        uncategorized_block, "other_printed_text", "other_printed_text",
        contact_dict, confidence_map,
    )

    # ----- metadata ブロック -----
    # 橋渡し：primary_lang → lang
    _store_scalar(metadata_block, "primary_lang", "lang", contact_dict, confidence_map)
    _store_scalar(
        metadata_block, "language_composition", "language_composition",
        contact_dict, confidence_map,
    )
    # ai_analysis_notes は §1.7 により Contact 用辞書に一切入れない（raw_json 内のみ）

    # ----- address 組み立て（§6.4 / §11.9.4） -----
    # 4 要素から compose_full_address で組み立てて Contact.address に格納する。
    # 結果が空文字なら contact_dict["address"] には入れない（Contact 側 default="" に委ねる）。
    composed_address = compose_full_address(
        contact_dict.get("postal_code", ""),
        contact_dict.get("region", ""),
        contact_dict.get("city", ""),
        contact_dict.get("rest_of_address", ""),
        contact_dict.get("country", ""),
        contact_dict.get("lang", ""),
    )
    if composed_address:
        contact_dict["address"] = composed_address

    # ----- 派生フィールド補完（§6.5 D カテゴリ） -----
    # org_core_name：OCR が出力していない場合のみ derive で補完
    if not contact_dict.get("org_core_name"):
        derived_core = derive_org_core_name(
            contact_dict.get("organization", ""),
            contact_dict.get("legal_entity_type", ""),
        )
        if derived_core:
            contact_dict["org_core_name"] = derived_core
    # org_domain_name：OCR が出力していない場合のみ derive で補完
    if not contact_dict.get("org_domain_name"):
        derived_domain = derive_org_domain_name(contact_dict.get("email", ""))
        if derived_domain:
            contact_dict["org_domain_name"] = derived_domain

    return contact_dict, confidence_map


def calc_orientation_adjusted_confidence_map(contact_dict, confidence_map, orientation):
    """orientation に応じて confidence_map を補正する（仕様書 v1.6.0 §5.3）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] contact_dict: Contact フィールド辞書（normalize_to_contact_dict の出力）
           confidence_map: Contact フィールド名 → 'high'/'mid'/'low'
           orientation: 採用 raw_json の card_meta.orientation の値
    [出力] 補正後の confidence_map（mid/low のみ含む。high は記録対象外）

    §5.3.1 競合ルール：raw_json 自体は不変。補正は累積で最も低い側に収束させる方針のため、
    本関数を check_name_consistency 補正の後に順次適用すれば自動的に最低値に収束する。
    本関数自体は confidence_map の現在値に対して downgrade を適用（より高くする方向の補正は
    一切行わない）。

    | orientation | 補正 |
    |---|---|
    | normal | 補正なし（confidence_map をそのまま返す） |
    | rotate_90_cw / rotate_90_ccw | high→mid、mid→mid、low→low |
    | rotate_180 / mirror | high→low、mid→low、low→low |

    値が空のフィールドは補正対象外。
    """
    if orientation in ("rotate_90_cw", "rotate_90_ccw"):
        downgrade = _CONFIDENCE_DOWNGRADE_90
    elif orientation in ("rotate_180", "mirror"):
        downgrade = _CONFIDENCE_DOWNGRADE_180
    else:
        # normal / 想定外（既存挙動踏襲）：補正なし、入力をコピーして返す
        return dict(confidence_map)

    result = {}
    for field, value in contact_dict.items():
        if not value:
            continue
        current = confidence_map.get(field, "high")
        adjusted = downgrade.get(current, _FALLBACK_CONFIDENCE)
        if adjusted in ("low", "mid"):
            result[field] = adjusted
    return result
