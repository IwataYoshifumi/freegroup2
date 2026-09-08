from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError

from attachments.models import Attachment

MAX_ATTACHMENT_SIZE = 15 * 1024 * 1024  # 15MB
BLOCKED_EXTENSIONS = {
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".scr",
    ".js",
    ".vbs",
    ".ps1",
    ".jar",
    ".msi",
    ".dll",
}


class AttachmentUploadForm(forms.ModelForm):
    """添付ファイルアップロード用フォーム（仕様書 v1.5 §4.1, §9.1）。"""

    class Meta:
        model = Attachment
        fields = ["file", "memo"]
        widgets = {
            "file": forms.FileInput(attrs={"class": "app-input"}),
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "メモ・備考（任意）"}),
        }

    def clean_file(self):
        uploaded_file = self.cleaned_data.get("file")
        if not uploaded_file:
            return uploaded_file

        if uploaded_file.size > MAX_ATTACHMENT_SIZE:
            raise ValidationError("ファイルサイズは15MB以下にしてください。")

        ext = Path(uploaded_file.name).suffix.lower()
        if ext in BLOCKED_EXTENSIONS:
            raise ValidationError("このファイル形式はセキュリティ上の理由によりアップロードできません。")

        return uploaded_file


class AttachmentMemoUpdateForm(forms.ModelForm):
    """添付ファイルメモ更新専用フォーム（仕様書 v1.5 §4.4.4）。"""

    class Meta:
        model = Attachment
        fields = ["memo"]
        widgets = {
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "メモ・備考"}),
        }
