"""cards アプリの View 層（仕様書 v1.1.0 §8.3 / §8.7）。

View 層の責務は HTTP リクエスト/レスポンス処理とテンプレート選択のみ。
ビジネスロジックは services 層・tasks 層に委譲する。
"""

import json
import logging
import statistics
from collections import Counter

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from back_navigator.back_navigator import BackNavigator

from .forms import UploadForm
from .models import BusinessCard, DebugMask, OriginalImage
from .services.image_processor import convert_to_jpeg, extract_exif_to_json
from .services.opencv_debug_cache import recalc_opencv_debug
from config.constants import DUPLICATE_CHECK_FIELDS
from contacts.models import Contact, ContactFieldConfidence

logger = logging.getLogger(__name__)


def placeholder_view(request):
    return HttpResponse("準備中", content_type="text/plain; charset=utf-8")


class UploadView(LoginRequiredMixin, FormView):
    template_name = "cards/upload.html"
    form_class = UploadForm

    def form_valid(self, form):
        uploaded_file = form.cleaned_data["image"]

        # EXIF 抽出は convert_to_jpeg の exif_transpose で EXIF が失われる前のタイミング
        # で行う（仕様書 v1.6.1 統合版 §7.2）。両者は独立して動く。
        exif_json = extract_exif_to_json(uploaded_file)
        jpeg_bytes = convert_to_jpeg(uploaded_file)

        user = self.request.user
        original = OriginalImage(
            user=user,
            status=OriginalImage.STATUS_PENDING,
            exif_json=exif_json,
        )
        filename = f"{original.id}.jpg"
        original.image_file.save(filename, ContentFile(jpeg_bytes), save=False)
        original.save()
        back = BackNavigator(self.request)
        target_url = reverse(
            "originals:original_detail",
            kwargs={"pk": original.id},
        )
        return HttpResponseRedirect(back.append_url(target_url))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_app"] = "cards"
        context["active_menu"] = "cards:card_upload"
        context["back"] = BackNavigator(self.request)
        return context


class OriginalListView(LoginRequiredMixin, ListView):
    model = OriginalImage
    template_name = "cards/original_list.html"
    context_object_name = "originals"

    def get_paginate_by(self, queryset):
        per_page = self.request.GET.get("per_page", "20")
        if per_page == "50":
            return 50
        return 20

    def get_queryset(self):
        user = self.request.user
        qs = (
            OriginalImage.objects.filter(user=user)
            .select_related("user")
            .annotate(
                bc_business_card_count=Count(
                    "businesscard",
                    filter=Q(businesscard__ocr_result=BusinessCard.OcrResult.BUSINESS_CARD),
                ),
                bc_other_count=Count(
                    "businesscard",
                    filter=~Q(businesscard__ocr_result=BusinessCard.OcrResult.BUSINESS_CARD),
                ),
            )
        )

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
        back.push_current("", ["status", "date_from", "date_to", "page", "per_page"])
        context["back"] = back

        context["active_app"] = "cards"
        context["active_menu"] = "originals:original_list"
        context["status_choices"] = OriginalImage.STATUS_CHOICES
        context["selected_statuses"] = self.request.GET.getlist("status")
        context["date_from"] = self.request.GET.get("date_from", "")
        context["date_to"] = self.request.GET.get("date_to", "")
        context["per_page"] = self.request.GET.get("per_page", "20")
        return context


