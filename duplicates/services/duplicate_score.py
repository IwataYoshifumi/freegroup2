"""重複検出のスコア計算とランク判定（仕様書 §8.3 / §8.4 / §10.5）。

公開関数：

- determine_score_and_rank(contact_a, contact_b)
  → 2 Contact のスコアとランクを返す準関数。find_duplicate_contacts から呼ばれる。

内部関数：

- _calculate_score(contact_a, contact_b)
  → スコア計算。両 Contact で「confidence='high' 扱い かつ 値が完全一致」のフィールド
     のみ加算。confidence の判定は Contact.get_high_fields()（A-2b 実装済み）に委譲。
- _determine_rank(score, contact_a, contact_b)
  → ランク判定（純関数）。スコア閾値とフィールド一致条件を AND で評価する。
- _is_generic_email(email)
  → 代表メール判定。仕様書 §8.7 の DUPLICATE_GENERIC_EMAIL_LOCALPARTS を使う純関数。

設計趣旨（B 前半指示書 §3.1）：本モジュールは履歴を参照しない。フィールド値と
confidence のみで判定する純粋なスコア計算ロジック。DuplicateCandidate テーブル
や PersonMergeLog 等の履歴は参照しない。
"""

import re

from config.constants import (
    DUPLICATE_FIELD_SCORES,
    DUPLICATE_GENERIC_EMAIL_LOCALPARTS,
    DUPLICATE_LOCATION_FIELDS,
    DUPLICATE_SCORE_EMAIL_GENERIC,
    DUPLICATE_SCORE_EMAIL_PERSONAL,
    POSSIBLE_HIGH_MIN_SCORE,
    POSSIBLE_LOW_MIN_SCORE,
    POSSIBLE_MID_MIN_SCORE,
)
from duplicates.models import DuplicateCandidate


def determine_score_and_rank(contact_a, contact_b):
    """2 Contact の重複スコアとランクを返す（仕様書 §10.5 / v0.1.5 §2.6）。

    [性質] 準関数（DB 読み取りのみ。Contact.get_high_fields() 経由で confidence を読む）
    [入力] contact_a: Contact, contact_b: Contact（どちらも status='primary' 想定）
    [出力] (score: int, rank: str) のタプル。rank は 'exact_match' / 'possible_high'
           / 'possible_mid' / 'possible_low' / 'none' のいずれか
    """
    score = _calculate_score(contact_a, contact_b)
    rank = _determine_rank(score, contact_a, contact_b)
    return score, rank


def _calculate_score(contact_a, contact_b):
    """重複スコアを計算する（仕様書 §8.3 / §10.5）。

    [性質] 準関数（DB 読み取り：Contact.get_high_fields() 経由）
    [入力] contact_a: Contact, contact_b: Contact
    [出力] int（合計スコア、>= 0）

    加算条件：
      - 両 Contact のフィールドが「両方 high 扱い」かつ「値が完全一致（空文字でない）」
      - confidence='high'（疑似 high）または confirmed_at != None（確認済み low/mid）
        を「high 扱い」と判定（A-2b 実装済み Contact.get_high_fields()）
      - email は個人メール / 代表メールで点数が異なるため、_is_generic_email() で判定

    branch は所属5フィールド判定にのみ参加し、スコア加算はしない（仕様書 §8.3 配点表）。
    """
    high_a = contact_a.get_high_fields()
    high_b = contact_b.get_high_fields()

    score = 0
    for field, points in DUPLICATE_FIELD_SCORES.items():
        if _both_high_and_equal(contact_a, contact_b, field, high_a, high_b):
            score += points

    # email は個人/代表で点数を分岐
    if _both_high_and_equal(contact_a, contact_b, "email", high_a, high_b):
        if _is_generic_email(contact_a.email):
            score += DUPLICATE_SCORE_EMAIL_GENERIC
        else:
            score += DUPLICATE_SCORE_EMAIL_PERSONAL

    return score


