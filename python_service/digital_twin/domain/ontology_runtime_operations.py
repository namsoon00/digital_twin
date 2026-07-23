"""Operational telemetry contracts for the TypeDB ontology runtime.

This module intentionally observes projection and native inference work after
it has happened.  It never evaluates an investment rule or changes a TypeDB
decision.  MySQL keeps these audit samples; TypeDB remains the compact active
world and inference store.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Mapping


ONTOLOGY_RUNTIME_OBSERVATION_VERSION = "ontology-runtime-observation-v1"
NATIVE_RULE_TIMING_PROFILE_VERSION = "typedb-native-rule-timing-v1"


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
    value = _number((settings or {}).get(key), fallback)
    return max(minimum, min(maximum, value))


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
            120,
            5,
            1800,
        ) * 1000),
        "inferenceSloMs": int(_setting_number(
            configured,
            "ontologyRuntimeInferenceSloSeconds",
            90,
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


def _stage_timings(result: Mapping[str, object]) -> Dict[str, int]:
    raw = result.get("runtimeStages") if isinstance(result, Mapping) else {}
    values = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return {
        _text(key): max(0, _integer(value))
        for key, value in values.items()
        if _text(key)
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
            "incompleteRuleCount": max(0, _integer(existing.get("incompleteRuleCount"))),
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

    def timing_row(item: Mapping[str, object], status: str) -> Dict[str, object]:
        symbols = item.get("candidateSymbols") if isinstance(item.get("candidateSymbols"), list) else []
        return {
            "ruleId": _text(item.get("ruleId")),
            "nativeRuleId": _text(item.get("nativeRuleId")),
            "schemaFunctionName": _text(item.get("schemaFunctionName")),
            "status": status,
            "rowCount": max(0, _integer(item.get("rowCount"))),
            "candidateSymbolCount": len([symbol for symbol in symbols if _text(symbol)]),
            "queryComplexity": max(0, _integer(item.get("queryComplexity"))),
            "queryCount": max(0, _integer(item.get("queryCount"))),
            "anyConditionQueryCount": max(0, _integer(item.get("anyConditionQueryCount"))),
            "elapsedMs": max(0, _integer(item.get("elapsedMs"))),
            "queryDurationMs": max(0, _integer(item.get("queryDurationMs"))),
        }

    rows = [timing_row(item, "ok") for item in executed]
    rows.extend(timing_row(item, _text(item.get("status")) or "blocked") for item in skipped)
    rows.sort(
        key=lambda item: (item["elapsedMs"], item["queryDurationMs"], item["ruleId"]),
        reverse=True,
    )
    bounded = rows[:max(1, min(20, int(limit or 8)))]
    return {
        "version": NATIVE_RULE_TIMING_PROFILE_VERSION,
        "wallClockMs": max(0, _integer(values.get("wallClockMs"))),
        "executedRuleCount": len(executed),
        "incompleteRuleCount": len(skipped),
        # Parallel rule durations overlap; this is a diagnostic total only.
        "aggregateRuleElapsedMs": sum(item["elapsedMs"] for item in rows),
        "aggregateQueryDurationMs": sum(item["queryDurationMs"] for item in rows),
        "slowestRules": bounded,
    }


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
    stages = _stage_timings(values)
    native_rule_timing = native_rule_timing_profile(execution)
    delta = _scope_delta(plan)
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
    observation = {
        "version": ONTOLOGY_RUNTIME_OBSERVATION_VERSION,
        "runId": _text(getattr(projection_run, "run_id", "")),
        "accountId": _text(getattr(projection_run, "account_id", "")),
        "observedAt": _text(getattr(projection_run, "completed_at", "")),
        "status": _text(values.get("status")),
        "graphStore": _text(values.get("graphStore") or getattr(projection_run, "graph_store", "")),
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
        },
        "inference": {
            "status": _text(inference.get("status")),
            "generationId": _text(inference.get("inferenceGenerationId")),
            "generationAligned": bool(inference.get("generationAligned")),
            "nativeTypeDbReasoningUsed": bool(inference.get("nativeTypeDbReasoningUsed")),
            "plannedTargetSymbolCount": len(plan.get("inferenceTargetSymbols") or []),
            "targetSymbolCount": len(actual_target_symbols),
            "targetSymbols": actual_target_symbols[:20],
            "candidateRuleCount": _integer(plan.get("candidateRuleCount")),
            "executedRuleCount": _integer(
                execution.get("typedbNativeRuleExecutedCount")
                or execution.get("nativeRuleSelectionExecutedCount")
            ),
            "deferredRuleCount": _integer(execution.get("nativeRuleSelectionDeferredCount")),
            "nativeRuleSelectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
            "nativeRuleSelectionFallbackReason": _text(execution.get("nativeRuleSelectionFallbackReason")),
            "matchedRuleCount": matched_rule_count,
            "traceCount": trace_count,
            "relationCount": _integer(inference.get("relationCount")),
            "entityCount": _integer(inference.get("entityCount")),
            "executionStatus": _text(execution.get("status")),
            "nativeRuleTiming": native_rule_timing,
        },
        "abox": {
            "snapshotId": _text(values.get("aboxSnapshotId") or getattr(projection_run, "abox_snapshot_id", "")),
            "entityCount": _integer(values.get("entityCount") or getattr(projection_run, "entity_count", 0)),
            "relationCount": _integer(values.get("relationCount") or getattr(projection_run, "relation_count", 0)),
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
