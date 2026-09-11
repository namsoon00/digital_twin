"""native_execution: retry through explicit injected capabilities."""

from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.planning import (
    typedb_native_rule_target_work_plan,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    typedb_planned_candidate_symbols,
)
from typing import Dict, Iterable, List
import time
from .retry_ports import NativeExecutionRetryStore, NativeExecutionRetryRuntime


def native_rule_entry_has_timeout_failure(
    result: Dict[str, object], *, _bindings: NativeExecutionRetryRuntime
) -> bool:
    """Return whether a failed native-rule entry is safe to retry smaller.

    Only a bounded read timeout is eligible. A malformed query, missing
    direct TypeQL query, or incomplete any-condition proof must remain a hard
    failure for the entire generation.
    """
    failure = dict((result or {}).get("failure") or {})
    status = str(failure.get("status") or "").strip().lower()
    reason = str(failure.get("reason") or "")
    return status == "query-timeout" or _bindings.typedb_error_code(reason) == "typedbTimeout"


def native_rule_entry_has_interrupted_transaction_failure(result: Dict[str, object]) -> bool:
    """Return whether TypeDB closed this read transaction while executing.

    This is intentionally narrower than a generic query error. A malformed
    query or incomplete TypeQL result must still fail the generation, while
    the server's explicit concurrent transaction-close response is safe to
    retry once through a fresh read transaction.
    """
    failure = dict((result or {}).get("failure") or {})
    status = str(failure.get("status") or "").strip().lower()
    reason = str(failure.get("reason") or "").lower()
    safe_close_markers = {
        "concurrent transaction close",
        "transaction is closed and no further operation is allowed",
    }
    return status == "query-error" and any(marker in reason for marker in safe_close_markers)


