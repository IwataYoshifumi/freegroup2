# OpenCV・OCR仕様書 v1.6.0（Claude.API）統合版

バージョン：v1.6.0（Claude.API・統合版）
作成日：2026-05-20
位置付け：FreeGroup2 次フェーズ OCR 改善の本体仕様書。

本書は FreeGroup2 次フェーズ OCR 改善の本体仕様書。OCR 系仕様書は3本構成。

1. OpenCV・OCR仕様書v1.6.0（Claude.API）統合版.md（本書）
2. OpenCV・OCR仕様書v1.6.0（Claude.API）OCRプロンプト.md（OCR バックエンド：Claude Sonnet 4.6、Tool Use 構造化 JSON 取得）
3. OpenCV・OCR仕様書v1.6.0（Claude.API）JSON構造・コンタクトフィールド対応表.md（OCR 出力 JSON 構造と Contact フィールドの対応）

本書は重複検出スコアリング詳細・マージ機構・View・URL・認証等の OCR と無関係な領域も含む統合本体。OCR 系の中核仕様は2と3を参照すること。3本のバージョンは v1.6.0 で揃える。

優先順位：本書と旧 v1.4.4 が食い違う場合、OCR改善に関わる部分は本書を正とする。OCR 出力 JSON 構造・フィールド対応の最新確定は添付2・3を正とする。

---

# 第1部 OCR出力JSON構造

## 1.1 全体構造（8ブロック）

OCR が返す JSON は8ブロック。card_meta を除く全フィールドは `{"value": ..., "confidence": "high|mid|low"}` のペア構造。card_meta のみ生値（is_business_card / orientation）で confidence ペアを持たない。

| ブロック | 役割 | フィールド数 |
|---|---|---|
| card_meta | 名刺判定・向き判定（パイプライン制御値） | 2 |
| name | 氏名情報 | 9 |
| job | 職務情報 | 4 |
| organization | 組織情報 | 7 |
| address | 住所情報 | 6 |
| contact | 連絡先情報 | 7 |
| uncategorized_card_content | 未分類（名刺に書いてある事実） | 2 |
| metadata | メタデータ | 3 |

name ブロックは v1.6.0 で **full_name を削除**し 9 フィールド（OCR は full_name を出力しない。詳細は §3.4）。

## 1.2 確定JSON構造（型・規格・既定値）

```
{
  "card_meta": {
    "is_business_card": true,        // boolean
    "orientation": "normal"          // string enum {normal|rotate_90_cw|rotate_90_ccw|rotate_180|mirror}（既存BusinessCard.orientation 5値・パイプライン制御値）
  },
  "name": {
    "original_script":  {"value": null, "confidence": "high"},  // string/null。現地文字の氏名原文（raw_json内のみ、Contact非マッピング。json_parserがfull_nameにコピー）
    "last_name":        {"value": null, "confidence": "high"},  // string/null
    "first_name":       {"value": null, "confidence": "high"},  // string/null
    "other_name_parts": {"value": null, "confidence": "high"},  // string/null。父称bin等・ミドル（中立名）
    "name_order":       {"value": null, "confidence": "high"},  // enum/null {last_first|first_last|single|other}
    "salutation_name":  {"value": null, "confidence": "high"},  // string/null。OCRが文化判断で組み立てる宛名完成形（手動入力時は必須・§1.5）
    "display_name":     {"value": null, "confidence": "high"},  // string/null。一覧表示用識別最適完成形
    "phonetic_name":    {"value": null, "confidence": "high"},  // string/null。発音表記カタカナ（記載なしでも推測値を入れる・§1.6）
    "alias_name":       {"value": null, "confidence": "high"}   // string/null。English Name・通称
  },
  "job": {
    "title":         {"value": null, "confidence": "high"},  // string/null
    "qualification": {"value": null, "confidence": "high"},  // string/null
    "department":    {"value": null, "confidence": "high"},  // string/null
    "catchphrase":   {"value": null, "confidence": "high"}   // string/null
  },
  "organization": {
    "org_name_full":              {"value": null, "confidence": "high"},  // string/null。名刺記載のままの社名
    "org_core_name":              {"value": null, "confidence": "high"},  // string/null。法人格除去後の固有名（OCR生成）
    "legal_entity_type":          {"value": null, "confidence": "high"},  // string/null。"株式会社"等（自由文字列）
    "legal_entity_type_code":     {"value": null, "confidence": "high"},  // enum/null {CP|LLP|GOV|NPO|REL|EDU|MED|PRO|IND|OTH}。二重検算
    "legal_entity_type_position": {"value": null, "confidence": "high"},  // enum/null {Pre|Post|Mid}。社名に対する法人格の前/後/中間
    "org_domain_name":            {"value": null, "confidence": "high"},  // string/null。emailの@以降抽出
    "branch":                     {"value": null, "confidence": "high"}   // string/null。支店
  },
  "address": {
    "full_address":    {"value": null, "confidence": "high"},  // string/null。名刺記載の住所全文（Contactには保存せずraw_jsonに残る）
    "postal_code":     {"value": null, "confidence": "high"},  // string/null。郵便番号（number不可・先頭ゼロ国別書式保持）
    "country":         {"value": null, "confidence": "high"},  // string(2)/null。ISO 3166-1 alpha-2（JP/US/GB/CN）
    "region":          {"value": null, "confidence": "high"},  // string/null。中間行政区画1つ・該当層ない国は空が正常値
    "city":            {"value": null, "confidence": "high"},  // string/null。市区町村相当
    "rest_of_address": {"value": null, "confidence": "high"}   // string/null。city下位をまとめる
  },
  "contact": {
    "email":          {"value": null, "confidence": "high"},  // string/null。1件
    "mobile_phone":   {"value": null, "confidence": "high"},  // string/null。本人携帯・単一値・E.164
    "personal_phone": {"value": [],   "confidence": "high"},  // array of string。本人個人直通・E.164
    "personal_fax":   {"value": [],   "confidence": "high"},  // array of string。本人FAX・E.164
    "org_phone":      {"value": [],   "confidence": "high"},  // array of string。会社代表・部署電話・E.164
    "org_fax":        {"value": [],   "confidence": "high"},  // array of string。会社・部署FAX・E.164
    "sns":            {"value": [],   "confidence": "high"}   // array of object {type, id}
  },
  "uncategorized_card_content": {
    "handwritten_text":   {"value": null, "confidence": "high"},  // string/null。名刺上の手書き＝事実
    "other_printed_text": {"value": null, "confidence": "high"}   // string/null。catchphrase以外の印字漏れ＝事実
  },
  "metadata": {
    "primary_lang":         {"value": null, "confidence": "high"},  // enum/null {ja|en|zh|und}。名刺の記載言語・中国語簡繁区別せずzh一本
    "language_composition": {"value": null, "confidence": "high"},  // enum/null {local_only|english_only|mix_bilingual|other}
    "ai_analysis_notes":    {"value": null, "confidence": "high"}   // string/null。AIの分析メモ（名刺に書いてない・Contact非マッピング・raw_json内のみ）
  }
}
```

