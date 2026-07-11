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


def inferencebox_snapshot_statements(symbols: List[str] = None, limit: int = 80) -> List[Dict[str, object]]:
    clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
    safe_limit = max(1, min(500, int(limit or 80)))
    entity_scope = "n.ontologyBox = 'InferenceBox' AND (size($symbols) = 0 OR n.symbol IN $symbols)"
    relation_scope = "r.ontologyBox = 'InferenceBox' AND (size($symbols) = 0 OR a.symbol IN $symbols OR b.symbol IN $symbols OR r.symbol IN $symbols)"
    trace_scope = "trace.kind = 'inference-trace' AND trace.ontologyBox = 'InferenceBox' AND (size($symbols) = 0 OR trace.symbol IN $symbols)"
    return [
        {
            "statement": (
                "MATCH (n:OntologyEntity) WHERE " + entity_scope + " "
                "RETURN count(n) AS entityCount, "
                "sum(CASE WHEN coalesce(n.nativeNeo4jReasoned, false) THEN 1 ELSE 0 END) AS nativeEntityCount"
            ),
            "parameters": {"symbols": clean_symbols},
        },
        {
            "statement": (
                "MATCH (a)-[r]->(b) WHERE " + relation_scope + " "
                "RETURN count(r) AS relationCount, "
                "sum(CASE WHEN coalesce(r.nativeNeo4jReasoned, false) THEN 1 ELSE 0 END) AS nativeRelationCount"
            ),
            "parameters": {"symbols": clean_symbols},
        },
        {
            "statement": (
                "MATCH (trace:OntologyEntity) WHERE " + trace_scope + " "
                "RETURN count(trace) AS traceCount, "
                "sum(CASE WHEN coalesce(trace.nativeNeo4jReasoned, false) THEN 1 ELSE 0 END) AS nativeTraceCount"
            ),
            "parameters": {"symbols": clean_symbols},
        },
        {
            "statement": (
                "MATCH (n:OntologyEntity) WHERE " + entity_scope + " "
                "RETURN n.id AS id, n.label AS label, n.kind AS kind, n.symbol AS symbol, "
                "n.ruleId AS ruleId, n.tboxClass AS tboxClass, n.polarity AS polarity, "
                "n.actionGroup AS actionGroup, n.actionLevel AS actionLevel, n.confidence AS confidence, "
                "n.decisionStage AS decisionStage, n.stagePriority AS stagePriority, "
                "n.nativeNeo4jReasoned AS nativeNeo4jReasoned, n.updatedAt AS updatedAt "
                "ORDER BY coalesce(n.updatedAt, '') DESC, n.id LIMIT $limit"
            ),
            "parameters": {"symbols": clean_symbols, "limit": safe_limit},
        },
        {
            "statement": (
                "MATCH (a)-[r]->(b) WHERE " + relation_scope + " "
                "RETURN type(r) AS type, a.id AS source, a.label AS sourceLabel, b.id AS target, b.label AS targetLabel, "
                "r.ruleId AS ruleId, r.polarity AS polarity, r.riskImpact AS riskImpact, r.supportImpact AS supportImpact, "
                "r.weight AS weight, r.decisionStage AS decisionStage, r.stagePriority AS stagePriority, "
                "r.aiInfluenceLabel AS aiInfluenceLabel, r.inferenceTraceId AS inferenceTraceId, "
                "r.nativeNeo4jReasoned AS nativeNeo4jReasoned, r.updatedAt AS updatedAt "
                "ORDER BY coalesce(r.updatedAt, '') DESC, type(r), a.id, b.id LIMIT $limit"
            ),
            "parameters": {"symbols": clean_symbols, "limit": safe_limit},
        },
        {
            "statement": (
                "MATCH (trace:OntologyEntity) WHERE " + trace_scope + " "
                "RETURN trace.id AS id, trace.label AS label, trace.symbol AS symbol, trace.ruleId AS ruleId, "
                "trace.confidence AS confidence, trace.matchedConditionIds AS matchedConditionIds, "
                "trace.nativeNeo4jReasoned AS nativeNeo4jReasoned, trace.updatedAt AS updatedAt "
                "ORDER BY coalesce(trace.updatedAt, '') DESC, trace.id LIMIT $limit"
            ),
            "parameters": {"symbols": clean_symbols, "limit": safe_limit},
        },
    ]

