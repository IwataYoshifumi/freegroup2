# FreeGroup2 URL 一覧表（v1.4.2 + v1.5.0 + v1.6 統合版）

**最終更新**：2026-05-18
**正本**：
- `_最終の最終版_名刺画像取り込みOCR仕様書_v1_4_2統合最終版.md` §11.3（v1.4.2 既存 URL）
- `_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_4.md` §12.6 / §12.7 / §13（v1.5.0 認証・認可・User管理 URL）
- **`仕様書_v1_6_メール配信_クリックトラッキング_ドラフト_rev12_3.md` §5.1 / §6.2 / §14（v1.6 メール配信・クリックトラッキング・タグ・リスト URL）**

**位置づけ**：本書は既存 `URL一覧表_v1_5_0.md`（v1.4.2 + v1.5.0 統合版）の正統な後継。v1.6 メール配信・クリックトラッキング機能の URL 群（rev12.3 §5.1 で確定済みの 42 番＋ rev12 で新設された画面 URL）を統合した一覧表。「ルート / メソッド / View 名 / URL name / ログイン要否 / Permission / ロール / 備考」の 9 列リスト構成は既存表を踏襲。

---

## 凡例

- **★1**：仕様書に明示されている URL／Permission／ロール
- **★2**：仕様書に明示されていない、サポート担当クロード君のレコメンド。実装前にたんたん確認必要
- **ログイン**：
  - 「必須」＝`LoginRequiredMixin` または `@login_required` が必要
  - 「不要」＝匿名アクセス可（ログイン画面・パスワードリセット申請画面等）
- **Permission**：Django Permission の codename（`<app>.<codename>` 形式）。複数の場合は OR/AND を明示
- **ロール**（v1.5.0 で確定、v1.6 で `email_admin` / `email_editor` / `email_viewer` / `tag_admin` / `tag_editor` / `tag_viewer` Group を追加）：
  - `admin`＝管理者（v1.6 で `email_admin` + `tag_admin` を default_groups に追加、rev12.3 §14.3）
  - `sales`＝営業（v1.6 で `email_editor` + `tag_editor` を default_groups に追加）
  - `viewer`＝閲覧者（v1.6 で `email_viewer` + `tag_viewer` を default_groups に追加）
  - 「本人」＝紐付け先 User と request.user が一致するケース／キャンペーン作成者本人（`campaign.created_by == request.user`）
  - 「全員」＝ログイン済み User なら誰でも
  - 「外部」＝匿名アクセス可（受信者向け：クリックトラッキング中継・配信停止リンク）

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
| 8 | `/persons/<uuid:pk>/` | GET | PersonDetailView | `persons:person_detail` | 必須 ★2 | `persons.view_person`（Django標準）★1 | 全員 ★1 | v1.4.2 既存。Person.status 別二系統化（active → ContactDetailView リダイレクト、merged/archived → 専用画面）。**v1.5.0 Phase 8 #4 で `person_detail_orphan.html` に「ユーザー紐付け」セクション追加**（`accounts:link_user_person_confirm` への導線、状態に応じて「紐付ける／解除する／（他 User 紐付け済み表示）」を出し分け） |
| 9 | `/persons/<uuid:pk>/add-additional-role/` | GET / POST | PersonAddAdditionalRoleView | `persons:person_add_additional_role` | 必須 ★2 | `persons.change_person`（Django標準）★2 | admin / sales ★2 | v1.4.2 既存。Person への追加役職付与（複数役職対応）。仕様書 §13.8 の `persons.link_user`（User-Person 紐付け権限）とは概念が異なるため、v1.5.0 では Django 標準で済ませる。**論点 F**：業務上の Permission `persons.add_additional_role` を v1.6+ で別途定義するかは要判断 |
| 10 | `/contacts/create/` | GET / POST | ContactCreateView | `contacts:contact_create` | 必須 ★2 | `contacts.add_contact`（Django標準）★2 | admin / sales ★2 | v1.4.2 既存 |
| 11 | `/contacts/<uuid:pk>/` | GET | ContactDetailView | `contacts:contact_detail` | 必須 ★2 | `contacts.view_contact`（Django標準）★2 | 全員 ★2 | v1.4.2 既存。業務メイン画面、6 セクション構成。**v1.5.0 Phase 8 #4 で操作ボタン列に「このユーザーで紐付ける／紐付け済み（解除）」リンク追加**（`accounts:link_user_person_confirm` への導線、`contact.person` が存在する場合のみ表示。`append_back_url` で戻り先を保持） |
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
| 36 | `/accounts/users/<int:user_id>/retire/` | GET / POST | RetireUserView | `accounts:retire_user` | 必須 ★1 | `accounts.retire_user` ★1 | admin ★1 | **v1.5.0 新規**（§12.6 案 B）。GET=後継者選択フォーム、POST=`retire_user()` 実行。Admin actions（`/admin/accounts/customuser/`）でも実行可（§4.1）。**Phase 8 でトップバー（`_topbar.html`）に「ユーザ管理」リンク追加**（`perms.accounts.retire_user` ガード）→ user_list 経由で本 View へ |
| 37 | `/accounts/profile/` | GET | ProfileView | `accounts:profile` | 必須 ★1 | なし | 全員（本人のみ） | **v1.5.0 Phase 8 新規**。ログインユーザ自身のプロフィール画面。User の username / 氏名 / メール / ロール / 部署 / 認証ソース / 紐付き Person を表示。紐付き Person がある場合は「紐付けを解除する」リンク（→ `accounts:link_user_person_confirm` 経由で確認画面へ）、未紐付け時は「Person を探して紐付ける」リンク（→ `accounts:start_link_flow`）を出し分け |
| 38 | `/accounts/user-person/confirm/<uuid:person_id>/` | GET / POST | LinkUserPersonConfirmView | `accounts:link_user_person_confirm` | 必須 ★1 | なし（`LoginRequiredMixin` のみ） | 全員（本人のみ） | **v1.5.0 Phase 8 #4 新規**。User-Person 紐付け／解除の確認画面。GET：ログイン User と対象 Person を横並び表示（会社名・氏名・メール・部署・役職）。POST：`action=link` で紐付け、`action=unlink` で解除を実行。本フローは常に request.user 自身への操作のため Permission 不要。Person 詳細・Contact 詳細・プロフィール画面からの導線を一本化する確認ハブ。詳細仕様は §12.7 |
| 39 | `/accounts/user-person/start-link/` | GET | StartLinkFlowView | `accounts:start_link_flow` | 必須 ★1 | なし（`LoginRequiredMixin` のみ） | 全員（本人のみ） | **v1.5.0 Phase 8 #4 新規**。「Person を探して紐付ける」フロー開始 View。`messages.info` で案内を出し、Person 一覧（`persons:person_list`）へ `email=<user.email>&searched=1&status=active` 付きでリダイレクト。プロフィール画面の「Person を探して紐付ける」ボタンから使われる。`status=active` を明示しないと Person 一覧のフィルタが 0 件になる仕様（searched=1 単独だと既定値が外れる）への対処を含む。詳細仕様は §12.7 |

