"""接続元 IP アドレスの取得（仕様書 v1.3.1 §5.7 / §14 #5）。"""

import ipaddress

from django.conf import settings


def get_client_ip(request) -> str | None:
    """リクエストから接続元 IP アドレスを 1 か所の規則で取得する。

    [性質] 純関数（settings と request.META を参照、副作用なし）
    [入力] request: HttpRequest
    [出力] str | None（妥当な IPv4/IPv6 文字列、取得不可または不正形式なら None）

    CLIENT_IP_SOURCE == "x_real_ip" のときは nginx が付与する X-Real-IP のみを読む。
    CLIENT_IP_SOURCE == "remote_addr" のときは REMOTE_ADDR のみを読む。
    X-Forwarded-For や CF-Connecting-IP 等のクライアント転送ヘッダは一切読まない。
    """
    source = getattr(settings, "CLIENT_IP_SOURCE", "x_real_ip")
    if source == "x_real_ip":
        raw = request.META.get("HTTP_X_REAL_IP")
    elif source == "remote_addr":
        raw = request.META.get("REMOTE_ADDR")
    else:
        return None

    if not raw or not isinstance(raw, str):
        return None

    candidate = raw.strip()
    if not candidate:
        return None

    try:
        ip_obj = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return str(ip_obj)
