# A-2b 実装指示書 ／ Contact + ContactFieldConfidence のモデルメソッド実装

**FreeGroup2 v1.4.2 ／ コード君（Claude Code）向け**

---

## 1. 本書の位置づけ

A-2 ブロック（モデルメソッド実装、第10章）の 2 つ目。本書では Contact と ContactFieldConfidence のモデルメソッドを実装する。

A-2a（Person のモデルメソッド）は完了済み（commit b4b34f1）。A-2b は同じブランチ feature/v1.4.2-models で継続する。

---

## 2. 本書のスコープ

### 2.1 やること（実装対象メソッド一覧）

`contacts/models.py` の `Contact` クラスと `ContactFieldConfidence` クラスに、以下の 7 メソッドを実装する。

| # | メソッド | 種別 | 責務 | 仕様書参照 |
|---|---|---|---|---|
| 1 | `contact.fix(form: 'ContactUpdateForm', user)` | Contact インスタンス | フォーム値で自身のフィールドを上書きし、全 ContactFieldConfidence を confirmed 化する | 10.5.1 / 10.5.2 |
| 2 | `contact.get_field_confidences()` | Contact インスタンス | 全フィールドの ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） | 10.5.1 / 10.5.3 |
| 3 | `contact.get_high_fields()` | Contact インスタンス | 実質 high なフィールド集合を返す | 10.5.1 |
| 4 | `contact.is_all_field_confidence_high(fields=None)` | Contact インスタンス | 全 high 判定（引数省略時は全フィールド） | 10.5.1 |
| 5 | `ContactFieldConfidence.get_for_contact(contact)` | クラスメソッド | 全フィールド分の ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） | 10.6.1 / 10.6.2 / 10.6.3 |
| 6 | `ContactFieldConfidence.create_for_contact(contact, confidence_map)` | クラスメソッド | OCR 結果の medium/low フィールドについて一括作成 | 10.6.1 |
| 7 | `ContactFieldConfidence.mark_fields_as_confirmed(contact, field_names, user)` | クラスメソッド | 指定フィールドを確認済み化 | 10.6.1 |

### 2.2 やらないこと

以下は本書のスコープ外。**触らない**。

| 項目 | 本書での扱い |
|---|---|
| `contact.fix()` 内で参照する「全フィールド」の定義 | **DUPLICATE_CHECK_FIELDS（9 フィールド）で代替**。本書の §6.3 / §9.1 参照 |
| `Contact.update_target_fields()` クラスメソッドの実装 | **作らない方針で確定**（仕様書 5.1 で言及されているが、たんたん判断で実装しない） |
| OCR パイプラインから `create_for_contact()` を呼び出す処理 | **B ブロック以降で別タスク**として実施 |
| ContactBaseForm の実装 | **D ブロックで実装**（型ヒントは forward reference で対応） |
| ContactFieldConfidence の CheckConstraint 追加 | **A-1c で実装済み**、変更しない |
| ContactFieldConfidence の save() オーバーライド | **A-1c で実装済み**、変更しない |
| Person / DuplicateCandidate / PersonMergeLog / ActionLog のモデルメソッド | **A-2a 完了済み or A-2c〜A-2e で実施** |
| サービス層関数の実装 | **B ブロックで実施** |
| View 層・Form 層の実装 | **D ブロックで実施** |
| マイグレーションファイルの生成・編集 | **A-1c で完成済み、変更しない** |
| モデル定義（フィールド・制約）の変更 | **A-1c で完成済み、変更しない** |
| コミット & プッシュの実行 | **指示があるまで実行しない** |

「ついでに ContactBaseForm の雛形を作ろう」「ついでに OCR パイプラインから呼び出すように修正しよう」のような前倒し実装は **やらない**。

---

## 3. 仕様書の参照場所

| トピック | 参照先 |
|---|---|
| §10.5 Contact のモデルメソッド詳細（本書のメイン参照先 1） | 統合最終版 §10.5 |
| §10.6 ContactFieldConfidence のモデルメソッド詳細（本書のメイン参照先 2） | 統合最終版 §10.6 |
| §10.5.2 `contact.fix()` の詳細仕様（シグネチャ・責務・呼ばれる場所） | 統合最終版 §10.5.2 |
| §10.5.3 `contact.get_field_confidences()` の戻り値仕様（疑似インスタンス方式） | 統合最終版 §10.5.3 |
| §10.6.2 疑似インスタンスの防御策（CheckConstraint / save() オーバーライド） | 統合最終版 §10.6.2 |
| §10.6.4 ContactFieldConfidence の生成・更新タイミング（3 ケース別） | 統合最終版 §10.6.4 |
| §4.6 ContactFieldConfidence（信頼度メタ DB） | 統合最終版 §4.6 |
| §4.6.1 high レコードの防御策 | 統合最終版 §4.6.1 |
| Contact のフィールド一覧 | 統合最終版 別表 A.5 |
| ContactFieldConfidence のフィールド一覧 | 統合最終版 別表 A.7 |
| DUPLICATE_CHECK_FIELDS（9 フィールドの定数）| 統合最終版 §8.8 / §14.3.5 |

