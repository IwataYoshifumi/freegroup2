"""tags アプリのテスト。

BulkTaggingView の従来モード（?tag なし）と固定モード（URL パスに tag）を検証する。
固定モードはタグ詳細「このタグを人に付与」からの遷移で、タグ選択を省いて対象 Person を
選ぶだけで付与し、付与後はタグ詳細へ戻す（タスクD：タグ固定遷移）。
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from contacts.models import Contact
from persons.models import Person
from tags.models import Tag, TagAssignment, TagCategory


User = get_user_model()


class BulkTaggingViewTests(TestCase):
    """BulkTaggingView の従来モード / 固定モードの単体テスト。"""

    def setUp(self):
        # permission_required("tags.assign_tag") を満たすため superuser を使う。
        self.user = User.objects.create_superuser(
            username="bulk_tag_test_user",
            email="bulk@example.com",
            password="dummy",
        )
        self.client = Client()
        self.client.force_login(self.user)

        self.category = TagCategory.objects.create(name="業種")
        self.tag = Tag.objects.create(
            name="製造業", category=self.category, created_by=self.user
        )
        self.other_tag = Tag.objects.create(
            name="IT", category=self.category, created_by=self.user
        )

        self.person_a = self._make_active_person("Alice")
        self.person_b = self._make_active_person("Bob")

        self.bulk_url = reverse("tags:bulk_tagging")
        self.fixed_url = reverse(
            "tags:bulk_tagging_for_tag", kwargs={"pk": self.tag.id}
        )

    def _make_active_person(self, full_name):
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, full_name=full_name
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person

    # ---- 従来モード（?tag なし＝URL パスに pk なし）：回帰確認 ----

    def test_traditional_mode_get_shows_tag_selection(self):
        """従来モードの GET（検索後）は固定モードでなく、Step3 のタグ選択 UI を出す。"""
        # Step3 は has_results（=検索済み）でのみ描画されるため searched=1 で確認。
        resp = self.client.get(self.bulk_url, {"searched": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_fixed_mode"])
        self.assertIsNone(resp.context["fixed_tag"])
        self.assertTrue(resp.context["has_results"])
        self.assertContains(resp, "Step 3：付与するタグを選択")

    def test_traditional_mode_post_assigns_multiple_tags(self):
        """従来モードの POST は POST の tag_ids（複数）どおりに付与し、リダイレクトしない。"""
        resp = self.client.post(
            self.bulk_url,
            {
                "person_ids": [str(self.person_a.id)],
                "tag_ids": [str(self.tag.id), str(self.other_tag.id)],
            },
        )
        self.assertEqual(resp.status_code, 200)  # リダイレクトせず結果表示
        self.assertIn("bulk_result", resp.context)
        self.assertTrue(
            TagAssignment.objects.filter(
                tag=self.tag, person=self.person_a
            ).exists()
        )
        self.assertTrue(
            TagAssignment.objects.filter(
                tag=self.other_tag, person=self.person_a
            ).exists()
        )

    # ---- 固定モード（URL パスに tag）----

    def test_fixed_mode_get_hides_tag_selection(self):
        """固定モードの GET（検索後）はタグ選択 UI を出さず、固定タグを文脈に持つ。"""
        # 検索後でも Step3 が出ないことを確認するため searched=1。
        resp = self.client.get(self.fixed_url, {"searched": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_fixed_mode"])
        self.assertEqual(resp.context["fixed_tag"], self.tag)
        self.assertTrue(resp.context["has_results"])
        self.assertNotContains(resp, "Step 3：付与するタグを選択")
        self.assertContains(resp, "付与するタグ")
        # 固定タグを送る hidden が入っている
        self.assertContains(resp, 'name="tag_ids" value="{}"'.format(self.tag.id))

    def test_fixed_mode_post_forces_tag_and_redirects(self):
        """固定モードの POST は tag_ids 改ざんを無視して固定タグに強制し、タグ詳細へ redirect。"""
        resp = self.client.post(
            self.fixed_url,
            {
                "person_ids": [str(self.person_a.id), str(self.person_b.id)],
                # 改ざん：別タグを送り込んでも固定タグに強制されること
                "tag_ids": [str(self.other_tag.id)],
            },
        )
        self.assertRedirects(
            resp, reverse("tags:tag_detail", kwargs={"pk": self.tag.id})
        )
        # 固定タグが 2 人に付与される
        self.assertEqual(
            TagAssignment.objects.filter(tag=self.tag).count(), 2
        )
        # 改ざんで送られた別タグは付与されない
        self.assertFalse(
            TagAssignment.objects.filter(tag=self.other_tag).exists()
        )

    def test_fixed_mode_post_shows_success_message(self):
        """固定モードの付与後、success メッセージが出る。"""
        resp = self.client.post(
            self.fixed_url,
            {"person_ids": [str(self.person_a.id)], "tag_ids": [str(self.tag.id)]},
            follow=True,
        )
        msgs = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("製造業" in m and "1 人" in m for m in msgs))

    def test_traditional_mode_back_link_points_to_tag_list(self):
        """従来モードの戻るは reverse 直リンクでタグ一覧へ（スタック非依存・ラベル「戻る」）。"""
        resp = self.client.get(self.bulk_url)
        self.assertContains(
            resp,
            '<a class="app-btn app-btn--secondary" href="{}">戻る</a>'.format(
                reverse("tags:tag_list")
            ),
            html=True,
        )

    def test_fixed_mode_back_link_points_to_tag_detail(self):
        """固定モードの戻るは reverse 直リンクで当該タグ詳細へ（検索 submit でも消えない設計）。"""
        detail_url = reverse("tags:tag_detail", kwargs={"pk": self.tag.id})
        # 検索後でも戻るが残ることを確認するため searched=1。
        resp = self.client.get(self.fixed_url, {"searched": "1"})
        self.assertContains(
            resp,
            '<a class="app-btn app-btn--secondary" href="{}">戻る</a>'.format(detail_url),
            html=True,
        )

    def test_back_link_appears_in_header_and_bottom(self):
        """戻るはヘッダ右上＋下部補助の2箇所に出る（縦長画面の補助・HIG 3.2）。検索結果が無くても出る。"""
        detail_url = reverse("tags:tag_detail", kwargs={"pk": self.tag.id})
        link = '<a class="app-btn app-btn--secondary" href="{}">戻る</a>'.format(detail_url)
        resp = self.client.get(self.fixed_url)  # has_results=False（未検索）
        self.assertEqual(resp.content.decode("utf-8").count(link), 2)

    def test_fixed_mode_nonexistent_tag_returns_404(self):
        """存在しない tag の固定モード URL は 404（500 を返さない）。"""
        url = reverse("tags:bulk_tagging_for_tag", kwargs={"pk": uuid.uuid4()})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    # ---- tag_detail の導線ボタン ----

    def test_tag_detail_shows_assign_button_for_active_tag(self):
        """アクティブなタグ詳細には「このタグを人に付与」導線（固定モード URL）が出る。"""
        resp = self.client.get(reverse("tags:tag_detail", kwargs={"pk": self.tag.id}))
        self.assertContains(resp, self.fixed_url)
        self.assertContains(resp, "このタグを人に付与")

    def test_tag_detail_hides_assign_button_for_archived_tag(self):
        """アーカイブ済みタグには付与導線を出さない（active 運用）。"""
        self.tag.is_archived = True
        self.tag.save(update_fields=["is_archived"])
        resp = self.client.get(reverse("tags:tag_detail", kwargs={"pk": self.tag.id}))
        self.assertNotContains(resp, "このタグを人に付与")

    def test_tag_detail_pushes_self_onto_back_stack(self):
        """タグ詳細はページネーション（?page=）を持つ起点画面なので push_current で
        自身をスタックに積む。これにより人物詳細から「戻る」でタグ詳細へ戻れる（HIG 3.2）。"""
        url = reverse("tags:tag_detail", kwargs={"pk": self.tag.id})
        resp = self.client.get(url, {"page": "1"})
        back = resp.context["back"]
        urls = " ".join(entry.get("url", "") for entry in back.back_stack)
        self.assertIn(url, urls)


class TagListViewSortTests(TestCase):
    """TagListView の多段サーバー側ソート（Task G 横展開）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tag_list_sort_test_user", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.category = TagCategory.objects.create(name="業種", sort_order=1)
        # 同一カテゴリ内・名前順が分かるよう Aaa/Bbb/Ccc を作成順とは別に作る。
        self.tag_b = Tag.objects.create(
            name="Bbb", category=self.category, created_by=self.user
        )
        self.tag_a = Tag.objects.create(
            name="Aaa", category=self.category, created_by=self.user
        )
        self.tag_c = Tag.objects.create(
            name="Ccc", category=self.category, created_by=self.user
        )
        self.url = reverse("tags:tag_list")

    def test_sort_by_name_asc_and_desc(self):
        """?sort=name 昇順 / ?sort=-name 降順でタグ名順に並ぶ。"""
        resp = self.client.get(self.url, {"sort": "name"})
        ids = [t.id for t in resp.context["tags"]]
        self.assertLess(ids.index(self.tag_a.id), ids.index(self.tag_b.id))
        self.assertLess(ids.index(self.tag_b.id), ids.index(self.tag_c.id))
        self.assertTrue(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_rows"][0], {"key": "name", "dir": "asc"})

        resp = self.client.get(self.url, {"sort": "-name"})
        ids = [t.id for t in resp.context["tags"]]
        self.assertLess(ids.index(self.tag_c.id), ids.index(self.tag_b.id))
        self.assertLess(ids.index(self.tag_b.id), ids.index(self.tag_a.id))
        self.assertEqual(resp.context["sort_rows"][0], {"key": "name", "dir": "desc"})

    def test_invalid_sort_keys_fall_back_to_default(self):
        """許可リスト外（description/status）・不正値のみなら sort 無効＝既定並びへ。"""
        resp = self.client.get(self.url, {"sort": "description,status,bogus"})
        self.assertFalse(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_value"], "")
        self.assertTrue(all(r["key"] == "" for r in resp.context["sort_rows"]))

    def test_invalid_key_mixed_keeps_valid_only(self):
        """許可リスト外キーは無視され、有効キーだけ適用される。"""
        resp = self.client.get(self.url, {"sort": "description,name"})
        self.assertTrue(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_rows"][0], {"key": "name", "dir": "asc"})

    def test_no_sort_param_keeps_default_order(self):
        """sort 未指定なら既定並び（category__sort_order, name）を維持。"""
        resp = self.client.get(self.url)
        self.assertFalse(resp.context["sort_is_active"])
        ids = [t.id for t in resp.context["tags"]]
        self.assertEqual(ids, [self.tag_a.id, self.tag_b.id, self.tag_c.id])

    def test_push_current_includes_sort(self):
        """push_current の keys に sort が乗り、戻るでソート状態を復元できる。"""
        resp = self.client.get(self.url, {"sort": "name"})
        back = resp.context["back"]
        urls = " ".join(entry.get("url", "") for entry in back.back_stack)
        self.assertIn("sort=name", urls)

    def test_sort_control_and_toggle_markers_rendered(self):
        """ソートコントロール・列切替・補助JSソートの tags 用マーカーが描画される。"""
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        self.assertIn("js-tag-sort-control", body)
        self.assertIn("js-tag-page-sort", body)
        self.assertIn("js-tag-column-toggle", body)
        self.assertIn("tag_list_visible_columns", body)
        self.assertIn('name="sort"', body)
        self.assertIn("並び替えを適用", body)
        # ソート列ドロップダウンの5選択肢
        for label in ("カテゴリ", "タグ名", "付与Person数", "作成者", "更新日時"):
            self.assertIn(label, body)
