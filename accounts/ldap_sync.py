"""LDAP 同期処理本体（仕様書 §5.4）。

django-auth-ldap の populate_user シグナル経由で呼ばれる関数群。
DB 書き込みのみで認証フローには関与しない（責務分離）。
"""

import logging

from django.core.exceptions import ValidationError

from .constants import AuthSource
from .models import CustomUser, Department, LdapGroup

logger = logging.getLogger(__name__)


def sync_ldap_user(user, ldap_user_info):
    """LDAP 認証成功時に呼ばれ、CustomUser の各フィールドを同期する（仕様書 §5.4）。

    [性質] 副作用あり（DB 書込：CustomUser / Department を upsert、user.save）
    [入力] user: CustomUser（django-auth-ldap が既に作成・取得済み、未 save の状態）
           ldap_user_info: django_auth_ldap.backend._LDAPUser（属性取得用）
    [出力] None
    [例外] ValidationError（同名 local ユーザが既存の場合、_check_username_collision）
    """
    _check_username_collision(user)

    is_new = user._state.adding

    if is_new:
        # 新規作成時のみ初期化
        user.auth_source = AuthSource.LDAP
        user.role = None  # 管理者承認待ち
        user.ldap_dn = ldap_user_info.dn
        user.set_unusable_password()  # ローカル認証経由のログインを防止（セキュリティ）
        # is_active は ldap_user_info の disabled 属性から判定
        user.is_active = _get_ldap_active_state(ldap_user_info, default=True)
    else:
        # 既存更新時: is_active は disabled 方向のみ同期（仕様書 §5.4）
        ldap_active = _get_ldap_active_state(ldap_user_info, default=None)
        if ldap_active is False:
            user.is_active = False
        # ldap_active is True / None なら user.is_active は触らない（退職処理を尊重）

    # email / name / department は新規・既存とも LDAP から反映（仕様書 §5.3 / §5.4）
    user.email = ldap_user_info.attrs.get("mail", [""])[0]
    user.first_name = ldap_user_info.attrs.get("givenName", [""])[0]
    user.last_name = ldap_user_info.attrs.get("sn", [""])[0]

    # Department 同期
    dept = sync_ldap_department(ldap_user_info)
    if dept is not None:
        user.department = dept

    user.save()


def sync_ldap_department(ldap_user_info):
    """LDAP user の department 属性から Department を upsert（仕様書 §5.4）。

    [性質] 副作用あり（DB 書込：Department を update_or_create）
    [入力] ldap_user_info: django_auth_ldap.backend._LDAPUser
    [出力] Department | None（department / departmentNumber 両方取得不可なら None）

    departmentNumber を優先キーとして突合、なければ name で突合（仕様書 §5.3）。
    """
    dept_name = ldap_user_info.attrs.get("department", [None])[0]
    dept_code = ldap_user_info.attrs.get("departmentNumber", [None])[0]
    if dept_name is None and dept_code is None:
        return None

    if dept_code:
        dept, _ = Department.objects.update_or_create(
            code=dept_code,
            defaults={
                "name": dept_name or dept_code,
                "auth_source": AuthSource.LDAP,
            },
        )
    else:
        dept, _ = Department.objects.update_or_create(
            name=dept_name,
            defaults={"auth_source": AuthSource.LDAP},
        )
    return dept


def sync_ldap_groups(user, ldap_user_info):
    """LDAP user の memberOf 属性から LdapGroup を upsert し M2M に反映（仕様書 §5.4）。

    [性質] 副作用あり（DB 書込：LdapGroup upsert + user.ldap_groups M2M set）
    [入力] user: CustomUser（既に save 済み）
           ldap_user_info: django_auth_ldap.backend._LDAPUser
    [出力] None

    auth.Group には触らない（v1.5.0 では memberOf は LdapGroup として独自管理、
    auth.Group は管理者が手動運用する、仕様書 §5.4）。
    """
    member_of = ldap_user_info.attrs.get("memberOf", [])
    ug_ids = []
    for dn in member_of:
        ug, _ = LdapGroup.objects.update_or_create(
            ldap_dn=dn,
            defaults={
                "name": _extract_cn_from_dn(dn),
                "auth_source": AuthSource.LDAP,
            },
        )
        ug_ids.append(ug.pk)
    user.ldap_groups.set(ug_ids)


def _check_username_collision(user):
    """[性質] 副作用あり（例外）／同名の auth_source='local' ユーザが既存なら ValidationError。"""
    if user._state.adding:
        if CustomUser.objects.filter(
            username=user.username,
            auth_source=AuthSource.LOCAL,
        ).exists():
            logger.error(
                "LDAP user creation blocked: username '%s' already exists as a local user.",
                user.username,
            )
            raise ValidationError(
                f"同名のローカルユーザが既に存在します: {user.username}"
            )


def _get_ldap_active_state(ldap_user_info, default=True):
    """[性質] 純関数／LDAP 属性から is_active 相当を判定。

    AD: userAccountControl の 0x2 (ACCOUNTDISABLE) ビット
    389DS: nsAccountLock = 'true' なら無効
    取得不可なら default を返す。
    """
    attrs = ldap_user_info.attrs
    if "userAccountControl" in attrs:
        uac = int(attrs["userAccountControl"][0])
        return not bool(uac & 0x2)
    if "nsAccountLock" in attrs:
        return attrs["nsAccountLock"][0].lower() != "true"
    return default


def _extract_cn_from_dn(dn):
    """[性質] 純関数／LDAP DN から CN 値を抽出（'cn=foo,ou=bar' → 'foo'）。"""
    for part in dn.split(","):
        if part.strip().lower().startswith("cn="):
            return part.split("=", 1)[1].strip()
    return dn
