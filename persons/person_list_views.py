"""パーソンリスト（PersonList）CRUD View（仕様書 v1.6 §2.3.3 No.21〜24）。"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from actionlogs.constants import (
    PERSON_LIST_CREATED,
    PERSON_LIST_DELETED,
    PERSON_LIST_UPDATED,
)
from actionlogs.models import ActionLog
from permissions.services import AccessListService
from persons.forms import PersonListForm
from persons.models import PersonList


class PersonListListView(LoginRequiredMixin, ListView):
    """パーソンリスト一覧（仕様書 v1.6 §2.3.3 No.21）。"""

    model = PersonList
    template_name = "persons/person_list_list.html"
    context_object_name = "person_lists"

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.has_perm("persons.view_all_persons"):
            return PersonList.objects.all().order_by("name")
        accessible_ids = AccessListService.accessible_person_list_ids(user)
        return PersonList.objects.filter(id__in=accessible_ids).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_add"] = user.is_superuser or user.has_perm("persons.add_personlist")
        context["active_menu"] = "persons:person_list_list"
        return context


class PersonListCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """パーソンリスト新規作成（仕様書 v1.6 §2.3.3 No.22）。"""

    permission_required = "persons.add_personlist"
    model = PersonList
    form_class = PersonListForm
    template_name = "persons/person_list_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        with transaction.atomic():
            response = super().form_valid(form)
            ActionLog.record(
                user=self.request.user,
                action=PERSON_LIST_CREATED,
                content_object=self.object,
                object_repr=str(self.object),
            )
            messages.success(self.request, f"パーソンリスト「{self.object.name}」を作成しました。")
            return response

    def get_success_url(self):
        return reverse("person_lists:person_list_detail", kwargs={"pk": self.object.pk})


class PersonListDetailView(LoginRequiredMixin, DetailView):
    """パーソンリスト詳細（仕様書 v1.6 §2.3.3 No.21/詳細）。"""

    model = PersonList
    template_name = "persons/person_list_detail.html"
    context_object_name = "person_list"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["can_edit"] = user.is_superuser or user.has_perm("persons.change_personlist")
        context["can_delete"] = user.is_superuser or user.has_perm("persons.delete_personlist")
        context["persons"] = (
            self.object.persons.filter(status="active")
            .select_related("primary_contact")[:50]
        )
        context["persons_count"] = self.object.persons.count()
        return context


class PersonListUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """パーソンリスト編集（仕様書 v1.6 §2.3.3 No.23）。
    アクセスリスト選択肢の絞り込み、および現在値保護（v1.6）を内包。
    """

    permission_required = "persons.change_personlist"
    model = PersonList
    form_class = PersonListForm
    template_name = "persons/person_list_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            ActionLog.record(
                user=self.request.user,
                action=PERSON_LIST_UPDATED,
                content_object=self.object,
                object_repr=str(self.object),
            )
            messages.success(self.request, f"パーソンリスト「{self.object.name}」を更新しました。")
            return response

    def get_success_url(self):
        return reverse("person_lists:person_list_detail", kwargs={"pk": self.object.pk})


class PersonListDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """パーソンリスト削除（仕様書 v1.6 §2.3.3 No.24）。
    所属Personが存在する間はPROTECTにより削除不可。
    """

    permission_required = "persons.delete_personlist"

    def post(self, request, pk):
        person_list = get_object_or_404(PersonList, pk=pk)
        person_count = person_list.persons.count()
        if person_count > 0:
            messages.error(
                request,
                f"このパーソンリストにはパーソンが {person_count} 件登録されているため削除できません。",
            )
            return redirect("person_lists:person_list_detail", pk=pk)

        with transaction.atomic():
            name = person_list.name
            ActionLog.record(
                user=request.user,
                action=PERSON_LIST_DELETED,
                object_repr=name,
            )
            person_list.delete()
            messages.success(request, f"パーソンリスト「{name}」を削除しました。")
            return redirect("person_lists:person_list_list")
