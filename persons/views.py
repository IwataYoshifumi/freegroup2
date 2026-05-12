"""persons アプリの View 層（仕様書 v1.4.2 §11.4 / §11.5）。

PersonListView：人物一覧画面（URL 7 番）。検索フォーム + status フィルタ + ページネーション。
PersonDetailView：人物詳細画面（URL 8 番）。Person.status で 4 分岐。
  - active + primary_contact あり → ContactDetailView へリダイレクト
  - active + primary_contact NULL → orphan 専用画面（Django Admin 誘導）
  - merged → merged 専用画面（merged_into / マージ履歴 / 残存 Contact）
  - archived → archived 専用画面（マージ履歴 / inactive 履歴）
PersonAddAdditionalRoleView：別肩書追加画面（URL 9 番、D-Form ステップ2）。
  active Person 配下に新規 active Contact を作成。CFC は作らない（§10.12）。

認証は ContactListView / ContactDetailView と同じく LoginRequiredMixin 未使用の
仮認証スタイル（仕様書 §18.1 / §18.2）。Person も user FK を持たないため全 Person 対象。
ただし PersonAddAdditionalRoleView は書込系のため LoginRequiredMixin を使う
（UpdateActiveContactView と同じ慣例）。
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import DetailView, ListView
from django.views.generic.edit import FormView

from back_navigator.back_navigator import BackNavigator
from contacts.forms import ContactAddAdditionalRoleForm
from contacts.models import Contact
from duplicates.models import PersonMergeLog

from .models import Person


class PersonListView(ListView):
    """人物一覧画面（仕様書 §11.4 7 番）。

    GET 専用。デフォルトは status='active' のみ表示。検索フォームに 7 フィールド
    （CardListView / ContactListView と同形、primary_contact 経由）と status 3
    チェックボックス（active / merged / archived）。primary_contact NULL の Person
    もリストに含まれる（氏名検索すると自動除外）。
    """

    model = Person
    template_name = "persons/person_list.html"
    context_object_name = "persons"
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
    _VALID_STATUSES = ("active", "merged", "archived")

    def _get_selected_statuses(self):
        """status チェック状態の解決。初回（searched なし）は active のみ、
        検索後はチェックされた値そのまま（不正値は捨てる、空も許容で 0 件）。"""
        if self.request.GET.get("searched") != "1":
            return ["active"]
        return [
            s
            for s in self.request.GET.getlist("status")
            if s in self._VALID_STATUSES
        ]

    def get_queryset(self):
        selected = self._get_selected_statuses()
        if not selected:
            return Person.objects.none()

        qs = (
            Person.objects.filter(status__in=selected)
            .select_related("primary_contact")
            .annotate(
                active_count=Count(
                    "contact",
                    filter=Q(contact__status__in=["primary", "active"]),
                )
            )
        )

        p = self.request.GET
        if p.get("name", "").strip():
            qs = qs.filter(
                primary_contact__full_name__icontains=p["name"].strip()
            )
        if p.get("company", "").strip():
            qs = qs.filter(
                primary_contact__company__icontains=p["company"].strip()
            )
        if p.get("department", "").strip():
            qs = qs.filter(
                primary_contact__department__icontains=p["department"].strip()
            )
        if p.get("title", "").strip():
            qs = qs.filter(
                primary_contact__title__icontains=p["title"].strip()
            )
        if p.get("email", "").strip():
            qs = qs.filter(
                primary_contact__email__icontains=p["email"].strip()
            )
        if p.get("tel", "").strip():
            tel = p["tel"].strip()
            qs = qs.filter(
                Q(primary_contact__phone__icontains=tel)
                | Q(primary_contact__mobile__icontains=tel)
                | Q(primary_contact__fax__icontains=tel)
            )
        if p.get("address", "").strip():
            qs = qs.filter(
                primary_contact__address__icontains=p["address"].strip()
            )

        return qs.order_by("-updated_at", "-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current(
            "人物一覧",
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

        context["active_app"] = "persons"
        context["active_menu"] = "persons:person_list"
        for key in self._SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        context["selected_statuses"] = self._get_selected_statuses()
        context["searched"] = self.request.GET.get("searched") == "1"
        return context


class PersonDetailView(DetailView):
    """人物詳細画面（仕様書 §11.5 8 番）。

    Person.status で 4 分岐：
      - active + primary_contact → ContactDetailView へリダイレクト（業務メイン画面）
      - active + primary_contact NULL → orphan 画面（Django Admin 誘導）
      - merged → merged 画面（merged_into リンク + マージ履歴）
      - archived → archived 画面（マージ履歴 + inactive 履歴）
    """

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
                back = BackNavigator(self.request)
                target = reverse(
                    "contacts:contact_detail",
                    kwargs={"pk": person.primary_contact_id},
                )
                return HttpResponseRedirect(back.append_url(target))
            template_name = "persons/person_detail_orphan.html"
        elif person.status == Person.Status.MERGED:
            template_name = "persons/person_detail_merged.html"
        else:
            template_name = "persons/person_detail_archived.html"

        context = self.get_context_data(**kwargs)
        return render(request, template_name, context)

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
        return context


class PersonAddAdditionalRoleView(LoginRequiredMixin, FormView):
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
        return context

    def form_valid(self, form):
        new_contact = form.get_update_contact()
        new_contact.person = self.person
        new_contact.status = Contact.Status.ACTIVE
        new_contact.save()
        self.created_contact = new_contact
        back = BackNavigator(self.request)
        return HttpResponseRedirect(back.append_url(self.get_success_url()))

    def get_success_url(self):
        return reverse(
            "contacts:contact_detail",
            kwargs={"pk": self.created_contact.pk},
        )
