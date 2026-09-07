# FreeGroup2 コンタクトCSVエクスポート・インポート機能 仕様書 v1.6

**作成日**：2026-09-07
**位置づけ**：メーリングリストメンバーのCSVエクスポート、およびコンタクトCSV一括インポート（Person生成まで）を新設する機能の正式仕様書。ジット君・ジェミニ君による6ラウンドのレビューを経て確定。**本版をもってコード君向け実装指示書の作成に着手する。**
**スコープ**：`contacts.export_contact`／`contacts.import_contact`の新設Permission、コンタクトのCSVエクスポート（メーリングリスト詳細画面起点）、コンタクトCSV一括インポート（コンタクト一覧画面起点）、および付随する`DEFAULT_CONTACT_COUNTRY`設定の新設。

## v1.5からの主な変更点（ジット君のレビュー反映）

- **§4.1 用語統一の取り残しを修正**：v1.5でプレビュー画面の集計区分を「成功見込み件数／スキップ見込み件数」の2区分に統一した際、「プレビュー画面のテーブル表示件数」の段だけ旧表現（「正常見込み件数・要確認件数」）が置き換え漏れとして残っていたため修正した

## 実装指示書向けの留意事項（ジェミニ君指摘、仕様書本体には含めず指示書側で補足）

- エクスポート項目選択画面で0件選択のままPOSTされた場合、Formの`clean()`で「1項目以上選択してください」のバリデーションエラーを出し、フォームを再表示する
- CSVヘッダー行の読み込み時、`header.strip()`等で前後空白を除去してからマッピング辞書と照合する
- アップロードファイルが0バイト・空の場合、Formの`FileField`必須チェックに加え、CSVリーダーがデータ行0件だった場合はプレビュー画面で「データ行が存在しません」と差し戻す
- 完了結果画面のボタンは、二重送信防止トークンが無効化された後のプレビュー画面への「戻る」ではなく、`contacts:contact_list`への直行リンクとする
- ActionLogは1行ごとに記録せず、一括処理完了時に「CSVインポート完了（○件成功、○件スキップ）」として全体で1件記録する（本フェーズのスコープに含めるかは実装時判断）

---

# 第1章 概要と設計方針

## 1.1 目的

FreeGroup2で蓄積されたコンタクト情報を、以下2方向でCSVとしてやり取りできるようにする。

1. **エクスポート**：メーリングリストのメンバーを起点に、コンタクト情報をCSVとして持ち出す
2. **インポート**：他システムからのコンタクトデータ移行を、CSVから一括でPerson・Contactを生成する形で受け入れる

設計の柱は次の2つ。

第一に、**エクスポートしたCSVがそのままインポートに戻せる（往復可能性）**こと。ユーザーがエクスポート画面で「インポート用の項目」をまとめて選択できるようにし、他システム連携用の軽量出力と、再投入前提のフル出力を1つの画面で両立させる。

第二に、**インポートは既存のOCR経由・手動作成経路と同じ正規化基盤（`contacts/services/normalization.py`）を通す**こと。CSVインポートはForm（`ContactBaseForm`）を経由しない一括処理のため、正規化関数を明示的に呼び出す設計とする。

## 1.2 四層データモデルとの関係

エクスポート対象はメーリングリストメンバー（`MailingListMember`）が持つ`person`を起点とし、必ず`person.get_surviving_person().primary_contact`を正本として解決する。Personがマージされている場合、`MailingListMember.person`が指す先が既に統合済み（`status='merged'`）である可能性があるため、単純な`person.primary_contact`ではなく生存Personを解決してから`primary_contact`を参照する。

インポートは、既存のOCR経由（`cards/tasks/ocr_pipeline.py` `_finalize_success()`）・手動作成経路（`contacts/views.py` `_create_person_and_contact()`）と同様に、**新規Person→新規Contact（`status='primary'`）→`person.set_primary_contact(contact)`**という核の流れを踏襲する。ただし既存2箇所のロジックはどちらもサービス層に独立しておらず、CSVインポート専用の新規サービス関数として新設する（既存コードの流用・共通化はコード君の実装時判断に委ねる）。

## 1.3 文字コード規約

- 文字コード：Excelでの文字化け防止のため、すべて**UTF-8 with BOM**（`utf-8-sig` / レスポンス生成時`"\ufeff" + buf.getvalue()`）を標準とする
- レスポンスヘッダー：`Content-Type: text/csv; charset=utf-8`、`Content-Disposition: attachment; filename*=UTF-8''...`

## 1.4 スコープ外事項

以下は本仕様書のスコープに含めない。

| 項目 | 理由 |
|---|---|
| 会社（Company）モデルとの紐付け（`link_contact_to_company()`） | Companyモデル・companiesアプリ自体が未実装（調査確認済み）。実装できない前提を仕様に含めない |
| SNS（ContactSns）のインポート・エクスポート | v1.6.1で別テーブル化されており、CSVの1行1レコード構造と相性が悪い。将来別途検討 |
| コンタクト一覧画面からの単体コンタクトエクスポート | 依頼書に記載はあったが、今回の目的（メーリングリスト起点のエクスポート／インポート→Person生成）には含まれない。ただし本仕様書のPermission（`contacts.export_contact`）は将来この機能にもそのまま流用できる設計とする |

