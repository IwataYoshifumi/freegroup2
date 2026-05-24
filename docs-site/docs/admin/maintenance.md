# メンテナンスコマンド

通常の名刺取り込みは [インストール](../install.md) で設定する 3 本の cron（`process_opencv` / `process_ocr` / `check_duplicates`）が自動で進めます。ここで説明するのは、それとは別に **管理者が必要なときに手動で実行する運用ツール** です。日常的に動かす必要はありません。

## retry_failed_ocr — 失敗レコードの差し戻し

OCR 処理が失敗したレコードを「処理待ち」状態に差し戻すコマンドです。差し戻したレコードは、次回の cron 起動時にもう一度処理されます。失敗の原因（一時的な API エラー、画像の問題など）を確認したうえで実行してください。

失敗は段階によって分かれており、どちらを差し戻すかをオプションで指定します。

- `--opencv`：OpenCV 段階（名刺の切り抜き）で失敗したレコードを差し戻す
- `--ocr`：OCR 段階で失敗したレコードを差し戻す
- `--dry-run`：実際には変更せず、差し戻し対象の件数だけを確認する

このコマンドは管理者が失敗レコードを確認したうえで手動実行するものです。**cron による自動化はしません**（原因を確かめずに差し戻しを繰り返すと、同じ失敗を無限に繰り返す恐れがあるためです）。

### 使用例

```bash
# OCR 段階の失敗を確認（dry-run。件数だけ表示し、変更はしない）
python manage.py retry_failed_ocr --ocr --dry-run

# OpenCV 段階の失敗を差し戻す
python manage.py retry_failed_ocr --opencv

# OCR 段階の失敗を差し戻す
python manage.py retry_failed_ocr --ocr
```

まず `--dry-run` で対象件数を確認し、想定どおりであれば本実行する流れを推奨します。

## reconcile_card_images — 画像ファイルの整合性検査

DB に記録された名刺画像のレコードと、実際に保存されているファイル（`MEDIA_ROOT` 配下）との間にズレがないかを検査・修復するコマンドです。ファイルの手動削除やバックアップ復元の際に、両者の整合性が崩れることがあります。

- デフォルトは **dry-run**（検出のみ。実際のファイルには手を加えません）
- `--apply`：検出したズレを実際に修復する

### 使用タイミング

日次で dry-run を cron に登録しておき、出力を確認する運用を推奨します。ズレが検出された場合に、内容を確認したうえで `--apply` を手動実行します。

```bash
# 毎日深夜 2 時に dry-run で検査（結果はサーバーログに記録される）
0 2 * * * cd /path/to/project && .venv/bin/python manage.py reconcile_card_images >> /var/log/freegroup2/reconcile.log 2>&1

# ズレが見つかった場合の修復（内容を確認のうえ手動実行）
python manage.py reconcile_card_images --apply
```

`/path/to/project` は実際のプロジェクトのパスに置き換えてください。`--apply` はファイルを移動する操作を伴うため、事前に [バックアップ](backup.md) を取っておくと安心です。
