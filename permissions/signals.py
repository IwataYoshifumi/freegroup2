"""FreeGroup2 AccessList シグナルハンドラ (§7)。"""

import logging
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import ACLEntry
from .services import AccessListService

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. ACLEntry 変更監視 (§7.1)
# ==============================================================================

@receiver(post_save, sender=ACLEntry)
def acl_entry_post_save(sender, instance, **kwargs):
    """ACLEntry 作成・更新時に所属 AccessList を再計算。"""
    try:
        AccessListService.rebuild_for_access_list(instance.access_list_id)
    except Exception as e:
        logger.exception("Error rebuilding access list %s on ACLEntry save: %s", instance.access_list_id, e)


@receiver(post_delete, sender=ACLEntry)
def acl_entry_post_delete(sender, instance, **kwargs):
    """ACLEntry 削除時に所属 AccessList を再計算。"""
    try:
        AccessListService.rebuild_for_access_list(instance.access_list_id)
    except Exception as e:
        logger.exception("Error rebuilding access list %s on ACLEntry delete: %s", instance.access_list_id, e)


# ==============================================================================
# 2. 部門（Department）変更・削除監視 (§7.2, §7.6)
# ==============================================================================

@receiver(pre_save, sender="accounts.Department")
def department_pre_save(sender, instance, **kwargs):
    """変更前の parent_id を捕捉。"""
    if instance.pk:
        Department = apps.get_model("accounts", "Department")
        orig = Department.objects.filter(pk=instance.pk).only("parent_id").first()
        instance._orig_parent_id = orig.parent_id if orig else None


@receiver(post_save, sender="accounts.Department")
def department_post_save(sender, instance, created, **kwargs):
    """部門作成・更新時に影響を受ける AccessList を再計算。"""
    try:
        orig_parent_id = getattr(instance, "_orig_parent_id", None)
        Department = apps.get_model("accounts", "Department")
        if not created and orig_parent_id != instance.parent_id:
            if orig_parent_id:
                old_parent = Department.objects.filter(pk=orig_parent_id).first()
                if old_parent:
                    AccessListService.rebuild_for_department(old_parent)
            if instance.parent:
                AccessListService.rebuild_for_department(instance.parent)
        AccessListService.rebuild_for_department(instance)
    except Exception as e:
        logger.exception("Error rebuilding for department %s: %s", instance.pk, e)


@receiver(post_delete, sender="accounts.Department")
def department_post_delete(sender, instance, **kwargs):
    """部門削除時の GFK 孤児 ACLEntry 削除および影響 AccessList の再計算。"""
    try:
        Department = apps.get_model("accounts", "Department")
        dept_ct = ContentType.objects.get_for_model(Department)
        entries = ACLEntry.objects.filter(
            target_content_type=dept_ct,
            target_object_id=str(instance.pk),
        )
        acl_ids = list(entries.values_list("access_list_id", flat=True).distinct())
        entries.delete()
        for acl_id in acl_ids:
            AccessListService.rebuild_for_access_list(acl_id)
    except Exception as e:
        logger.exception("Error cleaning up orphan ACLEntry for department %s: %s", instance.pk, e)


# ==============================================================================
# 3. ユーザーグループ（UserGroup）変更・削除監視 (§7.3, §7.5, §7.6)
# ==============================================================================

@receiver(post_save, sender="accounts.UserGroup")
def user_group_post_save(sender, instance, created, **kwargs):
    """UserGroup 作成・更新時に影響を受ける AccessList を再計算。"""
    try:
        AccessListService.rebuild_for_user_group(instance)
    except Exception as e:
        logger.exception("Error rebuilding for user group %s: %s", instance.pk, e)


@receiver(post_delete, sender="accounts.UserGroup")
def user_group_post_delete(sender, instance, **kwargs):
    """UserGroup 削除時の GFK 孤児 ACLEntry 削除および影響 AccessList の再計算。"""
    try:
        UserGroup = apps.get_model("accounts", "UserGroup")
        ug_ct = ContentType.objects.get_for_model(UserGroup)
        entries = ACLEntry.objects.filter(
            target_content_type=ug_ct,
            target_object_id=str(instance.pk),
        )
        acl_ids = list(entries.values_list("access_list_id", flat=True).distinct())
        entries.delete()
        for acl_id in acl_ids:
            AccessListService.rebuild_for_access_list(acl_id)
    except Exception as e:
        logger.exception("Error cleaning up orphan ACLEntry for user group %s: %s", instance.pk, e)


