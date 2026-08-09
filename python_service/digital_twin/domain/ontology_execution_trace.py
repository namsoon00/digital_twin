"""Operational trace contracts for one ontology reasoning generation.

The records in this module explain scheduling and execution after TypeDB has
made its decision. They never evaluate RuleBox conditions or create an
investment action outside the ontology boundary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, List, Mapping


ONTOLOGY_EXECUTION_TRACE_VERSION = "ontology-execution-trace-v1"
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
    if "CRITICAL_REASONING" in lanes:
        return "CRITICAL_REASONING"
    if "CORE_REASONING" in lanes:
        return "CORE_REASONING"
    if request.get("observationFollowupSymbols"):
        return "CRITICAL_REASONING"
    return "CORE_REASONING"


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
) -> List[Dict[str, object]]:
    values = _mapping(result)
    configured = _mapping(settings)
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
    inference = _mapping(values.get("inferenceBox"))
    add("rulebox-selection", _text(execution.get("status")) or "not-run", detail={
        "selectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
        "candidateRuleCount": _integer(execution.get("nativeRuleSelectionCandidateCount")),
        "executedRuleCount": _integer(
            execution.get("typedbNativeRuleExecutedCount")
            or execution.get("nativeRuleSelectionExecutedCount")
        ),
        "deferredRuleCount": _integer(execution.get("nativeRuleSelectionDeferredCount")),
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


def reasoning_rule_records(run: object, result: Mapping[str, object]) -> List[Dict[str, object]]:
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
    for index, (status_group, item) in enumerate(raw_rows):
        rule_id = _text(item.get("ruleId"))
        item_status = _text(item.get("status"))
        if status_group == "executed":
            status = "matched" if rule_id in matched_ids else "evaluated-no-match"
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
        symbols = _symbols(item.get("candidateSymbols") or source_symbols)
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
            "matched": rule_id in matched_ids,
            "reused": status_group == "selected" and rule_id not in candidate_ids,
            "failureReason": _text(item.get("reason"))[:500],
            "costClass": cost_class,
            "detail": {
                key: item.get(key)
                for key in [
                    "nativeRuleId",
                    "schemaFunctionName",
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
            },
        })
    return records


def reasoning_execution_trace_payload(
    run: object,
    result: Mapping[str, object],
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    stages = reasoning_stage_records(run, result, settings=settings)
    rules = reasoning_rule_records(run, result)
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
        "summary": {
            "stageCount": len(stages),
            "ruleRunCount": len(rules),
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
