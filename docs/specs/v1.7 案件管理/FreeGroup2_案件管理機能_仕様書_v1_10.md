# FreeGroup2 案件管理（Deal）・活動記録（Activity）・会社（Company）機能 仕様書 v1.10

**作成日**：2026-09-09
**位置づけ**：v1.9に対し、2件のレビューで判明した`DealPersonInline.has_delete_permission()`のDjango API誤用を修正した版。v1.9で追加した`DealPersonInline`のコード例は、単体登録`DealPersonAdmin`と同じ「`obj`は対象インスタンス」という前提のまま`has_delete_permission()`をコピーしていたが、`InlineModelAdmin.has_delete_permission(request, obj=None)`に渡される`obj`は実際には編集中の親オブジェクト（`Deal`）であり、子行（`DealPerson`）ではない。`obj.is_primary`へのアクセスは`Deal`インスタンスに対して行われるため、`DealPerson`をインラインとして組み込んだ`DealAdmin`の変更画面を開いた瞬間に`AttributeError`でクラッシュする欠陥だった。行単位の削除拒否をインラインで実現するAPIはDjangoに存在しないため、`DealPersonInline`は`can_delete = False`でインライン全体からの削除を禁止し、削除は`DealPersonAdmin`（単体登録、正しく機能する）または`DealDeletePersonView`（`delete_deal_person()`経由）に一本化する方針に改めた（§7.6.1）。
**スコープ**：Deal・Activity・Attachment・DealPerson/DealUser・ActivityPerson/ActivityUser・Company・CompanyDuplicateCandidate。

> **本仕様書と実装指示書の分担**（★v0.9で明示）
> 本仕様書は**データモデル・業務ルール・権限設計**を正本とする。マイグレーションの作成順序、admin登録、URLネームスペース、docstring規約、BackNavigatorの起点、テストの置き場と必須項目、CreateViewの画面構成、指示書の分割ステップといった**実装の段取り**は、本仕様書には含めず実装指示書側に記載する（§9.2に一覧）。URL・View名の正本は既存の`URL一覧表`への追記として別途作成する（CLAUDE.md §3）。

## v1.9からの主な変更点（★DealPersonInlineのDjango API誤用を修正）

v1.9で`DealPersonInline`に追加した行単位の削除拒否コードが、Djangoの`InlineModelAdmin.has_delete_permission()`のAPI仕様を誤解していたことがレビューで判明した。

- **`InlineModelAdmin.has_delete_permission(request, obj=None)`の`obj`は、インラインの行（`DealPerson`）ではなく親オブジェクト（`Deal`）である**。これは`get_formset(request, obj)`が「このDealに対してインライン全体の削除チェックボックスを一括で出してよいか」を判定するために呼ぶ設計であり、行単位の削除可否を判定するフックはインラインには存在しない
- v1.9のコードは`obj.is_primary`という判定を書いていたが、`obj`が`Deal`インスタンスであるため、この属性は存在せず`AttributeError`で管理画面がクラッシュする
- 対策として、インライン側での行単位の削除制御自体を諦め、**`can_delete = False`でインライン全体の削除を禁止**する方針に改めた。削除は`DealPersonAdmin`（単体登録、`obj`が正しく`DealPerson`インスタンスになるため当初のコードのまま機能する）または業務画面（`DealDeletePersonView`、`delete_deal_person()`経由）に一本化する。「インラインから主担当以外の参加者だけ直接削除したい」という利便性よりも、admin経由の事故防止を優先する判断であり、`AttachmentAdmin`が採用した「admin経由の削除は塞ぎ、専用経路に一本化する」という既存パターンとも揃える

## v1.8からの主な変更点（★admin経由の主担当削除ガード漏れを解消）

v1.8で`DealPerson`の通常削除に対するガード（`delete_deal_person()`）を新設したが、Django admin経由の削除にはこのガードが及ばないことがレビューで判明した。§7.6.1の保護対象表は`Deal.owner`・`Activity.is_archived`・`Company.status`／`merged_into`・`Attachment`の4モデルについては具体的な`ModelAdmin`のコード例まで示していたが、同じ表で「保護が必要」と一覧していた`DealPerson.is_primary`だけ、対応する`DealPersonAdmin`のコード例が存在しなかった。

`DealPersonAdmin`（`DealAdmin`にインラインとして組み込む場合は`DealPersonInline`にも同様に）、以下2つの保護を追加した。

- **トグル防止**：`is_primary`を常に`readonly_fields`に含め、admin経由の直接変更を禁止（付け替えは`reassign_deal_primary_person()`経由のみ）
- **削除防止**：`has_delete_permission()`で`is_primary=True`の行の個別削除を拒否。**一括削除（「選択した項目を削除」）は`has_delete_permission(obj=...)`を1件ずつ経由しないため**、`delete_queryset()`のオーバーライドも別途必須とした

あわせて、実装時にコード君が踏みやすい落とし穴として、`DealForm.Meta.fields`に`primary_person`を誤って含めると`FieldError`になる点、`DealCreateView.form_valid()`で`create_deal_with_primary_person()`呼び出し前に自前で`deal.save()`すると二重保存になる点を§9.2に追記した（§2.1のコード例自体は元から正しかったため、実装指示書側の注意喚起として追加）。

## v1.7からの主な変更点（★`primary_person`廃止に伴う実装レベルの穴を4点解消）

v1.7で`Deal.primary_person`を廃止し`DealPerson.is_primary`に一本化したが、この転換自体が新たに生んだ実装レベルの穴が2点、前バージョンから持ち越しだった穴が2点、レビューで判明した。

- **①Deal作成時の「相手方最低1名」保証が実装されていなかった（最重要）**：§2.1の`DealCreateView.form_valid()`コード例が、`create_deal_with_primary_person()`（§2.6.1）を一度も呼ばず、存在しない主担当DealPersonを検索するだけになっていた。この状態でコード君が実装すると、`company`自動補完は永久に発火せず、相手方0名のDealが普通に作成できてしまう。さらに§9.2の「CreateViewは本体のみ登録→サブフォームで追加という2段構え」という一般規約と、同じ§9.2内の「Deal作成フォームは相手方選択を必須にする」という指示が矛盾していた。**方針を確定**：Deal作成のみ「相手方選択を同一フォーム・同一トランザクションで完結させる1段階フロー」とする例外を明記し、§2.1のコード例を実際に`create_deal_with_primary_person()`を呼ぶ形に修正した
- **②主担当DealPersonが通常の削除操作で削除できてしまう**：`unique_deal_primary_person`制約は「主担当が2人以上になる」ことは防ぐが、「主担当が0人になる」ことは防がない。`delete_deal_person()`を新設し、`is_primary=True`の行の削除を拒否するガードを追加した（§2.6.1）
- **③（前バージョンから持ち越し）`Company.merged_into`の`on_delete`指定漏れ**：`Person.merged_into`と同じ`SET_NULL`を明記した（§6.2）
- **④（前バージョンから持ち越し）§2.4の存在しない画面遷移への言及**：「フォロー漏れ検出画面からDeal作成へ遷移する動線」という一文を削除した。たんたんの判断により、この動線自体は「必要になったら作る」として今回はスコープ外とした

## v1.6からの主な変更点（★`Deal.primary_person`廃止・呼称整理）

たんたんとの壁打ちで、`Deal.primary_person`（案件の相手方の主担当）を本体の単一FKとして持つ設計そのものを見直した。相手方は実際には複数人が関与するのが普通で、`DealPerson`が既にその全員を保持しているにもかかわらず、代表1名だけを別枠のFKで持つ二重構造になっていた。v1.6で発生した事故（`DealPerson.is_primary`の独自追加）は、この二重構造が生む「同じ概念を表現しうる場所が2つある」という問題の一例に過ぎず、v1.6の対応（追加を禁止する明記）は対症療法だった。

今回、`Deal.primary_person`を完全に廃止し、`DealPerson.is_primary`（部分ユニーク制約付き）に一本化した（§2.6.1で全面改訂）。これにより「2箇所に表現しうる」という構造上の問題そのものが解消され、v1.6の禁止事項は撤回した（§5.2）。あわせて、`Deal.owner`の表示ラベルを「案件オーナー」に変更し、`DealUser.role=primary`（表示ラベル「主担当」）・`DealPerson.is_primary`（同じく表示ラベル「主担当」、客先側なので実務上の混同はないと判断）との語彙衝突を整理した。

本機能はまだ実運用に投入されていないため、既存Dealデータの丁寧な移行マイグレーションは不要とし、開発DBの再作成で対応する方針とした（CLAUDE.md §4）。

## v1.5からの主な変更点（★実装レビューで判明した2点を明記）

Step 1実装（`feature/v1.7-deals-activities-company-step1`）のレビュー中、`DealPerson`に`is_primary`（主担当フラグ）フィールドが独自に追加されるコミットが見つかった。§5.1／§5.2は元々「`Deal.primary_person`が正」と定めていたが、`DealPerson`側に主担当フラグを持たせてはならないという禁止事項までは明記していなかったため、再発防止のため明記した（§5.1）。

あわせて、`can_edit_deal`が`Contact.can_edit_contact`と異なり`created_by`を編集権限の判定に含めない設計について、たんたんとの壁打ちで「案件の担当から外れた作成者は編集できなくなる方が実態に合っている」という結論が出たため、意図的な非対称であることを§7.1に明記した（判定関数のコード自体に変更はない）。

## v1.4からの主な変更点（★実装指示書向け申し送り1点追加）

ジェミニ君のレビューで、`calculate_company_match()`（§6.5.1）が10個の引数を位置引数で受け取る形になっており、コード君が実装時に引数の順序を誤る（例：`domain_a`の位置に`organization_b`を渡す）リスクが指摘された。仕様書本体のコード例は変更せず、§9.2の実装指示書向け申し送りに「引数の多い関数はキーワード引数で呼ぶこと」を1点追加した。

## v1.3からの主な変更点（★バージョン番号の訂正のみ）

内容修正後は必ずバージョンを変える運用ルールに従い、レビュー反映済みの内容を正式にv1.4として確定した。内容面の変更点は「v1.2からの主な変更点」を参照（実質的な差分は以下参照）。

## v1.2からの主な変更点（★URLの重み付け見直し・正規化ロジック新設）

たんたんとの壁打ちにより、名刺記載のURLは公式サイトを指す確度が高く、重複検出における裏付けとして会社名・ドメインと同格に扱うべきという判断に至った。これに伴いスコア表・ランク判定を再改訂した。

- **URLの配点を20点から100点に引き上げ、ドメインも120点から100点に統一**（§6.5.1）。会社名・ドメイン・URLの3項目を対等な主軸とした
- **`normalize_website()`を新設**（§6.5.1）：URLを「ドメイン＋パスの最初のセグメント」に正規化してから比較する。共有ホスティング（例：`example.com/companyA`のように1つのドメイン配下に複数契約者が同居する形式）で、ドメイン部分だけの比較だと無関係な会社同士が誤って一致してしまう問題に対処した。名刺記載のURLは通常トップページのみのため、このロジックで十分機能する
- **ランク判定の必須条件を「会社名 AND ドメイン」から「会社名 AND (ドメイン OR URL)」に一般化**（§6.5.2）：ドメインとURLのどちらか一方が会社名と一致していれば`exact_match`／`possible_high`に到達できるようにした
- **`possible_mid`／`possible_low`の判定方法を統合**：v1.1〜v1.2は「ドメイン一致ならmid、会社名一致ならlow」と一致した項目の種類で振り分けていたが、「会社名かドメインのどちらか一致していれば、あとはスコア合計だけで判定する」形に変更した（140点を境に mid／low を分岐）。会社名一致の方がドメイン一致より確からしいという直感に反して、v1.2までは会社名一致の方が到達に必要なスコアが高く設定されていた逆転を解消した
- **会社名一致は`exact_match`／`possible_high`の必須条件から外さないことを明記**：ドメイン・URL・電話・住所が全て一致していても会社名が不一致なら自動リンクしない設計を維持した。理由は、これらが全て一致するケースには「グループ会社間で連絡先を共有する別法人」の可能性があり、OCR誤読の救済よりも誤結合の防止を優先したため
- **★レビューで発覚・修正①：`possible_mid`／`possible_low`の必須条件にURLを追加**：当初案ではこの2ランクの必須条件を「会社名 OR ドメイン」のみとしていたが、これは「URLを対等な主軸にする」という本改訂の方針と矛盾する非対称（ドメイン単独一致100点はキューに乗るのに、URL単独一致100点は候補外として何の記録も残らない）を生んでいた。必須条件を「会社名 OR ドメイン OR URL」に修正した
- **★レビューで発覚・修正②：スコア計算・ランク判定・候補保存をつなぐコード例を追加**：`calculate_company_score()`（スコアのみ返す）と`determine_company_rank()`（3つの真偽値を要求する）の間をつなぐコードが一度も示されておらず、真偽値の導出方法が未定義だった。`calculate_company_score()`を`calculate_company_match()`に改称し、スコアと各項目の一致フラグをまとめて返す`CompanyMatchResult`を導入。3関数を実際につなぐ`register_company_candidate()`を新設した
- **`AttachmentAdmin.has_delete_permission()`を仕様書本体（§7.6.1）に格上げ**：v1.0時点でこの項目は実装指示書向けの申し送り（§9.2）にのみ記載されており、他の5フィールド（`Deal.owner`等）と扱いが不統一だった。同じ「Service関数を経由すべき重要操作をadminが迂回できる」性質の問題であるため、§7.6.1の表・コード例に統合した

## v1.1からの主な変更点（★スコア関数の欠陥修正）

v1.1のレビューで見つかった2点を修正した。

- **`calculate_company_score()`のシグネチャを生の値10個を受け取る形に戻す（★重要）**：v1.1では`Company`インスタンスを直接受け取る形に変えたが、これだと**Contact対Companyの照合に使えなくなる**（Contact側の属性名は`org_domain_name`／`org_phone`であり、`domain`／`phone`という属性はContactに存在しないため）。v1.0までの「生の値を渡す」形に戻し、Contact対Company・Company対Companyの両方の呼び出し例を明記した（§6.5.1）
- **`phone`／`address`／`website`の「空→値」穴埋め表現を実態に合わせて修正**：§6.2は3項目とも`domain`と同じ継続的な穴埋めと説明していたが、§6.5.6で「マージ時の引き継ぎは対象外」と決めたため、埋める経路が新規作成時以外に存在しなくなっていた。§6.2の説明を「新規作成時のコピーのみ」に修正し、§6.5.4手順5に実際のコピー処理コード（`create_company_from_contact()`）を追加した

## v1.0からの主な変更点（★Company重複検出のスコア・ランク改訂）

別セッション（v0.7時点をベースに検討）からの引継ぎ指示のうち、**スコアリングとランク判定のロジックのみ**を反映した（Companyフィールド追加・ランク選択肢追加は、スコア・ランクを機能させるために不可分な変更として合わせて反映。`is_generic_email_domain()`漏れ対策のActionLog記録は、指示の対象外として今回は含めない）。

- **Companyに`phone`／`address`／`website`を追加**（§6.2）。重複検出のスコアリング項目として使う。対応するContact側フィールドは`org_phone`／`address`／`website`。いずれも`domain`と同じ「空→値」の一方向穴埋め
- **重複検出のスコア表を2項目から5項目に拡張**（§6.5.1）：ドメイン120点（v1.0までは150点）・会社名100点（同130点）・電話20点・住所20点・URL20点（後者3つは新規）。「会社名一致だけで、実際は同名の別会社だった」というリスクへの対処として、電話・住所・URLを追加の裏付け材料とした
- **ランク判定を3段階から4段階のハイブリッド判定に変更**（§6.5.2）：`possible_low`（会社名一致＋電話等の追加裏付けが必須）を新設。`exact_match`と`possible_high`は同じ必須条件（会社名＋ドメイン一致）だが、追加の裏付けが1つでもあるかどうかでランクが分かれる、一段保守的な設計に変更した
- **`CompanyDuplicateCandidate.rank`の選択肢に`possible_low`を追加**（§6.5.3）
- **`phone`／`address`／`website`はマージ時に引き継がない**（今回のスコープ外である旨を§6.5.6に明記）

## v0.18からの主な変更点

- **Attachmentの実ファイル削除を`post_delete`シグナルに一本化**：v0.18までは`delete_attachment()`関数内で`transaction.on_commit`によりファイル削除を行っていたが、この関数を経由しない削除経路（admin画面からの直接削除、将来のCASCADE削除）ではファイルが孤児として残る。既存の`OriginalImage`等の規約（アーティファクトは`post_delete`シグナルで整合性を保つ）に倣い、シグナルに一本化した。ただしシグナル単体では削除のトランザクションがロールバックされた場合に不整合が生じるため、シグナル内でも`transaction.on_commit`を使う形にした（§4.4.2）
- **実装指示書向けの申し送り事項を追加**：`update_or_create`によるスコア更新、一覧画面のページネーションでのGETパラメータ引き継ぎ、`created_at`／`updated_at`の`auto_now_add`／`auto_now`明示、Attachment実ファイル削除のシグナル一本化（§9.2）

## v0.17からの主な変更点

- **`readonly_fields`の静的指定がDeal新規作成を不可能にする問題を修正**：§7.6.1で追加した`readonly_fields`は、Djangoでは追加（Add）・変更（Change）の両フォームに同じリストが適用される。`Deal.primary_person`は必須項目（デフォルト値なし）のため、常にreadonlyにするとadmin画面からDealを新規作成できなくなっていた。`get_readonly_fields()`で`obj is None`（追加時）は制限しない形に修正した（§7.6.1）
- **`Company.status`に`default=Status.ACTIVE`を追加**：`Deal.stage`にはデフォルト値があるのに`Company.status`にはなく、admin等での新規作成時に必須項目未入力になる懸念があった（§6.2）
- **`CompanyDuplicateCandidate.reviewed_by`／`reviewed_at`が永久にNULLだった問題を修正**：`review_status`が`pending`から遷移する2箇所（`execute_company_merge()`のQuerySet一括更新、および「別会社」への遷移）のどちらも、この2フィールドを設定するコードが示されていなかった。`execute_company_merge()`の`.update()`に`reviewed_by`／`reviewed_at`／`updated_at`を追加し、Person側の`mark_as_different_person()`と同型の`mark_as_different_company()`を新設した（§6.5.5、§6.5.5.1）

## v0.16からの主な変更点（★重要：保護機構の迂回経路を封鎖）

- **Django admin経由での保護迂回を防止する§7.6.1を新設**：本仕様書がここまで積み上げてきた「重要な変更は専用のService関数を経由させる」という原則は、すべてView層のフォーム構成に依存しており、Django adminはこれを丸ごと迂回できることが判明した。特に`Company.status`／`merged_into`をadmin経由で直接変更すると、`execute_company_merge()`が行うFK付け替え・レビューキューの後始末・ActionLog記録が一切走らないままCompanyだけが`merged`状態になり、実害が大きい。`Deal.owner`／`Deal.primary_person`／`Deal.is_archived`／`Activity.is_archived`／`Company.status`／`Company.merged_into`の6フィールドを、対応する`ModelAdmin`の`readonly_fields`に指定することを明記した（§7.6.1）

## v0.15からの主な変更点

- **`Deal.company`自動補完の実現方式を明記**：§2.1は「作成時に未指定なら初期値を設定する」という方針は示していたが、実装方式（バックエンド／フロントエンドのどちらで行うか）が未確定だった。JS等による動的なプルダウン書き換えに走ると複雑化するため、**バックエンドの`form_valid`で、未入力の場合のみ`primary_person.company`を補完する**方式に確定した（§2.1）

## v0.14からの主な変更点

- **Deal編集フォームから`owner`・`primary_person`を除外することを確定（★重要）**：§5.1の`clean()`（v0.14で新設）はDealPerson／DealUser作成・更新時にしか働かず、Deal本体の通常編集フォームで`owner`／`primary_person`を直接変更した場合には既存のDealPerson／DealUserが再検証されない、という指摘があった。対策として`clean()`側に衝突削除の副作用を持たせるのではなく、**そもそも通常編集フォームでこの2フィールドを変更できないようにする**ことで解決した（§2.6）
- **`primary_person`付け替えに専用の判定関数・実行関数を新設**：`owner`と同じ重みを持つ変更操作でありながら専用の経路がなかったため、`reassign_deal_owner()`と同型の`can_reassign_deal_primary_person()`／`reassign_deal_primary_person()`を新設した（§2.6.1）

## v0.13からの主な変更点

- **§5.2の「バリデーションで縛る」を実際にコード化（★13版を通じて未実装だった欠落）**：§5.2は初版から「`Deal.primary_person`／`Deal.owner`には同一人物を二重登録しない（バリデーションで縛る）」と宣言していたが、そのコードが一度も示されていなかった。§5.1の`UniqueConstraint`は「同じ人を2回登録すること」しか防げず、「`owner`本人を`DealUser`にも登録すること」は別物で防げていなかった。`DealPerson.clean()`／`DealUser.clean()`を新設し、Activity側（`ActivityUser.clean()`）にも同じ制約を追加した（§5.1）
- **`reassign_deal_owner()`にDealUser削除処理を統合**：v0.13時点では「new_ownerが既存DealUserなら削除して昇格させる」処理を「この関数の中、または呼び出し元のフォームで行う」と両論併記にしており、担当が決まっていなかった。上記の`clean()`追加により、この処理を怠ると`reassign_deal_owner()`自体が失敗するようになったため、**Service関数の責務として一本化**した（§2.6）
- **実装指示書向けの申し送り事項を追加**：既存ファイルの事前読み込み義務、`urls.py`の`app_name`設定、`Subquery`の`output_field`明示、Step4でのテンプレート作り込み禁止、`timezone`によるaware datetime統一、指示書を「全体ルール」と「Step1」に分けて渡す進め方（§9.2）

## v0.12からの主な変更点（実装者目線での追加点検）

- **`contacts ↔ companies`間の循環importを解消（★最優先・起動時クラッシュ）**：v0.9で`companies ⇔ deals`間の循環import対策（`Deal`を遅延import）を行ったのと同じ構造の問題が、`Contact.company`（§6.2）と`transfer_contacts_to_company()`（§6.5.5）の間にも存在し、見落としていた。`Contact.company`を既存慣習（`Person.primary_contact`と同じ文字列参照）に変更し、`companies/models.py`が`Contact`を通常importしても循環しないようにした。§1.2の依存図に`contacts → companies`を追記し、「アプリをまたぐFKは文字列参照で書く」原則を明記した
- **`Deal.owner`付け替えに専用の判定関数・実行関数を新設**：§2.6はプローズの説明だけでコード例がなく、判定関数さえ存在しなかった。`can_edit_deal`で丸ごとゲートすると、supportロールのDealUserが他人の案件のownerを勝手に付け替えられる穴があったため、`can_archive_deal`と同型の`can_reassign_deal_owner()`と、`ActionLog`記録を含む`reassign_deal_owner()`を新設した
- **CompanyDuplicateCandidateのレビュー操作の権限を`merge_company`に統一する旨を明記**：既存の`duplicates/views.py`が`persons.merge_person`を使い回している前例に倣った
- **付録A.7に`reassign_deal_owner()`等を追加**

## v0.11からの主な変更点（★致命的な取りこぼしの修正）

- **`visible_deals_for()`・`visible_activities_for()`の`related_name`書き換え漏れを修正（★最重要）**：§1.4で「全コード例を新しい`related_name`に書き換えた」と宣言していたが、この2関数だけ旧来のデフォルト逆参照名（`dealuser`／`activityuser`、アンダースコアなし）が残っていた。`related_name`を明示すると、そのFKのクエリ用キーワードも連動して変わるため、デフォルト名でのクエリは**`FieldError`で確実に失敗する**。しかも`visible_deals_for`はDeal一覧画面、`visible_activities_for`は日報一覧（§3.3）が直接呼び、`deals_with_last_contact`（§2.5、放置案件検出）も内部で`visible_deals_for`を呼ぶため、**最も使用頻度の高い3画面が初回アクセスで確実にクラッシュする**状態だった。皮肉にも、v0.9で本書自身が警告した事故（「コード君が`related_name`を付けた瞬間、旧コードがFieldErrorになる」）を、書き換え漏れという形で再現していた。文書全体を旧来のデフォルト名で再検索し、他に取りこぼしがないことを確認した上で修正した（§7.1、§7.3）
- **付録A.7に今回追加した関数を反映**：`archive_deal()`／`archive_activity()`／`get_companies_confirmed_as_different()`が一覧に抜けていたため追加

## v0.10からの主な変更点

- **`get_companies_confirmed_as_different()`の適用箇所を修正**：v0.10はContactのリンク先探索（§6.5.4手順2）でこの除外を使っていたが、Contact新規作成時は`contact.company`が`NULL`で渡す対象が存在せず、Contact更新時の再照合も「今のcompanyが正しいとは限らない」前提と循環するため、構造的に成立しなかった。**除外先を、Company対Companyのペアを実際に作る2箇所（§6.5.4手順4のexact_match複数候補ペアリング、§6.5.5の一括マージ対象選択）に変更した**
- **DealPerson／DealUser・ActivityPerson／ActivityUserの追加・削除に権限ゲートを追加**：サブフォームという動線だけ決まっていて、誰が操作できるかが未定義だった。それぞれ`can_edit_deal`／`can_edit_activity`を要求する（§5.2）
- **`archive_deal()`／`archive_activity()`の実行関数と画面導線を新設**：`can_archive_deal`／`can_archive_activity`という判定関数はあったが、実際に`is_archived`を立てる実行関数がどこにも定義されていなかった。§6.6のCompanyには手動アーカイブの動線があったのに、Deal／Activityには書き忘れていた（§7.1、§7.3）
- **Companyマージ時のActionLogの紐付け先を確認事項として明記**：Person側は`content_object`がPerson自体ではなく専用の`PersonMergeLog`インスタンスに紐づく設計であり、Companyの「消える側に個別ログを残す」設計とは前提が異なる。実装時にコード君が既存の慣習を確認する旨を申し送った（§6.5.5）
- **一覧テンプレートでの`get_FOO_display()`徹底を申し送りに追加**：§1.5でモデルの`__str__`は直したが、一覧画面のテンプレート側にも同じ配慮が要る旨を明記（§9.2）

## v0.9からの主な変更点（実コードとの照合結果）

- **Userへの`related_name`を既存規則に合わせて修正（★最重要の訂正）**：v0.9は`Person.managed_by → persons_managed`を根拠に9本全てへ命名を提案したが、実コードを確認した結果これは誤りだった。既存のUserへのFKは15本前後あり、**逆参照名を持つのは`managed_by`系の2本だけ**で、残りは全て`related_name="+"`（監査系は逆引きを作らない）。§7のコードもすべて正参照のみを使っており、v0.9の提案のままでは誰も使わない逆参照アクセサを9本作ることになっていた。監査系5本（`created_by`／`updated_by`／`uploaded_by`）を`+`に改め、業務的な逆引きが必要な4本（`deals_owned`／`activities_performed`／`deal_users`／`activity_users`）のみ命名を残した（§1.4.2）
- **`Company.merged_into`の命名がPersonの前例と異なる旨を明記**：`merged_companies`は`persons/models.py`の`merged_from_set`と異なる形だが、読みやすさを優先した意図的な変更として記録した（§1.4.2）
- **「別会社」判定済みペアが再照合のたびに再生成される問題を修正**：v0.9で`get_or_create`化した際、検索キーに`review_status=PENDING`のみを含めていたため、過去に人が`different_company`と判定したペアが除外されず、**再照合が走るたびに同じペアが新しいpendingとして再度レビューキューに並ぶ**状態だった。既存のPerson側の`get_persons_confirmed_as_different()`と同型の`get_companies_confirmed_as_different()`を新設し、判定フローの絞り込みに追加した（§6.5.3、§6.5.4）
- **モデル数の表記を9に統一**：§1.3が「全8モデル」としていたが、列挙されているのは9モデルだった
- **`attachment_upload_to`の定義順序とimportを修正**：`class Attachment`が関数定義より前に置かれており`NameError`になる状態だった。`timezone`のimportも欠けていた（§4.1）
- **`execute_company_merge()`の検証順序を並べ替え**：0件チェックを先頭に置いた（§6.5.5）
- **`MODEL_REGISTRY`の参照を1箇所に集約**：`get_permission_required()`と`get()`で個別に参照していたのを`get_registry_entry()`にまとめ、次フェーズで`cards`を追加する際に片方だけ直す事故を防ぐ（§4.3）
- **`__str__`の未定義5モデルを追加**：DealPerson・DealUser・ActivityPerson・ActivityUser・CompanyDuplicateCandidateの`__str__`を追加。`Activity.__str__`が選択肢の内部値をそのまま表示していた点も`get_activity_type_display()`に修正した（§1.5）
- **実装指示書の段取りに関する申し送り事項を追加**：4ステップへの分割発注、`ActionLog.record()`のシグネチャ事前確認、`get_FOO_display()`の徹底（§9.2）

## v0.8からの主な変更点

### 既存コードの実装慣習との整合（★最重要・全モデルに波及）

- **主キーをUUIDに明記（★筆頭項目）**：既存の全モデル（actionlogs／cards／contacts／persons／mailings／tags／duplicates）が例外なく`UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`である一方、`config/settings.py`の`DEFAULT_AUTO_FIELD`は`BigAutoField`のまま。仕様書に書かないとコード君は連番主キーで作り、**URLに連番IDが出て「他人のレコードIDを推測できない」という既存機能の前提が新機能だけ崩れる**。後から変更すると全FK・全マイグレーションのやり直しになる（§1.3）
- **`related_name`を全FKに明示し、コード例を全面的に書き換え（★）**：既存コードは全FKで`related_name`を明示している（`ContactSns.contact → sns_accounts`、`Person.managed_by → persons_managed`、`TrackingLink → tracking_links`等）。一方v0.8のコード例は`deal.dealuser_set`のようなDjangoの自動命名に依存しており、**コード君が既存慣習に倣って`related_name`を付けた瞬間、§3・§7のコードが全てFieldErrorになる**状態だった。命名表を§1.4に新設し、§3・§7の全コード例を新しい名前に書き換えた
- **`find_or_link_company()`を`link_contact_to_company()`に改名（★）**：CLAUDE.md §10の命名規約では`find_*`／`get_*`／`search_*`は「DBを読むが書かない」準関数と定義されている。この関数はCompanyを新規作成しContactにリンクしCandidateを積む副作用あり関数であり、規約違反だった。`can_view_deal`等の`can_*`は規約表にない形だが、既存の`mailings/services/permissions.py`に`can_view_campaign`の前例があるため**例外として明記した上で維持する**（§7.0）

### 実装すると壊れる箇所の修正

- **`create_permissions()`の呼び出しを既存6本と同一の形に修正（★最優先）**：v0.8のスニペットは`app_config.models_module = True → None`と書き換えていたが、**`None`に戻すと`create_permissions`は冒頭の`if not app_config.models_module: return`で早期returnするため、その後の`post_migrate`による標準権限生成がこの4アプリについて丸ごとスキップされる**。後からモデルやフィールドを追加したときに「権限が作られない」という追跡困難な形で表面化する。既存の`accounts/migrations/0004〜0010`と同一の書き方（`apps=apps`を渡し`models_module`には触れない）に統一し、`using=schema_editor.connection.alias`も加えた（§7.6）
- **`expected_value()`が`amount`／`probability`のNoneで`TypeError`**：§2.1は両方をnull許容とし「初期段階では未定が普通」と明言しているのに、集計サンプルが無条件に乗算していた。金額未定のDealを期待値集計でどう扱うかは業務判断でもあるため、方針ごと確定した（§2.4）
- **`CompanyDuplicateCandidate`生成時の`IntegrityError`**：§6.5.3で部分ユニーク制約を追加した一方、生成コードが`create()`のままだった。保険クーロンやContact更新で既存ペアに再度照合が走ると一意制約違反で500になる。`get_or_create`方式に変更（§6.5.3）
- **`can_archive_deal`／`can_archive_activity`に`change_*`のAND条件**：v0.8は`can_edit_*`にこれを入れてその理由まで説明したのに、`can_archive_*`に適用し忘れていた。退職・異動でviewerロールに変わった元営業は、owner付け替えが済むまで自分がownerのDealを持ち続けるため、実際に穴になる（§7.1、§7.3）
- **`can_delete_attachment`に`Activity.created_by`が漏れていた**：§3.1・§7.3で塞いだ「実施者が空だと登録者本人が触れない」穴が、添付の削除だけ残っていた（§4.4.3、§7.4）
- **§1.2の依存図に`deals → mailings`を追加**：`Deal.source_campaign`を新設した時点で発生する依存の記載漏れ（§1.2）

### 書かれていないため独自判断になっていた箇所の確定

- **`upload_to`のUUID化関数（★）**：§4.1の説明文は「保存パスは実装都合でUUID化されるため`original_filename`が必要」と書いていたが、コードは`upload_to='attachments/%Y/%m/'`だけで**元のファイル名がそのままディスクに載る**（`original_filename`を持つ理由が消える）状態だった（§4.1）
- **「最終接触日」列の実装方式（★）**：§2.5で列の存在だけ決めていたが方式が未定義で、素直に書くと行ごと1クエリのN+1になる。`Subquery`＋`OuterRef`での`annotate`とし、`visible_deals_for`との合成順序も明記（§2.5）
- **`Activity.occurred_at`の未来日禁止`clean()`のコード**：Dealの`clean()`はコードで示されているのにActivity側は表の記述だけだった。`DateTimeField`なので`timezone.now()`比較となり、Dealの`closed_at`（`DateField`／`localdate()`比較）と書き分けが必要（§3.1）
- **`execute_company_merge`の入力検証**：survivingが統合対象に混ざると`mark_as_merged(self)`で自己参照ループが生まれる。件数上限・status検証と併せて追加（§6.5.5）
- **`Deal.company`の自動補完ルール**（§2.1）、**Dealアーカイブ時のActivityの扱い**（§2.9）、**`Meta.ordering`の既定**（§1.5）、**添付のサイズ・拡張子制限**（§4.1、nginxの`client_max_body_size 20M`に揃える）、**`Company.created_by`は自動作成時にNULL**（§6.2）
- **`CompanyDuplicateCandidate`のスコアは2回計算する**：§6.5.4はContact対Companyのスコア、§6.5.3の`score`はCompany対Companyの値であり、候補を積むときに計算し直す必要がある点が読み取りにくかった（§6.5.3）
- **`link_contact_to_company()`は`update()`を使わない**：`queryset.update()`で書くとメモリ上のインスタンスに反映されず、呼び出し側で`refresh_from_db()`が必要になる。インスタンスに設定して`save()`する方式に固定し、呼び出し側の負担をなくす（§6.5.4）

### 権限・画面の穴

- **添付メモの編集動線を追加**：`change_attachment`を配っているのにメモを編集する画面がなく、v0.8自身が立てた「死蔵権限を作らない」基準に違反していた（§4.4.4）
- **`ProtectedFileDownloadView`の権限をレジストリ側に持たせる**：`permission_required`をクラス属性で`attachments.view_attachment`に固定したため、次フェーズで`cards`を登録すると**名刺画像を見るのに`attachments`アプリの権限が要る**状態になっていた。`MODEL_REGISTRY`を`{model_name: (model, permission)}`とし`get_permission_required()`で引く（§4.3）
- **添付削除時のActionLogに親のコンテキストを含める**：物理削除でGFKの参照先が消えるため、ログを見ても**どの案件・活動の添付だったかが特定不能**になる。`object_repr`も併せて設定（§4.4.2）
- **フォロー漏れ検出でアーカイブ済みPersonをどう扱うか**：レビューでは`status=ACTIVE`での除外が推奨されたが**採らない**。退職者のクリックは「後任へフォローすべき」という情報であり、除外すると消える。一覧に状態を表示し、任意フィルタとして持たせる（§3.5.2）
- **C群の明文化**：`observer`ロールの効力の限界／`can_delete_attachment`に`delete_attachment`のANDを入れない理由／§6.6のCompanyがService層判定を持たない理由／§2.9.4の日本語の破綻を修正

## v0.7からの主な変更点（再掲）

- **`FileField(storage=...)`をcallableに変更（★最優先）**：`FileSystemStorage`のインスタンスを直接渡すと、マイグレーションファイルに**その環境の絶対パスが焼き込まれる**。自宅PC・実家PC・Docker・worktreeで環境が異なるため、同期した瞬間に別環境で差分マイグレーションが生成され続ける。ストレージを返す関数を渡し、パスを実行時解決にする（§4.1）
- **選択肢（TextChoices）の実体を全10フィールド分定義（★指示書化の前提）**：v0.7時点では日本語の列挙文しかなく、value名が未定義だった。コード君が独自命名すると後の手戻りが大きいため確定（§2.9）
- **`Deal.source_campaign`を新設（★新規）**：どのメールキャンペーンから生まれた案件かをFKで正確に保持する。§3.2のCheckConstraintにより`Activity.deal`と`Activity.campaign`は排他であり、フォロー活動のActivityとその後に作られるDealはリンクを持たないため、Deal側に直接持たないとキャンペーンの成果が追跡できない（§2.1、§2.4）
- **`Activity.created_by`を追加（★権限の穴を塞ぐ）**：`Activity.user`（実施者）はnull許容のため、実施者を空で登録すると**登録者本人が自分の記録を編集もアーカイブもできなくなる**（`edit_all_activities`保持者しか触れない）状態だった。Dealと同じ監査パターンに揃え、編集・アーカイブ判定のOR条件に加える（§3.1、§7.3）
- **viewerロールの可視範囲を確定（★方針決定）**：`deal_viewer`／`activity_viewer`には`view_all_deals`／`view_all_activities`を**配らない**。閲覧させたい相手はDealUser／ActivityUser（`observer`ロール）として個別に登録する。§5.3で宣言済みの「ACL上は見えなくても関与者登録で個別に見える」2層構造と一貫させる（§7.6）
- **`can_edit_deal`／`can_edit_activity`に`change_*`権限のAND条件を追加**：viewerを閲覧目的でDealUserに登録する運用が標準になるため、判定関数が単体で呼ばれるとviewerに編集を許してしまう。View層のゲート頼みをやめ、判定関数を自己完結させる（§7.1、§7.3）
- **アーカイブのView層ゲートを`change_deal`／`change_activity`に確定**：`delete_deal`／`delete_activity`／`delete_company`は**どのグループにも配らない**（物理削除の画面が存在しないため）。`cards`で問題になっている死蔵権限を新規に3つ作らないための判断（§7.1、§7.6）
- **Attachmentの削除方式・権限境界を確定**：物理削除とし、DBレコード削除時に**ストレージ上の実ファイルも明示的に削除**する。削除できるのはアップロード者本人・親の`Deal.owner`／`Activity.user`・`edit_all_*`保持者のみで、**単なる同席者（DealUser／ActivityUser）は削除不可**（§4.4、§7.4）
- **Attachmentのアップロード・削除画面と`can_edit_attachment`を定義**：v0.7は権限を配る先だけ決めてあり、それを要求する画面が存在しなかった（§4.4、§7.4）
- **値域バリデーションの追加**：`amount`≧0、`probability`は0〜100、`closed_at`は未来日禁止（`Activity.occurred_at`と一貫させる）（§2.1、§2.4）
- **`ContactUpdateView`での再照合に実行順序を明記**：`form.save()`の**前**に旧Companyを変数へ退避しないと、更新後の新Company側で残存件数を数え、**作成したばかりのCompanyをarchivedに落とす**取り違えバグを生む（§6.5.4）
- **フォロー漏れ検出の論理削除除外**：`followed_persons`が`is_archived`を見ておらず、誤登録して即アーカイブした活動が「フォロー済み」と扱われ、未フォローリストから永久に消える不整合があった。あわせて返却QuerySetに`select_related('primary_contact')`を追加（§3.5.2）
- **PermissionGroup投入マイグレーションの実行順序を明記**：カスタム権限の`auth.Permission`レコードは`post_migrate`で生成されるため、素朴に`group.permissions.add()`を書くと`Permission.DoesNotExist`でクラッシュする。既存`0008`と同じデータマイグレーション方式を維持したまま、冒頭で`create_permissions()`を明示的に呼ぶ（§7.6）
- **`maybe_fill_company_domain`の`update_fields`に`updated_at`を追加**：`auto_now=True`は`update_fields`に含めないと更新されない。既存の`Person.mark_as_merged()`／`mark_as_active()`は必ず含めている（§6.5.6）
- **Companyの手動アーカイブ動線を追加**：`delete_company`を配らない以上、`status=archived`にする経路が必要（§6.6）
- **一括マージViewに`companies.merge_company`の要求を明記**：権限の宣言と配布だけ決まっていて、要求する箇所が書かれていなかった（§6.5.5）
- **`ProtectedFileDownloadView`に`PermissionRequiredMixin`を追加**：`view_attachment`を配っても要求箇所がなく死蔵になっていた。あわせて`deal_viewer`／`activity_viewer`にも`view_attachment`を配る（§4.3、§7.6）
- **付録Aとして名称対照表を新設**：日本語名とコーディング名の対照を巻末に集約

