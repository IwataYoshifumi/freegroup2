# **Run_Generate_Duplicate_Candidates 詳細仕様書 v0.1.6**

**FreeGroup2 名刺管理機能 / 重複候補生成タスクの処理フロー詳細**

**作成日：2026年5月4日（v0.1）／改訂：2026年5月5日（v0.1.1 / v0.1.2 / v0.1.3）／2026年5月6日（v0.1.4 / v0.1.5）／2026年5月13日（v0.1.6）**

**作成者：たんたん**

---

# **本書の位置づけ（v0.1.4 追記）**

## **本書と v1.4.2 統合最終版の関係**

本書は **v1.4.2 統合最終版仕様書** の `Run_Generate_Duplicate_Candidates` および関連関数（`generate_duplicate_candidates_for_contact` / `find_duplicate_contacts` / `get_persons_confirmed_as_different`）の処理詳細を定義するものである。

それ以外の領域（Form 設計、モデルメソッド、URL 一覧、マージ処理、UI カスタムタグ、ActionLog 全体設計、命名規則など）は **v1.4.2 統合最終版仕様書を Single Source of Truth とする**。

## **本書を読む前提**

本書を読むコード君（Claude Code）は、以下の前提を踏まえて実装に着手する：

1. **本書は v1.4.2 仕様書の補助文書**。本書と v1.4.2 統合最終版で記述が食い違う場合は、v1.4.2 統合最終版が優先する。ただし `Run_Generate_Duplicate_Candidates` の処理詳細については本書が一次情報源。
2. **関数名は v1.4.2 確定版**。本書では `Run_Generate_Duplicate_Candidates`（短縮版、引数 `limit=100`）を使用する。
3. **v0.2 は作成しない**。v0.1.3 までで「v0.2 で確定する」「v0.2 で詳細化する」とされていた項目は、v0.1.4 で **「v1.4.2 では本書の方針のまま実装し、運用後に実測・チューニングする」** 方針に変更した。コード君は v0.2 を待たずに本書の方針で実装してよい。
4. **rev6/rev7/rev8 で確定した周辺事項は v1.4.2 統合最終版に統合済み**。本書では `Run_Generate_Duplicate_Candidates` の処理に直接関係する事項のみ扱う。
5. **【最重要】重複チェックの実装は本書の方式に必ず従うこと**。具体的には以下の 2 点を本書 4.4 の通りに実装する：
   - **2 段階方式**（ステップ A：ID リスト取得を atomic 外で／ステップ B：ループ内で 1 件ずつ atomic を切ってロック取得）
   - **ロック取得後の 3 段再チェック**（duplicate_checked_at / Contact.status / Person.status）
   
   v1.4.2 統合最終版 12.4 には「1 Contact ごとに transaction.atomic() で囲む」という結論しか書かれていないが、これを素朴に「全体を atomic で囲む」と実装すると `TransactionManagementError` が発生したり、レアケースで `IntegrityError`（partial unique constraint 違反）が発生する。本書 4.4 の 2 段階方式と 3 段再チェックは、この問題への対処として確定した実装パターンであり、省略してはならない。

## **本書から v1.4.2 統合最終版への参照**

本書中で `rev5 4.5`、`rev5 12.6`、`v1.4.1 8.4` などの旧表記が残っている箇所がある。これらはすべて **v1.4.2 統合最終版仕様書の対応する節に統合済み** であるため、コード君は v1.4.2 統合最終版仕様書から該当節を参照すること（例：`rev5 12.6 エラーハンドリング` → v1.4.2 統合最終版の対応する章）。

---

# **第0章 本仕様書の位置づけ**

## **0.1 目的**

本仕様書は、FreeGroup2 における重複候補生成処理（cron 起動の `Run_Generate_Duplicate_Candidates` および関連 2 関数）の処理フロー詳細を定義する。コーディング段階前に、コード君（Claude Code）が独自判断する余地を減らし、実装の正解を仕様書として明文化することを目的とする。

## **0.2 対象範囲**

本仕様書は以下の 4 関数を対象とする。

| 関数 | 配置 | 性質 |
|---|---|---|
| `Run_Generate_Duplicate_Candidates(limit=100)` | duplicates/tasks/duplicate_check_runner.py | 副作用あり（タスク層上位、cron から呼ばれる） |
| `generate_duplicate_candidates_for_contact(contact)` | duplicates/tasks/duplicate_check_runner.py | 副作用あり（タスク層下位、1 Contact 単位） |
| `find_duplicate_contacts(contact, excluded_persons=None)` | duplicates/services/duplicate_detection.py | 準関数（DB 読み取りのみ） |
| `get_persons_confirmed_as_different(person)` | duplicates/services/duplicate_detection.py | 準関数（DB 読み取りのみ） |

## **0.3 既存仕様書との関係**

本仕様書は、以下の既存仕様書の付随詳細仕様書として位置づける。

| 既存仕様書 | 役割 | 本仕様書との関係 |
|---|---|---|
| 名刺画像取り込みOCR仕様書 v1.4.1 統合最終版 | 「何を」「なぜ」を記述する本体仕様書 | 本仕様書は v1.4.1 の 8.9 / 8.10 / 11.6.2 / 11.6.3 / 12 章を補完する詳細仕様 |
| 名刺画像取り込みOCR仕様書 v1.4.2 改訂差分 rev5 | v1.4.1 → v1.4.2 の差分 | 本仕様書の前提となる設計判断はすべて rev5 で確定済み |

将来 v1.4.2 統合版仕様書が作成される際、本仕様書の内容は以下のように組み込まれる想定とする。

| 本仕様書の章 | 統合版での組み込み先 |
|---|---|
| 第2章（find_duplicate_contacts） | 11.6.2 サービス層 |
| 第3章（generate_duplicate_candidates_for_contact） | 11.6.2 タスク層下位 |
| 第4章（Run_Generate_Duplicate_Candidates） | 11.6.2 タスク層上位 |
| 第5章（DuplicateCandidate 生成側で行う履歴参照判断） | 8.6 への追記、および 4.7（DuplicateCandidate モデル）への match_reason / matched_fields フィールド削除 |

## **0.4 段階分け（v0.1.4 で確定）**

本仕様書は当初 v0.1 と v0.2 の 2 段階に分けて作成する想定だったが、v0.1.4 において **v0.2 は作成しない** ことに決定した。理由：v0.1.3 までで明確化された設計判断は実装着手に十分であり、v0.2 で扱う予定だった項目（クエリ実行計画、インデックス効果、トランザクション粒度の妥当性、N=5000 想定の計測）は **実装後の運用フェーズで実測してチューニングする** 方が現実的なため。

| 段階 | 範囲 | 完了基準 |
|---|---|---|
| v0.1（本書） | 3 関数の処理フロー、group_id 発行の最小ルール、関数間の責務分担とトランザクション境界 | `find_duplicate_contacts` をループで回す最小骨格までが動作する |
| ~~v0.2（後続）~~ | **作成しない**。v0.2 で扱う予定だった項目は v0.1.4 で「運用後に実測・チューニング」方針に変更 | - |

v0.1.4 で v0.2 を作成しない方針に変更したため、本書の方針のまま v1.4.2 として実装する。実装してみないと判断つかない箇所（クエリ実行計画、インデックス効果、トランザクション粒度の妥当性）は本番運用後に EXPLAIN や ActionLog のデータをもとに実測してチューニングする。

## **0.5 前提となる設計判断（rev5 確定済み、本書では再議論しない）**

本仕様書の前提となる設計判断は、すべて v1.4.2 改訂差分 rev5 で確定済みである。本仕様書では再議論しない。

| 項目 | 確定箇所 |
|---|---|
| recover 一本化 | rev5 第7章 |
| Django モデルメソッド化の体系 | rev5 第10章 |
| ActionLog の位置づけ（業務機能、業務処理と同トランザクション） | rev5 第9章 |
| 効率化アルゴリズム（OR 絞り込み） | rev5 第8章 |
| N+1 対策（cron 経由は prefetch_related 必須） | rev5 4.7.4 / 8.3 |
| 関数命名規則（Pascal_Snake_Case / snake_case の使い分け） | rev5 第3章 |

ただし、関数名のみ rev6 確定名を先取りする（0.6 参照）。

## **0.6 関数名の rev6 確定名の採用**

タスク層上位関数の名前は、rev5 では `Run_Generate_Duplicate_Candidates` だったが、rev6 で `Run_Generate_Duplicate_Candidates` に簡潔化された（冗長な `_for_Contacts` を削除）。本仕様書は rev5 を本文の基準としつつ、関数名のみ rev6 確定名を採用する。

下位関数 `generate_duplicate_candidates_for_contact` および関連関数は rev5 と rev6 で変更なし。

## **0.7 v0.1 で確定する事項 / v0.1.4 で運用後チューニングに変更した事項**

### **0.7.1 v0.1 で確定する事項**

- 4 関数の処理フロー（Run_Generation / generate / find / get_persons_confirmed_as_different）
- 関数間の責務分担
- トランザクション境界の確定（上位関数のループ内で 1 件ずつ atomic を切る方式、4.4 参照）
- group_id 発行の最小ルール（cron 新規生成側）
- different_person 除外ロジックの実装（v1.4.1 8.9 永続性に基づく、第5章参照）
- DuplicateCandidate の bulk_create 時の各フィールドの値
- person_a / person_b の順序ルールの適用
- v0.1 段階の動作確認観点（最小限）

### **0.7.2 v0.2 に送る予定だった事項（v0.1.4 で運用後チューニングに変更）**

- ActionLog 記録項目の詳細仕様（rev5 12.10 を踏まえた最終確定）
- 失敗系シナリオの網羅（個別 Contact 失敗時、ロック取得失敗時、find 中の例外、ActionLog 書き込み失敗）
- DB 障害時のフォールバック挙動（rev5 9.2.4 を踏まえた具体化）
- 冪等性・リトライの詳細（cron が同じ Contact を再処理する場合の挙動）
- 本格的なテスト観点（単体・結合）
- N=5000 想定のクエリ実行計画（インデックス設計の検証、想定実行時間）
- 12.7 / 12.8 / 12.9 との相互作用の詳細
- バッチ内 group_id 検索の最適化（同一 generate_duplicate_candidates_for_contact 呼び出し内での DB アクセス回数削減）

### **0.7.3 v1.4.2 統合版仕様書への波及（本仕様書外の作業）**

本仕様書の確定内容を踏まえ、v1.4.2 統合版仕様書作成時に以下の修正が必要となる。

- v1.4.1 4.7 の DuplicateCandidate モデル定義表から `match_reason` フィールドと `matched_fields` フィールドを削除（理由：ランクと意味が重複するため）
- v1.4.1 14.1 の TextChoices 一覧から `DuplicateCandidate.MatchReason` を削除
- 改訂理由を v1.4.2 改訂履歴に記載

---

# **第1章 対象4関数の目的と全体像**

## **1.1 4関数の役割分担**

本仕様書で扱う 4 関数は、cron 起動から DuplicateCandidate の生成までを 3 階層で担う。各関数の責務は明確に分離されており、各層は自分の責務だけを担う。

| 階層 | 関数 | 責務 | 起動契機 |
|---|---|---|---|
| 1（タスク層上位） | `Run_Generate_Duplicate_Candidates(limit=100)` | duplicate_checked_at が NULL な Contact を `limit` 件取り出し、1 件ずつ下位関数を呼ぶオーケストレーション | cron（5 分間隔）から `check_duplicates` 管理コマンド経由 |
| 2（タスク層下位） | `generate_duplicate_candidates_for_contact(contact)` | 1 つの Contact について、履歴参照判断（除外対象取得）と find_duplicate_contacts の呼び出しを行い、結果を DuplicateCandidate として DB に書き込む | 上位関数から、または将来の他モジュールから |
| 3（サービス層） | `find_duplicate_contacts(contact, excluded_persons=None)` | 1 つの Contact について、重複候補を計算してタプル列で返す（DB 書き込みなし、除外対象 Person は引数で受け取る） | 下位関数から、および ContactCreateView の警告ダイアログ表示から |
| 3（サービス層） | `get_persons_confirmed_as_different(person)` | 過去にユーザーが「別人」と確定判定した相手 Person オブジェクトのリストを返す（DB 履歴を参照する準関数） | 下位関数から |

## **1.2 cron 起動から DuplicateCandidate 書き込みまでの俯瞰**

全体の流れは以下のとおり。各ステップの詳細は第 2 章以降で記述する。

（1）cron が `check_duplicates` 管理コマンドを起動する。

（2）管理コマンドは `Run_Generate_Duplicate_Candidates(limit=100)` を呼ぶ。

