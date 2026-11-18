from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

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
    signature = models.TextField(
        blank=True,
        default="",
        help_text=(
            "メール送信時の署名（プレーンテキスト、HTML タグ不可）。"
            "v1.6 メール配信機能で末尾に挿入。仕様書 §4.14.1"
        ),
    )

    @property
    def display_name(self) -> str:
        """ユーザの画面表示名。
        1. 紐づく Person が存在する場合はその氏名（primary_contactのfull_nameまたは姓・名）
        2. それ以外で get_full_name() が空でなければその氏名
        3. それ以外は username
        """
        if self.person_id and self.person:
            pc = getattr(self.person, "primary_contact", None)
            if pc:
                if pc.full_name:
                    return pc.full_name
                name = f"{pc.last_name} {pc.first_name}".strip()
                if name:
                    return name
            if hasattr(self.person, "contact_set"):
                c = self.person.contact_set.first()
                if c:
                    if c.full_name:
                        return c.full_name
                    name = f"{c.last_name} {c.first_name}".strip()
                    if name:
                        return name
        full_name = self.get_full_name()
        if full_name:
            return full_name
        return self.username

    class Meta:
        permissions = [
            ("link_user_to_person", "User と Person の紐付けを管理できる"),
            ("retire_user", "ユーザを退職処理できる"),
            ("assign_role_to_user", "ユーザにロールを割り当てられる"),
            ("manage_role", "業務ロールを管理できる"),
            ("manage_security_setting", "セキュリティ設定を管理できる"),
            ("reset_two_factor", "二段階認証をリセットできる"),
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

    def __str__(self):
        return self.name


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


# ======================================================================
# ログインセキュリティ強化（仕様書 v1.3.1 §9）
# ======================================================================


class SecuritySetting(models.Model):
    """ログインセキュリティ設定（シングルトン id=1、仕様書 §8.1 / §9.1）。

    シングルトン制約は mailings.models.MailingConfig と同じ
    CheckConstraint と save() オーバーライドの二重防衛方式。
    """

    class TwoFactorEnforcement(models.TextChoices):
        OPTIONAL = "optional", "任意"
        ADMIN_ONLY = "admin_only", "管理者のみ必須"
        ALL = "all", "全員必須"

    class LoginNotificationLevel(models.TextChoices):
        EVERY = "every", "毎回"
        NEW_DEVICE_ONLY = "new_device_only", "新しい端末のみ"

    id = models.IntegerField(primary_key=True, default=1)
    two_factor_enforcement = models.CharField(
        max_length=20,
        choices=TwoFactorEnforcement.choices,
        default=TwoFactorEnforcement.OPTIONAL,
    )
    login_notification_level = models.CharField(
        max_length=20,
        choices=LoginNotificationLevel.choices,
        default=LoginNotificationLevel.EVERY,
    )
    lockout_threshold = models.IntegerField(
        default=5,
        validators=[MinValueValidator(3), MaxValueValidator(10)],
    )
    lockout_minutes = models.IntegerField(
        default=15,
        validators=[MinValueValidator(5), MaxValueValidator(1440)],
    )
    ip_block_threshold = models.IntegerField(
        default=20,
        validators=[MinValueValidator(10), MaxValueValidator(100)],
    )
    trusted_device_days = models.IntegerField(
        default=30,
        validators=[MinValueValidator(0), MaxValueValidator(90)],
    )
    notify_admins_on_account_lock = models.BooleanField(default=True)
    notify_admins_on_ip_block = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1),
                name="security_setting_singleton",
            ),
        ]
        verbose_name = "セキュリティ設定"
        verbose_name_plural = "セキュリティ設定"

    def save(self, *args, **kwargs):
        # シングルトン強制（CheckConstraint と二重防衛）
        self.id = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"SecuritySetting (id={self.id})"


class RecoveryCode(models.Model):
    """二段階認証リカバリーコード（仕様書 §6.3 / §9.2）。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
    )
    # 鍵付きハッシュ(HMAC-SHA256)。パスワード用ハッシュ方式は使わない
    code_hash = models.CharField(
        max_length=128,
        db_index=True,
        help_text="鍵付きハッシュ(HMAC-SHA256)。パスワード用ハッシュ方式は使わない",
    )
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "code_hash"],
                name="unique_recovery_code_per_user",
            ),
        ]
        verbose_name = "リカバリーコード"
        verbose_name_plural = "リカバリーコード"

    def __str__(self):
        status = "使用済み" if self.used_at else "未使用"
        return f"RecoveryCode ({self.user_id}, {status})"


class LoginDevice(models.Model):
    """端末記録（仕様書 §6.6 / §7.2 / §9.3）。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="login_devices",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    trusted_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = "端末記録"
        verbose_name_plural = "端末記録"

    def __str__(self):
        return f"LoginDevice ({self.user_id}, {self.last_seen_at:%Y-%m-%d %H:%M})"


class AccountLock(models.Model):
    """アカウントロック状態（仕様書 §5.1 / §5.4 / §9.4）。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_lock",
    )
    failure_count = models.IntegerField(default=0)
    last_failed_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["user"]
        verbose_name = "アカウントロック"
        verbose_name_plural = "アカウントロック"

    def __str__(self):
        return f"AccountLock ({self.user_id}, failures={self.failure_count})"


class LoginAttempt(models.Model):
    """ログイン試行記録（仕様書 §5.6 / §9.5）。"""

    class Result(models.TextChoices):
        SUCCESS = "success", "成功"
        PASSWORD_FAILED = "password_failed", "パスワード失敗"
        CODE_FAILED = "code_failed", "コード失敗"
        REJECTED_LOCKED = "rejected_locked", "ロック中拒否"
        REJECTED_IP_BLOCKED = "rejected_ip_blocked", "IP遮断拒否"

    class SecondFactor(models.TextChoices):
        TOTP = "totp", "認証アプリ"
        RECOVERY_CODE = "recovery_code", "リカバリーコード"
        TRUSTED_DEVICE = "trusted_device", "省略"
        NONE = "none", "未設定"

    created_at = models.DateTimeField(default=timezone.now)
    username_input = models.CharField(max_length=150, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="login_attempts",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    result = models.CharField(max_length=32, choices=Result.choices)
    second_factor = models.CharField(
        max_length=32,
        choices=SecondFactor.choices,
        blank=True,
        default="",
    )
    is_new_device = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["ip_address"]),
        ]
        verbose_name = "ログイン試行記録"
        verbose_name_plural = "ログイン試行記録"

    def __str__(self):
        return f"LoginAttempt ({self.username_input}, {self.result}, {self.created_at:%Y-%m-%d %H:%M})"
