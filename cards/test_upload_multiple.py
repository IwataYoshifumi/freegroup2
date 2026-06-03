"""複数ファイル + ドラッグ&ドロップ対応アップロード（UploadView）のテスト。

1 ファイル = 1 OriginalImage を厳守し、各ファイルに validate_image を個別適用、
部分成功（通った分だけ作成・弾いた分は理由つきスキップ）を検証する。
既存の単一ファイル経路（field 'image'・成功時 302）が壊れていないことも確認する。
"""

import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from cards.models import OriginalImage

User = get_user_model()


def _jpeg_bytes(size=(120, 120), color=(200, 100, 50)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _valid_jpeg(name="card.jpg"):
    return SimpleUploadedFile(name, _jpeg_bytes(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="upload_multi_"))
class MultiUploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="up_multi", password="d")
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("cards:card_upload")

    def _count(self):
        return OriginalImage.objects.filter(user=self.user).count()

    # (1) 複数ファイル成功：各ファイルが独立 OriginalImage（pending）になる。
    def test_multiple_files_each_become_one_original(self):
        files = [_valid_jpeg("a.jpg"), _valid_jpeg("b.jpg"), _valid_jpeg("c.png")]
        resp = self.client.post(self.url, {"images": files})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._count(), 3)
        for o in OriginalImage.objects.filter(user=self.user):
            self.assertEqual(o.status, OriginalImage.STATUS_PENDING)
            self.assertTrue(o.image_file)
        self.assertContains(resp, "3件を取り込みました")

    # (2) 部分失敗：サイズ超過・非対応形式は弾き、正常分だけ作成する。
    def test_partial_failure_skips_invalid(self):
        big = SimpleUploadedFile(
            "big.jpg", b"x" * (5 * 1024 * 1024 + 10), content_type="image/jpeg"
        )
        gif = SimpleUploadedFile("anim.gif", _jpeg_bytes(), content_type="image/gif")
        corrupt = SimpleUploadedFile(
            "fake.jpg", b"not really an image", content_type="image/jpeg"
        )
        files = [_valid_jpeg("ok.jpg"), big, gif, corrupt]
        resp = self.client.post(self.url, {"images": files})

        self.assertEqual(resp.status_code, 200)
        # 正常 1 件のみ作成
        self.assertEqual(self._count(), 1)
        # 部分成功の文言（全N件中M件・残りX件）とスキップ理由が表示される
        self.assertContains(resp, "全4件中 1件を取り込みました")
        self.assertContains(resp, "残り3件は取り込めませんでした")
        self.assertContains(resp, "big.jpg")
        self.assertContains(resp, "5MB")
        self.assertContains(resp, "anim.gif")
        self.assertContains(resp, "対応していないファイル形式")
        self.assertContains(resp, "fake.jpg")

    # (2b) 全件失敗でも巻き戻さず（成功 0・スキップ表示）。
    def test_all_invalid_creates_nothing(self):
        gif = SimpleUploadedFile("x.gif", _jpeg_bytes(), content_type="image/gif")
        resp = self.client.post(self.url, {"images": [gif]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._count(), 0)
        self.assertContains(resp, "取り込めたファイルがありませんでした")

    # (3) 後方互換：単一ファイル（field 'image'）成功 → 302 redirect、1 件作成。
    def test_single_file_legacy_redirects(self):
        resp = self.client.post(self.url, {"image": _valid_jpeg("solo.jpg")})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._count(), 1)
        original = OriginalImage.objects.get(user=self.user)
        self.assertEqual(original.status, OriginalImage.STATUS_PENDING)
        self.assertIn(str(original.id), resp.url)

    # (3b) 新フィールドでも 1 件成功・スキップ 0 なら従来どおり 302。
    def test_single_file_via_images_field_redirects(self):
        resp = self.client.post(self.url, {"images": [_valid_jpeg("one.jpg")]})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._count(), 1)

    # ファイル未選択 → 200 でエラー表示、作成 0。
    def test_no_file_shows_error(self):
        resp = self.client.post(self.url, {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._count(), 0)
        self.assertContains(resp, "ファイルが選択されていません")

    # GET はフォーム表示（ドロップゾーン）。
    def test_get_renders_dropzone(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "js-upload-dropzone")
        self.assertContains(resp, 'name="images"')
