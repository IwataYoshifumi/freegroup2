# A-2e 実装指示書 ／ ActionLog + OriginalImage / BusinessCard のモデルメソッド実装

**FreeGroup2 v1.4.2 ／ コード君B（Claude Code）向け**

---

## 1. 本書の位置づけ

A-2 ブロック（モデルメソッド実装、第10章）の最終サブステップ。A-2a（Person）、A-2b（Contact + ContactFieldConfidence）、A-2c（DuplicateCandidate）、A-2d（PersonMergeLog）は完了済み。本書は A-2e（ActionLog + OriginalImage / BusinessCard のモデルメソッド実装）を担当する。本サブステップ完了で A-2 ブロック全体が完了する。

A-2e は3アプリ横断の実装：

| アプリ | 対象モデル | 実装対象 |
|---|---|---|
| `actionlogs/models.py` | ActionLog | フィールド修正 + クラスメソッド1個 |
| `cards/models.py` | OriginalImage | クラスメソッド2個 + インスタンスメソッド2個 |
| `cards/models.py` | BusinessCard | インスタンスメソッド2個 |
| `duplicates/models.py` | PersonMergeLog | インスタンスメソッド2個（A-2d から送り込み） |
| `duplicates/models.py` | DuplicateCandidate | インスタンスメソッド1個（A-2c から送り込み） |

合計：10メソッド + ActionLog のフィールド修正2点 + マイグレーション。

A-2e の特殊性：**ActionLog のフィールド修正（`diff` 削除・`extra` → `data` 改名）が含まれる**。これは A-1c で完成した DB スキーマの修正にあたるが、A-2e の中で「ActionLog 関連の最終形を確定する」テーマの一部として実施する（たんたんの判断）。

---

## 2. 実装対象メソッド一覧

### 2.1 actionlogs/models.py の修正

#### フィールド修正

| 項目 | 修正内容 |
|---|---|
| `diff` フィールド | **削除** |
| `extra` フィールド | **`data` に改名**（型・default は同じ：JSONField, default=dict） |

#### クラスメソッド（1個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 1 | `ActionLog.record(user, action, content_object=None, object_repr='', data=None, note='')` | 任意の業務イベントを直接記録（cron 実行ログなど、モデルインスタンスを持たない場面で使用） | 10.9.1 |

仕様書 §10.9.1 では引数名が `extra=None` となっているが、フィールド改名に合わせて `data=None` で実装する。

### 2.2 cards/models.py の OriginalImage クラス

#### クラスメソッド（2個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 2 | `OriginalImage.get_pending(limit)` | pending な OriginalImage を limit 件取得（cron 用） | 10.10.1 |
| 3 | `OriginalImage.release_stuck_locks(threshold_minutes)` | stuck な processing レコードを pending に戻す | 10.10.1 |

#### インスタンスメソッド（2個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 4 | `original_image.get_image_url()` | サムネイル用 URL を返す | 10.10.2 |
| 5 | `original_image.get_image_url_full()` | フルサイズ用 URL を返す | 10.10.2 |

### 2.3 cards/models.py の BusinessCard クラス

#### インスタンスメソッド（2個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 6 | `business_card.get_card_image_url()` | サムネイル用 URL を返す | 10.10.3 |
| 7 | `business_card.get_card_image_url_full()` | フルサイズ用 URL を返す | 10.10.3 |

### 2.4 duplicates/models.py の PersonMergeLog クラス（A-2d から送り込み）

#### インスタンスメソッド（2個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 8 | `merge_log.record_merge_action(user)` | マージ実行を ActionLog に記録（action='merged'） | 10.8.2 |
| 9 | `merge_log.record_undo_action(user)` | 復元実行を ActionLog に記録（action='undone'） | 10.8.2 |

### 2.5 duplicates/models.py の DuplicateCandidate クラス（A-2c から送り込み）

#### インスタンスメソッド（1個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 10 | `candidate.record_different_person_action(user)` | 自身の別人判定操作を ActionLog に記録（action='different_person'） | 10.7.2 |

