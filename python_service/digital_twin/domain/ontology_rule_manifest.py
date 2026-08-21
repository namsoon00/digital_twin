"""Governed, question-scoped metadata for every executable TypeDB rule."""

import hashlib
import json

from typing import Dict, Iterable, List

from .ontology_change_impact import rule_dependency_profile
from .ontology_rule_execution_policy import rule_execution_profile
from .ontology_rule_knowledge import resolved_rule_knowledge_basis
from .statistical_signals.rule_contracts import (
    rule_statistical_signal_contract,
    statistical_signal_reverse_index,
)


ONTOLOGY_RULE_MANIFEST_VERSION = "ontology-rule-domain-manifest-v5"
RULE_DEPENDENCY_CONTRACT_VERSION = "ontology-rule-dependency-contract-v2"
RULE_DEPENDENCY_INDEX_VERSION = "ontology-rule-dependency-index-v1"

ASSESSMENT_SCOPES = (
    "evidence-quality",
    "investment-opinion",
    "portfolio-fit",
    "execution-readiness",
)

EVENT_FAMILY_TOKENS = {
    "disclosure", "event", "evidence", "filing", "macro", "news",
    "rate", "research",
}
HOT_FAMILY_TOKENS = {
    "flow", "market", "position", "price", "state", "temporal", "trend",
}
EXECUTION_FAMILY_TOKENS = {
    "capacity", "execution", "liquidity", "orderbook", "slippage",
}
PORTFOLIO_FAMILY_TOKENS = {
    "allocation", "concentration", "portfolio", "rebalance",
}
QUALITY_FAMILY_TOKENS = {
    "conflict", "coverage", "freshness", "missing", "provenance", "quality",
}


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
    if action_group.startswith("execution"):
        return "trade-execution"
    if action_group == "rebalance" or values.intersection({"allocation", "concentration", "portfolio", "rebalance"}):
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
        "trade-execution": ["execution-readiness", "holding-action", "watchlist-entry"],
    }.get(module, ["holding-action", "watchlist-entry"])
    if action_group and action_group not in {"holding", "watchlist"}:
        values.append(action_group)
    return list(dict.fromkeys(values))


def _family_tokens(families: Iterable[str]) -> set:
    tokens = set()
    for family in families or []:
        clean = str(family or "").strip().lower().replace("_", "-")
        if not clean:
            continue
        tokens.add(clean)
        tokens.update(part for part in clean.split("-") if part)
    return tokens


def rule_assessment_scope(
    rule: object,
    families: Iterable[str],
    module: str = "",
    dependency: Dict[str, object] = None,
) -> str:
    """Return the owned decision area without evaluating investment facts."""

    rule_id = str(_value(rule, "rule_id", "ruleId") or "").strip()
    if rule_id:
        rule_kind = resolved_rule_knowledge_basis(rule).rule_kind
        governed_scope = {
            "data-quality-gate": "evidence-quality",
            "execution-gate": "execution-readiness",
            "policy-constraint": "portfolio-fit",
            "predictive-hypothesis": "investment-opinion",
        }.get(rule_kind)
        if governed_scope:
            return governed_scope

    tokens = _family_tokens(families)
    action_group = str(_value(rule, "action_group", "actionGroup") or "").strip().lower()
    module = str(module or "").strip().lower()
    dependency_keys = {
        str(value or "").strip().lower()
        for value in (dependency or {}).get("dependencyKeys") or []
    }
    derivations = _items(_value(rule, "derivations"))
    blocks_account_action = any(
        _items(_value(item, "blocked_actions", "blockedActions"))
        for item in derivations
    )
    quality_only = bool(tokens) and tokens.issubset(QUALITY_FAMILY_TOKENS)
    if quality_only or action_group in {"dataquality", "quality"}:
        return "evidence-quality"
    if (
        tokens & EXECUTION_FAMILY_TOKENS
        or module == "trade-execution"
        or action_group.startswith("execution")
    ):
        return "execution-readiness"
    if (
        tokens & PORTFOLIO_FAMILY_TOKENS
        or module == "allocation-rebalance"
        or action_group in {"allocation", "rebalance"}
        or ("relation:has-portfolio-state" in dependency_keys and blocks_account_action)
    ):
        return "portfolio-fit"
    return "investment-opinion"


def rule_lifecycle_class(families: Iterable[str], execution: Dict[str, object]) -> str:
    tokens = _family_tokens(families)
    if tokens & EVENT_FAMILY_TOKENS:
        return "event-driven"
    if tokens & HOT_FAMILY_TOKENS or str(execution.get("executionStage") or "") == "critical":
        return "hot"
    return "cold"


def rule_evidence_families(dependency: Dict[str, object]) -> List[str]:
    result = []
    for condition in dependency.get("conditionProfiles") or []:
        for family in condition.get("scopeFamilies") or []:
            clean = str(family or "").strip()
            if clean and clean not in result and clean != "unknown":
                result.append(clean)
    return sorted(result)


