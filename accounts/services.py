from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.functions import Lower, Trim
from django.urls import reverse
from django.utils.text import slugify

from contacts.models import Contact
from persons.models import Person

from .constants import ActionLogAction, ADMIN_ROLE_CODE, PersonLinkStatus


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


def assign_role_to_user(operator, user, role):
    """本体 UI からユーザにロールを割り当てる（宿題F・ロール割り当て UI）。

    [性質] 副作用あり（role が実際に変わったときのみ apply_role + ActionLog 記録、
            transaction.atomic 内）
    [入力] operator: 操作実行者の CustomUser（ActionLog.user に記録）
           user: ロール変更対象の CustomUser
           role: 付与する Role または None
    [出力] bool（実際に変更したら True / 現在と同じ role で何もしなかったら False）

    [発火ガード] CustomUserAdmin.save_model と同じ考え方で、現在の role と新 role が
    異なるときだけ apply_role を呼ぶ。同一 role で apply_role を呼ぶと groups.set() の
    全置換で手動追加 Group がサイレントに消えるため（apply_role の docstring 参照）。

    [ActionLog] role が実際に変わったときのみ ASSIGN_ROLE を記録する
    （link_user_to_person / retire_user と同じく services 側で ActionLog.record を呼ぶ流儀）。

    [権限チェック責務] 本関数は権限・自爆防止チェックを行わない。呼び出し側 (View) で
    accounts.assign_role_to_user の保持と「対象 == 操作者本人の禁止」を保証すること。

    [admin 両方向ガード（最終砦）] 本体業務 UI からは admin の任免を行わせない。
    - 対象 user の現ロールが admin → 拒否（admin からの降格・付け替えは Django Admin で）
    - 変更先 role が admin → 拒否（本体 UI で誰かを admin に昇格させない）
    UI 層（導線非表示・select から admin 除外）と二重防衛。直 URL POST・他経路もここで弾く。
    [例外] ValidationError（現ロール admin / 変更先 admin の場合）
    """
    old_role = user.role
    old_role_id = user.role_id
    new_role_id = role.id if role is not None else None

    if old_role is not None and old_role.code == ADMIN_ROLE_CODE:
        raise ValidationError(
            "管理者ロールのユーザのロールは本体画面から変更できません"
            "（Django 管理サイトで行ってください）"
        )
    if role is not None and role.code == ADMIN_ROLE_CODE:
        raise ValidationError(
            "本体画面から管理者ロールへは変更できません"
            "（Django 管理サイトで行ってください）"
        )

    if old_role_id == new_role_id:
        return False

    with transaction.atomic():
        apply_role(user, role)

        from actionlogs.models import ActionLog

        ActionLog.record(
            user=operator,
            action=ActionLogAction.ASSIGN_ROLE,
            content_object=user,
            object_repr=f"{user.username}: {old_role or '-'} → {role or '-'}",
            data={
                "user_id": user.pk,
                "old_role_id": old_role_id,
                "new_role_id": new_role_id,
                "old_role_code": old_role.code if old_role else None,
                "new_role_code": role.code if role else None,
            },
        )
    return True


def role_holder_count(role):
    """[性質] 準関数（DB 読み取り）／この Role を user.role に持つ User 数を返す。

    [入力] role: Role
    [出力] int（退職者 is_active=False も含めてカウント。割り当て中判定・削除可否に使う）
    """
    return role.users.count()


def generate_role_code(name):
    """name から一意な Role.code を自動採番する（業務UI ロール作成）。

    [性質] 準関数（DB 読み取りで一意性を確認・副作用なし）
    [入力] name: str（ロール表示名）
    [出力] str（slug 化した一意 code。予約語 'admin' は使わない）

    日本語等で slug が空になる場合は 'role' を基底にする。衝突時は連番で回避する。
    """
    from .models import Role

    base = slugify(name)[:24] or "role"
    if base == ADMIN_ROLE_CODE:
        base = "role"
    code = base
    i = 2
    while code == ADMIN_ROLE_CODE or Role.objects.filter(code=code).exists():
        code = f"{base}-{i}"
        i += 1
    return code


