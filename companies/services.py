import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from django.db import models, transaction
from django.utils import timezone

from actionlogs.models import ActionLog
from companies.models import Company, CompanyDuplicateCandidate
from contacts.services.normalization import (
    derive_org_domain_name,
    is_generic_email_domain,
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


def _check_address_match(addr_a: str, addr_b: str) -> bool:
    """住所の一致・前方一致判定（仕様書 §6.5.1）。"""
    if not addr_a or not addr_b:
        return False
    a = addr_a.replace(" ", "").replace("　", "").strip()
    b = addr_b.replace(" ", "").replace("　", "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    min_len = min(len(a), len(b))
    if min_len >= 5 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def calculate_address_match(addr_a: str, addr_b: str) -> tuple[int, bool]:
    """住所の一致判定（仕様書 §6.5.1）。一致時は20点固定。"""
    is_match = _check_address_match(addr_a, addr_b)
    return (20 if is_match else 0), is_match


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

    address_match = _check_address_match(addr_a, addr_b)

    norm_web_a = normalize_website(web_a)
    norm_web_b = normalize_website(web_b)
    url_match = bool(norm_web_a) and norm_web_a == norm_web_b

    score = 0
    if name_match:
        score += 100
    if domain_match:
        score += 100
    if url_match:
        score += 100
    if phone_match:
        score += 20
    if address_match:
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
    # 候補外（None）：100点未満、および会社名単独一致（追加のドメイン・URL・電話・住所一致が一切ない）ケース
    has_additional_match = domain_match or url_match or phone_match or address_match
    if name_match and not has_additional_match:
        return None
    if score < 100:
        return None

    # exact_match（完全一致）: 会社名一致 AND (ドメイン一致 OR URL一致) かつ スコア 220点 以上
    if name_match and (domain_match or url_match) and score >= 220:
        return CompanyDuplicateCandidate.Rank.EXACT_MATCH

    # possible_high（重複可能性大）: スコア 200点 以上
    if score >= 200:
        return CompanyDuplicateCandidate.Rank.POSSIBLE_HIGH

    # possible_mid（重複可能性中）: スコア 140点 以上
    if score >= 140:
        return CompanyDuplicateCandidate.Rank.POSSIBLE_MID

    # possible_low（重複可能性小）: いずれか1つ以上の一致があり かつ スコア 100点 以上
    has_any_match = name_match or domain_match or url_match or phone_match or address_match
    if has_any_match and score >= 100:
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
    phone = getattr(contact, "org_phone", "") or getattr(contact, "personal_phone", "") or ""
    return Company.objects.create(
        organization=contact.organization,
        domain=domain,
        phone=phone,
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

    # SQL プレフィルタ（仕様書 §6.5.4 性能対策）
    q_filter = models.Q()

    # (a) 独自ドメインによる絞り込み（汎用ドメイン以外）
    if contact.org_domain_name and not is_generic_email_domain(contact.org_domain_name):
        q_filter |= models.Q(domain__iexact=contact.org_domain_name.strip())

    # (b) 会社名による絞り込み（完全・部分一致および法人格除去コアキーワード）
    raw_org = (contact.organization or "").strip()
    if raw_org:
        q_filter |= models.Q(organization__icontains=raw_org)

        # 法人格（株式会社、有限会社、合同会社、㈱、㈲等）を取り除いたコアキーワードでも検索
        core_org = raw_org
        for term in (
            "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
            "一般社団法人", "公益社団法人", "一般財団法人", "公益財団法人",
            "医療法人", "学校法人", "社会福祉法人",
            "㈱", "㈲", "(株)", "（株）", "(有)", "（有）",
        ):
            core_org = core_org.replace(term, "")
        core_org = core_org.replace(" ", "").replace("　", "").strip()
        if len(core_org) >= 2:
            q_filter |= models.Q(organization__icontains=core_org)

    # (c) Webサイト（URL）による絞り込み
    raw_web = getattr(contact, "website", "") or ""
    norm_web = normalize_website(raw_web)
    if norm_web:
        web_host = norm_web.split("/")[0]
        if web_host and len(web_host) >= 3:
            q_filter |= models.Q(website__icontains=web_host)

    # プレフィルタで対象 Company を事前絞り込み
    active_companies = list(
        Company.objects.filter(status=Company.Status.ACTIVE).filter(q_filter)
    )

    exact_matches = []
    other_candidates = []

    contact_phone = getattr(contact, "org_phone", "") or getattr(contact, "personal_phone", "") or ""
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


def mark_as_different_company(candidate_id, user, note="") -> CompanyDuplicateCandidate:
    """レビュー状態を 'different_company' に更新し、監査情報を記録する（仕様書 §6.5.5.1）。"""
    with transaction.atomic():
        candidate = CompanyDuplicateCandidate.objects.get(pk=candidate_id)
        candidate.review_status = CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY
        candidate.reviewed_by = user
        candidate.reviewed_at = timezone.now()
        candidate.save(update_fields=["review_status", "reviewed_by", "reviewed_at", "updated_at"])
        ActionLog.record(
            user=user,
            action="company_different_company",
            content_object=candidate,
            object_repr=str(candidate),
            data={
                "company_a_id": str(candidate.company_a_id),
                "company_b_id": str(candidate.company_b_id),
            },
            note=note,
        )
        return candidate
