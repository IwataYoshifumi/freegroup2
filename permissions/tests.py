from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Department, UserGroup
from contacts.models import Contact
from deals.models import Deal, DealList, DealUser
from permissions.models import AccessList, ACLEntry, AccessListUserRole
from permissions.services import AccessListService
from persons.models import Person, PersonList

User = get_user_model()


class OrderEvaluationFirstMatchTests(TestCase):
    """順序評価・先勝ち判定の検証 (§5.1)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="admin_user")
        self.user = User.objects.create_user(username="test_user")
        self.access_list = AccessList.objects.create(
            name="テストACL", created_by=self.admin
        )

    def test_first_match_wins_when_admin_first(self):
        # order=10 に admin、order=20 に viewer
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.user,
        )
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=20,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=self.user,
        )

        AccessListService.rebuild_for_access_list(self.access_list)

        roles = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user
        )
        self.assertEqual(roles.count(), 1)
        self.assertEqual(roles.first().role, AccessListUserRole.Role.ADMIN)

    def test_first_match_wins_when_none_first_cancels_subsequent(self):
        # order=10 に none（未設定）、order=20 に admin
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.NONE,
            target=self.user,
        )
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=20,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.user,
        )

        AccessListService.rebuild_for_access_list(self.access_list)

        # 未設定が先勝ちで採用されたため、レコードは作成されない
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )

    def test_tie_break_order_created_at_and_id(self):
        # order が同一の場合は created_at 昇順でタイブレーク
        entry_first = ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.user,
        )
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=self.user,
        )

        AccessListService.rebuild_for_access_list(self.access_list)

        roles = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user
        )
        self.assertEqual(roles.count(), 1)
        self.assertEqual(roles.first().role, AccessListUserRole.Role.EDITOR)


class DepartmentInheritanceTests(TestCase):
    """部署階層継承の検証 (§5.2, §6.2)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="dept_admin")
        self.dept_parent = Department.objects.create(name="本社営業本部")
        self.dept_child = Department.objects.create(
            name="東京営業部", parent=self.dept_parent
        )
        self.dept_grandchild = Department.objects.create(
            name="法人第1課", parent=self.dept_child
        )

        self.user_parent = User.objects.create_user(
            username="user_p", department=self.dept_parent
        )
        self.user_child = User.objects.create_user(
            username="user_c", department=self.dept_child
        )
        self.user_grandchild = User.objects.create_user(
            username="user_gc", department=self.dept_grandchild
        )
        self.user_other = User.objects.create_user(username="user_other")

        self.access_list = AccessList.objects.create(
            name="営業ACL", created_by=self.admin
        )

    def test_parent_department_entry_propagates_to_descendants(self):
        # dept_parent を対象に editor で設定
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.dept_parent,
        )

        AccessListService.rebuild_for_access_list(self.access_list)

        # 親部署、子部署、孫部署の所属ユーザー全員に権限が波及
        role_p = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user_parent
        ).first()
        role_c = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user_child
        ).first()
        role_gc = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user_grandchild
        ).first()
        role_other = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user_other
        ).first()

        self.assertIsNotNone(role_p)
        self.assertEqual(role_p.role, AccessListUserRole.Role.EDITOR)
        self.assertIsNotNone(role_c)
        self.assertEqual(role_c.role, AccessListUserRole.Role.EDITOR)
        self.assertIsNotNone(role_gc)
        self.assertEqual(role_gc.role, AccessListUserRole.Role.EDITOR)
        self.assertIsNone(role_other)

    def test_rebuild_for_department_includes_ancestors(self):
        # 親部署を対象としたACLEntryがある状態で、孫部署を引数に rebuild_for_department を呼ぶ
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=self.dept_parent,
        )

        # 祖先チェーンを辿り、access_list が再計算対象に含まれることを確認
        acl_ids = AccessListService.get_acl_ids_for_department(self.dept_grandchild)
        self.assertIn(self.access_list.id, acl_ids)

        AccessListService.rebuild_for_department(self.dept_grandchild)
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user_grandchild
            ).exists()
        )


