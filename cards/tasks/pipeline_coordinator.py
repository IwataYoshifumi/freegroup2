"""1 OriginalImage の OCR 処理パイプライン（仕様書 v1.2.2 §8.5.1）。

OcrService → JSON Schema 検証 → raw_json 保存 → cards 走査 →
BusinessCard / Person / Contact / ContactFieldConfidence 生成 → status 確定。

【トランザクション境界】card 単位（1 card の失敗で他 card に影響しない）。
【error_message】部分失敗は "card_index=N: <種別> (<詳細>)" 形式で改行区切り（v1.2.1 §10.4）。
"""

import json
import logging
import os

import jsonschema
from django.conf import settings
from django.db import transaction

from cards.models import (
    BusinessCard,
    Contact,
    ContactFieldConfidence,
    OriginalImage,
    Person,
)
from cards.services.has_minimum_info import has_minimum_info
from cards.services.json_normalizer import normalize_to_contact_dict
from cards.tasks.card_cropper import create_card_image
from cards.tasks.ocr_service import OcrApiError, OcrService

logger = logging.getLogger(__name__)


class _PipelineFatal(Exception):
    """Pipeline 全体を失敗させる致命的エラー（OCR 通信・スキーマ検証）。"""


class PipelineCoordinator:
    """1 OriginalImage の処理全体を統括する指揮官（仕様書 v1.2.2 §8.5.1）。

    クラスとして実装している理由：処理途中の状態（error_messages, created_count）を
    保持し、内部メソッドに分割するため。
    """

    SCHEMA_RELATIVE_PATH = ("docs", "json_schema", "v1.0.0", "standard_response.json")
    LOW_CONF_VALUES = ("low", "medium")

    def __init__(self, original_image):
        self.original_image = original_image
        self.error_messages = []
        self.created_count = 0  # ローカル変数（DB 保存しない）
        self.failed_db_count = 0
        self._schema_cache = None
        self._ocr_service = None

    def run_pipeline(self):
        """OriginalImage の OCR 処理を実行する。

        [性質] 副作用あり（複合処理: API 呼び出し・DB 書き込み・ファイル書き込み）
        [入力] なし（self.original_image を使う）
        [出力] None（status / error_message / detected_count を OriginalImage に保存）
        [方針] 例外を外に漏らさない。失敗は status と error_message に集約する。
        """
        cards = []
        try:
            raw_json = self._call_ocr()
            self._validate_schema(raw_json)
            self.original_image.raw_json = raw_json

            cards = raw_json.get("cards") if isinstance(raw_json.get("cards"), list) else []
            self.original_image.detected_count = len(cards)

            for card_index in range(len(cards)):
                self._process_card(raw_json, card_index)

            self._finalize_status(cards)

        except _PipelineFatal as e:
            self.original_image.status = OriginalImage.STATUS_FAILED
            self.error_messages.append(str(e))
        except Exception as e:
            logger.exception(
                "PipelineCoordinator unexpected error for OriginalImage %s",
                self.original_image.id,
            )
            self.original_image.status = OriginalImage.STATUS_FAILED
            self.error_messages.append(f"想定外のエラー: {type(e).__name__}: {e}")
        finally:
            self.original_image.error_message = "\n".join(self.error_messages)
            self.original_image.save()

    # ----- 内部メソッド -----

    def _call_ocr(self):
        """[性質] 副作用あり（API 呼び出し）"""
        if self._ocr_service is None:
            self._ocr_service = OcrService()
        try:
            return self._ocr_service.run_ocr(self.original_image.image_file.path)
        except OcrApiError as e:
            raise _PipelineFatal(f"OCR API 失敗: {e}")
        except ValueError as e:
            raise _PipelineFatal(f"OCR 入力不正: {e}")

    def _validate_schema(self, raw_json):
        """[性質] 副作用あり（jsonschema 例外を内部例外に変換）"""
        try:
            jsonschema.validate(instance=raw_json, schema=self._load_schema())
        except jsonschema.ValidationError as e:
            raise _PipelineFatal(f"スキーマ検証失敗: {e.message}")

    def _load_schema(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）"""
        if self._schema_cache is None:
            schema_path = settings.BASE_DIR.joinpath(*self.SCHEMA_RELATIVE_PATH)
            with open(schema_path, encoding="utf-8") as f:
                self._schema_cache = json.load(f)
        return self._schema_cache

    def _process_card(self, raw_json, card_index):
        """[性質] 副作用あり（DB 書き込み・ファイル書き込み）

        1 枚の card を処理する。仕様書 v1.2.1 §10.1 / §10.2 に従い、
        is_business_card=true・正規化成功・has_minimum_info=true を満たす card のみ
        BusinessCard / Contact / Person / ContactFieldConfidence を作成する。
        """
        card = raw_json["cards"][card_index]
        card_meta = card.get("card_meta", {}) if isinstance(card, dict) else {}

        # C2: is_business_card=false ならスキップ（other card 次第）
        if not card_meta.get("is_business_card", False):
            return

        # 正規化（card_index は範囲内のはずだが防御）
        try:
            contact_dict, confidence_map = normalize_to_contact_dict(raw_json, card_index)
        except ValueError as e:
            self.error_messages.append(
                f"card_index={card_index}: 正規化失敗 ({e})"
            )
            return

        # has_minimum_info で弾かれたら BusinessCard 作成しない（garbage 候補）
        if not has_minimum_info(contact_dict):
            return

        # 切り抜き（失敗しても BusinessCard 作成は続行 v1.2.1 §10.3）
        bbox = card_meta.get("bbox", {})
        try:
            crop_success, card_image_path, crop_error = create_card_image(
                self.original_image.image_file.path, bbox
            )
        except Exception as e:
            crop_success, card_image_path, crop_error = False, None, str(e)
        if not crop_success:
            self.error_messages.append(
                f"card_index={card_index}: 切り抜き失敗 ({crop_error})"
            )

        # DB 保存（card 単位 transaction.atomic、失敗で他 card に影響させない）
        try:
            with transaction.atomic():
                self._create_records(card_index, contact_dict, confidence_map, card_image_path)
            self.created_count += 1
        except Exception as e:
            self.failed_db_count += 1
            self.error_messages.append(
                f"card_index={card_index}: DB 保存失敗 ({type(e).__name__}: {e})"
            )
            self._cleanup_card_image(card_image_path)

    def _create_records(self, card_index, contact_dict, confidence_map, card_image_path):
        """[性質] 副作用あり（DB 書き込み）"""
        person = Person.objects.create()
        business_card = BusinessCard.objects.create(
            original_image=self.original_image,
            card_image=card_image_path or None,
            card_index=card_index,
        )
        contact = Contact.objects.create(
            business_card=business_card,
            person=person,
            **contact_dict,
        )
        for field_name, confidence in confidence_map.items():
            if confidence in self.LOW_CONF_VALUES:
                ContactFieldConfidence.objects.create(
                    contact=contact,
                    field_name=field_name,
                    confidence=confidence,
                )

    def _cleanup_card_image(self, card_image_path):
        """[性質] 副作用あり（ファイル削除）/ DB 失敗で残ったファイルを削除する。"""
        if not card_image_path:
            return
        try:
            absolute_path = os.path.join(settings.MEDIA_ROOT, card_image_path)
            if os.path.exists(absolute_path):
                os.remove(absolute_path)
        except OSError as e:
            logger.warning(
                "card_image cleanup failed for %s: %s", card_image_path, e
            )

    def _finalize_status(self, cards):
        """[性質] 副作用あり（self.original_image.status を更新）

        v1.2.1 §4.7.1 の判定ルールに従って status を確定する。
        """
        if self.created_count >= 1:
            self.original_image.status = OriginalImage.STATUS_EXTRACTED
            return

        if len(cards) == 0:
            self.original_image.status = OriginalImage.STATUS_GARBAGE
            return

        valid_card_count = sum(
            1
            for c in cards
            if isinstance(c, dict)
            and isinstance(c.get("card_meta"), dict)
            and c["card_meta"].get("is_business_card", False)
        )
        if valid_card_count == 0:
            self.original_image.status = OriginalImage.STATUS_GARBAGE
            return

        # 有効 card あり、created=0：DB 失敗の有無で garbage / failed を分岐
        if self.failed_db_count == 0:
            # 全 card が has_minimum_info で弾かれた → garbage
            self.original_image.status = OriginalImage.STATUS_GARBAGE
        else:
            self.original_image.status = OriginalImage.STATUS_FAILED
