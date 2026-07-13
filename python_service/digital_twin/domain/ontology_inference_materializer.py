from typing import Dict

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
    rule_entity_id = entity_id("rule", rule.rule_id)
    graph.relations.append(OntologyRelation(
        rule_entity_id,
        trace_id,
        "TRIGGERED_INFERENCE",
        weight=confidence,
        evidence_ids=evidence_relation_ids,
        properties=inference_relation_properties("TRIGGERED_INFERENCE", {
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
