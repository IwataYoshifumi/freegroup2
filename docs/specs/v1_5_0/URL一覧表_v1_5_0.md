# FreeGroup2 URL 一覧表（v1.4.2 + v1.5.0 統合版）

**最終更新**：2026-05-15
**正本**：
- `_最終の最終版_名刺画像取り込みOCR仕様書_v1_4_2統合最終版.md` §11.3（v1.4.2 既存 URL）
- `_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_4.md` §12.6 / §12.7 / §13（v1.5.0 認証・認可・User管理 URL）

**位置づけ**：本書は v1.4.2 既存 URL に v1.5.0 で追加・変更される URL（ログイン・パスワード管理・User-Person 紐付け・退職処理）と、既存 URL への認証・認可付与方針を統合した一覧表。「ルート / View 名 / URL name / ログイン要否 / Permission / ロール / 備考」の 9 列リスト。

---

## 凡例

- **★1**：仕様書に明示されている URL／Permission／ロール
- **★2**：仕様書に明示されていない、サポート担当クロード君のレコメンド。実装前にたんたん確認必要
- **ログイン**：
  - 「必須」＝`LoginRequiredMixin` または `@login_required` が必要
  - 「不要」＝匿名アクセス可（ログイン画面・パスワードリセット申請画面等）
- **Permission**：Django Permission の codename（`<app>.<codename>` 形式）。複数の場合は OR/AND を明示
- **ロール**（v1.5.0 で確定）：
  - `admin`＝管理者
  - `sales`＝営業
  - `viewer`＝閲覧者
  - 「本人」＝紐付け先 User と request.user が一致するケース
  - 「全員」＝ログイン済み User なら誰でも

---

