"""共通 TextChoices と定数（仕様書 v1.4.2 §14.3）。

複数アプリで共通利用する TextChoices と定数をここに集約する。
モデル固有の選択肢は各モデルの内部クラスとして定義する（仕様書 §14.4）。
"""

import os

from django.db import models
from django.utils.translation import gettext_lazy as _


class PersonChangeReason(models.TextChoices):
    """Contact 編集の修正理由（UpdatePrimaryContactView 専用）。

    仕様書 §14.3.2 / 別表 C.7 参照。5 値（additional_role は v1.4.2 で削除）。
    """

    FIX = "fix", _("入力間違い・誤字訂正")
    TRANSFER = "transfer", _("異動・部署変更")
    PROMOTION = "promotion", _("役職変更・昇進")
    JOB_CHANGE = "job_change", _("転職")
    NAME_CHANGE = "name_change", _("結婚等による姓変更")


class DuplicateMergeReason(models.TextChoices):
    """DuplicateCandidate.review_result の merged 系（マージ画面専用）。

    仕様書 §14.3.3 / 別表 C.8 参照。7 値。
    PersonChangeReason と 4 つの共通値を持つが独立定義（§14.3.1）。
    """

    #SAME_CARD = "same_card", _("同一名刺（撮り直し・重複アップロード）")
    SAME_CARD = "same_card", _("同一名刺")
    TRANSFER = "transfer", _("異動・部署変更")
    PROMOTION = "promotion", _("役職変更・昇進等")
    JOB_CHANGE = "job_change", _("転職")
    ADDITIONAL_ROLE = "additional_role", _("別肩書追加（副業など）")
    NAME_CHANGE = "name_change", _("結婚等による姓変更")
    OTHER_MERGED = "other_merged", _("その他（マージ実行）")


class DifferentPersonReason(models.TextChoices):
    """DuplicateCandidate.review_result の different_person 系。

    仕様書 §14.3.4 / 別表 C.9 参照。3 値。
    """

    SAME_NAME = "same_name", _("同姓同名の別人")
    OCR_ERROR = "ocr_error", _("OCR 誤認識による誤検出")
    OTHER_DIFFERENT = "other_different", _("その他（別人確定）")


# 重複検出のスコア計算と Contact 編集の発火判定で共通利用するフィールド一覧（仕様書 §14.3.5）。
DUPLICATE_CHECK_FIELDS = [
    "full_name",
    "organization",
    "department",
    "title",
    "branch",
    "email",
    "personal_phone",
    "mobile_phone",
    "address",
]

# 代表メール判定の初期リスト（仕様書 §14.3.6 / §8.7）。
DUPLICATE_GENERIC_EMAIL_LOCALPARTS = [
    "info",
    "contact",
    "support",
    "sales",
    "admin",
    "office",
    "mail",
    "inquiry",
    "help",
    "service",
    "shop",
    "customer",
    "reception",
]

# 重複検出のスコア配点（仕様書 §8.3 / 初期値、運用後にチューニング想定）。
# 各フィールドの「両 Contact で confidence='high' 扱い かつ 完全一致」時の加算点数。
# email は個人メール / 代表メールで点数が異なるため別定数に分け、サービス層で
# DUPLICATE_GENERIC_EMAIL_LOCALPARTS による判定後にどちらを使うかを決める。
# branch（支店・営業所）は配点なし（所属5フィールド判定にのみ参加）。
DUPLICATE_FIELD_SCORES = {
    "full_name": 40,
    "organization": 10,
    "department": 10,
    "title": 5,
    "address": 10,
    "personal_phone": 5,
    "mobile_phone": 80,
}
DUPLICATE_SCORE_EMAIL_PERSONAL = 80
DUPLICATE_SCORE_EMAIL_GENERIC = 5

# ランク判定の閾値（仕様書 §19.3 / 初期値、運用後にチューニング想定）。
POSSIBLE_LOW_MIN_SCORE = 40
POSSIBLE_MID_MIN_SCORE = 120
POSSIBLE_HIGH_MIN_SCORE = 200

# 所属5フィールド（仕様書 §8.4 exact_match の「両方一致 or 両方空」判定用）。
# DUPLICATE_CHECK_FIELDS 9 項目から個人系（full_name / email / personal_phone / mobile_phone）を
# 除いた 5 項目。
DUPLICATE_LOCATION_FIELDS = [
    "organization",
    "department",
    "title",
    "branch",
    "address",
]

