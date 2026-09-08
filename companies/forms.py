from django import forms
from django.core.exceptions import ValidationError

from companies.models import Company


class CompanyForm(forms.ModelForm):
    """会社新規作成・編集フォーム（仕様書 v1.5 第6章）。
    ※ status, merged_into は除外。
    """

    class Meta:
        model = Company
        fields = ["organization", "domain", "phone", "address", "website"]
        widgets = {
            "organization": forms.TextInput(attrs={"class": "app-input", "placeholder": "会社名・組織名"}),
            "domain": forms.TextInput(attrs={"class": "app-input", "placeholder": "example.com"}),
            "phone": forms.TextInput(attrs={"class": "app-input", "placeholder": "03-1234-5678"}),
            "address": forms.TextInput(attrs={"class": "app-input", "placeholder": "東京都千代田区..."}),
            "website": forms.URLInput(attrs={"class": "app-input", "placeholder": "https://example.com"}),
        }


class CompanyMergeConfirmForm(forms.Form):
    """会社統合（マージ）確認フォーム（仕様書 v1.5 §6.5.5）。"""

    surviving_company_id = forms.UUIDField(widget=forms.HiddenInput())
    target_company_ids = forms.CharField(
        widget=forms.HiddenInput(),
        help_text="カンマ区切りの統合対象Company UUIDリスト",
    )

    def clean_target_company_ids(self):
        raw_ids = self.cleaned_data.get("target_company_ids", "")
        ids = [i.strip() for i in raw_ids.split(",") if i.strip()]
        if not ids:
            raise ValidationError("統合対象の会社が選択されていません。")
        return ids
