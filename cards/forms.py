"""cards アプリの Form 層。

- UploadForm（仕様書 v1.1.0 §3.1.2）：services.image_processor.validate_image を
  clean_image() で呼び出し、拡張子・サイズ検証をフォームレベルで実施する。
- BusinessCardEditForm（v1.7）：名刺編集画面（CardEditView）用。orientation /
  error_message の手動編集に使う ModelForm。
"""

from django import forms

from .models import BusinessCard
from .services.image_processor import validate_image


class UploadForm(forms.Form):
    image = forms.ImageField(required=True, label="名刺画像")

    def clean_image(self):
        uploaded_file = self.cleaned_data["image"]
        validate_image(uploaded_file)
        return uploaded_file


class BusinessCardEditForm(forms.ModelForm):
    """名刺（BusinessCard）編集フォーム（v1.7、CardEditView 用）。

    [性質] presentation 層クラス（DB 操作なし・副作用なし）

    編集対象は orientation（別表 C.2 の 5 値）と error_message のみ。ocr_result /
    ocr_status / claimed_at の終端化は View 側（「名刺でない」設定）の責務で本フォームは
    扱わない。widget には既存 BEM クラス app-input を付与する（CLAUDE.md §7）。
    """

    class Meta:
        model = BusinessCard
        fields = ["orientation", "error_message"]
        widgets = {
            "orientation": forms.Select(attrs={"class": "app-input"}),
            "error_message": forms.Textarea(attrs={"class": "app-input", "rows": 3}),
        }
        labels = {
            "orientation": "向き（OSD/OCR 判定）",
            "error_message": "エラーメッセージ（メモ）",
        }
