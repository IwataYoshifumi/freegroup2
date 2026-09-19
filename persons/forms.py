from django import forms
from django.core.exceptions import ValidationError

from permissions.models import AccessList
from permissions.services import AccessListService
from persons.models import PersonList


class PersonListForm(forms.ModelForm):
    """パーソンリスト新規作成・編集用フォーム（仕様書 v1.6 §2.3.3, §8.3.3）。"""

    class Meta:
        model = PersonList
        fields = ["name", "description", "access_list", "edit_scope"]
        labels = {
            "name": "パーソンリスト名",
            "description": "説明",
            "access_list": "アクセスリスト",
            "edit_scope": "編集範囲",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input", "placeholder": "パーソンリスト名を入力"}),
            "description": forms.Textarea(attrs={"class": "app-textarea app-input", "rows": 4, "placeholder": "説明を入力（任意）"}),
            "access_list": forms.Select(attrs={"class": "app-select app-input"}),
            "edit_scope": forms.Select(attrs={"class": "app-select app-input"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        if user is not None:
            accessible_ids = list(AccessListService.accessible_access_list_ids(user))
            self.fields["access_list"].queryset = AccessList.objects.filter(id__in=accessible_ids)

            # v1.6 最重要ガード: Update画面での現在値保護 (§8.3.3 / §2.3.3)
            # 現在設定されているAccessListがaccessible_idsに含まれない場合、
            # ModelChoiceFieldによる意図しない別AccessListへの暗黙の書き換え事故を防ぐため、
            # 当該フィールドを読み取り専用（disabled=True）として描画・保持する。
            if self.instance and self.instance.pk and self.instance.access_list_id:
                if self.instance.access_list_id not in accessible_ids:
                    self.fields["access_list"].disabled = True
                    self.fields["access_list"].required = False
                    self.fields["access_list"].queryset = AccessList.objects.filter(
                        id=self.instance.access_list_id
                    )
                    self.fields["access_list"].help_text = (
                        "このリストのアクセスリストを変更するには、アクセスリスト管理権限を持つ人に依頼してください。"
                    )

    def clean_access_list(self):
        # disabled なフィールドは POST データに含まれない場合があるため、現在値を維持
        if self.instance and self.instance.pk and self.fields["access_list"].disabled:
            return self.instance.access_list

        access_list = self.cleaned_data.get("access_list")
        if not access_list:
            raise ValidationError("アクセスリストを選択してください。")
        return access_list
