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

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from config.constants import DUPLICATE_CHECK_FIELDS, DuplicateMergeReason
from contacts.models import ContactFieldConfidence
from duplicates.models import DuplicateCandidate, PersonMergeLog
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


def Mark_as_Different_Person(candidate, form, user):
    """別人判定の本体（仕様書 §13.4.5 / §11.4.6 / §10.7.2）。

    [性質] 副作用あり（DB 書込：DuplicateCandidate の review_status 遷移 +
           ActionLog 1 件作成）
    [入力] candidate: DuplicateCandidate（判定対象の重複候補）
           form: MergeForm（cleaned_data['review_result'] / cleaned_data['note'] を参照）
           user: User（判定者、reviewed_by および ActionLog.user に記録）
    [出力] None

    処理範囲：当該 1 candidate のみ。他の pending 候補・duplicate_checked_at・
    Person のステータスには触れない（C-1+2 指示書「処理範囲」）。

    処理内容（§9.3.1 マージ系の 10 ステップとは別フロー）：
      1. transaction.atomic() で関数全体をラップ
      2. candidate.mark_as_different_person(user, review_result, note) で状態遷移
         （review_status='different_person' / review_result / reviewed_by /
         reviewed_at / note を 1 save で更新）
      3. candidate.record_different_person_action(user) で ActionLog 記録
         （data={"different_reason": ...} は X-5 のルールで self.review_result から
         組み立てられる）

    `form.cleaned_data["note"]` は任意（既定 ""）。`mark_as_different_person` は
    None を空文字に正規化するが、サービス側で .get(default="") して明示的に str を
    渡す。
    """
    review_result = form.cleaned_data["review_result"]
    note = form.cleaned_data.get("note", "")

    with transaction.atomic():
        candidate.mark_as_different_person(user, review_result, note)
        candidate.record_different_person_action(user)