def _determine_rank(score, contact_a, contact_b):
    """スコアと Contact 値からランクを判定する（仕様書 §8.4）。

    [性質] 準関数（Contact.get_high_fields() を呼ぶため）
    [入力] score: int, contact_a: Contact, contact_b: Contact
    [出力] str（'exact_match' / 'possible_high' / 'possible_mid' / 'possible_low' / 'none'）

    判定順序（上から評価し、合致したら確定）：
      1. score >= 200 AND 所属5フィールド両方一致 or 両方空 → exact_match
      2. score >= 200 → possible_high（フルネーム不一致でも到達可能）
      3. score >= 120 AND フルネーム一致 AND (メール一致 OR 携帯一致) → possible_mid
      4. score >= 40 AND フルネーム一致 → possible_low
      5. それ以外 → none

    閾値とフィールド一致条件は AND 関係（B 前半指示書の確認事項4で確定）。
    「フィールド一致」はスコア加算と同じく「両方 high 扱い かつ 値完全一致」を意味する。
    """
    high_a = contact_a.get_high_fields()
    high_b = contact_b.get_high_fields()

    full_name_match = _both_high_and_equal(
        contact_a, contact_b, "full_name", high_a, high_b
    )
    email_match = _both_high_and_equal(
        contact_a, contact_b, "email", high_a, high_b
    )
    mobile_match = _both_high_and_equal(
        contact_a, contact_b, "mobile_phone", high_a, high_b
    )

    if score >= POSSIBLE_HIGH_MIN_SCORE:
        if _location_fields_match_or_both_empty(contact_a, contact_b, high_a, high_b):
            return DuplicateCandidate.Rank.EXACT_MATCH
        return DuplicateCandidate.Rank.POSSIBLE_HIGH

    if score >= POSSIBLE_MID_MIN_SCORE and full_name_match and (email_match or mobile_match):
        return DuplicateCandidate.Rank.POSSIBLE_MID

    if score >= POSSIBLE_LOW_MIN_SCORE and full_name_match:
        return DuplicateCandidate.Rank.POSSIBLE_LOW

    return DuplicateCandidate.Rank.NONE


# ----------------------------------------------------------------------
# 内部ヘルパー
# ----------------------------------------------------------------------

def _both_high_and_equal(contact_a, contact_b, field, high_a, high_b):
    """両 Contact の指定フィールドが「両方 high 扱い かつ 値が完全一致（非空）」か。

    [性質] 純関数（high_a / high_b 引数経由で渡される set を見るだけ）
    [入力] contact_a / contact_b: Contact, field: str, high_a / high_b: set[str]
    [出力] bool

    呼び出し側で `Contact.get_high_fields()` の結果（high_a / high_b）を一度取得して
    使い回すことで、同じ Contact について複数回 confidence を読まない設計（_calculate_score
    と _determine_rank がそれぞれ独立に高々 1 回しか呼ばないようにする）。
    """
    if field not in high_a or field not in high_b:
        return False
    val_a = getattr(contact_a, field, "") or ""
    val_b = getattr(contact_b, field, "") or ""
    return bool(val_a) and val_a == val_b


def _location_fields_match_or_both_empty(contact_a, contact_b, high_a, high_b):
    """所属5フィールドの「両方一致 or 両方空」判定（仕様書 §8.4 exact_match 条件）。

    [性質] 純関数（high_a / high_b は引数で受け取る）
    [入力] contact_a / contact_b: Contact, high_a / high_b: set[str]
    [出力] bool

    判定対象：DUPLICATE_LOCATION_FIELDS（organization / department / title / branch / address）
    各フィールドにつき以下のいずれかを満たす必要がある：
      - 両 Contact で値が空文字（confidence は問わない、空なら自動 high 扱い）
      - 両 Contact で「両方 high 扱い」かつ「値が完全一致」

    全 5 フィールドが上記いずれかを満たして初めて True を返す。
    """
    for field in DUPLICATE_LOCATION_FIELDS:
        val_a = getattr(contact_a, field, "") or ""
        val_b = getattr(contact_b, field, "") or ""
        both_empty = not val_a and not val_b
        match = _both_high_and_equal(contact_a, contact_b, field, high_a, high_b)
        if not (both_empty or match):
            return False
    return True


def _is_generic_email(email):
    """代表メール判定（仕様書 §8.7）。

    [性質] 純関数（DB アクセスなし、文字列処理のみ）
    [入力] email: str
    [出力] bool（代表メール扱いなら True）

    判定ロジック：
      1. ローカル部（@ より前）を取り出して小文字化
      2. ハイフン / アンダースコア / ドットで分割
      3. 各セグメントから英字のみを抽出
      4. その英字部分が DUPLICATE_GENERIC_EMAIL_LOCALPARTS のいずれかと完全一致すれば True

    仕様書 §8.7 のバリエーション例（info-jp@、sales_team@、support2@）をすべてカバー。
    'infomation' のような「単語の一部に generic 語を含むだけ」のローカル部は False。
    """
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower()
    segments = re.split(r"[-_.]", local)
    for seg in segments:
        alpha = "".join(c for c in seg if c.isalpha())
        if alpha and alpha in DUPLICATE_GENERIC_EMAIL_LOCALPARTS:
            return True
    return False