## 1.5 HIG準拠事項

- 一覧画面のテーブル操作列・ボタン配置は既存HIG（v1.6.3）に従う
- 一括インポート処理結果のフィードバックは、成功件数・スキップ件数・エラー理由を行番号付きで明示する（HIG 5.1／5.2、PRGパターンで遷移後に状態表示）
- 画面上のメールアドレス表記は「メール」に統一し、「コンタクト」と区別する（HIG 3.9）
- 戻る導線は`BackNavigator`（`back_link`／`back_all_link`）を使用する

---

# 第2章 認可設計（Permission／Group）

## 2.1 新設Permission定義

| codename | 説明 | 対象モデル |
|---|---|---|
| `contacts.export_contact` | コンタクトのCSVエクスポート（メーリングリスト詳細画面からの呼び出しを含む） | Contact（例外・手動定義） |
| `contacts.import_contact` | コンタクトCSV一括インポート | Contact（例外・手動定義） |

既存の`mailings.export_report`（v1.6）・`tags.assign_tag`と同じ「例外Permission」方式を踏襲する。データの持ち出し（エクスポート）・一括投入（インポート）は、情報漏洩・データ汚染リスクが高いため、単なる閲覧権限（`view_contact`）や単発作成権限（`add_contact`）とは切り離す。

**Permissionをcontactsアプリに置く理由**：エクスポート対象は`MailingListMember`固有のデータではなく、Person経由のContactフィールドである。画面の入口がメーリングリスト詳細画面であることと、Permissionが宣言されるモデルは独立して決めてよく、`contacts.export_contact`を該当グループに配布すれば画面の実態と権限モデルは矛盾なく成立する。

## 2.2 グループへの配分表

| Group | 追加するPermission |
|---|---|
| `contact_admin` | `contacts.export_contact`、`contacts.import_contact` |
| `contact_editor` | `contacts.export_contact`、`contacts.import_contact` |
| `campaign_admin` | `contacts.export_contact` |
| `campaign_editor` | `contacts.export_contact` |

`contact_viewer`・`campaign_viewer`には**一切付与しない**（既存の設計原則：閲覧専用グループにはデータ持ち出し・投入系権限を配らない）。

**新設する全Viewへの個別付与を徹底する**：既存の`mailings/views.py`の規約に倣い、View（画面）ごとに`permission_required`を個別に設定する。本機能で新設するView**すべて**に、該当するPermissionを付与すること。

| 新設View | 付与するPermission |
|---|---|
| エクスポート項目選択View（`MailingListMemberExportFormView`） | `contacts.export_contact` |
| エクスポート実行View（`MailingListMemberExportView`） | `contacts.export_contact` |
| インポート・アップロードView | `contacts.import_contact` |
| インポート・プレビュー／確定View | `contacts.import_contact` |
| インポート・完了結果View | `contacts.import_contact` |

View構成の変更があった場合は、この対応表も必ず更新すること（本表自体が過去に更新漏れを起こした経緯があるため、View構成を変える改訂の際は本表を最優先で見直す）。

## 2.3 マイグレーション実装方針

既存の`accounts/migrations/0004`〜`0014`と同一の書き方を踏襲する。

- マイグレーション冒頭で`create_permissions()`を明示的に呼び、`apps=apps`・`using=schema_editor.connection.alias`を渡す（post_migrateシグナル待ちを回避する定石、既存6本と同じ形）
- `app_config.models_module`には触れない（`None`に書き換えると、その後のPermission生成が丸ごとスキップされる副作用があるため）
- Group取得は`get_or_create()`、Permission付与は`.add()`（冪等性の確保）
- `dependencies`は、`contacts`アプリの初期マイグレーションが先に適用されている実番号を明示的に固定する
- 配置：`accounts/migrations/0015_grant_export_import_contact.py`（現時点の最新が`0014_grant_manage_role.py`のため連番`0015`）

```python
# accounts/migrations/0015_grant_export_import_contact.py（実装イメージ）
from django.apps import apps as django_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


def ensure_permissions(apps, schema_editor):
    for app_config in django_apps.get_app_configs():
        create_permissions(
            app_config,
            apps=apps,
            using=schema_editor.connection.alias,
            verbosity=0,
        )


def grant(apps, schema_editor):
    ensure_permissions(apps, schema_editor)
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    export_perm = Permission.objects.get(
        content_type__app_label="contacts", codename="export_contact"
    )
    import_perm = Permission.objects.get(
        content_type__app_label="contacts", codename="import_contact"
    )

    for name in ("contact_admin", "contact_editor"):
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.add(export_perm, import_perm)

    for name in ("campaign_admin", "campaign_editor"):
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.add(export_perm)


def reverse(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    export_perm = Permission.objects.filter(
        content_type__app_label="contacts", codename="export_contact"
    ).first()
    import_perm = Permission.objects.filter(
        content_type__app_label="contacts", codename="import_contact"
    ).first()
    for name in ("contact_admin", "contact_editor", "campaign_admin", "campaign_editor"):
        group = Group.objects.filter(name=name).first()
        if group:
            if export_perm:
                group.permissions.remove(export_perm)
            if import_perm:
                group.permissions.remove(import_perm)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0014_grant_manage_role"),
        ("contacts", "0009_contact_display_name_is_manual_and_more"),
    ]
    operations = [
        migrations.RunPython(grant, reverse),
    ]
```

