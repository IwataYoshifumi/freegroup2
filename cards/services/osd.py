"""Tesseract OSD による名刺クロップの正立化（設計方針 OpenCV_OSD_rev2 第2部）。

OCR に渡す前に名刺クロップの向き（0/90/180/270）を Tesseract OSD で判定し、正立画像を作る。
script は使わず orientation（rotate 量）のみ使用。OSD 失敗（例外・タイムアウト）時は呼び出し側で
normal フォールバックする想定で、本モジュールの detect 関数は失敗を例外として送出する。

[回転方向の規約]
- Tesseract image_to_osd の "Rotate" は「**時計回り(CW)で正立させる度数**」（実機検証で確定：
  card3 を 90°CW 倒した入力に OSD が Rotate=270 を返し、270°CW=90°CCW で正立する＝CW 基準）。
- PIL.Image.rotate(θ, expand=True) は **反時計回り(CCW)** が正符号（実画像で確認済み：
  rotate(90,expand) で元の右上画素が左上へ移動＝CCW）。
- 符号が逆のため、CW 度数 rotate_deg を PIL へは **符号反転して** 渡す：
  image.rotate((360 - rotate_deg) % 360, expand=True)
  （0→0 / 90→rotate(270) / 180→rotate(180) / 270→rotate(90)＝いずれも CW 回転）。
"""

import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

_ROTATE_RE = re.compile(r"Rotate:\s*(\d+)")


def detect_osd_rotation(image, timeout: float | None = None) -> int:
    """[性質] 副作用あり（外部プロセス Tesseract 呼び出し）/ 正立化に要する回転量（度）を返す。

    [入力] image: PIL.Image（名刺クロップ）、timeout: image_to_osd のタイムアウト秒
    [出力] int: 正立化に必要な時計回り(CW)回転量（0/90/180/270。Tesseract Rotate そのまま）
    [例外] OSD 失敗（pytesseract 未導入・Tesseract 例外・Too few characters・timeout 等）は
           そのまま送出する（呼び出し側が捕捉して normal フォールバックする責務）。
    """
    import pytesseract  # 遅延 import（未導入環境でモジュール import を壊さない）

    cmd = getattr(settings, "TESSERACT_CMD", "") or ""
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    if timeout is None:
        timeout = float(getattr(settings, "OSD_TIMEOUT_SEC", 10))

    osd_text = pytesseract.image_to_osd(image, timeout=timeout)
    m = _ROTATE_RE.search(osd_text or "")
    if not m:
        raise ValueError(f"OSD 応答に Rotate が見つかりません: {osd_text!r}")
    return int(m.group(1)) % 360


def apply_upright(image, rotate_deg: int):
    """[性質] 純関数（DB操作なし・副作用なし）/ rotate_deg（CW度数）ぶん時計回りに回して正立画像を返す。

    rotate_deg=0 のときは入力をそのまま返す。expand=True で角を切らない。
    Tesseract Rotate は時計回り(CW)基準、PIL.rotate は反時計回り(CCW)が正符号のため、
    符号反転 (360 - rotate_deg) % 360 を PIL へ渡して CW 回転を実現する（本モジュール docstring 参照）。
    """
    if not rotate_deg % 360:
        return image
    return image.rotate((360 - rotate_deg % 360) % 360, expand=True)
