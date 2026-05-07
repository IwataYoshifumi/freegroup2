"""重複検出・人物統合の補助レコード（仕様書 v1.4.2 §4.7 / §4.8）。

DuplicateCandidate：重複候補ペアの DB レコード。
PersonMergeLog：マージ実行・復元の履歴ログ（status='undoable'/'undone'/'locked'）。
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DuplicateCandidate(models.Model):
    """重複候補DB（仕様書 v1.4.2 §4.7）。

    2 つの Person の組み合わせを記録する。person_a / person_b は ID 順で正規化される。
    review_status='pending' の場合のみ partial unique constraint が効く（再マージ可能のため）。
    match_reason / matched_fields は v1.4.2 で削除（A-1c 指示書 §7.1）。
    """

    class Rank(models.TextChoices):
        """ランク（仕様書 §14.4 / 別表 C.4）。"""

        EXACT_MATCH = "exact_match", _("完全一致")
        POSSIBLE_HIGH = "possible_high", _("高確信度")
        POSSIBLE_MID = "possible_mid", _("中確信度")
        POSSIBLE_LOW = "possible_low", _("低確信度")
        NONE = "none", _("該当なし")

    class ReviewStatus(models.TextChoices):
        """レビュー状態（仕様書 §14.4 / 別表 C.5）。"""

        PENDING = "pending", _("判定待ち")
        MERGED = "merged", _("マージ済み")
        DIFFERENT_PERSON = "different_person", _("別人確定")
        INVALIDATED = "invalidated", _("無効化")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group_id = models.UUIDField(null=True, blank=True)
    person_a = models.ForeignKey(
        "persons.Person",
        on_delete=models.CASCADE,
        related_name="+",
    )
    person_b = models.ForeignKey(
        "persons.Person",
        on_delete=models.CASCADE,
        related_name="+",
    )
    score = models.IntegerField()
    rank = models.CharField(max_length=20, choices=Rank.choices)
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    review_result = models.JSONField(default=list)
    note = models.TextField(blank=True, default="")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["person_a", "person_b"],
                condition=Q(review_status="pending"),
                name="unique_pending_person_pair",
            ),
        ]

    def __str__(self):
        return f"{self.person_a_id} ↔ {self.person_b_id} ({self.rank}/{self.review_status})"

    # ------------------------------------------------------------------
    # クラスメソッド（仕様書 §10.7.1）
    # ------------------------------------------------------------------

    @classmethod
    def _get_by_status(cls, contact, status):
        """contact の Person を起点に review_status で絞り込んだ候補を返す内部ヘルパー。

        [性質] 準関数（DB 読み取りのみ）
        [入力] contact: Contact / status: str
        [出力] QuerySet[DuplicateCandidate]

        person_a / person_b の OR 検索で、Person を起点とする候補を取得する。
        """
        person = contact.person
        return cls.objects.filter(
            Q(person_a=person) | Q(person_b=person),
            review_status=status,
        )

    @classmethod
    def get_pending(cls, contact):
        """contact が紐づく Person の pending 候補を取得（仕様書 §10.7.1 / §10.7.3）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] contact: Contact
        [出力] QuerySet[DuplicateCandidate]（review_status='pending' のもの）
        """
        return cls._get_by_status(contact, cls.ReviewStatus.PENDING)

    @classmethod
    def get_merged(cls, contact):
        """contact が紐づく Person の merged 候補を取得（仕様書 §10.7.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] contact: Contact
        [出力] QuerySet[DuplicateCandidate]（review_status='merged' のもの。マージ履歴表示用）
        """
        return cls._get_by_status(contact, cls.ReviewStatus.MERGED)

    @classmethod
    def get_different_person(cls, contact):
        """contact が紐づく Person の different_person 候補を取得（仕様書 §10.7.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] contact: Contact
        [出力] QuerySet[DuplicateCandidate]（review_status='different_person' のもの）
        """
        return cls._get_by_status(contact, cls.ReviewStatus.DIFFERENT_PERSON)

    @classmethod
    def get_invalidated(cls, contact):
        """contact が紐づく Person の invalidated 候補を取得（仕様書 §10.7.1 / §10.7.4）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] contact: Contact
        [出力] QuerySet[DuplicateCandidate]（review_status='invalidated' のもの。開発・デバッグ用）
        """
        return cls._get_by_status(contact, cls.ReviewStatus.INVALIDATED)

    @classmethod
    def has_duplicates(cls, contact, status):
        """指定 status の候補が存在するかどうかの判定（仕様書 §10.7.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] contact: Contact / status: str（review_status の値）
        [出力] bool

        実装メモ：SQLite 3.51.2 の planner bug 回避（partial unique index +
        OR フィルタの組み合わせで internal query planner error が発生するため、
        `.exists()` ではなく `.first() is not None` を使う）。
        詳細・適用ルールは docs/specs/v1_4_2/コード君への申し送りメモ_v1_4_2.md
        の「SQLite 3.51.2 の planner bug 回避ルール」を参照。
        """
        return cls._get_by_status(contact, status).first() is not None

    @classmethod
    def get_by_group(cls, group_id):
        """group_id 単位で候補を取得（仕様書 §10.7.1 / §10.7.3）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] group_id: UUID
        [出力] QuerySet[DuplicateCandidate]

        レビュー画面（DuplicateCandidateGroupUpdateView）の PRG パターン用。
        """
        return cls.objects.filter(group_id=group_id)

    @classmethod
    def create_recovered_from(cls, old_candidate, new_surviving_person):
        """recover 用の新規 DuplicateCandidate 作成（仕様書 §10.7.1 / §12.8.3 / §12.8.4）。

        [性質] 副作用あり（DB書込：新規 DuplicateCandidate を作成）
        [入力] old_candidate: DuplicateCandidate（recover 対象。片側が merged_person）
               new_surviving_person: Person（merged_person を置き換える surviving 側）
        [出力] DuplicateCandidate（review_status='pending' で新規作成されたレコード）
        [例外] ValueError（old_candidate のいずれの側も new_surviving_person に
               merged_into していない場合）

        score / rank / group_id は old_candidate からそのままコピーする。
        ⚠ スコアの再計算は禁止（仕様書 §12.8.2 / §12.8.4）。これは過去のレビュアー
        （GPT・Opus 含む）が繰り返し陥ってきた誤解。連続レビュー UX を優先する設計判断で
        あり、人物の同一性指標（score / rank）は Contact のフィールド値修正に依存しない。
        スコアの絶対値は次回 cron（duplicate_checked_at=NULL 経由）で正確な値に補正される。

        merged_person 側の特定は、old_candidate.person_a / person_b のうち
        merged_into == new_surviving_person のものを探すことで行う。
        person_a / person_b は ID 順に正規化して保存する（仕様書 §4.7）。
        """
        if old_candidate.person_a.merged_into_id == new_surviving_person.id:
            other_person = old_candidate.person_b
        elif old_candidate.person_b.merged_into_id == new_surviving_person.id:
            other_person = old_candidate.person_a
        else:
            raise ValueError(
                f"create_recovered_from: neither person_a "
                f"({old_candidate.person_a_id}) nor person_b "
                f"({old_candidate.person_b_id}) is merged into "
                f"new_surviving_person ({new_surviving_person.id})."
            )

        if new_surviving_person.id < other_person.id:
            person_a, person_b = new_surviving_person, other_person
        else:
            person_a, person_b = other_person, new_surviving_person

        return cls.objects.create(
            person_a=person_a,
            person_b=person_b,
            score=old_candidate.score,
            rank=old_candidate.rank,
            group_id=old_candidate.group_id,
            review_status=cls.ReviewStatus.PENDING,
        )

    # ------------------------------------------------------------------
    # インスタンスメソッド（仕様書 §10.7.2）
    # ------------------------------------------------------------------

    def mark_as_merged(self, user, review_result, note):
        """状態遷移：merged 化（仕様書 §10.7.2）。

        [性質] 副作用あり（自身のフィールド更新のみ）
        [入力] user: 確認者（reviewed_by に記録）
               review_result: list[str]（DuplicateMergeReason の値、複数選択可、§4.7.1）
               note: str（任意メモ。空文字も可）
        [出力] None

        review_result の妥当性検証（merged 系 / different_person 系混在禁止など、§4.7.1）
        は MergeForm.clean() の責務（D ブロックで実装）。本メソッドでは検証しない。
        """
        self.review_status = self.ReviewStatus.MERGED
        self.review_result = review_result
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.note = note
        self.save(
            update_fields=[
                "review_status",
                "review_result",
                "reviewed_by",
                "reviewed_at",
                "note",
                "updated_at",
            ]
        )

    def mark_as_different_person(self, user, review_result, note=None):
        """状態遷移：different_person 化（仕様書 §10.7.2）。

        [性質] 副作用あり（自身のフィールド更新のみ）
        [入力] user: 確認者（reviewed_by に記録）
               review_result: list[str]（DifferentPersonReason の値、複数選択可、§4.7.1）
               note: str | None（None なら空文字保存。モデル既定 default="" に揃える）
        [出力] None

        review_result の妥当性検証は MergeForm.clean() の責務（D ブロックで実装）。
        """
        self.review_status = self.ReviewStatus.DIFFERENT_PERSON
        self.review_result = review_result
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.note = note if note is not None else ""
        self.save(
            update_fields=[
                "review_status",
                "review_result",
                "reviewed_by",
                "reviewed_at",
                "note",
                "updated_at",
            ]
        )

    def record_different_person_action(self, user, different_reason=""):
        """自身の別人判定操作を ActionLog に記録（仕様書 §10.7.2 / §4.11.3）。

        [性質] 副作用あり（DB 書込：ActionLog レコードを作成。自身は変更しない）
        [入力] user: User（操作したユーザー、ActionLog.user に記録）
               different_reason: str（DifferentPersonReason の値、§14.3.4 / 別表 C.9。
                 'same_name' / 'ocr_error' / 'other_different'）
        [出力] None

        単一責任：ActionLog に書き込むだけ。自身の状態（review_status / reviewed_by 等）は
        変更しない（指示書 §3.2 / 仕様書 §10.8.4 の状態遷移と ActionLog 記録の分離）。状態
        遷移は別途 `mark_as_different_person()` の責務。

        data 構造：`{"different_reason": "..."}` のみ。`person_a_id` / `person_b_id` は
        FK で取れるので冗長コピーしない（指示書 §3.3 / §4.7「FK で取れる情報を data に
        冗長コピーしない原則」）。content_object=self で candidate への参照を持つので、
        ActionLog から `select_related` で参照可能。
        """
        from actionlogs.models import ActionLog  # 循環 import を避けるため遅延 import

        ActionLog.record(
            user=user,
            action="different_person",
            content_object=self,
            data={"different_reason": different_reason},
        )


class PersonMergeLog(models.Model):
    """マージ履歴DB（仕様書 v1.4.2 §4.8）。

    surviving_person / merged_person は PROTECT。マージログから過去状態を確実に追跡できるよう
    Person の物理削除をブロックする。
    """

    class Status(models.TextChoices):
        """ログ状態（仕様書 §14.4 / 別表 C.6）。"""

        UNDOABLE = "undoable", _("復元可能")
        UNDONE = "undone", _("復元済み")
        LOCKED = "locked", _("復元不可")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    surviving_person = models.ForeignKey(
        "persons.Person",
        on_delete=models.PROTECT,
        related_name="merge_logs_as_surviving",
    )
    merged_person = models.ForeignKey(
        "persons.Person",
        on_delete=models.PROTECT,
        related_name="merge_logs_as_merged",
    )
    duplicate_candidate = models.ForeignKey(
        DuplicateCandidate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNDOABLE,
    )
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    executed_at = models.DateTimeField()
    undone_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    undone_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.merged_person_id} → {self.surviving_person_id} ({self.status})"

    # ------------------------------------------------------------------
    # クラスメソッド（仕様書 §10.8.1）
    # ------------------------------------------------------------------

    @classmethod
    def _get_for_person(cls, person, status=None):
        """person を起点に（任意で status で絞り込んで）ログを返す内部ヘルパー。

        [性質] 準関数（DB 読み取りのみ）
        [入力] person: Person / status: str | None（None なら全状態を返す）
        [出力] QuerySet[PersonMergeLog]

        surviving_person / merged_person の OR 検索で、Person を起点とするログを取得する。
        get_for_person / get_undoable で共通利用する（DuplicateCandidate._get_by_status と同じ
        共通化方針）。
        """
        qs = cls.objects.filter(
            Q(surviving_person=person) | Q(merged_person=person)
        )
        if status is not None:
            qs = qs.filter(status=status)
        return qs

    @classmethod
    def create(cls, surviving_person, merged_person, user):
        """マージ実行のためのログレコードを作成（仕様書 §10.8.1 / §10.8.4）。

        [性質] 副作用あり（DB 書込：新規 PersonMergeLog レコードを作成）
        [入力] surviving_person: Person（マージで残る側）
               merged_person: Person（マージで統合される側）
               user: User（マージ実行者、executed_by に記録）
        [出力] PersonMergeLog（save 済みインスタンス）

        2回 save 方式：本メソッドは最低限のログレコードを作るだけ。duplicate_candidate /
        note 等のマージ処理文脈に依存する値は呼び出し側（マージ実行サービス層、B ブロック以降）
        が後から `merge_log.duplicate_candidate = candidate; merge_log.note = ...;
        merge_log.save(update_fields=['duplicate_candidate', 'note'])` で部分 save する想定。

        executed_at は明示的に timezone.now() を設定する（フィールド定義は auto_now_add=True
        ではなく素の DateTimeField のため、§4.8 の仕様書定義通り）。status は default='undoable'、
        note は default='' が効くので明示しない。
        """
        return cls.objects.create(
            surviving_person=surviving_person,
            merged_person=merged_person,
            executed_by=user,
            executed_at=timezone.now(),
        )

    @classmethod
    def lock_past_logs(cls, merged_person):
        """過去のログを locked 状態に変更（仕様書 §10.8.1 / §9.3.1 手順8 / §9.6）。

        [性質] 副作用あり（DB 書込：自モデル集合を一括 update）
        [入力] merged_person: Person（**今のマージで merged 側になる Person**。過去ログでは
               `surviving_person` フィールドに入っていた Person を指す）
        [出力] None（QuerySet.update() の戻り値は捨てる）

        引数名 `merged_person` と検索条件 `surviving_person=merged_person` の非対称性は
        意図したもの。仕様書 §9.3.1 手順8「過去のマージログを locked に変更（merged_person を
        surviving とする undoable なログ）」の表記通り。

        シナリオ：T1 で A→B（ML1: surviving=B, merged=A, status='undoable'）。続いて T2 で
        B→C を実行する際、`lock_past_logs(merged_person=B)` を呼ぶ。`surviving_person=B`
        かつ `status='undoable'` の ML1 がヒットして locked 化される。Contact の
        `previous_person` は T2 で B→C のマージにより上書きされ、A→B の復元はもうできない
        （直近1段階分しか保持しない）ため、ML1 を locked にする必要がある。

        既に `locked` / `undone` のログには影響しない（フィルタ条件 `status='undoable'`）。
        """
        cls.objects.filter(
            surviving_person=merged_person,
            status=cls.Status.UNDOABLE,
        ).update(status=cls.Status.LOCKED)

    @classmethod
    def get_for_person(cls, person):
        """Person 単位のログ一覧取得（仕様書 §10.8.1）。マージログ一覧画面用。

        [性質] 準関数（DB 読み取りのみ）
        [入力] person: Person
        [出力] QuerySet[PersonMergeLog]（surviving / merged どちら側のログも含む。
               status による絞り込みは行わない）
        """
        return cls._get_for_person(person)

    @classmethod
    def get_undoable(cls, person):
        """復元可能なログ取得（仕様書 §10.8.1）。

        [性質] 準関数（DB 読み取りのみ）
        [入力] person: Person
        [出力] QuerySet[PersonMergeLog]（status='undoable' のもの。surviving / merged
               どちら側でも返す）
        """
        return cls._get_for_person(person, status=cls.Status.UNDOABLE)

    # ------------------------------------------------------------------
    # インスタンスメソッド（仕様書 §10.8.2）
    # ------------------------------------------------------------------

    def is_undoable(self):
        """復元可能かどうかの判定（仕様書 §10.8.2）。

        [性質] 純関数（DB 操作なし・自身の status フィールドを読むだけ）
        [入力] なし
        [出力] bool（status='undoable' なら True）

        status フィールドが「現時点で復元可能か」の唯一の真実。多重マージで巻き込まれた場合は
        `lock_past_logs()` で `status='locked'` に、復元実行された場合は `mark_as_undone()`
        で `status='undone'` に、それぞれ起きた時点で反映済み。実行時に他のフィールドを再
        チェックする必要はない（複合判定にしない、§3.4 設計思想）。
        """
        return self.status == self.Status.UNDOABLE

    def mark_as_undone(self, user):
        """自身の状態遷移：undone 化（仕様書 §10.8.2）。

        [性質] 副作用あり（自身のフィールド更新のみ）
        [入力] user: User（復元実行者、undone_by に記録）
        [出力] None

        事前条件チェック（status が undoable かどうか）は呼び出し側（復元実行サービス層、
        B ブロック以降）が `is_undoable()` で行う責務。本メソッドは「自身の状態遷移を実行する」
        責務に絞る。Contact の `previous_*` 戻し等の復元処理本体も B ブロック以降の責務。
        """
        self.status = self.Status.UNDONE
        self.undone_by = user
        self.undone_at = timezone.now()
        self.save(update_fields=["status", "undone_by", "undone_at", "updated_at"])

    def get_undo_preview(self):
        """復元後の予測状態を返す（仕様書 §10.8.2 / §10.8.3）。確認画面表示用。

        [性質] 準関数（DB 読み取りのみ・SELECT のみ発行する読み取り専用メソッド）
        [入力] なし
        [出力] dict（以下のキーを持つ）
               - 'merged_person': Person（self.merged_person）
               - 'contacts_to_restore': QuerySet[Contact]（merged_person に戻る Contact）
               - 'contacts_remaining_in_surviving': QuerySet[Contact]（surviving 側に残る
                 Contact）

        スナップショットは取っていない。Contact モデル自身が `previous_person` /
        `previous_status` フィールドで「マージ前の状態」を保持する設計（§9.4.2）のため、
        `previous_person` を読むだけで「復元したらどう動くか」を予測できる。

        判定ロジック：
          - 「今 surviving 配下にあって `previous_person == merged_person`」=
            このマージで動いた Contact = 復元すると merged_person に戻る
          - 「今 surviving 配下にあって `previous_person != merged_person`」=
            元から surviving 配下にいた、または別マージで動いた Contact = 復元しても動かない

        実際の復元処理（Contact の person / status 戻し、PersonMergeLog の status='undone'
        化など）はマージ実行サービス層（B ブロック以降）の責務。本メソッドでは DB を一切
        変更しない。
        """
        from contacts.models import Contact  # 循環 import を避けるため遅延 import

        contacts_to_restore = Contact.objects.filter(
            person=self.surviving_person,
            previous_person=self.merged_person,
        )
        contacts_remaining_in_surviving = Contact.objects.filter(
            person=self.surviving_person,
        ).exclude(
            previous_person=self.merged_person,
        )
        return {
            "merged_person": self.merged_person,
            "contacts_to_restore": contacts_to_restore,
            "contacts_remaining_in_surviving": contacts_remaining_in_surviving,
        }

    def record_merge_action(self, user, merge_reason="", updated_fields=None):
        """マージ実行を ActionLog に記録（仕様書 §10.8.2 / §4.11.3）。

        [性質] 副作用あり（DB 書込：ActionLog レコードを作成。自身は変更しない）
        [入力] user: User（マージ実行者、ActionLog.user に記録）
               merge_reason: str（DuplicateMergeReason の値、§14.3.3 / 別表 C.8。
                 'same_card' / 'transfer' / 'promotion' / 'job_change' /
                 'additional_role' / 'name_change' / 'other_merged' のいずれか）
               updated_fields: list[str] | None（マージ画面で更新されたフィールド名リスト。
                 None / 空リストならフィールド更新なし）
        [出力] None

        単一責任：ActionLog に書き込むだけ。自身の状態（status / executed_by / executed_at
        等）は変更しない（指示書 §3.2 / 仕様書 §10.8.4 の状態遷移と ActionLog 記録の分離）。
        状態遷移は別途 `PersonMergeLog.create()` / `mark_as_undone()` の責務。

        data 構造：`{"merge_reason": "...", "updated_fields": [...]}` のみ。
        `surviving_person_id` / `merged_person_id` / `duplicate_candidate_id` は FK で
        取れるので冗長コピーしない（指示書 §3.3 / §4.7）。content_object=self で merge_log
        への参照を持つので、ActionLog から `select_related` で参照可能。
        """
        from actionlogs.models import ActionLog  # 循環 import を避けるため遅延 import

        ActionLog.record(
            user=user,
            action="merged",
            content_object=self,
            data={
                "merge_reason": merge_reason,
                "updated_fields": updated_fields if updated_fields else [],
            },
        )

    def record_undo_action(self, user):
        """復元実行を ActionLog に記録（仕様書 §10.8.2 / §4.11.3）。

        [性質] 副作用あり（DB 書込：ActionLog レコードを作成。自身は変更しない）
        [入力] user: User（復元実行者、ActionLog.user に記録）
        [出力] None

        単一責任：ActionLog に書き込むだけ。自身の状態は変更しない。状態遷移は別途
        `mark_as_undone()` の責務。

        data 構造：`{}`（空 dict）。`surviving_person_id` / `merged_person_id` は merge_log
        経由で取れるため冗長コピーしない。復元自体には追加の業務情報がないので、空 dict で
        十分（指示書 §3.3 / §4.7）。
        """
        from actionlogs.models import ActionLog  # 循環 import を避けるため遅延 import

        ActionLog.record(
            user=user,
            action="undone",
            content_object=self,
            data={},
        )
