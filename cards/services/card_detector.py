"""名刺検出インターフェース。

settings.CARD_DETECTOR_BACKEND で実装を切り替える。
pipeline_coordinator はこのファイルの detect_cards だけを呼ぶ。

戻り値型 CardDetectionResult は実装に関わらず常に固定。
"""

from __future__ import annotations

from typing import TypedDict

from PIL import Image


class PolygonPoint(TypedDict):
    x: float
    y: float


class Polygon(TypedDict):
    top_left: PolygonPoint
    top_right: PolygonPoint
    bottom_right: PolygonPoint
    bottom_left: PolygonPoint


class CardDetectionResult(TypedDict):
    polygon: Polygon
    warped_image: Image.Image  # 透視変換済み画像（向き補正なし）


def detect_cards(image_path: str) -> list[CardDetectionResult]:
    """画像から名刺を検出し、透視変換済み画像（向き補正なし）と4隅座標を返す。

    [性質] 副作用あり（バックエンドに委譲）
    [入力] image_path: 元画像のファイルパス
    [出力] CardDetectionResult のリスト。検出失敗時は空リスト。
    [切替] settings.CARD_DETECTOR_BACKEND で実装を選択する。
           "opencv" → cards.services.detectors.opencv_detector（既定）
           "ai"     → cards.services.detectors.ai_detector（将来用）
    """
    from django.conf import settings

    backend = getattr(settings, "CARD_DETECTOR_BACKEND", "opencv")

    if backend == "opencv":
        from cards.services.detectors.opencv_detector import detect_cards as _impl
    elif backend == "ai":
        from cards.services.detectors.ai_detector import detect_cards as _impl
    else:
        raise ValueError(f"Unknown CARD_DETECTOR_BACKEND: {backend!r}")

    return _impl(image_path)
