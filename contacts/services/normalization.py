"""Contact 正規化基盤（仕様書 v1.6.0 本編 §11.9 / OCR 統合版 §6）。

3 経路（OCR / 手動入力 Form / AJAX）が共有するフィールド単位の純関数群。
DB 操作なし・副作用なし（check_name_consistency の補正理由ログだけは仕様、§2.4.1）。

[Phase 2 スコープ]
- 純関数本体の実装と Contact.save() オーバーライド（compute_salutation_name の補完呼び出し）まで。
- 3 経路からの呼び出し統合は Phase 3（OCR 経路 / json_parser）・Phase 4（Form/AJAX 経路）で行う。
- ContactFieldConfidence への記録ロジックは本モジュール・Contact.save() に組み込まない
  （Phase 3 で json_parser が bulk_create、Phase 4 で Form/View が記録する責務分担）。

[配置・命名]
仕様書 v1.6.0 本編 §11.9.1 / OCR 統合版 §6.1 で関数名は確定。命名規則は v1.4.2 §13.2
（normalize_* / compose_* / derive_* / check_* / compute_* / is_*）。

[循環 import 回避]
本モジュールは contacts.models.Contact を import しない。compute_salutation_name は
Contact インスタンスを引数で受け取り、属性アクセスのみ行う。
"""

from __future__ import annotations

import logging
import re
import unicodedata

from django.core.exceptions import ValidationError


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# モジュール内定数（仕様書未掲載のヘルパー定数は _ プレフィックス）
# ----------------------------------------------------------------------

# 漢数字→半角数字の翻訳テーブル（住所・電話の漢数字対応、仕様書 §11.9.5.1）。
# 「十」「百」「千」のような桁付きは住所表記で稀かつ「-」化処理と組み合わせるため非対応。
# 漢数字のゼロには「零」(U+96F6) と「〇」(U+3007 IDEOGRAPHIC NUMBER ZERO) の 2 表記がある。
_KANJI_DIGITS_TABLE = str.maketrans("零〇一二三四五六七八九", "00123456789")

# rest_of_address 用：「丁目」「番地」「番」「号」を「-」へ置換するパターン。
_ADDRESS_SUFFIX_PATTERN = re.compile(r"丁目|番地|番|号")

# 各種ハイフン類を「-」へ統一するパターン（rest_of_address 用）。
# em-dash / en-dash / 全角ハイフン / ハイフンマイナス / 引算記号 / non-breaking hyphen 等を吸収。
_HYPHEN_PATTERN = re.compile("[—―ー−‐–－]")

# organization の株式会社系表記揺れ統一マップ（仕様書 §11.9.5.1）。
# 前後位置差は吸収しない方針なので、置換は表記のみで位置移動はしない。
_ORG_LEGAL_FORM_ALIASES = {
    "(株)": "株式会社",
    "（株）": "株式会社",
    "㈱": "株式会社",
    "㍿": "株式会社",
    "(有)": "有限会社",
    "（有）": "有限会社",
    "㈲": "有限会社",
    "(合)": "合同会社",
    "（合）": "合同会社",
    "㈳": "社団法人",
    "㈶": "財団法人",
    "㈻": "学校法人",
    "㈵": "特殊法人",
}

# 汎用メールドメイン（フリーメール・キャリアメール）のマスター。
# 仕様書 §11.9.6 / §6.6：org_domain_name の値自体は名刺どおり残し、本リストは
# 重複検出側の会社一致判定をスキップする判定にのみ使う（is_generic_email_domain で公開）。
# 必要に応じて随時追加して良い。
_GENERIC_EMAIL_DOMAINS = frozenset(
    {
        # グローバル系
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.jp",
        "ymail.com",
        "outlook.com",
        "outlook.jp",
        "hotmail.com",
        "hotmail.co.jp",
        "live.com",
        "live.jp",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "protonmail.com",
        # 日本のプロバイダ・キャリアメール
        "nifty.com",
        "biglobe.ne.jp",
        "so-net.ne.jp",
        "ocn.ne.jp",
        "docomo.ne.jp",
        "ezweb.ne.jp",
        "softbank.ne.jp",
        "i.softbank.jp",
        "au.com",
        "ymobile.ne.jp",
    }
)


