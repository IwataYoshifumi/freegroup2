"""ホーム画面アラート（単一候補）の紐づけ導線が確認画面リンクであることの検証。

User-Person 紐づけの入口を確認画面 accounts:link_user_person_confirm に統一した
（即実行ルート link_user_person の直叩きを廃止）。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class HomeSingleCandidateLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="iwata_home", password="d", email="iwata@example.com"
        )
        # email 一致・active・未紐づけの Person を 1 件（単一候補）。
        self.person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            email="iwata@example.com",
            full_name="岩田",
        )
        self.person.primary_contact = contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

    def test_single_candidate_links_to_confirm_screen(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["person_link_status"], "single_candidate")

        confirm_url = reverse(
            "accounts:link_user_person_confirm", kwargs={"person_id": self.person.id}
        )
        immediate_url = reverse(
            "accounts:link_user_person",
            kwargs={"user_id": self.user.id, "person_id": self.person.id},
        )
        # 確認画面への GET リンクを出力し、即実行ルートは出力しない。
        # ホームはルート画面でスタックが空のため append_back_url は素の URL を返す（back_stack 無し）。
        self.assertContains(resp, 'href="%s"' % confirm_url)
        self.assertNotContains(resp, immediate_url)
        # アラート部に即実行 POST フォームが残っていない（action=即実行URL の form 無し）。
        self.assertNotContains(resp, 'action="%s"' % immediate_url)

    def test_single_candidate_does_not_show_no_candidate_prompt(self):
        # 上部に単一候補アラートが出るときは、下部の no_candidate 促しは出さない。
        # （"名刺をアップロード" はようこそカード文にも含まれ汎用的なため、促し固有文言と
        #  アップロード href の不在で判定する。）
        self.client.force_login(self.user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.context["person_link_status"], "single_candidate")
        self.assertNotContains(resp, "あなたの名刺がまだ取り込まれていません")
        self.assertNotContains(resp, 'href="%s"' % reverse("cards:card_upload"))


class HomeNoCandidatePromptTests(TestCase):
    """ホームの本人向け名刺取り込み促し（no_candidate）の排他表示検証。"""

    def test_no_candidate_shows_single_upload_prompt(self):
        # 未取り込み（候補0件）：ようこそカード下部に促しカードが1つだけ出る。
        user = User.objects.create_user(
            username="home_nocand", password="d", email="nocand@example.com"
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["person_link_status"], "no_candidate")
        # 促しカードは二重に出さず1つだけ（固有文言・アップロード href とも 1 回ずつ）。
        # （"名刺をアップロード" 部分一致はようこそカード文とも重複するためカウント判定に使わない。）
        self.assertContains(resp, "あなたの名刺がまだ取り込まれていません", count=1)
        self.assertContains(resp, 'href="%s"' % reverse("cards:card_upload"), count=1)
        # 候補ありの表示は出ない。
        self.assertNotContains(resp, "あなたの名刺データが見つかりました")

    def test_multiple_candidates_does_not_show_no_candidate_prompt(self):
        # 複数候補：上部に複数候補誘導が出て、下部の no_candidate 促しは出さない。
        user = User.objects.create_user(
            username="home_multi", password="d", email="multi@example.com"
        )
        for name in ("候補A", "候補B"):
            person = Person.objects.create(status=Person.Status.ACTIVE)
            contact = Contact.objects.create(
                person=person,
                status=Contact.Status.PRIMARY,
                email="multi@example.com",
                full_name=name,
            )
            person.primary_contact = contact
            person.save(update_fields=["primary_contact", "updated_at"])
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(
            resp.context["person_link_status"], "multiple_candidates_need_merge"
        )
        self.assertContains(resp, "複数見つかりました")
        self.assertNotContains(resp, "あなたの名刺がまだ取り込まれていません")
        self.assertNotContains(resp, 'href="%s"' % reverse("cards:card_upload"))


class CrossAppNavDrawerTests(TestCase):
    """≤900px で隠れる横断ナビ（名刺管理・メール配信・ユーザ管理）が、トップバーに加え
    ドロワーにも出ること、権限出し分けが維持されることの検証。カウントは一意な
    nav aria-label で構造的に確認する（トップバー＋ドロワーの2箇所）。"""

    def test_home_topbar_and_drawer_show_cross_app_nav(self):
        user = User.objects.create_user(
            username="nav_user", password="d", email="n@example.com"
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        # 横断ナビはトップバー（PC幅・従来どおり）とドロワー既定の2箇所に出る。
        self.assertContains(resp, 'aria-label="アプリ切り替え"', count=2)
        # 項目（リンク文言）も各2回（topbar＋drawer）。トップバー表示は従来と同一。
        self.assertContains(resp, "名刺管理", count=2)
        self.assertContains(resp, "メール配信", count=2)
        # 権限の無いユーザにはユーザ管理は出ない（従来の出し分け維持）。
        self.assertNotContains(resp, "ユーザ管理")

    def test_home_cross_app_nav_shown_for_superuser(self):
        admin = User.objects.create_superuser(
            username="nav_admin", password="d", email="a@example.com"
        )
        self.client.force_login(admin)
        resp = self.client.get(reverse("home"))
        # 権限ありなら ユーザ管理 も topbar＋drawer の2回出る。
        self.assertContains(resp, "ユーザ管理", count=2)

    def test_app_page_drawer_has_cross_app_nav_and_sidebar(self):
        admin = User.objects.create_superuser(
            username="nav_admin2", password="d", email="a2@example.com"
        )
        self.client.force_login(admin)
        resp = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(resp.status_code, 200)
        # 横断ナビ：トップバー(1)＋ドロワー(1)。
        self.assertContains(resp, 'aria-label="アプリ切り替え"', count=2)
        # アプリ内サイドバー：app_content(1)＋ドロワー(1)。横断ナビとサイドバーが両方ドロワーに出る。
        self.assertContains(resp, 'aria-label="ユーザ管理メニュー"', count=2)

    def test_persons_page_drawer_has_cross_app_nav_and_sidebar(self):
        # persons は専用 app_base を持たず cards/app_base を継承。ドロワーに横断ナビと
        # cards サイドバー（名刺管理メニュー）が DOM として両方出ることを担保（CSS の表示是非は別）。
        user = User.objects.create_superuser(
            username="nav_persons", password="d", email="np@example.com"
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("persons:person_list"))
        self.assertEqual(resp.status_code, 200)
        # 横断ナビ：トップバー(1)＋ドロワー(1)。
        self.assertContains(resp, 'aria-label="アプリ切り替え"', count=2)
        # cards サイドバー：app_content(1)＋ドロワー(1)。
        self.assertContains(resp, 'aria-label="名刺管理メニュー"', count=2)
