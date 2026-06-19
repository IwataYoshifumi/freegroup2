"""card_list の v1.7 UI 改善：ステータス未指定＝全件（旧 business_card デフォルト廃止）と
ページネーション 50 件/ページの検証（views の最小変更を担保する）。"""

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import BusinessCard, OriginalImage
from cards.views import CardListView
from contacts.models import Contact
from persons.models import Person

User = get_user_model()


class CardListOcrEmptyAllTests(TestCase):
    """ocr_result 未指定 = 全ステータス（全件）。指定時は従来どおり絞れる。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_empty_all_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        # OCR待ち（ocr_result=None）/ 名刺 / 名刺ではない の 3 状態を用意。
        self.bc_pending = BusinessCard.objects.create(
            original_image=self.original, card_index=0,
            ocr_status=BusinessCard.OcrStatus.PENDING,
        )
        self.bc_card = BusinessCard.objects.create(
            original_image=self.original, card_index=1,
            ocr_status=BusinessCard.OcrStatus.DONE,
            ocr_result=BusinessCard.OcrResult.BUSINESS_CARD,
        )
        self.bc_notcard = BusinessCard.objects.create(
            original_image=self.original, card_index=2,
            ocr_status=BusinessCard.OcrStatus.DONE,
            ocr_result=BusinessCard.OcrResult.NOT_BUSINESS_CARD,
        )

    def _ids(self, query):
        view = CardListView()
        view.request = type("Req", (), {"user": self.user, "GET": query})()
        return set(view.get_queryset().values_list("pk", flat=True))

    def test_empty_param_returns_all_statuses(self):
        """ocr_result 空 → 3 件すべて（旧 business_card デフォルトを廃止）。"""
        ids = self._ids(QueryDict(""))
        self.assertEqual(
            ids, {self.bc_pending.pk, self.bc_card.pk, self.bc_notcard.pk}
        )

    def test_selected_business_card_only(self):
        """ocr_result=business_card → 名刺のみ（従来どおりの絞り込み）。"""
        q = QueryDict(mutable=True)
        q.appendlist("ocr_result", BusinessCard.OcrResult.BUSINESS_CARD)
        self.assertEqual(self._ids(q), {self.bc_card.pk})

    def test_selected_pending_only(self):
        """仮想値 _pending → OCR待ち（ocr_status=PENDING）のみ。"""
        q = QueryDict(mutable=True)
        q.appendlist("ocr_result", CardListView._OCR_FILTER_PENDING)
        self.assertEqual(self._ids(q), {self.bc_pending.pk})


class CardListPaginationConfigTests(TestCase):
    def test_paginate_by_is_50(self):
        self.assertEqual(CardListView.paginate_by, 50)


class CardListRenderTests(TestCase):
    """テンプレートが実描画で通ること（生コメントバグ・collapsible・pagination include）。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_render_user", password="dummy"
        )

    def test_card_list_renders(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("cards:card_list"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # ボタンUI・折りたたみ・ソート/件数 UI が描画される。
        self.assertIn("js-ocr-toggle", html)
        self.assertIn("app-collapsible", html)
        self.assertIn("js-card-sort-control", html)
        self.assertIn('name="per_page"', html)
        # 取り込み期間クイックボタンが 7 個（今日/今週/先週/今月/先月/2か月/今年）。
        self.assertEqual(html.count('class="app-btn app-btn--secondary app-btn--sm js-date-quick"'), 7)
        for label in ("今日", "今週", "先週", "今月", "先月", "2か月", "今年"):
            self.assertIn(label, html)
        # 期間クリアボタンがある。
        self.assertIn("js-date-clear", html)
        # 生コメントバグの再発防止：コメント本文が画面に漏れていないこと。
        self.assertNotIn("CLAUDE.md §7", html)


class CardListSortTests(TestCase):
    """多段ソート（?sort=key,-key）。既定は作成日時降順、指定で並び替え、不正キーは無視。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_sort_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        # 作成順は A→B→C（created_at を明示的に distinct 化）。組織は Aaa(B)<Bbb(A)<Ccc(C)。
        specs = [("A", "Bbb"), ("B", "Aaa"), ("C", "Ccc")]
        self.cards = {}
        base = timezone.now()
        for i, (name, org) in enumerate(specs):
            bc = BusinessCard.objects.create(
                original_image=self.original, card_index=i,
                ocr_status=BusinessCard.OcrStatus.DONE,
                ocr_result=BusinessCard.OcrResult.BUSINESS_CARD,
            )
            person = Person.objects.create()
            contact = Contact.objects.create(
                person=person, status=Contact.Status.PRIMARY, business_card=bc,
                full_name=name, phonetic_name=name, organization=org, title=name,
            )
            person.primary_contact = contact
            person.save(update_fields=["primary_contact", "updated_at"])
            self.cards[name] = bc
        # created_at を A<B<C に確定（auto_now_add は create 値を無視するため update で設定）。
        for name, minutes in (("A", 3), ("B", 2), ("C", 1)):
            BusinessCard.objects.filter(pk=self.cards[name].pk).update(
                created_at=base - timedelta(minutes=minutes)
            )

    def _ids(self, sort=None):
        view = CardListView()
        query = QueryDict(mutable=True)
        if sort is not None:
            query["sort"] = sort
        view.request = type("Req", (), {"user": self.user, "GET": query})()
        return list(view.get_queryset().values_list("pk", flat=True))

    def test_default_is_created_desc(self):
        self.assertEqual(
            self._ids(), [self.cards["C"].pk, self.cards["B"].pk, self.cards["A"].pk]
        )

    def test_sort_company_asc(self):
        # 組織昇順：Aaa(B) → Bbb(A) → Ccc(C)
        self.assertEqual(
            self._ids("company"),
            [self.cards["B"].pk, self.cards["A"].pk, self.cards["C"].pk],
        )

    def test_sort_created_asc(self):
        self.assertEqual(
            self._ids("created"),
            [self.cards["A"].pk, self.cards["B"].pk, self.cards["C"].pk],
        )

    def test_invalid_key_falls_back_to_default(self):
        self.assertEqual(
            self._ids("bogus"),
            [self.cards["C"].pk, self.cards["B"].pk, self.cards["A"].pk],
        )


class CardListPerPageResolveTests(TestCase):
    """per_page は 50/100/200 のみ。無指定・不正・範囲外は 50。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_perpage_user", password="dummy"
        )

    def _resolve(self, **params):
        view = CardListView()
        query = QueryDict(mutable=True)
        for k, v in params.items():
            query[k] = v
        view.request = type("Req", (), {"user": self.user, "GET": query})()
        return view.get_paginate_by(None)

    def test_default_50(self):
        self.assertEqual(self._resolve(), 50)

    def test_100(self):
        self.assertEqual(self._resolve(per_page="100"), 100)

    def test_200(self):
        self.assertEqual(self._resolve(per_page="200"), 200)

    def test_non_int_falls_back_50(self):
        self.assertEqual(self._resolve(per_page="abc"), 50)

    def test_out_of_range_falls_back_50(self):
        self.assertEqual(self._resolve(per_page="30"), 50)


class CardListUploadedDateRangeTests(TestCase):
    """取り込み期間フィルタ：元画像 created_at（アップロード日）で範囲絞り。片側/両方/空。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_daterange_user", password="dummy"
        )
        # 3 つの元画像をアップロード日 06-01 / 06-10 / 06-20 に確定し、各に BC を 1 枚。
        self.bc = {}
        for day in (1, 10, 20):
            original = OriginalImage.objects.create(
                user=self.user, status=OriginalImage.STATUS_EXTRACTED
            )
            OriginalImage.objects.filter(pk=original.pk).update(
                created_at=timezone.make_aware(datetime(2026, 6, day, 12, 0))
            )
            bc = BusinessCard.objects.create(
                original_image=original, card_index=0,
                ocr_status=BusinessCard.OcrStatus.DONE,
                ocr_result=BusinessCard.OcrResult.BUSINESS_CARD,
            )
            self.bc[day] = bc

    def _ids(self, **params):
        view = CardListView()
        query = QueryDict(mutable=True)
        for k, v in params.items():
            query[k] = v
        view.request = type("Req", (), {"user": self.user, "GET": query})()
        return set(view.get_queryset().values_list("pk", flat=True))

    def test_empty_returns_all(self):
        self.assertEqual(
            self._ids(), {self.bc[1].pk, self.bc[10].pk, self.bc[20].pk}
        )

    def test_after_only(self):
        # 06-10 以降（両端含む）→ 06-10, 06-20
        self.assertEqual(
            self._ids(uploaded_after="2026-06-10"),
            {self.bc[10].pk, self.bc[20].pk},
        )

    def test_before_only(self):
        # 06-10 以前（両端含む）→ 06-01, 06-10
        self.assertEqual(
            self._ids(uploaded_before="2026-06-10"),
            {self.bc[1].pk, self.bc[10].pk},
        )

    def test_both_bounds(self):
        # 06-05 〜 06-15 → 06-10 のみ
        self.assertEqual(
            self._ids(uploaded_after="2026-06-05", uploaded_before="2026-06-15"),
            {self.bc[10].pk},
        )

    def test_invalid_date_ignored(self):
        # 不正な日付は無視＝全件（フィルタなし）。
        self.assertEqual(
            self._ids(uploaded_after="not-a-date"),
            {self.bc[1].pk, self.bc[10].pk, self.bc[20].pk},
        )

    def test_combined_with_status_filter(self):
        # 期間 + ステータス（business_card）併用：06-10 以降の business_card → 06-10, 06-20。
        self.assertEqual(
            self._ids(uploaded_after="2026-06-10", ocr_result="business_card"),
            {self.bc[10].pk, self.bc[20].pk},
        )


class CardListPaginationRenderTests(TestCase):
    """件数超でページ送りが出る（is_paginated）。per_page で1ページ件数が変わる。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_pagi_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        for i in range(51):
            BusinessCard.objects.create(
                original_image=self.original, card_index=i,
                ocr_status=BusinessCard.OcrStatus.DONE,
                ocr_result=BusinessCard.OcrResult.BUSINESS_CARD,
            )

    def test_paginated_with_50(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("cards:card_list") + "?per_page=50")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_paginated"])
        self.assertEqual(len(resp.context["page_obj"]), 50)

    def test_not_paginated_with_100(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("cards:card_list") + "?per_page=100")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_paginated"])
        self.assertEqual(len(resp.context["page_obj"]), 51)

