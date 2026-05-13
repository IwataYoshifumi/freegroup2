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
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, UpdateView

from back_navigator.back_navigator import BackNavigator
from config.constants import PersonChangeReason
from duplicates.models import DuplicateCandidate, PersonMergeLog
from duplicates.services.duplicate_detection import find_duplicate_contacts
from persons.models import Person

from .forms import ContactCreateForm, ContactUpdateActiveForm, ContactUpdateForm
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


class ContactListView(ListView):
    """Contact 一覧画面（v1.4.2 仕様変更で追加、仕様書改訂は別途）。

    GET 専用。デフォルトは active Person 配下の primary / active のみ表示。
    検索フォームの「inactive を含める」チェックで inactive も表示できる
    （merged / archived Person 配下は常に除外）。検索仕様は CardListView と
    同形（7 フィールド AND、tel は phone / mobile / fax の OR）。
    """

    model = Contact
    template_name = "contacts/contact_list.html"
    context_object_name = "contacts"
    paginate_by = 20

    _SEARCH_PARAMS = (
        "name",
        "company",
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
        if p.get("company", "").strip():
            qs = qs.filter(company__icontains=p["company"].strip())
        if p.get("department", "").strip():
            qs = qs.filter(department__icontains=p["department"].strip())
        if p.get("title", "").strip():
            qs = qs.filter(title__icontains=p["title"].strip())
        if p.get("email", "").strip():
            qs = qs.filter(email__icontains=p["email"].strip())
        if p.get("tel", "").strip():
            tel = p["tel"].strip()
            qs = qs.filter(
                Q(phone__icontains=tel)
                | Q(mobile__icontains=tel)
                | Q(fax__icontains=tel)
            )
        if p.get("address", "").strip():
            qs = qs.filter(address__icontains=p["address"].strip())

        return qs.order_by("-updated_at", "-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current(
            "コンタクト一覧",
            [
                "name",
                "company",
                "department",
                "title",
                "email",
                "tel",
                "address",
                "status",
                "searched",
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
        return context


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

        # 同一 Person 配下の inactive Contact 履歴（自分自身は除外）。
        # active / primary のときは exclude は no-op、自分が inactive なら自身を弾く。
        inactive_contacts = (
            contact.person.get_inactive_contacts().exclude(pk=contact.pk)
        )

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
                "inactive_contacts": inactive_contacts,
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


class UpdatePrimaryContactView(LoginRequiredMixin, UpdateView):
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

    model = Contact
    form_class = ContactUpdateForm
    template_name = "contacts/contact_update_primary.html"
    pk_url_kwarg = "pk"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
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
        return context

    def form_valid(self, form):
        change_reason = form.cleaned_data["change_reason"]
        if change_reason == PersonChangeReason.FIX:
            self.object.fix(form, self.request.user)
        else:
            _promote_new_contact_as_primary(
                form, self.object, self.request.user
            )

        back = BackNavigator(self.request)
        target_url = reverse(
            "persons:person_detail",
            kwargs={"pk": self.object.person.pk},
        )
        return HttpResponseRedirect(back.append_url(target_url))


class UpdateActiveContactView(LoginRequiredMixin, UpdateView):
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

    model = Contact
    form_class = ContactUpdateActiveForm
    template_name = "contacts/contact_update_active.html"
    pk_url_kwarg = "pk"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
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
        return context

    def form_valid(self, form):
        self.object.fix(form, self.request.user)
        back = BackNavigator(self.request)
        return HttpResponseRedirect(back.append_url(self.get_success_url()))

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


@transaction.atomic
def _create_person_and_contact(form, user):
    """新規 Person + primary Contact を 1 トランザクションで作成（仕様書 §11.4.4）。

    [性質] 副作用あり（DB 書込：Person 1 件 + Contact 1 件 + Person.primary_contact 更新）
    [入力] form: ContactCreateForm（バリデーション済み、get_update_contact() を持つ）
           user: 操作実行者（Contact.created_by / updated_by に記録）
    [出力] Contact（新規作成された primary Contact、保存済み）

    set_primary_contact() の内部 transaction.atomic は外側の atomic とネスト可能
    （Django 標準動作、savepoint 経由）。Person モデルは created_by / updated_by を
    持たない（persons/models.py 現状）ので Person.objects.create() には渡さない。
    """
    person = Person.objects.create(status=Person.Status.ACTIVE)
    contact = form.get_update_contact()
    contact.person = person
    contact.status = Contact.Status.PRIMARY
    contact.created_by = user
    contact.updated_by = user
    contact.save()
    person.set_primary_contact(contact)
    return contact


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
    new_contact.save()
    target_contact.person.set_primary_contact(
        new_contact, old_primary_new_status="inactive"
    )
    return new_contact


class ContactCreateView(LoginRequiredMixin, View):
    """手動 Contact 新規作成画面（10 番、仕様書 §11.4.4 / §11.6.5）。

    GET：空フォームを表示。
    POST：
      1. フォーム検証 → NG なら同じテンプレートでエラー再表示
      2. 検証 OK なら find_duplicate_contacts() で重複候補取得
      3. 候補のうち rank ∈ (exact_match, possible_high) がある → 確認画面表示
         （上位 5 件 + 「+他 N 件」、強制作成はステップ3b で実装、本ステップでは
         disabled プレースホルダー）
      4. 候補なし → 1 トランザクションで Person + primary Contact を作成、
         Contact 詳細画面（11 番）へリダイレクト

    OCR を経由しないユーザー直接入力のため ContactFieldConfidence は作らない
    （仕様書 §10.6.4、PersonAddAdditionalRoleView と同じ流儀）。
    """

    template_name = "contacts/contact_create.html"
    duplicates_template_name = "contacts/contact_create_duplicates.html"

    def get(self, request):
        form = ContactCreateForm()
        return render(request, self.template_name, self._context(request, form))

    def post(self, request):
        form = ContactCreateForm(request.POST)
        if not form.is_valid():
            return render(
                request, self.template_name, self._context(request, form)
            )

        # 強制作成フラグ：重複検出をスキップして即保存（仕様書 §11.4.4、ステップ3b 論点1 案 B）。
        # 強制作成された Contact は後の cron で重複候補として再検出される。
        if "force_create" in request.POST:
            new_contact = _create_person_and_contact(form, request.user)
            back = BackNavigator(request)
            target_url = reverse(
                "contacts:contact_detail",
                kwargs={"pk": new_contact.pk},
            )
            return HttpResponseRedirect(back.append_url(target_url))

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
            ctx = self._context(request, form)
            ctx.update(
                {
                    "top5": top5,
                    "extra_count": extra_count,
                }
            )
            return render(request, self.duplicates_template_name, ctx)

        # 候補なし：保存して Contact 詳細画面へリダイレクト
        new_contact = _create_person_and_contact(form, request.user)
        back = BackNavigator(request)
        target_url = reverse(
            "contacts:contact_detail",
            kwargs={"pk": new_contact.pk},
        )
        return HttpResponseRedirect(back.append_url(target_url))

    def _context(self, request, form):
        back = BackNavigator(request)
        return {
            "form": form,
            "back": back,
            "active_app": "contacts",
            "active_menu": "contacts:contact_create",
        }


class PreviewContactView(LoginRequiredMixin, View):
    """Contact プレビュー（14 番、仕様書 §11.3 / §11.4.4 AJAX 連携）。

    GET のみ。HTML フラグメント（_preview_modal_body.html）を返す。
    読み取り専用なので Contact / Person ステータスガードなし（archived / merged
    Person 配下の inactive Contact もプレビュー可、論点4 / §3.5）。

    用途：
      - ContactCreateView の重複候補リストから「詳細を見る」リンクで呼ばれる（§11.4.4）
      - 将来的に Contact 一覧画面のモーダルプレビューでも使用（§11.3 14 番）
    """

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
