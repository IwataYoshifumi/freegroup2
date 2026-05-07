"""cards アプリの View 層（仕様書 v1.1.0 §8.3 / §8.7）。

View 層の責務は HTTP リクエスト/レスポンス処理とテンプレート選択のみ。
ビジネスロジックは services 層・tasks 層に委譲する。
"""

import json
import logging
import statistics
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from back_navigator.back_navigator import BackNavigator

from .forms import UploadForm
from .models import BusinessCard, Contact, ContactFieldConfidence, OriginalImage
from .services.detectors.opencv_detector import detect_cards_with_debug
from .services.image_processor import convert_to_jpeg
from .services.opencv_debug_cache import clear_debug_cache, save_debug_data

logger = logging.getLogger(__name__)

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

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.debug_json is None:
            logger.info("opencv-debug: COMPUTE for OriginalImage %s", self.object.id)
            result = detect_cards_with_debug(self.object.image_file.path)
            save_debug_data(self.object, result)
        else:
            logger.info("opencv-debug: CACHE HIT for OriginalImage %s", self.object.id)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_app"] = "cards"
        context["active_menu"] = "originals:original_list"
        business_cards = self.object.businesscard_set.all().order_by("created_at")
        context["business_cards"] = business_cards
        context["back"] = BackNavigator(self.request)

        # raw_json は dict のまま渡す（json_script フィルタで埋め込み、json-viewer で表示）
        raw_json = self.object.raw_json
        context["raw_json"] = raw_json

        debug_json = self.object.debug_json
        context["debug_json"] = debug_json
        context["debug_summary"] = _build_debug_summary(
            debug_json, raw_json=raw_json, bc_count=business_cards.count()
        )
        context["mask_urls"] = _build_mask_urls(self.object)
        context["candidates_passed"] = _candidates_passed(debug_json)
        context["candidates_all"] = _candidates_all(debug_json)
        context["overlay_polygons"] = _build_overlay_polygons(debug_json)
        context["warning_stripe"] = _build_warning_stripe(debug_json)

        return context


_REJECT_REASONS_ORDER = ("area_too_small", "area_too_large", "zero_size", "aspect_invalid")


def _build_debug_summary(debug_json, raw_json=None, bc_count=0):
    if not debug_json:
        return None
    cf = debug_json.get("candidates_filter") or []
    cd = debug_json.get("candidates_dedup") or []

    rejected = [x for x in cf if not x.get("passed")]
    reason_counter = Counter(x.get("reject_reason") or "" for x in rejected)
    reject_reason_str = ", ".join(
        f"{r}: {reason_counter.get(r, 0)}" for r in _REJECT_REASONS_ORDER
    )

    results_count = len(debug_json.get("results") or [])
    has_min_ng = _has_minimum_info_ng_count(raw_json)
    if has_min_ng is None:
        has_min_ng_str = "未実行（OCR 未走行）"
    else:
        has_min_ng_str = f"{has_min_ng} 件"

    detection_gap_str = (
        f"最終検出 {results_count} 件 / BusinessCard {bc_count} 件"
        f"（差分 {results_count - bc_count} 件）"
    )

    return {
        "image_size": debug_json.get("image_size") or {},
        "contours_count": debug_json.get("contours_count", 0),
        "passed_count": sum(1 for x in cf if x.get("passed")),
        "candidates_total": len(cf),
        "kept_count": sum(1 for x in cd if x.get("kept")),
        "dedup_total": len(cd),
        "warp_failures_count": len(debug_json.get("warp_failures") or []),
        "results_count": results_count,
        "error_message": debug_json.get("error_message") or "",
        "computed_at": debug_json.get("computed_at") or "",
        "reject_reason_str": reject_reason_str,
        "has_min_info_ng_str": has_min_ng_str,
        "detection_gap_str": detection_gap_str,
    }


