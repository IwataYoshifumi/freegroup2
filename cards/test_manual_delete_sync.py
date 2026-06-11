"""手動BC削除時の manual_results 同期（post_delete シグナル, 宿題A）のテスト。

- 手動BC削除 → debug_json.manual_results から該当エントリが消える（他エントリ・他キー温存）。
- 自動BC削除（manual_results に無い）→ manual_results 不変・他キー不変・例外なし。
- 親 OriginalImage ごと削除（CASCADE）→ 例外なく素通り（同期不要）。
- manual_results キー自体が無い debug_json でも no-op・例外なし。
"""

import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from cards.models import BusinessCard, OriginalImage

User = get_user_model()


def _poly(x0, y0):
    return {
        "top_left": {"x": x0, "y": y0}, "top_right": {"x": x0 + 100, "y": y0},
        "bottom_right": {"x": x0 + 100, "y": y0 + 60}, "bottom_left": {"x": x0, "y": y0 + 60},
    }


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="fg2_manual_delsync_test_"))
class ManualResultsDeleteSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="del_sync", password="d")

    def _original(self, debug_json=None):
        return OriginalImage.objects.create(
            user=self.user,
            status=OriginalImage.STATUS_CARDS_EXTRACTED,
            debug_json=debug_json,
        )

    def _bc(self, original, card_index):
        return BusinessCard.objects.create(
            original_image=original, card_index=card_index,
            ocr_status=BusinessCard.OcrStatus.PENDING,
        )

    def test_manual_bc_delete_removes_its_entry(self):
        original = self._original()
        bc_a = self._bc(original, 0)
        bc_b = self._bc(original, 1)
        original.debug_json = {
            "image_size": {"width": 400, "height": 300, "area": 120000},
            "attempts": [{"attempt_no": 1, "type": "normal"}],
            "integrated_results": [{"card_index": 0}],
            "manual_results": [
                {"business_card_id": str(bc_a.id), "polygon": _poly(10, 10)},
                {"business_card_id": str(bc_b.id), "polygon": _poly(200, 10)},
            ],
        }
        original.save(update_fields=["debug_json"])

        bc_a.delete()

        original.refresh_from_db()
        manual = original.debug_json["manual_results"]
        ids = [m["business_card_id"] for m in manual]
        self.assertEqual(ids, [str(bc_b.id)])              # A 除去・B 温存
        self.assertEqual(original.debug_json["attempts"], [{"attempt_no": 1, "type": "normal"}])
        self.assertEqual(original.debug_json["integrated_results"], [{"card_index": 0}])

    def test_auto_bc_delete_leaves_manual_results_unchanged(self):
        original = self._original()
        manual_bc = self._bc(original, 0)
        auto_bc = self._bc(original, 1)
        before = {
            "image_size": {"width": 400, "height": 300, "area": 120000},
            "integrated_results": [{"card_index": 0}, {"card_index": 1}],
            "manual_results": [
                {"business_card_id": str(manual_bc.id), "polygon": _poly(10, 10)},
            ],
        }
        original.debug_json = dict(before)
        original.save(update_fields=["debug_json"])

        auto_bc.delete()   # manual_results に無い BC

        original.refresh_from_db()
        # manual_results も他キーも完全不変。
        self.assertEqual(original.debug_json["manual_results"], before["manual_results"])
        self.assertEqual(original.debug_json["integrated_results"], before["integrated_results"])
        self.assertEqual(original.debug_json["image_size"], before["image_size"])

    def test_no_manual_results_key_is_noop(self):
        original = self._original(debug_json={"integrated_results": [{"card_index": 0}]})
        bc = self._bc(original, 0)

        bc.delete()   # 例外が出ないこと・他キー不変

        original.refresh_from_db()
        self.assertEqual(original.debug_json, {"integrated_results": [{"card_index": 0}]})

    def test_debug_json_none_is_noop(self):
        original = self._original(debug_json=None)
        bc = self._bc(original, 0)
        bc.delete()   # 例外なし
        original.refresh_from_db()
        self.assertIsNone(original.debug_json)

    def test_cascade_parent_delete_no_error(self):
        original = self._original()
        bc = self._bc(original, 0)
        original.debug_json = {
            "manual_results": [{"business_card_id": str(bc.id), "polygon": _poly(10, 10)}],
        }
        original.save(update_fields=["debug_json"])
        oid = original.id

        # 親ごと削除 → BC は CASCADE 削除され post_delete が発火するが、
        # 親が消えているので同期処理は no-op で例外なく完了する。
        original.delete()

        self.assertFalse(OriginalImage.objects.filter(id=oid).exists())
        self.assertEqual(BusinessCard.objects.filter(id=bc.id).count(), 0)