def Execute_Merge_Only(candidate, surviving_person, merged_person, form, user):
    """マージのみ実行（フィールド修正なし、仕様書 §9.3.1 / §13.4.5 / §11.4.6）。

    [性質] 副作用あり（DB 書込：複数モデルを同一トランザクションで更新）
    [入力] candidate: DuplicateCandidate（マージ起点の重複候補）
           surviving_person: Person（マージで残る側）
           merged_person: Person（マージで統合される側）
           form: MergeForm（get_merge_reason() / cleaned_data['review_result'] /
                 cleaned_data['note'] を参照）
           user: User（マージ実行者、PersonMergeLog.executed_by および ActionLog.user
                 に記録）
    [出力] None
    [例外] ValidationError（surviving 側、または additional_role 時の merged 側 primary に
           非 high フィールドが残る場合。トランザクション開始前に送出するので DB は不変）

    フィールド修正がないため、§9.3.1 の手順 3（フィールド反映）と手順 4
    （ContactFieldConfidence 記録）はスキップする。Execute_Merge_with_Updates との
    切り分け基準は「フィールド修正の有無」（§9.4.4）。

    処理順序：
      A. トランザクション開始前のバリデーション（§9.3.1 手順 2）
         1. surviving_person.primary_contact が DUPLICATE_CHECK_FIELDS 全 high
         2. ADDITIONAL_ROLE が merge_reason（list[str]）に含まれるときは
            merged_person.primary_contact も同条件
      B. transaction.atomic() 内で §9.3.1 手順 5〜10 を実行
         5. PersonMergeLog.create() でログ作成
         5'. duplicate_candidate / note を merge_log にセットして 2 回目 save
         6. merged_person.transfer_contacts_to(surviving_person, merge_reason)
         7. merged_person.mark_as_merged(surviving_person)
         8. PersonMergeLog.lock_past_logs(merged_person)
         10前. candidate.mark_as_merged(user, review_result, note)
              （recover 内部の防御コードでフォールバック merged 化されると reviewer 情報
              が欠落するため、明示的に先に呼ぶ。レビュー回答 §B 参照）
         9. recover_duplicate_candidates(merged_person, surviving_person)
         9末. surviving_person.primary_contact.duplicate_checked_at = now()
         10. merge_log.record_merge_action(user) で ActionLog 記録

    マジックストリング 'additional_role' を直接比較せず、`DuplicateMergeReason.ADDITIONAL_ROLE`
    で判定する。`DUPLICATE_CHECK_FIELDS` も config/constants.py から参照する
    （C-1+2 指示書「定数・enum の使用」）。
    """
    merge_reason = form.get_merge_reason()
    review_result = form.cleaned_data["review_result"]
    note = form.cleaned_data.get("note", "")
    surviving_primary = surviving_person.primary_contact

    # ---- atomic を冒頭から包む構造（D-4d-1 第 3 弾 §2 修正項目 4） ----
    # マージ画面の確認 CB 全 ON 検証（MergeForm.clean()）は通過済みだが、CFC の DB 状態
    # は未更新のため、ここで surviving 側 primary の未確認 low/mid CFC を一括 confirmed
    # 化してから is_all_field_confidence_high を見る。merged 側 CFC は触らない
    # （additional_role のときの merged 側バリデーションは現状の挙動を維持）。
    with transaction.atomic():
        # ---- 0. CFC 確定処理（confirm CB 全 ON 検証を DB に反映） ----
        unconfirmed_field_names = list(
            ContactFieldConfidence.objects.filter(
                contact=surviving_primary,
                confirmed_at__isnull=True,
            ).values_list("field_name", flat=True)
        )
        if unconfirmed_field_names:
            ContactFieldConfidence.mark_fields_as_confirmed(
                surviving_primary, unconfirmed_field_names, user
            )

        # ---- A. バリデーション（§9.3.1 手順 2） ----
        if not surviving_primary.is_all_field_confidence_high(
            DUPLICATE_CHECK_FIELDS
        ):
            raise ValidationError(
                "surviving_person の primary_contact に全 high でないフィールドが残っています。"
            )
        if DuplicateMergeReason.ADDITIONAL_ROLE in merge_reason:
            if not merged_person.primary_contact.is_all_field_confidence_high(
                DUPLICATE_CHECK_FIELDS
            ):
                raise ValidationError(
                    "additional_role でのマージでは merged_person の primary_contact も "
                    "全 high が必要です。"
                )

        # ---- B. マージ実行（§9.3.1 手順 5〜10） ----
        # 手順 5: PersonMergeLog 作成 + 2 回目 save で duplicate_candidate / note セット
        merge_log = PersonMergeLog.create(surviving_person, merged_person, user)
        merge_log.duplicate_candidate = candidate
        merge_log.note = note
        merge_log.save(
            update_fields=["duplicate_candidate", "note", "updated_at"]
        )

        # 手順 6: merged_person 配下 Contact を surviving_person に付け替え
        merged_person.transfer_contacts_to(surviving_person, merge_reason)

        # 手順 7: merged_person のステータス遷移（status='merged' / merged_into セット）
        merged_person.mark_as_merged(surviving_person)

        # 手順 8: 過去のマージログを locked 化
        PersonMergeLog.lock_past_logs(merged_person)

        # 手順 10 前段: 当該 candidate を merged 化（recover の前に明示、レビュー回答 §B）
        candidate.mark_as_merged(user, review_result, note)

        # 手順 9: recover 処理（第三者 Person との pending DC を invalidated → 再復帰）
        recover_duplicate_candidates(merged_person, surviving_person)

        # 手順 9 末: surviving 側 primary の duplicate_checked_at を now() に更新
        surviving_primary = surviving_person.primary_contact
        surviving_primary.duplicate_checked_at = timezone.now()
        surviving_primary.save(
            update_fields=["duplicate_checked_at", "updated_at"]
        )

        # 手順 10: ActionLog 記録（data={"merge_reason": ...} は X-5 のルールで
        # merge_log.duplicate_candidate.review_result から組み立てられる）
        merge_log.record_merge_action(user)