class UserGroupExceptionNegationTests(TestCase):
    """UserGroup による例外打ち消しの検証 (§5.3)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="group_admin")
        self.dept = Department.objects.create(name="開発部")
        self.user1 = User.objects.create_user(username="dev1", department=self.dept)
        self.user2 = User.objects.create_user(username="dev2", department=self.dept)

        self.group_excluded = UserGroup.objects.create(
            name="除外グループ", created_by=self.admin
        )
        self.group_excluded.members.add(self.user2)

        self.access_list = AccessList.objects.create(
            name="開発ACL", created_by=self.admin
        )

    def test_higher_order_none_group_negates_lower_order_department(self):
        # order=1: 除外UserGroup に none（未設定）
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=1,
            permission_level=ACLEntry.PermissionLevel.NONE,
            target=self.group_excluded,
        )
        # order=2: 開発部 に editor
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=2,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.dept,
        )

        AccessListService.rebuild_for_access_list(self.access_list)

        # user1 は editor
        role1 = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user1
        ).first()
        self.assertIsNotNone(role1)
        self.assertEqual(role1.role, AccessListUserRole.Role.EDITOR)

        # user2 は上位の none で打ち消され、レコード非作成
        role2 = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user2
        ).first()
        self.assertIsNone(role2)


class InactiveUserExclusionTests(TestCase):
    """退職者除外の検証 (§5.1, §6.2)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="status_admin")
        self.dept = Department.objects.create(name="総務部")
        self.active_user = User.objects.create_user(
            username="active_u", department=self.dept, is_active=True
        )
        self.inactive_user = User.objects.create_user(
            username="inactive_u", department=self.dept, is_active=False
        )
        self.access_list = AccessList.objects.create(
            name="総務ACL", created_by=self.admin
        )

    def test_inactive_user_not_added_to_user_roles(self):
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.dept,
        )

        AccessListService.rebuild_for_access_list(self.access_list)

        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.active_user
            ).exists()
        )
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.inactive_user
            ).exists()
        )

    def test_rebuild_for_user_status_change(self):
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.dept,
        )
        AccessListService.rebuild_for_access_list(self.access_list)
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.active_user
            ).exists()
        )

        # active_user を退職（is_active=False）にして status_change 実行
        self.active_user.is_active = False
        self.active_user.save(update_fields=["is_active"])

        AccessListService.rebuild_for_user_status_change(self.active_user)

        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.active_user
            ).exists()
        )


