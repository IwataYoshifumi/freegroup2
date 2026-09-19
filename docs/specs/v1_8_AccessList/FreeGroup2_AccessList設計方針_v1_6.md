# FreeGroup2 AccessList設計方針 v1.6

## 改訂履歴

| バージョン | 日付 | 内容 |
|---|---|---|
| v0.1 | 2026-09-13 | 初版作成。たんたんとの壁打ちで確定した論点を反映 |
| v0.2 | 2026-09-13 | レビュー反映。8.3.3のSQL修正（Contactあり/なし混在対応）、8.3.4をcreated_by特例の対象をContact単体に限定する方針に変更、複数AccessList紐付け時の合成ルール追加、ACLEntry「未設定」時のキャッシュ非作成を明記、GenericForeignKey対象ID型をCharFieldに統一、deal_list/person_listをNOT NULL化、権限レベルの権能定義表を追加、その他軽微な表記統一 |
| v0.3 | 2026-09-13 | 旧FG調査結果の再確認により、DealList／PersonListとAccessListの関係を**M2MからFK（多対1）に変更**。理由：旧FGは`accesslistables`テーブル自体はポリモーフィックM:N構造だったが、実装コードは`access_lists->first()`のみを使用しており、実運用は常に「1コンテナ：1アクセスリスト」だったことが調査結果から判明したため。これに伴いv0.2の第5.4節（複数AccessList合成ルール）を削除。あわせて、8.3.4の画面制御ルール明記、ACLEntry並び順のタイブレーク規則確定、DealList/PersonList/UserGroupの管理権限（機能レベル）明記、マージ処理内でのPersonList更新タイミング明記を追加 |
| v0.4 | 2026-09-13 | 8.3.4のcreated_by特例を再設計：BusinessCard.card_image（トリミング済み1名刺画像）とOriginalImage.image_file（複数名刺を含み得る元画像）を分離し、特例が及ぶのは前者のみとした（後者は8.3.3の積集合判定を必ず経由し、特例によるすり抜けを防止）。あわせて「Deal履歴を一律マスキング」という誤った記述を削除し、Deal自身のAccessListで独立判定する旨に修正。created_by特例の一覧画面（ListView）での非表示方針を明記。PersonMergeLogにマージ前PersonList退避フィールドを追加しマージ取り消し時の復元手順を明記。AccessList自体の管理権限（機能レベル）を第3.4節に追加。NOT NULL FK追加時のマイグレーション手順（3ステップ）を明記。AccessListUserRole再計算のtransaction.atomic()必須化を明記 |
| v0.5 | 2026-09-13 | **PersonMergeLog関連の再計算トリガを削除**（Person側のリスト所属変更はAccessListUserRoleキャッシュに影響しないため、マージ・マージ取り消し時の再計算自体が不要と判明）。**部署異動・UserGroup所属変更・Department親変更のトリガについて、変更前の所属も再計算対象に含めるよう修正**（従来の記述では異動元・変更前側に古い権限行が残留するバグを内包していたため）。8.3.4の403判定範囲を明確化（自作Contact＋親Person画面に限定、他人のContactは従来通り403/404）、「検索結果からの直リンク」の曖昧な表現を削除し活動履歴・通知経由に限定。マージUndo時のNULLフォールバック（既存ログのperson_list_before_merge欠落への対応）を追加。§3.4をRole→Group→Permission構造に合わせて配布先をGroup単位の記述に修正。第4.7節を新設し、新設モデルの配置アプリ・related_nameを明記。8.3.3にBusinessCardの逆参照名確認事項を追加。Personの基本属性表示に関する既知の軽微な妥協点を明記 |
| v0.6 | 2026-09-13 | **`rebuild_for_department()`の意味を再定義**（「departmentの現在の所属ユーザーを数え上げる」実装ではv0.5の即時剥奪修正が空振りするため、「departmentまたはその祖先を対象とするACLEntryを持つAccessListを特定し、rebuild_for_access_list()で全再評価する」方式に明確化）。**「Departmentの親変更」トリガの方向を訂正**（配下ではなく祖先チェーンを辿るべきことを明記）。マージ実行のService層データレベル権限チェック（両PersonListに編集者以上必須）を第10.3節として新設。PersonDetailViewのDeal一覧は`Deal.objects.visible_for()`経由必須と明記。AccessListUserRoleに`(user, access_list)`複合インデックスを追加。`m2m_changed`シグナルの`instance`型分岐トラップを明記。新設モデル全てのMeta.orderingを第4.8節として新設 |
| v0.7 | 2026-09-13 | **退職・復職（is_active変更）・CustomUser作成のトリガを修正**（v0.6で`rebuild_for_user()`の役割を個人直接指定のみに縮小した副作用で、Department／UserGroup経由でしか権限を持たないユーザーの即時剥奪・初期付与が漏れる不具合を修正。`rebuild_for_user()`に加え`rebuild_for_department()` / `rebuild_for_user_group()`を明示的に呼ぶ実装パターンを追加）。DealForm／PersonFormから`deal_list`／`person_list`を除外し管理者限定の付け替えとするフォーム層の制御を明記。`DealQuerySet.visible_for(user)` / `PersonQuerySet.visible_for(user)`の統一メソッドを新設し、View層でのクエリ手書きを禁止。マージ時、被統合側（merged_person）の`person_list`は変更せず元の値を維持する旨を明記 |
| v0.8 | 2026-09-13 | **DealList／PersonListのFKに`blank=True`を追記**（ModelFormから除外したNOT NULLフィールドは`blank=True`がないと`is_valid()`で必須エラーとなり保存できないDjango特有のトラップに対応）。**退職・復職・新規作成トリガの重複再計算を排除**：`rebuild_for_user_status_change(user)`という統合メソッドを新設し、対象AccessList IDをsetで集約してから重複排除して1回ずつ再計算する方式に変更（部署とUserGroupが同じAccessListを参照するケースでの無駄な再計算を防止）。`DealQuerySet.visible_for()` / `PersonQuerySet.visible_for()`にスーパーユーザー・全社閲覧権限保持者の特権バイパス（早期リターン）を追加。ACLEntryに`(target_content_type, target_object_id)`の複合インデックスを追加 |
| v0.9 | 2026-09-13 | **CustomUserシグナルの発火ガードを追加**（ログイン等の`last_login`更新でも`post_save`が無条件発火し、毎回全再計算が走る致命的な負荷を防ぐため、`pre_save`で変更前値を捕捉し実際に`is_active`が変化した場合のみ再計算する設計に修正）。**特権バイパスを`AccessListService.accessible_person_list_ids()` / `accessible_deal_list_ids()`という共通ヘルパーに集約**し、v0.8で`visible_for()`にしか適用されていなかった特権バイパスを第8.3.3節（OriginalImage積集合判定）・第10.2節（DuplicateCandidateレビュー可否判定）にも伝播させ、一覧では見えるのに詳細操作では弾かれるという矛盾を解消。`DealQuerySet.visible_for()`に`.distinct()`（M2M結合による行重複対策）と未認証ガードを追加。ルート部署が存在しない環境（CI・新規構築時）でのデータマイグレーション防御を第9.1節に追加。PersonDetailViewの既存リダイレクト仕様（active時はContactDetailViewへ302）とcreated_by特例の整合性を第8.3.4節に追加。GFKクエリの`target_object_id`比較時のPostgreSQL型キャスト（`str(pk)`明示）の注意を第6.2節に追加 |
| v1.0 | 2026-09-13 | 最終仕上げ。**`accessible_person_list_ids()` / `accessible_deal_list_ids()`自体に未認証ガードを追加**（`visible_for()`を経由しない第8.3.3節・第10.2節での直接呼び出しが500エラーになる穴を修正）。**部署異動とis_active変更のシグナルハンドラを1つに統合**（`user_pre_save` / `user_post_save`、重複クエリと接続漏れの防止）。`rebuild_for_department()`の祖先探索に「department自身のID」を含め忘れるバグパターンへの注記を追加。DealAdmin／PersonAdminでの`get_readonly_fields()`によるAdmin画面経由の付け替え迂回防止を第4.5節・第4.6節に追加。created_by特例のリダイレクト先が複数存在する場合のタイブレーク規則（`created_at`降順）を第8.3.4節に追加。第10.2節のQuerySet使い回しに関するコメントを実際のDjangoの遅延評価の挙動に合わせて訂正。`persons.view_all_persons` / `deals.view_all_deals`が既存権限か新設が必要かの確認注記を第6.2節に追加 |
| v1.1 | 2026-09-13 | 実装指示書化に向けた最終申し送り3点。**`accessible_*_list_ids()`の未認証時の戻り値に`.values_list('id', flat=True)`を統一**（他の分岐と要素の型を揃える）。**マイグレーションの依存関係を第4.7節に追加**（`deals`／`persons`の新規マイグレーションが`permissions`アプリに依存することを明記し、CI環境でのテーブル未作成エラーを防止）。**ActionLog記録の実装チェックリストを第4.9節として新設**（AccessList作成・更新、DealList／PersonListの付け替え時の監査ログ記録） |
| v1.2 | 2026-09-19 | **旧FG(PHP/Laravel版)調査結果を踏まえ、DealList／PersonListの新規レコード作成方式を「未指定時にデフォルトリストを自動設定」から「編集者以上の権限を持つリストから必ず選択させる」方式へ変更**（第4.5節・第4.6節）。これに伴い`blank=True`を撤回し`blank=False`（デフォルト）に戻す。**編集者以上限定の選択肢を返す`editable_person_list_ids()` / `editable_deal_list_ids()`を第6.2節に新設**。**旧FG「日報リスト」の編集範囲設定（作成者のみ／作成者・参加者／作成者・参加者・リスト編集者全員）を移植**：DealList・PersonListに「編集範囲」フィールドを新設し、`AccessListService.can_edit_deal()` / `can_edit_person()` / `can_edit_contact()`を第6.2節に新設（第3.3節の権限レベルとは別軸、AND条件で作用）。デフォルト値は`all_editors`で導入前の挙動と完全互換。旧FG実機（日報リスト編集画面）で「日報変更権限　初期値」が作成者／作成者・参加者／作成者・参加者・リスト編集者全員の3択セレクトボックスであることを実機確認し、本節の設計と一致することを確認。**Contact.created_by特例（第8.3.4節）は編集も許可**する旨を明記（自分が作成したContact単体に限る、編集範囲設定より優先）。**OriginalImageアップロード時にPersonList選択を必須化**し、OCR完了後の新規Person生成に引き継ぐフローを第8.3.5節として新設（本編仕様書側のOriginalImageモデルへのFK追加が前提、要調整）。**編集可能なリストが0件のユーザーは新規作成不可**（エラーで弾く。救済ロジックは設けない）という運用方針を第9.1節に明記。**第10.1節のマージ時警告に「編集範囲設定の相違」を追加**。CSVインポート（v1.7）は「インポート前に1つPersonListを選択し、当該回の全件に一括適用」する方式で本書と整合させる旨を第8.3.6節として新設（`FreeGroup2_インポートエクスポート機能_仕様書_v1_6`側との突合が別途必要） |
| v1.3 | 2026-09-19 | **レビュー指摘（ジット君・GPT君、双方一致）に基づく重大修正**。**【最重要・権限昇格バグ修正】`editable_person_list_ids()` / `editable_deal_list_ids()` / `can_edit_deal()` / `can_edit_person()` / `can_edit_contact()`の特権バイパス条件を、閲覧専用権限`view_all_persons` / `view_all_deals`から、新設する編集専用権限`persons.edit_all_persons` / `deals.edit_all_deals`に差し替え**（第6.2節）。閲覧目的で付与されたはずの権限が、v1.2で新設した「編集範囲」制限を丸ごとバイパスしてしまう事故を防止するため。`accessible_*_list_ids()`（閲覧用、一覧表示）は従来通り`view_all_*`のままでよく、変更しない。**【論理矛盾修正】`can_edit_deal()`を「まず該当DealListの編集者以上であることを大前提として判定し、その上で編集範囲設定によりさらに絞り込む」構造に修正**（第6.2節）。旧コードは`creator_only` / `creator_and_participants`の分岐でリスト自体のAccessListUserRoleを一切検証しておらず、そのDealListへのアクセス権を失った旧ownerや、閲覧目的で参加者登録されただけのDealUserが編集をすり抜けられる欠陥があった。**Dealにおける「作成者」の定義を明記**：編集範囲`creator_only` / `creator_and_participants`における「作成者」は、起票者`Deal.created_by`ではなく現在の案件オーナー`Deal.owner`を指す（第4.5節・第6.2節に注記追加、`FreeGroup2_案件管理機能_仕様書_v1_10` §7.1の「引き継いだ前担当者には編集権を残さない」方針との整合）。**DealFormへの`deal_list`追加の申し送りを第4.5節に追加**（`FreeGroup2_案件管理機能_仕様書_v1_10` §2.1のDealForm定義例に`deal_list`が含まれていないため、AccessList導入時にフォームへの追加と`editable_deal_list_ids(user)`によるqueryset絞り込みが必要である旨を明記。これを怠るとDeal新規作成画面が保存不能になる）。**`can_edit_person()`のdocstringを実装の実際の挙動（creator_only時はFalseを返すのみで、Contact単体判定への委譲は呼び出し側の責務）に一致するよう修正**。**第8.3.4節に、created_by特例は機能レベル権限（`contacts.change_contact`）の保有を前提とする旨を明記**（データレベル権限のみをバイパスする特例であり、機能レベル権限自体は不要にならない） |
| v1.4 | 2026-09-19 | **UI画面・ルートの骨格を第2.3節として新設し、次フェーズ送りの対象から外す**。データモデル・評価ロジックのみでは運用開始できない（AccessList／ACLEntry／UserGroup／DealList／PersonListを作成・編集する画面が存在しなければ、Step 1〜3を実装しても初日から業務が回らない）という指摘を受け、CRUD画面のルート・View名・入力項目の骨格を本書のスコープに含めることとした。画面のレイアウト・配色等の具体的なデザイン（HIG・app.css/app.js v3.1.1準拠）は引き続き別途（第2.2節に残す）が、**ルート自体の存在は次フェーズ送りとしない**。新設24ルート（AccessList管理9、UserGroup管理7、DealList/PersonList管理8）と、既存6画面への項目追加（Deal作成画面・ContactCreateView・OriginalImageUploadView・CSVインポート画面・マージレビュー画面・Deal/Personの管理者限定付け替え機能）を第2.3節に整理した |
| v1.5 | 2026-09-19 | **レビュー指摘（GPT君）に基づく修正**。DealListCreateView／DealListUpdateView／PersonListCreateView／PersonListUpdateView（第2.3.3節No.18〜19、22〜23）の「アクセスリスト」選択欄の選択肢が、これまで絞り込み条件を持たず全AccessListを表示していた点を修正。DealList管理者・PersonList管理者は、AccessList自体の管理権限（第3.4節`permissions.add_accesslist`等、より狭いGroup）を持つとは限らないため、無条件に全AccessList（機密性を示唆する名前を含み得る）を選択肢に出すのは、本書が一貫して守ってきた「見えてはいけないものを見せない」方針に反すると判断した。**`AccessListService.accessible_access_list_ids(user)`を第6.2節に新設**（`accessible_person_list_ids()` / `accessible_deal_list_ids()`と同型、そのAccessListに対して閲覧者以上の権限を持つもののみを返す）し、上記4画面のアクセスリスト選択肢をこれで絞り込むことを第2.3.3節に明記した |
| v1.6 | 2026-09-19 | **レビュー指摘（ジット君・GPT君）に基づく修正**。**【最重要】DealListUpdateView／PersonListUpdateViewで、現在設定されているAccessListが`accessible_access_list_ids(user)`の絞り込みに含まれない場合の挙動が未定義だった点を修正**（第2.3.3節「Update画面での現在値の扱い」を新設）。v1.5の絞り込み修正自体が、DjangoのModelChoiceFieldの仕様（querysetに現在値が含まれないと選択済み状態を描画できず、フォーム送信時に別のAccessListへ気づかないまま付け替わってしまう）に起因する新たな事故要因を持ち込んでいたため、現在値が絞り込みに含まれない場合はアクセスリスト選択欄を読み取り専用にし、案内文を表示する方式に確定した。**`permissions.view_all_access_lists`をAccessListの`Meta.permissions`に明示宣言し、配布対象に含める旨を第6.2節に実装上の申し送りとして追加**（ジット君指摘） |