## 1.3 型・規格の確定事項

- **postal_code = string**：number だと先頭ゼロが消える。国別書式（英数字混在・ハイフン）保持。zip_code と呼ばない（米国固有名回避）
- **country = ISO 3166-1 alpha-2**：2文字大文字（JP/US/GB/CN）。primary_lang を ISO 639-1 にしたのと規格対称
- **primary_lang = {ja|en|zh|und}**（ISO 639-1ベース、und は判定不能）。中国語は簡繁（zh-Hans/zh-Hant）を区別せず zh 一本（簡繁を分ける消費先＝中国語メール文面の簡繁出し分けが現状ないため）。zh→簡繁は後方互換、消費先ができた時点でプロンプトで足す。**名刺の記載言語であり Contact.lang にそのまま入る**
- **language_composition = 4値**（local_only/english_only/mix_bilingual/other）。local_only/english_only で normalizer 処理（ローマ字化要否）が変わる＝消費先実在
- **電話系5フィールド**：mobile_phone（本人携帯・単一値）/ personal_phone（本人個人直通・配列）/ personal_fax（本人FAX・配列）/ org_phone（会社代表・部署電話・配列）/ org_fax（会社FAX・配列）。すべて E.164 形式。**org_mobile_phone は作らない**（携帯は本人限定。組織携帯は個人/組織の区別を曖昧にし重複検出誤判定リスク）。代表電話と部署電話は org_phone に統合（区別する業務上の必要が現状ない）
- **confidence = high/mid/low**（mid であり medium ではない。ContactFieldConfidence モデル・OCR出力・json_parser すべて mid に統一）
- **card_meta.orientation = 既存5値**（normal/rotate_90_cw/rotate_90_ccw/rotate_180/mirror）。条件付き2回OCR（第4部）のパイプライン制御値
- **full_name は OCR 出力に含めない**（v1.6.0 変更）。OCR は original_script のみ出力し、json_parser が original_script を full_name にコピーする（§3.3 / §3.4）

## 1.4 confidence の3値基準と value/confidence ペア

| 値 | 基準 |
|---|---|
| high | はっきり読める／読み取れないと確定した（null が正解） |
| mid | 読めるが確信が持てない／構造上確信が持てない（zh の salutation_name 等） |
| low | かろうじて読める・推測が含まれる（phonetic_name の推測値含む、compute_salutation_name で生成した salutation_name も含む） |

- value が null（読み取れなかった）の confidence は high（「読み取れないと確定した」が正解）
- 配列フィールド（電話系・sns）の confidence は配列全体に1つ。要素ごとに信頼度が異なる場合は最も低い要素の confidence を全体に採用。空配列の confidence は high（「該当記載なしと確定した」が正解）
- confidence が low/mid のフィールドがある場合、必ずその理由を ai_analysis_notes に記述（理由のない low/mid は許容しない）
- **基本ルール（全フィールド共通）**：推測による値の入力は原則禁止。読み取れないものは null + confidence high。ただし phonetic_name のみ例外で記載なしでも推測値を入れる（§1.6）。推測が入る場合は confidence low を明示

## 1.5 salutation_name の生成・保存ルール（二段構え）

salutation_name（宛名完成形）は二段構えで決まる。OCR 出力をそのまま使う第1段と、Contact.save() オーバーライドが補完する第2段の組み合わせで、必ず非空の宛名が確定する設計とする。

- **第1段：OCR 経路（json_parser）** — OCR が出力した salutation_name をそのまま Contact に格納する（§1.5.2）。
- **第2段：Contact.save() オーバーライドと compute_salutation_name** — 第1段で空のまま抜けた場合、姓系フィールドから宛名を補完生成する（§1.5.3）。

### 1.5.1 文化別の組み立てルール

primary_lang に連動して文化別に組み立てる宛名完成形。第1段（OCR）・第2段（compute_salutation_name）とも本表のルールに準拠する。

| primary_lang | 組み立てルール | confidence |
|---|---|---|
| ja | 「{姓} 様」（姓が取れなければ「{original_script} 様」） | high |
| ko | 「{姓} 님」（姓が取れなければ「{original_script} 님」） | high |
| zh | 文化的に適切な形式で組み立て | **mid 固定** |
| en/und/その他 | 文化的に適切な形式で組み立て | high |

zh が mid 固定の理由：読み取り精度でなく「名刺OCRでは性別を取得できない構造上の限界」。中国語圏は宛名に性別敬称（先生/女士）の使い分けが必要だが名刺写真から性別は確実に判定できない。OCR は最善の組み立てを行い confidence を mid に固定し ContactFieldConfidence にレビュー対象として記録、担当者が手修正できる。**本フェーズでの zh の具体的な組み立て形式は v1.7+ で確定予定**。本フェーズでは OCR が判断した形を出力し、mid 固定で人手レビュー対象とする。

### 1.5.2 第1段：OCR 経路（json_parser）の挙動

| OCR 出力 | json_parser の挙動 |
|---|---|
| salutation_name に value あり | その値を Contact.salutation_name に格納。OCR が付けた confidence に従って ContactFieldConfidence に記録（mid/low のみ。high は記録しない） |
| salutation_name が null | Contact.salutation_name は空のまま json_parser を抜ける（第2段の Contact.save() に委ねる） |

json_parser は compute_salutation_name を呼ばない（第2段の責務）。詳細は §5.1 / §5.2。

**手動入力時必須化（v1.6.0 変更）：** salutation_name は手動入力経路で必須とする。DB レベルは NULL 許容のまま（既存スキーマ維持、既存空レコードを埋めるマイグレーションは不要）。Form/View 側で必須バリデーションを課す（ContactCreateForm / ContactUpdateForm / ContactUpdateActiveForm / ContactAjaxUpdateFieldView。詳細は §6.3）。

### 1.5.3 第2段：Contact.save() オーバーライドと compute_salutation_name

Contact.save() オーバーライドが、salutation_name_is_manual フラグと現在値に応じて補完する。

| 条件 | 挙動 |
|---|---|
| is_manual=True | 何もしない（手動入力を尊重） |
| is_manual=False かつ salutation_name が空 | compute_salutation_name(contact) で生成。結果の confidence は low（ContactFieldConfidence に low で記録） |
| is_manual=False かつ salutation_name が値あり | 何もしない（OCR 直接出力値、または前回 compute で生成した値を尊重） |

