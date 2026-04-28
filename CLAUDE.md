# FreeGroup2 — Claude 向けガイド

## プロジェクト概要

FreeGroup2 は名刺管理システム。ユーザーがアップロードした名刺画像から
Claude API（Tool Use）で名刺領域を検出し、OCR・データ正規化・DB 保存
までを行う。

- 旧版（v1.0.0 以前）は設計が悪かったため一度全削除し、その後
  v1.0.1 → v1.1.0 → v1.2.0 → v1.2.1 → v1.2.2 と段階的に進化
- 現在の正本は **v1.2.2**（Phase 3-2 着手前の確定版）
- バックエンドは Django、OCR は Claude Haiku 4.5 が既定

## 必ず参照する仕様書

- `docs/名刺画像取り込みOCR仕様書_v1_2_2.docx`（**最優先・正本**。実装方針はここに従う）
- `docs/名刺画像取り込みOCR仕様書_v1_1_0.docx`（参考。Contact 構造の改訂経緯を確認したい場合）
- `docs/BackNavigator使い方ガイド.docx`（戻るボタン実装ガイド）
- `docs/er_diagram_v2_with_person_contact.html`（ER 図、補助情報）

矛盾が出たら勝手に判断せず、ユーザーに必ず確認すること。
古い仕様書（v1.0.1 / v1.2.0 / v1.2.1）への直接参照は禁止。
v1.2.2 と過去バージョンに矛盾がある場合は **常に v1.2.2 を採用**。

## v1.2.0 主要方針（順守必須）

1. **Claude API 生レスポンスは OriginalImage.raw_json に集約保存**
   - cards 配列を含む API 応答全体を OriginalImage.raw_json に保存
   - BusinessCard は **raw_json / ocr_status / error_message を持たない**
   - BusinessCard.card_index で `OriginalImage.raw_json["cards"]` の該当要素を参照する

2. **UniqueConstraint: (original_image, card_index)**
   - 同じ元画像内で card_index は一意

3. **API 1 回呼び出しで全名刺一括取得（パターン A）**
   - 1 元画像 = Claude API 1 リクエスト = 複数名刺一括 OCR
   - 名刺ごとに API を叩き直す設計（パターン B）は採用しない

## v1.2.1 主要方針（順守必須）

1. **BusinessCard.card_image は null 許容**
   - 切り抜き失敗時は画像なしで BusinessCard を作成
   - 切り抜き失敗理由は `OriginalImage.error_message` に
     `"card_index=N: 切り抜き失敗 (理由)"` 形式で追記

2. **BusinessCard 作成可否は `has_minimum_info` で判定**
   - `full_name` が必須
   - かつ `company` / `email` / `phone` / `mobile` のいずれか1つ以上が必要
   - これを満たさない card は BusinessCard を作成しない

3. **json_normalizer は防御的実装**
   - 例外を raise するのは `card_index` 範囲外のみ（ValueError）
   - 構造想定外、`value=null`、サポート外 SNS type は `confidence=low` / 無視 で処理続行
   - エラーで止めず最大限の情報を取り出す方針

4. **OcrBackend は標準 JSON 形式を返す責務**
   - `schema_version` / `ocr_meta` / `cards` のトップレベル構造
   - JSON Schema は `docs/json_schema/v1.0.0/standard_response.json` で Git 管理

5. **不変性ルール**
   - `card_index` は不変
   - `raw_json` は不変
   - 差し替え禁止（変更したい場合は新規 OriginalImage を作る）

## v1.2.2 主要方針（順守必須）

1. **OCR 起動はユーザーアクション「画像アップロード」のみ**
   - 失敗した OriginalImage の retry 機能はユーザー UI に提供しない
   - ユーザー側のリカバリは「再アップロード」を促す
   - `retry_failed_ocr` は **開発・運用ツール**（管理コマンド）として位置付け、
     一般ユーザー向け機能ではない