## URL 一覧

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 1 | `/` | GET | HomeView | `home` | 必須 ★2 | なし | 全員 | v1.5.0 でアラート機能拡張（§12.4）。`LoginRequiredMixin` 追加（仕様書 §12.4 View 実装に明示）★1 |
| 2 | `/cards/upload/` | GET / POST | OriginalImageUploadView | `cards:card_upload` | 必須 ★2 | `cards.create_card` ★2 | admin / sales ★2 | v1.4.2 既存。Permission は §7.4 で `cards.create_card` が言及されているが §13.8 の初期データ Migration に未登場。**論点**：cards.* Permission の初期データ Migration 整備が必要 |
| 3 | `/cards/` | GET | CardListView | `cards:card_list` | 必須 ★2 | `cards.view_card`（Django標準）★2 | 全員 ★2 | v1.4.2 既存。閲覧系は viewer ロール以上で可 |
| 4 | `/cards/<uuid:pk>/` | GET | CardDetailView | `cards:card_detail` | 必須 ★2 | `cards.view_card`（Django標準）★2 | 全員 ★2 | v1.4.2 既存。業務画面化済（Contact 編集 UI 併設） |
| 5 | `/originals/` | GET | OriginalListView | `originals:original_list` | 必須 ★2 | `cards.view_card`（Django標準）★2 | 全員 ★2 | v1.4.2 既存 |
| 6 | `/originals/<uuid:pk>/` | GET | OriginalDetailView | `originals:original_detail` | 必須 ★2 | `cards.view_card`（Django標準）★2 | 全員 ★2 | v1.4.2 既存 |
| 7 | `/persons/` | GET | PersonListView | `persons:person_list` | 必須 ★2 | `persons.view_person`（Django標準）★1 | 全員 ★1 | v1.4.2 既存。§13.8 初期データ Migration で person_viewer Group に紐付け済 |
| 8 | `/persons/<uuid:pk>/` | GET | PersonDetailView | `persons:person_detail` | 必須 ★2 | `persons.view_person`（Django標準）★1 | 全員 ★1 | v1.4.2 既存。Person.status 別二系統化（active → ContactDetailView リダイレクト、merged/archived → 専用画面） |
| 9 | `/persons/<uuid:pk>/add-additional-role/` | GET / POST | PersonAddAdditionalRoleView | `persons:person_add_additional_role` | 必須 ★2 | `persons.change_person`（Django標準）★2 | admin / sales ★2 | v1.4.2 既存。Person への追加役職付与（複数役職対応）。仕様書 §13.8 の `persons.link_user`（User-Person 紐付け権限）とは概念が異なるため、v1.5.0 では Django 標準で済ませる。**論点 F**：業務上の Permission `persons.add_additional_role` を v1.6+ で別途定義するかは要判断 |
| 10 | `/contacts/create/` | GET / POST | ContactCreateView | `contacts:contact_create` | 必須 ★2 | `contacts.add_contact`（Django標準）★2 | admin / sales ★2 | v1.4.2 既存 |
| 11 | `/contacts/<uuid:pk>/` | GET | ContactDetailView | `contacts:contact_detail` | 必須 ★2 | `contacts.view_contact`（Django標準）★2 | 全員 ★2 | v1.4.2 既存。業務メイン画面、6 セクション構成 |
| 12 | `/contacts/<uuid:pk>/update-primary/` | GET / POST | UpdatePrimaryContactView | `contacts:contact_update_primary` | 必須 ★2 | `contacts.change_contact`（Django標準）★2 | admin / sales ★2 | v1.4.2 既存 |
| 13 | `/contacts/<uuid:pk>/update-active/` | GET / POST | UpdateActiveContactView | `contacts:contact_update_active` | 必須 ★2 | `contacts.change_contact`（Django標準）★2 | admin / sales ★2 | v1.4.2 既存 |
| 14 | `/contacts/<uuid:pk>/preview/` | GET | PreviewContactView | `contacts:contact_preview` | 必須 ★2 | `contacts.view_contact`（Django標準）★2 | 全員 ★2 | v1.4.2 既存 |
| 15 | `/duplicates/` | GET | DuplicateCandidateGroupListView | `duplicates:duplicate_group_list` | 必須 ★2 | `persons.merge_person` ★1 | admin / sales ★1 | v1.4.2 既存。マージ候補一覧。§13.7 View 層で `PermissionRequiredMixin` 適用 |
| 16 | `/duplicates/groups/<uuid:group_id>/` | GET | DuplicateCandidateGroupDetailView | `duplicates:duplicate_group_detail` | 必須 ★2 | `persons.merge_person` ★1 | admin / sales ★1 | v1.4.2 既存。マージ候補詳細 |
| 17 | `/duplicates/groups/<uuid:group_id>/review/` | GET / POST | DuplicateCandidateGroupUpdateView | `duplicates:duplicate_group_review` | 必須 ★2 | `persons.merge_person` ★1 | admin / sales ★1 | v1.4.2 既存。**v1.5.0 でマージ実行時の権限拡張**（§13.2 / §13.3 / §13.4 / §13.7 で `_check_merge_permission()` Service 層判定追加） |
| 18 | （欠番） | — | — | — | — | — | — | v1.4.2 改訂で旧 `/duplicates/groups/<uuid:group_id>/result/`（DuplicateCandidateGroupResultView）が廃止（§11.3.1） |
| 19 | `/merge-logs/` | GET | PersonMergeLogListView | `duplicates:merge_log_list` | 必須 ★2 | `duplicates.view_personmergelog`（Django標準）★2 | 全員 ★2 | v1.4.2 既存。マージログ一覧（閲覧のみ）。PersonMergeLog は `duplicates` アプリのモデルなので、Django 標準では `duplicates.view_personmergelog` が自然 |
| 20 | `/merge-logs/<uuid:pk>/` | GET | PersonMergeLogDetailView | `duplicates:merge_log_detail` | 必須 ★2 | `duplicates.view_personmergelog`（Django標準）★2 | 全員 ★2 | v1.4.2 既存。マージログ詳細。Permission は No.19 と同じ理由で duplicates アプリのものを使用 |
| 21 | `/merge-logs/<uuid:pk>/confirm-undo/` | GET / POST | PersonMergeLogConfirmUndoView | `duplicates:merge_log_confirm_undo` | 必須 ★2 | `persons.undo_merge` ★1 | admin ★1 | v1.4.2 既存。**v1.5.0 でマージ復元時の権限拡張**（§13.3 で `_check_merge_permission()` Service 層判定追加）。`undo_merge` は admin のみ |
| 22 | `/cards/<uuid:pk>/delete/` | POST | CardDeleteView | `cards:card_delete` | 必須 ★2 | `cards.delete_card`（Django標準）★2 | admin / sales ★2 | v1.4.2 既存（未実装、ストック #17 で仕様確定済）。POST 専用、CASCADE 連鎖、削除後は元画像詳細へ 302 |
| 23 | `/contacts/` | GET | ContactListView | `contacts:contact_list` | 必須 ★2 | `contacts.view_contact`（Django標準）★2 | 全員 ★2 | v1.4.2 既存 |

