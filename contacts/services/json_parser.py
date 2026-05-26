"""v1.6.1 の 8 ブロック構造 OCR JSON から Contact 用辞書を生成する（仕様書 OCR 統合版 §5）。

Phase C（道具立て先行）。本モジュールは 8 ブロック構造（card_meta / name / job /
organization / address / contact / uncategorized_card_content / metadata）を入力前提と
する。旧 v1.3.0 の fields/fields_array を読む cards/services/json_normalizer.py とは別物で、
ocr_pipeline への live 接続は Phase C-wire（OCR プロンプト書き換え + v1.6.1 スキーマ作成）で
別途行う。本フェーズではどの経路からも呼ばれず、ユニットテストで品質保証する。

主要関数：
  normalize_to_contact_dict(raw_json, card_index) -> (contact_dict, confidence_map)  純関数
  calc_orientation_adjusted_confidence_map(contact_dict, confidence_map, orientation)  純関数
  extract_sns_entries(raw_json, card_index) -> list[(sns_type, sns_id)]  純関数
  create_sns_records_for_contact(contact, sns_entries)  副作用あり（ContactSns 作成）

防御方針（§5.1）：構造想定外でも例外を投げずベストエフォート（confidence=low に倒す）。
例外を投げる唯一のケースは card_index 範囲外の ValueError。
"""

import logging
import re

from contacts.models import ContactSns
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
    normalize_postal_code_by_country,
    normalize_rest_of_address_by_country,
)

logger = logging.getLogger(__name__)

# 防御的処理の既定値
_FALLBACK_CONFIDENCE = "low"
_VALID_CONFIDENCES = ("high", "mid", "low")

# confidence の大小（_min_confidence 用。high > mid > low）
_CONFIDENCE_ORDER = {"high": 2, "mid": 1, "low": 0}

# orientation 補正テーブル（§5.3）
_DOWNGRADE_90 = {"high": "mid", "mid": "mid", "low": "low"}
_DOWNGRADE_180 = {"high": "low", "mid": "low", "low": "low"}

# enum 系フィールドの規格内集合と受け皿値（粒度 C、§5.1 / §6.5 カテゴリ B）
_NAME_ORDER_VALUES = {"last_first", "first_last", "single", "other"}
_LANG_VALUES = {"ja", "en", "zh", "und"}
_LANGUAGE_COMPOSITION_VALUES = {
    "local_only",
    "english_only",
    "mix_bilingual",
    "other",
}
_LEGAL_ENTITY_TYPE_CODE_VALUES = {
    "CP",
    "LLP",
    "GOV",
    "NPO",
    "REL",
    "EDU",
    "MED",
    "PRO",
    "IND",
    "OTH",
}
_LEGAL_ENTITY_TYPE_POSITION_VALUES = {"Pre", "Post", "Mid", "other"}

# OCR の sns type 表記揺れ吸収（§3.7.5）
_SNS_TYPE_ALIASES = {
    "x": "twitter",  # X → Twitter（旧称統一）
}

# ISO 3166-1 alpha-2 の正式割当コード集合（country の粒度 C 検証用）。"ZZ"（ユーザー割当・
# 不明国の受け皿）も妥当値として含める。リスト外の 2 文字（"XX" 等）は規格外として ZZ に倒す。
_ISO_3166_1_ALPHA2 = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM
    BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX
    CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG
    GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR
    IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV
    LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE
    NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO
    RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF
    TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF
    WS YE YT ZA ZM ZW ZZ
    """.split()
)


# ----------------------------------------------------------------------
# 内部ヘルパー（仕様書未掲載、モジュール内専用）
# ----------------------------------------------------------------------


def _get_card(raw_json, card_index):
    """[性質] 純関数。raw_json['cards'][card_index] を取り出す。

    cards が list でない / card_index が範囲外なら ValueError（§5.1 で許された唯一の例外）。
    card 要素が dict でなければ空 dict を返す（粒度 A の起点）。
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
    return card if isinstance(card, dict) else {}


def _get_block(card, name):
    """[性質] 純関数。card からブロック dict を取り出す。欠落 / 非 dict なら None（粒度 A）。"""
    block = card.get(name)
    return block if isinstance(block, dict) else None


