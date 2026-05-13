# A-1a 実装指示書 ／ 新規アプリ作成

**FreeGroup2 v1.4.2 ／ コード君（Claude Code）向け**

---

## 1. 本書の位置づけ

本書は FreeGroup2 v1.4.2 の実装を進めるにあたり、コード君（Claude Code）が**最初に着手するステップ A-1a（新規アプリ作成のみ）**を定義する。

本書は v1.4.2 統合最終版仕様書（`docs/specs/v1_4_2/名刺画像取り込みOCR仕様書_v1_4_2統合最終版.md`）の補助文書として、コード君が独自判断する余地を最小化することを目的とする。

本書と仕様書の記述が食い違う場合、**仕様書 v1.4.2 統合最終版が優先する**。ただし「実装の進め方・作業ステップの分割」については本書を一次情報源とする。

---

## 2. 全体ブロックの俯瞰

v1.4.2 の実装は以下の4ブロックに分けて進める。本書で扱うのは **ブロック A の中の A-1a** のみ。

### 2.1 ブロック分割

| ブロック | 内容 | 主要ファイル |
|---|---|---|
| **A** | 基盤系（モデル・マイグレーション・モデルメソッド） | `*/models.py`、`*/migrations/`、`config/constants.py` |
| **B** | サービス層・タスク層（重複検出・スコア計算・履歴参照判断） | `duplicates/services/`、`duplicates/tasks/`、`contacts/services/` |
| **C** | マージ実行系（マージ実行・別人判定・復元） | `duplicates/services/merge_executor.py` |
| **D** | UI 層（View / Form / Template / 管理コマンド・URL ルーティング） | `*/views.py`、`*/forms.py`、`templates/`、`*/management/commands/` |

実装順序は **A → B → C → D**。前のブロックが完了してから次のブロックに着手する。

### 2.2 ブロック A のサブステップ

ブロック A はさらに以下に分割される。本書で扱うのは **A-1a** のみ。

| サブステップ | 内容 | 完了状態 |
|---|---|---|
| **A-1a** | **新規アプリ4つを作成（本書で扱う）** | 本書 |
| A-1b | cards アプリから Person / Contact / ContactFieldConfidence を移動 | 別途指示書 |
| A-1c | v1.4.2 のフィールド追加・新規モデル作成・TextChoices 定義 | 別途指示書 |
| A-2 | モデルメソッドの実装（30個以上） | 別途指示書 |

### 2.3 重要原則

- **コード君は本書で扱う A-1a の範囲のみ実装する**。A-1b 以降を勝手に進めない。
- **B / C / D の詳細サブステップは現時点で未確定**。クロード君（サポート担当）とたんたんが順次確定する。
- **コミット & プッシュはコード君が単独で行わない**。実装完了報告後、クロード君（レビュー担当）の確認を経てたんたんから別途指示する。

---

## 3. A-1a の目的

FreeGroup2 v1.4.2 で必要な4つの新規アプリを作成し、Django プロジェクトに登録する。

**この時点ではモデル定義もマイグレーション生成も行わない**。アプリの「箱」を作るだけ。モデル・フィールド・マイグレーションは後続の A-1b / A-1c で実施する。

---

## 4. 前提（たんたんが事前に実施済み）

コード君が作業を開始する時点で、以下はすべて完了している。

- 両PCで `git pull origin main` 実施済み
- 自宅PCで `feature/v1.4.2-models` ブランチ作成済み
- コード君は **`feature/v1.4.2-models` ブランチ上で作業する**

---

## 5. 作成するアプリ（4つ）

仕様書 v1.4.2 統合最終版 11.1 / 11.2 をベースに、**ActionLog のみ独立アプリ（actionlogs）に変更**している（たんたん判断による方針変更）。

| アプリ名 | 用途（A-1c で配置するモデル） | 補足 |
|---|---|---|
| `persons` | Person | 人物本体（A-1b で cards から移動） |
| `contacts` | Contact / ContactFieldConfidence | 連絡先と信頼度（A-1b で cards から移動） |
| `duplicates` | DuplicateCandidate / PersonMergeLog | 重複検出（A-1c で新規作成） |
| `actionlogs` | ActionLog | 汎用ログ（A-1c で新規作成）／**仕様書から変更** |

