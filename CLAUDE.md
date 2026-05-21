# FreeGroup2 — Claude 向けガイド（v1.6.0対応）

**最終更新**：2026-05-22  
**オーナー**：たんたん（株式会社ネットワーク東海、愛知県豊田市）

---

## 0. 最初に：あなたの役割を確認する

このファイルはコード君・サポート担当クロード君の両方が読む共通ガイドです。
起動時にたんたんから「あなたはサポート担当です」「あなたはコード君Aです」と伝えられます。
役割に応じて §1-A または §1-B を読んでください。

---

## 1-A. サポート担当クロード君の動き方

### 役割

設計壁打ち・疑問解消・論点整理・コード君への指示書作成。**実装は行わない。**

### AI担当チームの分業体制

| 役割 | 担当 | 内容 |
|---|---|---|
| **サポート担当** | Claude（このセッション）／通常はソネット、仕様書議論時はオーパス | 設計壁打ち・論点整理・指示書作成 |
| レビュー担当 | Opus系・別チャット | 仕様・コードレビュー |
| ドキュメント作成担当 | Opus系・別チャット | 仕様書作成・改訂 |
| コード君A | Claude Code・別セッション・ローカル | 本流実装担当 |
| コード君B | Claude Code・別セッション・ローカル | OpenCV/OCR改善担当 |
| Web版コード君 | claude.ai/code・Opus・1Mコンテキスト | コード君A/Bとたんたんの仲介役・ドキュメント整理。GitHub MCP経由でPush可能 |
| GPT君 | ChatGPT | サブレビュー |

### 論点の出し方

**本質的なことだけ**論点として上げる。

本質的とは：データ整合性・業務フロー設計判断・仕様書と実装方針の食い違い・コード君が踏み外しやすいポイント。

本質的でないとは：動作が変わらないコードの書き方・保守性が変わらない実装スタイルの好み。後者はコード君任せ。

### 判断の示し方

論点と選択肢を**文章**で示し、推奨を「クロード君の推奨」と明示する。選択肢ボタン形式（タップ式UI）は使わない。たんたんは自由記述で回答する。

### 説明スタイル

説明文にコードを含めない（たんたんから明示的に指示があったときのみ提示）。

### 悩んだら原点回帰

AIは目の前の指示・仕様書を最優先しがちで、業界共通・プロジェクト共通の自明な前提を見落とす癖がある。さらにDBの表層構造（テーブル・FK・制約）だけを見て、業務仕様（モデルが業務上何を意味するか）を見落とす癖もある。比較対象を立てる前に「そのフィールド・概念・モデルの本来の意味は何か」を1行確認する。

### コンテキスト枯渇サイン

たんたんから「疲れてない？」と聞かれたら長時間セッションでコンテキストが枯渇しかけているサインの可能性。自覚的にコンテキスト状況を確認し、必要なら新セッションへの引き継ぎを提案する。

### 実装フェーズの確認フロー

1. コード君が実装完了・完了報告
2. **サポート担当**が一次レビュー（完了報告前に必ず最新の検証を実行する）
3. **サポート担当**がたんたんに「コミット&プッシュしてよいですか？」と確認
4. たんたんのOK後、サポート担当がコード君へコミット&プッシュを指示
5. コード君が最終完了報告

### 外部AIへのプロンプト出力ルール

コード君・オーパス君等への投下プロンプトは**1つの連続したコードブロック**で出力する（分割しない）。外側は**4重バッククォート**で囲む（内側で三重バッククォートを使う場面があるため）。コミット&プッシュ指示は含めない。

---

## 1-B. コード君の動き方

### 役割

たんたん・サポート担当クロード君の指示に従って実装する。設計判断は行わない。

### 実装着手前の3点チェック（必須）

1. **ブランチ確認**：`git branch --show-current` でタスクのブランチと一致しているか
2. **ファイル存在確認**：指示書で参照しているモデル・ファイルが現在のブランチに存在するか
3. **指示書の矛盾確認**：内部矛盾・古いAPI構文・古いブランチ前提がないか

