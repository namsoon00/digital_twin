"""Source-aware inputs for reproducible per-share valuation models.

This module only normalizes observations and performs arithmetic.  It does not
classify a security as attractive, expensive, buyable, or sellable; TypeDB
rules remain responsible for investment meaning after the inputs pass the
valuation governance gate.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from digital_twin.modules.market_data.contracts import number
from digital_twin.modules.portfolio.domain.valuation.contracts import normalize_valuation_period, period_is_annual_per_share


FUNDAMENTAL_MODEL_VERSION = "fundamental-evidence-per-v4"
MULTIPLE_SELECTION_POLICY_VERSION = "multiple-comparability-v2"
SUPPORTED_TARGET_MULTIPLE_BASES = {"historical", "peer"}


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _positive(value: object) -> float:
    parsed = number(value)
    return parsed if parsed > 0 else 0.0


def _optional_number(*values: object):
    for value in values:
        if value in (None, "") or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _valid_observation_date(value: object) -> bool:
    text = _text(value)
    if not text:
        return False
    try:
        datetime.fromisoformat(text[:10])
    except ValueError:
        return False
    return True


def _source_type(provider: object, explicit: object = "") -> str:
    normalized = _text(explicit).lower()
    if normalized:
        return normalized
    text = _text(provider).casefold()
    if "kis" in text:
        return "broker"
    if "dart" in text or "sec" in text:
        return "official"
    if text:
        return "external"
    return "unknown"


def _horizon(value: object) -> str:
    raw = _text(value).lower().replace(" ", "")
    aliases = {
        "0y": "fy1",
        "+1y": "fy2",
        "1y": "fy2",
        "current-year": "fy1",
        "next-year": "fy2",
        "forward": "forward-12m",
        "ntm": "forward-12m",
    }
    normalized = aliases.get(raw, normalize_valuation_period(raw))
    return normalized or "unknown"


def _observation(
    raw: Mapping[str, object],
    *,
    provider: object,
    source: object,
    default_period: object = "",
    default_as_of: object = "",
    default_estimate: bool = False,
) -> Dict[str, object]:
    base = _optional_number(raw.get("base"), raw.get("average"), raw.get("avg"), raw.get("value"), raw.get("eps"))
    low = _optional_number(raw.get("low"), raw.get("minimum"))
    high = _optional_number(raw.get("high"), raw.get("maximum"))
    period = _horizon(raw.get("horizon") or raw.get("period") or default_period)
    provider_text = _text(raw.get("provider") or provider)
    source_text = _text(raw.get("source") or source)
    if base is None:
        return {}
    analyst_count = number(raw.get("analystCount") if "analystCount" in raw else raw.get("numberOfAnalysts"))
    result = {
        "observationId": _text(raw.get("observationId") or raw.get("id")),
        "metric": "earnings-per-share",
        "value": round(base, 6),
        "base": round(base, 6),
        "period": period,
        "asOf": _text(raw.get("sourceAsOf") or raw.get("asOf") or raw.get("fiscalDateEnding") or raw.get("fetchedAt") or default_as_of),
        "provider": provider_text,
        "source": source_text,
        "sourceType": _source_type(provider_text, raw.get("sourceType")),
        "isEstimate": bool(raw.get("isEstimate", default_estimate)),
    }
    if low is not None:
        result["low"] = round(low, 6)
    if high is not None:
        result["high"] = round(high, 6)
    if analyst_count >= 0 and ("analystCount" in raw or "numberOfAnalysts" in raw):
        result["analystCount"] = int(analyst_count)
    for field in ("revision30dPct", "growthPct"):
        if raw.get(field) is not None:
            result[field] = number(raw.get(field))
    if raw.get("sampleState"):
        result["sampleState"] = _text(raw.get("sampleState"))
    for field in (
        "contractVersion", "normalizationVersion", "upstreamOrigin", "providerPeriod",
        "horizon", "targetPeriodStart", "targetPeriodEnd", "currency", "perShareBasis",
        "accountingBasis", "estimateBasis", "rangeKind", "revisionKind", "revisionFrom",
        "revisionTo", "fetchedAt", "epsBasis", "securityLine", "splitAdjustmentState",
        "securityAdjustmentState", "calculationMethod", "formula",
    ):
        if raw.get(field) not in (None, ""):
            result[field] = raw.get(field)
    references = [dict(item) for item in raw.get("sourceReferences", []) if isinstance(item, Mapping)]
    if references:
        result["sourceReferences"] = references
        result["validationState"] = _text(raw.get("validationState") or "observed")
        result["revisionState"] = _text(raw.get("revisionState") or "exact-source-revision")
    elif default_estimate or raw.get("isEstimate"):
        result["validationState"] = "legacy-unverified"
        result["revisionState"] = "missing-source-revision"
    eligible = (
        bool(raw.get("positivePerEligible"))
        if "positivePerEligible" in raw
        else bool(base > 0 and period_is_annual_per_share(period))
    )
    result["positivePerEligible"] = eligible
    excluded = [_text(item) for item in raw.get("excludedReasons") or [] if _text(item)]
    if base <= 0:
        excluded.append("non-positive-eps")
    if not period_is_annual_per_share(period):
        excluded.append("unsupported-per-horizon")
    if excluded:
        result["excludedReasons"] = excluded
    return result


def _exact_financial_report_references(row: Mapping[str, object]) -> List[Dict[str, object]]:
    report = row.get("reportContract") if isinstance(row.get("reportContract"), Mapping) else {}
    references = [
        dict(item) for item in report.get("sourceReferences") or []
        if isinstance(item, Mapping) and item.get("datasetId") and item.get("revisionId")
    ]
    row_period = _text(row.get("periodEnd") or row.get("period"))
    report_period = _text(report.get("periodEnd"))
    if (
        _text(row.get("frequency")).lower() != "annual"
        or _text(report.get("frequency")).lower() != "annual"
        or report.get("contractVersion") != "financial-report-observation-v1"
        or report.get("revisionState") != "immutable-source-bound"
        or not references
        or not row_period
        or not report_period
        or not (row_period.endswith(report_period) or report_period.endswith(row_period))
    ):
        return []
    return references


def _same_report_component(
    row: Mapping[str, object],
    left_field: str,
    right_field: str,
    right_basis: str,
) -> List[str]:
    provenance = row.get("metricProvenance") if isinstance(row.get("metricProvenance"), Mapping) else {}
    left = provenance.get(left_field) if isinstance(provenance.get(left_field), Mapping) else {}
    right = provenance.get(right_field) if isinstance(provenance.get(right_field), Mapping) else {}
    reasons = []
    if not _exact_financial_report_references(row):
        reasons.append("missing-exact-financial-report-revision")
    if not left or _text(left.get("attributionScope")).lower() != "common-stockholders":
        reasons.append("unverified-common-stockholder-attribution")
    if not right or _text(right.get("shareCountBasis")).lower() != right_basis:
        reasons.append("unverified-weighted-average-share-basis")
    for field in ("provider", "scope"):
        if not _text(left.get(field)) or not _text(right.get(field)) or _text(left.get(field)) != _text(right.get(field)):
            reasons.append("incompatible-" + field)
    for source in (left, right):
        if _text(source.get("durationBasis")).lower() != "annual":
            reasons.append("non-annual-component")
        source_period = _text(source.get("period"))
        row_period = _text(row.get("periodEnd") or row.get("period"))
        if not source_period or not row_period or not (source_period.endswith(row_period) or row_period.endswith(source_period)):
            reasons.append("component-period-mismatch")
    if _text(left.get("currency")) in {"", "shares"} or _text(right.get("currency")).lower() != "shares":
        reasons.append("component-unit-mismatch")
    for field in ("securityLine", "splitAdjustmentState", "adrRatio"):
        left_value, right_value = left.get(field), right.get(field)
        if (left_value not in (None, "") or right_value not in (None, "")) and left_value != right_value:
            reasons.append("incompatible-" + field)
    return sorted(set(reasons))


def _company_eps_observations(
    company: Mapping[str, object],
    annual_row: Mapping[str, object],
) -> List[Dict[str, object]]:
    row = dict(annual_row or {})
    provider = _text(row.get("provider")) or "+".join(
        _text(item.get("provider"))
        for item in company.get("provenance") or []
        if isinstance(item, Mapping) and item.get("provider")
    )
    references = _exact_financial_report_references(row)
    provenance = row.get("metricProvenance") if isinstance(row.get("metricProvenance"), Mapping) else {}
    period = _text(row.get("periodEnd") or row.get("period"))
    result: List[Dict[str, object]] = []
    reported_by_basis = {}
    for basis, field in (("diluted", "dilutedEPS"), ("basic", "basicEPS")):
        value = _optional_number(row.get(field))
        source = provenance.get(field) if isinstance(provenance.get(field), Mapping) else {}
        reasons = []
        if value is None:
            continue
        if not references:
            reasons.append("missing-exact-financial-report-revision")
        if _text(source.get("perShareBasis")).lower() != basis:
            reasons.append("unverified-per-share-basis")
        if _text(source.get("durationBasis")).lower() != "annual":
            reasons.append("non-annual-reported-eps")
        if not _text(source.get("provider")) or not _text(source.get("scope")):
            reasons.append("missing-reported-eps-lineage")
        observation = {
            "observationId": "eps:company-reported:" + basis + ":" + period,
            "metric": "earnings-per-share",
            "value": round(value, 6),
            "base": round(value, 6),
            "period": "annual",
            "asOf": period,
            "provider": _text(source.get("provider")) or provider,
            "source": "companyKnowledge." + field,
            "sourceType": _source_type(source.get("provider") or provider),
            "isEstimate": False,
            "epsBasis": basis,
            "currency": _text(source.get("currency")),
            "securityLine": _text(source.get("securityLine")),
            "splitAdjustmentState": _text(source.get("splitAdjustmentState")),
            "calculationMethod": "reported",
            "validationState": "verified-reported" if not reasons else "reference-only",
            "positivePerEligible": bool(value > 0 and not reasons),
            "sourceReferences": references,
        }
        exclusions = list(reasons)
        if value <= 0:
            exclusions.append("non-positive-eps-for-per")
        if exclusions:
            observation["excludedReasons"] = sorted(set(exclusions))
        result.append(observation)
        reported_by_basis[basis] = observation

    for basis, shares_field, shares_basis in (
        ("diluted", "weightedAverageSharesDiluted", "weighted-average-diluted"),
        ("basic", "weightedAverageSharesBasic", "weighted-average-basic"),
    ):
        numerator = _optional_number(row.get("netIncomeCommon"))
        denominator = _optional_number(row.get(shares_field))
        if numerator is None or denominator is None:
            continue
        reasons = _same_report_component(row, "netIncomeCommon", shares_field, shares_basis)
        if denominator <= 0:
            reasons.append("non-positive-weighted-average-shares")
        value = numerator / denominator if denominator > 0 else 0.0
        reported = reported_by_basis.get(basis)
        comparison = {}
        if reported and reported.get("validationState") == "verified-reported" and not reasons:
            reported_value = float(reported["base"])
            tolerance = max(0.000001, abs(reported_value) * 0.01)
            difference = abs(value - reported_value)
            comparison = {
                "reportedObservationId": reported["observationId"],
                "difference": round(difference, 6),
                "tolerance": round(tolerance, 6),
                "status": "reconciled" if difference <= tolerance else "mismatch",
            }
            if difference > tolerance:
                reasons.append("reported-eps-mismatch")
        observation = {
            "observationId": "eps:company-derived:" + basis + ":" + period,
            "metric": "earnings-per-share",
            "value": round(value, 6),
            "base": round(value, 6),
            "period": "annual",
            "asOf": period,
            "provider": provider,
            "source": "companyKnowledge.netIncomeCommon/" + shares_field,
            "sourceType": _source_type(provider),
            "isEstimate": False,
            "epsBasis": basis,
            "currency": _text((provenance.get("netIncomeCommon") or {}).get("currency")),
            "securityLine": _text((provenance.get("netIncomeCommon") or {}).get("securityLine")),
            "splitAdjustmentState": _text((provenance.get("netIncomeCommon") or {}).get("splitAdjustmentState")),
            "calculationMethod": "derived-reported-components",
            "formula": "netIncomeCommon / " + shares_field,
            "calculationInputs": {
                "numerator": {"metric": "netIncomeCommon", "value": numerator},
                "denominator": {"metric": shares_field, "value": denominator},
            },
            "validationState": "verified-reconstruction" if not reasons else "reference-only",
            "positivePerEligible": bool(value > 0 and not reasons),
            "sourceReferences": references,
        }
        if comparison:
            observation["reportedComparison"] = comparison
        exclusions = list(reasons)
        if value <= 0:
            exclusions.append("non-positive-eps-for-per")
        if exclusions:
            observation["excludedReasons"] = sorted(set(exclusions))
        result.append(observation)

    if not any(item.get("calculationMethod") == "derived-reported-components" for item in result):
        capital = company.get("capital") if isinstance(company.get("capital"), Mapping) else {}
        numerator = _optional_number(row.get("netIncomeCommon"), row.get("netIncome"))
        denominator = _optional_number(row.get("sharesOutstanding"), capital.get("sharesOutstanding"))
        if numerator is not None and denominator is not None and denominator > 0:
            reasons = ["current-share-count-is-not-weighted-average"]
            if row.get("netIncomeCommon") is None:
                reasons.append("unverified-common-stockholder-attribution")
            if not references:
                reasons.append("missing-exact-financial-report-revision")
            result.append({
                "observationId": "eps:company-reference:" + period,
                "metric": "earnings-per-share",
                "value": round(numerator / denominator, 6),
                "base": round(numerator / denominator, 6),
                "period": "annual",
                "asOf": period,
                "provider": provider,
                "source": "companyKnowledge.netIncome/currentSharesOutstanding",
                "sourceType": _source_type(provider),
                "isEstimate": False,
                "epsBasis": "unknown",
                "calculationMethod": "approximate-current-share-count",
                "formula": "netIncome / currentSharesOutstanding",
                "validationState": "reference-only",
                "positivePerEligible": False,
                "excludedReasons": sorted(set(reasons)),
                "sourceReferences": references,
            })
    return result


def collect_earnings_observations(
    overview: Mapping[str, object],
    report: Mapping[str, object],
    company_knowledge: Mapping[str, object] = None,
    source_references: Iterable[Mapping[str, object]] = (),
) -> List[Dict[str, object]]:
    """Return de-duplicated annual-compatible EPS observations with provenance."""

    overview = dict(overview or {}) if isinstance(overview, Mapping) else {}
    report = dict(report or {}) if isinstance(report, Mapping) else {}
    company = dict(company_knowledge or {}) if isinstance(company_knowledge, Mapping) else {}
    references = [dict(item) for item in source_references or [] if isinstance(item, Mapping)]
    result: List[Dict[str, object]] = []

    for owner, default_source in ((overview, "company-overview"), (report, "earnings-report")):
        rows = owner.get("earningsEstimates") if isinstance(owner.get("earningsEstimates"), list) else []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            provider = _text(raw.get("provider") or owner.get("provider")).casefold()
            consensus_refs = [
                reference for reference in references
                if _text(reference.get("datasetId")) == "yfinance.analyst" and "yfinance" in provider
            ]
            item = _observation(
                {**dict(raw), **({"sourceReferences": consensus_refs} if consensus_refs else {})},
                provider=owner.get("provider"),
                source=default_source,
                default_as_of=owner.get("fetchedAt"),
                default_estimate=True,
            )
            if item and item.get("period") in {"annual", "annualized", "ttm", "trailing-12m", "forward-12m", "fy1", "fy2"}:
                result.append(item)

    scalar_candidates = [
        (overview.get("trailingEPS") or overview.get("dilutedEPSTTM"), "ttm", overview, "trailingEPS", False),
        (report.get("trailingEPS"), "ttm", report, "trailingEPS", False),
    ]
    if not isinstance(overview.get("earningsEstimates"), list) or not overview.get("earningsEstimates"):
        scalar_candidates.append((overview.get("forwardEPS"), overview.get("epsPeriod") or "forward-12m", overview, "forwardEPS", True))
    if not isinstance(report.get("earningsEstimates"), list) or not report.get("earningsEstimates"):
        scalar_candidates.append((report.get("forwardEPS"), report.get("epsPeriod") or "forward-12m", report, "forwardEPS", True))
    annual = report.get("latestAnnual") if isinstance(report.get("latestAnnual"), Mapping) else {}
    if annual:
        scalar_candidates.append((
            annual.get("reportedEPS") or annual.get("estimatedEPS"),
            annual.get("epsPeriod") or "annual",
            {**report, **annual},
            "annualEPS",
            bool(annual.get("isEstimate")),
        ))
    latest = report.get("latestQuarter") if isinstance(report.get("latestQuarter"), Mapping) else {}
    latest_period = _horizon(latest.get("epsPeriod")) if latest else ""
    if latest and period_is_annual_per_share(latest_period):
        scalar_candidates.append((
            latest.get("estimatedEPS") or latest.get("reportedEPS"),
            latest_period,
            {**report, **latest},
            "latestQuarter.annualEPS",
            bool(latest.get("estimatedEPS")),
        ))
    company_valuation = company.get("valuation") if isinstance(company.get("valuation"), Mapping) else {}
    if company_valuation:
        provenance = company.get("provenance") if isinstance(company.get("provenance"), list) else []
        provider = "+".join(_text(item.get("provider")) for item in provenance if isinstance(item, Mapping) and item.get("provider"))
        as_of = max((_text(item.get("asOf")) for item in provenance if isinstance(item, Mapping)), default="")
        scalar_candidates.append((
            company_valuation.get("trailingEPS"),
            "ttm",
            {"provider": provider, "fetchedAt": as_of, "sourceReferences": company.get("sourceReferences") or []},
            "companyKnowledge.trailingEPS",
            False,
        ))
    company_financials = company.get("financials") if isinstance(company.get("financials"), Mapping) else {}
    annual_periods = company_financials.get("annual") if isinstance(company_financials.get("annual"), list) else []
    if annual_periods:
        latest_annual = annual_periods[0] if isinstance(annual_periods[0], Mapping) else {}
        result.extend(_company_eps_observations(company, latest_annual))

    for raw_value, period, owner, source, is_estimate in scalar_candidates:
        provider = _text(owner.get("provider")).casefold()
        owner_references = [dict(item) for item in owner.get("sourceReferences", []) if isinstance(item, Mapping)]
        if "yfinance" in provider:
            owner_references.extend(
                reference for reference in references
                if _text(reference.get("datasetId")) == "yfinance.fundamental"
            )
        raw_observation = {
                "value": raw_value, "period": period, "isEstimate": is_estimate,
                "currency": owner.get("currency"),
                "epsBasis": owner.get("epsBasis") or owner.get("perShareBasis"),
                "accountingBasis": owner.get("accountingBasis"),
                "securityLine": owner.get("securityLine"),
                "splitAdjustmentState": owner.get("splitAdjustmentState"),
                "securityAdjustmentState": owner.get("securityAdjustmentState"),
                **({"sourceReferences": owner_references} if owner_references else {}),
            }
        if source in {"annualEPS", "latestQuarter.annualEPS"} and not is_estimate:
            basis = _text(owner.get("epsBasis") or owner.get("perShareBasis")).lower()
            exact_revision = bool(owner_references)
            raw_observation.update({
                "epsBasis": basis or "unknown",
                "calculationMethod": "reported",
                "validationState": "verified-reported" if basis in {"basic", "diluted"} and exact_revision else "reference-only",
                "positivePerEligible": bool(number(raw_value) > 0 and basis in {"basic", "diluted"} and exact_revision),
                "excludedReasons": [
                    *([] if basis in {"basic", "diluted"} else ["unverified-per-share-basis"]),
                    *([] if exact_revision else ["missing-exact-financial-report-revision"]),
                ],
            })
        item = _observation(
            raw_observation,
            provider=owner.get("provider"),
            source=source,
            default_as_of=owner.get("fetchedAt") or owner.get("latestQuarter"),
            default_estimate=is_estimate,
        )
        if item and period_is_annual_per_share(item.get("period")):
            result.append(item)

    unique: List[Dict[str, object]] = []
    seen = set()
    for item in result:
        key = (
            item.get("period"),
            round(number(item.get("base")), 6),
            round(number(item.get("low")), 6),
            round(number(item.get("high")), 6),
            _text(item.get("provider")).casefold(),
            _text(item.get("asOf")),
            _text(item.get("calculationMethod")),
            _text(item.get("epsBasis")),
        )
        if key in seen:
            continue
        seen.add(key)
        item = dict(item)
        if not item.get("observationId"):
            item["observationId"] = "eps:" + ":".join(str(part) for part in key[:5])
        unique.append(item)
    return unique


def _weighted_median(values: Sequence[Tuple[float, float]]) -> float:
    rows = sorted((float(value), max(1.0, float(weight))) for value, weight in values if value > 0)
    if not rows:
        return 0.0
    threshold = sum(weight for _value, weight in rows) / 2.0
    cumulative = 0.0
    for value, weight in rows:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return rows[-1][0]


def _consensus_upstream_identity(value: Mapping[str, object]) -> tuple:
    row = dict(value or {})
    upstream = _text(row.get("upstreamOrigin")) or _text(row.get("provider"))
    return (
        upstream.casefold(),
        _horizon(row.get("period")),
        _text(row.get("targetPeriodEnd")),
    )


def _deduplicate_consensus_upstreams(values: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    selected = {}
    for raw in values or []:
        row = dict(raw)
        key = _consensus_upstream_identity(row)
        current = selected.get(key)
        clock = (_text(row.get("sourceAsOf")), _text(row.get("fetchedAt") or row.get("asOf")), _text(row.get("observationId")))
        current_clock = (
            _text(current.get("sourceAsOf")),
            _text(current.get("fetchedAt") or current.get("asOf")),
            _text(current.get("observationId")),
        ) if current else ()
        if current is None or clock >= current_clock:
            selected[key] = row
    return [selected[key] for key in sorted(selected)]


def earnings_scenario(observations: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    rows = [
        dict(item) for item in observations or []
        if isinstance(item, Mapping)
        and _positive(item.get("base"))
        and period_is_annual_per_share(item.get("period"))
        and item.get("positivePerEligible") is not False
    ]
    if not rows:
        return {}
    horizon_priority = ("fy1", "forward-12m", "ttm", "annual", "annualized")
    selected: List[Dict[str, object]] = []
    selected_horizon = ""
    for horizon in horizon_priority:
        candidates = [item for item in rows if _horizon(item.get("period")) == horizon]
        if candidates:
            selected = _deduplicate_consensus_upstreams(candidates)
            selected_horizon = horizon
            break
    if not selected:
        selected = rows
        selected_horizon = _horizon(rows[0].get("period"))

    weighted_bases = [
        (_positive(item.get("base")), max(1, int(number(item.get("analystCount")))))
        for item in selected
    ]
    base = _weighted_median(weighted_bases)
    lows = [
        (_positive(item.get("low")), max(1, int(number(item.get("analystCount")))))
        for item in selected
        if _positive(item.get("low"))
    ]
    highs = [
        (_positive(item.get("high")), max(1, int(number(item.get("analystCount")))))
        for item in selected
        if _positive(item.get("high"))
    ]
    low = _weighted_median(lows) if lows else (min(value for value, _weight in weighted_bases) if len(weighted_bases) >= 2 else base)
    high = _weighted_median(highs) if highs else (max(value for value, _weight in weighted_bases) if len(weighted_bases) >= 2 else base)
    low, base, high = sorted([low, base, high])
    provided_counts = [int(number(item.get("analystCount"))) for item in selected if item.get("analystCount") is not None]
    analyst_count = max(provided_counts, default=0)
    providers = sorted({_text(item.get("provider")) for item in selected if _text(item.get("provider"))})
    source_references = {
        (_text(reference.get("datasetId")), _text(reference.get("revisionId"))): dict(reference)
        for item in selected
        for reference in item.get("sourceReferences", [])
        if isinstance(reference, Mapping) and reference.get("datasetId") and reference.get("revisionId")
    }
    scenario_complete = bool(low > 0 and high > low)
    reported_range = any(
        _positive(item.get("low")) and _positive(item.get("high")) > _positive(item.get("low"))
        for item in selected
    )
    if len(providers) >= 2 and scenario_complete:
        confidence = "high"
    elif scenario_complete or analyst_count >= 3:
        confidence = "medium"
    else:
        confidence = "low"
    result = {
        "low": round(low, 6),
        "base": round(base, 6),
        "high": round(high, 6),
        "period": selected_horizon,
        "asOf": max((_text(item.get("asOf")) for item in selected), default=""),
        "sourceCount": len(providers),
        "observationCount": len(selected),
        "analystCountState": "reported" if provided_counts else "not-provided",
        "providers": providers,
        "scenarioComplete": scenario_complete,
        "confidence": confidence,
        "method": "reported-consensus-range" if scenario_complete and reported_range else "observed-point-range" if scenario_complete else "single-point-estimate",
        "observationIds": [str(item.get("observationId") or "") for item in selected if item.get("observationId")],
        "sourceReferences": [source_references[key] for key in sorted(source_references)],
    }
    for field in (
        "currency", "epsBasis", "accountingBasis", "securityLine",
        "splitAdjustmentState", "securityAdjustmentState",
    ):
        values = {_text(item.get(field)) for item in selected if _text(item.get(field))}
        if len(values) == 1:
            result[field] = next(iter(values))
    if provided_counts:
        result["analystCount"] = analyst_count
    return result


def _multiple_observation(
    raw: Mapping[str, object],
    *,
    provider: object,
    source: object,
    default_as_of: object = "",
) -> Dict[str, object]:
    value = _positive(raw.get("value") or raw.get("per") or raw.get("multiple"))
    if not value:
        return {}
    basis = _text(raw.get("basis") or raw.get("type") or "current-market").lower().replace("_", "-")
    aliases = {
        "historical-per": "historical",
        "history": "historical",
        "peer-per": "peer",
        "comparable": "peer",
        "forward": "current-market",
        "current": "current-market",
    }
    basis = aliases.get(basis, basis)
    provider_text = _text(raw.get("provider") or provider)
    horizon = _horizon(raw.get("earningsHorizon") or raw.get("horizon"))
    source_references = [
        dict(item) for item in raw.get("sourceReferences") or []
        if isinstance(item, Mapping) and item.get("datasetId") and item.get("revisionId")
    ]
    comparison_factors = raw.get("comparisonFactors") if isinstance(raw.get("comparisonFactors"), Mapping) else {}
    return {
        "observationId": _text(raw.get("observationId") or raw.get("id")),
        "metric": "price-earnings-multiple",
        "multipleMetric": _text(raw.get("multipleMetric") or raw.get("metric") or "per").lower(),
        "value": round(value, 6),
        "basis": basis,
        "period": _text(raw.get("period")),
        "asOf": _text(raw.get("asOf") or default_as_of),
        "priceAsOf": _text(raw.get("priceAsOf") or raw.get("marketAsOf") or raw.get("asOf") or default_as_of),
        "earningsAsOf": _text(raw.get("earningsAsOf") or raw.get("periodEnd")),
        "earningsHorizon": horizon,
        "accountingBasis": _text(raw.get("accountingBasis")),
        "epsBasis": _text(raw.get("epsBasis") or raw.get("perShareBasis")).lower(),
        "currency": _text(raw.get("currency")).upper(),
        "issuer": _text(raw.get("issuer") or raw.get("symbol")).upper(),
        "securityLine": _text(raw.get("securityLine")),
        "provider": provider_text,
        "upstreamOrigin": _text(raw.get("upstreamOrigin") or raw.get("datasetId") or provider_text).casefold(),
        "source": _text(raw.get("source") or source),
        "sourceType": _source_type(provider_text, raw.get("sourceType")),
        "peerSymbol": _text(raw.get("peerSymbol")),
        "freshnessState": _text(raw.get("freshnessState") or "unknown").lower(),
        "comparabilityState": _text(raw.get("comparabilityState") or "unverified").lower(),
        "comparisonFactors": dict(comparison_factors),
        "sourceReferences": source_references,
    }


def collect_multiple_observations(
    overview: Mapping[str, object],
    report: Mapping[str, object],
    company_knowledge: Mapping[str, object] = None,
) -> List[Dict[str, object]]:
    overview = dict(overview or {}) if isinstance(overview, Mapping) else {}
    report = dict(report or {}) if isinstance(report, Mapping) else {}
    company = dict(company_knowledge or {}) if isinstance(company_knowledge, Mapping) else {}
    result: List[Dict[str, object]] = []
    for owner, source in ((overview, "company-overview"), (report, "earnings-report"), (company, "company-knowledge")):
        for field in ("multipleObservations", "historicalPERs", "peerMultiples"):
            raw_rows = owner.get(field) if isinstance(owner.get(field), list) else []
            for raw in raw_rows:
                item = _multiple_observation(
                    raw if isinstance(raw, Mapping) else {"value": raw, "basis": "historical" if field == "historicalPERs" else "peer"},
                    provider=owner.get("provider"),
                    source=source + "." + field,
                    default_as_of=owner.get("fetchedAt"),
                )
                if item:
                    if not item.get("issuer"):
                        item["issuer"] = _text(owner.get("symbol")).upper()
                    if not item.get("securityLine"):
                        item["securityLine"] = _text(owner.get("securityLine"))
                    result.append(item)

    for field, basis in (("peRatio", "current-market"), ("forwardPE", "current-market")):
        item = _multiple_observation(
            {"value": overview.get(field), "basis": basis, "period": "ttm" if field == "peRatio" else "forward-12m"},
            provider=overview.get("provider"),
            source=field,
            default_as_of=overview.get("fetchedAt"),
        )
        if item:
            item["issuer"] = _text(overview.get("symbol")).upper()
            item["securityLine"] = _text(overview.get("securityLine"))
            result.append(item)

    normalized: List[Dict[str, object]] = []
    for item in result:
        key = item.get("observationId") or (
            item.get("basis"), item.get("issuer"), item.get("securityLine"),
            item.get("multipleMetric"), item.get("earningsHorizon"),
            item.get("priceAsOf"), item.get("earningsAsOf"), item.get("upstreamOrigin"),
            round(number(item.get("value")), 6),
        )
        item = dict(item)
        if not item.get("observationId"):
            item["observationId"] = "per:" + ":".join(str(part) for part in key)
        normalized.append(item)
    return normalized


def _percentile(values: Sequence[float], ratio: float) -> float:
    rows = sorted(float(value) for value in values if value > 0)
    if not rows:
        return 0.0
    if len(rows) == 1:
        return rows[0]
    position = max(0.0, min(1.0, ratio)) * (len(rows) - 1)
    lower = int(position)
    upper = min(len(rows) - 1, lower + 1)
    fraction = position - lower
    return rows[lower] + (rows[upper] - rows[lower]) * fraction


def bootstrap_multiple_band(archetypes: Iterable[str]) -> List[float]:
    archetypes = set(archetypes or [])
    if "AIGrowth" in archetypes:
        return [24.0, 34.0, 44.0]
    if "MegaCapQuality" in archetypes and "SemiconductorCyclical" not in archetypes:
        return [20.0, 28.0, 34.0]
    if "PlatformGrowth" in archetypes:
        return [18.0, 26.0, 34.0]
    if "SemiconductorHBM" in archetypes:
        return [8.0, 12.0, 16.0]
    if "SemiconductorCyclical" in archetypes:
        return [7.0, 10.0, 13.0]
    if "HighVolatilityGrowth" in archetypes:
        return [10.0, 18.0, 28.0]
    return [8.0, 12.0, 18.0]


def multiple_evidence_band(
    observations: Iterable[Mapping[str, object]],
    archetypes: Iterable[str],
    minimum_samples: int = 3,
    earnings: Mapping[str, object] = None,
) -> Dict[str, object]:
    archetype_set = set(archetypes or [])
    all_rows = [dict(item) for item in observations or [] if isinstance(item, Mapping)]
    target = dict(earnings or {}) if isinstance(earnings, Mapping) else {}
    target_horizon = _horizon(target.get("horizon") or target.get("period"))
    target_eps_basis = _text(target.get("epsBasis")).lower()
    target_accounting_basis = _text(target.get("accountingBasis")).lower()
    target_security = _text(target.get("securityLine"))
    target_currency = _text(target.get("currency")).upper()
    ledger: List[Dict[str, object]] = []
    candidates: List[Dict[str, object]] = []
    identity_values: Dict[Tuple[object, ...], set] = {}
    for item in all_rows:
        identity = (
            _text(item.get("basis")).lower(), _text(item.get("issuer")).upper(),
            _text(item.get("securityLine")), _text(item.get("multipleMetric")).lower(),
            _horizon(item.get("earningsHorizon")), _text(item.get("priceAsOf")),
            _text(item.get("earningsAsOf")), _text(item.get("upstreamOrigin")).casefold(),
        )
        identity_values.setdefault(identity, set()).add(round(number(item.get("value")), 6))
    seen_identities = set()
    for item in all_rows:
        reasons = []
        basis = _text(item.get("basis")).lower()
        value = _positive(item.get("value"))
        row_horizon = _horizon(item.get("earningsHorizon"))
        identity = (
            basis, _text(item.get("issuer")).upper(), _text(item.get("securityLine")),
            _text(item.get("multipleMetric")).lower(), row_horizon,
            _text(item.get("priceAsOf")), _text(item.get("earningsAsOf")),
            _text(item.get("upstreamOrigin")).casefold(),
        )
        if basis not in SUPPORTED_TARGET_MULTIPLE_BASES:
            reasons.append("unsupported-multiple-basis")
        if not 0.5 <= value <= 150.0:
            reasons.append("multiple-out-of-range")
        for field, reason in (
            ("issuer", "missing-issuer"), ("securityLine", "missing-security-line"),
            ("multipleMetric", "missing-multiple-metric"), ("priceAsOf", "missing-price-as-of"),
            ("earningsAsOf", "missing-earnings-as-of"), ("upstreamOrigin", "missing-upstream-origin"),
            ("provider", "missing-provider"), ("accountingBasis", "missing-accounting-basis"),
            ("epsBasis", "missing-eps-basis"), ("currency", "missing-currency"),
        ):
            if not _text(item.get(field)):
                reasons.append(reason)
        if row_horizon == "unknown":
            reasons.append("missing-earnings-horizon")
        if item.get("priceAsOf") and not _valid_observation_date(item.get("priceAsOf")):
            reasons.append("invalid-price-as-of")
        if item.get("earningsAsOf") and not _valid_observation_date(item.get("earningsAsOf")):
            reasons.append("invalid-earnings-as-of")
        if target_horizon != "unknown" and row_horizon != target_horizon:
            reasons.append("incompatible-earnings-horizon")
        row_eps_basis = _text(item.get("epsBasis")).lower()
        if not target_eps_basis or target_eps_basis == "unknown":
            reasons.append("target-eps-basis-unknown")
        elif row_eps_basis != target_eps_basis:
            reasons.append("incompatible-eps-basis")
        row_accounting_basis = _text(item.get("accountingBasis")).lower()
        if not target_accounting_basis:
            reasons.append("target-accounting-basis-unknown")
        elif row_accounting_basis != target_accounting_basis:
            reasons.append("incompatible-accounting-basis")
        if not target_security:
            reasons.append("target-security-line-unknown")
        elif _text(item.get("securityLine")) != target_security:
            reasons.append("incompatible-security-line")
        row_currency = _text(item.get("currency")).upper()
        if target_currency and row_currency and row_currency != target_currency:
            reasons.append("incompatible-currency")
        freshness_state = _text(item.get("freshnessState")).lower()
        if freshness_state in {"stale", "expired", "invalid"}:
            reasons.append("stale-multiple-observation")
        elif freshness_state not in {"fresh", "historical-valid"}:
            reasons.append("unverified-freshness")
        if _text(item.get("comparabilityState")).lower() != "verified":
            reasons.append("unverified-comparability")
        if len(identity_values.get(identity) or set()) > 1:
            reasons.append("conflicting-duplicate-observation")
        elif identity in seen_identities:
            reasons.append("duplicate-observation")
        seen_identities.add(identity)
        decision = {
            "observationId": _text(item.get("observationId")),
            "basis": basis,
            "value": round(value, 6),
            "issuer": _text(item.get("issuer")).upper(),
            "securityLine": _text(item.get("securityLine")),
            "earningsHorizon": row_horizon,
            "epsBasis": row_eps_basis,
            "accountingBasis": _text(item.get("accountingBasis")),
            "priceAsOf": _text(item.get("priceAsOf")),
            "earningsAsOf": _text(item.get("earningsAsOf")),
            "provider": _text(item.get("provider")),
            "upstreamOrigin": _text(item.get("upstreamOrigin")),
            "comparisonFactors": dict(item.get("comparisonFactors") or {}),
            "state": "included" if not reasons else "excluded",
            "reasons": sorted(set(reasons)),
        }
        ledger.append(decision)
        if not reasons:
            candidates.append(item)

    by_basis = {
        basis: [item for item in candidates if _text(item.get("basis")).lower() == basis]
        for basis in ("peer", "historical")
    }
    component_bands = {}
    threshold = max(1, int(minimum_samples))
    for basis, rows in by_basis.items():
        values = [_positive(item.get("value")) for item in rows]
        if values:
            component_bands[basis] = {
                "low": round(_percentile(values, 0.25), 4),
                "base": round(_percentile(values, 0.50), 4),
                "high": round(_percentile(values, 0.75), 4),
                "sampleCount": len(values),
                "providerCount": len({_text(item.get("provider")) for item in rows if _text(item.get("provider"))}),
                "observationIds": [_text(item.get("observationId")) for item in rows if _text(item.get("observationId"))],
            }
    band_block_reasons = []
    if "SemiconductorCyclical" in archetype_set and _text(target.get("normalizationState")).lower() != "verified":
        band_block_reasons.append("missing-cyclical-earnings-normalization")
    selectable = [
        basis for basis in ("peer", "historical")
        if len(by_basis[basis]) >= threshold and not band_block_reasons
    ]
    selected_basis = selectable[0] if selectable else ""
    eligible = by_basis.get(selected_basis) or []
    values = [_positive(item.get("value")) for item in eligible]
    if selected_basis:
        band = component_bands[selected_basis]
        providers = sorted({_text(item.get("provider")) for item in eligible if _text(item.get("provider"))})
        return {
            "low": band["low"],
            "base": band["base"],
            "high": band["high"],
            "basis": selected_basis,
            "sampleCount": len(values),
            "providerCount": len(providers),
            "providers": providers,
            "evidenceBacked": True,
            "comparabilityState": "comparable",
            "confidence": "high" if len(values) >= 8 and len(providers) >= 2 else "medium",
            "observationIds": [str(item.get("observationId") or "") for item in eligible if item.get("observationId")],
            "selectionPolicyVersion": MULTIPLE_SELECTION_POLICY_VERSION,
            "targetEarningsHorizon": target_horizon,
            "componentBands": component_bands,
            "selectionLedger": ledger,
            "selectionAssumption": {
                "type": "basis-priority", "priority": ["peer", "historical"],
                "version": MULTIPLE_SELECTION_POLICY_VERSION,
            },
            "bandBlockReasons": [],
        }
    prior = bootstrap_multiple_band(archetype_set)
    return {
        "low": prior[0],
        "base": prior[1],
        "high": prior[2],
        "basis": "bootstrap-prior",
        "sampleCount": len(candidates),
        "providerCount": len({_text(item.get("provider")) for item in candidates if _text(item.get("provider"))}),
        "providers": sorted({_text(item.get("provider")) for item in candidates if _text(item.get("provider"))}),
        "evidenceBacked": False,
        "comparabilityState": "insufficient-comparable-samples",
        "referenceOnly": True,
        "confidence": "insufficient",
        "observationIds": [],
        "selectionPolicyVersion": MULTIPLE_SELECTION_POLICY_VERSION,
        "targetEarningsHorizon": target_horizon,
        "componentBands": component_bands,
        "selectionLedger": ledger,
        "selectionAssumption": {
            "type": "basis-priority", "priority": ["peer", "historical"],
            "version": MULTIPLE_SELECTION_POLICY_VERSION,
        },
        "bandBlockReasons": band_block_reasons,
    }


def fair_value_from_evidence(
    earnings: Mapping[str, object],
    multiples: Mapping[str, object],
) -> Dict[str, object]:
    eps_low = _positive((earnings or {}).get("low"))
    eps_base = _positive((earnings or {}).get("base"))
    eps_high = _positive((earnings or {}).get("high"))
    per_low = _positive((multiples or {}).get("low"))
    per_base = _positive((multiples or {}).get("base"))
    per_high = _positive((multiples or {}).get("high"))
    if not all([eps_low, eps_base, eps_high, per_low, per_base, per_high]):
        return {}
    values = [eps_low * per_low, eps_base * per_base, eps_high * per_high]
    return {
        "fairValueLow": round(values[0], 4),
        "fairValue": round(values[1], 4),
        "fairValueBase": round(values[1], 4),
        "fairValueHigh": round(values[2], 4),
        "bearTargetPER": round(per_low, 4),
        "targetPER": round(per_base, 4),
        "bullTargetPER": round(per_high, 4),
        "scenarioAssumptions": [
            {"scenario": "bear", "eps": eps_low, "multiple": per_low, "fairValue": round(values[0], 4)},
            {"scenario": "base", "eps": eps_base, "multiple": per_base, "fairValue": round(values[1], 4)},
            {"scenario": "bull", "eps": eps_high, "multiple": per_high, "fairValue": round(values[2], 4)},
        ],
    }
