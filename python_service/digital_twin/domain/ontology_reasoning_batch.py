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
    pressure = bool(
        pending_requests >= pending_threshold
        or pending_symbols >= pending_threshold
        or oldest_wait >= age_threshold
    )
    runtime_guard = bool(
        recent_status in {"error", "timeout", "partial", "circuit-open"}
        or recent_runtime >= runtime_guard_seconds * 1000
    )
    baseline_target_symbol_count = _recent_target_symbol_count(execution)
    estimated_per_target_runtime_ms = (
        max(1, int((recent_runtime + baseline_target_symbol_count - 1) / baseline_target_symbol_count))
        if recent_runtime > 0
        else 0
    )
    budget_target_limit = hard_limit
    if estimated_per_target_runtime_ms > 0:
        budget_target_limit = max(
            1,
            min(
                hard_limit,
                int((execution_budget_seconds * 1000) / estimated_per_target_runtime_ms),
            ),
        )
    estimated_burst_runtime_ms = (
        estimated_per_target_runtime_ms * burst_limit
        if estimated_per_target_runtime_ms > 0
        else 0
    )
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
    elif pressure and budget_target_limit < burst_limit:
        mode = "runtime-budget-limited"
        target_limit = min(burst_limit, budget_target_limit)
        reason_codes.append("estimated-batch-runtime-budget")
    elif pressure:
        mode = "queue-pressure"
        target_limit = burst_limit
        if pending_requests >= pending_threshold:
            reason_codes.append("pending-request-threshold")
        if pending_symbols >= pending_threshold:
            reason_codes.append("pending-symbol-threshold")
        if oldest_wait >= age_threshold:
            reason_codes.append("oldest-wait-threshold")
    else:
        mode = "steady"
        target_limit = steady_limit
        reason_codes.append("steady-queue")

    return {
        "version": "adaptive-reasoning-batch-v1",
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
        "runtimeGuardSeconds": runtime_guard_seconds,
        "executionBudgetSeconds": execution_budget_seconds,
        "recentStatus": recent_status,
        "recentExecutionSource": recent_execution_source,
        "recentRuntimeMs": recent_runtime,
        "baselineTargetSymbolCount": baseline_target_symbol_count if recent_runtime else 0,
        "estimatedPerTargetRuntimeMs": estimated_per_target_runtime_ms,
        "estimatedBurstRuntimeMs": estimated_burst_runtime_ms,
        "budgetTargetSymbolLimit": budget_target_limit,
        "pressure": pressure,
        "runtimeGuard": runtime_guard,
        "reasonCodes": reason_codes,
    }