## v0.6からの主な変更点（再掲）

- **全モデルのCharFieldにmax_length追記（★即死級）**：未定義だとmakemigrations実行時にSystemCheckErrorで確実にクラッシュするため、全フィールドに明記
- **`Deal.closed_at`にblank=Trueを追加（★重要）**：`null=True`だけではフォーム層で必須扱いになり、オープンステージでの新規Deal登録自体ができなくなる罠を修正
- **フォロー漏れ検出の再修正（★重要）**：v0.6の修正は「突き合わせ判定」のみ生存Person基準にしたが、**返す一覧自体は元のPerson（マージで死亡した側含む）のまま**だった。これだと「フォロー済みなのに出続ける」不具合が「間違った（死んだ）相手にActivityを作ってしまう」というより悪い不具合に置き換わっていたため、クリック側も生存Person基準に正規化し、QuerySetとして返すよう修正
- **Attachmentモデルにstorage明記**：方針（保護ディレクトリへの保存）を文書で決めていたのに、モデル定義への反映が漏れていたのを追加
- **CompanyDuplicateCandidateの部分ユニーク制約・ID順正規化ガードを追加**：同一ペアの重複生成防止
- **Companyマージの`ActionLog.record()`呼び出し元を明記**：`transfer_contacts_to_company`自体には記録処理を入れず、呼び出し元（Execute_Merge_Company相当）の責務とする
- **再照合後の孤児Company対応**：Contact・Dealが0件になったCompanyは`archived`に落とす
- **一括マージのペア扱い修正**：被統合Company同士のペアも`merged`にする
- **`Deal.stage`にdefault追加、Activityにnull/blank指定を追加**
- **権限グループ設計を確定（★新規）**：`deal_admin`/`editor`/`viewer`、`activity_admin`/`editor`/`viewer`、`company_admin`/`editor`/`viewer`の9グループを新設。Attachmentは専用グループを持たず`deal_*`/`activity_*`に権限を含める。編集・削除の権限境界を明記

## v0.5からの主な変更点（再掲）

- **§6.5.5 レビューキュー後始末の修正（★重大）**：マージ対象ペア自体まで`invalidated`にしてしまい、`merged`の実績が1件も残らない不具合を修正。マージ対象ペアは`merged`、それ以外の巻き添えペアは`invalidated`に分けて更新
- **§6.5.5／§6.5.6 Company.domainのマージ時引き継ぎ**：`maybe_fill_company_domain()`が定義されているだけで実際にはどこからも呼ばれていなかった（exact_matchでリンクする時点では既にdomainが埋まっているため実質no-op）不具合を修正。マージ時に、surviving側のdomainが空で消える側に有効なdomainがあれば引き継ぐ
- **§2.5 close_dealの修正**：`full_clean()`による過剰検証（金額等の無関係な必須チェックで失注登録が弾かれる）を`deal.clean()`直接呼び出しに変更。受注復帰時の失注理由クリア、`updated_by`の設定、`transaction.atomic`、文字列リテラルではなく`Deal.Stage`列挙型を使うよう統一
- **§2.1 null/blank指定の明記**：`name`以外のフィールドは登録時点で未定であり得ることを反映
- **§3.5.2 フォロー漏れ検出の生存Person基準照合**：Personマージを考慮しておらず、マージ後にフォロー済みが「未フォロー」と誤検出される不具合を修正。クリック側・フォロー側双方を`get_surviving_person()`で解決してから突き合わせる
- **§3.5.2 UI方針の確定**：フォロー漏れ検出は`activities`側の独立画面とし、Campaign詳細からはリンクのみ（§1.2の依存方針と整合）。各行の「フォロー記録」リンクはActivity新規作成画面へ遷移し、`person`・`campaign`を初期値として引き継ぐ（モーダルではなく別画面。Activityは入力項目が多いため）。「未フォローのみ表示」フィルタを用意
- **§6.5.4 Contact編集時の再照合トリガー**：`ContactUpdateView`で`organization`または`org_domain_name`が変更された場合も`link_contact_to_company`を呼ぶことを明記。exact_match複数候補時のペアリングルールを「最古の代表と各候補の1対1ペア」と具体化
- **§3.3 日報一覧の初期表示フィルタ**：`user=request.user`, `occurred_at__date=今日`をデフォルト適用

## v0.4からの主な変更点（再掲）

- **アプリ構成を4アプリに変更（★循環import解消）**：`deals`／`activities`／`companies`／**`attachments`（新設）**。AttachmentをDeal/Activity両方に依存する独立アプリとして切り出し、`deals`↔`activities`間の循環依存を構造的に解消
- **§3.5.2 配置の記述統一**：本文が`mailings/services/`のまま残っていた記述漏れを修正し、§1.2の決定（`activities`側配置）と一致させた
- **§2.5 クローズ遷移のガード追加（★重要）**：文書上は「専用フォーム経由に限定」と決めていたが、`change_deal_stage`関数自体にガードがなかった。関数冒頭でクローズ系ステージへの直接遷移を拒否し、`close_deal()`という別関数に分離
- **§6.5.5 トランザクション境界・レビューキュー後始末の明記**：`transaction.atomic()`内での実行を前提条件として明記、マージで消えたCompanyを参照する`pending`な`CompanyDuplicateCandidate`を`invalidated`に遷移させる処理を追加
- **§6.2／§6.5.6 Company.domainの固定ルール修正**：「空→値」の一方向の穴埋めのみ許可（値→別の値の上書きは引き続き禁止）に変更し、汎用ドメイン起因でexact_matchが永久に成立しなくなる不具合を解消
- **§6.5.4 exact_match複数候補時のルール確定**：`created_at`昇順で最古を正とする
- **§7.1／§7.3 カスタム権限の宣言を明記**：`Meta.permissions`への宣言を追加
- **§7.3 一覧・詳細の権限集合を一致させる**：`visible_activities_for`に`view_all_deals`/`view_all_campaigns`保持者のQ条件を追加し、単体判定と同一集合を返すことを保証

## v0.3からの主な変更点（再掲）

- **アプリ構成を明示**：`deals`／`activities`／`companies`の3アプリを新設。Attachmentは`deals`アプリに配置（Deal/Activity両方から参照されるが、Dealが起点の機能のため）。フォロー漏れ検出（§3.5.2）は`mailings`ではなく`activities`側に配置（`mailings`が`activities`に依存する向きを避けるため）
- **§3.5.2 クエリ修正（★即死級）**：`related_name`指定を反映（`tracking_links`／`click_logs`）、`exclude`のNULL罠を回避（`isnull=False`追加）、ボットクリック除外（`is_valid_click=True`）
- **§6.5.4 Company照合の絞り込み強化**：マージ済み（`status≠active`）Companyを候補から除外。プレフィルタも汎用ドメイン・空ドメインを除外する形に修正
- **§7.3 認可ロジック修正**：`view_all_activities`を判定の最優先に変更（権限名と実際の挙動を一致させる）
- **§2.4 バリデーション経路の見直し**：受注/失注への遷移は専用フォーム経由に限定し、`clean()`が確実に発火する経路に一本化
- **§6.5.2 自動リンク条件**：C-1の緩和案（会社名一致＋ドメイン矛盾なしまで拡大）は不採用、現行の厳格ルール（exact_matchのみ自動リンク）を維持
- **D群の記述レベル修正**：ActivityPerson/ActivityUserの`on_delete`明記、`MODEL_REGISTRY`のKeyError対策（`.get()`＋404）、`can_view_attachment`とView呼び出しの整合

## v0.2からの主な変更点（再掲）

- **§3.2 CheckConstraint修正（★即死級）**：未実装の`project`/`schedule`/`task`を参照していたのを削除し、現行実装フィールド（`deal`／`campaign`）のみの排他条件に縮小
- **§6.5.5 マージ処理修正（★即死級）**：`transfer_contacts_to_company`に`Deal.company`の付け替え処理が抜けていたのを追加
- **§7.3 認可ロジック修正**：ActivityUser（同席者）が自分の活動記録を閲覧できない抜け穴を解消。一覧表示用の`visible_activities_for`を新設
- **§2.1 記載ミス修正**：`created_by`の重複行を削除
- **§5章 UniqueConstraint追加**：DealPerson/DealUser/ActivityPerson/ActivityUserの多重登録防止
- **§6.5.4 プレフィルタのNULL安全策**：`org_domain_name`が空の場合の動的Q構築を明記
- **§4.3 ファイル名対応**：`original_filename`保持と`as_attachment`指定を追記

## v0.1からの主な変更点（再掲）

- `Activity.person`（単一FK）を廃止し、ActivityPerson（0件以上）に一本化
- `Activity.campaign`を新規追加（キャンペーンフォロー活動の記録、フォロー漏れ検出機能とセット）
- `Activity.deal`をCASCADE→SET_NULLに修正
- Company重複検出のスコア表を`organization`／`org_domain_name`の2項目に簡素化（電話・住所・URLは削除）
- 自動リンク条件を「exact_match（会社名かつドメイン両方一致）のみ」に統一
- 既存実装（`is_generic_email_domain()`／`derive_org_domain_name()`／`Person.get_surviving_person()`）を新設せず流用
- `Deal.company`はCompanyマージ時に付け替える（`Deal.primary_person`とは非対称、理由は復元機能の有無）
- CheckConstraintの引数を`check=`から`condition=`に修正（Django 6対応）
- アクセス制御にActivity/Attachmentの判定ロジックを追加

---

# 第1章 背景・目的

（v0.1と同じ。省略）

## 1.1 スコープ外（次フェーズ以降）

- Schedule／Task／Project（受注後プロジェクト進捗管理）本体の実装
- AccessList本体（データレベル権限エンジン）
- Person閲覧制限（役員等の限定公開）
- Campaign／MailingList／Tagの権限体系改修
- 名刺画像等、既存ファイルへの配信経路変更
- 部署階層に基づく段階的な閲覧範囲（`Department.descendants()`のN+1未解消のため、AccessListフェーズに委ねる）

## 1.2 アプリ構成（★v0.5で4アプリに変更、循環import解消）

新設するDjangoアプリは以下の4つとする。

| アプリ名 | 収録モデル |
|---|---|
| `deals` | Deal・DealPerson・DealUser |
| `activities` | Activity・ActivityPerson・ActivityUser |
| `companies` | Company・CompanyDuplicateCandidate |
| `attachments`（★v0.5で新設） | Attachment |

**依存の向き（一方向のみ）**：

```
attachments → deals（can_view_deal を import）
attachments → activities（can_view_activity を import）
activities  → deals（can_view_deal を import。Activity.deal FK）
activities  → mailings（Campaign/TrackingLink/ClickLog を参照。Activity.campaign FK、フォロー漏れ検出）
deals       → companies（Deal.company FK）
deals       → mailings（Deal.source_campaign FK。★v0.9で追記）
contacts    → companies（Contact.company FK、文字列参照。★v0.13で追記）
```

**★v0.13で追記：`contacts → companies`と、アプリをまたぐFKは文字列参照にする原則**。§6.2で`Contact.company`（既存アプリ`contacts`が新設アプリ`companies`のCompanyを参照するFK）を新設した時点で、この依存が発生する。v0.12まではこれが依存図から漏れていた。

**アプリをまたぐFKは、既存の慣習（`persons/models.py`の`primary_contact = models.ForeignKey("contacts.Contact", ...)`）に倣い、常に文字列参照（`'companies.Company'`）で書く。** クラス参照（`models.ForeignKey(Company, ...)`）にすると、`contacts/models.py`が`companies/models.py`をトップレベルでimportする必要が生じる。一方§6.5.5の`transfer_contacts_to_company()`は`companies/models.py`側から`Contact`を参照するため、両方をクラス参照にすると**起動時に`ImportError: cannot import name 'Contact' from partially initialized module`で確実にクラッシュする**循環importになる。

文字列参照であれば、Djangoはアプリの読み込み完了後に遅延解決するため、`contacts/models.py`は`companies`を一切importしない。この結果、依存の向きは**`companies → contacts`の一方向のみ**（§6.5.5がContactを参照する側）に定まり、循環は生じない。§6.5.5に、なぜDealだけ遅延import（`apps.get_model`）が必要で、Contactは通常importでよいのかを明記した（後述）。

**★v0.9で追記：`deals → mailings`**。§2.1で`Deal.source_campaign`（Campaignへのnullable FK）を新設した時点で、この依存が実際に発生する。v0.8では§2.1の本文が「依存の向きは`deals → mailings`」と述べていたのに依存図側の更新が漏れていた。向きは一方向（`mailings`は`deals`を知らない）なので設計上の問題はない。Campaign側から「この配信から生まれた案件」を一覧する画面が必要になった場合も、§1.2の原則に従い**`deals`側の独立画面とし、Campaign詳細からはURLリンクを張るだけ**にする（逆方向の依存を作らないため）。

**★v0.5で変更した理由**：当初Attachmentを`deals`に配置していたが、Attachmentは`deal`にも`activity`にも紐づくため、権限判定関数がお互いをimportし合う循環（`deals ⇔ activities`）が発生していた。Attachmentを独立アプリに切り出すことで、依存が一方向だけになり、循環importが構造的に発生しなくなる。将来Project（受注後プロジェクト管理）を実装した際も、`attachments`が3方向（deals/activities/projects）に依存を伸ばすだけで済み、他のアプリ同士は互いを知らないままでいられる。

**★v0.6で追記：`activities`↔`mailings`の依存について**。§7.3（`activity.campaign.has_view_permission()`）・§3.5.2（フォロー漏れ検出）により、`activities → mailings`の依存は実際に発生する。これは一方向であり問題ない。逆方向（`mailings → activities`）が発生しないよう、**フォロー漏れ検出の画面はCampaign詳細画面に埋め込まず、`activities`側の独立画面とし、Campaign詳細画面からはURLリンクを張るだけにする**（§3.5.2参照）。これにより、画面（テンプレート・View）のレベルでも`mailings`が`activities`のコードを呼ぶことはなく、依存は`activities → mailings`の一方向のみに保たれる。

**`companies`→`deals`の逆参照について**：§6.5.5（Companyマージ時にDeal.companyを付け替える処理）は`companies`が`deals`のDealモデルを参照する必要があるが、これは`deals→companies`（Deal.company FK）と逆方向になり循環する。この参照は**モジュールレベルのimportを避け、関数内での遅延import、または`django.apps.apps.get_model('deals', 'Deal')`を使う**ことで回避する（§6.5.5参照）。

## 1.3 主キーはUUIDとする（★v0.9で新設・最重要）

**新設する全9モデル（Deal・DealPerson・DealUser・Activity・ActivityPerson・ActivityUser・Company・CompanyDuplicateCandidate・Attachment）の主キーはUUIDとする。**

```python
import uuid

class Deal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
```

**理由**：既存の全モデル（`actionlogs`／`cards`／`contacts`／`persons`／`mailings`／`tags`／`duplicates`）が例外なくこの形で書かれている。一方で`config/settings.py`の`DEFAULT_AUTO_FIELD`は`BigAutoField`のままであるため、**モデル側で明示しない限り新設モデルだけ連番主キーになる**。

連番になった場合の影響は2つある。1つはURLに連番IDが露出し、「他人のレコードIDを推測できない」という既存機能の前提が新機能だけ崩れること。もう1つは、後から変更するには全FK・全マイグレーションのやり直しが必要になることである。**指示書の冒頭に書くべき筆頭項目**として扱う。

## 1.4 `related_name`の命名（★v0.9で新設・最重要）

**新設モデルの全FKに`related_name`を明示する。** 既存コードは全FKで明示しており（`ContactSns.contact → sns_accounts`、`Person.managed_by → persons_managed`、`TrackingLink → tracking_links`、`ClickLog → click_logs`、逆参照が不要なものは`related_name="+"`）、Djangoの自動命名（`dealuser_set`等）に依存している箇所は存在しない。

v0.8までのコード例は自動命名を前提に書かれていたため、**コード君が既存慣習に倣って`related_name`を付けた瞬間、§3・§7のコードが全てFieldErrorになる**状態だった。本版で全コード例を下表の名前に書き換えている。

**命名規則**（既存の実例から導出）：1対Nの逆参照は複数形。1対1は単数形。Userへの逆参照は他モデルと衝突するため「モデル名の複数形＋修飾語」とする（`Person.managed_by → persons_managed`に倣う）。

### 1.4.1 モデル間のFK

| FK | related_name | 参照例 |
|---|---|---|
| `DealPerson.deal` → Deal | `deal_persons` | `deal.deal_persons.all()` |
| `DealUser.deal` → Deal | `deal_users` | `deal.deal_users.filter(user=user)` |
| `ActivityPerson.activity` → Activity | `activity_persons` | `activity.activity_persons.all()` |
| `ActivityUser.activity` → Activity | `activity_users` | `activity.activity_users.filter(user=user)` |
| `Attachment.deal` → Deal | `attachments` | `deal.attachments.all()` |
| `Attachment.activity` → Activity | `attachments` | `activity.attachments.all()` |
| `Activity.deal` → Deal | `activities` | `deal.activities.order_by('-occurred_at')` |
| `Activity.campaign` → Campaign | `activities` | `campaign.activities.all()` |
| `Deal.company` → Company | `deals` | `company.deals.all()` |
| `Deal.source_campaign` → Campaign | `source_deals` | `campaign.source_deals.all()` |
| `Contact.company` → Company | `contacts` | `company.contacts.all()` |
| `DealPerson.person` → Person | `deal_persons` | `person.deal_persons.all()`（**★v1.7**：`is_primary=True`で絞れば相手方の主担当が得られる。旧`Deal.primary_person`／`deals_as_primary`は廃止） |
| `ActivityPerson.person` → Person | `activity_persons` | `person.activity_persons.all()` |
| `Company.merged_into` → Company(self) | `merged_companies` | `company.merged_companies.all()` |
| `CompanyDuplicateCandidate.company_a` → Company | `duplicate_candidates_as_a` | |
| `CompanyDuplicateCandidate.company_b` → Company | `duplicate_candidates_as_b` | |

`Attachment.deal`と`Attachment.activity`はいずれも`attachments`だが、参照先モデルが異なるため衝突しない。同様に`deal_persons`（DealとPersonの両方から）も衝突しない。

### 1.4.2 Userへの FK（★v0.10で修正・特に注意）

**★v0.10で修正：既存の実コードを確認した結果、v0.9の提案は既存規則と逆だった。** Userを指すFKは既存コードに15本前後あり、逆参照名を付けているのは`Person.managed_by → persons_managed`・`Contact.managed_by → contacts_managed`の**2本だけ**である。残りは全て`related_name="+"`（逆参照を作らない）で、内訳は`ActionLog.user`、`Contact.created_by`／`updated_by`、`mailings`のUser FK6本（`Campaign.created_by`含む）、`duplicates`の`assigned_to`／`reviewed_by`／`executed_by`／`undone_by`、`tags`の2本である。

つまり既存の規則は**「Userへの監査系FK（`created_by`／`updated_by`／`reviewed_by`等）は`+`にして逆参照を作らない。逆参照を作るのは、業務的に『その人が担当している一覧』を引く必要がある`managed_by`のような場合に限る」**というものだった。v0.9が根拠にした`persons_managed`は規則そのものではなく、2本しかない例外の片方に過ぎなかった。

さらに、v0.9が提案した9本のうち、本仕様書のどのコードも逆参照を使っていない。§7の判定関数は全て`Deal.objects.filter(owner=user)`のような正参照の形である。旧提案のまま実装すると、誰も使わない逆参照アクセサを9本作ることになっていた。

新設モデルからUserへのFKは9本あり、**衝突を避けるため全てに`related_name`の指定は必須**だが、値は用途に応じて使い分ける。

| FK | related_name | 理由 |
|---|---|---|
| `Deal.owner` | `deals_owned` | 「自分がownerの案件一覧」を業務的に引く（§2.6のowner付け替え画面等） |
| `Deal.created_by` | `+` | 監査用。逆参照は使わない |
| `Deal.updated_by` | `+` | 同上 |
| `Activity.user`（実施者） | `activities_performed` | 「自分が実施した活動一覧」＝日報（§3.3）そのもの |
| `Activity.created_by` | `+` | 監査用。逆参照は使わない |
| `DealUser.user` | `deal_users` | 「自分が関与者として登録されている案件」を引く |
| `ActivityUser.user` | `activity_users` | 同上（Activity版） |
| `Attachment.uploaded_by` | `+` | 監査用。逆参照は使わない |
| `Company.created_by` | `+` | 監査用。逆参照は使わない |

逆参照を残すのは4本（`deals_owned`／`activities_performed`／`deal_users`／`activity_users`）のみとし、残り5本（監査系）は既存規則通り`+`とする。§7のコードはいずれも正参照のみを使っているため、この変更によるコード側の書き換えは発生しない。

`CompanyDuplicateCandidate.reviewed_by`も同じ理由で`related_name="+"`とする（既存の慣習に従う）。

**`Company.merged_into`の命名について（★v0.10で追記）**：本表§1.4.1では`merged_companies`としているが、Personの前例は`merged_from_set`（`persons/models.py:43`）であり、異なる命名になっている。読みやすさは`merged_companies`が上回るため変更の必要はないが、「既存の実例から導出」と冒頭で述べている以上、**Personとは意図的に変えたものである**旨をここに明記する。

**★実装時の確認事項**：本表は既存の仕様書類から読み取った命名規則に基づいており、実コード（`persons/models.py`・`contacts/models.py`等）と細部が食い違う可能性がある。**実装着手前に実コードの`related_name`を確認し、規則が異なっていればそちらに合わせる**こと。その場合は本表と§3・§7のコード例も併せて修正する。

## 1.5 Metaの定型（★v0.9で新設）

既存の全モデルが`ordering`・`verbose_name`・`verbose_name_plural`・`__str__`を持つ。新設9モデルにも同様に定義する。`ordering`は画面の既定の並び順を決めるため業務判断が入る。以下を既定とする。

| モデル | ordering | 理由 |
|---|---|---|
| Deal | `['-updated_at']` | 案件一覧は「動いている案件から見る」のが基本。受注予定日順は一覧のソート切り替えで対応する |
| Activity | `['-occurred_at']` | 日報・活動履歴はいずれも新しい順 |
| Company | `['organization']` | 会社一覧は名前順で探す |
| CompanyDuplicateCandidate | `['-score', '-created_at']` | レビューキューはスコアの高い（確からしい）ものから処理する |
| Attachment | `['-created_at']` | 新しく上げたものが上 |
| DealPerson／DealUser／ActivityPerson／ActivityUser | `['created_at']` | 登録順。関与者は追加した順に並ぶのが自然 |

`__str__`はDealが`name`、Companyが`organization`、Attachmentが`original_filename`を返す。

**★v0.10で修正：`activity_type`は`get_activity_type_display()`でラベル展開する**。素直に`f'{occurred_at:%Y-%m-%d} {activity_type}'`と書くと、`activity_type`は`ChoiceField`の内部値（`phone`等）であり、画面に「2026-09-05 phone」のような英語の内部値がそのまま出てしまう。`self.get_activity_type_display()`で表示名（「電話」等）に展開してから使う。

```python
def __str__(self):
    return f'{self.occurred_at:%Y-%m-%d} {self.get_activity_type_display()}'
```

**★v0.10で追加：残り5モデルの`__str__`**（v0.9では未定義だった）。

| モデル | `__str__` |
|---|---|
| DealPerson | `f'{self.deal.name} - {self.person} ({self.get_role_display()})'` |
| DealUser | `f'{self.deal.name} - {self.user} ({self.get_role_display()})'` |
| ActivityPerson | `f'{self.activity} - {self.person} ({self.get_role_display()})'` |
| ActivityUser | `f'{self.activity} - {self.user} ({self.get_role_display()})'` |
| CompanyDuplicateCandidate | `f'{self.company_a_id} ↔ {self.company_b_id} ({self.rank}/{self.review_status})'` |

`CompanyDuplicateCandidate`は既存の`DuplicateCandidate.__str__`（`f"{person_a_id} ↔ {person_b_id} ({rank}/{review_status})"`）と同型に揃えた。DealPerson等の4つも同様に`get_role_display()`でラベル展開する。

**選択肢フィールドを使う`__str__`・画面表示は、常に`get_FOO_display()`でラベルを展開すること。** これは本節に限らず本仕様書全体のコード例に共通する原則であり、内部値（英字のvalue）を利用者向け画面にそのまま出さないという既存のUI規約に基づく。

---

# 第2章 Deal（案件本体）

## 2.1 フィールド定義

**★v1.7で変更：`primary_person`（案件の相手方の主担当）はDealの単一FKとして持たない（重要）**。v1.6までは`Deal.primary_person`（FK, PROTECT, 必須）が正としていたが、たんたんとの壁打ちにより、相手方は「複数人が関与しうる」実態に合わせて`DealPerson`（§5章）に一本化する方針に変更した。相手方の中で誰が主担当かは、`DealPerson.is_primary`（§5.1で新設）で表現する。詳細な設計・移行理由は§2.6.1（旧「primary_person付け替え」から全面改訂）を参照。

**null/blank指定を明記**：商談の初期段階（初回商談直後等）では金額・受注予定日等が未定であることが普通にあり得るため、`name`以外は登録時点で未入力を許容する。これを明記しないと、コード君が全フィールドを登録時必須で実装し、`close_deal()`の`clean()`検証（§2.4）が無関係な項目で失敗する事故につながる。「相手方が最低1名必要」という制約はDeal本体のフィールドではなく、§2.6.1のService層バリデーションで担保する。

**★v0.7で追加：全CharFieldにmax_lengthを明記**。Djangoでは`CharField`に`max_length`が必須属性であり、指定しないと`makemigrations`実行時に`SystemCheckError: (fields.E120)`で確実にクラッシュする。

| 日本語名 | コーディング名 | 型 | 説明 |
|---|---|---|---|
| 案件名 | name | CharField(max_length=255) | 必須 |
| 会社 | company | FK(Company, PROTECT, null=True) | §6章参照。**★v1.7で修正：作成時に「主担当のDealPerson」の`person.company`から自動補完する**（後述） |
| 案件オーナー | owner | FK(User, SET_NULL, null=True) | **★v1.7で呼称変更**（旧「担当者」）。このDealの現在の実務・権限上の管理者。付け替え可能（§2.6参照）。表示ラベルは常に「案件オーナー」を用い、`DealUser.role=primary`（表示ラベル「主担当」、§2.9.5）と混同されないようにする |
| 作成者 | created_by | FK(User, SET_NULL, null=True) | 監査用。変更不可 |
| ステージ | stage | CharField(max_length=30, choices, **default=Deal.Stage.INITIAL_MEETING**) | §2.2参照。**★v0.7でdefault追加**（新規作成時は「初回商談」から開始） |
| 確度 | probability | IntegerField(null=True, blank=True, **validators=[MinValueValidator(0), MaxValueValidator(100)]**) | %。ステージとは独立入力、連動処理なし。登録時未定でよい。**★v0.8で値域制約を追加**（-10%・120%等のダーティデータを防ぐ） |
| 種別 | deal_type | CharField(max_length=30, choices=DealType.choices, blank=True) | 案件の性質。3値、§2.9参照。登録時未定でよい |
| 金額 | amount | DecimalField(max_digits=12, decimal_places=0, null=True, blank=True, **validators=[MinValueValidator(0)]**) | 税抜統一。登録時未定でよい。**★v0.8で値域制約を追加**（マイナス金額が入ると売上予測集計が狂うため） |
| 受注予定日 | expected_close_date | DateField(null=True, blank=True) | 登録時未定でよい |
| 成約確定日 | closed_at | DateField(**null=True, blank=True**) | 受注/失注時に入力。§2.4参照（専用フォーム経由での入力必須化）。**★v0.7で修正：`blank=True`を追加**。`null=True`だけではフォーム層で必須扱いになり、オープンステージでの新規Deal登録自体ができなくなる罠があったため。必須化は§2.4の`clean()`のみで制御する |
| 案件発生源 | lead_source | CharField(max_length=30, choices=LeadSource.choices, blank=True) | どの経路で接点ができたか。8値、§2.9参照。登録時未定でよい |
| 発生源キャンペーン | source_campaign | FK(Campaign, SET_NULL, null=True, blank=True) | **★v0.8で新設**。メールキャンペーン起点の案件で、どの配信から生まれたかを保持する。`lead_source`との整合は§2.4の`clean()`で担保。依存の向きは`deals → mailings`（`mailings`は`deals`を知らないため循環しない、§1.2） |
| 失注理由 | lost_reason | CharField(max_length=255, blank=True) | 失注時のみ必須。§2.4参照 |
| 概要メモ | memo | TextField(blank=True) | |
| 論理削除フラグ | is_archived | BooleanField(default=False) | |
| 更新者 | updated_by | FK(User, SET_NULL, null=True) | **v0.2で追加**（Contactと同じ監査パターンに統一） |
| 作成日時／更新日時 | created_at／updated_at | DateTimeField | |

**★v0.9で確定、★v1.7で`DealPerson`基準に改訂：`Deal.company`の自動補完ルール**。§6.2で定義した`Person.company`プロパティ（`primary_contact.company`を返す読み取り専用）の使い道が未定義だったため、ここで確定する。`primary_person`廃止（§2.6.1）に伴い、補完元を「主担当の`DealPerson`」に変更した。

- **Deal作成時、`company`が未指定なら、その場で`is_primary=True`として登録される`DealPerson.person.company`を初期値として設定する。** 案件の相手が決まれば会社も決まるのが通常であり、毎回選ばせるのは手間になるため
- **フォーム上で上書き可能とする。** 親会社と契約するが窓口は子会社の担当者、というケースが実際にあるため、自動補完はあくまで初期値である
- **作成後は追随させない。** 主担当の相手方が転職等で別会社に変わっても、`Deal.company`は変更しない。案件は「その時点でどの会社との商談だったか」の記録であり、相手の転職で過去の案件の相手先会社が書き換わるのは誤りである。担当者の転職に伴い案件そのものを引き継ぐ場合は、人が明示的に`company`を変更する
- **主担当を後から付け替えた場合も、`company`は自動変更しない**（同じ理由）。画面上に「相手方の会社と案件の会社が異なります」という注意表示を出すに留める

**★v0.16で追加：実現方式はバックエンドの`form_valid`で行う（JS等の動的UIにしない）**。上記の「未指定なら初期値を設定する」を素直に実装しようとすると、「相手方を選んだ瞬間に`company`のプルダウンをJS/HTMXで動的に書き換える」という複雑なUIに走りがちだが、**採らない**。

**★v1.8で修正：v1.7のコード例は`create_deal_with_primary_person()`（§2.6.1）を一度も呼んでおらず、主担当DealPersonが実際には作られないまま`primary_dp`を検索するだけの欠陥があった（レビューで判明）。**`primary_person`（相手方）はDeal作成フォームの必須入力とし、`DealCreateView.form_valid()`から`create_deal_with_primary_person()`を実際に呼び出す形に修正する。

```python
# deals/forms.py
class DealForm(forms.ModelForm):
    # ★v1.8：primary_personはDeal本体のフィールドではなくなったため、
    # フォームだけが持つ追加フィールドとして必須指定する（§2.6.1参照）。
    primary_person = forms.ModelChoiceField(
        queryset=Person.objects.exclude(status=Person.Status.MERGED),
        label='相手方（主担当）',
    )

    class Meta:
        model = Deal
        fields = ['name', 'company', 'stage', 'probability', 'deal_type', 'amount',
                   'expected_close_date', 'lead_source', 'source_campaign', 'memo']
        # owner・is_archivedは§2.6の通り別画面。primary_personはDeal本体のフィールドではない


# deals/views.py
class DealCreateView(...):
    form_class = DealForm

    def form_valid(self, form):
        """★v1.8で全面改訂：create_deal_with_primary_person()を実際に呼ぶ。
        Deal作成は「相手方を選ぶ」ことを必須ステップとして同一フォーム内で完結させる
        （他モデルの「本体のみ登録→サブフォームで追加」という2段構えの例外、§9.2参照）。
        """
        deal = form.save(commit=False)
        primary_person = form.cleaned_data['primary_person']
        create_deal_with_primary_person(deal, primary_person, self.request.user)

        primary_dp = deal.deal_persons.filter(is_primary=True).select_related(
            "person__primary_contact__company"
        ).first()
        if not deal.company_id and primary_dp and primary_dp.person.company:
            deal.company = primary_dp.person.company
            deal.save(update_fields=["company", "updated_at"])

        self.object = deal
        return redirect(self.get_success_url())
```

`company`フィールドは通常のセレクトボックスとしてフォームに置き、送信後に上記の通り**未入力の場合のみ**サーバ側で補完する。画面上は「company を選ばなければ、保存時に相手方の会社が自動で入る」という単純な挙動になり、フロントエンドに補完ロジックを持たせる必要がない。これはCLAUDE.md §7の「CSS/JSは既存クラス・関数のみ、新規ファイル追加禁止」とも整合し、この機能のためだけに動的UIのJSを新設せずに済む。

## 2.2 ステージ（8値：オープン6＋クローズ2）

```python
class Deal(models.Model):
    class Stage(models.TextChoices):
        INITIAL_MEETING = 'initial_meeting', '初回商談'
        NEEDS_ANALYSIS = 'needs_analysis', 'ヒアリング・課題整理'
        QUOTATION = 'quotation', '見積提示'
        UNDER_REVIEW = 'under_review', '検討中'
        INTERNAL_APPROVAL = 'internal_approval', '社内稟議中'
        NEGOTIATION = 'negotiation', '交渉'
        WON = 'won', '受注'
        LOST = 'lost', '失注'
```

見込み客との接触自体はActivityが担い、Dealは「商談化した」時点（`initial_meeting`）から開始する。「検討中」「社内稟議中」は放置案件検出のために特に重要なステージ（§2.4参照）。

## 2.3 オープン／クローズ判定

**循環import回避のため、`config/constants.py`にはDeal.Stageを参照させず、文字列リテラルの集合として定義する。**

```python
# config/constants.py（deals.models には依存しない）
DEAL_CLOSED_STAGE_VALUES = {'won', 'lost'}

# deals/models.py
class Deal(models.Model):
    @property
    def is_closed(self):
        return self.stage in DEAL_CLOSED_STAGE_VALUES
```

## 2.4 バリデーション（クローズ遷移は専用フォーム経由）

成約確定日・失注理由は、DB制約にはせず、**モデルの`clean()`によるバリデーション**で担保する。

- `stage`が`won`のとき：`closed_at`が必須
- `stage`が`lost`のとき：`closed_at`・`lost_reason`が必須
- **★v0.8で追加**：`closed_at`は**未来日禁止**（`Activity.occurred_at`の「実施済みの記録のみ」と一貫させる。「来月成約した」という先行入力を防ぐ）
- **★v0.8で追加**：`source_campaign`に値がある場合、`lead_source`は`campaign`でなければならない

**`source_campaign`と`lead_source`の整合ルール（★v0.8で新設）**。この2つは役割が異なるため併存させる。`lead_source`は**発生源の分類**（他の7経路と横並びで「案件の何割がキャンペーン起点か」を数える集計軸）、`source_campaign`は**どの配信か**という特定である。整合は片方向のみ強制する。

- `source_campaign`に値がある → `lead_source='campaign'`でなければエラー
- `lead_source='campaign'`だが`source_campaign`が空 → **許容する**（キャンペーン経由だと分かっているが、どの配信までは特定できないケースが実際にあるため）

画面側では、`lead_source`で「メールキャンペーン」を選択したときのみ`source_campaign`の入力欄を有効化する。

**★v1.8で削除：「フォロー漏れ検出画面からDeal作成へ遷移する動線」という記述は撤回する**。この一文はv0.8から存在していたが、§3.5.2が実際に確定している画面遷移は「フォロー漏れ検出一覧→Activity新規作成画面（`person`・`campaign`を初期値引き継ぎ）」までであり、そこからさらにDeal作成へ進む動線は本仕様書のどこにも設計されていなかった。存在しない画面への参照であることがレビューで判明したため削除する。キャンペーン起点の案件で`source_campaign`を人手で追跡する手段が無くなるわけではなく、上記の通り`lead_source='campaign'`かつ`source_campaign`が空の状態を正式に許容しているため、実害はない。Activity側に既に`campaign`が記録されているため、キャンペーンの成果集計はActivity経由でも代替できる。「フォロー漏れ検出／Activityから直接Deal作成に進める動線」自体は、必要になった時点で次フェーズとして改めて設計する（本版ではスコープ外とする）。

```python
def clean(self):
    super().clean()
    if self.stage == Deal.Stage.WON and not self.closed_at:
        raise ValidationError({'closed_at': '受注時は成約確定日が必須です。'})
    if self.stage == Deal.Stage.LOST:
        if not self.closed_at:
            raise ValidationError({'closed_at': '失注時は成約確定日が必須です。'})
        if not self.lost_reason:
            raise ValidationError({'lost_reason': '失注時は失注理由が必須です。'})
    if self.closed_at and self.closed_at > timezone.localdate():
        raise ValidationError({'closed_at': '成約確定日に未来日は指定できません。'})
    if self.source_campaign_id and self.lead_source != Deal.LeadSource.CAMPAIGN:
        raise ValidationError({
            'lead_source': '発生源キャンペーンを指定する場合、案件発生源は「メールキャンペーン」にしてください。'
        })
```

