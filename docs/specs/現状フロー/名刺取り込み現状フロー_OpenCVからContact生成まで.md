# 名刺取り込み 現状フロードキュメント

## OpenCVからContact生成まで（基準コミット：main、OpenCV／OCR分離後）

---

## 0. 本文書の位置付け

本文書は、FreeGroup2 の名刺取り込み処理が **現状の実装でどう動くか** を、頭から終わりまで通しで説明する現状把握ベース文書である。改善提案は含まない（改善議論は別フェーズ）。

- 基準コード：mainブランチ最新コミット時点の実装
- 一次情報：実装コード。仕様書・改訂メモは参考程度
- 仕様書と実装の食い違いがあった場合：**実装を正**とし、食い違いそのものを第10章にまとめる
- コードから読み取れない箇所：「不明」と明記して第11章にまとめる

---

## 1. 全体フローの俯瞰

ユーザー操作からデータベース反映までの全体は、独立した3層で構成されている。各層は別の管理コマンドとして cron 起動され、レコードの状態フィールドを介して疎結合に連携する。

| 層 | 担当 | 起動契機 | 担当する管理コマンド | 主な書き込み先 |
|---|---|---|---|---|
| 第1層 | アップロード受付 | ユーザーの画像送信（同期） | （View直下） | 元画像DBの新規作成 |
| 第2層 | OpenCV切り出し | cron（1〜5分間隔想定） | process_opencv | 名刺DBの新規作成 |
| 第3層 | OCR実行・コンタクト生成 | cron（1〜5分間隔想定） | process_ocr | 名刺DB更新、コンタクトDB・パーソンDB作成 |

第1層は同期処理。第2層・第3層は非同期で、ユーザー操作を待たせない。第2層と第3層はそれぞれ独立した cron 起動の管理コマンドであり、互いを直接呼ばない。

---

## 2. 入口：アップロード（第1層）

### 2.1 起動経路

ユーザーが名刺画像アップロード画面（UploadView）で画像ファイルを送信する。バリデーション通過後、View側でJPEG変換を実施したうえで元画像DBレコードを作成する。

実装：cards/views.py の UploadView.form_valid（L54-68）。

### 2.2 元画像DBレコード作成時の状態

| 項目 | 値 |
|---|---|
| 元画像DB.状態 | 処理待ち |
| 元画像DB.処理開始日時 | NULL |
| 元画像DB.OCR結果JSON | NULL |
| 元画像DB.デバッグJSON | NULL |
| 元画像DB.エラーメッセージ | 空文字 |
| 元画像DB.検出された名刺数 | 0 |
| 元画像DB.アップロードユーザー | 現在のリクエストユーザー（未認証時は最初のスーパーユーザー） |
| 元画像DB.元画像ファイル | アップロード画像をJPEG変換したもの |

名刺DBレコードはこの時点では作られない。OCR結果JSONも当然この時点ではNULL。

### 2.3 トランザクション

UploadView.form_valid内で `original.save()` を1回呼ぶのみ。明示的なトランザクション境界は使われていない（Djangoのデフォルトautocommitに依存）。画像ファイルの実体は `image_file.save()` の中でメディアディレクトリに書き込まれる。

---

## 3. OpenCV処理（第2層、process_opencv）

### 3.1 cron起動と引数

管理コマンド：cards/management/commands/process_opencv.py。

cron 起動時の引数：

| 引数 | 既定値 | 役割 |
|---|---|---|
| --limit | 10 | 1回の起動で処理する最大件数 |
| --id | NULL | 単一の元画像DB.IDをUUID指定（処理待ち状態の条件は維持、--limitは無視） |

### 3.2 stuck検出（処理冒頭）

cron起動の最初に、stuck検出を実行する（process_opencv.py L60-79）。

- 対象：元画像DB.状態 = 「OpenCV処理中」 かつ 元画像DB.処理開始日時が「現在時刻 - settings.OCR_STUCK_THRESHOLD_MINUTES（既定30分）」より古いレコード
- 救済処理：`_cleanup_stuck_record` （process_opencv.py L171-221）
  - 名刺DBが1件でも存在する場合：救済せずスキップ（名刺DB.カードインデックス不変担保のため）
  - 名刺DBが0件の場合：元画像DB.状態 を「処理待ち」に戻し、処理開始日時・OCR結果JSON・エラーメッセージ・検出された名刺数 をリセット

### 3.3 対象レコードの絞り込み

stuck検出後、本処理対象を `_collect_target_ids`（L137-168）で決定する。

| 引数指定 | 対象 |
|---|---|
| --id 指定あり | 指定UUIDの1件（状態=処理待ち の条件を維持） |
| --id 指定なし | 状態=処理待ち を作成日時昇順で最大 --limit 件 |

この時点では「BC 0件」の絞り込みは行われない（同じ絞り込みは後段の各IDループ内で再確認される）。

### 3.4 排他制御（CAS）

各対象IDについて、ループ内で `_claim_lock`（L224-238）を呼ぶ：

