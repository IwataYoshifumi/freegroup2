"""ユーザーの特定規則・セキュリティ設定取得・管理者宛先取得（仕様書 v1.3.1 §5.1 / §5.5 / §14 #21）。"""

from dataclasses import dataclass

from django.db.models import Q

from accounts.constants import ADMIN_ROLE_CODE
from accounts.models import CustomUser, SecuritySetting
from accounts.services import is_admin_user


@dataclass(frozen=True)
class LoginUserLookupResult:
    """ログイン入力ユーザー名に対する特定結果。"""

    target_users: list[CustomUser]
    primary_user: CustomUser | None


def resolve_login_users(username_input: str | None) -> LoginUserLookupResult:
    """入力ユーザー名からロック判定・失敗加算対象のユーザーを特定する。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] username_input: str | None（画面で入力されたユーザー名）
    [出力] LoginUserLookupResult(target_users, primary_user)
           - 完全一致ユーザーがいれば ([exact], exact)
           - 完全一致がなく大文字小文字違いのみが複数いる場合は (全員リスト[id昇順], id最小の1人)
           - 該当なしまたは空文字なら ([], None)
    """
    normalized = (username_input or "").strip()
    if not normalized:
        return LoginUserLookupResult(target_users=[], primary_user=None)

    matched = list(
        CustomUser.objects.filter(username__iexact=normalized)
        .select_related("role")
        .order_by("id")
    )
    if not matched:
        return LoginUserLookupResult(target_users=[], primary_user=None)

    for user in matched:
        if user.username == normalized:
            return LoginUserLookupResult(target_users=[user], primary_user=user)

    return LoginUserLookupResult(target_users=matched, primary_user=matched[0])


def get_security_setting() -> SecuritySetting:
    """シングルトンのセキュリティ設定（id=1）を返す。

    [性質] 準関数（DB 読み取り。万一未作成時のみ id=1 を作成）
    [入力] なし
    [出力] SecuritySetting（id=1）
    """
    setting, _ = SecuritySetting.objects.get_or_create(id=1)
    return setting


def get_admin_notification_recipients() -> list[CustomUser]:
    """通知メール宛先の「管理者全員」（有効な管理者ロール保持者＋スーパーユーザーのうちメールあり）を返す。

    [性質] 準関数（DB 読み取り、副作用なし）
    [入力] なし
    [出力] list[CustomUser]（id 昇順。is_admin_user() を満たし email が空でないユーザー）
    """
    candidates = (
        CustomUser.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(role__code=ADMIN_ROLE_CODE))
        .exclude(email="")
        .select_related("role")
        .distinct()
        .order_by("id")
    )
    return [
        user
        for user in candidates
        if user.email and user.email.strip() and is_admin_user(user)
    ]
