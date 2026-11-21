# URL一覧表 更新依頼（二段階認証・ログインセキュリティ強化）

- 対象機能: 二段階認証・ログインセキュリティ強化
- 参照仕様書: `docs/specs/two-factor-login/FreeGroup2_ログインセキュリティ強化_仕様書_v1_3_1.md`（§10）
- 実装参照元: `accounts/urls.py`（app_name: `accounts`、ルートプレフィックス: `/accounts/`）
- 対象バージョン目安: `docs/specs/v1_6_0/URL一覧表_v1_6_rev20.md` への反映用

---

## 1. 新規・更新 URL 一覧（`/accounts/` 配下）

| # | 画面名 / 機能 | パス（`/accounts/` 起点） | URL名 | メソッド | 認証・権限 | ビュー | テンプレート | 備考 |
|---|---|---|---|---|---|---|---|---|
| 1 | ログイン | `login/` | `accounts:login` | GET / POST | 不要 | `accounts.views.LoginView` | `accounts/login.html` | 二段階認証・ロック判定組み込み（更新） |
| 2 | 二段階認証コード入力 | `login/verify/` | `accounts:login_verify` | GET / POST | 仮認証のみ | `accounts.views.LoginVerifyView` | `accounts/login_verify.html` | 認証アプリコード・リカバリーコード・信頼済み端末チェック（新規） |
| 3 | 二段階認証設定（端末一覧含む） | `two-factor/` | `accounts:two_factor` | GET | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorSettingsView` | `accounts/two_factor_settings.html` | 認証アプリ状態・リカバリーコード残数・信頼済み端末一覧を表示（新規） |
| 4 | 認証アプリ登録・再登録 | `two-factor/setup/` | `accounts:two_factor_setup` | GET / POST | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorSetupView` | `accounts/two_factor_setup.html` | 初回登録・再登録・確認コード検証（新規） |
| 5 | リカバリーコード再発行 | `two-factor/recovery-codes/` | `accounts:two_factor_recovery_codes` | GET / POST | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorRecoveryCodesView` | `accounts/two_factor_recovery_codes.html` | 現在のコード確認後に10個を再発行し1度だけ表示（新規） |
| 6 | 二段階認証の無効化 | `two-factor/disable/` | `accounts:two_factor_disable` | GET / POST | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorDisableView` | `accounts/two_factor_disable.html` | `TWO_FACTOR_REQUIRED` の対象ユーザーは実行不可（新規） |
| 7 | 信頼済み端末の取り消し・削除 | `devices/<int:device_id>/revoke/` | `accounts:login_device_revoke` | POST | ログイン必須（本人） | `accounts.views_two_factor.LoginDeviceRevokeView` | なし（リダイレクト） | `accounts:two_factor` へリダイレクト（新規） |
| 8 | 管理者: 二段階認証リセット | `users/<int:user_id>/two-factor/reset/` | `accounts:two_factor_reset` | GET / POST | `accounts.reset_two_factor` | `accounts.views_login_security.TwoFactorResetView` | `accounts/two_factor_reset.html` | 対象ユーザーを制限・自分自身は不可・理由メモ必須（新規） |
| 9 | 管理者: アカウントロック解除 | `users/<int:user_id>/unlock/` | `accounts:account_unlock` | POST | `accounts.reset_two_factor` | `accounts.views_login_security.AccountUnlockView` | なし（リダイレクト） | `accounts:user_detail` へリダイレクト（新規） |
| 10 | 管理者: セキュリティ設定 | `security/` | `accounts:security_setting` | GET / POST | `accounts.manage_security_setting` | `accounts.views_login_security.SecuritySettingView` | `accounts/security_setting.html` | パスワード・ロック・二段階認証強制方式等の設定（新規） |
| 11 | 管理者: ログイン試行記録一覧 | `login-attempts/` | `accounts:login_attempt_list` | GET | `accounts.view_loginattempt` | `accounts.views_login_security.LoginAttemptListView` | `accounts/login_attempt_list.html` | 結果・期間・ユーザー・IP 等による絞り込み表示（新規） |

> **仕様書 §10 との差分メモ**
> - `two_factor_reset` および `account_unlock` のパスパラメータは、`accounts/urls.py` の既存ユーザー詳細系ルート（`users/<int:user_id>/...`）に合わせて `<int:user_id>` としています。
> - `login_device_revoke` のパスパラメータは `<int:device_id>` です。
> - #7（信頼済み端末の取り消し・削除）および #9（アカウントロック解除）は POST 専用エンドポイントであり、専用テンプレートを持たずリダイレクト応答を行います。

---

## 2. 既存画面・UI への追加箇所

| # | 画面名 / コンポーネント | パス（`/accounts/` 起点） | URL名 | 権限 | 追加内容 | テンプレート |
|---|---|---|---|---|---|---|
| 1 | プロフィール | `profile/` | `accounts:profile` | ログイン必須（本人） | 「二段階認証」状態表示（登録済み / 未登録・必須バッジ）・設定画面（`accounts:two_factor`）への導線、および「最近のログイン」（本人の成功記録 最大5件） | `accounts/profile.html` |
| 2 | ユーザー一覧 | `users/` | `accounts:user_list` | `accounts.retire_user` | 上部に有効管理者数警告（1名以下の場合）、テーブルに「セキュリティ」列（二段階認証 `登録済み`/`未登録`・`ロック中`・`通知先なし` バッジ） | `accounts/user_list.html` |
| 3 | ユーザー詳細 | `users/<int:user_id>/` | `accounts:user_detail` | `accounts.retire_user` | ユーザー情報カードに「二段階認証」「ロック状態（JST `Y-m-d H:i まで`）」を表示、ヘッダーに `accounts.reset_two_factor` 保持者向けの「二段階認証をリセット」（自分自身は非表示）および「ロックを解除」（ロック中のみ表示）を追加 | `accounts/user_detail.html` |
| 4 | アカウント管理サイドバー | （共通パーツ） | — | 各項目の権限に従う | 末尾に「セキュリティ設定」（`accounts.manage_security_setting`）および「ログイン試行記録」（`accounts.view_loginattempt`）を追加 | `accounts/_sidebar.html` |