- 元画像DB.状態 = 処理待ち の行に対して、状態=OpenCV処理中・処理開始日時=現在時刻 を一発UPDATEで書き込む
- 影響行数が1なら排他取得成功、0なら他workerが先取り済みまたはstatus driftとしてスキップ

CAS成功後にあらためて名刺DB.元画像が当該IDの行が存在するかをチェック（L92-97）。1件でも存在すれば「再実行禁止（カードインデックス不変担保）」としてスキップする。

### 3.5 OpenCV切り出し処理本体

排他取得した元画像DBレコードについて、`Run_Crop_Cards_From_OriginalImage(original).run()` を呼ぶ（cards/tasks/crop_cards.py L25-）。

#### 3.5.1 入力チェック

呼び出し時点で 元画像DB.状態 が「OpenCV処理中」でなければ、状態を「処理失敗」に切り替え、エラーメッセージに「CASが成立していない可能性があります」と記録して終了する（L53-70）。

#### 3.5.2 検出本体

`detect_cards_with_debug(image_file.path)` （cards/services/detectors/opencv_detector.py）を呼ぶ。戻り値の `attempts` 配列の最後の試行から、検出結果（透視変換後の各カード画像）を取り出す。

検出デバッグ情報は `save_debug_data` でDebugMaskレコードおよび元画像DB.デバッグJSONに保存される（失敗してもログ警告のみで処理続行）。

#### 3.5.3 結果分岐

| 結果 | 元画像DB.状態 | 元画像DB.検出された名刺数 | 名刺DBの作成 |
|---|---|---|---|
| 検出 0件 | 「無効画像」 | 0 | 作らない |
| 検出 1件以上 | 「OpenCV完了・OCR待ち」 | 検出数 | 各カードについて1件ずつ作成 |
| 想定外例外 | 「処理失敗」 | （途中まで更新） | （途中まで作成済み） |

#### 3.5.4 1カードごとの保存処理

各検出結果（透視変換済みPIL Image）について `_create_card`（L115-158）を呼ぶ。

カード画像保存：`save_card_image`（cards/tasks/card_cropper.py）が **最終パスへ同期書き** を行う（旧仕様の tmp 書き＋ on_commit リネーム方式ではない）。保存パスは `MEDIA_ROOT/cards/YYYY/MM/DD/{元画像UUID}-{カードインデックス}.jpg`。

| 結果 | 名刺DB.名刺画像 | 名刺DB作成 | 元画像DB.エラーメッセージ |
|---|---|---|---|
| 画像保存成功 → 名刺DB保存成功 | 保存パス | 1件作成 | 追記なし |
| 画像保存失敗 | NULL（空欄） | 1件作成（画像なし） | 「カードインデックス=N: 切り抜き失敗（理由）」を追記 |
| 画像保存成功 → 名刺DB保存失敗 | 保存パス | 作成されず | 「カードインデックス=N: DB保存失敗（型: 内容）」を追記。書き込み済み画像ファイルは削除 |

切り抜きに失敗しても名刺DBは作る点に注意（画像なしのレコード）。これにより、OCR処理側で「カード画像なしの名刺DB」を検知して INSUFFICIENT な失敗として記録できる。

#### 3.5.5 トランザクション境界

各カード（`_create_card`内）について `transaction.atomic()` で囲まれた1つのトランザクション。あるカードの保存失敗が他のカードに波及しない。元画像DB自体の状態更新は最後の `finally` 節で別途 atomic 書き込み（L103-113）。

---

## 4. OCR処理（第3層、process_ocr）

### 4.1 cron起動と引数

管理コマンド：cards/management/commands/process_ocr.py。

| 引数 | 既定値 | 役割 |
|---|---|---|
| --limit | 10 | 1回の起動で処理する最大件数 |
| --id | NULL | 単一の名刺DB.IDをUUID指定（OCR処理状態=OCR待ち の条件は維持、--limitは無視） |

### 4.2 stuck検出（処理冒頭）

cron起動の最初に、名刺DB単位でstuck検出を実行する（process_ocr.py L54-73）。

- 対象：名刺DB.OCR処理状態 = OCR処理中 かつ 名刺DB.処理開始日時が「現在時刻 - settings.OCR_STUCK_THRESHOLD_MINUTES（既定30分）」より古いレコード
- 救済処理：`reset_bc_to_pending`（cards/tasks/ocr_recovery.py L19-88）
  - 名刺DBを行ロック取得し、OCR処理状態が想定通りなら以下を実行
  - 関連コンタクトDBを削除（CASCADE でコンタクトフィールド信頼度メタも削除）
  - 削除したコンタクトが参照していたパーソンDBのうち、他から参照されていないものを削除
  - 名刺DB を OCR待ち にリセット：処理開始日時・1回目OCR生JSON・2回目OCR生JSON・OCR処理結果・向き（正位に戻す）・エラーメッセージ
  - 名刺画像は **保持**（OpenCV結果を捨てない）
  - 続けて `recalc_original_image_status_to_cards_extracted` を呼び、所属元画像DB の状態が「完了」または「処理失敗」だった場合は「OpenCV完了・OCR待ち」に戻す