**★v0.4で修正**：`clean()`は`ModelForm`経由の保存時にしか自動発火しない。当初「一覧のドロップダウンでステージをサッと変える」運用を全ステージ共通で想定していたが、これだと`Service`層が`save()`を直接呼ぶため、**受注/失注という最も重要な遷移で`clean()`が発火せず、確定日・失注理由が未入力のまま保存できてしまう**（防衛が実質ゼロになる）ことが判明した。

そのため、**遷移の種類によって経路を分ける**。

- **オープンなステージ間の遷移**（例：「検討中」→「社内稟議中」）：従来通り、一覧のドロップダウンで`clean()`を経由しない軽い保存が可能（クイックな運用の自由度を維持）
- **「受注」または「失注」への遷移**：一覧のドロップダウンでは完結させず、**確定日・失注理由の入力を伴う専用フォーム（`ModelForm`経由）を必ず開かせる**。これにより`clean()`が確実に発火し、必須項目が空のまま保存されることを防ぐ

実行者の制限（担当者本人のみ／上長承認必須等）は設けない。既存の所有者ベース制御（§7.1）の範囲内で、通常の編集権限を持つ者が実行できる。承認フロー等のワークフロー機能は、将来ワークフロー機能を実装する際に併せて検討する事項として保留する。

**確度のクローズ時強制（100%/0%）は行わない。** `Deal.probability`フィールドには手を入れず、パイプライン期待値の集計ロジック側で吸収する。

```python
# 集計時のイメージ（レポート/集計機能側）
# ★v0.9：amount / probability はいずれも null 許容（§2.1）のため、
# 無条件に乗算すると初期ステージの Deal で TypeError になる。
# 未入力の案件は「期待値の母数から外す」ため None を返す。
def expected_value(deal):
    if deal.amount is None:
        return None                      # 金額未入力 → 集計対象外
    if deal.stage == Deal.Stage.WON:
        return deal.amount               # 確度に関わらず100%扱い
    if deal.stage == Deal.Stage.LOST:
        return 0                         # 集計対象から除外（値は確定して0）
    if deal.probability is None:
        return None                      # 確度未入力 → 集計対象外
    return deal.amount * deal.probability / 100
```

**★v0.9で確定：金額・確度が未入力の案件の扱い**。`amount`・`probability`はどちらも「商談の初期段階では未定であることが普通」という理由でnull許容にしている（§2.1）ため、期待値集計での扱いを決めておく必要がある。

- **`amount`がNoneのDealは期待値集計の対象外**とし、金額としては足し込まない。ただし**件数だけは「金額未入力 N件」として画面に別途表示する**。黙って落とすと、パイプラインの合計額を見た人が「これが全案件の期待値だ」と誤解するため
- **`probability`がNoneの場合も対象外**とする。**0として扱わない。** 0扱いにすると「確度を入れ忘れた案件」と「見込みがゼロの案件」が集計上まったく区別できなくなる
- `stage`が`lost`の場合の`0`は、上記2つと意味が違う。**確度未入力ではなく「値が0だと確定している」**ため、母数に含めた上で0を足す

集計を行う画面には「金額未入力 N件」「確度未入力 N件」を併記し、入力を促す導線とする。

## 2.5 ステージ変更履歴・放置案件検出

専用テーブルは持たず、`ActionLog.record()`を使う。

**★v0.6で全面修正**。v0.5時点の実装には以下の問題があった。

- `close_deal`が`deal.full_clean()`を呼んでいたが、これは`clean()`だけでなく全フィールドの`clean_fields()`・unique検証まで走らせる。§2.1で`amount`等をnull許容にしたにも関わらず、以前の記述のままだと必須項目扱いとなり、**失注登録しようとしただけで無関係な項目のバリデーションエラーになる**事故が起きる。`clean()`のみを直接呼ぶ形に修正する
- `close_deal`が`won`に遷移する際、過去の`lost_reason`をクリアしていなかった（失注から受注に訂正した場合に古い理由が残留する）
- `updated_by`を設定していなかった
- `deal.save()`と`ActionLog.record()`が同一トランザクションで実行される保証がなかった
- `'lost'`／`'won'`という文字列リテラルを直書きしていた。`config/constants.py`の`DEAL_CLOSED_STAGE_VALUES`が文字列リテラルの集合なのは循環import回避が目的であり、`deals/models.py`内では`Deal.Stage.LOST`等の列挙型を使ってよい

```python
from django.db import transaction


def change_deal_stage(deal, new_stage, user):
    """オープンなステージ間の遷移専用。クローズ系（won/lost）への遷移はここでは行わない。"""
    if new_stage in DEAL_CLOSED_STAGE_VALUES:
        raise ValueError(
            'won/lost への遷移は change_deal_stage() では行えません。close_deal() を使ってください。'
        )
    with transaction.atomic():
        old_stage = deal.stage
        deal.stage = new_stage
        deal.updated_by = user
        deal.save(update_fields=['stage', 'updated_by', 'updated_at'])
        ActionLog.record(
            user=user, action='stage_changed', content_object=deal,
            data={'from_stage': old_stage, 'to_stage': new_stage},
        )


def close_deal(deal, new_stage, closed_at, lost_reason, user):
    """クローズ系（won/lost）への遷移専用。専用フォーム（ModelForm経由）からのみ呼ばれる想定。"""
    if new_stage not in DEAL_CLOSED_STAGE_VALUES:
        raise ValueError('close_deal() は won/lost への遷移専用です。')
    with transaction.atomic():
        old_stage = deal.stage
        deal.stage = new_stage
        deal.closed_at = closed_at
        # ★v0.6で修正：won復帰時は過去の失注理由をクリアする（残留防止）
        deal.lost_reason = lost_reason if new_stage == Deal.Stage.LOST else ''
        deal.updated_by = user
        # ★v0.6で修正：full_clean()ではなくclean()のみ呼ぶ。§2.4で定義した
        # 「won/lostに必要な項目」だけを検証し、amount等の無関係な必須チェックで
        # 失注登録が弾かれる事故を防ぐ。
        deal.clean()
        deal.save(update_fields=['stage', 'closed_at', 'lost_reason', 'updated_by', 'updated_at'])
        ActionLog.record(
            user=user, action='stage_changed', content_object=deal,
            data={'from_stage': old_stage, 'to_stage': new_stage},
        )
```

放置案件検出は、Deal一覧に「最終接触日（そのDealに紐づく最新Activityの実施日時）」列を出し、N日以上更新のない案件を絞り込めるようにする（NextAction代替）。

**★v0.9で確定：実装方式は`Subquery`＋`OuterRef`の`annotate`とする**。素直に書くと行ごとに1クエリを発行するN+1になり、一覧の件数に比例して遅くなる。またこの値で絞り込む（「30日以上接触なし」）要件があるため、Python側での算出では対応できない。

```python
from django.db.models import OuterRef, Subquery


def deals_with_last_contact(user):
    """§7.1 の visible_deals_for に最終接触日を付与した QuerySet を返す。"""
    last_activity = (
        Activity.objects
        .filter(deal=OuterRef('pk'), is_archived=False)   # アーカイブ済みは接触に数えない
        .order_by('-occurred_at')
        .values('occurred_at')[:1]
    )
    return visible_deals_for(user).annotate(
        last_contact_at=Subquery(last_activity)
    )
```

- **合成の順序**：`visible_deals_for(user)`（§7.1）で可視範囲を絞ってから`annotate`する。逆順にすると、見えない案件に対してもサブクエリが評価される
- **絞り込み**：`filter(last_contact_at__lt=timezone.now() - timedelta(days=n))`で「N日以上接触なし」を表現する。**接触が1件もない案件は`last_contact_at`がNULLになり、この条件からは外れる**ため、`Q(last_contact_at__isnull=True)`をORで加える（作成したまま一度も動いていない案件こそ放置案件であるため）
- **アーカイブ済みActivityは除外する**（§3.5.2のフォロー漏れ検出と同じ理由。アーカイブは「その活動はなかったことにする」操作である）

## 2.6 owner付け替え（退職・異動時の引き継ぎ）

- `Deal.owner`は退職・異動時に**管理者、または現在のowner本人**が付け替え可能
- 変更履歴は`ActionLog.record()`で記録（action='owner_changed'）
- `created_by`とは別フィールドのため、付け替えても起票者の記録は保持される

**★v0.15で確定：Deal本体の通常編集フォームから`owner`を除外する（重要）**。v0.14までのレビューで、「§5.1の`clean()`はDealPerson／DealUser作成・更新時にしか働かず、Deal編集フォームで`owner`を直接書き換えた場合には再検証されない」という指摘があった。

これは`clean()`の限界の指摘としては正しいが、対策は「Deal本体の`clean()`に衝突チェックを足す」ことではなく、**そもそも通常編集フォームで`owner`を直接変更できないようにする**ことで解決する。理由は2つある。

- `owner`については既に本節で「`can_edit_deal`で丸ごとゲートすると権限が広すぎる」という理由から専用画面に切り出す設計にしている。通常編集フォームからも変更できてしまうと、この設計そのものが迂回されてしまう
- バリデーション（`clean()`）に「衝突していたら該当のDealPerson/DealUserを削除する」という**副作用**を持たせるのはDjangoのベストプラクティスに反する。`clean()`は検証のみを行い、削除は明示的なService関数の責務とする方が筋が良い（これは§4.4.2で`delete_attachment()`をトランザクション内の明示的な処理としたのと同じ考え方）

**Deal編集フォーム（`DealUpdateView`、`change_deal`権限で開く通常の編集画面）のフォームフィールドから`owner`を除外する。** 画面には現在の値を読み取り専用で表示し、変更したい場合は専用の付け替え操作（本節の`reassign_deal_owner()`）に誘導する。この結果、§5.1の`clean()`が守る前提（`owner`の変更は専用関数を必ず経由する）が構造的に保証され、通常編集フォームを経由した迂回が起こらない。

**★v1.7の注記**：v1.6までここに含まれていた`primary_person`の除外は、`primary_person`自体がDeal本体のフィールドではなくなった（§2.6.1）ため、本節の対象外になった。相手方の主担当（`DealPerson.is_primary`）の付け替えは、そもそも通常のDeal編集フォームの管轄外（`DealPerson`は別のサブフォームで扱う、§5.2）であり、同じ懸念は生じない。

`can_archive_deal`と同型の判定関数、および`ActionLog`記録を含む実行関数を新設する。

```python
def can_reassign_deal_owner(user, deal):
    """owner付け替えの権限判定。can_archive_deal と同型（owner本人 or 管理者のみ）。
    can_edit_deal では代用しない（DealUser全員に真を返してしまうため）。"""
    if user.has_perm('deals.edit_all_deals'):
        return True
    if not user.has_perm('deals.change_deal'):
        return False
    return deal.owner_id == user.id


def reassign_deal_owner(deal, new_owner, user):
    """owner付け替えの実行。呼び出し前に can_reassign_deal_owner(user, deal) の
    チェック（Service層）と、Viewの change_deal 権限（View層）が前提。

    ★v0.14で追加：new_owner が既に DealUser として登録されている場合、
    その DealUser レコードを削除してから owner に昇格させる。§5.1 の
    DealUser.clean() が「owner 本人を DealUser にも登録すること」を禁じて
    いるため、新規に DealUser を作る経路（サブフォーム）ではこの clean() が
    効くが、既存の DealUser 側は削除しない限りそのまま残ってしまう。
    """
    with transaction.atomic():
        old_owner_id = deal.owner_id

        # ★v0.14：new_owner が既存の DealUser なら先に削除する。
        # 「呼び出し元のフォームで行ってもよい」という両論併記をやめ、
        # このService関数の責務として一本化する（transaction.atomic() の
        # 外で行うと、途中失敗時に「owner は変わったが DealUser は残っている」
        # という不整合な中間状態が起こり得るため）。
        deal.deal_users.filter(user=new_owner).delete()

        deal.owner = new_owner
        deal.updated_by = user
        deal.save(update_fields=['owner', 'updated_by', 'updated_at'])
        ActionLog.record(
            user=user, action='owner_changed', content_object=deal,
            data={
                'old_owner_id': str(old_owner_id) if old_owner_id else None,
                'new_owner_id': str(new_owner.id),
            },
        )
```

owner付け替えの画面は、Deal編集フォームに含めず**専用のフォーム・エンドポイント**とする（`permission_required = 'deals.change_deal'`、View層）。

**★v0.14で修正：「新ownerが既にDealUserなら削除して昇格させる」処理の担当を確定**。v0.13までは「`reassign_deal_owner()`の中、または呼び出し元のフォームで行う」と両論併記にしていたが、これでは実装者がどちらで書くか迷う。**`reassign_deal_owner()`自身の責務として一本化した**（上記コード）。トランザクション境界の外で行うと、DealUserの削除とownerの付け替えの間で失敗した場合に「ownerは変わったがDealUserは残っている」という不整合な中間状態が生じ得るため、必ず同一トランザクション内で行う。

## 2.6.1 相手方の主担当（`DealPerson.is_primary`）の設計・付け替え（★v1.7で全面改訂）

**★v1.7で確定：`Deal.primary_person`（本体の単一FK）を廃止し、`DealPerson.is_primary`に一本化する。** v0.15〜v1.6までは、相手方の主担当は`Deal.primary_person`（FK, PROTECT, 必須）を正としていたが、たんたんとの壁打ちにより、以下の理由で方針を変更した。

- 案件の相手方は実際には複数人が関与するのが普通で、`DealPerson`が既にその全員を保持している。「代表1名だけは別枠のFK、残りは別テーブル」という二重構造は、本来同じ集合（相手方）を不自然に分割するものだった
- v1.6で実際に発生した事故（`DealPerson`に独自の`is_primary`フラグが追加され、`Deal.primary_person`と意味が衝突した）は、突き詰めると「相手方の主担当という同一概念を表現する場所が2つ存在しうる」という設計そのものに起因していた。場所を1つに統合すれば、この種の事故は構造的に起こらなくなる

**この変更に伴い、`Deal.primary_person`という呼称・フィールドは仕様書から完全に削除する。** 以降、相手方の主担当は常に「`is_primary=True`の`DealPerson`」を指す。

### DealPersonのフィールド追加（§5.1本体の定義に統合）

`DealPerson`に`is_primary`（BooleanField, default=False, 表示ラベル「主担当」）を追加する。表示ラベルは`DealUser.role=primary`（同じく表示ラベル「主担当」、§2.9.5）と同じ言葉を使うが、**`DealPerson`（相手方＝客先）と`DealUser`（社内担当者）は別テーブル・別画面であり、同じ言葉が別テーブルで使われても実務上の混同は起きないと判断し、たんたんの了承のもと維持する**。一方、`Deal.owner`は表示ラベルを「案件オーナー」に変更済み（§2.1）であり、`owner`・`DealUser.role=primary`・`DealPerson.is_primary`の3者が同じ言葉で衝突する状態は解消されている。

**一意性の保証（DB制約）**：

```python
# deals/models.py
class DealPerson(models.Model):
    ...
    is_primary = models.BooleanField(default=False, verbose_name='主担当')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['deal', 'person'], name='unique_deal_person'),
            # ★v1.7で追加：同一Dealでis_primary=Trueは常に最大1件
            models.UniqueConstraint(
                fields=['deal'],
                condition=models.Q(is_primary=True),
                name='unique_deal_primary_person',
            ),
        ]
```

**必須性の保証（Service層）**：`primary_person`が持っていた「Dealには必ず1名の相手方主担当がいる」というNOT NULL相当の保証は、別テーブルの話になるためDBだけでは表現できない。Deal作成のService層（`DealCreateView.form_valid()`、§2.1参照）で、保存直後に「`is_primary=True`の`DealPerson`が0件ならエラーとする」バリデーションを設ける。

```python
def create_deal_with_primary_person(deal, primary_person, user):
    """Deal新規作成時、主担当となるDealPersonを同時に作成する。
    Deal作成フォームは『相手方を最低1名選ぶ』ステップを必須とし、
    選ばれた最初の1名を is_primary=True で登録する。"""
    with transaction.atomic():
        deal.created_by = user
        deal.save()
        DealPerson.objects.create(
            deal=deal, person=primary_person, is_primary=True,
            role=PersonRole.DECISION_MAKER,  # 画面側でrole選択を必須にする場合はフォーム入力値を渡す
        )
    return deal
```

**付け替え専用の判定・実行関数**：相手方の主担当を変更する操作は「案件がどの相手との商談か」という事実そのものを書き換える、`owner`の付け替えと同じ重みを持つ操作であり、引き続き**専用の関数を経由させる**。

```python
def can_reassign_deal_primary_person(user, deal):
    """相手方の主担当付け替えの権限判定。can_reassign_deal_owner と同型とする
    （owner本人 or 管理者のみ）。相手方の変更は担当者本人が主体的に行う操作であり、
    単なる同席者・協力担当に開放する理由がないため。"""
    if user.has_perm('deals.edit_all_deals'):
        return True
    if not user.has_perm('deals.change_deal'):
        return False
    return deal.owner_id == user.id


def reassign_deal_primary_person(deal, new_primary_person, user):
    """相手方の主担当付け替えの実行。呼び出し前に
    can_reassign_deal_primary_person(user, deal) のチェック（Service層）と、
    Viewの change_deal 権限（View層）が前提。

    ★v1.7：Deal本体のFK差し替えではなく、DealPerson.is_primaryを
    「旧主担当はFalseに戻す→新主担当はTrueにする（既存DealPersonなら更新、
    未登録ならroleを問い合わせた上で新規作成）」という2段階の更新に変更した。
    unique_deal_primary_person制約に一時的にも違反しないよう、
    旧主担当を先にFalseへ更新してから新主担当をTrueにする順序を守ること。
    """
    with transaction.atomic():
        old_primary = deal.deal_persons.filter(is_primary=True).first()
        old_primary_person_id = old_primary.person_id if old_primary else None

        if old_primary:
            old_primary.is_primary = False
            old_primary.save(update_fields=['is_primary'])

        new_dp, created = DealPerson.objects.get_or_create(
            deal=deal, person=new_primary_person,
            defaults={'is_primary': True, 'role': PersonRole.DECISION_MAKER},
        )
        if not created:
            new_dp.is_primary = True
            new_dp.save(update_fields=['is_primary'])

        ActionLog.record(
            user=user, action='primary_person_changed', content_object=deal,
            data={
                'old_primary_person_id': str(old_primary_person_id) if old_primary_person_id else None,
                'new_primary_person_id': str(new_primary_person.id),
            },
        )
```

相手方の主担当付け替えの画面も、owner付け替えと同じく専用のフォーム・エンドポイントとする（`permission_required = 'deals.change_deal'`）。`Deal.company`（§2.1）は主担当変更時に自動追随させない方針を既に確定しているため（相手の転職で過去の案件の会社が書き換わるのは誤りであるため）、この関数もその方針をそのまま踏襲し、`company`には触れない。

**★v1.8で追加：主担当DealPersonの削除ガード（重要）**。v1.6までは`Deal.primary_person`がPROTECTかつ必須FKだったため、「Dealには常に1名の相手方主担当がいる」という不変条件はDBスキーマ自体が保証していた。v1.7で`DealPerson.is_primary`に一本化した際、部分ユニーク制約（`unique_deal_primary_person`）で「同時に2人以上が主担当になる」ことは防いだが、「主担当が0人になる」ことを防ぐ歯止めが抜けていた（レビューで判明）。`DealPerson`の追加・削除は通常のサブフォーム操作（`can_edit_deal`のみが条件、§5.2）で行えるため、このままでは主担当の`DealPerson`をうっかり削除でき、そのDealは主担当0名の状態に静かに陥る。

`DealPerson`の削除は、単純な`.delete()`ではなく専用のService関数を経由させ、`is_primary=True`の行は直接削除させない。

```python
def delete_deal_person(deal_person, user):
    """DealPersonの削除。呼び出し前にcan_edit_deal(user, deal)のチェック
    （View層）が前提。

    ★v1.8で新設：is_primary=Trueの行を直接削除すると、Dealが相手方
    主担当0名の状態に陥る（reassign_deal_primary_person()のような
    付け替え専用関数を経由しない静かな不整合）。これを防ぐため、
    is_primary=Trueの行は削除を拒否し、先にreassign_deal_primary_person()
    で別の相手方に付け替えることを要求する。
    """
    if deal_person.is_primary:
        raise ValueError(
            '主担当の相手方は削除できません。'
            '先に別の相手方を主担当に指定してから削除してください。'
        )
    deal_person.delete()
```

`DealDeletePersonView`（§9.2）は`rel.delete()`を直接呼ぶのではなく、この`delete_deal_person()`を経由するよう実装指示書に明記する（§9.2参照）。`ValueError`はViewの`try/except`でフォームエラーとして表示し、500にしない（他のService関数の入力検証、§6.5.5の`execute_company_merge()`と同じ扱い）。

**★v1.7の申し送り：既存参照箇所の書き換え**。`Deal.primary_person`を直接参照していた以下の箇所は、`DealPerson`経由（`deal.deal_persons.filter(is_primary=True)`）に書き換える。

- 案件一覧の検索フィルタ（`DealListView`、旧`Q(primary_person__primary_contact__last_name__icontains=q)`等）
- `Deal.company`自動補完（§2.1、修正済み）
- Deal作成フォーム（`primary_person`の直接選択欄を廃止し、「相手方を選ぶ」ステップに置き換える。§9.2に段取りを追記）

**★v1.7の申し送り：データ移行**。本機能はまだ実運用に投入されていない（既存のDealレコードが業務データとして存在しない）ため、`primary_person`から`DealPerson`への丁寧な変換マイグレーションは不要とする。開発環境ではCLAUDE.md §4の「開発DBは削除してOK」の方針に従い、`Deal.primary_person`フィールドを削除するマイグレーションを流すだけでよい（既存の開発用Dealデータは再作成する）。

## 2.7 Deal / Project の切り分け

Dealは「受注または失注で完結する商談」のスコープに限定する。受注後のプロジェクト進捗管理は将来`Project`という別モデルで実装し、`Project.deal`（FK, 1:1想定）で連携する。ProjectPerson／ProjectUserもDealPerson／DealUserと同型で新設する想定。

## 2.8 Dealアーカイブ時の関連レコードの扱い（★v0.9で新設）

Dealを`is_archived=True`にしたとき、紐づくActivityは**そのまま残す**（連鎖してアーカイブしない）。

理由は、DealのアーカイブとActivityのアーカイブが別の意味を持つためである。Dealのアーカイブは「この案件はもう追わない」という案件側の判断であり、そこで行われた訪問や電話が「なかったことになる」わけではない。活動記録は担当者本人の実績でもあり、案件の都合で消えてはならない。

その上で、表示側の扱いを以下に定める。

- **日報一覧（§3.3）にはそのまま表示する。** その日に活動を行ったという事実は、案件の状態と関係なく変わらないため
- **Deal詳細画面の活動履歴もそのまま表示する**（Dealごとアーカイブされているので、通常の導線からは開かれない）
- **§2.5の「最終接触日」の算出は、Activity側の`is_archived`のみで判定する**（アーカイブ済みDealはそもそも一覧に出ないため、Deal側の状態は関係しない）
- **§3.5.2のフォロー漏れ検出も、Activity側の`is_archived`のみで判定する**

Companyのアーカイブ（§6.6）についても同様に、紐づくContact・Dealは連鎖させずそのまま残す。

## 2.9 選択肢（TextChoices）の実体定義（★v0.8で新設）

v0.7時点では`Deal.Stage`以外の選択肢が「日本語で候補を列挙した文章」しかなく、value名が未定義だった。この状態で指示書に渡すとコード君が独自命名し、後の修正コストが高くつくため、全フィールドのvalue／labelを本節で確定する。

配置は既存方針（CLAUDE.md §14）に従い、**モデル固有の選択肢はモデル内部クラス**として定義する。今回、複数アプリで共有する選択肢はないため`config/constants.py`には追加しない（§2.3の`DEAL_CLOSED_STAGE_VALUES`のみ既存通り）。

**選択肢はコード内固定（TextChoices）とする。** ディストリビュータ・エンドユーザーごとに値を変えたいという要望が実際に出た時点でマスタテーブル化を検討する。要望が出る前に汎用化すると、集計軸としての一貫性を失った上に管理画面が増えるため。

### 2.9.1 Deal.deal_type（案件の性質）

```python
class DealType(models.TextChoices):
    NEW = 'new', '新規開拓'
    EXPANSION = 'expansion', '既存深耕'
    RENEWAL = 'renewal', '更新・継続'
```

このフィールドが答えるべき問いは「売上のうち新規獲得はどれだけあるか」であり、新規／既存／維持の3分類がその最小単位である。**「リプレイス（他社製品からの置き換え）」は独立させず`expansion`に含める。** IT・SaaSベンダー特有の区分であり、FG2の利用者は業種を問わないため、多くの導入先で永久に使われない選択肢を1つ残すことになるため。

### 2.9.2 Deal.lead_source（案件発生源）

```python
class LeadSource(models.TextChoices):
    EXHIBITION = 'exhibition', '展示会・イベント'
    REFERRAL = 'referral', '紹介（既存客・取引先・知人）'
    INBOUND_WEB = 'inbound_web', 'Webサイトからの問い合わせ'
    INBOUND_DIRECT = 'inbound_direct', '電話・メールでの直接問い合わせ'
    CAMPAIGN = 'campaign', 'メールキャンペーン'
    EXISTING_CUSTOMER = 'existing_customer', '既存客からの相談・追加依頼'
    OUTBOUND = 'outbound', '自社からの営業（テレアポ・訪問・DM）'
    OTHER = 'other', 'その他'
```

「問い合わせ」を`inbound_web`と`inbound_direct`に分けているのは、この2つが**打ち手の異なる別チャネル**だからである。Web経由が伸びていればサイト・SEOへの投資が効いており、電話が鳴っているなら既存の認知や紹介網が効いている。同じ「問い合わせ」に混ぜると、どちらに投資すべきかが読めなくなる。

`campaign`と`Deal.source_campaign`（FK）の関係は§2.4参照。

### 2.9.3 Activity.activity_type／direction

```python
class ActivityType(models.TextChoices):
    PHONE = 'phone', '電話'
    VISIT = 'visit', '訪問'
    EMAIL = 'email', 'メール'
    WEB_MEETING = 'web_meeting', 'Web会議'
    OTHER = 'other', 'その他'


class Direction(models.TextChoices):
    OUTGOING = 'outgoing', '発信'
    INCOMING = 'incoming', '受信'
```

`direction`は電話・メールのみ意味を持つため、それ以外は空でよい（§3.1）。

### 2.9.4 DealPerson.role／ActivityPerson.role（社外の相手）

```python
class PersonRole(models.TextChoices):
    DECISION_MAKER = 'decision_maker', '決裁者'
    CONTACT_WINDOW = 'contact_window', '窓口'
    TECHNICAL = 'technical', '技術担当'
    ATTENDEE = 'attendee', '同席者'
    OTHER = 'other', 'その他'
```

DealPersonとActivityPersonで同一の選択肢を使う。両モデルは`deals`／`activities`という別々のアプリに属するが、選択肢の実体は`deals`側に定義し、`activities`側がそれをimportする形とする（§1.2の依存の向き`activities → deals`と一致するため循環しない）。

### 2.9.5 DealUser.role／ActivityUser.role（社内の担当）

```python
class UserRole(models.TextChoices):
    PRIMARY = 'primary', '主担当'
    SUPPORT = 'support', '協力担当'
    APPROVER = 'approver', '上長承認者'
    OBSERVER = 'observer', '閲覧者'      # ★v0.8で追加
    OTHER = 'other', 'その他'
```

**`observer`（閲覧者）は★v0.8で追加。** §7.6でviewerロールに`view_all_deals`／`view_all_activities`を配らない方針を確定したため、viewerに特定の案件を見せる手段はDealUser／ActivityUserへの登録のみとなる。この「閲覧目的の登録」を`other`に混ぜると、業務上の関与者を表すDealUserの意味が濁るため、専用の値を用意する。

### 2.9.6 Company.status／CompanyDuplicateCandidate.rank／review_status

```python
class Status(models.TextChoices):          # Company
    ACTIVE = 'active', '有効'
    MERGED = 'merged', '統合済み'
    ARCHIVED = 'archived', 'アーカイブ'


class Rank(models.TextChoices):            # CompanyDuplicateCandidate
    EXACT_MATCH = 'exact_match', '完全一致'
    POSSIBLE_HIGH = 'possible_high', '可能性高'
    POSSIBLE_MID = 'possible_mid', '可能性中'
    POSSIBLE_LOW = 'possible_low', '可能性低'  # ★v1.1で追加（§6.5.2）


class ReviewStatus(models.TextChoices):    # CompanyDuplicateCandidate
    PENDING = 'pending', '未処理'
    MERGED = 'merged', '統合済み'
    DIFFERENT_COMPANY = 'different_company', '別会社と判断'
    INVALIDATED = 'invalidated', '無効化'
```

`Company.Status`はPersonの`status`と同型（§6.2）。`review_status`の値は既存の`DuplicateCandidate`（`duplicates/models.py`）と揃える。

---

# 第3章 Activity（活動記録）

## 3.1 フィールド定義

**`person`フィールドは持たない。** 誰と接触したかは常にActivityPerson（0件以上）で表現する（§3.4参照、社内活動・単独作業等でActivityPersonが0件のケースを許容するため）。

**★v0.7でmax_length・null/blank指定を追加**。§2.1のDealと同じ理由（未指定だとmakemigrationsクラッシュ、または全フィールド必須実装による事故）で明記する。

| 日本語名 | コーディング名 | 型 | 説明 |
|---|---|---|---|
| 紐づく案件 | deal | FK(Deal, SET_NULL, null=True) | 案件化前の接触も記録可 |
| 紐づくキャンペーン | campaign | FK(Campaign, SET_NULL, null=True) | §3.5参照 |
| （将来）project／schedule／task | | 各nullable FK | §3.2参照 |
| 種別 | activity_type | CharField(max_length=30, choices=ActivityType.choices) | 5値、§2.9.3参照。**必須**（活動の記録である以上、種別未定は認めない） |
| 方向 | direction | CharField(max_length=10, choices=Direction.choices, blank=True) | 発信／受信（電話・メールのみ意味を持つため、それ以外は空でよい）。§2.9.3参照 |
| 実施日時 | occurred_at | DateTimeField | **未来日禁止**（実施済みの記録のみ、`clean()`で判定）。**必須** |
| 実施者 | user | FK(User, SET_NULL, null=True, blank=True) | 実施した社員。将来のSchedule連携等を考慮し空も許容 |
| 作成者 | created_by | FK(User, SET_NULL, null=True) | **★v0.8で追加**。監査用。変更不可。後述の理由により権限判定にも使う |
| 場所 | place | CharField(max_length=255, blank=True) | 任意 |
| 内容メモ | memo | TextField(blank=True) | 任意（種別・日時が主情報のため、メモは後から埋めることも許容） |
| 論理削除フラグ | is_archived | BooleanField(default=False) | |
| 作成日時／更新日時 | created_at／updated_at | DateTimeField | |

**★v0.8で`created_by`を追加した理由（権限の穴を塞ぐため）**。Dealは`created_by`／`updated_by`を持つのに、v0.7時点のActivityは`created_at`／`updated_at`のみだった。かつ`Activity.user`（実施者）は`null=True, blank=True`である。この組み合わせにより、**実施者を空のまま登録すると、登録した本人が自分のレコードを編集もアーカイブもできなくなる**（§7.3の`can_edit_activity`／`can_archive_activity`が`activity.user_id == user.id`しか見ないため、`edit_all_activities`保持者しか触れなくなる）。Dealと同じ監査パターンに揃えた上で、編集・アーカイブ判定のOR条件に加える（§7.3参照）。

**★v0.9で追加：`occurred_at`の未来日禁止の実装**。Dealの`clean()`（§2.4）はコードで示されている一方、Activity側は表の記述だけだった。**`occurred_at`は`DateTimeField`なので`timezone.now()`との比較**であり、Dealの`closed_at`（`DateField`／`timezone.localdate()`比較）とは書き分けが必要になる。

```python
def clean(self):
    super().clean()
    if self.occurred_at and self.occurred_at > timezone.now():
        raise ValidationError({'occurred_at': '実施日時に未来の日時は指定できません。'})
```

数分程度のずれ（入力中に時刻が進む等）は問題にならないため、許容誤差は設けない。予定を登録したい場合は将来のScheduleモデル（§1.1のスコープ外）で扱う。

## 3.2 ターゲットFKのCheckConstraint

`deal`／`campaign`のうち**0個または1個のみ**を許可する（Attachmentの「exactly one」とは異なり、Activityは単独記録が正当にあり得るため）。

**★重要**：この制約は**現時点でモデルに実装されているフィールドのみ**を対象とする。`project`／`schedule`／`task`はまだモデルに存在しないため、これらをCheckConstraintに含めると`makemigrations`実行時に`FieldError: Cannot resolve keyword 'project' into field.`で即座にクラッシュする。Schedule／Task／Project実装時は、**その都度この制約自体をマイグレーションで拡張**する（後述の拡張イメージはあくまで将来の参考であり、今回のマイグレーションには含めない）。

```python
class Meta:
    constraints = [
        models.CheckConstraint(
            condition=(  # Django 6: check= は削除済み、condition= を使う
                models.Q(deal__isnull=True, campaign__isnull=True) |
                models.Q(deal__isnull=False, campaign__isnull=True) |
                models.Q(deal__isnull=True, campaign__isnull=False)
            ),
            name='activity_at_most_one_target',
        ),
    ]
```

**将来の拡張イメージ（参考、今回は実装しない）**：Schedule／Task／Project実装時、フィールド追加と同時にこのCheckConstraintも対象フィールドを増やして拡張する。

```python
# project/schedule/task 追加後のイメージ（将来のマイグレーションで対応）
condition=(
    models.Q(deal__isnull=True, campaign__isnull=True, project__isnull=True, schedule__isnull=True, task__isnull=True) |
    models.Q(deal__isnull=False, campaign__isnull=True, project__isnull=True, schedule__isnull=True, task__isnull=True) |
    models.Q(deal__isnull=True, campaign__isnull=False, project__isnull=True, schedule__isnull=True, task__isnull=True) |
    models.Q(deal__isnull=True, campaign__isnull=True, project__isnull=False, schedule__isnull=True, task__isnull=True) |
    models.Q(deal__isnull=True, campaign__isnull=True, project__isnull=True, schedule__isnull=False, task__isnull=True) |
    models.Q(deal__isnull=True, campaign__isnull=True, project__isnull=True, schedule__isnull=True, task__isnull=False)
)
```

## 3.3 「日報」の実現方法

専用テーブルは持たない。Activityを担当者×日付で絞り込んだ一覧として実現する。NextAction（次の予定行動）専用テーブルは検討したが撤去し、Schedule/Task/Project実装時に統合する。

**一覧の初期表示フィルタ**。`ActivityListView`は、クエリパラメータなしでアクセスされた場合、**`user=request.user`、`occurred_at__date=本日`をデフォルト適用**する。これを明記しないと、全期間・全ユーザーの活動を初期ロードする実装になり、Activity件数が増えるにつれ画面が重くなる。ユーザーが明示的にフィルタを変更した場合はその条件を優先する。

**★v0.7で追加：権限フィルタと表示フィルタの合成順序**。`ActivityListView`のクエリセットは、**まず`visible_activities_for(request.user)`（§7.3）で権限による絞り込みを適用し、その上に`user=request.user`／`occurred_at__date=本日`等の表示フィルタを重ねる**、という順序で合成する。順序を逆にする理由はないが、「権限フィルタが土台、表示フィルタは土台の上でのユーザーの見やすさのための絞り込み」という関係を明記しないと、コード君がどちらか一方だけを実装する可能性があるため、念のため両方を合成する方針を明記する。

```python
def get_queryset(self):
    qs = visible_activities_for(self.request.user)  # 権限フィルタが先
    if 'user' not in self.request.GET and 'date' not in self.request.GET:
        qs = qs.filter(user=self.request.user, occurred_at__date=timezone.localdate())
    return qs.filter(is_archived=False)
```

## 3.4 ActivityPerson／ActivityUser

誰と接触したか（ActivityPerson）・誰が同席したか（ActivityUser）を、**0件以上**の中間テーブルで表現する。

| フィールド | 内容 |
|---|---|
| `activity` | Activity FK（CASCADE） |
| `person`（ActivityPersonのみ） | FK(Person, **CASCADE**)。必須（★v0.4でon_delete明記、DealPersonと統一） |
| `user`（ActivityUserのみ） | FK(User, **CASCADE**)。必須（★v0.4でon_delete明記） |
| `role` | 同席者／決裁者／窓口 等（ActivityPerson）、同席者／協力担当 等（ActivityUser） |

**最低1人ルールは設けない。** 実施者（`Activity.user`）だけの単独活動（研修・資料作成等）が正当にあり得るため、ActivityPersonが0件の状態を許容する。

**Person起点の検索**：`Activity.objects.filter(activity_persons__person=person).distinct()`（`distinct()`必須）。一覧表示は`prefetch_related('activity_persons__person')`で対応する。

## 3.5 キャンペーン起点のフォロー活動・フォロー漏れ検出

### 3.5.1 業務フロー

1. Campaignを配信（`DeliveryHistory`に記録）
2. 受信者がリンクをクリック（`TrackingLink`／`ClickLog`に記録済み）
3. 営業担当がクリックした人を電話等でフォローし、`Activity`（`campaign`＋ActivityPersonで相手を記録）を作成
4. 反応が良ければ`Deal`を新規作成、以降のActivityは`deal`に紐付く（`campaign`は使わなくなる）

キャンペーン配信のたびに受信者ごとのActivityを自動生成することはしない（`DeliveryHistory`が正本のまま）。`Activity.campaign`は営業担当が手動で記録する任意の紐付け先。

### 3.5.2 フォロー漏れ検出

**画面構成（★v0.6で確定）**：フォロー漏れ検出は`activities`側の**独立画面**とする。Campaign詳細画面に埋め込むと、その画面（`mailings`側）が`activities`の関数を呼ぶことになり、§1.2で構造的に解消したはずの依存関係に例外を持ち込むことになるため、これを避ける。Campaign詳細画面からは、この独立画面へのURLリンクを1つ置くだけにする（コードレベルの依存はゼロのまま、動線としては十分機能する）。

画面内の各行には「フォロー記録」リンクを配置する。クリックすると、**Activity新規作成画面**（モーダルではなく別画面。Activityは種別・実施日時・内容メモ等、入力項目が多い本格的なフォームのため、DealPerson/DealUser追加のような軽量サブフォームとは動線を分ける）に遷移し、`person`（クリックした人）・`campaign`（該当キャンペーン）を初期値として引き継ぐ。営業担当は種別・実施日時・内容メモだけ入力すればよい。保存後は元の一覧画面に戻る（BackNavigator活用）。一覧には「未フォローのみ表示」の絞り込みフィルタを用意する。

