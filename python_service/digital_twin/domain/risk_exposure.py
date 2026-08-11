"""Raw portfolio exposure and mandate comparison contracts."""

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List


RISK_EXPOSURE_VERSION = "risk-exposure-v1"


@dataclass(frozen=True)
class ExposureMetric:
    exposure_id: str
    portfolio_id: str
    exposure_type: str
    key: str
    value: float
    ratio_pct: float
    policy_limit_pct: float = 0.0
    observed_at: str = ""
    source: str = "portfolio-ledger"

    @property
    def policy_delta_pct(self) -> float:
        return round(float(self.ratio_pct) - float(self.policy_limit_pct), 6)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["policyDeltaPct"] = self.policy_delta_pct
        payload["version"] = RISK_EXPOSURE_VERSION
        return payload

    def to_abox(self) -> Dict[str, object]:
        return {
            "tboxClass": "PositionExposure" if self.exposure_type == "position" else "ExposureSnapshot",
            "exposureType": self.exposure_type,
            "exposureKey": self.key,
            "exposureValue": self.value,
            "exposureRatio": self.ratio_pct,
            "policyLimitRatio": self.policy_limit_pct,
            "policyDeltaRatio": self.policy_delta_pct,
            "observedAt": self.observed_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class ExposureSnapshot:
    snapshot_id: str
    portfolio_id: str
    metrics: List[ExposureMetric] = field(default_factory=list)
    observed_at: str = ""

    def __post_init__(self) -> None:
        if any(item.portfolio_id != self.portfolio_id for item in self.metrics):
            raise ValueError("All exposure metrics must belong to one portfolio.")

    def over_policy_metrics(self) -> List[ExposureMetric]:
        return [item for item in self.metrics if item.policy_limit_pct > 0 and item.policy_delta_pct > 0]

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": RISK_EXPOSURE_VERSION,
            "snapshotId": self.snapshot_id,
            "portfolioId": self.portfolio_id,
            "observedAt": self.observed_at,
            "metrics": [item.to_dict() for item in self.metrics],
        }


def exposure_metrics(values: Iterable[Dict[str, object]]) -> List[ExposureMetric]:
    rows = []
    for item in values or []:
        rows.append(ExposureMetric(
            exposure_id=str(item.get("exposureId") or item.get("id") or ""),
            portfolio_id=str(item.get("portfolioId") or ""),
            exposure_type=str(item.get("exposureType") or "position"),
            key=str(item.get("key") or item.get("symbol") or ""),
            value=float(item.get("value") or 0),
            ratio_pct=float(item.get("ratioPct") or item.get("exposureRatio") or 0),
            policy_limit_pct=float(item.get("policyLimitPct") or item.get("policyLimitRatio") or 0),
            observed_at=str(item.get("observedAt") or ""),
            source=str(item.get("source") or "portfolio-ledger"),
        ))
    return rows
