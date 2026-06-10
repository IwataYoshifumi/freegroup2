"""配信宛先抽出サービス（仕様書 v1.6 §7.3、rev14）。

`extract_recipients(campaign) -> QuerySet[Person]`：Campaign が指す mailing_list の
凍結メンバーに対し、Person/Contact 側の安全フィルタを適用して配信対象を返す。

【rev12 で確定の前提】 Campaign.mailing_list は必須 FK（NOT NULL）。タグ評価は
リスト作成時に済んでおり、配信時には MailingListMember.person をそのまま使う
（動的タグ評価は行わない、§11 章 / §7.3）。

【重大警告（§7.3 手順 5）】 SuppressedEmail フィルタは ON/OFF 不可・常時 ON 固定。
ハードバウンス済みメアドへの送信を継続するとドメインレピュテーション毀損の
重大事故になるため、apply_unsubscribe_filter の値に関係なく必ず実行する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Subquery

from mailings.models import MailingListMember, SuppressedEmail
from persons.models import Person

if TYPE_CHECKING:
    from mailings.models import Campaign


def extract_recipients(campaign: "Campaign"):
    """配信宛先 Person を抽出する（§7.3 の 7 手順、手順 2 は欠番）。

    [性質] 準関数（DB 読み取りのみ、副作用なし、純関数寄り）
    [入力] campaign: Campaign（mailing_list は必須 FK で NOT NULL、§4.2）
    [出力] QuerySet[Person]（select_related("primary_contact") 済み）

    処理順：
      1. mailing_list の凍結メンバー取得（MailingListMember.person、§11）
      2. （欠番、rev12 でタグ条件評価を削除）
      3. Person.status='active' フィルタ（merged / archived を除外）
      4. Person.is_unsubscribed=False フィルタ
         （apply_unsubscribe_filter=True のときのみ、§7.8.4）
      5. SuppressedEmail フィルタ（常時 ON、Subquery で N+1 回避）
      6. primary_contact.email が空でない
      7. primary_contact が NULL でない（archived で primary_contact=NULL のケースのカバー）
    """
    # 手順 1：mailing_list の凍結メンバーから Person を取得
    member_person_ids = MailingListMember.objects.filter(
        mailing_list=campaign.mailing_list
    ).values("person_id")

    qs = Person.objects.filter(id__in=Subquery(member_person_ids))

    # 手順 3：Person.status='active' に絞る
    qs = qs.filter(status="active")

    # 手順 4：apply_unsubscribe_filter が True のときだけ is_unsubscribed=False で絞る
    # （False のときはこのステップをスキップして配信停止者にも送る、§7.8.4）。
    if campaign.apply_unsubscribe_filter:
        qs = qs.filter(is_unsubscribed=False)

    # 手順 5：SuppressedEmail フィルタ（常時 ON、ON/OFF 不可、§7.3 重大警告）
    # primary_contact.email が active な SuppressedEmail に該当する Person を除外。
    # Subquery で N+1 を回避（N=5000 × M=1000 SuppressedEmail でも単一クエリ）。
    suppressed_emails = SuppressedEmail.objects.filter(
        cancelled_at__isnull=True
    ).values("email")
    qs = qs.exclude(primary_contact__email__in=Subquery(suppressed_emails))

    # 手順 6 / 7：primary_contact が NULL でない、email が空でない
    qs = qs.exclude(primary_contact__isnull=True).exclude(primary_contact__email="")

    return qs.select_related("primary_contact")
