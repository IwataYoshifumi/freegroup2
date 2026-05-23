# OpenCV・OCR仕様書 v1.6.1（Claude.API）JSON構造・コンタクトフィールド対応表

作成日：2026-05-20（v1.6.1 改訂：2026-05-23）
対象：FreeGroup2 次フェーズ OCR 改善

本書は OCR が出力する JSON 構造と、各フィールドが Contact のどのフィールドに入るか（json_parser の橋渡しマッピング）を 1 枚にまとめたもの。`OpenCV・OCR仕様書v1.6.1（Claude.API）OCRプロンプト.md` と整合した最新版。

---

## 1. 主要な設計変更点（2026-05-20 セッション確定分）

過去セッションからの変更点を以下にまとめる。

| 項目 | 旧 | 新 |
|---|---|---|
| 氏名フルネーム | OCR が `full_name` を出力 | **OCR は `full_name` を出力しない**。OCR は `original_script` のみ出力し、json_parser が `original_script` を `full_name` にコピーする |
| 氏名原文 Contact 格納 | `original_script` を Contact フィールドに新設 | **`original_script` を Contact フィールドに持たない**。raw_json の name ブロック内のみに存在 |
| 個人携帯 | `mobile_phone` | `mobile_phone`（変更なし） |
| 個人固定・直通 | `phone` | `personal_phone` にリネーム |
| 個人FAX | `fax` | `personal_fax` にリネーム |
| 会社携帯 | `org_mobile_phone` | **削除**（携帯＝個人のみ） |
| 会社代表・部署電話 | `org_phone` | `org_phone`（変更なし・代表/部署をまとめる） |
| 会社・部署FAX | `org_fax` | `org_fax`（変更なし） |
| 会社ドメイン | `org_domain` | `org_domain_name` にリネーム |
| confidence 値域 | `high` / `medium` / `low` | `high` / **`mid`** / `low`（medium を mid に統一） |
| phonetic_name | 名刺記載なしは null（推測禁止） | **記載なしでも推測値を入れる**（本フェーズ lang=ja 限定、カタカナ表記、confidence: low） |
| salutation_name | 任意項目 | **手動入力時は必須**（DB は NULL 許容のまま、Form/View 側で必須バリデーション） |
| salutation_name_is_manual | （なし） | **新設**（Contact 専用フラグ、BooleanField default=False。is_manual=True のとき Contact.save() の compute_salutation_name による自動再計算をスキップ。詳細は v1.6.1 統合版 §1.5） |

電話系は6→5フィールドに整理。

### 1.4 v1.6.1 改訂内容

v1.6.0 リリース後の実装フェーズ（Phase 1A 〜 3B）で判明した設計問題 3 点を v1.6.1 で解決する。

1. **電話系 4 フィールドの巻き戻し**：v1.6.0 で JSONField(default=list) としていた personal_phone / personal_fax / org_phone / org_fax を CharField(50) 単一値に巻き戻す。理由：JSONField 化は前任サポート担当 Claude の見落としで、たんたんが意識的に承認していなかった。v1.1.0 で確定した「1 名刺 = 1 Contact、主たる 1 つを保存、他は raw_json に残る」原則と矛盾するため修正。

2. **organization.website 追加**：OCR 出力 JSON の organization ブロックに website フィールドを新設し、既存 Contact.website（v1.4.4 から存在、CharField(500)）にストレート流し込みする経路を明示する。理由：OCR が読み取った会社サイト URL の流入経路が仕様書未定義のまま放置されていた。実データで sns 配列に website / URL が混入する状態が発生していたが、根本原因は出力先枠の欠如。

3. **ContactSns 別テーブル化**：Contact 個別 SNS フィールド 5 件（twitter / instagram / github / linkedin / facebook）を廃止し、ContactSns 別テーブルに統合する。sns_type は choices で 8 種固定（twitter / linkedin / facebook / instagram / github / blog / youtube / line、小文字統一）。json_parser で type 正規化（X → twitter 等）を行ってから ContactSns レコード作成。理由：type バリエーション拡張に DB 変更不要、複数チャンネル対応、検索ニーズ（LINE だけ抜き出す等）に対応。詳細は §8（ContactSns 別テーブル）参照。

