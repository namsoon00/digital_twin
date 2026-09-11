"""Graph write implementation; facade-independent dependencies."""

from __future__ import annotations
from .graph_write_ports import (
    GraphWritePort,
    WriteGraphBindings,
    ClearInferenceboxBindings,
    InsertQueriesBindings,
    GraphInsertQueriesBindings,
    StaticGraphInsertQueriesBindings,
)
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_storage_id,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from typing import Dict, Iterable, List, Tuple


def write_graph(
    _store: GraphWritePort,
    driver,
    imported,
    graph: PortfolioOntology,
    delete_boxes: Iterable[str] = None,
    *,
    _bindings: WriteGraphBindings,
) -> None:
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[
        0
    ]
    boxes = (
        _bindings.node_boxes(graph)
        if delete_boxes is None
        else list(delete_boxes or [])
    )
    static_replacement_boxes = {
        "TBox",
        "RuleBox",
        "RuleBoxGovernance",
        "LanguageGovernance",
    }
    static_boxes = sorted(static_replacement_boxes.intersection(boxes))
    # A broad static delete scans the whole ontology-node/assertion space
    # on a large durable ABox.  Delete each static box in bounded TypeQL
    # batches before inserting its replacement instead.  This keeps a
    # RuleBox-only policy change from monopolising the TypeDB writer.
    if static_boxes:
        _store.delete_box_rows_in_batches(driver, imported, static_boxes)
    delete_queries = _store.delete_queries(
        box for box in boxes if box not in static_replacement_boxes
    )
    graph_boxes = _bindings.node_boxes(graph)
    static_graph_write = bool(static_replacement_boxes.intersection(graph_boxes))
    insert_queries = (
        _store.static_graph_insert_queries(graph)
        if static_graph_write
        else _store.graph_insert_queries(graph)
    )
    if not delete_queries and not insert_queries:
        return
    transaction_query_count = (
        _store.abox_write_transaction_query_count()
        if "ABox" in graph_boxes
        else (
            _store.static_write_transaction_query_count()
            if static_graph_write
            else _store.graph_write_transaction_query_count()
        )
    )
    # Large static replacements span multiple batches. Commit their deletes
    # first so a later insert batch cannot collide with an old @unique
    # storage ID. Small ABoxControl pointer swaps remain one transaction.
    phases = (
        [delete_queries, insert_queries]
        if static_boxes
        else [delete_queries + insert_queries]
    )
    for queries in phases:
        for offset in range(0, len(queries), transaction_query_count):
            query_batch = queries[offset : offset + transaction_query_count]

            def write_batch():
                with _bindings.typedb_operation_timeout(
                    _store.write_operation_timeout_seconds(), "TypeDB graph write batch"
                ):
                    with driver.transaction(
                        _store.database,
                        TransactionType.WRITE,
                        options=_store.write_transaction_options(),
                    ) as tx:
                        for query in query_batch:
                            tx.query(query).resolve()
                        tx.commit()

            _store.with_typedb_retries(write_batch)


def clear_inferencebox(
    _store: GraphWritePort, world_id: str = "", *, _bindings: ClearInferenceboxBindings
) -> Dict[str, object]:
    if not _store.address:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return {
            "configured": True,
            "status": "driver-missing",
            "graphStore": "typedb",
            "reason": "typedb-driver Python package is not installed: "
            + str(imported[1])[:160],
        }
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[
        0
    ]
    try:

        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.ensure_schema(driver, imported)
                with driver.transaction(_store.database, TransactionType.WRITE) as tx:
                    world_clause = (
                        ", has ontology-world-id " + typedb_string(world_id)
                        if str(world_id or "").strip()
                        else ""
                    )
                    delete_queries = (
                        [
                            'match $r isa ontology-assertion, has ontology-box "InferenceBox"'
                            + world_clause
                            + "; delete $r;",
                            'match $n isa ontology-node, has ontology-box "InferenceBox"'
                            + world_clause
                            + "; delete $n;",
                        ]
                        if world_clause
                        else _store.delete_queries(["InferenceBox"])
                    )
                    for query in delete_queries:
                        tx.query(query).resolve()
                    tx.commit()
            finally:
                _store.close_driver(driver)

        _store.with_typedb_retries(operation)
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "clearedBox": "InferenceBox",
        }
    except (
        Exception
    ) as error:  # noqa: BLE001 - caller reports clear failure as inference boundary status.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
        }


