"""Portfolio allocation drift and review-only rebalance proposals."""

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Dict, Iterable, List


PORTFOLIO_REBALANCING_VERSION = "portfolio-rebalancing-v1"


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

    def __post_init__(self) -> None:
        if str(self.side or "").upper() not in {"INCREASE", "DECREASE", "HOLD"}:
            raise ValueError("Rebalance leg side must be INCREASE, DECREASE, or HOLD.")

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RebalanceProposal:
    proposal_id: str
    portfolio_id: str
    mandate_version: str
    exposure_snapshot_id: str
    drifts: List[AllocationDrift] = field(default_factory=list)
    legs: List[RebalanceLeg] = field(default_factory=list)
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
        created_at: str = "",
    ):
        raw = "|".join([str(portfolio_id), str(mandate_version), str(exposure_snapshot_id)])
        return cls(
            proposal_id="rebalance-proposal:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
            portfolio_id=str(portfolio_id or ""),
            mandate_version=str(mandate_version or ""),
            exposure_snapshot_id=str(exposure_snapshot_id or ""),
            drifts=list(drifts or []),
            legs=list(legs or []),
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
