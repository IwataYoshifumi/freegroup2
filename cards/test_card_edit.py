"""名刺（BusinessCard）編集画面 CardEditView のテスト（v1.7）。

- Contact 未生成の BC で編集画面に入れる／orientation・error_message が保存される。
- 「名刺でない」設定後、ocr_result=not_business_card・ocr_status=done になり、
  同一元画像の全 BC が terminal になったとき OriginalImage.status が extracted へ遷移する。
- Contact を持つ BC では編集画面が弾かれる（サーバ側ガード）。
- 名刺詳細で Contact を持つ BC には編集ボタンが出ない／持たない BC には出る。
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class CardEditViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="card_edit", password="d")
        self.client = Client()
        self.client.force_login(self.user)

    def _original(self, status=OriginalImage.STATUS_CARDS_EXTRACTED):
        return OriginalImage.objects.create(user=self.user, status=status)

    def _bc(self, original, idx=0, **kw):
        return BusinessCard.objects.create(
            original_image=original, card_index=idx, **kw
        )

    def _edit_url(self, bc):
        return reverse("cards:card_edit", kwargs={"pk": bc.pk})

    # --- 編集画面に入れる / 通常保存 ---
    def test_get_edit_page_for_unlinked_bc(self):
        bc = self._bc(self._original())
        resp = self.client.get(self._edit_url(bc))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "名刺編集")
        self.assertContains(resp, 'name="orientation"')
        self.assertContains(resp, 'name="error_message"')

    def test_normal_save_updates_orientation_and_error_message(self):
        bc = self._bc(self._original())
        resp = self.client.post(
            self._edit_url(bc),
            {"orientation": BusinessCard.ORIENTATION_180, "error_message": "手動メモ"},
        )
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        self.assertEqual(bc.orientation, BusinessCard.ORIENTATION_180)
        self.assertEqual(bc.error_message, "手動メモ")
        # 通常保存では ocr_status / ocr_result は変わらない
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.PENDING)
        self.assertIsNone(bc.ocr_result)

    # --- 「名刺でない」設定 → 終端化 + status 再集計 ---
    def test_mark_not_business_card_finalizes_and_aggregates(self):
        original = self._original()
        bc = self._bc(original, claimed_at=None)
        # OCR待ちの単独 BC を「名刺でない」に設定
        resp = self.client.post(
            self._edit_url(bc),
            {
                "orientation": BusinessCard.ORIENTATION_NORMAL,
                "error_message": "名刺ではないと判断",
                "mark_not_business_card": "1",
            },
        )
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        self.assertEqual(bc.ocr_result, BusinessCard.OcrResult.NOT_BUSINESS_CARD)
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.DONE)
        self.assertIsNone(bc.claimed_at)
        self.assertEqual(bc.error_message, "名刺ではないと判断")
        # Contact は作られない
        self.assertFalse(Contact.objects.filter(business_card=bc).exists())
        # 単独 BC が terminal になったので OriginalImage は extracted へ遷移
        original.refresh_from_db()
        self.assertEqual(original.status, OriginalImage.STATUS_EXTRACTED)

    def test_mark_not_business_card_keeps_pending_when_other_bc_unfinished(self):
        """同一元画像に未終端 BC が残っていれば status は cards_extracted のまま。"""
        original = self._original()
        bc0 = self._bc(original, idx=0)
        self._bc(original, idx=1)  # こちらは pending のまま
        self.client.post(
            self._edit_url(bc0),
            {
                "orientation": BusinessCard.ORIENTATION_NORMAL,
                "error_message": "",
                "mark_not_business_card": "1",
            },
        )
        original.refresh_from_db()
        self.assertEqual(original.status, OriginalImage.STATUS_CARDS_EXTRACTED)

    # --- P1: 再終端化ガード（ocr_result is None の BC だけ終端化可） ---
    def test_finalize_blocked_when_already_terminalized(self):
        """終端化済み（ocr_result 非 None）の BC は POST しても再終端化されない（後勝ち防止）。"""
        for existing in (
            BusinessCard.OcrResult.NOT_BUSINESS_CARD,
            BusinessCard.OcrResult.OTHERS,
            BusinessCard.OcrResult.BUSINESS_CARD,
            BusinessCard.OcrResult.INSUFFICIENT_INFO,
            BusinessCard.OcrResult.OCR_FAILED,
        ):
            for post_key in ("mark_not_business_card", "mark_others"):
                bc = self._bc(
                    self._original(),
                    ocr_result=existing,
                    ocr_status=BusinessCard.OcrStatus.DONE,
                    error_message="既存メモ",
                )
                resp = self.client.post(
                    self._edit_url(bc),
                    {
                        "orientation": BusinessCard.ORIENTATION_NORMAL,
                        "error_message": "上書き試行",
                        post_key: "1",
                    },
                )
                # 弾かれて名刺詳細へ redirect
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(
                    resp.url, reverse("cards:card_detail", kwargs={"pk": bc.pk})
                )
                bc.refresh_from_db()
                # 分類もメモも上書きされない
                self.assertEqual(
                    bc.ocr_result, existing, msg=f"{existing}/{post_key}"
                )
                self.assertEqual(bc.error_message, "既存メモ")

    def test_finalize_allowed_when_ocr_result_null(self):
        """ocr_result=None（OCR待ち）の BC は従来どおり終端化できる。"""
        bc = self._bc(self._original())
        self.assertIsNone(bc.ocr_result)
        resp = self.client.post(
            self._edit_url(bc),
            {
                "orientation": BusinessCard.ORIENTATION_NORMAL,
                "error_message": "",
                "mark_others": "1",
            },
        )
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        self.assertEqual(bc.ocr_result, BusinessCard.OcrResult.OTHERS)

    def test_edit_get_hides_finalize_buttons_when_terminalized(self):
        bc = self._bc(
            self._original(),
            ocr_result=BusinessCard.OcrResult.OTHERS,
            ocr_status=BusinessCard.OcrStatus.DONE,
        )
        resp = self.client.get(self._edit_url(bc))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "名刺ではない に設定")
        self.assertNotContains(resp, "その他 に設定")
        # 通常保存フォーム（向き/メモ）は従来どおり表示
        self.assertContains(resp, 'name="orientation"')
        self.assertContains(resp, 'name="error_message"')

    def test_edit_get_shows_finalize_buttons_when_pending(self):
        bc = self._bc(self._original())  # ocr_result=None
        resp = self.client.get(self._edit_url(bc))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "名刺ではない に設定")
        self.assertContains(resp, "その他 に設定")

    # --- 「その他」設定 → ocr_result=others で終端化 + 行列から除外 ---
    def test_mark_others_finalizes_and_excluded_from_ocr_queue(self):
        original = self._original()
        bc = self._bc(original, claimed_at=None)
        resp = self.client.post(
            self._edit_url(bc),
            {
                "orientation": BusinessCard.ORIENTATION_NORMAL,
                "error_message": "複数名刺が同居しているため",
                "mark_others": "1",
            },
        )
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        self.assertEqual(bc.ocr_result, BusinessCard.OcrResult.OTHERS)
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.DONE)
        self.assertIsNone(bc.claimed_at)
        # メモが同時保存される
        self.assertEqual(bc.error_message, "複数名刺が同居しているため")
        # Contact は作られない
        self.assertFalse(Contact.objects.filter(business_card=bc).exists())
        # OCR cron の選別条件（process_ocr: ocr_status=pending）から外れている
        pending_ids = BusinessCard.objects.filter(
            ocr_status=BusinessCard.OcrStatus.PENDING
        ).values_list("id", flat=True)
        self.assertNotIn(bc.id, list(pending_ids))
        # 単独 BC が terminal になったので OriginalImage は extracted へ遷移
        original.refresh_from_db()
        self.assertEqual(original.status, OriginalImage.STATUS_EXTRACTED)

    def test_mark_others_keeps_pending_when_other_bc_unfinished(self):
        """同一元画像に未終端 BC が残っていれば status は cards_extracted のまま。"""
        original = self._original()
        bc0 = self._bc(original, idx=0)
        self._bc(original, idx=1)  # こちらは pending のまま
        self.client.post(
            self._edit_url(bc0),
            {
                "orientation": BusinessCard.ORIENTATION_NORMAL,
                "error_message": "",
                "mark_others": "1",
            },
        )
        original.refresh_from_db()
        self.assertEqual(original.status, OriginalImage.STATUS_CARDS_EXTRACTED)

    def test_mark_others_rejected_when_bc_has_contact(self):
        original = self._original()
        bc = self._bc(original)
        person = Person.objects.create(status=Person.Status.ACTIVE)
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            business_card=bc,
            full_name="既存次郎",
        )
        resp = self.client.post(
            self._edit_url(bc),
            {
                "orientation": BusinessCard.ORIENTATION_NORMAL,
                "error_message": "x",
                "mark_others": "1",
            },
        )
        self.assertEqual(resp.status_code, 302)
        bc.refresh_from_db()
        # 弾かれたので others 化されない（pending のまま・Contact 維持）
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.PENDING)
        self.assertIsNone(bc.ocr_result)

    # --- Contact 済み BC は弾かれる（サーバ側ガード） ---
    def test_edit_rejected_when_bc_has_contact(self):
        original = self._original()
        bc = self._bc(original)
        person = Person.objects.create(status=Person.Status.ACTIVE)
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            business_card=bc,
            full_name="既存太郎",
        )
        # GET も POST も名刺詳細へ redirect
        get_resp = self.client.get(self._edit_url(bc))
        self.assertEqual(get_resp.status_code, 302)
        self.assertEqual(
            get_resp.url, reverse("cards:card_detail", kwargs={"pk": bc.pk})
        )
        post_resp = self.client.post(
            self._edit_url(bc),
            {"orientation": BusinessCard.ORIENTATION_180, "error_message": "x"},
        )
        self.assertEqual(post_resp.status_code, 302)
        # 弾かれたので編集は反映されない
        bc.refresh_from_db()
        self.assertEqual(bc.orientation, BusinessCard.ORIENTATION_NORMAL)

    # --- 名刺詳細の編集ボタン表示/非表示 ---
    def test_detail_shows_edit_button_when_unlinked(self):
        bc = self._bc(self._original())
        resp = self.client.get(reverse("cards:card_detail", kwargs={"pk": bc.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "名刺を編集")
        self.assertContains(resp, reverse("cards:card_edit", kwargs={"pk": bc.pk}))

    def test_detail_hides_edit_button_when_linked(self):
        original = self._original()
        bc = self._bc(original)
        person = Person.objects.create(status=Person.Status.ACTIVE)
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            business_card=bc,
            full_name="既存花子",
        )
        resp = self.client.get(reverse("cards:card_detail", kwargs={"pk": bc.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "名刺を編集")

    # --- v1.7: 新規コンタクト作成ボタンの表示制御（ocr_result is null のときだけ） ---
    def test_detail_shows_create_contact_button_when_ocr_result_null(self):
        # _bc は ocr_result=None（モデル default）で作られる＝OCR待ち
        bc = self._bc(self._original())
        self.assertIsNone(bc.ocr_result)
        resp = self.client.get(reverse("cards:card_detail", kwargs={"pk": bc.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "新規コンタクト作成")

    def test_detail_hides_create_contact_button_when_ocr_result_set(self):
        # ocr_result に値が入っていれば（5値いずれでも）ボタンは出さない
        for result in (
            BusinessCard.OcrResult.BUSINESS_CARD,
            BusinessCard.OcrResult.NOT_BUSINESS_CARD,
            BusinessCard.OcrResult.INSUFFICIENT_INFO,
            BusinessCard.OcrResult.OCR_FAILED,
            BusinessCard.OcrResult.OTHERS,
        ):
            bc = self._bc(
                self._original(),
                ocr_result=result,
                ocr_status=BusinessCard.OcrStatus.DONE,
            )
            resp = self.client.get(
                reverse("cards:card_detail", kwargs={"pk": bc.pk})
            )
            self.assertEqual(resp.status_code, 200)
            self.assertNotContains(
                resp, "新規コンタクト作成", msg_prefix=f"ocr_result={result}"
            )

    def test_detail_hides_create_contact_button_when_contact_linked(self):
        original = self._original()
        bc = self._bc(original)
        person = Person.objects.create(status=Person.Status.ACTIVE)
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            business_card=bc,
            full_name="既存三郎",
        )
        resp = self.client.get(reverse("cards:card_detail", kwargs={"pk": bc.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "新規コンタクト作成")

    # --- v1.7: ocr_status バッジ（別表 C.13 ラベル）併記 ---
    def test_detail_shows_ocr_status_badge_label(self):
        bc_pending = self._bc(self._original())  # ocr_status=pending
        resp = self.client.get(
            reverse("cards:card_detail", kwargs={"pk": bc_pending.pk})
        )
        self.assertContains(resp, "OCR待ち")

        bc_done = self._bc(
            self._original(),
            ocr_status=BusinessCard.OcrStatus.DONE,
            ocr_result=BusinessCard.OcrResult.NOT_BUSINESS_CARD,
        )
        resp_done = self.client.get(
            reverse("cards:card_detail", kwargs={"pk": bc_done.pk})
        )
        # ocr_status=done のバッジラベル「完了」が併記される（ocr_result バッジ「名刺ではない」も残る）
        self.assertContains(resp_done, "完了")
        self.assertContains(resp_done, "名刺ではない")
