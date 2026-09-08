from django.http import HttpRequest


def duplicate_counts(request: HttpRequest) -> dict:
    """サイドバーのアテンションバッジ表示用コンテキストプロセッサ。

    パーソンおよび会社の未処理（pending）重複候補件数を取得する。
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "person_duplicate_count": 0,
            "company_duplicate_count": 0,
            "has_pending_duplicates": False,
        }

    try:
        from duplicates.models import DuplicateCandidate
        from companies.models import CompanyDuplicateCandidate

        person_dup_count = DuplicateCandidate.objects.filter(
            review_status=DuplicateCandidate.ReviewStatus.PENDING
        ).count()
        company_dup_count = CompanyDuplicateCandidate.objects.filter(
            review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING
        ).count()

        return {
            "person_duplicate_count": person_dup_count,
            "company_duplicate_count": company_dup_count,
            "has_pending_duplicates": (person_dup_count > 0 or company_dup_count > 0),
        }
    except Exception:
        return {
            "person_duplicate_count": 0,
            "company_duplicate_count": 0,
            "has_pending_duplicates": False,
        }
