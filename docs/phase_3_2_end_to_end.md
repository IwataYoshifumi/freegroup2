# Phase 3-2 エンドツーエンド動作確認手順

仕様書 v1.2.2 に基づく Phase 3-2（tasks 層 / OCR 連携）の動作確認手順。
画像アップロード → process_pending → BusinessCard / Contact 作成までを通しで確認する。

## 1. 前提環境

### 1.1 必要パッケージ
```bash
pip install -r requirements.txt
```
主要依存：Django 6.0.2 / Pillow / python-dotenv / anthropic / jsonschema。

### 1.2 環境変数
プロジェクトルートに `.env` を作成（雛形は `.env.example`）：
```
DJANGO_SECRET_KEY=<本番では強力なキー>
DEBUG=True
ANTHROPIC_API_KEY=<your_anthropic_api_key>
OCR_BACKEND=claude_haiku_4_5
```
`ANTHROPIC_API_KEY` 未設定だと `OcrApiError` で status=failed になる。

### 1.3 DB マイグレーション
```bash
python manage.py migrate
```

### 1.4 テスト用ユーザー作成
```bash
python manage.py createsuperuser
```
`UploadView.get_current_user` ヘルパーが最初のスーパーユーザーを参照するため、
v1.2.x（認証未実装）でもこれが必要。

## 2. 動作確認の流れ

### 2.1 サーバー起動
```bash
python manage.py runserver
```

### 2.2 画像をアップロード
ブラウザで `http://127.0.0.1:8000/cards/upload/` にアクセス。
名刺画像（JPEG または PNG、5MB 以下）を選択 → アップロード。

### 2.3 アップロード直後の状態
- 画面遷移：`/originals/<id>/` にリダイレクト
- `OriginalImage.status = pending`
- `OriginalImage.raw_json = null`
- `BusinessCard` / `Contact` レコードは未作成

### 2.4 OCR 処理を実行
別ターミナルで管理コマンドを実行：
```bash
python manage.py process_pending
```

期待される出力例：
```
process_pending: 1 件を試行
  done <UUID>: status=extracted
process_pending: targets=1, processed=1, skipped=0
```

cron 運用時は同コマンドが 1〜5 分ごとに自動起動される（後述 §4）。

### 2.5 処理後の状態（成功時）
| 項目 | 期待値 |
| --- | --- |
| `OriginalImage.status` | `extracted` |
| `OriginalImage.raw_json` | Claude API 応答（schema_version / ocr_meta / cards）が JSON で格納 |
| `OriginalImage.detected_count` | `len(raw_json["cards"])` |
| `OriginalImage.error_message` | 切り抜き失敗があれば `card_index=N: 切り抜き失敗 (...)` 形式 |
| `BusinessCard` レコード | `card_index` 付きで cards 配列の数だけ（has_minimum_info 通過分のみ） |
| `BusinessCard.card_image` | 切り抜き成功時はパス、失敗時は null |
| `Person` / `Contact` | BusinessCard と同数（OneToOne / FK） |
| `ContactFieldConfidence` | confidence が low / medium のフィールドのみレコード作成 |

### 2.6 結果の確認
1. ブラウザで `/originals/<id>/` を再表示 → status バッジが「完了」
2. `/admin/cards/` で各テーブルを確認可能
3. Django shell で確認：
   ```python
   python manage.py shell
   >>> from cards.models import OriginalImage, BusinessCard, Contact, ContactFieldConfidence
   >>> o = OriginalImage.objects.last()
   >>> o.status, o.detected_count
   ('extracted', 1)
   >>> o.raw_json["cards"][0]["fields"]["full_name"]
   {'value': '山田太郎', 'confidence': 'high'}
   >>> bc = BusinessCard.objects.filter(original_image=o).first()
   >>> bc.contact.full_name, bc.contact.company
   ('山田太郎', '株式会社サンプル')
   >>> [(c.field_name, c.confidence) for c in bc.contact.confidences.all()]
   [('title', 'medium'), ('twitter', 'low')]
   ```

## 3. 失敗系の確認

### 3.1 OCR API 失敗（API キー不正・ネットワーク不可など）
- `process_pending` 実行 → `status = failed`
- `error_message` に "OCR API 失敗: ..." が入る
- `BusinessCard` / `Contact` は作成されない

