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
        self.assertContains(resp, 'href="%s' % confirm_url)
        # 単一候補のときは 0 件（アップロード）導線は出さない。
        self.assertNotContains(resp, "名刺をアップロード")

    def test_unlinked_multiple_candidates_shows_person_list_prefill(self):
        """未紐付け×候補複数：人物一覧メールプリフィル（StartLinkFlow相当）へ誘導。
        マージ画面ではない。"""
        from accounts.services import self_link_person_list_url

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
        # 誘導先は人物一覧メールプリフィルURL（共通ヘルパーと一致）。ヘルパー自体の組み立ても検証。
        select_url = self_link_person_list_url(user)
        self.assertIn(reverse("persons:person_list"), select_url)
        self.assertIn("status=active", select_url)
        self.assertIn("searched=1", select_url)
        # レンダリングされた href（& は HTML エスケープされ &amp; になる）を部分一致で検証。
        self.assertContains(resp, reverse("persons:person_list"))
        self.assertContains(resp, "status=active")
        self.assertContains(resp, "自分の Person を選んで紐付ける")
        # マージ画面へは誘導しない。
        self.assertNotContains(
            resp, 'href="%s"' % reverse("duplicates:duplicate_group_list")
        )
        # 複数候補のときは 0 件導線は前面に出さない。
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
        self.assertContains(resp, "パーソン詳細")
        # 左「あなた」カードに上段プロフィール相当の User 情報が漏れなく入る（ロール・認証ソース含む）。
        self.assertContains(resp, "ロール")
        self.assertContains(resp, "認証ソース")
        # 紐づく人物カード：会社名・部署名・役職は「所属」1行にまとめる
        # （ラベルは横並び 140px 枠に収めるため「所属」に短縮済み）。
        self.assertContains(resp, "所属")
        self.assertContains(resp, "テスト株式会社")
        self.assertContains(resp, "部長")
        self.assertContains(resp, "090-0000-0000")
        # 解除導線：右上アイコンから折りたたみ（summary「紐付けの管理」）内の warning ボタンへ移動。
        # 解除リンクの URL（link_user_person_confirm）は不変＝検証意図を保持。
        self.assertContains(resp, "紐付けの管理")
        self.assertContains(resp, "ユーザとパーソンの紐づけを解除する")
        self.assertContains(
            resp,
            reverse("accounts:link_user_person_confirm", kwargs={"person_id": person.id}),
        )
        # 紐づけ済みのときは候補アラートを出さない。
        self.assertNotContains(resp, "あなたの名刺データが見つかりました")
        self.assertNotContains(resp, "名刺をアップロード")


class StartLinkFlowRedirectTests(TestCase):
    """StartLinkFlow が共通ヘルパー（人物一覧メールプリフィル・status=active）へ
    リダイレクトし、複数候補誘導と同一URLに着地することの検証。"""

    def test_redirects_to_person_list_prefill_status_active(self):
        from accounts.services import self_link_person_list_url

        user = User.objects.create_user(
            username="slf_user", password="d", email="slf@example.com"
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("accounts:start_link_flow"))
        self.assertEqual(resp.status_code, 302)
        # 着地URLは共通ヘルパーと完全一致（複数候補誘導と同一）。
        self.assertEqual(resp.url, self_link_person_list_url(user))
        self.assertIn(reverse("persons:person_list"), resp.url)
        self.assertIn("status=active", resp.url)
        self.assertIn("searched=1", resp.url)