### Django Admin

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 40 | `/admin/...` | — | （Django 標準 Admin） | （Django 標準） | 必須 ★1 | `is_staff=True` ★1 | admin ★1 | Django Admin。CustomUserAdmin（§4.1）の特殊挙動：①`user_permissions` を fieldsets / form base_fields から除外（URL 直叩き保護）、② Role 変更時のみ `apply_role()` 発火、③ `retire_user_action` Admin アクション（後継者選択インターメディエイト画面付き）。**Phase 8 でトップバー（`_topbar.html`）のユーザメニューに「管理画面」リンク追加**（`user.is_staff` ガード） |

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

## v1.6 新規追加 URL（メール配信・クリックトラッキング、rev12.3 §5.1 / §6.2 / §14 反映）

**正本**：`仕様書_v1_6_メール配信_クリックトラッキング_ドラフト_rev12_3.md` §5.1（URL 一覧表、42 番）／§6.2（画面設計、リスト作成画面・タグカテゴリ管理画面・検索結果一括タグ付け画面）／§14（認可設計、Permission・Group・Role）。

### mailings アプリ（メールテンプレート）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 41 | `/mailings/templates/` | GET | EmailTemplateListView | `mailings:template_list` | 必須 ★1 | `mailings.view_campaign` ★1 | 全員 ★1 | **v1.6 新規**（rev12.3 §5.1 No.1）。テンプレートは全社共有方式（Sansan 方式、rev12.3 §14.4.2）。閲覧は全員（`mailings.view_campaign` 持ち） |
| 42 | `/mailings/templates/create/` | GET / POST | EmailTemplateCreateView | `mailings:template_create` | 必須 ★1 | `mailings.manage_template` ★1 | admin / sales（email_admin / email_editor） ★1 | **v1.6 新規**（rev12.3 §5.1 No.2）。本文はプレーンテキスト入力（rev9 確定、HTML タグは書かない、記法 3 種のみ）、`EmailContext.prepare` が HTML 化（rev12.3 §7.4 / §1.1） |
| 43 | `/mailings/templates/<uuid:pk>/` | GET | EmailTemplateDetailView | `mailings:template_detail` | 必須 ★1 | `mailings.view_campaign` ★1 | 全員 ★1 | **v1.6 新規**（rev12.3 §5.1 No.3） |
| 44 | `/mailings/templates/<uuid:pk>/update/` | GET / POST | EmailTemplateUpdateView | `mailings:template_update` | 必須 ★1 | `mailings.manage_template` ★1 | admin / sales ★1 | **v1.6 新規**（rev12.3 §5.1 No.4） |
| 45 | `/mailings/templates/<uuid:pk>/delete/` | POST | EmailTemplateDeleteView | `mailings:template_delete` | 必須 ★1 | `mailings.manage_template` ★1 | admin / sales ★1 | **v1.6 新規**（rev12.3 §5.1 No.5）。論理削除（`is_archived=True`、rev12.3 §12.4） |

