from typing import Dict, List

from .market_data import number
from .ontology_contracts import OntologyBelief, OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology, entity_id
from .ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from .ontology_rulebox_contracts import (
    GRAPH_REASONER_VERSION,
    HOLDING_TARGET_ROLE,
    WATCHLIST_ACTION_POLICY,
    WATCHLIST_ALLOWED_ACTIONS,
    WATCHLIST_BLOCKED_ACTIONS,
    WATCHLIST_TARGET_ROLE,
    GraphInferenceRule,
)
from .ontology_schema import abox_relation_properties
from .ontology_threshold_policy import default_ontology_threshold_policy


def materialize_rule_inference(
    graph: PortfolioOntology,
    rule: GraphInferenceRule,
    stock: OntologyEntity,
    context: Dict[str, object],
) -> None:
    properties = stock.properties or {}
    context = grounded_inference_context(graph, rule, stock, context)
    subject_key = stock.entity_id.replace(":", "-")
    symbol = str(properties.get("symbol") or subject_key).upper()
    display_name = stock.label or symbol
    confidence = number(context.get("confidence"))
    evidence_relation_ids = [str(item) for item in context.get("evidenceRelationIds") or []]
    trace_id = entity_id("inference-trace", symbol + ":" + rule.rule_id)
    trace_label = display_name + " · " + rule.label
    graph.entities.append(OntologyEntity(trace_id, trace_label, "inference-trace", inference_properties({
        "tboxClass": "InferenceTrace",
        "tboxClasses": ["InferenceTrace", "AIJudgmentAudit"],
        "symbol": symbol,
        "ruleId": rule.rule_id,
        "ruleLabel": rule.label,
        "engineVersion": GRAPH_REASONER_VERSION,
        "confidence": confidence,
        "matchedConditions": list(context.get("matchedConditions") or []),
        "evidenceRelationIds": evidence_relation_ids,
        "conditionDetailSource": str(context.get("conditionDetailSource") or ""),
        "evidenceCoverage": number(context.get("evidenceCoverage")),
        "freshnessStatus": str(context.get("freshnessStatus") or "unknown"),
        "promptHint": rule.prompt_hint,
    })))
    explanation_entities = materialize_inference_explanation_entities(
        graph,
        rule,
        stock,
        symbol,
        display_name,
        trace_id,
        confidence,
        evidence_relation_ids,
    )
    rule_entity_id = entity_id("rule", rule.rule_id)
    graph.relations.append(OntologyRelation(
        rule_entity_id,
        trace_id,
        "TRIGGERED_INFERENCE",
        weight=confidence,
        evidence_ids=evidence_relation_ids,
        properties=inference_relation_properties("TRIGGERED_INFERENCE", {
            "symbol": symbol,
            "ruleId": rule.rule_id,
            "aiInfluenceLabel": rule.label,
            "source": GRAPH_REASONER_VERSION,
        }),
    ))
    graph.relations.append(OntologyRelation(
        stock.entity_id,
        trace_id,
        "HAS_INFERENCE_TRACE",
        weight=confidence,
        evidence_ids=evidence_relation_ids,
        properties=inference_relation_properties("HAS_INFERENCE_TRACE", {
            "symbol": symbol,
            "ruleId": rule.rule_id,
            "aiInfluenceLabel": rule.label,
            "source": GRAPH_REASONER_VERSION,
        }),
    ))
    evidence_id_value = "evidence:inference:" + symbol + ":" + rule.rule_id
    graph.evidence.append(OntologyEvidence(
        evidence_id_value,
        stock.entity_id,
        "inference-trace",
        GRAPH_REASONER_VERSION,
        trace_label,
        {
            "ontologyBox": "InferenceBox",
            "ruleId": rule.rule_id,
            "ruleLabel": rule.label,
            "matchedConditions": list(context.get("matchedConditions") or []),
            "evidenceRelationIds": evidence_relation_ids,
            "conditionDetailSource": str(context.get("conditionDetailSource") or ""),
            "evidenceCoverage": number(context.get("evidenceCoverage")),
            "freshnessStatus": str(context.get("freshnessStatus") or "unknown"),
            "promptHint": rule.prompt_hint,
        },
        confidence,
    ))
    for index, derivation in enumerate(rule.derivations):
        target_key = fill_template(derivation.target_key, symbol, display_name, subject_key)
        target_label = fill_template(derivation.target_label, symbol, display_name, subject_key)
        target_id = entity_id(derivation.target_kind, target_key)
        action_group = derivation.action_group or rule.action_group
        action_level = derivation.action_level or rule.action_level
        action_policy = action_policy_properties(derivation, properties)
        decision_stage = derivation.decision_stage or decision_stage_from_action(action_group, action_level)
        stage_priority = derivation.stage_priority or relation_stage_priority({
            "decisionStage": decision_stage,
            "actionGroup": action_group,
            "actionLevel": action_level,
            "riskImpact": derivation.risk_impact,
            "supportImpact": derivation.support_impact,
        })
        graph.entities.append(OntologyEntity(target_id, target_label, derivation.target_kind, inference_properties({
            "tboxClass": derivation.tbox_class,
            "tboxClasses": derivation.tbox_classes or [derivation.tbox_class],
            "symbol": symbol,
            "ruleId": rule.rule_id,
            "ruleLabel": rule.label,
            "polarity": derivation.polarity,
            "actionGroup": action_group,
            "actionLevel": action_level,
            "decisionStage": decision_stage,
            "stagePriority": stage_priority,
            **action_policy,
            "inferenceTraceId": trace_id,
        })))
        relation_properties = {
            "symbol": symbol,
            "ruleId": rule.rule_id,
            "ruleLabel": rule.label,
            "derivationIndex": index,
            "polarity": derivation.polarity,
            "riskImpact": derivation.risk_impact,
            "supportImpact": derivation.support_impact,
            "actionGroup": action_group,
            "actionLevel": action_level,
            "decisionStage": decision_stage,
            "stagePriority": stage_priority,
            **action_policy,
            "aiInfluenceLabel": derivation.ai_influence_label or derivation.belief_label or target_label,
            "inferenceTraceId": trace_id,
            "source": GRAPH_REASONER_VERSION,
        }
        graph.relations.append(OntologyRelation(
            stock.entity_id,
            target_id,
            derivation.relation_type,
            weight=derivation.weight,
            evidence_ids=[evidence_id_value] + evidence_relation_ids,
            properties=inference_relation_properties(derivation.relation_type, relation_properties),
        ))
        graph.relations.append(OntologyRelation(
            target_id,
            trace_id,
            "EXPLAINED_BY_TRACE",
            weight=confidence,
            evidence_ids=[evidence_id_value] + evidence_relation_ids,
            properties=inference_relation_properties("EXPLAINED_BY_TRACE", {
                "symbol": symbol,
                "ruleId": rule.rule_id,
                "source": GRAPH_REASONER_VERSION,
                "aiInfluenceLabel": "추론 경로",
            }),
        ))
        if derivation.belief_label:
            belief_id = "belief:inference:" + symbol + ":" + rule.rule_id + ":" + str(index)
            graph.beliefs.append(OntologyBelief(
                belief_id,
                stock.entity_id,
                derivation.belief_label,
                derivation.polarity if derivation.polarity in {"risk", "support"} else "context",
                confidence,
                [evidence_id_value] + evidence_relation_ids,
            ))
    for explanation_id, relation_type, label in explanation_entities:
        graph.relations.append(OntologyRelation(
            stock.entity_id,
            explanation_id,
            relation_type,
            weight=confidence,
            evidence_ids=[evidence_id_value] + evidence_relation_ids,
            properties=inference_relation_properties(relation_type, {
                "symbol": symbol,
                "ruleId": rule.rule_id,
                "source": GRAPH_REASONER_VERSION,
                "aiInfluenceLabel": label,
                "inferenceTraceId": trace_id,
            }),
        ))
        graph.relations.append(OntologyRelation(
            explanation_id,
            trace_id,
            "EXPLAINED_BY_TRACE",
            weight=confidence,
            evidence_ids=[evidence_id_value] + evidence_relation_ids,
            properties=inference_relation_properties("EXPLAINED_BY_TRACE", {
                "symbol": symbol,
                "ruleId": rule.rule_id,
                "source": GRAPH_REASONER_VERSION,
                "aiInfluenceLabel": label,
            }),
        ))