---

## 4. 前提

### 4.1 状態

- A-1a / A-1b / A-1c / A-1d / A-2a は完了済み（ブランチ feature/v1.4.2-models）
- ContactFieldConfidence の CheckConstraint と save() オーバーライドは A-1c で実装済み（A-2b では変更しない）
- たんたんが手動 migrate 実施済み
- DUPLICATE_CHECK_FIELDS は config/constants.py に定数として定義済み（A-1c）

### 4.2 作業ブランチ

`feature/v1.4.2-models`（A-2a から継続）

### 4.3 自宅 PC 開発 DB 方針

自宅 PC の環境は完全に開発用なので、必要があれば既存 DB 全削除可能。動作確認用にテストデータを作って消して OK。

---

## 5. 完了基準

- 7 メソッドすべてが `contacts/models.py` に実装されている
- `python manage.py check` がエラーなく通る
- §7 の動作確認観点（Django shell）がすべてパスする
- 既存テストがあれば壊れない

---

## 6. やってはいけないこと（厳守）

### 6.1 実装範囲の踏み外し禁止

- `Contact.update_target_fields()` を実装すること（**作らない方針で確定**）
- OCR パイプラインから `create_for_contact()` を呼び出す処理を組み込むこと（**B ブロック以降**）
- ContactBaseForm を仮実装・スタブ実装すること（**D ブロックで実装**）
- ContactFieldConfidence の CheckConstraint / save() オーバーライドを変更すること（**A-1c で実装済み**）
- A-2c〜A-2e で扱うモデルメソッドに手を出すこと
- B / C / D ブロックの内容を前倒し実装すること
- モデル定義（フィールド・制約）を変更すること
- マイグレーションファイルを生成・編集すること
- **コミット & プッシュを実行すること**（クロード君の確認後、たんたん経由で別途指示する）

### 6.2 メソッドごとの踏み外しポイント（警告）

実装中に踏み外しやすい点を以下に明示する。仕様書を読めば書いてあるが、特に注意。

- **`contact.fix()` の型ヒント**：`form` 引数の型ヒントは **`'ContactUpdateForm'`（文字列、forward reference）** で書く。ContactBaseForm / ContactUpdateForm はまだ実装されていない（D ブロックで実装）。文字列型ヒントなら import 不要で型エラーも出ない（仕様書 §10.5.2 / 申し送りメモ参照）
- **`contact.get_field_confidences()` の責務分離**：Contact 側は薄いラッパーとして `ContactFieldConfidence.get_for_contact(self)` を呼ぶだけ。実ロジックは ContactFieldConfidence 側に置く（仕様書 §10.5.3 / §10.6.3 参照）
- **疑似インスタンスの save() 禁止**：high フィールドは ContactFieldConfidence の疑似インスタンス（DB 保存しない）として生成するだけ。**save() を呼ばないこと**。save() オーバーライドが ValueError を上げる（仕様書 §10.6.2 / §4.6.1 参照）
- **`mark_fields_as_confirmed` の更新対象**：`confirmed_at` / `confirmed_by` のみを更新。**confidence の値は変更しない**（low / medium はそのまま保持される、仕様書 §8.5.4 参照）

### 6.3 「全フィールド」の扱いについて

仕様書 §10.5.3 / §10.6.1 の戻り値仕様には「全フィールド分のキーが含まれる」と明記されているが、A-2b では **DUPLICATE_CHECK_FIELDS（9 フィールド）で代替**する。

理由：「全フィールド」の本来の定義は ContactBaseForm.Meta.fields（D ブロックで実装予定）に依存するが、A-2b 時点では ContactBaseForm が存在しない。先取り実装を避けるため、A-2b では確定済み定数 DUPLICATE_CHECK_FIELDS を参照する。

D ブロックで Form 実装後、別タスクで `DUPLICATE_CHECK_FIELDS` から `ContactBaseForm.Meta.fields` への切り替えを実施する。