**姓修正への追従：** is_manual=False のとき、last_name / full_name / lang / name_order 等の姓系フィールドが Contact.save() で変更された場合、compute_salutation_name を強制再実行する（姓を直せば宛名が追従する）。

### 1.5.4 salutation_name_is_manual フラグの遷移ルール

| 経路 | is_manual セット |
|---|---|
| OCR 経路（json_parser） | False のまま（明示的にセットしない） |
| 手動入力 Form 経路 | Form.has_changed() で salutation_name が書き換えられたら True、デフォルト値のままなら False |
| AJAX 経路（ContactAjaxUpdateFieldView） | field_name='salutation_name' で update_field 呼ばれたとき True 固定 |
| マイグレーション | 全件 False で初期化（v1.6 メール配信仕様書 rev12 §18.2 で確定済み） |

### 1.5.5 compute_salutation_name の関数仕様

| 項目 | 内容 |
|---|---|
| 配置 | contacts/services/normalization.py |
| 入力 | Contact インスタンス |
| 出力 | salutation_name 文字列 |
| 性質 | 純関数（Contact の状態を読むだけ、DB 書き込みなし） |
| 言語別ロジック | §1.5.1 の文化別ルールに準拠 |

詳細な組み立てロジックは v1.6 メール配信仕様書 §7.4.3 に既出（実装時参照）。

### 1.5.6 メール側からの参照

メール側は `person.primary_contact.salutation_name` を信頼してそのまま読む。生成・品質判定ロジックは持たない（v1.6 メール配信仕様書 §7.4.7 と整合）。

## 1.6 phonetic_name の規約（v1.6.0 改訂）

phonetic_name は発音表記（カタカナ等）。**他フィールドと異なり、記載がなくても推測値を入れること**。

- 名刺に発音表記の記載あり、はっきり読める：記載どおり、confidence: `"high"`
- 名刺に発音表記の記載あり、読み取りづらい：記載どおり（推測込み）、confidence: `"low"`
- 名刺に発音表記の記載なし、名前が記載されている場合：漢字・現地文字から推測したカタカナ表記を格納、confidence: `"low"`。複数読み可能・キラキラネーム・古風で推測困難な場合は最も一般的な読みを採用し、理由を `ai_analysis_notes` に記述
- 名刺に名前そのものが記載されていない場合：null、confidence: `"high"`
- 本フェーズではカタカナ表記で固定（運用言語が日本語のため）
- **この特例は phonetic_name にのみ適用する**。他フィールド（氏名・住所・連絡先・組織名など）は推測禁止の原則を維持。読み取れないものは null + `"high"`

§1.5 salutation_name と §1.6 phonetic_name は別条文として残す（統合しない）。両者は性質が異なる：§1.5 は中国語の宛名で性別敬称が必要だが名刺写真から性別判定不能という特定文化の構造的制約、§1.6 は日本人名の読みが漢字から一意に決まらないという特定言語の漢字→読みの多義性。原因も対象も違う。

## 1.7 ai_analysis_notes の役割と扱い

OCR が JSON 出力時に気づいたこと・迷ったこと・confidence が low/mid の理由・phonetic_name で推測した根拠を自由記述で残すフィールド。metadata ブロックに格納。

**重要：ai_analysis_notes は名刺に書かれていない AI の分析メモであり、名刺の事実情報ではない。** handwritten_text / other_printed_text（名刺に書かれている事実）とは性質が全く異なる。

- OCR 出力 JSON の metadata ブロックには含む（raw_json に保存される）
- **Contact フィールドには一切マッピングしない。** json_parser は ai_analysis_notes を Contact 用辞書に入れない
- raw_json（BC.raw_json_1 / raw_json_2）の中だけに存在。OCRチューニング時に「なぜこのフィールドが low/mid だったか」を参照する用途（raw_json は OCRチューニング用ログという既存設計を継承）
- Contact 側の既存 notes フィールド（ユーザーメモ）とは無関係。両者は独立した別物

---

# 第2部 OCRプロンプト仕様

## 2.1 出典と役割

OCRプロンプト本文の正本は `OpenCV・OCR仕様書v1.6.0（Claude.API）OCRプロンプト.md`（添付2）。本書はその採用宣言と要点を記す。プロンプト本文と本書が食い違った場合は添付2を正とする。採用後 `cards/prompts/extract_combined.txt` に反映。

OCRプロンプトの役割：OpenCV で切り出した「名刺と思われる画像」に対して、(1) 名刺かどうかの判定、(2) 名刺の向きの判定、(3) 構造化 JSON 抽出を行う。基本ルール：

1. 画像に実際に記載されている文字だけを読み取る（推測・補完で埋めない。phonetic_name のみ例外）
2. 画像の回転・傾きの補正（向き判定を card_meta.orientation に必ず反映）
3. 言語の自動判別（metadata.primary_lang に反映）
4. 出力フォーマット（純粋な JSON のみ、装飾禁止）

## 2.2 OCRバックエンド

Claude Sonnet 4.6 を OCR バックエンドとして採用（OcrService.DEFAULT_MODEL = "claude-sonnet-4-6"）。Tool Use で構造化 JSON 取得。出力は純粋な JSON オブジェクトのみ（説明文・コードブロック装飾禁止）。API 送信前に画像を最長辺 1568px 以内にリサイズ。process_ocr 管理コマンド（cron B）は1起動につき OcrService インスタンス1個でファイル I/O 回数を抑制（API 呼び出しは BC ごと）。

## 2.3 各ブロックの抽出責務

| ブロック | 責務 |
|---|---|
| card_meta | 名刺判定（is_business_card）と画像の向き判定（orientation） |
| name | 氏名情報（原文・分解・並び順・宛名形式・表示用完成形・読み・通称）。**OCR は original_script のみ出力（full_name は出力しない）** |
| job | 役職・資格・部署・キャッチコピー |
| organization | 会社名（法人格込み／除去後／コード／位置／ドメイン）・支店 |
| address | 全文・郵便番号・国・region・市区町村・残り住所 |
| contact | メール・電話系5フィールド・SNS |
| uncategorized_card_content | 手書きメモ・未分類印字テキスト |
| metadata | 主要言語・言語構成・AI分析メモ |

## 2.4 二重検算パターン

OCR が値を出す → normalizer が別ソース（マッピング表または他フィールド）と照合 → 不一致なら confidence を low に下げ人間レビューへ。

