"""バウンス処理抽象化レイヤー（仕様書 v1.6 §10、rev14）。

ハード/ソフトの判定済みバウンスを受け取り、SuppressedEmail / SoftBounceCounter /
DeliveryHistory に反映する。自社 SMTP（cron + IMAP/POP3）と外部 MTA（Webhook）の
2 つの取り込み口は本モジュールに集約される（§10.2 抽象化レイヤー）。

【設計原則（§10.0 / §7.2.3）】
  - バウンスは SuppressedEmail にのみ書き込み（Unsubscribe は作らない、§10.0）
  - Person.is_unsubscribed には触らない（§10.0）
  - 同一人物ユニット伝播はしない（メアド単位で SuppressedEmail フィルタが自動的に効く）
  - 送信失敗（DeliveryHistory.status=failed）と混同しない：送信失敗時に本モジュールの
    process_soft_bounce は呼ばない（§7.2.3 / §10.0 重大警告）
  - DeliveryHistory bounced 反映はベストエフォート（§10.4.1）。to_email 照合で
    引けた全件 update。Message-ID 照合や最新 1 件選別ロジックは作らない。

【処理経路】
  process_bounce(email, bounce_type, reason)
      ├─ 'hard' → process_hard_bounce(email, reason)
      │           → SuppressedEmail(source='bounce_hard') 作成（既存 active があれば skip）
      └─ 'soft' → process_soft_bounce(email, reason)
                  → SoftBounceCounter インクリメント
                     閾値到達なら SuppressedEmail(source='bounce_soft_promoted') 昇格 +
                     SoftBounceCounter 物理削除
      共通最後：_update_delivery_history_best_effort(email, reason) で DeliveryHistory
              に bounced 反映（ベストエフォート、§10.4）

  process_send_success(email)
      → SoftBounceCounter を物理削除（連続失敗の解消、§10.3.2 / §4.8A.2）。
        Phase 3 の send_one_recipient SENT 化直後で呼ぶ（後付け配線、Phase 5）。
"""

from __future__ import annotations

from typing import Optional

from django.db import transaction
from django.utils import timezone

from config.constants import SOFT_BOUNCE_PROMOTION_THRESHOLD
from mailings.models import (
    DeliveryHistory,
    SoftBounceCounter,
    SuppressedEmail,
)


BOUNCE_TYPE_HARD = "hard"
BOUNCE_TYPE_SOFT = "soft"


def process_bounce(email: str, bounce_type: str, reason: str = "") -> None:
    """バウンスを処理する統合エントリ（§10.2 抽象化レイヤー）。

    [性質] 副作用あり（DB 書込：SuppressedEmail / SoftBounceCounter / DeliveryHistory）
    [入力]
      email: str（バウンス対象メアド）
      bounce_type: 'hard' | 'soft'（§10.3、判定は呼び出し元の bounce_smtp_handler /
                   bounce_webhook_view が行う）
      reason: str（MTA からの応答メッセージ等、bounce_reason / error_message に保存）
    [出力] None
    [例外] ValueError（bounce_type が想定外の値の場合）

    本関数は判定済みのバウンスを受け取って反映に徹する。判定（5xx か 4xx か等）は
    呼び出し元の責務。
    """
    if not email:
        return  # 空メアドは無視（無効な通知）

    if bounce_type == BOUNCE_TYPE_HARD:
        process_hard_bounce(email, reason)
    elif bounce_type == BOUNCE_TYPE_SOFT:
        process_soft_bounce(email, reason)
    else:
        raise ValueError(f"Unknown bounce_type: {bounce_type!r}")

    # 共通：DeliveryHistory への反映（ベストエフォート、§10.4.1）
    _update_delivery_history_best_effort(email, reason, bounce_type)


