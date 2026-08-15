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
        from django.contrib.auth.models import Permission

        self.user = User.objects.create_user(
            username="tag_list_sort_test_user", password="dummy"
        )
        # Phase 7 ⑤：TagListView は tags.view_tag を要求する。
        self.user.user_permissions.add(Permission.objects.get(codename="view_tag"))
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
        self.assertIn("検索", body)
        # ソート列ドロップダウンの5選択肢
        for label in ("カテゴリ", "タグ名", "付与Person数", "作成者", "更新日時"):
            self.assertIn(label, body)


class Phase7TagCategoryViewAuthTests(TestCase):
    """Phase 7 ⑤-B-2：TagCategory 系 View の粗い Permission（標準 CRUD、所有者判定なし）。

    read=view_tagcategory / 作成=add / 編集・並び替え・非アーカイブ=change / 論理削除=delete。
    viewer は閲覧のみ通過・変更/作成/削除は 403、未ログインはリダイレクト。
    """

    def setUp(self):
        self.creator = User.objects.create_user(username="tc_creator", password="x")
        self.category = TagCategory.objects.create(name="業種", sort_order=1)

    def _grant(self, user, *codenames):
        from django.contrib.auth.models import Permission

        for cn in codenames:
            user.user_permissions.add(Permission.objects.get(codename=cn))

    def _client(self, user=None):
        c = Client()
        if user is not None:
            c.force_login(User.objects.get(pk=user.pk))
        return c

    def _user(self, *codenames):
        import uuid as _uuid

        u = User.objects.create_user(
            username=f"tc_{_uuid.uuid4().hex[:8]}", password="x"
        )
        if codenames:
            self._grant(u, *codenames)
        return u

    # ---- read: view_tagcategory ----
    def test_list_requires_view_tagcategory(self):
        url = reverse("tags:tag_category_list")
        self.assertEqual(self._client(self._user()).get(url).status_code, 403)
        viewer = self._user("view_tagcategory")
        self.assertEqual(self._client(viewer).get(url).status_code, 200)

    def test_list_anonymous_redirect(self):
        self.assertEqual(
            self._client().get(reverse("tags:tag_category_list")).status_code, 302
        )

    def test_detail_requires_view_tagcategory(self):
        url = reverse("tags:tag_category_detail", args=[self.category.pk])
        self.assertEqual(self._client(self._user()).get(url).status_code, 403)
        viewer = self._user("view_tagcategory")
        self.assertEqual(self._client(viewer).get(url).status_code, 200)

    # ---- 作成: add_tagcategory ----
    def test_create_requires_add_tagcategory(self):
        url = reverse("tags:tag_category_create")
        viewer = self._user("view_tagcategory")
        self.assertEqual(self._client(viewer).get(url).status_code, 403)
        editor = self._user("add_tagcategory")
        self.assertEqual(self._client(editor).get(url).status_code, 200)

    # ---- 編集: change_tagcategory ----
    def test_update_requires_change_tagcategory(self):
        url = reverse("tags:tag_category_update", args=[self.category.pk])
        viewer = self._user("view_tagcategory")
        self.assertEqual(self._client(viewer).get(url).status_code, 403)
        editor = self._user("change_tagcategory")
        self.assertEqual(self._client(editor).get(url).status_code, 200)

    def test_reorder_requires_change_tagcategory(self):
        import json

        url = reverse("tags:tag_category_reorder")
        body = json.dumps({"order": [str(self.category.pk)]})
        viewer = self._user("view_tagcategory")
        self.assertEqual(
            self._client(viewer).post(
                url, data=body, content_type="application/json"
            ).status_code,
            403,
        )
        editor = self._user("change_tagcategory")
        self.assertEqual(
            self._client(editor).post(
                url, data=body, content_type="application/json"
            ).status_code,
            200,
        )

    # ---- 論理削除: delete_tagcategory ----
    def test_delete_requires_delete_tagcategory(self):
        url = reverse("tags:tag_category_delete", args=[self.category.pk])
        # viewer は削除不可（POST）
        viewer = self._user("view_tagcategory")
        self.assertEqual(self._client(viewer).post(url).status_code, 403)
        # admin 相当（delete 保持）は通過（論理削除→リダイレクト 302）
        admin = self._user("delete_tagcategory")
        self.assertEqual(self._client(admin).post(url).status_code, 302)