配置は`activities/services/`とする（§1.2で確定した「`mailings`が`activities`に依存する向きを避ける」方針と一貫させるため。`mailings`側からは呼ばれる側ではなく、`activities`側が`mailings`のCampaign/TrackingLink/ClickLogを参照する形にする）。

**クエリの技術的な注意点**：`TrackingLink`／`ClickLog`は`related_name`が指定済み（`tracking_links`／`click_logs`）のため、Djangoのデフォルト逆参照名（`trackinglink`／`clicklog`）では`FieldError`になる。また`values_list(...).exclude(id__in=...)`は、対象がLEFT JOINで`None`を含む場合`NOT IN (NULL, ...)`となり常に0件を返す（ActivityPersonが0件のActivityが1件でもあれば発生する、§3.4で許容したActivityPerson 0件と衝突する罠）。さらに`ClickLog`は生アクセスを全件記録する設計のため、ボット・プリフェッチのクリックまで拾わないよう`is_valid_click=True`で絞る。

**Personマージへの対応（★v0.7で再修正）**。クリックした時点のPersonと、営業担当がActivity記録時に指定するPersonは、途中でPersonマージが起きていると一致しないことがある（`ActivityPerson.person`はマージ時にFKを付け替えない方針、§8.1）。放置すると「実際はフォロー済みなのに、いつまでも未フォロー扱いになり続ける」誤検出が起きる。

v0.6時点の実装は「突き合わせ判定」のみ生存Person基準にしていたが、**返す一覧自体は元のPerson（マージで死んだ側を含む）のまま**だった。この状態で§3.5.2の画面仕様（各行から生成される「フォロー記録」リンク→Activity新規作成画面へ、そのPersonを初期値として引き継ぐ）を実行すると、**営業担当が死んだPerson（`status=merged`）に対してActivityを作ってしまう**。ActivityPersonはマージ時にFKを付け替えない方針のため、この記録は生存Person側の画面には二度と出てこない。「フォロー済みなのに出続ける」不具合が「間違った相手に記録してしまう」というより悪い不具合に置き換わっていたことになる。

**修正**：クリック側も生存Person基準に正規化する。また、Python内包表記でリストを返すと`filter`/`order_by`/ページネーションが効かなくなり、§3.5.2の画面要件（「未フォローのみ表示」フィルタ）を満たせないため、**QuerySetとして返す**よう修正する。`select_related('merged_into')`を付けることで、生存解決ループの追加クエリもほぼ発生しない。

```python
def get_unfollowed_clickers(campaign):
    """クリックしたが、まだこのキャンペーンに紐づくActivityがない Person 一覧
    （クリック側・フォロー側とも生存Person基準に正規化し、QuerySetとして返す）"""
    clicked_persons = Person.objects.filter(
        tracking_links__campaign=campaign,
        tracking_links__click_logs__is_valid_click=True,  # related_name反映・ボット除外
    ).select_related('merged_into').distinct()
    followed_persons = Person.objects.filter(
        activity_persons__activity__campaign=campaign,
        activity_persons__activity__is_archived=False,  # ★v0.8で追加（後述）
    ).select_related('merged_into').distinct()

    followed_surviving_ids = {p.get_surviving_person().id for p in followed_persons}

    # クリック側も生存Personに正規化し、未フォローかつユニークな生存PersonのID集合を作る
    surviving_clicker_ids = {
        p.get_surviving_person().id for p in clicked_persons
    } - followed_surviving_ids

    # QuerySetとして返す（filter/order_by/ページネーションが効くようにするため）
    # ★v0.8：一覧で氏名・会社名を表示する時点でN+1になるため select_related を付与
    return Person.objects.filter(
        id__in=surviving_clicker_ids
    ).select_related('primary_contact')
```

**★v0.8で追加：論理削除済みActivityの除外**。`followed_persons`が`Activity.is_archived`を見ていなかった。この条件が抜けていると、**営業が誤って活動を登録し、直後にアーカイブ（論理削除）した場合でもフォロー済みとみなされ**、実際にはフォローされていない相手が未フォローリストから永久に消える。アーカイブは「その活動はなかったことにする」操作なので、フォロー実績としてカウントしてはならない。

**★v0.9で確定：アーカイブ済みPerson（`status='archived'`）は除外しない**。レビューでは返却QuerySetに`filter(status=Person.Status.ACTIVE)`を加える案が出たが、**採らない**。

`get_surviving_person()`が解決するのは「マージによって別Personに統合されたかどうか」であり、`status='archived'`（退職等でアーカイブされた人物）はそれとは別の概念である。ここで機械的に除外すると、**「退職した担当者がメールをクリックしていた」という、後任へのフォローにつながる情報がリストごと消える**。フォロー漏れ検出の目的に反する。

代わりに表示側で扱う。

- 一覧に**Personの状態（アーカイブ済み等）を列として表示する**
- **「アーカイブ済みを除く」を任意のフィルタとして提供する**（既定はOFF＝全件表示）

これにより、退職者を見落とさずに済み、かつ現役だけを見たいときにも対応できる。

**★v0.8で追加：多段マージ時のクエリ（既知の許容事項）**。`select_related('merged_into')`がカバーするのは1ホップ分のみであり、A→B→Cのような多段マージが存在する場合、`get_surviving_person()`の2ホップ目以降で追加クエリが発生する。実運用でPersonマージが3段以上連鎖するケースは稀であり、対策すると`get_surviving_person()`（既存の共有メソッド）の改修が必要になるため、**既知の許容事項として受容する**。

---

# 第4章 Attachment（添付ファイル）

## 4.1 フィールド定義

| フィールド | 型 | 説明 |
|---|---|---|
| `deal` | FK(Deal, CASCADE, null=True) | |
| `activity` | FK(Activity, CASCADE, null=True) | |
| `file` | FileField(storage=**get_protected_storage**, upload_to=**attachment_upload_to**) | §4.3参照（配信経路）。標準ストレージのままだと`/app/media/`配下に保存され、nginxからURL直叩きで誰でもダウンロードできてしまう。**★v0.8：インスタンスではなくcallable（関数）を渡す**。**★v0.9：`upload_to`もUUID化する関数にする**（いずれも理由は後述） |
| `original_filename` | CharField(max_length=255) | アップロード時の元ファイル名を保持（保存パスは実装都合でUUID化されるため、ダウンロード時に元の名前で返すために必要） |
| `uploaded_by` | FK(User, SET_NULL, null=True) | |
| `memo` | CharField(max_length=255, blank=True) | |
| `created_at` | DateTimeField | |

**★v0.8で修正：storageはインスタンスではなくcallableを渡す（最優先の修正事項）**。

`FileSystemStorage`は`@deconstructible`であるため、`FileField(storage=protected_storage)`と**インスタンスを直接渡すと、マイグレーションファイルに`FileSystemStorage(location='<解決済みの絶対パス>')`としてシリアライズされる**。`PROTECTED_MEDIA_ROOT = BASE_DIR / 'protected_media'`は環境ごとに異なるため、最初に`makemigrations`を実行した環境のパスがそのまま他環境へ持ち込まれる。

FG2の開発環境はこの問題を確実に踏む。自宅PC（`D:\user\projects\`配下のworktree複数）・実家PC（`C:\Users\iwata\projects\freegroup2`）・Docker本番（`/app/`）でパスが3系統以上あり、同期した瞬間に別環境で「storageが変わった」と判定されて差分マイグレーションが生成され続けるか、存在しないパスを見に行くかのどちらかになる。

**対策**：ストレージを返す関数を定義し、`storage`にはその関数参照を渡す（Django 3.1以降でサポートされている、まさにこの問題のための機能）。マイグレーションには関数への参照だけが記録され、実際のパスは実行時に`settings`から解決される。

```python
# settings.py
PROTECTED_MEDIA_ROOT = BASE_DIR / 'protected_media'
```

```python
# attachments/models.py
import uuid
from pathlib import Path

from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.utils import timezone


def get_protected_storage():
    """storageにインスタンスではなく関数を渡すことで、マイグレーションに
    環境依存の絶対パスが焼き込まれるのを防ぐ（実行時にsettingsから解決される）。"""
    return FileSystemStorage(location=settings.PROTECTED_MEDIA_ROOT)


def attachment_upload_to(instance, filename):
    """保存パスをUUID化する。元のファイル名は original_filename が保持する。

    ディスク上に元のファイル名・日本語・推測可能な名前を一切残さないため、
    拡張子のみ引き継いで UUID にリネームする。
    """
    ext = Path(filename).suffix.lower()
    return f'attachments/{timezone.now():%Y/%m}/{uuid.uuid4()}{ext}'


class Attachment(models.Model):
    file = models.FileField(
        storage=get_protected_storage,   # ← 呼び出さない。関数そのものを渡す
        upload_to=attachment_upload_to,  # ★v0.9：UUID化（後述）
    )
```

**★v0.10で修正：関数定義の順序とimportの誤り**。v0.9のスニペットは`class Attachment`を`attachment_upload_to`の定義より**前**に置いており、そのまま写すと`NameError`になる。また`attachment_upload_to`は`timezone.now()`を使うのに、示していたimportは`uuid`と`pathlib.Path`だけで`django.utils.timezone`が抜けていた。コード君はスニペットをそのまま写すため、**関数定義を先に置き、importを実際に使う分だけ揃えて**1つのブロックにまとめた。

**★v0.9で修正：`upload_to`をUUID化する（説明文と実装の食い違いを解消）**。

v0.8の`original_filename`の説明は「保存パスは実装都合でUUID化されるため、ダウンロード時に元の名前で返すために必要」と書いていたが、コード側は`upload_to='attachments/%Y/%m/'`だけで、**元のファイル名がそのままディスク上のパスに載る**状態だった。これでは`original_filename`を持つ理由そのものが消える上、日本語ファイル名がパスに載り、同名ファイルのアップロードでDjangoが自動的にサフィックスを付ける（`見積書_HDzk3s.pdf`）といった扱いにくさも生じる。

拡張子だけは引き継ぐ（ダウンロード時のContent-Type判定と、サーバ上での中身の把握のため）。元のファイル名は`original_filename`に保存し、ダウンロード時に`Content-Disposition`で復元する（§4.3）。

**★v0.9で追加：ファイルサイズ・拡張子の制限**。

- **サイズ上限は15MBとする。** nginxの`client_max_body_size`が20Mであり、これを超えるとDjangoのフォームバリデーションに到達せず**nginxが素の413を返す**（利用者には何が起きたか分からない）。Django側の上限をそれより低く設定することで、必ず分かりやすいフォームエラーとして返せる。フォームの`clean_file()`で検証する
- **実行可能ファイルは拒否する**（`.exe`／`.bat`／`.cmd`／`.com`／`.scr`／`.js`／`.vbs`／`.ps1`／`.jar`／`.msi`／`.dll`）。保護ストレージ経由でしか配信しないため実行される経路はないが、社内で受け渡す添付として保持する必要がない
- 許可リスト方式ではなく**拒否リスト方式**とする。業務で扱うファイル種別は事前に列挙しきれず（CAD図面・独自形式の見積データ等）、許可リストにすると運用のたびに仕様変更が必要になるため

**`PROTECTED_MEDIA_ROOT`の環境別設定**：Docker本番環境では、コンテナ再作成でファイルが消えないよう、`media`と同様にbind mountまたはvolumeでホスト側と同期させる。**ただし`media`とは別のディレクトリとし、nginxの`location /media/`のalias配下に含めないこと**（含めるとURL直叩きで配信可能になり、本節の対策全体が無意味になる）。

## 4.2 CheckConstraint

```python
class Meta:
    constraints = [
        models.CheckConstraint(
            condition=(
                models.Q(deal__isnull=False, activity__isnull=True) |
                models.Q(deal__isnull=True, activity__isnull=False)
            ),
            name='attachment_exactly_one_target',
        ),
    ]
```

GenericForeignKeyは不採用（DBレベルのCASCADE確実性を優先。理由は§4章末の設計判断メモ参照）。対象ごとに中間テーブルを分ける方式も、対象が2種類に留まる現状では過剰設計として見送った。

## 4.3 配信経路（実機確認済み）

本番環境のnginx（`nginx/nginx.conf`）は`/media/`配下を無権限で直接配信している。

```nginx
location /media/ {
    alias /app/media/;
}
```

**対応**：Attachment用に、権限チェック付きの汎用Django Download Viewを新設する。実ファイルは`/media/`配下ではなく、nginxが配信しない保護ディレクトリに保存する。

**★v0.8で追加：`PermissionRequiredMixin`を併用する**。v0.7の実装イメージは`LoginRequiredMixin`＋オブジェクト単位の`has_view_permission()`のみで、`attachments.view_attachment`（標準権限）を要求する箇所がどこにもなかった。このままでは§7.6でこの権限を配っても死蔵になる。§7.1で掲げた「View層で機能レベルの粗いゲート、Service層でレコード単位判定」という二重防衛の原則にも反するため、`permission_required`を明示する。

**★v0.9で修正：権限はクラス属性に固定せず、レジストリから引く**。v0.8は`permission_required`をクラス属性で`attachments.view_attachment`に固定したが、本Viewは`MODEL_REGISTRY`を持つ**汎用**Viewとして設計されており、次フェーズで`cards`（名刺画像）を登録する予定である（本節末尾）。固定したままだと、**名刺画像を見るのに`attachments`アプリの権限が必要**という状態になる。今はAttachmentしか登録しないので実害はないが、次フェーズで確実に踏む。`MODEL_REGISTRY`を`{model_name: (model, permission)}`の形にし、`get_permission_required()`で引く。

```python
# attachments/views.py
MODEL_REGISTRY = {
    'attachment': (Attachment, 'attachments.view_attachment'),
    # 次フェーズで 'card': (Card, 'cards.view_card') を追加する
}


def get_registry_entry(model_name):
    """★v0.10で追加：MODEL_REGISTRY の参照を1箇所に集約する。

    get_permission_required() と get() の両方から呼ぶ。次フェーズで card を
    追加する際、参照箇所が2つに分かれたままだと片方だけ直す事故が起きるため。
    """
    entry = MODEL_REGISTRY.get(model_name)
    if entry is None:
        raise Http404
    return entry


class ProtectedFileDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):

    def get_permission_required(self):
        """★v0.9：モデルごとに要求する権限をレジストリから引く。"""
        _model, permission = get_registry_entry(self.kwargs['model_name'])
        return (permission,)

    def get(self, request, model_name, pk):
        # ★v0.4で修正：MODEL_REGISTRY[model_name] だと未登録キーでKeyError→500になる。
        # get_registry_entry() 内で .get() を使い、未登録なら404にする。
        model, _permission = get_registry_entry(model_name)
        obj = get_object_or_404(model, pk=pk)
        if not obj.has_view_permission(request.user):
            raise PermissionDenied
        # ファイル名を明示しないと、保存パスのUUID等がそのままブラウザの
        # 保存ダイアログに表示されたり、日本語ファイル名が文字化けする。
        # original_filename（アップロード時に保持した元の名前）を明示する。
        return FileResponse(
            obj.file.open('rb'),
            as_attachment=True,
            filename=obj.original_filename,
        )
```

**`MODEL_REGISTRY`・本Viewの配置先**：`attachments`アプリ（§1.2参照、v0.5でAttachmentは独立アプリに変更）に置く。**★v0.5で明記**：`MODEL_REGISTRY`には`file`フィールドと`original_filename`・`has_view_permission()`を持つモデル（現時点ではAttachmentのみ）だけを登録する。Deal／Activity等、ファイルを持たないモデルを誤って登録すると`obj.file`が存在せず500エラーになる。将来ファイル添付を持つモデルが増えた場合のみ、このレジストリに追加する。

**Attachment自身は`owner`を持たないため、権限判定は親オブジェクトに委譲する**（§7.4参照）。**★v0.4で明記**：ViewはCampaignと同じパターンで、対象モデルの`has_view_permission(user)`という**薄いモデルメソッド**を呼ぶ想定とする。Deal・Activity・Attachmentそれぞれに、本章で定義した判定関数（`can_view_deal`／`can_view_activity`／`can_view_attachment`）を呼ぶだけの薄いラッパーメソッドを用意する。

```python
# deals/models.py
class Deal(models.Model):
    def has_view_permission(self, user):
        return can_view_deal(user, self)

# activities/models.py
class Activity(models.Model):
    def has_view_permission(self, user):
        return can_view_activity(user, self)

# attachments/models.py（★v0.5でdealsから独立アプリに変更）
class Attachment(models.Model):
    def has_view_permission(self, user):
        return can_view_attachment(user, self)
```

名刺画像等、既存ファイルへの適用と移行は次フェーズ（Person閲覧制限とセットで対応）。

## 4.4 アップロード・削除の動線と削除方式（★v0.8で新設）

v0.7は§7.6で`add_attachment`／`delete_attachment`の配布先だけを決めており、**それを要求する画面が仕様書のどこにも定義されていなかった**（§4にあるのはダウンロードViewのみ）。ここで動線・削除方式・権限境界を確定する。

### 4.4.1 アップロード

Deal詳細画面・Activity詳細画面の**サブフォーム**として実装する（DealPerson／DealUserの追加と同じ動線、§5.2）。専用画面は設けない。添付は常に親オブジェクトに従属するものであり、単独で管理する意味がないため。

- `permission_required = 'attachments.add_attachment'`
- 加えて、親オブジェクトに対する`can_edit_deal()`／`can_edit_activity()`（§7.1、§7.3）が真であることを要求する。閲覧しかできない相手が添付を追加できてはならないため
- `uploaded_by`には`request.user`を設定する（変更不可）
- `original_filename`にアップロード時の元ファイル名を保持する（§4.1）

### 4.4.2 削除方式：物理削除とする

DealやActivityは`is_archived`による論理削除だが、**Attachmentは物理削除**（DBレコード削除＋ストレージ上の実ファイル削除）とする。添付の削除は主に「誤ったファイルを上げてしまった」ケースであり、論理削除にすると保護ストレージに実ファイルが残り続けて容量を圧迫する上、「消したはずのファイルが消えていない」という利用者の期待との食い違いを生むため。

**★重要：Djangoは`FileField`を持つレコードを削除しても、ストレージ上の実ファイルを削除しない。** `Attachment`の削除時に明示的にファイル実体を削除する処理を書かないと、保護ストレージにゴミファイルが残り続ける。

**★v0.19で修正：実ファイルの削除は`post_delete`シグナルに一本化する（重要）**。v0.18までは`delete_attachment()`関数内で`transaction.on_commit`によりファイル削除を行っていたが、これでは**この関数を経由しない削除経路でファイルが孤児として残る**。具体的には、Django admin画面からAttachmentレコードを直接削除した場合と、親のDeal／ActivityがCASCADE削除された場合（本仕様書のスコープでは想定していないが、既存の`OriginalImage`等が同様の構造を持つ）である。

既存コード（`OriginalImage`等）は「ファイル等のアーティファクトはDBレコードに従属し、`post_delete`シグナルで整合性を保つ」という規約を持っている。これに倣い、ファイル削除のロジックを`delete_attachment()`から`post_delete`シグナルへ移す。ただし単純にシグナル内で即座に削除すると、v0.9で確立した「トランザクションのロールバック時にファイルだけ消えるのを防ぐ」という原則が崩れるため、シグナル内でも`transaction.on_commit`を使う（詳細は下記コードのコメント参照）。Django adminの削除処理も既定で`transaction.atomic`にラップされているため、`on_commit`はadmin経由の削除でも正しく機能する。

```python
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver


@receiver(post_delete, sender=Attachment)
def delete_attachment_file(sender, instance, **kwargs):
    """★v0.19：実ファイルの削除はここに一本化する。delete_attachment()経由の
    削除だけでなく、admin画面からの直接削除や、将来CASCADE削除が発生する
    経路でも、Attachmentレコードが消えた時点で必ずこのシグナルが呼ばれる
    ため、実ファイルが孤児として残ることはない。

    ★重要：transaction.on_commit() と組み合わせる。post_delete はDB上の
    削除操作の直後（トランザクションのコミット前）に同期的に発火するため、
    ここで即座に storage.delete() を呼ぶと、その後トランザクションが
    ロールバックされた場合に「DBレコードは復活したのに実ファイルは
    消えている」という不整合が生じる。on_commit でコミット確定後まで
    遅延させることで、v0.9で確立した「ロールバック時にファイルだけ
    消えるのを防ぐ」という原則をシグナル経由でも維持する。
    """
    if instance.file:
        file_name = instance.file.name
        storage = instance.file.storage
        transaction.on_commit(lambda: storage.delete(file_name))


def delete_attachment(attachment, user):
    """Attachmentの物理削除。実ファイルの削除はpost_deleteシグナル
    （delete_attachment_file）に委譲するため、ここでは行わない。"""
    # ★v0.9：物理削除でGFKの参照先が消えるため、親のコンテキストをdataに残す
    if attachment.deal_id:
        target_type, target_id = 'deal', str(attachment.deal_id)
        target_name = attachment.deal.name
    else:
        target_type, target_id = 'activity', str(attachment.activity_id)
        target_name = str(attachment.activity)
    with transaction.atomic():
        ActionLog.record(
            user=user, action='attachment_deleted', content_object=attachment,
            object_repr=attachment.original_filename,   # ★v0.9：削除後は解決不能になるため
            data={
                'original_filename': attachment.original_filename,
                'target_type': target_type,
                'target_id': target_id,
                'target_name': target_name,
            },
        )
        attachment.delete()  # ★v0.19：ここで post_delete シグナルが発火し実ファイルも消える
```

**★v0.9で追加：ログに親のコンテキストを含める理由**。Attachmentは物理削除されるため、レコードが消えた瞬間にActionLogの`content_object`（GenericForeignKey）の参照先が失われる。v0.8の`data`には`original_filename`しか入っていなかったため、後から監査ログを見ても「誰が何というファイルを消したか」は分かるが、**それがどの案件・活動に付いていた添付なのかが完全に特定不能**になる。`object_repr`も同じ理由で明示する（既存の`ActionLog.record()`の使い方に沿う）。

### 4.4.3 削除の権限境界（★重要）

閲覧・編集は親オブジェクトへ委譲する（§7.4）が、**削除まで同席者に委譲してはならない**。商談や会議に同席しただけの社員が、他人のアップロードした見積書・契約書PDFを誤って物理削除できてしまうためである。削除には復元手段がない。

削除できるのは以下のいずれかに該当する場合のみとする（詳細は§7.4）。

- アップロード者本人（`attachment.uploaded_by == user`）
- 親オブジェクトの所有者本人（`Deal.owner`／`Activity.user`）
- **親がActivityの場合は`Activity.created_by`本人も含む**（★v0.9で追加。§3.1・§7.3で塞いだ「実施者が空だと登録者本人が触れない」穴が、添付の削除だけ残っていたため）
- `deals.edit_all_deals`／`activities.edit_all_activities`の保持者

**DealUser／ActivityUser（同席者・閲覧者）は削除不可。** これはDeal／Activityのアーカイブ権限（§7.1、§7.3）で採用した「関わっている人」と「削除できる人」を区別する方針と一貫している。

- `permission_required = 'attachments.delete_attachment'`（View層の粗いゲート）
- `can_delete_attachment()`（Service層のレコード単位判定、§7.4）

### 4.4.4 メモの編集（★v0.9で新設）

§7.6は`change_attachment`を`deal_editor`／`deal_admin`／`activity_editor`／`activity_admin`に配っているが、v0.8時点では**それを要求する画面が存在しなかった**。これは§7.1で自ら立てた「要求するViewがない権限は配らない（死蔵権限を新規に作らない）」という基準に違反している。

動線を設ける側で揃える。**添付の削除以外に、誤ったメモを直したいだけのケースは実際にあり、そのために削除して上げ直させるのは筋が悪い**ため。

- Deal詳細画面・Activity詳細画面の添付一覧で、**メモをインライン編集**できるようにする（専用画面は設けない。アップロードと同じサブフォーム方式）
- 編集できるのは`memo`のみ。`file`・`original_filename`・`uploaded_by`は変更不可（差し替えたい場合は削除して上げ直す。同一レコードでファイルだけ差し替えると、ActionLogの履歴と実ファイルの対応が崩れるため）
- `permission_required = 'attachments.change_attachment'`
- Service層は`can_edit_attachment()`（§7.4、親への委譲）で判定する。**削除と違い同席者も可**とする。メモの修正は失うものがなく、共同で案件を追う相手に閉じる理由がないため

---

# 第5章 DealPerson／DealUser／ActivityPerson／ActivityUser

## 5.1 フィールド定義

**DealPerson**：`deal`（FK, CASCADE）／`person`（FK, CASCADE）／`role`（CharField(max_length=50, choices=PersonRole.choices)、**§2.9.4参照**）／`is_primary`（BooleanField(default=False)、**★v1.7で追加。相手方の主担当を示す。詳細は§2.6.1参照**）／`memo`（CharField(max_length=255, blank=True)）／`created_at`

**DealUser**：`deal`（FK, CASCADE）／`user`（FK, CASCADE）／`role`（CharField(max_length=50, choices=UserRole.choices)、**§2.9.5参照**）／`memo`（CharField(max_length=255, blank=True)）／`created_at`

**ActivityPerson／ActivityUser**：§3.4参照。`role`の選択肢はDealPerson／DealUserと共通（§2.9.4／§2.9.5）

**★v0.8：`UserRole`に`observer`（閲覧者）を追加**。§7.6でviewerロールに`view_all_deals`／`view_all_activities`を配らない方針を確定したため、viewerに特定の案件・活動を見せる唯一の手段がDealUser／ActivityUserへの登録となる。この「閲覧目的の登録」を`other`に混ぜると業務データとしてのDealUserの意味が濁るため、専用の値を設ける。

**★v0.3で追加：多重登録防止のUniqueConstraint**

連打・画面リロード等による同一人物・同一ユーザーの重複登録を防ぐため、4モデルすべてに複合ユニーク制約を設ける。

```python
# DealPerson
constraints = [
    models.UniqueConstraint(fields=['deal', 'person'], name='unique_deal_person'),
]
# DealUser
constraints = [
    models.UniqueConstraint(fields=['deal', 'user'], name='unique_deal_user'),
]
# ActivityPerson
constraints = [
    models.UniqueConstraint(fields=['activity', 'person'], name='unique_activity_person'),
]
# ActivityUser
constraints = [
    models.UniqueConstraint(fields=['activity', 'user'], name='unique_activity_user'),
]
```

同一の組み合わせで役割（`role`）だけ変えたい場合は、新規追加ではなく既存レコードの`role`を更新する運用とする。

**★v1.7の注記：`DealPerson.is_primary`の一意性制約は§2.6.1に集約**。「同一Dealで`is_primary=True`は最大1件」という部分ユニーク制約（`unique_deal_primary_person`）は、相手方主担当の設計をまとめて説明する§2.6.1に記載する（本節の一般的な多重登録防止制約とは目的が異なるため、混同を避けて分離した）。

**★v0.14で追加、★v1.7で範囲を修正：本体の単一FK（`owner`）との二重登録を防ぐ`clean()`（重要）**。§5.2は「`Deal.owner`が『正』であり、`DealUser`には同一人物を二重登録しない（バリデーションで縛る）」と宣言している。§5.1の`UniqueConstraint`が防ぐのは「同じ人を2回登録すること」であり、「`owner`本人を`DealUser`にも登録すること」は別物で、これでは防げない。

この穴は`reassign_deal_owner()`（§2.6）が実際に踏む。ownerを付け替える相手が既に`support`ロールで`DealUser`に登録されている場合、何もしないと**同一人物が`Deal.owner`と`DealUser`の両方に存在する状態**になり、参加者一覧に同じ人が「owner」と「DealUser（support）」の2行で並ぶ。

`clean()`で明示的に禁止する。

```python
# DealUser
def clean(self):
    super().clean()
    if self.user_id == self.deal.owner_id:
        raise ValidationError(
            '現在のownerと同じUserをDealUserに登録することはできません。'
        )
```

**★v1.7の注記：`DealPerson`に本節と同型の`clean()`は不要になった**。v1.6までは`Deal.primary_person`（本体の単一FK）と`DealPerson`（別テーブル）が別の場所にあったため、同一人物が両方に登録される事故を`clean()`で防ぐ必要があった。`primary_person`廃止（§2.6.1）により、相手方の主担当は`DealPerson.is_primary`という**同じテーブルの同じ行の属性**になったため、そもそも「二重登録」という状態が起こり得ない。`DealPerson`側は本節冒頭の`unique_deal_person`制約と、§2.6.1の`unique_deal_primary_person`制約のみで整合性を保つ。

**★v0.14で追加：Activity側にも同じ制約を設ける**。`Activity.user`（実施者）と`ActivityUser`（同席者）の間にも、Dealの`owner`／`DealUser`と同じ「1人だけが特別枠を持つ」構造があるが、これまでDeal側と非対称に、宣言自体が存在しなかった。実施者を同席者としても登録する意味はなく、Dealと扱いを変える理由もないため、同じ`clean()`を設ける。

```python
# ActivityPerson
def clean(self):
    super().clean()
    # Activityはperson単体FKを持たないため、Deal側のような対応する
    # 単一FKとの二重登録チェックは存在しない（Activityは複数の相手と
    # 同時に接触することが前提のため、Personの特別枠自体がない）

# ActivityUser
def clean(self):
    super().clean()
    if self.user_id == self.activity.user_id:
        raise ValidationError(
            '実施者と同じUserをActivityUserに登録することはできません。'
        )
```

`DealUser`は「社内担当者」という単一の特別枠（`Deal.owner`）を持つのに対し、`Activity`は`person`単体のFKを持たない（§3.1、複数の相手と同時に接触することが前提）ため、`ActivityPerson`側には対応する二重登録チェックが存在しない。この非対称は意図的である（`DealPerson`は★v1.7で`Deal.primary_person`が廃止されたため、この対比からは外れた。上記の通り理由は異なる）。

## 5.2 設計原則

- `Deal.owner`（本体の単一FK）が「正」。`DealUser`には同一人物を二重登録しない（バリデーションで縛る、§5.1）
- **★v1.7で変更**：相手方の主担当は`Deal.primary_person`のような本体の単一FKではなく、`DealPerson.is_primary`が「正」（詳細な制約・付け替え方法は§2.6.1）
- PersonとUserを1テーブルに混在させない（ライフサイクル・削除ルールが別物のため）
- 独立したCRUD画面は持たない。Deal/Activity作成・詳細画面のサブフォームとして実装する

**★v1.6で追加、★v1.7で撤回：`DealPerson`に「主担当フラグ」を持たせてはならない、という制約は誤りだった**。v1.6は、Step 1実装レビューで`DealPerson`に独自の`is_primary`が追加された事故を受け、「`DealPerson`／`DealUser`に主担当フラグを追加してはならない」と明記した。しかしこれは対症療法であり、根本原因（`Deal.primary_person`という本体FKと`DealPerson`という別テーブルの2箇所に「相手方の主担当」を表現しうる余地があったこと）を解消していなかった。

たんたんとの壁打ちにより、**`Deal.primary_person`自体を廃止し、`DealPerson.is_primary`に一本化する**方針に転換した（§2.6.1）。これにより「2箇所に表現しうる」という構造上の問題そのものが解消されるため、v1.6の禁止事項は撤回する。ただしv1.6の事故が示した教訓（**一意性制約のない主担当フラグは危険**）は活かし、§2.6.1で部分ユニーク制約（`unique_deal_primary_person`）と、付け替え専用関数（`reassign_deal_primary_person()`）を必須とした。`DealUser`側の`owner`については引き続き本体の単一FKのままとする（§7章参照、Contactの`managed_by`と同型のACL的役割を持つため）。

**★v0.11で追加：追加・削除の権限ゲート**。§5.2はサブフォームというUIの動線を決めていたが、**誰がそのサブフォームからDealPerson／DealUser／ActivityPerson／ActivityUserを追加・削除できるかが未定義**だった。コード君任せにすると、閲覧権限（`can_view_deal`）さえあれば誰でも自分をDealUserに追加できるような緩いエンドポイントを作りかねない。

- **DealPerson／DealUserの追加・削除は`can_edit_deal(user, deal)`を要求する**（§7.1）
- **ActivityPerson／ActivityUserの追加・削除は`can_edit_activity(user, activity)`を要求する**（§7.3）

閲覧者（viewerロールでDealUserに`observer`として登録されているだけの人）は、この判定に落ちるため関与者の追加・削除はできない。これは§7.0で確定した「関与しているが編集させたくない相手」という`observer`の位置づけと一貫する。

## 5.3 アクセス制御における位置づけ（★重要）

DealUserは業務データであると同時に、Deal/Activityの閲覧・編集可否の判定材料としても使う（§7章参照）。将来AccessListを導入した後も、DealUserは消えず、**「ACL上は見えない設定でも、関与者として登録されていれば個別に見える」というOR条件の恒久的な仕組み**として残り続ける（旧FreeGroupのReport/Calendarが採用していた「カテゴリ単位ACL＋個別permission」の2層構造を踏襲する方針）。

---

# 第6章 Company（会社エンティティ）

## 6.1 背景

`Contact.organization`は文字列のみで、表記ゆれによる名寄せができない。Deal.companyでの会社軸集計を可能にするため新設する。

## 6.2 フィールド定義

**Company**

| フィールド | 型 | 説明 |
|---|---|---|
| `organization` | CharField(max_length=255, **db_index=True**) | 名寄せ基準の会社名（`Contact.organization`＝OCRの`org_name_full`相当、法人格込みの完全な表記）。**★v0.7で追加：`db_index=True`**。`link_contact_to_company`のプレフィルタ（§6.5.4）で頻繁にOR検索されるため、未指定だとCompany件数増加時にフルスキャンで遅延する |
| `domain` | CharField(max_length=255, blank=True, **db_index=True**) | 会社間の重複判定に使う（§6.5.1）。新規Company作成時、最初に紐づいたContactの`org_domain_name`をコピーする（汎用ドメインの場合は空のまま）。以後は「空→値」の一方向の穴埋めのみ許可し、既に値がある場合の上書きは引き続き禁止する（§6.5.6参照）。**★v0.7で追加：`db_index=True`**（理由は`organization`と同様） |
| `status` | CharField(max_length=20, choices=Status.choices, **default=Status.ACTIVE**) | active／merged／archived（Personと同型）。**★v0.18で追加：`default`が未指定だった**。`Deal.stage`は§2.2で`default=Deal.Stage.INITIAL_MEETING`を明示しているのに、Companyは対応するデフォルトがなく、admin等での新規作成時に必須項目未入力になる懸念があったため追加する |
| `merged_into` | FK(self, SET_NULL, null=True) | **★v1.8で追加：`on_delete`の指定漏れを修正**。既存の`Person.merged_into`（`persons/models.py`）が`on_delete=models.SET_NULL`であるのに合わせる。`related_name`はPersonの前例（`merged_from_set`）とは意図的に異なる`merged_companies`とする（§1.4.1参照） |
| `phone` | CharField(max_length=50, blank=True) | **★v1.1で追加**。重複検出のスコアリング項目（§6.5.1）。対応する`Contact`側は`org_phone`。**★v1.2で修正**：今回のスコープでは、Company新規作成時に最初のContactの値をコピーするのみ（§6.5.4手順5参照）。`domain`のような継続的な「空→値」の穴埋めやマージ時の引き継ぎは次フェーズ（§6.5.6参照） |
| `address` | CharField(max_length=500, blank=True) | **★v1.1で追加**。重複検出のスコアリング項目（§6.5.1）。対応する`Contact`側は`address`（Contactは個人・会社の住所を区別しないフィールドのため、名刺記載の住所＝勤務先住所として扱う）。**★v1.2で修正**：新規作成時のコピーのみ（`phone`と同じ、詳細は上記参照） |
| `website` | CharField(max_length=500, blank=True) | **★v1.1で追加**。重複検出のスコアリング項目（§6.5.1）。対応する`Contact`側は`website`。**★v1.2で修正**：新規作成時のコピーのみ（`phone`と同じ、詳細は上記参照） |
| `created_by` | FK(User, SET_NULL, null=True) | **v0.2で追加**。**★v0.9：自動作成時はNULLとする**（後述） |
| `created_at`／`updated_at` | DateTimeField | |

`organization`にunique制約は設けない（重複検出で拾う前提のため、意図的に非unique）。

**★v0.9で明記：`Company.created_by`は自動作成時にNULLとする**。Companyが作られる経路は3つあり、うち2つには「操作した人」が存在しない。

| 経路 | `created_by` |
|---|---|
| Company新規作成画面から人が作る | `request.user` |
| OCR経由のContact作成から`link_contact_to_company()`が作る | **NULL** |
| 保険クーロン（`Run_Link_Companies`）が作る | **NULL** |

これは既存の`ActionLog.record()`が「システム実行（cron等）では`user`がNULL」としているのと同じ扱いである。明記しないと、コード君がバッチ処理にまで`request.user`を引き回そうとして設計が歪む。`link_contact_to_company()`は`created_by`を引数に取らず、常にNULLで作成する。

**Contact.company**：`FK('companies.Company', SET_NULL, null=True)`。**★v0.13：文字列参照とする**（理由は§1.2参照。既存の`Person.primary_contact = models.ForeignKey("contacts.Contact", ...)`と同じ慣習）。既存の`Contact.organization`（文字列）はそのまま残す。

**Person.company**：実体FKは持たない。読み取り専用プロパティとする。

```python
@property
def company(self):
    return self.primary_contact.company if self.primary_contact else None
```

**Deal.company**：直接FKで持たせる（§2.1参照）。マージ時の挙動は§8.1参照。

## 6.3 照合に使うフィールド

**★v1.1で拡張**。Company照合には`organization`・`domain`（Company側）／`org_domain_name`（Contact側）に加え、**`phone`（対応：`Contact.org_phone`）・`address`（対応：`Contact.address`）・`website`（対応：`Contact.website`）の3項目を追加**し、計5項目とする。「電話・住所・URLはCompanyモデル自体が持たないため比較対象にしない」というv0.2〜v1.0の制約は撤廃する。

## 6.4 手動Contact作成時の対応

`org_domain_name`はOCR経由・手動経由どちらでも、既存の`contacts/services/normalization.py`の`derive_org_domain_name()`（emailの`@`以降を抽出する純関数、実装済み）を`Contact.save()`から呼び、未設定時のみ補完する。**新規に関数を作らず、既存を呼ぶ。**

## 6.5 重複検出（CompanyDuplicateCandidate）

### 6.5.1 スコア表

**★v1.3で改訂：URLの配点を会社名・ドメインと同格（100点）に引き上げ**。v1.1〜v1.2ではURL一致を20点の弱い補助材料として扱っていたが、名刺に記載されたURLは公式サイトを指す確度が高く、「会社名一致だけで実は別会社だった」というリスクへの対抗材料として、ドメインと同格に扱うべきという判断に基づく。あわせてドメインの配点も100点に統一し、会社名・ドメイン・URLの3項目を対等な主軸とした（詳細な判定は§6.5.2）。

| フィールド | スコア |
|---|---|
| ドメイン（`org_domain_name`と`Company.domain`の完全一致、両者とも`is_generic_email_domain()`で汎用ドメインでないこと必須） | 100（★v1.3で120から変更） |
| 会社名（`organization`完全一致） | 100 |
| URL（`website`の正規化後の完全一致、詳細は後述） | 100（★v1.3で20から変更） |
| 電話一致（`org_phone`と`Company.phone`の完全一致） | 20 |
| 住所一致（`address`と`Company.address`の完全一致） | 20 |

満点340点。

**フリーメール除外（★重要、v1.0から変更なし）**：ドメイン一致の加点条件に「両者とも汎用ドメインでないこと」を追加する。既存の`contacts/services/normalization.py`の`is_generic_email_domain()`（実装済み、OCR仕様書§11.9.6準拠）を呼ぶ。新規に除外リストを作らない。

**★v1.3で新設：URLの正規化（`normalize_website()`）**。名刺に記載されるURLはトップページのみであり、`https://`／`www.`の有無、末尾スラッシュの有無といった表記ゆれがそのままでは一致しない。また、共有ホスティング（例：`https://example.com/companyA/`のように、1つのドメイン配下に複数の契約者のページが同居する形式）では、**ドメイン部分だけを比較すると無関係な会社同士が誤って一致してしまう**。名刺記載のURLは通常トップページのみでパスが深くならないため、「ドメイン＋パスの最初のセグメント」を正規化後の比較キーとすることで、独自ドメイン・共有ホスティングの両方に同じロジックで対応する。

