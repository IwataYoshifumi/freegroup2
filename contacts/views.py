"""contacts アプリの View 層。

D-3c で追加した AJAX 2 エンドポイント、および D-3b で追加した Contact 詳細画面を
実装する。仕様書 §10.6.4 ケース 4（AJAX 個別フィールド修正・確認）と
§11.3 11 番（Contact 詳細画面）を担う。

認証方針：
  v1.4.2 では認証が仮実装（仕様書 §18.1）。
  - AJAX エンドポイントは未認証で 403 JSON（D-3c 論点 1）
  - 詳細画面（GET）は既存 cards View 流儀の get_current_user 仮認証
    （D-3b 論点 1、未認証でもスーパーユーザー扱いで 200）

ガード方針（AJAX のみ、指示書 §3.6 / 補足）：
  - 認証必須：未認証は 403
  - Contact.status は 'primary' / 'active' のみ受け付ける（'inactive' は 403）
  - Person.status は 'active' のみ受け付ける（archived / merged 等は 403）
  - CSRF は Django デフォルト保護、@csrf_exempt は使わない
"""

import json

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView

from back_navigator.back_navigator import BackNavigator
from duplicates.models import DuplicateCandidate, PersonMergeLog

from .models import Contact, ContactFieldConfidence


User = get_user_model()


def get_current_user(request):
    """認証未実装のための仮処理（既存 cards/views.py と同じ実装）。

    request.user が認証済みならそれを返し、未認証なら最初のスーパーユーザーを返す。
    v1.4.2 は認証仮実装期（仕様書 §18.1）、本格的な認証は v1.5.0 以降で実装。
    """
    if request.user.is_authenticated:
        return request.user
    return User.objects.filter(is_superuser=True).first()


class ContactDetailView(DetailView):
    """Contact 詳細画面（仕様書 §11.3 11 番、D-3b）。

    GET 専用、業務メイン画面（active な Person を見る画面）。Contact.status と
    Person.status によって表示・操作モードが切り替わる：
      - 編集可能モード：Contact.status in ('primary', 'active') AND
                       Contact.person.status == 'active'
      - 表示のみモード：それ以外（inactive Contact / archived・merged Person 配下等）

    認証は既存 cards View と同じ仮認証スタイル（LoginRequiredMixin 未使用、
    D-3b 論点 1）。所有者フィルタなし（仕様書 §18.2、Contact は user フィールド
    を持たないため、全 Contact 対象）。

    関連 URL（修正 12/13、別肩書追加 9、Person 詳細 8、マージ 17、マージログ 20、
    重複候補 16）はすべて未実装のため、テンプレートではプレースホルダ「準備中」表示
    （D-3b 論点 3）。
    """

    model = Contact
    template_name = "contacts/contact_detail.html"
    context_object_name = "contact"
    pk_url_kwarg = "pk"

    def get_queryset(self):
        return Contact.objects.select_related(
            "person", "business_card", "previous_person"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contact = self.object

        # モード判定（D-3b §3.2）
        is_primary = contact.status == Contact.Status.PRIMARY
        is_active = contact.status == Contact.Status.ACTIVE
        is_inactive = contact.status == Contact.Status.INACTIVE
        is_editable = (
            contact.status in (Contact.Status.PRIMARY, Contact.Status.ACTIVE)
            and contact.person.status == "active"
        )

        # 他のアクティブコンタクト（D-3b 論点 4）
        if is_primary:
            other_active_contacts = contact.person.contact_set.filter(
                status=Contact.Status.ACTIVE
            )
        elif is_active:
            other_active_contacts = (
                contact.person.contact_set.filter(
                    status__in=[
                        Contact.Status.PRIMARY,
                        Contact.Status.ACTIVE,
                    ]
                )
                .exclude(pk=contact.pk)
            )
        else:
            other_active_contacts = Contact.objects.none()

        # 重複候補・マージログ・previous_person
        pending_duplicates = DuplicateCandidate.get_pending(contact)
        merge_logs = PersonMergeLog.get_for_person(contact.person)
        previous_person = contact.previous_person

        # BackNavigator（詳細画面なので push_current は呼ばない、既存 cards 慣例に揃える。
        # BackNavigator.push_current は keys=[] を DEBUG 時に ValueError として弾くため、
        # 詳細画面ではスタック生成だけ行い、一覧画面側で push_current 済みのスタックを参照する）
        back = BackNavigator(self.request)

        context.update(
            {
                "contact": contact,
                "field_confidences": contact.get_field_confidences(),
                "is_editable": is_editable,
                "is_primary": is_primary,
                "is_active": is_active,
                "is_inactive": is_inactive,
                "business_card": contact.business_card,
                "other_active_contacts": other_active_contacts,
                "pending_duplicates": pending_duplicates,
                "merge_logs": merge_logs,
                "previous_person": previous_person,
                "back": back,
                "active_app": "cards",
                "active_menu": "cards:card_list",
            }
        )
        return context


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
