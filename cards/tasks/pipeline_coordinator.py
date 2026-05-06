"""名刺画像の切り出しパイプライン（OpenCV 検出・透視変換 → Claude OCR 方式）。

① detect_cards で名刺を検出・透視変換済み横長画像を取得
② warped_image ごとに ocr_service.run_ocr でフィールドを取得
③ normalize_to_contact_dict → has_minimum_info → save_card_image_tmp で保存
④ BusinessCard / Contact / Person 保存

【トランザクション境界】card 単位（1 card の失敗で他 card に影響しない）。
"""

import json
import logging
import os
from datetime import datetime, timezone

import jsonschema
from django.conf import settings
from django.db import transaction

from cards.models import BusinessCard, OriginalImage
from cards.services.card_detector import detect_cards
from cards.services.has_minimum_info import has_minimum_info
from cards.services.json_normalizer import (
    calc_orientation_adjusted_confidence_map,
    normalize_to_contact_dict,
)
from cards.tasks.card_cropper import save_card_image_tmp
from cards.tasks.ocr_service import OcrService
from contacts.models import Contact, ContactFieldConfidence
from persons.models import Person

logger = logging.getLogger(__name__)


class PipelineCoordinator:
    """1 OriginalImage の切り出し処理を統括する。"""

    def __init__(self, original_image):
        self.original_image = original_image
        self.error_messages = []
        self.created_count = 0
        self.failed_db_count = 0
        self._ocr_service = OcrService()
        self._cards_by_index = {}   # {card_index: card_data or None}
        self._combined_schema = None
        self._last_api_response = None
        self._card_item_schema_cache = None

    def run_pipeline(self):
        """OriginalImage の切り出し処理を実行する。

        [性質] 副作用あり（DB 書き込み・ファイル書き込み・API 呼び出し）
        [入力] なし（self.original_image を使う）
        [出力] None（status / error_message / detected_count / raw_json を OriginalImage に保存）
        [前提] process_pending._claim_lock により status=processing に遷移済みであること
        [方針] 例外を外に漏らさない。失敗は status と error_message に集約する。
               正常・異常いずれのパスでも processing → extracted/garbage/failed に遷移する。
               pending に戻る経路はない。
        """
        # === 防御チェック（v1.3.1 追加） ===
        if self.original_image.status != OriginalImage.STATUS_PROCESSING:
            current_status = self.original_image.status
            logger.error(
                "run_pipeline called with status=%s, expected processing. Aborting. "
                "OriginalImage %s",
                current_status,
                self.original_image.id,
            )
            self.original_image.status = OriginalImage.STATUS_FAILED
            self.original_image.error_message = (
                f"run_pipeline が status={current_status} で呼ばれました。"
                "CAS が成立していない可能性があります。"
            )
            with transaction.atomic():
                self.original_image.save(
                    update_fields=["status", "error_message", "updated_at"]
                )
            return

        detections = []
        try:
            detections = detect_cards(self.original_image.image_file.path)
            self.original_image.detected_count = len(detections)

            if not detections:
                self.original_image.status = OriginalImage.STATUS_GARBAGE
                return

            for i in range(len(detections)):
                self._cards_by_index[i] = None

            for card_index, detection in enumerate(detections):
                self._process_card(
                    detection["warped_image"],
                    card_index,
                    detection["polygon"],
                )

            raw_json = {
                "schema_version": "1.3.0",
                "ocr_meta": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "api_response": self._last_api_response or {},
                "cards": [self._cards_by_index.get(i) for i in range(len(detections))],
            }
            if self._last_api_response:
                try:
                    jsonschema.validate(instance=raw_json, schema=self._load_combined_schema())
                except jsonschema.ValidationError as e:
                    logger.warning("raw_json スキーマ検証失敗（保存続行）: %s", e.message)
            self.original_image.raw_json = raw_json
            self.original_image.status = OriginalImage.STATUS_EXTRACTED

        except Exception as e:
            logger.exception(
                "PipelineCoordinator unexpected error for OriginalImage %s",
                self.original_image.id,
            )
            self.original_image.status = OriginalImage.STATUS_FAILED
            self.error_messages.append(f"想定外のエラー: {type(e).__name__}: {e}")
            # 処理済み card が存在する場合は部分的な raw_json を組み立てて保存する。
            # raw_json=None のまま BusinessCard が残ると不変条件が壊れるため。
            if self._cards_by_index:
                self.original_image.raw_json = {
                    "schema_version": "1.3.0",
                    "ocr_meta": {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    "api_response": self._last_api_response or {},
                    "cards": [
                        self._cards_by_index.get(i) for i in range(len(detections))
                    ],
                }
        finally:
            self.original_image.error_message = "\n".join(self.error_messages)
            with transaction.atomic():
                self.original_image.save(
                    update_fields=[
                        "status",
                        "raw_json",
                        "error_message",
                        "detected_count",
                        "updated_at",
                    ]
                )

    def _process_card(self, warped_image, card_index, polygon):
        """[性質] 副作用あり（API 呼び出し・DB 書き込み・ファイル書き込み）"""
        # ① DEBUG 保存
        self._save_dev_image(warped_image, card_index)

        # ② OCR（想定外の例外も含め全て捕捉し 1 card の失敗が全体に波及しないようにする）
        try:
            ocr_result = self._ocr_service.run_ocr(warped_image)
        except Exception as e:
            self.error_messages.append(f"card_index={card_index}: OCR失敗 ({type(e).__name__}: {e})")
            return

        # ③ api_response を保存
        self._last_api_response = ocr_result.get("api_response")

        # ④ card_data を抽出・集約
        cards_in_result = ocr_result.get("cards") or []
        card_data = cards_in_result[0] if cards_in_result else None
        self._cards_by_index[card_index] = card_data

        if card_data is None:
            self.error_messages.append(f"card_index={card_index}: OCR cards 空")
            return

        # ⑤ スキーマ検証（card 単位）
        try:
            jsonschema.validate(instance=card_data, schema=self._load_card_item_schema())
        except jsonschema.ValidationError as e:
            self.error_messages.append(
                f"card_index={card_index}: スキーマ検証失敗 ({e.message})"
            )
            return

        card_meta = card_data.get("card_meta") or {}

        # ⑥ is_business_card チェック
        if not card_meta.get("is_business_card", True):
            return

        # ⑦ フィールド正規化
        orientation = card_meta.get("orientation", "normal")
        try:
            contact_dict, confidence_map = normalize_to_contact_dict(ocr_result, 0)
        except Exception as e:
            self.error_messages.append(f"card_index={card_index}: 正規化失敗 ({e})")
            return

        # ⑧ orientation に応じた confidence 補正
        confidence_map = calc_orientation_adjusted_confidence_map(
            contact_dict, confidence_map, orientation
        )

        # ⑨ 最低情報チェック
        if not has_minimum_info(contact_dict):
            return

        # ⑩ DB先・ファイル後：card_image=None で DB 先行作成 → 一時ファイル保存 → on_commit でリネーム
        tmp_abs = None
        try:
            with transaction.atomic():
                # 仕様書 §15.4：循環 FK を 3 段階で解決する
                person = Person.objects.create(status=Person.Status.ACTIVE)
                business_card = BusinessCard.objects.create(
                    original_image=self.original_image,
                    card_image=None,
                    card_index=card_index,
                    orientation=orientation,
                )
                contact = Contact.objects.create(
                    business_card=business_card,
                    person=person,
                    status=Contact.Status.PRIMARY,
                    created_by=self.original_image.user,
                    **contact_dict,
                )
                person.primary_contact = contact
                person.save()
                for field_name, conf in confidence_map.items():
                    if conf in ("low", "medium"):
                        ContactFieldConfidence.objects.create(
                            contact=contact,
                            field_name=field_name,
                            confidence=conf,
                        )

                # 一時パスに JPEG を書き込む（DB コミット前）
                try:
                    crop_success, tmp_abs, final_rel, crop_error = save_card_image_tmp(
                        warped_image, str(self.original_image.id), card_index,
                    )
                except Exception as e:
                    crop_success, tmp_abs, final_rel, crop_error = False, None, None, str(e)

                if not crop_success:
                    self.error_messages.append(
                        f"card_index={card_index}: 画像保存失敗 ({crop_error})"
                    )
                else:
                    # コミット後に tmp → final にリネーム、card_image パスを UPDATE
                    _bc_id = business_card.id
                    _tmp = tmp_abs
                    _final_rel = final_rel
                    _final_abs = os.path.join(settings.MEDIA_ROOT, final_rel)

                    def _finalize_card_image(
                        bc_id=_bc_id, ta=_tmp, fr=_final_rel, fa=_final_abs
                    ):
                        renamed = False
                        try:
                            os.rename(ta, fa)
                            renamed = True
                            BusinessCard.objects.filter(id=bc_id).update(card_image=fr)
                        except Exception as exc:
                            logger.warning(
                                "card_image finalize failed for BusinessCard %s: %s",
                                bc_id,
                                exc,
                            )
                            cleanup_path = fa if renamed else ta
                            try:
                                os.unlink(cleanup_path)
                            except OSError:
                                pass

                    transaction.on_commit(_finalize_card_image)
                    tmp_abs = None  # on_commit に委譲済みなので例外ハンドラではクリーンアップ不要

            self.created_count += 1

        except Exception as e:
            self.failed_db_count += 1
            self.error_messages.append(
                f"card_index={card_index}: DB保存失敗 ({type(e).__name__}: {e})"
            )
            # ロールバック時に一時ファイルが残っていれば削除
            if tmp_abs:
                try:
                    os.unlink(tmp_abs)
                except OSError as ue:
                    logger.warning("tmp cleanup failed for %s: %s", tmp_abs, ue)

    def _save_dev_image(self, warped_image, card_index):
        """[性質] 副作用あり（ファイル書き込み）/ DEBUG=True のときのみ保存する。"""
        if not settings.DEBUG:
            return
        try:
            now = datetime.now()
            rel = (
                f"debug/{now.strftime('%Y')}/{now.strftime('%m')}/"
                f"{now.strftime('%d')}/{self.original_image.id}-{card_index}-dev.jpg"
            )
            abs_path = os.path.join(settings.MEDIA_ROOT, rel)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            warped_image.convert("RGB").save(abs_path, format="JPEG", quality=95)
            logger.debug("dev image saved: %s", rel)
        except Exception as e:
            logger.warning("dev image save failed for card_index=%s: %s", card_index, e)

    def _load_combined_schema(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）"""
        if self._combined_schema is None:
            schema_path = (
                settings.BASE_DIR
                / "docs"
                / "json_schema"
                / "v1.3.0"
                / "combined_response.json"
            )
            with open(schema_path, encoding="utf-8") as f:
                self._combined_schema = json.load(f)
        return self._combined_schema

    def _load_card_item_schema(self):
        """[性質] 副作用あり（ファイル読み込み・キャッシュ）

        cards 配列の item スキーマを抽出して card 単位の検証に使う。
        $defs を付与することで $ref 解決が機能する。
        """
        if self._card_item_schema_cache is None:
            full = self._load_combined_schema()
            self._card_item_schema_cache = {
                "$schema": full.get("$schema", ""),
                "$defs": full.get("$defs", {}),
                **full["properties"]["cards"]["items"],
            }
        return self._card_item_schema_cache
