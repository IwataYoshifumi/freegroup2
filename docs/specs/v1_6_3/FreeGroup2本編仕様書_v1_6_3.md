# FreeGroup2 本編仕様書
バージョン v1.6.3
FreeGroup2 グループウェア
2026年5月作成 / OCR・OpenCV 章を v1.6.0 系 3 本に分離し本編仕様書として再編（v1.6.1 で電話系巻き戻し・organization.website 追加・ContactSns 別テーブル化を反映 / v1.6.2 で Phase F1・F2・G の実装確定事項を反映 / v1.6.3 で Contact.status の業務語を主／副／旧コンタクトに統一）

# v1.6.0 改訂サマリー（v1.4.4 → v1.6.0）

本書は FreeGroup2 全体の本編仕様書である。v1.4.4 までは「名刺画像取り込み OCR・人物統合機能 仕様書」というタイトルで OCR/OpenCV 関連章を内包していたが、v1.6.0 で OCR/OpenCV 仕様を v1.6.0 系 3 本（統合版・OCR プロンプト・JSON 構造対応表）に分離した。本書はその結果として OCR/OpenCV 章を引き剥がし、FreeGroup2 全体の永続仕様（Contact 編集・正規化基盤・Form・View・URL・マージ・重複検出・認証・運用）を担う本編仕様書として再編した。

| # | 改訂内容 |
|---|---|
| 1 | 旧第 5 章（OCR 結果 JSON 仕様）・第 6 章（画像処理仕様）・第 7 章（OCR バックエンド仕様）・第 15 章（OCR パイプライン）・§4.12（DebugMask）を削除し、v1.6.0 系 3 本への参照に置き換え |
| 2 | 旧 §15.5（正規化ルール）を §11.9（Contact 正規化基盤）として第 11 章末尾に移植。v1.6.1 統合版 第 6 部（3 経路共有の正規化基盤・AJAX 経路の正規化通し・compose_full_address・正規化 5 カテゴリ・org_domain_name 汎用ドメイン無視）を統合 |
| 3 | Contact フィールドの v1.6.0 改訂（20 件新規・4 件リネーム・1 件削除）を別表 A.5・§4.4.1・関連各節に反映。新規 20 件は OCR キー対応のある 19 件 + salutation_name_is_manual（OCR キー対応関係なし・本編側永続仕様として追加） |
| 3-2 | salutation_name_is_manual（BooleanField, default=False）を新設。Contact.save() オーバーライドで is_manual=False のとき compute_salutation_name(self) を再計算する処理の所属を本編 §11.9.7 に明示（v1.6 メール配信仕様書 §18.2 と整合） |
| 4 | confidence 値域を `high`/`mid`/`low` に統一（`medium` → `mid`） |
| 5 | salutation_name 手動入力時必須化を §4.4.1・§11.6.2・§11.7 に反映 |
| 6 | OriginalImage に exif_json フィールド追加を別表 A.3 に反映 |
| 7 | DUPLICATE_CHECK_FIELDS のフィールド名リネームを §14.3.5・§8.8 に反映 |
| 8 | §11.5.5・§11.6.2 で「D-3 系 詳細仕様確定後、別途反映予定」となっていた保留記述を、v1.6.0 で確定済みの旨と §11.9 への参照に書き換え |
| 9 | 第 21 章（Phase 4 実装スコープ）を章ごと削除（v1.4.2 着手時の準備章で v1.6.0 では役割終了） |
| 10 | 別表 D（v1.4.2 改訂項目一覧表）・別表 E（v1.4.2 → v1.4.3 差分）・別表 F（v1.4.3 時点の不明点）を削除 |

削除した章・別表は **欠番のまま維持**（再採番しない）。理由：本編内の他章からの参照（§10.4.3・§11.3 等）が壊れることを防ぐため。

# v1.6.1 改訂サマリー（v1.6.0 → v1.6.1）

v1.6.0 リリース後の実装フェーズ（Phase 1A 〜 3B）で判明した設計問題 3 点を解決した改訂。

| # | 改訂内容 |
|---|---|
| 1 | Contact 電話系 4 フィールド（personal_phone / personal_fax / org_phone / org_fax）を JSONField(default=list) から CharField(50) に巻き戻し。別表 A.5・§4.4.1 に反映。理由：v1.6.0 の JSONField 化は前任サポート担当 Claude の見落としによる仕様逸脱で、v1.1.0 で確定した「1 名刺 = 1 Contact、主たる 1 つを保存」原則と整合させる |
| 2 | Contact 個別 SNS フィールド 5 件（twitter / instagram / github / linkedin / facebook）を廃止し、ContactSns 別テーブルに統合。別表 A.5・§4.4.1・§4.4.4・§11.5.7・§11.6 等に反映 |
| 3 | OCR 出力 JSON の organization.website を新設し、既存 Contact.website に同名ストレート流し込み。Contact 側のフィールド追加は不要（既存流用）。マイグレーション不要 |
| 4 | UPDATABLE_FIELDS から個別 SNS 5 件を削除、ContactSns 関連の取り扱いを追加 |
| 5 | マージ画面 24 フィールド比較表示（§11.5.7）の SNS 表示制御を ContactSns 対応に変更 |
| 6 | Form クラス設計（§11.6）で SNS 編集 UI を InlineFormSet 方式に変更 |

本サマリーは仕様書改訂のみを対象とする。実装（マイグレーション・コード変更）は v1.6.1 仕様書確定後に Phase 1A.5 / Phase 1C として別途指示する。

# v1.6.2 改訂サマリー（v1.6.1 → v1.6.2）

v1.6.1 仕様確定後の実装フェーズ（Phase F1・F2・G）で確定した事実を仕様書に反映する改訂。**設計判断は変更せず、実装で確定した事実の反映のみ**を行う。

| # | 改訂内容 |
|---|---|
| 1 | Contact UPDATABLE_FIELDS のフィールド数を 24 → **31** に更新（§11.5.7・§11.5.5 等）。31 フィールドの内訳（名前系 9 / 会社系 8 / 住所系 5 / 連絡先 7 / メモ言語 2）を §11.5.7 に明記。address は UPDATABLE_FIELDS から除外（Contact.save() が 4 要素から自動組み立て）、ContactSns は別テーブル化により除外（InlineFormSet で別途扱う） |
| 2 | **Phase F1**：ContactSns InlineFormSet を §11.6.7 に明文化（対象 4 画面・build_contact_sns_formset ヘルパー・prefix="sns"・extra=0 / can_delete=True / max_num=None・共通 partial・BEM クラス・JS フック命名・clean() による重複バリデーション・sns_id strip）。新規 Contact 作成時の ContactSns 引き継ぎロジックを §11.4.2.1 に新設。9 番フォームの salutation_name 必須化を §11.6.2 / §11.7 に追記。ContactSns の related_name="sns_accounts" と AppErrorList 配布を §4.4.4 に追記 |
| 3 | **Phase F2**：マージ画面の SNS 比較表示 UI 仕様を §11.5.7 に明文化（sns_type 別グルーピング・diff ハイライト・(なし) 表示・SnsType.choices 定義順・diff 判定基準・app-sns-compare__* クラス棲み分け）。マージ画面の縦順序に「SNS 比較ブロック」を §11.5.5 に追加 |
| 4 | **Phase G**：元画像詳細画面（6 番）の EXIF 情報表示仕様を §11.3 No.6 に追記（`<details>` 折りたたみ・JSON 整形表示・EXIF なし時の平文表示・3 関数の配置）。GPSAltitudeRef 等 BYTE 型の整数化・EXIF 業務利用 UI・認証仕様との整合を §20 将来構想に追記 |
| 5 | 実コード差分反映：salutation_name 自動計算のスナップショット方式（§11.9.7）、compute_salutation_name の lang 前方一致判定と lang 別宛名組み立てルール一覧（§11.9.7）、AJAX 経路のカテゴリA正規化挙動（前後 strip のみ、§11.9.3）を追記 |
| 6 | スクリプト追加：dev_create_dup_test_data を別表 B に追記。開発環境セットアップ（pytest.ini・requirements.txt の pytest / pytest-django）を §17 / 別表 B 周辺に追記 |

v1.6.1 改訂サマリーは変更しない。既存の節構成・欠番・別表番号はそのまま維持する（再採番しない）。

# 第1章 はじめに
## 1.1 目的
本書は、FreeGroup2 における名刺画像取り込み・データ管理機能、および重複検出・人物統合機能の設計を定義することを目的とする本編仕様書である。本機能は、ユーザーがアップロードした名刺画像から名刺情報を自動抽出してデータベースに保存・管理し、複数の名刺データの中から同一人物を検出して統合する仕組みを提供する。OCR/OpenCV の詳細仕様は v1.6.0 系 3 本に分離されている（§1.2 末尾参照）。
## 1.2 適用範囲
本書は FreeGroup2 の名刺・人物管理機能を中心とした本編仕様であり、以下の機能を含む。
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

OCR 関連の詳細仕様（OCR プロンプト・OCR 出力 JSON 構造・OpenCV パイプライン・json_parser・条件付き 2 回 OCR）は v1.6.0 系 3 本（OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 / OCRプロンプト / JSON構造・コンタクトフィールド対応表）を参照。本書は Contact 編集・正規化基盤・Form・View・URL・マージ・重複検出・認証・運用の永続仕様を担う。

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
| 旧コンタクト | Person に紐付く inactive 状態の Contact。転職前など過去の情報 |
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

# 第2章 システム概要
## 2.1 機能概要
本機能は、ユーザーが撮影した名刺画像を取り込み、AI（Claude API）を活用して名刺情報を自動抽出し、構造化されたデータとしてデータベースに保存する。1 枚の画像に複数の名刺が含まれていても、OpenCV による事前検出と Claude による個別 OCR を組み合わせて処理する。
保存された Contact および Person について、バックグラウンドで重複検出を実行し、同一人物候補を DuplicateCandidate として DB に記録する。ユーザーはレビュー画面で 1 ペアずつ判定し、人物統合（マージ）または別人判定を行う。マージは 1 段階前まで復元可能とする。
## 2.2 採用する処理方式
画像内の名刺検出を OpenCV で行い、各名刺の OCR を Claude API で行う方式を採用する。横長統一処理は v1.3.0 で削除され、縦書き名刺にも対応している。
OCR 結果から Contact を生成する際にフィールドの正規化を実施する。重複検出はバックグラウンド処理（cron 起動）で実行し、ユーザーの取り込み操作を待たせない。
## 2.3 処理フローの全体像
システム全体の処理フローは以下のとおり。
- ユーザーが画像をアップロード（同期処理） → OriginalImage 作成（status=pending）
- cron による process_opencv 起動 → OpenCV で名刺画像切り抜き → BusinessCard 作成（status=cards_extracted、ocr_status=pending）
- cron による process_ocr 起動 → BC 単位で条件付き 2 回 OCR 実行 → Contact / Person 作成、status=extracted（OpenCV と OCR は独立した 2 本 cron に分離、§15.6 / §17 別表 B 参照）
- cron による check_duplicates 起動 → 主コンタクト同士で重複検出 → DuplicateCandidate 作成
- ユーザーがレビュー画面を開き、1 ペアごとに判定（マージ / 別人 / 次の候補）
- マージ実行 → PersonMergeLog 作成 → Contact 付け替え → Person.status='merged' に変更 → recover_duplicate_candidates 実行
- 必要に応じて復元実行 → Contact を previous_person に戻す → ログを undone に
View 層の責務は元画像の保存（OriginalImage 作成、status=pending）までに限定される。OCR 処理、重複チェック処理、マージ処理は別プロセスまたは別 View で実行される。

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
| raw_json | JSONField (null) | OCR 結果 JSON（v1.4.3 で完全 deprecated：実装は書き込みも読み出しも行わない。OCR 結果は BC.raw_json_1 / raw_json_2 で参照する。フィールド自体は将来削除予定として物理残置） |
| debug_json | JSONField (null, blank) | OpenCV 検出のデバッグ中間データ。None なら次回 GET 時に再計算される。save_debug_data が DebugMask と並行して書き込む（v1.4.3 で正式記載） |
| detected_count | IntegerField (default=0) | 検出された名刺数（OriginalImage に紐づく BusinessCard レコードの総数。ocr_result の値に関わらず DB に保存された BC 全件をカウントする） |
| error_message | TextField (default='') | 失敗理由・部分失敗ログ |
| created_at | DateTimeField | auto_now_add |
| updated_at | DateTimeField | auto_now |

raw_json のリセット：stuck sweeper（_cleanup_stuck_record）および retry_failed_ocr --opencv で OriginalImage を pending に差し戻す際、raw_json は NULL にリセットされる（実装：cards/management/commands/process_opencv.py）。書き込みが行われていないため実害はないが、リセット処理は維持されている。
### 4.2.1 OriginalImage.status の値
詳細は別表 C.1 参照。pending / opencv_processing / cards_extracted / processing / extracted / garbage / failed の 7 値。
各値の意味：

| 値 | 意味 | 設定経路 |
|---|---|---|
| pending | アップロード直後／差し戻し直後の処理待ち | UploadView 直下、stuck 救済、retry_failed_ocr --opencv |
| opencv_processing | process_opencv の CAS 取得直後・OpenCV 検出中 | process_opencv の _claim_lock |
| cards_extracted | OpenCV 完了・OCR 待ち（BC が 1 件以上作成済み） | Run_Crop_Cards_From_OriginalImage 正常終了時 |
| processing | （旧 1 本パイプライン残置値、現フローでは未使用） | （現実装では設定されない、巻末別表 F 不明点参照） |
| extracted | 全 BC の OCR 完了（成功・名刺ではない・情報不足の混在含む） | _update_original_image_status 集計 |
| garbage | OpenCV 検出 0 件 | Run_Crop_Cards_From_OriginalImage |
| failed | OpenCV 想定外例外、または全 BC が OCR 失敗 | Run_Crop_Cards_From_OriginalImage ／ _update_original_image_status |

processing は v1.4.2 改訂前の 1 本パイプライン用の値で、TextChoices には物理残置されているが、現実装の OpenCV／OCR 分離フローではこの値に遷移する経路は確認できていない（巻末別表 F 不明点 #2 参照）。
### 4.2.2 OriginalImage のモデルメソッド
メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| OriginalImage.get_pending(limit) | クラスメソッド | pending な OriginalImage を limit 件取得（cron 用） |
| OriginalImage.release_stuck_locks(threshold_minutes) | クラスメソッド | stuck な processing レコードを pending に戻す |
| original_image.get_image_url() | インスタンスメソッド | サムネイル用 URL を返す |
| original_image.get_image_url_full() | インスタンスメソッド | フルサイズ用 URL を返す |

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
| business_card.get_card_image_url() | インスタンスメソッド | サムネイル用 URL を返す |
| business_card.get_card_image_url_full() | インスタンスメソッド | フルサイズ用 URL を返す |

### 4.3.2 BusinessCard と Contact の関係
v1.4.2 で has_minimum_info NG ケース等でも BC を残置する仕様（第 15 章参照）を採用したため、BusinessCard と Contact の関係は v1.4.2 改訂前の「常に 1:1」から「条件付きの 1:0..1」に変わる。

| BC.ocr_result | Contact の有無 |
|---|---|
| business_card | Contact を必ず持つ（OneToOne、§4.4 Contact 参照） |
| not_business_card / insufficient_info / ocr_failed / others | Contact を持たない |
| null（OpenCV cron 完了直後、OCR 未実行） | Contact を持たない |

【削除カスケード】 BC を削除すると、以下の順で連鎖削除される：
- BC レコード削除（bc.delete()）
- Contact（OneToOneField、CASCADE）が連鎖削除
- ContactFieldConfidence（Contact への ForeignKey、CASCADE）が連鎖削除
- card_image の FS 実体が post_delete シグナルで自動削除
OriginalImage.raw_json には削除した BC に対応する cards 配列要素が温存される（§5.4 不変ルール v1.2.1）。CardDeleteView 経由のハード削除でも本カスケードルールに従う（第 11 章参照）。
## 4.4 Contact（コンタクトDB）
### 4.4.0 設計趣旨：Contact はなぜ「スナップショット」か
Contact は「ある時点での名刺情報のスナップショット」として設計している。実世界では、人物の所属・肩書・連絡先は時間とともに変化し、変化のたびに新しい名刺が発行される。本仕様はこの事実をそのままモデル化し、転職や異動のたびに新しい Contact を作成して旧 Contact を inactive 化する運用とする（第11章参照）。
データモデルの正規化原則からは「Person を頂点に Contact がぶら下がる」構造に違和感があるかもしれないが、本仕様は実世界の事象との対応関係を優先する。1 人の人物（Person）に対して時系列の名刺履歴（Contact 群）が紐付くという構造は、名刺管理という業務の本質に素直である。
なお fix（誤字訂正）の場合のみ既存 Contact を更新するが、これは「同じ名刺の入力をやり直す」操作であり、新しい時点の情報ではないため例外的に上書きを許容している。
BusinessCard と Contact の関係は条件付きの 1:0..1（OCR 成功 BC は Contact を持つ、それ以外の BC は Contact を持たない）。詳細は §4.3.2 参照。
### 4.4.1 Contact のフィールド定義
名刺ごとまたは手動入力ごとのスナップショット。BusinessCard と OneToOne 関係（手動入力時は null 許容、また BC 側の ocr_result が business_card 以外のときも Contact は存在しない、§4.3.2 参照）。Person への FK は NOT NULL。

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
| full_name | CharField(255, blank=False) | 氏名（正規化済み、必須）。OCR 経路では json_parser が original_script をコピー |
| last_name / first_name | CharField(255) | 姓 / 名（オプション） |
| salutation_name | CharField(255) | 敬称表記。**手動入力時必須（v1.6.0）**。DB は NULL 許容のまま Form/View で必須 |
| salutation_name_is_manual | BooleanField(default=False) | **v1.6.0 新規**。salutation_name の手動入力フラグ。True なら Contact.save() の自動再計算で上書きされない（§11.9.7）。OCR 経路では設定しない |
| organization | CharField(255) | 会社名（正規化済み）。**v1.6.0 で company からリネーム** |
| department | CharField(255) | 部署 |
| title | CharField(255) | 役職 |
| branch | CharField(255) | 支店・営業所・店舗 |
| address | CharField(500) | 住所（compose_full_address が 4 要素から組み立て、§11.9.4） |
| email | CharField(255) | メール（小文字化済み） |
| mobile_phone | CharField(50) | 個人携帯（**v1.6.0 で mobile からリネーム**、単一値・E.164） |
| personal_phone | CharField(50) | 個人直通電話（**v1.6.0 で phone からリネーム**、単一値・E.164。v1.6.1 で JSONField から CharField に巻き戻し） |
| personal_fax | CharField(50) | 個人 FAX（**v1.6.0 で fax からリネーム**、単一値・E.164。v1.6.1 で JSONField から CharField に巻き戻し） |
| org_phone | CharField(50) | 会社代表・部署電話（v1.6.0 新規、単一値・E.164。v1.6.1 で CharField に巻き戻し） |
| org_fax | CharField(50) | 会社・部署 FAX（v1.6.0 新規、単一値・E.164。v1.6.1 で CharField に巻き戻し） |
| website | CharField(500) | ウェブサイト URL（v1.6.1 で OCR organization.website からのストレート流入経路を新設） |
| qualification / catchphrase | CharField(500) | 資格 / キャッチフレーズ |
| notes | TextField | 自由記述メモ（正規化対象外。OCR ai_analysis_notes とは無関係） |
| created_at / updated_at | DateTimeField | 自動付与 |

上表はリネーム・主要フィールドの抜粋。**v1.6.0 で新規追加 19 件・リネーム 4 件・流用変更を含む全フィールドは別表 A.5 を正本とする**。original_script・ai_analysis_notes は Contact フィールドに持たない（raw_json 内のみ）。

v1.6.1 改訂：個別 SNS フィールド 5 件（twitter / instagram / github / linkedin / facebook）を廃止し、ContactSns 別テーブルに統合した。詳細は §4.4.4（ContactSns のフィールド定義）参照。電話系 4 フィールド（personal_phone / personal_fax / org_phone / org_fax）は v1.6.0 で一時的に JSONField 化されていたが、v1.6.1 で CharField(50) 単一値に巻き戻した。

full_name は必須フィールド。OCR 由来・手動入力・マージ画面・AJAX 更新を含むすべての経路で空文字を弾く（DB 制約 + Form clean + AJAX View ガード）。salutation_name は手動入力時必須（v1.6.0、§11.6.2 / §11.7）。正規化ルールの詳細は §11.9（Contact 正規化基盤）を参照。
### 4.4.2 Contact.status の値

| 値 | 意味 |
|---|---|
| primary | 主コンタクト。1 人の Person につき 1 つだけ存在 |
| active | 副コンタクト。別肩書など、現役で有効な情報 |
| inactive | 旧コンタクト。転職前など、過去の情報 |

制約：partial unique constraint により、1 人の Person につき status='primary' の Contact は 1 つだけ。
表示ラベル（choices の label）は業務語に統一するが、フィールドの verbose_name は admin 表示・バリデーション cascade への波及回避のため、英字・既存のまま据え置く。
### 4.4.3 Contact のモデルメソッド
メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| contact.fix(form, user) | インスタンスメソッド | フォーム値で自身のフィールドを上書きし、全 ContactFieldConfidence を confirmed 化する。form 引数は ContactUpdateForm に限定 |
| contact.get_field_confidences() | インスタンスメソッド | 全フィールドの ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） |
| contact.get_high_fields() | インスタンスメソッド | 実質 high なフィールド集合を返す |
| contact.is_all_field_confidence_high(fields=None) | インスタンスメソッド | 全 high 判定（引数省略時は全フィールド、指定時は範囲限定） |

### 4.4.4 ContactSns のフィールド定義（v1.6.1 新設）

Contact 個別 SNS フィールド 5 件（twitter / instagram / github / linkedin / facebook）を廃止し、ContactSns 別テーブルに統合する。1 Contact に対し複数の SNS アカウントを持てる（YouTuber 複数チャンネル、Twitter 個人/会社両方等）。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| contact | FK(Contact, CASCADE, related_name="sns_accounts") | 親コンタクトへの参照 |
| sns_type | CharField(50, choices=SnsType) | SNS 種別（8 種固定・小文字統一） |
| sns_id | CharField(500) | URL またはユーザー ID |
| created_at | DateTimeField (auto_now_add) | 作成日時 |
| updated_at | DateTimeField (auto_now) | 更新日時 |

- **SnsType choices（8 種、小文字統一）**：twitter / linkedin / facebook / instagram / github / blog / youtube / line
- **UniqueConstraint**：fields=["contact", "sns_type", "sns_id"]、name="unique_contact_sns"。同一 Contact で同じ sns_type かつ同じ sns_id の重複登録を防ぐ。同一 sns_type で sns_id が異なるレコードは両立可（複数チャンネル対応）
- **json_parser でのレコード生成**：OCR sns 配列の各要素の type を正規化（X → twitter 等、`_SNS_TYPE_ALIASES`）してから choices 内のもののみ get_or_create でレコード作成。choices 外はサイレント無視。詳細は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §3.7 / JSON 構造対応表 §8 参照
- **設計根拠**：type 拡張に DB 変更不要、複数チャンネル対応、検索ニーズ（`Contact.objects.filter(sns_accounts__sns_type="line")` 等）に JOIN で対応、ユーザーが直接編集する JSONField を増やさない
- **編集 UI**：InlineFormSet 方式（§11.6.7 参照）

**v1.6.2 実装確定事項：**
- `related_name="sns_accounts"`（複数形）を採用済み。1 Contact に複数 SNS が紐付く設計を名前にも反映する（`contact.sns_accounts.all()` で参照）
- InlineFormSet の基底 `_BaseContactSnsFormSet` に `AppErrorList`（§11.6.6 参照）を error_class として配布済み。FormSet 内の各 Form のフィールドエラーに既存 BEM クラス app-form__error が自動付与される

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
【採用の経緯】 初期案は Person.primary_contact を持たず、Contact.status='primary' のみで代表を表現する方針であった。しかし実装検討にあたり、Person 起点で代表コンタクトを参照する処理が頻出することが判明し、Person.primary_contact を持たせた方が実装上有利な場面が多いと判断した。全クロード（複数の Claude インスタンス）との議論を経て、二重管理を許容する設計に切り替えた。
【Contact.status='primary' を保持する理由】 ユーザー視点では、Contact 一覧を見たときに Contact 自身の属性として「代表である」ことが分かる必要がある。また将来、別肩書対応や多言語対応で、1 人の Person に対し日本語コンタクト・英語コンタクトなど複数の active な Contact を並列保持する可能性があり、その場合に代表を識別する手段として status='primary' が必要となる。一見冗長に見えるが、将来に備えた設計である。
【正本と同期方針】 Person.primary_contact を正本とし、Contact.status='primary' はその派生情報として同期する。同期処理は Person.set_primary_contact() インスタンスメソッドに集約し、View 層・Model.save() からは直接変更しない。
【過去のレビュー指摘について】 「データの二重管理ではないか」という指摘は過去のレビューで複数回あり、本節はその指摘に対する設計判断の根拠を仕様書として明示するために記載する。今後同じ指摘を受けた場合は、本節を参照することで設計意図を伝達する。
### 4.5.3 Person のモデルメソッド
メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| person.mark_as_merged(surviving_person) | インスタンスメソッド | 自身の状態遷移（status='merged' / merged_into / primary_contact=NULL） |
| person.transfer_contacts_to(surviving_person, merge_reason) | インスタンスメソッド | 自身のコンタクト群を surviving に引き渡す（全 Contact 対象） |
| person.set_primary_contact(new_contact, old_primary_new_status='active') | インスタンスメソッド | primary_contact 切り替え。old_primary_new_status で旧 primary の遷移先を指定（'active' / 'inactive'） |
| person.get_active_contacts() | インスタンスメソッド | status='active' の Contact 一覧を返す |
| person.get_inactive_contacts() | インスタンスメソッド | status='inactive' の Contact 一覧を返す |
| Person.get_active() | クラスメソッド | status='active' の Person 一覧を返す |
| Person.get_archived() | クラスメソッド | status='archived' の Person 一覧を返す |

## 4.6 ContactFieldConfidence（信頼度メタDB）
Contact フィールドごとの信頼度を別テーブルで管理する。human-in-the-loop による確認履歴も保持する。

| フィールド名 | 型 | 説明 |
|---|---|---|
| id | UUIDField (PK) | プライマリキー |
| contact | FK(Contact, CASCADE) | related_name='confidences' |
| field_name | CharField(50) | Contact 側のフィールド名 |
| confidence | CharField(TextChoices) | low / mid のみ（high は記録対象外。v1.6.0 で medium→mid 統一） |
| confirmed_at | DateTimeField (null=True) | ユーザー確認日時 |
| confirmed_by | FK(User, SET_NULL) | 確認したユーザー |
| created_at / updated_at | DateTimeField | 自動付与 |

