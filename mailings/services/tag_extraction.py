"""タグ抽出サービス（仕様書 v1.6 §11.2.3 / §11.4.1）。

リスト作成時のタグ抽出を担う。同一カテゴリ内 OR・カテゴリ間 AND の評価ロジックを
ここに集約する（§11.2.3）。検索条件との AND 合成は §11.4.2.1 段階線引きにより
Phase 1 では「受け口（search_conditions 引数）」だけ用意して中身は空通し。

実装上の注意（§11.2.3）：
  - 同一カテゴリ内 OR は `tag__in=[...]`
  - カテゴリ間 AND は「カテゴリごとに絞り込みを連鎖」で実装
  - 単純な `tag__in=[全タグ]` で書くと全カテゴリが OR になり抽出が壊れる
"""

from collections import defaultdict

from django.db.models import Count, Q

from persons.models import Person
from persons.services.person_search import search_persons
from tags.models import Tag


def extract_persons_by_tags(tag_ids, *, search_conditions=None):
    """選択されたタグ ID 群から Person QuerySet を抽出する（§11.2.3 / §11.4.1）。

    [性質] 準関数（DB 読み取りのみ、副作用なし）
    [入力] tag_ids: iterable[UUID] or list[str]（カテゴリ横断のタグ ID 集合）
           search_conditions: dict-like or None（検索条件の受け口、Phase 1 では中身空）
    [出力] QuerySet[Person]（status='active' のみ、重複排除済み）

    動作：
      1. タグ ID 群をカテゴリ別にグルーピング
      2. カテゴリごとに `tag_assignments__tag__in=[同カテゴリのタグ群]` で OR フィルタ
      3. カテゴリ間は QuerySet を連鎖させて AND 合成
      4. 検索条件は §11.4.2.1 段階線引き：search_conditions が指定されれば
         search_persons() でタグ抽出結果をさらに絞り込む（中身が空でも空通し）
      5. 配信対象は active のみ（merged / archived は除外、§11.5.1 / §7.3 手順 3）

    カテゴリ未選択（tag_ids 空）の場合は active な Person 全体を返す
    （仕様書 §11.1.3「カテゴリ未選択は無条件通過」と整合）。
    """
    if tag_ids:
        cat_to_tags = _group_tags_by_category(tag_ids)
        qs = Person.objects.filter(status=Person.Status.ACTIVE)
        for tags_in_cat in cat_to_tags.values():
            qs = qs.filter(tag_assignments__tag__in=tags_in_cat)
        qs = qs.distinct()
    else:
        qs = Person.objects.filter(status=Person.Status.ACTIVE)

    # §11.4.2.1 段階線引き：検索条件との AND 合成（Phase 1 は空通し受け口のみ）。
    if search_conditions:
        # search_persons() は QuerySet を返す。タグ抽出結果と Person.id で intersect。
        narrowed = search_persons(
            search_conditions,
            default_statuses=(Person.Status.ACTIVE,),
        )
        qs = qs.filter(pk__in=narrowed.values("pk"))

    return (
        qs.select_related("primary_contact")
        .annotate(
            active_count=Count(
                "contact",
                filter=Q(contact__status__in=["primary", "active"]),
            )
        )
        .order_by("-updated_at", "-created_at")
    )


def _group_tags_by_category(tag_ids):
    """[性質] 純関数。タグ ID 群をカテゴリ ID 別にグルーピングして dict で返す。

    Tag.category（FK）の値で集約する。存在しない ID は自動的に除外される。
    戻り値: dict[category_id, list[Tag]]。順序保持。
    """
    cat_to_tags = defaultdict(list)
    tags = Tag.objects.filter(pk__in=list(tag_ids)).select_related("category")
    for tag in tags:
        cat_to_tags[tag.category_id].append(tag)
    return cat_to_tags


def count_persons_by_tags(tag_ids, *, search_conditions=None):
    """[性質] 準関数。extract_persons_by_tags() の結果件数だけを返す軽量版。

    プレビュー件数表示用（リスト作成画面の AJAX、§6.2.4）。
    """
    return extract_persons_by_tags(
        tag_ids, search_conditions=search_conditions
    ).count()


# ======================================================================
# Phase 1c-β 新関数（仕様書 rev5 §4.1 / §4.2、集合演算の拡張）
# ======================================================================


def extract_persons_by_tag_conditions(conditions):
    """conditions dict から拡張集合演算で active な Person QuerySet を返す（§4.2）。

    [性質] 準関数（DB 読み取りのみ、副作用なし）
    [入力] conditions: dict
        {
          'categories': [
            {
              'category_id': UUID,
              'operator': 'OR' or 'AND',
              'include_tag_ids': [UUID, ...],
              'exclude_tag_ids': [UUID, ...],
            },
            ...
          ],
          'global_exclude_tag_ids': [UUID, ...],
        }
    [出力] QuerySet[Person]（status='active' のみ、distinct 済み）

    判定ルール（§4.1.4）：
      - include_tag_ids が空のカテゴリは抽出条件に含めない
      - 有効カテゴリが 1 つもなく global_exclude も空 → 0 件
      - 有効カテゴリが 1 つもなく global_exclude あり → 全 Person − 横断除外
      - category_id 重複は呼び出し側（preview-v2 view）でガード済み前提

    is_unsubscribed は抽出層で除外しない（§4.2.1 / §9.2-30、配信実行層の責務）。

    ORM 実装方針（§4.2.1 / §9.2-37、最大の事故ポイント）：
      - カテゴリ内 AND は filter チェーンで実装（`tag_assignments__tag__in` は OR 動作になるため厳禁）
      - カテゴリ内 OR は `tag_assignments__tag__in=[...]`
      - カテゴリ内 NOT / カテゴリ間 NOT は `.exclude(tag_assignments__tag__in=[...])`
      - 複数 join による重複行は最終 .distinct() で除く
    """
    qs = Person.objects.filter(status=Person.Status.ACTIVE)

    categories = (conditions or {}).get("categories") or []
    global_exclude = list((conditions or {}).get("global_exclude_tag_ids") or [])

    # 有効カテゴリ（include_tag_ids が非空）のみを評価対象に絞る（§4.1.4）。
    effective_categories = [
        cat for cat in categories if cat.get("include_tag_ids")
    ]

    if not effective_categories and not global_exclude:
        # 有効カテゴリも横断除外もない → 抽出 0 件（§4.1.4）。
        return Person.objects.none()

    for cat in effective_categories:
        include_ids = list(cat.get("include_tag_ids") or [])
        exclude_ids = list(cat.get("exclude_tag_ids") or [])
        operator = cat.get("operator") or "OR"
        if operator == "AND":
            # AND：タグ 1 件ごとに .filter() を重ねる（§9.2-37、tag_assignments__tag__in は使わない）。
            for tag_id in include_ids:
                qs = qs.filter(tag_assignments__tag_id=tag_id)
        else:
            # OR：同カテゴリ内のいずれかを持つ。
            qs = qs.filter(tag_assignments__tag_id__in=include_ids)
        if exclude_ids:
            # カテゴリ内 NOT：同カテゴリ内の除外タグを持つ Person を除外。
            qs = qs.exclude(tag_assignments__tag_id__in=exclude_ids)

    if global_exclude:
        # カテゴリ間 NOT：横断除外タグを 1 つでも持つ Person を全体から除外。
        qs = qs.exclude(tag_assignments__tag_id__in=global_exclude)

    return qs.distinct()
