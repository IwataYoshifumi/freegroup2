"""PostgreSQL 実機用のログイン失敗加算・ロック／IP遮断 同時実行確認スクリプト。

目的:
  SQLite のユニットテストでは再現できない、PostgreSQL 環境での同時リクエスト時の
  失敗回数加算・初回行作成競合（IntegrityError 再取得）・条件付きロック成立・IP 遮断
  （仕様書 v1.3.1 §5.1〜§5.4 / §14-4）をマルチスレッドで検証する。

前提・安全策:
  - PostgreSQL（ENGINE に postgresql を含む）以外の DB（SQLite 等）では何も実行せず
    終了コード 2 で終了する。
  - `--yes` オプションを付けない場合は実行内容と接続先 DB 情報だけを表示して終了する。
  - メールバックエンドを強制的にメモリ実装（locmem）に切り替え、実メールは送信しない。
  - テスト用ユーザーは一意な接頭辞 `__conc_chk_<hex>_` 付きで都度作成し、IP は RFC 5737
    のテスト用予約アドレス帯（192.0.2.0/24）のみを使用する。
  - 実行終了時（例外・Ctrl+C を含む `finally`）に本スクリプトが作成したレコードのみ削除する。

実行方法（通常）:
  python scripts/check_login_security_concurrency.py
  python scripts/check_login_security_concurrency.py --yes
  python scripts/check_login_security_concurrency.py --yes --repeat 10

Docker 本番環境での実行方法と持ち込み手順:
  ※ `docker-compose.yml` の `web` サービスはソースコードを bind-mount せず、
     `Dockerfile` のビルド時 `COPY . .` でイメージに含めています。
     このスクリプトがまだ `web` コンテナ内の `/app/scripts/` に無い場合は、
     リポジトリを再ビルドするか、以下の手順でコンテナへコピーしてから実行してください。

  1) スクリプトを `web` コンテナへコピーする（再ビルドしない場合）:
     docker compose exec web mkdir -p /app/scripts
     docker compose cp scripts/check_login_security_concurrency.py web:/app/scripts/check_login_security_concurrency.py

  2) 接続先の確認（--yes なし・何も書き込まない）:
     docker compose exec web python scripts/check_login_security_concurrency.py

  3) 実行（--yes 付き）:
     docker compose exec web python scripts/check_login_security_concurrency.py --yes
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
import os
from pathlib import Path
import secrets
import sys
import threading
from typing import TextIO

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_USERNAME_PREFIX_ROOT = "__conc_chk_"


@dataclass(frozen=True)
class CaseResult:
    """1 回の試行（またはケース集計）の結果。"""

    case_name: str
    trial: int
    threads: int
    expected: str
    actual: str
    passed: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PostgreSQL 用ログインセキュリティ同時実行確認スクリプト",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="実際にテスト用データを書き込んで同時実行検証を行う（未指定時は確認表示のみ）。",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=5,
        help="各ケースの繰り返し回数（既定: 5）。",
    )
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat は 1 以上を指定してください。")
    return args


def is_postgresql_engine(engine: str | None) -> bool:
    """DB エンジン文字列が PostgreSQL 系かを判定する。"""
    return "postgresql" in (engine or "").lower()


def format_db_target(db_settings: dict) -> str:
    """接続先 DB の概要文字列（パスワード除外）を返す。"""
    engine = db_settings.get("ENGINE", "")
    name = db_settings.get("NAME", "")
    host = db_settings.get("HOST") or "(既定/ローカル)"
    port = db_settings.get("PORT") or "(既定)"
    return f"ENGINE={engine}, NAME={name}, HOST={host}, PORT={port}"


def validate_execution_guard(
    argv: list[str] | None = None,
    *,
    db_settings: dict | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> tuple[int | None, argparse.Namespace]:
    """実行前安全チェックを行う。

    戻り値の第1要素:
      - `2`: PostgreSQL 以外の DB（SQLite 等）のため拒否終了
      - `0`: `--yes` 未指定のため実行内容・接続先を表示して終了
      - `None`: PostgreSQL かつ `--yes` 指定済み（実行許可）
    """
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = parse_args(argv)

    if db_settings is None:
        from django.conf import settings

        db_settings = settings.DATABASES["default"]

    engine = str(db_settings.get("ENGINE", ""))
    if not is_postgresql_engine(engine):
        err.write(
            "エラー: このスクリプトは PostgreSQL 環境専用です"
            f"（現在の DB エンジン: {engine or '未設定'}）。\n"
            "開発環境（SQLite）等では誤実行防止のため処理を中止しました。\n"
        )
        return 2, args

    target_summary = format_db_target(db_settings)
    if not args.yes:
        out.write(
            "=== 同時実行確認スクリプト（実行前プレビュー） ===\n"
            f"接続先 DB: {target_summary}\n"
            f"繰り返し回数: {args.repeat} 回/ケース\n"
            "確認内容:\n"
            "  - アカウントロック（ケース A: しきい値-1 同時実行 / ケース B: しきい値×2 同時実行 / ケース C: しきい値直前から 2 同時実行）\n"
            "  - IP 遮断（IP ケース A: しきい値-1 同時実行 / IP ケース B: しきい値×2 同時実行 / IP ケース C: しきい値直前から 2 同時実行）\n"
            "安全策:\n"
            "  - メール送信バックエンドは locmem に固定し、実メールは送信しません。\n"
            "  - 使い捨てユーザー（__conc_chk_*）と予約 IP（192.0.2.0/24）のみを作成し、終了時に削除します。\n"
            "実際に実行するには `--yes` を付けて再実行してください。\n"
        )
        return 0, args

    return None, args


def setup_django() -> None:
    """単独スクリプト実行向けに Django を初期化し、メール送信を locmem に固定する。"""
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django
    from django.conf import settings

    django.setup()
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


def _fetch_postgres_version() -> str:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT version()")
        row = cursor.fetchone()
    return str(row[0]) if row else "unknown"


def _run_concurrent_user_failures(
    user_id: int,
    *,
    thread_count: int,
    setting_id: int,
    base_now,
) -> tuple[list[bool], list[BaseException]]:
    """指定ユーザーに対して `thread_count` 個のスレッドで同時に失敗を記録する。"""
    from django.db import connections
    from accounts.login_security.lockout import record_user_failure_and_check_lock
    from accounts.models import CustomUser, SecuritySetting

    barrier = threading.Barrier(thread_count)
    results: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(idx: int) -> None:
        try:
            u = CustomUser.objects.get(pk=user_id)
            cfg = SecuritySetting.objects.get(pk=setting_id)
            barrier.wait(timeout=30)
            # 仕様 §14-4: transaction.atomic() の外（autocommit）で呼ぶ
            locked_now = record_user_failure_and_check_lock(
                u,
                setting=cfg,
                now=base_now + timedelta(milliseconds=idx),
            )
            with lock:
                results.append(bool(locked_now))
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        if t.is_alive():
            errors.append(TimeoutError("ワーカースレッドがタイムアウトしました。"))

    return results, errors


def _run_concurrent_ip_failures(
    *,
    username_input: str,
    ip_address: str,
    thread_count: int,
    base_now,
) -> tuple[list[bool], list[BaseException]]:
    """指定 IP に対して `thread_count` 個のスレッドで同時に失敗記録を実行する。"""
    from django.db import connections
    from accounts.login_security.lockout import record_failure_and_evaluate
    from accounts.models import LoginAttempt

    barrier = threading.Barrier(thread_count)
    ip_blocked_flags: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=30)
            res = record_failure_and_evaluate(
                username_input=username_input,
                target_users=[],
                primary_user=None,
                ip_address=ip_address,
                user_agent="ConcurrencyCheckScript/1.0",
                result=LoginAttempt.Result.PASSWORD_FAILED,
                now=base_now + timedelta(milliseconds=idx),
            )
            with lock:
                ip_blocked_flags.append(bool(res.ip_blocked_now))
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        if t.is_alive():
            errors.append(TimeoutError("ワーカースレッドがタイムアウトしました。"))

    return ip_blocked_flags, errors


def cleanup_test_artifacts(username_prefix: str, used_ips: list[str]) -> dict[str, int]:
    """本スクリプトが作成したユーザー・失敗記録・ロック・試行記録・操作ログだけを削除する。"""
    from django.contrib.contenttypes.models import ContentType
    from django.db import connections
    from django.db.models import Q
    from accounts.models import AccountLock, CustomUser, LoginAttempt
    from actionlogs.models import ActionLog

    connections.close_all()
    counts = {"account_lock": 0, "login_attempt": 0, "action_log": 0, "custom_user": 0}

    user_ct = ContentType.objects.get_for_model(CustomUser)
    user_ids = list(
        CustomUser.objects.filter(username__startswith=username_prefix).values_list(
            "id", flat=True
        )
    )
    user_ids_str = [str(uid) for uid in user_ids]

    deleted_locks, _ = AccountLock.objects.filter(
        user__username__startswith=username_prefix
    ).delete()
    counts["account_lock"] = deleted_locks

    attempt_q = Q(user__username__startswith=username_prefix) | Q(
        username_input__startswith=username_prefix
    )
    if used_ips:
        attempt_q |= Q(ip_address__in=used_ips)
    deleted_attempts, _ = LoginAttempt.objects.filter(attempt_q).delete()
    counts["login_attempt"] = deleted_attempts

    action_q = Q(object_repr__startswith=username_prefix)
    if user_ids_str:
        action_q |= Q(content_type=user_ct, object_id__in=user_ids_str)
    if used_ips:
        action_q |= Q(object_repr__in=used_ips) | Q(data__ip_address__in=used_ips)
    deleted_actions, _ = ActionLog.objects.filter(action_q).delete()
    counts["action_log"] = deleted_actions

    deleted_users, _ = CustomUser.objects.filter(
        username__startswith=username_prefix
    ).delete()
    counts["custom_user"] = deleted_users

    return counts


def run_concurrency_checks(repeat: int, stdout: TextIO | None = None) -> int:
    """アカウントロックと IP 遮断の同時実行検証を実行し、終了コード（0/1）を返す。"""
    from django.utils import timezone
    from accounts.constants import ActionLogAction
    from accounts.login_security.lockout import (
        is_ip_blocked,
        is_user_locked,
        record_user_failure_and_check_lock,
    )
    from accounts.login_security.user_lookup import get_security_setting
    from accounts.models import AccountLock, CustomUser, LoginAttempt
    from actionlogs.models import ActionLog

    out = stdout if stdout is not None else sys.stdout
    run_token = secrets.token_hex(4)
    username_prefix = f"{TEST_USERNAME_PREFIX_ROOT}{run_token}_"
    used_ips: list[str] = []
    results: list[CaseResult] = []

    pg_version = _fetch_postgres_version()
    cfg = get_security_setting()
    lock_th = cfg.lockout_threshold
    ip_th = cfg.ip_block_threshold

    out.write(f"PostgreSQL バージョン: {pg_version}\n")
    out.write(
        f"セキュリティ設定: lockout_threshold={lock_th}, lockout_minutes={cfg.lockout_minutes}, "
        f"ip_block_threshold={ip_th}, repeat={repeat}\n"
    )
    out.write(f"テスト用ユーザー接頭辞: {username_prefix}\n\n")

    ip_counter = 10

    def next_test_ip() -> str:
        nonlocal ip_counter
        # RFC 5737 TEST-NET-1 (192.0.2.0/24) の .10〜.250 を循環割り当て（不足時は事前削除）
        octet = 10 + ((ip_counter - 10) % 240)
        ip_counter += 1
        ip = f"192.0.2.{octet}"
        if ip in used_ips:
            LoginAttempt.objects.filter(ip_address=ip).delete()
            ActionLog.objects.filter(
                action=ActionLogAction.IP_BLOCK, data__ip_address=ip
            ).delete()
        else:
            used_ips.append(ip)
        return ip

    try:
        for trial in range(1, repeat + 1):
            base_now = timezone.now()

            # ------------------------------------------------------------
            # アカウントロック ケース A: しきい値 - 1 同時実行
            # ------------------------------------------------------------
            u_a = CustomUser.objects.create_user(
                username=f"{username_prefix}a_{trial}",
                password="pw",
                email=f"{username_prefix}a_{trial}@example.invalid",
            )
            threads_a = lock_th - 1
            flags_a, errs_a = _run_concurrent_user_failures(
                u_a.pk,
                thread_count=threads_a,
                setting_id=cfg.pk,
                base_now=base_now,
            )
            lock_obj_a = AccountLock.objects.filter(user=u_a).first()
            fc_a = lock_obj_a.failure_count if lock_obj_a else -1
            locked_cnt_a = sum(1 for f in flags_a if f)
            is_locked_a = is_user_locked(u_a, now=base_now + timedelta(seconds=1))
            passed_a = (
                not errs_a
                and fc_a == threads_a
                and locked_cnt_a == 0
                and not is_locked_a
            )
            results.append(
                CaseResult(
                    case_name="アカウント ケースA（しきい値-1 同時）",
                    trial=trial,
                    threads=threads_a,
                    expected=f"例外=0, failure_count={threads_a}, ロック成立=0, ロック中=False",
                    actual=f"例外={len(errs_a)}, failure_count={fc_a}, ロック成立={locked_cnt_a}, ロック中={is_locked_a}",
                    passed=passed_a,
                )
            )

            # ------------------------------------------------------------
            # アカウントロック ケース B: しきい値 × 2 同時実行
            # ------------------------------------------------------------
            u_b = CustomUser.objects.create_user(
                username=f"{username_prefix}b_{trial}",
                password="pw",
                email=f"{username_prefix}b_{trial}@example.invalid",
            )
            threads_b = lock_th * 2
            flags_b, errs_b = _run_concurrent_user_failures(
                u_b.pk,
                thread_count=threads_b,
                setting_id=cfg.pk,
                base_now=base_now,
            )
            lock_rows_b = AccountLock.objects.filter(user=u_b).count()
            lock_obj_b = AccountLock.objects.filter(user=u_b).first()
            fc_b = lock_obj_b.failure_count if lock_obj_b else -1
            locked_cnt_b = sum(1 for f in flags_b if f)
            is_locked_b = is_user_locked(u_b, now=base_now + timedelta(seconds=1))
            # ロック確立後の追加失敗は数えないことを確認（§5.1）
            if lock_obj_b is not None:
                extra_locked = record_user_failure_and_check_lock(
                    u_b, setting=cfg, now=base_now + timedelta(seconds=2)
                )
                lock_obj_b.refresh_from_db()
                fc_b_after = lock_obj_b.failure_count
            else:
                extra_locked = False
                fc_b_after = -1
            passed_b = (
                not errs_b
                and lock_rows_b == 1
                and locked_cnt_b == 1
                and is_locked_b
                and (lock_th <= fc_b <= threads_b)
                and (not extra_locked)
                and (fc_b_after == fc_b)
            )
            results.append(
                CaseResult(
                    case_name="アカウント ケースB（しきい値×2 同時）",
                    trial=trial,
                    threads=threads_b,
                    expected=f"例外=0, 行=1, ロック成立=1, {lock_th}<=fc<={threads_b}, ロック後不変",
                    actual=f"例外={len(errs_b)}, 行={lock_rows_b}, ロック成立={locked_cnt_b}, fc={fc_b}->{fc_b_after}",
                    passed=passed_b,
                )
            )

            # ------------------------------------------------------------
            # アカウントロック ケース C: しきい値-1 到達後に 2 同時実行
            # ------------------------------------------------------------
            u_c = CustomUser.objects.create_user(
                username=f"{username_prefix}c_{trial}",
                password="pw",
                email=f"{username_prefix}c_{trial}@example.invalid",
            )
            for s in range(lock_th - 1):
                record_user_failure_and_check_lock(
                    u_c, setting=cfg, now=base_now + timedelta(seconds=s)
                )
            flags_c, errs_c = _run_concurrent_user_failures(
                u_c.pk,
                thread_count=2,
                setting_id=cfg.pk,
                base_now=base_now + timedelta(seconds=lock_th),
            )
            lock_rows_c = AccountLock.objects.filter(user=u_c).count()
            lock_obj_c = AccountLock.objects.filter(user=u_c).first()
            fc_c = lock_obj_c.failure_count if lock_obj_c else -1
            locked_cnt_c = sum(1 for f in flags_c if f)
            is_locked_c = is_user_locked(
                u_c, now=base_now + timedelta(seconds=lock_th + 1)
            )
            passed_c = (
                not errs_c
                and lock_rows_c == 1
                and locked_cnt_c == 1
                and is_locked_c
                and (lock_th <= fc_c <= lock_th + 1)
            )
            results.append(
                CaseResult(
                    case_name="アカウント ケースC（直前状態から 2 同時）",
                    trial=trial,
                    threads=2,
                    expected=f"例外=0, 行=1, ロック成立=1, {lock_th}<=fc<={lock_th + 1}",
                    actual=f"例外={len(errs_c)}, 行={lock_rows_c}, ロック成立={locked_cnt_c}, fc={fc_c}",
                    passed=passed_c,
                )
            )

            # ------------------------------------------------------------
            # IP 遮断 ケース A: しきい値 - 1 同時実行
            # ------------------------------------------------------------
            ip_a = next_test_ip()
            LoginAttempt.objects.filter(ip_address=ip_a).delete()
            ActionLog.objects.filter(
                action=ActionLogAction.IP_BLOCK, data__ip_address=ip_a
            ).delete()
            threads_ipa = ip_th - 1
            ip_flags_a, errs_ipa = _run_concurrent_ip_failures(
                username_input=f"{username_prefix}ipa_{trial}",
                ip_address=ip_a,
                thread_count=threads_ipa,
                base_now=base_now,
            )
            cnt_ipa = LoginAttempt.objects.filter(ip_address=ip_a).count()
            blocked_ipa = is_ip_blocked(
                ip_a, setting=cfg, now=base_now + timedelta(seconds=1)
            )
            blk_flag_cnt_a = sum(1 for f in ip_flags_a if f)
            passed_ipa = (
                not errs_ipa
                and cnt_ipa == threads_ipa
                and blk_flag_cnt_a == 0
                and not blocked_ipa
            )
            results.append(
                CaseResult(
                    case_name="IP遮断 ケースA（しきい値-1 同時）",
                    trial=trial,
                    threads=threads_ipa,
                    expected=f"例外=0, 試行記録={threads_ipa}, 遮断成立=0, 遮断中=False",
                    actual=f"例外={len(errs_ipa)}, 試行記録={cnt_ipa}, 遮断成立={blk_flag_cnt_a}, 遮断中={blocked_ipa}",
                    passed=passed_ipa,
                )
            )

            # ------------------------------------------------------------
            # IP 遮断 ケース B: しきい値 × 2 同時実行
            # ------------------------------------------------------------
            ip_b = next_test_ip()
            LoginAttempt.objects.filter(ip_address=ip_b).delete()
            ActionLog.objects.filter(
                action=ActionLogAction.IP_BLOCK, data__ip_address=ip_b
            ).delete()
            threads_ipb = ip_th * 2
            ip_flags_b, errs_ipb = _run_concurrent_ip_failures(
                username_input=f"{username_prefix}ipb_{trial}",
                ip_address=ip_b,
                thread_count=threads_ipb,
                base_now=base_now,
            )
            cnt_ipb = LoginAttempt.objects.filter(ip_address=ip_b).count()
            blocked_ipb = is_ip_blocked(
                ip_b, setting=cfg, now=base_now + timedelta(seconds=1)
            )
            blk_flag_cnt_b = sum(1 for f in ip_flags_b if f)
            log_cnt_ipb = ActionLog.objects.filter(
                action=ActionLogAction.IP_BLOCK, data__ip_address=ip_b
            ).count()
            passed_ipb = (
                not errs_ipb
                and cnt_ipb == threads_ipb
                and blocked_ipb
                and blk_flag_cnt_b >= 1
            )
            results.append(
                CaseResult(
                    case_name="IP遮断 ケースB（しきい値×2 同時）",
                    trial=trial,
                    threads=threads_ipb,
                    expected=f"例外=0, 試行記録={threads_ipb}, 遮断中=True, ip_blocked_now>=1",
                    actual=f"例外={len(errs_ipb)}, 試行記録={cnt_ipb}, 遮断中={blocked_ipb}, now={blk_flag_cnt_b}/log={log_cnt_ipb}",
                    passed=passed_ipb,
                )
            )

            # ------------------------------------------------------------
            # IP 遮断 ケース C: しきい値-1 到達後に 2 同時実行
            # ------------------------------------------------------------
            ip_c = next_test_ip()
            LoginAttempt.objects.filter(ip_address=ip_c).delete()
            ActionLog.objects.filter(
                action=ActionLogAction.IP_BLOCK, data__ip_address=ip_c
            ).delete()
            from accounts.login_security.lockout import record_failure_and_evaluate

            for s in range(ip_th - 1):
                record_failure_and_evaluate(
                    username_input=f"{username_prefix}ipc_{trial}",
                    target_users=[],
                    primary_user=None,
                    ip_address=ip_c,
                    user_agent="ConcurrencyCheckScript/1.0",
                    result=LoginAttempt.Result.PASSWORD_FAILED,
                    now=base_now + timedelta(seconds=s),
                )
            ip_flags_c, errs_ipc = _run_concurrent_ip_failures(
                username_input=f"{username_prefix}ipc_{trial}",
                ip_address=ip_c,
                thread_count=2,
                base_now=base_now + timedelta(seconds=ip_th),
            )
            cnt_ipc = LoginAttempt.objects.filter(ip_address=ip_c).count()
            blocked_ipc = is_ip_blocked(
                ip_c, setting=cfg, now=base_now + timedelta(seconds=ip_th + 1)
            )
            blk_flag_cnt_c = sum(1 for f in ip_flags_c if f)
            log_cnt_ipc = ActionLog.objects.filter(
                action=ActionLogAction.IP_BLOCK, data__ip_address=ip_c
            ).count()
            passed_ipc = (
                not errs_ipc
                and cnt_ipc == ip_th + 1
                and blocked_ipc
                and blk_flag_cnt_c >= 1
            )
            results.append(
                CaseResult(
                    case_name="IP遮断 ケースC（直前状態から 2 同時）",
                    trial=trial,
                    threads=2,
                    expected=f"例外=0, 試行記録={ip_th + 1}, 遮断中=True, ip_blocked_now>=1",
                    actual=f"例外={len(errs_ipc)}, 試行記録={cnt_ipc}, 遮断中={blocked_ipc}, now={blk_flag_cnt_c}/log={log_cnt_ipc}",
                    passed=passed_ipc,
                )
            )
    finally:
        cleaned = cleanup_test_artifacts(username_prefix, used_ips)
        out.write(
            "\n後始末完了: "
            f"AccountLock={cleaned['account_lock']}件, "
            f"LoginAttempt={cleaned['login_attempt']}件, "
            f"ActionLog={cleaned['action_log']}件, "
            f"CustomUser={cleaned['custom_user']}件\n\n"
        )

    out.write("| ケース | 試行 | スレッド | 期待値 | 実測値 | 判定 |\n")
    out.write("|---|---|---|---|---|---|\n")
    for r in results:
        verdict = "PASS" if r.passed else "FAIL"
        out.write(
            f"| {r.case_name} | {r.trial} | {r.threads} | {r.expected} | {r.actual} | {verdict} |\n"
        )

    failed_count = sum(1 for r in results if not r.passed)
    out.write(
        f"\n結果合計: 全 {len(results)} 件中 {len(results) - failed_count} 件 PASS / {failed_count} 件 FAIL\n"
    )
    return 1 if failed_count > 0 else 0


def main(argv: list[str] | None = None) -> int:
    setup_django()
    guard_code, args = validate_execution_guard(argv)
    if guard_code is not None:
        return guard_code
    return run_concurrency_checks(repeat=args.repeat)


if __name__ == "__main__":
    raise SystemExit(main())
