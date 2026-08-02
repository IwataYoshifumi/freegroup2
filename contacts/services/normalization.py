"""Contact 正規化基盤の純関数群（仕様書 v1.6.1 本編 §11.9 / OCR 統合版 §1.5・§2.4）。

本モジュールの公開関数はすべて DB を一切触らない純関数（同じ入力で同じ出力・副作用なし）。
唯一の例外は ``check_name_consistency`` の補正理由ログ（``logger.warning`` のみ、§2.4.1）。

3 経路（OCR / 手動 Form / AJAX）が同じ純関数群を共有し、入力経路によらず Contact
フィールドに格納される値が一致することを保証する（§11.9.2）。経路への組み込みは
Phase C 以降で行う。本モジュール自体はどの経路からもまだ呼ばれていない。

循環 import 回避のため Contact モデルを import しない。``compute_salutation_name`` は
Contact インスタンスを引数で受けるが、属性アクセス（duck typing）のみ行う（§2.2）。
"""

import logging
import re

import phonenumbers
from django.core.exceptions import ValidationError

from contacts.constants import COUNTRY_CALLING_CODES

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 内部ヘルパー（仕様書未掲載、モジュール内専用）
# ----------------------------------------------------------------------

# 全角英大文字 Ａ-Ｚ / 全角英小文字 ａ-ｚ / 全角数字 ０-９ → 半角（オフセット 0xFEE0）
_ZEN_DIGIT_RANGE = (0xFF10, 0xFF19)
_ZEN_UPPER_RANGE = (0xFF21, 0xFF3A)
_ZEN_LOWER_RANGE = (0xFF41, 0xFF5A)

# 漢数字 → 半角数字（桁単位のみ。位取り「十百千万」は変換しない＝過剰辞書化回避）
_KANJI_DIGITS = {
    "〇": "0",
    "零": "0",
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}

# 組織名の法人格略記号 → 正式表記（位置は変えない＝前株・後株は別会社扱い、§11.9.5.1）
_ORG_ABBREV_MAP = {
    "㈱": "株式会社",
    "(株)": "株式会社",
    "（株）": "株式会社",
    "㈲": "有限会社",
    "(有)": "有限会社",
    "（有）": "有限会社",
    "㈳": "社団法人",
    "㈶": "財団法人",
}

# 住所のハイフン類（全角ハイフン・ダッシュ・マイナス）→ 半角ハイフンに統一。
# 長音記号「ー」は地名で多用されるため意図的に含めない（コード君A 判断、§1.1.2）。
_ADDRESS_DASH_CHARS = "‐‑‒–—―−－"

# confidence の 1 段下げ表（high → mid → low、low は据え置き）
_DOWNGRADE = {"high": "mid", "mid": "low", "low": "low"}

# 汎用フリーメール・プロバイダドメインの無視リスト（マスター、§11.9.6）。
# org_domain_name の値自体は名刺どおり残す（空にしない）。重複検出側がこの判定を
# 会社一致判定の除外に使う。
_GENERIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.co.jp",
        "yahoo.com",
        "ymail.com",
        "hotmail.com",
        "hotmail.co.jp",
        "outlook.com",
        "outlook.jp",
        "live.com",
        "live.jp",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "ezweb.ne.jp",
        "docomo.ne.jp",
        "softbank.ne.jp",
        "i.softbank.jp",
        "au.com",
        "nifty.com",
        "biglobe.ne.jp",
        "ybb.ne.jp",
        "ocn.ne.jp",
        "so-net.ne.jp",
        "infoseek.jp",
        "excite.co.jp",
    }
)


def _zen_digit_to_han(s):
    """[性質] 純関数。全角数字 ０-９ を半角 0-9 に変換する。"""
    out = []
    for ch in s:
        code = ord(ch)
        if _ZEN_DIGIT_RANGE[0] <= code <= _ZEN_DIGIT_RANGE[1]:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _zen_alnum_to_han(s):
    """[性質] 純関数。全角英数字（A-Z / a-z / 0-9）のみ半角に変換する（記号は変えない）。"""
    out = []
    for ch in s:
        code = ord(ch)
        if (
            _ZEN_DIGIT_RANGE[0] <= code <= _ZEN_DIGIT_RANGE[1]
            or _ZEN_UPPER_RANGE[0] <= code <= _ZEN_UPPER_RANGE[1]
            or _ZEN_LOWER_RANGE[0] <= code <= _ZEN_LOWER_RANGE[1]
        ):
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _kanji_digit_to_han(s):
    """[性質] 純関数。桁単位の漢数字（〇〜九）を半角数字に変換する（位取りは変換しない）。"""
    return "".join(_KANJI_DIGITS.get(ch, ch) for ch in s)


