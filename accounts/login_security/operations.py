"""二段階認証・セキュリティ設定・アカウント復旧の管理操作（仕様書 v1.3.1 §6.2〜§6.11 / §8 / §11 / §12.1 / §14）。

構造の原則：
  (a) 権限・前提条件の確認（DB 読み取りのみ）
  (b) 重要操作のコード検証（verify_second_factor）。失敗記録がロールバックされないよう atomic の外で行う。
  (c) 状態の変更・セッション切断・操作ログ記録は transaction.atomic() でひとまとまりに行う。
  (d) 通知メールは notify_* で on_commit に登録する。

注意：本モジュールの操作関数は、呼び出し側が transaction.atomic() ブロックの中から呼ばないこと。
注意：本モジュールの処理は CustomUser モデルを保存しない（§14 #15）。
注意：秘密鍵・リカバリーコード・端末トークン・セッションの中身は操作ログ・エラーログに出力しない。
"""

from dataclasses import dataclass, field
from enum import Enum

from django.db import transaction
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.constants import ActionLogAction
from accounts.models import LoginDevice, RecoveryCode, SecuritySetting
from actionlogs.models import ActionLog

from .devices import (
    delete_user_device,
    revoke_all_devices_trust,
    revoke_device_trust,
    revoke_user_device_trust,
)
from .lockout import unlock_user
from .notifications import (
    WeakenedSettingChange,
    notify_recovery_codes_regenerated,
    notify_security_setting_weakened,
    notify_two_factor_disabled,
    notify_two_factor_enabled,
    notify_two_factor_reenrolled,
    notify_two_factor_reset,
)
from .policy import is_two_factor_required
from .recovery_codes import issue_recovery_codes
from .second_factor import (
    SecondFactorMethod,
    SecondFactorVerifyResult,
    SecondFactorVerifyStatus,
    verify_second_factor,
)
from .session_revoke import revoke_user_sessions
from .totp import (
    TotpEnrollmentData,
    TotpVerifyStatus,
    confirm_totp_enrollment,
    has_confirmed_totp,
    start_totp_enrollment,
)
from .user_lookup import get_security_setting


class OperationStatus(str, Enum):
    """管理操作の共通結果ステータス。"""

    SUCCESS = "success"
    PERMISSION_DENIED = "permission_denied"
    CODE_USED = "code_used"
    CODE_INVALID = "code_invalid"
    REJECTED = "rejected"
    NOT_ALLOWED = "not_allowed"
    NOT_ENROLLED = "not_enrolled"
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"


class DeviceRevokeMode(str, Enum):
    """本人による端末操作の方式。"""

    REVOKE_TRUST = "revoke_trust"
    DELETE = "delete"


@dataclass(frozen=True)
class OperationResult:
    """管理操作の共通結果オブジェクト。"""

    status: OperationStatus
    enrollment: TotpEnrollmentData | None = None
    recovery_codes: tuple[str, ...] = ()
    verify_result: SecondFactorVerifyResult | None = None
    changed_fields: dict[str, tuple[object, object]] = field(default_factory=dict)
    weakened_changes: tuple[WeakenedSettingChange, ...] = ()
    field_errors: dict[str, str] = field(default_factory=dict)
    is_reenroll: bool = False

    @property
    def is_success(self) -> bool:
        return self.status == OperationStatus.SUCCESS


def _map_verify_failure(verify_res: SecondFactorVerifyResult) -> OperationResult:
    if verify_res.status == SecondFactorVerifyStatus.USED:
        return OperationResult(
            status=OperationStatus.CODE_USED,
            verify_result=verify_res,
        )
    if verify_res.is_rejected:
        return OperationResult(
            status=OperationStatus.REJECTED,
            verify_result=verify_res,
        )
    return OperationResult(
        status=OperationStatus.CODE_INVALID,
        verify_result=verify_res,
    )