### 4.3 対象レコードの絞り込み

`_collect_target_ids`（process_ocr.py L121-149）で対象IDを取得する。

| 引数指定 | 対象 |
|---|---|
| --id 指定あり | 指定UUIDの1件（OCR処理状態=OCR待ち の条件を維持） |
| --id 指定なし | OCR処理状態=OCR待ち を、所属元画像DBの作成日時昇順 → 同一元画像DB内ではカードインデックス昇順 で最大 --limit 件 |

### 4.4 排他制御（CAS）

各名刺DB.IDについて `_claim_lock`（L152-162）を呼ぶ：

- OCR処理状態 = OCR待ち の行に対して、OCR処理状態=OCR処理中・処理開始日時=現在時刻 を一発UPDATEで書き込む
- 影響行数が1なら成功、0ならスキップ

### 4.5 OCRサービスのライフサイクル

cron 1回起動につき1個の `OcrService` インスタンスを生成し、全名刺DBのループで使い回す（process_ocr.py L84）。これにより、プロンプトとJSONスキーマのファイル読み込みは cron 起動ごとに1回だけになる（インスタンス内 `_prompt_cache` / `_tool_input_schema_cache` でキャッシュ）。

### 4.6 名刺DBごとのOCR処理：process_cardimage_with_ocr

実装：cards/tasks/ocr_pipeline.py L165-297。

呼び出し時点で 名刺DB.OCR処理状態 が「OCR待ち」または「OCR処理中」でなければ ValueError を発生させて止まる（L186-191）。それ以外の例外は外に漏らさず、エラーメッセージに集約する設計（L176）。

#### 4.6.1 段階1：名刺画像なしの早期分岐

名刺DB.名刺画像 が空（OpenCV段階で切り抜き失敗した場合）：

- 名刺DB.OCR処理状態 = OCR失敗
- 名刺DB.OCR処理結果 = OCR失敗
- 名刺DB.エラーメッセージ = 「名刺画像=Noneのため OCR 実行不可」
- 元画像DBの集計遷移を呼んで終了（コンタクトDB・パーソンDBは作らない）

#### 4.6.2 段階2：画像読み込み

PIL で 名刺DB.名刺画像 を読み込む。失敗すれば段階1と同じ流れで失敗確定。

#### 4.6.3 段階3：OCR API呼び出し（条件付き2回OCR）

`extract_carddata_via_ocr(card_image, ocr_service)` （L65-119）が処理本体。

1. 1回目OCR：`ocr_service.run_ocr(card_image)` で生JSONを取得。1回目で例外が発生したら呼び出し元に伝播（コンタクトDB・パーソンDBは作らずに 4.6.7 のOCR失敗確定処理へ）。
2. 1回目の生JSONから向きを取り出す（`_extract_orientation`）。cards[0].card_meta.orientation を防御的に読み、不正値・欠落なら「正位」扱い。
3. 向き=「正位」のとき：2回目はスキップ。戻り値の 2回目OCR生JSON は NULL。
4. 向き=「正位」以外のとき：`_rotate_card_image` で画像を補正回転（時計回り90°→反時計回り90°で戻す、反時計回り90°→時計回り90°で戻す、180°→180°、鏡像→水平反転）してから2回目OCRを実行。
5. 2回目失敗時：例外をcatchして 2回目OCR生JSON = NULL とし、エラーメッセージを戻り値に含める。

#### 4.6.4 段階4：採用する生JSONの選択

`adopted_raw_json` を以下のルールで決定（L241）：

| 状況 | 採用される生JSON |
|---|---|
| 2回目OCR成功（向きが正位以外で2回目走った） | 2回目OCR生JSON |
| 2回目OCRスキップ（向きが正位） | 1回目OCR生JSON |
| 2回目OCR失敗（向きが正位以外だが2回目で例外） | 1回目OCR生JSON |

#### 4.6.5 段階5：名刺画像の上書き保存

2回目OCRが走っていたケースでは、補正回転後の画像を **同パスへ上書き** する（`_overwrite_card_image`、L318-325）。2回目の成否に関わらず、画像補正自体は実施したため上書きする。トランザクション外で実行。

名刺DB.名刺画像 のフィールド値（保存パス）は変わらない（同一パス上書きのため）。

#### 4.6.6 段階6：OCR結果の判定

`_determine_ocr_result(adopted_raw_json, orientation_detected)` （L328-392）で、採用された生JSONから OCR処理結果 とコンタクト作成用辞書を導出する。

| 判定 | OCR処理結果 | コンタクト作成用辞書 | コンタクトDB・パーソンDB作成 |
|---|---|---|---|
| 採用生JSONのcards配列が空 or cards[0]が辞書でない | 情報不足 | NULL | 作らない |
| JSON Schema検証失敗（cards[0]がスキーマに合わない） | 情報不足 | NULL | 作らない |
| cards[0].card_meta.is_business_card = false | 名刺ではない | NULL | 作らない |
| 正規化処理（normalize_to_contact_dict）が例外発生 | 情報不足 | NULL | 作らない |
| has_minimum_info が False | 情報不足 | NULL | 作らない |
| 上記いずれにも該当しない | 名刺 | 辞書を返す | 作る |

