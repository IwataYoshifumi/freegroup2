# A-1b 実装指示書 ／ モデル移動と import 書き換え

**FreeGroup2 v1.4.2 ／ コード君（Claude Code）向け**

---

## 1. 本書の位置づけ

本書は FreeGroup2 v1.4.2 の実装の **A-1b（cards から persons / contacts へのモデル移動）** を定義する。

本書は v1.4.2 統合最終版仕様書（`docs/specs/v1_4_2/名刺画像取り込みOCR仕様書_v1_4_2統合最終版.md`）の補助文書として、コード君が独自判断する余地を最小化することを目的とする。

本書と仕様書の記述が食い違う場合、**仕様書 v1.4.2 統合最終版が優先する**。ただし「実装の進め方・作業ステップの分割」については本書を一次情報源とする。

A-1a（新規アプリ4つ作成）は完了済み。本書はその次のステップ。

---

## 2. 全体ブロックの俯瞰（再掲）

### 2.1 ブロック分割

| ブロック | 内容 | 主要ファイル |
|---|---|---|
| **A** | 基盤系（モデル・マイグレーション・モデルメソッド） | `*/models.py`、`*/migrations/`、`config/constants.py` |
| **B** | サービス層・タスク層 | `duplicates/services/`、`duplicates/tasks/`、`contacts/services/` |
| **C** | マージ実行系 | `duplicates/services/merge_executor.py` |
| **D** | UI 層 | `*/views.py`、`*/forms.py`、`templates/` |

実装順序は **A → B → C → D**。

### 2.2 ブロック A のサブステップ

| サブステップ | 内容 | 状態 |
|---|---|---|
| A-1a | 新規アプリ4つを作成 | ✅ 完了 |
| **A-1b** | **cards から Person / Contact / ContactFieldConfidence を移動（本書）** | 本書 |
| A-1c | v1.4.2 のフィールド追加・新規モデル作成・TextChoices 定義 | 別途指示書 |
| A-2 | モデルメソッドの実装 | 別途指示書 |

### 2.3 重要原則

- **コード君は A-1b の範囲のみ実装する**。A-1c 以降を勝手に進めない。
- v1.4.2 のフィールド追加・新規モデル作成は A-1c の作業。**A-1b では v1.1.0 のフィールドのまま移すだけ**。
- コミット & プッシュはコード君が単独で行わない。

---

## 3. A-1b の目的

cards/models.py に同居している **Person / Contact / ContactFieldConfidence** を、それぞれ **persons / contacts** アプリに移動する。

「移動」と表現しているが、実態は **削除 + 新規作成**。DB データを引き継がず、マイグレーションも全リセットする。

A-1b 完了後の状態：

- cards/models.py には OriginalImage / BusinessCard のみが残る
- persons/models.py に Person が存在する（v1.1.0 と同じ構成）
- contacts/models.py に Contact / ContactFieldConfidence が存在する（v1.1.0 と同じ構成）
- import 文（`from cards.models import Person, Contact, ContactFieldConfidence` 等）は全プロジェクト範囲で書き換え済み
- 各アプリの migrations/ は 0001 から始まる新規マイグレーションファイルが生成されている（適用はしない）

---

## 4. 前提

### 4.1 たんたんが事前に確認している事項

- **自宅PCの環境は完全に開発用**：マイグレーション時に既存DBを全削除して構わない（データ保護不要）
- 過去マイグレーション全リセットOK
- import 書き換え漏れがあった場合、後続の動作確認で検出する前提

### 4.2 作業ブランチ

`feature/v1.4.2-models`（A-1a で作業中のブランチを継続使用）

### 4.3 開始前の確認

`git status` で作業ツリーがクリーン（A-1a がコミット済み）であることを確認すること。

---

## 5. 作業手順

### Step 0：import 箇所の事前調査

作業開始前に、プロジェクト全体で `Person` / `Contact` / `ContactFieldConfidence` を import している箇所を grep で洗い出す。

調査対象：

- `*.py` ファイル全般（views.py / admin.py / forms.py / services/ / tasks/ / tests.py 等）
- `templates/` 配下のテンプレートタグ参照（`{% load %}` 等）
- `cards/migrations/` 配下のマイグレーションファイル（参考程度、後で全削除する）

