# インストール

FreeGroup2 をエックスサーバーなどのレンタルサーバー、または自前の Linux/Windows サーバーへ導入する手順を説明します。Python と Django の基本的な知識を前提とします。

## 必要な環境

| 項目 | バージョン・条件 |
| --- | --- |
| OS | Linux（推奨）/ Windows 10 以降 / macOS |
| Python | 3.13 以上（3.13.13 で動作確認済み） |
| Django | 6.0.2（`requirements.txt` で固定） |
| データベース | SQLite（既定）/ MySQL 8.0 / PostgreSQL 14 以上 |
| Web サーバー | Apache + mod_wsgi、または Nginx + Gunicorn |
| その他 | Git、pip、virtualenv（または venv） |
| 外部 API | Anthropic Claude API キー（OCR 用に必須） |

OpenCV は `opencv-python-headless` を使うため、GUI ライブラリ（X11 等）は不要です。

## 1. ソースコードを取得する

```bash
cd /home/youraccount/yourdomain/
git clone https://github.com/IwataYoshifumi/freegroup2.git
cd freegroup2
```

エックスサーバーの場合は、`~/yourdomain.com/public_html` の外に配置し、`public_html` には WSGI ファイルだけを置く構成を推奨します。

## 2. Python 仮想環境を作る

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows の場合は `python -m venv .venv` のあと、`.venv\Scripts\activate` を実行します。

## 3. 依存パッケージのインストール

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

インストールされる主なパッケージ：

- `Django==6.0.2`
- `django-crispy-forms` / `crispy-bootstrap5`（フォーム表示）
- `Pillow`（画像処理）
- `opencv-python-headless`（名刺領域検出）
- `anthropic`（Claude API クライアント）
- `python-dotenv`（環境変数読み込み）
- `django-auth-ldap`（LDAP 認証、任意）

## 4. 環境変数（.env）の設定

プロジェクトルートに `.env` ファイルを作成し、`.env.example` を参考に値を埋めます。

```text
DJANGO_SECRET_KEY=ランダムな長い文字列
DEBUG=False
ANTHROPIC_API_KEY=sk-ant-...（Anthropic の API キー）
OCR_BACKEND=claude_sonnet_4_6
CARD_DETECTOR_BACKEND=opencv

AUTH_BACKEND=local
```

- `DJANGO_SECRET_KEY` は本番環境では必ず推測困難なランダム文字列に変更してください。
- `DEBUG` は本番では必ず `False` にしてください（True のままだと内部情報が漏えいする恐れがあります）。
- `ANTHROPIC_API_KEY` は [console.anthropic.com](https://console.anthropic.com/) で取得します。OCR の課金はこの API キーに対して発生します。

LDAP 認証を使う場合は、`AUTH_BACKEND` を `ldap` または `both` にして、`LDAP_SERVER_URI` 以下の項目も設定します。詳細は [ユーザー管理](usage/users.md) を参照してください。

## 5. データベースの初期化

```bash
python manage.py migrate
python manage.py createsuperuser
```

`createsuperuser` で対話的に管理者ユーザーを作成します。ここで作成したユーザーが初回ログイン用の管理者になります。

## 6. 静的ファイルの収集

本番運用では `collectstatic` で静的ファイルを 1 か所に集めます。

```bash
python manage.py collectstatic --noinput
```

## 7. 動作確認

開発用サーバーで起動してみます。

```bash
python manage.py runserver 0.0.0.0:8000
```

ブラウザで `http://サーバーのIP:8000/` にアクセスし、ログイン画面が表示されれば成功です。

## 8. 本番運用の設定

開発用サーバー（`runserver`）は本番では使わないでください。Apache + mod_wsgi、もしくは Nginx + Gunicorn を使います。

エックスサーバーでの参考構成：

```text
~/yourdomain.com/freegroup2/        ← プロジェクト本体
~/yourdomain.com/public_html/       ← .htaccess と WSGI のみ
```

`config/wsgi.py` を WSGI エントリポイントに指定し、`.htaccess` で Python アプリケーションとして公開します。エックスサーバーは Python のバージョンが古い場合があるため、SSH でログインして自分の `.venv` の Python を使う構成が無難です。

## 9. 定期処理（cron）の設定

FreeGroup2 の名刺取り込みは、Web サーバーとは別に **cron で動く 3 本の定期処理** が支えています。**この 3 本を設定しないと、画像をアップロードしても名刺データが一切生成されません。** 必須の設定として、本番運用を始める前に必ず登録してください。

| コマンド | 起動間隔 | 役割 |
| --- | --- | --- |
| `process_opencv` | 5 分 | アップロードされた画像から名刺領域を切り抜き、DB に保存する |
| `process_ocr` | 5 分 | 切り抜き済みの名刺を Claude API で OCR 処理し、Contact / Person を生成する |
| `check_duplicates` | 5 分 | 主コンタクト同士の重複候補を検出し、DuplicateCandidate を生成する |

処理は「アップロード → `process_opencv`（切り抜き）→ `process_ocr`（OCR）→ `check_duplicates`（重複チェック）」の順に、それぞれの cron が pending 状態のレコードを拾って進めていきます。

### crontab の記載例

仮想環境（`.venv`）を使う構成での例です。`crontab -e` で以下を登録します。`/path/to/project` は実際のプロジェクトのパスに置き換えてください。

```text
*/5 * * * * cd /path/to/project && .venv/bin/python manage.py process_opencv
*/5 * * * * cd /path/to/project && .venv/bin/python manage.py process_ocr
*/5 * * * * cd /path/to/project && .venv/bin/python manage.py check_duplicates
```

1 回の起動で処理する件数は各コマンドの `--limit` オプションで調整できます（既定値あり）。取り込み量が多い環境では起動間隔を 1 分に短縮することもできます。

### 信頼性について

これらの cron は、多少のトラブルがあっても自動で復旧するように作られています。

- **多重起動対策が実装済み**のため、前回の処理が終わらないうちに次の cron が起動しても、同じレコードが二重に処理されることはありません。
- **OCR 処理が 30 分以上止まったレコードは自動で救済**され、次回起動時に処理待ちへ差し戻されます。
- **重複チェックは途中で中断された主コンタクト**を、次回起動時に自動で再処理します。

### 障害時のメンテナンス

OCR が失敗したレコードの差し戻しや、DB とメディアファイルの整合性検査には、別途メンテナンス用のコマンド（`retry_failed_ocr` / `reconcile_card_images`）を用意しています。詳細は [メンテナンスコマンド](admin/maintenance.md) を参照してください。

### Windows の場合

Windows サーバーで運用する場合は、cron の代わりに **タスクスケジューラ** で同じ 3 コマンドを 5 分間隔で実行するように登録すれば同等の運用ができます。

## 初期設定（管理画面でやっておくこと）

ログイン後、Django 管理画面（`/admin/`）から以下を設定しておきます。

- **Role（役割）の作成**：`admin`（管理者）、`sales`（営業）、`viewer`（閲覧者）など
- **Group（グループ）の作成**：部署横断で共有するグループ
- **Department（部署）の作成**：所属する部署のツリー構造
- **ユーザーへの Role / Group / Department の割り当て**

これで名刺取り込みを始められる状態になります。次は [名刺の取り込み](usage/import.md) に進んでください。
