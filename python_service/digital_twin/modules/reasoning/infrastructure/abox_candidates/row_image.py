"""Copy source graphs and resolve candidate rows without mutating source facts."""

from typing import Dict, Iterable, List, Tuple

import copy
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from .identity import ontology_storage_id
from .ports import CandidateRowMapper


def current_state_physical_graph(graph: PortfolioOntology, physical_scope_plan: Iterable[Dict[str, object]]) -> PortfolioOntology:
    """Return a persistence copy whose ABox rows use physical slot ids."""

    clone = copy.deepcopy(graph)
    physical_plan = [dict(item or {}) for item in physical_scope_plan or []]
    physical_state_mode = str(
        (physical_plan[0] if physical_plan else {}).get("physicalStateMode")
        or CURRENT_STATE_ABOX_PERSISTENCE_MODE
    )
    candidate_manifest_id = str(
        (clone.worldview or {}).get("aboxSnapshotId")
        or (clone.worldview or {}).get("snapshotId")
        or ""
    ).strip()
    by_scope = {
        str(item.get("scopeId") or "").strip(): item
        for item in physical_plan
        if str(item.get("scopeId") or "").strip()
    }

    def bind(properties: Dict[str, object]) -> Dict[str, object]:
        values = dict(properties or {})
        if str(values.get("ontologyBox") or "ABox") != "ABox":
            return values
        scope_id = str(values.get("aboxScopeId") or values.get("scopeId") or "").strip()
        physical = by_scope.get(scope_id) or {}
        generation_id = str(physical.get("generationId") or "").strip()
        if not generation_id:
            return values
        logical_generation_id = str(
            physical.get("logicalGenerationId")
            or values.get("scopeGenerationId")
            or values.get("snapshotId")
            or values.get("aboxSnapshotId")
            or ""
        ).strip()
        values.update({
            "logicalScopeGenerationId": logical_generation_id,
            "physicalGenerationId": generation_id,
            "scopeGenerationId": generation_id,
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            "physicalStateMode": physical_state_mode,
        })
        if physical.get("physicalGenerationChanged") and candidate_manifest_id:
            values["manifestId"] = candidate_manifest_id
        return values

    for item in clone.entities:
        item.properties = bind(item.properties)
    for item in clone.relations:
        item.properties = bind(item.properties)
    for item in clone.evidence:
        item.value = bind(item.value)
    for item in clone.opinions:
        item.legacy_model = bind(item.legacy_model)
    clone.reasoning_cards = [
        bind(item) if isinstance(item, dict) else item
        for item in clone.reasoning_cards or []
    ]
    support_scopes = {}
    for key, raw in dict((clone.worldview or {}).get("supportRelationScopes") or {}).items():
        metadata = dict(raw or {})
        scope_id = str(metadata.get("scopeId") or "").strip()
        physical = by_scope.get(scope_id) or {}
        generation_id = str(physical.get("generationId") or "").strip()
        if generation_id:
            metadata.update({
                "logicalScopeGenerationId": str(
                    physical.get("logicalGenerationId")
                    or metadata.get("scopeGenerationId")
                    or ""
                ),
                "physicalGenerationId": generation_id,
                "scopeGenerationId": generation_id,
                "snapshotId": generation_id,
                "aboxSnapshotId": generation_id,
            })
            if physical.get("physicalGenerationChanged") and candidate_manifest_id:
                metadata["manifestId"] = candidate_manifest_id
        support_scopes[str(key)] = metadata
    logical_generations = {
        str(item.get("scopeId") or ""): str(item.get("logicalGenerationId") or "")
        for item in physical_plan
        if str(item.get("scopeId") or "")
    }
    physical_generations = {
        str(item.get("scopeId") or ""): str(item.get("generationId") or "")
        for item in physical_plan
        if str(item.get("scopeId") or "")
    }
    clone.worldview.update({
        "persistenceMode": physical_state_mode,
        "physicalStateMode": physical_state_mode,
        "scopePlan": physical_plan,
        "logicalScopeGenerationIds": logical_generations,
        "scopeGenerationIds": physical_generations,
        "supportRelationScopes": support_scopes,
    })
    return clone


def scoped_abox_persistence_rows(store: CandidateRowMapper, graph: PortfolioOntology, scope_ids: Iterable[str]) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Build changed scope rows while resolving endpoints from full context."""
    selected = {str(item or "").strip() for item in scope_ids or [] if str(item or "").strip()}
    all_node_rows = [
        row for row in store.node_rows(graph)
        if str(row.get("ontologyBox") or "ABox") == "ABox"
    ]
    nodes_by_id = {
        str(row.get("id") or ""): row
        for row in all_node_rows
        if str(row.get("id") or "")
    }
    changed_nodes = [
        row for row in all_node_rows
        if str(row.get("scopeId") or "") in selected
    ]
    relation_rows: List[Dict[str, object]] = []
    for raw in store.rows_for_relations(graph) + store.support_relation_rows(graph):
        if str(raw.get("ontologyBox") or "ABox") != "ABox":
            continue
        source_id = str(raw.get("source") or "")
        target_id = str(raw.get("target") or "")
        if not str(raw.get("scopeId") or ""):
            source_row = nodes_by_id.get(source_id) or {}
            raw = {
                **raw,
                "scopeId": str(source_row.get("scopeId") or ""),
                "scopeType": str(source_row.get("scopeType") or ""),
                "manifestId": str(source_row.get("manifestId") or ""),
                "scopeGenerationId": str(source_row.get("scopeGenerationId") or source_row.get("snapshotId") or ""),
                "snapshotId": str(raw.get("snapshotId") or source_row.get("snapshotId") or ""),
                "aboxSnapshotId": str(raw.get("aboxSnapshotId") or source_row.get("aboxSnapshotId") or ""),
            }
        if str(raw.get("scopeId") or "") not in selected:
            continue
        source_row = nodes_by_id.get(source_id)
        target_row = nodes_by_id.get(target_id)
        if not source_row or not target_row:
            continue
        relation_rows.append({
            **raw,
            "sourceStorageId": ontology_storage_id(source_row, source_id, "node"),
            "targetStorageId": ontology_storage_id(target_row, target_id, "node"),
        })
    return changed_nodes, relation_rows
