from datetime import datetime, timezone
from statistics import median
from typing import Dict, Iterable, List, Optional

from .alert_formatting import compact_number, price_money
from .investment_research import research_evidence_from_external_signals, research_evidence_from_facts
from .investor_flow_psychology import investor_flow_psychology, investor_flow_values_reliable
from .macro_context import macro_context_facts
from .market_data import clamp, number
from . import news_analysis as news_domain
from .accounts import investment_strategy_profile
from .instrument_profiles import instrument_profile_for_position
from .ontology_relation_contracts import BTC_SENSITIVE_SYMBOLS
from .portfolio_ontology_valuation_concepts import (
    external_valuation_rows,
    position_runtime_valuation_rows,
    valuation_values,
)
from .portfolio import PortfolioSummary, Position, expects_kr_microstructure_signals
from .valuation_ai_proposals import ai_valuation_proposal_rows
from .volume_time_adjustment import trading_value_snapshot, volume_pace_snapshot


def _sector_ratio(position: Position, portfolio: PortfolioSummary) -> float:
    for item in portfolio.sectors or []:
        if str(item.get("sector") or item.get("label") or "") == str(position.sector or ""):
            return number(item.get("ratio"))
    return 0.0


def _position_weight(position: Position, portfolio: PortfolioSummary) -> float:
    invested = number(portfolio.invested)
    if invested <= 0:
        return 0.0
    return (number(position.market_value) / invested) * 100.0


def _position_account_weight(position: Position, portfolio: PortfolioSummary) -> float:
    total = number(portfolio.total)
    if total <= 0:
        return 0.0
    return (number(position.market_value) / total) * 100.0


def _investor_flow(position: Position) -> Dict[str, object]:
    psychology = investor_flow_psychology(position)
    if not investor_flow_values_reliable(position):
        return {
            "foreignNetVolume": 0.0,
            "institutionNetVolume": 0.0,
            "individualNetVolume": 0.0,
            "foreignNetAmount": 0.0,
            "institutionNetAmount": 0.0,
            "individualNetAmount": 0.0,
            "smartMoneyNetVolume": 0.0,
            "investorFlowBase": 0.0,
            "investorFlowScore": 0.0,
            "jointSmartMoneyInflow": False,
            "jointSmartMoneyOutflow": False,
            "smartMoneyDirection": "unknown",
            "investorFlowPsychology": "investorFlowUnavailable",
            "investorFlowPsychologyLabel": str(psychology.get("sentimentLabel") or "투자자별 수급 신뢰도 낮음"),
            "investorFlowPsychologyPolarity": "context",
        }
    return {
        "foreignNetVolume": number(psychology.get("foreignNetVolume")),
        "institutionNetVolume": number(psychology.get("institutionNetVolume")),
        "individualNetVolume": number(psychology.get("individualNetVolume")),
        "foreignNetAmount": number(psychology.get("foreignNetAmount")),
        "institutionNetAmount": number(psychology.get("institutionNetAmount")),
        "individualNetAmount": number(psychology.get("individualNetAmount")),
        "smartMoneyNetVolume": number(psychology.get("smartMoneyNetVolume")),
        "investorFlowBase": number(psychology.get("investorFlowBase")),
        "investorFlowScore": number(psychology.get("investorFlowScore")),
        "jointSmartMoneyInflow": bool(psychology.get("jointSmartMoneyInflow")),
        "jointSmartMoneyOutflow": bool(psychology.get("jointSmartMoneyOutflow")),
        "smartMoneyDirection": str(psychology.get("smartMoneyDirection") or "mixed"),
        "investorFlowPsychology": str(psychology.get("field") or "mixedInvestorPsychology"),
        "investorFlowPsychologyLabel": str(psychology.get("sentimentLabel") or "투자자별 수급 혼조"),
        "investorFlowPsychologyPolarity": str(psychology.get("polarity") or "context"),
    }


def _trend_facts(position: Position) -> Dict[str, object]:
    current = number(position.current_price)
    ma5 = number(position.ma5)
    ma20 = number(position.ma20)
    ma60 = number(position.ma60)
    ma5_distance = (((current / ma5) - 1) * 100.0 if current and ma5 else 0.0)
    ma20_distance = number(position.ma20_distance) or (((current / ma20) - 1) * 100.0 if current and ma20 else 0.0)
    ma60_distance = number(position.ma60_distance) or (((current / ma60) - 1) * 100.0 if current and ma60 else 0.0)
    ma20_slope = number(position.ma20_slope)
    ma60_slope = number(position.ma60_slope)
    price_change = number(position.change_rate)
    trend_curve = ma20_slope - ma60_slope
    short_term_breakdown = bool(ma20) and ma20_distance <= -5.0
    medium_term_support = bool(ma60) and ma60_distance >= 0.0
    support_retest = short_term_breakdown and bool(ma60) and ma60_distance >= -1.0
    recovery_attempt = (
        bool(current)
        and (ma20_distance < 0 or number(position.profit_loss_rate) < 0)
        and bool(ma60)
        and ma60_distance >= -1.0
        and (price_change >= 1.0 or ma20_slope >= 0.3 or trend_curve >= 0.5)
    )
    breakdown_acceleration = (
        short_term_breakdown
        and (
            price_change <= -2.0
            or ma20_slope <= -1.0
            or trend_curve <= -1.0
            or (ma60_distance <= -5.0 and ma20_slope < 0 and ma60_slope < 0)
        )
    )
    dynamic_risk = trend_dynamic_risk_score(
        ma20_distance,
        ma60_distance,
        price_change,
        ma20_slope,
        trend_curve,
        support_retest,
        recovery_attempt,
    )
    state = trend_state_label(ma20_distance, ma60_distance, support_retest, recovery_attempt, breakdown_acceleration)
    curve_label = trend_curve_label(trend_curve)
    slope_label = trend_slope_label(ma20_slope, ma60_slope)
    price_momentum_label = direction_label(price_change, "상승", "하락")
    score = clamp(
        ma20_distance * 0.45
        + ma60_distance * 0.25
        + ma20_slope * 3.0
        + ma60_slope * 2.0
        + price_change * 0.4,
        -35.0,
        35.0,
    )
    return {
        "currentPrice": current,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "ma5Distance": ma5_distance,
        "ma20Distance": ma20_distance,
        "ma60Distance": ma60_distance,
        "ma20Slope": ma20_slope,
        "ma60Slope": ma60_slope,
        "priceChangeRate": price_change,
        "priceMomentumLabel": price_momentum_label,
        "trendSlopeLabel": slope_label,
        "trendCurve": trend_curve,
        "trendCurveLabel": curve_label,
        "trendState": state,
        "shortTermBreakdown": short_term_breakdown,
        "mediumTermSupport": medium_term_support,
        "supportRetest": support_retest,
        "recoveryAttempt": recovery_attempt,
        "breakdownAcceleration": breakdown_acceleration,
        "trendDynamicRiskScore": dynamic_risk,
        "trendDynamics": {
            "state": state,
            "priceMomentum": price_momentum_label,
            "priceChangeRate": round(price_change, 2),
            "slope": slope_label,
            "ma20Slope": round(ma20_slope, 2),
            "ma60Slope": round(ma60_slope, 2),
            "curve": curve_label,
            "trendCurve": round(trend_curve, 2),
            "ma5Distance": round(ma5_distance, 2),
            "ma20Distance": round(ma20_distance, 2),
            "ma60Distance": round(ma60_distance, 2),
            "shortTermBreakdown": short_term_breakdown,
            "mediumTermSupport": medium_term_support,
            "supportRetest": support_retest,
            "recoveryAttempt": recovery_attempt,
            "breakdownAcceleration": breakdown_acceleration,
            "dynamicRiskScore": round(dynamic_risk, 1),
        },
        "trendScore": score,
    }


def state_number(state: Dict[str, object], *keys: str) -> float:
    if not isinstance(state, dict):
        return 0.0
    for key in keys:
        if key in state and state.get(key) not in (None, ""):
            return number(state.get(key))
    return 0.0


