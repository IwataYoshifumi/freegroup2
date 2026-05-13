"""cards アプリの単体テスト。"""

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact, ContactFieldConfidence
from persons.models import Person


User = get_user_model()


class CardListViewConfidenceAnnotateTests(TestCase):
    """CardListView の confidence ドット用 annotate テスト（仕様変更後）。

    DUPLICATE_CHECK_FIELDS 限定 + confirmed_at による「未確認」判定が反映されること
    を検証する。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_list_test_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            business_card=self.bc,
            full_name="T",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

    def _get_card(self):
        from cards.views import CardListView

        view = CardListView()
        # v1.4.2 統合（テーマ 6）以降、CardListView は ocr_result フィルタを持つ。
        # フィルタ未指定 = "business_card" のみ表示の業務仕様（ストック #15）が
        # 適用されると、ocr_result=None / ocr_status=PENDING のテスト BC が
        # queryset から除外されてしまう。annotate ロジックの検証目的を維持するため、
        # _OCR_FILTER_CHOICES の全 7 値を明示的に渡してフィルタを「全マッチ」にする。
        query = QueryDict(mutable=True)
        for value, _label in CardListView._OCR_FILTER_CHOICES:
            query.appendlist("ocr_result", value)
        view.request = type(
            "Req",
            (),
            {"user": self.user, "GET": query},
        )()
        return view.get_queryset().get(pk=self.bc.pk)

    def test_no_cfc_records_all_false(self):
        """CFC レコードなし → has_unconfirmed_low / has_unconfirmed_medium /
        has_confirmed すべて False。"""
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_medium)
        self.assertFalse(card.has_confirmed)

    def test_unconfirmed_low_in_dup_check_fields(self):
        """DUPLICATE_CHECK_FIELDS に含まれる未確認 low CFC → has_unconfirmed_low True。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        card = self._get_card()
        self.assertTrue(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_medium)
        self.assertFalse(card.has_confirmed)

    def test_unconfirmed_medium_in_dup_check_fields(self):
        """未確認 medium CFC → has_unconfirmed_medium True、low は False。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="phone",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertTrue(card.has_unconfirmed_medium)
        self.assertFalse(card.has_confirmed)

    def test_confirmed_cfc(self):
        """confirmed_at セット済み CFC → has_confirmed True、未確認系は False。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_medium)
        self.assertTrue(card.has_confirmed)

    def test_field_outside_dup_check_fields_excluded(self):
        """DUPLICATE_CHECK_FIELDS 外（notes / qualification）の CFC は無視される。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="notes",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="qualification",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_medium)
        self.assertFalse(card.has_confirmed)

    def test_mixed_states(self):
        """未確認 low + confirmed が混在 → 両方 True。優先順はテンプレート側で判定。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        card = self._get_card()
        self.assertTrue(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_medium)
        self.assertTrue(card.has_confirmed)


class CardDetailViewFieldConfidencesTests(TestCase):
    """CardDetailView の field_confidences context テスト（D-3b/D-3d パーツ再利用）。

    CFC レコード（low/mid/confirmed）が context に CFC インスタンス dict として
    反映されること、疑似 high（CFC レコードなし）は dict に含まれないことを検証。
    旧 confidence_map は廃止。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_detail_fc_test_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            business_card=self.bc,
            full_name="T",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

        self.client.force_login(self.user)
        self.url = reverse("cards:card_detail", kwargs={"pk": self.bc.pk})

    def test_field_confidences_includes_low_mid_confirmed_records(self):
        """CFC レコード（mid/low/confirmed）が field_confidences に CFC インスタンスで載る。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        fc = resp.context["field_confidences"]

        self.assertEqual(
            fc["company"].confidence, ContactFieldConfidence.Confidence.MEDIUM
        )
        self.assertIsNone(fc["company"].confirmed_at)
        self.assertEqual(
            fc["phone"].confidence, ContactFieldConfidence.Confidence.LOW
        )
        self.assertIsNone(fc["phone"].confirmed_at)
        self.assertEqual(
            fc["email"].confidence, ContactFieldConfidence.Confidence.LOW
        )
        self.assertIsNotNone(fc["email"].confirmed_at)

    def test_field_confidences_includes_pseudo_high_for_missing_cfc(self):
        """CFC レコードなしフィールドは confidence='high' の疑似インスタンスで含まれる
        （Contact.get_field_confidences の仕様、§10.5.3）。"""
        resp = self.client.get(self.url)
        fc = resp.context["field_confidences"]
        self.assertIn("full_name", fc)
        self.assertEqual(fc["full_name"].confidence, "high")
        self.assertIsNone(fc["full_name"].confirmed_at)
        self.assertIsNone(fc["full_name"].pk)

    def test_confidence_map_removed_from_context(self):
        """旧 confidence_map は context から削除されている（regression 防止）。"""
        resp = self.client.get(self.url)
        self.assertNotIn("confidence_map", resp.context)