class Phase7TagUnarchiveAuthTests(TestCase):
    """Phase 7 段2-C：TagUnarchiveView の粗い Permission（tags.change_tag、所有者判定なし）。

    対になる TagCategoryUnarchiveView（change_tagcategory）と同方式。change_tag 無しは
    403、有りは非アーカイブ化して 302。
    """

    def setUp(self):
        self.creator = User.objects.create_user(username="tu_creator", password="x")
        self.category = TagCategory.objects.create(name="業種", sort_order=1)
        self.tag = Tag.objects.create(
            name="廃止タグ",
            category=self.category,
            created_by=self.creator,
            is_archived=True,
        )

    def _user(self, *codenames):
        from django.contrib.auth.models import Permission

        u = User.objects.create_user(
            username=f"tu_{uuid.uuid4().hex[:8]}", password="x"
        )
        for cn in codenames:
            u.user_permissions.add(Permission.objects.get(codename=cn))
        return u

    def _client(self, user=None):
        c = Client()
        if user is not None:
            c.force_login(User.objects.get(pk=user.pk))
        return c

    def test_unarchive_requires_change_tag(self):
        url = reverse("tags:tag_unarchive", args=[self.tag.pk])
        # change_tag を持たないユーザー → 403、タグはアーカイブ済みのまま
        viewer = self._user("view_tag")
        self.assertEqual(self._client(viewer).post(url).status_code, 403)
        self.tag.refresh_from_db()
        self.assertTrue(self.tag.is_archived)

    def test_unarchive_with_change_tag_ok(self):
        url = reverse("tags:tag_unarchive", args=[self.tag.pk])
        # change_tag を持つユーザー → 非アーカイブ化されて 302
        editor = self._user("change_tag")
        self.assertEqual(self._client(editor).post(url).status_code, 302)
        self.tag.refresh_from_db()
        self.assertFalse(self.tag.is_archived)


