# 名刺画像取り込みOCR・人物統合機能 仕様書

**バージョン v1.4.2**

**FreeGroup2 名刺管理機能**

**2026年5月作成 / コーディング着手前最終確定版**

---

# v1.4.2 改訂サマリー

本書は v1.4.1 統合最終版に対して、コーディング着手前の最終確定として行った改訂を統合した v1.4.2 完結版である。設計思想・責務分離・命名規則・効率化アルゴリズム・運用基盤を整理し、コード君（Claude Code）が独自判断で実装する余地を最小化する。

コードの正解は実装ファイル（GitHub）を Single Source of Truth とし、仕様書は「何を」「なぜ」を記述する役割に専念する。

## v1.4.2 主要改訂内容

| # | 改訂内容 |
|---|---|
| 1 | **マージ後処理の recover 一本化**：値修正の有無を問わず `recover_duplicate_candidates` を呼ぶ統一設計に変更。連続レビュー UX を維持しつつ、補助レコードの整合性は次回 cron で収束させる |
| 2 | **サービス分割**：v1.4.1 の単一 `execute_merge` を 4 つの公開サービス（`Mark_as_Different_Person` / `Execute_Merge_Only` / `Execute_Merge_with_Updates` / `Execute_Merge_Undo`）に分割し、責務を明確化 |
| 3 | **Django モデルメソッド化の体系化**：`merge_helpers.py` を全削除し、Person / Contact / ContactFieldConfidence / DuplicateCandidate / PersonMergeLog / ActionLog の各モデルメソッドに分散配置。判断基準を仕様書として明文化 |
| 4 | **Form クラス活用方針の確定**：抽象基底クラス `ContactBaseForm` を導入。Form は DB に触らず `get_update_contact()` で新規 Contact インスタンスを返すまでに留める設計を確立 |
| 5 | **重複検出の効率化アルゴリズム**：N×(N-1)/2 の素朴比較を、フルネーム/メール/携帯一致の OR 絞り込みで現実的な時間に短縮 |
| 6 | **ActionLog の新規追加**：業務イベントの履歴を保持する汎用ログ DB を新設。PersonMergeLog（状態管理）とは別物として共存させる |
| 7 | **画面・URL の整理**：別肩書追加画面（9番）、active 副コンタクト修正画面（13番）、レビュー結果表示画面（16番）を新規追加。レビュー画面（17番）の URL を `/review` に変更し、結果画面（旧18番）を廃止 |
| 8 | **Form クラス継承構造の確立**：5 つの Form（`ContactUpdateForm` / `ContactUpdateActiveForm` / `ContactAddAdditionalRoleForm` / `ContactCreateForm` / `MergeForm`）が `ContactBaseForm` を継承する構造に統一 |
| 9 | **マージ前後のステータス遷移を別添 PDF として確定**：11 列横長表は別添 PDF（`/docs/spec/マージ前後のコンタクトのステータス等まとめ.pdf`）を正本とし、本文には設計趣旨と切り分け基準のみ記載 |
| 10 | **UI カスタムタグ・共通モーダル部品の整備**：画像表示・JSON ツリー・信頼度マークの共通化を 5 種類のカスタムタグで実現 |
| 11 | **OCR / OpenCV パイプラインの分離**：v1.2.0 で採用した「API 1 回呼び出しで全名刺一括取得（パターン A）」を**公式に撤回**。BC 1 枚 = Claude API 1〜2 リクエストに変更し、`process_opencv` / `process_ocr` の 2 本 cron に分離。条件付き 2 回 OCR（orientation 補正後の再 OCR）導入、`Run_Crop_Cards_From_OriginalImage` / `Run_Process_CardImages_With_OCR` 等の新関数群で実装（旧 `Extract_Cards_via_OCR` / `process_pending` / `PipelineCoordinator` は完全削除）|

## v1.4.0 から継承する主要機能

| # | 機能 |
|---|---|
| 1 | DuplicateCandidate モデル（重複候補の DB 管理） |
| 2 | PersonMergeLog モデル（マージ履歴・復元用） |
| 3 | Contact のステータス管理（primary / active / inactive） |
| 4 | Person のマージ機能（surviving / merged の階層管理） |
| 5 | 重複スコアおよびランク判定ロジック |
| 6 | バックグラウンド重複チェック処理（cron 起動） |
| 7 | 1 ペアごとのレビュー画面（PRG パターン） |
| 8 | マージ復元機能（1 段階前まで） |
| 9 | Contact のフィールド正規化（OCR 由来・手動入力で結果一致） |
| 10 | Contact 手動作成機能（重複警告付き） |
| 11 | アプリケーション分離（persons / contacts / duplicates の追加） |
| 12 | 設計案 A 採用：マージ画面が「データ品質確認の作業画面」も兼ねる設計 |

---

# 第1章 はじめに

## 1.1 目的

本仕様書は、FreeGroup2 における名刺画像取り込み・OCR・データ管理機能、および重複検出・人物統合機能の設計を定義することを目的とする。本機能は、ユーザーがアップロードした名刺画像から名刺情報を自動抽出してデータベースに保存・管理し、複数の名刺データの中から同一人物を検出して統合する仕組みを提供する。

## 1.2 適用範囲

本仕様書は FreeGroup2 の v1.4.2 における名刺管理機能を対象とし、以下の機能を含む。

- 画像アップロード機能
- 名刺領域の自動検出機能（OpenCV による事前検出）
- 名刺画像の正規化機能（透視変換・縦書き対応）
- OCR 実行機能（Claude Sonnet 4.6 による）
- OCR 結果の JSON 保存機能
- Person / Contact データの管理機能
- Contact の手動作成機能
- Contact フィールドの正規化機能
- 重複検出機能(DuplicateCandidate の生成)
- 人物統合機能（マージ・復元）
- レビュー画面（1ペアごとの判定）
- 信頼度メタデータの管理機能
- OCR パイプラインの堅牢性管理機能（多重起動対策・stuck 検出・整合性検査）
- ActionLog による業務履歴管理機能

## 1.3 用語定義

| 用語 | 定義 |
|---|---|
| 元画像 | ユーザーがアップロードした写真。複数枚の名刺を含む可能性がある |
| 名刺画像 | 元画像から個別の名刺領域を切り抜いて正規化した画像 |
| bbox | Bounding Box。画像内の物体の位置を表す矩形 |
| raw_json | OCR バックエンドから返される生の JSON 結果。不変データとして保管する |
| Tool Use | Claude API の構造化出力機能。JSON Schema を指定して安定した形式で結果を取得する |
| Person | 人物の本体。複数の Contact が紐付く可能性がある |
| Contact | 名刺ごと、または手動入力ごとのスナップショット。状態遷移を含む |
| 主コンタクト | 1 人の Person の代表 Contact。Person.primary_contact が指す |
| 副コンタクト | Person に紐付く active 状態の Contact のうち、主コンタクト以外 |
| ContactFieldConfidence | Contact フィールドの信頼度メタデータ |
| DuplicateCandidate | 重複候補の DB レコード。2 つの Person の組み合わせを記録 |
| PersonMergeLog | マージ実行・復元の履歴ログ |
| ActionLog | 業務イベントを記録する汎用ログ。マージ実行・別人判定・cron 実行・OCR 処理結果等を記録 |
| surviving_person | マージで残る側の Person |
| merged_person | マージで統合される側の Person |
| ランク | 重複候補の確信度。exact_match / possible_high / possible_mid / possible_low |
| グループ（group_id） | 同一の Person・同一ランクの DuplicateCandidate のまとまり |
| レビュー | ユーザーが DuplicateCandidate を確認し、同一人物 / 別人を判定する操作 |
| 復元（undo） | マージ実行を取り消す操作。1 段階前まで可能 |
| worker | OCR 処理または重複チェック処理を実行する管理コマンドのプロセス |
| CAS | Compare-And-Swap。楽観ロック方式 |
| stuck sweeper | 一定時間以上 processing のままのレコードを救済する仕組み |
| 純関数 | DB を一切触らない、副作用なし、同じ入力で同じ出力の関数 |
| 準関数 | DB を読むが書かない、外部世界に副作用なしの関数 |
| 副作用あり関数 | DB 書き込み・例外送出・API 呼び出しを行う関数 |

---

# 第2章 システム概要

## 2.1 機能概要

本機能は、ユーザーが撮影した名刺画像を取り込み、AI（Claude API）を活用して名刺情報を自動抽出し、構造化されたデータとしてデータベースに保存する。1 枚の画像に複数の名刺が含まれていても、OpenCV による事前検出と Claude による個別 OCR を組み合わせて処理する。

保存された Contact および Person について、バックグラウンドで重複検出を実行し、同一人物候補を DuplicateCandidate として DB に記録する。ユーザーはレビュー画面で 1 ペアずつ判定し、人物統合（マージ）または別人判定を行う。マージは 1 段階前まで復元可能とする。

## 2.2 採用する処理方式

画像内の名刺検出を OpenCV で行い、各名刺の OCR を Claude API で行う方式を採用する。横長統一処理は v1.3.0 で削除され、縦書き名刺にも対応している。

OCR 結果から Contact を生成する際にフィールドの正規化を実施する。重複検出はバックグラウンド処理（cron 起動）で実行し、ユーザーの取り込み操作を待たせない。

## 2.3 処理フローの全体像

システム全体の処理フローは以下のとおり。

1. ユーザーが画像をアップロード（同期処理） → OriginalImage 作成（status=pending）
2. cron による process_pending 起動 → OCR 実行 → 名刺画像切り抜き → BusinessCard / Contact / Person 作成
3. cron による check_duplicates 起動 → 主コンタクト同士で重複検出 → DuplicateCandidate 作成
4. ユーザーがレビュー画面を開き、1 ペアごとに判定（マージ / 別人 / 次の候補）
5. マージ実行 → PersonMergeLog 作成 → Contact 付け替え → Person.status='merged' に変更 → recover_duplicate_candidates 実行
6. 必要に応じて復元実行 → Contact を previous_person に戻す → ログを undone に

View 層の責務は元画像の保存（OriginalImage 作成、status=pending）までに限定される。OCR 処理、重複チェック処理、マージ処理は別プロセスまたは別 View で実行される。

---

# 第3章 機能要件

## 3.1 画像アップロード機能

### 3.1.1 受け付ける画像形式

- JPEG（.jpg, .jpeg）
- PNG（.png）

HEIC は v1.4.x 時点では受付不可（v1.5.0 以降で対応検討）。

### 3.1.2 バリデーション仕様

- 最大ファイルサイズ：5MB
- 受付拡張子：jpg / jpeg / png のみ
- 最低画像サイズ：100 × 100px（OpenCV 処理上の最低基準）

## 3.2 OCR 処理の起動

OCR 処理を開始するユーザーアクションは「画像アップロード」のみとする。失敗した OriginalImage に対する retry 機能はユーザーに提供しない。

ユーザーが失敗を確認した場合、画像を撮り直して新しくアップロードする。不要な失敗 OriginalImage は管理画面から削除する。

retry_failed_ocr 管理コマンドは開発・運用ツールとして提供する。本番運用ではエンドユーザーに再アップロードを促す。

## 3.3 重複検出機能

Contact が新規作成または更新された場合、バックグラウンド処理で他 Contact との重複検出を実行する。検出は Person の主コンタクト同士で行い、確信度ランクで分類した上で DuplicateCandidate として DB に保存する。検出タイミングはユーザー操作と非同期で、5 分間隔程度の cron 起動を想定する。

## 3.4 人物統合機能

ユーザーはレビュー画面から DuplicateCandidate を 1 ペアごとに判定する。「同一人物」と判定した場合はマージを実行し、片方の Person を merged 状態にして Contact を統合する。「別人」と判定した場合は今後候補に上がらないよう記録する。マージ実行時には PersonMergeLog を作成し、1 段階前までの復元を可能とする。

## 3.5 Contact 手動作成機能

名刺画像がない状態で Contact を手動入力する機能を提供する。電話で異動を聞いた場合などに使用する。保存時には可能性の高い重複候補（possible_high 以上）を警告ダイアログで表示し、ユーザーが「キャンセル」または「強制作成」を選択する。

## 3.6 別肩書追加機能

既存の Person に対して、副業など別肩書のコンタクトを active として追加する機能を提供する。別肩書追加画面（URL 一覧の 9 番、PersonAddAdditionalRoleView）から実行する。

---

# 第4章 データベース設計

## 4.1 全体構造

データモデル全体構造を以下に示す。OriginalImage を起点として、BusinessCard・Contact・Person・ContactFieldConfidence が階層的に紐付き、DuplicateCandidate と PersonMergeLog が重複・統合管理を担う。ActionLog は業務イベントを記録する汎用ログとして全モデル横断で参照される（GenericForeignKey）。

## 4.2 OriginalImage（元画像DB）

OriginalImage は元画像 1 件に対応するレコード。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー（uuid.uuid4） |
| user | FK(User, CASCADE) | アップロードしたユーザー |
| image_file | ImageField | 元画像ファイル |
| status | CharField(20) | 処理状態（7 値、4.2.1 参照） |
| claimed_at | DateTimeField (null) | CAS で processing / opencv_processing 遷移時刻を記録 |
| raw_json | JSONField (null) | OCR 結果 JSON（**v1.4.2 で deprecated**：読み出しは BC.raw_json_1 / raw_json_2 経由に統一、フィールド自体は物理残置）|
| detected_count | IntegerField (default=0) | 検出された名刺数（OriginalImage に紐づく BusinessCard レコードの総数。ocr_result の値に関わらず DB に保存された BC 全件をカウントする） |
| error_message | TextField (default='') | 失敗理由・部分失敗ログ |
| created_at | DateTimeField | auto_now_add |
| updated_at | DateTimeField | auto_now |

### 4.2.1 OriginalImage.status の値

詳細は別表 C.1 参照。pending / processing / opencv_processing / cards_extracted / extracted / garbage / failed の 7 値。

旧 v1.4.2 改訂前の 1 本パイプライン用の `processing` は後方互換のため物理残置するが、v1.4.2 以降のパイプライン分離（第 8 章参照）では `opencv_processing` / `cards_extracted` に置き換わる。

### 4.2.2 OriginalImage のモデルメソッド

メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| `OriginalImage.get_pending(limit)` | クラスメソッド | pending な OriginalImage を limit 件取得（cron 用） |
| `OriginalImage.release_stuck_locks(threshold_minutes)` | クラスメソッド | stuck な processing レコードを pending に戻す |
| `original_image.get_image_url()` | インスタンスメソッド | サムネイル用 URL を返す |
| `original_image.get_image_url_full()` | インスタンスメソッド | フルサイズ用 URL を返す |

## 4.3 BusinessCard（名刺DB）

BusinessCard は切り抜き済み名刺画像に対応するレコード。v1.4.2 のパイプライン分離（第 8 章参照）に伴い、BC 単位で OCR 処理状態・OCR 生 JSON・エラーメッセージを保持する。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| original_image | FK(OriginalImage, CASCADE) | 元画像への外部キー |
| card_image | ImageField (null) | 正規化後の名刺画像（補正回転後の画像で上書きされる場合あり、第 15 章参照） |
| card_index | IntegerField | OpenCV 検出結果の配列内インデックス |
| orientation | CharField(20, choices) | 検出時の元の orientation（5 値、別表 C.2 参照、補正ログ） |
| raw_json_1 | JSONField (null=True) | 1 回目 OCR の生 JSON 全体（OcrService.run_ocr の戻り値まるごと） |
| raw_json_2 | JSONField (null=True) | 2 回目 OCR の生 JSON 全体（orientation=normal なら null、補正再 OCR 時のみ格納） |
| ocr_status | CharField(20, choices) | OCR 処理状態（4 値、別表 C.13 参照、default=pending） |
| ocr_result | CharField(20, choices, null=True) | OCR 処理結果の分類（5 値、別表 C.14 参照、default=None。OpenCV cron で BC 作成直後は null、OCR 完了時に確定） |
| claimed_at | DateTimeField (null=True) | OCR cron の CAS 時刻 |
| error_message | TextField (blank=True, default='') | OCR 失敗時のエラー集約 |
| created_at | DateTimeField | auto_now_add |
| updated_at | DateTimeField | auto_now |

制約：UniqueConstraint(original_image, card_index)。

### 4.3.1 BusinessCard のモデルメソッド

| メソッド | 種別 | 責務 |
|---|---|---|
| `business_card.get_card_image_url()` | インスタンスメソッド | サムネイル用 URL を返す |
| `business_card.get_card_image_url_full()` | インスタンスメソッド | フルサイズ用 URL を返す |

### 4.3.2 BusinessCard と Contact の関係

v1.4.2 で has_minimum_info NG ケース等でも BC を残置する仕様（第 15 章参照）を採用したため、BusinessCard と Contact の関係は v1.4.2 改訂前の「常に 1:1」から「条件付きの 1:0..1」に変わる。

| BC.ocr_result | Contact の有無 |
|---|---|
| `business_card` | Contact を必ず持つ（OneToOne、§4.4 Contact 参照） |
| `not_business_card` / `insufficient_info` / `ocr_failed` / `others` | Contact を持たない |
| null（OpenCV cron 完了直後、OCR 未実行） | Contact を持たない |

【削除カスケード】 BC を削除すると、以下の順で連鎖削除される：

1. BC レコード削除（`bc.delete()`）
2. Contact（OneToOneField、CASCADE）が連鎖削除
3. ContactFieldConfidence（Contact への ForeignKey、CASCADE）が連鎖削除
4. `card_image` の FS 実体が `post_delete` シグナルで自動削除

`OriginalImage.raw_json` には削除した BC に対応する cards 配列要素が温存される（§5.4 不変ルール v1.2.1）。CardDeleteView 経由のハード削除でも本カスケードルールに従う（第 11 章参照）。

## 4.4 Contact（コンタクトDB）

### 4.4.0 設計趣旨：Contact はなぜ「スナップショット」か

Contact は「ある時点での名刺情報のスナップショット」として設計している。実世界では、人物の所属・肩書・連絡先は時間とともに変化し、変化のたびに新しい名刺が発行される。本仕様はこの事実をそのままモデル化し、転職や異動のたびに新しい Contact を作成して旧 Contact を inactive 化する運用とする（第11章参照）。

データモデルの正規化原則からは「Person を頂点に Contact がぶら下がる」構造に違和感があるかもしれないが、本仕様は実世界の事象との対応関係を優先する。1 人の人物（Person）に対して時系列の名刺履歴（Contact 群）が紐付くという構造は、名刺管理という業務の本質に素直である。

なお fix（誤字訂正）の場合のみ既存 Contact を更新するが、これは「同じ名刺の入力をやり直す」操作であり、新しい時点の情報ではないため例外的に上書きを許容している。

BusinessCard と Contact の関係は条件付きの 1:0..1（OCR 成功 BC は Contact を持つ、それ以外の BC は Contact を持たない）。詳細は §4.3.2 参照。

### 4.4.1 Contact のフィールド定義

名刺ごとまたは手動入力ごとのスナップショット。BusinessCard と OneToOne 関係（手動入力時は null 許容、また BC 側の ocr_result が `business_card` 以外のときも Contact は存在しない、§4.3.2 参照）。Person への FK は NOT NULL。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| business_card | OneToOne(BusinessCard, CASCADE, null=True) | null 許容 |
| person | FK(Person, CASCADE) | 人物本体への参照（NOT NULL） |
| status | CharField(TextChoices) | primary / active / inactive |
| previous_person | FK(Person, SET_NULL, null=True) | マージ復元用、1 段階前を保持 |
| previous_status | CharField(null=True) | マージ前の status を保持 |
| duplicate_checked_at | DateTimeField (null=True) | 重複チェック実行日時 |
| created_by | FK(User, SET_NULL, null=True) | Contact 作成者 |
| updated_by | FK(User, SET_NULL, null=True) | 直近の更新者 |
| lang | CharField (default='ja', blank=True) | 言語コード（ISO 639-1） |
| postal_code | CharField | 郵便番号（数字のみ正規化済み） |
| full_name | CharField(255, blank=False) | 氏名（正規化済み、必須） |
| last_name / first_name | CharField(255) | 姓 / 名（オプション） |
| salutation_name | CharField(255) | 敬称表記 |
| company | CharField(255) | 会社名（正規化済み） |
| department | CharField(255) | 部署 |
| title | CharField(255) | 役職 |
| branch | CharField(255) | 支店・営業所・店舗 |
| address | CharField(500) | 住所（郵便番号なし、建物名込み） |
| email | CharField(255) | メール（小文字化済み） |
| phone / mobile / fax | CharField(50) | 電話番号（数字のみ正規化済み） |
| website | CharField(500) | ウェブサイト URL |
| qualification / catchphrase | CharField(500) | 資格 / キャッチフレーズ |
| twitter / instagram / github | CharField(255) | SNS（短文系） |
| linkedin / facebook | CharField(500) | SNS（URL 系） |
| notes | TextField | 自由記述メモ（正規化対象外） |
| created_at / updated_at | DateTimeField | 自動付与 |

`full_name` は必須フィールド。OCR 由来・手動入力・マージ画面・AJAX 更新を含むすべての経路で空文字を弾く（DB 制約 + Form clean + AJAX View ガード）。詳細は §15.5.3 の正規化ルールを参照。

### 4.4.2 Contact.status の値

| 値 | 意味 |
|---|---|
| primary | 主コンタクト。1 人の Person につき 1 つだけ存在 |
| active | 副コンタクト。別肩書など、現役で有効な情報 |
| inactive | 非アクティブ。転職前など、過去の情報 |

制約：partial unique constraint により、1 人の Person につき status='primary' の Contact は 1 つだけ。

### 4.4.3 Contact のモデルメソッド

メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| `contact.fix(form, user)` | インスタンスメソッド | フォーム値で自身のフィールドを上書きし、全 ContactFieldConfidence を confirmed 化する。form 引数は `ContactUpdateForm` に限定 |
| `contact.get_field_confidences()` | インスタンスメソッド | 全フィールドの ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） |
| `contact.get_high_fields()` | インスタンスメソッド | 実質 high なフィールド集合を返す |
| `contact.is_all_field_confidence_high(fields=None)` | インスタンスメソッド | 全 high 判定（引数省略時は全フィールド、指定時は範囲限定） |

## 4.5 Person（人物DB）

人物の本体。複数の Contact が紐付く（同じ人の複数の名刺）。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| primary_contact | FK(Contact, SET_NULL, null=True) | 主コンタクトへの参照 |
| status | CharField(TextChoices) | active / merged / archived |
| merged_into | FK(self, SET_NULL, null=True) | merged 状態時の統合先 Person |
| created_at / updated_at | DateTimeField | 自動付与 |

### 4.5.1 Person.status の値

| 値 | 意味 |
|---|---|
| active | 通常状態。検索・マージ対象 |
| merged | 他 Person に統合済み。編集禁止、マージ対象外 |
| archived | アーカイブ。検索・マージ対象外。UI は v1.5.0 以降で実装 |

### 4.5.2 Person.primary_contact と Contact.status='primary' の二重管理に関する設計趣旨

本仕様では、Person の代表コンタクトを Person.primary_contact（FK）と Contact.status='primary' の 2 箇所で保持する。形式上は二重管理にあたるが、意図的にこの設計を採用しているため、設計趣旨を以下に記録する。

**【採用の経緯】** 初期案は Person.primary_contact を持たず、Contact.status='primary' のみで代表を表現する方針であった。しかし実装検討にあたり、Person 起点で代表コンタクトを参照する処理が頻出することが判明し、Person.primary_contact を持たせた方が実装上有利な場面が多いと判断した。全クロード（複数の Claude インスタンス）との議論を経て、二重管理を許容する設計に切り替えた。

**【Contact.status='primary' を保持する理由】** ユーザー視点では、Contact 一覧を見たときに Contact 自身の属性として「代表である」ことが分かる必要がある。また将来、別肩書対応や多言語対応で、1 人の Person に対し日本語コンタクト・英語コンタクトなど複数の active な Contact を並列保持する可能性があり、その場合に代表を識別する手段として status='primary' が必要となる。一見冗長に見えるが、将来に備えた設計である。

**【正本と同期方針】** Person.primary_contact を正本とし、Contact.status='primary' はその派生情報として同期する。同期処理は `Person.set_primary_contact()` インスタンスメソッドに集約し、View 層・Model.save() からは直接変更しない。

**【過去のレビュー指摘について】** 「データの二重管理ではないか」という指摘は過去のレビューで複数回あり、本節はその指摘に対する設計判断の根拠を仕様書として明示するために記載する。今後同じ指摘を受けた場合は、本節を参照することで設計意図を伝達する。

### 4.5.3 Person のモデルメソッド

メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| `person.mark_as_merged(surviving_person)` | インスタンスメソッド | 自身の状態遷移（status='merged' / merged_into / primary_contact=NULL） |
| `person.transfer_contacts_to(surviving_person, merge_reason)` | インスタンスメソッド | 自身のコンタクト群を surviving に引き渡す（全 Contact 対象） |
| `person.set_primary_contact(new_contact, old_primary_new_status='active')` | インスタンスメソッド | primary_contact 切り替え。`old_primary_new_status` で旧 primary の遷移先を指定（'active' / 'inactive'） |
| `person.get_active_contacts()` | インスタンスメソッド | status='active' の Contact 一覧を返す |
| `person.get_inactive_contacts()` | インスタンスメソッド | status='inactive' の Contact 一覧を返す |
| `Person.get_active()` | クラスメソッド | status='active' の Person 一覧を返す |
| `Person.get_archived()` | クラスメソッド | status='archived' の Person 一覧を返す |

## 4.6 ContactFieldConfidence（信頼度メタDB）

Contact フィールドごとの信頼度を別テーブルで管理する。human-in-the-loop による確認履歴も保持する。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| contact | FK(Contact, CASCADE) | related_name='confidences' |
| field_name | CharField(50) | Contact 側のフィールド名 |
| confidence | CharField(TextChoices) | low / medium のみ（high は記録対象外） |
| confirmed_at | DateTimeField (null=True) | ユーザー確認日時 |
| confirmed_by | FK(User, SET_NULL) | 確認したユーザー |
| created_at / updated_at | DateTimeField | 自動付与 |

方針：high の値はレコード作成しない。medium / low のみ記録。UniqueConstraint(contact, field_name)。

### 4.6.1 high レコードの防御策

`confidence='high'` のレコードが誤って DB に保存されないよう、二重防御を実装する。

1. **CheckConstraint（DB 制約）**：DB レベルで `confidence='high'` の保存を物理的に禁止する
2. **save() オーバーライド（アプリケーション層）**：`confidence='high'` で `save()` が呼ばれた場合、明示的なエラーメッセージで誤用を検出する

これにより、`get_field_confidences()` が返す疑似インスタンス（confidence='high'）が誤って `save()` されても、DB に混入することを防ぐ。

### 4.6.2 ContactFieldConfidence のモデルメソッド

メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| `ContactFieldConfidence.get_for_contact(contact)` | クラスメソッド | 全フィールド分の ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） |
| `ContactFieldConfidence.create_for_contact(contact, confidence_map)` | クラスメソッド | OCR 結果の medium/low フィールドについて一括作成 |
| `ContactFieldConfidence.mark_fields_as_confirmed(contact, field_names, user)` | クラスメソッド | 指定フィールドを確認済み化 |

## 4.7 DuplicateCandidate（重複候補DB）

