# Sonnet 4.6 vs Haiku 4.5 OCR 品質比較考察レポート（仮名化版）

> **注記**：本レポートは個人情報保護のため、実在の氏名・会社名・連絡先を仮名化した版である。
> 仮名 → 実名の対照は別途ローカル保管されている対照表（`freegroup2_local_archive/redaction_map_2026_05_23.md`）を参照。
> 実名版データは `freegroup2_local_archive/sonnet_vs_haiku_comparison_2026_05_23_full.md` および同梱の JSON にローカル保管。
> 作成日：2026-05-23

---

**作成日**：2026-05-23
**対象**：FreeGroup2 OCR バックエンドのモデル選定
**データソース**（実名版・ローカル保管）：
- `freegroup2_local_archive/sonnet_4_6_baseline_2026_05_23.json`（Phase 3B E2E 検証時の Sonnet 4.6 データ）
- `freegroup2_local_archive/haiku_4_5_2026_05_23.json`（同一画像 4 枚で Haiku 4.5 を実行したデータ）
**比較条件**：OCR プロンプト本文（`cards/prompts/extract_combined.txt`）・json_parser・JSON Schema は完全同一、モデルだけを変更（`DEFAULT_MODEL` を `"claude-sonnet-4-6"` → `"claude-haiku-4-5-20251001"`、検証後 Sonnet 4.6 に復元）

---

## エクスポートと画像同一性確認

- **OriginalImage**：4 件すべて `image_file` の SHA256 ハッシュで Sonnet 側と完全一致（同じバイナリ画像が同時アップロード）
- **BusinessCard ペアリング**：「**原画像 SHA256 + card_index**」で 23/23 件ペア成立。OpenCV は決定論的なため、同じ画像から同じ 23 枚（6 / 3 / 7 / 7）が切り出される
- **card_image SHA256 一致は 13/23**：残り 10 件は orientation 判定が Sonnet/Haiku で異なり 2 回目 OCR の補正画像で上書きされた結果ファイル内容が変わったケース（業務上問題なし、両者で同じ名刺と識別可能）

---

## 観点 C-1：基本動作の比較

| 指標 | Sonnet 4.6 | Haiku 4.5 | 差 |
|---|---|---|---|
| OpenCV 検出 BC 件数 | 23 | 23 | ±0（決定論的） |
| ocr_status=done | 23 | 23 | ±0 |
| **ocr_result: business_card** | **18** | **7** | **−11(−61%)** |
| ocr_result: not_business_card | 5 | 6 | +1 |
| **ocr_result: insufficient_info** | **0** | **10** | **+10** |
| ocr_result: ocr_failed | 0 | 0 | ±0 |
| **error_message 非空 BC** | **0** | **8** | **+8** |
| Contact 生成数 | 18 | **7** | **−11** |
| Person 生成数 | 18 | 7 | −11 |
| ContactFieldConfidence | 113 | 38 | −75 |

**観察**：Haiku は Contact 生成成功率が **39%（7/18）まで低下**。Sonnet が 100% 成功する画像セットで顕著な精度差。

---

## 観点 C-2：ai_analysis_notes の質的比較

| 指標 | Sonnet 4.6 | Haiku 4.5 |
|---|---|---|
| 平均文字数 | **390** | **184** |
| 最短 / 最長 | 128 / 710 | **0** / 460 |
| 空 BC | 0 | **7 件**（cards[0] が dict でない不正出力で取得不能） |
| ai_notes の shape | 全件 dict（規約遵守） | dict_proper 16 / card_not_dict 6 / no_cards 1 |

### 質的差の例

同一名刺（画像 A 系 `ci=0`、仮名：山田 太郎）：

- **Sonnet**：382 文字、「ローマ字 Yamada Taro から推測（confidence: low）。『太郎』の読みは『たろう』が一般的だが他の読み（たくみ等）も存在するため low とした。+81-90-XXXX-XXXX は携帯電話番号として判断し mobile_phone に格納...」
- **Haiku**：**取得不能**（Schema 違反で `cards[0]` が想定外、`'sns' was unexpected` エラーで insufficient_info に倒れる）

