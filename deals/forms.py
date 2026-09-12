from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from deals.models import Deal, DealPerson, DealUser, LeadSource, PersonRole, Stage, UserRole
from persons.models import Person

User = get_user_model()


class CommaDecimalField(forms.DecimalField):
    """カンマ付き数値を許容するDecimalField。"""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return super().to_python(value)


class DealAmountCleanMixin:
    """金額フィールドのカンマ除去およびDecimal正規化を行うMixin。"""

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if isinstance(amount, str):
            amount = amount.replace(",", "").strip()
            if not amount:
                return None
            return Decimal(amount)
        return amount


class DealForm(DealAmountCleanMixin, forms.ModelForm):
    """案件新規作成用フォーム（仕様書 v1.5 §2.1, §0.16）。"""

    amount = CommaDecimalField(
        required=False,
        max_digits=12,
        decimal_places=0,
        min_value=0,
        label="金額（円）",
        widget=forms.TextInput(attrs={"class": "app-input", "inputmode": "numeric", "placeholder": "金額（円）"}),
    )

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
        labels = {
            "name": "案件名",
            "primary_person": "相手方主担当（パーソン）",
            "company": "会社名",
            "owner": "社内担当者",
            "stage": "ステージ",
            "probability": "確度（%）",
            "deal_type": "商談種別",
            "amount": "金額（円）",
            "expected_close_date": "成約目標日",
            "lead_source": "流入経路",
            "source_campaign": "流入元キャンペーン",
            "memo": "メモ",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input", "placeholder": "案件名を入力"}),
            "primary_person": forms.Select(attrs={"class": "app-select app-input"}),
            "company": forms.Select(attrs={"class": "app-select app-input"}),
            "owner": forms.Select(attrs={"class": "app-select app-input"}),
            "stage": forms.Select(attrs={"class": "app-select app-input"}),
            "probability": forms.NumberInput(attrs={"class": "app-input", "min": 0, "max": 100, "placeholder": "0〜100"}),
            "deal_type": forms.Select(attrs={"class": "app-select app-input"}),
            "amount": forms.NumberInput(attrs={"class": "app-input", "min": 0, "placeholder": "金額（円）"}),
            "expected_close_date": forms.DateInput(attrs={"class": "app-input app-input--date", "type": "date"}),
            "lead_source": forms.Select(attrs={"class": "app-select app-input"}),
            "source_campaign": forms.Select(attrs={"class": "app-select app-input"}),
            "memo": forms.Textarea(attrs={"class": "app-textarea app-input", "rows": 4}),
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


class DealCreateForm(DealAmountCleanMixin, forms.ModelForm):
    """案件新規起票専用フォーム（スリム化：10項目）。"""

    amount = CommaDecimalField(
        required=False,
        max_digits=12,
        decimal_places=0,
        min_value=0,
        label="金額（円）",
        widget=forms.TextInput(attrs={"class": "app-input", "inputmode": "numeric", "placeholder": "金額（円）"}),
    )

    class Meta:
        model = Deal
        fields = [
            "name",
            "company",
            "owner",
            "stage",
            "amount",
            "expected_close_date",
            "probability",
            "deal_type",
            "lead_source",
            "source_campaign",
            "memo",
        ]
        labels = {
            "name": "案件名",
            "company": "会社名",
            "owner": "社内担当者",
            "stage": "ステージ",
            "amount": "金額（円）",
            "expected_close_date": "成約目標日",
            "probability": "確度（%）",
            "deal_type": "商談種別",
            "lead_source": "流入経路",
            "source_campaign": "流入元キャンペーン",
            "memo": "メモ",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input", "placeholder": "案件名を入力"}),
            "company": forms.HiddenInput(),
            "owner": forms.Select(attrs={"class": "app-select app-input"}),
            "stage": forms.Select(attrs={"class": "app-select app-input"}),
            "expected_close_date": forms.DateInput(attrs={"class": "app-input app-input--date", "type": "date"}),
            "probability": forms.NumberInput(attrs={"class": "app-input", "min": 0, "max": 100, "placeholder": "0〜100"}),
            "deal_type": forms.Select(attrs={"class": "app-select app-input"}),
            "lead_source": forms.Select(attrs={"class": "app-select app-input"}),
            "source_campaign": forms.Select(attrs={"class": "app-select app-input"}),
            "memo": forms.Textarea(attrs={"class": "app-textarea app-input", "rows": 4}),
        }

    def clean(self):
        cleaned_data = super().clean()
        source_campaign = cleaned_data.get("source_campaign")
        lead_source = cleaned_data.get("lead_source")
        if source_campaign and not lead_source:
            cleaned_data["lead_source"] = LeadSource.CAMPAIGN
        return cleaned_data


class DealUpdateForm(DealAmountCleanMixin, forms.ModelForm):
    """案件通常編集用フォーム（仕様書 v1.5 §2.6, §0.15）。
    ※ owner, primary_person, is_archived は除外。
    """

    amount = CommaDecimalField(
        required=False,
        max_digits=12,
        decimal_places=0,
        min_value=0,
        label="金額（円）",
        widget=forms.TextInput(attrs={"class": "app-input", "inputmode": "numeric", "placeholder": "金額（円）"}),
    )

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
        labels = {
            "name": "案件名",
            "company": "会社名",
            "stage": "ステージ",
            "probability": "確度（%）",
            "deal_type": "商談種別",
            "amount": "金額（円）",
            "expected_close_date": "成約目標日",
            "lead_source": "流入経路",
            "source_campaign": "流入元キャンペーン",
            "memo": "メモ",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input"}),
            "company": forms.HiddenInput(),
            "stage": forms.Select(attrs={"class": "app-select app-input"}),
            "probability": forms.NumberInput(attrs={"class": "app-input", "min": 0, "max": 100}),
            "deal_type": forms.Select(attrs={"class": "app-select app-input"}),
            "expected_close_date": forms.DateInput(attrs={"class": "app-input app-input--date", "type": "date"}),
            "lead_source": forms.Select(attrs={"class": "app-select app-input"}),
            "source_campaign": forms.Select(attrs={"class": "app-select app-input"}),
            "memo": forms.Textarea(attrs={"class": "app-textarea app-input", "rows": 4}),
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
        widget=forms.Select(attrs={"class": "app-select app-input"}),
        label="クローズ種別",
    )
    closed_at = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "app-input app-input--date", "type": "date"}),
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
        widget=forms.Select(attrs={"class": "app-select app-input"}),
        label="新担当者（社内）",
    )


