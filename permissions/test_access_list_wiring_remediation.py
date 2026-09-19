"""AccessList 実運用パスの完全配線・救済フォールバック撤去・Admin迂回防止の是正（指摘 A〜E）検証テスト。

仕様書 v1.6 §4.5, §4.6, §5.3, §8.3, §9.1, §10.1, §10.2, §10.3 に基づく包括的テストスイート。
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from activities.models import Activity
from activities.permissions import can_edit_activity
from contacts.models import Contact
from contacts.services.permissions import can_edit_contact
from deals.admin import DealAdmin
from deals.models import Deal, DealList, DealUser, Stage
from deals.permissions import can_edit_deal
from duplicates.models import DuplicateCandidate, PersonMergeLog
from duplicates.services.merge_executor import _check_merge_permission
from permissions.models import AccessList, AccessListUserRole
from persons.admin import PersonAdmin
from persons.models import Person, PersonList

User = get_user_model()


class RemediationBaseTestCase(TestCase):
    """是正テスト用共通フィクスチャ。"""

    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()

        # 部署・ユーザーのセットアップ
        self.admin_user = User.objects.create_superuser(
            username="rem_admin", email="admin@example.com", password="password"
        )
        self.staff_user = User.objects.create_user(
            username="rem_staff", email="staff@example.com", password="password", is_staff=True
        )
        self.member_user = User.objects.create_user(
            username="rem_member", email="member@example.com", password="password"
        )
        self.outsider_user = User.objects.create_user(
            username="rem_outsider", email="outsider@example.com", password="password"
        )

        # AccessList 1 (公開)
        self.acl_public = AccessList.objects.create(
            name="Public ACL",
            description="All members can view",
            created_by=self.admin_user,
        )
        AccessListUserRole.objects.create(
            access_list=self.acl_public,
            user=self.member_user,
            role=AccessListUserRole.Role.EDITOR,
        )

        # AccessList 2 (機密)
        self.acl_secret = AccessList.objects.create(
            name="Secret ACL",
            description="Only admin",
            created_by=self.admin_user,
        )

        # DealList / PersonList
        self.deal_list_public = DealList.objects.create(
            name="Public Deal List",
            access_list=self.acl_public,
            created_by=self.admin_user,
        )
        self.deal_list_secret = DealList.objects.create(
            name="Secret Deal List",
            access_list=self.acl_secret,
            created_by=self.admin_user,
        )

        self.person_list_public = PersonList.objects.create(
            name="Public Person List",
            access_list=self.acl_public,
            created_by=self.admin_user,
        )
        self.person_list_secret = PersonList.objects.create(
            name="Secret Person List",
            access_list=self.acl_secret,
            created_by=self.admin_user,
        )

        # Stage
        self.stage = Stage.INITIAL_MEETING


class RemediationATests(RemediationBaseTestCase):
    """指摘 A: 新規作成フォームへの user 伝達とモデル層救済ロジックの完全撤去。"""

    def test_deal_create_view_requires_deal_list_and_filters_by_user(self):
        """DealCreateView: user が伝達され、editable_deal_list_ids のリストのみ選択可。"""
        # add_deal 権限を付与
        self.member_user.user_permissions.add(
            Permission.objects.get(codename="add_deal", content_type__app_label="deals")
        )
        self.client.force_login(self.member_user)

        url = reverse("deals:deal_create")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # フォームの deal_list 選択肢に public は含まれ、secret は含まれない
        form = resp.context["form"]
        self.assertIn(self.deal_list_public, form.fields["deal_list"].queryset)
        self.assertNotIn(self.deal_list_secret, form.fields["deal_list"].queryset)

        # deal_list 未指定時はエラー
        post_data = {
            "name": "新規案件テスト",
            "stage": self.stage,
            "deal_list": "",
        }
        post_resp = self.client.post(url, data=post_data)
        self.assertEqual(post_resp.status_code, 200)
        self.assertFormError(post_resp.context["form"], "deal_list", "このフィールドは必須です。")

    def test_contact_create_view_requires_person_list(self):
        """ContactCreateView: user が渡されたとき person_list は必須。"""
        from contacts.tests import _empty_sns_management_form

        # add_contact, view_contact を付与
        for codename in ("add_contact", "view_contact"):
            self.member_user.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="contacts")
            )
        self.client.force_login(self.member_user)

        url = reverse("contacts:contact_create")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        form = resp.context["form"]
        self.assertTrue(form.fields["person_list"].required)

        # person_list 未指定で POST するとバリデーションエラー
        data = {f: "" for f in Contact.UPDATABLE_FIELDS}
        data["full_name"] = "必須検証太郎"
        data["salutation_name"] = "必須検証 様"
        data["person_list"] = ""
        data.update(_empty_sns_management_form())

        post_resp = self.client.post(url, data=data)
        self.assertEqual(post_resp.status_code, 200)
        self.assertFormError(post_resp.context["form"], "person_list", "このフィールドは必須です。")

    def test_deal_model_layer_does_not_auto_rescue_missing_list(self):
        """モデル層: _skip_test_default_list=True 時に Deal.save() で自動救済されず IntegrityError。"""
        deal = Deal(name="No List Deal", stage=self.stage, deal_list=None)
        deal._skip_test_default_list = True
        with self.assertRaises(IntegrityError):
            deal.save()

    def test_person_model_layer_does_not_auto_rescue_missing_list(self):
        """モデル層: _skip_test_default_list=True 時に Person.save() で自動救済されず IntegrityError。"""
        person = Person(person_list=None)
        person._skip_test_default_list = True
        with self.assertRaises(IntegrityError):
            person.save()


class RemediationBTests(RemediationBaseTestCase):
    """指摘 B: 認可判定の AccessListService への完全委譲。"""

    def test_can_edit_deal_delegation_and_deal_user_guard(self):
        """can_edit_deal: AccessListService に委譲、DealUser(can_edit=False) が最優先。"""
        self.member_user.user_permissions.add(
            Permission.objects.get(codename="change_deal", content_type__app_label="deals")
        )
        self.outsider_user.user_permissions.add(
            Permission.objects.get(codename="change_deal", content_type__app_label="deals")
        )

        deal = Deal.objects.create(
            name="Public Deal",
            stage=self.stage,
            deal_list=self.deal_list_public,
        )

        # member は public list の EDITOR なので通常は True
        self.assertTrue(can_edit_deal(self.member_user, deal))

        # outsider は権限なしなので False
        self.assertFalse(can_edit_deal(self.outsider_user, deal))

        # DealUser(can_edit=False) が設定された場合、EDITOR であっても最優先ガードで False
        DealUser.objects.create(deal=deal, user=self.member_user, can_edit=False)
        self.assertFalse(can_edit_deal(self.member_user, deal))

    def test_can_edit_contact_delegation_and_owner_guard(self):
        """can_edit_contact: AccessListService に委譲、created_by/managed_by は編集可。"""
        person_secret = Person.objects.create(person_list=self.person_list_secret)
        contact = Contact.objects.create(
            person=person_secret,
            status=Contact.Status.PRIMARY,
            full_name="Secret Contact",
            created_by=self.member_user,
        )

        # member は secret list の EDITOR ではないが、created_by なので True
        self.assertTrue(can_edit_contact(self.member_user, contact))

        # outsider は権限がなく所有者でもないので False
        self.assertFalse(can_edit_contact(self.outsider_user, contact))

    def test_can_edit_activity_delegates_to_access_list_service_deal(self):
        """can_edit_activity: Deal 紐付き時に AccessListService.can_edit_deal に委譲。"""
        from django.utils import timezone

        deal_public = Deal.objects.create(
            name="Activity Public Deal",
            stage=self.stage,
            deal_list=self.deal_list_public,
        )
        deal_secret = Deal.objects.create(
            name="Activity Secret Deal",
            stage=self.stage,
            deal_list=self.deal_list_secret,
        )

        now = timezone.now()
        act_public = Activity.objects.create(
            title="Public Deal Activity",
            deal=deal_public,
            user=self.admin_user,
            occurred_at=now,
        )
        act_secret = Activity.objects.create(
            title="Secret Deal Activity",
            deal=deal_secret,
            user=self.admin_user,
            occurred_at=now,
        )

        # change_activity と change_deal 権限を付与
        for codename, app in (("change_activity", "activities"), ("change_deal", "deals")):
            perm = Permission.objects.get(codename=codename, content_type__app_label=app)
            self.member_user.user_permissions.add(perm)
            self.outsider_user.user_permissions.add(perm)

        # member は deal_public のリスト権限あり → True、deal_secret のリスト権限なし → False
        self.assertTrue(can_edit_activity(self.member_user, act_public))
        self.assertFalse(can_edit_activity(self.member_user, act_secret))


class RemediationCTests(RemediationBaseTestCase):
    """指摘 C: 詳細画面のオブジェクトレベル認可ガード。"""

    def setUp(self):
        super().setUp()
        for codename in ("view_person",):
            self.outsider_user.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="persons")
            )
            self.member_user.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="persons")
            )
        for codename in ("view_contact",):
            self.outsider_user.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="contacts")
            )
            self.member_user.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="contacts")
            )

        self.person_secret = Person.objects.create(person_list=self.person_list_secret)
        self.contact_secret = Contact.objects.create(
            person=self.person_secret,
            status=Contact.Status.PRIMARY,
            full_name="Secret Person Contact",
        )

    def test_person_detail_view_denies_unauthorized_user(self):
        """PersonDetailView: 閲覧不能な person_list の Person は 403。"""
        self.client.force_login(self.outsider_user)
        url = reverse("persons:person_detail", kwargs={"pk": self.person_secret.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_contact_detail_view_denies_unauthorized_user(self):
        """ContactDetailView: 閲覧不能な person_list に紐づく Contact は 403。"""
        self.client.force_login(self.outsider_user)
        url = reverse("contacts:contact_detail", kwargs={"pk": self.contact_secret.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)


class RemediationDTests(RemediationBaseTestCase):
    """指摘 D: 重複候補認可 & マージ権限 & Undo 退避先是正。"""

    def setUp(self):
        import uuid
        super().setUp()
        self.person_public_1 = Person.objects.create(person_list=self.person_list_public)
        self.person_public_2 = Person.objects.create(person_list=self.person_list_public)
        self.person_secret = Person.objects.create(person_list=self.person_list_secret)

        # 候補1: public & public
        self.candidate_visible = DuplicateCandidate.objects.create(
            person_a=self.person_public_1,
            person_b=self.person_public_2,
            score=85,
            rank=DuplicateCandidate.Rank.POSSIBLE_HIGH,
            group_id=uuid.uuid4(),
        )
        # 候補2: public & secret
        self.candidate_hidden = DuplicateCandidate.objects.create(
            person_a=self.person_public_1,
            person_b=self.person_secret,
            score=90,
            rank=DuplicateCandidate.Rank.POSSIBLE_HIGH,
            group_id=uuid.uuid4(),
        )

        for codename, app in (
            ("view_duplicatecandidate", "duplicates"),
            ("change_duplicatecandidate", "duplicates"),
            ("merge_person", "persons"),
        ):
            perm = Permission.objects.get(codename=codename, content_type__app_label=app)
            self.member_user.user_permissions.add(perm)

    def test_candidate_list_filters_both_persons_accessible(self):
        """重複候補一覧: person_a と person_b の双方が閲覧可能な候補グループのみ表示。"""
        self.client.force_login(self.member_user)
        url = reverse("duplicates:duplicate_group_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        groups = resp.context["groups"]
        visible_group_ids = [g["group_id"] for g in groups]
        self.assertIn(self.candidate_visible.group_id, visible_group_ids)
        self.assertNotIn(self.candidate_hidden.group_id, visible_group_ids)

    def test_merge_executor_permission_denied_when_cannot_merge(self):
        """_check_merge_permission: AccessListService.can_merge_person 不可なら PermissionDenied。"""
        # member_user は person_secret への編集権限がないためマージ不可
        with self.assertRaises(PermissionDenied):
            _check_merge_permission(
                self.member_user,
                surviving_person=self.person_public_1,
                merged_person=self.person_secret,
            )

    def test_person_merge_log_saves_person_list_before_merge(self):
        """PersonMergeLog.create: surviving_person.person_list を退避先として記録。"""
        log = PersonMergeLog.create(
            surviving_person=self.person_public_1,
            merged_person=self.person_public_2,
            user=self.admin_user,
        )
        self.assertEqual(log.person_list_before_merge, self.person_list_public)


class RemediationETests(RemediationBaseTestCase):
    """指摘 E: Admin 迂回防止。"""

    def test_deal_admin_readonly_deal_list_when_no_change_deallist_perm(self):
        """DealAdmin: change_deallist 権限がないユーザーは deal_list が readonly。"""
        deal_admin = DealAdmin(Deal, AdminSite())
        request = self.factory.get("/admin/deals/deal/add/")
        request.user = self.staff_user

        readonly_fields = deal_admin.get_readonly_fields(request)
        self.assertIn("deal_list", readonly_fields)

        # 権限を付与した場合は readonly に入らない
        perm = Permission.objects.get(codename="change_deallist", content_type__app_label="deals")
        self.staff_user.user_permissions.add(perm)
        # キャッシュ対策
        self.staff_user = User.objects.get(pk=self.staff_user.pk)
        request.user = self.staff_user

        readonly_fields_with_perm = deal_admin.get_readonly_fields(request)
        self.assertNotIn("deal_list", readonly_fields_with_perm)

    def test_person_admin_readonly_person_list_when_no_change_personlist_perm(self):
        """PersonAdmin: change_personlist 権限がないユーザーは person_list が readonly。"""
        person_admin = PersonAdmin(Person, AdminSite())
        request = self.factory.get("/admin/persons/person/add/")
        request.user = self.staff_user

        readonly_fields = person_admin.get_readonly_fields(request)
        self.assertIn("person_list", readonly_fields)

        # 権限を付与した場合は readonly に入らない
        perm = Permission.objects.get(codename="change_personlist", content_type__app_label="persons")
        self.staff_user.user_permissions.add(perm)
        self.staff_user = User.objects.get(pk=self.staff_user.pk)
        request.user = self.staff_user

        readonly_fields_with_perm = person_admin.get_readonly_fields(request)
        self.assertNotIn("person_list", readonly_fields_with_perm)
