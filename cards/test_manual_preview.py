"""warp-only プレビューエンドポイント（ManualPreviewView, 段2-c2-②）のテスト。

- 正常系：4点→200＋{success:true, image:"data:image/png;base64,..."}。
- 異常系：点不足／範囲外／小さすぎ warp → 400・500 落ちなし。
- 副作用なし：BC が増えない・debug_json が変わらない（プレビューは保存しない）。
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

_MEDIA_ROOT = tempfile.mkdtemp(prefix="fg2_manual_preview_test_")

_IMG_W, _IMG_H = 400, 300
_RECT_W, _RECT_H = 200, 120
_X0, _Y0 = 50, 40
_POINTS = [
    [_X0 + _RECT_W, _Y0 + _RECT_H], [_X0, _Y0],
    [_X0 + _RECT_W, _Y0], [_X0, _Y0 + _RECT_H],
]


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class ManualPreviewViewTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_superuser(username="manual_preview", password="d")
        self.client = Client()
        self.client.force_login(self.user)

    def _original(self):
        oi = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED,
        )
        buf = io.BytesIO()
        Image.new("RGB", (_IMG_W, _IMG_H), "white").save(buf, "JPEG")
        oi.image_file.save("orig.jpg", ContentFile(buf.getvalue()), save=True)
        oi.debug_json = {"image_size": {"width": _IMG_W, "height": _IMG_H,
                                        "area": _IMG_W * _IMG_H},
                         "integrated_results": []}
        oi.save(update_fields=["debug_json"])
        return oi

    def _url(self, oi):
        return reverse("originals:manual_crop_preview", kwargs={"pk": oi.id})

    def _post(self, oi, points=None):
        body = {"points": _POINTS if points is None else points}
        return self.client.post(
            self._url(oi), data=json.dumps(body), content_type="application/json"
        )

    def test_preview_success_returns_base64_image(self):
        oi = self._original()
        resp = self._post(oi)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertIs(data["success"], True)
        self.assertTrue(data["image"].startswith("data:image/png;base64,"))
        # base64 部が空でない・デコードできる。
        import base64
        raw = base64.b64decode(data["image"].split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as im:
            # 素 warp（回転なし）→ 200x120。
            self.assertEqual(im.size, (_RECT_W, _RECT_H))

    def test_preview_no_side_effects(self):
        oi = self._original()
        before_dj = dict(oi.debug_json)
        resp = self._post(oi)
        self.assertEqual(resp.status_code, 200)
        oi.refresh_from_db()
        # BC は増えない・debug_json は不変（manual_results も付かない）。
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 0)
        self.assertEqual(oi.debug_json, before_dj)
        self.assertNotIn("manual_results", oi.debug_json)

    def test_too_few_points_400(self):
        oi = self._original()
        resp = self._post(oi, points=[[0, 0], [10, 0], [10, 10]])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_too_small_warp_400(self):
        oi = self._original()
        small = [[10, 10], [60, 10], [60, 40], [10, 40]]  # W=50<100
        resp = self._post(oi, points=small)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("小さ", resp.json()["error"])

    def test_out_of_range_400(self):
        oi = self._original()
        oob = [[0, 0], [_IMG_W + 50, 0], [_IMG_W + 50, 120], [0, 120]]
        resp = self._post(oi, points=oob)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(BusinessCard.objects.filter(original_image=oi).count(), 0)