def process_hard_bounce(email: str, reason: str = "") -> None:
    """ハードバウンスを即 SuppressedEmail に登録する（§10.3.1）。

    [性質] 副作用あり（DB 書込：SuppressedEmail 1 件）
    [入力] email: str、reason: str
    [出力] None

    冪等性：既に active な SuppressedEmail（同 email、cancelled_at=NULL）があれば
    新規作成しない（partial unique constraint で IntegrityError は出ないが、
    bounce_reason の上書き等は不要）。
    """
    with transaction.atomic():
        active_exists = SuppressedEmail.objects.filter(
            email=email, cancelled_at__isnull=True
        ).exists()
        if active_exists:
            return
        SuppressedEmail.objects.create(
            email=email,
            source=SuppressedEmail.Source.BOUNCE_HARD,
            bounce_reason=reason or "",
        )


def process_soft_bounce(email: str, reason: str = "") -> None:
    """ソフトバウンスを処理する（§10.3.2、SoftBounceCounter 連続方式）。

    [性質] 副作用あり（DB 書込：SoftBounceCounter または SuppressedEmail）
    [入力] email: str、reason: str
    [出力] None

    閾値（SOFT_BOUNCE_PROMOTION_THRESHOLD=5）到達でハード昇格：
      - SuppressedEmail(source='bounce_soft_promoted') を作成
      - SoftBounceCounter を物理削除（解除概念なし、§4.8A）

    閾値未満なら SoftBounceCounter の count をインクリメントするだけ。

    【重要】 送信失敗（§7.2.3）からは本関数を呼ばない。送信成功後の跳ね返り通知
    のみが本関数の対象（§10.0 重大警告参照）。
    """
    with transaction.atomic():
        # 既に SuppressedEmail に登録済みなら何もしない（重複昇格防止）
        if SuppressedEmail.objects.filter(
            email=email, cancelled_at__isnull=True
        ).exists():
            return

        counter, _created = SoftBounceCounter.objects.get_or_create(
            email=email,
            defaults={"count": 0, "last_bounce_at": timezone.now()},
        )
        counter.count += 1
        counter.last_bounce_at = timezone.now()
        counter.save(update_fields=["count", "last_bounce_at", "updated_at"])

        if counter.count >= SOFT_BOUNCE_PROMOTION_THRESHOLD:
            # 昇格 → SuppressedEmail に登録、カウンタは物理削除
            SuppressedEmail.objects.create(
                email=email,
                source=SuppressedEmail.Source.BOUNCE_SOFT_PROMOTED,
                bounce_reason=(
                    f"Soft bounce promoted after {counter.count} consecutive "
                    f"failures: {reason}"
                )[:5000],
            )
            counter.delete()


def process_send_success(email: str) -> None:
    """送信成功時の処理。SoftBounceCounter を物理削除して連続失敗状態をリセットする
    （§10.3.2 / §4.8A.2、連続方式）。

    [性質] 副作用あり（DB 書込：SoftBounceCounter 削除）
    [入力] email: str（送信に成功したメアド）
    [出力] None

    Phase 3 の send_one_recipient SENT 化直後に呼ばれる（Phase 5 で後付け配線、
    指示書 §2-2）。冪等：カウンタが無ければ何も起きない。
    """
    if not email:
        return
    SoftBounceCounter.objects.filter(email=email).delete()


def _update_delivery_history_best_effort(
    email: str, reason: str, bounce_type: str
) -> None:
    """DeliveryHistory.status='bounced' をベストエフォートで反映する（§10.4.1）。

    [性質] 副作用あり（DB 書込：DeliveryHistory 0..N 件 update）
    [入力] email: str、reason: str、bounce_type: 'hard' | 'soft'
    [出力] None

    to_email 照合で引けた全件を一括 update（最新 1 件か全件かの選別ロジックは
    入れない、§10.4.1）。Message-ID 照合も行わない。

    既に bounced のものも error_message を上書きする（最新のバウンス理由を反映）。
    Phase 3 の status 別の意味付け：bounced は実効力なし（実効力は SuppressedEmail
    フィルタが持つ）、レポート上の参考情報（§10.4.1）。
    """
    error_msg = f"{bounce_type}: {reason}"[:5000]
    DeliveryHistory.objects.filter(to_email=email).update(
        status=DeliveryHistory.Status.BOUNCED,
        error_message=error_msg,
    )
