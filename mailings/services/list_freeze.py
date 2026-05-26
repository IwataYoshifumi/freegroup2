"""リスト凍結保存サービス（仕様書 v1.6 §11.3 / §11.4.3 / §4.12）。

タグ抽出＋検索条件の結果として得た Person 集合を MailingListMember として物理保存
（凍結）する。リスト作成・再抽出の両方で使う共通処理。

凍結後のメンバー操作（Phase 1）：
  - メンバーを増やす方向 → 実装しない（凍結思想に反する、§11.3.1）
  - メンバーを減らす方向（対象外） → 物理削除で実装、`members_frozen_at` は更新しない
"""

from django.db import transaction
from django.utils import timezone

from mailings.models import MailingList, MailingListMember


def freeze_members(mailing_list, persons, user):
    """Person 集合を MailingListMember として一括凍結保存する（§11.3.1 / §11.4.3）。

    [性質] 副作用あり（DB 書込：既存メンバー全削除 → 新規 bulk_create →
            members_frozen_at 更新を 1 トランザクションで実行）
    [入力] mailing_list: MailingList（凍結先、保存済み）
           persons: iterable[Person]（凍結対象 Person）
           user: CustomUser（added_by に記録）
    [出力] int（凍結したメンバー数）

    リスト作成時・再抽出時の両方で使う。リスト再作成（メンバー上書き）方式：
      1. 既存 MailingListMember を全削除（CASCADE で消える）
      2. 引数 persons を bulk_create で再作成
      3. `mailing_list.members_frozen_at = now()` を保存

    `members_frozen_at` は「凍結された時点」の記録（§4.12）。Phase 1 での
    「対象外」操作（個別物理削除）では更新しないが、本関数の凍結保存では更新する
    （新規 / 再抽出の両方で「この時点で凍結し直した」を記録する）。
    """
    persons_list = list(persons)
    with transaction.atomic():
        MailingListMember.objects.filter(mailing_list=mailing_list).delete()
        members = [
            MailingListMember(
                mailing_list=mailing_list,
                person=person,
                added_by=user,
            )
            for person in persons_list
        ]
        if members:
            MailingListMember.objects.bulk_create(members)
        mailing_list.members_frozen_at = timezone.now()
        mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
    return len(persons_list)


def get_or_create_singleton_mailing_config():
    """[性質] 副作用あり（DB 書込：未存在なら id=1 で作成）。

    MailingConfig（シングルトン、§4.13）の初回アクセス時の自動作成（発注書 §3-3）。
    返り値は MailingConfig インスタンス（必ず id=1）。

    本関数は mailings.services.list_freeze と概念は別だが、Phase 1b の小規模ヘルパー
    として services 階層に置く（独立モジュール化は不要、後フェーズで肥大化したら分割）。
    """
    from mailings.models import MailingConfig  # 循環 import 回避のため遅延 import

    config, _created = MailingConfig.objects.get_or_create(
        id=1,
        defaults={
            "company_name": "",
            "company_address": "",
            "unsubscribe_contact": "",
        },
    )
    return config
