# A-2a 実装指示書 ／ Person のモデルメソッド実装

**FreeGroup2 v1.4.2 ／ コード君（Claude Code）向け**

---

## 1. 本書の位置づけ

A-1（モデル骨組み + マイグレーション + OCR 最小修正）は完了済み。ここから A-2 ブロック（モデルメソッド実装、第10章）に入る。

A-2 はメソッドが 30 個以上あるため、モデル単位で 5 つのサブステップ（A-2a〜A-2e）に分割する。本書はその 1 つ目、Person のモデルメソッド実装を担当する。

---

## 2. 本書のスコープ

### 2.1 やること（実装対象メソッド一覧）

`persons/models.py` の `Person` モデルに、以下の 6 メソッドを実装する。

| メソッド | 種別 | 責務 | 仕様書参照 |
|---|---|---|---|
| `mark_as_merged(surviving_person)` | インスタンス | 自身の状態遷移（status='merged' / merged_into / primary_contact=NULL） | 10.4.1 |
| `set_primary_contact(new_contact, old_primary_new_status='active')` | インスタンス | primary_contact 切り替え（派生情報の同期） | 10.4.3 |
| `get_active_contacts()` | インスタンス | status='active' の Contact 一覧を返す | 10.4.1 |
| `get_inactive_contacts()` | インスタンス | status='inactive' の Contact 一覧を返す | 10.4.1 |
| `Person.get_active()` | クラス | status='active' の Person 一覧を返す | 10.4.2 |
| `Person.get_archived()` | クラス | status='archived' の Person 一覧を返す | 10.4.2 |

### 2.2 やらないこと

以下は本書のスコープ外。**触らない**。

| 項目 | 本書での扱い |
|---|---|
| `transfer_contacts_to(surviving_person, merge_reason)` の実装 | **後回し**（適切なタイミングで別途実施） |
| Person 以外のモデルメソッド（Contact / ContactFieldConfidence / DuplicateCandidate / PersonMergeLog / ActionLog） | **A-2b〜A-2e で実施** |
| サービス層関数の実装 | **B ブロックで実施** |
| View 層の実装 | **D ブロックで実施** |
| マイグレーションファイルの生成・編集 | **A-1c で完成済み、変更しない** |
| モデル定義（フィールド・制約）の変更 | **A-1c で完成済み、変更しない** |
| コミット & プッシュの実行 | **指示があるまで実行しない** |

「ついでに transfer_contacts_to も実装しよう」「ついでに Contact のメソッドも書いておこう」のような前倒し実装は **やらない**。

---

## 3. 仕様書の参照場所

| トピック | 参照先 |
|---|---|
| §10.4 Person のモデルメソッド詳細（本書のメイン参照先） | 統合最終版 §10.4 |
| §10.2 モデルメソッド化の判断基準（責務分離の原則） | 統合最終版 §10.2 |
| §10.3 派生情報の同期はモデルメソッド化が許される例外 | 統合最終版 §10.3 |
| §4.5 Person（人物 DB） | 統合最終版 §4.5 |
| §4.5.2 Person.primary_contact と Contact.status='primary' の二重管理に関する設計趣旨 | 統合最終版 §4.5.2 |
| Person.Status の値（active / merged / archived） | 統合最終版 別表 C.11 |
| Contact.Status の値（primary / active / inactive） | 統合最終版 別表 C.10 |
| Person のフィールド一覧 | 統合最終版 別表 A.6 |

---

## 4. 前提

### 4.1 状態

- A-1a / A-1b / A-1c / A-1d は完了済み（ブランチ feature/v1.4.2-models）
- たんたんが手動 migrate 実施済み
- OCR パイプラインが v1.4.2 スキーマで動作確認済み（A-1d 実機確認）
- Person / Contact / ContactFieldConfidence のフィールド定義は v1.4.2 完成形に到達

### 4.2 作業ブランチ

`feature/v1.4.2-models`（A-1 から継続）

### 4.3 自宅 PC 開発 DB 方針

自宅 PC の環境は完全に開発用なので、必要があれば既存 DB 全削除可能。動作確認用にテストデータを作って消してOK。

---

## 5. 完了基準

- 6 メソッドすべてが `persons/models.py` の `Person` モデルに実装されている
- `python manage.py check` がエラーなく通る
- §7 の動作確認観点（Django shell）がすべてパスする
- 既存テストがあれば壊れない

---

## 6. やってはいけないこと（厳守）

### 6.1 実装範囲の踏み外し禁止

- `transfer_contacts_to(surviving_person, merge_reason)` を実装すること（**後回し方針**）
- A-2b〜A-2e で扱うモデルメソッドに手を出すこと
- B / C / D ブロックの内容を前倒しで実装すること
- モデル定義（フィールド・制約）を変更すること
- マイグレーションファイルを生成・編集すること
- **コミット & プッシュを実行すること**（クロード君の確認後、たんたん経由で別途指示する）

### 6.2 メソッドごとの踏み外しポイント（警告）

