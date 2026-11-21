"""accounts 管理コマンド共通ヘルパー（仕様書 v1.3.1 §6.8 / §12.2）。"""

from django.core.management.base import CommandError

from accounts.models import CustomUser


def find_user_exact_or_raise(username: str) -> CustomUser:
    """ユーザー名の完全一致でユーザーを特定する。

    見つからない場合は大文字・小文字のみが異なる候補をメッセージに示し CommandError を送出する。
    """
    user = (
        CustomUser.objects.filter(username=username)
        .select_related("role", "person", "person__primary_contact")
        .first()
    )
    if user is not None:
        return user

    candidates = list(
        CustomUser.objects.filter(username__iexact=username)
        .order_by("id")
        .values_list("username", flat=True)
    )
    if candidates:
        candidate_list = "、".join(candidates)
        raise CommandError(
            f"ユーザー '{username}' が見つかりません。"
            f"大文字・小文字が異なる候補があります: {candidate_list}"
        )

    raise CommandError(f"ユーザー '{username}' が見つかりません。")
