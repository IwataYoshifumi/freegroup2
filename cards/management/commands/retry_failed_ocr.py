"""失敗レコードを差し戻す管理コマンド（v1.5.0 / 開発・運用ツール）。

エンドユーザー向けではない（v1.2.2 §12.1）。

v1.5.0 では OpenCV 段階の失敗と OCR 段階の失敗を区別して差し戻す：

--opencv:
  対象: status=failed かつ BC 0 件 の OriginalImage（= OpenCV 段階で失敗したもの）
  処理: OriginalImage を status=pending に戻す（次回 process_opencv で拾われる）

--ocr:
  対象: ocr_status=failed の BusinessCard（= OCR 段階で失敗したもの）
  処理: BC を ocr_status=pending に戻し、関連 Contact / 孤立 Person を削除。
        所属 OriginalImage が status=extracted/failed なら cards_extracted に戻す。
        card_image は不変（OpenCV 結果を温存）。次回 process_ocr で拾われる。
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from cards.models import BusinessCard, OriginalImage
from cards.tasks.ocr_recovery import reset_bc_to_pending


class Command(BaseCommand):
    help = (
        "OpenCV 段階または OCR 段階で失敗したレコードを差し戻す。"
        "開発・運用ツール（エンドユーザー向けではない）。"
    )

    def add_arguments(self, parser):
        stage_group = parser.add_mutually_exclusive_group(required=True)
        stage_group.add_argument(
            "--opencv",
            action="store_true",
            help="OpenCV 段階の失敗（status=failed AND BC 0 件 の OriginalImage）を差し戻す",
        )
        stage_group.add_argument(
            "--ocr",
            action="store_true",
            help="OCR 段階の失敗（ocr_status=failed の BusinessCard）を差し戻す",
        )
        parser.add_argument(
            "--id",
            type=str,
            metavar="UUID",
            default=None,
            help=(
                "UUID 指定で 1 件のみ差し戻す。--opencv なら OriginalImage の UUID、"
                "--ocr なら BusinessCard の UUID。--limit は無視される。"
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="--id 未指定時の最大件数（任意、None なら無制限）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="対象の id を表示するだけで実際の差し戻しを行わない",
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（複合処理: DB 書込・再投入用ステータス遷移）"""
        if options["opencv"]:
            self._handle_opencv(options)
        else:
            self._handle_ocr(options)

    # ---------- --opencv 経路 ----------

    def _handle_opencv(self, options):
        target_ids = self._collect_opencv_target_ids(options)
        if not target_ids:
            self.stdout.write("retry_failed_ocr --opencv: 対象なし")
            return

        self.stdout.write(
            f"retry_failed_ocr --opencv: 対象 OriginalImage {len(target_ids)} 件"
        )

        if options["dry_run"]:
            for tid in target_ids:
                self.stdout.write(f"  would reset (oi): {tid}")
            self.stdout.write(self.style.SUCCESS(
                f"dry-run: would reset {len(target_ids)} OriginalImage(s)"
            ))
            return

        processed = 0
        skipped = 0
        for target_id in target_ids:
            if not self._still_eligible_opencv(target_id):
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (条件未充足: status=failed AND BC 0 件 不成立): {target_id}"
                ))
                continue
            self._reset_opencv_failed(target_id)
            self.stdout.write(f"  reset {target_id}: status=pending (oi)")
            processed += 1

        self.stdout.write(self.style.SUCCESS(
            f"retry_failed_ocr --opencv: targets={len(target_ids)}, "
            f"processed={processed}, skipped={skipped}"
        ))

    def _collect_opencv_target_ids(self, options):
        """[性質] 準関数（DB 読み取り）/ status=failed AND BC 0 件 の OriginalImage id を返す。"""
        if options["id"]:
            try:
                target_id = uuid.UUID(str(options["id"]))
            except (ValueError, TypeError) as e:
                raise CommandError(f"--id の値が UUID 形式ではありません: {options['id']} ({e})")
            if not self._still_eligible_opencv(target_id):
                self.stdout.write(self.style.WARNING(
                    f"  指定 id は条件未充足（status=failed AND BC 0 件 不成立）: {target_id}"
                ))
                return []
            return [target_id]

        qs = (
            OriginalImage.objects.filter(
                status=OriginalImage.STATUS_FAILED,
                businesscard__isnull=True,
            )
            .distinct()
            .order_by("created_at")
        )
        ids = list(qs.values_list("id", flat=True))
        limit = options.get("limit")
        if limit is not None and limit > 0:
            ids = ids[:limit]
        return ids

    @staticmethod
    def _still_eligible_opencv(original_image_id):
        """[性質] 準関数（DB 読み取り）/ status=failed AND BC 0 件 を満たすか。"""
        oi = OriginalImage.objects.filter(
            id=original_image_id, status=OriginalImage.STATUS_FAILED
        ).first()
        if oi is None:
            return False
        return not BusinessCard.objects.filter(original_image=oi).exists()

    @staticmethod
    def _reset_opencv_failed(original_image_id):
        """[性質] 副作用あり（DB 書込）/ OriginalImage を pending にリセットする。"""
        OriginalImage.objects.filter(
            id=original_image_id, status=OriginalImage.STATUS_FAILED
        ).update(
            status=OriginalImage.STATUS_PENDING,
            claimed_at=None,
            raw_json=None,
            error_message="",
            detected_count=0,
        )

    # ---------- --ocr 経路 ----------

    def _handle_ocr(self, options):
        target_ids = self._collect_ocr_target_ids(options)
        if not target_ids:
            self.stdout.write("retry_failed_ocr --ocr: 対象なし")
            return

        self.stdout.write(
            f"retry_failed_ocr --ocr: 対象 BusinessCard {len(target_ids)} 件"
        )

        if options["dry_run"]:
            for tid in target_ids:
                self.stdout.write(f"  would reset (bc): {tid}")
            self.stdout.write(self.style.SUCCESS(
                f"dry-run: would reset {len(target_ids)} BusinessCard(s)"
            ))
            return

        processed = 0
        skipped = 0
        for bc_id in target_ids:
            try:
                bc = BusinessCard.objects.get(id=bc_id)
            except BusinessCard.DoesNotExist:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"  消失: {bc_id}"))
                continue

            ok = reset_bc_to_pending(
                bc, expected_statuses=(BusinessCard.OcrStatus.FAILED,)
            )
            if ok:
                processed += 1
                self.stdout.write(
                    f"  reset {bc_id}: ocr_status=pending (oi={bc.original_image_id})"
                )
            else:
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip (状態 drift / 例外): {bc_id}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"retry_failed_ocr --ocr: targets={len(target_ids)}, "
            f"processed={processed}, skipped={skipped}"
        ))

    def _collect_ocr_target_ids(self, options):
        """[性質] 準関数（DB 読み取り）/ ocr_status=failed の BC id を並び順で返す。

        並び順: original_image__created_at 昇順 → card_index 昇順（process_ocr と揃える）
        """
        if options["id"]:
            try:
                target_id = uuid.UUID(str(options["id"]))
            except (ValueError, TypeError) as e:
                raise CommandError(f"--id の値が UUID 形式ではありません: {options['id']} ({e})")
            exists = BusinessCard.objects.filter(
                id=target_id, ocr_status=BusinessCard.OcrStatus.FAILED
            ).exists()
            if not exists:
                self.stdout.write(self.style.WARNING(
                    f"  指定 id は ocr_status=failed ではない / 存在しない: {target_id}"
                ))
                return []
            return [target_id]

        qs = (
            BusinessCard.objects.filter(ocr_status=BusinessCard.OcrStatus.FAILED)
            .order_by("original_image__created_at", "card_index")
        )
        ids = list(qs.values_list("id", flat=True))
        limit = options.get("limit")
        if limit is not None and limit > 0:
            ids = ids[:limit]
        return ids