---

# 第1章 本ドキュメントの位置付け

本書は、FreeGroup2（FG2）における**データレベル権限（AccessList）**の設計方針を、たんたん（岩田好史）との議論結果としてまとめたものである。

姉妹資料：

- `FreeGroup_PHP版_アクセスリスト機構_調査結果`（ジット君による旧FreeGroup(PHP/Laravel版)のACL実装調査。`AccessListUserRoleUpdate`の再計算ロジック、`accesslistables`（morphedByMany）テーブルの実態、Group/Calendar/ReportList/TaskList/Fileの新規作成時のAccessList選択方式（フォームでの単一選択、`sync([$request->access_list_id])`）、日報リストの「編集範囲」設定（作成者／作成者・参加者／作成者・参加者・リスト編集者全員の3択セレクトボックス、実機`freegroup.work`のスクリーンショットで確認済み）等の一次情報。v1.2はこの調査結果を反映）
- `_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_3`（機能レベル権限・Department・UserGroup等の土台設計。本書はこれを前提とする。`is_staff`分離原則（§6.1）・Role/Group/Permission三段構造（§6）も本書の権限判定はここに従う）
- `FreeGroup2_案件管理機能_仕様書_v1_10`（Deal/Activity/Companyの現行仕様。DealUser/ActivityUserはAccessList導入後も存続する前提で設計済み）
- `FreeGroup2_インポートエクスポート機能_仕様書_v1_6`（CSVインポート機能。v1.2で新設した第8.3.6節の内容と突合が必要）

本書は上記4資料の内容と競合しない。競合が判明した場合は個別に協議のうえ、該当資料側を改訂する。

**重要な前提の確認**：本書で扱うPerson/Contactのマージ処理は、`FreeGroup2本編仕様書_v1_6_3` §9.4.2の通り、マージド側Personに紐づく全Contactをサバイブ側Personへ**FK付け替えする**設計である。これはメール配信機能（v1.6メルマガ仕様書）で採用されている「A-1原則＝マージ時FK付け替えゼロ」とは**別の原則**であり、混同しないこと（A-1原則はDeliveryHistory等メール配信専用の6モデルにのみ適用される）。したがって`contact.person`は常に最新の生存Personを指しており、本書の各所で`get_surviving_person()`を明示的に呼び出す必要はない。

# 第2章 スコープ

## 2.1 本ドキュメントで確定する範囲

- AccessList／ACLEntry／AccessListUserRoleのモデル定義と評価ロジック
- UserGroup・DealList・PersonListの新設
- Department階層継承の扱いとgrant/revoke相当の例外処理
- 再計算トリガの設計（イベント駆動＋cronによる自己修復の役割分担）
- 案件（Deal）・活動履歴（Activity）・パーソン階層（Person/Contact/BusinessCard/OriginalImage）への適用方法
- 導入時のデフォルトリストと既存データ移行方針
- Person統合（マージ）時にPersonListが競合する場合の扱い
- **新規レコード（Deal／Person）作成時のリスト選択方式、およびリスト単位の編集範囲設定（v1.2で追加）**
- **AccessList／ACLEntry／UserGroup／DealList／PersonListのCRUD画面のルート・View名・入力項目の骨格（v1.4で追加、第2.3節）**

## 2.2 引き続き含まないもの（次フェーズ送り）

| 項目 | 理由 |
|---|---|
| 制限閲覧者（freeBusyReader）相当 | 旧FG側でも仕様未完（HP記載はあるが判定ロジックに組み込まれていない）。本書では扱わない |
| UI画面の具体的なデザイン（レイアウト・配色・ワイヤーフレーム） | HIG・app.css/app.js v3.1.1準拠を前提とするが、画面デザイン自体は別途。**ルート・View名・入力項目の骨格自体は第2.3節でスコープに含めた（v1.4）** |
| ACLEntry並び順の操作性強化（ドラッグ&ドロップ等） | 初期リリースは素朴な入れ替えUI（第2.3節No.9）で足りると判断。操作性向上は次フェーズ |
| `Department.descendants()`のmptt/treebeard/CTEへの置換 | 現時点では不要と判断（第7章参照）。将来の規模拡大時に再検討 |
| Personの「参加者」概念の新設 | v1.2で旧FGの3段階編集範囲設定を検討したが、Personには参加者に相当する概念がないため2段階（作成者のみ／リスト編集者全員）に簡略化して確定。DealUserに相当する新モデルは導入しない |

## 2.3 UI画面・ルート一覧（v1.4で新設）

データモデル・評価ロジックだけでは運用を開始できない（AccessList／ACLEntry／UserGroup／DealList／PersonListを実際に作成・編集する画面がなければ、Step 1〜3を実装しても初日から使えない）ため、CRUD画面のルート・View名・入力項目の骨格を本節で確定する。画面のレイアウト・配色等の具体的デザインはHIG・app.css/app.js v3.1.1準拠を前提に別途行う（第2.2節）。

### 2.3.1 AccessList管理系（新規画面）

| No. | ルート | メソッド | View名 | 備考 |
|---|---|---|---|---|
| 1 | `/access-lists/` | GET | AccessListListView | 一覧 |
| 2 | `/access-lists/create/` | GET/POST | AccessListCreateView | 名前・説明のみ |
| 3 | `/access-lists/<uuid:pk>/` | GET | AccessListDetailView | ACLEntry一覧を表示するメイン画面。並び順（第5.1節）通りに表示すること |
| 4 | `/access-lists/<uuid:pk>/update/` | GET/POST | AccessListUpdateView | 名前・説明編集 |
| 5 | `/access-lists/<uuid:pk>/delete/` | POST | AccessListDeleteView | DealList／PersonListからPROTECT参照されている間は削除不可（画面上でエラーメッセージ表示） |
| 6 | `/access-lists/<uuid:pk>/entries/create/` | GET/POST | ACLEntryCreateView | 対象（個人／部署／UserGroup）・権限レベル・並び順の追加 |
| 7 | `/access-lists/<uuid:pk>/entries/<uuid:entry_pk>/update/` | GET/POST | ACLEntryUpdateView | |
| 8 | `/access-lists/<uuid:pk>/entries/<uuid:entry_pk>/delete/` | POST | ACLEntryDeleteView | |
| 9 | `/access-lists/<uuid:pk>/entries/reorder/` | POST（AJAX） | ACLEntryReorderView | 並び順の入れ替え。第5.1節「先勝ちルール」の要であり、誤操作時の影響が大きいため、保存前の確認表示を設けること |

### 2.3.2 UserGroup管理系（新規画面）

| No. | ルート | メソッド | View名 | 備考 |
|---|---|---|---|---|
| 10 | `/user-groups/` | GET | UserGroupListView | |
| 11 | `/user-groups/create/` | GET/POST | UserGroupCreateView | |
| 12 | `/user-groups/<uuid:pk>/` | GET | UserGroupDetailView | メンバー一覧 |
| 13 | `/user-groups/<uuid:pk>/update/` | GET/POST | UserGroupUpdateView | |
| 14 | `/user-groups/<uuid:pk>/delete/` | POST | UserGroupDeleteView | |
| 15 | `/user-groups/<uuid:pk>/add-member/` | POST（AJAX） | UserGroupAddMemberView | |
| 16 | `/user-groups/<uuid:pk>/remove-member/` | POST（AJAX） | UserGroupRemoveMemberView | |

**画面上の注意書き**：第4.4節の運用上の注意（UserGroupはDepartment異動と自動連動しない）を、UserGroup編集画面に注意書きとして表示すること。

### 2.3.3 DealList／PersonList管理系（新規画面）

| No. | ルート | メソッド | View名 | 備考 |
|---|---|---|---|---|
| 17 | `/deal-lists/` | GET | DealListListView | |
| 18 | `/deal-lists/create/` | GET/POST | DealListCreateView | 名前・説明・アクセスリスト選択・編集範囲選択（3択）。**アクセスリストの選択肢は`AccessListService.accessible_access_list_ids(user)`（第6.2節、v1.5で新設）で絞り込む**（DealList管理者がAccessList管理者とは限らないため） |
| 19 | `/deal-lists/<uuid:pk>/update/` | GET/POST | DealListUpdateView | 同上（アクセスリスト選択肢の絞り込みも同様。**現在値がその絞り込みに含まれない場合の扱いは下記「Update画面での現在値の扱い（v1.6）」参照**） |
| 20 | `/deal-lists/<uuid:pk>/delete/` | POST | DealListDeleteView | 所属Dealが存在する間はPROTECTにより削除不可 |
| 21 | `/person-lists/` | GET | PersonListListView | |
| 22 | `/person-lists/create/` | GET/POST | PersonListCreateView | 名前・説明・アクセスリスト選択・編集範囲選択（2択）。**アクセスリストの選択肢は`AccessListService.accessible_access_list_ids(user)`で絞り込む（同上）** |
| 23 | `/person-lists/<uuid:pk>/update/` | GET/POST | PersonListUpdateView | 同上（アクセスリスト選択肢の絞り込みも同様。**現在値の扱いは下記参照**） |
| 24 | `/person-lists/<uuid:pk>/delete/` | POST | PersonListDeleteView | 所属Personが存在する間はPROTECTにより削除不可 |

**Update画面での現在値の扱い（v1.6で新設・重要）**：DealListUpdateView／PersonListUpdateViewのアクセスリスト選択欄は`accessible_access_list_ids(user)`で絞り込むが、そのDealList／PersonListに**現在設定されているAccessListが、たまたまこの絞り込みに含まれない**ケースがあり得る（例：以前は別の管理者が閲覧権限外のAccessListを割り当てていた場合）。DjangoのModelChoiceFieldは、querysetに現在値が含まれないと選択済み状態を描画できず、フォーム送信時に一覧の先頭など別のAccessListへ**気づかないまま黙って付け替わってしまう**事故が起きる。

これを防ぐため、以下の方式とする。

- フォーム側で、対象インスタンスの現在のアクセスリストIDが`accessible_access_list_ids(user)`に含まれるかを事前判定する
- 含まれない場合、アクセスリスト選択欄を**読み取り専用**にし、「このリストのアクセスリストを変更するには、アクセスリスト管理権限を持つ人に依頼してください」等の案内を表示する（現在値の名前をあえて選択肢に混ぜ込む方式は取らない。「見えてはいけないものを見せない」という本書の一貫した方針を優先する）
- 名前・編集範囲など他の項目は、アクセスリスト欄が読み取り専用であっても通常通り編集・保存できるようにする

### 2.3.4 既存画面への項目追加（新規ルートではない）

| 対象画面 | 追加内容 | 関連節 |
|---|---|---|
| Deal作成画面 | `deal_list`選択を必須項目として追加。選択肢は`editable_deal_list_ids(user)`で絞り込む | 第4.5節 |
| ContactCreateView（名刺なしでのPerson新規作成） | `person_list`選択を必須項目として追加。選択肢は`editable_person_list_ids(user)`で絞り込む | 第4.6節 |
| OriginalImageUploadView（名刺画像アップロード） | `person_list`選択を必須項目として追加 | 第8.3.5節 |
| CSVインポート画面（`FreeGroup2_インポートエクスポート機能_仕様書_v1_6`側、v1.7で実装予定） | インポート実行前に`person_list`選択ステップを追加し、当該回の新規作成分全件に適用 | 第8.3.6節 |
| DuplicateCandidateGroupUpdateView（マージレビュー画面） | 両Personの編集範囲設定が異なる場合の警告表示を追加 | 第10.1節 |
| Deal／Personの一覧・詳細画面（管理者限定の付け替え操作） | 既存Deal／Personが属するDealList／PersonListを付け替える専用機能を新設（一般ユーザー向け編集画面とは別立て） | 第4.5節・第4.6節 |

---

# 第3章 概念モデル・用語定義

FG2の権限は「機能レベル権限」と「データレベル権限」の2層構造である。

```
[機能レベル権限] ← Role + PermissionSet（Django標準Permission/Group）
  例：「案件を編集する機能」が使えるか

[データレベル権限] ← AccessList
  例：「この案件（Deal）」を見れる・編集できるか
```

両方を満たして初めて、特定のレコードを編集できる（AND条件）。

**v1.2での追加**：データレベル権限のうち「編集」については、さらに**リスト単位の編集範囲設定**（第4.5節・第4.6節、第6.2節）による絞り込みが加わる。これは第3.3節の権限レベル（管理者／編集者／閲覧者）とは別軸であり、「編集者」権限を持っていても、編集範囲設定が「作成者のみ」であれば、作成者本人以外は編集できない（AND条件でさらに絞られる）。

```
[機能レベル権限] AND [データレベル権限：権限レベル] AND [データレベル権限：編集範囲]
  → この3つをすべて満たして初めて編集できる
```

## 3.1 用語

| 用語 | 説明 |
|---|---|
| AccessList | 権限の集合を表す独立したエンティティ。名前・説明を持つ。複数のDealList／PersonListから参照され、使い回される |
| ACLEntry | AccessList内の1行。対象（個人／Department／UserGroup）と権限レベル、評価順序（order）を持つ |
| AccessListUserRole | ACLEntryを展開して確定した「ユーザーごとの最終的な権限レベル」を保持するフラット化キャッシュ |
| UserGroup | 業務管理者が手動で作る横断グループ（LDAP由来のLdapGroupとは別物）。ACLEntryの対象種別の1つ |
| DealList | 案件（Deal）のコンテナ。1つのAccessListをFKで参照する。編集範囲設定（v1.2）を持つ |
| PersonList | パーソン（Person）のコンテナ。1つのAccessListをFKで参照する。旧称「BusinessCardList」から改称（名刺を持たないPersonが存在するため）。編集範囲設定（v1.2）を持つ |

## 3.2 全体関係図（概念）

```
DealList   → FK → AccessList  （複数のDealListが同じAccessListを参照できる＝使い回し）
PersonList → FK → AccessList  （同上）

DealList   → Deal（多数）
PersonList → Person（多数） → Contact → BusinessCard → OriginalImage
                                              ↑
                                     （委譲できないケースは
                                      アップロード者本人＋機能レベル権限で判定）

ACLEntry.target → CustomUser / Department（階層継承あり） / UserGroup
```

**v0.3での変更**：v0.2まではDealList／PersonListとAccessListをM2Mで結んでいたが、FK（多対1）に変更した。理由は第4.5節・第4.6節を参照。

## 3.3 権限レベルの権能定義

データレベル権限（AccessList側）が許容する操作範囲を明確にする。実際に操作できるかは、ここに機能レベル権限（`contacts.change_contact`等）のAND判定が加わって最終決定される点に注意。**v1.2以降は、これに加えて第4.5節・第4.6節の「編集範囲」設定によるAND判定も加わる**（後述の権能表は「リスト編集者全員」設定時の挙動そのものであり、他の設定ではさらに絞られる）。