`Contact.Meta.permissions`に`export_contact`／`import_contact`を追加するマイグレーション（`contacts`アプリ側）が本マイグレーションより先に適用されている必要がある。作業順序として、まず`contacts/models.py`の`Contact.Meta.permissions`にこの2つを追加し、`contacts`アプリ側のマイグレーションを作成してから、その実番号を上記`dependencies`に固定すること。

---

# 第3章 コンタクトCSVエクスポート仕様

## 3.1 起点画面とURL／View設計

- 起点画面：メーリングリスト詳細画面（`mailings/templates/mailings/mailing_list_detail.html`）の見出し行にエクスポートボタンを配置する
- **カラム選択UIは専用画面に遷移させる**（詳細画面に直接埋め込まない）。既存の配信レポートCSV（`campaign_report_csv_form.html`）と同型の、専用View＋専用テンプレート（`mailing_list_export_form.html`）を新設する。理由：30項目分のチェックボックスを詳細画面に直接埋め込むと画面が煩雑になり、HIGの画面構成原則（起点画面はハブとして機能に遷移する構造）にも合わない
- View：エクスポート項目選択画面用View（`MailingListMemberExportFormView`）と、実際のCSV生成・ダウンロードを行うView（`MailingListMemberExportView`）の2段構成とする
- 認可：`permission_required = "contacts.export_contact"`（mailingsアプリのView内でcontactsアプリのPermissionを評価する。テンプレート側でボタン表示を分岐する場合は`{% if perms.contacts.export_contact %}`と記述する）
- URL：`mailings:mailing_list_member_export_form`（項目選択画面）、`mailings:mailing_list_member_export`（CSVダウンロード実行）

## 3.2 出力カラム全体定義

すべて`person.get_surviving_person().primary_contact`から取得する。

| カラム | 対応するContactフィールド | インポート時の扱い |
|---|---|---|
| Person ID | `person.id`（`get_surviving_person()`解決後） | インポート対象外（識別用） |
| 姓 | `last_name` | インポートで受け付ける（必須） |
| 名 | `first_name` | インポートで受け付ける（必須） |
| 氏名 | `full_name` | インポートで受け付ける（任意） |
| その他の名前部分 | `other_name_parts` | インポートで受け付ける（任意） |
| 言語コード | `lang` | インポートで受け付ける（任意） |
| 氏名の並び順 | `name_order` | **参考出力のみ**。インポート時は無視（`lang`から自動導出） |
| 宛名 | `salutation_name` | **参考出力のみ**。インポート時は無視（`compute_salutation_name()`で自動生成） |
| 会社名 | `organization` | インポートで受け付ける（任意） |
| 部署 | `department` | インポートで受け付ける（任意） |
| 役職 | `title` | インポートで受け付ける（任意） |
| 支店 | `branch` | インポートで受け付ける（任意） |
| 資格 | `qualification` | インポートで受け付ける（任意） |
| キャッチフレーズ | `catchphrase` | インポートで受け付ける（任意） |
| 郵便番号 | `postal_code` | インポートで受け付ける（任意） |
| 国 | `country` | インポートで受け付ける（任意、未入力時`DEFAULT_CONTACT_COUNTRY`補完） |
| 中間行政区画 | `region` | インポートで受け付ける（任意） |
| 市区町村 | `city` | インポートで受け付ける（任意） |
| 残り住所 | `rest_of_address` | インポートで受け付ける（任意） |
| 住所（完成形） | `address` | **参考出力のみ**。インポート時は無視（分割データから自動組み立て） |
| メール | `email` | インポートで受け付ける（任意） |
| 携帯電話 | `mobile_phone` | インポートで受け付ける（任意） |
| 個人直通電話 | `personal_phone` | インポートで受け付ける（任意） |
| 個人FAX | `personal_fax` | インポートで受け付ける（任意） |
| 会社代表電話 | `org_phone` | インポートで受け付ける（任意） |
| 会社FAX | `org_fax` | インポートで受け付ける（任意） |
| ウェブサイト | `website` | インポートで受け付ける（任意） |
| メモ | `notes` | インポートで受け付ける（任意） |
| 退会状態 | `MailingListMember.is_unsubscribed`（`退会済み`／空欄） | インポート対象外（メーリングリスト固有データ） |

## 3.3 ユーザー選択式カラム＋「インポート用」一括選択トグル