重複候補を表すレコード。2 つの Person の組み合わせで一意ではない（再マージのため UniqueConstraint なし）。person_a / person_b は ID 順で正規化される。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| group_id | UUIDField (null=True) | 同一 Person・同一ランクのグループ識別子 |
| person_a | FK(Person, CASCADE) | 候補の Person（順序ルールあり） |
| person_b | FK(Person, CASCADE) | 候補の Person（順序ルールあり） |
| score | IntegerField | 合計スコア（confidence=high のみ加算） |
| rank | CharField(TextChoices) | exact_match / possible_high / possible_mid / possible_low |
| review_status | CharField(TextChoices) | pending / merged / different_person / invalidated |
| review_result | JSONField | 判定理由の配列（複数選択可） |
| note | TextField (default='') | 任意メモ（other_* 選択時は必須） |
| assigned_to | FK(User, SET_NULL, null=True) | 担当者（自動割り当て） |
| reviewed_by | FK(User, SET_NULL, null=True) | 確認者 |
| reviewed_at | DateTimeField (null=True) | 判定日時 |
| created_at / updated_at | DateTimeField | 自動付与 |

### 4.7.1 review_result の値

merged 系（DuplicateMergeReason、7 値）と different_person 系（DifferentPersonReason、3 値）の混在禁止。複数選択可。詳細は別表 C.5 / C.8 / C.9 参照。

### 4.7.2 N+1 対策

cron 経由（`Run_Generate_Duplicate_Candidates`）で `_calculate_score` を多数回呼ぶ場合、内部で各 Contact の `get_field_confidences()` を呼ぶため N+1 問題が発生する。これを防ぐため、候補取得時に `prefetch_related('confidences')` を必須とする。

ContactCreateView からの呼び出しは 1 件ずつのため、prefetch_related は必須としない。

### 4.7.3 DuplicateCandidate のモデルメソッド

メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| `DuplicateCandidate.get_pending(contact)` | クラスメソッド | contact が紐づく Person の pending 候補を取得 |
| `DuplicateCandidate.get_merged(contact)` | クラスメソッド | contact が紐づく Person の merged 候補を取得 |
| `DuplicateCandidate.get_different_person(contact)` | クラスメソッド | contact が紐づく Person の different_person 候補を取得 |
| `DuplicateCandidate.get_invalidated(contact)` | クラスメソッド | contact が紐づく Person の invalidated 候補を取得 |
| `DuplicateCandidate.has_duplicates(contact, status)` | クラスメソッド | 指定 status の候補が存在するかどうかの判定 |
| `DuplicateCandidate.get_by_group(group_id)` | クラスメソッド | group_id 単位で取得 |
| `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` | クラスメソッド | old_candidate からスコア・ランク・group_id 等をコピーして新規作成 |
| `candidate.mark_as_merged(user, review_result, note)` | インスタンスメソッド | 自身の状態遷移（review_status='merged'） |
| `candidate.mark_as_different_person(user, review_result, note=None)` | インスタンスメソッド | 自身の状態遷移（review_status='different_person'） |
| `candidate.record_different_person_action(user)` | インスタンスメソッド | 自身の別人判定操作を ActionLog に記録 |

## 4.8 PersonMergeLog（マージ履歴DB）

マージ実行・復元の履歴ログ。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| surviving_person | FK(Person, PROTECT) | マージで残る側 |
| merged_person | FK(Person, PROTECT) | マージで統合される側 |
| duplicate_candidate | FK(DuplicateCandidate, PROTECT, null=True) | 起点となった候補 |
| status | CharField(TextChoices) | undoable / undone / locked |
| executed_by | FK(User, SET_NULL, null=True) | マージ実行者 |
| executed_at | DateTimeField | 実行日時 |
| undone_by | FK(User, SET_NULL, null=True) | 復元実行者 |
| undone_at | DateTimeField (null=True) | 復元日時 |
| note | TextField (default='') | 操作履歴 + ユーザー入力（文字列） |
| created_at / updated_at | DateTimeField | 自動付与 |

FK の強度：surviving_person / merged_person は PROTECT。マージログから過去の状態を確実に追跡できるよう、Person の物理削除をブロックする。

### 4.8.1 PersonMergeLog のモデルメソッド

メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| `PersonMergeLog.create(surviving_person, merged_person, user)` | クラスメソッド | マージ実行のためのログレコードを作成 |
| `PersonMergeLog.lock_past_logs(merged_person)` | クラスメソッド | 過去のログを locked 状態に変更 |
| `PersonMergeLog.get_for_person(person)` | クラスメソッド | Person 単位のログ一覧取得 |
| `PersonMergeLog.get_undoable(person)` | クラスメソッド | 復元可能なログ取得 |
| `merge_log.is_undoable()` | インスタンスメソッド | 復元可能かどうかの判定 |
| `merge_log.mark_as_undone(user)` | インスタンスメソッド | 自身の状態遷移（status='undone'） |
| `merge_log.record_merge_action(user)` | インスタンスメソッド | マージ実行を ActionLog に記録（action='merged'） |
| `merge_log.record_undo_action(user, note="")` | インスタンスメソッド | 復元実行を ActionLog に記録（action='undone'、data に {"note": str} を保存） |
| `merge_log.get_undo_preview()` | インスタンスメソッド | 復元後の予測状態を返す（確認画面用） |

## 4.9 status='merged' の Person の制約

status='merged' の Person は以下の操作ができない。

- フィールドの編集
- マージで surviving_person または merged_person として使用
- 重複検出の比較対象として使用

これらの制約により、マージログから過去の状態を確実に追跡できる。

## 4.10 ActionLog（アクションログDB）

業務イベントを記録する汎用ログ。マージ実行・別人判定・cron 実行・OCR 処理結果等を記録する。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| user | FK(User, SET_NULL, null=True) | 操作したユーザー（システム実行は NULL） |
| action | CharField | created / updated / deleted / merged / different_person / executed / undone 等 |
| content_type | FK(ContentType, null=True, blank=True) | 操作対象のモデル種別（システム実行は NULL） |
| object_id | CharField (null=True, blank=True) | 操作対象の PK（UUID 文字列、システム実行は NULL） |
| content_object | GenericForeignKey | 上 2 つを合わせた仮想 FK |
| object_repr | CharField | 操作時点の対象オブジェクトの文字列表現またはコマンド名 |
| data | JSONField (default=dict) | モデルごとの追加情報を自由に格納（変更前後の差分が必要な場合もこのフィールドに格納する） |
| note | TextField (default='') | 補足メモ |
| created_at | DateTimeField (auto_now_add) | 操作日時 |

### 4.10.1 ActionLog のモデルメソッド

| メソッド | 種別 | 責務 |
|---|---|---|
| `ActionLog.record(user, action, content_object=None, object_repr='', data=None, note='')` | クラスメソッド | 任意の業務イベントを直接記録（cron 実行ログなど、モデルインスタンスを持たない場面で使用） |

## 4.11 ActionLog と PersonMergeLog の関係

| | PersonMergeLog | ActionLog |
|---|---|---|
| 役割 | 復元処理用、状態管理 | 業務履歴管理、KPI 分析素材 |
| FK の強度 | 強い参照（PROTECT） | 弱い参照（GenericForeignKey） |
| 状態の更新 | あり（undoable→undone→locked） | なし（不変履歴） |
| 書き込みタイミング | マージと同トランザクション | マージと同トランザクション |
| 失敗時の挙動 | マージ全体ロールバック | マージ全体ロールバック |
| note | 操作履歴 + ユーザー入力（文字列） | data に構造化データで格納 |

両者は共存させる。マージ実行時は両方に書き込む。

### 4.11.1 ActionLog の位置づけ

ActionLog は syslog 的な「ログ収集機能」ではなく、FreeGroup の一機能としての「履歴管理アプリケーション」である。

各アプリ・各モデルが「何をしてきたか」を記録する業務機能であり、将来的な KPI・データ分析の素材として業務的に意味のあるデータを保持する。

業務機能であるため、業務処理（マージ実行・別人判定・OCR 処理等）と同じトランザクション内で書き込まれる。書き込み失敗 = 業務処理失敗として全体ロールバックする。

「マージは成功したのに ActionLog だけ書かれない」状態は業務データの欠損として扱い、許容しない。

### 4.11.2 ActionLog 書き込み方式

| 場面 | 書き込み方式 |
|---|---|
| モデルインスタンスがある場合（マージ実行・別人判定・復元など） | インスタンスメソッド経由（`merge_log.record_merge_action(user)` 等） |
| モデルインスタンスがない場合（cron 実行ログなど） | `ActionLog.record(...)` クラスメソッド直接呼び |

### 4.11.3 ActionLog に記録する対象

ActionLog に記録する対象は、すべて仕様書で明示的に定める。実装者の判断で記録対象を追加することは認めない。

【v1.4.2 で ActionLog に記録する対象】

- マージ実行（Execute_Merge_Only / Execute_Merge_with_Updates）→ `merge_log.record_merge_action(user)` 経由
- 別人判定（Mark_as_Different_Person）→ `candidate.record_different_person_action(user)` 経由
- マージ復元（Execute_Merge_Undo）→ `merge_log.record_undo_action(user, note)` 経由（note は MergeUndoForm の cleaned_data["note"]、空文字でも `{"note": ""}` 形式で data に保存して集計時のキーを揃える）
- cron 重複チェック実行（Run_Generate_Duplicate_Candidates）→ `ActionLog.record(...)` 直接呼び
- OCR 処理結果（使用トークン数、処理時間、読み取り名刺枚数等のレスポンスメタデータ）→ `ActionLog.record(...)` 直接呼び

【ActionLog 以外で記録すべきもの】

- コーディング・デバッグ中の中間ログ → 標準出力（icecream / Django logging）またはファイルログを使う
- 処理途中の試行錯誤情報 → 標準出力 / ファイルログ
- 高頻度な処理の細かいトレース → 標準出力 / ファイルログ

これらは ActionLog の対象としない。

### 4.11.4 DB 障害時のフォールバック

ActionLog は DB 上のモデルであるため、DB 自体が障害時には ActionLog への書き込みも不可能になる。「障害時にこそログが必要」という根本的要請に応えるため、ActionLog 書き込み失敗時のフォールバック機構を用意する。

【フォールバックの動作】

1. ActionLog のメソッドを呼ぶ
2. DB 例外（接続不能・トランザクションエラー等）が発生
3. ファイルログ（または標準出力ログ）に「ActionLog 書き込み失敗 + 対象 Person/Contact の ID + 試みた業務処理の種別」を**障害記録**として書き込む（業務データそのものではなく、障害発生情報を記録する）
4. 元の業務処理は通常通り例外として伝播（マージ等は失敗扱い、DB はロールバック）

手順3で記録するのは「ActionLog 書き込みに失敗した障害情報」であり、「マージ実行内容」ではない。マージは手順4でロールバックされて実際には起こらないため、ファイルログに「マージ内容」を記録すると「成功したマージ」と混同される恐れがある。あくまで障害発生の記録として位置づける。

【記録先】

- 本番環境：別途定めるログファイル（ローテーション設定済み）
- 開発環境：標準出力（icecream や Django logging）

これにより、DB 障害時でも最低限の障害情報が残り、原因調査が可能になる。

## 4.12 DebugMask（OpenCV デバッグ用マスク画像 DB）

DebugMask は、OpenCV 検出パイプラインで生成されるデバッグ用マスク画像（5 種）を DB 管理するための補助モデル。OriginalImage 1 件に対して最大 5 件（mask_type ごとに 1 件）の DebugMask が紐づく。BusinessCard と同じく cards アプリ配下に配置する。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| original_image | FK(OriginalImage, CASCADE, related_name='debug_masks') | 元画像への外部キー |
| mask_type | CharField(20, choices) | マスク種別（5 値、別表 C.15 参照） |
| mask_image | ImageField | マスク画像実体（保存先：`media/debug_masks/<original_id>/<mask_type>.png`） |
| metadata | JSONField (default=dict, null=False, blank=True) | マスク個別の属性（white_ratio 等） |
| created_at | DateTimeField | auto_now_add |

制約：UniqueConstraint(original_image, mask_type)。

### 4.12.1 設計趣旨：DB 1 次ソース、FS 実体は従属物

DebugMask は「DB レコードを 1 次ソースとし、FS 実体は DB レコードに紐付いた従属物」として扱う設計を採る。OriginalImage / BusinessCard / DebugMask それぞれに `post_delete` シグナルを定義し、レコード削除時に対応する FS 実体（image_file / card_image / mask_image）が自動削除される。

開発フェーズで OpenCV チューニングを繰り返す前提で、開発環境完全リセット手順「`db.sqlite3` 削除＋ migrate」だけで完結する状態を担保する。v1.4.2 改訂前は mask 画像が DB と独立して FS 上に存在し、`db.sqlite3` を削除しても media 配下の FS 実体が残る問題があったが、本モデル導入で解消する。

---

# 第5章 OCR 結果 JSON 仕様

## 5.1 標準 JSON 形式

OCR バックエンドからの結果 JSON の構造は schema_version / ocr_meta / api_response / cards の 4 階層で構成される。詳細は `docs/json_schema/v1.4.0/combined_response.json` を参照。

## 5.2 各 card のフィールド

各 card は名刺 1 枚分の情報を持つ。is_business_card / has_minimum_info / orientation / lang / postal_code / address ほかの主要フィールドを含む。confidence の値は high / medium / low の 3 値で、フィールドごとに付与される。

OCR バックエンドが GPT-4o / Vision / EasyOCR 等の場合は内部で「テキスト抽出 → LLM 構造化」の 2 段階処理を行い、本標準 JSON 形式に変換する。共通の解析ロジックを適用する。

## 5.3 v1.3.0 → v1.4.0 のスキーマ変更点

1. 各 card のフィールドに lang を追加。OCR が判定した名刺の主要言語（ISO 639-1 形式：'ja' / 'en' / 'zh' / 'ko' 等）
2. postal_code を address から独立した独立フィールドとして追加
3. address フィールドは郵便番号を含めない（建物名は含める）

## 5.4 raw_json は不変・card_index は不変

OriginalImage.raw_json は OCR 時点の証跡として保存し、その後変更してはならない。cards 配列の順序は raw_json 保存時点で固定される。BusinessCard.card_index は raw_json["cards"] 配列内の固定インデックスを参照する。

## 5.5 confidence 値の扱い

high / medium / low の 3 値のみ。orientation 別の自動調整あり（calc_orientation_adjusted_confidence_map 純関数）。

重複検出のスコア計算には confidence=high のフィールドのみ使用する（low / medium は加算対象外）。

---

# 第6章 画像処理仕様

## 6.1 名刺検出（OpenCV）

元画像から名刺領域を検出する処理は OpenCV で実装する。輝度差・Canny エッジ・HSV 彩度の 3 種マスクを OR 合成して候補を抽出し、minAreaRect で 4 隅座標を取得、透視変換で正立化した画像を返す（実装は opencv_detector.py を参照）。

v1.3.0 で横長統一処理を削除し、縦書き名刺対応とした。orientation 判定は Claude 側で行う。

## 6.2 切り抜き失敗時の扱い

切り抜き後の画像 width < 100 または height < 50 は失敗扱い（判定は opencv_detector._warp_card() 内で行う）。切り抜き失敗時も BusinessCard / Contact は作成可能（card_image=null）。失敗理由は OriginalImage.error_message に記録する。

---

# 第7章 OCR バックエンド仕様

## 7.1 サポートする OCR バックエンド

| バックエンド | v1.4.x 状況 | 備考 |
|---|---|---|
| Claude Sonnet 4.6 | 採用中 | Tool Use による構造化出力 |
| GPT-4o | 候補 | Structured Output 対応 |
| Google Cloud Vision | 候補 | テキスト抽出 + LLM 構造化の 2 段階処理 |
| EasyOCR | 候補 | ローカル動作。プライバシー要件対応 |

## 7.2 出力形式の統一方針

すべての OCR バックエンドは、第5章で定義する標準 JSON 形式（schema_version / ocr_meta / api_response / cards）で結果を返す。構造化出力非対応バックエンドは内部で「テキスト抽出 → LLM 構造化」の 2 段階処理を行う。

## 7.3 プロンプト仕様

Claude へのプロンプトは cards/prompts/extract_combined.txt に配置。以下の指示を含める。

1. 名刺の主要言語を判定し、ISO 639-1 形式で lang フィールドに出力する
2. 住所と郵便番号を分離して出力する。郵便番号は postal_code、住所本文は address に
3. 名刺に漢字主体でローマ字ヨミカナがある場合は 'ja' と判定する（補助的なローマ字は主要言語ではない）

## 7.4 JSON Schema ファイルの管理

docs/json_schema/v1.4.0/combined_response.json で Git 管理。Claude Tool Use の input_schema にそのまま指定する。

---

# 第8章 重複検出仕様

## 8.1 検出方針

Contact が新規作成または重複判定対象フィールドが更新された場合に、バックグラウンド処理で他 Contact との重複検出を実行する。検出はあくまで「候補としてリストアップする」までを担い、同一人物の最終判定はユーザーが行う（自動マージはしない）。

## 8.2 比較対象

重複検出の比較は、Person の主コンタクト（status='primary'）同士でのみ行う。副コンタクト（status='active'）と非アクティブコンタクト（status='inactive'）、archived な Person は比較対象外とする。

理由：シンプルさ優先。1 人の Person を 1 つの代表 Contact で表現することで、重複判定ロジックがシンプルになる。副コンタクトを比較対象に含めるのは v1.5.0 以降で検討する。

## 8.3 スコア表

各フィールドの完全一致に対して、点数を加算する。両 Contact の confidence=high（DB 上の low / medium レコードは加算対象外、ただし high はデフォルト値のため大半のフィールドが加算対象になりうる）かつ正規化後の値が完全一致した場合のみ加算する。

スコア表とランク閾値は `config/constants.py` の `DUPLICATE_FIELD_SCORES` / `DUPLICATE_SCORE_EMAIL_PERSONAL` / `DUPLICATE_SCORE_EMAIL_GENERIC` / `POSSIBLE_*_MIN_SCORE` で管理する。運用後にチューニング可能とするため、定数化された設計とする。初期値は以下のとおり。

| フィールド | スコア（high 一致時の加算点） |
|---|---|
| mobile | 80 |
| email（個人メール） | 80 |
| full_name | 40 |
| company | 10 |
| department | 10 |
| address | 10 |
| title | 5 |
| phone | 5 |
| email（代表メール） | 5 |
| branch | 0（配点なし。所属5フィールド判定にのみ参加、§8.4 参照） |

email は個人メール（`DUPLICATE_GENERIC_EMAIL_LOCALPARTS` に該当しないローカル部）と代表メール（該当するローカル部）で配点を分ける。判定は §8.7 のロジックでサービス層が行い、どちらの定数を使うかを決める。

合計スコアの 200 点到達例（参考）：

- `mobile` + `email`（個人）+ `full_name` = 80 + 80 + 40 = 200
- `mobile` + `email`（個人）+ `company` + `department` + `address` = 80 + 80 + 10 + 10 + 10 = 190（200 点に届かない）
- `mobile` + `email`（個人）+ `full_name` + `company` = 80 + 80 + 40 + 10 = 210

## 8.4 ランク判定

合計スコアと一致条件の組み合わせでランクを判定する。判定は以下のランクを **exact_match → possible_high → possible_mid → possible_low の順に上から評価し、最初に該当した条件のランクを採用する**。各ランクの「必須条件」内に列挙された条件はすべて **AND 関係**（すべて満たす必要がある）。

| ランク | 必須条件 |
|---|---|
| exact_match | 200点以上 AND 所属5フィールドが「両方一致」もしくは「両方空」 |
| possible_high | 200点以上（フルネーム不一致でも mobile + email + 所属系の加算で達成可能） |
| possible_mid | フルネーム一致 AND（email 一致 OR mobile 一致） |
| possible_low | 40〜119点 AND フルネーム一致 |
| none | 上記いずれにも該当しない |

ランク閾値の具体値（`config/constants.py`）：

| 定数 | 値 | 用途 |
|---|---|---|
| `POSSIBLE_LOW_MIN_SCORE` | 40 | possible_low の下限 |
| `POSSIBLE_MID_MIN_SCORE` | 120 | possible_mid のフォールバック上限（possible_low の上限 119 と接続） |
| `POSSIBLE_HIGH_MIN_SCORE` | 200 | possible_high / exact_match の下限 |

**所属5フィールド**：exact_match 判定の「両方一致 or 両方空」評価に用いる 5 項目。

| 所属5フィールド | コーディング名（`DUPLICATE_LOCATION_FIELDS`）|
|---|---|
| 会社名 | company |
| 部署 | department |
| 役職 | title |
| 支店 | branch |
| 住所 | address |

`DUPLICATE_CHECK_FIELDS`（9 フィールド）から個人系 4 項目（full_name / email / phone / mobile）を除いた残りが所属5フィールドに対応する。

### 8.4.1 ランク閾値の根拠

初期値は 40 点下限で同姓同名を含めて広く拾う設計とする。これにより、フルネーム一致のみのケースでも possible_low として候補化される。

理由：

1. 初期段階では「拾い漏れ」より「ノイズ」のほうが対応しやすい。同姓同名を多く拾ってしまっても、ユーザーは「別人」判定すればよく、different_person 判定後はシステムが再候補化しない（8.9）ため、一度判定すればノイズは消える。逆に拾い漏れがあると「同一人物の可能性に気付けない」という機会損失になり、これは検知できない
2. 運用データを見ないと最適な閾値は決まらない。机上で精緻に決めるより、運用後にチューニングする方が現実的
3. スコア表とランク閾値は `config/constants.py` で管理されている設計。`recheck_duplicates --all` コマンドで全件再判定もできる（12.9）。運用後の調整を前提に設計しているので、初期値で精緻に詰める必要はない

同姓同名のレビュー件数が運用上多すぎる場合は、`config/constants.py` のランク閾値（POSSIBLE_LOW_MIN_SCORE 等）を運用データに基づき調整する。閾値変更時は `recheck_duplicates --all` コマンドで全件再判定する。

## 8.5 マージ実行時の最終要件

マージ画面が「データ品質確認の作業画面」も兼ねる設計（設計案 A）に基づき、マージ実行時の最終要件として以下を設定する。

### 8.5.1 全 high 化の必須条件

surviving 側 Contact の DUPLICATE_CHECK_FIELDS（9 フィールド）が全 high であることを必須とする。additional_role の場合は merged 側 Contact も同条件を必須とする（9.4 参照）。

### 8.5.2 マージ画面に入る時点での扱い

8.5.1 の制約は、マージ画面に入る時点ではなく、マージ確定ボタン押下時に達成されていればよい。

### 8.5.3 マージ画面での修正・確認

マージ画面で以下のフィールドが確認・修正対象として表示される。

- DUPLICATE_CHECK_FIELDS のうち、confidence が mid または low のフィールド
- DUPLICATE_CHECK_FIELDS のうち、surviving と merged で値が異なるフィールド（両者 high でも確認対象）
- v1.4.2 で表示対象を拡大：上記以外の Contact フィールドも値違いまたは片方空のフィールドのみ表示・選択対象とする（last_name / first_name / salutation_name / fax / website / qualification / catchphrase / SNS各種 / notes / postal_code / lang）

値違いを確認対象とする理由は、OCR の誤認識・名刺の改版・実世界の変化を捕捉するためである。両者が high であっても、値が異なる場合は人による最終確認を担保する。

### 8.5.4 マージ画面での修正・確認 UI

マージ画面でのユーザー操作は以下のとおり。

- 値が正しい場合：「このフィールドは確認しました」のチェックボックスを ON。ContactFieldConfidence の confirmed_at / confirmed_by が記録される（confidence の値 mid / low はそのまま保持されるが、high 扱いとなる）
- 値を変更する場合：フィールドを編集して新しい値を入力。ContactFieldConfidence の confirmed_at / confirmed_by が記録される
- 値違いフィールドの確認方法：surviving の値を採用（チェックボックス）、merged の値を採用（ボタンで surviving 側に上書き）、どちらも違う（手入力）の 3 通り

マージ画面でのこれらの操作は、マージ確定ボタン押下まで DB に反映されない。実行前のキャンセルでマージ画面を離れた場合、Contact / ContactFieldConfidence は元の状態のまま保持される（トランザクション扱い）。

### 8.5.5 復元時の confidence の扱い

マージ実行後に復元（undo）した場合、マージ画面で人が修正・確認したフィールドの ContactFieldConfidence は元に戻さない（mid / low に戻さない）。「人が確認した結果は信頼できる成果物として残す」という設計思想に基づく。詳細は 9.5.2 参照。

### 8.5.6 設計趣旨

【過去のレビュー指摘について】v1.4.0 の 8.5 は「マージ画面に入る前に全 high 化が必要」という事前制約だったが、「8.5 はマージを止める」「全 high 必須は強すぎる」という指摘が複数回あった。v1.4.1 でこれらの指摘に応える形で、設計思想を「事前制約」から「マージ実行時の最終要件」に転換した。

## 8.6 グループ化（group_id）

同一 Person を起点とする同一ランクの DuplicateCandidate は、同じ group_id を持つ。これにより、レビュー画面で「Person A に関する exact_match の候補一覧」のような単位でレビューできる。

group_id 生成ロジック：バックグラウンド処理で重複チェック実行時、Person ごと・ランクごとに group_id を発行する。既存の group_id があれば再利用、なければ新規発行。

## 8.7 代表メール判定

メールアドレスのローカル部（@ より前）が以下のリストに該当する、または該当語の前後にハイフン・アンダースコア・ドットが付くバリエーションに該当する場合、代表メールと判定する。

初期リスト：info / contact / support / sales / admin / office / mail / inquiry / help / service / shop / customer / reception

バリエーション例：`info-jp@`、`sales_team@`、`info.jp@`、`sales.team@`、`support2@` なども代表メール扱い。

運用：config/constants.py の DUPLICATE_GENERIC_EMAIL_LOCALPARTS で管理。運用しながら追加可能。

### 8.7.1 代表メール判定リストの追加運用

リストは config/constants.py のソースコードとして管理する。追加・変更時はソースコード修正 → デプロイのフローを取る。動的な管理（Django Admin で UI から追加）は v1.5.0 以降で検討する。

リストに新しい値を追加した場合、過去に判定済みの DuplicateCandidate には影響しない。判定基準を遡及して見直したい場合は recheck_duplicates --all コマンド（12.9）で全件再判定する。

## 8.8 重複検出対象フィールド

重複検出のスコア計算に使うフィールドは、config/constants.py の DUPLICATE_CHECK_FIELDS で定数として管理する。同じ定数を ContactUpdateView の編集発火判定でも使用することで、整合性を保つ。

| DUPLICATE_CHECK_FIELDS（9 フィールド） |
|---|
| full_name, company, department, title, branch, email, phone, mobile, address |

## 8.9 different_person 判定の永続性

ユーザーが「別人として確定（different_person）」と判定した組み合わせは、システムが再度候補として上げない。Contact 編集後も再検出しない（過去の判定を尊重）。

ユーザーが「やっぱり同一人物だった」と気づいた場合の手動再判定は、v1.4.2 では実装しない。将来の手動 DuplicateCandidate 作成機能（v1.5.0 以降）で対応する。

## 8.10 重複検出の効率化アルゴリズム

### 8.10.1 課題と方針

主コンタクト同士の重複検出を素朴に実装すると、N Contact に対して N×(N-1)/2 回の比較が発生する。N=5000 で約 1250 万回となり、現実的な時間で処理できない。

8.4 のランク判定を逆算し、**possible_low 以上のランクになり得る候補だけを事前に DB で絞り込む** ことで、calculate_score の呼び出し回数を劇的に減らす。

