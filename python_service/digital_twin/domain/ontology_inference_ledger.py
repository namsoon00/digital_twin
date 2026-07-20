from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from .ontology_decision_state import (
    conflict_state_from_roles,
    evidence_role_from_relation,
    semantic_relation_sort_key,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: object) -> str:
    return str(value or "").strip()


def clean_symbol(value: object) -> str:
    return clean_text(value).upper()


def list_of_strings(value: object) -> List[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, tuple):
        return [clean_text(item) for item in value if clean_text(item)]
    if value in (None, ""):
        return []
    return [clean_text(value)]


def rule_id_of(rule: Dict[str, object]) -> str:
    return clean_text(rule.get("rule_id") or rule.get("ruleId") or rule.get("id"))


def condition_id_of(condition: Dict[str, object], index: int = 0) -> str:
    return clean_text(condition.get("condition_id") or condition.get("conditionId") or ("condition-" + str(index + 1)))


def relation_type_of(row: Dict[str, object]) -> str:
    return clean_text(row.get("type") or row.get("relationType")).upper()


def rulebox_rules(rulebox: Dict[str, object]) -> List[Dict[str, object]]:
    return [dict(item) for item in (rulebox or {}).get("rules") or [] if isinstance(item, dict)]


def relation_mentions_trace_or_rule(relation: Dict[str, object], trace: Dict[str, object]) -> bool:
    trace_id = clean_text(trace.get("id"))
    rule_id = clean_text(trace.get("ruleId") or trace.get("sourceRuleId"))
    symbol = clean_symbol(trace.get("symbol"))
    if trace_id and trace_id in {
        clean_text(relation.get("source")),
        clean_text(relation.get("target")),
        clean_text(relation.get("inferenceTraceId")),
    }:
        return True
    relation_rule_id = clean_text(relation.get("ruleId") or relation.get("sourceRuleId"))
    if rule_id and relation_rule_id and rule_id == relation_rule_id:
        relation_symbol = clean_symbol(relation.get("symbol"))
        if not symbol or not relation_symbol or relation_symbol == symbol:
            return True
        haystack = " ".join(clean_symbol(relation.get(key)) for key in ["source", "target", "sourceLabel", "targetLabel"])
        return symbol in haystack
    return False


def entity_mentions_trace_or_rule(entity: Dict[str, object], trace: Dict[str, object]) -> bool:
    trace_id = clean_text(trace.get("id"))
    rule_id = clean_text(trace.get("ruleId") or trace.get("sourceRuleId"))
    symbol = clean_symbol(trace.get("symbol"))
    if trace_id and trace_id in {
        clean_text(entity.get("sourceTraceId")),
        clean_text(entity.get("inferenceTraceId")),
        clean_text(entity.get("id")),
    }:
        return True
    entity_rule_id = clean_text(entity.get("ruleId") or entity.get("sourceRuleId"))
    if rule_id and entity_rule_id and rule_id == entity_rule_id:
        entity_symbol = clean_symbol(entity.get("symbol"))
        return not symbol or not entity_symbol or entity_symbol == symbol
    return False


def rule_match_keys(rule: Dict[str, object]) -> List[str]:
    rule_id = rule_id_of(rule)
    keys = [rule_id]
    native_rule_id = clean_text(rule.get("nativeRuleId"))
    semantic_rule_id = clean_text(rule.get("semanticRuleId"))
    for key in [native_rule_id, semantic_rule_id]:
        if key and key not in keys:
            keys.append(key)
    return [key for key in keys if key]


