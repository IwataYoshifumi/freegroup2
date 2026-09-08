import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from django.db import models, transaction
from django.utils import timezone

from actionlogs.models import ActionLog
from companies.models import Company, CompanyDuplicateCandidate
from contacts.services.generic_email_domains import is_generic_email_domain
from contacts.services.normalization import (
    derive_org_domain_name,
    normalize_organization,
    normalize_phone_value,
)

PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)


def normalize_website(url: str) -> str:
    """URLを比較用に正規化する。ドメイン＋パスの最初のセグメントを返す（仕様書 §6.5.1）。"""
    if not url:
        return ""
    normalized = url.strip().lower()
    if "://" not in normalized:
        normalized = f"//{normalized}"
    parsed = urlparse(normalized, scheme="https")
    netloc = parsed.netloc.removeprefix("www.")
    segments = [p for p in parsed.path.split("/") if p]
    first_segment = segments[0] if segments else ""
    return f"{netloc}/{first_segment}" if first_segment else netloc


def calculate_address_match(addr_a: str, addr_b: str) -> tuple[int, bool]:
    """住所の一致判定（仕様書 §6.5.1）。
    都道府県一致: +10点
    市区町村一致（または住所前方一致）: +20点（最大 +30点）
    Returns: (score, is_match)
    """
    if not addr_a or not addr_b:
        return 0, False

    a = addr_a.replace(" ", "").replace("　", "").strip()
    b = addr_b.replace(" ", "").replace("　", "").strip()
    if not a or not b:
        return 0, False

    pref_a = next((p for p in PREFECTURES if a.startswith(p)), "")
    pref_b = next((p for p in PREFECTURES if b.startswith(p)), "")
    pref_match = bool(pref_a and pref_b and pref_a == pref_b)

    city_match = False
    if a == b:
        pref_match = True
        city_match = True
    else:
        if pref_match:
            rem_a = a[len(pref_a):]
            rem_b = b[len(pref_b):]
            m_a = re.match(r"^(.+?[市区町村])", rem_a)
            m_b = re.match(r"^(.+?[市区町村])", rem_b)
            if m_a and m_b and m_a.group(1) == m_b.group(1):
                city_match = True
            elif rem_a and rem_b and (rem_a.startswith(rem_b) or rem_b.startswith(rem_a)):
                city_match = True
        else:
            min_len = min(len(a), len(b))
            if min_len >= 5 and (a.startswith(b) or b.startswith(a)):
                city_match = True

    score = 0
    if pref_match:
        score += 10
    if city_match:
        score += 20
    score = min(score, 30)

    return score, (score > 0)


def _check_address_match(addr_a: str, addr_b: str) -> bool:
    """住所の一致・前方一致判定（仕様書 §6.5.1、都道府県＋市区町村レベル）。"""
    return calculate_address_match(addr_a, addr_b)[1]


@dataclass
class CompanyMatchResult:
    """calculate_company_match() の返り値（仕様書 §6.5.1）。"""

    score: int
    name_match: bool
    domain_match: bool
    url_match: bool
    phone_match: bool = False
    address_match: bool = False
    rank: str = ""


def calculate_company_score(
    org_a, domain_a, phone_a, addr_a, web_a,
    org_b, domain_b, phone_b, addr_b, web_b,
) -> tuple[int, str]:
    """生の値10個を受け取り、重複検出スコアとランクを返す（仕様書 §6.5.1）。"""
    result = calculate_company_match(
        org_a, domain_a, phone_a, addr_a, web_a,
        org_b, domain_b, phone_b, addr_b, web_b,
    )
    return result.score, result.rank


