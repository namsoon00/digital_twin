"""native_execution: entry through explicit injected capabilities."""

from digital_twin.domain.model_signal_interpretation import is_model_signal_interpretation_rule
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.any_queries import (
    typedb_native_any_group_check_query,
)
from digital_twin.modules.reasoning.infrastructure.typeql.indexed_queries import (
    typedb_native_rule_runtime_query_plan,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    normalized_condition_role,
    symbol_from_subject,
    typedb_native_rule_id,
    typedb_planned_candidate_symbols,
)
from typing import Dict, Iterable
import time
from .entry_ports import NativeExecutionEntryStore, NativeExecutionEntryRuntime


def verify_typedb_native_any_conditions(
    _store: NativeExecutionEntryStore,
    driver,
    transaction_type,
    rule: GraphInferenceRule,
    source_id: str,
    timeout_seconds: float,
    scoped_manifest_only: bool,
    tx=None,
    world_id: str = "",
    evidence_read_index: Dict[str, object] = None,
    *,
    _bindings: NativeExecutionEntryRuntime
) -> Dict[str, object]:
    """Verify an N-of-M RuleBox group in one bounded TypeQL query.

    Schema functions retain only required and negative clauses.  Expanding
    `anyConditionMinCount` combinations inside a function made TypeDB
    compile an exponential search plan.  The candidate source is already
    narrowed by that base match, so TypeDB can safely evaluate the whole
    N-of-M group with a distinct RuleBox-condition aggregation.  Python
    receives only the matched/not-matched result; it never counts the
    branches to decide an investment rule.
    """
    conditions = [
        (index, condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {}))
        for index, condition in enumerate(getattr(rule, "conditions", []) or [])
        if normalized_condition_role(
            condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
        )
        in {"any", "optional"}
    ]
    if not conditions:
        return {
            "status": "matched",
            "matchedConditionIds": [],
            "requiredConditionCount": 0,
            "readTransactionCount": 0,
            "readQueryCount": 0,
        }
    required_count = max(1, int(number_or_none(getattr(rule, "any_condition_min_count", 1)) or 1))
    if required_count > len(conditions):
        return {
            "status": "invalid",
            "matchedConditionIds": [],
            "requiredConditionCount": required_count,
            "readTransactionCount": 0,
            "readQueryCount": 0,
            "reason": "RuleBox any condition minimum exceeds available conditions.",
        }
    verified_index = dict(evidence_read_index or {})
    index_payload = (
        dict(verified_index.get("index") or {})
        if str(verified_index.get("status") or "") == "verified"
        else {}
    )
    source_storage_id = str(
        dict(index_payload.get("sourceStorageIdsBySourceId") or {}).get(str(source_id or "")) or ""
    ).strip()
    source_symbol = (
        symbol_from_subject(str(source_id or "")) or str(source_id or "").upper().strip()
    )
    relation_storage_ids_by_type = dict(
        dict(index_payload.get("relationStorageIdsBySymbolAndType") or {}).get(source_symbol, {})
        or {}
    )
    any_relation_types = {
        str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        for _condition_index, condition in conditions
        if str(condition.get("kind") or "") == "relation"
        and str(condition.get("relation_type") or condition.get("relationType") or "").strip()
    }
    relation_storage_ids = sorted(
        {
            str(storage_id or "").strip()
            for relation_type in any_relation_types
            for storage_id in relation_storage_ids_by_type.get(relation_type, []) or []
            if str(storage_id or "").strip()
        }
    )
    # A v1 Manifest records the stock's exact physical source row but not
    # relation ids by type.  The relation endpoints are generation-scoped
    # physical nodes, so binding that source is already an exact active
    # ABox boundary.  Do not expand every relation id from the v1 index
    # into a large `or` solely to recreate a boundary the source link
    # already proves.  v2 narrows further to the rule's relation types.
    query_plan = typedb_native_any_group_check_query(
        rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {}),
        source_id,
        scoped_manifest_only=scoped_manifest_only,
        world_id=world_id,
        active_source_storage_id=source_storage_id,
        active_relation_storage_ids=relation_storage_ids,
        active_relation_storage_ids_by_type=relation_storage_ids_by_type,
    )
    if not query_plan.get("query"):
        return {
            "status": "error",
            "matchedConditionIds": [],
            "requiredConditionCount": required_count,
            "readTransactionCount": 0,
            "readQueryCount": 0,
            "reason": str(
                query_plan.get("reason") or "TypeDB any-condition group query could not be built."
            ),
        }
    requested_timeout = max(0.5, float(timeout_seconds or 0.5))
    query_timeout_cap = _store.native_rule_query_timeout_seconds()
    if (
        str(query_plan.get("anyConditionCheckMode") or "")
        == "distinct-condition-count-manifest-indexed"
    ):
        query_timeout_cap = _store.native_rule_indexed_any_condition_query_timeout_seconds()
    query_timeout = min(requested_timeout, query_timeout_cap)
    owns_transaction = tx is None
    try:
        if owns_transaction:
            with driver.transaction(
                _store.database,
                transaction_type.READ,
                _store.read_transaction_options(query_timeout),
            ) as transaction:
                rows = _store.read_rows_in_transaction(
                    transaction,
                    str(query_plan.get("query")),
                    query_plan.get("columns") or ["sourceId"],
                    label="nativeRuleAnyGroup:" + str(rule.rule_id or ""),
                    timeout_seconds=query_timeout,
                )
        else:
            rows = _store.read_rows_in_transaction(
                tx,
                str(query_plan.get("query")),
                query_plan.get("columns") or ["sourceId"],
                label="nativeRuleAnyGroup:" + str(rule.rule_id or ""),
                timeout_seconds=query_timeout,
            )
    except (
        Exception
    ) as error:  # noqa: BLE001 - a partial any check must block the whole inference generation.
        return {
            "status": (
                "query-timeout"
                if _bindings.typedb_error_code(error) == "typedbTimeout"
                else "error"
            ),
            "matchedConditionIds": [],
            "requiredConditionCount": required_count,
            "readTransactionCount": 1 if owns_transaction else 0,
            "readQueryCount": 0,
            "reason": str(error)[:220],
        }
    return {
        "status": "matched" if rows else "not-matched",
        # Detailed per-branch evidence is intentionally collected only by
        # the opt-in condition-detail path.  The group cardinality itself
        # is decided by TypeDB's `reduce count` query.
        "matchedConditionIds": [],
        "requiredConditionCount": required_count,
        "readTransactionCount": 1 if owns_transaction else 0,
        "readQueryCount": 1,
        "queryTimeoutSeconds": query_timeout,
        "anyConditionCheckMode": str(query_plan.get("anyConditionCheckMode") or ""),
        "typeDbCardinalityVerified": bool(rows),
    }


