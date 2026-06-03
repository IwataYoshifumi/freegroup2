"""cards アプリのテスト。

現状は OpenCV 検出 → PipelineCoordinator の results 受け渡しに関する再発防止スモークのみ。
（detector の返り値構造が attempts[] へ変わったのに pipeline が旧トップレベル results を
読み続け、全件 garbage 化＝BusinessCard が1件も作られなくなったバグ 1f9712f の番人）
"""

import io
import os
import shutil
import tempfile
from unittest.mock import patch

import numpy as np
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from django.urls import reverse

from cards.models import BusinessCard, DebugMask, OriginalImage
from cards.services.detectors.opencv_detector import (
    detect_cards,
    detect_cards_with_debug,
    results_from_debug_result,
)


# ─────────────────────────────────────────────────────────────────────────────
# 検出評価の物差し（rev2 §3.2「正解枚数ベース・第1段」）。
# 座標 IoU 照合は行わない簡易版＝「枚数の一致／過不足」のみで測る（正解4点は後日）。
# 各値は実物画像で確定済み。
# ─────────────────────────────────────────────────────────────────────────────
DETECTION_GROUND_TRUTH = {
    "296f9aef-68ea-49e8-9482-2683405039cb": 6,   # ①
    "602e1478-3b8e-49be-9900-a551978846f2": 0,   # ②（人物写真・名刺なし＝ゴースト判定用）
    "9ddc2224-4420-4336-953b-a234c897a333": 8,   # ③
    "9c4ca714-637e-4dfd-8892-97c05c81044b": 4,   # ④（白名刺2枚落ち）
    "67a044a2-47f0-4c29-b860-1bda870675f2": 1,   # ⑤（白名刺・検出0）
    # ⑥ 実物は8枚。設計方針rev2.md:84「名刺6枚」は誤記（rev2 本体は正式化時に修正）。
    "4d04ea8b-d61e-4528-8271-39c7f483cb84": 8,   # ⑥（緑机）
}


def score_detection_counts(ground_truth: dict, detected_counts: dict) -> dict:
    """[性質] 純関数（DB操作なし・副作用なし）/ 正解枚数と検出枚数から枚数ベースの簡易スコアを出す。

    座標を持たないため個々の検出が正解名刺に対応するかの IoU 照合は行わない簡易版。
    1画像の「正しく拾えた枚数」は min(検出, 正解)、「過検出」は max(0, 検出 - 正解) で近似する。

    [入力]
      ground_truth:    {uuid: 正解枚数}
      detected_counts: {uuid: 現状検出枚数（統合結果の件数）}
    [出力] dict:
      per_image:        [{uuid, truth, detected, diff, matched, over, short, is_ghost, ghost_hits}]
      truth_total:      正解総数（検出率の分母）
      matched_total:    正しく拾えた枚数の合計（Σ min(検出,正解)）
      detection_rate:   matched_total / truth_total（truth_total=0 なら None）
      over_total:       過検出件数の合計（Σ max(0, 検出-正解)）
      ghost_detections: {uuid: 検出数}（正解0＝名刺なし画像で1件以上出たもの）
    """
    per_image = []
    truth_total = 0
    matched_total = 0
    over_total = 0
    ghost_detections = {}
    for uuid, truth in ground_truth.items():
        det = detected_counts.get(uuid, 0)
        matched = min(det, truth)
        over = max(0, det - truth)
        short = max(0, truth - det)
        is_ghost = (truth == 0)
        truth_total += truth
        matched_total += matched
        over_total += over
        if is_ghost and det > 0:
            ghost_detections[uuid] = det
        per_image.append({
            "uuid": uuid, "truth": truth, "detected": det,
            "diff": det - truth, "matched": matched, "over": over,
            "short": short, "is_ghost": is_ghost,
            "ghost_hits": det if is_ghost else 0,
        })
    return {
        "per_image": per_image,
        "truth_total": truth_total,
        "matched_total": matched_total,
        "detection_rate": (matched_total / truth_total) if truth_total else None,
        "over_total": over_total,
        "ghost_detections": ghost_detections,
    }