（3）`Run_Generate_Duplicate_Candidates` は、対象 Contact の ID リストを取得する（ロックは取得しない、4.4 参照）。

（4）ID リストを 1 件ずつループし、各 ID について `transaction.atomic()` を張り、その中で `select_for_update(skip_locked=True).prefetch_related('confidences')` で Contact をロック取得し、`generate_duplicate_candidates_for_contact(contact)` を呼ぶ。

（5）`generate_duplicate_candidates_for_contact` は呼ばれた `transaction.atomic()` の中で以下を実行する。

  - `get_persons_confirmed_as_different(contact.person)` を呼んで除外対象 Person リストを取得
  - `find_duplicate_contacts(contact, excluded_persons=...)` を呼んで重複候補のタプル列を取得
  - `assigned_to` の値を 1 度だけ計算する（3.4.1 の決定ロジック）。全 DuplicateCandidate に共通で適用される
  - 各タプルから DuplicateCandidate のインスタンスを構築（person_a / person_b の順序正規化、group_id の発行）
  - `DuplicateCandidate.objects.bulk_create(...)` で一括書き込み
  - `contact.duplicate_checked_at = now()` を更新

（6）`find_duplicate_contacts(contact, excluded_persons=None)` は副作用なしで以下を実行する。

  - フルネーム / メール / 携帯のいずれかが一致する Contact を OR 絞り込みで取得（`excluded_persons` に含まれる Person は NOT IN で除外）
  - 絞り込まれた Contact 1 件ずつに `determine_score_and_rank(contact, candidate)` を呼ぶ
  - rank='none' を除外し、(candidate, score, rank) のタプル列を返す

（7）すべての Contact 処理完了後、`Run_Generate_Duplicate_Candidates` は ActionLog に実行結果を 1 レコード書き込む（v0.1 では「書き込む」とだけ宣言、詳細項目は本書の方針のまま実装し運用後にチューニング）。

## **1.3 トランザクション境界の概要**

トランザクション境界は以下のとおり設計する。

| 範囲 | 境界 |
|---|---|
| 上位関数 `Run_Generate_Duplicate_Candidates` 全体 | `transaction.atomic()` で囲まない（個別 Contact の失敗が他に波及しないように） |
| 上位関数のループ内、1 Contact ごとの処理 | `transaction.atomic()` で囲む。この中で `select_for_update` によるロック取得と、下位関数の処理を実行 |
| 下位関数 `generate_duplicate_candidates_for_contact` 内部 | 内部でも `transaction.atomic()` を張る（呼び出し元のトランザクションがある場合は savepoint としてネストする、Django の標準挙動） |
| ActionLog 書き込み | 上位関数の最後に独立して書き込む（業務処理が成功した後の実行結果記録） |

設計趣旨：rev5 12.6（エラーハンドリング）に従い、個別 Contact の処理でエラーが発生してもログに記録して続行する。1 Contact 単位で `transaction.atomic()` を切ることで、失敗した Contact のみがロールバックされ、他の Contact の処理結果は保持される。Django の `select_for_update` は `transaction.atomic()` 内でクエリ評価されないとエラーを投げる仕様のため、ロック取得は必ず `atomic()` 内で行う必要がある（4.4 参照）。

## **1.4 既存仕様書からの参照**

本仕様書の前提となる関連仕様：

| 項目 | 参照先 |
|---|---|
| 重複検出の比較対象（主コンタクト同士） | v1.4.1 8.2 |
| スコア表 | v1.4.1 8.3 |
| ランク判定（exact_match / possible_high / possible_mid / possible_low / none） | v1.4.1 8.4 |
| group_id（同一起点 Person・同一ランクのグループ識別子） | v1.4.1 8.6 |
| different_person 判定の永続性 | v1.4.1 8.9 |
| 効率化アルゴリズム（OR 絞り込み） | rev5 第8章 |
| `_calculate_score` の準関数化と N+1 対策 | rev5 4.7 |
| `Contact.get_field_confidences()` の戻り値仕様（疑似インスタンス方式） | rev5 10.3.4 |
| 重複チェックのバックグラウンド処理（cron、--limit、多重起動対策、エラーハンドリング） | v1.4.1 12.1〜12.6 |
| ActionLog の位置づけ（業務機能） | rev5 9.2 |
| cron 実行ログ（記録項目） | rev5 12.10 |

---

# **第2章 find_duplicate_contacts(contact, excluded_persons=None) の詳細仕様**

★将来の v1.4.2 統合版仕様書 11.6.2 サービス層節への組み込み対象。

## **2.1 関数定義**

| 項目 | 内容 |
|---|---|
| 関数名 | `find_duplicate_contacts(contact, excluded_persons=None)` |
| 配置 | duplicates/services/duplicate_detection.py |
| 性質 | 準関数（DB 読み取りはするが書き込みなし） |
| 入力 | contact: Contact（重複チェック対象、自身も主コンタクトであること）<br>excluded_persons: list[Person] または None（オプション、絞り込みから除外する Person オブジェクトのリスト。デフォルト None＝除外なし） |
| 出力 | list[tuple]：各要素は (duplicate_contact: Contact, score: int, rank: str) |
| 比較対象 | DB 全体の status='primary' かつ Person.status='active' な Contact（自身を除く、`excluded_persons` に含まれる Person も除外） |
| 絞り込み | フルネーム / メール / 携帯のいずれかが一致（OR 絞り込み） |
| ランク判定 | rank='none' の候補は戻り値に含めない |
| パフォーマンス | cron 経由で呼ばれる場合、候補取得時に `prefetch_related('confidences')` を必須とする（rev5 C-2 対応） |

## **2.2 比較対象の絞り込み条件**

主コンタクト同士の重複検出を素朴に実装すると、N Contact に対して N×(N-1)/2 回の比較が発生する。N=5000 で約 1250 万回となり、現実的な時間で処理できない。

v1.4.1 8.4 のランク判定を逆算すると、possible_low 以上のランクになり得るのは以下のいずれかが満たされる場合に限られる。

| ランク | 必須条件 |
|---|---|
| possible_low | フルネーム一致 |
| possible_mid | フルネーム一致 + メール or 携帯一致 |
| possible_high | 200 点以上（フルネーム不一致でもメール+携帯+所属で達成可能） |
| exact_match | 200 点以上 + 所属5フィールド両方一致 or 両方空 |

つまり、**フルネーム一致 / メール一致 / 携帯一致** のいずれも満たさない Contact は、possible_low 以上のランクにならない。これらの **OR 条件** で対象 Contact を絞り込む。

絞り込み条件（正規化済み値での完全一致）：

- `full_name` 完全一致（v1.4.1 10.3.1 の正規化適用後）
- `email` 完全一致（個人メール / 代表メール問わず、v1.4.1 10.3.4 の正規化適用後）
- `mobile` 完全一致（v1.4.1 10.3.3 の正規化適用後、ハイフン除去・半角数字のみ）

## **2.3 SQL 擬似コード**

絞り込みクエリは以下のとおり。

```
SELECT *
FROM contacts_contact AS c
INNER JOIN persons_person AS p ON c.person_id = p.id
WHERE c.status = 'primary'
  AND p.status = 'active'
  AND c.id != :target_contact_id
  AND p.id NOT IN (:excluded_person_ids)  -- excluded_persons が空または None の場合はこの句を省略
  AND (
    c.full_name = :target_full_name
    OR c.email = :target_email
    OR c.mobile = :target_mobile
  )
```

ただし、target_full_name / target_email / target_mobile が空文字または NULL の場合、それらの条件は OR 句から除外する（空文字同士の一致を絞り込み条件にしない）。

具体的には、Django ORM レベルで以下のように構築する想定とする：

- 入力 contact の full_name / email / mobile のうち、空でないものだけを Q オブジェクトの OR 条件として組み立てる
- **`excluded_persons` の判定は `if excluded_persons:` で行う**。Python の falsy 判定により、None も空リスト `[]` も「除外なし」として同じ扱いになる。判定が True（Person オブジェクトが 1 件以上入っている場合）のときのみ、`exclude(person__id__in=[p.id for p in excluded_persons])` を絞り込みに加える
- Django ORM は空リスト時に SQL の WHERE 句から該当条件を自動的に外すため、生 SQL のような `NOT IN ()` 構文エラーは発生しない（生 SQL を書く場合のみ注意が必要だが、本仕様書では Django ORM 経由の実装を前提とする）

## **2.4 想定インデックス**

OR 絞り込みを高速化するため、以下のインデックスを Contact / Person モデルに設定する。具体的なインデックス効果は運用後の N=5000 想定クエリ実行計画で検証する。

| インデックス対象 | 用途 | 配置 |
|---|---|---|
| Contact: `(status, full_name)` | フルネーム絞り込み用 | contacts/models.py の Contact.Meta.indexes |
| Contact: `(status, email)` | メール絞り込み用 | 同上 |
| Contact: `(status, mobile)` | 携帯絞り込み用 | 同上 |
| Person: `(status,)` | active な Person への JOIN 後絞り込み高速化 | persons/models.py の Person.Meta.indexes |
| Contact: `(created_at,)` | 4.4.1 ステップ A の ORDER BY 用（運用後に必要性検証） | contacts/models.py の Contact.Meta.indexes（運用後に追加判断） |

★ v0.1.2 注：v0.1.1 までは `(status, person__status, full_name)` のように Django ORM 記法で記述していたが、Django Meta.indexes はテーブルをまたぐ複合インデックスを実装できない。本仕様書では Contact 側に `(status, ...)` の 3 つの複合インデックスを置き、Person 側に `(status,)` の単独インデックスを置く構成とする。`person__status='active'` の絞り込みは Person テーブルとの JOIN で実現されるため、Person 側に `status` の単独インデックスがあれば JOIN 後の絞り込みが効率的に動く。

設計趣旨：絞り込みは Contact.status による絞り込みが先行するため、これを複合インデックスの先頭に置く。フルネーム / メール / 携帯はそれぞれ独立した絞り込み軸であり、3 つの複合インデックスを別々に定義する（OR 絞り込みは MySQL / PostgreSQL ともに UNION 的に処理されるため、各軸ごとに別インデックスがあれば効率的）。

本書では上記方針までを確定し、実装後に EXPLAIN で確認して必要に応じて運用後に調整する。

## **2.5 prefetch_related の必須化（cron 経由のみ）**

`_calculate_score` は rev4 で純関数から準関数に性質変更され、内部で `contact.get_field_confidences()` を呼ぶ。これにより、`find_duplicate_contacts` が大量の候補を返した場合、各候補について confidences の追加クエリが発生し、N+1 クエリ問題が発生する。

呼び出し元ごとの対応：

| 呼び出し元 | 対応 | 理由 |
|---|---|---|
| `generate_duplicate_candidates_for_contact`（cron 経由） | `prefetch_related('confidences')` を**必須** | --limit 100 × 候補数で N+1 リスクが顕在化する |
| `ContactCreateView`（手動作成時の警告表示） | `prefetch_related('confidences')` は任意 | 1 Contact ずつしか呼ばれないため影響が小さい |

cron 経由の呼び出しでは、`find_duplicate_contacts` 内部で候補取得時に `prefetch_related('confidences')` を呼ぶ。加えて、上位関数 `Run_Generate_Duplicate_Candidates` でも対象 contact 自身を `prefetch_related('confidences')` 済みで取得する（4.4 参照）。

## **2.6 絞り込み後の処理**

絞り込まれた Contact 群について、1 件ずつ以下を実行する。

1. `determine_score_and_rank(contact, candidate)` を呼んで (score, rank) を取得
2. rank == 'none' の候補は戻り値に含めない
3. (candidate, score, rank) のタプルとして結果リストに追加

`determine_score_and_rank` は rev5 4.5 で確定した公開準関数で、内部で `_calculate_score`（準関数）と `_determine_rank`（純関数）を呼ぶ。`_calculate_score` は `contact.get_field_confidences()` を呼ぶため、prefetch_related が効いていれば追加クエリは発生しない。

## **2.7 戻り値の構造**

戻り値は以下の構造のリストを返す。

```
[
    (contact_b, 220, 'possible_high'),
    (contact_c, 150, 'possible_mid'),
    (contact_d, 50, 'possible_low'),
]
```

- 各タプルの第 1 要素は重複候補の Contact インスタンス
- 第 2 要素は合計スコア（int）
- 第 3 要素はランク文字列（'exact_match' / 'possible_high' / 'possible_mid' / 'possible_low'）
- rank='none' は含まない
- 候補が 0 件の場合、空リスト `[]` を返す

