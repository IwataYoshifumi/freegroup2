import uuid

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from config.constants import DuplicateMergeReason


class PersonList(models.Model):
    """パーソンリスト（AccessList設計方針 v1.6 §4.6）。"""

    class EditScope(models.TextChoices):
        CREATOR_ONLY = "creator_only", _("作成者のみ")
        ALL_EDITORS = "all_editors", _("リスト編集者全員")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    access_list = models.ForeignKey(
        "permissions.AccessList",
        on_delete=models.PROTECT,
        related_name="person_lists",
    )
    edit_scope = models.CharField(
        max_length=30,
        choices=EditScope.choices,
        default=EditScope.ALL_EDITORS,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PersonQuerySet(models.QuerySet):
    """パーソン用カスタム QuerySet（仕様書 v1.6 §8.3.1）。"""

    def visible_for(self, user):
        """ユーザーが閲覧可能なパーソンに絞り込む（特権バイパス内包）。"""
        if not user or not user.is_authenticated:
            return self.none()
        from permissions.services import AccessListService
        accessible_ids = AccessListService.accessible_person_list_ids(user)
        return self.filter(status="active", person_list_id__in=accessible_ids)

    def bulk_create(self, objs, **kwargs):
        default_pl = None
        for obj in objs:
            if not getattr(obj, "person_list_id", None):
                if default_pl is None:
                    default_pl = get_or_create_default_person_list()
                obj.person_list = default_pl
        return super().bulk_create(objs, **kwargs)


def get_or_create_default_person_list():
    """デフォルトパーソンリストを取得または作成する。"""
    pl = PersonList.objects.first()
    if pl:
        return pl
    from django.contrib.auth import get_user_model
    from permissions.models import AccessList

    User = get_user_model()
    admin_user = User.objects.filter(is_superuser=True).first() or User.objects.first()
    if not admin_user:
        admin_user = User.objects.create_user(username="system_default_admin")
    acl = AccessList.objects.first()
    if not acl:
        acl = AccessList.objects.create(name="デフォルトアクセスリスト", created_by=admin_user)
    return PersonList.objects.create(
        name="デフォルトパーソンリスト", access_list=acl, created_by=admin_user
    )


class Person(models.Model):
    """人物DB（仕様書 v1.4.2 §4.5）。

    Person.primary_contact が代表 Contact の正本、Contact.status='primary' が派生情報
    （二重管理の設計趣旨は §4.5.2 参照）。
    """

    class Status(models.TextChoices):
        """Person のステータス（仕様書 §4.5.1 / 別表 C.11）。"""

        ACTIVE = "active", _("通常")
        MERGED = "merged", _("マージ済み")
        ARCHIVED = "archived", _("アーカイブ")

    objects = PersonQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person_list = models.ForeignKey(
        "persons.PersonList",
        on_delete=models.PROTECT,
        null=False,
        blank=False,
        related_name="persons",
    )
    primary_contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_from_set",
    )
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="persons_managed",
    )
    is_unsubscribed = models.BooleanField(
        default=False,
        help_text=(
            "メール配信停止フラグ（Unsubscribe レコードの派生情報、高速フィルタ用キャッシュ）。"
            "通常は Phase 3 で実装する unsubscribe_person() / cancel_unsubscribe() 経由で更新する。"
            "仕様書 §4.14.2 / §4.14.2.1"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("undo_merge", "マージ復元を実行できる"),
            ("merge_person", "Person マージを実行できる"),
            ("link_user", "User-Person 紐付けを設定できる"),
            ("view_all_persons", "全てのパーソンを閲覧できる"),
            ("edit_all_persons", "全てのパーソンを編集できる"),
        ]

    def full_clean(self, exclude=None, validate_unique=True):
        if not getattr(self, "person_list_id", None):
            self.person_list = get_or_create_default_person_list()
        super().full_clean(exclude=exclude, validate_unique=validate_unique)

    def save(self, *args, **kwargs):
        if not getattr(self, "person_list_id", None):
            self.person_list = get_or_create_default_person_list()
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        """表示用氏名。primary_contact、配下Contact、マージ先Personから安全に解決する。"""
        contact = self.effective_contact
        if contact:
            if contact.full_name:
                return contact.full_name
            name_parts = [p for p in [contact.last_name, contact.first_name] if p]
            if name_parts:
                return " ".join(name_parts)
            if contact.display_name:
                return contact.display_name
        if self.status == self.Status.MERGED and self.merged_into_id:
            try:
                surviving = self.get_surviving_person()
                if surviving and surviving.id != self.id:
                    return surviving.display_name
            except Exception:
                pass
        return "名称未設定の人物"

    @property
    def effective_person(self):
        """マージ済みの場合は統合先（surviving root）を返し、それ以外は自身を返す。"""
        if self.status == self.Status.MERGED and self.merged_into_id:
            try:
                return self.get_surviving_person()
            except Exception:
                pass
        return self

    @property
    def effective_contact(self):
        """有効なContact（primary_contact または最新Contact）。マージ済みの場合は統合先から取得。"""
        eff = self.effective_person
        if eff.primary_contact:
            return eff.primary_contact
        return eff.contact_set.order_by("-created_at").first()

    @property
    def effective_company(self):
        """所属会社（Companyインスタンス）。マージ先も探索。"""
        contact = self.effective_contact
        return contact.company if contact else None

    @property
    def effective_company_name(self):
        """会社名文字列。マージ先も探索。"""
        contact = self.effective_contact
        if not contact:
            return ""
        if contact.company:
            return contact.company.organization
        return contact.organization or ""

    @property
    def effective_department(self):
        """部署名文字列。マージ先も探索。"""
        contact = self.effective_contact
        return contact.department if contact else ""

    def __str__(self):
        return self.display_name

    # ------------------------------------------------------------------
    # インスタンスメソッド（仕様書 §10.4.1）
    # ------------------------------------------------------------------

    def transfer_relations_to(self, surviving_person):
        """自身（merged_person）に紐づく Deal / Activity 関連を surviving_person に付け替える。

        1. Deal.primary_person:
           案件の主担当パーソンが self の場合は surviving_person に更新。
        2. DealPerson:
           self に紐づく DealPerson を surviving_person へ移行。
           移行先の案件に既に surviving_person の DealPerson が存在する場合は重複を削除。
        3. ActivityPerson:
           self に紐づく ActivityPerson を surviving_person へ移行。
           移行先のアクティビティに既に surviving_person の ActivityPerson が存在する場合は、
           重複を防止するため一方を削除（必要に応じて role をマージ）。
        """
        from deals.models import Deal, DealPerson, PersonRole
        from activities.models import ActivityPerson

        # 1. Deal.primary_person の付け替え
        deals_as_primary = Deal.objects.filter(primary_person=self)
        for deal in deals_as_primary:
            deal.primary_person = surviving_person
            deal.save(update_fields=["primary_person", "updated_at"])
            # primary_person と同じ Person の DealPerson が存在する場合は削除（制約違反回避）
            DealPerson.objects.filter(deal=deal, person=surviving_person).delete()

        # 2. DealPerson の付け替え・重複削除
        for dp in list(self.deal_persons.select_related("deal").all()):
            deal = dp.deal
            if deal.primary_person_id == surviving_person.id:
                dp.delete()
                continue
            if DealPerson.objects.filter(deal=deal, person=surviving_person).exists():
                dp.delete()
            else:
                dp.person = surviving_person
                dp.save(update_fields=["person"])

        # 3. ActivityPerson の付け替え・重複削除・roleマージ
        role_priority = {
            PersonRole.DECISION_MAKER: 50,
            PersonRole.CONTACT_WINDOW: 40,
            PersonRole.TECHNICAL: 30,
            PersonRole.ATTENDEE: 20,
            PersonRole.OTHER: 10,
        }
        for ap in list(self.activity_persons.select_related("activity").all()):
            act = ap.activity
            target_ap = ActivityPerson.objects.filter(activity=act, person=surviving_person).first()
            if target_ap is not None:
                source_score = role_priority.get(ap.role, 0)
                target_score = role_priority.get(target_ap.role, 0)
                updates = []
                if source_score > target_score:
                    target_ap.role = ap.role
                    updates.append("role")
                if not target_ap.memo and ap.memo:
                    target_ap.memo = ap.memo
                    updates.append("memo")
                if updates:
                    target_ap.save(update_fields=updates)
                ap.delete()
            else:
                ap.person = surviving_person
                ap.save(update_fields=["person"])

    def mark_as_merged(self, surviving_person):
        """自身の状態遷移：merged 化（仕様書 §10.4.1）。

        [性質] 副作用あり（自身のフィールド更新のみ）
        [入力] surviving_person: Person（マージで残る側）
        [出力] None

        Contact 側のフィールド（status / person FK）には一切触らない（仕様書 §10.2）。
        Contact の付け替えは transfer_contacts_to() の責務。
        Deal / Activity 関連の付け替えは transfer_relations_to() で実行。
        """
        with transaction.atomic():
            self.status = self.Status.MERGED
            self.merged_into = surviving_person
            self.primary_contact = None
            self.save(
                update_fields=["status", "merged_into", "primary_contact", "updated_at"]
            )
            self.transfer_relations_to(surviving_person)

    def mark_as_archived(self):
        """自身の状態遷移：archived 化（仕様書 §10.4.1）。

        [性質] 副作用あり（自身のフィールド更新のみ）
        [入力] なし
        [出力] None
        [例外] ValueError（self.status が 'active' でない場合）

        Contact 側のフィールド（status / person FK）には一切触らない。
        """
        if self.status != self.Status.ACTIVE:
            raise ValueError(
                f"mark_as_archived() can only be called on active Person, "
                f"but Person {self.id} has status='{self.status}'"
            )
        self.status = self.Status.ARCHIVED
        self.save(update_fields=["status", "updated_at"])

    def mark_as_active(self):
        """自身の状態遷移：active 化（merged または archived からの復帰）。

        [性質] 副作用あり（自身のフィールド更新のみ）
        [入力] なし
        [出力] None
        [例外] ValueError（self.status が 'merged' でも 'archived' でもない場合）

        呼ばれる業務シナリオ：
          - Execute_Merge_Undo（C-3）から merged Person を active に戻す
          - 将来追加される archived 復元機能から archived Person を active に戻す

        primary_contact は触らない（mark_as_merged との非対称、X-6 指示書 §3.5）。
        primary_contact の再設定は呼び出し側が set_primary_contact() で別途実行する責務
        （仕様書 §10.4.3）。

        person.mark_as_merged() の対称メソッド。ActionLog 記録は本メソッドの責務外で、
        呼び出し元（Execute_Merge_Undo 等）が merge_log.record_undo_action(user) を別途
        呼ぶ（X-6 指示書 §3.6 / 仕様書 §10.6 / §10.8.4）。

        ガード方針（X-6 指示書 §3.7）：active な Person に対して呼ばれた場合は ValueError。
        archived → active の復帰時にサイレントに status を変えてしまうのを防ぎ、業務フロー
        のバグを早期検出する。
        """
        if self.status not in (self.Status.MERGED, self.Status.ARCHIVED):
            raise ValueError(
                f"mark_as_active() can only be called on merged or archived "
                f"Person, but Person {self.id} has status='{self.status}'"
            )
        self.status = self.Status.ACTIVE
        self.merged_into = None
        self.save(update_fields=["status", "merged_into", "updated_at"])

    def get_surviving_person(self):
        """自身の統合先（surviving root）の Person を返す。

        [性質] 純粋関数（DB 読み取りのみ）
        [入力] なし
        [出力] Person（merged_into を辿った root、マージされていなければ self）
        [例外] ValueError（merged_into の循環参照を検出した場合）
        """
        visited = set()
        root = self
        while root.merged_into is not None:
            if root.id in visited:
                raise ValueError(f"merged_into cycle detected at {root.id}")
            visited.add(root.id)
            root = root.merged_into
        return root

    def set_primary_contact(self, new_contact, old_primary_new_status="active"):
        """primary_contact 切り替え（派生情報の同期、仕様書 §10.4.3）。

        [性質] 副作用あり（自身と Contact の派生情報を同期更新）
        [入力] new_contact: Contact（self 配下の Contact、既に save 済み）
               old_primary_new_status: 'active' or 'inactive'（旧 primary の遷移先）
        [出力] None
        [例外] ValueError（new_contact が self 配下でない場合）

        前提条件：new_contact は self 配下の Contact。他 Person 配下の Contact を渡された場合は
        ValueError を上げる（v1.4.2 の実運用では発生しないケース。誤呼び出しを早期検知する）。
        """
        from contacts.models import Contact  # 循環 import を避けるため遅延 import

        if new_contact.person_id != self.id:
            raise ValueError(
                f"new_contact.person_id ({new_contact.person_id}) does not match "
                f"self.id ({self.id}). set_primary_contact() requires new_contact to be "
                f"already linked to this Person. Move the Contact under this Person "
                f"before calling set_primary_contact()."
            )

        with transaction.atomic():
            # Step 1: 旧 primary の status を old_primary_new_status に変更
            if (
                self.primary_contact_id is not None
                and self.primary_contact_id != new_contact.pk
            ):
                old = self.primary_contact
                old.status = old_primary_new_status
                old.save(update_fields=["status", "updated_at"])

            # Step 2: 新 primary の status を 'primary' に変更
            new_contact.status = Contact.Status.PRIMARY
            new_contact.save(update_fields=["status", "updated_at"])

            # Step 3 (FK 付け替え) は前提条件チェックで担保済みのため実装しない（A-2a 方針）

            # Step 4: self.primary_contact = new_contact に更新
            self.primary_contact = new_contact
            self.save(update_fields=["primary_contact", "updated_at"])

    def transfer_contacts_to(self, surviving_person, merge_reason):
        """自身（merged_person）のコンタクト群を surviving_person に引き渡す（仕様書 §10.4.1 / §9.4）。

        [性質] 副作用あり（DB書込：自身配下の Contact の status / previous_status /
               previous_person / person FK を更新。Contact の他フィールドは触らない）
        [入力] surviving_person: Person（マージで残る side）
               merge_reason: list[str]（DuplicateMergeReason の value のリスト、
                   D-4d-1 第 4 弾で複数選択化に対応。空リストは想定しない）
        [出力] None
        [前提] 呼び出し元の `transaction.atomic()` 内で実行されること（X-4 指示書 §5.5 / §9.3）。
               本メソッドでは atomic を切らない（呼び出し元の責務）。
        [仕様書] §10.4.1 / §9.4 / 別添 PDF「マージ前後のコンタクトのステータス等まとめ.pdf」
                 の Excute_Merge_Only 列に従って状態遷移。

        処理内容（merged 側 Contact 群を PDF 表通りに変換）：
          - 元 primary（最大 1 件）：
              status → INACTIVE（ただし ADDITIONAL_ROLE が merge_reason に含まれるときは
              ACTIVE、§9.4.3。D-4d-1 第 4 弾で in 比較に変更）
              previous_status='primary' / previous_person=self / person=surviving_person
          - 元 active 群：status は 'active' のまま（変更なし）
              previous_status='active' / previous_person=self / person=surviving_person
          - 元 inactive 群：status は 'inactive' のまま（変更なし）
              previous_status='inactive' / previous_person=self / person=surviving_person

        責務範囲外（本メソッドで触らないもの）：
          - サバイブ側 Contact のフィールド（§9.4.1 previous_* 不変原則）
          - ContactFieldConfidence（§10.5.1 / §10.6 の責務範囲）
          - self.primary_contact / self.status（mark_as_merged() の責務、§10.4.1）
          - ActionLog 記録（呼び出し元 Execute_Merge_* の責務）

        実装メモ（partial unique constraint 配慮、X-4 指示書 §6.3）：
          Contact の `UniqueConstraint(person, where status='primary')` 違反を避けるため、
          元 primary は status と person を **1 回の save() で同時更新** する。
          person FK を先に付け替えると surviving_person 側に瞬間的に primary が 2 つ存在
          する状態が発生し、IntegrityError になる。
        """
        from contacts.models import Contact  # 循環 import を避けるため遅延 import

        # 元 primary の遷移先 status を merge_reason から決定（PDF 表 / §9.4.3）。
        # ADDITIONAL_ROLE が含まれていれば ACTIVE、それ以外は INACTIVE
        # （D-4d-1 第 4 弾で MultipleChoiceField 化により単一値 == から in 比較へ）。
        if DuplicateMergeReason.ADDITIONAL_ROLE in merge_reason:
            new_primary_status = Contact.Status.ACTIVE
        else:
            new_primary_status = Contact.Status.INACTIVE

        # 元 primary（partial unique constraint により最大 1 件）の付け替え。
        # status と person を 1 回の save() で同時更新（partial unique 違反回避）。
        primary = self.contact_set.filter(status=Contact.Status.PRIMARY).first()
        if primary is not None:
            primary.previous_person = self
            primary.previous_status = Contact.Status.PRIMARY
            primary.status = new_primary_status
            primary.person = surviving_person
            primary.save(
                update_fields=[
                    "status",
                    "previous_status",
                    "previous_person",
                    "person",
                    "updated_at",
                ]
            )

        # 元 active 群の付け替え（status は 'active' のまま）。
        for contact in self.contact_set.filter(status=Contact.Status.ACTIVE):
            contact.previous_person = self
            contact.previous_status = Contact.Status.ACTIVE
            contact.person = surviving_person
            contact.save(
                update_fields=[
                    "previous_status",
                    "previous_person",
                    "person",
                    "updated_at",
                ]
            )

        # 元 inactive 群の付け替え（status は 'inactive' のまま）。
        for contact in self.contact_set.filter(status=Contact.Status.INACTIVE):
            contact.previous_person = self
            contact.previous_status = Contact.Status.INACTIVE
            contact.person = surviving_person
            contact.save(
                update_fields=[
                    "previous_status",
                    "previous_person",
                    "person",
                    "updated_at",
                ]
            )

    def get_active_contacts(self):
        """status='active' の Contact 一覧（QuerySet）を返す（仕様書 §10.4.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] なし
        [出力] QuerySet[Contact]（status='active' のもの。'primary' は含まない）
        """
        return self.contact_set.filter(status="active")

    def get_inactive_contacts(self):
        """status='inactive' の Contact 一覧（QuerySet）を返す（仕様書 §10.4.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] なし
        [出力] QuerySet[Contact]（status='inactive' のもの）
        """
        return self.contact_set.filter(status="inactive")

    @property
    def linked_user(self):
        """User との紐付け取得。未紐付け時は None（仕様書 §12.3）。

        [性質] 準関数（DB 読み取り、副作用なし）
        [入力] なし
        [出力] CustomUser | None

        循環依存回避のため、CustomUser を直接 import せず ObjectDoesNotExist で catch。
        accounts.CustomUser に依存しないので persons アプリ単体でテスト可能。
        コード君は person.user への直接アクセスを避け、本プロパティ経由で参照すること。
        """
        try:
            return self.user
        except ObjectDoesNotExist:
            return None

    # ------------------------------------------------------------------
    # 配信停止（v1.6 §4.14.2 / §9.5 / §9.8、Phase 5 で追加）
    # ------------------------------------------------------------------

    def recompute_unsubscribed_state(self):
        """is_unsubscribed を Unsubscribe レコードの正本から再計算する（§4.14.2 / §4.14.2.1）。

        [性質] 副作用あり（DB 書込：自身の is_unsubscribed を 1 件更新する場合あり）
        [入力] なし
        [出力] None

        正本：アクティブな（cancelled_at が NULL の）Unsubscribe レコードが 1 件以上あれば
              is_unsubscribed=True、なければ False。

        通常運用では `unsubscribe_person()` / `cancel_unsubscribe()` 経由で bulk update
        により同期されるため、本メソッドは呼ばれない。例外的経路（Django admin から
        個別 Unsubscribe レコードを cancelled_at 更新で解除する等）でのみ明示呼び出しする
        （仕様書 §4.14.2 / §9.3.1）。

        【注意】同一人物ユニット伝播は行わない（self 単独のフィールドのみ整合）。
        ユニット全員に揃えたい場合はサービス関数経由（unsubscribe_person /
        cancel_unsubscribe）を使う。
        """
        from mailings.models import Unsubscribe  # 循環 import 回避のため遅延

        has_active = Unsubscribe.objects.filter(
            person=self, cancelled_at__isnull=True
        ).exists()
        if self.is_unsubscribed != has_active:
            self.is_unsubscribed = has_active
            self.save(update_fields=["is_unsubscribed", "updated_at"])

    def set_unsubscribed_by_admin(self, user, note=""):
        """管理者代行による配信停止操作（§9.8 / §4.14.2）。

        [性質] 副作用あり（DB 書込：ユニット全員に Unsubscribe + is_unsubscribed
               同期 + ActionLog 1 件）
        [入力] user: CustomUser（操作した管理者、ActionLog の actor / Unsubscribe の
               cancelled_by 候補ではなく、source='manual' の意味記録 + ActionLog 用）
               note: str（補足説明、Unsubscribe.note と ActionLog.note に記録）
        [出力] None
        [例外] ValueError（self.primary_contact が NULL、または email が空の場合：
               source_email を確定できないため）

        内部処理：
          1. unsubscribe_person(target_person=self, source='manual',
             source_email=self.primary_contact.email, user=user, note=note)
             でユニット全員に Unsubscribe レコード作成 + is_unsubscribed=True 同期
          2. record_unsubscribe_by_admin_action(user, self, note) で ActionLog 記録
             （v1.4.2 §4.11.3 record_*_action パターン踏襲）
        """
        from mailings.services.audit import (  # 循環 import 回避のため遅延
            record_unsubscribe_by_admin_action,
        )
        from mailings.services.unsubscribe import unsubscribe_person

        if self.primary_contact is None or not self.primary_contact.email:
            raise ValueError(
                f"Person {self.id} has no primary_contact email; "
                "set_unsubscribed_by_admin requires a source_email."
            )

        unsubscribe_person(
            target_person=self,
            source="manual",
            source_email=self.primary_contact.email,
            user=user,
            note=note,
        )
        record_unsubscribe_by_admin_action(user=user, person=self, note=note)

    # ------------------------------------------------------------------
    # クラスメソッド（仕様書 §10.4.2）
    # ------------------------------------------------------------------

    @classmethod
    def get_active(cls):
        """status='active' の Person 一覧（QuerySet）を返す（仕様書 §10.4.2）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] なし
        [出力] QuerySet[Person]（status='active' のもの。merged / archived は含まない）
        """
        return cls.objects.filter(status=cls.Status.ACTIVE)

    @classmethod
    def get_archived(cls):
        """status='archived' の Person 一覧（QuerySet）を返す（仕様書 §10.4.2）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] なし
        [出力] QuerySet[Person]（status='archived' のもの。active / merged は含まない）
        """
        return cls.objects.filter(status=cls.Status.ARCHIVED)
