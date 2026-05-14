# FreeGroup2 URL 一覧表（v1.4.2）

**最終更新**：2026-05-13
**正本**：`名刺画像取り込みOCR仕様書_v1_4_2統合最終版.md` §11.3
**位置づけ**：本書は §11.3 から派生した「ルート / View 名 / URL name」の 3 列リスト。役割・内部処理の詳細・絞り込み仕様等は §11.3 および §11.4 / §11.5 を参照する。URL name（`name` 属性）は各アプリの `urls.py` 実装を 1 次ソースとし、本書では実装と整合する想定値を記す。

---

## URL 一覧

| No. | ルート | メソッド | View 名 | URL name |
|---|---|---|---|---|
| 1 | `/` | GET | HomeView | `home` |
| 2 | `/cards/upload/` | GET / POST | OriginalImageUploadView | `cards:upload` |
| 3 | `/cards/` | GET | CardListView | `cards:card_list` |
| 4 | `/cards/<uuid:pk>/` | GET | CardDetailView | `cards:card_detail` |
| 5 | `/originals/` | GET | OriginalListView | `originals:original_list` |
| 6 | `/originals/<uuid:pk>/` | GET | OriginalDetailView | `originals:original_detail` |
| 7 | `/persons/` | GET | PersonListView | `persons:person_list` |
| 8 | `/persons/<uuid:pk>/` | GET | PersonDetailView | `persons:person_detail` |
| 9 | `/persons/<uuid:pk>/add-additional-role/` | GET / POST | PersonAddAdditionalRoleView | `persons:add_additional_role` |
| 10 | `/contacts/create/` | GET / POST | ContactCreateView | `contacts:contact_create` |
| 11 | `/contacts/<uuid:pk>/` | GET | ContactDetailView | `contacts:contact_detail` |
| 12 | `/contacts/<uuid:pk>/update-primary/` | GET / POST | UpdatePrimaryContactView | `contacts:update_primary` |
| 13 | `/contacts/<uuid:pk>/update-active/` | GET / POST | UpdateActiveContactView | `contacts:update_active` |
| 14 | `/contacts/<uuid:pk>/preview/` | GET | PreviewContactView | `contacts:contact_preview` |
| 15 | `/duplicates/` | GET | DuplicateCandidateGroupListView | `duplicates:duplicate_group_list` |
| 16 | `/duplicates/groups/<uuid:group_id>/` | GET | DuplicateCandidateGroupDetailView | `duplicates:duplicate_group_detail` |
| 17 | `/duplicates/groups/<uuid:group_id>/review` | GET / POST | DuplicateCandidateGroupUpdateView | `duplicates:duplicate_group_update` |
| 19 | `/merge-logs/` | GET | PersonMergeLogListView | `duplicates:merge_log_list` |
| 20 | `/merge-logs/<uuid:pk>/` | GET | PersonMergeLogDetailView | `duplicates:merge_log_detail` |
| 21 | `/merge-logs/<uuid:pk>/confirm-undo/` | GET / POST | PersonMergeLogConfirmUndoView | `duplicates:merge_log_confirm_undo` |
| 22 | `/cards/<uuid:pk>/delete/` | POST | CardDeleteView | `cards:card_delete` |
| 23 | `/contacts/` | GET | ContactListView | `contacts:contact_list` |

---

## 補足

- **No.18 は欠番**：v1.4.2 改訂で旧 `/duplicates/groups/<uuid:group_id>/result/`（DuplicateCandidateGroupResultView）が廃止された（§11.3.1 参照）。No.19〜21 は新規追加（マージログ系 3 ビュー）、No.22 / 23 は v1.4.2 改訂で末尾追加。
- **PersonDetailView（No.8）の挙動**：Person.status 別二系統化（active → ContactDetailView へ HTTP 302 リダイレクト、merged / archived → 専用詳細画面）。詳細は §11.3 / §11.4 / ストック #24 参照。
- **CardDetailView（No.4）の業務画面化**：OpenCV デバッグセクションに加えて Contact 編集 UI を併設。詳細は §11.3 / ストック #28 参照。
- **ContactDetailView（No.11）は業務メイン画面**：表示モード分岐、6 セクション構成、AJAX 編集の本体仕様は D-3 系実装完了後に別途反映予定。詳細は §11.3 / §11.4 / ストック #25 / #30 / #31 参照。
- **CardDeleteView（No.22）の挙動**：POST 専用、`bc.delete()` で CASCADE 連鎖（Contact → ContactFieldConfidence → card_image post_delete）、削除後は元画像詳細（No.6）へ 302 リダイレクト。詳細は §4.3.2 / §11.3 / ストック #11 / #17 参照。

## 改訂履歴

| 日付 | 内容 |
|---|---|
| 2026-05-13 | 仕様書改訂担当オーパス君（5/13 引き継ぎ後）が、統合最終版 §11.3 と整合する形で新規作成。旧 `URL一覧表_v1.4.2.pdf` の番号体系を本ファイルに移行（PDF 18/19/20 と本体 19/20/21 のずれは、本ファイルを正として本体 §11.3 と整合させ統一）。PDF 自体の更新は別タスク。 |