既存のメール配信CSVエクスポート（v1.6 rev21 §13.2）と同じ「ユーザー選択式」の思想を踏襲する。§3.1の専用画面（`mailing_list_export_form.html`）上に配置する。

- 画面には各カラムのチェックボックスを個別に配置する
- 加えて「**インポート用の項目をまとめて選択**」というトグルを1つ用意する。ONにすると、上表で「インポートで受け付ける」に分類された全カラムのチェックボックスが自動的にONになる（`name_order`／`salutation_name`／`address`／Person ID／退会状態は対象外のまま）
- 個別チェックボックスは、トグルON後も手動で外せる（トグルは一括選択のショートカットであり、選択を固定するものではない）
- 初期状態はトグルOFF。デフォルトの選択状態は依頼書記載の9項目（氏名・会社名・役職・部署名・宛名・郵便番号・住所・メール・退会状態・Person ID）とする

## 3.4 「参考出力のみ」列の明記

`name_order`・`salutation_name`・`address`の3列は、エクスポートには含められるが、**再インポート時には無視される**。この旨をエクスポート画面・インポート画面の両方に注記として表示する（HIG原則4「補足説明はツールチップに」に従い、列見出し横の「?」アイコンで説明する）。

## 3.5 N+1対策クエリ・Personマージ解決ロジック

既存クエリ（依頼書記載）をベースに、生存Person解決のため`select_related`を拡張する。

```python
members_qs = (
    MailingListMember.objects.filter(mailing_list=mailing_list)
    .select_related(
        "person",
        "person__primary_contact",
        "person__merged_into",
        "person__merged_into__primary_contact",
        "added_by",
    )
)

for member in members_qs:
    surviving_person = member.person.get_surviving_person()
    contact = surviving_person.primary_contact
    if contact is None:
        # primary_contact が存在しないケースは、Person作成時に必ず
        # set_primary_contact() が呼ばれる既存フローの前提上、通常運用では
        # 基本的に発生しない（防御的なガード）。発生した場合はログにのみ記録し、
        # 黙ってスキップする。確認画面・画面上の注記は設けない。
        logger.warning("エクスポート対象Person(id=%s)にprimary_contactが存在しないためスキップ", surviving_person.id)
        continue
    ...
```

多段マージ（A→B→C）時は`get_surviving_person()`の2ホップ目以降で追加クエリが発生するが、既存の`FreeGroup2_案件管理機能_仕様書_v1_5.md`§8.2と同じ「既知の許容事項」として受容する（実運用で3段以上の連鎖は稀なため）。

## 3.6 ファイル名命名規則

`メーリングリスト_{リスト名}_{YYYYMMDD}.csv`（日本語を含むため、`filename*=UTF-8''`形式でRFC 5987準拠のパーセントエンコード（`urllib.parse.quote`）を用いる。既存の配信レポートCSV（`mailings/views.py`）は半角英数字ファイル名のため`filename="..."`のみだが、本機能は日本語ファイル名のため新規にこの方式を採用する）。

---

# 第4章 コンタクトCSVインポート仕様

## 4.1 画面遷移フロー

1. コンタクト一覧画面（`contacts:contact_list`）に「インポート」ボタンを配置する（既存の「コンタクト新規作成」ボタンと並べる、`app-action-area`内）
2. アップロード画面：CSVファイルを選択して送信
3. プレビュー画面：パース結果を件数（**成功見込み件数**／**スキップ見込み件数**）とともに表示し、確定ボタンで実行
4. 完了結果画面：成功件数・スキップ件数・エラー理由（行番号付き）を表示（PRGパターン、HIG 5.1）

**必須列チェックの共有関数化**：「`last_name`・`first_name`がともに空の行はスキップする」という判定（第4.8節）は、プレビュー画面の件数集計（成功見込み件数／スキップ見込み件数）と、実際の確定処理の両方で使われる。判定ロジックを`is_row_skippable(row) -> bool`のような小さい共有関数として`contacts/services/csv_import.py`に用意し、プレビュー・確定処理の両方から同じ関数を呼ぶこと。判定ロジックを別々に実装すると、「プレビューでは成功見込みと表示されたのに確定したらスキップされた」という食い違いが起きるため、必ず1箇所に集約する。

**「スキップ見込み件数」の定義**：`is_row_skippable(row)`が`True`を返す行数のみを指す（第4.8節の必須列チェックと同義）。それ以外の独立した「要確認」区分は設けない（往復可能性・正規化基盤への一本化というシンプル設計の柱を優先する）。

**View構成は3分割とする**：アップロードView・プレビュー／確定View・完了結果Viewの3つに分ける。完了結果画面への結果データ（成功件数・スキップ件数・エラー一覧）の引き渡しは、`request.session['import_result'] = result`にセットしてリダイレクトし、完了結果View側で`request.session.pop('import_result')`により取り出して表示する（他のPRGパターン同様、一度きりの表示でセッションから消す）。