### 3.2 スキーマ検証失敗
- Claude が schema 違反のレスポンスを返した場合、`status = failed`
- `error_message` に "スキーマ検証失敗: ..." が入る

### 3.3 garbage 判定の主因
| 条件 | 結果 |
| --- | --- |
| Claude が cards=[] を返した | garbage |
| 全 card で `is_business_card=false` | garbage |
| 全 card が `has_minimum_info` で弾かれた（full_name 不在、または連絡先 0 件） | garbage |

### 3.4 切り抜き失敗（部分失敗扱い、status=extracted のまま）
- `BusinessCard.card_image = null`
- `OriginalImage.error_message` に "card_index=N: 切り抜き失敗 (理由)" が追記
- 主因：bbox が画像範囲外、切り抜き後 width<100 または height<50

### 3.5 失敗の手動再投入（運用ツール）
```bash
# failed 全件（BusinessCard 0件のもの）を pending に戻す
python manage.py retry_failed_ocr --all

# 特定の 1 件だけ
python manage.py retry_failed_ocr --id <UUID>

# 件数制限つき
python manage.py retry_failed_ocr --all --limit 5

# 動作確認のみ（実際の更新なし）
python manage.py retry_failed_ocr --all --dry-run
```
実行後、対象 OriginalImage は `status=pending` に戻り、`raw_json` / `error_message` /
`detected_count` がクリアされる。次の `process_pending` 起動で再処理される。

`retry_failed_ocr` は **開発・運用ツール**（仕様書 v1.2.2 §12.1）。
エンドユーザー UI には提供しない。

## 4. cron 設定（参考）

本番では cron で `process_pending` を自動起動：
```
* * * * * cd /path/to/freegroup2 && /path/to/python manage.py process_pending
```

- SQLite 開発時は 1〜5 分間隔で十分
- 1 回の起動で最大 N=10 件処理（`--limit` で変更可）
- 楽観的ロック方式で多重起動対策（仕様書 §8.5.4）
- 本番 DB 移行時は `select_for_update(skip_locked=True)` を検討

## 5. トラブルシューティング

### Q. process_pending で「pending なし」と出る
- `/cards/upload/` でアップロード済みか確認
- DB で確認：
  ```python
  OriginalImage.objects.filter(status='pending').count()
  ```

### Q. status=failed になる主因
- `ANTHROPIC_API_KEY` が未設定または無効
- Claude API 到達不能（ネットワーク不可）
- スキーマ検証失敗
- DB 書き込み失敗
- `OriginalImage.error_message` に詳細

### Q. 切り抜き画像が表示されない
- `OriginalImage.error_message` を確認（`card_index=N: 切り抜き失敗`）
- 仕様書 v1.2.1 §10.3：切り抜き失敗でも Contact データが取れていれば
  BusinessCard は作成される（`card_image=null`）

### Q. ContactFieldConfidence にレコードが作られない
- `confidence=high` のフィールドはレコードを作らない仕様（仕様書 v1.2.2 §4.6）
- 「レコードがない = high と解釈」する設計
- `confidence=low` または `medium` のフィールドのみ ContactFieldConfidence に保存

### Q. 同じ画像をアップロードすると Person / Contact が増える
- v1.2.x では同一人物判定・統合機能は未実装（仕様書 §4.1.1）
- 1 枚アップロード = 1 Person + 1 Contact 新規作成
- 統合機能は v2.0.0 で実装予定

## 6. 後片付け（テスト後）

テスト用 OriginalImage と関連レコードを削除：
```python
python manage.py shell
>>> from cards.models import OriginalImage
>>> OriginalImage.objects.all().delete()
```
`media/originals/` および `media/cards/` 配下のファイルは手動で削除可。

## 7. 参照

- 仕様書本体：`docs/名刺画像取り込みOCR仕様書_v1_2_2.docx`
- JSON Schema：`docs/json_schema/v1.0.0/standard_response.json`
- 過去版：`docs/名刺画像取り込みOCR仕様書_v1_1_0.docx`、`docs/名刺画像取り込みOCR仕様書_v1_2_1.docx`
- プロジェクト方針・命名規則：`CLAUDE.md`
