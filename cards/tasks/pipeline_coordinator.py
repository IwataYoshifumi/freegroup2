"""名刺画像の切り出しパイプライン（OpenCV 検出・透視変換 → Claude OCR 方式）。

① detect_cards_with_debug で名刺を検出・透視変換済み横長画像を取得（debug_json も保存）
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

from cards.models import BusinessCard, Contact, ContactFieldConfidence, OriginalImage, Person
from cards.services.detectors.opencv_detector import (
    detect_cards_with_debug,
    results_from_debug_result,
)
from cards.services.has_minimum_info import has_minimum_info
from cards.services.json_normalizer import (
    calc_orientation_adjusted_confidence_map,
    normalize_to_contact_dict,
)
from cards.services.opencv_debug_cache import save_debug_data
from cards.services.osd import (
    apply_upright,
    detect_osd_info,
    detect_osd_rotation,
    run_tesseract_ocr,
)
from cards.tasks.card_cropper import save_card_image_tmp
from cards.tasks.ocr_service import OcrService

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
        self._tesseract_by_index = {}   # {card_index: {"osd": ..., "tesseract_ocr": ...}}（記録用）
        self._combined_schema = None
        self._last_api_response = None
        self._card_item_schema_cache = None
        self._opencv_only = False    # True=第1段(OpenCV検出＋OSD正立化のみ・OCR未実行)

    def run_pipeline(self):
        """OriginalImage の切り出し＋OCR を通しで実行する（stage1＋stage2）。

        [性質] 副作用あり（DB 書き込み・ファイル書き込み・API 呼び出し）
        [入力] なし（self.original_image を使う）
        [出力] None（status / error_message / detected_count / raw_json を OriginalImage に保存）
        [前提] process_pending._claim_lock により status=processing に遷移済みであること
        [方針] 例外を外に漏らさない。失敗は status と error_message に集約する。
               正常・異常いずれのパスでも processing → extracted/garbage/failed に遷移する。
               pending に戻る経路はない。
        """
        self._run(self._process_card, opencv_only=False)

    def run_opencv_stage(self):
        """OpenCV 検出＋OSD 正立化のみ実行する（第1段・OCR/課金なし）。

        [性質] 副作用あり（DB 書き込み・ファイル書き込み・外部プロセス OSD 呼び出し）
        [出力] None（status / detected_count / raw_json["cards"][*]["osd"] を保存）
        [方針] run_ocr は呼ばない。検出由来（stage1）のみで BC を作成し card_image に正立後画像を格納、
               osd を raw_json に残して EXTRACTED にする。状態管理の正式化は第2段（main の ocr_status）。
        """
        self._run(
            lambda warped, idx, polygon: self._process_card_stage1(warped, idx),
            opencv_only=True,
        )

    def _run(self, per_card, opencv_only):
        """[性質] 副作用あり（DB/ファイル/外部プロセス・条件により API）/ 検出→per_card ループ→保存の共通土台。

        per_card(warped_image, card_index, polygon) を各検出に適用する。run_pipeline と
        run_opencv_stage の足回り（防御チェック・検出・debug_json 保存・raw_json 組立・status 遷移・保存）
        を共有し、カードごとの処理だけを差し替える。
        """
        self._opencv_only = opencv_only

        # === 防御チェック（v1.3.1 追加） ===
        if self.original_image.status != OriginalImage.STATUS_PROCESSING:
            current_status = self.original_image.status
            logger.error(
                "_run called with status=%s, expected processing. Aborting. "
                "OriginalImage %s",
                current_status,
                self.original_image.id,
            )
            self.original_image.status = OriginalImage.STATUS_FAILED
            self.original_image.error_message = (
                f"パイプラインが status={current_status} で呼ばれました。"
                "CAS が成立していない可能性があります。"
            )
            with transaction.atomic():
                self.original_image.save(
                    update_fields=["status", "error_message", "updated_at"]
                )
            return

        detections = []
        try:
            debug_result = detect_cards_with_debug(self.original_image.image_file.path)
            # 検出結果は attempts[-1] に入る。在り処を知る唯一の窓口 results_from_debug_result()
            # を通して取り出す（旧トップレベル results を直読みして全件 garbage 化した 1f9712f の修正）。
            detections = results_from_debug_result(debug_result)
            self.original_image.detected_count = len(detections)

            # debug_json 保存は失敗しても処理を止めない（debug は副次情報のため）
            try:
                save_debug_data(self.original_image, debug_result)
            except Exception as e:
                logger.warning(
                    "save_debug_data failed for OriginalImage %s: %s",
                    self.original_image.id, e,
                )

            if not detections:
                self.original_image.status = OriginalImage.STATUS_GARBAGE
                return

            for i in range(len(detections)):
                self._cards_by_index[i] = None

            for card_index, detection in enumerate(detections):
                per_card(
                    detection["warped_image"],
                    card_index,
                    detection["polygon"],
                )

            self.original_image.raw_json = self._build_raw_json(len(detections))
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
                self.original_image.raw_json = self._build_raw_json(len(detections))
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

    def _build_raw_json(self, n):
        """[性質] 純関数寄り（DB操作なし）/ raw_json dict を組み立てる。

        OCR 実行時（_last_api_response あり）のみ JSON Schema で再検証する。第1段（OCR前）は
        cards が OCR 形を満たさないため検証をスキップする（osd だけ持つ最小カードを許す）。
        """
        raw_json = {
            "schema_version": "1.3.0",
            "ocr_meta": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "api_response": self._last_api_response or {},
            "cards": self._cards_payload(n),
        }
        if self._last_api_response:
            try:
                jsonschema.validate(instance=raw_json, schema=self._load_combined_schema())
            except jsonschema.ValidationError as e:
                logger.warning("raw_json スキーマ検証失敗（保存続行）: %s", e.message)
        return raw_json

    def _process_card(self, warped_image, card_index, polygon):
        """[性質] 副作用あり（API 呼び出し・DB 書き込み・ファイル書き込み）/ stage1＋stage2 の通し。"""
        business_card, upright_image = self._process_card_stage1(warped_image, card_index)
        if business_card is None:
            return  # stage1 で BC 作成失敗（DB エラー等）→ OCR に進まない
        self._process_card_stage2(business_card, upright_image, card_index)

    def _process_card_stage1(self, warped_image, card_index):
        """[性質] 副作用あり（外部プロセス OSD・DB 書き込み・ファイル書き込み）/ 検出由来の処理。

        クロップ → OSD 正立化 → BC 作成（card_image=正立後画像）→ osd 記録。run_ocr は呼ばない。
        [入力] warped_image: 透視変換済みクロップ（PIL.Image）、card_index: int
        [出力] (BusinessCard or None, upright_image)。BC 作成失敗時は (None, upright_image)。
        [方針] OCR 前なので orientation=normal・ocr_result は既定（第1段は検証用と割り切る）。
        """
        # ① DEBUG 保存
        self._save_dev_image(warped_image, card_index)

        # ①.5 Tesseract OSD で正立化（成功かつ rotate≠0 のときのみ回す。失敗・rotate=0 は元クロップ）。
        upright_image = self._upright_with_osd(warped_image, card_index)

        # ①.6 Tesseract OSD/OCR の出力を記録（osd を含む。Tesseract はローカル＝課金ゼロ）。
        #      向き判定・クロップ採否には使わない。全例外を捕捉し、失敗しても処理を止めない。
        self._tesseract_by_index[card_index] = self._collect_tesseract_record(
            warped_image, upright_image, card_index
        )

        # ② BC 作成（card_image=正立後画像）。OCR 由来の値は stage2 で確定する。
        business_card = self._create_card_with_image(card_index, upright_image)
        return business_card, upright_image

    def _process_card_stage2(self, business_card, upright_image, card_index):
        """[性質] 副作用あり（API 呼び出し・DB 書き込み）/ OCR 由来の処理。stage1 作成済み BC を更新する。

        run_ocr → card_data 抽出 → schema 検証 → ocr_result 確定 → has_minimum_info →
        Contact / Person 作成。第1段（run_opencv_stage）では実行しない。
        """
        # ② OCR（想定外の例外も含め全て捕捉し 1 card の失敗が全体に波及しないようにする）
        #    run_ocr は 1 回のみ（2回OCR はしない）。
        try:
            ocr_result = self._ocr_service.run_ocr(upright_image)
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
        orientation = card_meta.get("orientation", "normal")

        # ⑥ ocr_result 値の判定（is_business_card / has_minimum_info で分岐）
        contact_dict = None
        confidence_map = None

        if not card_meta.get("is_business_card", True):
            ocr_result_value = BusinessCard.OcrResult.NOT_BUSINESS_CARD
        else:
            try:
                contact_dict, confidence_map = normalize_to_contact_dict(ocr_result, 0)
            except Exception as e:
                self.error_messages.append(f"card_index={card_index}: 正規化失敗 ({e})")
                return

            confidence_map = calc_orientation_adjusted_confidence_map(
                contact_dict, confidence_map, orientation
            )

            if not has_minimum_info(contact_dict):
                ocr_result_value = BusinessCard.OcrResult.INSUFFICIENT_INFO
                contact_dict = None
                confidence_map = None
            else:
                ocr_result_value = BusinessCard.OcrResult.BUSINESS_CARD

        # ⑦ stage1 作成済み BC を更新し、Contact / Person を作成（contact_dict=None なら BC のみ）
        try:
            with transaction.atomic():
                business_card.orientation = orientation
                business_card.ocr_result = ocr_result_value
                business_card.save(update_fields=["orientation", "ocr_result", "updated_at"])
                if contact_dict is not None:
                    person = Person.objects.create()
                    contact = Contact.objects.create(
                        business_card=business_card,
                        person=person,
                        **contact_dict,
                    )
                    for field_name, conf in confidence_map.items():
                        if conf in ("low", "medium"):
                            ContactFieldConfidence.objects.create(
                                contact=contact,
                                field_name=field_name,
                                confidence=conf,
                            )
        except Exception as e:
            self.failed_db_count += 1
            self.error_messages.append(
                f"card_index={card_index}: DB保存失敗 ({type(e).__name__}: {e})"
            )

    def _create_card_with_image(self, card_index, image):
        """[性質] 副作用あり（DB 書き込み・ファイル書き込み）/ BC を作成し card_image に image を保存する。

        DB 先・ファイル後：card_image=None で BC 作成 → 一時ファイル保存 → on_commit でリネーム。
        orientation / ocr_result はモデル既定のまま（OCR 由来の確定は stage2 が担う）。
        [出力] BusinessCard or None（作成失敗時 None・error_messages に理由）。
        """
        tmp_abs = None
        try:
            with transaction.atomic():
                business_card = BusinessCard.objects.create(
                    original_image=self.original_image,
                    card_image=None,
                    card_index=card_index,
                )

                # 一時パスに JPEG を書き込む（DB コミット前）。image は正立後画像。
                try:
                    crop_success, tmp_abs, final_rel, crop_error = save_card_image_tmp(
                        image, str(self.original_image.id), card_index,
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
            return business_card

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
            return None

    def _upright_with_osd(self, warped_image, card_index):
        """[性質] 副作用あり（外部プロセス呼び出し・ファイル書き込み）/ OSD で正立化した画像を返す。

        [入力] warped_image: 透視変換済みクロップ（PIL.Image）、card_index: int
        [出力] PIL.Image: 正立化した画像（成功かつ回転要時）。OSD 失敗・回転不要時は warped_image をそのまま。
        [方針] image_to_osd の全例外・timeout は捕捉して normal フォールバック（回転せず元画像）。
               script は使わず rotate 量のみ使用。confidence では分岐しない（成功/例外でのみ分岐）。
               正立画像は別パスへ非破壊保存し、元クロップ（warped_image）は変更しない。
        """
        try:
            rotate_deg = detect_osd_rotation(warped_image)
        except Exception as e:
            logger.info(
                "OSD 失敗→normal フォールバック card_index=%s: %s: %s",
                card_index, type(e).__name__, e,
            )
            return warped_image

        if not rotate_deg % 360:
            return warped_image  # 既に正立（rotate=0）

        upright = apply_upright(warped_image, rotate_deg)
        self._save_upright_image(upright, card_index, rotate_deg)
        return upright

    def _save_upright_image(self, upright_image, card_index, rotate_deg):
        """[性質] 副作用あり（ファイル書き込み）/ OSD 正立化画像を非破壊保存する。

        保存先命名規則：MEDIA_ROOT/corrected/YYYY/MM/DD/<original_image_id>-<card_index>-osd<deg>.jpg
        失敗しても OCR を止めない（補正画像は副次情報のため warning ログのみ）。
        """
        try:
            now = datetime.now()
            rel = (
                f"corrected/{now.strftime('%Y')}/{now.strftime('%m')}/"
                f"{now.strftime('%d')}/{self.original_image.id}-{card_index}-osd{rotate_deg}.jpg"
            )
            abs_path = os.path.join(settings.MEDIA_ROOT, rel)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            upright_image.convert("RGB").save(abs_path, format="JPEG", quality=95)
            logger.debug("osd upright image saved: %s", rel)
        except Exception as e:
            logger.warning(
                "osd upright image save failed for card_index=%s: %s", card_index, e
            )

    def _collect_tesseract_record(self, warped_image, ocr_input_image, card_index):
        """[性質] 副作用あり（外部プロセス Tesseract 呼び出し）/ OSD/OCR 出力を記録 dict で返す。

        [入力] warped_image: 元クロップ（OSD は検出時点の向きを記録するため元クロップに対して取る）、
               ocr_input_image: 正立化後の画像（テキスト品質のため OCR はこちらに対して取る）、card_index
        [出力] dict: {"osd": {...}, "tesseract_ocr": {...}}（各々失敗時は {"error": "..."}）
        [方針] 記録・分析専用。向き判定やクロップ採否のロジックには一切使わない。
               OSD/OCR の全例外を捕捉し、error として残して pipeline を止めない。
        """
        record = {}
        try:
            record["osd"] = detect_osd_info(warped_image)
        except Exception as e:
            record["osd"] = {"error": f"{type(e).__name__}: {e}"}
            logger.info(
                "OSD info 取得失敗（記録のみ・無視）card_index=%s: %s: %s",
                card_index, type(e).__name__, e,
            )
        try:
            record["tesseract_ocr"] = run_tesseract_ocr(ocr_input_image)
        except Exception as e:
            record["tesseract_ocr"] = {"error": f"{type(e).__name__}: {e}"}
            logger.info(
                "Tesseract OCR 取得失敗（記録のみ・無視）card_index=%s: %s: %s",
                card_index, type(e).__name__, e,
            )
        return record

    def _cards_payload(self, n):
        """[性質] 純関数（DB操作なし・副作用なし）/ cards 配列に OSD/OCR 記録を非破壊で合成して返す。

        cards[i] が dict（OCR 由来 card_data）のとき osd / tesseract_ocr サブセクションを足す。
        OCR 実行時（通常）は card_data が None の検出には足さない（従来どおり None のまま）。
        第1段（self._opencv_only=True・OCR前）に限り、card_data が None でも osd / tesseract_ocr
        のみの最小カードを残す（詳細画面で OSD 値を表示するため）。
        元の card_data は変更しない（{**card, **record} で新 dict を作る）。
        """
        cards = []
        for i in range(n):
            card = self._cards_by_index.get(i)
            record = self._tesseract_by_index.get(i)
            if card is not None and record:
                card = {**card, **record}
            elif card is None and record and self._opencv_only:
                # 第1段（OCR前）：card_data が無いので osd / tesseract_ocr のみの最小カードを残す。
                # OCR 形スキーマは満たさないが、_build_raw_json が OCR 前は検証をスキップする。
                card = dict(record)
            cards.append(card)
        return cards

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
