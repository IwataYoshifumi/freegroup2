# A-2c 実装指示書 ／ DuplicateCandidate のモデルメソッド実装

**FreeGroup2 v1.4.2 ／ コード君A（Claude Code）向け**

---

## 1. 本書の位置づけ

A-2 ブロック（モデルメソッド実装、第10章）の3つ目のサブステップ。A-2a（Person）と A-2b（Contact + ContactFieldConfidence）は完了済み。本書は A-2c（DuplicateCandidate のモデルメソッド実装）を担当する。

A-2c で実装するメソッドは仕様書 10.7 節に定義されている。すべて `duplicates/models.py` に追加する。

---

## 2. 実装対象メソッド一覧

### クラスメソッド（7個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 1 | `DuplicateCandidate.get_pending(contact)` | contact が紐づく Person の pending 候補を取得 | 10.7.1 |
| 2 | `DuplicateCandidate.get_merged(contact)` | contact が紐づく Person の merged 候補を取得（マージ履歴表示用） | 10.7.1 |
| 3 | `DuplicateCandidate.get_different_person(contact)` | contact が紐づく Person の different_person 候補を取得 | 10.7.1 |
| 4 | `DuplicateCandidate.get_invalidated(contact)` | contact が紐づく Person の invalidated 候補を取得（開発・デバッグ用） | 10.7.1 |
| 5 | `DuplicateCandidate.has_duplicates(contact, status)` | 指定 status の候補が存在するかどうかの判定（True/False） | 10.7.1 |
| 6 | `DuplicateCandidate.get_by_group(group_id)` | group_id でまとめて候補を取得 | 10.7.1 |
| 7 | `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` | old_candidate からスコア・ランク・group_id 等をコピーして新規作成 | 10.7.1 / 12.8.3 |

### インスタンスメソッド（2個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 8 | `candidate.mark_as_merged(user, review_result, note)` | 自身の状態遷移（review_status='merged' / review_result / reviewed_by / reviewed_at / note） | 10.7.2 |
| 9 | `candidate.mark_as_different_person(user, review_result, note=None)` | 自身の状態遷移（review_status='different_person' / review_result / reviewed_by / reviewed_at / note） | 10.7.2 |

### A-2c で実装しないメソッド

- `candidate.record_different_person_action(user)` → A-2e に集約（ActionLog 連携系メソッドはまとめて A-2e で実装する方針）

---

## 3. 【最重要】設計思想：このメソッドは何のためにあるか

このセクションは特に `create_recovered_from`（#7）に関する設計思想を扱う。**実装中に「再計算した方が正確では？」と感じたら、必ず本セクションと仕様書 12.8.2 / 12.8.4 を再読すること**。

### 3.1 一文で言うと

`create_recovered_from` は、**ユーザが重複レビューを途切れさせず続けられるようにするための仕掛け**である。単なるコピーメソッドに見えるが、これがないとユーザの作業が止まる。

### 3.2 ユーザは画面で何をしているか

FreeGroup2 を使うのは、たんたんのような営業・経営者。本業の合間に名刺データを整理する。重複レビュー画面では、同じ人物かもしれない2件のコンタクトを左右に並べて見比べ、「同じ人」「別人」「次の候補」を判定する。

レビューはランクごとに認知の使い方が違う。

- **exact_match**：「同じ名刺の重複アップロード」を確認するモード。ほぼ機械的にマージ判定
- **possible_high**：「ほぼ確実に同一人物」を見比べるモード
- **possible_mid**：「フルネーム + メール or 携帯一致」を慎重に確認するモード
- **possible_low**：「同姓同名の可能性」をじっくり見比べて、別人判定が多くなるモード

これらは認知の使い方が違う作業。**同じ性質のレビューを連続させた方が、ユーザは疲れない**。「同じ名刺のマージばかり確認してたら、急に名前だけ一緒のやつが出てきたら疲れる」という話。だから FreeGroup2 では、同じランク・同じ起点 Person の候補をひとまとめにして、続けてレビューさせる設計になっている。

### 3.3 GID（group_id）の役割

GID は「同一起点 Person・同一ランクの候補をまとめる紐」。同じ紐でくくられた候補は、ユーザにとって「同じモードで判定できる仲間」。レビュー画面はこの紐単位で動く。1つマージ判定したら、同じ紐の次の候補が即座に表示される。