ActionLog を独立アプリとする理由：ActionLog は GenericForeignKey で全モデル横断の汎用ログであり、duplicates のドメインに閉じない設計のため。仕様書側（11.1 / 11.2 / 4.10 / 第10章 10.9 等）の修正は別途オーパス君（ドキュメント作成担当）に依頼する別タスク。

---

## 6. 作業手順

### Step 1：新規アプリ4つを作成

プロジェクトルートで以下を順に実行する。

```
python manage.py startapp persons
python manage.py startapp contacts
python manage.py startapp duplicates
python manage.py startapp actionlogs
```

各コマンドにより、それぞれのアプリディレクトリが生成される（`models.py` / `apps.py` / `views.py` / `admin.py` / `tests.py` / `migrations/__init__.py` 等）。

### Step 2：INSTALLED_APPS に登録

`config/settings.py` の `INSTALLED_APPS` リストに、4つのアプリを追加する。追加位置は **既存 `cards` アプリの直下**。

追加するアプリ名：

- `'persons'`
- `'contacts'`
- `'duplicates'`
- `'actionlogs'`

### Step 3：apps.py の確認

各アプリの `apps.py` で `default_auto_field = 'django.db.models.BigAutoField'` がデフォルトになっているはず。**この値は変更しない**。

仕様書では UUIDField を主キーに使うが、それは A-1c の各モデル定義で個別に `UUIDField(primary_key=True, ...)` で指定する方針。`default_auto_field` はそのままで良い。

`verbose_name` 等の日本語名は **A-1a では入れない**。A-1c でモデル定義と一緒に追加する。

### Step 4：動作確認

以下のコマンドを実行して、エラーが出ないことを確認する。

```
python manage.py check
python manage.py makemigrations --dry-run
```

`makemigrations --dry-run` では「**No changes detected**」と表示されること。マイグレーションファイルは生成されない（モデルが空なので変更なし）。

---

## 7. 完了基準

- 4つのアプリディレクトリ（`persons` / `contacts` / `duplicates` / `actionlogs`）が生成されている
- `INSTALLED_APPS` に4つが登録されている
- `python manage.py check` がエラーなく通る
- `python manage.py makemigrations --dry-run` で「No changes detected」と表示される

---

## 8. やってはいけないこと（厳守）

- 各アプリの `models.py` にモデルクラスを追加すること（**A-1c の作業**）
- マイグレーションファイルを生成すること（モデルがないので何も生成されないが、念のため明記）
- `python manage.py migrate` を実行すること
- 既存の `cards` アプリのファイル（`cards/models.py` 等）を編集すること
- DB を削除すること
- 既存データを削除すること
- `apps.py` の `default_auto_field` を変更すること
- 各アプリの `views.py` / `admin.py` / `urls.py` などに何かを追加すること（このステップでは models.py 含めて空のまま）
- **コミット & プッシュを実行すること**（クロード君の確認後、たんたん経由で別途指示する）

---

## 9. 完了報告内容

作業完了後、以下を報告する。

- 各 `startapp` コマンドの実行結果
- `settings.py` の差分（`INSTALLED_APPS` の変更箇所）
- `python manage.py check` の出力
- `python manage.py makemigrations --dry-run` の出力
- 生成された各アプリのディレクトリ構成（`dir persons\` または `ls persons/` の出力）

---

## 10. 補足

- 本書は A-1（モデル骨組み + マイグレーション生成）の中の **A-1a（アプリ作成のみ）**
- 後続の A-1b（モデル移動）、A-1c（フィールド追加と新規モデル作成）で本格的な作業が入る
- A-1a 単独では、ユーザー視点での機能変化はゼロ（DB も変わらない、画面も変わらない）
- Windows コマンドプロンプト・PowerShell どちらでも動く前提で記述している（`python` コマンドが使える前提）
- 実装中に判断に迷ったら、独自判断せずクロード君（サポート担当・たんたんとのチャットセッション）に確認すること

---

**改訂履歴**

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v1.0 | 2026-05-06 | 初版作成 | クロード君（サポート担当） |
