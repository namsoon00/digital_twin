"""Route a full decision audit into one relevance-bounded AI decision core.

The full world snapshot remains persisted for audit.  This module only selects
facts already connected to the current TypeDB decision synthesis; it never
scores evidence or chooses an investment action.
"""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Tuple


AI_DECISION_CONTEXT_ROUTE_VERSION = "notification-ai-context-route-v1"
AI_DECISION_CORE_VERSION = "investment-ai-decision-core-v1"


CORE_FACT_KEYS = (
    "currentPrice", "averagePrice", "profitLossRate", "profitLossRateDeltaPct",
    "quantity", "sellableQuantity", "marketValue", "positionWeight", "sectorWeight",
    "volume", "volumeRatio", "timeAdjustedVolumeRatio", "tradeStrength",
    "buyVolume", "sellVolume", "bidAskImbalance", "foreignNetVolume",
    "institutionNetVolume", "individualNetVolume", "smartMoneyNetVolume",
    "ma5", "ma20", "ma60", "ma5Distance", "ma20Distance", "ma60Distance",
    "ma20Slope", "ma60Slope", "priceChangeRate", "currency", "market",
)

VALUATION_MARKERS = (
    "valuation", "fair_value", "fundamental", "financial", "earnings",
    "company_value", "기업가치", "밸류", "실적", "재무",
)
EXTERNAL_EVIDENCE_MARKERS = (
    "news", "disclosure", "filing", "research", "event_risk",
    "뉴스", "공시", "리서치",
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _clean(value: object, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:max(1, int(limit or 1))]


def _sentence_text(value: object, limit: int = 320) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    candidate = text[: max(1, limit - 3)].rstrip()
    for separator in (". ", "다. ", "; ", " / ", " "):
        position = candidate.rfind(separator)
        if position >= max(24, int(limit * 0.55)):
            candidate = candidate[: position + (1 if separator != " " else 0)].rstrip()
            break
    return candidate.rstrip(".,;/ ") + "..."


def _selected(value: object, fields: Iterable[str]) -> Dict[str, object]:
    row = _mapping(value)
    return {
        key: row.get(key)
        for key in fields
        if row.get(key) not in (None, "", [], {})
    }


def _unique(values: Iterable[object], limit: int = 24) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            rows.append(text)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def _rule_linked_fact_keys(rules: List[Dict[str, object]], drivers: List[Dict[str, object]]) -> List[str]:
    keys: List[str] = []
    for rule in rules:
        evidence_state = _mapping(rule.get("evidenceState"))
        keys.extend(evidence_state.get("appliedFactFields") or [])
        for requirement in rule.get("ruleRequiredFacts") or []:
            text = str(requirement or "").strip()
            if ":field:" in text.lower():
                keys.append(text.lower().split(":field:", 1)[1].split(":", 1)[0])
            elif text:
                keys.append(text)
    for driver in drivers:
        keys.extend(driver.get("dataKeys") or [])
    return _unique(keys, 48)


def _active_rule_rows(inference: Dict[str, object], envelope: Dict[str, object]) -> List[Dict[str, object]]:
    hypothesis_set = _mapping(inference.get("hypothesisSet"))
    hypotheses = [item for item in hypothesis_set.get("hypotheses") or [] if isinstance(item, dict)]
    linked_ids = _unique([
        envelope.get("selectedRuleId"),
        *(envelope.get("drivingRuleIds") or []),
        *(envelope.get("blockingRuleIds") or []),
        *(envelope.get("constraintRuleIds") or []),
        *(envelope.get("supportRuleIds") or []),
        *[
            rule_id
            for hypothesis in hypotheses
            for rule_id in hypothesis.get("supportingRuleIds") or []
        ],
    ], 24)
    rows: List[Dict[str, object]] = []
    seen_rule_ids = set()
    for item in inference.get("activeRules") or []:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("ruleId") or "").strip()
        if not rule_id or rule_id in seen_rule_ids:
            continue
        evidence_state = _mapping(item.get("evidenceState"))
        if evidence_state.get("evidenceUsableForJudgement") is False:
            continue
        if str(evidence_state.get("inferenceEligibilityStatus") or "eligible") != "eligible":
            continue
        if linked_ids and rule_id not in linked_ids:
            continue
        row = _selected(
            item,
            (
                "ruleId", "label", "relationType", "evidenceRole", "dataState",
                "reviewLevel", "ruleRequiredFacts",
            ),
        )
        applied = _unique(evidence_state.get("appliedFactFields") or [], 12)
        if applied:
            row["appliedFactFields"] = applied
        if row:
            seen_rule_ids.add(rule_id)
            rows.append(row)
    priority = {rule_id: index for index, rule_id in enumerate(linked_ids)}
    rows.sort(key=lambda item: (priority.get(str(item.get("ruleId") or ""), len(priority)), str(item.get("ruleId") or "")))
    return rows[:8]