```python
from urllib.parse import urlparse


def normalize_website(url):
    """URLを比較用に正規化する。ドメイン＋パスの最初のセグメントを返す。

    独自ドメイン（https://network-tokai.co.jp/）はパスが空なので
    ドメインのみに正規化される。共有ホスティング配下のページ
    （https://example.com/companyA/）はパスの最初のセグメントまで
    含めることで、同じドメインを使う別契約者（example.com/companyB）
    と区別できる。名刺記載のURLは通常トップページのみのため、
    このロジックだけで十分機能する。
    """
    if not url:
        return ''
    normalized = url.strip().lower()
    if '://' not in normalized:
        normalized = f'//{normalized}'
    parsed = urlparse(normalized, scheme='https')
    netloc = parsed.netloc.removeprefix('www.')
    segments = [p for p in parsed.path.split('/') if p]
    first_segment = segments[0] if segments else ''
    return f'{netloc}/{first_segment}' if first_segment else netloc
```

`Company.website`・`Contact.website`はどちらも生のURL文字列のまま保存する（正規化前の値を表示に使うため）。比較の直前に`normalize_website()`を通す。

**★v1.3で改訂：`calculate_company_score()`を`calculate_company_match()`に置き換える（重要）**。レビューで、スコア（int）だけを返す関数と、真偽値4つ（`name_match`／`domain_match`／`url_match`、スコア）を引数に取る`determine_company_rank()`（§6.5.2）の間をつなぐコードが一度も示されていない、という指摘があった。`name_match`等の真偽値をどこでどう計算するかが未定義のままでは、コード君が`calculate_company_score()`の一致判定ロジックを重複実装するか、独自解釈で埋めるかのどちらかになってしまう。

**対策として、スコアと各項目の一致フラグをまとめて返す`calculate_company_match()`を新設し、`calculate_company_score()`を置き換える。** 呼び出し元は返り値から`.score`を取り出して表示・保存用に使い、`.name_match`／`.domain_match`／`.url_match`をそのまま`determine_company_rank()`に渡せる。

```python
from dataclasses import dataclass


@dataclass
class CompanyMatchResult:
    """calculate_company_match() の返り値。スコアと各項目の一致フラグを
    まとめて持つ。determine_company_rank()（§6.5.2）はこの一致フラグを
    そのまま受け取れる形にしている。"""
    score: int
    name_match: bool
    domain_match: bool
    url_match: bool


def calculate_company_match(organization_a, domain_a, phone_a, address_a, website_a,
                             organization_b, domain_b, phone_b, address_b, website_b):
    """★v1.3で calculate_company_score() から改称・改修。
    Companyインスタンスではなく生の値を受け取る形は維持する（★v1.2の理由と
    同じ：Contact対Company・Company対Companyの両方で使い回すため）。

    スコアだけでなく各項目の一致フラグも返すことで、determine_company_rank()
    （§6.5.2）への受け渡しを1関数で完結させる。真偽値の導出ロジックを
    ここに一本化することで、呼び出し元がスコアリングと同じ判定を
    重複実装する必要がなくなる。
    """
    name_match = bool(organization_a) and organization_a == organization_b
    domain_match = bool(domain_a) and domain_a == domain_b and not is_generic_email_domain(domain_a)
    phone_match = bool(phone_a) and phone_a == phone_b
    address_match = bool(address_a) and address_a == address_b

    normalized_website_a = normalize_website(website_a)
    normalized_website_b = normalize_website(website_b)
    url_match = bool(normalized_website_a) and normalized_website_a == normalized_website_b

    score = 0
    if name_match:
        score += 100
    if domain_match:
        score += 100
    if phone_match:
        score += 20
    if address_match:
        score += 20
    if url_match:
        score += 100

    return CompanyMatchResult(
        score=score, name_match=name_match,
        domain_match=domain_match, url_match=url_match,
    )
```

**呼び出し方（★v1.3で改訂：`determine_company_rank()`までつなげた完全な呼び出し例）**。Contact対Company（§6.5.4）：

```python
match_result = calculate_company_match(
    contact.organization, contact.org_domain_name, contact.org_phone,
    contact.address, contact.website,
    company.organization, company.domain, company.phone,
    company.address, company.website,
)
rank = determine_company_rank(
    match_result.score, match_result.name_match,
    match_result.domain_match, match_result.url_match,
)
```

Company対Company（§6.5.3、本節）：

```python
match_result = calculate_company_match(
    company_a.organization, company_a.domain, company_a.phone,
    company_a.address, company_a.website,
    company_b.organization, company_b.domain, company_b.phone,
    company_b.address, company_b.website,
)
rank = determine_company_rank(
    match_result.score, match_result.name_match,
    match_result.domain_match, match_result.url_match,
)
```

`CompanyDuplicateCandidate`の保存時は`match_result.score`を`score`フィールドに、`rank`を`rank`フィールドに、それぞれ渡す（§6.5.3の`get_or_create_candidate_pair()`の`defaults`に含める）。**Contact対Companyのスコアリング**（§6.5.4の照合フロー用）は上記の呼び出し例を参照。`normalize_website()`は`calculate_company_match()`内部で呼ばれるため、呼び出し元は生のURL文字列を渡すだけでよい。

### 6.5.2 ランク判定

**★v1.3で改訂：判定条件を「会社名軸」から「会社名 AND (ドメイン OR URL)」に変更**。v1.1〜v1.2では`exact_match`／`possible_high`の必須条件が「会社名一致 かつ ドメイン一致」に固定されていたが、URLをドメインと同格の主軸に引き上げたことに伴い、ドメインとURLのどちらか一方が会社名と一致していれば同じランクに到達できる形に一般化した。判定は上から順に評価し、最初に条件を満たしたランクを採用する。

**★v1.3改訂時の反省点（レビューで発覚、追記）**：当初案では`possible_mid`／`possible_low`の必須条件を「会社名 OR ドメイン」のみとし、URLを含めていなかった。これは「URLを会社名・ドメインと対等な主軸にする」という本節冒頭の方針と矛盾していた——ドメイン単独一致（100点）は`possible_low`としてレビューキューに乗るのに、URL単独一致（同じ100点）は候補外として一切記録が残らない、という非対称を生んでいた。下表・コードは、`possible_mid`／`possible_low`の必須条件にも`url_match`を加えた修正版である。

| ランク | 必須の一致条件 | スコア条件 | 実際に到達する組み合わせの例 |
|---|---|---|---|
| exact_match（自動リンク） | 会社名一致 AND (ドメイン一致 OR URL一致) | 220点以上 | 会社名+ドメイン+電話/住所いずれか1つ以上、または会社名+URL+電話/住所いずれか1つ以上（100+100+20=220） |
| possible_high | 会社名一致 AND (ドメイン一致 OR URL一致) | 200点（追加裏付けなし） | 会社名+ドメインのみ、または会社名+URLのみ（100+100=200点ちょうど） |
| possible_mid | 会社名一致 OR ドメイン一致 OR URL一致（いずれか1つ） | 140点以上 | 会社名+電話+住所（140点）、ドメイン+電話+住所（140点）、URL+電話+住所（140点） |
| possible_low | 会社名一致 OR ドメイン一致 OR URL一致（いずれか1つ） | 140点未満 | 会社名のみ（100点）、ドメインのみ（100点）、URLのみ（100点）、いずれか+電話または住所1つ（120点） |
| （候補外） | — | — | 電話・住所単独一致のみ |

```python
def determine_company_rank(score, name_match, domain_match, url_match):
    """★v1.3で全面改訂。
    exact_match/possible_high は会社名一致を必須条件から外さない
    （ドメイン・URL・電話・住所が全部一致していても会社名が違えば
    自動リンクしない。OCR誤読の救済より、グループ会社間で連絡先を
    共有する別法人を誤って自動統合するリスクを避けることを優先した）。
    possible_mid/possible_low は会社名・ドメイン・URLのいずれか1つが
    一致していれば候補になる（★v1.3修正：当初案ではURLを除外していたが、
    「対等な主軸」という方針と矛盾する非対称を生んでいたため、
    ドメイン・会社名と同列に含めた）。
    """
    if name_match and (domain_match or url_match) and score >= 220:
        return 'exact_match'
    if name_match and (domain_match or url_match) and score >= 200:
        return 'possible_high'
    if (name_match or domain_match or url_match) and score >= 140:
        return 'possible_mid'
    if (name_match or domain_match or url_match) and score >= 100:
        return 'possible_low'
    return None  # 候補外
```

**設計上のポイント**：

- **会社名一致は`exact_match`／`possible_high`の必須条件から外さない**。ドメイン・URL・電話・住所が全て一致していても会社名が不一致なら`possible_mid`止まりとする。理由は、これらの項目が全て一致するケースには「OCR誤読による会社名の表記ゆれ」だけでなく、「持株会社と事業会社のように、同じ住所・電話・サイトを共有する別法人」というケースが実務上あり得るため。会社名を必須条件から外して自動リンクを広げると、後者のケースで無関係な別法人を誤って統合してしまう。前者（OCR誤読）は`possible_mid`でレビューキューに乗るため、人が見て正しく統合すればよく、実害は「自動化されないだけ」で済む
- `exact_match`と`possible_high`は同じ必須条件（会社名＋(ドメインまたはURL)一致）だが、電話・住所の追加裏付けが1つでもあるかどうかでランクが分かれる。基本形（200点）は自動リンクせず`possible_high`に留め、より確からしい根拠が揃って初めて自動リンク対象になる、という一段保守的な設計を維持している
- `possible_mid`／`possible_low`は「会社名一致だけの方が確からしい」という直感に基づき、v1.1〜v1.2のように一致した項目の種類（ドメインかどうか）でランクを分けるのをやめ、**「会社名・ドメイン・URLのいずれか一致していれば、あとはスコア合計だけで判定する」**形に統合した。**★v1.3修正**：この2ランクの必須条件からURLを外していた当初案は、`exact_match`／`possible_high`側の主軸としてURLを使っているからという理由だったが、これは「対等な主軸」という方針と矛盾する非対称（ドメイン単独一致はレビューキューに乗るのにURL単独一致は候補外になる）を生んでいたため撤回し、会社名・ドメインと同列にURLも含めた

**自動リンクは`exact_match`のみ。** `possible_high`／`possible_mid`／`possible_low`は、スコアの大小に関わらず**すべて新規Company作成＋レビューキュー行き**とする（v1.0までと同じ方針）。

**★v0.4で確定（検討の上、緩和案は不採用、v1.3でも維持）**：「会社名完全一致かつドメインが矛盾しない（少なくとも一方が空）場合まで自動リンクを広げる」という緩和案も検討したが、**採用しない**。理由は、この方式だと「同じ会社の名刺が複数枚（メール記載なし）来るたびに新規Companyが作られ、レビューキューが膨らむ」という運用負担を軽減できる一方、**同一商号の別会社を誤って自動リンクしてしまうリスクを新たに背負う**ことになるため。誤結合のリスクを避けることを優先し、レビューキューの増加自体は§6.5.5の一括マージUIの使い勝手でカバーする方針とする。

### 6.5.3 CompanyDuplicateCandidateのフィールド

| フィールド | 内容 |
|---|---|
| `company_a`／`company_b` | FK(Company, CASCADE)。ID順で正規化 |
| `score` | 合計スコア（Company対Companyの`organization`・`domain`・`phone`・`address`・`website`比較で計算。**★v1.1で5項目に変更**） |
| `rank` | CharField(max_length=20, choices)：exact_match／possible_high／possible_mid／**possible_low（★v1.1で追加）** |
| `review_status` | CharField(max_length=20, choices)：pending／merged／different_company／invalidated |
| `reviewed_by`／`reviewed_at` | |
| `created_at`／`updated_at` | |

`group_id`相当の仕組みは省略する。

**★v0.7で追加：部分ユニーク制約とID順正規化ガード**。ContactUpdateViewからの再照合（§6.5.4）や保険クーロンが同じCompanyペアを何度も再生成し得るため、既存の`DuplicateCandidate`（`duplicates/models.py`）と同じ部分ユニーク制約を設ける。`review_status='pending'`の間だけ同一ペアの重複を禁止し、`merged`等に遷移した後の履歴レコードとは衝突しない。

```python
class Meta:
    constraints = [
        models.UniqueConstraint(
            fields=['company_a', 'company_b'],
            condition=models.Q(review_status='pending'),
            name='unique_pending_company_pair',
        ),
    ]
```

ペア作成時は、`company_a.id < company_b.id`となるようID順に正規化してから保存する（`oldest`をそのまま`company_a`に固定代入すると、IDの大小が逆転した場合に一意制約の判定がずれるため）。

**★v0.9で修正：`create()`ではなく`get_or_create()`を使う**。v0.8のコードは`create()`のままだったため、**部分ユニーク制約に当たると`IntegrityError`が送出され、処理全体が500エラーになる**。§6.5.4の`ContactUpdateView`からの再照合、保険クーロン（`Run_Link_Companies`）のいずれも、既にキューに存在するペアに対して再度照合が走り得るため、これは日常的に発生する。

```python
def get_or_create_candidate_pair(c1, c2, **kwargs):
    """pending の同一ペアが既にあればそれを返し、なければ作る。

    ★v0.9：create() だと部分ユニーク制約（unique_pending_company_pair）違反で
    IntegrityError になる。再照合・保険クーロンで同じペアが再生成され得るため
    get_or_create にする。
    """
    a, b = (c1, c2) if c1.id < c2.id else (c2, c1)
    candidate, _created = CompanyDuplicateCandidate.objects.get_or_create(
        company_a=a,
        company_b=b,
        review_status=CompanyDuplicateCandidate.ReviewStatus.PENDING,
        defaults=kwargs,      # score / rank 等。review_status は含めない
    )
    return candidate
```

**`review_status='pending'`は検索キーに含め、`defaults`には含めない。** 検索キーに含めることで、過去に`merged`／`different_company`として処理済みの履歴レコードとは衝突せず、**一度処理したペアが再び怪しくなった場合に新しいpendingを積める**（既存の`DuplicateCandidate`が「再マージのためUniqueConstraintなし」としているのと同じ考え方）。`defaults`に含めると、既存レコードが見つかったときにスコアが更新されずに古い値が残る点にも注意する。

**★v1.3で追加：3関数（`calculate_company_match()`／`determine_company_rank()`／`get_or_create_candidate_pair()`）をつなぐ完全な呼び出し例**。レビューで、この3つの関数が個別に定義されているだけで、実際につなぐコードが一度も示されていないという指摘があった。Company対Companyの候補登録（§6.5.4手順4の「exact_match以外の候補」で使う想定）は次のようになる。

```python
def register_company_candidate(company_a, company_b):
    """Company対Companyでスコア・ランクを計算し、レビューキューに積む。
    ★v1.3新設：calculate_company_match → determine_company_rank →
    get_or_create_candidate_pair を1つにまとめた、実際の呼び出し例。
    """
    match_result = calculate_company_match(
        company_a.organization, company_a.domain, company_a.phone,
        company_a.address, company_a.website,
        company_b.organization, company_b.domain, company_b.phone,
        company_b.address, company_b.website,
    )
    rank = determine_company_rank(
        match_result.score, match_result.name_match,
        match_result.domain_match, match_result.url_match,
    )
    if rank is None:
        return None  # 候補外。CompanyDuplicateCandidateは作らない
    return get_or_create_candidate_pair(
        company_a, company_b,
        score=match_result.score, rank=rank,
    )
```

**★v0.9で明記：スコアは2回計算する**。§6.5.4のスコアリング（**★v1.3で3項目を100点に統一：ドメイン100点・会社名100点・URL100点、電話20点・住所20点**）は**Contact対Company**の比較値であり、本モデルの`score`は**Company対Company**の比較値である。両者は別物なので、§6.5.4で新規Companyを作って候補を積む際、**候補登録用に改めてCompany対Companyでスコアを計算し直す**必要がある。v0.8の記述はこの点が読み取りにくかった。上記の`register_company_candidate()`が、この「候補登録用の再計算」を実際に行う関数である。

### 6.5.4 判定フロー（`link_contact_to_company(contact)`）

**★v0.9で改名：`find_or_link_company()` → `link_contact_to_company()`**。CLAUDE.md §10の命名規約では`find_*`／`get_*`／`search_*`は「DBを読むが書かない」準関数と定義されている。本関数はCompanyを新規作成し、Contactにリンクし、CompanyDuplicateCandidateを積む**完全に副作用のある関数**であり、`find_`を冠するのは規約違反だった。`link_`系に改める。

**★v0.9で明記：`queryset.update()`を使わず、インスタンスに設定して`save()`する**。`Contact.objects.filter(...).update(company=...)`のような書き方をすると、**呼び出し側がメモリ上に持っている`contact`インスタンスには反映されない**。§6.5.4の`ContactUpdateView`は`self.object.company`を見て旧Companyとの比較を行うため、`update()`で実装すると呼び出し側に`refresh_from_db()`を要求することになる。本関数は必ず`contact.company = company` → `contact.save(update_fields=['company', 'updated_at'])`の形で書き、**呼び出し側に再取得を要求しない**。


**★v0.11で修正：除外先を「Contactのリンク先探索」から「Company対Companyのペアリング・マージ対象選択」に変更（重要）**。v0.10はこの節に「`different_company`判定済みのCompanyをstep 2の比較対象から除外する」対策を追加したが、これは適用場所を誤っていた。

`different_company`が意味するのは「Company AとCompany Bは別法人だと人間が判断した」という**候補どうしの否定**である。一方、本節（`link_contact_to_company`）がやっているのは「このContactがどのCompanyに属するか」という**単一Contactの帰属先探し**であり、構造が異なる。

具体的な破綻はこうなる。Contact新規作成時は`contact.company`がまだ`NULL`で、渡すべき「自分」のCompanyが存在しない。Contact更新時の再照合は「`organization`／`org_domain_name`が変わったので、今の`company`が正しいとは限らない」という前提で走るのに、今の`company`を基準に除外するのは、今から見直そうとしている当のリンク先を基準に候補を絞るという循環になる。

**除外が本来効くべき場面は、Company対CompanyのペアをDBに作る箇所である**。具体的には、本節の**手順4（exact_match複数候補の1対1ペアリング）**と、**§6.5.5の一括マージ対象選択**の2箇所。「AとBは別会社」と既に人間が判断しているなら、次にexact_matchでA・Bがどちらも候補に挙がった際に、A-Bのペアを再生成しない・一括マージの対象候補として出さない、という使い方が本来の対応物である。

対策として、同型の除外関数を追加する。

```python
# companies/services/company_matching.py
def get_companies_confirmed_as_different(company):
    """company との組み合わせで review_status='different_company' と
    判定済みの相手 Company の ID 集合を返す。

    duplicates/services/duplicate_detection.py の
    get_persons_confirmed_as_different() と同型。ただし呼び出し場所は異なる
    （★v0.11：Contactのリンク先探索ではなく、Company対Companyのペア生成・
    マージ対象選択で使う。理由は上記参照）。
    """
    pairs = CompanyDuplicateCandidate.objects.filter(
        models.Q(company_a=company) | models.Q(company_b=company),
        review_status=CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY,
    )
    return {
        (p.company_b_id if p.company_a_id == company.id else p.company_a_id)
        for p in pairs
    }
```

適用箇所は2つ。

- **本節の手順4（exact_match複数候補のペアリング）**：最古の代表Companyと各余剰候補Companyのペアを作る際、`get_companies_confirmed_as_different(代表Company)`に含まれる候補とはペアを作らない（＝レビューキューに積まない）
- **§6.5.5の一括マージ対象選択**：一覧画面でチェックボックスを出す際、既に選択されているCompanyと`different_company`判定済みのCompanyは選択候補から外す（または選択時に警告を出す）。詳細は§6.5.5参照

配置：`companies/services/company_matching.py`

1. `contact.organization`が空文字またはNoneなら即座にreturn（Company照合をスキップ、`Contact.company`は`NULL`のまま）
2. **既存Companyのうち`status=active`のもののみを比較対象にする**（Personの`find_duplicate_contacts`が`person__status=ACTIVE`で絞っているのと同じ理由。この絞り込みがないと、マージで消えたCompanyが候補に残り、死んだCompanyに新しいContactが繋がってしまう）
3. 既存Companyとスコア計算。`calculate_company_match()`（§6.5.1、★v1.3で`calculate_company_score()`から改称）をContact対Companyの呼び出し方（§6.5.1の呼び出し例参照）で使い、返り値の`match_result.score`／`.name_match`／`.domain_match`／`.url_match`を`determine_company_rank()`（§6.5.2）に渡してランクを求める
4. **exact_match（会社名一致 AND (ドメイン一致 OR URL一致)、220点以上、★v1.3で条件を更新）の候補が存在する場合のみ**、その候補にリンク。exact_match候補が複数存在する場合（`organization`／`domain`ともユニーク制約がないため起こり得る）、`created_at`昇順で**最も古いもの**にリンクし、残りのexact_match候補とは`CompanyDuplicateCandidate`をレビューキューに積む。**★v0.11：ただし`get_companies_confirmed_as_different(代表Company)`に含まれる候補とはペアを作らない**（理由は上述）。「最も古いものを正とする」は§6.5.5の一括マージ（複数選択から代表を1つ選ぶ）とも思想が揃う。**★v0.6で具体化：ペアリングは総当たり（N×(N-1)/2）ではなく、「最古の代表Company（リンク先）と、各余剰候補Companyとの1対1ペア」を作成する**（例：exact_match候補がA・B・Cの3件でAが最古なら、`(A, B)`・`(A, C)`の2組を作成し、`(B, C)`は作らない）
5. exact_matchの候補が存在しない場合（**★v1.1で更新**：possible_high／possible_mid／possible_low止まり、または候補なし）→ **新規Company作成**。possible_high／possible_mid／possible_lowだった候補があれば、それら全てとの`CompanyDuplicateCandidate`をレビューキューに積む

**★v1.2で追加：新規Company作成時のフィールドコピー（実体コード）**。`domain`は既存（v0.2〜）の仕様通り最初のContactの値をコピーする。`phone`／`address`／`website`も**新規作成時のみ**、同様にコピーする（§6.2で確定した通り、これ以外の穴埋め・マージ時引き継ぎは次フェーズ）。

```python
def create_company_from_contact(contact):
    """exact_matchの候補が存在しない場合の新規Company作成。
    ★v1.2：domain（既存）に加えphone/address/websiteも新規作成時のみコピーする。
    穴埋め・マージ時引き継ぎは対象外（§6.2、§6.5.6参照）。"""
    domain = (contact.org_domain_name
              if contact.org_domain_name and not is_generic_email_domain(contact.org_domain_name)
              else '')
    return Company.objects.create(
        organization=contact.organization,
        domain=domain,
        phone=contact.org_phone,
        address=contact.address,
        website=contact.website,
        created_by=None,  # ★v0.9：システム経由の自動作成はNULL
    )
```

**呼び出し元**：OCR経由のContact作成、手動Contact作成（`ContactCreateView`）の両方から**同期実行**する（Personの重複検出よりずっと軽量なため）。`ContactUpdateView`（Contact手動編集時）でも、`organization`または`org_domain_name`が変更された場合は`link_contact_to_company()`を再実行する。OCRの誤認識（例：「株式会社ネットワー夕東海」）を後から手動修正した場合に再判定が走らないと、誤認識由来のゴミCompanyに紐づいたまま残ってしまうため。

**★v0.8で追加：`ContactUpdateView`での実行順序（★実装上の罠）**。再照合を素朴に実装すると、`form.save()`を呼んだ時点で`contact.company`は**既に新しいCompanyに書き換わっている**。旧Companyへの参照を`form.save()`の**前**に変数へ退避しておかないと、後述の孤児判定で「更新後の新Company側」の残存件数を数えることになり、**作成したばかりの正しいCompanyをarchivedに落とす**取り違えバグになる。コード君はこの順序を明示しないと高い確率で誤る。

```python
# ContactUpdateView.form_valid()
def form_valid(self, form):
    old_company = self.object.company  # ★ save() の前に退避（必須）
    old_organization = self.object.organization
    old_domain = self.object.org_domain_name

    response = super().form_valid(form)  # ここで contact.company が書き換わり得る

    if (self.object.organization != old_organization
            or self.object.org_domain_name != old_domain):
        link_contact_to_company(self.object)
        # 退避しておいた old_company に対して残存件数をチェックする
        if old_company and old_company != self.object.company:
            archive_company_if_orphaned(old_company)
    return response
```

**★v0.7で追加：再照合による孤児Companyの後始末**。`ContactUpdateView`での再照合により、Contactのリンク先が別のCompanyに変わると、元のCompanyにはもうどのContact・Dealも紐づかなくなることがある（動機として挙げたOCR誤認識のケースでは、Contactは正しいCompanyに移るが、誤認識由来のゴミCompany自体は`status=active`のまま残り、以後もずっと照合候補に上がり続けてしまう）。**再照合の結果、旧Companyに紐づくContact・Dealが1件も残らなくなった場合は`status=archived`に落とす**。保険用クーロン（`Run_Link_Companies`）側にも、同様の孤児Company検出処理を追加し、再照合の呼び出し漏れがあっても拾えるようにする。

**★v1.0で追加：`archive_company_if_orphaned()`の実体（未定義だった関数）**。上記コード例で呼び出している`archive_company_if_orphaned(old_company)`の中身が、v0.19まで一度も示されていなかった。

```python
# companies/services/company_matching.py
def archive_company_if_orphaned(company):
    """再照合により孤児となったCompanyの後始末。
    紐づくContact・Dealが1件も存在しない場合のみ status を ARCHIVED にする。"""
    if not company.contacts.exists() and not company.deals.exists():
        company.status = Company.Status.ARCHIVED
        company.save(update_fields=['status', 'updated_at'])
```

呼び出し元は本節の`ContactUpdateView.form_valid()`と、上述の保険用クーロンの2箇所。§6.6の手動アーカイブ（`companies.change_company`権限で人が行う操作）とは別に、この関数はシステムが自動判定するため権限チェックを持たない（`ActionLog`記録も行わない。人の判断を介さない自動処理であるため、§6.6の手動アーカイブと同列の監査対象にはしない）。

**プレフィルタ（性能対策）**：Companyが数千〜数万件規模になった場合に備え、Personの`find_duplicate_contacts`と同じ効率化を適用する。スコア計算前に、SQLレベルで`organization=...`または`domain=...`のOR条件で候補を事前絞り込みしてから、絞り込んだ候補にのみスコア計算を行う。

**NULL安全策**。`contact.org_domain_name`が空文字またはNoneの場合、`domain=""`や`domain__isnull=True`のCompanyが意図せず大量にヒットし、プレフィルタが意味をなさなくなる。**ドメイン条件は値が存在し、かつ汎用ドメインでない場合のみQに追加する**、動的なQオブジェクト構築にする。

**★v0.4で追加**：汎用ドメイン（`is_generic_email_domain()`が`True`を返すもの）も、空文字と同じ理由でプレフィルタのOR句から除外する。また比較対象は`status=active`のCompanyに限定する（§6.5.4参照）。

**★v1.1で確認：プレフィルタは変更不要**。§6.5.2の4段階ランクはいずれも「会社名一致」または「ドメイン一致」のどちらかを必須条件としており、電話・住所・URLだけが一致するケースは単独では候補にならない（§6.5.2の「候補外」参照）。したがってプレフィルタのOR条件に`phone`／`address`／`website`を加える必要はなく、`organization`／`domain`の2条件のままでよい。

```python
def build_company_prefilter_query(contact):
    query = models.Q(organization=contact.organization)
    if contact.org_domain_name and not is_generic_email_domain(contact.org_domain_name):
        query |= models.Q(domain=contact.org_domain_name)
    return query

candidates = Company.objects.filter(
    build_company_prefilter_query(contact),
    status=Company.Status.ACTIVE,
)
```

**保険用クーロン（`Run_Link_Companies`、仮称）**：`Contact.company IS NULL`かつ`organization`が空でないContactのみを対象に、`link_contact_to_company()`を再実行する。

### 6.5.5 マージ処理

**一括マージUI（★必須機能）**：Company一覧・レビューキュー画面で、複数のCompanyをチェックボックスで複数選択し、代表（surviving）を1つ指定して「統合する」ボタンで一括マージを実行する。Personの1対1レビューとは異なり、**複数選択→1つに統合**を前提に設計する。

**★v0.8で追加：権限要求を明記**。v0.7は§7.5で`merge_company`権限を宣言し§7.6で配布先を決めたが、**それを要求する箇所が仕様書のどこにも書かれていなかった**。既存の`duplicates/views.py`が各マージ系Viewに`permission_required = 'persons.merge_person'`を置いているのと同じ形で、**一括マージViewに`permission_required = 'companies.merge_company'`を置く**。

**★v0.9で追加：`execute_company_merge()`の入力検証**。この関数はViewからチェックボックスで選択されたIDの集合を受け取るため、想定外の入力に対するガードが必要である。v0.8には検証が一切なかった。

```python
def execute_company_merge(surviving_company, companies_to_merge, user):
    """★v0.9：入力検証を先に行う。★v0.10：0件チェックを先頭に並べ替え。
    ★v0.11：different_company 判定済みのペアが混ざっていないかを検証する。"""
    # 1. 対象が0件でないか（先に弾く。以降のチェックは空リストでも例外にはならないが、
    #    「対象がない」というエラーを最初に返す方が素直）
    if not companies_to_merge:
        raise ValueError('統合対象が指定されていません。')
    # 2. surviving が統合対象に混ざっていないか
    #    混ざると mark_as_merged(self) で自分自身を統合先とする自己参照ループが生まれ、
    #    get_surviving_company() が無限ループする
    if surviving_company in companies_to_merge:
        raise ValueError('統合先の会社を統合対象に含めることはできません。')
    # 3. 全社が active か（merged 済みを再統合するとチェーンが枝分かれする）
    if any(c.status != Company.Status.ACTIVE for c in companies_to_merge):
        raise ValueError('統合できるのは有効な会社のみです。')
    if surviving_company.status != Company.Status.ACTIVE:
        raise ValueError('統合先に指定できるのは有効な会社のみです。')
    # 4. 件数上限（誤操作で全選択されたまま実行されるのを防ぐ）
    if len(companies_to_merge) > 20:
        raise ValueError('一度に統合できるのは20社までです。')
    # 5. ★v0.11で追加：surviving と different_company 判定済みの会社が
    #    混ざっていないか（§6.5.4参照。人が「別会社」と判断した組み合わせを
    #    誤って統合してしまう事故を防ぐ）
    confirmed_different = get_companies_confirmed_as_different(surviving_company)
    if confirmed_different & {c.id for c in companies_to_merge}:
        raise ValueError(
            '統合対象に、以前「別会社」と判定した会社が含まれています。'
            '本当に統合する場合は、先にレビューキューでその判定を取り消してください。'
        )
    with transaction.atomic():
        ...
```

上限を20社としたのは、レビュー画面で人が「これらは同じ会社だ」と目視確認できる現実的な件数の上限だからである。それを超える統合が必要な場合は、複数回に分けて実行する（マージは繰り返し可能なため運用上の支障はない）。View側では、これらの`ValueError`をフォームエラーとして表示し、500にしない。

**★v0.11で追加：一覧・レビューキュー画面でのチェックボックスにも同じ除外を反映する**。§6.5.4で導入した`get_companies_confirmed_as_different()`は、Service層のガードだけでなく、**一括マージUIでチェックボックスを出す時点**でも使う。統合先として選んだCompanyと`different_company`判定済みのCompanyは、選択肢からグレーアウトする（または警告付きで選択可能にする）。これにより、Service層の`ValueError`に頼る前に画面上で誤操作を防げる。

**★v0.7で構成を見直し**：`transfer_contacts_to_company`（Company単体のFK付け替え・ドメイン引き継ぎ）と、`CompanyDuplicateCandidate`の後始末・`ActionLog`記録（呼び出し元＝一括マージ実行関数の責務）を分離する。理由は2つある。

1. **ActionLog記録の欠落防止**：本文は一貫して「マージ履歴はActionLog.record()で記録するのみ」としてきたが、実装イメージに記録処理が一度も現れていなかった。Companyマージの履歴はこれが唯一の記録手段なので、抜けると誰がいつどの会社を統合したか分からなくなる（復元機能を持たない前提が崩れる）。Person側が「ActionLog記録は呼び出し元`Execute_Merge_*`の責務」としているのと同じ形に揃える
2. **一括マージ時のペア判定の正確性**：AにB・Cをまとめて統合する場合、**B-Cのペアも「両方Aと同一と判断された」以上`merged`が正しい**。単体の`transfer_contacts_to_company`をB・Cそれぞれに独立して呼ぶだけでは、B-Cペアが「第三者の巻き添え」として`invalidated`に落ちてしまう。マージセット全体（surviving＋統合される全社）を把握した上で判定する必要があるため、この判定はマージ単体の関数ではなく、一括マージ全体を統括する関数の責務とする

```python
# companies/models.py
from contacts.models import Contact  # ★v0.13：通常importでよい（理由は後述）


def transfer_contacts_to_company(self, surviving_company):
    """Company統合時の一括付け替え（1社分）。CompanyDuplicateCandidateの後始末や
    ActionLog記録は含まない（呼び出し元 execute_company_merge() の責務）。
    Person.transfer_contacts_to() とは別名・別実装（あちらはmerge_reason必須の
    重い処理でstatus/previous_status/previous_personまで扱うが、Companyは
    単純な一括FK付け替えのみのため、同名にすると挙動を誤解されるため区別する）。

    呼び出し元の transaction.atomic() 内で実行されることが前提条件。
    """
    from django.apps import apps
    Deal = apps.get_model('deals', 'Deal')  # companies→dealsの逆参照は遅延import

    Contact.objects.filter(company=self).update(company=surviving_company)
    Deal.objects.filter(company=self).update(company=surviving_company)

    # surviving側のdomainが空で、消える側(self)に有効なドメインがあれば引き継ぐ。
    # maybe_fill_company_domain()の唯一の実効的な発動タイミングはここ（人がレビューして
    # 統合を判断した、最も信頼できる根拠に基づく穴埋め）。exact_matchでリンクする時点では
    # 既にdomainが埋まっているため、他の場所で呼んでも基本的にno-opになる。
    maybe_fill_company_domain(surviving_company, self.domain)

    self.mark_as_merged(surviving_company)


def execute_company_merge(surviving_company, companies_to_merge, user):
    """一括マージの実行本体。呼び出し元（View/Service）はこの関数を1回呼ぶだけでよい。
    companies_to_merge: マージされる側のCompanyのリスト（1件以上）。"""
    merge_set_ids = {surviving_company.id} | {c.id for c in companies_to_merge}
    now = timezone.now()  # ★v0.18：reviewed_at に使う（2箇所で同一時刻にするため先に確保）

    with transaction.atomic():
        for company in companies_to_merge:
            company.transfer_contacts_to_company(surviving_company)

        # ★マージセット内（surviving含む）の全ペア → merged
        # （AにB・Cを統合する場合、B-Cのペアも「両方Aと同一」と判断された以上mergedが正しい）
        # ★v0.18：reviewed_by/reviewed_at を追加（.update()は auto_now の恩恵を
        # 受けないため updated_at も明示的に必要、§6.5.6と同じ注意点）
        CompanyDuplicateCandidate.objects.filter(
            company_a_id__in=merge_set_ids,
            company_b_id__in=merge_set_ids,
            review_status='pending',
        ).update(
            review_status='merged',
            reviewed_by=user,
            reviewed_at=now,
            updated_at=now,
        )

        # マージセットの誰かが関与するが、相手がセット外の pending 候補 → invalidated
        # （マージで消えたことで無意味になった、第三者との候補）
        # ★v0.18：こちらも reviewed_by/reviewed_at を追加。invalidated はマージした
        # user の判断そのものではないが、「このマージ操作によって無効になった」という
        # 事実を記録する意味で、実行者を reviewed_by として残す
        CompanyDuplicateCandidate.objects.filter(
            models.Q(company_a_id__in=merge_set_ids) | models.Q(company_b_id__in=merge_set_ids),
            review_status='pending',
        ).exclude(
            company_a_id__in=merge_set_ids,
            company_b_id__in=merge_set_ids,
        ).update(
            review_status='invalidated',
            reviewed_by=user,
            reviewed_at=now,
            updated_at=now,
        )

        # ActionLog記録は呼び出し元（本関数）の責務とする（Person側の慣習と統一）
        for company in companies_to_merge:
            ActionLog.record(
                user=user, action='company_merged', content_object=company,
                data={'surviving_company_id': str(surviving_company.id)},
            )
```

**★v0.18で追加：`CompanyDuplicateCandidate.reviewed_by`／`reviewed_at`が永久にNULLだった問題を修正**。`review_status`が`pending`から遷移する箇所は本関数と、次項の「別会社と判断」の2つしかないが、17版を通じて**どちらも`reviewed_by`／`reviewed_at`を設定するコードが示されていなかった**。上記の`.update()`はQuerySetの一括更新であり`auto_now`の恩恵を受けないため、`updated_at`も含めて明示的に指定する必要がある（§6.5.6で確立した注意点と同じ）。これを埋めないと、「誰が・いつ、この2社は統合すべきと判断したか」という候補ペア単位の監査証跡が失われる。

### 6.5.5.1 CompanyDuplicateCandidateの「別会社」判定（★v0.18で新設）

§7.5は「レビューキュー画面での操作（`different_company`への遷移を含む）は`companies.merge_company`で判定する」と権限だけを決めていたが、**この遷移を実行する関数自体が17版を通じて一度も定義されていなかった**。既存のPerson側`DuplicateCandidate`には`mark_as_merged(user, review_result, note)`／`mark_as_different_person(user, ...)`という、userを受け取り`reviewed_by`／`reviewed_at`を書き込むインスタンスメソッドがあるが、Company側にこれに相当するものがなかった。

