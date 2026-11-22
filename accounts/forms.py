from django import forms
from django.contrib.auth.models import Group

from .models import Role, SecuritySetting


class RoleCreateForm(forms.ModelForm):
    """ロール新規作成フォーム（code は Service 側で name から自動採番するため入力させない）。"""

    sort_order = forms.IntegerField(
        required=False, initial=0,
        widget=forms.NumberInput(attrs={"class": "app-input"}),
    )

    class Meta:
        model = Role
        fields = ["name", "sort_order", "memo"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input"}),
            "memo": forms.Textarea(attrs={"class": "app-input", "rows": 3}),
        }


class RoleUpdateForm(forms.ModelForm):
    """ロール編集フォーム（表示名 ＋ default_groups を同一画面で編集。code は read-only）。"""

    sort_order = forms.IntegerField(
        required=False, initial=0,
        widget=forms.NumberInput(attrs={"class": "app-input"}),
    )
    default_groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all().order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="既定グループ（default_groups）",
    )

    class Meta:
        model = Role
        fields = ["name", "sort_order", "memo", "default_groups"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input"}),
            "memo": forms.Textarea(attrs={"class": "app-input", "rows": 3}),
        }


class UserSignatureForm(forms.ModelForm):
    """ユーザ署名（CustomUser.signature）編集フォーム。"""

    class Meta:
        from .models import CustomUser

        model = CustomUser
        fields = ["signature"]
        labels = {
            "signature": "署名（差出人の署名）",
        }
        help_texts = {
            "signature": "メール送信時の署名（プレーンテキスト、HTML タグ不可）。メール配信時に末尾に差し込まれます。",
        }
        widgets = {
            "signature": forms.Textarea(
                attrs={
                    "class": "app-input",
                    "rows": 8,
                    "placeholder": "例:\n--\n山田 太郎\nFreeGroup 株式会社\nTEL: 03-1234-5678\nemail: y.yamada@example.com",
                    "style": "min-height: 200px; line-height: 1.6;",
                }
            ),
        }


class SecuritySettingForm(forms.Form):
    """ログインセキュリティ設定フォーム（仕様書 v1.3.1 §8.1）。"""

    two_factor_enforcement = forms.ChoiceField(
        label="二段階認証強制レベル",
        choices=SecuritySetting.TwoFactorEnforcement.choices,
        widget=forms.Select(attrs={"class": "app-input"}),
    )
    login_notification_level = forms.ChoiceField(
        label="ログイン通知レベル",
        choices=SecuritySetting.LoginNotificationLevel.choices,
        widget=forms.Select(attrs={"class": "app-input"}),
    )
    lockout_threshold = forms.IntegerField(
        label="ロック失敗回数（3〜10回）",
        min_value=3,
        max_value=10,
        widget=forms.NumberInput(attrs={"class": "app-input", "min": 3, "max": 10}),
    )
    lockout_minutes = forms.IntegerField(
        label="ロック時間（分、5〜1440分）",
        min_value=5,
        max_value=1440,
        widget=forms.NumberInput(attrs={"class": "app-input", "min": 5, "max": 1440}),
    )
    ip_block_threshold = forms.IntegerField(
        label="IP遮断失敗回数（10〜100回）",
        min_value=10,
        max_value=100,
        widget=forms.NumberInput(attrs={"class": "app-input", "min": 10, "max": 100}),
    )
    trusted_device_days = forms.IntegerField(
        label="信頼期間（日、0〜90日・0で無効）",
        min_value=0,
        max_value=90,
        widget=forms.NumberInput(attrs={"class": "app-input", "min": 0, "max": 90}),
    )
    notify_admins_on_account_lock = forms.BooleanField(
        label="アカウントロック発生を管理者に通知する",
        required=False,
    )
    notify_admins_on_ip_block = forms.BooleanField(
        label="IP遮断発生を管理者に通知する",
        required=False,
    )


class SelfPasswordChangeForm(forms.Form):
    """本人によるパスワード変更フォーム（Step 3 サービス層へ全判定・保存を委譲、§5.2 / §13-17）。

    現在のパスワードが空でも必須チェックで止めずにサービス層へ渡して失敗計上するため、
    全入力欄を required=False としている。
    """

    old_password = forms.CharField(
        label="現在のパスワード",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "app-input", "autocomplete": "current-password"}
        ),
    )
    new_password1 = forms.CharField(
        label="新しいパスワード",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "app-input", "autocomplete": "new-password"}
        ),
    )
    new_password2 = forms.CharField(
        label="新しいパスワード（確認）",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "app-input", "autocomplete": "new-password"}
        ),
    )

    def __init__(self, user=None, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class SelfPasswordResetRequestForm(forms.Form):
    """パスワードリセット申請フォーム（空入力も含め Step 3 サービス層へ委譲、§6.2 / §13-16）。"""

    email = forms.CharField(
        label="メールアドレス",
        required=False,
        strip=False,
        widget=forms.TextInput(
            attrs={
                "class": "app-input",
                "autocomplete": "email",
                "inputmode": "email",
                "autofocus": True,
            }
        ),
    )


class SelfPasswordResetConfirmForm(forms.Form):
    """パスワード再設定フォーム（Step 3 サービス層へ検証・保存を委譲、§6.4 / §13-15）。"""

    new_password1 = forms.CharField(
        label="新しいパスワード",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "app-input", "autocomplete": "new-password"}
        ),
    )
    new_password2 = forms.CharField(
        label="新しいパスワード（確認）",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "app-input", "autocomplete": "new-password"}
        ),
    )

    def __init__(self, user=None, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