### 2.6 マイグレーション

`actionlogs/migrations/` 配下に新規マイグレーションファイルを1つ追加：

- `diff` フィールド削除
- `extra` → `data` フィールド改名

`python manage.py makemigrations actionlogs` で自動生成される（Django が変更を検出する）。ただし、**フィールド改名を Django が「削除→新規追加」と誤検出する場合は、手動で `migrations.RenameField` に修正**する。詳細は §3.4 を参照。

---

## 3. 【最重要】設計思想：このメソッドは何のためにあるか

このセクションは、過去のレビュアー（GPT 系・Opus 系・複数のクロード君）が繰り返し誤解してきた箇所と、A-2e 特有の設計判断を扱う。**実装中に「ここおかしくないか？」「もっと別の構造の方がいいのでは？」と感じたら、必ず本セクションを再読すること**。

A-2e には誤解しがちなポイントが4つある。

- 3.1：GenericForeignKey の `content_object` 引数の自動展開
- 3.2：状態遷移と ActionLog 記録の分離（連携メソッド3個の責務）
- 3.3：FK で取れる情報を `data` に冗長コピーしない原則（たんたんの設計判断）
- 3.4：`diff` 削除 / `extra` → `data` 改名の背景とマイグレーション

### 3.1 GenericForeignKey の挙動：`content_object` 引数で自動展開される

`ActionLog.record(...)` の引数 `content_object` は**Django モデルインスタンスを渡せば、内部で `content_type` と `object_id` が自動セットされる**。これは Django の GenericForeignKey の仕様。

例：

```python
ActionLog.record(
    user=user,
    action='merged',
    content_object=merge_log,  # PersonMergeLog インスタンス
    data={...}
)
```

`content_object=merge_log` を渡すと、内部で：
- `content_type` = ContentType.objects.get_for_model(PersonMergeLog) が自動セット
- `object_id` = str(merge_log.pk) が自動セット

になる。

逆に `content_object=None` のとき（cron 実行ログ等、モデルインスタンスを持たない場面）：
- `content_type = None`
- `object_id = None`

になる。仕様書 §4.10 の `content_type` / `object_id` の null 許容は、この cron 実行ログのケースのため。

**ここが過去レビュアーが誤解しやすい箇所**：「`content_type` と `object_id` を別々に引数で渡さないとダメでは？」と思いがち。Django の GenericForeignKey の標準挙動を信頼して、`content_object` だけを引数に持つ設計にする。

### 3.2 状態遷移と ActionLog 記録の分離

`merge_log.record_merge_action(user)` / `merge_log.record_undo_action(user)` / `candidate.record_different_person_action(user)` の3メソッドは、**自身の状態を変更しない**。これらは「ActionLog にレコードを書き込むだけ」の単一責任メソッド。

仕様書 §10.8.4 でこの設計趣旨が明示されている：

- `mark_as_*()` は状態遷移だけ
- `record_*_action()` はログ記録だけ
- マージ実行のフローでは両方を順に呼ぶ

理由：

- 単一責任：状態遷移と記録が別メソッドで明確
- テスト容易性：状態遷移と記録が分離されているのでテストしやすい
- 例外処理：もしログ記録だけ失敗した場合、状態遷移は成功している方がシンプル

**ここが過去レビュアーが誤解しやすい箇所**：「`record_*_action()` の中で対象モデルの状態も同時に更新すべきでは？」と思いがち。実装中に状態更新を入れない。`record_*_action()` は ActionLog に書き込むだけ。

### 3.3 FK で取れる情報を `data` に冗長コピーしない原則

3つの連携メソッドの `data` フィールドの構造は、たんたんの設計判断で**FK で取れる情報を冗長コピーしない**原則を徹底する。

#### `merge_log.record_merge_action(user)` の `data`

```python
{
    "merge_reason": "transfer",                # 仕様書 C.8 の DuplicateMergeReason 7値
    "updated_fields": ["full_name", "company"] # 更新したフィールド名リスト、空リストなら更新なし
}
```

