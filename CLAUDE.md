# FreeGroup2 — Claude 向けガイド

## プロジェクト概要

FreeGroup2 は名刺管理システム。ユーザーがアップロードした名刺画像から
Claude API（Tool Use）で名刺領域を検出し、OCR・データ正規化・DB 保存
までを行う。

- 旧版（v1.0.0 以前）は設計が悪かったため一度全削除し、v1.0.1 で作り直し
- バックエンドは Django、OCR は Claude Haiku 4.5 が既定

## 必ず参照する仕様書

- `docs/名刺画像取り込みOCR仕様書_v1_0_1.docx`（**最優先**。実装方針はここに従う）
- `docs/BackNavigator使い方ガイド.docx`（戻るボタン実装ガイド）
- `docs/er_diagram_v2_with_person_contact.html`（ER 図）

矛盾が出たら勝手に判断せず、ユーザーに必ず確認すること。

## v1.0.1 の重要な設計方針（順守必須）

1. **バックグラウンド処理は cron + 管理コマンド方式**
   - `threading.Thread` は使わない（v1.0.0 の設計失敗を反映した v1.0.1 の確定方針）
   - 1〜5 分間隔で `python manage.py process_pending` を cron 起動する
   - 失敗の手動再投入は `python manage.py retry_failed_ocr`

2. **View からスレッドを起動しない**
   - View は HTTP リクエスト/レスポンス処理とテンプレート選択のみ
   - アップロード受付時は `OriginalImage` を `status=pending` で保存して即レスポンス
   - 重い処理（OCR、画像切り抜き、JSON 正規化）はすべて `tasks/` 配下＋管理コマンド経由

3. **Person 一覧表示は関連 Contact から取得**
   - v1.0.1 では `Person.display_name` フィールドを設けない
   - 代表表示が必要なら最新 Contact を引く：
     `person.contact_set.order_by('-created_at').first()`
   - そこから `full_name` / `company` を表示する

4. **同名・同会社のグループ化表示**
   - 一覧では `Contact.full_name + Contact.company` が完全一致するレコードを
     1 グループとして扱い、代表（最新 Contact）1 行表示＋「他 N 件」展開
   - 重複統合機能は v2.0.0 で実装。v1.0.1 では UI 工夫だけで対処

## UI ガイドライン（必読）

- **CSS / JS は `static/css/app.css` / `static/js/app.js` の既存クラス・関数のみ使用**
- 新規 CSS クラス・JS 関数を勝手に作らない（必要になったらユーザー確認）
- **命名規則（BEM 風）**: `app-* / __ / -- / is-* / js-*`
  - 例: `app-card`, `app-card__header`, `app-card--compact`,
    `is-active`, `js-toggle-menu`
- **ボタン**: `app-btn--primary` / `app-btn--secondary` / `app-btn--danger`
  / `app-btn--sm` / `app-btn--icon`
- **フォーム**: `app-form-grid` / `app-input` / `app-form__group`
- **テーブル**: `app-table` / `app-table--nowrap`

## BackNavigator

戻るボタンは `back_navigator` アプリで提供。詳細は
`docs/BackNavigator使い方ガイド.docx` を参照。

- `push_current` は **1 リクエストにつき 1 回だけ呼ぶ**
- view_name + view_kwargs が同じならスタックに積まない（重複防止）
- テンプレートタグ: `{% load back_tags %}` で
  `back_url` / `back_all_url` / `append_back_url` / `hidden_back_field` が使える

## 環境変数

`.env`（Git 管理外）で以下を定義。雛形は `.env.example`。

| 変数名 | 用途 | 既定 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django SECRET_KEY | （必須） |
| `DEBUG` | デバッグモード | `True` |
| `ANTHROPIC_API_KEY` | Claude API キー | （必須） |
| `OCR_BACKEND` | OCR バックエンド | `claude_haiku_4_5` |

OS 環境変数を `.env` より優先（settings.py で `load_dotenv(..., override=False)`）。

## ディレクトリ構成（フェーズ1完了時点）

```
freegroup2/
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── CLAUDE.md
├── config/                     # Django プロジェクト
│   ├── settings.py
│   └── urls.py
├── cards/                      # 名刺アプリ（フェーズ2以降で実装）
├── back_navigator/             # 戻るボタンヘルパー
│   ├── back_navigator.py
│   └── templatetags/back_tags.py
├── templates/
│   └── base.html
├── static/
│   ├── css/app.css
│   └── js/app.js
└── docs/
    ├── 名刺画像取り込みOCR仕様書_v1_0_1.docx
    ├── BackNavigator使い方ガイド.docx
    └── er_diagram_v2_with_person_contact.html
```

## 今後の実装フェーズ（参考）

- フェーズ2: models（OriginalImage / BusinessCard / Contact / Person）
- フェーズ3: services（image_validator / json_normalizer）
- フェーズ4: tasks（pipeline_coordinator / ocr_service / card_cropper）
- フェーズ5: 管理コマンド（process_pending / retry_failed_ocr）
- フェーズ6: views / templates / urls
