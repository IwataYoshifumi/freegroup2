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

    SAME_CARD = "same_card", _("同一名刺（撮り直し・重複アップロード）")
    TRANSFER = "transfer", _("異動・部署変更")
    PROMOTION = "promotion", _("役職変更・昇進")
    JOB_CHANGE = "job_change", _("転職")
    ADDITIONAL_ROLE = "additional_role", _("別肩書追加（副業など）")
    NAME_CHANGE = "name_change", _("結婚等による姓変更")
    OTHER_MERGED = "other_merged", _("その他（マージ実行）")


class DifferentPersonReason(models.TextChoices):
    """DuplicateCandidate.review_result の different_person 系。

    仕様書 §14.3.4 / 別表 C.9 参照。3 値。
    """

    SAME_NAME = "same_name", _("同姓同名")
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
