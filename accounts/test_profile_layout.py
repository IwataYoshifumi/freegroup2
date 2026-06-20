"""プロフィール画面（ProfileView / profile.html）のレイアウト＋紐付け候補導線の検証。

未紐づけ時は紐付け候補の状態（単一→confirm／複数→マージ整理／0件→名刺アップロード）で
共通 partial（accounts/_link_candidate_alert.html）を出し分ける。判定は
accounts.services.self_link_alert_context（ホームと同一基準）。紐づけ済み時は
左右 2 カード表示で候補アラートは出さない。最大幅 960px の絞り枠も併せて検証する。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class ProfileLayoutRenderTests(TestCase):
    def _person_with_contact(self, email, full_name="紐づく人物"):
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            email=email,
            full_name=full_name,
            organization="テスト株式会社",
            title="部長",
            mobile_phone="090-0000-0000",
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person

    def test_unlinked_no_candidate_shows_upload(self):
        """未紐付け×候補0件：名刺アップロード誘導（cards:card_upload）を出す。"""
        user = User.objects.create_user(username="p_unlinked", password="d", email="u@example.com")
        self.client.force_login(user)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "max-width: 960px")
        self.assertEqual(resp.context["person_link_status"], "no_candidate")
        # 0 件状態は名刺アップロード導線。
        self.assertContains(resp, "名刺をアップロード")
        self.assertContains(resp, 'href="%s"' % reverse("cards:card_upload"))
        # 候補・紐づけ済み表示は出ない。
        self.assertNotContains(resp, "あなたの名刺データが見つかりました")
        self.assertNotContains(resp, "紐づく人物")

    def test_unlinked_single_candidate_shows_confirm(self):
        """未紐付け×候補単一：その候補へ confirm 導線（accounts:link_user_person_confirm）。"""
        user = User.objects.create_user(username="p_single", password="d", email="s@example.com")
        person = self._person_with_contact("s@example.com")
        self.client.force_login(user)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["person_link_status"], "single_candidate")
        confirm_url = reverse(
            "accounts:link_user_person_confirm", kwargs={"person_id": person.id}
        )
        self.assertContains(resp, "あなたの名刺データが見つかりました")
        self.assertContains(resp, 'href="%s"' % confirm_url)
        # 単一候補のときは 0 件（アップロード）導線は出さない。
        self.assertNotContains(resp, "名刺をアップロード")

    def test_unlinked_multiple_candidates_shows_merge(self):
        """未紐付け×候補複数：マージ整理へ導線（duplicates:duplicate_group_list）。"""
        user = User.objects.create_user(username="p_multi", password="d", email="m@example.com")
        self._person_with_contact("m@example.com", full_name="候補A")
        self._person_with_contact("m@example.com", full_name="候補B")
        self.client.force_login(user)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context["person_link_status"], "multiple_candidates_need_merge"
        )
        self.assertContains(resp, "複数見つかりました")
        self.assertContains(
            resp, 'href="%s"' % reverse("duplicates:duplicate_group_list")
        )
        # 複数候補のときは単一の confirm 導線・0 件導線は前面に出さない。
        self.assertNotContains(resp, "名刺をアップロード")

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
        # 紐づけ済みのときは候補アラートを出さない。
        self.assertNotContains(resp, "あなたの名刺データが見つかりました")
        self.assertNotContains(resp, "名刺をアップロード")