class DealReassignPrimaryPersonForm(forms.Form):
    """案件主担当者（相手方Person）付け替えフォーム（仕様書 v1.5 §2.6.1）。"""

    new_person = forms.ModelChoiceField(
        queryset=Person.objects.all(),
        widget=forms.Select(attrs={"class": "app-select app-input"}),
        label="新主担当者（相手方）",
    )


class DealPersonForm(forms.ModelForm):
    """案件関係者（社外）追加フォーム。"""

    class Meta:
        model = DealPerson
        fields = ["person", "role", "memo"]
        labels = {
            "person": "相手方関係者（パーソン）",
            "role": "役割",
            "memo": "関係メモ",
        }
        widgets = {
            "person": forms.Select(attrs={"class": "app-select app-input"}),
            "role": forms.Select(attrs={"class": "app-select app-input"}),
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "関係性・役割メモ"}),
        }


class DealUserForm(forms.ModelForm):
    """案件担当者（社内）追加フォーム。"""

    can_edit = forms.BooleanField(
        required=False,
        initial=True,
        label="編集を許可する",
        widget=forms.CheckboxInput(attrs={"class": "app-radio-toggle"}),
    )

    class Meta:
        model = DealUser
        fields = ["user", "role", "can_edit", "memo"]
        labels = {
            "user": "社内担当者",
            "role": "担当役割",
            "can_edit": "編集を許可する",
            "memo": "担当メモ",
        }
        widgets = {
            "user": forms.Select(attrs={"class": "app-select app-input"}),
            "role": forms.Select(attrs={"class": "app-select app-input"}),
            "memo": forms.TextInput(attrs={"class": "app-input", "placeholder": "社内役割・担当メモ"}),
        }