def _high_saturation_png_bytes(w=1000, h=800, rw=600, rh=360) -> bytes:
    """[性質] 純関数 / 全面を高彩度の緑で塗り、低彩度（白）の名刺矩形を1枚置いた PNG バイト列。

    緑デスク相当：背景が高彩度なので mask_sat の白画素率は旧 sat_fallback 閾値 0.50 を超える。
    sat_fallback の除外撤廃（rev2）で sat が除外されず候補抽出されることの検証に使う。
    """
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = (0, 200, 0)  # 高彩度の緑（HSV 彩度 ≈ 255）で全面塗り
    x0, y0 = (w - rw) // 2, (h - rh) // 2
    arr[y0:y0 + rh, x0:x0 + rw] = 255  # 低彩度（白）の名刺
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _overlay_debug_json():
    """[性質] 純関数 / マスクオーバーレイ検証用の合成 debug_json（単一 attempt）。

    diff: passed×1 / too_small(極小)×1 / zero_size×1、edge: aspect_invalid×1、sat: 空。
    全体通し番号（flatten 順 diff→edge→sat）は diff=[0,1,2], edge=[3] になる。
    """
    def _cand(index, area, area_ratio, center, size, angle, aspect, passed, reason):
        return {
            "index": index, "area": area, "area_ratio": area_ratio,
            "rect_center": center, "rect_size": size, "rect_angle": angle,
            "aspect_ratio": aspect, "passed": passed, "reject_reason": reason,
        }

    return {
        "image_size": {"width": 1000, "height": 800, "area": 800000},
        "attempts": [{
            "attempt_no": 1, "type": "normal",
            "masks": {
                "diff": {"excluded": False, "contours_count": 3, "candidates_filter": [
                    _cand(0, 216000, 0.27, [500, 400], [600, 360], 0.0, 1.667, True, ""),
                    _cand(1, 100, 0.000125, [10, 10], [10, 10], 0.0, None, False, "too_small"),
                    _cand(2, 0, 0.0, [5, 5], [0, 0], -90.0, None, False, "zero_size"),
                ]},
                "edge": {"excluded": False, "contours_count": 1, "candidates_filter": [
                    _cand(0, 30000, 0.0375, [300, 300], [200, 150], 0.0, 1.333, False, "aspect_invalid"),
                ]},
                "sat": {"excluded": False, "contours_count": 0, "candidates_filter": []},
            },
            "candidates_dedup": [], "warp_failures": [], "results": [],
        }],
        "sat_fallback": {"triggered": False, "sat_white_ratio": 0.1, "threshold": 0.5},
        "or_inversion": {"attempted": False, "passed_before": 1, "passed_after": 1,
                         "improved": False, "polygon_expand": {"ratio": 0.0, "min_px": 0}},
        "error_message": "",
        "computed_at": "2026-06-02T00:00:00+00:00",
    }


def _synthetic_card_png_bytes(w=1000, h=800, rw=600, rh=360) -> bytes:
    """[性質] 純関数 / 黒背景に白い名刺状の矩形（aspect≈1.667）を1枚置いた PNG バイト列。

    detector が確実に1枚検出できる合成画像（実画像・メディア依存・OCR API を持ち込まない）。
    """
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    x0, y0 = (w - rw) // 2, (h - rh) // 2
    arr[y0:y0 + rh, x0:x0 + rw] = 255
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


# card item スキーマを通る最小の OCR 結果（is_business_card=False で
# normalize/has_minimum_info の内部仕様に依存させず、BusinessCard 生成ループ到達のみを見る）。
_FAKE_OCR_RESULT = {
    "api_response": {
        "model": "test-model",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    },
    "cards": [
        {
            "card_meta": {"is_business_card": False, "orientation": "normal"},
            "fields": {},
            "fields_array": {},
        }
    ],
}


class DetectionScoringHelperTests(TestCase):
    """rev2 §3.2 枚数ベース採点ヘルパー（score_detection_counts）が動くことの確認。

    ベースラインの良し悪しは判定しない（検出率100%を要求しない）。採点ロジック自体の番人。
    """

    def test_scoring_basic_counts(self):
        gt = {"a": 6, "b": 0, "c": 4}
        det = {"a": 4, "b": 1, "c": 5}
        r = score_detection_counts(gt, det)
        # 正解総数 6+0+4=10
        self.assertEqual(r["truth_total"], 10)
        # 正しく拾えた枚数 min(4,6)+min(1,0)+min(5,4)=4+0+4=8
        self.assertEqual(r["matched_total"], 8)
        self.assertAlmostEqual(r["detection_rate"], 0.8)
        # 過検出 max(0,4-6)+max(0,1-0)+max(0,5-4)=0+1+1=2
        self.assertEqual(r["over_total"], 2)
        # ゴースト（正解0で検出>0）は b の1件
        self.assertEqual(r["ghost_detections"], {"b": 1})

    def test_scoring_missing_uuid_counts_as_zero(self):
        r = score_detection_counts({"x": 3}, {})  # 検出データ無し→0扱い
        self.assertEqual(r["matched_total"], 0)
        self.assertEqual(r["per_image"][0]["short"], 3)
        self.assertEqual(r["over_total"], 0)

    def test_ground_truth_ledger_has_six_entries(self):
        self.assertEqual(len(DETECTION_GROUND_TRUTH), 6)
        # ② は名刺なし（ゴースト判定の基準）
        self.assertEqual(DETECTION_GROUND_TRUTH["602e1478-3b8e-49be-9900-a551978846f2"], 0)