スコアによるソートは行わない（呼び出し側が必要に応じてソートする）。

## **2.8 設計趣旨**

### **2.8.1 なぜ準関数として独立しているか**

`find_duplicate_contacts` は cron 経由（DuplicateCandidate を DB に書き込む）と ContactCreateView 経由（警告ダイアログに表示するのみ、DB 書き込みなし）の両方から呼ばれる。判定ロジックの一貫性を保つため、両方が同じ関数を共有する設計とする。

DB 書き込みは呼び出し側の責務であり、本関数は「重複候補のタプル列を返す」までに責務を限定する。

### **2.8.2 なぜ rank='none' を戻り値から除外するか**

呼び出し側で再度 rank='none' を除外するロジックを書かなくて済むようにするため。本関数の戻り値は「重複候補として扱うべきもののみ」を意味する。

「rank='none' になったが念のため記録したい」というユースケースは v1.4.2 では発生しない。発生した場合は、別関数として切り出す。

### **2.8.3 なぜ Contact 側に 3 つの単軸複合インデックスを別々に定義するか**

`(status, full_name, email, mobile)` のような大複合インデックスを作っても、OR 絞り込みでは各軸が独立して使われるため、フルネーム / メール / 携帯のインデックスは別々のものとして機能する。各絞り込み軸ごとに必要十分なインデックスを 3 つ用意する方が、書き込みコストとのバランスが良い。

Person.status による絞り込みは Contact 側の複合インデックスではなく Person 側の単独インデックスで対応する（M-3 確定方針）。テーブルをまたぐ複合インデックスは Django Meta.indexes として実装できないため、JOIN 後の絞り込みで Person.status の単独インデックスが効率的に動く構成とする。

## **2.9 v1.4.2 では本書の方針のまま実装、運用後に再評価する事項**

- N=5000 想定の実 SQL 実行計画と EXPLAIN 結果
- インデックス効果の実測値
- find 中の例外発生時の扱い（本書では呼び出し側に伝播するとだけ宣言、運用後に具体化）

---

# **第3章 generate_duplicate_candidates_for_contact(contact) の最小骨格仕様**

★将来の v1.4.2 統合版仕様書 11.6.2 タスク層下位節への組み込み対象。

## **3.1 関数定義**

| 項目 | 内容 |
|---|---|
| 関数名 | `generate_duplicate_candidates_for_contact(contact)` |
| 配置 | duplicates/tasks/duplicate_check_runner.py |
| 性質 | 副作用あり（DB 書き込み） |
| 入力 | contact: Contact（重複チェック対象、prefetch_related('confidences') 済みで渡される前提） |
| 出力 | None（戻り値で結果は返さない、内部で DB 書き込みを完結する） |
| トランザクション | 関数内で `transaction.atomic()` を張る（呼び出し側のトランザクションに依存しない自己完結型） |

## **3.2 呼び出し前の前提**

本関数は以下を前提として呼ばれる。

- contact は `status='primary'` かつ `person.status='active'` であること
- contact は `select_for_update(skip_locked=True)` で取得済みであること（多重起動対策）
- contact は `prefetch_related('confidences')` 済みであること（N+1 対策、rev5 C-2）

これらの前提は呼び出し元（`Run_Generate_Duplicate_Candidates` または ContactCreateView）が保証する。本関数では前提のチェックは行わない（防御的プログラミングは 運用後に必要性を判断する）。

## **3.3 処理フロー**

`transaction.atomic()` で囲んだ中で、以下の手順を順に実行する。

（1）`get_persons_confirmed_as_different(contact.person)` を呼び、過去にユーザーが「別人」と確定判定した相手 Person オブジェクトのリストを取得する。

（2）`find_duplicate_contacts(contact, excluded_persons=...)` を呼び、重複候補のタプル列を取得する。`excluded_persons` には手順（1）で取得した Person リストを渡す。

（2.5）**事前フィルタ（v1.4.2 で追加、X-3 ランナバグ修正）**：`contact.person` と既に pending DuplicateCandidate として組まれている Person ID 集合を取得し、その集合に含まれる candidate をスキップする。

- 取得方法：`person_a=contact.person AND review_status='pending'` のクエリで person_b の ID 集合、`person_b=contact.person AND review_status='pending'` のクエリで person_a の ID 集合を、**person_a / person_b 別々の 2 クエリ** で取得する。
- 2 つの ID 集合を合算（`set` の union）して「既存 pending ペアの相手 Person ID 集合」を得る。
- 手順（2）のタプル列を走査し、`candidate.person.id` が上記集合に含まれるものをスキップする。
- スキップ判定は **person_id レベル** で行う（DuplicateCandidate インスタンス比較ではない）。

★ なぜ 2 クエリに分けるか：`Q(person_a=p) | Q(person_b=p)` の OR 検索は SQLite 3.51.2 の planner bug でインデックスを使わない最悪計画が選ばれるケースがあり、件数が増えると性能が落ちる。person_a / person_b を別クエリで取得することで、両方ともインデックスを使った高速な実行計画を保証できる。

★ なぜ事前フィルタが必要か：partial unique constraint（3.6）違反を **事前に回避**するため。同一 cron バッチ内で同 Person ペアの両側が処理対象になるケース（新規 OCR 取り込み・テストデータ生成スクリプト経由の流入等）で、12.7 / recover の事前整理経路を経由せずに pending DC が衝突する経路がある。事前フィルタで衝突候補自体を除外することで、IntegrityError の発生経路を排除する。

（3）（2.5）の事前フィルタ通過後のタプル列が空の場合、（4）（5）（6）をスキップして（7）に進む。

（4）`assigned_to` の値を 1 度だけ計算する。3.4 の決定ロジックに従い、入力 contact から `business_card.original_image.user` または `created_by` のどちらかを取得する。この値はループ内で生成するすべての DuplicateCandidate に共通で適用される（候補ごとに違う値にはならない）。

（5）（2.5）の事前フィルタを通過した各タプル `(candidate, score, rank)` から、DuplicateCandidate のインスタンスを構築する。フィールドの値は 3.4 を参照。`assigned_to` は手順（4）で計算した値を使う。

（6）構築したインスタンス群を `DuplicateCandidate.objects.bulk_create(...)` で一括書き込みする。

（7）`contact.duplicate_checked_at = timezone.now()` を設定し、`contact.save(update_fields=['duplicate_checked_at'])` で保存する。事前フィルタで全候補がスキップされた場合も、処理完了として duplicate_checked_at を更新する。

（8）トランザクションを正常終了する（コミット）。

## **3.4 DuplicateCandidate インスタンスの各フィールドの値**

各タプル `(candidate, score, rank)` に対応する DuplicateCandidate のフィールドを以下のように設定する。

| フィールド | 値 | 補足 |
|---|---|---|
| `id` | `uuid.uuid4()` | 自動付与（UUIDField のデフォルト） |
| `group_id` | 第5章 5.3 のロジックで決定 | 同 Person 起点・同ランクの pending を検索して再利用、なければ新規 UUID |
| `person_a` | 順序ルール（v1.4.1 4.7.1）で決定 | 入力 contact.person と candidate.person のうち、created_at が古い方。同時刻なら id（UUID 文字列比較）の小さい方 |
| `person_b` | 順序ルールの残り | person_a と対 |
| `score` | タプルの第 2 要素 | 整数 |
| `rank` | タプルの第 3 要素 | 'exact_match' / 'possible_high' / 'possible_mid' / 'possible_low' |
| `review_status` | `'pending'` | 固定値（システム生成直後） |
| `review_result` | `[]`（空配列） | 未判定 |
| `note` | `''`（空文字） | 未判定 |
| `assigned_to` | 3.4.1 の決定ロジックで取得（ループ前に 1 回計算、全 DuplicateCandidate に同じ値） | OCR 由来の場合は BusinessCard.original_image.user、手動入力の場合は Contact.created_by（v1.4.1 17.2 参照） |
| `reviewed_by` | NULL | 未判定 |
| `reviewed_at` | NULL | 未判定 |

### **3.4.1 assigned_to の決定ロジック**

`assigned_to` は、入力 contact のアップロードユーザーを示すフィールドである。決定ロジックは以下のとおり。

```
if contact.business_card_id is not None:
    # OCR 由来：BusinessCard.original_image 経由でアップロードユーザーを取得
    assigned_to = contact.business_card.original_image.user
else:
    # 手動入力：Contact 作成者を採用
    assigned_to = contact.created_by
```

このロジックは入力 contact 1 件につき 1 度だけ計算し、ループ内で生成するすべての DuplicateCandidate に同じ値を設定する。候補ごとに値が変わるわけではないため、ループ内で都度計算する必要はない。

★パフォーマンス上の注意：`contact.business_card.original_image.user` のアクセスチェーンは、上位関数 `Run_Generate_Duplicate_Candidates` の 4.4.2 でロック取得時に `select_related('business_card__original_image__user', 'created_by')` を呼ぶことで、追加クエリを発生させずに取得できる構成になっている。本関数では select_related の指定は呼び出し元の責務とし、本関数内では追加クエリを発生させない前提で実装する。

★ assigned_to が NULL になるケース：v1.4.1 4.4 / 4.7 のフィールド定義により、Contact.created_by は `FK(User, SET_NULL, null=True)` のため、ユーザー削除等で NULL になりうる。OCR 由来の場合の `BusinessCard.original_image.user` も同様に NULL になる可能性がある。両者とも NULL の場合、`assigned_to=NULL` として DuplicateCandidate に保存される（DuplicateCandidate.assigned_to も `FK(User, SET_NULL, null=True)` のため保存可能）。レビュー画面での担当者表示や KPI 集計時の挙動（v1.4.1 17.2 の「自動割り当て」前提から外れる扱い）は v1.4.2 では本書の方針のまま実装し、運用後に再評価する。v0.1 段階では NULL を許容する。

★ v0.1.1 注：v1.4.1 4.7 の DuplicateCandidate モデル定義表に存在する `match_reason` フィールドと `matched_fields` フィールドは、v1.4.2 で削除する。理由：両フィールドの情報はランク（exact_match / possible_high / possible_mid / possible_low）と一致したフィールドの並列表示で表現できており、別軸で持つ意義が薄い。運用後のチューニング時もスコアの付け方とランクの閾値調整で対応するため、別軸の追加情報は不要。本仕様書ではこの方針に従い、新規生成 DuplicateCandidate には match_reason / matched_fields を設定しない。v1.4.2 統合版仕様書作成時に、v1.4.1 4.7 のモデル定義表から両フィールドを削除する作業が必要となる（0.7.3 参照）。

## **3.5 トランザクション境界**

本関数の `transaction.atomic()` は、`get_persons_confirmed_as_different` の呼び出しから contact.duplicate_checked_at の更新までを 1 トランザクションで囲む。

理由：

- DuplicateCandidate の bulk_create と duplicate_checked_at の更新は、同時に成功するか同時に失敗するかのどちらかでなければならない。途中失敗した場合、duplicate_checked_at が更新されないことで次回 cron で自動的に再処理される（v1.4.1 12.5 参照）
- `get_persons_confirmed_as_different` および `find_duplicate_contacts` は副作用なしの準関数なので、本関数のトランザクション内で呼んでも安全

呼び出し元（`Run_Generate_Duplicate_Candidates`）も上位ループ内で 1 Contact ごとに `transaction.atomic()` を張る（4.4 参照）。本関数の `transaction.atomic()` は呼び出し元のトランザクションのネスト（savepoint）として動作する（Django の標準挙動）。1 Contact 単位で独立してコミット / ロールバックされるため、ある Contact の失敗が他の Contact に波及しない。

★ 二重ネスト atomic の挙動について：上位関数（4.4.2）で `with transaction.atomic():` を張った内側で、本関数（3.3）が再度 `transaction.atomic()` を張る形になるが、これは Django として有効な使い方である。Django の `transaction.atomic()` はネストされた場合、**外側のトランザクションは通常のトランザクションとして動作し、内側のトランザクションは savepoint として動作する**。内側のトランザクションが例外でロールバックされても、savepoint の範囲だけがロールバックされ、外側のトランザクションは継続できる（ただし本仕様では内側で発生した例外は外側にも伝播させてロールバックするため、実質的に二重ネストでも振る舞いは「1 Contact 単位でコミット / ロールバック」と同じになる）。コード君が「二重ネストで大丈夫か」と迷う必要はない。

## **3.6 partial unique constraint との関係（v1.4.2 改訂）**

DuplicateCandidate モデルには、`review_status='pending'` に限定した partial unique constraint が設定されている（v1.4.1 4.7）。

```
UniqueConstraint(fields=['person_a', 'person_b'], condition=Q(review_status='pending'))
```