JSON Schema は `docs/json_schema/v1.3.0/combined_response.json` の `properties.cards.items` を参照（L434-452）。

#### 4.6.7 段階7：名刺DBの確定書き込みとコンタクト・パーソン作成

ここまでの結果を1つの `transaction.atomic()` ブロック（L264-294）で書き込む：

1. 名刺DB の更新：1回目OCR生JSON、2回目OCR生JSON、向き、OCR処理結果、OCR処理状態（OCR完了）、エラーメッセージ、処理開始日時=NULL
2. OCR処理結果が「名刺」のときのみ：パーソンDBを新規作成
3. 続けてコンタクトDBを新規作成：名刺=この名刺DB、パーソン=直前に作ったパーソン、ステータス=主コンタクト、その他フィールドはコンタクト作成用辞書を展開して設定
4. `person.set_primary_contact(contact)` を呼んで、パーソンDB.主コンタクト と コンタクト.ステータス=主コンタクト の二重管理を整合させる
5. 信頼度マップ（normalize_to_contact_dict の戻り値、向き補正済み）から、low / medium のフィールドだけコンタクトフィールド信頼度メタを作成（high は記録しない設計）

OCR API失敗（4.6.3 で1回目から例外）の場合は、`_finalize_failed` （L300-315）が呼ばれ、名刺DB.OCR処理状態 = OCR失敗 / OCR処理結果 = OCR失敗 で確定する。コンタクトDB・パーソンDBは作らない。

#### 4.6.8 段階8：元画像DBの集計遷移

最後に `_update_original_image_status(original_image_id)` （L395-431）を呼ぶ。詳細は次章 5 に記載。

---

## 5. 元画像DBの集計遷移（複数名刺対応）

1枚の元画像に複数の名刺が写っていた場合、名刺DBは複数生成される。元画像DBの最終状態は、所属する全名刺DBのOCR処理状態を集計して決まる。

実装：cards/tasks/ocr_pipeline.py の `_update_original_image_status`（L395-431）。

### 5.1 集計タイミング

各名刺DBのOCR処理が完了するたび（process_cardimage_with_ocr の末尾）に呼ばれる。同じ元画像DBの別名刺DBが並列処理されていてもレースしないよう、`select_for_update` で元画像DBを行ロックする。

### 5.2 集計ルール

| 所属全名刺DBのOCR処理状態 | 元画像DB.状態（新） |
|---|---|
| OCR待ち or OCR処理中 を1件でも含む | 変更しない（OpenCV完了・OCR待ち のまま） |
| 全てが OCR失敗 | 処理失敗 |
| 全てが OCR完了 のみ、または OCR完了 と OCR失敗 の混在 | 完了 |

「OCR完了」となる名刺DBの OCR処理結果 は、名刺・名刺ではない・情報不足 のいずれでもよい。つまり「全カードが名刺ではない」と判定された元画像でも、元画像DB.状態 は「完了」になる。

---

## 6. 名刺DBの OCR状態フィールドの一覧

OCR処理に関わる名刺DB側のフィールドは2系統ある。

### 6.1 OCR処理状態

cards/models.py L157-161 の `OcrStatus`。処理の進行状態を表す。

| 値 | 意味 | 遷移元 → 遷移先 |
|---|---|---|
| OCR待ち | これからOCRを実行する | process_opencv 完了時の初期値（_create_card で設定） |
| OCR処理中 | OCR cron が排他取得して処理中 | process_ocr の CAS で OCR待ち → OCR処理中 |
| OCR完了 | OCR が完了した（成功・名刺ではない・情報不足のいずれも含む） | process_cardimage_with_ocr 正常終了時 |
| OCR失敗 | OCR が技術的に失敗した | process_cardimage_with_ocr で API例外・画像読み込み失敗・画像なし のいずれか |

### 6.2 OCR処理結果

cards/models.py L150-155 の `OcrResult`。OCR完了時の業務的な結果分類を表す。OCR処理状態 = OCR完了 または OCR失敗 のときに値が入る。それ以前は NULL。

| 値 | 意味 | 条件 |
|---|---|---|
| 名刺 | 名刺として正常に取り込めた | スキーマ検証OK & is_business_card=true & 正規化OK & has_minimum_info=true |
| 名刺ではない | OCRは成功したが名刺ではないと判定された | is_business_card=false |
| 情報不足 | OCRは成功したが氏名等の最低限情報が取れなかった | スキーマ検証失敗・正規化失敗・has_minimum_info=false のいずれか |
| OCR失敗 | OCR が技術的に失敗した | 画像なし・画像読み込み失敗・OCR API例外 |
| その他 | （TextChoices上は定義されているが、本実装パスでは設定されない） | 不明 |

「その他」値は OcrResult TextChoices に定義されているが、process_cardimage_with_ocr のフロー上はこの値を設定する経路が見当たらない（第11章 不明点 #1 参照）。

