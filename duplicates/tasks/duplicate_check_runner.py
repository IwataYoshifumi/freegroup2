"""重複チェックの cron / タスク層実装（仕様書 v0.1.5 第3章 / 第4章）。

公開関数：

- Run_Generate_Duplicate_Candidates(limit=100)
  → cron 起動の上位関数。2 段階ロック方式 + 3 段再チェックで対象 Contact を順次処理し、
     完了後に ActionLog を 1 件書き込む（v0.1.5 §4.1〜§4.9）。

- generate_duplicate_candidates_for_contact(contact)
  → 1 Contact 単位の下位関数。1 つの transaction.atomic() 内で、find_duplicate_contacts
     を呼び bulk_create で書き込む（v0.1.5 §3.1〜§3.7）。

設計上の最重要事項：

1. **2 段階ロック方式**（v0.1.5 §4.4）
   ステップ A：ID リスト取得（atomic 外、ロックなし）
   ステップ B：1 件ずつ atomic 内で select_for_update + 3 段再チェック
   全体を 1 つの atomic で囲むと「1 件失敗で全件ロールバック」になり §4.5 と矛盾する。

2. **3 段再チェック**（v0.1.5 §4.4.3）
   ロック取得後に duplicate_checked_at / Contact.status / Person.status を再確認。
   ステップ A 〜 B 間の状態変化（他 worker 完了・編集・マージ・archive）を捕捉し、
   partial unique constraint 違反（IntegrityError）の経路を排除する。

3. **ActionLog はループ外（特例）**（v0.1.5 §4.8.4）
   一般原則は「業務処理と同トランザクション、書き込み失敗で全体ロールバック」だが、
   本関数の ActionLog は「複数の独立した個別 Contact 処理を集約した cron 実行サマリ」
   であり業務処理本体ではない。個別 Contact の業務処理は各々の atomic で確定済み。

4. **bulk_create に ignore_conflicts=True を付けない**（v0.1.5 §3.6.1）
   「異常を隠さない」設計思想。3 段再チェックで競合経路は排除されているため
   IntegrityError は実運用で発生しない想定。万一発生した場合は当該 contact のみ
   ロールバック → duplicate_checked_at が NULL のまま → 次回 cron で再試行される。
"""

import logging
import uuid

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from accounts.services import get_excluded_persons_for_user_linked
from actionlogs.models import ActionLog
from contacts.models import Contact
from duplicates.models import DuplicateCandidate
from duplicates.services.duplicate_detection import (
    find_duplicate_contacts,
    get_persons_confirmed_as_different,
)
from persons.models import Person

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 上位関数（cron 起動）
# ----------------------------------------------------------------------

