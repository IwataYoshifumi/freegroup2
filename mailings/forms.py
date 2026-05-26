"""mailings アプリの Form 層（仕様書 v1.6 §6.2.4 / §4.13）。

MailingListForm: name / description のみ。タグ選択は別 UI（テンプレ + AJAX）で扱う。
MailingConfigForm: シングルトン MailingConfig の編集フォーム。
"""

from django import forms
from django.core.exceptions import ValidationError

from .models import MailingConfig, MailingList


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
