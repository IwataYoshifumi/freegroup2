"""IP アドレスマスキング（仕様書 v1.6 §8.3.1）。

ClickLog 作成時にリクエスト IP をプライバシー保護観点で粗くマスクして保存する：
  - IPv4 → /24（末尾オクテットを 0）
  - IPv6 → /64（下位 64 bit を 0）

§2.2.2 純関数／副作用関数の分離原則に従い、本モジュールは純関数のみを置く
（DB 操作・I/O なし、同じ入力に対し常に同じ出力）。マスク後の値の永続化は
副作用関数側（TrackingRedirectView の ClickLog 作成パス）が担う。
"""

from __future__ import annotations

import ipaddress
from typing import Optional


def mask_ip(raw_ip: Optional[str]) -> Optional[str]:
    """生 IP 文字列を /24 (IPv4) または /64 (IPv6) でマスクして返す（§8.3.1）。

    [性質] 純関数（DB 操作なし・I/O なし・例外送出なし）
    [入力] raw_ip: str or None（HttpRequest.META["REMOTE_ADDR"] 由来など）
    [出力] str（マスク後の IP 文字列）または None（None・空・不正値・未知種別の場合）

    パース失敗時は None を返す（ClickLog.ip_masked は null=True、§4.6）。
    例外を投げないことでビュー側が try/except せずに済む（呼び出し側コードの簡潔化）。
    """
    if not raw_ip:
        return None
    try:
        addr = ipaddress.ip_address(raw_ip)
    except (ValueError, TypeError):
        return None

    if isinstance(addr, ipaddress.IPv4Address):
        # 末尾オクテットを 0 にする = /24 ネットワークアドレス
        network = ipaddress.IPv4Network(f"{addr}/24", strict=False)
        return str(network.network_address)

    if isinstance(addr, ipaddress.IPv6Address):
        # 下位 64 bit を 0 にする = /64 ネットワークアドレス
        network = ipaddress.IPv6Network(f"{addr}/64", strict=False)
        return str(network.network_address)

    return None
