"""名刺向き正位化（cards/orientation）テスト（対象を絞った範囲）。

- 符号規約（ゴールデン18枚）：0/90/180/270 で detect ラベル一致＋transpose 正位化で再判定が 0。
- 前処理固定値：preprocess_pil / preprocess_path の等価・形状・移植値の安定。
- 閾値分岐：score<閾値=据え置き / >=閾値=回転 / label0=straight。
- 例外フェイルセーフ：判定器例外で (0,0.0,failed)→据え置き。
- ファクトリ：ORIENTATION_BACKEND 切替・未知値で ValueError。
- パイプライン結線：既定 PP-LCNet で BC に補正状態が入り osd_json=None / none で null。
- 手動回転：_rotate_and_overwrite_card_image が新2フィールドをクリア。
"""

import os
import re
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from PIL import Image

from cards.models import BusinessCard, OriginalImage
from cards.orientation import correct as correct_mod
from cards.orientation.backends import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PASSTHROUGH,
    NoneBackend,
    OrientationBackend,
    OrientationResult,
    OsdBackend,
    PPLCNetBackend,
)
from cards.orientation.correct import resolve_orientation
from cards.orientation.factory import get_orientation_backend
from cards.orientation.preprocess import preprocess_path, preprocess_pil

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent.parent / "test_fixtures" / "orientation"
_LABEL_RE = re.compile(r"label(\d+)\.jpg$")


class GoldenSignConventionTests(TestCase):
    """ゴールデン18枚で符号規約をロック（実証済みラベル／状態を継承）。"""

    def _fixtures(self):
        return sorted(FIXTURES.glob("*.jpg"))

    def test_18_fixtures_present(self):
        self.assertEqual(len(self._fixtures()), 18, "ゴールデン画像は18枚")

    def test_detect_label_matches_expected(self):
        backend = PPLCNetBackend()
        for f in self._fixtures():
            expected = int(_LABEL_RE.search(f.name).group(1))
            res = backend.detect(Image.open(f))
            self.assertEqual(res.label, expected, f"{f.name}: label 期待{expected}≠{res.label}")
            self.assertEqual(res.status, STATUS_OK)
            self.assertGreaterEqual(res.score, 0.7, f"{f.name}: score {res.score}")

    def test_transpose_uprights_then_redetect_is_zero(self):
        """符号規約の本丸：判定どおり正位化した画像を再判定すると label=0（正立）になる。"""
        backend = PPLCNetBackend()
        for f in self._fixtures():
            img = Image.open(f)
            res = backend.detect(img)
            final, correction, _ = resolve_orientation(img, res, 0.7)
            redetect = backend.detect(final)
            self.assertEqual(redetect.label, 0, f"{f.name}: 正位化後の再判定が0でない")
            expected = int(_LABEL_RE.search(f.name).group(1))
            self.assertEqual(
                correction, "straight" if expected == 0 else "rotated", f.name,
            )


class PreprocessTests(TestCase):
    def test_pil_and_path_equivalent_and_shape(self):
        f = sorted(FIXTURES.glob("*.jpg"))[0]
        from_pil = preprocess_pil(Image.open(f))
        from_path = preprocess_path(str(f))
        self.assertEqual(from_pil.shape, (1, 3, 224, 224))
        self.assertEqual(from_pil.dtype.name, "float32")
        # 同一画像なら PIL 経路と path 経路は完全一致（移植コアが同じ・JPEGを再デコードしない）。
        self.assertTrue((from_pil == from_path).all(), "preprocess_pil と preprocess_path が不一致")


class ThresholdBranchTests(TestCase):
    """resolve_orientation の閾値分岐（純関数）。"""

    def setUp(self):
        self.img = Image.new("RGB", (300, 180), "white")  # w=300,h=180

    def test_score_below_threshold_kept_no_rotation(self):
        final, corr, score = resolve_orientation(
            self.img, OrientationResult(90, 0.5, STATUS_OK), 0.7,
        )
        self.assertEqual(corr, "kept")
        self.assertEqual(final.size, self.img.size, "据え置きは無回転")
        self.assertEqual(score, 0.5)

    def test_label0_low_score_is_kept(self):
        # label=0 でも低スコアは kept（label 不問）。
        _, corr, _ = resolve_orientation(self.img, OrientationResult(0, 0.4, STATUS_OK), 0.7)
        self.assertEqual(corr, "kept")

    def test_score_at_threshold_rotates(self):
        final, corr, _ = resolve_orientation(
            self.img, OrientationResult(90, 0.7, STATUS_OK), 0.7,
        )
        self.assertEqual(corr, "rotated")
        self.assertEqual(final.size, (180, 300), "ROTATE_90 で寸法が入れ替わる")

    def test_label0_high_score_straight(self):
        final, corr, _ = resolve_orientation(
            self.img, OrientationResult(0, 0.95, STATUS_OK), 0.7,
        )
        self.assertEqual(corr, "straight")
        self.assertEqual(final.size, self.img.size)

    def test_passthrough_returns_none_fields(self):
        final, corr, score = resolve_orientation(
            self.img, OrientationResult(0, 0.0, STATUS_PASSTHROUGH), 0.7,
        )
        self.assertIs(final, self.img)
        self.assertIsNone(corr)
        self.assertIsNone(score)


