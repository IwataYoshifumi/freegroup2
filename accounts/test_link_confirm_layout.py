"""紐付け確認画面（link_user_person_confirm）のレイアウト変更後、各表示パターンが
正常描画（200）され、最大幅 960px の中央寄せ枠が適用されることの検証。"""

import base64
import json

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from back_navigator.back_navigator import BackNavigator

from contacts.models import Contact
from persons.models import Person

User = get_user_model()


def _encode_back_stack(stack):
    """BackNavigator と同じ方式で back_stack を base64 エンコードする（テスト用）。"""
    raw = base64.urlsafe_b64encode(json.dumps(stack, ensure_ascii=False).encode()).decode()
    return raw.rstrip("=")


class BackLinkLabelTagTests(TestCase):
    """back_link タグの label 引数（後方互換）の検証。"""

    def _back(self, title):
        rf = RequestFactory()
        stack = [{"url": "/home/", "title": title, "view_name": "home", "view_kwargs": {}}]
        request = rf.get("/here/", {"back_stack": _encode_back_stack(stack)})
        return BackNavigator(request)

    def _render(self, tag_body, back):
        tmpl = Template("{% load back_tags %}" + tag_body)
        return tmpl.render(Context({"back": back}))

    def test_label_omitted_uses_back_title(self):
        # 引数省略時は従来どおり back.back_title（スタックの title）を使う。
        out = self._render("{% back_link back %}", self._back("ホーム"))
        self.assertEqual(
            out, '<a class="app-btn app-btn--secondary" href="/home/">ホーム</a>'
        )

    def test_label_omitted_falls_back_to_modoru(self):
        # title が空なら従来どおり "戻る"。
        out = self._render("{% back_link back %}", self._back(""))
        self.assertEqual(
            out, '<a class="app-btn app-btn--secondary" href="/home/">戻る</a>'
        )

    def test_label_overrides_title(self):
        # label を渡すと title より優先（href は変わらない）。
        out = self._render('{% back_link back label="戻る" %}', self._back("人物詳細"))
        self.assertEqual(
            out, '<a class="app-btn app-btn--secondary" href="/home/">戻る</a>'
        )


class LinkConfirmLayoutRenderTests(TestCase):
    def _person_with_email(self, email):
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, email=email, full_name="対象人物"
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person

    def _get(self, user, person):
        self.client.force_login(user)
        return self.client.get(
            reverse("accounts:link_user_person_confirm", kwargs={"person_id": person.id})
        )

    def test_link_pattern_email_match(self):
        # superuser はユーザ管理サイドバーの項目が perms で表示される。
        user = User.objects.create_superuser(username="u_match", password="d", email="m@example.com")
        person = self._person_with_email("m@example.com")
        resp = self._get(user, person)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "max-width: 960px")
        self.assertContains(resp, "紐付ける")
        # 確認文はタイトル直下に info（青）アラートで出る。
        self.assertContains(resp, "app-alert app-alert--info")
        # 主従を「あなたのユーザー→人物」に修正した文言。
        self.assertContains(resp, "あなたのユーザーはまだ人物に紐付けられていません。この人物と紐付けますか")
        # ユーザ管理サイドバーが組み込まれている。
        self.assertContains(resp, "ユーザ管理メニュー")
        self.assertContains(resp, "ユーザ一覧")
        # 対象人物カードに携帯番号欄が追加されている。
        self.assertContains(resp, "携帯番号")

    def test_mismatch_pattern_danger(self):
        user = User.objects.create_user(username="u_mis", password="d", email="a@example.com")
        person = self._person_with_email("b@example.com")
        resp = self._get(user, person)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "max-width: 960px")
        # 不一致はタイトル直下に danger（赤）アラートで出る。
        self.assertContains(resp, "app-alert app-alert--danger")
        self.assertContains(resp, "メールアドレスが一致しないため")
        self.assertNotContains(resp, "紐付ける")

    def test_self_linked_unlink_pattern(self):
        user = User.objects.create_user(username="u_self", password="d", email="s@example.com")
        person = self._person_with_email("s@example.com")
        user.person = person
        user.save(update_fields=["person"])
        resp = self._get(user, person)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "max-width: 960px")
        self.assertContains(resp, "紐付けを解除")
        # 解除（可逆）はタイトル直下に warning（黄）アラートで出る。
        self.assertContains(resp, "app-alert app-alert--warning")
        self.assertContains(resp, "紐付けを解除しますか")

    def test_back_link_appears_with_back_stack(self):
        # 遷移元（ホーム等）が back_stack を付けて遷移してきた場合、戻るボタンが描画される。
        user = User.objects.create_superuser(
            username="u_back", password="d", email="bk@example.com"
        )
        person = self._person_with_email("bk@example.com")
        self.client.force_login(user)
        stack = [{"url": "/", "title": "ホーム", "view_name": "home", "view_kwargs": {}}]
        url = reverse("accounts:link_user_person_confirm", kwargs={"person_id": person.id})
        resp = self.client.get(url, {"back_stack": _encode_back_stack(stack)})
        self.assertEqual(resp.status_code, 200)
        # back_link タグの出力（戻る先＝ホーム、ラベルはスタックの title）。
        self.assertContains(resp, '<a class="app-btn app-btn--secondary" href="/">')

    def test_back_is_unified_to_label_modoru(self):
        # この画面の戻る系は「戻る」1系統。2階層スタックでも「最初に戻る」は出さず、
        # back_link のラベルは固定で「戻る」になる（href は戻り先のまま）。
        user = User.objects.create_superuser(
            username="u_unify", password="d", email="uni@example.com"
        )
        person = self._person_with_email("uni@example.com")
        self.client.force_login(user)
        stack = [
            {"url": "/persons/", "title": "人物一覧", "view_name": "persons", "view_kwargs": {}},
            {"url": "/c/x/", "title": "コンタクト詳細", "view_name": "contacts:contact_detail", "view_kwargs": {}},
        ]
        url = reverse("accounts:link_user_person_confirm", kwargs={"person_id": person.id})
        resp = self.client.get(url, {"back_stack": _encode_back_stack(stack)})
        self.assertEqual(resp.status_code, 200)
        # 「最初に戻る」（back_all_link）は出さない。
        self.assertNotContains(resp, "最初に戻る")
        # ラベルは「戻る」に固定。直前 title「コンタクト詳細」がボタンに出ない。
        self.assertNotContains(resp, ">コンタクト詳細</a>")
        self.assertContains(resp, ">戻る</a>")
        # href は従来どおり直前ページ（コンタクト詳細）のまま（back_stack クエリが付く）。
        self.assertContains(resp, 'href="/c/x/?')

    def test_other_user_linked_warning_pattern(self):
        owner = User.objects.create_user(username="u_owner", password="d", email="o@example.com")
        person = self._person_with_email("o@example.com")
        owner.person = person
        owner.save(update_fields=["person"])
        viewer = User.objects.create_user(username="u_viewer", password="d", email="v@example.com")
        resp = self._get(viewer, person)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "max-width: 960px")
        # 他User紐づけ済み（操作不可）はタイトル直下に warning（黄）アラートで出る。
        self.assertContains(resp, "app-alert app-alert--warning")
        self.assertContains(resp, "別のユーザー")
        # 操作不可ケースは下部に紐付け／解除ボタンを出さない（既存挙動の維持）。
        self.assertNotContains(resp, "紐付ける")
        self.assertNotContains(resp, "紐付けを解除")
