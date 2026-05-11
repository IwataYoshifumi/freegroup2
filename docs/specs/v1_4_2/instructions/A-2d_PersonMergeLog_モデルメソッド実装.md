# A-2d 実装指示書 ／ PersonMergeLog のモデルメソッド実装

**FreeGroup2 v1.4.2 ／ コード君A（Claude Code）向け**

---

## 1. 本書の位置づけ

A-2 ブロック（モデルメソッド実装、第10章）の4つ目のサブステップ。A-2a（Person）、A-2b（Contact + ContactFieldConfidence）、A-2c（DuplicateCandidate）は完了済み。本書は A-2d（PersonMergeLog のモデルメソッド実装）を担当する。

A-2d で実装するメソッドは仕様書 10.8 節に定義されている。すべて `duplicates/models.py` の `PersonMergeLog` クラスに追加する。

---

## 2. 実装対象メソッド一覧

### クラスメソッド（4個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 1 | `PersonMergeLog.create(surviving_person, merged_person, user)` | マージ実行のためのログレコードを作成（インスタンス生成＋save() を一気に実行）。duplicate_candidate / note 等は呼び出し側で追加設定 | 10.8.1 |
| 2 | `PersonMergeLog.lock_past_logs(merged_person)` | 過去のログを locked 状態に変更（多重マージ対応、自モデル集合操作） | 10.8.1 |
| 3 | `PersonMergeLog.get_for_person(person)` | Person 単位のログ一覧取得（マージログ一覧画面用） | 10.8.1 |
| 4 | `PersonMergeLog.get_undoable(person)` | 復元可能なログ取得 | 10.8.1 |

### インスタンスメソッド（3個）

| # | メソッド | 役割 | 仕様書節 |
|---|---|---|---|
| 5 | `merge_log.is_undoable()` | 復元可能かどうかの判定（status='undoable' なら True） | 10.8.2 |
| 6 | `merge_log.mark_as_undone(user)` | 自身の状態遷移（status='undone' / undone_by / undone_at を記録） | 10.8.2 |
| 7 | `merge_log.get_undo_preview()` | 復元後の予測状態を返す（確認画面表示用、dict を返す、DB 変更なし） | 10.8.2 / 10.8.3 |

### A-2d で実装しないメソッド

- `merge_log.record_merge_action(user)` → A-2e に集約
- `merge_log.record_undo_action(user)` → A-2e に集約

理由：これらは ActionLog モデルへの記録メソッドであり、ActionLog 自体が A-2e で実装される。A-2d 時点では呼び出し先の ActionLog がまだ存在しない。A-2c で `candidate.record_different_person_action(user)` を A-2e に集約したのと同じ判断（ActionLog 連携系メソッドはまとめて A-2e で実装する方針）。

---

## 3. 【最重要】設計思想：このメソッドは何のためにあるか

このセクションは、過去のレビュアー（GPT 系・Opus 系・複数のクロード君）が繰り返し誤解してきた箇所を扱う。**実装中に「ここおかしくないか？」「もっと厳密にチェックすべきでは？」と感じたら、必ず本セクションを再読すること**。

A-2d には誤解しがちなポイントが3つある。

- 3.1 〜 3.2：`lock_past_logs(merged_person)` の引数名と検索条件の非対称性
- 3.3：`is_undoable()` を `status` だけで判定する根拠
- 3.4：`get_undo_preview()` がスナップショットなしで予測できる根拠

### 3.1 PersonMergeLog の役割：状態管理レコード

そもそも PersonMergeLog は何のためにあるか。

FreeGroup2 では、ユーザがマージ画面で「2つの Person を統合する」操作を行う。例えば「B さんを A さんに統合（A が surviving、B が merged）」という操作。

このとき、後で「やっぱりこのマージ間違えた、戻したい」とユーザが思うことがある。本業の合間に名刺整理をしている中で、急いで判定してミスすることもある。なので**直近1段階分は復元できる**仕組みが必要。

