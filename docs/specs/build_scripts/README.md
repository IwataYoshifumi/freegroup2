# 名刺画像取り込みOCR 仕様書ビルドスクリプト

`build_spec.py` は FreeGroup2 の OCR 仕様書（v1.3.3 以降）を python-docx で生成するスクリプトである。
HOW（コード詳細）はリポジトリのソースコードを Single Source of Truth とし、本仕様書には WHAT / WHY のみを記述する方針を維持する。

## ディレクトリ構成

```
docs/specs/
├── build_scripts/
│   ├── build_spec.py          # 仕様書を生成する Python スクリプト
│   ├── requirements.txt       # python-docx の依存
│   ├── README.md              # 本ファイル
│   └── diagrams/              # 概念図 PNG（6 枚）
│       ├── A_flow.png         # 図 2-1 システム全体の処理フロー
│       ├── B_er.png           # 図 4-1 ER 図
│       ├── C_status.png       # 図 4-2 OriginalImage.status 遷移図
│       ├── D_timeline.png     # 図 8-2 worker タイムライン
│       ├── E_file.png         # 図 8-1 ファイル保存フロー
│       └── F_reconcile.png    # 図 8-3 reconcile_card_images 検出ロジック
├── v1_3_3/
│   └── 名刺画像取り込みOCR仕様書_v1_3_3.docx   # 旧版（コンテンツ確定版）
└── v1_3_4/
    └── 名刺画像取り込みOCR仕様書_v1_3_4.docx   # 現行版（見栄え修正パッチ）
```

## 使い方

### 1. 仮想環境の準備（初回のみ）

Windows（PowerShell / Git Bash）：

```bash
cd docs/specs/build_scripts
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
```

macOS / Linux：

```bash
cd docs/specs/build_scripts
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 生成

```bash
python build_spec.py
```

現行スクリプトでは `docs/specs/v1_3_4/名刺画像取り込みOCR仕様書_v1_3_4.docx` が生成される。
所要時間は数秒程度。

### 3. 出力確認

- Word / LibreOffice で開いて目視確認する
- ページ番号フッター（X / Y）は最初の表示時に Word が自動更新する。表示されない場合は `Ctrl+A` → `F9` でフィールドを一括更新する

## 概念図の差し替え手順

`diagrams/*.png` を直接置き換えるだけで build スクリプトに反映される。
PNG の幅は本文と同じ Inches(6.0) に揃えてセンタリング表示される。

ソース図形（draw.io / Excalidraw / Figma 等）は別管理とし、必要に応じて
`diagrams/` 配下に同じファイル名で書き出す。元素材を一緒に管理する場合は
`diagrams/sources/` などサブディレクトリを切ってもよい（生成スクリプトは
`*.png` の直接参照のみ）。

## バージョン履歴

| 版 | 概要 |
|---|---|
| v1.3.3 | コンテンツ確定版（v1.3.2 のレビュー指摘 6 件修正） |
| v1.3.4 | 見栄え修正パッチ：Heading 1〜4 スタイル / 表ヘッダー薄青背景 / ★改訂セル黄色背景 |

## 見栄え仕様（v1.3.4 で確定）

- **見出し**：python-docx の組込みスタイル `Heading 1〜4` を適用しつつ、ランレベルでフォント色（H1 #1F4E79 / H2 #2E75B6 / H3 #5B9BD5 / H4 #C00000）・サイズ（Pt 16/13/12/11）・太字を上書き。Word のナビゲーションウィンドウと目次自動生成が機能する
- **表ヘッダー背景**：`#DEEAF6`（薄青）。`_set_cell_bg()` 内で既存 `<w:shd>` を削除してから書き直すため、表スタイルとの干渉が起きない
- **★改訂セル背景**：`#FFF2CC`（黄）。`add_table()` の `revision_row_indices` 引数（行インデックスの集合）で指定する

## 次バージョン（v1.4.0 以降）の作成手順

### 単純な見栄え修正パッチの場合（v1.3.3 → v1.3.4 と同パターン）
1. `build_spec.py` の冒頭定数 `OUTPUT_DIR` / `OUTPUT_FILE` のパスとファイル名を更新
2. タイトルページのバージョン番号を更新
3. `section_revision_summary()` のサマリー表を新バージョン分に差し替え
4. `section_history()` の改訂履歴に 1 行追加（黄色化のため `revision_row_indices` も拡張）
5. 各章の改訂行 index を必要に応じて更新
6. `python build_spec.py` で生成

### コンテンツ変更を伴う改訂の場合
1. v1_3_4 の `build_spec.py` をコピーして編集（または上書き）
2. 上記に加えて、本文セクション（`section_chapterN`）の表データ・段落を編集
3. ★改訂が入った行は対応する `add_table()` の `revision_row_indices` に追加
4. 章タイトル等で「v1.3.x の」「v1.3.x 時点」と書いている節タイトル・本文を新バージョン名に更新
5. 履歴マーカー（"v1.3.x 改訂" 等）は残す（過去履歴の追跡用）

## トラブルシュート

- **`ModuleNotFoundError: No module named 'docx'`**：
  仮想環境を有効化していないか `pip install` 未実行。`pip install -r requirements.txt` を再実行。
- **画像が表示されない**：
  `diagrams/` 配下の PNG ファイル名が `A_flow.png` 〜 `F_reconcile.png` で揃っているか確認。
- **フォントが Yu Gothic にならない**：
  実行環境（OS）に Yu Gothic がインストールされているか確認。
  Linux 環境では Noto Sans CJK にフォールバックされることが多い。Word で開けば自動置換される。
- **ページ番号が `1 / 1` 等で固定表示される**：
  Word でフィールドを更新（`Ctrl+A` → `F9`）。LibreOffice では `Tools → Update → Update All`。

## CI 検証（任意）

LibreOffice がインストールされていれば、生成した .docx を PDF 化して
ページ数や主要見出しの存在を自動チェックできる：

```bash
soffice --headless --convert-to pdf docs/specs/v1_3_3/名刺画像取り込みOCR仕様書_v1_3_3.docx
pdfinfo 名刺画像取り込みOCR仕様書_v1_3_3.pdf | grep -E '^Pages:'
```

LibreOffice が無い環境ではスキップする。