def create_role(operator, name, sort_order=0, memo=""):
    """新規 Role を作成する（業務UI ロール CRUD）。

    [性質] 副作用あり（DB 書込：Role 作成 + ActionLog 記録）
    [入力] operator: 操作実行者の CustomUser
           name: str（表示名・一意）/ sort_order: int / memo: str
           code は name から自動採番（管理者に入力させない・予約語 admin 不可）
    [出力] Role（作成したインスタンス）
    [例外] ValidationError（名前空 / 同名既存）
    """
    from .models import Role

    name = (name or "").strip()
    if not name:
        raise ValidationError("ロール名を入力してください")
    if Role.objects.filter(name=name).exists():
        raise ValidationError("同名のロールが既に存在します")

    code = generate_role_code(name)
    with transaction.atomic():
        role = Role.objects.create(
            name=name, code=code, sort_order=sort_order or 0, memo=memo or ""
        )

        from actionlogs.models import ActionLog

        ActionLog.record(
            user=operator,
            action=ActionLogAction.ROLE_CREATE,
            content_object=role,
            object_repr=f"{role.name} ({role.code})",
            data={"role_id": role.pk, "code": role.code},
        )
    return role


def update_role(operator, role, name, default_groups, sort_order=None, memo=None):
    """Role の表示名・default_groups を更新し、変更時は全保持者へ即再適用する（業務UI ロール CRUD）。

    [性質] 副作用あり（DB 書込：Role 更新 + default_groups 変更時 apply_role 再適用 +
            ActionLog 記録、すべて transaction.atomic 内）
    [入力] operator: 操作実行者 / role: 更新対象 Role
           name: str（表示名）/ default_groups: iterable[Group]（新しい既定グループ集合）
           sort_order / memo: 任意（None なら据え置き）
    [出力] Role
    [例外] ValidationError（admin ロール / 名前空 / 同名既存）

    [admin ガード] code=='admin' は read-only（編集不可）。直 URL POST もここで弾く。

    [default_groups 即再適用] default_groups が変わった場合のみ、この Role を持つ全 User
    （退職者 is_active=False も含む）に apply_role を呼び直し groups を新 default_groups へ揃える。
    admin ロールの保持者は安全のため再適用対象から除外する（role 自体が admin なら上で弾く）。

    [ActionLog] ユーザ単位では撒かず、「ロール定義変更（ロール名＋影響ユーザ数）」を1件記録する。

    [戻り値] dict（"role": 更新後 Role / "groups_changed": bool / "affected_count": int）。
    affected_count は default_groups 変更時に再適用した保持者数（退職者含む・admin 除外。
    変更が無ければ 0）。View が結果メッセージで母数を二重定義しないために Service が返す。
    """
    from .models import Role

    if role.code == ADMIN_ROLE_CODE:
        raise ValidationError("管理者ロールは編集できません")

    name = (name or "").strip()
    if not name:
        raise ValidationError("ロール名を入力してください")
    if Role.objects.filter(name=name).exclude(pk=role.pk).exists():
        raise ValidationError("同名のロールが既に存在します")

    old_group_ids = set(role.default_groups.values_list("id", flat=True))
    new_groups = list(default_groups)
    new_group_ids = {g.id for g in new_groups}
    groups_changed = old_group_ids != new_group_ids

    with transaction.atomic():
        role.name = name
        if sort_order is not None:
            role.sort_order = sort_order
        if memo is not None:
            role.memo = memo
        role.save(update_fields=["name", "sort_order", "memo", "updated_at"])
        role.default_groups.set(new_groups)

        affected = 0
        if groups_changed:
            # 退職者も含む（is_active でフィルタしない）。admin 保持者は安全のため除外。
            holders = role.users.exclude(role__code=ADMIN_ROLE_CODE)
            for user in holders:
                apply_role(user, role)
                affected += 1

        from actionlogs.models import ActionLog

        ActionLog.record(
            user=operator,
            action=ActionLogAction.ROLE_UPDATE,
            content_object=role,
            object_repr=f"{role.name} ({role.code})",
            data={
                "role_id": role.pk,
                "groups_changed": groups_changed,
                "default_group_names": sorted(g.name for g in new_groups),
                "affected_user_count": affected,
            },
        )
    return {"role": role, "groups_changed": groups_changed, "affected_count": affected}


