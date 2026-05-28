"""差出ドメイン検証（仕様書 v1.6 §7.8.3、rev14）。

`sender_mode='newsletter'` のとき、`Campaign.sender_email` のドメインが
MailingConfig.dkim_domain 配下か文字列照合で検証する。許可外なら ValidationError。

Phase 3 では文字列照合だけ実装。DKIM 署名本体・キーペア管理は Phase 8（§15）。
仕様書「Settings」表記は実装ではシングルトン MailingConfig（rev9 / 発注書 §1.1 改名）。

呼ばれる箇所（二段、§7.8.3）：
  - キャンペーン保存時（draft 保存・確定保存とも、Campaign.clean() / フォーム層）
  - 配信実行直前（cron 起動時の (II) オーケストレーション冒頭）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

from mailings.models import MailingConfig

if TYPE_CHECKING:
    from mailings.models import Campaign


def validate_sender_domain(campaign: "Campaign") -> None:
    """sender_email のドメインが MailingConfig.dkim_domain 配下か検証する（§7.8.3）。

    [性質] 副作用あり（許可外で ValidationError を raise）、DB 読み取りあり（MailingConfig 取得）
    [入力] campaign: Campaign
    [出力] None（許可済なら何も返さない）
    [例外] ValidationError（許可外ドメイン or sender_email 空 or MailingConfig 未設定）

    sender_mode='creator' のときは検証不要（差出人=created_by の個人メアド）。
    sender_mode='newsletter' のときのみ呼ぶ。
    """
    if campaign.sender_mode != campaign.SenderMode.NEWSLETTER:
        return  # メール作成者方式は検証対象外

    sender_email = (campaign.sender_email or "").strip()
    if not sender_email:
        raise ValidationError(
            "sender_mode='newsletter' のとき sender_email は必須です（§7.8.3）"
        )
    if "@" not in sender_email:
        raise ValidationError(
            f"sender_email '{sender_email}' は不正な形式です（§7.8.3）"
        )
    sender_domain = sender_email.rsplit("@", 1)[1].lower()

    config = MailingConfig.objects.filter(pk=1).first()
    allowed_domain = (config.dkim_domain or "").strip().lower() if config else ""
    if not allowed_domain:
        raise ValidationError(
            "MailingConfig.dkim_domain が未設定のため、メルマガ方式の差出ドメインを検証できません"
            "（§7.8.3）"
        )

    # 完全一致 or サブドメイン許容（例：dkim_domain='example.com' のとき
    # 'mail.example.com' / 'example.com' の両方を許可）。
    if sender_domain != allowed_domain and not sender_domain.endswith(
        "." + allowed_domain
    ):
        raise ValidationError(
            f"sender_email のドメイン '{sender_domain}' は許可ドメイン "
            f"'{allowed_domain}' 配下ではありません（§7.8.3、なりすまし防止）"
        )
