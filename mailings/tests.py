"""mailings アプリ Phase 1c-α テスト（個別追加・個別削除、仕様書 §3 / §10 / §11.4）。

snapshot 方式・PRG パターン・?restore=1 判定・confirm 空フォールバック・凍結ガード・
退会済みバッジ・表示件数統一 UI の検証。Phase 1b までの既存 API（add-member /
remove-member / freeze 等）は本ファイルで触らず ε.6 時点の挙動を維持する前提。

Phase 1c-β-1（仕様書 rev5 §4.1 / §4.2 / §4.3）：拡張集合演算サービス関数と
preview-v2 API のテスト（クラス TagConditionsExtractionTests / PreviewV2ApiTests）。
"""

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contacts.models import Contact
from mailings.models import MailingList, MailingListMember
from mailings.services.tag_extraction import (
    extract_persons_by_tag_conditions,
    extract_persons_by_tags,
)
from persons.models import Person
from tags.models import Tag, TagAssignment, TagCategory

User = get_user_model()


class _MemberEditTestBase(TestCase):
    """個別追加・個別削除テストの共通基底。"""

    def setUp(self):
        self.user = User.objects.create_user(username="me_test", password="x")
        self.client = Client()
        self.client.force_login(self.user)

    def _make_person(
        self,
        full_name="Alice",
        *,
        status="active",
        is_unsubscribed=False,
        organization="",
        email=None,
    ):
        person = Person.objects.create(status=status, is_unsubscribed=is_unsubscribed)
        contact = Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            organization=organization,
            email=email if email is not None else f"{full_name.lower()}@example.com",
            created_by=self.user,
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        return person

    def _make_list(self, name="L1", *, frozen=False, archived=False):
        ml = MailingList.objects.create(
            name=name, created_by=self.user, is_archived=archived
        )
        if frozen:
            ml.members_frozen_at = timezone.now()
            ml.save(update_fields=["members_frozen_at", "updated_at"])
        return ml

    def _add_member(self, mailing_list, person):
        return MailingListMember.objects.create(
            mailing_list=mailing_list, person=person, added_by=self.user
        )

    def _put_session(self, key, value):
        session = self.client.session
        session[key] = value
        session.save()


# ======================================================================
# 個別追加 選択画面（MemberAddView）GET
# ======================================================================


class MemberAddViewGetTests(_MemberEditTestBase):
    def test_get_200_active_list(self):
        ml = self._make_list()
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_redirects(self):
        ml = self._make_list()
        c = Client()
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = c.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_excludes_existing_members(self):
        """個別追加の母集合は未所属の Person に限定（§6.7、重複追加防止）。"""
        ml = self._make_list()
        member = self._make_person("Member")
        other = self._make_person("Other")
        self._add_member(ml, member)
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        cands = list(resp.context["candidates"])
        self.assertIn(other, cands)
        self.assertNotIn(member, cands)

    def test_includes_unsubscribed_active_person(self):
        """退会者も追加候補に含まれる（§6.7、is_unsubscribed フィルタしない）。"""
        ml = self._make_list()
        p = self._make_person("Unsub", is_unsubscribed=True)
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertIn(p, list(resp.context["candidates"]))
        self.assertIn(">退会済み</span>", resp.content.decode("utf-8"))

    def test_excludes_archived_person(self):
        """個別追加の母集合は active のみ（§6.7）。"""
        ml = self._make_list()
        p = self._make_person("Arch", status="archived")
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertNotIn(p, list(resp.context["candidates"]))

    def test_status_query_string_cannot_inject_archived(self):
        """URL 直手入力で ?searched=1&status=archived を仕込んでも archived は出ない（二重防衛）。"""
        ml = self._make_list()
        p_arch = self._make_person("Arch", status="archived")
        url = (
            reverse("mailings:list_member_add", args=[ml.pk])
            + "?searched=1&status=archived"
        )
        resp = self.client.get(url)
        self.assertNotIn(p_arch, list(resp.context["candidates"]))

    def test_frozen_list_returns_409(self):
        ml = self._make_list(frozen=True)
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 409)

    def test_archived_list_returns_404(self):
        ml = self._make_list(archived=True)
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_get_without_restore_clears_session(self):
        """restore=1 なし GET は session を破棄（§6.4、放棄選択の残留防止）。"""
        ml = self._make_list()
        p = self._make_person("Alice")
        key = f"mailing_list_{ml.pk}_add_selection"
        self._put_session(key, [str(p.pk)])
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(key, self.client.session)

    def test_get_with_restore_keeps_session(self):
        """restore=1 GET は session を保持（§6.4、確認画面から戻る用）。"""
        ml = self._make_list()
        p = self._make_person("Alice")
        key = f"mailing_list_{ml.pk}_add_selection"
        self._put_session(key, [str(p.pk)])
        url = reverse("mailings:list_member_add", args=[ml.pk]) + "?restore=1"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.session.get(key), [str(p.pk)])


# ======================================================================
# 個別追加 選択画面 POST
# ======================================================================


class MemberAddViewPostTests(_MemberEditTestBase):
    def test_post_saves_session_and_redirects_to_confirm(self):
        ml = self._make_list()
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob")
        url = reverse("mailings:list_member_add", args=[ml.pk])
        confirm = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        resp = self.client.post(url, {"person_ids": [str(p1.pk), str(p2.pk)]})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(confirm, resp["Location"])
        self.assertEqual(
            set(self.client.session.get(f"mailing_list_{ml.pk}_add_selection") or []),
            {str(p1.pk), str(p2.pk)},
        )

    def test_post_empty_selection_redirects_to_selection(self):
        ml = self._make_list()
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.post(url, {"person_ids": []})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(url, resp["Location"])

    def test_post_invalid_uuids_are_dropped(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.post(
            url, {"person_ids": [str(p.pk), "garbage", "12345"]}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self.client.session.get(f"mailing_list_{ml.pk}_add_selection"),
            [str(p.pk)],
        )

    def test_post_frozen_returns_409(self):
        ml = self._make_list(frozen=True)
        p = self._make_person("Alice")
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.post(url, {"person_ids": [str(p.pk)]})
        self.assertEqual(resp.status_code, 409)


# ======================================================================
# 個別削除 選択画面（MemberRemoveView）GET
# ======================================================================


class MemberRemoveViewGetTests(_MemberEditTestBase):
    def test_get_200(self):
        ml = self._make_list()
        url = reverse("mailings:list_member_remove", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_only_members_in_candidates(self):
        ml = self._make_list()
        m = self._make_person("Member")
        non = self._make_person("Non")
        self._add_member(ml, m)
        url = reverse("mailings:list_member_remove", args=[ml.pk])
        resp = self.client.get(url)
        cands = list(resp.context["candidates"])
        self.assertIn(m, cands)
        self.assertNotIn(non, cands)

    def test_archived_member_still_listed(self):
        """退会・archived 化したメンバーも削除候補に含まれる（§6.7、status 不問）。"""
        ml = self._make_list()
        p = self._make_person("Arch", status="archived")
        self._add_member(ml, p)
        url = reverse("mailings:list_member_remove", args=[ml.pk])
        resp = self.client.get(url)
        self.assertIn(p, list(resp.context["candidates"]))

    def test_unsubscribed_member_listed_with_badge(self):
        ml = self._make_list()
        p = self._make_person("Unsub", is_unsubscribed=True)
        self._add_member(ml, p)
        url = reverse("mailings:list_member_remove", args=[ml.pk])
        resp = self.client.get(url)
        self.assertIn(p, list(resp.context["candidates"]))
        self.assertIn(">退会済み</span>", resp.content.decode("utf-8"))

    def test_text_filter_applied(self):
        ml = self._make_list()
        p1 = self._make_person("Alice", organization="ACME")
        p2 = self._make_person("Bob", organization="Globex")
        self._add_member(ml, p1)
        self._add_member(ml, p2)
        url = (
            reverse("mailings:list_member_remove", args=[ml.pk])
            + "?searched=1&organization=ACME"
        )
        resp = self.client.get(url)
        cands = list(resp.context["candidates"])
        self.assertIn(p1, cands)
        self.assertNotIn(p2, cands)

    def test_frozen_returns_409(self):
        ml = self._make_list(frozen=True)
        url = reverse("mailings:list_member_remove", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 409)


# ======================================================================
# 確認画面 GET
# ======================================================================


class MemberAddConfirmViewTests(_MemberEditTestBase):
    def test_renders_snapshot_persons(self):
        ml = self._make_list()
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob")
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", [str(p1.pk), str(p2.pk)]
        )
        url = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            {p.pk for p in resp.context["persons"]}, {p1.pk, p2.pk}
        )

    def test_empty_session_redirects_to_selection(self):
        """直叩き / 期限切れフォールバック：選択画面へ 302（§6.5）。"""
        ml = self._make_list()
        url = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        sel = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(sel, resp["Location"])

    def test_frozen_returns_409(self):
        ml = self._make_list(frozen=True)
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", [str(uuid.uuid4())]
        )
        url = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 409)

    def test_back_to_selection_url_has_restore_param(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        self._put_session(f"mailing_list_{ml.pk}_add_selection", [str(p.pk)])
        url = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("restore=1", resp.context["back_to_selection_url"])


class MemberRemoveConfirmViewTests(_MemberEditTestBase):
    def test_renders_snapshot(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        self._add_member(ml, p)
        self._put_session(
            f"mailing_list_{ml.pk}_remove_selection", [str(p.pk)]
        )
        url = reverse("mailings:list_member_remove_confirm", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_empty_session_redirects(self):
        ml = self._make_list()
        url = reverse("mailings:list_member_remove_confirm", args=[ml.pk])
        sel = reverse("mailings:list_member_remove", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(sel, resp["Location"])


# ======================================================================
# 確定エンドポイント POST
# ======================================================================


class MemberAddCommitViewTests(_MemberEditTestBase):
    def test_post_creates_members_and_redirects_detail(self):
        ml = self._make_list()
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob")
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", [str(p1.pk), str(p2.pk)]
        )
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse("mailings:mailing_list_detail", args=[ml.pk]),
            resp["Location"],
        )
        self.assertEqual(
            MailingListMember.objects.filter(mailing_list=ml).count(), 2
        )

    def test_post_does_not_duplicate_existing_member(self):
        """ignore_conflicts により既メンバーは無視され IntegrityError にならない（§6.8）。"""
        ml = self._make_list()
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob")
        self._add_member(ml, p1)  # 既メンバー
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", [str(p1.pk), str(p2.pk)]
        )
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            MailingListMember.objects.filter(mailing_list=ml).count(), 2
        )

    def test_post_clears_session(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        key = f"mailing_list_{ml.pk}_add_selection"
        self._put_session(key, [str(p.pk)])
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        self.client.post(url)
        self.assertNotIn(key, self.client.session)

    def test_get_returns_405(self):
        ml = self._make_list()
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 405)

    def test_empty_session_redirects_to_selection(self):
        ml = self._make_list()
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse("mailings:list_member_add", args=[ml.pk]),
            resp["Location"],
        )

    def test_frozen_returns_409(self):
        ml = self._make_list(frozen=True)
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 409)

    def test_snapshot_integrity_after_status_change(self):
        """確認 → 確定の間に Person.status が変化（active→archived）しても snapshot 顔ぶれが登録される（§6.3）。"""
        ml = self._make_list()
        p = self._make_person("Alice")
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", [str(p.pk)]
        )
        p.status = Person.Status.ARCHIVED
        p.save(update_fields=["status", "updated_at"])
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=ml, person=p
            ).exists()
        )

    def test_prg_no_double_add_on_replay(self):
        """commit POST を 2 回飛ばしても二重追加されない（PRG パターン + session クリア）。"""
        ml = self._make_list()
        p = self._make_person("Alice")
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", [str(p.pk)]
        )
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        resp1 = self.client.post(url)
        self.assertEqual(resp1.status_code, 302)
        resp2 = self.client.post(url)
        self.assertEqual(resp2.status_code, 302)
        self.assertIn(
            reverse("mailings:list_member_add", args=[ml.pk]),
            resp2["Location"],
        )
        self.assertEqual(
            MailingListMember.objects.filter(
                mailing_list=ml, person=p
            ).count(),
            1,
        )

    def test_detail_count_matches_actual_after_commit(self):
        """確定後の詳細画面の表示件数は実カウントを反映する（§6.3 末尾、ignore_conflicts でズレない）。"""
        ml = self._make_list()
        # 既メンバー 1 件 + snapshot 2 件（うち 1 件は既存と重複）
        p_existing = self._make_person("Exist")
        self._add_member(ml, p_existing)
        p_new = self._make_person("New")
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection",
            [str(p_existing.pk), str(p_new.pk)],
        )
        url = reverse("mailings:list_member_commit_add", args=[ml.pk])
        self.client.post(url)
        detail = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(detail)
        self.assertIn("全 2 件", resp.content.decode("utf-8"))


class MemberRemoveCommitViewTests(_MemberEditTestBase):
    def test_post_deletes_specified_members(self):
        ml = self._make_list()
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob")
        self._add_member(ml, p1)
        self._add_member(ml, p2)
        self._put_session(
            f"mailing_list_{ml.pk}_remove_selection", [str(p1.pk)]
        )
        url = reverse("mailings:list_member_commit_remove", args=[ml.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            MailingListMember.objects.filter(
                mailing_list=ml, person=p1
            ).exists()
        )
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=ml, person=p2
            ).exists()
        )

    def test_does_not_affect_other_list_membership(self):
        """同じ Person が他リストにいても、対象リストのメンバーシップだけ消す。"""
        ml1 = self._make_list("L1")
        ml2 = self._make_list("L2")
        p = self._make_person("Alice")
        self._add_member(ml1, p)
        self._add_member(ml2, p)
        self._put_session(
            f"mailing_list_{ml1.pk}_remove_selection", [str(p.pk)]
        )
        url = reverse("mailings:list_member_commit_remove", args=[ml1.pk])
        self.client.post(url)
        self.assertFalse(
            MailingListMember.objects.filter(
                mailing_list=ml1, person=p
            ).exists()
        )
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=ml2, person=p
            ).exists()
        )

    def test_frozen_returns_409(self):
        ml = self._make_list(frozen=True)
        url = reverse("mailings:list_member_commit_remove", args=[ml.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 409)

    def test_clears_session_after_delete(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        self._add_member(ml, p)
        key = f"mailing_list_{ml.pk}_remove_selection"
        self._put_session(key, [str(p.pk)])
        url = reverse("mailings:list_member_commit_remove", args=[ml.pk])
        self.client.post(url)
        self.assertNotIn(key, self.client.session)


# ======================================================================
# 詳細画面：個別追加・個別削除ボタン / 退会済みバッジ / 表示件数 UI
# ======================================================================


class DetailViewMemberButtonsTests(_MemberEditTestBase):
    def test_active_list_shows_enabled_buttons(self):
        ml = self._make_list()
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        add_url = reverse("mailings:list_member_add", args=[ml.pk])
        remove_url = reverse("mailings:list_member_remove", args=[ml.pk])
        self.assertIn(add_url, body)
        self.assertIn(remove_url, body)

    def test_frozen_list_disables_buttons(self):
        ml = self._make_list(frozen=True)
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        add_url = reverse("mailings:list_member_add", args=[ml.pk])
        self.assertIn("凍結中のため編集できません", body)
        self.assertNotIn(f'href="{add_url}', body)

    def test_archived_list_disables_buttons(self):
        ml = self._make_list(archived=True)
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        add_url = reverse("mailings:list_member_add", args=[ml.pk])
        self.assertIn("アーカイブ済みのため編集できません", body)
        self.assertNotIn(f'href="{add_url}', body)


class DetailViewUnsubscribedBadgeTests(_MemberEditTestBase):
    def test_badge_shown_for_unsubscribed_member(self):
        ml = self._make_list()
        p = self._make_person("Alice", is_unsubscribed=True)
        self._add_member(ml, p)
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        # バッジ本体は <span class="app-status-badge app-status-badge--warning js-unsubscribed-badge" ...>退会済み</span>
        self.assertIn(">退会済み</span>", body)
        self.assertIn("js-unsubscribed-badge", body)

    def test_no_badge_for_normal_member(self):
        ml = self._make_list()
        p = self._make_person("Alice")  # is_unsubscribed=False（既定）
        self._add_member(ml, p)
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        # 列切替メニュー側にも「退会済みバッジ」ラベルや JS 内 selector 文字列が出るため
        # substring チェックは不適。バッジ本体タグ（>退会済み</span>）の有無で判定する。
        self.assertNotIn(">退会済み</span>", resp.content.decode("utf-8"))


class DisplayCountUITests(_MemberEditTestBase):
    def test_detail_shows_total_count_badge(self):
        ml = self._make_list()
        for i in range(3):
            self._add_member(ml, self._make_person(f"P{i}"))
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        self.assertIn("全 3 件", resp.content.decode("utf-8"))

    def test_detail_shows_limit_sub_badge_over_50(self):
        ml = self._make_list()
        for i in range(52):
            self._add_member(ml, self._make_person(f"P{i:03d}"))
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn("全 52 件", body)
        self.assertIn("先頭 50 件", body)

    def test_selection_screen_shows_count(self):
        ml = self._make_list()
        for i in range(4):
            self._make_person(f"Q{i}")
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        self.assertIn("全 4 件", resp.content.decode("utf-8"))


# ======================================================================
# session の頑強性（大量 ID）
# ======================================================================


class SessionLargeSnapshotTests(_MemberEditTestBase):
    def test_thousands_of_ids_survive_session_round_trip(self):
        """確認画面 GET は 数千件の snapshot でも 200 を返す（DB バックエンド前提）。"""
        ml = self._make_list()
        big_ids = [str(uuid.uuid4()) for _ in range(3000)]
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", big_ids
        )
        url = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)


# ======================================================================
# UI 改善（要望1：ソート機能）
# ======================================================================


class SortViewTests(_MemberEditTestBase):
    """サーバサイドソートの ORM 適用検証（5 画面共通の挙動）。"""

    def _setup_three_persons(self, ml=None):
        ml = ml or self._make_list()
        c = self._make_person("Charlie", organization="Globex", email="c@x")
        a = self._make_person("Alice", organization="Acme", email="a@x")
        b = self._make_person("Bob", organization="Initech", email="b@x")
        return ml, [a, b, c]

    def test_selection_add_default_sort_is_name_asc(self):
        ml, expected = self._setup_three_persons()
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        ids = [str(p.pk) for p in resp.context["candidates"]]
        self.assertEqual(ids, [str(p.pk) for p in expected])
        self.assertEqual(resp.context["current_sort"], "name")
        self.assertEqual(resp.context["current_dir"], "asc")

    def test_selection_add_sort_by_company_asc(self):
        ml, _ = self._setup_three_persons()
        url = reverse("mailings:list_member_add", args=[ml.pk]) + "?sort=company&dir=asc"
        resp = self.client.get(url)
        names = [p.primary_contact.full_name for p in resp.context["candidates"]]
        self.assertEqual(names, ["Alice", "Charlie", "Bob"])  # Acme / Globex / Initech

    def test_selection_add_sort_by_company_desc(self):
        ml, _ = self._setup_three_persons()
        url = reverse("mailings:list_member_add", args=[ml.pk]) + "?sort=company&dir=desc"
        resp = self.client.get(url)
        names = [p.primary_contact.full_name for p in resp.context["candidates"]]
        self.assertEqual(names, ["Bob", "Charlie", "Alice"])

    def test_invalid_sort_key_falls_back_to_default(self):
        ml, _ = self._setup_three_persons()
        url = reverse("mailings:list_member_add", args=[ml.pk]) + "?sort=BOGUS&dir=desc"
        resp = self.client.get(url)
        self.assertEqual(resp.context["current_sort"], "name")
        # dir も desc は維持される（dir 自体は有効値）
        self.assertEqual(resp.context["current_dir"], "desc")

    def test_invalid_dir_falls_back_to_default(self):
        ml, _ = self._setup_three_persons()
        url = reverse("mailings:list_member_add", args=[ml.pk]) + "?sort=name&dir=sideways"
        resp = self.client.get(url)
        self.assertEqual(resp.context["current_dir"], "asc")

    def test_sort_works_with_restore_param(self):
        """?restore=1 と ?sort=...&dir=... を併用しても両方機能する（仕様書 §2.5）。"""
        ml = self._make_list()
        p = self._make_person("Alice")
        self._put_session(f"mailing_list_{ml.pk}_add_selection", [str(p.pk)])
        url = (
            reverse("mailings:list_member_add", args=[ml.pk])
            + "?restore=1&sort=company&dir=desc"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.context["current_sort"], "company")
        self.assertEqual(resp.context["current_dir"], "desc")
        # session は保持される（restore=1 のため）
        self.assertEqual(
            self.client.session.get(f"mailing_list_{ml.pk}_add_selection"),
            [str(p.pk)],
        )

    def test_detail_view_sort_applies(self):
        ml = self._make_list()
        c = self._make_person("Charlie")
        a = self._make_person("Alice")
        b = self._make_person("Bob")
        self._add_member(ml, c)
        self._add_member(ml, a)
        self._add_member(ml, b)
        url = reverse("mailings:mailing_list_detail", args=[ml.pk]) + "?sort=name&dir=desc"
        resp = self.client.get(url)
        members = list(resp.context["members"])
        names = [m.person.primary_contact.full_name for m in members]
        self.assertEqual(names, ["Charlie", "Bob", "Alice"])
        self.assertEqual(resp.context["current_sort"], "name")
        self.assertEqual(resp.context["current_dir"], "desc")

    def test_confirm_view_sort_applies(self):
        ml = self._make_list()
        c = self._make_person("Charlie")
        a = self._make_person("Alice")
        b = self._make_person("Bob")
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection",
            [str(c.pk), str(a.pk), str(b.pk)],
        )
        url = (
            reverse("mailings:list_member_add_confirm", args=[ml.pk])
            + "?sort=name&dir=asc"
        )
        resp = self.client.get(url)
        names = [p.primary_contact.full_name for p in resp.context["persons"]]
        self.assertEqual(names, ["Alice", "Bob", "Charlie"])

    def test_sort_arrow_rendered_for_active_column(self):
        ml = self._make_list()
        self._make_person("Alice")
        url = reverse("mailings:list_member_add", args=[ml.pk]) + "?sort=company&dir=desc"
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        # company 列のヘッダに data-sort-key と bi-arrow-down アイコン
        self.assertIn('data-sort-key="company"', body)
        self.assertIn('bi-arrow-down', body)
        # name 列はアクティブでないのでアイコンは出ない
        # （body 内 bi-arrow-up は出ないはず）
        self.assertNotIn('bi-arrow-up', body)


# ======================================================================
# UI 改善（要望2：表示列切替）
# ======================================================================