| 権限レベル | 閲覧 | 編集・追加 | 削除 | リスト自体の設定変更 | 他者への権限管理 |
|---|---|---|---|---|---|
| 管理者 | ○ | ○ | ○ | ○ | ○ |
| 編集者 | ○ | ○ | ✕ | ✕ | ✕ |
| 閲覧者 | ○ | ✕ | ✕ | ✕ | ✕ |
| 制限閲覧者 | 次フェーズ送り（2.2参照） | | | | |
| 未設定 | ✕ | ✕ | ✕ | ✕ | ✕ |

## 3.4 リスト・グループの管理権限（機能レベル）

「DealList／PersonList／UserGroupそのものを新設・削除できるのは誰か」は、データレベル権限（AccessList内の管理者権限）とは別の、**機能レベル権限**で管理する。一般社員（sales／viewerロール）がリストを乱立させないよう、管理者ロール限定とする。

**配布方法についての注記（v0.5で追加）**：FG2の認可基盤では、Roleは直接Permissionを持たず、`Role → Group（default_groups） → Permission`という構造を取る（`_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_3`参照）。したがって以下の権限は、実際にはDjangoのGroupに割り当て、そのGroupをadminロールの`default_groups`に含める形で配布する。

| 対象 | 必要な機能レベル権限 | 配布先Group |
|---|---|---|
| AccessList | `permissions.add_accesslist` / `permissions.change_accesslist` | 既存の`user_admin`Group、または新規`permission_admin`Groupを作りadminロールの`default_groups`に追加する（ACLEntryの中身自体を直接編集できる権限のため、配布範囲は最も狭くする） |
| UserGroup | `accounts.add_usergroup` / `accounts.change_usergroup` | 既存の`user_admin`Group（`user_admin`相当のadminロール`default_groups`） |
| DealList | `deals.add_deallist` / `deals.change_deallist` | `deal_admin`Group（`deal_admin`ロールの`default_groups`） |
| PersonList | `persons.add_personlist` / `persons.change_personlist` | Personの管理者Group（既存Group名はコード君が実装時に現物確認） |

**補足（v1.2）**：`is_staff`は上記いずれの機能レベル権限とも独立である（`_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_3` §6.1「is_staff分離原則」）。`is_staff=True`はDjango Admin（`/admin/`）への入口ガードのみを意味し、単独ではACL関連の編集権限を持たない。Django Admin上でACLEntry等を編集するには`is_staff=True`に加えて上表の機能レベル権限が必要であり、`is_superuser=True`はこれらすべてを自動的に満たす（Djangoの`has_perm()`が常にTrueを返すため）。AccessList管理者が組織内に1人もいなくなった場合の最終的な救済経路は、この機能レベル権限保持者またはスーパーユーザーである。

---

# 第4章 モデル定義

日本語のモデル名・フィールド名で記述する。コーディング名との対照は巻末別表を参照。

## 4.1 AccessList（アクセスリスト）

| フィールド名 | 型 | 説明 |
|---|---|---|
| ID | UUIDField (PK) | |
| 名前 | CharField(100) | 例：「営業部と社長」「役員限定」 |
| 説明 | TextField (blank) | |
| 作成者 | FK(CustomUser, PROTECT) | |
| 作成日時 | DateTimeField (auto_now_add) | |
| 更新日時 | DateTimeField (auto_now) | |

## 4.2 ACLEntry（アクセスリスト内エントリ）

| フィールド名 | 型 | 説明 |
|---|---|---|
| ID | UUIDField (PK) | |
| アクセスリスト | FK(AccessList, CASCADE) | |
| 並び順 | PositiveIntegerField | 昇順で評価。初出のユーザーが該当行の権限レベルを採用（先勝ち） |
| 権限レベル | CharField(choices) | 管理者／編集者／閲覧者／制限閲覧者（未実装）／未設定 |
| 対象種別 | ForeignKey(ContentType) | GenericForeignKeyの型部分 |
| 対象ID | CharField(64) | GenericForeignKeyのID部分。CustomUser（標準の整数PK）とDepartment／UserGroup（UUID PK）でPK型が混在するため、文字列型に統一して両対応する |
| 対象 | GenericForeignKey | CustomUser／Department／UserGroupのいずれかを指す |
| 作成日時 | DateTimeField (auto_now_add) | |
| 更新日時 | DateTimeField (auto_now) | |

**権限レベル「未設定」の用途**：Department階層継承によって自動的に付与された権限を、特定のUserGroupやユーザー個人に対してだけ打ち消したい場合に使う。「未設定」のエントリを打ち消したい対象より上位の順序に置くことで、先勝ちルールにより上書きできる（第5章参照）。この仕組みにより、grant/revokeという別軸のフラグは持たない。**並び順評価の結果「未設定」が採用されたユーザーについては、AccessListUserRoleにレコードを作成しない（スキップする）**。レコードが存在しないことをもって「権限なし」とする（第5.1節）。

**v1.2での確認事項**：この「未設定」による打ち消しは、対象ユーザーがデフォルトAccessList（第9.1節）からの権限すら失う結果になり得る。これは意図的なフェイルセーフとして許容する方針を第9.1節で確定した（編集可能なリストが0件になるユーザーの存在を許容し、新規作成はエラーで弾く。救済ロジックは設けない）。

**GenericForeignKeyの孤児化対策**：CustomUser／Department／UserGroupが削除された場合、Django標準ではGenericForeignKey経由のCASCADEは発火しない。各モデルの`post_delete`シグナルで、対応するACLEntryを明示的に削除するハンドラを`apps/permissions/signals.py`に実装する。

**インデックス（v0.8で追加）**：`rebuild_for_department()` / `rebuild_for_user_group()`（第6.2節）は、対象のDepartment（およびその祖先）やUserGroupから逆引きして該当するACLEntryを検索する。`対象種別`（`target_content_type`）と`対象ID`（`target_object_id`）の組み合わせにインデックスがないと、部署ツリーの判定のたびにACLEntryテーブルのフルスキャンが発生する。`Meta.indexes`に`models.Index(fields=['target_content_type', 'target_object_id'])`を追加すること。

## 4.3 AccessListUserRole（フラット化キャッシュ）

| フィールド名 | 型 | 説明 |
|---|---|---|
| ID | UUIDField (PK) | |
| アクセスリスト | FK(AccessList, CASCADE, related_name='user_roles') | |
| ユーザー | FK(CustomUser, CASCADE) | |
| 権限レベル | CharField(choices) | 管理者／編集者／閲覧者 |
| 作成日時 | DateTimeField (auto_now_add) | |

制約：UniqueConstraint(アクセスリスト, ユーザー)。

**インデックス（v0.6で追加）**：`UniqueConstraint(access_list, user)`により`(access_list, user)`順の複合インデックスは自動生成されるが、実際の認可チェックで最も頻繁に実行されるクエリ（例：`...__access_list__user_roles__user=user`）は`user`起点の逆引きである。`Meta.indexes = [models.Index(fields=['user', 'access_list'])]`を明示的に追加すること。

再計算のたびに該当AccessList分を全削除→全挿入する（PHP版`AccessListUserRoleUpdate`と同じ、差分更新ではない）。

**トランザクションの明記（v0.4で追加）**：全削除→全挿入の間にリクエストが来ると、その瞬間だけ該当AccessListの権限が全員分消えた状態になり得る。`AccessListService.rebuild_for_access_list()`は、削除と挿入を含む処理全体を必ず`transaction.atomic()`で囲むこと。

**フィールド名の使い分けについて**：ACLEntryの「権限レベル」（コーディング名`permission_level`、未設定を含みうる生の設定値）と、AccessListUserRoleの「権限レベル」（コーディング名`role`、確定済みの管理者／編集者／閲覧者のいずれかのみ）は、意図的に異なるコーディング名にしている。前者は管理者が入力する生の設定、後者は評価ロジックを通過した後の確定値、という性質の違いを名前で区別するためであり、表記揺れではない。統一しないこと。

## 4.4 UserGroup（業務管理者用横断グループ）

| フィールド名 | 型 | 説明 |
|---|---|---|
| ID | UUIDField (PK) | |
| 名前 | CharField(100) | 例：「経営会議メンバー」「新規事業課」 |
| 説明 | TextField (blank) | |
| メンバー | ManyToManyField(CustomUser) | |
| 作成者 | FK(CustomUser, PROTECT) | |
| 作成日時 | DateTimeField (auto_now_add) | |
| 更新日時 | DateTimeField (auto_now) | |

Department（LDAP同期対象、木構造）とは独立したモデルとする。Departmentをこれ以上細分化すると、LDAP/ADの実際の組織図（ディレクトリ）との不整合が生じるため、部署に馴染まない例外的な括りはUserGroup側で表現する。新設・削除に必要な機能レベル権限は第3.4節参照。

**運用上の注意**：第5.3節の例外処理（配下部署の一部除外）に使うUserGroupは、Department側の異動と自動連動しない。除外対象部署に新しく配属された社員は、管理者が手動でUserGroupにもメンバー追加しない限り、意図せず閲覧可能な状態のままになる。これは仕組み上のトレードオフとして受容し、UI設計時に「このUserGroupは特定Department配下と同期する運用である」旨の注意書きを画面に表示することを検討する（次フェーズ）。

## 4.5 DealList（案件リスト）

| フィールド名 | 型 | 説明 |
|---|---|---|
| ID | UUIDField (PK) | |
| 名前 | CharField(100) | |
| 説明 | TextField (blank) | |
| アクセスリスト | FK(AccessList, PROTECT)（v0.3で変更） | 複数のDealListが同じAccessListを参照できる（多対1）。旧FGの`accesslistables`調査結果により、1コンテナは常に1つのAccessListしか実運用で使っていなかったことが判明したため、M2Mではなく素直なFKとする |
| **編集範囲**（v1.2で新設） | CharField(choices) | `creator_only`（作成者のみ）／`creator_and_participants`（作成者・参加者）／`all_editors`（作成者・参加者・リスト編集者全員）。デフォルトは`all_editors`（導入前の挙動と完全互換）。旧FG「日報リスト」の`default_permission`（`creator`/`attendees`/`writers`）と同一設計 |
| 作成者 | FK(CustomUser, PROTECT) | |
| 作成日時 | DateTimeField (auto_now_add) | |
| 更新日時 | DateTimeField (auto_now) | |

`Deal`モデルに`案件リスト`（FK, PROTECT, **NOT NULL（`null=False`かつ`blank=False`、v1.2で確定）**）を追加する。

**v1.2での方針転換（重要）**：v1.1までは「一般ユーザーフォームから`deal_list`を除外し、未指定時はサービス層がデフォルトDealListを自動設定する」方式だったが、旧FG（PHP/Laravel版）の調査結果を踏まえ、**Deal作成時にユーザー自身が案件リストを選択する方式に変更する**。

旧FGの実装調査で確認された事実：
- AccessListが紐づく5モデル（Group／Calendar／ReportList／TaskList／File）は、いずれも作成時にユーザーがAccessListを1つ選択する方式（例：`$tasklist->access_lists()->sync([$request->access_list_id])`）であり、自動割り当てロジックは一切存在しない
- 部署とAccessListの固定対応表も存在しない
- 実機（`freegroup.work`の日報リスト編集画面）で、アクセスリスト選択が単一選択のセレクトボックスであることを確認済み（選択肢はそのユーザーが作成済みの、またはアクセス可能なAccessListの一覧）

これを踏まえ、Deal作成時は以下の方式とする。

- 選択肢は「そのユーザーが編集者以上の権限を持つDealListのみ」に限定する（第6.2節の`editable_deal_list_ids(user)`を使用）。これにより「役員限定リスト」のような、そのユーザーがアクセス権を持たないリストは選択肢に出てこないため、旧仕様が懸念していた「一般社員が機密リストへ自由に紐付けられる」問題は生じない
- 選択必須のバリデーションをフォーム層に設ける。**未指定時の自動デフォルト設定は行わない**
- **編集者以上のDealListが1件もないユーザーは、新規Deal作成自体ができない**（第9.1節参照。エラーメッセージで案内し、救済ロジックは設けない）
- `blank=True`は撤回し、`blank=False`（デフォルト）に戻す。理由：フォームからフィールドを除外する運用をやめたため、`blank=True`の存在意義（除外時のバリデーション回避）が消滅した。ModelFormを介さない一括操作（第9.2節のデータマイグレーション等）はFKへの直接代入のため`blank=False`の影響を受けない

**Django Admin経由の迂回防止（v1.0で追加、v1.2でも継続）**：一般ユーザー向けフォームで選択肢を絞っても、Django標準の`DealAdmin`ではドロップダウンが全件表示されたままであり、`is_staff`権限を持つユーザーが管理画面から任意のDealListへ自由に付け替えられてしまう。`DealAdmin.get_readonly_fields()`で、第3.4節の管理権限（`deals.change_deallist`相当）を持たないユーザーには`deal_list`を読み取り専用にすること。

DealListそのものの付け替え（既存Dealが属するDealListの変更）は、引き続き第3.4節の管理権限を持つユーザーのみが、管理画面または専用の付け替え機能から行える設計とする（作成時の選択とは別の操作）。

**`FreeGroup2_案件管理機能_仕様書_v1_10`との連携（v1.3で追加・重要）**：同仕様書 §2.1で定義されているDealFormの`Meta.fields`には、現時点で`deal_list`が含まれていない（`name` / `company` / `stage` / `probability` / `deal_type` / `amount` / `expected_close_date` / `lead_source` / `source_campaign` / `memo`のみ）。AccessList導入ステップの実装時、コード君が同仕様書のフォーム定義をそのまま踏襲すると、モデル側で`Deal.deal_list`が`blank=False`（本節で確定）であるにも関わらずフォームに項目がない状態になり、**Deal新規作成画面が「deal_listが未入力です」というバリデーションエラーで永久に保存できなくなる**。AccessList導入時の実装指示書には、以下を明記すること。

- `DealForm.Meta.fields`に`deal_list`を追加する
- `DealForm.__init__()`で、`deal_list`フィールドのqueryset を`AccessListService.editable_deal_list_ids(user)`（第6.2節）で絞り込む

## 4.6 PersonList（パーソンリスト）

| フィールド名 | 型 | 説明 |
|---|---|---|
| ID | UUIDField (PK) | |
| 名前 | CharField(100) | |
| 説明 | TextField (blank) | |
| アクセスリスト | FK(AccessList, PROTECT)（v0.3で変更） | DealListと同じ理由・構造 |
| **編集範囲**（v1.2で新設） | CharField(choices) | `creator_only`（作成者のみ）／`all_editors`（リスト編集者全員）の**2段階**。DealListと異なり「参加者」に相当する概念（DealUser相当）をPersonには新設しないため3段階目は持たない（第2.2節参照） |
| 作成者 | FK(CustomUser, PROTECT) | |
| 作成日時 | DateTimeField (auto_now_add) | |
| 更新日時 | DateTimeField (auto_now) | |

`Person`モデルに`パーソンリスト`（FK, PROTECT, **NOT NULL（`null=False`かつ`blank=False`、v1.2で確定）**）を追加する。Contact・BusinessCard（Contactあり）はPerson経由でこの設定を継承する（独自のリストは持たない、第8章参照）。

**v1.2での方針転換（重要）**：第4.5節のDealListと同じ理由・同じ方式で、Person作成時にユーザー自身がパーソンリストを選択する方式に変更する。

- 選択肢は「編集者以上の権限を持つPersonListのみ」（`editable_person_list_ids(user)`、第6.2節）
- 選択必須、未指定時の自動デフォルト設定は行わない
- 編集者以上のPersonListが1件もないユーザーは新規Person作成不可（第9.1節）
- `blank=True`を撤回し`blank=False`に戻す
- 手動入力（v1.7）でのPerson新規作成、およびOriginalImageアップロード時（第8.3.5節）の双方でこの選択が必要になる
- CSVインポート（v1.7）については第8.3.6節を参照

