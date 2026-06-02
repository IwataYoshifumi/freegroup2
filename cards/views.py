"""cards アプリの View 層（仕様書 v1.1.0 §8.3 / §8.7）。

View 層の責務は HTTP リクエスト/レスポンス処理とテンプレート選択のみ。
ビジネスロジックは services 層・tasks 層に委譲する。
"""

import json
import logging
import math
import statistics
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from back_navigator.back_navigator import BackNavigator

from .forms import UploadForm
from .models import BusinessCard, Contact, ContactFieldConfidence, DebugMask, OriginalImage
from .services.image_processor import convert_to_jpeg
from .services.opencv_debug_cache import recalc_opencv_debug

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
        business_cards = self.object.businesscard_set.all().order_by("created_at")
        context["business_cards"] = business_cards
        context["back"] = BackNavigator(self.request)

        # raw_json は dict のまま渡す（json_script フィルタで埋め込み、json-viewer で表示）
        raw_json = self.object.raw_json
        context["raw_json"] = raw_json

        debug_json = self.object.debug_json
        debug_masks = list(self.object.debug_masks.all())
        mask_white_ratios = _build_mask_white_ratios(debug_masks)
        block1_mask_urls, block2_mask_urls = _build_mask_blocks(debug_masks, debug_json)

        last_attempt = _get_last_attempt(debug_json) or {}

        context["debug_json"] = debug_json
        context["debug_summary"] = _build_debug_summary(
            debug_json, raw_json=raw_json, bc_count=business_cards.count()
        )
        context["block1_mask_urls"] = block1_mask_urls
        context["block2_mask_urls"] = block2_mask_urls
        context["mask_label_font"] = _overlay_label_font(
            (debug_json or {}).get("image_size")
        )
        context["candidates_passed"] = _candidates_passed(debug_json)
        context["candidates_all"] = _candidates_all(debug_json)
        context["candidates_dedup"] = last_attempt.get("candidates_dedup") or []
        context["warp_failures"] = last_attempt.get("warp_failures") or []
        context["overlay_polygons"] = _build_overlay_polygons(debug_json)
        context["warning_stripe"] = _build_warning_stripe(debug_json, mask_white_ratios)

        return context


# rev2 方式：area_too_small/area_too_large は廃止。サイズは絶対下限 too_small のみ。
_REJECT_REASONS_ORDER = ("too_small", "zero_size", "aspect_invalid")

# debug_json の attempt["masks"] のマスク名（順序固定）。
_MASK_NAMES = ("diff", "edge", "sat", "adaptive")


def _get_last_attempt(debug_json):
    """[性質] 純関数 / debug_json の最終 attempt dict を返す。なければ None。"""
    if not debug_json:
        return None
    attempts = debug_json.get("attempts") or []
    return attempts[-1] if attempts else None


def _flatten_attempt_candidates(attempt):
    """[性質] 純関数 / attempt["masks"] のマスク別 candidates_filter を1リストに平坦化する。

    各候補に由来マスク名 "mask" と、attempt 全体での連番 "index"（表示用の通し #）を付与する。
    マスク内の元 index は "mask_index" として温存する。
    """
    out = []
    masks = (attempt or {}).get("masks") or {}
    gi = 0
    for name in _MASK_NAMES:
        mr = masks.get(name) or {}
        for c in mr.get("candidates_filter") or []:
            d = dict(c)
            d["mask"] = name
            d["mask_index"] = c.get("index")
            d["index"] = gi
            out.append(d)
            gi += 1
    return out


def _attempt_contours_count(attempt):
    """[性質] 純関数 / attempt の全マスク合算の輪郭数を返す。"""
    masks = (attempt or {}).get("masks") or {}
    return sum((masks.get(n) or {}).get("contours_count", 0) for n in _MASK_NAMES)


