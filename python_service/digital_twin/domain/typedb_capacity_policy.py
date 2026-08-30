"""Pure TypeDB capacity-pressure policy for the ontology runtime.

TypeDB is a rebuildable active graph projection, while MySQL retains the
verified source snapshots and operational history.  This policy therefore
protects the filesystem before a TypeDB WAL/checkpoint expansion can turn an
otherwise recoverable projection backlog into an ENOSPC outage.
"""

from __future__ import annotations

from typing import Dict, Mapping


TYPEDB_CAPACITY_POLICY_VERSION = "typedb-capacity-pressure-v1"


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return fallback


def _integer(
    settings: Mapping[str, object],
    key: str,
    fallback: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def _role(value: object) -> str:
    normalized = str(value or "reasoning").strip().lower().replace("_", "-")
    return normalized or "reasoning"


def evaluate_typedb_capacity_policy(
    snapshot: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
    role: object = "reasoning",
    disk_health: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Choose a bounded worker action from direct TypeDB capacity evidence.

    At the warning threshold, only live reasoning may continue while the
    background writers yield and ABox cleanup gets one priority turn.  At the
    rotation threshold all ordinary TypeDB writers stop; the supervisor can
    then rebuild the active graph from its durable MySQL source without a
    filesystem-full race.
    """

    values = dict(snapshot or {})
    configured = dict(settings or {})
    operation_role = _role(role)
    size_mb = max(0.0, _number(values.get("typedbSizeMb")))
    limit_mb = max(0.0, _number(values.get("typedbLimitMb")))
    usage_percent = round(size_mb / limit_mb * 100.0, 1) if limit_mb > 0 else 0.0
    throttle_percent = _integer(
        configured,
        "typedbCapacityThrottlePercent",
        70,
        50,
        99,
    )
    rotation_percent = max(
        throttle_percent,
        _integer(
            configured,
            "typedbCapacityAutoRotatePercent",
            75,
            50,
            100,
        ),
    )
    critical_percent = max(
        rotation_percent,
        _integer(
            configured,
            "typedbCapacityCriticalPercent",
            90,
            50,
            100,
        ),
    )
    disk = dict(disk_health or {})
    disk_ready = bool(disk.get("ready", True))
    disk_reason = str(disk.get("reason") or "").strip()
    maintenance_role = operation_role in {"maintenance", "cleanup"}
    background_role = operation_role in {
        "projection",
        "world-projection",
        "inference-detail",
        "detail",
    }

    mode = "normal"
    ready = True
    normal_writes_allowed = True
    non_essential_writes_allowed = True
    cleanup_writes_allowed = True
    capacity_priority = False
    bypass_reasoning_deferral = False
    rotation_required = False
    reason = ""

    if not disk_ready:
        mode = "blocked-low-disk"
        ready = False
        normal_writes_allowed = False
        non_essential_writes_allowed = False
        cleanup_writes_allowed = False
        reason = disk_reason or "TypeDB 쓰기를 보류합니다. 공용 디스크 보호 여유가 부족합니다."
    elif limit_mb > 0 and usage_percent >= rotation_percent:
        mode = "rotation-required"
        ready = False
        normal_writes_allowed = False
        non_essential_writes_allowed = False
        cleanup_writes_allowed = False
        rotation_required = True
        reason = (
            "TypeDB 사용량 " + str(usage_percent) + "%가 안전 재구축 기준 "
            + str(rotation_percent) + "%에 도달했습니다. MySQL 원천 데이터로 재구축하는 동안 그래프 쓰기를 보류합니다."
        )
    elif limit_mb > 0 and usage_percent >= throttle_percent:
        mode = "write-throttled"
        normal_writes_allowed = True
        non_essential_writes_allowed = False
        capacity_priority = True
        bypass_reasoning_deferral = maintenance_role
        # A live reasoning turn may be needed to preserve the account-level
        # decision boundary. Background materialization and maintenance work can
        # always catch up from their durable outboxes after capacity recovers.
        ready = not background_role
        if maintenance_role:
            ready = True
        reason = (
            "TypeDB 사용량 " + str(usage_percent) + "%가 쓰기 완화 기준 "
            + str(throttle_percent) + "%에 도달했습니다. 라이브 추론과 ABox 정리를 우선하고 배경 그래프 쓰기를 보류합니다."
        )
    return {
        "version": TYPEDB_CAPACITY_POLICY_VERSION,
        "role": operation_role,
        "ready": ready,
        "mode": mode,
        "status": mode,
        "reason": reason,
        "typedbSizeMb": round(size_mb, 1),
        "typedbLimitMb": round(limit_mb, 1),
        "typedbUsagePercent": usage_percent,
        "throttlePercent": throttle_percent,
        "rotationPercent": rotation_percent,
        "criticalPercent": critical_percent,
        "criticalReached": bool(limit_mb > 0 and usage_percent >= critical_percent),
        "normalWritesAllowed": normal_writes_allowed,
        "nonEssentialWritesAllowed": non_essential_writes_allowed,
        "cleanupWritesAllowed": cleanup_writes_allowed,
        "capacityPriority": capacity_priority,
        "bypassReasoningDeferral": bypass_reasoning_deferral,
        "rotationRequired": rotation_required,
        "diskReady": disk_ready,
        "diskStatus": str(disk.get("status") or "ready"),
        "diskReason": disk_reason,
    }