### 3.4 マージ実行時に起きること

ユーザがマージボタンを押した瞬間に起きるべきことは2つ。

1. マージそのものを完了させる（Person の統合、Contact の付け替え、PersonMergeLog の作成）
2. **マージで巻き込まれた他の候補（merged 側を介して繋がっていた候補群）を、surviving 側に紐付け直して連続レビューを継続させる**

このうち2番目を担うのが、`create_recovered_from` を呼び出す `recover_duplicate_candidates` という処理（Bブロック以降で実装）。

具体例で説明する。

```
マージ前：Person A、B、C、D が存在
DuplicateCandidate（pending）：
  (A, B, score=220, rank=possible_high, GID=G1)  ← マージ対象
  (B, C, score=150, rank=possible_mid, GID=G2)
  (B, D, score=130, rank=possible_mid, GID=G2)
```

ユーザが「A vs B」をマージ（A を残す、B を統合）。

```
マージ後にやりたいこと：
  (B, C) → (A, C) に置き換え、GID=G2 のまま pending で復活
  (B, D) → (A, D) に置き換え、GID=G2 のまま pending で復活
```

これで、ユーザは続けて「A vs C」「A vs D」を同じレビュー画面で判定できる。`create_recovered_from` は、この「置き換えて復活させる」処理の核を担う。

### 3.5 なぜスコアを再計算しないか（最重要）

ここが、過去のレビュアー（GPT・Opus・複数のクロード君）が繰り返し誤解してきた箇所。

**直感的には「B が A に変わったのだから、ContactA vs ContactC でスコアを再計算すべき」と思える**。実際、その方が「正確」に見える。

しかし、再計算しない。理由はユーザの体験にある。

#### 再計算するとどうなるか

レビュー画面はマージボタン押下 → POST処理 → GETリダイレクト → 次の候補表示、までが1つの同期処理。recover の処理時間がそのまま「ボタン押してから次の画面が出るまでの待ち時間」になる。

もし再計算すると：

- A vs C、A vs D ... と新 surviving の主コンタクトと比較計算を毎回実行
- 1ペアあたり数十〜数百ms × 候補数
- ユーザは「マージボタン押した後、画面が止まったように感じる」
- 連続レビューのリズムが切れる、本業の合間にやってる作業の集中が途切れる

**FreeGroup2 のユーザは本業の合間に使っている**。レビュー画面で「待たされる」体験は、システムから離れる引き金になる。

#### コピーで済む論理的な理由

スコアとランクは「2つの Person が同一人物である可能性の指標」であり、**人物そのものに紐づく値**。Contact のフィールド値が修正されても、人物の同一性判定は変わらない。

GID は「同一起点 Person・同一ランクのまとまり」と定義されているので、GID をコピーすれば rank も同じであることが保証される。

スコアの絶対値が新しい比較対象（A vs C）で正確かどうかは、確かに不明。しかし、**ランクの近似性で十分**。次回 cron（5分後）が走れば、duplicate_checked_at=NULL のものについて正確な値で再判定される。**完璧な整合性より、連続レビュー UX を優先する**設計判断。

これは仕様書 12.8.4 で詳細に説明されている。

### 3.6 過去のレビュー警告（仕様書 12.8.2 より引用）

> その指摘は過去複数のレビュー（GPT レベル・Opus レベル含む）で繰り返し発生しており、すべて却下されている。Opus レベルのレビュアーでも 2 回連続で同じ誤解を起こした実績があるため、本警告を独立した見出しとして設置する。

実装中に「再計算した方が正確では？」と感じたら、それは過去のクロード君が必ず通った道。手を止めて、本セクションと仕様書 12.8.2 / 12.8.4 を再読してほしい。

### 3.7 この設計思想の適用範囲

A-2c で実装するのは `create_recovered_from` クラスメソッドだけだが、**この設計思想は recover 全体（Bブロック以降）にも適用される**。`create_recovered_from` を実装するときから、「単なるコピーメソッドではなく、ユーザの連続レビュー UX を支える根幹のメソッドだ」という認識で実装してほしい。

---

