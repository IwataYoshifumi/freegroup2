"""コンタクト CSV インポートサービス（仕様書 v1.6 §4）。

CSVファイルのパース、ヘッダー照合、正規化パイプライン、共通スキップ判定、
および Person / Contact の新規生成トランザクションを統括する。
"""

import csv
import io
import logging
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction

from contacts.models import Contact
from contacts.services.normalization import normalize_field
from persons.models import Person

logger = logging.getLogger(__name__)

# ヘッダー対応表（日本語表記 -> Contactフィールド名）
HEADER_MAPPING = {
    "姓": "last_name",
    "名": "first_name",
    "氏名": "full_name",
    "その他の名前部分": "other_name_parts",
    "言語コード": "lang",
    "言語": "lang",
    "会社名": "organization",
    "会社": "organization",
    "部署": "department",
    "部署名": "department",
    "役職": "title",
    "支店": "branch",
    "資格": "qualification",
    "キャッチフレーズ": "catchphrase",
    "郵便番号": "postal_code",
    "国": "country",
    "中間行政区画": "region",
    "市区町村": "city",
    "残り住所": "rest_of_address",
    "メール": "email",
    "メールアドレス": "email",
    "携帯電話": "mobile_phone",
    "個人直通電話": "personal_phone",
    "個人fax": "personal_fax",
    "個人FAX": "personal_fax",
    "会社代表電話": "org_phone",
    "会社fax": "org_fax",
    "会社FAX": "org_fax",
    "ウェブサイト": "website",
    "メモ": "notes",
}

# インポートで受け付ける全フィールド（英語名）
IMPORTABLE_FIELDS = {
    "last_name",
    "first_name",
    "full_name",
    "other_name_parts",
    "lang",
    "organization",
    "department",
    "title",
    "branch",
    "qualification",
    "catchphrase",
    "postal_code",
    "country",
    "region",
    "city",
    "rest_of_address",
    "email",
    "mobile_phone",
    "personal_phone",
    "personal_fax",
    "org_phone",
    "org_fax",
    "website",
    "notes",
}

# 汎用正規化ループで処理するフィールド（country, full_name は個別処理のため除外）
LOOP_FIELDS = IMPORTABLE_FIELDS - {"country", "full_name"}

# 参考出力のみ・メーリングリスト固有等（無視する列）
IGNORED_FIELDS = {
    "name_order",
    "氏名の並び順",
    "salutation_name",
    "宛名",
    "address",
    "住所",
    "住所（完成形）",
    "person_id",
    "person id",
    "Person ID",
    "PersonId",
    "id",
    "ID",
    "is_unsubscribed",
    "退会状態",
}


def is_row_skippable(row_dict: dict) -> bool:
    """姓（last_name）と名（first_name）がともに空の行をスキップ対象とする（仕様書 §4.1, §4.8）。

    プレビュー画面の集計とインポート確定実行の双方がこの共有関数を呼ぶ。
    """
    last_name = (row_dict.get("last_name") or "").strip()
    first_name = (row_dict.get("first_name") or "").strip()
    return not (last_name or first_name)


def derive_name_order(lang: str) -> str:
    """lang から name_order を導出する（ContactBaseForm._setup_effective_lang と同一ルール）。"""
    if (lang or "").strip().lower().startswith("en"):
        return Contact.NameOrder.FIRST_LAST
    return Contact.NameOrder.LAST_FIRST


def resolve_headers(raw_headers: list[str]) -> tuple[dict[int, str], list[str]]:
    """CSVヘッダー行を解析し、列インデックスとフィールド名のマップ、およびエラーリストを返す。"""
    col_map = {}
    missing_required = []

    for idx, header in enumerate(raw_headers):
        clean_header = (header or "").strip()
        if not clean_header:
            continue
        # 日本語名マッチ
        if clean_header in HEADER_MAPPING:
            col_map[idx] = HEADER_MAPPING[clean_header]
        # 英語名マッチ
        elif clean_header.lower() in IMPORTABLE_FIELDS:
            col_map[idx] = clean_header.lower()
        # 無視対象列
        elif clean_header in IGNORED_FIELDS or clean_header.lower() in IGNORED_FIELDS:
            continue
        else:
            # 未知の列はスキップ
            continue

    mapped_fields = set(col_map.values())
    if "last_name" not in mapped_fields:
        missing_required.append("姓（last_name）")
    if "first_name" not in mapped_fields:
        missing_required.append("名（first_name）")

    return col_map, missing_required


def build_contact_dict(row: dict) -> dict:
    """CSV1行分の生データから Contact 生成用の辞書を組み立てる（仕様書 §4.3）。"""
    contact_dict = {}

    # 1. country は先に個別解決。空白のみも未入力扱い（仕様書 §4.3, §4.5）。
    country_raw = (row.get("country") or "").strip()
    contact_dict["country"] = country_raw or getattr(
        settings, "DEFAULT_CONTACT_COUNTRY", "JP"
    )

    # 確定した country を反映した軽量インスタンスを作り、normalize_field に渡す
    partial_contact = Contact(country=contact_dict["country"])

    # 2. 残りのフィールドを汎用ループで正規化
    for field_name in LOOP_FIELDS:
        raw_val = row.get(field_name, "")
        if raw_val:
            normalized = normalize_field(
                field_name, raw_val, contact=partial_contact
            )
            if normalized:
                contact_dict[field_name] = normalized

    # 3. full_name はCSV入力があるときだけ設定。ValidationError 時はキーを設定しない（仕様書 §4.4）
    full_name_raw = (row.get("full_name") or "").strip()
    if full_name_raw:
        try:
            contact_dict["full_name"] = normalize_field(
                "full_name", full_name_raw, contact=partial_contact
            )
        except ValidationError:
            pass

    # 4. name_order は lang から導出して DB 永続化（仕様書 §4.4）
    contact_dict["name_order"] = derive_name_order(contact_dict.get("lang", ""))

    return contact_dict


