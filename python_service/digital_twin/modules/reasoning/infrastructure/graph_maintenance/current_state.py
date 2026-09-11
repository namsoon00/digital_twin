"""graph_maintenance: current_state through explicit injected capabilities."""

from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_value_match
from typing import Dict
from typing import Iterable
from typing import List
from typing import Tuple
import time
from .current_state_ports import GraphMaintenanceCurrentStateStore, GraphMaintenanceCurrentStateRuntime


def delete_current_state_slot_rows(_store: GraphMaintenanceCurrentStateStore, driver, imported, physical_generation_ids: Iterable[str], *, _bindings: GraphMaintenanceCurrentStateRuntime) -> Dict[str, object]:
    """Replace inactive physical slots with bounded grouped deletes."""

    generation_ids = sorted({
        str(value or "").strip()
        for value in physical_generation_ids or []
        if str(value or "").startswith("abox-current:")
    })
    if not generation_ids:
        return {
            "status": "skipped",
            "physicalGenerationCount": 0,
            "transactionCount": 0,
        }
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    batch_size = _store.current_state_inventory_batch_size()
    transaction_count = 0
    started = time.monotonic()
    for offset in range(0, len(generation_ids), batch_size):
        batch = generation_ids[offset: offset + batch_size]
        for type_label, variable in [
            ("ontology-assertion", "$r"),
            ("ontology-node", "$n"),
        ]:
            query = (
                "match " + variable + " isa " + type_label
                + ', has ontology-box "ABox", has ontology-snapshot-id $slot; '
                + typedb_value_match(
                    variable,
                    "ontology-snapshot-id",
                    batch,
                    "==",
                    "slotFilter",
                )
                + " delete " + variable + ";"
            )

            def delete_batch():
                with _bindings.typedb_operation_timeout(
                    _store.write_operation_timeout_seconds(),
                    "TypeDB current-state inactive slot delete",
                ):
                    with driver.transaction(
                        _store.database,
                        TransactionType.WRITE,
                        options=_store.write_transaction_options(),
                    ) as tx:
                        tx.query(query).resolve()
                        tx.commit()

            _store.with_typedb_retries(delete_batch)
            transaction_count += 1
    return {
        "status": "ok",
        "physicalGenerationCount": len(generation_ids),
        "transactionCount": transaction_count,
        "durationMs": int((time.monotonic() - started) * 1000),
    }


def delete_current_state_storage_ids(_store: GraphMaintenanceCurrentStateStore, driver, imported, node_storage_ids: Iterable[str], relation_storage_ids: Iterable[str], *, _bindings: GraphMaintenanceCurrentStateRuntime) -> Dict[str, object]:
    """Delete stale slot rows by exact unique identity before reinsertion."""

    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    transaction_count = 0
    deleted_identity_count = 0
    started = time.monotonic()
    delete_queries: List[Tuple[str, int]] = []
    for type_label, variable, raw_ids in [
        ("ontology-assertion", "$r", relation_storage_ids),
        ("ontology-node", "$n", node_storage_ids),
    ]:
        ids = sorted({
            str(value or "").strip()
            for value in raw_ids or []
            if str(value or "").strip()
        })
        for offset in range(0, len(ids), 64):
            batch = ids[offset: offset + 64]
            query = (
                "match " + variable + " isa " + type_label
                + ", has ontology-storage-id $storageId; "
                + typedb_value_match(
                    variable,
                    "ontology-storage-id",
                    batch,
                    "==",
                    "storageIdFilter",
                )
                + " delete " + variable + ";"
            )
            delete_queries.append((query, len(batch)))

    # Relation deletes stay ahead of node deletes, but bounded query
    # batches share a transaction. The previous one-commit-per-64-ids
    # path spent substantially more time on transaction validation than
    # on TypeQL execution during a normal two-symbol current-state patch.
    transaction_query_count = _store.abox_write_transaction_query_count(
        _bindings.runtime_settings()
    )
    for offset in range(0, len(delete_queries), transaction_query_count):
        query_batch = delete_queries[offset: offset + transaction_query_count]

        def delete_batch():
            with _bindings.typedb_operation_timeout(
                _store.write_operation_timeout_seconds(),
                "TypeDB current-state delta delete",
            ):
                with driver.transaction(
                    _store.database,
                    TransactionType.WRITE,
                    options=_store.write_transaction_options(),
                ) as tx:
                    for query, _identity_count in query_batch:
                        tx.query(query).resolve()
                    tx.commit()

        _store.with_typedb_retries(delete_batch)
        transaction_count += 1
        deleted_identity_count += sum(
            identity_count for _query, identity_count in query_batch
        )
    return {
        "status": "ok",
        "deletedIdentityCount": deleted_identity_count,
        "queryCount": len(delete_queries),
        "transactionCount": transaction_count,
        "transactionQueryCount": transaction_query_count,
        "durationMs": int((time.monotonic() - started) * 1000),
    }