---

## v1.5.0 新規追加 URL

### accounts アプリ（認証・パスワード管理）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 24 | `/accounts/login/` | GET / POST | LoginView（Django標準） | `accounts:login` | 不要 ★1 | なし | — | **v1.5.0 新規**。Django 標準 `django.contrib.auth.views.LoginView` を使用。templates/accounts/login.html を独自に用意 |
| 25 | `/accounts/logout/` | POST | LogoutView（Django標準） | `accounts:logout` | 必須 ★1 | なし | 全員 | **v1.5.0 新規**。Django 5.0+ から POST 必須。ログアウト後はログイン画面へリダイレクト |
| 26 | `/accounts/password/change/` | GET / POST | PasswordChangeView（Django標準） | `accounts:password_change` | 必須 ★2 | なし | 全員（本人のみ） | **v1.5.0 新規**。ログイン状態でのパスワード変更 |
| 27 | `/accounts/password/change/done/` | GET | PasswordChangeDoneView（Django標準） | `accounts:password_change_done` | 必須 ★2 | なし | 全員 | **v1.5.0 新規**。パスワード変更完了画面 |
| 28 | `/accounts/password/reset/` | GET / POST | PasswordResetView（Django標準） | `accounts:password_reset` | 不要 ★2 | なし | — | **v1.5.0 新規**。パスワードリセット申請（メール経由）。SMTP：Xサーバー使用予定 |
| 29 | `/accounts/password/reset/done/` | GET | PasswordResetDoneView（Django標準） | `accounts:password_reset_done` | 不要 ★2 | なし | — | **v1.5.0 新規**。リセット申請完了画面 |
| 30 | `/accounts/password/reset/<uidb64>/<token>/` | GET / POST | PasswordResetConfirmView（Django標準） | `accounts:password_reset_confirm` | 不要 ★2 | なし | — | **v1.5.0 新規**。メール内リンクから新パスワード設定 |
| 31 | `/accounts/password/reset/complete/` | GET | PasswordResetCompleteView（Django標準） | `accounts:password_reset_complete` | 不要 ★2 | なし | — | **v1.5.0 新規**。リセット完了画面 |

