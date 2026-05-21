"""重複検出のメインサービス（仕様書 §8.10 / v0.1.5 第2章 / 第5章）。

公開関数：

- get_persons_confirmed_as_different(person)
  → 過去にユーザーが「別人」と確定判定した相手 Person のうち、現在 active なものを返す
     準関数（v0.1.5 §5.2）。
- find_duplicate_contacts(contact, excluded_persons=None)
  → 重複候補の (Contact, score, rank) タプル列を返す準関数（v0.1.5 第2章）。

設計思想（B 前半指示書 §3）：

- find_duplicate_contacts は履歴を参照しない（§3.1 / v0.1.5 §5.4.1）。フィールド値の
  比較とスコア計算のみ。「過去に別人判定済み」の履歴判断は呼び出し元
  （generate_duplicate_candidates_for_contact）が get_persons_confirmed_as_different()
  を経由して取得し、excluded_persons 引数として渡す。
- excluded_persons はオプション引数（§3.2 / v0.1.5 §5.4.5）。cron 経由（履歴あり）
  と ContactCreateView 経由（履歴なし）の両方が同じ関数を共有するためデフォルトは None。
  関数を分割しない。
"""

from django.db.models import Q

from contacts.models import Contact
from duplicates.models import DuplicateCandidate
from duplicates.services.duplicate_score import determine_score_and_rank
from persons.models import Person


def get_persons_confirmed_as_different(person):
    """過去に「別人」と確定判定された相手 Person のうち active なものを返す。

    仕様書 v0.1.5 §5.2 / §8.9。

    [性質] 準関数（DB 読み取りのみ）
    [入力] person: Person（重複チェックの起点 Person）
    [出力] list[Person]（status='active' のみ。0 件なら空リスト）

    入力 person を含む `review_status='different_person'` な DuplicateCandidate を
    探し、相手側 Person ID を抽出して active のもののみを返す。0 件の場合は空リスト。

    実装方針（v0.1.5 §5.2.2 / §5.2.3）：
      - 重複は自然に排除される（同一相手が複数回別人判定されていても Person.id の
        set 化と Person.objects.filter(id__in=...) で 1 つになる）。distinct() 不要
      - リストの順序は不定
      - 「現在 active な Person のみ」とする理由：通常運用での find_duplicate_contacts
        の絞り込み対象が active に限定されているため、merged / archived を含めても
        除外対象として実際には使われない（§5.2.4）

    SQLite 3.51.2 planner bug：本関数は OR フィルタ + partial unique index に対する
    `.exists()` を使わない（QuerySet 反復で ID 抽出のみ）ので回避策は不要。
    """
    candidates = DuplicateCandidate.objects.filter(
        Q(person_a=person) | Q(person_b=person),
        review_status=DuplicateCandidate.ReviewStatus.DIFFERENT_PERSON,
    ).only("person_a_id", "person_b_id")

    other_ids = set()
    for cand in candidates:
        if cand.person_a_id != person.id:
            other_ids.add(cand.person_a_id)
        if cand.person_b_id != person.id:
            other_ids.add(cand.person_b_id)

    if not other_ids:
        return []

    return list(
        Person.objects.filter(id__in=other_ids, status=Person.Status.ACTIVE)
    )


def find_duplicate_contacts(contact, excluded_persons=None):
    """重複候補の (Contact, score, rank) タプル列を返す（仕様書 §8.10 / v0.1.5 第2章）。

    [性質] 準関数（DB 読み取りのみ。書き込みなし）
    [入力] contact: Contact（重複チェック対象。status='primary' / Person.status='active'
              が前提だが、本関数では検証しない。呼び出し側の責務）
           excluded_persons: list[Person] | None（オプション。Person オブジェクトの
              リスト。デフォルト None＝除外なし）
    [出力] list[tuple]：各要素は (duplicate_contact: Contact, score: int, rank: str)。
           rank='none' は含めない。候補 0 件なら空リスト。

    比較対象：DB 全体の status='primary' かつ Person.status='active' な Contact
    （自身を除く、excluded_persons に含まれる Person も除外）。

    絞り込み（v0.1.5 §2.2 / §2.3）：
      - フルネーム / メール / 携帯のいずれかが完全一致（OR）
      - 入力 contact の各フィールドが空文字 / NULL なら、その条件は OR 句から外す
      - excluded_persons は `if excluded_persons:` で判定（None も空リストも falsy）

    パフォーマンス（v0.1.5 §2.5）：
      - 候補取得時に prefetch_related('confidences') を**無条件**に呼ぶ
      - 呼び出し元（cron / ContactCreateView）の判別は行わない
      - cron 経由の N+1 リスクを確実に潰し、ContactCreateView 経由でも性能上の害はない

    設計思想（B 前半指示書 §3.1 / v0.1.5 §5.4.1）：本関数は履歴参照しない。
    DuplicateCandidate / PersonMergeLog 等のテーブルは読まない。履歴判断は
    excluded_persons 引数経由で受け取るのみ。
    """
    # 比較対象の絞り込み（status と自身除外）
    qs = Contact.objects.filter(
        status=Contact.Status.PRIMARY,
        person__status=Person.Status.ACTIVE,
    ).exclude(id=contact.id)

    # excluded_persons は None / 空リストどちらも「除外なし」として同じ扱い（v0.1.5 §2.3）
    if excluded_persons:
        qs = qs.exclude(person__id__in=[p.id for p in excluded_persons])

    # フルネーム / メール / 携帯の OR 絞り込み（空文字は条件から外す、v0.1.5 §2.3）
    or_q = Q()
    has_filter = False
    if contact.full_name:
        or_q |= Q(full_name=contact.full_name)
        has_filter = True
    if contact.email:
        or_q |= Q(email=contact.email)
        has_filter = True
    if contact.mobile_phone:
        or_q |= Q(mobile_phone=contact.mobile_phone)
        has_filter = True

    # フルネーム / メール / 携帯がすべて空なら、絞り込みヒットはあり得ないので空リストを返す。
    # 全件 SELECT を避けるための早期 return（仕様書の意図と整合）。
    if not has_filter:
        return []

    qs = qs.filter(or_q).prefetch_related("confidences")

    # スコア計算とランク判定。rank='none' は除外（v0.1.5 §2.6 / §2.8.2）
    results = []
    for candidate in qs:
        score, rank = determine_score_and_rank(contact, candidate)
        if rank != DuplicateCandidate.Rank.NONE:
            results.append((candidate, score, rank))
    return results
