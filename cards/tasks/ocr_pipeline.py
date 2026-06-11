"""BC 1 枚単位の OCR 実行パイプライン（v1.5.0 / cron B から呼ばれる）。

extract_carddata_via_ocr(card_image, ocr_service):
    1 枚の card_image を受け取り、1 回目 OCR → orientation 判定 →
    normal 以外なら回転補正して 2 回目 OCR、まで完結させて結果辞書を返す。
    BC レコードの更新・Contact / Person 生成・status 遷移はここの責務外。

process_cardimage_with_ocr(business_card, ocr_service):
    1 つの BusinessCard を引数に取り、OCR 実行から BC 更新・Contact / Person 生成・
    OriginalImage.status 集計遷移までを完結させる。
    process_ocr（cron B）の本体ループから 1 BC ごとに呼ばれる想定。

【プロンプトキャッシュ】
    OcrService は引数で受け取り、内部で作らない。cron 1 起動につき 1 インスタンスを
    使い回すことで _prompt_cache / _tool_input_schema_cache のファイル I/O を節約する。
"""

import json
import logging

import jsonschema
from django.conf import settings
from django.db import transaction
from PIL import Image, ImageOps

from cards.models import BusinessCard, OriginalImage
from cards.services.has_minimum_info import has_minimum_info
from contacts.services.json_parser import (
    calc_orientation_adjusted_confidence_map,
    create_sns_records_for_contact,
    extract_sns_entries,
    normalize_to_contact_dict,
)
from contacts.services.normalization import validate_phone_against_country
from contacts.models import Contact, ContactFieldConfidence
from persons.models import Person

# 二重検算（仕様書 §2.4）対象の電話系 5 フィールド（v1.6.1 で全て CharField 単一値）。
_PHONE_FIELDS = (
    "mobile_phone",
    "personal_phone",
    "personal_fax",
    "org_phone",
    "org_fax",
)

logger = logging.getLogger(__name__)

# 補正画像上書き時の JPEG 品質。既存 save_card_image (card_cropper.py) と同値。
_JPEG_QUALITY = 95

# card 単位の JSON Schema キャッシュ（プロセス起動中で 1 度だけロード）。
_card_item_schema_cache = None


# orientation 値 → 補正回転の対応。
# orientation の意味（OCR プロンプト §【２．orientationの判定基準】）：
#   normal       … 文字が正立・正常に読める
#   rotate_180   … 文字が上下逆になっている
#   rotate_90_cw … 文字が時計回りに 90° 回転している
#   rotate_90_ccw… 文字が反時計回りに 90° 回転している
#   mirror       … 文字が鏡像になっている
#
# 補正方針：文字が正立に見える状態に戻す。
# Pillow の Image.rotate(angle) は **反時計回り** に angle 度回転する点に注意。
#   rotate_90_cw  は時計回りに 90° 回転して見えているので、反時計回りに 90° で戻す → rotate(90)
#   rotate_90_ccw は反時計回りに 90° 回転して見えているので、時計回りに 90° で戻す → rotate(-90)
#   rotate_180   は 180° で戻す → rotate(180)
#   mirror       は水平反転で戻す → ImageOps.mirror
_ROTATE_ANGLE_TO_CORRECT = {
    BusinessCard.ORIENTATION_90_CW:  90,    # 反時計回りに 90° で正立化
    BusinessCard.ORIENTATION_90_CCW: -90,   # 時計回りに 90° で正立化
    BusinessCard.ORIENTATION_180:    180,
}


