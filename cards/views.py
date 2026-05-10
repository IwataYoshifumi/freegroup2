"""cards アプリの View 層（仕様書 v1.1.0 §8.3 / §8.7）。

View 層の責務は HTTP リクエスト/レスポンス処理とテンプレート選択のみ。
ビジネスロジックは services 層・tasks 層に委譲する。
"""

import json

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_date
from django.views.generic import DetailView, FormView, ListView

from back_navigator.back_navigator import BackNavigator

from .forms import UploadForm
from .models import BusinessCard, OriginalImage
from .services.image_processor import convert_to_jpeg
from config.constants import DUPLICATE_CHECK_FIELDS
from contacts.models import ContactFieldConfidence

User = get_user_model()


def get_current_user(request):
    """認証未実装のための仮処理（将来認証実装時に削除）。

    request.user が認証済みならそれを返し、未認証なら最初のスーパーユーザーを返す。
    OriginalImage.user は仕様書 §4.2 で必須なので、保存に必要な User を確保する。
    """
    if request.user.is_authenticated:
        return request.user
    return User.objects.filter(is_superuser=True).first()


def home_view(request):
    return render(request, "home.html")


def placeholder_view(request):
    return HttpResponse("準備中", content_type="text/plain; charset=utf-8")


class UploadView(FormView):
    template_name = "cards/upload.html"
    form_class = UploadForm

    def form_valid(self, form):
        uploaded_file = form.cleaned_data["image"]
        jpeg_bytes = convert_to_jpeg(uploaded_file)

        user = get_current_user(self.request)
        original = OriginalImage(user=user, status=OriginalImage.STATUS_PENDING)
        filename = f"{original.id}.jpg"
        original.image_file.save(filename, ContentFile(jpeg_bytes), save=False)
        original.save()
        return redirect("originals:original_detail", pk=original.id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_app"] = "cards"
        context["active_menu"] = "cards:card_upload"
        return context


class OriginalListView(ListView):
    model = OriginalImage
    template_name = "cards/original_list.html"
    context_object_name = "originals"

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get("per_page", "20")
        if per_page == "50":
            return 50
        return 20

    def get_queryset(self):
        user = get_current_user(self.request)
        qs = OriginalImage.objects.filter(user=user)

        statuses = self.request.GET.getlist("status")
        valid_statuses = {value for value, _ in OriginalImage.STATUS_CHOICES}
        statuses = [s for s in statuses if s in valid_statuses]
        if statuses:
            qs = qs.filter(status__in=statuses)

        date_from = parse_date(self.request.GET.get("date_from", ""))
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = parse_date(self.request.GET.get("date_to", ""))
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current("元画像一覧", ["status", "date_from", "date_to", "page", "per_page"])
        context["back"] = back

        context["active_app"] = "cards"
        context["active_menu"] = "originals:original_list"
        context["status_choices"] = OriginalImage.STATUS_CHOICES
        context["selected_statuses"] = self.request.GET.getlist("status")
        context["date_from"] = self.request.GET.get("date_from", "")
        context["date_to"] = self.request.GET.get("date_to", "")
        context["per_page"] = self.request.GET.get("per_page", "20")
        return context


class OriginalDetailView(DetailView):
    model = OriginalImage
    template_name = "cards/original_detail.html"
    context_object_name = "original"
    pk_url_kwarg = "pk"

    def get_queryset(self):
        user = get_current_user(self.request)
        return OriginalImage.objects.filter(user=user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_app"] = "cards"
        context["active_menu"] = "originals:original_list"
        context["business_cards"] = self.object.businesscard_set.all().order_by("created_at")
        context["back"] = BackNavigator(self.request)
        return context


class CardListView(ListView):
    """名刺一覧画面（仕様書 v1.2.2 / Phase 4）。

    BusinessCard を Contact 情報とともに一覧表示する。
    7フィールドの AND 検索（name / company / department / title / email / tel / address）。
    tel は phone / mobile / fax の OR 一致。
    """

    model = BusinessCard
    template_name = "cards/card_list.html"
    context_object_name = "cards"
    paginate_by = 20

    _SEARCH_PARAMS = ("name", "company", "department", "title", "email", "tel", "address")

    def get_queryset(self):
        user = get_current_user(self.request)
        # confidence ドットは DUPLICATE_CHECK_FIELDS のみ対象、confirmed_at で
        # 「未確認」を区別する（DEBUG=True 時のみ表示）。
        qs = (
            BusinessCard.objects.filter(original_image__user=user)
            .select_related("original_image", "contact")
            .annotate(
                has_unconfirmed_low=Exists(
                    ContactFieldConfidence.objects.filter(
                        contact__business_card=OuterRef("pk"),
                        field_name__in=DUPLICATE_CHECK_FIELDS,
                        confidence=ContactFieldConfidence.Confidence.LOW,
                        confirmed_at__isnull=True,
                    )
                ),
                has_unconfirmed_medium=Exists(
                    ContactFieldConfidence.objects.filter(
                        contact__business_card=OuterRef("pk"),
                        field_name__in=DUPLICATE_CHECK_FIELDS,
                        confidence=ContactFieldConfidence.Confidence.MEDIUM,
                        confirmed_at__isnull=True,
                    )
                ),
                has_confirmed=Exists(
                    ContactFieldConfidence.objects.filter(
                        contact__business_card=OuterRef("pk"),
                        field_name__in=DUPLICATE_CHECK_FIELDS,
                        confirmed_at__isnull=False,
                    )
                ),
            )
        )

        p = self.request.GET
        if p.get("name", "").strip():
            qs = qs.filter(contact__full_name__icontains=p["name"].strip())
        if p.get("company", "").strip():
            qs = qs.filter(contact__company__icontains=p["company"].strip())
        if p.get("department", "").strip():
            qs = qs.filter(contact__department__icontains=p["department"].strip())
        if p.get("title", "").strip():
            qs = qs.filter(contact__title__icontains=p["title"].strip())
        if p.get("email", "").strip():
            qs = qs.filter(contact__email__icontains=p["email"].strip())
        if p.get("tel", "").strip():
            tel = p["tel"].strip()
            qs = qs.filter(
                Q(contact__phone__icontains=tel)
                | Q(contact__mobile__icontains=tel)
                | Q(contact__fax__icontains=tel)
            )
        if p.get("address", "").strip():
            qs = qs.filter(contact__address__icontains=p["address"].strip())

        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current(
            "名刺一覧",
            ["name", "company", "department", "title", "email", "tel", "address", "page"],
        )
        context["back"] = back

        context["active_app"] = "cards"
        context["active_menu"] = "cards:card_list"
        for key in self._SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        return context


class CardDetailView(DetailView):
    """名刺詳細画面（仕様書 v1.2.2 / Phase 4）。

    BusinessCard の Contact 情報をグルーピング表示し、
    ContactFieldConfidence の low/medium マーカーを各フィールドに添える。
    同じ元画像内の他名刺は下部にサムネイルリストで表示する。
    """

    model = BusinessCard
    template_name = "cards/card_detail.html"
    context_object_name = "card"
    pk_url_kwarg = "pk"

    def get_queryset(self):
        user = get_current_user(self.request)
        return (
            BusinessCard.objects.filter(original_image__user=user)
            .select_related("original_image", "contact", "contact__person")
            .prefetch_related("contact__confidences")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_app"] = "cards"
        context["active_menu"] = "cards:card_list"
        context["back"] = BackNavigator(self.request)

        contact = getattr(self.object, "contact", None)
        # confidence_map は field_name -> 'low' / 'mid' / 'confirmed' の文字列。
        # confirmed_at セット済み → 'confirmed'、それ以外は confidence 値（'medium'
        # は 'mid' に短縮、Contact 詳細画面の {% confidence_state %} と整合）。
        # 疑似 high のフィールドは map に含めない（テンプレート側で「キーなし = 何も
        # 表示しない」を維持）。
        confidence_map = {}
        if contact is not None:
            for entry in contact.confidences.all():
                if entry.confirmed_at is not None:
                    confidence_map[entry.field_name] = "confirmed"
                elif entry.confidence == ContactFieldConfidence.Confidence.LOW:
                    confidence_map[entry.field_name] = "low"
                elif entry.confidence == ContactFieldConfidence.Confidence.MEDIUM:
                    confidence_map[entry.field_name] = "mid"
        context["contact"] = contact
        context["confidence_map"] = confidence_map

        sibling_cards = (
            BusinessCard.objects.filter(original_image=self.object.original_image)
            .exclude(pk=self.object.pk)
            .select_related("contact")
            .order_by("card_index")
        )
        context["sibling_cards"] = sibling_cards

        raw_json = self.object.original_image.raw_json
        card_json_str = None
        if raw_json:
            cards = raw_json.get("cards", [])
            idx = self.object.card_index
            if 0 <= idx < len(cards):
                card_json_str = json.dumps(cards[idx], ensure_ascii=False, indent=2)
        context["card_json_str"] = card_json_str

        return context
