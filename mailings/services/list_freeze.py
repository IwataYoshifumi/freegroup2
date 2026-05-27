"""リスト保存サービス（仕様書 v1.6 rev14.1 §11.3 / §11.4.3 / §4.12）。

タグ抽出＋検索条件の結果として得た Person 集合を MailingListMember として物理保存
する。リスト作成・再抽出の両方で使う共通処理（**rev14.1: 凍結発動はここでは行わない**）。

凍結タイミング（rev14.1 §11.4.3）：
  - リスト保存（本関数）では `members_frozen_at` をセットしない（流動・未凍結のまま）
  - `members_frozen_at` は Campaign の status が `scheduled` → `sending` に遷移した
    瞬間に配信実行サービス（§4.2.1、Phase 2 で実装）がセットする
"""

from django.db import transaction

from mailings.models import MailingList, MailingListMember


def freeze_members(mailing_list, persons, user):
    """Person 集合を MailingListMember として一括保存する（rev14.1 §11.3 / §11.4.3）。

    [性質] 副作用あり（DB 書込：既存メンバー全削除 → 新規 bulk_create を 1 トランザク
            ションで実行。**rev14.1: members_frozen_at はセットしない**）
    [入力] mailing_list: MailingList（保存先、保存済み）
           persons: iterable[Person]（保存対象 Person）
           user: CustomUser（added_by に記録）
    [出力] int（保存したメンバー数）

    リスト作成時・再抽出時の両方で使う。リスト再作成（メンバー上書き）方式：
      1. 既存 MailingListMember を全削除（CASCADE で消える）
      2. 引数 persons を bulk_create で再作成
      ※ `mailing_list.members_frozen_at` には触らない（rev14.1）

    関数名は履歴互換のため `freeze_members` を維持（rev14.1 で意味的には「保存」だが、
    Phase 2 で配信実行サービスから本関数を間接利用する想定）。
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
