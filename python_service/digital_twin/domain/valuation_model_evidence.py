"""Source-aware inputs for reproducible per-share valuation models.

This module only normalizes observations and performs arithmetic.  It does not
classify a security as attractive, expensive, buyable, or sellable; TypeDB
rules remain responsible for investment meaning after the inputs pass the
valuation governance gate.
"""

from __future__ import annotations

from statistics import median
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .market_data import number
from .valuation_contracts import normalize_valuation_period, period_is_annual_per_share


FUNDAMENTAL_MODEL_VERSION = "fundamental-evidence-per-v3"
SUPPORTED_TARGET_MULTIPLE_BASES = {"historical", "peer"}


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _positive(value: object) -> float:
    parsed = number(value)
    return parsed if parsed > 0 else 0.0


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
    base = _positive(raw.get("base") or raw.get("average") or raw.get("avg") or raw.get("value") or raw.get("eps"))
    low = _positive(raw.get("low") or raw.get("minimum"))
    high = _positive(raw.get("high") or raw.get("maximum"))
    period = _horizon(raw.get("horizon") or raw.get("period") or default_period)
    provider_text = _text(raw.get("provider") or provider)
    source_text = _text(raw.get("source") or source)
    if not base:
        return {}
    return {
        "observationId": _text(raw.get("observationId") or raw.get("id")),
        "metric": "earnings-per-share",
        "value": round(base, 6),
        "low": round(low, 6) if low else 0.0,
        "base": round(base, 6),
        "high": round(high, 6) if high else 0.0,
        "period": period,
        "asOf": _text(raw.get("asOf") or raw.get("fiscalDateEnding") or default_as_of),
        "provider": provider_text,
        "source": source_text,
        "sourceType": _source_type(provider_text, raw.get("sourceType")),
        "analystCount": int(_positive(raw.get("analystCount") or raw.get("numberOfAnalysts"))),
        "isEstimate": bool(raw.get("isEstimate", default_estimate)),
        "revision30dPct": number(raw.get("revision30dPct")),
        "growthPct": number(raw.get("growthPct")),
    }


