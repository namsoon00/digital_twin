"""Decision, execution, and portfolio outcome review contracts."""

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List


INVESTMENT_OUTCOME_REVIEW_VERSION = "investment-outcome-review-v1"


@dataclass(frozen=True)
class PerformanceAttribution:
    attribution_id: str
    decision_episode_id: str
    action_plan_id: str = ""
    execution_episode_id: str = ""
    market_return_pct: float = 0.0
    instrument_return_pct: float = 0.0
    execution_cost: float = 0.0
    realized_profit_loss: float = 0.0
    currency_effect_pct: float = 0.0
    observed_at: str = ""
    horizon_minutes: int = 0
    benchmark_symbol: str = ""
    data_state: str = "partial"
    missing_data: List[str] = field(default_factory=list)

    @property
    def active_return_pct(self) -> float:
        return round(self.instrument_return_pct - self.market_return_pct, 6)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["activeReturnPct"] = self.active_return_pct
        payload["version"] = INVESTMENT_OUTCOME_REVIEW_VERSION
        return payload


@dataclass(frozen=True)
class DecisionReview:
    review_id: str
    decision_episode_id: str
    selected_hypothesis_status: str
    policy_compliant: bool
    execution_compliant: bool
    evidence_still_valid: bool
    attribution_ids: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    reviewed_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["version"] = INVESTMENT_OUTCOME_REVIEW_VERSION
        return payload


def decision_quality_summary(
    attributions: Iterable[Dict[str, object]],
    reviews: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    attribution_rows = [dict(item) for item in attributions or [] if isinstance(item, dict)]
    review_rows = [dict(item) for item in reviews or [] if isinstance(item, dict)]
    complete = [item for item in attribution_rows if str(item.get("data_state") or item.get("dataState") or "") == "complete"]
    active_returns = [
        float(item.get("activeReturnPct") or 0)
        for item in complete
        if item.get("activeReturnPct") not in (None, "")
    ]
    by_horizon = {}
    for item in complete:
        horizon = str(item.get("horizon_minutes") or item.get("horizonMinutes") or 0)
        bucket = by_horizon.setdefault(horizon, {"sampleCount": 0, "activeReturnTotal": 0.0, "positiveActiveCount": 0})
        active = float(item.get("activeReturnPct") or 0)
        bucket["sampleCount"] += 1
        bucket["activeReturnTotal"] += active
        bucket["positiveActiveCount"] += 1 if active > 0 else 0
    horizon_rows = {}
    for horizon, bucket in by_horizon.items():
        count = int(bucket["sampleCount"])
        horizon_rows[horizon] = {
            "sampleCount": count,
            "meanActiveReturnPct": round(bucket["activeReturnTotal"] / count, 6) if count else 0.0,
            "positiveActiveRatePct": round(bucket["positiveActiveCount"] / count * 100, 2) if count else 0.0,
        }
    execution_compliant = sum(1 for item in review_rows if bool(item.get("execution_compliant", item.get("executionCompliant", False))))
    evidence_valid = sum(1 for item in review_rows if bool(item.get("evidence_still_valid", item.get("evidenceStillValid", False))))
    return {
        "version": INVESTMENT_OUTCOME_REVIEW_VERSION,
        "sampleCount": len(attribution_rows),
        "completeSampleCount": len(complete),
        "partialSampleCount": len(attribution_rows) - len(complete),
        "meanActiveReturnPct": round(sum(active_returns) / len(active_returns), 6) if active_returns else 0.0,
        "positiveActiveRatePct": round(sum(1 for value in active_returns if value > 0) / len(active_returns) * 100, 2) if active_returns else 0.0,
        "reviewCount": len(review_rows),
        "executionComplianceRatePct": round(execution_compliant / len(review_rows) * 100, 2) if review_rows else 0.0,
        "evidenceValidityRatePct": round(evidence_valid / len(review_rows) * 100, 2) if review_rows else 0.0,
        "byHorizonMinutes": horizon_rows,
        "dataState": "complete" if complete else "partial",
    }