class CardDetailViewEditableModeTests(TestCase):
    """CardDetailView の is_editable 判定 + 編集 UI 表示有無テスト。

    Contact.status と Person.status の組み合わせで編集可能モードと表示のみモードを
    切り替え、_contact_field.html の編集 UI（ラジオ等）が条件通りに出ること、
    Contact 紐付きなしのとき編集セクション自体が出ないことを検証。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_detail_editable_test_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            business_card=self.bc,
            full_name="T",
            company="C",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

        # low/mid CFC が無いと _contact_field.html がラジオを描画しないため
        # 編集 UI の表示有無を検証するために 1 件作る
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="company",
            confidence=ContactFieldConfidence.Confidence.MEDIUM,
        )

        self.client.force_login(self.user)
        self.url = reverse("cards:card_detail", kwargs={"pk": self.bc.pk})

    def _promote_other_to_primary(self):
        """self.contact を別ステータスにしたあと、別 Contact を primary に据える。"""
        other = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P",
        )
        self.person.primary_contact = other
        self.person.save(update_fields=["primary_contact", "updated_at"])
        return other

    def test_primary_contact_is_editable(self):
        """primary Contact + active Person → is_editable=True、編集 UI が描画される。"""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_editable"])
        content = resp.content.decode()
        self.assertIn("js-contact-field-row", content)
        self.assertIn("js-contact-field-action", content)
        self.assertNotIn("表示のみモード", content)

    def test_active_contact_is_editable(self):
        """active Contact + active Person → is_editable=True。"""
        self.contact.status = Contact.Status.ACTIVE
        self.contact.save(update_fields=["status", "updated_at"])
        self._promote_other_to_primary()

        resp = self.client.get(self.url)
        self.assertTrue(resp.context["is_editable"])

    def test_inactive_contact_is_not_editable(self):
        """inactive Contact → is_editable=False、表示のみモード、編集ラジオ非表示。"""
        self.contact.status = Contact.Status.INACTIVE
        self.contact.save(update_fields=["status", "updated_at"])
        self._promote_other_to_primary()

        resp = self.client.get(self.url)
        self.assertFalse(resp.context["is_editable"])
        content = resp.content.decode()
        self.assertIn("表示のみモード", content)
        self.assertNotIn("js-contact-field-action", content)

    def test_archived_person_is_not_editable(self):
        """archived Person 配下の Contact → is_editable=False。"""
        self.person.status = Person.Status.ARCHIVED
        self.person.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self.url)
        self.assertFalse(resp.context["is_editable"])

    def test_merged_person_is_not_editable(self):
        """merged Person 配下の Contact → is_editable=False。"""
        self.person.status = Person.Status.MERGED
        self.person.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self.url)
        self.assertFalse(resp.context["is_editable"])

    def test_no_contact_hides_editing_section(self):
        """Contact 紐付きなし → 編集セクション自体が出ず、デバッグセクションは維持。"""
        bc2 = BusinessCard.objects.create(
            original_image=self.original, card_index=1
        )
        url2 = reverse("cards:card_detail", kwargs={"pk": bc2.pk})

        resp = self.client.get(url2)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["contact"])
        self.assertFalse(resp.context["is_editable"])
        self.assertEqual(resp.context["field_confidences"], {})
        content = resp.content.decode()
        self.assertNotIn("js-contact-field-row", content)
        self.assertIn("Contact が紐付いていません", content)


class CardDetailDebugUidTests(TestCase):
    """DEBUG=True 時の Card UID コピペ表示（D-4d-1 第 6 弾 §2-1）。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_dbg_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.card = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self):
        return reverse("cards:card_detail", kwargs={"pk": self.card.pk})

    @override_settings(DEBUG=True)
    def test_card_uid_shown_in_debug_mode(self):
        resp = self.client.get(self._url())
        self.assertContains(resp, 'class="app-debug-uid"')
        self.assertContains(resp, "Card UID:")
        self.assertContains(resp, str(self.card.id))

    @override_settings(DEBUG=False)
    def test_card_uid_hidden_when_debug_false(self):
        resp = self.client.get(self._url())
        self.assertNotContains(resp, "Card UID:")