def delete_role(operator, role):
    """Role を削除する（業務UI ロール CRUD）。

    [性質] 副作用あり（DB 書込：Role 削除 + ActionLog 記録、transaction.atomic 内）
    [入力] operator: 操作実行者 / role: 削除対象 Role
    [出力] None
    [例外] ValidationError（admin ロール / 保持者が1人以上）

    [admin ガード] code=='admin' は削除不可。
    [割り当て中ガード（最終砦）] この Role を持つ User が1人でも居れば（退職者含む）拒否。
    直 URL POST もここで弾く（View だけで止めない）。
    """
    if role.code == ADMIN_ROLE_CODE:
        raise ValidationError("管理者ロールは削除できません")

    holder_count = role.users.count()
    if holder_count > 0:
        raise ValidationError(
            f"このロールは {holder_count} 人（退職者含む）に割り当てられています。"
            "先に対象者のロールを変更してください"
        )

    with transaction.atomic():
        object_repr = f"{role.name} ({role.code})"
        role_id = role.pk
        role.delete()

        from actionlogs.models import ActionLog

        ActionLog.record(
            user=operator,
            action=ActionLogAction.ROLE_DELETE,
            content_object=None,
            object_repr=object_repr,
            data={"role_id": role_id},
        )


def _normalize_email(value):
    """[性質] 純関数（DB 操作なし・副作用なし）／メール比較用に strip + 小文字化する。

    None / 空は "" を返す。
    """
    return (value or "").strip().lower()


# 本人同定（self-link）で email 一致とみなす Contact の status 集合。
# 入口（ホーム抽出 self_link_candidate_contacts）と出口（確認画面ガード
# is_self_link_email_match）が同一の集合を参照し、照合基準のズレを防ぐ。
# 本人同定の根拠は Person の代表名刺 primary_contact（= status=primary、Person につき1枚）
# のメール一致のみに限定する。副(active)Contact は集約経緯が不確かで誤同定リスクがあるため
# 自動同定の根拠にしない（副一致でしか拾えない本人は管理者の手動紐づけ operator≠user で救済）。
SELF_LINK_MATCH_CONTACT_STATUSES = (Contact.Status.PRIMARY,)


def self_link_candidate_contacts(user):
    """user.email に一致する本人紐付け候補 Contact の QuerySet を返す（入口：ホーム抽出用）。

    [性質] 準関数（DB 読み取りのみ・副作用なし）
    [入力] user: CustomUser
    [出力] Contact の QuerySet（status が primary（代表名刺）・email 正規化一致・
            Person が active かつ未紐づけ）。user.email が空なら空 QuerySet。

    出口 is_self_link_email_match と同一基準（SELF_LINK_MATCH_CONTACT_STATUSES＝primary のみ ＋
    Lower(Trim(email)) 正規化）で判定し、入口・出口の照合基準のズレを防ぐ。
    """
    normalized = _normalize_email(user.email)
    if not normalized:
        return Contact.objects.none()
    return (
        Contact.objects.filter(
            status__in=SELF_LINK_MATCH_CONTACT_STATUSES,
            person__status=Person.Status.ACTIVE,
            person__user__isnull=True,
        )
        .annotate(_norm_email=Lower(Trim("email")))
        .filter(_norm_email=normalized)
        .select_related("person")
    )