class AtomicTransactionProtectionTests(TestCase):
    """transaction.atomic による全削除→全挿入の保護検証 (§6.2)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="atomic_admin")
        self.user = User.objects.create_user(username="atomic_u")
        self.access_list = AccessList.objects.create(
            name="トランザクションACL", created_by=self.admin
        )
        self.entry = ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.user,
        )
        # ACLEntry 作成シグナルによって ADMIN が再計算された後、テスト用に VIEWER に更新
        AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user
        ).update(role=AccessListUserRole.Role.VIEWER)

    def test_atomic_rollback_on_error(self):
        # bulk_create で例外を発生させてロールバックをシミュレート
        with patch.object(
            AccessListUserRole.objects,
            "bulk_create",
            side_effect=RuntimeError("DBエラー発生"),
        ):
            with self.assertRaises(RuntimeError):
                AccessListService.rebuild_for_access_list(self.access_list)

        # ロールバックされ、元の VIEWER ロールが維持されていること
        role = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user
        ).first()
        self.assertIsNotNone(role)
        self.assertEqual(role.role, AccessListUserRole.Role.VIEWER)


class PrivilegeSeparationTests(TestCase):
    """特権バイパスの完全分離（view_all_* と edit_all_*）の検証 (§6.2)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="priv_admin")
        self.access_list = AccessList.objects.create(
            name="機密ACL", created_by=self.admin
        )
        self.deal_list = DealList.objects.create(
            name="機密案件リスト",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=DealList.EditScope.CREATOR_ONLY,
        )
        self.person_list = PersonList.objects.create(
            name="機密パーソンリスト",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=PersonList.EditScope.CREATOR_ONLY,
        )

        self.user_viewer = User.objects.create_user(username="view_all_user")
        # view_all_deals 権限付与
        perm_view_deals = Permission.objects.filter(codename="view_all_deals").first()
        self.user_viewer.user_permissions.add(perm_view_deals)
        self.user_viewer = User.objects.get(pk=self.user_viewer.pk)

        self.user_editor = User.objects.create_user(username="edit_all_user")
        # edit_all_deals / edit_all_persons 権限付与
        perm_edit_deals = Permission.objects.filter(codename="edit_all_deals").first()
        perm_edit_persons = Permission.objects.filter(codename="edit_all_persons").first()
        self.user_editor.user_permissions.add(perm_edit_deals, perm_edit_persons)
        self.user_editor = User.objects.get(pk=self.user_editor.pk)

        self.user_plain = User.objects.create_user(username="plain_user")

        self.deal = Deal.objects.create(
            name="秘密案件", deal_list=self.deal_list, owner=self.user_plain
        )
        self.person = Person.objects.create(person_list=self.person_list)
        self.contact = Contact.objects.create(
            person=self.person,
            full_name="山田太郎",
            created_by=self.user_plain,
        )

    def test_view_all_allows_accessible_but_denies_editable(self):
        # user_viewer は accessible に含まれるが、editable には含まれない
        accessible_deals = AccessListService.accessible_deal_list_ids(self.user_viewer)
        editable_deals = AccessListService.editable_deal_list_ids(self.user_viewer)
        self.assertIn(self.deal_list.id, accessible_deals)
        self.assertNotIn(self.deal_list.id, editable_deals)

    def test_edit_all_allows_editable(self):
        editable_deals = AccessListService.editable_deal_list_ids(self.user_editor)
        editable_persons = AccessListService.editable_person_list_ids(self.user_editor)
        self.assertIn(self.deal_list.id, editable_deals)
        self.assertIn(self.person_list.id, editable_persons)

    def test_view_all_does_not_bypass_can_edit(self):
        # view_all_deals 保持者は can_edit_deal をバイパスできない
        can_edit = AccessListService.can_edit_deal(self.user_viewer, self.deal)
        self.assertFalse(can_edit)

    def test_edit_all_bypasses_can_edit(self):
        # edit_all_deals 保持者は can_edit_deal をバイパスできる
        can_edit = AccessListService.can_edit_deal(self.user_editor, self.deal)
        self.assertTrue(can_edit)

        can_edit_p = AccessListService.can_edit_person(self.user_editor, self.person)
        self.assertTrue(can_edit_p)

        can_edit_c = AccessListService.can_edit_contact(self.user_editor, self.contact)
        self.assertTrue(can_edit_c)


