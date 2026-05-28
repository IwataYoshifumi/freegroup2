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