def extract_carddata_via_ocr(card_image, ocr_service):
    """1 枚の card_image に対して 1〜2 回 OCR を実行し結果を辞書で返す。

    [性質] 副作用あり（API 呼び出し / DB 操作なし・ファイル書き込みなし）
    [入力]
      card_image  : PIL.Image.Image（粗切り抜き済み・EXIF 補正済み）
      ocr_service : OcrService インスタンス（呼び出し元で 1 cron = 1 インスタンス使い回し前提）
    [出力] dict:
      {
        "raw_json_1": dict,                     # 1回目の生 JSON 全体 {"cards": [...], "api_response": {...}}
        "raw_json_2": dict | None,              # 2回目の生 JSON 全体。skip 時 / 2回目失敗時は None
        "card_image_corrected": PIL.Image.Image | None,
                                                # 2回目走った時のみ補正後画像（成功失敗に関わらず）。normal 時は None
        "orientation_detected": str,            # 1回目で取得した orientation（取れなければ "normal" 扱い）
        "second_attempt_error": str | None,     # 2回目失敗時のエラーメッセージ。成功時は None
      }
    [例外]
      1 回目 OCR の例外は **上位に伝播**（catch しない）。
      呼び出し元が ocr_status=failed に倒す責務を持つ。
      2 回目 OCR の例外は捕捉し、second_attempt_error にメッセージを格納して return する
      （「2 回目失敗時は 1 回目結果を採用」の確定方針）。
    """
    raw_json_1 = ocr_service.run_ocr(card_image)

    orientation = _extract_orientation(raw_json_1)

    if orientation == BusinessCard.ORIENTATION_NORMAL:
        return {
            "raw_json_1": raw_json_1,
            "raw_json_2": None,
            "card_image_corrected": None,
            "orientation_detected": orientation,
            "second_attempt_error": None,
        }

    corrected = _rotate_card_image(card_image, orientation)

    try:
        raw_json_2 = ocr_service.run_ocr(corrected)
        second_attempt_error = None
    except Exception as e:
        raw_json_2 = None
        second_attempt_error = f"{type(e).__name__}: {e}"
        logger.warning(
            "extract_carddata_via_ocr: 2nd attempt failed (orientation=%s): %s",
            orientation, second_attempt_error,
        )

    return {
        "raw_json_1": raw_json_1,
        "raw_json_2": raw_json_2,
        "card_image_corrected": corrected,
        "orientation_detected": orientation,
        "second_attempt_error": second_attempt_error,
    }


def _extract_orientation(raw_json):
    """[性質] 純関数 / raw_json から orientation を防御的に取り出す。

    cards 配列が空 / card_meta 欠落 / orientation キー欠落 / サポート外の値 のいずれかなら
    "normal" を返す（2 回目スキップ扱い）。
    """
    if not isinstance(raw_json, dict):
        return BusinessCard.ORIENTATION_NORMAL
    cards = raw_json.get("cards")
    if not isinstance(cards, list) or not cards:
        return BusinessCard.ORIENTATION_NORMAL
    first = cards[0]
    if not isinstance(first, dict):
        return BusinessCard.ORIENTATION_NORMAL
    card_meta = first.get("card_meta")
    if not isinstance(card_meta, dict):
        return BusinessCard.ORIENTATION_NORMAL
    value = card_meta.get("orientation")
    valid = {v for v, _ in BusinessCard.ORIENTATION_CHOICES}
    if value not in valid:
        return BusinessCard.ORIENTATION_NORMAL
    return value


def _rotate_card_image(image, orientation):
    """[性質] 純関数 / orientation 値に応じて image を「文字が正立になるよう」補正する。

    [入力]
      image       : PIL.Image.Image
      orientation : BusinessCard.ORIENTATION_CHOICES の値文字列
    [出力] 補正後の PIL.Image.Image（新規オブジェクト）
    [備考]
      normal が渡された場合は image をそのまま返す（呼び出し側はそもそも normal で呼ばない想定）。
      未知の orientation 値が渡された場合も image をそのまま返す（防御）。
    """
    if orientation == BusinessCard.ORIENTATION_MIRROR:
        return ImageOps.mirror(image)
    angle = _ROTATE_ANGLE_TO_CORRECT.get(orientation)
    if angle is None:
        return image
    return image.rotate(angle, expand=True)


