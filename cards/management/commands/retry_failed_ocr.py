"""status=failed の OriginalImage を pending に戻す管理コマンド（仕様書 v1.2.2 §8.5.5）。

開発・運用ツール。エンドユーザー向けではない（v1.2.2 §12.1）。
対象: status=failed AND BusinessCard 0件 のレコード
処理: raw_json を null クリア・error_message クリア・status を pending・detected_count=0
保存後は次の cron 起動で process_pending が拾う。
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from cards.models import OriginalImage


class Command(BaseCommand):
    help = (
        "status=failed AND BusinessCard 0件 の OriginalImage を pending に戻す。"
        "開発・運用ツール（エンドユーザー向けではない）。"
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--all",
            action="store_true",
            help="failed 全件（BusinessCard 0件）を対象にする",
        )
        group.add_argument(
            "--id",
            type=str,
            metavar="UUID",
            help="UUID 指定で 1 件だけ対象にする",
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
        """[性質] 副作用あり（DB 書き込み）

        [入力] --all / --id <UUID>（排他必須）、--limit、--dry-run
        [出力] None（処理結果は stdout に出力）
        """
        target_ids = retry_failed_ocr(
            target_id=options.get("id"),
            limit=options.get("limit"),
            dry_run=options["dry_run"],
            stdout=self.stdout,
        )

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(
                f"dry-run: would retry {len(target_ids)} record(s)"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"retry_failed_ocr: {len(target_ids)} 件を pending に戻しました。"
                "次回 process_pending で再処理されます。"
            ))


def retry_failed_ocr(target_id=None, limit=None, dry_run=False, stdout=None):
    """status=failed の OriginalImage を pending に戻す（開発・運用ツール）。

    [性質] 副作用あり（複合処理: DB 書き込み・再投入）
    [入力] target_id: UUID 文字列で 1 件指定（None なら全件対象）
           limit: 件数上限（None なら無制限）
           dry_run: True なら実際の更新をしない
           stdout: 進捗表示用（None でも可）
    [出力] list[UUID]（対象になった OriginalImage の id 一覧）
    [例外] CommandError: target_id が UUID として不正な場合
    """
    qs = _query_target(target_id)
    target_ids = list(qs.values_list("id", flat=True))
    if limit is not None and limit > 0:
        target_ids = target_ids[:limit]

    if not target_ids:
        if stdout:
            stdout.write("対象なし")
        return target_ids

    if stdout:
        for tid in target_ids:
            stdout.write(f"  target: {tid}")

    if dry_run:
        return target_ids

    OriginalImage.objects.filter(
        id__in=target_ids,
        status=OriginalImage.STATUS_FAILED,
    ).update(
        status=OriginalImage.STATUS_PENDING,
        raw_json=None,
        error_message="",
        detected_count=0,
    )
    return target_ids


def _query_target(target_id):
    """[性質] 準関数（DB 読み取り） / 対象 OriginalImage の queryset を返す"""
    qs = OriginalImage.objects.filter(
        status=OriginalImage.STATUS_FAILED,
        businesscard__isnull=True,
    ).distinct()
    if target_id:
        try:
            uuid_obj = uuid.UUID(str(target_id))
        except ValueError as e:
            raise CommandError(f"invalid UUID: {target_id} ({e})")
        qs = qs.filter(id=uuid_obj)
    return qs.order_by("created_at")