def materialize_inference_explanation_entities(
    graph: PortfolioOntology,
    rule: GraphInferenceRule,
    stock: OntologyEntity,
    symbol: str,
    display_name: str,
    trace_id: str,
    confidence: float,
    evidence_relation_ids: List[str],
) -> List[tuple]:
    threshold_policy = default_ontology_threshold_policy().inference_materialization
    risk_impact = max([number(getattr(item, "risk_impact", 0)) for item in rule.derivations or []], default=0)
    support_impact = max([number(getattr(item, "support_impact", 0)) for item in rule.derivations or []], default=0)
    primary = primary_derivation(rule)
    action_group = (getattr(primary, "action_group", "") or rule.action_group) if primary else rule.action_group
    action_level = (getattr(primary, "action_level", "") or rule.action_level) if primary else rule.action_level
    decision_stage = getattr(primary, "decision_stage", "") if primary else ""
    decision_stage = decision_stage or decision_stage_from_action(action_group, action_level)
    stage_priority = relation_stage_priority({
        "decisionStage": decision_stage,
        "actionGroup": action_group,
        "actionLevel": action_level,
        "riskImpact": risk_impact,
        "supportImpact": support_impact,
    })
    conflict_type = signal_conflict_type(risk_impact, support_impact)
    should_escalate = bool(
        stage_priority >= threshold_policy.escalate_stage_priority
        or risk_impact >= threshold_policy.escalate_risk_impact
        or support_impact >= threshold_policy.escalate_support_impact
        or confidence >= threshold_policy.escalate_confidence
    )
    state_key = "|".join([value for value in [decision_stage, rule.rule_id] if str(value or "").strip()])
    common = {
        "symbol": symbol,
        "ruleId": rule.rule_id,
        "ruleLabel": rule.label,
        "engineVersion": GRAPH_REASONER_VERSION,
        "confidence": confidence,
        "sourceTraceId": trace_id,
        "evidenceRelationIds": evidence_relation_ids,
        "decisionStage": decision_stage,
        "stagePriority": stage_priority,
        "thresholdPolicyId": threshold_policy.policy_id,
        "thresholdPolicyVersion": threshold_policy.version,
        "thresholdPolicySource": threshold_policy.source,
    }
    why_id = entity_id("why-now", symbol + ":" + rule.rule_id)
    conflict_id = entity_id("signal-conflict", symbol + ":" + rule.rule_id)
    timeline_id = entity_id("inference-timeline", symbol + ":" + rule.rule_id)
    graph.entities.append(OntologyEntity(why_id, display_name + " 지금 볼 이유", "why-now", inference_properties({
        **common,
        "tboxClass": "WhyNow",
        "tboxClasses": ["MaterialityAssessment", "WhyNow"],
        "reasoningQuestion": "왜 지금 다시 봐야 하는가",
        "shouldEscalate": should_escalate,
        "changeDrivers": unique_non_empty([rule.label, rule.prompt_hint]),
    })))
    graph.entities.append(OntologyEntity(conflict_id, display_name + " 신호 충돌", "signal-conflict", inference_properties({
        **common,
        "tboxClass": "SignalConflict",
        "tboxClasses": ["Contradiction", "SignalConflict"],
        "hasConflict": conflict_type != "none",
        "conflictType": conflict_type,
        "riskPressure": risk_impact,
        "supportEvidence": support_impact,
        "netRiskPressure": risk_impact - support_impact,
    })))
    graph.entities.append(OntologyEntity(timeline_id, display_name + " 추론 타임라인", "inference-timeline", inference_properties({
        **common,
        "tboxClass": "InferenceTimeline",
        "tboxClasses": ["InferenceTrace", "InferencePath", "InferenceTimeline"],
        "timelineBasis": "typedb-native-rule-materialization",
        "currentStateKey": state_key,
        "activeRuleIds": [rule.rule_id],
    })))
    return [
        (why_id, "HAS_WHY_NOW", "지금 볼 이유"),
        (conflict_id, "HAS_SIGNAL_CONFLICT", "신호 충돌"),
        (timeline_id, "HAS_INFERENCE_TIMELINE", "추론 타임라인"),
    ]


