from django import forms
from django.contrib.auth.models import Group

from .models import Role


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


class UserGroupForm(forms.ModelForm):
    """ユーザーグループ新規作成・編集用フォーム（仕様書 v1.6 §2.3.2）。"""

    class Meta:
        from .models import UserGroup

        model = UserGroup
        fields = ["name", "description"]
        labels = {
            "name": "グループ名",
            "description": "説明",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input", "placeholder": "グループ名を入力"}),
            "description": forms.Textarea(attrs={"class": "app-textarea app-input", "rows": 4, "placeholder": "説明を入力（任意）"}),
        }


