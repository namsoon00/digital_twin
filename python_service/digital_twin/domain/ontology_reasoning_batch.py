"""Operational batching policy for durable ontology reasoning work.

This module chooses a bounded TypeDB target set from queue pressure and
recent runtime evidence. It never evaluates investment facts or TypeDB rules;
it only controls how many already-requested subjects share one coherent ABox
and InferenceBox generation.
"""

from __future__ import annotations

from typing import Dict, Mapping


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def _enabled(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    return default if not text else text not in DISABLED_VALUES


def _integer(value: object, fallback: int, lower: int, upper: int) -> int:
    try:
        parsed = int(float(str(value if value is not None else "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


def _recent_runtime_ms(execution: Mapping[str, object]) -> int:
    values = dict(execution or {}) if isinstance(execution, Mapping) else {}
    stage = values.get("stageTiming")
    stage = stage if isinstance(stage, Mapping) else {}
    raw = stage.get("monitorAndProjectionMs") or values.get("durationMs") or 0
    return _integer(raw, 0, 0, 60 * 60 * 1000)


def _recent_target_symbol_count(execution: Mapping[str, object]) -> int:
    values = dict(execution or {}) if isinstance(execution, Mapping) else {}
    projection = values.get("projectionRuntime")
    projection = projection if isinstance(projection, Mapping) else {}
    return _integer(projection.get("targetSymbolCount"), 1, 1, 200)


def _projection_runtime_ms(execution: Mapping[str, object]) -> int:
    """Return the TypeDB projection duration when the audit recorded one.

    ``monitorAndProjectionMs`` is still the right value for the hard runtime
    guard: it measures the whole isolated turn.  It is not, however, a useful
    *per-target* cost.  Monitor preparation and the ABox/InferenceBox
    generation boundary are shared by every target in a coherent batch.
    """
    values = dict(execution or {}) if isinstance(execution, Mapping) else {}
    projection = values.get("projectionRuntime")
    projection = projection if isinstance(projection, Mapping) else {}
    return _integer(projection.get("durationMs"), 0, 0, 60 * 60 * 1000)


def _native_inference_runtime_ms(execution: Mapping[str, object]) -> int:
    values = dict(execution or {}) if isinstance(execution, Mapping) else {}
    projection = values.get("projectionRuntime")
    projection = projection if isinstance(projection, Mapping) else {}
    return _integer(projection.get("nativeInferenceMs"), 0, 0, 60 * 60 * 1000)


def _target_parallelism(settings: Mapping[str, object], hard_limit: int) -> int:
    configured = dict(settings or {}) if isinstance(settings, Mapping) else {}
    return _integer(
        configured.get("typedbNativeRuleTargetParallelism"),
        1,
        1,
        max(1, int(hard_limit or 1)),
    )


def _ceil_divide(value: int, divisor: int) -> int:
    clean_value = max(0, int(value or 0))
    clean_divisor = max(1, int(divisor or 1))
    return (clean_value + clean_divisor - 1) // clean_divisor


def adaptive_reasoning_batch_plan(
    settings: Mapping[str, object],
    *,
    native_rule_execution: bool,
    hard_target_symbol_limit: int,
    pending_request_count: int,
    pending_symbol_count: int,
    oldest_wait_seconds: int,
    recent_execution: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Return an explainable, bounded execution plan for one queue turn.

    Normal turns retain a small native-rule subject set. When latest-state
    work has accumulated, the runner can process a few independent symbols in
    one coherent graph generation. A slow, failed, or timed-out preceding
    projection immediately returns to the steady size.
    """
    configured = dict(settings or {})
    hard_limit = _integer(hard_target_symbol_limit, 1, 1, 200)
    pending_requests = _integer(pending_request_count, 0, 0, 100000)
    pending_symbols = _integer(pending_symbol_count, 0, 0, 100000)
    oldest_wait = _integer(oldest_wait_seconds, 0, 0, 7 * 24 * 60 * 60)
    execution = dict(recent_execution or {}) if isinstance(recent_execution, Mapping) else {}
    recent_status = str(execution.get("status") or "").strip().lower()
    recent_runtime = _recent_runtime_ms(execution)
    recent_execution_source = str(execution.get("batchRuntimeEvidenceSource") or "").strip()[:40]

    adaptive_enabled = bool(native_rule_execution) and _enabled(
        configured.get("ontologyReasoningAdaptiveBatchEnabled"),
        # Runtime settings opt in by default. Direct compatibility callers
        # without the new key retain their existing static target cap.
        False,
    )
    steady_limit = min(
        hard_limit,
        _integer(configured.get("ontologyReasoningAdaptiveBatchSteadySymbols"), 1, 1, 200),
    )
    burst_limit = min(
        hard_limit,
        _integer(configured.get("ontologyReasoningAdaptiveBatchBurstSymbols"), 3, 1, 200),
    )
    pending_threshold = _integer(
        configured.get("ontologyReasoningAdaptiveBatchPendingThreshold"),
        4,
        1,
        100000,
    )
    age_threshold = _integer(
        configured.get("ontologyReasoningAdaptiveBatchAgeSeconds"),
        60,
        1,
        24 * 60 * 60,
    )
    runtime_guard_seconds = _integer(
        configured.get("ontologyReasoningAdaptiveBatchRuntimeGuardSeconds"),
        180,
        10,
        1800,
    )
    execution_budget_seconds = _integer(
        configured.get("ontologyReasoningAdaptiveBatchBudgetSeconds"),
        150,
        30,
        1800,
    )
    backlog_burst_enabled = _enabled(
        configured.get("ontologyReasoningAdaptiveBatchBacklogBurstEnabled"),
        # Keep direct compatibility callers on the measured one-subject ramp
        # unless the runtime explicitly enables the backlog escape policy.
        False,
    )
    backlog_burst_age_seconds = _integer(
        configured.get("ontologyReasoningAdaptiveBatchBacklogBurstAgeSeconds"),
        120,
        10,
        7 * 24 * 60 * 60,
    )
    pressure = bool(
        pending_requests >= pending_threshold
        or pending_symbols >= pending_threshold
        or oldest_wait >= age_threshold
    )
    # The ordinary pressure path grows one target at a time so a newly
    # observed workload can be measured safely.  That policy is harmful once
    # an already measured queue is minutes behind: it guarantees multiple
    # full ABox generations before the burst ceiling is reached.  The escape
    # path below still obeys the measured runtime budget and runtime guard;
    # it only bypasses the artificial one-target ramp.
    backlog_escape = bool(
        backlog_burst_enabled
        and oldest_wait >= backlog_burst_age_seconds
    )
    runtime_guard = bool(
        recent_status in {"error", "timeout", "partial", "circuit-open"}
        or recent_runtime >= runtime_guard_seconds * 1000
    )
    baseline_target_symbol_count = _recent_target_symbol_count(execution)
    # Keep the legacy average for compatible telemetry, but do not use it to
    # price a coherent multi-target generation.  A one-target observation
    # contains large shared costs (snapshot assembly, ABox persistence,
    # activation, durable proof readback). Treating all of that as a marginal
    # per-target cost permanently prevents the configured burst size from ever
    # being exercised.
    estimated_per_target_runtime_ms = (
        max(1, int((recent_runtime + baseline_target_symbol_count - 1) / baseline_target_symbol_count))
        if recent_runtime > 0
        else 0
    )
    projection_runtime_ms = _projection_runtime_ms(execution)
    native_inference_runtime_ms = min(
        projection_runtime_ms,
        _native_inference_runtime_ms(execution),
    )
    target_parallelism = _target_parallelism(configured, hard_limit)
    estimate_basis_runtime_ms = projection_runtime_ms or recent_runtime
    estimate_basis = "projection-runtime" if projection_runtime_ms else (
        "monitor-and-projection-runtime" if recent_runtime else "unavailable"
    )
    observed_target_waves = _ceil_divide(
        baseline_target_symbol_count,
        target_parallelism,
    )
    estimated_fixed_runtime_ms = 0
    estimated_incremental_target_runtime_ms = 0
    detailed_estimate_available = bool(
        projection_runtime_ms > 0
        and native_inference_runtime_ms > 0
    )
    if detailed_estimate_available:
        estimated_fixed_runtime_ms = max(
            0,
            estimate_basis_runtime_ms - native_inference_runtime_ms,
        )
        # Native target work is intentionally the only marginal term. The
        # fixed generation terms are paid once, and targets in the same
        # configured parallel wave share their native wall-clock budget.
        estimated_incremental_target_runtime_ms = max(
            1,
            _ceil_divide(native_inference_runtime_ms, observed_target_waves),
        )

    def estimated_runtime_for_targets(target_count: int) -> int:
        if target_count <= 0 or estimate_basis_runtime_ms <= 0:
            return 0
        if not detailed_estimate_available:
            return estimated_per_target_runtime_ms * target_count
        return (
            estimated_fixed_runtime_ms
            + estimated_incremental_target_runtime_ms
            * _ceil_divide(target_count, target_parallelism)
        )

    budget_target_limit = hard_limit
    if estimate_basis_runtime_ms > 0:
        budget_target_limit = 1
        for candidate_limit in range(1, hard_limit + 1):
            if estimated_runtime_for_targets(candidate_limit) <= execution_budget_seconds * 1000:
                budget_target_limit = candidate_limit
            else:
                break
    estimated_burst_runtime_ms = estimated_runtime_for_targets(burst_limit)
    # Grow a measured batch by one subject at a time. This makes a pressure
    # burst observable before it reaches the configured hard cap and keeps a
    # one-target baseline from jumping directly to an unmeasured three-target
    # generation.
    ramp_target_limit = burst_limit
    if detailed_estimate_available and baseline_target_symbol_count < burst_limit:
        ramp_target_limit = min(burst_limit, baseline_target_symbol_count + 1)
    reason_codes = []

    if not adaptive_enabled:
        mode = "static"
        target_limit = hard_limit
        reason_codes.append("adaptive-batching-disabled-or-nonnative")
    elif runtime_guard:
        mode = "runtime-protected"
        target_limit = steady_limit
        if recent_status:
            reason_codes.append("recent-status-" + recent_status)
        if recent_runtime >= runtime_guard_seconds * 1000:
            reason_codes.append("recent-runtime-guard")
    elif pressure and not recent_runtime:
        # A new runtime has no observed per-target cost yet. Record one
        # ordinary generation first instead of spending the isolation timeout
        # budget on an unmeasured burst.
        mode = "baseline-collection"
        target_limit = steady_limit
        reason_codes.append("runtime-baseline-unavailable")
    elif backlog_escape:
        mode = "backlog-escape"
        target_limit = min(burst_limit, budget_target_limit)
        reason_codes.append("backlog-burst-oldest-wait")
        if budget_target_limit < burst_limit:
            reason_codes.append("estimated-batch-runtime-budget")
    elif pressure and budget_target_limit < ramp_target_limit:
        mode = "runtime-budget-limited"
        target_limit = min(ramp_target_limit, budget_target_limit)
        reason_codes.append("estimated-batch-runtime-budget")
    elif pressure:
        mode = "queue-pressure-ramp" if ramp_target_limit < burst_limit else "queue-pressure"
        target_limit = ramp_target_limit
        if pending_requests >= pending_threshold:
            reason_codes.append("pending-request-threshold")
        if pending_symbols >= pending_threshold:
            reason_codes.append("pending-symbol-threshold")
        if oldest_wait >= age_threshold:
            reason_codes.append("oldest-wait-threshold")
        if ramp_target_limit < burst_limit:
            reason_codes.append("measured-target-ramp")
    else:
        mode = "steady"
        target_limit = steady_limit
        reason_codes.append("steady-queue")

    return {
        "version": "adaptive-reasoning-batch-v2",
        "enabled": adaptive_enabled,
        "mode": mode,
        "targetSymbolLimit": max(1, min(hard_limit, target_limit)),
        "hardTargetSymbolLimit": hard_limit,
        "steadyTargetSymbolLimit": steady_limit,
        "burstTargetSymbolLimit": burst_limit,
        "pendingRequestCount": pending_requests,
        "pendingSymbolCount": pending_symbols,
        "oldestWaitSeconds": oldest_wait,
        "pendingThreshold": pending_threshold,
        "ageThresholdSeconds": age_threshold,
        "backlogBurstEnabled": backlog_burst_enabled,
        "backlogBurstAgeSeconds": backlog_burst_age_seconds,
        "backlogEscape": backlog_escape,
        "runtimeGuardSeconds": runtime_guard_seconds,
        "executionBudgetSeconds": execution_budget_seconds,
        "recentStatus": recent_status,
        "recentExecutionSource": recent_execution_source,
        "recentRuntimeMs": recent_runtime,
        "baselineTargetSymbolCount": baseline_target_symbol_count if recent_runtime else 0,
        "estimatedPerTargetRuntimeMs": estimated_per_target_runtime_ms,
        "runtimeEstimateBasis": estimate_basis,
        "runtimeEstimateBasisMs": estimate_basis_runtime_ms,
        "targetParallelism": target_parallelism,
        "estimatedFixedRuntimeMs": estimated_fixed_runtime_ms,
        "estimatedIncrementalTargetRuntimeMs": estimated_incremental_target_runtime_ms,
        "rampTargetSymbolLimit": ramp_target_limit,
        "estimatedBurstRuntimeMs": estimated_burst_runtime_ms,
        "budgetTargetSymbolLimit": budget_target_limit,
        "pressure": pressure,
        "runtimeGuard": runtime_guard,
        "reasonCodes": reason_codes,
    }
