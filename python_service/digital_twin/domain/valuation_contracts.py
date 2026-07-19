from datetime import datetime, timezone
from typing import Dict, Iterable, List

from .market_data import clamp, number


ANNUAL_EPS_PERIODS = {"annual", "annualized", "ttm", "trailing-12m", "forward-12m", "fy1"}


def normalize_valuation_period(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "trailing": "ttm",
        "trailing12m": "ttm",
        "trailing-twelve-months": "ttm",
        "ltm": "ttm",
        "forward": "forward-12m",
        "forward12m": "forward-12m",
        "next-12m": "forward-12m",
        "ntm": "forward-12m",
        "year": "annual",
        "yearly": "annual",
        "fiscal-year": "annual",
        "quarter": "quarterly",
    }
    return aliases.get(text, text)


def period_is_annual_per_share(value: object) -> bool:
    return normalize_valuation_period(value) in ANNUAL_EPS_PERIODS


def _iso_age_days(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10] + "T00:00:00+00:00")
        except ValueError:
            return 0.0
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)


def valuation_freshness_status(as_of: object, max_age_days: float = 120.0) -> str:
    age = _iso_age_days(as_of)
    if not age:
        return "unknown"
    if age <= max_age_days:
        return "fresh"
    if age <= max_age_days * 2:
        return "aging"
    return "stale"


def annual_eps_observation(overview: Dict[str, object], report: Dict[str, object]) -> Dict[str, object]:
    overview = overview if isinstance(overview, dict) else {}
    report = report if isinstance(report, dict) else {}
    candidates = [
        {
            "value": number(overview.get("forwardEPS")),
            "period": "forward-12m",
            "asOf": overview.get("fetchedAt") or overview.get("latestQuarter"),
            "field": "forwardEPS",
        },
        {
            "value": number(overview.get("trailingEPS") or overview.get("dilutedEPSTTM")),
            "period": "ttm",
            "asOf": overview.get("fetchedAt") or overview.get("latestQuarter"),
            "field": "trailingEPS",
        },
    ]
    annual = report.get("latestAnnual") if isinstance(report.get("latestAnnual"), dict) else {}
    if annual:
        candidates.append({
            "value": number(annual.get("reportedEPS") or annual.get("estimatedEPS")),
            "period": normalize_valuation_period(annual.get("epsPeriod") or "annual"),
            "asOf": annual.get("fiscalDateEnding") or annual.get("reportedDate") or report.get("fetchedAt"),
            "field": "annualEPS",
        })
    latest = report.get("latestQuarter") if isinstance(report.get("latestQuarter"), dict) else {}
    latest_period = normalize_valuation_period(latest.get("epsPeriod"))
    if latest and period_is_annual_per_share(latest_period):
        candidates.append({
            "value": number(latest.get("estimatedEPS") or latest.get("reportedEPS")),
            "period": latest_period,
            "asOf": latest.get("fiscalDateEnding") or latest.get("reportedDate") or report.get("fetchedAt"),
            "field": "reportedEPS",
        })
    for candidate in candidates:
        if number(candidate.get("value")) and period_is_annual_per_share(candidate.get("period")):
            candidate["value"] = number(candidate.get("value"))
            return candidate
    return {}


def fair_value_scenarios(
    eps: object,
    eps_period: object,
    target_multiples: Iterable[float],
    eps_factors: Iterable[float] = (0.85, 1.0, 1.15),
) -> Dict[str, float]:
    annual_eps = number(eps)
    multiples = [number(item) for item in target_multiples]
    factors = [number(item) for item in eps_factors]
    if not annual_eps or not period_is_annual_per_share(eps_period) or len(multiples) != 3 or len(factors) != 3:
        return {}
    values = [max(0.01, annual_eps * factors[index] * multiples[index]) for index in range(3)]
    values.sort()
    return {
        "fairValueLow": round(values[0], 4),
        "fairValue": round(values[1], 4),
        "fairValueBase": round(values[1], 4),
        "fairValueHigh": round(values[2], 4),
        "bearTargetPER": round(multiples[0], 4),
        "targetPER": round(multiples[1], 4),
        "bullTargetPER": round(multiples[2], 4),
    }


def scenario_margins(current_price: object, low: object, base: object, high: object) -> Dict[str, float]:
    current = number(current_price)
    if not current:
        return {}

    def margin(value: object) -> float:
        fair_value = number(value)
        return round(((fair_value / current) - 1.0) * 100.0, 2) if fair_value else 0.0

    return {
        "conservativeMarginOfSafetyPct": margin(low),
        "marginOfSafetyPct": margin(base),
        "optimisticMarginOfSafetyPct": margin(high),
    }


def valuation_input_coverage(required_inputs: Iterable[str], available_inputs: Iterable[str]) -> float:
    required = {str(item) for item in required_inputs if str(item or "").strip()}
    available = {str(item) for item in available_inputs if str(item or "").strip()}
    if not required:
        return 100.0
    return round(100.0 * len(required.intersection(available)) / len(required), 1)


def valuation_reliability_score(
    source_type: str,
    input_coverage_pct: object,
    eps_period: object = "",
    freshness_status: str = "unknown",
    scenario_complete: bool = False,
    model_count: int = 1,
) -> float:
    source_base = {
        "official": 76.0,
        "broker": 72.0,
        "external": 66.0,
        "user": 64.0,
        "ai": 42.0,
    }.get(str(source_type or "").strip().lower(), 45.0)
    score = source_base
    score += (clamp(number(input_coverage_pct), 0.0, 100.0) - 50.0) * 0.16
    if period_is_annual_per_share(eps_period):
        score += 6.0
    if scenario_complete:
        score += 5.0
    score += min(6.0, max(0, int(model_count) - 1) * 3.0)
    if freshness_status == "fresh":
        score += 4.0
    elif freshness_status == "aging":
        score -= 5.0
    elif freshness_status == "stale":
        score -= 16.0
    return round(clamp(score, 0.0, 95.0), 1)


def valuation_confidence_label(score: object) -> str:
    value = number(score)
    if value >= 80:
        return "높음"
    if value >= 65:
        return "보통"
    if value >= 50:
        return "낮음"
    return "매우 낮음"


def valuation_decision_eligible(
    source_type: str,
    reliability_score: object,
    approval_status: object,
    freshness_status: str,
    period_compatible: bool,
    fair_value: object,
) -> bool:
    if not number(fair_value) or freshness_status == "stale" or not period_compatible:
        return False
    source = str(source_type or "").strip().lower()
    approval = str(approval_status or "").strip().lower()
    if source == "ai" and approval not in {"user_approved", "user_modified", "approved", "modified"}:
        return False
    return number(reliability_score) >= 65.0


def unique_missing(values: Iterable[object]) -> List[str]:
    return sorted({str(item).strip() for item in values if str(item or "").strip()})