---

## 2. OCR 出力 JSON 構造

```json
{
  "card_meta": {
    "is_business_card": true,
    "orientation": "normal"
  },
  "name": {
    "original_script":  {"value": null, "confidence": "high"},
    "last_name":        {"value": null, "confidence": "high"},
    "first_name":       {"value": null, "confidence": "high"},
    "other_name_parts": {"value": null, "confidence": "high"},
    "name_order":       {"value": null, "confidence": "high"},
    "salutation_name":  {"value": null, "confidence": "high"},
    "display_name":     {"value": null, "confidence": "high"},
    "phonetic_name":    {"value": null, "confidence": "high"},
    "alias_name":       {"value": null, "confidence": "high"}
  },
  "job": {
    "title":         {"value": null, "confidence": "high"},
    "qualification": {"value": null, "confidence": "high"},
    "department":    {"value": null, "confidence": "high"},
    "catchphrase":   {"value": null, "confidence": "high"}
  },
  "organization": {
    "org_name_full":              {"value": null, "confidence": "high"},
    "org_core_name":              {"value": null, "confidence": "high"},
    "legal_entity_type":          {"value": null, "confidence": "high"},
    "legal_entity_type_code":     {"value": null, "confidence": "high"},
    "legal_entity_type_position": {"value": null, "confidence": "high"},
    "org_domain_name":            {"value": null, "confidence": "high"},
    "branch":                     {"value": null, "confidence": "high"},
    "website":                    {"value": null, "confidence": "high"}
  },
  "address": {
    "full_address":    {"value": null, "confidence": "high"},
    "postal_code":     {"value": null, "confidence": "high"},
    "country":         {"value": null, "confidence": "high"},
    "region":          {"value": null, "confidence": "high"},
    "city":            {"value": null, "confidence": "high"},
    "rest_of_address": {"value": null, "confidence": "high"}
  },
  "contact": {
    "email":          {"value": null, "confidence": "high"},
    "mobile_phone":   {"value": null, "confidence": "high"},
    "personal_phone": {"value": null, "confidence": "high"},
    "personal_fax":   {"value": null, "confidence": "high"},
    "org_phone":      {"value": null, "confidence": "high"},
    "org_fax":        {"value": null, "confidence": "high"},
    "sns":            {"value": [],   "confidence": "high"}
  },
  "uncategorized_card_content": {
    "handwritten_text":   {"value": null, "confidence": "high"},
    "other_printed_text": {"value": null, "confidence": "high"}
  },
  "metadata": {
    "primary_lang":         {"value": null, "confidence": "high"},
    "language_composition": {"value": null, "confidence": "high"},
    "ai_analysis_notes":    {"value": null, "confidence": "high"}
  }
}
```

---

## 3. OCRキー → Contactフィールド 対応表

「橋渡し」＝OCRキー名と Contact フィールド名が異なるもの。json_parser がマッピングする。

### 3-1. 氏名（name）

| OCRキー | Contactフィールド | 既存からの変更 |
|---|---|---|
| original_script | **（Contact 非マッピング・raw_json 内のみ）** | OCR JSON のみに存在。json_parser は `original_script` を `full_name` にコピー（後述 §4） |
| last_name | last_name | 既存（変更なし） |
| first_name | first_name | 既存（変更なし） |
| other_name_parts | other_name_parts | 新設 |
| name_order | name_order | 新設 |
| salutation_name | salutation_name | 既存（手動入力時必須化、DB は NULL 許容のまま。**Contact.save() オーバーライドで is_manual=False のとき compute_salutation_name による自動再計算あり**。詳細は v1.6.1 統合版 §1.5） |
| display_name | display_name | 新設 |
| phonetic_name | phonetic_name | 新設（本フェーズ lang=ja 限定・記載なしでも推測 low） |
| alias_name | alias_name | 新設 |
| （OCR キーなし） | salutation_name_is_manual | 新設（Contact 専用、本フェーズ新設。OCR 経路では False のまま、手動入力・AJAX 経路で View 層が True/False を切り替える。詳細は v1.6.1 統合版 §1.5 参照） |

#### full_name の扱い

