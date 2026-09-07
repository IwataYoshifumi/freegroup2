"""コンタクト CSV インポート機能のテストスイート（仕様書 v1.6 §4）。"""

import io
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from contacts.models import Contact
from contacts.services.csv_import import (
    build_contact_dict,
    delete_temp_import_file,
    execute_csv_import,
    is_row_skippable,
    parse_csv_for_preview,
    read_temp_import_file,
    save_temp_import_file,
)
from persons.models import Person

User = get_user_model()


class BaseImportTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="import_tester",
            password="testpassword",
            email="import_tester@example.com",
        )
        self.import_perm = Permission.objects.get(
            content_type__app_label="contacts", codename="import_contact"
        )
        self.view_contact_perm = Permission.objects.get(
            content_type__app_label="contacts", codename="view_contact"
        )
        self.user.user_permissions.add(self.import_perm, self.view_contact_perm)

        self.client = Client()
        self.client.force_login(self.user)

    def _make_csv_file(self, content_str, filename="test_contacts.csv"):
        # UTF-8 with BOM
        bom_content = "\ufeff" + content_str
        return SimpleUploadedFile(
            filename, bom_content.encode("utf-8"), content_type="text/csv"
        )


class ContactCSVImportPermissionTests(BaseImportTestCase):
    """認可テスト（仕様書 §2.1, §4.1: contacts.import_contact）。"""

    def test_anonymous_redirects_to_login(self):
        client = Client()
        resp = client.get(reverse("contacts:contact_import_upload"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_user_without_import_permission_forbidden(self):
        unprivileged = User.objects.create_user(
            username="no_import_user", password="x"
        )
        unprivileged.user_permissions.add(self.view_contact_perm)
        client = Client()
        client.force_login(unprivileged)

        # upload
        self.assertEqual(
            client.get(reverse("contacts:contact_import_upload")).status_code, 403
        )
        self.assertEqual(
            client.post(reverse("contacts:contact_import_upload")).status_code, 403
        )
        # preview
        self.assertEqual(
            client.get(reverse("contacts:contact_import_preview")).status_code, 403
        )
        self.assertEqual(
            client.post(reverse("contacts:contact_import_preview")).status_code, 403
        )
        # done
        self.assertEqual(
            client.get(reverse("contacts:contact_import_done")).status_code, 403
        )

    def test_user_with_import_permission_allowed(self):
        resp = self.client.get(reverse("contacts:contact_import_upload"))
        self.assertEqual(resp.status_code, 200)


class ContactCSVImportUploadTests(BaseImportTestCase):
    """アップロード画面・一時ファイル保持のテスト。"""

    def test_upload_without_file_shows_error(self):
        url = reverse("contacts:contact_import_upload")
        resp = self.client.post(url, {})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CSVファイルを選択してください")

    def test_upload_invalid_extension_shows_error(self):
        url = reverse("contacts:contact_import_upload")
        file = SimpleUploadedFile("test.txt", b"dummy content", content_type="text/plain")
        resp = self.client.post(url, {"csv_file": file})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "拡張子が .csv のファイルを選択してください")

    def test_valid_upload_saves_temp_and_redirects_to_preview(self):
        url = reverse("contacts:contact_import_upload")
        csv_data = "姓,名,会社名\n田中,太郎,テスト株式会社\n"
        uploaded_file = self._make_csv_file(csv_data)

        resp = self.client.post(url, {"csv_file": uploaded_file})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("contacts:contact_import_preview"))

        # セッションにトークンとパスが保存されていること
        token = self.client.session.get("contact_import_token")
        storage_path = self.client.session.get("contact_import_storage_path")
        self.assertIsNotNone(token)
        self.assertIsNotNone(storage_path)

        # 一時ファイルが存在すること
        self.assertTrue(default_storage.exists(storage_path))

        # 後始末
        delete_temp_import_file(storage_path)