def create_person_and_contact_from_row(contact_dict: dict, user) -> Contact:
    """CSVインポート1行から Person・Contact を新規作成する（仕様書 §4.6）。

    既存の contacts/views.py _create_person_and_contact() の核の流れを踏襲する。
    """
    person = Person.objects.create(status=Person.Status.ACTIVE)
    contact = Contact.objects.create(
        person=person,
        status=Contact.Status.PRIMARY,
        created_by=user,
        updated_by=user,
        duplicate_checked_at=None,
        **contact_dict,
    )
    person.set_primary_contact(contact)
    return contact


def save_temp_import_file(uploaded_file) -> tuple[str, str]:
    """アップロードされたCSVファイルを default_storage の一時領域に保存する。"""
    token = str(uuid.uuid4())
    storage_path = f"tmp/csv_import_{token}.csv"
    content = uploaded_file.read()
    default_storage.save(storage_path, ContentFile(content))
    return token, storage_path


def read_temp_import_file(storage_path: str) -> str:
    """一時領域からCSVテキストを読み出す（UTF-8 with BOM 対応）。"""
    with default_storage.open(storage_path, "rb") as f:
        content = f.read()
    return content.decode("utf-8-sig")


def delete_temp_import_file(storage_path: str) -> None:
    """一時領域のCSVファイルを削除する。"""
    try:
        if storage_path and default_storage.exists(storage_path):
            default_storage.delete(storage_path)
    except Exception as exc:
        logger.warning("一時ファイルの削除に失敗しました (%s): %s", storage_path, exc)


def parse_csv_for_preview(csv_text: str) -> dict:
    """プレビュー表示用のサマリー件数および先頭10件サンプルを生成する。"""
    reader = csv.reader(io.StringIO(csv_text))
    try:
        raw_headers = next(reader)
    except StopIteration:
        return {"error": "CSVファイルが空です。ヘッダー行が存在しません。"}

    col_map, missing_required = resolve_headers(raw_headers)
    if missing_required:
        return {
            "error": f"必須列が不足しています: {', '.join(missing_required)}"
        }

    total_count = 0
    skippable_count = 0
    rows = []

    for row in reader:
        if not any(cell.strip() for cell in row):
            continue  # 完全な空行はカウント外
        total_count += 1
        row_dict = {
            field: row[idx].strip() if idx < len(row) else ""
            for idx, field in col_map.items()
        }
        is_skip = is_row_skippable(row_dict)
        if is_skip:
            skippable_count += 1
            status = "skip"
            skip_reason = "姓および名が未入力"
        else:
            status = "valid"
            skip_reason = ""

        rows.append(
            {
                "row_num": total_count + 1,  # ヘッダーを1行目として2行目から
                "row_no": total_count + 1,
                "status": status,
                "skip_reason": skip_reason,
                "last_name": row_dict.get("last_name", ""),
                "first_name": row_dict.get("first_name", ""),
                "full_name": row_dict.get("full_name", ""),
                "org_name": row_dict.get("organization", ""),
                "organization": row_dict.get("organization", ""),
                "department": row_dict.get("department", ""),
                "title": row_dict.get("title", ""),
                "email": row_dict.get("email", ""),
                "is_skippable": is_skip,
            }
        )

    if total_count == 0:
        return {"error": "CSVファイルにデータ行が存在しません。"}

    success_estimated_count = total_count - skippable_count

    return {
        "total_count": total_count,
        "success_count": success_estimated_count,
        "skippable_count": skippable_count,
        "rows": rows,
        "samples": rows[:10],
    }


def execute_csv_import(csv_text: str, user) -> dict:
    """CSVインポート確定処理を実行する（1行1トランザクション、仕様書 §4.6）。"""
    reader = csv.reader(io.StringIO(csv_text))
    try:
        raw_headers = next(reader)
    except StopIteration:
        return {"total_count": 0, "success_count": 0, "skipped_count": 0, "errors": [{"line_no": 1, "reason": "ファイルが空です"}]}

    col_map, missing_required = resolve_headers(raw_headers)
    if missing_required:
        return {
            "total_count": 0,
            "success_count": 0,
            "skipped_count": 0,
            "errors": [{"line_no": 1, "reason": f"必須列不足: {', '.join(missing_required)}"}],
        }

    total_count = 0
    success_count = 0
    skipped_count = 0
    errors = []

    # ヘッダー行を1行目とし、データ行は2行目からカウント（仕様書 §4.1）
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue  # 完全な空行はスキップ
        total_count += 1
        row_dict = {
            field: row[idx].strip() if idx < len(row) else ""
            for idx, field in col_map.items()
        }

        if is_row_skippable(row_dict):
            skipped_count += 1
            errors.append(
                {"line_no": line_no, "reason": "姓・名が未入力のためスキップ"}
            )
            continue

        try:
            with transaction.atomic():
                contact_dict = build_contact_dict(row_dict)
                create_person_and_contact_from_row(contact_dict, user)
            success_count += 1
        except IntegrityError as exc:
            skipped_count += 1
            errors.append({"line_no": line_no, "reason": f"DB制約違反: {exc}"})
        except Exception as exc:
            skipped_count += 1
            errors.append({"line_no": line_no, "reason": f"予期しないエラー: {exc}"})

    return {
        "total_count": total_count,
        "success_count": success_count,
        "skipped_count": skipped_count,
        "errors": errors,
    }