**プレビュー画面のテーブル表示件数**：CSVが大量件数（1,000件超等）の場合にブラウザ・サーバーの負荷を避けるため、テーブルへの行表示は**先頭10件まで**のサンプル表示とする。全体のサマリー件数（合計件数・成功見込み件数・スキップ見込み件数）はテーブルとは別に、省略なく表示する。

**エラー行番号の基準**：完了結果画面で示す行番号は、CSVファイルのヘッダー行を1行目として数え、データは2行目からカウントする（`for line_no, row in enumerate(reader, start=2):`）。ユーザーがExcel等でCSVを開いた際の行番号と一致させ、該当行を特定しやすくするため。

**アップロード〜プレビュー間のCSVデータ一時保持方式**：アップロードされたファイルの生テキストを`request.session`にそのまま格納しない（Cookie／DBセッション容量の肥大化・シリアライズエラーの原因になる）。`tempfile`または`default_storage`（例：`tmp/csv_import_<uuid>.csv`）に一時保存し、セッションにはそのトークン／ファイルパスのみを保持する。

**一時ファイルの掃除（優先度を明確化）**：
- **必須**：確定処理の成功時・キャンセル時のいずれも、その場で該当一時ファイルを即座に削除する（`os.remove`または`storage.delete`）。これにより通常のフローでは一時ファイルが残らない
- **任意（余力があれば対応、なければ別タスク）**：プレビュー画面まで進まず離脱した場合等、後始末されずに残った一時ファイルを定期的に掃除する簡易cronまたは管理コマンド（保持期間の目安：24時間）。本フェーズの実装スコープとしては必須としない

**プレビュー確定時の二重送信対策**：確定ボタンの二重クリックやブラウザの「戻る」→再送信により、同じCSVから重複してPerson・Contactが作られる事故を防ぐため、確定処理が成功した時点でセッションのトークンを無効化する（同じトークンでの再確定はエラーとする）。

## 4.2 入力CSVフォーマット

- 文字コード：UTF-8 with BOM対応で読み込む
- ヘッダー行必須。列名は日本語（第3章の「カラム」列）または英語コーディング名（Contactフィールド名）のいずれかを許容し、ヘッダー行から自動判別する
- 必須列：**姓（`last_name`）・名（`first_name`）**
- 任意列：第3章表の「インポートで受け付ける」列すべて（`full_name`・`other_name_parts`・`lang`・`organization`・`department`・`title`・`branch`・`qualification`・`catchphrase`・`postal_code`・`country`・`region`・`city`・`rest_of_address`・`email`・`mobile_phone`・`personal_phone`・`personal_fax`・`org_phone`・`org_fax`・`website`・`notes`）
- CSVに存在しない列はスキップしてよい（全列を埋める必要はない）
- `name_order`・`salutation_name`・`address`・Person ID・退会状態の列が含まれていても**無視する**（第3.4節の通り、エクスポートしたCSVをそのまま再投入できるようにするため）

【補足】エクスポートしたCSVを再インポートする場合、`last_name`／`first_name`が空の行（インポート必須ルール制定前のOCR経由データ等）は必須チェックに引っかかりスキップされる。これは既存データの実態を反映した正しい挙動であり、インポート機能側の不具合ではない。

## 4.3 正規化処理

CSVインポートは`ContactBaseForm`を経由しないため、`contacts/services/normalization.py`の`normalize_field(field_name, value, contact)`を各フィールドに対して明示的に呼び出す。

**`country`は汎用ループの対象から除外し、先に個別解決する**（第4.5節）。理由：`normalize_field()`は`postal_code`・`rest_of_address`の正規化に`contact.country`を参照するため、`country`が確定する前に他フィールドを正規化すると不整合が起きる。また、CSVの`country`列が空白のみ（スペース等）の場合、汎用ループの「値があれば正規化する」という判定（空白はtruthy）を通ってしまうと、正規化後に空文字になった状態のまま`DEFAULT_CONTACT_COUNTRY`による補完が効かずに保存される事故になる。`country`は空白のみも「未入力」とみなして第4.5節の手順で解決すること。

```python
from contacts.services.normalization import normalize_field
from django.core.exceptions import ValidationError

contact_dict = {}

# country は先に個別解決（第4.5節）。空白のみも未入力扱いにする。
country_raw = (row.get("country") or "").strip()
contact_dict["country"] = country_raw or getattr(settings, "DEFAULT_CONTACT_COUNTRY", "JP")

# normalize_field() は postal_code・rest_of_address の正規化時に
# getattr(contact, "country", "") で country を読み取る（duck typing）。
# 確定した country を反映した軽量インスタンスを作り、これを contact= 引数に渡す。
partial_contact = Contact(country=contact_dict["country"])

# 残りのフィールドを汎用ループで正規化する（full_name のみ ValidationError を送出しうる）。
for field_name in IMPORTABLE_FIELDS:  # country・full_name は含まない
    raw_value = row.get(field_name, "")
    if raw_value:
        contact_dict[field_name] = normalize_field(field_name, raw_value, contact=partial_contact)

# full_name はCSV入力があるときだけ設定する。ValidationError（スペースのみ等）が出た場合は
# キー自体を設定せず、Contact.save() の自動組み立てに委ねる（第4.4節）。
full_name_raw = row.get("full_name", "")
if full_name_raw:
    try:
        contact_dict["full_name"] = normalize_field("full_name", full_name_raw, contact=partial_contact)
    except ValidationError:
        pass  # キーを設定しない。スキップしない。
```

