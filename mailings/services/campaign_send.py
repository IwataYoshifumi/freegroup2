"""配信実行サービス（仕様書 v1.6 §4.2.1 / §7.2、rev14）。

配信実行の 3 処理単位（§4.2.1）のうち、本ファイルが (II) と (III) を担う。
(I) は mailings/management/commands/send_scheduled_campaigns.py（管理コマンド）。

  (II) execute_campaign_send(campaign, batch_limit)：1 キャンペーン分のオーケストレーション。
       未送信受信者を N 件まで処理し、残りは次回 cron 起動が拾う。

  (III) send_one_recipient(campaign, history)：1 受信者 1 通送信。1 atomic。
        EmailContext.prepare → DeliveryHistory pending → send_mail → 成功後 bulk_create
        → status sent/failed 更新。

配信実行は Campaign インスタンスメソッドにしない（サービス層関数、v1.4.2 §13.2.2 命名規約）。
真のモデル横断（DeliveryHistory・TrackingLink・UnsubscribeLink・SoftBounceCounter 関連等の
多数モデルを変更する）のためサービス層が正しい配置（§4.2.1）。

【最重大事故防止】 送信失敗（DeliveryHistory.status=failed）は SoftBounceCounter に
一切触れない（§7.2.3 / §10.0）。ソフトバウンス（送信成功後の跳ね返り通知）は Phase 5 領域。
混同すると SMTP 一時不調で生存顧客が次々 SuppressedEmail 登録される復旧困難事故。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from config.constants import (
    CAMPAIGN_RECIPIENT_MAX_FAILURES,
    CAMPAIGN_SEND_BATCH_LIMIT,
)
from mailings.models import Campaign, DeliveryHistory
from mailings.services.audit import (
    record_send_campaign_action,
    record_unsubscribe_filter_off_send,
)
from mailings.services.email_context import EmailContext, EmailMode
from mailings.services.recipient_extraction import extract_recipients
from mailings.services.sender_domain import validate_sender_domain

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _ensure_delivery_histories(campaign: Campaign) -> int:
    """配信対象 Person 全件について DeliveryHistory(pending) が無ければ作成する。

    [性質] 副作用あり（DB 書込：DeliveryHistory bulk_create、Campaign.total_count 更新）
    [入力] campaign: Campaign
    [出力] int（既存 + 新規作成された DeliveryHistory 件数の合計）

    初回 cron 起動時に呼ばれる初期化（extract_recipients → bulk_create）。
    2 回目以降は既存と差分を取って未作成のみ追加（マージで Person 集合が変わるケースは
    Phase 9 領域、Phase 3 では初回作成後は変動しない前提）。
    """
    person_qs = extract_recipients(campaign)
    person_ids = list(person_qs.values_list("id", flat=True))
    if not person_ids:
        return 0

    existing_ids = set(
        DeliveryHistory.objects.filter(
            campaign=campaign, person_id__in=person_ids
        ).values_list("person_id", flat=True)
    )
    to_create_ids = [pid for pid in person_ids if pid not in existing_ids]
    if to_create_ids:
        person_map = {p.id: p for p in person_qs if p.id in to_create_ids}
        new_histories = []
        for pid in to_create_ids:
            person = person_map[pid]
            contact = person.primary_contact
            new_histories.append(
                DeliveryHistory(
                    campaign=campaign,
                    person=person,
                    contact=contact,
                    to_email=contact.email if contact else "",
                    organization_at_send=getattr(contact, "organization", "") or "",
                    full_name_at_send=getattr(contact, "full_name", "") or "",
                    salutation_name_at_send=getattr(contact, "salutation_name", "") or "",
                    status=DeliveryHistory.Status.PENDING,
                )
            )
        DeliveryHistory.objects.bulk_create(new_histories, ignore_conflicts=True)

    total = DeliveryHistory.objects.filter(campaign=campaign).count()
    if campaign.total_count != total:
        campaign.total_count = total
        campaign.save(update_fields=["total_count", "updated_at"])
    return total


def _send_email_via_backend(ctx: EmailContext) -> None:
    """送信抽象化レイヤー（§2.2.1）：Django の EmailMultiAlternatives を直接使う。

    [性質] 副作用あり（外部 SMTP 経由の送信、または開発時の locmem バックエンド）
    [入力] ctx: EmailContext
    [出力] None
    [例外] バックエンドが投げる任意の例外（呼び出し側で捕捉して failed 化）

    HTML 本文 + プレーンテキスト代替（html2text 風に <br> を改行に戻した最小実装）の
    multipart/alternative で送信（§7.6）。代替テキスト本格生成は §4.3 body_text の
    body_text_is_manual=True 経路でユーザー手動値を使う想定（Phase 3 の最小実装では
    HTML 本文から雑に変換）。
    """
    from_value = (
        f"{ctx.from_name} <{ctx.from_email}>" if ctx.from_name else ctx.from_email
    )
    # 簡易テキスト代替：<br> → 改行、<a href="X">Y</a> → "Y (X)" のシンプル変換のみ。
    # 本格生成（html2text）は Phase 5 以降で body_text_is_manual=False 経路に統合する想定。
    text_alt = ctx.body_html.replace("<br>", "\n")
    msg = EmailMultiAlternatives(
        subject=ctx.subject_rendered,
        body=text_alt,
        from_email=from_value,
        to=[ctx.to_email],
    )
    msg.attach_alternative(ctx.body_html, "text/html")
    msg.send(fail_silently=False)


def send_one_recipient(campaign: Campaign, history: DeliveryHistory) -> bool:
    """1 受信者 1 通送信（(III)、§4.2.1 / §7.2.1）。1 atomic = 1 コミット。

    [性質] 副作用あり（DB 書込：DeliveryHistory 更新 + 成功時 TrackingLink/UnsubscribeLink
           bulk_create、SMTP 送信）
    [入力] campaign: Campaign、history: DeliveryHistory（既に pending or failed で作成済み）
    [出力] bool（送信成功なら True、失敗なら False）

    処理順（§7.2.1）：
      1. EmailContext.prepare(PRODUCTION) でリンクのトークン発行とリンク本文を組む
      2. send_mail（HTML 変換済み本文）
      3. 成功 → bulk_create で TrackingLink/UnsubscribeLink 永続化、status=sent
      4. 失敗 → failed_count++、status=failed（SoftBounceCounter には触らない、§7.2.3）

    1 atomic でコミット。N 件全体を 1 トランザクションで囲まない（途中失敗時に
    送信済みメールを取り消せないのに DB 全ロールバックされる不整合防止、§7.2.1）。
    """
    with transaction.atomic():
        try:
            ctx = EmailContext.prepare(campaign, history.person, EmailMode.PRODUCTION)
            _send_email_via_backend(ctx)
        except Exception as exc:
            # 【重要】 ここで SoftBounceCounter に絶対に触れない（§7.2.3）。
            history.status = DeliveryHistory.Status.FAILED
            history.failed_count += 1
            history.last_failed_at = timezone.now()
            history.error_message = f"{type(exc).__name__}: {exc}"[:5000]
            history.save(
                update_fields=[
                    "status",
                    "failed_count",
                    "last_failed_at",
                    "error_message",
                ]
            )
            logger.warning(
                "Campaign %s person %s send failed (count=%d): %s",
                campaign.pk,
                history.person_id,
                history.failed_count,
                exc,
            )
            return False

        # 成功：build 済み TrackingLink/UnsubscribeLink を永続化（§7.4.3.3）。
        if ctx.tracking_links:
            from mailings.models import TrackingLink

            TrackingLink.objects.bulk_create(
                list(ctx.tracking_links), ignore_conflicts=True
            )
        if ctx.unsubscribe_links:
            from mailings.models import UnsubscribeLink

            UnsubscribeLink.objects.bulk_create(
                list(ctx.unsubscribe_links), ignore_conflicts=True
            )

        history.status = DeliveryHistory.Status.SENT
        history.sent_at = timezone.now()
        history.error_message = ""
        history.save(update_fields=["status", "sent_at", "error_message"])
        return True


def _check_campaign_completion(campaign: Campaign) -> None:
    """完了判定（§7.2.1 / §7.2.2）：全員 sent または最終 failed なら status=done。

    [性質] 副作用あり（条件を満たす場合のみ Campaign.status / completed_at / 集計値を更新）
    [入力] campaign: Campaign
    [出力] None
    """
    histories = DeliveryHistory.objects.filter(campaign=campaign)
    total = histories.count()
    if total == 0:
        return

    sent_qs = histories.filter(status=DeliveryHistory.Status.SENT)
    sent_count = sent_qs.count()
    final_failed = histories.filter(
        status=DeliveryHistory.Status.FAILED,
        failed_count__gte=CAMPAIGN_RECIPIENT_MAX_FAILURES,
    ).count()

    if sent_count + final_failed >= total:
        campaign.status = Campaign.Status.DONE
        campaign.completed_at = timezone.now()
        campaign.sent_count = sent_count
        campaign.failed_count = final_failed
        campaign.save(
            update_fields=[
                "status",
                "completed_at",
                "sent_count",
                "failed_count",
                "updated_at",
            ]
        )


def execute_campaign_send(
    campaign: Campaign, *, batch_limit: int = CAMPAIGN_SEND_BATCH_LIMIT, actor=None
) -> dict:
    """1 キャンペーン分の配信実行オーケストレーション（(II)、§4.2.1 / §7.2.1）。

    [性質] 副作用あり（DB 書込：Campaign.status 遷移・DeliveryHistory 初期作成と更新・
           TrackingLink/UnsubscribeLink 永続化、SMTP 送信、ActionLog 記録）
    [入力] campaign: Campaign（status=scheduled or sending）
           batch_limit: int（1 起動の処理上限 N、デフォルト CAMPAIGN_SEND_BATCH_LIMIT）
           actor: CustomUser | None（cron 起動時は None。campaign.created_by が監査に使われる）
    [出力] dict（処理結果サマリ：processed/sent/failed/finally_failed/done）
    [例外] ValidationError（§7.8.4 構造制約違反、§7.8.3 差出ドメイン許可外）

    処理順：
      1. 構造制約再検証（newsletter × apply_unsubscribe_filter=False 禁止、§7.8.4）
      2. validate_sender_domain（newsletter のときのみ、§7.8.3）
      3. 初回起動：DeliveryHistory 一括作成 + ActionLog 記録 + status=sending 遷移
      4. ループ：未送信受信者を skip_locked で batch_limit 件まで取得 → 1 受信者ずつ送信
      5. 完了判定：全員 sent or 最終 failed なら status=done
    """
    # 1. 構造制約再検証（draft 保存後に sender_mode のみ変更されるケースに対応、§7.8.4）
    if (
        campaign.sender_mode == Campaign.SenderMode.NEWSLETTER
        and not campaign.apply_unsubscribe_filter
    ):
        raise ValidationError(
            "sender_mode='newsletter' のとき apply_unsubscribe_filter=False は禁止です"
            "（特電法事故防止、§7.8.4）"
        )

    # 2. 差出ドメイン検証（newsletter のときのみ、§7.8.3）
    validate_sender_domain(campaign)

    # 3. 初回起動：DeliveryHistory を全件 pending で初期化、ActionLog 記録、status=sending
    is_first_invocation = campaign.status == Campaign.Status.SCHEDULED
    if is_first_invocation:
        _ensure_delivery_histories(campaign)
        invocation_actor = actor or campaign.created_by
        record_send_campaign_action(invocation_actor, campaign)
        if not campaign.apply_unsubscribe_filter:
            # §7.8.4：creator × OFF の場合のみ追加で監査（newsletter × OFF は手順 1 で拒否済み）
            record_unsubscribe_filter_off_send(invocation_actor, campaign)
        campaign.status = Campaign.Status.SENDING
        campaign.started_at = timezone.now()
        campaign.save(update_fields=["status", "started_at", "updated_at"])

    # 4. 未送信受信者を batch_limit 件取得して 1 受信者ずつ送信
    summary = {"processed": 0, "sent": 0, "failed": 0}
    with transaction.atomic():
        pending = list(
            DeliveryHistory.get_pending_for_campaign(campaign, batch_limit)
        )
    # 注：上の atomic は select_for_update を発行するためのロック取得。
    # 1 受信者の送信は send_one_recipient 内で別 atomic で囲み、1 atomic = 1 コミット
    # を守る（§7.2.1）。
    for history in pending:
        summary["processed"] += 1
        if send_one_recipient(campaign, history):
            summary["sent"] += 1
        else:
            summary["failed"] += 1

    # 5. 完了判定
    _check_campaign_completion(campaign)
    campaign.refresh_from_db(fields=["status"])
    summary["done"] = campaign.status == Campaign.Status.DONE
    summary["finally_failed"] = DeliveryHistory.objects.filter(
        campaign=campaign,
        status=DeliveryHistory.Status.FAILED,
        failed_count__gte=CAMPAIGN_RECIPIENT_MAX_FAILURES,
    ).count()
    return summary
