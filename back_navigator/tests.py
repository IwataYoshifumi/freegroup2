"""back_tags（back_link / back_all_link）のラベル固定化の検証（HIG 戻る統一）。

back_link は戻り先画面名（back_title）をラベルに使わず固定「戻る」、back_all_link は
固定「最初に戻る」を出す。表示判定（back_exist / back_all_exist）・href は従来どおり。
"""

import base64
import json

from django.template import Context, Template
from django.test import RequestFactory, TestCase

from back_navigator.back_navigator import BackNavigator


def _encode_back_stack(stack):
    raw = base64.urlsafe_b64encode(
        json.dumps(stack, ensure_ascii=False).encode()
    ).decode()
    return raw.rstrip("=")


class BackTagsLabelTests(TestCase):
    def _back(self, stack):
        rf = RequestFactory()
        request = rf.get("/here/", {"back_stack": _encode_back_stack(stack)})
        return BackNavigator(request)

    def _render(self, tag_body, back):
        tmpl = Template("{% load back_tags %}" + tag_body)
        return tmpl.render(Context({"back": back}))

    def _entry(self, url, title, view_name):
        return {"url": url, "title": title, "view_name": view_name, "view_kwargs": {}}

    def test_back_link_label_fixed_modoru(self):
        """戻り先画面名が「コンタクト詳細」でもラベルは固定「戻る」。"""
        back = self._back([
            self._entry("/c/x/", "コンタクト詳細", "contacts:contact_detail"),
        ])
        out = self._render("{% back_link back %}", back)
        self.assertIn(">戻る</a>", out)
        self.assertNotIn("コンタクト詳細", out)

    def test_back_link_href_preserved(self):
        """ラベルは固定でも href は戻り先 URL のまま。"""
        back = self._back([
            self._entry("/c/x/", "コンタクト詳細", "contacts:contact_detail"),
        ])
        out = self._render("{% back_link back %}", back)
        self.assertIn('href="/c/x/', out)

    def test_back_link_empty_when_no_history(self):
        """戻り先が無ければ空文字（表示判定は従来どおり）。"""
        rf = RequestFactory()
        back = BackNavigator(rf.get("/here/"))
        out = self._render("{% back_link back %}", back)
        self.assertEqual(out, "")

    def test_back_all_link_label_fixed(self):
        """2 階層以上で back_all_link は固定「最初に戻る」。起点画面名は出さない。"""
        back = self._back([
            self._entry("/persons/", "人物一覧", "persons:person_list"),
            self._entry("/c/x/", "コンタクト詳細", "contacts:contact_detail"),
        ])
        out = self._render("{% back_all_link back %}", back)
        self.assertIn(">最初に戻る</a>", out)
        self.assertNotIn("人物一覧", out)
