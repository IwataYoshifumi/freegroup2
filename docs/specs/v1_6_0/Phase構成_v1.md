# FreeGroup2 v1.6 Phase 構成（最新）

**バージョン**：v1（2026-05-18 確定）
**正本性**：本ドキュメントが v1.6 実装の Phase 構成の正本。**仕様書 rev12.3 §17 / 付録 C「実装ロードマップ案」は超過扱い（superseded）**。本書と仕様書付録 C の記述が食い違う場合は本書を優先する。
**作成経緯**：仕様書 rev12.3 付録 C の Phase 表は CRUD UI の Phase 割当が抜けており、Phase 1・2 がモデル骨格だけで動作検証できない構造的弱点があったため、たんたん・サポート担当の壁打ちで縦割り型（機能単位で動くものを完結させる）に再設計した。

---

## Phase 構成（16 Phase）

| Phase | 内容 | 主たる参照章 |
|---|---|---|
| **0** | 既存モデル拡張：`CustomUser.signature` / `Person.is_unsubscribed` フィールド定義のみ（`Contact.salutation_name` は仕様書 §4.14.3 によりフィールド定義変更なしのため対象外） | §4.14 |
| **1** | A-1 核心サービス関数 3 本（`get_same_person_unit()` / `unsubscribe_person()` / `cancel_unsubscribe()`）。実装のみ、配線は後 Phase。**絶対防衛線・本体不可侵** | §9.5 |
| **2** | タグ機能一式:TagCategory / Tag / TagAssignment モデル + Tag CRUD UI + カテゴリ管理画面 + 検索結果一括タグ付け画面 + **B-1 タグ同期サービス関数**（単純コピー、`get_same_person_unit()` 不使用）+ `Execute_Merge_Only` への配線 | §4.9 / §4.9A / §4.10 / §9.4.5 / §11 / §18.4 |
| **3** | リスト機能一式:MailingList / MailingListMember モデル + リスト CRUD UI + リスト作成画面（**B-2 段階線引き**：検索条件 AND 合成の受け口骨格まで、`extraction_snapshot` はフィールド定義のみ）+ 凍結保存 | §4.11 / §4.12 / §11.4.2.1 / §11.4.3.1 |
| **4** | Settings シングルトン CRUD UI + SuppressedEmail CRUD UI（基盤系 2 モデル） | §4.8 / §4.13 |
| **5** | Campaign モデル + EmailTemplate モデル + **メール本文生成エンジン（`EmailContext.prepare`）**（単体テストまで完結）。※前提：`Contact.salutation_name` 自動組み立て改修（仕様書 §18.2、コード君 B 担当）が完了していること | §4.2 / §4.3 / §7.4 / §18.2 |
| **6** | Campaign CRUD UI + プレビュー UI（Ajax モーダル、Phase 5 の `prepare` を呼んで実動作）+ `salutation_name` 未設定の宛先 UI 表示 | §6.2.1 / §7.7.1 |
| **7** | 送信処理本体:SMTP 抽象化レイヤー + テスト配信 + 予約配信 cron + 実送信 | §7.2 / §7.7 |
| **8** | テンプレート CRUD UI（Phase 5 で作った EmailTemplate モデルに対する CRUD 画面） | §5.1（No.41〜45） |
| **9** | クリックトラッキング:TrackingLink / ClickLog モデル + `/t/<token>/` 中継 View + ボット判定（5 段階）+ IP マスキング + 古 IP 削除 cron | 第 8 章 |
| **10** | 配信停止・バウンス処理:UnsubscribeLink / Unsubscribe / SoftBounceCounter モデル + `/u/<token>/` 受信者向け UI + バウンス取り込み cron（IMAP/POP3 ポーリング）+ **B-3 検証**（凍結メンバーの merged Person 自動振替なし）+ Phase 1 の `unsubscribe_person` / `cancel_unsubscribe` 配線 | §4.5A / §4.7 / §4.8A / 第 9 章 / 第 10 章 / §11.7.2.1 |
| **11** | 配信レポート + CSV ダウンロード（配信時点スナップショット出力） | 第 13 章 |
| **12** | 認可基盤接続:mailings/tags Permission + Group（email_admin/email_editor/email_viewer / tag_admin/tag_editor/tag_viewer）+ Role 拡張（default_groups）+ **初期データ Migration 全体整理** | 第 14 章 |
| **13** | ドメイン認証:DKIM 鍵ペア生成・署名処理 + 設定ガイド画面 + 診断ツール画面（SPF/DKIM/DMARC チェック） | 第 15 章 |
| **14** | v1.6 UI 統合:既存 Person 詳細画面にタグ・配信停止状態表示 + 既存 Contact 詳細画面にタグ表示 + プロフィール画面に **`CustomUser.signature` 編集 UI** 追加 + ホーム画面アラート（必要なら） | §5.1（既存 URL 改修分） |
| **15** | 全体動作確認 + main マージ | （全章） |

---

## 横断ルール（全 Phase 共通）

各 Phase の発注書では、該当する場合に下記も実装する：