class TwoStageEditEvaluationTests(TestCase):
    """2段階編集判定（リスト権限＋edit_scope）の検証 (§6.2, §8.1, §8.3.4)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="stage_admin")
        self.access_list = AccessList.objects.create(
            name="2段階ACL", created_by=self.admin
        )

        self.dl_creator_only = DealList.objects.create(
            name="作成者のみ案件",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=DealList.EditScope.CREATOR_ONLY,
        )
        self.dl_participants = DealList.objects.create(
            name="参加者案件",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=DealList.EditScope.CREATOR_AND_PARTICIPANTS,
        )
        self.dl_all_editors = DealList.objects.create(
            name="全編集者案件",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=DealList.EditScope.ALL_EDITORS,
        )

        self.user_editor1 = User.objects.create_user(username="editor_1")
        self.user_editor2 = User.objects.create_user(username="editor_2")
        self.user_viewer = User.objects.create_user(username="viewer_only")
        self.user_outsider = User.objects.create_user(username="outsider")

        # ACLロール付与
        AccessListUserRole.objects.create(
            access_list=self.access_list,
            user=self.user_editor1,
            role=AccessListUserRole.Role.EDITOR,
        )
        AccessListUserRole.objects.create(
            access_list=self.access_list,
            user=self.user_editor2,
            role=AccessListUserRole.Role.EDITOR,
        )
        AccessListUserRole.objects.create(
            access_list=self.access_list,
            user=self.user_viewer,
            role=AccessListUserRole.Role.VIEWER,
        )

    def test_deal_outsider_cannot_edit_even_if_owner(self):
        # リスト権限のない outsider が owner であっても編集不可（すり抜け防止）
        deal = Deal.objects.create(
            name="外部案件",
            deal_list=self.dl_creator_only,
            owner=self.user_outsider,
        )
        self.assertFalse(AccessListService.can_edit_deal(self.user_outsider, deal))

    def test_deal_creator_only_scope(self):
        deal = Deal.objects.create(
            name="作成者限定案件",
            deal_list=self.dl_creator_only,
            owner=self.user_editor1,
        )
        # owner かつ editor -> True
        self.assertTrue(AccessListService.can_edit_deal(self.user_editor1, deal))
        # editor だが owner ではない -> False
        self.assertFalse(AccessListService.can_edit_deal(self.user_editor2, deal))

    def test_deal_creator_and_participants_scope(self):
        deal = Deal.objects.create(
            name="参加者案件",
            deal_list=self.dl_participants,
            owner=self.user_editor1,
        )
        # user_editor2 を参加者に追加
        DealUser.objects.create(deal=deal, user=self.user_editor2)

        # owner -> True
        self.assertTrue(AccessListService.can_edit_deal(self.user_editor1, deal))
        # 参加者 かつ editor -> True
        self.assertTrue(AccessListService.can_edit_deal(self.user_editor2, deal))

        # viewer を参加者に追加しても、editor ではないため False
        DealUser.objects.create(deal=deal, user=self.user_viewer)
        self.assertFalse(AccessListService.can_edit_deal(self.user_viewer, deal))

    def test_deal_all_editors_scope(self):
        deal = Deal.objects.create(
            name="全員案件",
            deal_list=self.dl_all_editors,
            owner=self.user_editor1,
        )
        # editor であれば owner 以外でも True
        self.assertTrue(AccessListService.can_edit_deal(self.user_editor2, deal))
        # viewer は False
        self.assertFalse(AccessListService.can_edit_deal(self.user_viewer, deal))

    def test_person_and_contact_edit_scopes(self):
        pl_creator_only = PersonList.objects.create(
            name="作成者限定PL",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=PersonList.EditScope.CREATOR_ONLY,
        )
        pl_all_editors = PersonList.objects.create(
            name="全編集者PL",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=PersonList.EditScope.ALL_EDITORS,
        )

        person_c = Person.objects.create(person_list=pl_creator_only)
        # creator_only の場合 can_edit_person は False を返す（can_edit_contact を使う前提）
        self.assertFalse(AccessListService.can_edit_person(self.user_editor1, person_c))

        # 自作Contactの特例: outsider であっても自分が作成したものは常に編集可
        contact_by_outsider = Contact.objects.create(
            person=person_c,
            full_name="自作連絡先",
            created_by=self.user_outsider,
        )
        self.assertTrue(
            AccessListService.can_edit_contact(self.user_outsider, contact_by_outsider)
        )

        # 他人が作成したContact: creator_only の場合他人は編集不可
        self.assertFalse(
            AccessListService.can_edit_contact(self.user_editor1, contact_by_outsider)
        )

        # all_editors の Person: editor は編集可、viewer は不可
        person_a = Person.objects.create(person_list=pl_all_editors)
        self.assertTrue(AccessListService.can_edit_person(self.user_editor1, person_a))
        self.assertFalse(AccessListService.can_edit_person(self.user_viewer, person_a))

        contact_in_a = Contact.objects.create(
            person=person_a,
            full_name="一般連絡先",
            created_by=self.user_editor1,
        )
        # all_editors では別 editor も Contact 編集可
        self.assertTrue(
            AccessListService.can_edit_contact(self.user_editor2, contact_in_a)
        )
        self.assertFalse(
            AccessListService.can_edit_contact(self.user_viewer, contact_in_a)
        )


class UnauthenticatedUserTests(TestCase):
    """未認証ユーザー時の判定および戻り値の型統一の検証 (§6.2)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="anon_admin")
        self.access_list = AccessList.objects.create(
            name="公開ACL", created_by=self.admin
        )
        self.deal_list = DealList.objects.create(
            name="公開案件リスト",
            access_list=self.access_list,
            created_by=self.admin,
        )
        self.person_list = PersonList.objects.create(
            name="公開パーソンリスト",
            access_list=self.access_list,
            created_by=self.admin,
        )
        self.deal = Deal.objects.create(
            name="案件", deal_list=self.deal_list, owner=self.admin
        )
        self.person = Person.objects.create(person_list=self.person_list)
        self.contact = Contact.objects.create(
            person=self.person, full_name="連絡先", created_by=self.admin
        )

    def test_unauthenticated_returns_empty_and_false(self):
        anon = AnonymousUser()

        # ID集合ヘルパーは空の values_list を返す
        self.assertEqual(len(AccessListService.accessible_person_list_ids(anon)), 0)
        self.assertEqual(len(AccessListService.accessible_deal_list_ids(anon)), 0)
        self.assertEqual(len(AccessListService.accessible_access_list_ids(anon)), 0)
        self.assertEqual(len(AccessListService.editable_person_list_ids(anon)), 0)
        self.assertEqual(len(AccessListService.editable_deal_list_ids(anon)), 0)

        # None を渡しても安全に空
        self.assertEqual(len(AccessListService.accessible_person_list_ids(None)), 0)

        # 編集判定は False
        self.assertFalse(AccessListService.can_edit_deal(anon, self.deal))
        self.assertFalse(AccessListService.can_edit_person(anon, self.person))
        self.assertFalse(AccessListService.can_edit_contact(anon, self.contact))
        self.assertFalse(
            AccessListService.can_merge_person(anon, self.person, self.person)
        )