**Django Admin経由の迂回防止（v1.0で追加、v1.2でも継続）**：第4.5節のDealListと同じ理由で、`PersonAdmin.get_readonly_fields()`においても、第3.4節の管理権限（`persons.change_personlist`相当）を持たないユーザーには`person_list`を読み取り専用にすること。

付け替え（既存Personのリスト変更）は、引き続き第3.4節の管理権限を持つユーザー限定とする（作成時の選択とは別の操作）。

## 4.7 実装配置規約（v0.5で新設）

コード君がFG2の既存規約通りに一発でマイグレーションを通せるよう、新設モデルの配置アプリと`related_name`を明記する。

**配置アプリ**：

| モデル | 配置先アプリ |
|---|---|
| AccessList／ACLEntry／AccessListUserRole | `permissions`アプリ（新規） |
| UserGroup | `accounts`アプリ |
| DealList | `deals`アプリ |
| PersonList | `persons`アプリ |

アプリをまたぐFKは、FG2の既存規約通り文字列参照（例：`'permissions.AccessList'`）で定義し、循環importを避けること。

**related_nameの明記**（Djangoの自動命名`_set`は使わない）：

| FK | related_name |
|---|---|
| `DealList.アクセスリスト`（→AccessList） | `deal_lists` |
| `PersonList.アクセスリスト`（→AccessList） | `person_lists` |
| `Deal.案件リスト`（→DealList） | `deals` |
| `Person.パーソンリスト`（→PersonList） | `persons` |
| `PersonMergeLog.マージ前パーソンリスト`（→PersonList） | `+`（逆参照不要） |
| `AccessList.作成者`等の監査用FK（`created_by`） | `+`（逆参照不要、全モデル共通） |
| `OriginalImage.アップロード時指定パーソンリスト`（→PersonList、v1.2で新設、第8.3.5節） | `+`（逆参照不要） |

`AccessListUserRole.アクセスリスト`の`related_name='user_roles'`は第4.3節の通り。

**マイグレーションの依存関係（v1.1で追加）**：4つのアプリ（`permissions`／`accounts`／`deals`／`persons`）にモデルが新設・拡張されるため、マイグレーションの適用順序が重要になる。`accounts`（UserGroup）・`permissions`（AccessList／ACLEntry／AccessListUserRole）が先、`deals`（DealList・`Deal.deal_list`）・`persons`（PersonList・`Person.person_list`）が後という順序になる。`deals`・`persons`の新規マイグレーションの`dependencies`に`('permissions', '0001_initial')`（実際のマイグレーション番号はコード君が現物確認）が確実に含まれるようにすること。含まれていないと、CI環境等でテーブル未作成エラー（`relation does not exist`）が発生し得る。

## 4.8 Meta.ordering一覧（v0.6で新設）

FG2の既存コーディング規約では全モデルに`ordering`の明示が義務付けられている。新設モデルの`ordering`を以下の通り指定する。

| モデル | ordering |
|---|---|
| AccessList | `['name']` |
| ACLEntry | `['order', 'created_at', 'id']`（第5.1節で定めた評価順のタイブレーク規則と完全に一致させる） |
| AccessListUserRole | `['access_list', 'user']` |
| UserGroup | `['name']` |
| DealList | `['name']` |
| PersonList | `['name']` |

## 4.9 ActionLog記録（v1.1で新設）

FG2の既存規約（`ActionLog.record()`、`_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_3` §12/§13参照）に従い、ACL周りの重要操作は`ActionLog`に記録する。`actionlogs/constants.py`（プレーン文字列リテラルで管理、定数クラスは使わない、`environment-and-tools`の既存運用と同様）に以下を追加すること。

| 定数（例） | 記録タイミング |
|---|---|
| `ACCESS_LIST_CREATED` / `ACCESS_LIST_UPDATED` | AccessList作成・更新時 |
| `DEAL_LIST_CHANGED` | DealListの付け替え時（第4.5節、管理者操作） |
| `PERSON_LIST_CHANGED` | PersonListの付け替え時（第4.6節、管理者操作。第10.1節のマージ時のPersonList選択も含む） |

具体的な文言・記録項目の追加要否はコード君の実装時判断とするが、上記4種の操作はいずれか1つでも記録漏れがあると監査ログとして機能しないため、実装チェックリストに含めること。

---

# 第5章 評価ロジック

## 5.1 順序評価の原則

旧FG（PHP版）の`ListOfUsersInTheAccessList::make_all_list_first()` / `create_user_lists_by_roles()`をそのまま踏襲する。

1. ACLEntryを`並び順`昇順に並べる（**タイブレーク規則（v0.3で確定）**：並び順が同値の場合は`作成日時`昇順、さらに同値なら`ID`昇順で決定論的に確定する。クエリは`order_by('order', 'created_at', 'id')`とする）
2. 各エントリの対象（個人／Department／UserGroup）を実ユーザーに展開する
   - CustomUser → そのユーザー1人
   - Department → 階層継承を含めた所属ユーザー全員（第5.2節）
   - UserGroup → メンバー全員
3. 並び順の昇順に走査し、**初出のユーザーIDだけ**を該当権限レベルで採用する（同一ユーザーが後続行に再出現してもスキップ＝先勝ち）
4. 退職者（`is_active=False`）は除外する
5. 権限レベルが「未設定」と確定したユーザーについては、AccessListUserRoleにレコードを作成しない（第4.2節）
6. `AccessListUserRole`をこの結果で全削除→全挿入する

**優先順位は対象の種別（個人／部署／グループ）では決まらない**。旧FGの実装調査で確認済みの通り、優先順位はあくまで管理者が並べた`並び順`の値のみで決まる。「Dept経由で閲覧者、個人指定で管理者」のように矛盾する設定があっても、並び順が早い方が機械的に勝つ。

**注意（原点回帰）**：NTFSやAWS IAM等、業界で広く使われるACL実装の多くは「明示的な拒否は順序に関係なく常に優先する」という設計を採る。本書は意図的にそれとは異なる「並び順が唯一の正義（先勝ち）」という旧FG踏襲方式を採用している。これはたんたんとの議論で明示的に確定した設計判断であり、変更の必要はない。ただし並び順を誤ると「拒否したつもりが拒否されない」という直感に反する事故が起きやすい方式であるため、次フェーズのUI設計（順序入れ替え画面）では、並び順ミスを防ぐ導線（保存前の確認表示等）を検討すること。

## 5.2 Department階層継承

- AccessListEntryの対象にDepartmentを指定した場合、**配下部署も自動的に対象に含む（継承あり固定）**
- 部署ごとに継承のON/OFFを切り替える機能は持たない（複雑化を避けるため）
- 配下部署の展開には`Department.descendants()`を使用する（第7章参照）

## 5.3 例外処理の作法

配下部署の一部だけ除外したい場合、**新しい部署を切ってはならない**（LDAPディレクトリとの不整合を生むため）。代わりに以下の手順を取る。

1. 除外したい対象をUserGroupとして作成する
2. そのUserGroupに対して権限レベル「未設定」のACLEntryを作成する
3. 継承元のDepartmentのエントリより**上位の並び順**に置く

これにより、grant/revokeという別軸のフラグを持たずに、順序評価だけで例外処理を実現できる。運用上の注意点は第4.4節を参照。

**v1.2での確認**：この仕組みにより、特定のユーザーがデフォルトAccessList（第9.1節）からの権限すら失うケースが起こり得る。これは意図的な設計として許容し、当該ユーザーが他のいかなるPersonList／DealListへの編集者以上の権限も持たない場合、新規Person／Deal作成ができなくなる（第9.1節）。これはバグではなく、管理者が明示的に選んだ設定の帰結として受容する。

---

# 第6章 再計算トリガと実装方式

## 6.1 イベント駆動の原則

旧FG（PHP版）の`AccessListUserRoleUpdate`はイベント駆動であり、cron処理ではない（第7.1節で詳述）。この設計をDjangoのシグナルで再現する。

再計算トリガ一覧：

| トリガ | 再計算対象 |
|---|---|
| AccessList作成・更新・削除 | そのAccessListについて`rebuild_for_access_list()`を1回呼べば足りる。参照元のDealList／PersonListの数に関わらず、`AccessListUserRole`は`(access_list, user)`だけで決まるキャッシュであり、参照元Listごとに個別のキャッシュを持つわけではない（v0.5で明記） |
| ACLEntry変更 | 所属するAccessList分 |
| CustomUserの部署異動 | **旧所属Departmentと新所属Department、両方に関連する全AccessList分**（v0.5で修正、後述） |
| CustomUserのUserGroup所属変更 | **脱退したUserGroupと加入したUserGroup、両方に関連する全AccessList分**（v0.5で修正、後述） |
| UserGroupのメンバー変更 | そのUserGroupを参照する全AccessList分 |
| Departmentの親変更 | **旧親の祖先チェーン（旧親自身とその上位）、新親の祖先チェーン（新親自身とその上位）を対象とするACLEntryを持つAccessList分**（v0.6で方向を訂正、後述） |
| CustomUser作成（LDAP自動生成含む） | `rebuild_for_user_status_change(user)`を呼ぶ（v0.8で統合メソッド化、後述） |
| CustomUserのis_active変更（退職・復職） | `rebuild_for_user_status_change(user)`を呼ぶ（v0.8で統合メソッド化、後述） |

**v0.5での変更：Person統合（マージ）関連トリガの削除**：`AccessListUserRole`は「AccessList × CustomUser」の権限キャッシュであり、Person／Dealが「どのList（PersonList／DealList）に属するか」とは無関係である。マージ・マージ取り消しで変化するのは`Person.person_list`（単純なFK参照）だけであり、これはクエリ実行時に`person_list__access_list__user_roles__user=user`で都度動的に解決される。マージ・マージ取り消しの際に`AccessListUserRole`を再計算する必要は一切ない（呼んでも結果が変わらないまま全削除→全挿入が走るだけの無駄になる）。したがって「PersonMergeLog作成」「PersonMergeLog復元」の2行は再計算トリガ一覧から削除した。第10.1節のマージ処理内の`person_list`更新も、`AccessListService`の呼び出しを伴わない単純なフィールド更新で完結する。

**v0.5での修正：異動・脱退・親変更は「変更前」も再計算対象に含める**：CustomUserの部署異動・UserGroup所属変更、Departmentの親変更は、いずれも「異動後・変更後」の状態だけでなく「異動前・変更前」の状態についても再計算が必要である。シグナルの発火時点では、対象フィールドの値が既に新しい値に更新済みであることが多いため、素朴に「現在の所属から逆引き」すると、**異動元・変更前の側にユーザーの古いAccessListUserRole行が残り続ける**（退職者の即時剥奪と同じ理由で即時性が損なわれる）。詳細な実装方針は第6.2節参照。

**v0.6での訂正：「Departmentの親変更」で辿るべき方向は祖先、配下ではない**：Department Dの親が変わっても、**Dの配下（子孫）の木構造自体は変化しない**ため、Dの子孫を対象とするACLEntryは影響を受けない。影響を受けるのは「Dがどの上位Departmentの継承範囲に入るか」という**祖先側**である。したがって再計算すべきAccessListは、旧親・新親それぞれの祖先チェーン（旧親自身とその上位、新親自身とその上位）を対象とするACLEntryを持つものであり、「Dの配下全体」という表現はv0.5時点の誤りだったため訂正する。実装方針は第6.2節の`rebuild_for_department()`の再定義を参照。

**v0.7での訂正：退職・復職・新規作成時は`rebuild_for_user()`だけでは不十分**：v0.6で`rebuild_for_user(user)`の役割を「ユーザー個人が直接指定されたACLEntryのみ」に縮小したことに伴い、Department／UserGroup経由の権限は`rebuild_for_department()` / `rebuild_for_user_group()`が別途担当する設計とした（第6.2節）。この役割分担は部署異動・UserGroup脱退では正しく手当てされている（新旧両方を明示的に呼ぶため）が、**「退職・復職（is_active変更）」と「CustomUser作成」の2つのトリガでは、`rebuild_for_user(user)`しか呼ばない実装になっていると、Department／UserGroup経由でしか権限を持たないユーザーが一切処理対象から漏れる**。

具体例：個人名を直接指定したACLEntryを一切持たず、「営業部：編集者」というDepartment経由のACLEntryだけで権限を得ているユーザーが退職した場合、`rebuild_for_user(user)`だけを呼ぶ実装では該当AccessListが再評価されず、この退職者の編集者権限は翌晩のcronが走るまで生き残ってしまう。これは本書が繰り返し強調している「退職者の即時剥奪」という、最も重要視されているシナリオそのものが機能しなくなることを意味する。同じ理由で、LDAP同期時点で既にDepartment／UserGroupが割り当てられている新規ユーザーの初期権限付与にも、同様の漏れが生じ得る。

したがって、退職・復職・新規作成のシグナルハンドラでは、`rebuild_for_user(user)`に加えて、`rebuild_for_department(user.department)`（所属していれば）と、所属する全UserGroupについて`rebuild_for_user_group()`を呼ぶこと。**v0.8で追記**：ただし個別に3種類のメソッドを呼ぶと、部署とUserGroupが同じAccessListを参照している場合に同一AccessListへの再計算が重複するため、実装は第6.2節の`rebuild_for_user_status_change(user)`という統合メソッド（対象AccessList IDをsetで集約してから重複排除して1回ずつ再計算）を使うこと。

## 6.2 一元化サービスクラス方針

すべての再計算ロジック、および**v1.2で新設した編集可否判定ロジック**を`AccessListService`に集中させる（PHP版`AccessListUserRoleUpdate`に相当）。シグナルからはこのクラスのメソッドを呼ぶだけとする。

```python
class AccessListService:
    @staticmethod
    def rebuild_for_access_list(access_list):
        """1つのAccessListについてAccessListUserRoleを全再計算する。

        常に「現在の生きたデータ」（各ACLEntryの対象を現時点のDepartment階層・
        UserGroupメンバーシップで展開した結果）に基づいて全削除→全挿入する。
        """
        pass

    @staticmethod
    def rebuild_for_user(user):
        """特定ユーザーが直接指定されたACLEntryを持つ全AccessListを再計算する。

        ここでの「関与する」はユーザー個人が対象として直接指定されたACLEntryに限る。
        Department／UserGroup経由の間接的な影響範囲は、下記rebuild_for_department() /
        rebuild_for_user_group()が別途担当する（役割を分離することで、
        「現在の所属から数え上げる」設計に起因するバグを避ける、後述）。
        """
        pass

    @staticmethod
    def rebuild_for_department(department):
        """指定Department、またはその祖先（親・祖父…）を対象とするACLEntryを
        1つでも持つAccessListを特定し、それらのAccessListについて
        rebuild_for_access_list()を呼ぶ。

        【v0.6で明確化・最重要】本メソッドは「departmentの現在の所属ユーザーを
        数え上げてから、そのユーザーたちの権限を再計算する」という実装にしては
        ならない。異動済みで既にdepartmentのメンバーでなくなったユーザーは、
        その数え上げから漏れてしまい、古いAccessListUserRole行が削除されずに
        残留する（v0.5で修正したはずの即時剥奪が機能しなくなる）。

        正しい実装は「departmentまたはその祖先を対象とするACLEntryを持つ
        AccessListを（ACLEntry.targetから）特定する」→「特定したAccessListに
        ついてrebuild_for_access_list()を呼ぶ」の2段階。rebuild_for_access_list()
        自体が現在の生きたデータで全評価をやり直すため、異動済みユーザーは
        評価の結果として自動的に除外される。departmentの「現在のメンバー」を
        起点に考える必要はない。

        【v1.0で追記】内部で祖先チェーンのIDリストを組み立てる際（例：
        get_ancestor_ids(department)のような自作ヘルパー）、親を辿るループに
        気を取られて「department自身のID」をリストに含め忘れるミスが起きやすい。
        検索対象のIDリストには、必ず[str(department.pk)]（自分自身）を含めた上で
        親階層を辿ること。これを忘れると、departmentそのものを対象とした
        ACLEntryがヒットしなくなる。
        """
        pass

    @staticmethod
    def rebuild_for_user_group(user_group):
        """指定UserGroupを対象とするACLEntryを持つAccessListを特定し、
        rebuild_for_access_list()を呼ぶ。rebuild_for_department()と同じ理由で、
        「UserGroupの現在のメンバーを数え上げる」実装にしないこと。
        """
        pass
```