2. **process_pending は楽観的ロック方式で多重起動対策**
   - 1 回の `process_pending` で **最大 N=10 件** 処理
   - cron で多重起動されても重複処理が起きないよう楽観ロックでガード

3. **PipelineCoordinator のトランザクション境界は card 単位**
   - 1 card の失敗が他 card の保存をロールバックしない
   - card ごとに独立した `transaction.atomic` ブロックを張る

4. **detected_count vs created_count の分離**
   - `detected_count = len(cards)` （Claude が返した cards 配列の長さ）
   - `created_count` は DB に保存された BusinessCard 数（**ローカル変数**、DB 列なし）
   - 「検出されたが has_minimum_info を満たさず作成されなかった」も把握できるようにする

5. **CardCropper の最低画像サイズ基準**
   - `width < 100` または `height < 50` は **失敗扱い**

6. **`has_minimum_info` は strip 処理を行う**
   - `None` / `"   "` / `"\n"` などは空文字扱いで判定する

7. **ContactFieldConfidence.field_name は Contact 側のフィールド名**
   - 例: `"email"`, `"phone"`（単独形）
   - 例: `"emails"` のような raw_json 側の配列名は**使わない**

8. **保存前に jsonschema 再検証**
   - PipelineCoordinator は OCR 応答を保存する直前に JSON Schema で再検証する

## v1.0.1 から継続している共通設計方針

1. **バックグラウンド処理は cron + 管理コマンド方式**
   - `threading.Thread` は使わない（v1.0.0 の設計失敗を反映）
   - 1〜5 分間隔で `python manage.py process_pending` を cron 起動する
   - 失敗の手動再投入は `python manage.py retry_failed_ocr`（運用ツール扱い）

2. **View からスレッドを起動しない**
   - View は HTTP リクエスト/レスポンス処理とテンプレート選択のみ
   - アップロード受付時は `OriginalImage` を `status=pending` で保存して即レスポンス
   - 重い処理（OCR、画像切り抜き、JSON 正規化）はすべて `tasks/` 配下＋管理コマンド経由

3. **Person 一覧表示は関連 Contact から取得**
   - `Person.display_name` フィールドは設けない（v2.0.0 で追加予定）
   - 代表表示が必要なら最新 Contact を引く：
     `person.contact_set.order_by('-created_at').first()`
   - そこから `full_name` / `company` を表示する

4. **重複チェックは Contact DB ベース**
   - raw_json に対しては行わない
   - Contact DB（保存済みレコード）に対して行う
   - 理由：OCR バックエンド非依存・既存データとの比較容易性

## UI 実装方針

- **CSS / JS は `static/css/app.css` / `static/js/app.js` の既存クラス・関数のみ使用**
- 新規 CSS クラス・JS 関数を勝手に作らない（必要になったらユーザー確認）
- 既存の UI コンポーネントを使い、新しい UI を作らない方針
- **命名規則（BEM 風）**: `app-* / __ / -- / is-* / js-*`
  - 例: `app-card`, `app-card__header`, `app-card--compact`,
    `is-active`, `js-toggle-menu`
- **ボタン**: `app-btn--primary` / `app-btn--secondary` / `app-btn--danger`
  / `app-btn--sm` / `app-btn--icon`
- **フォーム**: `app-form-grid` / `app-input` / `app-form__group`
- **テーブル**: `app-table` / `app-table--nowrap`

## BackNavigator

戻るボタンは `back_navigator` アプリで提供。詳細は
`docs/BackNavigator使い方ガイド.docx` を参照。

- `push_current` は **1 リクエストにつき 1 回だけ呼ぶ**
- view_name + view_kwargs が同じならスタックに積まない（重複防止）
- テンプレートタグ: `{% load back_tags %}` で
  `back_url` / `back_all_url` / `append_back_url` / `hidden_back_field` が使える

## 環境変数

`.env`（Git 管理外）で以下を定義。雛形は `.env.example`。

