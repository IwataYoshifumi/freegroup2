import re
from datetime import timedelta
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from activities.admin import ActivityAdmin, ActivityPersonAdmin, ActivityUserAdmin
from activities.models import Activity, ActivityPerson, ActivityType, ActivityUser, Direction
from companies.models import Company
from contacts.models import Contact
from deals.models import Deal, DealPerson, DealUser, PersonRole, UserRole
from mailings.models import Campaign, ClickLog, EmailTemplate, TrackingLink
from persons.models import Person
from back_navigator.back_navigator import BackNavigator

User = get_user_model()


class ActivityModelValidationTests(TestCase):
    """Activity / ActivityUser / ActivityPerson のバリデーション・制約検証（仕様書 §3.1、§5.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="act_user", password="password")
        self.user2 = User.objects.create_user(username="act_user2", password="password")
        self.person = Person.objects.create()
        self.person2 = Person.objects.create()
        self.deal = Deal.objects.create(
            name="Activity Test Deal",
            primary_person=self.person,
            owner=self.user,
        )

    def test_occurred_at_future_datetime_raises_validation_error(self):
        future_time = timezone.now() + timedelta(hours=2)
        activity = Activity(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=future_time,
            user=self.user,
        )
        with self.assertRaises(ValidationError) as ctx:
            activity.full_clean()
        self.assertIn("occurred_at", ctx.exception.message_dict)

    def test_occurred_at_past_or_present_succeeds(self):
        past_time = timezone.now() - timedelta(hours=1)
        activity = Activity(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=past_time,
            user=self.user,
        )
        activity.full_clean()  # should not raise
        activity.save()

    def test_activity_user_cannot_be_activity_user(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        activity_user = ActivityUser(
            activity=activity,
            user=self.user,
            role=UserRole.SUPPORT,
        )
        with self.assertRaises(ValidationError) as ctx:
            activity_user.full_clean()
        self.assertTrue(
            any("実施者と同じUser" in msg for msg in ctx.exception.messages)
        )

    def test_activity_user_other_user_succeeds(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        activity_user = ActivityUser(
            activity=activity,
            user=self.user2,
            role=UserRole.SUPPORT,
        )
        activity_user.full_clean()  # should not raise
        activity_user.save()

    def test_activity_user_unique_constraint(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        ActivityUser.objects.create(activity=activity, user=self.user2, role=UserRole.SUPPORT)
        with self.assertRaises(IntegrityError):
            ActivityUser.objects.create(activity=activity, user=self.user2, role=UserRole.APPROVER)

    def test_activity_person_unique_constraint(self):
        activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        ActivityPerson.objects.create(activity=activity, person=self.person, role=PersonRole.ATTENDEE)
        with self.assertRaises(IntegrityError):
            ActivityPerson.objects.create(activity=activity, person=self.person, role=PersonRole.CONTACT_WINDOW)


class ActivityAdminTests(TestCase):
    """ActivityAdmin の保護制御検証（仕様書 §7.6.1）。"""

    def setUp(self):
        self.site = AdminSite()
        self.activity_admin = ActivityAdmin(Activity, self.site)
        self.activity_person_admin = ActivityPersonAdmin(ActivityPerson, self.site)
        self.activity_user_admin = ActivityUserAdmin(ActivityUser, self.site)
        self.user = User.objects.create_user(username="admin_user", is_staff=True)
        self.person = Person.objects.create()
        self.deal = Deal.objects.create(
            name="Admin Test Deal",
            primary_person=self.person,
            owner=self.user,
        )
        self.activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )

    def test_activity_admin_readonly_fields_new_object(self):
        readonly = self.activity_admin.get_readonly_fields(request=None, obj=None)
        self.assertEqual(readonly, [])

    def test_activity_admin_readonly_fields_existing_object(self):
        readonly = self.activity_admin.get_readonly_fields(request=None, obj=self.activity)
        self.assertIn("is_archived", readonly)

    def test_activity_person_admin_autocomplete_fields(self):
        self.assertIn("activity", self.activity_person_admin.autocomplete_fields)
        self.assertIn("person", self.activity_person_admin.autocomplete_fields)

    def test_activity_user_admin_autocomplete_fields(self):
        self.assertIn("activity", self.activity_user_admin.autocomplete_fields)
        self.assertIn("user", self.activity_user_admin.autocomplete_fields)


class ActivityServiceTests(TestCase):
    """activities/services.py のコアロジック検証（仕様書 §3.5.2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="sales_rep", password="password")

    def test_get_unfollowed_campaign_persons(self):
        from activities.services import get_unfollowed_campaign_persons
        from mailings.models import Campaign, ClickLog, EmailTemplate, TrackingLink

        template = EmailTemplate.objects.create(name="Webinar Template", subject="案内", body="本文", created_by=self.user)
        campaign = Campaign.objects.create(name="新商品ウェビナー案内", template=template, created_by=self.user)

        # 1. クリック済み・有効・活動なし -> 未フォロー
        p1 = Person.objects.create()
        tl1 = TrackingLink.objects.create(campaign=campaign, person=p1, original_url="https://example.com/1", token="tok1")
        ClickLog.objects.create(tracking_link=tl1, http_method="GET", is_valid_click=True)

        # 2. クリックしたが無効（ボット等、is_valid_click=False） -> 対象外
        p2 = Person.objects.create()
        tl2 = TrackingLink.objects.create(campaign=campaign, person=p2, original_url="https://example.com/2", token="tok2")
        ClickLog.objects.create(tracking_link=tl2, http_method="GET", is_valid_click=False)

        # 3. クリック済み・有効・活動あり（ActivityPersonに登録） -> フォロー済み（対象外）
        p3 = Person.objects.create()
        tl3 = TrackingLink.objects.create(campaign=campaign, person=p3, original_url="https://example.com/3", token="tok3")
        ClickLog.objects.create(tracking_link=tl3, http_method="GET", is_valid_click=True)
        act3 = Activity.objects.create(
            campaign=campaign,
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
        )
        ActivityPerson.objects.create(activity=act3, person=p3, role=PersonRole.ATTENDEE)

        # 4. Personマージ：旧Person p4_old がクリックし、p4_surviving にマージされた場合 -> 生存側 p4_surviving が返る
        p4_surviving = Person.objects.create()
        p4_old = Person.objects.create(merged_into=p4_surviving, status=Person.Status.MERGED)
        tl4 = TrackingLink.objects.create(campaign=campaign, person=p4_old, original_url="https://example.com/4", token="tok4")
        ClickLog.objects.create(tracking_link=tl4, http_method="GET", is_valid_click=True)

        # 5. 退職・アーカイブ済み Person（status='retired'）も後任フォローが必要なため対象に含まれる
        p5_retired = Person.objects.create(status="retired")
        tl5 = TrackingLink.objects.create(campaign=campaign, person=p5_retired, original_url="https://example.com/5", token="tok5")
        ClickLog.objects.create(tracking_link=tl5, http_method="GET", is_valid_click=True)

        unfollowed_qs = get_unfollowed_campaign_persons(campaign)
        unfollowed_ids = set(unfollowed_qs.values_list("id", flat=True))

        self.assertIn(p1.id, unfollowed_ids)
        self.assertNotIn(p2.id, unfollowed_ids)
        self.assertNotIn(p3.id, unfollowed_ids)
        self.assertIn(p4_surviving.id, unfollowed_ids)
        self.assertNotIn(p4_old.id, unfollowed_ids)
        self.assertIn(p5_retired.id, unfollowed_ids)

    def test_archive_activity(self):
        from actionlogs.models import ActionLog
        from activities.services import archive_activity

        person = Person.objects.create()
        activity = Activity.objects.create(
            activity_type=Activity.ActivityType.WEB_MEETING,
            occurred_at=timezone.now(),
            user=self.user,
        )
        archived = archive_activity(activity, user=self.user)
        self.assertTrue(archived.is_archived)

        log = ActionLog.objects.filter(action="activity_archived", content_type__model="activity").latest("created_at")
        self.assertEqual(str(log.content_object.id), str(activity.id))