# ==============================================================================
# Step 3: シグナル・管理コマンド検証テスト (§7, §7.8)
# ==============================================================================

class ACLEntrySignalTests(TestCase):
    """ACLEntry 作成・変更・削除時の自動再計算シグナル検証 (§7.1)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="acl_sig_admin")
        self.user = User.objects.create_user(username="acl_sig_user")
        self.access_list = AccessList.objects.create(
            name="シグナル検証ACL", created_by=self.admin
        )

    def test_acl_entry_create_triggers_rebuild(self):
        # ACLEntry 作成でシグナルが発火し、キャッシュが生成されること
        entry = ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.user,
        )
        role = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user
        ).first()
        self.assertIsNotNone(role)
        self.assertEqual(role.role, AccessListUserRole.Role.ADMIN)

    def test_acl_entry_update_triggers_rebuild(self):
        entry = ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.user,
        )
        # 権限レベルを EDITOR に更新
        entry.permission_level = ACLEntry.PermissionLevel.EDITOR
        entry.save()

        role = AccessListUserRole.objects.filter(
            access_list=self.access_list, user=self.user
        ).first()
        self.assertIsNotNone(role)
        self.assertEqual(role.role, AccessListUserRole.Role.EDITOR)

    def test_acl_entry_delete_triggers_rebuild(self):
        entry = ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.user,
        )
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )

        # 削除でシグナルが発火し、キャッシュからロールが消去されること
        entry.delete()
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )


class DepartmentSignalTests(TestCase):
    """部門（Department）変更・削除監視および GFK 孤児対策検証 (§7.2, §7.6)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="dept_sig_admin")
        self.user = User.objects.create_user(username="dept_sig_user")

        self.dept_p1 = Department.objects.create(name="親部署1")
        self.dept_p2 = Department.objects.create(name="親部署2")
        self.dept_child = Department.objects.create(
            name="子部署", parent=self.dept_p1
        )
        self.user.department = self.dept_child
        self.user.save()

        self.acl_p1 = AccessList.objects.create(name="P1用ACL", created_by=self.admin)
        self.acl_p2 = AccessList.objects.create(name="P2用ACL", created_by=self.admin)

        ACLEntry.objects.create(
            access_list=self.acl_p1,
            order=10,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=self.dept_p1,
        )
        ACLEntry.objects.create(
            access_list=self.acl_p2,
            order=10,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.dept_p2,
        )

    def test_department_reparenting_triggers_rebuild(self):
        # 初期状態: 子部署の親は P1 なので、P1用ACL に VIEWER を持つ
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.acl_p1, user=self.user, role=AccessListUserRole.Role.VIEWER
            ).exists()
        )
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.acl_p2, user=self.user
            ).exists()
        )

        # 親部署を P2 に変更
        self.dept_child.parent = self.dept_p2
        self.dept_child.save()

        # 旧親 P1 の ACL から除外され、新親 P2 の ACL (ADMIN) に波及していること
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.acl_p1, user=self.user
            ).exists()
        )
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.acl_p2, user=self.user, role=AccessListUserRole.Role.ADMIN
            ).exists()
        )

    def test_department_delete_cleans_orphan_acl_entries(self):
        acl = AccessList.objects.create(name="単一部署ACL", created_by=self.admin)
        dept = Department.objects.create(name="一時部署")
        self.user.department = dept
        self.user.save()

        entry = ACLEntry.objects.create(
            access_list=acl,
            order=10,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=dept,
        )
        dept_ct = ContentType.objects.get_for_model(Department)

        self.assertTrue(
            AccessListUserRole.objects.filter(access_list=acl, user=self.user).exists()
        )

        # 部署削除
        dept.delete()

        # GFK 孤児となった ACLEntry が削除されていること
        self.assertFalse(
            ACLEntry.objects.filter(
                target_content_type=dept_ct, target_object_id=str(dept.pk)
            ).exists()
        )
        # キャッシュからも除外されていること
        self.assertFalse(
            AccessListUserRole.objects.filter(access_list=acl, user=self.user).exists()
        )


