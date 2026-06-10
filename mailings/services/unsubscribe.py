"""配信停止サービス関数（仕様書 v1.6 §9.5、rev14）。

A-1 確定の核心 3 関数：
  - get_same_person_unit(person)
      → ある Person が含まれる同一人物ユニット（root + 配下の全 merged）を返す。
        循環検知付き（独自判断で省略しない、§9.5.3 必須要件）。
  - unsubscribe_person(target_person, source, source_email, user=None, note='')
      → ユニット全員に Unsubscribe レコード作成 + is_unsubscribed=True 同期。
        bulk_create / bulk update でユニットサイズ非依存の定数クエリ数（§9.5.3）。
  - cancel_unsubscribe(target_person, user, note='')
      → ユニット全員のアクティブ Unsubscribe を一括論理削除 + is_unsubscribed=False 同期。

【設計原則】
  - bulk_create / bulk update 必須（ループ内クエリ禁止）
  - 循環検知 visited/seen セット必須（データ不整合での無限ループ防御）
  - Unsubscribe モデルに save() オーバーライド・post_save シグナルを入れない
    （本サービス関数が bulk_create を使うため、シグナル経由で is_unsubscribed を
     更新する設計だと bulk_create で発火せず不整合になる、§9.5.3 末尾）
  - 呼び出し元：UnsubscribePageView / UnsubscribeConfirmView（受信者意思）、
    管理者代行 View、Person.set_unsubscribed_by_admin。
    バウンス処理からは呼ばない（バウンスは SuppressedEmail のみ、§10.0）
"""

from __future__ import annotations

from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from mailings.models import Unsubscribe
from persons.models import Person


def get_same_person_unit(person: Person) -> List[Person]:
    """ある Person が属する同一人物ユニットを返す（§9.5.1 / §9.5.3）。

    [性質] 準関数（DB 読み取りのみ）
    [入力] person: Person（active でも merged でもよい）
    [出力] List[Person]（root + 配下の全 merged、root が先頭）

    [例外] ValueError（merged_into の循環参照を検出した場合）

    アルゴリズム：
      1. person から merged_into を再帰的に辿って surviving root を見つける
         （person が active なら person 自身が root）
      2. root を起点に merged_into=root の Person を再帰的に集める
         （多段マージにも対応：A→B→C のとき root=C は B/A を含む）

    循環検知：visited / seen セットによる防御。データ不整合（merged_into が
    循環している等）があっても無限ループに陥らない（§9.5.3 必須要件）。
    """
    # 1. root を再帰的に辿る（循環検知付き）
    visited = set()
    root = person
    while root.merged_into is not None:
        if root.id in visited:
            raise ValueError(f"merged_into cycle detected at {root.id}")
        visited.add(root.id)
        root = root.merged_into

    # 2. root 配下の全 merged を BFS で集める（循環検知付き）
    unit: List[Person] = [root]
    pending: List[Person] = [root]
    seen = {root.id}
    while pending:
        current = pending.pop()
        children = Person.objects.filter(
            merged_into=current, status=Person.Status.MERGED
        )
        for child in children:
            if child.id in seen:
                continue
            seen.add(child.id)
            unit.append(child)
            pending.append(child)
    return unit


def unsubscribe_person(
    target_person: Person,
    source: str,
    source_email: str,
    user=None,
    note: str = "",
) -> List[Person]:
    """target_person が属する同一人物ユニット全員を配信停止する（§9.5.3、bulk 操作版）。

    [性質] 副作用あり（DB 書込：Unsubscribe bulk_create + Person.is_unsubscribed bulk update）
    [入力]
      target_person: Person（停止意思の対象）
      source: str（'unsubscribe_link' / 'manual' / 'api'、別表 C.24）
      source_email: str（操作元のメアド、Contact が消えても残す）
      user: CustomUser | None（管理者代行時のみ非 None、現状の Unsubscribe モデル
            は actor を持たないが、将来拡張・呼び出し元 View での ActionLog 記録に
            使うため引数で受ける）
      note: str（管理者代行時のメモ）
    [出力] List[Person]（処理対象になった同一人物ユニット全員、呼び出し元の
           ActionLog 記録等の参考用に返す）

    【性能】 ユニットサイズに関係なく定数クエリ数で完結：
      - get_same_person_unit 内の root 辿り + 子集めで O(unit_depth + breadth) 回
        （これは bulk 化できない木構造の辿り、現実的にはユニット深さ≪10）
      - 本関数本体は 3 クエリ：active 取得 1 + bulk_create 1 + bulk update 1

    冪等性：既に active な Unsubscribe を持つ Person はスキップする（重複作成しない）。
    呼び出し元が冪等性を気にせず複数回呼んでも DB が破綻しない（受信者の二度押し対策）。

    [注意] user 引数は現状 Unsubscribe モデルに保存されない（cancelled_by のみ存在、
    §4.7）。記録は呼び出し元 View が ActionLog に行う。
    """
    unit = get_same_person_unit(target_person)
    unit_ids = [p.id for p in unit]

    with transaction.atomic():
        # 1 クエリ：既に active な Unsubscribe を持つ Person ID を一括取得
        already_unsubscribed_person_ids = set(
            Unsubscribe.objects.filter(
                person_id__in=unit_ids,
                cancelled_at__isnull=True,
            ).values_list("person_id", flat=True)
        )

        # 1 クエリ：未停止の Person だけまとめて INSERT
        new_records = [
            Unsubscribe(
                person=person,
                source=source,
                source_email=source_email,
                note=note,
            )
            for person in unit
            if person.id not in already_unsubscribed_person_ids
        ]
        if new_records:
            Unsubscribe.objects.bulk_create(new_records)

        # 1 クエリ：ユニット全員の is_unsubscribed を一括 True に
        Person.objects.filter(id__in=unit_ids).update(is_unsubscribed=True)

    return unit


def cancel_unsubscribe(
    target_person: Person, user=None, note: str = ""
) -> List[Person]:
    """target_person が属する同一人物ユニット全員の配信停止を解除する（§9.5.3 / §9.5.5）。

    [性質] 副作用あり（DB 書込：Unsubscribe bulk update + Person.is_unsubscribed bulk update）
    [入力]
      target_person: Person
      user: CustomUser | None（解除を行った管理者、Unsubscribe.cancelled_by に保存）
      note: str（補足説明、現状は Unsubscribe モデルには反映しないため呼び出し元の
            ActionLog 記録向け）
    [出力] List[Person]（処理対象になった同一人物ユニット全員）

    【性能】 ユニットサイズに関係なく 2 クエリで完結（bulk update 2 件）。
    全停止のみ実装（部分解除は v1.7+、§9.5.5）。

    冪等性：active な Unsubscribe が無い Person については Unsubscribe 側 update が
    0 件になるだけで何も起きない。Person.is_unsubscribed は False に揃える。
    """
    unit = get_same_person_unit(target_person)
    unit_ids = [p.id for p in unit]

    with transaction.atomic():
        # 1 クエリ：ユニット全員の active な Unsubscribe を一括論理削除
        Unsubscribe.objects.filter(
            person_id__in=unit_ids,
            cancelled_at__isnull=True,
        ).update(
            cancelled_at=timezone.now(),
            cancelled_by=user,
        )

        # 1 クエリ：ユニット全員の is_unsubscribed を一括 False に
        Person.objects.filter(id__in=unit_ids).update(is_unsubscribed=False)

    return unit
