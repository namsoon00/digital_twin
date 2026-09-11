"""Operational execution policy derived from editable RuleBox semantics.

TypeDB remains responsible for deciding whether a rule matches.  This module
only orders rule execution and defines whether a failed, support-only rule may
leave a visible coverage gap instead of invalidating completed core reads.
"""

from math import comb
from typing import Dict, Iterable, List


RULE_EXECUTION_POLICY_VERSION = "rule-execution-policy-v1"
RULE_EXECUTION_STAGES = ("critical", "core", "supporting")
RULE_FAILURE_POLICIES = ("invalidate-generation", "preserve-core-with-gap")
RULE_COST_HINTS = ("low", "medium", "high")


def _value(subject: object, *names: str) -> object:
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
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def _normalized_values(values: Iterable[object]) -> set:
    return {
        str(value or "").strip().lower()
        for value in values
        if str(value or "").strip()
    }


def _rule_derivations(rule: object) -> List[object]:
    return _items(_value(rule, "derivations"))


def _rule_conditions(rule: object) -> List[object]:
    return _items(_value(rule, "conditions"))


def rule_execution_cost_profile(rule: object) -> Dict[str, object]:
    """Estimate relative query cost from RuleBox shape, never from outcomes."""

    conditions = _rule_conditions(rule)
    relation_count = sum(
        1
        for condition in conditions
        if str(_value(condition, "kind") or "").strip().lower() == "relation"
    )
    any_count = sum(
        1
        for condition in conditions
        if str(_value(condition, "role") or "required").strip().lower()
        in {"any", "optional"}
    )
    try:
        any_minimum = max(
            1,
            int(
                _value(rule, "any_condition_min_count", "anyConditionMinCount")
                or 1
            ),
        )
    except (TypeError, ValueError):
        any_minimum = 1
    combination_count = 0
    if any_count:
        combination_count = comb(any_count, min(any_count, any_minimum))
    score = len(conditions) + (relation_count * 2) + (min(64, combination_count) * 3)
    derived_hint = "high" if score >= 18 else "medium" if score >= 8 else "low"
    explicit_hint = str(
        _value(rule, "cost_hint", "costHint", "execution_cost_hint", "executionCostHint")
        or ""
    ).strip().lower()
    return {
        "costHint": explicit_hint if explicit_hint in RULE_COST_HINTS else derived_hint,
        "costScore": score,
        "conditionCount": len(conditions),
        "relationConditionCount": relation_count,
        "anyConditionCount": any_count,
        "anyConditionMinimum": any_minimum,
        "profileSource": "authored" if explicit_hint in RULE_COST_HINTS else "derived",
    }


def rule_execution_profile(rule: object) -> Dict[str, object]:
    """Resolve a conservative execution profile from RuleBox-owned fields."""

    derivations = _rule_derivations(rule)
    effects = _normalized_values(
        _value(derivation, "decision_effect", "decisionEffect")
        for derivation in derivations
    )
    polarities = _normalized_values(
        _value(derivation, "polarity") for derivation in derivations
    )
    severities = _normalized_values(
        _value(derivation, "notification_severity", "notificationSeverity")
        for derivation in derivations
    )
    action_levels = _normalized_values(
        [
            _value(rule, "action_level", "actionLevel"),
            *[
                _value(derivation, "action_level", "actionLevel")
                for derivation in derivations
            ],
        ]
    )
    blocked_actions = {
        str(action or "").strip().upper()
        for derivation in derivations
        for action in _items(_value(derivation, "blocked_actions", "blockedActions"))
        if str(action or "").strip()
    }
    explicit_stage = str(
        _value(rule, "execution_stage", "executionStage", "execution_stage_override", "executionStageOverride")
        or ""
    ).strip().lower()
    safety_critical = bool(
        effects & {"block", "constrain"}
        or "alert" in severities
        or "action" in action_levels
        or blocked_actions
    )
    support_only = bool(derivations) and effects == {"support"} and not safety_critical
    derived_stage = (
        "critical"
        if safety_critical
        else "supporting"
        if support_only
        else "core"
    )
    execution_stage = (
        explicit_stage if explicit_stage in RULE_EXECUTION_STAGES else derived_stage
    )

    explicit_failure_policy = str(
        _value(rule, "failure_policy", "failurePolicy", "failure_policy_override", "failurePolicyOverride")
        or ""
    ).strip().lower()
    requested_failure_policy = (
        explicit_failure_policy
        if explicit_failure_policy in RULE_FAILURE_POLICIES
        else "preserve-core-with-gap"
        if execution_stage == "supporting"
        else "invalidate-generation"
    )
    # An override can make scheduling more conservative, but it cannot permit
    # a risk, action, block, constrain, or defer rule to fail open.
    failure_policy = (
        "preserve-core-with-gap"
        if requested_failure_policy == "preserve-core-with-gap" and support_only
        else "invalidate-generation"
    )
    validation_warnings = []
    if (
        requested_failure_policy == "preserve-core-with-gap"
        and failure_policy != requested_failure_policy
    ):
        validation_warnings.append("unsafe-preserve-core-override-rejected")
    if explicit_stage == "supporting" and not support_only:
        validation_warnings.append("supporting-stage-has-non-support-effects")

    cost = rule_execution_cost_profile(rule)
    return {
        "version": RULE_EXECUTION_POLICY_VERSION,
        "executionStage": execution_stage,
        "failurePolicy": failure_policy,
        "costHint": cost["costHint"],
        "costScore": cost["costScore"],
        "conditionCount": cost["conditionCount"],
        "relationConditionCount": cost["relationConditionCount"],
        "anyConditionCount": cost["anyConditionCount"],
        "anyConditionMinimum": cost["anyConditionMinimum"],
        "decisionEffects": sorted(effects),
        "polarities": sorted(polarities),
        "notificationSeverities": sorted(severities),
        "supportOnly": support_only,
        "profileSource": "authored" if explicit_stage in RULE_EXECUTION_STAGES else "derived",
        "validationWarnings": validation_warnings,
    }
