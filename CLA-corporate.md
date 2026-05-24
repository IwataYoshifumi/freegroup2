# FreeGroup2 法人コントリビュータライセンス契約（CCLA）v1.0（草案）

**Corporate Contributor License Agreement**

**バージョン**：1.0  
**ライセンサー**：株式会社ネットワーク東海（代表者：岩田好史）  
**対象ソフトウェア**：FreeGroup2  
**準拠法**：日本法／**専属管轄**：名古屋地方裁判所  
**改訂日**：2026年5月24日（v1.0 草案・改訂）  

> 本書は士業（弁護士・行政書士）による最終チェック前の草案である。法的拘束力を持つ正式版は、士業のレビューを経て確定する。  
> 本契約は日本語版を正本とし、末尾に併記する英文版（English Version）は参考訳とする。両者に齟齬がある場合は日本語版が優先する。

### 改訂履歴

| 版 | 日付 | 主な内容 |
|---|---|---|
| v1.0 草案 | 2026年5月23日 | 初版作成 |
| v1.0 草案（2026-05-24改訂） | 2026年5月24日 | リーガルチェック依頼書 v1.1 rev5 のAIレビュー指摘を踏まえた条文修正：第5.4条新設（運用ルールの位置づけ）、第6.5条・第6.6条新設（権利保有の表明保証＋フォールバック）、第7条の項分け・人格権不行使の強化（損害担保＋本人同意取得義務）。 |

---

## 第1条 前文（目的・適用範囲）

本契約は、株式会社ネットワーク東海（以下「ライセンサー」という。）が開発・提供するソフトウェア「FreeGroup2」（以下「本ソフトウェア」という。）の本体リポジトリに対し、法人（以下「本法人」という。）に所属する者が貢献を行う場合における、本法人とライセンサーとの間の権利関係を定めるものである。

本法人は、その所属者が本ソフトウェア本体リポジトリへ貢献を行うに先立ち、本契約に同意し、これを締結しなければならない。本契約は、本法人が申告したGitHubアカウントから行われる一切の貢献に適用される。

## 第2条 定義

本契約において、次の各号に掲げる用語の意義は、当該各号に定めるところによる。

1. **本ソフトウェア**：ライセンサーが「FreeGroup2」の名称で提供するソフトウェアおよびそのすべてのバージョンをいう。
2. **本体リポジトリ**：ライセンサーが本ソフトウェアの正本として管理するソースコードリポジトリをいう。
3. **貢献**：本法人の申告アカウントから本体リポジトリに対し、プルリクエストの形式で提出される一切の成果物をいう。具体的な範囲は第6条第1項に定める。
4. **貢献された著作物**：貢献のうち、ライセンサーがプルリクエストを本体リポジトリのメインブランチにマージしたものをいう。
5. **ライセンサー**：株式会社ネットワーク東海（代表者：岩田好史）をいう。
6. **ライセンス本文**：ライセンサーが定める「FreeGroup2 License v1.0」（本ソフトウェアの利用条件を定める文書）をいう。
7. **登録事業者**：ライセンス本文に基づきライセンサーに登録を行い、商用利用を行う者をいう。
8. **申告アカウント**：本法人がライセンサーに申告したGitHubアカウントをいう。

## 第3条 法人の資格

本法人は、本契約の締結時において、登録事業者であることを要する。本法人は、自らがこの資格を満たすことを表明し、保証する。

## 第4条 申告者の権限の表明保証

本契約を本法人を代表して締結する者（以下「申告者」という。）は、本契約の締結および申告アカウントの申告について、本法人を代表する権限を有することを表明し、保証する。ライセンサーは、申告者に対し、これを超える権限の確認（登記事項証明書、印鑑証明書、実印その他の資料の提示等）を求めない。

## 第5条 申告アカウントの定義・追加・削除

### 5.1 申告アカウントによる対象範囲

本法人の申告アカウントから行われた貢献は、すべて本契約の対象とする。本法人は、所属者の範囲を個別に定義することを要しない。