def _temporal_facts(position: Position, previous_state: Dict[str, object] = None, previous_decision: Dict[str, object] = None) -> Dict[str, object]:
    previous_state = previous_state if isinstance(previous_state, dict) else {}
    previous_decision = previous_decision if isinstance(previous_decision, dict) else {}
    current_price = number(position.current_price)
    previous_price = state_number(previous_state, "currentPrice", "current_price", "price")
    current_pnl = number(position.profit_loss_rate)
    previous_pnl = state_number(previous_state, "profitLossRate", "profit_loss_rate")
    previous_ma20_distance = state_number(previous_state, "ma20Distance", "ma20_distance")
    previous_ma60_distance = state_number(previous_state, "ma60Distance", "ma60_distance")
    current_ma20_distance = number(position.ma20_distance)
    current_ma60_distance = number(position.ma60_distance)
    price_delta = ((current_price / previous_price) - 1) * 100 if current_price and previous_price else 0.0
    pnl_delta = current_pnl - previous_pnl if previous_state else 0.0
    ma20_distance_delta = current_ma20_distance - previous_ma20_distance if previous_state else 0.0
    ma60_distance_delta = current_ma60_distance - previous_ma60_distance if previous_state else 0.0
    previous_rule = str(previous_decision.get("selectedRuleId") or previous_decision.get("selected_rule_id") or "")
    previous_label = str(previous_decision.get("decision") or previous_decision.get("label") or "")
    return {
        "hasPreviousState": bool(previous_state or previous_decision),
        "previousPrice": previous_price,
        "previousProfitLossRate": previous_pnl,
        "previousMa20Distance": previous_ma20_distance,
        "previousMa60Distance": previous_ma60_distance,
        "previousRelationScore": state_number(previous_decision, "exitPressure", "exit_pressure", "score"),
        "previousSelectedRuleId": previous_rule,
        "previousDecisionLabel": previous_label,
        "priceDeltaFromPreviousPct": price_delta,
        "profitLossRateDeltaPct": pnl_delta,
        "ma20DistanceDeltaPct": ma20_distance_delta,
        "ma60DistanceDeltaPct": ma60_distance_delta,
        "reclaimedMa20Previously": previous_ma20_distance >= 0 and current_ma20_distance < 0,
        "lostMa60SincePrevious": previous_ma60_distance >= -1.0 and current_ma60_distance < -1.0,
    }


def _liquidity_facts(position: Position) -> Dict[str, object]:
    market_value = number(position.market_value)
    trading_value = number(position.trading_value)
    volume_ratio = number(position.volume_ratio)
    bid_ask_imbalance = number(position.bid_ask_imbalance)
    quantity = number(position.quantity)
    sellable_quantity = number(position.sellable_quantity)
    position_to_trading_value = (market_value / trading_value) * 100 if market_value and trading_value else 0.0
    exit_days = market_value / max(1.0, trading_value * 0.1) if market_value and trading_value else 0.0
    sellable_blocked = bool(quantity and sellable_quantity <= 0)
    liquidity_risk = clamp(
        position_to_trading_value * 2.0
        + max(0.0, 1.0 - volume_ratio) * 18.0
        + max(0.0, -bid_ask_imbalance) * 0.25
        + (25.0 if sellable_blocked else 0.0),
        0.0,
        100.0,
    )
    return {
        "positionToTradingValuePct": position_to_trading_value,
        "exitDaysAtTenPctADV": exit_days,
        "sellableBlocked": sellable_blocked,
        "liquidityRiskScore": liquidity_risk,
    }


def _external_quality_facts(external_signals: Dict[str, object]) -> Dict[str, object]:
    quality = external_signals.get("quality") if isinstance(external_signals.get("quality"), dict) else {}
    freshness = external_signals.get("freshness") if isinstance(external_signals.get("freshness"), dict) else {}
    statuses = external_signals.get("statuses") if isinstance(external_signals.get("statuses"), list) else []
    error_count = len([item for item in statuses if isinstance(item, dict) and not item.get("ok")])
    return {
        "externalSignalQualityScore": number(quality.get("score")) if quality else 0.0,
        "externalSignalCoverageScore": number(quality.get("coverageScore")) if quality else 0.0,
        "externalSignalSourceHealthScore": number(quality.get("sourceHealthScore")) if quality else 0.0,
        "externalSignalAgeMinutes": number(freshness.get("ageMinutes")) if freshness else 0.0,
        "externalSignalFreshnessStatus": str(freshness.get("status") or ""),
        "externalSignalErrorCount": error_count,
    }


VALUATION_MISSING_INPUT_LABELS = {
    "currentPrice": "현재가",
    "fairValue": "적정가",
    "fairValuePrice": "적정가",
    "expectedEPS": "예상 EPS",
    "targetPER": "목표 PER",
    "annualDividend": "연간 배당",
    "requiredYieldPct": "요구수익률",
}


def _valuation_runtime_context(settings: Optional[Dict[str, object]]) -> Dict[str, object]:
    if isinstance(settings, dict) and isinstance(settings.get("settings"), dict):
        return settings
    return {"settings": settings or {}}


def _valuation_source_metadata(row: Dict[str, object]) -> Dict[str, object]:
    source = str(row.get("source") or "").strip()
    provider = str(row.get("provider") or "").strip()
    source_lower = source.casefold()
    provider_lower = provider.casefold()
    if source_lower == "ai-valuation-proposal":
        return {
            "sourceType": "ai",
            "sourceLabel": "AI 제안",
            "provider": provider or "Orbit Alpha AI",
            "reliabilityLabel": "AI 초안(사용자 검토 전)",
            "reliabilityScore": number(row.get("reliabilityScore")) or 45.0,
        }
    if source_lower == "runtime-settings" or provider_lower == "runtimesettings":
        return {
            "sourceType": "user",
            "sourceLabel": "사용자 입력",
            "provider": provider or "RuntimeSettings",
            "reliabilityLabel": "사용자 가정",
            "reliabilityScore": 55.0,
        }
    if source_lower == "company-overview":
        return {
            "sourceType": "external",
            "sourceLabel": (provider or "Alpha Vantage") + " 기업개요",
            "provider": provider or "Alpha Vantage",
            "reliabilityLabel": "외부 기업개요",
            "reliabilityScore": 70.0,
        }
    if source_lower == "earnings-report":
        return {
            "sourceType": "external",
            "sourceLabel": (provider or "Alpha Vantage") + " 실적",
            "provider": provider or "Alpha Vantage",
            "reliabilityLabel": "외부 실적",
            "reliabilityScore": 65.0,
        }
    if provider:
        return {
            "sourceType": "external",
            "sourceLabel": provider,
            "provider": provider,
            "reliabilityLabel": "외부/혼합 데이터",
            "reliabilityScore": 60.0,
        }
    return {
        "sourceType": "unknown",
        "sourceLabel": "밸류에이션 데이터",
        "provider": "",
        "reliabilityLabel": "출처 미확인",
        "reliabilityScore": 45.0,
    }


def _valuation_price_text(value: object, currency: object = "") -> str:
    amount = number(value)
    if not amount:
        return ""
    return price_money(amount, str(currency or "KRW"))


def _valuation_multiplier_text(value: object) -> str:
    amount = number(value)
    if not amount:
        return ""
    return compact_number(amount) + "배"


def _valuation_substitution(values: Dict[str, object], row: Dict[str, object], currency: object) -> str:
    fair_value = number(values.get("fairValue"))
    expected_eps = number(values.get("expectedEPS"))
    target_per = number(values.get("targetPER"))
    annual_dividend = number(values.get("annualDividend"))
    required_yield = number(values.get("requiredYieldPct"))
    fair_value_low = number(values.get("fairValueLow"))
    fair_value_high = number(values.get("fairValueHigh"))
    if expected_eps and target_per and fair_value:
        return (
            _valuation_price_text(expected_eps, currency)
            + " x "
            + _valuation_multiplier_text(target_per)
            + " = "
            + _valuation_price_text(fair_value, currency)
            + ((" (범위 " + _valuation_price_text(fair_value_low, currency) + "~" + _valuation_price_text(fair_value_high, currency) + ")") if fair_value_low and fair_value_high and fair_value_low != fair_value_high else "")
        )
    if annual_dividend and required_yield and fair_value:
        return (
            "연간 배당 "
            + _valuation_price_text(annual_dividend, currency)
            + " / 요구수익률 "
            + _number_text(required_yield, 2)
            + "% = "
            + _valuation_price_text(fair_value, currency)
        )
    if fair_value:
        source = str(row.get("source") or "").casefold()
        prefix = "입력 적정가" if source == "runtime-settings" else "AI 제안 적정가" if source == "ai-valuation-proposal" else "제공 적정가"
        return prefix + " = " + _valuation_price_text(fair_value, currency)
    if expected_eps and target_per:
        return _valuation_price_text(expected_eps, currency) + " x " + _valuation_multiplier_text(target_per)
    return ""


