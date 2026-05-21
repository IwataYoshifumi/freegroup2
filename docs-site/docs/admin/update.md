# アップデート

FreeGroup2 を新しいバージョンに更新する手順と、更新時に注意すべきポイントをまとめます。本番運用中のシステムをアップデートする想定で書いています。

## アップデートの基本フロー

```text
1. バックアップ
2. 本番停止（メンテナンスモード）
3. ソースコードを取得（git pull）
4. 依存パッケージを更新（pip install）
5. マイグレーション（migrate）
6. 静的ファイル収集（collectstatic）
7. 動作確認
8. 本番再開
```

各ステップを順に説明します。

## 1. 事前準備：バックアップ

アップデート前に必ず DB とメディアファイルをバックアップしてください。万が一マイグレーションに失敗した場合、ここに戻すしかありません。

```bash
sqlite3 db.sqlite3 ".backup '/backup/db_before_update.sqlite3'"
tar czf /backup/media_before_update.tar.gz media/
```

詳細は [バックアップ](backup.md) を参照してください。

## 2. メンテナンスモードへ

ユーザーがアクセスしている最中にアップデートすると、半端な状態のページが表示されたり、書き込み中のレコードが壊れる恐れがあります。アクセスを止める方法はサーバー構成によります。

- Apache + mod_wsgi：`.htaccess` でメンテナンス用 HTML にリダイレクト
- Nginx + Gunicorn：Gunicorn を停止して 502 ページを返すか、別ポートに切り替え
- エックスサーバー：`.htaccess` で `Deny from all` または案内ページに転送

WSGI / Gunicorn は止めても、サーバー本体は動かしたままで構いません。

## 3. ソースコードの取得

```bash
cd /path/to/freegroup2
git fetch --all
git checkout main          # 本番で使うブランチへ
git pull origin main
```

特定のタグ（リリース版）にしたい場合：

```bash
git fetch --tags
git checkout v1.4.2
```

ローカルで設定ファイルなどを直接編集している場合、`git pull` でコンフリクトすることがあります。差分を確認して必要な変更だけ取り込んでください。

## 4. 依存パッケージの更新

仮想環境を有効化したうえで `requirements.txt` を再インストールします。

```bash
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

新しい依存が追加されている、あるいはバージョンが上がっている場合のみ実際に変更が入ります。

OS パッケージのアップデート（例：libpq、libjpeg、libssl 等）が必要なバージョンアップもあり得ます。リリースノートに OS 側の追加要件が記載されている場合は先に対応してください。

## 5. マイグレーションの実行

データベーススキーマの変更を反映します。

```bash
python manage.py migrate
```

正常終了すれば次のような出力になります。

```text
Operations to perform:
  Apply all migrations: accounts, cards, contacts, persons, ...
Running migrations:
  Applying cards.0042_xxx... OK
  Applying contacts.0031_yyy... OK
```

エラーになった場合は途中で止まる可能性があります。**焦って手動で DB を触らず**、まずバックアップから戻して、ログを管理者へ共有してください。

### マイグレーションの確認だけしたい場合

実際に DB を書き換える前に、何が起きるか SQL を見たいときは：

```bash
python manage.py showmigrations
python manage.py sqlmigrate <app名> <マイグレーション番号>
```

### データマイグレーションを含む場合

スキーマだけでなくデータも書き換える「データマイグレーション」が含まれていると、件数によっては数分〜数十分かかることがあります。リリースノートに記載があれば、所要時間を見積もってからメンテナンス枠を確保してください。

## 6. 静的ファイルの収集

CSS / JS / 画像などが更新されている可能性があるので、`collectstatic` を実行します。

```bash
python manage.py collectstatic --noinput
```

`STATIC_ROOT` 配下に最新の静的ファイルがコピーされます。CDN を使っている場合は、ここでキャッシュをパージしてください。

## 7. 動作確認

```bash
python manage.py check
python manage.py check --deploy   # 本番向けチェック
python manage.py runserver 0.0.0.0:8000
```

ブラウザで以下を確認します。

- トップページが表示される
- ログインできる
- コンタクト一覧が表示される
- 既存の名刺画像が見える
- 新しい名刺をアップロードして OCR が走る

問題なければ、メンテナンスモードを解除して本番運用に戻します。

## 8. アップデートでよく出る注意事項

### 環境変数の追加・変更

新バージョンで `.env` に新しい項目が追加されることがあります。`.env.example` を必ず差分確認し、必要な項目を追加してください。例：

```text
# v1.4.2 で追加されたもの（例）
CARD_DETECTOR_BACKEND=opencv
```

### `requirements.txt` のバージョン固定

Django や anthropic SDK のバージョンが上がる場合、API の挙動が変わることがあります。リリースノートで「破壊的変更」と書かれた箇所は事前に読んでおいてください。

### 古いマイグレーションの整理

開発ブランチでマイグレーションファイルをまとめる（squashmigrations）ことがあります。本番側で適用済みのマイグレーション番号と差分が出ると `migrate` が失敗するので、リリースノートの指示に従ってください。

### 名刺画像のファイル形式変更

メディアファイルの保存先・ファイル名規約が変更されると、既存画像の参照が壊れることがあります。リリースノートに「メディアファイル移行スクリプト」が用意されていれば必ず実行してください。

### LDAP 同期スキーマの変更

LDAP 連携を使っている場合、属性マッピングが変わることがあります。アップデート後の初回ログインで再同期が走るので、検証環境で先に確認することを強く推奨します。

## ロールバック手順

アップデートに問題が出た場合は、以下の手順で前のバージョンへ戻します。

```bash
# 1. 本番停止
# 2. ソースを前のバージョンへ
git checkout v1.4.1                    # 前のタグ・コミット
# 3. 仮想環境を巻き戻す
pip install -r requirements.txt
# 4. DB を復元
sqlite3 db.sqlite3 ".restore '/backup/db_before_update.sqlite3'"
# あるいは
mysql -u user -p freegroup2 < /backup/before_update.sql
# 5. メディアを復元（必要なら）
tar xzf /backup/media_before_update.tar.gz
# 6. 動作確認のうえ本番再開
```

DB だけ新バージョンのスキーマに進めて、ソースだけ古いものに戻す、というのは**絶対にやらないでください**。ソースと DB のバージョン整合性は常に守る必要があります。

## アップデート前後のチェックリスト

- [ ] DB バックアップを取得した
- [ ] メディアファイルのバックアップを取得した
- [ ] `.env` をバックアップした
- [ ] メンテナンスモードに切り替えた
- [ ] `git pull` で新バージョンを取得した
- [ ] `pip install -r requirements.txt` を実行した
- [ ] `.env.example` の差分を確認・反映した
- [ ] `python manage.py migrate` が正常終了した
- [ ] `python manage.py collectstatic` を実行した
- [ ] ログインと基本操作の動作確認をした
- [ ] メンテナンスモードを解除した
- [ ] エラーログを 24 時間程度監視した