---

## 7. 失敗時の差し戻し（retry_failed_ocr）

エンドユーザー向けではない、開発・運用ツール（cards/management/commands/retry_failed_ocr.py L1-15）。

### 7.1 --opencv モード

対象：元画像DB.状態 = 処理失敗 かつ 名刺DBが0件 の元画像DB（OpenCV段階で失敗したもの）

処理：元画像DBの状態を「処理待ち」に戻す。次回 process_opencv で拾われる。

### 7.2 --ocr モード

対象：名刺DB.OCR処理状態 = OCR失敗 の名刺DB（OCR段階で失敗したもの）

処理：`reset_bc_to_pending` を呼ぶ（4.2 と同じヘルパー、ただし第2引数 `expected_statuses=("failed",)` 指定）。BC を OCR待ち に戻し、関連コンタクトDB と孤立パーソンDB を削除。名刺画像は不変。所属元画像DBの状態が「完了」または「処理失敗」だった場合は「OpenCV完了・OCR待ち」に戻す。

---

## 8. トランザクション境界の一覧

実装上の `transaction.atomic()` 境界は以下のとおり。

| 場面 | 境界 | 何を1つに括っているか |
|---|---|---|
| アップロード時の元画像DB保存 | なし（autocommit） | original.save() 1回 |
| OpenCV stuck救済 | あり（1元画像DB単位） | 元画像DBの状態リセット |
| OpenCV処理 - 名刺DB作成 | あり（1カード単位） | 1カード分の名刺DB INSERT |
| OpenCV処理 - 元画像DB状態確定 | あり（1元画像DB単位） | 全カード処理後の状態・エラーメッセージ・検出数のまとめ書き |
| OCR stuck救済 | あり（1名刺DB単位） | 名刺DBリセット＋関連コンタクトDB削除＋孤立パーソンDB削除 |
| OCR名刺DB更新＋コンタクト・パーソン作成 | あり（1名刺DB単位） | 名刺DB更新・パーソンDB作成・コンタクトDB作成・set_primary_contact・コンタクトフィールド信頼度メタ作成 |
| OCR失敗確定 | あり（1名刺DB単位） | 名刺DBの失敗確定書き込み |
| OCR元画像DB集計遷移 | あり（1元画像DB単位、select_for_update付き） | 状態更新 |
| 補正画像上書き | なし（トランザクション外） | 物理ファイルの上書き保存 |

---

## 9. ユーザーから見たレコード状態の進み方

ユーザーが1枚アップロードしてから完了するまでの、元画像DBの状態の典型遷移：

| 段階 | 元画像DB.状態 | 元画像DB.OCR結果JSON | 名刺DB | コンタクトDB / パーソンDB |
|---|---|---|---|---|
| アップロード直後 | 処理待ち | NULL | 0件 | 0件 |
| process_opencv のCAS成立後 | OpenCV処理中 | NULL | 0件 | 0件 |
| OpenCV検出成功（N枚検出） | OpenCV完了・OCR待ち | NULL | N件（OCR処理状態=OCR待ち） | 0件 |
| OpenCV検出0件 | 無効画像 | NULL | 0件 | 0件 |
| OpenCV処理で想定外例外 | 処理失敗 | NULL | 0件（または途中まで） | 0件 |
| process_ocr で一部完了 | OpenCV完了・OCR待ち | NULL | N件（一部 OCR完了、残り OCR待ち） | 完了分のみ存在 |
| process_ocr で全完了（全部「名刺」と判定） | 完了 | NULL | N件（全部 OCR完了、OCR処理結果=名刺） | N人 / N件 |
| process_ocr で全完了（全部「名刺ではない」） | 完了 | NULL | N件（全部 OCR完了、OCR処理結果=名刺ではない） | 0件 |
| process_ocr で全失敗 | 処理失敗 | NULL | N件（全部 OCR失敗） | 0件 |

元画像DB.OCR結果JSON フィールドは、現実装では一貫してNULLのままになる。OCR結果は名刺DB側の 1回目OCR生JSON / 2回目OCR生JSON に保存される（第10章 差分 #1 参照）。

---

## 10. 既存仕様書（v1.4.2統合最終版）との差分

### 10.1 元画像DB.デバッグJSON フィールドは仕様書に記載なし

実装：cards/models.py L46-50 に `debug_json` JSONField が存在。OpenCV検出のデバッグ情報（中間データ）を保存する用途。

仕様書：別表 A.3（L3373-3386）の元画像DBフィールド一覧、および §4.2 本文のいずれにも記載なし。

### 10.2 元画像DB.OCR結果JSON フィールドの用途が変わっている

仕様書：§4.2 / 別表 A.3 では、元画像DB.OCR結果JSON（raw_json）に「OCR結果JSON全体（cards配列含む）も raw_json に集約して保存する」と記載（cards/models.py のクラスdocstring L10-14 も同じ）。

