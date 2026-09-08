# -*- coding: utf-8 -*-
"""汎用メールドメイン判定モジュール（後方互換用エイリアス）。

正本は contacts.services.normalization です。
本モジュールは後方互換性維持のために normalization モジュールから参照を再エクスポートします。
"""

from contacts.services.normalization import (
    GENERIC_EMAIL_DOMAINS,
    GENERIC_EMAIL_DOMAIN_SUFFIXES,
    is_generic_email_domain,
)

__all__ = [
    "GENERIC_EMAIL_DOMAINS",
    "GENERIC_EMAIL_DOMAIN_SUFFIXES",
    "is_generic_email_domain",
]
