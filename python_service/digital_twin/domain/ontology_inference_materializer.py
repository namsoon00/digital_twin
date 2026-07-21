from typing import Dict, List

from .data_freshness import age_minutes
from .market_data import number
from .ontology_contracts import OntologyBelief, OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology, entity_id
from .ontology_decision_state import (
    ACTION_LEVEL_RANK,
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    conflict_state_from_roles,
    review_level_for,
)
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
    data_state = str(context.get("dataState") or "partial")
    review_level = review_level_for(rule.action_level, data_state)
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
        "reviewLevel": review_level,
        "reviewLevelLabel": REVIEW_LEVEL_LABELS[review_level],
        "dataState": data_state,
        "dataStateLabel": DATA_STATE_LABELS[data_state],
        "matchedConditions": list(context.get("matchedConditions") or []),
        "evidenceRelationIds": evidence_relation_ids,
        "conditionDetailSource": str(context.get("conditionDetailSource") or ""),
        "requiredConditionCount": int(number(context.get("requiredConditionCount"))),
        "groundedConditionCount": int(number(context.get("groundedConditionCount"))),
        "freshnessStatus": str(context.get("freshnessStatus") or "unknown"),
        "freshnessGateReason": str(context.get("freshnessGateReason") or ""),
        "temporalEvidenceCount": int(number(context.get("temporalEvidenceCount"))),
        "evidenceUsableForJudgement": bool(context.get("evidenceUsableForJudgement")),
        "promptHint": rule.prompt_hint,
    })))
    explanation_entities = materialize_inference_explanation_entities(
        graph,
        rule,
        stock,
        symbol,
        display_name,
        trace_id,
        evidence_relation_ids,
        data_state,
    )
    rule_entity_id = entity_id("rule", rule.rule_id)
    graph.relations.append(OntologyRelation(
        rule_entity_id,
        trace_id,
        "TRIGGERED_INFERENCE",
        weight=1.0,
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
        weight=1.0,
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
            "requiredConditionCount": int(number(context.get("requiredConditionCount"))),
            "groundedConditionCount": int(number(context.get("groundedConditionCount"))),
            "freshnessStatus": str(context.get("freshnessStatus") or "unknown"),
            "freshnessGateReason": str(context.get("freshnessGateReason") or ""),
            "temporalEvidenceCount": int(number(context.get("temporalEvidenceCount"))),
            "evidenceUsableForJudgement": bool(context.get("evidenceUsableForJudgement")),
            "promptHint": rule.prompt_hint,
        },
        "context",
        data_state,
    ))
    for index, derivation in enumerate(rule.derivations):
        target_key = fill_template(derivation.target_key, symbol, display_name, subject_key)
        target_label = fill_template(derivation.target_label, symbol, display_name, subject_key)
        target_id = entity_id(derivation.target_kind, target_key)
        action_group = derivation.action_group or rule.action_group
        action_level = derivation.action_level or rule.action_level
        action_policy = action_policy_properties(derivation, properties)
        decision_stage = derivation.decision_stage
        evidence_role = derivation_evidence_role(derivation)
        derivation_review_level = review_level_for(action_level, data_state)
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
            "decisionLabel": derivation.decision_label or rule.label,
            "decisionTone": derivation.decision_tone,
            "evidenceRole": evidence_role,
            "reviewLevel": derivation_review_level,
            "reviewLevelLabel": REVIEW_LEVEL_LABELS[derivation_review_level],
            "dataState": data_state,
            "dataStateLabel": DATA_STATE_LABELS[data_state],
            **action_policy,
            "inferenceTraceId": trace_id,
        })))
        relation_properties = {
            "symbol": symbol,
            "ruleId": rule.rule_id,
            "ruleLabel": rule.label,
            "derivationIndex": index,
            "polarity": derivation.polarity,
            "evidenceRole": evidence_role,
            "actionGroup": action_group,
            "actionLevel": action_level,
            "decisionStage": decision_stage,
            "decisionLabel": derivation.decision_label or rule.label,
            "decisionTone": derivation.decision_tone,
            "reviewLevel": derivation_review_level,
            "dataState": data_state,
            **action_policy,
            "aiInfluenceLabel": derivation.ai_influence_label or derivation.belief_label or target_label,
            "inferenceTraceId": trace_id,
            "freshnessStatus": str(context.get("freshnessStatus") or "unknown"),
            "freshnessGateReason": str(context.get("freshnessGateReason") or ""),
            "evidenceUsableForJudgement": bool(context.get("evidenceUsableForJudgement")),
            "source": GRAPH_REASONER_VERSION,
        }
        graph.relations.append(OntologyRelation(
            stock.entity_id,
            target_id,
            derivation.relation_type,
            weight=1.0,
            evidence_ids=[evidence_id_value] + evidence_relation_ids,
            properties=inference_relation_properties(derivation.relation_type, relation_properties),
        ))
        graph.relations.append(OntologyRelation(
            target_id,
            trace_id,
            "EXPLAINED_BY_TRACE",
            weight=1.0,
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
                evidence_role,
                derivation_review_level,
                data_state,
                [evidence_id_value] + evidence_relation_ids,
            ))
    for explanation_id, relation_type, label in explanation_entities:
        graph.relations.append(OntologyRelation(
            stock.entity_id,
            explanation_id,
            relation_type,
            weight=1.0,
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
            weight=1.0,
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
    evidence_relation_ids: List[str],
    data_state: str,
) -> List[tuple]:
    primary = primary_derivation(rule)
    action_group = (getattr(primary, "action_group", "") or rule.action_group) if primary else rule.action_group
    action_level = (getattr(primary, "action_level", "") or rule.action_level) if primary else rule.action_level
    decision_stage = getattr(primary, "decision_stage", "") if primary else ""
    review_level = review_level_for(action_level, data_state)
    evidence_roles = [derivation_evidence_role(item) for item in rule.derivations or []]
    conflict_state = conflict_state_from_roles(evidence_roles)
    should_escalate = review_level in {"act", "immediate", "blocked"}
    state_key = "|".join([value for value in [decision_stage, rule.rule_id] if str(value or "").strip()])
    common = {
        "symbol": symbol,
        "ruleId": rule.rule_id,
        "ruleLabel": rule.label,
        "engineVersion": GRAPH_REASONER_VERSION,
        "sourceTraceId": trace_id,
        "evidenceRelationIds": evidence_relation_ids,
        "decisionStage": decision_stage,
        "reviewLevel": review_level,
        "reviewLevelLabel": REVIEW_LEVEL_LABELS[review_level],
        "dataState": data_state,
        "dataStateLabel": DATA_STATE_LABELS[data_state],
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
        "hasConflict": conflict_state == "mixed",
        "conflictState": conflict_state,
        "evidenceRoles": evidence_roles,
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
    return min(derivations, key=derivation_semantic_sort_key)


def derivation_evidence_role(derivation) -> str:
    explicit = str(getattr(derivation, "evidence_role", "") or "").strip().lower()
    if explicit in {"risk", "support", "counter", "context", "blocking"}:
        return explicit
    polarity = str(getattr(derivation, "polarity", "") or "").strip().lower()
    return polarity if polarity in {"risk", "support", "counter", "context"} else "context"


def derivation_semantic_sort_key(derivation):
    stage = str(getattr(derivation, "decision_stage", "") or "").strip()
    action_level = str(getattr(derivation, "action_level", "") or "reference").strip().lower()
    role_index = {"blocking": 0, "risk": 1, "counter": 2, "support": 3, "context": 4}.get(
        derivation_evidence_role(derivation),
        5,
    )
    return -ACTION_LEVEL_RANK.get(action_level, -1), role_index, stage, str(getattr(derivation, "relation_type", "") or "")


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
                stock_observation_properties = dict(stock.properties or {})
                stock_observation_properties.setdefault("observationKind", stock.kind)
                item.update(observation_metadata(stock_observation_properties, observed_value))
                item["field"] = field
            elif str(getattr(condition, "kind", "") or "") == "relation":
                relation, target = matching_evidence_relation(graph, stock.entity_id, condition)
                if relation:
                    relation_id = str((relation.properties or {}).get("_relationId") or "")
                    if relation_id:
                        item["relationId"] = relation_id
                        evidence_relation_ids.add(relation_id)
                    target_properties = dict(target.properties if target else {})
                    target_properties.setdefault("observationKind", target.kind if target else "")
                    target_properties.setdefault("observationRelationType", relation.relation_type)
                    observed_value = evidence_observed_value(target, condition)
                    item.update(observation_metadata(target_properties or {}, observed_value))
                    item["relationType"] = relation.relation_type
                    item["targetId"] = relation.target if relation.source == stock.entity_id else relation.source
                    item["targetKind"] = target.kind if target else ""
        grounded_conditions.append(item)
    grounded_count = sum(1 for item in grounded_conditions if condition_is_grounded(item))
    required_conditions = [
        item
        for item in grounded_conditions
        if str(item.get("role") or "required").strip().lower() not in {"optional"}
    ]
    freshness_status = aggregate_condition_freshness(grounded_conditions)
    temporal_conditions = [
        item
        for item in grounded_conditions
        if condition_is_grounded(item) and bool(item.get("freshnessRequired"))
    ]
    blocked_temporal_conditions = [
        item
        for item in temporal_conditions
        if not bool(item.get("judgementEvidenceUsable"))
    ]
    required_count = len(required_conditions)
    grounded_required_count = sum(1 for item in required_conditions if condition_is_grounded(item))
    if blocked_temporal_conditions:
        data_state = "unavailable" if grounded_required_count <= len(blocked_temporal_conditions) else "partial"
    elif required_count and grounded_required_count < required_count:
        data_state = "partial" if grounded_required_count else "insufficient"
    elif grounded_count:
        data_state = "sufficient"
    else:
        data_state = "insufficient"
    evidence_usable = data_state in {"sufficient", "partial"} and not blocked_temporal_conditions
    if data_state == "insufficient":
        freshness_gate_reason = "성립 조건의 실제 관측값이 충분하지 않습니다."
    elif blocked_temporal_conditions:
        freshness_gate_reason = str(
            blocked_temporal_conditions[0].get("freshnessGateReason")
            or "시간에 민감한 근거가 신선도 기준을 통과하지 못했습니다."
        )
    elif temporal_conditions:
        freshness_gate_reason = "시간에 민감한 근거가 원천 시각과 거래 세션 기준을 통과했습니다."
    else:
        freshness_gate_reason = "시간에 민감한 조건이 없는 구조·정책 추론입니다."
    payload.update({
        "matchedConditions": grounded_conditions,
        "evidenceRelationIds": sorted(evidence_relation_ids),
        "conditionDetailSource": "typedb-match+abox-grounding",
        "requiredConditionCount": required_count,
        "groundedConditionCount": grounded_required_count,
        "dataState": data_state,
        "dataStateLabel": DATA_STATE_LABELS[data_state],
        "freshnessStatus": freshness_status,
        "freshnessGateReason": freshness_gate_reason,
        "temporalEvidenceCount": len(temporal_conditions),
        "evidenceUsableForJudgement": evidence_usable,
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
    return sorted(
        candidates,
        key=lambda item: (
            str((item[0].properties or {}).get("_relationId") or ""),
            str(item[0].target or ""),
        ),
    )[0]


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
    for key in ["value", "valueNumber", "currentValue", "state", "status", "level"]:
        if properties.get(key) not in (None, ""):
            return properties.get(key)
    return filter_values or target.label


def observation_metadata(properties: Dict[str, object], observed_value: object) -> Dict[str, object]:
    properties = properties or {}
    source = str(
        properties.get("provider")
        or properties.get("observationSource")
        or properties.get("source")
        or properties.get("quoteSource")
        or ""
    ).strip()
    source_as_of = str(
        properties.get("sourceAsOf")
        or properties.get("observedAt")
        or properties.get("publishedAt")
        or ""
    ).strip()
    source_fetched_at = str(properties.get("sourceFetchedAt") or properties.get("fetchedAt") or "").strip()
    observed_at = source_as_of
    explicit_required = properties.get("freshnessRequired")
    if explicit_required in (None, ""):
        freshness_required = bool(
            source_as_of
            or source_fetched_at
            or properties.get("freshnessStatus")
            or properties.get("dataFreshnessStatus")
            or properties.get("quoteStatus")
            or properties.get("valuationFreshnessStatus")
            or inferred_time_sensitive_observation(properties)
        )
    else:
        freshness_required = bool_value(explicit_required)
    freshness = normalized_freshness_status(
        properties.get("freshnessStatus")
        or properties.get("dataFreshnessStatus")
        or properties.get("quoteStatus")
        or properties.get("valuationFreshnessStatus")
    )
    maximum_age = int(number(properties.get("maxAgeMinutes"))) or default_observation_max_age(properties)
    freshness_age = properties.get("freshnessAgeMinutes")
    if freshness_age in (None, "") and source_as_of:
        freshness_age = age_minutes(source_as_of)
    if freshness_required and freshness == "unknown" and freshness_age is not None:
        freshness = "fresh" if number(freshness_age) <= maximum_age else "stale"
    if not freshness_required:
        freshness = "not-applicable"
    source_timestamp_present = bool(source_as_of)
    explicit_usable = properties.get("judgementEvidenceUsable")
    if not freshness_required:
        judgement_usable = True
    elif explicit_usable not in (None, ""):
        judgement_usable = bool_value(explicit_usable) and source_timestamp_present and freshness == "fresh"
    else:
        judgement_usable = source_timestamp_present and freshness == "fresh"
    if not freshness_required:
        gate_reason = "시간에 따라 바뀌지 않는 구조·정책 근거입니다."
    elif not source_timestamp_present:
        gate_reason = "원천 기준시각이 없어 투자 판단 근거로 사용할 수 없습니다."
    elif freshness != "fresh":
        gate_reason = str(properties.get("freshnessReason") or "원천 데이터가 허용 시간보다 오래되었습니다.")
    elif not judgement_usable:
        gate_reason = str(properties.get("freshnessGateReason") or properties.get("marketSessionReason") or "현재 거래 세션에서는 판단 근거로 사용하지 않습니다.")
    else:
        gate_reason = str(properties.get("freshnessGateReason") or "원천 시각과 거래 세션 기준을 통과했습니다.")
    result = {
        "observedValue": observed_value,
        "source": source,
        "observedAt": observed_at,
        "sourceFetchedAt": source_fetched_at,
        "sourceTimestampPresent": source_timestamp_present,
        "freshnessRequired": freshness_required,
        "freshnessStatus": freshness,
        "freshnessAgeMinutes": freshness_age,
        "maxAgeMinutes": maximum_age if freshness_required else None,
        "freshnessGateReason": gate_reason,
        "marketSessionStatus": str(properties.get("marketSessionStatus") or ""),
        "judgementEvidenceUsable": judgement_usable,
    }
    source_trust_state = str(properties.get("sourceTrustState") or "").strip().lower()
    if not source_trust_state:
        legacy_reliability = number(properties.get("sourceReliability") or properties.get("reliabilityScore"))
        if legacy_reliability > 1:
            legacy_reliability /= 100.0
        source_trust_state = "trusted" if legacy_reliability >= 0.8 else "standard" if legacy_reliability >= 0.6 else "limited" if legacy_reliability > 0 else "unknown"
    result["sourceTrustState"] = source_trust_state
    return result


def normalized_freshness_status(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"fresh", "live", "ok", "available", "current"}:
        return "fresh"
    if text in {"aging", "delayed", "latency"}:
        return "aging"
    if text in {"stale", "cached", "expired", "unavailable"}:
        return "stale"
    if text in {"not-applicable", "not_applicable", "n/a", "na", "static"}:
        return "not-applicable"
    return "unknown"


def bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def default_observation_max_age(properties: Dict[str, object]) -> int:
    domain = str((properties or {}).get("observationDomain") or "").strip().lower()
    if domain in {"trend", "technical"}:
        return 4320
    if domain in {"news", "disclosure", "macro"}:
        return 120
    if domain == "valuation":
        return 1440
    return 10


def inferred_time_sensitive_observation(properties: Dict[str, object]) -> bool:
    semantic_text = " ".join([
        str((properties or {}).get("observationDomain") or ""),
        str((properties or {}).get("observationKind") or ""),
        str((properties or {}).get("observationRelationType") or ""),
        str((properties or {}).get("tboxClass") or ""),
    ]).strip().lower().replace("_", "-")
    time_sensitive_tokens = {
        "article", "corporate-action", "coverage-gap", "cross-market", "data-quality",
        "disclosure", "event-impact", "execution-capacity", "execution-metric", "external-signal",
        "fact-change", "flow", "freshness", "interest-rate", "investor", "key-level",
        "loss-defense", "macro", "margin-of-safety", "market-proxy-observation", "missing-data",
        "news", "price", "quote", "recovery", "research-evidence", "smart-money", "technical",
        "temporal", "trend", "valuation",
    }
    return any(token in semantic_text for token in time_sensitive_tokens)


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
        if condition_is_grounded(item) and bool(item.get("freshnessRequired"))
    ]
    if not statuses:
        return "not-applicable"
    if "stale" in statuses:
        return "stale"
    if "unknown" in statuses:
        return "unknown"
    if "aging" in statuses:
        return "aging"
    if statuses and all(item == "fresh" for item in statuses):
        return "fresh"
    return "unknown"
