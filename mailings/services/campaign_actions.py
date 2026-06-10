"""Campaign アクション 5 関数（(b) で新設、仕様書 §4.3 / §7.2.5 / §7.7 / §12.4）。

Campaign の各業務アクションをサービス関数として集約する：

  - create_campaign_with_template：新規作成（空 EmailTemplate + Campaign を atomic）
  - schedule_campaign：DRAFT → SCHEDULED 遷移
  - cancel_scheduled_campaign：SCHEDULED → DRAFT 復帰（§7.2.5、scheduled_at 維持）
  - archive_campaign：Campaign + EmailTemplate を同一 atomic で is_archived=True（§12.4）
  - run_test_send：代表 Person で prepare(mode=TEST)、to_email 差し替え、SMTP 送信（§7.7）

3 処理単位の配信実行サービス（campaign_send.py）には手を入れず、_send_email_via_backend
を import 再利用する形でテスト送信を組む。
"""

from __future__ import annotations

from dataclasses import replace

from django.core.exceptions import ValidationError
from django.db import transaction

from mailings.models import Campaign, EmailTemplate


def create_campaign_with_template(form, user):
    """新規 Campaign + 空 EmailTemplate を atomic 生成（(b) §5.1、仕様書 §4.3 rev17）。

    [性質] 副作用あり（DB 書込：EmailTemplate + Campaign を 1 トランザクション内で作成）
    [入力] form: CampaignForm（is_valid() 済み、cleaned_data を使う）、user: CustomUser
    [出力] Campaign（保存済み、template 紐付け済み）

    OneToOne 制約上、EmailTemplate を先に作って Campaign.template に紐付ける。
    両者を 1 atomic で囲むことで「Campaign は作られたのに template だけ無い」を構造的に防ぐ。
    """
    with transaction.atomic():
        template = EmailTemplate.objects.create(
            name=form.cleaned_data["name"],
            subject="",
            body="",
            created_by=user,
        )
        campaign = form.save(commit=False)
        campaign.template = template
        campaign.created_by = user
        campaign.full_clean()
        campaign.save()
        return campaign


def schedule_campaign(campaign):
    """DRAFT → SCHEDULED 遷移（(b) §5.4、仕様書 §4.2 / §7.8.4）。

    [性質] 副作用あり（DB 書込：Campaign.status / updated_at）
    [入力] campaign: Campaign（status=DRAFT、scheduled_at 確定済み）
    [例外] ValidationError（過去日時・必須欠落・組み合わせ禁止）、TransitionError 相当

    Campaign.clean() が scheduled_at の未来日時必須・newsletter 系必須・OFF 組み合わせ禁止
    すべてを担う。本関数は status 遷移 + 保存のみ。
    """
    if campaign.status != Campaign.Status.DRAFT:
        raise ValidationError(
            f"DRAFT 状態のキャンペーンのみ予約できます（現在: {campaign.status}）"
        )
    campaign.status = Campaign.Status.SCHEDULED
    campaign.full_clean()
    campaign.save(update_fields=["status", "updated_at"])


def cancel_scheduled_campaign(campaign):
    """SCHEDULED → DRAFT 復帰（(b) §5.5、仕様書 §7.2.5）。

    [性質] 副作用あり（DB 書込：Campaign.status / updated_at）
    [入力] campaign: Campaign（status=SCHEDULED）
    [例外] ValidationError

    scheduled_at は維持（指示書 §5.5、再予約時のデフォルト値として残す、UX）。
    cron が拾って sending に移行した後は呼ばない前提（呼ばれたら ValidationError）。
    """
    if campaign.status != Campaign.Status.SCHEDULED:
        raise ValidationError(
            f"SCHEDULED 状態のキャンペーンのみ予約取消できます（現在: {campaign.status}）"
        )
    campaign.status = Campaign.Status.DRAFT
    # scheduled_at はクリアしない（指示書 §5.5、UX）
    campaign.save(update_fields=["status", "updated_at"])


def archive_campaign(campaign):
    """Campaign + 紐付く EmailTemplate を同一 atomic で is_archived=True（(b) §5.6、仕様書 §12.4）。

    [性質] 副作用あり（DB 書込：Campaign.is_archived / EmailTemplate.is_archived を 1 atomic）
    [入力] campaign: Campaign（status=DRAFT のみ）
    [例外] ValidationError

    OneToOne PROTECT のため物理削除不可。Campaign 単独 archive は禁止＝必ず EmailTemplate も
    連動（§12.4）。1 atomic で「片方だけ is_archived=True」を構造的に作らせない。
    """
    if campaign.status != Campaign.Status.DRAFT:
        raise ValidationError(
            f"DRAFT 状態のキャンペーンのみ削除できます（現在: {campaign.status}）"
        )
    with transaction.atomic():
        template = campaign.template
        campaign.is_archived = True
        campaign.save(update_fields=["is_archived", "updated_at"])
        template.is_archived = True
        template.save(update_fields=["is_archived", "updated_at"])


def run_test_send(campaign, recipient_email):
    """テスト送信（(b) §5.7、仕様書 §7.7）。

    [性質] 副作用あり（外部 SMTP 送信、DeliveryHistory / TrackingLink / UnsubscribeLink
            は作成しない＝EmailMode.TEST が build を呼ばない、§7.4.6.4 / §7.7 手順 3）
    [入力] campaign: Campaign、recipient_email: str（操作者本人のメアドを想定、指示書 §3.2）
    [出力] None
    [例外] ValueError（代表 Person 不在）、SMTPException 等（呼び出し元の View が捕捉）

    処理順（仕様書 §7.7）：
      1. extract_recipients(campaign).first() で代表 Person 抽出
      2. EmailContext.prepare(campaign, person, mode=EmailMode.TEST) で組み立て
         （TEST モードは build を呼ばず素リンク化、TrackingLink/UnsubscribeLink を発行しない）
      3. dataclasses.replace(ctx, to_email=recipient_email) で宛先差し替え（§4.3 / §7.4.1.3）
      4. _send_email_via_backend(ctx) で実送信（既存 campaign_send.py 関数を import 再利用、
         campaign_send.py 本体には手を入れない）
      5. DeliveryHistory は作らない（テスト配信は集計に影響しない、§7.7 手順 3）
    """
    from mailings.services.campaign_send import _send_email_via_backend
    from mailings.services.email_context import EmailContext, EmailMode
    from mailings.services.recipient_extraction import extract_recipients

    person = extract_recipients(campaign).first()
    if person is None:
        raise ValueError(
            "テスト送信の代表 Person が見つかりません（宛先リストが空、または有効な"
            "メールアドレスを持つ Person がいません）"
        )
    ctx = EmailContext.prepare(campaign, person, EmailMode.TEST)
    ctx = replace(ctx, to_email=recipient_email)
    _send_email_via_backend(ctx)