def recover_timed_out_native_rule_entry(
    _store: NativeExecutionRetryStore,
    primary_result: Dict[str, object],
    planned: Dict[str, object],
    clean_symbols: Iterable[str],
    world_id: str,
    scoped_manifest_only: bool,
    imported,
    transaction_type,
    deadline: float,
    execution_mode: str,
    evidence_read_index: Dict[str, object] = None,
) -> Dict[str, object]:
    """Recover a bounded native-rule failure without accepting partial coverage.

    A native query that exceeds its bound invalidates its transaction, but
    a smaller query against the same immutable ABox can still be complete.
    A server-side concurrent transaction close is also safe to retry once
    using a new read transaction. Both paths run after the initial phase
    has drained, avoid new concurrency, and require complete coverage.
    """
    primary = dict(primary_result or {})
    timeout_failure = _store.native_rule_entry_has_timeout_failure(primary)
    interrupted_transaction = _store.native_rule_entry_has_interrupted_transaction_failure(primary)
    if not timeout_failure and not interrupted_transaction:
        return primary

    candidate_symbols = typedb_planned_candidate_symbols(
        dict(planned or {}),
        clean_symbols,
    )
    primary_failure = dict(primary.get("failure") or {})
    primary_elapsed_ms = int(number_or_none(primary_failure.get("elapsedMs")) or 0)
    primary_query_duration_ms = int(number_or_none(primary_failure.get("queryDurationMs")) or 0)
    read_transaction_count = int(primary.get("readTransactionCount") or 0)
    read_query_count = int(primary.get("readQueryCount") or 0)
    recovery_started = time.perf_counter()

    if interrupted_transaction:
        if deadline - time.monotonic() <= 0.5:
            return primary
        retried = _store.execute_typedb_native_rule_entry(
            dict(planned or {}),
            clean_symbols,
            world_id,
            scoped_manifest_only,
            imported,
            transaction_type,
            deadline,
            execution_mode,
            evidence_read_index,
        )
        retried = dict(retried or {})
        read_transaction_count += int(retried.get("readTransactionCount") or 0)
        read_query_count += int(retried.get("readQueryCount") or 0)
        if str(retried.get("status") or "partial") == "ok":
            executed = dict(retried.get("executed") or {})
            executed.update(
                {
                    "candidateSymbols": list(candidate_symbols),
                    "elapsedMs": primary_elapsed_ms
                    + int((time.perf_counter() - recovery_started) * 1000),
                    "queryDurationMs": primary_query_duration_ms
                    + int(number_or_none(executed.get("queryDurationMs")) or 0),
                    "interruptedTransactionRetryUsed": True,
                    "interruptedTransactionRetryMode": "fresh-read-transaction",
                }
            )
            retried.update(
                {
                    "readTransactionCount": read_transaction_count,
                    "readQueryCount": read_query_count,
                    "executed": executed,
                }
            )
            return retried
        retry_failure = dict(retried.get("failure") or primary_failure)
        retry_failure.update(
            {
                "candidateSymbols": list(candidate_symbols),
                "interruptedTransactionRetryAttempted": True,
                "interruptedTransactionRetryInitialStatus": str(
                    primary_failure.get("status") or ""
                ),
                "interruptedTransactionRetryInitialReason": str(
                    primary_failure.get("reason") or ""
                )[:220],
                "elapsedMs": primary_elapsed_ms
                + int((time.perf_counter() - recovery_started) * 1000),
                "queryDurationMs": primary_query_duration_ms
                + int(number_or_none(retry_failure.get("queryDurationMs")) or 0),
            }
        )
        return {
            **primary,
            "status": "partial",
            "readTransactionCount": read_transaction_count,
            "readQueryCount": read_query_count,
            "failure": retry_failure,
        }

    if len(candidate_symbols) < 2:
        return primary

    recovery_entry = dict(planned or {})
    recovery_entry["candidateSymbols"] = list(candidate_symbols)
    recovery_plan = typedb_native_rule_target_work_plan(
        [recovery_entry],
        target_parallelism=2,
    )
    work_items = list(recovery_plan.get("workItems") or [])
    if len(work_items) < 2:
        return primary

    def annotated_failure(
        failure: Dict[str, object],
        failed_shard_index: int = -1,
        attempted: bool = True,
        reason: str = "",
    ) -> Dict[str, object]:
        result = dict(primary)
        detail = dict(failure or primary_failure)
        if reason:
            detail["reason"] = reason
            detail["status"] = "deferred-by-runtime-budget"
        detail["candidateSymbols"] = list(candidate_symbols)
        detail["timeoutFallbackAttempted"] = bool(attempted)
        detail["timeoutFallbackShardCount"] = len(work_items)
        if failed_shard_index >= 0:
            detail["timeoutFallbackFailedShardIndex"] = failed_shard_index
        detail["timeoutFallbackInitialStatus"] = str(primary_failure.get("status") or "")
        elapsed_ms = primary_elapsed_ms + int((time.perf_counter() - recovery_started) * 1000)
        detail["elapsedMs"] = max(int(number_or_none(detail.get("elapsedMs")) or 0), elapsed_ms)
        detail["queryDurationMs"] = max(
            int(number_or_none(detail.get("queryDurationMs")) or 0),
            primary_query_duration_ms,
        )
        result.update(
            {
                "status": "partial",
                "readTransactionCount": read_transaction_count,
                "readQueryCount": read_query_count,
                "failure": detail,
            }
        )
        return result

    shard_results: List[Dict[str, object]] = []
    for shard_index, shard in enumerate(work_items):
        if deadline - time.monotonic() <= 0.5:
            return annotated_failure(
                primary_failure,
                attempted=bool(shard_results),
                reason="TypeDB native-rule runtime budget was exhausted before timeout recovery completed.",
            )
        shard_result = _store.execute_typedb_native_rule_entry(
            shard,
            clean_symbols,
            world_id,
            scoped_manifest_only,
            imported,
            transaction_type,
            deadline,
            execution_mode,
            evidence_read_index,
        )
        read_transaction_count += int(shard_result.get("readTransactionCount") or 0)
        read_query_count += int(shard_result.get("readQueryCount") or 0)
        if str(shard_result.get("status") or "partial") != "ok":
            return annotated_failure(
                dict(shard_result.get("failure") or primary_failure),
                failed_shard_index=shard_index,
            )
        shard_results.append(dict(shard_result))

    rule = planned.get("rule")
    query_plan = dict(shard_results[0].get("queryPlan") or {})
    rows = [row for shard_result in shard_results for row in shard_result.get("rows") or []]
    shard_executed = [dict(item.get("executed") or {}) for item in shard_results]
    first_executed = dict(shard_executed[0] or {})
    executed = {
        **first_executed,
        "ruleId": str(getattr(rule, "rule_id", "") or first_executed.get("ruleId") or ""),
        "candidateSymbols": list(candidate_symbols),
        # These fields describe configured target work. Recovery is kept
        # separate so telemetry does not imply a global sharding setting.
        "targetWorkShardIndex": int(number_or_none(planned.get("targetWorkShardIndex")) or 0),
        "targetWorkShardCount": max(
            1, int(number_or_none(planned.get("targetWorkShardCount")) or 1)
        ),
        "targetWorkShardingUsed": bool(planned.get("targetWorkShardingUsed")),
        "targetWorkAdaptiveShardingUsed": bool(planned.get("targetWorkAdaptiveShardingUsed")),
        "rowCount": sum(int(item.get("rowCount") or 0) for item in shard_executed),
        "queryCount": sum(int(item.get("queryCount") or 0) for item in shard_executed),
        "anyConditionQueryCount": sum(
            int(item.get("anyConditionQueryCount") or 0) for item in shard_executed
        ),
        "queryDurationMs": primary_query_duration_ms
        + sum(int(item.get("queryDurationMs") or 0) for item in shard_executed),
        "elapsedMs": primary_elapsed_ms + int((time.perf_counter() - recovery_started) * 1000),
        "timeoutFallbackUsed": True,
        "timeoutFallbackShardCount": len(work_items),
        "timeoutFallbackMode": "serial-target-shards",
    }
    return {
        "status": "ok",
        "rule": rule,
        "queryPlan": query_plan,
        "rows": rows,
        "readTransactionCount": read_transaction_count,
        "readQueryCount": read_query_count,
        "executed": executed,
    }