class ActivityPermissionTests(TestCase):
    """activities/permissions.py の認可述語検証（仕様書 §7.3）。"""

    def setUp(self):
        from django.contrib.auth.models import Permission
        self.user = User.objects.create_user(username="act_owner", password="password")
        self.attendee = User.objects.create_user(username="act_attendee", password="password")
        self.outsider = User.objects.create_user(username="outsider", password="password")
        self.privileged_user = User.objects.create_user(username="privileged", password="password")

        change_act_perm = Permission.objects.get(codename="change_activity")
        view_all_perm = Permission.objects.get(codename="view_all_activities")
        edit_all_perm = Permission.objects.get(codename="edit_all_activities")

        self.user.user_permissions.add(change_act_perm)
        self.attendee.user_permissions.add(change_act_perm)
        self.privileged_user.user_permissions.add(change_act_perm, view_all_perm, edit_all_perm)

        self.person = Person.objects.create()
        self.deal = Deal.objects.create(name="紐付き案件", primary_person=self.person, owner=self.user)
        self.activity = Activity.objects.create(
            deal=self.deal,
            activity_type=Activity.ActivityType.VISIT,
            occurred_at=timezone.now(),
            user=self.user,
        )
        ActivityUser.objects.create(activity=self.activity, user=self.attendee, role=UserRole.SUPPORT)

    def test_can_view_activity(self):
        from activities.permissions import can_view_activity

        self.assertTrue(can_view_activity(self.user, self.activity))
        self.assertTrue(can_view_activity(self.attendee, self.activity))
        self.assertTrue(can_view_activity(self.privileged_user, self.activity))
        self.assertFalse(can_view_activity(self.outsider, self.activity))

    def test_can_edit_activity(self):
        from activities.permissions import can_edit_activity

        self.assertTrue(can_edit_activity(self.user, self.activity))
        self.assertTrue(can_edit_activity(self.attendee, self.activity))
        self.assertTrue(can_edit_activity(self.privileged_user, self.activity))
        self.assertFalse(can_edit_activity(self.outsider, self.activity))

        # change_activity 権限を失った場合は実施者でも不可（仕様書 §7.3 の AND 条件厳守）
        from django.contrib.auth.models import Permission
        self.user.user_permissions.remove(Permission.objects.get(codename="change_activity"))
        self.user = User.objects.get(pk=self.user.pk)
        self.assertFalse(can_edit_activity(self.user, self.activity))

    def test_can_archive_activity(self):
        from activities.permissions import can_archive_activity

        # 実施者・特権保持者は可、ActivityUser（同席者）は不可
        self.assertTrue(can_archive_activity(self.user, self.activity))
        self.assertTrue(can_archive_activity(self.privileged_user, self.activity))
        self.assertFalse(can_archive_activity(self.attendee, self.activity))
        self.assertFalse(can_archive_activity(self.outsider, self.activity))

    def test_visible_activities_for(self):
        from activities.permissions import visible_activities_for

        act2 = Activity.objects.create(
            activity_type=Activity.ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.outsider,
        )

        # privileged は全件（2件）
        self.assertEqual(visible_activities_for(self.privileged_user).count(), 2)

        # user は自身の活動（1件）
        qs_user = visible_activities_for(self.user)
        self.assertEqual(qs_user.count(), 1)
        self.assertEqual(qs_user.first(), self.activity)

        # attendee は参加者となっている活動（1件）
        qs_attendee = visible_activities_for(self.attendee)
        self.assertEqual(qs_attendee.count(), 1)
        self.assertEqual(qs_attendee.first(), self.activity)

        # outsider は act2 のみ（1件）
        qs_outsider = visible_activities_for(self.outsider)
        self.assertEqual(qs_outsider.count(), 1)
        self.assertEqual(qs_outsider.first(), act2)


