"""mailings アプリのモデル（仕様書 v1.6 §4.3 / §4.11 / §4.12 / §4.13、第11章）。

rev12 でリストを凍結スナップショット方式に確定。
発注書 §1.1：Settings → MailingConfig に改名（django.conf.settings との混同回避）。
発注書 §3-1：EmailTemplate.body_html → body に改名（rev9 でプレーンテキスト化と整合）。
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint

from contacts.models import Contact
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


# ======================================================================
# Phase 2：配信系モデル骨格（仕様書 v1.6 §4.2 / §4.4 / §4.5 / §4.5A /
# §4.6 / §4.7 / §4.8 / §4.8A、rev13）。
#
# 本セクションは骨格（フィールド・制約・FK・choices）のみ。
# extract_recipients / build() / validate_sender_domain 本体 / 配信実行サービス /
# EmailContext / cron / バウンス処理 / マージ追従 はすべて Phase 3 以降。
# save() オーバーライドや post_save / pre_save signal は入れない
# （特に Unsubscribe：Phase 9 のサービス関数と二重実行・干渉するため、§9.5.3）。
# ======================================================================


class Campaign(models.Model):
    """配信キャンペーン（仕様書 §4.2、rev13）。

    rev12 で tag_condition を削除し mailing_list を必須 FK 化（NOT NULL、PROTECT）。
    rev6 で sender_mode を導入（creator / newsletter、別表 C.26）。
    Phase 2 では get_pending_scheduled / has_view_permission のみ実装、
    extract_recipients / validate_sender_domain 本体・配信実行サービスは Phase 3。
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "下書き"
        SCHEDULED = "scheduled", "予約配信待機中"
        SENDING = "sending", "配信中"
        DONE = "done", "配信完了"
        FAILED = "failed", "配信失敗"

    class SenderMode(models.TextChoices):
        CREATOR = "creator", "送信元（メール作成者）"
        NEWSLETTER = "newsletter", "送信元（メルマガ）"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=200,
        help_text="キャンペーン名（管理用、受信者には見えない）",
    )
    template = models.OneToOneField(
        EmailTemplate,
        on_delete=models.PROTECT,
        related_name="campaign",
    )
    mailing_list = models.ForeignKey(
        MailingList,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text="このキャンペーンの宛先リスト。配信を予約する時点までに必ず選んでください。",
    )
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="配信を実行する日時。下書きの間は空のままで OK ですが、予約する時点で未来の日時を指定してください。",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    sender_mode = models.CharField(
        max_length=20,
        choices=SenderMode.choices,
        default=SenderMode.CREATOR,
        help_text="送信元の名義を選びます。",
    )
    sender_email = models.EmailField(
        blank=True,
        help_text="メルマガとして配信する場合の差出人アドレス。自社の DKIM 設定済みドメインの配下を指定してください。",
    )
    sender_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="メルマガとして配信する場合の差出人名（受信メールの From 欄に表示されます）。",
    )
    apply_unsubscribe_filter = models.BooleanField(
        default=True,
        help_text="配信停止を希望した宛先には送らない設定です。通常はオンのままにしてください。メルマガとして配信する場合は常時オンに固定されます。",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_count = models.IntegerField(default=0)
    sent_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    is_archived = models.BooleanField(
        default=False,
        help_text=(
            "論理削除フラグ（§12.4）。Campaign 論理削除サービスが Campaign と紐付く"
            "EmailTemplate を同一 atomic で True に更新する（OneToOne PROTECT のため"
            "物理削除不可）。一覧・詳細は is_archived=False のみ表示"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "キャンペーン"
        verbose_name_plural = "キャンペーン"
        permissions = [
            ("send_campaign", "メール配信を実行できる（不可逆操作のため CRUD から分離、§14.1.2）"),
            ("view_all_campaigns", "他人の配信キャンペーン・配信レポートを閲覧できる"),
            ("export_report", "配信レポート CSV をダウンロードできる（§14.1.2）"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        """構造的制約と遷移時バリデーション（仕様書 §7.8.4 / §4.2 / §7.8.3、(b) で実装）。

        [性質] 副作用なし（DB 読み取りなし、ただし validate_sender_domain 経由で
                Settings シングルトンを読む可能性あり = 準関数）
        [入力] なし（self の値を検証）
        [例外] ValidationError

        検証順：
          1. sender_mode='newsletter' × apply_unsubscribe_filter=False（特電法事故防止、§7.8.4）
          2. sender_mode='newsletter' × sender_email 空（必須、§7.8.3）
          3. sender_mode='newsletter' × sender_name 空（必須、§7.8.3）
          4. sender_mode='newsletter' のとき validate_sender_domain（DKIM ドメイン外なら ValidationError）
          5. status='scheduled' 遷移時：scheduled_at 必須・未来日時のみ（§4.2）

        draft 保存時は (5) をスキップ（scheduled_at 未確定のまま draft 保存可）。
        (1)-(4) は draft でも常時検証（メルマガ × OFF 等は draft でも作らせない＝
        誤って scheduled に進めて配信実行直前バリデーションで弾かれる前に止める）。
        """
        super().clean()
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        errors = {}

        # (1) 構造的禁止：メルマガ方式 × Unsubscribe フィルタ OFF
        if (
            self.sender_mode == self.SenderMode.NEWSLETTER
            and not self.apply_unsubscribe_filter
        ):
            errors["apply_unsubscribe_filter"] = (
                "メルマガ方式では配信停止フィルタを OFF にできません（特電法事故防止、§7.8.4）"
            )

        if self.sender_mode == self.SenderMode.NEWSLETTER:
            # (2)(3) メルマガ方式の差出人必須
            if not (self.sender_email or "").strip():
                errors["sender_email"] = "メルマガ方式では差出人アドレスが必須です（§7.8.3）"
            if not (self.sender_name or "").strip():
                errors["sender_name"] = "メルマガ方式では差出人名が必須です（§7.8.3）"
            # (4) DKIM 許可ドメイン検証（sender_email が埋まっているときのみ）
            if (self.sender_email or "").strip() and "sender_email" not in errors:
                from mailings.services.sender_domain import validate_sender_domain

                try:
                    validate_sender_domain(self)
                except ValidationError as e:
                    errors["sender_email"] = e.messages[0] if e.messages else str(e)

        # (5) scheduled 遷移時：scheduled_at 必須・未来日時 + 宛先リスト必須 + 件名・本文必須
        if self.status == self.Status.SCHEDULED:
            if self.scheduled_at is None:
                errors["scheduled_at"] = "予約配信日時は必須です。"
            elif self.scheduled_at <= timezone.now():
                errors["scheduled_at"] = "予約配信日時は未来の日時を指定してください。"
            # 宛先リストは draft の間は空のままで OK だが、scheduled 遷移時には必須。
            # （rev12 までは DB レベル NOT NULL だったが、Campaign を「名前だけ」で
            # 先に作って後から宛先を埋める運用に揃えるため、必須チェックを scheduled
            # 遷移時に移管した）
            if self.mailing_list_id is None:
                errors["mailing_list"] = "宛先リストを選択してください。"

            template = None
            try:
                if self.template_id is not None:
                    template = self.template
            except Exception:
                template = None

            if template is None:
                errors["subject"] = "件名が未入力です"
                errors["body"] = "本文が未入力です"
            else:
                if not (template.subject or "").strip():
                    errors["subject"] = "件名が未入力です"
                if not (template.body or "").strip():
                    errors["body"] = "本文が未入力です"

        if errors:
            raise ValidationError(errors)

    @property
    def is_mailing_list_ready(self):
        """宛先リストが設定されているか（仕様書 §7.8 / フェーズ2）。"""
        return self.mailing_list_id is not None

    @property
    def is_template_ready(self):
        """メール件名および本文が両方入力されているか（仕様書 §7.8 / フェーズ2）。"""
        try:
            if self.template_id is None:
                return False
            t = self.template
            if t is None:
                return False
            subject_ok = bool((t.subject or "").strip())
            body_ok = bool((t.body or "").strip())
            return subject_ok and body_ok
        except Exception:
            return False

    @property
    def is_schedule_ready(self):
        """予約配信日時が設定され、かつ未来日時か（仕様書 §7.8 / フェーズ2）。"""
        from django.utils import timezone

        return self.scheduled_at is not None and self.scheduled_at > timezone.now()

    @property
    def missing_required_steps(self):
        """予約までに未完了の必須工程名リストを返す（仕様書 §7.8 / フェーズ2）。"""
        missing = []
        if not self.is_mailing_list_ready:
            missing.append("宛先リスト")
        if not self.is_template_ready:
            missing.append("メール内容")
        if not self.is_schedule_ready:
            missing.append("配信スケジュール")
        return missing

    @property
    def is_ready_to_schedule(self):
        """予約に必要な全必須工程が完了しているか（仕様書 §7.8 / フェーズ2）。"""
        return len(self.missing_required_steps) == 0

    @classmethod
    def get_pending_scheduled(cls):
        """status='scheduled' かつ scheduled_at <= now() の Campaign を返す（§4.2.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] なし
        [出力] QuerySet[Campaign]

        cron 起動の管理コマンド（Phase 3 で実装する send_scheduled_campaigns）が
        起動対象の絞り込みに使用する。scheduled_at が NULL の draft は __lte で
        自動的に除外される。
        """
        from django.utils import timezone

        return cls.objects.filter(
            status=cls.Status.SCHEDULED,
            scheduled_at__lte=timezone.now(),
        )

    def has_view_permission(self, user):
        """閲覧権限判定（§4.2.1）。

        [性質] 純関数（DB 操作なし、user.has_perm のみ参照）
        [入力] user: CustomUser
        [出力] bool

        view_all_campaigns 持ち、または作成者本人なら True。
        判定ロジックの正本は services.permissions.can_view_campaign（rev20 §14.5.2）で、
        本メソッドはその薄いラッパ（ロジックを二重に持たない）。
        """
        from mailings.services.permissions import can_view_campaign

        return can_view_campaign(user, self)


class DeliveryHistory(models.Model):
    """配信履歴（仕様書 §4.4、rev13）。

    キャンペーン × 受信者 1 件 1 件の送信レコード。Person マージ時の FK 付け替えは
    行わない（rev5、§9.4）。会社名・氏名・宛名は配信時点でスナップショット凍結保存
    （rev4、§4.4.1）。rev13 で本編 v1.6.0 リネーム反映により
    company_at_send → organization_at_send に改名（Contact.organization と整合）。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "送信前"
        SENT = "sent", "送信成功"
        FAILED = "failed", "送信失敗"
        BOUNCED = "bounced", "バウンス"
        UNSUBSCRIBED = "unsubscribed", "配信停止"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="delivery_histories",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="delivery_histories",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=(
            "配信時のプライマリーコンタクト（参考情報、マージ・削除で失われる可能性あり）"
        ),
    )
    to_email = models.EmailField(
        help_text="配信先メアド（送信時点の値を凍結保存、§4.4.1）",
    )
    organization_at_send = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "送信時点の組織名（会社名）スナップショット。rev13 で本編 v1.6.0 の "
            "Contact.organization リネーム反映により company_at_send から改名"
        ),
    )
    full_name_at_send = models.CharField(max_length=255, blank=True, default="")
    salutation_name_at_send = models.CharField(max_length=255, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="5 値（pending/sent/failed/bounced/unsubscribed、別表 C.22）",
    )
    error_message = models.TextField(blank=True, default="")
    failed_count = models.IntegerField(
        default=0,
        help_text=(
            "送信試行の累積失敗回数（§7.2.2）。"
            "CAMPAIGN_RECIPIENT_MAX_FAILURES に達したら「最終 failed」確定、"
            "status は failed のまま（別表 C.22 の 5 値は変更しない、§4.4）"
        ),
    )
    last_failed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="最後の失敗日時（運用視認性・デバッグ用、§7.2.2 では任意）",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "配信履歴"
        verbose_name_plural = "配信履歴"
        constraints = [
            UniqueConstraint(
                fields=["campaign", "person"],
                name="unique_delivery_history_campaign_person",
            ),
        ]

    def __str__(self):
        return f"{self.campaign} → {self.to_email}"

    @classmethod
    def get_pending_for_campaign(cls, campaign, limit):
        """配信実行サービス用：未送信 DeliveryHistory を skip_locked で取得（§7.2.1）。

        [性質] 準関数（DB 読み取り、ロック取得を伴う）
        [入力] campaign: Campaign、limit: int
        [出力] QuerySet[DeliveryHistory]

        対象：status != SENT かつ failed_count < CAMPAIGN_RECIPIENT_MAX_FAILURES
        （最終 failed 確定済みは拾わない）。select_for_update(skip_locked=True) を
        メソッド内に込めることで、呼び出し側にロック制御を書かせない（OriginalImage
        の get_pending と同型、§7.2.1）。複数 cron ワーカー同時起動でも同じ受信者を
        二重に掴まず、構造的に二重送信が起きない（PostgreSQL 前提）。
        """
        from config.constants import CAMPAIGN_RECIPIENT_MAX_FAILURES

        return (
            cls.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(campaign=campaign)
            .filter(
                models.Q(status=cls.Status.PENDING)
                | models.Q(
                    status=cls.Status.FAILED,
                    failed_count__lt=CAMPAIGN_RECIPIENT_MAX_FAILURES,
                )
            )
            .select_related("person", "person__primary_contact")
            .order_by("created_at")[:limit]
        )

    @classmethod
    def is_finally_failed(cls, history):
        """最終 failed 判定（§7.2.2）：failed_count が M に達したか。

        [性質] 純関数（DB 操作なし、フィールド参照のみ）
        [入力] history: DeliveryHistory
        [出力] bool
        """
        from config.constants import CAMPAIGN_RECIPIENT_MAX_FAILURES

        return (
            history.status == cls.Status.FAILED
            and history.failed_count >= CAMPAIGN_RECIPIENT_MAX_FAILURES
        )


class TrackingLink(models.Model):
    """トラッキングリンク（§4.5）。

    キャンペーン × 受信者 × 元 URL の組合せで一意。token は CharField(20) unique の
    定義のみ。secrets.token_urlsafe(8) での生成は Phase 3 の build() 責務（§4.5）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="tracking_links",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="tracking_links",
    )
    original_url = models.URLField(max_length=2000)
    token = models.CharField(
        max_length=20,
        unique=True,
        help_text="URL 埋め込みトークン（生成は Phase 3 build()、自動生成を Phase 2 で仕込まない）",
    )
    click_count = models.IntegerField(default=0, help_text="有効クリック回数（ボット除外後）")
    total_access_count = models.IntegerField(default=0, help_text="総アクセス回数（ボット含む）")
    last_clicked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "トラッキングリンク"
        verbose_name_plural = "トラッキングリンク"
        constraints = [
            UniqueConstraint(
                fields=["campaign", "person", "original_url"],
                name="unique_tracking_link_campaign_person_url",
            ),
        ]

    def __str__(self):
        return f"{self.campaign} → {self.token}"

    @classmethod
    def build(cls, campaign, person, original_url):
        """未保存の TrackingLink インスタンスを組み立てる（§7.4.3、PRODUCTION 専用）。

        [性質] 純関数（DB 操作なし、トークン生成のみ）
        [入力] campaign: Campaign、person: Person、original_url: str
        [出力] TrackingLink（未保存、後段が bulk_create で永続化）

        secrets.token_urlsafe(8) で 8 byte → 約 11 文字の URL-safe トークンを生成。
        トークン生成・URL 生成はこの classmethod に完全に閉じ、EmailContext.prepare /
        _render_merge_field はトークンに一切関与しない（§7.4.3 三層責務）。
        build は「保存先はあるが保存はまだ」を意味する慣用名（§7.4.3.1）。
        """
        import secrets

        return cls(
            campaign=campaign,
            person=person,
            original_url=original_url,
            token=secrets.token_urlsafe(8),
        )


class UnsubscribeLink(models.Model):
    """配信停止リンク（§4.5A、rev4 で新設）。

    TrackingLink と類似だが original_url を持たず（配信停止画面に固定）、配信停止と
    一般クリックを集計対象として分離するため別モデル（§4.5A.1）。
    トークン有効期限なし（rev10、§4.5A.0）：古いメールからの配信停止を正常利用と
    位置づけ、特電法のオプトアウト確実提供を優先する。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="unsubscribe_links",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="unsubscribe_links",
    )
    token = models.CharField(
        max_length=20,
        unique=True,
        help_text="URL 埋め込みトークン（TrackingLink とは別 namespace、生成は Phase 3）",
    )
    target_email = models.EmailField(
        # rev15 §4.5A：NOT NULL かつ default なし。発行時に必ず焼き込まれる正本。
        # UnsubscribeLink を作る経路は本番送信のみ（§7.4.6.4：TEST/PREVIEW では
        # build を呼ばない）であり、send_one_recipient → EmailContext.prepare →
        # UL.build(target_email=to_email) で必ず非空値が渡る。判定の信頼性を担保する。
        help_text=(
            "送信先メアドのスナップショット（発行時に焼き込み、rev15 §4.5A）。"
            "配信停止判定（個人/代表）の正本。配信後に person.primary_contact が"
            "差し替わっても判定が後天的にブレないよう、送信時の事実を保持する。"
            "DeliveryHistory.to_email と同一値だが、配信停止フローでは本フィールドが正本。"
        ),
    )
    accessed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="受信者の初回 GET 日時（§4.5A.2）",
    )
    unsubscribed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="配信停止確定日時（POST、§4.5A.2）",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "配信停止リンク"
        verbose_name_plural = "配信停止リンク"
        constraints = [
            UniqueConstraint(
                fields=["campaign", "person"],
                name="unique_unsubscribe_link_campaign_person",
            ),
            # rev15 §4.5A：target_email は発行時に必ず焼き込まれる正本。NOT NULL に
            # 加えて空文字も禁止し、未焼き込みレコードを構造的に作れなくする
            # （CharField 系の Django デフォルトは未指定で空文字が入るため、NOT NULL
            # だけだと「target_email 未指定で create」が空文字で通ってしまう）。
            CheckConstraint(
                condition=~Q(target_email=""),
                name="unsubscribelink_target_email_nonempty",
            ),
        ]

    def __str__(self):
        return f"{self.campaign} → {self.token}"

    @classmethod
    def build(cls, campaign, person, target_email):
        """未保存の UnsubscribeLink インスタンスを組み立てる（§7.4.3、PRODUCTION 専用）。

        [性質] 純関数（DB 操作なし、トークン生成のみ）
        [入力] campaign: Campaign、person: Person、target_email: str（送信先メアド、
               発行時の事実として焼き込む、rev15 §4.5A）
        [出力] UnsubscribeLink（未保存、後段が bulk_create で永続化）

        TrackingLink とは別 namespace のトークンを secrets.token_urlsafe(8) で生成。
        original_url を持たない（配信停止画面 /u/<token>/ に固定、§4.5A.1）。

        【rev15 で追加】 target_email は受信者へ実際に送られたメアドを保持する。配信
        停止判定（個人/代表）と代表経路の SuppressedEmail 登録メアドは、本値を正本と
        して使う（§9.5.4）。EmailContext.prepare 側が to_email を引数で渡す。
        """
        import secrets

        return cls(
            campaign=campaign,
            person=person,
            token=secrets.token_urlsafe(8),
            target_email=target_email,
        )


class ClickLog(models.Model):
    """クリックログ（§4.6）。

    トラッキングリンクへの 1 アクセスを表す。生のクリックログは全件記録し、
    ボット判定で is_valid_click を分岐。bot_reason に判定理由を別表 C.23 の
    5 値で保存（is_valid_click=False のときのみ値あり）。
    """

    class BotReason(models.TextChoices):
        NON_GET = "non_get", "GET 以外"
        KNOWN_BOT = "known_bot", "既知のボット"
        TOO_FAST = "too_fast", "プリフェッチ"
        DUPLICATE = "duplicate", "重複クリック"
        TEST_SEND = "test_send", "テスト配信"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tracking_link = models.ForeignKey(
        TrackingLink,
        on_delete=models.CASCADE,
        related_name="click_logs",
    )
    clicked_at = models.DateTimeField(auto_now_add=True)
    ip_masked = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text=(
            "マスク済み IP（IPv4 末尾 0、IPv6 下位ビットマスク）。"
            "保持日数経過後に NULL 上書き（Phase 3 の管理コマンド、§4.6）"
        ),
    )
    user_agent = models.TextField(blank=True, default="")
    http_method = models.CharField(max_length=10)
    is_valid_click = models.BooleanField()
    bot_reason = models.CharField(
        max_length=50,
        choices=BotReason.choices,
        blank=True,
        default="",
        help_text="無効と判定された理由（is_valid_click=False 時のみ値あり、別表 C.23）",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "クリックログ"
        verbose_name_plural = "クリックログ"

    def __str__(self):
        return f"{self.tracking_link} @ {self.clicked_at}"


class Unsubscribe(models.Model):
    """配信停止履歴（§4.7）。

    Person 単位の不変履歴（解除は cancelled_at の論理削除）。
    rev5 で source の 'bounce' を削除：バウンスは SuppressedEmail へのメアド登録のみ、
    Person 単位の Unsubscribe は作らない（§10）。
    bulk_create を使う前提のため save() オーバーライド・post_save signal は入れない
    （Phase 9 のサービス関数と二重実行・干渉するため、§9.5.3）。
    """

    class Source(models.TextChoices):
        UNSUBSCRIBE_LINK = "unsubscribe_link", "配信停止リンク"
        MANUAL = "manual", "管理者手動"
        API = "api", "API 経由"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="unsubscribes",
    )
    source_email = models.EmailField(
        help_text="操作元のメアド文字列（Contact が消えても残す、§4.7）",
    )
    source = models.CharField(max_length=30, choices=Source.choices)
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="配信停止解除日時（解除は論理削除、§4.7 設計趣旨）",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.person} ({self.source})"


class SuppressedEmail(models.Model):
    """配信拒否リスト（§4.8）。

    メアド単位の配信拒否レコード（代表メール由来 / ハードバウンス由来）。
    partial unique constraint（§4.8.1）：active (cancelled_at=NULL) は email ごとに
    1 件まで・解除済みは重複可・解除 → 再追加が IntegrityError なしで可能。
    Person との直接 FK は持たない（複数 Person が同じ代表メアドを共有しうるため、§4.8）。
    """

    class Source(models.TextChoices):
        UNSUBSCRIBE_GENERIC = "unsubscribe_generic", "代表メール配信停止"
        BOUNCE_HARD = "bounce_hard", "ハードバウンス"
        BOUNCE_SOFT_PROMOTED = "bounce_soft_promoted", "ソフトバウンス昇格"
        MANUAL = "manual", "管理者手動"
        API = "api", "API 経由"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(
        help_text=(
            "配信拒否メアド。unique=True は付けない（partial unique で制御、§4.8.1）。"
            "解除 → 再追加を IntegrityError なしで可能にするため"
        ),
    )
    source = models.CharField(max_length=30, choices=Source.choices)
    bounce_reason = models.TextField(
        blank=True,
        default="",
        help_text="バウンスの場合の MTA 応答メッセージ等",
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="配信拒否解除日時（論理削除、§4.8.1）",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(
                fields=["email"],
                condition=Q(cancelled_at__isnull=True),
                name="unique_active_suppressed_email",
            ),
        ]
        permissions = [
            (
                "manage_suppressed_email",
                "配信拒否リストを編集できる（解除・手動追加、§14.1.1）",
            ),
        ]

    def __str__(self):
        return self.email


class SoftBounceCounter(models.Model):
    """ソフトバウンス連続失敗カウンタ（§4.8A、rev4 で新設）。

    SuppressedEmail と責務分離（§4.8A.1）。閾値到達で SuppressedEmail に
    'bounce_soft_promoted' で昇格、本レコードは物理削除（§4.8A.2）。
    解除概念がないため email は素の unique=True（SuppressedEmail の partial と対照、§4.8A）。
    配信フィルタには使わない（フィルタは SuppressedEmail のみ、§4.8A.3）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(
        unique=True,
        help_text=(
            "カウント対象メアド。昇格時・送信成功時に物理削除されるため "
            "素の unique=True で十分（SuppressedEmail の partial unique と対照、§4.8A）"
        ),
    )
    count = models.IntegerField(default=0, help_text="現時点の連続ソフトバウンス回数")
    last_bounce_at = models.DateTimeField(help_text="最後にソフトバウンスを観測した日時")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_bounce_at"]
        verbose_name = "ソフトバウンスカウンタ"
        verbose_name_plural = "ソフトバウンスカウンタ"

    def __str__(self):
        return f"{self.email} (count={self.count})"
