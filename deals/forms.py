from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from deals.models import (
    Deal,
    DealList,
    DealPerson,
    DealUser,
    LeadSource,
    PersonRole,
    Stage,
    UserRole,
    get_or_create_default_deal_list,
)
from permissions.models import AccessList
from permissions.services import AccessListService
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


class SafeDealModelFormMixin:
    """Deal用ModelFormの安全処理Mixin。
    1. stage 選択肢をアクティブステージに限定する（クローズ済みの場合は既存ステージを維持）。
    2. Model.clean() がフォームに存在しないフィールド（closed_at, lost_reason等）のエラーを
       返した際に ValueError でクラッシュするのを防ぎ、stage またはノンフィールドエラーへ安全にマッピングする。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "stage" in self.fields:
            active_choices = Stage.active_choices()
            instance = getattr(self, "instance", None)
            if instance and instance.pk and instance.stage in [Stage.WON, Stage.LOST]:
                # すでにクローズ済みの案件の場合は、既存ステージを選択肢に含める
                self.fields["stage"].choices = [
                    (instance.stage, instance.get_stage_display())
                ] + [c for c in active_choices if c[0] != instance.stage]
            else:
                self.fields["stage"].choices = active_choices

    def clean_stage(self):
        stage = self.cleaned_data.get("stage")
        instance = getattr(self, "instance", None)
        # クローズ済みでステージを変更していない場合は許可
        if instance and instance.pk and instance.stage in [Stage.WON, Stage.LOST] and stage == instance.stage:
            return stage
        if stage in [Stage.WON, Stage.LOST]:
            raise ValidationError("受注・失注への変更は「案件クローズ」画面から行ってください。")
        return stage

    def _post_clean(self):
        opts = self._meta
        from django.forms.models import construct_instance
        try:
            self.instance = construct_instance(self, self.instance, opts.fields, opts.exclude)
        except ValidationError as e:
            self._update_errors(e)

        exclude = self._get_validation_exclusions()
        try:
            self.instance.full_clean(exclude=exclude, validate_unique=False)
        except ValidationError as e:
            # フォームに含まれないフィールドのエラーを安全にハンドリング
            cleaned_errors = {}
            for field, messages in e.message_dict.items():
                if field in self.fields:
                    cleaned_errors.setdefault(field, []).extend(messages)
                else:
                    # フォームにないフィールド（closed_at, lost_reason 等）のエラーは stage またはノンフィールドエラーへ
                    if field in ["closed_at", "lost_reason"] and "stage" in self.fields:
                        cleaned_errors.setdefault("stage", []).extend(messages)
                    else:
                        for msg in messages:
                            self.add_error(None, msg)
            if cleaned_errors:
                self._update_errors(ValidationError(cleaned_errors))

        if getattr(self, "_validate_unique", True):
            self.validate_unique()


class DealForm(SafeDealModelFormMixin, DealAmountCleanMixin, forms.ModelForm):
    """案件新規作成用フォーム（仕様書 v1.5 §2.1, §0.16, v1.6 §8.3）。"""

    deal_list = forms.ModelChoiceField(
        queryset=DealList.objects.all(),
        required=True,
        label="案件リスト",
        widget=forms.Select(attrs={"class": "app-select app-input"}),
    )
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
            "deal_list",
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
            "deal_list": "案件リスト",
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
            "deal_list": forms.Select(attrs={"class": "app-select app-input"}),
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

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        if user is not None:
            editable_ids = list(AccessListService.editable_deal_list_ids(user))
            self.fields["deal_list"].queryset = DealList.objects.filter(id__in=editable_ids)
            if not editable_ids:
                self.fields["deal_list"].help_text = (
                    "編集可能な案件リストがありません。管理者に権限付与を依頼してください。"
                )

    def clean_deal_list(self):
        deal_list = self.cleaned_data.get("deal_list")
        if not deal_list:
            raise ValidationError("案件リストを選択してください。")
        if self.user is not None and self.user.is_authenticated and not self.user.is_superuser:
            if deal_list.id not in AccessListService.editable_deal_list_ids(self.user):
                raise ValidationError("選択された案件リストへの編集権限がありません。")
        return deal_list

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


class DealCreateForm(SafeDealModelFormMixin, DealAmountCleanMixin, forms.ModelForm):
    """案件新規起票専用フォーム（スリム化：11項目）。"""

    deal_list = forms.ModelChoiceField(
        queryset=DealList.objects.all(),
        required=True,
        label="案件リスト",
        widget=forms.Select(attrs={"class": "app-select app-input"}),
    )
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
            "deal_list",
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
            "deal_list": "案件リスト",
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
            "deal_list": forms.Select(attrs={"class": "app-select app-input"}),
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

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        if user is not None:
            editable_ids = list(AccessListService.editable_deal_list_ids(user))
            self.fields["deal_list"].queryset = DealList.objects.filter(id__in=editable_ids)
            if not editable_ids:
                self.fields["deal_list"].help_text = (
                    "編集可能な案件リストがありません。管理者に権限付与を依頼してください。"
                )

    def clean_deal_list(self):
        deal_list = self.cleaned_data.get("deal_list")
        if not deal_list:
            raise ValidationError("案件リストを選択してください。")
        if self.user is not None and self.user.is_authenticated and not self.user.is_superuser:
            if deal_list.id not in AccessListService.editable_deal_list_ids(self.user):
                raise ValidationError("選択された案件リストへの編集権限がありません。")
        return deal_list

    def clean(self):
        cleaned_data = super().clean()
        source_campaign = cleaned_data.get("source_campaign")
        lead_source = cleaned_data.get("lead_source")
        if source_campaign and not lead_source:
            cleaned_data["lead_source"] = LeadSource.CAMPAIGN
        return cleaned_data


class DealListForm(forms.ModelForm):
    """案件リスト新規作成・編集用フォーム（仕様書 v1.6 §2.3.3, §8.3.3）。"""

    class Meta:
        model = DealList
        fields = ["name", "description", "access_list", "edit_scope"]
        labels = {
            "name": "案件リスト名",
            "description": "説明",
            "access_list": "アクセスリスト",
            "edit_scope": "編集範囲",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "app-input", "placeholder": "案件リスト名を入力"}),
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
        if self.instance and self.instance.pk and self.fields["access_list"].disabled:
            return self.instance.access_list

        access_list = self.cleaned_data.get("access_list")
        if not access_list:
            raise ValidationError("アクセスリストを選択してください。")
        return access_list


class DealUpdateForm(SafeDealModelFormMixin, DealAmountCleanMixin, forms.ModelForm):
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
        widget=forms.HiddenInput(),
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