この制約により、bulk_create が IntegrityError で失敗するケースが理論上ありうる。

【v1.4.2 改訂：事前フィルタで衝突を未然に回避】 v1.4.2 改訂前は「12.7 / recover が事前に整理する経路でのみ pending DC が作られる」想定で衝突は実運用上発生しないとしていたが、以下のケースで成立しない経路があった：

- 同一 cron バッチ内で同 Person ペアの両側（contact_a と contact_b）が `duplicate_checked_at=NULL` で処理対象になるケース（新規 OCR 取り込みで両側が同じバッチに入る、テストデータ生成スクリプト経由の流入、等）
- 上記ケースでは、最初の contact 処理で生成された pending DC を、次の contact 処理が認識せず重複生成しようとして IntegrityError

X-3 ランナバグ修正（v1.4.2、本仕様書 v0.1.6 で反映）で **事前フィルタ（3.3 手順 2.5）** を追加し、衝突候補を bulk_create 前に除外する設計に変更した。これにより、IntegrityError は実運用で発生しない想定が再び成立する。

- 12.7 の処理が Contact 編集時に必ず発火し、関連 pending を invalidated 化する（旧来の経路）
- マージ実行時の recover 処理（rev5 7.3）が同 Person ペアの pending を整理する（旧来の経路）
- `duplicate_checked_at = NULL` の Contact のみが本関数の処理対象となるため、過去 pending の存在は cron 起動時点では想定されない（旧来の経路）
- 上位関数 `Run_Generate_Duplicate_Candidates` の 4.4.3 で 3 段の再チェック（duplicate_checked_at / Contact.status / Person.status）を行うため、ロック取得から processing までの間の状態変化による競合経路は排除される（旧来の経路）
- **事前フィルタ（3.3 手順 2.5）が同一 cron バッチ内の両側処理ケースを吸収する（v1.4.2 で追加）**

4.4.3 の 3 段再チェックは引き続き有効。事前フィルタは「衝突を bulk_create より前に除外する」レイヤー、3 段再チェックは「ロック取得から processing までの状態変化に対する防御」レイヤーで、責務が異なる別レイヤーとして共存する。

### **3.6.1 IntegrityError 発生時の挙動（v1.4.2 改訂）**

万一 IntegrityError が発生した場合の挙動は以下のとおり。`bulk_create` の引数として `ignore_conflicts=True` は **付けない**（v0.1.2 確定、v1.4.2 でも維持）。

```
DuplicateCandidate.objects.bulk_create(candidates)
# ignore_conflicts=True は付けない
```

挙動：

- IntegrityError は例外として上位関数（`Run_Generate_Duplicate_Candidates` のループ内 try ブロック）に伝播する
- 当該 contact の `transaction.atomic()` ブロックが例外でロールバックされる（DuplicateCandidate の bulk_create も、その前後の処理もすべて取り消される）
- `contact.duplicate_checked_at` が NULL のまま残る
- 次回 cron 起動時に、当該 contact が再度処理対象に含まれる（rev5 12.6 のエラーハンドリング方針と整合）

【v1.4.2 改訂：発生時の解釈】 v1.4.2 改訂前は「IntegrityError は実運用で発生しない想定」だけ書いていたが、事前フィルタ（3.3 手順 2.5）追加により発生経路がより明確に排除された。万一 IntegrityError が発生した場合は、**事前フィルタの実装漏れまたは race condition の証拠** であり、業務データ・コードベースの整合性異常として扱う。当該 contact のトランザクションをロールバックして上位ループに伝播させる挙動は §1.3 / §4.5 と整合する。

★ `ignore_conflicts=True` を付けない理由（v0.1.2 確定）：

- 事前フィルタと 4.4.3 の 3 段再チェックで競合経路を排除しているため、IntegrityError は実運用で発生しない想定
- `ignore_conflicts=True` を付けると「衝突をなかったことにする」挙動になり、後から「なぜこの候補が作られなかったのか」が追えない（隠蔽的）
- Django の標準挙動を活かし、競合が起きたら例外で気付ける状態の方が、開発・運用上の透明性が高い
- たんたんの設計思想「異常を隠さない」と整合する

## **3.7 設計趣旨**

### **3.7.1 なぜ generate_* プレフィックス（snake_case）か**

本関数は副作用あり（DB 書き込み）の複合処理であり、rev5 13.2.1 のプレフィックス表により `generate_*` を採用する。

加えて、本関数はタスク層上位（`Run_*`）から呼ばれる「1 単位の処理」であり、Pascal_Snake_Case の公開サービスではなく snake_case の公開関数とする（rev5 13.2.2 の「タスク層の下位関数」枠）。

### **3.7.2 なぜ contact_id ではなく contact インスタンスを引数に取るか**

rev5 で `run_duplicate_check_for_contact(contact_id)` から `generate_duplicate_candidates_for_contact(contact)` に改名された際、引数も ID から Contact インスタンスに変更された。

理由は以下のとおり。

- 呼び出し元（`Run_Generate_Duplicate_Candidates`）は既に Contact インスタンスを取得済みであり、ID から再取得するのは無駄なクエリ
- prefetch_related('confidences') 済みのインスタンスを引数で渡すことで、本関数内で再取得する必要がない（N+1 対策の効果を維持）
- ContactCreateView 経由でも同様に Contact インスタンスを渡せる

### **3.7.3 なぜ自己完結型のトランザクション境界か**

本関数の `transaction.atomic()` は、上位関数のループに依存しない。これにより、上位関数は「1 件失敗したら次へ」というシンプルな制御で済む。

将来 Celery などの非同期処理基盤に移行する際（v1.4.1 12.11）、本関数を Celery タスクとしてそのまま登録できる設計でもある。

## **3.8 v1.4.2 では本書の方針のまま実装、運用後に再評価する事項**

- IntegrityError 発生時の扱い詳細
- 個別 Contact 失敗時のログ出力内容
- find_duplicate_contacts 中の例外発生時の扱い
- bulk_create のバッチサイズ調整（候補数が極端に多い場合の対策）

---

# **第4章 Run_Generate_Duplicate_Candidates(limit=100) の最小骨格仕様**

★将来の v1.4.2 統合版仕様書 11.6.2 タスク層上位節への組み込み対象。

## **4.1 関数定義**

| 項目 | 内容 |
|---|---|
| 関数名 | `Run_Generate_Duplicate_Candidates(limit=100)` |
| 配置 | duplicates/tasks/duplicate_check_runner.py |
| 性質 | 副作用あり（DB 読み取り＋下位関数呼び出し＋ ActionLog 書き込み） |
| 命名カテゴリ | `Run_*`（cron / タスクから呼ばれる、Pascal_Snake_Case） |
| 入力 | limit: int（処理する Contact 件数の上限、デフォルト 100） |
| 出力 | None（戻り値で結果は返さない、内部で完結） |
| 起動契機 | cron（5 分間隔）から `check_duplicates` 管理コマンド経由 |

## **4.2 呼び出し関係**

```
crontab
  └─ check_duplicates 管理コマンド
       └─ Run_Generate_Duplicate_Candidates(limit=100)
            └─ generate_duplicate_candidates_for_contact(contact)  ※ループで複数回呼ばれる
                 ├─ get_persons_confirmed_as_different(contact.person)
                 └─ find_duplicate_contacts(contact, excluded_persons=...)
```

`check_duplicates` 管理コマンドは本関数を 1 回呼ぶだけのオーケストレータ。引数の `--limit` を本関数の `limit` 引数に渡す。

## **4.3 処理フロー**

以下の手順を順に実行する。

（1）処理開始時刻 `started_at = timezone.now()` を記録する。

（2）対象 Contact の ID リストを取得する。クエリは `transaction.atomic()` の外で実行し、ロックは取得しない（4.4 参照）。

（3）取得した ID リストを 1 件ずつループする。各 contact_id について以下を実行する。

  - try ブロックの中で、`with transaction.atomic():` ブロックを開始
  - その中で `Contact.objects.select_for_update(skip_locked=True).prefetch_related('confidences').filter(id=contact_id).first()` で Contact をロック取得
  - 取得結果が None の場合（他の worker がロック中）、当該 contact_id はスキップして次へ進む
  - 取得結果が Contact インスタンスの場合、`generate_duplicate_candidates_for_contact(contact)` を呼ぶ
  - 成功時：処理件数カウンタをインクリメント
  - 例外発生時：エラーログに contact_id と例外内容を記録し、エラー件数カウンタをインクリメント、ループは続行（rev5 12.6 準拠）。`with transaction.atomic():` ブロック内で発生した例外は、当該 contact のトランザクションをロールバックさせるが、ループは継続する

（4）すべての contact_id 処理完了後、ActionLog に実行結果を 1 レコード書き込む（4.6 参照）。

（5）関数を正常終了する。

## **4.4 対象 Contact の取得（2 段階方式）**

Django の `select_for_update` は `transaction.atomic()` 内でクエリ評価されないと `TransactionManagementError` を投げる仕様である。一方で、本関数全体を 1 つの `transaction.atomic()` で囲むと、ループ中の 1 件失敗で全件ロールバックされ、rev5 12.6 のエラーハンドリング方針（個別失敗で続行）と矛盾する。

そのため、対象 Contact の取得を 2 段階に分ける設計とする。

### **4.4.1 ステップ A：対象 Contact の ID リスト取得（atomic 外、ロックなし）**

```
contact_ids = list(Contact.objects.filter(
    duplicate_checked_at__isnull=True,
    status='primary',
    person__status='active',
).order_by('created_at', 'id').values_list('id', flat=True)[:limit])
```

各句の役割：

| 句 | 役割 |
|---|---|
| `duplicate_checked_at__isnull=True` | 未チェックの Contact のみ対象 |
| `status='primary'` | 主コンタクトのみ対象（v1.4.1 8.2） |
| `person__status='active'` | active な Person に紐づく Contact のみ対象（merged / archived は除外） |
| `order_by('created_at', 'id')` | 確定的な順序を保証（FIFO 処理、デバッグ容易性確保）。同時刻の Contact がある場合は id（UUID 文字列比較）でタイブレーク |
| `values_list('id', flat=True)` | ID だけ取得（オブジェクト全体を取得しない、メモリ効率を考慮） |
| `[:limit]` | 件数制限（v1.4.1 12.2）。デフォルト 100 件 |

★ ORDER BY のインデックス効果について：created_at にインデックスがない場合、ORDER BY のためのソートコストが発生する。N=5000 規模では実用上問題ない見込みだが、N が大きくなった場合は created_at のインデックス追加を 運用後に検討する。

★ スキップ発生時の実処理件数について：4.4.2 のステップ B で 3 段の再チェックによりスキップが発生した場合、ステップ A で取得した limit 件のうち実際に処理される件数は limit 件未満になる。たとえば limit=100 で 30 件がスキップされた場合、当該 cron 実行での実処理は 70 件で終わる。残りの cron 余力（30 件分）は次回 cron まで未活用となるが、本仕様の運用規模（1 人開発・社内利用、N=5000 想定で候補数は 0〜数件）では実害ない見込みである。実処理件数を limit に近づける最適化（ステップ A の取得件数を `limit + α` にする、スキップ後に追加取得するなど）は 運用後に評価する。

このステップではロックを取得しない。ID リストを得たらすぐにステップ B のループに移る。

### **4.4.2 ステップ B：ループ内で 1 件ずつ atomic ＋ロック取得**

ステップ A で取得した ID リストを 1 件ずつループし、各 contact_id について以下を実行する。

```
for contact_id in contact_ids:
    try:
        with transaction.atomic():
            contact = Contact.objects.select_for_update(
                skip_locked=True
            ).select_related(
                'person',
                'business_card__original_image__user',
                'created_by',
            ).prefetch_related(
                'confidences',
            ).filter(id=contact_id).first()
            
            if contact is None:
                # 他の worker がロック中、または対象 Contact が消えた
                continue
            
            # ステップ A 以降の状態変化に対する 3 段再チェック（4.4.3 参照）
            if contact.duplicate_checked_at is not None:
                # 他の worker がすでに処理完了済み
                continue
            
            if contact.status != 'primary':
                # ステップ A 以降に Contact.status が変わった（編集・マージなど）
                continue
            
            if contact.person.status != 'active':
                # ステップ A 以降に Person.status が変わった（merged / archived など）
                continue
            
            generate_duplicate_candidates_for_contact(contact)
            # ここでカウンタをインクリメント
    except Exception as e:
        # エラーログに contact_id と例外内容を記録
        # エラーカウンタをインクリメント
        continue
```

各句の役割：