class UserGroupSignalTests(TestCase):
    """UserGroup メンバー変更（m2m）および GFK 孤児対策検証 (§7.3, §7.5, §7.6)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="ug_sig_admin")
        self.user = User.objects.create_user(username="ug_sig_user")
        self.access_list = AccessList.objects.create(
            name="グループACL", created_by=self.admin
        )
        self.user_group = UserGroup.objects.create(
            name="特別PJグループ", created_by=self.admin
        )
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=10,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.user_group,
        )

    def test_user_group_m2m_forward_add_remove_clear(self):
        # 1. 順方向 add (group.members.add)
        self.user_group.members.add(self.user)
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.access_list,
                user=self.user,
                role=AccessListUserRole.Role.EDITOR,
            ).exists()
        )

        # 2. 順方向 remove (group.members.remove)
        self.user_group.members.remove(self.user)
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )

        # 3. 順方向 clear (group.members.clear)
        self.user_group.members.add(self.user)
        self.user_group.members.clear()
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )

    def test_user_group_m2m_reverse_add_remove_clear(self):
        # 1. 逆参照 add (user.user_groups.add)
        self.user.user_groups.add(self.user_group)
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.access_list,
                user=self.user,
                role=AccessListUserRole.Role.EDITOR,
            ).exists()
        )

        # 2. 逆参照 remove (user.user_groups.remove)
        self.user.user_groups.remove(self.user_group)
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )

        # 3. 逆参照 clear (user.user_groups.clear)
        self.user.user_groups.add(self.user_group)
        self.user.user_groups.clear()
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )

    def test_user_group_delete_cleans_orphan_acl_entries(self):
        self.user_group.members.add(self.user)
        ug_ct = ContentType.objects.get_for_model(UserGroup)
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )

        # グループ削除
        self.user_group.delete()

        # GFK 孤児 ACLEntry が削除されていること
        self.assertFalse(
            ACLEntry.objects.filter(
                target_content_type=ug_ct, target_object_id=str(self.user_group.pk)
            ).exists()
        )
        # キャッシュが再計算され、ロールが削除されていること
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.access_list, user=self.user
            ).exists()
        )


class UserStatusSignalTests(TestCase):
    """ユーザー状態変更・部署異動・ログインガード・GFK孤児対策検証 (§7.4, §7.6)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="u_sig_admin")
        self.user = User.objects.create_user(username="u_sig_user")

        self.dept_a = Department.objects.create(name="営業1部")
        self.dept_b = Department.objects.create(name="営業2部")
        self.user.department = self.dept_a
        self.user.save()

        self.acl_a = AccessList.objects.create(name="ACL_A", created_by=self.admin)
        self.acl_b = AccessList.objects.create(name="ACL_B", created_by=self.admin)

        ACLEntry.objects.create(
            access_list=self.acl_a,
            order=10,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=self.dept_a,
        )
        ACLEntry.objects.create(
            access_list=self.acl_b,
            order=10,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.dept_b,
        )

    def test_user_retirement_and_reactivation_signals(self):
        # 初期状態: 営業1部所属のため ACL_A に VIEWER を持つ
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.acl_a, user=self.user
            ).exists()
        )

        # 1. 退職 (is_active=False)
        self.user.is_active = False
        self.user.save()

        # キャッシュから即時剥奪されていること
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.acl_a, user=self.user
            ).exists()
        )

        # 2. 復職 (is_active=True)
        self.user.is_active = True
        self.user.save()

        # キャッシュにロールが復元されていること
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.acl_a, user=self.user, role=AccessListUserRole.Role.VIEWER
            ).exists()
        )

    def test_user_department_transfer_signals(self):
        # 初期状態: ACL_A のみ保持
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.acl_a, user=self.user
            ).exists()
        )
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.acl_b, user=self.user
            ).exists()
        )

        # 異動: 営業1部から営業2部へ
        self.user.department = self.dept_b
        self.user.save()

        # 旧部署の ACL_A から除外され、新部署の ACL_B (EDITOR) に即座に反映されること
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=self.acl_a, user=self.user
            ).exists()
        )
        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=self.acl_b, user=self.user, role=AccessListUserRole.Role.EDITOR
            ).exists()
        )

    def test_login_last_login_update_skips_rebuild_guard(self):
        """【最重要ガード】ログイン時の last_login 更新で再計算がスキップされること。"""
        with patch.object(
            AccessListService, "rebuild_for_user_status_change"
        ) as mock_status_change, patch.object(
            AccessListService, "rebuild_for_department"
        ) as mock_dept:
            self.user.last_login = timezone.now()
            self.user.save(update_fields=["last_login"])

            mock_status_change.assert_not_called()
            mock_dept.assert_not_called()

    def test_user_delete_cleans_orphan_acl_entries(self):
        # ユーザー直接指定の ACLEntry
        direct_acl = AccessList.objects.create(name="個別ACL", created_by=self.admin)
        ACLEntry.objects.create(
            access_list=direct_acl,
            order=10,
            permission_level=ACLEntry.PermissionLevel.ADMIN,
            target=self.user,
        )
        user_ct = ContentType.objects.get_for_model(User)

        self.assertTrue(
            AccessListUserRole.objects.filter(
                access_list=direct_acl, user=self.user
            ).exists()
        )

        user_pk = str(self.user.pk)
        # ユーザー削除
        self.user.delete()

        # GFK 孤児 ACLEntry が削除されていること
        self.assertFalse(
            ACLEntry.objects.filter(
                target_content_type=user_ct, target_object_id=user_pk
            ).exists()
        )
        # キャッシュも削除されていること
        self.assertFalse(
            AccessListUserRole.objects.filter(
                access_list=direct_acl, user_id=user_pk
            ).exists()
        )


