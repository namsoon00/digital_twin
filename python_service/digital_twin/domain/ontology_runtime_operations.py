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
NATIVE_REPLAY_VALIDATION_VERSION = "typedb-native-replay-validation-v1"
SCOPED_ABOX_MAINTENANCE_POLICY_VERSION = "typedb-scoped-abox-maintenance-policy-v2"
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
        40,
        1,
        20000,
    ))
    critical_count = max(
        warning_count + 1,
        _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceCriticalInactiveManifestCount",
            120,
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
            8,
            1,
            10,
        )),
        "maxDeleteBatchesPerRun": max_delete_batches,
        "deleteBatchSize": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceDeleteBatchSize",
            50,
            10,
            500,
        )),
        "keepInactiveManifestCount": _integer(_setting_number(
            configured,
            "ontologyAboxMaintenanceKeepInactiveManifestCount",
            0,
            0,
            5,
        )),
        "warningInactiveManifestCount": warning_count,
        "criticalInactiveManifestCount": critical_count,
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
    warning_count = max(1, _integer(resolved_policy.get("warningInactiveManifestCount") or 40))
    critical_count = max(warning_count + 1, _integer(
        resolved_policy.get("criticalInactiveManifestCount") or 120
    ))
    if storage_status in {"error", "disabled", "driver-missing", "unavailable"}:
        return {
            "status": "warning",
            "state": "unavailable",
            "inactiveManifestCount": inactive_count,
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
    execution can only be reused when the preceding complete native proof is
    present and aligned. The function merely classifies persisted TypeDB
    evidence; it never evaluates a RuleBox condition in Python.
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
    if selection_applied:
        verified = bool(
            native_evaluation_complete
            and generation_aligned
            and coverage_complete
            and proof_verified
        )
        status = "verified-prior-coverage" if verified else "incomplete-coverage"
        if verified:
            reason = "Dependency-selected native execution is backed by an aligned prior complete TypeDB proof."
        elif not proof_verified:
            reason = "Dependency-selected execution is missing an aligned prior complete TypeDB proof."
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
    runtime_identity = values.get("runtimeIdentity")
    runtime_identity = dict(runtime_identity or {}) if isinstance(runtime_identity, Mapping) else {}
    target_patch = projection_scope.get("targetScopedManifestPatch")
    target_patch = dict(target_patch or {}) if isinstance(target_patch, Mapping) else {}
    stages = _stage_timings(values)
    native_rule_timing = native_rule_timing_profile(execution)
    delta = _scope_delta(plan)
    impact_diagnostics = _impact_diagnostics(plan)
    replay_validation = values.get("nativeReplayValidation")
    replay_validation = (
        dict(replay_validation)
        if isinstance(replay_validation, Mapping)
        else native_replay_validation(values)
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
                "fullReconcileMinutes": _number(target_patch.get("fullReconcileMinutes")),
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
            "targetParallelism": _integer(execution.get("typedbNativeRuleTargetParallelism")),
            "targetWorkShardingUsed": bool(execution.get("typedbNativeRuleTargetWorkShardingUsed")),
            "targetWorkShardCount": _integer(execution.get("typedbNativeRuleTargetWorkShardCount")),
            "targetWorkItemCount": _integer(execution.get("typedbNativeRuleWorkItemCount")),
            "commitMode": _text(execution.get("typedbNativeRuleCommitMode")),
            "deferredRuleCount": _integer(execution.get("nativeRuleSelectionDeferredCount")),
            "nativeRuleSelectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
            "nativeRuleSelectionFallbackReason": _text(execution.get("nativeRuleSelectionFallbackReason")),
            "matchedRuleCount": matched_rule_count,
            "traceCount": trace_count,
            "relationCount": _integer(inference.get("relationCount")),
            "entityCount": _integer(inference.get("entityCount")),
            "executionStatus": _text(execution.get("status")),
            "nativeRuleTiming": native_rule_timing,
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
