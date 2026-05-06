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
