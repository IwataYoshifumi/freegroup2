"""クリック中継ビューのボット・プリフェッチ判定（仕様書 v1.6 §8.2）。

5 段階判定を純関数として副作用と分離する（§2.2.2）。本モジュールは DB を一切触らず、
判定に必要な入力をすべて引数で受け取る。TrackingRedirectView（副作用関数側）が必要な
情報を集めて本関数を呼び、戻り値 (is_valid_click, bot_reason) を元に
ClickLog 作成と TrackingLink 更新を行う。

この分離により、将来 Celery 等での非同期化（仕様書 §2.2.2 想定）に向けて、
ボット判定ロジックの差し替え・テストが view 経路と独立して行える。
"""

from __future__ import annotations

import datetime
from typing import Iterable, Tuple


def detect_bot_status(
    *,
    http_method: str,
    user_agent: str,
    sent_at: datetime.datetime,
    now: datetime.datetime,
    threshold_seconds: int,
    bot_patterns: Iterable[str],
    has_existing_valid_click: bool,
) -> Tuple[bool, str]:
    """5 段階のボット判定を行い (is_valid_click, bot_reason) を返す（§8.2）。

    [性質] 純関数（DB 操作なし・I/O なし・副作用なし、引数だけで決定論的）
    [入力]
      http_method: リクエストの HTTP メソッド（"GET" / "HEAD" 等、大文字想定）
      user_agent: User-Agent 文字列（空文字 OK）
      sent_at: 配信時刻の近似（TrackingLink.created_at = PRODUCTION 配信の (III) atomic
               内で確定する時刻、§7.4.3.3 / §8.4 で sent_at と近似される）
      now: 判定時点の現在時刻（呼び出し側が timezone.now() を渡す）
      threshold_seconds: too_fast 判定の閾値秒数（CLICK_PREFETCH_THRESHOLD_SECONDS）
      bot_patterns: User-Agent 部分一致パターン列（KNOWN_BOT_USER_AGENT_PATTERNS）
      has_existing_valid_click: 同 TrackingLink で既に有効クリック済みか
                                （仕様書 §8.2 順4 はサマリベース＝TrackingLink.click_count > 0
                                 と履歴ベースを許容、本実装はサマリベースを採用）
    [出力] (is_valid_click: bool, bot_reason: str)
      bot_reason は ClickLog.BotReason の値（"non_get" / "known_bot" / "too_fast" /
      "duplicate" / ""）。is_valid_click=True のとき bot_reason は "" 空文字。

    判定順序（§8.2 通り）：
      1. GET 以外（HEAD 等） → ("non_get", False)
      2. UA がボットパターンに部分一致 → ("known_bot", False)
      3. sent_at から threshold_seconds 以内 → ("too_fast", False)
      4. 既に有効クリック済み → ("duplicate", False)
      5. 上記すべてパス → ("", True)

    本関数は test_send（別表 C.23 の 5 値目）を返さない。
    test_send は Phase 4 のクリック中継ビュー対象外（テスト配信モードでは
    TrackingLink 自体が作成されず /t/ を踏まないため、§7.4.6.4 / §8.4）。
    """
    # 順 1：GET 以外（HEAD 等）。HEAD はメーラープリフェッチで多く来るが、
    # 302 リダイレクトは別途返してプリフェッチを正常終了させる（ビュー側責務）。
    if http_method.upper() != "GET":
        return False, "non_get"

    # 順 2：既知のボット User-Agent。部分一致・大文字小文字区別なし（§8.2.1）。
    ua_lower = (user_agent or "").lower()
    if ua_lower:
        for pattern in bot_patterns:
            if pattern.lower() in ua_lower:
                return False, "known_bot"

    # 順 3：プリフェッチ判定（配信から threshold_seconds 以内のクリック）。
    # sent_at が未来日時のケースは現実には起きないが、念のため負値も含めて閾値未満で判定。
    delta_seconds = (now - sent_at).total_seconds()
    if delta_seconds < threshold_seconds:
        return False, "too_fast"

    # 順 4：重複クリック（同一トークンで既に有効クリック済み）。
    # サマリベース：TrackingLink.click_count > 0 を呼び出し側が解決して渡す。
    if has_existing_valid_click:
        return False, "duplicate"

    # 順 5：すべてパス → 有効クリック
    return True, ""