def _name_block_value(name_block, key):
    """[性質] 純関数。name ブロックの 1 キーの value を取り出す（{value,confidence} / 素値 両対応）。"""
    entry = name_block.get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _name_block_confidence(name_block, key):
    """[性質] 純関数。name ブロックの 1 キーの confidence を取り出す（欠落時は 'high'）。"""
    entry = name_block.get(key)
    if isinstance(entry, dict):
        return entry.get("confidence") or "high"
    return "high"


def _record_name_violation(corrections, name_block, field, reason):
    """[性質] 副作用あり（corrections dict 更新 + logger.warning）。

    name ブロック整合性違反を記録し confidence を 1 段下げる（high→mid→low、累積可）。
    補正理由はサーバーログのみ（DB 保存しない、§2.4.1）。
    """
    base = corrections.get(field)
    if base is None:
        base = _name_block_confidence(name_block, field)
    corrections[field] = _DOWNGRADE.get(base, "low")
    logger.warning(
        "check_name_consistency: '%s' を %s に補正（理由: %s）",
        field,
        corrections[field],
        reason,
    )


# ----------------------------------------------------------------------
# §1.1.1 正規化純関数（7 個）
# ----------------------------------------------------------------------


def normalize_full_name(raw):
    """full_name を正規化する（仕様書 §11.9.5.1）。

    [性質] 副作用あり（空文字検知で ValidationError を raise）。DB 操作はなし
    [入力] raw: str（OCR / 手動入力の氏名フルネーム）
    [出力] str（正規化後の full_name）
    [例外] ValidationError（raw が None・空文字・正規化後に空になる場合）

    全角空白→半角 / 半角空白除去 / 全角英数字→半角 / 前後空白除去。仕様書のロジックを
    変更せず本文化したもの（空白除去あり）。original_script からのコピー経路は別関数
    ``normalize_original_script_for_full_name``（空白を残す最小限正規化）を使う。
    """
    if raw is None:
        raise ValidationError("full_name は必須です（空の値は許可されません）。")
    s = raw.replace("　", " ")  # 全角空白→半角
    s = s.replace(" ", "")  # 半角空白除去
    s = _zen_alnum_to_han(s)  # 全角英数字→半角
    s = s.strip()  # 前後空白除去
    if not s:
        raise ValidationError("full_name は必須です（空の値は許可されません）。")
    return s


def normalize_organization(raw):
    """organization（旧 company）を正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（会社名）
    [出力] str（正規化後の organization。空入力は空文字を返す）

    株式会社系統一表記（㈱・(株) → 株式会社等）/ 全角半角空白除去 / 全角英数字→半角 /
    前後空白除去。法人格の前後位置差は吸収しない（前株・後株は別会社扱い）。
    """
    if not raw:
        return ""
    s = raw
    for abbrev, full in _ORG_ABBREV_MAP.items():
        s = s.replace(abbrev, full)
    s = s.replace("　", "").replace(" ", "")  # 全角半角空白除去（全削除）
    s = _zen_alnum_to_han(s)
    return s.strip()


def normalize_phone_value(raw):
    """電話番号 1 件を正規化する（仕様書 §11.9.5.1、v1.6.1 単一値処理）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（mobile_phone / personal_phone / personal_fax / org_phone / org_fax）
    [出力] str（数字のみ。空入力は空文字を返す）

    数字とハイフンのみ抽出 / ハイフン除去 / 全角数字→半角 / 漢数字→半角。結果は数字のみ。
    v1.6.1 で電話系 5 フィールドが CharField(50) 単一値に巻き戻されたため、配列ループは
    入れない（単一値処理）。country / region との国番号二重検算は Phase C 以降の別責務。
    """
    if not raw:
        return ""
    s = _zen_digit_to_han(raw)  # 全角数字→半角
    s = _kanji_digit_to_han(s)  # 漢数字→半角
    return re.sub(r"\D", "", s)  # 数字以外（ハイフン含む）を除去


def normalize_email(raw):
    """email を正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（メールアドレス）
    [出力] str（小文字化・前後空白除去後。空入力は空文字を返す）
    """
    if not raw:
        return ""
    return raw.strip().lower()


