"""dev_for_reset_ocr 管理コマンド（開発ツール）。

任意の OriginalImage に対して、関連 BusinessCard / Contact / ContactFieldConfidence /
孤児 Person と切り抜き画像を削除し、status=pending に戻す。
保存済みの元画像（OriginalImage.image_file）は保持されるため、process_opencv
（OpenCV 切り出し）→ process_ocr（OCR）を順次再実行することで OCR を再試行できる。

retry_failed_ocr（v1.2.2 §8.5.5、v1.5.0 で --opencv / --ocr 2 系統に拡張）との違い：
- retry_failed_ocr --opencv は status=failed AND BusinessCard 0件 のみ対象（運用ツール）
- retry_failed_ocr --ocr は ocr_status=failed の BusinessCard を対象（運用ツール）
- dev_for_reset_ocr は任意の OriginalImage を対象（開発ツール）

ファイル名・コマンド名のプレフィックスは `dev_*` とし、本番運用ツールと明確に分離する。
"""

import os
import uuid

from django.core.management.base import BaseCommand, CommandError

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact
from persons.models import Person


class Command(BaseCommand):
    help = (
        "OriginalImage の関連レコード（BusinessCard / Contact / ContactFieldConfidence / "
        "孤児 Person）と切り抜き画像を削除し、status=pending に戻す開発ツール。"
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--all",
            action="store_true",
            help="全 OriginalImage を対象にする",
        )
        group.add_argument(
            "--id",
            type=str,
            metavar="UUID",
            help="UUID 指定で 1 件のみ対象にする",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="--all 用の最大件数（任意）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="対象の id を表示するだけで実際には更新しない",
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（複合処理: DB 削除・ファイル削除・OriginalImage リセット）

        [入力] --all / --id <UUID>（排他必須）、--limit、--dry-run
        [出力] None（実行サマリを stdout に出力）
        """
        target_ids = _query_target_ids(target_id=options.get("id"), limit=options.get("limit"))
        if not target_ids:
            self.stdout.write("対象なし")
            return

        if options["dry_run"]:
            for tid in target_ids:
                self.stdout.write(f"  would reset: {tid}")
            self.stdout.write(self.style.SUCCESS(
                f"dry-run: would reset {len(target_ids)} record(s)"
            ))
            return

        summary = reset_ocr_for_originals(target_ids, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            f"dev_for_reset_ocr: targets={summary['targets']}, "
            f"deleted_business_cards={summary['business_cards']}, "
            f"deleted_card_image_files={summary['card_image_files']}, "
            f"deleted_orphan_persons={summary['orphan_persons']}"
        ))


def reset_ocr_for_originals(target_ids, stdout=None):
    """OriginalImage の関連レコードを削除し、status=pending に戻す。

    [性質] 副作用あり（複合処理: DB 削除・ファイル削除・OriginalImage リセット）
    [入力] target_ids: list[UUID]（対象 OriginalImage の id 一覧）
           stdout: 進捗表示用（任意、None でも可）
    [出力] dict (
             targets: 処理した OriginalImage 数,
             business_cards: 削除した BusinessCard 数,
             card_image_files: 物理削除した card_image ファイル数,
             orphan_persons: 削除した孤児 Person 数,
           )
    """
    summary = {
        "targets": 0,
        "business_cards": 0,
        "card_image_files": 0,
        "orphan_persons": 0,
    }

    for target_id in target_ids:
        bc_ids = list(
            BusinessCard.objects.filter(original_image_id=target_id).values_list("id", flat=True)
        )
        file_paths = [
            bc.card_image.path
            for bc in BusinessCard.objects.filter(id__in=bc_ids)
            if bc.card_image
        ]
        person_ids = set(
            Contact.objects.filter(business_card_id__in=bc_ids).values_list("person_id", flat=True)
        )

        # BusinessCard 削除（CASCADE: Contact, ContactFieldConfidence）
        BusinessCard.objects.filter(id__in=bc_ids).delete()

        # 孤児 Person 削除（他 Contact が参照していないものだけ）
        orphan_count = 0
        for pid in person_ids:
            if not Contact.objects.filter(person_id=pid).exists():
                Person.objects.filter(id=pid).delete()
                orphan_count += 1

        # card_image ファイル削除（DB が真、ファイルは派生）
        file_count = 0
        for path in file_paths:
            if os.path.exists(path):
                try:
                    os.remove(path)
                    file_count += 1
                except OSError as e:
                    if stdout:
                        stdout.write(f"  WARN: file delete failed: {path}: {e}")

        # OriginalImage リセット
        OriginalImage.objects.filter(id=target_id).update(
            raw_json=None,
            status=OriginalImage.STATUS_PENDING,
            error_message="",
            detected_count=0,
        )

        if stdout:
            stdout.write(
                f"  reset {target_id}: "
                f"bc_deleted={len(bc_ids)}, files_deleted={file_count}, orphans={orphan_count}"
            )

        summary["targets"] += 1
        summary["business_cards"] += len(bc_ids)
        summary["card_image_files"] += file_count
        summary["orphan_persons"] += orphan_count

    return summary


def _query_target_ids(target_id=None, limit=None):
    """[性質] 準関数（DB 読み取り）/ 対象 OriginalImage の id 一覧を返す"""
    qs = OriginalImage.objects.all().order_by("created_at")
    if target_id:
        try:
            uuid_obj = uuid.UUID(str(target_id))
        except ValueError as e:
            raise CommandError(f"invalid UUID: {target_id} ({e})")
        qs = qs.filter(id=uuid_obj)
    ids = list(qs.values_list("id", flat=True))
    if limit is not None and limit > 0:
        ids = ids[:limit]
    return ids