def primary_derivation(rule: GraphInferenceRule):
    derivations = list(rule.derivations or [])
    if not derivations:
        return None
    return max(derivations, key=lambda item: (
        number(getattr(item, "risk_impact", 0)) + number(getattr(item, "support_impact", 0)),
        number(getattr(item, "weight", 0)),
    ))


def signal_conflict_type(risk_impact: float, support_impact: float) -> str:
    threshold_policy = default_ontology_threshold_policy().inference_materialization
    if risk_impact <= 0 or support_impact <= 0:
        return "none"
    if risk_impact > support_impact + threshold_policy.conflict_dominance_gap:
        return "risk-dominant-with-support"
    if support_impact > risk_impact + threshold_policy.conflict_dominance_gap:
        return "support-dominant-with-risk"
    return "mixed-signal"


def unique_non_empty(values: List[object]) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result[:8]


def inference_properties(properties: Dict[str, object]) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "InferenceBox")
    payload.setdefault("box", "InferenceBox")
    payload.setdefault("boundedContext", "reasoning-insight")
    payload.setdefault("engineVersion", GRAPH_REASONER_VERSION)
    return payload


def inference_relation_properties(relation_type: str, properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = abox_relation_properties(relation_type, properties or {})
    payload.update({"ontologyBox": "InferenceBox", "box": "InferenceBox", "engineVersion": GRAPH_REASONER_VERSION})
    return payload


def fill_template(template: str, symbol: str, display_name: str, subject_id: str = "") -> str:
    return (
        str(template or "")
        .replace("{symbol}", symbol)
        .replace("{displayName}", display_name)
        .replace("{subjectId}", subject_id or symbol)
    )


def action_policy_properties(derivation, stock_properties: Dict[str, object]) -> Dict[str, object]:
    target_role = str(getattr(derivation, "target_role", "") or "").strip()
    if not target_role:
        target_role = WATCHLIST_TARGET_ROLE if str((stock_properties or {}).get("source") or "").strip().lower() == "watchlist" else HOLDING_TARGET_ROLE
    action_policy = str(getattr(derivation, "action_policy", "") or "").strip()
    allowed_actions = [str(item) for item in (getattr(derivation, "allowed_actions", []) or []) if str(item or "").strip()]
    blocked_actions = [str(item) for item in (getattr(derivation, "blocked_actions", []) or []) if str(item or "").strip()]
    if target_role == WATCHLIST_TARGET_ROLE:
        action_policy = action_policy or WATCHLIST_ACTION_POLICY
        allowed_actions = allowed_actions or list(WATCHLIST_ALLOWED_ACTIONS)
        blocked_actions = blocked_actions or list(WATCHLIST_BLOCKED_ACTIONS)
    return {
        "targetRole": target_role,
        "actionPolicy": action_policy,
        "allowedActions": allowed_actions,
        "blockedActions": blocked_actions,
    }


def grounded_inference_context(
    graph: PortfolioOntology,
    rule: GraphInferenceRule,
    stock: OntologyEntity,
    context: Dict[str, object],
) -> Dict[str, object]:
    payload = dict(context or {})
    conditions_by_id = {
        str(item.condition_id or ""): item
        for item in list(rule.conditions or [])
        if str(item.condition_id or "").strip()
    }
    grounded_conditions: List[Dict[str, object]] = []
    evidence_relation_ids = {
        str(item)
        for item in payload.get("evidenceRelationIds") or []
        if str(item or "").strip()
    }
    for raw in payload.get("matchedConditions") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        condition = conditions_by_id.get(str(item.get("conditionId") or ""))
        if condition and not item.get("absenceSatisfied"):
            if str(getattr(condition, "kind", "") or "") == "subject_property":
                field = str(getattr(condition, "field", "") or item.get("field") or "")
                observed_value = (stock.properties or {}).get(field)
                item.update(observation_metadata(stock.properties or {}, observed_value))
                item["field"] = field
            elif str(getattr(condition, "kind", "") or "") == "relation":
                relation, target = matching_evidence_relation(graph, stock.entity_id, condition)
                if relation:
                    relation_id = str((relation.properties or {}).get("_relationId") or "")
                    if relation_id:
                        item["relationId"] = relation_id
                        evidence_relation_ids.add(relation_id)
                    target_properties = target.properties if target else {}
                    observed_value = evidence_observed_value(target, condition)
                    item.update(observation_metadata(target_properties or {}, observed_value))
                    item["relationType"] = relation.relation_type
                    item["targetId"] = relation.target if relation.source == stock.entity_id else relation.source
                    item["targetKind"] = target.kind if target else ""
        grounded_conditions.append(item)
    grounded_count = sum(1 for item in grounded_conditions if condition_is_grounded(item))
    coverage = grounded_count / max(1, len(grounded_conditions))
    freshness_status = aggregate_condition_freshness(grounded_conditions)
    rule_reliability = max([number(getattr(item, "weight", 0)) for item in rule.derivations or []], default=0.72)
    freshness_factor = {"fresh": 1.0, "aging": 0.72, "delayed": 0.65, "stale": 0.3}.get(freshness_status, 0.55)
    confidence = min(0.96, max(0.25, rule_reliability * 0.45 + coverage * 0.35 + freshness_factor * 0.20))
    payload.update({
        "matchedConditions": grounded_conditions,
        "evidenceRelationIds": sorted(evidence_relation_ids),
        "conditionDetailSource": "typedb-match+abox-grounding",
        "evidenceCoverage": round(coverage * 100.0, 1),
        "freshnessStatus": freshness_status,
        "confidence": round(confidence, 3),
    })
    return payload


def matching_evidence_relation(graph: PortfolioOntology, stock_id: str, condition):
    relation_type = str(getattr(condition, "relation_type", "") or "")
    direction = str(getattr(condition, "direction", "") or "out").lower()
    entities = {item.entity_id: item for item in graph.entities}
    candidates = []
    for relation in graph.relations:
        if relation.relation_type != relation_type:
            continue
        if direction == "in" and relation.target != stock_id:
            continue
        if direction != "in" and relation.source != stock_id:
            continue
        target_id = relation.source if direction == "in" else relation.target
        target = entities.get(target_id)
        target_kind = str(getattr(condition, "target_kind", "") or "")
        if target_kind and (not target or target.kind != target_kind):
            continue
        if number(relation.weight) < number(getattr(condition, "min_weight", 0)):
            continue
        if not ontology_properties_match(
            target.properties if target else {},
            getattr(condition, "target_property_filters", {}) or {},
        ):
            continue
        if not ontology_properties_match(
            relation.properties or {},
            getattr(condition, "relation_property_filters", {}) or {},
        ):
            continue
        candidates.append((relation, target))
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: number(item[0].weight))