def rule_condition_contracts(dependency: Dict[str, object]) -> List[Dict[str, object]]:
    """Expose condition inputs without copying thresholds into the scheduler.

    The scheduler may use dependency identities to select TypeDB functions,
    while TypeDB remains responsible for evaluating operators and values.
    Every condition is retained as context because an unchanged news,
    governance, or valuation fact can still explain a fresh price result.
    """

    rows = []
    for item in dependency.get("conditionProfiles") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "required").strip().lower()
        rows.append({
            "conditionId": str(item.get("conditionId") or "").strip(),
            "role": role,
            "scopeFamilies": list(item.get("scopeFamilies") or []),
            "dependencyKeys": list(item.get("dependencyKeys") or []),
            "conditionKind": str(item.get("conditionKind") or "").strip(),
            "field": str(item.get("field") or "").strip(),
            "relationType": str(item.get("relationType") or "").strip(),
            "targetKind": str(item.get("targetKind") or "").strip(),
            "conservative": bool(item.get("conservative")),
            "canTriggerEvaluation": bool(item.get("canTriggerEvaluation", True)),
            "canInvalidatePriorResult": bool(item.get("canInvalidatePriorResult", True)),
            "contextOnly": bool(item.get("contextOnly")),
        })
    return rows


def rule_derived_outputs(rule: object) -> List[Dict[str, object]]:
    rows = []
    for item in _items(_value(rule, "derivations")):
        relation_type = str(_value(item, "relation_type", "relationType") or "").strip()
        target_kind = str(_value(item, "target_kind", "targetKind") or "").strip()
        rows.append({
            "relationType": relation_type,
            "targetKind": target_kind,
            "tboxClass": str(_value(item, "tbox_class", "tboxClass") or "").strip(),
            "decisionStage": str(_value(item, "decision_stage", "decisionStage") or "").strip(),
            "decisionEffect": str(_value(item, "decision_effect", "decisionEffect") or "").strip(),
            "dependencyKey": (
                "relation:" + relation_type.lower().replace("_", "-")
                if relation_type
                else ""
            ),
        })
    return rows


def rule_invalidation_contract(
    condition_contracts: Iterable[Dict[str, object]],
    lifecycle: Dict[str, object],
) -> Dict[str, object]:
    condition_rows = [dict(item) for item in condition_contracts or []]
    return {
        "mode": str(lifecycle.get("invalidationMode") or "typedb-rule-not-materialized"),
        "conditionIds": sorted({
            str(item.get("conditionId") or "").strip()
            for item in condition_rows
            if str(item.get("conditionId") or "").strip()
        } | {
            str(item or "").strip()
            for item in lifecycle.get("invalidationConditionIds") or []
            if str(item or "").strip()
        }),
        "dependencyKeys": sorted({
            str(key or "").strip()
            for item in condition_rows
            for key in item.get("dependencyKeys") or []
            if str(key or "").strip()
        }),
        "freshnessDomains": list(lifecycle.get("requiredFreshnessDomains") or []),
        "expiresAfterMinutes": int(lifecycle.get("validityMinutes") or 0),
    }


def assessment_output_contract(scope: str, effects: Iterable[str]) -> Dict[str, object]:
    output_type = {
        "evidence-quality": "EvidenceQualityAssessment",
        "investment-opinion": "InvestmentOpinionAssessment",
        "portfolio-fit": "PortfolioFitAssessment",
        "execution-readiness": "ExecutionReadinessAssessment",
    }[scope]
    cross_scope_effects = {
        "evidence-quality": ["may-block-judgement"],
        "investment-opinion": ["proposes-investment-action"],
        "portfolio-fit": ["may-constrain-position-size", "never-rewrites-investment-opinion"],
        "execution-readiness": ["may-constrain-or-block-execution", "never-rewrites-investment-opinion"],
    }[scope]
    return {
        "type": output_type,
        "assessmentScope": scope,
        "decisionEffects": sorted({
            str(value or "").strip()
            for value in effects or []
            if str(value or "").strip()
        }),
        "crossScopeEffects": cross_scope_effects,
    }


