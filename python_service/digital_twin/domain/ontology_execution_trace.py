"""Operational trace contracts for one ontology reasoning generation.

The records in this module explain scheduling and execution after TypeDB has
made its decision. They never evaluate RuleBox conditions or create an
investment action outside the ontology boundary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, List, Mapping


ONTOLOGY_EXECUTION_TRACE_VERSION = "ontology-execution-trace-v2"
TRACE_RETENTION_DAYS = 30
ALERT_READ_SET_RETENTION_DAYS = 180


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _integer(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _enabled(value: object, fallback: bool = False) -> bool:
    clean = _text(value).lower()
    if not clean:
        return fallback
    return clean not in {"0", "false", "no", "off", "disabled"}


def _symbols(values: Iterable[object]) -> List[str]:
    return sorted({
        _text(value).upper()
        for value in values or []
        if _text(value)
    })


def _run_value(run: object, name: str, fallback: object = "") -> object:
    if isinstance(run, Mapping):
        return run.get(name, fallback)
    return getattr(run, name, fallback)


def reasoning_execution_lane(run: object) -> str:
    context = _mapping(_run_value(run, "context_payload", {}))
    request = _mapping(context.get("reasoningRequest"))
    dispatch = _mapping(request.get("queueDispatch"))
    lanes = [
        _text(value)
        for value in dispatch.get("selectedLanes") or []
        if _text(value)
    ]
    if "REALTIME_REASONING" in lanes or "CRITICAL_REASONING" in lanes:
        return "REALTIME_REASONING"
    if "CONTEXT_REASONING" in lanes or "CORE_REASONING" in lanes:
        return "CONTEXT_REASONING"
    if "RECONCILIATION_REASONING" in lanes:
        return "RECONCILIATION_REASONING"
    if request.get("observationFollowupSymbols"):
        return "REALTIME_REASONING"
    return "CONTEXT_REASONING"


def _semantic_stage_status(stage_key: str, result: Mapping[str, object]) -> str:
    execution = _mapping(result.get("ruleboxExecution"))
    inference = _mapping(result.get("inferenceBox"))
    clean = stage_key.lower()
    if "native" in clean or "rule" in clean:
        return _text(execution.get("status")) or _text(result.get("status")) or "unknown"
    if "inferencebox" in clean or "activation" in clean:
        return _text(inference.get("status")) or _text(result.get("status")) or "unknown"
    return _text(result.get("status")) or "unknown"


def reasoning_stage_records(
    run: object,
    result: Mapping[str, object],
    settings: Mapping[str, object] = None,
    rule_trace_summary: Mapping[str, object] = None,
) -> List[Dict[str, object]]:
    values = _mapping(result)
    configured = _mapping(settings)
    context = _mapping(_run_value(run, "context_payload", {}))
    request = _mapping(context.get("reasoningRequest"))
    lane = reasoning_execution_lane(run)
    run_id = _text(_run_value(run, "run_id"))
    world_id = _text(_run_value(run, "world_id"))
    account_id = _text(_run_value(run, "account_id"))
    started_at = _text(_run_value(run, "started_at"))
    completed_at = _text(_run_value(run, "completed_at"))
    source_symbols = _symbols(_run_value(run, "source_symbols", []))
    runtime_stages = _mapping(values.get("runtimeStages"))
    inference_generation_id = _text(
        _mapping(values.get("inferenceBox")).get("inferenceGenerationId")
        or _run_value(run, "inference_generation_id")
    )
    records: List[Dict[str, object]] = []

    def add(
        stage_key: str,
        status: str,
        duration_ms: object = 0,
        detail: Mapping[str, object] = None,
    ) -> None:
        records.append({
            "version": ONTOLOGY_EXECUTION_TRACE_VERSION,
            "runId": run_id,
            "worldId": world_id,
            "accountId": account_id,
            "inferenceGenerationId": inference_generation_id,
            "lane": lane,
            "stageKey": _text(stage_key),
            "stageOrder": len(records),
            "status": _text(status) or "unknown",
            "startedAt": started_at,
            "completedAt": completed_at,
            "durationMs": _integer(duration_ms),
            "inputCount": len(source_symbols),
            "outputCount": 0,
            "detail": dict(detail or {}),
        })

    add("source-fact-capture", "recorded", detail={
        "targetSymbols": source_symbols,
        "sourceSnapshotAt": _text(_run_value(run, "source_snapshot_at")),
        "workClasses": list(request.get("workClasses") or []),
        "impactScopes": list(request.get("impactScopes") or []),
        "reasoningLanes": list(request.get("reasoningLanes") or []),
        "factTypes": list(request.get("factTypes") or []),
        "requestedScopeFamiliesBySymbol": dict(
            request.get("requestedScopeFamiliesBySymbol") or {}
        ),
        "changedFieldsBySymbol": dict(request.get("changedFieldsBySymbol") or {}),
        "revisionVectorsBySymbol": dict(request.get("revisionVectorsBySymbol") or {}),
    })
    projection_scope = _mapping(values.get("projectionScope"))
    target_patch = _mapping(projection_scope.get("targetScopedManifestPatch"))
    scope_trace = _mapping(target_patch.get("scopeSelectionTrace"))
    selected_scope_rows = [
        dict(item)
        for item in (scope_trace.get("selected") or [])[:40]
        if isinstance(item, Mapping)
    ]
    deferred_scope_rows = [
        dict(item)
        for item in (scope_trace.get("deferred") or [])[:40]
        if isinstance(item, Mapping)
    ]
    add("abox-scope-selection", _text(target_patch.get("status")) or "not-run", detail={
        "mode": _text(target_patch.get("mode")),
        "factSlotStatus": _text(target_patch.get("factSlotStatus")),
        "factSlotFamilies": list(target_patch.get("factSlotFamilies") or []),
        "factSlotFamiliesBySymbol": dict(
            target_patch.get("factSlotFamiliesBySymbol") or {}
        ),
        "changedFieldsBySymbol": dict(
            target_patch.get("factSlotChangedFieldsBySymbol") or {}
        ),
        "preciseFieldRoutingSymbols": list(
            target_patch.get("factSlotPreciseFieldRoutingSymbols") or []
        ),
        "unclassifiedChangedFieldsBySymbol": dict(
            target_patch.get("factSlotUnclassifiedChangedFieldsBySymbol") or {}
        ),
        "fallbackReason": _text(
            target_patch.get("factSlotFallbackReason")
            or target_patch.get("fallbackReason")
        ),
        "selectedScopeCount": _integer(target_patch.get("selectedIncomingScopeCount")),
        "deferredScopeCount": _integer(target_patch.get("deferredScopeCount")),
        "selectedScopes": selected_scope_rows,
        "deferredScopes": deferred_scope_rows,
    })
    relation_persistence = _mapping(values.get("relationPersistence"))
    persistence_scopes = [
        dict(item)
        for item in (relation_persistence.get("scopes") or [])[:40]
        if isinstance(item, Mapping)
    ]
    add("abox-persistence", _text(values.get("status")) or "not-run", detail={
        "version": _text(relation_persistence.get("version")),
        "scopeCount": _integer(relation_persistence.get("scopeCount")),
        "remainingScopeCount": _integer(relation_persistence.get("remainingScopeCount")),
        "scopes": persistence_scopes,
    })
    for raw_key, raw_duration in runtime_stages.items():
        stage_key = _text(raw_key)
        if not stage_key or not isinstance(raw_duration, (int, float)):
            continue
        add(
            "runtime:" + stage_key,
            _semantic_stage_status(stage_key, values),
            raw_duration,
            {"runtimeMetric": stage_key},
        )

    execution = _mapping(values.get("ruleboxExecution"))
    for raw_key, raw_duration in _mapping(execution.get("typedbNativeStageTimings")).items():
        stage_key = _text(raw_key)
        if not stage_key or not isinstance(raw_duration, (int, float)):
            continue
        add(
            "typedb-native:" + stage_key,
            _semantic_stage_status(stage_key, values),
            raw_duration,
            {"runtimeMetric": stage_key, "nestedUnder": "nativeInferenceMs"},
        )
    inference = _mapping(values.get("inferenceBox"))
    add("rulebox-selection", _text(execution.get("status")) or "not-run", detail={
        "selectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
        "candidateRuleCount": _integer(execution.get("nativeRuleSelectionCandidateCount")),
        "executedRuleCount": _integer(
            execution.get("typedbNativeRuleExecutedCount")
            or execution.get("nativeRuleSelectionExecutedCount")
        ),
        "deferredRuleCount": _integer(execution.get("nativeRuleSelectionDeferredCount")),
        "traceSummary": dict(rule_trace_summary or {}),
    })
    add("inferencebox-generation", _text(inference.get("status")) or "not-run", detail={
        "generationId": _text(inference.get("inferenceGenerationId")),
        "sourceAboxSnapshotId": _text(inference.get("sourceAboxSnapshotId")),
        "generationAligned": bool(inference.get("generationAligned")),
        "traceCount": _integer(inference.get("traceCount")) or len(inference.get("traces") or []),
    })

    ai_enabled = bool(
        _enabled(configured.get("notificationAiGateEnabled"), True)
        and _integer(configured.get("notificationAiQueueWorkerCount")) > 0
    )
    add(
        "notification-ai",
        "downstream-pending" if ai_enabled else "skipped-disabled",
        detail={
            "model": _text(configured.get("notificationAiModel")),
            "promptStatus": "not-created-in-projection",
            "reason": (
                "AI validation is handled by the downstream notification outbox."
                if ai_enabled
                else "Notification AI gate or all AI workers are disabled."
            ),
        },
    )
    add("notification-delivery", "downstream-pending", detail={
        "reason": "Notification candidates are evaluated after the aligned InferenceBox is available.",
    })
    return records


def _native_execution_payload(result: Mapping[str, object]) -> Dict[str, object]:
    execution = _mapping(result.get("ruleboxExecution"))
    native = _mapping(execution.get("nativeMatchResult"))
    return native or execution


def _rule_run_key(rule_id: str, item: Mapping[str, object], index: int) -> str:
    seed = "|".join([
        rule_id,
        _text(item.get("targetWorkShardIndex")),
        _text(item.get("targetWorkShardCount")),
        _text(item.get("candidateSymbols")),
        str(index),
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _match_symbol(item: Mapping[str, object], source_symbols: Iterable[object]) -> str:
    source_id = _text(item.get("sourceId")).upper()
    if not source_id:
        return ""
    for symbol in _symbols(source_symbols):
        if source_id == symbol or source_id.endswith(":" + symbol):
            return symbol
    return ""


def reasoning_rule_outcome_records(run: object, result: Mapping[str, object]) -> List[Dict[str, object]]:
    values = _mapping(result)
    execution = _mapping(values.get("ruleboxExecution"))
    native = _native_execution_payload(values)
    lane = reasoning_execution_lane(run)
    run_id = _text(_run_value(run, "run_id"))
    world_id = _text(_run_value(run, "world_id"))
    account_id = _text(_run_value(run, "account_id"))
    source_symbols = _symbols(_run_value(run, "source_symbols", []))
    inference_generation_id = _text(
        _mapping(values.get("inferenceBox")).get("inferenceGenerationId")
        or _run_value(run, "inference_generation_id")
    )
    selected_ids = {
        _text(value)
        for value in execution.get("nativeRuleSelectionExecutedRuleIds") or []
        if _text(value)
    }
    deferred_ids = {
        _text(value)
        for value in execution.get("nativeRuleSelectionDeferredRuleIds") or []
        if _text(value)
    }
    candidate_ids = set(selected_ids)
    impact = _mapping(values.get("inferenceImpactPlan"))
    candidate_ids.update(_text(value) for value in impact.get("candidateRuleIds") or [] if _text(value))
    matched_ids = {
        _text(item.get("ruleId"))
        for item in native.get("matches") or []
        if isinstance(item, Mapping) and _text(item.get("ruleId"))
    }
    matched_ids.update(
        _text(value)
        for value in execution.get("typedbNativeRuleMatchedRuleIds") or []
        if _text(value)
    )
    matched_symbols_by_rule: Dict[str, set] = {}
    match_identity_complete_by_rule: Dict[str, bool] = {}
    for item in native.get("matches") or []:
        if not isinstance(item, Mapping):
            continue
        rule_id = _text(item.get("ruleId"))
        if not rule_id:
            continue
        symbol = _match_symbol(item, source_symbols)
        if symbol:
            matched_symbols_by_rule.setdefault(rule_id, set()).add(symbol)
            match_identity_complete_by_rule[rule_id] = True
        else:
            match_identity_complete_by_rule.setdefault(rule_id, False)
    raw_rows = []
    for status_group, rows in (
        ("executed", native.get("executedRules") or execution.get("executedRules") or []),
        ("skipped", native.get("skippedRules") or execution.get("skippedRules") or []),
    ):
        for item in rows:
            if isinstance(item, Mapping) and _text(item.get("ruleId")):
                raw_rows.append((status_group, dict(item)))

    recorded_ids = {_text(item.get("ruleId")) for _group, item in raw_rows}
    for rule_id in sorted((selected_ids | deferred_ids) - recorded_ids):
        raw_rows.append(("deferred" if rule_id in deferred_ids else "selected", {"ruleId": rule_id}))

    records: List[Dict[str, object]] = []
    normal_non_match_statuses = {
        "not-applicable",
        "not-applicable-preflight",
        "planned",
    }
    for index, (status_group, item) in enumerate(raw_rows):
        rule_id = _text(item.get("ruleId"))
        item_status = _text(item.get("status"))
        normalized_item_status = item_status.lower()
        symbols = _symbols(item.get("candidateSymbols") or source_symbols)
        precise_match_symbols = matched_symbols_by_rule.get(rule_id, set())
        has_precise_match_identity = bool(match_identity_complete_by_rule.get(rule_id))
        matched_target_symbols = sorted(set(symbols).intersection(precise_match_symbols))
        record_matched = bool(matched_target_symbols)
        unresolved_match_target = False
        if normalized_item_status in normal_non_match_statuses:
            record_matched = False
            matched_target_symbols = []
        elif rule_id in matched_ids and not has_precise_match_identity:
            # A rule-level match proves that some subject matched, not that
            # every subject in a batched run matched. Legacy rows without a
            # sourceId remain usable only for a single unambiguous target.
            if len(symbols) == 1:
                record_matched = True
                matched_target_symbols = list(symbols)
            else:
                record_matched = False
                matched_target_symbols = []
                unresolved_match_target = True
        if status_group == "executed":
            status = (
                "matched-target-unresolved"
                if unresolved_match_target
                else "matched" if record_matched else "evaluated-no-match"
            )
        elif status_group == "selected":
            status = "selected"
        elif status_group == "deferred":
            status = "deferred"
        else:
            status = item_status or "skipped"
        if rule_id in candidate_ids:
            selected_reason = "changed-rule-dependency"
        elif rule_id in selected_ids:
            selected_reason = "prior-match-coverage"
        elif execution.get("nativeRuleSelectionApplied"):
            selected_reason = "dependency-selection"
        else:
            selected_reason = "complete-catalogue-evaluation"
        elapsed_ms = _integer(item.get("elapsedMs"))
        cost_class = "slow" if elapsed_ms >= 10000 else "moderate" if elapsed_ms >= 3000 else "fast"
        execution_stage = _text(item.get("executionStage")) or "core"
        records.append({
            "version": ONTOLOGY_EXECUTION_TRACE_VERSION,
            "runId": run_id,
            "ruleRunKey": _rule_run_key(rule_id, item, index),
            "worldId": world_id,
            "accountId": account_id,
            "inferenceGenerationId": inference_generation_id,
            "lane": lane,
            "stageKey": "native-rule-evaluation:" + execution_stage,
            "ruleId": rule_id,
            "ruleVersion": _text(item.get("ruleVersion")),
            "status": status,
            "selectedReason": selected_reason,
            "queryMode": _text(
                item.get("anyConditionCheckMode")
                or item.get("nativeExecutionMode")
                or native.get("nativeExecutionMode")
            ),
            "queryCount": _integer(item.get("queryCount")),
            "durationMs": elapsed_ms,
            "queryDurationMs": _integer(item.get("queryDurationMs")),
            "targetSymbols": symbols,
            "matchedTargetSymbols": matched_target_symbols,
            "matched": record_matched,
            "matchIdentityComplete": bool(
                record_matched and matched_target_symbols
            ),
            "reused": status_group == "selected" and rule_id not in candidate_ids,
            "failureReason": _text(item.get("reason"))[:500],
            "costClass": cost_class,
            "detail": {
                key: item.get(key)
                for key in [
                    "nativeRuleId",
                    "typeqlExecutionMode",
                    "rowCount",
                    "queryComplexity",
                    "anyConditionQueryCount",
                    "targetWorkShardIndex",
                    "targetWorkShardCount",
                    "targetWorkShardingUsed",
                    "timeoutFallbackUsed",
                    "executionStage",
                    "failurePolicy",
                    "costHint",
                    "costScore",
                    "supportOnly",
                    "executionProfileVersion",
                ]
                if item.get(key) not in (None, "", [], {})
            } | ({
                "ruleLevelMatched": True,
                "matchIdentityStatus": "target-unresolved",
            } if unresolved_match_target else {}),
        })
    return records


def _detailed_rule_record(item: Mapping[str, object]) -> bool:
    status = _text(item.get("status")).lower()
    failed = any(token in status for token in ("error", "timeout", "failed", "blocked"))
    return bool(
        item.get("matched")
        or _integer(item.get("queryCount"))
        or _integer(item.get("queryDurationMs"))
        or status in {"matched", "evaluated-no-match", "selected"}
        or failed
    )


def reasoning_rule_records(run: object, result: Mapping[str, object]) -> List[Dict[str, object]]:
    """Keep detailed rows only for rules that were queried or need audit."""

    return [
        item
        for item in reasoning_rule_outcome_records(run, result)
        if _detailed_rule_record(item)
    ]


def reasoning_rule_trace_summary(
    outcomes: Iterable[Mapping[str, object]],
    persisted: Iterable[Mapping[str, object]],
    result: Mapping[str, object],
) -> Dict[str, object]:
    outcome_rows = [dict(item) for item in outcomes or []]
    persisted_rows = [dict(item) for item in persisted or []]
    execution = _mapping(_mapping(result).get("ruleboxExecution"))
    impact = _mapping(_mapping(result).get("inferenceImpactPlan"))
    status_counts: Dict[str, int] = {}
    for item in outcome_rows:
        status = _text(item.get("status")) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
    catalogue_count = _integer(
        execution.get("nativeRuleSelectionFullRuleCount")
        or impact.get("enabledRuleCount")
        or len(outcome_rows)
    )
    return {
        "catalogueRuleCount": catalogue_count,
        "outcomeRuleCount": len(outcome_rows),
        "persistedDetailRuleCount": len(persisted_rows),
        "compactedRuleCount": max(0, len(outcome_rows) - len(persisted_rows)),
        "queriedRuleCount": len([item for item in outcome_rows if _integer(item.get("queryCount"))]),
        "matchedRuleCount": len([item for item in outcome_rows if item.get("matched")]),
        "failedRuleCount": len([
            item for item in outcome_rows
            if any(token in _text(item.get("status")).lower() for token in ("error", "timeout", "failed", "blocked"))
        ]),
        "statusCounts": dict(sorted(status_counts.items())),
        "compactionApplied": len(persisted_rows) < len(outcome_rows),
    }


def reasoning_execution_trace_payload(
    run: object,
    result: Mapping[str, object],
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    outcomes = reasoning_rule_outcome_records(run, result)
    rules = [item for item in outcomes if _detailed_rule_record(item)]
    rule_summary = reasoning_rule_trace_summary(outcomes, rules, result)
    stages = reasoning_stage_records(
        run,
        result,
        settings=settings,
        rule_trace_summary=rule_summary,
    )
    return {
        "version": ONTOLOGY_EXECUTION_TRACE_VERSION,
        "runId": _text(_run_value(run, "run_id")),
        "inferenceGenerationId": _text(
            _mapping(result).get("inferenceGenerationId")
            or _mapping(_mapping(result).get("inferenceBox")).get("inferenceGenerationId")
            or _run_value(run, "inference_generation_id")
        ),
        "lane": reasoning_execution_lane(run),
        "stages": stages,
        "rules": rules,
        # Used only by the result-slot writer in the same transaction. MySQL
        # persists the compact ``rules`` rows and the aggregate stage summary.
        "ruleOutcomes": outcomes,
        "summary": {
            "stageCount": len(stages),
            "ruleRunCount": len(rules),
            "ruleOutcomeCount": len(outcomes),
            "compactedRuleCount": rule_summary["compactedRuleCount"],
            "matchedRuleCount": len([item for item in rules if item.get("matched")]),
            "failedRuleCount": len([
                item for item in rules
                if item.get("status") in {"error", "blocked", "query-timeout", "query-error"}
            ]),
            "slowRuleCount": len([item for item in rules if item.get("costClass") == "slow"]),
        },
    }


def trace_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