**異動・脱退・親変更時の「変更前」の捕捉について**：上記`rebuild_for_department()` / `rebuild_for_user_group()`は「指定したDepartment／UserGroupを対象とするACLEntry」を特定する処理であり、departmentやuser_group自体は変化しない（部署異動やUserGroup脱退で変わるのはユーザー側の所属であって、Department／UserGroupというレコード自体は変わらない）。したがって部署異動・UserGroup脱退・Department親変更のシグナルハンドラは、**変更前の値を保存前に捕捉し、変更前・変更後の両方のDepartment／UserGroupについて`rebuild_for_department()` / `rebuild_for_user_group()`を呼ぶ**必要がある。

具体的には以下の実装パターンとする。

- **CustomUserのUserGroup脱退・加入**：`m2m_changed`シグナルの`pre_remove` / `pre_clear`で脱退前のUserGroup集合を捕捉し、`post_remove` / `post_add`で脱退元・加入先の両方について`rebuild_for_user_group()`を呼ぶ
- **Departmentの親変更**：`pre_save`で変更前の`parent`（旧親）を取得する。保存後、`rebuild_for_department(旧親)`と`rebuild_for_department(新親)`を両方呼ぶ（D自身やD配下ではなく、旧親・新親それぞれを起点に、その祖先チェーンを対象とするACLEntryを特定して全再評価する。第6.1節参照）。旧親・新親がルート部署で親を持たない場合は、そのDepartment自身のみを起点として扱う
- **CustomUserのis_active変更（退職・復職）・CustomUser作成（v0.7で追加、v0.8で重複排除方式に修正）**：`rebuild_for_user(user)` / `rebuild_for_department(user.department)` / 所属UserGroupごとの`rebuild_for_user_group()`を個別に呼ぶと、退職者の所属部署とUserGroupが同じAccessList（「全社AccessList」等）を参照しているケースで、同一AccessListに対する`rebuild_for_access_list()`（全削除→全挿入）が重複して連続実行され、不要なDBロック・遅延を招く。これを避けるため、対象となるAccessListのIDを`set`で集約してから、重複を排除して1回ずつ再計算する専用メソッドを設ける（退職・復職ではDepartment／UserGroup自体は変化しないため、pre_save等での事前捕捉は不要で、現在の所属をそのまま使ってよい）。

```python
class AccessListService:
    @classmethod
    def rebuild_for_user_status_change(cls, user):
        """退職・復職・新規作成時に呼ぶ統合エントリポイント。
        対象AccessListをsetで集約してから重複排除して1回ずつ再計算する。
        """
        target_acl_ids = set()
        target_acl_ids.update(cls.get_acl_ids_for_user(user))
        if user.department:
            target_acl_ids.update(cls.get_acl_ids_for_department(user.department))
        for user_group in user.usergroup_set.all():  # 実際のrelated_nameはコード君が現物確認
            target_acl_ids.update(cls.get_acl_ids_for_user_group(user_group))

        for acl_id in target_acl_ids:
            cls.rebuild_for_access_list_id(acl_id)
```

`get_acl_ids_for_*()`は対象AccessListのID集合を返すだけの補助メソッド（実際の再計算は行わない）とし、`rebuild_for_access_list_id()`が実際の全削除→全挿入を担う。退職・復職・新規作成のシグナルハンドラは、`rebuild_for_user()` / `rebuild_for_department()` / `rebuild_for_user_group()`を個別に呼ぶのではなく、この`rebuild_for_user_status_change(user)`を1回呼ぶこと。

**シグナル発火ガード（v0.9で追加・最重要、v1.0で部署異動と統合）**：`CustomUser`の`post_save`シグナルは、ログインのたびに更新される`last_login`のような無関係なフィールド変更でも無条件に発火する。ガードなしで`post_save`から再計算処理を呼ぶと、**ユーザーがログインするたびに、その人が関与する全AccessListの全削除→全挿入がバックグラウンドで走る**という致命的な負荷（DBロック・高CPU）を引き起こす。`pre_save`で変更前の`is_active`と`department_id`をキャッシュし、**実際に値が変化した場合のみ**（または新規作成時のみ）再計算を実行するガードを設けること。

**v1.0での統合**：部署異動（第6.1節）と退職・復職（is_active変更）は、どちらも`CustomUser`の`pre_save` / `post_save`を監視する点で共通するため、別々のハンドラに分けると`CustomUser.objects.filter(pk=instance.pk)`が重複発行され、シグナルの接続漏れも起きやすい。**1つの`user_pre_save` / `user_post_save`で`is_active`と`department_id`の両方の差分を監視する**ことに統合する。

```python
# apps/permissions/signals.py
def user_pre_save(sender, instance, **kwargs):
    if instance.pk:
        orig = CustomUser.objects.filter(pk=instance.pk).only('is_active', 'department_id').first()
        instance._orig_is_active = orig.is_active if orig else None
        instance._orig_department_id = orig.department_id if orig else None

def user_post_save(sender, instance, created, **kwargs):
    if created:
        AccessListService.rebuild_for_user_status_change(instance)
        return

    # 1. is_active変更時（退職・復職）
    if getattr(instance, '_orig_is_active', None) != instance.is_active:
        AccessListService.rebuild_for_user_status_change(instance)

    # 2. 部署異動時（旧部署・新部署の両方を再計算）
    orig_dept_id = getattr(instance, '_orig_department_id', None)
    if orig_dept_id != instance.department_id:
        if orig_dept_id:
            old_dept = Department.objects.filter(pk=orig_dept_id).first()
            if old_dept:
                AccessListService.rebuild_for_department(old_dept)
        if instance.department:
            AccessListService.rebuild_for_department(instance.department)
```

**PostgreSQL型キャストの徹底（v0.9で追加）**：`ACLEntry.target_object_id`は`CharField(64)`（第4.2節）だが、Department／UserGroupのPKはUUIDである。`get_acl_ids_for_department()`等の内部で`ACLEntry.objects.filter(target_object_id__in=ancestor_ids)`のように、UUIDオブジェクトのリストをそのまま`__in`に渡すと、PostgreSQLでは`character varying = uuid`の演算子が存在せず`operator does not exist`エラーでクエリが落ちる。`target_object_id__in`に渡すIDリストは、必ず`[str(pk) for pk in ids]`のように明示的に文字列へキャストすること。

**特権バイパスの集約（v0.9で新設・最重要）**：第8.1節・第8.3.1節の`visible_for()`にスーパーユーザー・全社閲覧権限保持者の特権バイパスを追加したが（v0.8）、第8.3.3節（OriginalImageの積集合判定）・第10.2節（DuplicateCandidateレビュー可否判定）は、素の`...access_list__user_roles__user=user`条件のままで特権バイパスを経由していなかった。個別の判定箇所ごとに`is_superuser`チェックを都度書き足す方式では、新しい判定箇所が増えるたびに同じ伝播漏れが再発するため、`AccessListService`に特権バイパスを内包した共通ヘルパーを1箇所定義し、AccessListUserRoleを直接参照するすべての箇所（`visible_for()`2つ、第8.3.3節、第10.2節）がこれを経由する設計に変更する。

```python
class AccessListService:
    @staticmethod
    def accessible_person_list_ids(user):
        """ユーザーが閲覧できるPersonListのID集合を返す（特権バイパスを内包）。
        権限レベル（管理者／編集者／閲覧者）を問わない。一覧表示・閲覧判定用。
        """
        if not user or not user.is_authenticated:
            return PersonList.objects.none().values_list('id', flat=True)
        if user.is_superuser or user.has_perm('persons.view_all_persons'):
            return PersonList.objects.values_list('id', flat=True)
        return PersonList.objects.filter(
            access_list__user_roles__user=user
        ).values_list('id', flat=True)

    @staticmethod
    def accessible_deal_list_ids(user):
        """ユーザーが閲覧できるDealListのID集合を返す（特権バイパスを内包）。
        権限レベルを問わない。一覧表示・閲覧判定用。
        """
        if not user or not user.is_authenticated:
            return DealList.objects.none().values_list('id', flat=True)
        if user.is_superuser or user.has_perm('deals.view_all_deals'):
            return DealList.objects.values_list('id', flat=True)
        return DealList.objects.filter(
            access_list__user_roles__user=user
        ).values_list('id', flat=True)

    @staticmethod
    def accessible_access_list_ids(user):
        """ユーザーが閲覧できるAccessListのID集合を返す（特権バイパスを内包）。
        権限レベル（管理者／編集者／閲覧者）を問わない。

        【v1.5で新設】DealListCreateView／PersonListCreateView（第2.3.3節）の
        「アクセスリスト」選択欄の選択肢を絞り込むために使う。AccessListの
        管理権限（第3.4節`permissions.add_accesslist`等）を持たないDealList
        管理者／PersonList管理者であっても、DealList／PersonListの新規作成は
        行える（別の機能レベル権限で許可されている、第3.4節）。このとき
        AccessList選択欄に、閲覧権限すら持たないAccessList（例：「役員限定」）
        の名前まで見えてしまうのは、本書が一貫して守ってきた「見えてはいけない
        ものを見せない」方針に反するため、accessible_person_list_ids() /
        accessible_deal_list_ids()と同型のヘルパーをAccessList自体にも用意する。

        DealListUpdateView／PersonListUpdateView（第2.3.3節）での、現在値が
        この絞り込みに含まれない場合の扱い（読み取り専用化）は第2.3.3節
        「Update画面での現在値の扱い」を参照。

        【実装上の申し送り】特権バイパスに用いる`permissions.view_all_access_lists`
        は、AccessListの`Meta.permissions`に明示宣言し、配布対象（第3.4節の
        AccessList管理Group等）に含めること。
        """
        if not user or not user.is_authenticated:
            return AccessList.objects.none().values_list('id', flat=True)
        if user.is_superuser or user.has_perm('permissions.view_all_access_lists'):
            return AccessList.objects.values_list('id', flat=True)
        return AccessList.objects.filter(
            user_roles__user=user
        ).distinct().values_list('id', flat=True)

    @staticmethod
    def editable_person_list_ids(user):
        """ユーザーが「編集者」以上の権限を持つPersonListのID集合を返す
        （特権バイパスを内包）。新規Person作成時・OriginalImageアップロード時の
        選択肢（第4.6節・第8.3.5節）、およびCSVインポート時の選択肢（第8.3.6節）に使う。
        閲覧者権限しか持たないPersonListは含めない（第3.3節：閲覧者は編集・追加不可）。

        【v1.3で修正】特権バイパスには閲覧専用権限view_all_personsではなく、
        編集専用権限persons.edit_all_personsを使う（下記「特権バイパス条件の
        使い分けについて」参照）。
        """
        if not user or not user.is_authenticated:
            return PersonList.objects.none().values_list('id', flat=True)
        if user.is_superuser or user.has_perm('persons.edit_all_persons'):
            return PersonList.objects.values_list('id', flat=True)
        return PersonList.objects.filter(
            access_list__user_roles__user=user,
            access_list__user_roles__role__in=['editor', 'admin'],
        ).values_list('id', flat=True)

    @staticmethod
    def editable_deal_list_ids(user):
        """ユーザーが「編集者」以上の権限を持つDealListのID集合を返す
        （特権バイパスを内包）。新規Deal作成時の選択肢（第4.5節）に使う。

        【v1.3で修正】特権バイパスには閲覧専用権限view_all_dealsではなく、
        編集専用権限deals.edit_all_dealsを使う。
        """
        if not user or not user.is_authenticated:
            return DealList.objects.none().values_list('id', flat=True)
        if user.is_superuser or user.has_perm('deals.edit_all_deals'):
            return DealList.objects.values_list('id', flat=True)
        return DealList.objects.filter(
            access_list__user_roles__user=user,
            access_list__user_roles__role__in=['editor', 'admin'],
        ).values_list('id', flat=True)
```

**戻り値の型統一（v1.1で追加）**：未認証時の`.none()`にも`.values_list('id', flat=True)`を付けること。他の分岐がすべて`values_list`（ID値のみのQuerySet）を返す中、未認証時だけモデルインスタンスのQuerySetを返すと、呼び出し側（例：第10.2節の`set(accessible_person_list_ids(user))`）で要素の型が不揃いになる。上記4メソッドすべてに同じ統一を適用する（v1.2）。

**特権バイパス条件の使い分けについて（v1.3で新設・最重要）**：レビュー（ジット君・GPT君の双方）で、`editable_*_list_ids()` / `can_edit_*()`が`accessible_*_list_ids()`と同じ`view_all_persons` / `view_all_deals`を特権バイパスに使っている点が権限昇格バグとして指摘された。`view_all_*`は「全件を閲覧できる」ことだけを意図した権限であり（例：コンプライアンス監査担当者に閲覧目的だけで付与する運用を想定）、これを編集系のバイパスにも流用すると、v1.2で新設した「編集範囲＝`creator_only`」等の制限を、この権限を持つ人だけが素通りできてしまう。そこで以下のように**閲覧用と編集用の特権バイパス権限を明確に分離**する。

| 用途 | 使用するメソッド | 特権バイパス権限 |
|---|---|---|
| 閲覧（一覧表示、8.3.3節の積集合判定、10.2節のレビュー可否） | `accessible_person_list_ids()` / `accessible_deal_list_ids()` | `persons.view_all_persons` / `deals.view_all_deals`（変更なし） |
| 編集（新規作成時の選択肢、`can_edit_*()`） | `editable_person_list_ids()` / `editable_deal_list_ids()` / `can_edit_deal()` / `can_edit_person()` / `can_edit_contact()` | **`persons.edit_all_persons` / `deals.edit_all_deals`（v1.3で新設）** |

`persons.edit_all_persons` / `deals.edit_all_deals`は、`view_all_*`とは独立した新設の機能レベル権限とする（`Meta.permissions`への追加が必要、コード君が実装時に対応）。「全件閲覧できる人には編集も含めて実質管理者権限を持たせたい」という運用にしたい場合は、その部署・ロールの`default_groups`に両方の権限を配布すればよく、権限モデル自体は分離したまま運用で吸収する。**`view_all_*`だけを持つユーザーが`can_edit_*()`をバイパスすることは、v1.3以降、決して発生しない。**

**未認証ガードの位置について（v1.0で追加）**：第8.1節・第8.3.1節の`visible_for(user)`には`if not user.is_authenticated: return self.none()`が入っているが、第8.3.3節・第10.2節はこの`accessible_*_list_ids()`ヘルパーを直接呼ぶため、`visible_for()`のガードを経由しない。未認証（`AnonymousUser`）が渡されると`access_list__user_roles__user=user`が`ValueError`を起こし即500エラーになるため、**ガードはヘルパー自体の冒頭に持たせる**（上記コードの通り）。呼び出し側で個別にガードを書く必要はない。

