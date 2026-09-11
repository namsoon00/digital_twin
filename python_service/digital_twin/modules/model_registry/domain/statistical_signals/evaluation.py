"""Point-in-time outcome and calibration contracts for model signals."""

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple


MODEL_SIGNAL_EVALUATION_VERSION = "statistical-model-signal-evaluation-v1"


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class ModelSignalOutcome:
    signal_id: str
    signal_type: str
    subject_id: str
    horizon: str
    observed_at: str
    outcome_at: str
    polarity: str
    score: float
    probability: Optional[float]
    forward_return: float
    maximum_favorable_excursion: float
    maximum_adverse_excursion: float
    point_in_time_verified: bool = True

    def success(self) -> bool:
        if self.polarity == "risk":
            return self.forward_return < 0
        if self.polarity == "support":
            return self.forward_return > 0
        return False

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": MODEL_SIGNAL_EVALUATION_VERSION,
            "signalId": self.signal_id,
            "signalType": self.signal_type,
            "subjectId": self.subject_id,
            "horizon": self.horizon,
            "observedAt": self.observed_at,
            "outcomeAt": self.outcome_at,
            "polarity": self.polarity,
            "score": self.score,
            "probability": self.probability,
            "forwardReturn": self.forward_return,
            "maximumFavorableExcursion": self.maximum_favorable_excursion,
            "maximumAdverseExcursion": self.maximum_adverse_excursion,
            "pointInTimeVerified": self.point_in_time_verified,
            "success": self.success(),
        }


def model_signal_evaluation_report(
    outcomes: Iterable[ModelSignalOutcome],
    minimum_sample_count: int = 60,
) -> Dict[str, object]:
    rows = [item for item in outcomes or [] if isinstance(item, ModelSignalOutcome)]
    verified = [item for item in rows if item.point_in_time_verified]
    probability_rows = [item for item in verified if item.probability is not None]
    successes = sum(1 for item in verified if item.success())
    directional_returns = [
        (-item.forward_return if item.polarity == "risk" else item.forward_return)
        for item in verified
        if item.polarity in {"risk", "support"}
    ]
    brier = None
    if probability_rows:
        brier = sum(
            (float(item.probability) - (1.0 if item.success() else 0.0)) ** 2
            for item in probability_rows
        ) / len(probability_rows)
    blockers = []
    if len(verified) < max(1, int(minimum_sample_count or 1)):
        blockers.append("minimum-outcome-sample-count-not-met")
    if len(probability_rows) < len(verified):
        blockers.append("probability-calibration-unavailable")
    if brier is None or brier > 0.25:
        blockers.append("brier-score-gate-not-met")
    if not directional_returns or sum(directional_returns) / len(directional_returns) <= 0:
        blockers.append("economic-utility-gate-not-met")
    return {
        "version": MODEL_SIGNAL_EVALUATION_VERSION,
        "status": "promotion-ready" if not blockers else "blocked",
        "sampleCount": len(rows),
        "verifiedSampleCount": len(verified),
        "probabilitySampleCount": len(probability_rows),
        "directionalHitRate": round(successes / max(1, len(verified)), 6),
        "meanDirectionalReturn": round(
            sum(directional_returns) / max(1, len(directional_returns)),
            8,
        ),
        "meanMaximumFavorableExcursion": round(
            sum(item.maximum_favorable_excursion for item in verified) / max(1, len(verified)),
            8,
        ),
        "meanMaximumAdverseExcursion": round(
            sum(item.maximum_adverse_excursion for item in verified) / max(1, len(verified)),
            8,
        ),
        "brierScore": round(brier, 8) if brier is not None else None,
        "promotionBlockers": sorted(set(blockers)),
    }
