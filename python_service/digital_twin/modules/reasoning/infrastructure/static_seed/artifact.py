"""Exact release artifact serialization and rehydration; no runtime reads."""

from typing import Dict, Iterable, Mapping
from digital_twin.domain.ontology_contracts import (
    OntologyEntity,
    OntologyRelation,
    PortfolioOntology,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.domain.ontology_schema import (
    default_tbox_metadata,
    normalize_tbox_metadata,
)
from digital_twin.domain.ontology_semantics import SEMANTIC_STORAGE_CONTRACT_VERSION
from digital_twin.infrastructure.graph_store_rulebox import (
    rulebox_graph_from_rules,
    rulebox_rules_to_payload,
)


def ontology_seed_graph(
    rules: Iterable[GraphInferenceRule] = None,
    language_registry: Dict[str, object] = None,
) -> PortfolioOntology:
    graph = rulebox_graph_from_rules(
        rules or default_graph_inference_rules(),
        language_registry=language_registry,
    )
    graph.portfolio_id = "ontology-seed"
    graph.worldview.update(
        {
            "model": "investment-ontology-seed",
            "description": "TBox schema and default RuleBox concepts persisted to TypeDB before runtime ABox projections arrive.",
            "skipNativeReasoning": True,
        }
    )
    return graph


def ontology_release_seed_artifact(
    rules: Iterable[GraphInferenceRule],
    language_registry: Dict[str, object] = None,
    tbox_metadata: Mapping[str, object] = None,
    release_bundle: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Freeze the executable static ontology needed to rebuild one release."""

    frozen_rules = list(rules or [])
    graph = ontology_seed_graph(frozen_rules, language_registry=language_registry)
    tbox = normalize_tbox_metadata(dict(tbox_metadata or default_tbox_metadata()))
    rules_payload = rulebox_rules_to_payload(frozen_rules)
    from digital_twin.domain.ontology_rulebox_governance import rulebox_rules_hash

    return {
        "version": "ontology-release-seed-artifact-v2",
        "semanticStorageContractVersion": SEMANTIC_STORAGE_CONTRACT_VERSION,
        "releaseBundle": dict(release_bundle or {}),
        "ruleboxFingerprint": rulebox_rules_hash(rules_payload),
        "tboxFingerprint": str(tbox.get("fingerprint") or ""),
        "tboxMetadata": tbox,
        "rules": rules_payload,
        "graph": {
            "portfolioId": graph.portfolio_id,
            "entities": [item.to_dict() for item in graph.entities],
            "relations": [item.to_dict() for item in graph.relations],
            "worldview": dict(graph.worldview or {}),
        },
    }


def ontology_seed_graph_from_artifact(
    artifact: Mapping[str, object]
) -> PortfolioOntology:
    """Rehydrate a frozen static graph without consulting the current catalog."""

    source = dict(artifact or {})
    graph_payload = dict(source.get("graph") or {})
    entities = []
    for value in list(graph_payload.get("entities") or []):
        item = dict(value or {})
        entity_id = str(item.get("id") or item.get("entity_id") or "").strip()
        if entity_id:
            entities.append(
                OntologyEntity(
                    entity_id,
                    str(item.get("label") or entity_id),
                    str(item.get("kind") or "concept"),
                    dict(item.get("properties") or {}),
                )
            )
    relations = []
    for value in list(graph_payload.get("relations") or []):
        item = dict(value or {})
        source_id = str(item.get("source") or "").strip()
        target_id = str(item.get("target") or "").strip()
        relation_type = str(item.get("type") or item.get("relation_type") or "").strip()
        if source_id and target_id and relation_type:
            relations.append(
                OntologyRelation(
                    source_id,
                    target_id,
                    relation_type,
                    weight=float(item.get("weight") or 1.0),
                    evidence_ids=list(
                        item.get("evidence_ids") or item.get("evidenceIds") or []
                    ),
                    properties=dict(item.get("properties") or {}),
                )
            )
    return PortfolioOntology(
        str(graph_payload.get("portfolioId") or "ontology-release-artifact"),
        entities=entities,
        relations=relations,
        worldview=dict(graph_payload.get("worldview") or {}),
    )


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
