# A-1c 実装指示書 ／ フィールド追加・新規モデル作成・TextChoices 定義

**FreeGroup2 v1.4.2 ／ コード君（Claude Code）向け**

---

## 1. 本書の位置づけ

本書は FreeGroup2 v1.4.2 の実装の **A-1c（v1.4.2 のフィールド追加・新規モデル作成・TextChoices 定義）** を定義する。

A-1a（新規アプリ作成）と A-1b（モデル移動）は完了済み。本書は A-1 の最終ステップ。

本書と仕様書の記述が食い違う場合、**仕様書 v1.4.2 統合最終版が優先する**。ただし「実装の進め方・作業ステップの分割」については本書を一次情報源とする。

---

## 2. 全体ブロックの俯瞰（再掲）

### 2.1 ブロック分割

| ブロック | 内容 |
|---|---|
| **A** | 基盤系（モデル・マイグレーション・モデルメソッド） |
| **B** | サービス層・タスク層 |
| **C** | マージ実行系 |
| **D** | UI 層 |

### 2.2 ブロック A のサブステップ

| サブステップ | 内容 | 状態 |
|---|---|---|
| A-1a | 新規アプリ4つを作成 | ✅ 完了 |
| A-1b | cards から persons / contacts へモデル移動 | ✅ 完了 |
| **A-1c** | **v1.4.2 フィールド追加・新規モデル・TextChoices 定義（本書）** | 本書 |
| A-2 | モデルメソッドの実装 | 別途指示書 |

### 2.3 重要原則

- **コード君は A-1c の範囲のみ実装する**。A-2（モデルメソッド）以降を勝手に進めない。
- **A-1c ではモデルメソッドを実装しない**。`fix()`, `mark_as_merged()`, `set_primary_contact()` などすべて A-2 の作業。
- マイグレーションファイル生成までが A-1c の範囲。**migrate 実行はたんたん手動**。
- コミット & プッシュはコード君が単独で行わない。

---

## 3. A-1c の目的

v1.4.2 の DB スキーマを完成させる。具体的には：

1. `config/constants.py` に共通 TextChoices と定数を定義
2. 既存モデル（Person / Contact / ContactFieldConfidence）に v1.4.2 のフィールドを追加し、内部クラス TextChoices と制約を追加
3. 新規モデル（DuplicateCandidate / PersonMergeLog / ActionLog）を作成
4. マイグレーションファイル生成

A-1c 完了後の状態：

- v1.4.2 のすべてのフィールド・制約・TextChoices が DB スキーマに反映されている
- マイグレーションファイル（既存アプリは 0002、新規モデルアプリは 0001_initial）が生成済み
- migrate は未実行（たんたん手動で適用予定）

---

## 4. 前提

### 4.1 たんたんの方針

- **自宅PCの環境は完全に開発用**：マイグレーション時に既存DB全削除可能（データ保護不要）
- A-1b で db.sqlite3 は既に削除済み
- マイグレーション履歴は残す（A-1b の 0001 はそのまま、A-1c で 0002 を生成）

### 4.2 作業ブランチ

`feature/v1.4.2-models`（A-1a / A-1b から継続使用）

### 4.3 開始前の確認

`git status` で作業ツリーがクリーン（A-1b がコミット済み）であることを確認。

---

## 5. 仕様書の参照場所

A-1c で参照する仕様書の主要章節を一覧化する。実装中はこれらを都度確認すること。

| トピック | 参照先 |
|---|---|
| モデル定義（フィールド一覧） | 統合最終版 4.4 / 4.5 / 4.6 / 4.7 / 4.8 / 4.10 |
| 共通 TextChoices | 統合最終版 14.3 |
| モデル固有 TextChoices | 統合最終版 14.4 |
| TextChoices 値の表示名 | 統合最終版 別表 C.1 〜 C.12 |
| モデル名・フィールド名対照 | 統合最終版 別表 A |
| ContactFieldConfidence の防御策 | 統合最終版 4.6.1 |
| Person ↔ Contact 二重管理の設計趣旨 | 統合最終版 4.5.2（理解の参考） |
| match_reason / matched_fields の扱い | **削除する**（v1.4.2 確定方針、本書 7.1 参照） |
| ActionLog 配置 | **actionlogs アプリ**（仕様書から変更、本書 7.2 参照） |

