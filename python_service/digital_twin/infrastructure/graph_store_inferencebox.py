import json
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
    properties = row_properties(row)
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "kind": str(row.get("kind") or ""),
        "symbol": str(row.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "sourceRuleId": str(row.get("sourceRuleId") or ""),
        "nativeRuleId": str(row.get("nativeRuleId") or ""),
        "semanticRuleId": str(row.get("semanticRuleId") or row.get("nativeRuleId") or ""),
        "hypothesisFamilyKey": str(row.get("hypothesisFamilyKey") or properties.get("hypothesisFamilyKey") or ""),
        "reasoningLayer": str(row.get("reasoningLayer") or ""),
        "reasoningMode": str(row.get("reasoningMode") or ""),
        "materializationSource": str(row.get("materializationSource") or ""),
        "tboxClass": str(row.get("tboxClass") or ""),
        "polarity": str(row.get("polarity") or ""),
        "actionGroup": str(row.get("actionGroup") or ""),
        "actionLevel": str(row.get("actionLevel") or ""),
        "decisionStage": str(row.get("decisionStage") or ""),
        "evidenceRole": str(properties.get("evidenceRole") or row.get("evidenceRole") or "context"),
        "reviewLevel": str(properties.get("reviewLevel") or row.get("reviewLevel") or "observe"),
        "reviewLevelLabel": str(properties.get("reviewLevelLabel") or row.get("reviewLevelLabel") or ""),
        "dataState": str(properties.get("dataState") or row.get("dataState") or "partial"),
        "dataStateLabel": str(properties.get("dataStateLabel") or row.get("dataStateLabel") or ""),
        "changeState": str(properties.get("changeState") or row.get("changeState") or "unchanged"),
        "validationState": str(properties.get("validationState") or row.get("validationState") or "conditional"),
        "targetRole": str(row.get("targetRole") or ""),
        "actionPolicy": str(row.get("actionPolicy") or ""),
        "allowedActions": list_of_strings(row.get("allowedActions")),
        "blockedActions": list_of_strings(row.get("blockedActions")),
        "sourceTraceId": str(row.get("sourceTraceId") or ""),
        "reasoningQuestion": str(row.get("reasoningQuestion") or ""),
        "shouldEscalate": bool(row.get("shouldEscalate")) if "shouldEscalate" in row else None,
        "hasConflict": bool(row.get("hasConflict")) if "hasConflict" in row else None,
        "conflictState": str(properties.get("conflictState") or row.get("conflictState") or "context-only"),
        "evidenceRoles": list_of_strings(properties.get("evidenceRoles") or row.get("evidenceRoles")),
        "timelineBasis": str(row.get("timelineBasis") or ""),
        "currentStateKey": str(row.get("currentStateKey") or ""),
        "nativeTypeDbReasoned": bool(row.get("nativeTypeDbReasoned")),
        "typedbNativeRuleReasoned": bool(row.get("typedbNativeRuleReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def inferencebox_relation_payload(row: Dict[str, object]) -> Dict[str, object]:
    properties = row_properties(row)
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
        "hypothesisFamilyKey": str(row.get("hypothesisFamilyKey") or properties.get("hypothesisFamilyKey") or ""),
        "reasoningLayer": str(row.get("reasoningLayer") or ""),
        "reasoningMode": str(row.get("reasoningMode") or ""),
        "materializationSource": str(row.get("materializationSource") or ""),
        "polarity": str(row.get("polarity") or ""),
        "evidenceRole": str(properties.get("evidenceRole") or row.get("evidenceRole") or "context"),
        "actionGroup": str(row.get("actionGroup") or ""),
        "actionLevel": str(row.get("actionLevel") or ""),
        "decisionStage": str(row.get("decisionStage") or ""),
        "reviewLevel": str(properties.get("reviewLevel") or row.get("reviewLevel") or "observe"),
        "dataState": str(properties.get("dataState") or row.get("dataState") or "partial"),
        "changeState": str(properties.get("changeState") or row.get("changeState") or "unchanged"),
        "conflictState": str(properties.get("conflictState") or row.get("conflictState") or "context-only"),
        "validationState": str(properties.get("validationState") or row.get("validationState") or "conditional"),
        "targetRole": str(row.get("targetRole") or ""),
        "actionPolicy": str(row.get("actionPolicy") or ""),
        "allowedActions": list_of_strings(row.get("allowedActions")),
        "blockedActions": list_of_strings(row.get("blockedActions")),
        "label": str(row.get("aiInfluenceLabel") or ""),
        "aiInfluenceLabel": str(row.get("aiInfluenceLabel") or ""),
        "inferenceTraceId": str(row.get("inferenceTraceId") or ""),
        "freshnessStatus": str(properties.get("freshnessStatus") or row.get("freshnessStatus") or ""),
        "freshnessGateReason": str(properties.get("freshnessGateReason") or row.get("freshnessGateReason") or ""),
        "evidenceUsableForJudgement": bool(properties.get("evidenceUsableForJudgement")) if "evidenceUsableForJudgement" in properties else (
            bool(row.get("evidenceUsableForJudgement")) if "evidenceUsableForJudgement" in row else None
        ),
        "nativeTypeDbReasoned": bool(row.get("nativeTypeDbReasoned")),
        "typedbNativeRuleReasoned": bool(row.get("typedbNativeRuleReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def inferencebox_trace_payload(row: Dict[str, object]) -> Dict[str, object]:
    properties = row_properties(row)
    matched_conditions = [
        dict(item)
        for item in properties.get("matchedConditions") or row.get("matchedConditions") or []
        if isinstance(item, dict)
    ]
    matched_condition_ids = list_of_strings(row.get("matchedConditionIds")) or [
        str(item.get("conditionId") or "")
        for item in matched_conditions
        if str(item.get("conditionId") or "")
    ]
    evidence_relation_ids = list_of_strings(properties.get("evidenceRelationIds") or row.get("evidenceRelationIds"))
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "symbol": str(row.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "sourceRuleId": str(row.get("sourceRuleId") or ""),
        "nativeRuleId": str(row.get("nativeRuleId") or ""),
        "semanticRuleId": str(row.get("semanticRuleId") or row.get("nativeRuleId") or ""),
        "hypothesisFamilyKey": str(row.get("hypothesisFamilyKey") or properties.get("hypothesisFamilyKey") or ""),
        "reasoningLayer": str(row.get("reasoningLayer") or ""),
        "reasoningMode": str(row.get("reasoningMode") or ""),
        "materializationSource": str(row.get("materializationSource") or ""),
        "matchedConditionIds": matched_condition_ids,
        "matchedConditions": matched_conditions,
        "ruleConditionShapes": [
            dict(item)
            for item in properties.get("ruleConditionShapes") or row.get("ruleConditionShapes") or []
            if isinstance(item, dict)
        ],
        "anyConditionMinCount": int(number_or_none(
            properties.get("anyConditionMinCount")
            if "anyConditionMinCount" in properties
            else row.get("anyConditionMinCount")
        ) or 1),
        "evidenceRelationIds": evidence_relation_ids,
        "promptHint": str(properties.get("promptHint") or row.get("promptHint") or ""),
        "conditionDetailSource": str(properties.get("conditionDetailSource") or row.get("conditionDetailSource") or ""),
        "requiredConditionCount": int(number_or_none(properties.get("requiredConditionCount") if "requiredConditionCount" in properties else row.get("requiredConditionCount")) or 0),
        "groundedConditionCount": int(number_or_none(properties.get("groundedConditionCount") if "groundedConditionCount" in properties else row.get("groundedConditionCount")) or 0),
        "reviewLevel": str(properties.get("reviewLevel") or row.get("reviewLevel") or "observe"),
        "reviewLevelLabel": str(properties.get("reviewLevelLabel") or row.get("reviewLevelLabel") or ""),
        "dataState": str(properties.get("dataState") or row.get("dataState") or "partial"),
        "dataStateLabel": str(properties.get("dataStateLabel") or row.get("dataStateLabel") or ""),
        "changeState": str(properties.get("changeState") or row.get("changeState") or "unchanged"),
        "conflictState": str(properties.get("conflictState") or row.get("conflictState") or "context-only"),
        "validationState": str(properties.get("validationState") or row.get("validationState") or "conditional"),
        "freshnessStatus": str(properties.get("freshnessStatus") or row.get("freshnessStatus") or "unknown"),
        "freshnessGateReason": str(properties.get("freshnessGateReason") or row.get("freshnessGateReason") or ""),
        "temporalEvidenceCount": int(number_or_none(properties.get("temporalEvidenceCount") if "temporalEvidenceCount" in properties else row.get("temporalEvidenceCount")) or 0),
        "evidenceUsableForJudgement": bool(properties.get("evidenceUsableForJudgement")) if "evidenceUsableForJudgement" in properties else (
            bool(row.get("evidenceUsableForJudgement")) if "evidenceUsableForJudgement" in row else None
        ),
        "nativeTypeDbReasoned": bool(row.get("nativeTypeDbReasoned")),
        "typedbNativeRuleReasoned": bool(row.get("typedbNativeRuleReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def row_properties(row: Dict[str, object]) -> Dict[str, object]:
    if isinstance(row.get("properties"), dict):
        return dict(row.get("properties") or {})
    try:
        parsed = json.loads(str(row.get("propertiesJson") or "{}"))
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}