同型のインスタンスメソッドを新設する。

```python
class CompanyDuplicateCandidate(models.Model):
    ...

    def mark_as_different_company(self, user, note=''):
        """レビューキュー画面で「別会社」と判断された時に呼ぶ。
        Person側の DuplicateCandidate.mark_as_different_person() と同型。"""
        with transaction.atomic():
            self.review_status = CompanyDuplicateCandidate.ReviewStatus.DIFFERENT_COMPANY
            self.reviewed_by = user
            self.reviewed_at = timezone.now()
            self.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'updated_at'])
            ActionLog.record(
                user=user, action='company_different_company', content_object=self,
                data={
                    'company_a_id': str(self.company_a_id),
                    'company_b_id': str(self.company_b_id),
                },
                note=note,
            )
```

レビューキュー画面の「別会社として管理する」ボタンはこのメソッドを呼ぶ。§7.5で確定した「レビューキュー画面の操作は`merge_company`で統一する」方針は、`mark_as_different_company()`を呼ぶViewにも同じく適用する（`permission_required = 'companies.merge_company'`）。§6.5.4の判定フロー手順4・§6.5.5の一括マージ対象選択で使う`get_companies_confirmed_as_different()`（§6.5.4）は、このメソッドで`different_company`に遷移したペアを読み取る側であり、両者は書き込み・読み取りの対で機能する。

マージ履歴・復元機能（`PersonMergeLog`相当）は持たない。`ActionLog.record()`で記録するのみ。誤マージ時は手動で個別Contactを付け替え直す。

**★v0.13で追加：ContactとDealで扱いが非対称な理由**。上のコードは`Contact`をファイル冒頭で通常importしているのに、`Deal`だけ関数内で`apps.get_model()`を使う遅延importにしている。これは扱いを間違えているのではなく、**FK宣言側の書き方が違うために生じる必然的な非対称**である。

- `Contact.company`は§6.2で文字列参照（`'companies.Company'`）に決めた。文字列参照であれば`contacts/models.py`は`companies`を一切importしないため、`companies/models.py`が`Contact`を通常importしても循環は生じない（依存は`companies → contacts`の一方向のみ）
- 一方`Deal.company`は§1.2の依存図の通り`deals → companies`という向きで**既にFKがクラス参照で存在する**。ここで`companies/models.py`が`Deal`を通常importすると、`deals`が`companies`をimportし、`companies`が`deals`をimportするという**双方向の循環**が生じる。これを避けるため`Deal`だけ遅延importが必要になる

つまり「FKの参照方向が二重に交差している側（Deal）」だけ遅延importが要り、「FKの参照方向が一方向で済んでいる側（Contact、文字列参照のおかげで）」は通常importでよい、という違いである。

**★v0.11で追記：ActionLogの紐付け先について（確認事項）**。上記コードは`content_object`を**消える側（`companies_to_merge`内の各Company）**にし、`data`に統合先のIDを入れる形にしている。レビューで「既存のPersonマージが生存側に紐づけているなら合わせるべきでは」という指摘があったが、確認した限り**Person側は`content_object`がPerson自体ではなく、専用の`PersonMergeLog`インスタンスに紐づく設計**（`merge_log.record_merge_action(user)`というインスタンスメソッド経由）であり、比較の前提が異なる。

Companyには`PersonMergeLog`に相当する専用ログモデルを持たせない設計（本節冒頭）のため、単純に「消える側」「生存側」のどちらかを選ぶことになる。一括マージで複数社が消える場合、**消える各社ごとに個別のActionLogレコードを残す現在の形は、どの会社がいつどこに統合されたかを個社単位で追跡しやすい**という利点があり、これは意図的に選んだ設計である。**実装時にコード君が既存の`ActionLog`の使われ方（特に`duplicates`アプリでの類似操作）を確認し、既存の慣習と著しく異なる場合はここで調整する**ことを申し送る。

### 6.5.6 支社・支店の扱い

特別なルールは設けない。会社名は不一致・ドメインは一致（`possible_high`）というケースとして自然にレビューキューに上がり、人が「統合する／別会社として管理する」を判断する。

**★v0.5で修正：`Company.domain`の穴埋めルール**。当初「最初にリンクされたContactの値で固定し、以後一切上書きしない」としていたが、これには不具合があった。汎用ドメインの名刺が最初にリンクされて`domain`が空のまま、あるいは`org_domain_name`未記載の名刺が最初で`domain`が空のまま固定されると、**そのCompanyは以後二度とexact_matchが成立せず**（会社名一致＋ドメイン一致の両方が必要なため）、同じ会社の名刺が来るたびに新規Companyが作られ続けるという、C-1で許容したレビュー負担を無意味に増やすだけの状態になってしまう。

**修正後のルール**：`domain`が**空のときのみ**、後から紐づいたContactの`org_domain_name`（汎用ドメインでないもの）で埋める（「空→値」の一方向の穴埋め）。**既に値が入っている場合の上書きは引き続き禁止**する。これにより、§6.5.6が保証してきた「静かな誤結合は起こらない」性質（値のある`domain`を後から書き換えることはない）は維持したまま、「一度空で固定されると永久に自動リンクできない」という不具合だけを解消する。

**★v0.6で明記：発動契機**。`link_contact_to_company()`がexact_matchでリンクする時点では、その定義上ドメインは既に一致済み（＝既に埋まっている）ため、そこで呼んでも実質no-opになる。**唯一の実効的な発動タイミングは、§6.5.5のCompanyマージ処理内**である。マージは「人がレビューして統合を判断した」という最も信頼できる根拠に基づく操作なので、ここで消える側（`self`）のドメインをsurviving側に引き継いでも、誤結合リスクは増えない。

```python
def maybe_fill_company_domain(company, org_domain_name):
    """company.domain が空の場合のみ、org_domain_name で埋める。
    唯一の呼び出し元は Company.transfer_contacts_to_company()（§6.5.5）。"""
    if company.domain:  # 既に値がある場合は何もしない
        return
    if org_domain_name and not is_generic_email_domain(org_domain_name):
        company.domain = org_domain_name
        # ★v0.8：auto_now=True の updated_at は update_fields に含めないと更新されない。
        # 既存の Person.mark_as_merged() / mark_as_active() も必ず含めている。
        company.save(update_fields=['domain', 'updated_at'])
```

**★v1.1で追記、v1.2で修正：`phone`／`address`／`website`はマージ時に引き継がない（今回のスコープ外）**。§6.2でこの3項目は「新規作成時のみコピー」（`domain`のような継続的な穴埋めではない、§6.2参照）としたため、`maybe_fill_company_domain()`のようなマージ時引き継ぎ処理も対象外である。マージ時、消える側のCompanyが`phone`／`address`／`website`に値を持っていても、生存側が空のままなら引き継がれず空欄のままになる。次フェーズでこれらのフィールドの活用が進んだ場合、`domain`と同じ「空→値」の継続的な穴埋め方針への格上げと、`maybe_fill_company_domain()`と同様のマージ時引き継ぎ関数の追加を合わせて検討する。

**★v0.8：`update_fields`と`auto_now`の注意**。`auto_now=True`のフィールドは、`update_fields`を指定した`save()`ではその一覧に含めない限り更新されない。本節だけでなく、**Company側に`mark_as_merged()`を実装する際も必ず`updated_at`を含める**こと（§2.5の`close_deal`は既に含めている）。

サブドメイン違い等で後から別ドメインのContactが紐づくケースは、`org_domain_name`が完全一致しない時点で自動リンクの対象にならず、会社名一致経由でpossible_midのレビューに必ず上がるため、静かな誤結合は起こらない。

## 6.6 Companyの手動アーカイブ（★v0.8で新設）

§7.6で`delete_company`をどのグループにも配らない方針を確定したため（物理削除の画面を作らないため）、**Companyを手動で`status=archived`にする動線を明示的に用意する必要がある**。用意しないと、廃業した会社・OCR誤認識由来のゴミCompanyを人が片付ける手段が一切なくなる。

- Company詳細画面に「アーカイブする」操作を置く。`permission_required = 'companies.change_company'`（標準の変更権限。§7.6の配布では`company_editor`／`company_admin`が保持）
- 実処理は`status`を`archived`に更新するのみ。`ActionLog.record()`で記録する
- **アーカイブしたCompanyは`link_contact_to_company()`の照合候補から自動的に外れる**（§6.5.4のステップ2で`status=active`に限定しているため）
- 復帰（`archived → active`）も同じ画面から可能とする。誤操作の取り消し手段がないと運用が硬直するため
- Contact・Dealが紐づいたままのCompanyもアーカイブ可能とする（禁止すると、廃業した会社を片付けられなくなるため）。紐づくレコードのFKはそのまま維持する

**★v0.9で明記：CompanyにはService層のレコード単位判定を設けない**。§7.1はDeal／Activityについて「View層の粗いゲート＋Service層のレコード単位判定」という二重防衛を掲げているが、Companyの手動アーカイブは`permission_required = 'companies.change_company'`（View層）のみで判定する。

**Companyは`owner`も所有者ベースの可視範囲制限も持たない**設計だからである（§7.5）。Deal／Activityで二重防衛が必要だったのは、「機能を使える人」と「このレコードを触れる人」が一致しないためだった。Companyは会社という共有マスタであり、`company_editor`が触れる会社と触れない会社を分ける概念が存在しない。判定するものがない以上、Service層に空の関数を置いても意味がない。

§6.5.4の自動アーカイブ（再照合による孤児Company）と本節の手動アーカイブは、同じ`status=archived`に収束する。

---

# 第7章 アクセス制御

## 7.0 判定関数の命名について（★v0.9で新設）

本章の判定関数は`can_view_deal`／`can_edit_deal`／`can_archive_deal`のように`can_*`を冠する。CLAUDE.md §10の命名規約の表には`can_*`という形が載っておらず（`is_*`／`has_*`が真偽値を返す関数として定義されている）、厳密には規約外である。

しかし既存の`mailings/services/permissions.py`が`can_view_campaign`を使っており、**アクセス制御の判定関数については`can_*`が既に事実上の標準になっている**。ここだけ`has_*`にすると、同じ役割の関数が2つの命名で並ぶことになり、かえって混乱する。

**`can_*`を規約の例外として明記し、本章では一貫して使用する。** コード君が「実装着手前の3点チェック」でこの不一致を報告してきた場合は、本節を根拠に続行してよい。あわせて、CLAUDE.md §10の規約表に`can_*`（アクセス制御の判定、副作用なし）を追記することを推奨する。

なお§6.5.4の`link_contact_to_company()`は、v0.8までの`find_or_link_company()`という名前が規約（`find_*`は読むが書かない）に正面から違反していたため、v0.9で改名した（§6.5.4参照）。

## 7.1 Deal：所有者ベース制御・編集・削除の権限境界

**カスタム権限の宣言**。`deals.view_all_deals`／`deals.edit_all_deals`はDjangoのデフォルト権限（add/change/delete/view）には含まれないため、`Deal.Meta.permissions`に明示的に宣言しないと`has_perm()`が常に`False`を返す。

**★v0.7で命名を確定**：既存の`contacts.edit_all_contacts`（Contact横断編集権限、既存実装で確認済み）と同じ命名規則に揃え、`change_all_deals`ではなく**`edit_all_deals`**とする。Campaign（`view_all_campaigns`のみ、編集の横断権限は持たず`change_campaign`をeditor全員に配る設計）とは異なり、Dealは所有者ベースの編集制限を持つため、Contactと同じ構造（標準`change_deal`＋横断編集`edit_all_deals`）を採用する。

```python
class Deal(models.Model):
    class Meta:
        permissions = [
            ('view_all_deals', '全ての案件を閲覧できる'),
            ('edit_all_deals', '全ての案件を編集できる'),
        ]
```

```python
# 閲覧：単体判定（詳細画面用）
def can_view_deal(user, deal):
    if user.has_perm('deals.view_all_deals'):
        return True
    if deal.owner_id == user.id:
        return True
    return deal.deal_users.filter(user=user).exists()

# 閲覧：QuerySet版（一覧画面用、N+1回避。Campaignのvisible_campaigns_forと同じパターン）
def visible_deals_for(user):
    """★v0.12で修正：dealuser → deal_users（§1.4.1で確定した related_name）。"""
    if user.has_perm('deals.view_all_deals'):
        return Deal.objects.all()
    return Deal.objects.filter(
        models.Q(owner=user) | models.Q(deal_users__user=user)
    ).distinct()  # DealUser経由のOR結合で行が重複するため distinct 必須

# 編集権限（owner本人・DealUser・edit_all_deals権限者が編集可能）
# ★v0.8で修正：change_deal 権限を AND 条件に追加（理由は後述）
def can_edit_deal(user, deal):
    if user.has_perm('deals.edit_all_deals'):
        return True
    if not user.has_perm('deals.change_deal'):   # ★v0.8で追加
        return False
    if deal.owner_id == user.id:
        return True
    return deal.deal_users.filter(user=user).exists()

# アーカイブ（論理削除）権限。DealUserは不可、owner本人か
# edit_all_deals権限者のみ。「関わっている人」と「削除できる人」を区別するため
def can_archive_deal(user, deal):
    if user.has_perm('deals.edit_all_deals'):
        return True
    if not user.has_perm('deals.change_deal'):   # ★v0.9で追加（can_edit_deal と同じ理由）
        return False
    return deal.owner_id == user.id
```

**★v0.9で追加：`can_archive_deal`にも`change_deal`のAND条件を入れる**。v0.8は`can_edit_deal`にこれを入れ、その理由まで説明しておきながら、`can_archive_deal`には適用していなかった。同じ原則が同じ理由で当てはまる。

具体的な穴は§2.6（owner付け替え）の運用と組み合わさる。**退職・異動でviewerロールに変わった元営業は、ownerの付け替えが済むまで自分がownerのDealを持ち続ける。** この間、Service層の関数が単体で呼ばれれば通ってしまう。View層の`change_deal`ゲートで止まるとはいえ、それはまさに「二重防衛の内側だけが破れている」状態そのものである。

**★v0.8で追加：`can_edit_deal`に`change_deal`のAND条件を入れる理由**。§7.6でviewerロールに`view_all_deals`を配らない方針を確定した結果、**viewerに特定の案件を見せる手段はDealUser（`observer`ロール）への登録のみ**となる。この運用が標準になると、v0.7の`can_edit_deal`は「DealUserであれば無条件に編集可」を返すため、**閲覧目的で登録したviewerに編集を許してしまう**。

現状はView層の`change_deal`ゲートで止まるが、Service層の関数が単体で呼ばれた瞬間に抜ける。二重防衛の内側だけが破れている状態は最も気づきにくいため、**判定関数自体を自己完結させる**。`can_edit_activity`（§7.3）も同型とする。閲覧側（`can_view_deal`）については、`view_deal`はDeal関連の全グループが保持しており分岐が生じないため、View層のゲートに委ねたままでよい。

**★v1.6で追加：`can_edit_deal`は`created_by`にフォールバックしない（意図的な非対称）**。`contacts.can_edit_contact`（`created_by_id == user.id or managed_by_id == user.id`）と見比べると、`can_edit_deal`は`owner`本人・`DealUser`登録者・`edit_all_deals`保持者のみを見ており、**Dealを最初に起票した人（`created_by`）というだけでは編集できない**。これはContactとの実装漏れではなく、意図的な設計である。

Dealは「今、誰が担当しているか」（`owner`）が業務上の実体であり、`created_by`は§2.6が明記する通り単なる起票記録（`owner`とは別フィールドで、付け替えても書き換わらない）に過ぎない。担当が異動・引き継ぎで外れた後も起票者だからという理由で編集権が残り続けると、**現在の担当ではない人物が案件を触れる**という実態と合わない状態になる。案件から完全に手を引いた元担当者に編集権を残したい場合は、`DealUser`に`observer`以外の適切な`role`で明示的に登録する（§2.9.5）ことで対応する。この点、Contactの`managed_by`ベースの判定とは別の結論を採っていることに注意する。

**★v0.8で確定：アーカイブ操作のView層ゲートは`change_deal`とする**。v0.7は`can_archive_deal`を新設したがView層の`permission_required`を定めておらず、どちらを選んでも矛盾が生じる状態だった。

- `delete_deal`にすると：`delete_deal`は`deal_admin`にしか配られないため、`can_archive_deal`の「owner本人は可」という分岐が**到達不能**になり、せっかく定義した権限境界が機能しない
- `change_deal`にすると：`delete_deal`／`delete_activity`／`delete_company`を要求するViewが仕様書のどこにも存在しなくなる

**後者を採る。** この3モデルはすべて論理削除（`is_archived`／`status=archived`）であり、物理削除の画面をそもそも作らないため、`delete_*`権限を要求する箇所が生じないのは設計として正しい。したがって**§7.6でこの3つの権限をどのグループにも配らない**（`cards.create_card`等で問題になっている死蔵権限を、新規に3つ再生産しないため）。誰がアーカイブできるかの絞り込みは`can_archive_deal()`が担う。

**一覧画面での`is_archived`除外について**：Deal・Activity一覧の既定表示は`is_archived=False`のもののみとする。この除外は`visible_deals_for`／`visible_activities_for`自体には含めず、**呼び出し側のView層で`.filter(is_archived=False)`を明示的に付与する**運用とする（アーカイブ済みDealをあえて見たい画面もあり得るため、権限判定関数の責務からは分離する）。

View層で`PermissionRequiredMixin`による機能レベルの粗いゲート、Service層で上記のレコード単位判定、という二重防衛はCampaignと同じ構成にする。

**★v0.11で追加：`archive_deal()`（実行関数）と画面導線**。`can_archive_deal()`はあくまで判定関数であり、実際に`is_archived`を立てて`ActionLog`に記録する実行関数が仕様書のどこにも定義されていなかった。§6.6でCompanyには手動アーカイブの動線を書いたのに、Deal（と次節のActivity）には書き忘れていた。判定関数だけがあってもコード君は「実際に案件を論理削除するボタン」を作り忘れる。

```python
def archive_deal(deal, user):
    """Dealをアーカイブする。呼び出し前に can_archive_deal(user, deal) の
    チェック（Service層）と、Viewの change_deal 権限（View層）が前提。"""
    with transaction.atomic():
        deal.is_archived = True
        deal.updated_by = user
        deal.save(update_fields=['is_archived', 'updated_by', 'updated_at'])
        ActionLog.record(
            user=user, action='deal_archived', content_object=deal,
            object_repr=deal.name,
        )
```

Deal詳細画面に「アーカイブする」アクションを置く。`permission_required = 'deals.change_deal'`（View層、§7.1の確定方針）。復帰（`is_archived=False`に戻す）も同じ画面から可能とする（誤操作の取り消し手段がないと運用が硬直するため、§6.6のCompanyと同じ考え方）。

## 7.2 AccessList本体：見送りの根拠

（v0.1と同じ。論点F/G未決着・UserGroup未実装・Department.descendants()のN+1未解消のため見送り）

## 7.3 Activity：横断権限＋実施者・同席者優先＋委譲

**カスタム権限の宣言**。`activities.view_all_activities`／`activities.edit_all_activities`（★v0.7で追加）も`Meta.permissions`への宣言が必要。

```python
class Activity(models.Model):
    class Meta:
        permissions = [
            ('view_all_activities', '全ての活動記録を閲覧できる'),
            ('edit_all_activities', '全ての活動記録を編集できる'),
        ]
```

**判定順序**：`view_all_activities`をDeal/Campaign委譲より後に置くと、**この権限を持っていてもDealに紐づく活動は見えない**（先にDealへ委譲されてしまうため）、という「権限名と実際の挙動が一致しない」問題がある。`view_all_activities`（「全部見たい」という横断権限）を**判定の最優先**にする。

```python
def can_view_activity(user, activity):
    if user.has_perm('activities.view_all_activities'):  # 横断権限は最優先
        return True
    if activity.user_id == user.id:  # 実施者本人は常に閲覧可
        return True
    if activity.activity_users.filter(user=user).exists():  # 同席者も常に閲覧可
        return True
    if activity.deal_id is not None:
        return can_view_deal(user, activity.deal)  # Dealの権限に委譲。将来ACL対応も自動
    if activity.campaign_id is not None:
        return activity.campaign.has_view_permission(user)  # Campaignの既存権限に委譲
    return False  # どちらの委譲先もなく、横断権限もない場合は不可


# 編集権限（実施者本人・作成者・同席者・edit_all_activities権限者が編集可能）
# ★v0.8で修正：change_activity のAND条件と created_by を追加
def can_edit_activity(user, activity):
    if user.has_perm('activities.edit_all_activities'):
        return True
    if not user.has_perm('activities.change_activity'):   # ★v0.8で追加（§7.1と同じ理由）
        return False
    if activity.user_id == user.id:
        return True
    if activity.created_by_id == user.id:                 # ★v0.8で追加（実施者が空の場合の救済）
        return True
    return activity.activity_users.filter(user=user).exists()


# アーカイブ（論理削除）権限。同席者は不可、実施者本人・作成者・
# edit_all_activities権限者のみ（Dealと同じ「関わっている人」と「削除できる人」の区別）
def can_archive_activity(user, activity):
    if user.has_perm('activities.edit_all_activities'):
        return True
    if not user.has_perm('activities.change_activity'):   # ★v0.9で追加（§7.1と同じ理由）
        return False
    if activity.created_by_id == user.id:                 # ★v0.8で追加
        return True
    return activity.user_id == user.id
```

**★v0.8で`created_by`を判定に加えた理由**。`Activity.user`（実施者）は`null=True, blank=True`のため、実施者を空のまま登録すると、v0.7の判定では**登録した本人が自分のレコードを編集もアーカイブもできなくなる**（`edit_all_activities`保持者しか触れない）。§3.1で追加した`created_by`をOR条件に加えることでこの穴を塞ぐ。

なお`visible_activities_for`（後述のQuerySet版）には`created_by`条件を**加えない**。閲覧については、実施者が空の活動を作成者が見られないという問題は`Q(user=user)`では起きるが、そもそも実施者を空にした活動は作成者自身が入力したものであり、閲覧目的では実務上の不都合が小さい。一方でここに条件を足すと`can_view_activity`との集合一致（§7.3の設計原則）を保つために単体判定側にも同じ分岐が必要になり、判定が複雑化する。**編集・アーカイブ側のみの救済に留める。**

**★v0.11で追加：`archive_activity()`（実行関数）と画面導線**。§7.1の`archive_deal()`と同じ理由で、Activity側にも実行関数が抜けていた。

```python
def archive_activity(activity, user):
    """Activityをアーカイブする。呼び出し前に can_archive_activity(user, activity) の
    チェック（Service層）と、Viewの change_activity 権限（View層）が前提。"""
    with transaction.atomic():
        activity.is_archived = True
        activity.save(update_fields=['is_archived', 'updated_at'])
        ActionLog.record(
            user=user, action='activity_archived', content_object=activity,
            object_repr=str(activity),
        )
```

Activity詳細画面（または日報一覧の行アクション）に「アーカイブする」を置く。`permission_required = 'activities.change_activity'`。**復帰は設けない**（Dealと異なり、Activityの誤登録は「上げ直す」方が自然であり、アーカイブ済みActivityを戻して再編集する運用は想定しないため）。誤ってアーカイブした場合は、`edit_all_activities`保持者が新規に登録し直す。

**一覧表示用のQuerySet版（`visible_activities_for`）**

日報画面（担当者×日付の一覧）で単体判定を1件ずつ呼ぶとN+1になるため、Dealの`visible_deals_for`と同様のQuerySet版を用意する。

`can_view_activity`と**完全に同一の集合**を返すよう、`view_all_deals`／`view_all_campaigns`保持者のQ条件を動的に追加する。

```python
def visible_activities_for(user):
    """can_view_activity() と同一の集合を返す。
    ★v0.12で修正：activityuser → activity_users、dealuser → deal_users
    （§1.4.1で確定した related_name。デフォルト名のまま残っていた）。"""
    if user.has_perm('activities.view_all_activities'):
        return Activity.objects.all()

    q = models.Q(user=user) | models.Q(activity_users__user=user)  # 実施者・同席者

    # Deal経由の認可委譲：can_view_deal と同一集合になるよう分岐
    if user.has_perm('deals.view_all_deals'):
        q |= models.Q(deal__isnull=False)
    else:
        q |= models.Q(deal__owner=user) | models.Q(deal__deal_users__user=user)

    # Campaign経由の認可委譲：Campaign.has_view_permission と同一集合になるよう分岐
    if user.has_perm('mailings.view_all_campaigns'):
        q |= models.Q(campaign__isnull=False)
    else:
        q |= models.Q(campaign__created_by=user)

    return Activity.objects.filter(q).distinct()  # 複数条件のOR結合で行が重複するため distinct 必須
```

`campaign__created_by=user`の部分は、Campaign本体の所有者判定ロジックをハードコピーしている点に注意する。Campaign側の判定ルールが将来変わった場合、ここも追随して変更する必要がある。

**設計判断の理由**：
- Dealに紐づく活動は、Dealの権限判定にそのまま委譲する。将来DealにACLが導入されても、Activity側は無改修で自動的に精緻化される（FG1のReport→ReportList委譲パターンと同型）
- Campaignに紐づく活動（キャンペーンのフォロー記録）は、既存の`Campaign.has_view_permission`（`view_all_campaigns`権限者は他人のキャンペーンも閲覧可）にそのまま委譲する。これにより、上長がキャンペーンのフォロー状況を確認したいというニーズは、新しい仕組みを作らずに満たされる
- 部署階層に基づく段階的な閲覧範囲（上長が部下の日報を見る、等）は、**今回は実装しない**。`Department.descendants()`のN+1が未解消であり、中途半端な部署ベースのロジックを今作ると、将来AccessListの平坦化キャッシュ方式に置き換える際に丸ごと破棄することになるため。どちらの委譲先もない社内活動（社内会議等）は、`view_all_activities`という単純な全社横断権限のみで判定する

## 7.4 Attachment：親オブジェクトへの委譲

```python
def can_view_attachment(user, attachment):
    if attachment.deal_id is not None:
        return can_view_deal(user, attachment.deal)
    return can_view_activity(user, attachment.activity)


# ★v0.8で追加：編集（メモの修正等）は親への委譲でよい
def can_edit_attachment(user, attachment):
    if attachment.deal_id is not None:
        return can_edit_deal(user, attachment.deal)
    return can_edit_activity(user, attachment.activity)


# ★v0.8で追加：削除は物理削除のため、親への単純委譲にはしない（§4.4.3）
def can_delete_attachment(user, attachment):
    if attachment.uploaded_by_id == user.id:      # アップロード者本人
        return True
    if attachment.deal_id is not None:
        if user.has_perm('deals.edit_all_deals'):
            return True
        return attachment.deal.owner_id == user.id          # 親の所有者本人
    if user.has_perm('activities.edit_all_activities'):
        return True
    # ★v0.9：created_by を追加。実施者が空の Activity に他人がアップロードした添付を、
    # その Activity を作った本人が消せない穴が残っていたため（§3.1・§7.3と同じ救済）
    return (attachment.activity.user_id == user.id
            or attachment.activity.created_by_id == user.id)
```

Attachmentは`owner`も所有者ベース制限も持たないため、`edit_all_*`に相当する専用の横断権限は設けない。閲覧・編集は親オブジェクトへの委譲とする。

**★v0.8：削除だけは親への単純委譲にしない**。`can_edit_deal`／`can_edit_activity`はDealUser／ActivityUser（同席者・閲覧者）にも真を返すため、削除まで委譲すると**商談に同席しただけの社員が他人のアップロードした見積書・契約書を物理削除できてしまう**。Attachmentは物理削除で復元手段がないため（§4.4.2）、削除できるのは「アップロード者本人・親の所有者本人・`edit_all_*`保持者」に限定する。これはDeal／Activityのアーカイブ権限（§7.1、§7.3）で採った「関わっている人」と「削除できる人」を区別する方針と一貫している。

**★v0.9で明記：`can_delete_attachment`だけ`delete_attachment`のAND条件を入れない理由**。`can_edit_deal`（§7.1）では「配布に依存した安全性」を否定して`change_deal`のANDを入れたのに、ここでは入れていない。扱いを変えているのは、**判定の構造そのものが違うから**である。

`can_edit_deal`がANDを必要としたのは、**DealUserという「関与しているが編集させたくない相手」（`observer`ロールのviewer）が判定条件の中に含まれている**ためだった。条件を満たす人の中に、権限を持たない人が混ざり得る構造になっていた。

一方`can_delete_attachment`の条件は「アップロード者本人」「親の所有者本人」「`edit_all_*`保持者」の3つで、**同席者・関与者を一切含まない**（§4.4.3でそう決めた）。自分でファイルを上げた人、案件のownerである人は、いずれも定義上その案件を編集できる立場である。したがって、条件を満たすが権限を持たない、という組み合わせが構造的に生じない。

その上でView層は`permission_required = 'attachments.delete_attachment'`を要求し（§4.4.3）、この権限は§7.6でviewerには配らない。二重防衛は成立している。

## 7.5 Company：merge_company権限

Companyは、Contact/Campaignと違って**所有者ベースの閲覧制限を設けない**（重複統合・レビューが中心機能で、Person/Tagに近い全社共有データという位置づけ）。そのため`view_all_companies`のような横断権限は不要で、標準CRUD権限だけで足りる。

一方、**Company統合（マージ実行）は独立した操作**として、`persons.merge_person`と同じ位置づけの例外権限を新設する。

```python
class Company(models.Model):
    class Meta:
        permissions = [
            ('merge_company', '会社の統合を実行できる'),
        ]
```

`merge_person`が`person_admin`・`person_editor`の**両方**に配られている（マージ操作自体は営業も通常業務として行える、`viewer`のみ持たない）前例に倣い、`merge_company`も`company_admin`・`company_editor`の両方に配る（Companyの判定条件はPersonより誤判定リスクが低いため、少なくとも同程度に緩めてよい）。

**★v0.8で明記：`company_editor`は`delete_company`を持たないが`merge_company`でCompanyを実質的に消せる**。これは`merge_person`の前例（`person_editor`は`delete_person`を持たないがマージは実行できる）に倣った**意図的な配分**である。マージは「2つのレコードが同一だと人が判断して統合する」操作であって、データを失う削除とは性質が異なり、日常の名寄せ業務として営業が行うことを想定している。後の権限見直しで「不整合だ」と誤って修正されないよう、意図的である旨をここに記録する。

**★v0.13で追加：CompanyDuplicateCandidateのレビュー操作も`merge_company`で統一する**。§7.6のグループ表にはCompany本体のCRUDと`merge_company`はあるが、`CompanyDuplicateCandidate`自体の標準権限（`view_companyduplicatecandidate`／`change_companyduplicatecandidate`）はどのグループにも配らない。**レビューキュー画面での操作（一覧閲覧・`different_company`への遷移を含む）は、すべて`companies.merge_company`で判定する。**

これは独自の判断ではなく、既存の`duplicates/views.py`の前例をそのまま踏襲している。既存の全View（一覧・レビュー・マージ実行）は、`duplicates.view_duplicatecandidate`のような専用権限を作らず、一貫して`persons.merge_person`／`persons.undo_merge`を使い回している。レビューキューでの判断（統合する／別会社として管理する）は、実質的にマージ機能の一部という位置づけである。Companyもこれに揃えることで、`CompanyDuplicateCandidate`という第10番目のモデル専用の権限を新たに作らずに済む。

## 7.6 PermissionGroup構成（★v0.7で新設、v0.8で配分を確定）

既存の16グループ（`person_*`／`card_*`／`contact_*`／`campaign_*`／`tag_*`／`user_admin`）に、以下9グループを追加する。命名・配分方針は既存の`contact_*`（0008で整備された、`_all_`系はadminのみ・editorはdeleteを持たない「綺麗な累積型」）に揃える。

**★v0.8で確定：`delete_deal`／`delete_activity`／`delete_company`は、どのグループにも配らない。** この3モデルはすべて論理削除（`is_archived`／`status=archived`）であり、物理削除の画面を作らないため、これらの権限を要求するViewが存在しない（§7.1）。配ると`cards.create_card`等と同じ死蔵権限を新規に3つ作ることになる。アーカイブ操作のView層ゲートは`change_deal`／`change_activity`／`change_company`とする。

| Group | Permission | Role |
|---|---|---|
| `deal_admin` | `view_deal`／`add_deal`／`change_deal`＋`view_all_deals`／`edit_all_deals`(独自)＋`view_attachment`／`add_attachment`／`change_attachment`／`delete_attachment` | admin |
| `deal_editor` | `view_deal`／`add_deal`／`change_deal`＋`view_attachment`／`add_attachment`／`change_attachment`／`delete_attachment` | sales |
| `deal_viewer` | `view_deal`＋`view_attachment` | viewer |
| `activity_admin` | `view_activity`／`add_activity`／`change_activity`＋`view_all_activities`／`edit_all_activities`(独自)＋`view_attachment`／`add_attachment`／`change_attachment`／`delete_attachment` | admin |
| `activity_editor` | `view_activity`／`add_activity`／`change_activity`＋`view_attachment`／`add_attachment`／`change_attachment`／`delete_attachment` | sales |
| `activity_viewer` | `view_activity`＋`view_attachment` | viewer |
| `company_admin` | `view_company`／`add_company`／`change_company`＋`merge_company`(独自) | admin |
| `company_editor` | `view_company`／`add_company`／`change_company`＋`merge_company`(独自) | sales |
| `company_viewer` | `view_company` | viewer |

**★v0.8で確定：viewerに`view_all_deals`／`view_all_activities`は配らない（方針決定）**。

ジット君のレビューは「viewerは`visible_deals_for`のどの条件（`view_all_deals`保持者／owner本人／DealUser）にも該当しないため、一覧が必ず0件になる」と指摘し、`view_all_*`の付与を推奨した。**この推奨は採らない。** 全案件・全活動を横断で見られる権限はadminに限定し、**viewerに見せたい案件はDealUser／ActivityUser（`observer`ロール、§2.9.5）として個別に登録する**方式を採る。

これは§5.3で既に宣言している「ACL上は見えない設定でも、関与者として登録されていれば個別に見える」という2層構造そのものであり、将来AccessListを導入した後も同じ形で機能し続ける。「閲覧専用の人には、関係する案件だけを見せる」という運用は業務要件としても自然である。

**★v0.9で明記：`observer`ロールの効力の限界**。`DealUser.role`／`ActivityUser.role`の`observer`は、**どの判定関数からも参照されない**。`can_edit_deal`はDealUserであるかどうかしか見ず、roleの値は見ないためである。

したがって「閲覧目的の登録」という意図が実際に効くのは、**`change_deal`権限を持たないviewerロールの相手に限られる**。salesロールのユーザーを`observer`として登録しても、そのユーザーは`change_deal`を持っているため編集できてしまう。

これは意図的な設計である。roleの値で編集可否を分岐させると、権限がPermissionGroupとroleの2箇所に分散し、どちらが優先かという新しい問題が生まれる。`observer`は**業務データとしての記録**（この人は関与者ではなく閲覧目的で登録されている）であって、権限の制御軸ではない。後で「observerにしたのに編集された」という話にならないよう、ここに明記する。

**この結果、DealUserに登録されていないviewerのDeal一覧は空になるが、これは仕様通りである。** ただし原因が画面から読み取れないと問い合わせの原因になるため、Deal／Activity一覧の空状態には「**関与者として登録されている案件がありません**」という文言を表示する（FG2 Human Interface Guidelinesの空状態パターンに従う。詳細は§9.3のUI設計で扱う）。

**Attachment専用グループは作らない**。Attachmentは独立した「誰のものか」という概念を持たず（§7.4）、権限は常に親（Deal/Activity）に委譲されるため、所有者ベースの横断権限を必要とする構造がない。標準権限を上表の通り`deal_*`／`activity_*`の各グループに含める形で配布する。

**★v0.8：viewerにも`view_attachment`を配る**。§4.3で`ProtectedFileDownloadView`に`PermissionRequiredMixin`（`attachments.view_attachment`）を追加したため、配らないと**DealUserとして登録されたviewerがDeal詳細は開けるのに添付だけ403になる**。オブジェクト単位の判定は`can_view_attachment()`が親へ委譲して行うため、この権限を配っても閲覧範囲は広がらない。`add`／`change`／`delete_attachment`はviewerには配らない。

**★v0.8で追加：マイグレーションの実行タイミング（`post_migrate`問題）**。新設9グループには、新規モデルのカスタム権限（`view_all_deals`／`edit_all_deals`／`view_all_activities`／`edit_all_activities`／`merge_company`）が含まれる。Djangoでは、モデルのパーミッション（`auth.Permission`レコード）は**マイグレーション実行中ではなく、全マイグレーション完了後の`post_migrate`シグナルで初めてDBに生成される**。データマイグレーション内で素朴に`group.permissions.add(...)`を書くと、`Permission.DoesNotExist`（あるいは対象0件で静かに何も追加されない）という形で確実に失敗する。

ジェミニ君のレビューは「独立した管理コマンド（`setup_default_groups`等）に分離する」ことを推奨したが、**この推奨は採らない。** FG2では既存の`0008`（`contact_*`新設時）がデータマイグレーションでの増分`.add()`方式で確立されており、ここだけ別方式にすると初期データ投入の入口が2つに分かれ、どちらを実行すべきかが環境ごとに曖昧になる。

**採用する方式**：既存`0008`と同じデータマイグレーション方式を維持したまま、**マイグレーション関数の冒頭で`create_permissions()`を明示的に呼び、Permissionレコードを先に生成する**。これがpost_migrate待ちを回避する定石であり、既存方式との一貫性も保てる。

**★v0.9で修正：既存6本とまったく同じコードにする（重要）**。v0.8のスニペットは既存実装と2点で違っており、うち1つは副作用がある。

- **`apps=apps`を渡していなかった**。この引数はマイグレーション内でhistorical modelレジストリを使わせるためのもので、既存の`accounts/migrations/0004`〜`0010`はすべて明示的に渡している。省くとグローバルの実モデルレジストリが使われ、マイグレーション途中の状態と食い違う
- **`app_config.models_module = True` → `None`と書き換えていた（★副作用あり）**。これはテスト用のダミーapp_configを通すための小技で、実app_configには不要である。そして`models_module = None`に戻すと、`create_permissions`は冒頭の`if not app_config.models_module: return`で早期returnする。つまり、**その後の`post_migrate`が同じapp_configを受け取っても何もせず、この4アプリについてDjango標準の権限生成が丸ごとスキップされる状態を作り込む**。今回は自分たちで先に生成しているので結果的に足りるが、**後からモデルやフィールドを追加したときに「権限が作られない」という追跡困難な形で表面化する**

既存6本と同じ形に統一し、あわせてマルチDB・テストランナーでの誤作動を防ぐ`using`を渡す。