class FailSafeTests(TestCase):
    def test_base_detect_swallows_exception(self):
        class Boom(OrientationBackend):
            def _detect(self, image):
                raise RuntimeError("broken")

        res = Boom().detect(Image.new("RGB", (10, 10)))
        self.assertEqual((res.label, res.score, res.status), (0, 0.0, STATUS_FAILED))

    def test_failed_result_is_kept_as_failed(self):
        img = Image.new("RGB", (300, 180))
        final, corr, score = resolve_orientation(img, OrientationResult(0, 0.0, STATUS_FAILED), 0.7)
        self.assertIs(final, img)
        self.assertEqual(corr, "failed")
        self.assertEqual(score, 0.0)

    def test_pplcnet_backend_failsafe_on_bad_session(self):
        # PPLCNetBackend は基底 detect のフェイルセーフ経由で failed を返す。
        with mock.patch(
            "cards.orientation.onnx_session.run_softmax", side_effect=RuntimeError("onnx down")
        ):
            res = PPLCNetBackend().detect(Image.new("RGB", (300, 180), "white"))
        self.assertEqual(res.status, STATUS_FAILED)


class FactoryTests(TestCase):
    def test_default_is_pplcnet(self):
        with override_settings(ORIENTATION_BACKEND="PP-LCNet"):
            self.assertIsInstance(get_orientation_backend(), PPLCNetBackend)

    def test_osd_and_none(self):
        with override_settings(ORIENTATION_BACKEND="OSD"):
            self.assertIsInstance(get_orientation_backend(), OsdBackend)
        with override_settings(ORIENTATION_BACKEND="none"):
            self.assertIsInstance(get_orientation_backend(), NoneBackend)

    def test_unknown_raises(self):
        with override_settings(ORIENTATION_BACKEND="bogus"):
            with self.assertRaises(ValueError):
                get_orientation_backend()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="orient_pipe_"))
class CreateCardPipelineTests(TestCase):
    """_create_card 結線：既定 PP-LCNet で補正状態が入り osd_json=None / none で null。"""

    def setUp(self):
        from cards.tasks.crop_cards import Run_Crop_Cards_From_OriginalImage

        self.user = User.objects.create_superuser("orient_u", "o@e.com", "x")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_OPENCV_PROCESSING,
        )
        self.runner = Run_Crop_Cards_From_OriginalImage(self.original)
        self.warped = Image.open(sorted(FIXTURES.glob("*label90.jpg"))[0]).convert("RGB")

    def _bc(self):
        return BusinessCard.objects.get(original_image=self.original, card_index=0)

    @override_settings(ORIENTATION_BACKEND="PP-LCNet")
    def test_default_sets_correction_and_null_osd_json(self):
        self.runner._create_card(self.warped, 0)
        bc = self._bc()
        self.assertIn(
            bc.orientation_correction,
            {"rotated", "straight", "kept", "failed"},
        )
        self.assertIsNotNone(bc.orientation_score)
        self.assertIsNone(bc.osd_json, "既定 PP-LCNet では osd_json=None")

    @override_settings(ORIENTATION_BACKEND="none")
    def test_none_backend_nulls_fields(self):
        self.runner._create_card(self.warped, 0)
        bc = self._bc()
        self.assertIsNone(bc.orientation_correction)
        self.assertIsNone(bc.orientation_score)
        self.assertIsNone(bc.osd_json)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="orient_manual_"))
class ManualRotateClearTests(TestCase):
    """手動回転で新2フィールドがクリアされる（自動診断値が嘘にならない）。"""

    def setUp(self):
        from django.conf import settings as dj

        self.user = User.objects.create_superuser("orient_m", "m@e.com", "x")
        self.original = OriginalImage.objects.create(
            user=self.user, status=OriginalImage.STATUS_CARDS_EXTRACTED,
        )
        rel = "cards/manual/test-0.jpg"
        abs_path = os.path.join(dj.MEDIA_ROOT, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        Image.new("RGB", (200, 120), "white").save(abs_path, "JPEG")
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0, card_image=rel,
            orientation_correction="rotated", orientation_score=0.93,
        )

    def test_manual_rotation_clears_new_fields(self):
        from cards.views import _rotate_and_overwrite_card_image

        _rotate_and_overwrite_card_image(self.bc, "cw")
        self.bc.refresh_from_db()
        self.assertIsNone(self.bc.orientation_correction)
        self.assertIsNone(self.bc.orientation_score)
        self.assertEqual(self.bc.orientation, BusinessCard.ORIENTATION_NORMAL)
