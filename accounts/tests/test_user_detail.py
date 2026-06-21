"""ユーザ詳細画面（UserDetailView / user_detail.html）の2カラム化の検証。

左＝ユーザー情報／右＝紐付き人物（primary_contact 経由）。タイトルはカード外。
退職処理ボタン（現職時）・人物の詳細へリンク（紐付きあり時）・未紐付けメッセージを担保する。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class UserDetailTwoColumnTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="ud_admin", password="x", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def _person_with_contact(self):
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            email="linked@example.com",
            full_name="紐付き太郎",
            organization="テスト株式会社",
            department="営業部",
            title="課長",
            mobile_phone="090-1111-2222",
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person

    def _make_user(self, username, person=None, is_active=True):
        u = User.objects.create_user(
            username=username, password="x", email=f"{username}@example.com",
            is_active=is_active,
        )
        if person:
            u.person = person
            u.save(update_fields=["person"])
        return u

    def test_title_outside_card_and_left_user_info(self):
        target = self._make_user("ud_plain")
        resp = self.client.get(reverse("accounts:user_detail", kwargs={"user_id": target.id}))
        self.assertEqual(resp.status_code, 200)
        # タイトルはカード外：<h1> が最初の app-card より前に出る。
        html = resp.content.decode("utf-8")
        self.assertIn("<h1>ユーザ詳細</h1>", html)
        self.assertLess(html.index("<h1>ユーザ詳細</h1>"), html.index('class="app-card"'))
        # 左カラム（ユーザー情報）の項目。
        self.assertContains(resp, "ユーザー情報")
        self.assertContains(resp, "ud_plain")
        self.assertContains(resp, "認証ソース")
        self.assertContains(resp, "現職")

    def test_linked_person_right_column(self):
        person = self._person_with_contact()
        target = self._make_user("ud_linked", person=person)
        resp = self.client.get(reverse("accounts:user_detail", kwargs={"user_id": target.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "紐付き人物")
        # primary_contact 由来の項目。
        self.assertContains(resp, "紐付き太郎")
        self.assertContains(resp, "linked@example.com")
        self.assertContains(resp, "テスト株式会社")
        self.assertContains(resp, "090-1111-2222")
        # 「人物の詳細へ」リンク（persons:person_detail）。
        detail_url = reverse("persons:person_detail", kwargs={"pk": person.id})
        self.assertContains(resp, 'href="%s"' % detail_url)
        self.assertContains(resp, "人物の詳細へ")

    def test_unlinked_person_message(self):
        target = self._make_user("ud_unlinked")
        resp = self.client.get(reverse("accounts:user_detail", kwargs={"user_id": target.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "（人物は紐付けられていません）")
        # 未紐付けなので人物詳細リンクは出ない。
        self.assertNotContains(resp, "人物の詳細へ")

    def test_retire_button_shown_for_active(self):
        target = self._make_user("ud_active")
        resp = self.client.get(reverse("accounts:user_detail", kwargs={"user_id": target.id}))
        retire_url = reverse("accounts:retire_user", kwargs={"user_id": target.id})
        self.assertContains(resp, 'href="%s"' % retire_url)
        self.assertContains(resp, "退職処理")

    def test_role_change_button_is_success_color_in_header(self):
        # ロール変更は編集系＝緑（app-btn--success）で、見出し行に app-btn--sm で並ぶ。
        target = self._make_user("ud_role")
        resp = self.client.get(reverse("accounts:user_detail", kwargs={"user_id": target.id}))
        self.assertContains(resp, "ロール変更")
        self.assertContains(resp, 'app-btn--success app-btn--sm">ロール変更')

    def test_retired_user_shows_no_retire_button(self):
        target = self._make_user("ud_retired", is_active=False)
        resp = self.client.get(reverse("accounts:user_detail", kwargs={"user_id": target.id}))
        self.assertContains(resp, "このユーザは退職済みです")
        self.assertNotContains(resp, "退職処理")