| フィールド | OCR出力 | normalizer照合先 |
|---|---|---|
| legal_entity_type_code | コード（CP等） | legal_entity_type＋position からマッピング算出 |
| 電話系5フィールド | E.164 | country/region から導く国番号 |
| country | ISOコード | 名刺の国名文字列からマッピング算出 |

知識・文脈判断が要る派生を OCR に出させつつ、機械照合で誤りを検出する二段構え。

### 2.4.1 name ブロック内の整合性チェック（v1.6.0 新規）

OCR が出力した name ブロックの内容を、normalization 基盤の純関数 `check_name_consistency(name_block: dict) -> dict[str, str]` が裏で検算し、confidence を補正する。二重検算パターンと同じ思想で、OCR の自己申告を機械照合で検証する。

| チェック内容 | 検算ルール | confidence 補正対象 |
|---|---|---|
| 氏名構成要素の文字カバー率 | original_script の文字を構成要素（last_name + first_name + other_name_parts）が全部カバーしているか確認。カバーされない文字がある場合、OCR が構成要素を読み間違えた可能性 | last_name / first_name / other_name_parts |
| name_order と構成要素の整合性 | name_order の値が構成要素と矛盾しないか（例：name_order=single なのに last_name と first_name 両方値あり、name_order=last_first なのに last_name が null 等） | name_order |
| primary_lang と name_order の整合性 | primary_lang から想定される name_order と OCR 出力の name_order が乖離していないか（例：primary_lang=ja で name_order=first_last は不整合） | name_order |
| salutation_name と姓の整合性 | primary_lang=ja で salutation_name の中身が last_name と整合しているか（「{姓} 様」規約、last_name が salutation_name に含まれるか確認） | salutation_name |

- 補正方針：違反検出時は confidence を下げる方向のみ（high→mid→low）、上げない
- 補正対象フィールドは合計5フィールド：last_name / first_name / other_name_parts / name_order / salutation_name
- 補正理由はサーバーログのみ（DB に補正理由フィールドを新設しない、UI 拡張は本フェーズ対象外）
- **OCR プロンプトには整合性チェックの存在を匂わせない**（OCR が裏で整合性を取りに行くリスク回避、二重検算パターンと同じ思想）
- 実装配置：`contacts/services/normalization.py` に純関数として追加。json_parser が呼び出す
- **適用範囲：** 本整合性チェックは第1段（OCR 経路）で OCR が出力した salutation_name にのみ適用する。第2段（Contact.save() オーバーライド）で compute_salutation_name が生成した salutation_name には適用しない（compute は姓ベースで組み立てるため整合性は自明。§1.5.3 参照）

---

# 第3部 Contactモデル変更

## 3.1 既存フィールドのリネーム（4件）

| 旧 | 新 | 旧型 | 新型 | 変更種別 |
|---|---|---|---|---|
| company | organization | CharField(255) | CharField(255) | リネームのみ |
| mobile | mobile_phone | CharField(50) | CharField(50) | リネームのみ |
| phone | personal_phone | CharField(50) | **JSONField(default=list)** | リネーム＋型変更 |
| fax | personal_fax | CharField(50) | **JSONField(default=list)** | リネーム＋型変更 |

phone/fax は個人直通・FAX が複数あり得る（部署直通＋内線等）ため配列化。OCR 出力も配列。マイグレーションは RenameField＋AlterField。

## 3.2 新規追加フィールド（19件）

| # | フィールド | 型 |
|---|---|---|
| 1 | name_order | CharField(20, choices) |
| 2 | other_name_parts | CharField(255) |
| 3 | display_name | CharField(255) |
| 4 | phonetic_name | CharField(255) |
| 5 | alias_name | CharField(255) |
| 6 | org_core_name | CharField(255) |
| 7 | legal_entity_type | CharField(50) |
| 8 | legal_entity_type_code | CharField(10, choices) |
| 9 | legal_entity_type_position | CharField(10, choices) |
| 10 | org_domain_name | CharField(255) |
| 11 | region | CharField(100) |
| 12 | city | CharField(100) |
| 13 | rest_of_address | CharField(500) |
| 14 | country | CharField(2) |
| 15 | language_composition | CharField(20, choices) |
| 16 | handwritten_text | CharField(500) |
| 17 | other_printed_text | TextField |
| 18 | org_phone | JSONField(default=list) |
| 19 | org_fax | JSONField(default=list) |

personal_phone / personal_fax は §3.1 のリネーム＋型変更で扱うため本一覧に含めない。

**`original_script` は本一覧から除外（v1.6.0 変更）：** original_script は OCR 出力 JSON の name ブロックに存在するが、**Contact フィールドには持たない**。json_parser が original_script をコピーして Contact.full_name に格納する（§3.4 参照）。旧版の20件から original_script を削除して19件。

**ai_analysis_notes は Contact フィールドに追加しない。** OCR 出力 JSON の metadata に置き raw_json 保存のみ。新規追加フィールド一覧に含めない。Contact 側既存 notes（ユーザーメモ）とは無関係、統合・分離議論は不要。

**salutation_name_is_manual は別途追加（本編 v1.6.0 側で 20 件目として扱う）：** salutation_name の手動入力フラグ（BooleanField, default=False）。OCR キー対応関係を持たない（OCR 経路では False のまま）ため本表（OCR 起点のフィールド一覧）には含めないが、Contact モデルには追加する。遷移ルールは §1.5.4。

## 3.3 既存フィールドの流用・橋渡し（名前変換3件＋コピー処理1件）

OCR キー名 ≠ Contact フィールド名となる箇所。json_parser が処理する。処理種別は「名前変換」と「コピー処理」の2種類。

| OCRキー | Contactフィールド | 処理種別 | 処理内容 |
|---|---|---|---|
| org_name_full | organization | 名前変換 | 既存 company を organization にリネーム、OCR は org_name_full のまま橋渡し |
| full_address | address | 名前変換 | 既存 address を流用。4要素から normalization 基盤が組み立てた結果を address に格納 |
| primary_lang | lang | 名前変換 | 既存 lang を流用 |
| original_script | full_name | **コピー処理** | json_parser が original_script の値に最小限の正規化を適用してから Contact.full_name に格納 |

上記4つ以外は OCRキー = Contactフィールド名（同名でそのまま格納）。橋渡し4件以外を勝手に名前変換しないこと（コード君注意）。

### 3.3.1 original_script → full_name のコピー処理詳細

OCR は original_script を出力する時点で以下の体裁を適用済み（添付2 OCRプロンプト仕様書 name ブロック参照）：

- 姓名境界に半角スペース1つ
- 全角空白 → 半角空白
- アルファベット表記は title case で揃える
- 一部の接頭辞・接続辞（van / der / O' 等）は慣習に従う