- `surviving_person_id` / `merged_person_id` / `duplicate_candidate_id` は `data` に**入れない**
- なぜなら：`content_object = merge_log` で merge_log への参照を持つので、`merge_log.surviving_person_id` / `merge_log.merged_person_id` / `merge_log.duplicate_candidate_id` で取れる
- `data` には**マージ実行時の業務情報のみ**（merge_reason、updated_fields）

#### `merge_log.record_undo_action(user)` の `data`

```python
{}
```

- 空 dict
- `surviving_person_id` / `merged_person_id` は merge_log 経由で取れる
- 復元自体には追加の業務情報がない
- 仕様書 §10.8.2 の「extra に復元理由＋復元内容として surviving 側 Person・復元側 Person のインスタンス情報を JSON で保存」は、たんたんの判断で**FK で取れる情報を冗長コピーしない原則**に置き換える

#### `candidate.record_different_person_action(user)` の `data`

```python
{
    "different_reason": "same_name"  # 仕様書 C.9 の DifferentPersonReason 3値
}
```

- `person_a_id` / `person_b_id` は `data` に**入れない**
- なぜなら：`content_object = candidate` で candidate への参照を持つので、`candidate.person_a_id` / `candidate.person_b_id` で取れる
- `data` には**別人判定理由のみ**

#### updated_fields の引数の受け取り方

`merge_log.record_merge_action(user)` の引数は **`user` のみ**。`updated_fields` は引数で受け取らず、**呼び出し側（マージ実行サービス層、B ブロック以降で実装）が `merge_log.note` に保存しておく仕組み**を使うか、別メソッドで渡す設計にする。

A-2e の段階では、シンプルに**`merge_log.record_merge_action(user, updated_fields=None)` 的に第2引数で受け取る形**で実装してよい。引数が増えるが、業務情報の流入経路として明確。

`merge_reason` も同様に第2引数（または別引数）で受け取る：

```python
def record_merge_action(self, user, merge_reason='', updated_fields=None):
    ...
```

詳細な引数構成はコード君B の判断に任せる。ただし**`data` の構造は §3.3 の通り**で固定。

**ここが過去レビュアーが誤解しやすい箇所**：「`data` に Person ID を入れた方が分析しやすいのでは？」と思いがち。**FK で取れる情報を `data` に冗長コピーしない**原則を一貫適用する。理由：

- ActionLog の役割は「業務固有の追加情報を記録する」
- FK で取れる情報は ActionLog から `select_related` で取れる
- 冗長コピーは「データの2重管理」を引き起こし、整合性を保つコストが高い
- KPI 分析時に SQL が複雑になるかもしれないが、それは読み手側の責務

### 3.4 `diff` 削除 / `extra` → `data` 改名の背景

#### 経緯

仕様書 §4.10 では `ActionLog` モデルに `diff` フィールド（JSONField, null=True）と `extra` フィールド（JSONField, default=dict）の2つが定義されていた。A-1c でこの定義通りに実装・マイグレーション適用済み。

しかし A-2e 着手時のたんたんレビューで以下の判断が確定：

1. **`diff` フィールド削除**：v1.4.2 で `diff` を使うシナリオが0個（仕様書 §4.11.3 の記録対象5つすべて `extra` に書く設計）。Django 標準 LogEntry のような汎用監査ログではないので、`diff` は不要
2. **`extra` → `data` 改名**：1本化された JSON フィールドの命名として、「追加」を意味する `extra` よりも「中身そのもの」を意味する `data` の方が実態に合う

#### マイグレーション

`actionlogs/migrations/` 配下に新規マイグレーションファイルを1つ追加する。Django の `makemigrations` が自動で以下を生成する想定：

```
operations = [
    migrations.RemoveField(
        model_name='actionlog',
        name='diff',
    ),
    migrations.RenameField(
        model_name='actionlog',
        old_name='extra',
        new_name='data',
    ),
]
```