def _hypothesis_rows(inference: Dict[str, object]) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    source = _mapping(inference.get("hypothesisSet"))
    known_rule_ids = {
        str(item.get("ruleId") or "").strip()
        for item in inference.get("activeRules") or []
        if (
            isinstance(item, dict)
            and str(item.get("ruleId") or "").strip()
            and _mapping(item.get("evidenceState")).get("evidenceUsableForJudgement") is not False
            and str(_mapping(item.get("evidenceState")).get("inferenceEligibilityStatus") or "eligible") == "eligible"
        )
    }
    rows: List[Dict[str, object]] = []
    for item in source.get("hypotheses") or []:
        if not isinstance(item, dict) or not str(item.get("hypothesisId") or "").strip():
            continue
        row = _selected(
            item,
            (
                "hypothesisId", "templateId", "familyId", "causalSignature",
                "familySource", "mergedRuleCount", "stance", "evidenceState",
                "verificationStatus", "approvalStatus", "scopeState", "horizon",
                "marketHypothesisId", "accountHypothesisOverlayId",
            ),
        )
        row["claim"] = _sentence_text(item.get("claim"), 320)
        for key in (
            "supportingRuleIds", "supportingEvidenceIds", "counterEvidenceIds",
            "causalPathIds", "requiredEvidenceTypes", "invalidationConditions",
        ):
            values = _unique(item.get(key) or [], 5)
            if values:
                row[key] = values
        if not row.get("supportingRuleIds"):
            template_id = str(row.get("templateId") or "").strip()
            template_rule_id = (
                template_id.split("hypothesis-template:", 1)[1]
                if template_id.startswith("hypothesis-template:")
                else ""
            )
            if template_rule_id in known_rule_ids:
                row["supportingRuleIds"] = [template_rule_id]
        if not set(row.get("supportingRuleIds") or []).intersection(known_rule_ids):
            continue
        rows.append(row)
        if len(rows) >= 12:
            break
    available_count = len(rows)
    try:
        required_minimum = max(1, int(float(str(source.get("minimumComparisonCount") or 1))))
    except (TypeError, ValueError):
        required_minimum = 1
    metadata = _selected(
        source,
        (
            "hypothesisSetId", "questionId", "subjectSymbol", "inferenceGenerationId",
            "scopeVersion", "createdAt",
        ),
    )
    metadata["comparisonRequired"] = bool(rows)
    metadata["minimumComparisonCount"] = min(required_minimum, available_count) if available_count else 0
    metadata["requiredMinimumComparisonCount"] = required_minimum
    source_summary = _mapping(source.get("decisionEvidenceSummary"))
    family_ids = {
        str(item.get("familyId") or item.get("causalSignature") or item.get("hypothesisId") or "").strip()
        for item in rows
        if str(item.get("familyId") or item.get("causalSignature") or item.get("hypothesisId") or "").strip()
    }
    metadata["decisionEvidenceSummary"] = {
        "totalHypothesisCount": available_count,
        "eligibleHypothesisCount": available_count,
        "eligibleFamilyCount": len(family_ids),
        "referenceHypothesisCount": int(source_summary.get("referenceHypothesisCount") or 0),
    }
    return metadata, rows


def _market_evidence_profile(value: object, facts: Dict[str, object]) -> Dict[str, object]:
    profile = _mapping(value)
    if not profile:
        return {}
    capabilities = {}
    for key, raw in sorted(_mapping(profile.get("capabilities")).items()):
        state = _selected(raw, ("state", "freshnessStatus", "latencyStatus", "judgementEvidenceUsable"))
        if str(state.get("state") or "").lower() in {"notapplicable", "not-applicable"}:
            continue
        if state:
            capabilities[str(key)] = state
    observable = [
        key for key in _unique(profile.get("observableFollowUpFields") or [], 24)
        if key in facts
    ]
    payload = _selected(
        profile,
        (
            "profileKey", "label", "market", "currency", "dataState",
            "judgementEvidenceUsable", "requiredCapabilities", "confirmationCapabilities",
        ),
    )
    if observable:
        payload["observableFollowUpFields"] = observable
    if capabilities:
        payload["capabilities"] = capabilities
    return payload


