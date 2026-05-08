"""マージ実行サービス内共通ヘルパー（仕様書 v1.4.2 §13.4.3）。

本モジュールには以下 2 関数を実装する：

- `invalidate_pending_candidates(contact)` …… §12.7 専用ヘルパー。
  Contact 編集時に、紐づく Person の pending DuplicateCandidate を invalidated 化し、
  contact.duplicate_checked_at を NULL に戻す。
- `recover_duplicate_candidates(merged_person, surviving_person)` …… §12.8 recover 一本化。
  マージ実行直後に呼ばれ、merged_person 縁故の他 pending DC を invalidated 化したうえで、
  surviving_person 起点の pending DC として再復帰させる。

両関数とも呼び出し元（`contact.fix` / Execute_Merge_Only / Execute_Merge_with_Updates 等）の
`transaction.atomic()` 内で動かす前提。本モジュール内では atomic を切らない（§12.7.2 末尾、
§9.3.1 手順9）。

仕様書根拠：
- §12.7 / §12.7.2 invalidate_pending_candidates
- §12.8.1 recover 一本化の方針
- §12.8.2 ⚠ レビュアー注意（スコアコピーで正しい）
- §12.8.3 recover 処理の手順 1〜3（手順 4 は呼び出し元の責務）
- §12.8.4 スコアコピーが論理的に正しい理由
- §13.4.3 サービス内共通（merge_executor.py）
"""

from __future__ import annotations

from duplicates.models import DuplicateCandidate
from persons.models import Person


def invalidate_pending_candidates(contact):
    """contact が紐づく Person の pending DuplicateCandidate を invalidated 化する。

    [性質] 副作用あり（DB書込：DuplicateCandidate.review_status 一括更新 +
           Contact.duplicate_checked_at の NULL 化）
    [入力] contact: Contact（紐づく Person を起点に処理）
    [出力] None
    [前提] 呼び出し元の `transaction.atomic()` 内で実行されること（§12.7.2 末尾）。
           本関数では atomic を切らない（§13.4.3 / 確定論点 §3.4）。
    [仕様書] §12.7.2 関数定義の手順 1〜3 を実装。

    処理内容：
      1. contact.person を起点に person_a または person_b に持つ DuplicateCandidate を抽出
      2. review_status='pending' のものを 'invalidated' に変更（一括 update）
      3. contact.duplicate_checked_at = NULL に戻す（次回 cron で再判定対象）

    review_status='merged' / 'different_person' のレコードはそのまま（過去判定の尊重、§12.7）。

    ActionLog は書き込まない（呼び出し元 `contact.fix` 等の責務、確定論点 §3.3）。
    """
    person = contact.person

    # 手順 1〜2: 当該 Person を片側に持つ pending DC を invalidated 化。
    # SQLite 3.51.2 の planner bug 回避のため、`Q | Q` で OR を組み立てず person_a /
    # person_b 別々に UPDATE を発行する（partial unique index + OR + UPDATE で
    # `internal query planner error` が出る既知問題。コード君への申し送りメモ §5）。
    pending = DuplicateCandidate.ReviewStatus.PENDING
    invalidated = DuplicateCandidate.ReviewStatus.INVALIDATED
    DuplicateCandidate.objects.filter(
        person_a=person,
        review_status=pending,
    ).update(review_status=invalidated)
    DuplicateCandidate.objects.filter(
        person_b=person,
        review_status=pending,
    ).update(review_status=invalidated)

    # 手順 3: contact.duplicate_checked_at を NULL に戻す（次回 cron で再判定）
    contact.duplicate_checked_at = None
    contact.save(update_fields=["duplicate_checked_at", "updated_at"])


