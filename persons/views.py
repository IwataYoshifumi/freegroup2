"""persons アプリの View 層（仕様書 v1.4.2 §11.4 / §11.5）。

PersonListView：人物一覧画面（URL 7 番）。検索フォーム + status フィルタ + ページネーション。
PersonDetailView：人物詳細画面（URL 8 番）。Person.status で 4 分岐。
  - active + primary_contact あり → ContactDetailView へリダイレクト
  - active + primary_contact NULL → orphan 専用画面（Django Admin 誘導）
  - merged → merged 専用画面（merged_into / マージ履歴 / 残存 Contact）
  - archived → archived 専用画面（マージ履歴 / inactive 履歴）
PersonAddAdditionalRoleView：別肩書追加画面（URL 9 番、D-Form ステップ2）。
  active Person 配下に新規 active Contact を作成。CFC は作らない（§10.12）。

認証・認可：全 View が LoginRequiredMixin（Phase 7 段1 で付与）。さらに
PersonListView / PersonDetailView は PermissionRequiredMixin で persons.view_person
を要求する（URL一覧表 rev20 No.7 / No.8 ★1、Phase 7 段3-1）。owner / 閲覧スコープ
判定は持たない（Person は user FK を持たず全 Person 対象、owner スコープは v1.7+ 先送り）。
PersonAddAdditionalRoleView は書込系で LoginRequiredMixin のみ（権限は★2未確定）。
"""

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import DetailView, ListView
from django.views.generic.edit import FormView

from accounts.services import is_self_link_email_match
from back_navigator.back_navigator import BackNavigator
from contacts.forms import ContactAddAdditionalRoleForm, build_contact_sns_formset
from contacts.models import Contact
from contacts.services.detail_context import build_contact_detail_context
from contacts.views import _create_sns_from_formset
from duplicates.models import PersonMergeLog

from .models import Person
from .services.person_search import SEARCH_PARAMS, search_persons


# ----------------------------------------------------------------------
# 人物一覧の多段ソート（HIG 第6章。検索フォーム内のソートコントロール一本化）。
# 共通化はしない＝persons 内で完結。許可リストは person_list.html に実在する
# ソート可能ヘッダ 4 列だけ：氏名 / 会社 / 役職 / 連絡先(メール)。
# 「画面に出ている列＝ソートできる列＝許可リストにあるキー」を一致させ、部署・住所等の
# 使わない経路は残さない（不正キーは無視＝既定の並びに戻す）。
#
# クエリは単一パラメータ sort に優先順つきでカンマ区切り、降順は先頭 "-"。
#   例 ?sort=company,-title,name （第1=会社昇順 / 第2=役職降順 / 第3=氏名昇順）
# dir パラメータは廃止。並びは primary_contact 経由。
# ----------------------------------------------------------------------

PERSON_LIST_SORT_FIELD_MAP = {
    # 氏名は読み（phonetic_name＝カタカナ）の五十音順で並べる（漢字コード順ではなく）。
    "name": "primary_contact__phonetic_name",
    "company": "primary_contact__organization",
    "title": "primary_contact__title",
    "email": "primary_contact__email",
}
# 多段ソートの最大段数（ソートコントロールの行数と一致）。
PERSON_LIST_SORT_MAX_KEYS = 3


