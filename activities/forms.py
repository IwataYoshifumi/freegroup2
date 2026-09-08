from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from activities.models import Activity, ActivityPerson, ActivityUser


class ActivityForm(forms.ModelForm):
    """活動記録フォーム（仕様書 v1.5 第3章）。"""

    class Meta:
        model = Activity
        fields = [
            "activity_type",
            "direction",
            "occurred_at",
            "user",
            "place",
            "memo",
            "deal",
            "campaign",
        ]
        widgets = {
            "activity_type": forms.Select(attrs={"class": "app-select"}),
            "direction": forms.Select(attrs={"class": "app-select"}),
            "occurred_at": forms.DateTimeInput(
                attrs={"class": "app-input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "user": forms.Select(attrs={"class": "app-select"}),
            "place": forms.TextInput(attrs={"class": "app-input", "placeholder": "訪問先・会議URL等"}),
            "memo": forms.Textarea(attrs={"class": "app-textarea", "rows": 4, "placeholder": "活動内容・議事録等"}),
            "deal": forms.Select(attrs={"class": "app-select"}),
            "campaign": forms.Select(attrs={"class": "app-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and "occurred_at" not in self.initial:
            self.initial["occurred_at"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def clean_occurred_at(self):
        occurred_at = self.cleaned_data.get("occurred_at")
        if occurred_at and occurred_at > timezone.now():
            raise ValidationError("未来日時の活動は記録できません。")
        return occurred_at


class ActivityPersonForm(forms.ModelForm):
    """活動関係者（社外パーソン）追加フォーム。"""

    class Meta:
        model = ActivityPerson
        fields = ["person", "role", "memo"]
        widgets = {
            "person": forms.Select(attrs={"class": "app-select"}),
            "role": forms.Select(attrs={"class": "app-select"}),
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "関係メモ"}),
        }


class ActivityUserForm(forms.ModelForm):
    """活動同席者（社内ユーザー）追加フォーム。"""

    class Meta:
        model = ActivityUser
        fields = ["user", "role", "memo"]
        widgets = {
            "user": forms.Select(attrs={"class": "app-select"}),
            "role": forms.Select(attrs={"class": "app-select"}),
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "担当役割メモ"}),
        }