def calculate_company_match(
    org_a, domain_a, phone_a, addr_a, web_a,
    org_b, domain_b, phone_b, addr_b, web_b,
) -> CompanyMatchResult:
    """生の値10個を受け取り、スコアと各項目の一致フラグをまとめて返す（仕様書 §6.5.1）。"""
    norm_org_a = normalize_organization(org_a)
    norm_org_b = normalize_organization(org_b)
    name_match = bool(norm_org_a) and norm_org_a == norm_org_b

    dom_a = (domain_a or "").strip().lower()
    dom_b = (domain_b or "").strip().lower()
    domain_match = (
        bool(dom_a)
        and dom_a == dom_b
        and not is_generic_email_domain(dom_a)
        and not is_generic_email_domain(dom_b)
    )

    norm_phone_a = normalize_phone_value(phone_a)
    norm_phone_b = normalize_phone_value(phone_b)
    phone_match = bool(norm_phone_a) and norm_phone_a == norm_phone_b

    address_score, address_match = calculate_address_match(addr_a, addr_b)

    norm_web_a = normalize_website(web_a)
    norm_web_b = normalize_website(web_b)
    url_match = bool(norm_web_a) and norm_web_a == norm_web_b

    score = 0
    if name_match:
        score += 120
    if domain_match:
        score += 150
    if phone_match:
        score += 60
    if address_score:
        score += address_score
    if url_match:
        score += 20

    rank = determine_company_rank(
        score=score,
        name_match=name_match,
        domain_match=domain_match,
        url_match=url_match,
        phone_match=phone_match,
        address_match=address_match,
    )
    return CompanyMatchResult(
        score=score,
        name_match=name_match,
        domain_match=domain_match,
        url_match=url_match,
        phone_match=phone_match,
        address_match=address_match,
        rank=rank or "",
    )


def determine_company_rank(
    score: int,
    name_match: bool,
    domain_match: bool,
    url_match: bool = False,
    phone_match: bool = False,
    address_match: bool = False,
) -> str | None:
    """重複検出ランク判定（仕様書 v1.5 §6.5.2）。"""
    # 候補外（None）：120点未満、および会社名のみ一致（追加の電話・住所・ドメイン・URL一致が一切ない）ケース
    has_additional_match = domain_match or phone_match or address_match or url_match
    if name_match and not has_additional_match:
        return None
    if score < 120:
        return None

    # exact_match（完全一致）: スコア 230点 以上（かつ name_match と domain_match 必須）
    if name_match and domain_match and score >= 230:
        return CompanyDuplicateCandidate.Rank.EXACT_MATCH

    # possible_high（重複可能性大）: スコア 200点 以上
    if score >= 200:
        return CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH

    # possible_mid（重複可能性中）: スコア 140点 以上
    if score >= 140:
        return CompanyDuplicateCandidate.Rank.POSSIBLE_MID

    # possible_low（重複可能性小）: スコア 120点 以上（※社名120点に加え、電話・住所等の追加一致がある場合のみ）
    if score >= 120:
        return CompanyDuplicateCandidate.Rank.POSSIBLE_LOW

    return None


def get_or_create_candidate_pair(c1: Company, c2: Company, **kwargs) -> CompanyDuplicateCandidate:
    """pending の同一ペアが既にあれば返し、なければ作成する（仕様書 §6.5.3）。"""
    a, b = (c1, c2) if c1.id < c2.id else (c2, c1)
    candidate, _created = CompanyDuplicateCandidate.objects.get_or_create(
        company_a=a,
        company_b=b,
        review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        defaults=kwargs,
    )
    return candidate


def register_company_candidate(company_a: Company, company_b: Company) -> CompanyDuplicateCandidate | None:
    """Company対Companyでスコア・ランクを計算し、レビューキューに積む（仕様書 §6.5.3）。"""
    match_result = calculate_company_match(
        company_a.organization, company_a.domain, company_a.phone,
        company_a.address, company_a.website,
        company_b.organization, company_b.domain, company_b.phone,
        company_b.address, company_b.website,
    )
    if not match_result.rank:
        return None
    return get_or_create_candidate_pair(
        company_a,
        company_b,
        score=match_result.score,
        rank=match_result.rank,
    )


