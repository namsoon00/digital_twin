"""Disabled TypeDB-rule candidates that consume validated model signals."""

from dataclasses import replace
import hashlib
import json
from typing import Dict, Iterable, List

from ..hypothesis_scoping import condition_scope_profile
from ..ontology_rulebox_contracts import (
    GraphInferenceRule,
    GraphRuleCondition,
    HypothesisLifecyclePolicy,
)
from .rule_contracts import PRICE_TREND_SIGNALS, rule_statistical_signal_contract


STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION = "statistical-rule-candidate-release-v1"
EMPIRICAL_PRICE_THEORIES = {
    "behavioral-momentum-and-trend",
    "behavioral-mean-reversion",
}


def _candidate_signal_type(rule: GraphInferenceRule) -> str:
    rule_id = str(rule.rule_id or "").lower()
    if any(value in rule_id for value in ("acceleration", "persistent_decline", "weakness_accumulation")):
        return "price-downside-acceleration-risk"
    if any(value in rule_id for value in ("break", "failure", "failed", "distribution", "protect", "risk")):
        return "price-trend-break-risk"
    if any(value in rule_id for value in ("rebound", "recovery", "reclaim", "reversal", "deceleration")):
        return "price-recovery-support"
    return "price-trend-continuation-support"


def _account_conditions(rule: GraphInferenceRule) -> List[GraphRuleCondition]:
    rows = []
    for index, condition in enumerate(rule.conditions or []):
        profile = condition_scope_profile(condition.to_dict(), index)
        if str(profile.get("scope") or "") == "account":
            rows.append(condition)
    return rows


def compile_price_signal_rule_candidate(rule: GraphInferenceRule) -> GraphInferenceRule:
    theory = str(rule.resolved_knowledge_basis.theory_family or "")
    if theory not in EMPIRICAL_PRICE_THEORIES:
        raise ValueError("Rule is not part of the price-signal migration cohort: " + rule.rule_id)
    signal_type = _candidate_signal_type(rule)
    if signal_type not in PRICE_TREND_SIGNALS:
        raise ValueError("Unsupported price signal type: " + signal_type)
    signal_condition = GraphRuleCondition(
        condition_id="validated-model-signal:" + rule.rule_id,
        kind="relation",
        description="역사적 재생과 확률 교정을 통과한 통계 신호가 존재합니다.",
        relation_type="HAS_MODEL_SIGNAL",
        direction="out",
        target_kind="statistical-model-signal",
        target_property_filters={
            "signalType": signal_type,
            "strengthBand": "strong",
            "validationStatus": "calibrated",
            "decisionEligibility": "eligible",
            "eligibilityStatus": "eligible",
        },
        role="required",
        hypothesis_scope="market",
        evidence_group_key="validated-model-signal:" + signal_type,
        change_trigger=True,
        invalidation_trigger=True,
    )
    account_conditions = _account_conditions(rule)
    conditions = [*account_conditions, signal_condition]
    formation_ids = [item.condition_id for item in conditions]
    lifecycle = rule.resolved_hypothesis_lifecycle()
    candidate_lifecycle = HypothesisLifecyclePolicy(
        formation_condition_ids=formation_ids,
        invalidation_condition_ids=[signal_condition.condition_id],
        validity_minutes=lifecycle.validity_minutes,
        required_freshness_domains=sorted(set([
            *list(lifecycle.required_freshness_domains or []),
            "model-signal",
        ])),
        next_data_requirements=list(lifecycle.next_data_requirements or []),
        invalidation_mode="typedb-rule-not-materialized",
        outcome_contract=lifecycle.outcome_contract,
    )
    basis = replace(
        rule.resolved_knowledge_basis,
        threshold_origin="calibrated-model-release-policy",
        validation_status="candidate-replay-required",
        decision_eligibility="reference-only",
        outcome_validation_required=True,
        plain_language_basis=(
            rule.resolved_knowledge_basis.plain_language_basis
            + " 숫자 경로 조건은 검증된 통계 모델 신호로 교체한 비활성 후보입니다."
        ).strip(),
    )
    return replace(
        rule,
        rule_id=rule.rule_id + ".model-signal-candidate.v1",
        label=rule.label + " · 통계 신호 후보",
        version=STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION,
        conditions=conditions,
        knowledge_basis=basis,
        hypothesis_lifecycle=candidate_lifecycle,
        any_condition_min_count=1,
        execution_stage="candidate-model-signal",
        failure_policy="block",
        cost_hint="compact-model-signal",
        enabled=False,
    )


def price_signal_rule_candidates(rules: Iterable[GraphInferenceRule]) -> List[GraphInferenceRule]:
    return [
        compile_price_signal_rule_candidate(rule)
        for rule in rules or []
        if str(rule.resolved_knowledge_basis.theory_family or "") in EMPIRICAL_PRICE_THEORIES
        and str(rule.resolved_knowledge_basis.rule_kind or "") == "predictive-hypothesis"
    ]


def statistical_rule_candidate_release(rules: Iterable[GraphInferenceRule]) -> Dict[str, object]:
    source_rules = list(rules or [])
    candidates = price_signal_rule_candidates(source_rules)
    payload = {
        "version": STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION,
        "status": "disabled-candidate",
        "productionEligible": False,
        "candidateCount": len(candidates),
        "sourceRuleIds": [rule.rule_id for rule in source_rules if rule_statistical_signal_contract(rule).get("migrationState") == "shadow-signal-available"],
        "candidateRuleIds": [rule.rule_id for rule in candidates],
        "rules": [rule.to_dict() for rule in candidates],
        "promotionGates": [
            "point-in-time-replay-complete",
            "minimum-outcome-sample-count-met",
            "probability-calibration-approved",
            "economic-utility-not-worse",
            "no-action-envelope-regression",
            "latency-slo-not-worse",
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    payload["fingerprint"] = hashlib.sha256(encoded).hexdigest()
    return payload
