"""FreeGroup2 AccessList機構 Step 4 総合検証テストスイート。

1. QuerySet データレベル認可 (DealQuerySet.visible_for, PersonQuerySet.visible_for)
2. 新設24ルーティングおよびView動作 (AccessList, ACLEntry, DealList, PersonList, UserGroup)
3. フォーム層統合と選択肢制御 (DealForm, ContactCreateForm)
4. 【v1.6 最重要ガード】Update画面での現在値保護 (DealListUpdateView, PersonListUpdateView)
5. ActionLog 監査ログ記録
6. Person マージ処理の統合
"""

import json
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Department, UserGroup
from actionlogs.constants import (
    ACCESS_LIST_CREATED,
    ACCESS_LIST_DELETED,
    ACCESS_LIST_UPDATED,
    ACL_ENTRY_CREATED,
    ACL_ENTRY_DELETED,
    ACL_ENTRY_REORDERED,
    ACL_ENTRY_UPDATED,
    DEAL_LIST_CREATED,
    DEAL_LIST_DELETED,
    DEAL_LIST_UPDATED,
    PERSON_LIST_CREATED,
    PERSON_LIST_DELETED,
    PERSON_LIST_UPDATED,
    USER_GROUP_CREATED,
    USER_GROUP_DELETED,
    USER_GROUP_MEMBER_ADDED,
    USER_GROUP_MEMBER_REMOVED,
    USER_GROUP_UPDATED,
)
from actionlogs.models import ActionLog
from contacts.forms import ContactCreateForm
from contacts.models import Contact
from deals.forms import DealForm, DealListForm
from deals.models import Deal, DealList, DealUser, Stage
from permissions.forms import ACLEntryForm, AccessListForm
from permissions.models import AccessList, ACLEntry, AccessListUserRole
from permissions.services import AccessListService
from persons.forms import PersonListForm
from persons.models import Person, PersonList

User = get_user_model()


class Step4BaseTestCase(TestCase):
    """Step 4 テスト用ベースクラス。"""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="step4_admin", email="admin@example.com", password="password"
        )
        self.user_editor = User.objects.create_user(
            username="step4_editor", email="editor@example.com", password="password"
        )
        self.user_viewer = User.objects.create_user(
            username="step4_viewer", email="viewer@example.com", password="password"
        )
        self.user_outsider = User.objects.create_user(
            username="step4_outsider", email="outsider@example.com", password="password"
        )

        # 権限付与
        for codename in [
            "add_accesslist", "change_accesslist", "delete_accesslist", "view_accesslist",
            "add_deallist", "change_deallist", "delete_deallist", "view_deallist",
            "add_personlist", "change_personlist", "delete_personlist", "view_personlist",
            "add_usergroup", "change_usergroup", "delete_usergroup", "view_usergroup",
            "add_deal", "change_deal", "view_deal",
            "add_person", "change_person", "view_person",
            "add_contact", "change_contact", "view_contact",
        ]:
            perm = Permission.objects.filter(codename=codename).first()
            if perm:
                self.user_editor.user_permissions.add(perm)

        # AccessList 作成
        self.access_list = AccessList.objects.create(name="社内ACL", created_by=self.admin)
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=1,
            permission_level=ACLEntry.PermissionLevel.EDITOR,
            target=self.user_editor,
        )
        ACLEntry.objects.create(
            access_list=self.access_list,
            order=2,
            permission_level=ACLEntry.PermissionLevel.VIEWER,
            target=self.user_viewer,
        )
        AccessListService.rebuild_for_access_list(self.access_list)

        # DealList & PersonList 作成
        self.deal_list = DealList.objects.create(
            name="案件リストA",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=DealList.EditScope.ALL_EDITORS,
        )
        self.person_list = PersonList.objects.create(
            name="パーソンリストA",
            access_list=self.access_list,
            created_by=self.admin,
            edit_scope=PersonList.EditScope.ALL_EDITORS,
        )


