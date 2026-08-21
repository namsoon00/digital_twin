"""Governed statistical-model release registry.

The initial release emits replay-required reference scores. It intentionally
does not claim calibrated probabilities or authorize an investment action.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


DEFAULT_PRICE_SIGNAL_RELEASE_ID = "price-path-statistics-shadow-v1"


@dataclass(frozen=True)
class StatisticalModelRelease:
    release_id: str
    model_family: str
    signal_types: Tuple[str, ...]
    status: str
    validation_status: str
    decision_eligibility: str
    minimum_samples: int
    minimum_coverage_ratio: float
    scorer_version: str
    description: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "releaseId": self.release_id,
            "modelFamily": self.model_family,
            "signalTypes": list(self.signal_types),
            "status": self.status,
            "validationStatus": self.validation_status,
            "decisionEligibility": self.decision_eligibility,
            "minimumSamples": self.minimum_samples,
            "minimumCoverageRatio": self.minimum_coverage_ratio,
            "scorerVersion": self.scorer_version,
            "description": self.description,
        }


def default_statistical_model_registry() -> Tuple[StatisticalModelRelease, ...]:
    return (
        StatisticalModelRelease(
            release_id=DEFAULT_PRICE_SIGNAL_RELEASE_ID,
            model_family="price-path-statistics",
            signal_types=(
                "price-trend-continuation-support",
                "price-trend-break-risk",
                "price-downside-acceleration-risk",
                "price-recovery-support",
            ),
            status="shadow",
            validation_status="replay-required",
            decision_eligibility="reference-only",
            minimum_samples=5,
            minimum_coverage_ratio=0.75,
            scorer_version="price-path-score-v1",
            description=(
                "가격 경로를 표준화한 재현 가능 점수입니다. 역사적 재생과 확률 교정 전에는 "
                "TypeDB 행동 후보를 만들 수 없습니다."
            ),
        ),
    )


def model_release(release_id: object = "") -> StatisticalModelRelease:
    requested = str(release_id or DEFAULT_PRICE_SIGNAL_RELEASE_ID).strip()
    for item in default_statistical_model_registry():
        if item.release_id == requested:
            return item
    raise ValueError("Unknown statistical model release: " + requested)


def model_registry_payload(releases: Iterable[StatisticalModelRelease] = None) -> Dict[str, object]:
    rows = tuple(releases or default_statistical_model_registry())
    return {
        "version": "statistical-model-registry-v1",
        "releaseCount": len(rows),
        "releases": [item.to_dict() for item in rows],
    }