方針：high の値はレコード作成しない。mid / low のみ記録。UniqueConstraint(contact, field_name)。
### 4.6.1 high レコードの防御策
confidence='high' のレコードが誤って DB に保存されないよう、二重防御を実装する。
- CheckConstraint（DB 制約）：DB レベルで confidence='high' の保存を物理的に禁止する
- save() オーバーライド（アプリケーション層）：confidence='high' で save() が呼ばれた場合、明示的なエラーメッセージで誤用を検出する
これにより、get_field_confidences() が返す疑似インスタンス（confidence='high'）が誤って save() されても、DB に混入することを防ぐ。
### 4.6.2 ContactFieldConfidence のモデルメソッド
メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| ContactFieldConfidence.get_for_contact(contact) | クラスメソッド | 全フィールド分の ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） |
| ContactFieldConfidence.create_for_contact(contact, confidence_map) | クラスメソッド | OCR 結果の mid/low フィールドについて一括作成 |
| ContactFieldConfidence.mark_fields_as_confirmed(contact, field_names, user) | クラスメソッド | 指定フィールドを確認済み化 |

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
cron 経由（Run_Generate_Duplicate_Candidates）で _calculate_score を多数回呼ぶ場合、内部で各 Contact の get_field_confidences() を呼ぶため N+1 問題が発生する。これを防ぐため、候補取得時に prefetch_related('confidences') を必須とする。
ContactCreateView からの呼び出しは 1 件ずつのため、prefetch_related は必須としない。
### 4.7.3 DuplicateCandidate のモデルメソッド
メソッド一覧は以下のとおり。詳細は第10章参照。

| メソッド | 種別 | 責務 |
|---|---|---|
| DuplicateCandidate.get_pending(contact) | クラスメソッド | contact が紐づく Person の pending 候補を取得 |
| DuplicateCandidate.get_merged(contact) | クラスメソッド | contact が紐づく Person の merged 候補を取得 |
| DuplicateCandidate.get_different_person(contact) | クラスメソッド | contact が紐づく Person の different_person 候補を取得 |
| DuplicateCandidate.get_invalidated(contact) | クラスメソッド | contact が紐づく Person の invalidated 候補を取得 |
| DuplicateCandidate.has_duplicates(contact, status) | クラスメソッド | 指定 status の候補が存在するかどうかの判定 |
| DuplicateCandidate.get_by_group(group_id) | クラスメソッド | group_id 単位で取得 |
| DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person) | クラスメソッド | old_candidate からスコア・ランク・group_id 等をコピーして新規作成 |
| candidate.mark_as_merged(user, review_result, note) | インスタンスメソッド | 自身の状態遷移（review_status='merged'） |
| candidate.mark_as_different_person(user, review_result, note=None) | インスタンスメソッド | 自身の状態遷移（review_status='different_person'） |
| candidate.record_different_person_action(user) | インスタンスメソッド | 自身の別人判定操作を ActionLog に記録 |

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
| PersonMergeLog.create(surviving_person, merged_person, user) | クラスメソッド | マージ実行のためのログレコードを作成 |
| PersonMergeLog.lock_past_logs(merged_person) | クラスメソッド | 過去のログを locked 状態に変更 |
| PersonMergeLog.get_for_person(person) | クラスメソッド | Person 単位のログ一覧取得 |
| PersonMergeLog.get_undoable(person) | クラスメソッド | 復元可能なログ取得 |
| merge_log.is_undoable() | インスタンスメソッド | 復元可能かどうかの判定 |
| merge_log.mark_as_undone(user) | インスタンスメソッド | 自身の状態遷移（status='undone'） |
| merge_log.record_merge_action(user) | インスタンスメソッド | マージ実行を ActionLog に記録（action='merged'） |
| merge_log.record_undo_action(user, note="") | インスタンスメソッド | 復元実行を ActionLog に記録（action='undone'、data に {"note": str} を保存） |
| merge_log.get_undo_preview() | インスタンスメソッド | 復元後の予測状態を返す（確認画面用） |

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
| ActionLog.record(user, action, content_object=None, object_repr='', data=None, note='') | クラスメソッド | 任意の業務イベントを直接記録（cron 実行ログなど、モデルインスタンスを持たない場面で使用） |

## 4.11 ActionLog と PersonMergeLog の関係

|  | PersonMergeLog | ActionLog |
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
| モデルインスタンスがある場合（マージ実行・別人判定・復元など） | インスタンスメソッド経由（merge_log.record_merge_action(user) 等） |
| モデルインスタンスがない場合（cron 実行ログなど） | ActionLog.record(...) クラスメソッド直接呼び |

### 4.11.3 ActionLog に記録する対象
ActionLog に記録する対象は、すべて仕様書で明示的に定める。実装者の判断で記録対象を追加することは認めない。
【v1.4.2 で ActionLog に記録する対象】
- マージ実行（Execute_Merge_Only）→ merge_log.record_merge_action(user) 経由
- 別人判定（Mark_as_Different_Person）→ candidate.record_different_person_action(user) 経由
- マージ復元（Execute_Merge_Undo）→ merge_log.record_undo_action(user, note) 経由（note は MergeUndoForm の cleaned_data["note"]、空文字でも {"note": ""} 形式で data に保存して集計時のキーを揃える）
- cron 重複チェック実行（Run_Generate_Duplicate_Candidates）→ ActionLog.record(...) 直接呼び
- OCR 処理結果（使用トークン数、処理時間、読み取り名刺枚数等のレスポンスメタデータ）→ ActionLog.record(...) 直接呼び
【ActionLog 以外で記録すべきもの】
- コーディング・デバッグ中の中間ログ → 標準出力（icecream / Django logging）またはファイルログを使う
- 処理途中の試行錯誤情報 → 標準出力 / ファイルログ
- 高頻度な処理の細かいトレース → 標準出力 / ファイルログ
これらは ActionLog の対象としない。
### 4.11.4 DB 障害時のフォールバック
ActionLog は DB 上のモデルであるため、DB 自体が障害時には ActionLog への書き込みも不可能になる。「障害時にこそログが必要」という根本的要請に応えるため、ActionLog 書き込み失敗時のフォールバック機構を用意する。
【フォールバックの動作】
- ActionLog のメソッドを呼ぶ
- DB 例外（接続不能・トランザクションエラー等）が発生
- ファイルログ（または標準出力ログ）に「ActionLog 書き込み失敗 + 対象 Person/Contact の ID + 試みた業務処理の種別」を障害記録として書き込む（業務データそのものではなく、障害発生情報を記録する）
- 元の業務処理は通常通り例外として伝播（マージ等は失敗扱い、DB はロールバック）
手順3で記録するのは「ActionLog 書き込みに失敗した障害情報」であり、「マージ実行内容」ではない。マージは手順4でロールバックされて実際には起こらないため、ファイルログに「マージ内容」を記録すると「成功したマージ」と混同される恐れがある。あくまで障害発生の記録として位置づける。
【記録先】
- 本番環境：別途定めるログファイル（ローテーション設定済み）
- 開発環境：標準出力（icecream や Django logging）
これにより、DB 障害時でも最低限の障害情報が残り、原因調査が可能になる。
## 4.12（欠番）DebugMask

旧 §4.12（DebugMask：OpenCV デバッグ用マスク画像 DB）は OpenCV デバッグ専用モデルのため v1.6.0 系 3 本に移行し本編から削除（欠番）。
# 旧 第 5 章 〜 第 7 章・第 15 章（OCR/OpenCV 関連仕様）

本書から OCR/OpenCV 関連仕様を引き剥がし、以下の v1.6.0 系 3 本に移行した。

- OpenCV・OCR 統合仕様：`OpenCV_OCR仕様書v1_6_1_Claude_API_統合版.md`
- OCR バックエンド指示書：`OpenCV_OCR仕様書v1_6_1_Claude_API_OCRプロンプト.md`
- JSON 構造・Contact フィールド対応表：`OpenCV_OCR仕様書v1_6_1_Claude_API_JSON構造_コンタクトフィールド対応表.md`

旧章節と移行先の対応：

- 旧 第 5 章 OCR 結果 JSON 仕様 → v1.6.1 統合版 第 1 部・JSON 構造対応表
- 旧 第 6 章 画像処理仕様 → v1.6.1 統合版 第 4 部（OpenCV パイプライン）
- 旧 第 7 章 OCR バックエンド仕様 → v1.6.1 統合版 第 2 部・OCR プロンプト
- 旧 §15.1〜§15.3, §15.6 OCR パイプライン → v1.6.1 統合版 第 3 部・第 4 部
- 旧 §15.4 新規 Contact 生成の 3 段階トランザクション → §10.4.3（Person.set_primary_contact）に責務統合
- 旧 §15.5 正規化ルール → §11.9 Contact 正規化基盤

なお、本書（FreeGroup2 本編仕様書）に残る正規化基盤の本体仕様は §11.9 を参照。

# 第8章 重複検出仕様
## 8.1 検出方針
Contact が新規作成または重複判定対象フィールドが更新された場合に、バックグラウンド処理で他 Contact との重複検出を実行する。検出はあくまで「候補としてリストアップする」までを担い、同一人物の最終判定はユーザーが行う（自動マージはしない）。
## 8.2 比較対象
重複検出の比較は、Person の主コンタクト（status='primary'）同士でのみ行う。副コンタクト（status='active'）と旧コンタクト（status='inactive'）、archived な Person は比較対象外とする。
理由：シンプルさ優先。1 人の Person を 1 つの代表 Contact で表現することで、重複判定ロジックがシンプルになる。副コンタクトを比較対象に含めるのは v1.5.0 以降で検討する。
## 8.3 スコア表
各フィールドの完全一致に対して、点数を加算する。両 Contact の confidence=high（DB 上の low / mid レコードは加算対象外、ただし high はデフォルト値のため大半のフィールドが加算対象になりうる）かつ正規化後の値が完全一致した場合のみ加算する。
スコア表とランク閾値は config/constants.py の DUPLICATE_FIELD_SCORES / DUPLICATE_SCORE_EMAIL_PERSONAL / DUPLICATE_SCORE_EMAIL_GENERIC / POSSIBLE_*_MIN_SCORE で管理する。運用後にチューニング可能とするため、定数化された設計とする。初期値は以下のとおり。

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

email は個人メール（DUPLICATE_GENERIC_EMAIL_LOCALPARTS に該当しないローカル部）と代表メール（該当するローカル部）で配点を分ける。判定は §8.7 のロジックでサービス層が行い、どちらの定数を使うかを決める。
合計スコアの 200 点到達例（参考）：
- mobile + email（個人）+ full_name = 80 + 80 + 40 = 200
- mobile + email（個人）+ company + department + address = 80 + 80 + 10 + 10 + 10 = 190（200 点に届かない）
- mobile + email（個人）+ full_name + company = 80 + 80 + 40 + 10 = 210
## 8.4 ランク判定
合計スコアと一致条件の組み合わせでランクを判定する。判定は以下のランクを exact_match → possible_high → possible_mid → possible_low の順に上から評価し、最初に該当した条件のランクを採用する。各ランクの「必須条件」内に列挙された条件はすべて AND 関係（すべて満たす必要がある）。

| ランク | 必須条件 |
|---|---|
| exact_match | 200点以上 AND 所属5フィールドが「両方一致」もしくは「両方空」 |
| possible_high | 200点以上（フルネーム不一致でも mobile + email + 所属系の加算で達成可能） |
| possible_mid | フルネーム一致 AND（email 一致 OR mobile 一致） |
| possible_low | 40〜119点 AND フルネーム一致 |
| none | 上記いずれにも該当しない |

ランク閾値の具体値（config/constants.py）：

| 定数 | 値 | 用途 |
|---|---|---|
| POSSIBLE_LOW_MIN_SCORE | 40 | possible_low の下限 |
| POSSIBLE_MID_MIN_SCORE | 120 | possible_mid のフォールバック上限（possible_low の上限 119 と接続） |
| POSSIBLE_HIGH_MIN_SCORE | 200 | possible_high / exact_match の下限 |

所属5フィールド：exact_match 判定の「両方一致 or 両方空」評価に用いる 5 項目。

| 所属5フィールド | コーディング名（`DUPLICATE_LOCATION_FIELDS`） |
|---|---|
| 会社名 | company |
| 部署 | department |
| 役職 | title |
| 支店 | branch |
| 住所 | address |

DUPLICATE_CHECK_FIELDS（9 フィールド）から個人系 4 項目（full_name / email / personal_phone / mobile_phone）を除いた残りが所属5フィールドに対応する。
### 8.4.1 ランク閾値の根拠
初期値は 40 点下限で同姓同名を含めて広く拾う設計とする。これにより、フルネーム一致のみのケースでも possible_low として候補化される。
理由：
- 初期段階では「拾い漏れ」より「ノイズ」のほうが対応しやすい。同姓同名を多く拾ってしまっても、ユーザーは「別人」判定すればよく、different_person 判定後はシステムが再候補化しない（8.9）ため、一度判定すればノイズは消える。逆に拾い漏れがあると「同一人物の可能性に気付けない」という機会損失になり、これは検知できない
- 運用データを見ないと最適な閾値は決まらない。机上で精緻に決めるより、運用後にチューニングする方が現実的
- スコア表とランク閾値は config/constants.py で管理されている設計。recheck_duplicates --all コマンドで全件再判定もできる（12.9）。運用後の調整を前提に設計しているので、初期値で精緻に詰める必要はない
同姓同名のレビュー件数が運用上多すぎる場合は、config/constants.py のランク閾値（POSSIBLE_LOW_MIN_SCORE 等）を運用データに基づき調整する。閾値変更時は recheck_duplicates --all コマンドで全件再判定する。
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
- v1.4.2 で表示対象を拡大：上記以外の Contact フィールドも値違いまたは片方空のフィールドのみ表示・選択対象とする（last_name / first_name / salutation_name / personal_fax / website / qualification / catchphrase / notes / postal_code / lang）。SNS は v1.6.1 で ContactSns 別テーブル化されたため、§11.5.7 の ContactSns 比較表示に従う
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
バリエーション例：info-jp@、sales_team@、info.jp@、sales.team@、support2@ なども代表メール扱い。
運用：config/constants.py の DUPLICATE_GENERIC_EMAIL_LOCALPARTS で管理。運用しながら追加可能。
### 8.7.1 代表メール判定リストの追加運用
リストは config/constants.py のソースコードとして管理する。追加・変更時はソースコード修正 → デプロイのフローを取る。動的な管理（Django Admin で UI から追加）は v1.5.0 以降で検討する。
リストに新しい値を追加した場合、過去に判定済みの DuplicateCandidate には影響しない。判定基準を遡及して見直したい場合は recheck_duplicates --all コマンド（12.9）で全件再判定する。
## 8.8 重複検出対象フィールド
重複検出のスコア計算に使うフィールドは、config/constants.py の DUPLICATE_CHECK_FIELDS で定数として管理する。同じ定数を ContactUpdateView の編集発火判定でも使用することで、整合性を保つ。

| DUPLICATE_CHECK_FIELDS（9 フィールド） |
|---|
| full_name, organization, department, title, branch, email, personal_phone, mobile_phone, address |

## 8.9 different_person 判定の永続性
ユーザーが「別人として確定（different_person）」と判定した組み合わせは、システムが再度候補として上げない。Contact 編集後も再検出しない（過去の判定を尊重）。
ユーザーが「やっぱり同一人物だった」と気づいた場合の手動再判定は、v1.4.2 では実装しない。将来の手動 DuplicateCandidate 作成機能（v1.5.0 以降）で対応する。
## 8.10 重複検出の効率化アルゴリズム
### 8.10.1 課題と方針
主コンタクト同士の重複検出を素朴に実装すると、N Contact に対して N×(N-1)/2 回の比較が発生する。N=5000 で約 1250 万回となり、現実的な時間で処理できない。
8.4 のランク判定を逆算し、possible_low 以上のランクになり得る候補だけを事前に DB で絞り込む ことで、calculate_score の呼び出し回数を劇的に減らす。
### 8.10.2 絞り込み条件
possible_low 以上の必須条件は以下のとおり。

| ランク | 必須条件 |
|---|---|
| possible_low | フルネーム一致 |
| possible_mid | フルネーム一致 + メール or 携帯一致 |
| possible_high | 200点以上（フルネーム不一致でもメール+携帯+所属で達成可能） |
| exact_match | 200点以上 + 所属5フィールド両方一致 or 両方空 |

つまり、フルネーム一致 / メール一致 / 携帯一致 のいずれも満たさない Contact は、possible_low 以上のランクにならない。
絞り込み条件：
- フルネーム完全一致（正規化後）
- メール完全一致（個人/代表問わず）
- 携帯番号完全一致
これらの OR 条件 で対象 Contact を絞り込む。
### 8.10.3 関数定義

| 項目 | 内容 |
|---|---|
| 関数名 | find_duplicate_contacts(contact) |
| 配置 | duplicates/services/duplicate_detection.py |
| 性質 | 準関数（DB 読み取りはするが書き込みなし） |
| 入力 | contact: Contact（重複チェック対象、自身も主コンタクトであること） |
| 出力 | list[tuple]：各要素は (duplicate_contact: Contact, score: int, rank: str) |
| 比較対象 | DB 全体の status='primary' かつ Person.status='active' な Contact（自身を除く） |
| 絞り込み | 上記の OR 条件 |
| ランク判定 | rank='none' の候補は戻り値に含めない |
| パフォーマンス | cron 経由（Run_Generate_Duplicate_Candidates）で呼ばれる場合、_calculate_score 内の get_field_confidences() による N+1 を防ぐため、候補取得時に prefetch_related('confidences') を必須とする。ContactCreateView からの呼び出しは 1 件ずつのため必須としない |

### 8.10.4 呼び出し元

| 呼び出し元 | 用途 | 戻り値の使い方 |
|---|---|---|
| Run_Generate_Duplicate_Candidates（タスク層） | cron による全件重複チェック | 各タプルから DuplicateCandidate を構築して DB 保存（bulk_create 推奨） |
| ContactCreateView（手動 Contact 作成時） | 警告ダイアログ表示 | 各タプルを画面に表示（DB 保存しない） |

2 つの呼び出し元で同じ関数を共有することで、判定基準の一貫性を保つ。
### 8.10.5 効率の見積もり
- N=5000 の DB
- フルネーム一致：通常 0〜数件（同姓同名がいる場合のみ）
- メール一致：通常 0〜1 件（個人メールはほぼユニーク）
- 携帯一致：通常 0〜1 件
- 平均：1 Contact あたり 0〜数件の絞り込み
calculate_score の呼び出し回数：素朴な実装で N-1 = 4999 回 → 効率化後 0〜数件
### 8.10.6 cron の件数制限との関係
仕様書 12.2 で --limit 100 がデフォルト。1 回の cron 実行で処理する Contact 件数が 100 件に制限されている。
100 件 × find_duplicate_contacts(contact) の処理時間（〜100ms）= 10 秒で 1 回の cron 実行が完了。5 分間隔の cron なら十分余裕。

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
- ユーザーが選択した surviving / merged を確定する
- バリデーション：surviving 側 Contact の DUPLICATE_CHECK_FIELDS が全 high であることを確認。additional_role の場合は merged 側 Contact も同条件を確認。条件を満たさない場合は、トランザクション開始前に処理を中断し、マージ画面に戻ってバリデーションエラーを表示（再入力を促す）
- マージ画面で修正・確認されたフィールドの値を Contact に反映（surviving 側、additional_role なら merged 側も）。Contact.updated_by = マージ実行ユーザー、Contact.updated_at = マージ実行時刻として記録
- 修正・確認されたフィールドの ContactFieldConfidence の confirmed_at / confirmed_by を記録（高扱いになる）
- PersonMergeLog 作成（status='undoable'）。note にはマージ画面の操作内容（フィールド変更・確認・上書きの履歴）と、ユーザー入力の補足記述を組み立てて記録
- merged_person の各 Contact を surviving_person に付け替え（person、previous_person、previous_status を記録、status を 9.4 の状態遷移に従って変更）
- merged_person の Person.status を 'merged' に変更、merged_into を surviving に設定
- 過去のマージログを locked に変更（merged_person を surviving とする undoable なログ）
- DuplicateCandidate の後処理として、12.8 の recover 処理を 9.3 のトランザクション内で実行する。値修正の有無に関係なく、`recover_duplicate_candidates` を呼び出す
- 当該マージの DuplicateCandidate を 'merged' に変更（review_status、review_result、reviewed_by、reviewed_at を記録）
【補足】手順 1 はユーザーがデフォルト推奨をそのまま使った場合と、明示的に切り替えた場合の両方をカバーする。手順 2 のバリデーションは、UI 側のバリデーションが破られた場合の最後の砦として機能する。手順 6 の状態遷移は確定後の merged 側に対して適用される。
【v1.4.2 補足：手順順序とサービス責務の明示】 マージ実行サービス（Execute_Merge_Only）は、上記手順を atomic 内で以下の順に呼ぶ：
- atomic 冒頭：CFC 確定処理 — surviving 側 primary_contact に紐づく confirmed_at IS NULL の CFC（未確認 low/mid）を ContactFieldConfidence.mark_fields_as_confirmed(surviving_primary, field_names, user) で一括 confirmed 化（マージ画面の確認 CB を ON でマージ実行した場合の CFC 反映を担保、Contact.fix() と同じパターン）
- バリデーション（手順 2）
- merged_person.transfer_contacts_to(surviving_person, merge_reason) 等の Contact 引き渡し
- merged_person.mark_as_merged(surviving_person) を呼ぶ
- candidate.mark_as_merged(user, review_result, note) を呼ぶ
- recover_duplicate_candidates(merged_person, surviving_person)（冪等性のための防御チェックのみ、§12.8.3 参照）
- surviving_person.duplicate_checked_at の更新
「mark_as_merged → recover」の順序を明示するのは、recover 関数の責務を「冪等性チェックのみ」に縮小し、状態変更の主体を呼び出し元（Execute_Merge_*）に集約する設計思想（§12.8.3）と整合させるため。マージ画面 UI の刷新と Execute_Merge_with_Updates 廃止に伴う §9.3.1 全面整理は、別途実施予定。
### 9.3.2 復元時の Person.primary_contact 同期
復元処理（9.5.2）では、Contact の status を previous_status に戻した後、Person.primary_contact の同期処理を実施する。Contact.status='primary' のものが Person.primary_contact と一致するように再同期する。同期処理は Person.set_primary_contact() インスタンスメソッド経由で実行する。
## 9.4 マージ前後のステータス遷移
マージ前後のサバイブ側・マージド側 Contact のステータス遷移、および previous_status / previous_person の記録ルールは、別添 PDF「マージ前後のコンタクトのステータス等まとめ.pdf」を正本とする。
配置：/docs/spec/マージ前後のコンタクトのステータス等まとめ.pdf（GitHub 管理 + Claude プロジェクトファイル）
PDF は merge_reason 別（merged 系 7 値 + different_person 系 3 値）に、サバイブ側パーソン・マージド側パーソンの各 Contact 群（プライマリー / アクティブ / インアクティブ）の status / previous_status / previous_person の遷移を表形式で示している。
### 9.4.1 サバイブ側パーソンに関する設計趣旨
サバイブ側に紐づいているコンタクトは、previous_person、previous_status の値の変更をしない。マージされたコンタクトの 1 つ前のマージ状態を保持し、マージされたことのないコンタクトは NULL のまま（既存の値は保持される）。
【補足】「変更しない」と「記録しない」は意味が異なる。サバイブ側 Contact の previous_* には、過去のマージで動いた履歴が既に入っている可能性がある。マージで status を変更した場合（修正ありで元 primary を inactive 化する場合）でも、previous_* は触らずそのまま保持する。Django の update_fields=['status'] を使うのが正しい実装パターン。
### 9.4.2 マージド側パーソンに関する設計趣旨
マージド側パーソンに紐づくすべてのコンタクト（primary / active / inactive）はサバイブ側パーソンへ付け替える。付け替え時、previous_person にマージ前の merged_person を、previous_status にマージ前の status を記録する。
### 9.4.3 additional_role の特殊挙動
merge_reason='additional_role' のとき、マージド側元 primary を inactive ではなく active（副コンタクト化）として残す。別肩書（副業など）としてサバイブ側に紐付けるため。
その他の Contact（マージド側元 active / 元 inactive、サバイブ側全 Contact）の挙動は他の merge_reason と同じ。
### 9.4.4 切り分け基準（Execute_Merge_Only に統一）
v1.4.2 改訂：マージ実行サービスは Execute_Merge_Only のみに統一する。Execute_Merge_with_Updates は廃止（D-3 系 Contact 詳細画面 AJAX 化に伴う設計大転換、§11.5 / §11.6.2 参照）。
- マージ画面では Contact のフィールド値修正を行わない（マージ画面に来る前に Contact 詳細画面で値修正済みの前提）
- すべてのマージは Execute_Merge_Only(candidate, surviving_person, merged_person, form, user) で処理
- merge_reason は MergeForm.get_merge_reason() から list[str] で受け取る（§11.6.2 / #58 参照）
【v1.4.2 改訂前】 v1.4.2 改訂前は「フィールド修正の有無」で Execute_Merge_Only / Execute_Merge_with_Updates を分岐していたが、マージ画面の値修正機能廃止により分岐自体が不要になった。
### 9.4.5 same_card かつコンタクト修正ありの特殊扱い（v1.4.2 で廃止）
v1.4.2 改訂前は Execute_Merge_with_Updates の merge_reason='same_card' 修正ありに対する特殊扱い（サバイブ側 primary を直接更新、新規 Contact 作らない、CFC 部分 confirmed 化）を定義していたが、Execute_Merge_with_Updates 廃止（§9.4.4）に伴い本特殊扱いも廃止。
【設計の移行】 same_card 系の値修正は、マージ画面に来る前に Contact 詳細画面（11 番、§11.3）で AJAX 経由で済ませる流れに変更。マージ画面に到達した時点で surviving 側 Contact のフィールドはすでに確定している状態となる。CFC 確定処理は Execute_Merge_Only の atomic 冒頭で一括 confirmed 化する形に置き換え（§9.3.1【v1.4.2 補足】参照）。
### 9.4.6 副コンタクト増加問題
additional_role を多用すると、サバイブ側に多数の active 副コンタクトが紐づく可能性がある。v1.4.2 ではこの問題への対応として：
- 副コンタクトの増加は仕様上仕方がない
- inactive という仕組みがあるので、運用で手動 inactive 化で対応
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
| 5 | merged_person.mark_as_active() を呼ぶ（status='active' / merged_into=NULL、§10.4.1 参照） |
| 6 | Person.primary_contact の同期処理（Contact.status='primary' のものが Person.primary_contact と一致するように再同期、Person.set_primary_contact() 経由） |
| 7 | PersonMergeLog.status を 'undone' に変更、undone_by、undone_at を記録 |
| 8 | merged_person.primary_contact.duplicate_checked_at = None をセット（次回 cron で再判定対象にするため） |

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
v1.4.2 ではアクティブコンタクトを primary に昇格させる機能（およびその逆）を実装しない。今後も議論しない。
### 設計趣旨
- DuplicateCandidate の整合性が崩れる：primary 同士で重複検知している（8.2）ため、primary が入れ替わると検知済みの候補が無効になる
- ユーザー視点での混乱：「どっちが本業？」を頻繁に切り替える運用は混乱を招く。固定された primary で運用する方が業務フローが安定する
- `set_primary_contact()` の責務には含まれない：第10章で確定した責務範囲は「旧 primary を old_primary_new_status に降格、新 primary を昇格」であり、能動的な入れ替えは想定外
### 将来検討
副コンタクト関連の機能拡張（副コンタクトの inactive 化機能、整理機能）は v1.5.0 以降で検討する余地は残す。ただし入れ替え機能はコア設計に影響するため、慎重な検討が必要。

