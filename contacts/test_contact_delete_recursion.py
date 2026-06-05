"""Contact 削除/部分ロード時の RecursionError リグレッションテスト。

背景（調査で実トレース確定）：CASCADE 削除で Collector が関連 Contact を姓系/住所系
フィールド deferred 付きで from_db 生成すると、Contact.__init__ の
_store_salutation_source_snapshot / _store_address_source_snapshot が getattr で
deferred 値に触れ、DeferredAttribute.__get__ → refresh_from_db → from_db →
__init__ … の無限再帰になっていた。スナップショット取得を self.__dict__ 直読みに
変更し、deferred フィールドのロードを誘発しないことで解消した本修正の回帰防止。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class ContactDeleteRecursionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="recursion_test_user")
        self.original = OriginalImage.objects.create(
            user=self.user,
            image_file="originals/_recursion_test.png",
            status=OriginalImage.STATUS_CARDS_EXTRACTED,
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            business_card=self.bc,
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="再帰 太郎",
            last_name="再帰",
            lang="ja",
            region="愛知県",
            city="豊田市",
        )

    def test_delete_business_card_with_contact_no_recursion(self):
        """Contact が紐づく BusinessCard の .delete() が再帰せず完了する。

        CASCADE 収集で Contact が deferred 付き部分ロードされる削除経路を通す。
        修正前はここで RecursionError になっていた。
        """
        bc_id = self.bc.id
        contact_id = self.contact.id

        # RecursionError なら例外送出でテスト失敗（明示 assert は不要だが意図を示す）
        try:
            self.bc.delete()
        except RecursionError:  # pragma: no cover - 失敗時の可視化用
            self.fail("bc.delete() で RecursionError が発生した（再帰未解消）")

        self.assertFalse(BusinessCard.objects.filter(id=bc_id).exists())
        self.assertFalse(Contact.objects.filter(id=contact_id).exists())

    def test_partial_loaded_contact_init_no_recursion(self):
        """姓系/住所系フィールドを deferred にした部分ロード生成が再帰しない。

        再帰の直接トリガー（__init__ → snapshot → deferred getattr）を最短経路で踏む。
        .only('id') で取得すると last_name / full_name / lang / name_order / region /
        city 等が deferred になり、修正前は取得（インスタンス生成）時点で RecursionError。
        """
        try:
            obj = Contact.objects.only("id").get(pk=self.contact.id)
        except RecursionError:  # pragma: no cover - 失敗時の可視化用
            self.fail("部分ロード Contact の生成で RecursionError が発生した")

        # 生成が成功していること、かつ deferred フィールドが正しく遅延ロードできること
        self.assertEqual(obj.last_name, "再帰")
        self.assertEqual(obj.region, "愛知県")

    def test_full_loaded_save_still_recomputes_salutation(self):
        """全ロード済みの通常 save 経路で姓変更 → salutation_name 再計算の追従が維持される。

        本修正で壊していないことの確認（守るべき挙動）。
        """
        contact = Contact.objects.get(pk=self.contact.id)
        before = contact.salutation_name
        contact.last_name = "別姓"
        contact.full_name = "別姓 太郎"
        contact.save()
        contact.refresh_from_db()
        # is_manual=False かつ姓系変更ありなので salutation_name が再計算され、
        # 旧値と変わる（新しい姓に追従している）。
        self.assertNotEqual(contact.salutation_name, before)
        self.assertIn("別姓", contact.salutation_name)