def process_cardimage_with_ocr(business_card, ocr_service):
    """1 つの BusinessCard を OCR して BC 更新・Contact 生成・OriginalImage 集計遷移までを行う。

    [性質] 副作用あり（複合処理：API 呼び出し / DB 書込 / ファイル書込）
    [入力]
      business_card : BusinessCard インスタンス（ocr_status=pending、card_image 有 or None）
      ocr_service   : OcrService インスタンス（cron 1 起動 = 1 インスタンス使い回し前提）
    [出力] None（すべての副作用は DB と FS に集約）
    [例外]
      呼び出し時点で business_card.ocr_status が pending または processing でなければ ValueError。
      processing を許容するのは CAS（process_ocr の _claim_lock 等）経由で呼ばれるため。
      それ以外の例外は外に漏らさず BC.error_message に集約する。
    [遷移]
      正常 → BC.ocr_status=done / ocr_result ∈ {business_card, not_business_card, insufficient_info}
      失敗 → BC.ocr_status=failed / ocr_result=ocr_failed
      全 BC が done/failed になった OriginalImage は extracted（全失敗なら failed）に遷移する。
    """
    _CALLABLE_STATUSES = (
        BusinessCard.OcrStatus.PENDING,
        BusinessCard.OcrStatus.PROCESSING,
    )
    if business_card.ocr_status not in _CALLABLE_STATUSES:
        raise ValueError(
            f"process_cardimage_with_ocr called with ocr_status="
            f"{business_card.ocr_status}, expected pending or processing. "
            f"BC {business_card.id}"
        )

    original_image_id = business_card.original_image_id

    # ① card_image なしの早期分岐
    if not business_card.card_image:
        _finalize_failed(
            business_card,
            ocr_result=BusinessCard.OcrResult.OCR_FAILED,
            error_text="card_image=None のため OCR 実行不可",
        )
        _update_original_image_status(original_image_id)
        return

    # ② 画像読み込み
    try:
        with Image.open(business_card.card_image.path) as f:
            card_image = f.copy()
    except Exception as e:
        _finalize_failed(
            business_card,
            ocr_result=BusinessCard.OcrResult.OCR_FAILED,
            error_text=f"card_image 読み込み失敗 ({type(e).__name__}: {e})",
        )
        _update_original_image_status(original_image_id)
        return

    # ③ OCR 実行（1〜2 回）
    try:
        result = extract_carddata_via_ocr(card_image, ocr_service)
    except Exception as e:
        logger.warning(
            "process_cardimage_with_ocr: OCR failed for BC %s: %s: %s",
            business_card.id, type(e).__name__, e,
        )
        _finalize_failed(
            business_card,
            ocr_result=BusinessCard.OcrResult.OCR_FAILED,
            error_text=f"OCR API 失敗 ({type(e).__name__}: {e})",
        )
        _update_original_image_status(original_image_id)
        return

    raw_json_1 = result["raw_json_1"]
    raw_json_2 = result["raw_json_2"]
    corrected = result["card_image_corrected"]
    orientation_detected = result["orientation_detected"]
    second_attempt_error = result["second_attempt_error"]

    # 採用 raw_json：raw_json_2 があればそちら、なければ raw_json_1
    adopted_raw_json = raw_json_2 if raw_json_2 is not None else raw_json_1

    # ⑤ 補正画像で card_image を上書き（同パス上書き、トランザクション外）
    error_msgs = []
    if corrected is not None:
        try:
            _overwrite_card_image(business_card, corrected)
        except Exception as e:
            error_msgs.append(
                f"補正画像保存失敗 ({type(e).__name__}: {e})"
            )

    # ⑥ 2 回目失敗時のメッセージ
    if second_attempt_error is not None:
        error_msgs.append(f"2回目 OCR 失敗: {second_attempt_error}")

    # ⑦〜⑨ ocr_result 判定 + BC 更新 + Contact 作成（判定は atomic 外・生成は BC 単位 atomic 内）
    _judge_and_persist_ocr_result(
        business_card,
        adopted_raw_json,
        orientation_detected,
        raw_json_1,
        raw_json_2,
        error_msgs,
    )

    # ⑩ OriginalImage 集計遷移（別 atomic + select_for_update）
    _update_original_image_status(original_image_id)