判断根拠の深さ・推測ソースの明示・confidence 設定理由の網羅性、いずれも **Sonnet が圧倒的に優位**。

---

## 観点 C-3：推測補強の発生頻度

Haiku は **構造を守れない 7 件 + Schema 違反 1 件** で計 8 件が ai_analysis_notes 取得不能のため、Sonnet 側で観察された「メールアドレス・ローマ字併記からの推測補強」の比較は **データ不足**。

成功した 7 件の業務名刺で見ると：

- Haiku は phonetic_name に推測値を入れる例あり（confidence: mid 寄り）
- ただし Sonnet のような「メールアドレス t-suzuki から姓 SUZUKI 確認」の明示的補強は **Haiku では未観察**

---

## 観点 C-4：条件付き 2 回 OCR の効果

| 指標 | Sonnet 4.6 | Haiku 4.5 |
|---|---|---|
| orientation != normal の BC | 11 | 検出範囲狭く、card_image SHA 不一致 10 件で示唆あり |
| rj1 → rj2 で氏名修正された BC | 6 + 偽陽性救済 1 件 | データ不足（Haiku 側の rj2 が cards[0] 不正で多数失敗） |

**Haiku の方で 2 回目 OCR が失敗するケースが多い**ことが card_image SHA 不一致から推測されます。

---

## 観点 C-5：confidence 分布の比較

| 指標 | Sonnet 4.6 | Haiku 4.5 |
|---|---|---|
| CFC 総数 | **113** | **38** |
| mid 件数 | 28 | 33 |
| low 件数 | **85** | **5** |
| mid : low 比率 | 25% : 75% | **87% : 13%** |

**Haiku は low をほとんど出さず、mid に集中させる傾向**。これは「OCR の不確実性を細かく表現できていない」可能性。一方 Sonnet は仕様書 §1.4 の 3 値基準（high/mid/low）を細やかに使い分けている。

特に **phonetic_name**：Sonnet では 12 件すべて low（§1.6 推測許容ルール）、Haiku は phonetic_name の CFC レコード自体が少ない（Contact 7 件 × 平均 5 件 ≒ 35 件以下、Sonnet 113 件の 1/3）。

---

## 観点 C-6：橋渡し 4 件・JSON Schema 準拠

| 指標 | Sonnet 4.6 | Haiku 4.5 |
|---|---|---|
| 8 ブロック構造の遵守 | **23/23 全件** | **16/23（70%）** |
| Schema validation 通過 | 23/23 | **15/23（65%）**（1 件は明確な Schema 違反） |
| **cards[0] が dict** | **23/23** | **16/23**（7 件で非 dict）|
| full_name 出力なし | 23/23 ✓ | 該当 16 件中 16 ✓（成功分のみ） |
| 橋渡し 4 件のマッピング | 全件正常動作 | 成功 7 件で動作（生成 Contact ベース） |

**Haiku の決定的弱点**：

- **23 BC 中 7 件（30%）で JSON 構造を破る**（cards[0] が dict でない出力）
- 1 件で `'sns' was unexpected` Schema 違反（プロンプトに `sns` が contact 配下にあると書いてあっても、Haiku は構造を逸脱）

これは v1.6.0 の `additionalProperties: false` 厳格運用との相性が悪い。

---

## 観点 C-7：トークン使用量・コスト比較

| 指標 | Sonnet 4.6 | Haiku 4.5 |
|---|---|---|
| API 呼び出し回数 | 34 | 33 |
| input_tokens 合計 | 326,249 | 317,975 |
| output_tokens 合計 | 52,525 | 36,417 |
| **コスト合計（USD）** | **$1.7666** | **$0.5001** |
| Sonnet/Haiku コスト比 | – | **3.53x**（Haiku は約 1/3.5） |
| business_card 1 件あたりコスト | $0.0981 | $0.0714 |
| **成功 1 件あたりの実効コスト** | $0.0981（100% 成功） | **$0.0714（39% 成功）** |

