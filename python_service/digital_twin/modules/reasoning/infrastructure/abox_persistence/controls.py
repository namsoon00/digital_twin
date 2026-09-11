"""Scoped active-pointer and pending-journal storage transactions."""

from typing import Dict, Iterable

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from .ports import ABoxControlStore, ABoxRuntime


class AtomicControlPatchTooLarge(RuntimeError):
    reason_code = "typedbAtomicControlPatchLimit"

    def __init__(self, query_count: int, transaction_limit: int):
        self.query_count = query_count
        self.transaction_limit = transaction_limit
        super().__init__(
            "Scoped ABox control patch requires " + str(query_count)
            + " queries, exceeding the atomic transaction limit " + str(transaction_limit)
            + ". No control rows were changed."
        )


def replace_scoped_abox_control_graph(
    store: ABoxControlStore,
    driver,
    imported,
    graph: PortfolioOntology,
    world_id: str = "",
    scope_ids: Iterable[str] = None,
    replace_all_scope_pointers: bool = False,
    *,
    runtime: ABoxRuntime,
) -> Dict[str, object]:
    """Atomically swap the active Manifest and only changed scope controls.

    Scope pointers deliberately outlive individual Manifest ids. The active
    Manifest row and activation journal are always replaced, while stable
    scope pointers remain untouched. This turns the normal live path into
    a small control-plane patch rather than a rewrite of every holding.
    """
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    clean_scope_ids = sorted({
        str(scope_id or "").strip()
        for scope_id in scope_ids or []
        if str(scope_id or "").strip()
    })
    delete_queries = [
        store.scoped_abox_control_delete_query("worldview-manifest-active-pointer", world_id),
        store.scoped_abox_control_delete_query("abox-activation-pending", world_id),
    ]
    if replace_all_scope_pointers:
        delete_queries.append(
            store.scoped_abox_control_delete_query("abox-scope-active-pointer", world_id)
        )
    else:
        delete_queries.extend(
            store.scoped_abox_control_delete_query(
                "abox-scope-active-pointer",
                world_id,
                scope_id,
            )
            for scope_id in clean_scope_ids
        )
    insert_queries = store.graph_insert_queries(graph)
    queries = delete_queries + insert_queries
    if not queries:
        return {
            "status": "skipped",
            "worldId": str(world_id or ""),
            "queryCount": 0,
            "transactionCount": 0,
        }
    # Control rows are tiny and must change together. Keep a bounded but
    # substantially larger transaction than the ABox row writer, whose
    # one-edge batches protect a very different high-volume path.
    raw_limit = number_or_none(runtime.settings().get("typedbScopedControlWriteTransactionQueryCount"))
    transaction_limit = int(raw_limit) if raw_limit is not None else 256
    transaction_limit = max(8, min(512, transaction_limit))
    # Never split pointer deletion, replacement and the recovery journal into
    # separate commits. An oversized patch must fail before the first write.
    if len(queries) > transaction_limit:
        raise AtomicControlPatchTooLarge(len(queries), transaction_limit)

    def write_batch():
        with runtime.timeout(
            store.write_operation_timeout_seconds(),
            "TypeDB scoped ABox control patch",
        ):
            with driver.transaction(
                store.database,
                TransactionType.WRITE,
                options=store.write_transaction_options(),
            ) as tx:
                for query in queries:
                    tx.query(query).resolve()
                tx.commit()

    store.with_typedb_retries(write_batch)
    return {
        "status": "ok",
        "worldId": str(world_id or ""),
        "mode": (
            "full-scoped-control-rebuild"
            if replace_all_scope_pointers
            else "incremental-scoped-control-patch"
        ),
        "replacedScopeIds": clean_scope_ids,
        "scopePointerWriteCount": len([
            entity
            for entity in graph.entities
            if str(entity.kind or "") == "abox-scope-active-pointer"
        ]),
        "queryCount": len(queries),
        "transactionCount": 1,
        "atomic": True,
    }


def clear_scoped_abox_pending_activation(
    store: ABoxControlStore,
    world_id: str = "",
    *,
    runtime: ABoxRuntime,
) -> Dict[str, object]:
    """Clear only the activation journal after aligned native inference."""
    imported = store.driver_imports()
    if imported[0] is None:
        return store.driver_missing_result(imported[1], PortfolioOntology("typedb-scoped-control"))
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    query = store.scoped_abox_control_delete_query("abox-activation-pending", world_id)
    try:
        def operation():
            driver = store.open_driver(imported)
            try:
                store.ensure_database(driver)
                store.ensure_schema(driver, imported)
                with runtime.timeout(
                    store.write_operation_timeout_seconds(),
                    "TypeDB scoped ABox activation journal clear",
                ):
                    with driver.transaction(
                        store.database,
                        TransactionType.WRITE,
                        options=store.write_transaction_options(),
                    ) as tx:
                        tx.query(query).resolve()
                        tx.commit()
            finally:
                store.close_driver(driver)

        store.with_typedb_retries(operation)
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "mode": "pending-journal-clear",
        }
    except Exception as error:  # noqa: BLE001 - retain the journal when its clear cannot commit.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reasonCode": runtime.error_code(error),
            "reason": str(error)[:220],
        }


def scoped_abox_control_delete_query(
    kind: str,
    world_id: str = "",
    scope_id: str = "",
) -> str:
    """Delete one control-plane node class, optionally for one scope."""
    return (
        'match $n isa ontology-node, has ontology-box "ABoxControl", has ontology-kind '
        + typedb_string(kind)
        + (
            ", has ontology-world-id " + typedb_string(world_id)
            if str(world_id or "").strip()
            else ""
        )
        + (
            ", has ontology-scope-id " + typedb_string(scope_id)
            if str(scope_id or "").strip()
            else ""
        )
        + "; delete $n;"
    )
