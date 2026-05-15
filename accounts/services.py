from django.core.exceptions import ValidationError
from django.db import transaction

from .constants import ActionLogAction


def apply_role(user, role):
    """Role を付与し、対応する PermissionGroup を自動設定する（仕様書 §8）。

    [性質] 副作用あり（DB 書込：user.role 更新 + user.groups M2M set/clear、transaction.atomic 内）
    [入力] user: CustomUser インスタンス
           role: Role インスタンス または None
    [出力] None

    ⚠️ この関数は Role が変わったときにだけ呼ぶこと。
    Role が変わっていない単なる保存で呼ぶと、手動で追加した Group がサイレントに消える。
    呼び出し側でガードする（CustomUserAdmin.save_model 参照）。

    ⚠️ user.save() を内部で呼ぶため、CustomUser の post_save シグナルでさらに
    apply_role() を呼ぶような実装は禁止（無限ループ回避）。

    ⚠️ Migration からは呼べない（historical model 制約）。
    Migration では本関数を import せず、apps.get_model() で取得した historical model に
    対して user.groups.set() を直接呼ぶこと（仕様書 §8 末尾「Migration からの呼び出し」）。
    """
    with transaction.atomic():
        user.role = role
        user.save(update_fields=["role"])
        if role is None:
            user.groups.clear()
        else:
            user.groups.set(role.default_groups.all())


def link_user_to_person(operator, user, person):
    """User と Person を紐付ける（仕様書 §12.7）。

    [性質] 副作用あり（DB 書込：user.person 更新 + ActionLog 記録、transaction.atomic 内）
    [入力] operator: 操作実行者の CustomUser（ActionLog.user に記録）
           user: 紐付け対象の CustomUser
           person: 紐付け先の Person
    [出力] None
    [例外] ValidationError（user 側 / Person 側のいずれかで既存紐付けがある場合）

    [権限チェック責務]
    本関数は権限チェックを行わない。呼び出し側 (View) で以下を保証すること:
    - operator == user (本人) または
    - operator.has_perm('accounts.link_user_to_person') (管理者権限)

    [両側ガード] OneToOne 制約違反防止のため、user 側・person 側両方をチェック。
    person.linked_user は Phase 1 のプロパティ経由（person.user 直接参照禁止、仕様書 §12.3）。
    """
    if user.person is not None:
        raise ValidationError(
            f"User {user.username} は既に別の Person に紐付いています。"
            "先に既存紐付けを解除してください"
        )

    existing_user = person.linked_user
    if existing_user is not None and existing_user != user:
        raise ValidationError(
            f"Person は既に User ({existing_user.username}) に紐付いています。"
            "先に既存紐付けを解除してください"
        )

    with transaction.atomic():
        user.person = person
        user.save(update_fields=["person"])

        from actionlogs.models import ActionLog

        ActionLog.record(
            user=operator,
            action=ActionLogAction.LINK_USER_TO_PERSON,
            content_object=user,
            object_repr=f"{user.username} ↔ Person({person.pk})",
            data={"person_id": str(person.pk)},
        )


def unlink_user_from_person(operator, user):
    """User と Person の紐付けを解除する（仕様書 §12.7）。

    [性質] 副作用あり（DB 書込：user.person を None に + ActionLog 記録、transaction.atomic 内）
    [入力] operator: 操作実行者の CustomUser
           user: 解除対象の CustomUser
    [出力] None

    [権限チェック責務] link_user_to_person() と同じ（呼び出し側で保証）。
    user.person is None なら何もせず早期 return（仕様書 §12.7）。
    """
    if user.person is None:
        return

    person = user.person
    with transaction.atomic():
        user.person = None
        user.save(update_fields=["person"])

        from actionlogs.models import ActionLog

        ActionLog.record(
            user=operator,
            action=ActionLogAction.UNLINK_USER_FROM_PERSON,
            content_object=user,
            object_repr=f"{user.username} ↮ Person({person.pk})",
            data={"person_id": str(person.pk)},
        )
