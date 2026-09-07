"""メーリングリスト CSV エクスポート機能のテスト（仕様書 v1.6 §3）。"""

import csv
import io
import urllib.parse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contacts.models import Contact
from mailings.models import MailingList, MailingListMember
from mailings.services.csv_export import (
    DEFAULT_SELECTED_KEYS,
    EXPORT_AVAILABLE_KEYS,
    EXPORT_COLUMNS_DICT,
    generate_mailing_list_csv,
    make_export_disposition_header,
)
from persons.models import Person

User = get_user_model()


class BaseCSVExportTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="export_tester",
            password="testpassword",
            email="tester@example.com",
        )
        self.export_perm = Permission.objects.get(
            content_type__app_label="contacts", codename="export_contact"
        )
        self.view_list_perm = Permission.objects.get(
            content_type__app_label="mailings", codename="view_mailinglist"
        )
        self.user.user_permissions.add(self.export_perm, self.view_list_perm)

        self.client = Client()
        self.client.force_login(self.user)

        self.mailing_list = MailingList.objects.create(
            name="東京支社リスト",
            description="2026年度テスト用リスト",
            created_by=self.user,
        )

    def _create_member(self, person, is_unsubscribed=False):
        if is_unsubscribed:
            person.is_unsubscribed = True
            person.save()
        return MailingListMember.objects.create(
            mailing_list=self.mailing_list,
            person=person,
            added_by=self.user,
        )

    def _create_person_with_contact(self, **contact_kwargs):
        person = Person.objects.create(status=Person.Status.ACTIVE)
        defaults = {
            "person": person,
            "status": Contact.Status.PRIMARY,
            "last_name": "山田",
            "first_name": "太郎",
            "full_name": "山田 太郎",
            "organization": "株式会社テスト",
            "department": "開発部",
            "title": "部長",
            "postal_code": "1000001",
            "country": "JP",
            "region": "東京都",
            "city": "千代田区",
            "rest_of_address": "千代田1-1",
            "email": "yamada@example.com",
            "created_by": self.user,
            "updated_by": self.user,
        }
        defaults.update(contact_kwargs)
        contact = Contact.objects.create(**defaults)
        person.set_primary_contact(contact)
        return person, contact