円換算（1 USD = 150 円仮定）：Sonnet $1.77 ≒ **265 円**、Haiku $0.50 ≒ **75 円**（**約 1/3.5 のコスト**）。

ただし **「コスト 1/3.5 だが Contact 生成数も 7/18 ≒ 39%」のため、業務側で再 OCR や手入力で残り 61% を補う必要があり、トータルコストはむしろ Haiku の方が割高**になる可能性が高いです。

---

## 観点 C-8：総合評価と推奨

### 同一名刺の出力対比（5 件抜粋、業務影響の典型例）

| 画像 | Sonnet | Haiku | 評価 |
|---|---|---|---|
| 画像 A 系 ci=0 | 山田 太郎 ✓ | **insufficient_info**（Schema 違反） | Haiku が完全失敗 |
| 画像 C 系 ci=5（中村 太郎） | 中村 太郎 / 株式会社サンプル ✓ | **鈴木 美咲 / サンプルソリューション株式会社**（全別人） | Haiku は **ハルシネーション** |
| 画像 D 系 ci=6（渡辺 花子） | 渡辺 花子 / 都道府県自動車整備振興会 | **佐藤 太一 / 株式会社サンプルテック**（全別人） | Haiku は **ハルシネーション** |
| 画像 A 系 ci=5 | 田中 健二 ✓ | 高橋 健二（誤読、Sonnet 時の Phase 3B rj1 段階と同じ誤読） | Haiku は 2 回目 OCR で正読できず |
| 画像 D 系 ci=5 | 伊藤 三郎 / サンプルショップ大水 | 伊藤 三郎 / サンプルショップ大水 | 一致度高い（軽微な誤読） |

### Haiku 4.5 を本番モデルに採用できるか

**判定：採用不可（No）**

**理由（コード君A 所見）**：

1. **Schema 遵守能力の決定的不足**：v1.6.0 の 8 ブロック構造 + `additionalProperties: false` の厳格 Schema を守れず、23 BC 中 7〜8 件（30%）で構造違反。これは json_parser の防御実装で吸収できる範囲を超える致命的差

2. **Contact 生成成功率 39%**：実画像ベースで残り 61% は手入力負担になる。コスト 1/3.5 のメリットを業務工数で帳消し以上に失う

3. **ハルシネーションリスク**：画像 C 系 ci=5（中村 太郎 → 鈴木 美咲）、画像 D 系 ci=6（渡辺 花子 → 佐藤 太一）のように、**実在しない氏名・組織を生成**するケースが複数。Sonnet では「読み取り困難 → confidence low」で抑制されるが、Haiku では断定的に誤読

4. **ai_analysis_notes の質的劣化**：Sonnet の判断根拠の深さ・推測ソース明示・補正説明は、後段の人手レビュー（CFC mid/low 確認）で価値が大きい。Haiku は平均文字数半分、7 件で取得不能のため、人手レビューでも判断材料不足

5. **confidence の細やかさ欠落**：low が極端に少なく mid 集中。「読み取れたが確信ない」と「推測込み」の区別がつかず、人手レビューの優先順位付けが困難

### 条件付き採用案（例：日常 Haiku + 難画像 Sonnet フォールバック）

**実装可能だが推奨しない**理由：

- Haiku が失敗するかどうかは事前判定不可（同じ画像でも day-by-day で安定しない可能性）
- 失敗時に Sonnet で再実行すると結局両方のコストがかかる
- アーキテクチャ複雑化に対する得が薄い

---

## 結論

**Sonnet 4.6（`claude-sonnet-4-6`）継続採用、Haiku 4.5 は本番不可。**

将来 Anthropic から Haiku 系の大幅改善モデルがリリースされた時に再評価する論点として持ち越し。本検証データ（実名版 JSON 2 件 + 完全 Markdown 1 件）は `freegroup2_local_archive/` 配下にローカル保管し、次回モデル比較時の基準として活用する。