実装中に踏み外しやすい点を以下に明示する。仕様書を読めば書いてあるが、特に注意。

- **`mark_as_merged`**：このメソッドは Person 自身の状態遷移だけを担う。Contact 側のフィールド（status / person FK）には**一切触らない**（仕様書 §10.2 / §10.4.1 参照）
- **`set_primary_contact`**：旧 primary を勝手に inactive 固定にしない。`old_primary_new_status` 引数で切り替え、**デフォルトは `'active'`**（仕様書 §10.4.3 参照）
- **`get_active_contacts()`**：`status='active'` のみ返す。**`primary` は含めない**（仕様書 §10.4.1 / 別表 C.10 参照）
- **`Person.Status`**：active / merged / archived の **3 値**。`get_active()` は active のみ、`get_archived()` は archived のみ（仕様書 別表 C.11 参照）

---

## 7. 動作確認観点（Django shell）

実装完了後、`python manage.py shell` で以下を順に確認すること。

### 7.1 `mark_as_merged` の動作確認

- 任意の active な Person を 2 つ用意（surviving_person / merged_person）
- `merged_person.mark_as_merged(surviving_person)` を実行
- 確認 1：`merged_person.status == 'merged'`
- 確認 2：`merged_person.merged_into == surviving_person`
- 確認 3：`merged_person.primary_contact is None`
- 確認 4：surviving_person のフィールド（status / merged_into / primary_contact）は一切変わっていない

### 7.2 `set_primary_contact` の動作確認

- 任意の Person に primary Contact 1 つと active Contact 1 つを紐付ける
- パターン A：`person.set_primary_contact(active_contact)` を引数なし（デフォルト `old_primary_new_status='active'`）で呼ぶ
  - 旧 primary の status が `'active'` に降格
  - 新 primary（旧 active）の status が `'primary'` に昇格
  - `person.primary_contact` が新 primary を指す
- パターン B：再度 active Contact を作って `person.set_primary_contact(active_contact, old_primary_new_status='inactive')` を呼ぶ
  - 旧 primary の status が `'inactive'` に降格

### 7.3 `get_active_contacts` / `get_inactive_contacts` の動作確認

- 任意の Person に primary 1 個 / active 2 個 / inactive 1 個の Contact を紐付ける
- `person.get_active_contacts()` が active 2 個のみ返す（primary は含まない）
- `person.get_inactive_contacts()` が inactive 1 個のみ返す

### 7.4 `Person.get_active` / `Person.get_archived` の動作確認

- active / merged / archived の Person を 1 つずつ用意（archived は Django shell から `person.status='archived'; person.save()` で作成して可）
- `Person.get_active()` が active の Person のみ返す（merged / archived は含まない）
- `Person.get_archived()` が archived の Person のみ返す（active / merged は含まない）

---

## 8. 完了報告内容

作業完了後、以下を報告する。

- 実装した 6 メソッドのリスト（メソッド名と行数の概要）
- 修正したファイルと変更概要（git diff --stat レベル）
- §7 の動作確認結果（各観点ごとに「確認 OK」または「想定外の挙動あり」を明記、想定外の場合は内容を記録）
- `python manage.py check` の出力
- 実装中に判断に迷った箇所があれば、その内容と取った判断（独自判断ではなくクロード君に相談済みの場合は相談内容も含む）

---

## 9. 補足

### 9.1 想定される実装の難所

- **`mark_as_merged` のトランザクション**：複数フィールドを同時に更新するため、`transaction.atomic()` で囲むのが安全。仕様書 §10.4.1 では明記されていないが、状態遷移系メソッドは原子性を担保すべき
- **`set_primary_contact` の処理順**：仕様書 §10.4.3 の処理内容に従う（旧 primary status 変更 → 新 primary status 変更 → Contact.person FK 付け替え → Person.primary_contact 更新）。partial unique constraint（1 Person につき primary は 1 つだけ）に違反しないよう、トランザクション内で順序を守る
- **`set_primary_contact` の Contact.person FK 付け替え**：仕様書 §10.4.3 の処理内容 3 に「new_contact が他 Person 配下なら surviving_person に付け替える」とあるが、A-2a の段階では「同一 Person 配下の Contact が primary に切り替わる」ケースのみ動作確認すれば十分。マージ実行系（C ブロック）で他 Person 配下からの付け替えが必要になるが、この場面では `Person.set_primary_contact()` のロジック自体は同じものが使えるはず

### 9.2 判断に迷ったら

実装中に判断に迷ったら、独自判断せずクロード君（サポート担当・たんたんとのチャットセッション）に確認すること。特に：

- 仕様書 §10.4 の記述だけでは実装方針が確定しない場合
- partial unique constraint 違反など、DB 制約に関わる問題が発生した場合
- 仕様書間の矛盾を発見した場合（仕様正本順位：v1.4.2 統合最終版 → PDF → URL 一覧表 → v0.1.5）

---

**改訂履歴**

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v1.0 | 2026-05-06 | 初版作成 | クロード君（サポート担当） |
