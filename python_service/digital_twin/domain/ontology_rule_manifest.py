"""Governed, question-scoped metadata for every executable TypeDB rule."""

from typing import Dict, Iterable, List

from .ontology_change_impact import rule_dependency_profile
from .ontology_rule_execution_policy import rule_execution_profile


ONTOLOGY_RULE_MANIFEST_VERSION = "ontology-rule-domain-manifest-v1"


def _value(subject: object, *names: str):
    if isinstance(subject, dict):
        for name in names:
            if name in subject:
                return subject.get(name)
        return None
    for name in names:
        if hasattr(subject, name):
            return getattr(subject, name)
    return None


def _items(value: object) -> List[object]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def rule_domain_module(rule: object, families: Iterable[str]) -> str:
    action_group = str(_value(rule, "action_group", "actionGroup") or "").lower()
    values = {str(item or "").lower() for item in families or []}
    if action_group == "rebalance" or "portfolio" in values or "exposure" in values:
        return "allocation-rebalance"
    if "evidence" in values:
        return "research-evidence"
    if any(value.startswith("macro") for value in values):
        return "market-observation"
    if "quality" in values:
        return "market-observation"
    if action_group in {"risk", "reduce", "exit", "loss"}:
        return "risk-exposure"
    return "decision-intelligence"


def rule_question_types(rule: object, module: str) -> List[str]:
    action_group = str(_value(rule, "action_group", "actionGroup") or "").lower()
    values = {
        "allocation-rebalance": ["portfolio-rebalance"],
        "risk-exposure": ["position-risk", "holding-action"],
        "research-evidence": ["event-impact", "holding-action", "watchlist-entry"],
        "market-observation": ["market-context", "holding-action", "watchlist-entry"],
    }.get(module, ["holding-action", "watchlist-entry"])
    if action_group and action_group not in {"holding", "watchlist"}:
        values.append(action_group)
    return list(dict.fromkeys(values))


def rule_domain_manifest(
    rule: object,
    dependency: Dict[str, object] = None,
    execution: Dict[str, object] = None,
) -> Dict[str, object]:
    dependency = dependency or rule_dependency_profile(rule)
    execution = execution or rule_execution_profile(rule)
    families = list(dependency.get("scopeFamilies") or [])
    module = rule_domain_module(rule, families)
    derivations = _items(_value(rule, "derivations"))
    stages = sorted({
        str(_value(item, "decision_stage", "decisionStage") or "").strip()
        for item in derivations
        if str(_value(item, "decision_stage", "decisionStage") or "").strip()
    })
    effects = sorted({
        str(_value(item, "decision_effect", "decisionEffect") or "").strip()
        for item in derivations
        if str(_value(item, "decision_effect", "decisionEffect") or "").strip()
    })
    lifecycle = _value(rule, "hypothesis_lifecycle", "hypothesisLifecycle")
    lifecycle_payload = lifecycle.to_dict() if hasattr(lifecycle, "to_dict") else dict(lifecycle or {}) if isinstance(lifecycle, dict) else {}
    policy_keys = []
    rule_id = str(_value(rule, "rule_id", "ruleId") or "")
    if rule_id == "graph.portfolio.concentration.review.v1":
        policy_keys = ["maxPositionWeightPct", "maxSectorWeightPct", "fxExposureReviewPct"]
    world_scope = "mixed" if any(value.startswith("macro") for value in families) else "portfolio"
    if families and all(value.startswith("macro") for value in families):
        world_scope = "market"
    return {
        "version": ONTOLOGY_RULE_MANIFEST_VERSION,
        "ruleId": rule_id,
        "module": module,
        "questionTypes": rule_question_types(rule, module),
        "inputFactFamilies": families,
        "dependencyKeys": list(dependency.get("dependencyKeys") or []),
        "requiredFreshness": list(lifecycle_payload.get("requiredFreshnessDomains") or []),
        "requiredProvenance": ["source", "observedAt"],
        "policyKeys": policy_keys,
        "worldScope": world_scope,
        "decisionStages": stages,
        "decisionEffects": effects,
        "conflictGroup": str(_value(rule, "action_group", "actionGroup") or module),
        "outcomeContract": dict(lifecycle_payload.get("outcomeContract") or {}),
        "executionStage": execution.get("executionStage"),
        "failurePolicy": execution.get("failurePolicy"),
        "costHint": execution.get("costHint"),
        "costScore": execution.get("costScore"),
        "conservativeRouting": bool(dependency.get("conservative")),
        "status": "active" if bool(_value(rule, "enabled") is not False) else "disabled",
    }


def validate_rule_domain_manifests(rules: Iterable[object]) -> Dict[str, object]:
    manifests = [
        dict(getattr(rule, "resolved_domain_manifest"))
        if hasattr(rule, "resolved_domain_manifest")
        else rule_domain_manifest(rule)
        for rule in rules or []
    ]
    invalid = [
        item["ruleId"]
        for item in manifests
        if not item.get("ruleId")
        or not item.get("module")
        or not item.get("questionTypes")
        or not item.get("decisionStages")
    ]
    conservative = [item["ruleId"] for item in manifests if item.get("conservativeRouting")]
    return {
        "version": ONTOLOGY_RULE_MANIFEST_VERSION,
        "valid": not invalid,
        "ruleCount": len(manifests),
        "invalidRuleIds": invalid,
        "conservativeRuleIds": conservative,
        "manifests": manifests,
    }
