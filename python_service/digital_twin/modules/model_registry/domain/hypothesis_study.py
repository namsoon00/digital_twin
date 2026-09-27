"""Measurement and retention readiness; never a substitute for empirical outcomes."""
from math import ceil


STUDY_VERSION = "hypothesis-study-readiness-v1"
FAMILY_MEASUREMENTS = {
    "event-absorption": {"premise": ["verifiedEventCount"], "result": ["excessReturnPct"],
                         "source": "dated-disclosure-and-market-quotes", "horizons": [1440, 10080]},
    "fundamental-continuity": {"premise": ["revenueGrowthPct", "freeCashFlowMarginPct", "operatingIncomeGrowthPct"],
                               "result": ["instrumentReturnPct"], "source": "next-published-financial-period", "horizons": [129600, 259200]},
    "valuation-gap": {"premise": ["valuationGapReductionPp"], "result": ["instrumentReturnPct"],
                       "source": "two-reproducible-same-method-valuation-bundles", "horizons": [10080, 43200]},
}


def study_readiness(rule, policy):
    claim = rule.get("claim_contract") or rule.get("claimContract") or {}
    outcome = claim.get("outcomeContract") or {}
    criteria = outcome.get("criteria") or []
    horizons = sorted({int(value) for value in outcome.get("outcomeHorizonMinutes") or [] if int(value) > 0})
    target = str(claim.get("predictionTarget") or "").lower()
    family = ("valuation-gap" if "valuation" in target or "value" in target else
              "fundamental-continuity" if any(row.get("metric") in FAMILY_MEASUREMENTS["fundamental-continuity"]["premise"] for row in criteria)
              else "event-absorption" if any(row.get("metric") == "verifiedEventCount" for row in criteria) else "price-prediction")
    required = FAMILY_MEASUREMENTS.get(family, {})
    metrics = {row.get("metric") for row in criteria}
    gaps = []
    if required and not metrics.intersection(required["premise"]):
        gaps.append({"kind": "measurement-contract", "requirement": required["premise"],
                     "reason": "price-outcome-does-not-measure-authored-premise"})
    if not horizons:
        gaps.append({"kind": "horizon", "reason": "authored-outcome-horizon-required"})
    samples = int(policy.get("minimumIndependentPairs") or 20)
    spacing = int(policy.get("independenceMinutes") or 1440)
    primary = min(horizons) if horizons else 0
    days = ceil(max(primary, spacing) * samples / 1440) if primary else None
    delay = int(outcome.get("maximumObservationDelayMinutes") or 180)
    review = int(policy.get("experimentEvidenceRetentionDays") or 7)
    available = int(policy.get("maximumShadowDays") or 30)
    if days and days > available:
        gaps.append({"kind": "study-duration", "reason": "longitudinal-research-required", "minimumDays": days, "configuredDays": available})
    return {"contract": STUDY_VERSION, "family": family, "measurementSource": required.get("source", "frozen-market-observation"),
            "state": "blocked" if gaps else "design-ready", "gaps": gaps,
            "primaryHorizonMinutes": primary, "horizons": horizons, "minimumIndependentObservations": samples,
            "minimumAcquisitionDays": days, "minimumRetentionDays": (days * 2 + ceil(delay / 1440) + review) if days else None,
            "retentionBasis": "pre-adoption-and-post-adoption-plus-publication-delay-and-review",
            "causalAttribution": "not-established", "empiricalStatus": "not-evaluated",
            "nextRequiredFact": gaps[0] if gaps else {"kind": "future-outcomes", "requirement": "preregistered-independent-observations"}}


def development_readiness(case, policy):
    row = case.to_dict() if hasattr(case, "to_dict") else dict(case)
    status = row.get("status") or ""
    stage = (row.get("progress") or {}).get("stage") or row.get("stage") or ""
    retry = row.get("retry") or {}
    blockers = retry.get("compilationBlockers") or retry.get("blockers") or row.get("blockers") or []
    if status in {"superseded", "rejected", "deployed", "adopted", "rolled-back", "closed", "retired", "strengthened"}:
        category = "terminal"
    elif any(item.get("kind") == "unsupported-capability" for item in blockers if isinstance(item, dict)):
        category = "unsupported-capability"
    elif status in {"needs-revision", "compiling", "proposed"}:
        category = "specification-repair"
    elif status in {"shadow", "observing", "monitoring", "evolution-monitoring", "evolution-shadow", "shadow-observing"}:
        category = "awaiting-outcomes"
    else:
        category = "validation-required"
    candidate = row.get("candidate") or row.get("compiledCandidate") or {}
    rule = row.get("candidateRule") or candidate.get("proposedRule") or row.get("proposedRule") or {}
    return {"category": category, "stage": stage, "blockedReason": row.get("blockedReason") or "",
            "automaticRepairAllowed": category == "specification-repair" and int(retry.get("authoringAttempts") or 0) < int(policy.get("maximumAuthoringAttempts") or 3),
            "preserveAttemptHistory": True, "study": study_readiness(rule, policy) if rule else {"state": "candidate-required"}}