検索パターンの例：

```
from cards.models import
from cards import models
cards.models.Person
cards.models.Contact
cards.models.ContactFieldConfidence
```

調査結果を **completion 報告に含める**（どのファイルの何行目で何を import しているか）。

### Step 1：persons/models.py に Person を新規作成

cards/models.py の **Person クラスをそのままコピー**して persons/models.py に配置する。

ポイント：

- v1.1.0 と同じフィールド構成（id / created_at / updated_at のみ）
- v1.1.0 と同じ `__str__` メソッド（contact_set を使う形）
- インポート文（`from django.db import models` 等）も合わせて追加
- v1.4.2 のフィールド追加（status / primary_contact / merged_into 等）は **追加しない**（A-1c の作業）

### Step 2：contacts/models.py に Contact / ContactFieldConfidence を新規作成

cards/models.py の **Contact / ContactFieldConfidence クラスをそのままコピー**して contacts/models.py に配置する。

ポイント：

- v1.1.0 と同じフィールド構成（status / previous_person 等の v1.4.2 追加は含まない）
- Contact の `business_card` フィールドは `OneToOneField(BusinessCard, ...)` のまま。BusinessCard は cards に残るので、`from cards.models import BusinessCard` を contacts/models.py に追加する
- Contact の `person` フィールドは `ForeignKey(Person, ...)`。Person は persons に移動したので、`from persons.models import Person` を contacts/models.py に追加する
- ContactFieldConfidence の `contact` フィールドは `ForeignKey(Contact, ...)` のまま（同一アプリ内）
- `related_name='confidences'` 等の設定もそのまま維持
- インポート文（`from django.db import models` / `from django.conf import settings` 等）も合わせて追加

### Step 3：cards/models.py から Person / Contact / ContactFieldConfidence を削除

cards/models.py から **3つのクラスとそれに関連する import 文**を削除する。

cards/models.py に残るもの：

- OriginalImage クラス（変更なし）
- BusinessCard クラス（変更なし）
- 必要な import 文（`from django.db import models` / `from django.conf import settings` 等）

### Step 4：import 文の書き換え

Step 0 で洗い出した import 箇所を全て書き換える。

書き換えルール：

- `from cards.models import Person` → `from persons.models import Person`
- `from cards.models import Contact` → `from contacts.models import Contact`
- `from cards.models import ContactFieldConfidence` → `from contacts.models import ContactFieldConfidence`
- 複数 import の場合は分割：`from cards.models import Person, Contact, OriginalImage` → `from cards.models import OriginalImage` と `from persons.models import Person` と `from contacts.models import Contact` の3行に分ける

書き換え対象ファイルの例：

