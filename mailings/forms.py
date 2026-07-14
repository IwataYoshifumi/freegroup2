"""mailings アプリの Form 層（仕様書 v1.6 §6.2.4 / §4.13）。

MailingListForm: name / description のみ。タグ選択は別 UI（テンプレ + AJAX）で扱う。
MailingConfigForm: シングルトン MailingConfig の編集フォーム。
"""

from django import forms
from django.core.exceptions import ValidationError

from .models import Campaign, EmailTemplate, MailingConfig, MailingList


class _AppInputMixin:
    """[性質] presentation 層。widget に app-input クラスを付与する共通処理。"""

    def _apply_widget_classes(self):
        for field in self.fields.values():
            widget = field.widget
            # チェックボックス・ラジオ・複数選択は app-input を付けると <input> 側に
            # 適用されて形が崩れる（ラベル幅一杯のボックス化など）。専用クラスを
            # 個別 widgets= に直書きする運用に揃える。
            if isinstance(
                widget,
                (
                    forms.CheckboxInput,
                    forms.RadioSelect,
                    forms.CheckboxSelectMultiple,
                ),
            ):
                continue
            css = widget.attrs.get("class", "")
            if "app-input" not in css.split():
                widget.attrs["class"] = (css + " app-input").strip()


class MailingListForm(_AppInputMixin, forms.ModelForm):
    """配信リスト本体（name / description）のフォーム（§4.11）。

    タグ選択・プレビュー・凍結は別 UI（テンプレ + AJAX）で扱うため、本フォームには含めない。
    """

    class Meta:
        model = MailingList
        fields = ["name", "description"]
        labels = {
            "name": "リスト名",
            "description": "説明",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_widget_classes()


class MailingConfigForm(_AppInputMixin, forms.ModelForm):
    """シングルトン MailingConfig の編集フォーム（§4.13）。

    必須：company_name / company_address / unsubscribe_contact（特電法フッター必須情報）。
    任意：DKIM 系 3 つ / メルマガ系 2 つ。
    条件付き検証：default_newsletter_sender_email を入れた場合のみ
    ドメインが dkim_domain 配下かを validate（両者空なら検証スキップ、§4.13 rev6 補足）。
    Phase 8 で本格的な DKIM 真贋検証は実装予定。
    """

    class Meta:
        model = MailingConfig
        fields = [
            "company_name",
            "company_address",
            "unsubscribe_contact",
            "dkim_domain",
            "dkim_selector",
            "dkim_private_key",
            "default_newsletter_sender_email",
            "default_newsletter_sender_name",
        ]
        labels = {
            "company_name": "会社名（必須）",
            "company_address": "会社住所（必須）",
            "unsubscribe_contact": "配信停止連絡先（必須、メアド / 電話 / URL）",
            "dkim_domain": "DKIM 署名対象ドメイン",
            "dkim_selector": "DKIM セレクタ",
            "dkim_private_key": "DKIM 秘密鍵",
            "default_newsletter_sender_email": "メルマガ方式の既定差出人アドレス",
            "default_newsletter_sender_name": "メルマガ方式の既定差出人名",
        }
        widgets = {
            "company_address": forms.Textarea(attrs={"rows": 3}),
            "dkim_private_key": forms.Textarea(
                attrs={"rows": 8, "placeholder": "Phase 8 で管理コマンドが生成"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 必須フィールドのマーキング（特電法フッター必須情報、§4.13）
        for required_field in ("company_name", "company_address", "unsubscribe_contact"):
            self.fields[required_field].required = True
        # DKIM / メルマガ系は任意
        for optional_field in (
            "dkim_domain",
            "dkim_selector",
            "dkim_private_key",
            "default_newsletter_sender_email",
            "default_newsletter_sender_name",
        ):
            self.fields[optional_field].required = False
        self._apply_widget_classes()

    def clean(self):
        """条件付き検証：default_newsletter_sender_email が入力されている場合、
        ドメインが dkim_domain 配下かをチェック（§4.13 rev6 補足）。両者空なら検証スキップ。
        """
        cleaned = super().clean()
        sender_email = (cleaned.get("default_newsletter_sender_email") or "").strip()
        dkim_domain = (cleaned.get("dkim_domain") or "").strip().lower()
        if sender_email and dkim_domain:
            domain_part = sender_email.split("@", 1)[-1].strip().lower()
            if domain_part != dkim_domain and not domain_part.endswith(
                "." + dkim_domain
            ):
                self.add_error(
                    "default_newsletter_sender_email",
                    ValidationError(
                        f"差出人アドレスのドメイン '{domain_part}' が "
                        f"dkim_domain '{dkim_domain}' 配下ではありません。"
                    ),
                )
        return cleaned


class EmailTemplateForm(_AppInputMixin, forms.ModelForm):
    """EmailTemplate 編集フォーム（仕様書 §4.3 / §12.2、rev17/rev18 で 1:1 運用確定）。

    指示書 (c) §3.1：name は編集画面から除外（Campaign と 1:1 のため、別管理する
    業務上の意味がなく UI 混乱の元）。モデルフィールドとしての name は v1.7+
    ライブラリ復活の余地として残置（§19 論点23）。本フォームでは name を読み書きしない。
    """

    class Meta:
        model = EmailTemplate
        fields = ["subject", "body", "body_text", "body_text_is_manual"]
        labels = {
            "subject": "件名",
            "body": "本文（プレーンテキスト、HTML 不可。記法 3 種：差し込み変数 {{...}} / {% tracked_link %} / {% unsubscribe_link %}）",
            "body_text": "代替テキスト（手動編集モード時のみ手動値を使う、§4.3）",
            "body_text_is_manual": "代替テキストを手動値で使う",
        }
        widgets = {
            "body": forms.Textarea(attrs={"rows": 16}),
            "body_text": forms.Textarea(attrs={"rows": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_widget_classes()


class CampaignNameForm(_AppInputMixin, forms.ModelForm):
    """Campaign 新規作成・名称変更フォーム。"""

    class Meta:
        model = Campaign
        fields = ["name"]
        labels = {"name": "キャンペーン名"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = True
        self._apply_widget_classes()


class CampaignSenderForm(_AppInputMixin, forms.ModelForm):
    """Campaign 配信形式（送信元方式・差出人・配信停止フィルタ）編集フォーム。"""

    class Meta:
        model = Campaign
        fields = [
            "sender_mode",
            "sender_email",
            "sender_name",
            "apply_unsubscribe_filter",
        ]
        labels = {
            "sender_mode": "送信元方式",
            "sender_email": "差出人アドレス",
            "sender_name": "差出人名",
            "apply_unsubscribe_filter": "配信停止フィルタを適用する",
        }
        widgets = {
            "sender_mode": forms.RadioSelect(attrs={"class": "app-radio-toggle"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sender_mode"].choices = [
            ("creator", "メール作成者"),
            ("newsletter", "メルマガ"),
        ]
        
        self.fields["apply_unsubscribe_filter"].widget = forms.RadioSelect(
            choices=[(True, "適用する"), (False, "適用しない")],
            attrs={"class": "app-radio-toggle"},
        )
        self.fields["apply_unsubscribe_filter"].required = False

        self.fields["sender_email"].required = False
        self.fields["sender_name"].required = False

        config = MailingConfig.objects.first()
        if config:
            if not self.instance.sender_email and config.default_newsletter_sender_email:
                self.initial["sender_email"] = config.default_newsletter_sender_email
            if not self.instance.sender_name and config.default_newsletter_sender_name:
                self.initial["sender_name"] = config.default_newsletter_sender_name

        self._apply_widget_classes()


class CampaignScheduleForm(_AppInputMixin, forms.ModelForm):
    """Campaign スケジュール編集フォーム。"""

    class Meta:
        model = Campaign
        fields = ["scheduled_at"]
        labels = {"scheduled_at": "予約配信日時"}
        widgets = {
            "scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheduled_at"].required = False
        self._apply_widget_classes()