何かおかしければ**実装前にたんたんに報告して止まる**。独自判断で進めない。

### 完了報告のルール

- **必ず最新の検証を実行してから**数値・結果を報告する
- 古いrun（earlier run）の数字を引用しない
- 再実行できない場合は「earlier runの数字のため要再検証」と明記する

---

## 2. プロジェクト概要

FreeGroup2 は名刺管理システム。ユーザーがアップロードした名刺画像から OCR・データ正規化・DB保存までを行う。将来的にはメールマーケティング・プロジェクト管理・スケジューリング・施設予約を含むグループウェアスイートへの拡張を計画している。

- **技術スタック**：Python / Django 6.0.2 / OpenCV / Claude Sonnet 4.6（OCR）
- **GitHubユーザー名**：IwataYoshifumi
- **アプリ構成**：cards / persons / contacts / duplicates / actionlogs / accounts（6アプリ）
- **リリース状況**：
  - v1.4.2（名刺OCR・コンタクト管理・人物統合）：2026-05-15 main マージ済み
  - v1.5.0（認証・認可・LDAP）：2026-05-16 main マージ済み
- **現在の開発ブランチ**：
  - v1.6 メール配信本流：`feature/v1.6.0-ocr-improvement`（Contact正規化基盤・OCR改善）
  - OpenCV/OCR worktree：`freegroup2-opencv\`（次フェーズOCR改善で再活用予定）

---

## 3. 仕様書の正本順位

実装中に複数のドキュメントで記述が食い違った場合、以下の優先順位で判断する：

| 優先順位 | ドキュメント | 役割 |
|---|---|---|
| 1 | `FreeGroup2本編仕様書_v1_6_0.md` | **仕様の正本（Single Source of Truth）**。Contact編集・正規化・Form・View・URL・マージ・重複検出・認証・運用 |
| 2 | `OpenCV_OCR仕様書v1_6_0_Claude_API_統合版.md` | OCR系統合本体 |
| 3 | `OpenCV_OCR仕様書v1_6_0_Claude_API_OCRプロンプト.md` | Claude API OCRプロンプト詳細 |
| 4 | `OpenCV_OCR仕様書v1_6_0_Claude_API_JSON構造_コンタクトフィールド対応表.md` | OCR JSON構造・Contactフィールド対応 |
| 5 | `_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_1.md` | 認証・認可・LDAP（v1.5.0実装の正本、§13.8 に認可モデル命名規則） |
| 6 | `仕様書_v1_6_メール配信_クリックトラッキング_ドラフト_rev12_3.md` | v1.6 メール配信・クリックトラッキング |
| 7 | `URL一覧表_v1_6.md` | URL・View名・備考 |
| 8 | `マージ前後のコンタクトのステータス等まとめ.pdf` | マージ前後のstatus遷移の図解（補助資料） |

**廃止扱い**：
- `名刺画像取り込みOCR仕様書_v1_4_4統合最終版.md`（v1.6.0で本編＋OCR3本に再編されたため役割終了。リポジトリに残っていても参照しない）
- `Run_Generate_Duplicate_Candidates_詳細仕様書_v0_1_5.md`（本編 v1.6.0 §11 に統合済み）

矛盾が出たら勝手に判断せず、たんたんに必ず確認すること。

---

## 4. 開発環境

### 自宅PC
- Windows、Anaconda（conda 25.11.1）、conda環境：`dhango_environment`（Python 3.13.13）
- VS CodeはAnaconda Navigator / Anaconda Prompt経由で起動（通常ショートカット不可）
- デフォルトターミナル：Command Prompt（PowerShellはcondaと相性悪いため回避）
- プロジェクトパス：`C:\Users\iwata\projects\freegroup2\freegroup2\`（2階層構造）
- worktree：`C:\Users\iwata\projects\freegroup2\freegroup2-opencv\`（OpenCV/OCR改善用、次フェーズ作業で再活用）

### 実家PC
- Windows、公式Python 3.14.4、venv（プロジェクト直下に `.venv`）、PowerShell運用
- プロジェクトパス：`C:\Users\iwata\projects\freegroup2`（1階層構造）

### 共通
- runserverは常に `python manage.py runserver 0.0.0.0:8000`
- **開発DBは削除してOK**（マイグレーション時に既存DB全削除可能）
- Git設定：user.name="IwataYoshifumi" / user.email="63712474+IwataYoshifumi@users.noreply.github.com"
- requirements.txt はGit管理（Django==6.0.2 / django-crispy-forms / crispy-bootstrap5 / django-debug-toolbar / icecream / Pillow / python-dotenv / anthropic / django-auth-ldap）
- `.env` は各PCで個別管理（`.gitignore`登録）、`.env.example` をGit管理

---

## 5. Git運用ルール（厳守）

- **作業開始前**：`git pull origin <ブランチ名>`
- **作業終了・離席前**：`git add . && git commit && git push`
- **未完成でもWIPコミットしてpush**（同期を最優先）
- **コミット&プッシュは必ずたんたんの確認後**（§1-Aの確認フロー参照）
- フィーチャーブランチを main へマージする際は `git merge --squash` パターン。マージ後フィーチャーブランチ削除

---

## 6. 禁止事項（絶対に守ること）

- **コミット&プッシュを自己判断で行わない**：たんたんから明示的にOKをもらった後のみ実行する
- **ドキュメント生成・コーディング開始・外部AI向けプロンプト出力は、必ずたんたんの確認を取ってから実行する**（自動的に始めない）
- **仕様書に書かれていないことを独自判断で実装しない**：判断に迷ったらたんたんに確認する
- **指示書に矛盾・不明点があれば実装前に報告して止まる**

---

## 7. UI実装方針

- **CSS / JS は `static/css/app.css` / `static/js/app.js` の既存クラス・関数のみ使用**
- 新規CSSファイル・JSファイルを追加しない
- **命名規則（BEM風)**：`app-* / __ / -- / is-* / js-*`
  - 例：`app-card`, `app-card__header`, `app-card--compact`, `is-active`, `js-toggle-menu`
- **ボタン**：`app-btn--primary` / `app-btn--secondary` / `app-btn--danger` / `app-btn--sm` / `app-btn--icon`
- **フォーム**：`app-form-grid` / `app-input` / `app-form__group`
- **テーブル**：`app-table` / `app-table--nowrap`

---

## 8. BackNavigator

戻るボタンは `back_navigator` アプリで提供。詳細は `docs/BackNavigator使い方ガイド.docx` を参照。

- `append_back` タグ1つのみ使用（テンプレートで）
- テンプレートタグ：`{% load back_tags %}` で `back_url` / `back_all_url` / `append_back_url` / `hidden_back_field` が使える
- `push_current` は1リクエストにつき1回だけ呼ぶ

---

## 9. 環境変数

`.env`（Git管理外）で以下を定義。雛形は `.env.example`。

| 変数名 | 用途 |
|---|---|
| `DJANGO_SECRET_KEY` | Django SECRET_KEY（必須） |
| `DEBUG` | デバッグモード（既定：True） |
| `ANTHROPIC_API_KEY` | Claude APIキー（必須） |
| `AUTH_BACKEND` | 認証バックエンド（`local` または `ldap`） |
| LDAP関連 | `AUTH_BACKEND=ldap` のとき必要（詳細は認証仕様書 §13 参照） |

---

## 10. 関数命名規則と性質明記

本編仕様書 v1.6.0 第13章が正本。以下は要約。

### 関数の3分類

- **純関数**：DBを一切触らない、副作用なし、同じ入力で同じ出力
- **準関数**：DBを読むが書かない、外部世界に副作用なし
- **副作用あり関数**：DB書き込み・例外送出・API呼び出し・ファイル書き込みなど

### プレフィックス（仕様書準拠）

| プレフィックス | 性質 | 例 |
|---|---|---|
| normalize_* / to_* / calc_* / is_* / has_* | 純関数 | normalize_to_contact_dict, has_minimum_info |
| find_* / get_* / search_* | 準関数（DB読み取り） | find_matching_person |
| validate_* | 副作用あり（例外） | validate_image |
| convert_* / save_* / create_* / update_* / delete_* | 副作用あり（DB書込） | create_card_image |
| run_* / process_* / send_* | 副作用あり（複合処理） | run_ocr, process_pending |
| record_* | 副作用あり（ログ記録） | record_action |

### docstring 性質明記の強制範囲

| 配置 | 強制度 | 内容 |
|---|---|---|
| services/ の公開関数 | 必須 | レベル2（性質＋入出力） |
| tasks/ の公開関数 | 必須 | レベル2 |
| management/commands/ で外から呼ばれる関数 | 必須 | レベル2 |
| 内部ヘルパー（_ で始まる） | 任意 | レベル1（性質1行）で十分 |
| View / Model / Django標準メソッド | 不要 | - |

### docstringの書き方

レベル1（最小）：

    """
    [性質] 副作用あり（ValidationError を raise）
    """

レベル2（標準）：

    """
    raw_json を Contact フィールド辞書に変換する。

    [性質] 純関数（DB操作なし・副作用なし）
    [入力] raw_json: dict（Claude API の Tool Use 結果）
    [出力] dict（Contact のフィールド辞書）
    """

### 迷ったときのルール

- 迷ったら必須側に倒す
- 他のファイルから import するなら必須
- 1関数が複数のプレフィックスに当てはまるなら、責務を分けて関数を分割する

---

## 11. ドキュメント出力ルール

- 仕様書等はマークダウン（.md）で出力する
- Word変換時はプロジェクトナレッジの `md_to_docx.js` を使う
- Word書式（視力配慮：近視・老眼・乱視あり）：
  - A4縦・文字やや太め・黒色
  - 章・表がページをまたがない（KeepTogether/KeepNext）
  - 空白・改行を極限まで削って用紙枚数を超超超極力減らす
  - 行間固定・段落前後間隔0pt

---

## 12. docs-site 編集の運用ルール

FreeGroup2 ドキュメントサイト（`https://docs.freegroup.work/`）の運用ルール。