def _build_debug_summary(debug_json, raw_json=None, bc_count=0):
    if not debug_json:
        return None
    last = _get_last_attempt(debug_json) or {}
    cf = _flatten_attempt_candidates(last)
    cd = last.get("candidates_dedup") or []
    wf = last.get("warp_failures") or []

    rejected = [x for x in cf if not x.get("passed")]
    reason_counter = Counter(x.get("reject_reason") or "" for x in rejected)
    reject_reason_str = ", ".join(
        f"{r}: {reason_counter.get(r, 0)}" for r in _REJECT_REASONS_ORDER
    )

    # 最終検出数は attempt 横断統合後（integrated_results）。旧 debug_json は最終 attempt にフォールバック。
    integrated = debug_json.get("integrated_results")
    if integrated is None:
        integrated = last.get("results") or []
    results_count = len(integrated)
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
        "contours_count": _attempt_contours_count(last),
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

    # 条件1: 最終 attempt のクローズ後マスク白画素率 ≥ 80%（マスク別の最大で判定）
    mwr = mask_white_ratios or {}
    last_ratios = mwr.get(last_attempt_no) or {}
    closed_ratios = [
        v for k, v in last_ratios.items()
        if isinstance(k, str) and k.endswith("_closed") and v is not None
    ]
    if closed_ratios and max(closed_ratios) >= 0.80:
        msgs.append(f"クローズ後マスク白画素率が異常 ({max(closed_ratios) * 100:.1f}%)")

    # 条件2: 最終 attempt の results が 0 件
    results = last.get("results") or []
    if len(results) == 0:
        msgs.append("最終検出 0 件")

    # （rev2: area_too_large 廃止のため旧「巨大候補」条件は削除。巨大判定はオーバーレイ
    #   _build_overlay_polygons の is_giant が担う）

    # 条件3: 反転リトライが実施されたか
    or_inv = debug_json.get("or_inversion") or {}
    if or_inv.get("attempted"):
        before = or_inv.get("passed_before", 0)
        after = or_inv.get("passed_after", 0)
        improved = "／改善あり" if or_inv.get("improved") else "／改善なし"
        msgs.append(f"反転リトライ実行（{before} 件 → {after} 件{improved}）")

    return msgs


# rev2 方式：マスク別（生 diff/edge/sat ＋ クローズ diff_closed/edge_closed/sat_closed）。
# 各ブロック内は「生 3 → クローズ 3」の順で並べる（テンプレートは :3 / 3: でスライスする）。
_RAW_MASK_TYPES = (
    DebugMask.MaskType.DIFF,
    DebugMask.MaskType.EDGE,
    DebugMask.MaskType.SAT,
    DebugMask.MaskType.ADAPTIVE,
)
_CLOSED_MASK_TYPES = (
    DebugMask.MaskType.DIFF_CLOSED,
    DebugMask.MaskType.EDGE_CLOSED,
    DebugMask.MaskType.SAT_CLOSED,
    DebugMask.MaskType.ADAPTIVE_CLOSED,
)
_BLOCK_MASK_TYPES = _RAW_MASK_TYPES + _CLOSED_MASK_TYPES

# MaskType → (マスク名, クローズ後か)。候補（candidates_filter）はクローズ後マスクから
# 抽出される（opencv_detector._extract_candidates_from_mask）ため、矩形オーバーレイは
# クローズ後マスク（is_closed=True）にのみ重ねる。
_MASK_TYPE_TO_NAME = {
    DebugMask.MaskType.DIFF:            ("diff", False),
    DebugMask.MaskType.EDGE:            ("edge", False),
    DebugMask.MaskType.SAT:             ("sat",  False),
    DebugMask.MaskType.ADAPTIVE:        ("adaptive", False),
    DebugMask.MaskType.DIFF_CLOSED:     ("diff", True),
    DebugMask.MaskType.EDGE_CLOSED:     ("edge", True),
    DebugMask.MaskType.SAT_CLOSED:      ("sat",  True),
    DebugMask.MaskType.ADAPTIVE_CLOSED: ("adaptive", True),
}

# 「惜しい棄却」を太字赤で出す閾値。too_small はこの面積比以上だけ near（緑採用品と同等規模）。
_OVERLAY_NEAR_AREA_RATIO = 0.03


def _rotated_rect_corners(cx, cy, w, h, angle_deg):
    """[性質] 純関数 / minAreaRect (中心+W×H+角度) の 4 頂点を返す（cv2.boxPoints 相当）。

    OpenCV cv::RotatedRect::points と同じ式。返り値は [(x,y), ...] ×4（元画像ピクセル座標）。
    candidates_filter には四隅が保存されていない（中心+サイズ+角度のみ）ため、描画用にここで復元する。
    """
    rad = math.radians(angle_deg)
    b = math.cos(rad) * 0.5
    a = math.sin(rad) * 0.5
    p0 = (cx - a * h - b * w, cy + b * h - a * w)
    p1 = (cx + a * h - b * w, cy - b * h - a * w)
    p2 = (2 * cx - p0[0], 2 * cy - p0[1])
    p3 = (2 * cx - p1[0], 2 * cy - p1[1])
    return [p0, p1, p2, p3]