class OsdUprightTests(TestCase):
    """rev2 第2部: OCR 前 OSD 正立化の分岐（成功＝正立化 / 失敗＝normal フォールバック）の番人。

    image_to_osd は detect_osd_rotation でラップしており、本テストはそこをモックして
    成功（rotate 値を返す）／失敗（例外）の両経路を担保する（環境に Tesseract 本体は不要）。
    """

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))
        self.user = get_user_model().objects.create_superuser(
            username="osd_test", email="osd@example.com", password="x"
        )

    def _coordinator(self):
        from cards.tasks.pipeline_coordinator import PipelineCoordinator
        oi = OriginalImage(user=self.user, status=OriginalImage.STATUS_PROCESSING)
        oi.image_file.save(
            f"{oi.id}.png", ContentFile(_synthetic_card_png_bytes()), save=False
        )
        oi.save()
        return PipelineCoordinator(oi)

    def test_apply_upright_direction_and_noop(self):
        """apply_upright: 90 で寸法入替、0 は同一オブジェクト返し（寸法のみ確認）。"""
        from cards.services.osd import apply_upright
        img = Image.new("RGB", (10, 4))
        self.assertIs(apply_upright(img, 0), img)            # 回転不要は素通し
        r = apply_upright(img, 90)
        self.assertEqual(r.size, (4, 10))                     # (10,4)→90回転→(4,10)

    def test_apply_upright_restores_upright_all_quadrants(self):
        """apply_upright が 0/90/180/270 の全分岐で正立画像へ戻すこと（方向＝CW の検証）。

        Tesseract Rotate は「CW で正立させる度数」。入力を t°CW 倒したとき OSD が返すはずの値は
        (360 - t) % 360。その値を apply_upright に与えると元の正立画像に一致する（90の倍数・
        NEAREST 回転で無損失）。180°ずれのリグレッション番人。
        """
        from cards.services.osd import apply_upright
        # 角ごとに異色の非対称な正立画像（回転方向の取り違えを画素で検知）
        U = Image.new("RGB", (6, 4))
        U.putpixel((0, 0), (255, 0, 0))      # 左上=赤
        U.putpixel((5, 0), (0, 255, 0))      # 右上=緑
        U.putpixel((0, 3), (0, 0, 255))      # 左下=青
        U.putpixel((5, 3), (255, 255, 0))    # 右下=黄
        expected = list(U.getdata())
        for t in (0, 90, 180, 270):
            tilted = U if t == 0 else U.rotate(-t, expand=True)   # -t = t°CW 倒し
            osd_rotate = (360 - t) % 360                          # OSD が返すはずの CW-to-upright
            out = apply_upright(tilted, osd_rotate)
            self.assertEqual(out.size, U.size, f"t={t}: 寸法が正立画像と不一致")
            self.assertEqual(
                list(out.getdata()), expected,
                f"t={t}: 画素が正立画像と不一致（回転方向の取り違え＝180°ずれの疑い）",
            )

    def test_osd_success_passes_upright_image(self):
        """OSD 成功（rotate=90）→ run_ocr に渡るのは正立画像（寸法入替）。"""
        coord = self._coordinator()
        warped = Image.new("RGB", (12, 4))  # 非正方で回転を寸法で検知
        with patch("cards.tasks.pipeline_coordinator.detect_osd_rotation", return_value=90):
            out = coord._upright_with_osd(warped, 0)
        self.assertIsNot(out, warped)        # 別画像（正立化された）
        self.assertEqual(out.size, (4, 12))  # 90度回転で寸法入替

    def test_osd_rotate_zero_returns_original(self):
        """OSD 成功だが rotate=0（既に正立）→ 元 warped をそのまま返す（保存なし）。"""
        coord = self._coordinator()
        warped = Image.new("RGB", (12, 4))
        with patch("cards.tasks.pipeline_coordinator.detect_osd_rotation", return_value=0):
            out = coord._upright_with_osd(warped, 0)
        self.assertIs(out, warped)

    def test_osd_failure_falls_back_to_normal(self):
        """OSD 失敗（image_to_osd 例外）→ 回転せず元 warped をそのまま返す（例外を漏らさない）。"""
        coord = self._coordinator()
        warped = Image.new("RGB", (12, 4))
        with patch(
            "cards.tasks.pipeline_coordinator.detect_osd_rotation",
            side_effect=RuntimeError("Too few characters"),
        ):
            out = coord._upright_with_osd(warped, 0)
        self.assertIs(out, warped)