| 句 | 役割 |
|---|---|
| `with transaction.atomic():` | 1 contact ごとのトランザクション境界。Django の `select_for_update` が要求する atomic コンテキスト |
| `select_for_update(skip_locked=True)` | 多重起動対策（v1.4.1 12.3）。複数 worker が同じ Contact を重複処理しないようにロック取得、取得失敗 Contact は `first()` が None を返してスキップ |
| `select_related('person', 'business_card__original_image__user', 'created_by')` | 後続の状態チェック（contact.person.status）と assigned_to 計算（contact.business_card.original_image.user / contact.created_by）で追加クエリを発生させないため、関連オブジェクトを JOIN で同時取得（rev5 C-2、3.4.1 参照） |
| `prefetch_related('confidences')` | N+1 対策（rev5 C-2）。Contact の get_field_confidences() で追加クエリが発生しないようにする。`generate_duplicate_candidates_for_contact` 内で `find_duplicate_contacts` 経由で `_calculate_score` が呼ばれるため、入力 contact 側の prefetch が必要 |
| `.filter(id=contact_id).first()` | ID で 1 件取得。`first()` を使うことで、ロック取得失敗時に None を返す（DoesNotExist 例外を投げない） |
| 3 段再チェック（duplicate_checked_at / Contact.status / Person.status） | ステップ A から B の間の状態変化に対する防御（4.4.3 参照） |

### **4.4.3 ステップ A とステップ B の間の状態変化への対処（v0.1.2 で確定）**

ステップ A で ID リストを取得した後、ステップ B でロック取得を試みるまでの間に、以下のような状態変化が発生する可能性がある。

- **状態変化 1**：他の worker が同じ contact_id を先に処理して duplicate_checked_at を更新した
- **状態変化 2**：ContactUpdateView 等で当該 contact が編集され、Contact.status が primary から変わった
- **状態変化 3**：当該 Person がマージ実行で merged 化、または archived 化された

これら 3 種の状態変化は、Django の `select_for_update(skip_locked=True)` だけでは検知できない。`skip_locked` は「他 worker がロック中の行をスキップ」する仕様であり、「他 worker が処理完了して既に解放した行」は通常通りロック取得できてしまう。そのため、ロック取得後に 3 段の再チェックを行い、どれか 1 つでも条件が合わなければスキップする。

| 再チェック | 条件 | 失敗時の挙動 |
|---|---|---|
| 1 | `contact.duplicate_checked_at is None` | `continue`（次の contact_id へ） |
| 2 | `contact.status == 'primary'` | `continue` |
| 3 | `contact.person.status == 'active'` | `continue` |

これら 3 つの再チェックを v0.1 段階で必須として確定する（v1.4.2 で実装する（本書で確定））。理由：再チェックなしで進むと、まれに DuplicateCandidate の partial unique constraint 違反（IntegrityError）が発生する可能性があるため（CR-1 / CR-2 のレビュー指摘で確定）。

### **4.4.4 「2 段階方式」の設計趣旨**

ロックなしで先に ID リストを取得し、ループ内で 1 件ずつロック取得する方式には、以下のメリットがある。

- ループ中の 1 件失敗で全件ロールバックされない（個別 atomic なので、失敗した contact のみロールバック）
- `select_for_update` の atomic 要件を満たす（atomic コンテキスト内でクエリ評価される）
- ロック保持時間が最小限（1 contact の処理時間だけロックを保持し、すぐ解放）
- `skip_locked` により、他 worker がロック中の contact は素直にスキップできる

クエリ回数は ID 取得 1 回 + ロック取得 limit 回で、`--limit 100` の場合 101 回。1 回のクエリは ms オーダーなので実用上問題なし。

## **4.5 エラーハンドリング**

個別 Contact の処理でエラーが発生した場合、以下のとおり処理する。

- エラーログ（icecream / Django logging）に Contact ID と例外内容を記録する
- エラー件数カウンタをインクリメントする
- ループは続行する（次の Contact の処理に進む）
- 失敗した Contact は `duplicate_checked_at` が NULL のままなので、次回 cron で自動的に再試行される（v1.4.1 12.6）

本関数自体は例外を呼び出し元（管理コマンド）に伝播させない。すべての Contact の処理を最後まで試みる。ただし、致命的なエラー（DB 接続喪失など）でループ自体が継続できない場合は、try ブロックの外で例外として伝播する。

## **4.6 ActionLog 書き込み**

すべての Contact 処理完了後、ActionLog に実行結果を 1 レコード書き込む。

書き込み方式：`ActionLog.record(...)` クラスメソッド直接呼び（rev5 9.2.2 / 12.10）。本処理はモデルインスタンスがない場面のため、インスタンスメソッド経由ではなくクラスメソッド直接呼びを使う。

書き込み内容（rev5 12.10 / 9.4）：

| フィールド | 値 |
|---|---|
| `action` | `'executed'` |
| `user` | NULL（cron 実行＝システム実行のためユーザーなし） |
| `content_type` | NULL |
| `object_id` | NULL |
| `object_repr` | `'check_duplicates'`（管理コマンド名） |
| `extra` | 後述の dict |
| `note` | `''`（空文字、v0.1 では補足なし） |

`extra` の内容（rev5 12.10）：

| キー | 内容 |
|---|---|
| `search_target_count` | duplicate_checked_at が NULL の Contact 総数（処理対象になりうる総数） |
| `processed_count` | 実際に処理した件数（--limit で制限後） |
| `hit_contacts` | 重複候補が 1 件以上返った Contact 数 |
| `candidates_generated` | 新規作成した DuplicateCandidate の総数 |
| `rank_breakdown` | exact_match / possible_high / possible_mid / possible_low の内訳 dict |
| `errors` | エラーで処理失敗した Contact 数 |
| `duration_seconds` | 処理時間（now() - started_at） |
| `status` | `'success'` / `'partial'` / `'failed'` |

`status` の判定ルール（v0.1 暫定、v1.4.2 では本書の暫定方針のまま実装し、運用後に確定）：

- errors == 0 かつ processed_count > 0：`'success'`
- errors > 0 かつ processed_count > errors：`'partial'`
- processed_count == 0、または errors == processed_count：`'failed'`

★ v0.1 注：ActionLog 書き込み自体は本関数のトランザクション外で実行する。書き込み失敗時のフォールバックは rev5 9.2.4 の方針に従うが、詳細実装は 運用後に記述する。

## **4.7 処理時間の見積もり**

rev5 8.6 の見積もりに従う。

- 100 件 × `find_duplicate_contacts(contact)` の処理時間（約 100ms）= 10 秒
- 5 分間隔の cron なら十分余裕

実際の処理時間は運用後に N=5000 想定で計測する。

## **4.8 設計趣旨**

### **4.8.1 なぜ Run_* カテゴリ（Pascal_Snake_Case）か**

本関数は cron / タスクから呼ばれる「処理フロー全体を担う主役関数」であり、rev5 13.2.2 の `Run_*` カテゴリに該当する。

`Execute_*` は View 層からのユーザー操作起点、`Mark_as_*` は状態遷移系、`Extract_*` はパイプライン処理から、というカテゴリ区別がある中で、cron / タスク起動の主役関数は `Run_*` とする。

### **4.8.2 なぜ rev6 で `_for_Contacts` を削除したか**

rev5 では `Run_Generate_Duplicate_Candidates` だったが、rev6 で `Run_Generate_Duplicate_Candidates` に簡潔化された。

理由：

- 「Generation_of_Duplicate_Candidates」（候補群の生成）と「generate_duplicate_candidates_for_contact」（1 Contact について候補を生成）の対比で、上位 / 下位の階層が読み取れる
- 上位は複数形が暗示されており（Generation の対象が Candidates 群）、`_for_Contacts` は冗長

rev5 13.2.6（変数・引数の命名方針）の「省略しない、ただし冗長な要素は除く」という思想に沿った調整。

### **4.8.3 なぜ本関数全体を transaction.atomic() で囲まないか**

ループ全体を 1 トランザクションで囲むと、ループ中の 1 件失敗で全件ロールバックされる。これは rev5 12.6 の「個別 Contact の処理でエラーが発生した場合、その Contact のみスキップ」という方針と矛盾する。

そのため、トランザクション境界は本関数のループ内に 1 contact ごとの `transaction.atomic()` を配置する設計とする（4.4 参照）。下位関数 `generate_duplicate_candidates_for_contact` 内部にも `transaction.atomic()` があるが、これは Django の標準挙動として savepoint としてネストする。本関数は単純なループで複数の独立したトランザクションを実行する。

### **4.8.4 なぜ ActionLog 書き込みをトランザクション外に置くか**

ActionLog の書き込みは「全 Contact 処理が終わった後の実行結果記録」であり、業務処理（個別 Contact の DuplicateCandidate 生成）とは別の責務である。

業務処理の途中で ActionLog を書くと、業務処理の失敗時に ActionLog だけ残ってしまう不整合が起きうる。本関数では、すべての Contact 処理が完了した後にまとめて書き込むことで、ActionLog の内容が「実際に行われた処理の結果」と一致することを保証する。

なお、本関数自体の異常終了（例外がループ外で発生した場合）には ActionLog は書き込まれない。これは v1.4.2 では本書の方針のまま実装し、運用後に再評価する（finally 句で部分結果を書くか、書かないか）。

なお、rev5 9.2.1 で定める「ActionLog は業務処理と同トランザクション、書き込み失敗 = 業務処理失敗として全体ロールバック」のルールは、単一の業務処理（マージ実行・別人判定等）に対するものである。本関数（Run_Generate_Duplicate_Candidates）の ActionLog 書き込みは「複数の独立した業務処理（個別 Contact の DuplicateCandidate 生成）を集約した実行記録」であり、各 Contact 処理本体はすでに各々の `transaction.atomic()` で確定済みである。本関数の ActionLog はこれら個別処理の集約結果として、最後に独立して書き込む。

## **4.9 v1.4.2 では本書の方針のまま実装、運用後に再評価する事項**

- ActionLog 書き込み失敗時のフォールバック詳細
- 致命的エラーで本関数が異常終了した場合の ActionLog 書き込み挙動
- search_target_count の算出方法（COUNT クエリのコスト評価）
- status 判定ロジックの最終確定
- N=5000 想定での実処理時間計測

---

# **第5章 DuplicateCandidate 生成側で行う履歴参照判断**

★将来の v1.4.2 統合版仕様書 8.6 への追記対象、および新関数 `get_persons_confirmed_as_different` の追加対象。

## **5.1 概要**

`generate_duplicate_candidates_for_contact` は、find_duplicate_contacts でスコア計算結果を得たうえで、DuplicateCandidate を新規生成する。この生成プロセスでは、DuplicateCandidate テーブルや他の履歴テーブルを参照する判断が複数発生する。

本章では、これら「DuplicateCandidate 生成側で行う履歴参照判断」を一箇所に集約して記述する。

| 判断 | 目的 | 配置 |
|---|---|---|
| different_person 除外 | 過去にユーザーが「別人」と確定判定した相手 Person を絞り込みから除外する（v1.4.1 8.9 永続性） | 5.2（関数 `get_persons_confirmed_as_different`） |
| group_id 発行 | 連続レビュー UX のため、同 Person 起点・同ランクの pending を 1 つの GID にまとめる | 5.3 |

これらの判断は、いずれも「スコア計算（純判定ロジック）」とは別の責務であり、`find_duplicate_contacts` ではなく `generate_duplicate_candidates_for_contact` 側で実施する。これにより、`find_duplicate_contacts` を純粋なスコア計算ロジックとして保ちつつ、履歴参照判断は呼び出し側に集約する。

## **5.2 get_persons_confirmed_as_different(person) 関数の詳細仕様**

### **5.2.1 関数定義**

| 項目 | 内容 |
|---|---|
| 関数名 | `get_persons_confirmed_as_different(person)` |
| 配置 | duplicates/services/duplicate_detection.py |
| 性質 | 準関数（DB 読み取りのみ） |
| 入力 | person: Person（重複チェックの起点となる Person） |
| 出力 | list[Person]：過去にユーザーが「別人」と確定判定した相手 Person オブジェクトのうち、現在 `status='active'` な Person のみのリスト。0 件の場合は空リスト |

### **5.2.2 処理ロジック**

入力 person を含む `review_status='different_person'` な DuplicateCandidate を検索し、相手側 Person オブジェクトのうち `status='active'` のもののみを取得して返す。

SQL 擬似コード：