def normalize_rest_of_address(raw):
    """rest_of_address（番地以降の住所）を正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（番地以降の住所文字列）
    [出力] str（正規化後。空入力は空文字を返す）

    全角半角空白除去 / 全角英数字→半角 / 漢数字→半角 / 丁目番地号を「-」に /
    ハイフン統一 / 前後空白除去。長音記号「ー」は地名で多用されるため統一対象外。
    """
    if not raw:
        return ""
    s = raw.replace("　", "").replace(" ", "")  # 全角半角空白除去
    s = _zen_alnum_to_han(s)  # 全角英数字→半角
    s = _kanji_digit_to_han(s)  # 漢数字→半角
    s = re.sub(f"[{_ADDRESS_DASH_CHARS}]", "-", s)  # ハイフン統一
    s = re.sub(r"丁目|番地|番|号", "-", s)  # 丁目番地号を - に
    s = re.sub(r"-+", "-", s)  # 連続ハイフンを 1 つに
    return s.strip("-").strip()  # 前後のハイフン・空白除去


def normalize_postal_code(raw):
    """postal_code を正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（郵便番号）
    [出力] str（数字のみ。空入力は空文字を返す）

    数字のみ（ハイフン除去）/ 全角数字→半角。
    """
    if not raw:
        return ""
    s = _zen_digit_to_han(raw)
    return re.sub(r"\D", "", s)


def normalize_department_title_branch(raw):
    """department / title / branch を共通正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（部署名 / 役職名 / 支店名のいずれか）
    [出力] str（正規化後。空入力は空文字を返す）

    全角半角空白除去 / 全角英数字→半角 / 前後空白除去。
    """
    if not raw:
        return ""
    s = raw.replace("　", "").replace(" ", "")  # 全角半角空白除去
    s = _zen_alnum_to_han(s)
    return s.strip()


# ----------------------------------------------------------------------
# §1.1.2 派生・組み立て純関数（5 個）
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Phase D2：postal_code の国別表示整形（POSTAL_FORMATTERS）
# ----------------------------------------------------------------------


def format_jp_postal(postal):
    """[性質] 純関数。日本の郵便番号を表示整形する（7 桁ちょうどのみハイフン挿入）。

    "1234567" → "123-4567"。7 桁の数字ちょうどのときだけ 3-4 でハイフン挿入し、
    それ以外（桁数違い・ハイフン既存・英字混じり等）はそのまま返す。空入力は空文字。
    """
    s = postal or ""
    if len(s) == 7 and s.isdigit():
        return f"{s[:3]}-{s[3:]}"
    return s


def format_us_postal(postal):
    """[性質] 純関数。米国 ZIP を表示整形する（9 桁ちょうどのみ ZIP+4 ハイフン挿入）。

    "94103" → "94103"（5 桁はそのまま）。"941031234" → "94103-1234"（9 桁のみ 5-4 で
    ハイフン挿入）。それ以外（6/7/8/10 桁・英字混じり等）はそのまま。空入力は空文字。
    """
    s = postal or ""
    if len(s) == 9 and s.isdigit():
        return f"{s[:5]}-{s[5:]}"
    return s


# country（ISO 3166-1 alpha-2 大文字）→ postal 整形関数。未対応国は _default（passthrough）。
POSTAL_FORMATTERS = {
    "JP": format_jp_postal,
    "US": format_us_postal,
    "_default": lambda p: p or "",
}


def format_postal_by_country(postal, country):
    """[性質] 純関数。country に対応する整形関数で postal_code を表示整形する（Phase D2）。

    [入力] postal: str（郵便番号。通常は normalize_postal_code 済みの数字列）
           country: str（ISO 3166-1 alpha-2。空・未対応は _default=passthrough）
    [出力] str（整形後の表示用文字列。空入力は空文字）

    話1（compose_full_address の US/_default 経路）と話2（format_postal テンプレートタグ）の
    両方から共有する。JP の compose は本関数を通さず raw を埋め込む（案1、ADDRESS_TEMPLATES
    の format_postal=False で制御）。一方 JP の画面単体表示はタグ経由で本関数を通す。
    """
    key = (country or "").strip().upper()
    formatter = POSTAL_FORMATTERS.get(key, POSTAL_FORMATTERS["_default"])
    return formatter(postal or "")