def _relation_facts(current: Dict[str, object], rules: List[Dict[str, object]], drivers: List[Dict[str, object]]) -> Dict[str, object]:
    facts = _mapping(current.get("relationFacts"))
    casefold_keys = {str(key).casefold(): key for key in facts}
    requested = list(CORE_FACT_KEYS)
    for key in _rule_linked_fact_keys(rules, drivers):
        original = casefold_keys.get(str(key).casefold())
        if original:
            requested.append(original)
    payload = {
        key: facts.get(key)
        for key in _unique(requested, 40)
        if facts.get(key) not in (None, "", [], {})
        and not isinstance(facts.get(key), (dict, list))
    }
    market_profile = _market_evidence_profile(facts.get("marketEvidenceProfile"), facts)
    if market_profile:
        payload["marketEvidenceProfile"] = market_profile
    return payload


def _temporal_evidence(current: Dict[str, object]) -> Dict[str, object]:
    summary = _mapping(current.get("temporalEvidenceSummary"))
    matched_keys = _unique(summary.get("matchedWindowKeys") or [], 12)
    matched = {
        key.upper() for key in matched_keys
    }
    windows = []
    for item in current.get("temporalWindows") or []:
        if not isinstance(item, dict):
            continue
        window_key = str(item.get("windowKey") or "").upper().strip()
        if window_key not in matched:
            continue
        windows.append(_selected(
            item,
            (
                "windowKey", "lookbackDays", "lookbackMinutes", "sampleCount",
                "hasSufficientHistory", "startPrice", "currentPrice", "priceChangePct",
                "drawdownFromPeakPct", "reboundFromTroughPct", "priceVelocityChangePct",
                "volumeRatioEnd", "tradeStrengthEnd", "bidAskImbalanceEnd",
                "smartMoneyDataState", "smartMoneyNetLatest",
            ),
        ))
    return {
        "loadedWindowCount": summary.get("loadedWindowCount") or len(current.get("temporalWindows") or []),
        "matchedWindowCount": len(windows),
        "matchedWindowKeys": matched_keys,
        "windows": windows[:8],
        "evidenceRole": "rule-matched-only",
    }


def _marker_relevant(rules: List[Dict[str, object]], hypotheses: List[Dict[str, object]], markers: Tuple[str, ...]) -> bool:
    text = " ".join(
        str(value or "")
        for row in [*rules, *hypotheses]
        for value in row.values()
        if isinstance(value, str)
    ).casefold()
    return any(marker.casefold() in text for marker in markers)


def _company_context(current: Dict[str, object], rules: List[Dict[str, object]], hypotheses: List[Dict[str, object]], facts: Dict[str, object]) -> Tuple[Dict[str, object], Dict[str, object]]:
    company = _mapping(current.get("companyContext"))
    if not company:
        return {}, {}
    relevant = bool(facts.get("valuationDecisionEligible")) or _marker_relevant(rules, hypotheses, VALUATION_MARKERS)
    profile = _selected(company.get("profile"), ("sector", "industry", "country", "exchange"))
    coverage = _selected(company.get("coverage"), ("dataState", "officialSource", "financialPeriods", "valuationFields"))
    if not relevant:
        return {}, {
            "role": "reference-only",
            "company": _selected(company, ("symbol", "companyName", "judgmentUse")),
            "profile": profile,
            "coverage": coverage,
        }
    financials = _mapping(company.get("latestFinancials"))
    financial_fields = (
        "period", "revenue", "revenueGrowthPct", "operatingIncome",
        "operatingIncomeGrowthPct", "operatingMarginPct", "netIncome",
        "netIncomeGrowthPct", "freeCashFlow",
    )
    return {
        **_selected(company, ("symbol", "companyName", "factRevision", "materialRevision", "judgmentUse")),
        "profile": profile,
        "valuation": _selected(
            company.get("valuation"),
            (
                "peRatio", "forwardPE", "pbr", "pegRatio", "trailingEPS",
                "returnOnEquityPct", "dividendYieldPct", "enterpriseToEbitda",
            ),
        ),
        "latestFinancials": {
            "annual": [_selected(item, financial_fields) for item in list(financials.get("annual") or [])[:1] if isinstance(item, dict)],
            "quarterly": [_selected(item, financial_fields) for item in list(financials.get("quarterly") or [])[:1] if isinstance(item, dict)],
        },
        "coverage": coverage,
    }, {}


