"""django-auth-ldap シグナルフック（仕様書 §5.4）。"""

from django.dispatch import receiver
from django_auth_ldap.backend import populate_user

from .ldap_sync import sync_ldap_groups, sync_ldap_user


@receiver(populate_user)
def on_populate_user(sender, user, ldap_user, **kwargs):
    """django-auth-ldap が User を作成・取得した直後に発火するシグナル。

    user は未 save の状態。sync_ldap_user 内で save される。
    sync_ldap_groups は user.save() 後に呼ぶ必要があるため、save 後に呼ぶ。
    """
    sync_ldap_user(user, ldap_user)
    sync_ldap_groups(user, ldap_user)
