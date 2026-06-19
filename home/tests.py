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
        self.assertContains(resp, 'href="%s"' % confirm_url)
        self.assertNotContains(resp, immediate_url)
        # アラート部に即実行 POST フォームが残っていない（action=即実行URL の form 無し）。
        self.assertNotContains(resp, 'action="%s"' % immediate_url)