### 5.2 追加・削除

本法人は、申告するGitHubアカウントを、いつでもライセンサーに通知することにより追加し、または削除することができる。

### 5.3 削除の効果

削除されたGitHubアカウントから、削除日以前に行われた貢献に係る本契約の効力は、当該削除によって何らの影響も受けない。退職その他の事由により所属者でなくなった者についても、本項の定めるところによる。

### 5.4 運用ルールの位置づけ

申告アカウント管理に関する運用ルールは、本契約の一部を構成する。本契約と運用ルールに齟齬がある場合は、本契約を優先する。ライセンサーは、運用ルールを改訂したときは、合理的な期間（30日を目安とする）の事前通知をもって本法人に通知する。

## 第6条 著作権の譲渡

### 6.1 譲渡の対象

本契約に基づく譲渡の対象は、貢献された著作物に限り、次の各号に掲げるものとする。

1. プルリクエストによりマージされたソースコード
2. プルリクエストによりマージされたドキュメント（`docs-site/` 配下等を含む。）
3. プルリクエストによりマージされたコミットメッセージ
4. プルリクエストの本文（プルリクエストの説明文）
5. プルリクエストに紐づくコメント（コードレビュー上のコメントを含む。）

イシュー、ディスカッション、外部のチャットツール（Slack、Discord 等）その他本体リポジトリのプルリクエスト外で行われた発言は、譲渡の対象に含まない。

### 6.2 譲渡への同意（CLA締結時点）

本法人は、本契約の締結をもって、申告アカウントから今後行われる貢献について、ライセンサーが当該貢献に係るプルリクエストを本体リポジトリのメインブランチにマージした時点で、当該貢献に係る著作権をライセンサーに譲渡することに同意する。

### 6.3 譲渡の効果発生（マージ時点）

著作権譲渡の効果は、ライセンサーが当該貢献に係るプルリクエストをメインブランチにマージした時点で発生する。マージされていないプルリクエストに係る貢献は、譲渡の対象とならない。

### 6.4 譲渡される権利の範囲

前各項に基づき本法人がライセンサーに譲渡する著作権には、著作権法第27条（翻訳権、翻案権等）および第28条（二次的著作物の利用に関する原著作者の権利）に定める権利を含む。

### 6.5 権利保有の表明保証

本法人は、申告アカウントから行われた各貢献について、その著作権を所属者から適法に取得しているか、または職務著作その他の事由により自ら原始的に保有していることを表明し、保証する。

### 6.6 権利不保有時のフォールバック

万一、本法人が当該著作権を保有していない場合、本法人は、所属者をして当該著作権をライセンサーに直接譲渡させ、または自らこれを取得したうえでライセンサーに譲渡する義務を負う。

## 第7条 著作者人格権の不行使

### 7.1 不行使

本法人は、その所属者をして、ライセンサーおよびライセンサーから権利を承継し、または許諾を受けた者に対し、貢献された著作物に係る著作者人格権を行使させないものとする。

### 7.2 損害担保および本人同意の取得

本法人は、所属者が前項に反して著作者人格権を行使したことによりライセンサーに生じた一切の損害を補償する。本法人は、申告アカウントの所属者から、本条に定める著作者人格権の不行使について同意を取得するものとし、ライセンサーの求めに応じてその取得状況を説明する。

## 第8条 表明保証

### 8.1 表明保証事項

本法人は、申告アカウントから行われた各貢献について、次の各号を表明し、保証する。

1. 貢献された著作物は、当該貢献を行った所属者のオリジナルの創作であること。
2. 貢献された著作物が、第三者の著作権、特許権、商標権、営業秘密その他一切の権利を侵害しないこと。
3. 貢献された著作物に第三者の著作物が含まれる場合は、その旨および当該第三者の著作物のライセンス条件を、当該貢献に係るプルリクエストの本文において明示すること。

### 8.2 補償