対象メソッド：

- `contact.get_field_confidences()` の dict のキー
- `contact.is_all_field_confidence_high(fields=None)` の引数省略時の対象範囲
- `contact.fix()` の上書き対象フィールド

これらは A-2b では DUPLICATE_CHECK_FIELDS の 9 フィールドのみを扱う。`config/constants.py` の DUPLICATE_CHECK_FIELDS を import して参照する。

---

## 7. 動作確認観点（Django shell）

実装完了後、`python manage.py shell` で以下を順に確認すること。

### 7.1 `ContactFieldConfidence.create_for_contact()` の動作確認

- 任意の Contact を 1 つ用意
- `confidence_map = {'full_name': 'high', 'company': 'medium', 'email': 'low', 'phone': 'high'}` のような dict を作る
- `ContactFieldConfidence.create_for_contact(contact, confidence_map)` を呼ぶ
- 確認 1：DB に作られた ContactFieldConfidence は medium / low の 2 つのみ（high は作られない）
- 確認 2：作られたレコードの confirmed_at は NULL、confirmed_by も NULL
- 確認 3：field_name と confidence の値が confidence_map と一致

### 7.2 `ContactFieldConfidence.get_for_contact()` の動作確認

- 7.1 のセットアップを再利用、または新規に Contact + ContactFieldConfidence を作成
- `ContactFieldConfidence.get_for_contact(contact)` を呼ぶ
- 確認 1：戻り値が dict 形式で、DUPLICATE_CHECK_FIELDS の 9 フィールド全部のキーが含まれる
- 確認 2：DB に存在する medium/low フィールドは DB レコードがそのまま返る
- 確認 3：DB に存在しないフィールド（high 扱い）は疑似インスタンスとして返る（confidence='high'、pk=None）
- 確認 4：疑似インスタンス（confidence='high'）に対して `save()` を呼ぶと ValueError になる

### 7.3 `contact.get_field_confidences()` の動作確認

- 任意の Contact を用意
- `contact.get_field_confidences()` を呼ぶ
- 確認 1：戻り値が `ContactFieldConfidence.get_for_contact(self)` と同一（薄いラッパー）
- 確認 2：DUPLICATE_CHECK_FIELDS の 9 フィールド全部のキーが含まれる

### 7.4 `contact.get_high_fields()` の動作確認

- Contact を用意し、ContactFieldConfidence を以下のパターンで作成：
  - 'company': medium, confirmed_at=None（未確認 medium）
  - 'email': low, confirmed_at=now()（確認済み low → high 扱い）
  - 'phone': medium, confirmed_at=now()（確認済み medium → high 扱い）
  - 'address': low, confirmed_at=None（未確認 low）
- `contact.get_high_fields()` を呼ぶ
- 確認 1：戻り値に full_name / department / title / branch / mobile（DB レコードなし＝疑似 high）が含まれる
- 確認 2：戻り値に email / phone（confirmed_at が記録された低/中）が含まれる
- 確認 3：戻り値に company / address（未確認 low/medium）は含まれない

### 7.5 `contact.is_all_field_confidence_high()` の動作確認

- パターン A：すべて high 扱い（DB レコードなし、または confirmed_at が記録済み）
  - `contact.is_all_field_confidence_high()` が True を返す
- パターン B：1 つでも未確認 low/medium がある
  - `contact.is_all_field_confidence_high()` が False を返す
- パターン C：引数で範囲指定
  - `contact.is_all_field_confidence_high(fields=['full_name', 'company'])` で対象範囲を限定して判定できる

### 7.6 `ContactFieldConfidence.mark_fields_as_confirmed()` の動作確認

- 7.4 のセットアップを再利用（4 つの ContactFieldConfidence レコード）
- `ContactFieldConfidence.mark_fields_as_confirmed(contact, ['company', 'address'], user)` を呼ぶ
- 確認 1：'company' / 'address' の confirmed_at が now() に設定される
- 確認 2：'company' / 'address' の confirmed_by が user に設定される
- 確認 3：'company' / 'address' の confidence の値は変更されていない（medium / low のまま）
- 確認 4：'email' / 'phone'（既に confirmed 済み）は変更されない、または上書き保存される（実装方針による）

### 7.7 `contact.fix()` の動作確認