json_parser は original_script を Contact.full_name にコピーする際、**保険として以下の最小限の正規化のみ追加適用**する：

- 全角空白 → 半角空白
- 連続空白を1つに統一
- 前後の空白除去
- 大文字小文字は変えない（OCR が title case で揃え済み）
- 全角英数字 → 半角英数字の強制変換は入れない

これは OCR が体裁を揃え忘れた場合の保険であり、強い正規化は行わない。すなわち最小限正規化の確定内容は「全角空白→半角空白／連続空白→半角1つに統一／前後の空白除去」の3点のみ。大文字小文字変換・全角英数字→半角変換等の重い正規化は行わない（OCR プロンプト側で title case ルールを既に適用しているため、json_parser での再正規化は不要）。

## 3.4 full_name の3経路別扱い（v1.6.0 全面改訂）

full_name は既存フィールド（必須・空文字 ValidationError）。

| 経路 | 扱い |
|---|---|
| OCR経路 | OCR は original_script のみ出力（full_name は出力しない）。json_parser が original_script を最小限の正規化（§3.3.1）を経て Contact.full_name にコピーする |
| 手動入力・修正画面経路 | last_name / first_name / other_name_parts / name_order から UIスクリプト（クライアント側 JS、app.jsベース）が補助組み立て。full_name 欄はユーザー直接上書き可（手入力尊重）。割れない名前（アラビア連鎖名・単一名・複姓）は構成要素合成で復元できないため手入力の余地が必要。**手入力後は必ず normalization 純関数を通す** |
| AJAX経路／サーバー側normalizer | full_name を構成要素から再生成しない。受け取った full_name に既存正規化（全角空白→半角・半角空白除去・全角英数字→半角・前後空白除去・空ならValidationError）のみ |

full_address との非対称：full_address は4要素合成で全文が一意に決まるためサーバー側で組み立て・直接編集させない。full_name は構成要素合成で復元できないケースがあり手入力余地が要るため、OCR 経路は json_parser コピー、手動は UI 補助＋手入力尊重、AJAX は既存正規化のみ。構造差で扱いが分かれており設計として一貫している。

## 3.5 UPDATABLE_FIELDS の更新

判断軸：他フィールドから生成される導出物は入れない（原文・構成要素を直せば追従）。名刺の文字でユーザーが直接直す対象は入れる。

**入れない（導出・派生・システム管理）**：address（full_address 導出先、4要素を直せば組み立て）/ org_core_name（org_name_full から導出）/ org_domain_name（email から導出）/ legal_entity_type_code（legal_entity_type＋position から導出）/ language_composition（OCR判定の事実）/ handwritten_text（事実保存用）/ other_printed_text（事実保存用）。original_script は **Contact に持たないため対象外**。ai_analysis_notes は Contact フィールドでないため検討対象外。salutation_name_is_manual も含めない（View 層が自動セット、画面から直接編集させない）

**入れる（新規追加分）**：name_order / other_name_parts / display_name / phonetic_name / alias_name / organization（旧company・継続）/ legal_entity_type / legal_entity_type_position / region / city / rest_of_address / country / postal_code（継続）/ mobile_phone（旧mobile・継続）/ personal_phone（旧phone・継続、JSONField配列）/ personal_fax（旧fax・継続、JSONField配列）/ org_phone（新規）/ org_fax（新規）。full_name は既存 UPDATABLE 継続

## 3.6 ContactFieldConfidence の confidence 値域 mid 統一

ContactFieldConfidence.Confidence の値域を medium → mid に統一。本フェーズスコープ内で確定。

波及範囲：

- TextChoices 値・CheckConstraint・save() の検証文字列を mid に
- 既存レコードの confidence="medium" を "mid" に一括更新するデータ移行マイグレーション（運用環境想定で作成、開発DBは削除可）
- 重複検出ロジック（confidence=="high" のみスコア加算、medium 直接参照箇所がないか要コード確認）
- 既存 v1.4.4 本体の medium 表記書き換え（別途）
- カスタムタグ（confidence/contact_confidence/confidence_state）の medium 直接参照を要コード確認
- OCR出力・json_parser は mid 前提（json_parser の値変換が不要になる）

high はレコード作成しない（疑似 high）。mid/low のみ ContactFieldConfidence 化、UniqueConstraint(contact, field_name)。

---

# 第4部 条件付き2回OCR

## 4.1 処理フロー

extract_carddata_via_ocr(card_image, ocr_service)（cards/tasks/ocr_pipeline.py）：

1. 1回目OCR → orientation 取得し raw_json_1 に格納
2. orientation == normal → 1回で完結（raw_json_2 = None）
3. orientation != normal → _rotate_card_image() で補正回転 → 2回目OCR → raw_json_2 に格納
4. 1回目・2回目とも同じプロンプト（フィールド抽出＋orientation 判定を同時実施）
5. 2回目失敗時は1回目結果を採用（raw_json_2=None、error_message に「2回目OCR失敗」、ocr_status=done）
6. 1回目自体失敗：ocr_status=failed / ocr_result=ocr_failed、retry_failed_ocr --ocr で差し戻し可

## 4.2 採用raw_json選択と補正回転マップ

| 場面 | 採用 |
|---|---|
| 2回目成功 | raw_json_2（補正後） |
| 2回目スキップ（orientation=normal）／2回目失敗 | raw_json_1 |

| orientation | 補正操作 |
|---|---|
| normal | 何もしない |
| rotate_90_cw | image.rotate(90, expand=True) |
| rotate_90_ccw | image.rotate(-90, expand=True) |
| rotate_180 | image.rotate(180, expand=True) |
| mirror | ImageOps.mirror(image)（誤認識ケース扱い、補正後精度は保証しない） |

2回目が走った時点で必ず補正画像で BC.card_image を上書き（2回目失敗でも画像補正は実施済み）。BusinessCard.orientation は「検出時の元の orientation」を補正ログとして保存。

---

# 第5部 json_parser（OCR出力→Contact用辞書変換）

## 5.1 配置と責務

`cards/services/json_normalizer.py` を `contacts/services/json_parser.py` に移動・全面改修。旧 json_normalizer.py は削除。

| 項目 | 内容 |
|---|---|
| 配置 | contacts/services/json_parser.py（Contact の責務として配置） |
| 主要関数 | normalize_to_contact_dict(raw_json, card_index) → (contact_dict, confidence_map) |
| 補助関数 | calc_orientation_adjusted_confidence_map(contact_dict, confidence_map, orientation) |
| 性質 | 純関数（DB操作なし・副作用なし） |
| 例外 | card_index が範囲外のときのみ ValueError |
| 防御方針 | 構造想定外でも例外を投げずベストエフォート（confidence=low に倒す） |