`full_name` は OCR 出力 JSON に含めない（OCR は `original_script` のみ出力）。json_parser が `original_script` をコピーして Contact.full_name に格納する。

手動入力経路では従来どおり last_name / first_name から UI スクリプト補助組み立て＋手入力可、入力後は normalization 純関数を通す（詳細は `OpenCV・OCR仕様書v1.6.1（Claude.API）OCRプロンプト.md` 第2節 name ブロック参照）。

### 3-2. 職務（job）

| OCRキー | Contactフィールド | 既存からの変更 |
|---|---|---|
| title | title | 既存（変更なし） |
| qualification | qualification | 既存（変更なし） |
| department | department | 既存（変更なし） |
| catchphrase | catchphrase | 既存（変更なし） |

### 3-3. 組織（organization）

| OCRキー | Contactフィールド | 既存からの変更 |
|---|---|---|
| org_name_full | **organization** | 既存 `company` を `organization` にリネーム（橋渡し：キー名≠フィールド名） |
| org_core_name | org_core_name | 新設 |
| legal_entity_type | legal_entity_type | 新設 |
| legal_entity_type_code | legal_entity_type_code | 新設 |
| legal_entity_type_position | legal_entity_type_position | 新設 |
| org_domain_name | org_domain_name | 新設（導出・UPDATABLE 非掲載） |
| branch | branch | 既存（変更なし） |
| website | website | v1.6.1 で OCR 流入経路新設（既存 Contact.website は v1.4.4 から存在。橋渡し不要、同名ストレート流し） |

### 3-4. 住所（address）

| OCRキー | Contactフィールド | 既存からの変更 |
|---|---|---|
| full_address | **address** | 既存 `address` に入れる（橋渡し：キー名≠フィールド名・既存フィールド流用） |
| postal_code | postal_code | 既存（変更なし） |
| country | country | 新設 |
| region | region | 新設（空が正常値） |
| city | city | 新設 |
| rest_of_address | rest_of_address | 新設 |

### 3-5. 連絡先（contact）

| OCRキー | Contactフィールド | 既存からの変更 |
|---|---|---|
| email | email | 既存（変更なし） |
| mobile_phone | mobile_phone | 既存 `mobile` を `mobile_phone` にリネーム |
| personal_phone | personal_phone | 既存 `phone` を `personal_phone` にリネーム（型変更なし、CharField(50) のまま） |
| personal_fax | personal_fax | 既存 `fax` を `personal_fax` にリネーム（型変更なし、CharField(50) のまま） |
| org_phone | org_phone | v1.6.1 新設（CharField(50)） |
| org_fax | org_fax | v1.6.1 新設（CharField(50)） |
| sns | ContactSns 別テーブル | v1.6.1 で別テーブル化。json_parser が sns 配列の各要素を ContactSns レコードに分配（type 正規化付き、§8 参照） |

### 3-6. 未分類（uncategorized_card_content）

| OCRキー | Contactフィールド | 既存からの変更 |
|---|---|---|
| handwritten_text | handwritten_text | 新設（UPDATABLE 非掲載） |
| other_printed_text | other_printed_text | 新設（UPDATABLE 非掲載） |

### 3-7. メタデータ（metadata）

| OCRキー | Contactフィールド | 既存からの変更 |
|---|---|---|
| primary_lang | lang | 既存 `lang` に入れる（橋渡し：キー名≠フィールド名） |
| language_composition | language_composition | 新設 |
| ai_analysis_notes | （Contact 非マッピング・raw_json 内のみ） | 新設（OCR チューニング用ログ、Contact に持たない） |

### 3-8. card_meta

| OCRキー | 入力先 | 備考 |
|---|---|---|
| is_business_card | BusinessCard 側で ocr_result 判定に使用 | Contact フィールドではない |
| orientation | BusinessCard.orientation（既存5値） | パイプライン制御値（条件付き2回 OCR の補正対象） |

---

## 4. 橋渡し（OCRキー → Contactフィールド の変換処理）一覧

json_parser が処理する箇所。コード君が間違えやすいので明示。

