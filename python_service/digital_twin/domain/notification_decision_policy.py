"""Decision-policy boundaries for instrument and portfolio notifications."""

from __future__ import annotations

from typing import Dict, Iterable, List

from .ontology_decision_assessments import without_portfolio_assessment
from .message_types import (
    MONITOR_CASH_CHANGE,
    PORTFOLIO_ACTIVITY_OBSERVATION,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    PORTFOLIO_ONTOLOGY_SIGNAL,
    PORTFOLIO_REBALANCE_REVIEW,
)


DECISION_POLICY_SCOPE_VERSION = "notification-decision-policy-scope-v1"
INSTRUMENT_MARKET_SCOPE = "instrument-market"
PORTFOLIO_REBALANCE_SCOPE = "portfolio-rebalance"

PORTFOLIO_POLICY_SOURCE_TYPES = {
    MONITOR_CASH_CHANGE,
    PORTFOLIO_ACTIVITY_OBSERVATION,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    PORTFOLIO_ONTOLOGY_SIGNAL,
    PORTFOLIO_REBALANCE_REVIEW,
}

MARKET_DECISION_STRATEGY_FIELDS = (
    "label",
    "riskTolerance",
    "timeHorizon",
    "lossTolerancePct",
    "profitProtectionPct",
    "addBuyPolicy",
    "addBuyWatchSignalMin",
    "addBuyReviewSignalMin",
    "allowLossAddBuyReview",
    "defaultHoldingRole",
    "watchlistActionPolicy",
    "holdingActionPolicy",
    "profile",
    "ontologyBox",
    "tboxClass",
    "accountId",
    "accountLabel",
)

REBALANCE_RELATION_FACT_KEYS = {
    "accountValue",
    "cashWeightPct",
    "fxExposureRatio",
    "positionAccountWeight",
    "positionWeight",
    "portfolioValue",
    "sectorRatio",
    "sectorWeight",
    "strategyFxExposureReviewPct",
    "strategyMaxPositionWeightPct",
    "strategyMaxSectorWeightPct",
    "strategyMinCashWeightPct",
    "totalAccountValue",
}

REBALANCE_RAW_LINE_LABELS = {
    "가용 현금",
    "계좌 평가금액",
    "리밸런싱",
    "섹터 비중",
    "업종 비중",
    "외화 노출",
    "외화 비중",
    "종목 비중",
    "포트폴리오 영향",
    "현금",
    "현금 비중",
}

REBALANCE_DECISION_TEXT_MARKERS = (
    "계정 한도",
    "계좌 한도",
    "목표 비중",
    "비중 한도",
    "현금 하한",
    "현금 비중",
    "외화 노출",
    "외화 비중",
    "집중도 초과",
    "리밸런싱",
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _string_values(value: object) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value if _clean(item)]
    return [_clean(value)] if _clean(value) else []


def notification_policy_source_types(context: Dict[str, object]) -> List[str]:
    """Collect structured source types without inferring scope from prose."""

    values: List[str] = []
    containers = [context]
    insight = context.get("ontologyInsight") if isinstance(context.get("ontologyInsight"), dict) else {}
    if insight:
        containers.append(insight)
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if metadata:
        containers.append(metadata)
    for container in containers:
        for key in ("messageType", "rule", "sourceSignalType", "sourceSignalTypes", "dispatchInsightType"):
            for item in _string_values(container.get(key)):
                if item not in values:
                    values.append(item)
    for item in context.get("sourceAlertEvents") or []:
        if not isinstance(item, dict):
            continue
        for key in ("rule", "messageType", "sourceSignalType"):
            value = _clean(item.get(key))
            if value and value not in values:
                values.append(value)
    return values


def notification_decision_policy_scope(context: Dict[str, object]) -> str:
    """Return the explicit policy world allowed to influence this decision."""

    explicit = _clean(context.get("decisionPolicyScope"))
    if explicit in {INSTRUMENT_MARKET_SCOPE, PORTFOLIO_REBALANCE_SCOPE}:
        return explicit
    for audit_key in ("notificationAiExecutionAudit", "notificationAiDecisionAudit"):
        audit = context.get(audit_key) if isinstance(context.get(audit_key), dict) else {}
        brief = audit.get("decisionBrief") if isinstance(audit.get("decisionBrief"), dict) else {}
        scope = brief.get("decisionPolicyScope") if isinstance(brief.get("decisionPolicyScope"), dict) else {}
        name = _clean(scope.get("name"))
        if name in {INSTRUMENT_MARKET_SCOPE, PORTFOLIO_REBALANCE_SCOPE}:
            return name
    source_types = set(notification_policy_source_types(context))
    if source_types.intersection(PORTFOLIO_POLICY_SOURCE_TYPES):
        return PORTFOLIO_REBALANCE_SCOPE
    source_event_name = _clean(context.get("sourceEventName") or context.get("source_event_name")).lower()
    if source_event_name.startswith("portfolio.rebalance"):
        return PORTFOLIO_REBALANCE_SCOPE
    return INSTRUMENT_MARKET_SCOPE