def _continuity_delta(value: object) -> Dict[str, object]:
    packet = _mapping(value)
    previous = _mapping(packet.get("previousDecision"))
    selected = _mapping(packet.get("selectedHypothesis"))
    followups = []
    for item in packet.get("followUpConditions") or []:
        if not isinstance(item, dict):
            continue
        row = _selected(
            item,
            ("field", "operator", "threshold", "purpose", "status", "observedValue", "onSatisfied"),
        )
        if row.get("onSatisfied"):
            row["onSatisfied"] = _sentence_text(row.get("onSatisfied"), 120)
        followups.append(row)
    previous_payload = _selected(previous, ("action", "decisionReadiness", "decidedAt"))
    previous_summary = _sentence_text(previous.get("decisionSummary"), 180)
    if previous_summary:
        previous_payload["summary"] = previous_summary
    payload = {
        "status": packet.get("status"),
        "previousDecision": previous_payload,
        "previousSelectedHypothesisId": selected.get("hypothesisId") or previous.get("selectedHypothesisId"),
        "followUpConditions": followups[:2],
        "observationState": _selected(packet.get("observationState"), ("userAction", "outcome", "followUp", "causalityClaimed")),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _external_evidence(brief: Dict[str, object], rules: List[Dict[str, object]], hypotheses: List[Dict[str, object]]) -> List[Dict[str, object]]:
    if not _marker_relevant(rules, hypotheses, EXTERNAL_EVIDENCE_MARKERS):
        return []
    evidence = _mapping(brief.get("evidence"))
    linked_ids = {
        str(value or "")
        for hypothesis in hypotheses
        for key in ("supportingEvidenceIds", "counterEvidenceIds")
        for value in hypothesis.get(key) or []
        if str(value or "")
    }
    rows: List[Dict[str, object]] = []
    seen = set()

    def append_row(row: Dict[str, object], evidence_use: str) -> None:
        identity = str(row.get("evidenceId") or row.get("title") or row.get("reportName") or "").strip()
        if not identity or identity.casefold() in seen:
            return
        seen.add(identity.casefold())
        row["evidenceUse"] = evidence_use
        rows.append(row)

    for item in evidence.get("researchEvidence") or []:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidenceId") or "")
        validation = str(item.get("validationState") or "").lower()
        directly_linked = bool(evidence_id and evidence_id in linked_ids)
        verified = validation in {"verified", "approved", "complete", "sufficient"}
        if directly_linked and verified:
            evidence_use = "action"
        elif validation in {"ready", "conditional", "partial", "verified", "approved", "complete", "sufficient"}:
            evidence_use = "rule-scoped-reference"
        else:
            continue
        append_row(
            _selected(
                item,
                (
                    "evidenceId", "kind", "title", "summary", "evidenceRole", "polarity",
                    "validationState", "dataState", "source", "publishedAt", "observedAt", "url",
                ),
            ),
            evidence_use,
        )
        if len(rows) >= 3:
            break
    for item in evidence.get("newsHeadlines") or []:
        if isinstance(item, dict) and len(rows) < 3:
            append_row(
                _selected(item, ("evidenceId", "title", "summary", "stockImpactLabel", "domain", "seenDate", "url")),
                "rule-scoped-reference",
            )
    disclosure = _mapping(evidence.get("disclosure"))
    if disclosure and len(rows) < 3:
        append_row(
            _selected(disclosure, ("evidenceId", "reportName", "receiptDate", "provider", "url")),
            "rule-scoped-reference",
        )
    return [row for row in rows if row][:3]


def _portfolio_policy(brief: Dict[str, object]) -> Dict[str, object]:
    scope = _mapping(brief.get("decisionPolicyScope"))
    if str(scope.get("portfolioRebalancePolicy") or "").lower() not in {"included", "true", "1"}:
        return {}
    account_policy = _mapping(brief.get("accountPolicy"))
    lifecycle = _mapping(account_policy.get("portfolioLifecycle"))
    mandate = _selected(
        lifecycle.get("mandate"),
        (
            "profile", "max_position_weight_pct", "max_sector_weight_pct",
            "fx_exposure_review_pct", "cash_floor_pct", "loss_budget_pct",
        ),
    )
    rebalance_state = _selected(
        lifecycle.get("rebalanceState"),
        ("status", "breachKeys", "maximumNotionalBySymbol", "revision"),
    )
    exposure = _mapping(lifecycle.get("exposureSnapshot"))
    metrics = [
        _selected(
            item,
            ("exposure_type", "key", "ratio_pct", "policy_limit_pct", "policyDeltaPct"),
        )
        for item in exposure.get("metrics") or []
        if isinstance(item, dict)
    ]
    payload = {
        "actionPolicy": account_policy.get("actionPolicy"),
        "mandate": mandate,
        "exposureSnapshot": {
            **_selected(exposure, ("observedAt", "dataState")),
            "metrics": [item for item in metrics if item][:12],
        },
        "rebalanceState": rebalance_state,
        "rebalanceProposal": _selected(lifecycle.get("rebalanceProposal"), ("status", "reason", "revision")),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _missing_data(value: object, company_relevant: bool, linked_fact_keys: List[str]) -> List[Dict[str, object]]:
    rows = []
    linked = " ".join(linked_fact_keys).casefold()
    for item in value or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get(key) or "") for key in ("key", "label", "effect")).casefold()
        valuation_gap = any(marker.casefold() in text for marker in VALUATION_MARKERS)
        generally_relevant = any(token in text for token in ("price", "volume", "fresh", "수급", "가격", "거래", "신선"))
        linked_relevant = any(key.casefold() in text for key in linked_fact_keys if key)
        if valuation_gap and not company_relevant:
            continue
        if linked and not (valuation_gap or generally_relevant or linked_relevant):
            continue
        rows.append(_selected(item, ("key", "label", "effect", "state")))
    return [row for row in rows if row][:4]