# 第10章 Django モデルメソッド体系
## 10.1 設計の出発点
v1.4.1 までは merge_helpers.py などのサービス層関数で「マージ実行時の各種処理」を担っていたが、議論メモで「これくらいの処理だと、普通にモデル使って書いた方が分かりやすい」という問題提起があった。
サービス層関数の責務が中途半端で、関数化のメリット（複雑な処理を名前で抽象化）が活きていない箇所がある。Django のモデルメソッドとして「自分自身の状態を変える処理」を表現する方が自然な箇所がある、という気づきに基づき、v1.4.2 では以下のモデルメソッド化を実施する。
merge_helpers.py ファイル全体を削除し、共通ヘルパー関数群を各モデルの責務に応じてモデルメソッド化する。
## 10.2 モデルメソッド化の判断基準
### 10.2.1 核心：「FK 保有はモデル横断ではない、他モデル状態変更が真の横断」
判断基準：
- 自己完結する状態遷移：自分自身のフィールドを更新するだけ → モデルメソッド化（インスタンスメソッド）
- 自モデル集合操作：自モデルのレコード群を一括更新 → モデルメソッド化（クラスメソッド）
- 真のモデル横断：他モデルの状態を実際に変更する処理 → サービス層関数
### 10.2.2 例
- PersonMergeLog.create()：自分のレコードを作るだけ → クラスメソッド
- PersonMergeLog.lock_past_logs(merged_person)：自モデルのレコード群を一括更新 → クラスメソッド
- merge_log.mark_as_undone(user)：自分自身のフィールドを更新 → インスタンスメソッド
- DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)：自モデルの新規レコード作成 → クラスメソッド
- recover_duplicate_candidates(merged_person, surviving_person)：DuplicateCandidate 横断＋ Person・Contact のフィールド更新 → サービス層関数
### 10.2.3 FK 保有とモデル横断の違い
PersonMergeLog は FK で Person を 2 つ持つが、自分のレコードを作るだけで Person 側のフィールドには触らない。これは「自己完結」であり「モデル横断」ではない。
一方、recover_duplicate_candidates は DuplicateCandidate を作りながら Person.duplicate_checked_at を更新するなど、複数モデルの状態を実際に変更する。これが「真のモデル横断」であり、サービス層に置くべき処理。
## 10.3 派生情報の同期はモデルメソッド化が許される例外
### 10.3.1 核心：「FK だけをいじる」「派生情報の同期」「整合性確保の責任が自モデル側にある」場合
許容条件：
- 関連モデルの FK と派生情報のみを変更し、関連モデル独自の状態遷移は含まない
- 整合性確保（partial unique constraint 等）の責任が自モデル側にある
- 他のサービス層関数を経由するより、自モデルのメソッドとして書いた方が読みやすい
### 10.3.2 適用例
- Person.set_primary_contact(new_contact, old_primary_new_status='active')：旧 primary_contact の status を old_primary_new_status に、新 primary_contact の status を 'primary' に変更し、Contact.person FK の付け替えと Person.primary_contact の更新を行う。Contact 側の status は派生情報なので、Person 側のメソッドで同期させる
- person.transfer_contacts_to(surviving_person, merge_reason)：merged_person の各 Contact を surviving_person に付け替える。Contact 側の person FK と previous_* は派生情報なので、Person 側のメソッドで同期させる
## 10.4 Person のモデルメソッド詳細
### 10.4.1 インスタンスメソッド
Person の状態遷移を表すインスタンスメソッドは `mark_as_*` シリーズ で命名を揃える設計。mark_as_merged ↔ mark_as_active は対称ペアとして隣接配置する（PersonMergeLog の mark_as_undone() 等とも命名スタイルが揃う）。

| メソッド | 責務 | 配置先 |
|---|---|---|
| person.mark_as_merged(surviving_person) | 自身の状態遷移（status='merged' / merged_into=surviving_person / primary_contact=NULL） | persons/models.py |
| person.mark_as_active() | 自身の状態遷移（status='active' / merged_into=NULL）。マージ復元処理（§9.5.2）で merged → active に戻す際に呼ぶ。archived → active も汎用化（archived 中は対象 Person を誰も触れないため安全）。primary_contact の復元は set_primary_contact() 側で同期させるため、本メソッドには含めない | persons/models.py |
| person.transfer_contacts_to(surviving_person, merge_reason) | 自身のコンタクト群を surviving に引き渡す。merge_reason は list[str]（DuplicateMergeReason value のリスト、複数可）。Case A〜D（§9.4）のステータス遷移を適用、全 Contact 対象：primary / active / inactive すべて。詳細は §10.4.1.1 参照 | persons/models.py |
| person.set_primary_contact(new_contact, old_primary_new_status='active') | 既存 Person の primary_contact 切り替え（派生情報の同期） | persons/models.py |
| person.get_active_contacts() | status='active' の Contact 一覧を返す | persons/models.py |
| person.get_inactive_contacts() | status='inactive' の Contact 一覧を返す | persons/models.py |

### 10.4.1.1 person.transfer_contacts_to() の詳細仕様

| 観点 | 記述内容 |
|---|---|
| 引数 | surviving_person（マージ先 Person）、merge_reason: list[str]（DuplicateMergeReason.values の部分集合、空リストは不可） |
| 対象 | 自 Person に紐づく全 Contact（status=primary / active / inactive すべて） |
| 処理 | merge_reason に応じて Case A〜D（§9.4）のステータス遷移を適用 |
| Case A | same_card 等：直接更新パターン（§9.4 / 別添 PDF 参照） |
| Case B | transfer / promotion / job_change / name_change / other_merged：標準的な引き渡しパターン（旧 primary は inactive、副コンタクト群も引き渡し） |
| Case C | additional_role：別肩書追加の特殊パターン。マージド側 primary を一時的に active に降格してから引き渡し、サバイブ側 primary は維持。partial unique constraint（Person.primary_contact が高々 1 件）違反を避ける順序制御を内部で行う |
| Case D | 復元時：previous_* を NULL にする不変原則を保つ（§9.4 参照） |
| 制約 | partial unique constraint 違反を避けるため、引き渡し順序を内部的に制御する |
| additional_role 判定 | DuplicateMergeReason.ADDITIONAL_ROLE in merge_reason で判定（複数選択可なので in 比較を使う） |

【サバイブ側 previous_ 不変原則】`transfer_contacts_to` はマージド側 Person の Contact を引き渡す処理であり、サバイブ側 Person の Contact の previous_ フィールドには一切触れない（業務所有権の分離）。
詳細な状態遷移は §9.4 および別添 PDF『マージ前後のコンタクトのステータス等まとめ.pdf』を参照。
### 10.4.2 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| Person.get_active() | status='active' の Person 一覧を返す（PersonListView 用） | persons/models.py |
| Person.get_archived() | status='archived' の Person 一覧を返す（将来の archived 一覧画面用） | persons/models.py |

### 10.4.3 Person.set_primary_contact() の詳細仕様
### 処理内容
- 旧 primary_contact の status を old_primary_new_status で指定された値に変更
- 新 primary_contact の status を 'primary' に変更
- Contact.person FK の付け替え（new_contact が他 Person 配下なら surviving_person に付け替える）
- Person.primary_contact = new_contact に更新
### old_primary_new_status の値

| 値 | 旧 primary の遷移先 | 使用場面 |
|---|---|---|
| 'active' | active（副コンタクト化） | デフォルト値として保持。v1.4.2 時点では実装上の使用場面なし（呼び出し元はすべて 'inactive' を明示。将来の拡張余地として API は維持） |
| 'inactive' | inactive（過去情報化） | 修正画面 transfer / promotion / job_change / name_change、マージ画面 transfer 等 |

### 呼ばれる場所
- 修正画面 ContactUpdateView（change_reason='transfer' / 'promotion' / 'job_change' / 'name_change' のとき）：person.set_primary_contact(new_contact, old_primary_new_status='inactive')
- 新規 Person 作成時（contacts/views.py _create_person_and_contact 内）：person.set_primary_contact(contact)（デフォルト引数で呼ぶが、旧 primary が存在しないため status 変更ステップはスキップされ、old_primary_new_status の値は実質不使用）
change_reason='fix' の場合は contact.fix(form, user) で既存 Contact を上書きするため set_primary_contact は呼ばない（§11.4.1 修正理由による処理分岐を正とする）。
### 設計趣旨
修正画面の transfer 等とマージ画面の transfer 等で、コードの形が揃う：
- 両方とも set_primary_contact(new_contact, old_primary_new_status='inactive') を呼ぶだけ
- 「旧 primary を active 化 → 直後に inactive 上書き」という不自然な順序がなくなる
- 引数を見れば旧 primary の遷移先が一目で分かる
### 10.4.4 設計趣旨（Person のモデルメソッド全般）
mark_as_merged / transfer_contacts_to / set_primary_contact は、関連モデル（Contact）の FK と派生情報のみを変更し、関連モデル独自の状態遷移は含まない。Person.primary_contact が「正本」、Contact.status='primary' が「派生情報」とする設計に基づき、これらの同期処理は Person の責務として置く（10.3 派生情報の同期はモデルメソッド化が許される例外、参照）。
person.get_primary_contact() は採用しない。person.primary_contact（FK 直接参照）で 1 行で済む処理を間接化する意義が薄いため。
## 10.5 Contact のモデルメソッド詳細
### 10.5.1 インスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| contact.fix(form: ContactUpdateForm, user) | フォーム値で自身のフィールドを上書きし、全 ContactFieldConfidence を confirmed 化する | contacts/models.py |
| contact.get_field_confidences() | 全フィールドの ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス、DB 保存しない） | contacts/models.py |
| contact.get_high_fields() | 実質 high なフィールド集合を返す（疑似 high または confirmed_at が記録されたものは high 扱い） | contacts/models.py |
| contact.is_all_field_confidence_high(fields=None) | 全 high 判定（引数省略時は全フィールド、指定時は範囲限定） | contacts/models.py |

### 10.5.2 contact.fix() の詳細仕様
### シグネチャ
contact.fix(form: ContactUpdateForm, user) -> None
form 引数の型は ContactUpdateForm に限定（MergeForm は受け付けない）。型ヒントから「fix 画面専用」が読める。
### 責務
- ガード：self.pk が None の場合エラー（save 済みの Contact のみ受け付ける）
- フォーム値で自身のフィールドを上書き：差分のあるフィールドのみ更新、save(update_fields=changed_fields) で限定 save
- 全 ContactFieldConfidence を confirmed 化：ContactFieldConfidence.mark_fields_as_confirmed(self, all_low_mid_field_names, user) を呼ぶ
### 呼ばれる場所
- 12 番 UpdatePrimaryContactView（change_reason='fix' のとき）
- 13 番 UpdateActiveContactView（change_reason フィールドなし、fix 相当の処理に固定）
マージ画面では contact.fix を呼ばない。Contact のフィールド値修正は事前に Contact 詳細画面（11 番）で AJAX 経由で済ませている前提（§11.5.5 / §11.6.2 / ストック #20 廃止系参照）。マージ画面到達時の CFC 確定処理は Execute_Merge_Only の atomic 冒頭で一括 confirmed 化される（§9.3.1【v1.4.2 補足】/ ストック #57 参照）。
### 10.5.3 contact.get_field_confidences() の戻り値仕様
### 戻り値の形式

```python
{
    'full_name': <ContactFieldConfidence: confidence='high' (疑似)>,
    'organization': <ContactFieldConfidence: confidence='mid', confirmed_at=None>,
    'title': <ContactFieldConfidence: confidence='mid', confirmed_at=2026-05-04>,
    'email': <ContactFieldConfidence: confidence='low', confirmed_at=None>,
    'address': <ContactFieldConfidence: confidence='high' (疑似)>,
    ...
}
```

全フィールドのキーが含まれる。high のフィールドは ContactFieldConfidence の疑似インスタンス（DB 保存しない）として生成して返す。
### 実装責務の分離
Contact 側は薄いラッパーとして ContactFieldConfidence.get_for_contact(self) を呼ぶだけ。実ロジックは ContactFieldConfidence 側に置く。
### 3 状態の判定（テンプレート・カスタムタグ側）

| 状態 | 判定ロジック | 表示例 |
|---|---|---|
| 1. high（確定） | conf.confidence == 'high' | 「高」 |
| 2. high扱い（確認済み） | conf.confidence in ('low', 'mid') and conf.confirmed_at != None | 「確認済み」 |
| 3. mid/low 未確認 | conf.confidence in ('low', 'mid') and conf.confirmed_at == None | 「要確認」 |

### メリット
- インターフェースが統一される（テンプレート・サービス層・カスタムタグすべて ContactFieldConfidence インスタンスを扱う）
- 拡張性が高い（confirmed_at / confirmed_by 等の既存フィールドもそのまま使える）
- 「ContactFieldConfidence は Contact のメタデータ」という設計思想に最も忠実
### 10.5.4 採用しなかったメソッド案
- Contact.transfer_to()：Person 主語に変更したため不採用。「merged_person よ、お前のコンタクトを surviving に渡せ」と Person 主語で命令する形が自然（Tell, Don't Ask 原則）。公開 API は merged_person.transfer_contacts_to(surviving_person, merge_reason) で統一
- Contact.save_with_confirmation()：「確認されたフィールドのリスト」を引数で受け取る必要があり、Form の状態を Contact が知ることになる（責務混在）。マージ画面でしか使われない汎用性の低いメソッド。代わりに ContactFieldConfidence.mark_fields_as_confirmed(contact, field_names, user) クラスメソッドを採用
## 10.6 ContactFieldConfidence のモデルメソッド詳細
### 10.6.1 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| ContactFieldConfidence.get_for_contact(contact) | 全フィールド分の ContactFieldConfidence インスタンス dict を返す（high は疑似インスタンス） | contacts/models.py |
| ContactFieldConfidence.create_for_contact(contact, confidence_map) | OCR 結果の mid/low フィールドについて一括作成 | contacts/models.py |
| ContactFieldConfidence.mark_fields_as_confirmed(contact, field_names, user) | 指定フィールドを確認済み化（マージ画面・修正画面で使用） | contacts/models.py |

### 10.6.2 疑似インスタンスの防御策
get_for_contact() は high のフィールドについて疑似インスタンスを返すが、これが誤って save() されると DB に high レコードが入ってしまい仕様書 4.6 の「high は記録対象外」が破れる。また mid/low の既存レコードを誤って上書きすると confirmed_at 等の確認履歴が壊れる。
これを防ぐため、以下の三重防御を実装する。
- CheckConstraint（DB 制約）：confidence='high' のレコード保存を物理的に禁止
- save() オーバーライド（アプリケーション層）：confidence='high' で save() が呼ばれた場合、明示的なエラーメッセージで誤用を検出
- 仕様書ルールでの mid/low レコードの保護：ContactFieldConfidence.save() を直接呼ぶことは禁止する。新規作成は create_for_contact()、確認済み化は mark_fields_as_confirmed() 経由でのみ行う
### 10.6.3 設計趣旨
書き込み系（自モデルのレコード作成・更新）は ContactFieldConfidence のクラスメソッドとして配置。読み取り系（Contact のフィールドごとの信頼度を取得）は Contact のモデルメソッド（get_field_confidences() 等）として配置。実装は ContactFieldConfidence 側のクラスメソッド get_for_contact() に委譲する。
理由：ContactFieldConfidence は概念的に Contact のメタデータの一部であり、読み取り側は Contact から自然にアクセスできるべき。書き込み側は ContactFieldConfidence 自身の責務として、自モデル集合操作（クラスメソッド）が自然。
### 10.6.4 ContactFieldConfidence の生成・更新タイミング（3 ケース別）
ユーザー入力は全 high で信頼するため、ContactFieldConfidence は OCR で取り込まれた Contact のみで作成される。3 ケース別の整理は以下のとおり。
### ケース 1：新規作成（10 番 ContactCreateView / 9 番 PersonAddAdditionalRoleView）
- ContactFieldConfidence は作成しない（ユーザー入力なので全 high 扱い）
- DB レコード数が減り、コード君の実装が単純化される
### ケース 2：既存修正（12 番 fix / 13 番 active 修正、contact.fix(form, user)）
- 既存の low/mid フィールドの ContactFieldConfidence は mark_fields_as_confirmed() で全 confirmed 化（confirmed_at / confirmed_by を記録）
- 新規に ContactFieldConfidence を作成することはない（既存レコードの更新のみ）
### ケース 3：マージ実行時の CFC 確定処理（17 番 Execute_Merge_Only の atomic 冒頭）
【v1.4.2 改訂】 v1.4.2 改訂前は「マージ画面 same_card 特殊処理（旧 Execute_Merge_with_Updates で merge_reason='same_card' かつ修正あり）」として部分 confirmed 化を定義していたが、マージ画面の値修正機能廃止と Execute_Merge_with_Updates 統合（§9.4.4 / §9.4.5 / ストック #20 廃止系）に伴い、本ケースを以下に書き換える。
- マージ画面に到達した時点で、surviving 側 Contact の値修正は Contact 詳細画面（11 番、AJAX）で済ませている前提（D-3 系）
- Execute_Merge_Only の atomic 冒頭で、surviving 側 primary_contact に紐づく confirmed_at IS NULL の低/中信頼度 CFC を ContactFieldConfidence.mark_fields_as_confirmed() で一括 confirmed 化する（マージ画面の確認 CB を ON でマージ実行した場合の CFC 反映を担保、§9.3.1【v1.4.2 補足】/ ストック #57 参照）
- 個別の値違いフィールドに対する部分 confirmed 化は行わない（Contact 詳細画面の AJAX 個別確認で対応する経路に置換）
### ContactFieldConfidence が作成される唯一の場面
OCR で取り込まれた Contact のみ、Claude の confidence 判定により low/mid/high が混在する。low/mid のフィールドだけ ContactFieldConfidence レコードが作成される（high は記録対象外）。
## 10.7 DuplicateCandidate のモデルメソッド詳細
### 10.7.1 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| DuplicateCandidate.get_pending(contact) | contact が紐づく Person の pending 候補を取得 | duplicates/models.py |
| DuplicateCandidate.get_merged(contact) | contact が紐づく Person の merged 候補を取得（マージ履歴表示用） | duplicates/models.py |
| DuplicateCandidate.get_different_person(contact) | contact が紐づく Person の different_person 候補を取得 | duplicates/models.py |
| DuplicateCandidate.get_invalidated(contact) | contact が紐づく Person の invalidated 候補を取得（開発・デバッグ用） | duplicates/models.py |
| DuplicateCandidate.has_duplicates(contact, status) | 指定 status の候補が存在するかどうかの判定（True/False） | duplicates/models.py |
| DuplicateCandidate.get_by_group(group_id) | group_id 単位で取得（レビュー画面の PRG パターン用） | duplicates/models.py |
| DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person) | old_candidate からスコア・ランク・group_id 等をコピーして新規 DuplicateCandidate を作成（review_status='pending'） | duplicates/models.py |

### 10.7.2 インスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| candidate.mark_as_merged(user, review_result, note) | 自身の状態遷移（review_status='merged' / review_result / reviewed_by / reviewed_at / note） | duplicates/models.py |
| candidate.mark_as_different_person(user, review_result, note=None) | 自身の状態遷移（review_status='different_person' / reviewed_by / reviewed_at / note） | duplicates/models.py |
| candidate.record_different_person_action(user) | 自身の別人判定操作を ActionLog に記録（action='different_person'、data に判定理由を格納） | duplicates/models.py |

### 10.7.3 設計趣旨
get_pending / get_merged / get_different_person / get_invalidated はクエリセットを返す設計とする。呼び出し側で .count() や .filter() を追加して柔軟に絞り込める。
引数は contact で統一。Contact → Person 変換は内部で行う。これにより、CardListView / ContactDetailView 等から直接 contact を渡せて呼び出し側がシンプルになる。
get_by_group(group_id) のみ引数が group_id。レビュー画面（DuplicateCandidateGroupUpdateView）が group_id 単位で動くため。
create_recovered_from(old_candidate, new_surviving_person) クラスメソッドは、recover 処理での DuplicateCandidate 新規作成を、merge_executor.py 内で直接 DuplicateCandidate.objects.create() を呼ぶのではなく、本クラスメソッド経由で行う。これにより「old_candidate からスコア・ランク・group_id 等をコピーして新規作成する」処理ロジックが DuplicateCandidate モデル側に集約され、関数名から意図が読める。
candidate.record_different_person_action(user) の命名は、状態遷移メソッド mark_as_merged / mark_as_different_person との対称性、PersonMergeLog 側の merge_log.record_merge_action(user) / merge_log.record_undo_action(user) との一貫性、将来 DuplicateCandidate に新たな記録メソッドが追加された場合の拡張性を考慮した命名である。
### 10.7.4 開発時のデバッグ画面での活用
開発時には get_invalidated を含む各 status の取得メソッドを画面表示で活用する。たんたんの方針として「各ビューの画面にデバッグ時は自分も UI 上で値確認したい」という運用方針があり、開発フェーズで重要な役割を果たす。
## 10.8 PersonMergeLog のモデルメソッド詳細
### 10.8.1 クラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| PersonMergeLog.create(surviving_person, merged_person, user) | マージ実行のためのログレコードを作成（インスタンス生成＋save() を一気に実行）。duplicate_candidate / note 等は呼び出し側で追加設定 | duplicates/models.py |
| PersonMergeLog.lock_past_logs(merged_person) | 過去のログを locked 状態に変更(自モデル集合操作) | duplicates/models.py |
| PersonMergeLog.get_for_person(person) | Person 単位のログ一覧取得（マージログ一覧画面用） | duplicates/models.py |
| PersonMergeLog.get_undoable(person) | 復元可能なログ取得 | duplicates/models.py |

### 10.8.2 インスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| merge_log.is_undoable() | 復元可能かどうかの判定（status='undoable' なら True） | duplicates/models.py |
| merge_log.mark_as_undone(user) | 自身の状態遷移（status='undone' / undone_by / undone_at） | duplicates/models.py |
| merge_log.record_merge_action(user) | マージ実行を ActionLog に記録（action='merged'、data に surviving/merged Person 情報、duplicate_candidate ID 等） | duplicates/models.py |
| merge_log.record_undo_action(user, note="") | 復元実行を ActionLog に記録（action='undone'、data に {"note": str} 形式で MergeUndoForm から受け取った備考を保存。空文字でも {"note": ""} で記録し、集計時のキーを揃える） | duplicates/models.py |
| merge_log.get_undo_preview() | 復元後の予測状態を返す（確認画面表示用：復元 Person・復元 Person に戻る Contact の集合・surviving 側 Person に残る Contact の集合） | duplicates/models.py |

### 10.8.3 merge_log.get_undo_preview() の戻り値設計
PersonMergeLogConfirmUndoView（復元確認画面）で「現在の状態と復元後の予測状態を表示する」ために使う。
戻り値は dict：

| キー | 値 |
|---|---|
| merged_person | Person |
| contacts_to_restore | QuerySet[Contact]（merged_person に戻る Contact の集合） |
| contacts_remaining_in_surviving | QuerySet[Contact]（surviving 側に残る Contact の集合） |

UI 側でこの dict を加工して表示する。実際の DB 変更は行わない（プレビューのみ）。
### 10.8.4 設計趣旨
PersonMergeLog.create() は「マージ用ログレコードを作成する」処理を 1 メソッドに集約。呼び出し側は merge_log = PersonMergeLog.create(surviving_person, merged_person, user) の 1 行で完結し、その後 merge_log.duplicate_candidate = candidate / merge_log.note = note を設定して保存する流れ。
ActionLog 記録メソッドの 2 分離（record_merge_action / record_undo_action）について：
- マージ実行時と復元実行時で記録するアクション内容が異なるため、PersonMergeLog のインスタンスメソッドを 2 つに分離
- インスタンス側（record_*_action）：自モデルの状態を ActionLog に記録する責務
- クラス側（ActionLog.record(...)）：任意の業務イベントを直接記録する責務（cron 実行ログなど、モデルインスタンスを持たない場面）
- 両者でメソッド名を変えているのは、インスタンス側は「自分の操作を記録する」ニュアンス、クラス側は「汎用的に記録する」ニュアンスを区別するため
状態遷移と ActionLog 記録は分離（一体化しない）：
- mark_as_*() は状態遷移だけ
- record_*_action() はログ記録だけ
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
| ActionLog.record(user, action, content_object=None, object_repr='', data=None, note='') | 任意の業務イベントを直接記録（cron 実行ログなど、モデルインスタンスを持たない場面で使用） | actionlogs/models.py |

### 10.9.2 設計趣旨
ActionLog の書き込みは 2 通り：
- モデルインスタンスがある場合：インスタンスメソッド経由（merge_log.record_merge_action(user) / candidate.record_different_person_action(user) 等）
- モデルインスタンスがない場合：ActionLog.record(...) クラスメソッド直接呼び（cron 実行ログ、OCR 処理結果など）
詳細は第4章 4.11.2 を参照。
## 10.10 OriginalImage / BusinessCard のモデルメソッド詳細
### 10.10.1 OriginalImage のクラスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| OriginalImage.get_pending(limit) | pending な OriginalImage を limit 件取得（cron 用） | cards/models.py |
| OriginalImage.release_stuck_locks(threshold_minutes) | stuck な processing レコードを pending に戻す | cards/models.py |

