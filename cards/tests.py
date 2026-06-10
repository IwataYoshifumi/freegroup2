"""cards アプリの単体テスト。"""

import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact, ContactFieldConfidence
from persons.models import Person


User = get_user_model()

EXIF_FIXTURES_DIR = Path(__file__).resolve().parent / "test_fixtures" / "exif"


class CardListViewConfidenceAnnotateTests(TestCase):
    """CardListView の confidence ドット用 annotate テスト（仕様変更後）。

    DUPLICATE_CHECK_FIELDS 限定 + confirmed_at による「未確認」判定が反映されること
    を検証する。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_list_test_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            business_card=self.bc,
            full_name="T",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

    def _get_card(self):
        from cards.views import CardListView

        view = CardListView()
        # v1.4.2 統合（テーマ 6）以降、CardListView は ocr_result フィルタを持つ。
        # フィルタ未指定 = "business_card" のみ表示の業務仕様（ストック #15）が
        # 適用されると、ocr_result=None / ocr_status=PENDING のテスト BC が
        # queryset から除外されてしまう。annotate ロジックの検証目的を維持するため、
        # _OCR_FILTER_CHOICES の全 7 値を明示的に渡してフィルタを「全マッチ」にする。
        query = QueryDict(mutable=True)
        for value, _label in CardListView._OCR_FILTER_CHOICES:
            query.appendlist("ocr_result", value)
        view.request = type(
            "Req",
            (),
            {"user": self.user, "GET": query},
        )()
        return view.get_queryset().get(pk=self.bc.pk)

    def test_no_cfc_records_all_false(self):
        """CFC レコードなし → has_unconfirmed_low / has_unconfirmed_mid /
        has_confirmed すべて False。"""
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_mid)
        self.assertFalse(card.has_confirmed)

    def test_unconfirmed_low_in_dup_check_fields(self):
        """DUPLICATE_CHECK_FIELDS に含まれる未確認 low CFC → has_unconfirmed_low True。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        card = self._get_card()
        self.assertTrue(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_mid)
        self.assertFalse(card.has_confirmed)

    def test_unconfirmed_mid_in_dup_check_fields(self):
        """未確認 mid CFC → has_unconfirmed_mid True、low は False。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="personal_phone",
            confidence=ContactFieldConfidence.Confidence.MID,
        )
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertTrue(card.has_unconfirmed_mid)
        self.assertFalse(card.has_confirmed)

    def test_confirmed_cfc(self):
        """confirmed_at セット済み CFC → has_confirmed True、未確認系は False。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_mid)
        self.assertTrue(card.has_confirmed)

    def test_field_outside_dup_check_fields_excluded(self):
        """DUPLICATE_CHECK_FIELDS 外（notes / qualification）の CFC は無視される。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="notes",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="qualification",
            confidence=ContactFieldConfidence.Confidence.MID,
        )
        card = self._get_card()
        self.assertFalse(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_mid)
        self.assertFalse(card.has_confirmed)

    def test_mixed_states(self):
        """未確認 low + confirmed が混在 → 両方 True。優先順はテンプレート側で判定。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.MID,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )
        card = self._get_card()
        self.assertTrue(card.has_unconfirmed_low)
        self.assertFalse(card.has_unconfirmed_mid)
        self.assertTrue(card.has_confirmed)