def _judge_and_persist_ocr_result(
    business_card,
    adopted_raw_json,
    orientation_detected,
    raw_json_1,
    raw_json_2,
    error_msgs,
):
    """採用 raw_json を判定し BC 確定 + Contact 系生成までを行う。

    [性質] 副作用あり（DB 書込）/ 判定（_determine_ocr_result）は atomic 外で行い、
      BC の save と Person / Contact / ContactFieldConfidence / ContactSns の生成を
      BC 単位の transaction.atomic() 内で行う（現状の atomic 境界を不変のまま括り出したもの）。
    [入力]
      business_card        : BusinessCard インスタンス
      adopted_raw_json     : 採用 raw_json（raw_json_2 優先）
      orientation_detected : 1 回目で検出した orientation（BC.orientation に保存）
      raw_json_1           : 1 回目 OCR の生 JSON 全体（BC.raw_json_1 に保存）
      raw_json_2           : 2 回目 OCR の生 JSON 全体 or None（BC.raw_json_2 に保存）
      error_msgs           : 前段で積み上げた警告/エラーの list（判定エラーを追記し改行連結で保存）
    [出力] None（副作用は DB に集約）
    """
    # ⑦ ocr_result 判定
    ocr_result_value, contact_dict, confidence_map, judge_errors = (
        _determine_ocr_result(adopted_raw_json, orientation_detected)
    )
    error_msgs.extend(judge_errors)

    # ⑧〜⑨ BC 更新 + Contact 作成（BC 単位 atomic）
    with transaction.atomic():
        business_card.raw_json_1 = raw_json_1
        business_card.raw_json_2 = raw_json_2
        business_card.orientation = orientation_detected
        business_card.ocr_result = ocr_result_value
        business_card.ocr_status = BusinessCard.OcrStatus.DONE
        business_card.error_message = "\n".join(error_msgs)
        business_card.claimed_at = None
        business_card.save()

        if contact_dict is not None:
            person = Person.objects.create()
            contact = Contact.objects.create(
                business_card=business_card,
                person=person,
                status=Contact.Status.PRIMARY,
                **contact_dict,
            )
            # G-5: Person.primary_contact を A 側メソッド経由で確定
            # （Contact.status='primary' との二重管理を整合させる責務はこのメソッド）
            person.set_primary_contact(contact)
            # CFC 作成は create_for_contact に一本化（bulk_create、§1.7）。
            # salutation_name は Contact.save() が補完時に CFC を作る責務のため除外する
            # （二重作成回避、§1.9）。create_for_contact は low/mid のみ INSERT する。
            cfc_map = {
                field_name: conf
                for field_name, conf in (confidence_map or {}).items()
                if field_name != "salutation_name"
            }
            ContactFieldConfidence.create_for_contact(contact, cfc_map)

            # SNS は別テーブル（ContactSns）へ分配（純関数で抽出 → 副作用関数で作成）。
            # adopted_raw_json は採用 raw_json（raw_json_2 優先）、card_index=0。
            sns_entries = extract_sns_entries(adopted_raw_json, 0)
            create_sns_records_for_contact(contact, sns_entries)


def _finalize_failed(business_card, *, ocr_result, error_text):
    """[性質] 副作用あり（DB 書込）/ BC を ocr_status=failed で確定させる。

    Contact / Person は作成しない。raw_json は None のまま。
    """
    with transaction.atomic():
        business_card.ocr_status = BusinessCard.OcrStatus.FAILED
        business_card.ocr_result = ocr_result
        business_card.error_message = error_text
        business_card.claimed_at = None
        business_card.save(
            update_fields=[
                "ocr_status", "ocr_result", "error_message",
                "claimed_at", "updated_at",
            ]
        )


def _overwrite_card_image(business_card, pil_image):
    """[性質] 副作用あり（ファイル書込）/ BC.card_image の最終パスに補正画像を上書き保存。

    同パスへの上書きなので BC.card_image のフィールド値は不変。トランザクション外で実行する。
    """
    abs_path = business_card.card_image.path
    img = pil_image if pil_image.mode == "RGB" else pil_image.convert("RGB")
    img.save(abs_path, format="JPEG", quality=_JPEG_QUALITY)