class QuerySetAuthorizationTests(Step4BaseTestCase):
    """(1) QuerySet 拡張とデータレベル認可適用 (§8.2)。"""

    def test_deal_queryset_visible_for(self):
        deal1 = Deal.objects.create(name="案件1", deal_list=self.deal_list, owner=self.admin)
        deal2 = Deal.objects.create(name="案件2（外部）", owner=self.admin)

        # editor and viewer can see deal1 (via access_list)
        self.assertIn(deal1, Deal.objects.visible_for(self.user_editor))
        self.assertIn(deal1, Deal.objects.visible_for(self.user_viewer))
        self.assertNotIn(deal1, Deal.objects.visible_for(self.user_outsider))

        # outsider can see if they are owner
        deal2.owner = self.user_outsider
        deal2.save()
        self.assertIn(deal2, Deal.objects.visible_for(self.user_outsider))

        # outsider can see if they are DealUser
        deal3 = Deal.objects.create(name="案件3", owner=self.admin)
        DealUser.objects.create(deal=deal3, user=self.user_outsider, role="participant")
        self.assertIn(deal3, Deal.objects.visible_for(self.user_outsider))

        # unauthenticated returns none
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(Deal.objects.visible_for(AnonymousUser()).count(), 0)

    def test_person_queryset_visible_for(self):
        person1 = Person.objects.create(person_list=self.person_list)
        other_list = PersonList.objects.create(
            name="秘密リスト",
            access_list=AccessList.objects.create(name="秘密ACL", created_by=self.admin),
            created_by=self.admin,
        )
        person2 = Person.objects.create(person_list=other_list)

        self.assertIn(person1, Person.objects.visible_for(self.user_editor))
        self.assertIn(person1, Person.objects.visible_for(self.user_viewer))
        self.assertNotIn(person1, Person.objects.visible_for(self.user_outsider))
        self.assertNotIn(person2, Person.objects.visible_for(self.user_editor))