def inferencebox_snapshot_default(status: str, reason: str, configured: bool, symbols: List[str] = None) -> Dict[str, object]:
    return {
        "configured": bool(configured),
        "saved": False,
        "status": status,
        "source": "neo4j",
        "reason": reason,
        "engineVersion": GRAPH_REASONER_VERSION,
        "symbols": list(symbols or []),
        "entities": [],
        "relations": [],
        "traces": [],
        "entityCount": 0,
        "relationCount": 0,
        "traceCount": 0,
        "nativeEntityCount": 0,
        "nativeRelationCount": 0,
        "nativeTraceCount": 0,
    }

def inferencebox_snapshot_from_rows(rowsets: Dict[str, List[Dict[str, object]]], source: str, symbols: List[str] = None) -> Dict[str, object]:
    entity_count_row = first_row(rowsets.get("entityCounts"))
    relation_count_row = first_row(rowsets.get("relationCounts"))
    trace_count_row = first_row(rowsets.get("traceCounts"))
    entities = [inferencebox_entity_payload(row) for row in rowsets.get("entities") or []]
    relations = [inferencebox_relation_payload(row) for row in rowsets.get("relations") or []]
    traces = [inferencebox_trace_payload(row) for row in rowsets.get("traces") or []]
    native_relation_count = int(number_or_none(relation_count_row.get("nativeRelationCount")) or 0)
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "source": source,
        "engineVersion": GRAPH_REASONER_VERSION,
        "symbols": list(symbols or []),
        "entityCount": int(number_or_none(entity_count_row.get("entityCount")) or len(entities)),
        "relationCount": int(number_or_none(relation_count_row.get("relationCount")) or len(relations)),
        "traceCount": int(number_or_none(trace_count_row.get("traceCount")) or len(traces)),
        "nativeEntityCount": int(number_or_none(entity_count_row.get("nativeEntityCount")) or 0),
        "nativeRelationCount": native_relation_count,
        "nativeTraceCount": int(number_or_none(trace_count_row.get("nativeTraceCount")) or 0),
        "neo4jNativeReasoningUsed": native_relation_count > 0,
        "entities": entities,
        "relations": relations,
        "traces": traces,
    }

def first_row(rows: List[Dict[str, object]]) -> Dict[str, object]:
    return rows[0] if rows else {}

def inferencebox_entity_payload(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "kind": str(row.get("kind") or ""),
        "symbol": str(row.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "tboxClass": str(row.get("tboxClass") or ""),
        "polarity": str(row.get("polarity") or ""),
        "actionGroup": str(row.get("actionGroup") or ""),
        "actionLevel": str(row.get("actionLevel") or ""),
        "decisionStage": str(row.get("decisionStage") or ""),
        "stagePriority": number_or_none(row.get("stagePriority")),
        "confidence": number_or_none(row.get("confidence")),
        "nativeNeo4jReasoned": bool(row.get("nativeNeo4jReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }

def inferencebox_relation_payload(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "type": str(row.get("type") or ""),
        "source": str(row.get("source") or ""),
        "sourceLabel": str(row.get("sourceLabel") or ""),
        "target": str(row.get("target") or ""),
        "targetLabel": str(row.get("targetLabel") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "polarity": str(row.get("polarity") or ""),
        "riskImpact": number_or_none(row.get("riskImpact")),
        "supportImpact": number_or_none(row.get("supportImpact")),
        "weight": number_or_none(row.get("weight")),
        "decisionStage": str(row.get("decisionStage") or ""),
        "stagePriority": number_or_none(row.get("stagePriority")),
        "label": str(row.get("aiInfluenceLabel") or ""),
        "aiInfluenceLabel": str(row.get("aiInfluenceLabel") or ""),
        "inferenceTraceId": str(row.get("inferenceTraceId") or ""),
        "nativeNeo4jReasoned": bool(row.get("nativeNeo4jReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }

def inferencebox_trace_payload(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "symbol": str(row.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "confidence": number_or_none(row.get("confidence")),
        "matchedConditionIds": list_of_strings(row.get("matchedConditionIds")),
        "nativeNeo4jReasoned": bool(row.get("nativeNeo4jReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }
