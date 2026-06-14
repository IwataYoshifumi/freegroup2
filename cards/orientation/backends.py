"""向き判定バックエンド（コンセント）：抽象インターフェース＋実装3種。

OCR_BACKEND と同じ三層思想（抽象＋実装＋.env 切替）。判定ロジックを process_opencv 本体に
癒着させない（v0.3 §3.1）。戻り値は 3 要素 OrientationResult(label, score, status)。

[出力（回転量ラベル）の契約 / v0.3 §3.4]
  label は transpose に渡す反時計回りラベル（＝現状の時計回り回転量、0/90/180/270）に一義化する。
  正位化は image.transpose(ROTATE_{label})。各実装が自座標系からこの契約値へ変換する責務を持つ。
  - PP-LCNet: モデル出力ラベルをそのまま契約値とする。
  - OSD 殻 : Tesseract の「CW 正立度数」を (360 − CW) % 360 で契約値へ変換する。

[例外フェイルセーフ / v0.3 §3.5・§7.3]
  判定器が例外を吐いてもパイプラインを巻き込んで落とさない。基底 detect() が一発で捕捉し
  OrientationResult(0, 0.0, STATUS_FAILED) を返す（呼び出し側は据え置きで OCR へ流す）。
"""

import logging
from collections import namedtuple

from django.conf import settings

logger = logging.getLogger(__name__)

# 3 要素の戻り値（v0.3 §3.5）。
OrientationResult = namedtuple("OrientationResult", ["label", "score", "status"])

# status（3要素目）。
STATUS_OK = "ok"
STATUS_FAILED = "failed"            # 判定器が例外で落ちた（→据え置き・field=failed）
STATUS_PASSTHROUGH = "passthrough"  # none 経路（正位化を一切行わない・field=null）

# PaddleX 正本 label_list の順（index→度数）。parity でクラス順一致を実証済み。
_LABELS = [0, 90, 180, 270]


class OrientationBackend:
    """向き判定バックエンドの抽象基底。detect() がフェイルセーフを一括で挟む。"""

    def detect(self, image) -> OrientationResult:
        """[性質] 副作用あり（実装依存）/ 例外を捕捉し、失敗時は (0, 0.0, failed)。"""
        try:
            return self._detect(image)
        except Exception as e:  # noqa: BLE001 — パイプラインを落とさないのが責務
            logger.warning(
                "orientation detect failed (%s): %s", type(self).__name__, e
            )
            return OrientationResult(0, 0.0, STATUS_FAILED)

    def _detect(self, image) -> OrientationResult:
        raise NotImplementedError


class PPLCNetBackend(OrientationBackend):
    """PP-LCNet_x1_0_doc_ori（ONNX＋onnxruntime）。既定バックエンド。"""

    def _detect(self, image) -> OrientationResult:
        import numpy as np

        from cards.orientation.onnx_session import run_softmax
        from cards.orientation.preprocess import preprocess_pil

        out = run_softmax(preprocess_pil(image))  # (4,) softmax 済み確率
        idx = int(np.argmax(out))
        # label はモデル出力＝契約値（current CW 回転量）。score は生の softmax 値（二重softmax禁止）。
        return OrientationResult(_LABELS[idx], float(out[idx]), STATUS_OK)


class OsdBackend(OrientationBackend):
    """OSD（Tesseract）殻（温存・差し戻し用）。既定パイプラインからは外れる。

    detect() を上書きし、各 OSD 呼び出しを個別に try/except で包んで osd_json を必ず組み立てる
    （従来 crop_cards._build_osd_record 相当）。正位化自体はパイプラインの transpose が担うため
    apply_upright は呼ばない。OSD は softmax を持たないため score=1.0 の番兵を返し、閾値経路で
    「rotate≠0 のとき必ず回す」従来挙動を保つ（実 conf は osd_json.osd.orientation_conf に残る）。
    """

    _OSD_JSON_SCHEMA_VERSION = "1.0.0"

    def __init__(self):
        self.osd_json = None  # OSD 経路のみ充填。パイプラインが BC.osd_json へ渡す。

    def detect(self, image) -> OrientationResult:
        from cards.services.osd import detect_osd_info, detect_osd_rotation, run_tesseract_ocr

        timeout = float(getattr(settings, "OSD_TIMEOUT_SEC", 10))

        # ① 向き（CW 正立度数 → 契約値）。失敗時は label0・score0・failed。
        label, score, status = 0, 0.0, STATUS_OK
        try:
            cw = detect_osd_rotation(image, timeout=timeout)
            label = (360 - cw % 360) % 360          # 契約値へ変換（v0.3 §3.4）
            score = 1.0                             # OSD は softmax 無し → 番兵（常に回す）
        except Exception as e:  # noqa: BLE001
            logger.info("OSD rotation 失敗→failed: %s: %s", type(e).__name__, e)
            label, score, status = 0, 0.0, STATUS_FAILED

        # ② osd_json（記録専用・従来構造）。各ブロックは独立 try/except。
        try:
            osd_block = detect_osd_info(image, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            osd_block = {"error": f"{type(e).__name__}: {e}"}
        try:
            tess_block = run_tesseract_ocr(image, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            tess_block = {"error": f"{type(e).__name__}: {e}"}
        self.osd_json = {
            "schema_version": self._OSD_JSON_SCHEMA_VERSION,
            "osd": osd_block,
            "tesseract_ocr": tess_block,
        }
        return OrientationResult(label, score, status)


class NoneBackend(OrientationBackend):
    """none（パススルー）。正位化を一切行わず素通し。緊急時に既存挙動へ完全復帰する口。"""

    def detect(self, image) -> OrientationResult:
        return OrientationResult(0, 0.0, STATUS_PASSTHROUGH)
