"""OCR バックエンド共通の例外（(2) FileOcrBackend 用、循環 import 回避のため独立モジュール）。

ocr_pipeline（catch 側）と file_ocr_backend（raise 側）の双方から import される。
本モジュールは cards.tasks 内の他モジュールを import しない（依存方向を一方向に保つ）。
"""


class OcrSourceNotReady(Exception):
    """対象 BC の OCR ソース（ワー君JSON）がまだ存在しない。

    エラーではなく「未着」を表す合図。呼び出し側（process_cardimage_with_ocr）は
    これを catch して BC を pending に戻す（error_message を汚さず、ログのみ）。
    リトライ上限は設けない（「まだ出ていない」が常態のため）。
    """


class FileOcrSchemaError(Exception):
    """OCR ソース JSON が正本スキーマ（v1.6.1）に違反している。

    取り込まず ocr_failed 終端にする合図。どのフィールドが何に違反したかを
    メッセージに含める（呼び出し側が error_message に記録する）。
    OcrSourceNotReady とは別型にし、catch 側で「未着」と「違反」を混同させない。
    """