# 信頼度の順位（check_name_consistency の補正方向判定用）。high→mid→low の下降のみ。
_CONFIDENCE_RANK = {"high": 3, "mid": 2, "low": 1}


# ======================================================================
# 正規化純関数群（仕様書 §11.9.5.1 / §6.5）
# ======================================================================

def normalize_full_name(raw):
    """raw を full_name 用に正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数（DB操作なし・副作用なし、ValidationError は raise する）
    [入力] raw: str / None
    [出力] str（正規化済み・非空）
    [例外] ValidationError（正規化後が空文字の場合）

    処理：全角空白→半角・半角空白除去・全角英数字→半角・前後空白除去・空なら ValidationError。
    内部の半角空白も除去する点に注意（姓名間スペースは消える）。
    OCR 経路から original_script をコピーする際の最小限正規化は
    normalize_original_script_for_full_name を使う（§3.3.1、姓名間スペースを残す別ルール）。
    """
    if raw is None:
        raise ValidationError("full_name must not be empty")
    s = unicodedata.normalize("NFKC", str(raw))
    s = s.replace(" ", "")
    s = s.strip()
    if not s:
        raise ValidationError("full_name must not be empty")
    return s


def normalize_organization(raw):
    """raw を organization 用に正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数
    [入力] raw: str / None
    [出力] str

    処理：株式会社系の表記揺れを統一（前後位置差は吸収しない）/ NFKC（全角英数字→半角・
    全角空白→半角）/ 全角半角空白除去 / 前後空白除去。
    """
    if raw is None:
        return ""
    s = str(raw)
    for alias, canonical in _ORG_LEGAL_FORM_ALIASES.items():
        s = s.replace(alias, canonical)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace(" ", "")
    return s.strip()


def normalize_phone_value(raw):
    """raw を電話番号 1 件分として正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数
    [入力] raw: str / None（配列フィールド personal_phone / personal_fax / org_phone /
           org_fax には呼び出し側で各要素ごとに適用）
    [出力] str

    処理：NFKC（全角数字→半角）/ 漢数字→半角 / 数字と「+」以外を除去
    （ハイフン・空白・カッコ等を吸収）。
    OCR は E.164 形式（仕様書 §1.2 / §2.4）で出力する想定のため、「+」を残しつつ非数字を除去。
    積極的な国番号正規化（0XXX → +81XXX 等）は本フェーズでは行わない（OCR 出力依存）。
    """
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    s = s.translate(_KANJI_DIGITS_TABLE)
    return re.sub(r"[^\d+]", "", s)


def normalize_email(raw):
    """raw を email 用に正規化する（仕様書 §11.9.5.1）：小文字化 + 前後空白除去。

    [性質] 純関数
    """
    if raw is None:
        return ""
    return str(raw).strip().lower()


def normalize_rest_of_address(raw):
    """raw を rest_of_address 用に正規化する（仕様書 §11.9.5.1）。

    [性質] 純関数

    処理：NFKC（全角英数字→半角・全角空白→半角）/ 漢数字→半角 /
    丁目・番地・番・号→「-」/ ハイフン類統一 / 全角半角空白除去 / 前後空白除去。
    """
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    s = s.translate(_KANJI_DIGITS_TABLE)
    s = _ADDRESS_SUFFIX_PATTERN.sub("-", s)
    s = _HYPHEN_PATTERN.sub("-", s)
    s = s.replace(" ", "").replace("　", "")
    return s.strip()


def normalize_postal_code(raw):
    """raw を postal_code 用に正規化する（仕様書 §11.9.5.1）：数字のみ。

    [性質] 純関数

    処理：NFKC（全角数字→半角）/ 漢数字→半角 / 数字以外を除去（ハイフンも除去）。
    """
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    s = s.translate(_KANJI_DIGITS_TABLE)
    return re.sub(r"\D", "", s)