class CardDetailViewFieldConfidencesTests(TestCase):
    """CardDetailView の field_confidences context テスト（D-3b/D-3d パーツ再利用）。

    CFC レコード（low/mid/confirmed）が context に CFC インスタンス dict として
    反映されること、疑似 high（CFC レコードなし）は dict に含まれないことを検証。
    旧 confidence_map は廃止。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_detail_fc_test_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            business_card=self.bc,
            full_name="T",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

        self.client.force_login(self.user)
        self.url = reverse("cards:card_detail", kwargs={"pk": self.bc.pk})

    def test_no_leaked_multiline_comment(self):
        """名刺詳細で旧複数行 {# #} コメントの本文が画面に漏れない（Phase F1 follow-up 不具合②）。"""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "SNS グループは v1.6.1 で")
        self.assertNotContains(resp, "別途実装する")

    def test_field_confidences_includes_low_mid_confirmed_records(self):
        """CFC レコード（mid/low/confirmed）が field_confidences に CFC インスタンスで載る。"""
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="personal_phone",
            confidence=ContactFieldConfidence.Confidence.LOW,
        )
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="email",
            confidence=ContactFieldConfidence.Confidence.LOW,
            confirmed_at=timezone.now(),
            confirmed_by=self.user,
        )

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        fc = resp.context["field_confidences"]

        self.assertEqual(
            fc["organization"].confidence, ContactFieldConfidence.Confidence.MID
        )
        self.assertIsNone(fc["organization"].confirmed_at)
        self.assertEqual(
            fc["personal_phone"].confidence, ContactFieldConfidence.Confidence.LOW
        )
        self.assertIsNone(fc["personal_phone"].confirmed_at)
        self.assertEqual(
            fc["email"].confidence, ContactFieldConfidence.Confidence.LOW
        )
        self.assertIsNotNone(fc["email"].confirmed_at)

    def test_field_confidences_includes_pseudo_high_for_missing_cfc(self):
        """CFC レコードなしフィールドは confidence='high' の疑似インスタンスで含まれる
        （Contact.get_field_confidences の仕様、§10.5.3）。"""
        resp = self.client.get(self.url)
        fc = resp.context["field_confidences"]
        self.assertIn("full_name", fc)
        self.assertEqual(fc["full_name"].confidence, "high")
        self.assertIsNone(fc["full_name"].confirmed_at)
        self.assertIsNone(fc["full_name"].pk)

    def test_confidence_map_removed_from_context(self):
        """旧 confidence_map は context から削除されている（regression 防止）。"""
        resp = self.client.get(self.url)
        self.assertNotIn("confidence_map", resp.context)