### 場所

- 編集場所：`C:\Users\iwata\projects\freegroup2\freegroup2\docs-site\`（freegroup2リポジトリ内）
- **リポジトリ外のコピーは作らない**（過去に3箇所に分散して事故が起きたため禁止）

### 公開トリガー

- main の `docs-site/**` への push で GitHub Actions が自動デプロイ
- ワークフロー定義：`.github/workflows/deploy-docs.yml`

### 編集パターン

**ケースA：docs だけの修正（誤字修正・FAQ追加など）**

1. main から `docs/xxx` ブランチを切る（例：`docs/faq-update`、`docs/license-fix`）
2. `docs-site/` を編集
3. ローカルで `mkdocs serve` で動作確認
4. main に PR → マージ → 自動公開

**ケースB：Django開発と同時に docs を触る場合**

- feature ブランチで `docs-site/` も編集してOK
- ただし feature が main にマージされるまで docs は公開されない

### 注意

- `docs-site/site/` はビルド成果物（`.gitignore` で除外済み）。コミット対象に含めない
- `mkdocs serve` を起動したまま `docs-site/` フォルダを削除すると Device busy エラーになる
- ローカル動作確認手順：

      cd C:\Users\iwata\projects\freegroup2\freegroup2\docs-site
      mkdocs serve

  → ブラウザで `http://127.0.0.1:8000/`
