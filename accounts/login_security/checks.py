"""ログインセキュリティ設定の Django システムチェック（仕様書 v1.3.1 §5.7）。"""

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security)
def check_client_ip_source(app_configs, **kwargs):
    """CLIENT_IP_SOURCE の設定値と本番設定の不整合を警告する。

    [性質] 純関数（settings を参照、副作用なし）
    [入力] app_configs, **kwargs（Django checks フレームワーク規約）
    [出力] list[CheckMessage]
    """
    source = getattr(settings, "CLIENT_IP_SOURCE", "x_real_ip")
    if source not in ("x_real_ip", "remote_addr"):
        return [
            Warning(
                f"CLIENT_IP_SOURCE に不正な値 {source!r} が設定されています。",
                hint="'x_real_ip' または 'remote_addr' を設定してください。",
                id="accounts.W002",
            )
        ]

    if not getattr(settings, "DEBUG", False) and source == "remote_addr":
        return [
            Warning(
                "DEBUG=False の環境で CLIENT_IP_SOURCE='remote_addr' が設定されています。",
                hint=(
                    "Docker/nginx 構成の本番環境では接続元が nginx の IP になり、"
                    "1人の失敗で全員が IP 遮断されます。CLIENT_IP_SOURCE='x_real_ip' を設定してください。"
                ),
                id="accounts.W001",
            )
        ]
    return []
