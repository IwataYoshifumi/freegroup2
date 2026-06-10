"""配信・配信停止系の ActionLog 監査記録（仕様書 v1.6 §16.1 / §7.8.4 / §9.8、rev14）。

配信実行サービスから呼ばれる ActionLog 記録関数（Phase 3）：

  record_send_campaign_action(user, campaign)
      → §16.1「メルマガ配信開始操作」。配信開始時に 1 回記録。

  record_unsubscribe_filter_off_send(user, campaign)
      → §7.8.4「Unsubscribe フィルタ OFF での配信実行」の監査証跡。
        sender_mode='creator' × apply_unsubscribe_filter=False のときのみ呼ぶ。
        特電法対応の事後説明責任（誰が・いつ・どのキャンペーンで OFF にして配信したか）。

配信停止サービスから呼ばれる ActionLog 記録関数（Phase 5 で追加、§9.8）：

  record_unsubscribe_by_admin_action(user, person, note='')
      → §9.8「アンサブスクライブ操作の管理者代行」。
        Person.set_unsubscribed_by_admin から呼ばれる。

  record_cancel_unsubscribe_action(user, person, note='')
      → 管理者による配信停止解除（§9.3.1 / §9.5.5）。
        SuppressedEmail 解除と Person 解除の両方で使い、対象 person または email を data に記録。

action 名定数・記録関数名はコード君判断（§7.8.4 末尾 / §9.8 末尾「v1.4.2 §4.11.3
record_*_action パターンに従う」）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from actionlogs.models import ActionLog

if TYPE_CHECKING:
    from mailings.models import Campaign
    from persons.models import Person


ACTION_SEND_CAMPAIGN = "campaign_send_started"
ACTION_UNSUBSCRIBE_FILTER_OFF_SEND = "campaign_sent_with_unsubscribe_filter_off"
ACTION_UNSUBSCRIBE_BY_ADMIN = "person_unsubscribed_by_admin"
ACTION_CANCEL_UNSUBSCRIBE = "person_unsubscribe_cancelled"


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


def record_unsubscribe_by_admin_action(
    user, person: "Person", note: str = ""
) -> None:
    """管理者代行による配信停止操作を ActionLog に記録する（§9.8 / §4.14.2）。

    [性質] 副作用あり（DB 書込：ActionLog 1 件）
    [入力] user: CustomUser（操作した管理者）、person: Person、note: str
    [出力] None

    Person.set_unsubscribed_by_admin から呼ばれる。サービス関数 unsubscribe_person
    の実行直後に記録（ActionLog はサービス関数とは独立、サービス関数を bulk_create
    で使うため signal 経由で発火させない設計）。
    """
    email = (
        person.primary_contact.email
        if person.primary_contact and person.primary_contact.email
        else ""
    )
    ActionLog.record(
        user=user,
        action=ACTION_UNSUBSCRIBE_BY_ADMIN,
        content_object=person,
        object_repr=str(person),
        data={
            "person_id": str(person.pk),
            "source_email": email,
            "source": "manual",
        },
        note=note or "管理者代行による配信停止（§9.8）",
    )


def record_cancel_unsubscribe_action(
    user, person: "Person", note: str = ""
) -> None:
    """管理者による配信停止解除を ActionLog に記録する（§9.3.1 / §9.5.5）。

    [性質] 副作用あり（DB 書込：ActionLog 1 件）
    [入力] user: CustomUser（解除を行った管理者）、person: Person、note: str
    [出力] None

    cancel_unsubscribe サービス関数の実行直後に呼び出し元（解除 View）が記録する。
    """
    email = (
        person.primary_contact.email
        if person.primary_contact and person.primary_contact.email
        else ""
    )
    ActionLog.record(
        user=user,
        action=ACTION_CANCEL_UNSUBSCRIBE,
        content_object=person,
        object_repr=str(person),
        data={
            "person_id": str(person.pk),
            "source_email": email,
        },
        note=note or "管理者による配信停止解除（§9.3.1）",
    )