class ContactCSVImportPreviewTests(BaseImportTestCase):
    """プレビュー画面・共通スキップ判定のテスト。"""

    def test_missing_required_headers_rejects(self):
        csv_data = "氏名,会社名\n田中太郎,テスト株式会社\n"  # 姓・名がない
        file = self._make_csv_file(csv_data)
        self.client.post(reverse("contacts:contact_import_upload"), {"csv_file": file})

        resp = self.client.get(reverse("contacts:contact_import_preview"), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "必須列が不足しています")

    def test_preview_counts_and_samples(self):
        # 3行: 1行目正常, 2行目姓のみ(正常扱い), 3行目姓名なし(スキップ扱い)
        csv_data = (
            "姓,名,会社名,メール\n"
            "山田,太郎,山田商事,yamada@example.com\n"
            "鈴木,,鈴木工業,suzuki@example.com\n"
            ",,名無商事,noname@example.com\n"
        )
        file = self._make_csv_file(csv_data)
        self.client.post(reverse("contacts:contact_import_upload"), {"csv_file": file})

        resp = self.client.get(reverse("contacts:contact_import_preview"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "contacts/contact_import_preview.html")

        # サマリー件数の確認
        preview = resp.context["preview"]
        self.assertEqual(preview["total_count"], 3)
        self.assertEqual(preview["success_count"], 2)
        self.assertEqual(preview["skippable_count"], 1)

        # HTML内での件数表示
        self.assertContains(resp, "合計データ行数")
        self.assertContains(resp, "登録見込み件数")
        self.assertContains(resp, "スキップ見込み件数")

    def test_preview_rows_and_pagination_structure(self):
        csv_data = (
            "姓,名,会社名,メール\n"
            "山田,太郎,山田商事,yamada@example.com\n"
            "鈴木,,鈴木工業,suzuki@example.com\n"
            ",,名無商事,noname@example.com\n"
        )
        file = self._make_csv_file(csv_data)
        self.client.post(reverse("contacts:contact_import_upload"), {"csv_file": file})

        resp = self.client.get(reverse("contacts:contact_import_preview"))
        self.assertEqual(resp.status_code, 200)

        # context 内の全パース行データ検証
        self.assertIn("preview_rows", resp.context)
        rows = resp.context["preview_rows"]
        self.assertEqual(len(rows), 3)

        # 1行目: valid
        self.assertEqual(rows[0]["row_num"], 2)
        self.assertEqual(rows[0]["status"], "valid")
        self.assertEqual(rows[0]["last_name"], "山田")
        self.assertEqual(rows[0]["first_name"], "太郎")
        self.assertEqual(rows[0]["skip_reason"], "")

        # 3行目: skip
        self.assertEqual(rows[2]["row_num"], 4)
        self.assertEqual(rows[2]["status"], "skip")
        self.assertEqual(rows[2]["skip_reason"], "姓および名が未入力")

        # HTML内要素検証
        self.assertContains(resp, 'data-filter="all"')
        self.assertContains(resp, 'data-filter="valid"')
        self.assertContains(resp, 'data-filter="skip"')
        self.assertContains(resp, 'id="import-preview-data"')
        self.assertContains(resp, 'id="pagination-info"')
        self.assertContains(resp, 'id="pagination-nav"')
        self.assertContains(resp, 'スキップ見込み')
        self.assertContains(resp, '姓および名が未入力')
        # ローディングスピナー要素の検証
        self.assertContains(resp, 'id="import-loading-overlay"')
        self.assertContains(resp, "インポート処理中...")
        self.assertContains(resp, "データの登録を行っています。")
        self.assertContains(resp, "完了まで画面を閉じずにお待ちください。")


class ContactCSVImportNormalizationTests(BaseImportTestCase):
    """正規化パイプラインおよびフィールド自動補完のテスト。"""

    def test_is_row_skippable(self):
        self.assertTrue(is_row_skippable({"last_name": "", "first_name": ""}))
        self.assertTrue(is_row_skippable({"last_name": "  ", "first_name": "\u3000"}))
        self.assertFalse(is_row_skippable({"last_name": "佐藤", "first_name": ""}))
        self.assertFalse(is_row_skippable({"last_name": "", "first_name": "花子"}))
        self.assertFalse(is_row_skippable({"last_name": "佐藤", "first_name": "花子"}))

    def test_country_default_fallback_and_custom(self):
        # country未入力 -> DEFAULT_CONTACT_COUNTRY (JP)
        row_default = {"last_name": "佐藤", "first_name": "次郎", "country": " "}
        c_dict1 = build_contact_dict(row_default)
        self.assertEqual(c_dict1["country"], "JP")

        # country指定時 -> そのまま正規化へ
        row_us = {"last_name": "Smith", "first_name": "John", "country": "US", "lang": "en"}
        c_dict2 = build_contact_dict(row_us)
        self.assertEqual(c_dict2["country"], "US")
        self.assertEqual(c_dict2["name_order"], Contact.NameOrder.FIRST_LAST)

    def test_name_order_derivation(self):
        # ja, ko, zh, und, 未入力 -> last_first
        self.assertEqual(build_contact_dict({"lang": "ja"})["name_order"], Contact.NameOrder.LAST_FIRST)
        self.assertEqual(build_contact_dict({"lang": "ko"})["name_order"], Contact.NameOrder.LAST_FIRST)
        self.assertEqual(build_contact_dict({"lang": "und"})["name_order"], Contact.NameOrder.LAST_FIRST)
        self.assertEqual(build_contact_dict({"lang": ""})["name_order"], Contact.NameOrder.LAST_FIRST)
        # en, en-US -> first_last
        self.assertEqual(build_contact_dict({"lang": "en"})["name_order"], Contact.NameOrder.FIRST_LAST)
        self.assertEqual(build_contact_dict({"lang": "en-US"})["name_order"], Contact.NameOrder.FIRST_LAST)


class ContactCSVImportExecutionTests(BaseImportTestCase):
    """インポート確定処理・Person/Contact生成・二重送信防止のテスト。"""

    def test_import_execution_creates_person_and_contact(self):
        # 往復可能性: 参考出力列（氏名の並び順・宛名・住所・Person ID・退会状態）を含むCSV
        csv_data = (
            "Person ID,姓,名,氏名,氏名の並び順,宛名,会社名,部署,役職,郵便番号,住所（完成形）,メール,退会状態\n"
            "00000000-0000-0000-0000-000000000001,高橋,一郎,高橋 一郎,last_first,高橋 一郎 様,高橋商事,営業部,課長,100-0001,東京都千代田区1-1,takahashi@example.com,退会済み\n"
            ",伊藤,二郎,,last_first,,伊藤工務店,,,1000002,,ito@example.com,\n"
            ",,,スキップ行,,,,,,,,,,\n"
        )
        file = self._make_csv_file(csv_data)
        self.client.post(reverse("contacts:contact_import_upload"), {"csv_file": file})

        token = self.client.session.get("contact_import_token")
        storage_path = self.client.session.get("contact_import_storage_path")

        # 確定実行 POST
        url = reverse("contacts:contact_import_preview")
        resp = self.client.post(url, {"action": "confirm", "token": token})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("contacts:contact_import_done"))

        # 一時ファイルが削除されていること（仕様書 §4.1）
        self.assertFalse(default_storage.exists(storage_path))

        # DB の確認
        # 1人目: 高橋 一郎
        c1 = Contact.objects.filter(email="takahashi@example.com").first()
        self.assertIsNotNone(c1)
        self.assertEqual(c1.last_name, "高橋")
        self.assertEqual(c1.first_name, "一郎")
        self.assertEqual(c1.full_name, "高橋一郎")  # normalize_full_name でスペース除去
        self.assertEqual(c1.status, Contact.Status.PRIMARY)
        self.assertIsNone(c1.duplicate_checked_at)
        self.assertIsNotNone(c1.person)
        self.assertEqual(c1.person.primary_contact, c1)
        # salutation_name が Contact.save() で自動生成されていること
        self.assertEqual(c1.salutation_name, "高橋 様")

        # 2人目: 伊藤 二郎 (full_name未入力 -> Contact.save() で自動組み立て)
        c2 = Contact.objects.filter(email="ito@example.com").first()
        self.assertIsNotNone(c2)
        self.assertEqual(c2.full_name, "伊藤 二郎")
        self.assertEqual(c2.salutation_name, "伊藤 様")

        # 結果画面の表示確認（PRG）
        resp_done = self.client.get(reverse("contacts:contact_import_done"))
        self.assertEqual(resp_done.status_code, 200)
        self.assertContains(resp_done, "一部の行をスキップしてインポートを完了しました")
        self.assertContains(resp_done, "登録成功件数")
        self.assertContains(resp_done, "2")
        self.assertContains(resp_done, "スキップ件数")
        self.assertContains(resp_done, "1")
        self.assertContains(resp_done, "4 行目")  # 3件目のスキップ行 (ヘッダー1 + データ3 = 4行目)

        # ActionLog の記録確認
        from actionlogs.models import ActionLog
        action_log = ActionLog.objects.filter(action="contact_import").last()
        self.assertIsNotNone(action_log)
        self.assertEqual(action_log.user, self.user)
        self.assertIn("CSVインポート完了: 成功 2件, スキップ 1件", action_log.note)
        self.assertEqual(action_log.data["success_count"], 2)
        self.assertEqual(action_log.data["skipped_count"], 1)

        # 2回目の結果画面アクセスはセッション破棄済みのため一覧へリダイレクト
        resp_done_repeat = self.client.get(reverse("contacts:contact_import_done"))
        self.assertEqual(resp_done_repeat.status_code, 302)
        self.assertEqual(resp_done_repeat.url, reverse("contacts:contact_list"))

    def test_double_submission_prevented(self):
        csv_data = "姓,名\n渡辺,健\n"
        file = self._make_csv_file(csv_data)
        self.client.post(reverse("contacts:contact_import_upload"), {"csv_file": file})

        token = self.client.session.get("contact_import_token")

        # 1回目の確定
        self.client.post(reverse("contacts:contact_import_preview"), {"action": "confirm", "token": token})

        # 2回目の確定（ブラウザ戻る再送信など）
        resp = self.client.post(reverse("contacts:contact_import_preview"), {"action": "confirm", "token": token})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("contacts:contact_list"))

        # 重複して作成されていないこと
        self.assertEqual(Contact.objects.filter(last_name="渡辺", first_name="健").count(), 1)

    def test_cancel_removes_temp_file_and_redirects(self):
        csv_data = "姓,名\n加藤,豪\n"
        file = self._make_csv_file(csv_data)
        self.client.post(reverse("contacts:contact_import_upload"), {"csv_file": file})

        storage_path = self.client.session.get("contact_import_storage_path")
        self.assertTrue(default_storage.exists(storage_path))

        # キャンセル POST
        resp = self.client.post(reverse("contacts:contact_import_preview"), {"action": "cancel"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("contacts:contact_list"))

        # 一時ファイルが削除されていること
        self.assertFalse(default_storage.exists(storage_path))


class ContactListImportButtonTests(BaseImportTestCase):
    """コンタクト一覧画面のインポートボタン表示制御テスト。"""

    def test_import_button_shown_for_privileged_user(self):
        resp = self.client.get(reverse("contacts:contact_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "インポート")
        self.assertContains(resp, reverse("contacts:contact_import_upload"))

    def test_import_button_hidden_for_unprivileged_user(self):
        unprivileged = User.objects.create_user(
            username="viewer_only", password="x"
        )
        unprivileged.user_permissions.add(self.view_contact_perm)
        client = Client()
        client.force_login(unprivileged)

        resp = client.get(reverse("contacts:contact_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("contacts:contact_import_upload"))


class ContactCSVExportImportRoundTripTests(BaseImportTestCase):
    """仕様書 v1.6 コア原則：メーリングリストエクスポートCSVの直接インポート（往復可能性）検証。"""

    def test_round_trip_export_to_import(self):
        from mailings.models import MailingList, MailingListMember
        from mailings.services.csv_export import (
            EXPORT_AVAILABLE_KEYS,
            generate_mailing_list_csv,
        )

        # 1. エクスポート元のメーリングリストおよびメンバーデータ作成
        mailing_list = MailingList.objects.create(
            name="往復テスト用リスト",
            created_by=self.user,
        )

        # メンバー1: full_name あり、退会なし
        p1 = Person.objects.create(status=Person.Status.ACTIVE)
        c1 = Contact.objects.create(
            person=p1,
            status=Contact.Status.PRIMARY,
            last_name="中村",
            first_name="剛",
            full_name="中村 剛",
            organization="中村商事",
            department="総務部",
            title="専務",
            postal_code="1500001",
            country="JP",
            region="東京都",
            city="渋谷区",
            rest_of_address="神宮前1-2",
            email="nakamura@example.com",
            created_by=self.user,
            updated_by=self.user,
        )
        p1.set_primary_contact(c1)
        MailingListMember.objects.create(
            mailing_list=mailing_list,
            person=p1,
            added_by=self.user,
        )

        # メンバー2: full_name 未入力、退会済み (is_unsubscribed=True)
        p2 = Person.objects.create(status=Person.Status.ACTIVE, is_unsubscribed=True)
        c2 = Contact.objects.create(
            person=p2,
            status=Contact.Status.PRIMARY,
            last_name="小林",
            first_name="幸子",
            full_name="",  # 空文字で保存し Contact.save() で自動組み立て
            organization="小林エンターテインメント",
            email="kobayashi@example.com",
            created_by=self.user,
            updated_by=self.user,
        )
        p2.set_primary_contact(c2)
        MailingListMember.objects.create(
            mailing_list=mailing_list,
            person=p2,
            added_by=self.user,
        )

        # 2. 全カラム選択でメーリングリスト CSV をエクスポート（BOM付き）
        exported_csv_text = generate_mailing_list_csv(mailing_list, EXPORT_AVAILABLE_KEYS)

        # エクスポート結果に参考出力列・管理列が含まれていることを確認
        self.assertIn("Person ID", exported_csv_text)
        self.assertIn("氏名の並び順", exported_csv_text)
        self.assertIn("宛名", exported_csv_text)
        self.assertIn("住所（完成形）", exported_csv_text)
        self.assertIn("退会状態", exported_csv_text)
        self.assertIn("退会済み", exported_csv_text)

        # 3. エクスポートされた CSV をそのままインポートアップロード画面に POST
        upload_file = SimpleUploadedFile(
            "exported_mailing_list.csv",
            exported_csv_text.encode("utf-8"),
            content_type="text/csv",
        )
        resp_upload = self.client.post(
            reverse("contacts:contact_import_upload"),
            {"csv_file": upload_file},
        )
        self.assertEqual(resp_upload.status_code, 302)
        self.assertEqual(resp_upload.url, reverse("contacts:contact_import_preview"))

        # 4. プレビュー画面で集計が正しいことを確認（全2件、スキップ0件）
        resp_preview = self.client.get(reverse("contacts:contact_import_preview"))
        self.assertEqual(resp_preview.status_code, 200)
        preview_data = resp_preview.context["preview"]
        self.assertEqual(preview_data["total_count"], 2)
        self.assertEqual(preview_data["success_count"], 2)
        self.assertEqual(preview_data["skippable_count"], 0)

        # 5. インポート確定実行
        token = self.client.session.get("contact_import_token")
        resp_confirm = self.client.post(
            reverse("contacts:contact_import_preview"),
            {"action": "confirm", "token": token},
        )
        self.assertEqual(resp_confirm.status_code, 302)
        self.assertEqual(resp_confirm.url, reverse("contacts:contact_import_done"))

        # 6. 新規生成された Person / Contact の検証
        # 既存 p1/p2 とは異なる新しい Person が生成されていること
        new_contacts = Contact.objects.filter(email__in=["nakamura@example.com", "kobayashi@example.com"]).exclude(id__in=[c1.id, c2.id])
        self.assertEqual(new_contacts.count(), 2)

        # 中村 剛
        new_c1 = new_contacts.get(email="nakamura@example.com")
        self.assertEqual(new_c1.last_name, "中村")
        self.assertEqual(new_c1.first_name, "剛")
        self.assertEqual(new_c1.full_name, "中村剛")  # normalize_full_name でスペース除去
        self.assertEqual(new_c1.organization, "中村商事")
        self.assertEqual(new_c1.department, "総務部")
        self.assertEqual(new_c1.title, "専務")
        self.assertEqual(new_c1.postal_code, "1500001")
        self.assertEqual(new_c1.status, Contact.Status.PRIMARY)
        self.assertIsNone(new_c1.duplicate_checked_at)
        self.assertNotEqual(new_c1.person_id, p1.id)  # 新規 Person
        self.assertEqual(new_c1.person.primary_contact, new_c1)
        self.assertEqual(new_c1.salutation_name, "中村 様")  # Contact.save() で自動生成

        # 小林 幸子 (full_name未入力 -> compute_full_name で自動組み立て)
        new_c2 = new_contacts.get(email="kobayashi@example.com")
        self.assertEqual(new_c2.last_name, "小林")
        self.assertEqual(new_c2.first_name, "幸子")
        self.assertEqual(new_c2.full_name, "小林 幸子")  # compute_full_name で組み立て
        self.assertEqual(new_c2.organization, "小林エンターテインメント")
        self.assertEqual(new_c2.status, Contact.Status.PRIMARY)
        self.assertIsNone(new_c2.duplicate_checked_at)
        self.assertNotEqual(new_c2.person_id, p2.id)  # 新規 Person
        self.assertEqual(new_c2.person.primary_contact, new_c2)
        self.assertEqual(new_c2.salutation_name, "小林 様")  # Contact.save() で自動生成