# 検索結果一括タグ付け（仕様書 v1.6 §6.2.6）の Person 件数上限。
# 1 回の操作で全 Person × 全タグを誤選択する事故防止と、
# bulk_create の SQL バッファ・サーバ応答時間の現実的な上限。
BULK_TAGGING_MAX_PERSONS = 500

# ======================================================================
# v1.6 メルマガ配信系（Phase 3）
# ======================================================================

# cron 1 起動あたりの未送信受信者処理上限 N（仕様書 v1.6 §7.2.1）。
# send_scheduled_campaigns 管理コマンドが 1 起動でこの件数まで処理し、
# 残りは次回 cron 起動が続きを拾う（自然再処理方式、§7.2.4 弱点許容）。
CAMPAIGN_SEND_BATCH_LIMIT = 100

# 受信者単位の送信失敗上限 M（仕様書 v1.6 §7.2.2）。
# DeliveryHistory.failed_count がこの値に達したら「最終 failed」確定、
# Campaign.status=done 判定の対象になる。
# 重複チェック系（失敗回数を数えない）からの意図的逸脱の理由は §7.2.2 重要警告を参照：
# 配信は永久失敗メアド（無効アドレス等）が混在するため回数を数えないと永久に done にならない。
CAMPAIGN_RECIPIENT_MAX_FAILURES = 3

# ソフトバウンス連続回数の SuppressedEmail 昇格閾値（仕様書 v1.6 §4.8A / §10.3.2）。
# SoftBounceCounter.count がこの値に達したら SuppressedEmail に
# source='bounce_soft_promoted' で登録、SoftBounceCounter は物理削除。
# Phase 5 で使うが、Phase 3 時点で定数を予約定義する（§19.1 論点 4）。
SOFT_BOUNCE_PROMOTION_THRESHOLD = 5

# ClickLog.ip_masked の保持日数（仕様書 v1.6 §4.6 / §8.3.2）。
# 期限経過後に管理コマンドが NULL 上書きする（個人情報保護観点）。
# Phase 4 で使うが、Phase 3 時点で定数を予約定義する。
CLICK_LOG_IP_RETENTION_DAYS = 90

# 配信メール内のリンクのベース URL（仕様書 v1.6 §7.4.6.4 / §8.1）。
# TrackingLink: {MAILING_SITE_URL}/t/<token>/、UnsubscribeLink: {MAILING_SITE_URL}/u/<token>/
# .env の MAILING_SITE_URL から取得する（settings.py の load_dotenv が先行読込済みのため
# constants 読込時には os.environ に反映されている）。環境別管理を前倒しで導入。
# 末尾スラッシュは email_context のヘルパーが "/t/<token>/" の形で先頭に付けるため、
# 二重スラッシュ防止に rstrip("/") で正規化する（.env 側に付けても安全）。
# 未設定時は runserver 既定の http://localhost:8000 にフォールバック（example.com には戻さない）。
MAILING_SITE_URL = os.getenv("MAILING_SITE_URL", "http://localhost:8000").rstrip("/")

# ======================================================================
# v1.6 クリックトラッキング系（Phase 4、仕様書 §8.2.1）
# ======================================================================

# クリック中継ビュー（TrackingRedirectView）の User-Agent ボット判定パターン（§8.2 順2）。
# 部分一致・大文字小文字区別なしで判定する（detect_bot_status 純関数側で小文字化）。
# v1.7+ で拡充予定（仕様書 §8.2.1）。クラウド IP レンジ判定は v1.6 ではスコープ外。
KNOWN_BOT_USER_AGENT_PATTERNS = [
    "bot",
    "crawl",
    "spider",
    "fetch",
    "preview",
    "scan",
    "monitor",
    "headless",
    "slurp",
    "curl",
    "wget",
    "python-requests",
]

# プリフェッチ判定の閾値秒数（§8.2 順3、§8.2.1）。
# TrackingLink.created_at から本秒数以内のクリックはメーラープリフェッチ扱い（too_fast）。
# TrackingLink.created_at は PRODUCTION mode 配信の (III) atomic 内で確定するため、
# 実質「メール送信時刻」と近似される（仕様書 §7.4.3.3 / §8.4）。
CLICK_PREFETCH_THRESHOLD_SECONDS = 3
