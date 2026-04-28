"""アップロードフォーム（仕様書 v1.1.0 §3.1.2）。

services.image_processor.validate_image を clean_image() で呼び出し、
拡張子・サイズ検証をフォームレベルで実施する。
"""

from django import forms

from .services.image_processor import validate_image


class UploadForm(forms.Form):
    image = forms.ImageField(required=True, label="名刺画像")

    def clean_image(self):
        uploaded_file = self.cleaned_data["image"]
        validate_image(uploaded_file)
        return uploaded_file