def normalize_department_title_branch(raw):
    """raw を department / title / branch 共通の正規化（仕様書 §11.9.5.1）。

    [性質] 純関数

    処理：NFKC（全角英数字→半角・全角空白→半角）/ 全角半角空白除去 / 前後空白除去。
    """
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    s = s.replace(" ", "").replace("　", "")
    return s.strip()


def normalize_original_script_for_full_name(raw):
    """OCR の original_script を full_name にコピーする際の最小限正規化（仕様書 §3.3.1）。

    [性質] 純関数

    処理（保険）：全角空白→半角 / 連続空白を 1 つに統一 / 前後空白除去。
    OCR プロンプト側で title case 等の体裁は揃っている前提のため、大文字小文字変換・
    全角英数字→半角の強制変換は行わない（normalize_full_name とは別ルール）。
    """
    if raw is None:
        return ""
    s = str(raw).replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ======================================================================
# 派生・組み立て純関数群（仕様書 §11.9.4 / §11.9.6 / §1.5）
# ======================================================================

def compose_full_address(postal_code, region, city, rest_of_address, country, lang):
    """4 要素から full_address を組み立てる（仕様書 §11.9.4 / §6.4）。

    [性質] 純関数
    [入力] postal_code / region / city / rest_of_address / country / lang: 各 str / None
    [出力] str（Contact.address に格納する想定）

    本フェーズは日本式のみ：`〒{postal_code} {region}{city}{rest_of_address}`。
    ja 以外も日本式と同順序で暫定組み立て（英語式・番地先国最後は v1.7+ で確定予定、
    仕様書 §6.4 / 第 8 部）。
    「〒」記号は lang='ja' のときのみ前置（その他言語では postal_code をそのまま前置）。
    いずれの引数が空でも組み立てが落ちないこと。

    country 引数は本フェーズでは未使用（インターフェース予約のみ。v1.7+ で英語式分岐に
    使う想定）。
    """
    postal_code = (postal_code or "").strip()
    region = (region or "").strip()
    city = (city or "").strip()
    rest_of_address = (rest_of_address or "").strip()

    parts = []
    if postal_code:
        parts.append(f"〒{postal_code}" if lang == "ja" else postal_code)
    body = f"{region}{city}{rest_of_address}"
    if body:
        parts.append(body)
    return " ".join(parts)


def derive_org_core_name(org_name_full, legal_entity_type):
    """org_name_full から legal_entity_type の文字列を除去し core 名を返す（仕様書 §11.9.5）。

    [性質] 純関数
    [入力] org_name_full: str / None, legal_entity_type: str / None
    [出力] str（org_name_full が空なら空文字、legal_entity_type が空なら org_name_full を strip）

    位置（前/後/中間）を問わず除去する。legal_entity_type_position 引数は取らない設計
    （位置情報は legal_entity_type 文字列の有無で十分、Phase 1B の TextChoices インナークラスで定義）。
    """
    if not org_name_full:
        return ""
    name = str(org_name_full)
    if legal_entity_type:
        name = name.replace(str(legal_entity_type), "")
    return name.strip()


def derive_org_domain_name(email):
    """email から @ 以降のドメイン部分を返す（仕様書 §11.9.6）。

    [性質] 純関数

    汎用ドメイン（gmail.com 等）に該当しても値は空にしない（名刺どおり残す）。
    無視リストでの判定は is_generic_email_domain を別途呼ぶこと（重複検出側の責務）。
    """
    if not email:
        return ""
    s = str(email).strip().lower()
    if "@" not in s:
        return ""
    return s.split("@", 1)[1]


def is_generic_email_domain(domain):
    """ドメインが汎用メール（フリーメール・キャリアメール）に該当するかを判定。

    [性質] 純関数

    重複検出側がこの判定を参照し、True のときは会社一致判定（org_domain_name の一致）を
    スキップする想定（仕様書 §11.9.6 / §6.6）。derive_org_domain_name 側は値を空にしないため、
    本判定は別軸で呼ぶ。
    """
    if not domain:
        return False
    return str(domain).strip().lower() in _GENERIC_EMAIL_DOMAINS