def Run_Generate_Duplicate_Candidates(limit=100):
    """重複チェックの cron 起動上位関数（仕様書 v0.1.5 §4.1〜§4.9）。

    [性質] 副作用あり（複合処理：DB 読み書き + ActionLog 書き込み + 下位関数呼び出し）
    [入力] limit: int（1 回の実行で処理する Contact 件数の上限、デフォルト 100）
    [出力] None（戻り値で結果は返さない、ActionLog に集約結果を書き込む）

    処理フロー（v0.1.5 §4.3）：
      (1) started_at 記録
      (2) 対象 Contact の ID リスト取得（ステップ A、atomic 外、ロックなし）
      (3) ID リストを 1 件ずつループ（ステップ B、各 contact につき atomic + ロック取得 +
          3 段再チェック + generate_duplicate_candidates_for_contact 呼び出し）
      (4) 全 contact 処理完了後、ActionLog に集約結果を 1 件書き込む（ループ外、§4.8.4）

    エラーハンドリング（v0.1.5 §4.5）：個別 contact のエラーはログ + カウントしてスキップ、
    ループは続行。失敗した contact は duplicate_checked_at が NULL のまま次回 cron で再試行。
    """
    started_at = timezone.now()

    # 集計用カウンタ
    processed_count = 0
    errors = 0
    # 起点 Person（contact.person.id）の集合。hit_contacts はループ後に
    # new_candidates と突き合わせて Python 側で算出する。
    # 理由：ループ内で OR フィルタ + partial unique index 条件 + .count() を呼ぶと
    # SQLite 3.51.2 で internal query planner error が発生する（申し送りメモ §5）。
    started_person_ids = set()

    # search_target_count（処理対象になりうる総数、§4.6）
    search_target_count = Contact.objects.filter(
        duplicate_checked_at__isnull=True,
        status=Contact.Status.PRIMARY,
        person__status=Person.Status.ACTIVE,
    ).count()

    # ----- ステップ A：対象 Contact の ID リスト取得（atomic 外、ロックなし、§4.4.1）
    contact_ids = list(
        Contact.objects.filter(
            duplicate_checked_at__isnull=True,
            status=Contact.Status.PRIMARY,
            person__status=Person.Status.ACTIVE,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:limit]
    )

    # ----- ステップ B：1 件ずつ atomic + ロック取得 + 3 段再チェック（§4.4.2）
    for contact_id in contact_ids:
        try:
            with transaction.atomic():
                contact = (
                    Contact.objects.select_for_update(
                        skip_locked=True, of=("self",)
                    )
                    .select_related(
                        "person",
                        "business_card__original_image__user",
                        "created_by",
                    )
                    .prefetch_related("confidences")
                    .filter(id=contact_id)
                    .first()
                )

                # ロック取得失敗 / 対象消失（first() が None を返す）
                if contact is None:
                    continue

                # 3 段再チェック（§4.4.3）
                if contact.duplicate_checked_at is not None:
                    continue
                if contact.status != Contact.Status.PRIMARY:
                    continue
                if contact.person.status != Person.Status.ACTIVE:
                    continue

                generate_duplicate_candidates_for_contact(contact)
                started_person_ids.add(contact.person_id)
                processed_count += 1
        except Exception as exc:
            logger.exception(
                "Run_Generate_Duplicate_Candidates: contact_id=%s の処理でエラー: %s",
                contact_id,
                exc,
            )
            errors += 1
            continue

    # ----- ループ後の集計（candidates_generated / rank_breakdown / hit_contacts）
    # cron 実行中に作成された pending を created_at__gte=started_at で抽出
    # （OR フィルタを使わないので SQLite planner bug の対象外）。
    new_candidates_qs = DuplicateCandidate.objects.filter(
        created_at__gte=started_at,
        review_status=DuplicateCandidate.ReviewStatus.PENDING,
    )
    # hit_contacts は Python 側で集計（OR + count を避ける、申し送りメモ §5）
    new_candidates_pairs = list(
        new_candidates_qs.values_list("person_a_id", "person_b_id")
    )
    hit_persons = set()
    for pa_id, pb_id in new_candidates_pairs:
        if pa_id in started_person_ids:
            hit_persons.add(pa_id)
        elif pb_id in started_person_ids:
            hit_persons.add(pb_id)
    hit_contacts = len(hit_persons)
    candidates_generated = len(new_candidates_pairs)
    rank_counts = dict(
        new_candidates_qs.values("rank")
        .annotate(c=Count("rank"))
        .values_list("rank", "c")
    )
    rank_breakdown = {
        DuplicateCandidate.Rank.EXACT_MATCH: rank_counts.get(
            DuplicateCandidate.Rank.EXACT_MATCH, 0
        ),
        DuplicateCandidate.Rank.POSSIBLE_HIGH: rank_counts.get(
            DuplicateCandidate.Rank.POSSIBLE_HIGH, 0
        ),
        DuplicateCandidate.Rank.POSSIBLE_MID: rank_counts.get(
            DuplicateCandidate.Rank.POSSIBLE_MID, 0
        ),
        DuplicateCandidate.Rank.POSSIBLE_LOW: rank_counts.get(
            DuplicateCandidate.Rank.POSSIBLE_LOW, 0
        ),
    }

    duration_seconds = (timezone.now() - started_at).total_seconds()
    status_str = _determine_status(processed_count, errors)

    # ----- ActionLog 書き込み（ループ外、トランザクション外、§4.8.4 / §6 特例）
    # フィールド名は A-2e で改名済み：extra → data、diff フィールドは削除（指示書 §5）
    ActionLog.record(
        user=None,
        action="executed",
        content_object=None,
        object_repr="check_duplicates",
        data={
            "search_target_count": search_target_count,
            "processed_count": processed_count,
            "hit_contacts": hit_contacts,
            "candidates_generated": candidates_generated,
            "rank_breakdown": rank_breakdown,
            "errors": errors,
            "duration_seconds": duration_seconds,
            "status": status_str,
        },
    )


