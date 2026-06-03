"""OSD デバッグ表示の復活（移植漏れ修繕）のテスト。

第2段OSD移植でバックエンドは入ったが、デバッグ表示3層（テンプレ・タグ・View context）が
移植漏れしていた。本テストは BusinessCard.osd_json["osd"] 起点での復活を検証する：
  - osd_debug_badge タグ（成功 rotate / 失敗 error / osd 無しの安全動作）
  - 一覧テンプレ（DEBUG時のみ OSD バッジ）
  - 詳細テンプレ + View context（osd / osd_rotate_zero、DEBUG ゲート）

debug ゲートは settings.DEBUG=True かつ REMOTE_ADDR ∈ INTERNAL_IPS（テストClientの既定
127.0.0.1）で True になる context_processors.debug 依存。既存 CardDetailDebugUidTests と同方式。
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from cards.models import BusinessCard, OriginalImage
from cards.templatetags.ui_tags import osd_debug_badge

User = get_user_model()


def _osd(**osd_block):
    """BusinessCard.osd_json の正規形（osd ブロックに渡した値を載せる）。"""
    return {
        "schema_version": "1.0.0",
        "osd": osd_block,
        "tesseract_ocr": {},
    }


class OsdDebugBadgeTagTests(TestCase):
    """osd_debug_badge タグ単体（BC.osd_json["osd"] 起点）。"""

    def setUp(self):
        self.user = User.objects.create_superuser(username="osd_tag", password="d")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED
        )

    def _bc(self, osd_json, idx=0):
        return BusinessCard.objects.create(
            original_image=self.original, card_index=idx, osd_json=osd_json
        )

    def test_success_shows_rotate_and_conf(self):
        bc = self._bc(_osd(rotate=90, orientation_deg=270, orientation_conf=1.27, script="Japanese"))
        html = str(osd_debug_badge(bc))
        self.assertIn("OSD rot 90", html)
        self.assertIn("conf 1.27", html)
        self.assertIn("app-status-badge--info", html)

    def test_error_shows_failure_badge_and_reason(self):
        bc = self._bc(_osd(error="Too few characters"), idx=1)
        html = str(osd_debug_badge(bc))
        self.assertIn("OSD失敗", html)
        self.assertIn("Too few characters", html)
        self.assertIn("app-status-badge--error", html)

    def test_missing_osd_json_is_empty(self):
        bc = self._bc(None, idx=2)
        self.assertEqual(str(osd_debug_badge(bc)), "")

    def test_osd_block_absent_is_empty(self):
        bc = self._bc({"schema_version": "1.0.0", "tesseract_ocr": {}}, idx=3)
        self.assertEqual(str(osd_debug_badge(bc)), "")

    def test_none_card_is_empty(self):
        self.assertEqual(str(osd_debug_badge(None)), "")


class OsdDetailViewContextTests(TestCase):
    """CardDetailView が osd / osd_rotate_zero を BC.osd_json から供給する。"""

    def setUp(self):
        self.user = User.objects.create_superuser(username="osd_ctx", password="d")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _detail(self, osd_json):
        bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0, osd_json=osd_json
        )
        return self.client.get(reverse("cards:card_detail", kwargs={"pk": bc.pk}))

    def test_context_osd_populated(self):
        resp = self._detail(_osd(rotate=90, orientation_conf=1.27))
        self.assertEqual(resp.context["osd"]["rotate"], 90)
        self.assertFalse(resp.context["osd_rotate_zero"])

    def test_context_rotate_zero_flag(self):
        resp = self._detail(_osd(rotate=0, orientation_conf=0.4))
        self.assertTrue(resp.context["osd_rotate_zero"])

    def test_context_osd_none_when_missing(self):
        resp = self._detail(None)
        self.assertIsNone(resp.context["osd"])
        self.assertFalse(resp.context["osd_rotate_zero"])


class OsdDetailRenderTests(TestCase):
    """詳細「開発用情報」の OSD ブロック描画（DEBUG ゲート）。"""

    def setUp(self):
        self.user = User.objects.create_superuser(username="osd_det", password="d")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, osd_json):
        bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0, osd_json=osd_json
        )
        return reverse("cards:card_detail", kwargs={"pk": bc.pk})

    @override_settings(DEBUG=True)
    def test_osd_block_shown_in_debug_success(self):
        resp = self.client.get(self._url(_osd(rotate=180, orientation_deg=180, orientation_conf=3.26, script="Japanese")))
        self.assertContains(resp, "OSD 向き判定")
        self.assertContains(resp, "rotate:")
        self.assertContains(resp, "180")

    @override_settings(DEBUG=True)
    def test_osd_block_shows_rotate_zero_notice(self):
        resp = self.client.get(self._url(_osd(rotate=0, orientation_conf=0.3, script="Latin")))
        self.assertContains(resp, "回さない判定 (rotate=0)")

    @override_settings(DEBUG=True)
    def test_osd_block_shows_error(self):
        resp = self.client.get(self._url(_osd(error="Too few characters")))
        self.assertContains(resp, "OSD失敗")
        self.assertContains(resp, "Too few characters")

    @override_settings(DEBUG=False)
    def test_osd_block_hidden_when_debug_off(self):
        resp = self.client.get(self._url(_osd(rotate=90, orientation_conf=1.2)))
        self.assertNotContains(resp, "OSD 向き判定")


class OsdListRenderTests(TestCase):
    """一覧の OSD バッジ描画（DEBUG ゲート、テーブル表示）。"""

    def setUp(self):
        self.user = User.objects.create_superuser(username="osd_list", password="d")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED
        )
        # 一覧は既定で ocr_result=business_card のみ表示。テスト BC を business_card に寄せる。
        BusinessCard.objects.create(
            original_image=self.original,
            card_index=0,
            ocr_status=BusinessCard.OcrStatus.DONE,
            ocr_result=BusinessCard.OcrResult.BUSINESS_CARD,
            osd_json=_osd(rotate=90, orientation_conf=1.27),
        )
        self.client = Client()
        self.client.force_login(self.user)

    @override_settings(DEBUG=True)
    def test_badge_shown_in_debug(self):
        resp = self.client.get(reverse("cards:card_list"))
        self.assertContains(resp, "OSD rot 90")

    @override_settings(DEBUG=False)
    def test_badge_hidden_when_debug_off(self):
        resp = self.client.get(reverse("cards:card_list"))
        self.assertNotContains(resp, "OSD rot 90")