## 4.4 氏名系の自動生成ロジック

**前提（v1.1で追記）**：`Contact.save()`はv1.7で`full_name`・`display_name`の自動組み立てを既に実装済みである（`salutation_name`と同じスナップショット方式、`_FULL_NAME_SOURCE_FIELDS = ("last_name", "first_name", "other_name_parts", "name_order")`）。CSVインポートサービスはこの既存ロジックと二重管理にならないよう、**`full_name`の組み立てを自前で行わない**。

- **`full_name`**：CSVに入力があれば`normalize_full_name()`で正規化して`contact_dict['full_name']`に設定する。**CSVに入力がなければ、`contact_dict`に`full_name`キー自体を含めない**（空文字を明示的に渡さない）。`full_name_is_manual`は`False`のまま`Contact.objects.create()`に渡す。これにより、`Contact.save()`側の`if not self.full_name_is_manual and (not self.full_name or ...)`の条件に合致し、`last_name`／`first_name`／`other_name_parts`／`name_order`から自動的に`compute_full_name()`が呼ばれ組み立てられる。**サービス層で`compute_full_name()`を呼び出す実装は行わないこと**（正規化基盤の二重管理を避けるため）
- **`display_name`**：CSV列としては受け取らない。`display_name_is_manual=False`のまま渡せば、`Contact.save()`が`full_name`と同値に自動設定する。CSVインポート側で組み立てる処理は不要
- **`name_order`**：CSV列としては受け取らないが、**`lang`から導出した値を`contact_dict['name_order']`として明示的に設定し、DBフィールドとして永続化する**（`full_name`組み立てのための一時変数に留めない。後日そのContactを編集する際にも`Contact.save()`が`name_order`を参照し続けるため）。

**導出ルールは既存の`contacts/forms.py` `ContactBaseForm._setup_effective_lang()`（v1.7、稼働中）と完全に一致させる**（`en`で始まるかどうかの二分法。当初「`ja`／`ko`／`zh`列挙」で検討していたが、既存Form経路と`und`等の扱いが食い違う結果になることが判明したため、既存の稼働中ロジックに合わせる）：

| `lang`（前方一致） | 自動設定される`name_order` |
|---|---|
| `en`で始まる | `first_last` |
| 上記以外すべて（`ja`／`ko`／`zh`／`und`／未入力等） | `last_first` |

これにより、同じ`lang`値が手動作成フォーム経由でもCSVインポート経由でも同じ`name_order`になり、`normalization.py`冒頭の設計原則（3経路で同じ値を保証する）と整合する。

- **`salutation_name`**：CSV列としては受け取らない。Person・Contact保存後、`Contact.save()`の既存オーバーライド処理により`compute_salutation_name(contact)`が自動的に呼ばれ、`salutation_name_is_manual=False`のまま生成される（既存のOCR経路・手動作成経路と同じ仕組みをそのまま利用する。インポート専用のロジックを新設する必要はない）
- **`other_name_parts`**：CSVに入力があれば正規化なしでそのまま保存する（対応する正規化関数なし、カテゴリA扱い）
- 整合性チェック（`check_name_consistency()`相当）は**行わない**。`last_name`／`first_name`と`full_name`の内容が食い違っていても、`full_name`優先で採用しスキップしない（CSVインポートは人間が入力した既存データの移行が前提であり、OCRの読み取り誤差補正とは性質が異なるため）

## 4.5 `country`未入力時のデフォルト補完

- CSVの`country`列が空（空白のみを含む）の場合、`getattr(settings, "DEFAULT_CONTACT_COUNTRY", "JP")`で補完する。この解決は第4.3節の汎用正規化ループより**先に**行う
- `DEFAULT_CONTACT_COUNTRY`は本仕様書で新設する設定値（第5章参照）
- `country`を確定させてから、`postal_code`・`rest_of_address`の国別正規化（`normalize_postal_code_by_country()`・`normalize_rest_of_address_by_country()`）を行う

## 4.6 Person・Contact生成

新規サービス関数（例：`contacts/services/csv_import.py`の`create_person_and_contact_from_row(row_dict, user)`）を新設する。核の流れは既存`_create_person_and_contact()`と同じにする。