def delete_queries(_store: GraphWritePort, boxes: Iterable[str]) -> List[str]:
    queries = []
    for box in sorted(
        set(str(item or "").strip() for item in boxes if str(item or "").strip())
    ):
        queries.append(
            "match $r isa ontology-assertion, has ontology-box "
            + typedb_string(box)
            + "; delete $r;"
        )
        queries.append(
            "match $n isa ontology-node, has ontology-box "
            + typedb_string(box)
            + "; delete $n;"
        )
    return queries


def insert_queries(
    _store: GraphWritePort,
    graph: PortfolioOntology,
    *,
    _bindings: InsertQueriesBindings,
) -> List[str]:
    queries: List[str] = []
    updated_at = _bindings.utc_now()
    node_rows, relation_rows = _store.graph_persistence_rows(graph)
    for row in node_rows:
        queries.append(_store.node_insert_query(row, updated_at))
    for row in relation_rows:
        queries.append(_store.relation_insert_query(row, updated_at))
    return [item for item in queries if item]


def graph_persistence_rows(
    _store: GraphWritePort, graph: PortfolioOntology
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    node_rows = _store.node_rows(graph)
    endpoint_rows = _store.node_rows(graph, include_external_relation_endpoints=True)
    node_rows_by_id = {
        str(row.get("id") or ""): row
        for row in endpoint_rows
        if str(row.get("id") or "")
    }
    node_ids = set(node_rows_by_id)
    relation_rows = [
        {
            **row,
            "sourceStorageId": ontology_storage_id(
                node_rows_by_id[str(row.get("source") or "")],
                row.get("source"),
                "node",
            ),
            "targetStorageId": ontology_storage_id(
                node_rows_by_id[str(row.get("target") or "")],
                row.get("target"),
                "node",
            ),
        }
        for row in _store.rows_for_relations(graph)
        + _store.support_relation_rows(graph)
        if str(row.get("source") or "") in node_ids
        and str(row.get("target") or "") in node_ids
    ]
    return node_rows, relation_rows


def graph_insert_queries(
    _store: GraphWritePort,
    graph: PortfolioOntology,
    *,
    _bindings: GraphInsertQueriesBindings,
) -> List[str]:
    updated_at = _bindings.utc_now()
    node_rows, relation_rows = _store.graph_persistence_rows(graph)
    settings = _bindings.runtime_settings()
    node_batch_size = _store.abox_node_batch_size(settings)
    relation_batch_size = _store.abox_relation_batch_size(settings)
    max_query_bytes = _store.write_query_max_bytes(settings)
    return [
        *_store.batched_node_insert_queries(
            node_rows, updated_at, node_batch_size, max_query_bytes
        ),
        *_store.batched_relation_insert_queries(
            relation_rows, updated_at, relation_batch_size, max_query_bytes
        ),
    ]


def static_graph_insert_queries(
    _store: GraphWritePort,
    graph: PortfolioOntology,
    *,
    _bindings: StaticGraphInsertQueriesBindings,
) -> List[str]:
    """Build static TBox/RuleBox writes without exponential node batches.

    Relation queries remain deliberately one edge each: grouping unrelated
    endpoint matches creates a TypeDB planner cross product.  They are
    still committed in short transactions by ``write_graph``.
    """
    updated_at = _bindings.utc_now()
    node_rows, relation_rows = _store.graph_persistence_rows(graph)
    settings = _bindings.runtime_settings()
    return [
        *_store.batched_node_insert_queries(
            node_rows,
            updated_at,
            _store.static_node_insert_batch_size(settings),
            _store.write_query_max_bytes(settings),
        ),
        *_store.batched_relation_insert_queries(
            relation_rows,
            updated_at,
            1,
            _store.write_query_max_bytes(settings),
        ),
    ]


def external_relation_endpoint_ids(graph: PortfolioOntology) -> set:
    return {
        str(item.entity_id or "")
        for item in getattr(graph, "entities", []) or []
        if str(item.entity_id or "")
        and bool(
            dict(getattr(item, "properties", {}) or {}).get(
                "_typedbExternalEndpointRef"
            )
        )
    }
