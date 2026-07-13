from typing import Dict, Iterable

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GraphInferenceRule
from ..domain.ontology_schema import default_tbox_metadata, normalize_tbox_metadata
from .graph_store_rulebox import rulebox_graph_from_rules


def ontology_seed_graph(rules: Iterable[GraphInferenceRule] = None) -> PortfolioOntology:
    graph = rulebox_graph_from_rules(rules or default_graph_inference_rules())
    graph.portfolio_id = "ontology-seed"
    graph.worldview.update({
        "model": "investment-ontology-seed",
        "description": "TBox schema and default RuleBox concepts persisted to TypeDB before runtime ABox projections arrive.",
        "skipNativeReasoning": True,
    })
    return graph


def graph_box_entity_counts(graph: PortfolioOntology) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in graph.entities:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        counts[box] = counts.get(box, 0) + 1
    return counts


def graph_box_relation_counts(graph: PortfolioOntology) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in graph.relations:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        counts[box] = counts.get(box, 0) + 1
    return counts


def active_tbox_metadata_unavailable(status: str, reason: str, source: str) -> Dict[str, object]:
    metadata = default_tbox_metadata()
    metadata.update({
        "configured": True,
        "status": status,
        "source": "code-fallback",
        "storeSource": source,
        "reason": reason,
    })
    return metadata


def active_tbox_metadata_from_rows(rowsets: Dict[str, list], source: str) -> Dict[str, object]:
    entity_row = (rowsets.get("entities") or [{}])[0]
    relation_row = (rowsets.get("relations") or [{}])[0]
    entity_count = int(entity_row.get("entityCount") or 0)
    if entity_count <= 0:
        metadata = default_tbox_metadata()
        metadata.update({
            "configured": True,
            "status": "code-fallback",
            "source": "code-fallback",
            "storeSource": source,
            "reason": "저장된 TBox 노드가 없어 코드 TBox 메타데이터를 사용합니다.",
        })
        return metadata
    metadata = normalize_tbox_metadata({
        "source": source,
        "version": entity_row.get("version") or default_tbox_metadata()["version"],
        "fingerprint": entity_row.get("fingerprint") or default_tbox_metadata()["fingerprint"],
        "entityCount": entity_count,
        "relationCount": int(relation_row.get("relationCount") or 0),
        "status": "ok",
    })
    metadata.update({
        "configured": True,
        "storeSource": source,
        "updatedAt": str(entity_row.get("updatedAt") or ""),
    })
    return metadata
