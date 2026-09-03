"""手動4点切り出しバックエンド（ManualCropView, 段2-b）のテスト。

- 正常系：有効な4点＋回転角で BC が1件できる／debug_json.manual_results に
  business_card_id＋polygon が入る／既存 attempts・integrated_results が温存される。
- 回転：90/180/270 で最終画像（card_image）の縦横が apply_upright どおり変わる。
- 異常系：点不足・小さすぎ warp・不正回転角 → いずれも 4xx/5xx の JSON 応答（500 で落とさない）。
- debug_json=None 始点：manual_results だけ持つ dict が作られ BC ができる。
"""

import io
import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from cards.models import BusinessCard, OriginalImage

User = get_user_model()

_MEDIA_ROOT = tempfile.mkdtemp(prefix="fg2_manual_crop_test_")

# 元画像サイズと、その中に取る切り出し矩形（W=200, H=120）。
_IMG_W, _IMG_H = 400, 300
_RECT_W, _RECT_H = 200, 120
_X0, _Y0 = 50, 40
# 任意順（BR → TL → TR → BL）で渡し、_sort_corners 経由で整列されることも兼ねて確認。
_POINTS = [
    [_X0 + _RECT_W, _Y0 + _RECT_H],  # BR
    [_X0, _Y0],                      # TL
    [_X0 + _RECT_W, _Y0],            # TR
    [_X0, _Y0 + _RECT_H],            # BL
]


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class ManualCropViewTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_superuser(username="manual_crop", password="d")
        self.client = Client()
        self.client.force_login(self.user)

    def _original(self, debug_json="default"):
        oi = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED,
        )
        buf = io.BytesIO()
        Image.new("RGB", (_IMG_W, _IMG_H), "white").save(buf, "JPEG")
        oi.image_file.save("orig.jpg", ContentFile(buf.getvalue()), save=True)
        if debug_json == "default":
            oi.debug_json = self._seed_debug_json()
            oi.save(update_fields=["debug_json"])
        elif debug_json is not None:
            oi.debug_json = debug_json
            oi.save(update_fields=["debug_json"])
        return oi

    def _seed_debug_json(self):
        """既存キー（attempts / integrated_results / image_size）を持つ debug_json。"""
        return {
            "image_size": {"width": _IMG_W, "height": _IMG_H, "area": _IMG_W * _IMG_H},
            "attempts": [
                {"attempt_no": 1, "type": "normal", "masks": {}, "results": []}
            ],
            "integrated_results": [
                {"card_index": 0, "polygon": {"top_left": {"x": 1, "y": 1}},
                 "origin": "normal"}
            ],
        }

    def _url(self, oi):
        return reverse("originals:manual_crop", kwargs={"pk": oi.id})

    def _post(self, oi, points=None, rotation=0):
        body = {"points": _POINTS if points is None else points, "rotation": rotation}
        return self.client.post(
            self._url(oi), data=json.dumps(body), content_type="application/json"
        )

    # ── 正常系 ─────────────────────────────────────────────
    def test_success_creates_bc_and_manual_result(self):
        oi = self._original()
        resp = self._post(oi, rotation=0)
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()

        bcs = BusinessCard.objects.filter(original_image=oi)
        self.assertEqual(bcs.count(), 1)
        bc = bcs.first()
        self.assertEqual(data["business_card_id"], str(bc.id))
        self.assertTrue(bool(bc.card_image), "card_image が保存される")

        oi.refresh_from_db()
        manual = oi.debug_json["manual_results"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["business_card_id"], str(bc.id))
        # polygon は _sort_corners 整列後（TL/TR/BR/BL）。
        poly = manual[0]["polygon"]
        self.assertEqual(set(poly), {"top_left", "top_right", "bottom_right", "bottom_left"})
        self.assertEqual((poly["top_left"]["x"], poly["top_left"]["y"]), (_X0, _Y0))
        self.assertEqual(
            (poly["bottom_right"]["x"], poly["bottom_right"]["y"]),
            (_X0 + _RECT_W, _Y0 + _RECT_H),
        )

    def test_existing_debug_keys_preserved(self):
        oi = self._original()
        before = self._seed_debug_json()
        self._post(oi, rotation=0)
        oi.refresh_from_db()
        self.assertEqual(oi.debug_json["attempts"], before["attempts"])
        self.assertEqual(oi.debug_json["integrated_results"], before["integrated_results"])
        self.assertEqual(oi.debug_json["image_size"], before["image_size"])
        self.assertIn("manual_results", oi.debug_json)

    # ── 回転：最終画像の縦横 ─────────────────────────────────
    def test_rotation_changes_card_image_dimensions(self):
        # warp 結果は W×H=200×120。0/180→(200,120)、90/270→(120,200)。
        cases = {0: (_RECT_W, _RECT_H), 180: (_RECT_W, _RECT_H),
                 90: (_RECT_H, _RECT_W), 270: (_RECT_H, _RECT_W)}
        for rot, expected in cases.items():
            with self.subTest(rotation=rot):
                oi = self._original()
                resp = self._post(oi, rotation=rot)
                self.assertEqual(resp.status_code, 201, resp.content)
                bc = BusinessCard.objects.get(id=resp.json()["business_card_id"])
                with Image.open(bc.card_image.path) as im:
                    self.assertEqual(im.size, expected)

    # ── 異常系（いずれも 500 で落とさない） ─────────────────
    def test_too_few_points_400(self):
        oi = self._original()
        resp = self._post(oi, points=[[0, 0], [10, 0], [10, 10]])  # 3点
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 0)

    def test_too_small_warp_400(self):
        oi = self._original()
        # W=10 < 20, H=5 < 10 → 緩和された手動切り出し下限未満で 400
        small = [[10, 10], [20, 10], [20, 15], [10, 15]]
        resp = self._post(oi, points=small)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("小さ", resp.json()["error"])
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 0)

    def test_small_warp_allowed_with_relaxed_limit(self):
        oi = self._original()
        # W=50, H=30（旧閾値 100x50 未満だが、新閾値 20x10 以上）→ 成功
        relaxed = [[10, 10], [60, 10], [60, 40], [10, 40]]
        resp = self._post(oi, points=relaxed, rotation=0)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 1)

    def test_warp_exception_handled_as_400(self):
        import cv2
        from unittest.mock import patch

        oi = self._original()
        # OpenCV 内部例外（透視変換計算失敗など）が発生しても 500 で落とさず 400
        with patch("cards.views.warp_card_from_points", side_effect=cv2.error("perspective transform failed")):
            resp = self._post(oi)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不正", resp.json()["error"])
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 0)

    def test_invalid_rotation_400(self):
        oi = self._original()
        resp = self._post(oi, rotation=45)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 0)

    def test_out_of_range_points_400(self):
        oi = self._original()
        oob = [[0, 0], [_IMG_W + 50, 0], [_IMG_W + 50, 120], [0, 120]]  # x が画像幅超
        resp = self._post(oi, points=oob)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 0)

    # ── debug_json=None 始点 ───────────────────────────────
    def test_debug_json_none_start(self):
        oi = self._original(debug_json=None)
        self.assertIsNone(oi.debug_json)
        resp = self._post(oi, rotation=0)
        self.assertEqual(resp.status_code, 201, resp.content)
        oi.refresh_from_db()
        self.assertIn("manual_results", oi.debug_json)
        self.assertEqual(len(oi.debug_json["manual_results"]), 1)
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 1)