```python
from django.apps import apps as django_apps
from django.contrib.auth.management import create_permissions


def ensure_permissions(apps, schema_editor):
    """post_migrate を待たずに Permission レコードを先に生成する。
    accounts/migrations/0004〜0010 と同一の書き方。models_module には触らない。
    """
    for app_config in django_apps.get_app_configs():
        create_permissions(
            app_config,
            apps=apps,                                   # historical model レジストリを使う
            using=schema_editor.connection.alias,        # ★v0.9：対象DBを明示
            verbosity=0,
        )


def create_groups(apps, schema_editor):
    ensure_permissions(apps, schema_editor)
    # 以降、既存0008と同じ増分 .add() 方式でGroupとPermissionを紐づける
```

**`dependencies`の書き方（★v0.9で明記）**：本マイグレーションは`accounts`アプリに置くが、4アプリの初期マイグレーションが先に適用されていないとPermissionを作れない。既存の`0008`が`("cards", "0003_...")`のように**実番号で固定**し、コメントに「先に走ると`create_permissions`が物理化できず`get`が落ちる」と理由まで書いているのと同じ形にする。**先に4アプリの初期マイグレーションを作成し、その実番号を`accounts`側の`dependencies`に固定する**という作業順序も含めて指示書に記載する。

**冪等性**：同じマイグレーションが複数環境で再実行されても壊れないよう、Group取得は`get_or_create()`、Permission付与は`.add()`（既に持っていても無害）を使う。逆操作（`reverse_code`）ではGroupを削除する。

**`default_groups`への追加**：既存の`0008`（contact_*新設時）と同じ「増分`.add()`」方式を踏襲する。

| Role | 追加するGroup |
|---|---|
| `admin` | `deal_admin`／`activity_admin`／`company_admin` |
| `sales` | `deal_editor`／`activity_editor`／`company_editor` |
| `viewer` | `deal_viewer`／`activity_viewer`／`company_viewer` |

**新規実装時の留意点（既存調査で判明した反面教師）**：既存実装調査で、`persons.change_person`・`mailings.view_unsubscribe`が「Viewで要求されているのにどのGroupにも配布されておらず、実質superuser専用になっている」という既存の不具合が見つかっている（Deal/Activityとは無関係の別課題だが、同じ「View要求権限と初期データの食い違い」を今回のGroup新設で繰り返さないよう、Viewが要求する全ての`permission_required`が必ずいずれかのGroupに含まれているか、実装後に確認すること）。

## 7.6.1 Django adminによる保護迂回の防止（★v0.17で新設・重要）

本仕様書はここまで、重要な変更操作を`change_deal_stage()`／`close_deal()`（§2.5）・`archive_deal()`／`archive_activity()`（§7.1、§7.3）・`reassign_deal_owner()`／`reassign_deal_primary_person()`（§2.6、§2.6.1）・`execute_company_merge()`（§6.5.5）という**専用のService関数に必ず経由させる**ことで、ActionLog記録漏れや関連レコードの不整合を防いできた。

しかし、この保護は**すべてView層のフォーム構成に依存している**（例：Deal編集フォームから`owner`を除外する、§2.6）。既存の全アプリ（`persons`／`cards`／`contacts`／`tags`／`mailings`／`duplicates`／`accounts`／`actionlogs`）は`admin.py`を持っており、新設4アプリにも`admin.py`が登録されるのはほぼ確実である。**Django admin（`ModelAdmin`）はデフォルトで全フィールドを直接編集可能にするため、`readonly_fields`等で明示的に塞がない限り、admin画面はこれらの保護を丸ごと迂回する。**

具体的に、admin経由で素通りしてしまう箇所と実害は以下の通り。

| フィールド | 迂回すると何が起きるか |
|---|---|
| `Deal.owner` | `reassign_deal_owner()`を通さず直接変更。新ownerが既に`DealUser`でも削除処理が走らないため、§5.1の`DealUser.clean()`に阻まれて保存自体ができなくなる |
| `DealPerson.is_primary`（**★v1.7で追加、★v1.8でリスクを追記**） | ①`DealPersonAdmin`（または`DealAdmin`のインライン）経由で`is_primary`を直接トグルすると、`reassign_deal_primary_person()`を迂回し、旧主担当の`is_primary`解除とActionLog記録が行われないまま新主担当が立つ。§2.6.1の部分ユニーク制約（`unique_deal_primary_person`）自体は違反を防ぐため最悪の不整合（複数主担当）は起きないが、監査ログの欠落は`Deal.owner`と同じ問題として扱う。②**（★v1.8で追加）**個別削除・一括削除（「選択した項目を削除」）で主担当の行自体を削除すると、`delete_deal_person()`（§2.6.1）のガードを迂回し、Dealが相手方主担当0名の状態に静かに陥る |
| `Deal.is_archived` | `archive_deal()`を通さず直接トグル。ActionLogに記録が残らない |
| `Activity.is_archived` | 同上（`archive_activity()`を迂回） |
| `Company.status`／`Company.merged_into` | `execute_company_merge()`を通さず直接`status='merged'`にできる。Contact／DealのFK付け替え・`CompanyDuplicateCandidate`の後始末・ActionLog記録のすべてが起きないまま、**Companyだけがmerged状態になり、Contact/Dealは死んだはずのCompanyを指したまま残る**。§6.5.4のプレフィルタ（`status=active`限定）や§6.5.5の一括マージが前提とする整合性がここで壊れる |
| `Attachment`の削除 | `delete_attachment()`（§4.4.2）を通さずadmin画面から直接削除できる。`post_delete`シグナルにより**実ファイルは消える**（§4.4.2でシグナルに一本化済みのため、孤児ファイルは残らない）が、**`ActionLog`記録は`delete_attachment()`の中にしか書かれていない**ため、「誰が・いつ、どの添付を消したか」が監査ログに一切残らない。**★v1.3で仕様書本体に格上げ**：この項目はv1.0時点で§9.2（実装指示書向け申し送り）にのみ記載されており、他の5フィールドと異なり仕様書本体（本節）に反映されていなかった。同じ「Service関数を経由すべき重要操作をadminが迂回できる」性質の問題であるため、扱いを統一する |

**`DealAdmin`／`ActivityAdmin`／`CompanyAdmin`では、上記フィールドを`readonly_fields`に指定する。`AttachmentAdmin`では削除操作自体を禁止する（`readonly_fields`では「削除」というアクションは制御できないため、`has_delete_permission()`を使う）。** 変更・削除が必要な場合は、admin画面上からではなく、通常の業務画面（専用エンドポイント）を使う。

**★v0.18で修正：静的な`readonly_fields`ではなく`get_readonly_fields()`を使う（重要）**。Djangoの`readonly_fields`はクラス属性として静的リストで指定すると、**「追加（Add）」フォームと「変更（Change）」フォームの両方に同じリストが適用される**。`Company.status`はモデル側にデフォルト値がなければ、常にreadonlyにすると§6.6が言及する「Company新規作成画面から人が作る」経路がadmin側では詰まる（`Company.status`のデフォルト値については後述、§6.2で対応済み）。**★v1.7の注記**：v1.6までは`Deal.primary_person`が必須項目（デフォルト値なし）だったため、この節は`Deal`についても同じ懸念（新規作成不可になる罠）を説明していたが、`primary_person`廃止（§2.1、§2.6.1）に伴い`Deal`本体には必須FKが残っていない。ただし同じ`get_readonly_fields()`のイディオム自体は`owner`にも当てはまるため、以下のパターンは維持する。

**新規作成時（`obj is None`）は制限せず、変更時のみreadonlyにする**、Djangoの標準的なイディオムに直す。

```python
# deals/admin.py
class DealAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj is None:  # 追加時は制限しない
            return []
        return ['owner', 'is_archived']
    # 表示はするが変更時は編集させない。付け替え・アーカイブは専用画面から行う

# activities/admin.py
class ActivityAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ['is_archived']

# companies/admin.py
class CompanyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ['status', 'merged_into']

# attachments/admin.py
class AttachmentAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False  # 削除は専用画面（delete_attachment()経由）からのみ行う。
                       # ActionLog記録をシグナル側に持たせられないため、
                       # admin側の削除操作自体を封じる（readonly_fieldsでは
                       # 「削除」というアクション自体は制御できないため）。
```

**★v1.8で追加：`DealPersonAdmin`のコード例が欠落していた（重要）**。上記の表（`DealPerson.is_primary`の行）は「admin経由の直接トグルはActionLog記録漏れを起こす」というリスクにしか触れておらず、コード例も他の4モデル（Deal／Activity／Company／Attachment）は示されているのに`DealPersonAdmin`だけが実在しなかった。さらに、この表の説明は「トグル」の危険性しか書いておらず、**「admin画面の通常の削除操作（個別削除、または一覧の『選択した項目を削除』の一括操作）で、主担当の`DealPerson`の行自体を削除できてしまう」というリスクに一言も触れていなかった**（レビューで判明）。これは§2.6.1で`delete_deal_person()`を新設した動機（サブフォームの通常削除で主担当が消せる）と全く同じ性質の穴が、admin経由でも成立してしまうことを意味する。`delete_deal_person()`はView層の呼び出し経路にしかガードを掛けておらず、admin側の`.delete()`は素通りする。

`DealPerson`は他の4モデルと異なり、「行全体の削除」自体は通常運用でも起こり得る（同席者・関係者を整理する操作）。そのため`AttachmentAdmin`のように削除操作を丸ごと禁止するのではなく、**`is_primary=True`の行だけ削除を拒否する**、条件付きの保護が必要になる。トグル防止（`readonly_fields`）と削除防止（`has_delete_permission()`に加え、一括削除は個別チェックを経由しないため`delete_queryset()`／`delete_model()`のオーバーライドも必須）の両方を組み込む。

```python
# deals/admin.py
class DealPersonAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return ['is_primary']  # 常にreadonly。主担当の変更は
                                 # reassign_deal_primary_person()経由のみ

    def has_delete_permission(self, request, obj=None):
        # 個別の削除確認画面・行の削除ボタンの表示可否（obj指定時のみ判定）
        if obj is not None and obj.is_primary:
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        if obj.is_primary:
            raise PermissionDenied('主担当の相手方は削除できません。')
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # ★重要：一覧の「選択した項目を削除」（一括削除）は
        # has_delete_permission(obj=...)を1件ずつ経由しないため、
        # queryset側でも主担当行の混入を確実に弾く必要がある。
        if queryset.filter(is_primary=True).exists():
            raise PermissionDenied(
                '選択項目に主担当の相手方が含まれています。個別に確認してください。'
            )
        super().delete_queryset(request, queryset)


# ★v1.10で修正：DealAdminのインラインとしてDealPersonを登録する場合、
# 行単位の削除拒否は狙わず、インライン全体の削除を禁止する（後述の理由）。
class DealPersonInline(admin.TabularInline):
    model = DealPerson
    can_delete = False  # 削除はDealPersonAdmin（単体登録）または
                         # DealDeletePersonView（delete_deal_person()経由）に一本化

    def get_readonly_fields(self, request, obj=None):
        return ['is_primary']
```

**★v1.10で修正：`DealPersonInline.has_delete_permission()`は`InlineModelAdmin`のAPI仕様上、行単位の削除拒否には使えない（重要、レビューで判明）**。上記の当初案は`DealPersonAdmin`（単体登録）の`has_delete_permission(request, obj=None)`と同じ書き方をインラインにもコピーしていたが、両者の`obj`の意味は異なる。**`InlineModelAdmin.has_delete_permission()`に渡される`obj`は、インラインの各行（`DealPerson`）ではなく、編集中の親オブジェクト（`Deal`）である。** これは`get_formset(request, obj)`が「このDealに対してインライン全体の削除チェックボックスを一括で出してよいか」を判定するために呼ぶ設計であり、行単位の削除可否を判定するフックはインラインには存在しない。当初案の`obj.is_primary`は、`obj`が`Deal`インスタンスであるため`AttributeError: 'Deal' object has no attribute 'is_primary'`を必ず起こし、`DealPerson`をインラインとして組み込んだ`DealAdmin`の変更画面を開いた瞬間にクラッシュする。

対策として、**インライン側での行単位の削除制御そのものを諦め、`can_delete = False`でインラインからの削除を丸ごと禁止する**方針に改めた。削除は`DealPersonAdmin`（単体登録、正しく機能する）または本来の業務画面（`DealDeletePersonView`、`delete_deal_person()`経由）に一本化する。「インラインから主担当以外の参加者だけ直接削除したい」という利便性よりも、admin経由の事故防止を優先する判断である（行単位の制御にこだわるなら`BaseInlineFormSet.clean()`をカスタマイズする方法もあるが、`AttachmentAdmin`が採用した「admin経由の削除は塞ぎ、専用経路に一本化する」という本仕様書の既存パターンと揃える方が実装コストに見合う）。

これは「admin登録の詳細（`autocomplete_fields`等）」という実装段取りの粒度とは性格が異なり、本仕様書がここまで積み上げてきた業務ルールの保護範囲そのものに関わるため、実装指示書側の申し送りに埋もれさせず、本節として仕様書側に明記する。

## 7.7 次フェーズ：Person閲覧制限

（v0.1と同じ）

## 7.8 触らないもの

（v0.1と同じ。Campaign／MailingList／Tagは今回一切改修しない）

---

# 第8章 Personマージ・Companyマージとの整合性

## 8.1 FK付け替えの方針一覧

| モデル | マージ時の挙動 | 理由 |
|---|---|---|
| Contact.person | 実際に付け替え（`transfer_contacts_to()`、既存） | 生きているマスタデータ |
| Contact.company | 実際に付け替え（`transfer_contacts_to_company()`、§6.5.5） | 同上 |
| DealPerson.person（**★v1.7**：`is_primary=True`の相手方主担当を含む）／Activity（ActivityPerson経由） | **付け替えない**。読み取り時解決 | 進行中の商談だが、Personには**復元機能がある**ため、付け替えると復元時に戻す処理（`previous_person`追加）が必要になり複雑化する |
| **Deal.company** | **実際に付け替える**（v0.2で方針変更） | Companyには**復元機能がない**（§6.5.5で決定済み）ため、Person側で付け替えを避けた根拠（復元との相性）が当てはまらない。またCompanyを新設した目的（§6.1、会社軸集計）は、付け替えないと`GROUP BY`で表現できず果たせないため、付け替える方が合理的 |

この非対称（Person経由は読み取り時解決、Company経由は実際に付け替え）は「復元機能の有無」という明確な基準で説明できる。

## 8.2 読み取り時の解決方法（Person側のみ）

**Person→そのユニット全体のDeal/Activityを見せる**（Person詳細画面用）：既存の`get_same_person_unit(person)`を使う。

**Deal→現在生きているPersonを1件たどる**（Deal詳細・一覧画面用）：**既存の`Person.get_surviving_person()`（`persons/models.py`に実装済み、循環検知込み）を使う。新設しない。**

```python
# 使用イメージ（新設不要、既存メソッドをそのまま使う）
# ★v1.7：Deal.primary_person廃止に伴い、主担当DealPerson経由でたどる
primary_dp = deal.deal_persons.select_related('person__merged_into').get(is_primary=True)
primary_dp.person.get_surviving_person()
```

一覧画面のN+1対策：`Deal.objects.prefetch_related(models.Prefetch('deal_persons', queryset=DealPerson.objects.filter(is_primary=True).select_related('person__merged_into')))`。

**★v0.8で明記、★v1.7でDealPerson経由に読み替え：多段マージ時の追加クエリ（既知の許容事項）**。`select_related('person__merged_into')`がカバーするのは1ホップ分のみであり、A→B→Cのような多段マージが存在する場合、`get_surviving_person()`の2ホップ目以降で追加クエリが発生する。§3.5.2のフォロー漏れ検出も同じ性質を持つ。実運用でPersonマージが3段以上連鎖するケースは稀であり、対策するには既存の共有メソッド`get_surviving_person()`の改修（あるいは生存Person IDの非正規化キャッシュ）が必要になるため、**本フェーズでは既知の許容事項として受容する**。

## 8.3 復元時の扱い

Deal/ActivityのFK（Person経由）はそもそも変更していないため、Person復元時に戻す処理は不要。ただし「マージされている間に、Company統合等の独立した変更が起きていた場合」、Person復元はその変更までは巻き戻さない（TagAssignmentと同型の、既知の許容リスクとして受容）。

---

# 第9章 確定事項・未確定事項

## 9.1 確定事項

**★v1.10で新たに確定した事項**：`DealPersonInline.has_delete_permission()`が`InlineModelAdmin`のAPI誤用（`obj`は親Dealであり子DealPersonではない）でクラッシュする欠陥を修正。`DealPersonInline`は`can_delete = False`でインライン全体の削除を禁止し、削除は`DealPersonAdmin`（単体登録）または`DealDeletePersonView`に一本化する方針に変更（§7.6.1）。

**★v1.9で新たに確定した事項**：`DealPersonAdmin`（および`DealAdmin`のインライン）に、`is_primary`のトグル防止（`readonly_fields`）と削除防止（`has_delete_permission()`＋一括削除用の`delete_queryset()`）を追加（§7.6.1）。`DealForm.Meta.fields`への`primary_person`混入によるFieldErrorと、`DealCreateView.form_valid()`での`deal.save()`二重呼び出しを、実装指示書向けの申し送りに追記（§9.2）。

**★v1.8で新たに確定した事項**：`DealCreateView.form_valid()`が`create_deal_with_primary_person()`を実際に呼ぶよう修正し、Deal作成フォームでの相手方選択必須化を実装可能な形にした（CreateViewの2段構え規約はDealのみ例外、§9.2）。`delete_deal_person()`を新設し、主担当DealPersonの削除ガードを追加（§2.6.1）。`Company.merged_into`の`on_delete=SET_NULL`を明記（§6.2）。§2.4の存在しない画面遷移への言及を削除（§2.4）。

**★v1.7で新たに確定した事項**：`Deal.primary_person`（本体の単一FK）を廃止し、`DealPerson.is_primary`（部分ユニーク制約付き）に一本化。付け替え関数`reassign_deal_primary_person()`をDealPerson基準に全面改訂。`Deal.owner`の表示ラベルを「案件オーナー」に変更（`DealUser.role=primary`の表示ラベル「主担当」との衝突を解消）。データ移行は不要（実運用未投入のため、開発DB削除で対応）。詳細は§2.1、§2.6、§2.6.1、§5.1、§5.2、§7.6.1、§9.2を参照。

**★v1.6で新たに確定した事項**：Step 1実装レビューで`DealPerson.is_primary`が独自追加された事故を受け、当初は`DealPerson`／`DealUser`への主担当フラグ追加を禁止する方針を明記したが、この方針は★v1.7で撤回・上書きされている（最新版の§5.2を参照）。`can_edit_deal`が`created_by`にフォールバックしない設計（Contactとの意図的な非対称）を§7.1に明記。

**★v1.5で新たに確定した事項**：§9.2に「引数の多い関数はキーワード引数で呼ぶこと」を追加（仕様書本体のロジックに変更なし）。

**★v1.3で新たに確定した事項**：URLの配点を100点に引き上げドメインも100点に統一。`normalize_website()`を新設しURLをドメイン+パス最初のセグメントに正規化して比較。ランク判定の必須条件を「会社名 AND (ドメイン OR URL)」に一般化。`possible_mid`／`possible_low`の判定方法をスコア合計ベースに統合。

**★v1.2で新たに確定した事項**：`calculate_company_score()`を生の値10個を受け取る形に戻しContact対Company照合の欠陥を修正。`phone`／`address`／`website`の説明を「新規作成時コピーのみ」に修正し`create_company_from_contact()`を新設。

**★v1.1で新たに確定した事項**：Companyに`phone`／`address`／`website`を追加、重複検出スコアを5項目（120/100/20/20/20点）に拡張、ランク判定を4段階（`possible_low`新設）のハイブリッド判定に変更。

**★v1.0で新たに確定した事項**：`archive_company_if_orphaned()`の実体を新設（19版を通じて未定義だった）。`ActionLogAction`定数クラスはFG2に実在しないため作らない旨を明記。admin.py登録時のチェックリスト（Service関数を経由すべき操作の塞ぎ忘れ防止）を集約。

**★v0.9で新たに確定した事項**：主キーはUUID（新設9モデル全部）／全FKの`related_name`命名表と、それに合わせた§3・§7のコード全面書き換え／`find_or_link_company()`→`link_contact_to_company()`への改名と`can_*`の規約例外明記／`Meta.ordering`の既定／`Deal.company`の自動補完ルール（作成時のみ、追随なし）／Dealアーカイブ時にActivityは連鎖させない／`expected_value()`のNone扱い（金額・確度未入力は母数から外し件数を別表示）／「最終接触日」のSubquery+OuterRef方式／`Activity.occurred_at`の`clean()`（`timezone.now()`比較）／`upload_to`のUUID化／添付のサイズ上限15MB・実行可能ファイル拒否／`get_or_create_candidate_pair()`（IntegrityError回避）とスコアの2回計算／`execute_company_merge()`の入力検証（自己参照ループ・status・件数上限）／`link_contact_to_company()`は`update()`を使わない／`Company.created_by`は自動作成時NULL／添付メモの編集動線／`ProtectedFileDownloadView`の権限をレジストリ化／添付削除時のActionLogに親コンテキストと`object_repr`／`can_archive_deal`・`can_archive_activity`への`change_*`AND条件／`can_delete_attachment`への`Activity.created_by`／`create_permissions()`を既存6本と同一形に修正（`post_migrate`の権限生成が止まる問題）／依存図に`deals → mailings`／フォロー漏れ検出でアーカイブ済みPersonは除外せず表示とフィルタで扱う／`observer`ロールの効力の限界・`can_delete_attachment`にANDを入れない理由・Companyに二重防衛を設けない理由の明文化。

**★v0.19で新たに確定した事項**：Attachmentの実ファイル削除を`post_delete`シグナル（`transaction.on_commit`併用）に一本化し、admin削除・CASCADE削除でも孤児ファイルが残らないようにした。実装指示書向けの申し送り事項（`update_or_create`、ページネーションのGETパラメータ引き継ぎ、`auto_now_add`／`auto_now`明示）を追加。

**★v0.18で新たに確定した事項**：`readonly_fields`を`get_readonly_fields()`に修正しDeal/Company新規作成の詰まりを解消／`Company.status`に`default=`を追加／`mark_as_different_company()`を新設し`CompanyDuplicateCandidate`の監査証跡の欠落を解消／`execute_company_merge()`の`.update()`に`reviewed_by`・`reviewed_at`を追加。

**★v0.17で新たに確定した事項**：Django admin経由の保護迂回を防ぐため、`Deal.owner`／`Deal.primary_person`／`Deal.is_archived`／`Activity.is_archived`／`Company.status`／`Company.merged_into`を`readonly_fields`にする方針を新設（§7.6.1）。

**★v0.16で新たに確定した事項**：`Deal.company`自動補完の実現方式をバックエンドの`form_valid`処理に確定し、動的UIを持たせない方針を明記。

**★v0.15で新たに確定した事項**：Deal編集フォームから`owner`・`primary_person`を除外し、変更は専用関数経由に限定することを確定／`primary_person`付け替え用の`can_reassign_deal_primary_person()`・`reassign_deal_primary_person()`を新設。

**★v0.14で新たに確定した事項**：`DealPerson.clean()`／`DealUser.clean()`／`ActivityUser.clean()`を新設し、§5.2が宣言していた「本体の単一FKとの二重登録禁止」を実際にコード化／`reassign_deal_owner()`にDealUser削除処理を統合し担当を一本化／実装指示書向けの申し送り事項（既存ファイル事前読み込み・`app_name`・`Subquery`の`output_field`・テンプレート作り込み禁止・aware datetime・段階的プロンプト）を追加。

**★v0.13で新たに確定した事項**：`Contact.company`を文字列参照に変更し`contacts ↔ companies`間の循環importを解消／§1.2の依存図に`contacts → companies`を追記し、アプリをまたぐFKは文字列参照で書く原則を明記／`can_reassign_deal_owner()`・`reassign_deal_owner()`を新設しowner付け替えの権限の穴を塞ぐ／CompanyDuplicateCandidateのレビュー操作は`companies.merge_company`で統一。

**★v0.12で新たに確定した事項**：`visible_deals_for()`・`visible_activities_for()`に残っていた`related_name`の書き換え漏れ（`dealuser`→`deal_users`、`activityuser`→`activity_users`）を修正し、文書全体を再検索して他の取りこぼしがないことを確認／付録A.7に`archive_deal()`・`archive_activity()`・`get_companies_confirmed_as_different()`を追加。

**★v0.11で新たに確定した事項**：`get_companies_confirmed_as_different()`の適用箇所をContactのリンク先探索から、Company対Companyのペアリング（exact_match複数候補・一括マージ対象選択）に修正／DealPerson・DealUser・ActivityPerson・ActivityUserの追加・削除に`can_edit_deal`・`can_edit_activity`の権限ゲートを追加／`archive_deal()`・`archive_activity()`の実行関数と画面導線を新設／Companyマージ時のActionLog紐付け先を確認事項として明記／一覧テンプレートでの`get_FOO_display()`徹底を申し送りに追加。

**★v0.10で新たに確定した事項**：Userへの`related_name`を既存規則（監査系は`+`、業務的な逆引きが要るものだけ命名）に合わせて修正／`get_companies_confirmed_as_different()`を新設し、別会社判定済みペアが再照合のたびに湧く問題を解消／`attachment_upload_to`の定義順序とimportの誤りを修正／`execute_company_merge()`の検証順序を0件チェック優先に並べ替え／`MODEL_REGISTRY`参照を`get_registry_entry()`に集約／`Activity.__str__`のラベル展開と、DealPerson・DealUser・ActivityPerson・ActivityUser・CompanyDuplicateCandidateの`__str__`を追加／モデル数の記載を9に統一。

**★v0.8で確定した事項（再掲）**：選択肢（TextChoices）の実体全10フィールド分（`deal_type`3値・`lead_source`8値・`activity_type`・`direction`・`PersonRole`・`UserRole`（`observer`追加）・`Company.Status`・`Rank`・`ReviewStatus`、コード内固定、モデル内部クラス配置）／`Deal.source_campaign`の新設と`lead_source`との整合ルール（片方向のみ強制）／`Activity.created_by`の追加と編集・アーカイブ判定への反映／値域バリデーション（`amount`≧0・`probability`0〜100・`closed_at`未来日禁止）／`FileField(storage=...)`のcallable化（マイグレーションへの絶対パス焼き込み回避）／Attachmentの物理削除・コミット後の実ファイル削除・アップロード/削除画面・削除の権限境界（同席者は削除不可）・`can_edit_attachment`／`can_delete_attachment`／viewerロールの可視範囲（`view_all_*`は配らず、DealUser/ActivityUserの`observer`で個別に開ける）／`can_edit_deal`・`can_edit_activity`への`change_*`AND条件／アーカイブのView層ゲートは`change_*`、`delete_deal`/`delete_activity`/`delete_company`は配らない／`ContactUpdateView`での`old_company`退避の実行順序／フォロー漏れ検出の`is_archived`除外と`select_related`／PermissionGroup投入時の`create_permissions()`明示呼び出し／Companyの手動アーカイブ動線／一括マージViewの`merge_company`要求／`ProtectedFileDownloadView`の`PermissionRequiredMixin`／`update_fields`への`updated_at`／多段マージのN+1は既知の許容事項として受容。

**v0.7までに確定済みの事項（再掲）**：Deal本体全フィールド（8値ステージ・default・全CharFieldにmax_length・closed_atのblank=True込み、クローズ遷移は`close_deal()`専用関数経由・transaction.atomic・updated_by更新込み）・確度独立入力・Deal/Project分離・Activity（personフィールド廃止・ActivityPerson一本化、campaign連携追加、null/blank指定込み、日報一覧のデフォルトフィルタと権限フィルタの合成順序）・Attachment（独立アプリ、storage明記）・DealPerson/DealUser/ActivityPerson/ActivityUser全設計（UniqueConstraint・max_length込み）・Company全設計（スコア表簡素化・フリーメール除外・自動リンクexact_matchのみ・緩和案不採用・status=active絞り込み・domain一方向穴埋め＋マージ時引き継ぎ・exact_match複数候補時は最古優先＋1対1ペアリング・部分ユニーク制約・ID順正規化ガード・db_index・一括マージUI・トランザクション境界・レビューキュー後始末（マージセット全体でのmerged/invalidated判定）・ActionLog記録は呼び出し元の責務・Contact編集時の再照合トリガーと孤児Company対応）・Personマージ整合性（読み取り時解決、既存メソッド流用、フォロー漏れ検出も生存Person基準でQuerySetとして返す）・Companyマージ整合性（Contact/Deal双方で付け替え）・アクセス制御（Deal/ActivityともMeta.permissions宣言（view_all/edit_all）、編集・アーカイブの権限境界（owner/DealUser/edit_all、アーカイブはDealUser不可）、Company merge_company権限、9新規Group＋Attachment委譲、所有者ベース＋QuerySet版が単体判定と完全一致・薄いラッパーメソッドで統一）・Attachment配信経路（実機確認済み、View経由・ファイル名対応）・退職時のowner引き継ぎ（承認フロー等は将来のワークフロー機能検討時に再考）・アプリ構成（deals/activities/companies/attachmentsの4アプリ、循環import解消済み、activities→mailingsは一方向のみ）・フォロー漏れ検出のUI（activities側独立画面、Activity新規作成画面への遷移）。

## 9.2 未確定事項

**設計上の未確定事項はなし。** v0.7・v0.8へのレビュー（ジット君・ジェミニ君、各2ラウンド）で指摘された事項、および独自に検出した論点はすべて本版で解消した。**本版をもって、コード君向け実装指示書の作成に着手する。**

**本仕様書に含めず、実装指示書側に記載する事項（★v0.9で整理）**：仕様ではなく実装の段取りに属するため、本書には書かない。

- マイグレーションの作成順序と`dependencies`の実番号固定（`companies`初期 → `contacts`の`Contact.company`追加 → `accounts`のGroup投入）、`swappable_dependency(settings.AUTH_USER_MODEL)`
- CreateViewの画面構成（本体のみ登録 → 詳細画面へリダイレクト → サブフォームで関与者・添付を追加する2段構え。巨大なinlineformsetを作らない）。**★v1.8で明記：`DealCreateView`のみこの2段構えの例外とする**（次項参照）。Activity／Attachment／Companyの各CreateViewは引き続きこの2段構えに従う
- **★v1.7で追加、★v1.8でコード例と整合させて確定：Deal作成フォームは「相手方を1名選ぶ」ことを必須ステップとし、Deal本体と同一フォーム・同一トランザクションで完結させること（`DealCreateView`のみの例外）**。v1.7では、この必須ステップと上記の一般的な2段構え規約が矛盾したまま両方書かれており、かつ§2.1のコード例が実際には主担当DealPersonを作成しない欠陥があった（レビューで判明）。§2.1の`DealForm`／`DealCreateView.form_valid()`（`create_deal_with_primary_person()`を呼ぶ最新のコード例）をそのまま実装すること。`primary_person`という単一の必須FKが無くなったため、この必須化を怠るとコード君が素朴に実装した場合に相手方0名のDealが作成できてしまう
- `Deal.owner`付け替え時に、新ownerが既にDealUserなら当該DealUserを削除して昇格させる（§5.2の二重登録禁止との整合）
- **★v1.8で追加：`DealDeletePersonView`は`DealPerson.objects.get().delete()`のように直接削除せず、必ず`delete_deal_person()`（§2.6.1）を経由すること**。直接削除すると、主担当（`is_primary=True`）の`DealPerson`がガードなしで削除でき、Dealが相手方0名の状態に静かに陥る
- 日報一覧のGETパラメータ（`date`／`user`）の不正値フォールバック（本日／自分）
- admin登録と`autocomplete_fields`、`CompanyAdmin.search_fields`。**Service関数を経由すべきフィールドの`readonly_fields`指定は§7.6.1を参照（実装段取りの範囲外、業務ルールの保護そのものであるため仕様書側に明記済み）**。**★v1.0で追記、★v1.7で対象を更新、★v1.8でDealPersonAdminのリスクを削除操作にも拡大、★v1.10で単体登録／インラインの実装方式の違いを明記：admin.py登録時のチェックリストとして、対象を問わず「Service関数を経由すべきフィールド・アクションを`readonly_fields`／`has_delete_permission`／`delete_queryset`で塞ぐ」ことを徹底する。該当：`Deal.owner`／`is_archived`（§7.6.1）、`is_primary`のトグルおよび削除（§7.6.1、★v1.7・v1.8・v1.10で追加）。**`DealPersonAdmin`（単体登録）は`readonly_fields`＋`has_delete_permission()`／`delete_model()`／`delete_queryset()`（obj=DealPersonインスタンス）で条件付き保護、`DealPersonInline`（`DealAdmin`組み込み）は`InlineModelAdmin.has_delete_permission()`のobjが親Dealになる仕様上、行単位の削除拒否ができないため`can_delete=False`でインライン全体の削除を禁止するという、実装方式が異なる点に注意**（一括削除の「選択した項目を削除」は`has_delete_permission(obj=...)`を1件ずつ経由しないため、単体登録側は`delete_queryset()`のオーバーライドが別途必須）、`Activity.is_archived`（§7.6.1）、`Company.status`／`merged_into`（§7.6.1）、**`AttachmentAdmin`の削除操作**（`post_delete`シグナルで実ファイルは消えるが、`ActionLog`記録は`delete_attachment()`経由でしか行われないため、`has_delete_permission()`を`False`にしてadmin経由の削除自体を禁止する。§4.4.2参照）。この5種類はいずれも同一パターン（Service関数を経由する重要操作を、admin登録時に個別に塞ぎ忘れる）であり、実装時にadmin.pyを書く段階でこのチェックリストを一度通しで確認すること
- **★v1.8で追加：`DealForm`の`primary_person`はモデルフィールドではなくフォーム専用フィールドとして宣言し、`Meta.fields`には含めないこと**。`Deal`本体に`primary_person`は存在しないため（§2.1、§2.6.1）、誤って`ModelForm.Meta.fields`に含めると初期化・検証時に`FieldError: Unknown field(s) (primary_person) specified for Deal`でクラッシュする
- **★v1.8で追加：`DealCreateView.form_valid()`では`form.save(commit=False)`の後に自前で`deal.save()`を呼ばないこと**。`create_deal_with_primary_person()`（§2.6.1）が内部で`deal.save()`を実行するため、呼び出し側で先に保存すると二重INSERTまたは無駄なクエリが発生する。未保存の`deal`インスタンスをそのまま`create_deal_with_primary_person()`に渡し、永続化はこの関数に一任すること（§2.1のコード例通り）
- URLネームスペースと画面名（`<app>:<model>_<action>`）
- docstring規約（CLAUDE.md §10のレベル2）。本書のコード例は骨組みであり、docstringは規約に従って書き起こす
- BackNavigatorの`push_current`をどのViewで呼ぶか（1リクエスト1回）
- テストの置き場と必須項目。最低限、権限の一覧・詳細の集合一致（`can_view_activity`と`visible_activities_for`）、CheckConstraint違反が`IntegrityError`になること、一括マージのペア遷移（`merged`／`invalidated`の振り分け）、フォロー漏れ検出がマージ済みPersonを正しく扱うこと、の4本
- **UI設計とURL一覧表が確定するまで画面レイヤに着手しない**旨（CLAUDE.md §7の「CSS/JSは既存クラス・関数のみ、新規ファイル追加禁止」に抵触させないため。特に一括マージUIは既存に前例がなく、既存クラスで組めるかの確認が要る）

**★v0.10で追加：指示書の段取りに関する申し送り事項**（ジェミニ君の助言に基づく）。

- **実装を1回で丸投げしない。4ステップに分割し、各ステップの完了ごとにコミット・報告させる**：Step1（4アプリの作成・モデル定義・CheckConstraint・UUID主キー・ストレージ関数・初期マイグレーション）→Step2（`ensure_permissions()`を含む権限投入マイグレーションとAdmin設定）→Step3（Service層のコア機能：クローズ遷移・一括マージ・フォロー漏れ検出等）→Step4（View層とURLルーティング）。新設4アプリ＋権限追加はコードベース全体にまたがる変更であり、一度に実装させるとファイルの変更漏れや巨大コミットの原因になる
- **`ActionLog.record()`を呼ぶ前に、既存の`actionlogs/models.py`の実シグネチャを確認させる**：§4.4.2で`object_repr`引数を渡すコード例を示しているが、既存実装がこの引数を受け取らない場合は`TypeError`になる。`object_repr`引数が存在しなければ`data`辞書内に含める、という代替も指示書に明記する
- **★v1.0で追記：`action`引数は文字列リテラルのままでよい（`ActionLogAction`定数クラスは作らない）**。レビューの過程で「`action`引数に`ActionLogAction`定数クラスを新設して参照させるべき」という指摘が複数回出たが、**この定数クラスはFG2に実在しない**。正しい運用は、本仕様書のコード例の通り文字列リテラルを直書きし、実装完了後に使用した`action`文字列の一覧を仕様書またはREADMEの別表に参考値として追記する形である。指示書側で同じ指摘が再度出た場合も、この方針を維持し定数クラスを新設しないこと
- **選択肢フィールドを使う`__str__`・画面表示は必ず`get_FOO_display()`を通す**：§1.5で修正済みだが、本仕様書の他のコード例（サブフォームの表示等）を実装に落とす際も同じ注意が必要である旨を指示書の冒頭に一言添える
- **一覧テンプレートでも`get_FOO_display()`を徹底する**（★v0.11で追加）：§1.5で修正したのはモデルの`__str__`のみである。Deal一覧の`stage`、Company一覧の`status`のように、**テンプレート側で選択肢フィールドの値を直接表示する箇所も同じ問題を持つ**。`__str__`で直した問題が他所で再発しないよう、テンプレートでは常に`{{ deal.get_stage_display }}`のように書く旨を指示書に明記する

**★v0.14で追加：AIエージェント特有の手抜き・Djangoの細部に関する申し送り事項**（レビューでの助言に基づく）。

- **既存ファイル改修時の事前読み込みを義務付ける**：新設アプリのコードはゼロから書くので問題ないが、§6.5.4の`ContactUpdateView`（`contacts`アプリ）のように既存ファイルを書き換える指示がある。ファイルの中身を見ずに推測でパッチを当てると差分エラーで失敗を繰り返すため、「既存ファイルを編集する際は、必ず最初にファイル全体を読み込み、現在のクラス定義・import状況を把握してから修正すること」を指示書に明記する
- **`urls.py`の`app_name`設定漏れに注意させる**：§9.2でURLネームスペース（`deals:deal_list`等）を指定しているが、各アプリの`urls.py`に`app_name = 'deals'`を書き忘れると、`reverse_lazy('deals:deal_list')`が`NoReverseMatch`で失敗する。「新設アプリの`urls.py`には必ず先頭に`app_name = '<アプリ名>'`を明記すること」を指示書に明記する
- **`Subquery`の`output_field`を明示させる**：§2.5の`deals_with_last_contact`のような`Subquery`＋`OuterRef`のannotateは、DjangoのバージョンやDBによっては戻り値の型を明示しないと型解決に失敗し500エラーになる。「`Subquery`を使う際は`output_field=models.DateTimeField()`のように明示的に指定すること」を指示書に明記する
- **View層実装ステップでのテンプレート作り込みを禁止する**：Step4（View層とURLルーティング）で、頼まれてもいない装飾入りの巨大なHTMLをコード君が勝手に作り込むと、§9.3で「UI設計は別タスク（HIG準拠）」と決めた方針にノイズが混ざる。「Step4ではテンプレートの装飾や巨大なフォームUIの構築は一切行わず、既存のベースレイアウトを継承し必要最小限のブロックを置くのみに留めること（正式なUI実装は次フェーズの別タスク）」を指示書に明記する
- **`datetime`ではなく`django.utils.timezone`の使用を徹底させる**：§3.1の`occurred_at`未来日禁止のような日時判定で、標準の`datetime`モジュールを直接使うと`RuntimeWarning: DateTimeField received a naive datetime`が発生する。「日時を扱う処理では必ず`django.utils.timezone`を使い、aware datetimeで統一すること」を指示書に明記する