def _determine_ocr_result(adopted_raw_json, orientation_for_field_value):
    """[性質] 純関数（DB 操作なし）/ 採用 raw_json から ocr_result 判定と Contact 用辞書を構築する。

    [入力]
      adopted_raw_json            : 採用された raw_json（{"cards":[...], "api_response":{...}}）
      orientation_for_field_value : BC.orientation に保存される値（1 回目で検出した orientation）。
                                    confidence 補正には「採用 raw_json の orientation」を別途使う。
    [出力] (ocr_result, contact_dict, confidence_map, errors)
      ocr_result      : BusinessCard.OcrResult の値
      contact_dict    : Contact 作成用 dict。Contact を作らないケースは None
      confidence_map  : ContactFieldConfidence 用 dict。Contact を作らないケースは None
      errors          : 判定中に発生した警告/エラーのテキスト list
    [判定経路]
      スキーマ検証失敗            → INSUFFICIENT_INFO
      normalize_to_contact_dict 失敗 → INSUFFICIENT_INFO
      is_business_card = False    → NOT_BUSINESS_CARD
      has_minimum_info = False    → INSUFFICIENT_INFO
      上記いずれにも該当しない    → BUSINESS_CARD（Contact / Person 作成対象）
    """
    errors = []
    cards = (adopted_raw_json or {}).get("cards") or []
    if not cards or not isinstance(cards[0], dict):
        errors.append("採用 raw_json の cards[0] が不正")
        return (
            BusinessCard.OcrResult.INSUFFICIENT_INFO, None, None, errors,
        )

    card_data = cards[0]

    # スキーマ検証（card 単位）
    try:
        jsonschema.validate(instance=card_data, schema=_get_card_item_schema())
    except jsonschema.ValidationError as e:
        errors.append(f"スキーマ検証失敗: {e.message}")
        return (
            BusinessCard.OcrResult.INSUFFICIENT_INFO, None, None, errors,
        )

    card_meta = card_data.get("card_meta") or {}
    if not card_meta.get("is_business_card", True):
        return BusinessCard.OcrResult.NOT_BUSINESS_CARD, None, None, errors

    # confidence 補正は採用 raw_json の orientation を使う（補正画像で 2 回目 OCR したなら normal）。
    orientation_for_confidence = card_meta.get(
        "orientation", BusinessCard.ORIENTATION_NORMAL
    )

    try:
        contact_dict, confidence_map = normalize_to_contact_dict(adopted_raw_json, 0)
    except Exception as e:
        errors.append(f"正規化失敗: {e}")
        return (
            BusinessCard.OcrResult.INSUFFICIENT_INFO, None, None, errors,
        )

    confidence_map = calc_orientation_adjusted_confidence_map(
        contact_dict, confidence_map, orientation_for_confidence
    )

    # 二重検算（仕様書 §2.4）：電話系 5 フィールドの E.164 国番号と country の不一致を検出し、
    # 不一致なら当該フィールドの confidence を low に補正する（フィールド別判定、値は変えない）。
    # 補正理由はサーバーログのみ（check_name_consistency と同方針、DB に理由フィールドは作らない）。
    country = contact_dict.get("country", "")
    for phone_field in _PHONE_FIELDS:
        phone_value = contact_dict.get(phone_field, "")
        if not validate_phone_against_country(phone_value, country):
            before = confidence_map.get(phone_field, "high")
            confidence_map[phone_field] = "low"
            logger.warning(
                "validate_phone_against_country: '%s' を %s→low に補正"
                "（理由: country=%s と電話 '%s' の国番号が不一致）",
                phone_field,
                before,
                country,
                phone_value,
            )

    if not has_minimum_info(contact_dict):
        return (
            BusinessCard.OcrResult.INSUFFICIENT_INFO, None, None, errors,
        )

    return BusinessCard.OcrResult.BUSINESS_CARD, contact_dict, confidence_map, errors


def _update_original_image_status(original_image_id):
    """[性質] 副作用あり（DB 書込）/ OriginalImage.status を全 BC の ocr_status で集計遷移させる。

    select_for_update で OriginalImage を行ロックし、同一 OriginalImage 内 BC の並列処理時のレースを排除する。
    全 BC が完了していなければ何もしない（cards_extracted のまま）。
    全 BC が failed なら status=failed、それ以外（成功 1 件でもあり、または部分失敗）なら status=extracted。
    """
    try:
        with transaction.atomic():
            original = OriginalImage.objects.select_for_update().get(id=original_image_id)
            bc_statuses = list(
                BusinessCard.objects.filter(original_image=original)
                .values_list("ocr_status", flat=True)
            )
            if not bc_statuses:
                return

            done = BusinessCard.OcrStatus.DONE
            failed = BusinessCard.OcrStatus.FAILED
            terminal = {done, failed}

            if not all(s in terminal for s in bc_statuses):
                return  # まだ pending / processing が残っている

            if all(s == failed for s in bc_statuses):
                new_status = OriginalImage.STATUS_FAILED
            else:
                new_status = OriginalImage.STATUS_EXTRACTED

            if original.status != new_status:
                original.status = new_status
                original.save(update_fields=["status", "updated_at"])
    except OriginalImage.DoesNotExist:
        logger.warning(
            "OriginalImage %s disappeared during status aggregation",
            original_image_id,
        )


def _get_card_item_schema():
    """[性質] 副作用あり（ファイル読み込み・キャッシュ）/ card 単位の JSON Schema を返す。"""
    global _card_item_schema_cache
    if _card_item_schema_cache is None:
        schema_path = (
            settings.BASE_DIR
            / "docs"
            / "json_schema"
            / "v1.6.1"
            / "combined_response.json"
        )
        with open(schema_path, encoding="utf-8") as f:
            full = json.load(f)
        _card_item_schema_cache = {
            "$schema": full.get("$schema", ""),
            "$defs": full.get("$defs", {}),
            **full["properties"]["cards"]["items"],
        }
    return _card_item_schema_cache
