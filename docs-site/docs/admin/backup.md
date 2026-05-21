# バックアップ

FreeGroup2 のデータを安全に保つためのバックアップと復元の手順です。管理者向けに、サーバー上のコマンドラインで行う想定で書いています。

## バックアップ対象

FreeGroup2 のデータは大きく 3 種類です。これらをすべて含めてバックアップしてください。

| 対象 | 内容 | 場所（既定） |
| --- | --- | --- |
| データベース | コンタクト・人物・ユーザー・ログなどの全レコード | `db.sqlite3` または MySQL/PostgreSQL のデータ |
| メディアファイル | 名刺画像（元画像 / 切り出し画像 / マスク等） | `media/` 配下 |
| 設定ファイル | `.env` と `config/settings.py` のローカル変更 | プロジェクトルート |

ソースコード自体は GitHub で管理されているため、バックアップ対象に含めなくても再取得可能です。ただし `.env` には API キーや SECRET_KEY など機密情報が入るので、必ず別途バックアップを取ってください。

## バックアップの頻度の目安

- **毎日**：データベース（自動化推奨）
- **毎週**：メディアファイル（差分でも可）
- **設定変更時**：`.env` と `settings.py` のローカル差分

バックアップ先は、本番サーバーとは別のストレージ（外部ディスク・クラウドストレージなど）に置くことを強く推奨します。

## データベースのバックアップ

### SQLite の場合

`db.sqlite3` ファイルを丸ごとコピーするだけです。書き込み中のコピーを避けたいので、`runserver` や WSGI を一時停止するか、SQLite の `.backup` コマンドを使います。

```bash
# シンプルな方法（書き込みが少ない時間帯に）
cp db.sqlite3 /backup/db_$(date +%Y%m%d).sqlite3

# 安全な方法（SQLite のオンラインバックアップ）
sqlite3 db.sqlite3 ".backup '/backup/db_$(date +%Y%m%d).sqlite3'"
```

### MySQL の場合

```bash
mysqldump -u DBユーザー -p --single-transaction --quick \
    --default-character-set=utf8mb4 \
    freegroup2 > /backup/freegroup2_$(date +%Y%m%d).sql
```

- `--single-transaction` をつけると、書き込みを止めずに整合性のあるダンプが取れます。
- 圧縮するなら `| gzip > ...sql.gz` をパイプで追加してください。

### PostgreSQL の場合

```bash
pg_dump -U DBユーザー -F c -f /backup/freegroup2_$(date +%Y%m%d).dump freegroup2
```

`-F c` はカスタム形式(圧縮済み)。復元時は `pg_restore` を使います。

### Django の dumpdata（フォーマット非依存）

DB 種別が変わってもデータを移行できる JSON 形式のバックアップです。サイズは大きくなりますが、可搬性が高い方法です。

```bash
python manage.py dumpdata \
    --natural-foreign --natural-primary \
    --exclude=contenttypes --exclude=auth.Permission \
    --indent=2 > /backup/freegroup2_$(date +%Y%m%d).json
```

## メディアファイルのバックアップ

`media/` ディレクトリ配下に、アップロードされた名刺画像と OCR で生成された切り出し画像・マスクが入っています。

### 単純コピー

```bash
tar czf /backup/media_$(date +%Y%m%d).tar.gz media/
```

### rsync（差分バックアップ）

```bash
rsync -avh --delete media/ /backup/media-mirror/
```

`--delete` はミラー先で消されたファイルを反映するオプションです。世代管理が必要なら、別途スナップショット機能のあるストレージや `rsnapshot` の利用を検討してください。

## 自動化（cron 例）

毎日午前 3 時に DB をバックアップし、7 世代だけ残す例：

```bash
0 3 * * * cd /path/to/freegroup2 && \
    sqlite3 db.sqlite3 ".backup '/backup/db_$(date +\%Y\%m\%d).sqlite3'" && \
    find /backup -name "db_*.sqlite3" -mtime +7 -delete
```

Windows サーバーの場合は「タスクスケジューラ」で同等の処理をスケジュールします。

## 復元手順

### 1. 復元前の準備

- 復元先のサーバーに FreeGroup2 がインストールされていること（[インストール](../install.md) 参照）
- バージョンを揃える（バックアップ時点と同じバージョンを git で checkout）
- `runserver`・WSGI を停止しておく

### 2. データベースの復元

**SQLite：**

```bash
cp /backup/db_20260520.sqlite3 db.sqlite3
```

**MySQL：**

```bash
mysql -u DBユーザー -p freegroup2 < /backup/freegroup2_20260520.sql
```

**PostgreSQL：**

```bash
pg_restore -U DBユーザー -d freegroup2 /backup/freegroup2_20260520.dump
```

**Django dumpdata からの復元（loaddata）：**

```bash
python manage.py migrate                # まずスキーマを作る
python manage.py loaddata /backup/freegroup2_20260520.json
```

### 3. メディアファイルの復元

```bash
tar xzf /backup/media_20260520.tar.gz -C /path/to/freegroup2/
```

### 4. 動作確認

```bash
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

ブラウザでログインして、コンタクト一覧と名刺画像が見えれば成功です。

## バックアップで陥りやすい注意点

- **DB だけ復元してメディアを忘れる**：画像が表示されない状態になります。必ず両方セットで管理してください。
- **`.env` のバックアップ忘れ**：`SECRET_KEY` が変わると既存のセッションが無効化されます。同じキーを使い続けるなら `.env` ごと保管してください。
- **OCR の途中で取ったバックアップ**：`pending` / `processing` のレコードが中途半端な状態で復元される可能性があります。バックアップは深夜帯など処理が止まっている時間帯を推奨します。
- **マイグレーション差分を考えない復元**：バックアップ時と現在で DB スキーマが違うと、復元しても起動しません。バージョンを必ず合わせてください（[アップデート](update.md) 参照）。
