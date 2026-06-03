"""crop_cards への OSD 結線（_create_card / _build_osd_record）の動作確認テスト。

第2段-第3陣（結線）。OSD が正常なケース・OSD が例外を投げるケースの両方で、
_create_card が完走し BC が作られ osd_json が埋まる（成功時は osd/tesseract_ocr、
失敗時は error）ことを確認する。OCR 本体（Claude API）は走らせない。
"""

import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from PIL import Image

from cards.models import BusinessCard, OriginalImage
from cards.tasks import crop_cards
from cards.tasks.crop_cards import Run_Crop_Cards_From_OriginalImage

User = get_user_model()

_OSD_PATH = "cards.tasks.crop_cards.{}"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="osd_crop_test_"))
class CreateCardOsdWiringTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="osd_tester", email="osd@example.com", password="x",
        )
        self.original = OriginalImage.objects.create(
            user=self.user,
            status=OriginalImage.STATUS_OPENCV_PROCESSING,
        )
        self.runner = Run_Crop_Cards_From_OriginalImage(self.original)
        self.warped = Image.new("RGB", (300, 180), "white")

    def _bc(self, card_index=0):
        return BusinessCard.objects.get(
            original_image=self.original, card_index=card_index,
        )

    # ── 正常系：OSD/OCR が成功 ────────────────────────────────
    def test_create_card_osd_success_writes_osd_json(self):
        osd_info = {
            "orientation_deg": 0, "rotate": 0, "orientation_conf": 12.3,
            "script": "Japanese", "script_conf": 4.5,
        }
        tess = {"lang": "jpn+jpn_vert"}
        with mock.patch(_OSD_PATH.format("detect_osd_rotation"), return_value=0) as m_rot, \
             mock.patch(_OSD_PATH.format("detect_osd_info"), return_value=osd_info), \
             mock.patch(_OSD_PATH.format("run_tesseract_ocr"), return_value=tess):
            self.runner._create_card(self.warped, 0)

        bc = self._bc()
        self.assertTrue(bool(bc.card_image), "正立画像が card_image として保存される")
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.PENDING)
        self.assertEqual(bc.osd_json["schema_version"], "1.0.0")
        self.assertEqual(bc.osd_json["osd"], osd_info)
        self.assertEqual(bc.osd_json["tesseract_ocr"], tess)
        m_rot.assert_called_once()

    # ── 正常系：rotate≠0 のとき apply_upright が呼ばれ、その画像が save される ──
    def test_create_card_rotates_and_saves_upright(self):
        rotated = Image.new("RGB", (180, 300), "black")  # apply_upright の戻り（別画像）
        with mock.patch(_OSD_PATH.format("detect_osd_rotation"), return_value=90), \
             mock.patch(_OSD_PATH.format("apply_upright"), return_value=rotated) as m_up, \
             mock.patch(_OSD_PATH.format("detect_osd_info"), return_value={"rotate": 90}), \
             mock.patch(_OSD_PATH.format("run_tesseract_ocr"), return_value={"lang": "jpn"}), \
             mock.patch(_OSD_PATH.format("save_card_image"),
                        return_value=(True, "cards/x-0.jpg", "")) as m_save:
            self.runner._create_card(self.warped, 0)

        m_up.assert_called_once()                       # rotate≠0 → 正立化される
        saved_img = m_save.call_args.args[0]            # save に渡された画像
        self.assertIs(saved_img, rotated, "save_card_image には正立後画像が渡る")
        self.assertEqual(self._bc().osd_json["osd"], {"rotate": 90})

    # ── OSD失敗系：全 OSD 呼び出しが例外 → 完走・error 記録・normal フォールバック ──
    def test_create_card_osd_all_raise_completes_with_error(self):
        boom = RuntimeError("pytesseract not available")
        with mock.patch(_OSD_PATH.format("detect_osd_rotation"), side_effect=boom), \
             mock.patch(_OSD_PATH.format("detect_osd_info"), side_effect=boom), \
             mock.patch(_OSD_PATH.format("run_tesseract_ocr"), side_effect=boom), \
             mock.patch(_OSD_PATH.format("save_card_image"),
                        return_value=(True, "cards/x-0.jpg", "")) as m_save:
            # 例外が _create_card の外へ伝播しない（切り出し完走）こと
            self.runner._create_card(self.warped, 0)

        bc = self._bc()
        self.assertTrue(bool(bc.card_image))
        # normal フォールバック：save には元クロップ（warped）が渡る
        self.assertIs(m_save.call_args.args[0], self.warped)
        self.assertIn("error", bc.osd_json["osd"])
        self.assertIn("error", bc.osd_json["tesseract_ocr"])
        self.assertEqual(bc.osd_json["schema_version"], "1.0.0")

    # ── 切り抜き失敗でも osd_json は書かれる（card_image=None・osd_json 充填）──
    def test_create_card_save_fail_still_writes_osd_json(self):
        with mock.patch(_OSD_PATH.format("detect_osd_rotation"), return_value=0), \
             mock.patch(_OSD_PATH.format("detect_osd_info"), return_value={"rotate": 0}), \
             mock.patch(_OSD_PATH.format("run_tesseract_ocr"), return_value={"lang": "jpn"}), \
             mock.patch(_OSD_PATH.format("save_card_image"),
                        return_value=(False, None, "書き込み失敗")):
            self.runner._create_card(self.warped, 0)

        bc = self._bc()
        self.assertFalse(bool(bc.card_image), "切り抜き失敗 → card_image=None")
        self.assertEqual(bc.osd_json["osd"], {"rotate": 0})
        self.assertEqual(bc.osd_json["tesseract_ocr"], {"lang": "jpn"})

    # ── ヘルパー単体：osd_json の構造と normal フォールバックの戻り ──
    def test_build_osd_record_structure(self):
        with mock.patch(_OSD_PATH.format("detect_osd_rotation"), return_value=0), \
             mock.patch(_OSD_PATH.format("detect_osd_info"), return_value={"rotate": 0}), \
             mock.patch(_OSD_PATH.format("run_tesseract_ocr"), return_value={"lang": "jpn"}):
            upright, osd_json = self.runner._build_osd_record(self.warped, 0)

        self.assertIs(upright, self.warped, "rotate=0 は元画像をそのまま返す")
        self.assertEqual(set(osd_json), {"schema_version", "osd", "tesseract_ocr"})
        self.assertEqual(osd_json["schema_version"], crop_cards._OSD_JSON_SCHEMA_VERSION)
