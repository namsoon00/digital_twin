"""Node rows implementation; facade-independent dependencies."""

from __future__ import annotations
from .node_rows_ports import (
    NodeRowsPort,
    BeliefNodeRowsBindings,
    SupportRelationRowsBindings,
)
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_scopes import support_relation_key
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    symbol_from_subject,
)
from typing import Dict, List
import json


def node_rows(
    _store: NodeRowsPort,
    graph: PortfolioOntology,
    include_external_relation_endpoints: bool = False,
) -> List[Dict[str, object]]:
    rows = []
    rows.extend(
        {**row, "nodeType": "ontology-entity"}
        for row in _store.rows_for_entities(graph)
    )
    rows.extend(_store.evidence_node_rows(graph))
    rows.extend(_store.belief_node_rows(graph))
    rows.extend(_store.opinion_node_rows(graph))
    rows.extend(_store.reasoning_card_node_rows(graph))
    external_ids = _store.external_relation_endpoint_ids(graph)
    return [
        row
        for row in rows
        if str(row.get("id") or "")
        and (
            include_external_relation_endpoints
            or str(row.get("id") or "") not in external_ids
        )
    ]


def evidence_node_rows(
    _store: NodeRowsPort, graph: PortfolioOntology
) -> List[Dict[str, object]]:
    return [
        {
            **row,
            "nodeType": "ontology-evidence",
            "label": row.get("summary") or row.get("id"),
            "kind": "evidence:" + str(row.get("kind") or "evidence"),
            "symbol": "",
            "ruleId": "",
            "tboxClass": "Evidence",
            "propertiesJson": row.get("valueJson") or "{}",
        }
        for row in _store.rows_for_evidence(graph)
    ]


def belief_node_rows(
    _store: NodeRowsPort, graph: PortfolioOntology, *, _bindings: BeliefNodeRowsBindings
) -> List[Dict[str, object]]:
    return [
        {
            **row,
            "nodeType": "ontology-belief",
            "label": row.get("label") or row.get("id"),
            "kind": "belief",
            "symbol": symbol_from_subject(row.get("subject")),
            "ruleId": _bindings.rule_id_from_value(row.get("id")),
            "tboxClass": "Belief",
            "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
        }
        for row in _store.rows_for_beliefs(graph)
    ]


def opinion_node_rows(
    _store: NodeRowsPort, graph: PortfolioOntology
) -> List[Dict[str, object]]:
    return [
        {
            **row,
            "nodeType": "ontology-opinion",
            "label": str(row.get("symbol") or row.get("id")),
            "kind": "opinion",
            "ruleId": "",
            "tboxClass": "InvestmentOpinion",
            "propertiesJson": row.get("payloadJson") or "{}",
        }
        for row in _store.rows_for_opinions(graph)
    ]


def reasoning_card_node_rows(
    _store: NodeRowsPort, graph: PortfolioOntology
) -> List[Dict[str, object]]:
    return [
        {
            **row,
            "nodeType": "ontology-reasoning-card",
            "label": row.get("companyName") or row.get("symbol") or row.get("id"),
            "kind": "reasoning-card",
            "ruleId": "",
            "tboxClass": "ReasoningCard",
            "propertiesJson": row.get("payloadJson") or "{}",
        }
        for row in _store.rows_for_reasoning_cards(graph)
    ]


def support_relation_rows(
    _store: NodeRowsPort,
    graph: PortfolioOntology,
    *,
    _bindings: SupportRelationRowsBindings,
) -> List[Dict[str, object]]:
    support_scope_plan = dict(
        (getattr(graph, "worldview", {}) or {}).get("supportRelationScopes") or {}
    )

    def scoped_owner(
        relation_type: str, source: object, target: object
    ) -> Dict[str, object]:
        metadata = support_scope_plan.get(
            support_relation_key(relation_type, source, target)
        )
        if not isinstance(metadata, dict):
            return {}
        scope_id = str(metadata.get("scopeId") or "").strip()
        generation_id = str(
            metadata.get("scopeGenerationId")
            or metadata.get("snapshotId")
            or metadata.get("aboxSnapshotId")
            or ""
        ).strip()
        if not scope_id or not generation_id:
            return {}
        return {
            "scopeId": scope_id,
            "scopeType": str(
                metadata.get("scopeType") or scope_id.split(":", 1)[0] or "link"
            ),
            "manifestId": str(metadata.get("manifestId") or ""),
            "scopeGenerationId": generation_id,
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
        }

    rows: List[Dict[str, object]] = []
    for row in _store.rows_for_evidence(graph):
        original_source = row.get("subject")
        original_target = row.get("id")
        owner = scoped_owner("HAS_EVIDENCE", original_source, original_target)
        metadata = support_scope_plan.get(
            support_relation_key("HAS_EVIDENCE", original_source, original_target)
        )
        metadata = dict(metadata or {}) if isinstance(metadata, dict) else {}
        source = metadata.get("source") or original_source
        target = metadata.get("target") or original_target
        rows.append(
            {
                "source": source,
                "target": target,
                "type": "HAS_EVIDENCE",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "scopeId": row.get("scopeId") or "",
                "scopeType": row.get("scopeType") or "",
                "manifestId": row.get("manifestId") or "",
                "scopeGenerationId": row.get("scopeGenerationId")
                or row.get("snapshotId")
                or row.get("aboxSnapshotId")
                or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
                **owner,
            }
        )
    for row in _store.rows_for_beliefs(graph):
        rows.append(
            {
                "source": row.get("subject"),
                "target": row.get("id"),
                "type": "HAS_BELIEF",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "scopeId": row.get("scopeId") or "",
                "scopeType": row.get("scopeType") or "",
                "manifestId": row.get("manifestId") or "",
                "scopeGenerationId": row.get("scopeGenerationId")
                or row.get("snapshotId")
                or row.get("aboxSnapshotId")
                or "",
                "ruleId": row.get("ruleId")
                or _bindings.rule_id_from_value(row.get("id")),
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    for row in _store.rows_for_opinions(graph):
        rows.append(
            {
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_OPINION",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    for row in _store.rows_for_reasoning_cards(graph):
        rows.append(
            {
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_REASONING_CARD",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    return [row for row in rows if row.get("source") and row.get("target")]