def _extract_value_and_confidence(field):
    """[性質] 純関数。フィールドから (value: str, confidence: str | None) を取り出す（粒度 B）。

    - field が None（キー欠落）：("", None)。情報なしとして confidence_map に記録しない。
    - field が dict 以外：("", "low")。構造想定外として防御的に低信頼で記録（粒度 B）。
    - field が dict で value=null：("", confidence)。confidence 想定外なら ("", "low")。
    - field が dict で value あり：(str(value).strip(), confidence)。confidence 想定外なら "low"。
    """
    if field is None:
        return "", None
    if not isinstance(field, dict):
        return "", _FALLBACK_CONFIDENCE
    raw_value = field.get("value")
    raw_confidence = field.get("confidence")
    confidence = (
        raw_confidence if raw_confidence in _VALID_CONFIDENCES else _FALLBACK_CONFIDENCE
    )
    if raw_value is None:
        return "", confidence
    return str(raw_value).strip(), confidence


def _take(block, key, *, block_missing):
    """[性質] 純関数。ブロックの 1 キーを取り出す。ブロック欠落時は ("", "low")（粒度 A）。"""
    if block_missing:
        return "", _FALLBACK_CONFIDENCE
    return _extract_value_and_confidence(block.get(key))


def _light_normalize(value):
    """[性質] 純関数。カテゴリ A（事実）の軽い整え：全角空白→半角・連続空白 1 つ・前後空白除去。

    大文字小文字・全角英数字→半角の強制変換はしない（§6.5 カテゴリ A）。
    normalize_original_script_for_full_name と同一ロジックだが、氏名以外のカテゴリ A
    フィールド向けに json_parser 内で独立保持する。
    """
    if not value:
        return ""
    s = value.replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _put(contact_dict, confidence_map, field, value, confidence):
    """[性質] 純関数相当（引数の dict を更新）。contact_dict と confidence_map に値を書き込む。

    confidence が low/mid のときのみ confidence_map に記録（high / None は記録しない、§3.6）。
    """
    contact_dict[field] = value
    if confidence in ("low", "mid"):
        confidence_map[field] = confidence


def _min_confidence(a, b):
    """[性質] 純関数。2 つの confidence のうち低い側（より厳しい側）を返す（§5.3.1）。"""
    return a if _CONFIDENCE_ORDER[a] <= _CONFIDENCE_ORDER[b] else b


def _resolve_enum(value, confidence, valid, fallback):
    """[性質] 純関数。enum 値を検証し、範囲外なら受け皿値 + low に倒す（粒度 C）。

    空値は空のまま返す（未指定。Contact 側 choices は blank 許容）。
    """
    if not value:
        return "", confidence
    if value in valid:
        return value, confidence
    return fallback, _FALLBACK_CONFIDENCE


def _resolve_country(value, confidence):
    """[性質] 純関数。country を解決する（§変更点2）。

    null/空 → "ZZ"（confidence は OCR 出力のまま据え置き）。
    ISO 3166-1 alpha-2 の正式コード → 大文字化してそのまま。
    規格外（"XX" 等の未割当 2 文字を含む）→ "ZZ" + low（粒度 C）。Contact.country は常に non-null。
    """
    if not value:
        return "ZZ", confidence if confidence else "high"
    v = value.strip().upper()
    if v in _ISO_3166_1_ALPHA2:
        return v, confidence or "high"
    return "ZZ", _FALLBACK_CONFIDENCE


def _resolve_lang(value, confidence):
    """[性質] 純関数。primary_lang → lang を解決する（§6.5 カテゴリ B）。

    空 → "und"（confidence 据え置き）。規格内 → そのまま。規格外 → "und" + low。
    lang は salutation 計算に必須のため空でも "und" 受け皿に倒す（コード君A 判断）。
    """
    v = (value or "").strip().lower()
    if not v:
        return "und", confidence if confidence else "high"
    if v in _LANG_VALUES:
        return v, confidence or "high"
    return "und", _FALLBACK_CONFIDENCE


def _normalize_sns_type(raw_type):
    """[性質] 純関数。OCR の sns type を正規化（小文字化 + エイリアス解決、§3.7.5）。"""
    if not raw_type:
        return ""
    normalized = raw_type.strip().lower()
    return _SNS_TYPE_ALIASES.get(normalized, normalized)


# ----------------------------------------------------------------------
# 公開関数
# ----------------------------------------------------------------------