実装：現実装の process_cardimage_with_ocr では元画像DB.OCR結果JSON への書き込みが行われない。OCR結果の生JSONは名刺DB側の 1回目OCR生JSON / 2回目OCR生JSON に保存される。元画像DBの cards/views.py L157 のコメントにも「v1.5.0: OriginalImage.raw_json の読み出しは廃止。OCR結果は BC.raw_json_1/2 を参照」と書かれており、フィールド自体は残置されているが使われていない状態と読み取れる。

### 10.3 名刺DBのクラスdocstringが古い記述のまま

実装：cards/models.py L130-135 の名刺DBクラスdocstringに「raw_json / ocr_status / error_message は持たず、OriginalImage.raw_json["cards"][card_index] の該当要素を参照する」と記載。

実態：実装は同モデル内に 1回目OCR生JSON / 2回目OCR生JSON / OCR処理状態 / OCR処理結果 / エラーメッセージ をすべて **持っている**（L186-194）。docstringが v1.2.1 時点の旧設計のまま残置されている。

### 10.4 元画像DB.状態 の値が仕様書記載より多い

仕様書：§4.2.1（L205-210 周辺）では「処理待ち / 処理中 / 完了 / 無効画像 / 処理失敗」相当の値を記載（v1.4.2 改訂前1本パイプライン時代の前提）。「OpenCV処理中」「OpenCV完了・OCR待ち」が物理残置として一部記述あり（L209）。

実装：cards/models.py L16-31 に「処理待ち / OpenCV処理中 / OpenCV完了・OCR待ち / 処理中 / 完了 / 無効画像 / 処理失敗」の7値が定義されている。「処理中」は本フロー上は使われていないように見えるが、TextChoicesには残置されている。

### 10.5 OcrResult.「その他」 が実装にあるが本フローでは使われない

実装：cards/models.py L150-155 の OcrResult TextChoices に「その他」（OTHERS = "others"）が定義されているが、本ドキュメントの第4章で示した process_cardimage_with_ocr のフロー上、この値が設定される経路は見当たらない。

仕様書：別表 C.14 ではこの値の意味付けが必ずしも明示されていない（不明点 #1 参照）。

### 10.6 OpenCV処理時の名刺画像保存方式が「同期書き」に変更

仕様書：v1.4.2 改訂サマリー #11 で「v1.4.2 のパイプライン分離に伴い、tmp 書き＋ on_commit リネーム方式から同期書きに変更」と記載（L678-679 付近）。

実装：cards/tasks/card_cropper.py の save_card_image が同期書きを実装しており、tmp 書き＋リネーム方式は存在しない。実装は仕様書 v1.4.2 改訂方針と一致。

### 10.7 元画像DB.OCR結果JSON のリセットタイミングが仕様書と異なる可能性

実装：process_opencv の stuck救済（`_cleanup_stuck_record`）と retry_failed_ocr --opencv では、元画像DBを処理待ちに戻す際に元画像DB.OCR結果JSON を NULL にリセットする（process_opencv.py L204）。

仕様書：元画像DB.OCR結果JSON のリセット仕様について明示的な記述を本確認では見つけられず。

### 10.8 「Run_Process_CardImages_With_OCR」公開サービス関数は実装されていない

仕様書：§13.4.1（L2649）で OCR パイプライン上位の公開サービスとして `Run_Process_CardImages_With_OCR()` を Pascal_Snake_Case で定義。

実装：当該クラス/関数は存在しない。process_ocr.py が直接 `process_cardimage_with_ocr(bc, ocr_service)` を1名刺DBごとに呼ぶ。OpenCV側は対称的に `Run_Crop_Cards_From_OriginalImage` クラスが存在する。OCR側だけ命名規約と公開サービス層が抜けている状態。

### 10.9 「contacts/services/normalization.py」フィールド単位正規化関数群が未実装

仕様書：§15.5.2 で「contacts/services/normalization.py に各フィールドの正規化関数（純関数）を配置」と明記。§15.5.3 で6種類の正規化ルール（フルネーム・会社名・電話番号・メール・住所・郵便番号）を詳細に規定。

実装：contacts/services/ ディレクトリ自体が存在しない。`normalize_full_name` / `normalize_company` / `normalize_phone` / `normalize_email` / `normalize_address` / `normalize_postal_code` のいずれも grep で 0件。

実態：現実装のフィールド値正規化は、cards/services/json_normalizer.py の `_extract_value_and_confidence` 内での `str(raw_value).strip()`（前後空白除去）のみ。仕様書 §15.5.3 で規定された全角半角統一・株式会社統一・電話番号の数字抽出・メール小文字化・住所漢数字変換・郵便番号数字化 は **どれも実行されていない**。

### 10.10 「cards/services/json_normalizer.py」が削除対象だが残置

仕様書：§21.2（L3242）で削除対象に明記。代替ファイル `contacts/services/json_parser.py` に移動・拡張するとされる。

実装：cards/services/json_normalizer.py が現役で残置されており、process_cardimage_with_ocr から import されている（cards/tasks/ocr_pipeline.py L28-31）。`contacts/services/json_parser.py` は存在しない。

