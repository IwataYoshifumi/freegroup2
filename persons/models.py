import uuid

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from config.constants import DuplicateMergeReason


class Person(models.Model):
    """人物DB（仕様書 v1.4.2 §4.5）。

    Person.primary_contact が代表 Contact の正本、Contact.status='primary' が派生情報
    （二重管理の設計趣旨は §4.5.2 参照）。
    """

    class Status(models.TextChoices):
        """Person のステータス（仕様書 §4.5.1 / 別表 C.11）。"""

        ACTIVE = "active", _("通常")
        MERGED = "merged", _("統合済み")
        ARCHIVED = "archived", _("アーカイブ")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("undo_merge", "マージ復元を実行できる"),
            ("merge_person", "Person マージを実行できる"),
            ("link_user", "User-Person 紐付けを設定できる"),
        ]

    def __str__(self):
        contact = self.contact_set.order_by("-created_at").first()
        if contact and contact.full_name:
            return contact.full_name
        return f"Person {self.id}"

    # ------------------------------------------------------------------
    # インスタンスメソッド（仕様書 §10.4.1）
    # ------------------------------------------------------------------

    def mark_as_merged(self, surviving_person):
        """自身の状態遷移：merged 化（仕様書 §10.4.1）。

        [性質] 副作用あり（自身のフィールド更新のみ）
        [入力] surviving_person: Person（マージで残る側）
        [出力] None

        Contact 側のフィールド（status / person FK）には一切触らない（仕様書 §10.2）。
        Contact の付け替えは transfer_contacts_to() の責務。
        """
        with transaction.atomic():
            self.status = self.Status.MERGED
            self.merged_into = surviving_person
            self.primary_contact = None
            self.save(
                update_fields=["status", "merged_into", "primary_contact", "updated_at"]
            )

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