def _valuation_explanation(values: Dict[str, object], row: Dict[str, object], currency: object) -> str:
    fair_value = number(values.get("fairValue"))
    current_price = number(values.get("currentPrice"))
    expected_eps = number(values.get("expectedEPS"))
    target_per = number(values.get("targetPER"))
    annual_dividend = number(values.get("annualDividend"))
    required_yield = number(values.get("requiredYieldPct"))
    margin = number(values.get("marginOfSafetyPct"))
    fair_value_low = number(values.get("fairValueLow"))
    fair_value_high = number(values.get("fairValueHigh"))
    if expected_eps and target_per and fair_value:
        base = (
            "예상 EPS "
            + _valuation_price_text(expected_eps, currency)
            + "에 목표 PER "
            + _valuation_multiplier_text(target_per)
            + "를 적용해 적정가 "
            + _valuation_price_text(fair_value, currency)
            + "로 계산했습니다."
        )
    elif annual_dividend and required_yield and fair_value:
        base = (
            "연간 배당 "
            + _valuation_price_text(annual_dividend, currency)
            + "을 요구수익률 "
            + _number_text(required_yield, 2)
            + "%로 나눠 적정가 "
            + _valuation_price_text(fair_value, currency)
            + "로 계산했습니다."
        )
        if str(row.get("source") or "").casefold() == "ai-valuation-proposal":
            base += " 이 값은 AI 제안값이라 사용자 검토 전 초안입니다."
    elif fair_value:
        source_name = str(row.get("source") or "").casefold()
        source = "사용자가 입력한" if source_name == "runtime-settings" else "AI가 임시로 제안한" if source_name == "ai-valuation-proposal" else "외부 데이터가 제공한"
        base = source + " 적정가 " + _valuation_price_text(fair_value, currency) + "를 현재가와 비교했습니다."
        if source_name == "ai-valuation-proposal":
            base += " 이 값은 사용자 검토 전 초안입니다."
    else:
        return "적정가 계산에 필요한 입력값이 부족해 현재가가 싼지 비싼지 단정하지 않습니다."
    if current_price and fair_value:
        base += " 현재가 " + _valuation_price_text(current_price, currency) + " 대비 안전마진은 " + _number_text(margin, 1, signed=True) + "%입니다."
    if fair_value_low and fair_value_high and fair_value_low != fair_value_high:
        base += " 보수적·낙관적 가정을 반영한 적정가 범위는 " + _valuation_price_text(fair_value_low, currency) + "~" + _valuation_price_text(fair_value_high, currency) + "입니다."
    if not bool(values.get("valuationDecisionEligible")):
        base += " 이 계산은 신뢰도나 승인 조건이 부족해 매수·매도 추론에는 직접 사용하지 않습니다."
    return base


def _valuation_row_payload(position: Position, row: Dict[str, object]) -> Dict[str, object]:
    currency = position.currency or "KRW"
    values = valuation_values(row, position)
    source = _valuation_source_metadata(row)
    missing_inputs = [
        VALUATION_MISSING_INPUT_LABELS.get(str(item), str(item))
        for item in (values.get("missingInputs") or [])
        if str(item or "").strip()
    ]
    fair_value = number(values.get("fairValue"))
    has_formula = bool(str(values.get("formula") or row.get("formula") or "").strip())
    complete_score = (
        (30.0 if fair_value else 0.0)
        + (10.0 if not missing_inputs else 0.0)
        + (5.0 if has_formula else 0.0)
        + (10.0 if bool(row.get("aiGenerated")) and str(values.get("valuationMethod") or "") != "ai-current-price-anchor" else 0.0)
    )
    source_score = 12.0 if source.get("sourceType") == "user" else 6.0 if source.get("sourceType") == "external" else 4.0 if source.get("sourceType") == "ai" else 0.0
    reliability_score = number(values.get("valuationReliabilityScore")) or number(source.get("reliabilityScore"))
    decision_eligible = bool(values.get("valuationDecisionEligible"))
    formula = str(values.get("formula") or row.get("formula") or "").strip()
    payload = {
        "assumptionKey": str(row.get("assumptionKey") or row.get("symbol") or position.symbol or "").strip(),
        "label": str(row.get("label") or row.get("name") or (position.name or position.symbol or "") + " 밸류에이션").strip(),
        "source": str(row.get("source") or ""),
        "provider": source.get("provider") or str(row.get("provider") or ""),
        "sourceType": source.get("sourceType"),
        "sourceLabel": source.get("sourceLabel"),
        "reliabilityLabel": values.get("valuationConfidenceLabel") or source.get("reliabilityLabel"),
        "reliabilityScore": reliability_score,
        "formula": formula,
        "substitution": _valuation_substitution(values, row, currency),
        "explanation": _valuation_explanation(values, row, currency),
        "missingInputs": missing_inputs,
        "dataStatus": "available" if fair_value and not missing_inputs else "partial" if fair_value or values.get("expectedEPS") or values.get("targetPER") or bool(row.get("aiGenerated")) else "missing",
        "selectionScore": complete_score + source_score + reliability_score * 0.25 + (40.0 if decision_eligible else 0.0),
        "hasUserInput": source.get("sourceType") == "user",
        "hasExternalInput": source.get("sourceType") == "external",
        "hasAiProposal": source.get("sourceType") == "ai",
        "approvalStatus": str(row.get("approvalStatus") or "").strip(),
        "activeStatus": str(row.get("activeStatus") or "").strip(),
        "reviewStatus": str(row.get("reviewStatus") or row.get("approvalStatus") or "").strip(),
        "autoApplied": bool(row.get("autoApplied")),
        "requiresUserApproval": bool(row.get("requiresUserApproval")),
        "aiGenerated": bool(row.get("aiGenerated")),
        "sourceReason": str(row.get("sourceReason") or "").strip(),
        "perValuationStatus": str(values.get("perValuationStatus") or row.get("perValuationStatus") or "").strip(),
        "perValuationReason": str(values.get("perValuationReason") or row.get("perValuationReason") or "").strip(),
        "preferredValuationMetric": str(values.get("preferredValuationMetric") or row.get("preferredValuationMetric") or "").strip(),
        "fundamentalDataSourcePriority": str(values.get("fundamentalDataSourcePriority") or row.get("fundamentalDataSourcePriority") or "").strip(),
        **values,
    }
    return payload


def _primary_valuation_row(rows: List[Dict[str, object]]) -> Dict[str, object]:
    if not rows:
        return {}
    return sorted(rows, key=lambda item: number(item.get("selectionScore")), reverse=True)[0]