def rule_domain_manifest(
    rule: object,
    dependency: Dict[str, object] = None,
    execution: Dict[str, object] = None,
) -> Dict[str, object]:
    dependency = dependency or rule_dependency_profile(rule)
    execution = execution or rule_execution_profile(rule)
    families = list(dependency.get("scopeFamilies") or [])
    module = rule_domain_module(rule, families)
    assessment_scope = rule_assessment_scope(rule, families, module, dependency)
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
    knowledge_basis = resolved_rule_knowledge_basis(rule).to_dict()
    statistical_signal_contract = rule_statistical_signal_contract(rule)
    condition_contracts = rule_condition_contracts(dependency)
    invalidation_contract = rule_invalidation_contract(condition_contracts, lifecycle_payload)
    derived_outputs = rule_derived_outputs(rule)
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
        "assessmentScope": assessment_scope,
        "questionTypes": rule_question_types(rule, module),
        "inputFactFamilies": families,
        "triggerFamilies": [value for value in families if value != "unknown"],
        "triggerDependencies": condition_contracts,
        "evidenceFamilies": rule_evidence_families(dependency),
        "requiredFacts": list(dependency.get("dependencyKeys") or []),
        "dependencyKeys": list(dependency.get("dependencyKeys") or []),
        "requiredContext": condition_contracts,
        "contextCompletenessPolicy": {
            "aboxReadMode": "complete-active-world",
            "ruleExecutionMode": "dependency-selected-single-pass",
            "retainUnchangedFacts": True,
            "retainPriorValidInferences": True,
        },
        "invalidationContract": invalidation_contract,
        "derivedOutputs": derived_outputs,
        "dependencyContractVersion": RULE_DEPENDENCY_CONTRACT_VERSION,
        "requiredFreshness": list(lifecycle_payload.get("requiredFreshnessDomains") or []),
        "requiredProvenance": ["source", "observedAt"],
        "policyKeys": policy_keys,
        "worldScope": world_scope,
        "decisionStages": stages,
        "decisionEffects": effects,
        "outputContract": assessment_output_contract(assessment_scope, effects),
        "conflictGroup": str(_value(rule, "action_group", "actionGroup") or module),
        "outcomeContract": dict(lifecycle_payload.get("outcomeContract") or {}),
        "knowledgeBasis": knowledge_basis,
        "ruleKind": knowledge_basis.get("ruleKind"),
        "theoryFamily": knowledge_basis.get("theoryFamily"),
        "thesisFamily": knowledge_basis.get("thesisFamily"),
        "decisionEligibility": knowledge_basis.get("decisionEligibility"),
        "requiresHypothesis": bool(knowledge_basis.get("requiresHypothesis")),
        "statisticalSignalContract": statistical_signal_contract,
        "executionStage": execution.get("executionStage"),
        "failurePolicy": execution.get("failurePolicy"),
        "costHint": execution.get("costHint"),
        "costScore": execution.get("costScore"),
        "lifecycleClass": rule_lifecycle_class(families, execution),
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
        or item.get("assessmentScope") not in ASSESSMENT_SCOPES
        or not item.get("outputContract")
        or not item.get("triggerDependencies")
        or not item.get("requiredContext")
        or not item.get("invalidationContract")
        or not item.get("derivedOutputs")
        or item.get("dependencyContractVersion") != RULE_DEPENDENCY_CONTRACT_VERSION
        or not item.get("knowledgeBasis")
        or not item.get("ruleKind")
        or not item.get("theoryFamily")
        or (
            item.get("ruleKind") == "predictive-hypothesis"
            and not (item.get("statisticalSignalContract") or {}).get("signalTypes")
        )
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


def rule_dependency_reverse_index(rules: Iterable[object]) -> Dict[str, object]:
    """Compile immutable change-routing lookups for one RuleBox release.

    The index selects TypeDB functions; it never evaluates a condition. Full
    active ABox context remains available to every selected function.
    """

    manifests = [
        dict(getattr(rule, "resolved_domain_manifest"))
        if hasattr(rule, "resolved_domain_manifest")
        else rule_domain_manifest(rule)
        for rule in rules or []
    ]
    indexes = {
        "triggerByDependencyKey": {},
        "invalidationByDependencyKey": {},
        "contextByDependencyKey": {},
        "triggerByFamily": {},
        "invalidationByFamily": {},
        "contextByFamily": {},
    }

    def add(index_name: str, key: object, rule_id: str) -> None:
        clean_key = str(key or "").strip()
        if not clean_key or not rule_id:
            return
        values = indexes[index_name].setdefault(clean_key, [])
        if rule_id not in values:
            values.append(rule_id)

    for manifest in manifests:
        rule_id = str(manifest.get("ruleId") or "").strip()
        for condition in manifest.get("requiredContext") or []:
            dependency_keys = list(condition.get("dependencyKeys") or [])
            families = list(condition.get("scopeFamilies") or [])
            for key in dependency_keys:
                add("contextByDependencyKey", key, rule_id)
            for family in families:
                add("contextByFamily", family, rule_id)
            if bool(condition.get("canTriggerEvaluation", True)):
                for key in dependency_keys:
                    add("triggerByDependencyKey", key, rule_id)
                for family in families:
                    add("triggerByFamily", family, rule_id)
            if bool(condition.get("canInvalidatePriorResult", True)):
                for key in dependency_keys:
                    add("invalidationByDependencyKey", key, rule_id)
                for family in families:
                    add("invalidationByFamily", family, rule_id)
    for values in indexes.values():
        for key in list(values):
            values[key] = sorted(values[key])
    statistical_signals = statistical_signal_reverse_index(manifests)
    # Statistical migration metadata is governance-only. It must not change
    # the executable dependency fingerprint until a candidate rule is
    # explicitly promoted into the RuleBox.
    fingerprint_payload = {
        "version": RULE_DEPENDENCY_INDEX_VERSION,
        "manifestVersion": ONTOLOGY_RULE_MANIFEST_VERSION,
        "dependencyContractVersion": RULE_DEPENDENCY_CONTRACT_VERSION,
        "indexes": indexes,
    }
    fingerprint = hashlib.sha256(json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        **fingerprint_payload,
        **indexes,
        "statisticalSignals": statistical_signals,
        "ruleCount": len(manifests),
        "fingerprint": fingerprint,
    }
