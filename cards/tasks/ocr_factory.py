"""OCR バックエンドのファクトリ（settings.OCR_BACKEND で切替）。

card_detector.py（CARD_DETECTOR_BACKEND）と同じパターン：設定値で分岐し、実装を
遅延 import して生成する。未知値は ValueError。

OCR_BACKEND の値（第2の設定キーは追加しない）：
  "claude_sonnet_4_6" … Claude API（OcrService）。値の "_" を "-" に変換してモデル名へ
  "worker_cowork"     … ワー君JSON（FileOcrBackend）
"""

from django.conf import settings


def get_ocr_backend():
    """[性質] 副作用あり（バックエンド実装を遅延 import して生成）。

    [入力] なし（settings.OCR_BACKEND を参照。既定 "claude_sonnet_4_6"）
    [出力] run_ocr(image, business_card) を持つバックエンドインスタンス
    [例外] ValueError（未知の OCR_BACKEND 値）

    既存 API 経路の不変性：未設定 / "claude_sonnet_4_6" のとき OcrService(model=
    "claude-sonnet-4-6") を返す（OcrService() の既定モデルと同一）。
    """
    backend = getattr(settings, "OCR_BACKEND", "claude_sonnet_4_6")

    if backend == "worker_cowork":
        from cards.tasks.file_ocr_backend import FileOcrBackend

        return FileOcrBackend()
    elif backend == "claude_sonnet_4_6":
        from cards.tasks.ocr_service import OcrService

        # アンダースコア区切りの設定値をモデル名（ハイフン区切り）へ変換。
        return OcrService(model=backend.replace("_", "-"))
    else:
        raise ValueError(f"Unknown OCR_BACKEND: {backend!r}")
