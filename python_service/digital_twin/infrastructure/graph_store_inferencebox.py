from typing import Dict, List

from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION
from .graph_store_payloads import list_of_strings, number_or_none


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
        "nativeTypeDbReasoningUsed": native_relation_count > 0,
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
        "sourceRuleId": str(row.get("sourceRuleId") or ""),
        "nativeRuleId": str(row.get("nativeRuleId") or ""),
        "semanticRuleId": str(row.get("semanticRuleId") or row.get("nativeRuleId") or ""),
        "reasoningLayer": str(row.get("reasoningLayer") or ""),
        "reasoningMode": str(row.get("reasoningMode") or ""),
        "materializationSource": str(row.get("materializationSource") or ""),
        "tboxClass": str(row.get("tboxClass") or ""),
        "polarity": str(row.get("polarity") or ""),
        "actionGroup": str(row.get("actionGroup") or ""),
        "actionLevel": str(row.get("actionLevel") or ""),
        "decisionStage": str(row.get("decisionStage") or ""),
        "stagePriority": number_or_none(row.get("stagePriority")),
        "targetRole": str(row.get("targetRole") or ""),
        "actionPolicy": str(row.get("actionPolicy") or ""),
        "allowedActions": list_of_strings(row.get("allowedActions")),
        "blockedActions": list_of_strings(row.get("blockedActions")),
        "confidence": number_or_none(row.get("confidence")),
        "sourceTraceId": str(row.get("sourceTraceId") or ""),
        "reasoningQuestion": str(row.get("reasoningQuestion") or ""),
        "shouldEscalate": bool(row.get("shouldEscalate")) if "shouldEscalate" in row else None,
        "hasConflict": bool(row.get("hasConflict")) if "hasConflict" in row else None,
        "conflictType": str(row.get("conflictType") or ""),
        "riskPressure": number_or_none(row.get("riskPressure")),
        "supportEvidence": number_or_none(row.get("supportEvidence")),
        "netRiskPressure": number_or_none(row.get("netRiskPressure")),
        "timelineBasis": str(row.get("timelineBasis") or ""),
        "currentStateKey": str(row.get("currentStateKey") or ""),
        "nativeTypeDbReasoned": bool(row.get("nativeTypeDbReasoned")),
        "typedbNativeRuleReasoned": bool(row.get("typedbNativeRuleReasoned")),
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
        "sourceRuleId": str(row.get("sourceRuleId") or ""),
        "nativeRuleId": str(row.get("nativeRuleId") or ""),
        "semanticRuleId": str(row.get("semanticRuleId") or row.get("nativeRuleId") or ""),
        "reasoningLayer": str(row.get("reasoningLayer") or ""),
        "reasoningMode": str(row.get("reasoningMode") or ""),
        "materializationSource": str(row.get("materializationSource") or ""),
        "polarity": str(row.get("polarity") or ""),
        "riskImpact": number_or_none(row.get("riskImpact")),
        "supportImpact": number_or_none(row.get("supportImpact")),
        "weight": number_or_none(row.get("weight")),
        "decisionStage": str(row.get("decisionStage") or ""),
        "stagePriority": number_or_none(row.get("stagePriority")),
        "targetRole": str(row.get("targetRole") or ""),
        "actionPolicy": str(row.get("actionPolicy") or ""),
        "allowedActions": list_of_strings(row.get("allowedActions")),
        "blockedActions": list_of_strings(row.get("blockedActions")),
        "label": str(row.get("aiInfluenceLabel") or ""),
        "aiInfluenceLabel": str(row.get("aiInfluenceLabel") or ""),
        "inferenceTraceId": str(row.get("inferenceTraceId") or ""),
        "nativeTypeDbReasoned": bool(row.get("nativeTypeDbReasoned")),
        "typedbNativeRuleReasoned": bool(row.get("typedbNativeRuleReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def inferencebox_trace_payload(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "symbol": str(row.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "sourceRuleId": str(row.get("sourceRuleId") or ""),
        "nativeRuleId": str(row.get("nativeRuleId") or ""),
        "semanticRuleId": str(row.get("semanticRuleId") or row.get("nativeRuleId") or ""),
        "reasoningLayer": str(row.get("reasoningLayer") or ""),
        "reasoningMode": str(row.get("reasoningMode") or ""),
        "materializationSource": str(row.get("materializationSource") or ""),
        "confidence": number_or_none(row.get("confidence")),
        "matchedConditionIds": list_of_strings(row.get("matchedConditionIds")),
        "nativeTypeDbReasoned": bool(row.get("nativeTypeDbReasoned")),
        "typedbNativeRuleReasoned": bool(row.get("typedbNativeRuleReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }
