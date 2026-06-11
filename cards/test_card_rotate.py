"""名刺画像の 90° 回転焼き直し（CardRotateView, v1.7）のテスト。

- 右90°（cw）で card_image が時計回りに回り、同一パスへ上書きされる。
- 左90°（ccw）で反時計回りに回る（右と逆）。
- 回転後は orientation が normal にリセットされる。
- ファイル保存失敗時に DB（orientation）変更がロールバックされる。
- Contact 紐付け済み BC ではサーバ側ガードで弾かれる（画像は変わらない）。
"""

import io
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact
from persons.models import Person

User = get_user_model()

_MEDIA_ROOT = tempfile.mkdtemp(prefix="fg2_rotate_test_")


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class CardRotateViewTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_superuser(username="card_rotate", password="d")
        self.client = Client()
        self.client.force_login(self.user)

    def _original(self, status=OriginalImage.STATUS_CARDS_EXTRACTED):
        return OriginalImage.objects.create(user=self.user, status=status)

    def _bc_with_image(self, **kw):
        """4x2・左上だけ赤の PNG を card_image に持つ BC を作る（回転方向の判定用）。"""
        bc = BusinessCard.objects.create(
            original_image=self._original(), card_index=kw.pop("idx", 0), **kw
        )
        img = Image.new("RGB", (4, 2), "black")
        img.putpixel((0, 0), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        bc.card_image.save("marker.png", ContentFile(buf.getvalue()), save=True)
        return bc

    def _rotate_url(self, bc):
        return reverse("cards:card_rotate", kwargs={"pk": bc.pk})

    def _red_pixels(self, path):
        with Image.open(path) as im:
            im = im.convert("RGB")
            size = im.size
            reds = [
                (x, y)
                for y in range(size[1])
                for x in range(size[0])
                if im.getpixel((x, y)) == (255, 0, 0)
            ]
        return size, reds

    # --- 右90°（時計回り） ---
    def test_rotate_cw_rotates_image_clockwise(self):
        bc = self._bc_with_image()
        resp = self.client.post(self._rotate_url(bc), {"direction": "cw"})
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        size, reds = self._red_pixels(bc.card_image.path)
        # 4x2 → 2x4、左上(0,0)の赤は時計回りで右上(1,0)へ
        self.assertEqual(size, (2, 4))
        self.assertEqual(reds, [(1, 0)])

    # --- 左90°（反時計回り）：右と逆 ---
    def test_rotate_ccw_rotates_image_counterclockwise(self):
        bc = self._bc_with_image()
        resp = self.client.post(self._rotate_url(bc), {"direction": "ccw"})
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        size, reds = self._red_pixels(bc.card_image.path)
        # 4x2 → 2x4、左上(0,0)の赤は反時計回りで左下(0,3)へ
        self.assertEqual(size, (2, 4))
        self.assertEqual(reds, [(0, 3)])

    # --- 回転後 orientation は normal にリセット ---
    def test_rotate_resets_orientation_to_normal(self):
        bc = self._bc_with_image(orientation=BusinessCard.ORIENTATION_180)
        self.client.post(self._rotate_url(bc), {"direction": "cw"})
        bc.refresh_from_db()
        self.assertEqual(bc.orientation, BusinessCard.ORIENTATION_NORMAL)

    # --- ファイル保存失敗時は DB がロールバック ---
    def test_file_save_failure_rolls_back_db(self):
        bc = self._bc_with_image(orientation=BusinessCard.ORIENTATION_180)
        with mock.patch(
            "cards.views._overwrite_image_file", side_effect=OSError("disk full")
        ):
            resp = self.client.post(self._rotate_url(bc), {"direction": "cw"})
        # View は例外を握って編集画面へ戻す
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        # orientation の normal リセットがロールバックされ 180 のまま
        self.assertEqual(bc.orientation, BusinessCard.ORIENTATION_180)

    # --- Contact 紐付け済みはサーバ側ガードで弾かれる ---
    def test_rotate_rejected_when_bc_has_contact(self):
        bc = self._bc_with_image(orientation=BusinessCard.ORIENTATION_180)
        person = Person.objects.create(status=Person.Status.ACTIVE)
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            business_card=bc,
            full_name="既存太郎",
        )
        size_before, reds_before = self._red_pixels(bc.card_image.path)
        resp = self.client.post(self._rotate_url(bc), {"direction": "cw"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("cards:card_detail", kwargs={"pk": bc.pk}))
        bc.refresh_from_db()
        # 弾かれたので orientation も画像も変わらない
        self.assertEqual(bc.orientation, BusinessCard.ORIENTATION_180)
        size_after, reds_after = self._red_pixels(bc.card_image.path)
        self.assertEqual(size_after, size_before)
        self.assertEqual(reds_after, reds_before)

    # --- 不正な direction は弾く（画像は変わらない） ---
    def test_invalid_direction_is_rejected(self):
        bc = self._bc_with_image()
        size_before, reds_before = self._red_pixels(bc.card_image.path)
        resp = self.client.post(self._rotate_url(bc), {"direction": "xyz"})
        self.assertEqual(resp.status_code, 302)
        size_after, reds_after = self._red_pixels(bc.card_image.path)
        self.assertEqual(size_after, size_before)
        self.assertEqual(reds_after, reds_before)

    # --- キャッシュバスティング URL に ?v= が付く ---
    def test_cache_busted_url_has_version_query(self):
        bc = self._bc_with_image()
        url = bc.get_card_image_url_busted()
        self.assertIn("?v=", url)
        self.assertTrue(url.startswith(bc.card_image.url))