## 4. やる範囲

### 4.1 配置場所

すべて `duplicates/models.py` の `DuplicateCandidate` クラスに追加する。

### 4.2 各メソッドの仕様

仕様書 10.7.1（クラスメソッド）と 10.7.2（インスタンスメソッド）に責務が定義されている。各メソッドの引数・戻り値・処理内容は仕様書を一次情報源とする。

特に以下は仕様書を熟読してほしい：

- `create_recovered_from` の処理内容 → 仕様書 12.8.3
- `mark_as_merged` / `mark_as_different_person` の引数 review_result の型と扱い → 仕様書 4.7（フィールド定義）と 4.7.1（merged系/different_person系混在禁止）
- group_id の扱い → 仕様書 8.6（同一起点 Person・同一ランクのグループ識別子）

### 4.3 review_result の扱い（重要な前提）

`mark_as_merged` / `mark_as_different_person` の引数 `review_result` は **list[str] 型**。仕様書 4.7 の通り JSONField に保存される「判定理由の配列（複数選択可）」。

- merged 系メソッドには DuplicateMergeReason の値（7値から複数）が入る前提
- different_person 系メソッドには DifferentPersonReason の値（3値から複数）が入る前提
- 値の妥当性チェック（merged系/different_person系の混在禁止など）は MergeForm 側で行う（Dブロックで実装）

A-2c の段階では Form は未実装なので、これらのメソッド側で値のバリデーションは**不要**。受け取った list[str] をそのまま `self.review_result` に保存する。

### 4.4 4つの get_* メソッドの実装

`get_pending` / `get_merged` / `get_different_person` / `get_invalidated` は status 違いで構造が似ている。共通のヘルパーで実装するか、個別に書くかは**コード君の判断に委ねる**。読みやすさ・保守性を優先して選んでほしい。

---

## 5. 仕様書からの変更点

### 5.1 docstring に経緯コメントを残す

`duplicates/models.py:20` 付近に、A-1c で `match_reason` / `matched_fields` / `MatchReason` を削除した経緯のコメントが既に存在する（v0.1.5 S-1 指摘で削除）。これは**維持する**。

理由：仕様書本文の修正がオーパス君タスクで未着手のため、当面は仕様書本文に match_reason の記述が残っている。コード側に「削除した」コメントがある方が、読み手の混乱を防げる。

A-2c で新たにコメントを追加する必要はない。既存コメントを誤って削除しないように注意するだけで OK。

### 5.2 仕様書外の方針の補足

- `record_different_person_action` は A-2c では実装しない（A-2e に集約）
- メソッドの動作確認は Django shell で行う（後述「動作確認観点」参照）

---

## 6. やってはいけないこと（厳守）

### 6.1 設計思想に反する実装

- **`create_recovered_from` で `_calculate_score` を呼んで再計算すること**（厳禁）。設計思想セクション3.5を読み直すこと。スコア・ランク・group_id は old_candidate からそのままコピーする
- **`recover_duplicate_candidates` の実装に手を出すこと**（Bブロック以降の作業）。A-2c では `create_recovered_from` をクラスメソッドとして単体で実装するだけ
- **review_result のバリデーションをメソッド側に書くこと**（Dブロックの MergeForm.clean() の責務）

### 6.2 スコープ外の実装

- A-2c 以外のメソッドを実装すること（`record_different_person_action` は A-2e、Person のメソッドは A-2a 完了済み等）
- `duplicates/models.py` 以外のファイルを変更すること
- マイグレーションファイルの追加・編集（A-1c で完成済み、メソッド追加だけならマイグレーション不要）
- サービス層・View 層・Form 層の実装
- `record_different_person_action` を「ついでに」実装すること（A-2e の責務）

### 6.3 既存コード・データへの影響

- 既存の DuplicateCandidate のフィールド定義・制約を変更すること
- `match_reason` / `matched_fields` / `MatchReason` を再追加すること（A-1c で削除済み、v0.1.5 S-1 指摘）
- 既存の docstring コメントを削除すること
- 開発DBのデータを削除すること（自宅PCの開発DBは削除可能だが、本作業ではDB操作は不要）

### 6.4 運用ルール

- コミット & プッシュを実行すること（実装完了後、クロード君（サポート担当）の確認を経てから別途指示する）

