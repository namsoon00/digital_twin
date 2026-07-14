from typing import Dict, Iterable, List, Optional

from .market_data import clamp
from .ontology_relation_catalog import DEFAULT_RELATION_RULES
from .ontology_relation_contracts import OntologyRuleMatch, RelationRuleDefinition
from .ontology_relation_decisions import (
    decision_stage_by_key,
    resolve_decision_stage,
    score_band,
    strength_label,
)


def _rule(rule_id: str, definitions: Optional[List[RelationRuleDefinition]] = None) -> RelationRuleDefinition:
    for item in definitions or DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    for item in DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    return DEFAULT_RELATION_RULES[-1]


def _match(
    rule_id: str,
    score: float,
    confidence: float,
    evidence: Iterable[str],
    missing: Iterable[str] = (),
    matched: bool = True,
    reference_only: bool = False,
    definitions: Optional[List[RelationRuleDefinition]] = None,
) -> OntologyRuleMatch:
    definition = _rule(rule_id, definitions)
    return OntologyRuleMatch(
        definition.rule_id,
        definition.label,
        definition.version,
        definition.relation_type,
        definition.signal_type,
        matched,
        clamp(score, 0.0, 100.0),
        strength_label(score),
        clamp(confidence, 0.0, 100.0),
        [str(item) for item in evidence if str(item or "").strip()],
        [str(item) for item in missing if str(item or "").strip()],
        reference_only,
        definition.prompt_hint,
    )


def decision_from_matches(facts: Dict[str, object], matches: List[OntologyRuleMatch]) -> Dict[str, object]:
    active = [item for item in matches if item.matched and not item.reference_only]
    if not active:
        stage = decision_stage_by_key("RELATION_WATCH")
        band = score_band(35.0)
        return {
            "label": stage.label,
            "tone": stage.tone,
            "score": 35.0,
            "basis": "ontologyRelationRules",
            "selectedRuleId": "",
            "decisionStage": stage.stage_key,
            "actionGroup": stage.action_group,
            "actionLevel": stage.action_level,
            "scoreBand": band.to_dict(),
            "nextStageAt": stage.next_stage_at,
        }
    priority = {
        "breakout.failure.v1": 48,
        "trend.breakdown_acceleration.v1": 45,
        "support.retest.failed.v1": 43,
        "holding.loss_guard.breakdown.v1": 40,
        "entry.wait_for_confirmation.v1": 39,
        "entry.momentum.confirmed.v1": 38,
        "entry.pullback.supported.v1": 38,
        "averaging_down.block.v1": 37,
        "holding.averaging_down.risk_guard.v1": 37,
        "distribution.detected.v1": 36,
        "profit.protection.volatility.v1": 36,
        "holding.profit_take.trend_weakness.v1": 35,
        "liquidity.exit_capacity.v1": 34,
        "news.direct_risk.new_material.v1": 33,
        "news.direct_risk.price_confirmed.v1": 32,
        "disclosure.material_event.v1": 30,
        "news.direct_support.new_material.v1": 29,
        "news.direct_support.price_confirmed.v1": 28,
        "news.direct_material.new.v1": 27,
        "rates.interest_rate.sensitivity.v1": 27,
        "factor.crowding.v1": 19,
        "macro.regime.shift.v1": 27,
        "fx.usd_krw.exposure.v1": 26,
        "external.crypto.btc_sensitivity.v1": 25,
        "news.sector_peer_context.v1": 24,
        "data.conflict.v1": 23,
        "holding.concentration.rebalance.v1": 20,
        "entry.add_buy.blocked.v1": 18,
        "holding.loss_smart_money.add_buy_review.v1": 35,
        "holding.winner_momentum.add_buy_review.v1": 35,
        "holding.loss_smart_money.reversal_watch.v1": 21,
        "holding.loss_smart_money.defense.v1": 19,
        "support.retest.confirmed.v1": 17,
        "trend.support_retest.v1": 16,
        "trend.recovery_attempt.v1": 14,
    }
    selected = max(active, key=lambda item: (priority.get(item.rule_id, 10), item.strength_score, item.confidence))
    stage = resolve_decision_stage(selected.rule_id, selected.strength_score, facts)
    band = score_band(selected.strength_score)
    tone = stage.tone
    if selected.rule_id == "holding.profit_take.trend_weakness.v1" and selected.strength_score >= 80:
        tone = "danger"
    return {
        "label": stage.label,
        "tone": tone,
        "score": round(float(selected.strength_score or 0), 1),
        "basis": "ontologyRelationRules",
        "selectedRuleId": selected.rule_id,
        "decisionStage": stage.stage_key,
        "actionGroup": stage.action_group,
        "actionLevel": stage.action_level,
        "scoreBand": band.to_dict(),
        "nextStageAt": stage.next_stage_at,
    }