def normalize_to_contact_dict(raw_json, card_index):
    """8 ブロック構造の raw_json から Contact フィールド辞書を生成する（§5.1 / §5.2）。

    [性質] 純関数（DB 操作なし・副作用なし。logger.warning は check_name_consistency 由来のみ）
    [入力] raw_json: dict（8 ブロック構造の OCR 出力）、card_index: int
    [出力] tuple (contact_dict, confidence_map)
           - contact_dict: Contact フィールド名 → 文字列値
           - confidence_map: フィールド名 → 'low'/'mid'（high は含めない、§3.6）
    [例外] ValueError（card_index が cards 配列範囲外の場合のみ）

    防御（§5.1）：ブロック欠落（粒度 A）/ フィールド構造異常（粒度 B）/ enum 範囲外（粒度 C）
    のいずれも例外を投げず、空 + low / 受け皿値 + low に倒す。confidence 補正は
    (1) OCR confidence → (2) check_name_consistency → の順に合成し、orientation 補正は
    呼び出し側が calc_orientation_adjusted_confidence_map で別途適用する（§5.3.1）。
    SNS は別テーブルのため本辞書には含めない（extract_sns_entries 参照）。
    """
    card = _get_card(raw_json, card_index)

    name_b = _get_block(card, "name")
    job_b = _get_block(card, "job")
    org_b = _get_block(card, "organization")
    addr_b = _get_block(card, "address")
    contact_b = _get_block(card, "contact")
    uncat_b = _get_block(card, "uncategorized_card_content")
    meta_b = _get_block(card, "metadata")

    contact_dict = {}
    confidence_map = {}

    # ---- name ブロック ----------------------------------------------
    name_missing = name_b is None
    # full_name は original_script を最小限正規化してコピー（§3.3.1 / §3.4）
    os_val, os_conf = _take(name_b, "original_script", block_missing=name_missing)
    _put(
        contact_dict,
        confidence_map,
        "full_name",
        normalize_original_script_for_full_name(os_val),
        os_conf,
    )
    # カテゴリ A（事実）：軽い整えのみ
    for key in ("last_name", "first_name", "other_name_parts", "display_name",
                "phonetic_name", "alias_name"):
        v, c = _take(name_b, key, block_missing=name_missing)
        _put(contact_dict, confidence_map, key, _light_normalize(v), c)
    # salutation_name：OCR 値を軽く整えて格納。空のときは confidence_map に入れない
    # （§1.9。空の補完は Contact.save() の責務で、CFC もそちらが作る）
    sal_v, sal_c = _take(name_b, "salutation_name", block_missing=name_missing)
    sal_v = _light_normalize(sal_v)
    contact_dict["salutation_name"] = sal_v
    if sal_v and sal_c in ("low", "mid"):
        confidence_map["salutation_name"] = sal_c
    # name_order：enum（粒度 C）
    no_v, no_c = _take(name_b, "name_order", block_missing=name_missing)
    no_v, no_c = _resolve_enum(no_v, no_c, _NAME_ORDER_VALUES, "other")
    _put(contact_dict, confidence_map, "name_order", no_v, no_c)

    # ---- job ブロック -----------------------------------------------
    job_missing = job_b is None
    # title / department は normalize_department_title_branch、qualification / catchphrase は対象外
    for key, normalizer in (
        ("title", normalize_department_title_branch),
        ("department", normalize_department_title_branch),
        ("qualification", None),
        ("catchphrase", None),
    ):
        v, c = _take(job_b, key, block_missing=job_missing)
        value = normalizer(v) if normalizer else v
        _put(contact_dict, confidence_map, key, value, c)

    # ---- organization ブロック --------------------------------------
    org_missing = org_b is None
    # org_name_full → organization（名前変換 + 正規化）
    org_v, org_c = _take(org_b, "org_name_full", block_missing=org_missing)
    organization = normalize_organization(org_v)
    _put(contact_dict, confidence_map, "organization", organization, org_c)
    # legal_entity_type（自由文字列、軽い整え）
    let_v, let_c = _take(org_b, "legal_entity_type", block_missing=org_missing)
    let_v = _light_normalize(let_v)
    _put(contact_dict, confidence_map, "legal_entity_type", let_v, let_c)
    # legal_entity_type_code：enum（粒度 C。二重検算は Phase C-追加スコープ）
    lec_v, lec_c = _take(org_b, "legal_entity_type_code", block_missing=org_missing)
    lec_v, lec_c = _resolve_enum(
        lec_v, lec_c, _LEGAL_ENTITY_TYPE_CODE_VALUES, "OTH"
    )
    _put(contact_dict, confidence_map, "legal_entity_type_code", lec_v, lec_c)
    # legal_entity_type_position：enum（粒度 C）
    lep_v, lep_c = _take(org_b, "legal_entity_type_position", block_missing=org_missing)
    lep_v, lep_c = _resolve_enum(
        lep_v, lep_c, _LEGAL_ENTITY_TYPE_POSITION_VALUES, "other"
    )
    _put(contact_dict, confidence_map, "legal_entity_type_position", lep_v, lep_c)
    # branch（normalize_department_title_branch）
    br_v, br_c = _take(org_b, "branch", block_missing=org_missing)
    _put(contact_dict, confidence_map, "branch", normalize_department_title_branch(br_v), br_c)
    # website（対象外、§3.3.2 ストレート流し）
    web_v, web_c = _take(org_b, "website", block_missing=org_missing)
    _put(contact_dict, confidence_map, "website", web_v, web_c)
    # org_core_name：導出（OCR 値でなくサーバー導出、§6.5 カテゴリ D）。confidence は記録しない
    contact_dict["org_core_name"] = derive_org_core_name(organization, let_v)

    # ---- contact ブロック（email / 電話系 5 / sns は別） -------------
    contact_missing = contact_b is None
    email_v, email_c = _take(contact_b, "email", block_missing=contact_missing)
    email = normalize_email(email_v)
    _put(contact_dict, confidence_map, "email", email, email_c)
    for key in ("mobile_phone", "personal_phone", "personal_fax", "org_phone", "org_fax"):
        v, c = _take(contact_b, key, block_missing=contact_missing)
        _put(contact_dict, confidence_map, key, normalize_phone_value(v), c)
    # org_domain_name：導出（email から、§6.5 カテゴリ D）。confidence は記録しない
    contact_dict["org_domain_name"] = derive_org_domain_name(email)

    # ---- metadata ブロック ------------------------------------------
    meta_missing = meta_b is None
    pl_v, pl_c = _take(meta_b, "primary_lang", block_missing=meta_missing)
    lang, lang_c = _resolve_lang(pl_v, pl_c)
    contact_dict["lang"] = lang
    if lang_c in ("low", "mid"):
        confidence_map["lang"] = lang_c
    lc_v, lc_c = _take(meta_b, "language_composition", block_missing=meta_missing)
    lc_v, lc_c = _resolve_enum(lc_v, lc_c, _LANGUAGE_COMPOSITION_VALUES, "other")
    _put(contact_dict, confidence_map, "language_composition", lc_v, lc_c)
    # ai_analysis_notes は Contact 非マッピング（raw_json 内のみ、§5.2）

    # ---- address ブロック（4 要素 → compose_full_address で組み立て） ----
    addr_missing = addr_b is None
    pc_v, pc_c = _take(addr_b, "postal_code", block_missing=addr_missing)
    for key in ("region", "city"):
        v, c = _take(addr_b, key, block_missing=addr_missing)
        _put(contact_dict, confidence_map, key, _light_normalize(v), c)
    # country を postal_code / rest_of_address の国別正規化より先に解決する（Phase D2）
    country_v, country_c = _take(addr_b, "country", block_missing=addr_missing)
    country, country_c = _resolve_country(country_v, country_c)
    contact_dict["country"] = country
    if country_c in ("low", "mid"):
        confidence_map["country"] = country_c
    # postal_code は country 別に正規化（JP/US=数字のみ / _default=英字保持、Phase D2）
    _put(
        contact_dict,
        confidence_map,
        "postal_code",
        normalize_postal_code_by_country(pc_v, country),
        pc_c,
    )
    # rest_of_address は country 別に正規化（JP=全スペース除去 / US・_default=スペース保持、Phase D2）
    roa_v, roa_c = _take(addr_b, "rest_of_address", block_missing=addr_missing)
    _put(
        contact_dict,
        confidence_map,
        "rest_of_address",
        normalize_rest_of_address_by_country(roa_v, country),
        roa_c,
    )
    # address：導出（4 要素 + country + lang から、§6.4）。full_address(OCR) は使わない
    contact_dict["address"] = compose_full_address(
        contact_dict["postal_code"],
        contact_dict["region"],
        contact_dict["city"],
        contact_dict["rest_of_address"],
        contact_dict["country"],
        contact_dict["lang"],
    )

    # ---- uncategorized_card_content ブロック（事実保存、正規化しない） ----
    uncat_missing = uncat_b is None
    for key in ("handwritten_text", "other_printed_text"):
        v, c = _take(uncat_b, key, block_missing=uncat_missing)
        _put(contact_dict, confidence_map, key, v, c)

    # ---- name ブロック整合性チェック（§2.4.1、step2 合成） ------------
    # OCR が出した name 内容を裏で検算し confidence を下げる方向に補正。
    # primary_lang は解決後の lang を注入。salutation_name は OCR 出力にのみ適用。
    if name_b is not None:
        name_block = {
            "original_script": name_b.get("original_script"),
            "last_name": name_b.get("last_name"),
            "first_name": name_b.get("first_name"),
            "other_name_parts": name_b.get("other_name_parts"),
            "name_order": name_b.get("name_order"),
            "salutation_name": name_b.get("salutation_name"),
            "primary_lang": contact_dict.get("lang"),
        }
        for field, corrected in check_name_consistency(name_block).items():
            existing = confidence_map.get(field, "high")
            merged = _min_confidence(existing, corrected)
            if merged in ("low", "mid"):
                confidence_map[field] = merged

    return contact_dict, confidence_map


