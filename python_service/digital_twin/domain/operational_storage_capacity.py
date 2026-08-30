"""Pure capacity-state and human-notification evaluation for local storage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Mapping

from .data_freshness import parse_datetime


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
ACTIVE_STATES = {"warning", "limited", "critical"}
STATE_ORDER = {"healthy": 0, "warning": 1, "limited": 2, "critical": 3}
COMPONENT_SPECS = (
    ("typedb", "typedbSizeMb", "typedbLimitMb"),
    ("mysql", "mysqlSizeMb", "mysqlLimitMb"),
    ("logs", "logSizeMb", "logLimitMb"),
)


def operational_storage_capacity_read_model(
    inventory: Mapping[str, object],
    observation: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Combine live inventory with the durable trend observation for operators."""

    current = dict(inventory or {})
    observed = dict(observation or {})
    reclaimable_mb = round(
        max(0.0, _number(current.get("mysqlReclaimableMb")))
        + max(
            0.0,
            _number(current.get("logSizeMb")) - _number(current.get("logLimitMb")),
        ),
        1,
    )
    cleanup_mode = str(current.get("cleanupMode") or observed.get("cleanupMode") or "normal")
    capacity_state = str(
        observed.get("capacityState")
        or observed.get("state")
        or current.get("mysqlCapacityStage")
        or "healthy"
    )
    return {
        "capacityState": capacity_state,
        "capacityObservedAt": str(observed.get("checkedAt") or ""),
        "forecast": {
            "available": int(observed.get("forecastSampleCount") or 0) >= 2,
            "detected": bool(observed.get("forecastDetected")),
            "sampleCount": int(observed.get("forecastSampleCount") or 0),
            "elapsedMinutes": observed.get("forecastElapsedMinutes"),
            "depletionRateMbPerMinute": _number(
                observed.get("forecastDepletionRateMbPerMinute")
            ),
            "etaMinutes": observed.get("forecastEtaMinutes"),
            "thresholdMb": _number(observed.get("forecastThresholdMb")),
            "projectedFreeMb": observed.get("forecastProjectedFreeMb"),
        },
        "cleanupPlan": {
            "mode": cleanup_mode,
            "automatic": cleanup_mode in {"accelerated", "emergency"},
            "canStart": reclaimable_mb > 0 or cleanup_mode != "normal",
            "estimatedReclaimableMb": reclaimable_mb,
            "mysqlReclaimableMb": max(0.0, _number(current.get("mysqlReclaimableMb"))),
            "protectedData": [
                "투자 결정과 판단 이력",
                "판단 후 결과와 체결 원장",
                "현재 가설 상태",
                "압축 전환 감사 이력",
            ],
            "cleanupTargets": [
                "만료된 대용량 처리 페이로드",
                "보존 기간이 지난 스냅샷과 시계열",
                "중복된 전달·추론 상세 이력",
            ],
        },
    }


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


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _max_state(current: str, candidate: str) -> str:
    return candidate if STATE_ORDER.get(candidate, 0) > STATE_ORDER.get(current, 0) else current


def _component_state(
    ratio: float,
    internal_percent: int,
    alert_percent: int,
    critical_percent: int,
) -> str:
    if ratio >= critical_percent / 100.0:
        return "critical"
    if ratio >= alert_percent / 100.0:
        return "limited"
    if ratio >= internal_percent / 100.0:
        return "warning"
    return "healthy"


def _enabled(settings: Mapping[str, object], key: str, fallback: bool = True) -> bool:
    raw = settings.get(key)
    if raw is None or str(raw).strip() == "":
        return fallback
    return str(raw).strip().lower() not in DISABLED_VALUES


def operational_storage_capacity_enabled(settings: Mapping[str, object] = None) -> bool:
    return _enabled(dict(settings or {}), "operationalStorageAlertEnabled")


def _recent_free_samples(
    previous: Mapping[str, object],
    current: datetime,
    free_mb: float,
    lookback_minutes: int,
) -> list[Dict[str, object]]:
    """Persist a small rolling observation window for a stable depletion forecast."""

    cutoff = _utc(current) - timedelta(minutes=lookback_minutes)
    candidates: Iterable[object] = list(previous.get("recentFreeSamples") or [])
    samples = []
    for value in candidates:
        if not isinstance(value, Mapping):
            continue
        observed = parse_datetime(value.get("at") or value.get("checkedAt"))
        if not observed:
            continue
        observed = _utc(observed)
        amount = _number(value.get("freeMb"), -1)
        if observed < cutoff or amount < 0:
            continue
        samples.append((observed, amount))

    # Existing records created before rolling samples were introduced still
    # supply one valid baseline as soon as the next observation arrives.
    if not samples:
        observed = parse_datetime(previous.get("checkedAt"))
        amount = _number(previous.get("freeMb"), -1)
        if observed and amount >= 0 and _utc(observed) >= cutoff:
            samples.append((_utc(observed), amount))

    samples.append((_utc(current), max(0.0, free_mb)))
    deduplicated = {}
    for observed, amount in samples:
        deduplicated[_iso(observed)] = amount
    return [
        {"at": observed, "freeMb": round(amount, 1)}
        for observed, amount in sorted(deduplicated.items())[-32:]
    ]


