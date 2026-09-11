"""Admission policy for MySQL maintenance beside realtime reasoning.

The policy is operational rather than investment meaning.  It keeps broad
history scans away from the realtime queue while guaranteeing that bounded
retention eventually runs even under sustained market activity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


def _bounded_int(
    settings: Mapping[str, object],
    key: str,
    fallback: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or fallback).strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class MySQLMaintenanceAdmission:
    run_cleanup: bool
    include_legacy: bool
    status: str
    reason: str
    next_interval_seconds: int
    deferral_started_at: float
    deferred_seconds: int

    def to_dict(self):
        return asdict(self)


def mysql_maintenance_admission(
    settings: Mapping[str, object],
    *,
    pending_count: int,
    now_epoch: float,
    deferral_started_at: float = 0.0,
    last_legacy_at: float = 0.0,
) -> MySQLMaintenanceAdmission:
    """Choose no cleanup, bounded cleanup, or the broader legacy pass."""

    busy_retry_seconds = _bounded_int(
        settings,
        "mysqlMaintenanceBusyRetrySeconds",
        60,
        30,
        300,
    )
    maximum_deferral_seconds = _bounded_int(
        settings,
        "mysqlMaintenanceMaxRealtimeDeferralSeconds",
        15 * 60,
        60,
        60 * 60,
    )
    legacy_interval_seconds = _bounded_int(
        settings,
        "mysqlLegacyRetentionIntervalSeconds",
        60 * 60,
        15 * 60,
        24 * 60 * 60,
    )
    now = max(0.0, float(now_epoch or 0.0))
    pending = max(0, int(pending_count or 0))
    if pending:
        started = float(deferral_started_at or now)
        deferred_seconds = max(0, int(now - started))
        if deferred_seconds < maximum_deferral_seconds:
            return MySQLMaintenanceAdmission(
                run_cleanup=False,
                include_legacy=False,
                status="realtime-queue-deferred",
                reason="실시간 추론 대기열을 보호하기 위해 MySQL 이력 정리를 잠시 연기했습니다.",
                next_interval_seconds=busy_retry_seconds,
                deferral_started_at=started,
                deferred_seconds=deferred_seconds,
            )
        return MySQLMaintenanceAdmission(
            run_cleanup=True,
            include_legacy=False,
            status="bounded-cleanup-after-max-deferral",
            reason="지속적인 실시간 부하 중에도 용량을 보호하도록 제한된 정리만 실행합니다.",
            next_interval_seconds=busy_retry_seconds,
            deferral_started_at=now,
            deferred_seconds=deferred_seconds,
        )

    include_legacy = not last_legacy_at or now - float(last_legacy_at) >= legacy_interval_seconds
    return MySQLMaintenanceAdmission(
        run_cleanup=True,
        include_legacy=include_legacy,
        status="idle-full-cleanup" if include_legacy else "idle-bounded-cleanup",
        reason=(
            "추론 대기열이 비어 광범위 이력 정리를 실행합니다."
            if include_legacy
            else "추론 대기열이 비어 제한된 이력 정리를 실행합니다."
        ),
        next_interval_seconds=busy_retry_seconds,
        deferral_started_at=0.0,
        deferred_seconds=0,
    )