def recover_duplicate_candidates(merged_person, surviving_person):
    """マージ実行直後に DuplicateCandidate を再構成する（recover 一本化、§12.8）。

    [性質] 副作用あり（DB書込：DuplicateCandidate の review_status 更新 + 新規 pending DC 作成）
    [入力] merged_person: Person（マージで統合される側。`status='merged'` および
            `merged_into = surviving_person` が呼び出し元で既にセット済みであること）
           surviving_person: Person（マージで残る側、active）
    [出力] None
    [前提] 呼び出し元の `transaction.atomic()` 内で実行されること（§9.3.1 手順 9 / §12.7.2）。
           本関数では atomic を切らない（確定論点 §3.4）。
    [前提] 呼び出し元が既に `merged_person.mark_as_merged(surviving_person)` を呼んで
           merged_person の `merged_into_id` を確定させていること（`create_recovered_from`
           が `merged_into_id == surviving_person.id` を頼りに「どちらが merged 側か」を
           判定するため。§10.7.1 / §12.8.3）。
    [仕様書] §12.8.3 手順 1〜3 を実装。手順 4（duplicate_checked_at の更新）は呼び出し元
             （Execute_Merge_Only / Execute_Merge_with_Updates）の責務（確定論点 §3.1）。

    処理内容：
      1. merged_person を含む pending DC を取得
         - 「当該マージの DC」（相手側 = surviving_person）と「他の DC」（相手側 = 第三者）に分類
      2. 当該マージの DC を 'merged' に変更（呼び出し元が `candidate.mark_as_merged(...)` を
         事前に呼んでいる正常パスでは、この時点で当該 DC は既に pending ではないため本処理は
         自然に no-op になる。冪等性のための防御実装）
      3. 他の pending DC を invalidated 化したうえで、相手側 Person が active のものについて
         `DuplicateCandidate.create_recovered_from(old_candidate, surviving_person)` 経由で
         surviving_person 起点の pending DC を新規作成する。score / rank / matched_fields /
         group_id は old_candidate からコピー（§12.8.4。再スコア計算は禁止、§12.8.2）

    再復帰の除外条件（§12.8.3 末尾）：
      - 相手側 Person が active 以外（merged / archived）なら再復帰させない
      - 既に (surviving, 相手) ペアの pending DC が存在する場合も再復帰させない
        （partial UniqueConstraint(person_a, person_b, where review_status='pending') 衝突回避）

    確定論点：
      - スコア再計算しない（§3.6 / §12.8.4 / §12.8.2）
      - DuplicateCandidate.objects.create() は直接呼ばない（§3.7、create_recovered_from 経由）
      - has_field_updates 引数を取らない（§3.2、recover 一本化、§12.8.1）
      - ActionLog 書き込まない（§3.3、呼び出し元の責務）
      - invalidate_pending_candidates を内部で呼ばない（§3.5、独自クエリ）
    """
    # 手順 1 の前段: merged_person を片側に持つ pending DC を一括取得し、
    # 当該マージの DC と「他の DC」に振り分け。
    # SQLite 3.51.2 の planner bug 回避のため、Q(person_a) | Q(person_b) で OR を
    # 組まず person_a / person_b 別々に取得して結合する（コード君への申し送りメモ §5）。
    pending = DuplicateCandidate.ReviewStatus.PENDING
    pending_a = list(
        DuplicateCandidate.objects.filter(
            person_a=merged_person,
            review_status=pending,
        ).select_related("person_a", "person_b")
    )
    pending_b = list(
        DuplicateCandidate.objects.filter(
            person_b=merged_person,
            review_status=pending,
        ).select_related("person_a", "person_b")
    )
    pending_dcs = pending_a + pending_b

    merge_dc = None
    other_dcs = []
    for dc in pending_dcs:
        # merged_person と対をなす相手の id を特定
        other_id = dc.person_b_id if dc.person_a_id == merged_person.id else dc.person_a_id
        if other_id == surviving_person.id:
            merge_dc = dc
        else:
            other_dcs.append(dc)

    # 手順 2: 当該マージの DC を 'merged' に変更（呼び出し元が `mark_as_merged` 済みなら
    # 上のクエリでは既に取得されないため、merge_dc is None となり本処理は自然に no-op）。
    if merge_dc is not None:
        merge_dc.review_status = DuplicateCandidate.ReviewStatus.MERGED
        merge_dc.save(update_fields=["review_status", "updated_at"])

    # 手順 1 の本体: 「他の pending DC」を invalidated 化（一括 update）。
    if other_dcs:
        DuplicateCandidate.objects.filter(
            pk__in=[dc.pk for dc in other_dcs],
        ).update(
            review_status=DuplicateCandidate.ReviewStatus.INVALIDATED,
        )

    # 手順 3: 第三者 Person ごとに recover（pending DC を新規作成）。
    # other_dcs はメモリ上の Python オブジェクト。DB 上は invalidated 化済みだが、
    # create_recovered_from は score / rank / group_id / merged_into_id しか参照しないため
    # 影響なし。
    for old_dc in other_dcs:
        # 第三者 Person を特定
        if old_dc.person_a_id == merged_person.id:
            other_person = old_dc.person_b
        else:
            other_person = old_dc.person_a

        # 除外条件 1: 相手側 Person が active 以外なら再復帰させない（§12.8.3 末尾）。
        # other_person は select_related で取得済みなので追加クエリ不要。
        if other_person.status != Person.Status.ACTIVE:
            continue

        # 除外条件 2: 既に (surviving, 相手) ペアの pending DC があれば skip
        # （partial UniqueConstraint 衝突回避。person_a / person_b は ID 順で正規化保存）。
        if surviving_person.id < other_person.id:
            check_a, check_b = surviving_person, other_person
        else:
            check_a, check_b = other_person, surviving_person
        already_pending = (
            DuplicateCandidate.objects
            .filter(
                person_a=check_a,
                person_b=check_b,
                review_status=DuplicateCandidate.ReviewStatus.PENDING,
            )
            .first() is not None
        )
        if already_pending:
            continue

        # 新規 pending DC を作成（score / rank / group_id は old_dc からコピー、
        # 再計算しない、§12.8.4）。直接 objects.create() を呼ばず、必ず
        # create_recovered_from を経由する（§3.7 / §10.7.1）。
        DuplicateCandidate.create_recovered_from(old_dc, surviving_person)