ただし Django が `RenameField` ではなく「`extra` 削除 + `data` 新規追加」と誤検出する可能性がある。その場合は対話プロンプトで「Did you rename actionlog.extra to actionlog.data (a JSONField)?」と聞かれるので **`y` を入力**する。または、生成された自動マイグレーションを手動で `RenameField` に修正する。

データの保全：開発DBには `actionlogs_actionlog` テーブルに既存レコードがある可能性は低い（v1.4.2 はまだ運用前）が、**`RenameField` を使えばデータ保全される**ので、誤って `RemoveField` + `AddField` のマイグレーションを通さないこと。

#### 仕様書修正の経緯

`diff` 削除 / `extra` → `data` 改名は仕様書 §4.10 / §4.11 / §10.7.2 / §10.8.2 / §10.9 / §12.10 等に多数の記述があるが、**A-2e のスコープでは仕様書本文の修正は行わない**。これらは別途オーパス君（ドキュメント作成担当）に依頼するタスクとして、たんたんが管理する。

A-2e のコード実装時点では、コード側の真実が **「`diff` なし、`data` フィールド」** で確定。仕様書本文と差異が出るが、これはコード優先で進める。

---

## 4. やる範囲

### 4.1 配置場所

| 対象 | 配置先 |
|---|---|
| ActionLog のフィールド修正（diff 削除・extra→data 改名） | `actionlogs/models.py` |
| ActionLog のマイグレーション | `actionlogs/migrations/000X_*.py`（新規） |
| `ActionLog.record()` クラスメソッド | `actionlogs/models.py` の `ActionLog` クラス |
| OriginalImage のメソッド4個 | `cards/models.py` の `OriginalImage` クラス |
| BusinessCard のメソッド2個 | `cards/models.py` の `BusinessCard` クラス |
| PersonMergeLog の連携メソッド2個 | `duplicates/models.py` の `PersonMergeLog` クラス |
| DuplicateCandidate の連携メソッド1個 | `duplicates/models.py` の `DuplicateCandidate` クラス |

### 4.2 実装の順序

以下の順序で進めることを推奨：

1. **ActionLog のフィールド修正**（diff 削除・extra→data 改名）
2. **マイグレーション生成・適用**（`makemigrations` → `migrate`、§3.4 の RenameField を確認）
3. **`ActionLog.record()` クラスメソッド実装**
4. **OriginalImage / BusinessCard のメソッド実装**（cards/models.py、ActionLog に依存しない独立メソッド）
5. **PersonMergeLog の連携メソッド2個実装**（duplicates/models.py、ActionLog.record() に依存）
6. **DuplicateCandidate の連携メソッド1個実装**（duplicates/models.py、ActionLog.record() に依存）

### 4.3 各メソッドの仕様

仕様書の以下の節を一次情報源とする：

- ActionLog 関連 → §4.10 / §4.11 / §10.9 / §12.10
- OriginalImage / BusinessCard → §4.2 / §4.3 / §10.10
- PersonMergeLog 連携 → §10.8.2
- DuplicateCandidate 連携 → §10.7.2

ただし以下の点で**仕様書からの逸脱**がある（§5 で詳述）：

- ActionLog のフィールド `diff` は削除、`extra` は `data` に改名
- ActionLog 連携メソッドの `data` 構造は §3.3 のたんたんの設計判断に従う

### 4.4 `get_image_url()` 系メソッドの実装方式

`OriginalImage.get_image_url()` / `OriginalImage.get_image_url_full()` / `BusinessCard.get_card_image_url()` / `BusinessCard.get_card_image_url_full()` の4メソッドは、たんたんの判断で**`image_field.url` をそのまま返すシンプル実装**で確定：

```python
def get_image_url(self):
    return self.image_file.url if self.image_file else ''

def get_image_url_full(self):
    return self.image_file.url if self.image_file else ''
```

サムネイルとフルサイズで戻り値が同じになる。サムネイル化は **CSS 側（`width: 200px; height: auto;` 等）で対応**する設計。