前項の表明保証に違反したことに起因してライセンサーが第三者から請求、訴訟その他の異議を受けた場合、本法人は、ライセンサーに生じた損害（合理的な弁護士費用を含む。）を補償するものとする。

## 第9条 譲渡対価

本法人は、本契約に基づく著作権譲渡の対価として、ライセンス本文に定めるコントリビュータ割引その他ライセンサーが提供する利益を受領する。本法人は、当該対価をもって本契約に基づく著作権譲渡の対価が支払われたものとし、別途の金銭的対価を請求しない。

## 第10条 有効期間・解除

### 10.1 有効期間

本契約は、本法人が締結した時点で効力を生じ、本法人の申告アカウントから本ソフトウェア本体リポジトリへの貢献が行われ得る限り存続する。

### 10.2 解除

本法人は、ライセンサーに通知することにより、将来に向かって本契約を解除することができる。解除は、解除日以前にマージされた貢献に係る著作権譲渡その他本契約に基づき既に生じた効果に何らの影響を及ぼさない。

## 第11条 準拠法・専属管轄

本契約は、日本法に準拠し、日本法に従って解釈される。本契約に関連して生じる一切の紛争については、名古屋地方裁判所を第一審の専属的合意管轄裁判所とする。

## 第12条 その他

### 12.1 本契約の変更

ライセンサーは、本契約を改訂することができる。改訂後の契約には新たな版番号を付す。本法人の申告アカウントから改訂後に行われる貢献には、その時点の最新版の本契約が適用される。

### 12.2 通知方法

本契約に基づく通知（申告アカウントの追加・削除を含む。）は、ライセンサーの定める方法（本体リポジトリのプルリクエスト、CLAボットを通じた電子的手続その他ライセンサーが指定する手段）により行う。

### 12.3 言語

本契約は日本語版を正本とする。末尾に併記する英文版は参考訳であり、日本語版と英文版との間に齟齬が生じた場合は、日本語版が優先する。

---

## 署名欄（電子的署名／CLAボット対応）

本契約は、本法人を代表する申告者が、ライセンサーの定める方法により申告アカウントを申告し、CLAボットを通じて電子的に同意の意思表示を行うことにより締結される。以降の貢献については、CLAボットが申告アカウントに基づき署名状況を自動的に判定する。

| 項目 | 内容 |
|---|---|
| 本法人の名称 | （申告） |
| 申告者氏名・役職 | （申告） |
| 登録事業者ID（該当する場合） | （申告） |
| 申告アカウント一覧 | （申告・追加削除可） |
| 同意日時 | （CLAボット記録） |

---
---

# FreeGroup2 Corporate Contributor License Agreement (CCLA) v1.0 (Draft)

**Version**: 1.0  
**Licensor**: Network Tokai Co., Ltd. (Representative: Yoshifumi Iwata)  
**Software**: FreeGroup2  
**Governing Law**: Laws of Japan / **Exclusive Jurisdiction**: Nagoya District Court  
**Revision Date**: May 24, 2026 (v1.0 Draft, Revised)

> This document is a draft prepared prior to final review by legal professionals (attorney / administrative scrivener). The legally binding final version will be confirmed after such review.  
> The Japanese version is the authoritative text. This English version is a reference translation. In the event of any discrepancy, the Japanese version shall prevail.

### Revision History

| Version | Date | Summary |
|---|---|---|
| v1.0 Draft | May 23, 2026 | Initial draft. |
| v1.0 Draft (Revised May 24, 2026) | May 24, 2026 | Revisions reflecting AI review feedback from the Legal Review Request v1.1 rev5: new Article 5.4 (positioning of operational rules), new Articles 6.5 and 6.6 (representation and warranty of rights ownership, and fallback for cases without ownership), and reorganization of Article 7 with strengthened non-exercise of moral rights (damages indemnification and obligation to obtain consent from affiliated persons). |

---

## Article 1 Preamble (Purpose and Scope)