### 8.10.2 絞り込み条件

possible_low 以上の必須条件は以下のとおり。

| ランク | 必須条件 |
|---|---|
| possible_low | フルネーム一致 |
| possible_mid | フルネーム一致 + メール or 携帯一致 |
| possible_high | 200点以上（フルネーム不一致でもメール+携帯+所属で達成可能） |
| exact_match | 200点以上 + 所属5フィールド両方一致 or 両方空 |

つまり、**フルネーム一致 / メール一致 / 携帯一致** のいずれも満たさない Contact は、possible_low 以上のランクにならない。

絞り込み条件：

- フルネーム完全一致（正規化後）
- メール完全一致（個人/代表問わず）
- 携帯番号完全一致

これらの **OR 条件** で対象 Contact を絞り込む。

### 8.10.3 関数定義

| 項目 | 内容 |
|---|---|
| 関数名 | `find_duplicate_contacts(contact)` |
| 配置 | duplicates/services/duplicate_detection.py |
| 性質 | 準関数（DB 読み取りはするが書き込みなし） |
| 入力 | contact: Contact（重複チェック対象、自身も主コンタクトであること） |
| 出力 | list[tuple]：各要素は (duplicate_contact: Contact, score: int, rank: str) |
| 比較対象 | DB 全体の status='primary' かつ Person.status='active' な Contact（自身を除く） |
| 絞り込み | 上記の OR 条件 |
| ランク判定 | rank='none' の候補は戻り値に含めない |
| パフォーマンス | cron 経由（`Run_Generate_Duplicate_Candidates`）で呼ばれる場合、`_calculate_score` 内の `get_field_confidences()` による N+1 を防ぐため、候補取得時に `prefetch_related('confidences')` を必須とする。ContactCreateView からの呼び出しは 1 件ずつのため必須としない |

### 8.10.4 呼び出し元

| 呼び出し元 | 用途 | 戻り値の使い方 |
|---|---|---|
| `Run_Generate_Duplicate_Candidates`（タスク層） | cron による全件重複チェック | 各タプルから DuplicateCandidate を構築して DB 保存（bulk_create 推奨） |
| `ContactCreateView`（手動 Contact 作成時） | 警告ダイアログ表示 | 各タプルを画面に表示（DB 保存しない） |

2 つの呼び出し元で同じ関数を共有することで、判定基準の一貫性を保つ。

### 8.10.5 効率の見積もり

- N=5000 の DB
- フルネーム一致：通常 0〜数件（同姓同名がいる場合のみ）
- メール一致：通常 0〜1 件（個人メールはほぼユニーク）
- 携帯一致：通常 0〜1 件
- 平均：1 Contact あたり 0〜数件の絞り込み

calculate_score の呼び出し回数：素朴な実装で N-1 = 4999 回 → 効率化後 0〜数件

### 8.10.6 cron の件数制限との関係

仕様書 12.2 で `--limit 100` がデフォルト。1 回の cron 実行で処理する Contact 件数が 100 件に制限されている。

100 件 × `find_duplicate_contacts(contact)` の処理時間（〜100ms）= 10 秒で 1 回の cron 実行が完了。5 分間隔の cron なら十分余裕。

---

# 第9章 マージ・復元仕様

## 9.1 マージ実行の経路

マージは必ず DuplicateCandidate 経由で実行する。Person 詳細画面から直接 Person を統合する経路は実装しない。

理由：すべてのマージを DuplicateCandidate のレビュー履歴として記録することで、判定理由・実行者・実行日時を一元管理する。

## 9.2 surviving_person の決定

どちらの Person を残すか（surviving）は、ユーザーがレビュー画面で選択する。デフォルトは「基準コンタクト判定ロジック」で決定された方が左側に表示され、デフォルトで surviving として選択される。

### 9.2.1 基準コンタクト判定ロジック

以下の優先順位で基準コンタクト（surviving 推奨側）を決定する。同点の場合は次の基準で判定。

| 順 | 判定基準 |
|---|---|
| 1 | PersonMergeLog で surviving_person として記録された回数が多い方 |
| 2 | Person に紐付く Contact の数が多い方 |
| 3 | 案件 DB やレポート DB 等の連携データが多い方（v1.5.0 以降の拡張用） |
| 4 | Contact の生成（created_at）が古い方 |
| 5 | 上記すべて同点の場合、DB クエリで first() で取得される方 |

このロジックは関数化し、duplicates/services/duplicate_detection.py または専用モジュールに実装する。

### 9.2.2 基準コンタクト判定の位置づけ

9.2.1 で記述したロジックは「マージ画面でデフォルト推奨される側」を決めるためのものであり、決定ではない。primary をどちらにするかは実際の運用ではユーザー都合で決めるべきものであり、ユーザーはマージ画面で自由に切り替えられる。

判定ロジックは「Person に紐づく情報が多い側のほうが、データとしては正確である可能性が高い」という前提に基づくが、これは実装上の便宜であり、業務上の優先度を表すものではない。

【過去のレビュー指摘について】「9.2.1 のロジックはマージ実績に基づくため、ユーザーの選択が学習的に切り替わる挙動になる」という観察があった。本節は、その観察への回答として「あくまで推奨で、ユーザーが自由に選択できる」という設計意図を明文化する。

## 9.3 マージ実行時の処理フロー

マージ実行は 1 ペア単位で行う。マージ画面でのユーザー操作（surviving / merged の選択、修正・確認、マージ理由の選択、補足記述）から、確定ボタン押下による DB 反映までをすべて 1 つのトランザクション内で実行する。途中失敗時は全ロールバックとする。

### 9.3.1 確定ボタン押下時の処理順序

1. ユーザーが選択した surviving / merged を確定する
2. バリデーション：surviving 側 Contact の DUPLICATE_CHECK_FIELDS が全 high であることを確認。additional_role の場合は merged 側 Contact も同条件を確認。条件を満たさない場合は、トランザクション開始前に処理を中断し、マージ画面に戻ってバリデーションエラーを表示（再入力を促す）
3. マージ画面で修正・確認されたフィールドの値を Contact に反映（surviving 側、additional_role なら merged 側も）。Contact.updated_by = マージ実行ユーザー、Contact.updated_at = マージ実行時刻として記録
4. 修正・確認されたフィールドの ContactFieldConfidence の confirmed_at / confirmed_by を記録（高扱いになる）
5. PersonMergeLog 作成（status='undoable'）。note にはマージ画面の操作内容（フィールド変更・確認・上書きの履歴）と、ユーザー入力の補足記述を組み立てて記録
6. merged_person の各 Contact を surviving_person に付け替え（person、previous_person、previous_status を記録、status を 9.4 の状態遷移に従って変更）
7. merged_person の Person.status を 'merged' に変更、merged_into を surviving に設定
8. 過去のマージログを locked に変更（merged_person を surviving とする undoable なログ）
9. **DuplicateCandidate の後処理として、12.8 の recover 処理を 9.3 のトランザクション内で実行する。値修正の有無に関係なく、`recover_duplicate_candidates` を呼び出す**
10. 当該マージの DuplicateCandidate を 'merged' に変更（review_status、review_result、reviewed_by、reviewed_at を記録）

【補足】手順 1 はユーザーがデフォルト推奨をそのまま使った場合と、明示的に切り替えた場合の両方をカバーする。手順 2 のバリデーションは、UI 側のバリデーションが破られた場合の最後の砦として機能する。手順 6 の状態遷移は確定後の merged 側に対して適用される。

【v1.4.2 補足：手順順序とサービス責務の明示】 マージ実行サービス（Execute_Merge_Only）は、上記手順を atomic 内で以下の順に呼ぶ：

1. **atomic 冒頭：CFC 確定処理** — surviving 側 primary_contact に紐づく `confirmed_at IS NULL` の CFC（未確認 low/mid）を `ContactFieldConfidence.mark_fields_as_confirmed(surviving_primary, field_names, user)` で一括 confirmed 化（マージ画面の確認 CB を ON でマージ実行した場合の CFC 反映を担保、Contact.fix() と同じパターン）
2. バリデーション（手順 2）
3. `merged_person.transfer_contacts_to(surviving_person, merge_reason)` 等の Contact 引き渡し
4. `merged_person.mark_as_merged(surviving_person)` を呼ぶ
5. `candidate.mark_as_merged(user, review_result, note)` を呼ぶ
6. `recover_duplicate_candidates(merged_person, surviving_person)`（冪等性のための防御チェックのみ、§12.8.3 参照）
7. surviving_person.duplicate_checked_at の更新

「mark_as_merged → recover」の順序を明示するのは、recover 関数の責務を「冪等性チェックのみ」に縮小し、状態変更の主体を呼び出し元（Execute_Merge_*）に集約する設計思想（§12.8.3）と整合させるため。マージ画面 UI の刷新と Execute_Merge_with_Updates 廃止に伴う §9.3.1 全面整理は、別途実施予定。

### 9.3.2 復元時の Person.primary_contact 同期

復元処理（9.5.2）では、Contact の status を previous_status に戻した後、Person.primary_contact の同期処理を実施する。Contact.status='primary' のものが Person.primary_contact と一致するように再同期する。同期処理は `Person.set_primary_contact()` インスタンスメソッド経由で実行する。

## 9.4 マージ前後のステータス遷移

マージ前後のサバイブ側・マージド側 Contact のステータス遷移、および previous_status / previous_person の記録ルールは、別添 PDF「**マージ前後のコンタクトのステータス等まとめ.pdf**」を**正本**とする。

**配置**：`/docs/spec/マージ前後のコンタクトのステータス等まとめ.pdf`（GitHub 管理 + Claude プロジェクトファイル）

PDF は merge_reason 別（merged 系 7 値 + different_person 系 3 値）に、サバイブ側パーソン・マージド側パーソンの各 Contact 群（プライマリー / アクティブ / インアクティブ）の status / previous_status / previous_person の遷移を表形式で示している。

### 9.4.1 サバイブ側パーソンに関する設計趣旨

サバイブ側に紐づいているコンタクトは、`previous_person`、`previous_status` の値の**変更をしない**。マージされたコンタクトの 1 つ前のマージ状態を保持し、マージされたことのないコンタクトは NULL のまま（既存の値は保持される）。

【補足】「変更しない」と「記録しない」は意味が異なる。サバイブ側 Contact の `previous_*` には、過去のマージで動いた履歴が既に入っている可能性がある。マージで status を変更した場合（修正ありで元 primary を inactive 化する場合）でも、`previous_*` は触らずそのまま保持する。Django の `update_fields=['status']` を使うのが正しい実装パターン。

### 9.4.2 マージド側パーソンに関する設計趣旨

マージド側パーソンに紐づくすべてのコンタクト（primary / active / inactive）はサバイブ側パーソンへ付け替える。付け替え時、`previous_person` にマージ前の merged_person を、`previous_status` にマージ前の status を記録する。

### 9.4.3 additional_role の特殊挙動

merge_reason='additional_role' のとき、マージド側元 primary を inactive ではなく **active**（副コンタクト化）として残す。別肩書（副業など）としてサバイブ側に紐付けるため。

その他の Contact（マージド側元 active / 元 inactive、サバイブ側全 Contact）の挙動は他の merge_reason と同じ。

### 9.4.4 切り分け基準（Execute_Merge_Only / Execute_Merge_with_Updates）

サービス分割の切り分け基準は **「フィールド修正の有無」** であり、merge_reason ではない。

- **コンタクト修正なし** → `Execute_Merge_Only` を呼ぶ
- **コンタクト修正あり** → `Execute_Merge_with_Updates` を呼ぶ

`merge_reason` は両サービスとも 7 値すべて受け付ける。

### 9.4.5 same_card かつコンタクト修正ありの特殊扱い

PDF 表は merge_reason 7 値すべてに対して同じ遷移パターンを示しているが、`Execute_Merge_with_Updates` の merge_reason='same_card' の場合のみ、実装上の挙動が他と異なる。

**same_card 修正なしの挙動（PDF 表通り）**

サバイブ側元 primary は変更なし（status='primary' のまま、`previous_*` も変更なし）。`Execute_Merge_Only` で処理。

**same_card 修正ありの挙動**

サバイブ側既存 primary について：

- **status は変更しない**（primary のまま）
- **フィールド値をフォームで修正された値で部分更新**（修正されていないフィールドは触らない）
- **ContactFieldConfidence は `ContactFieldConfidence.mark_fields_as_confirmed(contact, form.confirmed_field_names(), user)` で部分 confirmed 化**（修正されていないフィールドの confidence は変更しない）
- **新規 Contact は作らない**

マージド側 Contact 群は他の merge_reason と同じ挙動で transfer。

**設計趣旨**

same_card は「同一名刺の重複取り込み」が前提。値違いは新名刺の発行ではなく OCR 誤認識の可能性が高い。新規 Contact を作ると「同一名刺なのに 2 つの Contact がある」という意味的な不整合が起きる。よって既存 primary を直接更新し、新規 Contact は作らない運用とする。

ContactFieldConfidence の部分 confirmed 化（マージ画面 same_card のみ）と、`contact.fix(form, user)` 内の全 confirmed 化（修正画面 12 番 fix / 13 番 active 修正）の違いに注意：

- **same_card 修正あり（マージ画面）**：ユーザーが値違いを確認したフィールドのみ confirmed 化、それ以外の low/mid フィールドは触らない
- **fix（12 番 / 13 番）**：Form のバリデーションで全 low/mid フィールドが確認チェック ON されることが保証されているため、`contact.fix` 内で全 confirmed 化

### 9.4.6 副コンタクト増加問題

additional_role を多用すると、サバイブ側に多数の active 副コンタクトが紐づく可能性がある。v1.4.2 ではこの問題への対応として：

- 副コンタクトの増加は仕様上仕方がない
- inactive という仕組みがあるので、運用で**手動 inactive 化**で対応
- v1.5.0 以降で「副コンタクト整理機能」「inactive 一括変更機能」を検討する余地は残す

これは v1.4.2 のスコープを膨らませないための判断。

## 9.5 復元（undo）

### 9.5.1 復元可能な範囲

復元は 1 段階前まで可能。Contact.previous_person はマージのたびに上書きされるため、2 段階以上前のマージは PersonMergeLog.status='locked' として復元不可となる。

### 9.5.2 復元実行時の処理フロー

| 順 | 処理内容 |
|---|---|
| 1 | 対象 Contact の person を previous_person に戻す |
| 2 | 対象 Contact の status を previous_status に戻す |
| 3 | 対象 Contact の previous_person を NULL に戻す |
| 4 | 対象 Contact の previous_status を NULL に戻す |
| 5 | `merged_person.mark_as_active()` を呼ぶ（status='active' / merged_into=NULL、§10.4.1 参照） |
| 6 | Person.primary_contact の同期処理（Contact.status='primary' のものが Person.primary_contact と一致するように再同期、`Person.set_primary_contact()` 経由） |
| 7 | PersonMergeLog.status を 'undone' に変更、undone_by、undone_at を記録 |
| 8 | `merged_person.primary_contact.duplicate_checked_at = None` をセット（次回 cron で再判定対象にするため） |

すべての処理を 1 つのトランザクション内で実行する。

### 9.5.3 復元時の ContactFieldConfidence の扱い

復元時、マージ画面で人が修正・確認したフィールドの ContactFieldConfidence は変更しない。具体的には、ContactFieldConfidence のレコードを再作成して mid/low に戻す処理は行わない。

これは「人が確認した結果は信頼できる成果物として残す」という設計思想に基づく。復元は「マージ実行を取り消す」操作だが、データ品質確認作業の成果まで取り消す必要はない。

【結果として】

- surviving 側：マージ画面で確認・修正したフィールドは confirmed_at が記録された状態のまま、それ以外は元の状態のまま
- merged 側（additional_role 以外）：マージ画面で confidence は変更されていないので、元の状態のまま
- merged 側（additional_role）：マージ画面で確認・修正したフィールドは confirmed_at が記録された状態のまま、元の Person に戻る

additional_role の復元の場合、merged 側 Person は「確認済みの主コンタクトを持つ Person」として復活する。これは結果としてデータ品質が向上した状態であり、復元前より良い状態で元の Person 単独に戻ることになる。意図した副次効果である。

### 9.5.4 復元前の確認画面

ユーザーが「復元する」ボタンを押した際、現在の状態と復元後の予測状態を表示する確認画面に遷移する。確認画面で「復元実行」を押すことで実際の処理が行われる。

## 9.6 多重マージ対応

1 人の surviving_person に対して、複数の merged_person がマージされる場合がある。各マージは独立した PersonMergeLog レコードとして記録される。

ただし、surviving_person 自身が後にマージされる場合（A → B にマージ後、B → C にマージ）、過去のログ（A → B）は復元不可（locked）となる。

## 9.7 status='merged' の Person の制約

第4章 4.9 で記述。

## 9.8 アクティブ↔プライマリー入れ替え機能は実装しない

v1.4.2 ではアクティブコンタクトを primary に昇格させる機能（およびその逆）を**実装しない**。今後も議論しない。

### 設計趣旨

1. **DuplicateCandidate の整合性が崩れる**：primary 同士で重複検知している（8.2）ため、primary が入れ替わると検知済みの候補が無効になる
2. **ユーザー視点での混乱**：「どっちが本業？」を頻繁に切り替える運用は混乱を招く。固定された primary で運用する方が業務フローが安定する
3. **`set_primary_contact()` の責務には含まれない**：第10章で確定した責務範囲は「旧 primary を `old_primary_new_status` に降格、新 primary を昇格」であり、能動的な入れ替えは想定外

### 将来検討

副コンタクト関連の機能拡張（副コンタクトの inactive 化機能、整理機能）は v1.5.0 以降で検討する余地は残す。ただし入れ替え機能はコア設計に影響するため、慎重な検討が必要。

---

# 第10章 Django モデルメソッド体系

## 10.1 設計の出発点

v1.4.1 までは `merge_helpers.py` などのサービス層関数で「マージ実行時の各種処理」を担っていたが、議論メモで「これくらいの処理だと、普通にモデル使って書いた方が分かりやすい」という問題提起があった。

サービス層関数の責務が中途半端で、関数化のメリット（複雑な処理を名前で抽象化）が活きていない箇所がある。Django のモデルメソッドとして「自分自身の状態を変える処理」を表現する方が自然な箇所がある、という気づきに基づき、v1.4.2 では以下のモデルメソッド化を実施する。

`merge_helpers.py` ファイル全体を削除し、共通ヘルパー関数群を各モデルの責務に応じてモデルメソッド化する。

## 10.2 モデルメソッド化の判断基準

### 10.2.1 核心：「FK 保有はモデル横断ではない、他モデル状態変更が真の横断」

判断基準：

- **自己完結する状態遷移**：自分自身のフィールドを更新するだけ → モデルメソッド化（インスタンスメソッド）
- **自モデル集合操作**：自モデルのレコード群を一括更新 → モデルメソッド化（クラスメソッド）
- **真のモデル横断**：他モデルの状態を実際に変更する処理 → サービス層関数

### 10.2.2 例

- `PersonMergeLog.create()`：自分のレコードを作るだけ → クラスメソッド
- `PersonMergeLog.lock_past_logs(merged_person)`：自モデルのレコード群を一括更新 → クラスメソッド
- `merge_log.mark_as_undone(user)`：自分自身のフィールドを更新 → インスタンスメソッド
- `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)`：自モデルの新規レコード作成 → クラスメソッド
- `recover_duplicate_candidates(merged_person, surviving_person)`：DuplicateCandidate 横断＋ Person・Contact のフィールド更新 → サービス層関数

### 10.2.3 FK 保有とモデル横断の違い

`PersonMergeLog` は FK で `Person` を 2 つ持つが、自分のレコードを作るだけで Person 側のフィールドには触らない。これは「自己完結」であり「モデル横断」ではない。

一方、`recover_duplicate_candidates` は DuplicateCandidate を作りながら Person.duplicate_checked_at を更新するなど、**複数モデルの状態を実際に変更する**。これが「真のモデル横断」であり、サービス層に置くべき処理。

## 10.3 派生情報の同期はモデルメソッド化が許される例外

### 10.3.1 核心：「FK だけをいじる」「派生情報の同期」「整合性確保の責任が自モデル側にある」場合

許容条件：

1. 関連モデルの FK と派生情報のみを変更し、関連モデル独自の状態遷移は含まない
2. 整合性確保（partial unique constraint 等）の責任が自モデル側にある
3. 他のサービス層関数を経由するより、自モデルのメソッドとして書いた方が読みやすい

### 10.3.2 適用例

- `Person.set_primary_contact(new_contact, old_primary_new_status='active')`：旧 primary_contact の status を `old_primary_new_status` に、新 primary_contact の status を 'primary' に変更し、Contact.person FK の付け替えと Person.primary_contact の更新を行う。Contact 側の status は派生情報なので、Person 側のメソッドで同期させる
- `person.transfer_contacts_to(surviving_person, merge_reason)`：merged_person の各 Contact を surviving_person に付け替える。Contact 側の person FK と previous_* は派生情報なので、Person 側のメソッドで同期させる

## 10.4 Person のモデルメソッド詳細

### 10.4.1 インスタンスメソッド

Person の状態遷移を表すインスタンスメソッドは **`mark_as_*` シリーズ** で命名を揃える設計。`mark_as_merged` ↔ `mark_as_active` は対称ペアとして隣接配置する（PersonMergeLog の `mark_as_undone()` 等とも命名スタイルが揃う）。

| メソッド | 責務 | 配置先 |
|---|---|---|
| `person.mark_as_merged(surviving_person)` | 自身の状態遷移（status='merged' / merged_into=surviving_person / primary_contact=NULL） | persons/models.py |
| `person.mark_as_active()` | 自身の状態遷移（status='active' / merged_into=NULL）。マージ復元処理（§9.5.2）で merged → active に戻す際に呼ぶ。archived → active も汎用化（archived 中は対象 Person を誰も触れないため安全）。primary_contact の復元は `set_primary_contact()` 側で同期させるため、本メソッドには含めない | persons/models.py |
| `person.transfer_contacts_to(surviving_person, merge_reason)` | 自身のコンタクト群を surviving に引き渡す。`merge_reason` は `list[str]`（DuplicateMergeReason value のリスト、複数可）。Case A〜D（§9.4）のステータス遷移を適用、全 Contact 対象：primary / active / inactive すべて。詳細は §10.4.1.1 参照 | persons/models.py |
| `person.set_primary_contact(new_contact, old_primary_new_status='active')` | 既存 Person の primary_contact 切り替え（派生情報の同期） | persons/models.py |
| `person.get_active_contacts()` | status='active' の Contact 一覧を返す | persons/models.py |
| `person.get_inactive_contacts()` | status='inactive' の Contact 一覧を返す | persons/models.py |

#### 10.4.1.1 `person.transfer_contacts_to()` の詳細仕様

| 観点 | 記述内容 |
|---|---|
| 引数 | `surviving_person`（マージ先 Person）、`merge_reason: list[str]`（`DuplicateMergeReason.values` の部分集合、空リストは不可） |
| 対象 | 自 Person に紐づく全 Contact（status=primary / active / inactive すべて） |
| 処理 | `merge_reason` に応じて Case A〜D（§9.4）のステータス遷移を適用 |
| Case A | `same_card` 等：直接更新パターン（§9.4 / 別添 PDF 参照） |
| Case B | `transfer` / `promotion` / `job_change` / `name_change` / `other_merged`：標準的な引き渡しパターン（旧 primary は inactive、副コンタクト群も引き渡し） |
| Case C | `additional_role`：別肩書追加の特殊パターン。マージド側 primary を一時的に active に降格してから引き渡し、サバイブ側 primary は維持。partial unique constraint（Person.primary_contact が高々 1 件）違反を避ける順序制御を内部で行う |
| Case D | 復元時：previous_* を NULL にする不変原則を保つ（§9.4 参照） |
| 制約 | partial unique constraint 違反を避けるため、引き渡し順序を内部的に制御する |
| additional_role 判定 | `DuplicateMergeReason.ADDITIONAL_ROLE in merge_reason` で判定（複数選択可なので `in` 比較を使う） |

【サバイブ側 previous_* 不変原則】`transfer_contacts_to` はマージド側 Person の Contact を引き渡す処理であり、サバイブ側 Person の Contact の previous_* フィールドには一切触れない（業務所有権の分離）。

詳細な状態遷移は §9.4 および別添 PDF『マージ前後のコンタクトのステータス等まとめ.pdf』を参照。

### 10.4.2 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `Person.get_active()` | status='active' の Person 一覧を返す（PersonListView 用） | persons/models.py |
| `Person.get_archived()` | status='archived' の Person 一覧を返す（将来の archived 一覧画面用） | persons/models.py |

### 10.4.3 `Person.set_primary_contact()` の詳細仕様

#### 処理内容

1. 旧 primary_contact の status を `old_primary_new_status` で指定された値に変更
2. 新 primary_contact の status を 'primary' に変更
3. Contact.person FK の付け替え（new_contact が他 Person 配下なら surviving_person に付け替える）
4. `Person.primary_contact = new_contact` に更新

#### `old_primary_new_status` の値

| 値 | 旧 primary の遷移先 | 使用場面 |
|---|---|---|
| `'active'` | active（副コンタクト化）| デフォルト値として保持。**v1.4.2 時点では実装上の使用場面なし**（呼び出し元はすべて `'inactive'` を明示。将来の拡張余地として API は維持） |
| `'inactive'` | inactive（過去情報化）| 修正画面 transfer / promotion / job_change / name_change、マージ画面 transfer 等 |

#### 呼ばれる場所

- 修正画面 ContactUpdateView（change_reason='transfer' / 'promotion' / 'job_change' / 'name_change' のとき）：`person.set_primary_contact(new_contact, old_primary_new_status='inactive')`
- 新規 Person 作成時（contacts/views.py `_create_person_and_contact` 内）：`person.set_primary_contact(contact)`（デフォルト引数で呼ぶが、旧 primary が存在しないため status 変更ステップはスキップされ、`old_primary_new_status` の値は実質不使用）

`change_reason='fix'` の場合は `contact.fix(form, user)` で既存 Contact を上書きするため `set_primary_contact` は呼ばない（§11.4.1 修正理由による処理分岐を正とする）。

#### 設計趣旨

修正画面の transfer 等とマージ画面の transfer 等で、コードの形が揃う：

- 両方とも `set_primary_contact(new_contact, old_primary_new_status='inactive')` を呼ぶだけ
- 「旧 primary を active 化 → 直後に inactive 上書き」という不自然な順序がなくなる
- 引数を見れば旧 primary の遷移先が一目で分かる

### 10.4.4 設計趣旨（Person のモデルメソッド全般）

`mark_as_merged` / `transfer_contacts_to` / `set_primary_contact` は、関連モデル（Contact）の FK と派生情報のみを変更し、関連モデル独自の状態遷移は含まない。Person.primary_contact が「正本」、Contact.status='primary' が「派生情報」とする設計に基づき、これらの同期処理は Person の責務として置く（10.3 派生情報の同期はモデルメソッド化が許される例外、参照）。

`person.get_primary_contact()` は採用しない。`person.primary_contact`（FK 直接参照）で 1 行で済む処理を間接化する意義が薄いため。

## 10.5 Contact のモデルメソッド詳細

