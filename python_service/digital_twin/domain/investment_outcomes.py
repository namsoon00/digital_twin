"""Decision, execution, and portfolio outcome review contracts."""

from dataclasses import asdict, dataclass, field
from typing import Dict, List


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
