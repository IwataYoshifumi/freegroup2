"""OCR バックエンド抽象化（(2)）のテスト：ファクトリ切替・FileOcrBackend 3 系統。

- OcrFactoryTests          : OCR_BACKEND による get_ocr_backend() の切替
- FileOcrBackendUnitTests   : run_ocr 単体（ファイル有 / 無 / スキーマ違反 / BC=None）
- WorkerCoworkPipelineTests : process_cardimage_with_ocr 経由の end-to-end（有 / 無 / 違反）
"""

import io
import json
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from cards.models import BusinessCard, OriginalImage
from cards.tasks.file_ocr_backend import FileOcrBackend
from cards.tasks.ocr_exceptions import FileOcrSchemaError, OcrSourceNotReady
from cards.tasks.ocr_factory import get_ocr_backend
from cards.tasks.ocr_pipeline import process_cardimage_with_ocr
from cards.tasks.ocr_service import OcrService
from contacts.models import Contact
from contacts.test_phone_validation import _build_card

User = get_user_model()


class OcrFactoryTests(TestCase):
    """OCR_BACKEND によるバックエンド切替（card_detector パターン踏襲）。"""

    @override_settings(OCR_BACKEND="claude_sonnet_4_6")
    def test_claude_backend_returns_ocrservice_with_hyphen_model(self):
        backend = get_ocr_backend()
        self.assertIsInstance(backend, OcrService)
        # アンダースコア→ハイフン変換でモデル名へ。
        self.assertEqual(backend._model, "claude-sonnet-4-6")

    @override_settings(OCR_BACKEND="worker_cowork")
    def test_worker_backend_returns_file_backend(self):
        self.assertIsInstance(get_ocr_backend(), FileOcrBackend)

    @override_settings(OCR_BACKEND="bogus_backend")
    def test_unknown_backend_raises_value_error(self):
        with self.assertRaises(ValueError):
            get_ocr_backend()


class _FileBackendBase(TestCase):
    def setUp(self):
        # media を一時ディレクトリに隔離（実 media/ocr_json を汚さない）。
        self.tmp_media = tempfile.mkdtemp()
        ov = override_settings(MEDIA_ROOT=self.tmp_media)
        ov.enable()
        self.addCleanup(ov.disable)
        self.addCleanup(lambda: shutil.rmtree(self.tmp_media, ignore_errors=True))

        self.user = User.objects.create_superuser(username="fb_user", password="d")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED
        )
        self.bc = BusinessCard.objects.create(
            original_image=self.original,
            card_index=0,
            ocr_status=BusinessCard.OcrStatus.PENDING,
        )

    def _ocr_json_path(self):
        return (
            Path(self.tmp_media)
            / "ocr_json"
            / f"{self.bc.original_image_id}-{self.bc.card_index}.json"
        )

    def _write_ocr_json(self, card_dict):
        path = self._ocr_json_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(card_dict, f, ensure_ascii=False)

    def _attach_card_image(self):
        buf = io.BytesIO()
        Image.new("RGB", (12, 12), "white").save(buf, format="JPEG")
        self.bc.card_image.save(
            f"{self.bc.id}.jpg", ContentFile(buf.getvalue()), save=True
        )


class FileOcrBackendUnitTests(_FileBackendBase):
    """FileOcrBackend.run_ocr 単体（image は不要・無視される）。"""

    def test_file_present_returns_wrapped_cards(self):
        card = _build_card(country="JP")
        self._write_ocr_json(card)
        backend = FileOcrBackend()
        result = backend.run_ocr(None, business_card=self.bc)
        self.assertIn("cards", result)
        self.assertEqual(len(result["cards"]), 1)
        self.assertEqual(result["cards"][0], card)
        self.assertEqual(result["api_response"]["model"], "worker_cowork")

    def test_file_absent_raises_source_not_ready(self):
        # ファイルを書かない → 未着。
        backend = FileOcrBackend()
        with self.assertRaises(OcrSourceNotReady):
            backend.run_ocr(None, business_card=self.bc)

    def test_schema_violation_raises_schema_error_with_field(self):
        card = _build_card(country="JP")
        del card["metadata"]  # required ブロック欠落 → スキーマ違反
        self._write_ocr_json(card)
        backend = FileOcrBackend()
        with self.assertRaises(FileOcrSchemaError) as cm:
            backend.run_ocr(None, business_card=self.bc)
        # 違反内容（メッセージ）が記録対象として乗っていること。
        self.assertIn("スキーマ違反", str(cm.exception))

    def test_business_card_none_raises_value_error(self):
        self._write_ocr_json(_build_card(country="JP"))
        backend = FileOcrBackend()
        with self.assertRaises(ValueError):
            backend.run_ocr(None, business_card=None)


@override_settings(OCR_BACKEND="worker_cowork")
class WorkerCoworkPipelineTests(_FileBackendBase):
    """process_cardimage_with_ocr 経由の end-to-end（worker_cowork）。"""

    def test_file_present_creates_contact_and_done(self):
        self._attach_card_image()
        self._write_ocr_json(_build_card(country="JP"))

        process_cardimage_with_ocr(self.bc, get_ocr_backend())

        self.bc.refresh_from_db()
        self.assertEqual(self.bc.ocr_status, BusinessCard.OcrStatus.DONE)
        self.assertEqual(self.bc.ocr_result, BusinessCard.OcrResult.BUSINESS_CARD)
        self.assertEqual(
            Contact.objects.filter(business_card=self.bc).count(), 1
        )

    def test_file_absent_keeps_pending_no_error(self):
        self._attach_card_image()
        # ocr_json は書かない → 未着。

        process_cardimage_with_ocr(self.bc, get_ocr_backend())

        self.bc.refresh_from_db()
        # pending 維持・Contact 未生成・error_message を汚さない。
        self.assertEqual(self.bc.ocr_status, BusinessCard.OcrStatus.PENDING)
        self.assertEqual(self.bc.error_message, "")
        self.assertEqual(Contact.objects.filter(business_card=self.bc).count(), 0)

    def test_schema_violation_marks_failed_and_records_error(self):
        self._attach_card_image()
        card = _build_card(country="JP")
        del card["name"]  # required ブロック欠落 → スキーマ違反
        self._write_ocr_json(card)

        process_cardimage_with_ocr(self.bc, get_ocr_backend())

        self.bc.refresh_from_db()
        self.assertEqual(self.bc.ocr_status, BusinessCard.OcrStatus.FAILED)
        self.assertEqual(self.bc.ocr_result, BusinessCard.OcrResult.OCR_FAILED)
        self.assertIn("スキーマ違反", self.bc.error_message)
        self.assertEqual(Contact.objects.filter(business_card=self.bc).count(), 0)