---

## 6. 作業手順

### Step 1：config/constants.py の新規作成

`config/constants.py` を **新規作成する**（現状このファイルは存在しない）。以下の内容を定義する。

#### Step 1-1：共通 TextChoices

仕様書 14.3 / 別表 C.7 / C.8 / C.9 を参照。

- `PersonChangeReason`（5値：fix / transfer / promotion / job_change / name_change）
- `DuplicateMergeReason`（7値：same_card / transfer / promotion / job_change / additional_role / name_change / other_merged）
- `DifferentPersonReason`（3値：same_name / ocr_error / other_different）

各 TextChoices は `models.TextChoices` を継承する。値と表示名は別表 C を厳密に参照。

**重要**：3つは独立した TextChoices として別定義する。共通値（transfer / promotion / job_change / name_change）があっても統合しない。設計趣旨は 14.3.1 参照。

#### Step 1-2：共通定数

仕様書 14.3.5 / 14.3.6 を参照。

- `DUPLICATE_CHECK_FIELDS`：9フィールドのリスト `['full_name', 'company', 'department', 'title', 'branch', 'email', 'phone', 'mobile', 'address']`
- `DUPLICATE_GENERIC_EMAIL_LOCALPARTS`：13値のリスト `['info', 'contact', 'support', 'sales', 'admin', 'office', 'mail', 'inquiry', 'help', 'service', 'shop', 'customer', 'reception']`

### Step 2：persons/models.py に Person のフィールド・内部クラス追加

仕様書 4.5 を参照。

#### Step 2-1：内部クラス Status を追加

仕様書 4.5.1 / 別表 C.11 参照。値：active / merged / archived。

#### Step 2-2：フィールド追加

- `primary_contact`：`ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
  - related_name='+' は逆参照を作らない（Contact.person で逆参照可能なため）
- `status`：`CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)`
- `merged_into`：`ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='merged_from_set')`

`Contact` への ForeignKey は **文字列参照** で書くこと（`'contacts.Contact'`）。直接 import すると循環参照になる。

### Step 3：contacts/models.py に Contact のフィールド・内部クラス・制約追加

仕様書 4.4 を参照。

#### Step 3-1：内部クラス Status を追加

仕様書 4.4.2 / 別表 C.10 参照。値：primary / active / inactive。

#### Step 3-2：フィールド追加

- `status`：`CharField(max_length=20, choices=Status.choices)` ← **必須フィールド、デフォルト値なし**
- `previous_person`：`ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `previous_status`：`CharField(max_length=20, null=True, blank=True)`
- `duplicate_checked_at`：`DateTimeField(null=True, blank=True)`
- `created_by`：`ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `updated_by`：`ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `lang`：`CharField(max_length=10, default='ja')`
- `postal_code`：`CharField(max_length=20, blank=True, default='')`

既存の `business_card` / `person` 等のフィールドは **変更しない**。

#### Step 3-3：partial unique constraint を追加

仕様書 4.4.2 参照：「1 Person につき status='primary' の Contact は 1 つだけ」

```
UniqueConstraint(
    fields=['person'],
    condition=Q(status='primary'),
    name='unique_primary_contact_per_person'
)
```

これは既存の `unique_contact_field_name` 制約とは別物。Meta.constraints に追加する。

### Step 4：contacts/models.py に ContactFieldConfidence の制約追加

仕様書 4.6 / 4.6.1 を参照。

#### Step 4-1：CONFIDENCE_CHOICES を TextChoices に書き換え

既存の：

```
CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_CHOICES = [
    (CONFIDENCE_LOW, "低"),
    (CONFIDENCE_MEDIUM, "中"),
]
```

を内部クラス TextChoices に書き換え：

```
class Confidence(models.TextChoices):
    LOW = 'low', '低'
    MEDIUM = 'medium', '中'
```

`confidence` フィールドの `choices=CONFIDENCE_CHOICES` を `choices=Confidence.choices` に変更。

