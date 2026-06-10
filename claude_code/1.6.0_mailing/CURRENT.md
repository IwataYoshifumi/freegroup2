# CURRENT - 1.6.0_mailing

**フェーズ：** 1.6.0_mailing
**ブランチ：** `feature/v1.6-tags-template`
**最終更新：** 2026-05-30
**ステータス：** 進行中

---

## このファイルの位置付け

このファイルは v1.6 メール配信・クリックトラッキング機能の「いま」を 1 枚で把握するための文書。セッション開始時に AI（コード君 A/B、サブエージェント、オーパス君）が最初に読む。

- 上半分（§1〜§4）：**ダッシュボード**（全体像の俯瞰）
- 下半分（§5〜§7）：**作業ログ**（直近の作業状況）

---

# ─── ダッシュボード ───

## 1. フェーズ概要

- **目的：** メール配信・クリックトラッキング機能の実装。Sansan 元代理店経験を踏まえ、リスト凍結方式・配信停止の同一人物ユニット伝播・配信フィルタ ON/OFF 制御を含む実用的なメルマガ基盤を構築する。
- **対象仕様書：** `仕様書_v1_6_メール配信_クリックトラッキング_ドラフト_rev18.md`（rev18、2026-05-29 EmailTemplate を Campaign と 1:1 運用に確定）
- **想定期間：** 2026-05 中旬 〜 v1.6.2 リリースまで

---

## 2. スコープ

### やること

- タグ機能・リスト機能（仕様書 Phase 1）
- Campaign / DeliveryHistory / TrackingLink / ClickLog / Unsubscribe / SuppressedEmail（仕様書 Phase 2）
- テンプレートタグ + 差し込み変数展開 + メール送信抽象化レイヤー（仕様書 Phase 3）
- クリック中継ビュー + ボット判定 + IP マスキング（仕様書 Phase 4）
- 配信停止リンク + バウンス処理（仕様書 Phase 5）
- 配信レポート画面 + CSV ダウンロード（仕様書 Phase 6）
- 認可基盤接続（仕様書 Phase 7）
- ドメイン認証（DKIM 署名 + 設定ガイド + 診断ツール、仕様書 Phase 8）
- 同一人物ユニット系サービス関数（仕様書 Phase 9）
- 全体動作確認 + main マージ（仕様書 Phase 10）

### やらないこと（明示）

- **EmailTemplate の全社共有ライブラリ機能**（rev17 で撤回、Campaign と 1:1 運用に確定）
- **備考プリセット機能（仕様書 §4.5.3）**（rev8 で v1.7+ 見送り確定、実装しない）
- **`extraction_snapshot` の具体スキーマと再抽出 UI**（rev12.4 で v1.7+ 送り確定、フィールドは空 or NULL のまま据え置き）
- **マージ時の FK 付け替え**（A-1 設計、TrackingLink / DeliveryHistory / Unsubscribe いずれも付け替えない）
- **暗号化 DKIM 秘密鍵保存**（v1.6 は平文保存、v1.7+ 要望次第）
- **キャンペーン引き継ぎ機構**（退職時は閲覧運用のみ、`view_all_campaigns` 権限で対応）
- **Phase 1c 単独の main マージ**（メルマガ機能が全部できてから main へ squash マージ、たんたん確定）

---

## 3. マイルストーンと進捗

実装の進捗単位（α/β/(a2)/(b)/(c)/(d)）を主軸にし、仕様書 Phase との対応を備考欄に記載する。

| # | マイルストーン | 仕様書 Phase 対応 | ステータス | 完了日 / HEAD |
| --- | --- | --- | --- | --- |
| 1 | Phase 1c-α 個別追加・個別削除 UI | Phase 1（一部） | ✅ 完了 | 2026-05-26 / `50d5335` → `ae12881` |
| 2 | Phase 1c-β-1 拡張集合演算サービス + preview-v2 API | Phase 1（一部） | ✅ 完了 | 2026-05-28 / `eba6f60` |
| 3 | Phase 1c-β-2a 新規作成ウィザード骨格 | Phase 1（一部） | ✅ 完了 | 2026-05-28 / `0e20595` |
| 4 | Phase 1c-β-2b タグ選択 UI JS（create mode） | Phase 1（一部） | ✅ 完了 | 2026-05-28 / `b8313b0` |
| 5 | Phase 1c-β-3a タグで追加 | Phase 1（一部） | ✅ 完了 | 2026-05-28 / `4050233` |
| 6 | Phase 1c-β-3b タグで除外 | Phase 1（一部） | ✅ 完了 | 2026-05-28 / `adcf558`（764 passed） |
| 7 | (a2) Campaign.template OneToOneField 化 | Phase 2（一部） | ✅ 完了 | 2026-05-29 / `88610d1`（981 passed） |
| 8 | (c) EmailTemplate 編集画面＋プレビュー View | Phase 2 + Phase 3（一部） | ✅ 完了 | 2026-05-29 / `690f134`（997 passed） |
| 9 | (b) Campaign UI 全般 | Phase 2 | ✅ 完了 | 2026-05-30 / `ef8ade4`（1035 passed） |
| 10 | (d) Phase 7 認可一括ガード | Phase 7 | ⬜ 未着手 | — |
| 11 | DeliveryHistory / TrackingLink / ClickLog 等 | Phase 2 残り + Phase 4 | ⬜ 未着手 | — |
| 12 | テンプレートタグ + 差し込み変数 + 送信抽象化 | Phase 3 | ⬜ 未着手 | — |
| 13 | 配信停止リンク + バウンス処理 | Phase 5 | ⬜ 未着手 | — |
| 14 | 配信レポート画面 + CSV | Phase 6 | ⬜ 未着手 | — |
| 15 | ドメイン認証（DKIM） | Phase 8 | ⬜ 未着手 | — |
| 16 | 同一人物ユニット系サービス関数 | Phase 9 | ⬜ 未着手 | — |
| 17 | 全体動作確認 + main マージ + v1.6.2 タグ付け | Phase 10 | ⬜ 未着手 | — |

