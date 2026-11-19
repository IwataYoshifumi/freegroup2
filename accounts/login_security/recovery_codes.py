"""リカバリーコードの発行・鍵付きハッシュ照合・消費・残数取得（仕様書 v1.3.1 §6.3 / §14 #12）。"""

import re
import secrets
import unicodedata

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts.models import RecoveryCode

# 読み間違えやすい文字（0/O、1/l/I）を除外した英数字（大文字）
RECOVERY_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
RECOVERY_CODE_LENGTH = 10
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_HMAC_SALT = "accounts.recovery_code"


def normalize_recovery_code(code: str | None) -> str:
    """入力されたリカバリーコードを NFKC 正規化し、空白とハイフンを除去して大文字にそろえる。

    [性質] 純関数（副作用なし）
    [入力] code: str | None
    [出力] str
    """
    normalized = unicodedata.normalize("NFKC", code or "")
    return re.sub(r"[\s\-\u2010-\u2015\u2212\uff0d]+", "", normalized).upper()


def hash_recovery_code(code: str | None, *, secret: str | None = None) -> str:
    """リカバリーコードの鍵付きハッシュ（HMAC-SHA256）を計算する。

    [性質] 純関数（settings.SECRET_KEY または指定の secret を参照、副作用なし）
    [入力] code: str | None, secret: str | None
    [出力] str（64文字の16進ダイジェスト）
    """
    normalized = normalize_recovery_code(code)
    key = settings.SECRET_KEY if secret is None else secret
    return salted_hmac(
        RECOVERY_CODE_HMAC_SALT,
        normalized,
        secret=key,
        algorithm="sha256",
    ).hexdigest()


def _generate_formatted_code() -> str:
    raw = "".join(
        secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH)
    )
    return f"{raw[:5]}-{raw[5:]}"


def issue_recovery_codes(user) -> list[str]:
    """既存のリカバリーコードを無効化し、新規に 10 個を発行して表示用コードを返す。

    [性質] 副作用あり（DB 書込：旧 RecoveryCode 削除、新規 10 件作成。transaction.atomic 内）
    [入力] user: CustomUser
    [出力] list[str]（5桁ハイフン区切りの平文コード 10 個。この戻り値でのみ返す）

    注意：この関数は自前で atomic を持つ。失敗記録を残す関数（ロック・試行記録）の中からは呼ばないこと。
    注意：平文のコードはログ・操作ログに出力しないこと。
    """
    plain_codes: list[str] = []
    seen_hashes: set[str] = set()
    records: list[RecoveryCode] = []

    while len(plain_codes) < RECOVERY_CODE_COUNT:
        formatted = _generate_formatted_code()
        code_hash = hash_recovery_code(formatted)
        if code_hash in seen_hashes:
            continue
        seen_hashes.add(code_hash)
        plain_codes.append(formatted)
        records.append(RecoveryCode(user=user, code_hash=code_hash))

    with transaction.atomic():
        RecoveryCode.objects.filter(user=user).delete()
        RecoveryCode.objects.bulk_create(records)
    return plain_codes


def verify_and_consume_recovery_code(user, code: str | None) -> bool:
    """リカバリーコードを照合し、未使用なら使用済みに更新して True を返す（1個1回限り）。

    [性質] 副作用あり（DB 書込：一致した未使用コードの used_at を更新）
    [入力] user: CustomUser, code: str | None
    [出力] bool

    SECRET_KEY および SECRET_KEY_FALLBACKS の各鍵でハッシュを計算し、
    対象ユーザーの未使用コードのみを対象にハッシュ値で直接検索する。
    """
    normalized = normalize_recovery_code(code)
    if not normalized:
        return False

    keys = [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", [])]
    candidate_hashes = [hash_recovery_code(normalized, secret=k) for k in keys if k]
    if not candidate_hashes:
        return False

    updated = RecoveryCode.objects.filter(
        user=user,
        used_at__isnull=True,
        code_hash__in=candidate_hashes,
    ).update(used_at=timezone.now())
    return updated > 0


def get_remaining_recovery_code_count(user) -> int:
    """対象ユーザーの未使用リカバリーコード残数を返す。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] user: CustomUser
    [出力] int
    """
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()
