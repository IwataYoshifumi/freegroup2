"""業務イベントの汎用ログ（仕様書 v1.4.2 §4.10）。

ActionLog はマージ実行・別人判定・cron 実行・OCR 処理結果等を記録する汎用ログ。
GenericForeignKey で全モデル横断の参照を持つため、独立アプリ（actionlogs）に配置する
（A-1c 指示書 §7.2 のたんたん判断）。
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ActionLog(models.Model):
    """アクションログDB（仕様書 v1.4.2 §4.10）。

    action は TextChoices ではなく自由文字列（別表 C.12 は参考値）。
    システム実行（cron 等）では user / content_type / object_id が NULL になる。
    不変履歴ログのため updated_at は持たない（仕様書 §4.10）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    action = models.CharField(max_length=50)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    object_id = models.CharField(max_length=255, null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    object_repr = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(default=dict)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} {self.object_repr} ({self.created_at:%Y-%m-%d %H:%M})"

    # ------------------------------------------------------------------
    # クラスメソッド（仕様書 §10.9.1）
    # ------------------------------------------------------------------

    @classmethod
    def record(
        cls,
        user,
        action,
        content_object=None,
        object_repr="",
        data=None,
        note="",
    ):
        """任意の業務イベントを直接記録（仕様書 §10.9.1 / §4.11.2）。

        [性質] 副作用あり（DB 書込：新規 ActionLog レコードを作成）
        [入力] user: User | None（システム実行（cron 等）は None）
               action: str（'merged' / 'undone' / 'different_person' / 'executed' / 'created' 等）
               content_object: Model | None（対象モデルインスタンス。None なら content_type /
                 object_id は NULL になる。Django の GenericForeignKey が `content_object` の
                 設定を自動展開して `content_type` / `object_id` をセットする §3.1）
               object_repr: str（操作時点の対象オブジェクトの文字列表現またはコマンド名。
                 cron 実行ログ等の content_object なし時に「処理名」を残すために使う）
               data: dict | None（業務固有の追加情報。None のときは空 dict）
               note: str（補足メモ）
        [出力] ActionLog（save 済みインスタンス）

        cron 実行ログなど、モデルインスタンスを持たない場面で使用する。モデルインスタンスを
        持つ場面（マージ実行・別人判定・復元等）では各モデルのインスタンスメソッド経由で
        本メソッドを呼ぶ（仕様書 §4.11.2）。

        仕様書 §10.9.1 の引数 `extra=None` は、A-2e のフィールド改名（extra→data）に
        合わせて `data=None` に変更（指示書 §5.2）。`diff` 引数は §4.10 のフィールド削除に
        合わせて廃止（指示書 §5.1）。
        """
        return cls.objects.create(
            user=user,
            action=action,
            content_object=content_object,
            object_repr=object_repr,
            data=data if data is not None else {},
            note=note,
        )
