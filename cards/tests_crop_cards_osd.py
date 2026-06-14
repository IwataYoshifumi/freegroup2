"""OSD 殻（ORIENTATION_BACKEND=OSD）の結線テスト。

向き正位化コンセント導入後、OSD は温存バックエンド（cards.orientation.backends.OsdBackend）
として残る。本テストは ORIENTATION_BACKEND=OSD のとき _create_card が完走し、osd_json が
従来構造で埋まり、契約値ラベル（CW正立度数→(360-CW)%360）で transpose 正位化されることを確認する。
既定 PP-LCNet 経路の osd_json=None は cards/orientation/tests.py 側で確認する。
"""

import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from PIL import Image

from cards.models import BusinessCard, OriginalImage
from cards.orientation.backends import STATUS_FAILED, STATUS_OK, OsdBackend
from cards.tasks.crop_cards import Run_Crop_Cards_From_OriginalImage

User = get_user_model()

_OSD = "cards.services.osd.{}"          # OsdBackend は detect() 内で source module から import
_SAVE = "cards.tasks.crop_cards.save_card_image"


@override_settings(
    ORIENTATION_BACKEND="OSD",
    MEDIA_ROOT=tempfile.mkdtemp(prefix="osd_crop_test_"),
)
class CreateCardOsdWiringTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("osd_tester", "osd@example.com", "x")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_OPENCV_PROCESSING,
        )
        self.runner = Run_Crop_Cards_From_OriginalImage(self.original)
        self.warped = Image.new("RGB", (300, 180), "white")

    def _bc(self, card_index=0):
        return BusinessCard.objects.get(original_image=self.original, card_index=card_index)

    def test_create_card_osd_success_writes_osd_json(self):
        osd_info = {"orientation_deg": 0, "rotate": 0, "orientation_conf": 12.3,
                    "script": "Japanese", "script_conf": 4.5}
        tess = {"lang": "jpn+jpn_vert"}
        with mock.patch(_OSD.format("detect_osd_rotation"), return_value=0) as m_rot, \
             mock.patch(_OSD.format("detect_osd_info"), return_value=osd_info), \
             mock.patch(_OSD.format("run_tesseract_ocr"), return_value=tess):
            self.runner._create_card(self.warped, 0)

        bc = self._bc()
        self.assertTrue(bool(bc.card_image))
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.PENDING)
        self.assertEqual(bc.osd_json["schema_version"], "1.0.0")
        self.assertEqual(bc.osd_json["osd"], osd_info)
        self.assertEqual(bc.osd_json["tesseract_ocr"], tess)
        # cw=0 → 契約 label=0 → straight、score=1.0 番兵
        self.assertEqual(bc.orientation_correction, "straight")
        self.assertEqual(bc.orientation_score, 1.0)
        m_rot.assert_called_once()

    def test_create_card_rotates_via_contract_label(self):
        # cw=90（時計回り90で正立）→ 契約 label=(360-90)%360=270 → transpose(ROTATE_270)
        with mock.patch(_OSD.format("detect_osd_rotation"), return_value=90), \
             mock.patch(_OSD.format("detect_osd_info"), return_value={"rotate": 90}), \
             mock.patch(_OSD.format("run_tesseract_ocr"), return_value={"lang": "jpn"}), \
             mock.patch(_SAVE, return_value=(True, "cards/x-0.jpg", "")) as m_save:
            self.runner._create_card(self.warped, 0)

        saved_img = m_save.call_args.args[0]
        self.assertEqual(saved_img.size, (180, 300), "ROTATE_270 で 300x180→180x300")
        bc = self._bc()
        self.assertEqual(bc.orientation_correction, "rotated")
        self.assertEqual(bc.osd_json["osd"], {"rotate": 90})

    def test_create_card_osd_all_raise_completes_failed(self):
        boom = RuntimeError("pytesseract not available")
        with mock.patch(_OSD.format("detect_osd_rotation"), side_effect=boom), \
             mock.patch(_OSD.format("detect_osd_info"), side_effect=boom), \
             mock.patch(_OSD.format("run_tesseract_ocr"), side_effect=boom), \
             mock.patch(_SAVE, return_value=(True, "cards/x-0.jpg", "")) as m_save:
            self.runner._create_card(self.warped, 0)  # 例外は外へ伝播しない

        bc = self._bc()
        self.assertTrue(bool(bc.card_image))
        self.assertIs(m_save.call_args.args[0], self.warped, "failed は据え置き（元クロップ）")
        self.assertEqual(bc.orientation_correction, "failed")
        self.assertEqual(bc.orientation_score, 0.0)
        self.assertIn("error", bc.osd_json["osd"])
        self.assertIn("error", bc.osd_json["tesseract_ocr"])

    def test_create_card_save_fail_still_writes_osd_json(self):
        with mock.patch(_OSD.format("detect_osd_rotation"), return_value=0), \
             mock.patch(_OSD.format("detect_osd_info"), return_value={"rotate": 0}), \
             mock.patch(_OSD.format("run_tesseract_ocr"), return_value={"lang": "jpn"}), \
             mock.patch(_SAVE, return_value=(False, None, "書き込み失敗")):
            self.runner._create_card(self.warped, 0)

        bc = self._bc()
        self.assertFalse(bool(bc.card_image))
        self.assertEqual(bc.osd_json["osd"], {"rotate": 0})


class OsdBackendContractTests(TestCase):
    """OsdBackend 単体：CW正立度数→契約値ラベルの変換と osd_json 構造。"""

    def _detect_with(self, cw):
        with mock.patch(_OSD.format("detect_osd_rotation"), return_value=cw), \
             mock.patch(_OSD.format("detect_osd_info"), return_value={"rotate": cw}), \
             mock.patch(_OSD.format("run_tesseract_ocr"), return_value={"lang": "jpn"}):
            b = OsdBackend()
            return b, b.detect(Image.new("RGB", (50, 50)))

    def test_contract_conversion(self):
        # CW度数 → (360-CW)%360
        for cw, expected in [(0, 0), (90, 270), (180, 180), (270, 90)]:
            _, res = self._detect_with(cw)
            self.assertEqual(res.label, expected, f"cw={cw}")
            self.assertEqual(res.status, STATUS_OK)
            self.assertEqual(res.score, 1.0)

    def test_osd_json_structure_filled(self):
        b, _ = self._detect_with(0)
        self.assertEqual(set(b.osd_json), {"schema_version", "osd", "tesseract_ocr"})

    def test_rotation_failure_returns_failed(self):
        with mock.patch(_OSD.format("detect_osd_rotation"), side_effect=RuntimeError("x")), \
             mock.patch(_OSD.format("detect_osd_info"), return_value={"rotate": 0}), \
             mock.patch(_OSD.format("run_tesseract_ocr"), return_value={"lang": "jpn"}):
            res = OsdBackend().detect(Image.new("RGB", (50, 50)))
        self.assertEqual((res.label, res.score, res.status), (0, 0.0, STATUS_FAILED))
