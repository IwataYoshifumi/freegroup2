"""Tesseract OSD の疎通確認コマンド（rev2 第2部・運用ツール）。

Tesseract 本体バイナリの導入後、各環境で OSD が使える状態かを確認する。
  - settings の TESSERACT_CMD / OSD_TIMEOUT_SEC を表示
  - tesseract のバージョン（未導入なら明示）
  - 利用可能言語に "osd" が含まれるか（無いと image_to_osd は必ず失敗する）

例外は握り、原因が分かるメッセージを返す（スタックトレースを垂れ流さない）。

使い方：
  python manage.py osd_check
"""

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Tesseract OSD の疎通確認（TESSERACT_CMD/バージョン/osd 言語データの有無）。"

    def handle(self, *args, **options):
        """[性質] 副作用あり（外部プロセス Tesseract 呼び出し）/ 結果を stdout に出力。"""
        cmd = getattr(settings, "TESSERACT_CMD", "") or ""
        timeout = getattr(settings, "OSD_TIMEOUT_SEC", 10)
        self.stdout.write("=== OSD 疎通確認 ===")
        self.stdout.write(
            f"TESSERACT_CMD = {cmd!r}（空なら PATH の tesseract を使用）"
        )
        self.stdout.write(f"OSD_TIMEOUT_SEC = {timeout}")

        # pytesseract（pip パッケージ）の有無
        try:
            import pytesseract
        except ImportError:
            self.stdout.write(self.style.ERROR(
                "pytesseract（pip）が未導入です。`pip install -r requirements.txt` を実行してください。"
            ))
            return

        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd

        # Tesseract 本体バージョン
        try:
            version = pytesseract.get_tesseract_version()
            self.stdout.write(self.style.SUCCESS(f"tesseract バージョン: {version}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f"Tesseract 本体が見つからない/実行できません（{type(e).__name__}: {e}）。"
            ))
            self.stdout.write(
                "→ 本体バイナリを導入し PATH を通すか、TESSERACT_CMD にフルパスを設定してください。"
            )
            return

        # 利用可能言語に osd があるか
        try:
            langs = pytesseract.get_languages(config="")
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f"言語一覧の取得に失敗（{type(e).__name__}: {e}）。"
            ))
            return

        self.stdout.write(f"利用可能言語: {sorted(langs)}")
        if "osd" in langs:
            self.stdout.write(self.style.SUCCESS(
                "osd.traineddata あり → image_to_osd が利用可能です。"
            ))
        else:
            self.stdout.write(self.style.ERROR(
                "osd が一覧にありません。osd.traineddata を tessdata に配置してください"
                "（無いと image_to_osd は必ず失敗し、向き判定は常に normal フォールバックになります）。"
            ))
