# A-1d 実装指示書 ／ OCR パイプラインの v1.4.2 スキーマ対応（最小修正）

**FreeGroup2 v1.4.2 ／ コード君（Claude Code）向け**

---

## 1. 本書の位置づけ

A-1（モデル骨組み + マイグレーション生成）は完了済み。たんたんが migrate を適用したところ、画像アップロード後の OCR パイプラインが「処理中」のまま停止することが判明した。

原因：A-1c で Contact に必須フィールド（`status` など）を追加したため、v1.3.x 時代に書かれた `pipeline_coordinator.py` の Contact 生成ロジックが新スキーマに対応していない。

本書はこれを **最小修正で動作する状態に戻す**ための指示書。本来は B ブロック以降で扱う作業の一部を前倒しで実施する位置づけ。

「A-1d」と便宜上呼ぶ（A-1 の補修ステップ）。

---

## 2. 本書のスコープ

### 2.1 やること（仕様書 §15.3 の項目3のみ実施）

`cards/tasks/pipeline_coordinator.py` の Person + Contact 生成ロジックを **§15.4** に従って書き換える。具体的には：

- Person 作成時に `status='active'` を設定
- Contact 作成時に `status='primary'` および `created_by` を設定
- Person.primary_contact = contact を設定して保存

トランザクション内で循環 FK（Person ↔ Contact）を 3 段階で解決する処理にする。

### 2.2 やらないこと（仕様書 §15.3 の項目1・2・4・5は本書のスコープ外）

以下は B ブロック以降または別タスクで扱う。本書では**触らない**。

| 仕様書 §15.3 項目 | 内容 | 本書での扱い |
|---|---|---|
| 項目1 | `cards/services/json_normalizer.py` を `contacts/services/json_parser.py` に移動・拡張 | **触らない**（v1.3.x の場所のまま） |
| 項目2 | `contacts/services/normalization.py` の正規化関数を呼ぶ | **触らない**（normalization.py は作らない） |
| 項目3 | pipeline_coordinator の Contact + Person 生成ロジック変更 | **本書で実施** |
| 項目4 | OCR プロンプトに lang / postal_code 指示追加 | **触らない**（v1.3.x のプロンプトのまま） |
| 項目5 | JSON Schema を v1.4.0 にバージョンアップ | **触らない**（v1.3.x の Schema のまま） |

「ついでに項目2もやろう」「ついでに normalization.py を作っておこう」のような前倒し実装は **やらない**。本書の意図は「OCR フローが最小限で動作する状態に戻す」ことであり、項目2・4・5は v1.4.2 全体の他の作業と整合させてから実施する。

### 2.3 関連ファイルの調査範囲

`pipeline_coordinator.py` だけで完結しない可能性があるため、関連する Person / Contact 生成箇所も調査対象に含める。具体的には：

- `cards/management/commands/process_pending.py`：Contact / Person を直接生成している場合は同じ修正が必要
- その他、Contact / Person を直接 `objects.create(...)` している箇所すべて

事前 grep で全箇所を洗い出し、§15.4 に従って修正すること。

---

## 3. 仕様書の参照場所

| トピック | 参照先 |
|---|---|
| §15.4 新規 Contact 生成時の primary_contact 設定（本書のメイン参照先） | 統合最終版 §15.4 |
| §15.3 OCR パイプラインの v1.4.0 修正範囲（やる範囲・やらない範囲の確認） | 統合最終版 §15.3 |
| Contact のフィールド一覧 | 統合最終版 別表 A.5 |
| Person のフィールド一覧 | 統合最終版 別表 A.6 |
| Contact.Status の値 | 統合最終版 別表 C.10 |
| Person.Status の値 | 統合最終版 別表 C.11 |

---

## 4. 前提

### 4.1 状態

- A-1a / A-1b / A-1c は完了済み（ブランチ feature/v1.4.2-models）
- たんたんが手動 migrate 実施済み
- 既存ユーザー（iwata、スーパーユーザー）が DB に存在
- 画像アップロード自体は成功するが、OCR 後の Contact 生成で失敗する状態

### 4.2 作業ブランチ

`feature/v1.4.2-models`（A-1 から継続）

### 4.3 自宅PC開発DB方針

自宅PCの環境は完全に開発用なので、必要があれば既存DB全削除可能。テストで OriginalImage / BusinessCard / Contact / Person を作っても消してOK。

---

## 5. 完了基準

- 画像をアップロードすると、OCR 処理が完走し、BusinessCard / Person / Contact が生成される
- 生成された Person は `status='active'` で primary_contact が紐づいている
- 生成された Contact は `status='primary'` で created_by が設定されている
- `python manage.py check` がエラーなく通る
- 既存テストがあれば壊れない（Contact 生成テストがあれば修正してOK）

---

## 6. やってはいけないこと（厳守）

- 仕様書 §15.3 の **項目1・2・4・5** に手を出すこと（本書 §2.2 の表参照）
- `contacts/services/normalization.py` を作ること（B ブロック以降の作業）
- `contacts/services/json_parser.py` を作ること（B ブロック以降の作業）
- OCR プロンプト（`cards/prompts/extract_combined.txt`）を変更すること
- JSON Schema を変更すること
- モデル定義（models.py）を変更すること
- マイグレーションファイルを生成・編集すること（A-1c で完成済み）
- A-1a / A-1b / A-1c の生成物を変更すること
- DB 構造を変更すること
- 「ついでに」B ブロック以降の作業を前倒しでやること
- **コミット & プッシュを実行すること**（クロード君の確認後、たんたん経由で別途指示する）

---

## 7. 完了報告内容

作業完了後、以下を報告する。

- 事前 grep で発見した「Contact / Person を直接生成している箇所」のリスト
- 修正したファイルと変更概要（git diff --stat レベル）
- 実機テストの結果：画像をアップロードして OCR 完走、Person / Contact が生成されたことの確認（admin またはシェルで `Person.objects.count()` / `Contact.objects.count()` 等で確認）
- 生成された Person の primary_contact が紐づいているかの確認
- 生成された Contact の status / created_by の値の確認
- `python manage.py check` の出力

---

## 8. 補足

### 8.1 想定される実装の難所

- 循環 FK（Person ↔ Contact）の 3 段階解決：仕様書 §15.4 のフロー通り実装すること（Person.create → Contact.create → person.primary_contact 設定）
- トランザクション境界：仕様書 §15.4 では「1 つのトランザクション内で実行」と指定。`transaction.atomic()` を使う
- `created_by` の値：OCR 由来の場合、`OriginalImage.user`（アップロードユーザー）を渡すのが自然

### 8.2 判断に迷ったら

実装中に判断に迷ったら、独自判断せずクロード君（サポート担当・たんたんとのチャットセッション）に確認すること。特に：

- §15.4 のフロー通りで動かない場合
- 循環 FK のマイグレーション問題が発生した場合（既に解決済みのはずだが念のため）
- 既存の v1.3.x コードと §15.4 のフローで整合が取れない場合

---

**改訂履歴**

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v1.0 | 2026-05-06 | 初版作成 | クロード君（サポート担当） |
