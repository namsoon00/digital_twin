from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .investment_research import research_evidence_from_external_signals, research_evidence_from_facts
from .macro_context import macro_context_facts
from .market_data import clamp, number
from . import news_analysis as news_domain
from .ontology_relation_contracts import BTC_SENSITIVE_SYMBOLS
from .portfolio import PortfolioSummary, Position, expects_kr_microstructure_signals
from .volume_time_adjustment import volume_pace_snapshot


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


def _investor_flow(position: Position) -> Dict[str, float]:
    if not _investor_flow_values_reliable(position):
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
        }
    foreign_volume = number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume)
    institution_volume = number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume)
    individual_volume = number(position.individual_net_volume) or number(position.individual_buy_volume) - number(position.individual_sell_volume)
    foreign = foreign_volume or number(position.foreign_net_amount)
    institution = institution_volume or number(position.institution_net_amount)
    individual = individual_volume or number(position.individual_net_amount)
    base = abs(foreign) + abs(institution) + abs(individual)
    smart_money = foreign + institution
    score = clamp((smart_money - individual * 0.35) / base * 100.0, -100.0, 100.0) if base else 0.0
    return {
        "foreignNetVolume": foreign_volume,
        "institutionNetVolume": institution_volume,
        "individualNetVolume": individual_volume,
        "foreignNetAmount": number(position.foreign_net_amount),
        "institutionNetAmount": number(position.institution_net_amount),
        "individualNetAmount": number(position.individual_net_amount),
        "smartMoneyNetVolume": smart_money,
        "investorFlowBase": base,
        "investorFlowScore": score,
    }


def _investor_flow_values_reliable(position: Position) -> bool:
    coverage = position.market_signal_coverage if isinstance(position.market_signal_coverage, dict) else {}
    investor = coverage.get("investor") if isinstance(coverage.get("investor"), dict) else {}
    if not investor:
        return True
    status = str(investor.get("status") or "").strip()
    latency_status = str(investor.get("latencyStatus") or "").strip()
    if status in {"stale", "unknown", "unavailable", "missing", "empty"}:
        return False
    if investor.get("realTime") is False or latency_status or str(investor.get("cadence") or "") == "stale-repeat":
        return False
    return True


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
    investor_values_reliable = _investor_flow_values_reliable(position)
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
    volume_pace = volume_pace_snapshot(
        position.market,
        position.volume_ratio,
        volume=position.volume,
        trading_value=position.trading_value,
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
        "tradingValue": number(position.trading_value),
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
    research_by_id = {}
    for item in research_evidence_from_facts(symbol, facts) + research_evidence_from_external_signals(symbol, external_signals):
        research_by_id[item.evidence_id] = item.to_dict()
    facts["researchEvidence"] = list(research_by_id.values())
    facts.update(research_evidence_facts(facts["researchEvidence"]))
    facts.update(trend)
    facts.update(flow)
    facts.update(_temporal_facts(position, previous_state, previous_decision))
    facts.update(_liquidity_facts(position))
    facts.update(_external_quality_facts(external_signals))
    facts.update(macro_context_facts(position, portfolio, external_signals))
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
        elif investor_coverage.get("realTime") is False or investor_latency or investor_flow_status in {"stale", "unknown"}:
            effect = investor_latency_reason or "KIS 투자자별 수급이 지연·반복값으로 판정되어 주체별 수급은 중립으로 처리합니다."
            missing.append(_missing("investorFlow", "투자자별 수급", effect, investor_flow_status if investor_flow_status in {"stale", "unknown"} else "latency", "KIS investor"))
            effect = ""
        else:
            effect = "외국인·기관·개인 순매수는 수집되지 않아 주체별 수급은 중립으로 처리합니다. 가격·거래량·체결강도 중심 판단입니다."
        if effect:
            missing.append(_missing("investorFlow", "투자자별 수급", effect, investor_flow_status, "KIS investor"))
    if facts["isBtcSensitive"] and not btc:
        missing.append(_missing("btcMarket", "비트코인 시장 데이터", "비트코인 민감 종목의 외부 연동 위험을 확인하지 못합니다."))
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