旧 v1.3.0 は fields/fields_array 2階層フラット構造。v1.6.0 は8ブロック構造でフィールド大幅増・配列型変更。読み取りロジックとフィールドマッピングは全面作り直し。純関数性・防御実装・orientation補正は維持。配列フィールドは配列のまま JSONField に格納（旧「先頭1件のみ採用」は廃止）。

json_parser は原文 original_script を full_name にコピーし（§3.3.1）、name ブロック整合性チェック（§2.4.1）の純関数を呼んで confidence を補正する。

**salutation_name の扱い：** json_parser は OCR 出力の salutation_name を Contact 用辞書にそのまま格納する。compute_salutation_name は呼ばない（Contact.save() オーバーライドが第2段で処理する役割分担、§1.5.2 / §1.5.3 参照）。OCR が salutation_name に null を返した場合は Contact.salutation_name を空のまま抜け、第2段に委ねる。

## 5.2 OCRキー → Contactフィールド対応（全ブロック）

橋渡し：名前変換3件（org_name_full→organization、full_address→address、primary_lang→lang）＋コピー処理1件（original_script→full_name）。これ以外は同名格納。

| ブロック | 対応 |
|---|---|
| name（9） | original_script は Contact 非マッピング・raw_json 内のみ。json_parser が original_script を Contact.full_name にコピー（§3.3 / §3.4）。last_name / first_name / salutation_name は既存（変更なし）、name_order / other_name_parts / display_name / phonetic_name / alias_name は新設 |
| job（4） | 全て既存・同名 |
| organization（7） | org_name_full→organization（名前変換）、他6件同名（org_core_name/legal_entity_type/legal_entity_type_code/legal_entity_type_position/org_domain_name 新設、branch 既存） |
| address（6） | full_address→address（名前変換）、他5件同名（postal_code 既存、country/region/city/rest_of_address 新設） |
| contact（7） | email/mobile_phone/personal_phone/personal_fax は既存（リネーム後名）、org_phone/org_fax 新設、sns は SNS各種フィールドへ分配 |
| metadata（3） | primary_lang→lang（名前変換）、language_composition 同名（新設）、**ai_analysis_notes は Contact非マッピング**（raw_json内のみ、json_parser は Contact用辞書に入れない） |
| uncategorized（2） | handwritten_text/other_printed_text 同名（新設） |
| card_meta | is_business_card は ocr_result 判定の入力（Contactフィールドでない）、orientation は BusinessCard.orientation（補正ログ） |

OCR 出力 JSON の name ブロックは9フィールド（original_script + 構成要素・派生8フィールド）。Contact 側には original_script を持たず、full_name へコピーされる。

## 5.3 orientation補正によるconfidence調整

採用 raw_json の orientation に応じて confidence_map を補正（calc_orientation_adjusted_confidence_map）。

| orientation | 補正 |
|---|---|
| normal | 補正なし |
| rotate_90_cw / rotate_90_ccw | high→mid、mid→mid、low→low |
| rotate_180 / mirror | high→low、mid→low、low→low |

補正後 mid/low のみ ContactFieldConfidence に記録（high は記録しない）。値が空のフィールドは補正対象外。raw_json 自体は不変（補正で書き換えない）。

### 5.3.1 confidence 補正の競合ルール

confidence を下げる補正は本仕様内で2系統ある：(1) name ブロック整合性チェック（§2.4.1）、(2) orientation 補正（本節 §5.3）。両者とも「下げる方向のみ（high→mid→low）、上げない」。

**同一フィールドに両方の補正が適用される場合、より低い側（より厳しい側）の confidence を最終値として採用する。** 例：orientation 補正で high→mid に下がったフィールドが、整合性チェックでも high→low と判定された場合、最終値は low（より低い側）とする。補正は累積で最も低い結果に収束させる方針。raw_json 自体は不変で、補正は confidence_map 側でのみ行う。

---

# 第6部 正規化基盤（3経路共有）

## 6.1 配置

`contacts/services/normalization.py` を新規作成（フィールド単位の純関数群、DB操作なし・副作用なし）。v1.4.4 時点では未実装（strip のみ）、本フェーズで実装。

主要純関数：normalize_full_name / normalize_organization / normalize_phone_value（配列要素ごとに適用）/ normalize_email / normalize_rest_of_address / normalize_postal_code / normalize_department_title_branch / compose_full_address（4要素から組み立て）/ derive_org_core_name / derive_org_domain_name / **check_name_consistency（name ブロック整合性チェック、§2.4.1）** / **compute_salutation_name（§1.5.5）**。

original_script → full_name コピー時の最小限正規化（§3.3.1）も本基盤の純関数として実装する。

## 6.2 3経路共有

OCR経路・手動入力Form経路・AJAX経路の3経路すべてが同じ純関数群を共有。入力経路によらず Contact フィールドに格納される値が一致することを保証。フィールド名→正規化関数の対応は Contact.UPDATABLE_FIELDS を単一の真実として引く（テーブル別新設しない）。

| 経路 | 呼び出し元 |
|---|---|
| OCR経路 | json_parser が OCR 出力読み取り時点で各フィールド値を純関数に通す |
| 手動入力経路 | ContactCreateView/UpdateView の Form clean で各フィールド値を純関数に通す |
| AJAX経路 | ContactAjaxUpdateFieldView → Contact.update_field でフィールド値を純関数に通す |

## 6.3 AJAX経路の正規化通し（コード君踏み外し最大ポイント）

AJAX経路（Contact詳細画面で個別フィールドAJAX更新）は ContactAjaxUpdateFieldView → Contact.update_field を通る。**Form を経由しない**ため Django Form の clean も CharField デフォルト strip もかからない。対応しないと AJAX経路だけ正規化されない値が DB に保存され3経路共有設計が破綻する。

対応：Contact.update_field は保存前に必ず field_name に対応する normalization 純関数を呼ぶ。ValidationError は AJAX View に伝播。実装着手前に ContactAjaxUpdateFieldView と Contact.update_field の本体を全 read して確認（次フェーズ要コード確認）。

**salutation_name 必須化（v1.6.0）：** AJAX 経路でも salutation_name の空文字送信は 400 エラーとする（§1.5 手動入力時必須化）。ContactCreateForm / ContactUpdateForm / ContactUpdateActiveForm でも必須バリデーションを課す。DB は NULL 許容のまま、Form/View 側で必須を担保する。

## 6.4 full_address の組み立て

