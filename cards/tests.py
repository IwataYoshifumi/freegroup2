"""cards アプリの単体テスト。"""

from django.contrib.auth import get_user_model
from django.test import TestCase
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
        view.request = type(
            "Req",
            (),
            {"user": self.user, "GET": {}},
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


class CardDetailViewConfidenceMapTests(TestCase):
    """CardDetailView の confidence_map 拡張テスト（仕様変更後）。

    'low' / 'mid' / 'confirmed' の文字列を含むこと、'medium' は使われないこと。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_detail_test_user", password="dummy"
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

    def test_confidence_map_uses_mid_low_confirmed_strings(self):
        """confidence_map の値が 'low' / 'mid' / 'confirmed' に正規化される。"""
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
        cmap = resp.context["confidence_map"]

        self.assertEqual(cmap.get("company"), "mid")
        self.assertEqual(cmap.get("phone"), "low")
        self.assertEqual(cmap.get("email"), "confirmed")

        # 'medium' という値は使われない（'mid' に短縮されている）
        self.assertNotIn("medium", cmap.values())

    def test_confidence_map_excludes_pseudo_high(self):
        """疑似 high のフィールド（CFC レコードなし）は confidence_map に含まれない。"""
        # CFC レコードなしのまま
        resp = self.client.get(self.url)
        cmap = resp.context["confidence_map"]
        self.assertNotIn("full_name", cmap)
        self.assertNotIn("company", cmap)
        self.assertEqual(cmap, {})