def execute_typedb_native_rule_entry(
    _store: NativeExecutionEntryStore,
    planned: Dict[str, object],
    clean_symbols: Iterable[str],
    world_id: str,
    scoped_manifest_only: bool,
    imported,
    transaction_type,
    deadline: float,
    execution_mode: str,
    evidence_read_index: Dict[str, object] = None,
    shared_read_driver=None,
    *,
    _bindings: NativeExecutionEntryRuntime
) -> Dict[str, object]:
    """Run one independent native rule under the caller's ABox write lease.

    Parallel execution deliberately opens a short-lived read transaction per
    rule. The enclosing scoped ABox write lease prevents an ABox pointer
    transition while direct TypeQL reads run, while the per-rule transaction
    timeout remains effective in worker threads where SIGALRM is not.
    """
    entry_started = time.perf_counter()

    def with_elapsed(result: Dict[str, object]) -> Dict[str, object]:
        elapsed_ms = int((time.perf_counter() - entry_started) * 1000)
        executed = result.get("executed") if isinstance(result.get("executed"), dict) else {}
        failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
        if executed:
            executed.setdefault("elapsedMs", elapsed_ms)
            result["executed"] = executed
        if failure:
            failure.setdefault("elapsedMs", elapsed_ms)
            failure.setdefault("queryDurationMs", 0)
            failure.setdefault("queryCount", int(result.get("readQueryCount") or 0))
            result["failure"] = failure
        return result

    rule = planned.get("rule")
    if not rule:
        return with_elapsed(
            {
                "status": "partial",
                "readTransactionCount": 0,
                "readQueryCount": 0,
                "failure": {
                    "ruleId": "",
                    "status": "blocked",
                    "reason": "TypeDB native rule plan is missing its rule definition.",
                },
            }
        )
    rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
    candidate_symbols = typedb_planned_candidate_symbols(planned, clean_symbols)
    target_work_metadata = {
        "targetWorkShardIndex": int(number_or_none(planned.get("targetWorkShardIndex")) or 0),
        "targetWorkShardCount": max(
            1, int(number_or_none(planned.get("targetWorkShardCount")) or 1)
        ),
        "targetWorkShardingUsed": bool(planned.get("targetWorkShardingUsed")),
        "targetWorkAdaptiveShardingUsed": bool(planned.get("targetWorkAdaptiveShardingUsed")),
    }
    has_any_conditions = any(
        normalized_condition_role(
            condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
        )
        in {"any", "optional"}
        for condition in (rule.conditions or [])
    )
    query_plan = typedb_native_rule_runtime_query_plan(
        rule_payload,
        candidate_symbols,
        scoped_manifest_only=scoped_manifest_only,
        world_id=world_id,
        evidence_read_index=evidence_read_index,
        # A native rule decides only whether a source matches. When the
        # optional detailed-evidence path is off, returning every
        # relation combination makes the driver deserialize a potentially
        # unbounded Cartesian result that is discarded by the merge step.
        # TypeDB still evaluates the full predicate; the reducer only
        # returns one proven result row per source.
        compact_result_rows=not _store.condition_detail_queries_enabled(),
    )
    uses_indexed_evidence_query = bool(query_plan.get("indexedEvidenceQuery"))
    if not query_plan.get("query"):
        return with_elapsed(
            {
                "status": "partial",
                "readTransactionCount": 0,
                "readQueryCount": 0,
                "failure": {
                    "ruleId": str(rule.rule_id or ""),
                    "status": "blocked",
                    "reason": "Direct TypeQL rule query could not be built.",
                    "candidateSymbols": candidate_symbols,
                },
            }
        )

    def budget_failure(
        reason: str = "TypeDB native-rule realtime execution budget is exhausted.",
        read_transaction_count: int = 0,
        read_query_count: int = 0,
    ):
        return {
            "status": "partial",
            "readTransactionCount": read_transaction_count,
            "readQueryCount": read_query_count,
            "failure": {
                "ruleId": str(rule.rule_id or ""),
                "status": "deferred-by-runtime-budget",
                "reason": reason,
                "candidateSymbols": candidate_symbols,
            },
        }

    def operation():
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0.5:
            return budget_failure()
        query_timeout = min(_store.native_rule_query_timeout_seconds(), remaining_seconds)
        owns_driver = shared_read_driver is None
        driver = shared_read_driver or _store.open_native_rule_read_driver(
            imported,
            request_timeout_seconds=min(
                remaining_seconds,
                max(
                    query_timeout,
                    _store.native_rule_indexed_any_condition_query_timeout_seconds(),
                ),
            ),
        )
        read_transaction_count = 0
        read_call_count = 0
        query_duration_ms = 0.0
        try:
            if owns_driver:
                _store.ensure_database(driver)
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
                        query_plan.get("columns") or ["sourceId"],
                        label="nativeRule:" + str(rule.rule_id or ""),
                        timeout_seconds=query_timeout,
                    )
                finally:
                    query_duration_ms += (time.perf_counter() - query_started) * 1000
            read_transaction_count += 1
            read_call_count += 1
            any_condition_query_count = 0
            if rows and has_any_conditions and not bool(query_plan.get("anyConditionsVerified")):
                verified_rows = []
                for row in rows:
                    remaining_seconds = deadline - time.monotonic()
                    if remaining_seconds <= 0.5:
                        return budget_failure(
                            "TypeDB native-rule runtime budget was exhausted while verifying any conditions.",
                            read_transaction_count,
                            read_call_count,
                        )
                    verification_started = time.perf_counter()
                    try:
                        verification = _store.verify_typedb_native_any_conditions(
                            driver,
                            transaction_type,
                            rule,
                            str(row.get("sourceId") or ""),
                            remaining_seconds,
                            scoped_manifest_only,
                            world_id=world_id,
                            evidence_read_index=evidence_read_index,
                        )
                    finally:
                        query_duration_ms += (time.perf_counter() - verification_started) * 1000
                    read_transaction_count += int(verification.get("readTransactionCount") or 0)
                    read_call_count += int(verification.get("readQueryCount") or 0)
                    any_condition_query_count += int(verification.get("readQueryCount") or 0)
                    verification_status = str(verification.get("status") or "error")
                    if verification_status == "matched":
                        row["_matchedAnyConditionIds"] = list(
                            verification.get("matchedConditionIds") or []
                        )
                        row["_anyConditionsVerified"] = bool(
                            verification.get("typeDbCardinalityVerified")
                        )
                        verified_rows.append(row)
                        continue
                    if verification_status == "not-matched":
                        continue
                    return {
                        "status": "partial",
                        "readTransactionCount": read_transaction_count,
                        "readQueryCount": read_call_count,
                        "failure": {
                            "ruleId": str(rule.rule_id or ""),
                            "status": "any-condition-" + verification_status,
                            "reason": str(
                                verification.get("reason")
                                or "TypeDB any-condition verification did not complete."
                            )[:220],
                            "candidateSymbols": candidate_symbols,
                        },
                    }
                rows = verified_rows
            elif rows and has_any_conditions:
                for row in rows:
                    row["_matchedAnyConditionIds"] = []
                    row["_anyConditionsVerified"] = True
            return {
                "status": "ok",
                "rule": rule,
                "queryPlan": query_plan,
                "rows": rows,
                "readTransactionCount": read_transaction_count,
                "readQueryCount": read_call_count,
                "executed": {
                    "ruleId": rule.rule_id,
                    "nativeRuleId": typedb_native_rule_id(rule.rule_id),
                    "typeqlExecutionMode": "direct-typeql",
                    "queryMode": str(
                        query_plan.get("queryMode") or "typedb-scoped-typeql-any-verified-parallel"
                    ),
                    "indexedEvidenceQueryUsed": uses_indexed_evidence_query,
                    "modelSignalInterpretationPolicy": is_model_signal_interpretation_rule(
                        rule_payload
                    ),
                    "modelSignalInterpretationPolicyId": (
                        "model-signal-interpretation:" + str(rule.rule_id or "")
                        if is_model_signal_interpretation_rule(rule_payload)
                        else ""
                    ),
                    "sharedModelSignalBridge": bool(query_plan.get("sharedModelSignalBridge")),
                    "bridgeSourceScope": str(query_plan.get("bridgeSourceScope") or ""),
                    "dedicatedReadDriverReused": not owns_driver,
                    "resultRowsCompacted": bool(query_plan.get("resultRowsCompacted")),
                    "rowCount": len(rows),
                    "candidateSymbols": candidate_symbols,
                    **target_work_metadata,
                    "queryComplexity": int(planned.get("queryComplexity") or 0),
                    "queryCount": read_call_count,
                    "anyConditionQueryCount": any_condition_query_count,
                    "queryDurationMs": int(query_duration_ms),
                },
            }
        finally:
            if owns_driver:
                _store.close_native_rule_read_driver(driver)

    try:
        # Do not replay the same expensive rule shape through the generic
        # repository retry loop. A bounded timeout must return to the
        # serial recovery phase, which can split targets safely; an
        # explicitly closed transaction gets exactly one fresh retry.
        result = operation()
    except (
        Exception
    ) as error:  # noqa: BLE001 - a failed independent read blocks the complete generation.
        result = {
            "status": "partial",
            "readTransactionCount": 0,
            "readQueryCount": 0,
            "failure": {
                "ruleId": str(rule.rule_id or ""),
                "status": (
                    "query-timeout"
                    if _bindings.typedb_error_code(error) == "typedbTimeout"
                    else "query-error"
                ),
                "reason": str(error)[:220],
                "candidateSymbols": candidate_symbols,
            },
        }
    return with_elapsed(result)