compose_full_address(postal_code, region, city, rest_of_address, country, lang) が4要素から組み立て、Contact.address に格納（既存 address 流用）。

- OCR経路：json_parser が4要素を格納後、normalization 基盤が compose_full_address を呼び address 更新
- 手動入力経路：Form clean で4要素のいずれか変更で compose_full_address 呼び address 更新
- AJAX経路：4要素のいずれか update_field 更新で compose_full_address 呼び address 更新
- UI 上 address は直接編集不可（読み取り専用表示）。ユーザーは4要素を編集、address は自動組み立て直し
- 組み立て順序の言語・国分岐（日本式 vs 英語式・番地先国最後）は本フェーズ未確定。日本特化を本筋・他言語最小限。本フェーズは日本式実装、ja以外は次フェーズ確定（第8部）

## 6.5 正規化5カテゴリ

※ full_name は経路依存。OCR 経路ではカテゴリD（original_script からのコピー、§3.3.1 参照）、手動入力経路では UI 補助（last_name / first_name / other_name_parts / name_order から UI スクリプト補助組み立て＋手入力可）。下表ではカテゴリD と UI 補助の両方に full_name を記載しているが、これは経路によって扱いが分かれることを示す。

| カテゴリ | 性質 | 該当 |
|---|---|---|
| A：事実 | 原文の中身変えず軽い整え（前後空白除去・連続スペース1つ・全角スペース→半角）。大文字小文字・全角英数字→半角の強制変換なし | first_name/last_name/other_name_parts/display_name/salutation_name/phonetic_name/alias_name/handwritten_text/other_printed_text |
| B：enum規格 | 規定値強制、範囲外は受け皿値（other/und/OTH） | name_order/legal_entity_type_position/country/primary_lang/language_composition/legal_entity_type_code |
| C：二重検算 | OCR出力→normalizer照合→不一致でconfidence low | legal_entity_type_code/電話系5/country/name ブロック整合性チェック（§2.4.1） |
| D：導出 | 他フィールドから生成 | full_address（4要素から）/org_core_name（org_name_full から）/org_domain_name（email から）/legal_entity_type_code（legal_entity_type から）/full_name（original_script からコピー）/salutation_name（第2段 compute_salutation_name、§1.5.3） |
| UI補助 | サーバーで組み立てずUIスクリプト補助＋手入力尊重 | full_name（手動入力経路） |
| 対象外 | 正規化しない | catchphrase/qualification/website/SNS/Contact側既存notes。handwritten_text/other_printed_text も対象外（事実保存用）。ai_analysis_notes は Contact フィールドでないため対象外 |

**original_script について：** original_script は Contact に持たないため Contact フィールド表からは除外する。raw_json 内の original_script の体裁整え（カテゴリA相当）は OCR プロンプト側で済んでおり、normalization 基盤の責務外。json_parser が full_name にコピーする際の最小限正規化（§3.3.1）のみ基盤が担う。

**phonetic_name について：** カテゴリA（事実：軽い整え）のまま維持。normalization は体裁整えのみ（全角空白→半角・連続空白1つ・前後空白除去）。字数比較・構造照合はしない（カタカナと漢字で字数が一致しないため検算不能）。**name ブロック整合性チェック（§2.4.1）の対象外**（OCR が出した high/mid/low をそのまま採用）。

**salutation_name について：** OCR が出力した salutation_name（第1段）はカテゴリA（軽い整え）＋ §2.4.1 整合性チェック対象。第2段 compute_salutation_name で生成した salutation_name はカテゴリD（導出）。詳細は §1.5。

§15.5.3 既存ルール（full_name=全角空白→半角・半角空白除去・全角英数字→半角・前後空白除去・空ならValidationError／organization=株式会社系統一表記・前後位置差は吸収しない・全角半角空白除去／電話=数字ハイフン抽出・ハイフン除去・全角数字→半角・国番号正規化／email=小文字化・前後空白除去／rest_of_address=全角半角空白除去・漢数字→半角・丁目番地号を「-」・ハイフン統一／postal_code=数字のみ・全角数字→半角／department/title/branch=全角半角空白除去・全角英数字→半角・前後空白除去）は新フィールド名に揃えて踏襲、ロジック自体は変更しない。

## 6.6 org_domain_name の汎用ドメイン無視

フリーメール・プロバイダドメイン（gmail.com/yahoo.co.jp 等）の無視リスト（マスター）を持つ。org_domain_name の値は名刺どおり残す（空にしない＝個人事業主等の情報を失わない）。重複検出側がそのドメインを会社一致判定に使わない。判定は normalize 側。DUPLICATE_CHECK_FIELDS は変更しない。

---

# 第7部 重複検出・OriginalImage・OCR結果判定

## 7.1 DUPLICATE_CHECK_FIELDS（機械的リネームのみ）

| 旧 | 新 |
|---|---|
| full_name / company / department / title / branch / email / phone / mobile / address | full_name / **organization** / department / title / branch / email / **personal_phone** / **mobile_phone** / address |

変更3件（company→organization、phone→personal_phone、mobile→mobile_phone）は機械的リネーム反映のみ。フィールド数9件・スコア・ランク閾値・判定ロジック（confidence=="high" のみ加算）は変更しない。org_phone/org_fax は含めない（会社代表電話は同一会社の複数人で同値、重複検出に使うと別人が誤マージ）。

original_script は元から DUPLICATE_CHECK_FIELDS に含まれていない（変更なし）。

## 7.2 OriginalImage.exif_json

| フィールド | 型 | 既定値 |
|---|---|---|
| exif_json | JSONField(null=True, blank=True) | NULL |

- 取得タイミング：アップロード受信直後の生バイト列の時点（Pillow exif_transpose で再エンコードする前。再エンコード後は EXIF が失われる）
- 保存内容：GPS含む全EXIF（撮影日時 DateTimeOriginal / GPSInfo / Make/Model / 撮影設定等）。JSON 化可能な形に変換
- 既存の画像変換・保存処理（exif_transpose 等）は一切変更しない。生バイト列から EXIF を読み出して exif_json に格納するステップを1つ追加するのみ。両者は独立して動く
- 既存レコードは EXIF 復元不可（image_file が既に EXIF 除去済み）。新規アップロード分のみ exif_json に値が入る
- マイグレーションはフィールド追加1本
- **EXIF Orientation（撮影時の物理回転・EXIF標準1〜8）と BusinessCard.orientation（OCRが返す名刺の向き5値）は別物**。混同しない。前者は exif_json 保存（業務利用は将来検討）、後者は条件付き2回OCRの補正対象・confidence補正の入力

## 7.3 OCR結果判定（ocr_result 5値）