def _has_minimum_info_ng_count(raw_json):
    """raw_json.cards のうち full_name が空または欠損の件数。raw_json が None なら None。

    has_minimum_info（v1.2.1 仕様）の必須条件である full_name の有無のみで判定する。
    company / email / phone / mobile の AND 条件は本デバッグ画面では問わない（簡略表示）。
    """
    if not raw_json:
        return None
    cards = raw_json.get("cards") or []
    ng = 0
    for c in cards:
        if not isinstance(c, dict):
            continue
        fields = c.get("fields") or {}
        full_name = fields.get("full_name")
        if isinstance(full_name, dict):
            value = full_name.get("value")
        else:
            value = full_name
        if value is None or (isinstance(value, str) and not value.strip()):
            ng += 1
    return ng


def _build_warning_stripe(debug_json):
    """検出失敗の警告ストライプに表示する条件メッセージリスト（立った条件のみ列挙）。

    [出力] list[str]: 立った条件のメッセージ。1 つも該当しなければ空リスト。
    """
    if not debug_json:
        return []
    msgs = []

    # 条件1: mask_5 (closed) 白画素率 ≥ 80%
    mwr = debug_json.get("mask_white_ratios") or {}
    mask5 = mwr.get("mask_5")
    if mask5 is not None and mask5 >= 0.80:
        msgs.append(f"マスク白画素率が異常 ({mask5 * 100:.1f}%)")

    # 条件2: passed = 0
    cf = debug_json.get("candidates_filter") or []
    passed_count = sum(1 for x in cf if x.get("passed"))
    if passed_count == 0:
        msgs.append("passed が 0 件")

    # 条件3: area_too_large ≥ 1
    too_large = sum(1 for x in cf if x.get("reject_reason") == "area_too_large")
    if too_large >= 1:
        msgs.append(f"巨大候補が {too_large} 件")

    return msgs


def _build_mask_urls(original_image):
    """5枚のマスク画像 (label, url) のリスト。ファイル不在時は url=None。"""
    labels = (
        "mask_1: 輝度差 (diff)",
        "mask_2: エッジ (edge)",
        "mask_3: 彩度 (sat)",
        "mask_4: OR 合成 (or)",
        "mask_5: クロージング後 (closed)",
    )
    cache_dir = Path(settings.MEDIA_ROOT) / "debug_cache" / str(original_image.id)
    base_url = f"{settings.MEDIA_URL.rstrip('/')}/debug_cache/{original_image.id}"
    items = []
    for n, label in enumerate(labels, start=1):
        f = cache_dir / f"mask_{n}.png"
        url = f"{base_url}/mask_{n}.png" if f.exists() else None
        items.append((label, url))
    return items


def _format_candidate(c):
    """候補一覧テーブル表示用の整形済み文字列フィールドを付与した dict。"""
    area = c.get("area") or 0
    ratio = c.get("area_ratio") or 0
    rect_size = c.get("rect_size") or [0, 0]
    rect_center = c.get("rect_center") or [0, 0]
    rw = rect_size[0] if len(rect_size) > 0 else 0
    rh = rect_size[1] if len(rect_size) > 1 else 0
    cx = rect_center[0] if len(rect_center) > 0 else 0
    cy = rect_center[1] if len(rect_center) > 1 else 0
    aspect = c.get("aspect_ratio")
    return {
        **c,
        "area_str": f"{area:,.0f}",
        "area_ratio_pct_str": f"{ratio * 100:.1f}",
        "rect_w_str": f"{rw:.0f}",
        "rect_h_str": f"{rh:.0f}",
        "rect_cx_str": f"{cx:.0f}",
        "rect_cy_str": f"{cy:.0f}",
        "rect_angle_str": f"{(c.get('rect_angle') or 0):.1f}",
        "aspect_ratio_str": (f"{aspect:.2f}" if aspect is not None else "-"),
    }


def _candidates_passed(debug_json):
    if not debug_json:
        return []
    cf = debug_json.get("candidates_filter") or []
    return [
        _format_candidate(c)
        for c in sorted(
            (x for x in cf if x.get("passed")),
            key=lambda x: -(x.get("area") or 0),
        )
    ]