### accounts アプリ（User 管理）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 32 | `/accounts/users/` | GET | UserListView | `accounts:user_list` | 必須 ★1 | `accounts.view_customuser`（Django標準）★2 | admin ★2 | **v1.5.0 新規**。仕様書 §12.6 で RetireUserView のリダイレクト先として明示、URL 定義は仕様書では省略（「`# ... 他の URL ...`」）。最小機能：一覧表示 + 詳細・退職処理への導線 |
| 33 | `/accounts/users/<int:user_id>/` | GET | UserDetailView | `accounts:user_detail` | 必須 ★2 | `accounts.view_customuser`（Django標準）★2 | admin ★2 | **v1.5.0 新規**。最小機能：詳細表示 + 退職処理ボタンへの導線。**仕様書未記載**、業務上必要として追加 |
| 34 | `/accounts/users/<int:user_id>/link/<uuid:person_id>/` | POST | LinkUserPersonView | `accounts:link_user_person` | 必須 ★1 | 本人 OR `accounts.link_user_to_person` ★1 | 本人 / admin | **v1.5.0 新規**（§12.7）。POST 専用、ホーム画面アラートから遷移想定。成功時は home へリダイレクト |
| 35 | `/accounts/users/<int:user_id>/unlink/` | POST | UnlinkUserPersonView | `accounts:unlink_user_person` | 必須 ★1 | 本人 OR `accounts.link_user_to_person` ★1 | 本人 / admin | **v1.5.0 新規**（§12.7）。POST 専用。成功時は home へリダイレクト |
| 36 | `/accounts/users/<int:user_id>/retire/` | GET / POST | RetireUserView | `accounts:retire_user` | 必須 ★1 | `accounts.retire_user` ★1 | admin ★1 | **v1.5.0 新規**（§12.6 案 B）。GET=後継者選択フォーム、POST=`retire_user()` 実行。Admin actions（`/admin/accounts/customuser/`）でも実行可（§4.1） |

### Django Admin

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 37 | `/admin/...` | — | （Django 標準 Admin） | （Django 標準） | 必須 ★1 | `is_staff=True` ★1 | admin ★1 | Django Admin。CustomUserAdmin（§4.1）の特殊挙動：①`user_permissions` を fieldsets / form base_fields から除外（URL 直叩き保護）、② Role 変更時のみ `apply_role()` 発火、③ `retire_user_action` Admin アクション（後継者選択インターメディエイト画面付き） |

---

## v1.5.0 で守る既存 URL への認証・認可付与方針

仕様書 §8 / §13.7 に基づき、v1.4.2 既存 URL（No.1〜23）には以下を一括付与する。

### LoginRequiredMixin の付与（全 v1.4.2 既存 View）

- 全 23 URL（No.18 欠番除く）に `LoginRequiredMixin` を付与
- 未ログインアクセスは `/accounts/login/?next=<元URL>` にリダイレクト
- `settings.LOGIN_URL = 'accounts:login'` を設定（Django デフォルトの `/accounts/login/` と一致）

### PermissionRequiredMixin の付与（マージ系のみ）

仕様書 §13.7 / §13.8 で明示されている Permission を View 層で付与：

- **No.15 / No.16 / No.17**（duplicates 系）：`persons.merge_person`
- **No.21**（merge_log_confirm_undo）：`persons.undo_merge`

### View 層 vs Service 層の責務分担（§13.7）

- **View 層**：UI 制御（ボタン非表示・画面アクセス拒否）→ `request.user.has_perm('app.codename')`
- **Service 層**：データレベル権限（Person 個別の権限）→ `can_merge_person(user, person)` で詳細判定（§13.1）

両層で二重防衛。API 直叩き・shell 経由・別 View からの Service 呼び出しでも防御が効く。

---

## v1.5.0 で確定した Role / Group / Permission の紐付け（§13.8）

### Role（3 種）

| Role コード | 名前 | sort_order | default_groups |
|---|---|---|---|
| `admin` | 管理者 | 1 | `person_admin`, `user_admin` |
| `sales` | 営業 | 2 | `person_editor` |
| `viewer` | 閲覧者 | 3 | `person_viewer` |

### Group（仕様書明示分 + クロード推奨追加分）

| Group 名 | 含まれる Permission | 含まれる Role | 仕様書記載 |
|---|---|---|---|
| `person_admin` | `persons.undo_merge`, `persons.merge_person`, `persons.link_user` | `admin` | ★1（§13.8） |
| `person_editor` | `persons.merge_person`, `persons.link_user` | `sales` | ★1（§13.8） |
| `person_viewer` | `persons.view_person`（標準） | `viewer` | ★1（§13.8） |
| `user_admin` | `accounts.link_user_to_person`, `accounts.retire_user` | `admin` | ★1（§13.8） |
| `card_admin` | `cards.create_card`, `cards.edit_card`, `cards.merge_card` | `admin` | ★2 §8 運用例で言及あるが §13.8 初期データ Migration に未登場 |
| `card_editor` | `cards.create_card`, `cards.edit_card` | `sales` | ★2 同上 |
| `card_viewer` | `cards.view_card`（標準） | `viewer` | ★2 同上 |

