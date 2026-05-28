"""配信実行の ActionLog 監査記録（仕様書 v1.6 §16.1 / §7.8.4、rev14）。

配信実行サービスから呼ばれる ActionLog 記録関数：

  record_send_campaign_action(user, campaign)
      → §16.1「メルマガ配信開始操作」。配信開始時に 1 回記録。

  record_unsubscribe_filter_off_send(user, campaign)
      → §7.8.4「Unsubscribe フィルタ OFF での配信実行」の監査証跡。
        sender_mode='creator' × apply_unsubscribe_filter=False のときのみ呼ぶ。
        特電法対応の事後説明責任（誰が・いつ・どのキャンペーンで OFF にして配信したか）。

action 名定数・記録関数名はコード君判断（§7.8.4 末尾）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from actionlogs.models import ActionLog

if TYPE_CHECKING:
    from mailings.models import Campaign


ACTION_SEND_CAMPAIGN = "campaign_send_started"
ACTION_UNSUBSCRIBE_FILTER_OFF_SEND = "campaign_sent_with_unsubscribe_filter_off"


def record_send_campaign_action(user, campaign: "Campaign") -> None:
    """メルマガ配信開始操作を ActionLog に記録する（§16.1）。

    [性質] 副作用あり（DB 書込：ActionLog 1 件）
    [入力] user: CustomUser（cron 起動時は campaign.created_by を渡す想定）
    [出力] None
    """
    ActionLog.record(
        user=user,
        action=ACTION_SEND_CAMPAIGN,
        content_object=campaign,
        object_repr=campaign.name,
        data={
            "campaign_id": str(campaign.pk),
            "sender_mode": campaign.sender_mode,
            "apply_unsubscribe_filter": campaign.apply_unsubscribe_filter,
        },
    )


def record_unsubscribe_filter_off_send(user, campaign: "Campaign") -> None:
    """apply_unsubscribe_filter=False での配信実行を監査記録する（§7.8.4）。

    [性質] 副作用あり（DB 書込：ActionLog 1 件）
    [入力] user: CustomUser、campaign: Campaign（apply_unsubscribe_filter=False 前提）
    [出力] None
    """
    ActionLog.record(
        user=user,
        action=ACTION_UNSUBSCRIBE_FILTER_OFF_SEND,
        content_object=campaign,
        object_repr=campaign.name,
        data={
            "campaign_id": str(campaign.pk),
            "sender_mode": campaign.sender_mode,
        },
        note="特電法対応の事後説明責任のため、配信停止フィルタ OFF での配信実行を記録（§7.8.4）",
    )