def collect_earnings_observations(
    overview: Mapping[str, object],
    report: Mapping[str, object],
    company_knowledge: Mapping[str, object] = None,
) -> List[Dict[str, object]]:
    """Return de-duplicated annual-compatible EPS observations with provenance."""

    overview = dict(overview or {}) if isinstance(overview, Mapping) else {}
    report = dict(report or {}) if isinstance(report, Mapping) else {}
    company = dict(company_knowledge or {}) if isinstance(company_knowledge, Mapping) else {}
    result: List[Dict[str, object]] = []

    for owner, default_source in ((overview, "company-overview"), (report, "earnings-report")):
        rows = owner.get("earningsEstimates") if isinstance(owner.get("earningsEstimates"), list) else []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            item = _observation(
                raw,
                provider=owner.get("provider"),
                source=default_source,
                default_as_of=owner.get("fetchedAt"),
                default_estimate=True,
            )
            if item and period_is_annual_per_share(item.get("period")):
                result.append(item)

    scalar_candidates = [
        (overview.get("forwardEPS"), "forward-12m", overview, "forwardEPS", True),
        (report.get("forwardEPS"), "forward-12m", report, "forwardEPS", True),
        (overview.get("trailingEPS") or overview.get("dilutedEPSTTM"), "ttm", overview, "trailingEPS", False),
        (report.get("trailingEPS"), "ttm", report, "trailingEPS", False),
    ]
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
            {"provider": provider, "fetchedAt": as_of},
            "companyKnowledge.trailingEPS",
            False,
        ))
    company_financials = company.get("financials") if isinstance(company.get("financials"), Mapping) else {}
    annual_periods = company_financials.get("annual") if isinstance(company_financials.get("annual"), list) else []
    capital = company.get("capital") if isinstance(company.get("capital"), Mapping) else {}
    if annual_periods:
        latest_annual = annual_periods[0] if isinstance(annual_periods[0], Mapping) else {}
        shares = _positive(latest_annual.get("sharesOutstanding") or capital.get("sharesOutstanding"))
        net_income = number(latest_annual.get("netIncome"))
        if net_income > 0 and shares:
            provenance = company.get("provenance") if isinstance(company.get("provenance"), list) else []
            provider = "+".join(_text(item.get("provider")) for item in provenance if isinstance(item, Mapping) and item.get("provider"))
            result.append({
                "observationId": "eps:company-knowledge:" + _text(latest_annual.get("period")),
                "metric": "earnings-per-share",
                "value": round(net_income / shares, 6),
                "low": 0.0,
                "base": round(net_income / shares, 6),
                "high": 0.0,
                "period": "annual",
                "asOf": _text(latest_annual.get("period")),
                "provider": provider,
                "source": "companyKnowledge.netIncome/sharesOutstanding",
                "sourceType": _source_type(provider),
                "analystCount": 0,
                "isEstimate": False,
                "revision30dPct": 0.0,
                "growthPct": number(latest_annual.get("netIncomeGrowthPct")),
            })

    for raw_value, period, owner, source, is_estimate in scalar_candidates:
        item = _observation(
            {"value": raw_value, "period": period, "isEstimate": is_estimate},
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


def earnings_scenario(observations: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    rows = [dict(item) for item in observations or [] if isinstance(item, Mapping) and _positive(item.get("base"))]
    if not rows:
        return {}
    horizon_priority = ("fy1", "forward-12m", "fy2", "ttm", "annual", "annualized")
    selected: List[Dict[str, object]] = []
    selected_horizon = ""
    for horizon in horizon_priority:
        candidates = [item for item in rows if _horizon(item.get("period")) == horizon]
        if candidates:
            selected = candidates
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
    analyst_count = max((int(number(item.get("analystCount"))) for item in selected), default=0)
    providers = sorted({_text(item.get("provider")) for item in selected if _text(item.get("provider"))})
    scenario_complete = bool(low > 0 and high > low)
    if len(providers) >= 2 and scenario_complete:
        confidence = "high"
    elif scenario_complete or analyst_count >= 3:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "low": round(low, 6),
        "base": round(base, 6),
        "high": round(high, 6),
        "period": selected_horizon,
        "asOf": max((_text(item.get("asOf")) for item in selected), default=""),
        "sourceCount": len(providers),
        "observationCount": len(selected),
        "analystCount": analyst_count,
        "providers": providers,
        "scenarioComplete": scenario_complete,
        "confidence": confidence,
        "method": "reported-consensus-range" if scenario_complete else "single-point-estimate",
        "observationIds": [str(item.get("observationId") or "") for item in selected if item.get("observationId")],
    }


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
    return {
        "observationId": _text(raw.get("observationId") or raw.get("id")),
        "metric": "price-earnings-multiple",
        "value": round(value, 6),
        "basis": basis,
        "period": _text(raw.get("period")),
        "asOf": _text(raw.get("asOf") or default_as_of),
        "provider": provider_text,
        "source": _text(raw.get("source") or source),
        "sourceType": _source_type(provider_text, raw.get("sourceType")),
        "peerSymbol": _text(raw.get("peerSymbol")),
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
                    result.append(item)

    for field, basis in (("peRatio", "current-market"), ("forwardPE", "current-market")):
        item = _multiple_observation(
            {"value": overview.get(field), "basis": basis, "period": "ttm" if field == "peRatio" else "forward-12m"},
            provider=overview.get("provider"),
            source=field,
            default_as_of=overview.get("fetchedAt"),
        )
        if item:
            result.append(item)

    unique: List[Dict[str, object]] = []
    seen = set()
    for item in result:
        key = item.get("observationId") or (
            item.get("basis"),
            round(number(item.get("value")), 6),
            item.get("period"),
            _text(item.get("provider")).casefold(),
            item.get("peerSymbol"),
            item.get("asOf"),
        )
        if key in seen:
            continue
        seen.add(key)
        item = dict(item)
        if not item.get("observationId"):
            item["observationId"] = "per:" + ":".join(str(part) for part in key[:5])
        unique.append(item)
    return unique


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
) -> Dict[str, object]:
    all_rows = [dict(item) for item in observations or [] if isinstance(item, Mapping)]
    eligible = [
        item
        for item in all_rows
        if _text(item.get("basis")).lower() in SUPPORTED_TARGET_MULTIPLE_BASES
        and 0.5 <= _positive(item.get("value")) <= 150.0
    ]
    values = [_positive(item.get("value")) for item in eligible]
    if len(values) >= max(1, int(minimum_samples)):
        low = _percentile(values, 0.25)
        base = _percentile(values, 0.50)
        high = _percentile(values, 0.75)
        bases = sorted({_text(item.get("basis")) for item in eligible if _text(item.get("basis"))})
        providers = sorted({_text(item.get("provider")) for item in eligible if _text(item.get("provider"))})
        return {
            "low": round(low, 4),
            "base": round(base, 4),
            "high": round(high, 4),
            "basis": "+".join(bases),
            "sampleCount": len(values),
            "providerCount": len(providers),
            "providers": providers,
            "evidenceBacked": True,
            "confidence": "high" if len(values) >= 8 and len(providers) >= 2 else "medium",
            "observationIds": [str(item.get("observationId") or "") for item in eligible if item.get("observationId")],
        }
    prior = bootstrap_multiple_band(archetypes)
    return {
        "low": prior[0],
        "base": prior[1],
        "high": prior[2],
        "basis": "bootstrap-prior",
        "sampleCount": len(values),
        "providerCount": len({_text(item.get("provider")) for item in eligible if _text(item.get("provider"))}),
        "providers": sorted({_text(item.get("provider")) for item in eligible if _text(item.get("provider"))}),
        "evidenceBacked": False,
        "confidence": "insufficient",
        "observationIds": [str(item.get("observationId") or "") for item in eligible if item.get("observationId")],
    }


def fair_value_from_evidence(
    earnings: Mapping[str, object],
    multiples: Mapping[str, object],
) -> Dict[str, float]:
    eps_low = _positive((earnings or {}).get("low"))
    eps_base = _positive((earnings or {}).get("base"))
    eps_high = _positive((earnings or {}).get("high"))
    per_low = _positive((multiples or {}).get("low"))
    per_base = _positive((multiples or {}).get("base"))
    per_high = _positive((multiples or {}).get("high"))
    if not all([eps_low, eps_base, eps_high, per_low, per_base, per_high]):
        return {}
    values = sorted([eps_low * per_low, eps_base * per_base, eps_high * per_high])
    return {
        "fairValueLow": round(values[0], 4),
        "fairValue": round(values[1], 4),
        "fairValueBase": round(values[1], 4),
        "fairValueHigh": round(values[2], 4),
        "bearTargetPER": round(per_low, 4),
        "targetPER": round(per_base, 4),
        "bullTargetPER": round(per_high, 4),
    }