```
SELECT DISTINCT p.*
FROM persons_person AS p
WHERE p.status = 'active'
  AND p.id IN (
    SELECT
        CASE
            WHEN person_a_id = :input_person_id THEN person_b_id
            ELSE person_a_id
        END AS other_person_id
    FROM duplicates_duplicatecandidate
    WHERE review_status = 'different_person'
      AND (person_a_id = :input_person_id OR person_b_id = :input_person_id)
)
```

Django ORM レベルでの実装は、`Q(person_a=person) | Q(person_b=person)` の OR 検索で `review_status='different_person'` を絞り込み、両側の Person ID を抽出して入力 person 以外の Person オブジェクトを取得し、最後に `status='active'` でフィルタする形になる（具体的な実装はコード君の判断、ただし戻り値の型と意味は本仕様書の通り）。

★注：上記の SQL 擬似コードでは `SELECT DISTINCT p.*` としているが、サブクエリの IN (SELECT ... CASE ...) ですでに Person ID は重複なく抽出されているため、外側の DISTINCT は実質冗長である。Django ORM 経由の実装では `.distinct()` を呼ばなくても結果は重複しない（`exclude(pk=person.pk)` と `Q(person_a=person) | Q(person_b=person)` のサブクエリで自動的に重複排除される）。

### **5.2.3 戻り値の構造**

戻り値は Person オブジェクトのリスト。

- 過去に「別人」と確定判定した相手 Person のうち、**現在 `status='active'` の Person のみ**を含む
- 過去に別人判定されたが、その後 merged や archived になった Person は含まれない
- 過去に「別人」と確定判定した相手 Person が 1 人もいない場合（または全員が active ではない場合）、空リスト `[]` を返す
- 同じ相手 Person について複数回 different_person 判定された履歴がある場合（マージ → 復元 → 再判定など）、Person オブジェクトは重複なく 1 つだけリストに含む
- リストの順序は不定

### **5.2.4 設計趣旨**

**なぜ Person オブジェクトリストを返すか（ID リストではなく）**

呼び出し元の `find_duplicate_contacts` では、`excluded_persons` 引数として受け取った Person リストから ID を抽出して NOT IN 条件に使う。Person オブジェクトとして渡すことで、将来「除外と同時に Person の他の情報も使いたい」といった拡張があった場合に対応しやすい。ID への変換は呼び出し側の責務とする。

**なぜ「ユーザーが別人と判定した相手 Person」を返すか（review_result の値で絞り込まないか）**

review_status='different_person' に至る理由（review_result の値、たとえば same_name / ocr_error / other_different）は判定理由を示すものであり、判定方向（マージ vs 別人）は review_status に集約されている。本関数は「ユーザーが別人と確定判定したペアの相手」を全て返すことが責務であり、judging の理由による分岐は行わない。

**なぜ active な Person のみを返すか**

通常運用（cron 経由の重複チェック）では、`find_duplicate_contacts` の絞り込み対象が `Person.status='active'` に限定されているため、merged や archived な Person を除外対象として返しても実際には使われない。本関数の戻り値を active のみに限定することで、以下の効果がある。

- 戻り値が「実際に除外対象として意味がある Person」だけになり、呼び出し側の処理が無駄にならない
- 関数の挙動が直感的になり、コード君が「merged が混ざっているのは不具合では？」と迷わない
- find_duplicate_contacts の active フィルタと整合する

**なぜ将来の拡張用引数（status_filter 等）を持たせないか**

v0.1 段階で「将来のユースケース（KPI 集計、archived Person の再 active 化対応など）」のための引数を増やすと、コード君が「この引数いつ使うの？」と迷う原因になる。YAGNI 原則に従い、必要になった時点で別関数として切り出す方針とする（たとえば `get_all_persons_ever_confirmed_as_different(person)` のような形）。これはたんたんの設計思想「補助レコードに過剰な情報を持たせない」とも整合する。

## **5.3 group_id 発行の最小ルール（cron 新規生成側）**

### **5.3.1 group_id の役割（再確認）**

group_id は「同一起点 Person・同一ランクの DuplicateCandidate のまとまり」を識別する UUID である。レビュー画面（DuplicateCandidateGroupView）が group_id 単位で動き、ユーザーが連続レビューできる仕組みを提供する（rev5 7.2 / v1.4.1 8.6）。

A vs B、A vs C、A vs D のように同じ Person 起点の重複候補が複数ある場合、これらが同じ group_id を持つことで、ユーザーは「A 起点の連続レビュー」として一連で処理できる。

group_id を持つレコードは 2 種類の経路で作成される。

| 経路 | 関数 | 役割 |
|---|---|---|
| 新規生成（cron） | `generate_duplicate_candidates_for_contact` | 重複検出時に新規作成 |
| 復活（マージ実行時の recover） | `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` | old_candidate から group_id をコピー（rev5 7.3 / 10.5.1） |

本節は前者の「新規生成」側のルールを定める。後者の「復活」側は rev5 で `create_recovered_from` クラスメソッド経由でコピーすることが確定している（再議論しない）。

### **5.3.2 cron 新規生成時の group_id 発行ロジック**

`generate_duplicate_candidates_for_contact(contact)` 内で、find_duplicate_contacts が返したタプル列から DuplicateCandidate を構築する際、各 candidate について以下のロジックで group_id を決定する。

入力：

- 起点 Person（contact.person）
- candidate Person（duplicate_contact.person）
- ランク（rank）

ロジック：

（1）「起点 Person を含む person_a または person_b に持ち、かつ rank が一致し、かつ review_status='pending' の DuplicateCandidate」を DB から検索する。

```
DuplicateCandidate.objects.filter(
    Q(person_a=contact.person) | Q(person_b=contact.person),
    rank=rank,
    review_status='pending',
).first()
```

（2）見つかった場合、その DuplicateCandidate の group_id を再利用する。

（3）見つからなかった場合、新規 UUID（`uuid.uuid4()`）を発行する。

このロジックを candidate 1 件ごとに実行する。同じバッチ内で、起点 Person・ランクが同じ複数の candidate がある場合、同じ group_id が付くようにする（バッチ内でのキャッシュは 運用後に詳細化）。

【v1.4.2 補足】 3.3 手順 (2.5) の事前フィルタで除外された候補には group_id 発行を行わない（DuplicateCandidate インスタンス自体を構築する手順 (5) に到達しないため、自然にそうなる）。本ロジック (1)〜(3) は事前フィルタを通過した候補のみを対象とする。

★ v0.1 段階で「バッチ内キャッシュ最適化」を 運用後に再評価する妥当性根拠：rev5 8.5 の見積もりにより、N=5000 規模でも 1 contact あたりの候補数は 0〜数件と想定されている。candidate 1 件ごとに 1 回の group_id 検索クエリ（インデックスを使った高速な検索）が走るため、1 contact あたり 0〜数回の追加クエリにとどまる。limit=100 件処理で最大数十回〜数百回程度のクエリ増加であり、5 分間隔の cron 実行では実害ない見込みである。N=5000 想定での実測は 運用後の実測で行い、必要に応じて最適化を実装する。

### **5.3.3 person_a / person_b の順序ルールとの関係**

DuplicateCandidate.person_a / person_b は v1.4.1 4.7.1 の順序ルールで正規化される（created_at が古い方が person_a、同時刻なら id 文字列比較で小さい方）。

group_id 検索時、起点 Person が person_a 側か person_b 側かは決まっていないため、`Q(person_a=...) | Q(person_b=...)` の OR 検索で両側を見る必要がある。

設計趣旨：group_id は「起点 Person の視点でグルーピング」する識別子であり、person_a / person_b のどちら側かに依存しない概念である。検索時は OR で両側を見るのが自然。

### **5.3.4 ランク違いの扱い**

同じ Person ペア（A, B）について、ランクが違う pending DuplicateCandidate は同時に存在しない（partial unique constraint で禁止、v1.4.1 4.7）。

しかし、起点 Person A について、別の candidate との関係でランクが異なるケースはありうる。

- A vs B：rank='possible_high'、group_id=G1
- A vs C：rank='possible_mid'、group_id=G2

このように、同じ起点 A でもランクが違えば group_id は別になる。これは rev5 7.2 の「同じ起点 Person・同じランクの候補をまとめる」仕様と整合している。

### **5.3.5 同 Person ペアが既に別状態で存在する場合**

同じ Person ペア（A, B）について、過去に判定済み（merged / invalidated）の DuplicateCandidate が存在する場合、それらの group_id は新規生成側では参照しない（5.3.2 のクエリは review_status='pending' のみを見る）。

したがって、過去判定済みのレコードは group_id 発行ロジックに影響しない。新規 pending レコードの group_id は、起点 Person の他の pending と同じ group_id があれば再利用、なければ新規 UUID を発行する（5.3.2 のロジック）。

なお、different_person 判定済みのペアは 5.2 の `get_persons_confirmed_as_different` により `find_duplicate_contacts` の段階で除外されるため、本ロジックの考慮対象外である。

## **5.4 設計趣旨**

### **5.4.1 なぜ履歴参照判断を generate 側に集約するか**

`find_duplicate_contacts` はスコア計算の純粋なロジックであり、フィールド値（contact / candidate）と confidence の情報だけで結果が決まる。DuplicateCandidate テーブルや他の履歴テーブルを参照しない設計とする。

履歴参照判断（different_person 除外、group_id 発行、**既存 pending DC の存在チェック**）はすべて `generate_duplicate_candidates_for_contact` 側で実施し、`find_duplicate_contacts` には除外対象 Person を引数として渡す形にする。これにより：

- `find_duplicate_contacts` のテストが書きやすくなる（Mock 不要、純粋な入出力検証）
- `find_duplicate_contacts` を ContactCreateView から呼ぶ際、履歴参照を最小限にできる
- 「DB 履歴を見る判断」の責務が generate 側に一極集中する

【v1.4.2 拡張】 X-3 ランナバグ修正で「既存 pending DC の存在チェック」（3.3 手順 2.5 の事前フィルタ）も generate 側の責務として加わった。これにより、`find_duplicate_contacts` の純粋性（履歴参照なし）を維持しつつ、pending 衝突を generate 側で事前回避する設計となる。

### **5.4.2 なぜ「pending のみを見る」か**

新規生成時の group_id 検索は、`review_status='pending'` の既存レコードのみを参照する。

理由：

- merged / invalidated は確定済みのレコードであり、新規 pending と同じ group_id を共有する意味がない
- 過去の group_id が再利用されると、レビュー画面での「グループ単位の連続レビュー」が意味を失う（既に判定済みのレコードが混ざる）
- pending のみを見ることで、「現時点でレビュー対象となっているグループ」と整合する group_id 発行ができる

### **5.4.3 なぜ「最初に見つかった 1 件」を再利用するか**

5.3.2 のロジックで `.first()` を使い、最初に見つかった 1 件の group_id を再利用する設計とした。

理論的には、起点 Person・同ランク・pending の候補が複数あれば、それらは既に同じ group_id を共有しているはず（partial unique constraint と既存ロジックの帰結として）。そのため、`.first()` で取った 1 件の group_id は、その group の代表値として有効である。

万一複数の異なる group_id を持つ pending レコードが存在した場合の扱いは v1.4.2 では本書の方針のまま実装し、運用後に再評価する（v0.1 段階では発生しない想定）。

### **5.4.4 なぜ recover 側との分離が必要か**

recover 側（`DuplicateCandidate.create_recovered_from`）は、merged_person 起点の他 GID にあった候補を、surviving_person 起点の同じ GID に置き換えて再復帰させる。この場合、old_candidate からの group_id コピーが本質である（rev5 7.3）。

cron 新規生成側は recover とは別の経路で動き、独立して group_id を発行する。両者が混ざると、recover で意図的にコピーした group_id が新規生成で上書きされる事故が起きうるため、関数を分けて責務を明確化している。

### **5.4.5 ContactCreateView 経由での excluded_persons 扱い**

`find_duplicate_contacts` の `excluded_persons` 引数はオプション（デフォルト None）である。これにより、cron 経由（generate_duplicate_candidates_for_contact から呼ぶ）と ContactCreateView 経由（手動作成時の警告ダイアログから呼ぶ）の両方が同じ関数を共有できる。

ContactCreateView 経由では、Contact 作成「前」に警告ダイアログを表示するため、入力中の Contact に対応する Person はまだ DB に存在しない。そのため `get_persons_confirmed_as_different` を呼び出すことができず、`excluded_persons=None` で `find_duplicate_contacts` を呼ぶことになる。これは v0.1 段階の制約であり、v1.5.0 以降で「Person が確定する前の警告でも過去の different_person 履歴を考慮する」仕組みは別途検討する。

## **5.5 v1.4.2 では本書の方針のまま実装、運用後に再評価する事項**