# ----------------------------------------------------------------------
# 下位関数（1 Contact 単位）
# ----------------------------------------------------------------------

def generate_duplicate_candidates_for_contact(contact):
    """1 Contact 単位の重複候補生成（仕様書 v0.1.5 §3.1〜§3.7）。

    [性質] 副作用あり（DB 書き込み）
    [入力] contact: Contact
       前提（呼び出し側が保証、本関数では検証しない）：
         - status='primary' / person.status='active'
         - select_for_update(skip_locked=True) でロック取得済み
         - prefetch_related('confidences') 済み
         - select_related('business_card__original_image__user', 'created_by') 済み
    [出力] None（DB 書き込みで完結）

    処理フロー（v0.1.5 §3.3）：
      (1) get_persons_confirmed_as_different(contact.person) で別人判定済み相手を取得
      (2) find_duplicate_contacts(contact, excluded_persons=...) で候補タプル列を取得
      (3) 空ならスキップ、(7) へ
      (4) assigned_to を 1 度だけ計算（§3.4.1、ループ前で 1 回）
      (5) DuplicateCandidate インスタンス群を構築（group_id はバッチ内でランクごとに統一）
      (6) bulk_create（ignore_conflicts=True は付けない、§3.6.1）
      (7) contact.duplicate_checked_at = timezone.now() で limited save
      (8) コミット

    トランザクション境界（§3.5）：本関数内で transaction.atomic() を張る。上位関数
    Run_Generate_Duplicate_Candidates の 1 contact ループ内 atomic にネストして
    savepoint として動作する（§3.5 末尾の二重ネスト atomic 注を参照）。
    """
    with transaction.atomic():
        # (1) different_person 判定済み相手 Person のリスト
        excluded_persons = list(get_persons_confirmed_as_different(contact.person))

        # (1.5) v1.5.0: User 紐付き Person 同士のマージ候補を除外（仕様書 §13.5）
        # contact.person が User 紐付きなら、他の User 紐付き active Person をすべて除外。
        # contact.person が未紐付きなら空リストが返るので no-op。
        excluded_persons.extend(get_excluded_persons_for_user_linked(contact.person))

        # (2) 重複候補のタプル列
        tuples = find_duplicate_contacts(contact, excluded_persons=excluded_persons)

        # (2.5) 事前フィルタ（X-3）：既に pending DC として contact.person と組まれている
        # 相手 Person を集合で取得し、同 Person を相手とする候補を除外する。partial unique
        # constraint(person_a, person_b, where review_status='pending') 違反を未然回避する。
        # 先勝ち方針：score / rank の差異に関わらず既存の pending を尊重（仕様書 §12.8.4
        # 「補助レコードに完璧な整合性を求めず UX 優先」と整合）。
        # SQLite 3.51.2 planner bug 回避のため Q(person_a=p)|Q(person_b=p) を使わず、
        # person_a / person_b 別々の 2 クエリで集合を作る（申し送りメモ §5）。
        if tuples:
            pending_status = DuplicateCandidate.ReviewStatus.PENDING
            already_paired_person_ids = set(
                DuplicateCandidate.objects
                .filter(person_a=contact.person, review_status=pending_status)
                .values_list("person_b_id", flat=True)
            )
            already_paired_person_ids |= set(
                DuplicateCandidate.objects
                .filter(person_b=contact.person, review_status=pending_status)
                .values_list("person_a_id", flat=True)
            )
            if already_paired_person_ids:
                tuples = [
                    (cand_contact, score, rank)
                    for cand_contact, score, rank in tuples
                    if cand_contact.person_id not in already_paired_person_ids
                ]

        # (3) 空ならスキップ、(7) へ
        if tuples:
            # (4) assigned_to を 1 度だけ計算
            assigned_to = _determine_assigned_to(contact)

            # (5) DuplicateCandidate インスタンス群を構築
            # group_id のバッチ内キャッシュ：同じ起点 Person・同ランクの複数候補に
            # 同じ group_id を付ける（§5.3.2「同じバッチ内で...同じ group_id が付く」）
            group_id_cache = {}
            instances = []
            for cand_contact, score, rank in tuples:
                group_id = _resolve_group_id(contact.person, rank, group_id_cache)
                person_a, person_b = _order_persons(
                    contact.person, cand_contact.person
                )
                instances.append(
                    DuplicateCandidate(
                        group_id=group_id,
                        person_a=person_a,
                        person_b=person_b,
                        score=score,
                        rank=rank,
                        review_status=DuplicateCandidate.ReviewStatus.PENDING,
                        review_result=[],
                        note="",
                        assigned_to=assigned_to,
                        reviewed_by=None,
                        reviewed_at=None,
                    )
                )

            # (6) bulk_create（ignore_conflicts=True は付けない、§3.6.1）
            DuplicateCandidate.objects.bulk_create(instances)

        # (7) duplicate_checked_at を更新（候補ゼロ件でも必ず実行、§3.3 手順 7）
        contact.duplicate_checked_at = timezone.now()
        contact.save(update_fields=["duplicate_checked_at"])

        # (8) atomic ブロック終了でコミット


