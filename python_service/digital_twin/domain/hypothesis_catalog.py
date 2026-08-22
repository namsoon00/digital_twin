"""Governed predictive-hypothesis families used by the investment brain.

The catalog describes what a TypeDB-authored thesis is trying to predict and
which competing explanation must be considered. It never evaluates market
facts, scores a symbol, or chooses an investment action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, Optional, Tuple


HYPOTHESIS_CATALOG_VERSION = "investment-hypothesis-catalog-v1"


@dataclass(frozen=True)
class PredictiveHypothesisFamilyDefinition:
    family_id: str
    label: str
    theory_family: str
    prediction_target: str
    expected_direction: str
    expected_outcome: str
    default_horizon: str
    competing_family_ids: Tuple[str, ...]
    required_evidence_domains: Tuple[str, ...]
    outcome_metric: str
    falsification_contract: str
    version: str = HYPOTHESIS_CATALOG_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["competing_family_ids"] = list(self.competing_family_ids)
        payload["required_evidence_domains"] = list(self.required_evidence_domains)
        return payload


def _family(
    family_id: str,
    label: str,
    theory: str,
    target: str,
    direction: str,
    outcome: str,
    horizon: str,
    competitors: Iterable[str],
    evidence: Iterable[str],
    metric: str,
    falsification: str,
) -> PredictiveHypothesisFamilyDefinition:
    return PredictiveHypothesisFamilyDefinition(
        family_id=family_id,
        label=label,
        theory_family=theory,
        prediction_target=target,
        expected_direction=direction,
        expected_outcome=outcome,
        default_horizon=horizon,
        competing_family_ids=tuple(competitors),
        required_evidence_domains=tuple(evidence),
        outcome_metric=metric,
        falsification_contract=falsification,
    )


_DEFINITIONS = (
    _family("trend-continuation", "추세 지속", "behavioral-momentum-and-trend", "price-path", "support", "현재 방향의 위험조정 가격 경로가 이어짐", "short-to-medium", ("trend-break",), ("price", "temporal"), "benchmark-adjusted-return", "추세 조건이 만료되거나 반대 방향 전이가 성립"),
    _family("trend-break", "추세 훼손", "behavioral-momentum-and-trend", "price-path", "risk", "현재 추세가 약화되거나 반대 방향으로 전환", "short-to-medium", ("trend-continuation",), ("price", "temporal"), "maximum-adverse-excursion", "회복 관계가 반복 성립하고 훼손 조건이 해소"),
    _family("mean-reversion", "과잉반응 회복", "behavioral-mean-reversion", "price-recovery", "support", "과도한 가격 이탈이 기준 경로로 수렴", "intraday-to-short", ("failed-recovery",), ("price", "volume"), "recovery-return", "회복 가격대를 재이탈하거나 하락 속도가 재가속"),
    _family("failed-recovery", "회복 실패", "behavioral-mean-reversion", "price-recovery", "risk", "반등이 유지되지 못하고 이전 위험 경로로 복귀", "intraday-to-short", ("mean-reversion",), ("price", "volume"), "retest-failure-rate", "회복 가격대와 거래 확인이 다음 관측에서도 유지"),
    _family("flow-accumulation", "수급 매집", "market-microstructure-and-investor-flow", "investor-flow", "support", "독립된 매수 수요가 가격 경로를 지지", "intraday-to-short", ("flow-distribution",), ("flow", "price", "liquidity"), "flow-price-confirmation", "매수 수급이 소멸하거나 가격과 반대 괴리가 확대"),
    _family("flow-distribution", "수급 분산", "market-microstructure-and-investor-flow", "investor-flow", "risk", "매도 수요 또는 가격-수급 괴리가 하방 위험을 확대", "intraday-to-short", ("flow-accumulation",), ("flow", "price", "liquidity"), "flow-price-divergence", "매도 수급이 해소되고 가격 방어가 반복 확인"),
    _family("fundamental-rerating", "펀더멘털 재평가", "fundamental-valuation-and-factors", "fundamental-value-gap", "support", "이익·품질 개선이 가치 괴리 축소로 연결", "medium-to-long", ("fundamental-deterioration",), ("fundamental", "valuation", "price"), "valuation-convergence", "이익 또는 현금흐름 가정이 훼손되거나 가치 괴리가 확대"),
    _family("fundamental-deterioration", "펀더멘털 훼손", "fundamental-valuation-and-factors", "fundamental-value-gap", "risk", "실적·품질 저하가 가치 함정 또는 재평가 실패로 연결", "medium-to-long", ("fundamental-rerating",), ("fundamental", "valuation", "price"), "fundamental-revision", "공식 실적과 전망이 회복되고 가치 수렴이 확인"),
    _family("event-support", "우호 사건 확산", "event-information-diffusion", "event-price-response", "support", "검증된 우호 사건의 비정상 반응이 지속", "event-to-short", ("event-risk",), ("event", "price", "volume"), "event-abnormal-return", "원문이 기각되거나 가격 반응이 빠르게 소멸"),
    _family("event-risk", "위험 사건 확산", "event-information-diffusion", "event-price-response", "risk", "검증된 위험 사건의 영향이 가격과 펀더멘털에 지속", "event-to-short", ("event-support",), ("event", "price", "volume"), "event-abnormal-return", "후속 확인에서 영향이 제한되고 가격이 충격을 흡수"),
    _family("cross-asset-support", "연관자산 우호 전이", "cross-asset-and-regime-transmission", "cross-asset-residual", "support", "연관 시장 변화가 종목의 초과 반응을 지지", "short-to-medium", ("cross-asset-risk",), ("cross-asset", "price"), "cross-asset-residual-return", "민감도 관계가 해소되거나 종목 반응이 분리"),
    _family("cross-asset-risk", "연관자산 위험 전이", "cross-asset-and-regime-transmission", "cross-asset-residual", "risk", "거시·연관자산 충격이 종목 위험으로 전이", "short-to-medium", ("cross-asset-support",), ("cross-asset", "price"), "cross-asset-residual-risk", "충격이 완화되거나 종목이 독립적인 상대 강도를 확인"),
    _family("thesis-support", "투자 논리 유지", "authored-investment-thesis", "investment-thesis", "support", "검증된 투자 논리의 핵심 전제가 유지", "multi-horizon", ("thesis-risk",), ("price", "fundamental"), "thesis-outcome-utility", "핵심 전제 또는 인과 경로가 관측으로 반증"),
    _family("thesis-risk", "투자 논리 훼손", "authored-investment-thesis", "investment-thesis", "risk", "투자 논리의 핵심 전제가 약화 또는 반증", "multi-horizon", ("thesis-support",), ("price", "fundamental"), "thesis-outcome-utility", "핵심 전제가 공식 자료와 가격 반응으로 복원"),
)

HYPOTHESIS_FAMILY_CATALOG = {item.family_id: item for item in _DEFINITIONS}


def hypothesis_family_definition(family_id: object) -> Optional[PredictiveHypothesisFamilyDefinition]:
    return HYPOTHESIS_FAMILY_CATALOG.get(str(family_id or "").strip())


def hypothesis_catalog_payload() -> Dict[str, object]:
    return {
        "version": HYPOTHESIS_CATALOG_VERSION,
        "familyCount": len(HYPOTHESIS_FAMILY_CATALOG),
        "families": [HYPOTHESIS_FAMILY_CATALOG[key].to_dict() for key in sorted(HYPOTHESIS_FAMILY_CATALOG)],
    }


def validate_hypothesis_catalog() -> Tuple[str, ...]:
    issues = []
    for family_id, item in HYPOTHESIS_FAMILY_CATALOG.items():
        if not item.prediction_target or not item.expected_outcome or not item.outcome_metric:
            issues.append(family_id + ": incomplete prediction contract")
        if not item.competing_family_ids:
            issues.append(family_id + ": missing competing family")
        for competitor_id in item.competing_family_ids:
            competitor = HYPOTHESIS_FAMILY_CATALOG.get(competitor_id)
            if competitor is None:
                issues.append(family_id + ": unknown competitor " + competitor_id)
            elif family_id not in competitor.competing_family_ids:
                issues.append(family_id + ": non-reciprocal competitor " + competitor_id)
    return tuple(sorted(set(issues)))
