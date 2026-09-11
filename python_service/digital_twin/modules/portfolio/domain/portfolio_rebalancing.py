"""Portfolio allocation drift and review-only rebalance proposals."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Dict, Iterable, List, Mapping, Optional


PORTFOLIO_REBALANCING_VERSION = "portfolio-rebalancing-v2"
REBALANCE_STATE_VERSION = "portfolio-rebalance-state-v1"
REBALANCE_EXPOSURE_DELTA_PCT = 1.0
REBALANCE_VOLATILITY_DELTA_PCT = 3.0
REBALANCE_DRAWDOWN_DELTA_PCT = 2.0
REBALANCE_CORRELATION_DELTA = 0.10
REBALANCE_NOTIONAL_DELTA_KRW = 500_000.0
REBALANCE_NOTIONAL_DELTA_RATIO = 0.10


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _stable_hash(prefix: str, payload: Mapping[str, object]) -> str:
    raw = json.dumps(dict(payload or {}), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class RebalanceState:
    """Current policy fact state; it never chooses an investment action."""

    portfolio_id: str
    policy_version: str
    status: str
    semantic_fingerprint: str
    breach_keys: List[str] = field(default_factory=list)
    adjustment_directions: Dict[str, str] = field(default_factory=dict)
    exposure_deltas_pct: Dict[str, float] = field(default_factory=dict)
    maximum_notional_by_symbol: Dict[str, float] = field(default_factory=dict)
    volatility_policy_delta_pct: float = 0.0
    drawdown_policy_delta_pct: float = 0.0
    correlation_policy_delta: float = 0.0
    data_state: str = "partial"
    observed_at: str = ""
    exposure_snapshot_id: str = ""
    risk_snapshot_id: str = ""
    proposal_id: str = ""
    version: str = REBALANCE_STATE_VERSION

    @classmethod
    def from_analysis(
        cls,
        portfolio_id: str,
        policy_version: str,
        exposure,
        risk=None,
        proposal=None,
    ):
        metrics = list(getattr(exposure, "metrics", []) or [])
        breached = [
            item for item in metrics
            if _number(getattr(item, "policy_limit_pct", 0)) > 0
            and _number(getattr(item, "policy_delta_pct", 0)) > 0
        ]
        exposure_deltas = {
            str(getattr(item, "exposure_type", "") or "") + ":" + str(getattr(item, "key", "") or ""):
            round(_number(getattr(item, "policy_delta_pct", 0)), 6)
            for item in breached
        }
        directions = {}
        notionals = {}
        for leg in list(getattr(proposal, "legs", []) or []):
            key = str(getattr(leg, "allocation_key", "") or "")
            if not key:
                continue
            directions[key] = str(getattr(leg, "side", "") or "").upper()
            symbol = str(getattr(leg, "symbol", "") or "").upper().strip()
            if symbol:
                notionals[symbol] = round(_number(getattr(leg, "maximum_notional", 0)), 4)
        volatility_delta = _number(getattr(risk, "volatility_policy_delta_pct", 0))
        drawdown_delta = _number(getattr(risk, "drawdown_policy_delta_pct", 0))
        correlation_delta = _number(getattr(risk, "correlation_policy_delta", 0))
        risk_breaches = []
        if volatility_delta > 0:
            risk_breaches.append("risk:volatility")
        if drawdown_delta > 0:
            risk_breaches.append("risk:drawdown")
        if correlation_delta > 0:
            risk_breaches.append("risk:correlation")
        breach_keys = sorted(set(exposure_deltas) | set(risk_breaches))
        risk_data_state = str(getattr(risk, "data_state", "") or "partial")
        if not metrics:
            status = "DATA_BLOCKED"
        elif breach_keys:
            status = "POLICY_BREACH"
        else:
            status = "WITHIN_POLICY"
        semantic = {
            "policyVersion": str(policy_version or ""),
            "status": status,
            "breachKeys": breach_keys,
            "adjustmentDirections": dict(sorted(directions.items())),
            "dataState": risk_data_state,
        }
        return cls(
            portfolio_id=str(portfolio_id or ""),
            policy_version=str(policy_version or ""),
            status=status,
            semantic_fingerprint=_stable_hash("rebalance-state:", semantic),
            breach_keys=breach_keys,
            adjustment_directions=dict(sorted(directions.items())),
            exposure_deltas_pct=dict(sorted(exposure_deltas.items())),
            maximum_notional_by_symbol=dict(sorted(notionals.items())),
            volatility_policy_delta_pct=round(volatility_delta, 6),
            drawdown_policy_delta_pct=round(drawdown_delta, 6),
            correlation_policy_delta=round(correlation_delta, 6),
            data_state=risk_data_state,
            observed_at=str(getattr(exposure, "observed_at", "") or ""),
            exposure_snapshot_id=str(getattr(exposure, "snapshot_id", "") or ""),
            risk_snapshot_id=str(getattr(risk, "risk_snapshot_id", "") or ""),
            proposal_id=str(getattr(proposal, "proposal_id", "") or ""),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        values = dict(payload or {})
        return cls(
            portfolio_id=str(values.get("portfolioId") or values.get("portfolio_id") or ""),
            policy_version=str(values.get("policyVersion") or values.get("policy_version") or ""),
            status=str(values.get("status") or "WITHIN_POLICY"),
            semantic_fingerprint=str(values.get("semanticFingerprint") or values.get("semantic_fingerprint") or ""),
            breach_keys=list(values.get("breachKeys") or values.get("breach_keys") or []),
            adjustment_directions=dict(values.get("adjustmentDirections") or values.get("adjustment_directions") or {}),
            exposure_deltas_pct={
                str(key): _number(value)
                for key, value in dict(values.get("exposureDeltasPct") or values.get("exposure_deltas_pct") or {}).items()
            },
            maximum_notional_by_symbol={
                str(key): _number(value)
                for key, value in dict(values.get("maximumNotionalBySymbol") or values.get("maximum_notional_by_symbol") or {}).items()
            },
            volatility_policy_delta_pct=_number(values.get("volatilityPolicyDeltaPct") or values.get("volatility_policy_delta_pct")),
            drawdown_policy_delta_pct=_number(values.get("drawdownPolicyDeltaPct") or values.get("drawdown_policy_delta_pct")),
            correlation_policy_delta=_number(values.get("correlationPolicyDelta") or values.get("correlation_policy_delta")),
            data_state=str(values.get("dataState") or values.get("data_state") or "partial"),
            observed_at=str(values.get("observedAt") or values.get("observed_at") or ""),
            exposure_snapshot_id=str(values.get("exposureSnapshotId") or values.get("exposure_snapshot_id") or ""),
            risk_snapshot_id=str(values.get("riskSnapshotId") or values.get("risk_snapshot_id") or ""),
            proposal_id=str(values.get("proposalId") or values.get("proposal_id") or ""),
            version=str(values.get("version") or REBALANCE_STATE_VERSION),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "portfolioId": self.portfolio_id,
            "policyVersion": self.policy_version,
            "status": self.status,
            "semanticFingerprint": self.semantic_fingerprint,
            "breachKeys": list(self.breach_keys),
            "adjustmentDirections": dict(self.adjustment_directions),
            "exposureDeltasPct": dict(self.exposure_deltas_pct),
            "maximumNotionalBySymbol": dict(self.maximum_notional_by_symbol),
            "volatilityPolicyDeltaPct": self.volatility_policy_delta_pct,
            "drawdownPolicyDeltaPct": self.drawdown_policy_delta_pct,
            "correlationPolicyDelta": self.correlation_policy_delta,
            "dataState": self.data_state,
            "observedAt": self.observed_at,
            "exposureSnapshotId": self.exposure_snapshot_id,
            "riskSnapshotId": self.risk_snapshot_id,
            "proposalId": self.proposal_id,
        }


@dataclass(frozen=True)
class RebalanceTransition:
    transition_type: str
    previous_state: Optional[RebalanceState]
    current_state: RebalanceState
    reason_codes: List[str]
    revision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "transitionType": self.transition_type,
            "reasonCodes": list(self.reason_codes),
            "revision": self.revision,
            "previousState": self.previous_state.to_dict() if self.previous_state else {},
            "currentState": self.current_state.to_dict(),
        }


def _maximum_mapping_delta(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    keys = set(left or {}) | set(right or {})
    return max([abs(_number((left or {}).get(key)) - _number((right or {}).get(key))) for key in keys] or [0.0])


def rebalance_transition(
    previous: Optional[RebalanceState],
    current: RebalanceState,
) -> Optional[RebalanceTransition]:
    """Return only an event-worthy transition against the last emitted baseline."""

    if previous is None:
        if current.status != "POLICY_BREACH":
            return None
        transition_type = "OPENED"
        reasons = ["initial-policy-breach"]
    elif previous.status == "POLICY_BREACH" and current.status == "WITHIN_POLICY":
        transition_type = "RESOLVED"
        reasons = ["policy-breach-resolved"]
    elif previous.status != "POLICY_BREACH" and current.status == "POLICY_BREACH":
        transition_type = "OPENED"
        reasons = ["policy-breach-opened"]
    else:
        transition_type = "UPDATED"
        reasons = []
        if previous.policy_version != current.policy_version:
            reasons.append("policy-version-change")
        if previous.status != current.status:
            reasons.append("rebalance-status-change")
        if previous.data_state != current.data_state:
            reasons.append("data-state-change")
        if set(previous.breach_keys) != set(current.breach_keys):
            reasons.append("breach-set-change")
        if previous.adjustment_directions != current.adjustment_directions:
            reasons.append("adjustment-direction-change")
        if _maximum_mapping_delta(previous.exposure_deltas_pct, current.exposure_deltas_pct) >= REBALANCE_EXPOSURE_DELTA_PCT:
            reasons.append("exposure-delta-material-change")
        if abs(previous.volatility_policy_delta_pct - current.volatility_policy_delta_pct) >= REBALANCE_VOLATILITY_DELTA_PCT:
            reasons.append("volatility-delta-material-change")
        if abs(previous.drawdown_policy_delta_pct - current.drawdown_policy_delta_pct) >= REBALANCE_DRAWDOWN_DELTA_PCT:
            reasons.append("drawdown-delta-material-change")
        if abs(previous.correlation_policy_delta - current.correlation_policy_delta) >= REBALANCE_CORRELATION_DELTA:
            reasons.append("correlation-delta-material-change")
        for symbol in set(previous.maximum_notional_by_symbol) | set(current.maximum_notional_by_symbol):
            old = _number(previous.maximum_notional_by_symbol.get(symbol))
            new = _number(current.maximum_notional_by_symbol.get(symbol))
            absolute_delta = abs(new - old)
            relative_delta = absolute_delta / max(abs(old), 1.0)
            if absolute_delta >= REBALANCE_NOTIONAL_DELTA_KRW and relative_delta >= REBALANCE_NOTIONAL_DELTA_RATIO:
                reasons.append("rebalance-notional-material-change")
                break
        if not reasons:
            return None
    revision_payload = {
        "transitionType": transition_type,
        "current": current.to_dict(),
        "reasons": sorted(set(reasons)),
    }
    return RebalanceTransition(
        transition_type=transition_type,
        previous_state=previous,
        current_state=current,
        reason_codes=list(dict.fromkeys(reasons)),
        revision=_stable_hash("rebalance-revision:", revision_payload),
    )


@dataclass(frozen=True)
class AllocationBand:
    allocation_key: str
    target_weight_pct: float
    minimum_weight_pct: float
    maximum_weight_pct: float

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_weight_pct <= self.target_weight_pct <= self.maximum_weight_pct <= 100:
            raise ValueError("Allocation band must satisfy 0 <= min <= target <= max <= 100.")

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AllocationDrift:
    allocation_key: str
    current_weight_pct: float
    band: AllocationBand

    @property
    def target_delta_pct(self) -> float:
        return round(self.current_weight_pct - self.band.target_weight_pct, 6)

    @property
    def band_delta_pct(self) -> float:
        if self.current_weight_pct < self.band.minimum_weight_pct:
            return round(self.current_weight_pct - self.band.minimum_weight_pct, 6)
        if self.current_weight_pct > self.band.maximum_weight_pct:
            return round(self.current_weight_pct - self.band.maximum_weight_pct, 6)
        return 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "allocationKey": self.allocation_key,
            "currentWeightPct": self.current_weight_pct,
            "targetDeltaPct": self.target_delta_pct,
            "bandDeltaPct": self.band_delta_pct,
            "band": self.band.to_dict(),
        }


@dataclass(frozen=True)
class RebalanceLeg:
    allocation_key: str
    side: str
    target_delta_pct: float
    maximum_notional: float = 0.0
    symbol: str = ""
    estimated_cost: float = 0.0
    before_weight_pct: float = 0.0
    after_weight_pct: float = 0.0
    rationale: str = ""

    def __post_init__(self) -> None:
        if str(self.side or "").upper() not in {"INCREASE", "DECREASE", "HOLD"}:
            raise ValueError("Rebalance leg side must be INCREASE, DECREASE, or HOLD.")
        if self.maximum_notional < 0 or self.estimated_cost < 0:
            raise ValueError("Rebalance leg notional and cost cannot be negative.")

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RebalanceScenario:
    scenario_id: str
    scenario_type: str
    label: str
    legs: List[RebalanceLeg] = field(default_factory=list)
    before_metrics: Dict[str, object] = field(default_factory=dict)
    after_metrics: Dict[str, object] = field(default_factory=dict)
    estimated_cost: float = 0.0
    turnover_pct: float = 0.0
    policy_effects: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    data_state: str = "partial"
    missing_data: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            **asdict(self),
            "legs": [item.to_dict() for item in self.legs],
        }


@dataclass(frozen=True)
class RebalanceProposal:
    proposal_id: str
    portfolio_id: str
    mandate_version: str
    exposure_snapshot_id: str
    drifts: List[AllocationDrift] = field(default_factory=list)
    legs: List[RebalanceLeg] = field(default_factory=list)
    scenarios: List[RebalanceScenario] = field(default_factory=list)
    recommended_scenario_id: str = ""
    status: str = "review-required"
    created_at: str = ""

    @classmethod
    def create(
        cls,
        portfolio_id: str,
        mandate_version: str,
        exposure_snapshot_id: str,
        drifts: Iterable[AllocationDrift],
        legs: Iterable[RebalanceLeg] = None,
        scenarios: Iterable[RebalanceScenario] = None,
        recommended_scenario_id: str = "",
        created_at: str = "",
    ):
        drift_rows = list(drifts or [])
        leg_rows = list(legs or [])
        scenario_rows = list(scenarios or [])
        drift_signature = sorted(
            str(item.allocation_key or "") + ":" + (
                "ABOVE" if item.band_delta_pct > 0 else "BELOW" if item.band_delta_pct < 0 else "WITHIN"
            )
            for item in drift_rows
            if item.band_delta_pct
        )
        leg_signature = sorted(
            str(item.allocation_key or "") + ":" + str(item.side or "").upper()
            for item in leg_rows
            if str(item.side or "").upper() != "HOLD"
        )
        raw = json.dumps({
            "portfolioId": str(portfolio_id or ""),
            "mandateVersion": str(mandate_version or ""),
            "drifts": drift_signature,
            "legs": leg_signature,
        }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return cls(
            proposal_id="rebalance-proposal:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
            portfolio_id=str(portfolio_id or ""),
            mandate_version=str(mandate_version or ""),
            exposure_snapshot_id=str(exposure_snapshot_id or ""),
            drifts=drift_rows,
            legs=leg_rows,
            scenarios=scenario_rows,
            recommended_scenario_id=str(recommended_scenario_id or ""),
            created_at=str(created_at or ""),
        )

    def validate(self) -> List[str]:
        errors = []
        duplicate_keys = len({item.allocation_key for item in self.drifts}) != len(self.drifts)
        if duplicate_keys:
            errors.append("duplicate-allocation-drift")
        if any(item.maximum_notional < 0 for item in self.legs):
            errors.append("negative-maximum-notional")
        drift_keys = {item.allocation_key for item in self.drifts if item.band_delta_pct != 0}
        if any(item.allocation_key not in drift_keys for item in self.legs if item.side != "HOLD"):
            errors.append("leg-without-band-drift")
        scenario_ids = [item.scenario_id for item in self.scenarios]
        if len(set(scenario_ids)) != len(scenario_ids):
            errors.append("duplicate-rebalance-scenario")
        if self.recommended_scenario_id and self.recommended_scenario_id not in set(scenario_ids):
            errors.append("recommended-scenario-missing")
        return errors

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": PORTFOLIO_REBALANCING_VERSION,
            "proposalId": self.proposal_id,
            "portfolioId": self.portfolio_id,
            "mandateVersion": self.mandate_version,
            "exposureSnapshotId": self.exposure_snapshot_id,
            "drifts": [item.to_dict() for item in self.drifts],
            "legs": [item.to_dict() for item in self.legs],
            "scenarios": [item.to_dict() for item in self.scenarios],
            "recommendedScenarioId": self.recommended_scenario_id,
            "status": self.status,
            "createdAt": self.created_at,
        }


def allocation_drifts(current_weights: Dict[str, object], bands: Iterable[AllocationBand]) -> List[AllocationDrift]:
    return [
        AllocationDrift(
            allocation_key=band.allocation_key,
            current_weight_pct=float(current_weights.get(band.allocation_key) or 0),
            band=band,
        )
        for band in bands or []
    ]