class CardDetailViewEditableModeTests(TestCase):
    """CardDetailView の is_editable 判定 + 編集 UI 表示有無テスト。

    Contact.status と Person.status の組み合わせで編集可能モードと表示のみモードを
    切り替え、_contact_field.html の編集 UI（ラジオ等）が条件通りに出ること、
    Contact 紐付きなしのとき編集セクション自体が出ないことを検証。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_detail_editable_test_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.person = Person.objects.create()
        self.contact = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            business_card=self.bc,
            full_name="T",
            organization="C",
        )
        self.person.primary_contact = self.contact
        self.person.save(update_fields=["primary_contact", "updated_at"])

        # low/mid CFC が無いと _contact_field.html がラジオを描画しないため
        # 編集 UI の表示有無を検証するために 1 件作る
        ContactFieldConfidence.objects.create(
            contact=self.contact,
            field_name="organization",
            confidence=ContactFieldConfidence.Confidence.MID,
        )

        self.client.force_login(self.user)
        self.url = reverse("cards:card_detail", kwargs={"pk": self.bc.pk})

    def _promote_other_to_primary(self):
        """self.contact を別ステータスにしたあと、別 Contact を primary に据える。"""
        other = Contact.objects.create(
            person=self.person,
            status=Contact.Status.PRIMARY,
            full_name="P",
        )
        self.person.primary_contact = other
        self.person.save(update_fields=["primary_contact", "updated_at"])
        return other

    def test_primary_contact_is_editable(self):
        """primary Contact + active Person → is_editable=True、編集 UI が描画される。"""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_editable"])
        content = resp.content.decode()
        self.assertIn("js-contact-field-row", content)
        self.assertIn("js-contact-field-action", content)
        self.assertNotIn("表示のみモード", content)

    def test_active_contact_is_editable(self):
        """active Contact + active Person → is_editable=True。"""
        self.contact.status = Contact.Status.ACTIVE
        self.contact.save(update_fields=["status", "updated_at"])
        self._promote_other_to_primary()

        resp = self.client.get(self.url)
        self.assertTrue(resp.context["is_editable"])

    def test_inactive_contact_is_not_editable(self):
        """inactive Contact → is_editable=False、表示のみモード、編集ラジオ非表示。"""
        self.contact.status = Contact.Status.INACTIVE
        self.contact.save(update_fields=["status", "updated_at"])
        self._promote_other_to_primary()

        resp = self.client.get(self.url)
        self.assertFalse(resp.context["is_editable"])
        content = resp.content.decode()
        self.assertIn("表示のみモード", content)
        self.assertNotIn("js-contact-field-action", content)

    def test_archived_person_is_not_editable(self):
        """archived Person 配下の Contact → is_editable=False。"""
        self.person.status = Person.Status.ARCHIVED
        self.person.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self.url)
        self.assertFalse(resp.context["is_editable"])

    def test_merged_person_is_not_editable(self):
        """merged Person 配下の Contact → is_editable=False。"""
        self.person.status = Person.Status.MERGED
        self.person.save(update_fields=["status", "updated_at"])

        resp = self.client.get(self.url)
        self.assertFalse(resp.context["is_editable"])

    def test_no_contact_hides_editing_section(self):
        """Contact 紐付きなし → 編集セクション自体が出ず、デバッグセクションは維持。"""
        bc2 = BusinessCard.objects.create(
            original_image=self.original, card_index=1
        )
        url2 = reverse("cards:card_detail", kwargs={"pk": bc2.pk})

        resp = self.client.get(url2)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["contact"])
        self.assertFalse(resp.context["is_editable"])
        self.assertEqual(resp.context["field_confidences"], {})
        content = resp.content.decode()
        self.assertNotIn("js-contact-field-row", content)
        self.assertIn("Contact が紐付いていません", content)


class CardDetailDebugUidTests(TestCase):
    """DEBUG=True 時の Card UID コピペ表示（D-4d-1 第 6 弾 §2-1）。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="card_dbg_user", password="dummy"
        )
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_EXTRACTED
        )
        self.card = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _url(self):
        return reverse("cards:card_detail", kwargs={"pk": self.card.pk})

    @override_settings(DEBUG=True)
    def test_card_uid_shown_in_debug_mode(self):
        resp = self.client.get(self._url())
        self.assertContains(resp, 'class="app-debug-uid"')
        self.assertContains(resp, "Card UID:")
        self.assertContains(resp, str(self.card.id))

    @override_settings(DEBUG=False)
    def test_card_uid_hidden_when_debug_false(self):
        resp = self.client.get(self._url())
        self.assertNotContains(resp, "Card UID:")