high は記録対象外（疑似インスタンスとしてのみ生成、DB 保存しない）。これは仕様書方針。

#### Step 4-2：CheckConstraint の追加

`Meta.constraints` に追加：

```
CheckConstraint(
    check=Q(confidence__in=['low', 'medium']),
    name='confidence_low_or_medium'
)
```

#### Step 4-3：save() オーバーライドの追加

`confidence='high'` で save() が呼ばれた場合、`ValueError` を発生させる：

```
def save(self, *args, **kwargs):
    if self.confidence == 'high':
        raise ValueError(
            "ContactFieldConfidence with confidence='high' must not be saved. "
            "high values are pseudo-instances only."
        )
    super().save(*args, **kwargs)
```

### Step 5：duplicates/models.py に DuplicateCandidate を新規作成

仕様書 4.7 を参照。**`match_reason` と `matched_fields` フィールドは作らない**（v1.4.2 確定方針、本書 7.1 参照）。

#### Step 5-1：内部クラス Rank と ReviewStatus を追加

仕様書 14.4 / 別表 C.4 / C.5 参照。

- `Rank`（5値：exact_match / possible_high / possible_mid / possible_low / none）
- `ReviewStatus`（4値：pending / merged / different_person / invalidated）

#### Step 5-2：フィールド定義

- `id`：`UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`
- `group_id`：`UUIDField(null=True, blank=True)`
- `person_a`：`ForeignKey('persons.Person', on_delete=models.CASCADE, related_name='+')`
- `person_b`：`ForeignKey('persons.Person', on_delete=models.CASCADE, related_name='+')`
- `score`：`IntegerField()`
- `rank`：`CharField(max_length=20, choices=Rank.choices)`
- `review_status`：`CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)`
- `review_result`：`JSONField(default=list)`
- `note`：`TextField(blank=True, default='')`
- `assigned_to`：`ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `reviewed_by`：`ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `reviewed_at`：`DateTimeField(null=True, blank=True)`
- `created_at`：`DateTimeField(auto_now_add=True)`
- `updated_at`：`DateTimeField(auto_now=True)`

#### Step 5-3：partial unique constraint を追加

仕様書 4.7 参照：「review_status='pending' に限定した person_a, person_b ペアの一意性」

```
UniqueConstraint(
    fields=['person_a', 'person_b'],
    condition=Q(review_status='pending'),
    name='unique_pending_person_pair'
)
```

### Step 6：duplicates/models.py に PersonMergeLog を新規作成

仕様書 4.8 を参照。

#### Step 6-1：内部クラス Status を追加

仕様書 14.4 / 別表 C.6 参照。値：undoable / undone / locked。

#### Step 6-2：フィールド定義

- `id`：`UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`
- `surviving_person`：`ForeignKey('persons.Person', on_delete=models.PROTECT, related_name='merge_logs_as_surviving')`
- `merged_person`：`ForeignKey('persons.Person', on_delete=models.PROTECT, related_name='merge_logs_as_merged')`
- `duplicate_candidate`：`ForeignKey(DuplicateCandidate, on_delete=models.PROTECT, null=True, blank=True, related_name='+')`
- `status`：`CharField(max_length=20, choices=Status.choices, default=Status.UNDOABLE)`
- `executed_by`：`ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `executed_at`：`DateTimeField()`
- `undone_by`：`ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `undone_at`：`DateTimeField(null=True, blank=True)`
- `note`：`TextField(blank=True, default='')`
- `created_at`：`DateTimeField(auto_now_add=True)`
- `updated_at`：`DateTimeField(auto_now=True)`

surviving_person / merged_person を **PROTECT** とする理由：マージログから過去の状態を確実に追跡できるよう、Person の物理削除をブロックする（仕様書 4.8 参照）。

### Step 7：actionlogs/models.py に ActionLog を新規作成

仕様書 4.10 を参照。**仕様書では duplicates 配下と書かれているが、本書では actionlogs アプリに配置**（本書 7.2 参照）。

#### Step 7-1：必要な import

```
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
```

#### Step 7-2：フィールド定義