def _has_admin_permission(operator, perm: str, *, allow_command: bool) -> bool:
    if operator is None:
        return allow_command
    if not getattr(operator, "is_active", False):
        return False
    return bool(operator.has_perm(perm))


def begin_enrollment(
    user,
    *,
    code: str | None = None,
    method: SecondFactorMethod | str = SecondFactorMethod.TOTP,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> OperationResult:
    """【1】認証アプリの登録（初回または再登録）を開始する。

    [性質] 副作用あり（未確定 TOTPDevice 作成。再登録開始時は verify_second_factor による検証・失敗記録）
    [入力] user: CustomUser（ログイン済み本人）
           code: str | None（既に登録済みの場合は必須）
           method: SecondFactorMethod | str
           ip_address: str | None
           user_agent: str | None
           now: datetime | None
    [出力] OperationResult（成功時は enrollment を保持。旧確定済み登録は消さない）

    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    if has_confirmed_totp(user):
        verify_res = verify_second_factor(
            user,
            code,
            method=method,
            ip_address=ip_address,
            user_agent=user_agent,
            now=now,
        )
        if not verify_res.is_success:
            return _map_verify_failure(verify_res)

    enrollment = start_totp_enrollment(user)
    return OperationResult(status=OperationStatus.SUCCESS, enrollment=enrollment)


def complete_enrollment(
    user,
    *,
    device_id: int,
    code: str | None,
    keep_session_key: str | None = None,
    now=None,
) -> OperationResult:
    """【2】未確定の認証アプリ登録を確認コードで確定し、有効化または再登録を完了する。

    [性質] 副作用あり（DB 書込：TOTPDevice 確定／旧確定済み削除／リカバリーコード発行／
            端末信頼無効化／再登録時は他セッション切断／ActionLog 記録 ＋ 通知予約）
    [入力] user: CustomUser
           device_id: int（未確定 TOTPDevice の ID）
           code: str | None（確認コード）
           keep_session_key: str | None（再登録時に残す操作中セッションキー）
           now: datetime | None
    [出力] OperationResult（成功時は recovery_codes と is_reenroll を保持）

    注意：ここでの確認コード失敗は連続失敗回数に加算しない
          （本人が登録する自分の鍵であり、攻撃者の総当たり利益がないため）。
    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    device = TOTPDevice.objects.filter(
        pk=device_id, user=user, confirmed=False
    ).first()
    if device is None:
        return OperationResult(status=OperationStatus.NOT_FOUND)

    with transaction.atomic():
        had_confirmed_before = TOTPDevice.objects.filter(
            user=user, confirmed=True
        ).exists()

        totp_status = confirm_totp_enrollment(device, code)
        if totp_status == TotpVerifyStatus.USED:
            return OperationResult(status=OperationStatus.CODE_USED)
        if totp_status != TotpVerifyStatus.VALID:
            return OperationResult(status=OperationStatus.CODE_INVALID)

        TOTPDevice.objects.filter(user=user, confirmed=True).exclude(
            pk=device.pk
        ).delete()
        codes = issue_recovery_codes(user)
        revoke_user_device_trust(user)

        if had_confirmed_before:
            revoke_user_sessions(
                user, keep_session_key=keep_session_key, now=current
            )

        action = (
            ActionLogAction.TWO_FACTOR_REENROLL
            if had_confirmed_before
            else ActionLogAction.TWO_FACTOR_ENABLE
        )
        ActionLog.record(
            user=user,
            action=action,
            content_object=user,
            object_repr=user.username,
        )

    if had_confirmed_before:
        notify_two_factor_reenrolled(user, operated_at=current)
    else:
        notify_two_factor_enabled(user, operated_at=current)

    return OperationResult(
        status=OperationStatus.SUCCESS,
        recovery_codes=tuple(codes),
        is_reenroll=had_confirmed_before,
    )


def disable_two_factor(
    user,
    *,
    code: str | None,
    method: SecondFactorMethod | str = SecondFactorMethod.TOTP,
    keep_session_key: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> OperationResult:
    """【3】本人操作により二段階認証を無効化する（必須対象は不可）。

    [性質] 副作用あり（コード検証後に atomic で TOTP/リカバリーコード/信頼削除・他セッション切断・ActionLog ＋ 通知）
    [入力] user: CustomUser, code: str | None, method, keep_session_key, ip_address, user_agent, now
    [出力] OperationResult

    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    if not has_confirmed_totp(user):
        return OperationResult(status=OperationStatus.NOT_ENROLLED)
    if is_two_factor_required(user):
        return OperationResult(status=OperationStatus.NOT_ALLOWED)

    verify_res = verify_second_factor(
        user,
        code,
        method=method,
        ip_address=ip_address,
        user_agent=user_agent,
        now=current,
    )
    if not verify_res.is_success:
        return _map_verify_failure(verify_res)

    with transaction.atomic():
        TOTPDevice.objects.filter(user=user).delete()
        RecoveryCode.objects.filter(user=user).delete()
        revoke_user_device_trust(user)
        revoke_user_sessions(
            user, keep_session_key=keep_session_key, now=current
        )
        ActionLog.record(
            user=user,
            action=ActionLogAction.TWO_FACTOR_DISABLE,
            content_object=user,
            object_repr=user.username,
        )

    notify_two_factor_disabled(user, operated_at=current)
    return OperationResult(status=OperationStatus.SUCCESS, verify_result=verify_res)


def regenerate_recovery_codes(
    user,
    *,
    code: str | None,
    method: SecondFactorMethod | str = SecondFactorMethod.TOTP,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now=None,
) -> OperationResult:
    """【4】本人操作によりリカバリーコードを再発行する（旧コードはすべて無効）。

    [性質] 副作用あり（コード検証後に atomic で新コード10個発行・ActionLog ＋ 通知）
    [入力] user: CustomUser, code: str | None, method, ip_address, user_agent, now
    [出力] OperationResult（成功時は recovery_codes に新コード10個を保持）

    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    if not has_confirmed_totp(user):
        return OperationResult(status=OperationStatus.NOT_ENROLLED)

    verify_res = verify_second_factor(
        user,
        code,
        method=method,
        ip_address=ip_address,
        user_agent=user_agent,
        now=current,
    )
    if not verify_res.is_success:
        return _map_verify_failure(verify_res)

    with transaction.atomic():
        codes = issue_recovery_codes(user)
        ActionLog.record(
            user=user,
            action=ActionLogAction.RECOVERY_CODES_REGENERATE,
            content_object=user,
            object_repr=user.username,
        )

    notify_recovery_codes_regenerated(user, operated_at=current)
    return OperationResult(
        status=OperationStatus.SUCCESS,
        recovery_codes=tuple(codes),
        verify_result=verify_res,
    )


def reset_two_factor_by_admin(
    operator,
    target_user,
    *,
    reason_note: str = "",
    as_command: bool = False,
    now=None,
) -> OperationResult:
    """【5】管理者または管理コマンドにより対象ユーザーの二段階認証をリセットする。

    [性質] 副作用あり（権限確認後に atomic で登録/コード/信頼/ロック/全セッション削除・ActionLog ＋ 通知）
    [入力] operator: CustomUser | None（None は as_command=True のときのみ許可）
           target_user: CustomUser
           reason_note: str
           as_command: bool（管理コマンドからの実行フラグ）
           now: datetime | None
    [出力] OperationResult

    注意：自分自身は対象にできない。パスワードは変更しない。
    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    if not _has_admin_permission(
        operator, "accounts.reset_two_factor", allow_command=as_command
    ):
        return OperationResult(status=OperationStatus.PERMISSION_DENIED)

    if operator is not None and operator.pk == target_user.pk:
        return OperationResult(status=OperationStatus.NOT_ALLOWED)

    log_operator = None if (as_command and operator is None) else operator
    with transaction.atomic():
        TOTPDevice.objects.filter(user=target_user).delete()
        RecoveryCode.objects.filter(user=target_user).delete()
        revoke_user_device_trust(target_user)
        unlock_user(target_user)
        revoke_user_sessions(target_user, keep_session_key=None, now=current)
        ActionLog.record(
            user=log_operator,
            action=ActionLogAction.TWO_FACTOR_RESET,
            content_object=target_user,
            object_repr=target_user.username,
            note=(reason_note or "").strip(),
        )

    notify_two_factor_reset(
        target_user,
        operator=log_operator,
        reason_note=reason_note,
        operated_at=current,
    )
    return OperationResult(status=OperationStatus.SUCCESS)


def unlock_account_by_admin(
    operator,
    target_user,
    *,
    as_command: bool = False,
) -> OperationResult:
    """【6】管理者または管理コマンドにより対象ユーザーのアカウントロックを手動解除する。

    [性質] 副作用あり（権限確認後に atomic で AccountLock 解除・ActionLog 記録）
    [入力] operator: CustomUser | None（None は as_command=True のときのみ許可）
           target_user: CustomUser
           as_command: bool
    [出力] OperationResult
    """
    if not _has_admin_permission(
        operator, "accounts.reset_two_factor", allow_command=as_command
    ):
        return OperationResult(status=OperationStatus.PERMISSION_DENIED)

    log_operator = None if (as_command and operator is None) else operator
    with transaction.atomic():
        unlock_user(target_user)
        ActionLog.record(
            user=log_operator,
            action=ActionLogAction.ACCOUNT_UNLOCK,
            content_object=target_user,
            object_repr=target_user.username,
        )
    return OperationResult(status=OperationStatus.SUCCESS)


_ENFORCEMENT_ORDER = {
    SecuritySetting.TwoFactorEnforcement.OPTIONAL: 0,
    SecuritySetting.TwoFactorEnforcement.ADMIN_ONLY: 1,
    SecuritySetting.TwoFactorEnforcement.ALL: 2,
}
_ENFORCEMENT_LABELS = dict(SecuritySetting.TwoFactorEnforcement.choices)
_NOTIFICATION_LEVEL_LABELS = dict(SecuritySetting.LoginNotificationLevel.choices)

_SETTING_FIELDS = (
    "two_factor_enforcement",
    "login_notification_level",
    "lockout_threshold",
    "lockout_minutes",
    "ip_block_threshold",
    "trusted_device_days",
    "notify_admins_on_account_lock",
    "notify_admins_on_ip_block",
)


def _validate_setting_updates(updates: dict[str, object]) -> dict[str, str]:
    errors: dict[str, str] = {}
    int_ranges = {
        "lockout_threshold": (3, 10),
        "lockout_minutes": (5, 1440),
        "ip_block_threshold": (10, 100),
        "trusted_device_days": (0, 90),
    }

    for key, val in updates.items():
        if key not in _SETTING_FIELDS:
            errors[key] = "不明な設定項目です。"
        elif key == "two_factor_enforcement":
            if val not in SecuritySetting.TwoFactorEnforcement.values:
                errors[key] = "二段階認証強制レベルの値が不正です。"
        elif key == "login_notification_level":
            if val not in SecuritySetting.LoginNotificationLevel.values:
                errors[key] = "ログイン通知レベルの値が不正です。"
        elif key in int_ranges:
            low, high = int_ranges[key]
            if isinstance(val, bool) or not isinstance(val, int) or not (low <= val <= high):
                errors[key] = f"{low}〜{high} の整数を指定してください。"
        elif key in (
            "notify_admins_on_account_lock",
            "notify_admins_on_ip_block",
        ):
            if not isinstance(val, bool):
                errors[key] = "真偽値を指定してください。"
    return errors


def detect_weakened_setting_changes(
    before: dict[str, object],
    after: dict[str, object],
) -> list[WeakenedSettingChange]:
    """変更前後の設定辞書から「弱める変更」（仕様書 §8.2）に該当する項目一覧を返す。

    [性質] 純関数（副作用なし）
    [入力] before: dict[str, object], after: dict[str, object]
    [出力] list[WeakenedSettingChange]
    """
    weakened: list[WeakenedSettingChange] = []

    old_enf = before.get("two_factor_enforcement")
    new_enf = after.get("two_factor_enforcement")
    if (
        old_enf in _ENFORCEMENT_ORDER
        and new_enf in _ENFORCEMENT_ORDER
        and _ENFORCEMENT_ORDER[new_enf] < _ENFORCEMENT_ORDER[old_enf]
    ):
        weakened.append(
            WeakenedSettingChange(
                label="二段階認証強制レベル",
                old_value=_ENFORCEMENT_LABELS[old_enf],
                new_value=_ENFORCEMENT_LABELS[new_enf],
            )
        )

    old_notif = before.get("login_notification_level")
    new_notif = after.get("login_notification_level")
    if (
        old_notif == SecuritySetting.LoginNotificationLevel.EVERY
        and new_notif == SecuritySetting.LoginNotificationLevel.NEW_DEVICE_ONLY
    ):
        weakened.append(
            WeakenedSettingChange(
                label="ログイン通知レベル",
                old_value=_NOTIFICATION_LEVEL_LABELS[old_notif],
                new_value=_NOTIFICATION_LEVEL_LABELS[new_notif],
            )
        )

    old_l_th = before.get("lockout_threshold")
    new_l_th = after.get("lockout_threshold")
    if isinstance(old_l_th, int) and isinstance(new_l_th, int) and new_l_th > old_l_th:
        weakened.append(
            WeakenedSettingChange(
                label="ロック失敗回数",
                old_value=f"{old_l_th}回",
                new_value=f"{new_l_th}回",
            )
        )

    old_l_min = before.get("lockout_minutes")
    new_l_min = after.get("lockout_minutes")
    if isinstance(old_l_min, int) and isinstance(new_l_min, int) and new_l_min < old_l_min:
        weakened.append(
            WeakenedSettingChange(
                label="ロック時間（分）",
                old_value=f"{old_l_min}分",
                new_value=f"{new_l_min}分",
            )
        )

    old_ip_th = before.get("ip_block_threshold")
    new_ip_th = after.get("ip_block_threshold")
    if isinstance(old_ip_th, int) and isinstance(new_ip_th, int) and new_ip_th > old_ip_th:
        weakened.append(
            WeakenedSettingChange(
                label="IP遮断失敗回数",
                old_value=f"{old_ip_th}回",
                new_value=f"{new_ip_th}回",
            )
        )

    old_days = before.get("trusted_device_days")
    new_days = after.get("trusted_device_days")
    if isinstance(old_days, int) and isinstance(new_days, int) and new_days > old_days:
        weakened.append(
            WeakenedSettingChange(
                label="信頼期間（日）",
                old_value=f"{old_days}日",
                new_value=f"{new_days}日",
            )
        )

    if (
        before.get("notify_admins_on_account_lock") is True
        and after.get("notify_admins_on_account_lock") is False
    ):
        weakened.append(
            WeakenedSettingChange(
                label="ロック発生を管理者に通知",
                old_value="送る",
                new_value="送らない",
            )
        )

    if (
        before.get("notify_admins_on_ip_block") is True
        and after.get("notify_admins_on_ip_block") is False
    ):
        weakened.append(
            WeakenedSettingChange(
                label="IP遮断を管理者に通知",
                old_value="送る",
                new_value="送らない",
            )
        )

    return weakened


def update_security_setting(
    operator,
    updates: dict[str, object],
    *,
    as_command: bool = False,
    now=None,
) -> OperationResult:
    """【7】セキュリティ設定を更新し、操作ログと（弱める変更時は）管理者通知を行う。

    [性質] 副作用あり（権限・範囲検証後に atomic で設定更新・必要時全信頼無効化・ActionLog ＋ 通知）
    [入力] operator: CustomUser | None, updates: dict[str, object], as_command: bool, now: datetime | None
    [出力] OperationResult（変更なし時はログ・通知なしで SUCCESS）

    注意：信頼期間（trusted_device_days）を 0 以外のより短い日数に変更しても、
          すでに付与済みの端末の信頼期限は変更しない（仕様に定めがないため）。
    注意：呼び出し側は transaction.atomic() の中から呼ばないこと。
    """
    current = now or timezone.now()
    if not _has_admin_permission(
        operator, "accounts.manage_security_setting", allow_command=as_command
    ):
        return OperationResult(status=OperationStatus.PERMISSION_DENIED)

    field_errors = _validate_setting_updates(updates)
    if field_errors:
        return OperationResult(
            status=OperationStatus.VALIDATION_ERROR,
            field_errors=field_errors,
        )

    setting = get_security_setting()
    before_snapshot = {f: getattr(setting, f) for f in _SETTING_FIELDS}
    after_snapshot = dict(before_snapshot)
    changed_fields: dict[str, tuple[object, object]] = {}

    for key, new_val in updates.items():
        old_val = before_snapshot[key]
        if old_val != new_val:
            after_snapshot[key] = new_val
            changed_fields[key] = (old_val, new_val)

    if not changed_fields:
        return OperationResult(status=OperationStatus.SUCCESS)

    weakened = detect_weakened_setting_changes(before_snapshot, after_snapshot)
    log_operator = None if (as_command and operator is None) else operator

    with transaction.atomic():
        for key, (_, new_val) in changed_fields.items():
            setattr(setting, key, new_val)
        setting.updated_by = log_operator
        setting.save()

        if (
            "trusted_device_days" in changed_fields
            and before_snapshot["trusted_device_days"] > 0
            and after_snapshot["trusted_device_days"] == 0
        ):
            revoke_all_devices_trust()

        ActionLog.record(
            user=log_operator,
            action=ActionLogAction.SECURITY_SETTING_UPDATE,
            content_object=setting,
            object_repr="SecuritySetting",
            data={
                "changes": {
                    k: {"before": v[0], "after": v[1]}
                    for k, v in changed_fields.items()
                }
            },
        )

    if weakened:
        notify_security_setting_weakened(
            operator=log_operator,
            changes=weakened,
            operated_at=current,
        )

    return OperationResult(
        status=OperationStatus.SUCCESS,
        changed_fields=changed_fields,
        weakened_changes=tuple(weakened),
    )


def revoke_own_device(
    user,
    device_id: int,
    *,
    mode: DeviceRevokeMode | str = DeviceRevokeMode.DELETE,
) -> OperationResult:
    """【8】本人の端末記録について信頼取り消しまたは記録削除を行う。

    [性質] 副作用あり（atomic で端末の信頼取り消し／削除・ActionLog 記録）
    [入力] user: CustomUser, device_id: int, mode: DeviceRevokeMode | str
    [出力] OperationResult（他ユーザーの端末 ID なら NOT_FOUND）
    """
    mode_value = mode.value if isinstance(mode, DeviceRevokeMode) else str(mode)
    if mode_value not in (
        DeviceRevokeMode.REVOKE_TRUST.value,
        DeviceRevokeMode.DELETE.value,
    ):
        return OperationResult(
            status=OperationStatus.VALIDATION_ERROR,
            field_errors={"mode": "不正な操作種別です。"},
        )

    device = LoginDevice.objects.filter(pk=device_id, user=user).first()
    if device is None:
        return OperationResult(status=OperationStatus.NOT_FOUND)

    with transaction.atomic():
        if mode_value == DeviceRevokeMode.REVOKE_TRUST.value:
            revoke_device_trust(device)
        else:
            delete_user_device(user, device_id)

        ActionLog.record(
            user=user,
            action=ActionLogAction.LOGIN_DEVICE_REVOKE,
            content_object=user,
            object_repr=user.username,
            data={"device_id": device_id, "mode": mode_value},
        )

    return OperationResult(status=OperationStatus.SUCCESS)
