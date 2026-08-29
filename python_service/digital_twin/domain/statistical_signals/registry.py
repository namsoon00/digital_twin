"""Governed statistical-model release registry."""

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


DEFAULT_PRICE_SIGNAL_RELEASE_ID = "price-path-statistics-production-v2"
DEFAULT_FLOW_SIGNAL_RELEASE_ID = "flow-statistics-production-v2"
CAPITAL_FLOW_SHADOW_RELEASE_ID = "capital-flow-statistics-shadow-v1"
DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID = "cross-asset-statistics-production-v2"
DEFAULT_VALUATION_SIGNAL_RELEASE_ID = "valuation-statistics-production-v2"
DEFAULT_EVENT_SIGNAL_RELEASE_ID = "event-response-statistics-production-v2"
DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID = "authored-thesis-statistics-production-v2"


SIGNAL_HYPOTHESIS_FAMILY_BY_TYPE = {
    "price-trend-continuation-support": "trend-continuation",
    "price-trend-break-risk": "trend-break",
    "price-downside-acceleration-risk": "trend-break",
    "price-recovery-support": "mean-reversion",
    "flow-accumulation-support": "flow-accumulation",
    "flow-distribution-risk": "flow-distribution",
    "flow-price-divergence-risk": "flow-distribution",
    "cross-asset-residual-support": "cross-asset-support",
    "cross-asset-residual-risk": "cross-asset-risk",
    "regime-transition-risk": "cross-asset-risk",
    "valuation-relative-opportunity": "fundamental-rerating",
    "valuation-relative-stretch-risk": "fundamental-deterioration",
    "event-abnormal-return-support": "event-support",
    "event-abnormal-return-risk": "event-risk",
    "event-response-persistence": "event-support",
}


def signal_hypothesis_family(signal_type: object) -> str:
    return SIGNAL_HYPOTHESIS_FAMILY_BY_TYPE.get(str(signal_type or "").strip().lower(), "")


def validate_signal_hypothesis_mapping(releases=None) -> Tuple[str, ...]:
    rows = tuple(releases or default_statistical_model_registry())
    missing = sorted({
        signal_type
        for release in rows
        for signal_type in release.signal_types
        if signal_type not in SIGNAL_HYPOTHESIS_FAMILY_BY_TYPE
    })
    return tuple(missing)


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
            status="production",
            validation_status="validated-deterministic",
            decision_eligibility="conditional",
            minimum_samples=5,
            minimum_coverage_ratio=0.75,
            scorer_version="price-path-score-v1",
            description=(
                "시점 고정 가격 경로를 표준화한 재현 가능 점수입니다. 확률 예측이 아닌 "
                "조건부 점수이며 TypeDB가 가설 후보를 만들 때만 사용합니다."
            ),
        ),
        StatisticalModelRelease(
            release_id=DEFAULT_FLOW_SIGNAL_RELEASE_ID,
            model_family="investor-flow-statistics",
            signal_types=(
                "flow-accumulation-support",
                "flow-distribution-risk",
                "flow-price-divergence-risk",
            ),
            status="production",
            validation_status="validated-deterministic",
            decision_eligibility="reference-only",
            minimum_samples=20,
            minimum_coverage_ratio=0.75,
            scorer_version="flow-score-v1",
            description="혼합 시계열의 결손값 오염을 차단하는 동안 참고 전용으로 유지하는 기존 수급 신호입니다.",
        ),
        StatisticalModelRelease(
            release_id=CAPITAL_FLOW_SHADOW_RELEASE_ID,
            model_family="capital-flow-statistics",
            signal_types=(
                "flow-accumulation-support",
                "flow-distribution-risk",
                "flow-price-divergence-risk",
            ),
            status="shadow",
            validation_status="historical-replay-required",
            decision_eligibility="reference-only",
            minimum_samples=20,
            minimum_coverage_ratio=0.75,
            scorer_version="capital-flow-score-v1",
            description="분리 저장된 확정·추정 수급과 가격 경로를 검증하는 shadow 자금 흐름 신호입니다.",
        ),
        StatisticalModelRelease(
            release_id=DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
            model_family="cross-asset-statistics",
            signal_types=(
                "cross-asset-residual-support",
                "cross-asset-residual-risk",
                "regime-transition-risk",
            ),
            status="production",
            validation_status="validated-deterministic",
            decision_eligibility="conditional",
            minimum_samples=1,
            minimum_coverage_ratio=0.75,
            scorer_version="cross-asset-hypothesis-contract-v2",
            description="시점 고정 시장·금리·환율·크립토 사실을 규칙별 가설 계약으로 검증하는 조건부 신호입니다.",
        ),
        StatisticalModelRelease(
            release_id=DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
            model_family="valuation-factor-statistics",
            signal_types=(
                "valuation-relative-opportunity",
                "valuation-relative-stretch-risk",
            ),
            status="production",
            validation_status="validated-deterministic",
            decision_eligibility="conditional",
            minimum_samples=1,
            minimum_coverage_ratio=0.75,
            scorer_version="valuation-hypothesis-contract-v2",
            description="시점 재현 재무·가치·가격 사실을 규칙별 가설 계약으로 검증하는 조건부 신호입니다.",
        ),
        StatisticalModelRelease(
            release_id=DEFAULT_EVENT_SIGNAL_RELEASE_ID,
            model_family="event-response-statistics",
            signal_types=(
                "event-abnormal-return-support",
                "event-abnormal-return-risk",
                "event-response-persistence",
            ),
            status="production",
            validation_status="validated-deterministic",
            decision_eligibility="conditional",
            minimum_samples=1,
            minimum_coverage_ratio=0.75,
            scorer_version="event-response-hypothesis-contract-v2",
            description="검증된 뉴스·공시·사건과 가격 반응을 규칙별 가설 계약으로 평가하는 조건부 신호입니다.",
        ),
        StatisticalModelRelease(
            release_id=DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
            model_family="authored-thesis-statistics",
            signal_types=(
                "price-trend-continuation-support",
                "price-trend-break-risk",
                "cross-asset-residual-support",
                "cross-asset-residual-risk",
                "flow-accumulation-support",
                "flow-distribution-risk",
            ),
            status="production",
            validation_status="validated-deterministic",
            decision_eligibility="conditional",
            minimum_samples=1,
            minimum_coverage_ratio=0.75,
            scorer_version="authored-thesis-hypothesis-contract-v2",
            description="프로젝트 고유 투자 논리를 규칙별로 식별하고 재현 가능한 사실 계약으로 검증합니다.",
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
        "version": "statistical-model-registry-v2",
        "releaseCount": len(rows),
        "releases": [item.to_dict() for item in rows],
    }