### 10.5.1 インスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `contact.fix(form: ContactUpdateForm, user)` | フォーム値で自身のフィールドを上書きし、全 ContactFieldConfidence を confirmed 化する | contacts/models.py |
| `contact.get_field_confidences()` | 全フィールドの ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス、DB 保存しない） | contacts/models.py |
| `contact.get_high_fields()` | 実質 high なフィールド集合を返す（疑似 high または confirmed_at が記録されたものは high 扱い） | contacts/models.py |
| `contact.is_all_field_confidence_high(fields=None)` | 全 high 判定（引数省略時は全フィールド、指定時は範囲限定） | contacts/models.py |

### 10.5.2 `contact.fix()` の詳細仕様

#### シグネチャ

`contact.fix(form: ContactUpdateForm, user) -> None`

form 引数の型は `ContactUpdateForm` に限定（`MergeForm` は受け付けない）。型ヒントから「fix 画面専用」が読める。

#### 責務

1. **ガード**：`self.pk` が `None` の場合エラー（save 済みの Contact のみ受け付ける）
2. **フォーム値で自身のフィールドを上書き**：差分のあるフィールドのみ更新、`save(update_fields=changed_fields)` で限定 save
3. **全 ContactFieldConfidence を confirmed 化**：`ContactFieldConfidence.mark_fields_as_confirmed(self, all_low_mid_field_names, user)` を呼ぶ

#### 呼ばれる場所

- 12 番 UpdatePrimaryContactView（change_reason='fix' のとき）
- 13 番 UpdateActiveContactView（change_reason フィールドなし、fix 相当の処理に固定）

マージ画面 same_card は `contact.fix` を呼ばない（`Execute_Merge_with_Updates` 内で別処理として書き分け、9.4.5 参照）。

### 10.5.3 `contact.get_field_confidences()` の戻り値仕様

#### 戻り値の形式

```
{
    'full_name': <ContactFieldConfidence: confidence='high' (疑似)>,
    'company': <ContactFieldConfidence: confidence='medium', confirmed_at=None>,
    'title': <ContactFieldConfidence: confidence='medium', confirmed_at=2026-05-04>,
    'email': <ContactFieldConfidence: confidence='low', confirmed_at=None>,
    'address': <ContactFieldConfidence: confidence='high' (疑似)>,
    ...
}
```

**全フィールドのキーが含まれる**。high のフィールドは ContactFieldConfidence の疑似インスタンス（DB 保存しない）として生成して返す。

#### 実装責務の分離

Contact 側は薄いラッパーとして `ContactFieldConfidence.get_for_contact(self)` を呼ぶだけ。実ロジックは ContactFieldConfidence 側に置く。

#### 3 状態の判定（テンプレート・カスタムタグ側）

| 状態 | 判定ロジック | 表示例 |
|---|---|---|
| 1. high（確定） | `conf.confidence == 'high'` | 「高」 |
| 2. high扱い（確認済み） | `conf.confidence in ('low', 'medium') and conf.confirmed_at != None` | 「確認済み」 |
| 3. mid/low 未確認 | `conf.confidence in ('low', 'medium') and conf.confirmed_at == None` | 「要確認」 |

#### メリット

- インターフェースが統一される（テンプレート・サービス層・カスタムタグすべて ContactFieldConfidence インスタンスを扱う）
- 拡張性が高い（confirmed_at / confirmed_by 等の既存フィールドもそのまま使える）
- 「ContactFieldConfidence は Contact のメタデータ」という設計思想に最も忠実

### 10.5.4 採用しなかったメソッド案

- `Contact.transfer_to()`：Person 主語に変更したため不採用。「merged_person よ、お前のコンタクトを surviving に渡せ」と Person 主語で命令する形が自然（Tell, Don't Ask 原則）。公開 API は `merged_person.transfer_contacts_to(surviving_person, merge_reason)` で統一
- `Contact.save_with_confirmation()`：「確認されたフィールドのリスト」を引数で受け取る必要があり、Form の状態を Contact が知ることになる（責務混在）。マージ画面でしか使われない汎用性の低いメソッド。代わりに `ContactFieldConfidence.mark_fields_as_confirmed(contact, field_names, user)` クラスメソッドを採用

## 10.6 ContactFieldConfidence のモデルメソッド詳細

### 10.6.1 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `ContactFieldConfidence.get_for_contact(contact)` | 全フィールド分の ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） | contacts/models.py |
| `ContactFieldConfidence.create_for_contact(contact, confidence_map)` | OCR 結果の medium/low フィールドについて一括作成 | contacts/models.py |
| `ContactFieldConfidence.mark_fields_as_confirmed(contact, field_names, user)` | 指定フィールドを確認済み化（マージ画面・修正画面で使用） | contacts/models.py |

### 10.6.2 疑似インスタンスの防御策

`get_for_contact()` は high のフィールドについて疑似インスタンスを返すが、これが誤って save() されると DB に high レコードが入ってしまい仕様書 4.6 の「high は記録対象外」が破れる。また mid/low の既存レコードを誤って上書きすると confirmed_at 等の確認履歴が壊れる。

これを防ぐため、以下の三重防御を実装する。

1. **CheckConstraint（DB 制約）**：`confidence='high'` のレコード保存を物理的に禁止
2. **save() オーバーライド（アプリケーション層）**：`confidence='high'` で `save()` が呼ばれた場合、明示的なエラーメッセージで誤用を検出
3. **仕様書ルールでの mid/low レコードの保護**：`ContactFieldConfidence.save()` を直接呼ぶことは禁止する。新規作成は `create_for_contact()`、確認済み化は `mark_fields_as_confirmed()` 経由でのみ行う

### 10.6.3 設計趣旨

書き込み系（自モデルのレコード作成・更新）は ContactFieldConfidence のクラスメソッドとして配置。読み取り系（Contact のフィールドごとの信頼度を取得）は Contact のモデルメソッド（`get_field_confidences()` 等）として配置。実装は ContactFieldConfidence 側のクラスメソッド `get_for_contact()` に委譲する。

理由：ContactFieldConfidence は概念的に Contact のメタデータの一部であり、読み取り側は Contact から自然にアクセスできるべき。書き込み側は ContactFieldConfidence 自身の責務として、自モデル集合操作（クラスメソッド）が自然。

### 10.6.4 ContactFieldConfidence の生成・更新タイミング（3 ケース別）

ユーザー入力は全 high で信頼するため、ContactFieldConfidence は OCR で取り込まれた Contact のみで作成される。3 ケース別の整理は以下のとおり。

#### ケース 1：新規作成（10 番 ContactCreateView / 9 番 PersonAddAdditionalRoleView）

- ContactFieldConfidence は作成しない（ユーザー入力なので全 high 扱い）
- DB レコード数が減り、コード君の実装が単純化される

#### ケース 2：既存修正（12 番 fix / 13 番 active 修正、`contact.fix(form, user)`）

- 既存の low/mid フィールドの ContactFieldConfidence は `mark_fields_as_confirmed()` で全 confirmed 化（confirmed_at / confirmed_by を記録）
- 新規に ContactFieldConfidence を作成することはない（既存レコードの更新のみ）

#### ケース 3：マージ画面 same_card 特殊処理（17 番 `Execute_Merge_with_Updates` で merge_reason='same_card' かつ修正あり）

- ユーザーが値違いを確認したフィールドのみ `mark_fields_as_confirmed()` で部分 confirmed 化
- それ以外の low/mid フィールドの ContactFieldConfidence は触らない（部分 confirmed 化）

#### ContactFieldConfidence が作成される唯一の場面

OCR で取り込まれた Contact のみ、Claude の confidence 判定により low/mid/high が混在する。**low/mid のフィールドだけ ContactFieldConfidence レコードが作成される**（high は記録対象外）。

## 10.7 DuplicateCandidate のモデルメソッド詳細

### 10.7.1 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `DuplicateCandidate.get_pending(contact)` | contact が紐づく Person の pending 候補を取得 | duplicates/models.py |
| `DuplicateCandidate.get_merged(contact)` | contact が紐づく Person の merged 候補を取得（マージ履歴表示用） | duplicates/models.py |
| `DuplicateCandidate.get_different_person(contact)` | contact が紐づく Person の different_person 候補を取得 | duplicates/models.py |
| `DuplicateCandidate.get_invalidated(contact)` | contact が紐づく Person の invalidated 候補を取得（開発・デバッグ用） | duplicates/models.py |
| `DuplicateCandidate.has_duplicates(contact, status)` | 指定 status の候補が存在するかどうかの判定（True/False） | duplicates/models.py |
| `DuplicateCandidate.get_by_group(group_id)` | group_id 単位で取得（レビュー画面の PRG パターン用） | duplicates/models.py |
| `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` | old_candidate からスコア・ランク・group_id 等をコピーして新規 DuplicateCandidate を作成（review_status='pending'） | duplicates/models.py |

### 10.7.2 インスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `candidate.mark_as_merged(user, review_result, note)` | 自身の状態遷移（review_status='merged' / review_result / reviewed_by / reviewed_at / note） | duplicates/models.py |
| `candidate.mark_as_different_person(user, review_result, note=None)` | 自身の状態遷移（review_status='different_person' / reviewed_by / reviewed_at / note） | duplicates/models.py |
| `candidate.record_different_person_action(user)` | 自身の別人判定操作を ActionLog に記録（action='different_person'、data に判定理由を格納） | duplicates/models.py |

### 10.7.3 設計趣旨

`get_pending` / `get_merged` / `get_different_person` / `get_invalidated` はクエリセットを返す設計とする。呼び出し側で `.count()` や `.filter()` を追加して柔軟に絞り込める。

引数は contact で統一。Contact → Person 変換は内部で行う。これにより、CardListView / ContactDetailView 等から直接 contact を渡せて呼び出し側がシンプルになる。

`get_by_group(group_id)` のみ引数が group_id。レビュー画面（DuplicateCandidateGroupUpdateView）が group_id 単位で動くため。

`create_recovered_from(old_candidate, new_surviving_person)` クラスメソッドは、recover 処理での DuplicateCandidate 新規作成を、`merge_executor.py` 内で直接 `DuplicateCandidate.objects.create()` を呼ぶのではなく、本クラスメソッド経由で行う。これにより「old_candidate からスコア・ランク・group_id 等をコピーして新規作成する」処理ロジックが DuplicateCandidate モデル側に集約され、関数名から意図が読める。

`candidate.record_different_person_action(user)` の命名は、状態遷移メソッド `mark_as_merged` / `mark_as_different_person` との対称性、PersonMergeLog 側の `merge_log.record_merge_action(user)` / `merge_log.record_undo_action(user)` との一貫性、将来 `DuplicateCandidate` に新たな記録メソッドが追加された場合の拡張性を考慮した命名である。

### 10.7.4 開発時のデバッグ画面での活用

開発時には `get_invalidated` を含む各 status の取得メソッドを画面表示で活用する。たんたんの方針として「各ビューの画面にデバッグ時は自分も UI 上で値確認したい」という運用方針があり、開発フェーズで重要な役割を果たす。

## 10.8 PersonMergeLog のモデルメソッド詳細

### 10.8.1 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `PersonMergeLog.create(surviving_person, merged_person, user)` | マージ実行のためのログレコードを作成（インスタンス生成＋save() を一気に実行）。duplicate_candidate / note 等は呼び出し側で追加設定 | duplicates/models.py |
| `PersonMergeLog.lock_past_logs(merged_person)` | 過去のログを locked 状態に変更(自モデル集合操作) | duplicates/models.py |
| `PersonMergeLog.get_for_person(person)` | Person 単位のログ一覧取得（マージログ一覧画面用） | duplicates/models.py |
| `PersonMergeLog.get_undoable(person)` | 復元可能なログ取得 | duplicates/models.py |

### 10.8.2 インスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `merge_log.is_undoable()` | 復元可能かどうかの判定（status='undoable' なら True） | duplicates/models.py |
| `merge_log.mark_as_undone(user)` | 自身の状態遷移（status='undone' / undone_by / undone_at） | duplicates/models.py |
| `merge_log.record_merge_action(user)` | マージ実行を ActionLog に記録（action='merged'、data に surviving/merged Person 情報、duplicate_candidate ID 等） | duplicates/models.py |
| `merge_log.record_undo_action(user, note="")` | 復元実行を ActionLog に記録（action='undone'、data に `{"note": str}` 形式で MergeUndoForm から受け取った備考を保存。空文字でも `{"note": ""}` で記録し、集計時のキーを揃える） | duplicates/models.py |
| `merge_log.get_undo_preview()` | 復元後の予測状態を返す（確認画面表示用：復元 Person・復元 Person に戻る Contact の集合・surviving 側 Person に残る Contact の集合） | duplicates/models.py |

### 10.8.3 `merge_log.get_undo_preview()` の戻り値設計

PersonMergeLogConfirmUndoView（復元確認画面）で「現在の状態と復元後の予測状態を表示する」ために使う。

戻り値は dict：

| キー | 値 |
|---|---|
| `merged_person` | Person |
| `contacts_to_restore` | QuerySet[Contact]（merged_person に戻る Contact の集合） |
| `contacts_remaining_in_surviving` | QuerySet[Contact]（surviving 側に残る Contact の集合） |

UI 側でこの dict を加工して表示する。実際の DB 変更は行わない（プレビューのみ）。

### 10.8.4 設計趣旨

`PersonMergeLog.create()` は「マージ用ログレコードを作成する」処理を 1 メソッドに集約。呼び出し側は `merge_log = PersonMergeLog.create(surviving_person, merged_person, user)` の 1 行で完結し、その後 `merge_log.duplicate_candidate = candidate` / `merge_log.note = note` を設定して保存する流れ。

ActionLog 記録メソッドの 2 分離（`record_merge_action` / `record_undo_action`）について：

- マージ実行時と復元実行時で記録するアクション内容が異なるため、PersonMergeLog のインスタンスメソッドを 2 つに分離
- インスタンス側（`record_*_action`）：自モデルの状態を ActionLog に記録する責務
- クラス側（`ActionLog.record(...)`）：任意の業務イベントを直接記録する責務（cron 実行ログなど、モデルインスタンスを持たない場面）
- 両者でメソッド名を変えているのは、インスタンス側は「自分の操作を記録する」ニュアンス、クラス側は「汎用的に記録する」ニュアンスを区別するため

状態遷移と ActionLog 記録は分離（一体化しない）：

- `mark_as_*()` は状態遷移だけ
- `record_*_action()` はログ記録だけ
- マージ実行のフローでは両方を順に呼ぶ

理由：

- 単一責任：状態遷移と記録が別メソッドで明確
- テスト容易性：状態遷移と記録が分離されているのでテストしやすい
- 例外処理：もしログ記録だけ失敗した場合、状態遷移は成功している方がシンプル

抽象基底クラス・ミックスイン化は v1.4.2 では実装しない。PersonMergeLog だけ実装、運用で固まったら将来共通化する判断。

## 10.9 ActionLog のモデルメソッド詳細

### 10.9.1 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `ActionLog.record(user, action, content_object=None, object_repr='', data=None, note='')` | 任意の業務イベントを直接記録（cron 実行ログなど、モデルインスタンスを持たない場面で使用） | actionlogs/models.py |

### 10.9.2 設計趣旨

ActionLog の書き込みは 2 通り：

- モデルインスタンスがある場合：インスタンスメソッド経由（`merge_log.record_merge_action(user)` / `candidate.record_different_person_action(user)` 等）
- モデルインスタンスがない場合：`ActionLog.record(...)` クラスメソッド直接呼び（cron 実行ログ、OCR 処理結果など）

詳細は第4章 4.11.2 を参照。

## 10.10 OriginalImage / BusinessCard のモデルメソッド詳細

### 10.10.1 OriginalImage のクラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `OriginalImage.get_pending(limit)` | pending な OriginalImage を limit 件取得（cron 用） | cards/models.py |
| `OriginalImage.release_stuck_locks(threshold_minutes)` | stuck な processing レコードを pending に戻す | cards/models.py |

### 10.10.2 OriginalImage のインスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `original_image.get_image_url()` | サムネイル用 URL を返す | cards/models.py |
| `original_image.get_image_url_full()` | フルサイズ用 URL を返す | cards/models.py |

### 10.10.3 BusinessCard のインスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| `business_card.get_card_image_url()` | サムネイル用 URL を返す | cards/models.py |
| `business_card.get_card_image_url_full()` | フルサイズ用 URL を返す | cards/models.py |

## 10.11 各モデルメソッドの View からの呼び出し関係

各 View で使用するモデルメソッド・カスタムタグの一覧。

| View / 起動契機 | 使用するメソッド・タグ |
|---|---|
| CardListView | `DuplicateCandidate.get_pending(contact)` / `business_card.get_card_image_url()` / `{% card_image %}` |
| CardDetailView | `business_card.get_card_image_url()` / `business_card.get_card_image_url_full()` / `{% json_tree %}` |
| OriginalListView | `original_image.get_image_url()` / `{% original_image_thumbnail %}` |
| OriginalDetailView | `original_image.get_image_url()` / `original_image.get_image_url_full()` / `{% json_tree %}` |
| PersonListView | `Person.get_active()` / `DuplicateCandidate.get_pending(contact)` |
| PersonDetailView | `person.get_active_contacts()` / `person.get_inactive_contacts()` / `DuplicateCandidate.get_pending(contact)` / `PersonMergeLog.get_for_person(person)` |
| ContactDetailView | `contact.get_field_confidences()` / `DuplicateCandidate.get_pending(contact)` / `{% contact_confidence %}` |
| ContactCreateView | `find_duplicate_contacts(contact)` |
| UpdatePrimaryContactView（12 番） | `contact.fix(form, user)`（fix の場合）/ `Person.set_primary_contact()`（transfer 等の場合）/ `ContactFieldConfidence.mark_fields_as_confirmed()` |
| UpdateActiveContactView（13 番） | `contact.fix(form, user)`（fix 相当の処理に固定） |
| PersonAddAdditionalRoleView（9 番） | View 直書き（save 済み Contact が前提でないため、`set_primary_contact()` は使えない、10.12 参照） |
| DuplicateCandidateGroupListView（15 番） | `DuplicateCandidate.get_pending(contact)` / `DuplicateCandidate.get_by_group()` |
| DuplicateCandidateGroupDetailView（16 番） | 当該グループの DuplicateCandidate を review_status ごとに集計 |
| DuplicateCandidateGroupUpdateView（17 番） | `Execute_Merge_Only()` / `Execute_Merge_with_Updates()` / `Mark_as_Different_Person()` / `Contact.is_all_field_confidence_high()` / `contact.get_field_confidences()` |
| PersonMergeLogListView（19 番） | `PersonMergeLog.get_for_person()` / `PersonMergeLog.get_undoable()` |
| PersonMergeLogDetailView（20 番） | `merge_log.is_undoable()` |
| PersonMergeLogConfirmUndoView（21 番） | `Execute_Merge_Undo()` / `merge_log.get_undo_preview()` |

## 10.12 別肩書追加画面（9 番）の処理は View 直書き

別肩書追加画面の処理はメソッド化せず、View 内で直書きする。

### 理由

- v1.4.2 で active として新規 Contact を Person に紐付ける処理は、別肩書追加画面（9 番 PersonAddAdditionalRoleView）でしか発生しない
- `set_primary_contact()` と引数の前提が違う（`set_primary_contact()` は save 済み Contact が前提、9 番では pk なしの新規 Contact）ので、メソッドを並べるとコード君が迷う
- View 直書きでも 3〜4 行で完結する

### 処理内容

- フォーム値で新規 Contact を生成（pk なし）
- `new_contact.person = person`
- `new_contact.status = 'active'`
- `new_contact.save()` で DB に保存
- ContactFieldConfidence は作らない（ユーザー入力なので全 high、10.6.4 参照）

---

# 第11章 Django 実装設計

## 11.1 アプリケーション構成

v1.4.0 では既存の cards アプリに加えて、新たに 4 つのアプリを追加する。

| アプリ | 用途 |
|---|---|
| cards（既存） | BusinessCard、OriginalImage 関連 |
| persons(新規) | Person 関連 |
| contacts（新規） | Contact 関連 |
| duplicates（新規） | DuplicateCandidate、PersonMergeLog 関連 |
| actionlogs（新規） | ActionLog 関連（モデル横断の汎用ログ） |

## 11.2 ディレクトリ構成

v1.4.x で追加・変更するファイルを以下に示す。

| パス | 用途 |
|---|---|
| config/constants.py | 共通 TextChoices、定数（DUPLICATE_CHECK_FIELDS、DUPLICATE_GENERIC_EMAIL_LOCALPARTS 等） |
| persons/models.py | Person モデル |
| persons/views.py | PersonListView、PersonDetailView、PersonAddAdditionalRoleView |
| contacts/models.py | Contact モデル、ContactFieldConfidence モデル |
| contacts/views.py | ContactCreateView、ContactDetailView、UpdatePrimaryContactView、UpdateActiveContactView、PreviewContactView |
| contacts/forms.py | ContactBaseForm、ContactUpdateForm、ContactUpdateActiveForm、ContactAddAdditionalRoleForm、ContactCreateForm |
| contacts/services/normalization.py | フィールド正規化（純関数） |
| contacts/services/json_parser.py | raw_json → Contact 用辞書（v1.3.4 の json_normalizer から移動・拡張） |
| duplicates/models.py | DuplicateCandidate、PersonMergeLog モデル |
| actionlogs/models.py | ActionLog モデル |
| duplicates/views.py | DuplicateCandidateGroupListView、DuplicateCandidateGroupDetailView、DuplicateCandidateGroupUpdateView、PersonMergeLogListView、PersonMergeLogDetailView、PersonMergeLogConfirmUndoView |
| duplicates/forms.py | MergeForm、MergeUndoForm |
| duplicates/services/duplicate_detection.py | find_duplicate_contacts、_calculate_score、_determine_rank、determine_base_person |
| duplicates/services/merge_executor.py | Mark_as_Different_Person、Execute_Merge_Only、Execute_Merge_with_Updates、Execute_Merge_Undo、recover_duplicate_candidates、invalidate_pending_candidates |
| duplicates/tasks/duplicate_check_runner.py | generate_duplicate_candidates_for_contact（タスク層下位関数） |
| cards/management/commands/check_duplicates.py | cron 起動。Run_Generate_Duplicate_Candidates 呼び出し |
| cards/management/commands/recheck_duplicates.py | 全 Contact の duplicate_checked_at リセット |
| cards/management/commands/dev_reset_duplicates.py | 開発用 DuplicateCandidate リセット |
| templates/contacts/ ほか各テンプレート | 各画面のテンプレート |

`duplicates/services/merge_helpers.py` は v1.4.2 で全削除する（モデルメソッド化により不要）。

## 11.3 URL 一覧表

各 URL とその役割・内部処理を以下に示す。

| No. | URL | メソッド | View 名 | 役割・内部処理 |
|---|---|---|---|---|
| 1 | `/` | GET | HomeView | ホーム画面 |
| 2 | `/cards/upload/` | GET / POST | OriginalImageUploadView | 名刺画像アップロード |
| 3 | `/cards/` | GET | CardListView | 名刺一覧 |
| 4 | `/cards/<uuid:pk>/` | GET | CardDetailView | 名刺詳細 |
| 5 | `/originals/` | GET | OriginalListView | 元画像一覧 |
| 6 | `/originals/<uuid:pk>/` | GET | OriginalDetailView | 元画像詳細 |
| 7 | `/persons/` | GET | PersonListView | 人物一覧 |
| 8 | `/persons/<uuid:pk>/` | GET | PersonDetailView | 人物詳細 |
| 9 | `/persons/<uuid:pk>/add-additional-role/` | GET / POST | PersonAddAdditionalRoleView | 別肩書追加。Active コンタクトを追加 |
| 10 | `/contacts/create/` | GET / POST | ContactCreateView | 名刺なしでプライマリーコンタクトとパーソンを同時生成 |
| 11 | `/contacts/<uuid:pk>/` | GET | ContactDetailView | コンタクトの表示画面 |
| 12 | `/contacts/<uuid:pk>/update-primary/` | GET / POST | UpdatePrimaryContactView | プライマリーコンタクトの修正画面（fix の場合は既存コンタクトを上書き、transfer / promotion / job_change / name_change の場合は新規コンタクトを追加し既存を inactive 化）。プライマリーコンタクト以外がこのルートに入ってきたらガード |
| 13 | `/contacts/<uuid:pk>/update-active/` | GET / POST | UpdateActiveContactView | アクティブコンタクトの修正画面。プライマリーのように新規コンタクト生成なし、コンタクト値の修正のみ。change_reason フィールドは置かない（fix 相当の処理に固定）。アクティブコンタクト以外がこのルートに入ってきたらガード |
| 14 | `/contacts/<uuid:pk>/preview/` | GET | PreviewContactView | コンタクト一覧画面からのモーダルプレビュー用、AJAX 専用 |
| 15 | `/duplicates/` | GET | DuplicateCandidateGroupListView | 重複候補グループ一覧 |
| 16 | `/duplicates/groups/<uuid:group_id>/` | GET | DuplicateCandidateGroupDetailView | 同一グループ DuplicateCandidate の詳細表示。マージのレビューの最終結果表示画面（17 番からのリダイレクト直後は Django messages で完了メッセージを表示） |
| 17 | `/duplicates/groups/<uuid:group_id>/review` | GET / POST | DuplicateCandidateGroupUpdateView | マージレビュー画面。GET で次のペアを表示、POST で処理（Mark_as_Different_Person / Execute_Merge_Only / Execute_Merge_with_Updates のいずれか）→ 同一 URL に GET リダイレクト（PRG パターン）。すべて処理完了したら 16 番にリダイレクト + Django messages で結果メッセージ |
| 19 | `/merge-logs/` | GET | PersonMergeLogListView | マージログ一覧 |
| 20 | `/merge-logs/<uuid:pk>/` | GET | PersonMergeLogDetailView | マージログ詳細 |
| 21 | `/merge-logs/<uuid:pk>/confirm-undo/` | GET / POST | PersonMergeLogConfirmUndoView | マージ復元の確認画面と実行処理。実行完了後は詳細画面へリダイレクト。メッセージで詳細画面に復元実行結果を表示 |

一覧画面なし：`/contacts/`（Contact 一覧）、`/persons/<uuid>/update/`（Person 編集）。

命名規則：URL 名は update（edit ではない）、Class 名は XxxUpdateView / XxxCreateView 等。

### 11.3.1 旧 18 番の廃止

旧 `/duplicates/groups/<uuid:group_id>/result/`（DuplicateCandidateGroupResultView）は廃止された。17 番のリダイレクト先を 16 番に変更したため不要となった。

## 11.4 View 層の設計

View 層は薄く保ち、ビジネスロジックは services / tasks 層またはモデルメソッドに委譲する。

### 11.4.1 ContactUpdateView の修正理由による処理分岐（12 番 UpdatePrimaryContactView）

12 番の Contact 編集画面では、ユーザーが修正理由を選択する。理由によって内部処理が異なる。

| 値 | 表示名 | 内部処理 |
|---|---|---|
| fix | 入力間違い・誤字訂正 | 既存 Contact を更新（`contact.fix(form, user)` 経由） |
| transfer | 異動・部署変更 | 新規 Contact 作成 + 既存を inactive に |
| promotion | 役職変更・昇進 | 新規 Contact 作成 + 既存を inactive に |
| job_change | 転職 | 新規 Contact 作成 + 既存を inactive に |
| name_change | 結婚等による姓変更 | 新規 Contact 作成 + 既存を inactive に |

修正理由は config/constants.py の PersonChangeReason（TextChoices、5 値）で定義する。`additional_role`（別肩書追加）は v1.4.2 で 12 番から削除し、独立画面（9 番 PersonAddAdditionalRoleView）に分離した。

### 11.4.2 新規 Contact 作成時のフィールド初期値