### 10.11 OcrService が使用するモデル

実装：cards/tasks/ocr_service.py L30 で `DEFAULT_MODEL = "claude-sonnet-4-6"`。

仕様書：§7.1 では Claude Sonnet 4.6 を採用と記載（実装と一致）。

---

## 11. コードから読み取れない（不明）箇所

本ドキュメント作成時点で、実装コードからは挙動を確定できなかった点を列挙する。

1. **OcrResult.「その他」の使われ方**：TextChoices に定義されているが、process_cardimage_with_ocr のフロー上はこの値が設定される経路を見つけられなかった。手動作成・別肩書追加など本フロー外で使われている可能性はあるが、本ドキュメントのスコープでは確認できなかった。

2. **元画像DB.「処理中」状態の使われ道**：STATUS_PROCESSING は TextChoices に残置されているが、OpenCV/OCR 分離後のフローでこの値に遷移する経路を見つけられなかった。旧1本パイプライン時代の後方互換用残置かどうかは仕様書側で物理残置と説明があるが、現実装で実際にこの値が書き込まれる箇所があるかは断定できない。

3. **元画像DB.OCR結果JSON フィールドの将来計画**：実装上は書き込みも読み出しもされていないが、フィールドはまだ削除されていない。フィールド自体を残す意図か、将来削除予定かは、コードからは読み取れない。

4. **OpenCV検出デバッグの保存先**：`save_debug_data` は DebugMask レコードを作るとともに元画像DB.デバッグJSON にも書き込んでいるが、両者の使い分け（同じ情報を二重に持つのか、それぞれ別の情報を持つのか）の業務上の意味は、本ドキュメント作成時点の cards/services/opencv_debug_cache.py の精読範囲では断定できない。

5. **白黒反転リトライの発生条件**：DebugMask の attempt_no=2 として OR / クロージング のマスクが追加保存される設計になっている（cards/models.py L260-261）が、これが具体的にどんな入力画像のときに発生するかは、本ドキュメント作成時点の確認範囲では追えていない。

6. **2回目OCR時の card_meta.orientation の解釈**：補正画像で再OCRした2回目の生JSON でも orientation フィールドが返ってくる可能性があるが、その値が「正位」以外だった場合の挙動が `_determine_ocr_result` 内の `orientation_for_confidence` 経由でどう信頼度に効くかは、補正済み画像なので通常は「正位」が返るはずという前提に依存している。OCRが繰り返し非正位を返した場合の業務的な扱いは仕様書側にも明示が見当たらない。

7. **`_determine_ocr_result` 内のスキーマ検証で参照される JSON Schema のバージョン**：実装は `docs/json_schema/v1.3.0/combined_response.json` を読み込んでいる（cards/tasks/ocr_pipeline.py L442）。OCR プロンプト側で要求しているスキーマバージョン（OcrService.SCHEMA_VERSION = "1.3.0"）と一致しており整合的だが、将来 v1.4.0 等に上げる予定があるかどうかはコードから読み取れない。

8. **OcrService の `_prompt_cache` / `_tool_input_schema_cache` の読み込み元ファイル**：本ドキュメント作成時点では `OcrService.run_ocr` の入口部分のみ確認しており、プロンプト・スキーマファイルの具体パスは詳細追跡していない。

---

## 12. 主要な実装ファイル一覧

本ドキュメント作成時に参照した主要ファイル（リポジトリルートからの相対パス）：

| 役割 | パス |
|---|---|
| 元画像DB / 名刺DB / デバッグマスクDB モデル定義 | cards/models.py |
| アップロード受付 View | cards/views.py（UploadView 部分） |
| OpenCV 管理コマンド | cards/management/commands/process_opencv.py |
| OCR 管理コマンド | cards/management/commands/process_ocr.py |
| 失敗差し戻し管理コマンド | cards/management/commands/retry_failed_ocr.py |
| OpenCV 切り出しパイプライン | cards/tasks/crop_cards.py |
| OCR パイプライン本体 | cards/tasks/ocr_pipeline.py |
| OCR 差し戻し共通ヘルパー | cards/tasks/ocr_recovery.py |
| Claude API 連携 | cards/tasks/ocr_service.py |
| 名刺画像保存 | cards/tasks/card_cropper.py |
| OpenCV 検出本体 | cards/services/detectors/opencv_detector.py |
| OpenCV デバッグ保存 | cards/services/opencv_debug_cache.py |
| 生JSON → コンタクト辞書 変換 | cards/services/json_normalizer.py |
| 最低限情報判定 | cards/services/has_minimum_info.py |
| コンタクトDB / コンタクトフィールド信頼度メタDB モデル定義 | contacts/models.py |
| パーソンDB モデル定義 | persons/models.py |
| 設定値（stuck閾値・APIキー等） | config/settings.py |

---

## 13. 巻末別表：日本語名とコード上の名称の対照

### 13.1 モデル名

| 日本語名 | コーディング名 |
|---|---|
| 元画像DB | OriginalImage |
| 名刺DB | BusinessCard |
| デバッグマスクDB | DebugMask |
| コンタクトDB | Contact |
| コンタクトフィールド信頼度メタDB | ContactFieldConfidence |
| パーソンDB | Person |