class OriginalDetailView(LoginRequiredMixin, DetailView):
    model = OriginalImage
    template_name = "cards/original_detail.html"
    context_object_name = "original"
    pk_url_kwarg = "pk"

    def get_queryset(self):
        user = self.request.user
        return OriginalImage.objects.filter(user=user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_app"] = "cards"
        context["active_menu"] = "originals:original_list"
        business_cards = self.object.businesscard_set.all().order_by("created_at")
        context["business_cards"] = business_cards
        context["back"] = BackNavigator(self.request)

        # v1.5.0: OriginalImage.raw_json の読み出しは廃止。OCR 結果は BC.raw_json_1/2 を参照。
        debug_json = self.object.debug_json
        debug_masks = list(self.object.debug_masks.all())
        mask_white_ratios = _build_mask_white_ratios(debug_masks)
        block1_mask_urls, block2_mask_urls = _build_mask_url_blocks(debug_masks)

        last_attempt = _get_last_attempt(debug_json) or {}

        context["debug_json"] = debug_json
        context["debug_summary"] = _build_debug_summary(
            debug_json,
            business_cards=business_cards,
            bc_count=business_cards.count(),
        )
        context["block1_mask_urls"] = block1_mask_urls
        context["block2_mask_urls"] = block2_mask_urls
        context["candidates_passed"] = _candidates_passed(debug_json)
        context["candidates_all"] = _candidates_all(debug_json)
        context["candidates_dedup"] = last_attempt.get("candidates_dedup") or []
        context["warp_failures"] = last_attempt.get("warp_failures") or []
        context["overlay_polygons"] = _build_overlay_polygons(debug_json)
        context["warning_stripe"] = _build_warning_stripe(debug_json, mask_white_ratios)

        # Phase G: EXIF 情報セクション用に integer indent の raw JSON を渡す（§7.2）。
        # exif_json が NULL のときは None のまま、テンプレで「EXIF 情報なし」表示に分岐。
        if self.object.exif_json is None:
            context["exif_json_pretty"] = None
        else:
            context["exif_json_pretty"] = json.dumps(
                self.object.exif_json, ensure_ascii=False, indent=2
            )

        return context


_REJECT_REASONS_ORDER = ("area_too_small", "area_too_large", "zero_size", "aspect_invalid")


def _get_last_attempt(debug_json):
    """[性質] 純関数 / debug_json の最終 attempt dict を返す。なければ None。"""
    if not debug_json:
        return None
    attempts = debug_json.get("attempts") or []
    return attempts[-1] if attempts else None


def _build_debug_summary(debug_json, business_cards=None, bc_count=0):
    if not debug_json:
        return None
    last = _get_last_attempt(debug_json) or {}
    cf = last.get("candidates_filter") or []
    cd = last.get("candidates_dedup") or []
    results = last.get("results") or []
    wf = last.get("warp_failures") or []

    rejected = [x for x in cf if not x.get("passed")]
    reason_counter = Counter(x.get("reject_reason") or "" for x in rejected)
    reject_reason_str = ", ".join(
        f"{r}: {reason_counter.get(r, 0)}" for r in _REJECT_REASONS_ORDER
    )

    results_count = len(results)
    has_min_ng = _has_minimum_info_ng_count(business_cards)
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
        "contours_count": last.get("contours_count", 0) or 0,
        "passed_count": sum(1 for x in cf if x.get("passed")),
        "candidates_total": len(cf),
        "kept_count": sum(1 for x in cd if x.get("kept")),
        "dedup_total": len(cd),
        "warp_failures_count": len(wf),
        "results_count": results_count,
        "error_message": debug_json.get("error_message") or "",
        "computed_at": debug_json.get("computed_at") or "",
        "reject_reason_str": reject_reason_str,
        "has_min_info_ng_str": has_min_ng_str,
        "detection_gap_str": detection_gap_str,
    }


def _has_minimum_info_ng_count(business_cards):
    """各 BC の採用 raw_json から full_name 欠損数を集計する（v1.5.0 / BC ベース）。

    採用ロジック：raw_json_2 が None でなければ採用、なければ raw_json_1。
    raw_json_1 が None の BC は OCR 未実行扱いで対象外。
    全 BC が raw_json なし、または BC 自体が空のときは None を返す。
    """
    if not business_cards:
        return None
    ng = 0
    has_any_raw_json = False
    for bc in business_cards:
        adopted = bc.raw_json_2 if bc.raw_json_2 else bc.raw_json_1
        if not adopted:
            continue
        has_any_raw_json = True
        cards = adopted.get("cards") or []
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
    if not has_any_raw_json:
        return None
    return ng