PersonMergeLog はこのための**状態管理レコード**。1マージごとに1レコード作成され、`status` フィールド（`undoable` / `undone` / `locked`）で「現時点で復元可能か」を表現する。

| status の値 | 意味 |
|---|---|
| `undoable` | 復元可能。マージ実行直後のデフォルト状態 |
| `undone` | 既に復元済み。`mark_as_undone()` で遷移 |
| `locked` | 多重マージにより復元不可になった。`lock_past_logs()` で遷移 |

ActionLog（不変ログ、A-2e で実装）とは別物。ActionLog は「何が起きたかの履歴」を記録する不変ログで、PersonMergeLog は「現時点で復元できるかどうか」を管理する状態レコード。両方並存する。

### 3.2 多重マージとは何か：`lock_past_logs` の存在理由

「直近1段階だけ復元できる」と書いた。**なぜ「1段階だけ」なのか**を理解しないと `lock_past_logs` の引数名と検索条件が混乱する。

具体例で考える。

```
時刻 T1：A → B のマージ実行（B が surviving、A が merged）
  PersonMergeLog レコード ML1 が作成される：
    surviving_person = B
    merged_person   = A
    status          = 'undoable'
```

この時点では、ユーザは「ML1 を復元（A → B のマージを取り消す）」ことができる。Contact の `previous_person` / `previous_status` フィールドに「A 配下にいた頃の状態」が記録されているので、それを使って A を復活させられる。

ところがその後：

```
時刻 T2：B → C のマージ実行（C が surviving、B が merged）
  PersonMergeLog レコード ML2 が作成される：
    surviving_person = C
    merged_person   = B
    status          = 'undoable'
```

ここで問題が起きる。ML1（A → B）はもう復元できない。なぜなら：

- ML1 を復元するには「A 配下にいた Contact たち」を A に戻す必要がある
- それらの Contact の `previous_person` には B が記録されているはず（T1 のマージ時点）
- しかし T2 のマージで、Contact たちは B 配下から C 配下に移動。`previous_person` フィールドは T2 のマージで「B」に書き換えられる（T1 の「A」は失われる）
- つまり、Contact レベルで「A → B 時点のスナップショット」が消えている

**Contact の `previous_*` フィールドは1段階分しか保持しない**。これが「直近1段階だけ復元できる」の正体。

T2 のマージ実行時に、ML1 を `status='locked'` に書き換える必要がある。これをやるのが `PersonMergeLog.lock_past_logs(merged_person)`。

### 3.3 `lock_past_logs(merged_person)` の引数名は仕様書通り

ここから引数名の話になる。**過去レビュアーが繰り返し誤解した箇所**。

T2 のマージ実行コード（B ブロック以降で実装される）はこう書かれる想定：

```python
def execute_merge(surviving_person, merged_person, ...):
    # ...マージ処理...
    PersonMergeLog.lock_past_logs(merged_person=merged_person)
    # ...
```

このとき引数 `merged_person` に渡されるのは **B**（今のマージで merged 側になる Person）。

メソッドの中では、こう検索する：

```python
PersonMergeLog.objects.filter(
    surviving_person=merged_person,  # 引数 merged_person を surviving_person フィールドに対して検索
    status='undoable',
).update(status='locked')
```

**引数名と検索条件が一見ちぐはぐに見える**。引数 `merged_person` を渡したのに、メソッド内では `surviving_person=...` で検索している。

しかしこれは**仕様書 §9.3.1 手順8 の表記通り**：

> 過去のマージログを locked に変更（merged_person を surviving とする undoable なログ）

つまり「今のマージの merged 側（B）が、過去ログでは surviving 側だった」ログを探す、ということ。引数名 `merged_person` は「今のマージで merged 側になる Person」を指している。

**過去レビュアーが「引数名と検索条件が逆では？」「`now_merged_person` のような名前にすべきでは？」と指摘してきても、引数名は変えない**。仕様書の表記をそのまま実装することで、仕様書を見ながら実装する人と「メソッドの引数名」が一致する。

実装では、docstring に「ここでの `merged_person` は今のマージで merged 側になる Person。過去ログでは `surviving_person` フィールドに入っていた Person を指す」と明示すること。