| OCRキー | Contactフィールド | 処理種別 | 処理内容 |
|---|---|---|---|
| org_name_full | organization | 名前変換 | OCRキー名と Contact フィールド名が異なるので名前を付け替える |
| full_address | address | 名前変換 | 同上 |
| primary_lang | lang | 名前変換 | 同上 |
| original_script | full_name | **コピー処理** | json_parser が `original_script` の値に最小限の正規化を適用してから Contact.full_name に格納する |

上記4つ以外は OCRキー = Contactフィールド名（そのまま入れる）。

### 4-1. original_script → full_name のコピー処理詳細

OCR は `original_script` を出力する時点で以下の体裁を適用済み（OCRプロンプト仕様書 第2節 name ブロック参照）：

- 姓名境界に半角スペース1つ
- 全角空白 → 半角空白
- アルファベット表記は title case で揃える
- 一部の接頭辞・接続辞（van / der / O' 等）は慣習に従う

json_parser は `original_script` を Contact.full_name にコピーする際、**保険として以下の最小限の正規化のみ追加適用**する：

- 全角空白 → 半角空白
- 連続空白を1つに統一
- 前後の空白除去
- 大文字小文字は変えない（OCR が title case で揃え済み）
- 全角英数字 → 半角英数字の強制変換は入れない

これは OCR が体裁を揃え忘れた場合の保険であり、強い正規化は行わない。

---

## 5. 既存フィールドのリネーム影響範囲（コード君注意）

以下4フィールドはリネーム。参照箇所すべて修正・漏れゼロ。開発DBは削除OK（データ移行不要）。

| 旧フィールド名 | 新フィールド名 |
|---|---|
| company | organization |
| mobile | mobile_phone |
| phone | personal_phone |
| fax | personal_fax |

影響：

- `contacts/models.py`（フィールド定義・UPDATABLE_FIELDS）
- `config/constants.py` の `DUPLICATE_CHECK_FIELDS`（`company` / `phone` / `mobile` を含む → `organization` / `personal_phone` / `mobile_phone` に）
- 既存 UI テンプレート・View の参照
- 既存重複検出コード
- 既存テスト
- マイグレーション（フィールドリネーム）

`DUPLICATE_CHECK_FIELDS` 確定値：

`full_name / organization / department / title / branch / email / personal_phone / mobile_phone / address`

（`address` は既存フィールド流用なのでリネーム不要。`company` → `organization`・`phone` → `personal_phone`・`mobile` → `mobile_phone` のみ反映）

**v1.6.1 改訂：** v1.6.0 では phone / fax / org_phone / org_fax を JSONField(default=list) としていたが、v1.6.1 で CharField(50) に巻き戻した。本表のリネーム 4 件は型変更を伴わない（リネームのみ）。

---

## 6. ContactFieldConfidence の値域 mid 統一

旧 `medium` を `mid` に統一する。

| 確定 | 値域 |
|---|---|
| ContactFieldConfidence.Confidence の TextChoices | `high` / `mid` / `low` |
| CheckConstraint | mid を許可 |
| OCR 出力 JSON の confidence | mid（OCRプロンプト仕様書と整合） |
| json_parser の確認値 | mid |

既存レコードに `medium` がある場合は、データ移行マイグレーションで一括 `mid` に更新。重複検出ロジックは `confidence == "high"` のみスコア加算するため medium/mid 切り替えの影響なし。

カスタムタグ（confidence / contact_confidence / confidence_state）の medium 直接参照箇所と、本体仕様書 v1.4.4 の medium 表記書き換えは別途オーパス君統合版 v1.4.5 本体側で対応する。

---

## 7. 命名の紛らわしさ注意（コード君・仕様書対照表に明記）

- `original_script`（original の略）＝氏名の原文。**個人の名前**。**ただし Contact フィールドには持たず、raw_json の name ブロック内のみに存在**。json_parser が full_name にコピーする
- `org_*`（organization の略）＝**会社・組織**のもの（org_name_full / org_core_name / org_domain_name / org_phone / org_fax）

頭3文字が同じ（orig / org）で混同しやすい。意味は別物。json_parser のマッピング実装時に取り違えに注意。

---

## 8. ContactSns 別テーブル（v1.6.1 新設）