This Agreement governs the rights between Network Tokai Co., Ltd. ("Licensor") and a corporation ("Corporation") in cases where a person affiliated with the Corporation makes contributions to the main repository of the software "FreeGroup2" ("Software") developed and provided by the Licensor.

Before its affiliated persons make any contribution to the Software's main repository, the Corporation must agree to and enter into this Agreement. This Agreement applies to every contribution made from the GitHub accounts declared by the Corporation.

## Article 2 Definitions

In this Agreement, the following terms have the meanings set forth below.

1. **Software**: the software provided by the Licensor under the name "FreeGroup2" and all of its versions.
2. **Main Repository**: the source code repository maintained by the Licensor as the authoritative source of the Software.
3. **Contribution**: any work product submitted to the Main Repository from a Declared Account of the Corporation in the form of a pull request. Its specific scope is set out in Article 6.1.
4. **Contributed Work**: a Contribution that the Licensor has merged into the main branch of the Main Repository.
5. **Licensor**: Network Tokai Co., Ltd. (Representative: Yoshifumi Iwata).
6. **License Terms**: the "FreeGroup2 License v1.0" established by the Licensor.
7. **Registered Business**: a party that has registered with the Licensor under the License Terms and conducts commercial use.
8. **Declared Account**: a GitHub account that the Corporation has declared to the Licensor.

## Article 3 Eligibility of the Corporation

The Corporation must be a Registered Business at the time of entering into this Agreement. The Corporation represents and warrants that it satisfies this eligibility requirement.

## Article 4 Representation and Warranty of the Declarant's Authority

The person who enters into this Agreement on behalf of the Corporation ("Declarant") represents and warrants that they have authority to represent the Corporation with respect to entering into this Agreement and declaring the Declared Accounts. The Licensor shall not require any confirmation of authority beyond this (such as the presentation of a certificate of registered matters, a seal registration certificate, a registered seal, or other materials).

## Article 5 Declared Accounts: Definition, Addition, and Removal

### 5.1 Scope by Declared Accounts

All Contributions made from the Corporation's Declared Accounts are subject to this Agreement. The Corporation is not required to individually define the scope of its affiliated persons.

### 5.2 Addition and Removal

The Corporation may at any time add or remove the GitHub accounts it declares by notifying the Licensor.

### 5.3 Effect of Removal

The effect of this Agreement with respect to Contributions made from a removed GitHub account on or before the date of removal is not affected in any way by such removal. The same applies to persons who cease to be affiliated persons due to resignation or any other reason.

### 5.4 Positioning of Operational Rules

The operational rules concerning the management of Declared Accounts form part of this Agreement. In the event of any discrepancy between this Agreement and the operational rules, this Agreement shall prevail. When the Licensor revises the operational rules, the Licensor shall notify the Corporation with reasonable prior notice (30 days as a guideline).

## Article 6 Assignment of Copyright

### 6.1 Subject of Assignment

The subject of assignment under this Agreement is limited to Contributed Works, comprising the following.

1. Source code merged via a pull request.
2. Documentation merged via a pull request (including content under `docs-site/`).
3. Commit messages merged via a pull request.
4. The body (description) of the pull request.
5. Comments associated with the pull request (including code review comments).

Statements made in issues, discussions, external chat tools (such as Slack or Discord), or otherwise outside the pull requests of the Main Repository are not subject to assignment.

### 6.2 Consent to Assignment (at the Time of Execution)

By entering into this Agreement, the Corporation consents that, for any Contribution made thereafter from a Declared Account, the copyright in such Contribution shall be assigned to the Licensor at the moment the Licensor merges the pull request for such Contribution into the main branch of the Main Repository.

### 6.3 Effect of Assignment (at the Time of Merge)

The effect of the copyright assignment arises at the moment the Licensor merges the relevant pull request into the main branch. A Contribution under a pull request that has not been merged is not subject to assignment.

### 6.4 Scope of Assigned Rights

The copyright assigned by the Corporation to the Licensor under the preceding paragraphs includes the rights set out in Articles 27 and 28 of the Copyright Act of Japan.

