import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from cards.models import BusinessCard, OriginalImage

User = get_user_model()


class CardDeleteFeatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password123")
        for codename in ("delete_businesscard", "view_businesscard", "view_originalimage"):
            self.user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.user)

        self.original = OriginalImage.objects.create(
            user=self.user,
            status=OriginalImage.STATUS_EXTRACTED,
            detected_count=3,
            debug_json={
                "image_size": {"width": 1000, "height": 600, "area": 600000},
                "integrated_results": [
                    {
                        "card_index": 0,
                        "polygon": {
                            "top_left": {"x": 10, "y": 10},
                            "top_right": {"x": 200, "y": 10},
                            "bottom_right": {"x": 200, "y": 120},
                            "bottom_left": {"x": 10, "y": 120},
                        },
                    },
                    {
                        "card_index": 1,
                        "polygon": {
                            "top_left": {"x": 300, "y": 10},
                            "top_right": {"x": 500, "y": 10},
                            "bottom_right": {"x": 500, "y": 120},
                            "bottom_left": {"x": 300, "y": 120},
                        },
                    },
                    {
                        "card_index": 2,
                        "polygon": {
                            "top_left": {"x": 600, "y": 10},
                            "top_right": {"x": 800, "y": 10},
                            "bottom_right": {"x": 800, "y": 120},
                            "bottom_left": {"x": 600, "y": 120},
                        },
                    },
                ],
            },
        )

        # 0: 正規名刺（削除不可）
        self.bc_normal = BusinessCard.objects.create(
            original_image=self.original,
            card_index=0,
            ocr_result=BusinessCard.OcrResult.BUSINESS_CARD,
        )
        # 1: 名刺ではない（削除可）
        self.bc_not_card = BusinessCard.objects.create(
            original_image=self.original,
            card_index=1,
            ocr_result=BusinessCard.OcrResult.NOT_BUSINESS_CARD,
        )
        # 2: その他（削除可）
        self.bc_others = BusinessCard.objects.create(
            original_image=self.original,
            card_index=2,
            ocr_result=BusinessCard.OcrResult.OTHERS,
        )

    # ── 1. can_delete プロパティ ─────────────────────────────
    def test_can_delete_property(self):
        self.assertFalse(self.bc_normal.can_delete)
        self.assertTrue(self.bc_not_card.can_delete)
        self.assertTrue(self.bc_others.can_delete)

        # 未完了（ocr_result is None）は保護対象のため削除不可
        bc_pending = BusinessCard.objects.create(
            original_image=self.original, card_index=3, ocr_result=None
        )
        self.assertFalse(bc_pending.can_delete)

        # 不足情報・OCR失敗も削除可
        bc_insufficient = BusinessCard.objects.create(
            original_image=self.original,
            card_index=4,
            ocr_result=BusinessCard.OcrResult.INSUFFICIENT_INFO,
        )
        self.assertTrue(bc_insufficient.can_delete)

        bc_failed = BusinessCard.objects.create(
            original_image=self.original,
            card_index=5,
            ocr_result=BusinessCard.OcrResult.OCR_FAILED,
        )
        self.assertTrue(bc_failed.can_delete)

    # ── 2. CardDeleteView（単体削除）のガードと detected_count ──
    def test_single_delete_guard_blocks_business_card(self):
        url = reverse("cards:card_delete", kwargs={"pk": self.bc_normal.id})
        resp = self.client.post(url)
        # 詳細へリダイレクトされ、削除されない
        self.assertRedirects(resp, reverse("cards:card_detail", kwargs={"pk": self.bc_normal.id}))
        self.assertTrue(BusinessCard.objects.filter(id=self.bc_normal.id).exists())
        self.original.refresh_from_db()
        self.assertEqual(self.original.detected_count, 3)

    def test_single_delete_guard_blocks_pending_card(self):
        bc_pending = BusinessCard.objects.create(
            original_image=self.original, card_index=3, ocr_result=None
        )
        url = reverse("cards:card_delete", kwargs={"pk": bc_pending.id})
        resp = self.client.post(url)
        # 詳細へリダイレクトされ、削除されない
        self.assertRedirects(resp, reverse("cards:card_detail", kwargs={"pk": bc_pending.id}))
        self.assertTrue(BusinessCard.objects.filter(id=bc_pending.id).exists())

    def test_single_delete_success_and_updates_detected_count(self):
        url = reverse("cards:card_delete", kwargs={"pk": self.bc_not_card.id})
        resp = self.client.post(url)
        # 元画像詳細へリダイレクトされ、削除される
        self.assertRedirects(
            resp, reverse("originals:original_detail", kwargs={"pk": self.original.id})
        )
        self.assertFalse(BusinessCard.objects.filter(id=self.bc_not_card.id).exists())
        # detected_count が 2 件に再集計される
        self.original.refresh_from_db()
        self.assertEqual(self.original.detected_count, 2)

    # ── 3. CardBulkDeleteView（一括削除） ─────────────────────
    def test_bulk_delete_requires_permission(self):
        unprivileged = User.objects.create_user(username="no_perm", password="password123")
        self.client.force_login(unprivileged)
        url = reverse("cards:card_bulk_delete")
        resp = self.client.post(url, {"card_ids": [str(self.bc_not_card.id)]})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(BusinessCard.objects.filter(id=self.bc_not_card.id).exists())

    def test_bulk_delete_only_deletes_can_delete_and_owned(self):
        bc_pending = BusinessCard.objects.create(
            original_image=self.original, card_index=3, ocr_result=None
        )
        other_user = User.objects.create_user(username="other", password="password123")
        other_orig = OriginalImage.objects.create(
            user=other_user, status=OriginalImage.STATUS_EXTRACTED, detected_count=1
        )
        other_bc = BusinessCard.objects.create(
            original_image=other_orig,
            card_index=0,
            ocr_result=BusinessCard.OcrResult.NOT_BUSINESS_CARD,
        )

        url = reverse("cards:card_bulk_delete")
        # 1. 正常な名刺（ガードでスキップ）
        # 2. 名刺ではない（削除対象）
        # 3. その他（削除対象）
        # 4. 未完了カード（ガードでスキップ）
        # 5. 他人の名刺（ユーザースコープでスキップ）
        payload = {
            "card_ids": [
                str(self.bc_normal.id),
                str(self.bc_not_card.id),
                str(self.bc_others.id),
                str(bc_pending.id),
                str(other_bc.id),
            ]
        }
        resp = self.client.post(url, payload)
        self.assertRedirects(resp, reverse("cards:card_list"))

        # bc_normal（正規名刺）、bc_pending（OCR未実行）は残る
        self.assertTrue(BusinessCard.objects.filter(id=self.bc_normal.id).exists())
        self.assertTrue(BusinessCard.objects.filter(id=bc_pending.id).exists())
        # bc_not_card, bc_others は削除される
        self.assertFalse(BusinessCard.objects.filter(id=self.bc_not_card.id).exists())
        self.assertFalse(BusinessCard.objects.filter(id=self.bc_others.id).exists())
        # 他人の名刺は削除されない
        self.assertTrue(BusinessCard.objects.filter(id=other_bc.id).exists())

        # detected_count が 2（bc_normal, bc_pending）に追従する
        self.original.refresh_from_db()
        self.assertEqual(self.original.detected_count, 2)

    def test_bulk_delete_empty_payload(self):
        url = reverse("cards:card_bulk_delete")
        resp = self.client.post(url, {"card_ids": []})
        self.assertRedirects(resp, reverse("cards:card_list"))
        self.assertEqual(self.original.businesscard_set.count(), 3)

    # ── 4. 元画像詳細画面オーバーレイの削除追従 ──────────────
    def test_original_detail_overlay_skips_deleted_cards(self):
        # 削除前：overlay_polygons に 0, 1, 2 の3つが含まれる
        view_url = reverse("originals:original_detail", kwargs={"pk": self.original.id})
        view_perm = Permission.objects.get(codename="view_originalimage")
        self.user.user_permissions.add(view_perm)

        resp = self.client.get(view_url)
        self.assertEqual(resp.status_code, 200)
        polys = resp.context["overlay_polygons"]
        self.assertEqual([p["card_index"] for p in polys], [0, 1, 2])

        # card_index=1 を削除
        self.bc_not_card.delete()

        # 削除後：overlay_polygons に 0, 2 のみが含まれる（1 は除外）
        resp = self.client.get(view_url)
        polys = resp.context["overlay_polygons"]
        self.assertEqual([p["card_index"] for p in polys], [0, 2])

    # ── 5. UI 表示（詳細画面・一覧画面） ──────────────────────
    def test_card_detail_delete_button_visibility(self):
        view_bc_perm = Permission.objects.get(codename="view_businesscard")
        self.user.user_permissions.add(view_bc_perm)

        # 正規名刺：削除ボタンなし
        resp = self.client.get(reverse("cards:card_detail", kwargs={"pk": self.bc_normal.id}))
        self.assertNotContains(resp, 'class="js-card-delete-form"')

        # ゴミ名刺：削除ボタンあり
        resp = self.client.get(reverse("cards:card_detail", kwargs={"pk": self.bc_not_card.id}))
        self.assertContains(resp, 'class="js-card-delete-form"')
        self.assertContains(resp, "🗑 削除")

    def test_card_list_checkbox_visibility(self):
        view_bc_perm = Permission.objects.get(codename="view_businesscard")
        self.user.user_permissions.add(view_bc_perm)

        resp = self.client.get(reverse("cards:card_list"))
        self.assertEqual(resp.status_code, 200)
        # 一括削除フォームとトグルボタン・削除ボタンが存在する
        self.assertContains(resp, 'class="js-card-bulk-delete-form"')
        self.assertContains(resp, 'js-bulk-delete-toggle')
        self.assertContains(resp, "無効画像を削除する")

        # 正規名刺の行にはチェックボックスではなくハイフン（正規名刺は削除不可）
        # bc_not_card, bc_others の value チェックボックスが存在する
        self.assertContains(resp, f'value="{self.bc_not_card.id}"')
        self.assertContains(resp, f'value="{self.bc_others.id}"')
        self.assertNotContains(resp, f'value="{self.bc_normal.id}"')