def includes_portfolio_rebalance_policy(context: Dict[str, object]) -> bool:
    return notification_decision_policy_scope(context) == PORTFOLIO_REBALANCE_SCOPE


def decision_policy_scope_contract(context: Dict[str, object]) -> Dict[str, object]:
    scope = notification_decision_policy_scope(context)
    includes_rebalance = scope == PORTFOLIO_REBALANCE_SCOPE
    return {
        "version": DECISION_POLICY_SCOPE_VERSION,
        "name": scope,
        "portfolioRebalancePolicy": "included" if includes_rebalance else "excluded",
        "includedPolicyGroups": (
            ["portfolio-allocation", "cash-reserve", "concentration", "currency-exposure", "portfolio-risk"]
            if includes_rebalance
            else ["instrument-risk-tolerance", "loss-profit-protection", "instrument-action", "execution-safety"]
        ),
        "excludedPolicyGroups": (
            []
            if includes_rebalance
            else ["cash-reserve", "target-allocation", "concentration", "currency-exposure", "portfolio-risk", "rebalance-turnover"]
        ),
        "dedicatedReviewMessageType": PORTFOLIO_REBALANCE_REVIEW,
    }


def market_decision_investment_strategy(value: object) -> Dict[str, object]:
    strategy = dict(value or {}) if isinstance(value, dict) else {}
    return {
        key: strategy.get(key)
        for key in MARKET_DECISION_STRATEGY_FIELDS
        if strategy.get(key) not in (None, "", [], {})
    }


def market_decision_strategy_guidance(value: object) -> Dict[str, object]:
    guidance = dict(value or {}) if isinstance(value, dict) else {}
    return {
        key: guidance.get(key)
        for key in ("label", "profile")
        if guidance.get(key) not in (None, "", [], {})
    }


def market_decision_relation_facts(value: object) -> Dict[str, object]:
    facts = dict(value or {}) if isinstance(value, dict) else {}
    return {
        key: item
        for key, item in facts.items()
        if key not in REBALANCE_RELATION_FACT_KEYS
    }


def market_decision_raw_lines(values: Iterable[object]) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        text = _clean(value)
        if not text:
            continue
        label = text.lstrip("•- ").partition(":")[0].strip()
        if label in REBALANCE_RAW_LINE_LABELS:
            continue
        rows.append(text)
    return rows


def is_portfolio_rebalance_rule(value: object) -> bool:
    rule = dict(value or {}) if isinstance(value, dict) else {}
    module = _clean(rule.get("module") or rule.get("domainModule")).lower()
    action_group = _clean(rule.get("actionGroup") or rule.get("action_group")).lower()
    question_types = {_clean(item).lower() for item in rule.get("questionTypes") or []}
    evidence_state = rule.get("evidenceState") if isinstance(rule.get("evidenceState"), dict) else {}
    applied_fact_fields = {
        _clean(item)
        for item in evidence_state.get("appliedFactFields") or []
        if _clean(item)
    }
    return (
        module == "allocation-rebalance"
        or action_group == "rebalance"
        or "portfolio-rebalance" in question_types
        or bool(applied_fact_fields.intersection(REBALANCE_RELATION_FACT_KEYS))
    )


def market_decision_rule_rows(values: object) -> List[Dict[str, object]]:
    return [
        dict(item)
        for item in values or []
        if isinstance(item, dict) and not is_portfolio_rebalance_rule(item)
    ]


def _rule_id(value: object) -> str:
    rule = dict(value or {}) if isinstance(value, dict) else {}
    return _clean(rule.get("ruleId") or rule.get("rule_id"))


def _market_decision_drivers(values: object) -> List[Dict[str, object]]:
    excluded_categories = {
        "allocation",
        "cash",
        "concentration",
        "currencyexposure",
        "portfolio",
        "portfoliorisk",
        "rebalance",
    }
    rows = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        if _clean(item.get("category")).replace("-", "").lower() in excluded_categories:
            continue
        row = dict(item)
        if isinstance(row.get("dataKeys"), list):
            row["dataKeys"] = [
                key for key in row.get("dataKeys") or []
                if _clean(key) not in REBALANCE_RELATION_FACT_KEYS
            ]
        rows.append(row)
    return rows


def _market_decision_text_rows(values: object) -> List[object]:
    return [
        item
        for item in values or []
        if not any(marker in _clean(item) for marker in REBALANCE_DECISION_TEXT_MARKERS)
    ]


def _scope_decision_conditions(container: Dict[str, object]) -> Dict[str, object]:
    scoped = dict(container or {})
    for key in ("nextChecks", "strengthenConditions", "weakenConditions", "invalidationConditions"):
        if isinstance(scoped.get(key), list):
            scoped[key] = _market_decision_text_rows(scoped.get(key))
    return scoped


