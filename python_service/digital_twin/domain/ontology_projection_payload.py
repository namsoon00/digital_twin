"""Stable, narrow serialization for durable ontology projection work.

``PortfolioOntology.to_dict`` intentionally includes presentation payloads,
TBox catalogues, and AI packets.  Projection outbox messages must carry only
the already verified ABox slice required to update a shared world.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Dict, Mapping

from .ontology_contracts import (
    OntologyBelief,
    OntologyEntity,
    OntologyEvidence,
    OntologyOpinion,
    OntologyRelation,
    PortfolioOntology,
)


ONTOLOGY_PROJECTION_PAYLOAD_VERSION = "ontology-projection-payload-v1"


def _json_safe(value: object):
    """Normalise vendor values without allowing an arbitrary object in MySQL."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def serialize_portfolio_ontology(graph: PortfolioOntology) -> Dict[str, object]:
    return {
        "version": ONTOLOGY_PROJECTION_PAYLOAD_VERSION,
        "portfolioId": str(graph.portfolio_id or ""),
        "entities": [_json_safe(item.to_dict()) for item in graph.entities or []],
        "relations": [_json_safe(item.to_dict()) for item in graph.relations or []],
        "evidence": [_json_safe(item.to_dict()) for item in graph.evidence or []],
        "beliefs": [_json_safe(item.to_dict()) for item in graph.beliefs or []],
        "opinions": [_json_safe(item.to_dict()) for item in graph.opinions or []],
        "reasoningCards": _json_safe(list(graph.reasoning_cards or [])),
        "worldview": _json_safe(dict(graph.worldview or {})),
        "prompt": str(graph.prompt or ""),
    }


def deserialize_portfolio_ontology(payload: Mapping[str, object] = None) -> PortfolioOntology:
    values = dict(payload or {})
    entities = [
        OntologyEntity(
            str(item.get("id") or ""),
            str(item.get("label") or ""),
            str(item.get("kind") or ""),
            dict(item.get("properties") or {}),
        )
        for item in values.get("entities") or []
        if isinstance(item, Mapping) and str(item.get("id") or "")
    ]
    relations = [
        OntologyRelation(
            str(item.get("source") or ""),
            str(item.get("target") or ""),
            str(item.get("type") or item.get("relation_type") or ""),
            item.get("weight") or 1.0,
            list(item.get("evidence_ids") or item.get("evidenceIds") or []),
            dict(item.get("properties") or {}),
        )
        for item in values.get("relations") or []
        if isinstance(item, Mapping) and str(item.get("source") or "") and str(item.get("target") or "")
    ]
    evidence = [
        OntologyEvidence(
            str(item.get("id") or ""),
            str(item.get("subject") or ""),
            str(item.get("kind") or ""),
            str(item.get("source") or ""),
            str(item.get("summary") or ""),
            dict(item.get("value") or {}),
            str(item.get("evidence_role") or item.get("evidenceRole") or "context"),
            str(item.get("data_state") or item.get("dataState") or "partial"),
        )
        for item in values.get("evidence") or []
        if isinstance(item, Mapping) and str(item.get("id") or "") and str(item.get("subject") or "")
    ]
    beliefs = [
        OntologyBelief(
            str(item.get("id") or ""),
            str(item.get("subject") or ""),
            str(item.get("label") or ""),
            str(item.get("polarity") or "context"),
            str(item.get("evidence_role") or item.get("evidenceRole") or "context"),
            str(item.get("review_level") or item.get("reviewLevel") or "observe"),
            str(item.get("data_state") or item.get("dataState") or "partial"),
            list(item.get("evidence_ids") or item.get("evidenceIds") or []),
        )
        for item in values.get("beliefs") or []
        if isinstance(item, Mapping) and str(item.get("id") or "")
    ]
    opinions = [
        OntologyOpinion(
            str(item.get("symbol") or ""),
            str(item.get("action") or ""),
            str(item.get("tone") or ""),
            str(item.get("thesis") or ""),
            str(item.get("review_level") or item.get("reviewLevel") or "check"),
            str(item.get("data_state") or item.get("dataState") or "partial"),
            str(item.get("validation_state") or item.get("validationState") or "conditional"),
            list(item.get("supporting_beliefs") or item.get("supportingBeliefs") or []),
            list(item.get("contradictions") or []),
            list(item.get("dominant_risks") or item.get("dominantRisks") or []),
            list(item.get("opportunities") or []),
            dict(item.get("legacy_model") or item.get("legacyModel") or {}),
            list(item.get("evidence_ids") or item.get("evidenceIds") or []),
            list(item.get("relation_influences") or item.get("relationInfluences") or []),
        )
        for item in values.get("opinions") or []
        if isinstance(item, Mapping) and str(item.get("symbol") or "")
    ]
    return PortfolioOntology(
        str(values.get("portfolioId") or ""),
        entities=entities,
        relations=relations,
        evidence=evidence,
        beliefs=beliefs,
        opinions=opinions,
        reasoning_cards=deepcopy(list(values.get("reasoningCards") or [])),
        worldview=deepcopy(dict(values.get("worldview") or {})),
        prompt=str(values.get("prompt") or ""),
    )