- バッチ内（同じ generate_duplicate_candidates_for_contact 呼び出し内）で複数 candidate に対する group_id 検索の最適化（DB アクセス回数削減）
- 万一複数の異なる group_id を持つ pending レコードが発生した場合の扱い
- group_id の集計・分析クエリ（KPI 用）への影響評価
- ContactCreateView 経由での excluded_persons 拡張（v1.5.0 以降の検討項目として整理）

---

# **第6章 v0.1 段階の動作確認観点**

本章は v0.1 段階の最小限の動作確認観点を記述する。本格的なテスト観点（単体テスト・結合テストの網羅）は v1.4.2 では本書の方針のまま実装し、運用後に再評価する。

## **6.1 動作確認の目的**

v0.1 段階では「`find_duplicate_contacts` をループで回すところまで完成させる」ことが目標である。動作確認の目的は以下の 2 点に絞る。

（1）3 関数が連動して、cron 起動から DuplicateCandidate の DB 書き込みまで一連で動作すること。

（2）find_duplicate_contacts が想定どおり OR 絞り込みで効率的に動作すること（N=5000 想定の本格計測は運用後、本書では小規模データで定性的確認）。

## **6.2 確認観点（最小限）**

以下 6 つの観点で動作確認する。実際の確認手順・期待結果の詳細は 運用後に記述する。

| # | 確認観点 | 確認方法の方針 |
|---|---|---|
| 1 | 重複候補ゼロ件のケースで、duplicate_checked_at が更新されること | 重複候補がない Contact を 1 件用意し、Run_Generate_Duplicate_Candidates を実行。DuplicateCandidate が作られず、当該 Contact の duplicate_checked_at に値が入ることを確認 |
| 2 | 重複候補が複数件あるケースで、すべて DuplicateCandidate として書き込まれること | 同姓同名の Contact を 3 件用意し、1 件を起点として Run_Generate_Duplicate_Candidates を実行。残り 2 件分の DuplicateCandidate が作られることを確認 |
| 3 | フルネーム / メール / 携帯のいずれにも一致しない Contact が候補に上がらないこと | 完全に異なる属性の Contact を多数用意し、絞り込みで候補ゼロになることを確認 |
| 4 | person_a / person_b の順序ルールが適用されること | created_at が違う Contact ペアと、created_at が同時刻の Contact ペアの両方で確認 |
| 5 | group_id が起点 Person・同ランクで共有されること | 同じ起点 Person について複数の候補が出るケースで、同ランクの DuplicateCandidate が同じ group_id を持つことを確認。加えて、DB クエリログを観察して group_id 検索が candidate 数と同じだけ走ることを確認（運用後の最適化対象として 5.5 に記載済み） |
| 6 | --limit が効くこと | duplicate_checked_at=NULL の Contact を limit 件以上用意し、limit 件のみ処理されることを確認 |
| 7 | different_person 判定済みのペアが再度候補に上がらないこと | Person A vs Person B を different_person 判定済みの状態で、Person A を起点として Run_Generate_Duplicate_Candidates を実行。Person B が候補に上がらない（DuplicateCandidate が作られない）ことを確認。`get_persons_confirmed_as_different(A)` が B を含むリストを返すこと、およびその結果が `find_duplicate_contacts` に渡されて NOT IN で除外されることを併せて確認 |
| 8 | 同一 cron バッチ内で同 Person ペアの両側が処理対象になっても IntegrityError なしで完走すること（v1.4.2 で追加、X-3 ランナバグ修正の確認観点）| テストデータ生成スクリプト経由などで、同 Person ペアの両側（contact_a と contact_b）が `duplicate_checked_at=NULL` の状態を作る。`dev_create_test_contact_data --reset → check_duplicates --limit 100` で再現可能。事前フィルタ（3.3 手順 2.5）により衝突候補が除外され、両側の処理が IntegrityError なしで完走することを確認 |

## **6.3 確認しない事項（運用後にチューニング）**

v0.1 段階では以下は確認対象外とする。

- N=5000 想定の処理時間・SQL 実行計画
- ActionLog 書き込みの内容詳細（v0.1 では「書き込まれること」だけ確認）
- 失敗系シナリオ（個別 Contact 失敗時、ロック取得失敗時、ActionLog 書き込み失敗）
- DB 障害時のフォールバック動作
- 多重起動の競合動作（select_for_update + skip_locked の挙動）

これらは運用後に本格的なテスト観点として詳細化する。

## **6.4 確認用データの方針**

v0.1 動作確認は、開発環境（自宅 PC または実家 PC）の SQLite で実施する想定。本番想定の N=5000 規模ではなく、N=10〜50 程度の小規模データで定性的に確認する。

確認用データの作成は、Django shell または fixture / factory を使う。具体的な手順は 運用後に記述する。

---

# **第7章 運用後にチューニングする範囲の宣言**

本書の方針で v1.4.2 として実装した後、以下の範囲を運用後に実測・チューニングする。

## **7.1 ActionLog 記録項目の最終確定**

- search_target_count / processed_count / hit_contacts / candidates_generated / rank_breakdown / errors / duration_seconds / status の各項目の正式定義
- status 判定ロジックの最終確定（success / partial / failed の境界）
- ActionLog の extra フィールドの JSON スキーマ確定
- search_target_count の算出方法（COUNT クエリのコスト評価）

## **7.2 失敗系シナリオの網羅**

- 個別 Contact の処理失敗時のログ出力内容（icecream / Django logging）
- find_duplicate_contacts 中の例外発生時の扱い
- DuplicateCandidate.bulk_create 時の IntegrityError 発生時の扱い（partial unique constraint 違反など）
- ロック取得失敗時の挙動確認（select_for_update + skip_locked が想定通り動作することの確認、複数 worker 起動時の競合動作確認、1 Contact 処理中に他の worker が同じ Contact を取得しないことの確認を含む）
- 致命的エラー（DB 接続喪失など）でループ自体が継続できない場合の扱い

## **7.3 DB 障害時のフォールバック**

- ActionLog 書き込み失敗時のフォールバック動作（rev5 9.2.4 を踏まえた具体化）
- ファイルログへの障害記録の出力フォーマット
- 開発環境（標準出力）と本番環境（ログファイル）の切り替え方針

## **7.4 冪等性・リトライの詳細**

- cron が同じ Contact を再処理する場合の挙動（duplicate_checked_at が NULL に戻った Contact）
- bulk_create が部分的に失敗した場合のリカバリ
- generate_duplicate_candidates_for_contact が複数回呼ばれた場合の冪等性確認

## **7.5 本格的なテスト観点**

- 単体テスト（find_duplicate_contacts、get_persons_confirmed_as_different、generate_duplicate_candidates_for_contact、Run_Generate_Duplicate_Candidates の各関数単位）
- 結合テスト（cron 起動から ActionLog 書き込みまでの一連動作）
- エッジケーステスト（候補 0 件、候補 1 件、候補多数、ランク違いの混在など）
- 12.7（Contact 編集時の処理）との相互作用テスト
- 12.8（マージ実行時の DuplicateCandidate 処理）との相互作用テスト
- 12.9（recheck_duplicates）との相互作用テスト

## **7.6 N=5000 想定のクエリ実行計画**

- インデックス設計の検証（EXPLAIN による実測）
- 想定実行時間の計測
- prefetch_related の効果測定（N+1 が解消されているか）
- 必要に応じてインデックス追加・調整
- バッチ内（同一 generate_duplicate_candidates_for_contact 呼び出し内）で複数 candidate に対する group_id 検索の最適化（DB アクセス回数削減、第5章 5.5 にも記載）

## **7.7 12.7 / 12.8 / 12.9 との相互作用の詳細**

- 12.7（Contact 編集時の処理）：本関数とのレース条件評価
- 12.8（マージ実行時の DuplicateCandidate 処理）：本関数と recover の同時実行時の扱い（rev5 12.10 の方針を踏まえた具体化）
- 12.9（recheck_duplicates）：全件再判定実行時の本関数の挙動

---

# **巻末別表A 本仕様書で登場する関数一覧**

## **A.1 本仕様書で詳細仕様を定義する関数（4関数）**

| 関数名（rev5/rev6 確定名） | 配置 | 性質 | 章 |
|---|---|---|---|
| `Run_Generate_Duplicate_Candidates(limit=100)` | duplicates/tasks/duplicate_check_runner.py | 副作用あり（タスク層上位、Pascal_Snake_Case） | 第4章 |
| `generate_duplicate_candidates_for_contact(contact)` | duplicates/tasks/duplicate_check_runner.py | 副作用あり（タスク層下位、snake_case） | 第3章 |
| `find_duplicate_contacts(contact, excluded_persons=None)` | duplicates/services/duplicate_detection.py | 準関数 | 第2章 |
| `get_persons_confirmed_as_different(person)` | duplicates/services/duplicate_detection.py | 準関数 | 第5章 5.2 |

## **A.2 本仕様書から呼ばれる関連関数（rev5 確定済み、本書では再議論しない）**

| 関数名 | 配置 | 性質 | 関連箇所 |
|---|---|---|---|
| `determine_score_and_rank(contact_a, contact_b)` | duplicates/services/duplicate_score.py | 準関数 | 2.6 で呼ばれる |
| `_calculate_score(contact_a, contact_b)` | duplicates/services/duplicate_score.py | 準関数（rev4 で純関数→準関数に変更） | determine_score_and_rank の内部 |
| `_determine_rank(score, contact_a, contact_b)` | duplicates/services/duplicate_score.py | 純関数 | determine_score_and_rank の内部 |
| `Contact.get_field_confidences()` | contacts/models.py | インスタンスメソッド（rev5 C-3 で疑似インスタンス方式） | _calculate_score から呼ばれる |
| `ActionLog.record(...)` | （ActionLog モデル） | クラスメソッド | 4.6 で呼ばれる |

## **A.3 関数名の旧名・新名対応（rev3 → rev4 → rev5 → rev6）**

| rev3 以前 | rev4 | rev5 | rev6（現行） |
|---|---|---|---|
| `run_duplicate_check_for_contact(contact_id)` | （変更なし） | `generate_duplicate_candidates_for_contact(contact)` | （変更なし） |
| - | - | `Run_Generate_Duplicate_Candidates(limit=100)` | `Run_Generate_Duplicate_Candidates(limit=100)` |
| `find_duplicate_candidates(contact)` | `find_duplicate_contacts(contact)` | （変更なし） | （変更なし） |
| `calculate_score(contact_a, contact_b, confidence_map_a, confidence_map_b)` | `_calculate_score(contact_a, contact_b)`（純関数→準関数） | （変更なし） | （変更なし） |
| `determine_rank(...)` | `_determine_rank(...)`（内部関数化） | （変更なし） | （変更なし） |
| - | `determine_score_and_rank(contact_a, contact_b)`（公開関数として新規追加） | （変更なし） | （変更なし） |

詳細な関数移行表は v1.4.2 改訂差分 rev5 冒頭の「rev3 → rev4 → rev5 関数移行表」を参照。

## **A.4 本仕様書で扱わない関連関数**

以下の関数は本仕様書の対象外だが、参考として記載する。

| 関数名 | 役割 | 関連節 |
|---|---|---|
| `recover_duplicate_candidates(merged_person, surviving_person)` | マージ実行時の DuplicateCandidate 後処理（rev5 7.3） | 5.4.4 で参照 |
| `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` | recover での新規 DuplicateCandidate 作成（rev5 10.5.1） | 5.3.1 で参照 |
| `invalidate_pending_candidates(contact)` | 12.7 専用、Contact 編集時の pending 無効化（rev5 4.6） | 7.7 で参照 |

---

# **巻末別表B 本仕様書で参照する既存仕様書節への対照表**

## **B.1 v1.4.1 統合最終版への参照**

| 本仕様書の章 | 参照先 | 内容 |
|---|---|---|
| 1.4 | v1.4.1 4.7 | DuplicateCandidate モデル定義 |
| 1.4 | v1.4.1 4.7.1 | person_a / person_b の順序ルール |
| 2.2 | v1.4.1 8.3 | スコア表 |
| 2.2 | v1.4.1 8.4 | ランク判定 |
| 2.2 | v1.4.1 8.2 | 比較対象（主コンタクト同士） |
| 2.2 | v1.4.1 10.3.1 | full_name の正規化 |
| 2.2 | v1.4.1 10.3.3 | mobile / phone の正規化 |
| 2.2 | v1.4.1 10.3.4 | email の正規化 |
| 3.6 | v1.4.1 4.7 | partial unique constraint |
| 3.4 | v1.4.1 17.2 | assigned_to の自動割り当て |
| 4.4 | v1.4.1 12.2 | 処理の単位（--limit） |
| 4.4 | v1.4.1 12.3 | 多重起動対策（select_for_update + skip_locked） |
| 4.5 | v1.4.1 12.6 | エラーハンドリング |
| 4.6 | v1.4.1 12.5 | stuck 検出（重複チェックでは不要） |
| 4.7 | v1.4.1 12.11 | 将来の非同期化への配慮 |
| 5.2 | v1.4.1 8.9 | different_person 判定の永続性（get_persons_confirmed_as_different の根拠） |
| 5.3.1 | v1.4.1 8.6 | グループ化（group_id） |
| 7.7 | v1.4.1 12.7 | Contact 編集時の処理 |
| 7.7 | v1.4.1 12.9 | recheck_duplicates |

