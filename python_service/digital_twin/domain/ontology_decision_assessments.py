"""Independent decision-area read models over TypeDB InferenceBox results.

This module never evaluates prices, thresholds, or RuleBox conditions. It
groups already-materialized TypeDB relations by their governed assessment
scope and composes operational constraints around the investment opinion.
"""

from collections import Counter
from typing import Dict, Iterable, List, Mapping

from .ontology_decision_state import decision_effect_from_relation, semantic_relation_sort_key
from .ontology_rule_manifest import ASSESSMENT_SCOPES, rule_assessment_scope


DECISION_ASSESSMENT_BUNDLE_VERSION = "typedb-decision-assessment-bundle-v3"


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _values(subject: object, snake: str, camel: str = "") -> object:
    if isinstance(subject, Mapping):
        if snake in subject:
            return subject.get(snake)
        return subject.get(camel or snake)
    return getattr(subject, snake, None)


def _strings(values: Iterable[object], limit: int = 12) -> List[str]:
    result = []
    for value in values or []:
        clean = _text(value)
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _relation_for_match(match: object, relations: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    rule_id = _text(_values(match, "rule_id", "ruleId"))
    relation_type = _text(_values(match, "relation_type", "relationType"))
    exact = next((
        item for item in relations or []
        if _text(item.get("ruleId")) == rule_id
        and _text(item.get("type") or item.get("relationType")) == relation_type
    ), None)
    if exact:
        return dict(exact)
    return dict(next((item for item in relations or [] if _text(item.get("ruleId")) == rule_id), {}) or {})


def relation_assessment_scope(relation: Mapping[str, object], match: object = None) -> str:
    knowledge_basis = _mapping(
        relation.get("knowledgeBasis") or relation.get("knowledge_basis")
    )
    if not knowledge_basis:
        knowledge_basis = _mapping(_values(match, "knowledge_basis", "knowledgeBasis"))
    governed_scope = {
        "data-quality-gate": "evidence-quality",
        "execution-gate": "execution-readiness",
        "policy-constraint": "portfolio-fit",
        "predictive-hypothesis": "investment-opinion",
        "context-observation": "market-context",
    }.get(_text(knowledge_basis.get("ruleKind") or knowledge_basis.get("rule_kind")))
    if governed_scope:
        return governed_scope
    explicit = _text(relation.get("assessmentScope") or relation.get("assessment_scope"))
    if not explicit:
        explicit = _text(_values(match, "assessment_scope", "assessmentScope"))
    if explicit in ASSESSMENT_SCOPES:
        return explicit
    families = (
        relation.get("ruleScopeFamilies")
        or relation.get("rule_scope_families")
        or _values(match, "rule_scope_families", "ruleScopeFamilies")
        or []
    )
    action_group = (
        relation.get("actionGroup")
        or relation.get("action_group")
        or _values(match, "action_group", "actionGroup")
    )
    return rule_assessment_scope({"actionGroup": action_group}, families)


def _entry(match: object, relation: Mapping[str, object]) -> Dict[str, object]:
    evidence_state = _mapping(_values(match, "evidence_state", "evidenceState"))
    eligibility = _text(evidence_state.get("inferenceEligibilityStatus") or "eligible")
    effect = decision_effect_from_relation(dict(relation)) or _text(
        _values(match, "decision_effect", "decisionEffect")
    ).lower()
    return {
        "ruleId": _text(_values(match, "rule_id", "ruleId")),
        "label": _text(_values(match, "label")),
        "relationType": _text(relation.get("type") or relation.get("relationType")),
        "decisionStage": _text(relation.get("decisionStage") or _values(match, "decision_stage", "decisionStage")),
        "decisionEffect": effect,
        "candidateAction": _text(
            relation.get("candidateAction")
            or _values(match, "candidate_action", "candidateAction")
        ).upper(),
        "candidateActionLabel": _text(
            relation.get("candidateActionLabel")
            or _values(match, "candidate_action_label", "candidateActionLabel")
        ),
        "evidenceRole": _text(_values(match, "evidence_role", "evidenceRole")),
        "dataState": _text(_values(match, "data_state", "dataState")),
        "judgementBlocked": bool(evidence_state.get("judgementBlocked")),
        "inferenceEligibilityStatus": eligibility,
        "inferenceEligibilityReason": _text(evidence_state.get("inferenceEligibilityReason")),
        "evidenceUsableForJudgement": evidence_state.get("evidenceUsableForJudgement") is not False,
        "freshnessStatus": _text(evidence_state.get("freshnessStatus") or "unknown"),
        "nextChecks": list(_values(match, "next_checks", "nextChecks") or []),
        "invalidationConditions": list(_values(match, "weaken_conditions", "weakenConditions") or []),
        "strengthenConditions": list(_values(match, "strengthen_conditions", "strengthenConditions") or []),
        "relation": dict(relation),
    }


def _assessment(scope: str, entries: List[Dict[str, object]]) -> Dict[str, object]:
    effect_counts = Counter(_text(item.get("decisionEffect")) for item in entries if _text(item.get("decisionEffect")))
    opinion_entries = entries
    if scope == "investment-opinion":
        supported_entries = [
            item for item in entries
            if _text(item.get("decisionEffect")) == "support"
        ]
        opinion_entries = supported_entries or entries
    candidate_actions = _strings(
        _text(item.get("candidateAction")).upper()
        for item in opinion_entries
        if _text(item.get("candidateAction"))
    ) if scope == "investment-opinion" else []
    action_conflict = scope == "investment-opinion" and len(candidate_actions) > 1
    blocked = (
        bool(effect_counts.get("block"))
        or any(item.get("judgementBlocked") for item in entries)
        or action_conflict
    )
    if not entries:
        status = "not-evaluated"
    elif action_conflict:
        status = "conflicted"
    elif blocked:
        status = "blocked"
    elif effect_counts.get("constrain"):
        status = "constrained"
    elif effect_counts.get("defer") and not effect_counts.get("support"):
        status = "deferred"
    elif effect_counts.get("support"):
        status = "supported"
    else:
        status = "observed"
    selectable_entries = entries
    if scope == "investment-opinion":
        selectable_entries = [
            item
            for item in opinion_entries
            if candidate_actions
            and _text(item.get("candidateAction")).upper() == candidate_actions[0]
        ] if not action_conflict else []
    selected = (
        min(selectable_entries, key=lambda item: semantic_relation_sort_key(item["relation"]))
        if selectable_entries else {}
    )
    public_entries = [{key: value for key, value in item.items() if key != "relation"} for item in entries]
    return {
        "assessmentScope": scope,
        "status": status,
        "authoritativeSource": "typedb-materialized-rule-relations",
        "ruleIds": _strings(item.get("ruleId") for item in entries),
        "selectedRuleId": _text(selected.get("ruleId")),
        "candidateAction": _text(selected.get("candidateAction")) if scope == "investment-opinion" else "",
        "candidateActionLabel": _text(selected.get("candidateActionLabel")) if scope == "investment-opinion" else "",
        "candidateActions": candidate_actions,
        "candidateRuleIdsByAction": {
            action: _strings(
                item.get("ruleId")
                for item in opinion_entries
                if _text(item.get("candidateAction")).upper() == action
            )
            for action in candidate_actions
        },
        "actionConflict": action_conflict,
        "conflictReason": (
            "multiple-typedb-investment-actions-without-unique-selection"
            if action_conflict else ""
        ),
        "decisionEffectCounts": dict(sorted(effect_counts.items())),
        "judgementBlocked": blocked,
        "entries": public_entries[:12],
    }


def _monitoring_plan(assessments: Mapping[str, Mapping[str, object]]) -> Dict[str, object]:
    entries = [
        item
        for assessment in assessments.values()
        for item in assessment.get("entries") or []
        if isinstance(item, Mapping)
    ]
    return {
        "type": "MonitoringPlan",
        "nextChecks": _strings(
            (check for item in entries for check in item.get("nextChecks") or []), 10
        ),
        "invalidationConditions": _strings(
            (check for item in entries for check in item.get("invalidationConditions") or []), 10
        ),
        "strengthenConditions": _strings(
            (check for item in entries for check in item.get("strengthenConditions") or []), 10
        ),
    }


def _recommended_plan(assessments: Mapping[str, Mapping[str, object]]) -> Dict[str, object]:
    quality = assessments["evidence-quality"]
    opinion = assessments["investment-opinion"]
    portfolio = assessments["portfolio-fit"]
    execution = assessments["execution-readiness"]
    market_context = assessments.get("market-context") or {}
    opinion_action = _text(opinion.get("candidateAction")).upper()
    if opinion.get("actionConflict"):
        status = "judgement-conflicted"
        option = "resolve-opinion-conflict"
    elif quality.get("judgementBlocked"):
        status = "judgement-blocked"
        option = "wait-for-usable-evidence"
    elif not opinion_action and market_context.get("ruleIds"):
        status = "observe-context"
        option = "review-material-context"
    elif not opinion_action:
        status = "judgement-blocked"
        option = "wait-for-usable-evidence"
    elif execution.get("status") == "blocked":
        status = "execution-blocked"
        option = "defer-execution"
    elif execution.get("status") == "constrained" or portfolio.get("status") in {"blocked", "constrained"}:
        status = "constrained"
        option = "execute-with-constraints"
    elif opinion.get("status") == "deferred":
        status = "observe"
        option = "wait-for-confirmation"
    else:
        status = "ready"
        option = "execute-opinion"
    return {
        "type": "RecommendedInvestmentPlan",
        "status": status,
        "investmentAction": opinion_action,
        "investmentOpinionRuleId": _text(opinion.get("selectedRuleId")),
        "candidateActions": list(opinion.get("candidateActions") or []),
        "opinionConflict": bool(opinion.get("actionConflict")),
        "planOption": option,
        "portfolioConstraintRuleIds": list(portfolio.get("ruleIds") or []),
        "executionConstraintRuleIds": list(execution.get("ruleIds") or []),
        "marketContextRuleIds": list(market_context.get("ruleIds") or []),
        "meaningPreserved": True,
        "compositionRule": (
            "TypeDB investment opinion remains unchanged; evidence, portfolio, and execution "
            "assessments may only block, defer, or constrain its implementation."
        ),
    }


def decision_assessment_bundle(
    matches: Iterable[object],
    relations: Iterable[Mapping[str, object]],
) -> Dict[str, object]:
    """Group one aligned InferenceBox generation into independent assessments."""

    grouped = {scope: [] for scope in ASSESSMENT_SCOPES}
    excluded_entries = []
    for match in matches or []:
        if not bool(_values(match, "matched")):
            continue
        relation = _relation_for_match(match, relations)
        if not relation:
            continue
        scope = relation_assessment_scope(relation, match)
        entry = _entry(match, relation)
        if entry.get("inferenceEligibilityStatus") != "eligible":
            excluded_entries.append({
                "ruleId": entry.get("ruleId"),
                "label": entry.get("label"),
                "assessmentScope": scope,
                "reason": entry.get("inferenceEligibilityReason") or "판단 사용 조건을 충족하지 못했습니다.",
                "freshnessStatus": entry.get("freshnessStatus"),
            })
            continue
        grouped[scope].append(entry)
    assessments = {scope: _assessment(scope, grouped[scope]) for scope in ASSESSMENT_SCOPES}
    quality = dict(assessments["evidence-quality"])
    quality["excludedRuleIds"] = _strings(item.get("ruleId") for item in excluded_entries)
    quality["excludedEntries"] = excluded_entries[:12]
    if excluded_entries and quality.get("status") == "not-evaluated":
        quality["status"] = "degraded"
    assessments["evidence-quality"] = quality
    return {
        "version": DECISION_ASSESSMENT_BUNDLE_VERSION,
        "source": "typedb-inferencebox",
        "evidenceQuality": assessments["evidence-quality"],
        "investmentOpinion": assessments["investment-opinion"],
        "portfolioFit": assessments["portfolio-fit"],
        "executionReadiness": assessments["execution-readiness"],
        "marketContext": assessments["market-context"],
        "notificationDelivery": assessments["notification-delivery"],
        "recommendedPlan": _recommended_plan(assessments),
        "monitoringPlan": _monitoring_plan(assessments),
    }


def without_portfolio_assessment(bundle: Mapping[str, object]) -> Dict[str, object]:
    """Remove rebalance policy from a quote-alert view and recompose the plan."""

    scoped = dict(bundle or {})
    empty_portfolio = _assessment("portfolio-fit", [])
    scoped["portfolioFit"] = empty_portfolio
    assessments = {
        "evidence-quality": _mapping(scoped.get("evidenceQuality")),
        "investment-opinion": _mapping(scoped.get("investmentOpinion")),
        "portfolio-fit": empty_portfolio,
        "execution-readiness": _mapping(scoped.get("executionReadiness")),
        "market-context": _mapping(scoped.get("marketContext")),
        "notification-delivery": _mapping(scoped.get("notificationDelivery")),
    }
    scoped["recommendedPlan"] = _recommended_plan(assessments)
    scoped["policyScope"] = "instrument-market"
    return scoped