def self_link_person_list_url(user):
    """本人紐付けフローの着地URL（人物一覧へメールプリフィル）を組み立てて返す。

    [性質] 純関数（reverse + urlencode のみ・DB 操作なし・副作用なし）
    [入力] user: CustomUser
    [出力] str（persons:person_list ＋ email / searched=1 / status=active のクエリ付き URL）

    StartLinkFlowView と profile/ホームの複数候補誘導が同一URLに着地するための共通化
    （URL 組み立ての二重化を防ぐ）。status は active（Person の状態）。本人同定の
    primary_contact メール一致は person_list の email 検索（primary_contact 経由）が
    担保するため status=primary は使わない（person_list では無効値＝0件化する）。
    """
    params = urlencode([
        ("email", user.email),
        ("searched", "1"),
        ("status", "active"),
    ])
    return f"{reverse('persons:person_list')}?{params}"


def self_link_alert_context(user):
    """紐付け候補アラートの状態とコンテキストを返す（ホーム／プロフィール共通）。

    [性質] 準関数（DB 読み取りのみ・self_link_candidate_contacts を呼ぶだけ・副作用なし）
    [入力] user: CustomUser
    [出力] dict（person_link_status: PersonLinkStatus の値／
            person_link_candidates: 候補 Contact のリスト／
            person_link_select_url: 複数候補時の誘導先＝人物一覧メールプリフィルURL）

    distinct Person 件数で状態を決める：>1=複数候補（本人を選んで紐付け）／==1=単一候補（紐付け）／
    0=候補なし（プロフィールで名刺アップロード誘導）。ホーム home/views.py と入口・基準を
    一本化し、判定の二重化を防ぐ（_link_candidate_alert.html partial がこの dict を描画）。
    """
    candidates = list(self_link_candidate_contacts(user))
    distinct_persons = {c.person_id for c in candidates}
    if len(distinct_persons) > 1:
        status = PersonLinkStatus.MULTIPLE_CANDIDATES_NEED_MERGE
    elif distinct_persons:
        status = PersonLinkStatus.SINGLE_CANDIDATE
    else:
        status = PersonLinkStatus.NO_CANDIDATE
    return {
        "person_link_status": status,
        "person_link_candidates": candidates,
        # 複数候補時の誘導先（StartLinkFlow と同一の人物一覧メールプリフィルURL）。
        "person_link_select_url": self_link_person_list_url(user),
    }


def is_self_link_email_match(user, person):
    """本人フロー紐付けで User.email が Person 配下の対象 Contact と一致するか判定する。

    [性質] 準関数（DB 読み取り：person.contact_set を参照・副作用なし）
    [入力] user: CustomUser / person: Person
    [出力] bool（status=primary（代表名刺）の Contact と email 正規化一致なら True）

    入口 self_link_candidate_contacts と同一基準（SELF_LINK_MATCH_CONTACT_STATUSES＝primary のみ ＋
    Lower(Trim(email)) 正規化）。本人同定は代表名刺 primary_contact のメール一致のみを根拠とする。
    primary_contact が null／email 空なら一致なし（False）。
    本人フロー（operator == user）専用のガード判定。他人紐付け経路では使わない。
    """
    normalized = _normalize_email(user.email)
    if not normalized:
        return False
    return (
        person.contact_set.filter(status__in=SELF_LINK_MATCH_CONTACT_STATUSES)
        .annotate(_norm_email=Lower(Trim("email")))
        .filter(_norm_email=normalized)
        .exists()
    )


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

    # 入口（self_link_candidate_contacts）は active 限定。出口も active に限定し、
    # 非active（MERGED/ARCHIVED 等）Person への person_id 直叩き紐付けを防ぐ。
    if person.status != Person.Status.ACTIVE:
        raise ValidationError(
            "このパーソンは通常状態（active）ではないため紐付けできません。"
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