理由：

- v1.4.2 で過剰実装を避ける（YAGNI）
- 画像アップロード上限が 5MB と小さいので、ブラウザ側リサイズで実用上問題ない
- 将来 v1.5.0 以降で性能問題が出たら easy_thumbnails 等を導入する判断を後回しにできる
- メソッドを2つに分けて残しておく理由：将来サムネイル生成方式を変更するときに、メソッドシグネチャの変更を避けるため（サムネイル側だけ実装変更すればよい）

### 4.5 `OriginalImage.get_pending(limit)` の責務

cron（OCR バッチ処理）から呼ばれる。以下のクエリを返す：

- `status='pending'` の OriginalImage を新しい順（または古い順？コード君B の判断）で `limit` 件
- ただし**ロックは取らない**（タスク層側で `select_for_update(skip_locked=True)` を使う前提）
- 戻り値は QuerySet

### 4.6 `OriginalImage.release_stuck_locks(threshold_minutes)` の責務

stuck な processing レコードを pending に戻す処理。

- `status='processing'` かつ `claimed_at` が `threshold_minutes` 分前より古いレコードを抽出
- それらの `status` を `'pending'` に戻す（`claimed_at` は NULL に戻す or そのまま、コード君B の判断）
- 一括 update でよい（ロック取得不要）

仕様書 §19.2 / §20.1 で「stuck sweeper」と呼ばれている処理。CAS 楽観ロック方式と組み合わせて使う前提。

### 4.7 ActionLog 連携メソッド3個の `data` 構造

設計思想 §3.3 の通り：

| メソッド | data の中身 |
|---|---|
| `record_merge_action(user, merge_reason, updated_fields)` | `{"merge_reason": "...", "updated_fields": [...]}` |
| `record_undo_action(user)` | `{}` |
| `record_different_person_action(user, different_reason)` | `{"different_reason": "..."}` |

`merge_reason` / `updated_fields` / `different_reason` の引数の受け取り方はコード君B の判断に委ねる（位置引数 / キーワード引数 / デフォルト値の有無など）。ただし**`data` の構造は上表で固定**。

各メソッドの内部実装は基本的に `ActionLog.record(...)` を呼ぶラッパー：

```python
# 概念的な構造（実装はコード君Bの判断）
def record_merge_action(self, user, merge_reason='', updated_fields=None):
    ActionLog.record(
        user=user,
        action='merged',
        content_object=self,  # merge_log
        data={
            "merge_reason": merge_reason,
            "updated_fields": updated_fields or [],
        },
    )
```

---

## 5. 仕様書からの変更点

### 5.1 ActionLog のフィールド変更

| 仕様書記述 | 実装側の真実 |
|---|---|
| §4.10：`diff | JSONField (null=True)` | **削除**（コード側に存在しない） |
| §4.10：`extra | JSONField (default=dict)` | **`data` に改名**（実装側のフィールド名は `data`） |

### 5.2 ActionLog のメソッド引数

| 仕様書記述 | 実装側の真実 |
|---|---|
| §10.9.1：`ActionLog.record(... extra=None ...)` | **`data=None`** に改名 |

### 5.3 ActionLog 連携メソッドの `data` 構造

仕様書 §10.7.2 / §10.8.2 では「extra に〜を格納」と抽象的な記述だが、たんたんの設計判断で具体構造が確定（設計思想 §3.3 参照）：

| メソッド | 仕様書の抽象記述 | 実装側の具体構造 |
|---|---|---|
| `record_merge_action` | extra に surviving/merged Person 情報、duplicate_candidate ID 等 | `{"merge_reason": "...", "updated_fields": [...]}`（FK で取れる ID は冗長コピーしない） |
| `record_undo_action` | extra に復元理由＋復元内容として surviving 側 Person・復元側 Person のインスタンス情報 | `{}`（FK で取れる情報のみで足りる） |
| `record_different_person_action` | extra に判定理由を格納 | `{"different_reason": "..."}` |

### 5.4 アプリ配置

