from django.contrib.auth.models import AbstractUser, Group
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from .constants import AuthSource


class CustomUser(AbstractUser):
    role = models.ForeignKey(
        "Role",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )
    person = models.OneToOneField(
        "persons.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="user",
        help_text=(
            "ログイン中のユーザに対応する Person（任意）。"
            "1 User : 0..1 Person、Person 側も 1 User までに制限。"
            "紐付け運用の詳細は §12 を参照。"
        ),
    )
    auth_source = models.CharField(
        max_length=16,
        choices=AuthSource.choices,
        default=AuthSource.LOCAL,
    )
    department = models.ForeignKey(
        "Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )
    ldap_groups = models.ManyToManyField(
        "LdapGroup",
        blank=True,
        related_name="members",
        help_text="横断グループ（v1.5.0 では空運用も可）",
    )
    ldap_dn = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        unique=True,
        help_text="LDAP 同期時の突合キー（LDAP 由来ユーザのみ）",
    )

    class Meta:
        permissions = [
            ("link_user_to_person", "User と Person の紐付けを管理できる"),
            ("retire_user", "ユーザを退職処理できる"),
        ]


class Role(models.Model):
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="画面表示名（例: 管理者, 営業, 閲覧者）",
    )
    code = models.CharField(
        max_length=32,
        unique=True,
        help_text=(
            "プログラム判定用の安定キー（admin, sales, viewer）。"
            "作成後の変更不可。変更したい場合はマイグレーション必須"
        ),
    )
    memo = models.TextField(blank=True)
    sort_order = models.IntegerField(default=0)
    default_groups = models.ManyToManyField(
        "auth.Group",
        blank=True,
        related_name="default_for_roles",
        help_text="このRoleを付与した際に自動で入れるGroup",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class GroupProfile(models.Model):
    group = models.OneToOneField(
        Group,
        on_delete=models.CASCADE,
        related_name="profile",
        primary_key=True,
    )
    memo = models.TextField(blank=True)
    is_default = models.BooleanField(
        default=False,
        help_text="v1.5.0 では未使用。v1.6+ で運用方針確定",
    )
    auth_source = models.CharField(
        max_length=16,
        choices=AuthSource.choices,
        default=AuthSource.LOCAL,
    )
    ldap_dn = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        unique=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


@receiver(post_save, sender=Group)
def ensure_group_profile(sender, instance, created, **kwargs):
    if created:
        GroupProfile.objects.get_or_create(group=instance)


class Department(models.Model):
    code = models.CharField(max_length=64, unique=True, null=True, blank=True)
    name = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    auth_source = models.CharField(
        max_length=16,
        choices=AuthSource.choices,
        default=AuthSource.LOCAL,
    )
    ldap_dn = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        unique=True,
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["parent"]),
            models.Index(fields=["auth_source"]),
        ]
        ordering = ["sort_order", "name"]

    def descendants(self, include_self=False, _visited=None):
        """配下の部署を再帰取得（循環参照対策付き）。

        [性質] 準関数（DB 読み取り、副作用なし）
        [入力] include_self: bool / _visited: set | None（内部再帰用）
        [出力] list[Department]

        ⚠️ N+1 問題あり。v1.6+ で AccessList から本格利用する前に
        必ず django-mptt / treebeard / Recursive CTE に置換すること。
        v1.5.0 では LDAP 同期内・認証フロー・cron 処理での使用禁止。
        テストコード / Admin 画面（部署 10 件未満前提）でのみ使用可。
        """
        if _visited is None:
            _visited = set()
        if self.pk in _visited:
            return []
        _visited.add(self.pk)
        result = [self] if include_self else []
        for child in self.children.all():
            result.extend(child.descendants(include_self=True, _visited=_visited))
        return result

    def clean(self):
        parent = self.parent
        while parent:
            if parent.pk == self.pk:
                raise ValidationError("循環参照は禁止です")
            parent = parent.parent


class LdapGroup(models.Model):
    name = models.CharField(max_length=200, unique=True)
    memo = models.TextField(blank=True)
    auth_source = models.CharField(
        max_length=16,
        choices=AuthSource.choices,
        default=AuthSource.LOCAL,
    )
    ldap_dn = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        unique=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
