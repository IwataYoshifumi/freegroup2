"""dev_create_tag_test_data 管理コマンド（開発ツール）。

UIの動作確認および「カテゴリ内 OR、カテゴリ間 AND」の抽出ロジック確認のため、
TagCategory、Tag、および TagAssignment（タグ付与）のテストデータを生成する。

設計概要：
- 対象 Person は既存の status='active' なもの（persons.models.Person.Status.ACTIVE）のみを使用。
  Person が1件も存在しない場合は処理を行わずにメッセージを出して終了する。
- 4つのカテゴリ（業種、役職、エリア、決裁権限）を生成。
- 各カテゴリに複数のタグを生成。
- 既存の active な Person に対し、指定された確率（デフォルト 0.8）でランダムにタグを付与する。
  同一人物に複数カテゴリのタグを付与したり、同一カテゴリ内で複数タグを付与するケースを混在させる。
- --reset オプションで、このコマンドが生成するタグ系データを初期化してから再生成する。

専用ユーザー：
- `test_data_user`（または --user で指定されたユーザー）が Django Admin 等で事前作成されている前提。
- 存在しない場合はエラー終了。
"""

from __future__ import annotations

import random
from typing import Dict, List

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from persons.models import Person
from tags.models import TagCategory, Tag, TagAssignment

User = get_user_model()
DEFAULT_USER = "test_data_user"
DEFAULT_SEED = 42


class Command(BaseCommand):
    help = (
        "タグ管理・タグカテゴリ管理・一括タグ付け・リスト抽出UI確認用の"
        "TagCategory, Tag, TagAssignment テストデータを既存 of active な Person に対し生成します。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="このコマンドが生成するタグ系データ（TagCategory, Tag, TagAssignment）を削除してから生成する",
        )
        parser.add_argument(
            "--user",
            default=DEFAULT_USER,
            help=f"生成・付与を行う管理ユーザー名（既定 {DEFAULT_USER}）",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=DEFAULT_SEED,
            help="乱数再現用シード（既定 42）",
        )
        parser.add_argument(
            "--assignment-rate",
            type=float,
            default=0.8,
            help="Person にタグを付与する確率（0.0〜1.0、既定 0.8）",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="詳細なログを出力する",
        )

    def handle(self, *args, **options):
        username = options["user"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(
                f"ユーザー '{username}' が存在しません。"
                f"Django Admin で事前作成してから実行してください。"
                f"本コマンドはユーザーを作成しません。"
            )

        rate = options["assignment_rate"]
        if not (0.0 <= rate <= 1.0):
            raise CommandError(f"--assignment-rate は 0.0〜1.0 の範囲で指定してください: {rate}")

        # active な Person を取得（存在しなければ終了）
        active_persons = list(Person.objects.filter(status=Person.Status.ACTIVE))
        if not active_persons:
            self.stdout.write(
                self.style.WARNING("active な Person が存在しません。タグの生成・付与は行われません。")
            )
            return

        rng = random.Random(options["seed"])

        categories_data = {
            "業種": {
                "description": "取引先企業の業種分類",
                "sort_order": 10,
                "tags": ["製造業", "商社", "IT", "建設業", "サービス業"]
            },
            "役職": {
                "description": "個人の役職クラス",
                "sort_order": 20,
                "tags": ["代表取締役", "役員", "部長", "課長", "主任", "担当"]
            },
            "エリア": {
                "description": "主な営業管轄エリア",
                "sort_order": 30,
                "tags": ["関東", "関西", "中部", "九州・沖縄", "北海道・東北"]
            },
            "決裁権限": {
                "description": "商談における意思決定権限",
                "sort_order": 40,
                "tags": ["あり", "なし", "準決裁者"]
            }
        }

        with transaction.atomic():
            if options["reset"]:
                # このコマンドで生成されるタグ関係データをクリア
                # 1. まず TagAssignment を削除
                deleted_assignments, _ = TagAssignment.objects.filter(assigned_by=user).delete()
                # 2. Tag を削除
                deleted_tags, _ = Tag.objects.filter(created_by=user).delete()
                # 3. 指定したカテゴリ名の TagCategory を削除
                deleted_categories, _ = TagCategory.objects.filter(
                    name__in=list(categories_data.keys())
                ).delete()

                self.stdout.write(
                    self.style.SUCCESS(
                        f"リセット完了: 削除されたカテゴリ={deleted_categories}, "
                        f"タグ={deleted_tags}, 付与={deleted_assignments}"
                    )
                )

            # カテゴリとタグの生成
            created_categories = []
            created_tags = []

            for cat_name, cat_info in categories_data.items():
                category, cat_created = TagCategory.objects.get_or_create(
                    name=cat_name,
                    defaults={
                        "description": cat_info["description"],
                        "sort_order": cat_info["sort_order"],
                        "is_archived": False
                    }
                )
                created_categories.append(category)
                if options["verbose"] and cat_created:
                    self.stdout.write(f"カテゴリ作成: {cat_name}")

                for tag_name in cat_info["tags"]:
                    tag, tag_created = Tag.objects.get_or_create(
                        name=tag_name,
                        defaults={
                            "category": category,
                            "created_by": user,
                            "is_archived": False,
                            "description": f"{cat_name}カテゴリのタグ: {tag_name}"
                        }
                    )
                    created_tags.append(tag)
                    if options["verbose"] and tag_created:
                        self.stdout.write(f"  タグ作成: {tag_name}")

            # 既存 Person へのランダムタグ付与
            assigned_count = 0
            persons_with_tags = 0

            for person in active_persons:
                # rate の確率で付与対象にする
                if rng.random() > rate:
                    continue

                # 1人あたり 1〜4 個 of カテゴリからタグを付与
                num_categories = rng.randint(1, len(created_categories))
                selected_categories = rng.sample(created_categories, num_categories)

                assigned_to_this_person = False

                for category in selected_categories:
                    # このカテゴリに属するタグをフィルタリング
                    cat_tags = [t for t in created_tags if t.category_id == category.id]
                    if not cat_tags:
                        continue

                    # 1カテゴリ内で基本は1つ、稀に2つ（OR条件のテスト用）タグを付与
                    # 15%の確率で2つのタグを付与（タグ数が2つ以上ある場合）
                    num_tags = 2 if (rng.random() < 0.15 and len(cat_tags) >= 2) else 1
                    selected_tags = rng.sample(cat_tags, num_tags)

                    for tag in selected_tags:
                        assignment, created = TagAssignment.objects.get_or_create(
                            tag=tag,
                            person=person,
                            defaults={
                                "assigned_by": user
                            }
                        )
                        if created:
                            assigned_count += 1
                            assigned_to_this_person = True
                            if options["verbose"]:
                                self.stdout.write(f"  付与: Person {person.id} ➔ タグ '{tag.name}'")

                if assigned_to_this_person:
                    persons_with_tags += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"生成・付与完了: カテゴリ数={len(created_categories)}、"
                    f"タグ数={len(created_tags)}、"
                    f"タグ付与数={assigned_count} (付与対象 Person 数={persons_with_tags}/{len(active_persons)})"
                )
            )