**`persons.view_all_persons` / `deals.view_all_deals`の確認事項（v1.0で追加）**：上記2つの権限名が、既存の機能レベル権限として定義済みか、本書の導入に伴い`Meta.permissions`へ新規追加が必要かは、コード君が実装時に現物のコードで確認すること（第8.3.2節のBusinessCard状態区分の確認事項と同様の位置づけ）。

**編集範囲判定（v1.2で新設）**：第4.5節・第4.6節の「編集範囲」設定を評価する。第3.3節の権限レベル判定（`accessible_*_list_ids()` / `editable_*_list_ids()`）とは独立した、AND条件で作用する追加の判定である。

```python
class AccessListService:
    @staticmethod
    def can_edit_deal(user, deal):
        """データレベル権限としてこのDealを編集できるか（編集範囲設定を含む）。
        機能レベル権限（deals.change_deal等）とのAND判定は呼び出し側で別途行うこと。

        【v1.3で修正・最重要】まず「そのDealListの編集者以上であること」を
        大前提（土台）として判定し、そのAND条件として編集範囲設定でさらに
        絞り込む2段階構造にする。旧コード（v1.2）はcreator_only /
        creator_and_participantsの分岐でリスト自体のAccessListUserRoleを
        一切検証しておらず、以下2つのすり抜けが生じていた。
        (1) 異動・第5.3節の例外処理でそのDealListへのアクセス権自体を失った
            旧ownerが、deal.owner_id == user.idの一致だけで編集できてしまう
        (2) 閲覧目的でDealUserに登録されただけの参加者が、リストの編集権限
            なしにcreator_and_participants設定を素通りしてしまう
        本節の第3章冒頭で定義した「[権限レベル] AND [編集範囲]」という
        3重AND構造を、コードでも文字通りAND判定として実装する。

        【v1.3で修正】特権バイパスはdeals.view_all_dealsではなく
        deals.edit_all_dealsを使う（上記「特権バイパス条件の使い分けに
        ついて」参照）。

        【v1.3で明記】ここでの「作成者」は、起票者Deal.created_byではなく
        現在の案件オーナーDeal.ownerを指す（下記「Dealにおける『作成者』の
        扱いについて」参照）。
        """
        if user.is_superuser or user.has_perm('deals.edit_all_deals'):
            return True

        # 1. 大前提：そのDealListの「編集者」以上の権限を持っていること
        is_list_editor = AccessListUserRole.objects.filter(
            access_list=deal.deal_list.access_list, user=user, role__in=['editor', 'admin']
        ).exists()
        if not is_list_editor:
            return False

        # 2. 編集範囲設定によるさらなる絞り込み（AND条件）
        scope = deal.deal_list.編集範囲
        if scope == 'creator_only':
            return deal.owner_id == user.id
        if scope == 'creator_and_participants':
            return deal.owner_id == user.id or deal.deal_users.filter(user=user).exists()
        return True  # all_editors

    @staticmethod
    def can_edit_person(user, person):
        """データレベル権限としてこのPersonを編集できるか（編集範囲設定を含む）。
        Personには単一のcreated_byが存在しないため、creator_only設定時は
        「Person全体の編集」ではなく「自分が作成したContact単体の編集」の可否を返す
        （第8.3.4節のcreated_by特例と同じ粒度）。creator_only設定時に本メソッドが
        Falseを返すのは「Person全体としては編集不可」という意味であり、
        呼び出し側はその場合、別途can_edit_contact(user, contact)を用いて
        Contact単位の編集可否（created_by特例を含む）を判定する責務を負う。

        【v1.3で修正】特権バイパスはpersons.view_all_personsではなく
        persons.edit_all_personsを使う。
        """
        if user.is_superuser or user.has_perm('persons.edit_all_persons'):
            return True
        scope = person.person_list.編集範囲
        if scope == 'all_editors':
            return AccessListUserRole.objects.filter(
                access_list=person.person_list.access_list, user=user, role__in=['editor', 'admin']
            ).exists()
        # creator_only: 呼び出し側がcan_edit_contact()を使う前提でFalseを返す
        # （Person全体としては編集不可。Contact単体の判定へは委譲しない）。
        return False

    @staticmethod
    def can_edit_contact(user, contact):
        """Contact単位の編集可否。Person.person_list.編集範囲がcreator_onlyの場合、
        自分が作成したContactのみ編集可（第8.3.4節created_by特例と同じ根拠）。
        all_editorsの場合はcan_edit_person()と同じ判定に委譲する。

        【v1.3で修正】特権バイパスはpersons.view_all_personsではなく
        persons.edit_all_personsを使う。

        【v1.3で明記】created_by特例は機能レベル権限（contacts.change_contact）の
        保有を前提とする（第8.3.4節参照）。本メソッドはデータレベル権限のみを
        判定するため、機能レベル権限のAND判定は呼び出し側で別途行うこと。
        """
        if user.is_superuser or user.has_perm('persons.edit_all_persons'):
            return True
        # created_by特例：自分が作成したContactは、編集範囲設定に関わらず常に編集可
        # （ただし機能レベル権限contacts.change_contactの保有が別途前提、第8.3.4節）
        if contact.created_by_id == user.id:
            return True
        scope = contact.person.person_list.編集範囲
        if scope == 'creator_only':
            return False
        return AccessListService.can_edit_person(user, contact.person)
```

**Dealにおける「作成者」の扱いについて（v1.3で新設・重要）**：旧FGの日報リスト設定名「作成者のみ（creator）」をそのまま移植した経緯から、字面だけを見ると`Deal.created_by`（起票者）で判定したくなるが、これは誤りである。FG2のDealには`created_by`（起票者、監査用、変更不可）と`owner`（現在の案件オーナー、付け替え可能）の2つのフィールドが存在し、`FreeGroup2_案件管理機能_仕様書_v1_10` §7.1では「案件を引き継いだ元作成者（created_by）には編集権を残さず、現在のownerが正」という設計方針が既に確定している。したがって、編集範囲`creator_only` / `creator_and_participants`における「作成者」は**常に`Deal.owner`を指し、`Deal.created_by`で判定してはならない**。上記コード例の通り`deal.owner_id == user.id`を用いること。

**呼び出し箇所**：第10.3節の`can_merge_person`と同じ位置づけで、View層の編集ボタン表示制御とService層（更新系処理の入口）の両方に組み込む（`_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_3`が定める View層／Service層の二重防衛原則に従う）。

以降、第8.1節・第8.3.1節・第8.3.3節・第10.2節はすべてこのヘルパーを経由する形に統一する（詳細は各節参照）。特権バイパスの判定条件（`is_superuser`／`view_all_persons`／`view_all_deals`）を変更する必要が生じた場合も、この4メソッドだけを直せば全箇所に反映される。

**`m2m_changed`シグナルの型分岐トラップ（v0.6で追加）**：UserGroupのメンバー変更を監視する`m2m_changed`シグナル（`sender=UserGroup.members.through`）は、操作の経路によって`instance`引数の型が変わる。`user_group.members.add(user)`のように順方向で呼ばれた場合は`instance`がUserGroup、`pk_set`がユーザーIDの集合になる。一方`user.usergroup_set.add(user_group)`のように逆参照経由で呼ばれた場合は`instance`がCustomUser、`pk_set`がUserGroup IDの集合になる。ハンドラ内で`isinstance(instance, UserGroup)`により分岐し、どちらの経路から呼ばれても脱退・加入の対象を正しく抽出できるようにすること。

## 6.3 cronによる整合性検証・自己修復

イベント駆動の実装漏れ（特にDjangoの`QuerySet.update()` / `bulk_create()`はシグナルを発火しない点）に備え、以下を用意する。

- `python manage.py rebuild_all_access_lists`：全AccessListUserRoleを強制再構築する管理コマンド
- 夜間cronでの定期実行を推奨（既存のPermissionSet側の`rebuild_all_permissions`と同じ役割分担）

イベント駆動は即時性（退職者のアクセス即時剥奪等）を担保し、cronはトリガ漏れに対する保険として機能する、という二段構えとする。

## 6.4 シグナル非発火経路への注意喚起

コード君向けの実装ルールとして、以下を明記する。

- `bulk_create()` / `bulk_update()` / `QuerySet.update()`でCustomUser・Department・UserGroup関連のレコードを一括変更する処理を書く場合、対応するシグナルが発火しないため、**必ず`AccessListService`の該当メソッドを明示的に呼ぶこと**
- シグナル定義は`apps/permissions/signals.py`に集約し、抜け漏れを目視で確認できるようにする

---

# 第7章 Department.descendants()の使用方針

## 7.1 現時点での使用継続の根拠

`_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_3`では、`Department.descendants()`（再帰実装、N+1問題あり）について「LDAP同期内、認証フロー、cron処理では使用禁止」と定めていた。

今回のAccessList再計算処理は、この禁止対象には該当しないと判断する。理由：

- 旧FG（PHP版）の調査結果で、`AccessListUserRoleUpdate`の再計算トリガ（`Dept($dept)` / `Depts($array)`等）が**完全にイベント駆動**であり、cron処理ではないことを確認済み（第6.1節）
- 禁止事項の「cron処理」は、LDAP同期のような「全社員・全部署を毎晩ループする」全件バッチ処理を指しており、「特定のDepartmentが変更されたときだけ、その周辺だけを再計算する」というピンポイントな処理とは性質が異なる
- 対象顧客規模（中小企業、部署数は数十件・階層2〜4段程度が現実的）を踏まえると、再帰呼び出しによるクエリ数は許容範囲内

## 7.2 将来的な置き換え判断基準

以下のいずれかに該当した場合、`django-mptt` / `django-treebeard` / 再帰CTEへの置き換えを検討する。

- 部署数・階層が想定を大きく超えて増加した場合
- AccessList再計算処理のレスポンスタイムに実運用上の問題が生じた場合

---

# 第8章 各機能への適用

## 8.1 案件（Deal）

`DealList`を導入する（第4.5節）。`Deal.案件リスト`（FK, NOT NULL）→`DealList.アクセスリスト`（FK）経由でAccessListを適用する。

**統一QuerySetメソッド（v0.7で新設）**：`deals/models.py`のカスタムQuerySetに`DealQuerySet.visible_for(user)`を実装する。自身が`owner`、`DealUser`に含まれる、または`案件リスト`経由の閲覧権限を持つDealを返す統一メソッドとし、View層・API層の一覧取得は必ずこのメソッドを経由すること。Viewごとに`filter(deal_list__access_list__user_roles__user=user)`のようなクエリを個別に書くと、条件の綻びが生まれやすく認可漏れの温床になるため、手書きを禁止する。

**特権バイパス（v0.8で追加、v0.9で共通ヘルパー経由に変更）**：`visible_for(user)`は第6.2節の`AccessListService.accessible_deal_list_ids(user)`（特権バイパスを内包）を使い、`deal_list_id__in=`で絞り込む。個別に`is_superuser`判定を書かないこと。

**未認証・重複行対策（v0.9で追加）**：`visible_for(user)`の冒頭に`if not user.is_authenticated: return self.none()`を入れる。また、`owner` / `DealUser`（M2M） / `案件リスト`の3条件をORで結合するため、複数の関与者が登録された案件や、自分がownerかつDealUserにもなっている案件が複数行として重複しうる。**必ず末尾に`.distinct()`を付与すること**。

```python
# deals/models.py（イメージ）
class DealQuerySet(models.QuerySet):
    def visible_for(self, user):
        if not user.is_authenticated:
            return self.none()
        accessible_ids = AccessListService.accessible_deal_list_ids(user)
        return self.filter(
            Q(owner=user) | Q(deal_users__user=user) | Q(deal_list_id__in=accessible_ids)
        ).distinct()
```

**編集可否（v1.2で新設）**：閲覧（`visible_for`）とは別に、編集可否は`AccessListService.can_edit_deal(user, deal)`（第6.2節）で判定する。View層のボタン表示制御・Service層の更新処理入口の両方でこれを経由すること。DealUserは閲覧（本節）に加えて、編集範囲設定が`creator_and_participants`の場合に編集可否にも影響する（第4.5節・第6.2節）。

## 8.2 活動履歴（Activity）

独立した`ActivityList`は作らない。旧FG調査で確認済みの通り、Calendar（コンテナ）とSchedule（レコード）の関係と同型で、**ActivityはDealへの委譲を維持する**（`FreeGroup2_案件管理機能_仕様書_v1_10` §7.3の設計をそのまま踏襲）。

判定は以下の通り分岐する：

- **Dealに紐づく活動**：Dealへ委譲する（DealListのAccessList判定がそのまま適用される。編集可否も第8.1節の`can_edit_deal`に委譲）
- **Campaignに紐づく活動**（キャンペーンフォロー活動）：既存の`Campaign.has_view_permission`（`view_all_campaigns`保持者は他人のキャンペーンも閲覧可）へ委譲する
- **どちらにも紐づかない活動**（社内会議等の純粋な社内活動）：`view_all_activities`横断権限のみで判定する

## 8.3 パーソン階層（Person／Contact／BusinessCard／OriginalImage）

### 8.3.1 継承できる範囲

| モデル | 判定方法 |
|---|---|
| Person | PersonListから直接判定 |
| Contact | Person経由でPersonListに委譲（`person`はNOT NULL FKであり、マージ時もサバイブ側へ付け替えられる＝第1章の前提確認の通り、常に現在の生存Personを指すため迷いなく継承できる） |
| BusinessCard（Contactあり、`ocr_result=business_card`） | Contact経由でPersonListに委譲 |

**統一QuerySetメソッド（v0.7で新設）**：`persons/models.py`のカスタムQuerySetに`PersonQuerySet.visible_for(user)`を実装する。`status='active'`かつ`パーソンリスト`経由の閲覧権限を持つPersonを返す統一メソッドとする（created_by特例は第8.3.4節の通り一覧には含めない）。View層・API層の一覧取得は必ずこのメソッドを経由し、Viewごとの個別クエリ手書きは禁止する（第8.1節のDealQuerySet.visible_for()と同じ理由）。

**特権バイパス（v0.8で追加、v0.9で共通ヘルパー経由に変更）**：`visible_for(user)`は第6.2節の`AccessListService.accessible_person_list_ids(user)`（特権バイパスを内包）を使い、`person_list_id__in=`で絞り込む。個別に`is_superuser`判定を書かないこと。第8.1節のDealQuerySet.visible_for()と同じく、未認証ユーザーには`self.none()`を返す。

```python
# persons/models.py（イメージ）
class PersonQuerySet(models.QuerySet):
    def visible_for(self, user):
        if not user.is_authenticated:
            return self.none()
        accessible_ids = AccessListService.accessible_person_list_ids(user)
        return self.filter(status='active', person_list_id__in=accessible_ids)
```

**編集可否（v1.2で新設）**：閲覧（`visible_for`）とは別に、Person全体の編集可否は`AccessListService.can_edit_person(user, person)`、Contact単位の編集可否は`can_edit_contact(user, contact)`（いずれも第6.2節）で判定する。View層・Service層の両方でこれを経由すること。

### 8.3.2 継承できない範囲

| モデル | 判定方法 |
|---|---|
| BusinessCard（Contactなし。`not_business_card`／`insufficient_info`／`ocr_failed`／`others`） | Personへの経路が存在しないため、PersonListは適用外。アップロード者本人（`OriginalImage.user`経由）＋機能レベル権限（`cards.view_card`等）で判定する |
| OriginalImage | 第8.3.3節参照 |

**実装前の確認事項**：上記のBusinessCard状態区分（`not_business_card`／`insufficient_info`／`ocr_failed`／`others`）が、実装時点のモデルの`choices`定義と一致しているか、コード君側で現物のコードを確認すること。

