"""cards アプリのテスト。

現状は OpenCV 検出 → PipelineCoordinator の results 受け渡しに関する再発防止スモークのみ。
（detector の返り値構造が attempts[] へ変わったのに pipeline が旧トップレベル results を
読み続け、全件 garbage 化＝BusinessCard が1件も作られなくなったバグ 1f9712f の番人）
"""

import io
import os
import shutil
import tempfile
from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from cards.models import BusinessCard, OriginalImage
from cards.services.detectors.opencv_detector import (
    detect_cards,
    detect_cards_with_debug,
    results_from_debug_result,
)


def _synthetic_card_png_bytes(w=1000, h=800, rw=600, rh=360) -> bytes:
    """[性質] 純関数 / 黒背景に白い名刺状の矩形（aspect≈1.667）を1枚置いた PNG バイト列。

    detector が確実に1枚検出できる合成画像（実画像・メディア依存・OCR API を持ち込まない）。
    """
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    x0, y0 = (w - rw) // 2, (h - rh) // 2
    arr[y0:y0 + rh, x0:x0 + rw] = 255
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


# card item スキーマを通る最小の OCR 結果（is_business_card=False で
# normalize/has_minimum_info の内部仕様に依存させず、BusinessCard 生成ループ到達のみを見る）。
_FAKE_OCR_RESULT = {
    "api_response": {
        "model": "test-model",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    },
    "cards": [
        {
            "card_meta": {"is_business_card": False, "orientation": "normal"},
            "fields": {},
            "fields_array": {},
        }
    ],
}


class ResultsAccessorTests(TestCase):
    """detector の検出結果アクセサ（attempts[-1] を知る唯一の窓口）の契約テスト。"""

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))

    def test_results_accessor_finds_detections_not_toplevel(self):
        """検出結果は attempts[-1] にあり、results_from_debug_result が detect_cards と一致する。"""
        path = os.path.join(self._tmp_media, "synthetic_card.png")
        with open(path, "wb") as f:
            f.write(_synthetic_card_png_bytes())

        debug_result = detect_cards_with_debug(path)
        # バグの前提：トップレベルに results は無い（旧 pipeline はここを読んで常に空だった）
        self.assertNotIn("results", debug_result)
        accessor = results_from_debug_result(debug_result)
        self.assertGreaterEqual(len(accessor), 1)
        # 唯一の窓口の戻りが detect_cards（同じ窓口経由）と一致すること
        self.assertEqual(len(accessor), len(detect_cards(path)))


class PipelineDetectionFlowTests(TestCase):
    """PipelineCoordinator が detector の検出結果を正しく受け取り、card 生成ループに入るか。"""

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))
        self.user = get_user_model().objects.create_superuser(
            username="pipe_test", email="p@example.com", password="x"
        )

    def _make_original(self):
        oi = OriginalImage(user=self.user, status=OriginalImage.STATUS_PROCESSING)
        oi.image_file.save(
            f"{oi.id}.png", ContentFile(_synthetic_card_png_bytes()), save=False
        )
        oi.save()
        return oi

    def test_run_pipeline_creates_business_card_from_detections(self):
        """合成名刺1枚 → detections が空にならず BusinessCard が1件作られる（garbage 打ち切りに入らない）。

        OCR（Claude API）は OcrService をモックして置換し、ネットワーク・課金を持ち込まない。
        本テストは「results が pipeline に正しく渡り card 生成ループに入る」ことの番人。
        修正前のバグ状態（pipeline が旧トップレベル results を読む）では detections=[] となり
        status=garbage で打ち切られ BusinessCard は 0 件になる。
        """
        from cards.tasks.pipeline_coordinator import PipelineCoordinator

        oi = self._make_original()

        with patch("cards.tasks.pipeline_coordinator.OcrService") as MockOcr:
            MockOcr.return_value.run_ocr.return_value = _FAKE_OCR_RESULT
            PipelineCoordinator(oi).run_pipeline()

        oi.refresh_from_db()
        # garbage 早期 return に入っていない＝検出が pipeline に渡っている
        self.assertGreaterEqual(oi.detected_count, 1)
        self.assertEqual(oi.status, OriginalImage.STATUS_EXTRACTED)
        self.assertEqual(
            BusinessCard.objects.filter(original_image=oi).count(),
            oi.detected_count,
        )