def normalize_postal_code_intl(raw):
    """postal_code を非日本向けに正規化する（英数混在 postal を破壊しない、Phase D2）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（郵便番号）
    [出力] str（全角英数字→半角・全半角スペース除去・前後空白除去。英字・ハイフンは保持）

    GB の "SW1A 1AA"・CA の "K1A 0B1"・NL の "1234 AB" 等の英数混在 postal を保持する
    （数字抽出はしない）。ハイフンは情報欠落を避けるため除去せず維持する（コード君A 判断）。
    JP/US は従来どおり ``normalize_postal_code``（数字のみ）を使う。
    """
    if not raw:
        return ""
    s = _zen_alnum_to_han(raw)  # 全角英数字→半角（英字も半角化）
    s = s.replace("　", "").replace(" ", "")  # 全半角スペース除去
    return s.strip()


# country（大文字）→ postal_code 正規化関数。JP/US は数字のみ（normalize_postal_code）、
# 未対応国（GB / IE / NL / CA 等を含む）は _default＝英字保持（normalize_postal_code_intl）。
POSTAL_NORMALIZERS = {
    "JP": normalize_postal_code,
    "US": normalize_postal_code,
    "_default": normalize_postal_code_intl,
}


def normalize_postal_code_by_country(raw, country):
    """country 別に postal_code を正規化する（POSTAL_NORMALIZERS 準拠、Phase D2）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str、country: str（ISO 3166-1 alpha-2。空・未対応は _default）
    [出力] str（JP/US＝数字のみ / _default＝英字保持）

    仕様書 §11.9.5.1 の「数字のみ」は日本特化の記述で、英数混在 postal を持つ国（GB の
    SW1A1AA 等）を破壊するため、country で正規化を分岐する。
    """
    key = (country or "").strip().upper()
    normalizer = POSTAL_NORMALIZERS.get(key, POSTAL_NORMALIZERS["_default"])
    return normalizer(raw)


# ----------------------------------------------------------------------
# Phase D2：住所組み立ての country 別テンプレート（ADDRESS_TEMPLATES）
# ----------------------------------------------------------------------

# country（大文字）→ 組み立て方針。format は説明用の参考表記（実組み立ては compose 内の
# 専用ロジック）。rest_normalizer は rest_of_address の正規化種別、format_postal は compose
# 内で postal を整形するか（案1：JP は False＝raw 維持／US・_default は True）。
ADDRESS_TEMPLATES = {
    "JP": {
        "format": "〒{postal} {region}{city}{rest}",
        "rest_normalizer": "strip_all_spaces",
        "format_postal": False,
    },
    "US": {
        "format": "{rest}, {city}, {region} {postal}, {country}",
        "rest_normalizer": "preserve_spaces",
        "format_postal": True,
    },
    "_default": {
        "format": "{rest}, {city}, {region} {postal}, {country}",
        "rest_normalizer": "preserve_spaces",
        "format_postal": True,
    },
}


def normalize_rest_of_address_intl(raw):
    """rest_of_address を非日本住所向けに軽く正規化する（スペース保持版、Phase D2）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（番地以降の住所文字列）
    [出力] str（正規化後。空入力は空文字を返す）

    全角空白→半角 / 全角英数字→半角 / 連続スペース 1 つ / 前後空白除去のみ。日本住所向けの
    全スペース除去・漢数字変換・丁目番地号変換・ハイフン統一は行わない（"123 Market St" の
    語間スペースを保持する）。JP は従来どおり ``normalize_rest_of_address`` を使う。
    """
    if not raw:
        return ""
    s = raw.replace("　", " ")  # 全角空白→半角
    s = _zen_alnum_to_han(s)  # 全角英数字→半角
    s = re.sub(r"\s+", " ", s)  # 連続スペースを 1 つに
    return s.strip()


def normalize_rest_of_address_by_country(raw, country):
    """country 別に rest_of_address を正規化する（ADDRESS_TEMPLATES の rest_normalizer 準拠、Phase D2）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str、country: str（ISO 3166-1 alpha-2。空・未対応は _default）
    [出力] str（JP＝全スペース除去版 / US・_default＝スペース保持版）
    """
    key = (country or "").strip().upper()
    template = ADDRESS_TEMPLATES.get(key, ADDRESS_TEMPLATES["_default"])
    if template["rest_normalizer"] == "strip_all_spaces":
        return normalize_rest_of_address(raw)
    return normalize_rest_of_address_intl(raw)


