"""Bounded point-in-time facts for prediction and premise outcome axes."""

from typing import Mapping

from .hypothesis_outcome_evaluation import optional_number
from .observation_independence import observation_time


FINANCIAL_METRICS = (
    "revenueGrowthPct", "netIncomeGrowthPct", "freeCashFlowGrowthPct",
    "sharesOutstandingGrowthPct", "freeCashFlowMarginPct", "operatingIncomeGrowthPct",
)


def _mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def _period_end(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[-8:] if len(digits) >= 8 else ""


def freeze_outcome_baseline(facts: Mapping[str, object]):
    source = _mapping(facts)
    company = _mapping(source.get("companyContext"))
    financials = _mapping(company.get("latestFinancials") or company.get("financials"))
    periods = {}
    for frequency in ("annual", "interim", "quarterly"):
        rows = financials.get(frequency) or []
        if isinstance(rows, list):
            valid = [_mapping(row) for row in rows if _mapping(row).get("period")]
            if valid:
                periods[frequency] = max(_period_end(row["period"]) for row in valid)
    return {
        "financialPeriods": periods,
        "valuationObservation": valuation_observation(source),
        **{key: source[key] for key in ("currentPrice", "ma20Distance", "sourceAsOf", "observedAt") if key in source},
    }


def premise_observation_facts(baseline: Mapping[str, object], facts: Mapping[str, object]):
    start, source = _mapping(baseline), _mapping(facts)
    result = valuation_observation_facts(start.get("valuationObservation") or {},
                                         source.get("valuationObservation") or valuation_observation(source))
    before, after = optional_number(start.get("ma20Distance")), optional_number(source.get("ma20Distance"))
    if before is not None and after is not None:
        result["ma20DistanceChangePp"] = after - before
    company = _mapping(source.get("companyContext"))
    financials = _mapping(company.get("financials") or company.get("latestFinancials"))
    previous = _mapping(start.get("financialPeriods"))
    candidates = []
    for frequency, period in previous.items():
        rows = financials.get(frequency) or []
        if isinstance(rows, list):
            candidates.extend(_mapping(row) for row in rows if _period_end(period) and _period_end(_mapping(row).get("period")) > _period_end(period))
    if candidates:
        latest = max(candidates, key=lambda row: _period_end(row["period"]))
        result.update({
            "financialPeriod": str(latest["period"]),
            "newFinancialPeriod": True,
            "financialEvidenceSnapshotAt": str(source.get("evidenceSnapshotAt") or ""),
            **{key: latest[key] for key in FINANCIAL_METRICS if optional_number(latest.get(key)) is not None},
        })
    return result


def valuation_observation(facts):
    bundle, assessment = _mapping(facts.get("valuationBundle")), _mapping(facts.get("valuationAssessment"))
    base = next((row for row in assessment.get("scenarios") or [] if row.get("scenarioId") == "base"), {})
    return {"bundleId": bundle.get("bundleId"), "assessmentId": assessment.get("assessmentId"),
            "modelId": assessment.get("modelId"), "modelVersion": assessment.get("modelVersion"),
            "assumptionVersion": assessment.get("assumptionVersion"), "currency": bundle.get("valuationCurrency"),
            "observedAt": bundle.get("valuationAt"), "knownAt": bundle.get("knowledgeCutoffAt"),
            "reproducibilityState": bundle.get("reproducibilityState"),
            "fairValue": base.get("valuePerShare"), "price": (bundle.get("quote") or {}).get("value"),
            "sourceRevisionVector": bundle.get("sourceRevisionVector")}


def valuation_observation_facts(before, after):
    required = ("bundleId", "assessmentId", "modelId", "modelVersion", "assumptionVersion", "currency", "observedAt", "knownAt")
    if not all(before.get(key) and after.get(key) for key in required):
        return {"valuationMeasurementState": "two-immutable-valuation-observations-required"}
    if any(before[key] != after[key] for key in ("modelId", "modelVersion", "assumptionVersion", "currency")):
        return {"valuationMeasurementState": "valuation-method-or-unit-changed"}
    clocks = [observation_time(row[key]) for row in (before, after) for key in ("observedAt", "knownAt")]
    if not all(clocks) or clocks[2] <= clocks[0] or clocks[1] > clocks[0] or clocks[3] > clocks[2]:
        return {"valuationMeasurementState": "valuation-clock-invalid"}
    if any(row.get("reproducibilityState") != "reproducible" for row in (before, after)):
        return {"valuationMeasurementState": "valuation-not-reproducible"}
    values = [optional_number(row.get(key)) for row in (before, after) for key in ("fairValue", "price")]
    if any(value is None or value <= 0 for value in values):
        return {"valuationMeasurementState": "valuation-values-missing"}
    v0, p0, v1, p1 = values
    initial, price_only, final = abs(v0-p0), abs(v0-p1), abs(v1-p1)
    return {"valuationMeasurementState": "measured", "valuationGapReductionPp": (initial-final)/v0*100,
            "valuationPriceContributionPp": (initial-price_only)/v0*100,
            "valuationValueContributionPp": (price_only-final)/v0*100,
            "valuationObservation": after, "valuationBaselineBundleId": before["bundleId"],
            "valuationObservedBundleId": after["bundleId"], "causalAttribution": "not-established"}
