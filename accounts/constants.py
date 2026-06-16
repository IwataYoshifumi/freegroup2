from django.db import models


class AuthSource(models.TextChoices):
    """認証ソース（仕様書 §4.1）。"""

    LOCAL = "local", "ローカル"
    LDAP = "ldap", "LDAP"


class ActionLogAction:
    """ActionLog の action フィールドの文字列定数（v1.5.0 で追加分）。

    v1.4.2 既存の action（merged / undone / different_person 等）はリテラルのまま
    使用されているため、v1.5.0 で新規追加する 3 項目のみここで定数化する。
    """

    LINK_USER_TO_PERSON = "link_user_to_person"
    UNLINK_USER_FROM_PERSON = "unlink_user_from_person"
    RETIRE_USER = "retire_user"
    ASSIGN_ROLE = "assign_role"
    ROLE_CREATE = "role_create"
    ROLE_UPDATE = "role_update"
    ROLE_DELETE = "role_delete"


#: admin ロールの安定キー（Role.code）。本体業務 UI からの admin 任免を禁止する
#: 両方向ガード（assign_role_to_user / ロール割り当て UI）で参照する。admin の任免は
#: Django Admin 領域に寄せる（is_staff 分離思想）。Role コードは仕様書 §13.8 準拠。
ADMIN_ROLE_CODE = "admin"


class PersonLinkStatus:
    """ホーム画面アラートのステータス値（仕様書 §12.4）。"""

    SINGLE_CANDIDATE = "single_candidate"
    MULTIPLE_CANDIDATES_NEED_MERGE = "multiple_candidates_need_merge"
