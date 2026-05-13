"""共通 TextChoices と定数（仕様書 v1.4.2 §14.3）。

複数アプリで共通利用する TextChoices と定数をここに集約する。
モデル固有の選択肢は各モデルの内部クラスとして定義する（仕様書 §14.4）。
"""

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
    "company",
    "department",
    "title",
    "branch",
    "email",
    "phone",
    "mobile",
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
    "company": 10,
    "department": 10,
    "title": 5,
    "address": 10,
    "phone": 5,
    "mobile": 80,
}
DUPLICATE_SCORE_EMAIL_PERSONAL = 80
DUPLICATE_SCORE_EMAIL_GENERIC = 5

# ランク判定の閾値（仕様書 §19.3 / 初期値、運用後にチューニング想定）。
POSSIBLE_LOW_MIN_SCORE = 40
POSSIBLE_MID_MIN_SCORE = 120
POSSIBLE_HIGH_MIN_SCORE = 200

# 所属5フィールド（仕様書 §8.4 exact_match の「両方一致 or 両方空」判定用）。
# DUPLICATE_CHECK_FIELDS 9 項目から個人系（full_name / email / phone / mobile）を
# 除いた 5 項目。
DUPLICATE_LOCATION_FIELDS = [
    "company",
    "department",
    "title",
    "branch",
    "address",
]
