"""後段の判定＋永続化関数 _judge_and_persist_ocr_result の生成部テスト（v1.7）。

process_cardimage_with_ocr から括り出した _judge_and_persist_ocr_result を直接呼び、
is_business_card=true ／ has_minimum_info を満たす採用 raw_json から
BC 確定・Person/Contact/ContactFieldConfidence/ContactSns が生成されることを検証する。

card の組み立ては既存テスト fixture（contacts.test_phone_validation の _build_card / _E）を
流用する（schema 検証を通る妥当な 8 ブロック構造）。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from cards.models import BusinessCard, OriginalImage
from cards.tasks.ocr_pipeline import _judge_and_persist_ocr_result
from contacts.models import Contact, ContactFieldConfidence, ContactSns
from contacts.test_phone_validation import _build_card, _E
from persons.models import Person

User = get_user_model()


class JudgeAndPersistOcrResultTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="ocr_persist", password="d")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original,
            card_index=0,
            ocr_status=BusinessCard.OcrStatus.PENDING,
        )

    def test_business_card_generates_person_contact_cfc_sns(self):
        # email を low（→ CFC 生成対象）、SNS 1 件入りの妥当な card を用意。
        card = _build_card(
            country="JP",
            contact_override={
                "email": _E("taro@sample.co.jp", "low"),
                "sns": _E([{"type": "twitter", "id": "taro_handle"}]),
            },
        )
        adopted_raw_json = {"cards": [card], "api_response": {}}
        error_msgs = []

        _judge_and_persist_ocr_result(
            self.bc,
            adopted_raw_json,
            "normal",          # orientation_detected
            adopted_raw_json,  # raw_json_1
            None,              # raw_json_2
            error_msgs,
        )

        # --- BC が BUSINESS_CARD / done で確定 ---
        self.bc.refresh_from_db()
        self.assertEqual(self.bc.ocr_result, BusinessCard.OcrResult.BUSINESS_CARD)
        self.assertEqual(self.bc.ocr_status, BusinessCard.OcrStatus.DONE)

        # --- Person 1 件・Contact 1 件（PRIMARY・BC 紐付き） ---
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Contact.objects.count(), 1)
        contact = Contact.objects.get()
        self.assertEqual(contact.status, Contact.Status.PRIMARY)
        self.assertEqual(contact.business_card_id, self.bc.id)

        # --- person.primary_contact が設定される ---
        person = Person.objects.get()
        self.assertEqual(person.primary_contact_id, contact.id)

        # --- confidence_map の low（email）が CFC として作られる ---
        self.assertTrue(
            ContactFieldConfidence.objects.filter(
                contact=contact, field_name="email", confidence="low"
            ).exists()
        )
        # salutation_name は pipeline の cfc_map から除外され二重作成されない
        # （Contact.save() の補完経路で 1 件のみ作られる）。
        self.assertEqual(
            ContactFieldConfidence.objects.filter(
                contact=contact, field_name="salutation_name"
            ).count(),
            1,
        )

        # --- SNS が ContactSns として作られる ---
        self.assertEqual(
            ContactSns.objects.filter(
                contact=contact, sns_type=ContactSns.SnsType.TWITTER, sns_id="taro_handle"
            ).count(),
            1,
        )