def _valuation_facts(position: Position, external_signals: Dict[str, object], settings: Optional[Dict[str, object]]) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    if not symbol:
        return {}
    raw_rows = position_runtime_valuation_rows(_valuation_runtime_context(settings), symbol)
    raw_rows.extend(external_valuation_rows(external_signals or {}, symbol))
    proposal_settings = settings.get("settings") if isinstance(settings, dict) and isinstance(settings.get("settings"), dict) else settings
    raw_rows.extend(ai_valuation_proposal_rows(position, external_signals or {}, proposal_settings or {}))
    rows = [_valuation_row_payload(position, row) for row in raw_rows]
    if not rows:
        return {
            "valuationRows": [],
            "valuationDataStatus": "missing",
            "valuationSourceType": "missing",
            "valuationSourceLabel": "사용자 입력 없음 · 외부 밸류에이션 데이터 없음",
            "valuationReliabilityLabel": "판단 보류",
            "valuationReliabilityScore": 0.0,
            "valuationFormula": "",
            "valuationSubstitution": "",
            "valuationExplanation": "적정가 공식이나 적정가 입력값이 없어 현재가가 싼지 비싼지 계산하지 않았습니다.",
            "valuationCurrentPrice": number(position.current_price),
            "valuationFairValue": 0.0,
            "valuationFairValuePrice": 0.0,
            "valuationFairValueLow": 0.0,
            "valuationFairValueHigh": 0.0,
            "valuationExpectedEPS": 0.0,
            "valuationTargetPER": 0.0,
            "valuationMarginOfSafetyPct": 0.0,
            "valuationConservativeMarginOfSafetyPct": 0.0,
            "valuationOptimisticMarginOfSafetyPct": 0.0,
            "valuationMinimumMarginOfSafetyPct": 0.0,
            "valuationMissingInputs": ["적정가", "예상 EPS", "목표 PER"],
            "valuationHasUserInput": False,
            "valuationHasExternalInput": False,
            "valuationHasAiProposal": False,
            "valuationApprovalStatus": "",
            "valuationRequiresUserApproval": False,
            "valuationIsAiGenerated": False,
            "valuationSourceReason": "",
            "valuationPerStatus": "missing",
            "valuationPerReason": "PER/EPS와 적정가 입력이 없어 PER 기준 적정가를 계산하지 않았습니다.",
            "valuationPreferredMetric": "적정가 입력 또는 외부 PER/EPS",
            "valuationFundamentalDataSourcePriority": "사용자 입력 > KIS/Alpha Vantage/yfinance PER/EPS",
            "valuationAnnualDividend": 0.0,
            "valuationRequiredYieldPct": 0.0,
            "valuationCouponPct": 0.0,
            "valuationParValue": 0.0,
            "valuationMethod": "",
            "valuationActiveStatus": "",
            "valuationReviewStatus": "",
            "valuationAutoApplied": False,
            "valuationDecisionEligible": False,
            "valuationFreshnessStatus": "unknown",
            "valuationAsOf": "",
            "valuationInputCoveragePct": 0.0,
            "valuationConfidenceLabel": "판단 보류",
            "valuationModelCount": 0,
            "valuationConsensusPrice": 0.0,
            "valuationDisagreementPct": 0.0,
            "valuationConsensusStatus": "missing",
        }
    primary = _primary_valuation_row(rows)
    missing_inputs = list(primary.get("missingInputs") or [])
    fair_value = number(primary.get("fairValue"))
    current_price = number(primary.get("currentPrice"))
    eligible_fair_values = [
        number(item.get("fairValue"))
        for item in rows
        if bool(item.get("valuationDecisionEligible")) and number(item.get("fairValue"))
    ]
    consensus_price = median(eligible_fair_values) if eligible_fair_values else 0.0
    disagreement_pct = (
        (max(eligible_fair_values) - min(eligible_fair_values)) / consensus_price * 100.0
        if len(eligible_fair_values) >= 2 and consensus_price
        else 0.0
    )
    consensus_status = "conflict" if disagreement_pct > 35.0 else "agreement" if len(eligible_fair_values) >= 2 else "single-model"
    return {
        "valuationRows": rows,
        "primaryValuation": primary,
        "valuationDataStatus": primary.get("dataStatus") or ("available" if fair_value and current_price else "partial"),
        "valuationSourceType": primary.get("sourceType"),
        "valuationSourceLabel": primary.get("sourceLabel"),
        "valuationProvider": primary.get("provider"),
        "valuationReliabilityLabel": primary.get("reliabilityLabel"),
        "valuationReliabilityScore": number(primary.get("reliabilityScore")),
        "valuationFormula": primary.get("formula"),
        "valuationSubstitution": primary.get("substitution"),
        "valuationExplanation": primary.get("explanation"),
        "valuationCurrentPrice": current_price,
        "valuationFairValue": fair_value,
        "valuationFairValuePrice": fair_value,
        "valuationFairValueLow": number(primary.get("fairValueLow")),
        "valuationFairValueHigh": number(primary.get("fairValueHigh")),
        "valuationExpectedEPS": number(primary.get("expectedEPS")),
        "valuationTargetPER": number(primary.get("targetPER")),
        "valuationAnnualDividend": number(primary.get("annualDividend")),
        "valuationRequiredYieldPct": number(primary.get("requiredYieldPct")),
        "valuationCouponPct": number(primary.get("couponPct")),
        "valuationParValue": number(primary.get("parValue")),
        "valuationMarginOfSafetyPct": number(primary.get("marginOfSafetyPct")),
        "valuationConservativeMarginOfSafetyPct": number(primary.get("conservativeMarginOfSafetyPct")),
        "valuationOptimisticMarginOfSafetyPct": number(primary.get("optimisticMarginOfSafetyPct")),
        "valuationExpensivePremiumPct": number(primary.get("expensivePremiumPct")),
        "valuationMinimumMarginOfSafetyPct": number(primary.get("minimumMarginOfSafetyPct")),
        "valuationMethod": primary.get("valuationMethod"),
        "valuationActiveStatus": primary.get("activeStatus"),
        "valuationReviewStatus": primary.get("reviewStatus") or primary.get("approvalStatus"),
        "valuationAutoApplied": bool(primary.get("autoApplied")),
        "valuationDecisionEligible": bool(primary.get("valuationDecisionEligible")) and consensus_status != "conflict",
        "valuationModelCount": len(eligible_fair_values),
        "valuationConsensusPrice": round(consensus_price, 4) if consensus_price else 0.0,
        "valuationDisagreementPct": round(disagreement_pct, 2),
        "valuationConsensusStatus": consensus_status,
        "valuationFreshnessStatus": primary.get("valuationFreshnessStatus"),
        "valuationAsOf": primary.get("valuationAsOf"),
        "valuationInputCoveragePct": number(primary.get("valuationInputCoveragePct")),
        "valuationConfidenceLabel": primary.get("valuationConfidenceLabel"),
        "valuationEpsPeriod": primary.get("epsPeriod"),
        "valuationMultiplePeriod": primary.get("multiplePeriod"),
        "valuationMissingInputs": missing_inputs,
        "valuationHasUserInput": any(bool(item.get("hasUserInput")) for item in rows),
        "valuationHasExternalInput": any(bool(item.get("hasExternalInput")) for item in rows),
        "valuationHasAiProposal": any(bool(item.get("hasAiProposal")) for item in rows),
        "valuationApprovalStatus": primary.get("approvalStatus"),
        "valuationRequiresUserApproval": bool(primary.get("requiresUserApproval")),
        "valuationIsAiGenerated": bool(primary.get("aiGenerated")),
        "valuationSourceReason": primary.get("sourceReason"),
        "valuationPerStatus": primary.get("perValuationStatus"),
        "valuationPerReason": primary.get("perValuationReason"),
        "valuationPreferredMetric": primary.get("preferredValuationMetric"),
        "valuationFundamentalDataSourcePriority": primary.get("fundamentalDataSourcePriority"),
    }


def _has_numeric_fact(value: object) -> bool:
    if value in (None, ""):
        return False
    try:
        float(str(value).replace(",", "").strip())
        return True
    except (TypeError, ValueError):
        return False


def _number_text(value: object, decimals: int = 2, signed: bool = False) -> str:
    amount = number(value)
    text = (("%." + str(decimals) + "f") % amount).rstrip("0").rstrip(".")
    if "." not in text and abs(amount) >= 1000:
        text = format(int(round(amount)), ",")
    if signed and amount > 0:
        return "+" + text
    return text


def _bp_text(value: object) -> str:
    return _number_text(value, 0, signed=True) + "bp"


def _rate_context_line_from_facts(facts: Dict[str, object]) -> str:
    parts: List[str] = []
    if _has_numeric_fact(facts.get("macroDgs10")) and number(facts.get("macroDgs10")) > 0:
        parts.append("미국10년 " + _number_text(facts.get("macroDgs10"), 2) + "%")
    if _has_numeric_fact(facts.get("macroDgs2")) and number(facts.get("macroDgs2")) > 0:
        parts.append("미국2년 " + _number_text(facts.get("macroDgs2"), 2) + "%")
    if _has_numeric_fact(facts.get("macroDff")) and number(facts.get("macroDff")) > 0:
        parts.append("연방기금 " + _number_text(facts.get("macroDff"), 2) + "%")
    if _has_numeric_fact(facts.get("macroYieldSpread10y2y")):
        parts.append("10Y-2Y " + _number_text(facts.get("macroYieldSpread10y2y"), 2, signed=True) + "%p")
    if facts.get("hasInterestRateDeltaSignal"):
        delta_parts = []
        if facts.get("hasMacroDgs10Delta"):
            delta_parts.append("10년 " + _bp_text(facts.get("macroDgs10DeltaBp")))
        if facts.get("hasMacroDgs2Delta"):
            delta_parts.append("2년 " + _bp_text(facts.get("macroDgs2DeltaBp")))
        if facts.get("hasMacroYieldSpreadDelta"):
            delta_parts.append("스프레드 " + _bp_text(facts.get("macroYieldSpreadDeltaBp")))
        if delta_parts:
            parts.append("변화 " + " / ".join(delta_parts))
    if not parts:
        return ""
    regime = str(facts.get("rateRegime") or "")
    curve = str(facts.get("yieldCurveRegime") or "")
    regime_labels = {
        "high_rate": "고금리",
        "low_rate": "저금리",
        "neutral_rate": "중립 금리",
    }
    curve_labels = {
        "inverted_curve": "수익률곡선 역전",
        "positive_curve": "수익률곡선 정상",
        "flat_or_unknown_curve": "수익률곡선 평탄/미확인",
    }
    labels = [label for label in [regime_labels.get(regime, ""), curve_labels.get(curve, "")] if label]
    if labels:
        parts.append("레짐 " + " / ".join(labels))
    return "금리: " + " · ".join(parts)


def _fx_context_line_from_facts(facts: Dict[str, object]) -> str:
    pair = str(facts.get("fxRatePair") or "").upper().replace("/", "").strip()
    rate_value = facts.get("fxRateToKrw")
    if not _has_numeric_fact(rate_value):
        rate_value = facts.get("usdKrwRate")
    rate_amount = number(rate_value)
    if not pair and _has_numeric_fact(facts.get("usdKrwRate")) and number(facts.get("usdKrwRate")) > 0:
        pair = "USDKRW"
        rate_value = facts.get("usdKrwRate")
        rate_amount = number(rate_value)
    if not pair or len(pair) < 6 or rate_amount <= 0:
        return ""
    base = pair[:3]
    quote = pair[3:6]
    if base == quote:
        return ""
    parts = [base + "/" + quote, "1 " + base + " = " + _number_text(rate_value, 2) + " " + quote]
    source_type = str(facts.get("fxSourceType") or "").strip()
    provider = str(facts.get("fxProvider") or "").strip()
    source_labels = {
        "market_realtime": "실시간 API",
        "market_daily": "일일 API 갱신",
        "broker_applied_valuation": "계좌 적용 환율",
        "fallback_setting": "설정값 기준",
    }
    source_label = source_labels.get(source_type, "")
    if source_label:
        parts.append(source_label)
    elif provider:
        parts.append("출처 " + provider)
    if facts.get("hasFxDeltaSignal"):
        delta_krw = facts.get("usdKrwDeltaKrw")
        delta_pct = facts.get("usdKrwDeltaPct")
        delta_parts = []
        if _has_numeric_fact(delta_krw):
            delta_parts.append(_number_text(delta_krw, 2, signed=True) + " " + quote)
        if _has_numeric_fact(delta_pct):
            delta_parts.append(_number_text(delta_pct, 2, signed=True) + "%")
        if delta_parts:
            parts.append("변화 " + " / ".join(delta_parts))
    exposure = facts.get("fxExposureRatio")
    if _has_numeric_fact(exposure) and number(exposure) > 0:
        parts.append("노출 " + _number_text(exposure, 1) + "%")
    regime_labels = {
        "krw_weakening": "원화 약세",
        "krw_strengthening": "원화 강세",
        "fx_observed": "환율 관찰",
        "base_currency_or_unknown": "기준통화/미확인",
    }
    regime = regime_labels.get(str(facts.get("fxRegime") or ""), "")
    if regime:
        parts.append(regime)
    return "환율: " + " · ".join(parts)