# ======================================================================
# name ブロック整合性チェック（仕様書 §2.4.1）
# ======================================================================

def _downgrade_confidence(current, target):
    """[性質] 純関数。current 信頼度を target 方向に下げる（下げる方向のみ、上げない）。

    current が None や想定外の値なら target を採用（OCR 側でレコードが無い場合の補完）。
    target が想定外の値なら current のまま返す（補正しない）。
    """
    if current not in _CONFIDENCE_RANK:
        return target
    if target not in _CONFIDENCE_RANK:
        return current
    if _CONFIDENCE_RANK[target] < _CONFIDENCE_RANK[current]:
        return target
    return current


def check_name_consistency(name_block):
    """name ブロックの整合性 4 種類を裏で検算し、補正後 confidence を返す（仕様書 §2.4.1）。

    [性質] 純関数（補正理由のサーバーログ出力は仕様、§2.4.1 末尾）
    [入力] name_block: dict
      期待構造：各キーが {"value": ..., "confidence": ...} の dict。
      対象キー: original_script / last_name / first_name / other_name_parts /
              name_order / salutation_name / primary_lang
      ※primary_lang は name_block の階層に置く想定（OCR 出力 JSON では metadata 配下だが、
        本関数はチェック用に必要キーを集約した辞書を受け取る前提）。
    [出力] dict[str, str]: 5 フィールド分の補正後 confidence
      キー: last_name / first_name / other_name_parts / name_order / salutation_name

    補正方針：違反検出時は confidence を下げる方向のみ（high→mid→low）、上げない。
    補正理由は logger.info でサーバーログに出力（DB に保存しない、UI 拡張は本フェーズ対象外）。

    チェック 4 種類：
    (a) 文字カバー率：original_script の文字（空白除く）を構成要素
        (last_name + first_name + other_name_parts) がカバー
        カバー率 < 80% で last_name / first_name / other_name_parts を mid に補正
    (b) name_order と構成要素の整合性：
        single なのに last/first 両方値あり / last_first or first_last なのに last か first 空
        → name_order を low に補正
    (c) primary_lang と name_order の整合性：
        ja で first_last / en で last_first → name_order を mid に補正
    (d) salutation_name と姓の整合性（ja のみ）：
        salutation_name に last_name 文字列が含まれない → salutation_name を low に補正

    閾値（カバー率 80% 等）は本フェーズではコード君判断（運用しながら調整）。
    """
    if not isinstance(name_block, dict):
        return {}

    def _value_of(key):
        entry = name_block.get(key)
        if isinstance(entry, dict):
            return entry.get("value") or ""
        return entry or ""

    def _confidence_of(key):
        entry = name_block.get(key)
        if isinstance(entry, dict):
            return entry.get("confidence")
        return None

    last_name = _value_of("last_name")
    first_name = _value_of("first_name")
    other_name_parts = _value_of("other_name_parts")
    original_script = _value_of("original_script")
    name_order = _value_of("name_order")
    salutation_name = _value_of("salutation_name")
    primary_lang = _value_of("primary_lang")

    result = {
        "last_name": _confidence_of("last_name"),
        "first_name": _confidence_of("first_name"),
        "other_name_parts": _confidence_of("other_name_parts"),
        "name_order": _confidence_of("name_order"),
        "salutation_name": _confidence_of("salutation_name"),
    }

    # (a) 文字カバー率（閾値 80%）
    if original_script:
        os_chars = {c for c in original_script if not c.isspace()}
        constituent = f"{last_name}{first_name}{other_name_parts}"
        constituent_chars = {c for c in constituent if not c.isspace()}
        if os_chars:
            uncovered = os_chars - constituent_chars
            coverage = 1.0 - (len(uncovered) / len(os_chars))
            if coverage < 0.8:
                logger.info(
                    "name consistency: original_script coverage %.2f%% < 80%% "
                    "(uncovered chars=%r). Downgrading last_name/first_name/"
                    "other_name_parts to mid.",
                    coverage * 100,
                    "".join(sorted(uncovered)),
                )
                for k in ("last_name", "first_name", "other_name_parts"):
                    result[k] = _downgrade_confidence(result[k], "mid")

    # (b) name_order と構成要素の整合性
    if name_order == "single":
        if last_name and first_name:
            logger.info(
                "name consistency: name_order='single' but both last_name=%r "
                "and first_name=%r present. Downgrading name_order to low.",
                last_name, first_name,
            )
            result["name_order"] = _downgrade_confidence(result["name_order"], "low")
    elif name_order in ("last_first", "first_last"):
        if not last_name or not first_name:
            logger.info(
                "name consistency: name_order=%r but last_name=%r / first_name=%r "
                "incomplete. Downgrading name_order to low.",
                name_order, last_name, first_name,
            )
            result["name_order"] = _downgrade_confidence(result["name_order"], "low")

    # (c) primary_lang と name_order の整合性
    if primary_lang == "ja" and name_order == "first_last":
        logger.info(
            "name consistency: primary_lang=ja but name_order=first_last. "
            "Downgrading name_order to mid."
        )
        result["name_order"] = _downgrade_confidence(result["name_order"], "mid")
    elif primary_lang == "en" and name_order == "last_first":
        logger.info(
            "name consistency: primary_lang=en but name_order=last_first. "
            "Downgrading name_order to mid."
        )
        result["name_order"] = _downgrade_confidence(result["name_order"], "mid")

    # (d) salutation_name と姓の整合性（ja のみ）
    if primary_lang == "ja" and salutation_name and last_name:
        if last_name not in salutation_name:
            logger.info(
                "name consistency: salutation_name=%r does not contain "
                "last_name=%r. Downgrading salutation_name to low.",
                salutation_name, last_name,
            )
            result["salutation_name"] = _downgrade_confidence(
                result["salutation_name"], "low"
            )

    return result