Contact 個別 SNS フィールド 5 件（twitter / instagram / github / linkedin / facebook）を廃止し、ContactSns 別テーブルに統合する。

### 8.1 モデル定義

```python
class ContactSns(models.Model):
    id = UUIDField(primary_key=True, default=uuid.uuid4)
    contact = ForeignKey(Contact, on_delete=CASCADE, related_name="sns_accounts")
    sns_type = CharField(50, choices=SnsType.choices)
    sns_id = CharField(500)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["contact", "sns_type", "sns_id"],
                name="unique_contact_sns"
            )
        ]
```

### 8.2 SnsType choices（8 種、小文字統一）

```python
class SnsType(TextChoices):
    TWITTER   = "twitter",   "Twitter"
    LINKEDIN  = "linkedin",  "LinkedIn"
    FACEBOOK  = "facebook",  "Facebook"
    INSTAGRAM = "instagram", "Instagram"
    GITHUB    = "github",    "GitHub"
    BLOG      = "blog",      "Blog"
    YOUTUBE   = "youtube",   "YouTube"
    LINE      = "line",      "LINE"
```

### 8.3 UniqueConstraint の意味

同一 Contact に対して、同じ sns_type かつ同じ sns_id の組み合わせを重複登録しない。例：同じ Contact で twitter の `@example` を 2 レコード作ることはできないが、twitter の `@example` と twitter の `@another` は両立する（同一 sns_type で複数チャンネル可）。

### 8.4 json_parser でのレコード生成ロジック

OCR が出力する type は表記揺れがあり得る（`LinkedIn` / `linkedin` / `X` / `Twitter` 等）。json_parser で正規化してから ContactSns レコード作成する。

```python
_SNS_TYPE_ALIASES = {
    "x": "twitter",     # X → Twitter（旧称統一）
    # 必要に応じて追加（運用データを見て増やす）
}

def _normalize_sns_type(raw_type: str) -> str:
    if not raw_type:
        return ""
    normalized = raw_type.strip().lower()
    return _SNS_TYPE_ALIASES.get(normalized, normalized)
```

レコード生成フロー：

```
for entry in raw_json["contact"]["sns"]["value"]:
    raw_type = entry.get("type", "")
    raw_id = entry.get("id", "")

    normalized_type = _normalize_sns_type(raw_type)

    if normalized_type not in SnsType.values:
        # choices 外の type はサイレント無視（ログに warning は出してもよい）
        continue

    if not raw_id:
        continue

    # UniqueConstraint に違反する重複は get_or_create で抑止
    ContactSns.objects.get_or_create(
        contact=contact,
        sns_type=normalized_type,
        sns_id=raw_id,
    )
```

- choices 外の type は v1.6.1 ではサイレント無視（レコード生成しない）。運用データを蓄積し、v1.7.0 以降で other 受け皿の必要性を判断。ログに warning は出してもよい（実装時判断）
- 会社の公式ウェブサイト URL は sns に含めず organization.website に格納する（OCR プロンプト §6）

### 8.5 既存個別 SNS フィールドの廃止

Contact から twitter（CharField(255)）/ instagram（CharField(255)）/ github（CharField(255)）/ linkedin（CharField(500)）/ facebook（CharField(500)）の 5 フィールドを削除する。開発 DB は削除可（マイグレーション時のデータ移行は不要）。

---

## 9. 改訂履歴

| バージョン | 日付 | 内容 |
|---|---|---|
| v1.6.0 | 2026-05-20 | 初版（OCR 出力 JSON 構造・コンタクトフィールド対応表） |
| v1.6.1 | 2026-05-23 | 電話系 4 フィールド（personal_phone / personal_fax / org_phone / org_fax）を配列から単一値に巻き戻し（v1.6.0 で JSONField 化したのは前任サポート担当 Claude の見落としによる仕様逸脱だったため修正）。organization に website フィールドを新設（名刺記載の会社ウェブサイト URL を sns ではなくここに格納する経路を明示）。Contact 個別 SNS フィールド 5 件を廃止し ContactSns 別テーブル化（sns_type choices 8 種：twitter / linkedin / facebook / instagram / github / blog / youtube / line、json_parser で type 正規化）。 |