def ontology_properties_match(properties: Dict[str, object], filters: Dict[str, object]) -> bool:
    properties = properties or {}
    for key, expected in dict(filters or {}).items():
        actual_key = str(key)
        operator = "=="
        if actual_key == "minValue":
            actual_key, operator = "value", ">="
        elif actual_key == "maxValue":
            actual_key, operator = "value", "<="
        elif actual_key.startswith("min") and len(actual_key) > 3:
            actual_key, operator = actual_key[3].lower() + actual_key[4:], ">="
        elif actual_key.startswith("max") and len(actual_key) > 3:
            actual_key, operator = actual_key[3].lower() + actual_key[4:], "<="
        if isinstance(expected, dict):
            operator = str(expected.get("operator") or operator)
            expected = expected.get("value")
        if not ontology_property_value_matches(properties.get(actual_key), operator, expected):
            return False
    return True


def ontology_property_value_matches(actual: object, operator: str, expected: object) -> bool:
    if actual in (None, ""):
        return False
    if operator in {">", ">=", "<", "<="}:
        actual_number = number(actual)
        expected_number = number(expected)
        if operator == ">":
            return actual_number > expected_number
        if operator == ">=":
            return actual_number >= expected_number
        if operator == "<":
            return actual_number < expected_number
        return actual_number <= expected_number
    if isinstance(actual, (list, tuple, set)):
        actual_values = {str(item) for item in actual}
        if isinstance(expected, (list, tuple, set)):
            return bool(actual_values.intersection(str(item) for item in expected))
        return str(expected) in actual_values
    if isinstance(expected, (list, tuple, set)):
        return str(actual) in {str(item) for item in expected}
    if operator in {"!=", "not"}:
        return str(actual) != str(expected)
    return actual == expected or str(actual) == str(expected)