### mailings アプリ（キャンペーン）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 46 | `/mailings/campaigns/` | GET | CampaignListView | `mailings:campaign_list` | 必須 ★1 | `mailings.view_campaign`（本人） OR `mailings.view_all_campaigns`（全員） ★1 | 本人 / admin（view_all_campaigns 持ち） ★1 | **v1.6 新規**（rev12.3 §5.1 No.6）。自分のキャンペーン（`view_all_campaigns` 持ちなら全件、rev12.3 §14.4.1） |
| 47 | `/mailings/campaigns/create/` | GET / POST | CampaignCreateView | `mailings:campaign_create` | 必須 ★1 | `mailings.create_campaign` ★1 | admin / sales ★1 | **v1.6 新規**（rev12.3 §5.1 No.7）。テンプレート選択、宛先＝リスト選択のみ（rev12 で `tag_condition` 削除・`mailing_list` 必須 FK 化）、予約配信日時（必須・未来日時のみ） |
| 48 | `/mailings/campaigns/<uuid:pk>/` | GET | CampaignDetailView | `mailings:campaign_detail` | 必須 ★1 | `mailings.view_campaign` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.8）。`Campaign.has_view_permission(user)` で本人 or `view_all_campaigns` 持ち判定（rev12.3 §4.2.1） |
| 49 | `/mailings/campaigns/<uuid:pk>/update/` | GET / POST | CampaignUpdateView | `mailings:campaign_update` | 必須 ★1 | `mailings.create_campaign` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.9）。draft 状態のみ可 |
| 50 | （★2 候補、コード君判断） | POST | 予約登録用 View（具体名はコード君判断） | （★2、`mailings:campaign_schedule` 等が自然だがコード君判断） | 必須 ★2 | `mailings.create_campaign` + 所有者判定 ★2 | 本人 / admin ★2 | **【rev9 で即時配信実行エンドポイント `/mailings/campaigns/<uuid:pk>/execute/` は廃止】 予約登録用 URL・View 名は仕様書では確定せず、コード君の実装時判断**（rev12.3 §5.1 No.10、rev8 B 群・C 群の流儀踏襲）。★2 候補：URL を本表で確定しない |
| 51 | `/mailings/campaigns/<uuid:pk>/test-send/` | GET / POST | CampaignTestSendView | `mailings:campaign_test_send` | 必須 ★1 | `mailings.send_campaign` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.11）。テスト配信は cron を通らない例外、トークン非発行（`EmailContext.prepare(mode=EmailMode.TEST)`、rev12.3 §7.7）。`{% unsubscribe_link %}` は `/u/`（トークン無し独立ルート、No.84）に変換（rev10 確定） |