### 10.10.2 OriginalImage のインスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| original_image.get_image_url() | サムネイル用 URL を返す | cards/models.py |
| original_image.get_image_url_full() | フルサイズ用 URL を返す | cards/models.py |

### 10.10.3 BusinessCard のインスタンスメソッド

| メソッド | 責務 | 配置先 |
|---|---|---|
| business_card.get_card_image_url() | サムネイル用 URL を返す | cards/models.py |
| business_card.get_card_image_url_full() | フルサイズ用 URL を返す | cards/models.py |

## 10.11 各モデルメソッドの View からの呼び出し関係
各 View で使用するモデルメソッド・カスタムタグの一覧。

| View / 起動契機 | 使用するメソッド・タグ |
|---|---|
| CardListView | DuplicateCandidate.get_pending(contact) / business_card.get_card_image_url() / {% card_image %} |
| CardDetailView | business_card.get_card_image_url() / business_card.get_card_image_url_full() / {% ocr_result_badge %} / <andypf-json-viewer>（raw_json_1 / raw_json_2 表示） |
| OriginalListView | original_image.get_image_url() / {% original_image_thumbnail %} |
| OriginalDetailView | original_image.get_image_url() / original_image.get_image_url_full() / <andypf-json-viewer>（debug_json 表示） |
| PersonListView | Person.get_active() / DuplicateCandidate.get_pending(contact) |
| PersonDetailView | person.get_active_contacts() / person.get_inactive_contacts() / DuplicateCandidate.get_pending(contact) / PersonMergeLog.get_for_person(person) |
| ContactDetailView | contact.get_field_confidences() / DuplicateCandidate.get_pending(contact) / {% contact_confidence %} |
| ContactCreateView | find_duplicate_contacts(contact) |
| UpdatePrimaryContactView（12 番） | contact.fix(form, user)（fix の場合）/ Person.set_primary_contact()（transfer 等の場合）/ ContactFieldConfidence.mark_fields_as_confirmed() |
| UpdateActiveContactView（13 番） | contact.fix(form, user)（fix 相当の処理に固定） |
| PersonAddAdditionalRoleView（9 番） | View 直書き（save 済み Contact が前提でないため、set_primary_contact() は使えない、10.12 参照） |
| DuplicateCandidateGroupListView（15 番） | DuplicateCandidate.get_pending(contact) / DuplicateCandidate.get_by_group() |
| DuplicateCandidateGroupDetailView（16 番） | 当該グループの DuplicateCandidate を review_status ごとに集計 |
| DuplicateCandidateGroupUpdateView（17 番） | Execute_Merge_Only() / Mark_as_Different_Person() / MergeForm.get_merge_reason() / MergeForm.hidden_name_fields() / MergeForm.has_confirm_checkboxes() / contact.get_field_confidences() |
| PersonMergeLogListView（19 番） | PersonMergeLog.get_for_person() / PersonMergeLog.get_undoable() |
| PersonMergeLogDetailView（20 番） | merge_log.is_undoable() |
| PersonMergeLogConfirmUndoView（21 番） | Execute_Merge_Undo() / merge_log.get_undo_preview() |

## 10.12 別肩書追加画面（9 番）の処理は View 直書き
別肩書追加画面の処理はメソッド化せず、View 内で直書きする。
### 理由
- v1.4.2 で active として新規 Contact を Person に紐付ける処理は、別肩書追加画面（9 番 PersonAddAdditionalRoleView）でしか発生しない
- set_primary_contact() と引数の前提が違う（set_primary_contact() は save 済み Contact が前提、9 番では pk なしの新規 Contact）ので、メソッドを並べるとコード君が迷う
- View 直書きでも 3〜4 行で完結する
### 処理内容
- フォーム値で新規 Contact を生成（pk なし）
- new_contact.person = person
- new_contact.status = 'active'
- new_contact.save() で DB に保存
- ContactFieldConfidence は作らない（ユーザー入力なので全 high、10.6.4 参照）

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
| cards/views.py | CardListView、CardDetailView、CardDeleteView、OriginalListView、OriginalDetailView、OriginalImageUploadView |
| cards/urls.py | /cards/<uuid:pk>/delete/ を含む URL ルーティング（name='card_delete' 等） |
| cards/templatetags/__init__.py | カスタムタグ用パッケージ初期化 |
| cards/templatetags/ui_tags.py | UI 系カスタムタグ（ocr_result_badge 等、§11.8 参照） |
| persons/models.py | Person モデル |
| persons/views.py | PersonListView、PersonDetailView、PersonAddAdditionalRoleView |
| contacts/models.py | Contact モデル、ContactFieldConfidence モデル |
| contacts/views.py | ContactListView、ContactCreateView、ContactDetailView、UpdatePrimaryContactView、UpdateActiveContactView、PreviewContactView |
| contacts/urls.py | /contacts/ 配下の URL ルーティング（list / create / detail / update-primary / update-active / preview 等） |
| contacts/forms.py | ContactBaseForm、ContactUpdateForm、ContactUpdateActiveForm、ContactAddAdditionalRoleForm、ContactCreateForm |
| templates/contacts/_contact_field.html | Contact フィールド表示・編集の共通 include パーツ。ContactDetailView と CardDetailView が再利用 |
| templates/contacts/_inactive_contacts.html | inactive Contact 履歴セクションの共通 include。contacts / persons 両方から再利用（ContactDetailView マージ関連セクション内、PersonDetailView merged / archived から） |
| contacts/services/normalization.py | フィールド正規化（純関数） |
| contacts/services/json_parser.py | raw_json → Contact 用辞書（v1.3.4 の json_normalizer から移動・拡張） |
| duplicates/models.py | DuplicateCandidate、PersonMergeLog モデル |
| actionlogs/models.py | ActionLog モデル |
| duplicates/views.py | DuplicateCandidateGroupListView、DuplicateCandidateGroupDetailView、DuplicateCandidateGroupUpdateView、PersonMergeLogListView、PersonMergeLogDetailView、PersonMergeLogConfirmUndoView |
| duplicates/forms.py | MergeForm、MergeUndoForm |
| duplicates/services/duplicate_detection.py | find_duplicate_contacts、_calculate_score、_determine_rank、determine_base_person |
| duplicates/services/merge_executor.py | Mark_as_Different_Person、Execute_Merge_Only、Execute_Merge_Undo、recover_duplicate_candidates、invalidate_pending_candidates（v1.4.2 で Execute_Merge_with_Updates は廃止し Execute_Merge_Only に統合、§9.4.4 参照） |
| duplicates/tasks/duplicate_check_runner.py | generate_duplicate_candidates_for_contact（タスク層下位関数） |
| cards/management/commands/check_duplicates.py | cron 起動。Run_Generate_Duplicate_Candidates 呼び出し |
| cards/management/commands/recheck_duplicates.py | 全 Contact の duplicate_checked_at リセット |
| cards/management/commands/dev_reset_duplicates.py | 開発用 DuplicateCandidate リセット |
| templates/contacts/ ほか各テンプレート | 各画面のテンプレート |

duplicates/services/merge_helpers.py は v1.4.2 で全削除する（モデルメソッド化により不要）。
## 11.3 URL 一覧表
各 URL とその役割・内部処理を以下に示す。

| No. | URL | メソッド | View 名 | 役割・内部処理 |
|---|---|---|---|---|
| 1 | / | GET | HomeView | ホーム画面 |
| 2 | /cards/upload/ | GET / POST | OriginalImageUploadView | 名刺画像アップロード |
| 3 | /cards/ | GET | CardListView | 名刺一覧。7 値フィルタ（ocr_result 5 値 + _pending / _processing の仮想値）対応、初回は business_card のみ表示。BackNavigator 保持パラメータに ocr_result を含む |
| 4 | /cards/<uuid:pk>/ | GET | CardDetailView | 名刺詳細 + Contact 編集。OpenCV デバッグセクションと業務操作セクションを併設。Contact が紐づく BC については _contact_field.html パーツを include して ContactDetailView と同じ編集 UI（個別フィールドの確認 OK / 値修正）を提供する。{% if debug %} 外に「同じ画像に含まれる他の名刺」セクションを通常表示（sibling_cards 全件、ocr_result_badge 付き） |
| 5 | /originals/ | GET | OriginalListView | 元画像一覧 |
| 6 | /originals/<uuid:pk>/ | GET | OriginalDetailView | 元画像詳細。セクション 7「検出された名刺」テーブルは 8 列構成（操作 / サムネイル / 名刺ID / card_index / 向き / OCR結果 / 切り抜き画像 / 作成日時）、名刺詳細への遷移ボタンを含む。**v1.6.2：EXIF 情報セクションを追加（§11.3.2 参照）** |
| 7 | /persons/ | GET | PersonListView | 人物一覧 |
| 8 | /persons/<uuid:pk>/ | GET | PersonDetailView | 人物詳細。Person.status 別二系統化：(a) active な Person → 当該 Person の primary_contact を取得し /contacts/<primary_contact_uuid>/（11 番 ContactDetailView）へ HTTP 302 リダイレクト、(b) merged な Person → merged 状態の専用詳細画面を表示（過去どんな Contact があったか、誰と統合されたか、マージログ、§4.9 参照）、(c) archived な Person → archived 状態の専用詳細画面を表示（復元ボタン等の操作起点として将来活用） |
| 9 | /persons/<uuid:pk>/add-additional-role/ | GET / POST | PersonAddAdditionalRoleView | 別肩書追加。Active コンタクトを追加 |
| 10 | /contacts/create/ | GET / POST | ContactCreateView | 名刺なしでプライマリーコンタクトとパーソンを同時生成 |
| 11 | /contacts/<uuid:pk>/ | GET | ContactDetailView | コンタクト詳細画面（業務メイン画面）。Contact.status × Person.status による表示モード分岐：(primary or active) × Person.active なら編集可能モード（AJAX で値修正・confidence 確認可、§11.6.2 / AJAX 経路の正規化通しは §11.9.3。v1.6.0 で確定済み）、inactive または Person.archived/merged なら表示のみモード。セクション構成：操作ボタン（人物詳細リンク、修正画面、別肩書追加、マージ画面）/ Contact ヘッダー（名刺画像 + status バッジ + マージ候補バッジ）/ 一括確定（編集可能モードのみ）/ フィールド表示（high はシンプル、mid/low は信頼度マーク + ラジオ UI）/ 他のアクティブコンタクト（同 Person 配下の他 active）/ マージ関連（DuplicateCandidate / PersonMergeLog / previous_person / inactive Contact 履歴の共通 include） |
| 12 | /contacts/<uuid:pk>/update-primary/ | GET / POST | UpdatePrimaryContactView | プライマリーコンタクトの修正画面（fix の場合は既存コンタクトを上書き、transfer / promotion / job_change / name_change の場合は新規コンタクトを追加し既存を inactive 化）。プライマリーコンタクト以外がこのルートに入ってきたらガード |
| 13 | /contacts/<uuid:pk>/update-active/ | GET / POST | UpdateActiveContactView | アクティブコンタクトの修正画面。プライマリーのように新規コンタクト生成なし、コンタクト値の修正のみ。change_reason フィールドは置かない（fix 相当の処理に固定）。アクティブコンタクト以外がこのルートに入ってきたらガード |
| 14 | /contacts/<uuid:pk>/preview/ | GET | PreviewContactView | コンタクト一覧画面からのモーダルプレビュー用、AJAX 専用 |
| 15 | /duplicates/ | GET | DuplicateCandidateGroupListView | 重複候補グループ一覧。group_id 単位で集約表示。絞り込みフォーム（rank の複数選択 / 進捗の複数選択 / ユーザー絞り込み）あり。詳細は §11.5.x「DuplicateCandidateGroupListView の絞り込み仕様」参照 |
| 16 | /duplicates/groups/<uuid:group_id>/ | GET | DuplicateCandidateGroupDetailView | 同一グループ DuplicateCandidate の詳細表示。マージのレビューの最終結果表示画面（17 番からのリダイレクト直後は Django messages で完了メッセージを表示） |
| 17 | /duplicates/groups/<uuid:group_id>/review/ | GET / POST | DuplicateCandidateGroupUpdateView | マージレビュー画面。GET で次のペアを表示、POST で処理（Mark_as_Different_Person / Execute_Merge_Only のいずれか）→ 同一 URL に GET リダイレクト（PRG パターン）。すべて処理完了したら 16 番にリダイレクト + Django messages で結果メッセージ。UI 詳細は §11.5.5 / §11.6.2 参照 |
| 19 | /merge-logs/ | GET | PersonMergeLogListView | マージログ一覧。LoginRequiredMixin + ListView。絞り込み 3 種（status: undoable / undone / locked の複数選択 / user: 'me' で executed_by=ログインユーザー / searched=1 で絞り込み実行済みフラグ、未指定の初回は status='undoable' のみ）。ソート -executed_at（最新優先）、20 件/ページ。N+1 回避は select_related で surviving_person / merged_person の primary_contact / executed_by / undone_by |
| 20 | /merge-logs/<uuid:pk>/ | GET | PersonMergeLogDetailView | マージログ詳細。LoginRequiredMixin + View。1 件取得（select_related 5 段：surviving / merged primary_contact / executed_by / undone_by / duplicate_candidate）。DoesNotExist → Http404。context に is_undoable() + get_undo_preview() + 復元ボタン関連（21 番への遷移）を含む |
| 21 | /merge-logs/<uuid:pk>/confirm-undo/ | GET / POST | PersonMergeLogConfirmUndoView | マージ復元の確認画面と実行処理。LoginRequiredMixin + View。GET：is_undoable=False なら messages.error + 20 番リダイレクト、True なら MergeUndoForm を空で render。POST：is_undoable 再チェック → MergeUndoForm 検証 → Execute_Merge_Undo 呼び出し → ValidationError キャッチで messages.error + 20 番リダイレクト → 成功時 messages.success + 20 番リダイレクト。競合検出（GET / POST 両方）：他ユーザによる先行復元への防御 |
| 22 | /cards/<uuid:pk>/delete/ | POST | CardDeleteView | 名刺ハード削除（POST 専用、GET 等は 405）。認証ガード後 bc.delete() を呼ぶだけ。Contact CASCADE → CFC CASCADE → card_image post_delete の連鎖が走る（§4.3.2 参照）。削除後は元画像詳細（6 番）に 302 リダイレクト |
| 23 | /contacts/ | GET | ContactListView | コンタクト一覧画面。7 フィールド検索（氏名 / 会社 / 部署 / 役職 / メール / 電話 / 住所）AND 検索、status 3 チェックボックス絞り込み（primary / active / inactive、初回アクセス時は primary のみ ON）、ページネーション 20 件/ページ、BackNavigator 連携。Person.status='active' のみ表示（merged Person 配下は常に除外）、updated_at 降順 → created_at 降順。電話フィールドは personal_phone / mobile_phone / personal_fax / org_phone / org_fax の OR 一致 |

一覧画面なし：/persons/<uuid>/update/（Person 編集）。
命名規則：URL 名は update（edit ではない）、Class 名は XxxUpdateView / XxxCreateView 等。
### 11.3.1 旧 18 番の廃止
旧 /duplicates/groups/<uuid:group_id>/result/（DuplicateCandidateGroupResultView）は廃止された。17 番のリダイレクト先を 16 番に変更したため不要となった。
### 11.3.2 元画像詳細画面（6 番）の EXIF 情報表示（v1.6.2 / Phase G）
元画像詳細画面（6 番 OriginalDetailView）に「EXIF 情報」セクションを追加する。OriginalImage.exif_json（別表 A.3 / §4.2、JSONField(null=True, blank=True)）の内容を表示する。

| ケース | 表示 |
|---|---|
| EXIF あり（exif_json が non-NULL） | `<details>` 折りたたみで表示。展開すると JSON 整形表示（`json.dumps(exif_json, ensure_ascii=False, indent=2)`、CSS クラス `app-exif-json` の `<pre>` 等で表示） |
| EXIF なし（exif_json が NULL） | `<details>` を出さず、「EXIF 情報なし」と平文表示 |

実装の配置：EXIF 抽出・整形に関わる 3 関数（`extract_exif_to_json` / `_serialize_exif` / `_coerce_json_value`）はすべて `cards/services/image_processor.py` に配置する。抽出処理の詳細仕様は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §7.2 を参照。

既知挙動：GPSAltitudeRef 等の BYTE 型タグは現状 ASCII decode + rstrip で空文字になる（§20.2 に v1.7+ 送り事項として記載）。
## 11.4 View 層の設計
View 層は薄く保ち、ビジネスロジックは services / tasks 層またはモデルメソッドに委譲する。
### 11.4.1 ContactUpdateView の修正理由による処理分岐（12 番 UpdatePrimaryContactView）
12 番の Contact 編集画面では、ユーザーが修正理由を選択する。理由によって内部処理が異なる。

| 値 | 表示名 | 内部処理 |
|---|---|---|
| fix | 入力間違い・誤字訂正 | 既存 Contact を更新（contact.fix(form, user) 経由） |
| transfer | 異動・部署変更 | 新規 Contact 作成 + 既存を inactive に |
| promotion | 役職変更・昇進 | 新規 Contact 作成 + 既存を inactive に |
| job_change | 転職 | 新規 Contact 作成 + 既存を inactive に |
| name_change | 結婚等による姓変更 | 新規 Contact 作成 + 既存を inactive に |

修正理由は config/constants.py の PersonChangeReason（TextChoices、5 値）で定義する。additional_role（別肩書追加）は v1.4.2 で 12 番から削除し、独立画面（9 番 PersonAddAdditionalRoleView）に分離した。
### 11.4.2 新規 Contact 作成時のフィールド初期値
transfer / promotion / job_change / name_change で新規 Contact を作成する際、フィールド初期値は既存 Contact のフィールドを全コピーする。ユーザーは編集対象のフィールドだけ変更して保存する。
マージ理由ごとに自動でクリアするフィールドを変える方式は採用しない。実世界では「異動と同時に携帯番号も変わる」「結婚と同時に勤務先も変わる」など変則的なケースが多く、自動クリアはかえって入力ミスを誘発するためである。ユーザーが意識して変えるべき箇所を変える運用を前提とする。
既存 Contact の ContactFieldConfidence は新規 Contact にはコピーしない。新規 Contact のフィールドは、ユーザー入力直後の状態として扱う（confidence のレコードは作成されず、すべて high 扱い、第10章 10.6.4 参照）。
【マージ画面での値修正の扱い】設計案 A により、マージ画面で surviving 側 Contact のフィールドを値修正することがある。この修正は Contact.updated_by = マージ実行ユーザー、Contact.updated_at = マージ実行時刻として記録される。修正と同時に 12.7 の処理が発火する。マージ実行のトランザクションは 12.7 の発火と一体で処理する（ただし、マージ実行のトランザクション内では 12.8 の recover 処理が呼ばれるため、結果として 12.7 の invalidate 処理は不要となる、12.8 参照）。
### 11.4.2.1 新規 Contact 作成時の ContactSns 引き継ぎ（v1.6.2 / Phase F1）
ContactSns 別テーブル化（§4.4.4）に伴い、新規 Contact 作成経路では旧 Contact の ContactSns を新 Contact の InlineFormSet に initial として引き継ぐ。引き継いだ行はユーザーが不要なものを削除して保存できる。

| 画面 / ケース | ContactSns の扱い |
|---|---|
| 12 番 transfer / promotion / job_change / name_change（新規 Contact 作成） | 旧 Contact の ContactSns レコードを initial として新 Contact の InlineFormSet に渡す（ユーザーが不要なものを削除可） |
| 12 番 fix（既存 Contact 更新） | 既存 Contact を更新するため引き継ぎ処理は不要 |
| 9 番 別肩書追加（新規 Contact 作成） | primary の ContactSns を initial として引き継ぎ |
| 10 番 新規 Contact 作成 | 空表示（引き継ぎなし） |

initial を渡す際は、引き継ぎ件数分だけ extra 行を確保する（§11.6.7 の build_contact_sns_formset が initial 件数分の extra 行を生成する）。
### 11.4.3 ContactUpdateActiveView の処理（13 番 UpdateActiveContactView）
13 番（active 副コンタクト修正画面）は fix 相当の処理に固定する。contact.fix(form, user) を呼ぶ。change_reason フィールドは置かない（5 値の PersonChangeReason は適用しない）。
### 11.4.4 ContactCreateView の重複警告（10 番）
保存時に possible_high 以上の重複候補を検出し、警告ダイアログを表示する。
候補は上位 5 件 + 「+他 N 件」の表示形式。各候補に「詳細を見る」リンク（クリックで AJAX で /contacts/<id>/preview/ を取得し、モーダル表示）。
ユーザーの選択肢は「キャンセル」「強制作成」の 2 つ。追加警告なし（1 回の警告で十分）。
強制作成された Contact は status='primary' で新規 Person と共に作成され、後の cron で重複候補として再検出される。
将来的に、重複検知レベル（exact_match / possible_high / possible_mid / possible_low）を settings.py の DUPLICATE_WARNING_LEVEL で調整可能とする。デフォルトは possible_high。
### 強制作成後のユーザー体験フロー
強制作成された Contact は、後の cron による重複チェックで再度 DuplicateCandidate として上がってくる。これは意図した挙動であり、ユーザーは「強制作成時には別件と判断したが、改めてレビュー画面で同一人物だったと気付いてマージする」「別人だったと改めて確定する」のいずれかの操作を後から行える。
強制作成時に特別なフラグを立てたり、警告履歴を保存したりする必要はない（補助レコードに過剰な情報を持たせない方針）。
### 11.4.5 別肩書追加画面（9 番 PersonAddAdditionalRoleView）
別肩書追加画面の処理は View 内で直書きする。詳細は第10章 10.12 を参照。
### 11.4.6 マージ実行時の処理（17 番 DuplicateCandidateGroupUpdateView）
### POST 処理の流れ
- POST データを MergeForm に渡してバリデーション（11.6 参照）
- バリデーション通過後、form.cleaned_data['review_result'] を取得
- review_decision='different'（review_result が different 系：same_name / ocr_error / other_different のいずれかを含む）→ Mark_as_Different_Person を呼ぶ
- review_decision in ('merged', 'additional_role') → Execute_Merge_Only(candidate, surviving_person, merged_person, form: MergeForm, user) を呼ぶ（v1.4.2 で Execute_Merge_with_Updates 統合、§9.4.4 / §13.4.1 参照）
- すべて完了後、PRG パターンで GET リダイレクト（17 番の URL に）
### 「フィールド修正あり / なし」の判定
form.confirmed_field_names() または値違いの修正状態から判定する。具体的な実装は MergeForm 内のヘルパーメソッド（例：form.has_field_updates()）で表現する。実装の詳細は実装フェーズで決める。
### 設計上の依存関係
- 3 つのサービスが 1 つの MergeForm に依存する
- MergeForm のフィールド変更は 3 サービスすべてに影響する
- View が form の cleaned_data を見て分岐するロジックを持つ
- これは「マージ画面の入口が 1 つで、結果に応じて 3 つのサービスに振り分ける」という業務構造から生じる必然的な依存
## 11.5 レビュー画面の動作（PRG パターン）
### 11.5.1 GET /duplicates/groups/<uuid:group_id>/（16 番、DuplicateCandidateGroupDetailView）
詳細画面。グループ全体の状態を表示する。POST 処理なし。
- 当該グループの DuplicateCandidate を review_status ごとに集計（pending / merged / different_person）
- invalidated は集計に含めない（マージで巻き込まれて自動無効化されたものはユーザーの意思ではない）
- 集計結果を表示
- Django messages（17 番からのリダイレクト直後）があれば併せて表示
### 未レビュー候補がある場合の表示
- 候補ペア一覧を表示
- 「レビューを開始」ボタンで 17 番へ遷移
### すべてレビュー完了の場合の表示
- 結果サマリーを表示（マージ件数、別人判定件数）
- マージされた Person 一覧、別人判定された候補一覧
- メッセージ表示用エリア（17 番からリダイレクトされた直後の場合）
### 11.5.2 GET /duplicates/groups/<uuid:group_id>/review（17 番、DuplicateCandidateGroupUpdateView）
レビュー画面。次のペアを表示する。
- セッションの shown_pair_ids（表示済みペア ID リスト）を取得
- 当該グループの review_status='pending' かつ shown_pair_ids に含まれない DuplicateCandidate を取得
- 残ペアあり → ペア画面表示、shown_pair_ids に当該ペア ID を追加
- 残ペアなし、shown_pair_ids が空でない → /duplicates/groups/<group_id>/ に GET リダイレクト + Django messages で完了メッセージ + shown_pair_ids クリア
- 残ペアなし、shown_pair_ids も空 → /duplicates/ にリダイレクト
### 11.5.3 POST /duplicates/groups/<uuid:group_id>/review（17 番、DuplicateCandidateGroupUpdateView）
- アクションを取得（MergeForm.cleaned_data['review_decision'] の値で判定：merged / additional_role / different）
- review_decision in ('merged', 'additional_role')：Execute_Merge_Only(candidate, surviving_person, merged_person, form: MergeForm, user) を呼ぶ（v1.4.2 で Execute_Merge_with_Updates を統合、§9.4.4 / §13.4.1 参照）
- review_decision='different'：Mark_as_Different_Person を呼ぶ
- shown_pair_ids に当該ペア ID を追加
- 同じ URL（/duplicates/groups/<group_id>/review）に GET でリダイレクト（PRG パターン）
### 11.5.4 Django messages framework の使用
17 番から 16 番へのリダイレクト時の結果メッセージ表示は、Django 標準の django.contrib.messages を使用する。
- 17 番の処理内で messages.success(request, "...") のように記録
- 16 番のテンプレートで {% if messages %}{% for message in messages %}...{% endfor %}{% endif %} で表示
- メッセージは 1 回表示すると消える（Django messages framework の標準挙動）
URL パラメータやセッションを使った独自実装は避ける。
### 11.5.5 マージレビュー画面の構成（v1.4.2 全面刷新）
マージレビュー画面（17 番 DuplicateCandidateGroupUpdateView）の UI は、D-3 系 Contact 詳細画面 AJAX 化に伴う設計大転換（§11.6.2 / #20 廃止系参照）を受けて、v1.4.2 で全面刷新する。Contact のフィールド値修正機能は持たず、ユーザーは「判定情報の入力」と「確認チェック」のみを行う。値違いの修正は事前に Contact 詳細画面（11 番）で AJAX 経由で済ませている前提。
### 画面の縦順序

