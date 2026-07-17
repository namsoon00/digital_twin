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


def materialize_rule_inference(
    graph: PortfolioOntology,
    rule: GraphInferenceRule,
    stock: OntologyEntity,
    context: Dict[str, object],
) -> None:
    properties = stock.properties or {}
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
    should_escalate = bool(stage_priority >= 55 or risk_impact >= 12 or support_impact >= 12 or confidence >= 0.86)
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
    if risk_impact <= 0 or support_impact <= 0:
        return "none"
    if risk_impact > support_impact + 3:
        return "risk-dominant-with-support"
    if support_impact > risk_impact + 3:
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