### 3.4 `is_undoable()` は `status` だけで判定してよい

このメソッドは「この PersonMergeLog レコードは復元可能か」を返す True/False 判定。

**過去レビュアーが繰り返し誤解した箇所**：素朴に考えると、以下のような複合チェックが必要に思える。

- surviving_person 側がさらに別のマージで merged 済みになっていないか？
- 関連 Contact の `previous_*` がまだ生きているか？
- ContactFieldConfidence の状態は復元可能か？

**それは不要**。`status == 'undoable'` だけで十分。実装は1行。

なぜか。**`status` フィールドが「現時点で復元可能か」の唯一の真実**だから。

- マージ実行直後 → `status='undoable'`
- 別マージで巻き込まれた → `lock_past_logs()` で `status='locked'` に書き換え済み
- 復元実行された → `mark_as_undone()` で `status='undone'`

つまり、復元不可になる事象（多重マージ、復元済み）は**起きた時点で `status` に反映される**。実行時に他のフィールドを再チェックする必要はない。状態管理レコードの設計思想：「現時点で何ができるかは status を見れば分かる」。

過去レビュアーが「`is_undoable()` を `status` だけで判定するのは脆弱では？」「念のため複合チェックすべきでは？」と指摘してきても、**他のフィールドを見る複合判定にしない**。複合判定にすると：

- 状態管理の責務が `status` フィールドと「実行時の複合判定」に分散する
- 「`status='undoable'` なのに `is_undoable()` が False を返す」という矛盾が起きうる
- テストが複雑になる
- バグの温床になる

シンプルに保つ。`is_undoable()` は `self.status == self.Status.UNDOABLE` で1行。

### 3.5 `get_undo_preview()` は Contact の `previous_*` だけで予測できる

このメソッドは復元確認画面（`PersonMergeLogConfirmUndoView`、B ブロック以降で実装）が「復元したらこうなりますよ」を表示するためのデータを返す。

**重要：DB を変更しない**。SELECT 文だけ発行する読み取り専用メソッド。実際の復元処理（Contact の `person` 書き戻し、`status` の戻し、PersonMergeLog の `status='undone'` 化など）はマージ実行サービス層が担当（B ブロック以降）。

戻り値は dict（仕様書 §10.8.3）：

```python
{
    'merged_person': self.merged_person,
    'contacts_to_restore': QuerySet[Contact],
    'contacts_remaining_in_surviving': QuerySet[Contact],
}
```

**過去レビュアーが繰り返し誤解した箇所**：「マージ実行時にどこかにスナップショットを保存しているのか？」と思いがち。**スナップショットは取っていない**。

判定ロジック：

```python
# contacts_to_restore：merged_person に戻る Contact たち
contacts_to_restore = Contact.objects.filter(
    person=self.surviving_person,
    previous_person=self.merged_person,
)

# contacts_remaining_in_surviving：surviving 側に残る Contact たち
contacts_remaining_in_surviving = Contact.objects.filter(
    person=self.surviving_person,
).exclude(
    previous_person=self.merged_person,
)
```

なぜこれで「復元後の予測」が分かるか。

**Contact モデルが「マージ前の状態」を保持する設計**になっているから。仕様書 §9.4.2「マージド側パーソンに関する設計趣旨」でこう定義されている：

> マージド側パーソンに紐づくすべてのコンタクト（primary / active / inactive）はサバイブ側パーソンへ付け替える。付け替え時、`previous_person` にマージ前の merged_person を、`previous_status` にマージ前の status を記録する。

つまり、マージ実行直後の Contact たちは、surviving 側に紐づきつつ、自分のフィールドに「マージ前にどの Person 配下で、どんな status だったか」を保持している。

復元処理（仕様書 §9.5.2 手順1〜4）は、これらの Contact の `person` を `previous_person` に、`status` を `previous_status` に戻す処理。だから、**復元後の状態は Contact の `previous_*` フィールドを読むだけで分かる**。

`get_undo_preview()` の判定は単純：