| 順 | ブロック | 内容 |
|---|---|---|
| 1 | ヘッダー | breadcrumb / h1 / 戻るボタン |
| 2 | 名刺画像比較 | 左右 2 枚、クリックで拡大モーダル |
| 3 | フィールド比較 | Contact UPDATABLE_FIELDS 全 31 フィールドをグルーピング表示。フルネーム省略（MergeForm.hidden_name_fields()、§11.6.2 / ストック #54）、値違いハイライト（app-detail-item--diff、§11.8.6）、両側空フィールド非表示 |
| 4 | SNS 比較ブロック | 両 Person の ContactSns レコードを sns_type 別にグルーピング比較表示（v1.6.2 / Phase F2、§11.5.7 参照）。フィールド比較ブロックの後・サバイブ/主コンタクト選択の前に配置 |
| 5 | サバイブ/主コンタクト選択 | フィールド比較の直下、テーブル組み込み。review_decision に応じてラベル動的切替（merged → 「サバイブ側を選択」、additional_role → 「主コンタクトを選択」）、different 時は disabled 化 |
| 6 | 判定 | review_decision 3 値（merged / additional_role / different）、ボタン形式ラジオ |
| 7 | 判定理由 | review_result の複数選択 CB（マージ系 6 個 / 別人系 3 個）、第 1 段階に応じて動的表示（CSS :has()、§11.8.7） |
| 8 | 確認チェック | マージ系判定時のみ表示（MergeForm.has_confirm_checkboxes()、§11.6.2 / ストック #54）。different 判定時は非表示 |
| 9 | 備考 | review_note（CharField、required=False） |
| 10 | 決定ボタン | エラーサマリーは画面トップに表示（app-form__error-summary、§11.8.6） |

### 設計趣旨
旧 3 カラム（surviving / merged / 中央編集）構造は Execute_Merge_with_Updates（マージ実行とフィールド値修正の同時実行）の廃止（#20 廃止 1）に伴って意味を失った。新 2 カラム + 比較表 + ボタン形式ラジオ + 動的表示の構造は、人が業務判定する際の視線移動・誤判定リスクを最小化する UX 設計として確定。
### マージ画面の前提
マージ画面は情報密度が高い。PC 横長レイアウト（最低 1280px 幅）を前提とする。スマホ・タブレットでの最適化は v1.5.0 以降に送る。レイアウトは既存の app.css の BEM 命名規則に従い、新規クラスは app-merge-* / app-section--* / app-detail-item--* prefix で定義する（§11.8.6 参照）。
### 11.5.6 マージ画面のレイアウト（2 カラム + 中央判定情報）

| 観点 | 仕様 |
|---|---|
| カラム構造 | 左：Person A（基準コンタクト推奨側）、右：Person B（候補コンタクト） |
| 編集機能 | なし（マージ画面では Contact のフィールド値を修正しない、§11.5.5 参照） |
| 中央 | 判定情報入力（review_decision / surviving_person_choice / review_result / 確認チェック / review_note） |
| 比較表 | フィールド比較は両カラムを横並びで表示。値違いは行ごとに app-detail-item--diff クラスでハイライト |
| 前提幅 | PC 横長 1280px 以上（マージ画面のスマホ対応は v1.5.0 以降、§20.1 参照） |

【v1.4.2 改訂前との差分】 旧 3 カラム（左 surviving 候補 1 / 右 surviving 候補 2 / 中央マージ後 Contact 編集）を廃止、新 2 カラム + 中央判定情報に置換。中央カラムでの Contact 編集、フィールド横の「→」コピーボタン、notes 結合、low/mid 修正・確認 UI、値違い採用 3 通り選択肢（左カラム採用 / 右カラム採用 / 手入力）はすべて廃止。
### 11.5.7 表示対象フィールドの拡張（9 → 31 フィールド比較表示）
v1.4.2 で表示対象を DUPLICATE_CHECK_FIELDS（9 フィールド）から Contact UPDATABLE_FIELDS（31 フィールド、§4.4.1 のユーザー入力対象フィールド全体）に拡大する。マージ画面では「修正対象」ではなく「比較表示対象」として扱う（値違いの修正 UI は提供しない、修正は事前に Contact 詳細画面 11 番で済ませる前提）。

**v1.6.2：UPDATABLE_FIELDS の実フィールド数は 31 件**（v1.6.1 時点の実態。v1.4.x の「24 フィールド」記述を 31 に更新）。カテゴリ別内訳は以下のとおり。

| カテゴリ | 件数 | フィールド |
|---|---|---|
| 名前系 | 9 | full_name / last_name / first_name / salutation_name / other_name_parts / name_order / display_name / phonetic_name / alias_name |
| 会社系 | 8 | organization / legal_entity_type / legal_entity_type_position / department / title / qualification / catchphrase / branch |
| 住所系 | 5 | postal_code / country / region / city / rest_of_address |
| 連絡先 | 7 | email / personal_phone / mobile_phone / personal_fax / org_phone / org_fax / website |
| メモ言語 | 2 | notes / lang |
| **合計** | **31** | |

- **address は UPDATABLE_FIELDS から除外**：Contact.save() が 4 要素（postal_code / region / city / rest_of_address、+ country / lang）から自動組み立てるため（§11.9.4）。直接編集対象としない
- **ContactSns は UPDATABLE_FIELDS から除外**：別テーブル化（§4.4.4）に伴い、InlineFormSet（§11.6.7）で別途扱う。マージ画面の比較は §11.5.7 末尾の SNS 比較表示仕様に従う
### 表示制御

| 制御 | 内容 |
|---|---|
| 両側空フィールド非表示 | 両 Contact ともに空のフィールドは行ごと非表示。表示密度を上げる |
| フルネーム省略 | MergeForm.hidden_name_fields() が ["last_name", "first_name"] を返すケース（両側 full_name 一致 + 姓・名サブフィールドが full_name に含まれる）では last_name / first_name 行を省略表示。重複情報の冗長表示を防ぐ |
| 値違いハイライト | 両側で値が異なるフィールドは app-detail-item--diff クラスでハイライト |
| confidence 表示 | 各フィールドの両側 confidence を表示（high / mid / low / confirmed の 4 状態、{% confidence %} カスタムタグ、§11.8.2 参照） |

### 拡大対象（v1.4.1 → v1.4.2 で追加）

| カテゴリ | フィールド |
|---|---|
| 氏名サブ | last_name / first_name / salutation_name |
| 連絡先補足 | personal_fax / website / qualification / catchphrase |
| SNS | ContactSns 別テーブル（sns_type ごとにグルーピング表示） |
| 自由記述 | notes |
| 補助情報 | postal_code / lang |

これらは confidence による表示分岐なし、値違いまたは片方空のフィールドのみ表示対象とする。

v1.6.1 改訂：SNS は ContactSns 別テーブル化されたため、比較表示の単位を「個別フィールド」から「ContactSns レコード」に変更する。両 Person の ContactSns レコードを sns_type 別に並べ、両側で同じ sns_type を持つもの・片側のみ持つものを視覚的に比較できるよう表示する。

#### SNS 比較表示の UI 仕様（v1.6.2 / Phase F2）
マージ画面の SNS 比較ブロック（§11.5.5 縦順序の順 4）の表示仕様を以下に明文化する。

| 観点 | 仕様 |
|---|---|
| グルーピング | 両 Person の ContactSns レコードを sns_type 別にグルーピングして表示 |
| 両側同じ sns_type | 同じ行に左右並べて表示。sns_id が異なる場合は `app-sns-compare__item--diff` クラスでハイライト |
| 片側のみ持つ sns_type | もう一方のセルは「（なし）」表示 |
| 両側とも持たない sns_type | 行ごと非表示 |
| sns_type の並び順 | SnsType.choices の定義順（twitter / linkedin / facebook / instagram / github / blog / youtube / line） |
| diff 判定基準 | (sns_type, sns_id) ペアの一致で is_diff=False、不一致で is_diff=True |

CSS クラスの棲み分け：
- SNS 編集 UI（InlineFormSet、§11.6.7）：`app-sns-formset__*`
- SNS 比較表示（マージ画面）：`app-sns-compare__*`
### 11.5.8 DuplicateCandidateGroupListView の絞り込み仕様（15 番）
15 番 DuplicateCandidateGroupListView（重複候補グループ一覧画面）の絞り込みフォームと並び順は以下のとおり。

| 絞り込み | 仕様 |
|---|---|
| rank フィルタ | 4 値（exact_match / possible_high / possible_mid / possible_low）の複数選択。初回（searched 未指定）はデフォルトで全 rank |
| progress フィルタ | 「未レビュー（pending）」「完了（completed）」の複数選択。初回はデフォルトで「未レビュー」のみ |
| user フィルタ | チェックボックス 1 つ（user=me）。ON のとき person_a.primary_contact.created_by または person_b.primary_contact.created_by がログインユーザーに一致するペアの group のみ（OR 条件、自分が読み取った Person を含む候補を抽出） |

【並び順】 未レビュー優先（pending_count > 0 を先）→ rank 順（exact_match → possible_low）→ group_id 順。
【除外】 group_id IS NULL のレコードは集約対象外（単発候補は本画面の対象外）。
### 11.5.9 マージログ系 3 画面の実装詳細（19 / 20 / 21 番）
3 画面の責務・処理フローは §11.3 URL 一覧表 No.19 / 20 / 21 を参照。実装上の補足は以下のとおり。

| 観点 | 内容 |
|---|---|
| 認証 | 3 画面とも LoginRequiredMixin を付与 |
| select_related | DetailView は 5 段（surviving / merged primary_contact / executed_by / undone_by / duplicate_candidate）、ListView は surviving / merged primary_contact / executed_by / undone_by |
| ページネーション | ListView は 20 件 / ページ、-executed_at（最新優先） |
| 競合検出 | 21 番 GET / POST 両方で is_undoable() を再チェック、他ユーザーによる先行復元時は messages.error + 20 番リダイレクト |
| メッセージング | Django messages framework で復元実行の成否を 20 番画面に伝達 |
| URL ルート | duplicates/urls.py に 3 ルートを登録：merge_log_list / merge_log_detail / merge_log_confirm_undo（末尾スラッシュ付き） |

## 11.6 Form クラス設計
### 11.6.1 Form クラス継承図

```
ContactBaseForm（抽象基底、Contact フィールドのみ、error_class = AppErrorList）
├── ContactUpdateForm（12 番用：change_reason + ContactFieldConfidence の confirmed チェックボックス追加）
│   └── ContactUpdateActiveForm（13 番用：change_reason を除外、それ以外は親と同じ）
├── ContactAddAdditionalRoleForm（9 番用：Contact フィールドのみ）
└── ContactCreateForm（10 番用：手動で新規 Person + 新規 Contact 作成）

MergeForm（17 番用、forms.Form 直接継承、error_class = AppErrorList を明示）
MergeUndoForm（21 番用、forms.Form 直接継承、error_class = AppErrorList を明示）
```

【v1.4.2 改訂】 MergeForm は ContactBaseForm 継承から独立した。マージ画面が Contact のフィールド値修正機能を持たなくなった（Contact 詳細画面 AJAX 化に伴う設計大転換、D-3 系）ため、Contact フィールド継承の必要性がなくなった。新しい MergeForm はマージ判定情報（review_decision / review_result / surviving_person_choice / review_note）のみを受け持つ独立フォーム。
### 11.6.2 各 Form の責務
#### ContactBaseForm（抽象基底クラス）
- 責務：Contact のフィールド定義のみを持つ抽象基底クラス。UI 構造は持たない。継承する全フォームに AppErrorList（§11.6.6 参照）を error_class として配り、フィールドエラーの BEM クラスを統一する
- 配置：contacts/forms.py
- 継承元：forms.ModelForm
- Meta.fields：Contact のユーザー入力対象フィールド（v1.6.1 リネーム後）。full_name / last_name / first_name / name_order / other_name_parts / salutation_name / display_name / phonetic_name / alias_name / organization / department / title / branch / email / mobile_phone / personal_phone / personal_fax / org_phone / org_fax / postal_code / region / city / rest_of_address / country / website / qualification / catchphrase / notes / lang / language_composition / legal_entity_type / legal_entity_type_position など。導出フィールド（address / org_core_name / org_domain_name / legal_entity_type_code）と Contact 非保持（original_script / ai_analysis_notes）は含めない（§11.9.5・別表 A.5 参照）。salutation_name は必須バリデーション対象。**v1.6.1 改訂：個別 SNS フィールド 5 件（twitter / instagram / github / linkedin / facebook）は ContactSns 別テーブル化に伴い Meta.fields から削除。ContactSns の編集は別途 InlineFormSet で扱う（§11.6.7 参照）**
- 除外フィールド：status / previous_status / previous_person / confirmed_at / confirmed_by などシステムが管理する派生情報。**salutation_name_is_manual も Meta.fields に含めない**（画面から直接編集させない。salutation_name がユーザーによって入力・変更されたとき View 層が salutation_name_is_manual=True を自動セットする、§11.9.7）
- クラス変数：error_class = AppErrorList（継承する全フォームの個別フィールドエラーに既存 BEM クラス app-form__error を自動付与）
- `__init__`：kwargs.setdefault("error_class", self.error_class) を実装（Django BaseForm.__init__ のデフォルト引数による上書き対策）
#### ContactUpdateForm（12 番用）
- 責務：プライマリーコンタクトの修正画面用。change_reason + ContactFieldConfidence の確認チェックボックスを動的に追加
- 配置：contacts/forms.py
- 継承元：ContactBaseForm
- 追加フィールド：
  - change_reason（ChoiceField、PersonChangeReason の 5 値：fix / transfer / promotion / job_change / name_change）
  - note（CharField、required=False）
  - ContactFieldConfidence の確認チェックボックス（low/mid 信頼度 かつ `confirmed_at is None` のフィールドのみ動的追加。既に confirmed_at が記録された過去確認済みフィールドは追加対象外）
- メソッド：
  - clean()：動的追加された確認チェックボックスがすべて ON であることをバリデーション（§11.7.1 参照）
  - get_update_contact()：フォーム値だけを持った新規 Contact インスタンス（pk なし）を返す
  - confirmed_field_names()：ユーザーが確認・編集したフィールド名のリストを返す（戻り値: list[str]）
- `__init__` の引数：target_contact: Contact（バリデーション時に既存 Contact の confidence 状態を参照するため必須、§11.7 参照）
#### ContactUpdateActiveForm（13 番用）
- 責務：アクティブ副コンタクトの修正画面用。ContactUpdateForm を継承し、change_reason フィールドを除外する
- 配置：contacts/forms.py
- 継承元：ContactUpdateForm
- 除外フィールド：change_reason（親クラスから除外）
- 継承するフィールド：note、ContactFieldConfidence の確認チェックボックス
- 継承するメソッド：clean() / get_update_contact() / confirmed_field_names()（親クラスの実装をそのまま使用）
- 設計趣旨：active 副コンタクトの修正は fix 相当の処理に固定。change_reason は不要
#### ContactAddAdditionalRoleForm（9 番用）
- 責務：別肩書追加画面用。Contact フィールドのみ（追加項目なし）
- 配置：contacts/forms.py
- 継承元：ContactBaseForm
- 追加フィールド：なし
- **salutation_name 必須（v1.6.2）**：9 番でも salutation_name を必須とする（空文字・空白のみは ValidationError）。他の Contact 系 Form と揃える（§11.7 参照）
- メソッド：get_update_contact()（フォーム値だけを持った新規 Contact インスタンスを返す）
- `__init__` の引数：person: Person（紐付ける Person を View から渡す）
#### ContactCreateForm（10 番用）
- 責務：手動で新規 Person + 新規 Contact 作成画面用
- 配置：contacts/forms.py
- 継承元：ContactBaseForm
- 追加フィールド：必要に応じて追加
- メソッド：get_update_contact()
#### MergeForm（マージ画面 17 番用）
- 責務：マージ画面用。3 段階判定モデル（第 1 段階：review_decision、第 2 段階：review_result、第 3 段階：確認チェック）でユーザの判定情報を受け取る。値修正機能は持たず、Contact のフィールド値には触らない（マージ画面に来る前に Contact 詳細画面で値修正済みの前提）
- 配置：duplicates/forms.py
- 継承元：forms.Form（v1.4.2 で ContactBaseForm 継承から独立。Contact フィールドや値修正の責務を持たないため）
- クラス変数：error_class = AppErrorList（§11.6.6 参照、ContactBaseForm 経由ではないため明示的に設定）
- 追加フィールド：
  - review_decision（ChoiceField、3 値 [merged / additional_role / different]、required=True）。第 1 段階判定として UI で先に選ばせる。additional_role の場合、第 2 段階の review_result は UI に表示せず、clean() で ["additional_role"] を自動セット
  - review_result（MultipleChoiceField、複数選択可、required=False）。choices は DuplicateMergeReason.choices + DifferentPersonReason.choices（合計 10 値）。widget はテンプレ側で手動描画（<input type="checkbox" name="review_result"> 形式、CheckboxSelectMultiple 相当）。required=False なのは review_decision='additional_role' 時に空でも通る必要があるため
  - surviving_person_choice（ChoiceField、choices=[person_a / person_b]、required=False、initial 設定なし）。clean() で review_decision in ('merged', 'additional_role') のときのみ必須化。required=False と initial なし設計の狙いは「判定未選択 / 別人選択時に disabled 化したサバイブ選択 UI で誤誘導を起こさない」こと
  - review_note（CharField、required=False）
- メソッド：
  - clean()：第 1 段階・第 2 段階・サバイブ選択の整合性をまとめて検証（§11.7.3 参照）
  - get_merge_reason()：純関数（self.cleaned_data から導出）。戻り値 list[str]。review_result のうち DuplicateMergeReason.values に含まれる value だけをリスト化して返す。別人系のみ / 空のとき [] を返す。review_decision='additional_role' のとき clean() で review_result=["additional_role"] に整形済みのため、自動的に ["additional_role"] を返す
  - hidden_name_fields()：純関数（DB 操作なし）。戻り値 list[str]。両側 full_name が一致 + 両側 last_name 一致 + 両側 first_name 一致 + last_name / first_name が空でない + full_name に last_name と first_name の両方が部分一致で含まれる、を全部満たすとき ["last_name", "first_name"] を返す。それ以外は []。View 側で field_groups 整形時に省略対象を除外するための判定
  - has_confirm_checkboxes()：純関数（self.fields のキー走査のみ）。戻り値 bool。self.fields に confirmed_ 始まりのフィールドが 1 個以上あれば True。テンプレ側で確認チェックブロックの表示判定に使用
- `__init__` の引数：candidate: DuplicateCandidate、surviving_person: Person、merged_person: Person（マージのコンテキストを View から渡す）
【v1.4.2 廃止】 v1.4.2 改訂前の merge_reason フィールド、get_update_contact() メソッド、confirmed_field_names() メソッド、has_field_updates() メソッド、値違い確認の選択肢（左カラム採用 / 右カラム採用 / 手入力）、中央フォーム初期値ロジックはすべて廃止。Contact 詳細画面 AJAX 化に伴うマージ系処理の設計方針大転換による（v1.6.0 で確定。AJAX 経路の正規化通しは §11.9.3 を参照）。
#### MergeUndoForm（21 番用、独立クラス）
- 責務：マージ復元画面用。復元実行時の備考と確認チェックを受け取る。破壊的操作（DB に影響）の誤操作防止を Form レベルで強制する
- 配置：duplicates/forms.py
- 継承元：forms.Form（ContactBaseForm は継承しない、MergeForm と同様）
- クラス変数：error_class = AppErrorList（§11.6.6 参照、明示的に設定）
- 追加フィールド：
  - note（CharField、required=False、widget=Textarea、ラベル「復元の備考」）。PersonMergeLog.record_undo_action(user, note) 経由で ActionLog の data に {"note": str} 形式で保存される（§4.11.3 参照）
  - confirmed（BooleanField、required=True、ラベル「この操作は取り消せません。内容を理解しました」）。誤操作防止のための確認チェック
- メソッド：
  - clean()：追加バリデーションなし（confirmed の required=True で Form 標準の必須チェックが効くため）
- `__init__` の引数：特になし（通常の Form 生成、form = MergeUndoForm(request.POST)）
【設計趣旨】 マージ復元は破壊的操作（DB に影響）で、誤操作防止のため確認チェック CB を Form レベルで強制する。note は復元理由を任意で記録できる導線として MergeForm との対称性で持つ（マージ実行時も review_note 任意記録、復元時も note 任意記録）。
### 11.6.3 Form の設計原則

| 原則 | 内容 |
|---|---|
| Form は DB に触らない | get_update_contact() で Contact インスタンスを返すまで |
| Form は presentation 層 | パース・バリデーション・データ整形までが責務 |
| Model は永続層 | DB 書き込みはモデルメソッド経由 |
| 共通化しない | UI が違う Form は完全に別クラス |
| 共通モデルメソッドを使う | ContactFieldConfidence の更新等は共通メソッド経由 |
| 戻り値は新規 Contact インスタンス | pk なし、status / person 等は Form では設定しない |

### 11.6.4 抽象基底クラス導入の設計趣旨
Contact フィールド定義を 1 箇所に集約し、Contact 修正系・別肩書追加・新規作成・マージ画面の各 Form で共通基底として使う。Contact フィールドが追加・変更されたときの保守性を確保する。
- 抽象基底クラス `ContactBaseForm`：Contact フィールド定義の共通化のみ。UI 構造は持たない
- UI 構造（テンプレート、フォームレイアウト、特殊な表示処理）：各子 Form クラスで独立に実装する
これにより、Contact フィールドが追加・変更されたときの保守性を確保しつつ、UI の柔軟性を保つ。
### 11.6.5 form.get_update_contact() の戻り値仕様
ユーザーが入力した値だけを持った 新規 Contact インスタンス（pk なし） を返す。
重要な設計判断：
- pk は設定しない（メモリ上のインスタンスのみ）
- status や person の設定は Form では行わない（View またはサービス層が判断）
- 既存インスタンスを書き換えない（メモリ上の状態と DB 状態の乖離を起こさない）
呼び出し側の責務：

| 呼び出し画面 | View の処理 |
|---|---|
| 修正画面（12 番 / 13 番） | change_reason に応じて、既存 Contact への値反映（contact.fix(form, user) 経由）or 新規 Contact 作成を判断 |
| マージ画面（17 番） | サービス層（Execute_Merge_Only）に Form を渡す。マージ画面では Contact のフィールド値修正を行わないため、get_update_contact() 等の値修正系メソッドは廃止された（v1.4.2 改訂、§11.6.2 / §9.4.4 参照） |

設計趣旨：
Form は「ユーザー入力の整形」までが責務。「既存レコード上書きか新規追加か」の判断は Form ではなく View またはサービス層が行う。これにより、Form の責務を明確に保ち、再利用性を高める。
### 11.6.6 AppErrorList（共通エラー出力クラス）
- 責務：Django forms.utils.ErrorList のサブクラスとして、<ul class="errorlist"> の出力に既存 BEM クラス app-form__error を追加し、<ul class="errorlist app-form__error"> を出力させる
- 配置：contacts/forms.py
- 継承元：django.forms.utils.ErrorList
- 実装：__init__ で kwargs.setdefault("error_class", "app-form__error") を super に渡す
- 適用範囲：ContactBaseForm.error_class = AppErrorList で配り、ContactBaseForm を継承する全フォーム（ContactUpdateForm / ContactUpdateActiveForm / ContactCreateForm / ContactAddAdditionalRoleForm / MergeForm / MergeUndoForm）に自動波及
- CSS との接続：.errorlist.app-form__error の最小スタイル調整（list-style: none / padding-left: 0 等で <ul> ベースでも既存 .app-form__error スタイルが効くようにする）。詳細は §11.8 を参照
【設計趣旨】 Django デフォルトの <ul class="errorlist"> 出力はブラウザデフォルトで目立たないため、ユーザーがバリデーションエラーに気付けない問題があった。既存 .app-form__error スタイル（赤系・小さい・強調）を Django 自動出力に接続することで、テンプレ側に変更を加えずに全フォームのエラー表示が統一される。
### 11.6.7 ContactSns 編集 UI（InlineFormSet）（v1.6.1 新設 / v1.6.2 実装確定）
ContactSns 別テーブル化（§4.4.4）に伴い、SNS の編集を個別フィールドから InlineFormSet に変更する。

- ContactSns の編集は Django の `inlineformset_factory` を用いた InlineFormSet で実装する
- ContactSns 各レコードに対し sns_type（choices）と sns_id の編集 UI を提供する
- 追加・削除はクライアントサイド JS で動的に対応（既存の動的フォームセット実装パターンに準拠、app.js ベース）
- 以下の各画面で ContactSns InlineFormSet を組み込む：
  - 12 番 ContactUpdateView
  - 13 番 ContactUpdateActiveView
  - 9 番 ContactAddAdditionalRoleView
  - 10 番 ContactCreateView

**v1.6.2 実装確定事項：**

**ヘルパー関数：** `build_contact_sns_formset(*, data, instance, initial, prefix="sns")` で FormSet を生成する。initial 件数分だけ extra 行を確保する（9 番・12 番の新規 Contact 作成で旧 Contact の SNS を引き継ぐため、§11.4.2.1 参照）。

| パラメータ | 値 |
|---|---|
| prefix | `"sns"`（デフォルト） |
| extra | 0（初期空行ゼロ。initial 件数分だけ別途 extra 行を確保） |
| can_delete | True |
| max_num | None（無制限） |

**共通 partial：** `templates/contacts/_contact_sns_formset.html`。対象 4 画面すべてがこの partial を `{% include %}` で取り込む。

**BEM クラス：** `app-sns-formset` / `__rows` / `__row` / `__cell` / `__cell--type` / `__cell--id` / `__delete`

**JS フック命名**（app.js 末尾の IIFE で処理）：

| クラス | 役割 |
|---|---|
| `js-sns-formset` | ブロック全体（data-prefix を保持） |
| `js-sns-formset-container` | 行の追加先 |
| `js-sns-formset-row` | 1 行 |
| `js-sns-empty-form-template` | `<template>` 要素（追加用） |
| `js-sns-add-btn` | ＋ボタン |
| `js-sns-remove-btn` | ×ボタン（既存行は DELETE を ON にして非表示、新規行は DOM から除去） |

**重複バリデーション：** `validate_unique()` を no-op 化し、`clean()` で同一 (sns_type, sns_id) の重複を検出して日本語メッセージで返す（「同じ種別・同じ ID の SNS が重複しています。」）。

**sns_id の空白除去：** CharField 標準の strip を適用する（前後空白除去のみ。本格的な正規化は v1.7+ 送り）。

**error_class：** FormSet 基底 `_BaseContactSnsFormSet` に AppErrorList を配布（§4.4.4 / §11.6.6 参照）。
## 11.7 Form のバリデーション仕様

