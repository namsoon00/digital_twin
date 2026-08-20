"""Bounded decision context for the final investment AI judge.

The canonical notification context is intentionally much larger than one AI
request should be.  This module keeps the full context in storage while
building one versioned, decision-bearing packet without copied graph payloads.
"""

from __future__ import annotations

import json
from typing import Dict, Iterable, List

from .decision_evidence_contract import (
    decision_readiness_contract,
    temporal_evidence_summary,
)
from .decision_continuity import compact_decision_continuity_packet
from .notification_ai import criterion_lines, context_raw_lines, target_label
from .notification_ai_gate_validation import (
    ai_decision_input_packet,
    delivery_profile_from_context,
)
from .investment_strategy_guidance import merge_strategy_context
from .notification_ai_context_router import (
    fit_notification_ai_decision_core,
    route_notification_ai_decision_context,
)
from .notification_ai_prompt_release import (
    AI_DECISION_CONTRACT_VERSION,
    AI_DECISION_PROMPT_VERSION,
    active_notification_ai_prompt_release,
)
from .notification_decision_policy import (
    INSTRUMENT_MARKET_SCOPE,
    decision_policy_scope_contract,
    includes_portfolio_rebalance_policy,
    market_decision_investment_strategy,
    market_decision_raw_lines,
    market_decision_relation_context,
    market_decision_strategy_guidance,
)


AI_DECISION_BRIEF_VERSION = "investment-ai-decision-brief-v4"
AI_PROFILE_STANDARD = "standard"
AI_PROFILE_DEEP_RESEARCH = "deepResearch"
VALID_REASONING_EFFORTS = {"low", "medium", "high", "max"}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _clean(value: object, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:max(1, int(limit or 1))]


def _unique(values: Iterable[object], limit: int = 12) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values or []:
        text = _clean(value, 360)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            rows.append(text)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


def _reasoning_effort(value: object, fallback: str) -> str:
    normalized = str(value or fallback).strip().lower()
    return normalized if normalized in VALID_REASONING_EFFORTS else fallback


def _relation_hypothesis_ids(value: object) -> set:
    relation = _mapping(value)
    hypothesis_set = _mapping(relation.get("hypothesisSet")) or _mapping(
        _mapping(relation.get("investmentBrain")).get("hypothesisSet")
    )
    return {
        str(item.get("hypothesisId") or "").strip()
        for item in hypothesis_set.get("hypotheses") or []
        if isinstance(item, dict) and str(item.get("hypothesisId") or "").strip()
    }


def _authoritative_v2_relation_context(context: Dict[str, object]) -> Dict[str, object]:
    current = _mapping(context.get("ontologyRelationContext"))
    metadata = _mapping(context.get("metadata"))
    synthesis = _mapping(context.get("v2DecisionSynthesis")) or _mapping(
        metadata.get("v2DecisionSynthesis")
    )
    eligible_ids = {
        str(value or "").strip()
        for value in synthesis.get("eligible_hypothesis_ids") or synthesis.get("eligibleHypothesisIds") or []
        if str(value or "").strip()
    }
    if not eligible_ids:
        return current
    expected_generation = str(
        synthesis.get("inference_generation_id") or synthesis.get("inferenceGenerationId") or ""
    ).strip()
    candidates = [
        _mapping(metadata.get("ontologyRelationContext")),
        current,
    ]
    for candidate in candidates:
        if not candidate or not eligible_ids.issubset(_relation_hypothesis_ids(candidate)):
            continue
        candidate_generation = str(
            candidate.get("inferenceGenerationId")
            or _mapping(candidate.get("hypothesisSet")).get("inferenceGenerationId")
            or ""
        ).strip()
        if expected_generation and candidate_generation and candidate_generation != expected_generation:
            continue
        return candidate
    return current