```python
def build_contact_dict(row):
    """CSV1行分の生データから contact_dict を組み立てる（第4.3節のコード例本体）。
    country解決 → 汎用正規化ループ → full_name処理、の順で行う。
    partial_contact（Contact()の軽量インスタンス）は本関数内で country 確定後に
    生成する。呼び出し側では生成しない（呼び出し側が用意しても本関数内で
    シャドーイングされ一度も使われないため、混乱を避けるためこの形に統一する）。"""
    ...  # 第4.3節のコード例と同一処理

def create_person_and_contact_from_row(contact_dict, user):
    """CSVインポート1行からPerson・Contactを新規作成する。

    既存の contacts/views.py _create_person_and_contact() と同じ核の流れ
    （Person作成 → Contact作成 → set_primary_contact）を踏襲する。

    contact_dict には CSV に入力があったフィールドのみを含める（full_name は
    入力がなければキー自体を含めない。Contact.save() の v1.7 自動組み立て
    ロジックに委ねるため、ここで compute_full_name() を呼ばない）。
    name_order は lang から導出済みの値を必ず含める（DB永続化のため）。
    """
    person = Person.objects.create(status=Person.Status.ACTIVE)
    contact = Contact.objects.create(
        person=person,
        status=Contact.Status.PRIMARY,
        created_by=user,
        updated_by=user,
        duplicate_checked_at=None,
        **contact_dict,  # full_name未入力時はキー自体が含まれない想定
    )
    person.set_primary_contact(contact)
    return contact
```

**1行1トランザクションの徹底**：上記処理は必ず`transaction.atomic()`で囲むこと。Person作成後にContact作成が失敗すると、連絡先のない孤立Personが残る事故になる。捕捉対象は`IntegrityError`を主とする（`Contact.objects.create()`はデフォルトで`full_clean()`を呼ばないため、通常`ValidationError`はここまで伝播しない。`full_name`の`ValidationError`は第4.3節のコード例内で既に握りつぶし済みのため、ここでの捕捉は保険的な位置づけ）。

**`atomic()`ブロック内でエラー記録のDB書き込みをしない**：`except`節の中でDBクエリ（エラーログの保存等）を実行すると、`needs_rollback`状態のまま`atomic()`ブロックを抜けようとして`TransactionManagementError`になる。エラー理由はPythonのリスト等メモリ上に貯めるだけにとどめ、`atomic()`ブロックを完全に抜けた後（forループの次の反復に進む前、または全行処理が終わった後）にまとめて完了結果画面に渡す。

```python
from django.db import transaction, IntegrityError

errors = []  # atomic()ブロックの外で保持する
for line_no, row in enumerate(reader, start=2):  # ヘッダーが1行目、データは2行目から
    try:
        with transaction.atomic():
            partial_contact_dict = build_contact_dict(row)
            create_person_and_contact_from_row(partial_contact_dict, user)
    except IntegrityError as exc:
        # atomic()ブロックを抜けた後にメモリ上へ記録する（ブロック内でDBに触れない）
        errors.append({"line_no": line_no, "reason": str(exc)})
```

Company紐付け（`link_contact_to_company()`）は呼ばない（第1.4節、未実装のため）。`Contact.organization`は文字列としてそのまま保存する。

## 4.7 重複検出キュー連携

作成したContactは`duplicate_checked_at=None`のまま保存する（上記コード例の通り）。既存の`check_duplicates`管理コマンド（cron起動、`Run_Generate_Duplicate_Candidates`）が次回実行時に自動的に検査対象へ含める。インポート専用の重複検出処理は新設しない。

## 4.8 エラーハンドリング

行番号は第4.1節の基準（ヘッダーを1行目、データは2行目からカウント）に従う。

| 状況 | 扱い |
|---|---|
| `last_name`・`first_name`がともに空 | `is_row_skippable(row)`（第4.1節）が`True`を返す。当該行をスキップ。エラー理由「姓・名が未入力」として行番号とともに記録 |
| `full_name`列のバリデーション失敗（`normalize_field()`呼び出し時に`ValidationError`を捕捉：スペースのみ等） | **当該行をスキップしない**。`contact_dict`に`full_name`キーを設定せず、`Contact.save()`の自動組み立て（`compute_full_name()`、第4.4節）に委ねる。サービス層で独自にフォールバック処理を書かないこと |
| その他の正規化関数が例外を送出しない限り | 値をそのまま保存する（正規化関数は基本的に例外を投げない設計、`normalize_full_name`のみ例外） |
| 1行の処理失敗（DB制約違反等、予期しないエラー） | 当該行のみスキップし処理を継続する。他の行の処理は止めない（1行=1トランザクションとする、第4.6節） |

完了結果画面では、成功件数・スキップ件数・エラー理由を行番号付きで一覧表示する（HIG 5.1「一部失敗した」状態表示）。

---

# 第5章 `DEFAULT_CONTACT_COUNTRY`設定の新設

## 5.1 `.env`／`settings.py`への追加

既存の`.env`→`settings.py`配線パターン（`os.getenv(key, default)`）に倣う。

```python
# config/settings.py（追加箇所）
DEFAULT_CONTACT_COUNTRY = os.getenv("DEFAULT_CONTACT_COUNTRY", "JP")
```

`.env.example`にも追記する。

```
# コンタクトのデフォルト国コード（ISO 3166-1 alpha-2）。CSVインポートの country 未入力時、
# および手動作成フォームの country 初期値に使用。
DEFAULT_CONTACT_COUNTRY=JP
```