**【v1.6.0 共通 / v1.6.2 で 9 番追加】salutation_name 必須バリデーション：** ContactCreateForm / ContactUpdateForm / ContactUpdateActiveForm / **ContactAddAdditionalRoleForm（9 番、v1.6.2 で追加）** は salutation_name を必須とする（空文字・空白のみは ValidationError）。DB は NULL 許容のまま、Form 側で必須を担保する。AJAX 経路（ContactAjaxUpdateFieldView）も salutation_name 空文字送信は 400 エラー（§11.9.3）。各 Form の clean で salutation_name の非空チェックを行う。

**【v1.6.0 共通】正規化通し：** 各 Form の clean は、保存対象フィールド値を §11.9 の normalization 純関数に通す。AJAX 経路の正規化通しは §11.9.3 を参照。

### 11.7.1 ContactUpdateForm.clean()
### 責務
- salutation_name 必須チェック（v1.6.0、空なら ValidationError）
- 動的追加された確認チェックボックス（low/mid 信頼度 かつ confirmed_at is None のフィールドのみ）がすべて ON であることをバリデーション
- 各フィールド値を §11.9 の normalization 純関数に通す
- バリデーション失敗時は ValidationError を発生させる
### バリデーションロジックの方針
- target_contact（__init__ で受け取った既存 Contact）の get_field_confidences() を呼び、low/mid かつ `confirmed_at is None` のフィールドのリストを取得（過去に確認済みのフィールドは対象外）
- フォームの確認チェックボックスのうち、上記フィールドに対応するものがすべて ON か確認
- 1 つでも OFF があれば ValidationError を発生させる
- エラーメッセージは「『〇〇』フィールドの確認チェックを ON にしてください」のような形式
### target_contact をフォームに渡す方法
View から Form を生成する際、__init__ の引数として既存 Contact を渡す：
form = ContactUpdateForm(request.POST, target_contact=contact)
Form.__init__ 内で self.target_contact = target_contact として保持し、clean() から参照する。
実装の詳細は実装フェーズで決める。
### 11.7.2 ContactUpdateActiveForm.clean()
ContactUpdateForm.clean() を継承する。change_reason フィールドがない以外は同じバリデーションロジック。
### 11.7.3 MergeForm.clean()
### 責務
マージ画面のバリデーション。第 1 段階・第 2 段階・サバイブ選択・確認チェックの整合性をまとめて検証する：
- review_decision の必須性：required=True で担保（Form の required 機能）
- review_decision と review_result の整合性検証（`set.issubset()` ベース）：
- merged：review_result が 1 個以上 + すべてマージ系 value（DuplicateMergeReason から ADDITIONAL_ROLE を除く 6 値）。set.issubset(MERGED_VALUES) で判定
- additional_role：clean() 内で cleaned_data["review_result"] = ["additional_role"] を自動整形（UI 上は review_result を表示しないため、入力値は空でよい）
- different：review_result が 1 個以上 + すべて別人系 value（DifferentPersonReason 3 値）。set.issubset(DIFFERENT_VALUES) で判定
  - merged：review_result が 1 個以上 + すべてマージ系 value（DuplicateMergeReason から ADDITIONAL_ROLE を除く 6 値）。set.issubset(MERGED_VALUES) で判定
  - additional_role：clean() 内で cleaned_data["review_result"] = ["additional_role"] を自動整形（UI 上は review_result を表示しないため、入力値は空でよい）
  - different：review_result が 1 個以上 + すべて別人系 value（DifferentPersonReason 3 値）。set.issubset(DIFFERENT_VALUES) で判定
- surviving_person_choice の必須性：review_decision in ('merged', 'additional_role') のときのみ必須化。エラーメッセージは review_decision に応じて「サバイブ側を選択してください」/「主コンタクトを選択してください」を切替
- 確認チェック CB のバリデーション：review_decision in ('merged', 'additional_role') のときのみ走らせる。different 判定時は CB 検証をスキップ（別人判定では surviving 側 Contact をマージしないため CFC を confirmed 化する必要がない、UI 上も CB は disabled / 非表示）
- other_* 選択時の review_note 必須：review_result に other_merged / other_different が含まれるなら、review_note が必須
バリデーション失敗時は ValidationError を発生させる。エラーメッセージはフィールドごとに表示される（§11.6.6 AppErrorList 経由で app-form__error クラスが自動付与）。
【v1.4.2 廃止された旧バリデーション】
- 「マージ系と別人系の同時選択禁止」チェック：review_decision 3 値判定の構造上、物理的に起き得ないため削除
- 「マージ系のときの merge_reason 必須」チェック：merge_reason フィールド廃止（§11.6.2 参照、get_merge_reason() メソッド導出に置換）に伴い削除
- 「DUPLICATE_CHECK_FIELDS の全 high 化」「値違いフィールドの確認済み」：マージ画面での値修正廃止（D-3 系設計大転換）に伴い、マージ画面に来る前に Contact 詳細画面で対応する流れに変更（v1.6.0 で確定。Contact 詳細画面の AJAX 編集と正規化通しは §11.9.3 を参照）
### candidate / surviving_person / merged_person をフォームに渡す方法
View から Form を生成する際、__init__ の引数として渡す：
form = MergeForm(request.POST, candidate=candidate, surviving_person=surviving_person, merged_person=merged_person)
Form.__init__ 内で保持し、clean() から参照する。
### 11.7.4 設計趣旨
バリデーションを Form 側で行う理由：
- Form は presentation 層として、ユーザー入力の整形とバリデーションまでを担う
- View は処理の流れの制御に専念。バリデーション通過後にサービス層を呼ぶ
- Model（contact.fix など）はバリデーション済みを前提として動作。contact.fix 内で再度バリデーションを行わない
これにより：
- バリデーションロジックの重複を防ぐ（Form / View / Model のうち Form のみで行う）
- View / Model の責務が明確になる
- テストが書きやすい（Form のテスト、View のテスト、Model のテストが独立）
## 11.8 UI カスタムタグ・追加ルート・共通モーダル部品
UI 共通化のためのカスタムタグ 6 種・追加ルート 2 本・共通モーダル部品を提供する。
### 11.8.1 カスタムタグ一覧

| タグ | 引数 | 用途 |
|---|---|---|
| {% card_image url size %} | url, small/medium/large | 名刺画像表示（モーダル trigger 付き） |
| {% original_image_thumbnail url %} | url | 元画像サムネイル表示（モーダル trigger 付き） |
| {% confidence confidences field_name format %} | confidences dict, field名, 表示形式 | フィールド単位の信頼度マーク |
| {% contact_confidence contact format %} | contact オブジェクト, 表示形式 | Contact 単位の信頼度サマリー |
| {% confidence_state ... %} | （引数は実装側で確定） | 単一フィールドの確認状態（high / confirmed / unconfirmed）の表示状態判定 |
| {% ocr_result_badge bc %} | BusinessCard インスタンス | OCR 処理結果のバッジ表示（v1.4.2 で新規追加） |

JSON ツリー表示は v1.4.2 から @andypf/json-viewer カスタム要素（CDN）に統一し、カスタムタグ化はしない（テンプレ側で <andypf-json-viewer> 要素を直接記述、§11.8.2 末尾参照）。
### 11.8.2 カスタムタグの詳細
### {% card_image url size %}
名刺画像をサムネイル表示する。size パラメータで small / medium / large を指定。出力 HTML には js-image-modal-trigger クラスと data-image-url 属性を自動で付与し、クリックで共通モーダルが開く。
### {% original_image_thumbnail url %}
元画像のサムネイル表示。{% card_image %} と同様にモーダル trigger 自動付与。
### {% confidence confidences field_name format %}
フィールド単位の信頼度マーク表示。第 1 引数は contact.get_field_confidences() の戻り値（dict）。format で表示形式（icon / badge / count 等）を切り替え可能。
get_field_confidences() の戻り値が全フィールド分の ContactFieldConfidence インスタンス（high は疑似インスタンス）を返す設計のため、本タグは ContactFieldConfidence インスタンスの属性（confidence / confirmed_at 等）を直接参照して 3 状態を判定する（第10章 10.5.3 参照）。
### {% contact_confidence contact format %}
Contact 単位の信頼度サマリー表示。Contact 全体で何個の low/mid フィールドが残っているか、何個が確認済みかを集計表示する。
### {% confidence_state ... %}
単一フィールドの確認状態（high / confirmed / unconfirmed）の表示状態判定。_contact_field.html パーツ内で個別フィールドの「OK / 修正中」UI 切替に使用する。引数仕様は実装側で確定（cards/templatetags/ui_tags.py、§11.6.2 / §11.3 No.11 ContactDetailView 参照）。
### {% ocr_result_badge bc %}
BusinessCard の ocr_result 値に応じて、業務上の警戒度を表すバッジを表示する。引数 bc が None 時は空文字を返す防御あり。

| `ocr_result` 値 | バッジクラス | 表示テキスト |
|---|---|---|
| business_card | （バッジなし、空文字を返す） | - |
| not_business_card | app-status-badge--muted | 名刺ではない |
| insufficient_info | app-status-badge--warning | 情報不足 |
| ocr_failed | app-status-badge--error | OCR失敗 |
| others | app-status-badge--muted | その他 |

ラベルは bc.get_ocr_result_display() 経由で TextChoices 定義から取得する（仕様書で表示名が変わっても自動追従）。
【v1.4.2 拡張：ocr_status 分岐】 BC.ocr_status を見て、pending → 「OCR 待ち」バッジ（app-status-badge--muted）、processing → 「OCR 中」バッジ（app-status-badge--info）を表示。done / failed のときは上記 5 値表示ロジックに従う（新規 CSS / JS の追加なし、既存クラスのみ使用）。
【バッジ色の設計思想】 業務上の警戒度で割り当て：muted = 想定外だが警戒度低、info = 処理中の通知、warning = ユーザーに撮り直しを促す系、error = 明確な失敗。not_business_card と others が同じ muted になっているのは、現時点で others がセットされる経路自体がない（v1.4.x 時点では将来用受け皿のみ）ため実用上の問題なし。実際に others が出る経路ができたタイミングで色分けを再検討する。
### JSON ツリー表示（カスタムタグ化なし）
v1.4.2 で @andypf/json-viewer@2.4.0（CDN）カスタム要素に統一。テンプレ側で <andypf-json-viewer> 要素を直接記述する（カスタムタグ経由ではない）。
- 利用画面：CardDetailView（BC.raw_json_1 / raw_json_2 表示）、OriginalDetailView（debug_json 表示）
- 初期状態：折りたたみ（expanded="0"）
- 検索・コピー・サイズ表示は viewer 内蔵ツールバーで提供
- OriginalImage.raw_json の表示は v1.4.2 で廃止（§4.2 / ストック #35 参照、生 JSON は BC.raw_json_1 / raw_json_2 経由で参照）
- admin の readonly_fields も整理：OriginalImageAdmin から raw_json を削除、BusinessCardAdmin に raw_json_1 / raw_json_2 / ocr_status / claimed_at / error_message を追加、list_display / list_filter に ocr_status / ocr_result を追加
### 11.8.3 共通モーダル部品
画像表示・コンタクト詳細表示等で使う共通モーダル部品。
- HTML 構造：base テンプレートに 1 箇所だけ定義
- JS 制御：app.js 内のモーダル制御コードで一元管理
- カスタムタグの js-image-modal-trigger クラスをクリックすると、data-image-url の URL を取得してモーダルに表示
### 11.8.4 BackNavigator 機能
画面遷移時の「前の画面に戻る」機能を実装する。
- テンプレートでは append_back タグ 1 つだけ使用
- クエリキーは View 側の push_current(title, keys) に隠し、エンコードは 1 リクエスト 1 回のみ（キャッシュ）
- テンプレートタグは 4 種：append_back_url / back_url / back_all_url / hidden_back_field
詳細は別途「BackNavigator 使い方ガイド」を参照。
### 11.8.5 サイドバー構成
共通サイドバー（templates/cards/_sidebar.html）の最終構成（v1.4.2、8 項目）：

| 順 | 項目名 | 遷移先 | 備考 |
|---|---|---|---|
| 1 | 名刺アップロード | 2 番 OriginalImageUploadView | - |
| 2 | 名刺一覧 | 3 番 CardListView | - |
| 3 | 元画像一覧 | 5 番 OriginalListView | - |
| 4 | 人物一覧 | 7 番 PersonListView | - |
| 5 | コンタクト一覧 | 23 番 ContactListView | v1.4.2 で新規追加（ストック #29） |
| 6 | コンタクト新規作成 | 10 番 ContactCreateView | - |
| 7 | 重複候補一覧 | 15 番 DuplicateCandidateGroupListView | v1.4.2 で明文化（ストック #49） |
| 8 | マージログ | 19 番 PersonMergeLogListView | v1.4.2 で新規追加（ストック #67） |

各項目には active_menu パラメータでハイライト連動。例：active_menu="duplicates:merge_log_list" のとき 19 / 20 / 21 番画面で項目 8 に is-active クラスを付与する。
### 11.8.6 マージレビュー画面の BEM 階層
マージレビュー画面（17 番）の v1.4.2 全面刷新（§11.5.5）で追加された BEM クラスを以下に整理する。命名規則は CLAUDE.md §7 の app-* / __ / -- / is-* / js-* に従う。

| BEM クラス | 用途 |
|---|---|
| app-merge-decision / app-merge-decision__btn | 第 1 段階判定ボタン群（review_decision 3 値、§11.6.2） |
| app-merge-survivor / app-merge-survivor__btn | サバイブ/主コンタクト選択ボタン群（surviving_person_choice、§11.6.2） |
| app-merge-survivor__label--survivor / app-merge-survivor__label--primary-role | ボタンラベル動的切替（CSS で :has(.js-decision-additional_role:checked) 連動、§11.8.7） |
| app-merge-reason / app-merge-reason__btn | 第 2 段階判定理由ボタン群（review_result、CB ボタン形式） |
| app-section--merged-only / app-section--additional-role-only / app-section--different-only | 判定値に応じた動的セクション表示（§11.8.7） |
| app-section--needs-survivor / app-section--executes-merge / app-section--survivor-only / app-section--primary-role-only / app-section--survivor-unselected-label / app-section--survivor-disabled-label | サバイブ選択・判定状態に応じた動的表示（§11.8.7） |
| app-form__error-summary / app-form__error-summary__title / app-form__error-summary__list | 画面トップのエラーサマリブロック |
| app-detail-item--diff | フィールド比較の値違い行ハイライト |
| app-sns-compare / app-sns-compare__row / app-sns-compare__item / app-sns-compare__item--diff | SNS 比較ブロック（v1.6.2 / Phase F2、sns_type 別グルーピング・(sns_type, sns_id) 不一致の diff ハイライト、§11.5.7） |
| app-review-thumb / app-review-thumb__img / app-review-thumb__placeholder / app-review-field-group__title | 名刺画像サムネ + フィールドグループ見出し |
| app-debug-uid | DEBUG=True 時の UID コピペ用要素（§11.8.8） |

JS 識別クラス（イベントフック・CSS セレクタの両方で使用）：

| `js-` クラス | 用途 |
|---|---|
| js-decision-merged / js-decision-additional_role / js-decision-different | 第 1 段階ラジオの判定値別識別（CSS :has() の引数として使用） |
| js-reason-merged / js-reason-different | 第 2 段階 CB の系統別識別 |

### 11.8.7 CSS :has() による動的 UI 切替パターン
マージレビュー画面の動的セクション表示は、JS なしで CSS の `:has()` 擬似クラス で実現する。第 1 段階判定（review_decision）の値に応じて、第 2 段階セクション・サバイブ選択ブロック・判定理由 CB のラベル等を出し分ける。
### セレクタパターン
form:has(.js-decision-X:checked) .app-section--Y { display: block; }
「form 内に js-decision-X のチェックが入っているとき、app-section--Y を表示」のロジックを CSS だけで記述。
### 適用ケース

| 場面 | パターン |
|---|---|
| 判定値による第 2 段階セクションの出し分け | form:has(.js-decision-merged:checked) .app-section--merged-only など |
| サバイブ選択ブロックの常時表示 + 別人判定時 disabled 化 | form:has(.js-decision-different:checked) .app-merge-survivor { opacity: 0.5; pointer-events: none; } |
| 判定理由 CB のラベル動的切替 | form:has(.js-decision-additional_role:checked) .app-merge-survivor__label--survivor { display: none; } |

### ブラウザ要件
:has() 対応：Chrome 105+ / Safari 15.4+ / Firefox 121+。フォールバックは不要（社内利用前提、PC 横長 1280px 以上、§20.1）。
### 設計趣旨
JS なしで動的 UI が実現できるため、CLAUDE.md §7 の「新規 JS ファイル追加禁止 / app.js への追記のみ」方針と整合する。実装パターンを仕様書に明文化することで、将来他の画面でも同じパターンを再利用できる。
### 11.8.8 DEBUG=True 時の UID コピペ機能
開発・デバッグ時に DB レコードの UID（UUID 文字列）を頻繁にコピペする運用上の利便性を上げるため、各詳細画面に DEBUG モード限定で UID を表示する。
### 対象画面

| 対象画面 | 表示する UID |
|---|---|
| contact_detail（11 番 ContactDetailView） | Contact UID + Person UID の 2 件併記（active Person は ContactDetailView へ redirect される設計のため Person UID も併記） |
| card_detail（4 番 CardDetailView） | BusinessCard UID |
| person_detail_orphan / person_detail_merged / person_detail_archived（8 番 PersonDetailView の各分岐） | Person UID |

### 表示形式
- 表示位置：app-page-header 直下
- HTML：<code class="app-debug-uid">{{ object.id }}</code>
- DEBUG 制御：{% if debug %} で囲む（Django 標準 django.template.context_processors.debug 経由、INTERNAL_IPS 一致時のみ debug=True）
- CSS：.app-debug-uid に user-select: all; cursor: text; + inline-block / monospace / 11px / 軽い背景色（1 クリック全選択コピー可能、JS なし）
### 設計趣旨
開発・デバッグ時に DB レコードの UID をシェル / DB クライアント / 別画面に貼り付ける場面が多いため、画面上のテキストをワンクリックで全選択できる UI を CSS の user-select: all で実装。本番環境では INTERNAL_IPS 制御により表示されないため運用上の懸念なし。

## 11.9 Contact 正規化基盤

本節は Contact フィールドの 3 経路共有正規化基盤の本体仕様である。OCR 経路・手動入力 Form 経路・AJAX 経路の 3 経路がこの基盤を共有する。OCR 経路での純関数の呼び出しタイミング・順序・OCR 経路特有の前後処理は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §3.3.1 / §5.3 / §2.4.1 / §2.4 を参照（同じ仕様を 2 箇所に書かない方針）。

### 11.9.1 配置

`contacts/services/normalization.py` を新規作成（フィールド単位の純関数群、DB 操作なし・副作用なし）。v1.4.x 時点では未実装（strip のみ）、本フェーズで実装する。

主要純関数：

- `normalize_full_name(raw)`：full_name 正規化
- `normalize_organization(raw)`：会社名正規化（旧 company）
- `normalize_phone_value(raw)`：電話番号 1 件の正規化（v1.6.1 で personal_phone / personal_fax / org_phone / org_fax が CharField 単一値となったため、全電話系フィールドに単一値として適用）
- `normalize_email(raw)`：email 正規化
- `normalize_rest_of_address(raw)`：rest_of_address 正規化
- `normalize_postal_code(raw)`：postal_code 正規化
- `normalize_department_title_branch(raw)`：department / title / branch 共通正規化
- `compose_full_address(postal_code, region, city, rest_of_address, country, lang)`：full_address 組み立て（4 要素から組み立て、Contact.address に格納）
- `derive_org_core_name(org_name_full, legal_entity_type)`：org_core_name 導出
- `derive_org_domain_name(email)`：org_domain_name 導出
- `check_name_consistency(name_block: dict) -> dict[str, str]`：name ブロック整合性チェック（純関数）。**呼び出しタイミング・補正ルール（confidence を下げる方向のみ・サーバーログのみ・OCR プロンプトに匂わせない）は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §2.4.1 を参照**。本基盤には純関数として配置する

OCR 経路では json_parser が上記純関数を呼ぶ（呼び出しタイミング・順序は v1.6.1 統合版 §3.3.1 等を参照）。original_script → full_name コピー時の最小限正規化（全角空白→半角・連続空白 1 つ・前後空白除去・大文字小文字は変えない）も本基盤の純関数として実装する。

### 11.9.2 3 経路共有

3 経路すべてが同じ純関数群を共有する。入力経路によらず Contact フィールドに格納される値が一致することを保証する。フィールド名→正規化関数の対応は Contact.UPDATABLE_FIELDS を単一の真実として引く（テーブル別新設しない）。

| 経路 | 呼び出し元 |
|---|---|
| OCR 経路 | json_parser が OCR 出力読み取り時点で各フィールド値を純関数に通す（v1.6.1 統合版 §3.3.1 参照） |
| 手動入力経路 | ContactCreateView / ContactUpdateView の Form clean（§11.7）で各フィールド値を純関数に通す |
| AJAX 経路 | ContactAjaxUpdateFieldView → Contact.update_field（§11.9.3）でフィールド値を純関数に通す |

### 11.9.3 AJAX 経路の正規化通し（コード君踏み外し最大ポイント）

AJAX 経路（Contact 詳細画面で個別フィールドを AJAX 更新する経路）は ContactAjaxUpdateFieldView → Contact.update_field を通る。**Form を経由しない**ため、Django Form の clean も CharField デフォルトの strip もかからない。対応しないと AJAX 経路だけ正規化されない値が DB に保存され、3 経路共有設計が破綻する。

対応：Contact.update_field は保存前に必ず field_name に対応する normalization 純関数を呼ぶ。ValidationError は AJAX View に伝播。実装着手前に ContactAjaxUpdateFieldView と Contact.update_field の本体を全 read して確認すること（要コード確認）。

**salutation_name 必須化（v1.6.0）：** AJAX 経路でも salutation_name の空文字送信は 400 エラーとする。手動入力経路の Form（§11.6.2 / §11.7）でも必須バリデーションを課す。DB は NULL 許容のまま、Form/View 側で必須を担保する。

**カテゴリA 対象フィールドの正規化挙動（v1.6.2 明記）：** `normalize_field` のカテゴリA 対象フィールド（last_name / first_name / display_name 等、§11.9.5 カテゴリA）は正規化関数が未割当である。これらは手動 Form の CharField 標準 strip と揃え、AJAX 経路でも前後 strip のみを適用する（大文字小文字・全角→半角の強制変換は行わない）。これにより 3 経路（手動入力 / AJAX / OCR）でカテゴリA フィールドの扱いが一致する。

### 11.9.4 full_address の組み立て

`compose_full_address(postal_code, region, city, rest_of_address, country, lang)` が 4 要素から組み立て、Contact.address に格納（既存 address フィールド流用）。

- OCR 経路：json_parser が 4 要素を格納後、本基盤が compose_full_address を呼んで address 更新
- 手動入力経路：Form clean で 4 要素のいずれか変更で compose_full_address を呼んで address 更新
- AJAX 経路：4 要素のいずれか update_field 更新で compose_full_address を呼んで address 更新
- UI 上 address は直接編集不可（読み取り専用表示）。ユーザーは 4 要素を編集し、address は自動で組み立て直す
- 組み立て順序の言語・国分岐（日本式 vs 英語式・番地先国最後）は本フェーズ未確定。日本特化を本筋・他言語最小限。本フェーズは日本式実装、ja 以外は次フェーズ確定

### 11.9.5 正規化 5 カテゴリ

※ full_name は経路依存。OCR 経路ではカテゴリD（original_script からのコピー、v1.6.1 統合版 §3.3.1 参照）、手動入力経路では UI 補助（last_name / first_name / other_name_parts / name_order から UI スクリプト補助組み立て＋手入力可）。下表ではカテゴリD と UI 補助の両方に full_name を記載しているが、経路によって扱いが分かれることを示す。

| カテゴリ | 性質 | 該当 |
|---|---|---|
| A：事実 | 原文の中身変えず軽い整え（前後空白除去・連続スペース1つ・全角スペース→半角）。大文字小文字・全角英数字→半角の強制変換なし | first_name/last_name/other_name_parts/display_name/salutation_name/phonetic_name/alias_name/handwritten_text/other_printed_text |
| B：enum規格 | 規定値強制、範囲外は受け皿値（other/und/OTH） | name_order/legal_entity_type_position/country/lang（primary_lang 由来）/language_composition/legal_entity_type_code |
| C：二重検算 | 別ソース照合→不一致でconfidence low | legal_entity_type_code/電話系5/country/name ブロック整合性チェック（v1.6.1 統合版 §2.4.1） |
| D：導出 | 他フィールドから生成 | address（full_address：4要素から）/org_core_name（organization から）/org_domain_name（email から）/legal_entity_type_code（legal_entity_type から）/full_name（original_script からコピー） |
| UI補助 | サーバーで組み立てずUIスクリプト補助＋手入力尊重 | full_name（手動入力経路） |
| 対象外 | 正規化しない | catchphrase/qualification/website/SNS/既存 notes。handwritten_text/other_printed_text も対象外（事実保存用）。ai_analysis_notes は Contact フィールドでないため対象外 |

phonetic_name はカテゴリA（軽い整え）。normalization は体裁整えのみ（全角空白→半角・連続空白1つ・前後空白除去）。字数比較・構造照合はしない（カタカナと漢字で字数が一致しないため検算不能）。name ブロック整合性チェック（v1.6.1 統合版 §2.4.1）の対象外。

original_script は Contact に持たない（raw_json 内のみ）ため本カテゴリ表の Contact フィールドからは除外。raw_json 内の体裁整えは OCR プロンプト側で済んでおり本基盤の責務外。json_parser が full_name にコピーする際の最小限正規化のみ本基盤が担う。

#### 11.9.5.1 各フィールドの正規化ルール（本文化）

旧 §15.5.3 の既存ルールを本節に本文化する。フィールド名は v1.6.0 リネーム後に統一。ロジック自体は変更しない。

- **full_name**：全角空白→半角 / 半角空白除去 / 全角英数字→半角 / 前後空白除去 / 空なら ValidationError
- **organization（旧 company）**：株式会社系統一表記 / 前後位置差は吸収しない（前株・後株は別会社扱い）/ 全角半角空白除去 / 全角英数字→半角 / 前後空白除去
- **mobile_phone / personal_phone / personal_fax / org_phone / org_fax**：数字とハイフンのみ抽出 / ハイフン除去 / 全角数字→半角 / 国番号正規化 / 漢数字→半角（v1.6.1 で全 5 フィールドが CharField 単一値となったため、配列要素ごとの適用は不要）
- **email**：全体小文字化 / 前後空白除去
- **rest_of_address**：全角半角空白除去 / 全角英数字→半角 / 漢数字→半角 / 丁目番地号を「-」に / ハイフン統一 / 前後空白除去
- **postal_code**：数字のみ（ハイフン除去）/ 全角数字→半角
- **department / title / branch**：全角半角空白除去 / 全角英数字→半角 / 前後空白除去