class RebuildAllAccessListsCommandTests(TestCase):
    """自己修復管理コマンド rebuild_all_access_lists の実行検証 (§6.3, §7.8)。"""

    def setUp(self):
        self.admin = User.objects.create_user(username="cmd_admin")
        self.user1 = User.objects.create_user(username="cmd_user1")
        self.user2 = User.objects.create_user(username="cmd_user2")

        self.acl1 = AccessList.objects.create(name="バッチ検証ACL1", created_by=self.admin)
        self.acl2 = AccessList.objects.create(name="バッチ検証ACL2", created_by=self.admin)

        ACLEntry.objects.create(
            access_list=self.acl1,
            order=10,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=self.user1,
        )
        ACLEntry.objects.create(
            access_list=self.acl2,
            order=10,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.user2,
        )

    def test_rebuild_all_access_lists_command_executes_successfully(self):
        # キャッシュを意図的に全削除して壊れた状態をシミュレート
        AccessListUserRole.objects.all().delete()
        self.assertEqual(AccessListUserRole.objects.count(), 0)

        # 管理コマンド実行
        out = StringIO()
        call_command("rebuild_all_access_lists", stdout=out)
        output = out.getvalue()

        self.assertIn("再構築完了", output)
        self.assertIn("すべての AccessList の再構築が正常に完了しました", output)

        # 全ての AccessListUserRole が完全修復されたこと
        role1 = AccessListUserRole.objects.filter(
            access_list=self.acl1, user=self.user1
        ).first()
        self.assertIsNotNone(role1)
        self.assertEqual(role1.role, AccessListUserRole.Role.VIEWER)

        role2 = AccessListUserRole.objects.filter(
            access_list=self.acl2, user=self.user2
        ).first()
        self.assertIsNotNone(role2)
        self.assertEqual(role2.role, AccessListUserRole.Role.EDITOR)

    def test_rebuild_all_access_lists_dry_run(self):
        # キャッシュ全削除
        AccessListUserRole.objects.all().delete()

        # ドライラン実行
        out = StringIO()
        call_command("rebuild_all_access_lists", dry_run=True, stdout=out)
        output = out.getvalue()

        self.assertIn("ドライランモード", output)
        # 再計算されず、ロール数は0のままであること
        self.assertEqual(AccessListUserRole.objects.count(), 0)

