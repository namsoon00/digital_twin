"""Pure capacity-state evaluation for local operational storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Mapping

from .data_freshness import parse_datetime


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
ACTIVE_STATES = {"warning", "limited", "critical"}
STATE_ORDER = {"healthy": 0, "warning": 1, "limited": 2, "critical": 3}


def _integer(value: object, fallback: int, minimum: int = 0, maximum: int = 1024 * 1024) -> int:
    try:
        parsed = int(float(str(value if value is not None else "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _iso(value: datetime) -> str:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_from_ratio(ratio: float, warning_percent: int) -> str:
    if ratio >= 1.5:
        return "critical"
    if ratio >= 1.0:
        return "limited"
    if ratio >= max(1, warning_percent) / 100.0:
        return "warning"
    return "healthy"


def _max_state(current: str, candidate: str) -> str:
    return candidate if STATE_ORDER.get(candidate, 0) > STATE_ORDER.get(current, 0) else current


def operational_storage_capacity_enabled(settings: Mapping[str, object] = None) -> bool:
    value = str((settings or {}).get("operationalStorageAlertEnabled", "1")).strip().lower()
    return value not in DISABLED_VALUES


def evaluate_operational_storage_capacity(
    snapshot: Mapping[str, object],
    previous: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
    now: datetime = None,
) -> Dict[str, object]:
    """Classify capacity without performing I/O or sending notifications."""

    configured = dict(settings or {})
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    prior = dict(previous or {})
    values = dict(snapshot or {})

    warning_free_mb = _integer(
        configured.get("operationalStorageWarningFreeSpaceMb"),
        48 * 1024,
        1024,
    )
    minimum_free_mb = _integer(
        configured.get("operationalMinimumFreeSpaceMb"),
        32 * 1024,
        1024,
    )
    warning_free_mb = max(warning_free_mb, minimum_free_mb)
    critical_free_mb = min(
        minimum_free_mb,
        _integer(configured.get("operationalCriticalFreeSpaceMb"), 20 * 1024, 512),
    )
    component_warning_percent = _integer(
        configured.get("operationalStorageComponentWarningPercent"),
        80,
        50,
        99,
    )

    state = "healthy"
    limiting_components = []
    free_mb = _number(values.get("freeMb"))
    if free_mb < critical_free_mb:
        state = "critical"
        limiting_components.append({
            "component": "sharedDisk",
            "state": "critical",
            "currentMb": round(free_mb, 1),
            "limitMb": critical_free_mb,
            "direction": "minimum-free",
        })
    elif free_mb < minimum_free_mb:
        state = "limited"
        limiting_components.append({
            "component": "sharedDisk",
            "state": "limited",
            "currentMb": round(free_mb, 1),
            "limitMb": minimum_free_mb,
            "direction": "minimum-free",
        })
    elif free_mb < warning_free_mb:
        state = "warning"
        limiting_components.append({
            "component": "sharedDisk",
            "state": "warning",
            "currentMb": round(free_mb, 1),
            "limitMb": warning_free_mb,
            "direction": "minimum-free",
        })

    component_specs = (
        ("typedb", "typedbSizeMb", "typedbLimitMb"),
        ("mysql", "mysqlSizeMb", "mysqlLimitMb"),
        ("logs", "logSizeMb", "logLimitMb"),
    )
    for component, size_key, limit_key in component_specs:
        size_mb = _number(values.get(size_key))
        limit_mb = _number(values.get(limit_key))
        if limit_mb <= 0:
            continue
        component_state = _state_from_ratio(size_mb / limit_mb, component_warning_percent)
        state = _max_state(state, component_state)
        if component_state != "healthy":
            limiting_components.append({
                "component": component,
                "state": component_state,
                "currentMb": round(size_mb, 1),
                "limitMb": round(limit_mb, 1),
                "ratioPercent": round(size_mb / limit_mb * 100, 1),
                "direction": "maximum-size",
            })

    previous_state = str(prior.get("state") or "healthy").strip().lower()
    if previous_state not in STATE_ORDER:
        previous_state = "healthy"
    reminder_minutes = _integer(
        configured.get("operationalStorageAlertReminderMinutes"),
        60,
        5,
        7 * 24 * 60,
    )
    last_alert = parse_datetime(prior.get("lastAlertAt"))
    reminder_due = bool(
        state in ACTIVE_STATES
        and last_alert
        and (current.astimezone(timezone.utc) - last_alert.astimezone(timezone.utc)).total_seconds() >= reminder_minutes * 60
    )
    changed = state != previous_state
    recovered = state == "healthy" and previous_state in ACTIVE_STATES
    alert_kind = ""
    if changed and (state in ACTIVE_STATES or recovered):
        alert_kind = "recovered" if recovered else "state-changed"
    elif reminder_due:
        alert_kind = "reminder"

    suggested_action = "정상 보존 정책으로 운영 중입니다."
    component_names = {str(item.get("component") or "") for item in limiting_components}
    if "typedb" in component_names:
        suggested_action = "TypeDB 안전 재구축을 실행해 WAL·체크포인트와 비활성 그래프 세대를 회수하세요."
    elif "mysql" in component_names:
        suggested_action = "MySQL 이력 정리를 가속하고, 안전한 물리 압축 후보를 확인하세요."
    elif state == "critical":
        suggested_action = "비필수 수집·분석·그래프 쓰기를 보류하고 저장공간을 즉시 확보하세요."
    elif state in ACTIVE_STATES:
        suggested_action = "이력 정리를 가속하고 용량이 큰 구성요소를 우선 회수하세요."

    observed_at = _iso(current)
    result = {
        "state": state,
        "previousState": previous_state,
        "checkedAt": observed_at,
        "freeMb": round(free_mb, 1),
        "freePercent": round(_number(values.get("freePercent")), 2),
        "warningFreeMb": warning_free_mb,
        "minimumFreeMb": minimum_free_mb,
        "criticalFreeMb": critical_free_mb,
        "typedbSizeMb": round(_number(values.get("typedbSizeMb")), 1),
        "typedbLimitMb": round(_number(values.get("typedbLimitMb")), 1),
        "typedbWalMb": round(_number(values.get("typedbWalMb")), 1),
        "typedbCheckpointMb": round(_number(values.get("typedbCheckpointMb")), 1),
        "mysqlSizeMb": round(_number(values.get("mysqlSizeMb")), 1),
        "mysqlLimitMb": round(_number(values.get("mysqlLimitMb")), 1),
        "logSizeMb": round(_number(values.get("logSizeMb")), 1),
        "logLimitMb": round(_number(values.get("logLimitMb")), 1),
        "capacityLimitReached": state in {"limited", "critical"},
        "nonEssentialWritesAllowed": bool(values.get("nonEssentialWritesAllowed", state not in {"limited", "critical"})),
        "cleanupMode": str(values.get("cleanupMode") or "normal"),
        "limitingComponents": limiting_components,
        "reason": str(values.get("reason") or ""),
        "suggestedAction": suggested_action,
        "alertRequired": bool(alert_kind) and operational_storage_capacity_enabled(configured),
        "alertKind": alert_kind,
        "alertReminderMinutes": reminder_minutes,
    }
    if alert_kind:
        result["lastAlertAt"] = observed_at
    elif prior.get("lastAlertAt"):
        result["lastAlertAt"] = str(prior.get("lastAlertAt"))
    if recovered:
        result["recoveredFromState"] = previous_state
        result["incidentStartedAt"] = str(prior.get("stateSince") or prior.get("checkedAt") or "")
    if changed:
        result["stateSince"] = observed_at
    elif prior.get("stateSince"):
        result["stateSince"] = str(prior.get("stateSince"))
    return result