### 13.2 元画像DBのフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| アップロードユーザー | user |
| 元画像ファイル | image_file |
| 状態 | status |
| 処理開始日時 | claimed_at |
| OCR結果JSON | raw_json |
| デバッグJSON | debug_json |
| エラーメッセージ | error_message |
| 検出された名刺数 | detected_count |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### 13.3 元画像DB.状態 の値

| 日本語名 | コーディング名 |
|---|---|
| 処理待ち | pending |
| OpenCV処理中 | opencv_processing |
| OpenCV完了・OCR待ち | cards_extracted |
| 処理中 | processing |
| 完了 | extracted |
| 無効画像 | garbage |
| 処理失敗 | failed |

### 13.4 名刺DBのフィールド

| 日本語名 | コーディング名 |
|---|---|
| プライマリキー | id |
| 元画像 | original_image |
| 名刺画像 | card_image |
| カードインデックス | card_index |
| 向き | orientation |
| OCR処理結果 | ocr_result |
| OCR処理状態 | ocr_status |
| 1回目OCR生JSON | raw_json_1 |
| 2回目OCR生JSON | raw_json_2 |
| 処理開始日時 | claimed_at |
| エラーメッセージ | error_message |
| 作成日時 | created_at |
| 更新日時 | updated_at |

### 13.5 名刺DB.向き の値

| 日本語名 | コーディング名 |
|---|---|
| 正位 | normal |
| 時計回り90° | rotate_90_cw |
| 反時計回り90° | rotate_90_ccw |
| 180°回転 | rotate_180 |
| 鏡像 | mirror |

### 13.6 名刺DB.OCR処理状態 の値

| 日本語名 | コーディング名 |
|---|---|
| OCR待ち | pending |
| OCR処理中 | processing |
| OCR完了 | done |
| OCR失敗 | failed |

### 13.7 名刺DB.OCR処理結果 の値

| 日本語名 | コーディング名 |
|---|---|
| 名刺 | business_card |
| 名刺ではない | not_business_card |
| 情報不足 | insufficient_info |
| OCR失敗 | ocr_failed |
| その他 | others |

### 13.8 コンタクトDB.ステータス の値

| 日本語名 | コーディング名 |
|---|---|
| 主コンタクト | primary |
| 副コンタクト | active |
| 非アクティブ | inactive |

### 13.9 主な関数・クラス

| 日本語上の呼称 | コーディング上の名前 | 配置 |
|---|---|---|
| OpenCV切り出し上位クラス | Run_Crop_Cards_From_OriginalImage | cards/tasks/crop_cards.py |
| OpenCV 1カード保存 | _create_card（同クラスのメソッド） | cards/tasks/crop_cards.py |
| 名刺画像 同期書き | save_card_image | cards/tasks/card_cropper.py |
| OpenCV 検出本体 | detect_cards_with_debug | cards/services/detectors/opencv_detector.py |
| OpenCV stuck救済 | _cleanup_stuck_record | cards/management/commands/process_opencv.py |
| OpenCV CAS | _claim_lock | cards/management/commands/process_opencv.py |
| OCR cron 本体 | Command.handle | cards/management/commands/process_ocr.py |
| 条件付き2回OCR | extract_carddata_via_ocr | cards/tasks/ocr_pipeline.py |
| 名刺DB単位OCR処理本体 | process_cardimage_with_ocr | cards/tasks/ocr_pipeline.py |
| OCR結果判定 | _determine_ocr_result | cards/tasks/ocr_pipeline.py |
| 画像補正回転 | _rotate_card_image | cards/tasks/ocr_pipeline.py |
| 補正画像 同パス上書き | _overwrite_card_image | cards/tasks/ocr_pipeline.py |
| 元画像DB集計遷移 | _update_original_image_status | cards/tasks/ocr_pipeline.py |
| OCR失敗確定 | _finalize_failed | cards/tasks/ocr_pipeline.py |
| OCR差し戻し共通処理 | reset_bc_to_pending | cards/tasks/ocr_recovery.py |
| 元画像DB状態の差し戻し再評価 | recalc_original_image_status_to_cards_extracted | cards/tasks/ocr_recovery.py |
| OCR CAS | _claim_lock | cards/management/commands/process_ocr.py |
| Claude API 連携 | OcrService | cards/tasks/ocr_service.py |
| 生JSON → コンタクト辞書 変換 | normalize_to_contact_dict | cards/services/json_normalizer.py |
| 向きに応じた信頼度補正 | calc_orientation_adjusted_confidence_map | cards/services/json_normalizer.py |
| 最低限情報判定 | has_minimum_info | cards/services/has_minimum_info.py |

---

## 14. 改訂履歴

| 版 | 日付 | 内容 | 担当 |
|---|---|---|---|
| v0.1 | 2026-05-18 | 新規作成。OpenCV／OCR分離後の現状を main 基準で記述 | コード君（Web版） |
