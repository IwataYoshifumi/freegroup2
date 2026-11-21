"""ログインセキュリティ関連の共通集計ヘルパー（仕様書 v1.3.1 §6.9）。"""

from django.db.models import Q

from accounts.constants import ADMIN_ROLE_CODE
from accounts.models import CustomUser


def count_active_admins() -> int:
    """有効な管理者（管理者ロール保持者またはスーパーユーザー）の人数を返す。

    セキュリティ設定画面とユーザー一覧画面の双方から共通で利用する。
    """
    return (
        CustomUser.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(role__code=ADMIN_ROLE_CODE))
        .distinct()
        .count()
    )
