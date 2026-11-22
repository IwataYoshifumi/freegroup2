"""対象外の判定および新パスワードの検証（仕様書 v1.0 §5.2 / §5.5 / §6.4 / §13-9）。"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.constants import AuthSource


def is_password_self_service_eligible(user) -> bool:
    """ユーザーが本人によるパスワード変更・リセットの対象かを判定する（仕様書 §5.5 / §13-9）。

    次の 3 条件のどれか 1 つでも当てはまる場合は対象外（False）：
      1. 認証元が LDAP（auth_source == "ldap"）
      2. LDAP の識別名がある（ldap_dn が空でない）
      3. パスワードが使用不可（not user.has_usable_password()）

    パスワードが使えるか（has_usable_password）だけで判定しないこと。
    """
    if user is None:
        return False
    if getattr(user, "auth_source", None) == AuthSource.LDAP:
        return False
    if (getattr(user, "ldap_dn", None) or "").strip():
        return False
    if not user.has_usable_password():
        return False
    return True


def validate_new_password_for_user(
    user,
    new_password: str | None,
) -> tuple[bool, str | None, tuple[str, ...]]:
    """新パスワードの強度検証（AUTH_PASSWORD_VALIDATORS）と現在パスワードとの重複検査を行う。

    [性質] 純関数（DB 読み取りなし。user オブジェクトを参照）
    [入力] user: CustomUser, new_password: str | None
    [出力] (ok, error_kind, messages)
           - ok=True: (True, None, ())
           - 強度不足・空: (False, "validation_error", (...))
           - 現在と同じ: (False, "same_as_current", (...))
    """
    candidate = "" if new_password is None else str(new_password)
    if not candidate:
        try:
            validate_password("", user=user)
        except ValidationError as exc:
            return False, "validation_error", tuple(exc.messages)
        return (
            False,
            "validation_error",
            ("新しいパスワードを入力してください。",),
        )

    try:
        validate_password(candidate, user=user)
    except ValidationError as exc:
        return False, "validation_error", tuple(exc.messages)

    if user.check_password(candidate):
        return (
            False,
            "same_as_current",
            ("新しいパスワードは現在のパスワードと異なるものを入力してください。",),
        )

    return True, None, ()
