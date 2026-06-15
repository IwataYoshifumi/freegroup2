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


def _normalize_email(value):
    """[性質] 純関数（DB 操作なし・副作用なし）／メール比較用に strip + 小文字化する。

    None / 空は "" を返す。
    """
    return (value or "").strip().lower()


def is_self_link_email_match(user, person):
    """本人フロー紐付けで User.email と Person.primary_contact.email が一致するか判定する。

    [性質] 純関数（DB 読み取りは person.primary_contact 参照のみ・副作用なし）
    [入力] user: CustomUser / person: Person
    [出力] bool（両者を strip + 小文字化して完全一致なら True）

    Person 側メールが空（primary_contact 無し / email 空）は一致しない（False）。
    本人フロー（operator == user）専用のガード判定。他人紐付け経路では使わない。
    """
    primary = getattr(person, "primary_contact", None)
    person_email = _normalize_email(getattr(primary, "email", "") if primary else "")
    if not person_email:
        return False
    return _normalize_email(user.email) == person_email


def link_user_to_person(operator, user, person):
    """User と Person を紐付ける（仕様書 §12.7 + 本人フロー メール一致ガード）。

    [性質] 副作用あり（DB 書込：user.person 更新 + ActionLog 記録、transaction.atomic 内）
    [入力] operator: 操作実行者の CustomUser（ActionLog.user に記録）
           user: 紐付け対象の CustomUser
           person: 紐付け先の Person
    [出力] None
    [例外] ValidationError（user 側 / Person 側の既存紐付け、または本人フローでメール不一致）

    [権限チェック責務]
    本関数は権限チェックを行わない。呼び出し側 (View) で以下を保証すること:
    - operator == user (本人) または
    - operator.has_perm('accounts.link_user_to_person') (管理者権限)

    [両側ガード] OneToOne 制約違反防止のため、user 側・person 側両方をチェック。
    person.linked_user は Phase 1 のプロパティ経由（person.user 直接参照禁止、仕様書 §12.3）。

    [本人フロー メール一致ガード] operator == user のときだけ、User.email と
    Person.primary_contact.email が一致しないと紐付けを拒否する（最終砦、新規要件）。
    他人紐付け経路（operator != user・link_user_to_person 権限保持者）はガード対象外。
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

    if operator == user and not is_self_link_email_match(user, person):
        raise ValidationError(
            "メールアドレスが一致しないため紐付けできません。"
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


def can_merge_person(operator, person):
    """Person をマージできる権限があるか判定する（仕様書 §13.1、5' ルール拡張版）。

    [性質] 準関数（DB 読み取り：person.linked_user / managed_by_id の参照のみ、副作用なし）
    [入力] operator: 操作実行者の CustomUser
           person: 判定対象の Person
    [出力] bool

    判定ルール（仕様書 §13.1）：
      - User 未紐付け Person: 通常権限で判定（True）
      - 紐付き User 本人が現職: True
      - 退職者の Person: managed_by の現職者が代行可
      - その他: False

    呼び出し側で `_check_merge_permission` / `_check_different_person_permission` から
    両 Person について本関数を呼び、両方 True なら通過。
    person.linked_user は Phase 1 のプロパティ経由（person.user 直接参照禁止、仕様書 §12.3）。
    """
    linked_user = person.linked_user

    if linked_user is None:
        return True
    if linked_user.is_active and linked_user == operator:
        return True
    if not linked_user.is_active and person.managed_by_id == operator.id:
        return True
    return False


def retire_user(user, successor):
    """退職処理。managed_by を後継者に一括引き継ぎ（仕様書 §12.6）。

    [性質] 副作用あり（DB 書込：persons_managed / contacts_managed 更新 + user.is_active=False + ActionLog 記録、transaction.atomic 内）
    [入力] user: 退職させる CustomUser
           successor: 後継者となる現職の CustomUser
    [出力] None
    [例外] ValidationError（退職者と後継者が同じ / 後継者が非アクティブ）

    user.is_active = False にする。User.person 紐付けは維持（履歴として）。
    apply_role() は呼ばない（Role / Group は触らない）。
    """
    if user.pk == successor.pk:
        raise ValidationError("退職者と後継者が同じです")
    if not successor.is_active:
        raise ValidationError("後継者は現職者でなければなりません")

    with transaction.atomic():
        user.persons_managed.update(managed_by=successor)
        user.contacts_managed.update(managed_by=successor)
        user.is_active = False
        user.save(update_fields=["is_active"])

        from actionlogs.models import ActionLog

        ActionLog.record(
            user=successor,
            action=ActionLogAction.RETIRE_USER,
            content_object=user,
            object_repr=f"{user.username} → {successor.username}",
            data={"retired_user_id": str(user.pk), "successor_id": str(successor.pk)},
        )


def get_excluded_persons_for_user_linked(person):
    """User 紐付き Person の場合、他の User 紐付き Person を除外対象として返す（仕様書 §13.5）。

    [性質] 準関数（DB 読み取り：Person を user__isnull=False で絞り込み、副作用なし）
    [入力] person: 判定対象の Person
    [出力] list[Person]（除外対象。person が未紐付きなら空リスト）

    DuplicateCandidate 生成時に excluded_persons へ加えることで、両方 User 紐付き
    Person のペアが候補に上がらないようにする（多重防衛、仕様書 §13.6）。

    ⚠️ パフォーマンス注意: 全社員数が増えると返り値も増える。1000 件超で
    find_duplicate_contacts のクエリパフォーマンスが劣化する場合、excluded_persons
    を ID リストではなく Subquery で渡すよう呼び出し側を変更する検討が必要。
    """
    linked_user = person.linked_user
    if linked_user is None:
        return []
    from persons.models import Person

    return list(
        Person.objects.filter(user__isnull=False)
        .filter(status=Person.Status.ACTIVE)
        .exclude(pk=person.pk)
    )