class ActivityViewTests(TestCase):
    """activities View層の認可・表示・画面遷移の検証（仕様書 第3章, §7.3）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="act_owner", password="password")
        self.attendee = User.objects.create_user(username="act_attendee", password="password")
        self.outsider = User.objects.create_user(username="act_outsider", password="password")

        add_perm = Permission.objects.get(codename="add_activity")
        change_perm = Permission.objects.get(codename="change_activity")
        add_att_perm = Permission.objects.get(codename="add_attachment")
        self.user.user_permissions.add(add_perm, change_perm, add_att_perm)
        self.attendee.user_permissions.add(add_perm, change_perm)

        self.person = Person.objects.create()
        self.deal = Deal.objects.create(name="提案中案件", primary_person=self.person, owner=self.user)
        self.activity = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.VISIT,
            direction=Direction.OUTGOING,
            occurred_at=timezone.now(),
            user=self.user,
            memo="初回訪問議事録",
        )
        ActivityUser.objects.create(activity=self.activity, user=self.attendee, role=UserRole.SUPPORT)
        ActivityPerson.objects.create(activity=self.activity, person=self.person, role=PersonRole.ATTENDEE)

    def test_activity_list_view_anonymous_redirect(self):
        response = self.client.get(reverse("activities:activity_list"))
        self.assertEqual(response.status_code, 302)

    def test_activity_list_view_authenticated(self):
        self.client.login(username="act_owner", password="password")
        response = self.client.get(reverse("activities:activity_list") + "?mode=all")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "初回訪問議事録")
        # HIG準拠レイアウト・表示（コンテナ幅、種別バッジ、方向バッジ、省略メモ）
        self.assertContains(response, "max-width: 1200px;")
        self.assertContains(response, '<span class="app-badge app-badge--neutral">訪問</span>')
        self.assertContains(response, '<span class="app-badge app-badge--info">発信</span>')
        self.assertContains(response, 'white-space:nowrap;')
        # 不要ボタン撤去（日報/全期間ボタンの完全撤去）および新規作成ボタン配置
        self.assertNotContains(response, "本日の日報")
        self.assertNotContains(response, "全期間一覧")
        self.assertContains(response, "活動新規作成")
        # 期間検索フォーム要素（occurred_after, occurred_before, クイックプリセット）
        self.assertContains(response, 'name="occurred_after"')
        self.assertContains(response, 'name="occurred_before"')
        self.assertContains(response, 'js-date-quick')
        self.assertContains(response, 'js-date-clear')
        # 実施者・同席者の複数選択 hidden input およびモーダル
        self.assertContains(response, 'name="user_ids"')
        self.assertContains(response, 'name="attendee_user_ids"')
        self.assertContains(response, 'id="userSelectModal"')
        self.assertContains(response, 'id="attendeeSelectModal"')
        self.assertContains(response, 'data-target="userSelectModal"')
        self.assertContains(response, 'data-target="attendeeSelectModal"')
        # 状態ボタングループ
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'js-status-btn')
        # ソート、表示件数フォーム要素、プレースホルダー
        self.assertContains(response, 'name="sort"')
        self.assertContains(response, 'name="per_page"')
        self.assertContains(response, 'placeholder="内容・場所・案件名・会社名・担当者名..."')
        # 関連案件リンクに back_stack が含まれていること
        deal_url = reverse("deals:deal_detail", kwargs={"pk": self.deal.pk})
        self.assertIn(f'href="{deal_url}?back_stack=', response.content.decode("utf-8"))

    def test_activity_list_date_range_filter(self):
        """occurred_after / occurred_before による期間範囲検索の検証。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_list")

        base_time = timezone.now()
        act_past = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.PHONE,
            direction=Direction.INCOMING,
            occurred_at=base_time - timezone.timedelta(days=10),
            user=self.user,
            memo="10日前の電話",
        )
        act_mid = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.EMAIL,
            direction=Direction.OUTGOING,
            occurred_at=base_time - timezone.timedelta(days=5),
            user=self.user,
            memo="5日前のメール",
        )

        d_past = (base_time - timezone.timedelta(days=10)).strftime("%Y-%m-%d")
        d_mid = (base_time - timezone.timedelta(days=5)).strftime("%Y-%m-%d")

        # 1. occurred_after のみ（5日前以降） -> act_mid と self.activity(当日) が含まれ、act_past は除外
        resp_after = self.client.get(f"{url}?occurred_after={d_mid}")
        self.assertEqual(resp_after.status_code, 200)
        self.assertContains(resp_after, "5日前のメール")
        self.assertContains(resp_after, "初回訪問議事録")
        self.assertNotContains(resp_after, "10日前の電話")

        # 2. occurred_before のみ（5日前以前） -> act_past と act_mid が含まれ、self.activity は除外
        resp_before = self.client.get(f"{url}?occurred_before={d_mid}")
        self.assertEqual(resp_before.status_code, 200)
        self.assertContains(resp_before, "10日前の電話")
        self.assertContains(resp_before, "5日前のメール")
        self.assertNotContains(resp_before, "初回訪問議事録")

        # 3. occurred_after & occurred_before（期間指定：10日前〜5日前）
        resp_range = self.client.get(f"{url}?occurred_after={d_past}&occurred_before={d_mid}")
        self.assertEqual(resp_range.status_code, 200)
        self.assertContains(resp_range, "10日前の電話")
        self.assertContains(resp_range, "5日前のメール")
        self.assertNotContains(resp_range, "初回訪問議事録")

    def test_activity_list_user_filter_sort_and_pagination(self):
        """実施者絞り込み（デフォルト自分、すべて選択）、ソート（昇順/降順）、表示件数（per_page）の検証。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_list")

        # 別のユーザーによる活動を作成
        other_user = self.attendee
        act_other = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.WEB_MEETING,
            direction=Direction.INCOMING,
            occurred_at=timezone.now(),
            user=other_user,
            memo="同席者のWeb会議",
        )

        # 1. 初回アクセス（パラメータなし）: デフォルトでログインユーザー自身（act_owner）の活動のみ表示
        res_initial = self.client.get(url)
        self.assertEqual(res_initial.status_code, 200)
        self.assertContains(res_initial, "初回訪問議事録")
        self.assertNotContains(res_initial, "同席者のWeb会議")
        self.assertEqual(res_initial.context["current_user_id"], str(self.user.id))

        # 2. 実施者「すべて」選択（user_id=""）: 両方の活動が表示される
        res_all_users = self.client.get(f"{url}?mode=all&user_id=")
        self.assertEqual(res_all_users.status_code, 200)
        self.assertContains(res_all_users, "初回訪問議事録")
        self.assertContains(res_all_users, "同席者のWeb会議")
        self.assertEqual(res_all_users.context["current_user_id"], "")

        # 3. 別の実施者選択（user_id=other_user.id）: other_user の活動のみ表示
        res_other = self.client.get(f"{url}?mode=all&user_id={other_user.id}")
        self.assertEqual(res_other.status_code, 200)
        self.assertNotContains(res_other, "初回訪問議事録")
        self.assertContains(res_other, "同席者のWeb会議")
        self.assertEqual(res_other.context["current_user_id"], str(other_user.id))

        # 4. ソート順: date_asc vs date_desc
        past_act = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.PHONE,
            direction=Direction.OUTGOING,
            occurred_at=timezone.now() - timezone.timedelta(days=30),
            user=self.user,
            memo="30日前の電話",
        )
        # date_asc (古い順): past_act が先頭
        res_asc = self.client.get(f"{url}?mode=all&user_id=&sort=date_asc")
        self.assertEqual(res_asc.status_code, 200)
        acts_asc = list(res_asc.context["activities"])
        self.assertEqual(acts_asc[0], past_act)

        # date_desc (新しい順): past_act が末尾
        res_desc = self.client.get(f"{url}?mode=all&user_id=&sort=date_desc")
        self.assertEqual(res_desc.status_code, 200)
        acts_desc = list(res_desc.context["activities"])
        self.assertEqual(acts_desc[-1], past_act)

        # 5. 表示件数 (per_page) の動的切り替え
        for i in range(23):
            Activity.objects.create(
                deal=self.deal,
                activity_type=ActivityType.OTHER,
                occurred_at=timezone.now() - timezone.timedelta(minutes=i + 1),
                user=self.user,
                memo=f"追加活動{i}",
            )
        # per_page=20: ページネーションあり（1ページ目20件）
        res_p20 = self.client.get(f"{url}?mode=all&user_id=&per_page=20")
        self.assertEqual(len(res_p20.context["activities"]), 20)
        self.assertTrue(res_p20.context["is_paginated"])

        # per_page=50: 1ページですべて収まる（26件）
        res_p50 = self.client.get(f"{url}?mode=all&user_id=&per_page=50")
        self.assertEqual(len(res_p50.context["activities"]), 26)
        self.assertFalse(res_p50.context["is_paginated"])

    def test_activity_list_keyword_search(self):
        """会社名（案件・パーソン）、実施者名、同席者名によるキーワード検索（?q=...&user_id=）の検証。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_list")

        # 1. 案件の取引先会社名
        deal_company = Company.objects.create(organization="株式会社テスト商事")
        deal_with_company = Deal.objects.create(
            name="商事案件",
            primary_person=self.person,
            company=deal_company,
            owner=self.user,
        )
        act_deal_company = Activity.objects.create(
            deal=deal_with_company,
            activity_type=ActivityType.VISIT,
            occurred_at=timezone.now(),
            user=self.user,
            memo="商事案件の活動メモ",
        )

        # 2. 相手方パーソンの所属会社名
        person_company = Company.objects.create(organization="未来テクノロジー合同会社")
        person_with_company = Person.objects.create()
        contact_person = Contact.objects.create(
            person=person_with_company,
            company=person_company,
            last_name="鈴木",
            first_name="一郎",
        )
        person_with_company.primary_contact = contact_person
        person_with_company.save(update_fields=["primary_contact"])
        act_person_company = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.EMAIL,
            occurred_at=timezone.now(),
            user=self.user,
            memo="パーソン会社所属の活動メモ",
        )
        ActivityPerson.objects.create(
            activity=act_person_company,
            person=person_with_company,
            role=PersonRole.ATTENDEE,
        )

        # 3. 実施者ユーザー名・氏名
        rep_user = User.objects.create_user(
            username="rep_tanaka",
            first_name="花子",
            last_name="田中",
            password="password",
        )
        act_user_search = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=rep_user,
            memo="田中さんの活動メモ",
        )

        # 4. 同席者ユーザー名・氏名
        support_user = User.objects.create_user(
            username="supporter_yamamoto",
            first_name="三郎",
            last_name="山本",
            password="password",
        )
        act_supporter_search = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.WEB_MEETING,
            occurred_at=timezone.now(),
            user=self.user,
            memo="山本さん同席の活動メモ",
        )
        ActivityUser.objects.create(
            activity=act_supporter_search,
            user=support_user,
            role=UserRole.SUPPORT,
        )

        # 案件会社名で検索
        res1 = self.client.get(f"{url}?mode=all&user_id=&q=テスト商事")
        self.assertEqual(res1.status_code, 200)
        self.assertContains(res1, "商事案件の活動メモ")
        self.assertNotContains(res1, "パーソン会社所属の活動メモ")

        # パーソン所属会社名で検索
        res2 = self.client.get(f"{url}?mode=all&user_id=&q=未来テクノロジー")
        self.assertEqual(res2.status_code, 200)
        self.assertContains(res2, "パーソン会社所属の活動メモ")
        self.assertNotContains(res2, "商事案件の活動メモ")

        # 実施者（ユーザー名・姓・名）で検索
        res3_user = self.client.get(f"{url}?mode=all&user_id=&q=rep_tanaka")
        self.assertEqual(res3_user.status_code, 200)
        self.assertContains(res3_user, "田中さんの活動メモ")

        res3_last = self.client.get(f"{url}?mode=all&user_id=&q=田中")
        self.assertEqual(res3_last.status_code, 200)
        self.assertContains(res3_last, "田中さんの活動メモ")

        res3_first = self.client.get(f"{url}?mode=all&user_id=&q=花子")
        self.assertEqual(res3_first.status_code, 200)
        self.assertContains(res3_first, "田中さんの活動メモ")

        # 同席者（ユーザー名・姓・名）で検索
        res4_user = self.client.get(f"{url}?mode=all&user_id=&q=supporter_yamamoto")
        self.assertEqual(res4_user.status_code, 200)
        self.assertContains(res4_user, "山本さん同席の活動メモ")

        res4_last = self.client.get(f"{url}?mode=all&user_id=&q=山本")
        self.assertEqual(res4_last.status_code, 200)
        self.assertContains(res4_last, "山本さん同席の活動メモ")

        res4_first = self.client.get(f"{url}?mode=all&user_id=&q=三郎")
        self.assertEqual(res4_first.status_code, 200)
        self.assertContains(res4_first, "山本さん同席の活動メモ")

        # 不一致キーワードで検索
        res_none = self.client.get(f"{url}?mode=all&user_id=&q=存在しないキーワードXYZ")
        self.assertEqual(res_none.status_code, 200)
        self.assertNotContains(res_none, "商事案件の活動メモ")
        self.assertNotContains(res_none, "パーソン会社所属の活動メモ")
        self.assertNotContains(res_none, "田中さんの活動メモ")
        self.assertNotContains(res_none, "山本さん同席の活動メモ")

    def test_activity_list_multi_user_filter(self):
        """実施者（user_ids）複数指定による絞り込みの検証。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_list")

        user_a = self.user  # act_owner
        user_b = self.attendee  # act_attendee
        user_c = User.objects.create_user(username="user_c", password="password")

        act_a = self.activity  # user_a
        act_b = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.WEB_MEETING,
            occurred_at=timezone.now(),
            user=user_b,
            memo="Bさんの活動",
        )
        act_c = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=user_c,
            memo="Cさんの活動",
        )

        # 複数指定（カンマ区切り: user_a, user_b）
        res = self.client.get(f"{url}?mode=all&user_ids={user_a.id},{user_b.id}")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "初回訪問議事録")
        self.assertContains(res, "Bさんの活動")
        self.assertNotContains(res, "Cさんの活動")

        # 複数指定（getlist 形式: user_ids=A & user_ids=C）
        res_list = self.client.get(f"{url}?mode=all&user_ids={user_a.id}&user_ids={user_c.id}")
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "初回訪問議事録")
        self.assertNotContains(res_list, "Bさんの活動")
        self.assertContains(res_list, "Cさんの活動")

    def test_activity_list_attendee_user_filter(self):
        """同席者（attendee_user_ids）指定による絞り込みの検証。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_list")

        attendee_x = User.objects.create_user(username="attendee_x", password="password")
        act_with_x = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.VISIT,
            occurred_at=timezone.now(),
            user=self.user,
            memo="Xさん同席の訪問",
        )
        ActivityUser.objects.create(activity=act_with_x, user=attendee_x, role=UserRole.SUPPORT)

        # 同席者 attendee_x で絞り込み
        res = self.client.get(f"{url}?mode=all&user_id=&attendee_user_ids={attendee_x.id}")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Xさん同席の訪問")
        # 別の同席者（self.attendee）の活動は除外
        self.assertNotContains(res, "初回訪問議事録")

    def test_activity_list_status_filter_and_button_group(self):
        """ボタングループ形式のステータス（active, archived, all）切り替え検証。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_list")

        act_archived = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.PHONE,
            occurred_at=timezone.now(),
            user=self.user,
            memo="アーカイブされた活動",
            is_archived=True,
        )

        # 1. status=active (デフォルト): 有効のみ
        res_active = self.client.get(f"{url}?mode=all&user_id=&status=active")
        self.assertEqual(res_active.status_code, 200)
        self.assertContains(res_active, "初回訪問議事録")
        self.assertNotContains(res_active, "アーカイブされた活動")

        # 2. status=archived: アーカイブのみ
        res_archived = self.client.get(f"{url}?mode=all&user_id=&status=archived")
        self.assertEqual(res_archived.status_code, 200)
        self.assertNotContains(res_archived, "初回訪問議事録")
        self.assertContains(res_archived, "アーカイブされた活動")

        # 3. status=all: 両方表示
        res_all = self.client.get(f"{url}?mode=all&user_id=&status=all")
        self.assertEqual(res_all.status_code, 200)
        self.assertContains(res_all, "初回訪問議事録")
        self.assertContains(res_all, "アーカイブされた活動")

    def test_activity_list_multi_sort(self):
        """パーソン一覧準拠の多段ソート（?sort=key,-key）の検証。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_list")

        base_time = timezone.now()
        act_email_old = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.EMAIL,
            occurred_at=base_time - timezone.timedelta(days=2),
            user=self.user,
            memo="メール古い",
        )
        act_email_new = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.EMAIL,
            occurred_at=base_time - timezone.timedelta(days=1),
            user=self.user,
            memo="メール新しい",
        )
        act_phone = Activity.objects.create(
            deal=self.deal,
            activity_type=ActivityType.PHONE,
            occurred_at=base_time,
            user=self.user,
            memo="電話最新",
        )

        # 多段ソート: activity_type 昇順, occurred_at 降順
        # activity_type: email ("email") < phone ("phone") < visit ("visit")
        res = self.client.get(f"{url}?mode=all&user_id=&sort=activity_type,-occurred_at")
        self.assertEqual(res.status_code, 200)
        acts = list(res.context["activities"])
        # email 2件が先に来て、その中で新しいものが先
        self.assertEqual(acts[0], act_email_new)
        self.assertEqual(acts[1], act_email_old)

    def test_activity_detail_view_permissions(self):
        # 実施者
        self.client.login(username="act_owner", password="password")
        response = self.client.get(reverse("activities:activity_detail", kwargs={"pk": self.activity.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "初回訪問議事録")
        self.assertContains(response, "実施者")

        # 同席者
        self.client.login(username="act_attendee", password="password")
        response = self.client.get(reverse("activities:activity_detail", kwargs={"pk": self.activity.pk}))
        self.assertEqual(response.status_code, 200)

        # 部外者 -> 403
        self.client.login(username="act_outsider", password="password")
        response = self.client.get(reverse("activities:activity_detail", kwargs={"pk": self.activity.pk}))
        self.assertEqual(response.status_code, 403)

    def test_activity_detail_view_back_navigator_and_icon_buttons(self):
        """GET /activities/<pk>/ アクセス時に BackNavigator に自身が push され、
        編集・アーカイブがテキストボタンではなく app-icon-btn アイコンボタンとして表示されること。"""
        self.client.login(username="act_owner", password="password")
        detail_url = reverse("activities:activity_detail", kwargs={"pk": self.activity.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

        # BackNavigator のスタックに活動詳細の URL が push されていること
        back = response.context["back"]
        self.assertTrue(len(back.back_stack) >= 1)
        self.assertEqual(back.back_stack[-1]["url"], detail_url)

        # テキストボタン「編集」「アーカイブ」「関係者を管理・追加」「同席者を管理・追加」が存在しないこと
        content = response.content.decode("utf-8")
        self.assertNotIn('class="app-btn app-btn--success">編集<', content)
        self.assertNotIn('class="app-btn app-btn--warning">アーカイブ<', content)
        # タイトル行の検証（活動詳細テキストのみで種別・方向バッジが撤去されていること）
        self.assertIn('<h1 class="app-title"', content)
        self.assertIn('活動詳細</h1>', content)
        header_part = content.split('<section class="app-card"')[0]
        self.assertNotIn('app-status-badge', header_part)

        # 基本情報カード内にアイコンボタン（app-icon-btn, bi-pencil-fill, bi-trash-fill）が存在すること
        self.assertIn('class="app-icon-btn" title="活動を編集" aria-label="活動を編集"', content)
        self.assertIn('bi bi-pencil-fill', content)
        self.assertIn('class="app-icon-btn" title="活動を削除" aria-label="活動を削除"', content)
        self.assertIn('bi bi-trash-fill', content)

        # 相手方関係者・社内同席者カードに編集アイコン（app-icon-btn, bi-pencil-fill）が存在すること
        self.assertIn('title="関係者を管理・追加"', content)
        self.assertIn('title="同席者を管理・追加"', content)

        # アイコンリンクに back_stack が付与されていること
        edit_url = reverse("activities:activity_update", kwargs={"pk": self.activity.pk})
        self.assertIn(f'{edit_url}?back_stack=', content)
        archive_url = reverse("activities:activity_archive", kwargs={"pk": self.activity.pk})
        self.assertIn(f'{archive_url}?back_stack=', content)

        # 添付ファイルエリアの検証（D&Dゾーン、multiple属性）
        self.assertIn('id="attachment-dropzone"', content)
        self.assertIn('id="attachment-file-input"', content)
        self.assertIn('multiple', content)

        # コンテナ幅（1040px）およびグリッド（1fr 1fr）のレイアウト検証
        self.assertIn('class="activity-detail-container"', content)
        self.assertIn('max-width: 1040px;', content)
        self.assertIn('grid-template-columns: 1fr 1fr;', content)

    def test_activity_detail_attachment_memo_inline_ui(self):
        """活動詳細画面において添付ファイルメモが常時フォームではなくテキスト＋編集トリガーであることを検証。"""
        from attachments.models import Attachment
        from django.core.files.base import ContentFile

        att = Attachment.objects.create(
            activity=self.activity,
            file=ContentFile(b"dummy activity attachment", name="act_file.pdf"),
            original_filename="act_file.pdf",
            memo="活動添付メモテスト",
            uploaded_by=self.user,
        )

        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_detail", kwargs={"pk": self.activity.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # メモ列が常時フォームではなくテキスト表示＋編集トリガー（memo-edit-btn）になっていること
        self.assertIn('class="attachment-memo-cell"', html)
        self.assertIn('class="memo-view-mode"', html)
        self.assertIn("活動添付メモテスト", html)
        self.assertIn('class="app-icon-btn memo-edit-btn"', html)
        self.assertIn('class="memo-edit-mode"', html)

    def test_activity_create_view_auto_user_and_badge(self):
        """活動新規作成画面で実施者が固定バッジ表示され、POST時にuserとcreated_byが自動保存されること。"""
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # HTML表示の検証（ラベルおよび固定バッジ表示、userプルダウンが存在しないこと）
        self.assertContains(response, "実施者")
        self.assertContains(response, self.user.display_name)
        self.assertNotContains(response, '<select name="user"')

        post_data = {
            "activity_type": ActivityType.VISIT,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "deal": str(self.deal.id),
            "memo": "自動固定の訪問活動",
            "person_id": str(self.person.id),
        }
        res_post = self.client.post(url, data=post_data)
        self.assertEqual(res_post.status_code, 302)

        created = Activity.objects.get(memo="自動固定の訪問活動")
        self.assertEqual(created.user, self.user)
        self.assertEqual(created.created_by, self.user)

    def test_activity_persons_manage_crud_and_permissions(self):
        """activity_persons_manage でパーソンの検索、追加（デフォルト attendee）、更新、解除、および権限チェック。"""
        p_new = Person.objects.create()
        contact = Contact.objects.create(person=p_new, last_name="鈴木", first_name="一郎", full_name="鈴木 一郎")
        p_new.primary_contact = contact
        p_new.save(update_fields=["primary_contact"])

        # 権限なしユーザーは 403
        self.client.login(username="act_outsider", password="password")
        manage_url = reverse("activities:activity_persons_manage", kwargs={"pk": self.activity.pk})
        self.assertEqual(self.client.get(manage_url).status_code, 403)
        self.assertEqual(self.client.post(manage_url, data={}).status_code, 403)

        # 権限ありユーザーでアクセス（未検索時はガイダンス表示、候補一覧テーブル非表示）
        self.client.login(username="act_owner", password="password")
        resp = self.client.get(manage_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "社外関係者（パーソン）管理")
        self.assertContains(resp, "登録済みの関係者")
        self.assertContains(resp, "検索条件を入力して候補を検索してください")
        self.assertEqual(list(resp.context["candidate_persons"]), [])
        self.assertNotContains(resp, "鈴木 一郎")

        # 検索機能（検索実行時は結果テーブルと「追加」が表示されること）
        resp_q = self.client.get(manage_url + "?q=鈴木")
        self.assertEqual(resp_q.status_code, 200)
        self.assertContains(resp_q, "鈴木 一郎")
        self.assertContains(resp_q, ">追加</button>")
        self.assertIn(p_new, resp_q.context["candidate_persons"])

        # 追加（デフォルト attendee）
        add_url = reverse("activities:activity_add_person", kwargs={"pk": self.activity.pk})
        add_resp = self.client.post(add_url, data={
            "person": str(p_new.id),
            "role": "attendee",
            "memo": "",
            "next": manage_url,
        })
        self.assertEqual(add_resp.status_code, 302)
        rel = ActivityPerson.objects.get(activity=self.activity, person=p_new)
        self.assertEqual(rel.role, PersonRole.ATTENDEE)

        # 一括更新（役割・メモ変更）
        update_resp = self.client.post(manage_url, data={
            f"person_{rel.id}_exists": "1",
            f"person_{rel.id}_role": PersonRole.DECISION_MAKER,
            f"person_{rel.id}_memo": "決裁者として参加",
        })
        self.assertEqual(update_resp.status_code, 302)
        rel.refresh_from_db()
        self.assertEqual(rel.role, PersonRole.DECISION_MAKER)
        self.assertEqual(rel.memo, "決裁者として参加")

        # 削除ボタンのスタイル・アイコン検証（app-icon-btn, bi-trash-fill）
        res_manage = self.client.get(manage_url)
        self.assertContains(res_manage, 'class="app-icon-btn"')
        self.assertContains(res_manage, 'bi bi-trash-fill')

        # 解除（削除）
        del_url = reverse("activities:activity_delete_person", kwargs={"pk": self.activity.pk, "person_rel_id": rel.id})
        del_resp = self.client.post(del_url, data={"next": manage_url})
        self.assertEqual(del_resp.status_code, 302)
        self.assertFalse(ActivityPerson.objects.filter(id=rel.id).exists())

    def test_activity_users_manage_crud_and_permissions(self):
        """activity_users_manage で社内ユーザーの検索、追加（デフォルト support）、更新、解除、および権限チェック。"""
        colleague = User.objects.create_user(username="act_colleague", password="password")

        # 権限なしユーザーは 403
        self.client.login(username="act_outsider", password="password")
        manage_url = reverse("activities:activity_users_manage", kwargs={"pk": self.activity.pk})
        self.assertEqual(self.client.get(manage_url).status_code, 403)
        self.assertEqual(self.client.post(manage_url, data={}).status_code, 403)

        # 権限ありユーザーでアクセス（未検索時はガイダンス表示、候補一覧テーブル非表示）
        self.client.login(username="act_owner", password="password")
        resp = self.client.get(manage_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "社内同席者（ユーザー）管理")
        self.assertContains(resp, "登録済みの同席者")
        self.assertContains(resp, "検索条件を入力して候補を検索してください")
        self.assertEqual(list(resp.context["candidate_users"]), [])

        # 検索機能（検索実行時は結果テーブルと「追加」が表示されること）
        resp_q = self.client.get(manage_url + "?q=act_colleague")
        self.assertEqual(resp_q.status_code, 200)
        self.assertContains(resp_q, "act_colleague")
        self.assertContains(resp_q, ">追加</button>")

        # 実施者本人（act_owner）は候補から除外され、colleagueが含まれること
        candidate_ids = [u.id for u in resp_q.context["candidate_users"]]
        self.assertNotIn(self.user.id, candidate_ids)
        self.assertIn(colleague.id, candidate_ids)

        # 追加（デフォルト support）
        add_url = reverse("activities:activity_add_user", kwargs={"pk": self.activity.pk})
        add_resp = self.client.post(add_url, data={
            "user": str(colleague.id),
            "role": "support",
            "memo": "",
            "next": manage_url,
        })
        self.assertEqual(add_resp.status_code, 302)
        rel = ActivityUser.objects.get(activity=self.activity, user=colleague)
        self.assertEqual(rel.role, UserRole.SUPPORT)

        # 一括更新（役割・メモ変更）
        update_resp = self.client.post(manage_url, data={
            f"user_{rel.id}_exists": "1",
            f"user_{rel.id}_role": UserRole.APPROVER,
            f"user_{rel.id}_memo": "承認者として同席",
        })
        self.assertEqual(update_resp.status_code, 302)
        rel.refresh_from_db()
        self.assertEqual(rel.role, UserRole.APPROVER)
        self.assertEqual(rel.memo, "承認者として同席")

        # 削除ボタンのスタイル・アイコン検証（app-icon-btn, bi-trash-fill）
        res_manage = self.client.get(manage_url)
        self.assertContains(res_manage, 'class="app-icon-btn"')
        self.assertContains(res_manage, 'bi bi-trash-fill')

        # 解除（削除）
        del_url = reverse("activities:activity_delete_user", kwargs={"pk": self.activity.pk, "user_rel_id": rel.id})
        del_resp = self.client.post(del_url, data={"next": manage_url})
        self.assertEqual(del_resp.status_code, 302)
        self.assertFalse(ActivityUser.objects.filter(id=rel.id).exists())

    def test_activity_users_manage_cannot_add_executor(self):
        """activity_users_manage で実施者自身を同席者に追加しようとした場合、拒絶されること。"""
        self.client.login(username="act_owner", password="password")
        add_url = reverse("activities:activity_add_user", kwargs={"pk": self.activity.pk})
        resp = self.client.post(add_url, data={
            "user": str(self.user.id),
            "role": "support",
            "memo": "自分を追加",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ActivityUser.objects.filter(activity=self.activity, user=self.user).exists())

    def test_activity_create_view_and_post(self):
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_create")
        response = self.client.get(url + f"?person_id={self.person.id}&deal_id={self.deal.id}")
        self.assertEqual(response.status_code, 200)

        post_data = {
            "activity_type": ActivityType.PHONE,
            "direction": Direction.INCOMING,
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "deal": str(self.deal.id),
            "memo": "電話問い合わせ受付",
            "person_id": str(self.person.id),
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)

        new_act = Activity.objects.get(memo="電話問い合わせ受付")
        self.assertEqual(new_act.activity_type, ActivityType.PHONE)
        self.assertEqual(new_act.user, self.user)
        self.assertEqual(new_act.created_by, self.user)
        # ActivityPerson が自動生成されていること
        self.assertTrue(ActivityPerson.objects.filter(activity=new_act, person=self.person).exists())

    def test_activity_update_view(self):
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_update", kwargs={"pk": self.activity.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url, data={
            "activity_type": ActivityType.WEB_MEETING,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.localtime(self.activity.occurred_at).strftime("%Y-%m-%dT%H:%M"),
            "memo": "訪問からWeb会議に変更",
        })
        self.assertEqual(response.status_code, 302)
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.activity_type, ActivityType.WEB_MEETING)
        self.assertEqual(self.activity.memo, "訪問からWeb会議に変更")

    def test_activity_archive_view(self):
        self.client.login(username="act_owner", password="password")
        url = reverse("activities:activity_archive", kwargs={"pk": self.activity.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.activity.refresh_from_db()
        self.assertTrue(self.activity.is_archived)

    def test_campaign_unfollowed_list_view(self):
        self.client.login(username="act_owner", password="password")
        template = EmailTemplate.objects.create(name="Template", subject="Sub", body="Body", created_by=self.user)
        campaign = Campaign.objects.create(name="夏期プロモーション", template=template, created_by=self.user)
        link = TrackingLink.objects.create(campaign=campaign, person=self.person, original_url="https://example.com/promo", token="tok_unfollowed")

        contact = Contact.objects.create(person=self.person, last_name="山田", first_name="花子")
        self.person.primary_contact = contact
        self.person.save(update_fields=["primary_contact"])

        ClickLog.objects.create(
            tracking_link=link,
            http_method="GET",
            is_valid_click=True,
        )

        url = reverse("activities:campaign_unfollowed_list", kwargs={"campaign_id": campaign.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未フォローパーソン一覧")
        self.assertContains(response, str(self.person))
        self.assertContains(response, f"person_id={self.person.id}&campaign_id={campaign.id}")

    def test_activity_create_view_with_campaign_and_person_and_back_navigator(self):
        from back_navigator.back_navigator import BackNavigator
        from mailings.models import Campaign, EmailTemplate

        self.client.login(username="act_owner", password="password")
        template = EmailTemplate.objects.create(name="T", subject="S", body="B", created_by=self.user)
        campaign = Campaign.objects.create(name="テストCP", template=template, created_by=self.user)

        nav = BackNavigator(self.client.get("/").wsgi_request)
        back_stack = nav._calc_encode_stack([{"url": f"/mailings/campaigns/{campaign.pk}/report/clicked/", "title": "クリック受信者"}])

        url = f"{reverse('activities:activity_create')}?campaign={campaign.pk}&person={self.person.pk}&back_stack={back_stack}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # フォーム初期値およびコンテキストの検証
        form = response.context["form"]
        self.assertEqual(str(form.initial.get("campaign")), str(campaign.pk))
        self.assertEqual(str(response.context.get("person_id")), str(self.person.pk))

        # 戻るボタンの検証
        content = response.content.decode("utf-8")
        self.assertIn(f'href="/mailings/campaigns/{campaign.pk}/report/clicked/"', content)

        # POST 実行時のリダイレクトおよびActivityPerson紐付け検証
        post_data = {
            "activity_type": ActivityType.PHONE,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "user": str(self.user.id),
            "campaign": str(campaign.pk),
            "person_id": str(self.person.pk),
            "memo": "クリック受信者へのフォロー架電",
            "back_stack": back_stack,
        }
        post_resp = self.client.post(url, data=post_data)
        self.assertEqual(post_resp.status_code, 302)
        created_activity = Activity.objects.get(memo="クリック受信者へのフォロー架電")
        expected_url = reverse("activities:activity_persons_manage", kwargs={"pk": created_activity.pk}) + f"?wizard=1&back_stack={back_stack}"
        self.assertEqual(post_resp.url, expected_url)
        self.assertEqual(created_activity.campaign, campaign)
        self.assertTrue(ActivityPerson.objects.filter(activity=created_activity, person=self.person).exists())

class ActivityCreateInitialParamTests(TestCase):
    """ActivityCreateView の ?company= および ?person= パラメータ連携テスト"""

    def setUp(self):
        self.user = User.objects.create_user(username="test_act_user", password="password")
        add_perm = Permission.objects.get(codename="add_activity")
        change_perm = Permission.objects.get(codename="change_activity")
        self.user.user_permissions.add(add_perm, change_perm)
        self.client.login(username="test_act_user", password="password")

        self.company1 = Company.objects.create(organization="株式会社テスト商事")
        self.company2 = Company.objects.create(organization="別会社")

        self.person1 = Person.objects.create()
        self.contact1 = Contact.objects.create(
            person=self.person1,
            company=self.company1,
            last_name="山田",
            first_name="花子",
        )
        self.person1.primary_contact = self.contact1
        self.person1.save(update_fields=["primary_contact"])

        self.deal1 = Deal.objects.create(
            name="商事向け案件",
            company=self.company1,
            primary_person=self.person1,
            owner=self.user,
        )
        self.deal2 = Deal.objects.create(
            name="別会社向け案件",
            company=self.company2,
            owner=self.user,
        )

    def test_get_activity_create_with_company_param_filters_deals(self):
        url = f"{reverse('activities:activity_create')}?company={self.company1.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["selected_company"], self.company1)
        self.assertEqual(response.context["company_id"], str(self.company1.id))

        deal_queryset = response.context["form"].fields["deal"].queryset
        self.assertIn(self.deal1, deal_queryset)
        self.assertNotIn(self.deal2, deal_queryset)

        content = response.content.decode("utf-8")
        self.assertIn(self.company1.organization, content)

    def test_get_activity_create_with_person_param_filters_deals_and_sets_context(self):
        url = f"{reverse('activities:activity_create')}?person={self.person1.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["selected_person"], self.person1)
        self.assertEqual(response.context["person_id"], str(self.person1.id))
        self.assertEqual(response.context["selected_company"], self.company1)

        # 所属会社の案件に絞り込まれていること
        deal_queryset = response.context["form"].fields["deal"].queryset
        self.assertIn(self.deal1, deal_queryset)
        self.assertNotIn(self.deal2, deal_queryset)

    def test_post_activity_create_with_person_param_creates_activity_person(self):
        url = f"{reverse('activities:activity_create')}?person={self.person1.id}"
        post_data = {
            "activity_type": ActivityType.PHONE,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "deal": str(self.deal1.id),
            "memo": "パーソン起点での電話活動",
            "person_id": str(self.person1.id),
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)

        activity = Activity.objects.get(memo="パーソン起点での電話活動")
        self.assertEqual(activity.deal, self.deal1)
        self.assertTrue(ActivityPerson.objects.filter(activity=activity, person=self.person1).exists())


class ActivityWizardTests(TestCase):
    """活動新規作成ウィザード（基本情報 ➔ 参加者設定フロー）のテスト"""

    def setUp(self):
        self.user = User.objects.create_user(username="wizard_act_user", password="password")
        add_perm = Permission.objects.get(codename="add_activity")
        change_perm = Permission.objects.get(codename="change_activity")
        view_perm = Permission.objects.get(codename="view_activity")
        att_perm = Permission.objects.get(codename="add_attachment")
        self.user.user_permissions.add(add_perm, change_perm, view_perm, att_perm)
        self.company = Company.objects.create(organization="活動ウィザード社")
        self.person = Person.objects.create()
        Contact.objects.create(
            person=self.person,
            company=self.company,
            first_name="次郎",
            last_name="佐藤",
        )
        self.client.login(username="wizard_act_user", password="password")

    def test_activity_wizard_4step_flow_and_titles(self):
        """活動新規作成ウィザードの 4ステップ（Step 1 ➔ Step 2 ➔ Step 3 ➔ Step 4 ➔ 詳細）遷移および各画面のタイトル表記・ボタン・BackNavigator検証。"""
        # Step 1: 基本情報入力画面
        nav = BackNavigator(self.client.get("/").wsgi_request)
        back_stack = nav._calc_encode_stack([{"url": reverse("activities:activity_list"), "title": "活動一覧"}])
        res1 = self.client.get(f"{reverse('activities:activity_create')}?back_stack={back_stack}")
        self.assertEqual(res1.status_code, 200)
        content1 = res1.content.decode("utf-8")
        self.assertIn("活動記録作成 (1/4) 基本情報", content1)
        self.assertContains(res1, "次へ")
        self.assertNotContains(res1, "スキップ")
        # ステップインジケーター検証: (現在)が無く、シンプル化された表記であること
        self.assertNotContains(res1, "(現在)")
        self.assertContains(res1, "1. 基本情報")
        # 日時クイック入力ボタン（今日・昨日）およびOKボタン
        self.assertContains(res1, "今日")
        self.assertContains(res1, "昨日")
        self.assertContains(res1, "OK")

        # Step 1 POST -> Step 2 へリダイレクト
        post_data = {
            "activity_type": ActivityType.PHONE,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "memo": "ウィザード4ステップ活動",
            "back_stack": back_stack,
        }
        res_post = self.client.post(f"{reverse('activities:activity_create')}?back_stack={back_stack}", data=post_data)
        self.assertEqual(res_post.status_code, 302)
        activity = Activity.objects.get(memo="ウィザード4ステップ活動")
        self.assertTrue(res_post.url.startswith(reverse("activities:activity_persons_manage", kwargs={"pk": activity.pk})))
        self.assertIn("wizard=1", res_post.url)

        # Step 2: 相手方関係者設定画面
        step2_url = res_post.url
        res2 = self.client.get(step2_url)
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.context.get("is_wizard"))
        content2 = res2.content.decode("utf-8")
        self.assertIn("活動参加者設定 (2/4) 相手方関係者", content2)
        self.assertContains(res2, "次へ")
        self.assertNotContains(res2, "スキップ")
        self.assertNotContains(res2, "(現在)")
        self.assertContains(res2, "2. 相手方関係者")
        # BackNavigator: 直前の Step 1 への戻るリンクが存在すること
        self.assertTrue(res2.context["back"].back_exist)

        # Step 3: 社内同席者設定画面
        step3_base = reverse("activities:activity_users_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
        step3_url = res2.context["back"].append_url(step3_base)
        res3 = self.client.get(step3_url)
        self.assertEqual(res3.status_code, 200)
        self.assertTrue(res3.context.get("is_wizard"))
        content3 = res3.content.decode("utf-8")
        self.assertIn("活動参加者設定 (3/4) 社内同席者", content3)
        self.assertContains(res3, "次へ")
        self.assertNotContains(res3, "スキップ")
        self.assertNotContains(res3, "(現在)")
        self.assertContains(res3, "3. 社内同席者")
        # BackNavigator: 直前の Step 2 への戻るリンクが存在すること
        self.assertTrue(res3.context["back"].back_exist)

        # Step 4: 添付ファイル設定画面
        step4_base = reverse("activities:activity_attachments_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
        step4_url = res3.context["back"].append_url(step4_base)
        res4 = self.client.get(step4_url)
        self.assertEqual(res4.status_code, 200)
        self.assertTrue(res4.context.get("is_wizard"))
        content4 = res4.content.decode("utf-8")
        self.assertIn("活動ファイル添付 (4/4) 添付ファイル", content4)
        self.assertContains(res4, "保存")
        self.assertNotContains(res4, "スキップ")
        self.assertNotContains(res4, "(現在)")
        self.assertContains(res4, "4. 添付ファイル")
        # BackNavigator: 直前の Step 3 への戻るリンクが存在すること
        self.assertTrue(res4.context["back"].back_exist)

        # 詳細画面へ遷移
        detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)

    def test_activity_create_auto_copies_deal_members(self):
        """案件から活動を作成した際、案件の primary_person、deal_persons、owner、deal_users が自動的に初期コピーされること。"""
        deal_owner = User.objects.create_user(username="deal_owner_user", password="password")
        deal_member = User.objects.create_user(username="deal_member_user", password="password")

        person1 = self.person
        person2 = Person.objects.create()
        Contact.objects.create(
            person=person2,
            company=self.company,
            first_name="花子",
            last_name="山田",
        )

        deal = Deal.objects.create(
            name="メンバー引き継ぎ元案件",
            company=self.company,
            owner=deal_owner,
            primary_person=person1,
            created_by=self.user,
        )
        DealPerson.objects.create(
            deal=deal,
            person=person2,
            role=PersonRole.ATTENDEE,
            memo="案件側同席者メモ",
        )
        DealUser.objects.create(
            deal=deal,
            user=deal_member,
            role=UserRole.SUPPORT,
            memo="案件側サポートメモ",
        )

        post_data = {
            "activity_type": ActivityType.WEB_MEETING,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "deal": str(deal.id),
            "memo": "案件引き継ぎ活動",
        }
        res = self.client.post(reverse("activities:activity_create"), data=post_data)
        self.assertEqual(res.status_code, 302)

        activity = Activity.objects.get(memo="案件引き継ぎ活動")

        # 相手方パーソンのコピー検証
        ap_person_ids = set(activity.activity_persons.values_list("person_id", flat=True))
        self.assertIn(person1.id, ap_person_ids)
        self.assertIn(person2.id, ap_person_ids)
        self.assertEqual(activity.activity_persons.count(), 2)

        ap1 = activity.activity_persons.get(person=person1)
        self.assertEqual(ap1.role, PersonRole.CONTACT_WINDOW)

        ap2 = activity.activity_persons.get(person=person2)
        self.assertEqual(ap2.role, PersonRole.ATTENDEE)
        self.assertEqual(ap2.memo, "案件側同席者メモ")

        # 社内同席者のコピー検証
        au_user_ids = set(activity.activity_users.values_list("user_id", flat=True))
        self.assertIn(deal_owner.id, au_user_ids)
        self.assertIn(deal_member.id, au_user_ids)
        self.assertEqual(activity.activity_users.count(), 2)

        au_owner = activity.activity_users.get(user=deal_owner)
        self.assertEqual(au_owner.role, UserRole.PRIMARY)

        au_member = activity.activity_users.get(user=deal_member)
        self.assertEqual(au_member.role, UserRole.SUPPORT)
        self.assertEqual(au_member.memo, "案件側サポートメモ")

    def test_activity_wizard_step4_upload_attachment(self):
        """Step 4（添付ファイル管理画面）において活動のファイルが正常にアップロード・紐付け保存され、詳細画面等へ遷移できること。"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from attachments.models import Attachment

        activity = Activity.objects.create(
            activity_type=ActivityType.PHONE,
            direction=Direction.OUTGOING,
            occurred_at=timezone.now(),
            user=self.user,
            memo="Step4添付検証活動",
        )
        step4_url = reverse("activities:activity_attachments_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
        detail_url = reverse("activities:activity_detail", kwargs={"pk": activity.pk})

        test_file = SimpleUploadedFile("minute_doc.pdf", b"meeting minute content", content_type="application/pdf")
        post_data = {
            "activity_id": str(activity.id),
            "next": detail_url,
            "memo": "議事録",
            "files": test_file,
        }
        res = self.client.post(step4_url, data=post_data)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, detail_url)

        att = Attachment.objects.filter(activity=activity).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.original_filename, "minute_doc.pdf")
        self.assertEqual(att.memo, "議事録")
        self.assertEqual(att.uploaded_by, self.user)

        # ファイル未選択で保存した場合はエラーにならず詳細画面へリダイレクトされること
        empty_res = self.client.post(step4_url, data={"activity_id": str(activity.id), "next": detail_url})
        self.assertEqual(empty_res.status_code, 302)
        self.assertEqual(empty_res.url, detail_url)

    def test_activity_wizard_from_deal_detail_back_navigator(self):
        """案件詳細起点の活動ウィザード完了時、活動詳細の戻り先（back.back_url）が当該案件詳細画面を正しく指していること。"""
        deal = Deal.objects.create(
            name="活動起点の案件",
            company=self.company,
            owner=self.user,
        )
        deal_list_url = reverse("deals:deal_list")
        deal_detail_url = reverse("deals:deal_detail", kwargs={"pk": deal.pk})

        nav = BackNavigator(self.client.get("/").wsgi_request)
        initial_stack = [
            {"url": deal_list_url, "title": "案件一覧", "view_name": "deals:deal_list", "view_kwargs": {}},
        ]
        back_stack = nav._calc_encode_stack(initial_stack)

        # 1. 案件詳細画面へアクセス（案件一覧からの back_stack 付き）
        deal_detail_res = self.client.get(f"{deal_detail_url}?back_stack={back_stack}")
        self.assertEqual(deal_detail_res.status_code, 200)
        detail_html = deal_detail_res.content.decode("utf-8")

        # 「活動を記録」リンクの href を抽出
        import re
        match_create = re.search(r'href="([^"]+)"[^>]*>活動を記録</a>', detail_html)
        self.assertIsNotNone(match_create, "「活動を記録」リンクが見つかりません")
        step1_url = match_create.group(1).replace("&amp;", "&")

        # 2. Step 1: 活動作成画面
        res1 = self.client.get(step1_url)
        self.assertEqual(res1.status_code, 200)

        # Step 1 POST
        post_data = {
            "activity_type": ActivityType.PHONE,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "memo": "案件詳細起点ウィザード活動",
            "deal": str(deal.pk),
        }
        # form の hidden back_stack を引き継ぐ
        raw_back = res1.context["back"]._calc_encode_stack()
        post_data["back_stack"] = raw_back
        res1_post = self.client.post(step1_url, data=post_data)
        self.assertEqual(res1_post.status_code, 302)
        activity = Activity.objects.get(memo="案件詳細起点ウィザード活動")

        # 3. Step 2: 相手方関係者設定
        step2_url = res1_post.url
        self.assertIn(f"activities/{activity.pk}/persons/manage/", step2_url)
        self.assertIn("wizard=1", step2_url)
        res2 = self.client.get(step2_url)
        self.assertEqual(res2.status_code, 200)

        # 4. Step 3: 社内同席者設定
        step3_base = reverse("activities:activity_users_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
        step3_url = res2.context["back"].append_url(step3_base)
        res3 = self.client.get(step3_url)
        self.assertEqual(res3.status_code, 200)

        # 5. Step 4: 添付ファイル設定
        step4_base = reverse("activities:activity_attachments_manage", kwargs={"pk": activity.pk}) + "?wizard=1"
        step4_url = res3.context["back"].append_url(step4_base)
        res4 = self.client.get(step4_url)
        self.assertEqual(res4.status_code, 200)

        # Step 4 の完了ボタン（保存 / wizard-finish-btn）の href を抽出
        content4 = res4.content.decode("utf-8")
        match = re.search(r'id="wizard-finish-btn"[^>]*href="([^"]+)"', content4)
        if not match:
            match = re.search(r'href="([^"]+)"[^>]*id="wizard-finish-btn"', content4)
        self.assertIsNotNone(match, "wizard-finish-btn の href が見つかりません")
        finish_url = match.group(1).replace("&amp;", "&")

        # 6. 活動詳細画面へ遷移
        res_detail = self.client.get(finish_url)
        self.assertEqual(res_detail.status_code, 200)

        detail_back = res_detail.context["back"]
        self.assertTrue(detail_back.back_exist, "戻り先（案件詳細）が存在すること")
        # 直前の戻り先が案件詳細であること
        self.assertTrue(detail_back.back_url.startswith(deal_detail_url), f"back_url: {detail_back.back_url} が {deal_detail_url} で始まること")
        # 最初の戻り先が案件一覧であること
        self.assertEqual(detail_back.back_all_url, deal_list_url)




class ActivityPersonMergeTests(TestCase):
    """Personマージ時のActivity / ActivityPerson付け替え・重複解消および表示安全化テスト"""

    def setUp(self):
        self.user = User.objects.create_user(username="merge_act_user", password="password")
        add_perm = Permission.objects.get(codename="add_activity")
        change_perm = Permission.objects.get(codename="change_activity")
        view_perm = Permission.objects.get(codename="view_activity")
        self.user.user_permissions.add(add_perm, change_perm, view_perm)
        self.client.login(username="merge_act_user", password="password")

        self.company = Company.objects.create(organization="活動テスト商事")

        # マージ元（source）
        self.source_person = Person.objects.create()
        self.source_contact = Contact.objects.create(
            person=self.source_person,
            company=self.company,
            last_name="佐藤",
            first_name="次郎",
            full_name="佐藤 次郎",
            status=Contact.Status.PRIMARY,
        )
        self.source_person.primary_contact = self.source_contact
        self.source_person.save(update_fields=["primary_contact"])

        # マージ先（target / surviving）
        self.target_person = Person.objects.create()
        self.target_contact = Contact.objects.create(
            person=self.target_person,
            company=self.company,
            last_name="佐藤",
            first_name="次郎（本）",
            full_name="佐藤 次郎（本）",
            status=Contact.Status.PRIMARY,
        )
        self.target_person.primary_contact = self.target_contact
        self.target_person.save(update_fields=["primary_contact"])

    def test_activity_person_merge_transfer_and_role_merge(self):
        """マージ時に ActivityPerson がマージ先へ移行し、重複解消・role/memoマージされること。"""
        # act1: source (DECISION_MAKER, "重要決裁者") と target (ATTENDEE, "") が存在
        act1 = Activity.objects.create(
            activity_type=ActivityType.VISIT,
            direction=Direction.OUTGOING,
            occurred_at=timezone.now(),
            user=self.user,
            memo="活動1",
        )
        ActivityPerson.objects.create(
            activity=act1,
            person=self.source_person,
            role=PersonRole.DECISION_MAKER,
            memo="重要決裁者",
        )
        ActivityPerson.objects.create(
            activity=act1,
            person=self.target_person,
            role=PersonRole.ATTENDEE,
            memo="",
        )

        # act2: source のみ存在（通常移行ケース）
        act2 = Activity.objects.create(
            activity_type=ActivityType.PHONE,
            direction=Direction.OUTGOING,
            occurred_at=timezone.now(),
            user=self.user,
            memo="活動2",
        )
        ActivityPerson.objects.create(
            activity=act2,
            person=self.source_person,
            role=PersonRole.CONTACT_WINDOW,
            memo="窓口担当",
        )

        # マージ実行
        from config.constants import DuplicateMergeReason
        self.source_person.transfer_contacts_to(
            self.target_person, [DuplicateMergeReason.SAME_CARD.value]
        )
        self.source_person.mark_as_merged(self.target_person)

        # 検証1: act1 で source が削除され、target のみ 1 件存在すること
        self.assertFalse(act1.activity_persons.filter(person=self.source_person).exists())
        self.assertEqual(act1.activity_persons.filter(person=self.target_person).count(), 1)

        # 検証2: act1 の target の ActivityPerson に、より優先度の高い role と memo が引き継がれていること
        target_ap1 = act1.activity_persons.filter(person=self.target_person).first()
        self.assertEqual(target_ap1.role, PersonRole.DECISION_MAKER)
        self.assertEqual(target_ap1.memo, "重要決裁者")

        # 検証3: act2 の ActivityPerson が target に移行されていること
        self.assertFalse(act2.activity_persons.filter(person=self.source_person).exists())
        self.assertEqual(act2.activity_persons.filter(person=self.target_person).count(), 1)
        target_ap2 = act2.activity_persons.filter(person=self.target_person).first()
        self.assertEqual(target_ap2.role, PersonRole.CONTACT_WINDOW)
        self.assertEqual(target_ap2.memo, "窓口担当")

        # 検証4: 活動詳細画面で「Person <UUID>」が生露出せず、「マージ済み」文字列も存在せず、相手方関係者が正常に1件表示されること
        resp1 = self.client.get(reverse("activities:activity_detail", kwargs={"pk": act1.pk}))
        self.assertEqual(resp1.status_code, 200)
        content1 = resp1.content.decode("utf-8")
        self.assertNotIn(f"Person {self.source_person.id}", content1)
        self.assertNotIn(f"Person {self.target_person.id}", content1)
        self.assertIn("佐藤 次郎（本）", content1)
        self.assertNotContains(resp1, "マージ済み")
        self.assertEqual(len(resp1.context["activity_persons"]), 1)

        resp2 = self.client.get(reverse("activities:activity_detail", kwargs={"pk": act2.pk}))
        self.assertEqual(resp2.status_code, 200)
        content2 = resp2.content.decode("utf-8")
        self.assertNotIn(f"Person {self.source_person.id}", content2)
        self.assertNotIn(f"Person {self.target_person.id}", content2)
        self.assertIn("佐藤 次郎（本）", content2)
        self.assertNotContains(resp2, "マージ済み")
        self.assertEqual(len(resp2.context["activity_persons"]), 1)

    def test_activity_detail_completely_excludes_merged_person_records(self):
        """中間テーブルに merged Person が残存している場合でも、ActivityDetailView がクエリセットから除外し、「マージ済み」文字列が表示されないこと。"""
        act = Activity.objects.create(
            activity_type=ActivityType.PHONE,
            direction=Direction.OUTGOING,
            occurred_at=timezone.now(),
            user=self.user,
            memo="不整合残存活動",
        )
        ActivityPerson.objects.create(
            activity=act,
            person=self.target_person,
            role=PersonRole.CONTACT_WINDOW,
        )
        merged_person = Person.objects.create(status=Person.Status.MERGED, merged_into=self.target_person)
        ActivityPerson.objects.create(
            activity=act,
            person=merged_person,
            role=PersonRole.ATTENDEE,
        )

        resp = self.client.get(reverse("activities:activity_detail", kwargs={"pk": act.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "マージ済み")
        self.assertEqual(len(resp.context["activity_persons"]), 1)
        self.assertEqual(resp.context["activity_persons"][0].person, self.target_person)


class ActivityDetailAttachmentModalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="att_act_user", password="password", first_name="添付", last_name="活動")
        perm_change_activity = Permission.objects.get(codename="change_activity")
        perm_add_att = Permission.objects.get(codename="add_attachment")
        self.user.user_permissions.add(perm_change_activity, perm_add_att)
        self.activity = Activity.objects.create(
            activity_type=ActivityType.PHONE,
            direction=Direction.OUTGOING,
            occurred_at=timezone.now(),
            user=self.user,
            created_by=self.user,
            memo="添付テスト活動",
        )
        self.client.login(username="att_act_user", password="password")

    def test_activity_detail_attachment_modal_structure(self):
        """活動詳細の添付ファイルカード内に常時表示フォームが存在せず、モーダルトリガーとモーダル内フォームが存在することを検証。"""
        url = reverse("activities:activity_detail", kwargs={"pk": self.activity.pk})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # 1. カード内に常時表示のファイル入力フォームが存在しないこと
        card_match = re.search(r'<section class="app-card"[^>]*>(?:(?!<section)[\s\S])*?<h2[^>]*>添付ファイル</h2>[\s\S]*?</section>', html)
        self.assertIsNotNone(card_match)
        card_html = card_match.group(0)
        self.assertNotIn('id="attachment-upload-form"', card_html)
        self.assertNotIn('id="attachment-dropzone"', card_html)
        self.assertIn("添付ファイルはありません。", card_html)

        # 2. モーダルトリガーボタンが存在すること
        self.assertIn('data-action="open-modal"', card_html)
        self.assertIn('data-target="attachmentUploadModal"', card_html)
        self.assertIn("ファイルをアップロード", card_html)

        # 3. モーダル（#attachmentUploadModal）内にフォームと各要素が存在すること
        modal_match = re.search(r'<div class="app-modal" id="attachmentUploadModal"[\s\S]*?</form>\s*</div>\s*</div>', html)
        self.assertIsNotNone(modal_match)
        modal_html = modal_match.group(0)
        upload_url = reverse("attachments:attachment_upload")
        self.assertIn(f'action="{upload_url}"', modal_html)
        self.assertIn('enctype="multipart/form-data"', modal_html)
        self.assertIn('name="activity_id"', modal_html)
        self.assertIn(f'value="{self.activity.id}"', modal_html)
        self.assertIn('name="files"', modal_html)
        self.assertIn("multiple", modal_html)
        self.assertIn('name="memo"', modal_html)
        self.assertIn('id="attachment-dropzone"', modal_html)
        self.assertIn('id="attachment-upload-btn"', modal_html)
        self.assertIn('data-action="close-modal"', modal_html)