仕様書 §10.9.1 表の「配置先」欄では「duplicates/models.py または共通アプリ」と記載されているが、メモリ #28 で **`actionlogs` アプリとして独立**させることが確定済み。配置先は `actionlogs/models.py`。

### 5.5 仕様書本文の修正は別タスク

§5.1〜§5.4 の差異は、別途オーパス君（ドキュメント作成担当）への仕様書修正タスクとして処理される。A-2e のスコープでは仕様書本文には触らない。

---

## 6. やってはいけないこと（厳守）

### 6.1 設計思想に反する実装

- **ActionLog 連携メソッドの中で対象モデルの状態を更新すること**（厳禁）。設計思想 §3.2 の通り、これらは「ActionLog に書き込むだけ」の単一責任メソッド。状態遷移は `mark_as_*()` 系の責務
- **FK で取れる Person ID 等を `data` に冗長コピーすること**（厳禁）。設計思想 §3.3 の通り、`content_object` の参照経由で取れる情報は `data` に入れない
- **GenericForeignKey の `content_type` / `object_id` を別々の引数で受け取るインターフェース設計にすること**（厳禁）。設計思想 §3.1 の通り、`content_object` 1つで Django が自動展開する
- **画像 URL メソッドで easy_thumbnails 等のサードパーティライブラリを導入すること**（厳禁）。`image_field.url` をそのまま返す

### 6.2 マイグレーション関連の禁止事項

- **`extra` → `data` の改名で、データロスを発生させる実装をすること**（厳禁）。Django が誤って `RemoveField` + `AddField` のマイグレーションを生成する場合、それを通さない。`RenameField` を使う（手動修正が必要な場合は §3.4 参照）
- **既存の他モデル（OriginalImage / BusinessCard / PersonMergeLog 等）のフィールド定義を変更すること**（厳禁）。A-2e は ActionLog のフィールド修正のみ
- **A-2c / A-2d で実装済みのメソッドを変更すること**（厳禁）。A-2e は新規メソッドの追加のみ

### 6.3 スコープ外の実装

- A-2e 以外のメソッドを実装すること
  - サービス層（マージ実行・OCR 処理・cron 等）は B ブロック以降の責務
  - View 層・Form 層は C / D ブロックの責務
- 仕様書本文の修正（オーパス君タスク）
- メモ書きや TODO コメントの大量挿入

### 6.4 既存コード・データへの影響

- 既存の docstring コメントを削除すること
- 開発DBのデータを意図せず削除すること（マイグレーションで `extra` → `data` 改名は `RenameField` で**データ保全される**）
- `actionlogs/migrations/0001_initial.py` を直接編集すること（必ず新規マイグレーションファイルを追加する）

### 6.5 運用ルール

- コミット & プッシュを実行すること（実装完了後、クロード君（サポート担当）の確認を経てから別途指示する）

---

## 7. 動作確認観点

実装完了後、Django shell で以下の観点を確認する。テストデータは Django shell から作成してよい（自宅PC・実家PCともに開発DBは削除OK方針）。

### 7.1 ActionLog のフィールド修正

- `python manage.py check` がパス
- `python manage.py makemigrations` で新規マイグレーションファイルが生成される
- 生成されたマイグレーションが `RenameField('extra', 'data')` と `RemoveField('diff')` を含む（Django が誤検出した場合は手動修正済み）
- `python manage.py migrate` がエラーなく適用される
- 適用後、DB の `actionlogs_actionlog` テーブルに `data` カラムが存在し、`diff` / `extra` カラムが存在しない

### 7.2 `ActionLog.record()`

- `ActionLog.record(user=test_user, action='executed', object_repr='check_duplicates', data={"key": "value"})` を呼ぶ
- 戻り値が ActionLog インスタンスで、DB に save 済み
- 確認：
  - `record.user == test_user`
  - `record.action == 'executed'`
  - `record.object_repr == 'check_duplicates'`
  - `record.content_type is None` / `record.object_id is None`（content_object なしのケース）
  - `record.data == {"key": "value"}`
  - `record.note == ''`
  - `record.created_at` が呼び出し時刻範囲内