### Permission（仕様書明示分）

| Permission codename | content_type | 説明 | 仕様書記載 |
|---|---|---|---|
| `persons.undo_merge` | persons.person | マージ復元を実行できる | ★1（§13.8） |
| `persons.merge_person` | persons.person | Person マージを実行できる | ★1（§13.8） |
| `persons.link_user` | persons.person | User-Person 紐付けを設定できる | ★1（§13.8） |
| `accounts.link_user_to_person` | accounts.customuser | User と Person の紐付けを管理できる | ★1（§13.8） |
| `accounts.retire_user` | accounts.customuser | ユーザを退職処理できる | ★1（§13.8） |
| `cards.create_card` | cards.businesscard | 名刺カードを作成できる | ★2 §7.4 のみ |
| `cards.edit_card` | cards.businesscard | 名刺カードを編集できる | ★2 §7.4 のみ |
| `cards.merge_card` | cards.businesscard | 名刺カードをマージできる | ★2 §7.4 のみ |

---

## ★2 クロード君のレコメンド一覧（仕様書未記載項目）

実装前にたんたん確認が必要な項目を集約。

### A. cards.* Permission の初期データ Migration 整備（★2）

**現状**：仕様書 §7.4 で `cards.create_card` / `cards.edit_card` / `cards.merge_card` Permission が言及されている。§8 運用例で `card_admin` / `card_editor` / `card_viewer` Group が言及されている。しかし §13.8 の初期データ Migration には未登場。

**論点**：

- (1) cards.* Permission を v1.5.0 で実装するか、v1.6+ に持ち越すか
- (2) 実装するなら `cards/models.py` の `BusinessCard.Meta.permissions` に追加 + `accounts/migrations/00XX_create_initial_roles_and_groups.py` を拡張

**クロード推奨**：v1.5.0 で実装。理由は §7.4 で「v1.5.0」明示されているため。

### B. 既存 View（No.2〜14, No.19, No.20, No.22, No.23）の Permission（★2）

**現状**：仕様書 §8 では「PermissionGroup のプリセット運用」が明示されているが、個別 View（OriginalImageUploadView / CardListView 等）にどの Permission を要求するかは明示されていない。

**論点**：

- (1) Django 標準 Permission（`view_*` / `add_*` / `change_*` / `delete_*`）で十分か
- (2) 業務上の Permission（`cards.create_card` 等）を別途定義するか

**クロード推奨**：Django 標準 Permission で v1.5.0 を回す。業務上の Permission（A 項参照）は cards.* と persons.* で v1.5.0 で実装、その他は v1.6+ で要件が固まってから追加。

### C. ロール別アクセス可否（★2）

**現状**：仕様書には Role が「業務上の肩書き、UI/業務語彙の表示用」と位置付けられ、業務ロジックでの権限判定は `has_perm()` を使うと明示（§6）。個別 URL のロール別アクセス可否は明示されていない。

**論点**：本表で記載した「ロール」列の値は、サポート担当の業務上の想定。実際の運用で違和感があれば調整必要。

**クロード推奨**：実装後の運用で違和感が出た時点で、ロール別 default_groups を見直す。

### D. UserDetailView の要否（★2）

**現状**：仕様書 §12.6 で `accounts:user_list` が RetireUserView のリダイレクト先として言及されているが、`accounts:user_detail` は仕様書未記載。

**論点**：UserListView だけで「一覧 + 退職処理ボタン」をまかなえる場合、UserDetailView は不要。

**クロード推奨**：たんたんの Q3 回答「最小機能のみ（一覧表示 + 詳細表示、退職処理ボタンへの導線）」に従い、UserDetailView を実装。

### E. SMTP 設定（★2）

**現状**：パスワードリセット URL（No.28〜31）はメール送信が必要。仕様書では SMTP 設定方針は未記載。