def _depletion_forecast(
    samples: list[Mapping[str, object]],
    threshold_mb: int,
    horizon_minutes: int,
    minimum_samples: int,
    minimum_elapsed_minutes: int,
) -> Dict[str, object]:
    """Estimate time to the protected reserve from the recent net change."""

    if len(samples) < minimum_samples:
        return {
            "available": False,
            "sampleCount": len(samples),
            "depletionRateMbPerMinute": 0.0,
            "etaMinutes": None,
            "projectedFreeMb": None,
            "detected": False,
        }
    first = samples[0]
    latest = samples[-1]
    first_at = parse_datetime(first.get("at"))
    latest_at = parse_datetime(latest.get("at"))
    if not first_at or not latest_at:
        return {
            "available": False,
            "sampleCount": len(samples),
            "depletionRateMbPerMinute": 0.0,
            "etaMinutes": None,
            "projectedFreeMb": None,
            "detected": False,
        }
    elapsed_minutes = max(0.0, (_utc(latest_at) - _utc(first_at)).total_seconds() / 60.0)
    if elapsed_minutes < minimum_elapsed_minutes:
        return {
            "available": False,
            "sampleCount": len(samples),
            "elapsedMinutes": round(elapsed_minutes, 1),
            "depletionRateMbPerMinute": 0.0,
            "etaMinutes": None,
            "projectedFreeMb": None,
            "detected": False,
        }
    first_free_mb = _number(first.get("freeMb"))
    latest_free_mb = _number(latest.get("freeMb"))
    rate = max(0.0, (first_free_mb - latest_free_mb) / elapsed_minutes)
    projected = latest_free_mb - rate * horizon_minutes
    eta = 0.0 if latest_free_mb <= threshold_mb else (
        (latest_free_mb - threshold_mb) / rate if rate > 0 else None
    )
    return {
        "available": True,
        "sampleCount": len(samples),
        "elapsedMinutes": round(elapsed_minutes, 1),
        "depletionRateMbPerMinute": round(rate, 2),
        "etaMinutes": round(eta, 1) if eta is not None else None,
        "projectedFreeMb": round(projected, 1),
        "detected": bool(rate > 0 and eta is not None and eta <= horizon_minutes),
    }


def _reminder_minutes(configured: Mapping[str, object], state: str) -> int:
    if state == "critical":
        return _integer(
            configured.get("operationalStorageCriticalAlertReminderMinutes"),
            60,
            5,
            7 * 24 * 60,
        )
    if state == "limited":
        return _integer(
            configured.get("operationalStorageLimitedAlertReminderMinutes"),
            240,
            15,
            7 * 24 * 60,
        )
    return _integer(
        configured.get("operationalStorageAlertReminderMinutes"),
        240,
        15,
        7 * 24 * 60,
    )


def _materially_worsened(
    previous: Mapping[str, object],
    state: str,
    free_mb: float,
    limiting_components: Iterable[Mapping[str, object]],
    percentage: int,
) -> bool:
    """Escalate a stable limited incident only after a meaningful deterioration."""

    if state != "limited":
        return False
    baseline_free_mb = _number(previous.get("lastAlertFreeMb"), -1)
    if baseline_free_mb > 0 and free_mb <= baseline_free_mb * (1 - percentage / 100.0):
        return True
    baseline_components = previous.get("lastAlertComponentSizes")
    if not isinstance(baseline_components, Mapping):
        return False
    for component in limiting_components:
        name = str(component.get("component") or "")
        previous_size = _number(baseline_components.get(name), -1)
        current_size = _number(component.get("currentMb"), -1)
        if previous_size > 0 and current_size >= previous_size * (1 + percentage / 100.0):
            return True
    return False