# ======================================================================
# salutation_name 組み立て（仕様書 §1.5 / §11.9.7）
# ======================================================================

def compute_salutation_name(contact):
    """contact から salutation_name を組み立てる（仕様書 §1.5.1 / §1.5.5）。

    [性質] 純関数（contact の状態を読むだけ、DB 書き込みなし）
    [入力] contact: Contact インスタンス（型として import しない）
    [出力] salutation_name 文字列（材料が完全に空なら空文字）

    Contact モデルクラスは import しない設計（循環 import 回避）：
    contact.lang / contact.last_name / contact.full_name のみ getattr で参照。

    文化別ルール（仕様書 §1.5.1）：
    - ja：「{last_name} 様」（last_name 空なら「{full_name} 様」）
    - ko：「{last_name} 님」（last_name 空なら「{full_name} 님」）
    - zh：本フェーズは「{full_name}」のみ（v1.7+ で性別敬称の確定実装、confidence は
          呼び出し元が mid 固定で扱う、§1.5.1）
    - en / und / その他：「Dear {full_name},」（カンマ終わり）
    - 材料が完全に空（last_name も full_name も両方空）：空文字を返す
      （呼び出し元の Contact.save() は空文字なら何もしないことを期待、§1.5.3）
    """
    lang = (getattr(contact, "lang", "") or "").lower()
    last_name = (getattr(contact, "last_name", "") or "").strip()
    full_name = (getattr(contact, "full_name", "") or "").strip()

    if not last_name and not full_name:
        return ""

    if lang == "ja":
        base = last_name or full_name
        return f"{base} 様"
    if lang == "ko":
        base = last_name or full_name
        return f"{base} 님"
    if lang == "zh":
        # v1.7+ で性別敬称（先生/女士）の確定実装。本フェーズは full_name フォールバックのみ。
        return full_name or last_name
    # en / und / その他
    base = full_name or last_name
    return f"Dear {base},"