def normalize_field(field_name, value, contact=None):
    """フィールド名に対応する正規化を適用して返す（3 経路共有・AJAX 経路用、Phase E §6）。

    [性質] 副作用あり（full_name 等で ValidationError を raise しうる）。DB 操作なし
    [入力] field_name: str（Contact のフィールド名）
           value: 正規化前の値
           contact: Contact インスタンス（postal_code / rest_of_address の country 別正規化に
                    必要。country 属性のみ duck typing で参照、Contact モデルは import しない）
    [出力] 正規化後の値
    [例外] ValidationError（normalize_full_name が空入力で raise する等、値バリデーション失敗）

    国別正規化が必要なフィールド（postal_code / rest_of_address）は contact.country を引いて
    country 別ディスパッチャを呼ぶ。正規化関数が割り当てられていないフィールド（カテゴリ A：
    last_name / first_name / other_name_parts / display_name / salutation_name / phonetic_name /
    alias_name 等）は、手動 Form 経路（ModelForm CharField の strip）と挙動を揃えて前後空白除去のみ
    行う（str 以外はそのまま返す、Phase E §6.3 判断）。Form 側の _FIELD_NORMALIZERS とは独立だが、
    共有する純関数（normalize_full_name 等）が同一のため結果は一致する。
    """
    country = getattr(contact, "country", "") if contact is not None else ""
    if field_name == "full_name":
        return normalize_full_name(value)
    if field_name == "organization":
        return normalize_organization(value)
    if field_name in (
        "mobile_phone",
        "personal_phone",
        "personal_fax",
        "org_phone",
        "org_fax",
    ):
        return normalize_phone_value(value)
    if field_name == "email":
        return normalize_email(value)
    if field_name in ("department", "title", "branch"):
        return normalize_department_title_branch(value)
    if field_name == "postal_code":
        return normalize_postal_code_by_country(value, country)
    if field_name == "rest_of_address":
        return normalize_rest_of_address_by_country(value, country)
    # カテゴリ A / その他：手動 Form 経路（CharField strip）に合わせ前後 strip のみ
    if isinstance(value, str):
        return value.strip()
    return value


def _compose_jp_address(postal_code, region, city, rest_of_address):
    """[性質] 純関数。日本式住所を組み立てる（Phase D2 案1：postal は整形せず raw 埋め込み）。

    「〒{postal} {region}{city}{rest}」。postal 空なら 〒 部を省略、本体空なら省略、両者を
    半角スペース 1 つで連結。Phase D 以前の挙動を 1 文字も変えない（既存 JP テスト準拠）。
    """
    postal = (postal_code or "").strip()
    region = (region or "").strip()
    city = (city or "").strip()
    rest = (rest_of_address or "").strip()

    address_body = f"{region}{city}{rest}"
    parts = []
    if postal:
        parts.append(f"〒{postal}")  # 案1：JP は postal を整形しない（raw）
    if address_body:
        parts.append(address_body)
    return " ".join(parts)


def _compose_intl_address(postal_code, region, city, rest_of_address, country):
    """[性質] 純関数。英語式住所を組み立てる（US / _default、Phase D2）。

    「{rest}, {city}, {region} {postal}, {country}」。postal は ``format_postal_by_country``
    で国別整形（US は ZIP+4、その他は passthrough）。空要素は省略し、カンマ重複・前後の
    余分な区切りを作らない。region と postal は半角スペースで連結する。
    """
    key = (country or "").strip().upper()
    postal = format_postal_by_country((postal_code or "").strip(), key)
    region = (region or "").strip()
    city = (city or "").strip()
    rest = (rest_of_address or "").strip()
    country_out = (country or "").strip()

    region_postal = " ".join(p for p in (region, postal) if p)
    # locality（番地・市区・州+郵便）が全て空なら country 単独は出さず空文字を返す
    # （JP 経路の全空→"" と挙動を揃える）。
    locality = [s for s in (rest, city, region_postal) if s]
    if not locality:
        return ""
    if country_out:
        locality.append(country_out)
    return ", ".join(locality)