def route_notification_ai_decision_context(brief: Dict[str, object]) -> Tuple[Dict[str, object], Dict[str, object]]:
    current = _mapping(brief.get("currentSituation"))
    inference = _mapping(brief.get("inference"))
    decision_state = _mapping(brief.get("decisionState"))
    envelope = _mapping(decision_state.get("actionEnvelope"))
    hypothesis_metadata, hypotheses = _hypothesis_rows(inference)
    rules = _active_rule_rows(inference, envelope)
    drivers = []
    seen_drivers = set()
    for item in inference.get("decisionDrivers") or []:
        if not isinstance(item, dict):
            continue
        row = _selected(item, ("category", "direction", "evidenceRole", "label", "dataKeys", "summary"))
        if row.get("summary"):
            row["summary"] = _sentence_text(row.get("summary"), 160)
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if row and key not in seen_drivers:
            seen_drivers.add(key)
            drivers.append(row)
        if len(drivers) >= 4:
            break
    facts = _relation_facts(current, rules, drivers)
    linked_fact_keys = _rule_linked_fact_keys(rules, drivers)
    company, company_reference = _company_context(current, rules, hypotheses, _mapping(current.get("relationFacts")))
    external_evidence = _external_evidence(brief, rules, hypotheses)
    temporal = _temporal_evidence(current)
    data_coverage = _mapping(brief.get("dataCoverage"))
    assessment = _mapping(brief.get("assessmentBundle"))
    core = {
        "schemaVersion": AI_DECISION_CORE_VERSION,
        "question": _selected(brief.get("question"), ("questionId", "intent", "horizon", "text")),
        "subject": _selected(brief.get("subject"), ("symbol", "name", "market", "targetRole", "referenceDate")),
        "decision": {
            "previousAction": _mapping(decision_state.get("previousFinalDecision")).get("action"),
            "precomputedActionCandidate": decision_state.get("precomputedActionCandidate"),
            "typeDbDecision": _selected(decision_state.get("decision"), ("primaryAction", "decisionEffect", "judgementBlocked", "targetRole")),
            "actionEnvelope": _selected(envelope, ("status", "preferredAction", "allowedActions", "blockedActions", "judgementBlocked", "selectedRuleId", "blockingRuleIds", "targetRole")),
            "transition": _selected(decision_state.get("decisionTransition"), ("kind", "changed", "material", "previousAction", "currentAction", "summary")),
            "readiness": _selected(
                decision_state.get("systemReadiness"),
                (
                    "status", "state", "evaluated", "minimumEligibleFamilyCount",
                    "eligibleHypothesisCount", "eligibleFamilyCount",
                    "referenceHypothesisCount", "selectedCoreInferenceEligible",
                ),
            ),
            "investmentOpinionStatus": _mapping(assessment.get("investmentOpinion")).get("status"),
            "executionReadinessStatus": _mapping(assessment.get("executionReadiness")).get("status"),
            "recommendedPlanStatus": _mapping(assessment.get("recommendedPlan")).get("status"),
        },
        "continuityDelta": _continuity_delta(brief.get("decisionContinuity")),
        "facts": facts,
        "temporalEvidence": temporal,
        "companyEvidence": company,
        "rules": rules,
        "hypothesisSet": {
            **hypothesis_metadata,
            "hypotheses": hypotheses,
        },
        "externalEvidence": external_evidence,
        "background": company_reference,
        "dataLimits": _missing_data(data_coverage.get("missingData"), bool(company), linked_fact_keys),
        "policyScope": _selected(brief.get("decisionPolicyScope"), ("name", "portfolioRebalancePolicy")),
        "portfolioPolicy": _portfolio_policy(brief),
    }
    core = {key: value for key, value in core.items() if value not in (None, "", [], {})}
    route_audit = {
        "version": AI_DECISION_CONTEXT_ROUTE_VERSION,
        "status": "routed",
        "included": {
            "hypothesisCount": len(hypotheses),
            "ruleCount": len(rules),
            "factCount": len(facts),
            "matchedTemporalWindowCount": len(temporal.get("windows") or []),
            "externalEvidenceCount": len(external_evidence),
            "actionExternalEvidenceCount": len([
                item for item in external_evidence if item.get("evidenceUse") == "action"
            ]),
            "referenceExternalEvidenceCount": len([
                item for item in external_evidence if item.get("evidenceUse") != "action"
            ]),
            "companyEvidence": bool(company),
            "companyReferenceOnly": bool(company_reference),
            "continuityDelta": bool(core.get("continuityDelta")),
        },
        "excluded": {
            "unmatchedTemporalWindowCount": max(0, len(current.get("temporalWindows") or []) - len(temporal.get("windows") or [])),
            "referenceHypothesisCount": len(_mapping(inference.get("hypothesisSet")).get("referenceHypotheses") or []),
            "nonApplicableCapabilityCount": len([
                item for item in _mapping(_mapping(current.get("relationFacts")).get("marketEvidenceProfile")).get("unavailableCapabilities") or []
                if isinstance(item, dict) and str(item.get("state") or "").lower() in {"notapplicable", "not-applicable"}
            ]),
            "unlinkedResearchEvidenceCount": max(0, len(_mapping(brief.get("evidence")).get("researchEvidence") or []) - len(external_evidence)),
        },
        "fullDecisionBriefRetainedForAudit": True,
    }
    core["routingAudit"] = route_audit
    return core, route_audit


