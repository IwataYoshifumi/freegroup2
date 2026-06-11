"""v1.7：名刺詳細からの手動コンタクト作成経路のテスト（指示書「テスト範囲」(a)〜(d)）。

ContactCreateView に business_card クエリ/hidden を渡したときの挙動を検証する：
  (a) 未 Contact BC から作成 → Contact が BC に OneToOne 紐づき、BC が done、
      OriginalImage.status 集計が走る
  (b) 既に Contact ありの BC への作成試行が弾かれる（messages.error + 名刺詳細へ redirect）
  (c) business_card なしの従来作成が無影響（後方互換：business_card は None のまま）
  (d) 重複警告 → force_create で BC 紐づけ込みの作成が通る
"""

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


def _empty_sns_management_form(prefix="sns"):
    """ContactSns InlineFormSet の空 management_form（View POST テスト用、Phase F1 §11.6.7）。"""
    return {
        f"{prefix}-TOTAL_FORMS": "0",
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


class ManualCardEntryTests(TestCase):
    """名刺詳細 → ContactCreateView の手動作成経路（v1.7）。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="manual_card_user", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _create_data(self, **overrides):
        """ContactCreateForm 用 POST data（UPDATABLE_FIELDS 全埋め + salutation 必須 + sns 空）。"""
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["salutation_name"] = "手動 様"
        data.update(_empty_sns_management_form())
        data.update(overrides)
        return data

    def _make_unlinked_bc(self, status=OriginalImage.STATUS_CARDS_EXTRACTED):
        """未 Contact・ocr_status=pending の BC を 1 枚作る（元画像は引数の status）。"""
        original = OriginalImage.objects.create(user=self.user, status=status)
        return BusinessCard.objects.create(original_image=original, card_index=0)

    # ------------------------------------------------------------------
    # (a) 未 Contact BC から作成
    # ------------------------------------------------------------------
    def test_create_from_unlinked_bc_links_and_finalizes(self):
        bc = self._make_unlinked_bc()
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.PENDING)

        url = reverse("contacts:contact_create")
        data = self._create_data(full_name="紐づけ太郎", business_card=str(bc.id))
        resp = self.client.post(url, data=data)

        self.assertEqual(resp.status_code, 302)

        contact = Contact.objects.get(full_name="紐づけ太郎")
        # Contact が BC に OneToOne で紐づく
        self.assertEqual(contact.business_card_id, bc.id)
        self.assertEqual(contact.status, Contact.Status.PRIMARY)

        # BC が done / business_card / claimed_at=None で確定
        bc.refresh_from_db()
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.DONE)
        self.assertEqual(bc.ocr_result, BusinessCard.OcrResult.BUSINESS_CARD)
        self.assertIsNone(bc.claimed_at)

        # OriginalImage.status 集計が走った（全 BC 完了 → extracted）
        bc.original_image.refresh_from_db()
        self.assertEqual(bc.original_image.status, OriginalImage.STATUS_EXTRACTED)

    def test_card_detail_shows_create_button_when_unlinked(self):
        """名刺詳細（contact 無し）に business_card 付きの新規作成導線が出る。"""
        bc = self._make_unlinked_bc()
        url = reverse("cards:card_detail", kwargs={"pk": bc.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "新規コンタクト作成")
        self.assertContains(resp, f"business_card={bc.id}")

    def test_create_from_unlinked_bc_get_renders_image_section(self):
        """GET で BC を渡すと作成画面に hidden business_card と画像セクションが出る。"""
        bc = self._make_unlinked_bc()
        url = reverse("contacts:contact_create") + f"?business_card={bc.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'name="business_card" value="{bc.id}"')

    # ------------------------------------------------------------------
    # (b) 既に Contact ありの BC は弾かれる
    # ------------------------------------------------------------------
    def test_create_rejected_when_bc_already_linked(self):
        bc = self._make_unlinked_bc()
        person = Person.objects.create(status=Person.Status.ACTIVE)
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            business_card=bc,
            full_name="既存太郎",
        )
        before = Contact.objects.count()

        url = reverse("contacts:contact_create")
        data = self._create_data(full_name="新規花子", business_card=str(bc.id))
        resp = self.client.post(url, data=data)

        # 名刺詳細へ redirect、Contact は増えていない
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url, reverse("cards:card_detail", kwargs={"pk": bc.pk})
        )
        self.assertEqual(Contact.objects.count(), before)
        self.assertFalse(Contact.objects.filter(full_name="新規花子").exists())

        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("既に" in m for m in msgs), msgs)

    def test_get_rejected_when_bc_already_linked(self):
        bc = self._make_unlinked_bc()
        person = Person.objects.create(status=Person.Status.ACTIVE)
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            business_card=bc,
            full_name="既存次郎",
        )
        url = reverse("contacts:contact_create") + f"?business_card={bc.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url, reverse("cards:card_detail", kwargs={"pk": bc.pk})
        )

    def test_create_rejected_when_bc_not_found(self):
        """存在しない business_card id は名刺一覧へ redirect（id 不正含む）。"""
        url = reverse("contacts:contact_create")
        data = self._create_data(full_name="迷子太郎", business_card="not-a-uuid")
        resp = self.client.post(url, data=data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("cards:card_list"))
        self.assertFalse(Contact.objects.filter(full_name="迷子太郎").exists())

    # ------------------------------------------------------------------
    # (c) business_card なしの従来作成は無影響（後方互換）
    # ------------------------------------------------------------------
    def test_legacy_create_without_business_card(self):
        url = reverse("contacts:contact_create")
        data = self._create_data(full_name="従来太郎")
        resp = self.client.post(url, data=data)

        self.assertEqual(resp.status_code, 302)
        contact = Contact.objects.get(full_name="従来太郎")
        self.assertIsNone(contact.business_card_id)
        self.assertEqual(contact.status, Contact.Status.PRIMARY)

    # ------------------------------------------------------------------
    # (d) 重複警告 → force_create
    # ------------------------------------------------------------------
    def _make_existing_duplicate(self):
        """exact_match（score>=200）に届く既存 primary Contact を作る。

        full_name(40) + mobile_phone(80) + 個人メール(80) = 200。
        """
        person = Person.objects.create(status=Person.Status.ACTIVE)
        return Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            full_name="重複太郎",
            mobile_phone="09011112222",
            email="dup@example.com",
        )

    def test_duplicate_warning_then_force_create_with_bc(self):
        self._make_existing_duplicate()
        bc = self._make_unlinked_bc()
        url = reverse("contacts:contact_create")
        dup_fields = dict(
            full_name="重複太郎",
            mobile_phone="09011112222",
            email="dup@example.com",
        )

        # 1 回目：重複警告画面が出る（BC hidden も持ち回される）
        resp1 = self.client.post(
            url, data=self._create_data(business_card=str(bc.id), **dup_fields)
        )
        self.assertEqual(resp1.status_code, 200)
        self.assertIn(
            "contacts/contact_create_duplicates.html",
            [t.name for t in resp1.templates],
        )
        self.assertContains(resp1, f'name="business_card" value="{bc.id}"')

        # 2 回目：force_create で BC 紐づけ込みの作成が通る
        resp2 = self.client.post(
            url,
            data=self._create_data(
                business_card=str(bc.id), force_create="1", **dup_fields
            ),
        )
        self.assertEqual(resp2.status_code, 302)

        contact = Contact.objects.get(
            full_name="重複太郎", business_card=bc
        )
        self.assertEqual(contact.business_card_id, bc.id)
        bc.refresh_from_db()
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.DONE)
        self.assertEqual(bc.ocr_result, BusinessCard.OcrResult.BUSINESS_CARD)
