"""対象ユーザーのセッション切断（仕様書 v1.3.1 §6.10 / §14 #16）。"""

from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.utils import timezone


def revoke_user_sessions(
    user,
    keep_session_key: str | None = None,
    *,
    now=None,
) -> int:
    """有効期限内のセッションを 1 件ずつ復号し、対象ユーザーのセッションだけを削除する。

    [性質] 副作用あり（DB 書込：django_session 削除。CustomUser は保存しない）
    [入力] user: CustomUser
           keep_session_key: str | None（本人操作時に残す操作中セッションキー。管理者リセット時は None）
           now: datetime | None
    [出力] int（削除したセッション件数）

    前提：settings.SESSION_ENGINE がデータベース方式（django.contrib.sessions.backends.db）であること。
    注意：全件を一括ロードしないよう iterator() を使用し、壊れたセッションは例外にせずスキップする。
          セッションの中身はログに出力しない。
    """
    current = now or timezone.now()
    target_user_id = str(user.pk)
    keys_to_delete: list[str] = []

    qs = Session.objects.filter(expire_date__gt=current).only(
        "session_key", "session_data"
    )
    for session in qs.iterator(chunk_size=200):
        if keep_session_key and session.session_key == keep_session_key:
            continue
        try:
            data = session.get_decoded()
        except Exception:
            continue
        session_user_id = data.get(SESSION_KEY)
        if session_user_id is not None and str(session_user_id) == target_user_id:
            keys_to_delete.append(session.session_key)

    if not keys_to_delete:
        return 0

    deleted_count, _ = Session.objects.filter(
        session_key__in=keys_to_delete
    ).delete()
    return deleted_count
