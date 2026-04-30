"""DB ↔ MEDIA_ROOT 整合検査・修復コマンド（reconcile_card_images）。

【孤児ファイル】DB に参照がないファイルを検出し、--apply で _orphan/ に退避する。
【欠損ファイル】DB にパスが記録されているが実ファイルがないものを検出し、ログ出力する。

デフォルトは dry-run（ファイル操作なし）。
"""

import logging
import os
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand

from cards.models import BusinessCard

logger = logging.getLogger(__name__)

# BusinessCard.card_image の保存ルート（MEDIA_ROOT 相対）
_CARDS_SUBDIR = "cards"
# 孤児ファイルの退避先（MEDIA_ROOT/cards/_orphan/）
_ORPHAN_SUBDIR = "_orphan"


class Command(BaseCommand):
    help = (
        "BusinessCard.card_image と MEDIA_ROOT の整合性を検査する。"
        "デフォルトは dry-run（検出のみ）。--apply で孤児ファイルを退避する。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="孤児ファイルを _orphan/ に退避する（デフォルトは dry-run）",
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（--apply 時はファイル移動）

        [入力] --apply: 修復を実行するフラグ（デフォルト False）
        [出力] None（結果は stdout / logger に出力）
        """
        apply_mode = options["apply"]

        db_paths, db_records = _scan_db()
        fs_paths = _scan_filesystem()

        orphan_rel_paths = fs_paths - db_paths
        missing_rel_paths = db_paths - fs_paths

        # ── 検出結果の出力 ─────────────────────────────────────────────────
        for rel in sorted(orphan_rel_paths):
            abs_path = os.path.join(settings.MEDIA_ROOT, rel)
            self.stdout.write(f"[孤児ファイル] {abs_path}")

        for rel in sorted(missing_rel_paths):
            abs_path = os.path.join(settings.MEDIA_ROOT, rel)
            for bc in db_records.get(rel, []):
                self.stdout.write(f"[欠損ファイル] BusinessCard ID={bc.id} → {abs_path}")

        orphan_count = len(orphan_rel_paths)
        missing_count = len(missing_rel_paths)

        if not apply_mode:
            self.stdout.write(
                f"検出件数：孤児{orphan_count}件・欠損{missing_count}件"
            )
            self.stdout.write("※修復は --apply オプションで実行できます")
            return

        # ── --apply モード ────────────────────────────────────────────────
        orphan_dir = os.path.join(settings.MEDIA_ROOT, _CARDS_SUBDIR, _ORPHAN_SUBDIR)
        os.makedirs(orphan_dir, exist_ok=True)

        moved = 0
        for rel in sorted(orphan_rel_paths):
            src_abs = os.path.join(settings.MEDIA_ROOT, rel)
            fname = os.path.basename(rel)
            dest_abs = _resolve_orphan_dest(orphan_dir, fname)
            try:
                os.rename(src_abs, dest_abs)
                self.stdout.write(f"[孤児→退避] {src_abs} → {dest_abs}")
                logger.info("orphan moved: %s -> %s", src_abs, dest_abs)
                moved += 1
            except OSError as e:
                self.stderr.write(self.style.ERROR(f"退避失敗 {src_abs}: {e}"))
                logger.warning("orphan move failed: %s: %s", src_abs, e)

        for rel in sorted(missing_rel_paths):
            abs_path = os.path.join(settings.MEDIA_ROOT, rel)
            for bc in db_records.get(rel, []):
                self.stdout.write(
                    f"[欠損ファイル] BusinessCard ID={bc.id} → {abs_path}（手動対応が必要）"
                )
                logger.warning(
                    "missing card image: BusinessCard id=%s path=%s", bc.id, abs_path
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"処理結果：孤児退避{moved}件・欠損検出{missing_count}件"
            )
        )


def _scan_db():
    """DB 上の card_image パスと BusinessCard レコードを収集する。

    [性質] 準関数（DB 読み取り）
    [出力] tuple(db_paths: set[str], db_records: dict[str, list[BusinessCard]])
           - db_paths: MEDIA_ROOT 相対パスの集合（前方スラッシュ統一）
           - db_records: 相対パス → BusinessCard リストの辞書
    """
    db_records: dict[str, list] = {}
    qs = (
        BusinessCard.objects.exclude(card_image__isnull=True)
        .exclude(card_image="")
        .only("id", "card_image")
    )
    for bc in qs.iterator():
        # ImageField は文字列として返る。Windows 対策でスラッシュ統一
        rel = str(bc.card_image).replace("\\", "/")
        db_records.setdefault(rel, []).append(bc)
    db_paths = set(db_records.keys())
    return db_paths, db_records


def _scan_filesystem():
    """MEDIA_ROOT/cards/ 配下のファイルパスを収集する。

    [性質] 副作用あり（ファイルシステム走査）
    [出力] set[str]（MEDIA_ROOT 相対パス。.tmp ファイルと _orphan/ は除外）
    """
    cards_dir = os.path.join(settings.MEDIA_ROOT, _CARDS_SUBDIR)
    fs_paths: set[str] = set()
    if not os.path.isdir(cards_dir):
        return fs_paths
    for root, dirs, files in os.walk(cards_dir):
        # _orphan/ はスキャン対象外（再帰もしない）
        dirs[:] = [d for d in dirs if d != _ORPHAN_SUBDIR]
        for fname in files:
            if fname.endswith(".tmp"):
                continue
            abs_path = os.path.join(root, fname)
            rel = os.path.relpath(abs_path, settings.MEDIA_ROOT).replace("\\", "/")
            fs_paths.add(rel)
    return fs_paths


def _resolve_orphan_dest(orphan_dir: str, fname: str) -> str:
    """衝突を回避した退避先絶対パスを返す。

    [性質] 副作用あり（時刻取得・ファイル存在確認）
    [入力] orphan_dir: 退避先ディレクトリの絶対パス
           fname: 元ファイル名
    [出力] str（退避先絶対パス。同名が既存なら タイムスタンプ付きで返す）
    """
    dest = os.path.join(orphan_dir, fname)
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(fname)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")
    return os.path.join(orphan_dir, f"{base}_{ts}{ext}")
