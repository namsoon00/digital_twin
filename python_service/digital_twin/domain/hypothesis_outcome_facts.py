"""Bounded point-in-time facts for prediction and premise outcome axes."""

from typing import Mapping

from .hypothesis_outcome_evaluation import optional_number


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
        **{key: source[key] for key in ("currentPrice", "ma20Distance", "sourceAsOf", "observedAt") if key in source},
    }


def premise_observation_facts(baseline: Mapping[str, object], facts: Mapping[str, object]):
    start, source = _mapping(baseline), _mapping(facts)
    result = {}
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
