"""メーリングリスト CSV エクスポートサービス（仕様書 v1.6 §3）。

メーリングリストメンバーから Person マージ解決を行い、Contact 情報・退会状態を
ユーザー選択項目に応じた CSV（UTF-8 BOM 付き）として出力する。
"""

import csv
import io
import logging
import urllib.parse

from django.utils import timezone

from contacts.services.normalization import compose_full_address

logger = logging.getLogger(__name__)

# カラム定義一覧（仕様書 v1.6 §3.2）
# (key, label, is_default, is_importable, is_reference_only, note)
EXPORT_COLUMNS = [
    ("person_id", "Person ID", True, False, False, ""),
    ("last_name", "姓", False, True, False, ""),
    ("first_name", "名", False, True, False, ""),
    ("full_name", "氏名", True, True, False, ""),
    ("other_name_parts", "その他の名前部分", False, True, False, ""),
    ("lang", "言語コード", False, True, False, ""),
    ("name_order", "氏名の並び順", False, False, True, "参考出力のみ。インポート時は言語コードから自動導出されます"),
    ("salutation_name", "宛名", True, False, True, "参考出力のみ。インポート時は自動生成されます"),
    ("organization", "会社名", True, True, False, ""),
    ("department", "部署", True, True, False, ""),
    ("title", "役職", True, True, False, ""),
    ("branch", "支店", False, True, False, ""),
    ("qualification", "資格", False, True, False, ""),
    ("catchphrase", "キャッチフレーズ", False, True, False, ""),
    ("postal_code", "郵便番号", True, True, False, ""),
    ("country", "国", False, True, False, ""),
    ("region", "中間行政区画", False, True, False, ""),
    ("city", "市区町村", False, True, False, ""),
    ("rest_of_address", "残り住所", False, True, False, ""),
    ("address", "住所（完成形）", True, False, True, "参考出力のみ。インポート時は各住所フィールドから自動組み立てされます"),
    ("email", "メール", True, True, False, ""),
    ("mobile_phone", "携帯電話", False, True, False, ""),
    ("personal_phone", "個人直通電話", False, True, False, ""),
    ("personal_fax", "個人FAX", False, True, False, ""),
    ("org_phone", "会社代表電話", False, True, False, ""),
    ("org_fax", "会社FAX", False, True, False, ""),
    ("website", "ウェブサイト", False, True, False, ""),
    ("notes", "メモ", False, True, False, ""),
    ("is_unsubscribed", "退会状態", True, False, False, ""),
]

EXPORT_COLUMNS_DICT = {col[0]: col for col in EXPORT_COLUMNS}
EXPORT_AVAILABLE_KEYS = [col[0] for col in EXPORT_COLUMNS]
DEFAULT_SELECTED_KEYS = [col[0] for col in EXPORT_COLUMNS if col[2]]
IMPORTABLE_KEYS = [col[0] for col in EXPORT_COLUMNS if col[3]]


def extract_column_value(col_key, member, contact, surviving_person):
    """指定されたカラムキーに対応する値を Contact / Person / Member から抽出する。"""
    if col_key == "person_id":
        return str(surviving_person.id)
    elif col_key == "last_name":
        return contact.last_name or ""
    elif col_key == "first_name":
        return contact.first_name or ""
    elif col_key == "full_name":
        return contact.full_name or ""
    elif col_key == "other_name_parts":
        return contact.other_name_parts or ""
    elif col_key == "lang":
        return contact.lang or ""
    elif col_key == "name_order":
        return contact.name_order or ""
    elif col_key == "salutation_name":
        return contact.salutation_name or ""
    elif col_key == "organization":
        return contact.organization or ""
    elif col_key == "department":
        return contact.department or ""
    elif col_key == "title":
        return contact.title or ""
    elif col_key == "branch":
        return contact.branch or ""
    elif col_key == "qualification":
        return contact.qualification or ""
    elif col_key == "catchphrase":
        return contact.catchphrase or ""
    elif col_key == "postal_code":
        return contact.postal_code or ""
    elif col_key == "country":
        return contact.country or ""
    elif col_key == "region":
        return contact.region or ""
    elif col_key == "city":
        return contact.city or ""
    elif col_key == "rest_of_address":
        return contact.rest_of_address or ""
    elif col_key == "address":
        return compose_full_address(
            contact.postal_code or "",
            contact.region or "",
            contact.city or "",
            contact.rest_of_address or "",
            contact.country or "",
            contact.lang or "",
        )
    elif col_key == "email":
        return contact.email or ""
    elif col_key == "mobile_phone":
        return contact.mobile_phone or ""
    elif col_key == "personal_phone":
        return contact.personal_phone or ""
    elif col_key == "personal_fax":
        return contact.personal_fax or ""
    elif col_key == "org_phone":
        return contact.org_phone or ""
    elif col_key == "org_fax":
        return contact.org_fax or ""
    elif col_key == "website":
        return contact.website or ""
    elif col_key == "notes":
        return contact.notes or ""
    elif col_key == "is_unsubscribed":
        is_unsub = getattr(member, "is_unsubscribed", None)
        if is_unsub is None:
            is_unsub = getattr(surviving_person, "is_unsubscribed", False) or getattr(
                member.person, "is_unsubscribed", False
            )
        return "退会済み" if is_unsub else ""
    return ""


def generate_mailing_list_csv(mailing_list, selected_keys):
    """メーリングリストのメンバーから CSV 文字列（UTF-8 BOM 付き）を生成する。"""
    from mailings.models import MailingListMember

    members_qs = (
        MailingListMember.objects.filter(mailing_list=mailing_list)
        .select_related(
            "person",
            "person__primary_contact",
            "person__merged_into",
            "person__merged_into__primary_contact",
            "added_by",
        )
        .order_by("created_at")
    )

    buf = io.StringIO()
    writer = csv.writer(buf)

    # ヘッダー行
    headers = [
        EXPORT_COLUMNS_DICT[k][1] for k in selected_keys if k in EXPORT_COLUMNS_DICT
    ]
    writer.writerow(headers)

    # データ行
    for member in members_qs:
        surviving_person = member.person.get_surviving_person()
        contact = surviving_person.primary_contact
        if contact is None:
            # primary_contact が存在しない場合は防御的にログ記録してスキップ（仕様書 §3.5）
            logger.warning(
                "エクスポート対象Person(id=%s)にprimary_contactが存在しないためスキップ",
                surviving_person.id,
            )
            continue
        row = [
            extract_column_value(col_key, member, contact, surviving_person)
            for col_key in selected_keys
            if col_key in EXPORT_COLUMNS_DICT
        ]
        writer.writerow(row)

    return "\ufeff" + buf.getvalue()


def make_export_disposition_header(mailing_list):
    """RFC 5987 準拠の Content-Disposition ヘッダー値を生成する。"""
    today_str = timezone.now().strftime("%Y%m%d")
    # ファイル名禁止文字・制御文字を置換
    safe_name = "".join(
        c for c in mailing_list.name if c not in r'/\:*?"<>|' and ord(c) >= 32
    ).strip()
    if not safe_name:
        safe_name = "list"
    filename_utf8 = f"メーリングリスト_{safe_name}_{today_str}.csv"
    fallback_filename = f"mailing_list_{mailing_list.pk}_{today_str}.csv"
    quoted = urllib.parse.quote(filename_utf8.encode("utf-8"))
    return f'attachment; filename="{fallback_filename}"; filename*=UTF-8\'\'{quoted}'
