"""dev_create_link_test_data 管理コマンド（開発ツール、ユーザー紐付けモード確認用）。

ユーザー紐付けモード（プロフィール / ホームの紐付け候補表示・人物一覧の紐付けモード）の
実機確認用に、「指定したメールアドレスを primary_contact のメールに持つ、status=ACTIVE・
User 未紐付けの Person」を複数生成する。

ログインユーザーのメール（例 iwata@network-tokai.jp）と一致する primary Contact を持つ
Person を作ることで、そのユーザーに対する紐付け候補を発生させる。既存の
dev_create_dup_test_data / dev_create_test_contact_data は会社ドメインのメールしか
生成しないため、本コマンドがこれを補う。

設計概要：
- Person は status=ACTIVE、User 未紐付け（User.person を一切触らない）、primary_contact をセット
- primary Contact は status=primary、email=--email の値、full_name=「{prefix}{連番}」
- ContactFieldConfidence は作成しない（紐付け判定にスコアは不要、重複検出を兼ねない）
- DuplicateCandidate / OriginalImage / BusinessCard / PersonMergeLog は生成しない
- --reset で「同一 user 由来 かつ full_name が prefix で始まる」テストデータを削除してから生成

専用ユーザー：
- 既定 `test_data_user`（--user で変更可）を Django Admin 等で事前作成しておく前提
- 起動時に取得のみ。存在しなければエラー終了（自動作成しない）

ファイル名・コマンド名のプレフィックスは `dev_*`（開発ツール）。実行は手動のみ。
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from contacts.models import Contact
from persons.models import Person


User = get_user_model()

DEFAULT_USER = "test_data_user"
DEFAULT_EMAIL = "iwata@network-tokai.jp"
DEFAULT_COUNT = 2
DEFAULT_PREFIX = "紐付けテスト"


# ----------------------------------------------------------------------
# DB 書込み（副作用あり）
# ----------------------------------------------------------------------


def _create_link_persons(email: str, count: int, prefix: str, user) -> list[Person]:
    """[性質] 副作用あり（DB 書込）。紐付け候補用の Person + primary Contact を count 件作成する。

    [入力] email: primary Contact のメール / count: 生成件数 / prefix: 氏名プレフィックス /
           user: created_by・updated_by に使う User
    [出力] list[Person]（生成した Person）

    1 件ずつ Person(status=ACTIVE) → Contact(status=primary, email=email) →
    Person.primary_contact 同期、の順で作成する。User.person は一切触らない（未紐付けを保つ）。
    ContactFieldConfidence は作成しない。
    """
    created: list[Person] = []
    for i in range(1, count + 1):
        label = f"{prefix}{i:02d}"
        person = Person.objects.create(status=Person.Status.ACTIVE)
        contact = Contact.objects.create(
            business_card=None,
            person=person,
            status=Contact.Status.PRIMARY,
            created_by=user,
            updated_by=user,
            full_name=label,
            last_name=prefix,
            first_name=f"{i:02d}",
            email=email,
        )
        person.primary_contact = contact
        person.save(update_fields=["primary_contact", "updated_at"])
        created.append(person)
    return created


def _reset_test_data(prefix: str, user, stdout) -> dict[str, int]:
    """[性質] 副作用あり（DB 削除）。同一 user 由来 かつ full_name が prefix 始まりのテストデータを削除。

    [入力] prefix: 氏名プレフィックス / user: 対象 User / stdout: 進捗用
    [出力] dict（contacts: 削除 Contact 数, persons: 削除した孤児 Person 数）

    Contact を delete() するとカスケードで子レコードは自動削除される。孤児になった
    Person のみ別途検出して物理削除する（dev_create_dup_test_data と同じ流儀）。
    """
    target = Contact.objects.filter(created_by=user, full_name__startswith=prefix)
    affected_person_ids = set(target.values_list("person_id", flat=True))
    _total, per_model = target.delete()
    deleted_contacts = per_model.get(Contact._meta.label, 0)
    deleted_persons = 0
    for pid in affected_person_ids:
        if pid is not None and not Contact.objects.filter(person_id=pid).exists():
            Person.objects.filter(id=pid).delete()
            deleted_persons += 1
    stdout.write(
        f"reset: deleted contacts={deleted_contacts}, orphan persons={deleted_persons}"
    )
    return {"contacts": deleted_contacts, "persons": deleted_persons}


# ----------------------------------------------------------------------
# Command 本体
# ----------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "ユーザー紐付けモード確認用に、指定メールを primary に持つ "
        "status=ACTIVE・User 未紐付けの Person を生成する開発ツール。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default=DEFAULT_EMAIL,
            help=(
                f"生成する Person の primary Contact のメール（既定 {DEFAULT_EMAIL}）。"
                "ログインユーザーのメールと一致させると紐付け候補になる。"
            ),
        )
        parser.add_argument(
            "--count",
            type=int,
            default=DEFAULT_COUNT,
            help=(
                f"生成する Person の件数（既定 {DEFAULT_COUNT}）。"
                "1 なら単一候補、2 以上なら複数候補状態を作れる。"
            ),
        )
        parser.add_argument(
            "--user",
            default=DEFAULT_USER,
            help=f"Contact の作成者ユーザー名（既定 {DEFAULT_USER}）",
        )
        parser.add_argument(
            "--prefix",
            default=DEFAULT_PREFIX,
            help=(
                f"氏名のプレフィックス（既定 {DEFAULT_PREFIX}）。"
                "--reset 時の削除キーにもなる。"
            ),
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="既存テストデータ（同一 user 由来・prefix 始まり）を削除してから生成する",
        )

    def handle(self, *args, **opts):
        username = opts["user"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(
                f"ユーザー '{username}' が存在しません。"
                "Django Admin 等で事前作成してから実行してください。"
                "本コマンドはユーザーを作成しません。"
            )

        email = opts["email"]
        count = opts["count"]
        prefix = opts["prefix"]
        if count < 0:
            raise CommandError("--count は 0 以上で指定してください。")

        with transaction.atomic():
            if opts["reset"]:
                _reset_test_data(prefix, user, self.stdout)
            created = _create_link_persons(email, count, prefix, user)

        self.stdout.write(
            self.style.SUCCESS(
                f"生成完了：Person {len(created)} 件 "
                f"(email={email} / status=active / User 未紐付け / prefix='{prefix}')"
            )
        )
        for person in created:
            c = person.primary_contact
            self.stdout.write(
                f"  Person id={person.id} status={person.status} "
                f"primary='{c.full_name}' email={c.email}"
            )
        self.stdout.write(
            self.style.NOTICE(
                f"削除する場合: manage.py dev_create_link_test_data "
                f"--reset --count 0 --user {username} --prefix '{prefix}'"
            )
        )