**論点**：`settings.py` の EMAIL_BACKEND / EMAIL_HOST / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD / DEFAULT_FROM_EMAIL の設定。

**クロード推奨**：たんたん回答通り Xサーバー使用。開発時はコンソール出力（`django.core.mail.backends.console.EmailBackend`）で代用も可。`.env` の設定項目なので、Phase 0 / Phase 1 / Phase 2 では関与しない。**優先度：Phase 3 LDAP 実装の頃に決まればよい、Phase 4 紐付けまたは Phase 5 マージ権限拡張の前くらいまでに確定で間に合う**。

### F. PersonAddAdditionalRoleView の Permission 定義（★2）

**現状**：No.9 `PersonAddAdditionalRoleView` は v1.4.2 既存機能（Person に追加役職を付与）。v1.5.0 仕様書 §13.8 の `persons.link_user` は「User-Person 紐付けを設定できる」と定義されており、概念が異なる。

**論点**：

- (1) v1.5.0 では Django 標準 `persons.change_person` で済ませる（クロード推奨）
- (2) v1.6+ で業務上の Permission `persons.add_additional_role` を別途定義するか判断

**クロード推奨**：v1.5.0 では Django 標準 `persons.change_person` を使用。v1.6+ で AccessList 導入や Role の業務拡張を検討する段階で、`persons.add_additional_role` の必要性を再評価する。

---

## 仕様書改訂依頼項目（オーパス君向け）

実装着手前に仕様書側のバグ・整合性問題として修正が必要な箇所。

### 1. §13.8 の Permission 重複タイポ

**箇所**：v1.5.0 仕様書 v1.4 §13.8 「実装すべき Permission」セクション、`Person.Meta.permissions`

**現状**：

```python
permissions = [
    ('undo_merge',   'マージ復元を実行できる'),
    ('merge_person', 'Person マージを実行できる'),
    ('link_user',    'User-Person 紐付けを設定できる'),
    ('link_user',    'User-Person 紐付けを設定できる'),  # ← 重複！
]
```

**問題**：`link_user` が2回定義されている。Django はマイグレーション時に重複定義を許容しないため、Migration 実行エラーになる可能性。

**修正依頼**：2行目の `('link_user', ...)` を削除。

### 2. §13.8 と §8 の cards.* Permission の不整合

**箇所**：v1.5.0 仕様書 v1.4 §7.4 / §8 / §13.8

**現状**：

- §7.4：「`cards.create_card` / `cards.edit_card` / `cards.merge_card` ← v1.5.0」と明示
- §8 運用例：Role 別の default_groups に `card_admin` / `card_editor` / `card_viewer` が登場
- §13.8 命名規約：Group 名に `card_admin` / `card_editor` / `card_viewer` が登場
- §13.8 初期データ Migration：cards.* Permission と card_* Group が**未登場**

**問題**：cards.* Permission を v1.5.0 で実装するのか、v1.6+ に持ち越すのか、仕様書内で整合性が取れていない。

**修正依頼**：どちらかに統一。クロード推奨は v1.5.0 で実装（§7.4 で「v1.5.0」と明示されているため）。実装する場合は §13.8 初期データ Migration に追記。

### 3. accounts:user_list の URL 定義省略

**箇所**：v1.5.0 仕様書 v1.4 §12.6 / §12.7

**現状**：§12.6 で `RetireUserView` のリダイレクト先として `accounts:user_list` が言及されているが、§12.7 末尾の URL 定義では「`# ... 他の URL ...`」と省略されている。

**修正依頼**：§12.7 末尾の URL 定義に `user_list` / `user_detail` を追記。本書 No.32 / No.33 を参照。

---

## 工程表（`v1_5_認証_認可_工程表.docx`）との対応

各 URL がどの Phase で実装されるかの対応表。

