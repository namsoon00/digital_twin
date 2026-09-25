"""Bounded one-variable reverse DCF using the forward DCF calculator."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Dict, Mapping

from digital_twin.modules.portfolio.domain.valuation.dcf import calculate_driver_dcf


REVERSE_DCF_VERSION = "reverse-dcf-growth-solver-v1"


def _finite(value: object):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _with_growth(inputs: Mapping[str, object], growth_pct: float) -> Dict[str, object]:
    result = copy.deepcopy(dict(inputs or {}))
    base_revenue = _finite(result.get("baseRevenue"))
    rows = [dict(item) for item in result.get("projectionYears") or [] if isinstance(item, Mapping)]
    if base_revenue is None and rows:
        base_revenue = _finite(rows[0].get("baseRevenue"))
    if base_revenue is None:
        return result
    rate = growth_pct / 100.0
    for index, row in enumerate(rows, start=1):
        row["revenue"] = base_revenue * ((1.0 + rate) ** index)
    result["projectionYears"] = rows
    result["reverseSolvedGrowthPct"] = growth_pct
    return result


def solve_implied_revenue_growth(
    inputs: Mapping[str, object],
    *,
    target_price: object,
    lower_growth_pct: float = -50.0,
    upper_growth_pct: float = 100.0,
    tolerance: float = None,
    iteration_cap: int = 120,
) -> Dict[str, object]:
    """Solve one constant revenue growth rate while keeping all else fixed."""

    target = _finite(target_price)
    lower = _finite(lower_growth_pct)
    upper = _finite(upper_growth_pct)
    reasons = []
    if target is None or target <= 0:
        reasons.append("invalid-target-price")
    if lower is None or upper is None or lower >= upper or lower <= -100:
        reasons.append("invalid-search-bracket")
    if not isinstance(iteration_cap, int) or iteration_cap <= 0 or iteration_cap > 1000:
        reasons.append("invalid-iteration-cap")
    base_revenue = _finite((inputs or {}).get("baseRevenue"))
    if base_revenue is None or base_revenue < 0:
        reasons.append("base-revenue-missing")
    effective_tolerance = tolerance if tolerance is not None else max(0.0001, (target or 0.0) * 0.000001)
    if effective_tolerance <= 0:
        reasons.append("invalid-tolerance")

    def blocked(extra=None):
        all_reasons = sorted(set(reasons + list(extra or [])))
        material = {
            "contractVersion": REVERSE_DCF_VERSION,
            "status": "blocked",
            "targetPrice": target,
            "searchBracketPct": [lower, upper],
            "blockedReasons": all_reasons,
        }
        digest = _digest({"inputs": inputs, "result": material})
        return {**material, "solverId": "reverse-dcf:" + digest[:32], "materialFingerprint": "reverse-dcf-material:" + digest}

    if reasons:
        return blocked()

    def evaluate(growth):
        result = calculate_driver_dcf(_with_growth(inputs, growth))
        value = _finite(result.get("valuePerShare")) if result.get("status") == "calculated" else None
        return result, value

    lower_result, lower_value = evaluate(lower)
    upper_result, upper_value = evaluate(upper)
    if lower_value is None or upper_value is None:
        errors = list(lower_result.get("blockedReasons") or []) + list(upper_result.get("blockedReasons") or [])
        return blocked(["forward-dcf-invalid-at-bracket", *errors])
    increasing = upper_value > lower_value
    if math.isclose(lower_value, upper_value, rel_tol=1e-12, abs_tol=1e-12):
        return blocked(["non-monotonic-or-flat-bracket"])
    minimum, maximum = sorted((lower_value, upper_value))
    if target < minimum - effective_tolerance or target > maximum + effective_tolerance:
        return blocked(["target-not-bracketed"])

    trace = []
    best_growth = lower
    best_result = lower_result
    best_residual = lower_value - target
    for iteration in range(1, iteration_cap + 1):
        midpoint = (lower + upper) / 2.0
        midpoint_result, midpoint_value = evaluate(midpoint)
        if midpoint_value is None:
            return blocked(["forward-dcf-invalid-during-search", *(midpoint_result.get("blockedReasons") or [])])
        residual = midpoint_value - target
        trace.append({
            "iteration": iteration,
            "growthPct": round(midpoint, 10),
            "valuePerShare": round(midpoint_value, 10),
            "residual": round(residual, 10),
        })
        if abs(residual) < abs(best_residual):
            best_growth, best_result, best_residual = midpoint, midpoint_result, residual
        if abs(residual) <= effective_tolerance:
            break
        if (residual < 0 and increasing) or (residual > 0 and not increasing):
            lower = midpoint
        else:
            upper = midpoint
    else:
        return blocked(["iteration-cap-reached"])

    material = {
        "contractVersion": REVERSE_DCF_VERSION,
        "status": "solved",
        "targetPrice": target,
        "quoteAsOf": str((inputs or {}).get("quoteAsOf") or ""),
        "freeVariable": "constant-revenue-growth-pct",
        "impliedRevenueGrowthPct": round(best_growth, 8),
        "forwardValuePerShare": round(float(best_result["valuePerShare"]), 8),
        "residual": round(best_residual, 10),
        "tolerance": effective_tolerance,
        "searchBracketPct": [lower_growth_pct, upper_growth_pct],
        "fixedAssumptions": {
            key: (inputs or {}).get(key)
            for key in (
                "waccPct", "terminalGrowthPct", "cash", "debt", "preferredEquity",
                "nonControllingInterest", "nonOperatingAssets", "dilutedShares", "sbcPolicy",
            )
        },
        "iterationCount": len(trace),
        "solverTrace": trace,
        "interpretation": "다른 가정을 고정했을 때 현재 가격과 일치하는 단일 매출 성장률입니다. 시장의 실제 기대를 관측한 값은 아닙니다.",
        "independentEvidence": False,
    }
    digest = _digest(material)
    return {
        **material,
        "solverId": "reverse-dcf:" + digest[:32],
        "materialFingerprint": "reverse-dcf-material:" + digest,
    }


__all__ = ["REVERSE_DCF_VERSION", "solve_implied_revenue_growth"]