transfer / promotion / job_change / name_change で新規 Contact を作成する際、フィールド初期値は既存 Contact のフィールドを全コピーする。ユーザーは編集対象のフィールドだけ変更して保存する。

マージ理由ごとに自動でクリアするフィールドを変える方式は採用しない。実世界では「異動と同時に携帯番号も変わる」「結婚と同時に勤務先も変わる」など変則的なケースが多く、自動クリアはかえって入力ミスを誘発するためである。ユーザーが意識して変えるべき箇所を変える運用を前提とする。

既存 Contact の ContactFieldConfidence は新規 Contact にはコピーしない。新規 Contact のフィールドは、ユーザー入力直後の状態として扱う（confidence のレコードは作成されず、すべて high 扱い、第10章 10.6.4 参照）。

【マージ画面での値修正の扱い】設計案 A により、マージ画面で surviving 側 Contact のフィールドを値修正することがある。この修正は Contact.updated_by = マージ実行ユーザー、Contact.updated_at = マージ実行時刻として記録される。修正と同時に 12.7 の処理が発火する。マージ実行のトランザクションは 12.7 の発火と一体で処理する（ただし、マージ実行のトランザクション内では 12.8 の recover 処理が呼ばれるため、結果として 12.7 の invalidate 処理は不要となる、12.8 参照）。

### 11.4.3 ContactUpdateActiveView の処理（13 番 UpdateActiveContactView）

13 番（active 副コンタクト修正画面）は fix 相当の処理に固定する。`contact.fix(form, user)` を呼ぶ。change_reason フィールドは置かない（5 値の PersonChangeReason は適用しない）。

### 11.4.4 ContactCreateView の重複警告（10 番）

保存時に possible_high 以上の重複候補を検出し、警告ダイアログを表示する。

候補は上位 5 件 + 「+他 N 件」の表示形式。各候補に「詳細を見る」リンク（クリックで AJAX で /contacts/<id>/preview/ を取得し、モーダル表示）。

ユーザーの選択肢は「キャンセル」「強制作成」の 2 つ。追加警告なし（1 回の警告で十分）。

強制作成された Contact は status='primary' で新規 Person と共に作成され、後の cron で重複候補として再検出される。

将来的に、重複検知レベル（exact_match / possible_high / possible_mid / possible_low）を settings.py の DUPLICATE_WARNING_LEVEL で調整可能とする。デフォルトは possible_high。

#### 強制作成後のユーザー体験フロー

強制作成された Contact は、後の cron による重複チェックで再度 DuplicateCandidate として上がってくる。これは意図した挙動であり、ユーザーは「強制作成時には別件と判断したが、改めてレビュー画面で同一人物だったと気付いてマージする」「別人だったと改めて確定する」のいずれかの操作を後から行える。

強制作成時に特別なフラグを立てたり、警告履歴を保存したりする必要はない（補助レコードに過剰な情報を持たせない方針）。

### 11.4.5 別肩書追加画面（9 番 PersonAddAdditionalRoleView）

別肩書追加画面の処理は View 内で直書きする。詳細は第10章 10.12 を参照。

### 11.4.6 マージ実行時の処理（17 番 DuplicateCandidateGroupUpdateView）

#### POST 処理の流れ

1. POST データを `MergeForm` に渡してバリデーション（11.6 参照）
2. バリデーション通過後、`form.cleaned_data['review_result']` を取得
3. **review_result が different 系**（same_name / ocr_error / other_different のいずれかを含む）→ `Mark_as_Different_Person` を呼ぶ
4. **review_result が merged 系 + フィールド修正あり** → `Execute_Merge_with_Updates` を呼ぶ
5. **review_result が merged 系 + フィールド修正なし** → `Execute_Merge_Only` を呼ぶ
6. すべて完了後、PRG パターンで GET リダイレクト（17 番の URL に）

#### 「フィールド修正あり / なし」の判定

`form.confirmed_field_names()` または値違いの修正状態から判定する。具体的な実装は `MergeForm` 内のヘルパーメソッド（例：`form.has_field_updates()`）で表現する。実装の詳細は実装フェーズで決める。

#### 設計上の依存関係

- 3 つのサービスが 1 つの `MergeForm` に依存する
- `MergeForm` のフィールド変更は 3 サービスすべてに影響する
- View が form の cleaned_data を見て分岐するロジックを持つ
- これは「マージ画面の入口が 1 つで、結果に応じて 3 つのサービスに振り分ける」という業務構造から生じる必然的な依存

## 11.5 レビュー画面の動作（PRG パターン）

### 11.5.1 GET /duplicates/groups/<uuid:group_id>/（16 番、DuplicateCandidateGroupDetailView）

詳細画面。グループ全体の状態を表示する。POST 処理なし。

1. 当該グループの DuplicateCandidate を review_status ごとに集計（pending / merged / different_person）
2. invalidated は集計に含めない（マージで巻き込まれて自動無効化されたものはユーザーの意思ではない）
3. 集計結果を表示
4. Django messages（17 番からのリダイレクト直後）があれば併せて表示

#### 未レビュー候補がある場合の表示

- 候補ペア一覧を表示
- 「レビューを開始」ボタンで 17 番へ遷移

#### すべてレビュー完了の場合の表示

- 結果サマリーを表示（マージ件数、別人判定件数）
- マージされた Person 一覧、別人判定された候補一覧
- メッセージ表示用エリア（17 番からリダイレクトされた直後の場合）

### 11.5.2 GET /duplicates/groups/<uuid:group_id>/review（17 番、DuplicateCandidateGroupUpdateView）

レビュー画面。次のペアを表示する。

1. セッションの shown_pair_ids（表示済みペア ID リスト）を取得
2. 当該グループの review_status='pending' かつ shown_pair_ids に含まれない DuplicateCandidate を取得
3. **残ペアあり** → ペア画面表示、shown_pair_ids に当該ペア ID を追加
4. **残ペアなし、shown_pair_ids が空でない** → /duplicates/groups/<group_id>/ に GET リダイレクト + Django messages で完了メッセージ + shown_pair_ids クリア
5. **残ペアなし、shown_pair_ids も空** → /duplicates/ にリダイレクト

### 11.5.3 POST /duplicates/groups/<uuid:group_id>/review（17 番、DuplicateCandidateGroupUpdateView）

1. アクションを取得（review_result の値で判定：merged 系 / different_person 系）
2. **merged 系**：`Execute_Merge_Only` / `Execute_Merge_with_Updates` のいずれかを呼ぶ（フィールド修正の有無で分岐）
3. **different_person 系**：`Mark_as_Different_Person` を呼ぶ
4. shown_pair_ids に当該ペア ID を追加
5. 同じ URL（/duplicates/groups/<group_id>/review）に GET でリダイレクト（PRG パターン）

### 11.5.4 Django messages framework の使用

17 番から 16 番へのリダイレクト時の結果メッセージ表示は、Django 標準の `django.contrib.messages` を使用する。

- 17 番の処理内で `messages.success(request, "...")` のように記録
- 16 番のテンプレートで `{% if messages %}{% for message in messages %}...{% endfor %}{% endif %}` で表示
- メッセージは 1 回表示すると消える（Django messages framework の標準挙動）

URL パラメータやセッションを使った独自実装は避ける。

### 11.5.5 ペア表示画面の構成

画面は以下の構成。詳細な UI デザインは実装フェーズで調整する。

- 上部：グループ情報（rank、残り件数）
- 中央：左右並列表示。左 = 基準コンタクト（surviving 推奨）、右 = 候補コンタクト
- 各 Contact に「詳細を見る」ボタン（モーダルで詳細表示）
- 判定選択：「同じ人物」「別人として確定」「次の候補」のラジオボタン
- surviving 選択：「同じ人物」を選んだ場合のみ表示（デフォルト：左側）
- review_result 選択：複数選択可、merged 系と different_person 系で表示切替
- note 入力：other_* 選択時は必須
- 「決定」「キャンセル」ボタン

【設計案 A 対応追加項目】

- DUPLICATE_CHECK_FIELDS の各フィールド表示（surviving 側 / merged 側を並列）
- mid / low または値違いのフィールドにマークと修正・確認 UI（チェックボックス、merged 値採用ボタン、手入力編集）
- マージ理由選択時の動的 UI 切り替え（additional_role なら merged 側修正・確認 UI も追加表示）
- 確認必須フィールドがすべて確認済みになるまで、マージ確定ボタンは非活性

詳細な UI レイアウト・操作フロー（マージ理由選択を先か surviving 選択を先か、「全部一括で確認」ボタンの有無等）は実装フェーズでプロトタイプして決定する。

#### マージ画面の前提

マージ画面は情報密度が高い。PC 横長レイアウト（最低 1280px 幅）を前提とする。スマホ・タブレットでの最適化は v1.5.0 以降に送る。レイアウトは既存の app.css の BEM 命名規則に従い、新規クラスは app-merge-* prefix で定義する。

UI 操作のセッション扱い（マージ実行ボタン押下まで DB に反映されない、キャンセルで全変更が破棄される）については 8.5.4 を参照する。

### 11.5.6 マージ画面の 3 カラム設計

マージ画面は以下の 3 カラム構造とする。

- 左カラム：surviving 候補 1（基準コンタクト推奨側）
- 右カラム：surviving 候補 2
- 中央カラム：マージ後の Contact（編集可能）

操作フロー：

1. 判定選択：「同一人物（マージ）」「同一人物だが別肩書」「同性同名の別人」の 3 択
2. surviving 側の選択（左／右）
3. 中央フォームに選択側の値が初期値として入る
4. 各フィールドに confidence ラベル（low / mid / high）を表示
5. low / mid のフィールドは、修正または確認チェックボックス ON が必須（DUPLICATE_CHECK_FIELDS のみ対象）
6. 反対側から値をコピーしたい場合は、フィールド横の「→」ボタンで中央にコピー可能
7. notes フィールドは、surviving 側と merged 側の notes を結合した文字列が中央フォームの初期値として入る
8. 完了ボタンでマージ確定

### 11.5.7 マージ画面の表示対象フィールドの拡大

v1.4.1 では「DUPLICATE_CHECK_FIELDS のみ表示・修正対象」としていたが、v1.4.2 では Contact のほぼ全フィールドに拡大する。

理由：マージは破壊的操作であり、merged 側のフィールドが付け替え後どう扱われるかが曖昧なまま実装すると、ユーザーが意図しないデータ消失が起きる可能性がある。

表示対象（追加）：

- last_name / first_name / salutation_name
- fax / website / qualification / catchphrase
- twitter / instagram / github / linkedin / facebook
- notes
- postal_code / lang

これらは confidence 関係なし、値違いまたは片方空のフィールドのみ表示・選択対象。

## 11.6 Form クラス設計

### 11.6.1 Form クラス継承図

```
ContactBaseForm（抽象基底、Contact フィールドのみ）
├── ContactUpdateForm（12 番用：change_reason + ContactFieldConfidence の confirmed チェックボックス追加）
│   └── ContactUpdateActiveForm（13 番用：change_reason を除外、それ以外は親と同じ）
├── ContactAddAdditionalRoleForm（9 番用：Contact フィールドのみ）
├── ContactCreateForm（10 番用：手動で新規 Person + 新規 Contact 作成）
└── MergeForm（マージ画面 17 番用：merge_reason + 値違い確認 + 3 カラム構造のヘルパー）

MergeUndoForm（21 番用、独立クラス、ContactBaseForm を継承しない）
```

### 11.6.2 各 Form の責務

#### ContactBaseForm（抽象基底クラス）

- **責務**：Contact のフィールド定義のみを持つ抽象基底クラス。UI 構造は持たない
- **配置**：`contacts/forms.py`
- **継承元**：`forms.ModelForm`
- **Meta.fields**：Contact のユーザー入力対象フィールド（full_name / last_name / first_name / name_order / company / department / title / email / mobile / phone / fax / postal_code / address / branch / website / SNS各種 / notes / lang など）
- **除外フィールド**：`status` / `previous_status` / `previous_person` / `confirmed_at` / `confirmed_by` などシステムが管理する派生情報

#### ContactUpdateForm（12 番用）

- **責務**：プライマリーコンタクトの修正画面用。change_reason + ContactFieldConfidence の確認チェックボックスを動的に追加
- **配置**：`contacts/forms.py`
- **継承元**：`ContactBaseForm`
- **追加フィールド**：
  - `change_reason`（ChoiceField、PersonChangeReason の 5 値：fix / transfer / promotion / job_change / name_change）
  - `note`（CharField、required=False）
  - ContactFieldConfidence の確認チェックボックス（low/mid フィールドのみ動的追加）
- **メソッド**：
  - `clean()`：low/mid フィールドはすべて確認チェックボックス ON であることをバリデーション（11.7 参照）
  - `get_update_contact()`：フォーム値だけを持った新規 Contact インスタンス（pk なし）を返す
  - `confirmed_field_names()`：ユーザーが確認・編集したフィールド名のリストを返す（戻り値: `list[str]`）
- **`__init__` の引数**：`target_contact: Contact`（バリデーション時に既存 Contact の confidence 状態を参照するため必須、11.7 参照）

#### ContactUpdateActiveForm（13 番用）

- **責務**：アクティブ副コンタクトの修正画面用。`ContactUpdateForm` を継承し、change_reason フィールドを除外する
- **配置**：`contacts/forms.py`
- **継承元**：`ContactUpdateForm`
- **除外フィールド**：`change_reason`（親クラスから除外）
- **継承するフィールド**：`note`、ContactFieldConfidence の確認チェックボックス
- **継承するメソッド**：`clean()` / `get_update_contact()` / `confirmed_field_names()`（親クラスの実装をそのまま使用）
- **設計趣旨**：active 副コンタクトの修正は fix 相当の処理に固定。change_reason は不要

#### ContactAddAdditionalRoleForm（9 番用）

- **責務**：別肩書追加画面用。Contact フィールドのみ（追加項目なし）
- **配置**：`contacts/forms.py`
- **継承元**：`ContactBaseForm`
- **追加フィールド**：なし
- **メソッド**：`get_update_contact()`（フォーム値だけを持った新規 Contact インスタンスを返す）
- **`__init__` の引数**：`person: Person`（紐付ける Person を View から渡す）

#### ContactCreateForm（10 番用）

- **責務**：手動で新規 Person + 新規 Contact 作成画面用
- **配置**：`contacts/forms.py`
- **継承元**：`ContactBaseForm`
- **追加フィールド**：必要に応じて追加
- **メソッド**：`get_update_contact()`

#### MergeForm（マージ画面 17 番用）

- **責務**：マージ画面用。3 カラム構造、値違い確認、merge_reason / different_person reason の選択
- **配置**：`duplicates/forms.py`
- **継承元**：`ContactBaseForm`
- **追加フィールド**：
  - `review_result`（MultipleChoiceField、merged 系 7 値 + different 系 3 値）
  - `merge_reason`（ChoiceField、DuplicateMergeReason の 7 値、review_result が merged 系のときのみ有効）
  - `review_note`（CharField、required=False）
  - 値違い確認の選択肢（左カラム採用 / 右カラム採用 / 手入力）
- **メソッド**：
  - `clean()`：マージ用バリデーション（11.7 参照）
  - `get_update_contact()`：中央フォームの値だけを持った新規 Contact インスタンス（pk なし）を返す
  - `confirmed_field_names()`：ユーザーが確認・編集したフィールド名のリストを返す
- **`__init__` の引数**：`candidate: DuplicateCandidate`、`surviving_person: Person`、`merged_person: Person`（マージのコンテキストを View から渡す）

#### MergeUndoForm（21 番用、独立クラス）

- **責務**：マージ復元画面用
- **配置**：`duplicates/forms.py`
- **継承元**：`forms.Form`（`ContactBaseForm` は継承しない）
- **追加フィールド**：`undo_note`（CharField、required=False）

### 11.6.3 Form の設計原則

| 原則 | 内容 |
|---|---|
| Form は DB に触らない | `get_update_contact()` で Contact インスタンスを返すまで |
| Form は presentation 層 | パース・バリデーション・データ整形までが責務 |
| Model は永続層 | DB 書き込みはモデルメソッド経由 |
| 共通化しない | UI が違う Form は完全に別クラス |
| 共通モデルメソッドを使う | ContactFieldConfidence の更新等は共通メソッド経由 |
| 戻り値は新規 Contact インスタンス | pk なし、status / person 等は Form では設定しない |

### 11.6.4 抽象基底クラス導入の設計趣旨

Contact フィールド定義を 1 箇所に集約し、Contact 修正系・別肩書追加・新規作成・マージ画面の各 Form で**共通基底**として使う。Contact フィールドが追加・変更されたときの保守性を確保する。

- **抽象基底クラス `ContactBaseForm`**：Contact フィールド定義の共通化のみ。UI 構造は持たない
- **UI 構造（テンプレート、フォームレイアウト、特殊な表示処理）**：各子 Form クラスで独立に実装する

これにより、Contact フィールドが追加・変更されたときの保守性を確保しつつ、UI の柔軟性を保つ。

### 11.6.5 `form.get_update_contact()` の戻り値仕様

ユーザーが入力した値だけを持った **新規 Contact インスタンス（pk なし）** を返す。

**重要な設計判断**：

- pk は設定しない（メモリ上のインスタンスのみ）
- `status` や `person` の設定は **Form では行わない**（View またはサービス層が判断）
- 既存インスタンスを書き換えない（メモリ上の状態と DB 状態の乖離を起こさない）

**呼び出し側の責務**：

| 呼び出し画面 | View の処理 |
|---|---|
| 修正画面（12 番 / 13 番） | `change_reason` に応じて、既存 Contact への値反映（`contact.fix(form, user)` 経由）or 新規 Contact 作成を判断 |
| マージ画面（17 番） | サービス層（`Execute_Merge_with_Updates`）に渡し、サービス層内で適切に処理 |

**設計趣旨**：

Form は「ユーザー入力の整形」までが責務。「既存レコード上書きか新規追加か」の判断は Form ではなく View またはサービス層が行う。これにより、Form の責務を明確に保ち、再利用性を高める。

## 11.7 Form のバリデーション仕様

### 11.7.1 ContactUpdateForm.clean()

#### 責務

- low/mid confidence のフィールドはすべて確認チェックボックス ON であることをバリデーション
- バリデーション失敗時は `ValidationError` を発生させる

#### バリデーションロジックの方針

1. `target_contact`（`__init__` で受け取った既存 Contact）の `get_field_confidences()` を呼び、low/mid フィールドのリストを取得
2. フォームの確認チェックボックスのうち、low/mid フィールドに対応するものがすべて ON か確認
3. 1 つでも OFF があれば `ValidationError` を発生させる
4. エラーメッセージは「『〇〇』フィールドの確認チェックを ON にしてください」のような形式

#### `target_contact` をフォームに渡す方法

View から Form を生成する際、`__init__` の引数として既存 Contact を渡す：

`form = ContactUpdateForm(request.POST, target_contact=contact)`

`Form.__init__` 内で `self.target_contact = target_contact` として保持し、`clean()` から参照する。

実装の詳細は実装フェーズで決める。

### 11.7.2 ContactUpdateActiveForm.clean()

`ContactUpdateForm.clean()` を継承する。change_reason フィールドがない以外は同じバリデーションロジック。

### 11.7.3 MergeForm.clean()

#### 責務

マージ画面のバリデーション。以下を確認：

1. **review_result の整合性**：merged 系 / different_person 系の混在禁止。最低 1 つ必須
2. **other_* 選択時の note 必須**：review_result に other_merged / other_different が含まれるなら、review_note が必須
3. **merge_reason の必須性**：review_result が merged 系のときのみ merge_reason が必須
4. **DUPLICATE_CHECK_FIELDS の全 high 化**：surviving 側 Contact の DUPLICATE_CHECK_FIELDS（9 フィールド）が全 high であることを確認（マージ画面で修正・確認することで全 high 化、8.5）。additional_role の場合は merged 側 Contact も同条件
5. **値違いフィールドの確認済み**：surviving / merged で値違いがあるフィールドは、ユーザーが採用判断（左カラム採用 / 右カラム採用 / 手入力）を済ませていることを確認
6. **surviving_person の選択**：必須

バリデーション失敗時は `ValidationError` を発生させる。エラーメッセージはフィールドごとに表示される。

#### `candidate` / `surviving_person` / `merged_person` をフォームに渡す方法

View から Form を生成する際、`__init__` の引数として渡す：

`form = MergeForm(request.POST, candidate=candidate, surviving_person=surviving_person, merged_person=merged_person)`

`Form.__init__` 内で保持し、`clean()` から参照する。

### 11.7.4 設計趣旨

バリデーションを Form 側で行う理由：

- **Form は presentation 層**として、ユーザー入力の整形とバリデーションまでを担う
- **View は処理の流れの制御**に専念。バリデーション通過後にサービス層を呼ぶ
- **Model（contact.fix など）はバリデーション済みを前提**として動作。`contact.fix` 内で再度バリデーションを行わない

これにより：

- バリデーションロジックの重複を防ぐ（Form / View / Model のうち Form のみで行う）
- View / Model の責務が明確になる
- テストが書きやすい（Form のテスト、View のテスト、Model のテストが独立）

## 11.8 UI カスタムタグ・追加ルート・共通モーダル部品

UI 共通化のためのカスタムタグ 5 種・追加ルート 2 本・共通モーダル部品を提供する。

### 11.8.1 カスタムタグ一覧

| タグ | 引数 | 用途 |
|---|---|---|
| `{% card_image url size %}` | url, small/medium/large | 名刺画像表示（モーダル trigger 付き） |
| `{% original_image_thumbnail url %}` | url | 元画像サムネイル表示（モーダル trigger 付き） |
| `{% json_tree data %}` | JSON データ | JSON ツリー表示（json-view ラップ） |
| `{% confidence confidences field_name format %}` | confidences dict, field名, 表示形式 | フィールド単位の信頼度マーク |
| `{% contact_confidence contact format %}` | contact オブジェクト, 表示形式 | Contact 単位の信頼度サマリー |

### 11.8.2 カスタムタグの詳細

#### `{% card_image url size %}`

名刺画像をサムネイル表示する。size パラメータで small / medium / large を指定。出力 HTML には `js-image-modal-trigger` クラスと `data-image-url` 属性を自動で付与し、クリックで共通モーダルが開く。

#### `{% original_image_thumbnail url %}`

元画像のサムネイル表示。`{% card_image %}` と同様にモーダル trigger 自動付与。

#### `{% json_tree data %}`

JSON データをツリー表示する汎用タグ。内部で json-view ライブラリ（CDN 経由）に合わせた HTML と初期化 JS を出力する。

利用画面：

- OriginalDetailView（OriginalImage.raw_json の表示）
- CardDetailView（BusinessCard 関連の JSON 表示）

将来 json-view を別ライブラリに変えたくなっても、タグの内部だけ修正すれば全画面に反映される。

#### `{% confidence confidences field_name format %}`

フィールド単位の信頼度マーク表示。第 1 引数は `contact.get_field_confidences()` の戻り値（dict）。format で表示形式（icon / badge / count 等）を切り替え可能。

`get_field_confidences()` の戻り値が**全フィールド分の ContactFieldConfidence インスタンス**（high は疑似インスタンス）を返す設計のため、本タグは ContactFieldConfidence インスタンスの属性（confidence / confirmed_at 等）を直接参照して 3 状態を判定する（第10章 10.5.3 参照）。

#### `{% contact_confidence contact format %}`

Contact 単位の信頼度サマリー表示。Contact 全体で何個の low/mid フィールドが残っているか、何個が確認済みかを集計表示する。

### 11.8.3 共通モーダル部品

画像表示・コンタクト詳細表示等で使う共通モーダル部品。

- HTML 構造：base テンプレートに 1 箇所だけ定義
- JS 制御：app.js 内のモーダル制御コードで一元管理
- カスタムタグの `js-image-modal-trigger` クラスをクリックすると、`data-image-url` の URL を取得してモーダルに表示

### 11.8.4 BackNavigator 機能

画面遷移時の「前の画面に戻る」機能を実装する。

- テンプレートでは `append_back` タグ 1 つだけ使用
- クエリキーは View 側の `push_current(title, keys)` に隠し、エンコードは 1 リクエスト 1 回のみ（キャッシュ）
- テンプレートタグは 4 種：`append_back_url` / `back_url` / `back_all_url` / `hidden_back_field`

詳細は別途「BackNavigator 使い方ガイド」を参照。

---

# 第12章 重複チェックのバックグラウンド処理

## 12.1 実行頻度

check_duplicates 管理コマンドは cron で起動する。推奨頻度は 5 分間隔。実際の起動間隔は crontab で設定する。

crontab 例：`*/5 * * * * cd /path/to/project && python manage.py check_duplicates`

## 12.2 処理の単位

1 回の実行で処理する Contact 件数は、`--limit` オプションで指定可能。デフォルトは 100 件。

## 12.3 多重起動対策

select_for_update + skip_locked により、同じ Contact が複数の worker で同時処理されないようにする。

ロック取得できなかった Contact はスキップする。本番 DB（PostgreSQL）で動作。SQLite は skip_locked 未対応だが、SQLite の特性上、多重起動は実質的に起きない（書き込み時に DB 全体ロック）。

## 12.4 トランザクション

1 Contact ごとの重複チェック処理を transaction.atomic() で囲む。DuplicateCandidate 作成と duplicate_checked_at 更新を同一トランザクションで実行する。途中失敗時は全ロールバック。

## 12.5 stuck 検出

check_duplicates では stuck sweeper のような仕組みは不要。理由：処理が短時間（数秒）で完結する。中断された Contact は duplicate_checked_at が NULL のまま、次回の cron で自然に再処理される。

## 12.6 エラーハンドリング

個別 Contact の処理でエラーが発生した場合、その Contact のみスキップしてログに記録し、他の Contact の処理は続行する。

Contact に error フィールドは持たない。失敗した Contact は duplicate_checked_at が NULL のまま、次回 cron で再試行される。

## 12.7 Contact 編集時の処理

ContactUpdateView 内で明示的に処理する（Django signal は使わない）。

DUPLICATE_CHECK_FIELDS に含まれるフィールド（full_name、company、department、title、branch、email、phone、mobile、address）が編集された場合：

1. 当該 Contact が紐付く Person を特定
2. その Person を person_a または person_b に持つ DuplicateCandidate を抽出
3. review_status='pending' のものを 'invalidated' に変更
4. 当該 Contact の duplicate_checked_at を NULL に戻す
5. 次の cron で新しい DuplicateCandidate が生成される

review_status='merged' / 'different_person' のレコードはそのまま（過去の判定を尊重）。

### 12.7.1 12.7 が呼ばれる場面

12.7 が呼ばれる「Contact 編集」は、変更の規模に関係なく、Contact のフィールドが変わるすべてのケースが対象。

大規模な変更（実質的に新規コンタクト生成）：

- 転職・異動による所属変更（会社名・部署・役職の変更）
- 改姓・改名（フルネームの変更）
- 連絡先の総入れ替え（メール・電話の刷新）

小規模な変更（単純修正）：

- フィールドの入力間違い修正（typo、番地ミス等）
- 1 フィールドだけの値訂正

どちらの規模でも、Contact のフィールドが変わった以上、Person 全体の重複度の判定結果が変わる可能性がある。よって 12.7 では変更規模を問わず、Person に紐づく全 pending DuplicateCandidate を破棄（invalidated 化）して、次回 cron で再判定する設計とする。

実装側で「軽微な修正だから 12.7 の処理はスキップしてよい」という最適化はしないこと。フィールドが 1 文字でも変われば、重複度判定の結果が変わる可能性は理論上ゼロではない。

