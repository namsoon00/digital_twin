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
    inference_ms = _integer(execution.get("durationMs") or execution.get("elapsedMs"))
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
    delta = _scope_delta(plan)
    duration_ms = iso_duration_ms(
        getattr(projection_run, "started_at", ""),
        getattr(projection_run, "completed_at", ""),
    )
    policy = runtime_slo_policy(settings)
    trace_count = _integer(inference.get("traceCount"))
    if not trace_count:
        trace_count = len(inference.get("traces") or [])
    matched_rule_count = _integer(execution.get("matchedRuleCount")) or trace_count
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
            "affectedScopeCount": len(delta.get("affectedScopeIds") or []),
            "dependencyAffectedScopeCount": len(delta.get("dependencyAffectedScopeIds") or []),
            "families": list(plan.get("changedScopeFamilies") or []),
            "globalImpact": bool(plan.get("globalImpact")),
        },
        "inference": {
            "status": _text(inference.get("status")),
            "generationId": _text(inference.get("inferenceGenerationId")),
            "generationAligned": bool(inference.get("generationAligned")),
            "nativeTypeDbReasoningUsed": bool(inference.get("nativeTypeDbReasoningUsed")),
            "targetSymbolCount": len(plan.get("inferenceTargetSymbols") or projection_scope.get("targetSymbols") or []),
            "candidateRuleCount": _integer(plan.get("candidateRuleCount")),
            "matchedRuleCount": matched_rule_count,
            "traceCount": trace_count,
            "relationCount": _integer(inference.get("relationCount")),
            "entityCount": _integer(inference.get("entityCount")),
            "executionStatus": _text(execution.get("status")),
        },
        "abox": {
            "snapshotId": _text(values.get("aboxSnapshotId") or getattr(projection_run, "abox_snapshot_id", "")),
            "entityCount": _integer(values.get("entityCount") or getattr(projection_run, "entity_count", 0)),
            "relationCount": _integer(values.get("relationCount") or getattr(projection_run, "relation_count", 0)),
            "cleanup": _cleanup_summary(values),
        },
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
    return {
        "contract": ONTOLOGY_RUNTIME_OBSERVATION_VERSION,
        "status": state,
        "sampleCount": len(rows),
        "latest": latest,
        "averageDurationMs": round(sum(durations) / len(durations), 1) if durations else 0.0,
        "maximumDurationMs": max(durations) if durations else 0,
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