class MailingListCSVExportPermissionTests(BaseCSVExportTestCase):
    """認可テスト（仕様書 §3.1: contacts.export_contact）。"""

    def test_anonymous_redirects_to_login(self):
        client = Client()
        form_url = reverse(
            "mailings:mailing_list_member_export_form",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp = client.get(form_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_user_without_export_contact_permission_denied(self):
        unprivileged = User.objects.create_user(
            username="unprivileged", password="x"
        )
        unprivileged.user_permissions.add(self.view_list_perm)
        client = Client()
        client.force_login(unprivileged)

        form_url = reverse(
            "mailings:mailing_list_member_export_form",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp_get = client.get(form_url)
        self.assertEqual(resp_get.status_code, 403)

        export_url = reverse(
            "mailings:mailing_list_member_export",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp_post = client.post(export_url, {"fields": ["full_name"]})
        self.assertEqual(resp_post.status_code, 403)

    def test_user_with_export_contact_permission_allowed(self):
        form_url = reverse(
            "mailings:mailing_list_member_export_form",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp = self.client.get(form_url)
        self.assertEqual(resp.status_code, 200)


class MailingListCSVExportFormViewTests(BaseCSVExportTestCase):
    """カラム選択画面（GET）のテスト。"""

    def test_form_view_renders_properly(self):
        url = reverse(
            "mailings:mailing_list_member_export_form",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "mailings/mailing_list_export_form.html")
        self.assertContains(resp, "CSV エクスポート：東京支社リスト")
        self.assertContains(resp, "インポート用の項目をまとめて選択")
        self.assertContains(resp, "CSVダウンロード")

        # デフォルト選択項目のチェックボックス属性
        for key in DEFAULT_SELECTED_KEYS:
            self.assertContains(resp, f'value="{key}"')

        # 参考出力列の注記表示
        self.assertContains(resp, "インポート時無視")


class MailingListCSVExportValidationTests(BaseCSVExportTestCase):
    """バリデーションテスト（項目0件選択）。"""

    def test_zero_fields_selected_shows_validation_error(self):
        url = reverse(
            "mailings:mailing_list_member_export",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp = self.client.post(url, {"fields": []})
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "mailings/mailing_list_export_form.html")
        self.assertContains(resp, "1項目以上選択してください")


class MailingListCSVExportExecutionTests(BaseCSVExportTestCase):
    """CSV 出力・ダウンロード実行（POST）のテスト。"""

    def test_csv_export_content_and_headers(self):
        p1, c1 = self._create_person_with_contact(
            last_name="佐藤",
            first_name="次郎",
            full_name="佐藤 次郎",
            organization="テスト商事",
            postal_code="1000002",
            email="sato@example.com",
        )
        p2, c2 = self._create_person_with_contact(
            last_name="鈴木",
            first_name="三郎",
            full_name="鈴木 三郎",
            email="suzuki@example.com",
        )
        self._create_member(p1, is_unsubscribed=False)
        self._create_member(p2, is_unsubscribed=True)

        url = reverse(
            "mailings:mailing_list_member_export",
            kwargs={"pk": self.mailing_list.pk},
        )
        selected = ["full_name", "organization", "email", "address", "is_unsubscribed"]
        resp = self.client.post(url, {"fields": selected})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")

        # BOM の確認（UTF-8 BOM: \xef\xbb\xbf）
        raw_bytes = resp.content
        self.assertTrue(raw_bytes.startswith(b"\xef\xbb\xbf"))

        # RFC 5987 準拠の Content-Disposition
        disposition = resp["Content-Disposition"]
        self.assertIn("attachment;", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        # 日本語ファイル名がパーセントエンコードされていること
        today_str = timezone.now().strftime("%Y%m%d")
        expected_name_encoded = urllib.parse.quote(
            f"メーリングリスト_東京支社リスト_{today_str}.csv".encode("utf-8")
        )
        self.assertIn(expected_name_encoded, disposition)

        # CSV 内容のパース
        text = raw_bytes.decode("utf-8-sig")
        reader = list(csv.reader(io.StringIO(text)))

        # ヘッダー行
        expected_headers = [EXPORT_COLUMNS_DICT[k][1] for k in selected]
        self.assertEqual(reader[0], expected_headers)

        # データ行数
        self.assertEqual(len(reader), 3)

        # 行1（佐藤）
        row1 = reader[1]
        self.assertEqual(row1[0], "佐藤 次郎")
        self.assertEqual(row1[1], "テスト商事")
        self.assertEqual(row1[2], "sato@example.com")
        self.assertIn("千代田区", row1[3])  # address
        self.assertEqual(row1[4], "")  # 退会なし

        # 行2（鈴木）
        row2 = reader[2]
        self.assertEqual(row2[0], "鈴木 三郎")
        self.assertEqual(row2[2], "suzuki@example.com")
        self.assertEqual(row2[4], "退会済み")  # 退会済みフラグ


class MailingListCSVExportMergeResolutionTests(BaseCSVExportTestCase):
    """Person マージ解決および primary_contact 不在時のテスト（仕様書 §3.5）。"""

    def test_merged_person_resolves_surviving_contact(self):
        # p_target（生存先）
        p_target, c_target = self._create_person_with_contact(
            full_name="統合後 本人",
            email="target@example.com",
            organization="新会社",
        )
        # p_merged（マージ元）
        p_merged, c_merged = self._create_person_with_contact(
            full_name="マージ元 旧氏名",
            email="old@example.com",
        )
        p_merged.merged_into = p_target
        p_merged.status = Person.Status.MERGED
        p_merged.save()

        # マージ元 Person をメンバーに追加
        self._create_member(p_merged)

        url = reverse(
            "mailings:mailing_list_member_export",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp = self.client.post(url, {"fields": ["person_id", "full_name", "email", "organization"]})
        self.assertEqual(resp.status_code, 200)

        text = resp.content.decode("utf-8-sig")
        reader = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(reader), 2)  # ヘッダー + 1行
        row = reader[1]
        # surviving_person (p_target) の情報が出力されること
        self.assertEqual(row[0], str(p_target.id))
        self.assertEqual(row[1], "統合後 本人")
        self.assertEqual(row[2], "target@example.com")
        self.assertEqual(row[3], "新会社")

    def test_missing_primary_contact_skipped_safely(self):
        # primary_contact を持たない Person
        orphan_person = Person.objects.create(status=Person.Status.ACTIVE)
        self._create_member(orphan_person)

        # 正常な Person
        p_normal, c_normal = self._create_person_with_contact(full_name="正常 太郎")
        self._create_member(p_normal)

        url = reverse(
            "mailings:mailing_list_member_export",
            kwargs={"pk": self.mailing_list.pk},
        )
        resp = self.client.post(url, {"fields": ["full_name"]})
        self.assertEqual(resp.status_code, 200)

        text = resp.content.decode("utf-8-sig")
        reader = list(csv.reader(io.StringIO(text)))
        # orphan_person はスキップされ、正常太郎の1行のみ出力されること
        self.assertEqual(len(reader), 2)
        self.assertEqual(reader[1][0], "正常 太郎")


class MailingListDetailButtonTests(BaseCSVExportTestCase):
    """詳細画面（mailing_list_detail.html）のエクスポートボタンおよびエクスポートモード表示制御テスト。"""

    def test_export_button_shown_for_privileged_user_in_export_mode(self):
        url = reverse("mailings:mailing_list_detail", kwargs={"pk": self.mailing_list.pk}) + "?mode=export"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CSVエクスポート")
        export_form_url = reverse(
            "mailings:mailing_list_member_export_form",
            kwargs={"pk": self.mailing_list.pk},
        )
        self.assertContains(resp, export_form_url)
        # エクスポートモード時は編集ペン・アーカイブ・メンバー操作ボタンが非表示
        self.assertNotContains(resp, "bi-pencil-fill")
        self.assertNotContains(resp, "title=\"アーカイブ化\"")
        self.assertNotContains(resp, "メンバーを追加")
        self.assertNotContains(resp, "タグで追加")
        self.assertNotContains(resp, "メンバーを削除")
        self.assertNotContains(resp, "タグで除外")

    def test_export_button_hidden_in_normal_mode(self):
        url = reverse("mailings:mailing_list_detail", kwargs={"pk": self.mailing_list.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "CSVエクスポート")
        # 通常アクセス時は編集アイコン・メンバー操作ボタンが表示される
        self.assertContains(resp, "bi-pencil-fill")
        self.assertContains(resp, "メンバーを追加")

    def test_export_button_hidden_for_unprivileged_user(self):
        unprivileged = User.objects.create_user(
            username="unprivileged_btn", password="x"
        )
        unprivileged.user_permissions.add(self.view_list_perm)
        client = Client()
        client.force_login(unprivileged)

        url = reverse("mailings:mailing_list_detail", kwargs={"pk": self.mailing_list.pk}) + "?mode=export"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "CSVエクスポート")


class MailingListListExportModeTests(BaseCSVExportTestCase):
    """一覧画面（mailing_list_list.html）のエクスポートモード表示制御テスト。"""

    def test_list_in_normal_mode(self):
        url = reverse("mailings:mailing_list_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "新規リスト作成")
        self.assertContains(resp, "操作")

    def test_list_in_export_mode(self):
        url = reverse("mailings:mailing_list_list") + "?mode=export"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "新規リスト作成")
        self.assertNotContains(resp, "操作")
        self.assertContains(resp, "mode=export")

