"""プロフィール画面（ProfileView / profile.html）のレイアウト改修後、
未紐づけ・紐づけ済みの両パターンが正常描画（200）され、最大幅 960px の
絞り枠が適用されることの検証。"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class ProfileLayoutRenderTests(TestCase):
    def _person_with_contact(self, email):
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            email=email,
            full_name="紐づく人物",
            organization="テスト株式会社",
            title="部長",
            mobile_phone="090-0000-0000",
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person

    def test_unlinked_shows_search_button(self):
        user = User.objects.create_user(username="p_unlinked", password="d", email="u@example.com")
        self.client.force_login(user)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "max-width: 960px")
        self.assertContains(resp, "Person を探して紐付ける")
        # 紐づけ済みの 2 カード見出しは出ない。
        self.assertNotContains(resp, "紐づく人物")

    def test_linked_shows_two_cards_and_unlink(self):
        user = User.objects.create_user(username="p_linked", password="d", email="l@example.com")
        person = self._person_with_contact("l@example.com")
        user.person = person
        user.save(update_fields=["person"])
        self.client.force_login(user)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "max-width: 960px")
        # 左右 2 カードの見出し。
        self.assertContains(resp, "<h2>ユーザ</h2>")
        self.assertContains(resp, "人物詳細")
        # 左「あなた」カードに上段プロフィール相当の User 情報が漏れなく入る（ロール・認証ソース含む）。
        self.assertContains(resp, "ロール")
        self.assertContains(resp, "認証ソース")
        # 紐づく人物カード：会社名・部署名・役職は「所属」1行にまとめる。
        self.assertContains(resp, "所属（会社・部署・役職）")
        self.assertContains(resp, "テスト株式会社")
        self.assertContains(resp, "部長")
        self.assertContains(resp, "090-0000-0000")
        # 解除リンク（連携を解除するツールチップ／aria-label）。
        self.assertContains(resp, "連携を解除する")
        # 未紐づけ案内ボタンは出ない。
        self.assertNotContains(resp, "Person を探して紐付ける")