class ColumnToggleTests(_MemberEditTestBase):
    """列切替 partial の埋め込みと data-col-key の整合性。

    クライアントサイド永続化（localStorage）と display 切替は JS 挙動のため pytest
    では検証しない（カバレッジは実機確認で担保）。ここでは partial が正しく埋め込まれ
    必要な data-col-key が <th>/<td> に揃っていることを検証する。
    """

    def test_detail_embeds_column_toggle_with_storage_key(self):
        ml = self._make_list()
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn('data-storage-key="mailing_list_visible_columns_detail"', body)
        self.assertIn("表示列を選択", body)

    def test_selection_storage_key_is_mode_specific(self):
        ml = self._make_list()
        add_url = reverse("mailings:list_member_add", args=[ml.pk])
        rem_url = reverse("mailings:list_member_remove", args=[ml.pk])
        add_resp = self.client.get(add_url)
        rem_resp = self.client.get(rem_url)
        self.assertIn(
            "mailing_list_visible_columns_selection_add",
            add_resp.content.decode("utf-8"),
        )
        self.assertIn(
            "mailing_list_visible_columns_selection_remove",
            rem_resp.content.decode("utf-8"),
        )

    def test_confirm_storage_key_is_mode_specific(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        self._put_session(
            f"mailing_list_{ml.pk}_add_selection", [str(p.pk)]
        )
        self._put_session(
            f"mailing_list_{ml.pk}_remove_selection", [str(p.pk)]
        )
        add_resp = self.client.get(
            reverse("mailings:list_member_add_confirm", args=[ml.pk])
        )
        rem_resp = self.client.get(
            reverse("mailings:list_member_remove_confirm", args=[ml.pk])
        )
        self.assertIn(
            "mailing_list_visible_columns_confirm_add",
            add_resp.content.decode("utf-8"),
        )
        self.assertIn(
            "mailing_list_visible_columns_confirm_remove",
            rem_resp.content.decode("utf-8"),
        )

    def test_table_cells_have_data_col_key(self):
        ml = self._make_list()
        self._add_member(ml, self._make_person("Alice"))
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        for key in ("name", "company", "title", "department", "address", "email"):
            self.assertIn(f'data-col-key="{key}"', body)

    def test_unsubscribed_badge_has_js_marker_class(self):
        ml = self._make_list()
        p = self._make_person("Alice", is_unsubscribed=True)
        self._add_member(ml, p)
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        # バッジ span に js-unsubscribed-badge クラスが付与されている
        self.assertIn("js-unsubscribed-badge", body)


# ======================================================================
# UI 改善（要望3：ボタン色とラベル）
# ======================================================================


class ButtonColorAndLabelTests(_MemberEditTestBase):
    def test_detail_add_button_is_primary_with_new_label(self):
        ml = self._make_list()
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn('class="app-btn app-btn--primary">メンバーを追加</a>', body)
        self.assertNotIn(">個別追加<", body)

    def test_detail_remove_button_is_danger_with_new_label(self):
        ml = self._make_list()
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn('class="app-btn app-btn--danger">メンバーを削除</a>', body)
        self.assertNotIn(">個別削除<", body)
        # warning カラーは旧ラベルでは使われていない（=危険操作=danger）
        self.assertNotIn('class="app-btn app-btn--warning">メンバーを削除', body)

    def test_frozen_detail_buttons_keep_color_and_label(self):
        ml = self._make_list(frozen=True)
        url = reverse("mailings:mailing_list_detail", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        # disabled でも色クラスは維持（§4.1）
        self.assertIn("app-btn--primary", body)
        self.assertIn("app-btn--danger", body)
        self.assertIn("メンバーを追加", body)
        self.assertIn("メンバーを削除", body)

    def test_selection_header_label_with_current_count(self):
        ml = self._make_list()
        self._add_member(ml, self._make_person("Existing"))
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn("メンバーを追加：", body)
        self.assertIn("（現在 1 件）", body)

    def test_selection_add_submit_button_is_primary_with_count_placeholder(self):
        ml = self._make_list()
        self._make_person("Alice")
        url = reverse("mailings:list_member_add", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        # mode=add の確定ボタンは primary
        self.assertIn('class="app-btn app-btn--primary js-selection-submit"', body)
        self.assertIn('data-label-template="選択した {count} 件を追加する"', body)
        # 初期描画は "0 件" + disabled
        self.assertIn("選択した 0 件を追加する", body)

    def test_selection_remove_submit_button_is_danger(self):
        ml = self._make_list()
        self._add_member(ml, self._make_person("Alice"))
        url = reverse("mailings:list_member_remove", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn('class="app-btn app-btn--danger js-selection-submit"', body)
        self.assertIn('data-label-template="選択した {count} 件を削除する"', body)

    def test_confirm_add_button_is_primary(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        self._put_session(f"mailing_list_{ml.pk}_add_selection", [str(p.pk)])
        url = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn('class="app-btn app-btn--primary">追加を確定</button>', body)

    def test_confirm_remove_button_is_danger(self):
        ml = self._make_list()
        p = self._make_person("Alice")
        self._add_member(ml, p)
        self._put_session(f"mailing_list_{ml.pk}_remove_selection", [str(p.pk)])
        url = reverse("mailings:list_member_remove_confirm", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn('class="app-btn app-btn--danger">削除を確定</button>', body)

    def test_confirm_description_uses_count(self):
        ml = self._make_list()
        for n in ["Alice", "Bob"]:
            self._put_session_addhelper = self._make_person(n)
        ids = [
            str(p.pk)
            for p in Person.objects.filter(
                primary_contact__full_name__in=["Alice", "Bob"]
            )
        ]
        self._put_session(f"mailing_list_{ml.pk}_add_selection", ids)
        url = reverse("mailings:list_member_add_confirm", args=[ml.pk])
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn("以下の 2 件をリストに追加します", body)


# ======================================================================
# Phase 1c-β-1（仕様書 rev5 §4.1 / §4.2）
# 抽出サービス新関数 extract_persons_by_tag_conditions のテスト
# ======================================================================


class _TagExtractionTestBase(TestCase):
    """β-1 テストの共通基底：カテゴリ / タグ / Person × Tag 付与のヘルパー。"""

    def setUp(self):
        self.user = User.objects.create_user(username="be_test", password="x")
        self.client = Client()
        self.client.force_login(self.user)
        # 2 カテゴリ × 3 タグずつ、合計 6 タグ
        self.cat_role = TagCategory.objects.create(name="役職")
        self.cat_area = TagCategory.objects.create(name="地域")
        self.t_role_a = Tag.objects.create(
            name="役職A", category=self.cat_role, created_by=self.user
        )
        self.t_role_b = Tag.objects.create(
            name="役職B", category=self.cat_role, created_by=self.user
        )
        self.t_role_c = Tag.objects.create(
            name="役職C", category=self.cat_role, created_by=self.user
        )
        self.t_area_x = Tag.objects.create(
            name="地域X", category=self.cat_area, created_by=self.user
        )
        self.t_area_y = Tag.objects.create(
            name="地域Y", category=self.cat_area, created_by=self.user
        )
        self.t_area_z = Tag.objects.create(
            name="地域Z", category=self.cat_area, created_by=self.user
        )

    def _make_person(self, full_name, *, status="active", is_unsubscribed=False):
        p = Person.objects.create(status=status, is_unsubscribed=is_unsubscribed)
        c = Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            email=f"{full_name.lower()}@example.com",
            created_by=self.user,
        )
        p.primary_contact = c
        p.save(update_fields=["primary_contact", "updated_at"])
        return p

    def _assign(self, person, *tags):
        for tag in tags:
            TagAssignment.objects.create(
                tag=tag, person=person, assigned_by=self.user
            )


class TagConditionsExtractionTests(_TagExtractionTestBase):
    def test_in_category_or_returns_union(self):
        p_a = self._make_person("AOnly")
        p_b = self._make_person("BOnly")
        p_none = self._make_person("None")  # noqa: F841
        self._assign(p_a, self.t_role_a)
        self._assign(p_b, self.t_role_b)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [
                        str(self.t_role_a.pk),
                        str(self.t_role_b.pk),
                    ],
                    "exclude_tag_ids": [],
                }
            ],
            "global_exclude_tag_ids": [],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_a, p_b})

    def test_in_category_and_returns_intersection_not_union(self):
        """AND は intersection。tags__in 誤用していると OR=union になり失敗する（§9.2-37）。"""
        p_ab = self._make_person("Both")
        p_a = self._make_person("AOnly")
        p_b = self._make_person("BOnly")
        self._assign(p_ab, self.t_role_a, self.t_role_b)
        self._assign(p_a, self.t_role_a)
        self._assign(p_b, self.t_role_b)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "AND",
                    "include_tag_ids": [
                        str(self.t_role_a.pk),
                        str(self.t_role_b.pk),
                    ],
                    "exclude_tag_ids": [],
                }
            ],
            "global_exclude_tag_ids": [],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_ab})

    def test_in_category_not_excludes(self):
        p_a = self._make_person("AOnly")
        p_ac = self._make_person("AC")
        self._assign(p_a, self.t_role_a)
        self._assign(p_ac, self.t_role_a, self.t_role_c)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [str(self.t_role_a.pk)],
                    "exclude_tag_ids": [str(self.t_role_c.pk)],
                }
            ],
            "global_exclude_tag_ids": [],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_a})

    def test_cross_category_and(self):
        """異なるカテゴリ間は AND 合成（役職A かつ 地域X）。"""
        p_only_role = self._make_person("OnlyRole")
        p_only_area = self._make_person("OnlyArea")
        p_both = self._make_person("Both")
        self._assign(p_only_role, self.t_role_a)
        self._assign(p_only_area, self.t_area_x)
        self._assign(p_both, self.t_role_a, self.t_area_x)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [str(self.t_role_a.pk)],
                    "exclude_tag_ids": [],
                },
                {
                    "category_id": str(self.cat_area.pk),
                    "operator": "OR",
                    "include_tag_ids": [str(self.t_area_x.pk)],
                    "exclude_tag_ids": [],
                },
            ],
            "global_exclude_tag_ids": [],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_both})

    def test_global_exclude_removes_from_result(self):
        p_a = self._make_person("AOnly")
        p_ax = self._make_person("AX")
        self._assign(p_a, self.t_role_a)
        self._assign(p_ax, self.t_role_a, self.t_area_x)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [str(self.t_role_a.pk)],
                    "exclude_tag_ids": [],
                }
            ],
            "global_exclude_tag_ids": [str(self.t_area_x.pk)],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_a})

    def test_no_categories_with_global_exclude_returns_all_minus_excluded(self):
        """有効カテゴリ 0 + 横断除外あり → 全 active − 横断除外（§4.1.4）。"""
        p_clean = self._make_person("Clean")
        p_tagged = self._make_person("Tagged")
        self._assign(p_tagged, self.t_area_x)
        conditions = {
            "categories": [],
            "global_exclude_tag_ids": [str(self.t_area_x.pk)],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_clean})

    def test_no_categories_no_global_exclude_returns_empty(self):
        """有効カテゴリ 0 + 横断除外なし → 0 件（§4.1.4）。"""
        self._make_person("Whoever")
        conditions = {"categories": [], "global_exclude_tag_ids": []}
        result = list(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, [])

    def test_empty_include_tag_ids_category_is_ignored(self):
        """include 空のカテゴリは無視され、他のカテゴリだけで判定される（§4.1.4）。"""
        p_role_a = self._make_person("RoleA")
        self._assign(p_role_a, self.t_role_a)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [str(self.t_role_a.pk)],
                    "exclude_tag_ids": [],
                },
                {
                    "category_id": str(self.cat_area.pk),
                    "operator": "OR",
                    "include_tag_ids": [],  # ←無視されるべき
                    "exclude_tag_ids": [str(self.t_area_x.pk)],  # 無視（include 空のため）
                },
            ],
            "global_exclude_tag_ids": [],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_role_a})

    def test_archived_person_excluded(self):
        p_active = self._make_person("Active")
        p_arch = self._make_person("Arch", status="archived")
        self._assign(p_active, self.t_role_a)
        self._assign(p_arch, self.t_role_a)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [str(self.t_role_a.pk)],
                    "exclude_tag_ids": [],
                }
            ],
            "global_exclude_tag_ids": [],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_active})

    def test_unsubscribed_person_included(self):
        """is_unsubscribed=True でも抽出対象に含まれる（§9.2-30、配信実行層の責務）。"""
        p_normal = self._make_person("Normal")
        p_unsub = self._make_person("Unsub", is_unsubscribed=True)
        self._assign(p_normal, self.t_role_a)
        self._assign(p_unsub, self.t_role_a)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [str(self.t_role_a.pk)],
                    "exclude_tag_ids": [],
                }
            ],
            "global_exclude_tag_ids": [],
        }
        result = set(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(result, {p_normal, p_unsub})

    def test_distinct_removes_join_duplicates(self):
        """複数 OR タグを持つ Person が 1 件だけ返る（.distinct() の検証）。"""
        p_multi = self._make_person("Multi")
        self._assign(p_multi, self.t_role_a, self.t_role_b, self.t_role_c)
        conditions = {
            "categories": [
                {
                    "category_id": str(self.cat_role.pk),
                    "operator": "OR",
                    "include_tag_ids": [
                        str(self.t_role_a.pk),
                        str(self.t_role_b.pk),
                        str(self.t_role_c.pk),
                    ],
                    "exclude_tag_ids": [],
                }
            ],
            "global_exclude_tag_ids": [],
        }
        result = list(extract_persons_by_tag_conditions(conditions))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], p_multi)


# ======================================================================
# Phase 1c-β-1（仕様書 rev5 §4.3）：preview-v2 API のテスト
# ======================================================================


class _PreviewV2ApiTestBase(_TagExtractionTestBase):
    def setUp(self):
        super().setUp()
        self.url = reverse("mailings:mailing_list_preview_v2")

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _ok_body(self, mailing_list=None, conditions=None):
        body = {
            "conditions": conditions
            or {
                "categories": [],
                "global_exclude_tag_ids": [],
            }
        }
        if mailing_list is not None:
            body["list_id"] = str(mailing_list.pk)
        return body

    def _make_list(self):
        return MailingList.objects.create(name="TestList", created_by=self.user)


