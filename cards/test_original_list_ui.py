"""元画像一覧（OriginalListView）の v1.7 UI 刷新：ステータスフィルタ（空→全件/複数）・
取り込み期間（created_at）・多段ソート・表示件数 20/50/100・ページ送り・描画の検証。"""

from datetime import datetime

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import OriginalImage
from cards.views import OriginalListView
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


def _make_view(user, **params):
    view = OriginalListView()
    query = QueryDict(mutable=True)
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            for item in v:
                query.appendlist(k, item)
        else:
            query[k] = v
    view.request = type("Req", (), {"user": user, "GET": query})()
    return view


class OriginalListStatusAndDateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="orig_ui_user", password="d")
        # status / detected_count / アップロード日 を変えた 3 件。
        specs = [
            ("o1", "pending", 5, 1),
            ("o2", "extracted", 1, 10),
            ("o3", "failed", 9, 20),
        ]
        self.o = {}
        for name, status, detected, day in specs:
            obj = OriginalImage.objects.create(
                user=self.user, status=status, detected_count=detected
            )
            OriginalImage.objects.filter(pk=obj.pk).update(
                created_at=timezone.make_aware(datetime(2026, 6, day, 12, 0))
            )
            self.o[name] = obj

    def _ids(self, **params):
        return set(_make_view(self.user, **params).get_queryset().values_list("pk", flat=True))

    def test_empty_status_returns_all(self):
        self.assertEqual(
            self._ids(), {self.o["o1"].pk, self.o["o2"].pk, self.o["o3"].pk}
        )

    def test_single_status(self):
        self.assertEqual(self._ids(status="extracted"), {self.o["o2"].pk})

    def test_group_attention_values(self):
        # 「要対処」グループの値（garbage, failed）。今回 failed のみ該当 → o3。
        self.assertEqual(self._ids(status=["garbage", "failed"]), {self.o["o3"].pk})

    def test_date_from(self):
        self.assertEqual(
            self._ids(date_from="2026-06-10"), {self.o["o2"].pk, self.o["o3"].pk}
        )

    def test_date_to(self):
        self.assertEqual(
            self._ids(date_to="2026-06-10"), {self.o["o1"].pk, self.o["o2"].pk}
        )


class OriginalListSortTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="orig_sort_user", password="d")
        specs = [("o1", "pending", 5, 1), ("o2", "extracted", 1, 10), ("o3", "failed", 9, 20)]
        self.o = {}
        for name, status, detected, day in specs:
            obj = OriginalImage.objects.create(
                user=self.user, status=status, detected_count=detected
            )
            OriginalImage.objects.filter(pk=obj.pk).update(
                created_at=timezone.make_aware(datetime(2026, 6, day, 12, 0))
            )
            self.o[name] = obj

    def _ids(self, sort=None):
        params = {} if sort is None else {"sort": sort}
        return list(_make_view(self.user, **params).get_queryset().values_list("pk", flat=True))

    def test_default_created_desc(self):
        self.assertEqual(self._ids(), [self.o["o3"].pk, self.o["o2"].pk, self.o["o1"].pk])

    def test_sort_detected_asc(self):
        # 検出枚数 昇順：1(o2), 5(o1), 9(o3)
        self.assertEqual(self._ids("detected"), [self.o["o2"].pk, self.o["o1"].pk, self.o["o3"].pk])

    def test_sort_detected_desc(self):
        self.assertEqual(self._ids("-detected"), [self.o["o3"].pk, self.o["o1"].pk, self.o["o2"].pk])

    def test_sort_status_asc(self):
        # status 昇順（文字列）：extracted, failed, pending → o2, o3, o1
        self.assertEqual(self._ids("status"), [self.o["o2"].pk, self.o["o3"].pk, self.o["o1"].pk])

    def test_invalid_key_falls_back_to_default(self):
        self.assertEqual(self._ids("bogus"), [self.o["o3"].pk, self.o["o2"].pk, self.o["o1"].pk])

    def test_uploader_key_removed_and_ignored(self):
        # uploader は許可リストから削除済み → 不正キー扱いで無視＝既定（作成日時降順）。
        from cards.views import ORIGINAL_LIST_SORT_FIELD_MAP

        self.assertNotIn("uploader", ORIGINAL_LIST_SORT_FIELD_MAP)
        self.assertEqual(self._ids("uploader"), [self.o["o3"].pk, self.o["o2"].pk, self.o["o1"].pk])


class OriginalListPerPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="orig_pp_user", password="d")

    def _pp(self, **params):
        return _make_view(self.user, **params).get_paginate_by(None)

    def test_default_20(self):
        self.assertEqual(self._pp(), 20)

    def test_50(self):
        self.assertEqual(self._pp(per_page="50"), 50)

    def test_100(self):
        self.assertEqual(self._pp(per_page="100"), 100)

    def test_non_int_falls_back_20(self):
        self.assertEqual(self._pp(per_page="abc"), 20)

    def test_out_of_range_falls_back_20(self):
        self.assertEqual(self._pp(per_page="30"), 20)


class OriginalListRenderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="orig_render_user", password="d")

    def test_renders(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("originals:original_list"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("js-status-toggle", html)
        self.assertIn("js-sort-control", html)
        self.assertIn('name="per_page"', html)
        self.assertEqual(html.count('class="app-btn app-btn--secondary app-btn--sm js-date-quick"'), 7)
        self.assertIn("js-date-clear", html)
        # ソート選択肢から uploader を削除（表示列「アップロード者」の th は残る）。
        self.assertNotIn('value="uploader"', html)
        # 生コメントが画面に漏れていないこと。
        self.assertNotIn("CLAUDE.md §7", html)
        self.assertNotIn("endcomment", html)


class OriginalListUploaderDisplayTests(TestCase):
    """アップロード者列：Person 紐づけあり→氏名、なし→username フォールバック。"""

    def test_linked_user_shows_person_name(self):
        user = User.objects.create_superuser(username="alice_uploader", password="d")
        person = Person.objects.create()
        contact = Contact.objects.create(
            person=person, status=Contact.Status.PRIMARY, full_name="山田太郎表示名"
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        user.person = person
        user.save(update_fields=["person"])
        OriginalImage.objects.create(user=user, status="extracted")

        self.client.force_login(user)
        html = self.client.get(reverse("originals:original_list")).content.decode()
        # Person 氏名はこのアップロード者セルにしか出ない（トップバーは username 表示）。
        self.assertIn("山田太郎表示名", html)

    def test_unlinked_user_shows_username(self):
        user = User.objects.create_superuser(username="bob_unlinked_zzz", password="d")
        self.assertIsNone(user.person)
        OriginalImage.objects.create(user=user, status="extracted")

        self.client.force_login(user)
        html = self.client.get(reverse("originals:original_list")).content.decode()
        self.assertIn("bob_unlinked_zzz", html)


class OriginalListPaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="orig_pagi_user", password="d")
        for _ in range(21):
            OriginalImage.objects.create(user=self.user, status="extracted")

    def test_paginated_with_20(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("originals:original_list") + "?per_page=20")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_paginated"])
        self.assertEqual(len(resp.context["page_obj"]), 20)

    def test_not_paginated_with_50(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("originals:original_list") + "?per_page=50")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_paginated"])
        self.assertEqual(len(resp.context["page_obj"]), 21)