class OriginalDetailDebugGatingTests(TestCase):
    """original_detail.html のデバッグ表示 debug ガード（HIG v1.4 原則4）。

    JSON 系・チューニング数値・内部語は DEBUG=True かつ INTERNAL_IPS 内のときだけ
    出し、本番（DEBUG=False）では出さない。検出枚数（日本語ラベル）は常に表示。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="orig_dbg_user", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.original = OriginalImage.objects.create(
            user=self.user,
            status=OriginalImage.STATUS_EXTRACTED,
            detected_count=3,
            debug_json={
                "image_size": {"width": 100, "height": 100, "area": 10000},
                "computed_at": "2026-05-31T00:00:00",
                "attempts": [
                    {
                        "attempt_no": 1,
                        "contours_count": 1,
                        "candidates_filter": [
                            {
                                "passed": False,
                                "reject_reason": "area_too_small",
                                "area": 10,
                            },
                        ],
                        "candidates_dedup": [],
                        "results": [],
                        "warp_failures": [
                            {
                                "polygon": {
                                    "top_left": {"x": 0, "y": 0},
                                    "top_right": {"x": 10, "y": 0},
                                    "bottom_right": {"x": 10, "y": 10},
                                    "bottom_left": {"x": 0, "y": 10},
                                },
                                "computed_width": 10,
                                "computed_height": 10,
                                "min_required": [50, 50],
                            }
                        ],
                    }
                ],
            },
        )

    def _url(self):
        return reverse("originals:original_detail", kwargs={"pk": self.original.id})

    @override_settings(DEBUG=True)
    def test_debug_terms_shown_in_debug_mode(self):
        resp = self.client.get(self._url())
        # セクション2 検出統計サマリーの各数値
        self.assertContains(resp, "画像サイズ")
        self.assertContains(resp, "輪郭検出数")
        self.assertContains(resp, "フィルタ通過 / 全候補")
        self.assertContains(resp, "重複除去後 / 通過候補")
        self.assertContains(resp, "透視変換失敗")
        self.assertContains(resp, "最終検出名刺数")
        self.assertContains(resp, "最終計算日時")
        self.assertContains(resp, "reject_reason 別件数")
        self.assertContains(resp, "has_minimum_info")
        self.assertContains(resp, "検出 vs BC 化ギャップ")
        # セクション3〜6（中間生成物・候補/重複/warp 表）
        self.assertContains(resp, "OpenCV マスク画像")
        self.assertContains(resp, "OpenCV 候補一覧テーブル")
        self.assertContains(resp, "OpenCV 重複除去結果")
        self.assertContains(resp, "透視変換失敗候補")  # セクション6 warp 表
        self.assertContains(resp, "area_too_small")  # 候補表セルの生値
        self.assertContains(resp, "debug_json 全体")  # JSON viewer セクション
        # 検出枚数（ユーザー向け）は debug でも当然表示
        self.assertContains(resp, "検出した名刺の枚数")

    @override_settings(DEBUG=False)
    def test_debug_terms_hidden_in_production(self):
        resp = self.client.get(self._url())
        # セクション2 検出統計サマリーの各数値
        self.assertNotContains(resp, "画像サイズ")
        self.assertNotContains(resp, "輪郭検出数")
        self.assertNotContains(resp, "フィルタ通過 / 全候補")
        self.assertNotContains(resp, "重複除去後 / 通過候補")
        self.assertNotContains(resp, "最終検出名刺数")
        self.assertNotContains(resp, "最終計算日時")
        self.assertNotContains(resp, "reject_reason")
        self.assertNotContains(resp, "has_minimum_info")
        self.assertNotContains(resp, "検出 vs BC 化ギャップ")
        # セクション3〜6 のセクション見出し（中間生成物）
        self.assertNotContains(resp, "OpenCV マスク画像")
        self.assertNotContains(resp, "OpenCV 候補一覧テーブル")
        self.assertNotContains(resp, "OpenCV 重複除去結果")
        self.assertNotContains(resp, "透視変換失敗候補")  # セクション6 warp 表
        self.assertNotContains(resp, "area_too_small")  # 生値は出さない
        self.assertNotContains(resp, "debug_json 全体")  # JSON viewer セクション非表示

    @override_settings(DEBUG=False)
    def test_user_facing_info_shown_in_production(self):
        resp = self.client.get(self._url())
        # 日本語化した検出枚数ラベル（英字 BusinessCard を外したもの）は本番でも表示
        self.assertContains(resp, "検出した名刺の枚数")
        self.assertNotContains(resp, "検出枚数 (BusinessCard)")
        # 検出枚数の値（detected_count=3）も表示される
        self.assertContains(resp, "検出した名刺の枚数")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="phase_g_exif_"))
class PhaseGExifUploadTests(TestCase):
    """Phase G：UploadView 経由で OriginalImage.exif_json が正しく保存される
    （仕様書 v1.6.1 統合版 §7.2）。

    4 つの fixture 画像（no_exif / with_exif / with_gps / with_odd_bytes）を
    cards/test_fixtures/exif/ から読み込んで UploadView に POST し、生成された
    OriginalImage.exif_json を検証する。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="phase_g_exif_user", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("cards:card_upload")

    def _upload(self, fixture_name):
        path = EXIF_FIXTURES_DIR / fixture_name
        with open(path, "rb") as fp:
            payload = fp.read()
        upload = SimpleUploadedFile(
            fixture_name, payload, content_type="image/jpeg"
        )
        response = self.client.post(self.url, {"image": upload}, follow=False)
        self.assertEqual(
            response.status_code,
            302,
            f"UploadView は成功時 302 redirect (実際: {response.status_code})",
        )
        return OriginalImage.objects.filter(user=self.user).latest("created_at")

    def test_exif_json_populated_when_image_has_exif(self):
        """ケース 1：EXIF 付き画像 → exif_json に値が入る（§4-1）。"""
        original = self._upload("with_exif.jpg")
        self.assertIsNotNone(original.exif_json)
        self.assertEqual(original.exif_json.get("Make"), "TestCameraCo")
        self.assertEqual(original.exif_json.get("Model"), "TestPhone X")
        self.assertEqual(
            original.exif_json.get("DateTimeOriginal"), "2026:05:25 10:30:00"
        )

    def test_exif_json_null_when_image_has_no_exif(self):
        """ケース 2：EXIF なし画像 → exif_json が NULL のまま（§4-2）。"""
        original = self._upload("no_exif.jpg")
        self.assertIsNone(original.exif_json)

    def test_exif_json_handles_non_json_serializable_values(self):
        """ケース 3：bytes / IFDRational を含む画像 → エラーなく保存（§4-3）。

        _coerce_json_value が IFDRational → float、bytes → str に変換することで、
        生の Exif オブジェクトでは JSON ダンプできない値も exif_json に格納できる。
        """
        original = self._upload("with_odd_bytes.jpg")
        self.assertIsNotNone(original.exif_json)
        # dict 自体が JSON ダンプ可能であること（_coerce_json_value が機能した証拠）
        json.dumps(original.exif_json)
        # IFDRational(1, 250) が float に変換されていること
        self.assertIsInstance(original.exif_json.get("ExposureTime"), float)
        # bytes が ASCII decode されて str になっていること
        self.assertIsInstance(original.exif_json.get("UserComment"), str)

    def test_exif_json_contains_gps_info(self):
        """ケース 4：GPS 付き画像 → exif_json["GPSInfo"] から GPS が取れる（§4-4）。"""
        original = self._upload("with_gps.jpg")
        self.assertIsNotNone(original.exif_json)
        self.assertIn("GPSInfo", original.exif_json)
        gps = original.exif_json["GPSInfo"]
        self.assertEqual(gps.get("GPSLatitudeRef"), "N")
        self.assertEqual(gps.get("GPSLongitudeRef"), "E")
        # 度分秒の tuple → list of float（IFDRational が float 化されている）
        self.assertIsInstance(gps.get("GPSLatitude"), list)
        self.assertEqual(gps["GPSLatitude"][0], 35.0)
        self.assertEqual(gps["GPSLatitude"][1], 40.0)
        self.assertIsInstance(gps.get("GPSLongitude"), list)
        self.assertEqual(gps["GPSLongitude"][0], 139.0)
        self.assertEqual(gps["GPSLongitude"][1], 45.0)