class PreviewV2ApiHappyPathTests(_PreviewV2ApiTestBase):
    def test_post_no_list_id_returns_null_count_fields(self):
        p = self._make_person("Alice")
        self._assign(p, self.t_role_a)
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_count"], 1)
        for k in ("new_count", "already_in_list", "out_of_list_count", "in_list_count"):
            self.assertIsNone(data[k], f"{k} must be null when list_id not given")
        self.assertEqual(data["invalid_tag_ids"], [])
        self.assertEqual(data["invalid_category_ids"], [])

    def test_post_with_list_id_aliases_match(self):
        """out_of_list_count == new_count, in_list_count == already_in_list（§4.3.3）。"""
        ml = self._make_list()
        p_in = self._make_person("InList")
        p_new = self._make_person("NewCandidate")
        self._assign(p_in, self.t_role_a)
        self._assign(p_new, self.t_role_a)
        MailingListMember.objects.create(
            mailing_list=ml, person=p_in, added_by=self.user
        )
        body = self._ok_body(
            ml,
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            },
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_count"], 2)
        self.assertEqual(data["already_in_list"], 1)
        self.assertEqual(data["new_count"], 1)
        # エイリアス関係
        self.assertEqual(data["out_of_list_count"], data["new_count"])
        self.assertEqual(data["in_list_count"], data["already_in_list"])

    def test_samples_capped_at_10_and_sorted_by_id_asc(self):
        for i in range(15):
            p = self._make_person(f"P{i:02d}")
            self._assign(p, self.t_role_a)
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        resp = self._post(body)
        data = resp.json()
        self.assertEqual(len(data["samples"]), 10)
        ids = [s["id"] for s in data["samples"]]
        self.assertEqual(ids, sorted(ids))  # id 昇順

    def test_samples_include_address_field(self):
        p = self._make_person("Alice")
        p.primary_contact.address = "愛知県豊田市岩滝町滝坂556-8"
        p.primary_contact.save(update_fields=["address", "updated_at"])
        self._assign(p, self.t_role_a)
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        resp = self._post(body)
        sample = resp.json()["samples"][0]
        self.assertIn("address", sample)
        self.assertEqual(sample["address"], "愛知県豊田市岩滝町滝坂556-8")
        # 仕様書 §4.3.3 で必須の他フィールドも存在
        for k in (
            "id",
            "name",
            "company",
            "department",
            "title",
            "email",
            "is_unsubscribed",
            "already_in_list",
        ):
            self.assertIn(k, sample)

    def test_already_in_list_flag_per_sample(self):
        ml = self._make_list()
        p_in = self._make_person("InList")
        p_out = self._make_person("OutList")
        self._assign(p_in, self.t_role_a)
        self._assign(p_out, self.t_role_a)
        MailingListMember.objects.create(
            mailing_list=ml, person=p_in, added_by=self.user
        )
        body = self._ok_body(
            ml,
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            },
        )
        resp = self._post(body)
        data = resp.json()
        flags = {s["id"]: s["already_in_list"] for s in data["samples"]}
        self.assertTrue(flags[str(p_in.pk)])
        self.assertFalse(flags[str(p_out.pk)])

    def test_already_in_list_false_when_no_list_id(self):
        p = self._make_person("Alice")
        self._assign(p, self.t_role_a)
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        resp = self._post(body)
        sample = resp.json()["samples"][0]
        self.assertFalse(sample["already_in_list"])

    def test_empty_conditions_returns_zero(self):
        self._make_person("X")
        body = self._ok_body()
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_count"], 0)
        self.assertEqual(data["samples"], [])

    def test_global_exclude_only_counts_remaining_persons(self):
        p_clean = self._make_person("Clean")
        p_excluded = self._make_person("Excluded")
        self._assign(p_excluded, self.t_area_x)
        body = self._ok_body(
            conditions={
                "categories": [],
                "global_exclude_tag_ids": [str(self.t_area_x.pk)],
            }
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(data["samples"][0]["id"], str(p_clean.pk))


class PreviewV2ApiSilentlyIgnoreTests(_PreviewV2ApiTestBase):
    def test_archived_tag_silently_ignored(self):
        p = self._make_person("Alice")
        self._assign(p, self.t_role_a)
        self.t_role_b.is_archived = True
        self.t_role_b.save(update_fields=["is_archived", "updated_at"])
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [
                            str(self.t_role_a.pk),
                            str(self.t_role_b.pk),
                        ],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_count"], 1)
        self.assertIn(str(self.t_role_b.pk), data["invalid_tag_ids"])

    def test_nonexistent_tag_silently_ignored(self):
        ghost = str(uuid.uuid4())
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [ghost],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(ghost, data["invalid_tag_ids"])

    def test_nonexistent_category_silently_ignored(self):
        ghost_cat = str(uuid.uuid4())
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": ghost_cat,
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(ghost_cat, data["invalid_category_ids"])
        # カテゴリ無効化により抽出対象なし
        self.assertEqual(data["total_count"], 0)


class PreviewV2ApiErrorTests(_PreviewV2ApiTestBase):
    def test_get_returns_405(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_unauthenticated_returns_302(self):
        c = Client()
        resp = c.post(
            self.url,
            data=json.dumps({"conditions": {"categories": [], "global_exclude_tag_ids": []}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 302)

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not-json{", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "invalid_json")

    def test_missing_field_conditions_returns_400(self):
        resp = self._post({})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "missing_field")

    def test_missing_field_global_exclude_returns_400(self):
        resp = self._post({"conditions": {"categories": []}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "missing_field")

    def test_missing_field_in_category_returns_400(self):
        body = {
            "conditions": {
                "categories": [{"category_id": str(self.cat_role.pk)}],
                "global_exclude_tag_ids": [],
            }
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "missing_field")

    def test_invalid_uuid_hyphen_stripped_32_chars(self):
        """ハイフンなし 32 文字は invalid_uuid（§4.3.5）。"""
        body = {
            "conditions": {
                "categories": [],
                "global_exclude_tag_ids": [str(self.t_role_a.pk).replace("-", "")],
            }
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_uuid")

    def test_invalid_uuid_in_list_id(self):
        body = {
            "list_id": "not-a-uuid",
            "conditions": {"categories": [], "global_exclude_tag_ids": []},
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_uuid")

    def test_invalid_operator(self):
        body = {
            "conditions": {
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "XOR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_operator")

    def test_duplicate_category(self):
        cat_id = str(self.cat_role.pk)
        body = {
            "conditions": {
                "categories": [
                    {
                        "category_id": cat_id,
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    },
                    {
                        "category_id": cat_id,
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_b.pk)],
                        "exclude_tag_ids": [],
                    },
                ],
                "global_exclude_tag_ids": [],
            }
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "duplicate_category")

    def test_list_not_found(self):
        body = {
            "list_id": str(uuid.uuid4()),
            "conditions": {"categories": [], "global_exclude_tag_ids": []},
        }
        resp = self._post(body)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "list_not_found")


class PreviewV2ApiPerformanceTests(_PreviewV2ApiTestBase):
    def test_no_n_plus_1_in_samples(self):
        """samples 取得は select_related で 1 クエリ化（N+1 不発、§4.3.6）。"""
        for i in range(10):
            p = self._make_person(f"P{i:02d}")
            self._assign(p, self.t_role_a)
        body = self._ok_body(
            conditions={
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            }
        )
        # 同じ samples 取得を 10 件 / 1 件で比較し、samples 件数に応じてクエリ数が
        # 増えないことを確認する（N+1 だと 1 件あたり +1 クエリ増える）。
        with self.assertNumQueries(self._count_queries(body)):
            resp = self._post(body)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.json()["samples"]), 10)

    def _count_queries(self, body):
        """先行実行で実際のクエリ数を取得し、回帰判定の基準にする。

        N+1 検出の主眼は「件数に応じて伸びないこと」だが、ベースライン値を assertion
        にすることでクエリ過多な実装変更を弾く。
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self._post(body)
        return len(ctx.captured_queries)


# ======================================================================
# 既存機能の非破壊検証（§11.3-8）
# ======================================================================


class LegacyExtractionRegressionTests(_TagExtractionTestBase):
    def test_legacy_extract_persons_by_tags_still_returns_or_within_category(self):
        """既存 extract_persons_by_tags は同カテゴリ内 OR の挙動を維持する。"""
        p_a = self._make_person("AOnly")
        p_b = self._make_person("BOnly")
        self._assign(p_a, self.t_role_a)
        self._assign(p_b, self.t_role_b)
        result = set(
            extract_persons_by_tags([str(self.t_role_a.pk), str(self.t_role_b.pk)])
        )
        self.assertEqual(result, {p_a, p_b})

    def test_legacy_preview_endpoint_still_responds(self):
        """既存 /mailings/lists/preview/（POST）が引き続き動作する（§9.2-21）。"""
        url = reverse("mailings:mailing_list_preview")
        resp = self.client.post(
            url,
            data=json.dumps({"tag_ids": [str(self.t_role_a.pk)]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("count", data)
        self.assertIn("samples", data)


# ======================================================================
# Phase 1c-β-2a 新規作成ウィザード（仕様書 rev6 §4.5、§10 #17〜#20 #25）
# ======================================================================
#
# 1-B → 1-C → 1-D → 確定 の 4 ステップで MailingList + MailingListMember を
# snapshot 方式で一括作成する。session 'mailing_list_new_create_state' を
# 唯一の真実とし、ステップ進行ガード（§4.5.1a）で直叩きから保護。
#
# 指示書 §6.2 T1-T14 を網羅。β-2b で実装される JS 動的挙動（プレビュー AJAX・
# 警告ダイアログ・備考プリセット）はここではテストしない。

NEW_LIST_SESSION_KEY = "mailing_list_new_create_state"


class _NewListWizardTestBase(_TagExtractionTestBase):
    """新規作成ウィザード共通基底：1-B → 1-C → 1-D の URL とヘルパー。"""

    def setUp(self):
        super().setUp()
        self.url_meta = reverse("mailings:new_list_meta")
        self.url_tags = reverse("mailings:new_list_tag_selection")
        self.url_confirm = reverse("mailings:new_list_confirm")
        self.url_commit = reverse("mailings:new_list_commit")
        self.url_list = reverse("mailings:mailing_list_list")

    def _session(self):
        return self.client.session.get(NEW_LIST_SESSION_KEY) or {}

    def _put_session(self, **patch):
        session = self.client.session
        state = session.get(NEW_LIST_SESSION_KEY) or {}
        state.update(patch)
        session[NEW_LIST_SESSION_KEY] = state
        session.save()

    def _build_post_for_tag_selection(self, *, include=(), exclude=(), operator=None, global_exclude=()):
        """1-C POST 用 form データを組み立てる（テストヘルパー）。"""
        post = {}
        cat_id = str(self.cat_role.pk)
        if include:
            post[f"include_{cat_id}"] = [str(t.pk) for t in include]
        if exclude:
            post[f"exclude_{cat_id}"] = [str(t.pk) for t in exclude]
        if operator is not None:
            post[f"operator_{cat_id}"] = operator
        # 必ず include_<cat_id> キーが POST に存在しないと _parse_conditions_from_post
        # がカテゴリを認識しないため、空でも空 list を入れる。Django test client は
        # 空 list を送らないため、include 指定なしの場合は POST 自体に出ないことが
        # 期待される（カテゴリは無視される、§4.1.4）。
        if global_exclude:
            post["global_exclude_tag_ids"] = [str(t.pk) for t in global_exclude]
        return post


class NewListMetaViewTests(_NewListWizardTestBase):
    def test_get_initializes_session(self):
        # 既に何か入っていても初期化される
        self._put_session(name="OldName", description="old", step="confirm")
        resp = self.client.get(self.url_meta)
        self.assertEqual(resp.status_code, 200)
        state = self._session()
        self.assertEqual(state.get("name", ""), "")
        self.assertEqual(state.get("description", ""), "")
        self.assertEqual(state.get("step"), "meta")

    def test_unauthenticated_redirects(self):
        c = Client()
        resp = c.get(self.url_meta)
        self.assertEqual(resp.status_code, 302)

    def test_post_valid_saves_session_and_redirects_to_tags(self):
        resp = self.client.post(
            self.url_meta, {"name": "L1", "description": "memo"}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.url_tags, resp["Location"])
        state = self._session()
        self.assertEqual(state["name"], "L1")
        self.assertEqual(state["description"], "memo")
        self.assertEqual(state["step"], "tags")

    def test_post_empty_name_returns_400_and_does_not_save(self):
        resp = self.client.post(
            self.url_meta, {"name": "", "description": "memo"}
        )
        self.assertEqual(resp.status_code, 400)
        # session には書き込まれていない（=空 or step=meta のまま）
        state = self._session()
        self.assertFalse(state.get("name"))

    def test_post_duplicate_name_is_warning_not_error(self):
        MailingList.objects.create(name="L1", created_by=self.user)
        resp = self.client.post(
            self.url_meta, {"name": "L1", "description": ""}
        )
        # 警告のみ、許可 → 302 で 1-C へ
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._session()["name"], "L1")


class NewListTagSelectionViewTests(_NewListWizardTestBase):
    def test_get_without_name_redirects_to_meta(self):
        """1-C 直叩き（session 空）→ 1-B に 302（§4.5.1a / T1）。"""
        resp = self.client.get(self.url_tags)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.url_meta, resp["Location"])

    def test_get_with_name_renders(self):
        self._put_session(name="L1", description="memo", step="tags")
        resp = self.client.get(self.url_tags)
        self.assertEqual(resp.status_code, 200)
        # コンテキスト経由でリスト名・備考が渡っている（T3）
        self.assertEqual(resp.context["list_name"], "L1")
        self.assertEqual(resp.context["list_description"], "memo")
        self.assertEqual(resp.context["mode"], "create")

    def test_get_after_meta_post_preserves_name(self):
        """1-B POST → 1-C GET で name/description が消えない（T3）。"""
        self.client.post(
            self.url_meta, {"name": "Preserved", "description": "kept"}
        )
        resp = self.client.get(self.url_tags)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["list_name"], "Preserved")
        self.assertEqual(resp.context["list_description"], "kept")

    def test_get_meta_after_progress_clears_session(self):
        """1-C に進んだ後 1-B 再 GET → session の name/description クリア（T4）。"""
        self._put_session(name="WIP", description="wip", step="tags")
        resp = self.client.get(self.url_meta)
        self.assertEqual(resp.status_code, 200)
        state = self._session()
        self.assertEqual(state.get("name", ""), "")

    def test_post_with_valid_conditions_saves_snapshot_and_redirects_to_confirm(self):
        p = self._make_person("Alice")
        self._assign(p, self.t_role_a)
        self._put_session(name="L1", description="", step="tags")
        post = self._build_post_for_tag_selection(
            include=[self.t_role_a], operator="OR"
        )
        resp = self.client.post(self.url_tags, post)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.url_confirm, resp["Location"])
        state = self._session()
        self.assertEqual(state["step"], "confirm")
        self.assertEqual(state["snapshot_person_ids"], [str(p.pk)])
        self.assertIn("conditions", state)

    def test_post_with_empty_conditions_returns_400(self):
        """サーバ側保険：実質空 conditions は 400（§3.5）。"""
        self._put_session(name="L1", description="", step="tags")
        resp = self.client.post(self.url_tags, {})
        self.assertEqual(resp.status_code, 400)
        # snapshot は書かれない
        state = self._session()
        self.assertNotIn("snapshot_person_ids", state)

    def test_post_with_and_operator(self):
        """AND 演算が反映される（T13、β-1 AND 実装の経路確認）。"""
        p_ab = self._make_person("Both")
        p_a = self._make_person("AOnly")
        self._assign(p_ab, self.t_role_a, self.t_role_b)
        self._assign(p_a, self.t_role_a)
        self._put_session(name="L1", description="", step="tags")
        post = self._build_post_for_tag_selection(
            include=[self.t_role_a, self.t_role_b], operator="AND"
        )
        resp = self.client.post(self.url_tags, post)
        self.assertEqual(resp.status_code, 302)
        snapshot = self._session()["snapshot_person_ids"]
        self.assertEqual(snapshot, [str(p_ab.pk)])

    def test_post_with_global_exclude(self):
        """横断除外が反映される（T13）。"""
        p_clean = self._make_person("Clean")
        p_excluded = self._make_person("Excluded")
        self._assign(p_clean, self.t_role_a)
        self._assign(p_excluded, self.t_role_a, self.t_area_x)
        self._put_session(name="L1", description="", step="tags")
        post = self._build_post_for_tag_selection(
            include=[self.t_role_a],
            operator="OR",
            global_exclude=[self.t_area_x],
        )
        resp = self.client.post(self.url_tags, post)
        self.assertEqual(resp.status_code, 302)
        snapshot = set(self._session()["snapshot_person_ids"])
        self.assertEqual(snapshot, {str(p_clean.pk)})

    def test_post_includes_unsubscribed_person_in_snapshot(self):
        """退会者も snapshot に含まれる（T14、§9.2-30）。"""
        p_unsub = self._make_person("Unsub", is_unsubscribed=True)
        self._assign(p_unsub, self.t_role_a)
        self._put_session(name="L1", description="", step="tags")
        post = self._build_post_for_tag_selection(
            include=[self.t_role_a], operator="OR"
        )
        resp = self.client.post(self.url_tags, post)
        self.assertEqual(resp.status_code, 302)
        snapshot = self._session()["snapshot_person_ids"]
        self.assertIn(str(p_unsub.pk), snapshot)


class NewListConfirmViewTests(_NewListWizardTestBase):
    def test_get_empty_session_redirects_to_meta(self):
        """1-D 直叩き（session 完全空）→ 1-B に 302（T2 / T7）。"""
        resp = self.client.get(self.url_confirm)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.url_meta, resp["Location"])

    def test_get_with_name_only_redirects_to_tags(self):
        """1-D 直叩き（name のみ、snapshot 未保存）→ 1-C に 302（T2）。"""
        self._put_session(name="L1", description="", step="tags")
        resp = self.client.get(self.url_confirm)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.url_tags, resp["Location"])

    def test_get_with_all_required_renders(self):
        p = self._make_person("Alice")
        self._put_session(
            name="L1",
            description="memo",
            step="confirm",
            snapshot_person_ids=[str(p.pk)],
        )
        resp = self.client.get(self.url_confirm)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["list_name"], "L1")
        self.assertEqual(resp.context["snapshot_count"], 1)

    def test_post_method_returns_405(self):
        self._put_session(
            name="L1",
            description="",
            step="confirm",
            snapshot_person_ids=[str(uuid.uuid4())],
        )
        # NewListConfirmView は GET のみだが、URL 上 POST すると method not allowed
        resp = self.client.post(self.url_confirm)
        self.assertEqual(resp.status_code, 405)

    def test_renders_only_count_no_person_list(self):
        """1-D は Person 列を表示しない（§4.5.4 / §9.2-42）。"""
        p = self._make_person("VisibleName")
        self._put_session(
            name="L1",
            description="",
            step="confirm",
            snapshot_person_ids=[str(p.pk)],
        )
        resp = self.client.get(self.url_confirm)
        body = resp.content.decode("utf-8")
        # 件数バッジは出る
        self.assertIn("対象 1 件でリストを作成します", body)
        # Person 名は出さない（一覧テーブルがない）
        self.assertNotIn("VisibleName", body)


class NewListCommitViewTests(_NewListWizardTestBase):
    def test_post_creates_list_and_members_and_redirects_detail(self):
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob")
        self._put_session(
            name="L1",
            description="desc",
            step="confirm",
            snapshot_person_ids=[str(p1.pk), str(p2.pk)],
        )
        resp = self.client.post(self.url_commit)
        self.assertEqual(resp.status_code, 302)
        # 詳細画面リダイレクト → MailingList 1 件と Member 2 件
        new_list = MailingList.objects.get(name="L1")
        self.assertEqual(new_list.description, "desc")
        self.assertIn(
            reverse("mailings:mailing_list_detail", args=[new_list.pk]),
            resp["Location"],
        )
        self.assertEqual(
            MailingListMember.objects.filter(mailing_list=new_list).count(), 2
        )
        # session クリア
        self.assertNotIn(NEW_LIST_SESSION_KEY, self.client.session)

    def test_post_get_method_returns_405(self):
        resp = self.client.get(self.url_commit)
        self.assertEqual(resp.status_code, 405)

    def test_post_empty_session_redirects_to_meta(self):
        resp = self.client.post(self.url_commit)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.url_meta, resp["Location"])

    def test_prg_replay_does_not_double_create(self):
        """PRG パターン：commit POST 二度目は session クリア済みのため 1-B に差し戻し（T8）。"""
        p = self._make_person("Alice")
        self._put_session(
            name="L1",
            description="",
            step="confirm",
            snapshot_person_ids=[str(p.pk)],
        )
        resp1 = self.client.post(self.url_commit)
        self.assertEqual(resp1.status_code, 302)
        resp2 = self.client.post(self.url_commit)
        # 2 回目は session 空のため 1-B へ差し戻し（二重作成されない）
        self.assertEqual(resp2.status_code, 302)
        self.assertIn(self.url_meta, resp2["Location"])
        self.assertEqual(MailingList.objects.filter(name="L1").count(), 1)

    def test_post_duplicate_person_id_in_snapshot_no_error(self):
        """snapshot に同じ Person ID が重複しても bulk_create + ignore_conflicts でエラーにならない（T9）。"""
        p = self._make_person("Alice")
        self._put_session(
            name="L1",
            description="",
            step="confirm",
            snapshot_person_ids=[str(p.pk), str(p.pk)],
        )
        resp = self.client.post(self.url_commit)
        self.assertEqual(resp.status_code, 302)
        new_list = MailingList.objects.get(name="L1")
        # 重複は弾かれて 1 件のみ
        self.assertEqual(
            MailingListMember.objects.filter(mailing_list=new_list).count(), 1
        )

    def test_snapshot_unchanged_by_post_commit_db_mutation(self):
        """snapshot 方式：1-C POST 後の Person 変更が確定結果に影響しない（T5）。

        1-C POST 時に snapshot ID 集合が固定される。確定までの間に Person のタグが
        変更されても、snapshot ID で MailingListMember を作成するので顔ぶれは変わらない。
        """
        p = self._make_person("Alice")
        self._assign(p, self.t_role_a)
        self._put_session(name="L1", description="", step="tags")
        # 1-C POST で snapshot 確定
        post = self._build_post_for_tag_selection(
            include=[self.t_role_a], operator="OR"
        )
        self.client.post(self.url_tags, post)
        # Person のタグを剝がす（抽出条件から外れる）
        TagAssignment.objects.filter(person=p).delete()
        # 1-D 確定 → snapshot 通りに p が登録されるはず
        resp = self.client.post(self.url_commit)
        self.assertEqual(resp.status_code, 302)
        new_list = MailingList.objects.get(name="L1")
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=new_list, person=p
            ).exists()
        )

    def test_snapshot_archived_person_still_committed(self):
        """snapshot に含まれる Person を archived 化しても snapshot は維持（T6）。"""
        p = self._make_person("Alice")
        self._put_session(
            name="L1",
            description="",
            step="confirm",
            snapshot_person_ids=[str(p.pk)],
        )
        p.status = Person.Status.ARCHIVED
        p.save(update_fields=["status", "updated_at"])
        resp = self.client.post(self.url_commit)
        self.assertEqual(resp.status_code, 302)
        new_list = MailingList.objects.get(name="L1")
        # snapshot 通り p が登録される
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=new_list, person=p
            ).exists()
        )


class NewListCancelTests(_NewListWizardTestBase):
    def test_cancel_links_in_meta_point_to_list(self):
        resp = self.client.get(self.url_meta)
        body = resp.content.decode("utf-8")
        # キャンセル先はリスト一覧
        self.assertIn(self.url_list, body)

    def test_meta_get_clears_session_when_called_after_progress(self):
        """T12 関連：1-B GET（=入口）が session をクリアする → 「キャンセル相当」になる。

        厳密な「キャンセル」ボタンによるクリアではないが、ユーザが 1-B に戻ってきた
        時点で session が初期化される設計（§4.5.1a）。
        """
        self._put_session(
            name="WIP", description="wip",
            step="confirm",
            snapshot_person_ids=[str(uuid.uuid4())],
        )
        self.client.get(self.url_meta)
        state = self._session()
        self.assertEqual(state.get("name", ""), "")
        self.assertNotIn("snapshot_person_ids", state)


# ======================================================================
# Phase 1c-β-3a タグで追加（仕様書 rev6 §4.6 / §10 #21〜#24）
# ======================================================================
#
# 既存リストに対する「タグで追加」フロー 1-H → 1-K → 確定 を検証する。
# snapshot は「抽出 − 既登録」のみ、退会者は含む。bulk_create + ignore_conflicts
# で確定、PRG で詳細画面へ。session キー mailing_list_<pk>_add_by_tag_state。
#
# tag_selection.html の add_by_tag mode 描画 + リスト詳細「タグで追加」ボタンの
# 配置（追加系の行）も検証。タグで除外（remove_by_tag）は β-3b 範囲、本ファイルでは
# 触らない。


def _add_by_tag_session_key(mailing_list_pk):
    return f"mailing_list_{mailing_list_pk}_add_by_tag_state"


class _AddByTagTestBase(_TagExtractionTestBase):
    def setUp(self):
        super().setUp()
        self.user = self.user  # _TagExtractionTestBase が作る
        self.mailing_list = MailingList.objects.create(
            name="L1", created_by=self.user
        )

    def _make_list_person(self, full_name, *, is_unsubscribed=False, in_list=False, status="active"):
        p = self._make_person(full_name, is_unsubscribed=is_unsubscribed, status=status)
        if in_list:
            MailingListMember.objects.create(
                mailing_list=self.mailing_list, person=p, added_by=self.user
            )
        return p

    def _post_conditions(self, *, include=(), operator="OR"):
        post = {}
        cat_id = str(self.cat_role.pk)
        if include:
            post[f"include_{cat_id}"] = [str(t.pk) for t in include]
            post[f"operator_{cat_id}"] = operator
        return post

    def _put_state(self, **state):
        session = self.client.session
        session[_add_by_tag_session_key(self.mailing_list.pk)] = state
        session.save()

    def _state(self):
        return self.client.session.get(
            _add_by_tag_session_key(self.mailing_list.pk)
        ) or {}


class MemberAddByTagViewGetTests(_AddByTagTestBase):
    def test_get_200(self):
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # add_by_tag mode のコンテキストと緑バナー文言を確認
        body = resp.content.decode("utf-8")
        self.assertIn("タグで追加：L1", body)
        self.assertIn("app-message--success", body)

    def test_get_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 409)

    def test_get_archived_returns_404(self):
        self.mailing_list.is_archived = True
        self.mailing_list.save(update_fields=["is_archived", "updated_at"])
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_template_has_list_id_data_attribute(self):
        """JS が dataset.listId で読むため、テンプレに data-list-id 出力必須。"""
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn(f'data-list-id="{self.mailing_list.pk}"', body)


class MemberAddByTagViewPostTests(_AddByTagTestBase):
    def test_post_snapshot_excludes_already_in_list(self):
        """snapshot は「抽出 − 既登録」のみ（§4.6.1）。"""
        p_in = self._make_list_person("Alice", in_list=True)  # 既登録
        p_new = self._make_list_person("Bob")  # 未登録
        self._assign(p_in, self.t_role_a)
        self._assign(p_new, self.t_role_a)
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        state = self._state()
        self.assertEqual(state["snapshot_person_ids"], [str(p_new.pk)])
        self.assertEqual(state["total_extracted"], 2)
        self.assertEqual(state["already_in_list_count"], 1)

    def test_post_snapshot_includes_unsubscribed(self):
        """退会者は snapshot に含まれる（§9.2-30）。"""
        p_unsub = self._make_list_person("Unsub", is_unsubscribed=True)
        self._assign(p_unsub, self.t_role_a)
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._state()["snapshot_person_ids"], [str(p_unsub.pk)])

    def test_post_empty_conditions_returns_400(self):
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(url, {})
        self.assertEqual(resp.status_code, 400)
        # snapshot は保存されない
        self.assertEqual(self._state(), {})

    def test_post_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        p = self._make_list_person("Alice")
        self._assign(p, self.t_role_a)
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 409)

    def test_post_redirects_to_confirm(self):
        p = self._make_list_person("Alice")
        self._assign(p, self.t_role_a)
        url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        confirm_url = reverse(
            "mailings:list_member_add_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(confirm_url, resp["Location"])

    def test_get_after_post_restores_conditions(self):
        """戻り時の状態復元（§7.1）：POST 後の GET で current_conditions が復元される。"""
        p = self._make_list_person("Alice")
        self._assign(p, self.t_role_a)
        post_url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        self.client.post(
            post_url, self._post_conditions(include=[self.t_role_a])
        )
        resp = self.client.get(post_url)
        self.assertEqual(resp.status_code, 200)
        ctx_conditions = resp.context["current_conditions"]
        self.assertEqual(len(ctx_conditions["categories"]), 1)
        self.assertIn(
            str(self.t_role_a.pk),
            ctx_conditions["categories"][0]["include_tag_ids"],
        )


class MemberAddByTagConfirmViewTests(_AddByTagTestBase):
    def test_empty_session_redirects_to_selection(self):
        """session 空（conditions なし）→ 1-H に 302（§9.2-45）。"""
        url = reverse(
            "mailings:list_member_add_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        sel = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(sel, resp["Location"])

    def test_renders_snapshot_persons_and_diff_count(self):
        p1 = self._make_list_person("Alice")
        p2 = self._make_list_person("Bob")
        # 既登録 1 件（snapshot 外）
        p_existing = self._make_list_person("Existing", in_list=True)
        self._put_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p1.pk), str(p2.pk)],
            total_extracted=3,
            already_in_list_count=1,
        )
        url = reverse(
            "mailings:list_member_add_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertEqual(ctx["new_count"], 2)
        self.assertEqual(ctx["total_extracted"], 3)
        self.assertEqual(ctx["already_in_list_count"], 1)
        self.assertEqual(ctx["current_member_count"], 1)
        self.assertEqual(ctx["after_count"], 3)
        body = resp.content.decode("utf-8")
        # 重複除外の説明文 + Before/After
        self.assertIn("抽出 <strong>3 件</strong>", body)
        self.assertIn("既にリストに登録されています", body)
        self.assertIn("→ +2 件", body)

    def test_renders_unsubscribed_badge_in_snapshot(self):
        p_unsub = self._make_list_person("Unsub", is_unsubscribed=True)
        self._put_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p_unsub.pk)],
            total_extracted=1,
            already_in_list_count=0,
        )
        url = reverse(
            "mailings:list_member_add_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertIn("js-unsubscribed-badge", resp.content.decode("utf-8"))

    def test_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        self._put_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(uuid.uuid4())],
            total_extracted=1,
            already_in_list_count=0,
        )
        url = reverse(
            "mailings:list_member_add_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 409)

    def test_post_method_returns_405(self):
        url = reverse(
            "mailings:list_member_add_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 405)


class MemberAddByTagCommitViewTests(_AddByTagTestBase):
    def test_commit_creates_members_and_redirects_to_detail(self):
        p1 = self._make_list_person("Alice")
        p2 = self._make_list_person("Bob")
        self._put_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p1.pk), str(p2.pk)],
            total_extracted=2,
            already_in_list_count=0,
        )
        url = reverse(
            "mailings:list_member_commit_add_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse(
                "mailings:mailing_list_detail", args=[self.mailing_list.pk]
            ),
            resp["Location"],
        )
        self.assertEqual(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list
            ).count(),
            2,
        )
        # session クリア
        self.assertEqual(self._state(), {})

    def test_commit_ignore_conflicts_existing_member(self):
        """既登録 Person を snapshot に含めても ignore_conflicts でエラー無し。"""
        p_existing = self._make_list_person("Existing", in_list=True)
        p_new = self._make_list_person("New")
        # snapshot は通常「抽出 − 既登録」だが、レースで既登録になった場合も想定
        self._put_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p_existing.pk), str(p_new.pk)],
            total_extracted=2,
            already_in_list_count=0,
        )
        url = reverse(
            "mailings:list_member_commit_add_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        # 既存 1 + 新規 1 = 計 2 件
        self.assertEqual(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list
            ).count(),
            2,
        )

    def test_commit_empty_session_redirects_to_selection(self):
        url = reverse(
            "mailings:list_member_commit_add_by_tag",
            args=[self.mailing_list.pk],
        )
        sel = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(sel, resp["Location"])

    def test_commit_get_method_returns_405(self):
        url = reverse(
            "mailings:list_member_commit_add_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 405)

    def test_commit_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        p = self._make_list_person("Alice")
        self._put_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p.pk)],
            total_extracted=1,
            already_in_list_count=0,
        )
        url = reverse(
            "mailings:list_member_commit_add_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 409)

    def test_commit_snapshot_unchanged_by_db_mutation(self):
        """snapshot 方式：POST 後の Person タグ変更が確定結果に影響しない（§9.2-26）。"""
        p = self._make_list_person("Alice")
        self._assign(p, self.t_role_a)
        post_url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        self.client.post(
            post_url, self._post_conditions(include=[self.t_role_a])
        )
        # snapshot 確定後にタグを剥がしても、commit は snapshot ID で進む
        TagAssignment.objects.filter(person=p).delete()
        commit_url = reverse(
            "mailings:list_member_commit_add_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(commit_url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list, person=p
            ).exists()
        )

    def test_prg_replay_does_not_double_add(self):
        """PRG：commit 2 回目は session クリア済みで 1-H に差し戻し（二重追加なし）。"""
        p = self._make_list_person("Alice")
        self._put_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p.pk)],
            total_extracted=1,
            already_in_list_count=0,
        )
        commit_url = reverse(
            "mailings:list_member_commit_add_by_tag",
            args=[self.mailing_list.pk],
        )
        resp1 = self.client.post(commit_url)
        self.assertEqual(resp1.status_code, 302)
        resp2 = self.client.post(commit_url)
        self.assertEqual(resp2.status_code, 302)
        self.assertIn(
            reverse(
                "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
            ),
            resp2["Location"],
        )
        self.assertEqual(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list, person=p
            ).count(),
            1,
        )


class DetailViewAddByTagButtonTests(_AddByTagTestBase):
    def test_detail_shows_add_by_tag_button_active(self):
        url = reverse(
            "mailings:mailing_list_detail", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        add_by_tag_url = reverse(
            "mailings:list_member_add_by_tag", args=[self.mailing_list.pk]
        )
        self.assertIn(add_by_tag_url, body)
        # 追加系 = success カラーのリンク
        self.assertIn(">タグで追加</a>", body)
        self.assertIn("app-btn--success", body)

    def test_detail_frozen_disables_add_by_tag_button(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        url = reverse(
            "mailings:mailing_list_detail", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        # disabled span（リンクでない）
        self.assertNotIn(
            f'href="{reverse("mailings:list_member_add_by_tag", args=[self.mailing_list.pk])}',
            body,
        )
        self.assertIn(">タグで追加</span>", body)
        self.assertIn("凍結中のため編集できません", body)


# ======================================================================
# Phase 1c-β-3b タグで除外（仕様書 rev6 §4.6.5 / §10 #27〜#30 #32）
# ======================================================================
#
# 既存リストから「タグで対象を抽出して外す」フロー 1-L → 1-M → 確定 を検証する。
# snapshot は「抽出 ∩ リスト内」（add の「抽出 − 既登録」と逆、§3.2）、退会者含む。
# bulk_delete で確定、PRG で詳細画面へ。session キー mailing_list_<pk>_remove_by_tag_state。
#
# in_list_count（=除外対象）/ out_of_list_count（=リスト外）の中立別名を読むこと、
# archived Person はタグ経由では外れないこと、UI 誤操作防止 8 項目（特に二重確認）
# も検証。タグで追加（β-3a）と独立に動くことも確認。


def _remove_by_tag_session_key(mailing_list_pk):
    return f"mailing_list_{mailing_list_pk}_remove_by_tag_state"


class _RemoveByTagTestBase(_AddByTagTestBase):
    """β-3a の _AddByTagTestBase を継承（同じ mailing_list / cat / tag セットを使う）。"""

    def _post_conditions(self, *, include=(), operator="OR"):
        post = {}
        cat_id = str(self.cat_role.pk)
        if include:
            post[f"include_{cat_id}"] = [str(t.pk) for t in include]
            post[f"operator_{cat_id}"] = operator
        return post

    def _put_remove_state(self, **state):
        session = self.client.session
        session[_remove_by_tag_session_key(self.mailing_list.pk)] = state
        session.save()

    def _remove_state(self):
        return self.client.session.get(
            _remove_by_tag_session_key(self.mailing_list.pk)
        ) or {}


class MemberRemoveByTagViewGetTests(_RemoveByTagTestBase):
    def test_get_200_renders_red_banner(self):
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        # 赤バナー（app-message--error）+ タブタイトル + 「除外」文言
        self.assertIn("タグで除外：L1", body)
        self.assertIn("app-message--error", body)
        # タブタイトル「タグで除外 - ◯◯リスト」（§4.6.5.4-3）
        # {% block title %} 内に改行が含まれるため <title> 直後一致ではなく
        # 「タグで除外 - L1 — FreeGroup2」のフレーズ単位で検証する。
        self.assertIn("タグで除外 - L1 — FreeGroup2", body)

    def test_get_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 409)

    def test_get_archived_returns_404(self):
        self.mailing_list.is_archived = True
        self.mailing_list.save(update_fields=["is_archived", "updated_at"])
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_template_has_list_id_data_attribute(self):
        """JS が dataset.listId で読むため、data-list-id 出力必須。"""
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertIn(f'data-list-id="{self.mailing_list.pk}"', body)


class MemberRemoveByTagViewPostTests(_RemoveByTagTestBase):
    def test_post_snapshot_is_intersection_of_extracted_and_in_list(self):
        """snapshot は「抽出 ∩ リスト内」（§3.2、add の『抽出 − 既登録』と逆）。"""
        p_in_extracted = self._make_list_person("InAndExtracted", in_list=True)
        p_in_not_extracted = self._make_list_person("InOnly", in_list=True)
        p_extracted_not_in = self._make_list_person("ExtractedOnly")
        self._assign(p_in_extracted, self.t_role_a)
        self._assign(p_extracted_not_in, self.t_role_a)
        # p_in_not_extracted はリスト内だがタグ無し → 抽出されない
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        state = self._remove_state()
        self.assertEqual(state["snapshot_person_ids"], [str(p_in_extracted.pk)])
        self.assertEqual(state["total_extracted"], 2)  # 抽出 = 2 件
        self.assertEqual(state["out_of_list_count"], 1)  # リスト外 = 1 件

    def test_post_snapshot_does_not_include_out_of_list(self):
        """リスト外（リストに居ない Person）は snapshot に入らない（§3.2）。"""
        p_out = self._make_list_person("Out")  # リスト外
        self._assign(p_out, self.t_role_a)
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        state = self._remove_state()
        # snapshot 空（除外対象 0、リスト外 1）
        self.assertEqual(state["snapshot_person_ids"], [])
        self.assertEqual(state["total_extracted"], 1)
        self.assertEqual(state["out_of_list_count"], 1)

    def test_post_snapshot_includes_unsubscribed_member(self):
        """退会者もリスト内かつ抽出されれば snapshot に含む（個別削除と整合、§9.2-30）。"""
        p = self._make_list_person("Unsub", is_unsubscribed=True, in_list=True)
        self._assign(p, self.t_role_a)
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self._remove_state()["snapshot_person_ids"], [str(p.pk)]
        )

    def test_post_archived_person_in_list_not_extracted(self):
        """archived Person はリスト内に居てもタグ経由で外せない（§3.6 / §4.6.5.5）。

        preview-v2 が active のみ抽出 → 抽出 ∩ リスト内に archived は入らない。
        """
        p_arch = self._make_list_person("Arch", status="archived", in_list=True)
        self._assign(p_arch, self.t_role_a)
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        state = self._remove_state()
        self.assertEqual(state["snapshot_person_ids"], [])
        self.assertEqual(state["total_extracted"], 0)

    def test_post_empty_conditions_returns_400(self):
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(url, {})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._remove_state(), {})

    def test_post_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        p = self._make_list_person("Alice", in_list=True)
        self._assign(p, self.t_role_a)
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 409)

    def test_post_redirects_to_confirm(self):
        p = self._make_list_person("Alice", in_list=True)
        self._assign(p, self.t_role_a)
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        confirm_url = reverse(
            "mailings:list_member_remove_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(
            url, self._post_conditions(include=[self.t_role_a])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(confirm_url, resp["Location"])

    def test_remove_and_add_sessions_are_independent(self):
        """remove_by_tag と add_by_tag の session キーが衝突しない（§3.4）。"""
        # add の state を入れる
        session = self.client.session
        session[_add_by_tag_session_key(self.mailing_list.pk)] = {
            "conditions": {"categories": [], "global_exclude_tag_ids": []},
            "snapshot_person_ids": [str(uuid.uuid4())],
            "total_extracted": 1,
            "already_in_list_count": 0,
        }
        session.save()
        # remove 側で POST しても add の state を上書きしない
        p = self._make_list_person("Alice", in_list=True)
        self._assign(p, self.t_role_a)
        url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        self.client.post(url, self._post_conditions(include=[self.t_role_a]))
        # add の state は保持
        add_state = self.client.session.get(
            _add_by_tag_session_key(self.mailing_list.pk)
        )
        self.assertIsNotNone(add_state)
        self.assertEqual(len(add_state["snapshot_person_ids"]), 1)
        # remove の state も別キーで保存される
        self.assertIn(str(p.pk), self._remove_state()["snapshot_person_ids"])


class MemberRemoveByTagConfirmViewTests(_RemoveByTagTestBase):
    def test_empty_session_redirects_to_selection(self):
        """session 空（conditions なし）→ 1-L に 302（§9.2-45）。"""
        url = reverse(
            "mailings:list_member_remove_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        sel = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(sel, resp["Location"])

    def test_renders_after_count_with_minus(self):
        p_in = self._make_list_person("Alice", in_list=True)
        # 既登録の追加メンバー（snapshot 外、現在件数を増やすため）
        p_other = self._make_list_person("Bob", in_list=True)
        self._put_remove_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p_in.pk)],
            total_extracted=1,
            out_of_list_count=0,
        )
        url = reverse(
            "mailings:list_member_remove_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertEqual(ctx["removed_count"], 1)
        self.assertEqual(ctx["current_member_count"], 2)
        self.assertEqual(ctx["after_count"], 1)
        body = resp.content.decode("utf-8")
        # 「-N 件」赤マイナス表示（§4.6.5.4-6）
        self.assertIn("→ -1 件", body)
        # 「外します」強調（§4.6.5.4-5）
        self.assertIn("<strong>外します</strong>", body)
        # 「除外を確定」赤ボタン（§4.6.5.4-7）
        self.assertIn(">除外を確定</button>", body)
        # 二重確認ダイアログのメッセージ属性（§4.6.5.4-8）
        self.assertIn('data-confirm-message="1 件をリストから除外します。', body)

    def test_renders_unsubscribed_badge(self):
        p = self._make_list_person("Unsub", is_unsubscribed=True, in_list=True)
        self._put_remove_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p.pk)],
            total_extracted=1,
            out_of_list_count=0,
        )
        url = reverse(
            "mailings:list_member_remove_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertIn("js-unsubscribed-badge", resp.content.decode("utf-8"))

    def test_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        self._put_remove_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(uuid.uuid4())],
            total_extracted=1,
            out_of_list_count=0,
        )
        url = reverse(
            "mailings:list_member_remove_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 409)

    def test_post_method_returns_405(self):
        url = reverse(
            "mailings:list_member_remove_by_tag_confirm",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 405)


class MemberRemoveByTagCommitViewTests(_RemoveByTagTestBase):
    def test_commit_deletes_only_snapshot_members(self):
        """bulk_delete は snapshot の Person のみ削除（リスト内の他は残る）。"""
        p_remove = self._make_list_person("Remove", in_list=True)
        p_stay = self._make_list_person("Stay", in_list=True)
        self._put_remove_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p_remove.pk)],
            total_extracted=1,
            out_of_list_count=0,
        )
        url = reverse(
            "mailings:list_member_commit_remove_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse(
                "mailings:mailing_list_detail", args=[self.mailing_list.pk]
            ),
            resp["Location"],
        )
        # snapshot の Person は削除、他は残る
        self.assertFalse(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list, person=p_remove
            ).exists()
        )
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list, person=p_stay
            ).exists()
        )
        # session クリア
        self.assertEqual(self._remove_state(), {})

    def test_commit_does_not_affect_other_lists(self):
        """同じ Person が他リストに居ても、対象リストのメンバーシップのみ削除。"""
        other = MailingList.objects.create(name="OtherList", created_by=self.user)
        p = self._make_list_person("Shared", in_list=True)
        MailingListMember.objects.create(
            mailing_list=other, person=p, added_by=self.user
        )
        self._put_remove_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p.pk)],
            total_extracted=1,
            out_of_list_count=0,
        )
        url = reverse(
            "mailings:list_member_commit_remove_by_tag",
            args=[self.mailing_list.pk],
        )
        self.client.post(url)
        self.assertFalse(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list, person=p
            ).exists()
        )
        self.assertTrue(
            MailingListMember.objects.filter(
                mailing_list=other, person=p
            ).exists()
        )

    def test_commit_empty_session_redirects_to_selection(self):
        url = reverse(
            "mailings:list_member_commit_remove_by_tag",
            args=[self.mailing_list.pk],
        )
        sel = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(sel, resp["Location"])

    def test_commit_get_method_returns_405(self):
        url = reverse(
            "mailings:list_member_commit_remove_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 405)

    def test_commit_frozen_returns_409(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        p = self._make_list_person("Alice", in_list=True)
        self._put_remove_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p.pk)],
            total_extracted=1,
            out_of_list_count=0,
        )
        url = reverse(
            "mailings:list_member_commit_remove_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 409)

    def test_commit_snapshot_unchanged_by_db_mutation(self):
        """snapshot 方式：POST 後の Person タグ変更が確定結果に影響しない（§9.2-26）。"""
        p = self._make_list_person("Alice", in_list=True)
        self._assign(p, self.t_role_a)
        post_url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        self.client.post(
            post_url, self._post_conditions(include=[self.t_role_a])
        )
        # snapshot 確定後にタグを剝がしても、commit は snapshot ID で進む
        TagAssignment.objects.filter(person=p).delete()
        commit_url = reverse(
            "mailings:list_member_commit_remove_by_tag",
            args=[self.mailing_list.pk],
        )
        resp = self.client.post(commit_url)
        self.assertEqual(resp.status_code, 302)
        # snapshot 通り p が削除される
        self.assertFalse(
            MailingListMember.objects.filter(
                mailing_list=self.mailing_list, person=p
            ).exists()
        )

    def test_prg_replay_does_not_double_delete(self):
        """PRG：commit 2 回目は session クリア済みで 1-L に差し戻し（二重削除なし）。"""
        p = self._make_list_person("Alice", in_list=True)
        self._put_remove_state(
            conditions={"categories": [], "global_exclude_tag_ids": []},
            snapshot_person_ids=[str(p.pk)],
            total_extracted=1,
            out_of_list_count=0,
        )
        commit_url = reverse(
            "mailings:list_member_commit_remove_by_tag",
            args=[self.mailing_list.pk],
        )
        resp1 = self.client.post(commit_url)
        self.assertEqual(resp1.status_code, 302)
        # 2 回目：session クリア済み、削除済み Person 自体も既に存在しない
        resp2 = self.client.post(commit_url)
        self.assertEqual(resp2.status_code, 302)
        self.assertIn(
            reverse(
                "mailings:list_member_remove_by_tag",
                args=[self.mailing_list.pk],
            ),
            resp2["Location"],
        )


class DetailViewRemoveByTagButtonTests(_RemoveByTagTestBase):
    def test_detail_shows_remove_by_tag_button_active(self):
        url = reverse(
            "mailings:mailing_list_detail", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        remove_by_tag_url = reverse(
            "mailings:list_member_remove_by_tag", args=[self.mailing_list.pk]
        )
        self.assertIn(remove_by_tag_url, body)
        self.assertIn(">タグで除外</a>", body)
        # 削除系の行＝赤ボタン
        # 削除系の行は <a ... class="app-btn app-btn--danger">タグで除外</a>
        self.assertIn('class="app-btn app-btn--danger">タグで除外</a>', body)

    def test_detail_frozen_disables_remove_by_tag_button(self):
        self.mailing_list.members_frozen_at = timezone.now()
        self.mailing_list.save(update_fields=["members_frozen_at", "updated_at"])
        url = reverse(
            "mailings:mailing_list_detail", args=[self.mailing_list.pk]
        )
        resp = self.client.get(url)
        body = resp.content.decode("utf-8")
        self.assertNotIn(
            f'href="{reverse("mailings:list_member_remove_by_tag", args=[self.mailing_list.pk])}',
            body,
        )
        self.assertIn(">タグで除外</span>", body)


class PreviewV2NeutralAliasIntegrationTests(_RemoveByTagTestBase):
    """preview-v2 が in_list_count == already_in_list、out_of_list_count == new_count
    のエイリアス関係を保つことを再検証（§3.1 配線ミス防止の構造的根拠）。
    """

    def test_aliases_match_in_real_response(self):
        p_in = self._make_list_person("InList", in_list=True)
        p_out = self._make_list_person("OutList")
        self._assign(p_in, self.t_role_a)
        self._assign(p_out, self.t_role_a)
        body = {
            "list_id": str(self.mailing_list.pk),
            "conditions": {
                "categories": [
                    {
                        "category_id": str(self.cat_role.pk),
                        "operator": "OR",
                        "include_tag_ids": [str(self.t_role_a.pk)],
                        "exclude_tag_ids": [],
                    }
                ],
                "global_exclude_tag_ids": [],
            },
        }
        resp = self.client.post(
            reverse("mailings:mailing_list_preview_v2"),
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_count"], 2)
        self.assertEqual(data["in_list_count"], data["already_in_list"])
        self.assertEqual(data["out_of_list_count"], data["new_count"])
        self.assertEqual(data["in_list_count"], 1)
        self.assertEqual(data["out_of_list_count"], 1)


# ======================================================================
# Phase 2：配信系モデル骨格テスト（仕様書 §4.2 / §4.4 / §4.5 / §4.5A /
# §4.6 / §4.7 / §4.8 / §4.8A、別表 C.21〜C.26）。
# ======================================================================


from datetime import timedelta

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction

from mailings.models import (
    Campaign,
    ClickLog,
    DeliveryHistory,
    EmailTemplate,
    SoftBounceCounter,
    SuppressedEmail,
    TrackingLink,
    Unsubscribe,
    UnsubscribeLink,
)


class _Phase2ModelTestBase(TestCase):
    """Phase 2 配信系モデルテストの共通基底。

    Campaign を作るために必要な依存物（EmailTemplate / MailingList / Person /
    CustomUser）を一通り用意する。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="phase2_user", password="x")
        self.other_user = User.objects.create_user(
            username="phase2_other", password="x"
        )
        self.template = EmailTemplate.objects.create(
            name="T1",
            subject="件名",
            body="本文",
            created_by=self.user,
        )
        self.mailing_list = MailingList.objects.create(
            name="ML1", created_by=self.user
        )
        self.person = self._make_person("Alice")
        self.person2 = self._make_person("Bob")

    def _make_person(self, full_name):
        p = Person.objects.create(status="active")
        c = Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            email=f"{full_name.lower()}@example.com",
            created_by=self.user,
        )
        p.primary_contact = c
        p.save(update_fields=["primary_contact", "updated_at"])
        return p

    def _make_campaign(self, *, status=Campaign.Status.DRAFT, scheduled_at=None):
        # rev18 §4.2：Campaign.template は OneToOneField。同一 setUp 内で
        # 複数 Campaign を作るテストがあるため、_make_campaign は呼び出し毎に
        # 新規 EmailTemplate を生成する（共有不可）。
        template = EmailTemplate.objects.create(
            name=f"T_{uuid.uuid4().hex[:8]}",
            subject="件名",
            body="本文",
            created_by=self.user,
        )
        return Campaign.objects.create(
            name="C1",
            template=template,
            mailing_list=self.mailing_list,
            scheduled_at=scheduled_at,
            status=status,
            created_by=self.user,
        )


class CampaignModelTests(_Phase2ModelTestBase):
    def test_create_minimal(self):
        c = self._make_campaign()
        self.assertIsNotNone(c.pk)
        self.assertEqual(c.status, Campaign.Status.DRAFT)
        self.assertEqual(c.sender_mode, Campaign.SenderMode.CREATOR)
        self.assertTrue(c.apply_unsubscribe_filter)
        self.assertEqual(c.total_count, 0)
        self.assertEqual(c.sent_count, 0)
        self.assertEqual(c.failed_count, 0)

    def test_status_choices_5_values(self):
        values = {v for v, _ in Campaign.Status.choices}
        self.assertEqual(
            values, {"draft", "scheduled", "sending", "done", "failed"}
        )

    def test_sender_mode_choices_2_values(self):
        values = {v for v, _ in Campaign.SenderMode.choices}
        self.assertEqual(values, {"creator", "newsletter"})

    def test_mailing_list_is_required(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Campaign.objects.create(
                    name="C2",
                    template=self.template,
                    mailing_list=None,
                    created_by=self.user,
                )

    def test_view_all_campaigns_permission_exists(self):
        ct = ContentType.objects.get_for_model(Campaign)
        self.assertTrue(
            Permission.objects.filter(
                content_type=ct, codename="view_all_campaigns"
            ).exists()
        )


class CampaignGetPendingScheduledTests(_Phase2ModelTestBase):
    def test_returns_only_scheduled_with_past_scheduled_at(self):
        now = timezone.now()
        past = self._make_campaign(
            status=Campaign.Status.SCHEDULED, scheduled_at=now - timedelta(minutes=5)
        )
        # future scheduled は除外
        self._make_campaign(
            status=Campaign.Status.SCHEDULED, scheduled_at=now + timedelta(minutes=5)
        )
        # draft は除外（scheduled_at=NULL）
        self._make_campaign(status=Campaign.Status.DRAFT)
        # 既に sending / done は除外
        self._make_campaign(
            status=Campaign.Status.SENDING, scheduled_at=now - timedelta(minutes=5)
        )
        self._make_campaign(
            status=Campaign.Status.DONE, scheduled_at=now - timedelta(minutes=5)
        )
        result = list(Campaign.get_pending_scheduled())
        self.assertEqual(result, [past])

    def test_exact_now_boundary_included(self):
        now = timezone.now()
        c = self._make_campaign(
            status=Campaign.Status.SCHEDULED, scheduled_at=now - timedelta(seconds=1)
        )
        self.assertIn(c, Campaign.get_pending_scheduled())


class CampaignHasViewPermissionTests(_Phase2ModelTestBase):
    def test_creator_can_view(self):
        c = self._make_campaign()
        self.assertTrue(c.has_view_permission(self.user))

    def test_other_user_without_permission_cannot_view(self):
        c = self._make_campaign()
        self.assertFalse(c.has_view_permission(self.other_user))

    def test_user_with_view_all_campaigns_permission_can_view(self):
        perm = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(Campaign),
            codename="view_all_campaigns",
        )
        self.other_user.user_permissions.add(perm)
        # has_perm キャッシュ回避のため再取得
        other = User.objects.get(pk=self.other_user.pk)
        c = self._make_campaign()
        self.assertTrue(c.has_view_permission(other))


class DeliveryHistoryModelTests(_Phase2ModelTestBase):
    def test_create_with_snapshot_fields(self):
        c = self._make_campaign()
        dh = DeliveryHistory.objects.create(
            campaign=c,
            person=self.person,
            to_email="alice@example.com",
            organization_at_send="Acme",
            full_name_at_send="Alice Tanaka",
            salutation_name_at_send="田中 様",
        )
        self.assertEqual(dh.status, DeliveryHistory.Status.PENDING)
        self.assertEqual(dh.organization_at_send, "Acme")

    def test_snapshot_fields_default_empty(self):
        c = self._make_campaign()
        dh = DeliveryHistory.objects.create(
            campaign=c, person=self.person, to_email="alice@example.com"
        )
        self.assertEqual(dh.organization_at_send, "")
        self.assertEqual(dh.full_name_at_send, "")
        self.assertEqual(dh.salutation_name_at_send, "")
        self.assertEqual(dh.error_message, "")

    def test_unique_campaign_person(self):
        c = self._make_campaign()
        DeliveryHistory.objects.create(
            campaign=c, person=self.person, to_email="alice@example.com"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DeliveryHistory.objects.create(
                    campaign=c, person=self.person, to_email="alice2@example.com"
                )

    def test_same_person_different_campaign_ok(self):
        c1 = self._make_campaign()
        # rev18 §4.2：template は OneToOne。c2 用に別 template を用意する。
        c2 = Campaign.objects.create(
            name="C2",
            template=EmailTemplate.objects.create(
                name="T2", subject="件名", body="本文", created_by=self.user
            ),
            mailing_list=self.mailing_list,
            created_by=self.user,
        )
        DeliveryHistory.objects.create(
            campaign=c1, person=self.person, to_email="alice@example.com"
        )
        DeliveryHistory.objects.create(
            campaign=c2, person=self.person, to_email="alice@example.com"
        )

    def test_status_choices_5_values(self):
        values = {v for v, _ in DeliveryHistory.Status.choices}
        self.assertEqual(
            values, {"pending", "sent", "failed", "bounced", "unsubscribed"}
        )


class TrackingLinkModelTests(_Phase2ModelTestBase):
    def test_create_minimal(self):
        c = self._make_campaign()
        tl = TrackingLink.objects.create(
            campaign=c,
            person=self.person,
            original_url="https://example.com/a",
            token="tok_abc12345",
        )
        self.assertEqual(tl.click_count, 0)
        self.assertEqual(tl.total_access_count, 0)
        self.assertIsNone(tl.last_clicked_at)

    def test_unique_campaign_person_original_url(self):
        c = self._make_campaign()
        TrackingLink.objects.create(
            campaign=c,
            person=self.person,
            original_url="https://example.com/a",
            token="tok_aaaaaaaa",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TrackingLink.objects.create(
                    campaign=c,
                    person=self.person,
                    original_url="https://example.com/a",
                    token="tok_bbbbbbbb",
                )

    def test_same_campaign_person_different_url_ok(self):
        c = self._make_campaign()
        TrackingLink.objects.create(
            campaign=c,
            person=self.person,
            original_url="https://example.com/a",
            token="tok_aaaaaaaa",
        )
        TrackingLink.objects.create(
            campaign=c,
            person=self.person,
            original_url="https://example.com/b",
            token="tok_bbbbbbbb",
        )

    def test_token_unique_across_campaigns(self):
        c1 = self._make_campaign()
        # rev18 §4.2：template は OneToOne。c2 用に別 template を用意する。
        c2 = Campaign.objects.create(
            name="C2",
            template=EmailTemplate.objects.create(
                name="T2", subject="件名", body="本文", created_by=self.user
            ),
            mailing_list=self.mailing_list,
            created_by=self.user,
        )
        TrackingLink.objects.create(
            campaign=c1,
            person=self.person,
            original_url="https://example.com/a",
            token="dup_token",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TrackingLink.objects.create(
                    campaign=c2,
                    person=self.person2,
                    original_url="https://example.com/x",
                    token="dup_token",
                )


class UnsubscribeLinkModelTests(_Phase2ModelTestBase):
    def test_create_minimal(self):
        c = self._make_campaign()
        ul = UnsubscribeLink.objects.create(
            campaign=c,
            person=self.person,
            token="utok_abcdef12",
            target_email="alice@example.com",
        )
        self.assertIsNone(ul.accessed_at)
        self.assertIsNone(ul.unsubscribed_at)

    def test_unique_campaign_person(self):
        c = self._make_campaign()
        UnsubscribeLink.objects.create(
            campaign=c,
            person=self.person,
            token="utok_aaaaaaaa",
            target_email="alice@example.com",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UnsubscribeLink.objects.create(
                    campaign=c,
                    person=self.person,
                    token="utok_bbbbbbbb",
                    target_email="alice@example.com",
                )

    def test_token_unique_across_campaigns(self):
        c1 = self._make_campaign()
        # rev18 §4.2：template は OneToOne。c2 用に別 template を用意する。
        c2 = Campaign.objects.create(
            name="C2",
            template=EmailTemplate.objects.create(
                name="T2", subject="件名", body="本文", created_by=self.user
            ),
            mailing_list=self.mailing_list,
            created_by=self.user,
        )
        UnsubscribeLink.objects.create(
            campaign=c1,
            person=self.person,
            token="udup_token",
            target_email="alice@example.com",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UnsubscribeLink.objects.create(
                    campaign=c2,
                    person=self.person2,
                    token="udup_token",
                    target_email="bob@example.com",
                )


class ClickLogModelTests(_Phase2ModelTestBase):
    def test_create_valid_click(self):
        c = self._make_campaign()
        tl = TrackingLink.objects.create(
            campaign=c,
            person=self.person,
            original_url="https://example.com/a",
            token="tok_a",
        )
        log = ClickLog.objects.create(
            tracking_link=tl,
            http_method="GET",
            is_valid_click=True,
        )
        self.assertEqual(log.bot_reason, "")
        self.assertEqual(log.user_agent, "")

    def test_bot_reason_choices_5_values(self):
        values = {v for v, _ in ClickLog.BotReason.choices}
        self.assertEqual(
            values, {"non_get", "known_bot", "too_fast", "duplicate", "test_send"}
        )


class UnsubscribeModelTests(_Phase2ModelTestBase):
    def test_create_minimal(self):
        u = Unsubscribe.objects.create(
            person=self.person,
            source_email="alice@example.com",
            source=Unsubscribe.Source.UNSUBSCRIBE_LINK,
        )
        self.assertIsNone(u.cancelled_at)
        self.assertIsNone(u.cancelled_by)

    def test_source_choices_3_values_no_bounce(self):
        values = {v for v, _ in Unsubscribe.Source.choices}
        self.assertEqual(values, {"unsubscribe_link", "manual", "api"})
        self.assertNotIn("bounce", values)

    def test_bulk_create_supported(self):
        # bulk_create で save() オーバーライド・signal の干渉がないことを担保（§9.5.3）。
        objs = [
            Unsubscribe(
                person=self.person,
                source_email="alice@example.com",
                source=Unsubscribe.Source.MANUAL,
            ),
            Unsubscribe(
                person=self.person2,
                source_email="bob@example.com",
                source=Unsubscribe.Source.MANUAL,
            ),
        ]
        created = Unsubscribe.objects.bulk_create(objs)
        self.assertEqual(len(created), 2)
        self.assertEqual(Unsubscribe.objects.count(), 2)


class SuppressedEmailPartialUniqueTests(_Phase2ModelTestBase):
    def test_create_minimal(self):
        s = SuppressedEmail.objects.create(
            email="bad@example.com", source=SuppressedEmail.Source.BOUNCE_HARD
        )
        self.assertIsNone(s.cancelled_at)
        self.assertEqual(s.bounce_reason, "")

    def test_active_duplicate_email_rejected(self):
        SuppressedEmail.objects.create(
            email="dup@example.com", source=SuppressedEmail.Source.BOUNCE_HARD
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SuppressedEmail.objects.create(
                    email="dup@example.com",
                    source=SuppressedEmail.Source.MANUAL,
                )

    def test_cancelled_duplicate_email_allowed(self):
        # 解除済みは email 重複可（履歴として残せる）。
        SuppressedEmail.objects.create(
            email="hist@example.com",
            source=SuppressedEmail.Source.BOUNCE_HARD,
            cancelled_at=timezone.now(),
            cancelled_by=self.user,
        )
        SuppressedEmail.objects.create(
            email="hist@example.com",
            source=SuppressedEmail.Source.MANUAL,
            cancelled_at=timezone.now(),
            cancelled_by=self.user,
        )
        self.assertEqual(
            SuppressedEmail.objects.filter(email="hist@example.com").count(), 2
        )

    def test_cancel_then_readd_ok(self):
        # 解除 → 再追加が IntegrityError なしで可能（§4.8.1 の主目的）。
        s1 = SuppressedEmail.objects.create(
            email="recycle@example.com", source=SuppressedEmail.Source.BOUNCE_HARD
        )
        s1.cancelled_at = timezone.now()
        s1.cancelled_by = self.user
        s1.save()
        # 解除後の再追加は OK
        SuppressedEmail.objects.create(
            email="recycle@example.com", source=SuppressedEmail.Source.BOUNCE_HARD
        )

    def test_source_choices_5_values(self):
        values = {v for v, _ in SuppressedEmail.Source.choices}
        self.assertEqual(
            values,
            {
                "unsubscribe_generic",
                "bounce_hard",
                "bounce_soft_promoted",
                "manual",
                "api",
            },
        )


class SoftBounceCounterModelTests(_Phase2ModelTestBase):
    def test_create_minimal(self):
        sbc = SoftBounceCounter.objects.create(
            email="soft@example.com", count=1, last_bounce_at=timezone.now()
        )
        self.assertEqual(sbc.count, 1)

    def test_email_unique_no_partial(self):
        # SuppressedEmail と違い素の unique=True（§4.8A）。
        # cancelled_at=NULL 等の条件付きではなく、解除済み概念なし。
        SoftBounceCounter.objects.create(
            email="dup@example.com", count=1, last_bounce_at=timezone.now()
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SoftBounceCounter.objects.create(
                    email="dup@example.com",
                    count=1,
                    last_bounce_at=timezone.now(),
                )


# ======================================================================
# Phase 3：配信エンジン（EmailContext + 差し込み + cron 駆動配信実行）
# 仕様書 §4.2.1 / §7.2 / §7.3 / §7.4 / §7.8 / §16.1（rev14）
# ======================================================================


from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from io import StringIO

from actionlogs.models import ActionLog
from config.constants import (
    CAMPAIGN_RECIPIENT_MAX_FAILURES,
    CAMPAIGN_SEND_BATCH_LIMIT,
    MAILING_SITE_URL,
)
from mailings.models import MailingConfig
from mailings.services.audit import (
    ACTION_SEND_CAMPAIGN,
    ACTION_UNSUBSCRIBE_FILTER_OFF_SEND,
)
from mailings.services.campaign_send import (
    execute_campaign_send,
    send_one_recipient,
)
from mailings.services.email_context import EmailContext, EmailMode
from mailings.services.recipient_extraction import extract_recipients
from mailings.services.sender_domain import validate_sender_domain


class _Phase3TestBase(TestCase):
    """Phase 3 配信エンジンテストの共通基底。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="phase3_user",
            password="x",
            email="taro@example.com",
            first_name="太郎",
            last_name="山田",
        )
        self.user.signature = "山田 太郎\n株式会社サンプル"
        self.user.save(update_fields=["signature"])
        self.template = EmailTemplate.objects.create(
            name="T1",
            subject="{{会社名}}の{{宛名}}へ：ご案内",
            body=(
                "{{宛名}}\n\n"
                "いつもお世話になっております。\n"
                "{{会社名}} {{部署}} {{役職}} {{フルネーム}} 様\n\n"
                "詳しくはこちら：{% tracked_link %}https://www.sample.com{% endtracked_link %}\n"
                'カタログ：{% tracked_link "https://catalog.example.com" %}カタログを見る{% endtracked_link %}\n\n'
                "{% unsubscribe_link %}配信停止はこちら{% endunsubscribe_link %}\n\n"
                "{{差出人の氏名}}\n{{差出人の署名}}"
            ),
            created_by=self.user,
        )
        self.mailing_list = MailingList.objects.create(
            name="ML1", created_by=self.user
        )
        self.config = MailingConfig.objects.create(
            company_name="株式会社サンプル",
            company_address="東京都千代田区1-1-1",
            unsubscribe_contact="unsubscribe@example.com",
            dkim_domain="example.com",
        )

    def _make_person(self, full_name="Alice Tanaka", *, organization="Acme", email=None):
        p = Person.objects.create(status="active")
        c = Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            last_name="田中",
            first_name="アリス",
            salutation_name="田中 様",
            organization=organization,
            department="営業部",
            title="部長",
            branch="渋谷支店",
            address="東京都渋谷区1-2-3",
            postal_code="150-0001",
            email=email if email is not None else "alice@example.com",
            personal_phone="03-1111-2222",
            org_phone="03-9999-8888",
            mobile_phone="090-1111-2222",
            personal_fax="03-1111-2223",
            org_fax="03-9999-8889",
            created_by=self.user,
        )
        p.primary_contact = c
        p.save(update_fields=["primary_contact", "updated_at"])
        return p

    def _make_campaign(
        self,
        *,
        scheduled_at=None,
        status=Campaign.Status.SCHEDULED,
        sender_mode=Campaign.SenderMode.CREATOR,
        sender_email="",
        sender_name="",
        apply_unsubscribe_filter=True,
    ):
        return Campaign.objects.create(
            name="C1",
            template=self.template,
            mailing_list=self.mailing_list,
            scheduled_at=scheduled_at or (timezone.now() - timedelta(minutes=1)),
            status=status,
            sender_mode=sender_mode,
            sender_email=sender_email,
            sender_name=sender_name,
            apply_unsubscribe_filter=apply_unsubscribe_filter,
            created_by=self.user,
        )

    def _add_member(self, mailing_list, person):
        return MailingListMember.objects.create(
            mailing_list=mailing_list, person=person, added_by=self.user
        )


class EmailModeEnumTests(_Phase3TestBase):
    def test_enum_3_values(self):
        self.assertEqual(EmailMode.PRODUCTION.value, "本番送信")
        self.assertEqual(EmailMode.TEST.value, "テスト配信")
        self.assertEqual(EmailMode.PREVIEW.value, "プレビュー")
        self.assertEqual(len(EmailMode), 3)

    def test_prepare_rejects_string_literal_mode(self):
        person = self._make_person()
        campaign = self._make_campaign()
        with self.assertRaises(TypeError):
            EmailContext.prepare(campaign, person, "本番送信")


class EmailContextPrepareProductionTests(_Phase3TestBase):
    def test_18_merge_fields_expand(self):
        person = self._make_person()
        campaign = self._make_campaign()
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        # 差し込みが効いていること（HTML エスケープ後でも日本語値はそのまま含まれる）
        self.assertIn("Acme", ctx.body_html)
        self.assertIn("田中 様", ctx.body_html)
        self.assertIn("営業部", ctx.body_html)
        self.assertIn("部長", ctx.body_html)
        self.assertIn("Alice Tanaka", ctx.body_html)
        # 件名にも差し込みが効く（HTML エスケープしない）
        self.assertIn("Acme", ctx.subject_rendered)
        self.assertIn("田中 様", ctx.subject_rendered)
        # 差出人系
        self.assertIn("山田 太郎", ctx.body_html)
        self.assertIn("株式会社サンプル", ctx.body_html)

    def test_empty_value_leaves_placeholder_intact(self):
        # branch=空のテンプレに「{{支店}}」を入れた場合、Sansan 互換で記法が残る（§7.4.5）
        person = self._make_person()
        person.primary_contact.branch = ""
        person.primary_contact.save(update_fields=["branch"])
        tpl = EmailTemplate.objects.create(
            name="T2", subject="件名", body="支店：{{支店}}", created_by=self.user
        )
        campaign = Campaign.objects.create(
            name="C2",
            template=tpl,
            mailing_list=self.mailing_list,
            scheduled_at=timezone.now(),
            created_by=self.user,
        )
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        # {{支店}} が HTML エスケープされて &lbrace; 等にはならず、{ } のまま残る
        # （html.escape は { } をエスケープしない）
        self.assertIn("{{支店}}", ctx.body_html)

    def test_production_builds_tokens_and_links(self):
        person = self._make_person()
        campaign = self._make_campaign()
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        # トラッキングリンク 2 件（形態 1 と形態 2）、unsubscribe リンク 1 件
        self.assertEqual(len(ctx.tracking_links), 2)
        self.assertEqual(len(ctx.unsubscribe_links), 1)
        # トークンが発行されている
        for tl in ctx.tracking_links:
            self.assertTrue(tl.token)
            self.assertEqual(len(tl.token), 11)  # secrets.token_urlsafe(8) は約 11 文字
        # build 段階では DB に存在しない（UUIDField の default で pk は振られるが未保存）
        for tl in ctx.tracking_links:
            self.assertFalse(TrackingLink.objects.filter(pk=tl.pk).exists())
        for ul in ctx.unsubscribe_links:
            self.assertFalse(UnsubscribeLink.objects.filter(pk=ul.pk).exists())
        # 本文にトークン入り URL が埋め込まれる
        token = ctx.tracking_links[0].token
        self.assertIn(f"{MAILING_SITE_URL}/t/{token}/", ctx.body_html)
        utoken = ctx.unsubscribe_links[0].token
        self.assertIn(f"{MAILING_SITE_URL}/u/{utoken}/", ctx.body_html)

    def test_processing_order_no_link_breakage_when_body_contains_lt(self):
        """処理順事故防止（§7.4.4.5）：本文に '<' を含むケースでエスケープされ、
        かつカスタムタグ由来 <a> は壊れないことを担保する。
        """
        tpl = EmailTemplate.objects.create(
            name="T3",
            subject="件名",
            body=(
                "比較演算 a < b について\n"
                "詳しくは {% tracked_link %}https://example.com/a{% endtracked_link %}"
            ),
            created_by=self.user,
        )
        campaign = Campaign.objects.create(
            name="C3",
            template=tpl,
            mailing_list=self.mailing_list,
            scheduled_at=timezone.now(),
            created_by=self.user,
        )
        person = self._make_person()
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        # ユーザー入力の '<' はエスケープされている
        self.assertIn("a &lt; b", ctx.body_html)
        # カスタムタグ由来の <a> は生きている（&lt;a&gt; 化されていない）
        self.assertIn("<a href=", ctx.body_html)
        self.assertNotIn("&lt;a href=", ctx.body_html)

    def test_double_quote_in_body_escapes_and_tracked_link_form2_still_works(self):
        """仕様書 §7.4.4.5 ②：`"` も完全エスケープ。それでも形態 2 カスタムタグが生きる。

        本文に `"Pro"` を含み、同じ本文内に `{% tracked_link "URL" %}テキスト{% endtracked_link %}`
        が両立する。`"` は `&quot;` にエスケープされ、トラッキングリンクはトークン付き
        `<a>` として正常生成される（form 2 の `"URL"` 内 `"` も ② で `&quot;` 化されて
        いるが、本実装の正規表現は `&quot;` 前提で書かれているためマッチする）。
        """
        tpl = EmailTemplate.objects.create(
            name="T_QUOTE",
            subject="新製品 \"Pro\" シリーズのご案内",
            body=(
                "弊社の新製品 \"Pro\" シリーズは 15\" モニターと組み合わせて最適です。\n"
                "詳細：{% tracked_link \"https://example.com/pro?model=15&size=full\" %}\"Pro\"の詳細{% endtracked_link %}"
            ),
            created_by=self.user,
        )
        campaign = Campaign.objects.create(
            name="C_QUOTE",
            template=tpl,
            mailing_list=self.mailing_list,
            scheduled_at=timezone.now(),
            created_by=self.user,
        )
        person = self._make_person()
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)

        # ① ユーザー本文の `"` は `&quot;` にエスケープされている（仕様書 §7.4.4.5 ② 厳守）
        self.assertIn("&quot;Pro&quot;", ctx.body_html)
        self.assertIn("15&quot; モニター", ctx.body_html)
        # ② トラッキングリンクが正しく生成されている（形態 2 の `"URL"` がマッチ）
        self.assertEqual(len(ctx.tracking_links), 1)
        token = ctx.tracking_links[0].token
        self.assertIn(f'<a href="{MAILING_SITE_URL}/t/{token}/">', ctx.body_html)
        # ③ DB 保存される original_url は生 URL（unescape 済み、& は生のまま）
        self.assertEqual(
            ctx.tracking_links[0].original_url,
            "https://example.com/pro?model=15&size=full",
        )
        # ④ リンクテキストの `"Pro"の詳細` も `&quot;Pro&quot;の詳細` としてエスケープ済みで <a> 内に存在
        self.assertIn(f'>&quot;Pro&quot;の詳細</a>', ctx.body_html)
        # ⑤ 件名にも差し込みエスケープが効く（§7.4.4.4：件名はプレーンテキスト、HTML 化しない）
        # ※ 件名側は HTML 化しないため `"` はそのまま（_render_subject は _render_merge_field のみ）
        self.assertIn('"Pro"', ctx.subject_rendered)

    def test_newline_to_br_conversion(self):
        person = self._make_person()
        campaign = self._make_campaign()
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        # 改行が <br> に変換されている
        self.assertIn("<br>", ctx.body_html)
        # 生の \n は残っていない（HTML 化前のテンプレ）
        # ※ <br>\n の形にはなりうるが、ここでは雑に「行末の改行が <br> 化」だけ検証
        self.assertGreater(ctx.body_html.count("<br>"), 0)

    def test_sender_mode_creator_uses_created_by(self):
        person = self._make_person()
        campaign = self._make_campaign(sender_mode=Campaign.SenderMode.CREATOR)
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        self.assertEqual(ctx.from_email, self.user.email)

    def test_sender_mode_newsletter_uses_campaign_sender(self):
        person = self._make_person()
        campaign = self._make_campaign(
            sender_mode=Campaign.SenderMode.NEWSLETTER,
            sender_email="news@example.com",
            sender_name="メルマガ事務局",
        )
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        self.assertEqual(ctx.from_email, "news@example.com")
        self.assertEqual(ctx.from_name, "メルマガ事務局")

    def test_frozen_context_cannot_be_mutated(self):
        person = self._make_person()
        campaign = self._make_campaign()
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        with self.assertRaises(Exception):
            ctx.to_email = "evil@example.com"  # type: ignore[misc]


class EmailContextPrepareTestPreviewTests(_Phase3TestBase):
    def test_test_mode_no_token_generation(self):
        person = self._make_person()
        campaign = self._make_campaign()
        ctx = EmailContext.prepare(campaign, person, EmailMode.TEST)
        self.assertEqual(len(ctx.tracking_links), 0)
        self.assertEqual(len(ctx.unsubscribe_links), 0)
        # 素リンクに変換（トークンなしの URL）
        self.assertIn('href="https://www.sample.com"', ctx.body_html)
        self.assertIn('href="https://catalog.example.com"', ctx.body_html)
        # unsubscribe は /u/ 案内ルート（トークン無し独立ルート、§4.5A.2.1）
        self.assertIn(f'href="{MAILING_SITE_URL}/u/"', ctx.body_html)
        self.assertNotIn("/u/abc", ctx.body_html)  # トークン入りでないこと

    def test_preview_mode_no_token_generation(self):
        person = self._make_person()
        campaign = self._make_campaign()
        ctx = EmailContext.prepare(campaign, person, EmailMode.PREVIEW)
        self.assertEqual(len(ctx.tracking_links), 0)
        self.assertEqual(len(ctx.unsubscribe_links), 0)
        self.assertIn(f'href="{MAILING_SITE_URL}/u/"', ctx.body_html)


class RecipientExtractionTests(_Phase3TestBase):
    def test_returns_active_members(self):
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob", email="bob@example.com")
        self._add_member(self.mailing_list, p1)
        self._add_member(self.mailing_list, p2)
        campaign = self._make_campaign()
        result = list(extract_recipients(campaign).values_list("id", flat=True))
        self.assertCountEqual(result, [p1.id, p2.id])

    def test_excludes_merged_archived_persons(self):
        active = self._make_person("Alice")
        merged_p = self._make_person("Bob", email="bob@example.com")
        merged_p.status = "merged"
        merged_p.save(update_fields=["status"])
        archived_p = self._make_person("Carol", email="carol@example.com")
        archived_p.status = "archived"
        archived_p.save(update_fields=["status"])
        for p in (active, merged_p, archived_p):
            self._add_member(self.mailing_list, p)
        campaign = self._make_campaign()
        result = list(extract_recipients(campaign).values_list("id", flat=True))
        self.assertEqual(result, [active.id])

    def test_unsubscribe_filter_on_excludes_is_unsubscribed(self):
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob", email="bob@example.com")
        p2.is_unsubscribed = True
        p2.save(update_fields=["is_unsubscribed"])
        self._add_member(self.mailing_list, p1)
        self._add_member(self.mailing_list, p2)
        campaign = self._make_campaign(apply_unsubscribe_filter=True)
        result = list(extract_recipients(campaign).values_list("id", flat=True))
        self.assertEqual(result, [p1.id])

    def test_unsubscribe_filter_off_includes_is_unsubscribed(self):
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob", email="bob@example.com")
        p2.is_unsubscribed = True
        p2.save(update_fields=["is_unsubscribed"])
        self._add_member(self.mailing_list, p1)
        self._add_member(self.mailing_list, p2)
        campaign = self._make_campaign(apply_unsubscribe_filter=False)
        result = list(extract_recipients(campaign).values_list("id", flat=True))
        self.assertCountEqual(result, [p1.id, p2.id])

    def test_suppressed_email_always_excluded_even_with_filter_off(self):
        """§7.3 重大警告：SuppressedEmail フィルタは ON/OFF 不可、常時 ON 固定。"""
        p1 = self._make_person("Alice", email="alice@example.com")
        p2 = self._make_person("Bob", email="bob@example.com")
        SuppressedEmail.objects.create(
            email="bob@example.com",
            source=SuppressedEmail.Source.BOUNCE_HARD,
        )
        self._add_member(self.mailing_list, p1)
        self._add_member(self.mailing_list, p2)
        # フィルタ OFF でも SuppressedEmail は除外される
        campaign = self._make_campaign(apply_unsubscribe_filter=False)
        result = list(extract_recipients(campaign).values_list("id", flat=True))
        self.assertEqual(result, [p1.id])

    def test_cancelled_suppression_does_not_exclude(self):
        p1 = self._make_person("Alice")
        SuppressedEmail.objects.create(
            email="alice@example.com",
            source=SuppressedEmail.Source.BOUNCE_HARD,
            cancelled_at=timezone.now(),
            cancelled_by=self.user,
        )
        self._add_member(self.mailing_list, p1)
        campaign = self._make_campaign()
        result = list(extract_recipients(campaign).values_list("id", flat=True))
        self.assertEqual(result, [p1.id])

    def test_empty_email_excluded(self):
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob", email="")
        self._add_member(self.mailing_list, p1)
        self._add_member(self.mailing_list, p2)
        campaign = self._make_campaign()
        result = list(extract_recipients(campaign).values_list("id", flat=True))
        self.assertEqual(result, [p1.id])


class ValidateSenderDomainTests(_Phase3TestBase):
    def test_creator_mode_skip(self):
        # creator では検証しない（差出人 = created_by の個人メアド）
        campaign = self._make_campaign(sender_mode=Campaign.SenderMode.CREATOR)
        validate_sender_domain(campaign)  # 例外が出ないことを確認

    def test_newsletter_mode_within_dkim_domain_passes(self):
        campaign = self._make_campaign(
            sender_mode=Campaign.SenderMode.NEWSLETTER,
            sender_email="news@example.com",
            sender_name="メルマガ",
        )
        validate_sender_domain(campaign)

    def test_newsletter_mode_subdomain_passes(self):
        campaign = self._make_campaign(
            sender_mode=Campaign.SenderMode.NEWSLETTER,
            sender_email="news@mail.example.com",
            sender_name="メルマガ",
        )
        validate_sender_domain(campaign)

    def test_newsletter_mode_outside_domain_raises(self):
        campaign = self._make_campaign(
            sender_mode=Campaign.SenderMode.NEWSLETTER,
            sender_email="news@evil.com",
            sender_name="メルマガ",
        )
        with self.assertRaises(ValidationError):
            validate_sender_domain(campaign)

    def test_newsletter_mode_empty_sender_email_raises(self):
        campaign = self._make_campaign(
            sender_mode=Campaign.SenderMode.NEWSLETTER,
            sender_email="",
            sender_name="メルマガ",
        )
        with self.assertRaises(ValidationError):
            validate_sender_domain(campaign)


class SendOneRecipientTests(_Phase3TestBase):
    def setUp(self):
        super().setUp()
        self.person = self._make_person("Alice")
        self._add_member(self.mailing_list, self.person)
        self.campaign = self._make_campaign(status=Campaign.Status.SENDING)
        self.history = DeliveryHistory.objects.create(
            campaign=self.campaign,
            person=self.person,
            contact=self.person.primary_contact,
            to_email=self.person.primary_contact.email,
            status=DeliveryHistory.Status.PENDING,
        )

    def test_successful_send_sets_sent_status(self):
        mail.outbox = []
        result = send_one_recipient(self.campaign, self.history)
        self.assertTrue(result)
        self.history.refresh_from_db()
        self.assertEqual(self.history.status, DeliveryHistory.Status.SENT)
        self.assertIsNotNone(self.history.sent_at)
        self.assertEqual(self.history.failed_count, 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("alice@example.com", mail.outbox[0].to)

    def test_successful_send_persists_tracking_and_unsubscribe_links(self):
        send_one_recipient(self.campaign, self.history)
        self.assertEqual(
            TrackingLink.objects.filter(campaign=self.campaign).count(), 2
        )
        self.assertEqual(
            UnsubscribeLink.objects.filter(campaign=self.campaign).count(), 1
        )

    def test_failed_send_increments_failed_count_no_softbounce_touch(self):
        """【最重大事故防止】 送信失敗で SoftBounceCounter に一切触れない（§7.2.3）。"""
        mail.outbox = []
        with patch(
            "mailings.services.campaign_send._send_email_via_backend",
            side_effect=Exception("SMTP timeout"),
        ):
            result = send_one_recipient(self.campaign, self.history)
        self.assertFalse(result)
        self.history.refresh_from_db()
        self.assertEqual(self.history.status, DeliveryHistory.Status.FAILED)
        self.assertEqual(self.history.failed_count, 1)
        self.assertIsNotNone(self.history.last_failed_at)
        self.assertIn("SMTP timeout", self.history.error_message)
        # ネガティブ担保：SoftBounceCounter が増えていない
        self.assertEqual(SoftBounceCounter.objects.count(), 0)
        # トラッキングリンクも保存されていない（送信失敗時は bulk_create を呼ばない）
        self.assertEqual(
            TrackingLink.objects.filter(campaign=self.campaign).count(), 0
        )

    def test_repeated_failure_accumulates_count(self):
        with patch(
            "mailings.services.campaign_send._send_email_via_backend",
            side_effect=Exception("err"),
        ):
            send_one_recipient(self.campaign, self.history)
            send_one_recipient(self.campaign, self.history)
            send_one_recipient(self.campaign, self.history)
        self.history.refresh_from_db()
        self.assertEqual(self.history.failed_count, 3)
        self.assertTrue(DeliveryHistory.is_finally_failed(self.history))


class ExecuteCampaignSendTests(_Phase3TestBase):
    def test_first_invocation_sets_sending_and_records_actionlog(self):
        p1 = self._make_person("Alice")
        self._add_member(self.mailing_list, p1)
        campaign = self._make_campaign()
        mail.outbox = []
        summary = execute_campaign_send(campaign)
        campaign.refresh_from_db()
        # 全員 sent なので done になる
        self.assertEqual(campaign.status, Campaign.Status.DONE)
        self.assertIsNotNone(campaign.started_at)
        self.assertIsNotNone(campaign.completed_at)
        # ActionLog が 1 件（配信開始操作）記録される
        self.assertTrue(
            ActionLog.objects.filter(
                action=ACTION_SEND_CAMPAIGN, object_id=str(campaign.pk)
            ).exists()
        )
        self.assertEqual(summary["sent"], 1)
        self.assertTrue(summary["done"])

    def test_unsubscribe_filter_off_creator_records_audit(self):
        p1 = self._make_person("Alice")
        self._add_member(self.mailing_list, p1)
        campaign = self._make_campaign(apply_unsubscribe_filter=False)
        execute_campaign_send(campaign)
        # §7.8.4：OFF 配信は監査記録される
        self.assertTrue(
            ActionLog.objects.filter(
                action=ACTION_UNSUBSCRIBE_FILTER_OFF_SEND,
                object_id=str(campaign.pk),
            ).exists()
        )

    def test_newsletter_off_combination_rejected(self):
        """§7.8.4 構造的禁止：newsletter × apply_unsubscribe_filter=False を拒否。"""
        campaign = self._make_campaign(
            sender_mode=Campaign.SenderMode.NEWSLETTER,
            sender_email="news@example.com",
            sender_name="メルマガ",
            apply_unsubscribe_filter=False,
        )
        with self.assertRaises(ValidationError):
            execute_campaign_send(campaign)

    def test_newsletter_invalid_domain_rejected(self):
        campaign = self._make_campaign(
            sender_mode=Campaign.SenderMode.NEWSLETTER,
            sender_email="news@evil.com",
            sender_name="メルマガ",
        )
        with self.assertRaises(ValidationError):
            execute_campaign_send(campaign)

    def test_completion_when_all_sent_or_finally_failed(self):
        """全員 sent または M 回失敗で done（§7.2.2）。"""
        p1 = self._make_person("Alice")
        p2 = self._make_person("Bob", email="bob@example.com")
        self._add_member(self.mailing_list, p1)
        self._add_member(self.mailing_list, p2)
        campaign = self._make_campaign()

        # Bob だけ常に失敗するように mock
        original_send = mail.EmailMultiAlternatives.send

        def selective_fail(self, *args, **kwargs):
            if "bob@example.com" in self.to:
                raise Exception("permanent bounce")
            return original_send(self, *args, **kwargs)

        with patch.object(
            mail.EmailMultiAlternatives, "send", autospec=True, side_effect=selective_fail
        ):
            # 1 回目：Alice sent、Bob failed_count=1
            execute_campaign_send(campaign)
            # 2-3 回目：Bob 失敗を積む（M=3 まで）
            execute_campaign_send(campaign)
            execute_campaign_send(campaign)

        campaign.refresh_from_db()
        # 全員 sent or 最終 failed なので done
        self.assertEqual(campaign.status, Campaign.Status.DONE)
        bob_history = DeliveryHistory.objects.get(campaign=campaign, person=p2)
        self.assertEqual(bob_history.failed_count, CAMPAIGN_RECIPIENT_MAX_FAILURES)

    def test_batch_limit_partial_processing(self):
        """batch_limit=1 で 1 件だけ処理し残りは次回起動が拾う。"""
        for i in range(3):
            p = self._make_person(f"Person{i}", email=f"p{i}@example.com")
            self._add_member(self.mailing_list, p)
        campaign = self._make_campaign()
        summary = execute_campaign_send(campaign, batch_limit=1)
        self.assertEqual(summary["sent"], 1)
        campaign.refresh_from_db()
        # まだ全員終わってないので sending のまま
        self.assertEqual(campaign.status, Campaign.Status.SENDING)
        # 残りも処理
        execute_campaign_send(campaign, batch_limit=10)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DONE)

    def test_first_invocation_freezes_mailing_list_members(self):
        """rev16 §4.2.1 / §11.4.3：SCHEDULED → SENDING 遷移時に当該リストの
        members_frozen_at を現在時刻でセットする。"""
        p1 = self._make_person("Alice")
        self._add_member(self.mailing_list, p1)
        # 事前条件：リストは未凍結
        self.assertIsNone(self.mailing_list.members_frozen_at)
        campaign = self._make_campaign()
        before = timezone.now()
        execute_campaign_send(campaign)
        after = timezone.now()
        self.mailing_list.refresh_from_db()
        self.assertIsNotNone(self.mailing_list.members_frozen_at)
        # 現在時刻でセットされている（before ≤ frozen ≤ after）
        self.assertGreaterEqual(self.mailing_list.members_frozen_at, before)
        self.assertLessEqual(self.mailing_list.members_frozen_at, after)

    def test_freeze_is_idempotent_second_campaign_does_not_overwrite(self):
        """§19 論点 21：同一リストを参照する 2 つ目のキャンペーン送信時、
        既に立っている members_frozen_at は上書きしない（最初に立った時刻で確定）。"""
        p1 = self._make_person("Alice")
        self._add_member(self.mailing_list, p1)
        # 1 件目のキャンペーン送信で凍結発動
        campaign1 = self._make_campaign()
        execute_campaign_send(campaign1)
        self.mailing_list.refresh_from_db()
        first_frozen_at = self.mailing_list.members_frozen_at
        self.assertIsNotNone(first_frozen_at)

        # 別のキャンペーンが同じリストを参照して送信
        # rev18 §4.2：template は OneToOne。campaign2 用に別 template を用意する。
        campaign2 = Campaign.objects.create(
            name="C2",
            template=EmailTemplate.objects.create(
                name="T2",
                subject="件名2",
                body="本文2",
                created_by=self.user,
            ),
            mailing_list=self.mailing_list,
            scheduled_at=timezone.now() - timedelta(seconds=1),
            status=Campaign.Status.SCHEDULED,
            sender_mode=Campaign.SenderMode.CREATOR,
            apply_unsubscribe_filter=True,
            created_by=self.user,
        )
        execute_campaign_send(campaign2)

        self.mailing_list.refresh_from_db()
        # 上書きされず最初の値のまま
        self.assertEqual(self.mailing_list.members_frozen_at, first_frozen_at)

    def test_freeze_preserved_after_send_failure(self):
        """§11.3.6：配信失敗時も凍結は維持（NULL に戻さない）。"""
        p1 = self._make_person("Alice")
        self._add_member(self.mailing_list, p1)
        campaign = self._make_campaign()
        with patch(
            "mailings.services.campaign_send._send_email_via_backend",
            side_effect=Exception("SMTP down"),
        ):
            execute_campaign_send(campaign)
        self.mailing_list.refresh_from_db()
        # 全件失敗でも凍結は立ったまま
        self.assertIsNotNone(self.mailing_list.members_frozen_at)

    def test_freeze_not_triggered_on_second_invocation_of_same_campaign(self):
        """batch_limit で分割される 2 回目以降の起動（status は既に SENDING）では
        凍結は再発動しない（is_first_invocation 分岐内に閉じている）。"""
        for i in range(2):
            p = self._make_person(f"Person{i}", email=f"p{i}@example.com")
            self._add_member(self.mailing_list, p)
        campaign = self._make_campaign()
        # 1 回目：batch_limit=1 で凍結発動 + 1 件処理
        execute_campaign_send(campaign, batch_limit=1)
        self.mailing_list.refresh_from_db()
        first_frozen_at = self.mailing_list.members_frozen_at
        self.assertIsNotNone(first_frozen_at)
        # 2 回目：status は既に SENDING、is_first_invocation=False で凍結処理を通らない
        execute_campaign_send(campaign, batch_limit=10)
        self.mailing_list.refresh_from_db()
        self.assertEqual(self.mailing_list.members_frozen_at, first_frozen_at)


class SendScheduledCampaignsCommandTests(_Phase3TestBase):
    def test_command_picks_pending_campaigns(self):
        p1 = self._make_person("Alice")
        self._add_member(self.mailing_list, p1)
        campaign = self._make_campaign()
        # 未来日時の予約は拾わない
        future_p = self._make_person("FutureUser", email="future@example.com")
        future_list = MailingList.objects.create(
            name="FutureList", created_by=self.user
        )
        MailingListMember.objects.create(
            mailing_list=future_list, person=future_p, added_by=self.user
        )
        # rev18 §4.2：template は OneToOne。FutureCampaign 用に別 template を用意する。
        Campaign.objects.create(
            name="FutureCampaign",
            template=EmailTemplate.objects.create(
                name="T_future",
                subject="件名",
                body="本文",
                created_by=self.user,
            ),
            mailing_list=future_list,
            scheduled_at=timezone.now() + timedelta(hours=1),
            status=Campaign.Status.SCHEDULED,
            created_by=self.user,
        )
        out = StringIO()
        call_command("send_scheduled_campaigns", stdout=out)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DONE)
        # FutureCampaign は scheduled のまま
        future_campaign = Campaign.objects.get(name="FutureCampaign")
        self.assertEqual(future_campaign.status, Campaign.Status.SCHEDULED)

    def test_command_no_targets_does_not_error(self):
        out = StringIO()
        call_command("send_scheduled_campaigns", stdout=out)
        self.assertIn("対象キャンペーンなし", out.getvalue())


# ======================================================================
# Phase 4：クリック中継ビュー（仕様書 v1.6 §8.1 / §8.2 / §8.3、rev14）
#
# - TrackingRedirectView（GET /t/<token>/、URL name mailings:tracking_redirect）
# - ボット・プリフェッチ判定 5 段階（純関数）
# - IP マスキング（純関数）
# - ClickLog 作成（副作用）+ TrackingLink カウンタ更新（F() expression）
# - purge_old_click_log_ips 管理コマンド（90 日経過 ip_masked → NULL 上書き）
# ======================================================================


from django.test import TransactionTestCase

from mailings.services.bot_detection import detect_bot_status
from mailings.services.ip_masking import mask_ip


class _Phase4TestBase(TestCase):
    """Phase 4 テストの共通基底（Person / Campaign / TrackingLink を用意）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="phase4_user", password="x")
        self.template = EmailTemplate.objects.create(
            name="T1",
            subject="件名",
            body="本文",
            created_by=self.user,
        )
        self.mailing_list = MailingList.objects.create(
            name="ML1", created_by=self.user
        )
        self.person = self._make_person("Alice")
        self.campaign = Campaign.objects.create(
            name="C1",
            template=self.template,
            mailing_list=self.mailing_list,
            created_by=self.user,
        )

    def _make_person(self, full_name):
        p = Person.objects.create(status="active")
        c = Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            email=f"{full_name.lower()}@example.com",
            created_by=self.user,
        )
        p.primary_contact = c
        p.save(update_fields=["primary_contact", "updated_at"])
        return p

    def _make_tracking_link(self, *, token="tok_abc1234", original_url=None,
                            created_at=None):
        url = original_url or "https://example.com/landing"
        tl = TrackingLink.objects.create(
            campaign=self.campaign,
            person=self.person,
            original_url=url,
            token=token,
        )
        if created_at is not None:
            # auto_now_add の値を後から上書きするには update が必要
            TrackingLink.objects.filter(pk=tl.pk).update(created_at=created_at)
            tl.refresh_from_db()
        return tl


class IpMaskingPureFunctionTests(TestCase):
    """services/ip_masking.py の純関数テスト（§8.3.1）。"""

    def test_ipv4_normal(self):
        self.assertEqual(mask_ip("118.243.45.67"), "118.243.45.0")

    def test_ipv4_already_zero_tail(self):
        self.assertEqual(mask_ip("10.0.0.0"), "10.0.0.0")

    def test_ipv4_loopback(self):
        self.assertEqual(mask_ip("127.0.0.1"), "127.0.0.0")

    def test_ipv6_normal(self):
        result = mask_ip("2001:db8:1234:5678:abcd:ef01:2345:6789")
        # ipaddress モジュールの正規化形式：2001:db8:1234:5678::
        self.assertEqual(result, "2001:db8:1234:5678::")

    def test_ipv6_already_zero_lower(self):
        result = mask_ip("2001:db8:1234:5678::")
        self.assertEqual(result, "2001:db8:1234:5678::")

    def test_ipv6_full_zero(self):
        self.assertEqual(mask_ip("::"), "::")

    def test_invalid_string_returns_none(self):
        self.assertIsNone(mask_ip("not_an_ip"))
        self.assertIsNone(mask_ip("999.999.999.999"))
        self.assertIsNone(mask_ip("123.45.67"))

    def test_empty_returns_none(self):
        self.assertIsNone(mask_ip(""))

    def test_none_returns_none(self):
        self.assertIsNone(mask_ip(None))


class BotDetectionPureFunctionTests(TestCase):
    """services/bot_detection.py の純関数テスト（§8.2、5 段階判定）。"""

    def _base_kwargs(self):
        now = timezone.now()
        return dict(
            user_agent="Mozilla/5.0",
            sent_at=now - timedelta(minutes=5),
            now=now,
            threshold_seconds=3,
            bot_patterns=["bot", "crawl"],
            has_existing_valid_click=False,
        )

    def test_rank1_non_get_returns_non_get(self):
        kwargs = self._base_kwargs()
        is_valid, reason = detect_bot_status(http_method="HEAD", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "non_get")

    def test_rank1_post_returns_non_get(self):
        kwargs = self._base_kwargs()
        is_valid, reason = detect_bot_status(http_method="POST", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "non_get")

    def test_rank2_known_bot_by_user_agent_case_insensitive(self):
        kwargs = self._base_kwargs()
        kwargs["user_agent"] = "Googlebot/2.1"
        is_valid, reason = detect_bot_status(http_method="GET", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "known_bot")

    def test_rank2_known_bot_partial_match(self):
        kwargs = self._base_kwargs()
        kwargs["user_agent"] = "MyCrawlerAgent/1.0"
        is_valid, reason = detect_bot_status(http_method="GET", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "known_bot")

    def test_rank3_too_fast_within_threshold(self):
        now = timezone.now()
        kwargs = self._base_kwargs()
        kwargs["now"] = now
        kwargs["sent_at"] = now - timedelta(seconds=2)
        kwargs["threshold_seconds"] = 3
        is_valid, reason = detect_bot_status(http_method="GET", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "too_fast")

    def test_rank3_just_at_threshold_is_valid(self):
        # delta == threshold は too_fast 扱いではない（< 未満で判定）
        now = timezone.now()
        kwargs = self._base_kwargs()
        kwargs["now"] = now
        kwargs["sent_at"] = now - timedelta(seconds=3)
        kwargs["threshold_seconds"] = 3
        is_valid, reason = detect_bot_status(http_method="GET", **kwargs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_rank4_duplicate_when_existing_valid_click(self):
        kwargs = self._base_kwargs()
        kwargs["has_existing_valid_click"] = True
        is_valid, reason = detect_bot_status(http_method="GET", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "duplicate")

    def test_rank5_all_pass_is_valid(self):
        kwargs = self._base_kwargs()
        is_valid, reason = detect_bot_status(http_method="GET", **kwargs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_priority_non_get_beats_known_bot(self):
        # 順 1 → 順 2 の優先順序：HEAD かつ UA にボット文字列があれば non_get
        kwargs = self._base_kwargs()
        kwargs["user_agent"] = "Googlebot/2.1"
        is_valid, reason = detect_bot_status(http_method="HEAD", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "non_get")

    def test_priority_known_bot_beats_too_fast(self):
        now = timezone.now()
        kwargs = self._base_kwargs()
        kwargs["user_agent"] = "Googlebot/2.1"
        kwargs["now"] = now
        kwargs["sent_at"] = now - timedelta(seconds=1)
        is_valid, reason = detect_bot_status(http_method="GET", **kwargs)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "known_bot")


class TrackingRedirectView404Tests(_Phase4TestBase):
    """無効トークンの 404 経路（§8.1）。"""

    def test_unknown_token_returns_404(self):
        response = self.client.get("/t/nonexistent_token/")
        self.assertEqual(response.status_code, 404)

    def test_unknown_token_does_not_create_clicklog(self):
        before = ClickLog.objects.count()
        self.client.get("/t/nonexistent_token/")
        self.assertEqual(ClickLog.objects.count(), before)


class TrackingRedirectViewValidClickTests(_Phase4TestBase):
    """有効クリック（順 5）の経路（§8.1 / §8.2）。"""

    def test_valid_click_redirects_to_original_url(self):
        tl = self._make_tracking_link(
            token="tok_valid01",
            original_url="https://example.com/landing?utm=mail",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        response = self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/landing?utm=mail")

    def test_valid_click_increments_both_counters(self):
        tl = self._make_tracking_link(
            token="tok_valid02",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        tl.refresh_from_db()
        self.assertEqual(tl.total_access_count, 1)
        self.assertEqual(tl.click_count, 1)
        self.assertIsNotNone(tl.last_clicked_at)

    def test_valid_click_creates_clicklog_with_is_valid_true(self):
        tl = self._make_tracking_link(
            token="tok_valid03",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        self.client.get(
            f"/t/{tl.token}/",
            HTTP_USER_AGENT="Mozilla/5.0",
            REMOTE_ADDR="118.243.45.67",
        )
        log = ClickLog.objects.get(tracking_link=tl)
        self.assertTrue(log.is_valid_click)
        self.assertEqual(log.bot_reason, "")
        self.assertEqual(log.http_method, "GET")
        self.assertEqual(log.user_agent, "Mozilla/5.0")
        self.assertEqual(log.ip_masked, "118.243.45.0")


class TrackingRedirectViewHeadMethodTests(_Phase4TestBase):
    """HEAD（順 1：non_get）の経路（§8.2、§8.1 末尾の 302 返却）。"""

    def test_head_returns_302_for_prefetcher(self):
        tl = self._make_tracking_link(
            token="tok_head01",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        response = self.client.head(f"/t/{tl.token}/")
        # HEAD でも 302 を返す（メーラープリフェッチを正常終了させる）
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/landing")

    def test_head_does_not_increment_click_count(self):
        tl = self._make_tracking_link(
            token="tok_head02",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        self.client.head(f"/t/{tl.token}/")
        tl.refresh_from_db()
        # total_access_count は +1、click_count は据え置き
        self.assertEqual(tl.total_access_count, 1)
        self.assertEqual(tl.click_count, 0)
        self.assertIsNone(tl.last_clicked_at)

    def test_head_creates_clicklog_with_non_get_reason(self):
        tl = self._make_tracking_link(
            token="tok_head03",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        self.client.head(f"/t/{tl.token}/")
        log = ClickLog.objects.get(tracking_link=tl)
        self.assertFalse(log.is_valid_click)
        self.assertEqual(log.bot_reason, "non_get")
        self.assertEqual(log.http_method, "HEAD")


class TrackingRedirectViewBotUserAgentTests(_Phase4TestBase):
    """既知ボット UA（順 2：known_bot）の経路。"""

    def test_googlebot_ua_is_known_bot(self):
        tl = self._make_tracking_link(
            token="tok_bot01",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        response = self.client.get(
            f"/t/{tl.token}/", HTTP_USER_AGENT="Googlebot/2.1 (+http://www.google.com/bot.html)"
        )
        self.assertEqual(response.status_code, 302)
        tl.refresh_from_db()
        self.assertEqual(tl.total_access_count, 1)
        self.assertEqual(tl.click_count, 0)
        log = ClickLog.objects.get(tracking_link=tl)
        self.assertFalse(log.is_valid_click)
        self.assertEqual(log.bot_reason, "known_bot")


class TrackingRedirectViewTooFastTests(_Phase4TestBase):
    """プリフェッチ（順 3：too_fast）の経路。"""

    def test_click_within_threshold_is_too_fast(self):
        # TrackingLink.created_at = 今（=送信直後）。閾値 3 秒以内のクリック → too_fast
        tl = self._make_tracking_link(token="tok_fast01")
        response = self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(response.status_code, 302)
        tl.refresh_from_db()
        self.assertEqual(tl.total_access_count, 1)
        self.assertEqual(tl.click_count, 0)
        log = ClickLog.objects.get(tracking_link=tl)
        self.assertFalse(log.is_valid_click)
        self.assertEqual(log.bot_reason, "too_fast")


class TrackingRedirectViewDuplicateTests(_Phase4TestBase):
    """重複クリック（順 4：duplicate）の経路。"""

    def test_second_valid_click_is_duplicate(self):
        tl = self._make_tracking_link(
            token="tok_dup01",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        # 1 回目：有効
        self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        # 2 回目：duplicate
        self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        tl.refresh_from_db()
        self.assertEqual(tl.total_access_count, 2)
        self.assertEqual(tl.click_count, 1)  # 初回のみ
        logs = ClickLog.objects.filter(tracking_link=tl).order_by("clicked_at")
        self.assertEqual(logs.count(), 2)
        self.assertTrue(logs[0].is_valid_click)
        self.assertFalse(logs[1].is_valid_click)
        self.assertEqual(logs[1].bot_reason, "duplicate")


class TrackingRedirectViewCounterIntegrityTests(_Phase4TestBase):
    """カウンタの数え方検証：total は毎回 +1、click は有効時のみ +1（§13.2）。"""

    def test_total_access_count_increments_for_every_request(self):
        tl = self._make_tracking_link(
            token="tok_cnt01",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        # 連続 5 回（1 回目有効 + 2-5 回目 duplicate）
        for _ in range(5):
            self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        tl.refresh_from_db()
        self.assertEqual(tl.total_access_count, 5)
        self.assertEqual(tl.click_count, 1)

    def test_last_clicked_at_only_updates_on_valid_click(self):
        tl = self._make_tracking_link(
            token="tok_cnt02",
            created_at=timezone.now() - timedelta(minutes=10),
        )
        # 1 回目：有効
        self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        tl.refresh_from_db()
        first_last_clicked = tl.last_clicked_at
        self.assertIsNotNone(first_last_clicked)
        # 2 回目：duplicate（last_clicked_at 据え置き）
        self.client.get(f"/t/{tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
        tl.refresh_from_db()
        self.assertEqual(tl.last_clicked_at, first_last_clicked)


class TrackingRedirectViewConcurrencySafetyTests(TransactionTestCase):
    """同時クリック耐性：F() expression で race-free にカウンタ更新されること。

    SQLite/PG 共通で確実に通すため、TransactionTestCase 配下で threading.Thread を
    2 本走らせる。F() を使わない実装（Python レベルの read-modify-write）だと
    片方の +1 が消えるため total_access_count == 1 になり失敗するが、F() なら 2 が確定。
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from contacts.models import Contact
        from persons.models import Person

        User = get_user_model()
        self.user = User.objects.create_user(username="phase4_concur", password="x")
        self.template = EmailTemplate.objects.create(
            name="T1", subject="件名", body="本文", created_by=self.user
        )
        self.mailing_list = MailingList.objects.create(
            name="ML1", created_by=self.user
        )
        person = Person.objects.create(status="active")
        Contact.objects.create(
            person=person,
            status=Contact.Status.PRIMARY,
            full_name="Alice",
            email="alice@example.com",
            created_by=self.user,
        )
        person.refresh_from_db()
        self.campaign = Campaign.objects.create(
            name="C1",
            template=self.template,
            mailing_list=self.mailing_list,
            created_by=self.user,
        )
        self.tl = TrackingLink.objects.create(
            campaign=self.campaign,
            person=person,
            original_url="https://example.com/landing",
            token="tok_concur01",
        )
        # created_at を過去にして too_fast を回避
        TrackingLink.objects.filter(pk=self.tl.pk).update(
            created_at=timezone.now() - timedelta(minutes=10)
        )

    def test_concurrent_clicks_do_not_lose_total_access_count(self):
        import threading
        from django.db import connection

        client1 = Client()
        client2 = Client()

        def _hit(client):
            try:
                client.get(f"/t/{self.tl.token}/", HTTP_USER_AGENT="Mozilla/5.0")
            finally:
                # 各スレッドの DB 接続を閉じる（threading 共有を避ける）
                connection.close()

        t1 = threading.Thread(target=_hit, args=(client1,))
        t2 = threading.Thread(target=_hit, args=(client2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.tl.refresh_from_db()
        # F() expression なので並行 2 リクエストでも total_access_count は 2 で確定
        self.assertEqual(self.tl.total_access_count, 2)
        # ClickLog も 2 件作られる
        self.assertEqual(ClickLog.objects.filter(tracking_link=self.tl).count(), 2)


class PurgeOldClickLogIpsCommandTests(_Phase4TestBase):
    """purge_old_click_log_ips 管理コマンド（§8.3.2）。"""

    def _make_click_log(self, *, created_at, ip_masked="118.243.45.0"):
        # original_url も毎回ユニークにする（TrackingLink の UNIQUE (campaign, person,
        # original_url) 制約を回避）
        unique_suffix = uuid.uuid4().hex[:8]
        tl = TrackingLink.objects.create(
            campaign=self.campaign,
            person=self.person,
            original_url=f"https://example.com/page-{unique_suffix}",
            token=f"tok_{uuid.uuid4().hex[:10]}",
        )
        log = ClickLog.objects.create(
            tracking_link=tl,
            ip_masked=ip_masked,
            user_agent="UA",
            http_method="GET",
            is_valid_click=True,
            bot_reason="",
        )
        ClickLog.objects.filter(pk=log.pk).update(created_at=created_at)
        log.refresh_from_db()
        return log

    def test_purges_logs_older_than_retention_days(self):
        old_log = self._make_click_log(
            created_at=timezone.now() - timedelta(days=91),
        )
        recent_log = self._make_click_log(
            created_at=timezone.now() - timedelta(days=10),
        )
        out = StringIO()
        call_command("purge_old_click_log_ips", stdout=out)

        old_log.refresh_from_db()
        recent_log.refresh_from_db()
        self.assertIsNone(old_log.ip_masked)
        self.assertEqual(recent_log.ip_masked, "118.243.45.0")

    def test_already_null_logs_are_not_counted(self):
        # 既に NULL のレコードは update 対象外（運用ログでの差分検知のため）
        old_null = self._make_click_log(
            created_at=timezone.now() - timedelta(days=100),
            ip_masked=None,
        )
        old_with_ip = self._make_click_log(
            created_at=timezone.now() - timedelta(days=100),
            ip_masked="10.0.0.0",
        )
        out = StringIO()
        call_command("purge_old_click_log_ips", stdout=out)
        # 出力の件数は 1 件のみ（既に NULL のものは数えない）
        self.assertIn("1 件", out.getvalue())
        old_with_ip.refresh_from_db()
        self.assertIsNone(old_with_ip.ip_masked)

    def test_no_targets_outputs_zero(self):
        out = StringIO()
        call_command("purge_old_click_log_ips", stdout=out)
        self.assertIn("0 件", out.getvalue())

    def test_custom_days_argument(self):
        # --days=7 を指定すると、7 日経過分が NULL 化される
        log = self._make_click_log(created_at=timezone.now() - timedelta(days=8))
        out = StringIO()
        call_command("purge_old_click_log_ips", "--days=7", stdout=out)
        log.refresh_from_db()
        self.assertIsNone(log.ip_masked)


# ======================================================================
# Phase 5 §1：§9.5 サービス関数（get_same_person_unit / unsubscribe_person /
# cancel_unsubscribe）＋ Person への 2 メソッド追加（recompute_unsubscribed_state /
# set_unsubscribed_by_admin）の基盤テスト（仕様書 v1.6 §9.5 / §4.14.2 / §9.8、rev14）
# ======================================================================


from actionlogs.models import ActionLog

from mailings.services.audit import (
    ACTION_CANCEL_UNSUBSCRIBE,
    ACTION_UNSUBSCRIBE_BY_ADMIN,
    record_cancel_unsubscribe_action,
    record_unsubscribe_by_admin_action,
)
from mailings.services.unsubscribe import (
    cancel_unsubscribe,
    get_same_person_unit,
    unsubscribe_person,
)


class _Phase5UnitTestBase(TestCase):
    """Phase 5 §1 テストの共通基底。Person 木構造のヘルパーを提供。"""

    def setUp(self):
        self.user = User.objects.create_user(username="phase5_user", password="x")

    def _make_person(self, full_name, email=None):
        p = Person.objects.create(status="active")
        c = Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            email=email or f"{full_name.lower()}@example.com",
            created_by=self.user,
        )
        p.primary_contact = c
        p.save(update_fields=["primary_contact", "updated_at"])
        return p

    def _make_merged(self, root, full_name):
        merged = self._make_person(full_name, email=f"merged-{full_name.lower()}@example.com")
        # merged 化（primary_contact は mark_as_merged が NULL にする）
        merged.mark_as_merged(root)
        merged.refresh_from_db()
        return merged


class GetSamePersonUnitTests(_Phase5UnitTestBase):
    def test_active_person_alone_returns_self(self):
        alice = self._make_person("Alice")
        unit = get_same_person_unit(alice)
        self.assertEqual(unit, [alice])

    def test_merged_person_returns_root_and_self(self):
        root = self._make_person("Root")
        merged = self._make_merged(root, "Merged")
        unit = get_same_person_unit(merged)
        ids = {p.id for p in unit}
        self.assertEqual(ids, {root.id, merged.id})
        # root が先頭
        self.assertEqual(unit[0].id, root.id)

    def test_root_with_multiple_merged_children(self):
        root = self._make_person("Root2")
        m1 = self._make_merged(root, "M1")
        m2 = self._make_merged(root, "M2")
        unit = get_same_person_unit(root)
        ids = {p.id for p in unit}
        self.assertEqual(ids, {root.id, m1.id, m2.id})

    def test_multi_level_merge_collected_from_root(self):
        # A → B → C（C が最終 root、A も B 経由で C に収集される必要あり）
        c_root = self._make_person("C")
        b = self._make_merged(c_root, "B")
        # A を B にマージ → 後で B を C に再マージ済みなので A は B 経由
        # mark_as_merged は parent を直接書くため、A を B に向ける
        a = self._make_person("A")
        a.mark_as_merged(b)
        a.refresh_from_db()
        # ユニット = {C, B, A}（C 起点で BFS、B の子に A）
        unit = get_same_person_unit(c_root)
        ids = {p.id for p in unit}
        self.assertEqual(ids, {c_root.id, b.id, a.id})

    def test_multi_level_merge_from_leaf_returns_full_unit(self):
        c_root = self._make_person("C2")
        b = self._make_merged(c_root, "B2")
        a = self._make_person("A2")
        a.mark_as_merged(b)
        a.refresh_from_db()
        # 葉の A から呼んでも root + 全 merged が返る
        unit = get_same_person_unit(a)
        ids = {p.id for p in unit}
        self.assertEqual(ids, {c_root.id, b.id, a.id})
        self.assertEqual(unit[0].id, c_root.id)

    def test_cycle_detection_raises_value_error(self):
        # データ不整合：A.merged_into = B、B.merged_into = A（循環）
        a = self._make_person("CycA")
        b = self._make_person("CycB")
        # update で強制的に循環状態を作る（mark_as_merged は active→merged 専用）
        Person.objects.filter(pk=a.pk).update(
            merged_into=b, status=Person.Status.MERGED
        )
        Person.objects.filter(pk=b.pk).update(
            merged_into=a, status=Person.Status.MERGED
        )
        a.refresh_from_db()
        with self.assertRaises(ValueError):
            get_same_person_unit(a)


class UnsubscribePersonTests(_Phase5UnitTestBase):
    def test_single_active_person_unsubscribed(self):
        alice = self._make_person("Solo")
        unsubscribe_person(
            target_person=alice,
            source="unsubscribe_link",
            source_email="solo@example.com",
        )
        alice.refresh_from_db()
        self.assertTrue(alice.is_unsubscribed)
        unsub = Unsubscribe.objects.get(person=alice)
        self.assertEqual(unsub.source, "unsubscribe_link")
        self.assertEqual(unsub.source_email, "solo@example.com")
        self.assertIsNone(unsub.cancelled_at)

    def test_unit_all_members_get_unsubscribe_records(self):
        root = self._make_person("UnitRoot")
        merged = self._make_merged(root, "UnitMerged")
        unsubscribe_person(
            target_person=root,
            source="manual",
            source_email="contact@example.com",
            note="phone request",
        )
        root.refresh_from_db()
        merged.refresh_from_db()
        self.assertTrue(root.is_unsubscribed)
        self.assertTrue(merged.is_unsubscribed)
        self.assertEqual(Unsubscribe.objects.filter(person=root).count(), 1)
        self.assertEqual(Unsubscribe.objects.filter(person=merged).count(), 1)
        self.assertEqual(Unsubscribe.objects.get(person=root).note, "phone request")

    def test_idempotent_does_not_create_duplicate_active_unsubscribe(self):
        alice = self._make_person("Idem")
        unsubscribe_person(
            target_person=alice,
            source="unsubscribe_link",
            source_email="idem@example.com",
        )
        # 2 回目（受信者の二度押し相当）
        unsubscribe_person(
            target_person=alice,
            source="unsubscribe_link",
            source_email="idem@example.com",
        )
        self.assertEqual(
            Unsubscribe.objects.filter(person=alice, cancelled_at__isnull=True).count(),
            1,
        )

    def test_re_unsubscribe_after_cancel_creates_new_record(self):
        # 一度停止 → 解除 → 再度停止 のシナリオ。新しい active レコードが 1 件できる。
        alice = self._make_person("Recycle")
        unsubscribe_person(
            target_person=alice,
            source="unsubscribe_link",
            source_email="recycle@example.com",
        )
        cancel_unsubscribe(target_person=alice, user=self.user)
        unsubscribe_person(
            target_person=alice,
            source="manual",
            source_email="recycle@example.com",
        )
        alice.refresh_from_db()
        self.assertTrue(alice.is_unsubscribed)
        active = Unsubscribe.objects.filter(person=alice, cancelled_at__isnull=True)
        cancelled = Unsubscribe.objects.filter(person=alice, cancelled_at__isnull=False)
        self.assertEqual(active.count(), 1)
        self.assertEqual(cancelled.count(), 1)

    def test_returns_unit_list(self):
        root = self._make_person("RetRoot")
        merged = self._make_merged(root, "RetMerged")
        result = unsubscribe_person(
            target_person=merged,
            source="manual",
            source_email="x@example.com",
        )
        ids = {p.id for p in result}
        self.assertEqual(ids, {root.id, merged.id})


class CancelUnsubscribeTests(_Phase5UnitTestBase):
    def test_cancels_all_active_in_unit(self):
        root = self._make_person("CancRoot")
        merged = self._make_merged(root, "CancMerged")
        unsubscribe_person(
            target_person=root,
            source="manual",
            source_email="canc@example.com",
        )
        # 全員 active な状態
        self.assertEqual(
            Unsubscribe.objects.filter(cancelled_at__isnull=True).count(), 2
        )
        cancel_unsubscribe(target_person=root, user=self.user, note="resume")
        root.refresh_from_db()
        merged.refresh_from_db()
        self.assertFalse(root.is_unsubscribed)
        self.assertFalse(merged.is_unsubscribed)
        self.assertEqual(
            Unsubscribe.objects.filter(cancelled_at__isnull=True).count(), 0
        )
        for u in Unsubscribe.objects.all():
            self.assertEqual(u.cancelled_by, self.user)

    def test_cancel_with_no_active_records_just_sets_flag_false(self):
        alice = self._make_person("Plain")
        # まだ active な Unsubscribe が無くてもエラーにならない
        cancel_unsubscribe(target_person=alice, user=self.user)
        alice.refresh_from_db()
        self.assertFalse(alice.is_unsubscribed)


class PersonRecomputeUnsubscribedStateTests(_Phase5UnitTestBase):
    def test_true_when_active_unsubscribe_exists(self):
        alice = self._make_person("Rec1")
        Unsubscribe.objects.create(
            person=alice,
            source_email="rec1@example.com",
            source=Unsubscribe.Source.MANUAL,
        )
        alice.recompute_unsubscribed_state()
        alice.refresh_from_db()
        self.assertTrue(alice.is_unsubscribed)

    def test_false_when_no_active_unsubscribe(self):
        alice = self._make_person("Rec2")
        # 既に is_unsubscribed=True に設定された人物（不整合状態）
        Person.objects.filter(pk=alice.pk).update(is_unsubscribed=True)
        alice.refresh_from_db()
        alice.recompute_unsubscribed_state()
        alice.refresh_from_db()
        self.assertFalse(alice.is_unsubscribed)

    def test_false_when_only_cancelled_unsubscribe(self):
        alice = self._make_person("Rec3")
        Unsubscribe.objects.create(
            person=alice,
            source_email="rec3@example.com",
            source=Unsubscribe.Source.MANUAL,
            cancelled_at=timezone.now(),
        )
        # 不整合状態（is_unsubscribed=True なのに active な Unsubscribe なし）にする
        Person.objects.filter(pk=alice.pk).update(is_unsubscribed=True)
        alice.refresh_from_db()
        alice.recompute_unsubscribed_state()
        alice.refresh_from_db()
        self.assertFalse(alice.is_unsubscribed)


class PersonSetUnsubscribedByAdminTests(_Phase5UnitTestBase):
    def test_set_unsubscribed_by_admin_propagates_and_records_actionlog(self):
        root = self._make_person("AdminRoot")
        merged = self._make_merged(root, "AdminMerged")
        root.set_unsubscribed_by_admin(user=self.user, note="telephone request")

        root.refresh_from_db()
        merged.refresh_from_db()
        self.assertTrue(root.is_unsubscribed)
        self.assertTrue(merged.is_unsubscribed)
        self.assertEqual(
            Unsubscribe.objects.filter(
                person__in=[root, merged], cancelled_at__isnull=True
            ).count(),
            2,
        )
        # ActionLog が記録されている
        log = ActionLog.objects.filter(action=ACTION_UNSUBSCRIBE_BY_ADMIN).get()
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.object_repr, str(root))
        self.assertEqual(log.data["source"], "manual")
        self.assertIn("telephone", log.note)

    def test_raises_value_error_when_no_primary_contact_email(self):
        # primary_contact が無い Person
        broken = Person.objects.create(status="active")
        with self.assertRaises(ValueError):
            broken.set_unsubscribed_by_admin(user=self.user)


class RecordCancelUnsubscribeActionTests(_Phase5UnitTestBase):
    def test_record_cancel_unsubscribe_creates_actionlog(self):
        alice = self._make_person("CancRec")
        record_cancel_unsubscribe_action(user=self.user, person=alice, note="resume on phone")
        log = ActionLog.objects.filter(action=ACTION_CANCEL_UNSUBSCRIBE).get()
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.data["person_id"], str(alice.pk))
        self.assertIn("resume", log.note)


class RecordUnsubscribeByAdminActionTests(_Phase5UnitTestBase):
    def test_default_note_when_empty(self):
        alice = self._make_person("UbyA")
        record_unsubscribe_by_admin_action(user=self.user, person=alice, note="")
        log = ActionLog.objects.filter(action=ACTION_UNSUBSCRIBE_BY_ADMIN).get()
        # 空 note のときデフォルト文言が入る
        self.assertIn("管理者代行", log.note)


# ======================================================================
# Phase 5 §2：バウンス処理（仕様書 v1.6 §10、rev14）
# ======================================================================


from mailings.services.bounce_processor import (
    BOUNCE_TYPE_HARD,
    BOUNCE_TYPE_SOFT,
    process_bounce,
    process_hard_bounce,
    process_send_success,
    process_soft_bounce,
)


class _Phase5BounceTestBase(TestCase):
    """バウンス処理テストの共通基底。Campaign / DeliveryHistory を用意する。"""

    def setUp(self):
        self.user = User.objects.create_user(username="phase5_bounce", password="x")
        self.template = EmailTemplate.objects.create(
            name="T1", subject="件名", body="本文", created_by=self.user
        )
        self.mailing_list = MailingList.objects.create(
            name="ML1", created_by=self.user
        )
        self.person = Person.objects.create(status="active")
        contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="BounceUser",
            email="bounceuser@example.com",
            created_by=self.user,
        )
        self.person.primary_contact = contact
        self.person.save(update_fields=["primary_contact", "updated_at"])
        self.campaign = Campaign.objects.create(
            name="BounceC",
            template=self.template,
            mailing_list=self.mailing_list,
            created_by=self.user,
        )

    def _make_history(self, *, to_email, status=None):
        return DeliveryHistory.objects.create(
            campaign=self.campaign,
            person=self.person,
            to_email=to_email,
            status=status or DeliveryHistory.Status.SENT,
        )


class ProcessHardBounceTests(_Phase5BounceTestBase):
    def test_hard_creates_suppressed_email(self):
        process_hard_bounce("dead@example.com", "550 User unknown")
        sup = SuppressedEmail.objects.get(email="dead@example.com")
        self.assertEqual(sup.source, SuppressedEmail.Source.BOUNCE_HARD)
        self.assertIn("550", sup.bounce_reason)

    def test_hard_idempotent_when_active_exists(self):
        process_hard_bounce("dead@example.com", "550 User unknown")
        process_hard_bounce("dead@example.com", "550 second event")
        # 2 件目は作られない（partial unique で active は 1 件まで）
        self.assertEqual(
            SuppressedEmail.objects.filter(
                email="dead@example.com", cancelled_at__isnull=True
            ).count(),
            1,
        )


class ProcessSoftBounceTests(_Phase5BounceTestBase):
    def test_soft_increments_counter_until_threshold(self):
        for i in range(1, 5):  # 1..4 回 → SuppressedEmail に昇格しない
            process_soft_bounce("soft@example.com", f"4xx attempt {i}")
            counter = SoftBounceCounter.objects.get(email="soft@example.com")
            self.assertEqual(counter.count, i)
        # 5 回目で昇格
        process_soft_bounce("soft@example.com", "4xx attempt 5")
        self.assertFalse(SoftBounceCounter.objects.filter(email="soft@example.com").exists())
        sup = SuppressedEmail.objects.get(email="soft@example.com")
        self.assertEqual(sup.source, SuppressedEmail.Source.BOUNCE_SOFT_PROMOTED)

    def test_soft_skipped_when_already_suppressed(self):
        SuppressedEmail.objects.create(
            email="already@example.com",
            source=SuppressedEmail.Source.BOUNCE_HARD,
        )
        process_soft_bounce("already@example.com", "4xx")
        # SoftBounceCounter は作られない（重複昇格防止）
        self.assertFalse(
            SoftBounceCounter.objects.filter(email="already@example.com").exists()
        )


class ProcessSendSuccessTests(_Phase5BounceTestBase):
    def test_resets_soft_bounce_counter(self):
        SoftBounceCounter.objects.create(
            email="reset@example.com", count=3, last_bounce_at=timezone.now()
        )
        process_send_success("reset@example.com")
        self.assertFalse(SoftBounceCounter.objects.filter(email="reset@example.com").exists())

    def test_no_op_when_no_counter(self):
        # 例外を出さない
        process_send_success("noop@example.com")

    def test_empty_email_is_noop(self):
        SoftBounceCounter.objects.create(
            email="keep@example.com", count=1, last_bounce_at=timezone.now()
        )
        process_send_success("")
        # 空メアドはノーオペ、既存カウンタは残る
        self.assertTrue(SoftBounceCounter.objects.filter(email="keep@example.com").exists())


class ProcessBounceDispatchTests(_Phase5BounceTestBase):
    def test_hard_dispatches_to_process_hard(self):
        process_bounce("dh@example.com", BOUNCE_TYPE_HARD, "550")
        self.assertTrue(
            SuppressedEmail.objects.filter(email="dh@example.com").exists()
        )

    def test_soft_dispatches_to_process_soft(self):
        process_bounce("ds@example.com", BOUNCE_TYPE_SOFT, "421")
        self.assertEqual(
            SoftBounceCounter.objects.get(email="ds@example.com").count, 1
        )

    def test_unknown_bounce_type_raises(self):
        with self.assertRaises(ValueError):
            process_bounce("x@example.com", "unknown", "?")

    def test_empty_email_silently_skipped(self):
        process_bounce("", BOUNCE_TYPE_HARD, "550")
        self.assertEqual(SuppressedEmail.objects.count(), 0)


class DeliveryHistoryBouncedUpdateTests(_Phase5BounceTestBase):
    def test_best_effort_updates_matching_history(self):
        h = self._make_history(to_email="target@example.com")
        process_bounce("target@example.com", BOUNCE_TYPE_HARD, "550 User unknown")
        h.refresh_from_db()
        self.assertEqual(h.status, DeliveryHistory.Status.BOUNCED)
        self.assertIn("550", h.error_message)

    def test_multiple_histories_all_marked(self):
        # 同一メアドで 2 件 → 全件 bounced（最新 1 件選別ロジックを作らない、§10.4.1）
        # ただし UniqueConstraint(campaign, person) があるため、別 campaign で 2 件作る
        # rev18 §4.2：template は OneToOne。campaign2 用に別 template を用意する。
        campaign2 = Campaign.objects.create(
            name="BC2",
            template=EmailTemplate.objects.create(
                name="T2", subject="件名", body="本文", created_by=self.user
            ),
            mailing_list=self.mailing_list,
            created_by=self.user,
        )
        h1 = self._make_history(to_email="multi@example.com")
        h2 = DeliveryHistory.objects.create(
            campaign=campaign2,
            person=self.person,
            to_email="multi@example.com",
            status=DeliveryHistory.Status.SENT,
        )
        process_bounce("multi@example.com", BOUNCE_TYPE_SOFT, "421")
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertEqual(h1.status, DeliveryHistory.Status.BOUNCED)
        self.assertEqual(h2.status, DeliveryHistory.Status.BOUNCED)


class SendOneRecipientResetsSoftCounterTests(_Phase5BounceTestBase):
    """Phase 5 後付け配線：送信成功で SoftBounceCounter リセット（§10.3.2）。"""

    def test_send_success_clears_existing_counter(self):
        from unittest.mock import patch

        from mailings.services.campaign_send import send_one_recipient

        # 配信前に SoftBounceCounter を仕込む
        SoftBounceCounter.objects.create(
            email=self.person.primary_contact.email,
            count=2,
            last_bounce_at=timezone.now(),
        )
        history = DeliveryHistory.objects.create(
            campaign=self.campaign,
            person=self.person,
            to_email=self.person.primary_contact.email,
            status=DeliveryHistory.Status.PENDING,
        )
        # MailingConfig（送信に必要）
        MailingConfig.objects.create(
            company_name="TestCo",
            company_address="TestAddr",
            unsubscribe_contact="contact@example.com",
            dkim_domain="example.com",
        )
        # _send_email_via_backend をモックして成功扱い
        with patch(
            "mailings.services.campaign_send._send_email_via_backend"
        ) as mock_send:
            mock_send.return_value = None
            ok = send_one_recipient(self.campaign, history)
        self.assertTrue(ok)
        # 送信成功で SoftBounceCounter が消えている
        self.assertFalse(
            SoftBounceCounter.objects.filter(
                email=self.person.primary_contact.email
            ).exists()
        )


# ======================================================================
# Phase 5 §2b：BounceWebhookView（最小実装、§10.1 / §10.2）
# ======================================================================


from django.test import override_settings as _override_settings_for_webhook


@_override_settings_for_webhook(
    BOUNCE_WEBHOOK_ENABLED=True, BOUNCE_WEBHOOK_SHARED_SECRET="test-secret-token"
)
class BounceWebhookViewTests(_Phase5BounceTestBase):
    """フラグ ON + 正しいシークレット下での挙動（payload 形式バリデーション等）。"""

    SECRET_HEADER_KWARGS = {"HTTP_X_BOUNCE_WEBHOOK_TOKEN": "test-secret-token"}

    def test_valid_hard_payload_creates_suppressed(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=json.dumps(
                {"email": "wh@example.com", "bounce_type": "hard", "reason": "550"}
            ),
            content_type="application/json",
            **self.SECRET_HEADER_KWARGS,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SuppressedEmail.objects.filter(email="wh@example.com").exists())

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=b"not json",
            content_type="application/json",
            **self.SECRET_HEADER_KWARGS,
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_fields_returns_400(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=json.dumps({"email": "x@example.com"}),
            content_type="application/json",
            **self.SECRET_HEADER_KWARGS,
        )
        self.assertEqual(response.status_code, 400)


# ======================================================================
# Phase 5 §2c：IMAP ハンドラ パーサ（純関数、§10.1 / §10.2）
# ======================================================================


from mailings.services.bounce_smtp_handler import (
    parse_bounce_message,
    process_one_message,
)


class ParseBounceMessageTests(TestCase):
    def test_extracts_email_and_hard_from_dsn_with_5xx(self):
        raw = (
            b"From: postmaster@example.com\r\n"
            b"To: bounce@freegroup2.example.com\r\n"
            b"Subject: Delivery Status Notification (Failure)\r\n"
            b"Failed-Recipients: rfc822; dead@target.com\r\n"
            b"\r\n"
            b"550 5.1.1 User unknown\r\n"
        )
        result = parse_bounce_message(raw)
        self.assertIsNotNone(result)
        email, bounce_type, reason = result
        self.assertEqual(email, "dead@target.com")
        self.assertEqual(bounce_type, BOUNCE_TYPE_HARD)
        self.assertIn("550", reason)

    def test_extracts_soft_from_4xx(self):
        raw = (
            b"From: postmaster@example.com\r\n"
            b"To: bounce@freegroup2.example.com\r\n"
            b"\r\n"
            b"421 4.7.0 Service temporarily unavailable; please retry "
            b"to soft@target.com\r\n"
        )
        result = parse_bounce_message(raw)
        self.assertIsNotNone(result)
        email, bounce_type, _ = result
        self.assertEqual(email, "soft@target.com")
        self.assertEqual(bounce_type, BOUNCE_TYPE_SOFT)

    def test_returns_none_when_no_email_extractable(self):
        raw = b"From: x\r\n\r\n(empty body)\r\n"
        self.assertIsNone(parse_bounce_message(raw))

    def test_invalid_bytes_returns_none(self):
        self.assertIsNone(parse_bounce_message(b""))


class ProcessOneMessageTests(_Phase5BounceTestBase):
    def test_hard_message_results_in_suppressed(self):
        raw = (
            b"From: postmaster@example.com\r\n"
            b"To: bounce@freegroup2.example.com\r\n"
            b"Failed-Recipients: rfc822; pm-hard@target.com\r\n"
            b"\r\n"
            b"550 5.1.1 User unknown\r\n"
        )
        ok = process_one_message(raw)
        self.assertTrue(ok)
        self.assertTrue(SuppressedEmail.objects.filter(email="pm-hard@target.com").exists())

    def test_unparseable_returns_false(self):
        self.assertFalse(process_one_message(b"garbage"))


# ======================================================================
# Phase 5 §3：配信停止 受信者 UI（§9.2 / §4.5A.2、URL: /u/ 系）
# ======================================================================


class _Phase5UIBase(TestCase):
    """受信者 UI / 管理 UI 共通基底。"""

    def setUp(self):
        self.user = User.objects.create_user(username="phase5_ui", password="x")
        self.template = EmailTemplate.objects.create(
            name="T1", subject="件名", body="本文", created_by=self.user
        )
        self.mailing_list = MailingList.objects.create(
            name="ML1", created_by=self.user
        )
        self.campaign = Campaign.objects.create(
            name="C1",
            template=self.template,
            mailing_list=self.mailing_list,
            created_by=self.user,
        )

    def _make_person(self, full_name, email):
        p = Person.objects.create(status="active")
        c = Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            email=email,
            created_by=self.user,
        )
        p.primary_contact = c
        p.save(update_fields=["primary_contact", "updated_at"])
        return p

    def _make_unsub_link(self, person, token="utok_xxx", target_email=None):
        # rev15 §4.5A：target_email は発行時に焼き込まれる必須値。テストでは未指定なら
        # person.primary_contact.email を焼き込む（実運用と同じ value セマンティクス）。
        if target_email is None:
            target_email = (
                person.primary_contact.email if person.primary_contact else ""
            )
        return UnsubscribeLink.objects.create(
            campaign=self.campaign,
            person=person,
            token=token,
            target_email=target_email,
        )


class UnsubscribePageGetTests(_Phase5UIBase):
    def test_get_updates_accessed_at_only(self):
        person = self._make_person("Alice", "alice@example.com")
        link = self._make_unsub_link(person, token="utok_001")
        response = self.client.get("/u/utok_001/")
        self.assertEqual(response.status_code, 200)
        link.refresh_from_db()
        self.assertIsNotNone(link.accessed_at)
        # GET では Unsubscribe / SuppressedEmail / is_unsubscribed を変えない
        self.assertEqual(Unsubscribe.objects.count(), 0)
        self.assertEqual(SuppressedEmail.objects.count(), 0)
        person.refresh_from_db()
        self.assertFalse(person.is_unsubscribed)

    def test_get_with_invalid_token_redirects_to_tokenless(self):
        response = self.client.get("/u/nope/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/u/")

    def test_masked_email_displayed_from_target_email(self):
        # rev15：表示メアドは UnsubscribeLink.target_email を使う（primary_contact ではない）
        person = self._make_person("Bob", "robert@example.com")
        self._make_unsub_link(person, token="utok_002", target_email="snapshot@example.com")
        response = self.client.get("/u/utok_002/")
        # snapshot@... が表示される（primary_contact の robert@... ではない）
        self.assertContains(response, "sna***@example.com")
        self.assertNotContains(response, "rob***@example.com")


class UnsubscribeConfirmPostTests(_Phase5UIBase):
    def test_post_personal_triggers_unsubscribe_person(self):
        person = self._make_person("Carol", "carol.t@example.com")
        link = self._make_unsub_link(person, token="utok_003")
        response = self.client.post("/u/utok_003/confirm/")
        self.assertEqual(response.status_code, 302)
        person.refresh_from_db()
        self.assertTrue(person.is_unsubscribed)
        self.assertEqual(
            Unsubscribe.objects.filter(person=person, cancelled_at__isnull=True).count(),
            1,
        )
        link.refresh_from_db()
        self.assertIsNotNone(link.unsubscribed_at)

    def test_post_generic_creates_suppressed_email_from_target_email(self):
        # rev15：個人/代表判定の正本は target_email。primary_contact が個人メアドでも
        # target_email が代表メアドなら代表経路に倒れる（焼き込み時の事実が優先）。
        person = self._make_person("MixCase", "personal@example.com")
        self._make_unsub_link(
            person, token="utok_004", target_email="info@example.com"
        )
        response = self.client.post("/u/utok_004/confirm/")
        self.assertEqual(response.status_code, 302)
        person.refresh_from_db()
        # 代表メアドは Unsubscribe を作らない、Person は停止されない
        self.assertFalse(person.is_unsubscribed)
        self.assertEqual(Unsubscribe.objects.count(), 0)
        # SuppressedEmail は target_email で登録される
        self.assertTrue(
            SuppressedEmail.objects.filter(email="info@example.com").exists()
        )

    def test_post_personal_uses_target_email_even_if_primary_contact_changed(self):
        # rev15：配信後に primary_contact が代表メアドに差し替わっても、target_email が
        # 個人メアドなら個人経路で停止する（判定が後天的にブレない、§4.5A）。
        person = self._make_person("Recipient", "info@example.com")
        # UnsubscribeLink には個人メアドを焼き込み（発行時の事実）
        link = self._make_unsub_link(
            person, token="utok_005a", target_email="personal@example.com"
        )
        response = self.client.post("/u/utok_005a/confirm/")
        self.assertEqual(response.status_code, 302)
        person.refresh_from_db()
        # 個人経路 → Person 停止
        self.assertTrue(person.is_unsubscribed)
        self.assertEqual(Unsubscribe.objects.count(), 1)
        # 代表経路の SuppressedEmail は作られない
        self.assertEqual(SuppressedEmail.objects.count(), 0)
        link.refresh_from_db()
        self.assertIsNotNone(link.unsubscribed_at)

    def test_post_propagates_to_unit_for_personal_email_after_merge(self):
        # rev15：マージ後に merged.primary_contact が NULL になっても、target_email は
        # 焼き込み済みなので fallback 不要で正常に個人経路 → ユニット全員伝播。
        root = self._make_person("UnitRoot2", "uroot@example.com")
        merged = self._make_person("UnitMerged2", "umerged@example.com")
        # 発行時に target_email を焼き込んだ UnsubscribeLink
        link = UnsubscribeLink.objects.create(
            campaign=self.campaign,
            person=merged,
            token="utok_005",
            target_email="umerged@example.com",
        )
        # マージ実行（merged.primary_contact が NULL になる）
        merged.mark_as_merged(root)
        merged.refresh_from_db()
        self.assertIsNone(merged.primary_contact)

        self.client.post("/u/utok_005/confirm/")
        root.refresh_from_db()
        merged.refresh_from_db()
        # target_email から個人メアド判定 → unsubscribe_person → ユニット全員伝播
        self.assertTrue(root.is_unsubscribed)
        self.assertTrue(merged.is_unsubscribed)
        # UnsubscribeLink 自体は person=merged のまま、unsubscribed_at は更新
        link.refresh_from_db()
        self.assertEqual(link.person_id, merged.id)
        self.assertIsNotNone(link.unsubscribed_at)

    def test_post_invalid_token_redirects_to_tokenless(self):
        response = self.client.post("/u/nope/confirm/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/u/")


class UnsubscribeDoneTests(_Phase5UIBase):
    def test_done_view_does_not_require_unsublink(self):
        # UnsubscribeLink が無くても 200（仕様書 §4.5A.2：done は UnsubscribeLink を読まない）
        response = self.client.get("/u/random_token/done/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "配信停止を受け付けました")


class UnsubscribeTokenlessTests(TestCase):
    def test_tokenless_returns_guidance_page(self):
        response = self.client.get("/u/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "正規メール内のリンクから")


class MaskEmailHelperTests(TestCase):
    def test_normal_local_part(self):
        from mailings.views import _mask_email

        self.assertEqual(_mask_email("robert@example.com"), "rob***@example.com")

    def test_short_local_part(self):
        from mailings.views import _mask_email

        self.assertEqual(_mask_email("ab@example.com"), "***@example.com")

    def test_empty_returns_empty(self):
        from mailings.views import _mask_email

        self.assertEqual(_mask_email(""), "")


# ======================================================================
# Phase 5 §4：管理者代行・解除 + SuppressedEmail 管理 UI
# ======================================================================


class _Phase5AdminBase(_Phase5UIBase):
    """管理 UI テスト共通：権限付き管理者を用意する。"""

    def setUp(self):
        super().setUp()
        admin = User.objects.create_user(username="phase5_admin", password="x")
        perm = Permission.objects.get(codename="manage_suppressed_email")
        admin.user_permissions.add(perm)
        self.admin = admin
        self.admin_client = Client()
        self.admin_client.force_login(admin)


class PersonUnsubscribeByAdminViewTests(_Phase5AdminBase):
    def test_post_propagates_to_unit_and_records_actionlog(self):
        root = self._make_person("AdminProp", "adminprop@example.com")
        merged = self._make_person("AdminPropM", "adminpropm@example.com")
        merged.mark_as_merged(root)
        url = f"/mailings/persons/{root.pk}/unsubscribe-by-admin/"
        response = self.admin_client.post(url, data={"note": "telephone"})
        self.assertEqual(response.status_code, 302)
        root.refresh_from_db()
        merged.refresh_from_db()
        self.assertTrue(root.is_unsubscribed)
        self.assertTrue(merged.is_unsubscribed)
        log = ActionLog.objects.filter(action=ACTION_UNSUBSCRIBE_BY_ADMIN).get()
        self.assertEqual(log.user, self.admin)

    def test_permission_required(self):
        person = self._make_person("PermNo", "permno@example.com")
        # 権限なしユーザ
        no_perm = User.objects.create_user(username="noperm", password="x")
        c = Client()
        c.force_login(no_perm)
        response = c.post(f"/mailings/persons/{person.pk}/unsubscribe-by-admin/")
        self.assertEqual(response.status_code, 403)


class PersonCancelUnsubscribeViewTests(_Phase5AdminBase):
    def test_post_cancels_unit_unsubscribe_and_records(self):
        from mailings.services.unsubscribe import unsubscribe_person

        person = self._make_person("CancAdm", "cancadm@example.com")
        unsubscribe_person(
            target_person=person, source="manual", source_email="cancadm@example.com"
        )
        url = f"/mailings/persons/{person.pk}/cancel-unsubscribe/"
        response = self.admin_client.post(url, data={"note": "resume"})
        self.assertEqual(response.status_code, 302)
        person.refresh_from_db()
        self.assertFalse(person.is_unsubscribed)
        log = ActionLog.objects.filter(action=ACTION_CANCEL_UNSUBSCRIBE).get()
        self.assertEqual(log.user, self.admin)


class SuppressedEmailListViewTests(_Phase5AdminBase):
    def test_list_renders(self):
        SuppressedEmail.objects.create(
            email="list@example.com", source=SuppressedEmail.Source.MANUAL
        )
        response = self.admin_client.get("/mailings/suppressed/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "list@example.com")


class SuppressedEmailDetailViewTests(_Phase5AdminBase):
    def test_get_renders(self):
        sup = SuppressedEmail.objects.create(
            email="det@example.com", source=SuppressedEmail.Source.BOUNCE_HARD
        )
        response = self.admin_client.get(f"/mailings/suppressed/{sup.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "det@example.com")

    def test_post_cancels(self):
        sup = SuppressedEmail.objects.create(
            email="canc@example.com", source=SuppressedEmail.Source.MANUAL
        )
        response = self.admin_client.post(f"/mailings/suppressed/{sup.pk}/")
        self.assertEqual(response.status_code, 302)
        sup.refresh_from_db()
        self.assertIsNotNone(sup.cancelled_at)
        self.assertEqual(sup.cancelled_by, self.admin)


class SuppressedEmailCreateViewTests(_Phase5AdminBase):
    def test_post_creates(self):
        response = self.admin_client.post(
            "/mailings/suppressed/add/",
            data={"email": "new@example.com", "note": "added"},
        )
        self.assertEqual(response.status_code, 302)
        sup = SuppressedEmail.objects.get(email="new@example.com")
        self.assertEqual(sup.source, SuppressedEmail.Source.MANUAL)
        self.assertEqual(sup.note, "added")

    def test_post_duplicate_active_silently_skipped(self):
        SuppressedEmail.objects.create(
            email="dup@example.com", source=SuppressedEmail.Source.MANUAL
        )
        response = self.admin_client.post(
            "/mailings/suppressed/add/",
            data={"email": "dup@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        # 重複作成されない（partial unique 違反を回避）
        self.assertEqual(
            SuppressedEmail.objects.filter(email="dup@example.com").count(), 1
        )

    def test_post_empty_email_returns_form(self):
        response = self.admin_client.post("/mailings/suppressed/add/", data={"email": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "メアドを入力してください")


# ======================================================================
# Phase 5 是正：UnsubscribeLink.target_email 焼き込み（rev15 §4.5A / §9.5.4）
# ======================================================================


class UnsubscribeLinkBuildTargetEmailTests(_Phase5UIBase):
    """UnsubscribeLink.build() が target_email を必須引数で取り、焼き込むこと（§4.5A）。"""

    def test_build_stores_target_email(self):
        person = self._make_person("Build1", "build1@example.com")
        link = UnsubscribeLink.build(
            campaign=self.campaign, person=person, target_email="build1@example.com"
        )
        self.assertEqual(link.target_email, "build1@example.com")
        # 未保存（DB に書かない）
        self.assertFalse(
            UnsubscribeLink.objects.filter(token=link.token).exists()
        )

    def test_build_with_different_email_than_primary(self):
        # target_email は person.primary_contact.email と独立（呼び出し元が決める値）
        person = self._make_person("Build2", "primary@example.com")
        link = UnsubscribeLink.build(
            campaign=self.campaign, person=person, target_email="snapshot@example.com"
        )
        self.assertEqual(link.target_email, "snapshot@example.com")


class UnsubscribeLinkTargetEmailConstraintTests(_Phase5UIBase):
    """target_email が空文字のまま作成できない構造的担保（§4.5A、rev15 是正）。

    Django の CharField 系デフォルト挙動で「未指定 = 空文字」が入るため、NOT NULL だけ
    では「target_email を渡さない create」が空文字で通ってしまう。本テストは
    CheckConstraint(target_email != '') が効いて IntegrityError になることを確認する。
    """

    def test_create_without_target_email_raises_integrity_error(self):
        person = self._make_person("NoTarget", "x@example.com")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UnsubscribeLink.objects.create(
                    campaign=self.campaign,
                    person=person,
                    token="utok_empty01",
                    # target_email を渡さない → CharField デフォルトで "" → CheckConstraint 違反
                )

    def test_create_with_empty_target_email_raises_integrity_error(self):
        person = self._make_person("EmptyTarget", "y@example.com")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UnsubscribeLink.objects.create(
                    campaign=self.campaign,
                    person=person,
                    token="utok_empty02",
                    target_email="",  # 明示的に空文字でも CheckConstraint で弾く
                )


class EmailContextBakeTargetEmailTests(_Phase5UIBase):
    """EmailContext.prepare(PRODUCTION) が UnsubscribeLink.target_email に to_email を
    焼き込むこと（rev15 §4.5A、Phase 3 配線経由）。"""

    def test_production_bakes_to_email_into_unsubscribe_link(self):
        from mailings.services.email_context import EmailContext, EmailMode

        # body にカスタムタグを含むテンプレート
        template = EmailTemplate.objects.create(
            name="UnsubT",
            subject="件名",
            body="本文 {% unsubscribe_link %}",
            created_by=self.user,
        )
        # MailingConfig（送信に必要）
        MailingConfig.objects.create(
            company_name="TestCo",
            company_address="TestAddr",
            unsubscribe_contact="contact@example.com",
            dkim_domain="example.com",
        )
        person = self._make_person("Bake", "baked-recipient@example.com")
        campaign = Campaign.objects.create(
            name="BakeC",
            template=template,
            mailing_list=self.mailing_list,
            created_by=self.user,
        )
        ctx = EmailContext.prepare(campaign, person, EmailMode.PRODUCTION)
        self.assertEqual(len(ctx.unsubscribe_links), 1)
        # target_email = ctx.to_email = person.primary_contact.email
        self.assertEqual(ctx.unsubscribe_links[0].target_email, "baked-recipient@example.com")
        self.assertEqual(ctx.to_email, "baked-recipient@example.com")


# ======================================================================
# Phase 5 是正：BounceWebhookView 防御（settings フラグ + 共有シークレット、rev15）
# ======================================================================


from django.test import override_settings


@override_settings(BOUNCE_WEBHOOK_ENABLED=False)
class BounceWebhookDisabledTests(_Phase5BounceTestBase):
    """フラグ OFF（既定）で 404 を返し process_bounce に到達しない。"""

    def test_disabled_returns_404(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=json.dumps(
                {"email": "x@example.com", "bounce_type": "hard", "reason": "550"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        # process_bounce に到達していない（SuppressedEmail も作られない）
        self.assertFalse(SuppressedEmail.objects.filter(email="x@example.com").exists())


@override_settings(BOUNCE_WEBHOOK_ENABLED=True, BOUNCE_WEBHOOK_SHARED_SECRET="")
class BounceWebhookSecretMissingTests(_Phase5BounceTestBase):
    """フラグ ON でも共有シークレット未設定なら 404（設定漏れの暴露を防ぐ）。"""

    def test_missing_secret_returns_404(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=json.dumps(
                {"email": "x@example.com", "bounce_type": "hard", "reason": "550"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SuppressedEmail.objects.filter(email="x@example.com").exists())


@override_settings(
    BOUNCE_WEBHOOK_ENABLED=True, BOUNCE_WEBHOOK_SHARED_SECRET="s3cr3t-token"
)
class BounceWebhookSecretMismatchTests(_Phase5BounceTestBase):
    """シークレット不一致は 404 で弾き、process_bounce に到達しない。"""

    def test_mismatch_returns_404(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=json.dumps(
                {"email": "x@example.com", "bounce_type": "hard", "reason": "550"}
            ),
            content_type="application/json",
            HTTP_X_BOUNCE_WEBHOOK_TOKEN="wrong",
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SuppressedEmail.objects.filter(email="x@example.com").exists())

    def test_missing_header_returns_404(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=json.dumps(
                {"email": "x@example.com", "bounce_type": "hard", "reason": "550"}
            ),
            content_type="application/json",
            # ヘッダなし
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SuppressedEmail.objects.filter(email="x@example.com").exists())

    def test_correct_secret_proceeds(self):
        response = self.client.post(
            "/mailings/bounce/webhook/",
            data=json.dumps(
                {"email": "ok@example.com", "bounce_type": "hard", "reason": "550"}
            ),
            content_type="application/json",
            HTTP_X_BOUNCE_WEBHOOK_TOKEN="s3cr3t-token",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SuppressedEmail.objects.filter(email="ok@example.com").exists())


# ======================================================================
# Phase 6：配信レポート画面 + CSV ダウンロード（仕様書 v1.6 第13章 / §6.2.2 /
# §6.2.3 / §13.2、rev15）
# ======================================================================


from mailings.services.report_aggregation import (
    CSV_AVAILABLE_FIELDS,
    CSV_AVAILABLE_KEYS,
    CSV_FIELD_EMAIL,
    CSV_FIELD_FULL_NAME,
    CSV_FIELD_IS_BOUNCED,
    CSV_FIELD_IS_UNSUBSCRIBED,
    CSV_FIELD_LAST_CLICKED_AT,
    CSV_FIELD_ORGANIZATION,
    CSV_FIELD_PERSON_ID,
    CSV_FIELD_STATUS,
    CSV_FIELD_TOTAL_ACCESS,
    CSV_FIELD_VALID_CLICKS,
    csv_rows,
    get_bounced_persons,
    get_campaign_summary,
    get_clicked_persons,
    get_link_aggregates,
    get_unsubscribed_persons,
)


class _Phase6TestBase(TestCase):
    """Phase 6 レポートテスト基底：Campaign + 受信者 + 配信履歴 + リンクの fixture。"""

    def setUp(self):
        self.user = User.objects.create_user(username="phase6_user", password="x")
        self.other_user = User.objects.create_user(
            username="phase6_other", password="x"
        )
        self.template = EmailTemplate.objects.create(
            name="T1", subject="件名", body="本文", created_by=self.user
        )
        self.mailing_list = MailingList.objects.create(
            name="ML1", created_by=self.user
        )
        # Campaign 本体（自分作成、view_all_campaigns なしでも閲覧可）
        self.campaign = Campaign.objects.create(
            name="MyCampaign",
            template=self.template,
            mailing_list=self.mailing_list,
            created_by=self.user,
            total_count=10,
            sent_count=8,
            failed_count=1,
        )
        # 他人キャンペーン（rev18 §4.2：template は OneToOne、別 template を用意）
        self.other_template = EmailTemplate.objects.create(
            name="T_other", subject="件名", body="本文", created_by=self.other_user
        )
        self.other_campaign = Campaign.objects.create(
            name="OthersCampaign",
            template=self.other_template,
            mailing_list=self.mailing_list,
            created_by=self.other_user,
        )
        self.client.force_login(self.user)

    def _make_person(self, full_name, email, organization=""):
        p = Person.objects.create(status="active")
        c = Contact.objects.create(
            person=p,
            status=Contact.Status.PRIMARY,
            full_name=full_name,
            email=email,
            organization=organization,
            created_by=self.user,
        )
        p.primary_contact = c
        p.save(update_fields=["primary_contact", "updated_at"])
        return p, c

    def _make_history(self, person, contact, to_email=None, status=None,
                       organization=None, full_name=None, salutation=None,
                       error=""):
        return DeliveryHistory.objects.create(
            campaign=self.campaign,
            person=person,
            contact=contact,
            to_email=to_email or (contact.email if contact else ""),
            organization_at_send=organization
            if organization is not None
            else contact.organization or "",
            full_name_at_send=full_name
            if full_name is not None
            else (contact.full_name if contact else ""),
            salutation_name_at_send=salutation or "",
            status=status or DeliveryHistory.Status.SENT,
            error_message=error,
        )

    def _make_tracking(self, person, url, *, click_count=0, total_access_count=0,
                        last_clicked_at=None):
        token = uuid.uuid4().hex[:11]
        return TrackingLink.objects.create(
            campaign=self.campaign,
            person=person,
            original_url=url,
            token=token,
            click_count=click_count,
            total_access_count=total_access_count,
            last_clicked_at=last_clicked_at,
        )

    def _make_unsub_link(self, person, *, unsubscribed_at=None, token=None):
        return UnsubscribeLink.objects.create(
            campaign=self.campaign,
            person=person,
            token=token or uuid.uuid4().hex[:11],
            target_email=person.primary_contact.email,
            unsubscribed_at=unsubscribed_at,
        )


class GetCampaignSummaryTests(_Phase6TestBase):
    def test_summary_uses_campaign_fields_for_total_sent(self):
        s = get_campaign_summary(self.campaign)
        self.assertEqual(s["total"], 10)
        self.assertEqual(s["sent"], 8)

    def test_bounced_counts_delivery_history_bounced(self):
        p1, c1 = self._make_person("B1", "b1@example.com")
        p2, c2 = self._make_person("B2", "b2@example.com")
        self._make_history(p1, c1, status=DeliveryHistory.Status.BOUNCED)
        self._make_history(p2, c2, status=DeliveryHistory.Status.SENT)
        s = get_campaign_summary(self.campaign)
        self.assertEqual(s["bounced"], 1)

    def test_clicks_sum_tracking_link_counts(self):
        p1, _ = self._make_person("C1", "c1@example.com")
        p2, _ = self._make_person("C2", "c2@example.com")
        self._make_tracking(p1, "https://a/", click_count=3, total_access_count=5)
        self._make_tracking(p2, "https://a/", click_count=2, total_access_count=4)
        s = get_campaign_summary(self.campaign)
        self.assertEqual(s["valid_clicks"], 5)
        self.assertEqual(s["total_access"], 9)

    def test_unsubscribed_uses_unsubscribelink_not_delivery_history(self):
        """配信停止数の出所は UnsubscribeLink、DeliveryHistory.status='unsubscribed' は使わない。"""
        p1, c1 = self._make_person("U1", "u1@example.com")
        p2, c2 = self._make_person("U2", "u2@example.com")
        # UnsubscribeLink の unsubscribed_at 非NULL は 1 件
        self._make_unsub_link(p1, unsubscribed_at=timezone.now())
        self._make_unsub_link(p2, unsubscribed_at=None)  # まだ確定していない
        # ノイズとして DeliveryHistory.status='unsubscribed' を作っても集計に影響しないこと
        self._make_history(p1, c1, status=DeliveryHistory.Status.UNSUBSCRIBED)
        self._make_history(p2, c2, status=DeliveryHistory.Status.UNSUBSCRIBED)
        s = get_campaign_summary(self.campaign)
        self.assertEqual(s["unsubscribed"], 1)

    def test_empty_campaign_returns_zeros(self):
        s = get_campaign_summary(self.other_campaign)
        self.assertEqual(s["bounced"], 0)
        self.assertEqual(s["valid_clicks"], 0)
        self.assertEqual(s["total_access"], 0)
        self.assertEqual(s["unsubscribed"], 0)


class GetLinkAggregatesTests(_Phase6TestBase):
    def test_per_url_aggregation(self):
        p1, _ = self._make_person("L1", "l1@example.com")
        p2, _ = self._make_person("L2", "l2@example.com")
        p3, _ = self._make_person("L3", "l3@example.com")
        # URL A：p1 と p2 がクリック（unique=2）、click_count 合計 4
        tl_a1 = self._make_tracking(p1, "https://a/", click_count=2, total_access_count=3)
        tl_a2 = self._make_tracking(p2, "https://a/", click_count=2, total_access_count=2)
        # URL B：p3 のみクリック（unique=1）
        tl_b = self._make_tracking(p3, "https://b/", click_count=1, total_access_count=1)
        # ClickLog（unique_users 算出用、is_valid_click=True のもの）
        for tl in (tl_a1, tl_a2):
            ClickLog.objects.create(
                tracking_link=tl, http_method="GET", is_valid_click=True
            )
        ClickLog.objects.create(
            tracking_link=tl_b, http_method="GET", is_valid_click=True
        )
        # ボットクリックは unique_users に含めない
        ClickLog.objects.create(
            tracking_link=tl_a1,
            http_method="GET",
            is_valid_click=False,
            bot_reason="known_bot",
        )

        rows = get_link_aggregates(self.campaign)
        # URL 昇順
        self.assertEqual([r["original_url"] for r in rows], ["https://a/", "https://b/"])
        self.assertEqual(rows[0]["valid_clicks"], 4)
        self.assertEqual(rows[0]["total_access"], 5)
        self.assertEqual(rows[0]["unique_users"], 2)  # ボットクリックは数えない
        self.assertEqual(rows[1]["valid_clicks"], 1)
        self.assertEqual(rows[1]["unique_users"], 1)


class GetClickedPersonsTests(_Phase6TestBase):
    def test_returns_persons_with_click_count_gt_zero_only(self):
        p1, _ = self._make_person("Clk1", "c1@example.com")
        p2, _ = self._make_person("Clk2", "c2@example.com")
        p3, _ = self._make_person("NoClk", "n@example.com")
        now = timezone.now()
        self._make_tracking(
            p1, "https://a/", click_count=2, last_clicked_at=now
        )
        self._make_tracking(
            p2,
            "https://a/",
            click_count=1,
            last_clicked_at=now - timedelta(minutes=5),
        )
        # p3 は click_count=0 → 含まれない
        self._make_tracking(p3, "https://a/", click_count=0)
        rows = get_clicked_persons(self.campaign)
        person_ids = [r["person"].pk for r in rows]
        self.assertIn(p1.pk, person_ids)
        self.assertIn(p2.pk, person_ids)
        self.assertNotIn(p3.pk, person_ids)
        # 最新優先で並ぶ
        self.assertEqual(rows[0]["person"].pk, p1.pk)

    def test_person_dedup_across_multiple_urls(self):
        # 同一 person が複数 URL をクリックしても 1 行
        p, _ = self._make_person("Multi", "m@example.com")
        now = timezone.now()
        self._make_tracking(p, "https://a/", click_count=2, last_clicked_at=now)
        self._make_tracking(
            p, "https://b/", click_count=1, last_clicked_at=now - timedelta(minutes=1)
        )
        rows = get_clicked_persons(self.campaign)
        self.assertEqual(len(rows), 1)
        # last_action_at は最大値（最新の last_clicked_at）
        self.assertEqual(rows[0]["last_action_at"], now)


class GetBouncedPersonsTests(_Phase6TestBase):
    def test_only_bounced_status_included(self):
        p1, c1 = self._make_person("B1", "b1@example.com")
        p2, c2 = self._make_person("B2", "b2@example.com")
        self._make_history(p1, c1, status=DeliveryHistory.Status.BOUNCED)
        self._make_history(p2, c2, status=DeliveryHistory.Status.SENT)
        rows = get_bounced_persons(self.campaign)
        person_ids = [r["person"].pk for r in rows]
        self.assertEqual(person_ids, [p1.pk])


class GetUnsubscribedPersonsTests(_Phase6TestBase):
    def test_uses_unsubscribelink_not_delivery_history(self):
        p1, c1 = self._make_person("UP1", "up1@example.com")
        p2, _ = self._make_person("UP2", "up2@example.com")
        self._make_unsub_link(p1, unsubscribed_at=timezone.now())
        self._make_unsub_link(p2, unsubscribed_at=None)
        # ノイズ：DeliveryHistory.status='unsubscribed' を作っても出ないこと
        self._make_history(p2, c1, status=DeliveryHistory.Status.UNSUBSCRIBED)
        rows = get_unsubscribed_persons(self.campaign)
        person_ids = [r["person"].pk for r in rows]
        self.assertEqual(person_ids, [p1.pk])


class CsvRowsTests(_Phase6TestBase):
    def test_csv_header_and_snapshot_values(self):
        p, c = self._make_person("Snap", "snap@example.com", organization="OrgX")
        self._make_history(
            p,
            c,
            to_email="snap@example.com",
            organization="OrgAtSendTime",  # スナップショット値
            full_name="NameAtSendTime",
            salutation="様",
        )
        rows = list(
            csv_rows(
                self.campaign,
                [
                    CSV_FIELD_PERSON_ID,
                    CSV_FIELD_ORGANIZATION,
                    CSV_FIELD_FULL_NAME,
                    CSV_FIELD_EMAIL,
                ],
            )
        )
        header, data = rows[0], rows[1]
        self.assertEqual(
            header, ("Person ID", "会社名", "氏名", "メアド")
        )
        self.assertEqual(data[0], str(p.pk))
        # スナップショットが取れている（現在の Contact.organization='OrgX' ではなくスナップショット値）
        self.assertEqual(data[1], "OrgAtSendTime")
        self.assertEqual(data[2], "NameAtSendTime")
        self.assertEqual(data[3], "snap@example.com")

    def test_csv_is_unsubscribed_uses_current_person_state(self):
        p, c = self._make_person("IU", "iu@example.com")
        self._make_history(p, c)
        # 現在状態で is_unsubscribed=True に
        Person.objects.filter(pk=p.pk).update(is_unsubscribed=True)
        rows = list(csv_rows(self.campaign, [CSV_FIELD_IS_UNSUBSCRIBED]))
        self.assertEqual(rows[1][0], "1")

    def test_csv_is_bounced_based_on_status(self):
        p, c = self._make_person("IB", "ib@example.com")
        self._make_history(p, c, status=DeliveryHistory.Status.BOUNCED, error="550")
        rows = list(csv_rows(self.campaign, [CSV_FIELD_IS_BOUNCED]))
        self.assertEqual(rows[1][0], "1")

    def test_csv_click_aggregates_from_tracking_link(self):
        p, c = self._make_person("CA", "ca@example.com")
        self._make_history(p, c)
        now = timezone.now()
        self._make_tracking(
            p,
            "https://x/",
            click_count=3,
            total_access_count=5,
            last_clicked_at=now,
        )
        self._make_tracking(
            p,
            "https://y/",
            click_count=2,
            total_access_count=2,
            last_clicked_at=now - timedelta(minutes=10),
        )
        rows = list(
            csv_rows(
                self.campaign,
                [
                    CSV_FIELD_VALID_CLICKS,
                    CSV_FIELD_TOTAL_ACCESS,
                    CSV_FIELD_LAST_CLICKED_AT,
                ],
            )
        )
        self.assertEqual(rows[1][0], "5")  # 3+2
        self.assertEqual(rows[1][1], "7")  # 5+2
        # last_clicked_at = max（最新）
        self.assertEqual(rows[1][2], now.isoformat())

    def test_unknown_field_silently_filtered(self):
        rows = list(
            csv_rows(self.campaign, ["bogus_field", CSV_FIELD_PERSON_ID])
        )
        # ヘッダのみ
        self.assertEqual(rows[0], ("Person ID",))


class CampaignListViewTests(_Phase6TestBase):
    def test_list_filters_to_own_when_no_view_all_perm(self):
        response = self.client.get("/mailings/campaigns/")
        self.assertEqual(response.status_code, 200)
        # 自キャンペーンは表示、他人は非表示
        self.assertContains(response, "MyCampaign")
        self.assertNotContains(response, "OthersCampaign")

    def test_list_shows_all_when_view_all_perm(self):
        perm = Permission.objects.get(codename="view_all_campaigns")
        self.user.user_permissions.add(perm)
        # キャッシュをクリアするため再ログイン
        self.client.force_login(self.user)
        response = self.client.get("/mailings/campaigns/")
        self.assertContains(response, "MyCampaign")
        self.assertContains(response, "OthersCampaign")

    def test_list_renders_summary_card(self):
        # バウンス 1 件作って表示確認
        p, c = self._make_person("Bx", "bx@example.com")
        self._make_history(p, c, status=DeliveryHistory.Status.BOUNCED)
        response = self.client.get("/mailings/campaigns/")
        self.assertContains(response, "MyCampaign")


class CampaignReportViewTests(_Phase6TestBase):
    def test_view_renders_for_owner(self):
        response = self.client.get(
            f"/mailings/campaigns/{self.campaign.pk}/report/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MyCampaign")

    def test_view_forbidden_for_non_owner_without_perm(self):
        response = self.client.get(
            f"/mailings/campaigns/{self.other_campaign.pk}/report/"
        )
        self.assertEqual(response.status_code, 403)

    def test_view_allowed_for_view_all_perm(self):
        perm = Permission.objects.get(codename="view_all_campaigns")
        self.user.user_permissions.add(perm)
        self.client.force_login(self.user)
        response = self.client.get(
            f"/mailings/campaigns/{self.other_campaign.pk}/report/"
        )
        self.assertEqual(response.status_code, 200)


class CampaignReportFilteredListViewsTests(_Phase6TestBase):
    def test_clicked_view_renders_only_clickers(self):
        p1, _ = self._make_person("Clk", "clk@example.com")
        self._make_tracking(
            p1, "https://a/", click_count=1, last_clicked_at=timezone.now()
        )
        response = self.client.get(
            f"/mailings/campaigns/{self.campaign.pk}/report/clicked/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "clk@example.com")

    def test_bounced_view_renders_only_bouncers(self):
        p, c = self._make_person("Bnc", "bnc@example.com")
        self._make_history(p, c, status=DeliveryHistory.Status.BOUNCED)
        response = self.client.get(
            f"/mailings/campaigns/{self.campaign.pk}/report/bounced/"
        )
        self.assertContains(response, "bnc@example.com")

    def test_unsubscribed_view_renders_only_unsubscribers(self):
        p, _ = self._make_person("Uns", "uns@example.com")
        self._make_unsub_link(p, unsubscribed_at=timezone.now())
        response = self.client.get(
            f"/mailings/campaigns/{self.campaign.pk}/report/unsubscribed/"
        )
        self.assertContains(response, "uns@example.com")

    def test_forbidden_for_non_owner(self):
        response = self.client.get(
            f"/mailings/campaigns/{self.other_campaign.pk}/report/clicked/"
        )
        self.assertEqual(response.status_code, 403)


class CampaignReportCSVViewTests(_Phase6TestBase):
    def test_get_renders_form(self):
        response = self.client.get(
            f"/mailings/campaigns/{self.campaign.pk}/report/csv/"
        )
        self.assertEqual(response.status_code, 200)
        # 全項目のラベルが出る
        for _, label in CSV_AVAILABLE_FIELDS:
            self.assertContains(response, label)

    def test_post_returns_csv_with_bom(self):
        p, c = self._make_person("CSV1", "csv1@example.com", organization="OrgZ")
        self._make_history(
            p, c, organization="OrgZ", full_name="CSV1", salutation="様"
        )
        response = self.client.post(
            f"/mailings/campaigns/{self.campaign.pk}/report/csv/",
            data={"fields": [CSV_FIELD_EMAIL, CSV_FIELD_ORGANIZATION]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        body = response.content.decode("utf-8")
        # BOM が先頭にある（Excel 文字化け回避）
        self.assertTrue(body.startswith("﻿"))
        self.assertIn("csv1@example.com", body)
        self.assertIn("OrgZ", body)
        # Content-Disposition でダウンロード扱い
        self.assertIn("attachment", response["Content-Disposition"])

    def test_post_with_no_fields_returns_form(self):
        response = self.client.post(
            f"/mailings/campaigns/{self.campaign.pk}/report/csv/", data={}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 つ以上の項目を選択してください")

    def test_post_unknown_field_filtered(self):
        p, c = self._make_person("CSV2", "csv2@example.com")
        self._make_history(p, c)
        response = self.client.post(
            f"/mailings/campaigns/{self.campaign.pk}/report/csv/",
            data={"fields": ["bogus", CSV_FIELD_EMAIL]},
        )
        # bogus は弾かれて EMAIL のみ
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("メアド", body)

    def test_post_forbidden_for_non_owner(self):
        response = self.client.post(
            f"/mailings/campaigns/{self.other_campaign.pk}/report/csv/",
            data={"fields": [CSV_FIELD_EMAIL]},
        )
        self.assertEqual(response.status_code, 403)
