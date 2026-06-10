"""accounts 確認画面の業務語化（HIG v1.4 原則4）の単体テスト。

link_user_person_confirm / retire_user の画面に内部語（英字モデル名 Person /
Contact / User・リレーション名 primary_contact）が出ず、業務語が出ることを検証する。
画面出力文字列のみの変更で、ビュー・サービス・モデルは不変。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from persons.models import Person

User = get_user_model()


class LinkUserPersonConfirmTermsTests(TestCase):
    """link_user_person_confirm 画面の文言検証。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="acc_link_user", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.user)
        # primary_contact 未設定・linked_user なしの素の Person で
        # 「主コンタクト未設定の人物です」「この人物はまだ…」両分岐を踏む。
        self.person = Person.objects.create()

    def _url(self):
        return reverse(
            "accounts:link_user_person_confirm",
            kwargs={"person_id": self.person.id},
        )

    def test_no_internal_terms(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        # 業務語が出る
        self.assertIn("ユーザーと人物の紐付け確認", body)
        self.assertIn("対象人物", body)
        self.assertIn("主コンタクト未設定の人物です", body)
        self.assertIn("この人物は", body)
        # 内部語（英字モデル名・リレーション名）は出ない
        self.assertNotIn("User-Person 紐付け確認", body)
        self.assertNotIn("対象 Person", body)
        self.assertNotIn("あなた（User）", body)
        self.assertNotIn("primary_contact 未設定", body)
        self.assertNotIn("この Person", body)


class RetireUserTermsTests(TestCase):
    """retire_user 画面の文言検証。"""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="acc_retire_admin", password="dummy"
        )
        perm = Permission.objects.get(
            content_type__app_label="accounts", codename="retire_user"
        )
        self.admin.user_permissions.add(perm)
        self.target = User.objects.create_user(
            username="acc_retire_target", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def _url(self):
        return reverse("accounts:retire_user", kwargs={"user_id": self.target.id})

    def test_no_internal_terms(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        # 業務語が出る（「取り消せません」は維持）
        self.assertIn("人物・連絡先は後継者に引き継がれます", body)
        self.assertIn("この操作は取り消せません。", body)
        # 内部語（英字モデル名）は出ない
        self.assertNotIn("Person・Contact", body)