def calc_orientation_adjusted_confidence_map(contact_dict, confidence_map, orientation):
    """orientation に応じて confidence_map を補正する（§5.3 / §5.3.1、step3 合成）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] contact_dict / confidence_map（normalize_to_contact_dict の出力）、orientation: str
    [出力] 補正後の confidence_map（low/mid のみ含む）

    normal 以外で OCR の高信頼を下方補正する。既存値（check_name_consistency 後）と
    orientation 補正後を _min_confidence で比較し、より低い側を最終値とする（§5.3.1）。
    値が空のフィールドは補正対象外。raw_json は変更しない。
    """
    if orientation in ("rotate_90_cw", "rotate_90_ccw"):
        downgrade = _DOWNGRADE_90
    elif orientation in ("rotate_180", "mirror"):
        downgrade = _DOWNGRADE_180
    else:
        return dict(confidence_map)

    result = dict(confidence_map)
    for field, value in contact_dict.items():
        if not value:
            continue
        current = confidence_map.get(field, "high")
        merged = _min_confidence(current, downgrade[current])
        if merged in ("low", "mid"):
            result[field] = merged
    return result


def extract_sns_entries(raw_json, card_index):
    """contact.sns 配列から正規化済みの (sns_type, sns_id) リストを取り出す（§3.7.5）。

    [性質] 純関数（DB 操作なし。logger.warning は出す）
    [入力] raw_json: dict（8 ブロック構造）、card_index: int
    [出力] list[tuple[str, str]]（sns_type は choices 内・小文字統一、sns_id は非空）
    [例外] ValueError（card_index 範囲外）

    type を正規化（X→twitter 等）し、choices 外はサイレント無視（warning ログ）、
    id 空も無視する。レコード作成（get_or_create）は create_sns_records_for_contact が担う。
    """
    card = _get_card(raw_json, card_index)
    contact_b = card.get("contact")
    if not isinstance(contact_b, dict):
        return []
    sns_field = contact_b.get("sns")
    if not isinstance(sns_field, dict):
        return []
    sns_list = sns_field.get("value")
    if not isinstance(sns_list, list):
        return []

    valid_types = set(ContactSns.SnsType.values)
    entries = []
    for entry in sns_list:
        if not isinstance(entry, dict):
            continue
        sns_type = _normalize_sns_type(entry.get("type", ""))
        raw_id = entry.get("id", "")
        sns_id = str(raw_id).strip() if raw_id is not None else ""
        if sns_type not in valid_types:
            logger.warning(
                "json_parser: unsupported sns type ignored (card_index=%s, type=%r)",
                card_index,
                entry.get("type"),
            )
            continue
        if not sns_id:
            continue
        entries.append((sns_type, sns_id))
    return entries


def create_sns_records_for_contact(contact, sns_entries):
    """ContactSns レコードを作成する（§3.7.5）。

    [性質] 副作用あり（DB 書込：ContactSns）
    [入力] contact: Contact（保存済み）、sns_entries: list[tuple[sns_type, sns_id]]
    [出力] None

    UniqueConstraint(contact, sns_type, sns_id) 違反を get_or_create で抑止する。
    sns_id は前後空白を除去して格納する（Form 経路の CharField strip と挙動を揃える、§1.7）。
    """
    for sns_type, sns_id in sns_entries:
        ContactSns.objects.get_or_create(
            contact=contact, sns_type=sns_type, sns_id=(sns_id or "").strip()
        )
