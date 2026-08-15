"""Operational telemetry contracts for the TypeDB ontology runtime.

This module intentionally observes projection and native inference work after
it has happened.  It never evaluates an investment rule or changes a TypeDB
decision.  MySQL keeps these audit samples; TypeDB remains the compact active
world and inference store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping


ONTOLOGY_RUNTIME_OBSERVATION_VERSION = "ontology-runtime-observation-v1"
NATIVE_RULE_TIMING_PROFILE_VERSION = "typedb-native-rule-timing-v1"
NATIVE_RULE_ADAPTIVE_TARGET_SHARDING_PROFILE_VERSION = "typedb-native-rule-adaptive-target-sharding-v1"
NATIVE_REPLAY_VALIDATION_VERSION = "typedb-native-replay-validation-v1"
NATIVE_RULE_FAILURE_DIAGNOSTIC_VERSION = "typedb-native-rule-failure-v1"
SCOPED_ABOX_MAINTENANCE_POLICY_VERSION = "typedb-scoped-abox-maintenance-policy-v4"
BACKGROUND_WORK_FAIRNESS_POLICY_VERSION = "ontology-background-work-fairness-v1"
SCOPED_ABOX_MAINTENANCE_YIELD_POLICY_VERSION = "typedb-scoped-abox-maintenance-yield-v1"
DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return fallback


def _integer(value: object, fallback: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return fallback


def _setting_number(
    settings: Mapping[str, object],
    key: str,
    fallback: float,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = (settings or {}).get(key)
    value = fallback if raw_value in (None, "") else _number(raw_value, fallback)
    return max(minimum, min(maximum, value))


def _timestamp(value: object) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _elapsed_seconds(value: object, now: datetime) -> int:
    parsed = _timestamp(value)
    if not parsed:
        return 0
    return max(0, int((now - parsed).total_seconds()))


def bounded_background_work_fairness(
    *,
    reasoning_pending_count: object,
    active_reasoning_count: object,
    background_work_pending: bool,
    oldest_background_work_at: object,
    last_fairness_at: object = "",
    max_deferral_seconds: object = 600,
    fairness_cooldown_seconds: object = 300,
    now: datetime = None,
) -> Dict[str, object]:
    """Decide whether an aged background task may use one bounded turn.

    Live investment reasoning retains normal priority. A background task is
    considered only when the durable queue proves that no reasoning lease is
    running. This prevents background work from starving forever under a
    continuously non-empty mailbox without permitting concurrent TypeDB
    writers.
    """

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    pending = max(0, _integer(reasoning_pending_count))
    active_known = active_reasoning_count not in (None, "")
    active = max(0, _integer(active_reasoning_count)) if active_known else 0
    maximum = max(15, min(24 * 60 * 60, _integer(max_deferral_seconds, 600)))
    cooldown = max(10, min(60 * 60, _integer(fairness_cooldown_seconds, 300)))
    oldest = _text(oldest_background_work_at)
    last_grant = _text(last_fairness_at)
    wait_seconds = _elapsed_seconds(oldest, current)
    last_grant_age = _elapsed_seconds(last_grant, current)
    cooldown_remaining = max(0, cooldown - last_grant_age) if last_grant else 0
    checked_at = current.isoformat().replace("+00:00", "Z")

    result = {
        "version": BACKGROUND_WORK_FAIRNESS_POLICY_VERSION,
        "checkedAt": checked_at,
        "reasoningPendingCount": pending,
        "activeReasoningKnown": active_known,
        "activeReasoningCount": active if active_known else None,
        "backgroundWorkPending": bool(background_work_pending),
        "oldestBackgroundWorkAt": oldest,
        "backgroundWaitSeconds": wait_seconds,
        "lastFairnessAt": last_grant,
        "lastFairnessAgeSeconds": last_grant_age if last_grant else None,
        "maxDeferralSeconds": maximum,
        "fairnessCooldownSeconds": cooldown,
        "cooldownRemainingSeconds": cooldown_remaining,
        "deferred": False,
        "fairnessGranted": False,
        "reasonCode": "reasoning-idle",
        "reason": "라이브 추론 대기열이 비어 있어 배경 작업을 실행할 수 있습니다.",
    }
    if not background_work_pending:
        result.update({
            "reasonCode": "background-idle",
            "reason": "처리할 배경 작업이 없습니다.",
        })
        return result
    if pending <= 0:
        return result
    if not active_known:
        result.update({
            "deferred": True,
            "reasonCode": "active-lease-unknown",
            "reason": "라이브 추론 lease 상태를 확인할 수 없어 배경 작업을 유예합니다.",
        })
        return result
    if active > 0:
        result.update({
            "deferred": True,
            "reasonCode": "active-reasoning-lease",
            "reason": "실행 중인 라이브 추론 lease가 있어 배경 작업을 유예합니다.",
        })
        return result
    if not oldest:
        result.update({
            "deferred": True,
            "reasonCode": "background-age-unknown",
            "reason": "배경 작업의 대기 시작 시각이 없어 공정 실행을 아직 허용하지 않습니다.",
        })
        return result
    if wait_seconds < maximum:
        result.update({
            "deferred": True,
            "reasonCode": "background-within-deferral-budget",
            "reason": "라이브 추론 우선 기간 안이어서 배경 작업을 유예합니다.",
        })
        return result
    if cooldown_remaining > 0:
        result.update({
            "deferred": True,
            "reasonCode": "fairness-cooldown",
            "reason": "직전 공정 실행 뒤 최소 간격이 남아 있어 배경 작업을 유예합니다.",
        })
        return result
    result.update({
        "fairnessGranted": True,
        "reasonCode": "aged-background-turn",
        "reason": "실행 중인 라이브 추론 없이 배경 작업 대기 시간이 한도를 넘어 제한된 공정 실행을 허용합니다.",
    })
    return result


def runtime_slo_policy(settings: Mapping[str, object] = None) -> Dict[str, object]:
    """Return operational, configurable service objectives.

    Defaults are intentionally lenient for a local TypeDB instance.  They
    flag sustained runtime degradation without turning a temporary slow graph
    operation into an investment alert.
    """

    configured = settings or {}
    return {
        "projectionSloMs": int(_setting_number(
            configured,
            "ontologyRuntimeProjectionSloSeconds",
            30,
            5,
            1800,
        ) * 1000),
        "inferenceSloMs": int(_setting_number(
            configured,
            "ontologyRuntimeInferenceSloSeconds",
            30,
            5,
            1800,
        ) * 1000),
        "consecutiveBreachCount": _integer(_setting_number(
            configured,
            "ontologyRuntimeSloConsecutiveBreachCount",
            3,
            1,
            50,
        )),
        "auditWindowRuns": _integer(_setting_number(
            configured,
            "ontologyRuntimeAuditWindowRuns",
            40,
            5,
            500,
        )),
    }


def scoped_abox_maintenance_policy(settings: Mapping[str, object] = None) -> Dict[str, object]:
    """Return bounded retention policy for immutable scoped ABox manifests.

    These are operational limits, not RuleBox thresholds.  They control how
    quickly obsolete immutable manifest generations are reclaimed after their
    active replacement has passed native inference verification.
    """

    configured = settings or {}
    warning_count = _integer(_setting_number(
        configured,
        "ontologyAboxMaintenanceWarningInactiveManifestCount",
        8,
        1,
        20000,
    ))
    critical_count = max(
        warning_count + 1,
        _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceCriticalInactiveManifestCount",
            24,
            2,
            50000,
        )),
    )
    max_delete_batches = _integer(_setting_number(
        configured,
        "ontologyAboxMaintenanceMaxDeleteBatchesPerRun",
        2,
        1,
        50,
    ))
    adaptive_enabled = _text(
        configured.get("ontologyAboxMaintenanceAdaptiveDrainEnabled")
    ).lower() not in DISABLED_VALUES
    return {
        "version": SCOPED_ABOX_MAINTENANCE_POLICY_VERSION,
        "intervalSeconds": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceIntervalSeconds",
            60,
            15,
            3600,
        )),
        "maxManifestsPerRun": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceMaxManifestsPerRun",
            2,
            1,
            10,
        )),
        "maxDeleteBatchesPerRun": max_delete_batches,
        "deleteBatchSize": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceDeleteBatchSize",
            150,
            10,
            500,
        )),
        "sliceSeconds": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceSliceSeconds",
            45,
            10,
            300,
        )),
        "keepInactiveManifestCount": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceKeepInactiveManifestCount",
            1,
            0,
            5,
        )),
        "warningInactiveManifestCount": warning_count,
        "criticalInactiveManifestCount": critical_count,
        # The maintenance runner reads the lightweight Manifest inventory
        # before it acquires the writer lease.  Once a world has this many
        # retired generations, it takes precedence over round-robin order so
        # a busy portfolio cannot keep growing while quieter worlds are
        # inspected first.
        "priorityInactiveManifestCount": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenancePriorityInactiveManifestCount",
            8,
            1,
            50000,
        )),
        # A prolonged critical backlog can receive a modestly larger physical
        # delete budget only after confirmed lease-owning cleanup passes.
        # This remains an operational retention control, never an investment
        # RuleBox threshold.
        "adaptiveDrainEnabled": adaptive_enabled,
        "adaptiveDrainMaxDeleteBatchesPerRun": max(
            max_delete_batches,
            _integer(_setting_number(
                configured,
                "ontologyAboxMaintenanceAdaptiveDrainMaxDeleteBatchesPerRun",
                4,
                1,
                50,
            )),
        ),
        "adaptiveDrainCriticalRunsBeforeIncrease": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceAdaptiveDrainCriticalRunsBeforeIncrease",
            2,
            1,
            20,
        )),
        "adaptiveDrainBacklogGrowthRunsBeforeIncrease": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceAdaptiveDrainBacklogGrowthRunsBeforeIncrease",
            2,
            1,
            20,
        )),
        "adaptiveDrainMaxConsecutiveWorldRuns": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceAdaptiveDrainMaxConsecutiveWorldRuns",
            3,
            1,
            20,
        )),
    }


def scoped_abox_maintenance_yield_policy(
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Return the bounded hand-off policy between reasoning and ABox retention.

    This is deliberately an operational scheduling policy, not a RuleBox
    condition.  It gives a verified, aged inactive-manifest backlog a brief
    writer opportunity when continuously arriving reasoning work would
    otherwise keep every low-priority cleanup pass out of TypeDB.
    """

    configured = settings or {}
    # The hand-off happens only between inference batches and only for a fresh,
    # directly observed backlog. This prevents cleanup starvation without
    # interrupting an active investment transaction.
    # Retention already competes through the low-priority projection
    # coordinator and TypeDB capacity rotation has its own hard guard. A
    # routine inactive-generation count must not pause fresh investment work;
    # an operator can still enable bounded yield windows during a deliberate
    # cleanup campaign.
    enabled = _text(configured.get("ontologyAboxMaintenanceYieldEnabled") or "0").lower()
    priority_count = _integer(_setting_number(
        configured,
        "ontologyAboxMaintenancePriorityInactiveManifestCount",
        8,
        1,
        50000,
    ))
    return {
        "version": SCOPED_ABOX_MAINTENANCE_YIELD_POLICY_VERSION,
        "enabled": enabled not in DISABLED_VALUES,
        "afterSeconds": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceYieldAfterSeconds",
            120,
            30,
            24 * 60 * 60,
        )),
        "windowSeconds": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceYieldWindowSeconds",
            30,
            10,
            5 * 60,
        )),
        # A request can be created while an already-started native inference
        # owns the writer. Keep the request long enough for that bounded
        # batch to finish; the reasoning worker still yields in short
        # ``windowSeconds`` slices once it sees the request.
        "requestTtlSeconds": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceYieldRequestTtlSeconds",
            420,
            30,
            30 * 60,
        )),
        "cooldownSeconds": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceYieldCooldownSeconds",
            120,
            30,
            24 * 60 * 60,
        )),
        "inventoryMaxAgeSeconds": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceYieldInventoryMaxAgeSeconds",
            900,
            60,
            24 * 60 * 60,
        )),
        "priorityInactiveManifestCount": priority_count,
    }