DBマイグレーションは発生しない（`Contact.country`フィールド自体の`default`は変更せず、空文字のまま維持する。第5.2節の2箇所とCSVインポート処理でのみ本設定値を参照する）。

## 5.2 既存箇所の置き換え

`contacts/forms.py`の以下2箇所のハードコードを置き換える。

| ファイル・箇所 | 変更前 | 変更後 |
|---|---|---|
| `ContactCreateForm.__init__`（558行目付近） | `self.fields["country"].initial = "JP"` | `self.fields["country"].initial = getattr(settings, "DEFAULT_CONTACT_COUNTRY", "JP")` |
| `ContactAddAdditionalRoleForm.__init__`（597行目付近） | `self.fields["country"].initial = "JP"` | `self.fields["country"].initial = getattr(settings, "DEFAULT_CONTACT_COUNTRY", "JP")` |

`contacts/forms.py`冒頭に`from django.conf import settings`のimportが必要（未importの場合追加）。

---

# 第6章 実装ステップとファイル一覧

## 6.1 段階的な実装順序

1. `Contact.Meta.permissions`に`export_contact`／`import_contact`を追加し、`contacts`アプリのマイグレーションを作成
2. 上記マイグレーションの実番号を確認し、`accounts/migrations/0015_grant_export_import_contact.py`を作成（第2.3節）
3. `config/settings.py`・`.env.example`に`DEFAULT_CONTACT_COUNTRY`を追加し、`contacts/forms.py`の2箇所を置き換え（第5章）
4. `contacts/services/csv_import.py`を新設し、`create_person_and_contact_from_row()`・CSVパース・正規化呼び出し・エラーハンドリングを実装（第4章）
5. コンタクトCSVインポート用View・テンプレート（アップロード／プレビュー／完了結果の3画面）を実装
6. メーリングリストCSVエクスポート用View・テンプレート（既存`mailing_list_detail.html`へのボタン追加含む）を実装（第3章）
7. 動作確認（開発DBは削除可、実データでの往復テスト＝エクスポート→インポートを含む）

## 6.2 新規／変更ファイル一覧

| ファイル | 種別 | 内容 |
|---|---|---|
| `contacts/models.py` | 変更 | `Contact.Meta.permissions`に`export_contact`／`import_contact`追加 |
| `contacts/migrations/00XX_add_export_import_permissions.py` | 新規 | 上記Permission追加のマイグレーション |
| `accounts/migrations/0015_grant_export_import_contact.py` | 新規 | Group配分（第2.3節） |
| `config/settings.py` | 変更 | `DEFAULT_CONTACT_COUNTRY`追加 |
| `.env.example` | 変更 | `DEFAULT_CONTACT_COUNTRY`追加 |
| `contacts/forms.py` | 変更 | `ContactCreateForm`・`ContactAddAdditionalRoleForm`の`country`初期値を設定値参照に変更 |
| `contacts/services/csv_import.py` | 新規 | CSVパース・正規化・Person/Contact生成サービス関数 |
| `contacts/views.py` | 変更 | インポート用View（アップロード／プレビュー／完了結果）追加 |
| `contacts/urls.py` | 変更 | インポート用URL追加 |
| `templates/contacts/contact_list.html` | 変更 | 「インポート」ボタン追加 |
| `templates/contacts/` 配下 | 新規 | インポート用テンプレート（アップロード／プレビュー／完了結果） |
| `mailings/views.py` | 変更 | `MailingListDetailView`にエクスポート機能追加、または専用View新設 |
| `mailings/urls.py` | 変更 | エクスポート用URL追加 |
| `mailings/templates/mailings/mailing_list_detail.html` | 変更 | エクスポートボタン・カラム選択UI追加 |

---

# 巻末別表：日本語⇔コーディング名対照表

| 日本語での呼び名 | コーディング名 | 参照 |
|---|---|---|
| コンタクトのエクスポート権限 | `contacts.export_contact` | §2.1 |
| コンタクトのインポート権限 | `contacts.import_contact` | §2.1 |
| フィールド別正規化ディスパッチャ | `normalize_field()` | §4.3 |
| 氏名組み立て | `compute_full_name()` | §4.4 |
| 宛名組み立て | `compute_salutation_name()` | §4.4 |
| 住所組み立て | `compose_full_address()` | §3.2 |
| 郵便番号の国別正規化 | `normalize_postal_code_by_country()` | §4.3 |
| 残り住所の国別正規化 | `normalize_rest_of_address_by_country()` | §4.3 |
| 生存Personの解決 | `person.get_surviving_person()` | §3.5 |
| 主コンタクトの切り替え | `person.set_primary_contact()` | §4.6 |
| インポート専用のPerson/Contact生成 | `create_person_and_contact_from_row()`（新設） | §4.6 |
| デフォルト国コード設定 | `DEFAULT_CONTACT_COUNTRY` | §5.1 |
| 重複検出バッチ | `Run_Generate_Duplicate_Candidates` | §4.7 |
