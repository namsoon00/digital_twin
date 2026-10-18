"""Cash-conserving arithmetic for review proposals, without action selection."""
from dataclasses import replace
import math


VERSION = "rebalance-arithmetic-v1"
RISK_FIELDS = ("annualizedVolatilityPct", "maximumDrawdownPct", "maximumPairwiseCorrelation")


def size_legs(legs, total, cash, turnover_limit_pct, cost_bps, cash_floor_pct=0):
    """Scale all requested changes together; do not rank or invent trades."""
    if not all(math.isfinite(x) and x >= 0 for x in (total, cash, turnover_limit_pct, cost_bps, cash_floor_pct)):
        raise ValueError("rebalance arithmetic requires finite nonnegative inputs")
    if not total:
        return []
    rate = cost_bps / 10000
    requested = sum(row.maximum_notional for row in legs)
    scale = min(1.0, turnover_limit_pct * total / 100 / requested) if requested else 0
    def rounded(value):
        return math.floor(value * 10000) / 10000
    sized = [replace(row, maximum_notional=rounded(row.maximum_notional * scale)) for row in legs]
    sales = sum(row.maximum_notional for row in sized if row.side == "DECREASE")
    buys = sum(row.maximum_notional for row in sized if row.side == "INCREASE")
    # A sale with costs above its proceeds cannot finance a cash deficit.
    if rate > 1 and sales * (rate - 1) > cash:
        sale_scale = cash / (sales * (rate - 1)) if sales else 0
        sized = [replace(row, maximum_notional=rounded(row.maximum_notional * sale_scale))
                 if row.side == "DECREASE" else row for row in sized]
        sales = sum(row.maximum_notional for row in sized if row.side == "DECREASE")
    floor = min(100, cash_floor_pct) / 100
    available = cash + sales * (1 - rate) - floor * (total - rate * sales)
    buy_limit = max(0.0, available / (1 + rate * (1 - floor)))
    buy_scale = min(1.0, buy_limit / buys) if buys else 0
    sized = [replace(row, maximum_notional=rounded(row.maximum_notional * buy_scale))
             if row.side == "INCREASE" else row for row in sized]
    # Round down notionals: rounding up can breach the shared cap or funding.
    sized = [row for row in sized if row.maximum_notional >= .0001]
    cost = sum(row.maximum_notional * rate for row in sized)
    after_total = total - cost
    if after_total <= 0:
        return []
    return reweight_legs([replace(row, estimated_cost=row.maximum_notional * rate) for row in sized], total)


def reweight_legs(legs, total):
    after_total = total - sum(row.estimated_cost for row in legs)
    return [replace(row,
        after_weight_pct=(row.before_weight_pct * total / 100 +
            row.maximum_notional * (1 if row.side == "INCREASE" else -1)) / after_total * 100)
        for row in legs] if after_total > 0 else []


def simulate(legs, drifts, total, cash, turnover_limit_pct):
    cost = sum(row.estimated_cost for row in legs)
    after_total = total - cost
    after_cash = cash + sum(row.maximum_notional * (1 if row.side == "DECREASE" else -1)
                            - row.estimated_cost for row in legs)
    by_key = {row.allocation_key: row for row in legs}
    checks = []
    for drift in drifts:
        kind, _, key = drift.allocation_key.partition(":")
        after = None
        if kind == "position" and after_total > 0:
            leg = by_key.get(drift.allocation_key)
            delta = leg.maximum_notional * (1 if leg.side == "INCREASE" else -1) if leg else 0
            after = (drift.current_weight_pct * total / 100 + delta) / after_total * 100
        elif kind == "cash" and key == "KRW" and after_total > 0:
            after = after_cash / after_total * 100
        checks.append({"allocationKey": drift.allocation_key, "afterWeightPct": after,
            "withinBand": None if after is None else
                drift.band.minimum_weight_pct - 1e-6 <= after <= drift.band.maximum_weight_pct + 1e-6})
    turnover = sum(row.maximum_notional for row in legs) / total * 100 if total else 0
    return {"arithmeticVersion": VERSION, "portfolioTotal": after_total, "cash": after_cash,
        "cashWeightPct": after_cash / after_total * 100 if after_total > 0 else None,
        "allocationBandChecks": checks,
        "allocationBandsRestored": bool(checks) and all(row["withinBand"] is True for row in checks),
        "fundingFeasible": after_cash >= -1e-6 and after_total > 0,
        "turnoverWithinLimit": turnover <= turnover_limit_pct + 1e-6,
        "grossTurnoverPct": turnover, "turnoverLimitPct": turnover_limit_pct, "estimatedCost": cost,
        "riskSimulationState": "not-simulated", **{key: None for key in RISK_FIELDS}}