### 12.7.2 関数定義

`invalidate_pending_candidates(contact)`：12.7 専用ヘルパー。配置は `duplicates/services/merge_executor.py`。

処理内容：

1. contact が紐づく Person を特定
2. その Person を person_a または person_b に持つ DuplicateCandidate のうち review_status='pending' のものを invalidated に変更
3. contact.duplicate_checked_at = NULL に戻す

これらを呼び出し元のトランザクション内で実行する。

## 12.8 マージ実行時の DuplicateCandidate 処理（recover 一本化）

### 12.8.1 v1.4.2 の方針：recover 一本化

マージ実行時の DuplicateCandidate 処理は、値修正の有無を問わず**同じ recover 処理**を適用する。

【設計思想】

- 対象人物の同一性判定（スコア・ランク）は、Contact のフィールド値とは独立した指標
- recover でスコアコピーを行うことで、連続レビュー UX を維持
- 値修正による新規重複候補の発生は、duplicate_checked_at=NULL → 次回 cron で拾う

【役割分担】

- recover = merged_person 縁故の候補を復活（スコアコピー、即時）
- 次回 cron = 値修正で新規発生する重複候補を拾う（値修正ありの場合のみ duplicate_checked_at=NULL）

### 12.8.2 ⚠️ レビュアー注意：「スコアコピーはおかしい、再計算が必須」と思った人へ STOP

このセクションを読み始めて「スコアコピー？コンタクトの値が変わったのだから再計算が必要では？」と思ったレビュアー（人間・AI 問わず）へ。

その指摘は過去複数のレビュー（GPT レベル・Opus レベル含む）で繰り返し発生しており、すべて却下されている。Opus レベルのレビュアーでも 2 回連続で同じ誤解を起こした実績があるため、本警告を独立した見出しとして設置する。

設計判断として意図的にスコアコピーを採用している理由は、本節末尾の【スコアコピーが論理的に正しい理由】（12.8.4）を必ず読んでから判断すること。読まずに「これはバグだ」「論理的におかしい」と指摘しないこと。

### 12.8.3 recover 処理の手順

`recover_duplicate_candidates(merged_person, surviving_person)` は、マージ実行サービス（Execute_Merge_Only）から **mark_as_merged 後に呼び出される後処理**である。v1.4.2 の責務縮小により、recover 関数は「冪等性のための防御チェック」と「DuplicateCandidate の再復帰」を担い、Person / DuplicateCandidate の状態遷移そのものは呼び出し元（Execute_Merge_*）が事前に実行する前提とする。

1. **merged_person を含む他の pending DuplicateCandidate を invalidated 化**（A / B 以外の Person 集合を保持）
2. **当該マージの DuplicateCandidate の状態確認**（冪等性チェックのみ）：呼び出し元（Execute_Merge_*）が事前に `candidate.mark_as_merged(user, review_result, note)` を呼んでいる前提。recover 関数内では DuplicateCandidate を改めて 'merged' に変更する処理は行わない（呼び出し元責務）
3. **保持した DuplicateCandidate を再復帰**：
   - **`DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` クラスメソッド**で新規作成
   - score / rank / group_id は old_candidate からコピー（再スコア計算は不要）
   - merged_person だった側を surviving_person（new_surviving_person）に置き換え
   - review_status='pending' で作成
4. **surviving_person.duplicate_checked_at の更新は recover 関数では行わない**（呼び出し元 Execute_Merge_* の責務）。v1.4.2 改訂前は recover 内で更新していたが、責務分担の明確化のため呼び出し元に移管。

【再復帰の除外条件】 相手側 Person が active 以外（merged / archived）になっている場合は、当該 DuplicateCandidate は再復帰させない。

手順 3 の DuplicateCandidate 新規作成は、`merge_executor.py` 内で直接 `DuplicateCandidate.objects.create()` を呼ぶのではなく、`DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` クラスメソッド経由で行う。これにより「old_candidate からスコア・ランク・group_id 等をコピーして新規作成する」処理ロジックが DuplicateCandidate モデル側に集約され、関数名から意図が読める。

【設計思想】「DB 履歴を見る判断」を generate 側（および呼び出し元）に集約し、recover の責務は冪等性チェックと再復帰のみに絞る。これは X-3 ランナバグ修正で確定した generate_duplicate_candidates_for_contact 側への履歴参照集約（v0.1.5 詳細仕様書 §5.4.1 参照）と同じ思想である。

### 12.8.4 スコアコピーが論理的に正しい理由

具体例で説明する。

マージ前の状態：

- Person A、B、C、D がいる
- DuplicateCandidate に複数の pending レコード：
  - `(A, B, score=220, rank=possible_high, group_id=G1, pending)` ← マージ対象
  - `(B, C, score=150, rank=possible_mid, group_id=G2, pending)`
  - `(B, D, score=130, rank=possible_mid, group_id=G2, pending)`
- A の主コンタクト = ContactA、B の主コンタクト = ContactB、以下同様

マージ実行：「A vs B」をマージして A を surviving、B を merged にする（マージ理由 same_card、値修正なし）。

再復帰処理（recover）で発生すること：

- `(B, C)` `(B, D)` を invalidated 化
- 新たに `(A, C, score=150, rank=possible_mid, group_id=G2, pending)` を作成
- 新たに `(A, D, score=130, rank=possible_mid, group_id=G2, pending)` を作成
- score / rank / group_id は元のものをそのままコピー

**ここで誤解されやすいのが「surviving 側が B から A に変わったのだから、比較対象も変わってスコアも変わるはず」という直感である。**

確かに正確に計算するなら、新しい `(A, C)` のスコアは「ContactA vs ContactC」を比較した結果になる。これは 150 点とは限らない。

しかし、本仕様では計算をやり直さず、`(B, C)` のスコアをそのままコピーする。

**【中心ロジック】**

コンタクトの値がマージで変わっても、対象の人物が同一人物である可能性は変わらない。スコア・ランクは「2 つの Person が同一人物である可能性の指標」であり、人物そのものに紐づく値。Contact のフィールド値が修正されても、人物の同一性判定は変わらないため、スコアコピーが成立する。

**【連続レビュー UX の優先】**

「B を介した縁故」を活用した連続レビュー UX を優先する設計判断である。マージ実行直後、ユーザーは「B の周辺人物を整理する」モードに入っている。B と重複候補だった C や D を、B を統合した直後の A でもレビューさせることで、ユーザーは効率的に B の周辺人物を確認できる。

もし正確な再計算をして rank='none' になり候補から消えると、ユーザーは「あれ、C の候補が消えた」と感じ、レビューフローが途切れる。

スコアの絶対値は次回 cron で正確な値に補正される。本仕様では、UX（連続レビュー）を優先し、ランクの近似性で十分とする設計判断をしている。

**補助レコードに完璧な整合性を求めず、UX（連続レビューフロー）を優先する設計思想に基づく。**

**【値修正の有無を問わず適用】**

v1.4.2 では recover 一本化により、マージ画面での値修正の有無を問わず本処理（スコアコピー）を適用する。値修正があった場合でも、対象人物の同一性判定（スコア・ランク）は変わらないため、スコア流用が成立する。

### 12.8.5 UX への影響

レビュー継続性：マージ後も同 GID で連続レビューを継続できる（recover による）。値修正による「新規 Person との重複」検出は、最大 5 分の遅延を許容（次回 cron 待ち）。

## 12.9 判定ロジック変更時の全件再判定

スコア表・ランク判定・正規化ルール等の判定ロジックが変更された場合、recheck_duplicates 管理コマンドを実行することで全 Contact を再判定できる。

動作：

1. 全 active な Contact の duplicate_checked_at を NULL に戻す
2. 既存の pending な DuplicateCandidate を全削除
3. 次の cron で新しい DuplicateCandidate が生成される

### 12.9.1 運用想定

recheck_duplicates --all は以下の場面で実行する想定とする。

- 平常時：判定ロジック（スコア表・ランク判定・正規化ルール・代表メール判定リスト・DUPLICATE_CHECK_FIELDS など）が変更された後、変更を全 Contact に反映するため
- リリース時：v1.4.2 リリース直後に 1 回、既存 Contact の初期化のため

recheck_duplicates --all の処理自体は数秒で完了する（duplicate_checked_at を NULL に戻し、pending な DuplicateCandidate を削除するのみ）。実際の重複チェックは check_duplicates の cron が後から処理する。Contact 数が多い場合（例：5000 件で約 4 時間）、cron 処理の完了まで時間がかかるため、夜間バッチでの実行を推奨する。

## 12.10 重複チェックの実行ログ（ActionLog）

`Run_Generate_Duplicate_Candidates` 実行時に ActionLog にレコードを書き込む（`ActionLog.record(...)` 直接呼び）：

| 項目 | 値 |
|---|---|
| action | 'executed' |
| content_type | NULL |
| object_repr | 'check_duplicates'（管理コマンド名） |

data に以下を格納：

- search_target_count（duplicate_checked_at が NULL の Contact 総数）
- processed_count（実際に処理した件数、--limit で制限後）
- hit_contacts（候補が返った Contact 数）
- candidates_generated（DuplicateCandidate 生成数）
- rank_breakdown（exact_match / possible_high / possible_mid / possible_low の内訳）
- errors（エラーで処理失敗した Contact 数）
- duration_seconds（処理時間）
- status（success / partial / failed）

## 12.11 cron とマージ実行の並行性について

本仕様の重複チェックは cron で 5 分間隔の起動、マージ実行はユーザーがレビュー画面で操作する設計である。両者が同時刻に動作した場合、cron 側が古いスナップショットで DuplicateCandidate を生成し、マージ側が 12.8 の処理と競合する可能性が理論上存在する。

ただし、本システムは 1 人開発・社内利用規模を前提としており、月あたりのマージ操作が数十件程度、cron の 1 回の実行時間も数秒以内で完結することから、両者が同時刻に動作する確率は実運用上ほぼゼロに近い。

万一発生した場合も、12.7 の duplicate_checked_at = NULL による再処理ロジックにより、次回 cron で当該 Person ペアが再評価され、結果が自然に収束する。データ整合性が壊れることはない（DuplicateCandidate / PersonMergeLog は補助的な履歴レコードであり、Person・Contact の本体データには影響しない）。

そのため v1.4.2 では、cron とマージ実行の間に明示的なロック機構を設けない。本格的な認証・ロールベース権限・複数管理者運用が導入される v1.5.0 以降で、必要に応じて Person 単位のロック追加を再検討する。

【過去のレビュー指摘について】「cron とマージの並行実行で DuplicateCandidate にゴーストレコードが残る可能性があるのではないか」という指摘があった。本節は、その指摘に対する v1.4.2 の設計判断（運用規模を踏まえた割り切り）の根拠を仕様書として明示するために記載する。

## 12.12 将来の非同期化への配慮

v1.4.2 では cron + 管理コマンドによる同期処理だが、将来的に Celery などの非同期処理基盤への移行を念頭に設計する。

設計上の配慮：

- 重複判定ロジックは純関数化（duplicates/services/）
- 副作用処理は分離（duplicates/tasks/）
- 管理コマンドはオーケストレーションのみ
- 1 Contact ごとの処理は独立（並列実行可能）

移行時は、tasks/duplicate_check_runner.py を Celery タスクとして登録し、管理コマンドの呼び出し方を delay() に変更するだけで対応可能。

---

# 第13章 関数命名規則

## 13.1 関数の 3 分類

- **純関数**：DB を一切触らない、副作用なし、同じ入力で同じ出力
- **準関数**：DB を読むが書かない、外部世界に副作用なし
- **副作用あり関数**：DB 書き込み・例外送出・API 呼び出し・ファイル書き込み等

## 13.2 命名規則

### 13.2.1 プレフィックス（基本）

| プレフィックス | 性質 | 例 |
|---|---|---|
| normalize_* / to_* / calc_* / is_* / has_* | 純関数 | normalize_full_name / has_minimum_info |
| find_* / get_* / search_* / determine_* | 準関数 | find_duplicate_contacts / determine_base_person |
| validate_* | 副作用あり（例外） | validate_image |
| convert_* / save_* / create_* / update_* / delete_* | 副作用あり（変換・DB 書込） | convert_to_jpeg / save_card_image |
| run_* / process_* / send_* / execute_* / extract_* / generate_* | 副作用あり（複合処理） | run_ocr / Extract_Cards_via_OCR / generate_duplicate_candidates_for_contact |
| retry_* | 副作用あり（再投入） | retry_failed_ocr |

### 13.2.2 サービス層主要関数の命名規則（Pascal_Snake_Case）

View 層・cron・タスクから直接呼ばれる「処理フロー全体を担う主役関数」は `Pascal_Snake_Case` を使用する。

ルール：

- 各単語の最初の文字を大文字
- 単語間はアンダースコアで区切る
- 接続詞・前置詞（with / of / to / for / and / or / via 等）は小文字のまま

起動契機ごとの命名カテゴリ：

| カテゴリ | 起動契機 | 例 |
|---|---|---|
| `Execute_*` | View 層から（ユーザー操作起点） | `Execute_Merge_Only` / `Execute_Merge_with_Updates` / `Execute_Merge_Undo` |
| `Mark_as_*` | View 層から(状態遷移系) | `Mark_as_Different_Person` |
| `Run_*` | cron / タスク起動 | `Run_Generate_Duplicate_Candidates` / `Run_Crop_Cards_From_OriginalImage` / `Run_Process_CardImages_With_OCR` |

`Extract_*` カテゴリは v1.4.2 で廃止。旧 `Extract_Cards_via_OCR`（1 本パイプライン用上位関数）も廃止し、OpenCV と OCR を担う 2 本の `Run_*` 上位関数に分離した。詳細は §15.6 / §13.4.1 参照。

### 13.2.3 モジュール内専用ヘルパー関数の接頭辞

関数名の先頭に `_` を付けてモジュール内専用を示す。

例：`_calculate_score()` / `_determine_rank()`（duplicate_score.py 内）

### 13.2.4 アンダースコアの 2 用途

1. **i18n 翻訳関数のエイリアス**（14.2 参照）
   `from django.utils.translation import gettext_lazy as _`
   使用例：`_('氏名')`
2. **モジュール内専用ヘルパー関数の接頭辞**（13.2.3）
   関数名の先頭に `_` を付けてモジュール内専用を示す
   使用例：`_calculate_score()`

両者は構文上明確に区別される（前者は文字列リテラルを引数に取る、後者は関数定義）。

### 13.2.5 変数・引数の命名方針

変数名・引数名は省略しない。読み手が一瞬考えなくても意図が伝わる名前を選ぶ。

良い例：

- `surviving_contact`（マージで残る側の Contact）
- `confirmed_field_names`（ユーザーが確認・編集したフィールド名のリスト）
- `merged_person` / `surviving_person`

避ける例：

- `confidence_map`（何の confidence か考える必要がある）
- `c`（contact か何か不明）
- `data`（汎用すぎる）

略語は、業界・プロジェクト全体で確立されたもののみ許容する：

- `OCR` / `JSON` / `URL` / `FK` / `PK` / `UUID` 等
- これらはむしろ省略しない方が冗長になる

### 13.2.6 引数・戻り値が責務を語る

関数の引数と戻り値は、その関数の責務を雄弁に物語る。命名と引数の整合性は、設計の質を保つ重要な指標。

公開サービス（Pascal_Snake_Case）の特徴：

- 引数が多い（4〜6 個程度）
- 複数のドメインモデルを受け取る（Contact / Person / DuplicateCandidate / User）
- トランザクション境界を持つ
- 戻り値は新規作成された主要レコード（PersonMergeLog 等）または None

ヘルパー関数（snake_case / _snake_case）の特徴：

- 引数が少ない（1〜3 個程度）
- 1〜2 のドメインモデルを受け取る
- 単一の責務に集中
- 戻り値は処理結果（int / str / dict）または None

責務と引数の整合チェック：

- 引数が多すぎる関数（7 個以上）→ 責務分割を検討
- 引数が少ないのに公開サービス命名 → 命名カテゴリの見直し
- 引数の数だけ多くて何も返さない関数 → 副作用の塊。トランザクション境界を確認

### 13.2.7 モデルメソッド化の判断基準

第10章 10.2 / 10.3 を参照。

### 13.2.8 関数名は文章として読める

関数名は、関数の振る舞いが「文章として読める」ように選ぶ。`record_action(user)` のような曖昧な命名ではなく、`record_different_person_action(user)` のように何を記録するアクションかを関数名から読めるようにする。

例：

- `record_action(user)` ❌ 何を記録するか関数名から読めない
- `record_different_person_action(user)` ✅ 別人判定のアクションを記録することが関数名から読める
- `record_merge_action(user)` ✅ マージのアクションを記録する
- `record_undo_action(user)` ✅ 復元のアクションを記録する

## 13.3 docstring 性質明記

関数の docstring 冒頭に性質を明記する：[性質] 純関数 / 準関数 / 副作用あり、[入力]、[出力]、[例外]。

## 13.4 関数命名最終確定一覧

### 13.4.1 公開サービス（Pascal_Snake_Case）

| 関数名 | シグネチャ | 戻り値 | 配置 | 役割 |
|---|---|---|---|---|
| `Mark_as_Different_Person` | `(candidate, form, user)` | `None` | duplicates/services/merge_executor.py | 別人判定の本体 |
| `Execute_Merge_Only` | `(candidate, surviving_person, merged_person, form, user)` | `None` | duplicates/services/merge_executor.py | マージのみ（フィールド修正なし） |
| `Execute_Merge_with_Updates` | `(candidate, surviving_person, merged_person, form, user)` | `None` | duplicates/services/merge_executor.py | マージ＋更新（フィールド修正あり） |
| `Execute_Merge_Undo` | `(merge_log, form, user)` | `None` | duplicates/services/merge_executor.py | マージ復元の本体 |
| `Run_Generate_Duplicate_Candidates` | `(limit=100)` | （別ドキュメントで定義） | duplicates/tasks/duplicate_check_runner.py | タスク層上位関数。cron から呼ばれる |
| `Run_Crop_Cards_From_OriginalImage` | `(original_image)` | `None` | cards/tasks/crop_cards.py | OpenCV パイプライン上位（`process_opencv` cron 経由）。検出 → BC 作成 → OriginalImage.status=cards_extracted まで |
| `Run_Process_CardImages_With_OCR` | `()` | `None` | cards/tasks/ocr_runner.py | OCR パイプライン上位（`process_ocr` cron 経由）。BC を CAS で claim → `process_cardimage_with_ocr` 呼び出し |
| `process_cardimage_with_ocr` | `(business_card, ocr_service)` | `None` | cards/tasks/ocr_runner.py | BC 1 枚を引数に、OCR 実行 → BC 更新 → Contact / Person 生成 → OriginalImage.status 集計遷移までを完結（snake_case、Pascal_Snake_Case 主役関数の補助タスク扱い）|
| `extract_carddata_via_ocr` | `(card_image, ocr_service)` | `dict` | cards/tasks/ocr_runner.py | 1 枚の card_image に対して条件付き 2 回 OCR を実行し結果を辞書で返す純粋ラッパー（§15.6 参照） |

【v1.4.2 廃止】 旧 `Extract_Cards_via_OCR`（1 本パイプライン用上位関数）は廃止。`PipelineCoordinator` クラスおよび `process_pending` 管理コマンドも完全削除（§17 別表 B 参照）。

### 13.4.2 モジュール内専用ヘルパー（_snake_case）

- `_calculate_score(contact_a, contact_b)`：duplicate_score.py 内
- `_determine_rank(score, contact_a, contact_b)`：duplicate_score.py 内

### 13.4.3 サービス内共通（merge_executor.py、snake_case）

| 関数名 | 配置 | 役割 |
|---|---|---|
| `recover_duplicate_candidates(merged_person, surviving_person)` | duplicates/services/merge_executor.py | マージ後の DuplicateCandidate 後処理の唯一の関数 |
| `invalidate_pending_candidates(contact)` | duplicates/services/merge_executor.py | 12.7 専用（contact が紐づく Person の pending DuplicateCandidate を invalidated 化、`contact.duplicate_checked_at = NULL` も同時に更新） |

### 13.4.4 タスク層下位関数

- `generate_duplicate_candidates_for_contact(contact)`：duplicates/tasks/ 内

### 13.4.5 サービス層関数の責務一覧

| 関数名 | 性質と用途 |
|---|---|
| `find_duplicate_contacts(contact)` | 準関数。1 Contact について重複候補を検出 |
| `determine_base_person(person_a, person_b)` | 準関数。基準コンタクト判定（マージ数等） |
| `Mark_as_Different_Person(candidate, form, user)` | 副作用あり。別人判定（トランザクション内） |
| `Execute_Merge_Only(candidate, surviving_person, merged_person, form, user)` | 副作用あり。マージのみ実行（トランザクション内） |
| `Execute_Merge_with_Updates(candidate, surviving_person, merged_person, form, user)` | 副作用あり。マージ＋更新実行（トランザクション内） |
| `Execute_Merge_Undo(merge_log, form, user)` | 副作用あり。復元実行（トランザクション内） |
| `recover_duplicate_candidates(merged_person, surviving_person)` | 副作用あり。recover 処理 |
| `invalidate_pending_candidates(contact)` | 副作用あり。pending invalidated 化 |

正規化関数群（contacts/services/normalization.py）：

| 関数名 | 性質と用途 |
|---|---|
| `normalize_full_name(raw)` | 純関数。フルネーム正規化 |
| `normalize_company(raw)` | 純関数。会社名正規化 |
| `normalize_phone(raw)` | 純関数。電話番号正規化 |
| `normalize_email(raw)` | 純関数。メール正規化 |
| `normalize_address(raw)` | 純関数。住所正規化 |
| `normalize_postal_code(raw)` | 純関数。郵便番号正規化 |
| `normalize_to_contact_dict(raw_json)` | 純関数。raw_json → Contact 用辞書 |

## 13.5 Management Commands

| コマンド | オプション | 用途 |
|---|---|---|
| `process_pending` | `--limit` | cron 起動。pending 画像を OCR 処理 |
| `retry_failed_ocr` | `--all / --id / --limit / --dry-run` | failed を pending に戻す |
| `reconcile_card_images` | `--apply` | DB↔MEDIA_ROOT 整合検査・修復 |
| `dev_reset_ocr` | `--all / --id / --limit / --dry-run` | 開発用 OCR リセット（旧 `dev_for_reset_ocr` から改名） |
| `check_duplicates` | `--limit`（デフォルト 100） | cron 起動。重複チェック実行 |
| `recheck_duplicates` | `--all / --dry-run` | 運用用。判定ロジック変更後の全件再判定 |
| `dev_reset_duplicates` | `--all / --id / --limit / --dry-run` | 開発用重複チェックリセット（旧 `dev_for_reset_duplicates` から改名） |

## 13.6 設計思想の明文化

### 13.6.1 Form 渡し vs 引数渡しの判断基準

> **form の情報のうち、そのモデルが本来内包すべき情報がほぼ全てなら Form を渡してよい。そうでなければ、必要な値だけを引数で渡す。**

| メソッド | Form を渡す？ | 理由 |
|---|---|---|
| `contact.fix(form, user)` | ✅ 渡す | form の情報は Contact + ContactFieldConfidence で、ほぼ Contact が内包すべき情報 |
| `Person.set_primary_contact(new_contact, old_primary_new_status)` | ❌ 渡さない | 必要な値（new_contact、old_primary_new_status）だけを引数で渡す |

### 13.6.2 ユーザー入力は全 high で信頼する設計（3 ケース別）

詳細は第10章 10.6.4 参照。

### 13.6.3 アクティブ↔プライマリー入れ替え機能は実装しない

詳細は第9章 9.8 参照。

---

# 第14章 共通定数と TextChoices

## 14.1 配置場所

複数のアプリで共通利用する定数は config/constants.py に集約する。モデル固有の選択肢は、各モデルの内部クラスとして定義する。

## 14.2 TextChoices の採用

v1.4.0 では Django TextChoices で choices を統一する。v1.3.4 で既存の choices もすべて TextChoices に書き換える。表示名は gettext_lazy（`_()`）でラップして翻訳対応する。

## 14.3 共通定数（config/constants.py）

### 14.3.1 設計趣旨

本仕様では、PersonChangeReason・DuplicateMergeReason・DifferentPersonReason の 3 つを、それぞれ独立した TextChoices として定義する。

【独立定義とする理由】PersonChangeReason はコンタクト修正画面（UpdatePrimaryContactView）が起点となり、コンタクトのフィールド更新または新規コンタクト作成を行う際に使用する。一方 DuplicateMergeReason はマージ画面（DuplicateCandidateGroupUpdateView）が起点となり、2 つの Person を統合する際に使用する。

両者は、実世界で扱う事象（異動・昇進・転職・肩書追加・結婚等の名前変更など）には重なりがあるが、起点となる画面とユーザーが行う操作の文脈が異なる。コンタクト修正画面で「同一名刺（same_card）」「その他マージ（other_merged）」が選択肢として現れることはあり得ず、逆にマージ画面で「誤字訂正（fix）」が現れることもあり得ない。

【単一 TextChoices に統合しない判断】設計検討時、共通値の重複を避けるため、単一の TextChoices として定義し、フォーム側で表示する値を絞るアプローチも検討した。しかし以下の理由でこれを採用しなかった。

- 単一定義にすると、enum の中に「修正画面に存在しない値」「マージ画面に存在しない値」が並ぶことになり、コード上もユーザー操作の文脈でも誤解の温床になる
- 別 TextChoices として定義することで、型レベルで「修正画面で same_card は選べない」「マージ画面で fix は選べない」が保証され、実装ミスを防げる
- ユーザーに迷わせる原因をなくすという品質保証思想を優先する

共通値（transfer / promotion / job_change / name_change の 4 つ）の文字列定義が 2 箇所に存在することによる保守コストは認識しているが、ユーザー誤操作防止と実装堅牢性のほうを優先する判断とした。

【過去のレビュー指摘について】「PersonChangeReason と DuplicateMergeReason は値が重複しているのだから単一定義にすべきではないか」という指摘は過去のレビューで複数回受けている。本節は、その指摘に対する設計判断の根拠を仕様書として明示するために記載する。

### 14.3.2 PersonChangeReason

Contact 編集の修正理由（UpdatePrimaryContactView 専用）。

値（5 値）：fix / transfer / promotion / job_change / name_change

詳細は別表 C.7 参照。

`additional_role` は v1.4.2 で削除した（別肩書追加画面が独立画面 9 番に分離したため）。

### 14.3.3 DuplicateMergeReason

DuplicateCandidate.review_result の merged 系（マージ画面専用）。

値（7 値）：same_card / transfer / promotion / job_change / additional_role / name_change / other_merged

PersonChangeReason と 4 つの共通値（transfer / promotion / job_change / name_change）を持つが、独立した TextChoices として別定義する（14.3.1 参照）。詳細は別表 C.8 参照。

### 14.3.4 DifferentPersonReason

DuplicateCandidate.review_result の different_person 系。

値（3 値）：same_name / ocr_error / other_different

詳細は別表 C.9 参照。

### 14.3.5 DUPLICATE_CHECK_FIELDS

リスト形式の定数。重複検出のスコア計算、Contact 編集の発火判定で共通利用。

値：`['full_name', 'company', 'department', 'title', 'branch', 'email', 'phone', 'mobile', 'address']`（9 フィールド）

### 14.3.6 DUPLICATE_GENERIC_EMAIL_LOCALPARTS