@receiver(m2m_changed)
def user_group_members_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    """UserGroup メンバー変更（所属・脱退・クリア）監視 (§7.5)。"""
    UserGroup = apps.get_model("accounts", "UserGroup")
    if sender != UserGroup.members.through:
        return

    try:
        if action == "pre_clear":
            if reverse:
                # 逆参照: instance は CustomUser
                instance._pre_clear_group_ids = set(
                    instance.user_groups.values_list("id", flat=True)
                )
            else:
                # 順方向: instance は UserGroup
                pass

        elif action in ("post_add", "post_remove", "post_clear"):
            if not reverse:
                # 順方向: instance は UserGroup
                AccessListService.rebuild_for_user_group(instance)
            else:
                # 逆方向: instance は CustomUser
                if action in ("post_add", "post_remove") and pk_set:
                    for group_id in pk_set:
                        AccessListService.rebuild_for_user_group(group_id)
                elif action == "post_clear":
                    group_ids = getattr(instance, "_pre_clear_group_ids", set())
                    for group_id in group_ids:
                        AccessListService.rebuild_for_user_group(group_id)
    except Exception as e:
        logger.exception("Error handling m2m_changed for UserGroup: %s", e)


# ==============================================================================
# 4. ユーザー（CustomUser）変更・削除監視 (§7.4, §7.6)
# ==============================================================================

@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def user_pre_save(sender, instance, **kwargs):
    """ユーザー保存前に is_active, department_id の変更前状態を捕捉。

    【最重要ガード: ログイン暴走防止】
    update_fields が指定されており、かつその更新対象が last_login のみの場合は、
    過剰な再計算を抑止するため即座にスキップする。
    """
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and set(update_fields) == {"last_login"}:
        instance._skip_acl_rebuild = True
        return

    if instance.pk:
        User = apps.get_model("accounts", "CustomUser")
        orig = User.objects.filter(pk=instance.pk).only("is_active", "department_id").first()
        instance._orig_is_active = orig.is_active if orig else None
        instance._orig_department_id = orig.department_id if orig else None


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def user_post_save(sender, instance, created, **kwargs):
    """ユーザー作成、退職・復職、部署異動時の AccessList 再計算。"""
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and set(update_fields) == {"last_login"}:
        return
    if getattr(instance, "_skip_acl_rebuild", False):
        return

    try:
        if created:
            AccessListService.rebuild_for_user_status_change(instance)
            return

        # 1. is_active 変更時（退職・復職）
        if getattr(instance, "_orig_is_active", None) != instance.is_active:
            AccessListService.rebuild_for_user_status_change(instance)

        # 2. 部署異動時（旧部署・新部署の両方を再計算）
        orig_dept_id = getattr(instance, "_orig_department_id", None)
        if orig_dept_id != instance.department_id:
            Department = apps.get_model("accounts", "Department")
            if orig_dept_id:
                old_dept = Department.objects.filter(pk=orig_dept_id).first()
                if old_dept:
                    AccessListService.rebuild_for_department(old_dept)
            if instance.department:
                AccessListService.rebuild_for_department(instance.department)
    except Exception as e:
        logger.exception("Error rebuilding ACL on user save for %s: %s", instance.pk, e)


@receiver(post_delete, sender=settings.AUTH_USER_MODEL)
def user_post_delete(sender, instance, **kwargs):
    """ユーザー削除時の GFK 孤児 ACLEntry 削除および影響 AccessList の再計算。"""
    try:
        User = apps.get_model("accounts", "CustomUser")
        user_ct = ContentType.objects.get_for_model(User)
        entries = ACLEntry.objects.filter(
            target_content_type=user_ct,
            target_object_id=str(instance.pk),
        )
        acl_ids = list(entries.values_list("access_list_id", flat=True).distinct())
        entries.delete()
        for acl_id in acl_ids:
            AccessListService.rebuild_for_access_list(acl_id)
    except Exception as e:
        logger.exception("Error cleaning up orphan ACLEntry for user %s: %s", instance.pk, e)
