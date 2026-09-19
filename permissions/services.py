from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from permissions.models import AccessList, ACLEntry, AccessListUserRole


class AccessListService:
    """AccessList 評価・再計算エンジンおよび権限判定サービス (§5, §6.2)。"""

    # ------------------------------------------------------------------
    # 再計算・評価エンジン (§5.1, §5.2, §5.3, §6.2)
    # ------------------------------------------------------------------

    @classmethod
    def rebuild_for_access_list(cls, access_list):
        """1つのAccessListについてAccessListUserRoleキャッシュを全再計算する (§5.1, §6.2)。

        - 並び順昇順走査、タイブレーク規則（order, created_at, id）
        - ACLEntry 対象（CustomUser, Department階層継承, UserGroupメンバー）の実ユーザー展開
        - 先勝ち（First-match wins）ルール（初出ユーザーのみ採用）
        - 退職者（is_active=False）の除外
        - 権限レベル「未設定」確定時のレコード非作成（打ち消し・例外処理）
        - トランザクション保護（全削除→全挿入の atomic 実行）
        """
        if not isinstance(access_list, AccessList):
            access_list = AccessList.objects.get(pk=access_list)

        User = get_user_model()
        Department = apps.get_model("accounts", "Department")
        UserGroup = apps.get_model("accounts", "UserGroup")

        entries = access_list.entries.select_related("target_content_type").order_by(
            "order", "created_at", "id"
        )

        seen_user_ids = set()
        user_roles_to_create = []

        for entry in entries:
            target = entry.target
            if target is None:
                continue

            # 対象の実ユーザー一覧を展開
            if isinstance(target, User):
                users = [target] if target.is_active else []
            elif isinstance(target, Department):
                depts = target.descendants(include_self=True)
                users = User.objects.filter(department__in=depts, is_active=True)
            elif isinstance(target, UserGroup):
                users = target.members.filter(is_active=True)
            else:
                users = []

            for user in users:
                if user.pk in seen_user_ids:
                    # 先勝ちルール: 既に上位エントリで確定したユーザーはスキップ
                    continue
                seen_user_ids.add(user.pk)

                # 権限レベル「未設定」確定時はレコードを作成しない（打ち消し）
                if entry.permission_level == ACLEntry.PermissionLevel.NONE or entry.permission_level == "none":
                    continue

                user_roles_to_create.append(
                    AccessListUserRole(
                        access_list=access_list,
                        user=user,
                        role=entry.permission_level,
                    )
                )

        with transaction.atomic():
            AccessListUserRole.objects.filter(access_list=access_list).delete()
            if user_roles_to_create:
                AccessListUserRole.objects.bulk_create(user_roles_to_create)

    @classmethod
    def rebuild_for_access_list_id(cls, access_list_id):
        """access_list_id をキーに rebuild_for_access_list を呼び出す補助メソッド。"""
        return cls.rebuild_for_access_list(access_list_id)

    @classmethod
    def get_ancestor_ids(cls, department):
        """department自身および祖先チェーンのIDリスト（文字列）を返す (§6.2)。"""
        Department = apps.get_model("accounts", "Department")
        if not isinstance(department, Department):
            department = Department.objects.filter(pk=department).first()
        if not department:
            return []
        ancestor_ids = [str(department.pk)]
        curr = department.parent
        visited = {department.pk}
        while curr:
            if curr.pk in visited:
                break
            visited.add(curr.pk)
            ancestor_ids.append(str(curr.pk))
            curr = curr.parent
        return ancestor_ids

    @classmethod
    def get_acl_ids_for_department(cls, department):
        """指定Departmentまたはその祖先チェーンを対象とするACLEntryを持つAccessList ID集合を返す。"""
        Department = apps.get_model("accounts", "Department")
        dept_ct = ContentType.objects.get_for_model(Department)
        ancestor_ids = cls.get_ancestor_ids(department)
        if not ancestor_ids:
            return set()
        return set(
            ACLEntry.objects.filter(
                target_content_type=dept_ct,
                target_object_id__in=ancestor_ids,
            ).values_list("access_list_id", flat=True).distinct()
        )

    @classmethod
    def rebuild_for_department(cls, department):
        """指定Departmentまたはその祖先を対象とするACLEntryを持つ全AccessListを再計算する (§6.2)。"""
        acl_ids = cls.get_acl_ids_for_department(department)
        for acl_id in acl_ids:
            cls.rebuild_for_access_list_id(acl_id)

    @classmethod
    def get_acl_ids_for_user_group(cls, user_group):
        """指定UserGroupを対象とするACLEntryを持つAccessList ID集合を返す。"""
        UserGroup = apps.get_model("accounts", "UserGroup")
        ug_ct = ContentType.objects.get_for_model(UserGroup)
        ug_pk = getattr(user_group, "pk", user_group)
        return set(
            ACLEntry.objects.filter(
                target_content_type=ug_ct,
                target_object_id=str(ug_pk),
            ).values_list("access_list_id", flat=True).distinct()
        )

    @classmethod
    def rebuild_for_user_group(cls, user_group):
        """指定UserGroupを対象とするACLEntryを持つ全AccessListを再計算する (§6.2)。"""
        acl_ids = cls.get_acl_ids_for_user_group(user_group)
        for acl_id in acl_ids:
            cls.rebuild_for_access_list_id(acl_id)

    @classmethod
    def get_acl_ids_for_user(cls, user):
        """特定ユーザーが直接指定されたACLEntryを持つAccessList ID集合を返す。"""
        User = get_user_model()
        user_ct = ContentType.objects.get_for_model(User)
        user_pk = getattr(user, "pk", user)
        return set(
            ACLEntry.objects.filter(
                target_content_type=user_ct,
                target_object_id=str(user_pk),
            ).values_list("access_list_id", flat=True).distinct()
        )

    @classmethod
    def rebuild_for_user(cls, user):
        """特定ユーザーが直接指定されたACLEntryを持つ全AccessListを再計算する (§6.2)。"""
        acl_ids = cls.get_acl_ids_for_user(user)
        for acl_id in acl_ids:
            cls.rebuild_for_access_list_id(acl_id)

    @classmethod
    def rebuild_for_user_status_change(cls, user):
        """退職・復職・新規作成時に呼ぶ統合エントリポイント (§6.2)。

        対象AccessListをsetで集約してから重複排除して1回ずつ再計算する。
        """
        User = get_user_model()
        if not isinstance(user, User):
            user = User.objects.filter(pk=user).first()
        if not user:
            return

        target_acl_ids = set()
        target_acl_ids.update(cls.get_acl_ids_for_user(user))
        if getattr(user, "department_id", None) and user.department:
            target_acl_ids.update(cls.get_acl_ids_for_department(user.department))
        for user_group in user.user_groups.all():
            target_acl_ids.update(cls.get_acl_ids_for_user_group(user_group))

        for acl_id in target_acl_ids:
            cls.rebuild_for_access_list_id(acl_id)

    # ------------------------------------------------------------------
    # 一覧表示・閲覧判定用ヘルパー (§6.2, §8.1, §8.3.1)
    # ------------------------------------------------------------------

    @staticmethod
    def accessible_person_list_ids(user):
        """ユーザーが閲覧できるPersonListのID集合を返す（特権バイパスを内包）。
        権限レベル（管理者／編集者／閲覧者）を問わない。一覧表示・閲覧判定用。
        """
        PersonList = apps.get_model("persons", "PersonList")
        if not user or not user.is_authenticated:
            return PersonList.objects.none().values_list("id", flat=True)
        if user.is_superuser or user.has_perm("persons.view_all_persons"):
            return PersonList.objects.values_list("id", flat=True)
        return PersonList.objects.filter(
            access_list__user_roles__user=user
        ).distinct().values_list("id", flat=True)

    @staticmethod
    def accessible_deal_list_ids(user):
        """ユーザーが閲覧できるDealListのID集合を返す（特権バイパスを内包）。
        権限レベルを問わない。一覧表示・閲覧判定用。
        """
        DealList = apps.get_model("deals", "DealList")
        if not user or not user.is_authenticated:
            return DealList.objects.none().values_list("id", flat=True)
        if user.is_superuser or user.has_perm("deals.view_all_deals"):
            return DealList.objects.values_list("id", flat=True)
        return DealList.objects.filter(
            access_list__user_roles__user=user
        ).distinct().values_list("id", flat=True)

    @staticmethod
    def accessible_access_list_ids(user):
        """ユーザーが閲覧できるAccessListのID集合を返す（特権バイパスを内包）。
        権限レベル（管理者／編集者／閲覧者）を問わない。
        """
        AccessList = apps.get_model("permissions", "AccessList")
        if not user or not user.is_authenticated:
            return AccessList.objects.none().values_list("id", flat=True)
        if user.is_superuser or user.has_perm("permissions.view_all_access_lists"):
            return AccessList.objects.values_list("id", flat=True)
        return AccessList.objects.filter(
            user_roles__user=user
        ).distinct().values_list("id", flat=True)

    # ------------------------------------------------------------------
    # 編集用ヘルパー・権限判定述語 (§6.2, §8.1, §8.3.4)
    # ------------------------------------------------------------------

    @staticmethod
    def editable_person_list_ids(user):
        """ユーザーが「編集者」以上の権限を持つPersonListのID集合を返す（特権バイパスを内包）。
        新規Person作成時等の選択肢用。特権バイパスには edit_all_persons を使用。
        """
        PersonList = apps.get_model("persons", "PersonList")
        if not user or not user.is_authenticated:
            return PersonList.objects.none().values_list("id", flat=True)
        if user.is_superuser or user.has_perm("persons.edit_all_persons"):
            return PersonList.objects.values_list("id", flat=True)
        return PersonList.objects.filter(
            access_list__user_roles__user=user,
            access_list__user_roles__role__in=["editor", "admin"],
        ).distinct().values_list("id", flat=True)

    @staticmethod
    def editable_deal_list_ids(user):
        """ユーザーが「編集者」以上の権限を持つDealListのID集合を返す（特権バイパスを内包）。
        新規Deal作成時等の選択肢用。特権バイパスには edit_all_deals を使用。
        """
        DealList = apps.get_model("deals", "DealList")
        if not user or not user.is_authenticated:
            return DealList.objects.none().values_list("id", flat=True)
        if user.is_superuser or user.has_perm("deals.edit_all_deals"):
            return DealList.objects.values_list("id", flat=True)
        return DealList.objects.filter(
            access_list__user_roles__user=user,
            access_list__user_roles__role__in=["editor", "admin"],
        ).distinct().values_list("id", flat=True)

    @staticmethod
    def can_edit_deal(user, deal):
        """データレベル権限としてこのDealを編集できるか（編集範囲設定を含む）。

        大前提: リスト編集者以上 (editor/admin)
        AND条件: 編集範囲設定 (creator_only: owner, creator_and_participants: ownerまたはdeal_users)
        特権バイパス: edit_all_deals (view_all_deals は不可)
        """
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm("deals.edit_all_deals"):
            return True

        # 1. 大前提: そのDealListの「編集者」以上の権限を持っていること
        is_list_editor = AccessListUserRole.objects.filter(
            access_list=deal.deal_list.access_list_id,
            user=user,
            role__in=["editor", "admin"],
        ).exists()
        if not is_list_editor:
            return False

        # 2. 編集範囲設定によるさらなる絞り込み（AND条件）
        scope = deal.deal_list.edit_scope
        if scope == "creator_only":
            return deal.owner_id == user.id
        if scope == "creator_and_participants":
            return deal.owner_id == user.id or deal.deal_users.filter(user=user).exists()
        return True  # all_editors

    @staticmethod
    def can_edit_person(user, person):
        """データレベル権限としてこのPersonを編集できるか（編集範囲設定を含む）。

        creator_only設定時はFalseを返す（呼び出し側がcan_edit_contactを使う前提）。
        特権バイパス: edit_all_persons (view_all_persons は不可)
        """
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm("persons.edit_all_persons"):
            return True

        scope = person.person_list.edit_scope
        if scope == "all_editors":
            return AccessListUserRole.objects.filter(
                access_list=person.person_list.access_list_id,
                user=user,
                role__in=["editor", "admin"],
            ).exists()
        return False

    @classmethod
    def can_edit_contact(cls, user, contact):
        """Contact単位の編集可否。

        自分が作成したContactはcreated_by特例により編集範囲設定に関わらず常に編集可。
        all_editorsの場合はcan_edit_person()と同じ判定に委譲する。
        特権バイパス: edit_all_persons (view_all_persons は不可)
        """
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm("persons.edit_all_persons"):
            return True

        # created_by特例: 自分が作成したContactは、編集範囲設定に関わらず常に編集可
        if contact.created_by_id == user.id:
            return True

        scope = contact.person.person_list.edit_scope
        if scope == "creator_only":
            return False

        return cls.can_edit_person(user, contact.person)

    @staticmethod
    def can_merge_person(user, person_a, person_b):
        """マージ実行時のデータレベル権限チェック (§10.3)。

        機能レベル権限 persons.merge_person に加え、両方のPersonListに対して
        「編集者」以上の権限を持つことを必須条件とする。
        """
        if not user or not user.is_authenticated:
            return False
        if not user.has_perm("persons.merge_person"):
            return False
        if user.is_superuser or user.has_perm("persons.edit_all_persons"):
            return True

        return (
            AccessListUserRole.objects.filter(
                access_list=person_a.person_list.access_list_id,
                user=user,
                role__in=["editor", "admin"],
            ).exists()
            and AccessListUserRole.objects.filter(
                access_list=person_b.person_list.access_list_id,
                user=user,
                role__in=["editor", "admin"],
            ).exists()
        )