### mailings アプリ（配信レポート）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 52 | `/mailings/campaigns/<uuid:pk>/report/` | GET | CampaignReportView | `mailings:campaign_report` | 必須 ★1 | `mailings.view_campaign` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.12）。配信レポート詳細 |
| 53 | `/mailings/campaigns/<uuid:pk>/report/clicked/` | GET | CampaignReportClickedListView | `mailings:campaign_report_clicked` | 必須 ★1 | `mailings.view_campaign` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.13）。有効クリックした受信者の一覧 |
| 54 | `/mailings/campaigns/<uuid:pk>/report/bounced/` | GET | CampaignReportBouncedListView | `mailings:campaign_report_bounced` | 必須 ★1 | `mailings.view_campaign` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.14）。バウンスした受信者の一覧（DeliveryHistory.status=bounced はベストエフォート反映、配信を止める実効力は SuppressedEmail フィルタが持つ、rev12.3 §10.4） |
| 55 | `/mailings/campaigns/<uuid:pk>/report/unsubscribed/` | GET | CampaignReportUnsubscribedListView | `mailings:campaign_report_unsubscribed` | 必須 ★1 | `mailings.view_campaign` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.15）。配信停止した受信者の一覧 |
| 56 | `/mailings/campaigns/<uuid:pk>/report/csv/` | GET | CampaignReportCSVView | `mailings:campaign_report_csv` | 必須 ★1 | `mailings.export_report` + 所有者判定 ★1 | 本人 / admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.16）。CSV ダウンロード（配信時点スナップショット出力、rev6 §13.2） |

### mailings アプリ（配信拒否リスト）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 57 | `/mailings/suppressed/` | GET | SuppressedEmailListView | `mailings:suppressed_list` | 必須 ★1 | `mailings.view_campaign` ★1 | 全員 ★1 | **v1.6 新規**（rev12.3 §5.1 No.17）。SuppressedEmail 一覧 |
| 58 | `/mailings/suppressed/<uuid:pk>/` | GET / POST | SuppressedEmailDetailView | `mailings:suppressed_detail` | 必須 ★1 | GET=`mailings.view_campaign` / POST=`mailings.manage_suppressed_email` ★1 | GET=全員 / POST=admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.18）。`cancelled_at` の更新（解除） |
| 59 | `/mailings/suppressed/add/` | GET / POST | SuppressedEmailCreateView | `mailings:suppressed_create` | 必須 ★1 | `mailings.manage_suppressed_email` ★1 | admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.19）。管理者手動登録 |

### mailings アプリ（システム設定）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 60 | `/settings/system/` | GET / POST | SystemSettingsView | `mailings:settings_system` | 必須 ★1 | `is_staff=True` または別途 ★2 | admin のみ ★1 | **v1.6 新規**（rev12.3 §5.1 No.38）。Settings モデル編集（シングルトン、特電法対応の会社情報・差出メール・DKIM 設定等、rev12.3 §4.13）。具体的な Permission codename は rev12.3 §14 で未明示、★2 候補（`mailings.manage_settings` 等が自然だがコード君判断） |
| 61 | `/settings/domain-auth/guide/` | GET | DomainAuthGuideView | `mailings:settings_domain_auth_guide` | 必須 ★2 | ★2（明示なし、admin 想定） | admin ★2 | **v1.6 新規**（rev12.3 §5.1 No.39）。DNS 設定値を顧客向けに表示（DKIM 鍵ペア生成後、rev12.3 第15章）。Permission は rev12.3 §14 で未明示、★2 候補 |
| 62 | `/settings/domain-auth/diagnose/` | GET / POST | DomainAuthDiagnoseView | `mailings:settings_domain_auth_diagnose` | 必須 ★2 | ★2（明示なし、admin 想定） | admin ★2 | **v1.6 新規**（rev12.3 §5.1 No.40）。SPF / DKIM / DMARC の設定状況チェック（rev12.3 第15章）。Permission は rev12.3 §14 で未明示、★2 候補 |

