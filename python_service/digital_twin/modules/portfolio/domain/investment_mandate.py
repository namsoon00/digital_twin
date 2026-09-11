"""Versioned portfolio policy facts used by TypeDB and execution guards."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Dict, Iterable, List


INVESTMENT_MANDATE_VERSION = "investment-mandate-v2"
DEFAULT_ALLOWED_ACTIONS = ("BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID")


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _actions(values: Iterable[object]) -> List[str]:
    return list(dict.fromkeys(
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    ))


def _first_present(values: Dict[str, object], *keys: str, default=None):
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return default


@dataclass(frozen=True)
class InvestmentMandate:
    mandate_id: str
    portfolio_id: str
    account_id: str
    profile: str
    risk_tolerance: str
    time_horizon: str
    loss_tolerance_pct: float
    profit_protection_pct: float
    max_position_weight_pct: float
    max_sector_weight_pct: float
    fx_exposure_review_pct: float
    min_cash_weight_pct: float
    allowed_actions: List[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_ACTIONS))
    add_buy_policy: str = ""
    holding_action_policy: str = ""
    watchlist_action_policy: str = ""
    target_allocations: Dict[str, float] = field(default_factory=dict)
    allocation_band_pct: float = 5.0
    max_portfolio_volatility_pct: float = 35.0
    max_portfolio_drawdown_pct: float = 20.0
    max_pairwise_correlation: float = 0.85
    max_rebalance_turnover_pct: float = 20.0
    estimated_transaction_cost_bps: float = 15.0
    benchmark_symbols: Dict[str, str] = field(default_factory=lambda: {
        "KR": "KOSPI",
        "US": "SPY",
        "CRYPTO": "BTC",
    })
    effective_at: str = ""
    version: str = INVESTMENT_MANDATE_VERSION
    fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_actions", _actions(self.allowed_actions))
        if not self.portfolio_id or not self.account_id:
            raise ValueError("InvestmentMandate requires portfolio_id and account_id.")
        if not 0 <= self.max_position_weight_pct <= 100:
            raise ValueError("max_position_weight_pct must be between 0 and 100.")
        if not 0 <= self.max_sector_weight_pct <= 100:
            raise ValueError("max_sector_weight_pct must be between 0 and 100.")
        if not 0 <= self.min_cash_weight_pct <= 100:
            raise ValueError("min_cash_weight_pct must be between 0 and 100.")
        if not 0 <= self.allocation_band_pct <= 100:
            raise ValueError("allocation_band_pct must be between 0 and 100.")
        if not 0 <= self.max_pairwise_correlation <= 1:
            raise ValueError("max_pairwise_correlation must be between 0 and 1.")
        if not 0 <= self.max_rebalance_turnover_pct <= 100:
            raise ValueError("max_rebalance_turnover_pct must be between 0 and 100.")
        if self.max_portfolio_volatility_pct < 0:
            raise ValueError("max_portfolio_volatility_pct cannot be negative.")
        if self.max_portfolio_drawdown_pct < 0:
            raise ValueError("max_portfolio_drawdown_pct cannot be negative.")
        if self.estimated_transaction_cost_bps < 0:
            raise ValueError("estimated_transaction_cost_bps cannot be negative.")
        raw_allocations = {
            str(key or "").strip(): _number(value)
            for key, value in dict(self.target_allocations or {}).items()
            if str(key or "").strip()
        }
        if any(value < 0 or value > 100 for value in raw_allocations.values()):
            raise ValueError("target_allocations must be between 0 and 100 percent.")
        allocations = raw_allocations
        if sum(allocations.values()) > 100.000001:
            raise ValueError("target_allocations cannot exceed 100 percent.")
        object.__setattr__(self, "target_allocations", allocations)
        object.__setattr__(self, "benchmark_symbols", {
            str(key or "").upper().strip(): str(value or "").upper().strip()
            for key, value in dict(self.benchmark_symbols or {}).items()
            if str(key or "").strip() and str(value or "").strip()
        })
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", self.calculate_fingerprint())

    @classmethod
    def from_profile(
        cls,
        account_id: str,
        portfolio_id: str,
        profile: Dict[str, object],
        effective_at: str = "",
    ):
        values = dict(profile or {})
        mandate_id = "investment-mandate:" + str(portfolio_id or account_id or "default")
        return cls(
            mandate_id=mandate_id,
            portfolio_id=str(portfolio_id or "portfolio:" + str(account_id or "default")),
            account_id=str(account_id or "default"),
            profile=str(values.get("profile") or "balanced"),
            risk_tolerance=str(values.get("riskTolerance") or "medium"),
            time_horizon=str(values.get("timeHorizon") or "mid"),
            loss_tolerance_pct=_number(values.get("lossTolerancePct"), -8),
            profit_protection_pct=_number(values.get("profitProtectionPct"), 12),
            max_position_weight_pct=_number(values.get("maxPositionWeightPct"), 25),
            max_sector_weight_pct=_number(values.get("maxSectorWeightPct"), 45),
            fx_exposure_review_pct=_number(values.get("fxExposureReviewPct"), 12),
            min_cash_weight_pct=_number(values.get("minCashWeightPct"), 10),
            allowed_actions=_actions(values.get("allowedActions") or DEFAULT_ALLOWED_ACTIONS),
            add_buy_policy=str(values.get("addBuyPolicy") or ""),
            holding_action_policy=str(values.get("holdingActionPolicy") or ""),
            watchlist_action_policy=str(values.get("watchlistActionPolicy") or ""),
            target_allocations=dict(values.get("targetAllocations") or {}),
            allocation_band_pct=_number(values.get("allocationBandPct"), 5),
            max_portfolio_volatility_pct=_number(values.get("maxPortfolioVolatilityPct"), 35),
            max_portfolio_drawdown_pct=_number(values.get("maxPortfolioDrawdownPct"), 20),
            max_pairwise_correlation=_number(values.get("maxPairwiseCorrelation"), 0.85),
            max_rebalance_turnover_pct=_number(values.get("maxRebalanceTurnoverPct"), 20),
            estimated_transaction_cost_bps=_number(values.get("estimatedTransactionCostBps"), 15),
            benchmark_symbols=dict(values.get("benchmarkSymbols") or {"KR": "KOSPI", "US": "SPY", "CRYPTO": "BTC"}),
            effective_at=str(effective_at or ""),
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        values = dict(payload or {})
        return cls(
            mandate_id=str(_first_present(values, "mandateId", "mandate_id", default="")),
            portfolio_id=str(_first_present(values, "portfolioId", "portfolio_id", default="")),
            account_id=str(_first_present(values, "accountId", "account_id", default="")),
            profile=str(values.get("profile") or "balanced"),
            risk_tolerance=str(_first_present(values, "riskTolerance", "risk_tolerance", default="medium")),
            time_horizon=str(_first_present(values, "timeHorizon", "time_horizon", default="mid")),
            loss_tolerance_pct=_number(_first_present(values, "lossTolerancePct", "loss_tolerance_pct", default=-8), -8),
            profit_protection_pct=_number(_first_present(values, "profitProtectionPct", "profit_protection_pct", default=12), 12),
            max_position_weight_pct=_number(_first_present(values, "maxPositionWeightPct", "max_position_weight_pct", default=25), 25),
            max_sector_weight_pct=_number(_first_present(values, "maxSectorWeightPct", "max_sector_weight_pct", default=45), 45),
            fx_exposure_review_pct=_number(_first_present(values, "fxExposureReviewPct", "fx_exposure_review_pct", default=12), 12),
            min_cash_weight_pct=_number(_first_present(values, "minCashWeightPct", "min_cash_weight_pct", default=10), 10),
            allowed_actions=_actions(_first_present(values, "allowedActions", "allowed_actions", default=DEFAULT_ALLOWED_ACTIONS)),
            add_buy_policy=str(_first_present(values, "addBuyPolicy", "add_buy_policy", default="")),
            holding_action_policy=str(_first_present(values, "holdingActionPolicy", "holding_action_policy", default="")),
            watchlist_action_policy=str(_first_present(values, "watchlistActionPolicy", "watchlist_action_policy", default="")),
            target_allocations=dict(_first_present(values, "targetAllocations", "target_allocations", default={}) or {}),
            allocation_band_pct=_number(_first_present(values, "allocationBandPct", "allocation_band_pct", default=5), 5),
            max_portfolio_volatility_pct=_number(_first_present(values, "maxPortfolioVolatilityPct", "max_portfolio_volatility_pct", default=35), 35),
            max_portfolio_drawdown_pct=_number(_first_present(values, "maxPortfolioDrawdownPct", "max_portfolio_drawdown_pct", default=20), 20),
            max_pairwise_correlation=_number(_first_present(values, "maxPairwiseCorrelation", "max_pairwise_correlation", default=0.85), 0.85),
            max_rebalance_turnover_pct=_number(_first_present(values, "maxRebalanceTurnoverPct", "max_rebalance_turnover_pct", default=20), 20),
            estimated_transaction_cost_bps=_number(_first_present(values, "estimatedTransactionCostBps", "estimated_transaction_cost_bps", default=15), 15),
            benchmark_symbols=dict(_first_present(values, "benchmarkSymbols", "benchmark_symbols", default={"KR": "KOSPI", "US": "SPY", "CRYPTO": "BTC"}) or {}),
            effective_at=str(_first_present(values, "effectiveAt", "effective_at", default="")),
            version=str(values.get("version") or INVESTMENT_MANDATE_VERSION),
            fingerprint=str(values.get("fingerprint") or values.get("policyFingerprint") or ""),
        )

    def calculate_fingerprint(self) -> str:
        payload = asdict(self)
        payload.pop("fingerprint", None)
        payload.pop("effective_at", None)
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    @property
    def policy_version(self) -> str:
        return self.version + ":" + self.fingerprint

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["policyVersion"] = self.policy_version
        return payload

    def to_abox(self) -> Dict[str, object]:
        return {
            "tboxClass": "InvestmentMandate",
            "mandateId": self.mandate_id,
            "portfolioId": self.portfolio_id,
            "accountId": self.account_id,
            "profile": self.profile,
            "riskTolerance": self.risk_tolerance,
            "timeHorizon": self.time_horizon,
            "lossTolerancePct": self.loss_tolerance_pct,
            "profitProtectionPct": self.profit_protection_pct,
            "maxPositionWeightPct": self.max_position_weight_pct,
            "maxSectorWeightPct": self.max_sector_weight_pct,
            "fxExposureReviewPct": self.fx_exposure_review_pct,
            "minCashWeightPct": self.min_cash_weight_pct,
            "targetAllocations": dict(self.target_allocations),
            "allocationBandPct": self.allocation_band_pct,
            "maxPortfolioVolatilityPct": self.max_portfolio_volatility_pct,
            "maxPortfolioDrawdownPct": self.max_portfolio_drawdown_pct,
            "maxPairwiseCorrelation": self.max_pairwise_correlation,
            "maxRebalanceTurnoverPct": self.max_rebalance_turnover_pct,
            "estimatedTransactionCostBps": self.estimated_transaction_cost_bps,
            "benchmarkSymbols": dict(self.benchmark_symbols),
            "allowedActions": list(self.allowed_actions),
            "policyVersion": self.policy_version,
            "policyFingerprint": self.fingerprint,
            "effectiveAt": self.effective_at,
        }
