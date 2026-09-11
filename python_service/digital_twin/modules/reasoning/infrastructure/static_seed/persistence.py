"""TypeDB static-seed persistence owner; no facade or runtime construction."""

from .persistence_ports import PersistenceStore, PersistenceBindings
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_to_payload
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from typing import Dict, Iterable, List


def save_static_seed_boxes(
    _store: PersistenceStore,
    graph: PortfolioOntology,
    boxes: Iterable[str],
    rules_payload: List[Dict[str, object]] = None,
    schema_prepared: bool = False,
    tbox_metadata: Dict[str, object] = None,
    *,
    _bindings: PersistenceBindings
) -> Dict[str, object]:
    """Refresh only selected immutable seed boxes through one graph write.

    This bypasses ``save_graph`` because a targeted RuleBox slice carries
    a read-only TBox endpoint for its cross-box declaration relation.  The
    endpoint is used to match the existing node, never inserted or deleted.
    Every static box is append-only. The manifest pointer activates the
    completed TBox/RuleBox/language generation after this write succeeds,
    so a TBox evolution never scans or deletes the live ABox.
    """
    selected_boxes = [
        box
        for box in _store.seed_static_box_names()
        if box in {str(item or "").strip() for item in boxes or []}
    ]
    if not selected_boxes:
        return {
            "configured": bool(_store.address),
            "saved": True,
            "status": "unchanged",
            "graphStore": "typedb",
            "refreshedBoxes": [],
        }
    if not _store.address:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "refreshedBoxes": selected_boxes,
            "reason": "TypeDB ontology storage is not configured.",
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        result = _store.driver_missing_result(imported[1], graph)
        result["refreshedBoxes"] = selected_boxes
        return result
    slice_graph = _store.graph_for_boxes(
        graph, selected_boxes, retain_cross_box_relations=True
    )
    metadata = _store.seed_static_manifest_metadata(
        graph,
        list(
            rules_payload
            or rulebox_rules_to_payload(
                _store._last_rules or default_graph_inference_rules()
            )
        ),
        tbox_metadata=tbox_metadata,
    )
    generation_ids = _store.static_seed_generation_ids(metadata)
    slice_graph = _store.graph_with_static_seed_generation(
        slice_graph, _store.seed_static_box_names(), generation_ids
    )
    rulebox_generation = str(generation_ids.get("RuleBox") or "")
    delete_boxes: List[str] = []
    (node_rows, relation_rows) = _store.graph_persistence_rows(slice_graph)
    try:

        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                if not schema_prepared:
                    _store.ensure_schema(driver, imported)
                _store.write_graph(
                    driver, imported, slice_graph, delete_boxes=delete_boxes
                )
            finally:
                _store.close_driver(driver)

        _store.with_typedb_retries(operation)
    except Exception as error:
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "refreshedBoxes": selected_boxes,
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:240],
            "entityCount": len(node_rows),
            "relationCount": len(relation_rows),
            "ruleboxSnapshotId": rulebox_generation,
        }
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "graphStore": "typedb",
        "refreshedBoxes": selected_boxes,
        "entityCount": len(node_rows),
        "relationCount": len(relation_rows),
        "crossBoxEndpointReferenceCount": len(
            _store.external_relation_endpoint_ids(slice_graph)
        ),
        "ruleboxSnapshotId": rulebox_generation,
        "staticGenerationIds": generation_ids,
        "staticWriteMode": "append-only-static-generation",
    }


def save_seed_static_manifest(
    _store: PersistenceStore,
    graph: PortfolioOntology,
    rules_payload: List[Dict[str, object]],
    schema_prepared: bool = False,
    tbox_metadata: Dict[str, object] = None,
    *,
    _bindings: PersistenceBindings
) -> Dict[str, object]:
    """Atomically publish the static seed identity after a successful refresh."""
    manifest_graph = _store.seed_static_manifest_graph(
        graph, rules_payload, tbox_metadata=tbox_metadata
    )
    metadata = _store.seed_static_manifest_metadata(
        graph, rules_payload, tbox_metadata=tbox_metadata
    )
    if not _store.address:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return _store.driver_missing_result(imported[1], manifest_graph)
    (_TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType) = (
        imported[0]
    )
    delete_query = (
        "match $n isa ontology-node, has ontology-storage-id "
        + typedb_string(_store.seed_static_manifest_storage_id())
        + "; delete $n;"
    )
    try:

        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                if not schema_prepared:
                    _store.ensure_schema(driver, imported)
                insert_queries = _store.static_graph_insert_queries(manifest_graph)
                if not insert_queries:
                    raise ValueError("Static seed manifest replacement is empty.")
                # Keep the old pointer visible until the replacement commits.
                with _bindings.typedb_operation_timeout(
                    _store.write_operation_timeout_seconds(),
                    "TypeDB static seed manifest activation",
                ):
                    with driver.transaction(
                        _store.database,
                        TransactionType.WRITE,
                        options=_store.write_transaction_options(),
                    ) as tx:
                        tx.query(delete_query).resolve()
                        for query in insert_queries:
                            tx.query(query).resolve()
                        tx.commit()
            finally:
                _store.close_driver(driver)

        _store.with_typedb_retries(operation)
    except Exception as error:
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:240],
        }
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "graphStore": "typedb",
        "staticSeedFingerprint": metadata.get("staticSeedFingerprint"),
    }