### tags アプリ（タグ）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 63 | `/tags/` | GET | TagListView | `tags:tag_list` | 必須 ★1 | `tags.view_tag` ★1 | 全員 ★1 | **v1.6 新規**（rev12.3 §5.1 No.20） |
| 64 | `/tags/create/` | GET / POST | TagCreateView | `tags:tag_create` | 必須 ★1 | `tags.create_tag` ★1 | admin / sales（tag_admin / tag_editor） ★1 | **v1.6 新規**（rev12.3 §5.1 No.21）。タグ作成時は **`category` 必須 FK**（rev12 で `group_id` 削除→TagCategory FK 必須化、rev12.3 §4.9A）。TagCategory が 0 件だと Tag を作成できない（rev12.3 §4.9 初期データ方針：空スタート・運用者が手動作成） |
| 65 | `/tags/<uuid:pk>/` | GET | TagDetailView | `tags:tag_detail` | 必須 ★1 | `tags.view_tag` ★1 | 全員 ★1 | **v1.6 新規**（rev12.3 §5.1 No.22） |
| 66 | `/tags/<uuid:pk>/update/` | GET / POST | TagUpdateView | `tags:tag_update` | 必須 ★1 | `tags.edit_tag` ★1 | admin / sales ★1 | **v1.6 新規**（rev12.3 §5.1 No.23） |
| 67 | `/tags/<uuid:pk>/delete/` | POST | TagDeleteView | `tags:tag_delete` | 必須 ★1 | `tags.delete_tag` ★1 | admin ★1 | **v1.6 新規**（rev12.3 §5.1 No.24）。論理削除 |
| 68 | `/tags/assign/` | POST | TagAssignView | `tags:tag_assign` | 必須 ★1 | `tags.assign_tag` ★1 | admin / sales ★1 | **v1.6 新規**（rev12.3 §5.1 No.25）。AJAX 用、Person 詳細画面から呼ぶ。**【マージ時はサービス関数経由で survive 側へ単純コピー、rev12.3 §9.4.5 / 改訂4 / 単純コピー確定】** |
| 69 | `/tags/unassign/` | POST | TagUnassignView | `tags:tag_unassign` | 必須 ★1 | `tags.assign_tag` ★1 | admin / sales ★1 | **v1.6 新規**（rev12.3 §5.1 No.26）。AJAX 用 |

### mailings アプリ（リスト＝凍結スナップショット）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 70 | `/mailing-lists/` | GET | MailingListListView | `mailings:list_list` | 必須 ★1 | ★2（明示なし、`mailings.view_campaign` 想定） | 全員 ★2 | **v1.6 新規**（rev12.3 §5.1 No.27）。リスト一覧。rev12 でリスト凍結方式確定（rev12.3 §11.3 / §4.11）。Permission は rev12.3 §14 で `mailings:list_*` 系の codename が未明示、★2 候補 |
| 71 | `/mailing-lists/create/` | GET / POST | MailingListCreateView | `mailings:list_create` | 必須 ★1 | ★2（明示なし、`mailings.create_campaign` 想定） | admin / sales ★2 | **v1.6 新規**（rev12.3 §5.1 No.28）。【**【rev12 で全面改訂】 リスト作成画面の本体は別 URL（No.81 ★2 候補、§6.2.4 リスト作成画面）**】 本 No.28 は最小のリスト作成エンドポイント、画面の具体はコード君判断 |
| 72 | `/mailing-lists/<uuid:pk>/` | GET | MailingListDetailView | `mailings:list_detail` | 必須 ★1 | ★2（明示なし） | 全員 ★2 | **v1.6 新規**（rev12.3 §5.1 No.29）。リスト詳細（凍結メンバー表示）。**`extraction_snapshot` を使った再抽出機能は §19 論点 15 確定待ち・Phase 1 スコープ外**（rev12.3 §11.4.3.1 段階線引き、Phase 1 では `extraction_snapshot` はフィールド定義のみ） |
| 73 | `/mailing-lists/<uuid:pk>/update/` | GET / POST | MailingListUpdateView | `mailings:list_update` | 必須 ★1 | ★2（明示なし） | admin / sales ★2 | **v1.6 新規**（rev12.3 §5.1 No.30）。リスト名・説明等の編集 |
| 74 | `/mailing-lists/<uuid:pk>/delete/` | POST | MailingListDeleteView | `mailings:list_delete` | 必須 ★1 | ★2（明示なし） | admin ★2 | **v1.6 新規**（rev12.3 §5.1 No.31）。論理削除（リストは論理削除運用、`Campaign.mailing_list` PROTECT のため物理削除はブロックされる、rev12.3 §4.11 / §11.3.4） |
| 75 | `/mailing-lists/<uuid:pk>/add-member/` | POST | MailingListAddMemberView | `mailings:list_add_member` | 必須 ★1 | ★2（明示なし） | admin / sales ★2 | **v1.6 新規**（rev12.3 §5.1 No.32）。AJAX。**【マージ時は MailingListMember は付け替えない＝凍結スナップショット、rev12 改訂5 で §601 行矛盾是正済み、rev12.3 §4.12 / §9.4.1 / §11.7.2】** |
| 76 | `/mailing-lists/<uuid:pk>/remove-member/` | POST | MailingListRemoveMemberView | `mailings:list_remove_member` | 必須 ★1 | ★2（明示なし） | admin / sales ★2 | **v1.6 新規**（rev12.3 §5.1 No.33）。AJAX |