- 「今 surviving 配下にあって、`previous_person == merged_person` の Contact」 = マージで動いた Contact = 復元すると merged_person に戻る = `contacts_to_restore`
- 「今 surviving 配下にあって、`previous_person != merged_person` の Contact」 = 元から surviving 配下にいた Contact = 復元しても動かない = `contacts_remaining_in_surviving`

別テーブルにスナップショットを保存する設計にしないこと。Contact モデルの `previous_*` フィールドだけで完結する。

### 3.6 この設計思想の適用範囲

A-2d で実装するのは7メソッドだが、以下は B ブロック以降にも適用される：

- 多重マージの仕組みと `lock_past_logs` の役割（マージ実行サービス層で `lock_past_logs()` を呼ぶときの引数の取り方）
- 状態管理レコードとしての PersonMergeLog（マージ実行サービス層で PersonMergeLog の作成・状態遷移を扱うときの考え方）
- スナップショットなし設計（復元実行サービス層で Contact の `previous_*` を使った復元処理）

A-2d の段階で設計思想を理解しておくと、B ブロックの実装もスムーズになる。

---

## 4. やる範囲

### 4.1 配置場所

すべて `duplicates/models.py` の `PersonMergeLog` クラスに追加する。新規ファイルは作らない。新規 import が必要な場合のみ、ファイル冒頭に追加する。

### 4.2 各メソッドの仕様

仕様書 §10.8.1（クラスメソッド）と §10.8.2（インスタンスメソッド）に責務が定義されている。各メソッドの引数・戻り値・処理内容は仕様書を一次情報源とする。

特に以下は仕様書を熟読してほしい：

- `PersonMergeLog.create()` の引数構成と「2回 save 方式」 → 仕様書 §10.8.4
- `lock_past_logs(merged_person)` の検索条件 → 仕様書 §9.3.1 手順8、§9.6
- `get_undo_preview()` の戻り値 dict 構造 → 仕様書 §10.8.3
- マージ前後の status / previous_status / previous_person 遷移 → 別添 PDF「マージ前後のコンタクトのステータス等まとめ.pdf」（`get_undo_preview()` 実装時の理解に役立つ）

### 4.3 `PersonMergeLog.create()` の引数は3つだけ

仕様書 §10.8.4 の通り、`PersonMergeLog.create()` の引数は **3 つだけ**：

```python
@classmethod
def create(cls, surviving_person, merged_person, user):
    # インスタンス生成 + save() を一気に実行
    ...
    return instance
```

`duplicate_candidate` / `note` をオプション引数として追加しないこと。仕様書 §10.8.4 通りの「2回 save 方式」が責務分離として正しい。呼び出し側（マージ実行サービス層、B ブロック以降）はこう書く想定：

```python
merge_log = PersonMergeLog.create(surviving_person, merged_person, user)
# ... マージ処理が進む ...
merge_log.duplicate_candidate = candidate
merge_log.note = built_note_text
merge_log.save(update_fields=['duplicate_candidate', 'note'])
```

`PersonMergeLog.create()` は「最低限のログレコードを残す」モデル側の責務に絞る。マージ処理の文脈に依存する値（candidate / note）をモデル側に持ち込まない。

### 4.4 `is_undoable()` は1行で書く

設計思想 §3.4 の通り。実装イメージ：

```python
def is_undoable(self):
    return self.status == self.Status.UNDOABLE
```

複合判定にしないこと。

### 4.5 4つのクラスメソッド（create / lock_past_logs / get_for_person / get_undoable）の共通化判断

各メソッドは責務がそれぞれ異なる（create は新規作成、lock_past_logs は集合更新、get_for_person / get_undoable は QuerySet 取得）ので、共通化は無理に行わない。ただし `get_for_person` と `get_undoable` の2つは status 違いで構造が似ているので、共通のヘルパーで実装するか個別に書くかは**コード君A の判断に委ねる**。読みやすさ・保守性を優先して選んでほしい。

### 4.6 `mark_as_undone(user)` の処理内容

