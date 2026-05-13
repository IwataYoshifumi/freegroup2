"""OCR 段階の差し戻し / リカバリ用ユーティリティ（v1.5.0）。

stuck sweeper（process_ocr）と手動差し戻し（retry_failed_ocr --ocr）の両方から
呼ばれる共通ヘルパーを集約する。BC を pending 状態に戻し、関連 Contact /
孤立 Person を削除する点は共通で、ソース状態（processing or failed）だけが異なる。
"""

import logging

from django.db import transaction

from cards.models import BusinessCard, OriginalImage
from contacts.models import Contact
from persons.models import Person

logger = logging.getLogger(__name__)


def reset_bc_to_pending(business_card, *, expected_statuses):
    """[性質] 副作用あり（DB 書込）/ BC を pending に戻し、関連 Contact / 孤立 Person を削除する。

    [入力]
      business_card     : BusinessCard インスタンス
      expected_statuses : 期待される現在の ocr_status の集合(tuple/list)。
                          現在値がここに含まれなければ skip して False を返す。
                          通常は ("processing",) または ("failed",) を渡す。
    [出力] bool
      True  : 差し戻し成功（BC リセット + 関連 Contact 削除 + OriginalImage.status 再評価まで完了）
      False : 状態 drift で skip、または atomic 内で例外（logger に記録）
    [挙動]
      1. select_for_update で BC を行ロック取得し、ocr_status が expected_statuses 内か再確認
      2. 関連 Contact 削除（CASCADE で ContactFieldConfidence も削除）
      3. 削除した Contact が参照していた Person のうち、他から参照されていないものを削除
      4. BC を pending 化（claimed_at=None / raw_json_1=None / raw_json_2=None /
         error_message="" / ocr_result=None / orientation=normal）
      5. card_image は **不変**（OpenCV 結果は維持）
      6. 成功時 recalc_original_image_status_to_cards_extracted を続けて呼ぶ
    """
    try:
        with transaction.atomic():
            refreshed = BusinessCard.objects.select_for_update().get(id=business_card.id)
            if refreshed.ocr_status not in expected_statuses:
                logger.info(
                    "reset_bc_to_pending skipped (ocr_status=%s not in %s): BC %s",
                    refreshed.ocr_status, tuple(expected_statuses), business_card.id,
                )
                return False

            person_ids_to_check = set(
                Contact.objects.filter(business_card=refreshed)
                .values_list("person_id", flat=True)
            )
            Contact.objects.filter(business_card=refreshed).delete()

            deleted_persons = 0
            for pid in person_ids_to_check:
                if pid is None:
                    continue
                if not Contact.objects.filter(person_id=pid).exists():
                    Person.objects.filter(id=pid).delete()
                    deleted_persons += 1
            if deleted_persons > 0:
                logger.info(
                    "reset_bc_to_pending: orphan Person deleted %d records (BC %s)",
                    deleted_persons, business_card.id,
                )

            refreshed.ocr_status = BusinessCard.OcrStatus.PENDING
            refreshed.claimed_at = None
            refreshed.raw_json_1 = None
            refreshed.raw_json_2 = None
            refreshed.error_message = ""
            refreshed.ocr_result = None
            refreshed.orientation = BusinessCard.ORIENTATION_NORMAL
            refreshed.save(
                update_fields=[
                    "ocr_status", "claimed_at", "raw_json_1", "raw_json_2",
                    "error_message", "ocr_result", "orientation", "updated_at",
                ]
            )
    except Exception as e:
        logger.error(
            "reset_bc_to_pending failed for BC %s: %s", business_card.id, e,
        )
        return False

    recalc_original_image_status_to_cards_extracted(business_card.original_image_id)
    return True


def recalc_original_image_status_to_cards_extracted(original_image_id):
    """[性質] 副作用あり（DB 書込）/ OriginalImage.status を必要なら cards_extracted に戻す。

    [入力] original_image_id: 対象 OriginalImage の id
    [出力] None
    [挙動]
      - status が extracted / failed のときのみ評価対象
      - pending / processing の BC が 1 件でもあれば status を cards_extracted に書き戻す
      - select_for_update で行ロック、同一 OriginalImage の並列差し戻し時のレースを排除
      - 例外時は logger.error で握りつぶし（呼び出し元の処理は続行）
    """
    try:
        with transaction.atomic():
            original = OriginalImage.objects.select_for_update().get(id=original_image_id)
            if original.status not in (
                OriginalImage.STATUS_EXTRACTED, OriginalImage.STATUS_FAILED
            ):
                return
            unfinished = BusinessCard.objects.filter(
                original_image=original,
                ocr_status__in=(
                    BusinessCard.OcrStatus.PENDING,
                    BusinessCard.OcrStatus.PROCESSING,
                ),
            ).exists()
            if not unfinished:
                return
            original.status = OriginalImage.STATUS_CARDS_EXTRACTED
            original.save(update_fields=["status", "updated_at"])
    except OriginalImage.DoesNotExist:
        logger.warning(
            "OriginalImage %s disappeared during status recalc", original_image_id,
        )
    except Exception as e:
        logger.error(
            "recalc_original_image_status_to_cards_extracted failed for %s: %s",
            original_image_id, e,
        )