def fit_notification_ai_decision_core(core: Dict[str, object], budget_bytes: int) -> Dict[str, object]:
    """Reduce reference detail only; never remove or truncate hypothesis IDs."""

    budget = max(6 * 1024, int(budget_bytes or 6 * 1024))
    fitted = json.loads(json.dumps(core, ensure_ascii=False, default=str))
    if _json_bytes(fitted) <= budget:
        return fitted
    fitted.pop("background", None)
    if _json_bytes(fitted) <= budget:
        fitted["routingAudit"]["status"] = "reference-trimmed"
        return fitted
    fitted["externalEvidence"] = list(fitted.get("externalEvidence") or [])[:2]
    fitted["decisionDrivers"] = list(fitted.get("decisionDrivers") or [])[:4]
    if _json_bytes(fitted) <= budget:
        fitted["routingAudit"]["status"] = "reference-trimmed"
        return fitted
    for rule in fitted.get("rules") or []:
        if isinstance(rule, dict):
            rule["evidence"] = list(rule.get("evidence") or [])[:1]
    fitted["externalEvidence"] = list(fitted.get("externalEvidence") or [])[:1]
    if _json_bytes(fitted) <= budget:
        fitted["routingAudit"]["status"] = "reference-trimmed"
        return fitted
    raise ValueError(
        "AI decision core cannot preserve TypeDB hypotheses within "
        + str(budget)
        + " bytes"
    )
