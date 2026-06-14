"""向き判定バックエンドのファクトリ（settings.ORIENTATION_BACKEND で切替）。

ocr_factory.get_ocr_backend と同じパターン：設定値で分岐し実装を遅延 import して生成。
未知値は ValueError。
"""

from django.conf import settings

# .env 既定（settings 側でも同じ既定を持つ）。
BACKEND_PPLCNET = "PP-LCNet"
BACKEND_OSD = "OSD"
BACKEND_NONE = "none"
DEFAULT_BACKEND = BACKEND_PPLCNET


def get_orientation_backend():
    """[性質] 副作用あり（バックエンド実装を遅延 import して生成）。

    [入力] なし（settings.ORIENTATION_BACKEND を参照。既定 "PP-LCNet"）
    [出力] detect(image)->OrientationResult を持つバックエンドインスタンス
    [例外] ValueError（未知の ORIENTATION_BACKEND 値）
    """
    name = getattr(settings, "ORIENTATION_BACKEND", DEFAULT_BACKEND)
    if name == BACKEND_PPLCNET:
        from cards.orientation.backends import PPLCNetBackend

        return PPLCNetBackend()
    if name == BACKEND_OSD:
        from cards.orientation.backends import OsdBackend

        return OsdBackend()
    if name == BACKEND_NONE:
        from cards.orientation.backends import NoneBackend

        return NoneBackend()
    raise ValueError(f"Unknown ORIENTATION_BACKEND: {name!r}")
