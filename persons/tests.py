"""persons アプリの View 層テスト（v1.4.2 §11.4 / §11.5）。"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.constants import DuplicateMergeReason
from contacts.models import Contact, ContactSns
from duplicates.models import PersonMergeLog
from persons.models import Person


User = get_user_model()


def _empty_sns_management_form(prefix="sns"):
    """ContactSns InlineFormSet の空 management_form（POST テスト用、Phase F1 §11.6.7）。"""
    return {
        f"{prefix}-TOTAL_FORMS": "0",
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def _sns_management_form(total, initial=0, prefix="sns"):
    """ContactSns InlineFormSet の management_form（行ありテスト用、Phase F1 §11.6.7）。"""
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


class PersonListViewTests(TestCase):
    """PersonListView の単体テスト。

    7 フィールド検索（primary_contact 経由）、status 3 チェックボックス、
    初回 active のみ、ページネーション、primary_contact NULL Person のリスト掲載
    を検証する。
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="person_list_test_user", password="dummy"
        )
        self.person_a = Person.objects.create()
        self.contact_a = Contact.objects.create(
            person=self.person_a,
            status=Contact.Status.PRIMARY,
            full_name="Alice Smith",
            organization="Acme Corp",
            department="Sales",
            title="Manager",
            email="alice@acme.example",
            personal_phone="03-1234-5678",
            address="Tokyo",
        )
        self.person_a.primary_contact = self.contact_a
        self.person_a.save(update_fields=["primary_contact", "updated_at"])

        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("persons:person_list")

    def _make_active_with_primary(self, **contact_kwargs):
        """active な Person + primary Contact のセットを作るヘルパー。"""
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, **contact_kwargs
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person, contact

    def test_default_shows_active_only(self):
        """初回（searched なし）→ active のみ、merged / archived は非表示。"""
        merged = Person.objects.create(status=Person.Status.MERGED)
        archived = Person.objects.create(status=Person.Status.ARCHIVED)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(self.person_a.id, ids)
        self.assertNotIn(merged.id, ids)
        self.assertNotIn(archived.id, ids)
        self.assertEqual(resp.context["selected_statuses"], ["active"])
        self.assertFalse(resp.context["searched"])

    def test_status_filter_merged(self):
        """?status=merged&searched=1 → merged のみ表示。"""
        merged = Person.objects.create(status=Person.Status.MERGED)
        archived = Person.objects.create(status=Person.Status.ARCHIVED)

        resp = self.client.get(self.url, {"searched": "1", "status": "merged"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(merged.id, ids)
        self.assertNotIn(self.person_a.id, ids)
        self.assertNotIn(archived.id, ids)

    def test_status_filter_all_unchecked(self):
        """?searched=1 + status なし → 0 件。"""
        Person.objects.create(status=Person.Status.MERGED)
        resp = self.client.get(self.url, {"searched": "1"})
        self.assertEqual(list(resp.context["persons"]), [])
        self.assertEqual(resp.context["selected_statuses"], [])

    def test_search_name_through_primary_contact(self):
        """primary_contact.full_name で検索ヒット。"""
        p_other, _ = self._make_active_with_primary(full_name="Bob Other")

        resp = self.client.get(self.url, {"name": "Alice"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(self.person_a.id, ids)
        self.assertNotIn(p_other.id, ids)

    def test_search_tel_or_personal_phone_mobile_phone_personal_fax(self):
        """tel は primary_contact の personal_phone / mobile_phone / personal_fax の OR 一致。"""
        p_mobile_phone, _ = self._make_active_with_primary(
            full_name="MobOnly", mobile_phone="090-1111-2222"
        )
        p_personal_fax, _ = self._make_active_with_primary(
            full_name="FaxOnly", personal_fax="06-9999-8888"
        )

        resp = self.client.get(self.url, {"tel": "1234"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(self.person_a.id, ids)
        self.assertNotIn(p_mobile_phone.id, ids)
        self.assertNotIn(p_personal_fax.id, ids)

        resp = self.client.get(self.url, {"tel": "090-1111"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(p_mobile_phone.id, ids)
        self.assertNotIn(self.person_a.id, ids)

        resp = self.client.get(self.url, {"tel": "06-9999"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(p_personal_fax.id, ids)
        self.assertNotIn(self.person_a.id, ids)

    def test_search_and_name_organization(self):
        """name と organization の AND 検索。"""
        self._make_active_with_primary(
            full_name="Alice Tanaka", organization="Wonder Corp"
        )
        self._make_active_with_primary(
            full_name="Bob Smith", organization="Acme Industries"
        )
        p_both, _ = self._make_active_with_primary(
            full_name="Alice Brown", organization="Acme Group"
        )

        resp = self.client.get(self.url, {"name": "Alice", "organization": "Acme"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(self.person_a.id, ids)
        self.assertIn(p_both.id, ids)
        self.assertEqual(len(ids), 2)

    # ---- HIG 第6章：多段ソート（単一 sort パラメータ、例 ?sort=company,-title,name）----

    def test_single_key_sort_asc_and_desc(self):
        """?sort=company（昇順）/ ?sort=-company（降順）で会社順に並ぶ。"""
        p_c, _ = self._make_active_with_primary(full_name="C", organization="Cccorp")
        p_a, _ = self._make_active_with_primary(full_name="A", organization="Aaacorp")
        p_b, _ = self._make_active_with_primary(full_name="B", organization="Bbbcorp")

        resp = self.client.get(self.url, {"sort": "company"})
        ids = [p.id for p in resp.context["persons"]]
        # Aaacorp < Acme Corp(person_a) < Bbbcorp < Cccorp
        self.assertLess(ids.index(p_a.id), ids.index(p_b.id))
        self.assertLess(ids.index(p_b.id), ids.index(p_c.id))
        self.assertTrue(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "asc"})

        resp = self.client.get(self.url, {"sort": "-company"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertLess(ids.index(p_c.id), ids.index(p_b.id))
        self.assertLess(ids.index(p_b.id), ids.index(p_a.id))
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "desc"})

    def test_multi_key_sort_priority(self):
        """?sort=company,-name で 会社昇順 → 同社内は氏名降順（多段・全件）。"""
        # 同じ会社 "Same" を持つ 2 人を氏名違いで作る。
        # 氏名ソートは phonetic_name（読み）順なので、-name 降順で Zoe→Amy になるよう読みを付ける。
        p_same_z, _ = self._make_active_with_primary(
            full_name="Zoe", organization="Same", phonetic_name="ゾエ"
        )
        p_same_a, _ = self._make_active_with_primary(
            full_name="Amy", organization="Same", phonetic_name="エイミー"
        )
        # 別会社（後ろに来る）
        p_other, _ = self._make_active_with_primary(
            full_name="Mike", organization="Zzcorp"
        )
        resp = self.client.get(self.url, {"sort": "company,-name"})
        ids = [p.id for p in resp.context["persons"]]
        # 会社 "Same" グループが "Zzcorp" より前、グループ内は氏名降順 Zoe→Amy
        self.assertLess(ids.index(p_same_z.id), ids.index(p_same_a.id))
        self.assertLess(ids.index(p_same_a.id), ids.index(p_other.id))
        # sort_rows が 2 段ぶん復元される
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "asc"})
        self.assertEqual(resp.context["sort_rows"][1], {"key": "name", "dir": "desc"})

    def test_name_sort_uses_phonetic_name(self):
        """氏名ソート（?sort=name）は漢字コード順でなく phonetic_name（カタカナ読み）の五十音順。"""
        # 漢字コード順と読み順が一致しない 3 人を用意（読み: サトウ < スズキ < タナカ）。
        # 漢字順なら 佐(U+4F50) < 田(U+7530) < 鈴(U+9234) で sato→tanaka→suzuki になり、
        # 読み順（sato→suzuki→tanaka）と食い違うため、読みで並んでいることを区別できる。
        p_tanaka, _ = self._make_active_with_primary(
            full_name="田中", phonetic_name="タナカ"
        )
        p_suzuki, _ = self._make_active_with_primary(
            full_name="鈴木", phonetic_name="スズキ"
        )
        p_sato, _ = self._make_active_with_primary(
            full_name="佐藤", phonetic_name="サトウ"
        )

        resp = self.client.get(self.url, {"sort": "name"})
        ids = [p.id for p in resp.context["persons"]]
        # 読み昇順：サトウ → スズキ → タナカ
        self.assertLess(ids.index(p_sato.id), ids.index(p_suzuki.id))
        self.assertLess(ids.index(p_suzuki.id), ids.index(p_tanaka.id))

        resp = self.client.get(self.url, {"sort": "-name"})
        ids = [p.id for p in resp.context["persons"]]
        # 読み降順：タナカ → スズキ → サトウ
        self.assertLess(ids.index(p_tanaka.id), ids.index(p_suzuki.id))
        self.assertLess(ids.index(p_suzuki.id), ids.index(p_sato.id))

    def test_invalid_keys_ignored_in_multi(self):
        """許可リスト外キー（department/address）は無視され、有効キーだけ適用される。"""
        p_z, _ = self._make_active_with_primary(full_name="Z", organization="Zzcorp")
        p_a, _ = self._make_active_with_primary(full_name="A", organization="Aaacorp")
        resp = self.client.get(self.url, {"sort": "department,company,address"})
        ids = [p.id for p in resp.context["persons"]]
        # company 昇順だけが効く
        self.assertLess(ids.index(p_a.id), ids.index(p_z.id))
        self.assertTrue(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_rows"][0], {"key": "company", "dir": "asc"})

    def test_all_invalid_or_empty_sort_keeps_default(self):
        """全キーが不正（or 空）なら sort 無効＝既定の並びを維持し、折りたたみは閉じ判定。"""
        resp = self.client.get(self.url, {"sort": "department,address"})
        self.assertFalse(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_value"], "")
        # 全行が「指定なし」
        self.assertTrue(all(r["key"] == "" for r in resp.context["sort_rows"]))

        resp_empty = self.client.get(self.url, {"sort": ""})
        self.assertFalse(resp_empty.context["sort_is_active"])

    def test_no_sort_param_keeps_default_order(self):
        """sort 未指定なら search_persons の既定並びを維持し、折りたたみは閉じ判定。"""
        resp = self.client.get(self.url)
        self.assertFalse(resp.context["sort_is_active"])
        self.assertEqual(resp.context["sort_value"], "")

    def test_duplicate_key_first_wins(self):
        """同一キーの二重指定は先勝ち（?sort=-company,company → company 降順のみ）。"""
        resp = self.client.get(self.url, {"sort": "-company,company"})
        rows = resp.context["sort_rows"]
        self.assertEqual(rows[0], {"key": "company", "dir": "desc"})
        # 2 行目以降は未指定（重複が落ちている）
        self.assertEqual(rows[1]["key"], "")

    def test_collapse_open_state_reflects_sort(self):
        """ソート指定があれば折りたたみは開く（is-open）、無ければ閉じる。"""
        resp_sorted = self.client.get(self.url, {"sort": "company"})
        self.assertContains(resp_sorted, "js-person-sort-control is-open")
        resp_plain = self.client.get(self.url)
        self.assertNotContains(resp_plain, "js-person-sort-control is-open")

    def test_thead_uses_japanese_status_heading(self):
        """<thead> 見出しが「状態」になり、英字見出し status を出さない（HIG 原則4）。"""
        resp = self.client.get(self.url)
        self.assertContains(resp, "状態")
        self.assertNotContains(resp, ">status<")

    def test_header_sort_removed_and_sort_control_rendered(self):
        """列見出しクリックのソート（data-sort-key/方向アイコン script）は廃止され、
        検索フォーム内のソートコントロール＋列切替が描画される。"""
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        # 旧・列見出しクリックの URL 書き換え式ソート（__personSortHeaderInit）は消えている。
        # ※ data-sort-key は現在ページ内 DOM ソートの氏名 phonetic 比較用に正当に使うため、
        #    「存在しないこと」のチェック対象からは外す（旧式の判定指標は __personSortHeaderInit）。
        self.assertNotIn("__personSortHeaderInit", body)
        # フォーム内ソートコントロール（折りたたみ＋4列ドロップダウン＋方向トグル）
        self.assertIn("js-person-sort-control", body)
        self.assertIn('name="sort"', body)
        self.assertIn("app-radio-toggle", body)
        for label in ("指定なし", "氏名", "会社", "役職", "連絡先"):
            self.assertIn(label, body)
        # 列切替（data-col-key と persons 専用キー）は維持
        self.assertIn("data-col-key", body)
        self.assertIn("person_list_visible_columns", body)

    def test_column_toggle_init_is_deferred_until_dom_ready(self):
        """列切替の初期化が DOM 構築後に走る（再読込時もテーブルに表示状態が適用される）。

        partial はテーブルより上のツールバーに置かれるため、即時実行だと初期 apply の
        時点で [data-col-key] が未生成になり localStorage 復元状態が反映されない。
        DOMContentLoaded 遅延が入っていることを描画 HTML で回帰確認する（JS 層のため）。
        """
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        self.assertIn("DOMContentLoaded", body)
        self.assertIn("readyState", body)
        self.assertIn("person_list_visible_columns", body)

    def test_pagination_preserves_sort_query(self):
        """ソート状態でページ送りしても sort が失われない（_pagination.html）。"""
        for i in range(25):
            self._make_active_with_primary(
                full_name=f"pp-{i:02d}", organization=f"org{i:02d}"
            )
        resp = self.client.get(self.url, {"sort": "-company"})
        self.assertTrue(resp.context["is_paginated"])
        body = resp.content.decode("utf-8")
        # urlencode で - は %2D にエンコードされるため両表記を許容して確認
        self.assertTrue("sort=-company" in body or "sort=%2Dcompany" in body)

    def test_push_current_captures_sort_not_dir(self):
        """戻る復元用に push_current が sort を取り込み、廃止した dir は keys に無い（HIG 6.1）。"""
        resp = self.client.get(self.url, {"sort": "company,-title"})
        back = resp.context["back"]
        urls = " ".join(entry.get("url", "") for entry in back.back_stack)
        self.assertIn("sort=", urls)
        self.assertIn("company", urls)
        self.assertNotIn("dir=", urls)

    def test_pagination_21_records(self):
        """21 件以上で 2 ページに分かれる（paginate_by=20）。"""
        for i in range(20):
            self._make_active_with_primary(full_name=f"page-{i:02d}")

        resp = self.client.get(self.url)
        self.assertTrue(resp.context["is_paginated"])
        self.assertEqual(len(list(resp.context["persons"])), 20)

        resp2 = self.client.get(self.url, {"page": "2"})
        self.assertEqual(len(list(resp2.context["persons"])), 1)

    def test_orphan_person_in_list(self):
        """primary_contact NULL の active Person もリストに表示される。"""
        orphan = Person.objects.create()  # primary_contact=None, status=active
        resp = self.client.get(self.url)
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(orphan.id, ids)
        body = resp.content.decode()
        # 氏名欄は内部語を出さず (氏名なし) に畳む（HIG 原則4）
        self.assertIn("(氏名なし)", body)
        self.assertNotIn("(primary_contact 未設定)", body)

    # ---- v1.6 残UI改修：連絡先4列分割・住所列・ステータス業務語/トグル/折りたたみ・列見出しJSソート ----

    def test_contact_columns_split_into_four_and_address(self):
        """連絡先1列が メール/携帯電話/個人電話・FAX/会社電話・FAX の4列に分割され、住所列が増える。

        電話・FAX セルは両値あれば「TEL ◯◯／FAX ◯◯」併記、片方だけならそれだけ出す（item 8）。
        """
        self._make_active_with_primary(
            full_name="Carol Contact",
            email="carol@ex.example",
            mobile_phone="090-5555-6666",
            org_phone="0561-00-0000",
            org_fax="0561-11-1111",
            personal_phone="052-2222-3333",
            address="Aichi Toyota",
        )
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        # 見出し（業務語・分割後）
        for head in ("メール", "携帯電話", "個人電話・FAX", "会社電話・FAX", "住所"):
            self.assertIn(head, body)
        # 値（携帯・住所・会社電話/FAX 併記）
        self.assertIn("090-5555-6666", body)
        self.assertIn("Aichi Toyota", body)
        self.assertIn("TEL 0561-00-0000／FAX 0561-11-1111", body)
        # 個人は phone のみ → TEL のみ（FAX 併記なし）
        self.assertIn("TEL 052-2222-3333", body)

    def test_status_filter_japanese_business_words_and_toggle(self):
        """ステータスフィルタが業務語（有効/マージ済み/アーカイブ）＋複数選択トグルで描画される。

        表示は業務語、送信値（internal）は active/merged/archived のまま（item 2/3）。
        """
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        for label in ("有効", "マージ済み", "アーカイブ"):
            self.assertIn(label, body)
        # 送信値は internal を維持
        for value in ('value="active"', 'value="merged"', 'value="archived"'):
            self.assertIn(value, body)
        # 複数選択トグル（checkbox を app-radio-toggle でトグル化、name は維持）
        self.assertIn("app-radio-toggle", body)
        self.assertIn('name="status"', body)

    def test_status_filter_collapsed_by_default(self):
        """ステータスフィルタは折りたたみ（app-collapsible）に入り、初期は閉（item 4）。"""
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        self.assertIn("app-person-filter-collapsible", body)
        # ステータス見出しの折りたたみは初期 aria-expanded=false（自動展開しない）
        self.assertIn("<span>ステータス</span>", body)
        self.assertIn('aria-expanded="false"', body)

    def test_search_card_width_scoped_to_persons(self):
        """検索カードに persons 専用の横幅スコープ class が付く（item 1・共有partを汚さない）。"""
        resp = self.client.get(self.url)
        self.assertContains(resp, "app-person-search")

    def test_column_toggle_lists_new_columns_with_off_defaults(self):
        """列切替メニューに新列が並び、増えた3列は data-default="0"（初期OFF）になる（item 9）。"""
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        for key in ("status", "email", "mobile_phone",
                    "personal_contact", "org_contact", "address"):
            self.assertIn('data-col-key="%s"' % key, body)
        # 増設3列は初期OFF
        for key in ("personal_contact", "org_contact", "address"):
            self.assertIn('data-col-key="%s" data-default="0"' % key, body)

    def test_page_sort_markers_present_and_not_url_rewrite(self):
        """列見出しの現在ページ内JSソート用マーカーが出る一方、旧URL書き換え方式の痕跡は無い（item 11）。"""
        resp = self.client.get(self.url)
        body = resp.content.decode("utf-8")
        # クライアント側DOMソートの目印
        self.assertIn("js-person-page-sort", body)
        self.assertIn("data-sort-col", body)
        # 旧 URL クエリ書き換え方式（サーバー側ソートへの入力）と取り違えていないこと。
        # ※ data-sort-key は現在ページ内 DOM ソートの氏名 phonetic 比較用に正当に使うため、
        #    取り違え検出の指標は __personSortHeaderInit（旧式 JS の初期化マーカー）の不在で判定する。
        self.assertNotIn("__personSortHeaderInit", body)


class PersonDetailViewTests(TestCase):
    """PersonDetailView の単体テスト（status による 4 分岐）。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="person_detail_test_user", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, person):
        return reverse("persons:person_detail", kwargs={"pk": person.pk})

    def _make_active_with_primary(self):
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, full_name="A"
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person, contact

    def test_active_person_redirects_to_primary_contact_detail(self):
        """active + primary_contact あり → 302 リダイレクト → ContactDetailView。"""
        person, contact = self._make_active_with_primary()
        resp = self.client.get(self._url(person))
        self.assertEqual(resp.status_code, 302)
        expected = reverse(
            "contacts:contact_detail", kwargs={"pk": contact.pk}
        )
        # back_stack を渡していないので URL に付かず素のまま
        self.assertEqual(resp.url, expected)

    def test_active_person_with_null_primary_renders_orphan_page(self):
        """active + primary_contact NULL → orphan テンプレート + Admin リンク。"""
        person = Person.objects.create()
        resp = self.client.get(self._url(person))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "persons/person_detail_orphan.html")
        body = resp.content.decode()
        self.assertIn("Django Admin で編集", body)
        self.assertIn("primary_contact 未設定", body)

    def test_merged_person_renders_merged_page(self):
        """merged → merged テンプレート + merged_into リンク表示。"""
        surviving = Person.objects.create()
        merged = Person.objects.create(
            status=Person.Status.MERGED, merged_into=surviving
        )
        resp = self.client.get(self._url(merged))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "persons/person_detail_merged.html")
        body = resp.content.decode()
        self.assertIn("統合先 Person", body)
        self.assertIn(str(surviving.id), body)

    def test_archived_person_renders_archived_page(self):
        """archived → archived テンプレート使用。"""
        archived = Person.objects.create(status=Person.Status.ARCHIVED)
        resp = self.client.get(self._url(archived))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "persons/person_detail_archived.html")
        # status バッジは status_badge タグで日本語ラベル表示（HIG 原則4・英字値は出さない）
        self.assertIn("アーカイブ", resp.content.decode())
        self.assertNotIn(">archived<", resp.content.decode())

    def test_merged_person_shows_merge_logs(self):
        """merged Person はマージ履歴セクションを表示。"""
        surviving = Person.objects.create()
        merged = Person.objects.create(
            status=Person.Status.MERGED, merged_into=surviving
        )
        PersonMergeLog.objects.create(
            surviving_person=surviving,
            merged_person=merged,
            executed_at=timezone.now(),
            executed_by=self.user,
        )
        resp = self.client.get(self._url(merged))
        body = resp.content.decode()
        self.assertIn("マージ履歴", body)
        # デフォルト status は UNDOABLE → 「復元可能」バッジが出る
        self.assertIn("復元可能", body)

    def test_merged_person_shows_inactive_contacts(self):
        """merged Person は inactive Contact 履歴セクションを表示。"""
        surviving = Person.objects.create()
        merged = Person.objects.create(
            status=Person.Status.MERGED, merged_into=surviving
        )
        Contact.objects.create(
            person=merged,
            status=Contact.Status.INACTIVE,
            full_name="inactive-name",
        )
        resp = self.client.get(self._url(merged))
        body = resp.content.decode()
        self.assertIn("inactive Contact 履歴", body)
        self.assertIn("inactive-name", body)


# ======================================================================
# D-Form ステップ2：PersonAddAdditionalRoleView (9 番) のテスト
# ======================================================================


class PersonAddAdditionalRoleViewTests(TestCase):
    """PersonAddAdditionalRoleView（9 番、仕様書 §3.6 / §10.12 / §11.4.5）の単体テスト。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="add_role_test_user", password="dummy"
        )
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P-name",
            organization="P-co",
            # salutation_name を埋め is_manual=True とし、Contact.save() の自動補完を抑止する
            # （補完されると salutation_name の CFC が増えてカウントがずれるため）。
            salutation_name="テスト 様",
            salutation_name_is_manual=True,
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])

        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, person=None):
        return reverse(
            "persons:person_add_additional_role",
            kwargs={"pk": (person or self.person).pk},
        )

    def _base_post_data(self):
        from contacts.models import Contact as _Contact

        data = {f: "" for f in _Contact.UPDATABLE_FIELDS}
        # Phase F1 で 9 番も salutation_name 必須化。既定で有効値を入れておく
        # （空必須エラーを検証する test では明示的に空へ上書きする）。
        data["salutation_name"] = "別肩書 様"
        data.update(_empty_sns_management_form())
        return data

    # ---- GET ----

    def test_get_active_returns_200(self):
        """active Person → 200、context に form / person / back を含む。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)
        self.assertEqual(resp.context["person"], self.person)
        self.assertIn("back", resp.context)

    def test_get_active_orphan_returns_200(self):
        """active かつ primary_contact NULL（orphan）→ 200。"""
        orphan = Person.objects.create()  # status=ACTIVE (default), primary_contact=None
        self.assertIsNone(orphan.primary_contact_id)
        resp = self.client.get(self._url(orphan))
        self.assertEqual(resp.status_code, 200)

    def test_get_archived_returns_404(self):
        self.person.status = Person.Status.ARCHIVED
        self.person.save(update_fields=["status", "updated_at"])
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_get_merged_returns_404(self):
        self.person.status = Person.Status.MERGED
        self.person.save(update_fields=["status", "updated_at"])
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_get_nonexistent_returns_404(self):
        import uuid as _uuid

        url = reverse(
            "persons:person_add_additional_role",
            kwargs={"pk": _uuid.uuid4()},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_redirects(self):
        """LoginRequiredMixin → 未ログインは login にリダイレクト（302）。"""
        c = Client()
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 302)

    # ---- POST ----

    def test_post_valid_creates_active_contact_and_redirects(self):
        """有効データ → 新規 active Contact 作成、Contact 詳細画面にリダイレクト。"""
        data = self._base_post_data()
        data["full_name"] = "別肩書 太郎"
        data["organization"] = "別会社"

        resp = self.client.post(self._url(), data=data)

        self.assertEqual(resp.status_code, 302)
        # full_name は §3.4 正規化で空白除去される（"別肩書 太郎" → "別肩書太郎"）
        new_contact = Contact.objects.filter(
            person=self.person, full_name="別肩書太郎"
        ).first()
        self.assertIsNotNone(new_contact)
        self.assertEqual(new_contact.status, Contact.Status.ACTIVE)
        self.assertEqual(new_contact.organization, "別会社")
        self.assertEqual(
            resp.url,
            reverse(
                "contacts:contact_detail", kwargs={"pk": new_contact.pk}
            ),
        )

    def test_post_does_not_create_field_confidence_records(self):
        """別肩書追加では ContactFieldConfidence は作成されない（仕様書 §10.12）。"""
        from contacts.models import ContactFieldConfidence

        cfc_before = ContactFieldConfidence.objects.count()
        data = self._base_post_data()
        data["full_name"] = "新規 CFC 不要"
        # salutation_name を埋めて Contact.save() の自動補完を回避する（補完されると
        # salutation_name の CFC が 1 件作られる）。Phase D で Form が必須化 + is_manual セット。
        data["salutation_name"] = "テスト 様"
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 302)
        cfc_after = ContactFieldConfidence.objects.count()
        self.assertEqual(cfc_after, cfc_before)

    def test_post_primary_contact_of_person_unchanged(self):
        """別肩書追加で元の primary_contact は影響を受けない。"""
        data = self._base_post_data()
        data["full_name"] = "別肩書"
        self.client.post(self._url(), data=data)

        self.person.refresh_from_db()
        self.assertEqual(self.person.primary_contact_id, self.primary.pk)
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.status, Contact.Status.PRIMARY)

    def test_post_invalid_redisplays_form(self):
        """max_length 違反データ → 200 でフォーム再表示、Contact 未作成。"""
        data = self._base_post_data()
        data["full_name"] = "x" * 300  # max_length=255 違反
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("full_name", resp.context["form"].errors)
        self.assertFalse(
            Contact.objects.filter(full_name="x" * 300).exists()
        )

    def test_post_blank_salutation_invalid(self):
        """salutation_name 空 → 200 でフォーム再表示、Contact 未作成（Phase F1 必須化）。"""
        data = self._base_post_data()
        data["full_name"] = "宛名なし太郎"
        data["salutation_name"] = ""  # 必須化により空はエラー
        contact_count_before = Contact.objects.count()
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("salutation_name", resp.context["form"].errors)
        self.assertEqual(Contact.objects.count(), contact_count_before)

    def test_post_to_archived_returns_404(self):
        """archived Person への POST も 404（dispatch ガードが POST にも効く）。"""
        self.person.status = Person.Status.ARCHIVED
        self.person.save(update_fields=["status", "updated_at"])
        data = self._base_post_data()
        data["full_name"] = "X"
        before = Contact.objects.count()
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(Contact.objects.count(), before)


class PersonTransferContactsToTests(TestCase):
    """Person.transfer_contacts_to() の単体テスト（D-4d-1 第 4 弾 §6-C）。

    merge_reason が list[str]（MultipleChoiceField 化）に変更されたことに伴い、
    元 primary の遷移先 status 判定が `== ADDITIONAL_ROLE` → `ADDITIONAL_ROLE in list`
    に変わった。本テスト群はその挙動を担保する。
    """

    def setUp(self):
        self.surviving = Person.objects.create()
        Contact.objects.create(
            person=self.surviving,
            status=Contact.Status.PRIMARY,
            full_name="surviving",
        )
        self.merged = Person.objects.create()
        self.merged_primary = Contact.objects.create(
            person=self.merged,
            status=Contact.Status.PRIMARY,
            full_name="merged",
        )

    def _run_transfer(self, merge_reason):
        with transaction.atomic():
            self.merged.transfer_contacts_to(self.surviving, merge_reason)
        self.merged_primary.refresh_from_db()

    def test_additional_role_only_sets_primary_active(self):
        """ADDITIONAL_ROLE 単独 → 元 primary は ACTIVE に遷移。"""
        self._run_transfer([DuplicateMergeReason.ADDITIONAL_ROLE.value])
        self.assertEqual(self.merged_primary.status, Contact.Status.ACTIVE)

    def test_non_additional_role_sets_primary_inactive(self):
        """ADDITIONAL_ROLE を含まない単独 → 元 primary は INACTIVE に遷移。"""
        self._run_transfer([DuplicateMergeReason.SAME_CARD.value])
        self.assertEqual(self.merged_primary.status, Contact.Status.INACTIVE)

    def test_additional_role_in_multiple_sets_primary_active(self):
        """複数値で ADDITIONAL_ROLE を含む → 元 primary は ACTIVE に遷移。"""
        self._run_transfer(
            [
                DuplicateMergeReason.TRANSFER.value,
                DuplicateMergeReason.ADDITIONAL_ROLE.value,
            ]
        )
        self.assertEqual(self.merged_primary.status, Contact.Status.ACTIVE)

    def test_multiple_without_additional_role_sets_primary_inactive(self):
        """複数値で ADDITIONAL_ROLE を含まない → 元 primary は INACTIVE に遷移。"""
        self._run_transfer(
            [
                DuplicateMergeReason.TRANSFER.value,
                DuplicateMergeReason.PROMOTION.value,
            ]
        )
        self.assertEqual(self.merged_primary.status, Contact.Status.INACTIVE)


@override_settings(DEBUG=True)
class PersonDetailDebugUidTests(TestCase):
    """DEBUG=True 時の Person UID コピペ表示（D-4d-1 第 6 弾 §2-1）。

    person_detail_orphan / merged / archived の 3 テンプレを覆う。active Person は
    ContactDetailView へ redirect されるため本クラスでは扱わない（contacts 側でカバー）。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="person_dbg", password="dummy")
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self, person):
        return reverse("persons:person_detail", kwargs={"pk": person.pk})

    def test_orphan_template_shows_person_uid(self):
        person = Person.objects.create()  # primary_contact=NULL → orphan テンプレ
        resp = self.client.get(self._url(person))
        self.assertContains(resp, 'class="app-debug-uid"')
        self.assertContains(resp, "Person UID:")
        self.assertContains(resp, str(person.id))

    def test_merged_template_shows_person_uid(self):
        surviving = Person.objects.create()
        merged = Person.objects.create()
        merged.status = Person.Status.MERGED
        merged.merged_into = surviving
        merged.save(update_fields=["status", "merged_into", "updated_at"])
        resp = self.client.get(self._url(merged))
        self.assertContains(resp, 'class="app-debug-uid"')
        self.assertContains(resp, str(merged.id))

    def test_archived_template_shows_person_uid(self):
        archived = Person.objects.create()
        archived.status = Person.Status.ARCHIVED
        archived.save(update_fields=["status", "updated_at"])
        resp = self.client.get(self._url(archived))
        self.assertContains(resp, 'class="app-debug-uid"')
        self.assertContains(resp, str(archived.id))


class PersonDetailDebugUidOffTests(TestCase):
    """DEBUG=False 時に Person UID コピペ表示が出ないこと（orphan 1 シナリオで担保）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="person_dbg_off", password="dummy")
        self.client = Client()
        self.client.force_login(self.user)

    @override_settings(DEBUG=False)
    def test_orphan_template_hides_person_uid(self):
        person = Person.objects.create()
        resp = self.client.get(
            reverse("persons:person_detail", kwargs={"pk": person.pk})
        )
        self.assertNotContains(resp, "Person UID:")
        self.assertNotContains(resp, "app-debug-uid")


# ======================================================================
# Phase F1：9 番別肩書追加の ContactSns 連携 + テンプレ D/D2/E 改修（§11.6.7 / §1.4）
# ======================================================================


class PersonAddAdditionalRoleSnsTests(TestCase):
    """9 番 PersonAddAdditionalRoleView の ContactSns 引き継ぎ（§11.6.7 / §1.8.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="add_sns_user", password="x")
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P",
            salutation_name="P 様",
            salutation_name_is_manual=True,
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        ContactSns.objects.create(
            contact=self.primary, sns_type="twitter", sns_id="@p"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self):
        return reverse(
            "persons:person_add_additional_role", kwargs={"pk": self.person.pk}
        )

    def test_get_prefills_primary_sns(self):
        """GET 時に primary の SNS が初期表示行として引き継がれる。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        formset = resp.context["sns_formset"]
        self.assertEqual(formset.total_form_count(), 1)
        self.assertContains(resp, "@p")

    def test_post_creates_sns_on_new_contact(self):
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "別肩書"
        data["salutation_name"] = "別肩書 様"
        # 引き継いだ 1 行 + 追加 1 行を submit（id なし＝新規 Contact に作成）
        data.update(_sns_management_form(2, initial=0))
        data["sns-0-sns_type"] = "twitter"
        data["sns-0-sns_id"] = "@p"
        data["sns-1-sns_type"] = "github"
        data["sns-1-sns_id"] = "newgh"
        resp = self.client.post(self._url(), data=data)
        self.assertEqual(resp.status_code, 302)
        new_contact = Contact.objects.get(
            person=self.person, full_name="別肩書"
        )
        self.assertEqual(new_contact.sns_accounts.count(), 2)
        # primary 側は変更されない
        self.assertEqual(self.primary.sns_accounts.count(), 1)


class PersonAddAdditionalRoleRenderTests(TestCase):
    """9 番テンプレの D/D2/E 相当改修の回帰（§1.4 / §1.8.3）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="add_render_user", password="x")
        self.person = Person.objects.create()
        self.primary = Contact.objects.create(
            person=self.person, status=Contact.Status.PRIMARY, full_name="P"
        )
        self.person.primary_contact = self.primary
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self):
        return reverse(
            "persons:person_add_additional_role", kwargs={"pk": self.person.pk}
        )

    def test_renders_all_updatable_fields(self):
        """UPDATABLE_FIELDS 全 31 フィールドの input/select id が描画される。"""
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        for field_name in Contact.UPDATABLE_FIELDS:
            self.assertContains(resp, f'id="id_{field_name}"')

    def test_does_not_render_address_field(self):
        """address は UPDATABLE_FIELDS 外（Phase E）。フォームフィールドとして描画しない。"""
        resp = self.client.get(self._url())
        self.assertNotContains(resp, 'id="id_address"')

    def test_has_app_form_label_and_sns_container(self):
        resp = self.client.get(self._url())
        self.assertContains(resp, "app-form__label")
        self.assertContains(resp, "js-sns-formset-container")