class OsdManagementCommandTests(TestCase):
    """OSD 管理コマンドが Tesseract 未導入でも起動し、未導入を明示できることの確認。"""

    def test_osd_check_runs_without_tesseract(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        # 例外を投げずに完走し、何らかの状態を出力する（pytesseract 未導入 or 本体未導入を明示）。
        call_command("osd_check", stdout=out, stderr=StringIO())
        text = out.getvalue()
        self.assertIn("OSD 疎通確認", text)
        # 未導入環境では pytesseract 未導入 か Tesseract 本体未検出 のどちらかを表示する。
        self.assertTrue(
            ("pytesseract" in text) or ("Tesseract" in text) or ("tesseract" in text)
        )

    def test_osd_verify_rotation_missing_image_errors_cleanly(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        # 存在しないパス指定 → CommandError（スタックトレースでなく明快なエラー）。
        with self.assertRaises(CommandError):
            call_command("osd_verify_rotation", "--image", "/no/such/file.png")


def _poly(x, y, w, h):
    """[性質] 純関数 / 軸平行矩形の polygon dict（四隅）。テスト用。"""
    return {
        "top_left":     {"x": x,     "y": y},
        "top_right":    {"x": x + w, "y": y},
        "bottom_right": {"x": x + w, "y": y + h},
        "bottom_left":  {"x": x,     "y": y + h},
    }


class CrossAttemptIntegrationTests(TestCase):
    """rev2: 反転常時化＋attempt横断統合dedup（通常優先）の番人。"""

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))

    def test_inversion_always_runs_two_attempts(self):
        """通常で検出できても反転 attempt は常に走り、attempts は2要素・integrated_results を持つ。"""
        path = os.path.join(self._tmp_media, "synthetic_card.png")
        with open(path, "wb") as f:
            f.write(_synthetic_card_png_bytes())

        debug_result = detect_cards_with_debug(path)
        self.assertEqual(len(debug_result["attempts"]), 2)
        self.assertEqual(debug_result["attempts"][0]["type"], "normal")
        self.assertEqual(debug_result["attempts"][1]["type"], "inverted")
        self.assertTrue(debug_result["or_inversion"]["attempted"])
        self.assertIn("integrated_results", debug_result)

    def test_integrate_results_normal_priority_on_overlap(self):
        """通常・反転が同一名刺で重複したら通常(normal)を残す。"""
        from cards.services.detectors.opencv_detector import _integrate_results

        normal = [{"polygon": _poly(100, 100, 600, 360), "warped_image": "N"}]
        inverted = [{"polygon": _poly(105, 105, 600, 360), "warped_image": "I"}]
        merged = _integrate_results(normal, inverted)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["origin"], "normal")
        self.assertEqual(merged[0]["warped_image"], "N")

    def test_integrate_results_keeps_disjoint_from_both(self):
        """重ならない通常・反転の検出は両方残り、origin が付く。"""
        from cards.services.detectors.opencv_detector import _integrate_results

        normal = [{"polygon": _poly(100, 100, 600, 360), "warped_image": "N"}]
        inverted = [{"polygon": _poly(100, 1000, 600, 360), "warped_image": "I"}]
        merged = _integrate_results(normal, inverted)

        self.assertEqual(len(merged), 2)
        origins = {m["origin"] for m in merged}
        self.assertEqual(origins, {"normal", "inverted"})

    def test_bbox_from_polygon(self):
        from cards.services.detectors.opencv_detector import _bbox_from_polygon

        self.assertEqual(_bbox_from_polygon(_poly(10, 20, 30, 40)), (10, 20, 30, 40))


class SatFallbackExclusionRemovedTests(TestCase):
    """rev2: sat_fallback の除外動作撤廃の番人。

    高彩度背景（緑デスク相当）で sat_white_ratio が旧閾値 0.50 を超えても、sat は
    候補抽出から除外されない（excluded=False・候補が抽出される）ことを担保する。
    triggered フラグ自体は診断情報として残るが、それで除外はしない。
    """

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))

    def test_high_saturation_background_does_not_exclude_sat(self):
        path = os.path.join(self._tmp_media, "green_desk.png")
        with open(path, "wb") as f:
            f.write(_high_saturation_png_bytes())

        debug_result = detect_cards_with_debug(path)

        # 前提：高彩度背景で sat_white_ratio は旧閾値 0.50 を超える（診断 triggered は立つ）
        sat_fb = debug_result["sat_fallback"]
        self.assertGreater(sat_fb["sat_white_ratio"], 0.50)
        self.assertTrue(sat_fb["triggered"])

        # 本題：それでも sat は除外されず、各 attempt で候補抽出される
        attempts = debug_result.get("attempts") or []
        self.assertGreaterEqual(len(attempts), 1)
        for att in attempts:
            sat = att["masks"]["sat"]
            self.assertFalse(
                sat["excluded"],
                msg=f"attempt {att['attempt_no']}: sat が除外されている（撤廃したはず）",
            )
            # 除外されていれば contours=0/candidates=[] になる。抽出経路に入った証拠を見る。
            self.assertGreater(sat["contours_count"], 0)
            self.assertGreaterEqual(len(sat["candidates_filter"]), 1)


class ResultsAccessorTests(TestCase):
    """detector の検出結果アクセサ（attempts[-1] を知る唯一の窓口）の契約テスト。"""

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))

    def test_results_accessor_finds_detections_not_toplevel(self):
        """検出結果は integrated_results にあり、results_from_debug_result が detect_cards と一致する。"""
        path = os.path.join(self._tmp_media, "synthetic_card.png")
        with open(path, "wb") as f:
            f.write(_synthetic_card_png_bytes())

        debug_result = detect_cards_with_debug(path)
        # バグの前提：トップレベルに生 results キーは無い（旧 pipeline はここを読んで常に空だった）
        self.assertNotIn("results", debug_result)
        accessor = results_from_debug_result(debug_result)
        self.assertGreaterEqual(len(accessor), 1)
        # 唯一の窓口の戻りが detect_cards（同じ窓口経由）と一致すること
        self.assertEqual(len(accessor), len(detect_cards(path)))


