# B 前半 実装指示書（コード君A 向け）

**ブランチ**：`feature/v1.4.2-models`
**前提**：A-2 ブロック完了済み（コミット a070c4c）
**スコープ**：B ブロック前半 — 重複検出のうち副作用なし5関数の実装

---

## 0. 作業前に必ず読むもの（着手前に全体を熟読）

| 優先 | ドキュメント | 範囲 |
|---|---|---|
| 1 | `コード君への申し送りメモ_v1_4_2.md` | **全体** |
| 2 | `Run_Generate_Duplicate_Candidates_詳細仕様書_v0_1_5.md` | **第2章 / 第5章 / 第6章**（B 前半の一次情報源） |
| 3 | `名刺画像取り込みOCR仕様書_v1_4_2統合最終版.md` | 8.3 / 8.4 / 8.10 / 10.5 |

**B 前半の一次情報源は v0.1.5 詳細仕様書**。統合最終版 8.10 は概要のみで、実装の詳細は v0.1.5 第2章 / 第5章を参照すること。

---

## 1. 実装範囲（5 関数）

### 1.1 ファイル新規作成

両ファイルとも **新規作成**（既存ファイルなし）。

```
duplicates/services/duplicate_score.py       ← 新規作成
duplicates/services/duplicate_detection.py   ← 新規作成
```

### 1.2 関数一覧

| # | 関数 | 配置 | 性質 | 仕様書 |
|---|---|---|---|---|
| 1 | `determine_score_and_rank(contact_a, contact_b)` | duplicate_score.py | 準関数 | 統合最終版 10.5 / v0.1.5 別表 A.2 |
| 2 | `_calculate_score(contact_a, contact_b)` | duplicate_score.py | 準関数（内部） | 統合最終版 8.3 / 10.5 |
| 3 | `_determine_rank(score, contact_a, contact_b)` | duplicate_score.py | 純関数（内部） | 統合最終版 8.4 |
| 4 | `get_persons_confirmed_as_different(person)` | duplicate_detection.py | 準関数 | v0.1.5 第5章 5.2 |
| 5 | `find_duplicate_contacts(contact, excluded_persons=None)` | duplicate_detection.py | 準関数 | v0.1.5 第2章 |

**実装順序はコード君A 判断**。ただし依存関係（5 が 1, 4 を呼ぶ／1 が 2, 3 を呼ぶ）は崩さないこと。

---

## 2. 実装上の最重要注意

### 2.1 SQLite 3.51.2 planner bug 回避

SQLite 3.51.2 では **partial unique index + OR フィルタ + `.exists()`** の組み合わせで internal query planner error が発生する事例が A-2c で確認されている。OR 検索を含むクエリでは `.exists()` を使わず、`.first() is not None` を使うこと。

B 前半では `Q(person_a=person) | Q(person_b=person)` のような OR 検索を `get_persons_confirmed_as_different` / `find_duplicate_contacts` の両方で書く。`.exists()` で存在チェックしたくなる場面が出ても、`.first() is not None` で書くこと。

### 2.2 prefetch_related('confidences') 必須

`find_duplicate_contacts` 内部で候補 Contact を取得する際、**無条件で `prefetch_related('confidences')` を呼ぶこと**。

仕様書 2.5 では「cron 経由のみ必須、ContactCreateView 経由は任意」と書かれているが、関数内で呼び出し元を判別する分岐は書かない。常に prefetch しても性能上のデメリットはなく、N+1 リスクを確実に潰せる。

---

## 3. 設計思想（コード君A が踏み外しやすい箇所）

### 3.1 【設計思想1】find_duplicate_contacts は履歴参照しない

`find_duplicate_contacts` は2つの Contact のフィールド値だけを見てスコア・ランクを計算する純粋なロジックである。DuplicateCandidate テーブルや他の履歴テーブルは参照しないこと。「過去に別人判定済みなので除外」のような履歴参照判断は、呼び出し元の `generate_duplicate_candidates_for_contact` 側で行い、その結果を `excluded_persons` 引数として渡す形にする。詳細は v0.1.5 の 5.4.1 を参照。