class PhaseGExifDetailViewTests(TestCase):
    """Phase G：OriginalDetailView の EXIF 情報セクション表示（仕様書 §7.2）。

    DB に exif_json を埋めた OriginalImage を作って GET し、テンプレートの
    レンダリング結果を検証する。アップロード経路は PhaseGExifUploadTests でカバー済み。
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="phase_g_detail_user", password="dummy"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _make_and_get(self, exif_json):
        original = OriginalImage.objects.create(
            user=self.user,
            status=OriginalImage.STATUS_EXTRACTED,
            exif_json=exif_json,
        )
        url = reverse("originals:original_detail", kwargs={"pk": original.id})
        return self.client.get(url)

    def test_detail_shows_no_data_message_when_exif_null(self):
        """exif_json が None → 「EXIF 情報なし」が出て <details> は出ない（§詳細画面ケース 1）。"""
        resp = self._make_and_get(None)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "EXIF 情報なし")
        self.assertNotContains(resp, "app-exif-json")
        # セクション見出し自体は出る（実装漏れと区別できるよう）
        self.assertContains(resp, "EXIF 情報")

    def test_detail_renders_json_when_exif_dict_present(self):
        """exif_json が dict → 整形 JSON が <pre class="app-exif-json"> 内に表示（§詳細画面ケース 2）。"""
        resp = self._make_and_get({"Make": "TestCo", "Model": "X"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "app-exif-json")
        self.assertContains(resp, "EXIF 情報を展開")
        self.assertContains(resp, "&quot;Make&quot;")
        self.assertContains(resp, "TestCo")
        self.assertNotContains(resp, "EXIF 情報なし")

    def test_detail_renders_gps_info(self):
        """GPS 含む exif_json → GPSInfo の値が表示される（§詳細画面ケース 3）。"""
        payload = {
            "Make": "Apple",
            "Model": "iPhone 13 mini",
            "GPSInfo": {
                "GPSLatitudeRef": "N",
                "GPSLatitude": [35.0, 1.0, 21.59],
                "GPSLongitudeRef": "E",
                "GPSLongitude": [137.0, 5.0, 29.86],
            },
        }
        resp = self._make_and_get(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "GPSInfo")
        self.assertContains(resp, "GPSLatitudeRef")
        self.assertContains(resp, "21.59")
        self.assertContains(resp, "29.86")


class Phase7CardsViewAuthTests(TestCase):
    """Phase 7 段3-2：cards 6 View の Django 標準 CRUD 権限ガード（rev20 No.2-6/22 ★2）。

    既存の owner 分離（filter(user=user)）は不変で、permission 層のみを検証する。
    破壊的な CardDeleteView は 403 時に削除されないことまで確認。
    """

    def _user_with(self, *codenames):
        import uuid as _uuid

        from django.contrib.auth.models import Permission

        u = User.objects.create_user(
            username=f"cards_auth_{_uuid.uuid4().hex[:8]}", password="x"
        )
        for cn in codenames:
            u.user_permissions.add(
                Permission.objects.get(
                    codename=cn, content_type__app_label="cards"
                )
            )
        return u

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_upload_requires_add_originalimage(self):
        url = reverse("cards:card_upload")
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(
            self._client(self._user_with("add_originalimage")).get(url).status_code,
            200,
        )

    def test_original_list_requires_view_originalimage(self):
        url = reverse("originals:original_list")
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(
            self._client(self._user_with("view_originalimage")).get(url).status_code,
            200,
        )

    def test_original_detail_requires_view_originalimage(self):
        viewer = self._user_with("view_originalimage")
        original = OriginalImage.objects.create(
            user=viewer, status=OriginalImage.STATUS_EXTRACTED
        )
        url = reverse("originals:original_detail", kwargs={"pk": original.pk})
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(self._client(viewer).get(url).status_code, 200)

    def test_card_list_requires_view_businesscard(self):
        url = reverse("cards:card_list")
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(
            self._client(self._user_with("view_businesscard")).get(url).status_code,
            200,
        )

    def test_card_detail_requires_view_businesscard(self):
        viewer = self._user_with("view_businesscard")
        original = OriginalImage.objects.create(
            user=viewer, status=OriginalImage.STATUS_EXTRACTED
        )
        bc = BusinessCard.objects.create(original_image=original, card_index=0)
        url = reverse("cards:card_detail", kwargs={"pk": bc.pk})
        self.assertEqual(self._client(self._user_with()).get(url).status_code, 403)
        self.assertEqual(self._client(viewer).get(url).status_code, 200)

    def test_card_delete_requires_delete_businesscard(self):
        owner = self._user_with("delete_businesscard")
        original = OriginalImage.objects.create(
            user=owner, status=OriginalImage.STATUS_EXTRACTED
        )
        bc = BusinessCard.objects.create(original_image=original, card_index=0)
        url = reverse("cards:card_delete", kwargs={"pk": bc.pk})
        # 権限なし → 403、BusinessCard は削除されない（破壊的操作の防御）
        resp = self._client(self._user_with()).post(url)
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(BusinessCard.objects.filter(pk=bc.pk).exists())
        # 権限あり（かつ owner）→ 302、削除される
        resp = self._client(owner).post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BusinessCard.objects.filter(pk=bc.pk).exists())

