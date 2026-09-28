"""Deterministic semantic audit for every active predictive RuleBox contract."""

from __future__ import annotations

from typing import Dict, Iterable, List

from .hypothesis_catalog import hypothesis_family_definition
from .ontology_rulebox_contracts import GraphInferenceRule
from .statistical_signals.registry import signal_hypothesis_family


HYPOTHESIS_SEMANTIC_AUDIT_VERSION = "hypothesis-semantic-audit-v1"


def _model_signal_rows(rule: GraphInferenceRule) -> List[Dict[str, object]]:
    return [
        condition.to_dict()
        for condition in rule.conditions or []
        if str(condition.relation_type or "").upper() == "HAS_MODEL_SIGNAL"
    ]


def _row(rule: GraphInferenceRule) -> Dict[str, object]:
    basis = rule.resolved_knowledge_basis
    claim = rule.resolved_claim_contract
    signals = _model_signal_rows(rule)
    signal_types = sorted({
        str((item.get("target_property_filters") or {}).get("signalType") or "")
        for item in signals
        if str((item.get("target_property_filters") or {}).get("signalType") or "")
    })
    signal_families = sorted({signal_hypothesis_family(item) for item in signal_types})
    signal_families = [item for item in signal_families if item]
    family = hypothesis_family_definition(basis.thesis_family)
    criteria = list(claim.outcome_contract.criteria or [])
    issues = []
    if len(signals) != 1:
        issues.append("exactly-one-model-signal-required")
    if len(signal_families) != 1:
        issues.append("one-signal-hypothesis-family-required")
    elif signal_families[0] != basis.thesis_family:
        issues.append("signal-and-rule-family-mismatch")
    if not family:
        issues.append("unknown-hypothesis-family")
    else:
        expected = {
            "theory": family.theory_family,
            "target": family.prediction_target,
            "direction": family.expected_direction,
            "outcome": family.expected_outcome,
            "metric": family.outcome_metric,
            "falsification": family.falsification_contract,
        }
        actual = {
            "theory": claim.theory_family,
            "target": claim.prediction_target,
            "direction": claim.expected_direction,
            "outcome": claim.expected_outcome,
            "metric": claim.outcome_metric,
            "falsification": claim.falsification_contract,
        }
        if actual != expected:
            issues.append("claim-and-family-contract-mismatch")
    roles = {item.role for item in criteria if item.required}
    if "result" not in roles or "invalidation" not in roles:
        issues.append("result-and-invalidation-required")
    if basis.thesis_family in {
        "fundamental-rerating", "fundamental-deterioration",
        "valuation-convergence", "valuation-divergence",
    }:
        gap = [item for item in criteria if item.metric == "valuationGapReductionPp" and item.required]
        if len(gap) != 1 or "valuation" not in claim.outcome_contract.required_observation_domains:
            issues.append("comparable-valuation-gap-measurement-required")
    return {
        "ruleId": rule.rule_id,
        "version": rule.version,
        "label": rule.label,
        "signalTypes": signal_types,
        "thesisFamily": basis.thesis_family,
        "predictionTarget": claim.prediction_target,
        "expectedDirection": claim.expected_direction,
        "expectedOutcome": claim.expected_outcome,
        "outcomeMetric": claim.outcome_metric,
        "outcomeHorizons": list(claim.outcome_contract.outcome_horizon_minutes or []),
        "falsificationContract": claim.falsification_contract,
        "requiredObservationDomains": list(claim.outcome_contract.required_observation_domains or []),
        "criterionMetrics": sorted({item.metric for item in criteria}),
        "issues": sorted(set(issues)),
    }


def audit_active_predictive_rules(rules: Iterable[GraphInferenceRule]) -> Dict[str, object]:
    """Compare condition evidence, claim meaning and outcome contract for all active predictions."""

    candidates = [
        rule
        for rule in rules or []
        if rule.enabled
        and rule.source_kind == "stock"
        and rule.resolved_knowledge_basis.rule_kind == "predictive-hypothesis"
    ]
    rows = [_row(rule) for rule in sorted(candidates, key=lambda item: item.rule_id)]
    issues = [
        {"ruleId": row["ruleId"], "issues": list(row["issues"])}
        for row in rows
        if row["issues"]
    ]
    families: Dict[str, int] = {}
    for row in rows:
        families[row["thesisFamily"]] = families.get(row["thesisFamily"], 0) + 1
    return {
        "version": HYPOTHESIS_SEMANTIC_AUDIT_VERSION,
        "activePredictiveRuleCount": len(rows),
        "alignedRuleCount": len(rows) - len(issues),
        "issueRuleCount": len(issues),
        "familyCounts": dict(sorted(families.items())),
        "issues": issues,
        "rules": rows,
    }
