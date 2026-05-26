"""tags アプリの Form 層（仕様書 v1.6 §6.2.5 / §5.1 No.20〜24）。"""

from django import forms

from .models import Tag, TagCategory


class _AppInputMixin:
    """[性質] presentation 層。widget に app-input クラスを付与する共通処理。"""

    def _apply_widget_classes(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            css = widget.attrs.get("class", "")
            if "app-input" not in css.split():
                widget.attrs["class"] = (css + " app-input").strip()


class TagCategoryForm(_AppInputMixin, forms.ModelForm):
    """タグカテゴリ作成・編集フォーム（§6.2.5）。"""

    class Meta:
        model = TagCategory
        fields = ["name", "description", "sort_order"]
        labels = {
            "name": "カテゴリ名",
            "description": "説明",
            "sort_order": "並び順",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_widget_classes()


class TagForm(_AppInputMixin, forms.ModelForm):
    """タグ作成・編集フォーム（§5.1 No.21 / No.23）。

    category は必須 FK（§4.9A）。category 選択肢は is_archived=False のみ。
    """

    class Meta:
        model = Tag
        fields = ["name", "category", "description"]
        labels = {
            "name": "タグ名",
            "category": "カテゴリ",
            "description": "説明",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = TagCategory.objects.filter(
            is_archived=False
        ).order_by("sort_order", "name")
        self._apply_widget_classes()