def notification_ai_execution_profile(
    context: Dict[str, object],
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    """Select workload policy from categorical states, never price thresholds."""

    settings = dict(settings or {})
    relation = _mapping(_mapping(context).get("ontologyRelationContext"))
    transition = _mapping(context.get("decisionTransition")) or _mapping(
        _mapping(context.get("ontologyRelationDiff")).get("decisionTransition")
    )
    research = _mapping(relation.get("researchCycle")) or _mapping(context.get("researchCycle"))
    news_impact = _mapping(context.get("newsImpact"))
    review_level = str(relation.get("reviewLevel") or "").strip().lower()
    change_state = str(relation.get("changeState") or transition.get("kind") or "").strip().lower()
    conflict_state = str(relation.get("conflictState") or "").strip().lower()
    deep_reasons: List[str] = []
    if review_level in {"immediate", "act"}:
        deep_reasons.append("high-review-level")
    if change_state in {"direction-changed", "action-changed", "new-condition", "envelope-changed"}:
        deep_reasons.append("material-decision-change")
    if conflict_state in {"mixed", "conflicted", "contested", "blocking"}:
        deep_reasons.append("competing-evidence")
    if bool(news_impact.get("decisionChanging")):
        deep_reasons.append("decision-changing-external-evidence")
    if int(research.get("changedEvidenceCount") or 0) > 0:
        deep_reasons.append("verified-research-update")

    deep_enabled = str(settings.get("notificationAiDeepResearchProfileEnabled", "1")).strip().lower() not in {
        "0", "false", "no", "off", "disabled",
    }
    profile = AI_PROFILE_DEEP_RESEARCH if deep_enabled and deep_reasons else AI_PROFILE_STANDARD
    configured_effort = settings.get("notificationAiReasoningEffort")
    if profile == AI_PROFILE_DEEP_RESEARCH:
        effort = _reasoning_effort(
            configured_effort or settings.get("notificationAiDeepReasoningEffort"),
            "high",
        )
        prompt_bytes = _int_setting(settings, "notificationAiDeepPromptMaxBytes", 20 * 1024, 16 * 1024, 20 * 1024)
    else:
        effort = _reasoning_effort(
            configured_effort or settings.get("notificationAiStandardReasoningEffort"),
            "high",
        )
        prompt_bytes = _int_setting(settings, "notificationAiStandardPromptMaxBytes", 16 * 1024, 12 * 1024, 16 * 1024)
    queue_limit = _int_setting(settings, "notificationAiQueueMaxPromptBytes", 24 * 1024, 12 * 1024, 24 * 1024)
    return {
        "version": "notification-ai-execution-profile-v2",
        "name": profile,
        "reasoningEffort": effort,
        "maxPromptBytes": min(prompt_bytes, queue_limit),
        "selectionReasons": _unique(deep_reasons, 8) or ["standard-investment-review"],
        "researchExecution": "asynchronous",
    }


def _decision_changing_gaps(plan: Dict[str, object]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for task in plan.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        relevance = str(task.get("decisionRelevance") or "supporting").strip().lower()
        status = str(task.get("status") or "ready").strip().lower()
        if relevance not in {"direct", "important"} and status != "blocked-by-data":
            continue
        rows.append({
            "taskId": task.get("taskId"),
            "question": _clean(task.get("question"), 300),
            "decisionRelevance": relevance,
            "status": status,
            "requiredEvidenceTypes": list(task.get("requiredEvidenceTypes") or [])[:8],
        })
        if len(rows) >= 5:
            break
    return rows


def notification_ai_decision_brief(
    context: Dict[str, object],
    settings: Dict[str, object] = None,
    profile: Dict[str, object] = None,
) -> Dict[str, object]:
    """Build the complete decision-bearing packet for one AI invocation."""

    merged = merge_strategy_context(dict(context or {}))
    message_type = str(merged.get("messageType") or merged.get("rule") or "notification")
    policy_scope = decision_policy_scope_contract(merged)
    include_rebalance = includes_portfolio_rebalance_policy(merged)
    decision_context = dict(merged)
    decision_context["ontologyRelationContext"] = _authoritative_v2_relation_context(decision_context)
    if not include_rebalance:
        decision_context["ontologyRelationContext"] = market_decision_relation_context(
            decision_context.get("ontologyRelationContext")
        )
        decision_context["rawLines"] = market_decision_raw_lines(context_raw_lines(merged))
        decision_context["lines"] = market_decision_raw_lines(merged.get("lines") or [])
        decision_context["activeInvestmentOpinion"] = {}
    canonical_relation = _mapping(decision_context.get("ontologyRelationContext"))
    canonical_brain = _mapping(canonical_relation.get("investmentBrain"))
    canonical_facts = _mapping(canonical_relation.get("facts"))
    prompt_context = {
        "facts": {
            "messageType": message_type,
            "target": target_label(decision_context),
            "rawLines": context_raw_lines(decision_context),
            "criteria": criterion_lines(decision_context),
            "relationFacts": canonical_facts,
            "trendDynamics": canonical_facts.get("trendDynamics") or {},
            "researchEvidence": canonical_facts.get("researchEvidence") or [],
            "newsHeadlines": canonical_facts.get("newsHeadlines") or [],
            "disclosure": canonical_facts.get("disclosure") or merged.get("disclosure") or {},
            "sourceAlertEvents": canonical_facts.get("sourceAlertEvents") or [],
            "companyContext": canonical_facts.get("companyContext") or {},
            "companyValuationContext": canonical_facts.get("companyValuationContext") or {},
            "missingData": canonical_relation.get("missingData") or canonical_facts.get("missingData") or [],
        },
    }
    delivery_profile = delivery_profile_from_context(decision_context)
    decision_input = ai_decision_input_packet(decision_context, prompt_context, delivery_profile)
    if not include_rebalance:
        decision_input["investmentStrategy"] = market_decision_investment_strategy(
            decision_input.get("investmentStrategy")
        )
        decision_input["investmentStrategyGuidance"] = market_decision_strategy_guidance(
            decision_input.get("investmentStrategyGuidance")
        )
        decision_input["precomputedOpinionCandidate"] = {}
    relation = _mapping(decision_input.get("relationshipDatabaseInference"))
    active_rule_rows = [
        dict(item)
        for item in relation.get("activeRules") or []
        if isinstance(item, dict)
    ]
    context_policies = [
        dict(item.get("contextCompletenessPolicy") or {})
        for item in active_rule_rows
        if isinstance(item.get("contextCompletenessPolicy"), dict)
    ]
    subject = _mapping(canonical_relation.get("subject"))
    internal = _mapping(decision_context.get("notificationAiInternalData"))
    temporal_summary = temporal_evidence_summary(
        internal.get("temporalWindows") or [],
        canonical_relation,
    )
    system_readiness_full = decision_readiness_contract(decision_context)
    system_readiness = {
        key: system_readiness_full.get(key)
        for key in (
            "version", "status", "state", "evaluated", "minimumEligibleFamilyCount",
            "eligibleHypothesisCount", "eligibleFamilyCount", "referenceHypothesisCount",
            "selectedCoreInferenceEligible", "reasons",
        )
        if system_readiness_full.get(key) not in (None, "", [], {})
    }
    portfolio_lifecycle = _compact_portfolio_lifecycle(
        merged.get("portfolioLifecycle"),
        subject.get("symbol") or merged.get("rawSymbol") or merged.get("symbol"),
        include_rebalance=include_rebalance,
    )
    decision_continuity = compact_decision_continuity_packet(
        merged.get("decisionContinuityPacket")
    )
    execution_profile = dict(profile or notification_ai_execution_profile(decision_context, settings))
    hypothesis_set = _mapping(relation.get("hypothesisSet"))
    research_cycle = _mapping(relation.get("researchCycle"))
    research_plan = _mapping(canonical_brain.get("researchPlan")) or _mapping(relation.get("researchPlan"))

    return {
        "schemaVersion": AI_DECISION_BRIEF_VERSION,
        "decisionContractVersion": AI_DECISION_CONTRACT_VERSION,
        "messageType": message_type,
        "decisionPolicyScope": policy_scope,
        "executionProfile": execution_profile,
        "question": relation.get("investmentQuestion") or {
            "text": _clean(merged.get("investmentBrainQuestionText") or "현재 투자 행동과 다음 확인 조건을 판단한다."),
            "subjectSymbol": subject.get("symbol") or merged.get("rawSymbol") or merged.get("symbol"),
            "subjectName": subject.get("name") or merged.get("displayTarget") or merged.get("target"),
        },
        "subject": {
            "symbol": subject.get("symbol") or merged.get("rawSymbol") or merged.get("symbol"),
            "name": subject.get("name") or merged.get("displayTarget") or merged.get("target"),
            "market": subject.get("market"),
            "targetRole": relation.get("targetRole") or decision_input.get("targetPositionRole"),
            "referenceDate": _mapping(decision_input.get("rawAlert")).get("referenceDate"),
        },
        "decisionState": {
            "previousFinalDecision": decision_input.get("previousFinalDecision") or {},
            "precomputedActionCandidate": decision_input.get("precomputedActionCandidate"),
            "decisionTransition": relation.get("decisionTransition") or {},
            "decision": relation.get("decision") or {},
            "actionEnvelope": relation.get("actionEnvelope") or {},
            "allowedActions": relation.get("allowedActions") or decision_input.get("allowedActions") or [],
            "blockedActions": relation.get("blockedActions") or decision_input.get("blockedActions") or [],
            "reviewLevel": relation.get("reviewLevel"),
            "dataState": relation.get("dataState"),
            "changeState": relation.get("changeState"),
            "conflictState": relation.get("conflictState"),
            "systemReadiness": system_readiness,
        },
        "decisionContinuity": decision_continuity,
        "assessmentBundle": relation.get("assessmentBundle") or {},
        "currentSituation": {
            "rawAlert": decision_input.get("rawAlert") or {},
            "relationFacts": relation.get("relationFacts") or {},
            "trendDynamics": relation.get("trendDynamics") or {},
            "temporalWindows": internal.get("temporalWindows") or [],
            "temporalEvidenceSummary": temporal_summary,
            "companyContext": relation.get("companyContext") or {},
            "companyValuationContext": relation.get("companyValuationContext") or {},
        },
        "inference": {
            "activeRules": active_rule_rows,
            "contextCoverage": {
                "activeRuleCount": len(active_rule_rows),
                "contractedRuleCount": len(context_policies),
                "aboxReadMode": sorted({
                    str(item.get("aboxReadMode") or "")
                    for item in context_policies
                    if str(item.get("aboxReadMode") or "")
                }),
                "unchangedFactsRetained": bool(context_policies) and all(
                    bool(item.get("retainUnchangedFacts"))
                    for item in context_policies
                ),
                "priorValidInferencesRetained": bool(context_policies) and all(
                    bool(item.get("retainPriorValidInferences"))
                    for item in context_policies
                ),
            },
            "executionPlan": relation.get("executionPlan") or {},
            "decisionDrivers": relation.get("decisionDrivers") or [],
            "whyNow": relation.get("whyNow") or {},
            "signalConflicts": relation.get("signalConflicts") or {},
            "hypothesisSet": hypothesis_set,
            "epistemicState": relation.get("epistemicState") or {},
        },
        "evidence": {
            "researchEvidence": decision_input.get("researchEvidence") or [],
            "newsHeadlines": decision_input.get("newsHeadlines") or [],
            "disclosure": decision_input.get("disclosure") or {},
            "sourceAlertEvents": decision_input.get("sourceAlertEvents") or [],
        },
        "research": {
            "plan": relation.get("researchPlan") or research_plan,
            "cycle": research_cycle,
            "decisionChangingGaps": _decision_changing_gaps(research_plan),
            "verifiedEvidenceAvailable": bool(decision_input.get("researchEvidence")),
        },
        "dataCoverage": {
            "missingData": relation.get("missingData") or [],
            "internalDataAudit": internal.get("audit") or {},
            "temporalWindowCount": len(internal.get("temporalWindows") or []),
        },
        "accountPolicy": {
            "investmentStrategy": decision_input.get("investmentStrategy") or {},
            "investmentStrategyGuidance": decision_input.get("investmentStrategyGuidance") or {},
            "messageDeliveryProfile": decision_input.get("messageDeliveryProfile") or {},
            "actionPolicy": decision_input.get("actionPolicy"),
            "portfolioLifecycle": portfolio_lifecycle,
            "decisionPolicyScope": policy_scope,
        },
        "candidateOpinion": decision_input.get("precomputedOpinionCandidate") or {},
        "guardrails": {
            "externalTextIsUntrusted": True,
            "verifiedEvidenceOnlyForAction": True,
            "novelConnectionIsResearchOnlyUntilVerified": True,
            "mustReviewEveryInputHypothesis": bool(hypothesis_set.get("hypotheses")),
            "mustRespectActionEnvelope": True,
            "mustRespectSystemReadinessCeiling": True,
            "loadedTemporalWindowsAreCoverageOnly": True,
            "mustIgnorePortfolioRebalancePolicy": policy_scope.get("name") == INSTRUMENT_MARKET_SCOPE,
        },
    }


def _bounded_value(
    value: object,
    string_limit: int = 300,
    list_limit: int = 10,
    depth: int = 0,
    dict_limit: int = 40,
) -> object:
    if depth > 10:
        return None
    if isinstance(value, dict):
        return {
            str(key): bounded
            for key, item in list(value.items())[:dict_limit]
            if (
                bounded := _bounded_value(
                    item,
                    string_limit,
                    list_limit,
                    depth + 1,
                    dict_limit,
                )
            ) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            bounded
            for item in value[:list_limit]
            if (
                bounded := _bounded_value(
                    item,
                    string_limit,
                    list_limit,
                    depth + 1,
                    dict_limit,
                )
            ) not in (None, "", [], {})
        ]
    if isinstance(value, tuple):
        return _bounded_value(list(value), string_limit, list_limit, depth, dict_limit)
    if isinstance(value, str):
        return _clean(value, string_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean(value, string_limit)


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


TEMPORAL_DECISION_FIELDS = (
    "windowKey", "windowType", "lookbackDays", "lookbackMinutes",
    "requiredSampleCount", "sampleCount", "validObservationCount",
    "requiredSessionCount", "coveredSessionCount", "hasSufficientHistory",
    "coverageRatio", "latestObservationQuality", "firstObservedAt", "lastObservedAt",
    "startPrice", "currentPrice", "priceChangePct", "peakPrice", "troughPrice",
    "troughReturnPct", "drawdownFromPeakPct", "reboundFromTroughPct",
    "priorPriceChangePct", "recentPriceChangePct", "priceVelocityChangePct",
    "volumeRatioEnd", "tradeStrengthEnd", "bidAskImbalanceEnd",
    "smartMoneyDataState", "smartMoneyNetLatest", "smartMoneyNetChange",
)

HYPOTHESIS_DECISION_FIELDS = (
    "hypothesisId", "templateId", "familyId", "label", "claim", "stance",
    "evidenceState", "supportingRuleIds", "supportingEvidenceIds",
    "counterEvidenceIds", "causalPathIds", "assumptions", "invalidationConditions",
    "horizon", "scopeState", "verificationStatus", "approvalStatus",
)

DECISION_FIELDS = (
    "basis", "label", "candidateAction", "sourceCandidateAction", "primaryAction",
    "primaryActionLabel", "decisionStage", "decisionEffect", "actionGroup",
    "actionLevel", "actionPolicy", "allowedActions", "blockedActions", "targetRole",
    "judgementBlocked", "selectedRuleId", "candidateRuleIds", "eligibleRuleIds",
    "excludedRuleIds", "reviewLevel",
    "dataState", "changeState", "conflictState", "nextChecks",
)

ACTION_ENVELOPE_FIELDS = (
    "status", "preferredAction", "allowedActions", "blockedActions", "aiAllowedActions",
    "aiMayDowngrade", "aiMayUpgradeToBuy", "judgementBlocked", "selectedRuleId",
    "drivingRuleIds", "supportRuleIds", "blockingRuleIds", "constraintRuleIds",
    "invalidationConditions", "strengthenConditions", "nextChecks", "targetRole",
    "dataReadiness", "coreInferenceSelection",
)

RULE_DECISION_FIELDS = (
    "ruleId", "label", "relationType", "reviewLevel", "dataState", "evidenceRole",
    "evidence", "evidenceState",
)

DRIVER_DECISION_FIELDS = (
    "category", "direction", "evidenceRole", "label", "dataKeys", "summary",
)

EVIDENCE_DECISION_FIELDS = (
    "evidenceId", "kind", "eventType", "title", "summary", "evidenceRole",
    "polarity", "materialityState", "relevanceState", "validationState", "dataState",
    "source", "sourceKind", "sourceTrustState", "publishedAt", "observedAt", "url",
)

ACCOUNT_STRATEGY_FIELDS = (
    "label", "riskTolerance", "timeHorizon", "lossTolerancePct",
    "profitProtectionPct", "maxPositionWeightPct", "maxSectorWeightPct",
    "fxExposureReviewPct", "minCashWeightPct", "addBuyPolicy",
    "allowLossAddBuyReview", "watchlistActionPolicy", "holdingActionPolicy", "profile",
)

PORTFOLIO_MANDATE_FIELDS = ACCOUNT_STRATEGY_FIELDS + (
    "risk_tolerance", "time_horizon", "loss_tolerance_pct", "profit_protection_pct",
    "max_position_weight_pct", "max_sector_weight_pct", "fx_exposure_review_pct",
    "min_cash_weight_pct", "add_buy_policy", "watchlist_action_policy",
    "holding_action_policy", "allowed_actions",
)

RELATION_FACT_PRIORITY = (
    "marketEvidenceProfile",
    "currentPrice", "averagePrice", "profitLossRate", "profitLossRateDeltaPct",
    "quantity", "sellableQuantity", "marketValue", "positionWeight", "sectorWeight",
    "volume", "volumeRatio", "tradeStrength", "buyExecutionVolume", "sellExecutionVolume",
    "bidAskImbalance", "foreignNetVolume", "institutionNetVolume", "individualNetVolume",
    "ma5", "ma20", "ma60", "ma5Distance", "ma20Distance", "ma60Distance",
    "ma20Slope", "ma60Slope", "changeRate", "currency", "market", "source",
    "usdKrw", "macroDgs2", "macroDgs10", "macroDff", "btcPrice", "btcChange24h",
    "btcChange7d", "valuationDecisionEligible", "valuationCurrentPrice",
    "valuationFairValue", "valuationFairValueLow", "valuationFairValueHigh",
)


def _selected_fields(value: object, fields: Iterable[str]) -> Dict[str, object]:
    row = _mapping(value)
    return {
        key: row.get(key)
        for key in fields
        if row.get(key) not in (None, "", [], {})
    }


def _compact_relation_facts(value: object, limit: int = 64) -> Dict[str, object]:
    facts = _mapping(value)
    compact: Dict[str, object] = {}
    for key in RELATION_FACT_PRIORITY:
        if key in facts and facts.get(key) not in (None, "", [], {}):
            compact[key] = facts.get(key)
    for key in sorted(facts):
        if key in compact or len(compact) >= max(1, int(limit or 1)):
            continue
        item = facts.get(key)
        if isinstance(item, (str, int, float, bool)) and item not in (None, ""):
            compact[key] = item
    return compact


def _compact_temporal_windows(value: object) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    seen = set()
    for item in value or []:
        row = _selected_fields(item, TEMPORAL_DECISION_FIELDS)
        window_key = str(row.get("windowKey") or "").upper().strip()
        if not window_key or window_key in seen:
            continue
        seen.add(window_key)
        rows.append(row)
    return rows[:12]


def _compact_hypotheses(value: object) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    seen = set()
    for item in value or []:
        row = _selected_fields(item, HYPOTHESIS_DECISION_FIELDS)
        hypothesis_id = str(row.get("hypothesisId") or "").strip()
        if not hypothesis_id or hypothesis_id in seen:
            continue
        seen.add(hypothesis_id)
        for key in (
            "supportingRuleIds", "supportingEvidenceIds", "counterEvidenceIds",
            "causalPathIds", "assumptions", "invalidationConditions",
        ):
            if isinstance(row.get(key), list):
                row[key] = list(row[key])[:8]
        rows.append(row)
    return rows[:12]


def _compact_dict_rows(value: object, fields: Iterable[str], limit: int) -> List[Dict[str, object]]:
    return [
        row
        for item in list(value or [])[:max(1, int(limit or 1))]
        if (row := _selected_fields(item, fields))
    ]


def _compact_company_context(value: object) -> Dict[str, object]:
    company = _mapping(value)
    financials = _mapping(company.get("latestFinancials"))
    financial_fields = (
        "period", "revenue", "revenueGrowthPct", "grossProfit",
        "operatingIncome", "operatingIncomeGrowthPct", "operatingMarginPct",
        "netIncome", "netIncomeGrowthPct", "netMarginPct",
        "operatingCashFlow", "capitalExpenditure", "freeCashFlow",
        "cash", "totalDebt", "equity", "debtToEquityPct",
    )
    return {
        **_selected_fields(
            company,
            (
                "schemaVersion", "symbol", "companyName", "factRevision",
                "materialRevision", "judgmentUse",
            ),
        ),
        "profile": _selected_fields(
            company.get("profile"),
            (
                "sector", "industry", "country", "exchange", "ceoName",
                "marketCapitalization", "employees",
            ),
        ),
        "valuation": _selected_fields(
            company.get("valuation"),
            (
                "peRatio", "forwardPE", "pbr", "pegRatio", "trailingEPS",
                "returnOnEquityPct", "returnOnAssetsPct", "dividendYieldPct",
                "enterpriseToEbitda", "beta",
            ),
        ),
        "ownership": _selected_fields(
            company.get("ownership"),
            ("insiderOwnershipPct", "institutionalOwnershipPct"),
        ),
        "capital": _selected_fields(
            company.get("capital"),
            ("cash", "totalDebt", "sharesOutstanding", "floatShares", "sharesShort"),
        ),
        "coverage": _selected_fields(
            company.get("coverage"),
            ("dataState", "officialSource", "financialPeriods", "valuationFields"),
        ),
        "latestFinancials": {
            "annual": _compact_dict_rows(financials.get("annual"), financial_fields, 1),
            "quarterly": _compact_dict_rows(financials.get("quarterly"), financial_fields, 1),
        },
        "provenance": _compact_dict_rows(
            company.get("provenance"),
            ("provider", "scope", "asOf"),
            3,
        ),
    }


def _compact_portfolio_lifecycle(
    value: object,
    subject_symbol: object,
    include_rebalance: bool = False,
) -> Dict[str, object]:
    lifecycle = _mapping(value)
    symbol = str(subject_symbol or "").upper().strip()
    mandate = _mapping(lifecycle.get("mandate"))
    reconciliation = _mapping(lifecycle.get("reconciliation"))
    exposure = _mapping(lifecycle.get("exposureSnapshot"))
    risk = _mapping(lifecycle.get("portfolioRiskSnapshot"))
    rebalance = _mapping(lifecycle.get("rebalanceProposal"))
    rebalance_state = _mapping(lifecycle.get("rebalanceState"))
    state = _mapping(lifecycle.get("portfolioState"))

    metrics = []
    for item in exposure.get("metrics") or []:
        row = _mapping(item)
        key = str(row.get("key") or "").upper().strip()
        try:
            breached = float(row.get("policyDeltaPct") or 0) > 0
        except (TypeError, ValueError):
            breached = False
        if key == symbol or breached or str(row.get("exposure_type") or "") in {"cash", "currency"}:
            metrics.append(_selected_fields(
                row,
                ("exposure_type", "key", "ratio_pct", "policy_limit_pct", "policyDeltaPct", "observed_at"),
            ))

    positions = [
        _selected_fields(
            item,
            (
                "symbol", "weight_pct", "period_return_pct", "maximum_drawdown_pct",
                "annualized_volatility_pct", "beta", "data_state", "latest_observation_at",
                "sample_count", "missing_data",
            ),
        )
        for item in risk.get("positions") or []
        if str(_mapping(item).get("symbol") or "").upper().strip() == symbol
    ]
    subject_positions = [
        _selected_fields(
            item,
            (
                "symbol", "currentWeightPct", "profitLossRate", "marketValueKrw",
                "holdingDays", "openedAt", "lastIncreaseAt", "lastDecreaseAt",
            ),
        )
        for item in state.get("positions") or []
        if str(_mapping(item).get("symbol") or "").upper().strip() == symbol
    ]
    if not include_rebalance:
        market_position_rows = [
            _selected_fields(
                item,
                (
                    "symbol", "profitLossRate", "marketValueKrw", "holdingDays",
                    "openedAt", "lastIncreaseAt", "lastDecreaseAt",
                ),
            )
            for item in subject_positions
        ]
        return {
            **_selected_fields(lifecycle, ("status", "portfolioId")),
            "reconciliation": _selected_fields(
                reconciliation,
                ("status", "differenceCount", "source", "sourceSnapshotAt", "createdAt"),
            ),
            "portfolioState": {
                "subjectPositions": market_position_rows[:1],
            },
        }
    all_rebalance_legs = [
        item
        for item in rebalance.get("legs") or []
        if isinstance(item, dict)
    ]
    subject_rebalance_legs = all_rebalance_legs
    breach_keys = [str(value or "").strip() for value in rebalance_state.get("breachKeys") or [] if str(value or "").strip()]
    rebalance_relevant = bool(include_rebalance or subject_rebalance_legs or breach_keys)
    return {
        **_selected_fields(lifecycle, ("status", "portfolioId")),
        "mandate": _selected_fields(mandate, PORTFOLIO_MANDATE_FIELDS),
        "reconciliation": _selected_fields(
            reconciliation,
            ("status", "differenceCount", "source", "sourceSnapshotAt", "createdAt"),
        ),
        "exposureSnapshot": {
            **_selected_fields(exposure, ("observedAt",)),
            "metrics": metrics[:10],
        },
        "portfolioRiskSnapshot": {
            **_selected_fields(
                risk,
                (
                    "dataState", "observedAt", "periodReturnPct", "benchmarkReturnPct",
                    "activeReturnPct", "maximumDrawdownPct", "annualizedVolatilityPct",
                    "maximumPairwiseCorrelation", "sampleCount", "missingData",
                ),
            ),
            "subjectPositions": positions[:1],
        },
        "rebalanceProposal": {
            **_selected_fields(rebalance, ("status", "createdAt", "recommendedScenarioId")),
            "drifts": _compact_dict_rows(
                rebalance.get("drifts"),
                ("allocationKey", "currentWeightPct", "targetDeltaPct", "bandDeltaPct"),
                6,
            ),
            "legs": _compact_dict_rows(
                subject_rebalance_legs,
                ("symbol", "side", "before_weight_pct", "after_weight_pct", "target_delta_pct", "rationale"),
                12,
            ),
        } if rebalance_relevant else {},
        "rebalanceState": _selected_fields(
            rebalance_state,
            (
                "status", "policyVersion", "breachKeys", "adjustmentDirections",
                "exposureDeltasPct", "maximumNotionalBySymbol",
                "volatilityPolicyDeltaPct", "drawdownPolicyDeltaPct",
                "correlationPolicyDelta", "dataState", "observedAt",
                "revision", "lastTransitionType",
            ),
        ) if rebalance_relevant else {},
        "portfolioState": {
            **_selected_fields(state, ("cashWeightPct", "positionCount", "observedAt")),
            "subjectPositions": subject_positions[:1],
        },
    }


def _question_bounded_active_rules(
    values: object,
    action_envelope: Dict[str, object],
    limit: int = 6,
) -> List[Dict[str, object]]:
    """Select only usable rules that can answer this decision question."""

    envelope = _mapping(action_envelope)
    readiness = _mapping(envelope.get("dataReadiness"))
    eligible_ids = {
        str(item or "").strip()
        for item in readiness.get("eligibleRuleIds") or []
        if str(item or "").strip()
    }
    priority_ids = _unique([
        envelope.get("selectedRuleId"),
        *(envelope.get("drivingRuleIds") or []),
        *(envelope.get("blockingRuleIds") or []),
        *(envelope.get("constraintRuleIds") or []),
        *(envelope.get("supportRuleIds") or []),
        *(envelope.get("deferRuleIds") or []),
    ], limit * 2)
    rows = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("ruleId") or "").strip()
        evidence_state = _mapping(item.get("evidenceState"))
        eligibility = str(evidence_state.get("inferenceEligibilityStatus") or "eligible")
        if eligibility != "eligible" or evidence_state.get("evidenceUsableForJudgement") is False:
            continue
        if eligible_ids and rule_id not in eligible_ids:
            continue
        rows.append(dict(item))
    rows.sort(key=lambda item: (
        priority_ids.index(str(item.get("ruleId") or ""))
        if str(item.get("ruleId") or "") in priority_ids else len(priority_ids),
        str(item.get("ruleId") or ""),
    ))
    return rows[:limit]


def _critical_decision_brief(brief: Dict[str, object]) -> Dict[str, object]:
    """Keep decision-bearing fields before reducing presentation detail.

    A generic list or dictionary slice can silently remove long-horizon windows
    and the hypothesis list while retaining low-value fields that happened to
    be inserted first.  This contract makes those omissions impossible within
    the supported twelve-window/twelve-hypothesis boundary.
    """

    current = _mapping(brief.get("currentSituation"))
    inference = _mapping(brief.get("inference"))
    hypothesis_set = _mapping(inference.get("hypothesisSet"))
    research = _mapping(brief.get("research"))
    evidence = _mapping(brief.get("evidence"))
    account_policy = _mapping(brief.get("accountPolicy"))
    lifecycle = _mapping(account_policy.get("portfolioLifecycle"))
    subject = _mapping(brief.get("subject"))
    decision_state = _mapping(brief.get("decisionState"))
    action_envelope = _mapping(decision_state.get("actionEnvelope"))
    assessment_bundle = _mapping(brief.get("assessmentBundle"))
    raw_alert = _mapping(current.get("rawAlert"))
    policy_scope = _mapping(brief.get("decisionPolicyScope"))
    include_rebalance = policy_scope.get("name") != INSTRUMENT_MARKET_SCOPE
    hypotheses = _compact_hypotheses(hypothesis_set.get("hypotheses") or [])
    temporal_windows = _compact_temporal_windows(current.get("temporalWindows") or [])
    return {
        "schemaVersion": brief.get("schemaVersion"),
        "decisionContractVersion": brief.get("decisionContractVersion"),
        "messageType": brief.get("messageType"),
        "decisionPolicyScope": policy_scope,
        "executionProfile": brief.get("executionProfile"),
        "question": brief.get("question"),
        "subject": subject,
        "decisionState": {
            "previousFinalDecision": _selected_fields(
                decision_state.get("previousFinalDecision"),
                ("action", "label", "summary", "source", "generatedAt", "referenceDate"),
            ),
            "precomputedActionCandidate": decision_state.get("precomputedActionCandidate"),
            "decisionTransition": _selected_fields(
                decision_state.get("decisionTransition"),
                ("kind", "changed", "previousAction", "currentAction", "summary", "reason"),
            ),
            "decision": _selected_fields(decision_state.get("decision"), DECISION_FIELDS),
            "actionEnvelope": _bounded_value(
                _selected_fields(decision_state.get("actionEnvelope"), ACTION_ENVELOPE_FIELDS),
                string_limit=160,
                list_limit=6,
                dict_limit=24,
            ),
            **_selected_fields(
                decision_state,
                (
                    "allowedActions", "blockedActions", "reviewLevel", "dataState",
                    "changeState", "conflictState", "systemReadiness",
                ),
            ),
        },
        "decisionContinuity": _bounded_value(
            brief.get("decisionContinuity") or {},
            string_limit=160,
            list_limit=6,
            dict_limit=24,
        ),
        "assessmentBundle": {
            key: _bounded_value(
                assessment_bundle.get(key),
                string_limit=160,
                list_limit=4,
                dict_limit=18,
            )
            for key in (
                "version", "source", "evidenceQuality", "investmentOpinion",
                "portfolioFit", "executionReadiness", "recommendedPlan", "monitoringPlan",
            )
            if assessment_bundle.get(key) not in (None, "", [], {})
        },
        "currentSituation": {
            "rawAlert": {
                **_selected_fields(raw_alert, ("messageType", "target", "referenceDate")),
                "rawLines": list(raw_alert.get("rawLines") or [])[:6],
                "criteria": list(raw_alert.get("criteria") or [])[:5],
            },
            "relationFacts": _compact_relation_facts(current.get("relationFacts"), 36),
            "trendDynamics": _bounded_value(
                current.get("trendDynamics") or {},
                string_limit=160,
                list_limit=6,
                dict_limit=24,
            ),
            "temporalWindows": temporal_windows,
            "temporalEvidenceSummary": _bounded_value(
                current.get("temporalEvidenceSummary") or {},
                string_limit=120,
                list_limit=12,
                dict_limit=24,
            ),
            "companyContext": _compact_company_context(current.get("companyContext")),
            "companyValuationContext": _bounded_value(
                current.get("companyValuationContext") or {},
                string_limit=160,
                list_limit=6,
                dict_limit=24,
            ),
        },
        "inference": {
            "activeRules": _compact_dict_rows(
                _question_bounded_active_rules(inference.get("activeRules"), action_envelope),
                RULE_DECISION_FIELDS,
                6,
            ),
            "executionPlan": _selected_fields(
                inference.get("executionPlan"),
                (
                    "engineVersion", "targetRole", "actionPolicy", "candidateAction",
                    "decisionLabel", "decisionStage", "primaryAction", "allowedActions",
                    "blockedActions", "supportSignals", "nextChecks", "missingDataImpact",
                ),
            ),
            "decisionDrivers": _compact_dict_rows(
                inference.get("decisionDrivers"), DRIVER_DECISION_FIELDS, 6,
            ),
            "whyNow": _bounded_value(
                inference.get("whyNow") or {},
                string_limit=160,
                list_limit=4,
                dict_limit=18,
            ),
            "signalConflicts": _bounded_value(
                inference.get("signalConflicts") or {},
                string_limit=160,
                list_limit=4,
                dict_limit=18,
            ),
            "hypothesisSet": {
                **{
                    key: hypothesis_set.get(key)
                    for key in (
                        "hypothesisSetId", "questionId", "subjectSymbol",
                        "inferenceGenerationId", "comparisonRequired",
                        "minimumComparisonCount", "scopeVersion", "createdAt",
                        "decisionEvidenceSummary",
                    )
                    if hypothesis_set.get(key) not in (None, "", [], {})
                },
                "hypotheses": hypotheses,
                "referenceHypotheses": _compact_dict_rows(
                    hypothesis_set.get("referenceHypotheses"),
                    (
                        "hypothesisId", "templateId", "templateLabel", "stance",
                        "approvalStatus", "verificationStatus",
                    ),
                    4,
                ),
            },
            "epistemicState": _bounded_value(
                inference.get("epistemicState") or {},
                string_limit=160,
                list_limit=4,
                dict_limit=18,
            ),
        },
        "evidence": {
            "researchEvidence": _compact_dict_rows(
                evidence.get("researchEvidence"), EVIDENCE_DECISION_FIELDS, 5,
            ),
            "newsHeadlines": _bounded_value(
                list(evidence.get("newsHeadlines") or [])[:3],
                string_limit=180,
                list_limit=3,
                dict_limit=12,
            ),
            "disclosure": _bounded_value(
                evidence.get("disclosure") or {},
                string_limit=180,
                list_limit=3,
                dict_limit=14,
            ),
            "sourceAlertEvents": _bounded_value(
                list(evidence.get("sourceAlertEvents") or [])[:4],
                string_limit=160,
                list_limit=4,
                dict_limit=12,
            ),
        },
        "research": {
            "plan": _selected_fields(
                research.get("plan"),
                ("planId", "questionId", "status", "maxRounds", "createdAt", "unresolvedQuestions"),
            ),
            "cycle": _selected_fields(
                research.get("cycle"),
                (
                    "cycleId", "status", "round", "changedEvidenceCount",
                    "verifiedEvidenceCount", "startedAt", "completedAt",
                ),
            ),
            "decisionChangingGaps": _bounded_value(
                list(research.get("decisionChangingGaps") or [])[:4],
                string_limit=180,
                list_limit=4,
                dict_limit=10,
            ),
            "verifiedEvidenceAvailable": bool(research.get("verifiedEvidenceAvailable")),
        },
        "dataCoverage": _bounded_value(
            brief.get("dataCoverage") or {},
            string_limit=160,
            list_limit=6,
            dict_limit=16,
        ),
        "accountPolicy": {
            "investmentStrategy": _selected_fields(
                account_policy.get("investmentStrategy"), ACCOUNT_STRATEGY_FIELDS,
            ),
            "investmentStrategyGuidance": _selected_fields(
                account_policy.get("investmentStrategyGuidance"),
                ("label", "profile", "stance", "actionBoundaries", "riskChecks"),
            ),
            "actionPolicy": account_policy.get("actionPolicy"),
            "decisionPolicyScope": _mapping(account_policy.get("decisionPolicyScope")) or policy_scope,
            "portfolioLifecycle": _compact_portfolio_lifecycle(
                lifecycle,
                subject.get("symbol"),
                include_rebalance=include_rebalance,
            ),
        },
        "candidateOpinion": _bounded_value(
            brief.get("candidateOpinion") or {},
            string_limit=160,
            list_limit=4,
            dict_limit=16,
        ),
        "guardrails": brief.get("guardrails") or {},
        "contextBudget": {
            "status": "decision-critical",
            "temporalWindowCount": len(temporal_windows),
            "hypothesisCount": len(hypotheses),
            "reason": "기간 식별자와 경쟁 가설을 우선 보존한 판단 전용 컨텍스트입니다.",
        },
    }


def _minimum_hypotheses(value: object, *, emergency: bool = False) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for item in value or []:
        row = _mapping(item)
        hypothesis_id = str(row.get("hypothesisId") or "").strip()
        if not hypothesis_id:
            continue
        compact = _selected_fields(
            row,
            (
                "hypothesisId", "templateId", "familyId", "claim", "stance",
                "evidenceState", "verificationStatus", "approvalStatus", "scopeState",
            ),
        )
        compact["claim"] = _clean(compact.get("claim"), 48 if emergency else 120)
        if emergency:
            for key in ("templateId", "familyId", "evidenceState", "approvalStatus", "scopeState"):
                compact.pop(key, None)
        id_limit = 1 if emergency else 3
        id_fields = (
            ("supportingEvidenceIds", "counterEvidenceIds")
            if emergency else
            ("supportingEvidenceIds", "counterEvidenceIds", "causalPathIds")
        )
        for key in id_fields:
            values = [str(value or "").strip() for value in row.get(key) or [] if str(value or "").strip()]
            if values:
                compact[key] = values[:id_limit]
        rows.append(compact)
    return rows[:12]


def _minimum_temporal_windows(value: object, *, emergency: bool = False) -> List[Dict[str, object]]:
    fields = (
        "windowKey", "lookbackDays", "lookbackMinutes", "sampleCount",
        "requiredSampleCount", "coveredSessionCount", "requiredSessionCount",
        "hasSufficientHistory", "lastObservedAt", "startPrice", "currentPrice", "priceChangePct",
        "drawdownFromPeakPct", "reboundFromTroughPct", "priceVelocityChangePct",
        "volumeRatioEnd", "tradeStrengthEnd", "bidAskImbalanceEnd",
        "smartMoneyDataState", "smartMoneyNetLatest",
    )
    if emergency:
        fields = (
            "windowKey", "sampleCount", "hasSufficientHistory",
            "startPrice", "priceChangePct", "drawdownFromPeakPct",
            "reboundFromTroughPct", "priceVelocityChangePct",
        )
    return [_selected_fields(item, fields) for item in list(value or [])[:12] if isinstance(item, dict)]


def _minimum_company_context(value: object, *, emergency: bool = False) -> Dict[str, object]:
    company = _mapping(value)
    financials = _mapping(company.get("latestFinancials"))
    financial_fields = (
        "period", "revenue", "revenueGrowthPct", "operatingIncome",
        "operatingIncomeGrowthPct", "netIncome", "netIncomeGrowthPct", "freeCashFlow",
    )
    annual = _compact_dict_rows(financials.get("annual"), financial_fields, 1)
    quarterly = _compact_dict_rows(financials.get("quarterly"), financial_fields, 1)
    payload = {
        **_selected_fields(
            company,
            ("symbol", "companyName", "factRevision", "materialRevision", "judgmentUse"),
        ),
        "profile": _selected_fields(company.get("profile"), ("sector", "industry", "country")),
        "valuation": _selected_fields(
            company.get("valuation"),
            (
                "peRatio", "forwardPE", "pbr", "pegRatio", "trailingEPS",
                "returnOnEquityPct", "dividendYieldPct", "enterpriseToEbitda",
            ),
        ),
        "latestFinancials": {
            "annual": annual,
            "quarterly": quarterly,
        },
        "coverage": _selected_fields(
            company.get("coverage"),
            ("dataState", "officialSource", "financialPeriods", "valuationFields"),
        ),
    }
    if emergency:
        payload["profile"] = _selected_fields(company.get("profile"), ("sector",))
        payload["valuation"] = _selected_fields(
            company.get("valuation"),
            ("peRatio", "forwardPE", "pbr", "pegRatio", "returnOnEquityPct"),
        )
        payload["latestFinancials"] = {"latest": (quarterly or annual)[:1]}
        payload["coverage"] = _selected_fields(company.get("coverage"), ("dataState",))
    return _bounded_value(
        payload,
        string_limit=72 if emergency else 100,
        list_limit=1 if emergency else 2,
        dict_limit=12,
    )


def _minimum_rule_rows(value: object, *, emergency: bool = False) -> List[Dict[str, object]]:
    fields = (
        ("ruleId", "label", "evidenceRole")
        if emergency else
        (
            "ruleId", "label", "relationType", "reviewLevel", "dataState",
            "evidenceRole", "evidenceState",
        )
    )
    return [
        _bounded_value(
            _selected_fields(item, fields),
            string_limit=72 if emergency else 100,
            list_limit=1,
            dict_limit=8,
        )
        for item in list(value or [])[:4]
        if isinstance(item, dict)
    ]


def _minimum_driver_rows(value: object, *, emergency: bool = False) -> List[Dict[str, object]]:
    fields = (
        ("category", "direction", "evidenceRole", "summary")
        if emergency else
        ("category", "direction", "evidenceRole", "label", "summary")
    )
    return [
        _bounded_value(
            _selected_fields(item, fields),
            string_limit=72 if emergency else 100,
            list_limit=1,
            dict_limit=6,
        )
        for item in list(value or [])[:2 if emergency else 4]
        if isinstance(item, dict)
    ]


def _minimum_market_evidence_profile(value: object) -> Dict[str, object]:
    profile = _mapping(value)
    capability_states = {}
    for key, capability in sorted(_mapping(profile.get("capabilities")).items()):
        state = _selected_fields(
            capability,
            ("state", "freshnessStatus", "latencyStatus", "judgementEvidenceUsable"),
        )
        if state:
            capability_states[str(key)] = state
    payload = _selected_fields(
        profile,
        (
            "profileKey", "label", "market", "currency", "dataState",
            "judgementEvidenceUsable", "requiredCapabilities",
            "confirmationCapabilities", "observableFollowUpFields",
        ),
    )
    if capability_states:
        payload["capabilities"] = capability_states
    unavailable = _compact_dict_rows(
        profile.get("unavailableCapabilities"),
        ("capability", "label", "state", "reason"),
        3,
    )
    if unavailable:
        payload["unavailableCapabilities"] = unavailable
    return _bounded_value(payload, string_limit=72, list_limit=20, dict_limit=16)


def _minimum_relation_facts(value: object, limit: int = 24) -> Dict[str, object]:
    facts = _mapping(value)
    compact: Dict[str, object] = {}
    market_profile = _minimum_market_evidence_profile(facts.get("marketEvidenceProfile"))
    if market_profile:
        compact["marketEvidenceProfile"] = market_profile
    for key in RELATION_FACT_PRIORITY:
        if key == "marketEvidenceProfile" or len(compact) >= max(1, int(limit or 1)):
            continue
        item = facts.get(key)
        if item not in (None, "", [], {}):
            compact[key] = item
    for key in sorted(facts):
        if key in compact or len(compact) >= max(1, int(limit or 1)):
            continue
        item = facts.get(key)
        if isinstance(item, (str, int, float, bool)) and item not in (None, ""):
            compact[key] = item
    return compact


def _minimum_decision_continuity(value: object) -> Dict[str, object]:
    packet = _mapping(value)
    payload = _selected_fields(packet, ("contractVersion", "status", "capturedAt"))
    previous = _selected_fields(
        packet.get("previousDecision"),
        (
            "action", "decisionReadiness", "decidedAt", "referenceDate",
            "decisionSummary", "invalidationCondition",
        ),
    )
    if previous:
        payload["previousDecision"] = _bounded_value(previous, string_limit=96, list_limit=1, dict_limit=8)
    selected = _selected_fields(
        packet.get("selectedHypothesis"),
        (
            "hypothesisId", "claim", "stance", "verificationStatus", "verdict",
            "supportingEvidenceIds", "counterEvidenceIds",
        ),
    )
    if selected:
        payload["selectedHypothesis"] = _bounded_value(selected, string_limit=72, list_limit=1, dict_limit=8)
    row_specs = (
        (
            "followUpConditions",
            ("conditionId", "field", "operator", "threshold", "purpose", "status", "currentValue"),
            2,
        ),
        (
            "observedOutcomes",
            (
                "outcomeId", "observedAt", "price", "profitLossRate",
                "priceChangeFromDecisionPct", "selectedHypothesisStatus",
            ),
            2,
        ),
        (
            "actionObservations",
            (
                "observationId", "observedAt", "priorAction", "observedDirection",
                "previousQuantity", "observedQuantity", "quantityDelta", "causalityClaimed",
            ),
            1,
        ),
    )
    for key, fields, limit in row_specs:
        rows = _compact_dict_rows(packet.get(key), fields, limit)
        if rows:
            payload[key] = _bounded_value(rows, string_limit=72, list_limit=limit, dict_limit=8)
    current_position = _selected_fields(
        packet.get("currentPosition"),
        (
            "symbol", "quantity", "sellableQuantity", "averagePrice", "currentPrice",
            "profitLossRate", "observationState",
        ),
    )
    if current_position:
        payload["currentPosition"] = current_position
    observation = _selected_fields(
        packet.get("observationState"),
        ("userAction", "outcome", "followUp", "noActionMeansHold", "causalityClaimed"),
    )
    if observation:
        payload["observationState"] = observation
    summary = _selected_fields(
        packet.get("summary"),
        (
            "followUpCount", "pendingFollowUpCount", "transitionedFollowUpCount",
            "outcomeCount", "actionObservationCount", "executionRecorded",
        ),
    )
    if summary:
        payload["summary"] = summary
    return payload


def _minimum_temporal_evidence_summary(value: object) -> Dict[str, object]:
    return _selected_fields(
        value,
        (
            "loadedWindowCount", "loadedWindowKeys", "sufficientWindowCount",
            "matchedWindowCount", "matchedWindowKeys", "temporalEvidenceFamilyCount",
            "interpretation",
        ),
    )


def _minimum_decision_evidence_summary(value: object) -> Dict[str, object]:
    return _selected_fields(
        value,
        (
            "totalHypothesisCount", "eligibleHypothesisCount",
            "eligibleFamilyCount", "referenceHypothesisCount",
        ),
    )


def _minimum_decision_brief(critical: Dict[str, object], *, emergency: bool = False) -> Dict[str, object]:
    decision_state = _mapping(critical.get("decisionState"))
    envelope = _mapping(decision_state.get("actionEnvelope"))
    current = _mapping(critical.get("currentSituation"))
    inference = _mapping(critical.get("inference"))
    hypothesis_set = _mapping(inference.get("hypothesisSet"))
    evidence = _mapping(critical.get("evidence"))
    research = _mapping(critical.get("research"))
    account_policy = _mapping(critical.get("accountPolicy"))
    assessment = _mapping(critical.get("assessmentBundle"))
    if emergency:
        decision_payload = _bounded_value(
            _selected_fields(
                decision_state.get("decision"),
                (
                    "primaryAction", "decisionEffect", "judgementBlocked",
                    "selectedRuleId", "targetRole",
                ),
            ),
            string_limit=72,
            list_limit=2,
            dict_limit=12,
        )
        envelope_payload = _bounded_value(
            _selected_fields(
                envelope,
                (
                    "status", "preferredAction", "allowedActions", "blockedActions",
                    "judgementBlocked", "selectedRuleId", "drivingRuleIds",
                    "blockingRuleIds", "constraintRuleIds", "targetRole",
                ),
            ),
            string_limit=72,
            list_limit=1,
            dict_limit=12,
        )
        assessment_payload = {
            "investmentOpinion": _bounded_value(
                _selected_fields(
                    assessment.get("investmentOpinion"),
                    (
                        "status", "candidateAction", "selectedRuleId", "judgementBlocked",
                        "decisionEffectCounts", "assessmentScope",
                    ),
                ),
                string_limit=72,
                list_limit=2,
                dict_limit=8,
            ),
            "executionReadiness": _bounded_value(
                _selected_fields(
                    assessment.get("executionReadiness"),
                    ("status", "selectedRuleId", "judgementBlocked", "assessmentScope"),
                ),
                string_limit=72,
                list_limit=1,
                dict_limit=6,
            ),
            "recommendedPlan": _bounded_value(
                _selected_fields(
                    assessment.get("recommendedPlan"),
                    (
                        "status", "type", "investmentAction", "planOption",
                        "executionConstraintRuleIds", "meaningPreserved",
                    ),
                ),
                string_limit=72,
                list_limit=1,
                dict_limit=7,
            ),
        }
        execution_plan_payload = _bounded_value(
            _selected_fields(
                inference.get("executionPlan"),
                (
                    "primaryAction", "candidateAction", "allowedActions", "blockedActions",
                    "decisionStage", "targetRole", "nextChecks", "missingDataImpact",
                ),
            ),
            string_limit=72,
            list_limit=1,
            dict_limit=9,
        )
        question_payload = _selected_fields(
            critical.get("question"),
            ("questionId", "intent", "horizon"),
        )
        research_evidence_payload = _bounded_value(
            _compact_dict_rows(
                evidence.get("researchEvidence"),
                (
                    "evidenceId", "kind", "title", "evidenceRole", "validationState",
                    "dataState", "source", "observedAt",
                ),
                1,
            ),
            string_limit=72,
            list_limit=1,
            dict_limit=8,
        )
        research_gaps_payload = _bounded_value(
            _compact_dict_rows(
                research.get("decisionChangingGaps"),
                ("taskId", "question", "decisionRelevance", "status"),
                1,
            ),
            string_limit=72,
            list_limit=1,
            dict_limit=4,
        )
        raw_coverage = _mapping(critical.get("dataCoverage"))
        coverage_payload = {
            "missingData": _bounded_value(
                _compact_dict_rows(
                    raw_coverage.get("missingData"),
                    ("key", "label", "effect"),
                    1,
                ),
                string_limit=72,
                list_limit=1,
                dict_limit=3,
            ),
            "internalDataAudit": _selected_fields(
                raw_coverage.get("internalDataAudit"),
                ("status", "loadedWindowCount", "requiredWindowCount", "cacheHit"),
            ),
            "temporalWindowCount": raw_coverage.get("temporalWindowCount"),
        }
        guardrails_payload = _selected_fields(
            critical.get("guardrails"),
            (
                "mustReviewEveryInputHypothesis", "mustRespectActionEnvelope",
                "mustIgnorePortfolioRebalancePolicy",
            ),
        )
    else:
        decision_payload = _bounded_value(
            decision_state.get("decision") or {},
            string_limit=100,
            list_limit=3,
            dict_limit=14,
        )
        envelope_payload = _selected_fields(
            envelope,
            (
                "status", "preferredAction", "allowedActions", "blockedActions",
                "judgementBlocked", "selectedRuleId", "drivingRuleIds",
                "blockingRuleIds", "constraintRuleIds", "targetRole",
                "invalidationConditions", "nextChecks",
            ),
        )
        assessment_payload = {
            key: _bounded_value(
                assessment.get(key),
                string_limit=100,
                list_limit=2,
                dict_limit=10,
            )
            for key in ("evidenceQuality", "investmentOpinion", "executionReadiness", "recommendedPlan")
            if assessment.get(key) not in (None, "", [], {})
        }
        execution_plan_payload = _bounded_value(
            inference.get("executionPlan") or {},
            string_limit=100,
            list_limit=3,
            dict_limit=12,
        )
        question_payload = _bounded_value(
            critical.get("question") or {}, string_limit=120, list_limit=2, dict_limit=8,
        )
        research_evidence_payload = _bounded_value(
            list(evidence.get("researchEvidence") or [])[:3],
            string_limit=120,
            list_limit=3,
            dict_limit=10,
        )
        research_gaps_payload = _bounded_value(
            list(research.get("decisionChangingGaps") or [])[:2],
            string_limit=120,
            list_limit=2,
            dict_limit=8,
        )
        coverage_payload = _bounded_value(
            critical.get("dataCoverage") or {},
            string_limit=100,
            list_limit=3,
            dict_limit=10,
        )
        guardrails_payload = critical.get("guardrails") or {}
    payload = {
        "schemaVersion": critical.get("schemaVersion"),
        "decisionContractVersion": critical.get("decisionContractVersion"),
        "messageType": critical.get("messageType"),
        "decisionPolicyScope": _selected_fields(
            critical.get("decisionPolicyScope"),
            ("name", "portfolioRebalancePolicy", "reason"),
        ),
        "executionProfile": _selected_fields(
            critical.get("executionProfile"),
            ("name", "reasoningEffort", "selectionReasons"),
        ),
        "question": question_payload,
        "subject": _selected_fields(
            critical.get("subject"),
            ("symbol", "name", "market", "targetRole", "referenceDate"),
        ),
        "decisionState": {
            "previousFinalDecision": _selected_fields(
                decision_state.get("previousFinalDecision"),
                ("action", "summary", "referenceDate"),
            ),
            "precomputedActionCandidate": decision_state.get("precomputedActionCandidate"),
            "decision": decision_payload,
            "actionEnvelope": envelope_payload,
            **_selected_fields(
                decision_state,
                (
                    "allowedActions", "blockedActions", "reviewLevel", "dataState",
                    "conflictState", "systemReadiness",
                ),
            ),
        },
        "decisionContinuity": (
            _minimum_decision_continuity(critical.get("decisionContinuity"))
            if emergency
            else _bounded_value(
                critical.get("decisionContinuity") or {},
                string_limit=120,
                list_limit=4,
                dict_limit=20,
            )
        ),
        "assessmentBundle": assessment_payload,
        "currentSituation": {
            "relationFacts": _bounded_value(
                (
                    _minimum_relation_facts(current.get("relationFacts"), 20)
                    if emergency
                    else _compact_relation_facts(current.get("relationFacts"), 24)
                ),
                string_limit=80 if emergency else 120,
                list_limit=14 if emergency else 24,
                dict_limit=14 if emergency else 24,
            ),
            "temporalWindows": _minimum_temporal_windows(
                current.get("temporalWindows"), emergency=emergency,
            ),
            "temporalEvidenceSummary": _bounded_value(
                (
                    _minimum_temporal_evidence_summary(current.get("temporalEvidenceSummary"))
                    if emergency
                    else current.get("temporalEvidenceSummary") or {}
                ),
                string_limit=80 if emergency else 120,
                list_limit=8,
                dict_limit=16,
            ),
            "companyContext": _minimum_company_context(
                current.get("companyContext"), emergency=emergency,
            ),
            "companyValuationContext": _bounded_value(
                current.get("companyValuationContext") or {},
                string_limit=100,
                list_limit=2,
                dict_limit=14,
            ),
        },
        "inference": {
            "activeRules": _minimum_rule_rows(
                inference.get("activeRules"), emergency=emergency,
            ),
            "executionPlan": execution_plan_payload,
            "decisionDrivers": _minimum_driver_rows(
                inference.get("decisionDrivers"), emergency=emergency,
            ),
            "hypothesisSet": {
                **_selected_fields(
                    hypothesis_set,
                    (
                        "hypothesisSetId", "questionId", "subjectSymbol",
                        "inferenceGenerationId", "minimumComparisonCount",
                    ),
                ),
                "decisionEvidenceSummary": (
                    _minimum_decision_evidence_summary(hypothesis_set.get("decisionEvidenceSummary"))
                    if emergency
                    else hypothesis_set.get("decisionEvidenceSummary") or {}
                ),
                "hypotheses": _minimum_hypotheses(
                    hypothesis_set.get("hypotheses"), emergency=emergency,
                ),
                "referenceHypotheses": (
                    []
                    if emergency
                    else _bounded_value(
                        list(hypothesis_set.get("referenceHypotheses") or [])[:2],
                        string_limit=80,
                        list_limit=2,
                        dict_limit=6,
                    )
                ),
            },
        },
        "evidence": {
            "researchEvidence": research_evidence_payload,
        },
        "research": {
            "decisionChangingGaps": research_gaps_payload,
            "verifiedEvidenceAvailable": research.get("verifiedEvidenceAvailable"),
        },
        "dataCoverage": coverage_payload,
        "accountPolicy": {
            "actionPolicy": account_policy.get("actionPolicy"),
            "decisionPolicyScope": _selected_fields(
                account_policy.get("decisionPolicyScope"),
                ("name", "portfolioRebalancePolicy"),
            ),
            "portfolioLifecycle": _bounded_value(
                account_policy.get("portfolioLifecycle") or {},
                string_limit=100,
                list_limit=2,
                dict_limit=10,
            ),
        },
        "guardrails": guardrails_payload,
        "contextBudget": {
            **_mapping(critical.get("contextBudget")),
            "status": "emergency-decision-contract" if emergency else "minimum-decision-contract",
        },
    }
    if emergency:
        payload["currentSituation"].pop("companyValuationContext", None)
        payload["assessmentBundle"].pop("evidenceQuality", None)
        payload["contextBudget"].pop("reason", None)
        if _clean(_mapping(critical.get("decisionPolicyScope")).get("name"), 40).lower() != "portfolio-rebalance":
            payload["accountPolicy"].pop("portfolioLifecycle", None)
    return payload


def bounded_decision_brief(brief: Dict[str, object], budget_bytes: int) -> Dict[str, object]:
    budget = max(6 * 1024, int(budget_bytes or 6 * 1024))
    critical = _critical_decision_brief(brief)
    for string_limit, list_limit, dict_limit in (
        (260, 16, 64),
        (200, 16, 56),
        (150, 16, 48),
        (110, 16, 40),
    ):
        bounded = _bounded_value(
            critical,
            string_limit=string_limit,
            list_limit=list_limit,
            dict_limit=dict_limit,
        )
        if _json_bytes(bounded) <= budget:
            return bounded
    compact = _bounded_value(critical, string_limit=72, list_limit=16, dict_limit=32)
    compact["contextBudget"] = dict(critical.get("contextBudget") or {})
    compact["contextBudget"]["status"] = "minimum-decision-contract"
    compact["contextBudget"]["reason"] = "용량 한도에서도 기간 식별자와 경쟁 가설 ID를 보존했습니다."
    if _json_bytes(compact) <= budget:
        return compact
    minimum = _minimum_decision_brief(critical)
    if _json_bytes(minimum) <= budget:
        return minimum
    emergency = _minimum_decision_brief(critical, emergency=True)
    if _json_bytes(emergency) <= budget:
        return emergency
    raise ValueError(
        "AI decision brief cannot preserve the minimum ontology contract within "
        + str(budget)
        + " bytes"
    )


def build_notification_ai_prompt_bundle(
    context: Dict[str, object],
    settings: Dict[str, object] = None,
    max_prompt_bytes: int = 0,
    profile: Dict[str, object] = None,
    decision_brief: Dict[str, object] = None,
) -> Dict[str, object]:
    execution_profile = dict(profile or notification_ai_execution_profile(context, settings))
    maximum = max(12 * 1024, int(max_prompt_bytes or execution_profile.get("maxPromptBytes") or 16 * 1024))
    brief = dict(decision_brief or notification_ai_decision_brief(context, settings, execution_profile))
    release = active_notification_ai_prompt_release(settings)
    decision_core, routing_audit = route_notification_ai_decision_context(brief)
    policy_scope = _mapping(decision_core.get("policyScope"))
    policy_scope_instruction = (
        "instrument-market 판단에서는 현금, 배분, 집중도, 외화 노출과 리밸런싱 정책을 행동 근거로 사용하지 않는다."
        if policy_scope.get("name") == INSTRUMENT_MARKET_SCOPE
        else "portfolio-rebalance 판단에서는 입력된 포트폴리오 정책과 배분 이탈만 비교한다."
    )
    instructions = [*release.instructions, policy_scope_instruction]
    if release.policy_flags:
        instructions.append(
            "운영 정책 플래그: "
            + json.dumps(release.policy_flags, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    instructions.extend([
        "PromptRelease: " + json.dumps({
            "version": release.version,
            "contractVersion": release.contract_version,
            "fingerprint": release.fingerprint,
        }, ensure_ascii=False, separators=(",", ":")),
        "응답 스키마: " + json.dumps(release.response_schema, ensure_ascii=False, separators=(",", ":")),
        "DecisionCore:",
    ])
    instruction_bytes = len("\n".join(instructions).encode("utf-8")) + 1
    payload = fit_notification_ai_decision_core(
        decision_core,
        max(6 * 1024, maximum - instruction_bytes),
    )
    rendered = "\n".join([*instructions, json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)])
    rendered_bytes = len(rendered.encode("utf-8"))
    if rendered_bytes > maximum:
        raise ValueError(
            "AI decision prompt exceeded its hard limit: "
            + str(rendered_bytes)
            + " > "
            + str(maximum)
            + " bytes"
        )
    return {
        "prompt": rendered,
        "decisionCore": payload,
        "decisionBrief": brief,
        "contextRouting": routing_audit,
        "promptRelease": release.to_public_dict(),
    }


def build_notification_ai_decision_prompt(
    context: Dict[str, object],
    settings: Dict[str, object] = None,
    max_prompt_bytes: int = 0,
    profile: Dict[str, object] = None,
    decision_brief: Dict[str, object] = None,
) -> str:
    return str(build_notification_ai_prompt_bundle(
        context,
        settings,
        max_prompt_bytes=max_prompt_bytes,
        profile=profile,
        decision_brief=decision_brief,
    )["prompt"])