def _parse_person_sort(params):
    """単一 sort パラメータ（例 "company,-title,name"）を [(key, direction), ...] に解決する。

    [性質] 純関数（DB 操作なし・副作用なし）
    [入力] params: QueryDict 様（request.GET）
    [出力] list[tuple[str, str]]（key は許可リスト内、direction は "asc" / "desc"）
        - 先頭 "-" は降順、無印は昇順。
        - 許可リスト外キー・空トークンは無視（不正値は黙って捨てる＝既定の並びに戻す）。
        - 同一キーの二重指定は先勝ちで除外。最大 PERSON_LIST_SORT_MAX_KEYS 段まで。
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
        if key not in PERSON_LIST_SORT_FIELD_MAP or key in seen:
            continue
        seen.add(key)
        tokens.append((key, direction))
        if len(tokens) >= PERSON_LIST_SORT_MAX_KEYS:
            break
    return tokens


def _apply_person_list_sort(qs, params):
    """Person QuerySet に多段ソート（?sort=key,-key,...）を適用する（純関数）。

    [性質] 純関数（QuerySet を加工して返すのみ・DB 操作なし）
    有効トークンが無ければ qs をそのまま返す（search_persons() の既定並びを温存）。
    指定有りのときだけ許可リスト経由で order_by を多段で差し替える（末尾に pk で安定化）。
    """
    tokens = _parse_person_sort(params)
    if not tokens:
        return qs
    order = []
    for key, direction in tokens:
        prefix = "-" if direction == "desc" else ""
        order.append(prefix + PERSON_LIST_SORT_FIELD_MAP[key])
    order.append("pk")
    return qs.order_by(*order)


def _person_sort_context(params):
    """ソート UI（検索フォーム内の折りたたみ）の描画用 context を作る（純関数）。

    [出力] dict:
        sort_rows: PERSON_LIST_SORT_MAX_KEYS 行分の [{"key", "dir"}]
                   （未指定行は key="" / dir="asc"）。各行が列ドロップダウン＋方向トグルに対応。
        sort_is_active: bool（有効トークンが 1 つでもあるか＝折りたたみの開閉初期状態の判定）。
        sort_value: 正規化済み sort 文字列（hidden の初期値・no-JS 再送信用）。
    """
    tokens = _parse_person_sort(params)
    rows = [{"key": k, "dir": d} for (k, d) in tokens]
    while len(rows) < PERSON_LIST_SORT_MAX_KEYS:
        rows.append({"key": "", "dir": "asc"})
    return {
        "sort_rows": rows,
        "sort_is_active": bool(tokens),
        "sort_value": ",".join(
            ("-" + k if d == "desc" else k) for k, d in tokens
        ),
    }


class PersonListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """人物一覧画面（仕様書 §11.4 7 番）。

    GET 専用。デフォルトは status='active' のみ表示。検索フォームに 7 フィールド
    （CardListView / ContactListView と同形、primary_contact 経由）と status 3
    チェックボックス（active / merged / archived）。primary_contact NULL の Person
    もリストに含まれる（氏名検索すると自動除外）。
    """

    # 認可（Phase 7 段3-1、URL一覧表 rev20 No.7 ★1）：persons.view_person。
    permission_required = "persons.view_person"

    model = Person
    template_name = "persons/person_list.html"
    context_object_name = "persons"
    paginate_by = 20

    def get_queryset(self):
        # v1.6 Phase 1b: 検索ロジックを persons.services.person_search に切り出し（論点 A-1）。
        # HIG 第6章：?sort=key,-key,... があればサーバー側で全件多段ソート、無ければ既定並びを維持。
        qs = search_persons(self.request.GET)
        return _apply_person_list_sort(qs, self.request.GET)

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
                # HIG 6.1：並び替え・ページ状態を戻るで復元するため sort を追加（単一パラメータ）。
                "sort",
                "page",
            ],
        )
        context["back"] = back

        # 検索フォーム内ソートコントロール用（sort_rows / sort_is_active / sort_value）。
        context.update(_person_sort_context(self.request.GET))

        context["active_app"] = "persons"
        context["active_menu"] = "persons:person_list"
        for key in SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        # _search_form.html partial と既存テンプレ両方で利用するチェック状態。
        if self.request.GET.get("searched") != "1":
            context["selected_statuses"] = ["active"]
        else:
            context["selected_statuses"] = [
                s
                for s in self.request.GET.getlist("status")
                if s in ("active", "merged", "archived")
            ]
        context["searched"] = self.request.GET.get("searched") == "1"
        return context


class PersonDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """人物詳細画面（仕様書 §11.5 8 番）。

    Person.status で 4 分岐：
      - active + primary_contact → ContactDetailView へリダイレクト（業務メイン画面）
      - active + primary_contact NULL → orphan 画面（Django Admin 誘導）
      - merged → merged 画面（merged_into リンク + マージ履歴）
      - archived → archived 画面（マージ履歴 + inactive 履歴）
    """

    # 認可（Phase 7 段3-1、URL一覧表 rev20 No.8 ★1）：persons.view_person。
    permission_required = "persons.view_person"

    model = Person
    pk_url_kwarg = "pk"
    context_object_name = "person"

    def get_queryset(self):
        return Person.objects.select_related("primary_contact", "merged_into")

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        person = self.object

        if person.status == Person.Status.ACTIVE:
            if person.primary_contact_id is not None:
                # active + primary_contact あり → 独立した人物詳細画面を render する
                # （旧：ContactDetailView へ 302 リダイレクト。v1.7 でリダイレクト廃止）。
                return self._render_active_person_detail(request, person)
            template_name = "persons/person_detail_orphan.html"
        elif person.status == Person.Status.MERGED:
            template_name = "persons/person_detail_merged.html"
        else:
            template_name = "persons/person_detail_archived.html"

        context = self.get_context_data(**kwargs)
        return render(request, template_name, context)

    def _render_active_person_detail(self, request, person):
        """active かつ primary_contact ありの人物詳細を render する（リダイレクト廃止）。

        主役 Contact は primary_contact に固定し、コンタクト詳細と同じ共通部品
        （build_contact_detail_context）で context を組み立てて contact_detail.html を流用する。
        画面メタ（back / active_app / active_menu / page_title）は人物詳細として設定する
        （別肩書リスト等のパーソン単位データは次段スコープのため、ここでは積まない）。
        """
        # ContactDetailView.get_queryset と同じ select_related で主役 Contact を取得。
        contact = (
            Contact.objects.select_related(
                "person", "business_card", "previous_person"
            ).get(pk=person.primary_contact_id)
        )
        context = build_contact_detail_context(contact, request.user)

        # BackNavigator：人物詳細画面として自身を push_current（コンタクト詳細と同じ起点ハブ運用）。
        back = BackNavigator(request)
        back.push_current("人物詳細", ["page"])

        context.update(
            {
                "back": back,
                "page_title": "人物詳細",
                "active_app": "persons",
                "active_menu": "persons:person_list",
            }
        )
        return render(request, "contacts/contact_detail.html", context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = self.object

        context["active_contacts_remaining"] = person.get_active_contacts()
        context["inactive_contacts"] = person.get_inactive_contacts()
        context["merge_logs"] = PersonMergeLog.get_for_person(person)
        context["admin_url"] = reverse(
            "admin:persons_person_change", args=[person.id]
        )

        context["back"] = BackNavigator(self.request)
        context["active_app"] = "persons"
        context["active_menu"] = "persons:person_list"

        # 本人紐付けボタン（orphan 画面の self-link 専用）の表示条件を出口ガードに揃える。
        # 既存の本人同定基準 is_self_link_email_match を再利用（新規判定は作らない）。
        context["email_match"] = is_self_link_email_match(self.request.user, person)
        context["person_active"] = person.status == Person.Status.ACTIVE

        # v1.6 Phase 1b-β：タグ付与・解除 UI 用 context（仕様書 §5.2.3）。
        # 遅延 import で循環回避（tags は persons より後の INSTALLED_APPS）。
        from tags.models import TagAssignment, TagCategory

        context["person_tag_assignments"] = (
            TagAssignment.objects.filter(person=person)
            .select_related("tag", "tag__category")
            .order_by("tag__category__sort_order", "tag__name")
        )
        categories = (
            TagCategory.objects.filter(is_archived=False)
            .prefetch_related("tags")
            .order_by("sort_order", "name")
        )
        context["tags_by_category"] = [
            (cat, list(cat.tags.filter(is_archived=False).order_by("name")))
            for cat in categories
        ]

        return context


class PersonAddAdditionalRoleView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    """別肩書追加画面（9 番、仕様書 §3.6 / §10.12 / §11.4.5）。

    既存 active Person 配下に新規 active Contact を追加する。OCR を経由しない
    ユーザー直接入力のため、ContactFieldConfidence は作らない（全フィールド high
    扱い、§10.12 / §10.6.4）。

    Person.status='active' のみ受け付け、archived / merged / 未存在は Http404
    （論点3 案A）。orphan（active かつ primary_contact NULL）も受け付ける。

    POST 成功時は作成した別肩書 Contact の詳細画面（11 番）へリダイレクト。

    実装は FormView ベース（論点2 案B）。Contact 作成処理は form_valid に View
    直書き（§10.12 通り）、save 系の services 関数は作らない。
    """

    # 認可（Phase 7 段3-2、rev20 No.9 ★2）：操作の実体は active Person 配下への
    # 新規 Contact 作成（form_valid で Contact を作り contact_detail へ遷移、Person は
    # 変更しない）ため、Contact 作成権限 contacts.add_contact を付与（ContactCreateView と対称）。
    permission_required = "contacts.add_contact"
    form_class = ContactAddAdditionalRoleForm
    template_name = "persons/person_add_additional_role.html"

    def dispatch(self, request, *args, **kwargs):
        person = get_object_or_404(Person, pk=kwargs["pk"])
        if person.status != Person.Status.ACTIVE:
            raise Http404(
                "PersonAddAdditionalRoleView is only for active Persons."
            )
        self.person = person
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["person"] = self.person
        return kwargs

    def _primary_sns_initial(self):
        """所属 Person の primary_contact の ContactSns を初期表示用 initial に変換する（§11.6.7）。

        [性質] 準関数（DB 読み取りのみ）。primary_contact が無ければ空リスト。
        別肩書は同一人物の別名刺なので、primary の SNS を初期表示として引き継ぎ、
        ユーザーが不要なものを削除・追加できるようにする（仕様書 §11.6.7 / §11.4.2.1）。
        """
        primary = self.person.primary_contact
        if primary is None:
            return []
        return [
            {"sns_type": sns.sns_type, "sns_id": sns.sns_id}
            for sns in primary.sns_accounts.all()
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        back = BackNavigator(self.request)
        context.update(
            {
                "back": back,
                "person": self.person,
                "active_app": "persons",
                "active_menu": "persons:person_list",
            }
        )
        # GET：primary の SNS を初期表示として引き継ぐ。POST 再描画時は form_invalid が
        # bound formset を渡すため、未指定のときだけ initial バインドで生成。
        if "sns_formset" not in context:
            context["sns_formset"] = build_contact_sns_formset(
                instance=None, initial=self._primary_sns_initial(), prefix="sns"
            )
        return context

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        sns_formset = build_contact_sns_formset(
            data=request.POST, instance=None, prefix="sns"
        )
        if form.is_valid() and sns_formset.is_valid():
            return self.form_valid(form, sns_formset)
        return self.form_invalid(form, sns_formset)

    def form_valid(self, form, sns_formset):
        with transaction.atomic():
            new_contact = form.get_update_contact()
            new_contact.person = self.person
            new_contact.status = Contact.Status.ACTIVE
            # §3.6：宛名がフォームで編集されていれば手動扱い（save 前に立てて自動再計算を抑止）。
            if "salutation_name" in form.changed_data:
                new_contact.salutation_name_is_manual = True
            new_contact.save()
            # submit された SNS 行を新規 Contact 配下に作成（§11.6.7）。
            _create_sns_from_formset(new_contact, sns_formset)
        self.created_contact = new_contact
        back = BackNavigator(self.request)
        return HttpResponseRedirect(back.append_url(self.get_success_url()))

    def form_invalid(self, form, sns_formset):
        return self.render_to_response(
            self.get_context_data(form=form, sns_formset=sns_formset)
        )

    def get_success_url(self):
        return reverse(
            "contacts:contact_detail",
            kwargs={"pk": self.created_contact.pk},
        )