### 受信者向け公開 URL（外部、ログイン不要）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 77 | `/t/<str:token>/` | GET / HEAD | TrackingRedirectView | `mailings:tracking_redirect` | 不要 ★1 | なし（トークン保持者のみ） ★1 | 外部 ★1 | **v1.6 新規**（rev12.3 §5.1 No.34）。クリックトラッキング中継、極短 URL。**本番送信 mode のメールにのみトークン付きで埋まる**（テスト配信・プレビューでは `{% tracked_link %}` が素リンクに変換され `/t/` 経由を通らない、rev10 補足・rev12.3 §7.4.6.4）。ボット判定（5 段階）、IP マスク保存（rev12.3 §8） |
| 78 | `/u/<str:token>/` | GET | UnsubscribePageView | `mailings:unsubscribe_page` | 不要 ★1 | なし（トークン保持者のみ） ★1 | 外部 ★1 | **v1.6 新規**（rev12.3 §5.1 No.35）。配信停止確認画面。**トークン有効期限なし（rev10 確定）**：古いメールからの配信停止は正常利用、特電法のオプトアウト確実提供（rev12.3 §4.5A.0）。**無効トークン時の遷移**（`/u/` リダイレクトか404か）はコード君の実装時判断（rev12.3 §4.5A.2.1） |
| 79 | `/u/<str:token>/confirm/` | POST | UnsubscribeConfirmView | `mailings:unsubscribe_confirm` | 不要 ★1 | なし（トークン保持者のみ） ★1 | 外部 ★1 | **v1.6 新規**（rev12.3 §5.1 No.36）。配信停止実行（Unsubscribe または SuppressedEmail に登録、UnsubscribeLink.unsubscribed_at 更新）。**配信停止意思は同一人物ユニットに伝播**（`unsubscribe_person()` サービス関数経由、rev12.3 §9.5） |
| 80 | `/u/<str:token>/done/` | GET | UnsubscribeDoneView | `mailings:unsubscribe_done` | 不要 ★1 | なし | 外部 ★1 | **v1.6 新規**（rev12.3 §5.1 No.37）。配信停止完了画面 |
| 81 | `/u/` | GET | （トークン無し案内 View、具体名はコード君判断） | （★2、`mailings:unsubscribe_page_tokenless` 等が自然だがコード君判断） | 不要 ★1 | なし ★1 | 外部 ★1 | **v1.6 新規・rev10 で新設**（rev12.3 §5.1 No.41、§4.5A.2.1）。**トークン無し独立ルート、案内表示のみ**。テスト・プレビュー由来のトークン無しアクセス、または本番メールの `/u/<token>/` 無効トークン時のフォールバック。**メールアドレス手入力 UI は設けない**（第三者が他人のメアドを入力して勝手に配信停止する悪用を構造的に断つ、本書 §4.5A.2.1）。リンク不調時は Settings の `unsubscribe_contact` への誘導で対応。具体的 URL name はコード君判断 ★2 |

### mailings アプリ（プレビュー）

| No. | ルート | メソッド | View 名 | URL name | ログイン | Permission | ロール | 備考 |
|---|---|---|---|---|---|---|---|---|
| 82 | `/mailings/campaigns/<uuid:pk>/preview/<person_id>/` | GET | プレビュー View（具体名はコード君判断） | （★2、`mailings:campaign_preview` 等が自然だがコード君判断） | 必須 ★1 | `mailings.view_campaign` + 所有者判定 ★2 | 本人 / admin ★2 | **v1.6 新規・rev11 で確定**（rev12.3 §5.1 No.42、§7.7.1）。**Ajax モーダル表示**、`EmailContext.prepare(mode=EmailMode.PREVIEW)` を直接呼ぶ、3 処理単位を通らない。`<uuid:pk>`=キャンペーン、`<person_id>`=プレビュー対象 Person（§7.7 テスト配信と同思想の代表 Person、ステップ画面側で決定）。送信も配信処理も行わない読み取り専用表示。記法残存（`{{...}}`）のみ警告色（送信用 prepare 出力をそのまま使い、表示層が拾って色付け、rev12.3 §7.7.1.3）。Permission codename・URL name の具体はコード君判断 ★2 |

### v1.6 新画面（rev12 新設、URL はコード君判断＝★2 候補・本表で確定しない）