def compose_full_address(postal_code, region, city, rest_of_address, country, lang):
    """住所 6 要素から full_address を組み立てる（仕様書 §11.9.4 / Phase D2 country 別テンプレート化）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] postal_code / region / city / rest_of_address / country / lang: str
    [出力] str（組み立て後の full_address。Contact.address に格納する完成形）

    country（ISO 3166-1 alpha-2、大文字化して判定）で ADDRESS_TEMPLATES を引く：
      - JP：日本式「〒{postal} {region}{city}{rest}」。postal は整形せず raw 埋め込み（案1）。
            Phase D 以前の挙動を完全維持（既存 JP テスト無改変で通る）。
      - US / _default（"" / "ZZ" / 未対応国を含む）：英語式
            「{rest}, {city}, {region} {postal}, {country}」。US は postal を ZIP+4 整形、
            _default は postal passthrough。空要素は省略。
    lang 引数は後方互換のため残すが組み立てには用いない（country で分岐、Phase D2 確定）。
    """
    key = (country or "").strip().upper()
    if key == "JP":
        return _compose_jp_address(postal_code, region, city, rest_of_address)
    return _compose_intl_address(postal_code, region, city, rest_of_address, country)


def derive_org_core_name(org_name_full, legal_entity_type):
    """法人格を除いた会社名コアを導出する（仕様書 §11.9.5 カテゴリ D）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] org_name_full: str（法人格を含む会社名フル）
           legal_entity_type: str（法人格の表記。例「株式会社」）
    [出力] str（法人格を除いた会社名コア。空入力は空文字を返す）

    legal_entity_type が org_name_full に含まれていれば除去する。含まれない場合はそのまま
    （前後空白除去のみ）。位置（前株・後株）は問わず該当文字列を除去する。
    """
    if not org_name_full:
        return ""
    core = org_name_full
    if legal_entity_type:
        core = core.replace(legal_entity_type, "")
    return core.strip()


def derive_org_domain_name(email):
    """email のドメイン部分を抽出する（仕様書 §11.9.5 カテゴリ D / §11.9.6）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] email: str（メールアドレス）
    [出力] str（@ 以降のドメイン部分を小文字化。@ がない / 空入力は空文字を返す）

    汎用フリーメールドメイン（gmail.com 等）でも名刺どおりの値を返す（空にしない＝個人
    事業主等の事実情報を失わない、§11.9.6）。汎用判定は ``is_generic_email_domain`` で別途行う。
    """
    if not email:
        return ""
    email = email.strip()
    if "@" not in email:
        return ""
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain


def check_name_consistency(name_block):
    """name ブロックの整合性を検算し confidence 補正対象を返す（仕様書 §2.4.1）。

    [性質] 副作用あり（補正理由を logger.warning で出力。DB 操作なし・戻り値は純粋）
    [入力] name_block: dict（OCR の name ブロック。各キーは {value, confidence} 形式または
           素値。original_script / last_name / first_name / other_name_parts / name_order /
           salutation_name / primary_lang を参照する。キー欠落時は当該チェックをスキップ）
    [出力] dict[str, str]（補正対象フィールド名 → 補正後 confidence。補正なしなら空 dict）

    4 種類のチェック（文字カバー率 / name_order と構成要素 / primary_lang と name_order /
    salutation_name と姓）を行い、違反フィールドの confidence を下げる方向のみ補正する
    （high→mid→low、累積可）。補正対象は last_name / first_name / other_name_parts /
    name_order / salutation_name の 5 フィールド。

    本チェックは第 1 段（OCR 経路）の salutation_name にのみ適用する想定。第 2 段
    （Contact.save() の compute_salutation_name 生成値）には適用しない（§2.4.1）。
    呼び出しタイミングは Phase C で json_parser に組み込む。
    """
    corrections = {}

    original_script = _name_block_value(name_block, "original_script") or ""
    last_name = _name_block_value(name_block, "last_name") or ""
    first_name = _name_block_value(name_block, "first_name") or ""
    other_name_parts = _name_block_value(name_block, "other_name_parts") or ""
    name_order = _name_block_value(name_block, "name_order") or ""
    salutation_name = _name_block_value(name_block, "salutation_name") or ""
    primary_lang = _name_block_value(name_block, "primary_lang") or ""

    # チェック 1：氏名構成要素の文字カバー率
    # original_script の文字が構成要素（last+first+other）でカバーされているか。
    # 構成要素が 1 つでも値を持つ前提でのみ判定（全要素空の単一名カードは誤検知回避）。
    if original_script and (last_name or first_name or other_name_parts):
        script_chars = set(re.sub(r"\s", "", original_script))
        component_chars = set(
            re.sub(r"\s", "", last_name + first_name + other_name_parts)
        )
        uncovered = script_chars - component_chars
        if uncovered:
            for field in ("last_name", "first_name", "other_name_parts"):
                _record_name_violation(
                    corrections,
                    name_block,
                    field,
                    f"original_script にカバーされない文字 {sorted(uncovered)} がある",
                )

    # チェック 2：name_order と構成要素の整合性
    if name_order == "single" and last_name and first_name:
        _record_name_violation(
            corrections,
            name_block,
            "name_order",
            "name_order=single だが last_name と first_name の両方に値がある",
        )
    elif name_order == "last_first" and not last_name:
        _record_name_violation(
            corrections,
            name_block,
            "name_order",
            "name_order=last_first だが last_name が空",
        )
    elif name_order == "first_last" and not first_name:
        _record_name_violation(
            corrections,
            name_block,
            "name_order",
            "name_order=first_last だが first_name が空",
        )

    # チェック 3：primary_lang と name_order の整合性
    # 東アジア圏（ja / ko / zh）は姓先が自然なため first_last は不整合とみなす。
    if primary_lang in ("ja", "ko", "zh") and name_order == "first_last":
        _record_name_violation(
            corrections,
            name_block,
            "name_order",
            f"primary_lang={primary_lang} だが name_order=first_last",
        )

    # チェック 4：salutation_name と姓の整合性（primary_lang=ja の「{姓} 様」規約）
    if primary_lang == "ja" and salutation_name and last_name:
        if last_name not in salutation_name:
            _record_name_violation(
                corrections,
                name_block,
                "salutation_name",
                f"primary_lang=ja だが salutation_name に last_name '{last_name}' が含まれない",
            )

    return corrections