def _classify_overlay_candidate(c):
    """[性質] 純関数 / 候補を描画カテゴリ "passed"/"near"/"noise" に分類する。

    passed=true               → "passed"（緑・常時表示）
    reject_reason=aspect_invalid、または too_small かつ area_ratio≥0.03 → "near"（赤・常時表示）
    zero_size、または too_small かつ area_ratio<0.03 → "noise"（既定非表示・トグルで表示）
    """
    if c.get("passed"):
        return "passed"
    reason = c.get("reject_reason") or ""
    if reason == "aspect_invalid":
        return "near"
    if reason == "too_small" and (c.get("area_ratio") or 0) >= _OVERLAY_NEAR_AREA_RATIO:
        return "near"
    return "noise"


def _format_overlay_candidate(c, num):
    """[性質] 純関数 / 1 候補を SVG 描画用 dict（points 文字列・ラベル位置・カテゴリ）に整形する。

    num はセクション4テーブルと一致させる全体通し番号（diff→edge→sat を貫く index）。
    """
    rect_center = c.get("rect_center") or [0, 0]
    rect_size = c.get("rect_size") or [0, 0]
    cx = rect_center[0] if len(rect_center) > 0 else 0
    cy = rect_center[1] if len(rect_center) > 1 else 0
    w = rect_size[0] if len(rect_size) > 0 else 0
    h = rect_size[1] if len(rect_size) > 1 else 0
    corners = _rotated_rect_corners(cx, cy, w, h, c.get("rect_angle") or 0)
    points_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in corners)
    return {
        "num": num,
        "points_str": points_str,
        "cx": cx,
        "cy": cy,
        "category": _classify_overlay_candidate(c),
        "reason": c.get("reject_reason") or "",
    }


def _build_attempt_overlays(attempt):
    """[性質] 純関数 / 1 attempt の候補を {マスク名: [描画用候補]} に整形する。

    全体通し番号（num）は _flatten_attempt_candidates と同じ規則（diff→edge→sat の順で
    全候補を貫く連番）で振るため、セクション4テーブルの「#」列と一致する。
    """
    overlays = {name: [] for name in _MASK_NAMES}
    masks = (attempt or {}).get("masks") or {}
    gi = 0
    for name in _MASK_NAMES:
        mr = masks.get(name) or {}
        for c in mr.get("candidates_filter") or []:
            overlays[name].append(_format_overlay_candidate(c, gi))
            gi += 1
    return overlays


def _overlay_label_font(image_size):
    """[性質] 純関数 / マスクサムネ上のラベル文字サイズ（viewBox 単位）を返す。

    マスク画像は約 320px 幅で表示されるため、画面上でおよそ 12px になるよう
    画像幅に比例させる（既存の元画像オーバーレイ用 120px はサムネには過大なため別途算出）。
    """
    w = (image_size or {}).get("width") or 0
    return max(12, round(w * 0.0375))


def _build_mask_blocks(debug_masks, debug_json):
    """マスク画像 dict のリストを attempt ごとの 2 ブロックで返す。

    [性質] 準関数（DebugMask の mask_image.url 参照のみ・DB 書込なし）
    [入力]
      debug_masks: DebugMask の iterable（同一 OriginalImage 配下）
      debug_json:  OriginalImage.debug_json（候補のオーバーレイ元）
    [出力] (block1, block2)。各ブロックは長さ 8 の dict リスト：
      {label, url|None, mask_name, is_closed, overlay}
      順序は diff → edge → sat → adaptive → diff_closed → edge_closed → sat_closed → adaptive_closed。
      overlay はクローズ後マスク（is_closed=True）にのみ入る（候補抽出元のため）。
        block1 は attempt_no=1、block2 は attempt_no=2 の候補を載せる（取り違え防止）。
      block2 は attempt_no=2 の DebugMask が無ければ []。
    label の "mask_N" の番号は MaskType.choices の定義順（1〜8）に合わせる。
    """
    by_key = {(m.mask_type, m.attempt_no): m for m in debug_masks}
    type_to_idx = {
        mt: idx for idx, (mt, _) in enumerate(DebugMask.MaskType.choices, start=1)
    }
    verbose_of = dict(DebugMask.MaskType.choices)
    attempt_by_no = {
        a.get("attempt_no"): a for a in (debug_json or {}).get("attempts") or []
    }

    def _block(attempt_no):
        overlays = _build_attempt_overlays(attempt_by_no.get(attempt_no))
        slots = []
        for mt in _BLOCK_MASK_TYPES:
            mask_name, is_closed = _MASK_TYPE_TO_NAME[mt]
            idx = type_to_idx[mt]
            rec = by_key.get((mt, attempt_no))
            slots.append({
                "label": f"mask_{idx}: {verbose_of[mt]}",
                "url": rec.mask_image.url if rec else None,
                "mask_name": mask_name,
                "is_closed": is_closed,
                "overlay": overlays.get(mask_name, []) if is_closed else [],
            })
        return slots

    block1 = _block(1)
    has_attempt2 = any(m.attempt_no == 2 for m in debug_masks)
    block2 = _block(2) if has_attempt2 else []

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
    cf = _flatten_attempt_candidates(last)
    return [
        _format_candidate(c)
        for c in sorted(
            (x for x in cf if x.get("passed")),
            key=lambda x: -(x.get("area") or 0),
        )
    ]