def _btc_market(external_signals: Dict[str, object]) -> Dict[str, object]:
    markets = external_signals.get("cryptoMarkets") if isinstance(external_signals, dict) else {}
    if not isinstance(markets, dict):
        return {}
    for coin_id, item in markets.items():
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or coin_id or "").upper()
        name = str(item.get("name") or "").lower()
        if symbol == "BTC" or str(coin_id or "").lower() == "bitcoin" or name == "bitcoin":
            return dict(item)
    return {}


def _investment_strategy_facts(
    external_signals: Optional[Dict[str, object]] = None,
    account_context: Optional[Dict[str, object]] = None,
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    external_signals = external_signals or {}
    settings = settings or {}
    context = account_context if isinstance(account_context, dict) else {}
    if not context and isinstance(external_signals.get("accountContext"), dict):
        context = external_signals.get("accountContext") or {}
    profile_payload = context.get("investmentStrategy") if isinstance(context.get("investmentStrategy"), dict) else {}
    profile_key = (
        profile_payload.get("profile")
        or context.get("investmentStrategyProfile")
        or settings.get("investmentStrategyProfile")
    )
    profile = investment_strategy_profile(profile_key)
    return {
        "investmentStrategyProfile": profile.get("profile"),
        "investmentStrategyProfileLabel": profile.get("label"),
        "investmentRiskTolerance": profile.get("riskTolerance"),
        "strategyLossTolerancePct": number(profile.get("lossTolerancePct")),
        "strategyProfitProtectionPct": number(profile.get("profitProtectionPct")),
        "strategyMaxPositionWeightPct": number(profile.get("maxPositionWeightPct")),
        "strategyMaxSectorWeightPct": number(profile.get("maxSectorWeightPct")),
        "strategyFxExposureReviewPct": number(profile.get("fxExposureReviewPct")),
        "strategyAddBuyPolicy": profile.get("addBuyPolicy"),
        "strategyAddBuyWatchSignalMin": int(number(profile.get("addBuyWatchSignalMin")) or 3),
        "strategyAddBuyReviewSignalMin": int(number(profile.get("addBuyReviewSignalMin")) or 5),
        "strategyAllowLossAddBuyReview": bool(profile.get("allowLossAddBuyReview")),
    }


def _loss_severity_band(pnl: float, loss_tolerance: float) -> str:
    if pnl >= 0:
        return "not_loss"
    tolerance = abs(loss_tolerance or -8.0)
    loss = abs(pnl)
    if loss >= max(20.0, tolerance * 2.0):
        return "large_loss"
    if loss >= tolerance:
        return "loss_control"
    return "mild_loss"


def _loss_smart_money_facts(facts: Dict[str, object]) -> Dict[str, object]:
    pnl = number(facts.get("profitLossRate"))
    loss_tolerance = number(facts.get("strategyLossTolerancePct")) or -8.0
    max_position_weight = number(facts.get("strategyMaxPositionWeightPct")) or 25.0
    watch_min = int(number(facts.get("strategyAddBuyWatchSignalMin")) or 3)
    review_min = int(number(facts.get("strategyAddBuyReviewSignalMin")) or 5)
    allow_review = bool(facts.get("strategyAllowLossAddBuyReview"))
    is_holding = bool(facts.get("isHolding"))
    joint_inflow = bool(facts.get("jointSmartMoneyInflow"))
    direct_risk_news = int(number(facts.get("directRiskNewsCount")))
    position_weight = number(facts.get("positionAccountWeight")) or number(facts.get("positionWeight"))
    ma5_recovered = bool(facts.get("ma5")) and number(facts.get("ma5Distance")) >= 0
    ma20_recovered = bool(facts.get("ma20")) and number(facts.get("ma20Distance")) >= 0
    ma60_supported = bool(facts.get("ma60")) and number(facts.get("ma60Distance")) >= -1.0
    volume_confirmed = number(facts.get("volumeRatio")) >= 1.0
    trade_confirmed = number(facts.get("tradeStrength")) >= 100
    bid_confirmed = number(facts.get("bidAskImbalance")) >= 5
    trend_recovery = bool(facts.get("supportRetest") or facts.get("recoveryAttempt") or number(facts.get("priceChangeRate")) >= 1.0)
    news_clear = direct_risk_news == 0
    position_weight_ok = not position_weight or position_weight <= max_position_weight
    avoid_averaging_down = bool(facts.get("avoidAveragingDown"))
    recovery_flags = {
        "5일선 회복": ma5_recovered,
        "20일선 회복": ma20_recovered,
        "60일선 부근 지지": ma60_supported,
        "거래량 확인": volume_confirmed,
        "체결강도 확인": trade_confirmed,
        "매수 호가 우위": bid_confirmed,
        "가격 회복 시도": trend_recovery,
        "직접 악재 뉴스 없음": news_clear,
    }
    recovery_count = sum(1 for value in recovery_flags.values() if value)
    loss_active = is_holding and pnl < 0
    stage = "NONE"
    label = "추가매수 판단 대상 아님"
    blocked_reasons: List[str] = []
    opened_reasons = [key for key, value in recovery_flags.items() if value]
    if loss_active and not joint_inflow:
        stage = "ADD_BUY_BLOCKED"
        label = "추가매수 보류"
        blocked_reasons.append("외국인·기관 동반 순매수 없음")
    elif loss_active and joint_inflow:
        stage = "FLOW_DEFENSE"
        label = "매도 강도 완화"
        review_ready = (
            allow_review
            and recovery_count >= review_min
            and news_clear
            and position_weight_ok
            and (ma20_recovered or (ma5_recovered and ma60_supported))
        )
        profile_blocks_loss_add = avoid_averaging_down and not review_ready
        if direct_risk_news:
            blocked_reasons.append("직접 악재 뉴스 있음")
        if not position_weight_ok:
            blocked_reasons.append("종목 비중이 투자 성향 한도보다 큼")
        if profile_blocks_loss_add:
            blocked_reasons.append("종목 타입 정책상 손실 구간 추가매수 회피")
        if recovery_count < watch_min:
            blocked_reasons.append("회복 확인 신호 부족")
        if recovery_count >= watch_min and not profile_blocks_loss_add:
            stage = "ADD_BUY_WATCH"
            label = "추가매수 관찰"
        elif recovery_count >= watch_min and profile_blocks_loss_add:
            stage = "FLOW_DEFENSE"
            label = "수급 방어 관찰"
        if review_ready:
            stage = "ADD_BUY_REVIEW"
            label = "조건부 추가매수 검토"
    return {
        "lossSeverityBand": _loss_severity_band(pnl, loss_tolerance),
        "lossSmartMoneyDefenseActive": bool(loss_active and joint_inflow),
        "lossRecoverySignalCount": recovery_count,
        "lossRecoverySignalLabels": opened_reasons,
        "addBuyEligibilityStage": stage,
        "addBuyEligibilityLabel": label,
        "addBuyBlockedReasons": blocked_reasons,
        "addBuyWatchSignalMin": watch_min,
        "addBuyReviewSignalMin": review_min,
        "addBuyPolicy": facts.get("strategyAddBuyPolicy"),
    }


def _missing(key: str, label: str, effect: str, status: str = "missing", source: str = "") -> Dict[str, str]:
    item = {"key": key, "label": label, "effect": effect, "status": status}
    if source:
        item["source"] = source
    return item


def _data_quality_warning(key: str, label: str, effect: str, status: str = "warning", source: str = "") -> Dict[str, str]:
    item = {"key": key, "label": label, "effect": effect, "status": status}
    if source:
        item["source"] = source
    return item


def _coverage_item(coverage: Dict[str, object], stage: str) -> Dict[str, object]:
    if not isinstance(coverage, dict):
        return {}
    item = coverage.get(stage)
    return item if isinstance(item, dict) else {}


def _coverage_status(
    coverage: Dict[str, object],
    stage: str,
    keys: Iterable[str],
    value: float = 0.0,
    quote_status: str = "",
    quote_hint: str = "",
) -> str:
    if isinstance(coverage, dict) and coverage:
        item = _coverage_item(coverage, stage)
        fields = set(str(field) for field in (item.get("fields") or []))
        non_zero_fields = set(str(field) for field in (item.get("nonZeroFields") or []))
        status = str(item.get("status") or "").strip()
        if status in {"stale", "unknown"}:
            return status
        if any(key in fields for key in keys):
            return "available" if any(key in non_zero_fields for key in keys) or value else "zero"
        if status in {"empty", "missing"}:
            return status
    if value:
        return "available"
    if quote_hint and quote_hint in str(quote_status or ""):
        return "zero"
    return "missing"


def _availability(status: str, source: str = "") -> Dict[str, str]:
    item = {"status": str(status or "missing")}
    if source:
        item["source"] = source
    return item


def _availability_with_coverage(status: str, source: str, coverage: Dict[str, object], stage: str) -> Dict[str, object]:
    item: Dict[str, object] = _availability(status, source)
    stage_item = _coverage_item(coverage, stage)
    for key in [
        "realTime",
        "transport",
        "freshnessStatus",
        "sourceAsOfConfidence",
        "aiUsableAsStrongEvidence",
        "judgementEvidenceUsable",
        "cadence",
        "latencyStatus",
        "latencyLabel",
        "latencyReason",
        "staleReason",
        "reason",
        "sourceAsOf",
        "fetchedAt",
        "unchangedCount",
    ]:
        if key in stage_item and stage_item.get(key) not in (None, ""):
            item[key] = stage_item.get(key)
    return item


def _missing_penalty(item: Dict[str, object]) -> float:
    status = str(item.get("status") or "missing")
    if status in {"zero", "proxy", "empty"}:
        return 6.0
    return 12.0


def _warning_penalty(item: Dict[str, object]) -> float:
    status = str(item.get("status") or "warning")
    if status == "stale":
        return 8.0
    if status == "unknown":
        return 6.0
    return 3.0


def moving_average_distance_text(label: str, distance: float) -> str:
    value = abs(round(float(distance or 0), 1))
    value_text = str(int(value)) if value.is_integer() else str(value)
    if distance > 0:
        return label + "보다 " + value_text + "% 높음"
    if distance < 0:
        return label + "보다 " + value_text + "% 낮음"
    return label + "과 같음"


def direction_label(value: float, positive: str, negative: str, flat: str = "보합", threshold: float = 0.2) -> str:
    parsed = float(value or 0)
    if parsed >= threshold:
        return positive
    if parsed <= -threshold:
        return negative
    return flat


def trend_slope_label(ma20_slope: float, ma60_slope: float) -> str:
    short = float(ma20_slope or 0)
    medium = float(ma60_slope or 0)
    if short <= -0.3 and medium <= -0.2:
        return "단기·중기 하락"
    if short >= 0.3 and medium >= 0.2:
        return "단기·중기 상승"
    if short <= -0.3 and medium > 0:
        return "단기 둔화·중기 지지"
    if short >= 0.3 and medium <= 0:
        return "단기 회복·중기 둔화"
    return "혼조·완만"


def trend_curve_label(curve: float) -> str:
    value = float(curve or 0)
    if value <= -1.0:
        return "하락 커브 확대"
    if value <= -0.4:
        return "단기 둔화 커브"
    if value >= 1.0:
        return "회복 커브 확대"
    if value >= 0.4:
        return "단기 회복 커브"
    return "커브 완만"


def trend_state_label(
    ma20_distance: float,
    ma60_distance: float,
    support_retest: bool,
    recovery_attempt: bool,
    breakdown_acceleration: bool,
) -> str:
    if breakdown_acceleration:
        return "하락 가속"
    if support_retest and recovery_attempt:
        return "60일선 지지 반등 시도"
    if support_retest:
        return "60일선 지지 재확인"
    if recovery_attempt:
        return "회복 시도"
    if ma20_distance < 0 and ma60_distance < 0:
        return "추세 하방"
    if ma20_distance > 0 and ma60_distance > 0:
        return "추세 상방"
    return "추세 혼조"


def trend_dynamic_risk_score(
    ma20_distance: float,
    ma60_distance: float,
    price_change: float,
    ma20_slope: float,
    trend_curve: float,
    support_retest: bool,
    recovery_attempt: bool,
) -> float:
    risk = 0.0
    if ma20_distance <= -5:
        risk += min(30.0, abs(ma20_distance) * 2.0)
    if ma60_distance < 0:
        risk += min(20.0, abs(ma60_distance) * 3.0)
    if price_change < 0:
        risk += min(15.0, abs(price_change) * 4.0)
    if ma20_slope < 0:
        risk += min(15.0, abs(ma20_slope) * 8.0)
    if trend_curve < 0:
        risk += min(10.0, abs(trend_curve) * 6.0)
    if support_retest and ma60_distance >= 0:
        risk -= 5.0
    if recovery_attempt:
        risk -= 10.0
    return clamp(risk, 0.0, 100.0)


def position_signal_facts(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Optional[Dict[str, object]] = None,
    previous_state: Optional[Dict[str, object]] = None,
    previous_decision: Optional[Dict[str, object]] = None,
    account_context: Optional[Dict[str, object]] = None,
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    external_signals = external_signals or {}
    trend = _trend_facts(position)
    flow = _investor_flow(position)
    buy_volume = number(position.buy_volume)
    sell_volume = number(position.sell_volume)
    total_execution_volume = buy_volume + sell_volume
    buy_share = (buy_volume / total_execution_volume) * 100.0 if total_execution_volume else 0.0
    sell_share = 100.0 - buy_share if total_execution_volume else 0.0
    orderbook_bid_volume = number(position.orderbook_bid_volume)
    orderbook_ask_volume = number(position.orderbook_ask_volume)
    bid_ask_imbalance = number(position.bid_ask_imbalance)
    investor_values_reliable = investor_flow_values_reliable(position)
    foreign_buy_volume = number(position.foreign_buy_volume) if investor_values_reliable else 0.0
    foreign_sell_volume = number(position.foreign_sell_volume) if investor_values_reliable else 0.0
    institution_buy_volume = number(position.institution_buy_volume) if investor_values_reliable else 0.0
    institution_sell_volume = number(position.institution_sell_volume) if investor_values_reliable else 0.0
    individual_buy_volume = number(position.individual_buy_volume) if investor_values_reliable else 0.0
    individual_sell_volume = number(position.individual_sell_volume) if investor_values_reliable else 0.0
    execution_direction_proxy = bool(position.trade_strength or bid_ask_imbalance or orderbook_bid_volume or orderbook_ask_volume)
    market_signal_coverage = dict(position.market_signal_coverage or {}) if isinstance(position.market_signal_coverage, dict) else {}
    quote_status = str(position.quote_status or "")
    btc = _btc_market(external_signals)
    disclosures = external_signals.get("dartDisclosures") if isinstance(external_signals, dict) else {}
    symbol = str(position.symbol or "").upper()
    disclosure = disclosures.get(symbol) if isinstance(disclosures, dict) else None
    news_headlines = external_signals.get("newsHeadlines") if isinstance(external_signals, dict) else {}
    news_context = news_headlines.get(symbol) if isinstance(news_headlines, dict) and isinstance(news_headlines.get(symbol), dict) else {}
    sec_filings = external_signals.get("secFilings") if isinstance(external_signals, dict) else {}
    sec_context = sec_filings.get(symbol) if isinstance(sec_filings, dict) and isinstance(sec_filings.get(symbol), dict) else {}
    trading_snapshot = trading_value_snapshot(position.current_price, position.volume, position.trading_value)
    volume_pace = volume_pace_snapshot(
        position.market,
        position.volume_ratio,
        volume=position.volume,
        trading_value=trading_snapshot.get("tradingValue"),
        observed_at=position.updated_at,
    )
    facts: Dict[str, object] = {
        "symbol": symbol,
        "name": position.name,
        "market": position.market,
        "currency": position.currency,
        "sector": position.sector,
        "source": position.source,
        "quoteSource": position.quote_source,
        "quoteStatus": quote_status,
        "quoteMessage": position.quote_message,
        "dataQuality": position.data_quality,
        "updatedAt": position.updated_at,
        "marketSignalCoverage": market_signal_coverage,
        "isHolding": str(position.source or "holding") != "watchlist",
        "isWatchlist": str(position.source or "") == "watchlist",
        "profitLossRate": number(position.profit_loss_rate),
        "profitLoss": number(position.profit_loss),
        "marketValue": number(position.market_value),
        "quantity": number(position.quantity),
        "sellableQuantity": number(position.sellable_quantity),
        "averagePrice": number(position.average_price),
        "sectorRatio": _sector_ratio(position, portfolio),
        "positionWeight": _position_weight(position, portfolio),
        "positionAccountWeight": _position_account_weight(position, portfolio),
        "tradeStrength": number(position.trade_strength),
        "volume": number(position.volume),
        "volumeRatio": number(position.volume_ratio),
        "rawVolumeRatio": volume_pace.get("rawVolumeRatio"),
        "timeAdjustedVolumeRatio": volume_pace.get("timeAdjustedVolumeRatio"),
        "expectedVolumeRatioNow": volume_pace.get("expectedVolumeRatioNow"),
        "volumePaceStatus": volume_pace.get("volumePaceStatus"),
        "volumePaceLabel": volume_pace.get("volumePaceLabel"),
        "volumePaceSession": volume_pace.get("volumePaceSession"),
        "volumePaceSessionLabel": volume_pace.get("volumePaceSessionLabel"),
        "volumePaceElapsedPct": volume_pace.get("volumePaceElapsedPct"),
        "volumePaceLocalTime": volume_pace.get("volumePaceLocalTime"),
        "volumePaceBasis": volume_pace.get("volumePaceBasis"),
        "tradingValue": number(trading_snapshot.get("tradingValue")),
        "reportedTradingValue": number(trading_snapshot.get("reportedTradingValue")),
        "estimatedTradingValue": number(trading_snapshot.get("estimatedTradingValue")),
        "tradingValueQuality": trading_snapshot.get("tradingValueQuality"),
        "tradingValueBasis": trading_snapshot.get("tradingValueBasis"),
        "tradingValueMismatchPct": trading_snapshot.get("tradingValueMismatchPct"),
        "tradingValueEstimated": trading_snapshot.get("tradingValueEstimated"),
        "tradingValueReliable": trading_snapshot.get("tradingValueReliable"),
        "buyVolume": buy_volume,
        "sellVolume": sell_volume,
        "buyShare": buy_share,
        "sellShare": sell_share,
        "orderbookBidVolume": orderbook_bid_volume,
        "orderbookAskVolume": orderbook_ask_volume,
        "bidAskImbalance": bid_ask_imbalance,
        "foreignBuyVolume": foreign_buy_volume,
        "foreignSellVolume": foreign_sell_volume,
        "institutionBuyVolume": institution_buy_volume,
        "institutionSellVolume": institution_sell_volume,
        "individualBuyVolume": individual_buy_volume,
        "individualSellVolume": individual_sell_volume,
        "executionDirectionProxy": execution_direction_proxy,
        "btcChange24h": number(btc.get("change24h")) if btc else 0.0,
        "btcChange7d": number(btc.get("change7d")) if btc else 0.0,
        "btcPrice": number(btc.get("price")) if btc else 0.0,
        "btcVolume24h": number(btc.get("volume24h")) if btc else 0.0,
        "isBtcSensitive": symbol in BTC_SENSITIVE_SYMBOLS,
        "dartDisclosure": dict(disclosure or {}) if isinstance(disclosure, dict) else {},
        "newsHeadlines": dict(news_context or {}) if isinstance(news_context, dict) else {},
        "secFiling": dict(sec_context or {}) if isinstance(sec_context, dict) else {},
        "expectsKrMicrostructureSignals": expects_kr_microstructure_signals(position.market, position.currency, symbol),
    }
    profile = instrument_profile_for_position(position, settings)
    profile_payload = profile.to_dict()
    facts.update({
        "instrumentProfile": profile_payload,
        "instrumentProfileLabel": profile.label,
        "instrumentArchetypes": list(profile.archetypes),
        "instrumentArchetypeLabels": list(profile_payload.get("archetypeLabels") or []),
        "instrumentPositionIntent": profile.position_intent,
        "instrumentPositionIntentLabel": profile_payload.get("positionIntentLabel") or "",
        "instrumentPositionIntentDescription": profile_payload.get("positionIntentDescription") or "",
        "instrumentSensitivities": dict(profile.sensitivities),
        "instrumentPolicies": dict(profile.policies),
        "allowAddOnStrength": profile.allow_add_on_strength,
        "trimOnTrendBreak": profile.trim_on_trend_break,
        "avoidAveragingDown": profile.avoid_averaging_down,
    })
    research_by_id = {}
    for item in research_evidence_from_facts(symbol, facts) + research_evidence_from_external_signals(symbol, external_signals):
        research_by_id[item.evidence_id] = item.to_dict()
    facts["researchEvidence"] = list(research_by_id.values())
    facts.update(research_evidence_facts(facts["researchEvidence"]))
    facts.update(trend)
    facts.update(flow)
    facts.update(_investment_strategy_facts(external_signals, account_context, settings))
    facts.update(_temporal_facts(position, previous_state, previous_decision))
    facts.update(_liquidity_facts(position))
    facts.update(_external_quality_facts(external_signals))
    facts.update(macro_context_facts(position, portfolio, external_signals))
    facts.update(_valuation_facts(position, external_signals, settings))
    facts.update(_loss_smart_money_facts(facts))
    missing: List[Dict[str, str]] = []
    if not facts["currentPrice"]:
        missing.append(_missing("currentPrice", "현재가", "가격·이동평균 관계 판단 신뢰도가 낮아집니다."))
    if facts.get("isWatchlist") and not facts["ma5"]:
        missing.append(_missing("ma5", "5일 이동평균", "짧은 진입 타이밍을 확인할 수 없어 신규 진입 판단 강도를 낮춥니다."))
    if not facts["ma20"]:
        missing.append(_missing("ma20", "20일 이동평균", "단기 추세 이탈 여부를 확인할 수 없습니다."))
    if not facts["ma60"]:
        missing.append(_missing("ma60", "60일 이동평균", "중기 추세 위치를 확인할 수 없습니다."))
    expects_kr_signals = bool(facts.get("expectsKrMicrostructureSignals"))
    trade_strength_status = _coverage_status(
        market_signal_coverage,
        "ccnl",
        ["tradeStrength"],
        float(facts["tradeStrength"] or 0),
        quote_status,
        "체결강도",
    )
    execution_volume_status = _coverage_status(
        market_signal_coverage,
        "ccnl",
        ["buyVolume", "sellVolume"],
        float(total_execution_volume or 0),
        quote_status,
        "방향별 체결량",
    )
    investor_flow_status = _coverage_status(
        market_signal_coverage,
        "investor",
        [
            "foreignBuyVolume",
            "foreignSellVolume",
            "foreignNetVolume",
            "foreignNetAmount",
            "institutionBuyVolume",
            "institutionSellVolume",
            "institutionNetVolume",
            "institutionNetAmount",
            "individualBuyVolume",
            "individualSellVolume",
            "individualNetVolume",
            "individualNetAmount",
        ],
        float(facts["investorFlowBase"] or 0),
        quote_status,
        "투자자별 수급",
    )
    if execution_volume_status != "available" and execution_direction_proxy:
        execution_volume_status = "proxy"
    facts["dataAvailability"] = {
        "tradeStrength": _availability_with_coverage(trade_strength_status, "KIS ccnl", market_signal_coverage, "ccnl"),
        "executionVolume": _availability_with_coverage(execution_volume_status, "KIS ccnl/orderbook", market_signal_coverage, "ccnl"),
        "investorFlow": _availability_with_coverage(investor_flow_status, "KIS investor", market_signal_coverage, "investor"),
    }
    data_quality_warnings: List[Dict[str, str]] = []
    investor_coverage = _coverage_item(market_signal_coverage, "investor")
    investor_latency = str(investor_coverage.get("latencyStatus") or "").strip()
    investor_latency_reason = str(
        investor_coverage.get("latencyReason")
        or investor_coverage.get("staleReason")
        or investor_coverage.get("reason")
        or ""
    ).strip()
    if expects_kr_signals and investor_flow_status in {"available", "stale", "unknown"} and (
        investor_coverage.get("realTime") is False
        or investor_coverage.get("aiUsableAsStrongEvidence") is False
        or investor_latency
        or investor_flow_status in {"stale", "unknown"}
    ):
        latency_label = str(investor_coverage.get("latencyLabel") or "투자자별 수급 지연 가능").strip()
        effect = investor_latency_reason or "투자자별 수급은 장중 누적·지연 가능 데이터라 현재가·호가 같은 실시간 체결 근거로 과신하지 않습니다."
        data_quality_warnings.append(_data_quality_warning(
            "investorFlowLatency",
            latency_label,
            effect,
            investor_flow_status if investor_flow_status in {"stale", "unknown"} else "latency",
            "KIS investor",
        ))
    if expects_kr_signals and trade_strength_status != "available":
        if trade_strength_status == "zero":
            effect = "체결강도 응답은 있었지만 0으로 들어와 체결 압력 근거로 쓰지 않습니다."
        elif trade_strength_status == "empty":
            effect = "KIS 체결 단계 응답이 비어 있어 수급 방향 판단을 가격·거래량 중심으로 봅니다."
        else:
            effect = "체결 압력 확인값이 없어 수급 방향 판단을 가격·거래량 중심으로 봅니다."
        missing.append(_missing("tradeStrength", "체결강도", effect, trade_strength_status, "KIS ccnl"))
    if expects_kr_signals and not total_execution_volume and not execution_direction_proxy:
        if execution_volume_status == "zero":
            effect = "방향별 체결량 응답은 있었지만 매수·매도 합계가 0으로 들어와 수급 방향 점수는 중립에 가깝게 처리합니다."
        elif execution_volume_status == "empty":
            effect = "KIS 체결 단계 응답이 비어 있어 매수·매도 방향별 체결 압력을 확인하지 못합니다."
        else:
            effect = "매수·매도 방향별 체결 압력을 확인하지 못해 수급 방향 점수는 중립에 가깝게 처리합니다."
        missing.append(_missing("executionVolume", "방향별 매수/매도 체결량", effect, execution_volume_status, "KIS ccnl"))
    if expects_kr_signals and not facts["investorFlowBase"]:
        if investor_flow_status == "zero":
            effect = "투자자별 수급 응답은 있었지만 외국인·기관·개인 순매수 합계가 0으로 들어와 방향성 근거로 쓰지 않습니다."
        elif investor_flow_status == "empty":
            effect = "KIS 투자자 단계 응답이 비어 있어 주체별 수급은 중립으로 처리합니다."
        elif investor_coverage.get("realTime") is False or investor_coverage.get("aiUsableAsStrongEvidence") is False or investor_latency or investor_flow_status in {"stale", "unknown"}:
            effect = investor_latency_reason or "KIS 투자자별 수급이 지연·반복값으로 판정되어 주체별 수급은 중립으로 처리합니다."
            missing.append(_missing("investorFlow", "투자자별 수급", effect, investor_flow_status if investor_flow_status in {"stale", "unknown"} else "latency", "KIS investor"))
            effect = ""
        else:
            effect = "외국인·기관·개인 순매수는 수집되지 않아 주체별 수급은 중립으로 처리합니다. 가격·거래량·체결강도 중심 판단입니다."
        if effect:
            missing.append(_missing("investorFlow", "투자자별 수급", effect, investor_flow_status, "KIS investor"))
    if facts["isBtcSensitive"] and not btc:
        missing.append(_missing("btcMarket", "비트코인 시장 데이터", "비트코인 민감 종목의 외부 연동 위험을 확인하지 못합니다."))
    valuation_missing_inputs = facts.get("valuationMissingInputs") if isinstance(facts.get("valuationMissingInputs"), list) else []
    if facts.get("valuationRows") and valuation_missing_inputs:
        missing.append(_missing(
            "valuationInputs",
            "밸류에이션 입력값",
            "적정가 판단에 필요한 값이 일부 부족합니다: " + ", ".join(str(item) for item in valuation_missing_inputs[:5]),
            "missing",
            str(facts.get("valuationSourceLabel") or "valuation"),
        ))
    data_quality = clamp(
        100.0
        - sum(_missing_penalty(item) for item in missing)
        - sum(_warning_penalty(item) for item in data_quality_warnings),
        35.0,
        100.0,
    )
    facts["missingData"] = missing
    facts["dataQualityWarnings"] = data_quality_warnings
    facts["dataQualityScore"] = data_quality
    return facts


def _evidence_payload(item: Dict[str, object]) -> Dict[str, object]:
    payload = item.get("payload") if isinstance(item, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _evidence_scope(item: Dict[str, object]) -> str:
    payload = _evidence_payload(item)
    return str(item.get("relationScope") or payload.get("relationScope") or "").strip().lower()


def _evidence_relevance(item: Dict[str, object]) -> float:
    payload = _evidence_payload(item)
    return number(item.get("relevanceScore") or payload.get("relevanceScore"))


def _evidence_source_reliability(item: Dict[str, object]) -> float:
    payload = _evidence_payload(item)
    return number(item.get("sourceReliability") or payload.get("sourceReliability") or item.get("confidence"))


def _evidence_title(item: Dict[str, object]) -> str:
    return " ".join(str(item.get("title") or item.get("summary") or "").split())[:180]


def _evidence_event_type(item: Dict[str, object]) -> str:
    payload = _evidence_payload(item)
    return str(item.get("eventType") or payload.get("eventType") or "general").strip() or "general"


def _evidence_materiality(item: Dict[str, object]) -> float:
    payload = _evidence_payload(item)
    return number(item.get("materialityScore") or payload.get("materialityScore"))


def _evidence_minutes_since(item: Dict[str, object]) -> float:
    raw = str(item.get("publishedAt") or item.get("observedAt") or "").strip()
    if not raw:
        return 0.0
    for candidate in [raw, raw.replace("Z", "+00:00")]:
        try:
            parsed = datetime.fromisoformat(candidate)
            parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60)
        except ValueError:
            pass
    for fmt in ["%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y%m%d"]:
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 60)
        except ValueError:
            pass
    return 0.0


def research_evidence_facts(evidence_items: Iterable[Dict[str, object]]) -> Dict[str, object]:
    news_items = [
        item
        for item in evidence_items or []
        if isinstance(item, dict) and str(item.get("kind") or "").lower() == "news"
        and news_domain.relation_scope_is_investable(_evidence_scope(item))
    ]
    direct = [item for item in news_items if _evidence_scope(item) == "direct"]
    peer = [item for item in news_items if _evidence_scope(item) == "peer"]
    sector = [item for item in news_items if _evidence_scope(item) == "sector"]
    market = [item for item in news_items if _evidence_scope(item) == "market"]
    risk = [item for item in direct if str(item.get("polarity") or "").lower() in {"risk", "contradiction"}]
    support = [item for item in direct if str(item.get("polarity") or "").lower() == "support"]
    material = [
        item
        for item in news_items
        if _evidence_scope(item) == "direct" or _evidence_relevance(item) >= 55 or _evidence_materiality(item) >= 45
    ]
    event_type_counts: Dict[str, int] = {}
    for item in news_items:
        event_type = _evidence_event_type(item)
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1

    def titles(rows: List[Dict[str, object]], limit: int = 4) -> List[str]:
        result: List[str] = []
        for row in sorted(rows, key=lambda item: (_evidence_relevance(item), number(item.get("impactScore"))), reverse=True):
            title = _evidence_title(row)
            if title and title not in result:
                result.append(title)
            if len(result) >= limit:
                break
        return result

    relevance_values = [_evidence_relevance(item) for item in news_items if _evidence_relevance(item)]
    reliability_values = [_evidence_source_reliability(item) for item in news_items if _evidence_source_reliability(item)]
    materiality_values = [_evidence_materiality(item) for item in news_items if _evidence_materiality(item)]
    risk_score = sum((number(item.get("impactScore")) or 2.0) * max(0.35, _evidence_relevance(item) / 100) * max(0.8, _evidence_materiality(item) / 55 if _evidence_materiality(item) else 1) for item in risk)
    support_score = sum((number(item.get("impactScore")) or 2.0) * max(0.35, _evidence_relevance(item) / 100) * max(0.8, _evidence_materiality(item) / 55 if _evidence_materiality(item) else 1) for item in support)
    latest_direct_age = min([_evidence_minutes_since(item) for item in direct if _evidence_minutes_since(item)], default=0.0)
    return {
        "researchEvidenceCount": len([item for item in evidence_items or [] if isinstance(item, dict)]),
        "newsEvidenceCount": len(news_items),
        "directNewsCount": len(direct),
        "directRiskNewsCount": len(risk),
        "directSupportNewsCount": len(support),
        "peerNewsCount": len(peer),
        "sectorNewsCount": len(sector),
        "marketNewsCount": len(market),
        "materialNewsCount": len(material),
        "averageNewsRelevanceScore": round(sum(relevance_values) / len(relevance_values), 1) if relevance_values else 0.0,
        "averageNewsSourceReliability": round(sum(reliability_values) / len(reliability_values), 2) if reliability_values else 0.0,
        "averageNewsMaterialityScore": round(sum(materiality_values) / len(materiality_values), 1) if materiality_values else 0.0,
        "newsEventTypeCounts": event_type_counts,
        "topNewsEventTypes": [key for key, _count in sorted(event_type_counts.items(), key=lambda entry: entry[1], reverse=True)[:5]],
        "latestDirectNewsAgeMinutes": round(latest_direct_age, 1),
        "newsMomentumScore": round(support_score - risk_score, 1),
        "newsConflictScore": round(min(support_score, risk_score), 1) if risk_score and support_score else 0.0,
        "topNewsTitles": titles(material or news_items, 5),
        "directRiskNewsTitles": titles(risk, 4),
        "directSupportNewsTitles": titles(support, 4),
        "sectorNewsTitles": titles(peer + sector + market, 4),
    }