def _build_warning_stripe(debug_json, mask_white_ratios=None):
    """検出失敗の警告ストライプに表示する条件メッセージリスト（立った条件のみ列挙）。

    [入力]
      debug_json         : OriginalImage.debug_json（新構造）
      mask_white_ratios  : {attempt_no: {mask_type: white_ratio}} 形式の dict
    [出力] list[str]: 立った条件のメッセージ。1 つも該当しなければ空リスト。

    判定対象は最終 attempt（反転リトライがあれば 2 回目、なければ 1 回目）。
    反転リトライが走っていた場合は、その事実自体もストライプに追加する。
    """
    if not debug_json:
        return []
    msgs = []
    last = _get_last_attempt(debug_json) or {}
    last_attempt_no = last.get("attempt_no")

    # 条件1: 最終 attempt の closed マスク白画素率 ≥ 80%
    mwr = mask_white_ratios or {}
    closed_ratio = (mwr.get(last_attempt_no) or {}).get(DebugMask.MaskType.CLOSED)
    if closed_ratio is not None and closed_ratio >= 0.80:
        msgs.append(f"マスク白画素率が異常 ({closed_ratio * 100:.1f}%)")

    # 条件2: 最終 attempt の results が 0 件
    results = last.get("results") or []
    if len(results) == 0:
        msgs.append("最終検出 0 件")

    # 条件3: 最終 attempt の area_too_large ≥ 1
    cf = last.get("candidates_filter") or []
    too_large = sum(1 for x in cf if x.get("reject_reason") == "area_too_large")
    if too_large >= 1:
        msgs.append(f"巨大候補が {too_large} 件")

    # 条件4: 反転リトライが実施されたか
    or_inv = debug_json.get("or_inversion") or {}
    if or_inv.get("attempted"):
        before = or_inv.get("passed_before", 0)
        after = or_inv.get("passed_after", 0)
        improved = "／改善あり" if or_inv.get("improved") else "／改善なし"
        msgs.append(f"反転リトライ実行（{before} 件 → {after} 件{improved}）")

    return msgs


_BLOCK1_MASK_TYPES = (
    DebugMask.MaskType.DIFF,
    DebugMask.MaskType.EDGE,
    DebugMask.MaskType.SAT,
    DebugMask.MaskType.OR,
    DebugMask.MaskType.CLOSED,
)
_BLOCK2_MASK_TYPES = (
    DebugMask.MaskType.OR,
    DebugMask.MaskType.CLOSED,
)


def _build_mask_url_blocks(debug_masks):
    """マスク画像 (label, url) のリストを attempt ごとの 2 ブロックで返す。

    [性質] 準関数（DebugMask の mask_image.url 参照のみ・DB 書込なし）
    [入力] debug_masks: DebugMask の iterable（同一 OriginalImage 配下）
    [出力] (block1, block2)
      block1: 1 回目（attempt_no=1）の (label, url|None) リスト。長さ常に 5。
              順序は diff → edge → sat → or → closed。
      block2: 2 回目（attempt_no=2）の (label, url|None) リスト。
              attempt_no=2 のレコードが 1 件も無ければ [] を返す。
              ある場合は or → closed の 2 件固定。
    label の "mask_N" の番号は MaskType.choices の定義順（1〜5）に合わせる。
    """
    by_key = {(m.mask_type, m.attempt_no): m for m in debug_masks}
    type_to_idx = {
        mt: idx for idx, (mt, _) in enumerate(DebugMask.MaskType.choices, start=1)
    }
    verbose_of = dict(DebugMask.MaskType.choices)

    def _entry(mask_type, attempt_no):
        idx = type_to_idx[mask_type]
        label = f"mask_{idx}: {verbose_of[mask_type]}"
        rec = by_key.get((mask_type, attempt_no))
        url = rec.mask_image.url if rec else None
        return (label, url)

    block1 = [_entry(mt, 1) for mt in _BLOCK1_MASK_TYPES]

    has_attempt2 = any(m.attempt_no == 2 for m in debug_masks)
    block2 = [_entry(mt, 2) for mt in _BLOCK2_MASK_TYPES] if has_attempt2 else []

    return block1, block2


def _build_mask_white_ratios(debug_masks):
    """[性質] 純関数 / DebugMask.metadata の white_ratio を attempt_no 別に集約。

    [出力] {attempt_no: {mask_type: ratio}} 形式の dict。
    """
    ratios = {}
    for m in debug_masks:
        if not isinstance(m.metadata, dict):
            continue
        wr = m.metadata.get("white_ratio")
        if wr is None:
            continue
        ratios.setdefault(m.attempt_no, {})[m.mask_type] = wr
    return ratios


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
    last = _get_last_attempt(debug_json) or {}
    cf = last.get("candidates_filter") or []
    return [
        _format_candidate(c)
        for c in sorted(
            (x for x in cf if x.get("passed")),
            key=lambda x: -(x.get("area") or 0),
        )
    ]


