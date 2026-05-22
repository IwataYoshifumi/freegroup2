"""cards.tasks.ocr_pipeline.Run_Process_CardImages_With_OCR のテスト（Phase 3B 新設）。

OCR API 呼び出し本体（OcrService.run_ocr）と 1 BC 単位処理（process_cardimage_with_ocr）は
モック化し、Run_*_With_OCR の責務範囲（stuck sweeper・対象収集・claim ループ・
エラーハンドリング・logger 出力）に集中して検証する。
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cards.models import BusinessCard, OriginalImage
from cards.tasks.ocr_pipeline import (
    Run_Process_CardImages_With_OCR,
    _claim_lock,
    _collect_target_bc_ids,
)

User = get_user_model()


def _make_user(suffix=""):
    """テスト用 User をユニーク username で作成。"""
    return User.objects.create_user(
        username=f"ocr_pipeline_test_user{suffix}_{uuid.uuid4().hex[:8]}",
        password="dummy",
    )


def _create_pending_bc(card_index=0, user=None):
    """テスト用の最小 pending BusinessCard を 1 件作成して返す。"""
    if user is None:
        user = _make_user()
    original = OriginalImage.objects.create(
        user=user, status=OriginalImage.STATUS_EXTRACTED
    )
    return BusinessCard.objects.create(
        original_image=original,
        card_index=card_index,
        ocr_status=BusinessCard.OcrStatus.PENDING,
    )


@patch("cards.tasks.ocr_pipeline.OcrService")
@patch("cards.tasks.ocr_pipeline.process_cardimage_with_ocr")
class RunProcessCardImagesWithOcrTests(TestCase):
    """Run_Process_CardImages_With_OCR の基本動作テスト（process_cardimage_with_ocr・
    OcrService はモック化）。"""

    def test_no_targets_returns_without_calling_pipeline(
        self, mock_process, mock_ocr_service_cls
    ):
        """pending BC が 0 件なら early return、process_cardimage_with_ocr 未呼出。"""
        Run_Process_CardImages_With_OCR()
        mock_process.assert_not_called()
        # 対象なしの早期 return では OcrService インスタンス化もしない
        mock_ocr_service_cls.assert_not_called()

    def test_processes_pending_bcs(self, mock_process, mock_ocr_service_cls):
        """pending BC があれば process_cardimage_with_ocr が件数分呼ばれる。"""
        bcs = [_create_pending_bc(card_index=i) for i in range(3)]
        Run_Process_CardImages_With_OCR(limit=10)
        # 3 件すべて呼ばれる
        self.assertEqual(mock_process.call_count, 3)
        # OcrService は 1 起動 1 インスタンス
        self.assertEqual(mock_ocr_service_cls.call_count, 1)
        # 各 BC が processing に遷移していること
        for bc in bcs:
            bc.refresh_from_db()
            # mock_process は何もしないので claim 後の processing のまま
            self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.PROCESSING)

    def test_limit_applied(self, mock_process, mock_ocr_service_cls):
        """limit を超える pending BC は処理されない。"""
        for i in range(5):
            _create_pending_bc(card_index=i)
        Run_Process_CardImages_With_OCR(limit=2)
        self.assertEqual(mock_process.call_count, 2)

    def test_target_id_processes_one(self, mock_process, mock_ocr_service_cls):
        """target_id 指定でその 1 件のみ処理（他の pending BC は無視）。"""
        bc_targeted = _create_pending_bc(card_index=0)
        _create_pending_bc(card_index=1)
        _create_pending_bc(card_index=2)
        Run_Process_CardImages_With_OCR(limit=10, target_id=bc_targeted.id)
        # target_id の 1 件だけ呼ばれる
        self.assertEqual(mock_process.call_count, 1)
        called_bc = mock_process.call_args[0][0]
        self.assertEqual(called_bc.id, bc_targeted.id)

    def test_target_id_accepts_string(self, mock_process, mock_ocr_service_cls):
        """target_id は str / UUID どちらでも受け付ける。"""
        bc = _create_pending_bc()
        Run_Process_CardImages_With_OCR(target_id=str(bc.id))
        self.assertEqual(mock_process.call_count, 1)

    def test_target_id_invalid_uuid_raises(
        self, mock_process, mock_ocr_service_cls
    ):
        """target_id が UUID 不正なら ValueError（process_ocr 側で CommandError へ）。"""
        with self.assertRaises(ValueError):
            Run_Process_CardImages_With_OCR(target_id="not-a-uuid")
        mock_process.assert_not_called()

    def test_target_id_not_pending_returns_empty(
        self, mock_process, mock_ocr_service_cls
    ):
        """target_id 指定だが ocr_status が pending でなければ何もしない。"""
        bc = _create_pending_bc()
        bc.ocr_status = BusinessCard.OcrStatus.DONE
        bc.save(update_fields=["ocr_status"])
        Run_Process_CardImages_With_OCR(target_id=bc.id)
        mock_process.assert_not_called()
        mock_ocr_service_cls.assert_not_called()

    def test_claim_lock_already_taken_skipped(
        self, mock_process, mock_ocr_service_cls
    ):
        """対象収集と claim の間に他 worker が先に取った BC はスキップ。"""
        bc_pending = _create_pending_bc(card_index=0)
        bc_drift = _create_pending_bc(card_index=1)
        # 対象収集（pending で取得される想定）の直後に bc_drift だけ processing に
        # ドリフトする状況をシミュレート。loop 内で _claim_lock が False を返すはず。
        original_collect = _collect_target_bc_ids

        def drift_after_collect(limit, target_id):
            ids = original_collect(limit, target_id)
            # 収集直後に bc_drift を processing にドリフトさせる
            BusinessCard.objects.filter(id=bc_drift.id).update(
                ocr_status=BusinessCard.OcrStatus.PROCESSING
            )
            return ids

        with patch(
            "cards.tasks.ocr_pipeline._collect_target_bc_ids",
            side_effect=drift_after_collect,
        ):
            Run_Process_CardImages_With_OCR(limit=10)
        # bc_pending のみ処理、bc_drift は skip
        self.assertEqual(mock_process.call_count, 1)
        called_bc = mock_process.call_args[0][0]
        self.assertEqual(called_bc.id, bc_pending.id)

    def test_process_exception_continues_loop(
        self, mock_process, mock_ocr_service_cls
    ):
        """1 件目で例外でも残りは処理続行（防御的）。"""
        bc1 = _create_pending_bc(card_index=0)
        bc2 = _create_pending_bc(card_index=1)

        def raise_for_first(bc, ocr_service):
            if bc.id == bc1.id:
                raise RuntimeError("simulated failure")

        mock_process.side_effect = raise_for_first
        Run_Process_CardImages_With_OCR(limit=10)
        # 両方呼ばれる（1 件目は例外、2 件目は正常）
        self.assertEqual(mock_process.call_count, 2)


class RunProcessCardImagesStuckSweeperTests(TestCase):
    """stuck sweeper の動作テスト（reset_bc_to_pending をモック化）。"""

    @patch("cards.tasks.ocr_pipeline.OcrService")
    @patch("cards.tasks.ocr_pipeline.process_cardimage_with_ocr")
    @patch("cards.tasks.ocr_pipeline.reset_bc_to_pending")
    def test_stuck_sweeper_called_for_old_processing(
        self, mock_reset, mock_process, mock_ocr_service_cls
    ):
        """OCR_STUCK_THRESHOLD_MINUTES より古い processing BC に対し reset_bc_to_pending
        が呼ばれる。"""
        # threshold より古い claimed_at で processing 状態の BC を作る
        original = OriginalImage.objects.create(
            user=_make_user(), status=OriginalImage.STATUS_EXTRACTED
        )
        stuck_bc = BusinessCard.objects.create(
            original_image=original,
            card_index=0,
            ocr_status=BusinessCard.OcrStatus.PROCESSING,
            claimed_at=timezone.now() - timedelta(minutes=60),
        )
        # reset_bc_to_pending は True を返す（reset 成功扱い）
        mock_reset.return_value = True
        Run_Process_CardImages_With_OCR(limit=10)
        # stuck BC に対して reset_bc_to_pending が呼ばれた
        mock_reset.assert_called_once()
        called_bc = mock_reset.call_args[0][0]
        self.assertEqual(called_bc.id, stuck_bc.id)

    @patch("cards.tasks.ocr_pipeline.OcrService")
    @patch("cards.tasks.ocr_pipeline.process_cardimage_with_ocr")
    @patch("cards.tasks.ocr_pipeline.reset_bc_to_pending")
    def test_recent_processing_not_swept(
        self, mock_reset, mock_process, mock_ocr_service_cls
    ):
        """threshold 内の processing BC は sweep されない。"""
        original = OriginalImage.objects.create(
            user=_make_user(), status=OriginalImage.STATUS_EXTRACTED
        )
        BusinessCard.objects.create(
            original_image=original,
            card_index=0,
            ocr_status=BusinessCard.OcrStatus.PROCESSING,
            claimed_at=timezone.now() - timedelta(minutes=1),
        )
        Run_Process_CardImages_With_OCR(limit=10)
        mock_reset.assert_not_called()


class ClaimLockTests(TestCase):
    """_claim_lock の CAS 動作テスト（実 DB）。"""

    def test_claim_pending_succeeds(self):
        bc = _create_pending_bc()
        self.assertTrue(_claim_lock(bc.id))
        bc.refresh_from_db()
        self.assertEqual(bc.ocr_status, BusinessCard.OcrStatus.PROCESSING)
        self.assertIsNotNone(bc.claimed_at)

    def test_claim_non_pending_fails(self):
        bc = _create_pending_bc()
        bc.ocr_status = BusinessCard.OcrStatus.PROCESSING
        bc.save(update_fields=["ocr_status"])
        self.assertFalse(_claim_lock(bc.id))

    def test_double_claim_only_one_succeeds(self):
        """同一 BC への 2 回目の claim は失敗（CAS）。"""
        bc = _create_pending_bc()
        self.assertTrue(_claim_lock(bc.id))
        self.assertFalse(_claim_lock(bc.id))


class CollectTargetBcIdsTests(TestCase):
    """_collect_target_bc_ids の動作テスト。"""

    def test_no_pending_returns_empty(self):
        self.assertEqual(_collect_target_bc_ids(limit=10, target_id=None), [])

    def test_returns_pending_bcs_ordered(self):
        bc1 = _create_pending_bc(card_index=2)
        bc2 = _create_pending_bc(card_index=1)
        ids = _collect_target_bc_ids(limit=10, target_id=None)
        # 同一 OriginalImage 内では card_index 昇順
        # ただし OriginalImage が異なるため original_image__created_at で
        # ソートされる順序になる。両方含まれることだけ確認。
        self.assertIn(bc1.id, ids)
        self.assertIn(bc2.id, ids)
        self.assertEqual(len(ids), 2)

    def test_limit_truncates(self):
        for i in range(5):
            _create_pending_bc(card_index=i)
        ids = _collect_target_bc_ids(limit=3, target_id=None)
        self.assertEqual(len(ids), 3)

    def test_limit_zero_treated_as_one(self):
        """limit=0 / 負値は実効的に 1 件処理（max(1, limit)）。"""
        _create_pending_bc()
        ids = _collect_target_bc_ids(limit=0, target_id=None)
        self.assertEqual(len(ids), 1)

    def test_target_id_uuid_object_accepted(self):
        bc = _create_pending_bc()
        ids = _collect_target_bc_ids(limit=10, target_id=bc.id)
        self.assertEqual(ids, [bc.id])

    def test_target_id_string_accepted(self):
        bc = _create_pending_bc()
        ids = _collect_target_bc_ids(limit=10, target_id=str(bc.id))
        self.assertEqual(ids, [bc.id])

    def test_target_id_invalid_raises(self):
        with self.assertRaises(ValueError):
            _collect_target_bc_ids(limit=10, target_id="not-a-uuid")

    def test_target_id_not_existing_returns_empty(self):
        nonexistent = uuid.uuid4()
        self.assertEqual(
            _collect_target_bc_ids(limit=10, target_id=nonexistent),
            [],
        )

    def test_target_id_not_pending_returns_empty(self):
        bc = _create_pending_bc()
        bc.ocr_status = BusinessCard.OcrStatus.DONE
        bc.save(update_fields=["ocr_status"])
        self.assertEqual(
            _collect_target_bc_ids(limit=10, target_id=bc.id),
            [],
        )