### 6.5 Representation and Warranty of Rights Ownership

For each Contribution made from a Declared Account, the Corporation represents and warrants that it has lawfully obtained the copyright in such Contribution from the affiliated person, or that it originally owns such copyright by reason of work made for hire or other grounds.

### 6.6 Fallback in Case of Non-Ownership

In the unlikely event that the Corporation does not own such copyright, the Corporation shall be obligated either to cause the affiliated person to assign such copyright directly to the Licensor, or to first acquire such copyright itself and then assign it to the Licensor.

## Article 7 Non-Exercise of Moral Rights

### 7.1 Non-Exercise

The Corporation shall cause its affiliated persons not to exercise any moral rights in the Contributed Work against the Licensor or any party that succeeds to or is licensed the rights from the Licensor.

### 7.2 Damages Indemnification and Obtaining Consent from Affiliated Persons

The Corporation shall indemnify the Licensor for any and all damages incurred by the Licensor as a result of an affiliated person exercising moral rights in breach of the preceding paragraph. The Corporation shall obtain the consent of the affiliated persons of its Declared Accounts to the non-exercise of moral rights set out in this Article, and shall explain the status of such consent at the Licensor's request.

## Article 8 Representations and Warranties

### 8.1 Matters Represented and Warranted

For each Contribution made from a Declared Account, the Corporation represents and warrants the following.

1. The Contributed Work is the original creation of the affiliated person who made the Contribution.
2. The Contributed Work does not infringe any copyright, patent right, trademark right, trade secret, or any other right of any third party.
3. Where the Contributed Work contains a third party's work, the Corporation shall expressly state that fact and the license terms of such third party's work in the body of the relevant pull request.

### 8.2 Indemnification

If the Licensor receives any claim, suit, or other objection from a third party arising from a breach of the representations and warranties in the preceding paragraph, the Corporation shall indemnify the Licensor for the damages incurred by the Licensor (including reasonable attorneys' fees).

## Article 9 Consideration for Assignment

As consideration for the copyright assignment under this Agreement, the Corporation receives the contributor discount and other benefits provided by the Licensor as set out in the License Terms. The Corporation agrees that such consideration constitutes payment of the consideration for the copyright assignment under this Agreement, and shall not claim any separate monetary consideration.

## Article 10 Term and Termination

### 10.1 Term

This Agreement takes effect upon execution by the Corporation and remains in effect for as long as Contributions may be made from the Corporation's Declared Accounts to the Software's Main Repository.

### 10.2 Termination

The Corporation may terminate this Agreement prospectively by notifying the Licensor. Termination does not affect the copyright assignment of, or any other effect already arisen under this Agreement with respect to, Contributions merged on or before the date of termination.

## Article 11 Governing Law and Exclusive Jurisdiction

This Agreement is governed by and construed in accordance with the laws of Japan. The Nagoya District Court shall have exclusive jurisdiction as the court of first instance over any dispute arising in connection with this Agreement.

## Article 12 Miscellaneous

### 12.1 Amendment

The Licensor may revise this Agreement. A revised Agreement shall bear a new version number. Contributions made from the Corporation's Declared Accounts after a revision are governed by the latest version of this Agreement in effect at that time.

### 12.2 Method of Notice

Notices under this Agreement (including the addition or removal of Declared Accounts) shall be given by the method designated by the Licensor (a pull request in the Main Repository, an electronic procedure via the CLA bot, or any other means designated by the Licensor).

### 12.3 Language

The Japanese version of this Agreement is the authoritative text. This English version is a reference translation, and in the event of any discrepancy between the Japanese and English versions, the Japanese version shall prevail.

---

## Signature (Electronic Signature / CLA Bot)

This Agreement is executed when the Declarant representing the Corporation declares the Declared Accounts by the method designated by the Licensor and expresses consent electronically via the CLA bot. For subsequent Contributions, the CLA bot automatically determines the signature status based on the Declared Accounts.