def compute_full_name(last_name, first_name, other_name_parts, name_order):
    """姓・名・ミドル・name_order から full_name（氏名）を組み立てる。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] last_name / first_name / other_name_parts: str（原本フィールド）
           name_order: str（Contact.NameOrder の値。last_first / first_last / single 等）
    [出力] str（組み立てた氏名）または None（自動組み立て対象外の name_order）

    クライアント JS（static/js/app.js の js-name-full 補助組み立て）と同じ並びにする：
      - last_first ：「{姓} {名}」（ミドルは含めない）
      - first_last ：「{名} {ミドル} {姓}」
      - single     ：姓（無ければ名）
      - other / 未選択 / 不明：自動組み立てしない → None を返す（呼び出し側は full_name を据え置く）
    各要素は strip し、空要素は半角スペース結合から除外する（JS の filter(Boolean) と同じ）。
    """
    last = (last_name or "").strip()
    first = (first_name or "").strip()
    other = (other_name_parts or "").strip()
    order = (name_order or "").strip()
    if order == "last_first":
        return " ".join(p for p in (last, first) if p)
    if order == "first_last":
        return " ".join(p for p in (first, other, last) if p)
    if order == "single":
        return last or first
    # other / 未選択は自動組み立てしない（手入力のみ、JS と同じ）
    return None


def compute_salutation_name(contact):
    """Contact の言語と姓名から salutation_name 文字列を組み立てる（仕様書 §1.5.1 / §11.9.7）。

    [性質] 純関数（contact の属性を読むだけ・DB 書き込みなし・副作用なし）
    [入力] contact: Contact インスタンス（lang / last_name / full_name 属性を duck typing で参照）
    [出力] str（言語別の宛名完成形。組み立て不能なら空文字）

    primary_lang（contact.lang）別ルール：
      - ja  ：「{last_name} 様」（last_name が空なら「{full_name} 様」）
      - ko  ：「{last_name} 님」（last_name が空なら「{full_name} 님」）
      - zh  ：「{full_name}」のみ（先生/女士の使い分けは v1.7+ で確定。本フェーズは文字列のみ）
      - en / und / その他：「Dear {full_name},」（カンマ終わり）
    last_name も full_name も両方空のときは空文字を返す。Contact モデルクラスは参照しない
    （循環 import 回避、§2.2）。
    """
    lang = (getattr(contact, "lang", "") or "").strip().lower()
    last_name = (getattr(contact, "last_name", "") or "").strip()
    full_name = (getattr(contact, "full_name", "") or "").strip()

    if lang.startswith("ja"):
        base = last_name or full_name
        return f"{base} 様" if base else ""
    if lang.startswith("ko"):
        base = last_name or full_name
        return f"{base} 님" if base else ""
    if lang.startswith("zh"):
        return full_name
    # en / und / その他
    return f"Dear {full_name}," if full_name else ""