- `id`：`UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`
- `user`：`ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')`
- `action`：`CharField(max_length=50)`（TextChoices ではなく自由文字列、別表 C.12 は参考値）
- `content_type`：`ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)`
- `object_id`：`CharField(max_length=255, null=True, blank=True)`（UUID 文字列を保持するため CharField）
- `content_object`：`GenericForeignKey('content_type', 'object_id')`
- `object_repr`：`CharField(max_length=255, blank=True, default='')`
- `diff`：`JSONField(null=True, blank=True)`
- `extra`：`JSONField(default=dict)`
- `note`：`TextField(blank=True, default='')`
- `created_at`：`DateTimeField(auto_now_add=True)`

ActionLog には `updated_at` は **作らない**（不変履歴ログのため）。仕様書 4.10 では created_at のみ。

### Step 8：マイグレーション生成

```
python manage.py makemigrations
```

期待される生成ファイル：

- `cards/migrations/`：変更なし（cards は触っていないので新規ファイル生成なし）
- `persons/migrations/0002_*.py`：Person への status / primary_contact / merged_into 追加
- `contacts/migrations/0002_*.py`：Contact への大量フィールド追加 + ContactFieldConfidence の CheckConstraint 追加
- `duplicates/migrations/0001_initial.py`：DuplicateCandidate / PersonMergeLog 新規
- `actionlogs/migrations/0001_initial.py`：ActionLog 新規

#### Step 8-1：循環参照の確認

Person ↔ Contact は循環参照（Person.primary_contact が Contact、Contact.person が Person）。Django は通常これを自動処理するが、生成されたマイグレーションファイルで以下を確認：

- 依存関係（`dependencies`）が persons → contacts → persons のような順序で正しく解決されているか
- Person.primary_contact が後付けの `migrations.AddField` で追加されている可能性がある（Django の自動処理）

異常があった場合は独自判断で修正せず、クロード君に相談すること。

#### Step 8-2：生成内容の確認

各マイグレーションファイルの operations を確認し、想定通りの操作が含まれていることを確認する。確認結果は完了報告に含める。

### Step 9：動作確認

```
python manage.py check
python manage.py makemigrations --dry-run
```

- `check` がエラーなく通ること
- `makemigrations --dry-run` で「No changes detected」と表示されること

### Step 10：migrate は実行しない

migrate の実行は **たんたんが手動で行う**。コード君は migrate を実行しない。

---

## 7. 重要な確認事項

### 7.1 DuplicateCandidate の match_reason / matched_fields 削除

仕様書 v1.4.2 統合最終版 4.7 / 別表 A.8 / 別表 C には `match_reason` / `matched_fields` フィールドが残っているが、**これは仕様書の消し忘れ**。最新方針は **削除**。

根拠：詳細仕様書 v0.1.5 の S-1 指摘で「ランクと一致フィールド表示で表現できるため別軸不要」と判断済み。

A-1c では **削除した状態で実装する**：

- `match_reason` フィールドを作らない
- `matched_fields` フィールドを作らない
- 内部 TextChoices `MatchReason` も作らない

別表 A.8 / 別表 C.5（仕様書）の修正は別途オーパス君に依頼する別タスク。

### 7.2 ActionLog の配置先変更

仕様書 v1.4.2 統合最終版では ActionLog が duplicates アプリ配下とされているが、**たんたん判断で actionlogs アプリ（独立アプリ）に配置**。

理由：ActionLog は GenericForeignKey で全モデル横断の汎用ログ。duplicates のドメインに閉じない。

A-1a で actionlogs アプリは作成済み。本書では actionlogs/models.py に ActionLog を作成する。

仕様書側（11.1 / 11.2 / 4.10 / 第10章 10.9 等）の修正は別途オーパス君に依頼する別タスク。

---

## 8. 完了基準

