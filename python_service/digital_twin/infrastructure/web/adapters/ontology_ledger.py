"""Web ontology ledger boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_audit_symbols
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_repository_world_call
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_world_id_from_query
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.common import safe_int
from digital_twin.modules.reasoning.domain.ontology_inference_ledger import inference_trace_ledger_payload
from typing import Dict
from typing import List
import json


ONTOLOGY_INFERENCE_LEDGER_READ_MODEL = StaleReadModelCache(
    "ontology-inference-ledger",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


def ontology_inference_ledger_cache_key(symbols: List[str], limit: int, world_id: str) -> str:
    return json.dumps({
        "symbols": sorted(str(item or "").upper() for item in symbols or []),
        "limit": int(limit or 0),
        "worldId": str(world_id or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ontology_inference_graph_read_model(
    symbols: List[str],
    limit: int,
    world_id: str,
) -> Dict[str, object]:
    settings = runtime_settings(fast_operational_read=True)
    repo = ontology_repository_from_settings(settings)
    errors = []
    try:
        rulebox = repo.rulebox_snapshot() if hasattr(repo, "rulebox_snapshot") else {}
    except Exception as error:  # noqa: BLE001 - a partial graph snapshot can still be retained.
        errors.append("RuleBox: " + str(error)[:220])
        rulebox = {"status": "error", "reason": str(error)[:220], "rules": []}
    try:
        inferencebox = ontology_repository_world_call(
            repo,
            "inferencebox_snapshot",
            symbols=symbols,
            limit=limit,
            world_id=world_id,
        ) if hasattr(repo, "inferencebox_snapshot") else {}
    except Exception as error:  # noqa: BLE001 - the prior durable read model remains usable.
        errors.append("InferenceBox: " + str(error)[:220])
        inferencebox = {
            "status": "error",
            "reason": str(error)[:220],
            "graphStore": getattr(repo, "store_key", "typedb"),
            "source": "typedbInferenceBox",
            "entities": [],
            "relations": [],
            "traces": [],
        }
    rulebox_usable = bool((rulebox or {}).get("rules")) or str((rulebox or {}).get("status") or "").lower() in {"ok", "ready", "current"}
    inference_usable = bool(
        (inferencebox or {}).get("entities")
        or (inferencebox or {}).get("relations")
        or (inferencebox or {}).get("traces")
    ) or str((inferencebox or {}).get("status") or "").lower() in {"ok", "ready", "current"}
    if not rulebox_usable and not inference_usable:
        raise RuntimeError("; ".join(errors) or "TypeDB inference read model is unavailable")
    return {
        "rulebox": rulebox,
        "inferencebox": inferencebox,
        "generatedAt": now(),
        "worldId": world_id,
    }


def compact_reasoning_stage_detail(stage_key: str, detail: object) -> Dict[str, object]:
    value = detail if isinstance(detail, dict) else {}
    key = str(stage_key or "")
    if key == "source-fact-capture":
        return {
            "changedFieldsBySymbol": dict(value.get("changedFieldsBySymbol") or {}),
            "factTypes": list(value.get("factTypes") or []),
        }
    if key == "abox-scope-selection":
        return {
            "selectedScopeCount": value.get("selectedScopeCount"),
            "deferredScopeCount": value.get("deferredScopeCount"),
            "factSlotFamilies": list(value.get("factSlotFamilies") or []),
            "selectedScopes": [{
                field: scope.get(field)
                for field in ("symbol", "scopeFamily", "scopeId", "reasons")
                if field in scope
            } for scope in value.get("selectedScopes") or [] if isinstance(scope, dict)],
        }
    if key == "abox-persistence":
        return {
            "scopeCount": value.get("scopeCount"),
            "scopes": [{
                field: scope.get(field)
                for field in ("symbol", "scopeFamily", "scopeId", "requested", "inserted", "reused")
                if field in scope
            } for scope in value.get("scopes") or [] if isinstance(scope, dict)],
        }
    if key == "rulebox-selection":
        return {
            field: value.get(field)
            for field in ("candidateRuleCount", "executedRuleCount", "deferredRuleCount")
            if field in value
        }
    if key.startswith("runtime:"):
        return {
            field: value.get(field)
            for field in ("runtimeMetric", "budgetMs", "ratio", "withinBudget")
            if field in value
        }
    if key == "performance-contract":
        return {
            field: value.get(field)
            for field in (
                "version", "withinBudget", "bottleneckStage", "bottleneckRatio",
                "violations",
            )
            if field in value
        }
    return {}


def compact_reasoning_execution_history(history: object) -> Dict[str, object]:
    payload = dict(history or {}) if isinstance(history, dict) else {}
    compact_runs = []
    for raw_run in payload.get("runs") or []:
        if not isinstance(raw_run, dict):
            continue
        stages = []
        for raw_stage in raw_run.get("stages") or []:
            if not isinstance(raw_stage, dict):
                continue
            stage_key = str(raw_stage.get("stageKey") or "")
            stages.append({
                field: raw_stage.get(field)
                for field in (
                    "stageKey", "status", "durationMs",
                )
                if field in raw_stage
            } | {"detail": compact_reasoning_stage_detail(stage_key, raw_stage.get("detail"))})
        rules = [{
            field: raw_rule.get(field)
            for field in (
                "ruleId", "status", "selectedReason", "durationMs", "failureReason",
            )
            if field in raw_rule
        } for raw_rule in raw_run.get("rules") or [] if isinstance(raw_rule, dict)]
        compact_runs.append({
            field: raw_run.get(field)
            for field in (
                "runId", "worldId", "accountId", "inferenceGenerationId", "lane", "updatedAt",
            )
            if field in raw_run
        } | {"stages": stages, "rules": rules})
    payload["runs"] = compact_runs
    payload["detailLevel"] = "summary"
    payload["fullDetailAvailable"] = True
    return payload


def compact_rule_audit(audit: object) -> Dict[str, object]:
    payload = dict(audit or {}) if isinstance(audit, dict) else {}
    payload["rules"] = [{
        **{
            field: rule.get(field)
            for field in (
                "ruleId", "label", "status", "enabled", "assessmentScope",
                "lifecycleClass", "evaluationGrain", "ownerWorld", "executionCadence",
                "incrementalEligible", "triggerEventClasses", "executionUnit",
                "sampleCount", "matchedCount", "failureCount",
                "averageDurationMs", "p95DurationMs", "maxDurationMs", "reviewReasons",
            )
            if field in rule
        },
        "executionProfile": {
            "executionStage": (
                rule.get("executionProfile").get("executionStage")
                if isinstance(rule.get("executionProfile"), dict)
                else None
            ),
        },
    } for rule in payload.get("rules") or [] if isinstance(rule, dict)]
    payload["detailLevel"] = "summary"
    payload["fullDetailAvailable"] = True
    return payload


def ontology_inference_ledger_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    from digital_twin.modules.model_registry.domain.ontology_rule_audit import rule_audit_payload

    settings = operational_read_settings()
    symbols = ontology_audit_symbols(query)
    limit = safe_int(first_query(query, "limit"), 80, 1, 300)
    world_id = ontology_world_id_from_query(query)
    cache_key = ontology_inference_ledger_cache_key(symbols, limit, world_id)
    loader = lambda: ontology_inference_graph_read_model(symbols, limit, world_id)
    direct = request_bool(first_query(query, "direct"), False)
    if direct:
        read_model = ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.refresh(cache_key, loader)
    else:
        # HTTP reads must never run a TypeDB graph scan inside the web process.
        # A direct diagnostic can refresh the durable snapshot explicitly;
        # ordinary screens keep serving the last good graph plus MySQL history.
        read_model = ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.snapshot(cache_key)
        if (not read_model.get("hasData") or read_model.get("stale")) and not read_model.get("retryAfterSeconds"):
            ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.refresh_async(cache_key, loader)
            read_model = ONTOLOGY_INFERENCE_LEDGER_READ_MODEL.snapshot(cache_key)
    graph_payload = read_model.get("payload") if isinstance(read_model.get("payload"), dict) else {}
    rulebox = graph_payload.get("rulebox") if isinstance(graph_payload.get("rulebox"), dict) else {
        "status": "deferred",
        "reason": str(read_model.get("lastError") or "TypeDB snapshot refresh is pending."),
        "rules": [],
    }
    inferencebox = graph_payload.get("inferencebox") if isinstance(graph_payload.get("inferencebox"), dict) else {
        "status": "deferred",
        "reason": str(read_model.get("lastError") or "TypeDB snapshot refresh is pending."),
        "graphStore": "typedb",
        "source": "persistentReadModel",
        "entities": [],
        "relations": [],
        "traces": [],
    }
    payload = inference_trace_ledger_payload(inferencebox, rulebox=rulebox, symbols=symbols, limit=limit)
    payload["ruleboxStatus"] = rulebox.get("status")
    payload["ruleboxReason"] = rulebox.get("reason")
    payload["worldId"] = world_id
    try:
        execution_store = stores.ontology_projection_run_store(settings)
        execution_history = execution_store.execution_trace(
            run_id=str(first_query(query, "runId") or ""),
            account_id=str(first_query(query, "accountId") or first_query(query, "account") or ""),
            world_id=world_id,
            limit=safe_int(first_query(query, "runLimit"), 12, 1, 50),
        )
        if str(first_query(query, "historyDetail") or "summary").strip().lower() != "full":
            execution_history = compact_reasoning_execution_history(execution_history)
        elif isinstance(execution_history, dict):
            execution_history["detailLevel"] = "full"
        payload["executionHistory"] = execution_history
        payload["ruleRuntimeSummary"] = execution_store.rule_runtime_summary(
            account_id=str(first_query(query, "accountId") or first_query(query, "account") or ""),
            world_id=world_id,
            limit=safe_int(first_query(query, "ruleSampleLimit"), 500, 100, 10000),
        )
        payload["ruleResultSlots"] = execution_store.rule_result_slot_summary(
            account_id=str(first_query(query, "accountId") or first_query(query, "account") or ""),
            world_id=world_id,
            symbols=symbols,
            limit=safe_int(first_query(query, "slotLimit"), 500, 100, 10000),
            execution_namespace_id=str(first_query(query, "executionNamespaceId") or ""),
        )
        payload["ruleAudit"] = rule_audit_payload(
            rulebox.get("rules") or [],
            payload["ruleRuntimeSummary"],
        )
        if str(first_query(query, "auditDetail") or "summary").strip().lower() != "full":
            payload["ruleAudit"] = compact_rule_audit(payload["ruleAudit"])
        elif isinstance(payload["ruleAudit"], dict):
            payload["ruleAudit"]["detailLevel"] = "full"
    except Exception as error:  # noqa: BLE001 - active InferenceBox trace remains readable.
        payload["executionHistory"] = {
            "status": "error",
            "reason": str(error)[:220],
            "runCount": 0,
            "runs": [],
        }
        payload["ruleRuntimeSummary"] = {
            "status": "error",
            "reason": str(error)[:220],
            "sampleCount": 0,
            "ruleCount": 0,
            "rules": [],
        }
        payload["ruleResultSlots"] = {
            "status": "error",
            "reason": str(error)[:220],
            "slotCount": 0,
            "symbolCount": 0,
            "symbols": [],
        }
        payload["ruleAudit"] = rule_audit_payload(rulebox.get("rules") or [], {})
        if str(first_query(query, "auditDetail") or "summary").strip().lower() != "full":
            payload["ruleAudit"] = compact_rule_audit(payload["ruleAudit"])
    operational_count = sum([
        int((payload.get("executionHistory") or {}).get("runCount") or 0),
        int((payload.get("ruleRuntimeSummary") or {}).get("sampleCount") or 0),
        int((payload.get("ruleResultSlots") or {}).get("slotCount") or 0),
    ])
    has_graph = bool(read_model.get("hasData"))
    stale_graph = bool(read_model.get("stale"))
    refreshing = bool(read_model.get("refreshing"))
    usable = has_graph or operational_count > 0
    status = (
        "stale" if has_graph and stale_graph
        else "ok" if has_graph
        else "degraded" if operational_count > 0
        else "refreshing" if refreshing
        else "unavailable"
    )
    dependency_status = (
        "stale" if has_graph and stale_graph
        else "available" if has_graph
        else "refreshing" if refreshing
        else "unavailable"
    )
    payload.update({
        "status": status,
        "usable": usable,
        "retryable": not has_graph,
        "generatedAt": now(),
        "dataFreshness": {
            "status": "stale" if stale_graph else "fresh" if has_graph else "unavailable",
            "ageSeconds": int(read_model.get("ageSeconds") or 0),
            "lastSuccessAt": str(read_model.get("lastSuccessAt") or ""),
            "source": "persistent-read-model" if has_graph else "mysql-execution-history",
        },
        "dependencyStatus": {
            "typedb": {
                "status": dependency_status,
                "refreshing": refreshing,
                "lastAttemptAt": str(read_model.get("lastAttemptAt") or ""),
                "lastError": str(read_model.get("lastError") or ""),
                "retryAfterSeconds": int(read_model.get("retryAfterSeconds") or 0),
                "refreshMode": "direct-diagnostic" if direct else "stale-while-revalidate",
            }
        },
    })
    if direct and not usable:
        payload["error"] = str(read_model.get("lastError") or "TypeDB inference API is unavailable.")
    return payload