class AccessListAndACLEntryViewTests(Step4BaseTestCase):
    """(2) permissions アプリ（9ルート）の検証。"""

    def test_access_list_list_and_detail(self):
        self.client.login(username="step4_admin", password="password")
        # 1. List
        resp = self.client.get(reverse("permissions:access_list_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.access_list.name)

        # 3. Detail
        resp = self.client.get(reverse("permissions:access_list_detail", kwargs={"pk": self.access_list.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "設定エントリ")

    def test_access_list_create_update_delete(self):
        self.client.login(username="step4_admin", password="password")
        # 管理者エントリ（step4_admin）を entries_json に含める（管理者必須バリデーション対応）
        admin_entry = json.dumps([
            {
                "id": "",
                "order": 1,
                "permission_level": "admin",
                "target_type": "user",
                "target_id": str(self.admin.pk),
            }
        ])

        # 2. Create
        resp = self.client.post(
            reverse("permissions:access_list_create"),
            {"name": "新規ACL", "description": "テスト説明", "entries_json": admin_entry},
        )
        self.assertEqual(resp.status_code, 302)
        new_acl = AccessList.objects.get(name="新規ACL")
        self.assertTrue(
            ActionLog.objects.filter(action=ACCESS_LIST_CREATED, object_id=str(new_acl.pk)).exists()
        )

        # 4. Update
        resp = self.client.post(
            reverse("permissions:access_list_update", kwargs={"pk": new_acl.pk}),
            {"name": "更新ACL", "description": "更新説明", "entries_json": admin_entry},
        )
        self.assertEqual(resp.status_code, 302)
        new_acl.refresh_from_db()
        self.assertEqual(new_acl.name, "更新ACL")
        self.assertTrue(
            ActionLog.objects.filter(action=ACCESS_LIST_UPDATED, object_id=str(new_acl.pk)).exists()
        )

        # 5. Delete (Success when unreferenced)
        resp = self.client.post(reverse("permissions:access_list_delete", kwargs={"pk": new_acl.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(AccessList.objects.filter(pk=new_acl.pk).exists())
        self.assertTrue(ActionLog.objects.filter(action=ACCESS_LIST_DELETED).exists())


    def test_access_list_delete_protected_by_deallist_or_personlist(self):
        self.client.login(username="step4_admin", password="password")
        # self.access_list is referenced by self.deal_list
        resp = self.client.post(reverse("permissions:access_list_delete", kwargs={"pk": self.access_list.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(AccessList.objects.filter(pk=self.access_list.pk).exists())

    def test_acl_entry_crud_and_reorder(self):
        self.client.login(username="step4_admin", password="password")
        # 6. Entry Create
        resp = self.client.post(
            reverse("permissions:acl_entry_create", kwargs={"pk": self.access_list.pk}),
            {
                "target_type": "user",
                "user_target": self.user_outsider.pk,
                "permission_level": "editor",
                "order": 10,
            },
        )
        self.assertEqual(resp.status_code, 302)
        entry = ACLEntry.objects.get(access_list=self.access_list, order=10)
        self.assertEqual(entry.permission_level, "editor")
        self.assertTrue(ActionLog.objects.filter(action=ACL_ENTRY_CREATED).exists())

        # 7. Entry Update
        resp = self.client.post(
            reverse("permissions:acl_entry_update", kwargs={"pk": self.access_list.pk, "entry_pk": entry.pk}),
            {
                "target_type": "user",
                "user_target": self.user_outsider.pk,
                "permission_level": "viewer",
                "order": 10,
            },
        )
        self.assertEqual(resp.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.permission_level, "viewer")
        self.assertTrue(ActionLog.objects.filter(action=ACL_ENTRY_UPDATED).exists())

        # 9. Entry Reorder (Up/Down)
        resp = self.client.post(
            reverse("permissions:acl_entry_reorder", kwargs={"pk": self.access_list.pk}),
            {"entry_id": str(entry.pk), "direction": "up"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ActionLog.objects.filter(action=ACL_ENTRY_REORDERED).exists())

        # 8. Entry Delete
        resp = self.client.post(
            reverse("permissions:acl_entry_delete", kwargs={"pk": self.access_list.pk, "entry_pk": entry.pk}),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ACLEntry.objects.filter(pk=entry.pk).exists())
        self.assertTrue(ActionLog.objects.filter(action=ACL_ENTRY_DELETED).exists())

    def test_inline_batch_create_and_update_with_entries(self):
        """インライン一括編集による AccessList と ACLEntry の不可分保存・キャッシュ再計算の検証。"""
        import json
        self.client.login(username="step4_admin", password="password")

        # 1. Create 画面 GET
        resp = self.client.get(reverse("permissions:access_list_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("users_data", resp.context)
        self.assertIn("departments_data", resp.context)
        self.assertIn("user_groups_data", resp.context)

        # 2. 一括作成 POST
        entries_payload = [
            {"order": 1, "permission_level": "admin", "target_type": "user", "target_id": str(self.user_editor.pk)},
            {"order": 2, "permission_level": "viewer", "target_type": "user", "target_id": str(self.user_viewer.pk)},
        ]
        resp = self.client.post(
            reverse("permissions:access_list_create"),
            {
                "name": "一括テストACL",
                "description": "インライン一括登録テスト",
                "entries_json": json.dumps(entries_payload),
            },
        )
        self.assertEqual(resp.status_code, 302)
        new_acl = AccessList.objects.get(name="一括テストACL")
        self.assertEqual(new_acl.entries.count(), 2)
        # キャッシュの即時完全再計算確認
        self.assertTrue(
            AccessListUserRole.objects.filter(access_list=new_acl, user=self.user_editor, role="admin").exists()
        )
        self.assertTrue(
            AccessListUserRole.objects.filter(access_list=new_acl, user=self.user_viewer, role="viewer").exists()
        )

        # 3. Update 画面 GET
        resp_get = self.client.get(reverse("permissions:access_list_update", kwargs={"pk": new_acl.pk}))
        self.assertEqual(resp_get.status_code, 200)
        self.assertEqual(len(resp_get.context["initial_entries"]), 2)

        # 4. 一括更新 POST（user_editor を viewer に変更、user_viewer を削除、user_outsider を admin として追加）
        updated_payload = [
            {"order": 1, "permission_level": "admin", "target_type": "user", "target_id": str(self.user_outsider.pk)},
            {"order": 2, "permission_level": "viewer", "target_type": "user", "target_id": str(self.user_editor.pk)},
        ]
        resp_up = self.client.post(
            reverse("permissions:access_list_update", kwargs={"pk": new_acl.pk}),
            {
                "name": "一括テストACL（更新版）",
                "description": "更新完了",
                "entries_json": json.dumps(updated_payload),
            },
        )
        self.assertEqual(resp_up.status_code, 302)
        new_acl.refresh_from_db()
        self.assertEqual(new_acl.name, "一括テストACL（更新版）")
        self.assertEqual(new_acl.entries.count(), 2)
        self.assertTrue(
            AccessListUserRole.objects.filter(access_list=new_acl, user=self.user_outsider, role="admin").exists()
        )
        self.assertTrue(
            AccessListUserRole.objects.filter(access_list=new_acl, user=self.user_editor, role="viewer").exists()
        )
        # 削除された user_viewer のロールキャッシュは消えていること
        self.assertFalse(
            AccessListUserRole.objects.filter(access_list=new_acl, user=self.user_viewer).exists()
        )

    def test_detail_grouped_roles_and_target_lists(self):
        """詳細画面におけるロール別ユーザー表示および設定先リスト（案件/パーソン）表示の検証。"""
        self.client.login(username="step4_admin", password="password")
        resp = self.client.get(reverse("permissions:access_list_detail", kwargs={"pk": self.access_list.pk}))
        self.assertEqual(resp.status_code, 200)

        # ロール別グループ表示の検証
        self.assertIn("admin_roles", resp.context)
        self.assertIn("editor_roles", resp.context)
        self.assertIn("viewer_roles", resp.context)
        self.assertContains(resp, "確定権限ユーザー")
        self.assertContains(resp, "管理者")
        self.assertContains(resp, "編集者")
        self.assertContains(resp, "閲覧者")

        # 設定先（参照先リスト）の検証
        self.assertIn("deal_lists", resp.context)
        self.assertIn("person_lists", resp.context)
        self.assertContains(resp, "案件リストA")
        self.assertContains(resp, reverse("deal_lists:deal_list_detail", kwargs={"pk": self.deal_list.pk}))
        self.assertContains(resp, "パーソンリストA")
        self.assertContains(resp, reverse("person_lists:person_list_detail", kwargs={"pk": self.person_list.pk}))



class DealListAndPersonListViewTests(Step4BaseTestCase):
    """(2) deals & persons アプリ（各5ルート）の検証。"""

    def test_deal_list_crud(self):
        self.client.login(username="step4_admin", password="password")
        # List
        resp = self.client.get(reverse("deal_lists:deal_list_list"))
        self.assertEqual(resp.status_code, 200)

        # Create
        resp = self.client.post(
            reverse("deal_lists:deal_list_create"),
            {
                "name": "新規案件リスト",
                "description": "説明",
                "access_list": self.access_list.pk,
                "edit_scope": "creator_only",
            },
        )
        self.assertEqual(resp.status_code, 302)
        dl = DealList.objects.get(name="新規案件リスト")
        self.assertTrue(ActionLog.objects.filter(action=DEAL_LIST_CREATED).exists())

        # Detail
        resp = self.client.get(reverse("deal_lists:deal_list_detail", kwargs={"pk": dl.pk}))
        self.assertEqual(resp.status_code, 200)

        # Update
        resp = self.client.post(
            reverse("deal_lists:deal_list_update", kwargs={"pk": dl.pk}),
            {
                "name": "更新案件リスト",
                "description": "更新",
                "access_list": self.access_list.pk,
                "edit_scope": "all_editors",
            },
        )
        self.assertEqual(resp.status_code, 302)
        dl.refresh_from_db()
        self.assertEqual(dl.name, "更新案件リスト")
        self.assertTrue(ActionLog.objects.filter(action=DEAL_LIST_UPDATED).exists())

        # Delete (Protected when deals exist)
        deal = Deal.objects.create(name="D1", deal_list=dl)
        resp = self.client.post(reverse("deal_lists:deal_list_delete", kwargs={"pk": dl.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(DealList.objects.filter(pk=dl.pk).exists())

        # Delete (Success when 0 deals)
        deal.delete()
        resp = self.client.post(reverse("deal_lists:deal_list_delete", kwargs={"pk": dl.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DealList.objects.filter(pk=dl.pk).exists())
        self.assertTrue(ActionLog.objects.filter(action=DEAL_LIST_DELETED).exists())

    def test_person_list_crud(self):
        self.client.login(username="step4_admin", password="password")
        # List
        resp = self.client.get(reverse("person_lists:person_list_list"))
        self.assertEqual(resp.status_code, 200)

        # Create
        resp = self.client.post(
            reverse("person_lists:person_list_create"),
            {
                "name": "新規PL",
                "description": "説明",
                "access_list": self.access_list.pk,
                "edit_scope": "all_editors",
            },
        )
        self.assertEqual(resp.status_code, 302)
        pl = PersonList.objects.get(name="新規PL")
        self.assertTrue(ActionLog.objects.filter(action=PERSON_LIST_CREATED).exists())

        # Detail
        resp = self.client.get(reverse("person_lists:person_list_detail", kwargs={"pk": pl.pk}))
        self.assertEqual(resp.status_code, 200)

        # Update
        resp = self.client.post(
            reverse("person_lists:person_list_update", kwargs={"pk": pl.pk}),
            {
                "name": "更新PL",
                "description": "更新",
                "access_list": self.access_list.pk,
                "edit_scope": "creator_only",
            },
        )
        self.assertEqual(resp.status_code, 302)
        pl.refresh_from_db()
        self.assertEqual(pl.name, "更新PL")
        self.assertTrue(ActionLog.objects.filter(action=PERSON_LIST_UPDATED).exists())

        # Delete
        resp = self.client.post(reverse("person_lists:person_list_delete", kwargs={"pk": pl.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(PersonList.objects.filter(pk=pl.pk).exists())
        self.assertTrue(ActionLog.objects.filter(action=PERSON_LIST_DELETED).exists())


class UserGroupViewTests(Step4BaseTestCase):
    """(2) accounts アプリ UserGroup（5ルート/7エンドポイント）の検証。"""

    def test_user_group_crud_and_members(self):
        self.client.login(username="step4_admin", password="password")
        # List
        resp = self.client.get(reverse("user_groups:user_group_list"))
        self.assertEqual(resp.status_code, 200)

        # Create
        resp = self.client.post(
            reverse("user_groups:user_group_create"),
            {"name": "営業特務班", "description": "重要案件チーム"},
        )
        self.assertEqual(resp.status_code, 302)
        ug = UserGroup.objects.get(name="営業特務班")
        self.assertTrue(ActionLog.objects.filter(action=USER_GROUP_CREATED).exists())

        # Detail
        resp = self.client.get(reverse("user_groups:user_group_detail", kwargs={"pk": ug.pk}))
        self.assertEqual(resp.status_code, 200)

        # Add Member
        resp = self.client.post(
            reverse("user_groups:user_group_add_member", kwargs={"pk": ug.pk}),
            {"user_id": self.user_editor.pk},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.user_editor, ug.members.all())
        self.assertTrue(ActionLog.objects.filter(action=USER_GROUP_MEMBER_ADDED).exists())

        # Remove Member
        resp = self.client.post(
            reverse("user_groups:user_group_remove_member", kwargs={"pk": ug.pk}),
            {"user_id": self.user_editor.pk},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn(self.user_editor, ug.members.all())
        self.assertTrue(ActionLog.objects.filter(action=USER_GROUP_MEMBER_REMOVED).exists())

        # Update
        resp = self.client.post(
            reverse("user_groups:user_group_update", kwargs={"pk": ug.pk}),
            {"name": "営業特務班（改）", "description": "改定"},
        )
        self.assertEqual(resp.status_code, 302)
        ug.refresh_from_db()
        self.assertEqual(ug.name, "営業特務班（改）")
        self.assertTrue(ActionLog.objects.filter(action=USER_GROUP_UPDATED).exists())

        # Delete
        resp = self.client.post(reverse("user_groups:user_group_delete", kwargs={"pk": ug.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(UserGroup.objects.filter(pk=ug.pk).exists())
        self.assertTrue(ActionLog.objects.filter(action=USER_GROUP_DELETED).exists())


class CurrentValueProtectionAndFormTests(Step4BaseTestCase):
    """(3) フォーム層統合および【v1.6 最重要ガード】Update画面現在値保護 (§8.3.3)。"""

    def test_current_value_protection_when_access_list_not_accessible(self):
        """操作ユーザーに閲覧権限のないAccessListが設定されたリストを編集する際、
        access_list フィールドが disabled=True となり、保存時に現在値が保持されること。
        """
        # 秘密のAccessList（admin作成、editorには権限なし）
        secret_acl = AccessList.objects.create(name="極秘役員ACL", created_by=self.admin)
        deal_list = DealList.objects.create(
            name="極秘案件リスト",
            access_list=secret_acl,
            created_by=self.admin,
        )

        # user_editor は secret_acl への閲覧権限を持たない
        form = DealListForm(instance=deal_list, user=self.user_editor)
        self.assertTrue(form.fields["access_list"].disabled)
        self.assertIn("アクセスリスト管理権限を持つ人に依頼", form.fields["access_list"].help_text)

        # POST 送信時（disabledフィールドはPOSTに含まれないか、異なる値でも現在値が維持される）
        form_post = DealListForm(
            data={"name": "極秘案件リスト（名前のみ変更）", "description": "更新", "edit_scope": "all_editors"},
            instance=deal_list,
            user=self.user_editor,
        )
        self.assertTrue(form_post.is_valid())
        saved_dl = form_post.save()
        self.assertEqual(saved_dl.access_list, secret_acl)  # 現在値が保護された！

    def test_deal_form_choices_filtered_by_editable_deal_list_ids(self):
        """DealFormの案件リスト選択肢が editable_deal_list_ids で絞り込まれること。"""
        # user_editor は self.deal_list の編集権限を持つ
        form = DealForm(user=self.user_editor)
        self.assertIn(self.deal_list, form.fields["deal_list"].queryset)

        # user_viewer は閲覧権限しか持たないため editable には含まれない
        form_viewer = DealForm(user=self.user_viewer)
        self.assertNotIn(self.deal_list, form_viewer.fields["deal_list"].queryset)

    def test_deal_form_fails_when_zero_editable_lists(self):
        """編集可能リストが0件のユーザーは保存できずValidationErrorとなること（自動救済しない）。"""
        form = DealForm(
            data={"name": "新規案件", "stage": Stage.INITIAL_MEETING},
            user=self.user_outsider,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("deal_list", form.errors)


class PersonMergeIntegrationTests(Step4BaseTestCase):
    """(5) Person マージ処理の統合 (§10.1)。"""

    def test_merge_executor_saves_person_list_before_merge(self):
        from duplicates.models import PersonMergeLog, DuplicateCandidate
        from duplicates.forms import MergeForm
        from contacts.models import Contact

        p_surviving = Person.objects.create(person_list=self.person_list)
        p_merged = Person.objects.create(person_list=self.person_list)

        c1 = Contact.objects.create(person=p_surviving, last_name="Surviving", status=Contact.Status.PRIMARY)
        c2 = Contact.objects.create(person=p_merged, last_name="Merged", status=Contact.Status.PRIMARY)
        p_surviving.primary_contact = c1
        p_surviving.save()
        p_merged.primary_contact = c2
        p_merged.save()

        # 新しい別のリスト
        new_list = PersonList.objects.create(
            name="マージ後リスト", access_list=self.access_list, created_by=self.admin
        )

        # 1. PersonMergeLog.create で被統合側のリストが退避されることを確認
        log = PersonMergeLog.create(p_surviving, p_merged, self.admin)
        self.assertEqual(log.person_list_before_merge, self.person_list)

        # 2. MergeForm の selected_person_list 選択肢と初期値を確認
        candidate = DuplicateCandidate.objects.create(
            person_a=p_surviving,
            person_b=p_merged,
            score=0.9,
        )
        form = MergeForm(
            candidate=candidate,
            surviving_person=p_surviving,
            merged_person=p_merged,
            user=self.admin,
        )
        self.assertIn(new_list, form.fields["selected_person_list"].queryset)
        self.assertEqual(form.fields["selected_person_list"].initial, self.person_list.id)


class SidebarNavigationTests(Step4BaseTestCase):
    """各サイドバーのメニュー導線・HIG行き止まり排除・アクティブ状態の検証。"""

    def test_accounts_sidebar_menu_and_permissions(self):
        from django.contrib.auth.models import Permission

        perm_retire = Permission.objects.get(codename="retire_user", content_type__app_label="accounts")
        self.user_viewer.user_permissions.add(perm_retire)

        # 1. 権限なしユーザー: 部署・ユーザーグループ・アクセスリストのいずれも表示されない
        self.client.login(username="step4_viewer", password="password")
        resp = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "組織・権限管理")
        self.assertNotContains(resp, reverse("admin:accounts_department_changelist"))
        self.assertNotContains(resp, reverse("accounts:user_group_list"))
        self.assertNotContains(resp, reverse("permissions:access_list_list"))

        # 2. 権限付与: ユーザーグループとアクセスリスト
        perm_ug = Permission.objects.get(codename="view_usergroup", content_type__app_label="accounts")
        perm_al = Permission.objects.get(codename="view_all_access_lists", content_type__app_label="permissions")
        perm_dept = Permission.objects.get(codename="view_department", content_type__app_label="accounts")
        self.user_viewer.user_permissions.add(perm_ug, perm_al, perm_dept)

        resp = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "組織・権限管理")
        self.assertContains(resp, reverse("admin:accounts_department_changelist"))
        self.assertContains(resp, reverse("accounts:user_group_list"))
        self.assertContains(resp, reverse("permissions:access_list_list"))

        # 3. アクティブ表示
        resp_ug = self.client.get(reverse("accounts:user_group_list"))
        self.assertEqual(resp_ug.status_code, 200)
        self.assertContains(resp_ug, f'href="{reverse("accounts:user_group_list")}" class="is-active"')

        resp_al = self.client.get(reverse("permissions:access_list_list"))
        self.assertEqual(resp_al.status_code, 200)
        self.assertContains(resp_al, f'href="{reverse("permissions:access_list_list")}" class="is-active"')

    def test_deals_sidebar_menu_and_permissions(self):
        from django.contrib.auth.models import Permission

        # 1. 権限なしユーザー
        self.client.login(username="step4_viewer", password="password")
        resp = self.client.get(reverse("deals:deal_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("deals:deal_list_list"))

        # 2. 権限付与: view_deallist
        perm_dl = Permission.objects.get(codename="view_deallist", content_type__app_label="deals")
        self.user_viewer.user_permissions.add(perm_dl)

        resp = self.client.get(reverse("deals:deal_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("deals:deal_list_list"))

        # 3. アクティブ表示
        resp_dl = self.client.get(reverse("deals:deal_list_list"))
        self.assertEqual(resp_dl.status_code, 200)
        self.assertContains(resp_dl, f'href="{reverse("deals:deal_list_list")}" class="is-active"')

    def test_cards_sidebar_menu_and_permissions(self):
        from django.contrib.auth.models import Permission

        perm_person = Permission.objects.get(codename="view_person", content_type__app_label="persons")
        self.user_viewer.user_permissions.add(perm_person)

        # 1. 権限なしユーザー
        self.client.login(username="step4_viewer", password="password")
        resp = self.client.get(reverse("persons:person_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("persons:person_list_list"))

        # 2. 権限付与: view_personlist
        perm_pl = Permission.objects.get(codename="view_personlist", content_type__app_label="persons")
        self.user_viewer.user_permissions.add(perm_pl)

        resp = self.client.get(reverse("persons:person_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("persons:person_list_list"))

        # 3. アクティブ表示
        resp_pl = self.client.get(reverse("persons:person_list_list"))
        self.assertEqual(resp_pl.status_code, 200)
        self.assertContains(resp_pl, f'href="{reverse("persons:person_list_list")}" class="is-active"')


class AccessListFormValidationAndAlertTests(Step4BaseTestCase):
    """アクセスリストの管理者必須バリデーションおよび自己除外警告の検証。"""

    def test_form_validation_fails_without_admin_entry(self):
        """管理者エントリが0件の場合、ValidationErrorとなり保存できないこと。"""
        # 1. エントリが空の場合
        form_empty = AccessListForm(
            data={"name": "管理者なしACL", "entries_json": json.dumps([])}
        )
        self.assertFalse(form_empty.is_valid())
        self.assertIn("アクセスリストには最低1つの「管理者」を設定してください。", form_empty.non_field_errors())

        # 2. 編集者・閲覧者のみで管理者がいない場合
        entries = [
            {
                "order": 1,
                "permission_level": "editor",
                "target_type": "user",
                "target_id": str(self.user_editor.pk),
            },
            {
                "order": 2,
                "permission_level": "viewer",
                "target_type": "user",
                "target_id": str(self.user_viewer.pk),
            },
        ]
        form_no_admin = AccessListForm(
            data={"name": "管理者なしACL2", "entries_json": json.dumps(entries)}
        )
        self.assertFalse(form_no_admin.is_valid())
        self.assertIn("アクセスリストには最低1つの「管理者」を設定してください。", form_no_admin.non_field_errors())

        # direct clean() raises ValidationError
        with self.assertRaises(ValidationError) as ctx:
            form_no_admin.clean()
        self.assertIn("アクセスリストには最低1つの「管理者」を設定してください。", str(ctx.exception))

    def test_form_validation_succeeds_with_admin_entry(self):
        """管理者エントリが存在する場合、正常に保存できること。"""
        entries = [
            {
                "order": 1,
                "permission_level": "admin",
                "target_type": "user",
                "target_id": str(self.admin.pk),
            },
            {
                "order": 2,
                "permission_level": "viewer",
                "target_type": "user",
                "target_id": str(self.user_viewer.pk),
            },
        ]
        form = AccessListForm(
            data={"name": "管理者ありACL", "entries_json": json.dumps(entries)}
        )
        self.assertTrue(form.is_valid())
        saved_acl = form.save(commit=True, user=self.admin)
        self.assertEqual(saved_acl.name, "管理者ありACL")
        self.assertEqual(saved_acl.entries.count(), 2)

        # キャッシュの検証
        role_admin = AccessListUserRole.objects.get(access_list=saved_acl, user=self.admin)
        self.assertEqual(role_admin.role, ACLEntry.PermissionLevel.ADMIN)
        role_viewer = AccessListUserRole.objects.get(access_list=saved_acl, user=self.user_viewer)
        self.assertEqual(role_viewer.role, ACLEntry.PermissionLevel.VIEWER)

    def test_view_warning_message_when_user_excludes_self_from_admin(self):
        """自身を管理者から除外して保存した際、messages.warning が通知されること。"""
        self.client.login(username="step4_admin", password="password")

        # 別のユーザー（user_editor）だけを管理者にして自分を外す
        entries = [
            {
                "order": 1,
                "permission_level": "admin",
                "target_type": "user",
                "target_id": str(self.user_editor.pk),
            },
        ]
        resp = self.client.post(
            reverse("permissions:access_list_create"),
            data={"name": "自己除外ACL", "entries_json": json.dumps(entries)},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        # warning メッセージが表示されること
        messages_list = list(resp.context["messages"])
        self.assertTrue(any(
            m.level_tag == "warning" and "注意: あなたはこのアクセスリストの管理者ではなくなりました。" in m.message
            for m in messages_list
        ))

    def test_view_success_message_when_user_remains_admin(self):
        """自身が管理者のまま保存した際、messages.success が通知されること。"""
        self.client.login(username="step4_admin", password="password")

        entries = [
            {
                "order": 1,
                "permission_level": "admin",
                "target_type": "user",
                "target_id": str(self.admin.pk),
            },
        ]
        resp = self.client.post(
            reverse("permissions:access_list_create"),
            data={"name": "自己管理者ACL", "entries_json": json.dumps(entries)},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        # success メッセージが表示されること
        messages_list = list(resp.context["messages"])
        self.assertTrue(any(
            m.level_tag == "success" and "作成しました" in m.message
            for m in messages_list
        ))



