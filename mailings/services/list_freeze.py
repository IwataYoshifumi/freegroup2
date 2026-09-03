"""MailingConfig ヘルパーサービス（仕様書 v1.6 §4.13）。"""

from mailings.models import MailingConfig


def get_or_create_singleton_mailing_config():
    """[性質] 副作用あり（DB 書込：未存在なら id=1 で作成）。

    MailingConfig（シングルトン、§4.13）の初回アクセス時の自動作成（発注書 §3-3）。
    返り値は MailingConfig インスタンス（必ず id=1）。

    本関数は mailings.services.list_freeze と概念は別だが、Phase 1b の小規模ヘルパー
    として services 階層に置く（独立モジュール化は不要、後フェーズで肥大化したら分割）。
    """
    config, _created = MailingConfig.objects.get_or_create(
        id=1,
        defaults={
            "company_name": "",
            "company_address": "",
            "unsubscribe_contact": "",
        },
    )
    return config