- `content_object` を渡したケース：
  - 任意のモデルインスタンス（例：merge_log）を `content_object` に渡す
  - `record.content_type` が PersonMergeLog の ContentType を指している
  - `record.object_id == str(merge_log.pk)`
  - `record.content_object == merge_log`（GenericForeignKey で逆参照できる）

### 7.3 `OriginalImage.get_pending(limit)`

- 任意の OriginalImage を status 違いで複数件作成（pending / processing / extracted / garbage / failed を混在）
- `OriginalImage.get_pending(limit=5)` を呼ぶ
- 戻り値が QuerySet で、`status='pending'` のものだけが返る
- 件数が `limit` 以下である
- 戻り値の順序は何らかの一貫性がある（created_at の昇順または降順）

### 7.4 `OriginalImage.release_stuck_locks(threshold_minutes)`

- 任意の OriginalImage を `status='processing'`、`claimed_at` を「現在より40分前」として作成
- 別に `status='processing'`、`claimed_at` を「現在より10分前」のレコードも作成
- `OriginalImage.release_stuck_locks(threshold_minutes=30)` を呼ぶ
- 確認：
  - 40分前のレコードは `status='pending'` に戻っている
  - 10分前のレコードは `status='processing'` のまま
- `status='extracted'` 等の他のステータスのレコードは影響を受けないこと

### 7.5 `original_image.get_image_url()` / `get_image_url_full()`

- 任意の OriginalImage を作成（`image_file` に画像をセット）
- `original_image.get_image_url()` と `original_image.get_image_url_full()` の戻り値が同じ URL（`image_file.url`）
- `image_file` が空の場合、空文字列または None が返る（実装方式による）

### 7.6 `business_card.get_card_image_url()` / `get_card_image_url_full()`

- §7.5 と同様に BusinessCard の `card_image` で確認

### 7.7 `merge_log.record_merge_action(user, ...)`

- 任意の PersonMergeLog インスタンス `merge_log` を作成（A-2d で実装済みの `PersonMergeLog.create()` を使う）
- `merge_log.record_merge_action(user=test_user, merge_reason='transfer', updated_fields=['full_name'])` を呼ぶ
- ActionLog のレコードが1件作成されたこと
- 確認：
  - `actionlog.action == 'merged'`
  - `actionlog.user == test_user`
  - `actionlog.content_object == merge_log`
  - `actionlog.data == {"merge_reason": "transfer", "updated_fields": ["full_name"]}`
  - `actionlog.data` に `surviving_person_id` / `merged_person_id` が**含まれていない**こと（FK で取れる情報の冗長コピーしない原則の検証）
- `merge_log` 自身の状態が変わっていないこと（status / executed_by / executed_at 等が record_merge_action 呼び出し前後で同じ）

### 7.8 `merge_log.record_undo_action(user)`

- §7.7 と同じセットアップで `merge_log.record_undo_action(user=test_user)` を呼ぶ
- ActionLog のレコードが1件作成されたこと
- 確認：
  - `actionlog.action == 'undone'`
  - `actionlog.user == test_user`
  - `actionlog.content_object == merge_log`
  - `actionlog.data == {}`（空 dict）
- `merge_log` 自身の状態が変わっていないこと

### 7.9 `candidate.record_different_person_action(user, ...)`

- 任意の DuplicateCandidate インスタンス `candidate` を作成
- `candidate.record_different_person_action(user=test_user, different_reason='same_name')` を呼ぶ
- ActionLog のレコードが1件作成されたこと
- 確認：
  - `actionlog.action == 'different_person'`
  - `actionlog.user == test_user`
  - `actionlog.content_object == candidate`
  - `actionlog.data == {"different_reason": "same_name"}`
  - `actionlog.data` に `person_a_id` / `person_b_id` が**含まれていない**こと
- `candidate` 自身の状態が変わっていないこと（review_status / reviewed_by 等）