- 既存 Contact を用意し、ContactFieldConfidence を 2 つ作成（'company': medium, 'email': low、いずれも未確認）
- ContactUpdateForm はまだ実装されていないので、**Form の代わりに簡易な mock オブジェクト**を作る（`get_update_contact()` メソッドだけ持つ最小オブジェクト、Contact インスタンスを返す）
- `contact.fix(mock_form, user)` を呼ぶ
- 確認 1：DUPLICATE_CHECK_FIELDS の 9 フィールドのうち、差分があるものだけが上書きされる
- 確認 2：差分がないフィールドは save() の update_fields に含まれない
- 確認 3：Contact の updated_at が更新される
- 確認 4：'company' / 'email' の ContactFieldConfidence の confirmed_at / confirmed_by が記録される（mark_fields_as_confirmed 経由）
- 確認 5：`self.pk = None` の Contact に対して `fix()` を呼ぶと ValueError が上がる（ガード）

---

## 8. 完了報告内容

作業完了後、以下を報告する。

- 実装した 7 メソッドのリスト（メソッド名と行数の概要）
- 修正したファイルと変更概要（git diff --stat レベル）
- §7 の動作確認結果（各観点ごとに「確認 OK」または「想定外の挙動あり」を明記、想定外の場合は内容を記録）
- `python manage.py check` の出力
- 実装中に判断に迷った箇所があれば、その内容と取った判断（独自判断ではなくクロード君に相談済みの場合は相談内容も含む）

---

## 9. 補足

### 9.1 「全フィールド」を DUPLICATE_CHECK_FIELDS で代替する理由

仕様書 §10.5.3 / §10.6.1 の戻り値仕様は「全フィールド分のキーが含まれる」と明記されており、本来は Contact のユーザー入力対象フィールド全部（21 フィールド前後、ContactBaseForm.Meta.fields で定義予定）が対象。

しかし A-2b 時点では ContactBaseForm がまだ実装されていない（D ブロックで実装）。先取り実装を避けるため、A-2b では確定済み定数 DUPLICATE_CHECK_FIELDS（9 フィールド）で代替する。

D ブロックで Form 実装後、別タスクで切り替える。実装時の参照元を `from config.constants import DUPLICATE_CHECK_FIELDS` で揃えておけば、切り替え時の影響範囲が明確になる。

### 9.2 ContactFieldConfidence の生成・更新タイミング 3 ケース別

仕様書 §10.6.4 で 3 ケース別に整理されている：

- **ケース 1**：新規作成（10/9 番）→ ContactFieldConfidence は作成しない
- **ケース 2**：既存修正（12/13 番、`contact.fix()`）→ 全 confirmed 化
- **ケース 3**：マージ画面 same_card 特殊処理 → 部分 confirmed 化

A-2b で実装する `mark_fields_as_confirmed` は **ケース 2 / 3 の両方から呼ばれる**（field_names 引数で挙動を切り替える）。メソッド自体は「指定された field_names を確認済み化する」だけの単一責任。呼び出し側のコンテキストの違いはサービス層・View 層（B / C / D ブロック）で扱う。

### 9.3 想定される実装の難所

- **疑似インスタンスの生成**：DB に存在しない high フィールドは、メモリ上で `ContactFieldConfidence(contact=contact, field_name='xxx', confidence='high')` のように生成して dict に詰める。**save() は呼ばないこと**（仕様書 §10.6.2 参照）
- **`get_for_contact()` の効率**：DB から medium/low レコードを 1 回のクエリで取得し、残りのフィールドは疑似インスタンスで埋める実装が望ましい。仕様書 §4.7.2（N+1 対策）も参考に
- **`contact.fix()` の差分検出**：差分があるフィールドだけ `update_fields` に含めて save。差分がないフィールドは触らない（A-2a の `Person.set_primary_contact` で同様の手法を使った）
- **mock_form の作成（§7.7）**：ContactUpdateForm が未実装のため、動作確認用に `get_update_contact()` だけを持つ最小オブジェクトを Python の SimpleNamespace や class で作る。実装の参考になりそうなら、Contact のコピーインスタンスを返す簡易実装でよい

### 9.4 判断に迷ったら

実装中に判断に迷ったら、独自判断せずクロード君（サポート担当・たんたんとのチャットセッション）に確認すること。特に：

- 仕様書 §10.5 / §10.6 の記述だけでは実装方針が確定しない場合
- ContactBaseForm 未実装に起因する問題が発生した場合
- 仕様書間の矛盾を発見した場合（仕様正本順位：v1.4.2 統合最終版 → PDF → URL 一覧表 → v0.1.5）

---

**改訂履歴**

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v1.0 | 2026-05-06 | 初版作成 | クロード君（サポート担当） |