| No. | URL name | 実装 Phase | 備考 |
|---|---|---|---|
| 1〜23 | （v1.4.2 既存 URL 全般） | Phase 2-3（CustomUserAdmin 実装時）／Phase 5（マージ系のみ Service 層権限拡張） | LoginRequiredMixin 一括付与は Phase 2 で対応 |
| 1 | `home` | **Phase 4-3**（ホーム画面アラート） | §12.4 |
| 15-17, 19-21 | duplicates 系 | **Phase 5**（マージ権限拡張、v1.4.2 核心部に触るので慎重に） | §13 |
| 24-25 | `accounts:login` / `accounts:logout` | **Phase 2-3**（CustomUserAdmin 実装と同時、または Phase 3 LDAP 連携時） | Django 標準 LoginView / LogoutView |
| 26-31 | パスワード変更・リセット系 | **Phase 3〜4**（SMTP 設定が決まる時期） | Phase 3 LDAP 連携で .env を整備するタイミング |
| 32-33 | `accounts:user_list` / `accounts:user_detail` | **Phase 6**（退職処理 UI） | RetireUserView のリダイレクト先として必須 |
| 34-35 | `accounts:link_user_person` / `accounts:unlink_user_person` | **Phase 4**（User-Person 紐付け） | §12.7 |
| 36 | `accounts:retire_user` | **Phase 6**（退職処理） | §12.6 |
| 37 | Django Admin | **Phase 2-3**（CustomUserAdmin 実装） | §4.1 |

---

## 補足

- **No.18 は欠番**：v1.4.2 改訂で旧 `/duplicates/groups/<uuid:group_id>/result/`（DuplicateCandidateGroupResultView）が廃止された（§11.3.1 参照）
- **PersonDetailView（No.8）の挙動**：Person.status 別二系統化（active → ContactDetailView へ HTTP 302 リダイレクト、merged / archived → 専用詳細画面）
- **CardDetailView（No.4）の業務画面化**：OpenCV デバッグセクションに加えて Contact 編集 UI を併設
- **ContactDetailView（No.11）は業務メイン画面**：表示モード分岐、6 セクション構成、AJAX 編集
- **CardDeleteView（No.22）の挙動**：POST 専用、`bc.delete()` で CASCADE 連鎖、削除後は元画像詳細（No.6）へ 302
- **マージ系（No.15〜17, No.21）の v1.5.0 改修**：仕様書 §13 で `_check_merge_permission()` を Service 層に追加。両方 User 紐付き Person のマージは禁止（多重防衛）
- **ホーム画面（No.1）の v1.5.0 改修**：仕様書 §12.4 で `LoginRequiredMixin` 適用 + email マッチ Contact の紐付けアラート機能追加（ORM 完結クエリ）

---

## 改訂履歴

| 日付 | 内容 |
|---|---|
| 2026-05-13 | 旧 `URL一覧表_v1_4_2.md` 作成（v1.4.2 既存 URL のみ） |
| 2026-05-15 | サポート担当クロード君が v1.5.0 認証・認可・LDAP 設計方針 v1.4 を元に、v1.5.0 で追加・変更される URL（No.24〜37）と既存 URL への認証・認可付与方針を統合。★1 / ★2 マークで仕様書記載度合いを明示、★2 項目は別セクションに集約。 |
| 2026-05-15（2回目） | 別セッションのサポート担当クロード君のレビューに基づき修正：(1) No.9 PersonAddAdditionalRoleView の Permission を `persons.link_user`（概念ズレ）→ `persons.change_person`（Django標準）に変更、論点 F を追加。(2) No.19 / No.20 merge_log 系の Permission を `persons.view_person`（流用）→ `duplicates.view_personmergelog`（正しいアプリ）に変更。(3) ★2-E SMTP 設定の優先度を「Phase 3〜4 で確定で間に合う」と明記。(4) 「仕様書改訂依頼項目」セクションを新設（§13.8 link_user 重複タイポ、§7.4/§8/§13.8 の cards.* 不整合、accounts:user_list の URL 定義省略）。(5) 「工程表との対応」セクションを新設、各 URL を Phase に紐付け。 |
