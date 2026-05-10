"""contacts アプリの View 層。

D-3c で追加した AJAX 2 エンドポイントを実装する。仕様書 §10.6.4 ケース 4
（AJAX 個別フィールド修正・確認）を担う。

認証方針（仕様書 §18.1 / D-3c 論点 1）：
  v1.4.2 では認証が仮実装のため、AJAX エンドポイントだけ先行して 403 ガードを入れる。
  LoginRequiredMixin は使わず、未認証時は JSON で 403 を返す（302 リダイレクトだと
  AJAX で扱いにくいため）。

ガード方針（指示書 §3.6 / 補足）：
  - 認証必須：未認証は 403
  - Contact.status は 'primary' / 'active' のみ受け付ける（'inactive' は 403）
  - Person.status は 'active' のみ受け付ける（archived / merged 等は 403）
  - CSRF は Django デフォルト保護、@csrf_exempt は使わない
"""

import json

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST

from .models import Contact, ContactFieldConfidence


def _error(message, status):
    """エラー用 JsonResponse の統一形（D-3c 論点 7）。"""
    return JsonResponse({"success": False, "error": message}, status=status)


def _unconfirmed_count(contact):
    """当該 Contact 内の未確認 low/mid フィールド数（D-3c 論点 6）。

    ContactFieldConfidence は low/mid のみレコードが存在する設計（§10.6 / §4.6.1）の
    ため、`confirmed_at IS NULL` のレコード数 = 未確認 low/mid フィールド数。
    """
    return ContactFieldConfidence.objects.filter(
        contact=contact, confirmed_at__isnull=True
    ).count()


@method_decorator(require_POST, name="dispatch")
class _ContactAjaxBase(View):
    """AJAX 2 View の共通処理（認証 / Contact 取得 / ガード / JSON パース）。

    POST 限定（require_POST デコレータ）。CSRF は Django ミドルウェアで自動チェック。
    """

    def dispatch(self, request, *args, **kwargs):
        # 認証チェック（D-3c 論点 1、案 A：未認証は JSON 403）
        if not request.user.is_authenticated:
            return _error("Authentication required", 403)
        return super().dispatch(request, *args, **kwargs)

    def _get_contact_or_error(self, pk):
        """Contact を取得し、ガード違反なら (None, error_response) を返す。

        戻り値：(contact, None) または (None, JsonResponse)
        """
        try:
            contact = Contact.objects.select_related("person").get(pk=pk)
        except Contact.DoesNotExist:
            return None, _error("Contact not found", 404)

        # Contact.status ガード（'inactive' を弾く）
        if contact.status not in (Contact.Status.PRIMARY, Contact.Status.ACTIVE):
            return None, _error("Cannot edit this contact", 403)

        # Person.status ガード（archived / merged を弾く）
        if contact.person.status != "active":
            return None, _error("Cannot edit this contact", 403)

        return contact, None

    def _parse_json_body(self, request):
        """request.body を JSON としてパース。失敗時は (None, error_response)。"""
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return None, _error("Invalid JSON", 400)
        if not isinstance(data, dict):
            return None, _error("Invalid JSON", 400)
        return data, None


class ContactAjaxUpdateFieldView(_ContactAjaxBase):
    """1 フィールド値の修正 + 自動 confirmed 化（仕様書 §10.6.4 ケース 4）。

    リクエスト：JSON `{"field_name": str, "new_value": Any}`
    レスポンス：JSON `{"success": True, "field_name": ..., "updated_value": ...,
                       "confidence_state": "confirmed", "unconfirmed_count": int}`

    内部で Contact.update_field() を呼び、値修正 + 当該フィールドの CFC confirmed 化 +
    DUPLICATE_CHECK_FIELDS なら invalidate_pending_candidates を 1 トランザクションで実行。
    """

    def post(self, request, pk):
        contact, err = self._get_contact_or_error(pk)
        if err is not None:
            return err

        data, err = self._parse_json_body(request)
        if err is not None:
            return err

        field_name = data.get("field_name")
        if not isinstance(field_name, str) or not field_name:
            return _error("field_name is required", 400)

        if field_name not in Contact.UPDATABLE_FIELDS:
            return _error(f"Invalid field name: {field_name}", 400)

        # new_value はキー存在チェックのみ。値型は呼び出し側 / モデル側で扱う
        if "new_value" not in data:
            return _error("new_value is required", 400)
        new_value = data["new_value"]

        try:
            contact.update_field(field_name, new_value, request.user)
        except ValueError as exc:
            return _error(str(exc), 400)

        contact.refresh_from_db()
        return JsonResponse(
            {
                "success": True,
                "field_name": field_name,
                "updated_value": getattr(contact, field_name),
                "confidence_state": "confirmed",
                "unconfirmed_count": _unconfirmed_count(contact),
            }
        )


class ContactAjaxConfirmFieldsView(_ContactAjaxBase):
    """confidence 確認のみ（値修正なし、個別 / 一括両用、仕様書 §10.6.4 ケース 4）。

    リクエスト：JSON `{"field_names": [str, ...]}`
    レスポンス：JSON `{"success": True, "confirmed_field_names": [...],
                       "unconfirmed_count": int}`

    値（new_value）は絶対に扱わない（指示書 §3.5 責務分離）。CFC レコードがない疑似 high
    フィールドが含まれていても mark_fields_as_confirmed が no-op で扱う（D-3c 論点 8）。
    field_names が空配列なら no-op で 200 を返す（D-3c 論点 5）。
    """

    def post(self, request, pk):
        contact, err = self._get_contact_or_error(pk)
        if err is not None:
            return err

        data, err = self._parse_json_body(request)
        if err is not None:
            return err

        field_names = data.get("field_names")
        if not isinstance(field_names, list):
            return _error("field_names must be a list", 400)

        # 各要素が UPDATABLE_FIELDS に含まれることを確認
        for fn in field_names:
            if not isinstance(fn, str) or fn not in Contact.UPDATABLE_FIELDS:
                return _error(f"Invalid field name: {fn}", 400)

        # 空配列なら no-op（D-3c 論点 5）
        ContactFieldConfidence.mark_fields_as_confirmed(
            contact, field_names, request.user
        )

        return JsonResponse(
            {
                "success": True,
                "confirmed_field_names": list(field_names),
                "unconfirmed_count": _unconfirmed_count(contact),
            }
        )