class PipelineDetectionFlowTests(TestCase):
    """PipelineCoordinator が detector の検出結果を正しく受け取り、card 生成ループに入るか。"""

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))
        self.user = get_user_model().objects.create_superuser(
            username="pipe_test", email="p@example.com", password="x"
        )

    def _make_original(self):
        oi = OriginalImage(user=self.user, status=OriginalImage.STATUS_PROCESSING)
        oi.image_file.save(
            f"{oi.id}.png", ContentFile(_synthetic_card_png_bytes()), save=False
        )
        oi.save()
        return oi

    def test_run_pipeline_creates_business_card_from_detections(self):
        """合成名刺1枚 → detections が空にならず BusinessCard が1件作られる（garbage 打ち切りに入らない）。

        OCR（Claude API）は OcrService をモックして置換し、ネットワーク・課金を持ち込まない。
        本テストは「results が pipeline に正しく渡り card 生成ループに入る」ことの番人。
        修正前のバグ状態（pipeline が旧トップレベル results を読む）では detections=[] となり
        status=garbage で打ち切られ BusinessCard は 0 件になる。
        """
        from cards.tasks.pipeline_coordinator import PipelineCoordinator

        oi = self._make_original()

        with patch("cards.tasks.pipeline_coordinator.OcrService") as MockOcr:
            MockOcr.return_value.run_ocr.return_value = _FAKE_OCR_RESULT
            PipelineCoordinator(oi).run_pipeline()

        oi.refresh_from_db()
        # garbage 早期 return に入っていない＝検出が pipeline に渡っている
        self.assertGreaterEqual(oi.detected_count, 1)
        self.assertEqual(oi.status, OriginalImage.STATUS_EXTRACTED)
        self.assertEqual(
            BusinessCard.objects.filter(original_image=oi).count(),
            oi.detected_count,
        )


