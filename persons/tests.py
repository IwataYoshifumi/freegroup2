"""persons アプリの View 層テスト（v1.4.2 §11.4 / §11.5）。"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contacts.models import Contact
from duplicates.models import PersonMergeLog
from persons.models import Person


User = get_user_model()


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
            company="Acme Corp",
            department="Sales",
            title="Manager",
            email="alice@acme.example",
            phone="03-1234-5678",
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

    def test_search_tel_or_phone_mobile_fax(self):
        """tel は primary_contact の phone / mobile / fax の OR 一致。"""
        p_mobile, _ = self._make_active_with_primary(
            full_name="MobOnly", mobile="090-1111-2222"
        )
        p_fax, _ = self._make_active_with_primary(
            full_name="FaxOnly", fax="06-9999-8888"
        )

        resp = self.client.get(self.url, {"tel": "1234"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(self.person_a.id, ids)
        self.assertNotIn(p_mobile.id, ids)
        self.assertNotIn(p_fax.id, ids)

        resp = self.client.get(self.url, {"tel": "090-1111"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(p_mobile.id, ids)
        self.assertNotIn(self.person_a.id, ids)

        resp = self.client.get(self.url, {"tel": "06-9999"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(p_fax.id, ids)
        self.assertNotIn(self.person_a.id, ids)

    def test_search_and_name_company(self):
        """name と company の AND 検索。"""
        self._make_active_with_primary(
            full_name="Alice Tanaka", company="Wonder Corp"
        )
        self._make_active_with_primary(
            full_name="Bob Smith", company="Acme Industries"
        )
        p_both, _ = self._make_active_with_primary(
            full_name="Alice Brown", company="Acme Group"
        )

        resp = self.client.get(self.url, {"name": "Alice", "company": "Acme"})
        ids = [p.id for p in resp.context["persons"]]
        self.assertIn(self.person_a.id, ids)
        self.assertIn(p_both.id, ids)
        self.assertEqual(len(ids), 2)

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
        self.assertIn("(primary_contact 未設定)", body)


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
        self.assertIn("archived", resp.content.decode())

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
            company="P-co",
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

        return {f: "" for f in _Contact.UPDATABLE_FIELDS}

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
        data["company"] = "別会社"

        resp = self.client.post(self._url(), data=data)

        self.assertEqual(resp.status_code, 302)
        new_contact = Contact.objects.filter(
            person=self.person, full_name="別肩書 太郎"
        ).first()
        self.assertIsNotNone(new_contact)
        self.assertEqual(new_contact.status, Contact.Status.ACTIVE)
        self.assertEqual(new_contact.company, "別会社")
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
