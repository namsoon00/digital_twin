"""TypeDB static-seed reads owner; no facade or runtime construction."""

from .reads_ports import ReadsStore
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_storage_id,
    relation_row_id,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import (
    json_object,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from typing import Dict, List


def read_seed_static_manifest(_store: ReadsStore) -> Dict[str, object]:
    """Read the static seed manifest through its unique storage identity."""
    query = (
        "match $n isa ontology-node, has ontology-storage-id "
        + typedb_string(_store.seed_static_manifest_storage_id())
        + ", has ontology-json $json; limit 1;"
    )
    try:
        rows = _store.read_rows(query, ["json"], label="typedb.static-seed-manifest")
    except Exception as error:
        return {"status": "error", "reason": str(error)[:180], "metadata": {}}
    metadata = json_object((rows[0] if rows else {}).get("json"))
    if not metadata:
        return {"status": "missing", "metadata": {}}
    if str(metadata.get("manifestVersion") or "") != "typedb-static-seed-manifest-v1":
        return {"status": "invalid", "metadata": metadata}
    return {"status": "ok", "metadata": metadata}


def seed_static_sentinels_present(
    _store: ReadsStore, graph: PortfolioOntology, generation_ids=None
) -> Dict[str, object]:
    missing = []
    try:
        for sentinel in _store.seed_static_sentinels(graph, generation_ids):
            rows = _store.read_rows(
                "match $item isa "
                + sentinel["type"]
                + ", has ontology-storage-id "
                + typedb_string(sentinel["storageId"])
                + "; limit 1;",
                [],
                label="typedb.static-seed-sentinel",
            )
            if not rows:
                missing.append(sentinel["name"])
    except Exception as error:
        return {"status": "error", "missing": missing, "reason": str(error)[:180]}
    return {"status": "ok" if not missing else "missing", "missing": missing}


def seed_static_node_properties(
    _store: ReadsStore, graph: PortfolioOntology, entity_id_value: str
) -> Dict[str, object]:
    node_row = next(
        (
            row
            for row in _store.node_rows(graph)
            if str(row.get("id") or "") == str(entity_id_value or "")
        ),
        None,
    )
    if not isinstance(node_row, dict):
        return {}
    query = (
        "match $n isa ontology-node, has ontology-storage-id "
        + typedb_string(ontology_storage_id(node_row, node_row.get("id"), "node"))
        + ", has ontology-json $json; limit 1;"
    )
    rows = _store.read_rows(query, ["json"], label="typedb.static-seed-node")
    return json_object((rows[0] if rows else {}).get("json"))


def missing_seed_relation_rows(
    _store: ReadsStore, graph: PortfolioOntology
) -> List[Dict[str, object]]:
    """Return immutable static relation rows absent from the graph store."""
    (_node_rows, relation_rows) = _store.graph_persistence_rows(graph)
    expected_boxes = {
        str(row.get("ontologyBox") or "ABox")
        for row in relation_rows
        if str(row.get("ontologyBox") or "ABox") != "ABox"
    }
    stored_ids = set()
    for box in sorted(expected_boxes):
        rows = _store.read_rows(
            "match $r isa ontology-assertion, has ontology-box "
            + typedb_string(box)
            + ", has ontology-id $id;",
            ["id"],
            label="typedb.seed-relation-repair-audit",
        )
        stored_ids.update((str(row.get("id") or "") for row in rows))
    return [
        row
        for row in relation_rows
        if str(row.get("ontologyBox") or "ABox") in expected_boxes
        and relation_row_id(row) not in stored_ids
    ]
