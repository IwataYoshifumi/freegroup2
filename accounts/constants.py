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

    # ログインセキュリティ強化（仕様書 v1.3.1 付録A）
    SECURITY_SETTING_UPDATE = "security_setting_update"  # セキュリティ設定の変更
    TWO_FACTOR_ENABLE = "two_factor_enable"  # 二段階認証の有効化
    TWO_FACTOR_DISABLE = "two_factor_disable"  # 二段階認証の無効化
    TWO_FACTOR_REENROLL = "two_factor_reenroll"  # 二段階認証の再登録
    RECOVERY_CODES_REGENERATE = "recovery_codes_regenerate"  # リカバリーコードの再発行
    TWO_FACTOR_RESET = "two_factor_reset"  # 管理者による二段階認証リセット
    ACCOUNT_LOCK = "account_lock"  # アカウントロック発生
    ACCOUNT_UNLOCK = "account_unlock"  # ロックの手動解除
    IP_BLOCK = "ip_block"  # IP 遮断発生
    LOGIN_DEVICE_REVOKE = "login_device_revoke"  # 端末の信頼取り消し・削除


#: admin ロールの安定キー（Role.code）。本体業務 UI からの admin 任免を禁止する
#: 両方向ガード（assign_role_to_user / ロール割り当て UI）で参照する。admin の任免は
#: Django Admin 領域に寄せる（is_staff 分離思想）。Role コードは仕様書 §13.8 準拠。
ADMIN_ROLE_CODE = "admin"


class PersonLinkStatus:
    """ホーム／プロフィール紐付け候補アラートのステータス値（仕様書 §12.4）。"""

    SINGLE_CANDIDATE = "single_candidate"
    MULTIPLE_CANDIDATES_NEED_MERGE = "multiple_candidates_need_merge"
    #: 候補 0 件（プロフィールで「名刺未取り込み→アップロード誘導」を出す状態）。
    #: ホームは 0 件で非表示のため、partial 側を show_no_candidate フラグで抑止する。
    NO_CANDIDATE = "no_candidate"