def rule_by_id(rulebox: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    lookup: Dict[str, Dict[str, object]] = {}
    for rule in rulebox_rules(rulebox):
        for key in rule_match_keys(rule):
            lookup[key] = rule
    return lookup


def matched_condition_details(trace: Dict[str, object]) -> Tuple[set, Dict[str, Dict[str, object]]]:
    details = {}
    for item in trace.get("matchedConditions") or []:
        if not isinstance(item, dict):
            continue
        condition_id = clean_text(item.get("conditionId") or item.get("condition_id"))
        if condition_id:
            details[condition_id] = dict(item)
    matched_ids = set(details.keys())
    matched_ids.update(list_of_strings(trace.get("matchedConditionIds")))
    return matched_ids, details


def condition_ledger_rows(rule: Dict[str, object], trace: Dict[str, object]) -> List[Dict[str, object]]:
    matched_ids, details = matched_condition_details(trace)
    conditions = [dict(item) for item in rule.get("conditions") or [] if isinstance(item, dict)] if rule else []
    rows: List[Dict[str, object]] = []
    if not conditions:
        for index, condition_id in enumerate(sorted(matched_ids)):
            detail = details.get(condition_id, {})
            rows.append({
                "id": condition_id,
                "label": condition_id,
                "kind": clean_text(detail.get("kind")),
                "role": clean_text(detail.get("role") or "required"),
                "status": "matched",
                "relationType": clean_text(detail.get("relationType")),
                "field": clean_text(detail.get("field")),
                "operator": clean_text(detail.get("operator")),
                "value": detail.get("value"),
                "evidenceRelationId": clean_text(detail.get("relationId")),
                "index": index,
            })
        return rows
    for index, condition in enumerate(conditions):
        condition_id = condition_id_of(condition, index)
        role = clean_text(condition.get("role") or condition.get("conditionRole") or "required")
        detail = details.get(condition_id, {})
        matched = condition_id in matched_ids
        if matched and detail.get("absenceSatisfied"):
            status = "absence-satisfied"
        elif matched:
            status = "matched"
        elif role in {"optional", "any"}:
            status = "not-used"
        elif role == "not":
            status = "not-returned"
        else:
            status = "not-returned"
        rows.append({
            "id": condition_id,
            "label": clean_text(condition.get("description") or condition_id),
            "kind": clean_text(condition.get("kind") or detail.get("kind")),
            "role": role,
            "status": status,
            "relationType": clean_text(condition.get("relation_type") or condition.get("relationType") or detail.get("relationType")),
            "field": clean_text(condition.get("field") or detail.get("field")),
            "operator": clean_text(condition.get("operator") or detail.get("operator")),
            "value": condition.get("value") if "value" in condition else detail.get("value"),
            "evidenceRelationId": clean_text(detail.get("relationId")),
            "index": index,
        })
    return rows


def derivation_ledger_rows(rule: Dict[str, object]) -> List[Dict[str, object]]:
    rows = []
    for index, derivation in enumerate((rule or {}).get("derivations") or []):
        if not isinstance(derivation, dict):
            continue
        rows.append({
            "index": index,
            "relationType": clean_text(derivation.get("relation_type") or derivation.get("relationType")),
            "targetKind": clean_text(derivation.get("target_kind") or derivation.get("targetKind")),
            "targetLabel": clean_text(derivation.get("target_label") or derivation.get("targetLabel")),
            "polarity": clean_text(derivation.get("polarity")),
            "evidenceRole": clean_text(derivation.get("evidence_role") or derivation.get("evidenceRole") or derivation.get("polarity") or "context"),
            "reviewLevel": clean_text(derivation.get("review_level") or derivation.get("reviewLevel")),
            "dataState": clean_text(derivation.get("data_state") or derivation.get("dataState")),
            "decisionStage": clean_text(derivation.get("decision_stage") or derivation.get("decisionStage")),
            "actionGroup": clean_text(derivation.get("action_group") or derivation.get("actionGroup")),
            "actionLevel": clean_text(derivation.get("action_level") or derivation.get("actionLevel")),
            "label": clean_text(derivation.get("ai_influence_label") or derivation.get("aiInfluenceLabel") or derivation.get("belief_label") or derivation.get("beliefLabel")),
        })
    return rows


def primary_relation(relations: Iterable[Dict[str, object]]) -> Dict[str, object]:
    ranked = sorted(
        [dict(item) for item in relations or [] if isinstance(item, dict)],
        key=semantic_relation_sort_key,
    )
    return ranked[0] if ranked else {}


def trace_stage_rows(
    trace: Dict[str, object],
    rule: Dict[str, object],
    conditions: List[Dict[str, object]],
    relations: List[Dict[str, object]],
    entities: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    matched_count = len([item for item in conditions if item.get("status") in {"matched", "absence-satisfied"}])
    not_returned = len([item for item in conditions if item.get("status") == "not-returned"])
    decision = primary_relation(relations)
    notification_count = len([
        item for item in relations
        if relation_type_of(item) in {"CREATES_NOTIFICATION_INTENT", "REQUIRES_NEXT_CHECK"}
    ])
    evidence_count = len(set(list_of_strings(trace.get("evidenceRelationIds")) + [
        clean_text(item.get("evidenceRelationId"))
        for item in conditions
        if clean_text(item.get("evidenceRelationId"))
    ]))
    return [
        {"id": "source-data", "label": "Source facts", "status": "observed", "detail": str(evidence_count) + " evidence relation ids"},
        {"id": "rulebox", "label": "RuleBox", "status": "matched" if matched_count else "observed", "detail": str(matched_count) + "/" + str(len(conditions)) + " conditions matched"},
        {"id": "inferencebox", "label": "InferenceBox", "status": "materialized" if trace.get("nativeTypeDbReasoned") else "observed", "detail": clean_text(trace.get("reasoningMode") or trace.get("materializationSource") or "trace materialized")},
        {"id": "derived-output", "label": "Derived output", "status": "materialized" if relations or entities else "empty", "detail": str(len(relations)) + " relations · " + str(len(entities)) + " entities"},
        {"id": "decision", "label": "Decision context", "status": clean_text(decision.get("decisionStage") or "pending"), "detail": clean_text(decision.get("aiInfluenceLabel") or decision.get("targetLabel") or rule.get("prompt_hint") or rule.get("promptHint"))},
        {"id": "notification", "label": "Notification intent", "status": "linked" if notification_count else "none", "detail": str(notification_count) + " notification-related relations"},
        {"id": "audit", "label": "Auditability", "status": "review" if not_returned else "complete", "detail": str(not_returned) + " required conditions not returned in trace payload"},
    ]


def trace_ledger_row(
    trace: Dict[str, object],
    rule: Dict[str, object],
    relations: List[Dict[str, object]],
    entities: List[Dict[str, object]],
    index: int,
) -> Dict[str, object]:
    conditions = condition_ledger_rows(rule, trace)
    derivations = derivation_ledger_rows(rule)
    decision = primary_relation(relations)
    matched_count = len([item for item in conditions if item.get("status") in {"matched", "absence-satisfied"}])
    not_returned = len([item for item in conditions if item.get("status") == "not-returned"])
    relation_types = []
    for relation in relations:
        relation_type = relation_type_of(relation)
        if relation_type and relation_type not in relation_types:
            relation_types.append(relation_type)
    rule_id = clean_text(trace.get("ruleId") or trace.get("sourceRuleId") or rule_id_of(rule))
    evidence_roles = [evidence_role_from_relation(item) for item in relations]
    conflict_state = conflict_state_from_roles(evidence_roles)
    data_state = clean_text(decision.get("dataState") or trace.get("dataState") or "partial")
    review_level = clean_text(decision.get("reviewLevel") or trace.get("reviewLevel") or "observe")
    validation_state = clean_text(decision.get("validationState") or trace.get("validationState") or ("ready" if matched_count and not not_returned else "conditional"))
    return {
        "key": clean_text(trace.get("id") or (rule_id + ":" + clean_symbol(trace.get("symbol")))) or ("trace-" + str(index + 1)),
        "traceId": clean_text(trace.get("id")),
        "symbol": clean_symbol(trace.get("symbol")),
        "ruleId": rule_id,
        "ruleLabel": clean_text((rule or {}).get("label") or trace.get("label") or rule_id),
        "promptHint": clean_text((rule or {}).get("prompt_hint") or (rule or {}).get("promptHint") or trace.get("promptHint")),
        "status": "complete" if matched_count and not not_returned else "review",
        "reasoningMode": clean_text(trace.get("reasoningMode")),
        "materializationSource": clean_text(trace.get("materializationSource")),
        "updatedAt": clean_text(trace.get("updatedAt")),
        "reviewLevel": review_level,
        "dataState": data_state,
        "changeState": clean_text(decision.get("changeState") or trace.get("changeState") or "unchanged"),
        "conflictState": conflict_state,
        "validationState": validation_state,
        "evidenceRoles": sorted(set(evidence_roles)),
        "decisionStage": clean_text(decision.get("decisionStage")),
        "actionGroup": clean_text(decision.get("actionGroup")),
        "actionLevel": clean_text(decision.get("actionLevel")),
        "actionPolicy": clean_text(decision.get("actionPolicy")),
        "allowedActions": list_of_strings(decision.get("allowedActions")),
        "blockedActions": list_of_strings(decision.get("blockedActions")),
        "matchedConditionCount": matched_count,
        "conditionCount": len(conditions),
        "notReturnedConditionCount": not_returned,
        "derivedRelationCount": len(relations),
        "derivedEntityCount": len(entities),
        "relationTypes": relation_types[:12],
        "conditions": conditions,
        "derivations": derivations,
        "relations": relations,
        "entities": entities,
        "stages": trace_stage_rows(trace, rule or {}, conditions, relations, entities),
        "rawTrace": trace,
    }


def inference_trace_ledger_payload(
    inferencebox: Dict[str, object],
    rulebox: Dict[str, object] = None,
    symbols: List[str] = None,
    limit: int = 80,
) -> Dict[str, object]:
    inferencebox = dict(inferencebox or {})
    rulebox = dict(rulebox or {})
    clean_symbols = sorted(set(clean_symbol(item) for item in (symbols or []) if clean_symbol(item)))
    safe_limit = max(1, min(300, int(limit or 80)))
    rules_lookup = rule_by_id(rulebox)
    traces = [
        dict(item)
        for item in inferencebox.get("traces") or []
        if isinstance(item, dict) and (not clean_symbols or clean_symbol(item.get("symbol")) in clean_symbols)
    ]
    relations = [dict(item) for item in inferencebox.get("relations") or [] if isinstance(item, dict)]
    entities = [dict(item) for item in inferencebox.get("entities") or [] if isinstance(item, dict)]
    rows = []
    for index, trace in enumerate(traces[:safe_limit]):
        rule = rules_lookup.get(clean_text(trace.get("ruleId"))) or rules_lookup.get(clean_text(trace.get("sourceRuleId"))) or {}
        trace_relations = [relation for relation in relations if relation_mentions_trace_or_rule(relation, trace)]
        trace_entities = [entity for entity in entities if entity_mentions_trace_or_rule(entity, trace)]
        rows.append(trace_ledger_row(trace, rule, trace_relations, trace_entities, index))
    matched_rule_ids = sorted(set(clean_text(row.get("ruleId")) for row in rows if clean_text(row.get("ruleId"))))
    active_rule_ids = sorted(set(rule_id_of(rule) for rule in rulebox_rules(rulebox) if rule_id_of(rule) and rule.get("enabled", True) is not False))
    untraced_rule_ids = [rule_id for rule_id in active_rule_ids if rule_id not in matched_rule_ids]
    status = clean_text(inferencebox.get("status") or "empty")
    if rows:
        status = "ok"
    return {
        "generatedAt": utc_now_iso(),
        "status": status,
        "graphStore": clean_text(inferencebox.get("graphStore") or "typedb"),
        "source": clean_text(inferencebox.get("source")),
        "reason": clean_text(inferencebox.get("reason")),
        "query": {"symbols": clean_symbols, "limit": safe_limit},
        "inferenceGenerationId": clean_text(inferencebox.get("inferenceGenerationId")),
        "inferenceGenerationAt": clean_text(inferencebox.get("inferenceGenerationAt")),
        "reasoningMode": clean_text(inferencebox.get("reasoningMode")),
        "materializationSource": clean_text(inferencebox.get("materializationSource")),
        "nativeTypeDbReasoningUsed": bool(inferencebox.get("nativeTypeDbReasoningUsed")),
        "ruleboxShortHash": clean_text(inferencebox.get("ruleboxShortHash") or rulebox.get("activeShortHash") or rulebox.get("shortHash")),
        "summary": {
            "ledgerCount": len(rows),
            "traceCount": inferencebox.get("traceCount") or len(traces),
            "relationCount": inferencebox.get("relationCount") or len(relations),
            "entityCount": inferencebox.get("entityCount") or len(entities),
            "matchedRuleCount": len(matched_rule_ids),
            "activeRuleCount": len(active_rule_ids),
            "untracedRuleCount": len(untraced_rule_ids),
            "conditionCount": sum(int(row.get("conditionCount") or 0) for row in rows),
            "matchedConditionCount": sum(int(row.get("matchedConditionCount") or 0) for row in rows),
            "notReturnedConditionCount": sum(int(row.get("notReturnedConditionCount") or 0) for row in rows),
        },
        "ruleCoverage": {
            "matchedRuleIds": matched_rule_ids,
            "untracedRuleIds": untraced_rule_ids[:80],
            "coverageRatio": round((len(matched_rule_ids) / len(active_rule_ids)) * 100, 1) if active_rule_ids else 0.0,
        },
        "rows": rows,
    }
