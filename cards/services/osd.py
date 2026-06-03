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
# image_to_osd の他項目（記録・分析用。正立判定には使わない）。
_ORIENT_DEG_RE = re.compile(r"Orientation in degrees:\s*(\d+)")
_ORIENT_CONF_RE = re.compile(r"Orientation confidence:\s*([\d.]+)")
_SCRIPT_RE = re.compile(r"Script:\s*(\S+)")
_SCRIPT_CONF_RE = re.compile(r"Script confidence:\s*([\d.]+)")

# OCR 既定言語（縦書き名刺があるため vertical も併用）。settings.TESSERACT_OCR_LANG で上書き可。
_DEFAULT_OCR_LANG = "jpn+jpn_vert"
# image_to_data の word レベル（page/block/para/line は 1〜4、word が 5）。
_WORD_LEVEL = 5


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


def _set_tesseract_cmd(pytesseract):
    """[性質] 副作用あり（pytesseract のグローバル設定）/ settings.TESSERACT_CMD があれば反映。"""
    cmd = getattr(settings, "TESSERACT_CMD", "") or ""
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def detect_osd_info(image, timeout: float | None = None) -> dict:
    """[性質] 副作用あり（外部プロセス Tesseract 呼び出し）/ OSD 全項目を dict で返す（記録・分析用）。

    detect_osd_rotation と異なり、正立判定には使わず image_to_osd の出力を丸ごと拾って残す用途。
    [入力] image: PIL.Image（名刺クロップ）、timeout: image_to_osd のタイムアウト秒
    [出力] dict: {orientation_deg, rotate, orientation_conf, script, script_conf}
           （見つからない項目は None。rotate は CW-to-upright 度数で detect_osd_rotation と同義）
    [例外] OSD 失敗（pytesseract 未導入・Tesseract 例外・Too few characters・timeout 等）は
           そのまま送出する（呼び出し側が捕捉して記録する責務）。
    """
    import pytesseract  # 遅延 import（未導入環境でモジュール import を壊さない）

    _set_tesseract_cmd(pytesseract)
    if timeout is None:
        timeout = float(getattr(settings, "OSD_TIMEOUT_SEC", 10))

    osd_text = pytesseract.image_to_osd(image, timeout=timeout)

    def _grab(rx, cast):
        m = rx.search(osd_text or "")
        if not m:
            return None
        try:
            return cast(m.group(1))
        except (TypeError, ValueError):
            return None

    return {
        "orientation_deg": _grab(_ORIENT_DEG_RE, int),
        "rotate": _grab(_ROTATE_RE, lambda v: int(v) % 360),
        "orientation_conf": _grab(_ORIENT_CONF_RE, float),
        "script": _grab(_SCRIPT_RE, str),
        "script_conf": _grab(_SCRIPT_CONF_RE, float),
    }


def _words_from_data(data: dict) -> list:
    """[性質] 純関数（DB操作なし・副作用なし）/ image_to_data(DICT) を word レベルの記録に整形する。

    level=5（word）かつ非空テキストの行のみ残す。level 1〜4（page/block/para/line）の
    container 行は text 空・conf=-1 のため落とす。bbox・conf に加え block/par/line/word_num を
    残して行段落構造を保つ。
    [入力] data: pytesseract.image_to_data(output_type=DICT) の戻り（列名→リストの dict）
    [出力] list[dict]: 単語ごとの {text, conf, left, top, width, height, block_num, par_num,
            line_num, word_num}
    """
    texts = data.get("text") or []
    levels = data.get("level") or []
    words = []
    for i in range(len(texts)):
        text = (texts[i] or "").strip()
        try:
            level = int(levels[i])
        except (TypeError, ValueError, IndexError):
            level = None
        if level != _WORD_LEVEL or not text:
            continue

        def _int(col, default=None):
            try:
                return int((data.get(col) or [])[i])
            except (TypeError, ValueError, IndexError):
                return default

        try:
            conf = float((data.get("conf") or [])[i])
        except (TypeError, ValueError, IndexError):
            conf = None

        words.append({
            "text": text,
            "conf": conf,
            "left": _int("left"),
            "top": _int("top"),
            "width": _int("width"),
            "height": _int("height"),
            "block_num": _int("block_num"),
            "par_num": _int("par_num"),
            "line_num": _int("line_num"),
            "word_num": _int("word_num"),
        })
    return words


def run_tesseract_ocr(image, lang: str | None = None, timeout: float | None = None) -> dict:
    """[性質] 副作用あり（外部プロセス Tesseract 呼び出し）/ OCR テキストと単語データを返す（記録・分析用）。

    image_to_string（全文テキスト）と image_to_data（単語ごと bbox/conf）を取得する。
    OCR テキスト・confidence は記録専用で、向き判定・クロップ採否には使わない。
    [入力] image: PIL.Image、lang: 既定 settings.TESSERACT_OCR_LANG or "jpn+jpn_vert"、timeout 秒
    [出力] dict: {"lang": str, "text": str, "data": list[dict]（_words_from_data 整形済み）}
    [例外] Tesseract 失敗（未導入・言語データ欠落・timeout 等）はそのまま送出する
           （呼び出し側が捕捉して記録する責務）。
    """
    import pytesseract  # 遅延 import

    _set_tesseract_cmd(pytesseract)
    if lang is None:
        lang = getattr(settings, "TESSERACT_OCR_LANG", "") or _DEFAULT_OCR_LANG
    if timeout is None:
        timeout = float(getattr(settings, "OSD_TIMEOUT_SEC", 10))

    text = pytesseract.image_to_string(image, lang=lang, timeout=timeout)
    raw = pytesseract.image_to_data(
        image, lang=lang, timeout=timeout, output_type=pytesseract.Output.DICT
    )
    return {"lang": lang, "text": text, "data": _words_from_data(raw)}