def _candidates_all(debug_json):
    """passed 優先 → area 降順。"""
    if not debug_json:
        return []
    cf = debug_json.get("candidates_filter") or []
    return [
        _format_candidate(c)
        for c in sorted(
            cf,
            key=lambda x: (
                0 if x.get("passed") else 1,
                -(x.get("area") or 0),
            ),
        )
    ]


def _build_overlay_polygons(debug_json):
    """SVG polygon 用の points 文字列・重心・巨大候補フラグを事前計算したリスト。

    is_giant: results 内の area_ratio 中央値の 1.5 倍以上のものに True。
              ただし results が 1 件以下の場合は判定スキップ（全て False）。
    """
    if not debug_json:
        return []
    keys = ("top_left", "top_right", "bottom_right", "bottom_left")
    image_size = debug_json.get("image_size") or {}
    image_area = image_size.get("area") or 0

    # 各 polygon の座標と shoelace area を一旦計算
    raw = []
    for r in debug_json.get("results") or []:
        polygon = r.get("polygon") or {}
        coords = []
        for k in keys:
            p = polygon.get(k) or {}
            coords.append((p.get("x", 0), p.get("y", 0)))
        # shoelace 公式で四角形の面積
        s = 0.0
        n = len(coords)
        for i in range(n):
            x1, y1 = coords[i]
            x2, y2 = coords[(i + 1) % n]
            s += x1 * y2 - x2 * y1
        poly_area = abs(s) / 2.0
        area_ratio = poly_area / image_area if image_area > 0 else 0.0
        raw.append({
            "card_index": r.get("card_index"),
            "coords": coords,
            "area_ratio": area_ratio,
        })

    # 巨大判定。results が 2 件以上のときのみ実施。
    if len(raw) >= 2:
        median_ratio = statistics.median(x["area_ratio"] for x in raw)
        threshold = median_ratio * 1.5
    else:
        threshold = None

    items = []
    for r in raw:
        coords = r["coords"]
        points_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        cx = sum(c[0] for c in coords) / 4.0
        cy = sum(c[1] for c in coords) / 4.0
        is_giant = bool(threshold is not None and r["area_ratio"] >= threshold)
        items.append({
            "card_index": r["card_index"],
            "points_str": points_str,
            "centroid_x": cx,
            "centroid_y": cy,
            "is_giant": is_giant,
            "area_ratio": r["area_ratio"],
        })
    return items


class RecalcDebugView(View):
    """OpenCV デバッグキャッシュを破壊し、元画像詳細にリダイレクトする（POST 専用）。

    実際の再計算は OriginalDetailView の GET ハンドラに任せる。本 View は
    debug_cache ディレクトリの削除と debug_json のクリアのみを行う。
    GET / その他メソッドは Django 標準の 405 応答（method_not_allowed）が返る。
    """

    def post(self, request, pk):
        user = get_current_user(request)
        original = get_object_or_404(OriginalImage, pk=pk, user=user)
        clear_debug_cache(original)
        return redirect("originals:original_detail", pk=original.id)


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
        qs = (
            BusinessCard.objects.filter(
                original_image__user=user,
                ocr_result=BusinessCard.OcrResult.BUSINESS_CARD,
            )
            .select_related("original_image", "contact")
            .annotate(
                has_low=Exists(
                    ContactFieldConfidence.objects.filter(
                        contact__business_card=OuterRef("pk"),
                        confidence=ContactFieldConfidence.CONFIDENCE_LOW,
                    )
                ),
                has_medium=Exists(
                    ContactFieldConfidence.objects.filter(
                        contact__business_card=OuterRef("pk"),
                        confidence=ContactFieldConfidence.CONFIDENCE_MEDIUM,
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

        try:
            contact = self.object.contact
        except Contact.DoesNotExist:
            contact = None
        confidence_map = {}
        if contact is not None:
            for entry in contact.confidences.all():
                confidence_map[entry.field_name] = entry.confidence
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
