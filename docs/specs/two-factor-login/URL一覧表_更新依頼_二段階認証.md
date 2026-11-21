# URL一覧表 更新依頼（二段階認証・ログインセキュリティ強化）

- 版: v1.0.1（v1.0 の備考欄の誤りを修正）
- 対象機能: 二段階認証・ログインセキュリティ強化
- 参照仕様書: `docs/specs/two-factor-login/FreeGroup2_ログインセキュリティ強化_仕様書_v1_3_1.md`（§10）
- 実装参照元: `accounts/urls.py`（app_name: `accounts`、ルートプレフィックス: `/accounts/`）
- 対象バージョン目安: `docs/specs/v1_6_0/URL一覧表_v1_6_rev20.md` への反映用

### 改訂履歴

- v1.0: 新規
- v1.0.1: 備考欄の修正

---

## 1. 新規・更新 URL 一覧（`/accounts/` 配下）

| # | 画面名 / 機能 | パス（`/accounts/` 起点） | URL名 | メソッド | 認証・権限 | ビュー | テンプレート | 備考 |
|---|---|---|---|---|---|---|---|---|
| 1 | ログイン | `login/` | `accounts:login` | GET / POST | 不要 | `accounts.views.LoginView` | `accounts/login.html` | 二段階認証・ロック判定組み込み（更新） |
| 2 | 二段階認証コード入力 | `login/verify/` | `accounts:login_verify` | GET / POST | 仮認証のみ | `accounts.views.LoginVerifyView` | `accounts/login_verify.html` | 認証アプリコード入力、リカバリーコード切替、この端末を信頼するチェック（新規） |
| 3 | 二段階認証設定（端末一覧含む） | `two-factor/` | `accounts:two_factor` | GET | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorSettingsView` | `accounts/two_factor_settings.html` | 認証アプリ状態・リカバリーコード残数・ログイン端末一覧（信頼状態と取り消し／削除）を表示（新規） |
| 4 | 認証アプリ登録・再登録 | `two-factor/setup/` | `accounts:two_factor_setup` | GET / POST | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorSetupView` | `accounts/two_factor_setup.html` | 初回登録と再登録。再登録の開始には、現在のコード（認証アプリまたはリカバリーコード）が必要。確認コードが通るまで旧登録は有効（新規） |
| 5 | リカバリーコードの表示／再発行 | `two-factor/recovery-codes/` | `accounts:two_factor_recovery_codes` | GET / POST | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorRecoveryCodesView` | `accounts/two_factor_recovery_codes.html` | 登録・再登録の確定直後に10個を一度だけ表示。登録済みのユーザーは、現在のコードを確認したうえで再発行できる（新規） |
| 6 | 二段階認証の無効化 | `two-factor/disable/` | `accounts:two_factor_disable` | GET / POST | ログイン必須（本人） | `accounts.views_two_factor.TwoFactorDisableView` | `accounts/two_factor_disable.html` | 強制レベルにより必須対象のユーザーは無効化できない（403。コードの検証も失敗回数の加算もしない）。無効化には現在のコードが必要（新規） |
| 7 | 信頼済み端末の取り消し・削除 | `devices/<int:device_id>/revoke/` | `accounts:login_device_revoke` | POST | ログイン必須（本人） | `accounts.views_two_factor.LoginDeviceRevokeView` | なし（リダイレクト） | `accounts:two_factor` へリダイレクト（新規） |
| 8 | 管理者: 二段階認証リセット | `users/<int:user_id>/two-factor/reset/` | `accounts:two_factor_reset` | GET / POST | `accounts.reset_two_factor` | `accounts.views_login_security.TwoFactorResetView` | `accounts/two_factor_reset.html` | 自分自身は対象にできない（400）。理由メモは任意（500文字まで）。確認画面に、FG2 の外での本人確認と、本人がすぐに登録操作を行える状態での実行の注意を表示（新規） |
| 9 | 管理者: アカウントロック解除 | `users/<int:user_id>/unlock/` | `accounts:account_unlock` | POST | `accounts.reset_two_factor` | `accounts.views_login_security.AccountUnlockView` | なし（リダイレクト） | `accounts:user_detail` へリダイレクト（新規） |
| 10 | 管理者: セキュリティ設定 | `security/` | `accounts:security_setting` | GET / POST | `accounts.manage_security_setting` | `accounts.views_login_security.SecuritySettingView` | `accounts/security_setting.html` | 8項目（強制レベル・ログイン通知レベル・ロック失敗回数・ロック時間・IP遮断失敗回数・信頼期間・ロック発生の管理者通知・IP遮断の管理者通知）を設定。弱める変更は管理者全員にメール通知（新規） |
| 11 | 管理者: ログイン試行記録一覧 | `login-attempts/` | `accounts:login_attempt_list` | GET | `accounts.view_loginattempt` | `accounts.views_login_security.LoginAttemptListView` | `accounts/login_attempt_list.html` | 結果・入力ユーザー名・IP・期間（JST）で絞り込み表示（20件/ページ）。入力ユーザー名欄に取り違えたパスワードが残り得る旨を画面に注記（新規） |

> **実装メモ**
> - `two_factor_reset` および `account_unlock` のパスパラメータは `<int:user_id>`、`login_device_revoke` のパスパラメータは `<int:device_id>` です。
> - #7（信頼済み端末の取り消し・削除）および #9（アカウントロック解除）は POST 専用エンドポイントであり、専用テンプレートを持たずリダイレクト応答を行います。

---

## 2. 既存画面・UI への追加箇所

| # | 画面名 / コンポーネント | パス（`/accounts/` 起点） | URL名 | 権限 | 追加内容 | テンプレート |
|---|---|---|---|---|---|---|
| 1 | プロフィール | `profile/` | `accounts:profile` | ログイン必須（本人） | 「二段階認証」状態表示（登録済み / 未登録・必須バッジ）・設定画面（`accounts:two_factor`）への導線、および「最近のログイン」（本人の成功記録 最大5件） | `accounts/profile.html` |
| 2 | ユーザー一覧 | `users/` | `accounts:user_list` | `accounts.retire_user` | 上部に有効管理者数警告（1名以下の場合）、テーブルに「セキュリティ」列（二段階認証 `登録済み`/`未登録`・`ロック中`・`通知先なし` バッジ） | `accounts/user_list.html` |
| 3 | ユーザー詳細 | `users/<int:user_id>/` | `accounts:user_detail` | `accounts.retire_user` | ユーザー情報カードに「二段階認証」「ロック状態（JST `Y-m-d H:i まで`）」を表示、ヘッダーに `accounts.reset_two_factor` 保持者向けの「二段階認証をリセット」（自分自身は非表示）および「ロックを解除」（ロック中のみ表示）を追加 | `accounts/user_detail.html` |
| 4 | アカウント管理サイドバー | （共通パーツ） | — | 各項目の権限に従う | 末尾に「セキュリティ設定」（`accounts.manage_security_setting`）および「ログイン試行記録」（`accounts.view_loginattempt`）を追加 | `accounts/_sidebar.html` |
