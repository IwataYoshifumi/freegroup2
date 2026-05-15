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


class PersonLinkStatus:
    """ホーム画面アラートのステータス値（仕様書 §12.4）。"""

    SINGLE_CANDIDATE = "single_candidate"
    MULTIPLE_CANDIDATES_NEED_MERGE = "multiple_candidates_need_merge"