| 値 | 意味 | セット条件 |
|---|---|---|
| business_card | 名刺として正常取り込み | スキーマ検証OK & is_business_card=true & 正規化OK & has_minimum_info=true |
| not_business_card | OCR成功だが名刺でない | cards[0].card_meta.is_business_card=false |
| insufficient_info | OCR成功だが最低限情報不足 | スキーマ検証失敗／正規化失敗／has_minimum_info=false |
| ocr_failed | OCR技術的失敗 | 画像なし／読み込み失敗／OCR API例外 |
| others | 将来用受け皿 | 現実装フローではセットされない |

others は将来用受け皿として TextChoices に定義のみ存在、現フェーズでは設定経路を持たないのが設計どおり（OCRバックエンド追加等で分類しきれない結果が出たとき新値マイグレーションなしで採用できる予備枠）。値の廃止・新値追加はしない。

has_minimum_info：full_name（必須・strip後非空）かつ organization/email/personal_phone/mobile_phone のいずれか1つ以上が非空（リネーム後のフィールド名で参照）。判定ロジック自体は変更しない。

---

# 第8部 未確定事項と次フェーズ要確認

推測で確定記述しない項目。

- **Lang対応（compose_full_address の組み立て順序）**：日本式（〒postal→region→city→rest）と英語式（番地先・国最後）等の言語・国分岐は未確定。日本特化本筋・他言語最小限。本フェーズは日本式実装、ja以外は暫定（日本式と同順 or 英語式は要コード判断）。詳細は次フェーズ
- **AJAX経路の正規化通し**：ContactAjaxUpdateFieldView と Contact.update_field 本体を全 read し、normalization 基盤呼び出しを確実に組み込む（実装着手前）。salutation_name 空文字 400 エラーの実装確認も含む
- **認証仕様書と exif_json GPS保存の整合**：`_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_1.md` の情報管理方針（GPS のプライバシー・保持期間・閲覧権限）と矛盾しないか確認（実装着手前）
- **既存4フィールド（company/mobile/phone/fax）リネームの全参照洗い出し**：grep 全件、UI・View・テスト・重複検出・マイグレーション・テンプレ・カスタムタグ・URL。リネーム漏れゼロまで実装着手しない
- **name ブロック整合性チェック（§2.4.1）の検算ロジック詳細**：文字カバー率の判定閾値・name_order と構成要素の矛盾パターンの網羅・salutation_name と姓の照合方法は実装時に純関数として詰める。補正は下げる方向のみ・サーバーログのみ

---

# 付録 orig と org の混同注意

頭3文字が同じ「orig」と「org」のフィールドが共存する。意味は全く別物、混同すると業務的誤マッピング（人名情報を会社情報フィールドに入れる等）が起きる。

- **orig 系（個人の氏名情報）**：original_script ＝ 名刺記載の現地文字の氏名原文（漢字・ハングル・アラビア文字等そのまま）。orig は original の略、人名の原文。**ただし Contact フィールドには持たず、raw_json の name ブロック内のみに存在**。json_parser が full_name にコピーする
- **org 系（会社・組織情報）**：org_name_full（会社名フル）/ org_core_name（法人格除去後）/ org_domain_name（会社ドメイン）/ org_phone（会社電話）/ org_fax（会社FAX）。org は organization の略、会社・組織のもの

original_script（個人氏名原文）と org_name_full（会社名フル）は両方「名前っぽい原文」として並ぶ。json_parser のマッピングで取り違えが構造上起きやすい。読み下しと意味確認を毎回行う。UI ではラベルを「氏名原文」「会社名」と日本語明示。

---

# 付録 日本語↔コーディング名対照表（主要）

| 日本語 | コーディング名 |
|---|---|
| 名刺判定 | is_business_card |
| 向き | orientation |
| 氏名原文（raw_json内のみ） | original_script |
| 姓 | last_name |
| 名 | first_name |
| 他の名前部分 | other_name_parts |
| 名前並び順 | name_order |
| 宛名形式 | salutation_name |
| 表示用完成形 | display_name |
| 読み（カタカナ発音表記） | phonetic_name |
| 通称・別名 | alias_name |
| 役職 | title |
| 資格 | qualification |
| 部署 | department |
| キャッチコピー | catchphrase |
| 会社名（法人格込み） | org_name_full |
| 会社固有名（法人格除去後） | org_core_name |
| 法人格 | legal_entity_type |
| 法人格コード | legal_entity_type_code |
| 法人格位置 | legal_entity_type_position |
| 会社ドメイン | org_domain_name |
| 支店 | branch |
| 住所全文 | full_address |
| 郵便番号 | postal_code |
| 国 | country |
| 中間行政区画 | region |
| 市区町村 | city |
| 残り住所 | rest_of_address |
| メール | email |
| 個人携帯 | mobile_phone |
| 個人直通電話 | personal_phone |
| 個人FAX | personal_fax |
| 会社代表・部署電話 | org_phone |
| 会社・部署FAX | org_fax |
| SNS | sns |
| 手書きメモ | handwritten_text |
| その他印字テキスト | other_printed_text |
| 主要言語 | primary_lang |
| 言語構成 | language_composition |
| AI分析メモ | ai_analysis_notes |
| 氏名フルネーム（Contact） | full_name |
| 会社名（Contact） | organization |
| 言語コード（Contact） | lang |
| 住所（Contact） | address |
| 敬称手動入力フラグ | salutation_name_is_manual |
| EXIF情報JSON | exif_json |
| 信頼度・中 | mid（v1.4.4までは medium） |

---

# 改訂履歴

| バージョン | 日付 | 内容 |
|---|---|---|
| v1.4.5 完全統合版 | 2026-05-19 | 独立版＋本体差分＋旧v1.4.4該当部分を差分形式解消して1本に統合 |
| v1.6.0（Claude.API）統合版 | 2026-05-20 | OCR は full_name を出力しない設計に変更（json_parser が original_script をコピー）。original_script を Contact フィールドから外し raw_json 内のみに（新規追加 20→19件）。phonetic_name は記載なしでも推測 low（lang=ja 限定・カタカナ表記）。salutation_name 二段構え（OCR 第1段 + Contact.save() 第2段 compute_salutation_name）と salutation_name_is_manual フラグ遷移ルールを §1.5 に大幅拡張（6サブ節）。派生フィールド null 取り扱い共通ルールをプロンプトに追加。confidence mid 統一を本フェーズスコープ内で確定。name ブロック内整合性チェック4種類を normalization 基盤に追加（§2.4.1、適用範囲は第1段のみ）。ファイル名・バージョンを OCR 系3本で v1.6.0 に統一 |

**（本書終わり）**