### 8.3.3 OriginalImageの積集合（AND）判定

1枚のOriginalImageは複数の名刺（複数のPerson）を含み得る（`検証画像`シリーズで実際に6〜9枚/画像の実例あり）。したがって「PersonListの単一継承」は成立せず、**その画像に紐づくContactを持つBusinessCardすべてについて、対応するPersonを閲覧できるユーザーだけがOriginalImageを閲覧できる（積集合／全称除算）**という条件にする。

判定対象は「Contactを持つBusinessCardのみ」に限定する。Contactを持たないBusinessCard（8.3.2の対象）は積集合の判定材料に含めない。含めてしまうと、名刺以外の写り込みが1枚でも混ざった画像が誰からも閲覧できなくなる不具合が生じるためである。該当するContact付きBusinessCardが1件も存在しない場合は、8.3.2と同じくアップロード者本人＋機能レベル権限のフォールバックのみで判定する。

**実装方針（SQL）**：v0.3でPersonList→AccessListがFK（単数）になったことに伴い、参照パスを簡素化した。**v0.9で修正**：素の`access_list__user_roles__user=user`ではなく、第6.2節の`AccessListService.accessible_person_list_ids(user)`（特権バイパスを内包）を経由する形に統一する。

```python
from django.db.models import Exists, OuterRef, Q

accessible_ids = AccessListService.accessible_person_list_ids(user)

# 対象画像に紐づく「Contactを持つ」BusinessCardのうち、
# このユーザーが閲覧できるPersonListに属さないものが1件でもあるか
denied_cards_with_contact = BusinessCard.objects.filter(
    original_image=OuterRef('pk'),
    contact__isnull=False,
).exclude(
    contact__person__person_list_id__in=accessible_ids
)

# 対象画像に「Contactを持つ」BusinessCardが1件でも存在するか
has_contact_card = BusinessCard.objects.filter(
    original_image=OuterRef('pk'),
    contact__isnull=False,
)

OriginalImage.objects.annotate(
    has_denied=Exists(denied_cards_with_contact),
    has_contact_card=Exists(has_contact_card),
).filter(
    # ケース1：Contact付きカードが存在し、かつ全員分の閲覧権限を持つ
    Q(has_contact_card=True, has_denied=False)
    # ケース2：Contact付きカードが1件もない（8.3.2と同じフォールバック対象）
    #         → アップロード者本人のみ（機能レベル権限は別途View層でAND判定）
    | Q(has_contact_card=False, user=user)
)
```

`accessible_person_list_ids(user)`が特権バイパスを内包しているため、スーパーユーザー・全社閲覧権限保持者はこの判定でも正しく除外されずに済む（v0.8で`visible_for()`にのみ追加した特権バイパスが、本節では反映されていなかった不整合の修正）。

`AccessListUserRole`が事前計算済みのフラット化キャッシュであるため、このサブクエリはインデックス検索のみで完結し、重い計算をクエリ内でやり直すことはない。

**必要インデックス**：`BusinessCard(original_image_id)`、`Contact(business_card_id)`（既存OneToOneで自動付与）、`AccessListUserRole(access_list_id, user_id)`。

**実装前の確認事項（v0.5で追加）**：上記SQL例では`BusinessCard`から`contact`という名前で逆参照しているが、これが実装上の実際の`related_name`（デフォルトのモデル名小文字か、カスタム設定か）と一致するか、コード君が現物のコードで確認すること（8.3.2節のBusinessCard状態区分の確認事項と同様の位置づけ）。

### 8.3.4 Contact.created_by本人の閲覧・編集特例（v1.2で編集を追加、範囲は変更なし）

`Contact.created_by`本人は、PersonListの設定に関わらず、**自身が作成した当該Contactレコード**を閲覧・編集できる。

この特例の本来の目的は「自分が入力した情報の正確性に自分が責任を持つ以上、その情報は常に見られる（そして訂正できる）べき」という点にある。マージによって合流してきた、自分とは無関係な他人作成のContact情報まで見せる・触らせる根拠にはならないため、Person全体ではなく作成した当該Contact単体に対象を限定する。

**v1.2での追加：編集可否**：v1.1までは閲覧の可否のみを定めていたが、第4.6節でPersonListに「編集範囲」設定（`creator_only` / `all_editors`）を新設したことに伴い、編集可否も明記する。

- **自分が作成したContactは、PersonListの権限（第3.3節）・編集範囲設定（第4.6節）に関わらず、常に編集可能**とする。これは「自分の入力ミスを訂正できないのは趣旨に反する」という理由による。判定は`AccessListService.can_edit_contact(user, contact)`（第6.2節）に一元化する
- **【v1.3で明記】この特例がバイパスするのはあくまでデータレベル権限（PersonListの権限・編集範囲設定）のみである。機能レベル権限（`contacts.change_contact`）自体は本特例によって不要にはならず、通常通りAND条件として必要**。コード君が「created_by特例＝機能レベル権限すら不要」と拡大解釈しないよう、実装指示書には両者を混同しない旨を明記すること
- 他人が作成したContactの編集可否は、通常通り第3.3節の権限レベルおよび第4.6節の編集範囲設定に従う（`creator_only`設定のPersonListでは、作成者以外は編集者権限を持っていても編集不可）
- この特例は、作成時点でPersonListへの編集権限を保持していたユーザーが、その後の異動・退職・第5.3節の例外処理等で権限を失った場合に主に機能する（第1章の前提通り、作成自体は編集者以上の権限を持つPersonListからしか選べないため、「最初から無権限で作成された」ケースは発生しない）

**画像への適用範囲（v0.4で修正）**：v0.3では「当該Contactに直接紐づくBusinessCard／OriginalImage」まで特例の対象としていたが、これは8.3.3の積集合判定をすり抜ける経路になっていた（1枚のOriginalImageに複数名刺が写る場合、そのうち1枚を自分が作成しただけで、他の写り込み全員分の元画像が見えてしまう）。v0.4では対象を以下のように分離する。

- **BusinessCard.card_image**（トリミング済みの1名刺単体の画像）：created_by本人は常に閲覧可能。1名刺分の情報しか含まないため、積集合の問題が発生せず、PersonListの判定を経由する必要もない
- **OriginalImage.image_file**（複数名刺を含み得る元画像そのもの）：created_by特例は**及ばない**。閲覧したい場合は8.3.3の積集合判定に厳密に従う（画像に写る他の全名刺についても、対応するPersonへの閲覧権限が必要）

**画面制御ルール**：PersonListの権限を持たないユーザーが、created_by特例によりアクセス可能なContactを持つ場合の画面挙動を以下の通り定める。

- **自身が作成したContactの詳細画面、および当該Contactが紐づくPersonの詳細画面**は403で弾かない。表示自体は許可する（200 OK）（v0.5で範囲を明確化：これはあくまで「自作Contactおよびその親Person」に限った話であり、**自身が作成していない他人のContactの詳細画面は、PersonListの閲覧権限がなければ従来通り403 Forbidden（または404）で弾く**。created_by特例が既存の403判定を全面的に無効化するものではない）
- Person詳細画面上で閲覧できるのは、**Personの基本属性（氏名等、個人が特定できる最小限の情報）**、**自身が作成したContact履歴**、および**自身が作成したContactに紐づくBusinessCard.card_image（トリミング済み画像）のみ**
- 他人が作成したContact履歴・OriginalImage（元画像）は、画面上でマスキング（非表示）する
- **Deal履歴について（v0.4で訂正）**：当該Personに紐づくDealの閲覧可否は、本特例による一律マスキングの対象としない。Dealの閲覧可否はDeal自身のAccessList（DealList経由）で独立に判定する。PersonListの権限がないことを理由に、Deal側で正当な閲覧権限を持つユーザーの表示まで奪ってはならない。**実装上の注意（v0.6で追加）**：`PersonDetailView`の`get_context_data`でテンプレートに渡すDeal一覧は、`person.deals.all()`のような素朴なクエリを渡してはならない。必ず第8.1節で定義した`DealQuerySet.visible_for(request.user)`を経由したQuerySetを渡すこと。素朴な全件取得を渡すと、DealListの権限を持たないDealまで画面に露出する

**既知の軽微な妥協点（v0.5で追記）**：Personの基本属性（氏名等）は、自身が作成したContact以外の情報（他人が後から訂正した氏名等）で上書きされている場合がある。created_by特例は「自分が作成したContactの内容」に限定する設計だが、Personの基本属性表示だけは例外的に最新値をそのまま見せる（自分のContact作成時点の値に固定しない）。実害は氏名程度の軽微なものであり、意図的に許容する妥協点として扱う。

**一覧画面（ListView）での扱い**：`PersonListView` / `ContactListView`等の一覧画面は、**PersonList経由の正規の権限のみで絞り込む**（created_by特例は一覧のフィルタ条件に含めない）。created_by特例は、**活動履歴・通知等、created_by本人に紐づく別画面からの直リンクアクセス時のみ**適用する（v0.5で明確化：通常のPerson／Contact検索画面はPersonList経由の権限だけで絞り込まれるため、権限のないPersonはそもそも検索結果に出てこない。「検索結果からの直リンク」という表現は誤解を招くため削除し、活動履歴・通知経由の直リンクに限定する）。理由：一覧クエリに`created_by`による結合条件を加えると、大量データ時の`distinct()`コストで一覧画面全体が重くなるため。

**実装上の要件**：Person詳細画面でContact履歴を列挙する箇所すべてで、「このContactは自分が作成したものか」による行単位の表示可否フィルタ、および編集ボタンの表示可否フィルタ（`can_edit_contact()`、v1.2）が必要になる（PersonListの範囲外にあるPersonでも、created_by本人であれば当該Contact行と、それに紐づくBusinessCard.card_imageだけは表示・編集可能とする）。この行単位フィルタと上記マスキングルールの実装をコード君向けの実装ルールとして明記すること。

**既存ルーティングとの整合（v0.9で追加）**：FG2の既存仕様では、Person詳細画面（`persons:person_detail`）は`Person.status == 'active'`の場合、専用画面を持たず代表コンタクト（`primary_contact`）の`ContactDetailView`へ302リダイレクトするアーキテクチャになっている。created_by特例のユーザーがこのURLを開いた際、代表コンタクトが他人の作成したものであれば、リダイレクト先の`ContactDetailView`で通常の403（8.3.4の「他人のContactは403で弾く」原則）に引っかかってしまい、「特例でPersonは見られるはずなのに何も見えない」という矛盾した挙動になる。`PersonDetailView`（または`persons:person_detail`のルーティング処理）は、**PersonListの正規権限を持たず、created_by特例のみでアクセスしているユーザーの場合、代表コンタクトへの302リダイレクトをバイパスし、当該ユーザー自身が作成したContactの詳細画面へリダイレクトする（または、代表コンタクトに依存しないマスキング専用のPerson画面を描画する）**よう分岐を追加すること。**タイブレーク規則（v1.0で追加）**：当該ユーザーが作成したContactが同一Person配下に複数存在する場合（役職変更等で複数回登録しているケース）、`created_at`降順で最新のものの詳細画面へリダイレクトする。

### 8.3.5 OriginalImageアップロード時のPersonList選択（v1.2で新設）

**背景**：新規Person作成方式を「作成時に編集者以上のPersonListから必ず選択させる」に統一した（第4.6節）ことに伴い、OCR経由での新規Person生成（OriginalImage→BusinessCard→Contact→Person）についても同様の選択が必要になる。ただしOCRは非同期処理（`process_pending`）であり、アップロード時点ではPerson／Contactがまだ存在しないため、Person／Contact作成フォームと同じ方式は使えない。

**フロー**：

1. OriginalImageアップロード画面で、`editable_person_list_ids(user)`（第6.2節）を選択肢としたPersonList選択を必須項目とする
2. 選択されたPersonListを`OriginalImage`モデルの新設フィールド「アップロード時指定パーソンリスト」（FK(PersonList, PROTECT, null=True)、related_name='+'、第4.7節参照）に保持する
3. OCR完了後、当該OriginalImageから新規に生成されるPerson（重複検出でどの既存Personともマージされなかったもの）に、このPersonListを適用する
4. 重複検出（DuplicateCandidate）により既存Personとマージされた場合は、このPersonListは適用しない（第10.1節の既存マージルールが優先する）
5. 1枚のOriginalImageに複数名刺（＝複数の新規Person）が含まれる場合（第8.3.3節参照）、新規生成される全Personに同一のPersonListが一律適用される

**本編仕様書側との調整が必要な点**：上記2の`OriginalImage`モデルへのフィールド追加は、本書（AccessList設計方針）のスコープを超え、`FreeGroup2本編仕様書_v1_6_3`側のOriginalImageモデル定義への変更を伴う。実装前に本編仕様書側の担当ラインとの整合確認が必要である。

### 8.3.6 CSVインポート（v1.7）とのPersonList整合（v1.2で新設）

`FreeGroup2_インポートエクスポート機能_仕様書_v1_6`で仕様策定中のCSVインポート機能は、1回の操作で複数のPersonを一括生成する。第4.6節の「作成時に編集者以上のPersonListから選択」ルールを、以下の方式で適用する。

- インポート実行前の画面で、`editable_person_list_ids(user)`を選択肢としたPersonList選択を1回だけ行わせる
- 選択されたPersonListを、当該インポート回で新規生成される全Personに一括適用する（行ごとの個別選択は行わない）
- 既存Personとのマッチング（重複検出）によって新規作成されずに更新扱いとなる行については、このPersonList指定は適用しない（第10.1節の既存マージルールに準ずる）

**確認が必要な点**：`FreeGroup2_インポートエクスポート機能_仕様書_v1_6`は既にレビュー完了・承認済み（v1.6）であり、PersonList関連の記述の有無、および画面フロー（インポート設定画面の項目構成）との整合を、インポート仕様書側の担当ラインと突合する必要がある。本書はインポート機能側の詳細フローまでは規定しない。

## 8.4 既存DealUser／ActivityUserとの共存

`FreeGroup2_案件管理機能_仕様書_v1_10` §5.3の通り、DealUser／ActivityUserはAccessList導入後も消えず、「ACL上は見えない設定でも、関与者として登録されていれば個別に見える」というOR条件の恒久的な仕組みとして残り続ける（閲覧について）。**v1.2で追加**：編集可否についても、第4.5節の編集範囲設定が`creator_and_participants`の場合、DealUserであることが編集可否判定（`can_edit_deal`、第6.2節）に加算的に作用する。ただし編集範囲設定が`creator_only`または`all_editors`の場合、DealUserであることは編集可否に影響しない（`creator_only`は作成者のみ、`all_editors`はリスト編集者全員であり、いずれの場合もDealUser該当性そのものは判定条件に含まれない）。

---

# 第9章 デフォルトリストと導入時の移行

## 9.1 デフォルトPersonList／デフォルトDealListの新設

データマイグレーションで以下を作成する。

- 「デフォルトAccessList」を1件作成し、ACLEntry「ルート部署（全社員が属する最上位のDepartment）：編集者」を1行だけ設定する
- 「デフォルトPersonList」「デフォルトDealList」を1件ずつ作成し、それぞれの`アクセスリスト`FKに上記デフォルトAccessListを設定する。編集範囲は両方とも`all_editors`（デフォルト値）とする

階層継承（第5.2節）により、この1行だけで組織全体が自動的にカバーされる。「全員」という特別な対象種別を新設する必要はない。

権限レベルは`編集者`とする。実際に編集できるかは機能レベル権限（`contacts.change_contact`等）との**AND判定**で最終的に決まるため、データレベル権限を`編集者`にしておいても、機能レベル権限を持たないユーザーが新たに編集できるようになるわけではない。導入前の「制限なし」の挙動を素直に再現できる。

