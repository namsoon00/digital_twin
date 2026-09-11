"""Portfolio-owned configuration contracts; persisted field meanings are unchanged."""

from typing import Dict


DEFAULT_INVESTMENT_STRATEGY_PROFILE = "balanced"


INVESTMENT_STRATEGY_PROFILES = {
    "capitalPreservation": {
        "label": "안정형",
        "description": "손실 제한과 현금 여력을 우선하고, 새 진입은 강한 확인 뒤에만 검토합니다.",
        "riskTolerance": "low",
        "timeHorizon": "mid",
        "lossTolerancePct": -5,
        "profitProtectionPct": 7,
        "maxPositionWeightPct": 15,
        "maxSectorWeightPct": 30,
        "fxExposureReviewPct": 8,
        "minCashWeightPct": 15,
        "addBuyPolicy": "blocked_until_recovery",
        "addBuyWatchSignalMin": 4,
        "addBuyReviewSignalMin": 6,
        "allowLossAddBuyReview": False,
        "defaultHoldingRole": "core",
        "watchlistActionPolicy": "entry_after_confirmation",
        "holdingActionPolicy": "protect_capital_first",
        "promptInstruction": "손실 제한과 비중 축소 기준을 우선 검토하고, 추가매수는 주요 평균 가격 회복과 거래 증가가 같이 확인될 때만 제안한다.",
    },
    "balanced": {
        "label": "균형형",
        "description": "손실 관리와 수익 유지의 균형을 잡고, 보유와 관심종목의 행동 범위를 명확히 나눕니다.",
        "riskTolerance": "medium",
        "timeHorizon": "mid",
        "lossTolerancePct": -8,
        "profitProtectionPct": 12,
        "maxPositionWeightPct": 25,
        "maxSectorWeightPct": 45,
        "fxExposureReviewPct": 12,
        "minCashWeightPct": 10,
        "addBuyPolicy": "watch_after_flow_defense",
        "addBuyWatchSignalMin": 3,
        "addBuyReviewSignalMin": 5,
        "allowLossAddBuyReview": True,
        "defaultHoldingRole": "coreSatellite",
        "watchlistActionPolicy": "small_entry_after_confirmation",
        "holdingActionPolicy": "risk_adjusted_hold_trim",
        "promptInstruction": "손익률, 5/20/60일 평균 가격, 수급, 뉴스·공시, 금리·환율을 함께 보고 보유·분할축소·소액 진입을 균형 있게 제안한다.",
    },
    "growth": {
        "label": "성장형",
        "description": "추세와 성장 근거가 유지되면 변동성을 더 허용하되, 손실 확대 구간은 분할 대응합니다.",
        "riskTolerance": "high",
        "timeHorizon": "long",
        "lossTolerancePct": -12,
        "profitProtectionPct": 18,
        "maxPositionWeightPct": 35,
        "maxSectorWeightPct": 55,
        "fxExposureReviewPct": 18,
        "minCashWeightPct": 5,
        "addBuyPolicy": "review_after_recovery",
        "addBuyWatchSignalMin": 2,
        "addBuyReviewSignalMin": 4,
        "allowLossAddBuyReview": True,
        "defaultHoldingRole": "growthCore",
        "watchlistActionPolicy": "staged_entry",
        "holdingActionPolicy": "let_winners_run_with_trim_guard",
        "promptInstruction": "성장 근거와 추세 유지 여부를 더 크게 보되, 평균 가격 이탈·뉴스 악화·수급 약화가 겹치면 분할축소 기준을 제안한다. 손실 구간의 외국인·기관 동반 순매수는 회복 확인 후 조건부 분할 추가매수로만 해석한다.",
    },
    "aggressive": {
        "label": "공격형",
        "description": "기회 포착을 더 중시하지만, 집중도와 급락 리스크는 별도 경고로 강하게 표시합니다.",
        "riskTolerance": "very_high",
        "timeHorizon": "mixed",
        "lossTolerancePct": -15,
        "profitProtectionPct": 25,
        "maxPositionWeightPct": 45,
        "maxSectorWeightPct": 65,
        "fxExposureReviewPct": 25,
        "minCashWeightPct": 3,
        "addBuyPolicy": "review_with_guardrails",
        "addBuyWatchSignalMin": 1,
        "addBuyReviewSignalMin": 3,
        "allowLossAddBuyReview": True,
        "defaultHoldingRole": "highConviction",
        "watchlistActionPolicy": "staged_entry_allowed",
        "holdingActionPolicy": "momentum_follow_with_risk_stop",
        "promptInstruction": "강한 추세·수급·뉴스가 동시에 맞으면 진입 후보를 적극 제안하되, 집중도 과다와 급락 신호는 즉시 축소 조건으로 제시한다. 손실 구간 추가매수도 수급, 가격 회복, 비중 한도를 통과한 소액 분할 검토로 제한한다.",
    },
}


def normalize_investment_strategy_profile(value: object) -> str:
    text = str(value or "").strip()
    aliases = {
        "안정형": "capitalPreservation",
        "안정": "capitalPreservation",
        "capital_preservation": "capitalPreservation",
        "capital-preservation": "capitalPreservation",
        "conservative": "capitalPreservation",
        "균형형": "balanced",
        "균형": "balanced",
        "balance": "balanced",
        "성장형": "growth",
        "성장": "growth",
        "공격형": "aggressive",
        "공격": "aggressive",
    }
    normalized = aliases.get(text, text)
    return (
        normalized
        if normalized in INVESTMENT_STRATEGY_PROFILES
        else DEFAULT_INVESTMENT_STRATEGY_PROFILE
    )


def investment_strategy_profile(value: object = None) -> Dict[str, object]:
    normalized = normalize_investment_strategy_profile(value)
    profile = dict(INVESTMENT_STRATEGY_PROFILES[normalized])
    profile["profile"] = normalized
    profile["ontologyBox"] = "ABox"
    profile["tboxClass"] = "InvestmentStrategyProfile"
    return profile