def Execute_Merge_Undo(merge_log, form, user):
    """マージ復元の本体（仕様書 §9.5 / §13.4.5 / §11.4.6、D-4f-2 で note 連携）。

    [性質] 副作用あり（DB 書込：複数モデルを同一トランザクションで更新）
    [入力] merge_log: PersonMergeLog（復元対象、status='undoable' 必須）
           form: MergeUndoForm（cleaned_data['note'] を ActionLog に転記）
           user: User（復元実行者、PersonMergeLog.undone_by および ActionLog.user
                 に記録）
    [出力] None
    [例外] ValidationError（merge_log が undoable でない場合。トランザクション開始前
           に送出するので DB は不変）

    処理順序：
      A. トランザクション開始前のバリデーション
         - merge_log.is_undoable() == False なら ValidationError
      B. transaction.atomic() 内で §9.5.2 手順 1〜7 を実行
         手順 1〜4: merged_person 由来の Contact 群を previous_* から復元
                   （person=surviving_person AND previous_person=merged_person で
                    絞り込み、status / person を previous 値に戻し、previous_* を
                    NULL にクリア）
         手順 5: merged_person.mark_as_active()
         手順 6: merged_person 側のみ primary_contact 同期（§9.3.2、レビュー回答 §A）
                  surviving_person 側はマージ前後で primary が変わらないため同期不要
         手順 7: merge_log.mark_as_undone(user)
         DC 後処理: merged_person.primary_contact.duplicate_checked_at = None
                   （次回 cron で merged_person 起点の DC が再生成される。
                   §12.8.4 補助レコードに完璧な整合性を求めず UX 優先）
         ActionLog: merge_log.record_undo_action(user)

    DuplicateCandidate / ContactFieldConfidence は一切触らない（§12.8.4 / §9.5.3）。

    補足（restored_primary の二重 save）：
      手順 2 で元 primary Contact に status='primary' を save し、手順 6 の
      set_primary_contact() 内 Step 2 でも status='primary' を再 save する形になる。
      同一トランザクション内なので実用上問題なく、コードの読みやすさを優先する判断
      （レビュー回答「補足」）。
    """
    from contacts.models import Contact  # 循環 import を避けるため遅延 import

    # ---- A. バリデーション（atomic 外） ----
    if not merge_log.is_undoable():
        raise ValidationError(
            f"merge_log {merge_log.id} は復元できません（status='{merge_log.status}'）。"
        )

    surviving_person = merge_log.surviving_person
    merged_person = merge_log.merged_person
    note = form.cleaned_data.get("note", "")

    # ---- B. トランザクション内（§9.5.2 手順 1〜7） ----
    with transaction.atomic():
        # 手順 1〜4: Contact のロールバック（merged_person 由来の Contact 群）
        rollback_targets = Contact.objects.filter(
            person=surviving_person,
            previous_person=merged_person,
        )
        for contact in rollback_targets:
            contact.person = contact.previous_person
            contact.status = contact.previous_status
            contact.previous_person = None
            contact.previous_status = None
            contact.save(
                update_fields=[
                    "person",
                    "status",
                    "previous_person",
                    "previous_status",
                    "updated_at",
                ]
            )

        # 手順 5: merged_person を active 化
        merged_person.mark_as_active()

        # 手順 6: primary_contact 同期（merged_person 側のみ、レビュー回答 §A）
        #         surviving_person.primary_contact はマージ前後で変化しないため同期不要
        merged_primary_after = merged_person.contact_set.filter(
            status=Contact.Status.PRIMARY
        ).first()
        if merged_primary_after is not None:
            merged_person.set_primary_contact(merged_primary_after)

        # 手順 7: PersonMergeLog の状態遷移
        merge_log.mark_as_undone(user)

        # DC 後処理: merged_person.primary_contact.duplicate_checked_at = None
        # （次回 cron で再生成される。§12.8.4）
        if merged_primary_after is not None:
            merged_primary_after.duplicate_checked_at = None
            merged_primary_after.save(
                update_fields=["duplicate_checked_at", "updated_at"]
            )

        # ActionLog 記録（D-4f-2：form.cleaned_data['note'] を data['note'] に転記）
        merge_log.record_undo_action(user, note)