def evidence_observed_value(target: OntologyEntity, condition):
    if not target:
        return ""
    properties = target.properties or {}
    filters = getattr(condition, "target_property_filters", {}) or {}
    filter_values = {
        str(key): properties.get(str(key))
        for key in filters.keys()
        if properties.get(str(key)) not in (None, "")
    }
    for key in ["value", "valueNumber", "currentValue", "materialityScore", "score"]:
        if properties.get(key) not in (None, ""):
            return properties.get(key)
    return filter_values or target.label


def observation_metadata(properties: Dict[str, object], observed_value: object) -> Dict[str, object]:
    properties = properties or {}
    source = str(
        properties.get("provider")
        or properties.get("source")
        or properties.get("quoteSource")
        or ""
    ).strip()
    observed_at = str(
        properties.get("sourceAsOf")
        or properties.get("observedAt")
        or properties.get("sourceFetchedAt")
        or properties.get("updatedAt")
        or properties.get("publishedAt")
        or ""
    ).strip()
    freshness = normalized_freshness_status(
        properties.get("freshnessStatus")
        or properties.get("dataFreshnessStatus")
        or properties.get("quoteStatus")
        or properties.get("valuationFreshnessStatus")
    )
    result = {
        "observedValue": observed_value,
        "source": source,
        "observedAt": observed_at,
        "freshnessStatus": freshness,
    }
    reliability = number(properties.get("sourceReliability") or properties.get("reliabilityScore"))
    if reliability > 0:
        result["sourceReliability"] = round(reliability, 1)
    return result


def normalized_freshness_status(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"fresh", "live", "ok", "available", "current"}:
        return "fresh"
    if text in {"aging", "delayed", "latency"}:
        return "aging"
    if text in {"stale", "cached", "expired", "unavailable"}:
        return "stale"
    return "unknown"


def condition_is_grounded(condition: Dict[str, object]) -> bool:
    return bool(
        condition.get("observedValue") not in (None, "")
        or str(condition.get("relationId") or "").strip()
        or condition.get("absenceSatisfied")
    )


def aggregate_condition_freshness(conditions: List[Dict[str, object]]) -> str:
    statuses = [
        str(item.get("freshnessStatus") or "unknown")
        for item in conditions or []
        if condition_is_grounded(item)
    ]
    if "stale" in statuses:
        return "stale"
    if "aging" in statuses:
        return "aging"
    if statuses and all(item == "fresh" for item in statuses):
        return "fresh"
    return "unknown"