rev12 で §6.2 に新設された 3 画面の URL は、rev12.3 §5.1 表で**個別エントリ未登録**（「URL はコード君判断・§5.1 別途追加」と注記、rev12.3 §6.1 画面一覧表参照）。本依頼書 §0 の方針に従い、**URL は本表で確定しない（★2 候補マークのみ）**。

| No. | 画面 | 仕様書節 | ルート | View 名 | URL name | ログイン | Permission | ロール | 備考（★2 候補） |
|---|---|---|---|---|---|---|---|---|---|
| 83 | リスト作成画面（カテゴリ別タグ選択＋検索条件＋凍結保存） | rev12.3 §6.2.4 | ★2 候補（コード君判断、リスト管理 No.70〜76 配下サブパスの想定） | ★2 候補 | ★2 候補 | 必須 ★1 | ★2（明示なし、`mailings.create_campaign` または `tags.assign_tag` 系想定） | admin / sales ★2 | **rev12 新設、URL 未確定**。本依頼書スコープ外、★2 サポート担当が後段で埋めてたんたん確認に回す。検索条件具体項目は **§19 論点 14**（レビュー対象）、カテゴリ単一/複数選択属性は **§19 論点 17**、空リスト/全員リストの扱いは **§19 論点 18**（rev12.3 §11.4.2.1 段階線引き／§11.4.3.1）。**画面内部仕様は残論点だが URL は本表で確定しない（仕様書未明示のため）** |
| 84 | タグカテゴリ管理画面 | rev12.3 §6.2.5 | ★2 候補（コード君判断） | ★2 候補 | ★2 候補 | 必須 ★1 | `mailings.manage_tag_category`（仮称、rev12.3 §6.2.5 末尾で「仮称」と明記）★2 | admin ★2 | **rev12 新設、URL 未確定**。カテゴリの作成・編集・並び替え・論理削除。**Permission codename は rev12.3 §6.2.5 で「仮称」と明示**＝★2 候補。本依頼書スコープ外、★2 で埋める |
| 85 | 検索結果一括タグ付け画面 | rev12.3 §6.2.6 | ★2 候補（コード君判断） | ★2 候補 | ★2 候補 | 必須 ★1 | `tags.assign_tag` ★2 | admin / sales ★2 | **rev12 新設、URL 未確定**。Person 検索 → 結果一覧で複数選択 → 一括 TagAssignment 作成（bulk_create）。rev11 残論点だった「タグ・リストのメンバー投入導線」を解消（rev12.3 §6.2.6） |

---

## v1.6 で追加する Permission / Group / Role（rev12.3 §14）

### Permission（mailings アプリ）

| Permission codename | content_type | 説明 | 仕様書記載 |
|---|---|---|---|
| `mailings.send_campaign` | mailings.campaign | メール配信を実行できる | ★1（rev12.3 §14.1.1） |
| `mailings.create_campaign` | mailings.campaign | 配信キャンペーンを作成できる | ★1（rev12.3 §14.1.1） |
| `mailings.view_campaign` | mailings.campaign | 配信キャンペーン・配信レポートを閲覧できる（自分のもの） | ★1（rev12.3 §14.1.1） |
| `mailings.view_all_campaigns` | mailings.campaign | 他人の配信キャンペーン・配信レポートを閲覧できる | ★1（rev12.3 §14.1.1） |
| `mailings.manage_template` | mailings.emailtemplate | メールテンプレートを作成・編集・削除できる | ★1（rev12.3 §14.1.1） |
| `mailings.manage_suppressed_email` | mailings.suppressedemail | 配信拒否リストを編集できる（解除・手動追加） | ★1（rev12.3 §14.1.1） |
| `mailings.export_report` | mailings.campaign | 配信レポート CSV をダウンロードできる | ★1（rev12.3 §14.1.1） |

### Permission（tags アプリ）

| Permission codename | content_type | 説明 | 仕様書記載 |
|---|---|---|---|
| `tags.create_tag` | tags.tag | タグを作成できる | ★1（rev12.3 §14.1.2） |
| `tags.edit_tag` | tags.tag | タグを編集できる | ★1（rev12.3 §14.1.2） |
| `tags.delete_tag` | tags.tag | タグを削除できる（論理削除） | ★1（rev12.3 §14.1.2） |
| `tags.assign_tag` | tags.tag | コンタクト / Person にタグを付与・解除できる | ★1（rev12.3 §14.1.2） |
| `tags.view_tag` | tags.tag | タグの一覧・詳細を閲覧できる | ★1（rev12.3 §14.1.2） |

### Group（v1.6 で追加）

