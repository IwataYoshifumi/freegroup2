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

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, UpdateView

from back_navigator.back_navigator import BackNavigator
from cards.models import BusinessCard
from cards.tasks.ocr_pipeline import _update_original_image_status
from config.constants import PersonChangeReason
from duplicates.models import DuplicateCandidate
from duplicates.services.duplicate_detection import find_duplicate_contacts
from persons.models import Person

from .forms import (
    ContactCreateForm,
    ContactUpdateActiveForm,
    ContactUpdateForm,
    build_contact_sns_formset,
)
from .models import Contact, ContactFieldConfidence, ContactSns
from .services.detail_context import build_contact_detail_context
from .services.permissions import can_edit_contact


# ----------------------------------------------------------------------
# Contact 一覧の多段サーバー側ソート（HIG v1.5 §6.2、persons 実装に倣う）。
#
# 「画面でソートできる列＝許可リストにあるキー」を一致させ、それ以外は無視＝既定の並びに戻す。
# クエリは単一パラメータ sort に優先順つきカンマ区切り、降順は先頭 "-"。
#   例 ?sort=organization,-title,name （第1=会社昇順 / 第2=役職降順 / 第3=氏名昇順）
# 氏名は読み（phonetic_name）の五十音順。Contact 自身のフィールドなので直参照（join 不要）。
# ----------------------------------------------------------------------

CONTACT_LIST_SORT_FIELD_MAP = {
    "name": "phonetic_name",      # 氏名は読み（カタカナ）の五十音順
    "company": "organization",
    "title": "title",
    "email": "email",             # 連絡先列のソートキー
}
# 多段ソートの最大段数（ソートコントロールの行数と一致）。
CONTACT_LIST_SORT_MAX_KEYS = 3


def _parse_contact_sort(params):
    """単一 sort パラメータ（例 "company,-title,name"）を [(key, direction), ...] に解決する。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] params: QueryDict 様（request.GET）
    [出力] list[tuple[str, str]]（key は許可リスト内、direction は "asc" / "desc"）
        - 先頭 "-" は降順、無印は昇順。
        - 許可リスト外キー・空トークンは無視（不正値は黙って捨てる＝既定の並びに戻す）。
        - 同一キーの二重指定は先勝ちで除外。最大 CONTACT_LIST_SORT_MAX_KEYS 段まで。
    """
    raw = params.get("sort") or ""
    tokens = []
    seen = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("-"):
            direction = "desc"
            key = chunk[1:]
        else:
            direction = "asc"
            key = chunk
        if key not in CONTACT_LIST_SORT_FIELD_MAP or key in seen:
            continue
        seen.add(key)
        tokens.append((key, direction))
        if len(tokens) >= CONTACT_LIST_SORT_MAX_KEYS:
            break
    return tokens


def _apply_contact_list_sort(qs, params):
    """Contact QuerySet に多段ソート（?sort=key,-key,...）を適用する（純関数）。

    [性質] 純関数（QuerySet を加工して返すのみ・DB 操作なし）
    有効トークンが無ければ qs をそのまま返す（呼び出し側の既定並びを温存）。
    指定有りのときだけ許可リスト経由で order_by を多段で差し替える（末尾に pk で安定化）。
    """
    tokens = _parse_contact_sort(params)
    if not tokens:
        return qs
    order = []
    for key, direction in tokens:
        prefix = "-" if direction == "desc" else ""
        order.append(prefix + CONTACT_LIST_SORT_FIELD_MAP[key])
    order.append("pk")
    return qs.order_by(*order)


def _contact_sort_context(params):
    """ソート UI（検索フォーム内の折りたたみ）の描画用 context を作る（純関数）。

    [性質] 純関数（DB 操作なし・副作用なし）
    [出力] dict:
        sort_rows: CONTACT_LIST_SORT_MAX_KEYS 行分の [{"key", "dir"}]
                   （未指定行は key="" / dir="asc"）。各行が列ドロップダウン＋方向トグルに対応。
        sort_is_active: bool（有効トークンが 1 つでもあるか＝折りたたみの開閉初期状態の判定）。
        sort_value: 正規化済み sort 文字列（hidden の初期値・no-JS 再送信用）。
    """
    tokens = _parse_contact_sort(params)
    rows = [{"key": k, "dir": d} for (k, d) in tokens]
    while len(rows) < CONTACT_LIST_SORT_MAX_KEYS:
        rows.append({"key": "", "dir": "asc"})
    return {
        "sort_rows": rows,
        "sort_is_active": bool(tokens),
        "sort_value": ",".join(
            ("-" + k if d == "desc" else k) for k, d in tokens
        ),
    }


class ContactListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Contact 一覧画面（v1.4.2 仕様変更で追加、仕様書改訂は別途）。

    GET 専用。デフォルトは active Person 配下の primary / active のみ表示。
    検索フォームの「inactive を含める」チェックで inactive も表示できる
    （merged / archived Person 配下は常に除外）。検索仕様は CardListView と
    同形（7 フィールド AND、tel は personal_phone / mobile_phone / personal_fax の OR）。
    """

    # 認可（Phase 7 段3-2、rev20 No.23 ★2）：Contact 閲覧 → view_contact。
    permission_required = "contacts.view_contact"
    model = Contact
    template_name = "contacts/contact_list.html"
    context_object_name = "contacts"
    paginate_by = 20

    _SEARCH_PARAMS = (
        "name",
        "organization",
        "department",
        "title",
        "email",
        "tel",
        "address",
    )
    _VALID_STATUSES = ("primary", "active", "inactive")

    def _get_selected_statuses(self):
        """status チェック状態の解決。初回（searched なし）は primary のみ、
        検索後はチェックされた値そのまま（不正値は捨てる、空も許容で 0 件）。"""
        if self.request.GET.get("searched") != "1":
            return ["primary"]
        return [
            s
            for s in self.request.GET.getlist("status")
            if s in self._VALID_STATUSES
        ]

    def get_queryset(self):
        selected = self._get_selected_statuses()
        if not selected:
            return Contact.objects.none()

        qs = Contact.objects.filter(
            status__in=selected, person__status="active"
        ).select_related("person", "business_card")

        p = self.request.GET
        if p.get("name", "").strip():
            qs = qs.filter(full_name__icontains=p["name"].strip())
        if p.get("organization", "").strip():
            qs = qs.filter(organization__icontains=p["organization"].strip())
        if p.get("department", "").strip():
            qs = qs.filter(department__icontains=p["department"].strip())
        if p.get("title", "").strip():
            qs = qs.filter(title__icontains=p["title"].strip())
        if p.get("email", "").strip():
            qs = qs.filter(email__icontains=p["email"].strip())
        if p.get("tel", "").strip():
            tel = p["tel"].strip()
            qs = qs.filter(
                Q(personal_phone__icontains=tel)
                | Q(mobile_phone__icontains=tel)
                | Q(personal_fax__icontains=tel)
            )
        if p.get("address", "").strip():
            qs = qs.filter(address__icontains=p["address"].strip())

        # 既定の並びは更新日時降順。?sort= があれば許可リスト経由で多段ソートに差し替える。
        qs = qs.order_by("-updated_at", "-created_at")
        return _apply_contact_list_sort(qs, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current(
            "",
            [
                "name",
                "organization",
                "department",
                "title",
                "email",
                "tel",
                "address",
                "status",
                "searched",
                # HIG §6.1：並び替え・ページ状態を戻るで復元するため sort を追加（単一パラメータ）。
                "sort",
                "page",
            ],
        )
        context["back"] = back

        context["active_app"] = "contacts"
        context["active_menu"] = "contacts:contact_list"
        for key in self._SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        context["selected_statuses"] = self._get_selected_statuses()
        context["searched"] = self.request.GET.get("searched") == "1"
        # 検索フォーム内ソートコントロール用（sort_rows / sort_is_active / sort_value）。
        context.update(_contact_sort_context(self.request.GET))
        return context


class ContactDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
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

    # 認可（Phase 7 段3-2、rev20 No.11 ★2）：Contact 閲覧 → view_contact。
    permission_required = "contacts.view_contact"
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

        # 主役 Contact 1 枚分の表示 context は共通部品に集約（active 人物詳細と共有）。
        context.update(build_contact_detail_context(contact, self.request.user))

        # BackNavigator：コンタクト詳細を起点ハブ化し、子画面（別肩書追加・主コンタクト修正・名刺
        # 詳細・将来の案件/報告書 等）の「戻る」がここへ戻るよう、自身を push_current で積む
        # （ガイド §9：クエリ無し詳細でも push_current して戻り先にする正規の使い方）。
        # 保存すべき GET クエリを持たないため、keys は path-only になる無害な非空キー
        # 1 つ（"page"）を渡す（空 keys は §8 NG・DEBUG で ValueError）。view_name + view_kwargs
        # の重複チェック（back_navigator.py）でリロード・子からの戻り時の二重 push は防止される。
        back = BackNavigator(self.request)
        back.push_current("コンタクト詳細", ["page"])

        # 画面メタ（共通部品には含めない＝各 View の責務）。
        context.update(
            {
                "back": back,
                "page_title": "コンタクト詳細",
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

    ContactFieldConfidence は low/mid のみレコードが存在する設計（§10.6 / §4.6.1 /
    v1.6.0 で medium → mid 統一）のため、`confirmed_at IS NULL` のレコード数
    = 未確認 low/mid フィールド数。
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

    def _get_contact_or_error(self, pk, user):
        """Contact を取得し、ガード違反なら (None, error_response) を返す。

        戻り値：(contact, None) または (None, JsonResponse)
        """
        try:
            contact = Contact.objects.select_related("person").get(pk=pk)
        except Contact.DoesNotExist:
            return None, _error("Contact not found", 404)

        # 所有者ガード（Phase 7 段2-B、正本は services.permissions.can_edit_contact）。
        # created_by / managed_by 本人または横断権限保持者のみ編集可、それ以外は 403。
        if not can_edit_contact(user, contact):
            return None, _error("You do not have permission to edit this contact", 403)

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
        contact, err = self._get_contact_or_error(pk, request.user)
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
            # 引数エラー（未保存・不正 field_name 等）
            return _error(str(exc), 400)
        except ValidationError as exc:
            # データバリデーション失敗（salutation 空・full_name 空等）
            message = exc.messages[0] if exc.messages else str(exc)
            return _error(message, 400)

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
        contact, err = self._get_contact_or_error(pk, request.user)
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


class UpdatePrimaryContactView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """primary Contact 修正画面（12 番、仕様書 §11.3 / §11.4.1 / §11.4.2）。

    GET：フォーム表示。POST：change_reason の値で処理を分岐：
      - fix：contact.fix(form, user) で既存 Contact を上書き（仕様書 §10.5.2）
      - transfer / promotion / job_change / name_change：新規 Contact 作成 + 旧 primary を
        inactive 化 + Person.primary_contact 切り替え（_promote_new_contact_as_primary 経由）

    対象は status='primary' の Contact のみ。active / inactive は Http404。
    active 修正は別途 UpdateActiveContactView（13 番）が担当する。

    POST 成功時はリダイレクト先 = Person 詳細画面（persons:person_detail、self.object.person.pk）。
    fix / transfer 系どちらも Person 起点で結果を確認する動線（v9 セッション、たんたん判断）。

    BackNavigator：push_current は呼ばず、リダイレクト時に back.append_url() で back
    スタックを引き継ぐ（A-2-追加 で確立、b9f8776）。
    """

    # 認可（Phase 7 段3-2、rev20 No.12 ★2）：Contact 変更 → change_contact。
    permission_required = "contacts.change_contact"
    model = Contact
    form_class = ContactUpdateForm
    template_name = "contacts/contact_update_primary.html"
    pk_url_kwarg = "pk"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        # 所有者ガード（宿題F、正本は services.permissions.can_edit_contact）。
        # AJAX 経路（_ContactAjaxBase._get_contact_or_error）と同じ判定をフォーム経路にも
        # 効かせ、GET / POST どちらも get_object を通るため両方でガードされる。
        if not can_edit_contact(self.request.user, obj):
            raise PermissionDenied("You do not have permission to edit this contact.")
        if obj.status != Contact.Status.PRIMARY:
            raise Http404("This view is only for primary contacts.")
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["target_contact"] = self.object
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        context.update(
            {
                "back": back,
                "contact": self.object,
                "field_confidences": self.object.get_field_confidences(),
                "active_app": "contacts",
                "active_menu": "contacts:contact_list",
            }
        )
        # ContactSns 編集ブロック（§11.6.7）。POST 再描画時は form_invalid が
        # bound formset を渡すため、未指定（GET）のときだけ instance バインドで生成。
        if "sns_formset" not in context:
            context["sns_formset"] = build_contact_sns_formset(
                instance=self.object, prefix="sns"
            )
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        sns_formset = build_contact_sns_formset(
            data=request.POST, instance=self.object, prefix="sns"
        )
        if form.is_valid() and sns_formset.is_valid():
            return self.form_valid(form, sns_formset)
        return self.form_invalid(form, sns_formset)

    def form_valid(self, form, sns_formset):
        with transaction.atomic():
            change_reason = form.cleaned_data["change_reason"]
            if change_reason == PersonChangeReason.FIX:
                _fix_with_salutation_flag(self.object, form, self.request.user)
                # fix は既存 Contact のインプレース編集なので formset.save() で
                # 追加・更新・削除を反映する（instance は self.object）。
                sns_formset.save()
            else:
                new_contact = _promote_new_contact_as_primary(
                    form, self.object, self.request.user
                )
                # transfer 系は新規 Contact へ submit 内容を作り直す（旧 Contact の
                # ContactSns は時点スナップショットとして変更しない）。
                _create_sns_from_formset(new_contact, sns_formset)

        back = BackNavigator(self.request)
        target_url = reverse(
            "persons:person_detail",
            kwargs={"pk": self.object.person.pk},
        )
        return HttpResponseRedirect(back.append_url(target_url))

    def form_invalid(self, form, sns_formset):
        return self.render_to_response(
            self.get_context_data(form=form, sns_formset=sns_formset)
        )


class UpdateActiveContactView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """active Contact 修正画面（13 番、仕様書 §11.6 / §11.7）。

    GET：フォーム表示。POST：フォーム値で Contact.fix() を呼び、Contact 詳細画面へ
    リダイレクト（修正完了 → 結果確認、という業務フロー）。

    対象は status='active' の Contact のみ。primary / inactive は Http404。
    primary 修正は別途 UpdatePrimaryContactView（12 番、未実装）が担当する。

    POST 成功時の DB 書込責務は Contact.fix() が持つ（仕様書 §10.5.2）。本 View は
    form_valid() で fix() を呼ぶだけで、form.save() は呼ばない。

    BackNavigator：push_current は呼ばず（サポート担当補足 §3）、テンプレート側で
    {% back_url back %} を使ってキャンセル時の戻り先（呼び出し元）を解決する。
    """

    # 認可（Phase 7 段3-2、rev20 No.13 ★2）：Contact 変更 → change_contact。
    permission_required = "contacts.change_contact"
    model = Contact
    form_class = ContactUpdateActiveForm
    template_name = "contacts/contact_update_active.html"
    pk_url_kwarg = "pk"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        # 所有者ガード（宿題F、正本は services.permissions.can_edit_contact）。
        # AJAX 経路（_ContactAjaxBase._get_contact_or_error）と同じ判定をフォーム経路にも
        # 効かせ、GET / POST どちらも get_object を通るため両方でガードされる。
        if not can_edit_contact(self.request.user, obj):
            raise PermissionDenied("You do not have permission to edit this contact.")
        if obj.status != Contact.Status.ACTIVE:
            raise Http404("This view is only for active contacts.")
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["target_contact"] = self.object
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        context.update(
            {
                "back": back,
                "contact": self.object,
                "field_confidences": self.object.get_field_confidences(),
                "active_app": "contacts",
                "active_menu": "contacts:contact_list",
            }
        )
        if "sns_formset" not in context:
            context["sns_formset"] = build_contact_sns_formset(
                instance=self.object, prefix="sns"
            )
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        sns_formset = build_contact_sns_formset(
            data=request.POST, instance=self.object, prefix="sns"
        )
        if form.is_valid() and sns_formset.is_valid():
            return self.form_valid(form, sns_formset)
        return self.form_invalid(form, sns_formset)

    def form_valid(self, form, sns_formset):
        with transaction.atomic():
            _fix_with_salutation_flag(self.object, form, self.request.user)
            # active 修正は fix 相当のインプレース編集（instance は self.object）。
            sns_formset.save()
        back = BackNavigator(self.request)
        return HttpResponseRedirect(back.append_url(self.get_success_url()))

    def form_invalid(self, form, sns_formset):
        return self.render_to_response(
            self.get_context_data(form=form, sns_formset=sns_formset)
        )

    def get_success_url(self):
        return reverse(
            "contacts:contact_detail", kwargs={"pk": self.object.pk}
        )


# ======================================================================
# D-Form ステップ3a：10 番 ContactCreateView + 14 番 PreviewContactView
# ======================================================================


# 仕様書 §11.4.4：重複候補のうちユーザーに警告すべき rank のセット。
# 将来的に settings の DUPLICATE_WARNING_LEVEL で調整可能（本ステップ範囲外、別ストック）。
_DUPLICATE_WARNING_RANKS = (
    DuplicateCandidate.Rank.EXACT_MATCH,
    DuplicateCandidate.Rank.POSSIBLE_HIGH,
)

# rank 表示優先順位（exact_match を最優先）
_RANK_PRIORITY = {
    DuplicateCandidate.Rank.EXACT_MATCH: 0,
    DuplicateCandidate.Rank.POSSIBLE_HIGH: 1,
}


def _salutation_name_edited(form):
    """フォーム経路で salutation_name が編集されたか判定する（Phase D §3.6・View 層）。

    [性質] 純関数（form.changed_data を読むだけ・DB 操作なし）
    [入力] form: ContactBaseForm 系（changed_data を持つバリデーション済みフォーム）
    [出力] bool（salutation_name が changed_data に含まれれば True）

    OCR 経路（json_parser）は本判定を通さず salutation_name_is_manual=False のまま。
    手動 Form 経路でのみ、ユーザーが宛名を書き換えたかを Django 標準の changed_data で判定する。
    """
    return "salutation_name" in form.changed_data


def _create_sns_from_formset(contact, sns_formset):
    """ContactSnsFormSet の有効・非削除行から ContactSns を contact に作成する（§11.6.7）。

    [性質] 副作用あり（DB 書込：ContactSns）
    [入力] contact: Contact（保存済み）、sns_formset: bound かつ is_valid() 済みの ContactSnsFormSet
    [出力] None

    fix 経路（既存 Contact のインプレース編集）は formset.save() を使う。本関数は新規
    Contact 作成経路（10 番新規作成 / 9 番別肩書追加 / 12 番 transfer 系昇格）で、submit
    された行をそのまま新しい Contact 配下に作り直す。transfer 系では旧 Contact の
    ContactSns は変更しない（時点スナップショットとして保持する）。
    UniqueConstraint(contact, sns_type, sns_id) 違反は get_or_create で抑止する。
    """
    for form in sns_formset.forms:
        cleaned = getattr(form, "cleaned_data", None)
        if not cleaned or cleaned.get("DELETE"):
            continue
        sns_type = cleaned.get("sns_type")
        sns_id = (cleaned.get("sns_id") or "").strip()
        if not sns_type or not sns_id:
            continue
        ContactSns.objects.get_or_create(
            contact=contact, sns_type=sns_type, sns_id=sns_id
        )


def _fix_with_salutation_flag(contact, form, user):
    """Contact.fix() を呼びつつ salutation_name_is_manual を設定・永続化する（§3.6・View 層）。

    [性質] 副作用あり（DB 書込：Contact.fix() + 手動フラグ立て時の限定 save）
    [入力] contact: Contact（保存済み primary/active）、form: バリデーション済みフォーム、user: 操作者
    [出力] None

    salutation_name がフォームで編集された場合のみ手動フラグを True にする。Contact.save()
    の自動再計算（姓系フィールド変更時の宛名上書き）より前にフラグを立てる必要があるため、
    fix() 呼び出し前にメモリ上のフラグを立て、fix() 後に当該フィールドだけ限定 save で
    永続化する（salutation_name_is_manual は UPDATABLE_FIELDS 外で fix() の差分 save には
    載らないため）。編集がなければ既存値を維持する（False への戻しはしない、§3.6）。
    """
    edited = _salutation_name_edited(form)
    if edited:
        contact.salutation_name_is_manual = True
    contact.fix(form, user)
    if edited:
        contact.save(update_fields=["salutation_name_is_manual"])


@transaction.atomic
def _create_person_and_contact(form, user, business_card=None):
    """新規 Person + primary Contact を 1 トランザクションで作成（仕様書 §11.4.4）。

    [性質] 副作用あり（DB 書込：Person 1 件 + Contact 1 件 + Person.primary_contact 更新）
    [入力] form: ContactCreateForm（バリデーション済み、get_update_contact() を持つ）
           user: 操作実行者（Contact.created_by / updated_by に記録）
           business_card: BusinessCard | None（名刺詳細からの手動作成経路で、save 前に
              Contact.business_card へ OneToOne 紐づけする。画像なし従来作成は None）
    [出力] Contact（新規作成された primary Contact、保存済み）

    set_primary_contact() の内部 transaction.atomic は外側の atomic とネスト可能
    （Django 標準動作、savepoint 経由）。Person モデルは created_by / updated_by を
    持たない（persons/models.py 現状）ので Person.objects.create() には渡さない。

    business_card は v1.7 で追加した任意引数（後方互換：未指定なら従来どおり None）。
    BC の done 化・OriginalImage 集計遷移は呼び出し側（View）が同一トランザクション内で行う。
    """
    person = Person.objects.create(status=Person.Status.ACTIVE)
    contact = form.get_update_contact()
    contact.person = person
    contact.status = Contact.Status.PRIMARY
    contact.created_by = user
    contact.updated_by = user
    # 名刺詳細からの手動作成経路は save 前に BC を OneToOne 紐づけする（指示書 §3-1）。
    if business_card is not None:
        contact.business_card = business_card
    # §3.6：宛名がフォームで編集されていれば手動扱い（save 前に立てて自動再計算を抑止）。
    if _salutation_name_edited(form):
        contact.salutation_name_is_manual = True
    contact.save()
    person.set_primary_contact(contact)
    return contact


def _confirm_business_card_as_done(business_card):
    """手動作成した Contact の元 BC を done / business_card で確定する（指示書 §3-2,3-3、v1.7）。

    [性質] 副作用あり（DB 書込：BusinessCard 更新 + OriginalImage 集計遷移）
    [入力] business_card: BusinessCard（Contact 紐づけ済み、未 done を想定）
    [出力] None

    呼び出し側の transaction.atomic 内で Contact 作成と同一トランザクションで実行すること。
    BC を done 化しないまま Contact だけ作ると process_ocr が pending の BC を再度拾い、
    Contact を二重生成する（指示書「最大の地雷」）。OriginalImage.status は自前で書かず、
    既存集計ロジック _update_original_image_status に委ねる（全 BC 完了で extracted へ遷移）。
    """
    business_card.ocr_status = BusinessCard.OcrStatus.DONE
    business_card.ocr_result = BusinessCard.OcrResult.BUSINESS_CARD
    business_card.claimed_at = None
    business_card.save(
        update_fields=["ocr_status", "ocr_result", "claimed_at", "updated_at"]
    )
    _update_original_image_status(business_card.original_image_id)


@transaction.atomic
def _promote_new_contact_as_primary(form, target_contact, user):
    """新規 Contact を primary に昇格し、旧 primary を inactive 化する（仕様書 §11.4.1 / §11.4.2 / §10.4.3）。

    [性質] 副作用あり（DB 書込：Contact 1 件新規 + Contact 1 件更新 + Person.primary_contact 更新）
    [入力] form: ContactUpdateForm（バリデーション済み、get_update_contact() を呼べる）
           target_contact: Contact（旧 primary、保存済み）
           user: 操作実行者（新規 Contact の created_by / updated_by に記録）
    [出力] Contact（新規作成された primary Contact、保存済み）

    set_primary_contact() の内部 transaction.atomic は外側の atomic とネスト可能
    （Django 標準動作、savepoint 経由）。新規 Contact には ContactFieldConfidence を
    作らない（仕様書 §10.6.4 ケース1 / §11.4.2、全 high 扱い）。
    """
    new_contact = form.get_update_contact()
    new_contact.person = target_contact.person
    # set_primary_contact() が後で primary に昇格させるため、保存時は仮で active を入れる。
    new_contact.status = Contact.Status.ACTIVE
    new_contact.created_by = user
    new_contact.updated_by = user
    # §3.6：宛名がフォームで編集されていれば手動扱い（save 前に立てて自動再計算を抑止）。
    if _salutation_name_edited(form):
        new_contact.salutation_name_is_manual = True
    new_contact.save()
    target_contact.person.set_primary_contact(
        new_contact, old_primary_new_status="inactive"
    )
    return new_contact


class ContactCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """手動 Contact 新規作成画面（10 番、仕様書 §11.4.4 / §11.6.5）。

    GET：空フォームを表示。
    POST：
      1. フォーム検証 → NG なら同じテンプレートでエラー再表示
      2. 検証 OK なら find_duplicate_contacts() で重複候補取得
      3. 候補のうち rank ∈ (exact_match, possible_high) がある → 確認画面表示
         （上位 5 件 + 「+他 N 件」、強制作成は force_create フラグで再 POST）
      4. 候補なし → 1 トランザクションで Person + primary Contact を作成、
         Contact 詳細画面（11 番）へリダイレクト

    v1.7：名刺詳細から business_card クエリ付きで遷移してきた場合、その BC（未紐づけ）
    に Contact を OneToOne 紐づけし、保存と同一トランザクションで BC を done 確定する。
    business_card が無い従来の画像なし手動作成は完全に後方互換（指示書「やらないこと」）。

    OCR を経由しないユーザー直接入力のため ContactFieldConfidence は作らない
    （仕様書 §10.6.4、PersonAddAdditionalRoleView と同じ流儀）。
    """

    # 認可（Phase 7 段3-2、rev20 No.10 ★2）：Contact 作成 → add_contact。
    permission_required = "contacts.add_contact"
    template_name = "contacts/contact_create.html"
    duplicates_template_name = "contacts/contact_create_duplicates.html"

    def get(self, request):
        business_card, err = self._resolve_business_card(request)
        if err is not None:
            return err
        form = ContactCreateForm()
        sns_formset = build_contact_sns_formset(instance=None, prefix="sns")
        return render(
            request,
            self.template_name,
            self._context(request, form, sns_formset, business_card),
        )

    def post(self, request):
        business_card, err = self._resolve_business_card(request)
        if err is not None:
            return err

        form = ContactCreateForm(request.POST)
        sns_formset = build_contact_sns_formset(
            data=request.POST, instance=None, prefix="sns"
        )
        if not (form.is_valid() and sns_formset.is_valid()):
            return render(
                request,
                self.template_name,
                self._context(request, form, sns_formset, business_card),
            )

        # 強制作成フラグ：重複検出をスキップして即保存（仕様書 §11.4.4、ステップ3b 論点1 案 B）。
        # 強制作成された Contact は後の cron で重複候補として再検出される。
        if "force_create" in request.POST:
            new_contact = self._save_contact(
                request, form, sns_formset, business_card
            )
            return self._redirect_to_detail(request, new_contact)

        # 検証 OK：未保存 Contact を生成して重複検出
        prospective = form.get_update_contact()
        candidates = find_duplicate_contacts(prospective)
        critical = [
            (c, s, r)
            for c, s, r in candidates
            if r in _DUPLICATE_WARNING_RANKS
        ]

        if critical:
            critical_sorted = sorted(
                critical,
                key=lambda x: (_RANK_PRIORITY.get(x[2], 99), -x[1]),
            )
            top5 = critical_sorted[:5]
            extra_count = max(0, len(critical_sorted) - 5)
            # 重複確認画面でも入力済み ContactSns 行を hidden で持ち回す（強制作成再 POST 用）。
            ctx = self._context(request, form, sns_formset, business_card)
            ctx.update(
                {
                    "top5": top5,
                    "extra_count": extra_count,
                }
            )
            return render(request, self.duplicates_template_name, ctx)

        # 候補なし：保存して Contact 詳細画面へリダイレクト
        new_contact = self._save_contact(request, form, sns_formset, business_card)
        return self._redirect_to_detail(request, new_contact)

    def _resolve_business_card(self, request):
        """business_card クエリ/hidden から BC を解決・検証する（指示書 §2、GET/POST 共通）。

        [性質] 副作用あり（検証失敗時 messages.error）。DB 読み取りのみ
        [出力] (business_card, error_response)
          - パラメータ無し          → (None, None)（画像なし従来作成、後方互換）
          - BC 不在 / id 不正        → (None, redirect 名刺一覧)
          - 既に Contact 紐づけ済み  → (None, redirect 名刺詳細)
          - 有効な未紐づけ BC        → (bc, None)

        GET は query、POST（force_create 再 POST は action 固定で query 無し）は body から読む
        ため、両方を or で拾う。
        """
        bc_id = request.GET.get("business_card") or request.POST.get("business_card")
        if not bc_id:
            return None, None
        try:
            bc = BusinessCard.objects.select_related("contact").get(pk=bc_id)
        except (BusinessCard.DoesNotExist, ValidationError, ValueError):
            messages.error(request, "指定された名刺が見つかりません。")
            return None, redirect("cards:card_list")
        # 既に Contact が紐づく BC への二重作成を弾く（指示書 §2 検証）。
        if getattr(bc, "contact", None) is not None:
            messages.error(request, "この名刺には既にコンタクトが紐づいています。")
            return None, redirect("cards:card_detail", pk=bc.pk)
        return bc, None

    def _save_contact(self, request, form, sns_formset, business_card):
        """Person + Contact 作成・SNS 作成・BC done 確定を 1 トランザクションで実行する。

        [性質] 副作用あり（DB 書込）。business_card が None なら従来どおり BC 処理はスキップ。
        """
        with transaction.atomic():
            new_contact = _create_person_and_contact(
                form, request.user, business_card=business_card
            )
            _create_sns_from_formset(new_contact, sns_formset)
            if business_card is not None:
                _confirm_business_card_as_done(business_card)
        return new_contact

    def _redirect_to_detail(self, request, new_contact):
        back = BackNavigator(request)
        target_url = reverse(
            "contacts:contact_detail",
            kwargs={"pk": new_contact.pk},
        )
        return HttpResponseRedirect(back.append_url(target_url))

    def _context(self, request, form, sns_formset=None, business_card=None):
        back = BackNavigator(request)
        return {
            "form": form,
            "sns_formset": sns_formset,
            "back": back,
            "business_card": business_card,
            "active_app": "contacts",
            "active_menu": "contacts:contact_create",
        }


class PreviewContactView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Contact プレビュー（14 番、仕様書 §11.3 / §11.4.4 AJAX 連携）。

    GET のみ。HTML フラグメント（_preview_modal_body.html）を返す。
    読み取り専用なので Contact / Person ステータスガードなし（archived / merged
    Person 配下の inactive Contact もプレビュー可、論点4 / §3.5）。

    用途：
      - ContactCreateView の重複候補リストから「詳細を見る」リンクで呼ばれる（§11.4.4）
      - 将来的に Contact 一覧画面のモーダルプレビューでも使用（§11.3 14 番）
    """

    # 認可（Phase 7 段3-2、rev20 No.14 ★2）：Contact 閲覧 → view_contact。
    permission_required = "contacts.view_contact"
    template_name = "contacts/_preview_modal_body.html"

    def get(self, request, pk):
        contact = get_object_or_404(
            Contact.objects.select_related("person", "business_card"),
            pk=pk,
        )
        return render(
            request,
            self.template_name,
            {
                "contact": contact,
                "field_confidences": contact.get_field_confidences(),
            },
        )