代表メール判定の初期リスト。

値：`['info', 'contact', 'support', 'sales', 'admin', 'office', 'mail', 'inquiry', 'help', 'service', 'shop', 'customer', 'reception']`

## 14.4 モデル固有の TextChoices

各モデルの内部クラスとして定義する。

| クラス | 値 |
|---|---|
| Contact.Status | primary / active / inactive |
| Person.Status | active / merged / archived |
| OriginalImage.Status | pending / processing / opencv_processing / cards_extracted / extracted / garbage / failed |
| BusinessCard.Orientation | normal / rotate_90_cw / rotate_90_ccw / rotate_180 / mirror |
| BusinessCard.OcrStatus | pending / processing / done / failed |
| BusinessCard.OcrResult | business_card / not_business_card / insufficient_info / ocr_failed / others |
| DebugMask.MaskType | diff / edge / sat / or / closed |
| ContactFieldConfidence.Confidence | low / medium（high は記録対象外） |
| DuplicateCandidate.Rank | exact_match / possible_high / possible_mid / possible_low / none |
| DuplicateCandidate.ReviewStatus | pending / merged / different_person / invalidated |
| PersonMergeLog.Status | undoable / undone / locked |

---

# 第15章 OCR パイプライン

## 15.1 cards 処理ルール一覧

| ID | ケース | 処理 |
|---|---|---|
| C1 | cards 配列が空 | status=garbage、BusinessCard 作成なし |
| C2 | 全 card が is_business_card=false | status=garbage、BusinessCard 作成なし |
| C3 | 全 card が has_minimum_info で弾かれた | status=extracted（BusinessCard 0 件）、error_message に記録 |
| C4 | confidence=low/medium のフィールドあり | ContactFieldConfidence に記録（high は記録しない） |
| C5 | 切り抜き失敗（card_image=null） | has_minimum_info を満たせば BusinessCard 作成 |
| C6 | OCR 例外発生 | 当該 card のみ失敗扱い、他 card への波及なし |

## 15.2 部分失敗時の status / error_message

少なくとも 1 件の BusinessCard 作成成功 → status=extracted、error_message に失敗 card の理由を集約。全 card 失敗 → status=failed、error_message に詳細を記録。

## 15.3 OCR パイプラインの v1.4.0 修正範囲

v1.4.0 で OCR パイプラインに以下の変更を加えている。

1. cards/services/json_normalizer.py を廃止し、contacts/services/json_parser.py に移動・拡張
2. Contact 生成時に各フィールドで contacts/services/normalization.py の正規化関数を呼ぶ
3. pipeline_coordinator の Contact + Person 生成ロジックを変更（Person.primary_contact、Contact.status、Contact.created_by 等の設定追加）
4. OCR プロンプト（cards/prompts/extract_combined.txt）に lang 判定と postal_code 分離の指示を追加
5. JSON Schema を v1.4.0 にバージョンアップ（lang、postal_code フィールド追加）

## 15.4 新規 Contact 生成時の primary_contact 設定

OCR 由来および手動入力で新規 Contact を生成する際、以下のフローを 1 つのトランザクション内で実行する。

1. Person.objects.create(status='active') で新規 Person 作成
2. Contact.objects.create(person=person, status='primary', created_by=user, ...) で新規 Contact 作成
3. person.primary_contact = contact、person.save() で primary_contact を更新

循環 FK（Person ↔ Contact）が発生するが、トランザクション内で 3 段階に分けて実行することで整合性を保つ。

## 15.5 正規化ルール

### 15.5.1 正規化の方針

Contact のフィールドは、生成時に正規化済み値で保存する。OCR 由来の Contact（OriginalImage 経由）も、手動入力の Contact も、同じ正規化関数を通すことで結果が一致するように設計する。

生の値（OCR が読み取った素のテキスト）は OriginalImage.raw_json に残るため、Contact フィールドには正規化済み値のみを保存する。

### 15.5.2 正規化関数の配置

contacts/services/normalization.py に各フィールドの正規化関数（純関数）を配置する。

contacts/services/json_parser.py（v1.3.4 の json_normalizer.py を移動・拡張）が、raw_json から Contact 用辞書を生成する際に、各フィールドで normalization の関数を呼ぶ。

ContactCreateView / ContactUpdateView も同じ normalization の関数を呼ぶことで、入力経路によらず結果が一致する。

### 15.5.3 各フィールドの正規化ルール

#### フルネーム（full_name）

- 全角空白 → 半角空白に統一
- 半角空白を除去（空白なしで比較）
- 全角英数字 → 半角英数字
- 前後の空白除去
- 正規化後に空文字となった場合は `ValidationError` を raise する（保存・更新前バリデーション、§4.4.1 の必須フィールド制約と整合）

#### 会社名（company）

- 「株式会社」「(株)」「㈱」「（株）」を統一表記に変換
- 前後位置の差は吸収しない（前株/後株は別会社扱い）
- 全角空白・半角空白を除去
- 全角英数字 → 半角英数字
- 前後の空白除去

#### 携帯番号 / 会社代表電話 / FAX（mobile / phone / fax）

- 数字とハイフンのみ抽出（その他の文字を除去）
- ハイフン除去（数字のみで保存・比較）
- 全角数字 → 半角数字
- 国番号正規化（+81 → 0、81 → 0）
- 漢数字 → 半角数字に変換

表示時は Contact.lang に応じて UI 側でハイフン整形を行う。

#### メールアドレス（email）

- 全体を小文字化
- 前後の空白除去

#### 住所（address）

- 全角空白・半角空白を除去
- 全角英数字 → 半角英数字
- 漢数字 → 半角数字に変換
- 「丁目」「番地」「号」を「-」に変換
- ハイフン統一（−／―／－ → -）
- 郵便番号は postal_code フィールドに分離（address には含めない）
- 建物名は address に含める（v1.4.0 では分離しない、v1.5.0 以降で検討）
- 前後の空白除去

【正規化の限界】日本語住所の表記揺れは大きく（「1-2-3」「一丁目二番地三号」「1 丁目 2-3」など）、本ルールで完全に同一表記に揃わないケースが残る。重複検出のスコア表で住所一致は +10 点と低く設定しているのは、この揺れを織り込んだ重みである。住所のみで重複を判定することはなく、フルネーム・メール・携帯との組み合わせで判定する設計のため、住所の表記揺れが致命的な誤判定を生むことはない。

#### 郵便番号（postal_code）

- 数字のみで保存（ハイフン除去）
- 全角数字 → 半角数字

表示時は Contact.lang に応じて UI 側でハイフン整形を行う（例：lang='ja' なら 1070052 → 〒107-0052）。

#### 部署 / 役職 / 支店（department / title / branch）

- 全角空白・半角空白を除去
- 全角英数字 → 半角英数字
- 前後の空白除去

#### lang

OCR が判定した名刺の主要言語を ISO 639-1 形式で保存。デフォルトは 'ja'。OCR で判定不能な場合は 'ja'。

値の例：ja / en / zh / ko / other

#### 正規化対象外フィールド

- notes（自由記述メモ、重複検出にも使用しない）
- catchphrase（キャッチフレーズ）
- qualification（資格）
- website / SNS 各種

## 15.6 条件付き 2 回 OCR

v1.4.2 のパイプライン分離（§13.4.1 参照）に伴い、1 BC につき orientation に応じて条件付きで 2 回 OCR を実行する仕様を導入する。`extract_carddata_via_ocr(card_image, ocr_service)` が 1 枚の card_image に対して以下のフローで処理する。

### 15.6.1 処理フロー

1. 1 枚の card_image を `extract_carddata_via_ocr(card_image, ocr_service)` に渡す
2. 1 回目 OCR を実行 → 結果から `orientation` を取得し `raw_json_1` に格納
3. `orientation == 'normal'` → 1 回で完結（`raw_json_2 = None`）
4. `orientation != 'normal'` → `_rotate_card_image()` で補正 → 2 回目 OCR → `raw_json_2` に格納
5. 1 回目／2 回目とも同じプロンプト（フィールド抽出 + orientation 判定を同時に実施）
6. 2 回目失敗時は 1 回目結果を採用（`raw_json_2 = None`、`error_message` に「2 回目 OCR 失敗」を追記、`ocr_status = done`）
7. 1 回目自体失敗時：`ocr_status = failed` / `ocr_result = ocr_failed`、`retry_failed_ocr --ocr` で差し戻し可能（§17 別表 B 参照）

### 15.6.2 採用 raw_json の選択ロジック

| 場面 | 採用 raw_json |
|---|---|
| 2 回目成功時 | `raw_json_2` を採用（補正後の OCR 結果） |
| 2 回目スキップ時（orientation=normal）/ 2 回目失敗時 | `raw_json_1` を採用 |

### 15.6.3 card_image 上書き保存ルール

- 2 回目が走った時点で **必ず補正画像で BC.card_image を上書き**（2 回目が失敗しても、画像補正自体は実施済みのため上書き）
- 既存 `BusinessCard.orientation` フィールドは「**検出時の元の orientation**」を保存（補正ログとして残す）

### 15.6.4 orientation → 補正回転マップ

| orientation 値 | 補正操作 |
|---|---|
| `normal` | 何もしない |
| `rotate_90_cw` | 反時計回り 90°（Pillow `image.rotate(90, expand=True)`） |
| `rotate_90_ccw` | 時計回り 90°（Pillow `image.rotate(-90, expand=True)`） |
| `rotate_180` | 180° 回転（Pillow `image.rotate(180, expand=True)`） |
| `mirror` | 水平反転（Pillow `ImageOps.mirror(image)`） |

mirror は他 orientation と同じパイプラインで処理するが、業務的に「誤認識ケース」の扱いであり、補正後の精度は保証しない。`cv2.flip` ではなく Pillow `ImageOps.mirror` を採用する理由は、回転処理と同じ Pillow API で揃えることで実装の単純さを優先するため。

### 15.6.5 設計趣旨

- AI OCR は名刺が回転していると読み取り精度が極端に落ちるため、補正後再 OCR で精度を引き上げる
- 1 回目／2 回目両方の生 JSON を BC に保存（`raw_json_1` / `raw_json_2`、§4.3 参照）することで、回転補正の効果測定・プロンプトチューニング材料がそのまま蓄積される
- 1 回目で `orientation=normal` のときは 2 回目スキップで API トークンコストを節約

---

# 第16章 重複検出の動作シナリオ

## 16.1 シナリオ別の判定結果

代表的なシナリオでの重複判定結果を以下に示す。

| シナリオ | 判定 | 備考 |
|---|---|---|
| 同じ名刺の撮り直し | exact_match | 全項目一致、自動マージ候補 |
| 転職（携帯のみ継続） | possible_mid | 携帯+フルネーム一致、メール変更 |
| 個人事業主→法人化 | possible_high | メール+携帯+フルネーム継続、会社名変更 |
| 部署異動（メール・携帯継続） | possible_high | 全項目一致するが部署が変わるため exact_match から外れる |
| 同会社の同僚（同姓同名なし） | none | フルネーム一致なしで候補に上がらない |
| 同姓同名（フルネームのみ一致） | possible_low | 合計 40 点、フルネーム一致のため候補に上がる |
| 同姓同名 + 同会社 | possible_low | フルネーム+会社等で 40 点以上 |
| 転職（メールのみ継続） | possible_mid | メール+フルネーム一致 |
| 転職（フルネームのみ一致） | possible_low | 合計 40 点、フルネーム一致のため候補に上がる |
| 退職者メール引き継ぎ（フルネーム違い） | none | フルネーム一致なし |
| 派遣携帯使い回し（フルネーム違い） | none | フルネーム一致なし |

## 16.2 判定結果の意味付け

各ランクは「同一人物の確信度」を表す。

| ランク | 意味 |
|---|---|
| exact_match | ほぼ確実に同一人物（同じ名刺の重複取り込み） |
| possible_high | ほぼ同一人物（個人特定の証拠 + 状態変化の可能性） |
| possible_mid | 同一人物の可能性が高い（強い証拠が 1 つ） |
| possible_low | 同姓同名の可能性あり、要慎重確認 |

---

# 第17章 通知・運用設計

## 17.1 表示方針

重複チェックは本業ではない。派手なアラート・通知でユーザーの本業を妨げないよう、表示は控えめにする。

具体的には、名刺一覧画面の上部に件数を控えめに表示し、該当画面へのボタンを設置する。詳細な UI デザインは実装フェーズで調整する（仕様書には方針のみ記載）。

## 17.2 担当者の管理

DuplicateCandidate.assigned_to は、当該重複チェックの起点となった Contact のアップロードユーザー（または Contact.created_by）が自動設定される。

OCR 由来の Contact：BusinessCard.original_image.user

手動入力の Contact：Contact.created_by

### 17.2.1 担当者の自動割り当ての設計趣旨

担当者は「名刺をアップロード・作成した本人が、自分の取り込んだデータの重複レビューに責任を持つ」という運用前提で自動設定される。本人が自分の取引先や顧客との関係を知っているため、同一人物か別人かの判断精度が最も高いという考え方である。

アップロード担当者と重複レビュー担当者を分けたい運用要件は v1.4.2 では非サポート。v1.5.0 以降のロールベース権限導入時に「担当者の手動再割り当て」「特定ロールへのデフォルト割り当て」機能を検討する。

## 17.3 KPI 評価への活用（将来）

v1.4.2 では実装しないが、将来的に DuplicateCandidate.assigned_to と review_status を集計して、担当者ごとの処理率を KPI として活用することを想定する。具体的な実装は v1.5.0 以降で検討。

ActionLog（第4章 4.10、4.11 参照）にマージ実行・別人判定・cron 実行等を記録しているため、これらを集計することで担当者ごとの処理率・各操作の所要時間・ロジック変更の影響などを分析できる。

## 17.4 メール通知（将来）

v1.4.2 ではメール通知機能を実装しない。将来的に「新しい重複候補が割り当てられた」「期限超過」等の通知をメールで送る機能を v1.5.0 以降で検討する。

---

# 第18章 認証・権限

## 18.1 v1.4.2 の認証状態

v1.3.4 と同様、認証は仮実装の状態を継続する。cards.views.get_current_user() がデフォルトユーザーを返す。

Phase 4（v1.5.0 以降）で LoginRequiredMixin による本格認証を導入予定。

## 18.2 マージ実行権限

v1.4.2 では、マージ実行に対する明示的な権限制約は設けない。ログイン済みユーザーなら誰でもマージ可能（仮認証下では実質的に誰でも可能）。

v1.5.0 以降の認証本格導入時に、ロールベース権限（管理者のみマージ実行可能、等）を導入する。

## 18.3 CSRF・画像アップロード

Django 標準の CSRF 保護を継続。画像アップロードはバリデーション必須（image_processor.validate_image を UploadForm.clean_image() 経由で呼ぶ）。

---

# 第19章 非機能要件

## 19.1 性能要件

- 画像アップロード → 名刺一覧表示まで：画像 1 枚あたり 30 秒以内
- OCR API 1 回あたりのタイムアウト：60 秒
- process_pending の cron 起動間隔：1〜5 分
- stuck sweeper のしきい値：30 分（OCR 処理）
- check_duplicates の cron 起動間隔：5 分（推奨）
- 1 Contact の重複チェック処理時間：数秒以内

## 19.2 信頼性要件

- 多重起動対策：CAS 楽観ロック方式（OCR）、select_for_update + skip_locked（重複チェック）
- 異常終了対策：stuck sweeper（OCR）、duplicate_checked_at 自動再処理（重複チェック）
- 整合性検査：reconcile_card_images コマンドによる定期監視
- ActionLog 書き込み失敗時のフォールバック：ファイルログへの障害記録（第4章 4.11.4 参照）

## 19.3 設定値一覧

| 設定キー | 既定値 | 用途 |
|---|---|---|
| OCR_STUCK_THRESHOLD_MINUTES | 30 | OCR の stuck sweeper しきい値（分） |
| ANTHROPIC_API_KEY | (必須) | Claude API キー |
| MEDIA_ROOT | (必須) | 画像ファイル保存先 |
| DUPLICATE_GENERIC_EMAIL_LOCALPARTS | 13 値の初期リスト | 代表メール判定リスト |
| DUPLICATE_WARNING_LEVEL | possible_high | Contact 手動作成時の重複警告レベル（v1.5.0 以降で本格活用） |
| POSSIBLE_LOW_MIN_SCORE | 40 | possible_low ランクの下限スコア |
| POSSIBLE_MID_MIN_SCORE | 120 | possible_mid ランクの下限スコア |
| POSSIBLE_HIGH_MIN_SCORE | 200 | possible_high ランクの下限スコア |

スコア表とランク閾値は config/constants.py で管理する。運用後にチューニング可能とするため、定数化された設計とする。

---

# 第20章 制約事項・将来の拡張

## 20.1 v1.4.2 の制約事項

- OCR 処理の起動ユーザーアクションは画像アップロードのみ
- 失敗した OriginalImage の retry 機能はユーザーに提供しない
- OriginalImage.status に processing 状態（CAS 中間状態）
- ファイル保存方式は DB 先・ファイル後（on_commit でリネーム）
- Claude API タイムアウトは 60 秒固定
- 画像アップロード上限：5MB / JPEG・PNG のみ
- 重複チェックの比較対象は主コンタクト（status='primary'）同士のみ
- 副コンタクト・非アクティブ・archived は重複チェック対象外
- マージ実行は必ず DuplicateCandidate 経由（直接マージ不可）
- マージ復元は 1 段階前まで（多重マージは locked になる）
- different_person 判定後の自動再判定は行わない
- アクティブ↔プライマリー入れ替え機能は実装しない（第9章 9.8 参照）
- Person の archived 化は Django Admin のみ（一般ユーザー UI は v1.5.0 以降）
- 物理削除は一般ユーザー UI なし、Django Admin のみ
- 認証は仮実装、本格認証は v1.5.0 以降
- Contact 一覧画面は実装しない（Person 一覧と名刺一覧で代替）
- Person 編集画面は実装しない（Person 自体に編集対象が少ない）
- KPI レポート、メール通知、due_date による期限管理は v1.5.0 以降
- マージ画面のスマホ対応は v1.5.0 以降（v1.4.2 では PC 横長 1280px 前提）
- 副コンタクト同士の重複検知、副コンタクト整理機能は v1.5.0 以降

## 20.2 v1.5.0 以降で予定する変更

### 認証・権限

- 認証の本格導入（LoginRequiredMixin / 適切なユーザーモデル）
- ロールベース権限（マージ実行は管理者のみ等）

### archived Person 関連

- archived Person の UI（archived 化ボタン、復活ボタン、検索フィルタ）

### 重複検出の拡張

- 手動 DuplicateCandidate 作成機能（is_manual フィールドの追加が必要）
- 重複検知レベルの設定機能（DUPLICATE_WARNING_LEVEL の動的変更）
- 重複候補のメール通知
- 副コンタクト同士の重複検知
- 副コンタクト整理機能（inactive 一括変更機能）
- different_person 判定の管理コマンド経由での救済機能

### KPI・運用

- KPI レポート画面（担当者ごとの処理率）
- ActionLog のデータ分析画面（KPI ダッシュボード）
- ActionLog の対象拡大（OriginalImage アップロード、Contact CRUD、Person 等）
- ActionLog の本格的な UI
- ActionLog 書き込みの抽象基底クラス・ミックスイン化（複数モデルで使い回し）
- due_date による期限管理
- 期限超過候補の通知・強調表示
- 要コンタクト情報確認画面（ContactFieldConfidence の確認 UI）

### OCR・画像処理

- HEIC 受付対応・最大ファイルサイズの引き上げ
- Claude API タイムアウトを settings.py の設定値化
- 住所の建物名分離

### マージ画面の改善

- 値違い時の「採用理由」の個別記録（OCR 誤認識・名刺改版・実世界の変化など、フィールド単位で記録）
- マージ画面の操作履歴の詳細記録（ContactFieldConfidence の confirmed_by / confirmed_at 活用）
- マージ画面のスマホ対応
- primary 切り替え以外の主たる業務指定機能（複数 active コンタクトでの優先順位指定）

### 管理コマンドの拡張

- recheck_duplicates への --id / --limit 追加（部分再判定）

## 20.3 v2.0.0 以降で予定する拡張

- OCR バックエンドの切替対応（GPT-4o / Vision / EasyOCR）
- 副コンタクトを含む高度な重複検出（v1.4.2 では主コンタクトのみ）
- OriginalImage / BusinessCard の削除機能（UI 提供）
- 再 OCR 設計（raw_json 上書きなし）
- SNS 別テーブル化
- Celery による非同期化（重複チェック・OCR）
- 案件管理・メール配信・スケジュール管理・設備予約への発展

---

# 第21章 Phase 4 実装スコープ

## 21.1 v1.4.2 で実装するファイル一覧

| # | ファイル | 用途・変更内容 |
|---|---|---|
| 1 | config/constants.py | 共通 TextChoices、DUPLICATE_CHECK_FIELDS、DUPLICATE_GENERIC_EMAIL_LOCALPARTS、ランク閾値定数等 |
| 2 | persons/models.py | Person モデル + モデルメソッド（mark_as_merged / transfer_contacts_to / set_primary_contact / get_active_contacts / get_inactive_contacts / get_active / get_archived） |
| 3 | persons/views.py | PersonListView、PersonDetailView、PersonAddAdditionalRoleView |
| 4 | contacts/models.py | Contact モデル + モデルメソッド（fix / get_field_confidences / get_high_fields / is_all_field_confidence_high）、ContactFieldConfidence モデル + モデルメソッド（get_for_contact / create_for_contact / mark_fields_as_confirmed） + 防御策（CheckConstraint / save() オーバーライド） |
| 5 | contacts/views.py | ContactCreateView、ContactDetailView、UpdatePrimaryContactView、UpdateActiveContactView、PreviewContactView |
| 6 | contacts/forms.py | ContactBaseForm、ContactUpdateForm、ContactUpdateActiveForm、ContactAddAdditionalRoleForm、ContactCreateForm |
| 7 | contacts/services/normalization.py | フィールド正規化（純関数群） |
| 8 | contacts/services/json_parser.py | raw_json → Contact 用辞書（v1.3.4 の json_normalizer から移動・拡張） |
| 9 | duplicates/models.py | DuplicateCandidate モデル + モデルメソッド（get_pending / get_merged / get_different_person / get_invalidated / has_duplicates / get_by_group / create_recovered_from / mark_as_merged / mark_as_different_person / record_different_person_action）、PersonMergeLog モデル + モデルメソッド（create / lock_past_logs / get_for_person / get_undoable / is_undoable / mark_as_undone / record_merge_action / record_undo_action / get_undo_preview）、ActionLog モデル + モデルメソッド（record） |
| 10 | duplicates/views.py | DuplicateCandidateGroupListView、DuplicateCandidateGroupDetailView、DuplicateCandidateGroupUpdateView、PersonMergeLogListView、PersonMergeLogDetailView、PersonMergeLogConfirmUndoView |
| 11 | duplicates/forms.py | MergeForm、MergeUndoForm |
| 12 | duplicates/services/duplicate_detection.py | find_duplicate_contacts、_calculate_score、_determine_rank、determine_base_person |
| 13 | duplicates/services/merge_executor.py | Mark_as_Different_Person、Execute_Merge_Only、Execute_Merge_with_Updates、Execute_Merge_Undo、recover_duplicate_candidates、invalidate_pending_candidates |
| 14 | duplicates/tasks/duplicate_check_runner.py | generate_duplicate_candidates_for_contact、Run_Generate_Duplicate_Candidates |
| 15 | cards/management/commands/check_duplicates.py | cron 起動。Run_Generate_Duplicate_Candidates 呼び出し |
| 16 | cards/management/commands/recheck_duplicates.py | 全 Contact の duplicate_checked_at リセット |
| 17 | cards/management/commands/dev_reset_duplicates.py | 開発用 DuplicateCandidate リセット |
| 18 | cards/management/commands/dev_reset_ocr.py | 開発用 OCR リセット（旧 dev_for_reset_ocr から改名） |
| 19 | cards/templatetags/back_tags.py | BackNavigator 用カスタムタグ（append_back_url / back_url / back_all_url / hidden_back_field） |
| 20 | cards/templatetags/ui_tags.py | UI カスタムタグ（card_image / original_image_thumbnail / json_tree / confidence / contact_confidence） |
| 21 | static/css/app.css | UI 全体のスタイル（マージ画面用 app-merge-* prefix のクラス追加） |
| 22 | static/js/app.js | 共通モーダル制御、BackNavigator JS、マージ画面の JS |
| 23 | templates/contacts/ ほか | 各画面のテンプレート（base.html、各 View 用テンプレート、共通モーダル部品） |

`duplicates/services/merge_helpers.py` は v1.4.2 で全削除する（モデルメソッド化により不要）。

## 21.2 削除対象

| 旧ファイル | 削除理由 |
|---|---|
| duplicates/services/merge_helpers.py | モデルメソッド化により不要 |
| cards/services/json_normalizer.py | contacts/services/json_parser.py に移動・拡張 |

## 21.3 v1.4.0 → v1.4.2 で改名された関数の対応表

実装中にコード君が「以前のバージョンで見た関数名がない」と迷わないよう、改名一覧を記録する。

| 旧関数名 | 新関数名 | 理由 |
|---|---|---|
| `execute_merge` | `Execute_Merge_Only` / `Execute_Merge_with_Updates`（2 つに分割） | マージのみとマージ＋更新の責務分離 |
| `regenerate_duplicate_candidates` | （削除） | recover 一本化により不要 |
| `run_pipeline` | `Extract_Cards_via_OCR` | Pascal_Snake_Case 命名規則 + Extract_* カテゴリ |
| `run_duplicate_check_for_contact(contact_id)` | `generate_duplicate_candidates_for_contact(contact)` | generate_* プレフィックス + 引数を Contact インスタンスに変更 |
| `Run_Generation_of_Duplicate_Candidates_for_Contacts(limit=100)` | `Run_Generate_Duplicate_Candidates(limit=100)` | 命名短縮（39 文字 → 33 文字）+ generate ベースで命名対称性 |
| `dev_for_reset_ocr` | `dev_reset_ocr` | for を削除して簡潔化 |
| `dev_for_reset_duplicates` | `dev_reset_duplicates` | for を削除して簡潔化 |
| `record_action_log` | `merge_log.record_merge_action(user)` / `merge_log.record_undo_action(user)` / `candidate.record_different_person_action(user)` / `ActionLog.record(...)` | モデルメソッド化（PersonMergeLog / DuplicateCandidate のインスタンスメソッド + ActionLog のクラスメソッド） |
| `create_merge_log` | `PersonMergeLog.create(surviving_person, merged_person, user)` | クラスメソッド化 |
| `update_person_status_to_merged` | `Person.mark_as_merged(surviving_person)` | インスタンスメソッド化 |
| `lock_past_merge_logs` | `PersonMergeLog.lock_past_logs(merged_person)` | クラスメソッド化 |
| `mark_candidate_as_merged` | `candidate.mark_as_merged(user, review_result, note)` | インスタンスメソッド化 |
| `apply_field_decisions`（rev3 で検討、rev4 で削除） | （削除、Form クラスへ移行） | Form クラス活用方針により不要 |
| `field_decisions`（概念名） | `form.get_update_contact()` / `form.confirmed_field_names()` | 抽象概念から実装上の具体名へ |
| `form.confirmed_fields`（属性） | `form.confirmed_field_names()`（メソッド、戻り値: list[str]） | 戻り値型を明確化 |
| `record_action(user)` | `record_different_person_action(user)` / `record_merge_action(user)` / `record_undo_action(user)` | 命名から記録対象が読めるよう明確化（13.2.8 の命名思想） |