1. **ActionLog 連携**：v1.4.2 ActionLog にイベント記録（メール送信実行・配信停止実行・タグ付与・マージ時タグ同期等）
2. **cron 整備**：必要な cron はその Phase で配備。Phase 7 で初出時に配備方法（`management/commands/` + scheduler）を確立、後続 Phase は追加のみ
3. **Permission 都度追加**：その Phase で必要な Permission を Model.Meta.permissions に追加。**Group 紐付け・初期データ Migration 全体整理は Phase 12 で**
4. **マイグレーション**：モデル変更時は migration 同時作成

---

## ブランチ戦略

Phase ごとに 1 ブランチ・1 PR・main へマージ。命名は仕様書付録 C 記載の `feature/v1.6-<内容>` 形式を踏襲：

| Phase | ブランチ名（案） |
|---|---|
| 0 | `feature/v1.6-existing-models` |
| 1 | `feature/v1.6-same-person-unit` |
| 2 | `feature/v1.6-tags` |
| 3 | `feature/v1.6-mailing-lists` |
| 4 | `feature/v1.6-settings-suppressed` |
| 5 | `feature/v1.6-campaign-models-engine` |
| 6 | `feature/v1.6-campaign-ui-preview` |
| 7 | `feature/v1.6-send-processing` |
| 8 | `feature/v1.6-template-crud` |
| 9 | `feature/v1.6-click-tracking` |
| 10 | `feature/v1.6-unsubscribe-bounce` |
| 11 | `feature/v1.6-reports` |
| 12 | `feature/v1.6-permissions` |
| 13 | `feature/v1.6-domain-auth` |
| 14 | `feature/v1.6-ui-integration` |
| 15 | `feature/v1.6-final-check` |

---

## Phase 順序の依存関係

```
Phase 0 (モデル拡張)
  ↓
Phase 1 (A-1 核心)
  ↓
Phase 2 (タグ + B-1) ─┐
Phase 3 (リスト)      ├─→ Phase 5 (Campaign モデル + prepare)
Phase 4 (基盤 CRUD) ─┘        ↓
                          Phase 6 (Campaign UI + プレビュー)
                              ↓
                          Phase 7 (送信処理)
                              ├─→ Phase 8 (テンプレート CRUD)
                              ├─→ Phase 9 (クリックトラッキング)
                              ├─→ Phase 10 (配信停止・バウンス)
                              ├─→ Phase 11 (配信レポート)
                              ├─→ Phase 12 (認可基盤)
                              ├─→ Phase 13 (ドメイン認証)
                              └─→ Phase 14 (UI 統合)
                                          ↓
                                  Phase 15 (動作確認・マージ)
```

Phase 8〜14 は Phase 7 完了後であれば順序入れ替え可能。並列化はコード君 A の体力次第。

---

## 仕様書付録 C との対応

仕様書 rev12.3 §17 付録 C との対応関係：

| 仕様書付録 C の Phase | 本書 Phase | 差分 |
|---|---|---|
| Phase 0 既存モデル拡張 | Phase 0 | 同一 |
| Phase 1 新規モデル骨格（タグ・リスト・テンプレート・Settings） | Phase 2 / 3 / 4 / 5 に分割 | モデル骨格だけでは動作検証できないため、CRUD UI まで含めて機能単位で完結 |
| Phase 2 Campaign 系モデル | Phase 5 | EmailTemplate モデルも Phase 5 に統合、`EmailContext.prepare` まで含む |
| Phase 3 テンプレートエンジン | Phase 5 / 6 / 7 に分散 | エンジン本体（prepare）は Phase 5、UI は Phase 6、送信処理は Phase 7 |
| Phase 4 クリック中継 | Phase 9 | 同等内容 |
| Phase 5 配信停止 + バウンス | Phase 10 | 同等内容 |
| Phase 6 配信レポート | Phase 11 | 同等内容 |
| Phase 7 認可基盤 | Phase 12 | 同等内容 |
| Phase 8 ドメイン認証 | Phase 13 | 同等内容 |
| Phase 9 同一人物ユニット | Phase 1 + Phase 10 配線 | A-1 核心実装を Phase 1 に前倒し（絶対防衛線の早期確定）、配線は使用先 Phase で |
| Phase 10 全体動作確認 | Phase 15 | 同等内容 |
| （付録 C 未記載：CRUD UI） | Phase 2 / 3 / 4 / 6 / 8 で網羅 | 付録 C の構造的弱点を解消 |
| （付録 C 未記載：v1.6 UI 統合） | Phase 14 | v1.5.0 Phase 8 と同型の UI 統合 Phase を新設 |

---

## 改訂履歴

| 日付 | バージョン | 内容 |
|---|---|---|
| 2026-05-18 | v1 | 初版。仕様書 rev12.3 付録 C の構造的弱点（CRUD UI 未割当・モデル骨格 Phase の動作検証不能）を解消し、縦割り型（機能単位完結）に再設計。たんたん・サポート担当壁打ちで確定。 |