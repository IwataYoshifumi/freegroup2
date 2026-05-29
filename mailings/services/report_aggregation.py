"""配信レポート集計サービス（仕様書 v1.6 第13章 / §6.2.2 / §6.2.3、rev15）。

Phase 6：配信レポートは「既存データを読んで集計・表示する」フェーズ。配信実行（Phase 3）
で更新済みの Campaign.total_count / sent_count / failed_count、TrackingLink の click_count
/ total_access_count、ClickLog、DeliveryHistory、UnsubscribeLink を読んで都度集計する。

【設計原則】
  - 都度集計：Campaign に集計フィールド（bounce_count 等）を追加しない（§13.3）
  - N+1 対策：QuerySet annotate / aggregate / 集合演算で 1〜数クエリに収める
  - 配信停止数の出所は UnsubscribeLink（campaign × unsubscribed_at 非NULL）、
    DeliveryHistory.status='unsubscribed' は使わない（指示書 §2-A 確定事項）
  - CSV のスナップショット値（会社名・氏名・宛名・メアド）は DeliveryHistory 由来、
    現在状態（配信停止有無・バウンス有無）は Person / DeliveryHistory 由来の意図的混在
"""

from __future__ import annotations

from typing import Iterable, List, Dict, Any

from django.db.models import Count, Max, Q, Sum

from mailings.models import (
    Campaign,
    ClickLog,
    DeliveryHistory,
    TrackingLink,
    UnsubscribeLink,
)


# ----------------------------------------------------------------------
# CSV 出力項目（仕様書 §13.2、ユーザー選択方式）
# ----------------------------------------------------------------------

CSV_FIELD_PERSON_ID = "person_id"
CSV_FIELD_CONTACT_ID = "contact_id"
CSV_FIELD_ORGANIZATION = "organization"
CSV_FIELD_FULL_NAME = "full_name"
CSV_FIELD_SALUTATION = "salutation_name"
CSV_FIELD_EMAIL = "email"
CSV_FIELD_STATUS = "status"
CSV_FIELD_VALID_CLICKS = "valid_clicks"
CSV_FIELD_TOTAL_ACCESS = "total_access"
CSV_FIELD_LAST_CLICKED_AT = "last_clicked_at"
CSV_FIELD_IS_UNSUBSCRIBED = "is_unsubscribed"
CSV_FIELD_IS_BOUNCED = "is_bounced"
CSV_FIELD_BOUNCE_REASON = "bounce_reason"

# 順序付き定義（CSV ヘッダ / フォーム表示の順序）
CSV_AVAILABLE_FIELDS: List[tuple] = [
    (CSV_FIELD_PERSON_ID, "Person ID"),
    (CSV_FIELD_CONTACT_ID, "Contact ID"),
    (CSV_FIELD_ORGANIZATION, "会社名"),
    (CSV_FIELD_FULL_NAME, "氏名"),
    (CSV_FIELD_SALUTATION, "宛名"),
    (CSV_FIELD_EMAIL, "メアド"),
    (CSV_FIELD_STATUS, "配信ステータス"),
    (CSV_FIELD_VALID_CLICKS, "有効クリック回数"),
    (CSV_FIELD_TOTAL_ACCESS, "総アクセス回数"),
    (CSV_FIELD_LAST_CLICKED_AT, "最終クリック日時"),
    (CSV_FIELD_IS_UNSUBSCRIBED, "配信停止有無"),
    (CSV_FIELD_IS_BOUNCED, "バウンス有無"),
    (CSV_FIELD_BOUNCE_REASON, "バウンス理由"),
]
CSV_AVAILABLE_KEYS = {key for key, _ in CSV_AVAILABLE_FIELDS}


# ----------------------------------------------------------------------
# A. 集計値カード（§6.2.2 項目2 / §13.1）
# ----------------------------------------------------------------------


def get_campaign_summary(campaign: Campaign) -> Dict[str, int]:
    """配信レポートのカード集計値を返す（§13.1）。

    [性質] 準関数（DB 読み取りのみ）
    [入力] campaign: Campaign
    [出力] dict（total / sent / bounced / valid_clicks / total_access / unsubscribed）

    出所マップ：
      - total          : Campaign.total_count（フィールド参照、Phase 3 で更新済み）
      - sent           : Campaign.sent_count
      - bounced        : DeliveryHistory(campaign, status='bounced').count
      - valid_clicks   : TrackingLink(campaign).aggregate(Sum('click_count'))
      - total_access   : TrackingLink(campaign).aggregate(Sum('total_access_count'))
      - unsubscribed   : UnsubscribeLink(campaign, unsubscribed_at__isnull=False).count
                         （DeliveryHistory.status='unsubscribed' は使わない、指示書確定事項）
    """
    bounced = DeliveryHistory.objects.filter(
        campaign=campaign, status=DeliveryHistory.Status.BOUNCED
    ).count()

    click_agg = TrackingLink.objects.filter(campaign=campaign).aggregate(
        valid=Sum("click_count"), total=Sum("total_access_count")
    )
    valid_clicks = click_agg["valid"] or 0
    total_access = click_agg["total"] or 0

    unsubscribed = UnsubscribeLink.objects.filter(
        campaign=campaign, unsubscribed_at__isnull=False
    ).count()

    return {
        "total": campaign.total_count,
        "sent": campaign.sent_count,
        "bounced": bounced,
        "valid_clicks": valid_clicks,
        "total_access": total_access,
        "unsubscribed": unsubscribed,
    }