## 21.4 旧画面・URL の廃止対応

| 旧 | 新 | 理由 |
|---|---|---|
| `/contacts/<uuid:pk>/update/`（ContactUpdateView） | `/contacts/<uuid:pk>/update-primary/`（UpdatePrimaryContactView） | primary 専用であることを URL に明示 |
| `/contacts/<uuid:pk>/update/`（ContactUpdateView の active 用途） | `/contacts/<uuid:pk>/update-active/`（UpdateActiveContactView、新規） | active 副コンタクトの修正画面を独立化 |
| `/duplicates/groups/<uuid:group_id>/`（DuplicateCandidateGroupView、GET / POST 両対応） | `/duplicates/groups/<uuid:group_id>/`（DuplicateCandidateGroupDetailView、GET のみ）+ `/duplicates/groups/<uuid:group_id>/review`（DuplicateCandidateGroupUpdateView、GET / POST） | 詳細表示とレビュー処理を URL レベルで分離 |
| `/duplicates/groups/<uuid:group_id>/result/`（DuplicateCandidateGroupResultView） | （廃止） | 17 番のリダイレクト先を 16 番に変更したため不要 |

## 21.5 マイグレーション順序

v1.4.2 のマイグレーション適用は以下の順序で行う。

1. config/constants.py の TextChoices 定義を追加・更新
2. ContactFieldConfidence モデルに CheckConstraint（confidence__in=['low', 'medium']）を追加
3. ContactFieldConfidence の save() オーバーライドを追加
4. 開発 DB に confidence='high' のレコードが残っていないか確認：`SELECT COUNT(*) FROM contacts_contactfieldconfidence WHERE confidence='high';`
5. 万一 0 件でない場合は、マイグレーション適用前に `DELETE FROM contacts_contactfieldconfidence WHERE confidence='high';` を実行
6. ActionLog モデルを新規作成（4.10 参照）
7. URL ルーティングを更新（11.3 参照）：9 番、13 番、16 番、17 番の追加・変更、18 番の削除
8. DuplicateMergeReason に additional_role を維持、PersonChangeReason から additional_role を削除（5 値化）
9. PersonChangeReason / DuplicateMergeReason / DifferentPersonReason の独立 TextChoices 化
10. 各モデルのモデルメソッドを実装（第10章参照）
11. Form クラス継承構造を実装（第11章 11.6 参照）
12. サービス層関数の実装（第13章 13.4 参照）
13. UI カスタムタグ・共通モーダル部品の実装（第11章 11.8 参照）
14. テンプレート実装

## 21.6 開発 DB の confidence='high' 確認

両 PC（自宅 PC、実家 PC）で以下のクエリを実行して確認する。

`SELECT COUNT(*) FROM contacts_contactfieldconfidence WHERE confidence='high';`

期待される結果：0 件

万一 0 件でない場合は、マイグレーション適用前に削除コマンドを実行し、結果を記録する。

## 21.7 PDF 表の配置

マージ前後のステータス遷移表（11 列横長）は別添 PDF として `/docs/spec/マージ前後のコンタクトのステータス等まとめ.pdf` に配置する。

- Git 管理対象
- Claude プロジェクトファイルにも追加
- コード君は実装時にこの PDF を参照する

## 21.8 コーディング着手前のチェックリスト

実装開始前に以下を確認する。

- [ ] v1.4.2 統合最終版の精読完了
- [ ] PDF 表（`/docs/spec/マージ前後のコンタクトのステータス等まとめ.pdf`）の精読完了
- [ ] 開発 DB の confidence='high' 確認完了（両 PC）
- [ ] スコアコピーの設計趣旨（12.8.4）の理解確認（実装中に「再計算が必要では？」と疑問が出ても、警告小節 12.8.2 を再読する）
- [ ] Person.primary_contact と Contact.status='primary' の二重管理の設計趣旨（4.5.2）の理解確認
- [ ] Contact = スナップショット設計（4.4.0）の理解確認
- [ ] recover 一本化の設計趣旨（12.8.1）の理解確認
- [ ] サバイブ側 Contact の previous_* は変更しないという原則（9.4.1）の理解確認
- [ ] same_card 修正ありの特殊扱い（9.4.5）の理解確認
- [ ] ContactFieldConfidence の生成・更新タイミング 3 ケース別（10.6.4）の理解確認

---

# 巻末別表

## 別表A モデル名・フィールド名対照表

仕様書本文では日本語で表記しているモデル名・フィールド名・プロパティ名と、コーディング名（アルファベット）の対照を以下に示す。

### A.1 アプリケーション

| 日本語名 | コーディング名 |
|---|---|
| 名刺アプリ | cards |
| 人物アプリ | persons |
| コンタクトアプリ | contacts |
| 重複検出アプリ | duplicates |
| アクションログアプリ | actionlogs |
| 共通設定 | config |

### A.2 モデル

| 日本語名 | コーディング名 |
|---|---|
| 元画像 | OriginalImage |
| 名刺 | BusinessCard |
| デバッグマスク | DebugMask |
| 人物 | Person |
| コンタクト | Contact |
| 信頼度メタ | ContactFieldConfidence |
| 重複候補 | DuplicateCandidate |
| マージ履歴 | PersonMergeLog |
| アクションログ | ActionLog |
| ユーザー | User |

### A.3 OriginalImage のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| アップロードユーザー | user |
| 元画像ファイル | image_file |
| 処理状態 | status |
| 処理開始日時 | claimed_at |
| OCR 結果 JSON | raw_json |
| 検出された名刺数 | detected_count |
| エラーメッセージ | error_message |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### A.4 BusinessCard のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| 元画像 | original_image |
| 名刺画像 | card_image |
| カードインデックス | card_index |
| 向き | orientation |
| 1 回目 OCR 生 JSON | raw_json_1 |
| 2 回目 OCR 生 JSON | raw_json_2 |
| OCR 処理状態 | ocr_status |
| OCR 処理結果 | ocr_result |
| OCR 開始日時 | claimed_at |
| エラーメッセージ | error_message |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### A.5 Contact のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| 名刺 | business_card |
| 人物 | person |
| ステータス | status |
| 前の人物 | previous_person |
| 前のステータス | previous_status |
| 重複チェック日時 | duplicate_checked_at |
| 作成者 | created_by |
| 更新者 | updated_by |
| 言語コード | lang |
| 郵便番号 | postal_code |
| フルネーム | full_name |
| 姓 | last_name |
| 名 | first_name |
| 敬称表記 | salutation_name |
| 会社名 | company |
| 部署 | department |
| 役職 | title |
| 支店 | branch |
| 住所 | address |
| メール | email |
| 電話 | phone |
| 携帯 | mobile |
| FAX | fax |
| ウェブサイト | website |
| 資格 | qualification |
| キャッチフレーズ | catchphrase |
| Twitter | twitter |
| Instagram | instagram |
| GitHub | github |
| LinkedIn | linkedin |
| Facebook | facebook |
| メモ | notes |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### A.6 Person のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| 主コンタクト | primary_contact |
| ステータス | status |
| 統合先 | merged_into |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### A.7 ContactFieldConfidence のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| コンタクト | contact |
| フィールド名 | field_name |
| 信頼度 | confidence |
| 確認日時 | confirmed_at |
| 確認者 | confirmed_by |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### A.8 DuplicateCandidate のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| グループ ID | group_id |
| 人物 A | person_a |
| 人物 B | person_b |
| スコア | score |
| ランク | rank |
| レビューステータス | review_status |
| レビュー結果 | review_result |
| メモ | note |
| 担当者 | assigned_to |
| 確認者 | reviewed_by |
| 確認日時 | reviewed_at |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### A.9 PersonMergeLog のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| サバイブ側人物 | surviving_person |
| マージド側人物 | merged_person |
| 重複候補 | duplicate_candidate |
| ステータス | status |
| マージ実行者 | executed_by |
| マージ実行日時 | executed_at |
| 復元実行者 | undone_by |
| 復元日時 | undone_at |
| メモ | note |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### A.10 ActionLog のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| 操作ユーザー | user |
| アクション | action |
| 対象モデル種別 | content_type |
| 対象オブジェクト ID | object_id |
| 対象オブジェクト | content_object |
| 対象オブジェクト表現 | object_repr |
| 追加情報 | data |
| 補足メモ | note |
| 作成日時 | created_at |

### A.11 DebugMask のフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| 元画像 | original_image |
| マスク種別 | mask_type |
| マスク画像 | mask_image |
| メタデータ | metadata |
| 作成日時 | created_at |

### A.12 主要なプロパティ・属性

| 日本語名 | コーディング名 |
|---|---|
| 主コンタクト | primary_contact |
| アクティブコンタクト一覧 | active_contacts |
| インアクティブコンタクト一覧 | inactive_contacts |
| サバイブ側 | surviving |
| マージド側 | merged |

---

## 別表B 管理コマンド一覧

| コマンド | オプション | 用途 |
|---|---|---|
| process_pending | --limit | cron 起動。OCR 処理 |
| retry_failed_ocr | --all / --id / --limit / --dry-run | failed を pending に戻す |
| reconcile_card_images | --apply | DB ↔ MEDIA_ROOT 整合検査・修復 |
| dev_reset_ocr | --all / --id / --limit / --dry-run | 開発用 OCR リセット |
| check_duplicates | --limit（デフォルト 100） | cron 起動。重複チェック実行 |
| recheck_duplicates | --all / --dry-run | 運用用。判定ロジック変更後の全件再判定 |
| dev_reset_duplicates | --all / --id / --limit / --dry-run | 開発用重複チェックリセット |

---

## 別表C TextChoices 値一覧

### C.1 OriginalImage.Status

| コード値 | 表示名 | 意味 |
|---|---|---|
| pending | 処理待ち | OpenCV / OCR 未実行 |
| processing | 処理中 | v1.4.2 改訂前の 1 本パイプライン用（後方互換のため物理残置） |
| opencv_processing | OpenCV 処理中 | OpenCV cron の CAS で claim 後、検出処理中 |
| cards_extracted | OpenCV 完了・OCR 待ち | OpenCV 検出完了・BC 作成済み・OCR 未実行 |
| extracted | 完了 | OCR 成功（部分失敗含む） |
| garbage | 無効画像 | cards 配列が空、または全 card が is_business_card=false |
| failed | 処理失敗 | OpenCV / OCR の致命的失敗 |

### C.2 BusinessCard.Orientation

| コード値 | 表示名 | 意味 |
|---|---|---|
| normal | 正常 | 名刺の上が正立 |
| rotate_90_cw | 右 90 度回転 | 時計回りに 90 度 |
| rotate_90_ccw | 左 90 度回転 | 反時計回りに 90 度 |
| rotate_180 | 上下反転 | 180 度回転 |
| mirror | 鏡像 | 左右反転（誤認識ケース） |

### C.3 ContactFieldConfidence.Confidence

| コード値 | 表示名 | 意味 |
|---|---|---|
| low | 低 | OCR 信頼度が低い |
| medium | 中 | OCR 信頼度が中程度 |

high は記録対象外（疑似インスタンスとしてのみ生成、DB 保存しない）。

### C.4 DuplicateCandidate.Rank

| コード値 | 表示名 | 意味 |
|---|---|---|
| exact_match | 完全一致 | 同じ名刺の重複取り込み |
| possible_high | 高確信度 | ほぼ同一人物 |
| possible_mid | 中確信度 | 同一人物の可能性が高い |
| possible_low | 低確信度 | 同姓同名の可能性あり、要慎重確認 |
| none | 該当なし | 候補に上がらない |

### C.5 DuplicateCandidate.ReviewStatus

| コード値 | 表示名 | 意味 |
|---|---|---|
| pending | 判定待ち | 未処理 |
| merged | マージ済み | マージ実行された |
| different_person | 別人確定 | 別人として確定 |
| invalidated | 無効化 | Contact 編集またはマージで無効化 |

### C.6 PersonMergeLog.Status

| コード値 | 表示名 | 意味 |
|---|---|---|
| undoable | 復元可能 | マージ実行直後の通常状態 |
| undone | 復元済み | 復元された |
| locked | 復元不可 | 多重マージで上書きされた |

### C.7 PersonChangeReason 値一覧（5 値）

Contact 編集の修正理由（UpdatePrimaryContactView 専用）。

| コード値 | 表示名 |
|---|---|
| fix | 入力間違い・誤字訂正 |
| transfer | 異動・部署変更 |
| promotion | 役職変更・昇進 |
| job_change | 転職 |
| name_change | 結婚等による姓変更 |

`additional_role`（別肩書追加）は v1.4.2 で削除した。別肩書追加は独立画面（9 番 PersonAddAdditionalRoleView）に分離。

13 番 UpdateActiveContactView では change_reason フィールドを置かない（fix 相当の処理に固定）。

### C.8 DuplicateMergeReason 値一覧（7 値）

DuplicateCandidate.review_result の merged 系（マージ画面専用）。

| コード値 | 表示名 |
|---|---|
| same_card | 同一名刺（撮り直し・重複アップロード） |
| transfer | 異動・部署変更 |
| promotion | 役職変更・昇進 |
| job_change | 転職 |
| additional_role | 別肩書追加（副業など） |
| name_change | 結婚等による姓変更 |
| other_merged | その他（マージ実行） |

### C.9 DifferentPersonReason 値一覧（3 値）

DuplicateCandidate.review_result の different_person 系。

| コード値 | 表示名 |
|---|---|
| same_name | 同姓同名 |
| ocr_error | OCR 誤認識による誤検出 |
| other_different | その他（別人確定） |

### C.10 Contact.Status

| コード値 | 表示名 | 意味 |
|---|---|---|
| primary | 主コンタクト | 1 人の Person につき 1 つだけ存在 |
| active | 副コンタクト | 別肩書など、現役で有効な情報 |
| inactive | 非アクティブ | 転職前など、過去の情報 |

### C.11 Person.Status

| コード値 | 表示名 | 意味 |
|---|---|---|
| active | 通常 | 検索・マージ対象 |
| merged | 統合済み | 他 Person に統合済み。編集禁止、マージ対象外 |
| archived | アーカイブ | 検索・マージ対象外 |

### C.12 ActionLog.action（参考値）

ActionLog の action フィールドに記録される代表的な値。新たな業務イベントを記録する際は、本表に値を追加する。

| コード値 | 意味 |
|---|---|
| created | 作成 |
| updated | 更新 |
| deleted | 削除 |
| merged | マージ実行 |
| different_person | 別人判定 |
| undone | マージ復元実行 |
| executed | cron 等の実行 |

### C.13 BusinessCard.OcrStatus

BC 単位の OCR 処理状態。v1.4.2 のパイプライン分離（第 8 章参照）で導入。

| コード値 | 表示名 | 意味 |
|---|---|---|
| pending | OCR 待ち | OpenCV で BC 作成済み、OCR 未実行 |
| processing | OCR 中 | OCR cron の CAS で claim 後、処理中 |
| done | 完了 | OCR 成功（採用 raw_json と ocr_result が確定） |
| failed | 失敗 | OCR の致命的失敗（1 回目自体が失敗、retry_failed_ocr --ocr で差し戻し可能） |

### C.14 BusinessCard.OcrResult

BC 単位の OCR 処理結果の分類。null 許容（OpenCV cron 完了直後は null、OCR cron 完了時に下記 5 値のいずれかに確定）。

| コード値 | 表示名 | 意味 |
|---|---|---|
| business_card | 名刺 | OCR 成功、Contact 生成あり |
| not_business_card | 名刺ではない | is_business_card=False 判定 |
| insufficient_info | 情報不足 | has_minimum_info NG |
| ocr_failed | OCR 失敗 | card 単位の OCR 例外 |
| others | その他 | 上記いずれにも該当しない予期せぬケース |

`ocr_failed` と `others` は将来用の受け皿として定義のみ。v1.4.2 時点での実装上のセット箇所はステップ 2 以降で確定する。

### C.15 DebugMask.MaskType

OpenCV 検出パイプラインで生成されるデバッグ用マスク画像の種別（5 種）。

| コード値 | 表示名 | 意味 |
|---|---|---|
| diff | 差分マスク | 差分検出マスク |
| edge | エッジマスク | エッジ検出マスク |
| sat | 彩度マスク | 彩度ベースのマスク |
| or | OR 合成マスク | 複数マスクの OR 合成結果 |
| closed | クローズマスク | モルフォロジー処理後のマスク |

---

## 別表D v1.4.2 改訂項目一覧表

v1.4.1 から v1.4.2 への改訂項目を、確定章節番号と根拠コード（rev6 / rev7 / rev8 のレビュー指摘ID）で淡々と並べる。コード君が実装中に「これはなぜこうなったか」を確認する際の参照用。

### D.1 主要な設計判断（rev6 で確定）

| 項目 | 確定章節 | 根拠 |
|---|---|---|
| 責務分離の体系化（View 層・サービス層・関数） | 11.4 / 13.4 | rev6 改訂サマリー #1 |
| 5 フローの整理（フロー1：別人 / フロー2/2'：マージのみ / フロー3/3'：マージ＋更新） | 11.5 | rev6 改訂サマリー #2 |
| サービス分割（execute_merge → 4 つのサービスに分割） | 13.4.1 | rev6 改訂サマリー #3 |
| 命名規則のローカルルール | 13.2 | rev6 改訂サマリー #4 |
| 関数の改名・統合・削除 | 13.4 / 21.3 | rev6 改訂サマリー #5 |
| マージ画面 UI の 3 カラム設計 | 11.5.6 | rev6 改訂サマリー #6 |
| マージ画面の表示対象を Contact のほぼ全フィールドに拡大 | 8.5.3 / 11.5.7 | rev6 改訂サマリー #7 |
| 12.8 の書き換え（recover 一本化） | 12.8 | rev6 改訂サマリー #8 |
| 重複検出の効率化アルゴリズム（OR 絞り込み） | 8.10 | rev6 改訂サマリー #9 |
| ActionLog の新規追加 | 4.10 / 4.11 / 12.10 | rev6 改訂サマリー #10 |
| Sonnet クロード君指摘事項 6 点の対応（復元時の primary_contact 同期含む） | 9.5.2 | rev6 改訂サマリー #11 |
| run_pipeline を Extract_Cards_via_OCR にリネーム | 13.4.1 | rev6 改訂サマリー #12 |
| Django モデルメソッド化の体系化 | 第10章 | rev6 改訂サマリー #13 |
| Form クラス活用方針の確定 | 11.6 | rev6 改訂サマリー #14 |
| UI カスタムタグ・追加ルート・共通モーダル部品 | 11.8 | rev6 改訂サマリー #15 |

### D.2 rev6 レビュー指摘の反映（rev6 内で確定）

| 指摘 ID | 内容 | 確定章節 |
|---|---|---|
| C-1 | DuplicateCandidate.create_recovered_from() クラスメソッド追加 | 10.7.1 / 12.8.3 |
| C-2 | cron 経由での prefetch_related('confidences') 必須化（N+1 対策） | 4.7.2 / 8.10.3 |
| C-3 | get_field_confidences() の戻り値を疑似インスタンス方式に変更 | 10.5.3 |
| C-4 | form.get_update_contact() の戻り値仕様明確化 | 11.6.5 |
| S-1 | rev3→rev4→rev5 関数移行表の追加 | 21.3 |
| S-2 | サービス関数フローの統合版での 1 箇所集約方針 | 13.4 |
| S-3 | ActionLog 記録メソッドの分離（record_merge_action / record_undo_action） | 10.8.2 |
| M5-3 | Run_Generation_of_Duplicate_Candidates_for_Contacts を Run_Generate_Duplicate_Candidates(limit=100) に短縮 | 13.4.1 |
| M5-4 | 7.1 比較表の関数構成行を最終確定版に更新 | 13.4 |
| S5-1 | field_decisions という抽象概念を削除（form.get_update_contact() / form.confirmed_field_names() に置き換え） | 11.6 / 13.2.5 |
| S5-1 派生 | form.confirmed_fields を form.confirmed_field_names() に変更（属性 → メソッド、戻り値型 list[str]） | 11.6.2 / 13.2.5 |
| S5-2 | candidate.record_action(user) を candidate.record_different_person_action(user) に改名 | 10.7.2 / 13.2.8 |
| ContactFieldConfidence の疑似インスタンス防御策 | CheckConstraint + save() オーバーライド | 4.6.1 / 10.6.2 |
| Execute_Merge_Without_Updates → Execute_Merge_Only への再変更 | 短さと直感性を優先 | 13.4.1 |
| タスク層関数を generate ベースに改名・ファイル名変更 | 命名対称性 | 13.2.1 / 13.4.4 |

### D.3 rev7 で確定（9 カテゴリ）

| カテゴリ | 内容 | 確定章節 |
|---|---|---|
| A：スコア下限矛盾の訂正 | 16.1 シナリオ表の 3 行訂正、設計趣旨追加 | 8.4.1 / 16.1 |
| B：マージ時のステータス遷移 | 9.4 表を別添 PDF として参照させる方針（rev8 で最終確定） | 9.4 |
| C：URL ・画面構成 | 11.3 URL 一覧表の整理。9 番、13 番、16 番が新規追加、12 番・17 番が URL 変更、18 番が廃止 | 11.3 |
| D：TextChoices | PersonChangeReason から additional_role を削除（5 値化）。DuplicateMergeReason は 7 値のまま | C.7 / C.8 / 14.3.2 / 14.3.3 |
| E：Form クラス構成 | ContactBaseForm 抽象基底クラス導入 | 11.6.1 |
| F：メソッド設計 | contact.fix(form, user) / contact.mark_all_confidence_confirmed(user) 新規追加 | 10.5.1 |
| G：公開サービス関数 | 4 つの公開サービス関数のシグネチャを 13.4.1 に追記 | 13.4.1 |
| H：設計思想 | Form 渡し vs 引数渡しの判断基準を明文化。ユーザー入力は全 high で信頼する設計を明文化 | 13.6.1 / 10.6.4 |
| I：その他 | candidate.mark_as_different_person(user, review_result, note=None) に更新 | 10.7.2 |

### D.4 rev8 で確定（8 カテゴリ）

| 指摘 ID | 内容 | 確定章節 |
|---|---|---|
| CR7-1' | サバイブ側 Contact の previous_* は変更しない（既存の値があれば保持）、PDF 表を別添ファイル化 | 9.4.1 |
| CR7-3 | contact.fix の form 引数を ContactUpdateForm に限定、マージ画面 same_card は別処理 | 10.5.2 / 9.4.5 |
| DI7-1 | 13 番（active 副コンタクト修正画面）は change_reason フィールドなし、Form は ContactUpdateActiveForm | 11.4.3 / 11.6.2 / C.7 |
| DI7-2 | ContactFieldConfidence の生成・更新タイミングを 3 ケース別に整理 | 10.6.4 |
| DI7-3' | v1.4.1 11.5 の挙動を rev8 で更新（17 番のリダイレクト先を 16 番に変更）、Django messages framework の使用明記 | 11.5.4 |
| DI7-4 | Execute_Merge_with_Updates の merge_reason='same_card' 分岐の挙動を明確化（部分更新、mark_fields_as_confirmed で部分 confirmed 化） | 9.4.5 |
| DI7-5 | Person.set_primary_contact() に old_primary_new_status 引数追加（デフォルト 'active'） | 10.4.3 |
| M7-1 | typo 修正（Excute_* → Execute_*）+ 単複統一（Execute_Merge_with_Updates に戻す） | 13.4.1 |
| M7-2 | 9 番 View 名 PersonAddAdditionalRoleView に変更（URL の Person 起点と整合） | 11.3 |
| M7-7 | 9.5 の Form 継承図を削除し、4.1 への参照に置き換え | 11.6.1 |

### D.5 rev8 新規明文化事項

| 内容 | 確定章節 |
|---|---|
| PDF 表の別添ファイル化（/docs/spec/マージ前後のコンタクトのステータス等まとめ.pdf） | 9.4 / 21.7 |
| 「previous_* は変更しない」と「記録しない」の表現統一 | 9.4.1 |
| same_card 特殊扱いの明確化（fix 相当を削除、部分更新と部分 confirmed 化と明記） | 9.4.5 |
| Form のバリデーション仕様の新規明文化（ContactUpdateForm.clean() / MergeForm.clean() / target_contact の渡し方） | 11.7 |
| rev7 のコード片を「シグネチャ + 業務ルールの表 + 処理フロー（関数名の並び）」の 3 部構成に書き直し | 13.4 / 第10章 |

---

# 改訂履歴

| バージョン | 日付 | 改訂内容 | 改訂者 |
|---|---|---|---|
| v1.0.0 | 2026/04/27 | 新規作成 | たんたん |
| v1.0.1 | 2026/04/27 | GPT レビュー指摘 4 点を反映 | たんたん |
| v1.1.0 | 2026/04/28 | Contact 配列廃止・ContactFieldConfidence 追加・services 統合 | たんたん |
| v1.2.0 | 2026/04/28 | raw_json を OriginalImage に集約・BusinessCard 簡素化・card_index 追加 | たんたん |
| v1.2.1 | 2026/04/28 | cards 処理ルール明文化・OcrBackend 抽象化方針・JSON Schema 管理・card_image null 許容 | たんたん |
| v1.2.2 | 2026/04/28 | Phase 3-2 着手前確定版 | たんたん |
| v1.3.0 | 2026/04/30 | OCR パイプライン堅牢性修正 | たんたん |
| v1.3.1 | 2026/04/30 | 完結版・全文掲載。run_pipeline 防御チェック・stuck cleanup | たんたん |
| v1.3.2 | 2026/04/30 | コードスニペット削除・概念図追加・実装との差異解消 | たんたん |
| v1.3.3 | 2026/05/01 | v1.3.2 レビュー指摘 6 件修正 | たんたん |
| v1.3.4 | 2026/05/01 | v1.3.3 見栄え 3 点修正（Heading・表ヘッダー・★改訂セル） | たんたん |
| v1.4.0 | 2026/05/01 | 重複検出・人物統合機能を完全統合した v1.4.0 完結版。DuplicateCandidate / PersonMergeLog / Contact ステータス管理 / Person マージ / フィールド正規化 / バックグラウンド重複チェック / レビュー画面（PRG パターン）/ Contact 手動作成 / アプリケーション分離 / TextChoices 統一 | たんたん |
| v1.4.1 | 2026/05/02 | 設計案 A 採用（マージ画面が「データ品質確認の作業画面」も兼ねる設計）。8.5 全面書き直し（事前制約 → マージ実行時の最終要件）。9.3 マージ処理フロー全面改訂。12.8 条件分岐版に拡張（再復帰除外条件含む）。14.3 独立 TextChoices 設計趣旨追加。別表 C.7/C.8/C.9 分離。設計趣旨節 13 箇所追加。計 27 箇所改訂 | たんたん |
| v1.4.2 | 2026/05/05 | コーディング着手前最終確定版。v1.4.1 + rev6 + rev7 + rev8 を統合した v1.4.2 統合最終版。recover 一本化、サービス分割（4 公開サービス）、Django モデルメソッド化体系（merge_helpers.py 全削除）、Form クラス活用方針確定（ContactBaseForm 抽象基底クラス）、重複検出効率化アルゴリズム、ActionLog 新規追加、画面・URL 整理（9 番・13 番・16 番新規、17 番 URL 変更、18 番廃止）、PDF 表別添化、UI カスタムタグ・共通モーダル部品。詳細は別表 D 参照 | たんたん |

---

**（仕様書終わり）**