def _candidates_all(debug_json):
    """passed 優先 → area 降順。マスク別候補を平坦化したうえで並べる。"""
    last = _get_last_attempt(debug_json) or {}
    cf = _flatten_attempt_candidates(last)
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

    対象は attempt 横断統合の最終結果（integrated_results）。各要素の由来（origin）も付与する
    （normal=通常 / inverted=反転）。integrated_results が無い旧 debug_json は最終 attempt に
    フォールバックする。
    """
    if not debug_json:
        return []
    keys = ("top_left", "top_right", "bottom_right", "bottom_left")
    image_size = debug_json.get("image_size") or {}
    image_area = image_size.get("area") or 0

    source = debug_json.get("integrated_results")
    if source is None:
        source = (_get_last_attempt(debug_json) or {}).get("results") or []

    # 各 polygon の座標と shoelace area を一旦計算
    raw = []
    for r in source:
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
            "origin": r.get("origin"),
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
        # 由来マーク：反転由来は "↺" を番号に添えて画面で判別可能にする（normal は無印）。
        origin = r.get("origin")
        origin_mark = "↺" if origin == "inverted" else ""
        items.append({
            "card_index": r["card_index"],
            "points_str": points_str,
            "centroid_x": cx,
            "centroid_y": cy,
            "is_giant": is_giant,
            "area_ratio": r["area_ratio"],
            "origin": origin,
            "origin_mark": origin_mark,
        })
    return items


class RecalcDebugView(View):
    """OpenCV デバッグキャッシュを再計算し、元画像詳細にリダイレクトする（POST 専用）。

    recalc_opencv_debug() で 3 ステップ（clear → detect → save）を実行する。
    OriginalImage.status / BusinessCard / raw_json は触らない。
    GET / その他メソッドは Django 標準の 405 応答（method_not_allowed）が返る。
    """

    def post(self, request, pk):
        user = get_current_user(request)
        original = get_object_or_404(OriginalImage, pk=pk, user=user)
        recalc_opencv_debug(original)
        return redirect("originals:original_detail", pk=original.id)


class CardDeleteView(View):
    """BusinessCard を削除し、元画像詳細にリダイレクトする（POST 専用）。

    削除対象は本人所有の BC のみ（user スコープで絞り込み）。
    Contact / ContactFieldConfidence は CASCADE で自動削除、card_image の FS 実体は
    post_delete シグナルで自動削除される。OriginalImage.raw_json は温存される。
    GET / その他メソッドは Django 標準の 405 応答（method_not_allowed）が返る。
    """

    def post(self, request, pk):
        user = get_current_user(request)
        bc = get_object_or_404(BusinessCard, pk=pk, original_image__user=user)
        original_image_id = bc.original_image_id
        bc.delete()
        return redirect("originals:original_detail", pk=original_image_id)


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

    def _selected_ocr_results(self):
        """有効な ocr_result 値のリストを返す。0 件なら BUSINESS_CARD のみのデフォルト扱い。

        [性質] 純関数（DB操作なし、request.GET の読み取りのみ）
        [入力] self.request.GET（ocr_result マルチ値）
        [出力] list[str]: 有効な OcrResult.values の部分集合
        """
        valid = set(BusinessCard.OcrResult.values)
        raw = self.request.GET.getlist("ocr_result")
        selected = [v for v in raw if v in valid]
        if not selected:
            return [BusinessCard.OcrResult.BUSINESS_CARD]
        return selected

    def get_queryset(self):
        user = get_current_user(self.request)
        qs = (
            BusinessCard.objects.filter(
                original_image__user=user,
                ocr_result__in=self._selected_ocr_results(),
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
            ["name", "company", "department", "title", "email", "tel", "address", "ocr_result", "page"],
        )
        context["back"] = back

        context["active_app"] = "cards"
        context["active_menu"] = "cards:card_list"
        for key in self._SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        context["ocr_result_choices"] = BusinessCard.OcrResult.choices
        context["selected_ocr_results"] = self._selected_ocr_results()
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