仕様書 §10.8.2 の通り、自身の状態遷移を行う：

- `status='undone'` に変更
- `undone_by=user` を記録
- `undone_at=timezone.now()` を記録
- `save(update_fields=['status', 'undone_by', 'undone_at'])` で限定 save

引数のバリデーション（status が undoable かどうかのチェック等）は**メソッド側に書かない**。呼び出し側（復元実行サービス層、B ブロック以降）が `is_undoable()` で事前チェックする責務。`mark_as_undone()` は「自身の状態遷移を実行する」責務に絞る。

---

## 5. 仕様書からの変更点

なし。仕様書 §10.8 / §4.8 / §9.5 / §9.6 / §14.4 の記述通りに実装する。

ただし、ActionLog 連携の2メソッド（`record_merge_action` / `record_undo_action`）は A-2d では実装せず、A-2e に送る（§2 参照）。

---

## 6. やってはいけないこと（厳守）

### 6.1 設計思想に反する実装

- **`PersonMergeLog.create()` に `duplicate_candidate` / `note` のオプション引数を追加すること**（厳禁）。設計思想 §3.1 と §4.3 を読み直すこと。仕様書 §10.8.4 通りの「2回 save 方式」が責務分離として正しい
- **`is_undoable()` で複合判定を行うこと**（厳禁）。設計思想 §3.4 の通り、`status == 'undoable'` だけで判定する
- **`get_undo_preview()` で DB を書き換えること**（厳禁）。読み取り専用。SELECT 文だけ発行する
- **`get_undo_preview()` のために別テーブルにスナップショットを保存する設計を提案すること**（厳禁）。設計思想 §3.5 の通り、Contact の `previous_*` フィールドだけで完結する
- **`lock_past_logs(merged_person)` の引数名を変更すること**（厳禁）。設計思想 §3.3 の通り、仕様書の表記通り `merged_person` を使う

### 6.2 スコープ外の実装

- A-2d 以外のメソッドを実装すること
  - `record_merge_action(user)` / `record_undo_action(user)` は A-2e の責務
  - 他モデル（Person / Contact / DuplicateCandidate）のメソッドは A-2a / A-2b / A-2c 完了済み
- `duplicates/models.py` 以外のファイルを変更すること
- マイグレーションファイルの追加・編集（A-1c で完成済み、メソッド追加だけならマイグレーション不要）
- サービス層・View 層・Form 層の実装（マージ実行・復元実行サービス層は B ブロック以降）
- 復元処理本体（Contact の `person` / `status` 書き戻し等）を `mark_as_undone()` 内に実装すること（B ブロック以降の復元実行サービス層の責務）

### 6.3 既存コード・データへの影響

- 既存の PersonMergeLog のフィールド定義・制約を変更すること（A-1c で完成済み）
- 既存の docstring コメントを削除すること
- A-2a / A-2b / A-2c で実装済みのメソッドを変更すること
- 開発DBのデータを削除すること（自宅PCの開発DBは削除可能だが、本作業ではDB操作は不要。動作確認用のテストデータは Django shell から作成してよい）

### 6.4 運用ルール

- コミット & プッシュを実行すること（実装完了後、クロード君（サポート担当）の確認を経てから別途指示する）

---

## 7. 動作確認観点

実装完了後、Django shell で以下の観点を確認する。テストデータは Django shell から作成してよい（自宅PCの開発DBは削除OK方針）。

### 7.1 `PersonMergeLog.create()`

- 任意の Person A、B を作成
- `merge_log = PersonMergeLog.create(surviving_person=A, merged_person=B, user=test_user)` を呼ぶ
- 戻り値が PersonMergeLog インスタンスであること
- 以下のフィールドが正しく設定されていること：
  - `surviving_person == A`
  - `merged_person == B`
  - `executed_by == test_user`
  - `executed_at` が現在時刻（auto_now_add）
  - `status == 'undoable'`
  - `duplicate_candidate is None`
  - `note == ''`
  - `undone_by is None`
  - `undone_at is None`
