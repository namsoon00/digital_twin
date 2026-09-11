"""native_execution: bridge through explicit injected capabilities."""

from digital_twin.modules.model_registry.contracts import MODEL_SIGNAL_BRIDGE_VERSION, model_signal_bridge_conditions, model_signal_conditions, model_signal_interpretation_contract_id
from digital_twin.modules.reasoning.infrastructure.typeql.model_signal_queries import (
    typedb_dispatch_model_signal_bridge_rows,
    typedb_model_signal_bridge_batch_query,
)
from digital_twin.modules.reasoning.infrastructure.typeql.planning import (
    typedb_rule_execution_profile_fields,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import typedb_native_rule_id
from typing import Dict, Iterable, List, Set
import time
from .bridge_ports import NativeExecutionBridgeStore, NativeExecutionBridgeRuntime


def execute_typedb_model_signal_bridge_batches(
    _store: NativeExecutionBridgeStore,
    batches: Iterable[Dict[str, object]],
    *,
    world_id: str,
    imported,
    transaction_type,
    deadline: float,
    evidence_read_index: Dict[str, object] = None,
    _bindings: NativeExecutionBridgeRuntime
) -> Dict[str, object]:
    """Execute one read per simple model-signal source-scope batch."""

    batch_rows = [dict(item or {}) for item in batches or []]
    if not batch_rows:
        return {
            "status": "ok",
            "readTransactionCount": 0,
            "readQueryCount": 0,
            "executedRules": [],
            "failures": [],
            "dispatchedMatches": [],
            "ignoredContractIds": [],
            "sourceRowCount": 0,
            "dispatchedMatchCount": 0,
            "matchedContractIds": [],
            "matchedSymbols": [],
            "indexedEvidenceReadCount": 0,
        }
    driver = _store.open_native_rule_read_driver(
        imported,
        request_timeout_seconds=max(
            _store.native_rule_query_timeout_seconds(),
            min(120.0, max(1.0, deadline - time.monotonic())),
        ),
    )
    read_transaction_count = 0
    read_query_count = 0
    executed_rules: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    dispatched_matches: List[Dict[str, object]] = []
    ignored_contract_ids: Set[str] = set()
    source_row_count = 0
    matched_contract_ids: Set[str] = set()
    matched_symbols: Set[str] = set()
    indexed_evidence_read_count = 0
    try:
        _store.ensure_database(driver)
        for batch_index, batch in enumerate(batch_rows):
            entries = [dict(item or {}) for item in batch.get("entries") or []]
            batch_started = time.perf_counter()
            remaining_seconds = deadline - time.monotonic()
            query_plan = typedb_model_signal_bridge_batch_query(
                batch,
                world_id=world_id,
                # Shared bridges are a v2 scoped-world feature. Their
                # source, signal entity, and assertion must all resolve
                # through the active scope pointers even when the source
                # predicate itself came from a direct TypeQL query.
                scoped_manifest_only=True,
                evidence_read_index=evidence_read_index,
            )
            if remaining_seconds <= 0.5 or not query_plan.get("query"):
                status = "deferred-by-runtime-budget" if remaining_seconds <= 0.5 else "blocked"
                reason = (
                    "TypeDB native-rule realtime execution budget is exhausted."
                    if remaining_seconds <= 0.5
                    else str(
                        query_plan.get("reason")
                        or "Shared model-signal bridge query could not be built."
                    )
                )
                for entry in entries:
                    rule = entry.get("rule")
                    failures.append(
                        {
                            "ruleId": str(getattr(rule, "rule_id", "") or ""),
                            "status": status,
                            "reason": reason[:220],
                            "candidateSymbols": list(entry.get("candidateSymbols") or []),
                            "sharedModelSignalBridgeBatch": True,
                            "bridgeSourceScope": str(batch.get("sourceScope") or ""),
                            "indexedEvidenceQueryUsed": bool(
                                query_plan.get("indexedEvidenceQuery")
                            ),
                            **typedb_rule_execution_profile_fields(entry),
                        }
                    )
                continue
            query_timeout = min(
                _store.native_rule_query_timeout_seconds(),
                remaining_seconds,
            )
            query_duration_ms = 0
            try:
                with driver.transaction(
                    _store.database,
                    transaction_type.READ,
                    _store.read_transaction_options(query_timeout),
                ) as tx:
                    query_started = time.perf_counter()
                    try:
                        rows = _store.read_rows_in_transaction(
                            tx,
                            str(query_plan.get("query")),
                            query_plan.get("columns") or [],
                            label=("modelSignalBridgeBatch:" + str(batch.get("sourceScope") or "")),
                            timeout_seconds=query_timeout,
                        )
                    finally:
                        query_duration_ms = int((time.perf_counter() - query_started) * 1000)
                read_transaction_count += 1
                read_query_count += 1
            except Exception as error:  # noqa: BLE001 - one bridge gap blocks complete coverage.
                status = (
                    "query-timeout"
                    if _bindings.typedb_error_code(error) == "typedbTimeout"
                    else "query-error"
                )
                for entry in entries:
                    rule = entry.get("rule")
                    failures.append(
                        {
                            "ruleId": str(getattr(rule, "rule_id", "") or ""),
                            "status": status,
                            "reason": str(error)[:220],
                            "candidateSymbols": list(entry.get("candidateSymbols") or []),
                            "queryDurationMs": query_duration_ms,
                            "sharedModelSignalBridgeBatch": True,
                            "bridgeSourceScope": str(batch.get("sourceScope") or ""),
                            "indexedEvidenceQueryUsed": bool(
                                query_plan.get("indexedEvidenceQuery")
                            ),
                            **typedb_rule_execution_profile_fields(entry),
                        }
                    )
                continue
            if query_plan.get("indexedEvidenceQuery"):
                indexed_evidence_read_count += 1
            dispatch = typedb_dispatch_model_signal_bridge_rows(batch, rows)
            source_row_count += len(rows)
            ignored_contract_ids.update(dispatch.get("ignoredContractIds") or [])
            if str(dispatch.get("status") or "") != "ok":
                reason = "; ".join(str(item) for item in dispatch.get("failures") or [])
                for entry in entries:
                    rule = entry.get("rule")
                    failures.append(
                        {
                            "ruleId": str(getattr(rule, "rule_id", "") or ""),
                            "status": "contract-integrity-error",
                            "reason": reason[:220],
                            "candidateSymbols": list(entry.get("candidateSymbols") or []),
                            "queryDurationMs": query_duration_ms,
                            "sharedModelSignalBridgeBatch": True,
                            "bridgeSourceScope": str(batch.get("sourceScope") or ""),
                            **typedb_rule_execution_profile_fields(entry),
                        }
                    )
                continue
            dispatched = [dict(item or {}) for item in dispatch.get("matches") or []]
            matched_count_by_rule_id: Dict[str, int] = {}
            for item in dispatched:
                entry = dict(item.get("entry") or {})
                rule = entry.get("rule")
                rule_id = str(getattr(rule, "rule_id", "") or "")
                contract_id = model_signal_interpretation_contract_id(rule)
                if contract_id:
                    matched_contract_ids.add(contract_id)
                source_symbol = (
                    str(dict(item.get("row") or {}).get("sourceSymbol") or "").upper().strip()
                )
                if source_symbol:
                    matched_symbols.add(source_symbol)
                signal_condition = model_signal_conditions(rule)[0]
                signal_condition_payload = (
                    signal_condition.to_dict()
                    if hasattr(signal_condition, "to_dict")
                    else dict(signal_condition or {})
                )
                signal_condition_id = str(
                    signal_condition_payload.get("condition_id")
                    or signal_condition_payload.get("conditionId")
                    or ""
                )
                per_rule_query_plan = {
                    **query_plan,
                    "ruleId": rule_id,
                    "nativeRuleId": typedb_native_rule_id(rule_id),
                    "conditionEvidenceColumns": (
                        {signal_condition_id: str(query_plan.get("relationIdColumn") or "")}
                        if signal_condition_id and query_plan.get("relationIdColumn")
                        else {}
                    ),
                    "modelSignalInterpretationPolicy": True,
                    "modelSignalInterpretationPolicyId": "model-signal-interpretation:" + rule_id,
                    "bridgeConditionIds": [
                        str(
                            (
                                condition.to_dict()
                                if hasattr(condition, "to_dict")
                                else dict(condition or {})
                            ).get("condition_id")
                            or (
                                condition.to_dict()
                                if hasattr(condition, "to_dict")
                                else dict(condition or {})
                            ).get("conditionId")
                            or ""
                        )
                        for condition in model_signal_bridge_conditions(rule)
                    ],
                    "residualConditionIds": [signal_condition_id] if signal_condition_id else [],
                }
                dispatched_matches.append(
                    {
                        "rule": rule,
                        "queryPlan": per_rule_query_plan,
                        "row": dict(item.get("row") or {}),
                    }
                )
                matched_count_by_rule_id[rule_id] = matched_count_by_rule_id.get(rule_id, 0) + 1
            batch_elapsed_ms = int((time.perf_counter() - batch_started) * 1000)
            for entry_index, entry in enumerate(entries):
                rule = entry.get("rule")
                rule_id = str(getattr(rule, "rule_id", "") or "")
                executed_rules.append(
                    {
                        "ruleId": rule_id,
                        "nativeRuleId": typedb_native_rule_id(rule_id),
                        "typeqlExecutionMode": "direct-typeql",
                        "queryMode": str(query_plan.get("queryMode") or ""),
                        "indexedEvidenceQueryUsed": bool(query_plan.get("indexedEvidenceQuery")),
                        "modelSignalInterpretationPolicy": True,
                        "modelSignalInterpretationPolicyId": "model-signal-interpretation:"
                        + rule_id,
                        "sharedModelSignalBridge": True,
                        "sharedModelSignalBridgeBatch": True,
                        "modelSignalBridgeVersion": MODEL_SIGNAL_BRIDGE_VERSION,
                        "bridgeSourceScope": str(batch.get("sourceScope") or ""),
                        "bridgeBatchIndex": batch_index,
                        "bridgeBatchPolicyCount": len(entries),
                        "rowCount": int(matched_count_by_rule_id.get(rule_id) or 0),
                        "candidateSymbols": list(entry.get("candidateSymbols") or []),
                        "queryComplexity": int(entry.get("queryComplexity") or 0),
                        # The physical read belongs to the batch leader. Every
                        # logical policy remains visible without inflating the
                        # actual query count in per-rule telemetry.
                        "queryCount": 1 if entry_index == 0 else 0,
                        "sharedBridgeReadCount": 1 if entry_index == 0 else 0,
                        "anyConditionQueryCount": 0,
                        "elapsedMs": batch_elapsed_ms,
                        "queryDurationMs": query_duration_ms,
                        **typedb_rule_execution_profile_fields(entry),
                    }
                )
    finally:
        _store.close_native_rule_read_driver(driver)
    return {
        "status": "ok" if not failures else "partial",
        "readTransactionCount": read_transaction_count,
        "readQueryCount": read_query_count,
        "executedRules": executed_rules,
        "failures": failures,
        "dispatchedMatches": dispatched_matches,
        "ignoredContractIds": sorted(ignored_contract_ids),
        "sourceRowCount": source_row_count,
        "dispatchedMatchCount": len(dispatched_matches),
        "matchedContractIds": sorted(matched_contract_ids)[:80],
        "matchedSymbols": sorted(matched_symbols)[:80],
        "indexedEvidenceReadCount": indexed_evidence_read_count,
    }