def _forecast_eta_materially_worsened(
    previous: Mapping[str, object],
    forecast: Mapping[str, object],
    percentage: int,
) -> bool:
    current_eta = forecast.get("etaMinutes")
    previous_eta = _number(previous.get("lastAlertForecastEtaMinutes"), -1)
    if current_eta is None or previous_eta <= 0:
        return False
    current_value = _number(current_eta, -1)
    minimum_drop = max(10.0, previous_eta * percentage / 100.0)
    return current_value >= 0 and previous_eta - current_value >= minimum_drop and current_value <= previous_eta * (1 - percentage / 100.0)


def evaluate_operational_storage_capacity(
    snapshot: Mapping[str, object],
    previous: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
    now: datetime = None,
) -> Dict[str, object]:
    """Classify storage and decide whether a human needs to be paged.

    Internal cleanup begins early, while notifications require an actionable
    reserve breach, near-term depletion forecast, or a near-full component.
    """

    configured = dict(settings or {})
    current = _utc(now or datetime.now(timezone.utc))
    prior = dict(previous or {})
    values = dict(snapshot or {})

    cleanup_free_mb = _integer(
        configured.get("operationalStorageWarningFreeSpaceMb"),
        48 * 1024,
        1024,
    )
    alert_free_mb = min(
        cleanup_free_mb,
        _integer(configured.get("operationalStorageAlertFreeSpaceMb"), 24 * 1024, 512),
    )
    minimum_free_mb = min(
        alert_free_mb,
        _integer(configured.get("operationalMinimumFreeSpaceMb"), 12 * 1024, 512),
    )
    critical_free_mb = min(
        minimum_free_mb,
        _integer(configured.get("operationalCriticalFreeSpaceMb"), 6 * 1024, 256),
    )
    component_warning_percent = _integer(
        configured.get("operationalStorageComponentWarningPercent"),
        80,
        50,
        99,
    )
    component_cleanup_percent = min(
        component_warning_percent,
        _integer(
            configured.get("operationalStorageComponentCleanupPercent"),
            70,
            50,
            99,
        ),
    )
    component_alert_percent = max(
        component_warning_percent,
        _integer(configured.get("operationalStorageComponentAlertPercent"), 90, 50, 99),
    )
    component_critical_percent = max(
        component_alert_percent,
        _integer(configured.get("operationalStorageComponentCriticalPercent"), 95, 50, 100),
    )
    forecast_enabled = _enabled(configured, "operationalStorageForecastEnabled")
    forecast_horizon_minutes = _integer(
        configured.get("operationalStorageForecastHorizonMinutes"), 60, 5, 24 * 60
    )
    forecast_threshold_mb = min(
        minimum_free_mb,
        _integer(
            configured.get("operationalStorageForecastThresholdMb"), minimum_free_mb, 256
        ),
    )
    forecast_lookback_minutes = _integer(
        configured.get("operationalStorageForecastLookbackMinutes"), 30, 5, 24 * 60
    )
    forecast_minimum_samples = _integer(
        configured.get("operationalStorageForecastMinimumSamples"), 3, 2, 32
    )
    forecast_minimum_elapsed_minutes = _integer(
        configured.get("operationalStorageForecastMinimumElapsedMinutes"), 5, 1, 120
    )
    material_worsening_percent = _integer(
        configured.get("operationalStorageMaterialWorseningPercent"), 25, 5, 90
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
            "alertThresholdReached": True,
        })
    elif free_mb < minimum_free_mb:
        state = "limited"
        limiting_components.append({
            "component": "sharedDisk",
            "state": "limited",
            "currentMb": round(free_mb, 1),
            "limitMb": minimum_free_mb,
            "direction": "minimum-free",
            "alertThresholdReached": True,
        })
    elif free_mb < cleanup_free_mb:
        state = "warning"
        limiting_components.append({
            "component": "sharedDisk",
            "state": "warning",
            "currentMb": round(free_mb, 1),
            "limitMb": cleanup_free_mb,
            "direction": "minimum-free",
            "alertThresholdReached": free_mb < alert_free_mb,
        })

    for component, size_key, limit_key in COMPONENT_SPECS:
        size_mb = _number(values.get(size_key))
        limit_mb = _number(values.get(limit_key))
        if limit_mb <= 0:
            continue
        ratio = size_mb / limit_mb
        component_state = _component_state(
            ratio,
            component_warning_percent,
            component_alert_percent,
            component_critical_percent,
        )
        state = _max_state(state, component_state)
        if component_state != "healthy":
            limiting_components.append({
                "component": component,
                "state": component_state,
                "currentMb": round(size_mb, 1),
                "limitMb": round(limit_mb, 1),
                "ratioPercent": round(ratio * 100, 1),
                "direction": "maximum-size",
                "alertThresholdReached": ratio >= component_alert_percent / 100.0,
            })

    recent_samples = _recent_free_samples(prior, current, free_mb, forecast_lookback_minutes)
    forecast = _depletion_forecast(
        recent_samples,
        forecast_threshold_mb,
        forecast_horizon_minutes,
        forecast_minimum_samples,
        forecast_minimum_elapsed_minutes,
    )
    forecast_detected = bool(forecast.get("detected")) and forecast_enabled
    disk_alert_reached = free_mb < alert_free_mb
    component_alert_reached = any(bool(item.get("alertThresholdReached")) for item in limiting_components)
    alert_eligible = bool(
        state in {"limited", "critical"}
        or disk_alert_reached
        or component_alert_reached
        or forecast_detected
    )
    previous_alert_eligible = bool(prior.get("alertEligible"))
    previous_state = str(prior.get("state") or "healthy").strip().lower()
    if previous_state not in STATE_ORDER:
        previous_state = "healthy"
    changed = state != previous_state
    worsened_state = STATE_ORDER.get(state, 0) > STATE_ORDER.get(previous_state, 0)
    recovered = not alert_eligible and previous_alert_eligible
    reminder_minutes = _reminder_minutes(configured, state)
    last_alert = parse_datetime(prior.get("lastAlertAt"))
    reminder_due = bool(
        alert_eligible
        and last_alert
        and (current - _utc(last_alert)).total_seconds() >= reminder_minutes * 60
    )
    materially_worsened = _materially_worsened(
        prior,
        state,
        free_mb,
        limiting_components,
        material_worsening_percent,
    )
    forecast_eta_worsened = forecast_detected and _forecast_eta_materially_worsened(
        prior,
        forecast,
        material_worsening_percent,
    )

    alert_kind = ""
    if recovered:
        alert_kind = "recovered"
    elif alert_eligible and not previous_alert_eligible:
        alert_kind = "forecast" if forecast_detected and not (disk_alert_reached or component_alert_reached) else "threshold-crossed"
    elif alert_eligible and worsened_state:
        alert_kind = "state-changed"
    elif materially_worsened:
        alert_kind = "material-worsening"
    elif forecast_eta_worsened:
        alert_kind = "forecast-eta-worsened"
    elif reminder_due:
        alert_kind = "reminder"

    component_names = {str(item.get("component") or "") for item in limiting_components}
    suggested_action = "정상 보존 정책으로 운영 중입니다."
    if bool(values.get("coreWritesOnly")):
        suggested_action = "핵심 투자 이력만 기록하고 재생성 가능한 분석 데이터의 수집·저장을 중단하세요."
    elif state == "critical":
        suggested_action = "비필수 수집·분석·그래프 쓰기를 보류하고 저장공간을 즉시 확보하세요."
    elif forecast_detected:
        eta = forecast.get("etaMinutes")
        eta_text = "" if eta is None else " 약 " + str(eta) + "분 후"
        suggested_action = "현재 감소 속도면" + eta_text + " 보호 여유 공간에 도달합니다. 큰 구성요소를 우선 회수하세요."
    elif "typedb" in component_names:
        suggested_action = "TypeDB 안전 재구축을 준비해 WAL·체크포인트와 비활성 그래프 세대를 회수하세요."
    elif "mysql" in component_names:
        suggested_action = "MySQL 이력 정리를 가속하고, 안전한 물리 압축 후보를 확인하세요."
    elif alert_eligible:
        suggested_action = "이력 정리를 가속하고 용량이 큰 구성요소를 우선 회수하세요."
    elif state == "warning":
        suggested_action = "내부 이력 정리를 가속해 여유 공간을 회복합니다."

    observed_at = _iso(current)
    result = {
        "state": state,
        "previousState": previous_state,
        "checkedAt": observed_at,
        "freeMb": round(free_mb, 1),
        "freePercent": round(_number(values.get("freePercent")), 2),
        "warningFreeMb": cleanup_free_mb,
        "alertFreeMb": alert_free_mb,
        "minimumFreeMb": minimum_free_mb,
        "criticalFreeMb": critical_free_mb,
        "typedbSizeMb": round(_number(values.get("typedbSizeMb")), 1),
        "typedbApparentSizeMb": round(_number(values.get("typedbApparentSizeMb")), 1),
        "typedbSharedLinkedMb": round(_number(values.get("typedbSharedLinkedMb")), 1),
        "typedbLimitMb": round(_number(values.get("typedbLimitMb")), 1),
        "typedbWalMb": round(_number(values.get("typedbWalMb")), 1),
        "typedbCheckpointMb": round(_number(values.get("typedbCheckpointMb")), 1),
        "typedbCheckpointReferencedMb": round(_number(values.get("typedbCheckpointReferencedMb")), 1),
        "mysqlSizeMb": round(_number(values.get("mysqlSizeMb")), 1),
        "mysqlLimitMb": round(_number(values.get("mysqlLimitMb")), 1),
        "mysqlUsagePercent": round(
            _number(values.get("mysqlUsagePercent"))
            or (
                _number(values.get("mysqlSizeMb"))
                / _number(values.get("mysqlLimitMb"))
                * 100
                if _number(values.get("mysqlLimitMb")) > 0
                else 0
            ),
            1,
        ),
        "mysqlCapacityStage": str(values.get("mysqlCapacityStage") or "normal"),
        "mysqlMetadataStatus": str(values.get("mysqlMetadataStatus") or "unavailable"),
        "mysqlMetadataReason": str(values.get("mysqlMetadataReason") or "")[:180],
        "mysqlLiveDataMb": round(_number(values.get("mysqlLiveDataMb")), 1),
        "mysqlReclaimableMb": round(_number(values.get("mysqlReclaimableMb")), 1),
        "mysqlAllocatedTableMb": round(_number(values.get("mysqlAllocatedTableMb")), 1),
        "mysqlHardLimitReached": bool(values.get("mysqlHardLimitReached")),
        "coreWritesOnly": bool(values.get("coreWritesOnly")),
        "logSizeMb": round(_number(values.get("logSizeMb")), 1),
        "logLimitMb": round(_number(values.get("logLimitMb")), 1),
        "componentCleanupPercent": component_cleanup_percent,
        "componentWarningPercent": component_warning_percent,
        "componentAlertPercent": component_alert_percent,
        "componentCriticalPercent": component_critical_percent,
        "capacityLimitReached": state in {"limited", "critical"},
        "nonEssentialWritesAllowed": bool(values.get("nonEssentialWritesAllowed", True))
        and state not in {"limited", "critical"},
        "cleanupMode": str(values.get("cleanupMode") or "normal"),
        "limitingComponents": limiting_components,
        "reason": str(values.get("reason") or ""),
        "suggestedAction": suggested_action,
        "maintenanceFailureReason": str(values.get("maintenanceFailureReason") or "")[:300],
        "alertEligible": alert_eligible,
        "alertRequired": bool(alert_kind) and operational_storage_capacity_enabled(configured),
        "alertKind": alert_kind,
        "alertReminderMinutes": reminder_minutes,
        "materialWorseningPercent": material_worsening_percent,
        "recentFreeSamples": recent_samples,
        "forecastEnabled": forecast_enabled,
        "forecastThresholdMb": forecast_threshold_mb,
        "forecastHorizonMinutes": forecast_horizon_minutes,
        "forecastDetected": forecast_detected,
        "forecastSampleCount": forecast.get("sampleCount"),
        "forecastElapsedMinutes": forecast.get("elapsedMinutes"),
        "forecastDepletionRateMbPerMinute": forecast.get("depletionRateMbPerMinute"),
        "forecastEtaMinutes": forecast.get("etaMinutes"),
        "forecastProjectedFreeMb": forecast.get("projectedFreeMb"),
    }
    if alert_kind:
        result["lastAlertAt"] = observed_at
        result["lastAlertFreeMb"] = round(free_mb, 1)
        result["lastAlertComponentSizes"] = {
            str(item.get("component") or ""): _number(item.get("currentMb"))
            for item in limiting_components
            if str(item.get("component") or "")
        }
        if forecast.get("etaMinutes") is not None:
            result["lastAlertForecastEtaMinutes"] = forecast.get("etaMinutes")
    else:
        for key in (
            "lastAlertAt",
            "lastAlertFreeMb",
            "lastAlertComponentSizes",
            "lastAlertForecastEtaMinutes",
            "lastRuntimeFailureAlertAt",
            "lastForcedCapacityAlertAt",
        ):
            if key in prior:
                result[key] = prior[key]
    if recovered:
        result["recoveredFromState"] = previous_state
        result["incidentStartedAt"] = str(prior.get("stateSince") or prior.get("checkedAt") or "")
    if changed:
        result["stateSince"] = observed_at
    elif prior.get("stateSince"):
        result["stateSince"] = str(prior.get("stateSince"))
    return result