# ----------------------------------------------------------------------
# §1.1.3 最小限正規化純関数（1 個）
# ----------------------------------------------------------------------


def normalize_original_script_for_full_name(raw):
    """original_script → full_name コピー時の保険正規化（仕様書 §3.3.1）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] raw: str（OCR の original_script の値）
    [出力] str（最小限正規化後。空入力は空文字を返す）

    全角空白→半角 / 連続空白を 1 つに統一 / 前後空白除去のみ。大文字小文字変換・
    全角英数字→半角の強制変換は入れない（OCR が title case で揃え済みのため）。
    """
    if not raw:
        return ""
    s = raw.replace("　", " ")  # 全角空白→半角
    s = re.sub(r"\s+", " ", s)  # 連続空白を 1 つに
    return s.strip()  # 前後空白除去


# ----------------------------------------------------------------------
# §1.1.4 補助関数（1 個）
# ----------------------------------------------------------------------


def is_generic_email_domain(domain):
    """汎用フリーメール・プロバイダドメインか判定する（仕様書 §11.9.6）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] domain: str（ドメイン名。例 "gmail.com"）
    [出力] bool（汎用ドメインなら True、それ以外 / 空入力なら False）

    重複検出側が会社一致判定の除外に使えるよう公開関数として export する。無視リストは
    本モジュール内の定数 ``_GENERIC_EMAIL_DOMAINS`` で管理する。
    """
    if not domain:
        return False
    return domain.strip().lower() in _GENERIC_EMAIL_DOMAINS


# ----------------------------------------------------------------------
# 二重検算（仕様書 §2.4）：電話系 5 フィールド × country の国番号照合
# ----------------------------------------------------------------------


def validate_phone_against_country(phone_e164, country):
    """電話番号（E.164）の国番号と country（ISO 3166-1 alpha-2）の整合を判定する（仕様書 §2.4）。

    [性質] 純関数（DB 非接触・副作用なし・例外を送出しない・bool を返す）
    [入力] phone_e164: str | None（E.164 形式の電話番号。先頭 "+" の有無どちらでも可）
           country: str | None（ISO 3166-1 alpha-2。"ZZ" は判定不能）
    [出力] bool（True=一致 または 検算スキップ / False=不一致＝呼び出し側で confidence を
           low に下げる対象）

    検算スキップ（True 返却）：phone_e164 が空 / country が空・"ZZ" / country が
    ``COUNTRY_CALLING_CODES`` に存在しない（防御的）/ 数字が 1 つもない。
    それ以外は country の国番号を導出し、phone の数字列が国番号で前方一致するかで判定する。
    OCR は E.164 を出力するため正規化後（"+" 除去）の数字列は国番号で始まる。region は
    使わず country のみで導出する（§注6）。米加 +1 圏等の同一国番号は判別できないが本フェーズ
    は誤判定を許容する（§注7）。
    """
    if not phone_e164:
        return True
    if not country or country.strip().upper() == "ZZ":
        return True
    calling_code = COUNTRY_CALLING_CODES.get(country.strip().upper())
    if calling_code is None:
        return True  # マッピング外は検算不能扱い
    digits = re.sub(r"\D", "", phone_e164)
    if not digits:
        return True
    return digits.startswith(str(calling_code))


# ----------------------------------------------------------------------
# 表示整形：電話番号の国際対応フォーマット化
# ----------------------------------------------------------------------


def format_phone_number_display(raw_phone):
    """E.164 形式の電話番号を国に応じた表示形式に整形して返す（画面表示専用・純関数）。

    [性質] 純関数（DB 非接触・副作用なし・例外を送出しない）
    [ルール]
      - 日本の番号 (国番号 81) -> 国内形式（例: "0565-35-6826" / "080-3610-3605"）
      - 日本以外の番号         -> 国際形式（例: "+1 206-555-0100"）
      - パース不能・空値等     -> 生データをそのまま返却（フォールバック）
    """
    if not raw_phone or not isinstance(raw_phone, str):
        return raw_phone or ""
    val = raw_phone.strip()
    if not val:
        return ""
    num_str = val if val.startswith("+") else "+" + val
    try:
        parsed = phonenumbers.parse(num_str, None)
        if not phonenumbers.is_valid_number(parsed):
            return val
        if parsed.country_code == 81:
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.NATIONAL
            )
        else:
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
    except Exception:
        return val