| Group 名 | 含まれる Permission | 含まれる Role | 仕様書記載 |
|---|---|---|---|
| `email_admin` | 全 `mailings.*` | `admin` | ★1（rev12.3 §14.2.1） |
| `email_editor` | `mailings.send_campaign`, `mailings.create_campaign`, `mailings.view_campaign`, `mailings.manage_template`, `mailings.export_report` | `sales` | ★1（rev12.3 §14.2.1） |
| `email_viewer` | `mailings.view_campaign` のみ | `viewer` | ★1（rev12.3 §14.2.1） |
| `tag_admin` | 全 `tags.*` | `admin` | ★1（rev12.3 §14.2.2） |
| `tag_editor` | `tags.create_tag`, `tags.assign_tag`, `tags.view_tag` | `sales` | ★1（rev12.3 §14.2.2） |
| `tag_viewer` | `tags.view_tag` のみ | `viewer` | ★1（rev12.3 §14.2.2） |

### Role の default_groups 拡張（rev12.3 §14.3）

| Role | v1.5.0 確定済み default_groups | v1.6 で追加 |
|---|---|---|
| `admin` | `person_admin`, `user_admin`, `card_admin` | `email_admin`, `tag_admin` |
| `sales` | `person_editor`, `card_editor` | `email_editor`, `tag_editor` |
| `viewer` | `person_viewer`, `card_viewer` | `email_viewer`, `tag_viewer` |

実装は v1.5.0 §8 の PermissionGroup プリセット運用パターンに従う。`accounts/services.py` の `apply_role(user, role_name)` 関数が、ロール変更時に新旧 Group を差し替える。

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
| 37 | `accounts:profile` | **Phase 8**（UI 統合） | プロフィール画面新設（仕様書未明示・実装で追加） |
| 38 | `accounts:link_user_person_confirm` | **Phase 8 #4**（紐付け確認画面新設） | 仕様書未明示・実装で追加（§12.7 に追記） |
| 39 | `accounts:start_link_flow` | **Phase 8 #4**（Person 探索フロー開始） | 仕様書未明示・実装で追加（§12.7 に追記） |
| 40 | Django Admin | **Phase 2-3**（CustomUserAdmin 実装） | §4.1 |

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
| 2026-05-18 | **v1.6 メール配信・クリックトラッキング機能の URL 統合**：仕様書 rev12.3 §5.1（URL 一覧表 42 番）／§6.2（画面設計、リスト作成・タグカテゴリ管理・検索結果一括タグ付け）／§14（認可設計、Permission・Group・Role）を統合。v1.6 で追加した URL は No.41〜82 として登録（既存 No.1〜40 の番号は維持、衝突なし）。rev12 新設の 3 画面（リスト作成画面・タグカテゴリ管理画面・検索結果一括タグ付け画面）は URL がコード君判断で仕様書未明示のため、No.83〜85 として **★2 候補**マークのみ（本表では確定しない、サポート担当が後段で埋めてたんたん確認に回す）。受信者向け公開 URL（クリックトラッキング中継 `/t/<token>/`、配信停止 `/u/<token>/` 系 4 番、トークン無し独立ルート `/u/`）は ★1 で明示。タイトルを「v1.4.2 + v1.5.0 + v1.6 統合版」に更新。v1.4.2 + v1.5.0 分（No.1〜40）の URL 定義は**意味内容を 1 文字も変更していない**。 |
| 2026-05-16 | **Phase 8 / Phase 8 #4 反映**：(1) 新規 URL を追加：No.37 `accounts:profile`（Phase 8 で新設したプロフィール画面）、No.38 `accounts:link_user_person_confirm`（Phase 8 #4 で新設した紐付け／解除確認画面、`action=link/unlink` 切替で 1 View 兼用）、No.39 `accounts:start_link_flow`（Phase 8 #4 で新設した Person 探索フロー開始 View、`?email=...&searched=1&status=active` 付きで `persons:person_list` へリダイレクト）。(2) 既存 URL の備考更新：No.8 PersonDetailView（`person_detail_orphan.html` に紐付け／解除セクション追加）、No.11 ContactDetailView（操作ボタン列に紐付け導線追加）、No.36 RetireUserView（トップバーに「ユーザ管理」リンク追加）、No.40 Django Admin（旧 No.37 から繰下げ、トップバーユーザメニューに「管理画面」リンク追加）。(3) 工程表に Phase 8 / Phase 8 #4 行を追加。 |