- DB に save 済みであること（`merge_log.pk` が存在）
- 続けて `merge_log.duplicate_candidate = candidate; merge_log.note = 'テスト'; merge_log.save(update_fields=['duplicate_candidate', 'note'])` で部分 save が動くこと（2回 save 方式の検証）

### 7.2 `PersonMergeLog.lock_past_logs(merged_person)`

多重マージのシナリオを再現してテスト：

- Person A、B、C を作成
- `ML1 = PersonMergeLog.create(surviving=B, merged=A, user)` を作成（ML1.status='undoable'）
- 別に独立したマージログを作成して影響範囲を検証：
  - `ML0 = PersonMergeLog.create(surviving=X, merged=Y, user)` を作成（ML0.status='undoable'、X / Y は B / C と無関係の Person）
- `PersonMergeLog.lock_past_logs(merged_person=B)` を呼ぶ
- 確認：
  - `ML1.refresh_from_db()` で `status == 'locked'` になっていること
  - `ML0.refresh_from_db()` で `status == 'undoable'` のまま（影響を受けていないこと）
- 既に `status='locked'` または `'undone'` のログには影響しないこと（`status='undoable'` のもののみが対象）：
  - 別の Person ペアで `ML2 = PersonMergeLog.create(...)` を作成し `mark_as_undone()` で undone 化
  - その状態で `lock_past_logs(merged_person=...)` を呼んでも `status` が変わらないこと（undone のまま）

### 7.3 `PersonMergeLog.get_for_person(person)`

- 任意の Person A について、A が surviving_person または merged_person のログを複数作成（状態違い：undoable / undone / locked を混在）
- `PersonMergeLog.get_for_person(A)` を呼ぶ
- A に関連するログがすべて返ること（surviving 側 / merged 側 両方）
- A に無関係なログは返らないこと
- status による絞り込みは行わないこと（全状態が返る）

### 7.4 `PersonMergeLog.get_undoable(person)`

- 7.3 と同じテストデータを使う
- `PersonMergeLog.get_undoable(A)` を呼ぶ
- `status='undoable'` のログのみ返ること
- `status='undone'` / `'locked'` のログは返らないこと
- A が surviving 側 / merged 側 どちらでも返ること

### 7.5 `merge_log.is_undoable()`

- `status='undoable'` の merge_log で True を返すこと
- `status='undone'` の merge_log で False を返すこと
- `status='locked'` の merge_log で False を返すこと
- 実装が1行であること（複合判定になっていないこと）

### 7.6 `merge_log.mark_as_undone(user)`

- `status='undoable'` の merge_log に対して `mark_as_undone(test_user)` を呼ぶ
- 確認：
  - `merge_log.refresh_from_db()` で `status == 'undone'` になっていること
  - `undone_by == test_user`
  - `undone_at` が現在時刻
- save が `update_fields=['status', 'undone_by', 'undone_at']` で限定 save されていること（他のフィールドが触られていないこと、例えば `executed_by` / `executed_at` が変わらないこと）

### 7.7 `merge_log.get_undo_preview()`（最重要）

復元プレビューの正しさを検証する観点。

- Person A、B、C、D を作成
- A 配下に Contact `a-1`（primary）、`a-2`（active）を作成（既存）
- B 配下に Contact `b-1`（primary）、`b-2`（active）を作成
- 「B → A のマージ」を Django shell から手動で再現：
  - Contact `b-1` の `person=A`、`previous_person=B`、`previous_status='primary'`、`status` は仕様書 §9.4 の遷移に従う（例：`inactive`）
  - Contact `b-2` の `person=A`、`previous_person=B`、`previous_status='active'`、`status` は遷移後の値
  - `merge_log = PersonMergeLog.create(surviving_person=A, merged_person=B, user)` を作成
- C → A のマージも別途実行（複数のマージが surviving=A に存在する状態を作る）：
  - Contact `c-1`（C のプライマリー）を A 配下に付け替え、`previous_person=C`、`previous_status='primary'`、`status='inactive'` 等
  - こちらは `merge_log_C = PersonMergeLog.create(surviving_person=A, merged_person=C, user)` で記録
