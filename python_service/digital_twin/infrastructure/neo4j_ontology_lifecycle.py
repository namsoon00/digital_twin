import json
import re
from typing import Dict, Iterable, List

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
    rulebox_version_payload,
)
from ..domain.ontology_rulebox_projection import add_rulebox_concepts
from ..domain.ontology_schema import default_tbox_metadata, normalize_tbox_metadata, tbox_entities, tbox_relations
from .settings import utc_now
from .neo4j_ontology_payloads import (
    bool_or_none,
    condition_relation_filter_bool,
    condition_relation_filter_number,
    condition_relation_filter_values,
    condition_target_filter_bool,
    condition_target_filter_number,
    condition_target_filter_values,
    condition_target_level_types,
    derivation_decision_stage,
    derivation_stage_priority,
    group_relation_rows,
    list_of_strings,
    number_or_none,
    safe_relation_type,
)
from .neo4j_ontology_rulebox import rulebox_graph_from_rules


def ontology_seed_graph(rules: Iterable[GraphInferenceRule] = None) -> PortfolioOntology:
    graph = rulebox_graph_from_rules(rules or default_graph_inference_rules())
    graph.portfolio_id = "ontology-seed"
    graph.worldview.update({
        "model": "investment-ontology-seed",
        "description": "TBox schema and default RuleBox concepts persisted to Neo4j before runtime ABox projections arrive.",
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

def active_tbox_metadata_statements() -> List[Dict[str, object]]:
    return [
        {
            "statement": (
                "MATCH (n:OntologyEntity) "
                "WHERE n.ontologyBox = 'TBox' "
                "RETURN count(n) AS entityCount, "
                "max(coalesce(n.version, '')) AS version, "
                "max(coalesce(n.tboxFingerprint, '')) AS fingerprint, "
                "max(coalesce(n.updatedAt, '')) AS updatedAt"
            ),
            "parameters": {},
        },
        {
            "statement": (
                "MATCH ()-[r]->() "
                "WHERE r.ontologyBox = 'TBox' "
                "RETURN count(r) AS relationCount"
            ),
            "parameters": {},
        },
    ]

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

def active_tbox_metadata_from_rows(rowsets: Dict[str, List[Dict[str, object]]], source: str) -> Dict[str, object]:
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
        "source": "neo4j",
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

def clear_rulebox_statements(clear_inference: bool = True) -> List[Dict[str, object]]:
    statements = []
    if clear_inference:
        statements.extend(clear_inferencebox_statements())
    statements.append({
        "statement": "MATCH (n:OntologyEntity) WHERE n.ontologyBox = 'RuleBox' DETACH DELETE n",
        "parameters": {},
    })
    return statements

def graph_abox_lifecycle(graph: PortfolioOntology) -> Dict[str, object]:
    lifecycle = (graph.worldview or {}).get("aboxLifecycle") if isinstance(graph.worldview, dict) else {}
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    account_id = str(lifecycle.get("accountId") or "").strip()
    snapshot_id = str(lifecycle.get("aboxSnapshotId") or lifecycle.get("snapshotId") or "").strip()
    if not account_id or not snapshot_id:
        return {}
    return {
        "accountId": account_id,
        "aboxSnapshotId": snapshot_id,
        "updatedAt": utc_now(),
    }

def deactivate_current_abox_statements(graph: PortfolioOntology) -> List[Dict[str, object]]:
    lifecycle = graph_abox_lifecycle(graph)
    if not lifecycle:
        return []
    return [
        {
            "statement": (
                "MATCH (n:OntologyEntity) "
                "WHERE n.ontologyBox = 'ABox' "
                "AND n.accountId = $accountId "
                "AND coalesce(n.isCurrent, false) = true "
                "AND coalesce(n.aboxSnapshotId, '') <> $aboxSnapshotId "
                "SET n.isCurrent = false, n.supersededAt = $updatedAt"
            ),
            "parameters": lifecycle,
        },
        {
            "statement": (
                "MATCH ()-[r]->() "
                "WHERE r.ontologyBox = 'ABox' "
                "AND r.accountId = $accountId "
                "AND coalesce(r.isCurrent, false) = true "
                "AND coalesce(r.aboxSnapshotId, '') <> $aboxSnapshotId "
                "SET r.isCurrent = false, r.supersededAt = $updatedAt"
            ),
            "parameters": lifecycle,
        },
        {
            "statement": (
                "MATCH (n) "
                "WHERE (n:OntologyEvidence OR n:OntologyBelief OR n:OntologyOpinion OR n:OntologyReasoningCard) "
                "AND n.ontologyBox = 'ABox' "
                "AND n.accountId = $accountId "
                "AND coalesce(n.isCurrent, false) = true "
                "AND coalesce(n.aboxSnapshotId, '') <> $aboxSnapshotId "
                "SET n.isCurrent = false, n.supersededAt = $updatedAt"
            ),
            "parameters": lifecycle,
        },
    ]

def clear_inferencebox_statements() -> List[Dict[str, object]]:
    return [
        {
            "statement": "MATCH (n:OntologyEntity) WHERE n.ontologyBox = 'InferenceBox' DETACH DELETE n",
            "parameters": {},
        },
        {
            "statement": "MATCH (n:OntologyEvidence) WHERE n.ontologyBox = 'InferenceBox' DETACH DELETE n",
            "parameters": {},
        },
        {
            "statement": "MATCH (n:OntologyBelief) WHERE n.ontologyBox = 'InferenceBox' DETACH DELETE n",
            "parameters": {},
        },
    ]

def http_result_rowsets(payload: Dict[str, object], keys: List[str]) -> Dict[str, List[Dict[str, object]]]:
    rowsets: Dict[str, List[Dict[str, object]]] = {}
    for key, result in zip(keys, payload.get("results") or []):
        columns = result.get("columns") or []
        rows = []
        for item in result.get("data") or []:
            values = item.get("row") if isinstance(item, dict) else []
            rows.append(dict(zip(columns, values or [])))
        rowsets[key] = rows
    for key in keys:
        rowsets.setdefault(key, [])
    return rowsets

def neo4j_record_to_dict(record) -> Dict[str, object]:
    if hasattr(record, "data"):
        return record.data()
    return dict(record)