def _candidates_all(debug_json):
    """passed 優先 → area 降順。"""
    last = _get_last_attempt(debug_json) or {}
    cf = last.get("candidates_filter") or []
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

    対象は最終 attempt の results（反転リトライがあれば 2 回目、なければ 1 回目）。
    """
    if not debug_json:
        return []
    last = _get_last_attempt(debug_json) or {}
    keys = ("top_left", "top_right", "bottom_right", "bottom_left")
    image_size = debug_json.get("image_size") or {}
    image_area = image_size.get("area") or 0

    # 各 polygon の座標と shoelace area を一旦計算
    raw = []
    for r in last.get("results") or []:
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


class RecalcDebugView(LoginRequiredMixin, View):
    """OpenCV デバッグキャッシュを再計算し、元画像詳細にリダイレクトする（POST 専用）。

    recalc_opencv_debug() で 3 ステップ（clear → detect → save）を実行する。
    OriginalImage.status / BusinessCard / raw_json は触らない。
    GET / その他メソッドは Django 標準の 405 応答（method_not_allowed）が返る。
    """

    def post(self, request, pk):
        user = request.user
        original = get_object_or_404(OriginalImage, pk=pk, user=user)
        recalc_opencv_debug(original)
        return redirect("originals:original_detail", pk=original.id)


class CardDeleteView(LoginRequiredMixin, View):
    """BusinessCard を削除し、元画像詳細にリダイレクトする（POST 専用）。

    削除対象は本人所有の BC のみ（user スコープで絞り込み）。
    Contact / ContactFieldConfidence は CASCADE で自動削除、card_image の FS 実体は
    post_delete シグナルで自動削除される。OriginalImage.raw_json は温存される。
    GET / その他メソッドは Django 標準の 405 応答（method_not_allowed）が返る。
    """

    def post(self, request, pk):
        user = request.user
        bc = get_object_or_404(BusinessCard, pk=pk, original_image__user=user)
        original_image_id = bc.original_image_id
        bc.delete()
        return redirect("originals:original_detail", pk=original_image_id)


class CardListView(LoginRequiredMixin, ListView):
    """名刺一覧画面（仕様書 v1.2.2 / Phase 4）。

    BusinessCard を Contact 情報とともに一覧表示する。
    7フィールドの AND 検索（name / organization / department / title / email / tel / address）。
    tel は personal_phone / mobile_phone / personal_fax の OR 一致。
    """

    model = BusinessCard
    template_name = "cards/card_list.html"
    context_object_name = "cards"
    paginate_by = 20

    _SEARCH_PARAMS = ("name", "organization", "department", "title", "email", "tel", "address")

    # v1.5.0: フィルタは ocr_status 由来 2 値 + ocr_result 5 値 の 7 値。
    # 仮想値 "_pending" / "_processing" は実フィールドにないため、queryset 構築時に
    # ocr_status クエリへ変換する。状態遷移順（pending → processing → 5 値結果）で並べる。
    _OCR_FILTER_PENDING = "_pending"
    _OCR_FILTER_PROCESSING = "_processing"
    _OCR_FILTER_CHOICES = (
        (_OCR_FILTER_PENDING,    "OCR待ち"),
        (_OCR_FILTER_PROCESSING, "OCR中"),
    ) + tuple(BusinessCard.OcrResult.choices)

    def _selected_ocr_filters(self):
        """有効なフィルタ値のリストを返す。0 件ならデフォルト（business_card のみ）。

        [性質] 純関数（DB操作なし、request.GET の読み取りのみ）

        request.GET は通常 QueryDict だが、A 側テストが dict を直接渡すケースに
        備えて getattr で getlist の有無を確認する defensive 実装（保険）。
        本筋は呼び出し側が QueryDict を渡すこと。
        """
        valid = {v for v, _ in self._OCR_FILTER_CHOICES}
        getlist = getattr(self.request.GET, "getlist", None)
        if callable(getlist):
            raw = getlist("ocr_result")
        else:
            value = self.request.GET.get("ocr_result")
            raw = [value] if value else []
        selected = [v for v in raw if v in valid]
        if not selected:
            return [BusinessCard.OcrResult.BUSINESS_CARD]
        return selected

    def _build_filter_q(self, selected):
        """[性質] 純関数 / 選択フィルタを Q オブジェクトに変換する。"""
        q = Q()
        if self._OCR_FILTER_PENDING in selected:
            q |= Q(ocr_status=BusinessCard.OcrStatus.PENDING)
        if self._OCR_FILTER_PROCESSING in selected:
            q |= Q(ocr_status=BusinessCard.OcrStatus.PROCESSING)
        real_results = [
            s for s in selected
            if s not in (self._OCR_FILTER_PENDING, self._OCR_FILTER_PROCESSING)
        ]
        if real_results:
            q |= Q(ocr_result__in=real_results)
        return q

    def get_queryset(self):
        user = self.request.user
        # confidence ドットは DUPLICATE_CHECK_FIELDS のみ対象、confirmed_at で
        # 「未確認」を区別する（DEBUG=True 時のみ表示）。
        selected = self._selected_ocr_filters()
        qs = (
            BusinessCard.objects.filter(original_image__user=user)
            .filter(self._build_filter_q(selected))
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
                has_unconfirmed_mid=Exists(
                    ContactFieldConfidence.objects.filter(
                        contact__business_card=OuterRef("pk"),
                        field_name__in=DUPLICATE_CHECK_FIELDS,
                        confidence=ContactFieldConfidence.Confidence.MID,
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
        if p.get("organization", "").strip():
            qs = qs.filter(contact__organization__icontains=p["organization"].strip())
        if p.get("department", "").strip():
            qs = qs.filter(contact__department__icontains=p["department"].strip())
        if p.get("title", "").strip():
            qs = qs.filter(contact__title__icontains=p["title"].strip())
        if p.get("email", "").strip():
            qs = qs.filter(contact__email__icontains=p["email"].strip())
        if p.get("tel", "").strip():
            tel = p["tel"].strip()
            qs = qs.filter(
                Q(contact__personal_phone__icontains=tel)
                | Q(contact__mobile_phone__icontains=tel)
                | Q(contact__personal_fax__icontains=tel)
            )
        if p.get("address", "").strip():
            qs = qs.filter(contact__address__icontains=p["address"].strip())

        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current(
            "",
            ["name", "organization", "department", "title", "email", "tel", "address", "ocr_result", "page"],
        )
        context["back"] = back

        context["active_app"] = "cards"
        context["active_menu"] = "cards:card_list"
        for key in self._SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        context["ocr_result_choices"] = self._OCR_FILTER_CHOICES
        context["selected_ocr_results"] = self._selected_ocr_filters()
        return context


class CardDetailView(LoginRequiredMixin, DetailView):
    """名刺詳細画面（仕様書 v1.4.2 / Phase 4）。

    OpenCV デバッグ情報の閲覧と Contact フィールド編集（ContactDetailView と同じ
    AJAX 編集 UI）を併せ持つ業務画面。Contact 編集パーツは _contact_field.html
    を再利用するため、ContactDetailView と同じ context（field_confidences /
    is_editable）を提供する。
    """

    model = BusinessCard
    template_name = "cards/card_detail.html"
    context_object_name = "card"
    pk_url_kwarg = "pk"

    def get_queryset(self):
        user = self.request.user
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
        context["contact"] = contact

        # Contact 編集 UI 用 context（ContactDetailView と同等、_contact_field.html 再利用）。
        # contact が None なら編集セクションを丸ごと出さないため、空辞書 + False で安全に。
        if contact is not None:
            context["field_confidences"] = contact.get_field_confidences()
            context["is_editable"] = (
                contact.status in (Contact.Status.PRIMARY, Contact.Status.ACTIVE)
                and contact.person.status == "active"
            )
        else:
            context["field_confidences"] = {}
            context["is_editable"] = False

        sibling_cards = (
            BusinessCard.objects.filter(original_image=self.object.original_image)
            .exclude(pk=self.object.pk)
            .select_related("contact")
            .order_by("card_index")
        )
        context["sibling_cards"] = sibling_cards

        # v1.5.0: BC.raw_json_1 / raw_json_2 を dict のまま渡す（テンプレで json_script フィルタ →
        # andypf-json-viewer で表示）。OI.raw_json 由来の card_json_str は廃止。
        context["raw_json_1"] = self.object.raw_json_1
        context["raw_json_2"] = self.object.raw_json_2

        return context