class MaskOverlayHelperTests(TestCase):
    """マスクオーバーレイ用 view ヘルパー（純関数）のテスト。"""

    def test_rotated_rect_corners_axis_aligned(self):
        """軸平行（angle=0）矩形は中心±半幅・半高の4隅になる。"""
        from cards.views import _rotated_rect_corners

        corners = _rotated_rect_corners(100, 100, 40, 20, 0)
        xs = sorted({round(x) for x, _ in corners})
        ys = sorted({round(y) for _, y in corners})
        self.assertEqual(xs, [80, 120])
        self.assertEqual(ys, [90, 110])

    def test_rotated_rect_corners_matches_cv2_boxpoints(self):
        """回転矩形の4隅が cv2.boxPoints と一致する（描画座標の正しさ）。"""
        import cv2

        from cards.views import _rotated_rect_corners

        rect = ((123.0, 222.0), (88.0, 50.0), 31.0)
        cv_pts = cv2.boxPoints(rect)
        mine = _rotated_rect_corners(123.0, 222.0, 88.0, 50.0, 31.0)
        for (mx, my), cvpt in zip(mine, cv_pts):
            self.assertAlmostEqual(mx, float(cvpt[0]), places=3)
            self.assertAlmostEqual(my, float(cvpt[1]), places=3)

    def test_classify_overlay_candidate(self):
        """passed/near/noise の分類（aspect_invalid と area比0.03 が境界）。"""
        from cards.views import _classify_overlay_candidate as cls

        self.assertEqual(cls({"passed": True}), "passed")
        self.assertEqual(
            cls({"passed": False, "reject_reason": "aspect_invalid"}), "near")
        self.assertEqual(
            cls({"passed": False, "reject_reason": "too_small", "area_ratio": 0.05}),
            "near")
        self.assertEqual(
            cls({"passed": False, "reject_reason": "too_small", "area_ratio": 0.01}),
            "noise")
        self.assertEqual(
            cls({"passed": False, "reject_reason": "zero_size", "area_ratio": 0.0}),
            "noise")

    def test_overlay_numbers_match_table_flatten_index(self):
        """オーバーレイの num が候補一覧テーブルの「#」（全体通し番号）と一致する。"""
        from cards.views import _build_attempt_overlays, _flatten_attempt_candidates

        attempt = _overlay_debug_json()["attempts"][0]
        flat = _flatten_attempt_candidates(attempt)
        expected = {}
        for c in flat:
            expected.setdefault(c["mask"], []).append(c["index"])

        overlays = _build_attempt_overlays(attempt)
        for mask, cands in overlays.items():
            self.assertEqual([c["num"] for c in cands], expected.get(mask, []))
        # 具体値：diff=[0,1,2], edge=[3], sat=[]
        self.assertEqual([c["num"] for c in overlays["diff"]], [0, 1, 2])
        self.assertEqual([c["num"] for c in overlays["edge"]], [3])
        self.assertEqual(overlays["sat"], [])
        # カテゴリ
        self.assertEqual([c["category"] for c in overlays["diff"]],
                         ["passed", "noise", "noise"])
        self.assertEqual(overlays["edge"][0]["category"], "near")

    def test_build_mask_blocks_overlay_only_on_closed(self):
        """クローズ後マスクにのみ overlay が載り、生マスクには載らない。block2 は attempt2 が無ければ空。"""
        from types import SimpleNamespace

        from cards.views import _build_mask_blocks

        def _fake(mask_type, attempt_no):
            return SimpleNamespace(
                mask_type=mask_type, attempt_no=attempt_no,
                mask_image=SimpleNamespace(url=f"/m/{mask_type}_{attempt_no}.png"),
            )

        masks = [
            _fake(DebugMask.MaskType.DIFF, 1),
            _fake(DebugMask.MaskType.EDGE, 1),
            _fake(DebugMask.MaskType.SAT, 1),
            _fake(DebugMask.MaskType.ADAPTIVE, 1),
            _fake(DebugMask.MaskType.DIFF_CLOSED, 1),
            _fake(DebugMask.MaskType.EDGE_CLOSED, 1),
            _fake(DebugMask.MaskType.SAT_CLOSED, 1),
            _fake(DebugMask.MaskType.ADAPTIVE_CLOSED, 1),
        ]
        block1, block2 = _build_mask_blocks(masks, _overlay_debug_json())

        self.assertEqual(len(block1), 8)
        self.assertEqual(block2, [])
        by_name = {(s["mask_name"], s["is_closed"]): s for s in block1}
        # 生マスクは overlay 無し
        self.assertEqual(by_name[("diff", False)]["overlay"], [])
        self.assertEqual(by_name[("edge", False)]["overlay"], [])
        # クローズ後マスクは overlay 有り（diff=3件, edge=1件, sat=0件, adaptive=0件）
        self.assertEqual(len(by_name[("diff", True)]["overlay"]), 3)
        self.assertEqual(len(by_name[("edge", True)]["overlay"]), 1)
        self.assertEqual(by_name[("sat", True)]["overlay"], [])
        self.assertEqual(by_name[("adaptive", True)]["overlay"], [])

    def test_build_mask_blocks_block2_uses_attempt2_candidates(self):
        """反転リトライ時、block2 は attempt_no=2 の候補を載せる（attempt 取り違え防止）。"""
        from types import SimpleNamespace

        from cards.views import _build_mask_blocks

        dj = _overlay_debug_json()
        # attempt2 を追加（edge に passed 1 件のみ）
        a2 = {
            "attempt_no": 2, "type": "inverted",
            "masks": {
                "diff": {"excluded": False, "contours_count": 0, "candidates_filter": []},
                "edge": {"excluded": False, "contours_count": 1, "candidates_filter": [
                    {"index": 0, "area": 200000, "area_ratio": 0.25,
                     "rect_center": [500, 400], "rect_size": [600, 360],
                     "rect_angle": 0.0, "aspect_ratio": 1.667,
                     "passed": True, "reject_reason": ""},
                ]},
                "sat": {"excluded": False, "contours_count": 0, "candidates_filter": []},
            },
            "candidates_dedup": [], "warp_failures": [], "results": [],
        }
        dj["attempts"].append(a2)

        def _fake(mask_type, attempt_no):
            return SimpleNamespace(
                mask_type=mask_type, attempt_no=attempt_no,
                mask_image=SimpleNamespace(url=f"/m/{mask_type}_{attempt_no}.png"),
            )

        masks = [_fake(mt, n) for n in (1, 2) for mt in (
            DebugMask.MaskType.DIFF, DebugMask.MaskType.EDGE, DebugMask.MaskType.SAT,
            DebugMask.MaskType.DIFF_CLOSED, DebugMask.MaskType.EDGE_CLOSED,
            DebugMask.MaskType.SAT_CLOSED,
        )]
        block1, block2 = _build_mask_blocks(masks, dj)

        b2 = {(s["mask_name"], s["is_closed"]): s for s in block2}
        # block2 の edge_closed は attempt2 の passed 候補（num=0、edge が先頭）
        self.assertEqual(len(b2[("edge", True)]["overlay"]), 1)
        self.assertEqual(b2[("edge", True)]["overlay"][0]["category"], "passed")
        # block1 の edge_closed は attempt1（aspect_invalid=near）のまま
        b1 = {(s["mask_name"], s["is_closed"]): s for s in block1}
        self.assertEqual(b1[("edge", True)]["overlay"][0]["category"], "near")


class OriginalDetailMaskOverlayRenderTests(TestCase):
    """OriginalDetailView がマスクへ候補矩形 SVG を描画することの結合テスト。"""

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))
        self.user = get_user_model().objects.create_superuser(
            username="overlay_test", email="o@example.com", password="x"
        )
        self.client.force_login(self.user)

    def _make_original_with_masks(self):
        oi = OriginalImage(user=self.user, status=OriginalImage.STATUS_PENDING,
                           debug_json=_overlay_debug_json())
        oi.image_file.save(
            f"{oi.id}.png", ContentFile(_synthetic_card_png_bytes()), save=False
        )
        oi.save()
        for mtype in (
            DebugMask.MaskType.DIFF, DebugMask.MaskType.EDGE, DebugMask.MaskType.SAT,
            DebugMask.MaskType.DIFF_CLOSED, DebugMask.MaskType.EDGE_CLOSED,
            DebugMask.MaskType.SAT_CLOSED,
        ):
            m = DebugMask(original_image=oi, mask_type=mtype, attempt_no=1)
            m.mask_image.save(f"{mtype}.png",
                              ContentFile(_synthetic_card_png_bytes(120, 100)), save=False)
            m.save()
        return oi

    def test_detail_renders_mask_overlay_polygons(self):
        oi = self._make_original_with_masks()
        url = reverse("originals:original_detail", kwargs={"pk": oi.id})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        # オーバーレイ SVG が描画され、緑(採用)・赤(惜しい棄却)・ノイズが分類されている
        self.assertIn("app-mask-overlay__svg", body)
        self.assertIn("app-debug-polygon--passed", body)
        self.assertIn("app-debug-polygon--near", body)
        self.assertIn("app-mask-cand--noise", body)
        # 惜しい棄却には理由ラベル
        self.assertIn("aspect_invalid", body)
        # 番号がテーブル # と一致：採用=0、惜しい棄却(edge)=3
        self.assertIn("<tspan>0</tspan>", body)
        self.assertIn("<tspan>3</tspan>", body)
        # SVG は overlay 非空のクローズ後マスク（diff_closed, edge_closed）の 2 枚のみ
        self.assertEqual(body.count("app-mask-overlay__svg"), 2)


