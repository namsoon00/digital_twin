"""Lifecycle for generation-scoped InferenceBox publication."""

from typing import Dict, Iterable

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from .markers import inference_generation_marker_row
from .ports import PublicationRuntime, PublicationStore


def activate_inference_generation(
    store: PublicationStore,
    graph: PortfolioOntology,
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
    world_id: str = '',
    *,
    runtime: PublicationRuntime,
) -> Dict[str, object]:
    generation_id = str((graph.worldview or {}).get("inferenceGenerationId") or "").strip()
    if not generation_id:
        return {"status": "invalid", "activated": False, "reason": "generation id is empty"}
    imported = store.driver_imports()
    if imported[0] is None:
        return {"status": "driver-missing", "activated": False, "reason": str(imported[1])[:180]}
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    active_marker_query = store.node_insert_query(
        inference_generation_marker_row(graph, node_rows, relation_rows, "active", now=runtime.now),
        runtime.now(),
    )
    marker_world_clause = (
        ", has ontology-world-id " + typedb_string(world_id)
        if str(world_id or "").strip()
        else ""
    )
    delete_markers = [
        'match $n isa ontology-node, has ontology-box "InferenceBox", has ontology-kind "inference-generation"'
        + marker_world_clause + "; delete $n;",
        'match $n isa ontology-node, has ontology-box "InferenceBox", has ontology-kind "inference-generation-candidate"'
        + marker_world_clause + "; delete $n;",
    ]
    try:
        def operation():
            with runtime.timeout(store.write_operation_timeout_seconds(), "TypeDB InferenceBox generation activation"):
                driver = store.open_driver(imported)
                try:
                    store.ensure_database(driver)
                    with driver.transaction(store.database, TransactionType.WRITE) as tx:
                        for query in delete_markers:
                            tx.query(query).resolve()
                        tx.query(active_marker_query).resolve()
                        tx.commit()
                finally:
                    store.close_driver(driver)
        store.with_typedb_retries(operation)
        return {
            "status": "ok",
            "activated": True,
            "activeGenerationId": generation_id,
            "worldId": world_id,
            "activationMode": "validated-candidate-pointer-swap",
        }
    except Exception as error:  # noqa: BLE001 - preserve the previous active marker on transaction failure.
        return {
            "status": "error",
            "activated": False,
            "activeGenerationId": generation_id,
            "reasonCode": runtime.error_code(error),
            "reason": str(error)[:220],
            "preservedPreviousInference": True,
        }


def prune_inferencebox_generations(
    store: PublicationStore,
    active_generation_id: str,
    keep_count: int = 2,
    world_id: str = '',
) -> Dict[str, object]:
    active_generation_id = str(active_generation_id or "").strip()
    if not active_generation_id:
        return {"configured": bool(store.address), "status": "skipped", "reason": "active generation id is empty"}
    try:
        records = store.read_inference_generation_records(published_only=False, world_id=world_id)
    except Exception as error:  # noqa: BLE001 - pruning must not fail materialization.
        return {"configured": True, "status": "error", "reason": str(error)[:180], "activeGenerationId": active_generation_id}
    if not records:
        return {"configured": True, "status": "skipped", "reason": "no generation-scoped InferenceBox rows", "activeGenerationId": active_generation_id}
    keep = {active_generation_id}
    published_records = [item for item in records if str(item.get("publicationStatus") or "active") in {"active", "published"}]
    for item in sorted(published_records, key=lambda row: str(row.get("latestAt") or ""), reverse=True)[: max(1, int(keep_count or 2))]:
        keep.add(str(item.get("generationId") or ""))
    prune_ids = [
        str(item.get("generationId") or "")
        for item in records
        if str(item.get("generationId") or "") and str(item.get("generationId") or "") not in keep
    ]
    if not prune_ids:
        return {
            "configured": True,
            "status": "ok",
            "activeGenerationId": active_generation_id,
            "keptGenerationCount": len(keep),
            "deletedGenerationCount": 0,
        }
    imported = store.driver_imports()
    if imported[0] is None:
        return {"configured": True, "status": "driver-missing", "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160]}
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    queries = []
    for generation_id in prune_ids:
        queries.append(
            "match $r isa ontology-assertion, has ontology-box \"InferenceBox\", has ontology-snapshot-id "
            + typedb_string(generation_id)
            + (", has ontology-world-id " + typedb_string(world_id) if str(world_id or "").strip() else "")
            + "; delete $r;"
        )
        queries.append(
            "match $n isa ontology-node, has ontology-box \"InferenceBox\", has ontology-snapshot-id "
            + typedb_string(generation_id)
            + (", has ontology-world-id " + typedb_string(world_id) if str(world_id or "").strip() else "")
            + "; delete $n;"
        )
    try:
        def operation():
            driver = store.open_driver(imported)
            try:
                store.ensure_database(driver)
                store.ensure_schema(driver, imported)
                with driver.transaction(store.database, TransactionType.WRITE) as tx:
                    for query in queries:
                        tx.query(query).resolve()
                    tx.commit()
            finally:
                store.close_driver(driver)
        store.with_typedb_retries(operation)
        return {
            "configured": True,
            "status": "ok",
            "activeGenerationId": active_generation_id,
            "worldId": world_id,
            "keptGenerationCount": len(keep),
            "deletedGenerationCount": len(prune_ids),
            "deletedGenerationIds": prune_ids[:20],
        }
    except Exception as error:  # noqa: BLE001 - pruning is non-critical but must be visible.
        return {
            "configured": True,
            "status": "error",
            "reason": str(error)[:220],
            "activeGenerationId": active_generation_id,
            "deletedGenerationCount": 0,
        }