def scoped_abox_maintenance_yield_backlog(
    state: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
    now: datetime = None,
) -> Dict[str, object]:
    """Select one recently observed inactive-manifest backlog for a yield.

    A stale cursor must never defer live inference.  Only an inventory that
    was directly observed within the configured freshness window can request
    this bounded maintenance hand-off.
    """

    policy = scoped_abox_maintenance_yield_policy(settings)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    payload = dict(state or {}) if isinstance(state, Mapping) else {}
    rows = payload.get("backlogByWorld") if isinstance(payload.get("backlogByWorld"), Mapping) else {}
    candidates = []
    stale_count = 0
    observed_count = 0
    for raw_world_id, raw_value in rows.items():
        world_id = _text(raw_world_id)
        value = dict(raw_value or {}) if isinstance(raw_value, Mapping) else {}
        if not world_id or not bool(value.get("inventoryAvailable")):
            continue
        inactive_count = max(0, _integer(value.get("lastInactiveManifestCount")))
        if inactive_count < int(policy["priorityInactiveManifestCount"]):
            continue
        observed_at = _text(value.get("lastInventoryObservedAt"))
        observed_age = _elapsed_seconds(observed_at, current)
        if not observed_at or observed_age > int(policy["inventoryMaxAgeSeconds"]):
            stale_count += 1
            continue
        observed_count += 1
        candidates.append((inactive_count, world_id, observed_at, observed_age))
    if not candidates:
        status = "stale-inventory" if stale_count else "below-priority-backlog"
        return {
            "version": SCOPED_ABOX_MAINTENANCE_YIELD_POLICY_VERSION,
            "eligible": False,
            "status": status,
            "worldId": "",
            "inactiveManifestCount": 0,
            "inventoryObservedAt": "",
            "inventoryAgeSeconds": None,
            "observedPriorityWorldCount": observed_count,
            "stalePriorityWorldCount": stale_count,
            "policy": policy,
        }
    inactive_count, world_id, observed_at, observed_age = sorted(
        candidates,
        key=lambda item: (-item[0], item[1]),
    )[0]
    return {
        "version": SCOPED_ABOX_MAINTENANCE_YIELD_POLICY_VERSION,
        "eligible": True,
        "status": "eligible",
        "worldId": world_id,
        "inactiveManifestCount": inactive_count,
        "inventoryObservedAt": observed_at,
        "inventoryAgeSeconds": observed_age,
        "observedPriorityWorldCount": observed_count,
        "stalePriorityWorldCount": stale_count,
        "policy": policy,
    }


def scoped_abox_maintenance_yield_status(
    state: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
    now: datetime = None,
) -> Dict[str, object]:
    """Read the durable maintenance-yield request without touching TypeDB."""

    policy = scoped_abox_maintenance_yield_policy(settings)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    payload = dict(state or {}) if isinstance(state, Mapping) else {}
    raw_request = payload.get("maintenanceYieldRequest")
    request = dict(raw_request or {}) if isinstance(raw_request, Mapping) else {}
    requested_at = _text(request.get("requestedAt"))
    expires_at = _text(request.get("expiresAt"))
    expiry = _timestamp(expires_at)
    remaining = max(0, int((expiry - current).total_seconds())) if expiry else 0
    active = bool(
        policy["enabled"]
        and requested_at
        and expiry
        and remaining > 0
        and _text(request.get("worldId"))
    )
    request_age = _elapsed_seconds(requested_at, current) if requested_at else None
    last_requested_at = _text(payload.get("maintenanceYieldLastRequestedAt"))
    last_granted_at = _text(payload.get("maintenanceYieldLastGrantedAt"))
    last_released_at = _text(payload.get("maintenanceYieldLastReleasedAt"))
    last_activity_at = last_requested_at
    if _timestamp(last_granted_at) and (
        not _timestamp(last_activity_at)
        or _timestamp(last_granted_at) > _timestamp(last_activity_at)
    ):
        last_activity_at = last_granted_at
    last_activity_age = _elapsed_seconds(last_activity_at, current) if last_activity_at else None
    cooldown_remaining = max(
        0,
        int(policy["cooldownSeconds"]) - int(last_activity_age or 0),
    ) if last_activity_at else 0
    return {
        "version": SCOPED_ABOX_MAINTENANCE_YIELD_POLICY_VERSION,
        "enabled": bool(policy["enabled"]),
        "active": active,
        "status": (
            "active" if active
            else "disabled" if not policy["enabled"]
            else "expired" if requested_at and expiry and remaining <= 0
            else "invalid" if requested_at or expires_at
            else "idle"
        ),
        "checkedAt": current.isoformat().replace("+00:00", "Z"),
        "retryAfterSeconds": min(remaining, int(policy["windowSeconds"])) if active else 0,
        "requestRemainingSeconds": remaining if active else 0,
        "requestedAt": requested_at,
        "requestAgeSeconds": request_age,
        "expiresAt": expires_at,
        "worldId": _text(request.get("worldId")),
        "inactiveManifestCount": max(0, _integer(request.get("inactiveManifestCount"))),
        "inventoryObservedAt": _text(request.get("inventoryObservedAt")),
        "backgroundWaitSeconds": max(0, _integer(request.get("backgroundWaitSeconds"))),
        "lastRequestedAt": last_requested_at,
        "lastGrantedAt": last_granted_at,
        "lastReleasedAt": last_released_at,
        "cooldownRemainingSeconds": cooldown_remaining,
        "policy": policy,
    }