def get_companies_confirmed_as_different(company: Company) -> set[uuid.UUID]:
    """company との組み合わせで review_status='different_company' と判定済みの相手 Company ID 集合を返す（仕様書 §6.5.4）。"""
    pairs = CompanyDuplicateCandidate.objects.filter(
        models.Q(company_a=company) | models.Q(company_b=company),
        review_status=CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY,
    )
    return {
        (p.company_b_id if p.company_a_id == company.id else p.company_a_id)
        for p in pairs
    }


def create_company_from_contact(contact, user=None) -> Company:
    """exact_matchの候補が存在しない場合の新規Company作成（仕様書 §6.5.4）。
    contact.org_domain_name が空の場合でも、contact.email から独自ドメインを抽出する（§6.4）。
    """
    org_domain = contact.org_domain_name or ""
    if not org_domain and getattr(contact, "email", ""):
        org_domain = derive_org_domain_name(contact.email)

    domain = org_domain if org_domain and not is_generic_email_domain(org_domain) else ""
    return Company.objects.create(
        organization=contact.organization,
        domain=domain,
        phone=getattr(contact, "org_phone", "") or "",
        address=getattr(contact, "address", "") or "",
        website=getattr(contact, "website", "") or "",
        created_by=user,
    )


def maybe_fill_company_domain(company: Company, org_domain_name: str) -> bool:
    """company.domain が空の場合のみ、org_domain_name で埋める（仕様書 §6.5.6）。"""
    if company.domain:
        return False
    if org_domain_name and not is_generic_email_domain(org_domain_name):
        company.domain = org_domain_name
        company.save(update_fields=["domain", "updated_at"])
        return True
    return False


def link_contact_to_company(contact, user=None) -> Company | None:
    """Contact 作成・更新時に呼び出され、Company と紐付ける（仕様書 §6.5.4）。"""
    if not contact.organization or not contact.organization.strip():
        return None

    # contact.org_domain_name が未設定で email がある場合は補完（§6.4）
    if not contact.org_domain_name and getattr(contact, "email", ""):
        derived_dom = derive_org_domain_name(contact.email)
        if derived_dom:
            contact.org_domain_name = derived_dom
            contact.save(update_fields=["org_domain_name", "updated_at"])

    active_companies = list(Company.objects.filter(status=Company.Status.ACTIVE))

    exact_matches = []
    other_candidates = []

    contact_phone = getattr(contact, "org_phone", "") or ""
    contact_addr = getattr(contact, "address", "") or ""
    contact_web = getattr(contact, "website", "") or ""

    for comp in active_companies:
        match_result = calculate_company_match(
            contact.organization, contact.org_domain_name, contact_phone,
            contact_addr, contact_web,
            comp.organization, comp.domain, comp.phone,
            comp.address, comp.website,
        )
        if match_result.rank == CompanyDuplicateCandidate.Rank.EXACT_MATCH:
            exact_matches.append(comp)
        elif match_result.rank:
            other_candidates.append(comp)

    if exact_matches:
        # created_at 昇順で最古の Company を採用
        exact_matches.sort(key=lambda c: c.created_at)
        oldest = exact_matches[0]

        contact.company = oldest
        contact.save(update_fields=["company", "updated_at"])

        # ドメインの穴埋め（§6.5.6）：既存Companyのdomainが空の場合、紐づくContactの独自ドメインで穴埋め
        if contact.org_domain_name:
            maybe_fill_company_domain(oldest, contact.org_domain_name)

        # 余剰の exact_matches 候補とのペアリング
        confirmed_different = get_companies_confirmed_as_different(oldest)
        for surplus in exact_matches[1:]:
            if surplus.id not in confirmed_different:
                register_company_candidate(oldest, surplus)

        return oldest

    # exact_match が存在しない場合：新規作成
    new_company = create_company_from_contact(contact, user=user)
    contact.company = new_company
    contact.save(update_fields=["company", "updated_at"])

    # 他の候補があればレビューキューに登録
    for candidate in other_candidates:
        register_company_candidate(new_company, candidate)

    return new_company