class TesseractRecordTests(TestCase):
    """OSD/OCR 出力の raw_json 記録（osd / tesseract_ocr サブセクション）の番人。

    Tesseract 本体は不要：osd.py の image_to_osd / image_to_string / image_to_data を
    モックして、パース・フィルタ・raw_json への合成・スキーマ適合を担保する。
    記録は分析専用であり、向き判定・採否ロジックには使わないこと（本テストの対象外）。
    """

    def setUp(self):
        self._tmp_media = tempfile.mkdtemp(prefix="fg2_test_media_")
        self._override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._tmp_media, ignore_errors=True))
        self.user = get_user_model().objects.create_superuser(
            username="tess_test", email="tess@example.com", password="x"
        )

    def _coordinator(self):
        from cards.tasks.pipeline_coordinator import PipelineCoordinator
        oi = OriginalImage(user=self.user, status=OriginalImage.STATUS_PROCESSING)
        oi.image_file.save(
            f"{oi.id}.png", ContentFile(_synthetic_card_png_bytes()), save=False
        )
        oi.save()
        return PipelineCoordinator(oi)

    _OSD_SAMPLE = (
        "Page number: 0\n"
        "Orientation in degrees: 90\n"
        "Rotate: 270\n"
        "Orientation confidence: 12.34\n"
        "Script: Japanese\n"
        "Script confidence: 5.67\n"
    )

    def test_detect_osd_info_parses_all_fields(self):
        """image_to_osd の全 5 項目を型付きで拾う（rotate は %360）。"""
        from cards.services.osd import detect_osd_info
        with patch("pytesseract.image_to_osd", return_value=self._OSD_SAMPLE):
            info = detect_osd_info(Image.new("RGB", (8, 8)))
        self.assertEqual(info, {
            "orientation_deg": 90,
            "rotate": 270,
            "orientation_conf": 12.34,
            "script": "Japanese",
            "script_conf": 5.67,
        })

    def test_words_from_data_keeps_only_nonempty_words(self):
        """_words_from_data: level=5 かつ非空のみ残す（container 行・空白語は落とす）。"""
        from cards.services.osd import _words_from_data
        data = {
            "level":     [1, 2, 3, 4, 5, 5],
            "text":      ["", "", "", "", "Hello", "   "],
            "conf":      ["-1", "-1", "-1", "-1", "95.5", "-1"],
            "left":      [0, 0, 0, 0, 10, 0],
            "top":       [0, 0, 0, 0, 20, 0],
            "width":     [0, 0, 0, 0, 30, 0],
            "height":    [0, 0, 0, 0, 12, 0],
            "block_num": [0, 1, 1, 1, 1, 1],
            "par_num":   [0, 0, 1, 1, 1, 1],
            "line_num":  [0, 0, 0, 1, 1, 1],
            "word_num":  [0, 0, 0, 0, 1, 2],
        }
        words = _words_from_data(data)
        self.assertEqual(len(words), 1)              # 空白のみの level5 と container 行は除外
        self.assertEqual(words[0]["text"], "Hello")
        self.assertEqual(words[0]["conf"], 95.5)
        self.assertEqual(
            (words[0]["left"], words[0]["top"], words[0]["width"], words[0]["height"]),
            (10, 20, 30, 12),
        )
        self.assertEqual(words[0]["line_num"], 1)

    def test_run_tesseract_ocr_returns_text_and_filtered_words(self):
        """run_tesseract_ocr: text と word データ（フィルタ済み）を返す。"""
        from cards.services.osd import run_tesseract_ocr
        data = {
            "level": [5], "text": ["meishi"], "conf": ["88.0"],
            "left": [1], "top": [2], "width": [3], "height": [4],
            "block_num": [1], "par_num": [1], "line_num": [1], "word_num": [1],
        }
        with patch("pytesseract.image_to_string", return_value="meishi\n"), \
             patch("pytesseract.image_to_data", return_value=data):
            res = run_tesseract_ocr(Image.new("RGB", (8, 8)))
        self.assertEqual(res["lang"], "jpn+jpn_vert")
        self.assertEqual(res["text"], "meishi\n")
        self.assertEqual(len(res["data"]), 1)
        self.assertEqual(res["data"][0]["text"], "meishi")

    def test_collect_tesseract_record_captures_errors(self):
        """OSD/OCR が例外を投げても record に error として残り、pipeline を止めない。"""
        coord = self._coordinator()
        warped = Image.new("RGB", (120, 80))
        with patch(
            "cards.tasks.pipeline_coordinator.detect_osd_info",
            side_effect=RuntimeError("Too few characters"),
        ), patch(
            "cards.tasks.pipeline_coordinator.run_tesseract_ocr",
            side_effect=RuntimeError("tesseract not found"),
        ):
            rec = coord._collect_tesseract_record(warped, warped, 0)
        self.assertIn("Too few characters", rec["osd"]["error"])
        self.assertIn("tesseract not found", rec["tesseract_ocr"]["error"])

    def test_cards_payload_merges_record_and_keeps_none(self):
        """_cards_payload: dict カードには osd/tesseract_ocr を非破壊合成、None には足さない。"""
        coord = self._coordinator()
        card0 = {
            "card_meta": {"is_business_card": True, "orientation": "normal"},
            "fields": {}, "fields_array": {},
        }
        coord._cards_by_index = {0: card0, 1: None}
        coord._tesseract_by_index = {
            0: {"osd": {"rotate": 0}, "tesseract_ocr": {"lang": "jpn", "text": "a", "data": []}},
            1: {"osd": {"rotate": 90}, "tesseract_ocr": {"lang": "jpn", "text": "b", "data": []}},
        }
        payload = coord._cards_payload(2)
        self.assertEqual(payload[0]["osd"], {"rotate": 0})
        self.assertEqual(payload[0]["tesseract_ocr"]["text"], "a")
        self.assertEqual(payload[0]["card_meta"]["is_business_card"], True)  # 既存キー保持
        self.assertNotIn("osd", card0)                       # 元 dict は不変
        self.assertIsNone(payload[1])                        # None には足さない

    def test_attached_card_validates_against_schema(self):
        """osd/tesseract_ocr を足した raw_json が combined スキーマに適合する。"""
        import jsonschema
        coord = self._coordinator()
        card = {
            "card_meta": {"is_business_card": True, "orientation": "normal"},
            "fields": {"full_name": {"value": "name", "confidence": "high"}},
            "fields_array": {},
        }
        coord._cards_by_index = {0: card}
        coord._tesseract_by_index = {0: {
            "osd": {"orientation_deg": 0, "rotate": 0, "orientation_conf": 1.0,
                    "script": "Japanese", "script_conf": 2.0},
            "tesseract_ocr": {"lang": "jpn+jpn_vert", "text": "name",
                              "data": [{"text": "name", "conf": 90.0, "left": 1, "top": 2,
                                        "width": 3, "height": 4, "block_num": 1, "par_num": 1,
                                        "line_num": 1, "word_num": 1}]},
        }}
        payload = coord._cards_payload(1)
        # card 単体スキーマ・combined スキーマ双方で検証エラーが出ないこと
        jsonschema.validate(instance=payload[0], schema=coord._load_card_item_schema())
        raw = {
            "schema_version": "1.3.0",
            "ocr_meta": {"timestamp": "2026-06-03T00:00:00+00:00"},
            "api_response": {"model": "m", "stop_reason": "end_turn", "usage": {}},
            "cards": payload,
        }
        jsonschema.validate(instance=raw, schema=coord._load_combined_schema())

    def test_process_card_populates_tesseract_record(self):
        """_process_card の通しで _tesseract_by_index が埋まり、payload に反映される（配線確認）。"""
        coord = self._coordinator()
        warped = Image.new("RGB", (120, 80))
        card = {
            "card_meta": {"is_business_card": False, "orientation": "normal"},
            "fields": {}, "fields_array": {},
        }
        ocr_result = {
            "cards": [card],
            "api_response": {"model": "m", "stop_reason": "end_turn", "usage": {}},
        }
        with patch("cards.tasks.pipeline_coordinator.detect_osd_rotation", return_value=0), \
             patch("cards.tasks.pipeline_coordinator.detect_osd_info",
                   return_value={"orientation_deg": 0, "rotate": 0, "orientation_conf": 1.0,
                                 "script": "Japanese", "script_conf": 2.0}), \
             patch("cards.tasks.pipeline_coordinator.run_tesseract_ocr",
                   return_value={"lang": "jpn+jpn_vert", "text": "X", "data": []}), \
             patch.object(coord._ocr_service, "run_ocr", return_value=ocr_result):
            coord._process_card(warped, 0, _poly(0, 0, 120, 80))

        rec = coord._tesseract_by_index[0]
        self.assertEqual(rec["osd"]["script"], "Japanese")
        self.assertEqual(rec["tesseract_ocr"]["text"], "X")
        payload = coord._cards_payload(1)
        self.assertEqual(payload[0]["osd"]["rotate"], 0)
        self.assertEqual(payload[0]["tesseract_ocr"]["lang"], "jpn+jpn_vert")