def scoped_abox_maintenance_health(
    storage: Mapping[str, object] = None,
    policy: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Classify scoped ABox retention without affecting investment inference."""

    values = dict(storage or {}) if isinstance(storage, Mapping) else {}
    resolved_policy = dict(policy or {}) if isinstance(policy, Mapping) else scoped_abox_maintenance_policy()
    storage_status = _text(values.get("status") or "ok").lower()
    inactive_count = max(0, _integer(values.get("inactiveManifestCount")))
    retired_generation_backlog = max(0, _integer(values.get("retiredScopeGenerationBacklogCount")))
    warning_count = max(1, _integer(resolved_policy.get("warningInactiveManifestCount") or 40))
    critical_count = max(warning_count + 1, _integer(
        resolved_policy.get("criticalInactiveManifestCount") or 120
    ))
    if storage_status in {"error", "disabled", "driver-missing", "unavailable"}:
        return {
            "status": "warning",
            "state": "unavailable",
            "inactiveManifestCount": inactive_count,
            "retiredScopeGenerationBacklogCount": retired_generation_backlog,
            "warningInactiveManifestCount": warning_count,
            "criticalInactiveManifestCount": critical_count,
            "drainRequired": False,
            "recommendedMaxManifests": 0,
            "reason": "Scoped ABox retention inventory is unavailable.",
        }
    if inactive_count >= critical_count:
        state = "critical"
        reason = "Inactive scoped ABox manifests exceeded the critical retention backlog threshold."
    elif inactive_count >= warning_count:
        state = "warning"
        reason = "Inactive scoped ABox manifests exceeded the warning retention backlog threshold."
    elif inactive_count:
        state = "draining"
        reason = "Inactive scoped ABox manifests are waiting for bounded background retention."
    else:
        state = "ok"
        reason = "No inactive scoped ABox manifest requires retention."
    return {
        "status": "ok" if state in {"ok", "draining"} else state,
        "state": state,
        "inactiveManifestCount": inactive_count,
        "retiredScopeGenerationBacklogCount": retired_generation_backlog,
        "warningInactiveManifestCount": warning_count,
        "criticalInactiveManifestCount": critical_count,
        "drainRequired": inactive_count > 0,
        "recommendedMaxManifests": (
            max(1, _integer(resolved_policy.get("maxManifestsPerRun") or 1))
            if inactive_count
            else 0
        ),
        "reason": reason,
    }


def iso_duration_ms(started_at: object, completed_at: object) -> int:
    """Calculate a bounded duration from durable ISO timestamps when present."""

    start = _text(started_at)
    end = _text(completed_at)
    if not start or not end:
        return 0
    try:
        start_value = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_value = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, min(24 * 60 * 60 * 1000, int((end_value - start_value).total_seconds() * 1000)))


def _scope_delta(plan: Mapping[str, object]) -> Dict[str, object]:
    raw = plan.get("scopeDelta") if isinstance(plan, Mapping) else {}
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _cleanup_summary(result: Mapping[str, object]) -> Dict[str, object]:
    finalization = result.get("aboxActivationFinalization")
    finalization = dict(finalization or {}) if isinstance(finalization, Mapping) else {}
    cleanup = finalization.get("cleanup")
    cleanup = dict(cleanup or {}) if isinstance(cleanup, Mapping) else {}
    return {
        "status": _text(cleanup.get("status") or finalization.get("status") or "not-required"),
        "removedManifestCount": len(cleanup.get("removedManifestIds") or []),
        "remainingInactiveManifestCount": _integer(cleanup.get("remainingInactiveManifestCount")),
        "deletedBatchCount": _integer(cleanup.get("deletedBatchCount")),
        "deferred": bool(finalization.get("cleanupDeferred")),
    }


def compact_abox_relation_persistence(value: object) -> Dict[str, object]:
    """Keep scoped ABox relation-write telemetry bounded in durable audit rows."""
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    if not raw:
        return {}

    def histogram(value: object) -> Dict[str, object]:
        source = dict(value or {}) if isinstance(value, Mapping) else {}
        items = source.get("items") if isinstance(source.get("items"), list) else []
        compact_items = []
        for item in items[:24]:
            if not isinstance(item, Mapping):
                continue
            key = _text(item.get("key"))
            if not key:
                continue
            compact_items.append({
                "key": key[:220],
                "count": max(0, _integer(item.get("count"))),
            })
        return {
            "distinctCount": max(0, _integer(source.get("distinctCount"))),
            "items": compact_items,
            "remainingCount": max(0, _integer(source.get("remainingCount"))),
        }

    def breakdown(value: object) -> Dict[str, object]:
        source = dict(value or {}) if isinstance(value, Mapping) else {}
        return {
            "relationCount": max(0, _integer(source.get("relationCount"))),
            "byRelationType": histogram(source.get("byRelationType")),
            "byScopeFamily": histogram(source.get("byScopeFamily")),
            "bySymbol": histogram(source.get("bySymbol")),
            "byScope": histogram(source.get("byScope")),
        }

    def row_counts(value: object) -> Dict[str, int]:
        source = dict(value or {}) if isinstance(value, Mapping) else {}
        return {
            "entityCount": max(0, _integer(source.get("entityCount"))),
            "relationCount": max(0, _integer(source.get("relationCount"))),
        }

    scope_rows = []
    for item in raw.get("scopes") or []:
        if not isinstance(item, Mapping) or len(scope_rows) >= 40:
            continue
        scope_id = _text(item.get("scopeId"))
        if not scope_id:
            continue
        scope_rows.append({
            "scopeId": scope_id[:260],
            "scopeFamily": _text(item.get("scopeFamily"))[:80],
            "symbol": _text(item.get("symbol"))[:64],
            "requested": row_counts(item.get("requested")),
            "inserted": row_counts(item.get("inserted")),
            "reused": row_counts(item.get("reused")),
        })

    return {
        "version": _text(raw.get("version")),
        "requested": breakdown(raw.get("requested")),
        "inserted": breakdown(raw.get("inserted")),
        "reused": breakdown(raw.get("reused")),
        "scopeCount": max(len(scope_rows), _integer(raw.get("scopeCount"))),
        "scopes": scope_rows,
        "remainingScopeCount": max(0, _integer(raw.get("remainingScopeCount"))),
    }


def _stage_timings(result: Mapping[str, object]) -> Dict[str, int]:
    raw = result.get("runtimeStages") if isinstance(result, Mapping) else {}
    values = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return {
        _text(key): max(0, _integer(value))
        for key, value in values.items()
        if _text(key)
    }


def native_replay_validation(result: Mapping[str, object] = None) -> Dict[str, object]:
    """Validate native-rule coverage without running a second rule engine.

    A full TypeDB execution is complete by itself. A dependency-selected
    execution is a complete delta when TypeDB evaluated every selected rule
    and the execution ledger accounts for every deferred RuleBox rule. An
    aligned prior proof can add unchanged matches, but its absence must not
    invalidate the current changed-candidate result. The function merely
    classifies persisted TypeDB evidence; it never evaluates a condition.
    """
    values = dict(result or {}) if isinstance(result, Mapping) else {}
    inference = values.get("inferenceBox")
    inference = dict(inference or {}) if isinstance(inference, Mapping) else {}
    execution = values.get("ruleboxExecution")
    execution = dict(execution or {}) if isinstance(execution, Mapping) else {}
    proof = values.get("inferenceReuseProof")
    proof = dict(proof or {}) if isinstance(proof, Mapping) else {}
    plan = values.get("inferenceImpactPlan")
    plan = dict(plan or {}) if isinstance(plan, Mapping) else {}
    projection_scope = values.get("projectionScope")
    projection_scope = dict(projection_scope or {}) if isinstance(projection_scope, Mapping) else {}

    selection_applied = bool(execution.get("nativeRuleSelectionApplied"))
    native_evaluation_complete = bool(
        inference.get("nativeTypeDbReasoningCompleted")
        or inference.get("typedbNativeRuleEvaluationCompleted")
        or execution.get("nativeInferenceEvaluationComplete")
    )
    generation_aligned = bool(inference.get("generationAligned"))
    requested_symbols = {
        _text(symbol).upper()
        for symbol in (
            inference.get("requestedSymbols")
            or plan.get("inferenceTargetSymbols")
            or projection_scope.get("targetSymbols")
            or []
        )
        if _text(symbol)
    }
    actual_symbols = {
        _text(symbol).upper()
        for symbol in (inference.get("targetSymbols") or [])
        if _text(symbol)
    }
    coverage_complete = not requested_symbols or requested_symbols.issubset(actual_symbols)
    proof_verified = (
        _text(proof.get("status")) == "verified"
        and bool(proof.get("coverageComplete"))
        and bool(proof.get("selectionApplied")) == selection_applied
    )
    candidate_rule_count = max(0, _integer(execution.get("nativeRuleSelectionCandidateCount")))
    executed_rule_count = max(0, _integer(execution.get("nativeRuleSelectionExecutedCount")))
    deferred_rule_count = max(0, _integer(execution.get("nativeRuleSelectionDeferredCount")))
    full_rule_count = max(0, _integer(execution.get("nativeRuleSelectionFullRuleCount")))
    selected_ledger_complete = bool(
        full_rule_count > 0
        and executed_rule_count >= candidate_rule_count
        and executed_rule_count + deferred_rule_count == full_rule_count
    )
    if selection_applied:
        verified = bool(
            native_evaluation_complete
            and generation_aligned
            and coverage_complete
            and selected_ledger_complete
        )
        if verified and proof_verified:
            status = "verified-prior-coverage"
            reason = "Dependency-selected native execution is backed by an aligned prior complete TypeDB proof."
        elif verified:
            status = "verified-selected-delta"
            reason = "TypeDB evaluated every changed candidate and explicitly accounted for every deferred RuleBox rule."
        else:
            status = "incomplete-coverage"
            if not selected_ledger_complete:
                reason = "Dependency-selected execution does not account for the complete selected/deferred RuleBox ledger."
            elif not coverage_complete:
                reason = "Dependency-selected execution did not cover every requested target symbol."
            elif not native_evaluation_complete:
                reason = "TypeDB did not confirm native rule evaluation completion."
            else:
                reason = "TypeDB InferenceBox is not aligned with the active ABox generation."
    else:
        verified = bool(native_evaluation_complete and generation_aligned and coverage_complete)
        status = "complete-native-evaluation" if verified else "incomplete-native-evaluation"
        if verified:
            reason = "Current ABox received a complete native TypeDB evaluation."
        elif not coverage_complete:
            reason = "Native execution did not cover every requested target symbol."
        elif not native_evaluation_complete:
            reason = "TypeDB did not confirm native rule evaluation completion."
        else:
            reason = "TypeDB InferenceBox is not aligned with the active ABox generation."
    return {
        "version": NATIVE_REPLAY_VALIDATION_VERSION,
        "status": status,
        "reason": reason,
        "verified": verified,
        "selectionApplied": selection_applied,
        "coverageComplete": coverage_complete,
        "nativeEvaluationComplete": native_evaluation_complete,
        "generationAligned": generation_aligned,
        "requestedTargetSymbolCount": len(requested_symbols),
        "actualTargetSymbolCount": len(actual_symbols),
        "priorProofStatus": _text(proof.get("status")),
        "candidateRuleCount": candidate_rule_count,
        "executedRuleCount": executed_rule_count,
        "deferredRuleCount": deferred_rule_count,
        "fullRuleCount": full_rule_count,
        "selectedRuleLedgerComplete": selected_ledger_complete,
    }


def native_rule_failure_diagnostic(
    execution: Mapping[str, object] = None,
    target_symbols: Iterable[object] = None,
) -> Dict[str, object]:
    """Summarize a failed native RuleBox turn without judging a rule in Python.

    TypeDB already identifies the blocking rule in ``nativeMatchResult``.  The
    projection layer needs a compact, stable operational payload so it can
    preserve that root failure instead of replacing it with a secondary ABox
    and InferenceBox alignment symptom.
    """

    values = dict(execution or {}) if isinstance(execution, Mapping) else {}
    execution_status = _text(values.get("status")).lower()
    native_match = values.get("nativeMatchResult")
    native_match = dict(native_match or {}) if isinstance(native_match, Mapping) else {}
    native_status = _text(native_match.get("status")).lower()
    completed = bool(
        values.get("nativeInferenceEvaluationComplete")
        or values.get("nativeTypeDbReasoningCompleted")
        or values.get("typedbNativeRuleEvaluationCompleted")
    )

    # A complete match and a complete no-match are both safe outcomes. The
    # deferred statuses are handled by their dedicated projection branches.
    if (execution_status in {"ok", "empty"} and completed) or execution_status.startswith("deferred-"):
        return {}
    if execution_status in {"", "ok", "empty"} and not native_status:
        return {}

    blocking = values.get("blockingRule")
    blocking = dict(blocking or {}) if isinstance(blocking, Mapping) else {}
    if not blocking:
        blocking = native_match.get("blockingRule")
        blocking = dict(blocking or {}) if isinstance(blocking, Mapping) else {}
    if not blocking:
        for item in native_match.get("skippedRules") or values.get("skippedRules") or []:
            if isinstance(item, Mapping) and _text(item.get("status")).lower() not in {
                "",
                "not-applicable",
                "not-applicable-preflight",
                "planned",
            }:
                blocking = dict(item)
                break

    blocking_status = _text(blocking.get("status")).lower()
    reason_code = _text(
        native_match.get("reasonCode") or values.get("reasonCode")
    )
    reason = _text(native_match.get("reason") or values.get("reason"))
    blocking_reason = _text(blocking.get("reason"))
    timeout = (
        blocking_status == "query-timeout"
        or native_status == "query-timeout"
        or "timeout" in reason_code.lower()
        or "timeout" in reason.lower()
        or "timed out" in reason.lower()
        or "[tsv13]" in reason.lower()
    )
    budget_exhausted = (
        blocking_status == "deferred-by-runtime-budget"
        or native_status == "deferred-by-runtime-budget"
        or "budget" in reason_code.lower()
    )
    diagnostic_status = (
        "query-timeout"
        if timeout
        else "runtime-budget-exhausted"
        if budget_exhausted
        else "native-rule-incomplete"
        if execution_status == "partial" or native_status == "partial"
        else "native-rule-failed"
    )
    raw_candidates = (
        blocking.get("candidateSymbols")
        or target_symbols
        or values.get("targetSymbols")
        or []
    )
    if isinstance(raw_candidates, (str, bytes)):
        raw_candidates = [raw_candidates]
    candidates = [
        _text(symbol).upper()
        for symbol in raw_candidates
        if _text(symbol)
    ]
    rule_id = _text(blocking.get("ruleId") or values.get("ruleId"))
    if not reason:
        reason = blocking_reason or "TypeDB native RuleBox execution did not complete."
    return {
        "version": NATIVE_RULE_FAILURE_DIAGNOSTIC_VERSION,
        "stage": "native-rule-query",
        "status": diagnostic_status,
        "executionStatus": execution_status or native_status or "error",
        "reasonCode": reason_code,
        "reason": reason[:500],
        "ruleId": rule_id,
        "blockingRuleStatus": blocking_status,
        "targetSymbols": sorted(set(candidates)),
        "queryMode": _text(
            native_match.get("nativeExecutionMode")
            or values.get("typedbNativeExecutionMode")
            or values.get("nativeExecutionMode")
        ),
        "retryable": diagnostic_status in {
            "query-timeout",
            "runtime-budget-exhausted",
            "native-rule-incomplete",
            "native-rule-failed",
        },
        "recommendedRetryAfterSeconds": 30 if timeout or budget_exhausted else 15,
    }


def _impact_diagnostics(plan: Mapping[str, object]) -> Dict[str, object]:
    diagnostics = plan.get("diagnostics") if isinstance(plan, Mapping) else {}
    diagnostics = dict(diagnostics or {}) if isinstance(diagnostics, Mapping) else {}
    scope_types = [
        {
            "type": _text(item.get("type")),
            "label": _text(item.get("label")),
            "count": max(0, _integer(item.get("count"))),
        }
        for item in diagnostics.get("globalScopeTypes") or []
        if isinstance(item, Mapping)
    ]
    return {
        "classification": _text(diagnostics.get("classification")),
        "reasonCodes": [_text(item) for item in diagnostics.get("reasonCodes") or [] if _text(item)][:20],
        "globalScopeCount": max(0, _integer(diagnostics.get("globalScopeCount"))),
        "globalScopeTypes": scope_types[:12],
        "candidateRuleRatioPct": max(0.0, _number(diagnostics.get("candidateRuleRatioPct"))),
        "candidateSubsetAvailable": bool(diagnostics.get("candidateSubsetAvailable")),
        "selectionEligibilityReason": _text(diagnostics.get("selectionEligibilityReason")),
        "eventScopeAgreement": _text(diagnostics.get("eventScopeAgreement")),
        "eventFactFamilies": [_text(item) for item in diagnostics.get("eventFactFamilies") or [] if _text(item)][:20],
        "unexpectedChangedFamilies": [
            _text(item) for item in diagnostics.get("unexpectedChangedFamilies") or [] if _text(item)
        ][:20],
    }


def native_rule_timing_profile(
    payload: Mapping[str, object] = None,
    limit: int = 8,
) -> Dict[str, object]:
    """Return bounded operational timing for TypeDB schema functions only."""

    values = dict(payload or {}) if isinstance(payload, Mapping) else {}
    existing = values.get("typedbNativeRuleTimingProfile")
    if not isinstance(existing, Mapping):
        existing = values.get("nativeRuleTimingProfile")
    if isinstance(existing, Mapping) and isinstance(existing.get("slowestRules"), list):
        rows = [
            dict(item)
            for item in existing.get("slowestRules") or []
            if isinstance(item, Mapping)
        ]
        return {
            "version": _text(existing.get("version")) or NATIVE_RULE_TIMING_PROFILE_VERSION,
            "wallClockMs": max(0, _integer(existing.get("wallClockMs"))),
            "executedRuleCount": max(0, _integer(existing.get("executedRuleCount"))),
            "executedRuleWorkCount": max(
                0,
                _integer(existing.get("executedRuleWorkCount") or existing.get("executedRuleCount")),
            ),
            "incompleteRuleCount": max(0, _integer(existing.get("incompleteRuleCount"))),
            "notApplicableRuleCount": max(0, _integer(existing.get("notApplicableRuleCount"))),
            "aggregateRuleElapsedMs": max(0, _integer(existing.get("aggregateRuleElapsedMs"))),
            "aggregateQueryDurationMs": max(0, _integer(existing.get("aggregateQueryDurationMs"))),
            "slowestRules": rows[:max(1, min(20, int(limit or 8)))],
        }

    executed = [
        dict(item)
        for item in values.get("executedRules") or []
        if isinstance(item, Mapping) and _text(item.get("ruleId"))
    ]
    skipped = [
        dict(item)
        for item in values.get("skippedRules") or []
        if isinstance(item, Mapping) and _text(item.get("ruleId"))
    ]
    incomplete_statuses = {
        "blocked",
        "error",
        "partial",
        "query-error",
        "query-timeout",
        "deferred-by-runtime-budget",
    }
    incomplete = [
        item for item in skipped
        if _text(item.get("status")).lower() in incomplete_statuses
    ]
    not_applicable = [item for item in skipped if item not in incomplete]

    def timing_row(item: Mapping[str, object], status: str) -> Dict[str, object]:
        symbols = item.get("candidateSymbols") if isinstance(item.get("candidateSymbols"), list) else []
        return {
            "ruleId": _text(item.get("ruleId")),
            "nativeRuleId": _text(item.get("nativeRuleId")),
            "schemaFunctionName": _text(item.get("schemaFunctionName")),
            "status": status,
            "rowCount": max(0, _integer(item.get("rowCount"))),
            "candidateSymbolCount": len([symbol for symbol in symbols if _text(symbol)]),
            "targetWorkShardIndex": max(0, _integer(item.get("targetWorkShardIndex"))),
            "targetWorkShardCount": max(1, _integer(item.get("targetWorkShardCount") or 1)),
            "targetWorkShardingUsed": bool(item.get("targetWorkShardingUsed")),
            "targetWorkAdaptiveShardingUsed": bool(item.get("targetWorkAdaptiveShardingUsed")),
            "timeoutFallbackUsed": bool(item.get("timeoutFallbackUsed")),
            "timeoutFallbackShardCount": max(0, _integer(item.get("timeoutFallbackShardCount"))),
            "queryComplexity": max(0, _integer(item.get("queryComplexity"))),
            "queryCount": max(0, _integer(item.get("queryCount"))),
            "anyConditionQueryCount": max(0, _integer(item.get("anyConditionQueryCount"))),
            "elapsedMs": max(0, _integer(item.get("elapsedMs"))),
            "queryDurationMs": max(0, _integer(item.get("queryDurationMs"))),
        }

    rows = [timing_row(item, "ok") for item in executed]
    rows.extend(timing_row(item, _text(item.get("status")) or "blocked") for item in incomplete)
    rows.sort(
        key=lambda item: (item["elapsedMs"], item["queryDurationMs"], item["ruleId"]),
        reverse=True,
    )
    bounded = rows[:max(1, min(20, int(limit or 8)))]
    return {
        "version": NATIVE_RULE_TIMING_PROFILE_VERSION,
        "wallClockMs": max(0, _integer(values.get("wallClockMs"))),
        "executedRuleCount": max(
            0,
            _integer(values.get("executedRuleCount"))
            or len({
                _text(item.get("ruleId"))
                for item in executed
                if _text(item.get("ruleId"))
            }),
        ),
        "executedRuleWorkCount": max(
            0,
            _integer(values.get("executedRuleWorkCount")) or len(executed),
        ),
        "incompleteRuleCount": len(incomplete),
        "notApplicableRuleCount": len(not_applicable),
        # Parallel rule durations overlap; this is a diagnostic total only.
        "aggregateRuleElapsedMs": sum(item["elapsedMs"] for item in rows),
        "aggregateQueryDurationMs": sum(item["queryDurationMs"] for item in rows),
        "slowestRules": bounded,
    }


def native_rule_adaptive_target_sharding_policy(
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Return the bounded operational policy for proactive target sharding.

    This controls only TypeDB read scheduling.  It does not alter the ABox,
    TypeDB rule semantics, or which inference result becomes active.
    """

    configured = settings or {}
    enabled = _text(
        configured.get("typedbNativeRuleAdaptiveTargetShardingEnabled", "1")
    ).lower() not in DISABLED_VALUES
    return {
        "version": NATIVE_RULE_ADAPTIVE_TARGET_SHARDING_PROFILE_VERSION,
        "enabled": enabled,
        "lookbackRunLimit": _integer(_setting_number(
            configured,
            "typedbNativeRuleAdaptiveTargetShardingLookbackRuns",
            12,
            1,
            80,
        )),
        "targetParallelism": _integer(_setting_number(
            configured,
            "typedbNativeRuleAdaptiveTargetShardingParallelism",
            2,
            2,
            4,
        )),
        "nearTimeoutRatio": _setting_number(
            configured,
            "typedbNativeRuleAdaptiveTargetShardingNearTimeoutRatio",
            0.7,
            0.5,
            0.95,
        ),
        "queryTimeoutMs": int(_setting_number(
            configured,
            "typedbNativeRuleQueryTimeoutSeconds",
            10,
            0.5,
            120,
        ) * 1000),
    }


def native_rule_adaptive_target_sharding_profile(
    observations: Iterable[Mapping[str, object]],
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Derive a conservative read-scheduling profile from completed audits.

    A single prior bounded-read timeout is enough to make the same rule use
    smaller target groups for a limited number of later runs.  Rules that only
    approach the timeout must do so repeatedly before they are changed.  The
    profile is intentionally ephemeral and bounded by recent audit rows so a
    temporary TypeDB slowdown cannot permanently multiply read work.
    """

    policy = native_rule_adaptive_target_sharding_policy(settings)
    profile = {
        "version": NATIVE_RULE_ADAPTIVE_TARGET_SHARDING_PROFILE_VERSION,
        "enabled": bool(policy["enabled"]),
        "status": "disabled" if not policy["enabled"] else "no-history",
        "lookbackRunLimit": int(policy["lookbackRunLimit"]),
        "sampledRunCount": 0,
        "queryTimeoutMs": int(policy["queryTimeoutMs"]),
        "nearTimeoutThresholdMs": int(
            int(policy["queryTimeoutMs"]) * float(policy["nearTimeoutRatio"])
        ),
        "targetParallelism": int(policy["targetParallelism"]),
        "rules": [],
        "preemptiveRuleIds": [],
    }
    if not policy["enabled"]:
        return profile

    stats_by_rule: Dict[str, Dict[str, object]] = {}
    threshold_ms = int(profile["nearTimeoutThresholdMs"])
    sampled = 0
    for observation in list(observations or [])[:int(policy["lookbackRunLimit"])]:
        if not isinstance(observation, Mapping):
            continue
        inference = observation.get("inference")
        inference = dict(inference or {}) if isinstance(inference, Mapping) else {}
        timing = inference.get("nativeRuleTiming")
        timing = dict(timing or {}) if isinstance(timing, Mapping) else {}
        rows = timing.get("slowestRules")
        if not isinstance(rows, list):
            continue
        sampled += 1
        # A pre-sharded rule produces one timing row per target shard. Aggregate
        # them first so one generation counts once toward the history policy.
        per_run: Dict[str, Dict[str, object]] = {}
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            rule_id = _text(item.get("ruleId"))
            candidate_count = max(0, _integer(item.get("candidateSymbolCount")))
            if not rule_id or candidate_count < 2:
                continue
            elapsed_ms = max(0, _integer(item.get("elapsedMs")))
            query_duration_ms = max(0, _integer(item.get("queryDurationMs")))
            current = per_run.setdefault(rule_id, {
                "candidateSymbolCount": candidate_count,
                "elapsedMs": elapsed_ms,
                "queryDurationMs": query_duration_ms,
                "timeoutFallbackUsed": False,
                "timedOut": False,
            })
            current["candidateSymbolCount"] = max(
                int(current["candidateSymbolCount"]), candidate_count
            )
            current["elapsedMs"] = max(int(current["elapsedMs"]), elapsed_ms)
            current["queryDurationMs"] = max(
                int(current["queryDurationMs"]), query_duration_ms
            )
            current["timeoutFallbackUsed"] = bool(
                current["timeoutFallbackUsed"] or item.get("timeoutFallbackUsed")
            )
            current["timedOut"] = bool(
                current["timedOut"]
                or _text(item.get("status")).lower() == "query-timeout"
            )
        for rule_id, item in per_run.items():
            stats = stats_by_rule.setdefault(rule_id, {
                "ruleId": rule_id,
                "observedRunCount": 0,
                "timeoutFallbackRunCount": 0,
                "timedOutRunCount": 0,
                "nearTimeoutRunCount": 0,
                "maximumCandidateSymbolCount": 0,
                "maximumElapsedMs": 0,
                "maximumQueryDurationMs": 0,
            })
            stats["observedRunCount"] = int(stats["observedRunCount"]) + 1
            stats["timeoutFallbackRunCount"] = int(stats["timeoutFallbackRunCount"]) + int(
                bool(item["timeoutFallbackUsed"])
            )
            stats["timedOutRunCount"] = int(stats["timedOutRunCount"]) + int(
                bool(item["timedOut"])
            )
            stats["nearTimeoutRunCount"] = int(stats["nearTimeoutRunCount"]) + int(
                int(item["queryDurationMs"]) >= threshold_ms
            )
            stats["maximumCandidateSymbolCount"] = max(
                int(stats["maximumCandidateSymbolCount"]), int(item["candidateSymbolCount"])
            )
            stats["maximumElapsedMs"] = max(
                int(stats["maximumElapsedMs"]), int(item["elapsedMs"])
            )
            stats["maximumQueryDurationMs"] = max(
                int(stats["maximumQueryDurationMs"]), int(item["queryDurationMs"])
            )

    rows = []
    for stats in stats_by_rule.values():
        timeout_history = int(stats["timeoutFallbackRunCount"]) + int(stats["timedOutRunCount"])
        repeated_near_timeout = int(stats["nearTimeoutRunCount"]) >= 2
        preemptive = timeout_history > 0 or repeated_near_timeout
        reason = (
            "recent-timeout-recovery"
            if timeout_history > 0
            else "repeated-near-timeout"
            if repeated_near_timeout
            else ""
        )
        rows.append({
            **stats,
            "preemptiveTargetSharding": preemptive,
            "targetParallelism": min(
                int(policy["targetParallelism"]),
                int(stats["maximumCandidateSymbolCount"]),
            ) if preemptive else 1,
            "reason": reason,
        })
    rows.sort(
        key=lambda item: (
            bool(item["preemptiveTargetSharding"]),
            int(item["timeoutFallbackRunCount"]) + int(item["timedOutRunCount"]),
            int(item["nearTimeoutRunCount"]),
            int(item["maximumQueryDurationMs"]),
            str(item["ruleId"]),
        ),
        reverse=True,
    )
    bounded_rows = rows[:20]
    preemptive_rule_ids = [
        str(item["ruleId"])
        for item in bounded_rows
        if bool(item["preemptiveTargetSharding"]) and int(item["targetParallelism"]) > 1
    ]
    profile.update({
        "sampledRunCount": sampled,
        "rules": bounded_rows,
        "preemptiveRuleIds": preemptive_rule_ids,
        "status": "active" if preemptive_rule_ids else "no-slow-rules",
    })
    return profile


def _slo_state(
    result: Mapping[str, object],
    duration_ms: int,
    inference: Mapping[str, object],
    execution: Mapping[str, object],
    policy: Mapping[str, object],
) -> Dict[str, object]:
    status = _text(result.get("status")).lower()
    inference_status = _text(inference.get("status")).lower()
    execution_status = _text(execution.get("status")).lower()
    violations: List[Dict[str, str]] = []
    if duration_ms > _integer(policy.get("projectionSloMs")):
        violations.append({
            "code": "projection_latency",
            "severity": "warning",
            "message": "Projection duration exceeded the configured SLO.",
        })
    stages = _stage_timings(result)
    inference_ms = _integer(
        execution.get("durationMs")
        or execution.get("elapsedMs")
        or stages.get("nativeInferenceMs")
    )
    if inference_ms > _integer(policy.get("inferenceSloMs")):
        violations.append({
            "code": "inference_latency",
            "severity": "warning",
            "message": "Native inference duration exceeded the configured SLO.",
        })
    if any(token in status for token in ["error", "failed", "invalid", "blocked"]) or (
        status not in {"", "unchanged-material-facts"}
        and inference_status in {"error", "failed", "blocked-pending-abox-activation", "pending-abox-activation"}
    ):
        violations.append({
            "code": "projection_or_inference_failure",
            "severity": "critical",
            "message": "Projection or native InferenceBox did not complete safely.",
        })
    if execution_status in {"deferred-inference-write-lease", "blocked-pending-abox-activation"}:
        violations.append({
            "code": "serialized_writer_wait",
            "severity": "warning",
            "message": "A projection waited for the serialized TypeDB writer boundary.",
        })
    equivalence = result.get("incrementalEquivalenceAudit")
    equivalence = dict(equivalence or {}) if isinstance(equivalence, Mapping) else {}
    if str(equivalence.get("status") or "") == "mismatch-reconciled":
        violations.append({
            "code": "incremental_inference_mismatch_reconciled",
            "severity": "warning",
            "message": "A sampled full TypeDB pass corrected stale incremental rule slots.",
        })
    severity = "critical" if any(item["severity"] == "critical" for item in violations) else "warning" if violations else "ok"
    return {
        "state": severity,
        "violations": violations,
        "projectionSloMs": _integer(policy.get("projectionSloMs")),
        "inferenceSloMs": _integer(policy.get("inferenceSloMs")),
    }


def build_projection_runtime_observation(
    projection_run,
    result: Mapping[str, object],
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Build a compact operational record already safe for MySQL audit JSON."""

    values = dict(result or {})
    plan = values.get("inferenceImpactPlan")
    plan = dict(plan or {}) if isinstance(plan, Mapping) else {}
    projection_scope = values.get("projectionScope")
    projection_scope = dict(projection_scope or {}) if isinstance(projection_scope, Mapping) else {}
    inference = values.get("inferenceBox")
    inference = dict(inference or {}) if isinstance(inference, Mapping) else {}
    execution = values.get("ruleboxExecution")
    execution = dict(execution or {}) if isinstance(execution, Mapping) else {}
    runtime_identity = values.get("runtimeIdentity")
    runtime_identity = dict(runtime_identity or {}) if isinstance(runtime_identity, Mapping) else {}
    target_patch = projection_scope.get("targetScopedManifestPatch")
    target_patch = dict(target_patch or {}) if isinstance(target_patch, Mapping) else {}
    scope_selection_trace = target_patch.get("scopeSelectionTrace")
    scope_selection_trace = (
        dict(scope_selection_trace or {})
        if isinstance(scope_selection_trace, Mapping)
        else {}
    )
    relation_persistence = compact_abox_relation_persistence(values.get("relationPersistence"))
    stages = _stage_timings(values)
    native_rule_timing = native_rule_timing_profile(execution)
    raw_native_stage_timings = execution.get("typedbNativeStageTimings")
    raw_native_stage_timings = (
        dict(raw_native_stage_timings)
        if isinstance(raw_native_stage_timings, Mapping)
        else {}
    )
    native_stage_timings = {
        _text(key): max(0, _integer(value))
        for key, value in raw_native_stage_timings.items()
        if _text(key) and isinstance(value, (int, float))
    }
    delta = _scope_delta(plan)
    impact_diagnostics = _impact_diagnostics(plan)
    replay_validation = values.get("nativeReplayValidation")
    replay_validation = (
        dict(replay_validation)
        if isinstance(replay_validation, Mapping)
        else native_replay_validation(values)
    )
    equivalence_audit = values.get("incrementalEquivalenceAudit")
    equivalence_audit = (
        dict(equivalence_audit)
        if isinstance(equivalence_audit, Mapping)
        else {}
    )
    duration_ms = iso_duration_ms(
        getattr(projection_run, "started_at", ""),
        getattr(projection_run, "completed_at", ""),
    ) or _integer(stages.get("totalMs"))
    policy = runtime_slo_policy(settings)
    trace_count = _integer(inference.get("traceCount"))
    if not trace_count:
        trace_count = len(inference.get("traces") or [])
    matched_rule_count = _integer(execution.get("matchedRuleCount")) or trace_count
    actual_target_symbols = [
        _text(symbol).upper()
        for symbol in (
            inference.get("targetSymbols")
            or execution.get("targetSymbols")
            or projection_scope.get("targetSymbols")
            or []
        )
        if _text(symbol)
    ]
    requested_target_symbols = [
        _text(symbol).upper()
        for symbol in (
            inference.get("requestedSymbols")
            or inference.get("symbols")
            or plan.get("inferenceTargetSymbols")
            or actual_target_symbols
        )
        if _text(symbol)
    ]
    not_evaluated_symbols = sorted(set(requested_target_symbols) - set(actual_target_symbols))
    target_coverage_status = _text(inference.get("targetCoverageStatus"))
    if not target_coverage_status:
        target_coverage_status = (
            "not-requested"
            if not requested_target_symbols
            else "partial"
            if not_evaluated_symbols
            else "complete"
        )
    observation = {
        "version": ONTOLOGY_RUNTIME_OBSERVATION_VERSION,
        "runId": _text(getattr(projection_run, "run_id", "")),
        "accountId": _text(getattr(projection_run, "account_id", "")),
        "observedAt": _text(getattr(projection_run, "completed_at", "")),
        "status": _text(values.get("status")),
        "graphStore": _text(values.get("graphStore") or getattr(projection_run, "graph_store", "")),
        "runtimeIdentity": {
            "contract": _text(runtime_identity.get("contract")),
            "version": _text(runtime_identity.get("version")),
            "revision": _text(runtime_identity.get("revision")),
            "source": _text(runtime_identity.get("source")),
            "python": _text(runtime_identity.get("python")),
        },
        "durationMs": duration_ms,
        "materialChangeDetected": bool(values.get("materialChangeDetected")),
        "preservedActiveGeneration": bool(values.get("preservedActiveGeneration")),
        "scope": {
            "scopeCount": _integer(projection_scope.get("scopeCount")),
            "previousScopeCount": _integer(delta.get("previousScopeCount")),
            "nextScopeCount": _integer(delta.get("nextScopeCount")),
            "addedScopeCount": len(delta.get("addedScopeIds") or []),
            "removedScopeCount": len(delta.get("removedScopeIds") or []),
            "changedScopeCount": len(delta.get("changedScopeIds") or []),
            "directChangedScopeCount": len(delta.get("directChangedScopeIds") or delta.get("changedScopeIds") or []),
            "affectedScopeCount": len(delta.get("affectedScopeIds") or []),
            "dependencyAffectedScopeCount": len(delta.get("dependencyAffectedScopeIds") or []),
            "families": list(plan.get("changedScopeFamilies") or []),
            "dependencyAffectedFamilies": list(delta.get("dependencyAffectedScopeFamilies") or []),
            "globalImpact": bool(plan.get("globalImpact")),
            "impactDiagnostics": impact_diagnostics,
            "targetScopedManifestPatch": {
                "status": _text(target_patch.get("status")),
                "mode": _text(target_patch.get("mode")),
                "fallbackReason": _text(target_patch.get("fallbackReason")),
                "targetSymbolCount": len(target_patch.get("targetSymbols") or []),
                "targetSymbols": [
                    _text(symbol).upper()
                    for symbol in (target_patch.get("targetSymbols") or [])[:20]
                    if _text(symbol)
                ],
                "selectedIncomingScopeCount": _integer(target_patch.get("selectedIncomingScopeCount")),
                "reusedActiveScopeCount": _integer(target_patch.get("reusedActiveScopeCount")),
                "deferredScopeCount": _integer(target_patch.get("deferredScopeCount")),
                "factSlotStatus": _text(target_patch.get("factSlotStatus")),
                "factSlotSelectedScopeCount": _integer(target_patch.get("factSlotSelectedScopeCount")),
                "factSlotDeferredScopeCount": _integer(target_patch.get("factSlotDeferredScopeCount")),
                "factSlotFamilies": list(target_patch.get("factSlotFamilies") or [])[:20],
                "factSlotFallbackReason": _text(target_patch.get("factSlotFallbackReason")),
                "factSlotFamiliesBySymbol": {
                    _text(symbol).upper(): [
                        _text(value)
                        for value in (families or [])[:20]
                        if _text(value)
                    ]
                    for symbol, families in dict(
                        target_patch.get("factSlotFamiliesBySymbol") or {}
                    ).items()
                    if _text(symbol)
                },
                "factSlotChangedFieldsBySymbol": {
                    _text(symbol).upper(): [
                        _text(value)
                        for value in (fields or [])[:80]
                        if _text(value)
                    ]
                    for symbol, fields in dict(
                        target_patch.get("factSlotChangedFieldsBySymbol") or {}
                    ).items()
                    if _text(symbol)
                },
                "factSlotPreciseFieldRoutingSymbols": [
                    _text(symbol).upper()
                    for symbol in (
                        target_patch.get("factSlotPreciseFieldRoutingSymbols") or []
                    )[:20]
                    if _text(symbol)
                ],
                "factSlotUnclassifiedChangedFieldsBySymbol": {
                    _text(symbol).upper(): [
                        _text(value)
                        for value in (fields or [])[:80]
                        if _text(value)
                    ]
                    for symbol, fields in dict(
                        target_patch.get(
                            "factSlotUnclassifiedChangedFieldsBySymbol"
                        ) or {}
                    ).items()
                    if _text(symbol)
                },
                "scopeIntegrityAuditIntervalMinutes": _number(
                    target_patch.get("scopeIntegrityAuditIntervalMinutes")
                ),
                "scopeIntegrityAuditDue": bool(target_patch.get("scopeIntegrityAuditDue")),
                "automaticFullProjectionBlocked": bool(
                    target_patch.get("automaticFullProjectionBlocked")
                ),
                "scopeSelectionTrace": {
                    "version": _text(scope_selection_trace.get("version")),
                    "selected": [
                        dict(item)
                        for item in (scope_selection_trace.get("selected") or [])[:40]
                        if isinstance(item, Mapping)
                    ],
                    "deferred": [
                        dict(item)
                        for item in (scope_selection_trace.get("deferred") or [])[:40]
                        if isinstance(item, Mapping)
                    ],
                },
            },
        },
        "inference": {
            "status": _text(inference.get("status")),
            "generationId": _text(inference.get("inferenceGenerationId")),
            "generationAligned": bool(inference.get("generationAligned")),
            "nativeTypeDbReasoningUsed": bool(inference.get("nativeTypeDbReasoningUsed")),
            "plannedTargetSymbolCount": len(plan.get("inferenceTargetSymbols") or []),
            "requestedTargetSymbolCount": len(requested_target_symbols),
            "targetSymbolCount": len(actual_target_symbols),
            "targetSymbols": actual_target_symbols[:20],
            "notEvaluatedSymbolCount": len(not_evaluated_symbols),
            "notEvaluatedSymbols": not_evaluated_symbols[:20],
            "targetCoverageStatus": target_coverage_status,
            "candidateRuleCount": _integer(plan.get("candidateRuleCount")),
            "enabledRuleCount": _integer(plan.get("enabledRuleCount")),
            "candidateRuleRatioPct": _number(impact_diagnostics.get("candidateRuleRatioPct")),
            "nativeRuleSelectionEligibilityReason": _text(
                plan.get("nativeRuleSelectionEligibilityReason")
                or impact_diagnostics.get("selectionEligibilityReason")
            ),
            "executedRuleCount": _integer(
                execution.get("typedbNativeRuleExecutedCount")
                or execution.get("nativeRuleSelectionExecutedCount")
            ),
            "executedRuleWorkCount": _integer(execution.get("typedbNativeRuleExecutedWorkCount")),
            "manifestEvidencePreflightEnabled": bool(
                execution.get("typedbNativeManifestEvidencePreflightEnabled")
            ),
            "relationEvidencePreflightEnabled": bool(
                execution.get("typedbNativeRelationEvidencePreflightEnabled")
            ),
            "manifestEvidencePreflightPrunedSymbolCount": _integer(
                execution.get("typedbNativeManifestEvidencePreflightPrunedSymbolCount")
            ),
            "targetParallelism": _integer(execution.get("typedbNativeRuleTargetParallelism")),
            "subjectRuleParallelism": _integer(
                execution.get("typedbNativeRuleSubjectRuleParallelism")
            ),
            "totalReadParallelismCap": _integer(
                execution.get("typedbNativeRuleTotalReadParallelismCap")
            ),
            "effectiveTotalReadParallelism": _integer(
                execution.get("typedbNativeRuleEffectiveTotalReadParallelism")
            ),
            "subjectFanoutUsed": bool(execution.get("typedbNativeRuleSubjectFanoutUsed")),
            "subjectFanoutParallelism": _integer(
                execution.get("typedbNativeRuleSubjectFanoutParallelism")
            ),
            "subjectFanoutDurationMs": _integer(
                execution.get("typedbNativeRuleSubjectFanoutDurationMs")
            ),
            "subjectFanoutFailureCount": _integer(
                execution.get("typedbNativeRuleSubjectFanoutFailureCount")
            ),
            "subjectFanoutSubjects": [
                dict(item)
                for item in execution.get("typedbNativeRuleSubjectFanoutSubjects") or []
                if isinstance(item, Mapping)
            ][:8],
            "targetWorkShardingUsed": bool(execution.get("typedbNativeRuleTargetWorkShardingUsed")),
            "targetWorkShardCount": _integer(execution.get("typedbNativeRuleTargetWorkShardCount")),
            "targetWorkItemCount": _integer(execution.get("typedbNativeRuleWorkItemCount")),
            "adaptiveTargetShardingEnabled": bool(
                execution.get("typedbNativeRuleAdaptiveTargetShardingEnabled")
            ),
            "adaptiveTargetShardingProfileStatus": _text(
                execution.get("typedbNativeRuleAdaptiveTargetShardingProfileStatus")
            ),
            "adaptiveTargetShardingUsed": bool(
                execution.get("typedbNativeRuleAdaptiveTargetShardingUsed")
            ),
            "adaptiveTargetShardedRuleCount": _integer(
                execution.get("typedbNativeRuleAdaptiveTargetShardedRuleCount")
            ),
            "commitMode": _text(execution.get("typedbNativeRuleCommitMode")),
            "deferredRuleCount": _integer(execution.get("nativeRuleSelectionDeferredCount")),
            "nativeRuleSelectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
            "nativeRuleSelectionFallbackReason": _text(execution.get("nativeRuleSelectionFallbackReason")),
            "matchedRuleCount": matched_rule_count,
            "matchedRuleIds": sorted({
                _text(value)
                for value in execution.get("typedbNativeRuleMatchedRuleIds") or []
                if _text(value)
            })[:160],
            "traceCount": trace_count,
            "relationCount": _integer(inference.get("relationCount")),
            "entityCount": _integer(inference.get("entityCount")),
            "executionStatus": _text(execution.get("status")),
            "nativeRuleTiming": native_rule_timing,
            "nativeStageTimings": native_stage_timings,
            "nativeRulePreflight": {
                "status": _text(execution.get("nativeRulePreflightStatus")),
                "mode": _text(execution.get("nativeRulePreflightMode")),
                "reason": _text(execution.get("nativeRulePreflightReason"))[:220],
                "sourceCount": _integer(execution.get("nativeRulePreflightSourceCount")),
                "loadedSourceCount": _integer(
                    execution.get("nativeRulePreflightLoadedSourceCount")
                ),
                "entityCount": _integer(execution.get("nativeRulePreflightEntityCount")),
                "relationCount": _integer(execution.get("nativeRulePreflightRelationCount")),
            },
            "replayValidation": {
                "version": _text(replay_validation.get("version")),
                "status": _text(replay_validation.get("status")),
                "reason": _text(replay_validation.get("reason"))[:300],
                "verified": bool(replay_validation.get("verified")),
                "selectionApplied": bool(replay_validation.get("selectionApplied")),
                "coverageComplete": bool(replay_validation.get("coverageComplete")),
                "nativeEvaluationComplete": bool(replay_validation.get("nativeEvaluationComplete")),
                "generationAligned": bool(replay_validation.get("generationAligned")),
            },
            "incrementalEquivalenceAudit": {
                "version": _text(equivalence_audit.get("version")),
                "status": _text(equivalence_audit.get("status")),
                "verified": bool(equivalence_audit.get("verified")),
                "reconciledByFullEvaluation": bool(
                    equivalence_audit.get("reconciledByFullEvaluation")
                ),
                "comparedRuleCount": _integer(equivalence_audit.get("comparedRuleCount")),
                "mismatchCount": _integer(equivalence_audit.get("mismatchCount")),
            },
        },
        "abox": {
            "snapshotId": _text(values.get("aboxSnapshotId") or getattr(projection_run, "abox_snapshot_id", "")),
            "entityCount": _integer(values.get("entityCount") or getattr(projection_run, "entity_count", 0)),
            "relationCount": _integer(values.get("relationCount") or getattr(projection_run, "relation_count", 0)),
            "relationPersistence": relation_persistence,
            "cleanup": _cleanup_summary(values),
        },
        "stages": stages,
    }
    observation["slo"] = _slo_state(values, duration_ms, inference, execution, policy)
    return observation


def summarize_projection_runtime_observations(
    observations: Iterable[Mapping[str, object]],
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Summarize newest-first projection observations for diagnostics and SLOs."""

    policy = runtime_slo_policy(settings)
    rows = [dict(item or {}) for item in observations or [] if isinstance(item, Mapping)]
    durations = [_integer(item.get("durationMs")) for item in rows if _integer(item.get("durationMs")) > 0]
    latest = rows[0] if rows else {}
    consecutive = 0
    for item in rows:
        slo = item.get("slo") if isinstance(item.get("slo"), Mapping) else {}
        if _text(slo.get("state")) in {"warning", "critical"}:
            consecutive += 1
        else:
            break
    threshold = _integer(policy.get("consecutiveBreachCount"), 3)
    latest_state = _text((latest.get("slo") or {}).get("state")) if latest else "unavailable"
    state = "unavailable" if not rows else "critical" if latest_state == "critical" else "warning" if consecutive >= threshold else latest_state or "ok"
    sorted_durations = sorted(durations)

    def percentile(fraction: float) -> float:
        if not sorted_durations:
            return 0.0
        index = max(0, min(len(sorted_durations) - 1, int(round((len(sorted_durations) - 1) * fraction))))
        return float(sorted_durations[index])

    breach_count = sum(
        1
        for item in rows
        if _text((item.get("slo") or {}).get("state")) in {"warning", "critical"}
    )
    return {
        "contract": ONTOLOGY_RUNTIME_OBSERVATION_VERSION,
        "status": state,
        "sampleCount": len(rows),
        "latest": latest,
        "averageDurationMs": round(sum(durations) / len(durations), 1) if durations else 0.0,
        "medianDurationMs": percentile(0.5),
        "p90DurationMs": percentile(0.9),
        "p95DurationMs": percentile(0.95),
        "maximumDurationMs": max(durations) if durations else 0,
        "sloBreachRate": round((breach_count / len(rows)) * 100, 1) if rows else 0.0,
        "consecutiveBreachCount": consecutive,
        "sustainedBreach": bool(consecutive >= threshold),
        "sustainedBreachThreshold": threshold,
        "policy": policy,
        "interpretation": (
            "No projection runtime samples are available yet."
            if not rows
            else "Sustained operational SLO breach requires operator attention."
            if consecutive >= threshold
            else "Latest projection and native inference telemetry are within the configured operational policy."
            if latest_state == "ok"
            else "Latest projection recorded an operational warning; it remains observable without changing investment judgement."
        ),
    }