## **B.2 v1.4.2 改訂差分 rev5 への参照**

| 本仕様書の章 | 参照先 | 内容 |
|---|---|---|
| 0.5 | rev5 第7章 | recover 一本化 |
| 0.5 | rev5 第10章 | Django モデルメソッド化の体系 |
| 0.5 | rev5 第9章 | ActionLog の位置づけ |
| 0.5 | rev5 第8章 | 効率化アルゴリズム |
| 0.5 | rev5 4.7.4 / 8.3 | N+1 対策 |
| 0.5 | rev5 第3章 | 関数命名規則 |
| 1.3 | rev5 12.6 | エラーハンドリング |
| 2.5 | rev5 4.7.4 / 8.3 | prefetch_related 必須化 |
| 2.6 | rev5 4.5 | determine_score_and_rank の確定 |
| 2.6 | rev5 4.7 | _calculate_score の準関数化 |
| 4.6 | rev5 12.10 | 重複チェックの実行ログ（ActionLog 記録項目） |
| 4.6 | rev5 9.2.2 | ActionLog 書き込み方式 |
| 4.6 | rev5 9.4 | ActionLog に記録するアクション |
| 4.8.1 | rev5 13.2.2 | 命名カテゴリ Run_* |
| 4.8.2 | rev5 13.2.6 | 変数・引数の命名方針 |
| 4.8.4 | rev5 9.2.1 | ActionLog の同トランザクションルール（本関数の集約実行記録は別扱い） |
| 5.3.1 | rev5 7.2 | GID と連続レビュー UX の全体フロー |
| 5.3.1 | rev5 7.3 | recover_duplicate_candidates の処理フロー |
| 5.3.1 | rev5 10.5.1 | DuplicateCandidate.create_recovered_from |
| 7.3 | rev5 9.2.4 | DB 障害時のフォールバック |
| 7.7 | rev5 4.6 | invalidate_pending_candidates |

## **B.3 将来の v1.4.2 統合版仕様書への組み込み対応**

| 本仕様書の章 | 統合版の組み込み先（想定） |
|---|---|
| 第2章 find_duplicate_contacts（excluded_persons オプション引数を含む） | 11.6.2 サービス層節（rev5 S-2 方針に従い、各関数につき1節を新設） |
| 第3章 generate_duplicate_candidates_for_contact | 11.6.2 タスク層下位節 |
| 第4章 Run_Generate_Duplicate_Candidates | 11.6.2 タスク層上位節 |
| 第5章 5.2 get_persons_confirmed_as_different | 11.6.2 サービス層節（新関数として独立節を新設） |
| 第5章 5.3 group_id 発行の最小ルール | 8.6 への追記（既存「グループ化（group_id）」節に詳細を追加） |
| 第5章 5.4 履歴参照判断の設計趣旨 | 8.6 / 11.6.2 の補足として配置 |
| 0.7.3 v1.4.2 統合版波及作業 | 4.7（DuplicateCandidate モデル定義表）から match_reason / matched_fields フィールドを削除、14.1 から DuplicateCandidate.MatchReason を削除 |

---

# **改訂履歴**

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v0.1 | 2026/05/04 | 初版作成。`Run_Generate_Duplicate_Candidates` / `generate_duplicate_candidates_for_contact` / `find_duplicate_contacts` の処理フロー詳細、group_id 発行の最小ルール、関数間の責務分担とトランザクション境界を確定。ActionLog 記録項目詳細・失敗系・DB 障害時フォールバック・冪等性詳細・本格テスト観点・N=5000 クエリ実行計画は v0.2 に送る。 | たんたん |
| v0.1.1 | 2026/05/05 | レビュー君指摘 CR-1 / CR-2 / S-1 / S-2 / M-1 / M-2 / M-3 を反映。<br>**CR-1**：上位関数のトランザクション境界を「ID リスト取得 → ループ内で 1 件ずつ atomic を切ってロック取得」方式に確定（4.4 全面書き換え）。<br>**CR-2**：different_person 判定済みペアの除外ロジックを v0.1 で実装することに確定。実装方法はたんたん提案により、新関数 `get_persons_confirmed_as_different(person)` を追加し、その戻り値を `find_duplicate_contacts` に `excluded_persons` オプション引数として渡す方式とする。<br>**S-1**：DuplicateCandidate モデルから match_reason / matched_fields フィールドを削除（ランクと意味が重複するため）。v1.4.2 統合版作成時に v1.4.1 4.7 / 14.1 から該当フィールドを削除する作業を 0.7.3 に明記。<br>**S-2**：「全 3 フィールド空 Contact」の防御コード記述を削除（OCR の has_minimum_info とランク判定で実質的に除外されるため過剰防御）。<br>**M-1**：CR-1 / S-1 の対応により 7.7 / 7.9 が消滅し自動解消。<br>**M-2**：4.8.4 に rev5 9.2.1 との関係を明示する追記を追加。<br>**M-3**：6.2 確認観点 #5 に DB クエリログ観察の追記を追加。加えて、確認観点 #7 として different_person 除外の動作確認を追加。<br>第5章を「DuplicateCandidate 生成側で行う履歴参照判断」として再構成（5.2 get_persons_confirmed_as_different 詳細仕様、5.3 group_id 発行ルール、5.4 設計趣旨を統合）。 | たんたん |
| v0.1.2 | 2026/05/05 | レビュー君（v0.1.1 のレビュー結果）指摘 CR-1 / CR-2 / S-1 / S-2 / S-3 / M-1 / M-2 / M-3 / M-4 を反映。<br>**CR-1（v0.1.1 の指摘）**：4.4.2 のロック取得後に 3 段の再チェック（duplicate_checked_at / Contact.status / Person.status）を追加。4.4.3 を「v0.1.2 で確定」に書き換え。<br>**CR-2（v0.1.1 の指摘）**：3.6.1 で IntegrityError 発生時の挙動を明示確定（`ignore_conflicts=True` は付けない、例外は上位伝播、duplicate_checked_at が NULL のまま次回 cron で再試行）。<br>**S-1**：2.3 の SQL 擬似コード説明部分に excluded_persons の None / 空リスト判定（`if excluded_persons:`）と Django ORM 経由実装の方針を明記。<br>**S-2**：3.4 / 3.4.1 / 4.4.2 で assigned_to の決定ロジックを明示。`select_related('business_card__original_image__user', 'created_by', 'person')` をロック取得時に追加して追加クエリゼロを実現。assigned_to はループ前に 1 度だけ計算して全 DuplicateCandidate に同じ値を設定する流れを 3.3 処理フローに明記。<br>**S-3**：5.2.1 / 5.2.2 / 5.2.3 / 5.2.4 で `get_persons_confirmed_as_different` の戻り値を「現在 active な Person のみ」に確定（通常運用に最適化、シンプル設計、YAGNI 原則）。<br>**M-1**：0.6 のタイトルと本文を「rev6 先取り」→「rev6 確定名の採用」に修正。<br>**M-2**：4.6 の ActionLog 書き込み内容表に user フィールド（NULL、cron 実行＝システム実行のためユーザーなし）を追加。<br>**M-3**：2.4 のインデックス定義を Django Meta.indexes として実装可能な形に書き直し（Contact 側 3 つ + Person 側 1 つ）。注釈で「テーブルをまたぐ複合インデックスは実装できない」と明示。<br>**M-4**：4.4.1 のクエリに `order_by('created_at', 'id')` を追加（FIFO 処理、デバッグ容易性確保）。 | たんたん |
| v0.1.3 | 2026/05/05 | レビュー君（v0.1.2 のレビュー結果）指摘 S-1 / S-2 / S-3 / S-4 / M-1 / M-2 / M-3 / M-4 を反映（クリティカル指摘なし、すべて補足記述）。<br>**S-1**：4.4.1 末尾にステップ A の取得件数とスキップ件数の関係に関する注釈を追加（運用後に評価する旨を明記）。<br>**S-2**：5.3.2 末尾に「バッチ内キャッシュ最適化を 運用後に再評価する妥当性根拠」を追記（rev5 8.5 の候補数見積もりに基づく）。<br>**S-3**：3.5 末尾に二重ネスト `transaction.atomic()` の Django 挙動（外側はトランザクション、内側は savepoint）を明示。<br>**S-4**：3.4.1 末尾に assigned_to が NULL になるケース（Contact.created_by の SET_NULL、OCR 由来でも同様）の扱いを明記。v0.1 では NULL を許容、レビュー画面表示や KPI 集計の挙動は 運用後に詳細化。<br>**M-1**：1.2 の俯瞰の手順（5）に「assigned_to の値を 1 度だけ計算」を明示追加（3.3 との整合）。<br>**M-2**：2.4 のインデックス表に `Contact: (created_at,)` の単独インデックスを追加（4.4.1 ORDER BY 用、運用後に必要性検証）。<br>**M-3**：5.2.2 の SQL 擬似コードに DISTINCT の冗長性に関する注釈を追加（Django ORM 経由では `.distinct()` 不要）。<br>**M-4**：5.3.5 の括弧書きを整理し、「過去判定済みのレコードは group_id 発行ロジックに影響しない」とシンプルに記述。 | たんたん |
| v0.1.4 | 2026/05/06 | コード君（Claude Code）への引き渡し前最終版。(1) 関数名を v1.4.2 確定版（`Run_Generate_Duplicate_Candidates` 短縮版）に置換。(2) v0.2 送り事項を「v1.4.2 では本書の方針のまま実装、運用後に実測・チューニング」方針に変更（v0.2 は作成しない）。(3) 冒頭に v1.4.2 統合最終版との位置づけセクションを追加。本書は v1.4.2 統合最終版の補助文書として、Run_Generate_Duplicate_Candidates の処理詳細のみを定義する。 | たんたん |
| v0.1.5 | 2026/05/06 | コード君（Claude Code）への引き渡し前最終版（v0.1.4 のレビュー反映）。(1) 関数配置先を v1.4.2 統合最終版（11.2 / 13.4.1 / 21章）と整合する `duplicates/tasks/duplicate_check_runner.py` に統一。v0.1.4 までの `duplicate_candidate_generator.py` は誤記（統合最終版との不整合）。(2) 冒頭「本書を読む前提」項目 5 を新設し、2 段階ロック方式（ステップ A / ステップ B）と 3 段再チェック（duplicate_checked_at / Contact.status / Person.status）の実装が必須であることを強調。統合最終版 12.4 だけでは実装が `TransactionManagementError` で動かないため。 | たんたん |
| v0.1.6 | 2026/05/13 | X-3 ランナバグ修正（v1.4.2 仕様書改訂ストック #19）を反映。(1) §3.3 に手順 (2.5) **事前フィルタ** を新規挿入：`contact.person` と既に pending DuplicateCandidate として組まれている Person ID 集合を `person_a` / `person_b` 別々の 2 クエリで取得し、その集合に含まれる候補を bulk_create 前にスキップする。partial unique constraint 違反を **事前に回避** する設計に変更。(2) §3.6 全面書き換え：v1.4.2 改訂前の「実運用では発生しない想定」記述を「事前フィルタで衝突を未然に回避」方針に置換。4.4.3 三段再チェックとの責務分離を明示。(3) §3.6.1 改訂：IntegrityError 発生時は事前フィルタの実装漏れまたは race condition の証拠と解釈を整理、`ignore_conflicts=True` 不採用方針は維持。(4) §5.3.2 補足：事前フィルタ除外候補は group_id 発行対象外を明記。(5) §5.4.1 拡張：履歴参照判断の責務に「既存 pending DC の存在チェック」を追記、v1.4.2 拡張で generate 側責務に加わった旨を明示。(6) §6.2 確認観点 #8 を追加：同一 cron バッチ内で同 Person ペアの両側が処理対象になっても IntegrityError なしで完走することを確認する観点。 | 仕様書改訂担当オーパス君（Claude Code、5/13 引き継ぎ後）|
