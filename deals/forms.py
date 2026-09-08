from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from deals.models import Deal, DealPerson, DealUser, LeadSource, PersonRole, Stage, UserRole
from persons.models import Person

User = get_user_model()


class DealForm(forms.ModelForm):
    """案件新規作成用フォーム（仕様書 v1.5 §2.1, §0.16）。"""

    class Meta:
        model = Deal
        fields = [
            "name",
            "primary_person",
            "company",
            "owner",
            "stage",
            "probability",
            "deal_type",
            "amount",
            "expected_close_date",
            "lead_source",
            "source_campaign",
            "memo",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input", "placeholder": "案件名を入力"}),
            "primary_person": forms.Select(attrs={"class": "app-select"}),
            "company": forms.Select(attrs={"class": "app-select"}),
            "owner": forms.Select(attrs={"class": "app-select"}),
            "stage": forms.Select(attrs={"class": "app-select"}),
            "probability": forms.NumberInput(attrs={"class": "app-input", "min": 0, "max": 100, "placeholder": "0〜100"}),
            "deal_type": forms.Select(attrs={"class": "app-select"}),
            "amount": forms.NumberInput(attrs={"class": "app-input", "min": 0, "placeholder": "金額（円）"}),
            "expected_close_date": forms.DateInput(attrs={"class": "app-input", "type": "date"}),
            "lead_source": forms.Select(attrs={"class": "app-select"}),
            "source_campaign": forms.Select(attrs={"class": "app-select"}),
            "memo": forms.Textarea(attrs={"class": "app-textarea", "rows": 4}),
        }

    def clean(self):
        cleaned_data = super().clean()
        primary_person = cleaned_data.get("primary_person")
        company = cleaned_data.get("company")
        source_campaign = cleaned_data.get("source_campaign")
        lead_source = cleaned_data.get("lead_source")

        # primary_person選択時、companyが空なら主コンタクトの会社を自動補完
        if primary_person and not company:
            primary_contact = getattr(primary_person, "primary_contact", None)
            if primary_contact and primary_contact.company:
                cleaned_data["company"] = primary_contact.company

        # source_campaign指定時、lead_sourceが空ならCAMPAIGNを自動補完
        if source_campaign and not lead_source:
            cleaned_data["lead_source"] = LeadSource.CAMPAIGN

        return cleaned_data


class DealUpdateForm(forms.ModelForm):
    """案件通常編集用フォーム（仕様書 v1.5 §2.6, §0.15）。
    ※ owner, primary_person, is_archived は除外。
    """

    class Meta:
        model = Deal
        fields = [
            "name",
            "company",
            "stage",
            "probability",
            "deal_type",
            "amount",
            "expected_close_date",
            "lead_source",
            "source_campaign",
            "memo",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input"}),
            "company": forms.Select(attrs={"class": "app-select"}),
            "stage": forms.Select(attrs={"class": "app-select"}),
            "probability": forms.NumberInput(attrs={"class": "app-input", "min": 0, "max": 100}),
            "deal_type": forms.Select(attrs={"class": "app-select"}),
            "amount": forms.NumberInput(attrs={"class": "app-input", "min": 0}),
            "expected_close_date": forms.DateInput(attrs={"class": "app-input", "type": "date"}),
            "lead_source": forms.Select(attrs={"class": "app-select"}),
            "source_campaign": forms.Select(attrs={"class": "app-select"}),
            "memo": forms.Textarea(attrs={"class": "app-textarea", "rows": 4}),
        }

    def clean(self):
        cleaned_data = super().clean()
        source_campaign = cleaned_data.get("source_campaign")
        lead_source = cleaned_data.get("lead_source")

        if source_campaign and not lead_source:
            cleaned_data["lead_source"] = LeadSource.CAMPAIGN

        return cleaned_data


class DealCloseForm(forms.Form):
    """案件クローズ（受注/失注）専用フォーム（仕様書 v1.5 §2.5）。"""

    stage = forms.ChoiceField(
        choices=[(Stage.WON, "受注"), (Stage.LOST, "失注")],
        widget=forms.Select(attrs={"class": "app-select"}),
        label="クローズ種別",
    )
    closed_at = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "app-input", "type": "date"}),
        label="成約・失注確定日",
        help_text="空欄の場合は本日の日付が設定されます",
    )
    lost_reason = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "app-input", "placeholder": "失注理由を入力（失注時は必須）"}),
        label="失注理由",
    )

    def clean(self):
        cleaned_data = super().clean()
        stage = cleaned_data.get("stage")
        closed_at = cleaned_data.get("closed_at")
        lost_reason = cleaned_data.get("lost_reason")

        if not closed_at:
            cleaned_data["closed_at"] = timezone.localdate()
        elif closed_at > timezone.localdate():
            self.add_error("closed_at", "成約確定日に未来日は指定できません。")

        if stage == Stage.LOST and not (lost_reason and lost_reason.strip()):
            self.add_error("lost_reason", "失注時は失注理由の入力が必須です。")
        elif stage == Stage.WON:
            cleaned_data["lost_reason"] = ""

        return cleaned_data


class DealReassignOwnerForm(forms.Form):
    """案件担当者（owner）付け替えフォーム（仕様書 v1.5 §2.6）。"""

    new_owner = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        widget=forms.Select(attrs={"class": "app-select"}),
        label="新担当者（社内）",
    )


class DealReassignPrimaryPersonForm(forms.Form):
    """案件主担当者（相手方Person）付け替えフォーム（仕様書 v1.5 §2.6.1）。"""

    new_person = forms.ModelChoiceField(
        queryset=Person.objects.all(),
        widget=forms.Select(attrs={"class": "app-select"}),
        label="新主担当者（相手方）",
    )


class DealPersonForm(forms.ModelForm):
    """案件関係者（社外）追加フォーム。"""

    class Meta:
        model = DealPerson
        fields = ["person", "role", "memo"]
        widgets = {
            "person": forms.Select(attrs={"class": "app-select"}),
            "role": forms.Select(attrs={"class": "app-select"}),
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "役割・関係メモ"}),
        }


class DealUserForm(forms.ModelForm):
    """案件担当者（社内）追加フォーム。"""

    class Meta:
        model = DealUser
        fields = ["user", "role", "memo"]
        widgets = {
            "user": forms.Select(attrs={"class": "app-select"}),
            "role": forms.Select(attrs={"class": "app-select"}),
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "担当役割メモ"}),
        }