class TagListFilterTests(TestCase):
    """v1.6 UI 第3弾：タグ一覧の絞り込み（タグ名・タグカテゴリ複数トグル・状態トグル）。"""

    def setUp(self):
        from django.contrib.auth.models import Permission

        self.user = User.objects.create_user(username="tag_filter_user", password="x")
        self.user.user_permissions.add(Permission.objects.get(codename="view_tag"))
        self.client = Client()
        self.client.force_login(self.user)
        self.cat_a = TagCategory.objects.create(name="業種", sort_order=1)
        self.cat_b = TagCategory.objects.create(name="地域", sort_order=2)
        self.tag_mfg = Tag.objects.create(
            name="製造業", category=self.cat_a, created_by=self.user
        )
        self.tag_it = Tag.objects.create(
            name="IT", category=self.cat_a, created_by=self.user
        )
        self.tag_tokyo = Tag.objects.create(
            name="東京", category=self.cat_b, created_by=self.user
        )
        self.tag_archived = Tag.objects.create(
            name="廃止タグ", category=self.cat_a, created_by=self.user,
            is_archived=True,
        )
        self.url = reverse("tags:tag_list")

    def test_filter_by_tag_name(self):
        resp = self.client.get(self.url, {"name": "製造"})
        self.assertEqual(resp.status_code, 200)
        names = [t.name for t in resp.context["tags"]]
        self.assertIn("製造業", names)
        self.assertNotIn("IT", names)

    def test_category_toggle_multi_select(self):
        # 複数カテゴリ選択（getlist）で OR 抽出。
        resp = self.client.get(self.url, {"searched": "1", "category": [str(self.cat_b.id)]})
        self.assertEqual(resp.status_code, 200)
        names = [t.name for t in resp.context["tags"]]
        self.assertIn("東京", names)
        self.assertNotIn("製造業", names)

    def test_status_toggle_archived(self):
        # 既定はアクティブのみ＝アーカイブは出ない。
        resp = self.client.get(self.url)
        names = [t.name for t in resp.context["tags"]]
        self.assertNotIn("廃止タグ", names)
        self.assertIn("製造業", names)
        # 状態トグルで archived を選ぶとアーカイブのみ。
        resp = self.client.get(self.url, {"searched": "1", "status": "archived"})
        names = [t.name for t in resp.context["tags"]]
        self.assertIn("廃止タグ", names)
        self.assertNotIn("製造業", names)

    def test_filter_form_markers_rendered(self):
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        self.assertIn("タグカテゴリ", body)             # カテゴリ絞り込みラベル（体言、§6で「で絞り込み」除去）
        self.assertIn('name="name"', body)             # タグ名絞り込み
        self.assertIn('name="status"', body)           # 状態トグル

    def test_no_template_comment_leak(self):
        # 複数行 {# #} の画面漏れが塞がれている（第8弾の検索フォーム説明コメント等）。
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertNotIn("検索フォーム（HIG", body)   # コメント本文が漏れていない
        self.assertNotIn("{% comment %}", body)        # comment タグ自体も描画されない

    def test_list_structure_aligned(self):
        # 第8弾：キャンペーン一覧と同構造。テキスト（タグ名）は折りたたみの外、
        # トグル絞り込みは折りたたみの中で初期閉、ソートと横並び。
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("app-form-grid-edit", body)              # テキスト欄は折りたたみの外
        self.assertIn('aria-expanded="false"', body)        # 絞り込み折りたたみは初期閉
        self.assertNotIn("app-person-filter-collapsible is-open", body)
        self.assertIn("js-tag-sort-control", body)          # ソート折りたたみと同フォーム
        self.assertIn('name="category"', body)         # カテゴリトグル（checkbox）
        self.assertNotIn("アーカイブ済みのみを表示", body)  # 旧ボタンは廃止
        self.assertNotIn("＋ 新規タグ作成", body)          # ＋ 除去