### 3.2 【設計思想2】excluded_persons はオプション引数（必須にしない・関数分割しない）

`find_duplicate_contacts` は cron 経由（`generate_duplicate_candidates_for_contact` から呼ばれる）と ContactCreateView 経由（手動 Contact 作成時の警告ダイアログから呼ばれる）の両方から呼ばれる。両者で同じ判定ロジックを共有するため、`excluded_persons` はオプション引数（デフォルト `None`）とする。「呼び出し元ごとに別関数に分ける」「必須引数にする」のは NG。詳細は v0.1.5 の 5.4.5 を参照。

---

## 4. 動作確認

### 4.1 確認観点

仕様書 v0.1.5 第6章 6.2 のうち、**観点 #3 と #7** を B 前半で確認すること。

| # | 確認観点 |
|---|---|
| 3 | フルネーム / メール / 携帯のいずれにも一致しない Contact が候補に上がらないこと |
| 7 | different_person 判定済みのペアが再度候補に上がらないこと（`get_persons_confirmed_as_different` が相手 Person を含むリストを返し、その結果が `find_duplicate_contacts` に渡されて NOT IN で除外されることを併せて確認） |

その他の観点（#1 / #2 / #4 / #5 / #6）は B 後半（`Run_Generate_Duplicate_Candidates` 実装後）の対象なので B 前半では確認不要。

### 4.2 確認方法

確認の方法（検証スクリプト / Django shell / その他）は **コード君A の判断**にお任せ。

ただし、以下は厳守：

- アップロード済みの本物データ（OriginalImage / BusinessCard / Contact 等）には触れないこと
- 検証用のダミー Person / Contact を作成して使うこと
- 終了時にダミーレコードは削除すること

---

## 5. やってはいけないこと

- 仕様書の記述を独自判断で改変する（迷ったらたんたんに確認）
- B 後半の関数（`Run_Generate_Duplicate_Candidates` / `generate_duplicate_candidates_for_contact` / `invalidate_pending_candidates` / `recover_duplicate_candidates`）に手を出す
- 既存ファイル（A-2 で実装済みのモデル等）を編集する
- 自己判断でコミット&プッシュする（実装完了 → クロード君がレビュー → たんたん確認 → コミット&プッシュ指示の順）
- DB 削除を伴う操作（自宅 PC は開発DBなので削除可だが、本物の検証用 OriginalImage は触れないこと）

---

## 6. 完了報告で報告してほしいこと

1. 変更ファイル一覧（git status）
2. 各関数の実装内容の要点（仕様書のどこを参照したか）
3. 動作確認の結果（観点 #3 / #7 がどう確認できたか）
4. 仕様書から外れた判断があれば、その箇所と理由
5. 気付いた点・判断に迷った点（仕様書の不明瞭な箇所、申し送りメモとの不整合など）

---

## 7. 参考：B 後半でやること（参考情報、B 前半では実装しない）

B 前半の5関数が完了したら、B 後半で以下を実装する。B 前半の関数を呼び出す側になる。

- `Run_Generate_Duplicate_Candidates(limit=100)` — タスク層上位、cron 起動
- `generate_duplicate_candidates_for_contact(contact)` — タスク層下位
- `invalidate_pending_candidates(contact)` — Contact 編集時の処理
- `recover_duplicate_candidates(merged_person, surviving_person)` — マージ実行時の recover

B 前半の `find_duplicate_contacts` は B 後半の `generate_duplicate_candidates_for_contact` から呼ばれることになる。B 前半の段階では**呼び出し元が存在しない状態で実装する**ため、動作確認は Django shell や検証スクリプトで直接呼び出す形になる。

---

**指示書はここまで。実装に進む前に不明点があればたんたんに確認してください。**