def archive_company_if_orphaned(company: Company) -> bool:
    """紐づく有効な Contact および Deal が 0 件になった Company を status='archived' に更新する（仕様書 §6.6）。"""
    has_contacts = company.contacts.exists()
    has_deals = company.deals.filter(is_archived=False).exists() if hasattr(company, "deals") else False
    if not has_contacts and not has_deals:
        company.status = Company.Status.ARCHIVED
        company.save(update_fields=["status", "updated_at"])
        return True
    return False


def execute_company_merge(surviving_company: Company, target_companies: list[Company], user=None) -> Company:
    """複数の Company を surviving_company に一括マージする（仕様書 §6.5.5）。"""
    if not target_companies:
        raise ValueError("統合対象が指定されていません。")
    if surviving_company in target_companies:
        raise ValueError("統合先の会社を統合対象に含めることはできません。")
    if any(c.status != Company.Status.ACTIVE for c in target_companies):
        raise ValueError("統合できるのは有効な会社のみです。")
    if surviving_company.status != Company.Status.ACTIVE:
        raise ValueError("統合先に指定できるのは有効な会社のみです。")
    if len(target_companies) > 20:
        raise ValueError("一度に統合できるのは20社までです。")

    confirmed_different = get_companies_confirmed_as_different(surviving_company)
    if confirmed_different & {c.id for c in target_companies}:
        raise ValueError(
            "統合対象に、以前「別会社」と判定した会社が含まれています。"
            "本当に統合する場合は、先にレビューキューでその判定を取り消してください。"
        )

    merge_set_ids = {surviving_company.id} | {c.id for c in target_companies}
    now = timezone.now()

    with transaction.atomic():
        for comp in target_companies:
            comp.transfer_contacts_to_company(surviving_company)

        # マージセット内の pending ペアを 'merged' に更新
        merged_ids = list(
            CompanyDuplicateCandidate.objects.filter(
                company_a_id__in=merge_set_ids,
                company_b_id__in=merge_set_ids,
                review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
            ).values_list("id", flat=True)
        )
        if merged_ids:
            CompanyDuplicateCandidate.objects.filter(id__in=merged_ids).update(
                review_status=CompanyDuplicateCandidate.ReviewStatus.MERGED,
                reviewed_by=user,
                reviewed_at=now,
                updated_at=now,
            )

        # マージセット関連の第三者 pending ペアを 'invalidated' に更新
        # （内部ペアは直前で MERGED に更新済みのため、残る PENDING は第三者ペアのみ）
        invalidated_ids = list(
            CompanyDuplicateCandidate.objects.filter(
                models.Q(company_a_id__in=merge_set_ids) | models.Q(company_b_id__in=merge_set_ids),
                review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
            ).values_list("id", flat=True)
        )
        if invalidated_ids:
            CompanyDuplicateCandidate.objects.filter(id__in=invalidated_ids).update(
                review_status=CompanyDuplicateCandidate.ReviewStatus.INVALIDATED,
                reviewed_by=user,
                reviewed_at=now,
                updated_at=now,
            )

        # 監査ログを記録
        for comp in target_companies:
            ActionLog.record(
                user=user,
                action="company_merged",
                content_object=comp,
                object_repr=str(comp),
                data={"surviving_company_id": str(surviving_company.id)},
            )

    return surviving_company


def mark_as_different_company(candidate_id, user) -> CompanyDuplicateCandidate:
    """レビュー状態を 'different_company' に更新し、監査情報を記録する（仕様書 §6.5.3）。"""
    candidate = CompanyDuplicateCandidate.objects.get(pk=candidate_id)
    candidate.review_status = CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY
    candidate.reviewed_by = user
    candidate.reviewed_at = timezone.now()
    candidate.save(update_fields=["review_status", "reviewed_by", "reviewed_at", "updated_at"])
    return candidate
