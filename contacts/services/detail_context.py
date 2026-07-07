"""主役 Contact 1 枚分の詳細表示 context を組み立てる共通部品。

ContactDetailView（/contacts/）と active 人物詳細（/persons/ の active+primary 分岐）の
両方から呼ぶ。同一の見た目（contact_detail.html）を「主役 Contact 1 枚」で描くために必要な
context をここに集約し、両画面でコピーせず共有する。

責務の線引き：
  - ここが返すのは「主役 Contact 1 枚分」の従属データのみ。
  - 画面メタ（back / active_app / active_menu / page_title）は各 View の責務＝含めない。
  - パーソン単位で増えるデータ（別肩書リスト・将来の案件等）も含めない。
"""

from accounts.services import is_self_link_email_match
from duplicates.models import DuplicateCandidate, PersonMergeLog
from persons.models import Person

from ..models import Contact


def build_contact_detail_context(contact, user):
    """主役 Contact 1 枚を contact_detail.html で描くための context dict を返す。

    [性質] 準関数（DB 読み取りのみ・副作用なし）
    [入力] contact: Contact（描画対象の主役 Contact。person / business_card /
                    previous_person を select_related 済みが望ましい）
           user: 認証ユーザー（self-link 判定 email_match の算出に使用）
    [出力] dict（contact_detail.html が参照する主役 Contact 従属 context 一式）

    画面メタ（back / active_app / active_menu / page_title）は含めない＝呼び出し側 View の責務。
    パーソン単位で増えるデータ（別肩書リスト・将来の案件等）も含めない（コンタクト 1 枚分に限定）。
    """
    # モード判定（D-3b §3.2）
    is_primary = contact.status == Contact.Status.PRIMARY
    is_active = contact.status == Contact.Status.ACTIVE
    is_inactive = contact.status == Contact.Status.INACTIVE
    is_editable = (
        contact.status in (Contact.Status.PRIMARY, Contact.Status.ACTIVE)
        and contact.person.status == "active"
    )

    # 他のアクティブコンタクト（D-3b 論点 4）
    if is_primary:
        other_active_contacts = contact.person.contact_set.filter(
            status=Contact.Status.ACTIVE
        )
    elif is_active:
        other_active_contacts = (
            contact.person.contact_set.filter(
                status__in=[
                    Contact.Status.PRIMARY,
                    Contact.Status.ACTIVE,
                ]
            )
            .exclude(pk=contact.pk)
        )
    else:
        other_active_contacts = Contact.objects.none()

    # 重複候補・マージログ・previous_person
    pending_duplicates = DuplicateCandidate.get_pending(contact)
    merge_logs = PersonMergeLog.get_for_person(contact.person)
    previous_person = contact.previous_person

    # 同一 Person 配下の inactive Contact 履歴（自分自身は除外）。
    inactive_contacts = (
        contact.person.get_inactive_contacts().exclude(pk=contact.pk)
    )

    # 本人紐付けボタン（self-link 専用）の表示条件を出口ガードに揃える。
    # 既存の本人同定基準 is_self_link_email_match を再利用（新規判定は作らない）。
    person = contact.person
    email_match = (
        is_self_link_email_match(user, person) if person else False
    )
    person_active = bool(person and person.status == Person.Status.ACTIVE)

    return {
        "contact": contact,
        "field_confidences": contact.get_field_confidences(),
        "is_editable": is_editable,
        "is_primary": is_primary,
        "is_active": is_active,
        "is_inactive": is_inactive,
        "business_card": contact.business_card,
        "other_active_contacts": other_active_contacts,
        "pending_duplicates": pending_duplicates,
        "merge_logs": merge_logs,
        "previous_person": previous_person,
        "inactive_contacts": inactive_contacts,
        "email_match": email_match,
        "person_active": person_active,
    }
