"""Bounded physical ABox writes; this writer cannot activate a Manifest."""

import json
import time
from typing import Dict, Iterable, List

from .ports import ABoxRowStore, ABoxRuntime


def write_persistence_rows(
    store: ABoxRowStore,
    driver,
    imported,
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
    telemetry: Dict[str, object] = None,
    assume_missing_storage: bool = False,
    *,
    runtime: ABoxRuntime,
) -> Dict[str, object]:
    """Write pre-resolved rows without requiring every endpoint in a slice."""
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    settings = runtime.settings()
    trace = telemetry if isinstance(telemetry, dict) else {}
    trace["stage"] = "storage-reuse-plan"
    reuse_started = time.monotonic()
    reuse_plan = store.scoped_abox_storage_reuse_plan(
        node_rows,
        relation_rows,
        assume_missing_storage=bool(assume_missing_storage),
    )
    trace["storageReusePlanMs"] = round(
        (time.monotonic() - reuse_started) * 1000,
        1,
    )
    trace["storageLookupMode"] = str(reuse_plan.get("storageLookupMode") or "")
    if str(reuse_plan.get("status") or "") != "ok":
        raise RuntimeError(
            "Scoped ABox storage identity conflict: "
            + json.dumps(list(reuse_plan.get("conflicts") or [])[:3], ensure_ascii=False, sort_keys=True)
        )
    node_rows_to_insert = list(reuse_plan.get("nodeRowsToInsert") or [])
    relation_rows_to_insert = list(reuse_plan.get("relationRowsToInsert") or [])
    expected_counts_by_scope = store.scoped_abox_counts_by_scope(
        reuse_plan.get("nodeRows") or [],
        reuse_plan.get("relationRows") or [],
    )
    inserted_counts_by_scope = store.scoped_abox_counts_by_scope(
        node_rows_to_insert,
        relation_rows_to_insert,
    )
    reused_counts_by_scope = store.scoped_abox_counts_by_scope(
        reuse_plan.get("reusedNodeRows") or [],
        reuse_plan.get("reusedRelationRows") or [],
    )
    node_queries = store.batched_node_insert_queries(
        node_rows_to_insert,
        runtime.now(),
        store.abox_node_batch_size(settings),
        store.write_query_max_bytes(settings),
    )
    relation_batch_size = store.abox_relation_batch_size(settings)
    updated_at = runtime.now()
    relation_write_plans = store.given_relation_insert_plans(
        relation_rows_to_insert,
        updated_at,
        settings=settings,
    )
    relation_queries = [str(item.get("query") or "") for item in relation_write_plans]
    queries = list(node_queries)
    batch_size = store.abox_write_transaction_query_count(settings)
    query_durations_ms: List[float] = []
    relation_fallback_count = 0
    relation_given_row_count = 0
    relation_given_batch_count = 0
    relation_given_transaction_count = 0
    relation_legacy_query_count = 0
    relation_legacy_transaction_count = 0
    trace.update({
        "requestedNodeCount": len(reuse_plan.get("nodeRows") or []),
        "requestedRelationCount": len(reuse_plan.get("relationRows") or []),
        "insertedNodeCount": len(node_rows_to_insert),
        "insertedRelationCount": len(relation_rows_to_insert),
        "reusedNodeCount": len(reuse_plan.get("reusedNodeRows") or []),
        "reusedRelationCount": len(reuse_plan.get("reusedRelationRows") or []),
        "nodeQueryCount": len(node_queries),
        "plannedRelationQueryCount": len(relation_queries),
        "completedNodeTransactionCount": 0,
        "completedRelationTransactionCount": 0,
    })

    def write_query_batch(query_batch: List[str]) -> None:
        if not query_batch:
            return

        def write_batch():
            # TypeDB 3.12 can close one long-lived gRPC driver after a
            # large world replay has opened hundreds of sequential write
            # transactions. Keep each bounded commit on a fresh local
            # driver; the outer driver remains available for the final
            # scope verification and control-plane swap.
            write_driver = store.open_driver(imported)
            try:
                store.ensure_database(write_driver)
                with runtime.timeout(store.write_operation_timeout_seconds(), "TypeDB scoped ABox write batch"):
                    with write_driver.transaction(
                        store.database,
                        TransactionType.WRITE,
                        options=store.write_transaction_options(),
                    ) as tx:
                        for query in query_batch:
                            query_started = time.monotonic()
                            tx.query(query).resolve()
                            query_durations_ms.append(round((time.monotonic() - query_started) * 1000, 1))
                        tx.commit()
            finally:
                store.close_driver(write_driver)

        store.with_typedb_retries(write_batch)

    # Nodes must exist before relation batches resolve their storage ids.
    # Keep the established short transaction budget for node writes.
    trace["stage"] = "node-write"
    for offset in range(0, len(queries), batch_size):
        query_batch = queries[offset: offset + batch_size]
        write_query_batch(query_batch)
        trace["completedNodeTransactionCount"] = int(
            trace.get("completedNodeTransactionCount") or 0
        ) + 1

    # TypeDB does not treat a zero-row relation match as an error. Check
    # all physical endpoints in one batched inventory read before sending
    # relation plans; this turns silent partial writes into a precise,
    # recoverable candidate failure.
    trace["stage"] = "relation-endpoint-verification"
    endpoint_storage_ids = sorted({
        str(value or "").strip()
        for row in relation_rows_to_insert
        for value in (
            (row or {}).get("sourceStorageId"),
            (row or {}).get("targetStorageId"),
        )
        if str(value or "").strip()
    })
    endpoint_verification_started = time.monotonic()
    endpoint_inventory = store.current_state_storage_inventory(
        driver,
        imported,
        node_storage_ids=endpoint_storage_ids,
        relation_storage_ids=[],
    )
    missing_endpoint_storage_ids = store.missing_relation_endpoint_storage_ids(
        relation_rows_to_insert,
        endpoint_inventory.get("nodes") or {},
    )
    trace["relationEndpointVerificationMs"] = round(
        (time.monotonic() - endpoint_verification_started) * 1000,
        1,
    )
    trace["relationEndpointCount"] = len(endpoint_storage_ids)
    trace["missingRelationEndpointCount"] = len(missing_endpoint_storage_ids)
    if missing_endpoint_storage_ids:
        trace["missingRelationEndpointStorageIds"] = missing_endpoint_storage_ids[:20]
        endpoint_diagnostics = store.missing_relation_endpoint_diagnostics(
            relation_rows_to_insert,
            missing_endpoint_storage_ids,
        )
        trace["missingRelationEndpointDiagnostics"] = endpoint_diagnostics[:20]
        raise RuntimeError(
            "Scoped ABox relation endpoint verification failed for "
            + str(len(missing_endpoint_storage_ids))
            + " physical nodes; sample="
            + json.dumps(
                endpoint_diagnostics[:5],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "."
        )

    given_plans = [
        plan
        for plan in relation_write_plans
        if str(plan.get("query") or "") and list(plan.get("givenRows") or [])
    ]
    legacy_plans = [
        plan
        for plan in relation_write_plans
        if str(plan.get("query") or "") and not list(plan.get("givenRows") or [])
    ]

    def write_given_plan_batch(plans: List[Dict[str, object]]) -> None:
        if not plans:
            return

        def write_given_transaction():
            write_driver = store.open_driver(imported)
            try:
                store.ensure_database(write_driver)
                with runtime.timeout(store.write_operation_timeout_seconds(), "TypeDB scoped ABox given relation batch"):
                    with write_driver.transaction(
                        store.database,
                        TransactionType.WRITE,
                        options=store.write_transaction_options(),
                    ) as tx:
                        for plan in plans:
                            query_started = time.monotonic()
                            tx.query(
                                str(plan.get("query") or ""),
                                given_rows=list(plan.get("givenRows") or []),
                            ).resolve()
                            query_durations_ms.append(round((time.monotonic() - query_started) * 1000, 1))
                        tx.commit()
            finally:
                store.close_driver(write_driver)

        store.with_typedb_retries(write_given_transaction)

    # Query shape controls TypeDB planning, not transaction ownership.
    # Group several stable ``given`` plans into one bounded commit so a
    # normal changed-symbol projection does not acquire the schema/data
    # writer lock once per relation type.
    trace["stage"] = "relation-write"
    for offset in range(0, len(given_plans), batch_size):
        plan_batch = given_plans[offset: offset + batch_size]
        try:
            write_given_plan_batch(plan_batch)
            relation_given_transaction_count += 1
            trace["completedRelationTransactionCount"] = int(
                trace.get("completedRelationTransactionCount") or 0
            ) + 1
            relation_given_batch_count += len(plan_batch)
            relation_given_row_count += sum(
                len(plan.get("givenRows") or []) for plan in plan_batch
            )
            continue
        except Exception:
            # One unsupported shape rolls back the complete grouped
            # transaction. Retry each plan independently to identify and
            # preserve the legacy compatibility fallback without writing
            # a partial candidate generation.
            pass

        for plan in plan_batch:
            rows = list(plan.get("givenRows") or [])
            try:
                write_given_plan_batch([plan])
                relation_given_transaction_count += 1
                trace["completedRelationTransactionCount"] = int(
                    trace.get("completedRelationTransactionCount") or 0
                ) + 1
                relation_given_batch_count += 1
                relation_given_row_count += len(rows)
                continue
            except Exception:
                relation_fallback_count += 1
            fallback_queries = store.batched_relation_insert_queries(
                list(plan.get("rows") or []),
                updated_at,
                relation_batch_size,
                store.write_query_max_bytes(settings),
            )
            for fallback_offset in range(0, len(fallback_queries), batch_size):
                write_query_batch(fallback_queries[fallback_offset: fallback_offset + batch_size])
                relation_legacy_transaction_count += 1
                trace["completedRelationTransactionCount"] = int(
                    trace.get("completedRelationTransactionCount") or 0
                ) + 1
            relation_legacy_query_count += len(fallback_queries)

    for plan in legacy_plans:
        write_query_batch([str(plan.get("query") or "")])
        relation_legacy_query_count += 1
        relation_legacy_transaction_count += 1
        trace["completedRelationTransactionCount"] = int(
            trace.get("completedRelationTransactionCount") or 0
        ) + 1
    trace["stage"] = "complete"
    trace["totalQueryMs"] = round(sum(query_durations_ms), 1)
    trace["slowestQueryMs"] = max(query_durations_ms) if query_durations_ms else 0.0
    return {
        "queryCount": len(node_queries) + relation_given_batch_count + relation_legacy_query_count,
        "nodeQueryCount": len(node_queries),
        "relationQueryCount": relation_given_batch_count + relation_legacy_query_count,
        "plannedRelationQueryCount": len(relation_queries),
        "requestedNodeCount": len(reuse_plan.get("nodeRows") or []),
        "requestedRelationCount": len(reuse_plan.get("relationRows") or []),
        "insertedNodeCount": len(node_rows_to_insert),
        "insertedRelationCount": len(relation_rows_to_insert),
        "reusedNodeCount": len(reuse_plan.get("reusedNodeRows") or []),
        "reusedRelationCount": len(reuse_plan.get("reusedRelationRows") or []),
        "storageLookupMode": str(reuse_plan.get("storageLookupMode") or ""),
        "expectedCountsByScope": expected_counts_by_scope,
        "insertedCountsByScope": inserted_counts_by_scope,
        "reusedCountsByScope": reused_counts_by_scope,
        "requestedRelationBreakdown": store.scoped_abox_relation_breakdown(
            reuse_plan.get("relationRows") or [],
        ),
        "insertedRelationBreakdown": store.scoped_abox_relation_breakdown(
            relation_rows_to_insert,
        ),
        "reusedRelationBreakdown": store.scoped_abox_relation_breakdown(
            reuse_plan.get("reusedRelationRows") or [],
        ),
        "relationBatchSize": relation_batch_size,
        "relationWriteMode": (
            "given-rows" if relation_given_batch_count and not relation_fallback_count
            else "given-rows-with-legacy-fallback" if relation_given_batch_count
            else "legacy-single-edge"
        ),
        "relationGivenBatchCount": relation_given_batch_count,
        "relationGivenTransactionCount": relation_given_transaction_count,
        "relationGivenRowCount": relation_given_row_count,
        "relationGivenFallbackCount": relation_fallback_count,
        "transactionQueryCount": batch_size,
        "transactionCount": (
            (len(node_queries) + batch_size - 1) // batch_size
            + relation_given_transaction_count
            + relation_legacy_transaction_count
        ),
        "slowestQueryMs": max(query_durations_ms) if query_durations_ms else 0.0,
        "totalQueryMs": round(sum(query_durations_ms), 1),
    }