- `config/constants.py` に PersonChangeReason / DuplicateMergeReason / DifferentPersonReason / DUPLICATE_CHECK_FIELDS / DUPLICATE_GENERIC_EMAIL_LOCALPARTS が定義されている
- persons/models.py の Person に status / primary_contact / merged_into と Status 内部クラスが追加されている
- contacts/models.py の Contact に9フィールド + Status 内部クラス + partial unique constraint が追加されている
- contacts/models.py の ContactFieldConfidence に Confidence 内部クラス + CheckConstraint + save() オーバーライドが追加されている
- duplicates/models.py に DuplicateCandidate（match_reason / matched_fields なし）が定義されている
- duplicates/models.py に PersonMergeLog が定義されている
- actionlogs/models.py に ActionLog が定義されている（GenericForeignKey 含む）
- `python manage.py makemigrations` で各アプリのマイグレーションファイルが生成されている
- `python manage.py check` がエラーなく通る
- `python manage.py makemigrations --dry-run` で「No changes detected」と表示される

---

## 9. やってはいけないこと（厳守）

- **モデルメソッドを実装すること**（A-2 の作業：`fix()`, `mark_as_merged()`, `set_primary_contact()`, `get_active_contacts()`, `record_merge_action()`, `create_recovered_from()` など全て）
- `match_reason` / `matched_fields` フィールドを DuplicateCandidate に追加すること（削除方針）
- `MatchReason` の TextChoices を作ること（削除方針）
- ActionLog を duplicates アプリに作成すること（actionlogs アプリに作成する）
- `python manage.py migrate` を実行すること
- 既存の OriginalImage / BusinessCard を変更すること
- A-1b で生成された 0001_initial.py を編集・削除すること
- マイグレーションファイルを手動で編集すること（自動生成のまま使用）
- Form / View / Template を作成すること（D ブロックの作業）
- サービス層関数を実装すること（B / C ブロックの作業）
- URL ルーティングを変更すること（D ブロックの作業）
- 各 `apps.py` の `default_auto_field` を変更すること
- **コミット & プッシュを実行すること**（クロード君の確認後、たんたん経由で別途指示する）

---

## 10. 完了報告内容

作業完了後、以下を報告する。

- `config/constants.py` の追加内容（diff の概要、各 TextChoices の値が想定通りか）
- 各 `models.py` の変更概要（git diff --stat レベル）
- 生成されたマイグレーションファイル一覧（パス + 含まれる operation の概要）
- 各マイグレーションファイルの dependencies（特に persons / contacts の循環参照解決の確認）
- `python manage.py check` の出力
- `python manage.py makemigrations --dry-run` の出力
- 各モデルのフィールド数の概要（コード君がフィールドを書き漏らしていないかセルフチェック用）

---

## 11. 補足

### 11.1 想定される実装の難所

- **Contact.status の必須化**：`null=True` ではなく必須フィールド。既存データがあると問題になるが、A-1b で db.sqlite3 削除済みなので問題なし
- **Person ↔ Contact 循環参照**：Django が自動処理するが、生成されたマイグレーションを確認すること
- **partial unique constraint の SQLite 対応**：SQLite 3.8 以降サポート。Django 6.0 で問題なし
- **CheckConstraint の SQLite 対応**：SQLite 3.25 以降の `CHECK` 構文を使う。Django 6.0 で問題なし
- **GenericForeignKey の content_type 依存**：`django.contrib.contenttypes` が `INSTALLED_APPS` にあるか確認（Django デフォルトで含まれる）

### 11.2 ContactFieldConfidence の Confidence 書き換え

既存の `CONFIDENCE_LOW = "low"` / `CONFIDENCE_MEDIUM = "medium"` / `CONFIDENCE_CHOICES` の定数を **削除して** 内部クラス TextChoices に書き換える。既存のコードで `ContactFieldConfidence.CONFIDENCE_LOW` のような参照が残っていれば、`ContactFieldConfidence.Confidence.LOW` に書き換える必要がある（Step 0 のような事前調査をコード君が判断して実施すること）。

### 11.3 判断に迷ったら

実装中に判断に迷ったら、独自判断せずクロード君（サポート担当・たんたんとのチャットセッション）に確認すること。特に：

- 仕様書の記述と本書の記述が食い違う場合（基本は仕様書優先、ただし本書 7章で明示変更がある場合は本書優先）
- 循環参照のマイグレーションが期待通り生成されない場合
- フィールドの型・null 許容・default 値で迷う場合

---

**改訂履歴**

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v1.0 | 2026-05-06 | 初版作成 | クロード君（サポート担当） |