# ----------------------------------------------------------------------
# B. リンク別クリック数表（§6.2.2 項目3）
# ----------------------------------------------------------------------


def get_link_aggregates(campaign: Campaign) -> List[Dict[str, Any]]:
    """各 original_url ごとに有効クリック数・総アクセス数・ユニーククリックユーザー数を返す。

    [性質] 準関数（DB 読み取りのみ）
    [入力] campaign: Campaign
    [出力] List[dict]（original_url ごとに集計、original_url 昇順）
      - original_url: str
      - valid_clicks: 該当 URL の TrackingLink の click_count 合算
      - total_access: 該当 URL の TrackingLink の total_access_count 合算
      - unique_users: 該当 URL を有効クリックしたユニークな受信者数（person 単位重複排除）

    ユニーククリックユーザー数の算出：
      ClickLog(is_valid_click=True, tracking_link.original_url=X, tracking_link.campaign=C)
      から tracking_link.person をユニーク化してカウント。original_url ごとに値が変わる
      ため、TrackingLink を URL でグルーピングして個別集計する。
    """
    # original_url 単位で TrackingLink を集約。tracking_links は campaign 内では
    # (person, original_url) でユニークなので、original_url ごとに TrackingLink 群が
    # 並ぶ。click_count / total_access_count は Sum、unique_users は distinct person 数。
    results: List[Dict[str, Any]] = []
    qs = (
        TrackingLink.objects.filter(campaign=campaign)
        .values("original_url")
        .annotate(
            valid_clicks=Sum("click_count"),
            total_access=Sum("total_access_count"),
        )
        .order_by("original_url")
    )

    for row in qs:
        url = row["original_url"]
        # ユニーククリックユーザー数：当該 URL の TrackingLink を有効クリックした person を distinct
        unique_users = (
            ClickLog.objects.filter(
                tracking_link__campaign=campaign,
                tracking_link__original_url=url,
                is_valid_click=True,
            )
            .values("tracking_link__person")
            .distinct()
            .count()
        )
        results.append(
            {
                "original_url": url,
                "valid_clicks": row["valid_clicks"] or 0,
                "total_access": row["total_access"] or 0,
                "unique_users": unique_users,
            }
        )

    return results


# ----------------------------------------------------------------------
# C. 絞り込み済みコンタクト一覧（§6.2.3 / No.13〜15）
# ----------------------------------------------------------------------


def get_clicked_persons(campaign: Campaign):
    """有効クリックした受信者一覧（§6.2.3、Person 単位、重複排除）。

    [性質] 準関数
    [出力] List[dict]（person / last_action_at で並ぶ、last_action_at 降順）
      - person: Person（select_related primary_contact 済み）
      - last_action_at: 該当 campaign で当該 person の有効クリックの最終日時
                       （TrackingLink.last_clicked_at の max）
    """
    # campaign × person で TrackingLink を引き、有効クリックがある（click_count>0）
    # ものに絞って last_clicked_at の最大を取る。Person 単位の重複排除はここで成立。
    rows = (
        TrackingLink.objects.filter(campaign=campaign, click_count__gt=0)
        .values("person")
        .annotate(last_action_at=Max("last_clicked_at"))
        .order_by("-last_action_at")
    )
    return _hydrate_person_rows(rows, "last_action_at")


def get_bounced_persons(campaign: Campaign):
    """バウンスした受信者一覧（§6.2.3、Person 単位）。

    [性質] 準関数
    [出力] List[dict]
      - person, last_action_at（DeliveryHistory の updated 相当＝created_at で代用）
    """
    # DeliveryHistory は (campaign, person) でユニーク（§4.4 UniqueConstraint）。
    # status='bounced' のものを person 単位で取る。アクション日時は created_at を採用
    # （DeliveryHistory に bounced_at 専用フィールドはない、§10.4.1 ベストエフォート）。
    rows = (
        DeliveryHistory.objects.filter(
            campaign=campaign, status=DeliveryHistory.Status.BOUNCED
        )
        .values("person")
        .annotate(last_action_at=Max("created_at"))
        .order_by("-last_action_at")
    )
    return _hydrate_person_rows(rows, "last_action_at")


def get_unsubscribed_persons(campaign: Campaign):
    """配信停止した受信者一覧（§6.2.3、UnsubscribeLink 経由、Person 単位）。

    [性質] 準関数
    [出力] List[dict]
      - person, last_action_at（UnsubscribeLink.unsubscribed_at）
    """
    rows = (
        UnsubscribeLink.objects.filter(
            campaign=campaign, unsubscribed_at__isnull=False
        )
        .values("person")
        .annotate(last_action_at=Max("unsubscribed_at"))
        .order_by("-last_action_at")
    )
    return _hydrate_person_rows(rows, "last_action_at")


