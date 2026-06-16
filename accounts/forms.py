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