### 11.9.6 org_domain_name の汎用ドメイン無視

フリーメール・プロバイダドメイン（gmail.com / yahoo.co.jp 等）の無視リスト（マスター）を持つ。org_domain_name の値は名刺どおり残す（空にしない＝個人事業主等の情報を失わない）。重複検出側がそのドメインを会社一致判定に使わない。判定は normalize 側。DUPLICATE_CHECK_FIELDS は変更しない。

### 11.9.7 salutation_name の再計算と手動入力フラグ

`compute_salutation_name(contact)` は contact の primary_lang（lang）と姓名から salutation_name を組み立てる純関数（文化別ルールの本体は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §1.5 を参照。本編は再計算の所属と発火条件を定める）。

- **Contact.save() オーバーライドで、`salutation_name_is_manual=False` のときのみ `compute_salutation_name(self)` を呼び出して salutation_name を再計算する**（v1.6 メール配信仕様書 §18.2 のマイグレーション・処理の所属は本編 §11.9 が正本）
- `salutation_name_is_manual=True` のときは再計算せず、ユーザーが手動入力した値を保持する
- 手動入力フラグの自動セット：手動入力経路（Form）・AJAX 経路で salutation_name がユーザーによって直接入力・変更されたとき、View 層が `salutation_name_is_manual=True` をセットする（§11.6.2 / §11.7）。salutation_name_is_manual 自体は Form の Meta.fields に含めず、画面から直接編集させない
- OCR 経路では salutation_name_is_manual を設定しない（False のまま。OCR が組み立てた salutation_name は再計算対象として扱う）

**v1.6.2 実装確定事項：salutation_name 自動計算のスナップショット方式**

Contact.save() での salutation_name 自動計算は「スナップショット方式」で実装されている。

- `__init__` で姓系フィールド（last_name 等、compute_salutation_name の入力となるフィールド）の値を記録（スナップショット）する
- `save()` 時にスナップショットと現在値を比較し、変更があったときに再計算する（`salutation_name_is_manual=False` の場合）
- 毎回無条件に再計算するのではなく、入力フィールドの変更検知で発火する

**compute_salutation_name の lang 判定方式（v1.6.2 明記）**

`compute_salutation_name(contact)` の lang 判定は前方一致で行う。`"ja-JP"` 等の地域付きコードも拾える。

| lang 判定 | 宛名組み立てルール |
|---|---|
| `lang.startswith("ja")` | 「{last_name} 様」 |
| `lang.startswith("ko")` | 「{last_name} 님」 |
| `lang.startswith("zh")` | {full_name} のみ |
| 上記以外（en / und 等） | 「Dear {full_name},」 |

文化別ルールの本体仕様は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §1.5 を正本とする。本表は本編側で実装確定した判定方式の要約。

# 第12章 重複チェックのバックグラウンド処理
## 12.1 実行頻度
check_duplicates 管理コマンドは cron で起動する。推奨頻度は 5 分間隔。実際の起動間隔は crontab で設定する。
crontab 例：*/5 * * * * cd /path/to/project && python manage.py check_duplicates
## 12.2 処理の単位
1 回の実行で処理する Contact 件数は、--limit オプションで指定可能。デフォルトは 100 件。
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
DUPLICATE_CHECK_FIELDS に含まれるフィールド（full_name、organization、department、title、branch、email、personal_phone、mobile_phone、address）が編集された場合：
- 当該 Contact が紐付く Person を特定
- その Person を person_a または person_b に持つ DuplicateCandidate を抽出
- review_status='pending' のものを 'invalidated' に変更
- 当該 Contact の duplicate_checked_at を NULL に戻す
- 次の cron で新しい DuplicateCandidate が生成される
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
invalidate_pending_candidates(contact)：12.7 専用ヘルパー。配置は duplicates/services/merge_executor.py。
処理内容：
- contact が紐づく Person を特定
- その Person を person_a または person_b に持つ DuplicateCandidate のうち review_status='pending' のものを invalidated に変更
- contact.duplicate_checked_at = NULL に戻す
これらを呼び出し元のトランザクション内で実行する。
## 12.8 マージ実行時の DuplicateCandidate 処理（recover 一本化）
### 12.8.1 v1.4.2 の方針：recover 一本化
マージ実行時の DuplicateCandidate 処理は、値修正の有無を問わず同じ recover 処理を適用する。
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
recover_duplicate_candidates(merged_person, surviving_person) は、マージ実行サービス（Execute_Merge_Only）から mark_as_merged 後に呼び出される後処理である。v1.4.2 の責務縮小により、recover 関数は「冪等性のための防御チェック」と「DuplicateCandidate の再復帰」を担い、Person / DuplicateCandidate の状態遷移そのものは呼び出し元（Execute_Merge_*）が事前に実行する前提とする。
- merged_person を含む他の pending DuplicateCandidate を invalidated 化（A / B 以外の Person 集合を保持）
- 当該マージの DuplicateCandidate の状態確認（冪等性チェックのみ）：呼び出し元（Execute_Merge_*）が事前に candidate.mark_as_merged(user, review_result, note) を呼んでいる前提。recover 関数内では DuplicateCandidate を改めて 'merged' に変更する処理は行わない（呼び出し元責務）
- 保持した DuplicateCandidate を再復帰：
- `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` クラスメソッドで新規作成
- score / rank / group_id は old_candidate からコピー（再スコア計算は不要）
- merged_person だった側を surviving_person（new_surviving_person）に置き換え
- review_status='pending' で作成
  - `DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person)` クラスメソッドで新規作成
  - score / rank / group_id は old_candidate からコピー（再スコア計算は不要）
  - merged_person だった側を surviving_person（new_surviving_person）に置き換え
  - review_status='pending' で作成
- surviving_person.duplicate_checked_at の更新は recover 関数では行わない（呼び出し元 Execute_Merge_* の責務）。v1.4.2 改訂前は recover 内で更新していたが、責務分担の明確化のため呼び出し元に移管。
【再復帰の除外条件】 相手側 Person が active 以外（merged / archived）になっている場合は、当該 DuplicateCandidate は再復帰させない。
手順 3 の DuplicateCandidate 新規作成は、merge_executor.py 内で直接 DuplicateCandidate.objects.create() を呼ぶのではなく、DuplicateCandidate.create_recovered_from(old_candidate, new_surviving_person) クラスメソッド経由で行う。これにより「old_candidate からスコア・ランク・group_id 等をコピーして新規作成する」処理ロジックが DuplicateCandidate モデル側に集約され、関数名から意図が読める。
【設計思想】「DB 履歴を見る判断」を generate 側（および呼び出し元）に集約し、recover の責務は冪等性チェックと再復帰のみに絞る。これは X-3 ランナバグ修正で確定した generate_duplicate_candidates_for_contact 側への履歴参照集約（v0.1.5 詳細仕様書 §5.4.1 参照）と同じ思想である。
### 12.8.4 スコアコピーが論理的に正しい理由
具体例で説明する。
マージ前の状態：
- Person A、B、C、D がいる
- DuplicateCandidate に複数の pending レコード：
- (A, B, score=220, rank=possible_high, group_id=G1, pending) ← マージ対象
- (B, C, score=150, rank=possible_mid, group_id=G2, pending)
- (B, D, score=130, rank=possible_mid, group_id=G2, pending)
  - (A, B, score=220, rank=possible_high, group_id=G1, pending) ← マージ対象
  - (B, C, score=150, rank=possible_mid, group_id=G2, pending)
  - (B, D, score=130, rank=possible_mid, group_id=G2, pending)
- A の主コンタクト = ContactA、B の主コンタクト = ContactB、以下同様
マージ実行：「A vs B」をマージして A を surviving、B を merged にする（マージ理由 same_card、値修正なし）。
再復帰処理（recover）で発生すること：
- (B, C) (B, D) を invalidated 化
- 新たに (A, C, score=150, rank=possible_mid, group_id=G2, pending) を作成
- 新たに (A, D, score=130, rank=possible_mid, group_id=G2, pending) を作成
- score / rank / group_id は元のものをそのままコピー
ここで誤解されやすいのが「surviving 側が B から A に変わったのだから、比較対象も変わってスコアも変わるはず」という直感である。
確かに正確に計算するなら、新しい (A, C) のスコアは「ContactA vs ContactC」を比較した結果になる。これは 150 点とは限らない。
しかし、本仕様では計算をやり直さず、(B, C) のスコアをそのままコピーする。
【中心ロジック】
コンタクトの値がマージで変わっても、対象の人物が同一人物である可能性は変わらない。スコア・ランクは「2 つの Person が同一人物である可能性の指標」であり、人物そのものに紐づく値。Contact のフィールド値が修正されても、人物の同一性判定は変わらないため、スコアコピーが成立する。
【連続レビュー UX の優先】
「B を介した縁故」を活用した連続レビュー UX を優先する設計判断である。マージ実行直後、ユーザーは「B の周辺人物を整理する」モードに入っている。B と重複候補だった C や D を、B を統合した直後の A でもレビューさせることで、ユーザーは効率的に B の周辺人物を確認できる。
もし正確な再計算をして rank='none' になり候補から消えると、ユーザーは「あれ、C の候補が消えた」と感じ、レビューフローが途切れる。
スコアの絶対値は次回 cron で正確な値に補正される。本仕様では、UX（連続レビュー）を優先し、ランクの近似性で十分とする設計判断をしている。
補助レコードに完璧な整合性を求めず、UX（連続レビューフロー）を優先する設計思想に基づく。
【値修正の有無を問わず適用】
v1.4.2 では recover 一本化により、マージ画面での値修正の有無を問わず本処理（スコアコピー）を適用する。値修正があった場合でも、対象人物の同一性判定（スコア・ランク）は変わらないため、スコア流用が成立する。
### 12.8.5 UX への影響
レビュー継続性：マージ後も同 GID で連続レビューを継続できる（recover による）。値修正による「新規 Person との重複」検出は、最大 5 分の遅延を許容（次回 cron 待ち）。
## 12.9 判定ロジック変更時の全件再判定
スコア表・ランク判定・正規化ルール等の判定ロジックが変更された場合、recheck_duplicates 管理コマンドを実行することで全 Contact を再判定できる。
動作：
- 全 active な Contact の duplicate_checked_at を NULL に戻す
- 既存の pending な DuplicateCandidate を全削除
- 次の cron で新しい DuplicateCandidate が生成される
### 12.9.1 運用想定
recheck_duplicates --all は以下の場面で実行する想定とする。
- 平常時：判定ロジック（スコア表・ランク判定・正規化ルール・代表メール判定リスト・DUPLICATE_CHECK_FIELDS など）が変更された後、変更を全 Contact に反映するため
- リリース時：v1.4.2 リリース直後に 1 回、既存 Contact の初期化のため
recheck_duplicates --all の処理自体は数秒で完了する（duplicate_checked_at を NULL に戻し、pending な DuplicateCandidate を削除するのみ）。実際の重複チェックは check_duplicates の cron が後から処理する。Contact 数が多い場合（例：5000 件で約 4 時間）、cron 処理の完了まで時間がかかるため、夜間バッチでの実行を推奨する。
## 12.10 重複チェックの実行ログ（ActionLog）
Run_Generate_Duplicate_Candidates 実行時に ActionLog にレコードを書き込む（ActionLog.record(...) 直接呼び）：

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

# 第13章 関数命名規則
## 13.1 関数の 3 分類
- 純関数：DB を一切触らない、副作用なし、同じ入力で同じ出力
- 準関数：DB を読むが書かない、外部世界に副作用なし
- 副作用あり関数：DB 書き込み・例外送出・API 呼び出し・ファイル書き込み等
## 13.2 命名規則
### 13.2.1 プレフィックス（基本）

| プレフィックス | 性質 | 例 |
|---|---|---|
| normalize_ / to_ / calc_ / is_ / has_* | 純関数 | normalize_full_name / has_minimum_info |
| find_ / get_ / search_ / determine_ | 準関数 | find_duplicate_contacts / determine_base_person |
| validate_* | 副作用あり（例外） | validate_image |
| convert_ / save_ / create_ / update_ / delete_* | 副作用あり（変換・DB 書込） | convert_to_jpeg / save_card_image |
| run_ / process_ / send_ / execute_ / extract_ / generate_ | 副作用あり（複合処理） | run_ocr / extract_carddata_via_ocr / generate_duplicate_candidates_for_contact |
| retry_* | 副作用あり（再投入） | retry_failed_ocr |

### 13.2.2 サービス層主要関数の命名規則（Pascal_Snake_Case）
View 層・cron・タスクから直接呼ばれる「処理フロー全体を担う主役関数」は Pascal_Snake_Case を使用する。
ルール：
- 各単語の最初の文字を大文字
- 単語間はアンダースコアで区切る
- 接続詞・前置詞（with / of / to / for / and / or / via 等）は小文字のまま
起動契機ごとの命名カテゴリ：

| カテゴリ | 起動契機 | 例 |
|---|---|---|
| Execute_* | View 層から（ユーザー操作起点） | Execute_Merge_Only / Execute_Merge_Undo |
| Mark_as_* | View 層から(状態遷移系) | Mark_as_Different_Person |
| Run_* | cron / タスク起動 | Run_Generate_Duplicate_Candidates / Run_Crop_Cards_From_OriginalImage / Run_Process_CardImages_With_OCR |

Extract_* カテゴリは v1.4.2 で廃止。旧 Extract_Cards_via_OCR（1 本パイプライン用上位関数）も廃止し、OpenCV と OCR を担う 2 本の Run_* 上位関数に分離した。詳細は §15.6 / §13.4.1 参照。
### 13.2.3 モジュール内専用ヘルパー関数の接頭辞
関数名の先頭に _ を付けてモジュール内専用を示す。
例：_calculate_score() / _determine_rank()（duplicate_score.py 内）
### 13.2.4 アンダースコアの 2 用途
- i18n 翻訳関数のエイリアス（14.2 参照）
from django.utils.translation import gettext_lazy as _
使用例：_('氏名')
- モジュール内専用ヘルパー関数の接頭辞（13.2.3）
関数名の先頭に _ を付けてモジュール内専用を示す
使用例：_calculate_score()
両者は構文上明確に区別される（前者は文字列リテラルを引数に取る、後者は関数定義）。
### 13.2.5 変数・引数の命名方針
変数名・引数名は省略しない。読み手が一瞬考えなくても意図が伝わる名前を選ぶ。
良い例：
- surviving_contact（マージで残る側の Contact）
- confirmed_field_names（ユーザーが確認・編集したフィールド名のリスト）
- merged_person / surviving_person
避ける例：
- confidence_map（何の confidence か考える必要がある）
- c（contact か何か不明）
- data（汎用すぎる）
略語は、業界・プロジェクト全体で確立されたもののみ許容する：
- OCR / JSON / URL / FK / PK / UUID 等
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
関数名は、関数の振る舞いが「文章として読める」ように選ぶ。record_action(user) のような曖昧な命名ではなく、record_different_person_action(user) のように何を記録するアクションかを関数名から読めるようにする。
例：
- record_action(user) ❌ 何を記録するか関数名から読めない
- record_different_person_action(user) ✅ 別人判定のアクションを記録することが関数名から読める
- record_merge_action(user) ✅ マージのアクションを記録する
- record_undo_action(user) ✅ 復元のアクションを記録する
## 13.3 docstring 性質明記
関数の docstring 冒頭に性質を明記する：[性質] 純関数 / 準関数 / 副作用あり、[入力]、[出力]、[例外]。
## 13.4 関数命名最終確定一覧
### 13.4.1 公開サービス（Pascal_Snake_Case）

| 関数名 | シグネチャ | 戻り値 | 配置 | 役割 |
|---|---|---|---|---|
| Mark_as_Different_Person | (candidate, form, user) | None | duplicates/services/merge_executor.py | 別人判定の本体 |
| Execute_Merge_Only | (candidate, surviving_person, merged_person, form: MergeForm, user) | None | duplicates/services/merge_executor.py | マージ実行の本体（v1.4.2 で Execute_Merge_with_Updates 統合、マージ画面の値修正機能廃止により本サービスに一本化）。atomic 冒頭で surviving 側 primary の未確認 low/mid CFC を mark_fields_as_confirmed で一括 confirmed 化（§9.3.1【v1.4.2 補足】参照） |
| Execute_Merge_Undo | (merge_log, form: MergeUndoForm, user) | None | duplicates/services/merge_executor.py | マージ復元の本体。form.cleaned_data["note"] を取り出して merge_log.record_undo_action(user, note) に渡す（§4.11.3 / §10.8.2 参照） |
| Run_Generate_Duplicate_Candidates | (limit=100) | （別ドキュメントで定義） | duplicates/tasks/duplicate_check_runner.py | タスク層上位関数。cron から呼ばれる |
| Run_Crop_Cards_From_OriginalImage | (original_image) | None | cards/tasks/crop_cards.py | OpenCV パイプライン上位（process_opencv cron 経由）。検出 → BC 作成 → OriginalImage.status=cards_extracted まで。実装済み |
| Run_Process_CardImages_With_OCR | () | None | cards/tasks/ocr_runner.py | OCR パイプライン上位（process_ocr cron 経由）。BC を CAS で claim → process_cardimage_with_ocr 呼び出し。【v1.4.3 注記】現状未実装。process_ocr 管理コマンドが直接 `process_cardimage_with_ocr` を BC ごとに呼ぶ実装になっており、対称的な公開サービス層が欠落している。次フェーズ（v1.4.x OCR 改善）で実装予定 |
| process_cardimage_with_ocr | (business_card, ocr_service) | None | cards/tasks/ocr_pipeline.py | BC 1 枚を引数に、OCR 実行 → BC 更新 → Contact / Person 生成 → OriginalImage.status 集計遷移までを完結（snake_case、Pascal_Snake_Case 主役関数の補助タスク扱い）。実装済み。実装パスは cards/tasks/ocr_pipeline.py（仕様書記載の cards/tasks/ocr_runner.py ではない、v1.4.3 で実装パスに合わせて記載修正） |
| extract_carddata_via_ocr | (card_image, ocr_service) | dict | cards/tasks/ocr_pipeline.py | 1 枚の card_image に対して条件付き 2 回 OCR を実行し結果を辞書で返す純粋ラッパー（§15.6 参照）。実装済み。実装パスは cards/tasks/ocr_pipeline.py |

【v1.4.2 廃止】 旧 Extract_Cards_via_OCR（1 本パイプライン用上位関数）は廃止。PipelineCoordinator クラスおよび process_pending 管理コマンドも完全削除（§17 別表 B 参照）。
【v1.4.3 補足：実装パスの修正】仕様書 v1.4.2 では OCR 関連の上位関数の配置を cards/tasks/ocr_runner.py としていたが、現実装ではすべて cards/tasks/ocr_pipeline.py に集約されている。Run_Process_CardImages_With_OCR 実装時のファイル名は次フェーズの実装指示書で確定する（ocr_pipeline.py への追加か、ocr_runner.py を新設するかは未確定）。
### 13.4.2 モジュール内専用ヘルパー（_snake_case）
- _calculate_score(contact_a, contact_b)：duplicate_score.py 内
- _determine_rank(score, contact_a, contact_b)：duplicate_score.py 内
### 13.4.3 サービス内共通（merge_executor.py、snake_case）

| 関数名 | 配置 | 役割 |
|---|---|---|
| recover_duplicate_candidates(merged_person, surviving_person) | duplicates/services/merge_executor.py | マージ後の DuplicateCandidate 後処理の唯一の関数 |
| invalidate_pending_candidates(contact) | duplicates/services/merge_executor.py | 12.7 専用（contact が紐づく Person の pending DuplicateCandidate を invalidated 化、contact.duplicate_checked_at = NULL も同時に更新） |

### 13.4.4 タスク層下位関数
- generate_duplicate_candidates_for_contact(contact)：duplicates/tasks/ 内
### 13.4.5 サービス層関数の責務一覧

| 関数名 | 性質と用途 |
|---|---|
| find_duplicate_contacts(contact) | 準関数。1 Contact について重複候補を検出 |
| determine_base_person(person_a, person_b) | 準関数。基準コンタクト判定（マージ数等） |
| Mark_as_Different_Person(candidate, form, user) | 副作用あり。別人判定（トランザクション内） |
| Execute_Merge_Only(candidate, surviving_person, merged_person, form: MergeForm, user) | 副作用あり。マージ実行（トランザクション内）。v1.4.2 で Execute_Merge_with_Updates を統合し本サービスに一本化 |
| Execute_Merge_Undo(merge_log, form: MergeUndoForm, user) | 副作用あり。復元実行（トランザクション内）。form.cleaned_data["note"] を record_undo_action に渡す |
| recover_duplicate_candidates(merged_person, surviving_person) | 副作用あり。recover 処理 |
| invalidate_pending_candidates(contact) | 副作用あり。pending invalidated 化 |

正規化関数群（contacts/services/normalization.py）：
【v1.4.3 注記：実装状況】 以下 6 関数（normalize_full_name / normalize_company / normalize_phone / normalize_email / normalize_address / normalize_postal_code）は 現状未実装。contacts/services/ ディレクトリ自体が現リポジトリに存在しない。現実装でのフィールド値正規化は、cards/services/json_normalizer.py の _extract_value_and_confidence 内で str(raw_value).strip()（前後空白除去）のみが実行されている。仕様書 §15.5.3 で規定された全角半角統一・株式会社統一・電話番号の数字抽出・メール小文字化・住所漢数字変換・郵便番号数字化は どれも実行されていない。これらは次フェーズ（v1.4.x OCR 改善）で実装予定であり、本表は実装対象として残置する。normalize_to_contact_dict のみ cards/services/json_normalizer.py に存在（v1.4.3 注記：本来は contacts/services/json_parser.py に配置すべきだが §21.2 削除対象の対応と合わせて次フェーズで移動予定）。

| 関数名 | 性質と用途 | 実装状況 |
|---|---|---|
| normalize_full_name(raw) | 純関数。フルネーム正規化 | 未実装（次フェーズ実装予定） |
| normalize_company(raw) | 純関数。会社名正規化 | 未実装（次フェーズ実装予定） |
| normalize_phone(raw) | 純関数。電話番号正規化 | 未実装（次フェーズ実装予定） |
| normalize_email(raw) | 純関数。メール正規化 | 未実装（次フェーズ実装予定） |
| normalize_address(raw) | 純関数。住所正規化 | 未実装（次フェーズ実装予定） |
| normalize_postal_code(raw) | 純関数。郵便番号正規化 | 未実装（次フェーズ実装予定） |
| normalize_to_contact_dict(raw_json) | 純関数。raw_json → Contact 用辞書 | 実装済み（ただし配置が cards/services/json_normalizer.py、次フェーズで contacts/services/json_parser.py に移動予定） |

## 13.5 Management Commands

| コマンド | オプション | 用途 |
|---|---|---|
| process_opencv | --limit / --id <oid> | cron 起動。OpenCV パイプライン専用。pending かつ BC 0 件の OriginalImage を取得 → OpenCV 検出 → BC 作成 → status=cards_extracted まで。BC が 1 件でも存在する OriginalImage は対象外（再実行禁止、card_index 不変担保） |
| process_ocr | --limit / --id <bc> | cron 起動。OCR パイプライン専用。BC.ocr_status=pending を取得 → 条件付き 2 回 OCR → Contact / Person 生成 → OriginalImage.status の集計遷移 |
| retry_failed_ocr | --opencv / --ocr (mutually exclusive、必須) + --limit / --id / --dry-run | 失敗の差し戻し。--opencv：status=failed AND BC 0 件の OriginalImage を pending に戻す。--ocr：BC.ocr_status=failed の BC を pending に戻し所属 OriginalImage.status を cards_extracted に戻す（所属 Contact 削除・孤立 Person 削除を含む。共通ヘルパー cards/tasks/ocr_recovery.py に集約） |
| reconcile_card_images | --apply | DB↔MEDIA_ROOT 整合検査・修復 |
| dev_reset_ocr | --all / --id / --limit / --dry-run | 開発用 OCR リセット |
| check_duplicates | --limit（デフォルト 100） | cron 起動。重複チェック実行 |
| recheck_duplicates | --all / --dry-run | 運用用。判定ロジック変更後の全件再判定 |
| dev_reset_duplicates | --all / --id / --limit / --dry-run | 開発用重複チェックリセット |

【v1.4.2 廃止】 旧 process_pending（1 本パイプライン用）は完全削除。cards/tasks/pipeline_coordinator.py（PipelineCoordinator クラス）と cards/tasks/card_cropper.py の save_card_image_tmp 関数も削除（§15.x card_image 同期書きへの変更と整合）。
## 13.6 設計思想の明文化
### 13.6.1 Form 渡し vs 引数渡しの判断基準
form の情報のうち、そのモデルが本来内包すべき情報がほぼ全てなら Form を渡してよい。そうでなければ、必要な値だけを引数で渡す。

| メソッド | Form を渡す？ | 理由 |
|---|---|---|
| contact.fix(form, user) | ✅ 渡す | form の情報は Contact + ContactFieldConfidence で、ほぼ Contact が内包すべき情報 |
| Person.set_primary_contact(new_contact, old_primary_new_status) | ❌ 渡さない | 必要な値（new_contact、old_primary_new_status）だけを引数で渡す |

### 13.6.2 ユーザー入力は全 high で信頼する設計（3 ケース別）
詳細は第10章 10.6.4 参照。
### 13.6.3 アクティブ↔プライマリー入れ替え機能は実装しない
詳細は第9章 9.8 参照。

# 第14章 共通定数と TextChoices
## 14.1 配置場所
複数のアプリで共通利用する定数は config/constants.py に集約する。モデル固有の選択肢は、各モデルの内部クラスとして定義する。
## 14.2 TextChoices の採用
v1.4.0 では Django TextChoices で choices を統一する。v1.3.4 で既存の choices もすべて TextChoices に書き換える。表示名は gettext_lazy（_()）でラップして翻訳対応する。
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
additional_role は v1.4.2 で削除した（別肩書追加画面が独立画面 9 番に分離したため）。
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
値：['full_name', 'organization', 'department', 'title', 'branch', 'email', 'personal_phone', 'mobile_phone', 'address']（9 フィールド。v1.6.0 で company→organization / phone→personal_phone / mobile→mobile_phone を機械的リネーム反映。フィールド数・スコア・ランク閾値・判定ロジック〈confidence=="high" のみ加算〉は不変。org_phone / org_fax は含めない＝会社代表電話は同一会社の複数人で同値のため重複検出に使うと別人が誤マージ。v1.6.1 で personal_phone が JSONField から CharField(50) に巻き戻されたが、DUPLICATE_CHECK_FIELDS の構成・スコア・判定ロジックに影響なし）
### 14.3.6 DUPLICATE_GENERIC_EMAIL_LOCALPARTS
代表メール判定の初期リスト。
値：['info', 'contact', 'support', 'sales', 'admin', 'office', 'mail', 'inquiry', 'help', 'service', 'shop', 'customer', 'reception']
## 14.4 モデル固有の TextChoices
各モデルの内部クラスとして定義する。