# ----------------------------------------------------------------------
# 内部ヘルパー
# ----------------------------------------------------------------------

def _determine_assigned_to(contact):
    """assigned_to の決定ロジック（v0.1.5 §3.4.1）。

    [性質] 純関数（contact のフィールド参照のみ。呼び出し側で select_related 済み前提）
    [入力] contact: Contact
    [出力] User | None（ユーザー削除等で NULL になり得る、v0.1 では NULL を許容）

    OCR 由来（business_card_id is not None）は BusinessCard.original_image.user を採用。
    手動入力（business_card_id is None）は Contact.created_by を採用。
    両者とも NULL になり得る（FK SET_NULL）。
    """
    if contact.business_card_id is not None:
        return contact.business_card.original_image.user
    return contact.created_by


def _resolve_group_id(person, rank, batch_cache):
    """group_id の決定ロジック（v0.1.5 §5.3.2）。

    [性質] 準関数（DB 読み取り：DuplicateCandidate を OR 検索）
    [入力] person: Person（起点 Person、contact.person）
           rank: str（DuplicateCandidate.Rank の値）
           batch_cache: dict[rank -> UUID]（同一 contact 処理内で再利用）
    [出力] UUID

    ロジック：
      1. batch_cache に rank があればそれを返す（バッチ内キャッシュ、§5.3.2 末尾）
      2. 起点 Person を含む（OR 検索）かつ同 rank かつ pending な既存
         DuplicateCandidate を検索し、見つかればその group_id を再利用
      3. 見つからなければ uuid.uuid4() で新規発行
      4. batch_cache に保存して返す

    バッチ内キャッシュは bulk_create 前に同一 contact 起点の同ランク候補が
    別々の UUID を持たないようにするために必須（DB にはまだ書き込まれていないため、
    DB 検索だけでは同バッチ内の整合は取れない）。
    """
    if rank in batch_cache:
        return batch_cache[rank]

    existing = DuplicateCandidate.objects.filter(
        Q(person_a=person) | Q(person_b=person),
        rank=rank,
        review_status=DuplicateCandidate.ReviewStatus.PENDING,
    ).first()

    group_id = existing.group_id if existing else uuid.uuid4()
    batch_cache[rank] = group_id
    return group_id


def _order_persons(p1, p2):
    """person_a / person_b の順序ルール（v1.4.1 §4.7.1 / v0.1.5 §3.4）。

    [性質] 純関数（フィールド参照のみ）
    [入力] p1, p2: Person
    [出力] (person_a, person_b) のタプル

    順序ルール：
      - created_at が古い方が person_a
      - 同時刻なら id（UUID 文字列比較）の小さい方が person_a
    """
    if p1.created_at < p2.created_at:
        return p1, p2
    if p1.created_at > p2.created_at:
        return p2, p1
    if str(p1.id) < str(p2.id):
        return p1, p2
    return p2, p1


def _determine_status(processed_count, errors):
    """ActionLog の status 判定ルール（v0.1.5 §4.6）。

    [性質] 純関数（数値計算のみ）
    [入力] processed_count: int, errors: int
    [出力] str（'success' / 'partial' / 'failed'）

    判定ルール：
      - processed_count == 0 または errors == processed_count → 'failed'
      - errors == 0 かつ processed_count > 0 → 'success'
      - errors > 0 かつ processed_count > errors → 'partial'

    v0.1 暫定、運用後に確定。
    """
    if processed_count == 0 or errors == processed_count:
        return "failed"
    if errors == 0:
        return "success"
    return "partial"
