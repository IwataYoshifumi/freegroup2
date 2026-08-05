"""cards アプリの View 層（仕様書 v1.1.0 §8.3 / §8.7）。

View 層の責務は HTTP リクエスト/レスポンス処理とテンプレート選択のみ。
ビジネスロジックは services 層・tasks 層に委譲する。
"""

import base64
import json
import logging
import math
import statistics
from collections import Counter
from io import BytesIO

import numpy as np
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Exists, Max, OuterRef, Q, Value
from django.db.models.functions import Replace
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import DetailView, ListView
from PIL import Image

from back_navigator.back_navigator import BackNavigator

from .forms import BusinessCardEditForm
from .models import BusinessCard, DebugMask, OriginalImage
from .services.image_processor import (
    convert_to_jpeg,
    extract_exif_to_json,
    validate_image,
)
from .services.detectors.opencv_detector import warp_card_from_points
from .services.opencv_debug_cache import recalc_opencv_debug
from .services.osd import apply_upright
from .tasks.crop_cards import _unlink_card_image, save_and_create_business_card
from .tasks.ocr_pipeline import _update_original_image_status
from config.constants import DUPLICATE_CHECK_FIELDS
from contacts.models import Contact, ContactFieldConfidence

logger = logging.getLogger(__name__)


def placeholder_view(request):
    return HttpResponse("準備中", content_type="text/plain; charset=utf-8")


def _overwrite_image_file(img, path, image_format):
    """[性質] 副作用あり（ファイル上書き）。img を path へ image_format 形式で上書き保存する。

    回転焼き直し（_rotate_and_overwrite_card_image）のファイル書き込み部分を分離した
    ヘルパー。失敗時に呼び出し側のトランザクションをロールバックさせるため例外はそのまま伝播する。
    """
    img.save(path, format=image_format)


def _rotate_and_overwrite_card_image(card, direction):
    """[性質] 副作用あり（DB 書込 + 画像ファイル上書き）。card_image を実際に 90° 回す。

    [入力] card: BusinessCard（card_image を持つこと）, direction: str（"cw"=時計回り90° /
            "ccw"=反時計回り90°）
    [出力] None

    OSD 正立化後の card_image を Pillow で direction 方向に 90° 回し、同一ファイルパスへ上書き
    保存する。あわせて orientation を normal にリセットする（実画像と orientation バッジの食い違いを
    消すため）。DB 更新（orientation / updated_at）とファイル上書きは同一トランザクションで行い、
    ファイル保存に失敗した場合は DB 変更もロールバックする（中途半端な状態を残さない）。
    元の切り抜き前画像（OriginalImage 側）には一切触れない。
    """
    path = card.card_image.path
    with Image.open(path) as opened:
        opened.load()
        # PIL の ROTATE_* は反時計回り基準：ROTATE_270 = 時計回り90°、ROTATE_90 = 反時計回り90°。
        if direction == "cw":
            rotated = opened.transpose(Image.Transpose.ROTATE_270)
        else:
            rotated = opened.transpose(Image.Transpose.ROTATE_90)
        image_format = opened.format or "JPEG"

    with transaction.atomic():
        card.orientation = BusinessCard.ORIENTATION_NORMAL
        # 自動正位化の診断値は手動回転で嘘になるためクリア（手動相当＝null へリセット）。
        card.orientation_correction = None
        card.orientation_score = None
        card.save(update_fields=[
            "orientation",
            "orientation_correction",
            "orientation_score",
            "updated_at",
        ])
        _overwrite_image_file(rotated, path, image_format)


class UploadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """名刺画像アップロード画面（複数ファイル + ドラッグ&ドロップ対応）。

    GET：ドロップゾーン付きフォームを表示。
    POST：複数選択 / D&D の ``images`` と、後方互換の単一 ``image`` の両方を受け取り、
      各ファイルに validate_image を **個別適用** する。1 ファイル = 1 OriginalImage を厳守し
      （複数を 1 レコードにまとめない。process_opencv / detected_count 集計を壊さないため）、
      通ったファイルごとに独立した OriginalImage（status=pending）を作成する。弾いた
      ファイルは作成せず、理由つきでスキップ一覧に積む（部分成功・全件巻き戻しはしない）。
      OCR / OpenCV はこの View では走らせない（既存方針：View 層は pending 作成までに限定）。

    後方互換：成功 1 件・スキップ 0 のときは従来どおり当該 OriginalImage 詳細へ 302 redirect
      （単一ファイルアップロードの既存挙動・既存テストを維持）。複数 / 部分失敗時はアップロード
      画面に結果サマリ（成功 N 件 / スキップ M 件と理由）を表示する。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用、rev20 No.2 ★2）：OriginalImage 作成 → add_originalimage。
    permission_required = "cards.add_originalimage"

    template_name = "cards/upload.html"

    def get(self, request):
        return render(request, self.template_name, self._context(request))

    def post(self, request):
        # 複数選択 / D&D は name="images"、後方互換の単一は name="image"。両方拾う。
        files = request.FILES.getlist("images") + request.FILES.getlist("image")
        if not files:
            return render(
                request,
                self.template_name,
                self._context(request, form_error="ファイルが選択されていません。"),
            )

        user = request.user
        created = []
        skipped = []
        for uploaded_file in files:
            # ① 既存バリデーション（拡張子 JPEG/PNG・5MB）を 1 ファイル単位で適用。
            try:
                validate_image(uploaded_file)
            except ValidationError as exc:
                skipped.append(
                    {"name": uploaded_file.name, "reason": " ".join(exc.messages)}
                )
                continue
            # ② EXIF 抽出は convert_to_jpeg の exif_transpose 前に行う（§7.2）。
            #    画像として開けない（拡張子詐称等）ものはここで弾いてスキップ扱いにする。
            try:
                exif_json = extract_exif_to_json(uploaded_file)
                jpeg_bytes = convert_to_jpeg(uploaded_file)
            except Exception:
                logger.warning(
                    "UploadView: 画像変換失敗のためスキップ: %s", uploaded_file.name
                )
                skipped.append(
                    {"name": uploaded_file.name, "reason": "画像として読み込めませんでした。"}
                )
                continue
            # ③ 1 ファイル = 1 OriginalImage（status=pending）。OCR/OpenCV は走らせない。
            original = OriginalImage(
                user=user,
                status=OriginalImage.STATUS_PENDING,
                exif_json=exif_json,
            )
            original.image_file.save(
                f"{original.id}.jpg", ContentFile(jpeg_bytes), save=False
            )
            original.save()
            created.append(original)

        # 後方互換：成功 1 件・スキップ 0 → 従来どおり当該詳細へ 302 redirect。
        if len(created) == 1 and not skipped:
            back = BackNavigator(request)
            target_url = reverse(
                "originals:original_detail", kwargs={"pk": created[0].id}
            )
            return HttpResponseRedirect(back.append_url(target_url))

        # 複数 / 部分失敗 → 結果サマリを表示。
        return render(
            request,
            self.template_name,
            self._context(request, created=created, skipped=skipped),
        )

    def _context(self, request, *, created=None, skipped=None, form_error=None):
        return {
            "active_app": "cards",
            "active_menu": "cards:card_upload",
            "back": BackNavigator(request),
            "created": created or [],
            "skipped": skipped or [],
            "form_error": form_error,
        }


# 元画像一覧の多段ソート（persons の ?sort=key,-key 機構を流用。列は元画像一覧の実カラムに限定）。
ORIGINAL_LIST_SORT_FIELD_MAP = {
    "uploaded": "created_at",
    "status": "status",
    "detected": "detected_count",
}
ORIGINAL_LIST_SORT_MAX_KEYS = 3


def _parse_original_sort(params):
    """単一 sort（例 "status,-detected"）を [(key, direction), ...] に解決（純関数）。許可外/空/二重は無視。"""
    raw = params.get("sort") or ""
    tokens = []
    seen = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("-"):
            direction, key = "desc", chunk[1:]
        else:
            direction, key = "asc", chunk
        if key not in ORIGINAL_LIST_SORT_FIELD_MAP or key in seen:
            continue
        seen.add(key)
        tokens.append((key, direction))
        if len(tokens) >= ORIGINAL_LIST_SORT_MAX_KEYS:
            break
    return tokens


def _apply_original_list_sort(qs, params):
    """OriginalImage QuerySet に多段ソートを適用（純関数）。指定無しは qs のまま。"""
    tokens = _parse_original_sort(params)
    if not tokens:
        return qs
    order = []
    for key, direction in tokens:
        prefix = "-" if direction == "desc" else ""
        order.append(prefix + ORIGINAL_LIST_SORT_FIELD_MAP[key])
    order.append("pk")
    return qs.order_by(*order)


def _original_sort_context(params):
    """ソートUI描画用 context（純関数）。sort_rows / sort_is_active / sort_value。"""
    tokens = _parse_original_sort(params)
    rows = [{"key": k, "dir": d} for (k, d) in tokens]
    while len(rows) < ORIGINAL_LIST_SORT_MAX_KEYS:
        rows.append({"key": "", "dir": "asc"})
    return {
        "sort_rows": rows,
        "sort_is_active": bool(tokens),
        "sort_value": ",".join(("-" + k if d == "desc" else k) for k, d in tokens),
    }


class OriginalListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    # 認可（Phase 7 段3-2 を v1.7 へ適用、rev20 No.5 ★2）：OriginalImage 閲覧 → view_originalimage。
    permission_required = "cards.view_originalimage"
    model = OriginalImage
    template_name = "cards/original_list.html"
    context_object_name = "originals"
    # 元画像一覧は監視用途のため 20/50/100（既定 20）。名刺一覧の 50/100/200 とは別の刻み。
    _PER_PAGE_CHOICES = (20, 50, 100)

    def _resolve_per_page(self):
        """[性質] 純関数 / per_page を 20/50/100 に解決。無指定・不正・範囲外は 20。"""
        try:
            n = int(self.request.GET.get("per_page", 20))
        except (TypeError, ValueError):
            return 20
        return n if n in self._PER_PAGE_CHOICES else 20

    def get_paginate_by(self, queryset):
        return self._resolve_per_page()

    def get_queryset(self):
        user = self.request.user
        qs = (
            OriginalImage.objects.filter(user=user)
            # user__person はアップロード者列の Person 氏名表示（N+1 回避）。
            .select_related("user", "user__person")
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

        # 既定はアップロード日時の降順。?sort=key,-key,... 指定があれば多段ソートで差し替える。
        qs = qs.order_by("-created_at")
        return _apply_original_list_sort(qs, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current(
            "元画像一覧",
            ["status", "date_from", "date_to", "sort", "per_page", "page"],
        )
        context["back"] = back

        context["active_app"] = "cards"
        context["active_menu"] = "originals:original_list"
        context["status_choices"] = OriginalImage.STATUS_CHOICES
        context["selected_statuses"] = self.request.GET.getlist("status")
        context["date_from"] = self.request.GET.get("date_from", "")
        context["date_to"] = self.request.GET.get("date_to", "")
        # ソート（折りたたみ）と表示件数セレクタ用 context。
        context.update(_original_sort_context(self.request.GET))
        context["per_page"] = self._resolve_per_page()
        context["per_page_choices"] = self._PER_PAGE_CHOICES
        return context


class OriginalDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    # 認可（Phase 7 段3-2 を v1.7 へ適用、rev20 No.6 ★2）：OriginalImage 閲覧 → view_originalimage。
    permission_required = "cards.view_originalimage"
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
        # 再検出ボタンの表示可否。本番では非表示（実行ガードは RecalcDebugView 側が本体）。
        # Django 標準の {% if debug %} は INTERNAL_IPS 依存で罠があるため View から明示的に渡す。
        context["recalc_debug_enabled"] = settings.DEBUG
        business_cards = self.object.businesscard_set.all().order_by("created_at")
        back = BackNavigator(self.request)
        back.push_current("元画像詳細", ["pk"])
        context["back"] = back

        # v1.5.0: OriginalImage.raw_json の読み出しは廃止。OCR 結果は BC.raw_json_1/2 を参照。
        debug_json = self.object.debug_json
        debug_masks = list(self.object.debug_masks.all())
        mask_white_ratios = _build_mask_white_ratios(debug_masks)

        # rev2/rev3：マスク別に「そのマスク自身の描画対象候補（passed/near、noise 除外）」を view 段で構築。
        overlay_data = _build_mask_overlay_data(debug_json)
        attempts = (debug_json or {}).get("attempts") or []

        context["debug_json"] = debug_json
        context["debug_summary"] = _build_debug_summary(
            debug_json,
            business_cards=business_cards,
            bc_count=business_cards.count(),
        )
        # マスク表示（attempt 別・実在マスクのみ・クローズ後に候補矩形を重畳）。
        context["mask_attempt_blocks"] = _build_mask_attempt_blocks(debug_masks, overlay_data)
        # 候補一覧テーブル（マスク別・マスク内 idx＝オーバーレイ番号と一致、描画対象＝passed/near のみ）。
        context["candidates_table"] = _build_candidates_table(overlay_data)
        context["candidates_dedup"] = [
            d for a in attempts for d in (a.get("candidates_dedup") or [])
        ]
        context["warp_failures"] = [
            w for a in attempts for w in (a.get("warp_failures") or [])
        ]
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


# rev2/rev3 の reject_reason は aspect_invalid / too_small / zero_size。
_REJECT_REASONS_ORDER = ("aspect_invalid", "too_small", "zero_size")

# debug_json の attempt["masks"] のマスク名（順序固定）。マスク内 idx・表示順の基準。
_MASK_NAMES = ("diff", "edge", "sat", "adaptive")

# 各マスク名 → (生マスク種別, クローズ後マスク種別)。候補はクローズ後マスク上に重ねる。
_MASK_NAME_TYPES = {
    "diff": (DebugMask.MaskType.DIFF, DebugMask.MaskType.DIFF_CLOSED),
    "edge": (DebugMask.MaskType.EDGE, DebugMask.MaskType.EDGE_CLOSED),
    "sat": (DebugMask.MaskType.SAT, DebugMask.MaskType.SAT_CLOSED),
    "adaptive": (DebugMask.MaskType.ADAPTIVE, DebugMask.MaskType.ADAPTIVE_CLOSED),
}

# 「惜しい棄却」を赤で描画する閾値。too_small はこの面積比以上のみ near 扱い（採用品と同等規模）。
_OVERLAY_NEAR_AREA_RATIO = 0.03


def _get_last_attempt(debug_json):
    """[性質] 純関数 / debug_json の最終 attempt dict を返す。なければ None。"""
    if not debug_json:
        return None
    attempts = debug_json.get("attempts") or []
    return attempts[-1] if attempts else None


def _final_results(debug_json):
    """[性質] 純関数 / 最終検出結果（rev2/rev3 の integrated_results、無ければ attempts[-1].results）。"""
    if not debug_json:
        return []
    if "integrated_results" in debug_json:
        return debug_json.get("integrated_results") or []
    attempts = debug_json.get("attempts") or []
    return (attempts[-1].get("results") or []) if attempts else []


def _all_candidates(debug_json):
    """[性質] 純関数 / 全 attempt × 全 mask の candidates_filter を平坦化（統計用、描画対象に絞らない）。"""
    out = []
    for a in (debug_json or {}).get("attempts") or []:
        masks = a.get("masks") or {}
        if isinstance(masks, dict):
            for name in _MASK_NAMES:
                out.extend((masks.get(name) or {}).get("candidates_filter") or [])
            for name, mdata in masks.items():
                if name in _MASK_NAMES:
                    continue
                out.extend((mdata or {}).get("candidates_filter") or [])
        out.extend(a.get("candidates_filter") or [])  # 旧構造フォールバック
    return out


def _is_drawable_candidate(c):
    """[性質] 純関数 / overlay 描画対象か判定する（noise は view 段で除外＝CSS の display:none に頼らない）。

    描画対象： passed=True（緑）／ reject_reason=aspect_invalid（赤）／
              reject_reason=too_small かつ area_ratio>=0.03（赤）。それ以外（zero_size・極小 too_small）は False。
    """
    if c.get("passed"):
        return True
    reason = c.get("reject_reason") or ""
    if reason == "aspect_invalid":
        return True
    if reason == "too_small" and (c.get("area_ratio") or 0) >= _OVERLAY_NEAR_AREA_RATIO:
        return True
    return False


def _rotated_rect_points(c):
    """[性質] 純関数 / rect_center/size/angle から回転矩形 4 隅の points 文字列と重心を返す。

    座標は元画像ピクセル系（cv2 RotatedRect::points 相当）。SVG は viewBox=image_size +
    preserveAspectRatio で closed_image の表示寸法へ自動フィットするため、ここでのスケール変換は不要
    （元画像オーバーレイ _build_overlay_polygons と同じ手法）。
    """
    rc = c.get("rect_center") or [0, 0]
    rs = c.get("rect_size") or [0, 0]
    cx = rc[0] if len(rc) > 0 else 0
    cy = rc[1] if len(rc) > 1 else 0
    w = rs[0] if len(rs) > 0 else 0
    h = rs[1] if len(rs) > 1 else 0
    rad = math.radians(c.get("rect_angle") or 0)
    bx = math.cos(rad) * 0.5
    ax = math.sin(rad) * 0.5
    p0 = (cx - ax * h - bx * w, cy + bx * h - ax * w)
    p1 = (cx + ax * h - bx * w, cy - bx * h - ax * w)
    p2 = (2 * cx - p0[0], 2 * cy - p0[1])
    p3 = (2 * cx - p1[0], 2 * cy - p1[1])
    points_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in (p0, p1, p2, p3))
    return points_str, cx, cy


def _drawable_candidate_dict(c, idx, attempt_no, mask_name):
    """[性質] 純関数 / 描画対象候補 1 件を overlay/テーブル兼用の表示 dict に整形する。

    idx はマスク内（描画対象に絞った後の）連番。category は passed→緑 / それ以外→near(赤)。
    """
    points_str, cx, cy = _rotated_rect_points(c)
    area = c.get("area") or 0
    ratio = c.get("area_ratio") or 0
    rs = c.get("rect_size") or [0, 0]
    rw = rs[0] if len(rs) > 0 else 0
    rh = rs[1] if len(rs) > 1 else 0
    aspect = c.get("aspect_ratio")
    return {
        "idx": idx,
        "attempt_no": attempt_no,
        "mask_name": mask_name,
        "points_str": points_str,
        "cx": cx,
        "cy": cy,
        "category": "passed" if c.get("passed") else "near",
        "passed": bool(c.get("passed")),
        "reason": c.get("reject_reason") or "",
        "area_str": f"{area:,.0f}",
        "area_ratio_pct_str": f"{ratio * 100:.1f}",
        "rect_w_str": f"{rw:.0f}",
        "rect_h_str": f"{rh:.0f}",
        "aspect_ratio_str": (f"{aspect:.2f}" if aspect is not None else "-"),
    }


def _build_mask_overlay_data(debug_json):
    """[性質] 純関数 / {(attempt_no, mask_name): [描画対象候補]} を返す。

    各マスクの candidates_filter から **そのマスク自身の** 描画対象（passed/near）だけを抽出し、
    マスク内連番 idx を振る。noise はここで除外する（DOM に出さない）。マスクをまたいで混ぜない。
    """
    data = {}
    for a in (debug_json or {}).get("attempts") or []:
        attempt_no = a.get("attempt_no")
        masks = a.get("masks")
        if not isinstance(masks, dict):
            continue
        for name in _MASK_NAMES:
            cf = (masks.get(name) or {}).get("candidates_filter") or []
            drawn = []
            idx = 0
            for c in cf:
                if not _is_drawable_candidate(c):
                    continue
                drawn.append(_drawable_candidate_dict(c, idx, attempt_no, name))
                idx += 1
            data[(attempt_no, name)] = drawn
    return data


def _build_mask_attempt_blocks(debug_masks, overlay_data):
    """[性質] 準関数 / attempt 別のマスク表示ブロックを返す（実在マスクのみ・旧 OR/closed 固定枠は出さない）。

    [出力] [{attempt_no, title, raw:[{label,url}], closed:[{label,url,mask_name,overlay}]}]
      raw  : 生マスク（diff/edge/sat/adaptive のうち実在分）。overlay なし。
      closed: クローズ後マスク（候補抽出元）。overlay に自分のマスクの描画対象候補を載せる。
    """
    verbose_of = dict(DebugMask.MaskType.choices)
    by_key = {(m.mask_type, m.attempt_no): m for m in debug_masks}
    attempt_nos = sorted({m.attempt_no for m in debug_masks})

    def _url(mt, attempt_no):
        rec = by_key.get((mt, attempt_no))
        return rec.mask_image.url if (rec and rec.mask_image) else None

    blocks = []
    for an in attempt_nos:
        raw, closed = [], []
        for name in _MASK_NAMES:
            raw_mt, closed_mt = _MASK_NAME_TYPES[name]
            if (raw_mt, an) in by_key:
                raw.append({"label": verbose_of.get(raw_mt, raw_mt), "url": _url(raw_mt, an)})
            if (closed_mt, an) in by_key:
                closed.append(
                    {
                        "label": verbose_of.get(closed_mt, closed_mt),
                        "url": _url(closed_mt, an),
                        "mask_name": name,
                        "overlay": overlay_data.get((an, name), []),
                    }
                )
        if raw or closed:
            title = "1回目（通常処理）" if an == 1 else (
                "2回目（反転リトライ）" if an == 2 else f"attempt {an}"
            )
            blocks.append({"attempt_no": an, "title": title, "raw": raw, "closed": closed})
    return blocks


def _build_candidates_table(overlay_data):
    """[性質] 純関数 / 描画対象候補をマスク別（attempt → _MASK_NAMES 順 → idx）に並べたテーブル行。

    各行の idx はマスク内連番で、同じ (attempt, mask) のオーバーレイ番号と一致する。
    """
    rows = []
    for key in sorted(
        overlay_data.keys(),
        key=lambda k: (k[0], _MASK_NAMES.index(k[1]) if k[1] in _MASK_NAMES else 99),
    ):
        rows.extend(overlay_data[key])
    return rows


def _build_debug_summary(debug_json, business_cards=None, bc_count=0):
    if not debug_json:
        return None
    last = _get_last_attempt(debug_json) or {}
    attempts = debug_json.get("attempts") or []
    # rev2/rev3：候補は per-mask、最終検出は integrated_results。dedup/warp は attempt 横断で集計。
    cf = _all_candidates(debug_json)
    cd = [c for a in attempts for c in (a.get("candidates_dedup") or [])]
    results = _final_results(debug_json)
    wf = [w for a in attempts for w in (a.get("warp_failures") or [])]

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

    # contours_count は per-mask（masks[name].contours_count）合算（全 attempt）。
    contours_count = 0
    for a in attempts:
        masks = a.get("masks") or {}
        if isinstance(masks, dict):
            for mdata in masks.values():
                contours_count += (mdata or {}).get("contours_count", 0) or 0

    return {
        "image_size": debug_json.get("image_size") or {},
        "contours_count": contours_count,
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

    # 条件1: 最終 attempt の closed マスク白画素率 ≥ 80%（rev2/rev3 では各 *_closed の最大値で判定）。
    mwr = (mask_white_ratios or {}).get(last_attempt_no) or {}
    closed_ratios = [
        mwr.get(mt)
        for _name, (_raw, mt) in _MASK_NAME_TYPES.items()
        if mwr.get(mt) is not None
    ]
    if closed_ratios and max(closed_ratios) >= 0.80:
        msgs.append(f"マスク白画素率が異常 ({max(closed_ratios) * 100:.1f}%)")

    # 条件2: 最終検出（integrated_results）が 0 件
    if len(_final_results(debug_json)) == 0:
        msgs.append("最終検出 0 件")

    # 条件3: aspect_invalid のうち面積比の大きい候補（隣接結合/全面ブロブの目安）が一定数
    big_aspect_invalid = sum(
        1
        for c in _all_candidates(debug_json)
        if c.get("reject_reason") == "aspect_invalid"
        and (c.get("area_ratio") or 0) >= 0.10
    )
    if big_aspect_invalid >= 1:
        msgs.append(f"大面積の aspect_invalid 候補が {big_aspect_invalid} 件")

    # 条件4: 反転リトライが実施されたか
    or_inv = debug_json.get("or_inversion") or {}
    if or_inv.get("attempted"):
        before = or_inv.get("passed_before", 0)
        after = or_inv.get("passed_after", 0)
        improved = "／改善あり" if or_inv.get("improved") else "／改善なし"
        msgs.append(f"反転リトライ実行（{before} 件 → {after} 件{improved}）")

    return msgs


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


def _build_overlay_polygons(debug_json):
    """SVG polygon 用の points 文字列・重心・巨大候補フラグを事前計算したリスト。

    is_giant: results 内の area_ratio 中央値の 1.5 倍以上のものに True。
              ただし results が 1 件以下の場合は判定スキップ（全て False）。
    is_manual: 自動枠（integrated_results 由来）は False、手動枠（manual_results 由来）は True。

    自動枠の対象は最終検出 integrated_results（通常・反転の統合。BC 化される集合と一致）。
    手動枠（debug_json.manual_results）は giant 判定の母集団に混ぜず is_giant=False 固定で
    末尾に「足すだけ」（自動枠の points_str/centroid/is_giant/area_ratio は一切変えない）。
    """
    if not debug_json:
        return []
    keys = ("top_left", "top_right", "bottom_right", "bottom_left")
    image_size = debug_json.get("image_size") or {}
    image_area = image_size.get("area") or 0

    # 各 polygon の座標と shoelace area を一旦計算
    raw = []
    for r in _final_results(debug_json):
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
            "is_manual": False,
        })

    # 手動枠（debug_json.manual_results）を合流。giant 判定の母集団には入れず、
    # is_giant=False 固定・is_manual=True で末尾に足すだけ（自動枠の値・赤判定に非干渉）。
    for m in debug_json.get("manual_results") or []:
        polygon = m.get("polygon") or {}
        coords = [
            ((polygon.get(k) or {}).get("x", 0), (polygon.get(k) or {}).get("y", 0))
            for k in keys
        ]
        points_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        cx = sum(c[0] for c in coords) / 4.0
        cy = sum(c[1] for c in coords) / 4.0
        items.append({
            "card_index": None,
            "points_str": points_str,
            "centroid_x": cx,
            "centroid_y": cy,
            "is_giant": False,
            "area_ratio": 0.0,
            "is_manual": True,
        })
    return items


class RecalcDebugView(LoginRequiredMixin, View):
    """OpenCV デバッグキャッシュを再計算し、元画像詳細にリダイレクトする（POST 専用）。

    recalc_opencv_debug() で 3 ステップ（clear → detect → save）を実行する。
    OriginalImage.status / BusinessCard / raw_json は触らない。
    GET / その他メソッドは Django 標準の 405 応答（method_not_allowed）が返る。

    再検出は debug_json を丸ごと再生成・上書きするため、本番（settings.DEBUG=False）では
    塞ぐ。非 DEBUG では画面ボタンを隠すだけでなく URL 直叩き POST も 404 で弾く
    （表示制御は補助、本体はこの View 側の実行ガード）。
    """

    def post(self, request, pk):
        if not settings.DEBUG:
            raise Http404("recalc_debug is available only when DEBUG is enabled")
        user = request.user
        original = get_object_or_404(OriginalImage, pk=pk, user=user)
        recalc_opencv_debug(original)
        return redirect("originals:original_detail", pk=original.id)


# 手動切り出しで受け付ける回転角（時計回り度数）。
_MANUAL_CROP_ROTATIONS = (0, 90, 180, 270)


def _validate_manual_points(points):
    """[性質] 純関数 / 手動4点入力を検証し (ok, parsed, message) を返す。

    [入力] points: リクエスト由来の4点（[[x, y], ...] または [{"x":, "y":}, ...]）
    [出力] (ok: bool, parsed: list[tuple[float, float]] | None, message: str)
           ok=True のとき parsed は (x, y) 4 組。ok=False のとき message にエラー理由。
    """
    if not isinstance(points, (list, tuple)) or len(points) != 4:
        return False, None, "4点の座標が必要です。"
    parsed = []
    for p in points:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            x, y = p[0], p[1]
        elif isinstance(p, dict) and "x" in p and "y" in p:
            x, y = p["x"], p["y"]
        else:
            return False, None, "各点は [x, y] または {x, y} 形式で指定してください。"
        # bool は int のサブクラスなので明示的に弾く。
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) \
                or not isinstance(y, (int, float)):
            return False, None, "座標は数値で指定してください。"
        parsed.append((float(x), float(y)))
    return True, parsed, ""


def _prepare_manual_warp(original, points_payload):
    """[性質] 準関数（画像ファイル読み取りのみ・DB 書込なし）/ 手動4点を検証し warp 結果を返す。

    点検証 → 範囲チェック → 元画像読込 → warp_card_from_points までを共有する
    （保存・回転・BC 生成は行わない）。ManualCropView（確定保存）と ManualPreviewView
    （素 warp プレビュー）で同一の検証・warp を使うためのヘルパー。
    [入力] original: OriginalImage、points_payload: リクエスト由来の4点
    [出力] (warp_result, error_response)
      成功: ((warped_image: PIL.Image, polygon: dict), None)
      失敗: (None, JsonResponse(status=400))
    """
    ok, parsed_points, msg = _validate_manual_points(points_payload)
    if not ok:
        return None, JsonResponse({"error": msg}, status=400)

    # 座標の範囲チェック（image_size があれば上限も照合、無ければ 0 以上のみ）。
    image_size = (original.debug_json or {}).get("image_size") or {}
    max_w = image_size.get("width")
    max_h = image_size.get("height")
    for x, y in parsed_points:
        if x < 0 or y < 0:
            return None, JsonResponse({"error": "座標が負の値です。"}, status=400)
        if max_w is not None and x > max_w:
            return None, JsonResponse({"error": "座標が画像の幅を超えています。"}, status=400)
        if max_h is not None and y > max_h:
            return None, JsonResponse({"error": "座標が画像の高さを超えています。"}, status=400)

    # 元画像の生ピクセル取得。
    try:
        with Image.open(original.image_file.path) as opened:
            np_rgb = np.array(opened.convert("RGB"))
    except Exception as e:
        logger.warning(
            "manual_crop: 元画像の読み込みに失敗 OriginalImage %s: %s", original.id, e
        )
        return None, JsonResponse({"error": "元画像を読み込めませんでした。"}, status=400)

    # warp（公開ラッパー経由・None は小さすぎ等）。
    warp_result = warp_card_from_points(np_rgb, parsed_points)
    if warp_result is None:
        return None, JsonResponse(
            {"error": "指定範囲が小さすぎます（名刺として切り出せません）。"}, status=400
        )
    return warp_result, None


class ManualCropView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """元画像上で手動指定された4点から名刺を切り出し BusinessCard を作る（POST 専用・JSON）。

    自動検出（OpenCV）が取りこぼした名刺を、ユーザーが元画像上で4隅を指定して切り出す経路。
    入力（JSON body）:
        {"points": [[x, y], [x, y], [x, y], [x, y]], "rotation": 0|90|180|270}
        points  : 元画像ピクセル座標の4点（任意順）。[x, y] / {"x":, "y":} の両形式可。
        rotation: 切り出し後に適用する時計回り角度（apply_upright に渡す）。
    所有者スコープは既存 View 群を踏襲（request.user + user 付き get_object_or_404）。
    成功時 201 で BusinessCard の id / card_index / 詳細 URL を JSON で返す。
    入力不正・小さすぎ等は 400、作成失敗は 500（いずれも JSON エラー応答・例外を外に漏らさない）。
    GET / その他メソッドは Django 標準の 405 応答。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用）：手動切り出し＝BusinessCard 作成 → add_businesscard。
    permission_required = "cards.add_businesscard"

    def post(self, request, pk):
        user = request.user
        original = get_object_or_404(OriginalImage, pk=pk, user=user)

        # ── 入力パース＋バリデーション（400・500 で落とさない） ──
        try:
            payload = json.loads(request.body or b"{}")
        except (ValueError, TypeError):
            return JsonResponse({"error": "JSON ボディが不正です。"}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({"error": "JSON ボディが不正です。"}, status=400)

        rotation = payload.get("rotation", 0)
        if rotation not in _MANUAL_CROP_ROTATIONS:
            return JsonResponse(
                {"error": "回転角は 0 / 90 / 180 / 270 のいずれかにしてください。"}, status=400
            )

        # ── 点検証→範囲チェック→画像読込→warp（プレビューEPと共有）──
        warp_result, error = _prepare_manual_warp(original, payload.get("points"))
        if error is not None:
            return error
        warped_image, polygon = warp_result

        # ── ユーザー選択角で回転（段1 関数は回転しないのでここで適用）──
        final_image = apply_upright(warped_image, rotation)

        # ── d.〜g. 採番 → atomic でBC生成＋debug_json 追記（競合時1回リトライ） ──
        business_card = self._create_manual_card(original, final_image, polygon)
        if business_card is None:
            return JsonResponse({"error": "名刺の作成に失敗しました。"}, status=500)

        back = BackNavigator(request)
        card_detail_url = back.append_url(
            reverse("cards:card_detail", kwargs={"pk": business_card.id})
        )

        return JsonResponse(
            {
                "success": True,
                "business_card_id": str(business_card.id),
                "card_index": business_card.card_index,
                "card_detail_url": card_detail_url,
            },
            status=201,
        )

    def _next_card_index(self, original):
        """[性質] 準関数（DB 読み取り）/ 既存 BC の最大 card_index + 1（無ければ 0）。"""
        current_max = original.businesscard_set.aggregate(m=Max("card_index"))["m"]
        return 0 if current_max is None else current_max + 1

    def _create_manual_card(self, original, final_image, polygon):
        """[性質] 副作用あり（DB 書込・ファイル書込）/ 採番→BC生成→debug_json追記を atomic で行う。

        ① save_and_create_business_card で保存＋BC作成、② debug_json.manual_results 追記を
        同一 transaction.atomic 内で行う（②失敗時は BC をロールバックし、書き込み済み画像も
        _unlink_card_image で明示削除する）。card_index の UniqueConstraint 競合（段1 が
        IntegrityError を握って business_card=None を返す）時は採番し直して1回だけリトライする
        （select_for_update は使わない）。
        [出力] BusinessCard（成功）/ None（リトライ後も失敗・作成失敗）
        """
        for attempt in range(2):
            card_index = self._next_card_index(original)
            cleanup_rel = None
            collision = False
            try:
                with transaction.atomic():
                    result = save_and_create_business_card(
                        final_image, original, card_index, osd_json=None,
                    )
                    if result.business_card is None:
                        # 段1 が card_index 競合（IntegrityError）を握って None 返し
                        # （書き込み済み画像も段1側で削除済み）。初回のみ採番し直してリトライ。
                        if result.db_error and "IntegrityError" in result.db_error \
                                and attempt == 0:
                            collision = True
                        else:
                            logger.warning(
                                "manual_crop: BC作成に失敗 OriginalImage %s: crop_error=%s db_error=%s",
                                original.id, result.crop_error, result.db_error,
                            )
                            return None
                    else:
                        cleanup_rel = str(result.business_card.card_image) or None
                        self._append_manual_result(
                            original, result.business_card.id, polygon
                        )
            except Exception as e:
                # ② debug_json 追記等で失敗：atomic で BC はロールバック済み。
                # 画像ファイルは非トランザクションなので明示削除（迷子ファイルを残さない）。
                logger.warning(
                    "manual_crop: BC生成/debug_json追記で例外 OriginalImage %s: %s",
                    original.id, e,
                )
                if cleanup_rel:
                    _unlink_card_image(cleanup_rel)
                return None

            if collision:
                continue
            return result.business_card
        return None

    def _append_manual_result(self, original, business_card_id, polygon):
        """[性質] 副作用あり（DB 書込）/ debug_json トップレベル manual_results に1件追記する。

        既存キー（attempts / integrated_results 等）は触らず manual_results のみ部分更新する。
        debug_json が None でも manual_results だけ持つ dict を作る。
        """
        dj = dict(original.debug_json or {})
        manual = list(dj.get("manual_results") or [])
        manual.append({"business_card_id": str(business_card_id), "polygon": polygon})
        dj["manual_results"] = manual
        original.debug_json = dj
        original.save(update_fields=["debug_json", "updated_at"])


class ManualPreviewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """手動指定された4点を素 warp して切り出し画像（base64 PNG）を返す（POST 専用・JSON）。

    段2-c2-② の回転モーダル用プレビュー。保存・BC 生成・debug_json 追記・回転は一切行わず、
    rotation=0 の素 warp 画像のみ返す（回転はブラウザ側で見せ、確定時に ManualCropView が
    apply_upright で適用する＝プレビューと保存結果の向きが一致する）。
    入力（JSON body）: {"points": [[x, y]×4]}（[x,y] / {"x":,"y":} 両形式可）。
    成功: 200 + {"success": true, "image": "data:image/png;base64,<...>"}。
    点不足・範囲外・小さすぎ warp は ManualCropView と同じ作法で 400（500 で落とさない）。
    所有者スコープは既存 View 群を踏襲。GET / その他メソッドは Django 標準の 405。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用）：手動切り出しプレビュー（同フロー）→ add_businesscard。
    permission_required = "cards.add_businesscard"

    def post(self, request, pk):
        user = request.user
        original = get_object_or_404(OriginalImage, pk=pk, user=user)

        try:
            payload = json.loads(request.body or b"{}")
        except (ValueError, TypeError):
            return JsonResponse({"error": "JSON ボディが不正です。"}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({"error": "JSON ボディが不正です。"}, status=400)

        # 点検証→範囲チェック→画像読込→warp（ManualCropView と共有・回転や保存はしない）。
        warp_result, error = _prepare_manual_warp(original, payload.get("points"))
        if error is not None:
            return error
        warped_image, _polygon = warp_result

        # 素 warp 画像（rotation=0）を PNG→base64 data URL にして返す。
        try:
            buf = BytesIO()
            rgb = warped_image if warped_image.mode == "RGB" else warped_image.convert("RGB")
            rgb.save(buf, format="PNG")
            data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as e:
            logger.warning(
                "manual_crop preview: 画像エンコードに失敗 OriginalImage %s: %s", original.id, e
            )
            return JsonResponse({"error": "プレビュー画像の生成に失敗しました。"}, status=400)

        return JsonResponse({"success": True, "image": data_url}, status=200)


class CardDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """BusinessCard を削除し、元画像詳細にリダイレクトする（POST 専用）。

    削除対象は本人所有の BC のみ（user スコープで絞り込み）。
    Contact / ContactFieldConfidence は CASCADE で自動削除、card_image の FS 実体は
    post_delete シグナルで自動削除される。OriginalImage.raw_json は温存される。
    GET / その他メソッドは Django 標準の 405 応答（method_not_allowed）が返る。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用、rev20 No.22 ★2）：BusinessCard 削除 → delete_businesscard。
    permission_required = "cards.delete_businesscard"

    def post(self, request, pk):
        user = request.user
        bc = get_object_or_404(BusinessCard, pk=pk, original_image__user=user)
        original_image_id = bc.original_image_id
        bc.delete()
        return redirect("originals:original_detail", pk=original_image_id)


# 名刺一覧の多段ソート（persons の ?sort=key,-key 機構を流用。列は名刺一覧の実カラムに限定）。
# 「画面に出ている列＝ソートできる列」を一致させる：氏名 / 会社 / 役職 / 作成日時。
# 氏名は読み（phonetic_name＝カタカナ）の五十音順で並べる（persons と同方針）。
CARD_LIST_SORT_FIELD_MAP = {
    "name": "contact__phonetic_name",
    "company": "contact__organization",
    "title": "contact__title",
    "created": "created_at",
}
CARD_LIST_SORT_MAX_KEYS = 3


def _parse_card_sort(params):
    """単一 sort パラメータ（例 "company,-title,created"）を [(key, direction), ...] に解決する。

    [性質] 純関数（DB 操作なし・副作用なし）。許可リスト外キー・空・二重指定は無視。
    """
    raw = params.get("sort") or ""
    tokens = []
    seen = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("-"):
            direction, key = "desc", chunk[1:]
        else:
            direction, key = "asc", chunk
        if key not in CARD_LIST_SORT_FIELD_MAP or key in seen:
            continue
        seen.add(key)
        tokens.append((key, direction))
        if len(tokens) >= CARD_LIST_SORT_MAX_KEYS:
            break
    return tokens


def _apply_card_list_sort(qs, params):
    """BusinessCard QuerySet に多段ソートを適用する（純関数）。指定が無ければ qs をそのまま返す。"""
    tokens = _parse_card_sort(params)
    if not tokens:
        return qs
    order = []
    for key, direction in tokens:
        prefix = "-" if direction == "desc" else ""
        order.append(prefix + CARD_LIST_SORT_FIELD_MAP[key])
    order.append("pk")
    return qs.order_by(*order)


def _card_sort_context(params):
    """ソートUI（折りたたみ）の描画用 context（純関数）。sort_rows / sort_is_active / sort_value。"""
    tokens = _parse_card_sort(params)
    rows = [{"key": k, "dir": d} for (k, d) in tokens]
    while len(rows) < CARD_LIST_SORT_MAX_KEYS:
        rows.append({"key": "", "dir": "asc"})
    return {
        "sort_rows": rows,
        "sort_is_active": bool(tokens),
        "sort_value": ",".join(("-" + k if d == "desc" else k) for k, d in tokens),
    }


class CardListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """名刺一覧画面（仕様書 v1.2.2 / Phase 4）。

    BusinessCard を Contact 情報とともに一覧表示する。
    7フィールドの AND 検索（name / organization / department / title / email / tel / address）。
    tel は personal_phone / mobile_phone / personal_fax の OR 一致。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用、rev20 No.3 ★2）：BusinessCard 閲覧 → view_businesscard。
    permission_required = "cards.view_businesscard"
    model = BusinessCard
    template_name = "cards/card_list.html"
    context_object_name = "cards"
    paginate_by = 50
    _PER_PAGE_CHOICES = (50, 100, 200)

    _SEARCH_PARAMS = ("name", "organization", "department", "title", "email", "tel", "address")

    def _resolve_per_page(self):
        """[性質] 純関数 / per_page を 50/100/200 に解決する。無指定・不正値は 50。"""
        try:
            n = int(self.request.GET.get("per_page", 50))
        except (TypeError, ValueError):
            return 50
        return n if n in self._PER_PAGE_CHOICES else 50

    def get_paginate_by(self, queryset):
        return self._resolve_per_page()

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
        """有効なフィルタ値のリストを返す。0 件なら空（フィルタなし＝全件）。

        [性質] 純関数（DB操作なし、request.GET の読み取りのみ）

        request.GET は通常 QueryDict だが、A 側テストが dict を直接渡すケースに
        備えて getattr で getlist の有無を確認する defensive 実装（保険）。
        本筋は呼び出し側が QueryDict を渡すこと。

        v1.7 UI 改善：ステータス未選択＝全件表示に統一したため、空のときは
        business_card デフォルトに寄せず空リストを返す（_build_filter_q が Q() を返し全件）。
        """
        valid = {v for v, _ in self._OCR_FILTER_CHOICES}
        getlist = getattr(self.request.GET, "getlist", None)
        if callable(getlist):
            raw = getlist("ocr_result")
        else:
            value = self.request.GET.get("ocr_result")
            raw = [value] if value else []
        return [v for v in raw if v in valid]

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

        def _clean_q(val):
            return (val or "").replace("　", "").replace(" ", "").strip()

        name_q = _clean_q(p.get("name"))
        if name_q:
            qs = qs.annotate(
                _clean_full_name=Replace(
                    Replace("contact__full_name", Value("　"), Value("")),
                    Value(" "),
                    Value(""),
                )
            ).filter(_clean_full_name__icontains=name_q)

        org_q = _clean_q(p.get("organization"))
        if org_q:
            qs = qs.annotate(
                _clean_organization=Replace(
                    Replace("contact__organization", Value("　"), Value("")),
                    Value(" "),
                    Value(""),
                )
            ).filter(_clean_organization__icontains=org_q)

        dept_q = _clean_q(p.get("department"))
        if dept_q:
            qs = qs.annotate(
                _clean_department=Replace(
                    Replace("contact__department", Value("　"), Value("")),
                    Value(" "),
                    Value(""),
                )
            ).filter(_clean_department__icontains=dept_q)

        title_q = _clean_q(p.get("title"))
        if title_q:
            qs = qs.annotate(
                _clean_title=Replace(
                    Replace("contact__title", Value("　"), Value("")),
                    Value(" "),
                    Value(""),
                )
            ).filter(_clean_title__icontains=title_q)

        if p.get("email", "").strip():
            qs = qs.filter(contact__email__icontains=p["email"].strip())

        if p.get("tel", "").strip():
            tel = p["tel"].strip()
            qs = qs.filter(
                Q(contact__personal_phone__icontains=tel)
                | Q(contact__mobile_phone__icontains=tel)
                | Q(contact__personal_fax__icontains=tel)
            )

        addr_q = _clean_q(p.get("address"))
        if addr_q:
            qs = qs.annotate(
                _clean_address=Replace(
                    Replace("contact__address", Value("　"), Value("")),
                    Value(" "),
                    Value(""),
                )
            ).filter(_clean_address__icontains=addr_q)

        # 取り込み期間フィルタ：元画像（OriginalImage）のアップロード日時 = created_at で範囲絞り。
        # 日付入力（YYYY-MM-DD）の日単位で両端含む。片側のみ・両方空（フィルタなし）も許容。
        uploaded_after = parse_date((p.get("uploaded_after") or "").strip())
        if uploaded_after:
            qs = qs.filter(original_image__created_at__date__gte=uploaded_after)
        uploaded_before = parse_date((p.get("uploaded_before") or "").strip())
        if uploaded_before:
            qs = qs.filter(original_image__created_at__date__lte=uploaded_before)

        # 既定は作成日時の降順。?sort=key,-key,... 指定があれば多段ソートで差し替える。
        qs = qs.order_by("-created_at")
        return _apply_card_list_sort(qs, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        back = BackNavigator(self.request)
        back.push_current(
            "名刺一覧",
            ["name", "organization", "department", "title", "email", "tel", "address",
             "uploaded_after", "uploaded_before", "ocr_result", "sort", "per_page", "page"],
        )
        context["back"] = back

        context["active_app"] = "cards"
        context["active_menu"] = "cards:card_list"
        for key in self._SEARCH_PARAMS:
            context[key] = self.request.GET.get(key, "")
        context["ocr_result_choices"] = self._OCR_FILTER_CHOICES
        context["selected_ocr_results"] = self._selected_ocr_filters()
        # ソート（折りたたみ）と表示件数セレクタ用 context。
        context.update(_card_sort_context(self.request.GET))
        context["per_page"] = self._resolve_per_page()
        context["per_page_choices"] = self._PER_PAGE_CHOICES
        # 取り込み期間（日付入力の値差し戻し）。
        context["uploaded_after"] = self.request.GET.get("uploaded_after", "")
        context["uploaded_before"] = self.request.GET.get("uploaded_before", "")
        return context


class CardDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """名刺詳細画面（仕様書 v1.4.2 / Phase 4）。

    OpenCV デバッグ情報の閲覧と Contact フィールド編集（ContactDetailView と同じ
    AJAX 編集 UI）を併せ持つ業務画面。Contact 編集パーツは _contact_field.html
    を再利用するため、ContactDetailView と同じ context（field_confidences /
    is_editable）を提供する。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用、rev20 No.4 ★2）：BusinessCard 閲覧 → view_businesscard。
    permission_required = "cards.view_businesscard"
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

        # v1.7: 「新規コンタクト作成」ボタンは OCR 判定がまだ下りていない（ocr_result is None＝OCR待ち）
        # BC でのみ表示する。判定はサーバ側で行い、None と空文字を厳密に区別する（モデル default=None 基準）。
        # Contact 紐付け済みのガードはテンプレ側 {% if contact %} 分岐が従来どおり担う。
        context["can_create_contact"] = self.object.ocr_result is None

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

        # OSD（Tesseract 由来・OCR前）デバッグ表示用。第2段OSD移植で OSD は
        # BusinessCard.osd_json（{"schema_version","osd":{...},"tesseract_ocr":{...}}）に格納される
        # （移植元は OriginalImage.raw_json 起点だったが現行 data model へ読み替え）。
        # rotate=0 かつ低 conf は「倒れているのに回さない誤判定」の疑いなので目視で拾えるよう渡す。
        # Claude の card_meta.orientation（BC.orientation バッジ）とは別系統。
        osd = None
        osd_json = self.object.osd_json
        if isinstance(osd_json, dict) and isinstance(osd_json.get("osd"), dict):
            osd = osd_json["osd"]
        context["osd"] = osd
        context["osd_rotate_zero"] = bool(osd) and osd.get("rotate") == 0

        return context


class CardEditView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """名刺（BusinessCard）編集画面（v1.7、代替OCR運用の手動終端化）。

    対象は **Contact 未生成の BC のみ**（Contact を持つ BC は編集不可。詳細は下記ガード）。
    GET：orientation / error_message の編集フォームを表示。
    POST：
      - 通常保存：orientation / error_message を更新（ocr_status は変えない）。
      - 「名刺でない」設定（POST に mark_not_business_card）：当該 BC を
        ocr_result=not_business_card / ocr_status=done / claimed_at=None に終端化し
        （error_message は入力値を反映）、既存の _update_original_image_status で元画像 status を
        再集計する。手動 Contact 作成経路（_confirm_business_card_as_done）と同じ集計に合流し、
        新しい集計ロジックは書かない。Contact は作らない（not_business_card は Contact を持たない）。
        BC 更新と status 再集計は同一トランザクションで行う。
      - 「その他」設定（POST に mark_others）：既存4分類のどれにも当てはまらない BC を
        ocr_result=others に設定する以外は「名刺でない」設定と同一経路（_finalize_bc）。
        理由は error_message（メモ）欄に手入力する運用。Contact は作らない。

    ガード（業務ルール）：Contact を既に持つ BC は編集不可。reverse OneToOne の
    `getattr(bc, "contact", None)` で判定し、ある場合は messages.error + 名刺詳細へ redirect
    （テンプレ側の「編集」ボタン非表示と二重構え）。所有者スコープ（original_image__user）も既存流儀に揃える。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用）：BusinessCard 編集 → change_businesscard。
    permission_required = "cards.change_businesscard"

    template_name = "cards/card_edit.html"

    def _get_editable_bc(self, request, pk):
        """編集可能な BC を取得する。Contact 済みなら (None, redirect) を返す。"""
        user = request.user
        bc = get_object_or_404(
            BusinessCard.objects.select_related("original_image", "contact"),
            pk=pk,
            original_image__user=user,
        )
        if getattr(bc, "contact", None) is not None:
            messages.error(
                request, "この名刺は既にコンタクトが紐づいているため編集できません。"
            )
            return None, redirect("cards:card_detail", pk=bc.pk)
        return bc, None

    def get(self, request, pk):
        bc, err = self._get_editable_bc(request, pk)
        if err is not None:
            return err
        form = BusinessCardEditForm(instance=bc)
        return render(request, self.template_name, self._context(request, bc, form))

    def post(self, request, pk):
        bc, err = self._get_editable_bc(request, pk)
        if err is not None:
            return err

        form = BusinessCardEditForm(request.POST, instance=bc)
        if not form.is_valid():
            return render(
                request, self.template_name, self._context(request, bc, form)
            )

        if "mark_not_business_card" in request.POST:
            finalized = self._finalize_bc(form, BusinessCard.OcrResult.NOT_BUSINESS_CARD)
        elif "mark_others" in request.POST:
            finalized = self._finalize_bc(form, BusinessCard.OcrResult.OTHERS)
        else:
            # 通常保存：orientation / error_message のみ更新（ocr_status は変えない）。
            form.save()
            finalized = None

        # 再終端化ガード（P1）：既に終端化済み（ocr_result 非 None）の BC を別ワーカーが
        # 同時終端化した場合、_finalize_bc が False を返す。後勝ち上書きを起こさず弾く。
        if finalized is False:
            messages.error(request, "この名刺は既に処理済みのため、再設定できません。")
            return redirect("cards:card_detail", pk=bc.pk)

        back = BackNavigator(request)
        target_url = reverse("cards:card_detail", kwargs={"pk": bc.pk})
        return HttpResponseRedirect(back.append_url(target_url))

    def _finalize_bc(self, form, ocr_result):
        """[性質] 副作用あり（DB 書込）/ BC を ocr_result で終端化し元画像 status を再集計する。

        「名刺でない」「その他」共通の終端化処理。ocr_status=done / claimed_at=None で OCR 待ち
        行列（process_ocr が ocr_status=pending で選別）から確実に外し、orientation / error_message
        などフォーム入力も同時保存する。BC 更新と status 再集計は同一トランザクション。
        Contact は作らない（既存集計 _update_original_image_status に合流）。

        再終端化ガード（P1・並列運用対策）：対象 BC を select_for_update で行ロックし、ocr_result が
        まだ None（OCR待ち）であることを最新の committed 状態で再確認してからのみ更新する。既に
        終端化済み（ocr_result が非 None）なら更新せず False を返し、後勝ちの分類/メモ上書きを防ぐ。
        None 判定はモデル default=None 基準で厳密に行う（空文字とは区別）。

        [入力] form: BusinessCardEditForm（is_valid 済み）, ocr_result: BusinessCard.OcrResult
        [出力] bool（True=終端化を実行 / False=既に終端化済みのため弾いた）
        """
        with transaction.atomic():
            card = form.save(commit=False)
            # 行ロック後に最新状態で再チェック（チェック〜更新の割り込みを防ぐ）。
            locked = BusinessCard.objects.select_for_update().get(pk=card.pk)
            if locked.ocr_result is not None:
                return False
            card.ocr_result = ocr_result
            card.ocr_status = BusinessCard.OcrStatus.DONE
            card.claimed_at = None
            card.save()
            _update_original_image_status(card.original_image_id)
        return True

    def _context(self, request, bc, form):
        return {
            "card": bc,
            "form": form,
            "back": BackNavigator(request),
            "active_app": "cards",
            "active_menu": "cards:card_list",
            # 終端化（名刺ではない / その他）ボタンは OCR待ち（ocr_result is None）の BC でのみ表示。
            # 詳細画面の can_create_contact と同条件。POST 側 _finalize_bc の行ロック再チェックと二重構え。
            "can_finalize": bc.ocr_result is None,
        }


class CardRotateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """名刺画像の 90° 回転焼き直し（v1.7）。

    対象は **Contact 未生成の BC のみ**（CardEditView と同じ業務ガード）。POST 専用で、
    direction（"cw"=時計回り90° / "ccw"=反時計回り90°）に従い card_image を実際に回して
    同一パスへ上書きし、orientation を normal にリセットする（_rotate_and_overwrite_card_image）。
    処理後は名刺編集画面へ redirect（back チェーンを保持）。表示側のキャッシュバスティングは
    BusinessCard.get_card_image_url_busted（updated_at ベース）で担保する。

    ガード：Contact 済み BC はサーバ側で弾く（テンプレ側の回転ボタン非表示と二重構え）。
    所有者スコープ（original_image__user）も既存流儀に揃える。
    """

    # 認可（Phase 7 段3-2 を v1.7 へ適用）：回転対象は BusinessCard.card_image（orientation も
    # BusinessCard を更新）→ change_businesscard。
    permission_required = "cards.change_businesscard"

    def post(self, request, pk):
        user = request.user
        bc = get_object_or_404(
            BusinessCard.objects.select_related("original_image", "contact"),
            pk=pk,
            original_image__user=user,
        )
        if getattr(bc, "contact", None) is not None:
            messages.error(
                request, "この名刺は既にコンタクトが紐づいているため回転できません。"
            )
            return redirect("cards:card_detail", pk=bc.pk)
        if not bc.card_image:
            messages.error(request, "画像がないため回転できません。")
            return redirect("cards:card_edit", pk=bc.pk)

        direction = request.POST.get("direction")
        if direction not in ("cw", "ccw"):
            messages.error(request, "回転方向が不正です。")
            return redirect("cards:card_edit", pk=bc.pk)

        try:
            _rotate_and_overwrite_card_image(bc, direction)
        except Exception:
            logger.exception("card_image rotate failed: pk=%s direction=%s", pk, direction)
            messages.error(request, "画像の回転に失敗しました。")
            return redirect("cards:card_edit", pk=bc.pk)

        messages.success(request, "名刺画像を回転しました。")
        back = BackNavigator(request)
        target_url = reverse("cards:card_edit", kwargs={"pk": bc.pk})
        return HttpResponseRedirect(back.append_url(target_url))
