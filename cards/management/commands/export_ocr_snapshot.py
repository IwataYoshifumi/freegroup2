"""OCR 出力データの永続スナップショット用エクスポート（モデル比較・ベースライン保管用）。

使い方:
    python manage.py export_ocr_snapshot \\
        --output=docs/ocr_analysis/sonnet_4_6_baseline_2026_05_23.json \\
        --model=claude-sonnet-4-6 \\
        --description="Phase 3B E2E 検証時のデータ"

    python manage.py export_ocr_snapshot \\
        --output=docs/ocr_analysis/haiku_4_5_2026_05_23.json \\
        --model=claude-haiku-4-5-20251001

出力 JSON 構造:
    {
        "metadata": {
            "model": "<--model>",
            "schema_version": "1.6.0",
            "exported_at": "<ISO8601>",
            "git_commit": "<現 HEAD SHA、Git 取れなければ null>",
            "ocr_session_description": "<--description>",
            "counts": {...},
        },
        "original_images": [...],  # image_file は SHA256 ハッシュで識別
        "business_cards": [...],   # card_image も SHA256 ハッシュ + raw_json_1 / raw_json_2
        "contacts": [...],
        "contact_field_confidences": [...],
        "persons": [...],
    }

image_file / card_image の中身（バイナリ）はエクスポートせず、SHA256 ハッシュとファイル名のみ
記録する。同じ画像かどうかを後でハッシュ突合で確認できる設計。
"""

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact, ContactFieldConfidence
from persons.models import Person


def _sha256_of_file_field(file_field):
    """[性質] 副作用あり（ファイル読み込み）

    FileField の中身を SHA256 ハッシュ化して 16 進文字列で返す。
    ファイルなし・ファイル消失時は None を返す（エクスポート失敗にしない）。
    """
    if not file_field:
        return None
    try:
        h = hashlib.sha256()
        with file_field.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (FileNotFoundError, OSError):
        return None


def _serialize_field_value(value):
    """[性質] 純関数 / JSON シリアライズ可能な型に変換。"""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        # datetime / date / time
        return value.isoformat()
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _serialize_model_instance(instance, exclude_fields=None, extra=None):
    """[性質] 純関数（concrete フィールドを動的に列挙、FK は <name>_id で出力）。

    [入力] instance: Model インスタンス, exclude_fields: 除外するフィールド名集合,
           extra: 追加で含める dict（FileField の SHA256 等）
    [出力] dict（JSON シリアライズ可能）

    reverse FK / m2m / generic relation は concrete=False のため除外。
    FK は <name>_id 形式で str(UUID) として出力。
    """
    exclude_fields = set(exclude_fields or [])
    result = {}
    for field in instance._meta.get_fields():
        if not getattr(field, "concrete", False):
            continue
        name = field.name
        if name in exclude_fields:
            continue
        if field.is_relation:
            fk_id = getattr(instance, name + "_id", None)
            result[name + "_id"] = str(fk_id) if fk_id is not None else None
        else:
            result[name] = _serialize_field_value(getattr(instance, name, None))
    if extra:
        result.update(extra)
    return result


def _git_commit_sha():
    """[性質] 副作用あり（git コマンド実行）/ 現 HEAD の SHA を返す。失敗時 None。"""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[3]),
        ).decode().strip()
        return sha
    except Exception:
        return None


class Command(BaseCommand):
    help = (
        "OCR 出力データを JSON にエクスポート（モデル比較ベースライン保管用）。"
        "image_file / card_image の中身はエクスポートせず SHA256 ハッシュのみ保存。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            required=True,
            help="出力ファイルパス（例: docs/ocr_analysis/foo.json）",
        )
        parser.add_argument(
            "--model",
            required=True,
            help="OCR 実行時のモデル名（metadata.model に記録、例: claude-sonnet-4-6）",
        )
        parser.add_argument(
            "--description",
            default="",
            help="メタデータの ocr_session_description（任意の説明文）",
        )

    def handle(self, *args, **options):
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)

        # OriginalImage
        ois = []
        for oi in OriginalImage.objects.all().order_by("created_at"):
            extra = {
                "image_file_name": oi.image_file.name if oi.image_file else None,
                "image_file_sha256": _sha256_of_file_field(oi.image_file),
            }
            ois.append(
                _serialize_model_instance(
                    oi, exclude_fields={"image_file"}, extra=extra
                )
            )

        # BusinessCard
        bcs = []
        for bc in BusinessCard.objects.all().order_by(
            "original_image__created_at", "card_index"
        ):
            extra = {
                "card_image_name": bc.card_image.name if bc.card_image else None,
                "card_image_sha256": _sha256_of_file_field(bc.card_image),
            }
            bcs.append(
                _serialize_model_instance(
                    bc, exclude_fields={"card_image"}, extra=extra
                )
            )

        # Contact
        contacts = []
        for c in Contact.objects.all().order_by("created_at"):
            contacts.append(_serialize_model_instance(c))

        # ContactFieldConfidence
        cfcs = []
        for cfc in ContactFieldConfidence.objects.all().order_by(
            "contact_id", "field_name"
        ):
            cfcs.append(_serialize_model_instance(cfc))

        # Person
        persons = []
        for p in Person.objects.all().order_by("created_at"):
            persons.append(_serialize_model_instance(p))

        payload = {
            "metadata": {
                "model": options["model"],
                "schema_version": "1.6.0",
                "exported_at": timezone.now().isoformat(),
                "git_commit": _git_commit_sha(),
                "ocr_session_description": options["description"],
                "counts": {
                    "original_images": len(ois),
                    "business_cards": len(bcs),
                    "contacts": len(contacts),
                    "contact_field_confidences": len(cfcs),
                    "persons": len(persons),
                },
            },
            "original_images": ois,
            "business_cards": bcs,
            "contacts": contacts,
            "contact_field_confidences": cfcs,
            "persons": persons,
        }

        with open(output, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Exported: {output}"))
        for k, v in payload["metadata"]["counts"].items():
            self.stdout.write(f"  {k}: {v}")