- cards/views.py
- cards/admin.py
- cards/forms.py
- cards/services/*.py
- cards/tasks/*.py
- cards/tests.py（あれば）
- back_navigator/ 配下（あれば）
- その他、Step 0 で発見した全ファイル

### Step 5：マイグレーション全リセット

過去のマイグレーション履歴を全削除する。

#### Step 5-1：既存マイグレーションファイルの削除

cards/migrations/ 配下の `__init__.py` 以外のすべての `.py` ファイルを削除する。

削除対象例：

- `cards/migrations/0001_initial.py`
- `cards/migrations/0002_*.py`
- `cards/migrations/0003_*.py`
- `cards/migrations/0004_alter_businesscard_orientation.py`
- `cards/migrations/0005_originalimage_claimed_at_alter_originalimage_st...py`
- ほか、cards/migrations/ 配下にある `__init__.py` 以外の全ファイル

`__init__.py` は **残す**（マイグレーションディレクトリ自体は維持）。

persons / contacts / duplicates / actionlogs の migrations/ 配下は A-1a 時点で `__init__.py` のみなので、削除作業は不要。

#### Step 5-2：DB ファイルの削除（自宅PCのみ、開発DB前提）

`db.sqlite3` をプロジェクトルートから削除する。

これは **「自宅PCの開発DBは全削除OK」というたんたん方針** に基づく。実家PC・本番環境では別ルールが必要。

#### Step 5-3：新規マイグレーションファイルの生成

```
python manage.py makemigrations cards
python manage.py makemigrations persons
python manage.py makemigrations contacts
```

各アプリで 0001_initial.py が生成されるはず。生成されたマイグレーションファイルの内容（モデル定義のスナップショット）を確認すること。

duplicates / actionlogs は **A-1b ではモデル未定義なので makemigrations しても何も生成されない**（A-1c で対応）。

### Step 6：動作確認

```
python manage.py check
python manage.py makemigrations --dry-run
```

- `check` がエラーなく通ること
- `makemigrations --dry-run` で「No changes detected」と表示されること（Step 5-3 で全部生成済みのはず）

### Step 7：migrate は実行しない

migrate の実行は **たんたんが手動で行う**。コード君は migrate を実行しない。

たんたんが migrate を実行する前に、コード君は完了報告 → クロード君（レビュー担当）の確認を経る。

---

## 6. 完了基準

- cards/models.py から Person / Contact / ContactFieldConfidence が削除されている
- persons/models.py に Person が新規作成されている（v1.1.0 と同じ構成）
- contacts/models.py に Contact / ContactFieldConfidence が新規作成されている（v1.1.0 と同じ構成）
- 全プロジェクト範囲で import 文が書き換え済み（`from cards.models import Person` 等が残っていない）
- cards/migrations/ 配下が `__init__.py` のみ
- `db.sqlite3` が削除されている
- 各アプリ（cards / persons / contacts）に 0001_initial.py が新規生成されている
- `python manage.py check` がエラーなく通る
- `python manage.py makemigrations --dry-run` で「No changes detected」と表示される

---

## 7. やってはいけないこと（厳守）

- v1.4.2 のフィールド追加（status / previous_person / duplicate_checked_at 等）を **A-1b で行うこと**（A-1c の作業）
- 新規モデル（DuplicateCandidate / PersonMergeLog / ActionLog）を **A-1b で追加すること**（A-1c の作業）
- TextChoices の定義（PersonChangeReason 等）を **A-1b で追加すること**（A-1c の作業）
- モデルメソッドを実装すること（A-2 の作業）
- `python manage.py migrate` を実行すること
- OriginalImage / BusinessCard のフィールド定義を変更すること
- `apps.py` を変更すること
- `views.py` / `admin.py` の機能を変更すること（import 文の書き換えのみ可）
- import 書き換えに失敗した状態で完了報告を出すこと（Step 0 で洗い出した全箇所を書き換えてから報告）
- **コミット & プッシュを実行すること**（クロード君の確認後、たんたん経由で別途指示する）

---

## 8. 完了報告内容

作業完了後、以下を報告する。

- Step 0 の調査結果：`from cards.models import` を含む全ファイルとその import 内容のリスト
- Step 1 〜 4 の実施内容：書き換え前後の差分（主要ファイルのみで可、`git diff --stat` 程度の概要）
- Step 5 の結果：削除したマイグレーションファイル一覧、削除した DB ファイル、生成された新規マイグレーションファイル一覧
- Step 6 の出力：`python manage.py check` と `python manage.py makemigrations --dry-run` の出力
- 0001_initial.py の冒頭部分（cards / persons / contacts の3アプリ分）：何のモデルを含むかが分かる程度の抜粋

---

## 9. 補足

### 9.1 既存テストへの影響

cards/tests.py に Person / Contact を使うテストが存在する場合、import 文を書き換える必要がある。テストの内容は **変更しない**（migrate 後にたんたん手動でテスト実行する）。

### 9.2 admin.py 登録の扱い

cards/admin.py で Person / Contact / ContactFieldConfidence を `admin.site.register(...)` している場合、これらの登録は **persons/admin.py / contacts/admin.py に移動する**。

理由：admin の登録もアプリ単位で管理するのが Django の慣例。

### 9.3 判断に迷ったら

実装中に判断に迷ったら、独自判断せずクロード君（サポート担当・たんたんとのチャットセッション）に確認すること。特に：

- import 書き換えで複数の書き方がある場合
- migrations のリネーム・削除で迷う場合
- テストの書き換えで判断が必要な場合

---

**改訂履歴**

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v1.0 | 2026-05-06 | 初版作成 | クロード君（サポート担当） |
