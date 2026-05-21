# ユーザー管理

複数人で FreeGroup2 を利用するときの、ユーザー・グループ・権限まわりの設定方法を説明します。基本操作は Django の標準管理画面（`/admin/`）から行います。

## ユーザーの種類

FreeGroup2 のユーザーモデル（`CustomUser`）は、Django 標準のユーザーに加えて、以下の属性を持ちます。

| 属性 | 内容 |
| --- | --- |
| `username` / `password` | ログイン ID とパスワード |
| `role` | 役割（Role）。例：admin / sales / viewer |
| `department` | 所属部署（Department） |
| `groups` | 横断的なグループ（Group） |
| `person` | 対応する人物（Person）の紐付け |
| `auth_source` | 認証元（local / ldap） |

`person` を紐付けると、「このユーザー = 名刺データベース上のこの人」という対応付けができ、自分の名刺を自分で管理したり、「営業担当：山田」のような表示に使えます。

## ユーザーの追加

1. 管理者でログインし、`/admin/` を開く
2. 「ユーザー」→「追加」を選択
3. ユーザー名・パスワードを入力して保存
4. 続いて編集画面で次を設定
    - 氏名・メール
    - **Role**（admin / sales / viewer など）
    - **Department**（所属部署）
    - **Groups**（必要に応じて追加）
    - **Person**（任意。対応する人物があれば紐付け）
    - **権限**：通常は Role 経由で付与されるので個別設定は不要

スタッフ画面にログインさせる場合は「スタッフ権限（is_staff）」を有効にしてください。管理者にする場合は「スーパーユーザー（is_superuser）」も有効にします。

## ユーザーの削除

1. `/admin/` の「ユーザー」一覧で対象を選択
2. 操作メニューから「削除」を実行

ただし、削除するとそのユーザーが作成したコンタクトの「作成者（created_by）」が空になります。実運用では削除よりも「無効化」を推奨します。

### 無効化（退職処理）

- `is_active` を `False` にする：ログイン不可になるが、データは残る
- 必要なら `retire_user` 権限を持つユーザーが「退職処理」を実行する

これにより履歴を保ちつつ、退職者がシステムにログインできない状態にできます。

## 権限・グループ管理

### Role（役割）

Role は「このユーザーは何ができるか」を大まかに決めるラベルです。`code` は変更不可で、プログラム判定に使われます。

| code 例 | 想定する役割 |
| --- | --- |
| `admin` | 全機能を利用可能。ユーザー管理・マージ取り消しなど |
| `sales` | 名刺取り込み・コンタクト編集が可能 |
| `viewer` | 閲覧のみ |

Role に「デフォルトグループ（default_groups）」を紐付けておくと、Role 付与時に自動でそのグループに入れられます。

### Group（グループ）

Django 標準のグループです。FreeGroup2 では「部署横断のチーム」「特定機能の権限セット」などに使います。グループに権限（Permission）を割り当てておけば、メンバー全員に同じ権限が行き渡ります。

例：

- `card_uploader`：名刺アップロード権限を持つグループ
- `merge_managers`：マージ・マージ取り消しを実行できるグループ

### Department（部署）

部署の階層構造を表すモデルです。`parent` で親部署を指定できるので、「営業本部 → 法人営業部 → 第 1 課」のようなツリーを作れます。

部署はユーザー（CustomUser.department）と紐付けて、所属管理や絞り込みに使います。

### 主要な権限（Permission）

FreeGroup2 が独自に定義する権限：

| 権限 | 意味 |
| --- | --- |
| `link_user_to_person` | ユーザーと人物の紐付けを変更できる |
| `retire_user` | ユーザーを退職処理できる |
| `merge_person` | Person のマージを実行できる |
| `undo_merge` | マージの取り消しを実行できる |
| `link_user` | User-Person 紐付け（人物側） |

これらを Group に割り当てて、必要なユーザーに行き渡らせてください。

## パスワードの再発行

ユーザーがパスワードを忘れた場合：

1. 管理者が `/admin/` でそのユーザーを開く
2. 「パスワードを変更する」リンクから新しいパスワードを設定
3. 本人に伝えて、初回ログイン後に各自で変更してもらう

メール経由のパスワードリセット機能は、SMTP 設定を行えば Django 標準機能として有効化できます。

## LDAP 連携（概要）

社内に Active Directory や OpenLDAP が稼働している場合、FreeGroup2 のログインをそれらに統合できます。詳細な設定は管理者向けですが、概要だけ紹介します。

### 認証方式の切り替え

`.env` の `AUTH_BACKEND` で切り替えます。

| 値 | 動作 |
| --- | --- |
| `local` | FreeGroup2 内のユーザー DB だけで認証（既定） |
| `ldap` | LDAP のみで認証 |
| `both` | ローカル → LDAP の順で試す |

### 必要な設定項目

LDAP を使うときは `.env` で以下を設定します。

```text
AUTH_BACKEND=ldap
LDAP_SERVER_URI=ldap://ldap.example.com:389
LDAP_BIND_DN=cn=admin,dc=example,dc=com
LDAP_BIND_PASSWORD=...
LDAP_USER_SEARCH_BASE=ou=People,dc=example,dc=com
LDAP_GROUP_SEARCH_BASE=ou=Groups,dc=example,dc=com
LDAP_USER_USERNAME_ATTR=sAMAccountName
LDAP_CACHE_ENABLED=true
LDAP_CACHE_TTL=3600
```

- `sAMAccountName` は Active Directory 用の属性です。OpenLDAP の場合は `uid` などに変更します。
- LDAP のユーザー・グループは、初回ログイン時に FreeGroup2 側にも自動で同期されます（`ldap_sync.py`）。

### LDAP 同期の注意点

- LDAP 由来のユーザーは `auth_source=ldap` となり、`ldap_dn` で LDAP のエントリと突合します。
- パスワードは LDAP 側で管理され、FreeGroup2 のローカル DB には保存されません。
- LDAP から削除されたユーザーは、同期時に `is_active=False` にされる設定が推奨されます。

LDAP 連携の詳細な設定や、Active Directory 特有の項目については、`django-auth-ldap` の公式ドキュメントもあわせて参照してください。
