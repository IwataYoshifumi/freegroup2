"""名刺向き正位化（feature/orientation-onnx）。

ORIENTATION_BACKEND コンセント（抽象インターフェース＋実装3種：PP-LCNet / OSD / none）で
切り抜き画像の向きを判定し、process_opencv パイプラインで正位化する。

既存 BusinessCard.orientation（5値・OCR判定・mirror含む）とは別概念・別フィールド。
判定記録は BusinessCard.orientation_correction / orientation_score（バックエンド共通）へ書く。
"""