class TagCategoryCascadeArchiveTests(TestCase):
    """TagCategory のアーカイブ化・非アーカイブ化に伴う配下 Tag 連動処理のテスト。"""

    def setUp(self):
        from django.contrib.auth.models import Permission

        self.user = User.objects.create_user(
            username="cascade_test_user", password="password"
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="delete_tagcategory"),
            Permission.objects.get(codename="change_tagcategory"),
            Permission.objects.get(codename="change_tag"),
            Permission.objects.get(codename="view_tag"),
        )
        self.client = Client()
        self.client.force_login(self.user)

        # 対象カテゴリ A
        self.cat_a = TagCategory.objects.create(name="カテゴリA")
        self.tag_a1 = Tag.objects.create(
            name="タグA1(アクティブ)", category=self.cat_a, created_by=self.user, is_archived=False
        )
        self.tag_a2 = Tag.objects.create(
            name="タグA2(事前アーカイブ)", category=self.cat_a, created_by=self.user, is_archived=True
        )

        # 別カテゴリ B
        self.cat_b = TagCategory.objects.create(name="カテゴリB")
        self.tag_b1 = Tag.objects.create(
            name="タグB1(アクティブ)", category=self.cat_b, created_by=self.user, is_archived=False
        )

    def test_archive_category_archives_active_child_tags(self):
        """TagCategory をアーカイブ化すると、配下のアクティブな Tag が全て is_archived=True になる。"""
        url = reverse("tags:tag_category_delete", kwargs={"pk": self.cat_a.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        self.cat_a.refresh_from_db()
        self.tag_a1.refresh_from_db()
        self.assertTrue(self.cat_a.is_archived)
        self.assertTrue(self.tag_a1.is_archived)

    def test_archive_category_keeps_already_archived_child_tags(self):
        """TagCategory をアーカイブ化しても、既に個別アーカイブ済みだった Tag の状態は変わらない（is_archived=Trueのまま）。"""
        url = reverse("tags:tag_category_delete", kwargs={"pk": self.cat_a.pk})
        self.client.post(url)

        self.tag_a2.refresh_from_db()
        self.assertTrue(self.tag_a2.is_archived)

    def test_unarchive_category_unarchives_all_child_tags(self):
        """TagCategory を非アーカイブ化すると、配下のアーカイブ済み Tag（個別アーカイブ済みを含む）が全て is_archived=False になる。"""
        # まずカテゴリAをアーカイブ化
        self.cat_a.is_archived = True
        self.cat_a.save()
        self.tag_a1.is_archived = True
        self.tag_a1.save()

        # 非アーカイブ化実行
        url = reverse("tags:tag_category_unarchive", kwargs={"pk": self.cat_a.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        self.cat_a.refresh_from_db()
        self.tag_a1.refresh_from_db()
        self.tag_a2.refresh_from_db()

        self.assertFalse(self.cat_a.is_archived)
        self.assertFalse(self.tag_a1.is_archived)
        self.assertFalse(self.tag_a2.is_archived)  # 個別アーカイブ済みだったタグも復元される（仕様通りの挙動）

    def test_other_category_tags_unaffected(self):
        """別カテゴリに属する Tag が、対象カテゴリのアーカイブ・非アーカイブ操作の影響を受けない。"""
        # カテゴリAをアーカイブ化
        url_archive = reverse("tags:tag_category_delete", kwargs={"pk": self.cat_a.pk})
        self.client.post(url_archive)

        self.tag_b1.refresh_from_db()
        self.assertFalse(self.tag_b1.is_archived)

        # カテゴリAを非アーカイブ化
        url_unarchive = reverse("tags:tag_category_unarchive", kwargs={"pk": self.cat_a.pk})
        self.client.post(url_unarchive)

        self.tag_b1.refresh_from_db()
        self.assertFalse(self.tag_b1.is_archived)

    def test_individual_tag_unarchive_blocked_when_category_is_archived(self):
        """親カテゴリがアーカイブ済みの状態で配下タグの個別の非アーカイブ化を試みるとブロックされる。"""
        # 親カテゴリをアーカイブ状態にする
        self.cat_a.is_archived = True
        self.cat_a.save()
        self.tag_a2.is_archived = True
        self.tag_a2.save()

        url = reverse("tags:tag_unarchive", kwargs={"pk": self.tag_a2.pk})
        resp = self.client.post(url, follow=True)

        self.tag_a2.refresh_from_db()
        self.assertTrue(self.tag_a2.is_archived)  # ブロックされ is_archived=True のまま

        msgs = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("先にカテゴリを非アーカイブ化してください" in m for m in msgs))

    def test_individual_tag_unarchive_succeeds_when_category_is_active(self):
        """親カテゴリがアクティブな状態では、タグの個別非アーカイブ化が成功する。"""
        self.cat_a.is_archived = False
        self.cat_a.save()
        self.tag_a2.is_archived = True
        self.tag_a2.save()

        url = reverse("tags:tag_unarchive", kwargs={"pk": self.tag_a2.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        self.tag_a2.refresh_from_db()
        self.assertFalse(self.tag_a2.is_archived)  # 成功して is_archived=False になる


class TagBulkAssignViewTests(TestCase):
    """TagBulkAssignView の単体テスト。"""

    def setUp(self):
        from django.contrib.auth.models import Permission
        self.user = get_user_model().objects.create_user(
            username="tag_user", password="password"
        )
        assign_perm = Permission.objects.get(codename="assign_tag")
        self.user.user_permissions.add(assign_perm)
        self.client.login(username="tag_user", password="password")

        self.person = Person.objects.create()
        self.cat = TagCategory.objects.create(name="カテゴリ1", sort_order=1)
        self.tag1 = Tag.objects.create(name="タグ1", category=self.cat, created_by=self.user)
        self.tag2 = Tag.objects.create(name="タグ2", category=self.cat, created_by=self.user)
        self.tag3 = Tag.objects.create(name="タグ3", category=self.cat, created_by=self.user)
        self.url = reverse("tags:tag_bulk_assign")

    def test_bulk_assign_add_and_remove(self):
        """追加分・削除分が同時に正しく反映されること（差分計算の検証）。"""
        TagAssignment.objects.create(tag=self.tag1, person=self.person, assigned_by=self.user)

        payload = {
            "person_id": str(self.person.id),
            "tag_ids": [str(self.tag2.id), str(self.tag3.id)],
        }
        resp = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        self.assertTrue(data["ok"])
        assigned_tag_ids = set(
            TagAssignment.objects.filter(person=self.person).values_list("tag_id", flat=True)
        )
        self.assertEqual(assigned_tag_ids, {self.tag2.id, self.tag3.id})
        self.assertNotIn(self.tag1.id, assigned_tag_ids)

    def test_bulk_assign_add_only(self):
        """追加のみのケース。"""
        TagAssignment.objects.create(tag=self.tag1, person=self.person, assigned_by=self.user)

        payload = {
            "person_id": str(self.person.id),
            "tag_ids": [str(self.tag1.id), str(self.tag2.id)],
        }
        resp = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        assigned_tag_ids = set(
            TagAssignment.objects.filter(person=self.person).values_list("tag_id", flat=True)
        )
        self.assertEqual(assigned_tag_ids, {self.tag1.id, self.tag2.id})

    def test_bulk_assign_remove_only(self):
        """削除のみのケース。"""
        TagAssignment.objects.create(tag=self.tag1, person=self.person, assigned_by=self.user)
        TagAssignment.objects.create(tag=self.tag2, person=self.person, assigned_by=self.user)

        payload = {
            "person_id": str(self.person.id),
            "tag_ids": [str(self.tag1.id)],
        }
        resp = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        assigned_tag_ids = set(
            TagAssignment.objects.filter(person=self.person).values_list("tag_id", flat=True)
        )
        self.assertEqual(assigned_tag_ids, {self.tag1.id})

    def test_bulk_assign_no_change(self):
        """変更なしの場合、TagAssignment の状態が変わらないこと。"""
        asg = TagAssignment.objects.create(tag=self.tag1, person=self.person, assigned_by=self.user)

        payload = {
            "person_id": str(self.person.id),
            "tag_ids": [str(self.tag1.id)],
        }
        resp = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        current_asg = TagAssignment.objects.get(person=self.person)
        self.assertEqual(current_asg.id, asg.id)

    def test_bulk_assign_requires_permission(self):
        """権限のないユーザーがブロックされること。"""
        no_perm_user = get_user_model().objects.create_user(username="noperm", password="password")
        self.client.login(username="noperm", password="password")

        payload = {
            "person_id": str(self.person.id),
            "tag_ids": [str(self.tag1.id)],
        }
        resp = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 403)

    def test_bulk_assign_invalid_tag_id_returns_400(self):
        """存在しないまたはアーカイブ済みの tag_id でエラー（400）になること。"""
        archived_tag = Tag.objects.create(name="アーカイブタグ", category=self.cat, created_by=self.user, is_archived=True)

        payload = {
            "person_id": str(self.person.id),
            "tag_ids": [str(archived_tag.id)],
        }
        resp = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_bulk_assign_missing_person_id_returns_400(self):
        """person_id が欠損している場合 400 になること。"""
        payload = {"tag_ids": [str(self.tag1.id)]}
        resp = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])



