"""_build_overlay_polygons の手動枠合流（段2-c1）のユニットテスト。

- manual_results を渡すと is_manual:True の要素が手動枠の数だけ増える。
- 自動枠（is_manual:False）の数・points_str・is_giant は手動枠の有無で変わらない（挙動不変）。
- 手動枠の is_giant は必ず False（giant 母集団に非干渉）。
- manual_results 無し/空/debug_json=None でも従来どおり（手動枠ゼロ）。
"""

from django.test import SimpleTestCase

from cards.views import _build_overlay_polygons


def _poly(x0, y0, w, h):
    return {
        "top_left": {"x": x0, "y": y0},
        "top_right": {"x": x0 + w, "y": y0},
        "bottom_right": {"x": x0 + w, "y": y0 + h},
        "bottom_left": {"x": x0, "y": y0 + h},
    }


def _debug_json(integrated, manual=None):
    dj = {
        "image_size": {"width": 1000, "height": 1000, "area": 1_000_000},
        "integrated_results": integrated,
    }
    if manual is not None:
        dj["manual_results"] = manual
    return dj


class BuildOverlayPolygonsManualTests(SimpleTestCase):
    def setUp(self):
        # 自動枠2件（giant 判定が走る最小=2件）。1件は巨大にして is_giant=True を発生させる。
        self.integrated = [
            {"card_index": 0, "polygon": _poly(10, 10, 100, 60)},
            {"card_index": 1, "polygon": _poly(10, 200, 800, 480)},  # 面積大 → giant
        ]
        self.manual = [
            {"business_card_id": "bc-A", "polygon": _poly(500, 500, 200, 120)},
        ]

    def _auto(self, items):
        return [p for p in items if not p["is_manual"]]

    def _manual(self, items):
        return [p for p in items if p["is_manual"]]

    def test_manual_items_added_with_is_manual_true(self):
        items = _build_overlay_polygons(_debug_json(self.integrated, self.manual))
        manual = self._manual(items)
        self.assertEqual(len(manual), 1)
        self.assertTrue(all(p["is_manual"] for p in manual))
        # points_str は TL→TR→BR→BL 順（自動枠と同形式）。
        self.assertEqual(
            manual[0]["points_str"], "500.0,500.0 700.0,500.0 700.0,620.0 500.0,620.0"
        )
        # 重心
        self.assertEqual(manual[0]["centroid_x"], 600.0)
        self.assertEqual(manual[0]["centroid_y"], 560.0)

    def test_manual_is_giant_always_false(self):
        # 手動枠を画像全面（巨大）にしても is_giant は False のまま。
        big_manual = [{"business_card_id": "bc-big", "polygon": _poly(0, 0, 999, 999)}]
        items = _build_overlay_polygons(_debug_json(self.integrated, big_manual))
        self.assertTrue(all(p["is_giant"] is False for p in self._manual(items)))

    def test_auto_unchanged_by_manual_presence(self):
        without = _build_overlay_polygons(_debug_json(self.integrated))
        with_manual = _build_overlay_polygons(_debug_json(self.integrated, self.manual))

        auto_without = self._auto(without)
        auto_with = self._auto(with_manual)
        # 自動枠の数・points_str・is_giant・card_index が手動枠の有無で不変。
        self.assertEqual(len(auto_without), len(auto_with))
        for a, b in zip(auto_without, auto_with):
            self.assertEqual(a["card_index"], b["card_index"])
            self.assertEqual(a["points_str"], b["points_str"])
            self.assertEqual(a["is_giant"], b["is_giant"])
            self.assertEqual(a["area_ratio"], b["area_ratio"])
        # giant 判定が実際に1件立っていること（母集団が手動で歪まない前提の確認）。
        self.assertEqual(sum(1 for p in auto_with if p["is_giant"]), 1)
        self.assertEqual(sum(1 for p in auto_without if p["is_giant"]), 1)

    def test_all_auto_items_have_is_manual_false(self):
        items = _build_overlay_polygons(_debug_json(self.integrated, self.manual))
        self.assertTrue(all(p["is_manual"] is False for p in self._auto(items)))

    def test_no_manual_key(self):
        items = _build_overlay_polygons(_debug_json(self.integrated))
        self.assertEqual(len(self._manual(items)), 0)
        self.assertEqual(len(self._auto(items)), 2)

    def test_empty_manual_list(self):
        items = _build_overlay_polygons(_debug_json(self.integrated, []))
        self.assertEqual(len(self._manual(items)), 0)

    def test_debug_json_none(self):
        self.assertEqual(_build_overlay_polygons(None), [])

    def test_manual_only_no_integrated(self):
        # 自動枠ゼロでも手動枠は出る（合流が integrated に依存しない）。
        items = _build_overlay_polygons(_debug_json([], self.manual))
        self.assertEqual(len(self._auto(items)), 0)
        self.assertEqual(len(self._manual(items)), 1)
