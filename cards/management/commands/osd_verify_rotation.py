"""OSD 回転方向の目視確認コマンド（rev2 第2部・リリースブロッカー確認用・半自動）。

正立した名刺画像1枚を 0/90/180/270 度に倒し、それぞれを detect_osd_rotation → apply_upright に
通して「正立に戻るか」を **人間が目視** で確認するための補助コマンド。
取り違え（180度ズレ等）は例外を出さないため自動判定はせず、補正後画像を保存して目視を促す。

使い方：
  python manage.py osd_verify_rotation                 # 既定: 検証画像⑤(67a044a2)を使用
  python manage.py osd_verify_rotation --image <path>  # 任意の正立名刺画像を指定

保存先：MEDIA_ROOT/corrected/osd_verify/<stem>-in<入力角>-osd<Rotate>-out.jpg
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

# 既定の検証画像（⑤ 名刺1枚・超簡単）。
_DEFAULT_OID = "67a044a2-47f0-4c29-b860-1bda870675f2"
# PIL.rotate は反時計回り。「入力をこの角だけ CCW で倒す」→ OSD が正立に戻せるかを見る。
_INPUT_ANGLES = (0, 90, 180, 270)


class Command(BaseCommand):
    help = (
        "正立名刺を 0/90/180/270 度に倒して OSD 正立化を通し、補正後画像を保存する"
        "（正誤は目視で判断。リリース前の方向確認用）。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--image",
            type=str,
            default=None,
            help="正立した名刺画像のパス。省略時は検証画像⑤(67a044a2)の元画像を使う。",
        )

    def handle(self, *args, **options):
        """[性質] 副作用あり（外部プロセス Tesseract 呼び出し・ファイル書き込み）。"""
        from cards.services.osd import apply_upright, detect_osd_rotation

        src_path = options["image"] or self._default_image_path()
        if not src_path or not os.path.exists(src_path):
            raise CommandError(
                f"画像が見つかりません: {src_path!r}。--image で正立名刺画像のパスを指定してください。"
            )

        try:
            base = Image.open(src_path).convert("RGB")
        except Exception as e:
            raise CommandError(f"画像を開けません（{type(e).__name__}: {e}）: {src_path}")

        out_dir = os.path.join(settings.MEDIA_ROOT, "corrected", "osd_verify")
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(src_path))[0]

        self.stdout.write(f"入力画像: {src_path}")
        self.stdout.write("入力角 | OSD Rotate | 適用回転 | 保存先")
        self.stdout.write("-" * 70)

        for ang in _INPUT_ANGLES:
            # PIL.rotate(ang) は CCW。ang 度倒した「お題」画像を作る。
            tilted = base.rotate(ang, expand=True) if ang else base
            try:
                rotate_deg = detect_osd_rotation(tilted)
                osd_disp = str(rotate_deg)
            except Exception as e:
                rotate_deg = 0
                osd_disp = f"失敗({type(e).__name__})→0"
            corrected = apply_upright(tilted, rotate_deg)
            rel = f"{stem}-in{ang}-osd{rotate_deg}-out.jpg"
            abs_path = os.path.join(out_dir, rel)
            try:
                corrected.save(abs_path, format="JPEG", quality=95)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  保存失敗 in{ang}: {e}"))
                continue
            self.stdout.write(
                f"  {ang:>3}° | {osd_disp:>12} | {rotate_deg:>3}° | corrected/osd_verify/{rel}"
            )

        self.stdout.write("-" * 70)
        self.stdout.write(self.style.WARNING(
            "【目視確認】上記 *-out.jpg が全て正立しているか目で確認してください。"
        ))
        self.stdout.write(
            "取り違え（例: in180 の out が倒れたまま）があれば osd.py の rotate マッピングを修正。"
            "例外は出ないためログ・テストでは検知できません。これを通すまでリリースしないこと。"
        )

    def _default_image_path(self):
        """[性質] 準関数（DB 読み取り）/ 既定の検証画像⑤の元画像パスを返す（無ければ None）。"""
        from cards.models import OriginalImage
        oi = OriginalImage.objects.filter(id=_DEFAULT_OID).first()
        if oi and oi.image_file:
            try:
                return oi.image_file.path
            except Exception:
                return None
        return None
