"""mailings アプリのモデル（仕様書 v1.6 §4.3 / §4.11 / §4.12 / §4.13、第11章）。

rev12 でリストを凍結スナップショット方式に確定。
発注書 §1.1：Settings → MailingConfig に改名（django.conf.settings との混同回避）。
発注書 §3-1：EmailTemplate.body_html → body に改名（rev9 でプレーンテキスト化と整合）。
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint

from persons.models import Person


class MailingList(models.Model):
    """リスト（仕様書 §4.11）。

    rev12 で凍結スナップショット方式に確定。配信時は MailingListMember を読むだけで
    抽出評価は行わない（§11.0 / §11.3）。extraction_snapshot は再抽出補助メタ情報で
    配信時には一切参照しない（参照すると動的評価に逆戻りするため、§11.4.3.1）。
    Phase 1 では extraction_snapshot は NULL or 空のまま（具体スキーマは §19 残論点）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    extraction_snapshot = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "抽出条件スナップショット（再抽出補助のみ、配信時は参照しない）。"
            "JSON 具体スキーマは §19 残論点、Phase 1 ではフィールド定義のみ（§11.4.3.1）"
        ),
    )
    members_frozen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="メンバー凍結時刻。リスト作成・再抽出時に更新する（§4.11）",
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "リスト"
        verbose_name_plural = "リスト"

    def __str__(self):
        return self.name


class MailingListMember(models.Model):
    """リストメンバー（仕様書 §4.12）。

    凍結された宛先の実体。リスト作成時に Person 集合を物理保存し、以後タグが変わっても
    メンバーは変わらない（§11.3.1）。Person マージ時は付け替えない（§9.4.1 / §11.7.2.1）。

    Phase 1 では凍結後の「対象外」操作（物理削除）のみ許可する：
      - メンバーを増やす方向 → 凍結思想に反するため実装しない
      - メンバーを減らす方向 → 凍結の精度を上げる方向、Phase 1 で許可（物理削除）
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailing_list = models.ForeignKey(
        MailingList,
        on_delete=models.CASCADE,
        related_name="members",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="mailing_list_memberships",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="凍結された時点（§4.12）",
    )

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["mailing_list", "person"],
                name="unique_mailing_list_person",
            ),
        ]
        verbose_name = "リストメンバー"
        verbose_name_plural = "リストメンバー"

    def __str__(self):
        return f"{self.mailing_list} ← {self.person}"


class EmailTemplate(models.Model):
    """メールテンプレート（仕様書 §4.3）。

    rev9 でユーザー入力はプレーンテキストのみに確定（HTML タグ・WYSIWYG なし、§1.1）。
    本文に書ける記法は 3 つだけ：差し込み変数 {{...}} / {% tracked_link %} /
    {% unsubscribe_link %}。配信時に EmailContext.prepare（Phase 3）が HTML 化する。

    発注書 §3-1：v1.6 仕様書の body_html フィールドを Phase 1 で body に改名
    （実態と命名を揃える、別表 §20.3 の改訂はジット君が後で対応）。
    Phase 1 ではモデル定義のみ、編集画面は Phase 3 で実装。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    subject = models.CharField(
        max_length=255,
        help_text="件名（プレーンテキスト、差し込み変数可、HTML 不可、§4.3）",
    )
    body = models.TextField(
        help_text=(
            "本文（プレーンテキスト）。差し込み変数 {{...}} と "
            "{% tracked_link %} / {% unsubscribe_link %} の 3 種記法のみ可。"
            "HTML タグは書かない（rev9 確定、§1.1）。Phase 1 で body_html → body に改名"
        ),
    )
    body_text = models.TextField(
        blank=True,
        default="",
        help_text=(
            "代替テキスト。body_text_is_manual=True なら手動値として使用、"
            "False（既定）なら配信時に html2text 等で自動生成（§4.3）"
        ),
    )
    body_text_is_manual = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "メールテンプレート"
        verbose_name_plural = "メールテンプレート"
        permissions = [
            ("manage_template", "メールテンプレートを作成・編集・削除できる"),
        ]

    def __str__(self):
        return self.name


class MailingConfig(models.Model):
    """メール配信システム設定（仕様書 §4.13）。

    シングルトン (id=1)。特電法フッターで使用するシステム会社情報と DKIM 設定。
    発注書 §1.1：Django の django.conf.settings との混同回避のため
    Settings → MailingConfig に改名。

    シングルトン制約：CheckConstraint と save() override の二重防衛。
    Phase 1 では初回アクセス時に View 側で get_or_create する（発注書 §3-3）。
    """

    id = models.IntegerField(primary_key=True, default=1)
    company_name = models.CharField(max_length=200)
    company_address = models.TextField()
    unsubscribe_contact = models.CharField(
        max_length=200,
        help_text="配信停止連絡先（メアド / 電話 / URL）。特電法フッターで使用",
    )
    dkim_domain = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="DKIM 署名対象ドメイン。Phase 8 で本格運用（§4.13）",
    )
    dkim_selector = models.CharField(
        max_length=50,
        blank=True,
        default="default",
    )
    dkim_private_key = models.TextField(
        blank=True,
        default="",
        help_text="DKIM 秘密鍵（Phase 8 で管理コマンドが生成）",
    )
    default_newsletter_sender_email = models.EmailField(
        blank=True,
        default="",
        help_text=(
            "メルマガ方式のデフォルト差出人アドレス。"
            "値を入れる場合は dkim_domain 配下のドメインに限定（§4.13 rev6 補足）"
        ),
    )
    default_newsletter_sender_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            CheckConstraint(condition=Q(id=1), name="mailing_config_singleton"),
        ]
        verbose_name = "メール配信設定"
        verbose_name_plural = "メール配信設定"

    def save(self, *args, **kwargs):
        # シングルトン強制（CheckConstraint と二重防衛）
        self.id = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"MailingConfig (id={self.id})"
