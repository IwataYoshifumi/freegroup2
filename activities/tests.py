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
from contacts.models import Contact
from deals.models import Deal, PersonRole, UserRole
from mailings.models import Campaign, ClickLog, EmailTemplate, TrackingLink
from persons.models import Person

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
        self.user.user_permissions.add(add_perm, change_perm)
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
        self.assertContains(response, "訪問")  # get_activity_type_display
        self.assertContains(response, "初回訪問議事録")

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

        # テキストボタン「編集」「アーカイブ」が存在しないこと
        content = response.content.decode("utf-8")
        self.assertNotIn('class="app-btn app-btn--success">編集<', content)
        self.assertNotIn('class="app-btn app-btn--warning">アーカイブ<', content)

        # アイコンボタン（app-icon-btn, bi-pencil-fill, bi-archive-fill）が存在すること
        self.assertIn('class="app-icon-btn" title="編集" aria-label="編集"', content)
        self.assertIn('bi bi-pencil-fill', content)
        self.assertIn('class="app-icon-btn" title="アーカイブ" aria-label="アーカイブ"', content)
        self.assertIn('bi bi-archive-fill', content)

        # アイコンリンクに back_stack が付与されていること
        edit_url = reverse("activities:activity_update", kwargs={"pk": self.activity.pk})
        self.assertIn(f'{edit_url}?back_stack=', content)
        archive_url = reverse("activities:activity_archive", kwargs={"pk": self.activity.pk})
        self.assertIn(f'{archive_url}?back_stack=', content)

        # コンテナ幅（1040px）およびグリッド（1fr 1fr）のレイアウト検証
        self.assertIn('class="activity-detail-container"', content)
        self.assertIn('max-width: 1040px;', content)
        self.assertIn('grid-template-columns: 1fr 1fr;', content)

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

        # POST時に user / created_by がログインユーザーで自動設定されること（代理指定があってもログインユーザーで固定）
        post_data = {
            "activity_type": ActivityType.VISIT,
            "direction": Direction.OUTGOING,
            "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "user": str(self.attendee.id),
            "memo": "自動固定の訪問活動",
        }
        post_resp = self.client.post(url, data=post_data)
        self.assertEqual(post_resp.status_code, 302)

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

        # 権限ありユーザーでアクセス
        self.client.login(username="act_owner", password="password")
        resp = self.client.get(manage_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "社外関係者（パーソン）管理")
        self.assertContains(resp, "登録済みの関係者")

        # 検索機能
        resp_q = self.client.get(manage_url + "?q=鈴木")
        self.assertEqual(resp_q.status_code, 200)
        self.assertContains(resp_q, "鈴木 一郎")

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

        # 権限ありユーザーでアクセス
        self.client.login(username="act_owner", password="password")
        resp = self.client.get(manage_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "社内同席者（ユーザー）管理")
        self.assertContains(resp, "登録済みの同席者")

        # 実施者本人（act_owner）は候補から除外されていること
        candidate_ids = [u.id for u in resp.context["candidate_users"]]
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
        self.assertEqual(post_resp.url, f"/mailings/campaigns/{campaign.pk}/report/clicked/")

        created_activity = Activity.objects.get(memo="クリック受信者へのフォロー架電")
        self.assertEqual(created_activity.campaign, campaign)
        self.assertTrue(ActivityPerson.objects.filter(activity=created_activity, person=self.person).exists())