### 7.10 全体確認

- `python manage.py check` がパス
- `python manage.py makemigrations --dry-run` で「No changes detected」が表示されること（マイグレーションファイル追加後の状態で）
- A-2c / A-2d で実装済みの既存メソッドが動作すること（リグレッションが起きていないこと）

---

## 8. 完了報告内容

作業完了後、以下を報告してほしい。

1. 実装したメソッド一覧（10個 + フィールド修正2点）と各メソッドの行数
2. `actionlogs/models.py` の差分の概要（フィールド修正の前後）
3. 新規マイグレーションファイルの内容（`RenameField` / `RemoveField` の operations）
4. `cards/models.py` の差分の概要（OriginalImage / BusinessCard へのメソッド追加）
5. `duplicates/models.py` の差分の概要（PersonMergeLog / DuplicateCandidate へのメソッド追加）
6. `python manage.py check` の出力
7. `python manage.py makemigrations --dry-run` の出力（マイグレーションファイル追加後）
8. 動作確認結果（7.1〜7.10 の各観点で実行した内容と結果）
9. ActionLog 連携メソッドの引数構成（`merge_reason` / `updated_fields` / `different_reason` をどう受け取る設計にしたか、その判断理由）
10. 実装中に判断に迷った点・気になった点（あれば）
11. 申し送りメモへの追記候補（A-2e で得た知見で B ブロック以降に役立つもの。なければ「なし」と報告）

---

## 9. 補足

### 9.1 関連メソッドの実装タイミング

A-2e で10メソッド + フィールド修正を実装する。残りの周辺機能は以下のタイミングで実装：

- マージ実行サービス層（`merge_log.record_merge_action()` を呼ぶ側） → B ブロック
- 復元実行サービス層（`merge_log.record_undo_action()` を呼ぶ側） → B ブロック
- 別人判定サービス層（`candidate.record_different_person_action()` を呼ぶ側） → B ブロック
- cron 重複チェック実行（`ActionLog.record(...)` 直接呼び） → B ブロック
- OCR 処理結果記録（`ActionLog.record(...)` 直接呼び） → B ブロック以降
- OCR バッチ処理（`OriginalImage.get_pending()` / `release_stuck_locks()` を呼ぶ側） → B ブロック以降
- 画像表示（`get_image_url()` / `get_card_image_url()` を呼ぶ側のテンプレート・カスタムタグ） → D ブロック

A-2e では各メソッドが**単体で動くモデルメソッド**として実装すれば足りる。Django shell から直接呼び出せて、戻り値・副作用が確認できる状態にする。

### 9.2 困ったときは

仕様書（v1.4.2 統合最終版）の以下の節を参照：

- ActionLog のフィールド定義 → §4.10
- ActionLog と PersonMergeLog の関係 → §4.11
- ActionLog に記録する対象 → §4.11.3
- ActionLog 書き込み失敗時のフォールバック → §4.11.4（A-2e ではフォールバック実装はしないが、将来の B ブロック以降で実装される）
- OriginalImage のフィールド定義 → §4.2
- BusinessCard のフィールド定義 → §4.3
- ActionLog のメソッド責務 → §10.9
- OriginalImage / BusinessCard のメソッド責務 → §10.10
- 重複チェックの実行ログ（`ActionLog.record(...)` の使用例） → §12.10
- TextChoices（OriginalImage.Status / DuplicateMergeReason / DifferentPersonReason） → §14.4 / 別表 C

仕様書だけで判断に迷う場合は、たんたんに確認してほしい（クロード君が壁打ちで対応する）。独自判断で実装を進めない。

特に「設計思想 §3.1 / §3.2 / §3.3」のいずれかで「これは仕様書と違う」「もっと別の構造がいいのでは」と感じたら、実装を止めて先に確認すること。これらは過去レビュアーが繰り返し誤解した箇所、またはたんたんの設計判断で確定した箇所であり、仕様書本文との差異がある。

---

**（指示書終わり）**