**★v0.19で追加：さらに4点（レビューでの助言に基づく）**。

- **§6.5.3の候補ペア生成は`get_or_create`ではなく`update_or_create`を使う**：`get_or_create`のままだと、手動更新等でContactのスコアが変わっても、既に`pending`キューにある候補のスコアが古いまま更新されない。「候補ペア生成は`update_or_create`を使用し、`defaults`に最新の`score`等を渡すこと」を指示書に明記する（`§6.5.3`のコード例自体は`get_or_create`のままとする。**理由**：`update_or_create`に変えると`defaults`に含めた`review_status`以外のフィールドが既存レコードでも上書きされる挙動になるため、`rank`等の再計算ロジックと合わせて実装時に調整が必要であり、コード例を書き換えるより実装時の注意点として渡す方が安全）
- **一覧画面のページネーションでGETパラメータを引き継ぐ**：§3.3の「クエリパラメータがない場合はuser=自分・date=本日をデフォルト適用する」をView層の`get_queryset()`だけで実装すると、ページネーションのリンク（`?page=2`）がフィルタ条件を保持せず、2ページ目に遷移した瞬間にデフォルトのフィルタへ戻ってしまう（他人の日報の2ページ目を見ようとしても常に自分の本日に戻される）。「一覧画面のページネーションリンクを生成する際は、現在のURLのGETパラメータ（`user`／`date`等）をすべて引き継ぐこと」を指示書に明記する
- **`created_at`／`updated_at`の自動更新引数を明示する**：仕様書の型定義表には`DateTimeField`とだけ書かれている箇所があるが、実装時は既存モデルに倣い`created_at`に`auto_now_add=True`、`updated_at`に`auto_now=True`を必ず設定することを指示書に明記する
- **ファイルの物理削除は`post_delete`シグナルに一本化する**：詳細は§4.4.2で確定済み。指示書側にも「Attachmentの実ファイル削除は`delete_attachment()`関数内ではなく`post_delete`シグナル（`delete_attachment_file`）で行うこと。CASCADE削除やadmin画面からの削除でも実ファイルが確実に消えるようにするため」と一言添える

**★v1.4で追加**。

- **引数の多い関数はキーワード引数で呼ぶ**：§6.5.1の`calculate_company_match()`は10個の引数（`organization_a`／`domain_a`／...／`organization_b`／...）を位置引数で受け取る形で定義している。位置引数のまま呼び出すと、コード君が`domain_a`の位置に`organization_b`を渡すような順序間違いを起こしうる。「`calculate_company_match()`のような引数の多い関数を呼び出す際は、必ずキーワード引数（`organization_a=...`）で明示すること」を指示書に明記する

**★v0.14で追加：指示書は「全体ルール」と「Step1の指示」を分けて渡す**。実装指示書を1つの巨大なテキストにするより、まず全体ルール（本節の申し送り事項＋既存のガードレール：`git add .`禁止、`select_for_update`の`of=`指定、コアロジック不変更等）だけを渡し、コード君に「ルールと仕様書を理解したら、Step1の指示を待っている旨だけ返答せよ、コードはまだ書くな」と指示する。理解を確認してからStep1の具体的な指示を渡すことで、エージェントのコンテキスト溢れや暴走を抑える。

**別途作成するドキュメント**：URL・View名の正本は既存の`URL一覧表`への追記とする（CLAUDE.md §3）。Deal 8本・Activity 5本・Company 7本・Attachment 3本・フォロー漏れ検出1本で、合計20本前後になる。

次フェーズ（AccessList・Person閲覧制限・Schedule/Task/Project・受注/失注の承認ワークフロー）に関する論点は本仕様書のスコープ外として別途整理する。

**実装後に確認すべき事項（設計の未確定ではなく、実装完了時のチェック項目）**：

- Viewが要求する全ての`permission_required`が、いずれかのGroupに含まれているか（§7.6の反面教師を繰り返さないため）
- `delete_deal`／`delete_activity`／`delete_company`を要求するViewが1つも存在しないこと（存在するなら§7.1の判断を見直す必要がある）
- 複数環境で`makemigrations`を実行しても、storage起因の差分マイグレーションが生成されないこと（§4.1）

**既存の別課題（本仕様書では対応しない、記録のみ）**：`persons.change_person`・`mailings.view_unsubscribe`がどのGroupにも配布されておらず実質superuser専用になっている（§7.6参照）。`cards.create_card`／`edit_card`／`merge_card`が死蔵権限のまま残っている。

## 9.3 残っている作業（このドキュメント外）

- コード君向け実装指示書の作成
- Deal/Company一覧・詳細画面等のUI設計（HIG準拠、一括マージUIの画面仕様含む）
- AccessList・Person閲覧制限の次フェーズ設計メモの正式ドキュメント化
- MailingListの所有者判定要否に関する運用上の再検討（Dealとは独立タスク）

---

# 改訂履歴

| バージョン | 日付 | 内容 |
|---|---|---|
| v1.10 | 2026-09-09 | 2件のレビューで判明した、`DealPersonInline.has_delete_permission()`のDjango API誤用（`obj`は子DealPersonではなく親Dealが渡される仕様）を修正。行単位の削除拒否をインラインで実現するAPIは存在しないため、`DealPersonInline`は`can_delete = False`でインライン全体の削除を禁止し、削除は`DealPersonAdmin`（単体登録）または`DealDeletePersonView`（`delete_deal_person()`経由）に一本化する方針に変更。影響箇所：§7.6.1、§9.1、§9.2 |
| v1.9 | 2026-09-09 | 2件のレビューで判明した、admin画面経由での主担当DealPerson保護漏れを解消。`DealPersonAdmin`（`DealAdmin`インライン含む）に`is_primary`のトグル防止（`readonly_fields`）と削除防止（`has_delete_permission()`＋一括削除対応の`delete_queryset()`）を追加（§7.6.1、§9.2）。実装時の落とし穴2点（`DealForm.Meta.fields`への`primary_person`混入によるFieldError、`DealCreateView.form_valid()`での`deal.save()`二重呼び出し）を§9.2の申し送りに追記。`Subquery`の`output_field`明示は既存の§9.2記載で対応済みのため変更なし。影響箇所：§7.6.1、§9.1、§9.2 |
| v1.8 | 2026-09-09 | レビューで判明した4点を反映。①`DealCreateView.form_valid()`が`create_deal_with_primary_person()`を呼んでおらず主担当DealPersonが作られない欠陥を修正し、§9.2の「CreateView 2段構え」規約とDeal作成フォームの必須化指示の矛盾を解消（Dealのみ例外と明記）。②主担当DealPerson（`is_primary=True`）を通常削除できてしまう穴を塞ぐ`delete_deal_person()`を新設（§2.6.1）。③`Company.merged_into`の`on_delete`指定漏れを`SET_NULL`に修正（§6.2、`Person.merged_into`に統一）。④§2.4の「フォロー漏れ検出画面からDeal作成へ遷移する動線」という存在しない画面への言及を削除（たんたんの判断によりこの動線自体は次フェーズ送り）。影響箇所：§2.1、§2.4、§2.6.1、§6.2、§9.1、§9.2 |
| v1.7 | 2026-09-09 | たんたんとの壁打ちにより、`Deal.primary_person`（本体の単一FK）を廃止し`DealPerson.is_primary`（部分ユニーク制約付き）に一本化。v1.6の「DealPerson/DealUserへの主担当フラグ追加禁止」は対症療法だったため撤回し、根本原因（相手方の主担当を表現しうる場所が2箇所あったこと）を解消。付け替え関数`reassign_deal_primary_person()`をDealPerson基準に全面改訂（§2.6.1）。`Deal.owner`の表示ラベルを「案件オーナー」に変更し、`DealUser.role=primary`（表示ラベル「主担当」）との語彙衝突を解消。データ移行は実運用未投入のため不要（開発DB再作成で対応）。影響箇所：§2.1、§2.6、§2.6.1、§5.1、§5.2、§7.6.1、§8.1、§8.2、§9.1、§9.2、付録A.2 |
| v1.6 | 2026-09-09 | Step 1実装レビュー（サポート担当クロード君）で判明した2点を反映。①`DealPerson`に`is_primary`（主担当フラグ）を独自追加してしまう実装があったため、`DealPerson`／`DealUser`に主担当・主担当者を示すフィールドを追加してはならない旨を§5.2に明記（`Deal.primary_person`／`owner`が唯一の正）。②`can_edit_deal`が`Contact.can_edit_contact`と異なり`created_by`にフォールバックしない設計について、たんたんとの壁打ちで「担当を外れた作成者は編集できなくなる方が実態に合っている」と確定したため、意図的な非対称である旨を§7.1に明記。判定関数のコード自体・DBスキーマに変更はない |
| v0.1 | 2026-09-05 | 設計壁打ちセッションの結論を初版としてまとめ |
| v0.2 | 2026-09-05 | ジット君・ジェミニ君のレビュー（実コード突き合わせ）を反映。Activity.person廃止・campaign追加、Company重複検出の簡素化・自動リンク条件統一、CheckConstraint記法修正（condition=）、Deal.company付け替え方針の変更、既存実装の流用（重複実装の解消）、アクセス制御にActivity/Attachmentの判定ロジックを追加 |
| v0.3 | 2026-09-05 | ジット君・ジェミニ君のレビューを反映。§3.2 CheckConstraintの未実装フィールド参照を削除（makemigrationsクラッシュ回避）、§6.5.5にDeal.company付け替え処理を追加、§7.3にActivityUser（同席者）の閲覧権限と`visible_activities_for`を追加、§2.1のcreated_by重複行を削除、§5章にUniqueConstraint追加、§6.5.4にプレフィルタのNULL安全策を追加、§4.3にファイル名対応（original_filename・as_attachment）を追加 |
| v0.4 | 2026-09-05 | ジット君・ジェミニ君のレビューを反映。アプリ構成（deals/activities/companies）を明示、§3.5.2のクエリ修正（related_name反映・NULL除外罠回避・ボットクリック除外）、§6.5.4にマージ済みCompany除外を追加、§6.5.2でC-1緩和案の不採用を確定、§7.3で`view_all_activities`を判定最優先に変更、§2.4でクローズ遷移（受注/失注）を専用フォーム経由に限定、§3.4でActivityPerson/ActivityUserのon_delete明記、§4.3でMODEL_REGISTRYのKeyError対策と`has_view_permission`の薄いラッパーメソッドによる整合を追加 |
| v0.5 | 2026-09-05 | ジット君・ジェミニ君のレビューを反映。Attachmentを独立アプリ`attachments`に切り出し循環importを構造的に解消、§3.5.2の配置記述の食い違いを修正、§2.5に`change_deal_stage`のガードと`close_deal()`関数分離を追加、§6.5.5にtransaction.atomic前提・レビューキュー後始末・遅延importを追加、§6.2/§6.5.6でCompany.domainを「空→値」の一方向穴埋めルールに修正、§6.5.4にexact_match複数候補時のルール（最古優先）を追加、§7.1/§7.3にMeta.permissions宣言を追加、§7.3のvisible_activities_forを単体判定と完全一致する集合に修正、§4.3のMODEL_REGISTRYをファイル保持モデル専用と明記 |
| v0.6 | 2026-09-05 | ジット君・ジェミニ君のレビューを反映。§6.5.5のレビューキュー後始末をmerged/invalidatedに分離（マージ実績が消える不具合を修正）、Company.domainのマージ時引き継ぎ処理（maybe_fill_company_domain呼び出し）を追加、§2.5 close_dealをfull_clean()からclean()直接呼びに変更・失注理由クリア・updated_by設定・transaction.atomic追加・Deal.Stage列挙型統一、§2.1にnull/blank指定を明記、§3.5.2フォロー漏れ検出を生存Person基準の照合に変更しUI方針（activities側独立画面、Activity新規作成画面への遷移）を確定、§1.2にactivities→mailingsの依存を明記、§6.5.4にContact編集時の再照合トリガーとexact_match複数候補時の1対1ペアリングルールを追加、§3.3に日報一覧のデフォルトフィルタを追加 |
| v1.5 | 2026-09-06 | ジット君・ジェミニ君の両方からv1.4に対するLGTMを得た上で、ジェミニ君から実装指示書向けの追加提案（`calculate_company_match()`のような引数の多い関数はキーワード引数で呼ぶこと）があったため、§9.2の申し送り事項に1点追加。仕様書本体のモデル定義・業務ルールに変更はない |
| v1.4 | 2026-09-06 | バージョン番号の訂正。「内容が変わったら必ずバージョンを変える」運用ルールに反し、直前のレビュー反映（ジット君の2点：URL主軸化の非対称解消、関数間の接続コード追加）をv1.3のまま出力してしまっていたため、正式にv1.4として切り直した。内容はレビュー反映後のv1.3と同一（差分なし） |
| v1.3 | 2026-09-06 | たんたんとの壁打ちにより、Company重複検出のURL重み付けを再検討。名刺記載のURLは公式サイトを指す確度が高いという判断から、URLの配点を20点から100点に引き上げ、ドメインも120点から100点に統一し、会社名・ドメイン・URLを対等な主軸とした（§6.5.1）。共有ホスティング配下のページで無関係な会社同士がドメイン一致してしまう問題に対処するため、URLを「ドメイン＋パスの最初のセグメント」に正規化する`normalize_website()`を新設。ランク判定の必須条件を「会社名 AND ドメイン」から「会社名 AND (ドメイン OR URL)」に一般化し、`possible_mid`／`possible_low`の判定を「一致した項目の種類」ベースから「会社名・ドメイン・URLのいずれか一致していればスコア合計で判定」という形に統合（v1.1〜v1.2では会社名一致の方がドメイン一致より到達スコアが高く設定されていた逆転を解消）。会社名一致を`exact_match`／`possible_high`の必須条件から外さない設計は維持（グループ会社間の連絡先共有による誤結合を避けるため）。§6.5.4手順4の閾値表現を更新。**ジット君のレビューを受けて2点追加修正**：①`possible_mid`／`possible_low`の必須条件が「会社名 OR ドメイン」のみでURLを含んでおらず、「対等な主軸」という方針と矛盾する非対称（ドメイン単独一致はキューに乗るがURL単独一致は候補外）を生んでいたため、両ランクの必須条件にURLを追加。②スコア計算・ランク判定・候補保存の3関数（`calculate_company_score()`・`determine_company_rank()`・`get_or_create_candidate_pair()`）を接続する呼び出し例が一度も示されていなかったため、`calculate_company_score()`を`calculate_company_match()`に改称し`CompanyMatchResult`（スコア＋各項目の一致フラグ）を返す形に改修、3関数をつなぐ`register_company_candidate()`を新設。あわせて、v1.0時点で§9.2にのみ記載されていた`AttachmentAdmin.has_delete_permission()`を§7.6.1（仕様書本体）に格上げし、他5フィールドと同列に管理する形に統一 |
| v1.2 | 2026-09-05 | ジット君の追加レビュー（v1.1に対して）を反映。§6.5.1の`calculate_company_score()`が`Company`インスタンスを直接受け取る形になっており、Contact対Companyの照合（Contact側は`org_domain_name`／`org_phone`という異なる属性名を持つ）に使えなくなっていた欠陥を修正。生の値10個を受け取る形に戻し、Contact対Company・Company対Companyそれぞれの呼び出し例を明記。§6.5.4手順3をこの呼び出し例への参照に更新。§6.2の`phone`／`address`／`website`の説明を、§6.5.6で「マージ時引き継ぎは対象外」とした決定と整合するよう「新規作成時のコピーのみ」に修正し、§6.5.4手順5に`create_company_from_contact()`の実体コードを追加。§6.5.6の記述も整合するよう修正。付録A.7に`calculate_company_score()`・`create_company_from_contact()`を追加 |
| v1.1 | 2026-09-05 | 別セッション（v0.7時点をベースに検討）からの引継ぎ指示のうち、Company重複検出の**スコアリングとランク判定のみ**を反映。§6.2に`phone`／`address`／`website`を追加（対応するContact側は`org_phone`／`address`／`website`）。§6.5.1のスコア表を5項目に拡張（ドメイン120・会社名100・電話20・住所20・URL20）。§6.5.2のランク判定を4段階のハイブリッド判定に変更し`possible_low`を新設。§6.5.3の`rank`選択肢に`possible_low`を追加。§6.5.4の判定フロー（手順3〜5）とプレフィルタの説明を新スコア・新ランクに合わせて更新。§6.5.6に`phone`／`address`／`website`はマージ時引き継ぎの対象外である旨を追記。指示書にあった`is_generic_email_domain()`漏れ対策のActionLog記録（変更5）は、たんたんの指示により今回のスコープに含めない。付録A.4・A.6を更新 |
| v1.0 | 2026-09-05 | **正式版として確定。** v0.1〜v0.19（のべ24回のレビューパス：ジット君・ジェミニ君による構造検証、加えて「漏れ確認・コード君目線チェック」を5回）で見つかった問題をすべて解消した状態でバージョン番号を格上げ。加えて2点を追加：①§6.5.4で呼び出されていた`archive_company_if_orphaned()`の実体が19版を通じて一度も定義されていなかったため新設（付録A.7にも追加）。②§9.2に「`ActionLogAction`定数クラスはFG2に実在しないため作らない」旨を明記（レビューで複数回出た誤った推奨を訂正）、および「admin.py登録時のチェックリスト」としてService関数を経由すべき操作の塞ぎ忘れ防止項目（Attachment削除の`has_delete_permission()`を含む）を集約。直近5ラウンド（v0.14〜v0.19）は「Service関数を経由すべき重要操作をDjango adminが迂回できる」という同一パターンの再発見に収束したため、これ以上の仕様書側でのレビュー往復を終了し、以降は実装指示書側のチェックリストとして一元管理する。本版をもってコード君向け実装指示書の作成に着手する |
| v0.19 | 2026-09-05 | ジェミニ君のレビュー（v0.17時点、到達遅延分）を反映。§4.4.2のAttachment実ファイル削除を、`delete_attachment()`内の直書きから`post_delete`シグナル（`delete_attachment_file`）に一本化。admin画面からの直接削除やCASCADE削除でも実ファイルが孤児として残らないようにした。ただしシグナル単体だとトランザクションロールバック時に不整合が生じるため、シグナル内でも`transaction.on_commit`を使う形に修正（レビュー原案はシグナル単体だったが、v0.9で確立した原則との整合を取るためこちらで補強）。残り3点（`update_or_create`、ページネーションのGETパラメータ引き継ぎ、`auto_now_add`／`auto_now`明示）は仕様書ではなく実装指示書の申し送り事項として§9.2に追加。付録A.7に`delete_attachment_file()`を追加 |
| v0.18 | 2026-09-05 | ジット君の追加レビュー（v0.17確定後、admin保護コードの副作用点検とCompanyDuplicateCandidateの監査証跡点検）を反映。§7.6.1の`readonly_fields`静的指定が、`Deal.primary_person`（必須項目）を含むためDeal新規作成をadmin経由で不可能にしていた問題を`get_readonly_fields()`に修正。§6.2の`Company.status`に`default=Status.ACTIVE`を追加（`Deal.stage`と非対称だった）。§6.5.5の`execute_company_merge()`の`.update()`呼び出し2箇所に`reviewed_by`・`reviewed_at`・`updated_at`を追加し、`CompanyDuplicateCandidate`の監査証跡が永久にNULLのままだった問題を修正。§6.5.5.1を新設し、17版を通じて定義されていなかった`mark_as_different_company()`（Person側`mark_as_different_person()`と同型）を追加。付録A.7に追加 |
| v0.17 | 2026-09-05 | ジット君の追加レビュー（v0.16確定後、Django admin経由の抜け道の点検）を反映。§7.6.1を新設し、本仕様書が積み上げてきた「重要な変更は専用のService関数を経由させる」原則がすべてView層のフォーム構成に依存しており、Django adminがこれを丸ごと迂回できる問題に対応。`Deal.owner`／`Deal.primary_person`／`Deal.is_archived`／`Activity.is_archived`／`Company.status`／`Company.merged_into`の6フィールドを対応する`ModelAdmin`の`readonly_fields`に指定する方針を明記。特に`Company.status`/`merged_into`のadmin経由の直接変更は、FK付け替え・レビューキュー後始末・ActionLog記録を一切伴わないCompany単独のmerged化という実害が大きいため優先度が高い。§9.2のadmin登録の申し送りから§7.6.1への参照を追加 |
| v0.16 | 2026-09-05 | ジェミニ君のレビュー（v0.13時点、到達遅延分）のうち未反映だった1点を反映。§2.1の`Deal.company`自動補完について、実現方式が「未指定なら初期値を設定する」という方針止まりで、バックエンド／フロントエンドどちらで実装するかが未確定だった。JS等による動的なプルダウン書き換えに走ると複雑化するため、`DealCreateView.form_valid`で未入力の場合のみサーバ側で補完する方式に確定 |
| v0.15 | 2026-09-05 | ジット君の追加レビュー（v0.14確定後）を反映。§5.1で新設した`clean()`はDealPerson／DealUser作成・更新時にしか働かず、Deal本体の通常編集フォームで`owner`／`primary_person`を直接変更すると再検証されないという指摘に対応。`clean()`に削除の副作用を持たせるのではなく、Deal編集フォームから`owner`・`primary_person`を除外し変更を専用関数経由に限定する方針を確定（§2.6）。`primary_person`付け替え用に`reassign_deal_owner()`と同型の`can_reassign_deal_primary_person()`・`reassign_deal_primary_person()`を新設（§2.6.1）。§9.2にDeal編集フォームのフィールド除外の申し送りを追加。付録A.7に新設関数を追加 |
| v0.14 | 2026-09-05 | ジット君・ジェミニ君の追加レビュー（v0.13確定後、さらに一段掘り下げた再点検）を反映。§5.1に`DealPerson.clean()`・`DealUser.clean()`を新設し、§5.2が初版から宣言していた「本体の単一FKとの二重登録禁止（バリデーションで縛る）」を13版を通じて初めてコード化。Activity側にも同型の`ActivityUser.clean()`を追加（`ActivityPerson`側は対応する単一FKがないため対象外）。§2.6の`reassign_deal_owner()`に、new_ownerが既存DealUserなら削除する処理を統合し、v0.13までの両論併記（関数内／呼び出し元フォームのどちらでもよい）を解消。§9.2に実装指示書向けの申し送り事項（既存ファイルの事前読み込み義務、`urls.py`の`app_name`、`Subquery`の`output_field`明示、Step4でのテンプレート作り込み禁止、`timezone`によるaware datetime統一、全体ルールとStep1を分けて渡す進め方）を追加 |
| v0.13 | 2026-09-05 | ジット君の追加レビュー（v0.12確定後、実装者目線での再点検）を反映。§6.2の`Contact.company`を文字列参照（`'companies.Company'`）に変更し、`contacts ↔ companies`間の循環import（`companies → deals`と同型だが見落としていた問題）を解消。§1.2の依存図に`contacts → companies`を追記し、アプリをまたぐFKは文字列参照で書く原則を明記。§6.5.5に、ContactとDealでimportの扱いが非対称な理由（FKの参照方向の交差の有無）を追記。§2.6に`can_reassign_deal_owner()`・`reassign_deal_owner()`を新設（`can_edit_deal`で代用するとDealUser全員にowner付け替えを許してしまう穴があったため、判定関数も実行関数も存在しなかった状態を解消）。§7.5にCompanyDuplicateCandidateのレビュー操作を`companies.merge_company`で統一する旨を明記（既存`duplicates`の前例に倣う）。付録A.7に`reassign_deal_owner()`等を追加 |
| v0.12 | 2026-09-05 | ジット君のレビュー（v0.11に対して）を反映。§7.1の`visible_deals_for()`と§7.3の`visible_activities_for()`に残っていた`related_name`の書き換え漏れ（デフォルトの`dealuser`／`activityuser`のまま）を`deal_users`／`activity_users`に修正。Deal一覧・日報一覧・放置案件検出（`deals_with_last_contact`）が初回アクセスで`FieldError`になる致命的な欠陥だった。文書全体を旧デフォルト名で再検索し、他の取りこぼしがないことを確認済み。付録A.7に`archive_deal()`・`archive_activity()`・`get_companies_confirmed_as_different()`を追加 |
| v0.11 | 2026-09-05 | ジット君・ジェミニ君のレビュー（v0.10に対して）を反映。§6.5.4の`get_companies_confirmed_as_different()`の適用箇所を、Contactのリンク先探索（構造的に成立しない）から、exact_match複数候補のペアリングと一括マージ対象選択（§6.5.5）に修正。§5.2にDealPerson／DealUser／ActivityPerson／ActivityUserの追加・削除の権限ゲート（`can_edit_deal`／`can_edit_activity`）を追加。§7.1・§7.3に`archive_deal()`／`archive_activity()`の実行関数と画面導線を新設（判定関数はあったが実行関数が欠落していた）。§6.5.5にCompanyマージ時のActionLog紐付け先（消える側／生存側）についての確認事項を追記（Person側は専用ログモデルに紐づく設計で前提が異なるため）。§9.2の申し送りに一覧テンプレートでの`get_FOO_display()`徹底を追加 |
| v0.10 | 2026-09-05 | ジット君の再々レビュー（実コードとの照合）を反映。§1.4.2のUserへの`related_name`を実コード規則（監査系FKは`related_name="+"`、業務的な逆引きが必要なものだけ命名する）に合わせて全面修正、`Company.merged_into`がPersonの前例と異なる命名である旨を明記、§6.5.3に`get_companies_confirmed_as_different()`を新設し「別会社」判定済みペアが再照合のたびに再生成される問題を解消、§1.3の「全8モデル」を「全9モデル」に修正、§4.1の`attachment_upload_to`の定義順序（`NameError`の原因）とimport漏れ（`timezone`）を修正、§6.5.5の`execute_company_merge()`の検証順序を0件チェック優先に並べ替え、§4.3の`MODEL_REGISTRY`参照を`get_registry_entry()`に集約、§1.5に`Activity.__str__`のラベル展開注記と残り5モデルの`__str__`を追加。指示書作成に向けた段取り（ステップ分割・`ActionLog.record`のシグネチャ事前確認・`get_FOO_display()`の徹底）は本書には含めず、実装指示書側の申し送り事項とする |
| v0.9 | 2026-09-05 | ジット君・ジェミニ君の再レビュー（v0.8に対する2ラウンド目）を反映し、**既存コードの実装慣習との整合**を取り込んで確定。主キーをUUIDに明記（§1.3新設）、全FKの`related_name`命名表を新設し§3・§7のコード例を全面書き換え（§1.4新設）、`Meta.ordering`の既定を追加（§1.5新設）、`find_or_link_company()`を`link_contact_to_company()`に改名し`can_*`を規約の例外として明記（§7.0新設）、`create_permissions()`を既存6本と同一形に修正（`models_module`の書き換えで`post_migrate`の権限生成が止まる問題）、`can_archive_deal`／`can_archive_activity`に`change_*`のAND条件追加、`can_delete_attachment`に`Activity.created_by`追加、依存図に`deals → mailings`追加、`expected_value()`のNone対応と未入力案件の扱い確定、「最終接触日」をSubquery+OuterRef方式に確定、`Activity.occurred_at`の`clean()`コード追加、`upload_to`のUUID化と添付のサイズ・拡張子制限、`get_or_create_candidate_pair()`（IntegrityError回避）とスコア2回計算の明記、`execute_company_merge()`の入力検証追加、`Deal.company`の自動補完ルール確定、Dealアーカイブ時のActivityの扱いを新設（§2.8）、`Company.created_by`は自動作成時NULL、添付メモの編集動線を新設（§4.4.4）、`ProtectedFileDownloadView`の権限をレジストリ化、添付削除ログに親コンテキストと`object_repr`、フォロー漏れ検出でアーカイブ済みPersonは除外せず表示とフィルタで扱う方針を確定、`observer`の効力の限界・`can_delete_attachment`にANDを入れない理由・Companyに二重防衛を設けない理由を明文化、仕様書と実装指示書の分担を§9.2に整理 |
| v0.8 | 2026-09-05 | ジット君・ジェミニ君のレビューを反映し、指示書化に必要な空白を埋めて確定。`FileField(storage=...)`をcallable化（マイグレーションへの絶対パス焼き込み回避）、選択肢（TextChoices）の実体を全10フィールド分定義（§2.8新設）、`Deal.source_campaign`新設と`lead_source`との整合ルール追加、`Activity.created_by`追加と編集・アーカイブ判定への反映、値域バリデーション（amount・probability・closed_at未来日）追加、viewerロールの可視範囲を確定（`view_all_*`は配らずDealUserの`observer`で個別に開ける）、`can_edit_deal`／`can_edit_activity`に`change_*`のAND条件追加、アーカイブのViewゲートを`change_*`に確定し`delete_*`3権限は配らない方針を決定、Attachmentの物理削除・実ファイル削除・アップロード/削除画面・削除の権限境界を定義（§4.4新設）、`can_edit_attachment`／`can_delete_attachment`追加、`ContactUpdateView`の`old_company`退避の実行順序を明記、フォロー漏れ検出に`is_archived=False`と`select_related`追加、PermissionGroup投入に`create_permissions()`明示呼び出しを追加、Companyの手動アーカイブ動線を新設（§6.6）、一括マージViewの`merge_company`要求と`ProtectedFileDownloadView`の`PermissionRequiredMixin`を明記、`update_fields`への`updated_at`追加、多段マージのN+1を既知の許容事項として明記、付録Aに名称対照表を新設 |
| v0.7 | 2026-09-05 | ジット君・ジェミニ君のレビューを反映し、権限グループ設計を確定。全モデルのCharFieldにmax_length追記、Deal.closed_atにblank=True追加、フォロー漏れ検出をクリック側も生存Person基準に正規化しQuerySetで返すよう再修正（v0.6は判定のみ生存基準で一覧は元Personのままだった不具合を修正）、Attachmentモデルにstorage明記、CompanyDuplicateCandidateに部分ユニーク制約・ID順正規化ガード・max_lengthを追加、Companyマージのペア判定をマージセット全体で行うよう再構成しActionLog記録を呼び出し元（execute_company_merge）の責務に分離、ContactUpdateView再照合後の孤児Company対応（archived化）を追加、Deal.stageにdefault追加、Activityにnull/blank指定を追加、日報一覧の権限フィルタと表示フィルタの合成順序を明記、Company.organization/domainにdb_index追加、編集・削除の権限境界（edit_all_deals/edit_all_activities/merge_company）と9新規PermissionGroupを新設（既存contact_*と同じ命名・配分方針に統一） |

---

# 付録A 名称対照表（★v0.8で新設）

本文中の日本語名と、実装時のコーディング名の対照。FKの`related_name`は§1.4の命名表を参照。

## A.1 アプリ・モデル

| 日本語名 | コーディング名 | 所属アプリ |
|---|---|---|
| 案件 | Deal | deals |
| 案件関係者（社外） | DealPerson | deals |
| 案件担当者（社内） | DealUser | deals |
| 活動記録 | Activity | activities |
| 活動関係者（社外） | ActivityPerson | activities |
| 活動同席者（社内） | ActivityUser | activities |
| 会社 | Company | companies |
| 会社重複候補 | CompanyDuplicateCandidate | companies |
| 添付ファイル | Attachment | attachments |
| 案件（受注後） | Project（次フェーズ） | － |

## A.2 Dealのフィールド

**★v1.7で修正**：`primary_person`（相手方の主担当）はDeal本体のフィールドではなくなった。相手方の主担当は`DealPerson.is_primary`（§5.1、§2.6.1参照）で表現する。`owner`の表示ラベルは「案件オーナー」に変更（コーディング名`owner`自体は変更なし）。

| 日本語名 | コーディング名 | 日本語名 | コーディング名 |
|---|---|---|---|
| 案件名 | name | 受注予定日 | expected_close_date |
| 会社 | company | 成約確定日 | closed_at |
| 案件オーナー | owner | 案件発生源 | lead_source |
| 作成者 | created_by | 発生源キャンペーン | source_campaign |
| 更新者 | updated_by | 失注理由 | lost_reason |
| ステージ | stage | 概要メモ | memo |
| 確度 | probability | 論理削除フラグ | is_archived |
| 種別 | deal_type | 作成日時 | created_at |
| 金額 | amount | 更新日時 | updated_at |

## A.3 Activityのフィールド

| 日本語名 | コーディング名 | 日本語名 | コーディング名 |
|---|---|---|---|
| 紐づく案件 | deal | 実施者 | user |
| 紐づくキャンペーン | campaign | 作成者 | created_by |
| 種別 | activity_type | 場所 | place |
| 方向 | direction | 内容メモ | memo |
| 実施日時 | occurred_at | 論理削除フラグ | is_archived |

## A.4 Company／CompanyDuplicateCandidateのフィールド

| 日本語名 | コーディング名 | 日本語名 | コーディング名 |
|---|---|---|---|
| 会社名 | organization | 会社A／会社B | company_a／company_b |
| ドメイン | domain | スコア | score |
| 電話（★v1.1追加） | phone | ランク | rank |
| 住所（★v1.1追加） | address | レビュー状態 | review_status |
| ウェブサイト（★v1.1追加） | website | レビュー者／レビュー日時 | reviewed_by／reviewed_at |
| 状態 | status | | |
| 統合先 | merged_into | | |
| 作成者 | created_by | | |

## A.5 Attachmentのフィールド

| 日本語名 | コーディング名 |
|---|---|
| 紐づく案件／紐づく活動 | deal／activity |
| ファイル | file |
| 元ファイル名 | original_filename |
| アップロード者 | uploaded_by |
| メモ | memo |

## A.6 ステージ・選択肢（§2.9の再掲・一覧）

| 分類 | 日本語ラベル | value |
|---|---|---|
| ステージ | 初回商談／ヒアリング・課題整理／見積提示／検討中／社内稟議中／交渉／受注／失注 | initial_meeting／needs_analysis／quotation／under_review／internal_approval／negotiation／won／lost |
| 案件の性質 | 新規開拓／既存深耕／更新・継続 | new／expansion／renewal |
| 案件発生源 | 展示会・イベント／紹介／Web問い合わせ／直接問い合わせ／メールキャンペーン／既存客からの相談／自社からの営業／その他 | exhibition／referral／inbound_web／inbound_direct／campaign／existing_customer／outbound／other |
| 活動種別 | 電話／訪問／メール／Web会議／その他 | phone／visit／email／web_meeting／other |
| 方向 | 発信／受信 | outgoing／incoming |
| 社外の役割 | 決裁者／窓口／技術担当／同席者／その他 | decision_maker／contact_window／technical／attendee／other |
| 社内の役割 | 主担当／協力担当／上長承認者／閲覧者／その他 | primary／support／approver／observer／other |
| 会社の状態 | 有効／統合済み／アーカイブ | active／merged／archived |
| 重複ランク | 完全一致／可能性高／可能性中／可能性低 | exact_match／possible_high／possible_mid／possible_low |
| レビュー状態 | 未処理／統合済み／別会社と判断／無効化 | pending／merged／different_company／invalidated |

## A.7 主要な関数・権限

| 日本語での呼び名 | コーディング名 | 参照 |
|---|---|---|
| ステージ変更（オープン間） | change_deal_stage() | §2.5 |
| クローズ遷移（受注/失注） | close_deal() | §2.5 |
| フォロー漏れ検出 | get_unfollowed_clickers() | §3.5.2 |
| 添付の削除 | delete_attachment() | §4.4.2 |
| 添付の実ファイル削除（signal） | delete_attachment_file() | §4.4.2 |
| 会社の照合・自動リンク | link_contact_to_company() | §6.5.4 |
| 重複検出のスコア・一致フラグ計算 | calculate_company_match()（★v1.3で改称、旧calculate_company_score） | §6.5.1 |
| URLの正規化（比較用） | normalize_website() | §6.5.1 |
| 重複検出ランクの判定 | determine_company_rank() | §6.5.2 |
| Contactから新規Company作成 | create_company_from_contact() | §6.5.4 |
| 孤児Companyの自動アーカイブ | archive_company_if_orphaned() | §6.5.4 |
| 案件owner付け替えの判定／実行 | can_reassign_deal_owner()／reassign_deal_owner() | §2.6 |
| 案件primary_person付け替えの判定／実行 | can_reassign_deal_primary_person()／reassign_deal_primary_person() | §2.6.1 |
| 重複候補ペアの生成 | get_or_create_candidate_pair() | §6.5.3 |
| 別会社と判定済みの相手Company集合の取得 | get_companies_confirmed_as_different() | §6.5.4 |
| 最終接触日付きの案件一覧 | deals_with_last_contact() | §2.5 |
| 保護ストレージの取得 | get_protected_storage() | §4.1 |
| 添付の保存パス生成 | attachment_upload_to() | §4.1 |
| 会社の一括統合 | execute_company_merge() | §6.5.5 |
| 重複候補ペアを「別会社」と判定 | mark_as_different_company() | §6.5.5.1 |
| 会社の付け替え（1社分） | transfer_contacts_to_company() | §6.5.5 |
| ドメインの穴埋め | maybe_fill_company_domain() | §6.5.6 |
| 案件の閲覧可否（単体／一覧） | can_view_deal()／visible_deals_for() | §7.1 |
| 案件の編集可否／アーカイブ可否 | can_edit_deal()／can_archive_deal() | §7.1 |
| 案件のアーカイブ実行 | archive_deal() | §7.1 |
| 活動の閲覧可否（単体／一覧） | can_view_activity()／visible_activities_for() | §7.3 |
| 活動の編集可否／アーカイブ可否 | can_edit_activity()／can_archive_activity() | §7.3 |
| 活動のアーカイブ実行 | archive_activity() | §7.3 |
| 添付の閲覧／編集／削除可否 | can_view_attachment()／can_edit_attachment()／can_delete_attachment() | §7.4 |
| 全案件閲覧権限／全案件編集権限 | deals.view_all_deals／deals.edit_all_deals | §7.1 |
| 全活動閲覧権限／全活動編集権限 | activities.view_all_activities／activities.edit_all_activities | §7.3 |
| 会社統合権限 | companies.merge_company | §7.5 |