- `result = merge_log.get_undo_preview()` を呼ぶ（B → A のマージのプレビュー）
- 戻り値の検証：
  - `result['merged_person'] == B`
  - `result['contacts_to_restore']` が QuerySet で、`b-1` と `b-2` が含まれていること
  - `result['contacts_to_restore']` に `a-1` / `a-2`（元から A 配下）、`c-1`（別マージで動いた）が含まれていないこと
  - `result['contacts_remaining_in_surviving']` が QuerySet で、`a-1` / `a-2` / `c-1` が含まれていること
  - `result['contacts_remaining_in_surviving']` に `b-1` / `b-2` が含まれていないこと
- DB が変更されていないこと：
  - `b-1.refresh_from_db()` で `person == A`（戻されていない）
  - `merge_log.refresh_from_db()` で `status == 'undoable'`（変わっていない）
- 戻り値の `contacts_to_restore` / `contacts_remaining_in_surviving` が QuerySet 型であること（list ではなく）

### 7.8 全体確認

- `python manage.py check` がパス
- `python manage.py makemigrations --dry-run` で「No changes detected」が表示されること（メソッド追加だけならマイグレーション不要）

---

## 8. 完了報告内容

作業完了後、以下を報告してほしい。

1. 実装したメソッド一覧（7個）と各メソッドの行数
2. `duplicates/models.py` の差分の概要（どこに何を追加したか）
3. `python manage.py check` の出力
4. `python manage.py makemigrations --dry-run` の出力
5. 動作確認結果（7.1〜7.8 の各観点で実行した内容と結果）
6. `get_for_person` / `get_undoable` を共通化したか個別実装したか、その判断理由
7. 実装中に判断に迷った点・気になった点（あれば）
8. 申し送りメモへの追記候補（A-2d で得た知見で B ブロック以降に役立つもの。なければ「なし」と報告）

---

## 9. 補足

### 9.1 PersonMergeLog 周辺の関連メソッドの実装タイミング

A-2d では7メソッドを実装する。残りの周辺機能は以下のタイミングで実装：

- `record_merge_action(user)` / `record_undo_action(user)` → A-2e（ActionLog 実装と同タイミング）
- マージ実行サービス層（`PersonMergeLog.create()` を呼ぶ側、`lock_past_logs()` を呼ぶ側） → B ブロック
- 復元実行サービス層（`is_undoable()` で事前チェック、`mark_as_undone()` を呼ぶ、Contact の `previous_*` を使った復元処理） → B ブロック
- マージログ一覧画面・復元確認画面（`get_for_person()` / `get_undoable()` / `get_undo_preview()` を使う側） → D ブロック

A-2d では各メソッドが**単体で動くモデルメソッド**として実装すれば足りる。Django shell から直接呼び出せて、戻り値・副作用が確認できる状態にする。

### 9.2 困ったときは

仕様書（v1.4.2 統合最終版）の以下の節を参照：

- メソッド責務 → §10.8.1（クラスメソッド）/ §10.8.2（インスタンスメソッド）
- `PersonMergeLog.create()` の設計趣旨 → §10.8.4
- `get_undo_preview()` の戻り値設計 → §10.8.3
- PersonMergeLog のフィールド定義 → §4.8
- 多重マージ対応 → §9.6
- 復元処理フロー → §9.5.2
- マージ実行フロー → §9.3.1（特に手順8 が `lock_past_logs()` 関連）
- マージ前後の Contact 状態遷移 → §9.4 と別添 PDF
- TextChoices（PersonMergeLog.Status の3値） → §14.4

仕様書だけで判断に迷う場合は、たんたんに確認してほしい（クロード君が壁打ちで対応する）。独自判断で実装を進めない。

特に「設計思想 §3.3 / §3.4 / §3.5」のいずれかで「これは仕様書間違ってないか？」と感じたら、実装を止めて先に確認すること。これらは過去レビュアーが繰り返し誤解した箇所であり、仕様書通りの実装が正解。

---

**（指示書終わり）**
