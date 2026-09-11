"""TypeDB static-seed graphs owner; no facade or runtime construction."""

from .graphs_ports import GraphsStore
from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_storage_id,
    relation_row_id,
)
from typing import Dict, Iterable, List, Tuple
import copy


def graph_for_boxes(
    _store: GraphsStore,
    graph: PortfolioOntology,
    boxes: Iterable[str],
    retain_cross_box_relations: bool = False,
) -> PortfolioOntology:
    """Return a persistence-safe graph slice for the requested ontology boxes.

    Static boxes are normally independent, except for a small number of
    declaration edges such as ``TBox RuleBox -> RuleBox RuleRegistry``.
    A targeted RuleBox refresh must retain those edges without re-inserting
    the already durable TBox endpoint.  External endpoints are therefore
    kept only as in-memory lookup rows and are excluded from node writes.
    """
    allowed = {
        str(item or "").strip() for item in boxes or [] if str(item or "").strip()
    }
    if not allowed:
        return PortfolioOntology(str(graph.portfolio_id or "typedb-empty"))
    clone = copy.deepcopy(graph)
    source_entities = list(clone.entities)
    source_entity_ids = {
        str(item.entity_id or "")
        for item in source_entities
        if str(item.entity_id or "")
    }
    selected_entities = [
        item
        for item in source_entities
        if str((item.properties or {}).get("ontologyBox") or "ABox") in allowed
    ]
    selected_entity_ids = {str(item.entity_id or "") for item in selected_entities}
    selected_relations = [
        item
        for item in clone.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox") in allowed
        and str(item.source or "") in source_entity_ids
        and (str(item.target or "") in source_entity_ids)
        and (
            retain_cross_box_relations
            or (
                str(item.source or "") in selected_entity_ids
                and str(item.target or "") in selected_entity_ids
            )
        )
    ]
    if retain_cross_box_relations:
        endpoint_ids = {
            str(endpoint or "")
            for relation in selected_relations
            for endpoint in [relation.source, relation.target]
            if str(endpoint or "")
        }
        external_endpoint_ids = endpoint_ids - selected_entity_ids
        selected_entities.extend(
            (
                item
                for item in source_entities
                if str(item.entity_id or "") in external_endpoint_ids
            )
        )
        for item in selected_entities:
            if str(item.entity_id or "") in external_endpoint_ids:
                item.properties = dict(item.properties or {})
                item.properties["_typedbExternalEndpointRef"] = True
    clone.entities = selected_entities
    clone.relations = selected_relations
    clone.evidence = [
        item
        for item in clone.evidence
        if str((item.value or {}).get("ontologyBox") or "ABox") in allowed
    ]
    return clone


def graph_with_static_seed_generation(
    _store: GraphsStore, graph: PortfolioOntology, boxes: Iterable[str], generation_id
) -> PortfolioOntology:
    """Attach immutable static generation IDs to selected persisted boxes.

    ``generation_id`` accepts either one shared value or a per-box map.
    The latter is required for targeted static updates: a changed RuleBox
    must still link to the active TBox generation without rewriting that
    TBox.  Cross-box endpoint references receive their owning box's
    storage identity but remain excluded from node writes.
    """
    selected_boxes = {
        str(item or "").strip() for item in boxes or [] if str(item or "").strip()
    }
    if isinstance(generation_id, dict):
        generation_by_box = {
            str(box or "").strip(): str(value or "").strip()
            for (box, value) in generation_id.items()
            if str(box or "").strip() and str(value or "").strip()
        }
    else:
        clean_generation = str(generation_id or "").strip()
        generation_by_box = {
            box: clean_generation for box in selected_boxes if clean_generation
        }
    if not generation_by_box or not selected_boxes:
        return graph
    clone = copy.deepcopy(graph)
    for item in clone.entities:
        properties = dict(item.properties or {})
        box = str(properties.get("ontologyBox") or "ABox")
        generation = generation_by_box.get(box)
        if generation:
            properties["snapshotId"] = generation
            properties["staticSeedGeneration"] = generation
            item.properties = properties
    for item in clone.relations:
        properties = dict(item.properties or {})
        box = str(properties.get("ontologyBox") or "ABox")
        generation = generation_by_box.get(box)
        if generation:
            properties["snapshotId"] = generation
            properties["staticSeedGeneration"] = generation
            item.properties = properties
    return clone


def seed_static_manifest_graph(
    _store: GraphsStore,
    graph: PortfolioOntology,
    rules_payload: List[Dict[str, object]],
    tbox_metadata: Dict[str, object] = None,
) -> PortfolioOntology:
    metadata = _store.seed_static_manifest_metadata(
        graph, rules_payload, tbox_metadata=tbox_metadata
    )
    return PortfolioOntology(
        "typedb-static-seed-manifest",
        entities=[
            OntologyEntity(
                _store.seed_static_manifest_entity_id(),
                "TypeDB static ontology seed manifest",
                "ontology-seed-manifest",
                {
                    "ontologyBox": "TBox",
                    "tboxClass": "OntologySeedManifest",
                    **metadata,
                },
            )
        ],
    )


def seed_static_sentinels(
    _store: GraphsStore, graph: PortfolioOntology, generation_ids=None
) -> List[Dict[str, str]]:
    """Return stable static records that must exist beside a valid manifest."""
    if isinstance(generation_ids, dict):
        resolved_generation_ids = _store.static_seed_generation_ids(generation_ids)
        if not resolved_generation_ids:
            resolved_generation_ids = {
                str(box or "").strip(): str(value or "").strip()
                for (box, value) in generation_ids.items()
                if str(box or "").strip() and str(value or "").strip()
            }
    else:
        rulebox_snapshot_id = str(generation_ids or "").strip()
        resolved_generation_ids = (
            {"RuleBox": rulebox_snapshot_id} if rulebox_snapshot_id else {}
        )
    static_graph = _store.graph_with_static_seed_generation(
        graph, _store.seed_static_box_names(), resolved_generation_ids
    )
    (node_rows, relation_rows) = _store.graph_persistence_rows(static_graph)
    candidates: List[Tuple[str, Dict[str, object]]] = []
    for row in node_rows:
        node_id = str(row.get("id") or "")
        if node_id == "ontology-box:TBox":
            candidates.append(("tbox", row))
        elif str(row.get("kind") or "") == "rule-registry":
            candidates.append(("rulebox", row))
        elif str(row.get("kind") or "") == "language-registry-version":
            candidates.append(("language", row))
    for row in relation_rows:
        if (
            str(row.get("type") or "") == "DEFINES_RULE"
            and str(row.get("source") or "") == "ontology-box:RuleBox"
        ):
            candidates.append(("rulebox-declaration", row))
            break
    sentinels = []
    seen = set()
    for name, row in candidates:
        if name in seen:
            continue
        seen.add(name)
        owner_kind = "relation" if "source" in row and "target" in row else "node"
        canonical_id = (
            relation_row_id(row) if owner_kind == "relation" else row.get("id")
        )
        sentinels.append(
            {
                "name": name,
                "type": (
                    "ontology-assertion"
                    if owner_kind == "relation"
                    else "ontology-node"
                ),
                "storageId": ontology_storage_id(row, canonical_id, owner_kind),
            }
        )
    return sentinels