| 変数名 | 用途 | 既定 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django SECRET_KEY | （必須） |
| `DEBUG` | デバッグモード | `True` |
| `ANTHROPIC_API_KEY` | Claude API キー | （必須） |
| `OCR_BACKEND` | OCR バックエンド | `claude_haiku_4_5` |

OS 環境変数を `.env` より優先（settings.py で `load_dotenv(..., override=False)`）。

## Git 同期ルール

- **作業開始前**: `git pull origin main`
- **作業終了・席を立つ前**: `git add . && git commit && git push origin main`
- 未完成でも WIP コミットして push する（手元にしかない状態を残さない）

## 実装フェーズ進捗と今後の予定

### 完了済み
- **Phase 1**: Django 初期構築（settings / cards app / back_navigator / 環境ファイル）
- **Phase 2**: models（OriginalImage / BusinessCard / Contact / Person） ※後に v1.1.0 で改訂
- **Phase 3-1**: services 層初期実装（image_processor / json_normalizer）
- **Phase 4-UI（先行）**: home / upload / originals 画面（views / templates / namespace routing）

### Phase 3-2（次に着手）
v1.2.2 で確定した実装スコープは以下 7 ファイル：

1. `cards/tasks/pipeline_coordinator.py`（クラス実装）
2. `cards/tasks/ocr_service.py`（クラス実装）
3. `cards/tasks/card_cropper.py`（関数実装、主関数 `create_card_image()`）
4. `cards/management/commands/process_pending.py`
5. `cards/management/commands/retry_failed_ocr.py`
6. `cards/services/json_normalizer.py`（v1.2.2 仕様に合わせて改訂）
7. `cards/services/has_minimum_info.py`（単独ファイル、新規）

### 以降の予定
- Phase 4 残り: 名刺一覧画面 / 名刺詳細画面 / 認証 / 権限
- Phase 5 以降: v2.0.0 スコープ（同一人物統合・Celery 化など）

## 関数命名規則と性質明記

### 関数の3分類

- 純関数：DB を一切触らない、副作用なし、同じ入力で同じ出力
- 準関数：DB を読むが書かない、外部世界に副作用なし
- 副作用あり関数：DB 書き込み・例外送出・API 呼び出し・ファイル書き込みなど

### プレフィックス（推奨）

| プレフィックス | 性質 | 例 |
|---|---|---|
| normalize_* / to_* / calc_* / is_* / has_* | 純関数 | normalize_to_contact_dict, has_minimum_info |
| find_* / get_* / search_* | 準関数（DB読み取り） | find_matching_person |
| validate_* | 副作用あり（例外） | validate_image |
| convert_* / save_* / create_* / update_* / delete_* | 副作用あり（変換・DB書込） | convert_to_jpeg, create_card_image |
| run_* / process_* / send_* | 副作用あり（複合処理） | run_ocr, process_pending |
| retry_* | 副作用あり（複合処理、再投入） ★v1.2.2 で追加 | retry_failed_ocr |

### docstring 性質明記の強制範囲

| 配置 | 強制度 | 内容 |
|---|---|---|
| services/ の公開関数 | 必須 | レベル2（性質 + 入出力） |
| tasks/ の公開関数 | 必須 | レベル2 |
| management/commands/ で外から呼ばれる関数 | 必須 | レベル2 |
| 内部ヘルパー（_ で始まる） | 任意 | レベル1（性質1行）で十分 |
| View / Model / Django 標準メソッド | 不要 | - |

### docstring の書き方

レベル1（最小）：性質1行のみ
```
"""
[性質] 副作用あり（ValidationError を raise）
"""
```

レベル2（標準・必須範囲で書く形）：
```
"""
raw_json を Contact フィールド辞書に変換する。

[性質] 純関数（DB操作なし・副作用なし）
[入力] raw_json: dict（Claude API の Tool Use 結果）
[出力] dict（Contact のフィールド辞書）
"""
```

### 迷ったときのルール

- 迷ったら必須側に倒す
- 他のファイルから import するなら必須
- 1関数が複数のプレフィックスに当てはまるなら、責務を分けて関数を分割する
