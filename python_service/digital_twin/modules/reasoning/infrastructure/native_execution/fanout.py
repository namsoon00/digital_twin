"""native_execution: fanout through explicit injected capabilities."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    TYPEDB_NATIVE_RULE_ENGINE_VERSION,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict, Iterable, List
import time
from .fanout_ports import NativeExecutionFanoutStore


def merge_subject_fanout_matches(results: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    """Merge subject reads into the same rule/source identity as a combined read."""
    merged: Dict[str, Dict[str, object]] = {}
    for result in results or []:
        for raw in (result or {}).get("matches") or []:
            item = dict(raw or {})
            key = str(item.get("ruleId") or "") + "|" + str(item.get("sourceId") or "")
            if key == "|":
                continue
            existing = merged.get(key)
            if not existing:
                merged[key] = item
                continue
            existing["evidenceRelationIds"] = sorted(
                set(
                    list(existing.get("evidenceRelationIds") or [])
                    + list(item.get("evidenceRelationIds") or [])
                )
            )
            conditions = list(existing.get("matchedConditions") or [])
            condition_ids = {
                str(condition.get("conditionId") or "")
                for condition in conditions
                if isinstance(condition, dict)
            }
            for condition in item.get("matchedConditions") or []:
                condition_id = (
                    str((condition or {}).get("conditionId") or "")
                    if isinstance(condition, dict)
                    else ""
                )
                if condition_id and condition_id not in condition_ids:
                    conditions.append(dict(condition))
                    condition_ids.add(condition_id)
            existing["matchedConditions"] = conditions
    return [merged[key] for key in sorted(merged)]


def match_typedb_native_rules_by_subject(
    _store: NativeExecutionFanoutStore,
    rules: Iterable[GraphInferenceRule],
    target_symbols: Iterable[str],
    *,
    world_id: str,
    planner_topology: Dict[str, object] = None,
    preflight_graph: PortfolioOntology = None,
    preflight_incoming_relations_complete: bool = False,
    evidence_read_index: Dict[str, object] = None
) -> Dict[str, object]:
    """Evaluate subjects independently while one caller owns the stable ABox lease.

    No partial result is accepted.  The caller writes and activates one
    InferenceBox generation only after every subject returns complete core
    coverage, preserving the existing atomic generation boundary.
    """
    clean_symbols = clean_symbols_from_payload(target_symbols or [])
    parallelism = min(_store.native_rule_subject_parallelism(), len(clean_symbols))
    active_subject_parallelism = max(1, parallelism)
    per_subject_rule_parallelism = max(
        1,
        min(
            _store.native_rule_parallelism(),
            _store.native_rule_total_read_parallelism() // active_subject_parallelism,
        ),
    )
    started_at = time.perf_counter()

    def run_subject(symbol: str) -> Dict[str, object]:
        subject_started = time.perf_counter()
        result = _store.match_typedb_native_rules(
            rules,
            target_symbols=[symbol],
            world_id=world_id,
            planner_topology=planner_topology,
            preflight_graph=preflight_graph,
            preflight_incoming_relations_complete=preflight_incoming_relations_complete,
            # Divide the explicit global read cap across active subjects.
            # This permits bounded rule concurrency without multiplying
            # TypeDB transactions as the subject count grows.
            native_rule_parallelism=per_subject_rule_parallelism,
            native_rule_target_parallelism=1,
            # The outer fan-out caller still owns the same immutable ABox
            # lease. Preserve that fact so the one-subject path uses the
            # bounded per-rule read channels. Each worker lane reuses one
            # driver, so nested concurrency does not multiply handshakes.
            stable_abox_write_lease_held=True,
            evidence_read_index=evidence_read_index,
        )
        return {
            "symbol": symbol,
            "durationMs": int((time.perf_counter() - subject_started) * 1000),
            "result": dict(result or {}),
        }

    subject_rows: List[Dict[str, object]] = []
    if parallelism <= 1:
        subject_rows = [run_subject(symbol) for symbol in clean_symbols]
    else:
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = {executor.submit(run_subject, symbol): symbol for symbol in clean_symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    subject_rows.append(future.result())
                except Exception as error:  # noqa: BLE001 - one subject blocks activation.
                    subject_rows.append(
                        {
                            "symbol": symbol,
                            "durationMs": 0,
                            "result": {
                                "status": "error",
                                "reason": str(error)[:220],
                                "coreNativeInferenceEvaluationComplete": False,
                                "nativeInferenceEvaluationComplete": False,
                                "matches": [],
                                "executedRules": [],
                                "skippedRules": [],
                            },
                        }
                    )
    subject_rows.sort(key=lambda item: str(item.get("symbol") or ""))
    results = [dict(item.get("result") or {}) for item in subject_rows]
    complete = bool(results) and all(
        str(item.get("status") or "") == "ok"
        and bool(
            item.get("coreNativeInferenceEvaluationComplete")
            if "coreNativeInferenceEvaluationComplete" in item
            else True
        )
        for item in results
    )
    full_complete = complete and all(
        bool(
            item.get("nativeInferenceEvaluationComplete")
            if "nativeInferenceEvaluationComplete" in item
            else True
        )
        for item in results
    )
    matches = _store.merge_subject_fanout_matches(results)
    executed_rules = [
        dict(entry)
        for result in results
        for entry in result.get("executedRules") or []
        if isinstance(entry, dict)
    ]
    skipped_rules = [
        dict(entry)
        for result in results
        for entry in result.get("skippedRules") or []
        if isinstance(entry, dict)
    ]
    subject_summary = [
        {
            "symbol": str(row.get("symbol") or ""),
            "status": str((row.get("result") or {}).get("status") or "error"),
            "durationMs": int(row.get("durationMs") or 0),
            "matchedCount": int(number_or_none((row.get("result") or {}).get("matchedCount")) or 0),
            "coreEvaluationComplete": bool(
                (row.get("result") or {}).get("coreNativeInferenceEvaluationComplete")
                if "coreNativeInferenceEvaluationComplete" in (row.get("result") or {})
                else str((row.get("result") or {}).get("status") or "") == "ok"
            ),
            "reasonCode": str((row.get("result") or {}).get("reasonCode") or ""),
        }
        for row in subject_rows
    ]
    failures = [
        item
        for item in subject_summary
        if not item["coreEvaluationComplete"] or item["status"] != "ok"
    ]
    first_result = results[0] if results else {}
    model_signal_subjects = [
        dict(item.get("modelSignalBridgeExecution") or {})
        for item in results
        if isinstance(item.get("modelSignalBridgeExecution"), dict)
    ]
    model_signal_execution = {
        "status": (
            "ok"
            if model_signal_subjects
            and all(str(item.get("status") or "") == "ok" for item in model_signal_subjects)
            else "partial" if model_signal_subjects else "not-planned"
        ),
        "logicalModelSignalPolicyCount": max(
            [int(item.get("logicalModelSignalPolicyCount") or 0) for item in model_signal_subjects]
            or [0]
        ),
        "batchedSimplePolicyCount": max(
            [int(item.get("batchedSimplePolicyCount") or 0) for item in model_signal_subjects]
            or [0]
        ),
        "constrainedPolicyCount": max(
            [int(item.get("constrainedPolicyCount") or 0) for item in model_signal_subjects] or [0]
        ),
        "modelSignalBridgeReadCount": sum(
            int(item.get("modelSignalBridgeReadCount") or 0) for item in model_signal_subjects
        ),
        "eliminatedModelSignalPolicyQueryCount": sum(
            int(item.get("eliminatedModelSignalPolicyQueryCount") or 0)
            for item in model_signal_subjects
        ),
        "indexedEvidenceReadCount": sum(
            int(item.get("indexedEvidenceReadCount") or 0) for item in model_signal_subjects
        ),
        "ignoredContractIds": sorted(
            {
                str(contract_id or "")
                for item in model_signal_subjects
                for contract_id in item.get("ignoredContractIds") or []
                if str(contract_id or "")
            }
        ),
        "sourceRowCount": sum(
            int(item.get("sourceRowCount") or 0) for item in model_signal_subjects
        ),
        "dispatchedMatchCount": sum(
            int(item.get("dispatchedMatchCount") or 0) for item in model_signal_subjects
        ),
        "matchedContractIds": sorted(
            {
                str(contract_id or "")
                for item in model_signal_subjects
                for contract_id in item.get("matchedContractIds") or []
                if str(contract_id or "")
            }
        )[:80],
        "matchedSymbols": sorted(
            {
                str(symbol or "").upper().strip()
                for item in model_signal_subjects
                for symbol in item.get("matchedSymbols") or []
                if str(symbol or "").strip()
            }
        )[:80],
        "subjectCount": len(model_signal_subjects),
    }
    return {
        "status": "ok" if complete else "partial",
        "graphStore": "typedb",
        "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
        "nativeQueryUsed": complete,
        "indexedEvidenceQueryUsed": any(
            bool(item.get("indexedEvidenceQueryUsed")) for item in results
        ),
        "nativeExecutionMode": "subject-fanout",
        "nativeRuleParallelism": parallelism,
        "nativeRuleTargetParallelism": parallelism,
        "subjectFanoutUsed": True,
        "subjectFanoutParallelism": parallelism,
        "subjectRuleParallelism": per_subject_rule_parallelism,
        "totalReadParallelismCap": _store.native_rule_total_read_parallelism(),
        "effectiveTotalReadParallelism": min(
            _store.native_rule_total_read_parallelism(),
            active_subject_parallelism * per_subject_rule_parallelism,
        ),
        "subjectFanoutDurationMs": int((time.perf_counter() - started_at) * 1000),
        "subjectFanoutSubjects": subject_summary,
        "subjectFanoutFailureCount": len(failures),
        "parallelRuleExecution": parallelism > 1,
        "nativeInferenceEvaluationComplete": full_complete,
        "coreNativeInferenceEvaluationComplete": complete,
        "nativeCoverageStatus": (
            "complete"
            if full_complete
            else "blocking-rule-failure" if not complete else "core-complete-supporting-partial"
        ),
        "blockingRuleFailureCount": len(failures),
        "supportingRuleFailureCount": sum(
            int(item.get("supportingRuleFailureCount") or 0) for item in results
        ),
        "supportingRuleFailures": [
            dict(failure)
            for item in results
            for failure in item.get("supportingRuleFailures") or []
            if isinstance(failure, dict)
        ],
        "executedRuleCount": len(
            {
                str(item.get("ruleId") or "")
                for item in executed_rules
                if str(item.get("ruleId") or "")
            }
        ),
        "executedRuleWorkCount": len(executed_rules),
        "skippedRuleCount": len(
            {
                str(item.get("ruleId") or "")
                for item in skipped_rules
                if str(item.get("ruleId") or "")
            }
        ),
        "skippedRuleWorkCount": len(skipped_rules),
        "matchedCount": len(matches),
        "readTransactionCount": sum(int(item.get("readTransactionCount") or 0) for item in results),
        "readQueryCount": sum(int(item.get("readQueryCount") or 0) for item in results),
        "conditionDetailQueryCount": sum(
            int(item.get("conditionDetailQueryCount") or 0) for item in results
        ),
        "matches": matches,
        "executedRules": executed_rules,
        "skippedRules": skipped_rules,
        "executionPlan": dict(first_result.get("executionPlan") or {}),
        "modelSignalBridgeExecution": model_signal_execution,
        "ruleContext": {
            "status": "ok" if complete else "partial",
            "symbols": clean_symbols,
            "source": "subject-fanout",
            "subjects": [dict(item.get("ruleContext") or {}) for item in results],
        },
        "evidenceFieldIndex": {
            "status": "subject-fanout",
            "subjects": [dict(item.get("evidenceFieldIndex") or {}) for item in results],
        },
        "reasonCode": "" if complete else "typedbSubjectFanoutIncomplete",
        "reason": (
            ""
            if complete
            else "At least one subject did not complete native TypeDB rule evaluation."
        ),
        "typedbQueryMetrics": _store.query_metrics_snapshot(),
    }
