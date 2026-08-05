"""Person 検索サービス（v1.6 仕様書 §11.4 / §11.4.2.1）。

v1.6 Phase 1b で `persons/views.py:PersonListView.get_queryset()` から検索ロジックを
切り出して新設。Phase 1b 進め方論点 A-1 確定（同じ検索ロジックをリスト作成画面と
検索結果一括タグ付け画面で再利用するためのサービス層化）。

`search_persons()` は v1.4.2 §13.2.8（真のモデル横断はサービス層）と整合し、
仕様書 §7.3 `extract_recipients()` の `QuerySet[Person]` 返却方針とも揃う。
"""

from django.db.models import Count, Q, Value
from django.db.models.functions import Replace

from persons.models import Person

# 検索可能なテキスト項目（v1.5.0 既存 PersonListView と同じ 7 項目）。
SEARCH_PARAMS = (
    "name",
    "organization",
    "department",
    "title",
    "email",
    "tel",
    "address",
)

VALID_STATUSES = ("active", "merged", "archived")


def search_persons(params, *, default_statuses=("active",)):
    """検索条件パラメータから Person の QuerySet を返す（純粋に絞り込みのみ）。

    [性質] 準関数（DB 読み取りのみ、副作用なし）
    [入力] params: QueryDict もしくは dict 様（request.GET 等を受ける）
           default_statuses: tuple[str]（searched=1 が無いときに使う status の既定値、
                             既定は ("active",)）
    [出力] QuerySet[Person]（遅延評価、list 化しない）

    既存 PersonListView の挙動と完全互換：
      - searched=1 が無ければ status='active' のみ表示
      - searched=1 があれば params["status"] のチェック値（不正値は捨てる、空は 0 件）
      - 7 項目（name / organization / department / title / email / tel / address）は
        primary_contact 経由で icontains フィルタ
      - tel は personal_phone / mobile_phone / personal_fax の OR
      - select_related("primary_contact") + annotate(active_count) を付与
      - 並び順は -updated_at, -created_at

    Phase 1b 注意：本関数はあくまで Phase 1 で既存挙動を切り出した純粋 refactoring。
    タグ抽出（カテゴリ内 OR・カテゴリ間 AND）は別関数 `extract_persons_by_tags`
    （mailings.services.tag_extraction）が担当する。
    """
    selected = _resolve_statuses(params, default_statuses=default_statuses)
    if not selected:
        return Person.objects.none()

    qs = (
        Person.objects.filter(status__in=selected)
        .select_related("primary_contact")
        .annotate(
            active_count=Count(
                "contact",
                filter=Q(contact__status__in=["primary", "active"]),
            )
        )
    )

    qs = _apply_text_filters(qs, params)
    return qs.order_by("-updated_at", "-created_at")


def _resolve_statuses(params, *, default_statuses):
    """[性質] 純関数。searched=1 が無ければ default、あればチェック値（不正値除外）。"""
    if _get(params, "searched") != "1":
        return list(default_statuses)
    return [s for s in _getlist(params, "status") if s in VALID_STATUSES]


def _clean_search_query(val):
    if not val:
        return ""
    return val.replace("　", "").replace(" ", "").strip()


def _apply_text_filters(qs, params):
    """[性質] 準関数。7 項目の icontains フィルタを QuerySet に適用して返す（氏名・会社・部署・役職・住所はスペース正規化）。"""
    name = _clean_search_query(_get(params, "name"))
    if name:
        qs = qs.annotate(
            _clean_full_name=Replace(
                Replace("primary_contact__full_name", Value("　"), Value("")),
                Value(" "),
                Value(""),
            )
        ).filter(_clean_full_name__icontains=name)

    organization = _clean_search_query(_get(params, "organization"))
    if organization:
        qs = qs.annotate(
            _clean_organization=Replace(
                Replace("primary_contact__organization", Value("　"), Value("")),
                Value(" "),
                Value(""),
            )
        ).filter(_clean_organization__icontains=organization)

    department = _clean_search_query(_get(params, "department"))
    if department:
        qs = qs.annotate(
            _clean_department=Replace(
                Replace("primary_contact__department", Value("　"), Value("")),
                Value(" "),
                Value(""),
            )
        ).filter(_clean_department__icontains=department)

    title = _clean_search_query(_get(params, "title"))
    if title:
        qs = qs.annotate(
            _clean_title=Replace(
                Replace("primary_contact__title", Value("　"), Value("")),
                Value(" "),
                Value(""),
            )
        ).filter(_clean_title__icontains=title)

    email = _strip_value(params, "email")
    if email:
        qs = qs.filter(primary_contact__email__icontains=email)

    tel = _strip_value(params, "tel")
    if tel:
        qs = qs.filter(
            Q(primary_contact__personal_phone__icontains=tel)
            | Q(primary_contact__mobile_phone__icontains=tel)
            | Q(primary_contact__personal_fax__icontains=tel)
        )

    address = _clean_search_query(_get(params, "address"))
    if address:
        qs = qs.annotate(
            _clean_address=Replace(
                Replace("primary_contact__address", Value("　"), Value("")),
                Value(" "),
                Value(""),
            )
        ).filter(_clean_address__icontains=address)

    return qs


def _get(params, key, default=""):
    """[性質] 純関数。QueryDict / dict 両対応の単値取得。"""
    if hasattr(params, "get"):
        return params.get(key, default)
    return default


def _getlist(params, key):
    """[性質] 純関数。QueryDict / dict 両対応のリスト取得。"""
    getlist = getattr(params, "getlist", None)
    if callable(getlist):
        return getlist(key)
    value = _get(params, key)
    return [value] if value else []


def _strip_value(params, key):
    """[性質] 純関数。前後空白を除去して返す（空入力は空文字）。"""
    return (_get(params, key) or "").strip()