**注：** 仕様書 Phase 1〜10 はあくまで仕様書上の分割。実装は rev17（2026-05-29 EmailTemplate 1:1 確定）以降、Campaign 周辺（Phase 2 系）と EmailTemplate UI（Phase 2 + 3 接続）を (a2)/(c)/(b) として並走実装している。Phase 7 認可は (b)(c) で残した TODO を (d) で一括処理する方式（案 B）。

---

## 4. 未解決の論点

現時点で結論が出ていない、または保留中の事項。

- **論点 21：凍結発動時の競合制御**（仕様書 §19）
  - `scheduled` → `sending` 遷移と同時にリストの `members_frozen_at` をセットする際の競合制御方針
  - 保留理由：配信実行サービス（Phase 2）実装時に確定する想定
- **論点 22：配信失敗からの draft 復帰時の凍結扱い**（仕様書 §19）
  - 配信失敗 → `draft` に戻る場合、リストの凍結を解除するか維持するか
  - 保留理由：配信実行サービス（Phase 2）実装時に確定する想定
- **論点 23：EmailTemplate ライブラリ機能の v1.7+ 復活余地**（仕様書 §19、rev17 追加）
  - rev17 で全社共有テンプレートライブラリ機能を撤回、Campaign 1:1 運用に確定。将来 v1.7+ で復活する余地は残す
  - 保留理由：v1.6 スコープ外、v1.7+ で要望に応じて再検討
- **(b) で気になった点（軽微）**：
  - `_campaign_action_confirm.html` 未使用ファイルの扱い
  - `CreateCampaignWithTemplateTests` の atomic 性 2 観点

---

# ─── 作業ログ ───

## 5. 今やっていること

- **担当：** 待機中（コード君 A は (b) 完了で一旦休止、たんたんの (d) 着手指示待ち）
- **直近の状態：** (b) Campaign UI 全般のコミット&プッシュ完了直後、`feature/v1.6-tags-template` HEAD `ef8ade4`、working tree clean

---

## 6. 次の一手

1. **(d) Phase 7 認可一括ガード** に着手
   - (b)(c) で残した `TODO(Phase 7): mailings.<perm> + 所有者判定` の一括解消
   - docstring / help_text の rev16〜rev18 反映漏れ追従（`members_frozen_at`、`Campaign.template` 周辺、§14.4.1 退職運用の旧記述）
2. (b)(c)(d) を通じた人間目視チェック（ブラウザ実機）
3. 仕様書改訂をジット君に依頼
   - `is_archived` 追加の §4.2 反映
   - 「基本情報を編集」ボタン明記の §6.1 No.8 反映
4. (b) で気になった点の整理
5. (d) 完了後、Phase 2 残り（DeliveryHistory / TrackingLink / ClickLog 等）へ進む

---

## 7. ブロッカー・要確認事項

- **X サーバー直送経路の `.env` 読み込み確認**
  - 内容：メール送信先の接続情報がハードコードされていないか、`.env` から読む実装になっているか
  - 待っている相手：コード君 A への要確認
  - 影響：X サーバー ⇄ SendGrid 切替時に `.env` 差し替えだけで済むかどうか
- **main への squash マージタイミング**
  - 内容：メルマガ機能が全部できてからマージ（Phase 1c 単独ではマージしない、たんたん確定）
  - 待っている相手：v1.6 全体完了
  - 影響：main HEAD はまだ `3adb12e`（v1.6.1 完了点）のまま

---

## 8. 関連ドキュメント

### 対象仕様書

- `仕様書_v1_6_メール配信_クリックトラッキング_ドラフト_rev18.md`（本フェーズの主仕様書）
- `URL一覧表_v1_6_rev17.md`（関連 URL 一覧）
- `FreeGroup2_v1_6_2_Phase1c_仕様書_rev8.md`（Phase 1c 完了済み）

### プロジェクト全体の標準ガイド

- **`docs/FG2_Human_Interface_Guidelines_v1_2.md`**：本フェーズで UI に触れる作業（Campaign UI・EmailTemplate 編集画面・リスト編集画面など、HTML テンプレ・CSS・JS の追加・改修）は**すべて本ガイドラインに従うこと**。BEM 風命名（`app-*` / `__` / `--` / `is-*` / `js-*`）、既存 `app.css` / `app.js` の流用原則、新規 UI ファイル作成禁止などを規定。

### その他

- 関連 decisions：（未作成、レトロアクティブ整理予定）
- 関連コミット系列（`feature/v1.6-tags-template`）：
  ```
  ef8ade4  (b) Campaign UI 全般
  690f134  (c) EmailTemplate 編集画面＋プレビュー
  88610d1  (a2) Campaign.template OneToOneField 化
  adcf558  Phase 1c-β-3b: タグで除外
  4050233  Phase 1c-β-3a: タグで追加
  b8313b0  Phase 1c-β-2b: タグ選択 UI JS
  0e20595  Phase 1c-β-2a: 新規作成ウィザード
  eba6f60  Phase 1c-β-1: 拡張集合演算 + preview-v2 API
  ae12881  Phase 1c-α UI 改善
  50d5335  Phase 1c-α: 個別追加・個別削除 UI
  ```

---

## 9. 改訂履歴

| 日付       | 改訂内容                                       |
| ---------- | ---------------------------------------------- |
| 2026-05-30 | 初版作成（オーパス君、Phase 1c〜(b) 完了時点） |