**v1.2での役割変更**：v1.1までは「新規レコード作成時、未指定ならこのデフォルトリストを自動的に採用する」という自動補完先としての役割を持っていたが、v1.2で新規作成時は必ずユーザーが選択する方式に変更したため（第4.5節・第4.6節）、デフォルトPersonList／デフォルトDealListの役割は「①第9.2節で述べる既存データの一括移行先」「②`editable_*_list_ids()`の選択肢の1つとして通常は全社員に提示される候補」の2つに変わる。自動的に何かへ割り当てられる特別な存在ではなくなった。

**部署未所属ユーザーの扱い**：LDAP自動生成直後で、まだどのDepartmentにも割り当てられていないユーザーは、階層継承の対象外となり、デフォルトリストを含む**全AccessListについて閲覧権限を持たない**。これは意図的なフェイルセーフ（ホワイトリスト方式の帰結）であり、バグではない。管理者によるDepartment割り当て・PermissionSetAssignmentでの個別承認を経て初めて、各種データを閲覧できるようになる。運用担当者向けのドキュメント（ヘルプ・FAQ等）にもこの挙動を明記し、「新入社員が何も見えない」という問い合わせに仕様書一枚で即答できるようにしておくこと。

**編集可能なリストが0件のユーザーの扱い（v1.2で新設・重要）**：第5.3節の例外処理（配下部署の一部除外）を使うと、特定のユーザーだけがデフォルトPersonList／デフォルトDealListへの編集者権限を含む、あらゆるリストへの編集権限を失うケースが起こり得る。この場合、当該ユーザーは新規Person／Deal作成画面を開いても選択肢が0件になり、**新規作成自体ができない**。

この挙動は**意図した仕様として許容する**。特別な救済ロジック（デフォルトリストだけは強制的に選択肢に残す等）は設けない。編集可能なリストを1件も持たないユーザーが存在することは正常な状態であり、そのようなユーザーが新規レコードを作成しようとした場合は、「選択可能なリストがありません」等のエラーメッセージで案内し、必要であれば管理者に個別のリストへの編集権限付与を依頼する運用とする。

**ルート部署が存在しない場合のマイグレーション防御（v0.9で追加）**：新規開発環境の構築、Dockerコンテナ立ち上げ、CI（GitHub Actions等）でのテスト実行時など、LDAP同期が一度も走っておらず`Department`テーブルが空（0件）の状態で`manage.py migrate`が実行されるケースが多々ある。データマイグレーションが`Department.objects.filter(parent__isnull=True).first()`の戻り値（`None`の可能性がある）を無条件に参照してACLEntryを作成しようとすると、マイグレーション自体がクラッシュする。データマイグレーションは、ルート部署が存在しない場合、**ACLEntryの作成をスキップし、警告ログを標準出力に残して正常終了**させること。後からルート部署が作成された際は、`python manage.py rebuild_all_access_lists`（第6.3節）を実行すれば自己修復できる。

## 9.2 既存レコードの一括登録

導入時のデータマイグレーションで、既存の全Person・全Dealを、それぞれデフォルトPersonList・デフォルトDealListに一括登録する。これにより導入初日に業務が止まることを防ぐ。第4.5節・第4.6節の通り`案件リスト`／`パーソンリスト`はNOT NULLであるため、このマイグレーションはスキーマ変更と同一トランザクション内、またはスキーマ変更直後に必ず実行すること。

**マイグレーション手順の注記（v0.4で追加）**：既存レコードが存在するDeal・Personに対して、いきなりNOT NULLのFKを追加すると、通常の`makemigrations`は既定値の入力を求める対話型プロンプトで停止し、CI・自動デプロイが失敗する。コード君は以下の3段階でマイグレーションを組むこと。

1. `null=True`でカラムを追加するマイグレーション
2. データマイグレーションで、既存の全レコードにデフォルトPersonList／デフォルトDealListを設定する
3. `null=False`に変更するマイグレーション

**v1.2での確認**：上記手順はモデル定義自体の`null`制約（DB制約）の話であり、v1.2で変更した`blank`（フォームバリデーション）とは独立している。この3段階手順自体に変更はない。

## 9.3 運用方針

PersonList／DealListの粒度は**粗く**保ち、個別リストへの分割は本当に機密性の高い一部の対象に限定する（例外は少数に留める）。理由は第10.2節（重複候補レビューの詰まり）参照。部署ごとに細かくリストを作りすぎると、リストをまたぐ重複候補が頻発し、レビューできる人が限られて滞留しやすくなる。

---

# 第10章 Person統合（マージ）時の考慮事項

## 10.1 PersonListが異なるPerson同士のマージ

重複検出によるマージは、PersonList=X（例：営業部限定）のPersonと、PersonList=Y（例：役員限定）のPersonの間でも起こり得る。機械的に生存Person側のPersonListをそのまま採用すると、意図しない情報の公開範囲拡大・縮小が起こり得る。

**対応方針**：

- 重複候補レビュー画面で、両Personの`PersonList`が異なる場合に警告を表示する
- **v1.2で追加**：両Personの`PersonList`の**編集範囲設定**（第4.6節）が異なる場合も、同様に警告を表示する（例：PersonList=X が`creator_only`、PersonList=Y が`all_editors`の場合、統合後にどちらの編集範囲ルールが適用されるかで、既存Contactの編集可否が変わり得るため）
- 統合後のPersonListをどちらにするか（あるいは維持するか）をレビュー担当者に明示的に選択させる
- 未選択時のデフォルトは**生存Person側のPersonListを維持**

**適用範囲の明確化**：本メカニズムが手当てするのは、PersonList経由の閲覧範囲・編集範囲のみである。第8.3.4節のContact.created_by特例はPersonList判定をバイパスする別経路であり、本メカニズムの対象外である（8.3.4節の方針により、この特例による情報漏れリスクは、対象をContact単体に限定することで別途解消している。編集についても同様に、created_by特例は編集範囲設定に関わらず常に有効）。

**マージ処理内での実行タイミング**：既存のマージ実行処理（`duplicates/services/merge_executor.py`内の`Execute_Merge_Only`）のうち、Contactの付け替え処理（`FreeGroup2本編仕様書_v1_6_3` §9.4.2）が完了した後、`Person.status='merged'`への変更が確定する前後のいずれかのタイミングで、同一トランザクション内に`surviving_person.person_list = selected_person_list`をセットし`save(update_fields=['person_list'])`を実行する処理を差し込む。既存手順の正確な行番号・前後関係はコード君が`merge_executor.py`の現物を確認して決定すること。**この更新は単純なフィールド更新のみで完結し、`AccessListService`の再計算呼び出しは不要である**（第6.1節参照：Person側のリスト所属変更はAccessListUserRoleキャッシュに影響しないため）。

**被統合側（merged_person）のPersonListの扱い（v0.7で明記）**：被統合側（`merged_person`、`status='merged'`）の`person_list`は**変更せず、元の値をそのまま維持する**。サバイブ側にだけ選択結果を反映し、マージド側のデータには触れない。これは既存のマージ設計（`FreeGroup2本編仕様書_v1_6_3` §9.4.1「サバイブ側に関する設計趣旨」）が徹底している「マージド側のデータは極力弄らない」という原則と一貫しており、マージ取り消し（Undo）時のデータ整合性も保ちやすくなる。

**マージ取り消し（Undo）時のPersonList復元**：v0.1で確認した通り、Person統合の復元は既存の仕組み（`PersonMergeLog`、1段階前まで復元可能）に乗せる。本書のマージ時PersonList変更（上記）を復元可能にするため、`PersonMergeLog`に以下のフィールドを追加する。

| フィールド名 | 型 | 説明 |
|---|---|---|
| マージ前パーソンリスト | FK(PersonList, PROTECT, null=True, related_name='+') | マージ実行時点での、サバイブ側Personの`person_list`の値（本節の変更が上書きする直前の値）を退避する |

`Execute_Merge_Only`内で`surviving_person.person_list`を更新する直前に、更新前の値を`PersonMergeLog.person_list_before_merge`へ記録する。`Execute_Merge_Undo`（`duplicates/services/merge_undo.py`）の復元処理内に、`surviving_person.person_list = merge_log.person_list_before_merge`を再セットする手順を追加する（こちらも単純なフィールド更新のみで、`AccessListService`の呼び出しは不要）。

**Undo実行時のNULLフォールバック（v0.5で追加）**：本フィールド追加より前に作成された既存の`PersonMergeLog`レコードは、`person_list_before_merge`が`NULL`のままである。そのようなレコードに対してUndoを実行すると、`surviving_person.person_list`にNoneをセットしようとして`person_list`のNOT NULL制約に違反し、`IntegrityError`でクラッシュする。`Execute_Merge_Undo`の実装では、`merge_log.person_list_before_merge`が`NULL`の場合、**デフォルトPersonList（第9.1節）をフォールバックとしてセットする**という安全弁を設けること。

## 10.2 重複候補（DuplicateCandidate）レビュー可否の判定

`DuplicateCandidate`（PersonA vs PersonB）をレビューできるのは、**PersonAとPersonB両方のPersonListに閲覧権限を持つユーザーのみ**とする。第6.2節の`AccessListService.accessible_person_list_ids(user)`を経由し、特権バイパスを反映する。

**v1.0での訂正**：`accessible_person_list_ids(user)`は遅延評価のQuerySetを返すため、これを`person_a`・`person_b`の2箇所でそのまま使い回しても、コンパイルされたSQL上は同じサブクエリが2回埋め込まれるだけで、実行結果は正しいが「1回の計算結果を使い回す」という性能上の最適化にはならない。本当に1回だけ評価して使い回したい場合は、`set(...)`等で先に具体化してから両条件に渡すこと。

```python
accessible_ids = set(AccessListService.accessible_person_list_ids(user))

candidates_in_group.filter(
    Q(person_a__person_list_id__in=accessible_ids) &
    Q(person_b__person_list_id__in=accessible_ids)
)
```

権限を持たない候補は、その人のレビュー画面には最初から表示されない（自動スキップ）。これにより、通常の営業担当は自分のPersonListの範囲内で完結する候補だけをレビューでき、PersonListをまたぐ候補は両方を見られる人（多くの場合デフォルトPersonListを見られる管理者等）に自然に絞り込まれる。

## 10.3 マージ実行時のデータレベル権限チェック（v0.6で新設）

第10.2節はレビュー**画面の表示可否**（閲覧権限）を定めたものであり、実際にマージを**実行**するService層（`can_merge_person` / `Execute_Merge_Only`）の認可チェックとは別に明記する必要がある。マージはPersonの一方を`status='merged'`にし、Contactを付け替える重大なデータ変更操作であり、View層とService層の二重防衛というFG2の既存原則（`_最終版_FreeGroup2_v1_5_0_認証_認可_LDAP_設計方針v1_5_3`）に従い、Service層でも単なる閲覧権限以上のチェックを行う。

`can_merge_person(user, person_a, person_b)`は、既存の機能レベル権限（`persons.merge_person`）のチェックに加えて、**ユーザーがperson_aとperson_bの両方のPersonListに対して「編集者」以上の権限を持つこと**を必須条件とする。閲覧権限（閲覧者）だけではマージを実行できない。

**v1.2での確認**：この`can_merge_person`の権限レベル判定（第3.3節）は、第4.6節の「編集範囲」設定とは独立している。編集範囲が`creator_only`のPersonListであっても、マージ実行という操作自体はPerson個々の`created_by`とは無関係な管理的操作であるため、`can_merge_person`の判定基準（権限レベルのみ）を変更する必要はない。

---

# 第11章 未確定事項（次フェーズ持ち越し）

| 論点 | 内容 |
|---|---|
| 制限閲覧者（freeBusyReader）の扱い | 旧FGでも判定ロジックに未組み込み。本書では扱わない |
| UI設計 | ACLEntryの順序入れ替え画面、PersonList/DealList管理画面等の具体的な画面設計。並び順ミスを防ぐ導線（第5.1節）もあわせて検討 |
| `Department.descendants()`の置き換え時期 | 第7.2節の基準に該当した時点で再検討 |
| CSVインポート仕様書側との突合（v1.2で追加） | 第8.3.6節の内容を`FreeGroup2_インポートエクスポート機能_仕様書_v1_6`側の担当ラインと突合する必要がある |
| OriginalImageモデルへのフィールド追加（v1.2で追加） | 第8.3.5節の「アップロード時指定パーソンリスト」フィールドは本編仕様書側（`FreeGroup2本編仕様書_v1_6_3`）のモデル定義変更を伴うため、担当ラインとの調整が必要 |

---

# 巻末別表　モデル名・フィールド名対照表

## 別表A モデル名

| 日本語名 | コーディング名 |
|---|---|
| アクセスリスト | AccessList |
| アクセスリストエントリ | ACLEntry |
| アクセスリストユーザー権限 | AccessListUserRole |
| ユーザーグループ | UserGroup |
| 案件リスト | DealList |
| パーソンリスト | PersonList |

## 別表B AccessListのフィールド

| 日本語名 | コーディング名 |
|---|---|
| 名前 | name |
| 説明 | description |
| 作成者 | created_by |
| 作成日時 | created_at |
| 更新日時 | updated_at |

## 別表C ACLEntryのフィールド

| 日本語名 | コーディング名 |
|---|---|
| アクセスリスト | access_list |
| 並び順 | order |
| 権限レベル | permission_level（生の設定値。未設定を含む。AccessListUserRoleの`role`とは意図的に別名、第4.3節参照） |
| 対象種別 | target_content_type |
| 対象ID | target_object_id（CharField(64)） |
| 対象 | target |

## 別表D AccessListUserRoleのフィールド

| 日本語名 | コーディング名 |
|---|---|
| アクセスリスト | access_list |
| ユーザー | user |
| 権限レベル | role（確定値。管理者／編集者／閲覧者のみ、第4.3節参照） |

## 別表E UserGroupのフィールド

| 日本語名 | コーディング名 |
|---|---|
| 名前 | name |
| 説明 | description |
| メンバー | members |
| 作成者 | created_by |

## 別表F DealList／PersonListのフィールド（共通構造、v0.3で変更、v1.2で編集範囲を追加）

| 日本語名 | コーディング名 |
|---|---|
| 名前 | name |
| 説明 | description |
| アクセスリスト | access_list（v0.3でaccess_listsから変更。FK、単数） |
| 編集範囲 | edit_scope（v1.2で新設。DealList: `creator_only`/`creator_and_participants`/`all_editors`、PersonList: `creator_only`/`all_editors`。実際のコーディング名はコード君が実装時に確定） |
| 作成者 | created_by |

## 別表G 既存モデルへの追加フィールド

| モデル | 日本語名 | コーディング名 | 備考 |
|---|---|---|---|
| Deal | 案件リスト | deal_list | FK, PROTECT, NOT NULL(null=False, blank=False。v1.2で`blank=True`から変更) |
| Person | パーソンリスト | person_list | FK, PROTECT, NOT NULL(null=False, blank=False。v1.2で`blank=True`から変更) |
| PersonMergeLog | マージ前パーソンリスト | person_list_before_merge | FK(PersonList, PROTECT, null=True, related_name='+')。v0.4で追加、マージ取り消し時の復元用（第10.1節）。Undo実行時、値がNULLの場合はデフォルトPersonListへフォールバック（v0.5） |
| OriginalImage | アップロード時指定パーソンリスト | uploaded_person_list（仮、コード君が実装時に確定） | FK(PersonList, PROTECT, null=True, related_name='+')。v1.2で新設（第8.3.5節）。本編仕様書側との調整が必要 |
