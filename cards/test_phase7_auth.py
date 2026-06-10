"""Phase 7 認可ガードの v1.7 適用テスト（cards）。

main（6bd75df）の Phase 7（段1 認証ガード／段3-2 cards permission）を v1.7 の cards へ
適用したことの検証。対象は書き換え UploadView と v1.7 新設の ManualCrop / ManualPreview /
CardEdit / CardRotate。各 View で「未認証→302・権限なし→403・権限あり→正常（ゲート通過）」と、
owner 分離（他人の OriginalImage / BusinessCard を操作できない）を確認する。
"""

import json
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import BusinessCard, OriginalImage

User = get_user_model()


def _user_with(*codenames):
    """cards アプリの指定 permission のみを持つ一般ユーザーを作る（superuser ではない）。"""
    u = User.objects.create_user(username=f"p7_{uuid.uuid4().hex[:8]}", password="x")
    for cn in codenames:
        u.user_permissions.add(
            Permission.objects.get(codename=cn, content_type__app_label="cards")
        )
    return u


def _client(user=None):
    c = Client()
    if user is not None:
        c.force_login(user)
    return c


class Phase7UploadViewAuthTests(TestCase):
    """書き換え UploadView（複数アップロード）への cards.add_originalimage ガード。"""

    def _url(self):
        return reverse("cards:card_upload")

    def test_anonymous_redirects(self):
        self.assertEqual(_client().get(self._url()).status_code, 302)

    def test_without_permission_forbidden(self):
        self.assertEqual(_client(_user_with()).get(self._url()).status_code, 403)

    def test_with_add_originalimage_ok(self):
        self.assertEqual(
            _client(_user_with("add_originalimage")).get(self._url()).status_code, 200
        )


class Phase7ManualCropAuthTests(TestCase):
    """ManualCropView / ManualPreviewView への cards.add_businesscard ガード＋owner 分離。"""

    def setUp(self):
        self.owner = _user_with("add_businesscard")
        self.original = OriginalImage.objects.create(
            user=self.owner, status=OriginalImage.STATUS_PENDING
        )

    def _crop_url(self, pk=None):
        return reverse("originals:manual_crop", kwargs={"pk": pk or self.original.pk})

    def _preview_url(self, pk=None):
        return reverse(
            "originals:manual_crop_preview", kwargs={"pk": pk or self.original.pk}
        )

    def _post(self, client, url, body=None):
        return client.post(
            url, data=json.dumps(body or {}), content_type="application/json"
        )

    # ---- manual_crop ----
    def test_crop_anonymous_redirects(self):
        self.assertEqual(self._post(_client(), self._crop_url()).status_code, 302)

    def test_crop_without_permission_forbidden(self):
        self.assertEqual(
            self._post(_client(_user_with()), self._crop_url()).status_code, 403
        )

    def test_crop_with_permission_passes_gate(self):
        # 権限あり＋owner：認可ゲートを通過し View 本体に到達（空ボディなので入力検証で 400）。
        resp = self._post(_client(self.owner), self._crop_url(), {"points": None})
        self.assertNotEqual(resp.status_code, 403)
        self.assertEqual(resp.status_code, 400)

    def test_crop_non_owner_404(self):
        # 権限はあるが他人の OriginalImage → owner スコープで 404（操作不可）。
        stranger = _user_with("add_businesscard")
        resp = self._post(_client(stranger), self._crop_url())
        self.assertEqual(resp.status_code, 404)

    # ---- manual_crop_preview ----
    def test_preview_anonymous_redirects(self):
        self.assertEqual(self._post(_client(), self._preview_url()).status_code, 302)

    def test_preview_without_permission_forbidden(self):
        self.assertEqual(
            self._post(_client(_user_with()), self._preview_url()).status_code, 403
        )

    def test_preview_non_owner_404(self):
        stranger = _user_with("add_businesscard")
        self.assertEqual(
            self._post(_client(stranger), self._preview_url()).status_code, 404
        )


class Phase7CardEditRotateAuthTests(TestCase):
    """CardEditView / CardRotateView への cards.change_businesscard ガード＋owner 分離。"""

    def setUp(self):
        self.owner = _user_with("change_businesscard")
        self.original = OriginalImage.objects.create(
            user=self.owner, status=OriginalImage.STATUS_EXTRACTED
        )
        # Contact 未生成の BC（編集・回転の対象条件）。
        self.bc = BusinessCard.objects.create(
            original_image=self.original, card_index=0
        )

    def _edit_url(self, pk=None):
        return reverse("cards:card_edit", kwargs={"pk": pk or self.bc.pk})

    def _rotate_url(self, pk=None):
        return reverse("cards:card_rotate", kwargs={"pk": pk or self.bc.pk})

    # ---- card_edit (GET) ----
    def test_edit_anonymous_redirects(self):
        self.assertEqual(_client().get(self._edit_url()).status_code, 302)

    def test_edit_without_permission_forbidden(self):
        self.assertEqual(_client(_user_with()).get(self._edit_url()).status_code, 403)

    def test_edit_with_permission_ok(self):
        self.assertEqual(_client(self.owner).get(self._edit_url()).status_code, 200)

    def test_edit_non_owner_404(self):
        stranger = _user_with("change_businesscard")
        self.assertEqual(_client(stranger).get(self._edit_url()).status_code, 404)

    # ---- card_rotate (POST) ----
    def test_rotate_anonymous_redirects(self):
        self.assertEqual(
            _client().post(self._rotate_url(), {"direction": "cw"}).status_code, 302
        )

    def test_rotate_without_permission_forbidden(self):
        self.assertEqual(
            _client(_user_with()).post(
                self._rotate_url(), {"direction": "cw"}
            ).status_code,
            403,
        )

    def test_rotate_with_permission_passes_gate(self):
        # 権限あり＋owner：ゲート通過し View 本体に到達（card_image 無しなので 302 で編集へ戻す）。
        resp = _client(self.owner).post(self._rotate_url(), {"direction": "cw"})
        self.assertNotEqual(resp.status_code, 403)
        self.assertEqual(resp.status_code, 302)

    def test_rotate_non_owner_404(self):
        stranger = _user_with("change_businesscard")
        self.assertEqual(
            _client(stranger).post(
                self._rotate_url(), {"direction": "cw"}
            ).status_code,
            404,
        )