def market_decision_relation_context(value: object) -> Dict[str, object]:
    """Remove portfolio-policy evidence from a symbol market decision packet.

    TypeDB keeps the complete world. This projection only limits which part of
    that world may influence an instrument quote notification.
    """

    relation = dict(value or {}) if isinstance(value, dict) else {}
    source_rules = [
        dict(item)
        for key in ("activeRules", "matchedRules")
        for item in relation.get(key) or []
        if isinstance(item, dict)
    ]
    excluded_rule_ids = {
        _rule_id(item)
        for item in source_rules
        if is_portfolio_rebalance_rule(item) and _rule_id(item)
    }
    for key in ("activeRules", "matchedRules"):
        if key in relation:
            relation[key] = market_decision_rule_rows(relation.get(key))
    if isinstance(relation.get("assessmentBundle"), dict):
        relation["assessmentBundle"] = without_portfolio_assessment(
            relation.get("assessmentBundle")
        )

    facts = market_decision_relation_facts(relation.get("facts"))
    if isinstance(facts.get("relationFacts"), dict):
        facts["relationFacts"] = market_decision_relation_facts(facts.get("relationFacts"))
    relation["facts"] = facts

    execution = dict(relation.get("executionPlan") or {}) if isinstance(relation.get("executionPlan"), dict) else {}
    if execution:
        execution = _scope_decision_conditions(execution)
        execution["decisionDrivers"] = _market_decision_drivers(execution.get("decisionDrivers"))
        execution["sourceFacts"] = market_decision_relation_facts(execution.get("sourceFacts"))
        for assessment_key in ("addBuyAssessment", "profitTakeAssessment"):
            assessment = execution.get(assessment_key) if isinstance(execution.get(assessment_key), dict) else {}
            if assessment:
                execution[assessment_key] = _scope_decision_conditions(assessment)
        relation["executionPlan"] = execution

    for container_key in ("decision", "actionEnvelope"):
        container = relation.get(container_key) if isinstance(relation.get(container_key), dict) else {}
        if container:
            relation[container_key] = _scope_decision_conditions(container)

    brain = dict(relation.get("investmentBrain") or {}) if isinstance(relation.get("investmentBrain"), dict) else {}
    hypothesis_set = dict(brain.get("hypothesisSet") or {}) if isinstance(brain.get("hypothesisSet"), dict) else {}
    if hypothesis_set and excluded_rule_ids:
        hypotheses = []
        for item in hypothesis_set.get("hypotheses") or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            supporting_rule_ids = [
                _clean(rule_id)
                for rule_id in row.get("supportingRuleIds") or []
                if _clean(rule_id)
            ]
            if supporting_rule_ids and all(rule_id in excluded_rule_ids for rule_id in supporting_rule_ids):
                continue
            if supporting_rule_ids:
                row["supportingRuleIds"] = [
                    rule_id for rule_id in supporting_rule_ids if rule_id not in excluded_rule_ids
                ]
            hypotheses.append(row)
        hypothesis_set["hypotheses"] = hypotheses
        brain["hypothesisSet"] = hypothesis_set
        # The stored research and change summaries were generated from the
        # pre-scope hypothesis set. Reusing them after removing a portfolio
        # policy path would reintroduce that path as prose or audit fields.
        brain["researchPlan"] = {}
        brain["selfQuestions"] = []
        brain["hypothesisDecisionBrief"] = {}
        relation["investmentBrain"] = brain
        relation["researchPlan"] = {}
        relation["researchCycle"] = {}
        relation["hypothesisDecisionBrief"] = {}
        relation["whyNow"] = {}
        relation["signalConflicts"] = {}

    selected_rule_ids = set()
    for container_key in ("decision", "actionEnvelope", "executionPlan"):
        container = relation.get(container_key) if isinstance(relation.get(container_key), dict) else {}
        selected_rule_ids.add(_clean(container.get("selectedRuleId")))
    if excluded_rule_ids.intersection(selected_rule_ids):
        relation_decision = relation.get("decision") if isinstance(relation.get("decision"), dict) else {}
        target_role = _clean(relation.get("targetRole") or relation_decision.get("targetRole"))
        relation["decision"] = {
            "basis": "notification-policy-scope",
            "label": "종목 시세 판단 재확인",
            "candidateAction": "HOLD",
            "targetRole": target_role,
            "judgementBlocked": True,
            "reviewLevel": "check",
            "dataState": "partial",
            "changeState": "scope-filtered",
            "conflictState": "policy-scope-conflict",
            "nextChecks": ["포트폴리오 정책 판단은 전용 리밸런싱 알림에서 별도로 확인"],
        }
        relation["actionEnvelope"] = {
            "status": "POLICY_SCOPE_FILTERED",
            "preferredAction": "HOLD",
            "allowedActions": ["HOLD"],
            "aiAllowedActions": ["HOLD"],
            "blockedActions": [],
            "judgementBlocked": True,
            "targetRole": target_role,
            "source": "notification-policy-scope",
        }
        relation["executionPlan"] = {
            "candidateAction": "HOLD",
            "allowedActions": ["HOLD"],
            "decisionDrivers": _market_decision_drivers(execution.get("decisionDrivers")),
            "targetRole": target_role,
        }
        relation["allowedActions"] = ["HOLD"]
        relation["blockedActions"] = []
        relation["policyScopeViolation"] = {
            "status": "filtered",
            "excludedRuleIds": sorted(excluded_rule_ids.intersection(selected_rule_ids)),
        }
    return relation