---

## 7. 動作確認観点

実装完了後、Django shell で以下の観点を確認する。テストデータは Django shell から作成してよい（自宅PCの開発DBは削除OK方針）。

### 7.1 4つの get_* メソッド

- 任意の Person に対して pending / merged / different_person / invalidated の DuplicateCandidate をそれぞれ作成
- contact 引数で渡したとき、その contact が紐づく Person を起点として正しく候補が返ること
- 各メソッドが対応する review_status のもののみ返すこと
- person_a / person_b 両方の側から検索されること（Person を起点に person_a または person_b の OR 検索になる）

### 7.2 has_duplicates

- 候補があるとき True、ないとき False を返すこと
- status 引数（review_status）の値で正しく絞り込めること

### 7.3 get_by_group

- 同じ group_id を持つ候補が複数件返ること
- 異なる group_id の候補は返らないこと

### 7.4 create_recovered_from（最重要）

- old_candidate（Person B 起点の DuplicateCandidate、例：(B, C, score=150, rank=possible_mid, GID=G2)）を用意
- `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person=A)` を呼ぶ
- 戻り値が新規 DuplicateCandidate であること
- 以下がすべて満たされること：
  - `score` が old_candidate からコピーされていること（再計算されていないこと）
  - `rank` が old_candidate からコピーされていること
  - `group_id` が old_candidate からコピーされていること
  - merged_person だった側（B）が surviving_person（A）に置き換わっていること
  - 相手側 Person（C）はそのまま保持されていること
  - person_a / person_b の ID 順正規化が適用されていること（仕様書 4.7 で person_a / person_b は ID 順）
  - `review_status='pending'` で作成されていること
  - `reviewed_by` / `reviewed_at` が NULL であること

### 7.5 mark_as_merged

- pending な candidate に対して `mark_as_merged(user, review_result=['same_card'], note='テスト')` を呼ぶ
- review_status が 'merged' になっていること
- review_result に渡した list がそのまま保存されていること
- reviewed_by / reviewed_at / note が記録されていること

### 7.6 mark_as_different_person

- pending な candidate に対して `mark_as_different_person(user, review_result=['same_name'])` を呼ぶ（note 省略）
- review_status が 'different_person' になっていること
- review_result に渡した list がそのまま保存されていること
- reviewed_by / reviewed_at が記録されていること
- note は空文字 or None で保存されていること（モデルのデフォルト挙動）

### 7.7 全体確認

- `python manage.py check` がパス
- `python manage.py makemigrations --dry-run` で「No changes detected」が表示されること（メソッド追加だけならマイグレーション不要）

---

## 8. 完了報告内容

作業完了後、以下を報告してほしい。

1. 実装したメソッド一覧（9個）と各メソッドの行数
2. `duplicates/models.py` の差分の概要（どこに何を追加したか）
3. `python manage.py check` の出力
4. `python manage.py makemigrations --dry-run` の出力
5. 動作確認結果（7.1〜7.7 の各観点で実行した内容と結果）
6. 4つの get_* メソッドを共通化したか、個別実装したか、その判断理由
7. 実装中に判断に迷った点・気になった点（あれば）

---

## 9. 補足

### 9.1 `create_recovered_from` を呼び出す側について

呼び出し側の `recover_duplicate_candidates` は Bブロック以降で実装する。A-2c では `create_recovered_from` を**単体で動くクラスメソッド**として実装すれば足りる。Django shell から直接呼び出せて、戻り値が確認できる状態にする。

### 9.2 困ったときは

仕様書（v1.4.2 統合最終版）の以下の節を参照：

- メソッド責務 → 10.7.1（クラスメソッド）/ 10.7.2（インスタンスメソッド）
- create_recovered_from の処理内容 → 12.8.3
- スコアコピーの設計趣旨 → 12.8.4
- 過去レビュー警告 → 12.8.2
- DuplicateCandidate のフィールド定義 → 4.7
- group_id の定義 → 8.6
- 命名規則 → 13.2

仕様書だけで判断に迷う場合は、たんたんに確認してほしい（クロード君が壁打ちで対応する）。独自判断で実装を進めない。

---

**（指示書終わり）**