def _hydrate_person_rows(rows, action_key: str) -> List[Dict[str, Any]]:
    """person ID + last_action_at の組を Person オブジェクトに膨らませる（N+1 対策）。

    [性質] 準関数（DB 読み取り 1 回：Person + primary_contact を select_related で取得）
    """
    from persons.models import Person

    person_ids = [r["person"] for r in rows]
    if not person_ids:
        return []

    person_map = {
        p.pk: p
        for p in Person.objects.filter(pk__in=person_ids).select_related(
            "primary_contact"
        )
    }
    out: List[Dict[str, Any]] = []
    for r in rows:
        person = person_map.get(r["person"])
        if person is None:
            continue
        out.append({"person": person, "last_action_at": r[action_key]})
    return out


# ----------------------------------------------------------------------
# D. CSV 出力（§13.2 / No.16）
# ----------------------------------------------------------------------


def csv_rows(campaign: Campaign, selected_fields: Iterable[str]):
    """CSV ヘッダ行 + データ行を順に yield する（§13.2）。

    [性質] 準関数（DB 読み取り、yield ベースで大規模配信もメモリ抑制）
    [入力] campaign: Campaign
           selected_fields: ユーザー選択した CSV_AVAILABLE_KEYS の部分集合
    [出力] ジェネレータ：tuple(str, ...) の行列

    値の出所（指示書 §2-D / §13.2）：
      - スナップショット（配信時点固定）：会社名・氏名・宛名・メアド・配信ステータス・
        バウンス理由は DeliveryHistory 由来
      - 現在状態（意図的混在）：配信停止有無は Person.is_unsubscribed、バウンス有無は
        DeliveryHistory.status='bounced'（仕様書混在を維持）
      - 集計値（campaign × person で都度集計）：有効クリック回数 / 総アクセス回数 /
        最終クリック日時は TrackingLink から
    """
    # 未知のキーは黙って弾く（UI の改変で誤キーが来た場合の防御）
    selected = [k for k, _ in CSV_AVAILABLE_FIELDS if k in set(selected_fields)]
    if not selected:
        # 1 件も選ばれていない → ヘッダだけ空行を返す（呼び出し側で 400 にしてもよい）
        return

    # ヘッダ行
    label_map = dict(CSV_AVAILABLE_FIELDS)
    yield tuple(label_map[k] for k in selected)

    # DeliveryHistory を起点に person 単位で出力。同 campaign 内 (campaign, person) 一意。
    histories = (
        DeliveryHistory.objects.filter(campaign=campaign)
        .select_related("person", "contact")
        .order_by("created_at")
    )

    # person 単位の集計値を 1 クエリで先取り（N+1 回避）
    tl_aggregates = {
        row["person"]: row
        for row in TrackingLink.objects.filter(campaign=campaign)
        .values("person")
        .annotate(
            valid=Sum("click_count"),
            total=Sum("total_access_count"),
            last_clicked=Max("last_clicked_at"),
        )
    }

    for h in histories:
        person = h.person
        agg = tl_aggregates.get(person.pk, {})

        row_values = []
        for k in selected:
            if k == CSV_FIELD_PERSON_ID:
                row_values.append(str(person.pk))
            elif k == CSV_FIELD_CONTACT_ID:
                row_values.append(str(h.contact_id) if h.contact_id else "")
            elif k == CSV_FIELD_ORGANIZATION:
                row_values.append(h.organization_at_send)
            elif k == CSV_FIELD_FULL_NAME:
                row_values.append(h.full_name_at_send)
            elif k == CSV_FIELD_SALUTATION:
                row_values.append(h.salutation_name_at_send)
            elif k == CSV_FIELD_EMAIL:
                row_values.append(h.to_email)
            elif k == CSV_FIELD_STATUS:
                row_values.append(h.get_status_display())
            elif k == CSV_FIELD_VALID_CLICKS:
                row_values.append(str(agg.get("valid") or 0))
            elif k == CSV_FIELD_TOTAL_ACCESS:
                row_values.append(str(agg.get("total") or 0))
            elif k == CSV_FIELD_LAST_CLICKED_AT:
                last = agg.get("last_clicked")
                row_values.append(last.isoformat() if last else "")
            elif k == CSV_FIELD_IS_UNSUBSCRIBED:
                # 現在値（仕様書 §13.2、意図的にスナップショットでなく現在状態）
                row_values.append("1" if person.is_unsubscribed else "0")
            elif k == CSV_FIELD_IS_BOUNCED:
                row_values.append(
                    "1" if h.status == DeliveryHistory.Status.BOUNCED else "0"
                )
            elif k == CSV_FIELD_BOUNCE_REASON:
                row_values.append(
                    h.error_message
                    if h.status == DeliveryHistory.Status.BOUNCED
                    else ""
                )
            else:
                row_values.append("")
        yield tuple(row_values)