| クラス | 値 |
|---|---|
| Contact.Status | primary / active / inactive |
| ContactSns.SnsType | twitter / linkedin / facebook / instagram / github / blog / youtube / line（v1.6.1 新設・小文字統一） |
| Person.Status | active / merged / archived |
| OriginalImage.Status | pending / processing / opencv_processing / cards_extracted / extracted / garbage / failed |
| BusinessCard.Orientation | normal / rotate_90_cw / rotate_90_ccw / rotate_180 / mirror |
| BusinessCard.OcrStatus | pending / processing / done / failed |
| BusinessCard.OcrResult | business_card / not_business_card / insufficient_info / ocr_failed / others |
| DebugMask.MaskType | diff / edge / sat / or / closed |
| ContactFieldConfidence.Confidence | low / mid（high は記録対象外。v1.6.0 で medium→mid 統一） |
| DuplicateCandidate.Rank | exact_match / possible_high / possible_mid / possible_low / none |
| DuplicateCandidate.ReviewStatus | pending / merged / different_person / invalidated |
| PersonMergeLog.Status | undoable / undone / locked |

# 第15章（欠番）OCR パイプライン

旧 第 15 章は OCR/OpenCV 関連仕様のため v1.6.0 系 3 本に移行（旧第 5〜7 章の参照ブロック参照）。旧 §15.4（新規 Contact 生成の 3 段階トランザクション）は §10.4.3（Person.set_primary_contact）に責務統合。旧 §15.5（正規化ルール）は §11.9（Contact 正規化基盤）に移植。
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

# 第18章 認証・権限
## 18.1 v1.4.2 の認証状態
v1.3.4 と同様、認証は仮実装の状態を継続する。cards.views.get_current_user() がデフォルトユーザーを返す。
Phase 4（v1.5.0 以降）で LoginRequiredMixin による本格認証を導入予定。
## 18.2 マージ実行権限
v1.4.2 では、マージ実行に対する明示的な権限制約は設けない。ログイン済みユーザーなら誰でもマージ可能（仮認証下では実質的に誰でも可能）。
v1.5.0 以降の認証本格導入時に、ロールベース権限（管理者のみマージ実行可能、等）を導入する。
## 18.3 CSRF・画像アップロード
Django 標準の CSRF 保護を継続。画像アップロードはバリデーション必須（image_processor.validate_image を UploadForm.clean_image() 経由で呼ぶ）。

# 第19章 非機能要件
## 19.1 性能要件
- 画像アップロード → 名刺一覧表示まで：画像 1 枚あたり 30 秒以内
- OCR API 1 回あたりのタイムアウト：60 秒
- process_opencv の cron 起動間隔：1〜5 分（OpenCV は API 不使用のため高頻度実行可）
- process_ocr の cron 起動間隔：1〜5 分（API レートに応じて調整）
- stuck sweeper のしきい値：30 分（OpenCV / OCR 共通、settings.OCR_STUCK_THRESHOLD_MINUTES）
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

# 第20章 制約事項・将来の拡張
## 20.1 v1.4.2 の制約事項
- OCR 処理の起動ユーザーアクションは画像アップロードのみ
- 失敗した OriginalImage の retry 機能はユーザーに提供しない
- OriginalImage.status に processing 状態（CAS 中間状態）
- ファイル保存方式は DB 先・ファイル後（on_commit でリネーム）
- Claude API タイムアウトは 60 秒固定
- 画像アップロード上限：5MB / JPEG・PNG のみ
- 重複チェックの比較対象は主コンタクト（status='primary'）同士のみ
- 副コンタクト・旧コンタクト・archived は重複チェック対象外
- マージ実行は必ず DuplicateCandidate 経由（直接マージ不可）
- マージ復元は 1 段階前まで（多重マージは locked になる）
- different_person 判定後の自動再判定は行わない
- アクティブ↔プライマリー入れ替え機能は実装しない（第9章 9.8 参照）
- Person の archived 化は Django Admin のみ（一般ユーザー UI は v1.5.0 以降）
- 物理削除は一般ユーザー UI なし、Django Admin のみ
- 認証は仮実装、本格認証は v1.5.0 以降
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
- Celery による非同期化（重複チェック・OCR）
- 案件管理・メール配信・スケジュール管理・設備予約への発展
## 20.4 v1.7+ 送り事項（v1.6.2 で確定）
### EXIF 関連（Phase G）
- GPSAltitudeRef 等の BYTE 型タグの整数化（現在は ASCII decode + rstrip で空文字になる既知挙動。§11.3.2 参照）
- 元画像詳細画面の EXIF 業務利用 UI（撮影日時表示・GPS 地図表示等）
- 認証仕様書と exif_json の GPS 保存の整合（GPS 情報のアクセス制御上の未確定論点）
### ContactSns 関連（Phase F1）
- sns_id の本格正規化（現在は前後 strip のみ。URL/ID の表記ゆれ吸収は v1.7+ 送り、§11.6.7 参照）
### 開発スクリプト
- 旧 dev_create_test_contact_data.py の削除（dev_create_dup_test_data と共存中。削除は v1.7+ で検討、別表 B 参照）

# 第21章（欠番）

旧 第 21 章（Phase 4 実装スコープ）は v1.4.2 着手時の準備章であり、v1.6.0 では役割を終えたため章ごと削除（欠番）。
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
| コンタクトSNS | ContactSns |
| 信頼度メタ | ContactFieldConfidence |
| 重複候補 | DuplicateCandidate |
| マージ履歴 | PersonMergeLog |
| アクションログ | ActionLog |
| ユーザー | User |

### A.3 OriginalImage のフィールド

| 日本語名 | コーディング名 | 備考 |
|---|---|---|
| プライマリキー | id |  |
| アップロードユーザー | user |  |
| 元画像ファイル | image_file |  |
| 処理状態 | status | 7 値（別表 C.1） |
| 処理開始日時 | claimed_at | CAS 遷移時刻 |
| OCR 結果 JSON | raw_json | v1.4.3 で完全 deprecated（書き込み・読み出しなし。将来削除予定として残置） |
| デバッグ JSON | debug_json | v1.4.3 で正式記載。OpenCV 検出の中間データ |
| EXIF 情報 JSON | exif_json | v1.6.0 で追加。JSONField(null=True, blank=True)。アップロード受信直後の生バイト列から GPS 含む全 EXIF を JSON 化して保存。既存処理（exif_transpose 等）は変更せず読み出しステップを 1 つ追加。既存レコードは復元不可・新規分のみ。詳細は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §7.2 |
| 検出された名刺数 | detected_count |  |
| エラーメッセージ | error_message |  |
| 作成日時 | created_at |  |
| 更新日時 | updated_at |  |

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

v1.6.0 → v1.6.1 改訂後：19 件新規・4 件リネーム（型変更なし、すべて CharField のまま）・個別 SNS 5 件廃止（ContactSns 別テーブルへ統合、§4.4.4）。正本は OpenCV_OCR仕様書v1_6_1_Claude_API_JSON構造_コンタクトフィールド対応表 §3。`original_script` は Contact フィールドに持たない（raw_json 内のみ。json_parser が full_name にコピー）。

| 日本語名 | コーディング名 | 変更種別・備考 |
|---|---|---|
| プライマリキー | id | 既存 |
| 名刺 | business_card | 既存 |
| 人物 | person | 既存 |
| ステータス | status | 既存 |
| 前の人物 | previous_person | 既存 |
| 前のステータス | previous_status | 既存 |
| 重複チェック日時 | duplicate_checked_at | 既存 |
| 作成者 | created_by | 既存 |
| 更新者 | updated_by | 既存 |
| 言語コード | lang | 既存（OCR primary_lang の格納先・橋渡し） |
| 郵便番号 | postal_code | 既存 |
| フルネーム | full_name | 既存（OCR は original_script をコピーして格納・手動入力時必須化なし／必須は salutation_name） |
| 姓 | last_name | 既存 |
| 名 | first_name | 既存 |
| 敬称表記 | salutation_name | 既存。**手動入力時必須化（v1.6.0）**。DB は NULL 許容のまま Form/View で必須 |
| 会社名 | organization | **リネーム（旧 company）**。OCR org_name_full の格納先・橋渡し |
| 部署 | department | 既存 |
| 役職 | title | 既存 |
| 支店 | branch | 既存 |
| 住所 | address | 既存（OCR full_address の格納先・橋渡し。4 要素から compose_full_address が組み立て） |
| メール | email | 既存 |
| 個人直通電話 | personal_phone | **リネーム（旧 phone、CharField(50) のまま。v1.6.1 で JSONField から巻き戻し）** |
| 個人携帯 | mobile_phone | **リネーム（旧 mobile）** |
| 個人 FAX | personal_fax | **リネーム（旧 fax、CharField(50) のまま。v1.6.1 で JSONField から巻き戻し）** |
| ウェブサイト | website | 既存（v1.6.1 で OCR organization.website からのストレート流入経路を新設） |
| 資格 | qualification | 既存 |
| キャッチフレーズ | catchphrase | 既存 |
| メモ | notes | 既存（ユーザーメモ。OCR ai_analysis_notes とは無関係） |
| 名前並び順 | name_order | **新規**（CharField(20, choices)：last_first/first_last/single/other） |
| 他の名前部分 | other_name_parts | **新規**（CharField(255)：ミドルネーム・父称等） |
| 表示用完成形 | display_name | **新規**（CharField(255)） |
| 読み（カタカナ） | phonetic_name | **新規**（CharField(255)：記載なしでも推測 low、lang=ja 限定） |
| 通称・別名 | alias_name | **新規**（CharField(255)） |
| 会社固有名 | org_core_name | **新規**（CharField(255)：法人格除去後・導出・UPDATABLE 非掲載） |
| 法人格 | legal_entity_type | **新規**（CharField(50)） |
| 法人格コード | legal_entity_type_code | **新規**（CharField(10, choices)：CP/LLP/GOV/NPO/REL/EDU/MED/PRO/IND/OTH） |
| 法人格位置 | legal_entity_type_position | **新規**（CharField(10, choices)：Pre/Post/Mid） |
| 会社ドメイン | org_domain_name | **新規**（CharField(255)：email から導出・UPDATABLE 非掲載） |
| 国 | country | **新規**（CharField(2)：ISO 3166-1 alpha-2） |
| 中間行政区画 | region | **新規**（CharField(100)：空が正常値） |
| 市区町村 | city | **新規**（CharField(100)） |
| 残り住所 | rest_of_address | **新規**（CharField(500)） |
| 言語構成 | language_composition | **新規**（CharField(20, choices)：local_only/english_only/mix_bilingual/other） |
| 会社代表・部署電話 | org_phone | **v1.6.0 新規・v1.6.1 で CharField(50) 単一値**（E.164） |
| 会社・部署 FAX | org_fax | **v1.6.0 新規・v1.6.1 で CharField(50) 単一値**（E.164） |
| 手書きメモ | handwritten_text | **新規**（CharField(500)：事実保存用・UPDATABLE 非掲載） |
| その他印字テキスト | other_printed_text | **新規**（TextField：事実保存用・UPDATABLE 非掲載） |
| 敬称手動入力フラグ | salutation_name_is_manual | **新規**（BooleanField, default=False）。salutation_name の手動入力フラグ。True の場合 Contact.save() の自動再計算で上書きされない。OCR 経路では設定しない（OCR キー対応表に記載なし。§11.9.7 / v1.6 メール配信仕様書 §18.2） |
| 作成日時 | created_at | 既存 |
| 更新日時 | updated_at | 既存 |

新規追加は 20 件（上記 19 件 + salutation_name_is_manual）。salutation_name_is_manual は OCR キー対応関係を持たない（OCR 出力 → Contact 対応表は OCR キーがある項目のみ扱うため対応表には記載なし）が、本編側の永続仕様として Contact に追加する。

**Contact に持たないもの**：original_script（raw_json 内のみ。json_parser が full_name にコピー）、ai_analysis_notes（raw_json 内のみ。OCR チューニング用ログ）。**削除**：org_mobile_phone は新設しない（携帯＝個人のみ、mobile_phone に集約）。

**v1.6.1 で Contact から廃止した個別 SNS フィールド**：twitter / instagram / github / linkedin / facebook の 5 件は ContactSns 別テーブルに統合（別表 A.5.1・§4.4.4 参照）。

### A.5.1 ContactSns のフィールド（v1.6.1 新設）

| 日本語名 | コーディング名 | 変更種別・備考 |
|---|---|---|
| プライマリキー | id | v1.6.1 新規（UUIDField） |
| コンタクト | contact | v1.6.1 新規（FK(Contact, CASCADE, related_name="sns_accounts")） |
| SNSタイプ | sns_type | v1.6.1 新規（CharField(50, choices=SnsType)：8 種・小文字統一） |
| SNS識別子 | sns_id | v1.6.1 新規（CharField(500)：URL またはユーザー ID） |
| 作成日時 | created_at | v1.6.1 新規 |
| 更新日時 | updated_at | v1.6.1 新規 |

UniqueConstraint：fields=["contact", "sns_type", "sns_id"]、name="unique_contact_sns"。SnsType choices（8 種）：twitter / linkedin / facebook / instagram / github / blog / youtube / line。詳細は §4.4.4 / OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §3.7。

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

### A.11（欠番）

旧 A.11（DebugMask のフィールド）は §4.12 とともに削除（欠番）。
## 別表B 管理コマンド一覧

| コマンド | オプション | 用途 |
|---|---|---|
| process_opencv | --limit / --id | cron 起動。OpenCV パイプライン専用（BC 0 件の OriginalImage を pending→opencv_processing→cards_extracted、再実行禁止） |
| process_ocr | --limit / --id | cron 起動。OCR パイプライン専用（BC.ocr_status=pending を CAS→条件付き 2 回 OCR→Contact/Person 生成→OriginalImage.status 集計遷移） |
| retry_failed_ocr | --opencv / --ocr (必須) + --limit / --id / --dry-run | 失敗の差し戻し。--opencv は OpenCV 段階、--ocr は OCR 段階。共通ヘルパー cards/tasks/ocr_recovery.py に集約 |
| reconcile_card_images | --apply | DB ↔ MEDIA_ROOT 整合検査・修復 |
| dev_reset_ocr | --all / --id / --limit / --dry-run | 開発用 OCR リセット |
| check_duplicates | --limit（デフォルト 100） | cron 起動。重複チェック実行 |
| recheck_duplicates | --all / --dry-run | 運用用。判定ロジック変更後の全件再判定 |
| dev_reset_duplicates | --all / --id / --limit / --dry-run | 開発用重複チェックリセット |
| dev_create_dup_test_data | --reset / --user（既定 "test_data_user"）/ --total（既定 100）/ --seed / --verbose | 開発用。v1.6.1 重複検出テストデータ生成。配置：cards/management/commands/dev_create_dup_test_data.py（v1.6.2 追記） |

**dev_create_dup_test_data の詳細（v1.6.2 / F1）：**
- 用途：v1.6.1 重複検出テストデータ生成。4 種類（clone / minor_diff / major_diff / noise）を生成（比率は clone:0.20 / minor_diff:0.30 / major_diff:0.30 / noise:0.20）
- CLI オプション：`--reset`（既存テストデータ削除）/ `--user`（生成者、デフォルト "test_data_user"）/ `--total`（総 Contact 件数、デフォルト 100）/ `--seed`（乱数シード）/ `--verbose`（1 件ごとログ）
- v1.6.1 全フィールド生成・ContactSns 生成あり・ContactFieldConfidence は作成しない（全フィールドを疑似 high 扱い）
- 旧 dev_create_test_contact_data.py と共存（削除は v1.7+ で検討、§20.4 参照）

**開発環境セットアップ（v1.6.2 / F2）：**
- `pytest.ini` をプロジェクト直下に配置（`DJANGO_SETTINGS_MODULE = config.settings` / `python_files = test_*.py *_test.py tests.py`）
- `requirements.txt` に pytest / pytest-django を追記済み（v1.6.1 main 取り込み時）

## 別表C TextChoices 値一覧
### C.1 OriginalImage.Status

| コード値 | 表示名 | 意味 | 現実装で書き込まれるか |
|---|---|---|---|
| pending | 処理待ち | OpenCV / OCR 未実行 | ✅ アップロード時 / stuck 救済時 / retry_failed_ocr --opencv |
| opencv_processing | OpenCV 処理中 | OpenCV cron の CAS で claim 後、検出処理中 | ✅ process_opencv の _claim_lock |
| cards_extracted | OpenCV 完了・OCR 待ち | OpenCV 検出完了・BC 作成済み・OCR 未実行 | ✅ Run_Crop_Cards_From_OriginalImage 正常終了 / recalc_original_image_status_to_cards_extracted 差し戻し |
| processing | 処理中 | v1.4.2 改訂前の 1 本パイプライン用（後方互換のため物理残置） | ❌ 現フローでは書き込み経路を確認できず（巻末別表 F 不明点 #2 参照） |
| extracted | 完了 | OCR 完了（成功・名刺ではない・情報不足の混在含む） | ✅ _update_original_image_status 集計 |
| garbage | 無効画像 | OpenCV 検出 0 件 | ✅ Run_Crop_Cards_From_OriginalImage |
| failed | 処理失敗 | OpenCV 想定外例外、または全 BC が OCR 失敗 | ✅ Run_Crop_Cards_From_OriginalImage / _update_original_image_status |

### C.2 BusinessCard.Orientation

| コード値 | 表示名 | 意味 |
|---|---|---|
| normal | 正常 | 名刺の上が正立 |
| rotate_90_cw | 右 90 度回転 | 時計回りに 90 度 |
| rotate_90_ccw | 左 90 度回転 | 反時計回りに 90 度 |
| rotate_180 | 上下反転 | 180 度回転 |
| mirror | 鏡像 | 左右反転（誤認識ケース） |

### C.3 ContactFieldConfidence.Confidence

v1.6.0 で値域を `medium` → `mid` に統一（OCR 出力・json_parser・カスタムタグと表記を揃える）。既存レコードの `medium` はデータ移行マイグレーションで `mid` に一括更新。CheckConstraint も mid を許可。

| コード値 | 表示名 | 意味 |
|---|---|---|
| low | 低 | 信頼度が低い |
| mid | 中 | 信頼度が中程度（v1.4.4 までは medium） |

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

additional_role（別肩書追加）は v1.4.2 で削除した。別肩書追加は独立画面（9 番 PersonAddAdditionalRoleView）に分離。
13 番 UpdateActiveContactView では change_reason フィールドを置かない（fix 相当の処理に固定）。
### C.8 DuplicateMergeReason 値一覧（7 値）
DuplicateCandidate.review_result の merged 系（マージ画面専用）。

| コード値 | 表示名 |
|---|---|
| same_card | 同一名刺 |
| transfer | 異動・部署変更 |
| promotion | 役職変更・昇進等 |
| job_change | 転職 |
| additional_role | 別肩書追加（副業など） |
| name_change | 結婚等による姓変更 |
| other_merged | その他（マージ実行） |

【v1.4.2 ラベル微調整】 same_card の旧表示名「同一名刺（撮り直し・重複アップロード）」はボタン形式ラジオで長すぎたため「同一名刺」に短縮（ストック #69）。promotion の旧表示名「役職変更・昇進」は「役職変更・昇進等」に拡張し、PersonChangeReason 側の同名値とは独立に運用する。
### C.9 DifferentPersonReason 値一覧（3 値）
DuplicateCandidate.review_result の different_person 系。

| コード値 | 表示名 |
|---|---|
| same_name | 同姓同名の別人 |
| ocr_error | OCR 誤認識による誤検出 |
| other_different | その他（別人確定） |

【v1.4.2 ラベル変更】 same_name の表示名を「同姓同名」→「同姓同名の別人」に変更（ストック #68）。「同姓同名」だけでは「同姓同名で同一人物」とも読み取れる曖昧さがあり、別人判定の意図を明確にするため。enum value（same_name）は維持。
### C.10 Contact.Status

| コード値 | 表示名 | 意味 |
|---|---|---|
| primary | 主コンタクト | 1 人の Person につき 1 つだけ存在 |
| active | 副コンタクト | 別肩書など、現役で有効な情報 |
| inactive | 旧コンタクト | 転職前など、過去の情報 |

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

| コード値 | 表示名 | 意味 | 現実装でセットされる経路 |
|---|---|---|---|
| business_card | 名刺 | OCR 成功、Contact 生成あり | スキーマ検証 OK & is_business_card=true & 正規化成功 & has_minimum_info=true |
| not_business_card | 名刺ではない | is_business_card=False 判定 | cards[0].card_meta.is_business_card=false |
| insufficient_info | 情報不足 | has_minimum_info NG | スキーマ検証失敗 / 正規化失敗 / has_minimum_info=false のいずれか |
| ocr_failed | OCR 失敗 | card 単位の OCR 例外 | 画像なし / 画像読み込み失敗 / OCR API 例外 |
| others | その他 | 将来用の受け皿 | 現フェーズでは設定経路を持たないのが設計どおり |

`others` は **将来用の受け皿**として TextChoices に定義のみ存在する。v1.6.0 時点では設定経路を持たないのが設計どおり（OCR バックエンド追加等で現判定ロジックに分類しきれない結果が出たとき、新値マイグレーションなしで採用できる予備枠）。値の廃止・新値追加はしない。詳細は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 §7.3。
### C.15（欠番）

旧 C.15（DebugMask.MaskType）は §4.12 とともに削除（欠番）。OpenCV デバッグ仕様は v1.6.0 系 3 本へ。
## 別表D（欠番）

旧 別表D（v1.4.2 改訂項目一覧表）は v1.6.0 で削除（欠番）。v1.4.2 改訂経緯の索引のため本編では不要。
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
| v1.4.3 | 2026/05/18 | OpenCV/OCR タスク分離後の現状を実装基準で統合。矛盾箇所は実装を正として本文改訂。未実装項目（contacts/services 配下の正規化関数群、Run_Process_CardImages_With_OCR 公開サービス、json_normalizer.py の移動）は削除せず実装予定注記として保持。元画像 raw_json の用途移行、debug_json 正式記載、OriginalImage.status 7 値の明文化、別表 C.14 OcrResult.others の用途確認保留、別表 E（差分対照表）・別表 F（不明点）を新設。詳細は別表 E 参照 | コード君（Web 版） |
| v1.4.4 | 2026/05/18 | §5.3 / §15.3 の実装乖離記述を是正。v1.4.0 計画記述（スキーマ v1.4.0 化・パイプライン修正範囲）を『未着手・次フェーズ実施予定』に位置づけ直し、§15.3 項目 3 の削除済み pipeline_coordinator 前提記述を現状（§15.4 の 3 段階トランザクションで実装済み）に修正。修正範囲は §5.3 / §15.3 の 2 箇所のみで、他章・別表は v1.4.3 のまま不変 | コード君（Web 版） |
| v1.6.0 | 2026/05/21 | OCR/OpenCV 章（旧第 5/6/7/15 章・§4.12・別表 A.11/C.15）を v1.6.0 系 3 本に分離・参照化し、タイトルを「FreeGroup2 本編仕様書」に改称。旧 §15.5 を §11.9（Contact 正規化基盤）に移植。Contact フィールド 19 件新規・4 件リネーム・1 件削除を別表 A.5・§4.4.1 等に反映。confidence を mid 統一、salutation_name 手動入力時必須化、OriginalImage に exif_json 追加、DUPLICATE_CHECK_FIELDS をリネーム反映。第 21 章・別表 D/E/F を削除（すべて欠番維持・再採番せず） | オーパス君 |
| v1.6.1 | 2026/05/23 | 電話系 4 フィールド（personal_phone / personal_fax / org_phone / org_fax）を JSONField(default=list) から CharField(50) 単一値に巻き戻し（v1.6.0 の JSONField 化は前任サポート担当 Claude の見落としによる仕様逸脱の修正）。Contact 個別 SNS 5 件（twitter / instagram / github / linkedin / facebook）を廃止し ContactSns 別テーブル化（§4.4.4・別表 A.5.1・§11.6.7）。OCR organization.website を新設し既存 Contact.website に同名ストレート流し込み。UPDATABLE_FIELDS・マージ画面比較表示（§11.5.7）・Form（§11.6）を ContactSns 対応に更新。別表 A.5・§4.4.1・§11.9.5.1・§14.3.5 に反映 | オーパス君 |
| v1.6.2 | 2026/05/26 | UPDATABLE_FIELDS 24→31件更新（§11.5.7等）/ Phase F1（ContactSns InlineFormSet 4画面・§11.6.7新設・§11.4.2.1引き継ぎロジック）/ Phase F2（マージ画面SNS比較UI仕様明文化・§11.5.5・§11.5.7）/ Phase G（元画像詳細EXIF表示・§7.2反映・v1.7+送り事項追記）/ 実コード差分反映（salutation_name計算スナップショット方式・lang前方一致判定・カテゴリA正規化挙動）/ スクリプト追加（dev_create_dup_test_data・別表B追記）/ 環境セットアップ（pytest.ini・requirements.txt） | オーパス君 |
| v1.6.3 | 2026/06/28 | Contact.status の業務語を「主／副／旧コンタクト」に統一。§1.3 用語定義に「旧コンタクト」を追加、§4.4.2 値表・地の文（重複検出の比較範囲・重複チェック対象外列挙）・巻末コード名対照表の inactive 表示語を「非アクティブ」から「旧コンタクト」に是正。HIG（有効/履歴）との食い違いを解消。§4.4.2 に verbose_name 据置（admin 表示・バリデーション cascade への波及回避）の注記を追加。コード（コード値 status='inactive'・verbose_name・メソッド名）は不変、ドキュメントのみ | ジット君 |

# 巻末別表E・F（欠番）

旧 別表E（v1.4.2 → v1.4.3 差分一覧）・別表F（v1.4.3 時点の不明点）は v1.6.0 で削除（欠番）。OCR 関連の不明点は OpenCV_OCR仕様書v1_6_1_Claude_API_統合版 第 8 部に統合済み。
