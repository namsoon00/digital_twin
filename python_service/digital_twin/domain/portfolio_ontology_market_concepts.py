from typing import Dict, List

from .investor_flow_psychology import investor_flow_psychology
from .market_data import clamp, number
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .portfolio import Position, expects_kr_microstructure_signals
from .portfolio_ontology_catalog import METRIC_CONCEPTS
from .volume_time_adjustment import volume_pace_snapshot


def metric_tbox_classes(tbox_class: str, field_name: str) -> List[str]:
    if tbox_class == "PriceMetric":
        return ["Observation", "PriceObservation", "PriceMetric"]
    if tbox_class == "TechnicalIndicator":
        return ["Observation", "TechnicalObservation", "TechnicalIndicator", "TrendSignal"]
    if tbox_class == "TradeFlow":
        if field_name == "volume":
            return ["Observation", "VolumeObservation", "TradeFlow", "FlowSignal"]
        return ["Observation", "FlowObservation", "TradeFlow", "FlowSignal"]
    if tbox_class == "DataQuality":
        return ["Observation", "DataQuality", "DataQualitySignal"]
    return [tbox_class]

def symbol_key(position: Position) -> str:
    return str(position.symbol or position.name or "").upper().strip()


def smart_money_score(position: Position) -> float:
    profile = investor_flow_psychology(position)
    smart_money = number(profile.get("smartMoneyNetVolume"))
    base = abs(number(profile.get("foreignNetVolume"))) + abs(number(profile.get("institutionNetVolume")))
    return clamp((smart_money / base) * 100, -100.0, 100.0) if base else 0.0

def trend_score(position: Position) -> float:
    return clamp(
        number(position.ma20_distance) * 0.45
        + number(position.ma60_distance) * 0.25
        + number(position.ma20_slope) * 4
        + number(position.ma60_slope) * 3
        + number(position.change_rate) * 0.4,
        -35.0,
        35.0,
    )

def trend_dynamic_facts(position: Position) -> Dict[str, object]:
    ma20_distance = number(position.ma20_distance)
    ma60_distance = number(position.ma60_distance)
    ma20_slope = number(position.ma20_slope)
    ma60_slope = number(position.ma60_slope)
    price_change = number(position.change_rate)
    trend_curve = ma20_slope - ma60_slope
    has_ma20_context = bool(number(position.ma20) or ma20_distance)
    has_ma60_context = bool(number(position.ma60) or ma60_distance)
    short_term_breakdown = has_ma20_context and ma20_distance <= -5.0
    medium_term_support = has_ma60_context and ma60_distance >= 0.0
    support_retest = short_term_breakdown and has_ma60_context and ma60_distance >= -1.0
    recovery_attempt = (
        (ma20_distance < 0 or number(position.profit_loss_rate) < 0)
        and has_ma60_context
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
    if breakdown_acceleration:
        state = "하락 가속"
        polarity = "risk"
        impact = 16.0
    elif support_retest:
        state = "60일선 지지 재확인"
        polarity = "context"
        impact = 7.0
    elif recovery_attempt:
        state = "회복 시도"
        polarity = "support"
        impact = 7.0
    elif has_ma20_context and has_ma60_context and ma20_distance >= 0 and ma60_distance >= 0:
        state = "상승 추세 유지"
        polarity = "support"
        impact = 6.0
    elif short_term_breakdown:
        state = "단기 추세 훼손"
        polarity = "risk"
        impact = 10.0
    else:
        state = "중립 추세"
        polarity = "context"
        impact = 0.0
    risk_score = clamp(
        abs(min(0.0, ma20_distance)) * 2.0
        + abs(min(0.0, ma60_distance)) * 1.4
        + abs(min(0.0, price_change)) * 3.0
        + abs(min(0.0, ma20_slope)) * 6.0
        + abs(min(0.0, trend_curve)) * 4.0
        - (10.0 if support_retest or recovery_attempt else 0.0),
        0.0,
        100.0,
    )
    return {
        "state": state,
        "priceChangeRate": round(price_change, 2),
        "ma20Distance": round(ma20_distance, 2),
        "ma60Distance": round(ma60_distance, 2),
        "ma20Slope": round(ma20_slope, 2),
        "ma60Slope": round(ma60_slope, 2),
        "trendCurve": round(trend_curve, 2),
        "shortTermBreakdown": short_term_breakdown,
        "mediumTermSupport": medium_term_support,
        "supportRetest": support_retest,
        "recoveryAttempt": recovery_attempt,
        "breakdownAcceleration": breakdown_acceleration,
        "dynamicRiskScore": round(risk_score, 1),
        "polarity": polarity,
        "opinionImpact": impact,
    }

def data_quality_score(position: Position) -> float:
    missing = 0
    for value in [
        position.current_price,
        position.market_value,
        position.quantity,
        position.profit_loss_rate,
        position.ma20,
        position.ma60,
    ]:
        if value in (None, "", 0):
            missing += 1
    return clamp(100 - missing * 14, 15.0, 100.0)

def metric_value(position: Position, field_name: str) -> float:
    return number(getattr(position, field_name, 0))

def metric_relation_properties(field_name: str, value: float, source: str) -> Dict[str, object]:
    properties: Dict[str, object] = {"field": field_name, "source": source}
    if field_name == "profit_loss_rate":
        if value <= -8:
            properties.update({"polarity": "risk", "opinionImpact": min(18.0, abs(value)), "aiInfluenceLabel": "손실률이 관계 리스크를 높임"})
        elif value >= 20:
            properties.update({"polarity": "risk", "opinionImpact": 8.0, "aiInfluenceLabel": "큰 수익 구간이 이익 보호 필요성을 높임"})
        elif value > 0:
            properties.update({"polarity": "support", "supportImpact": min(8.0, value / 3), "aiInfluenceLabel": "수익 구간이 보유 근거를 보강"})
    elif field_name in {"ma20_distance", "ma60_distance", "ma20_slope", "ma60_slope"}:
        if value <= -5:
            properties.update({"polarity": "risk", "opinionImpact": min(14.0, abs(value)), "aiInfluenceLabel": "추세 지표 약화"})
        elif value >= 5:
            properties.update({"polarity": "support", "supportImpact": min(10.0, value), "aiInfluenceLabel": "추세 지표 우호"})
    elif field_name in {"foreign_net_volume", "institution_net_volume", "foreign_net_amount", "institution_net_amount"}:
        if value < 0:
            properties.update({"polarity": "risk", "opinionImpact": 5.0, "aiInfluenceLabel": "주요 수급 순매도"})
        elif value > 0:
            properties.update({"polarity": "support", "supportImpact": 5.0, "aiInfluenceLabel": "주요 수급 순매수"})
    elif field_name in {"volume_ratio", "trade_strength", "bid_ask_imbalance"}:
        if value:
            properties.update({"polarity": "context", "aiInfluenceLabel": "단기 수급 맥락"})
    return properties

def pct_distance_safe(value: float, reference: float) -> float:
    return ((number(value) / number(reference)) - 1) * 100 if number(value) and number(reference) else 0.0

def compact_price(value: object) -> str:
    numeric = number(value)
    if numeric >= 1000:
        return str(int(round(numeric)))
    return str(round(numeric, 4)).rstrip("0").rstrip(".")

def liquidity_profile(position: Position) -> Dict[str, object]:
    market_value = number(position.market_value)
    trading_value = number(position.trading_value)
    current_price = number(position.current_price)
    volume = number(position.volume)
    volume_ratio = number(position.volume_ratio)
    ask_pressure = max(0.0, -number(position.bid_ask_imbalance))
    sellable_quantity = number(position.sellable_quantity)
    quantity = number(position.quantity)
    bid_depth = number(position.orderbook_bid_volume)
    exit_days = market_value / max(1.0, trading_value * 0.1) if market_value and trading_value else 0.0
    position_to_value = (market_value / trading_value) * 100 if market_value and trading_value else 0.0
    position_to_volume = (sellable_quantity / volume) * 100 if sellable_quantity and volume else 0.0
    position_to_bid_depth = (sellable_quantity / bid_depth) * 100 if sellable_quantity and bid_depth else 0.0
    bid_depth_coverage = (bid_depth / sellable_quantity) * 100 if sellable_quantity and bid_depth else 0.0
    bid_depth_value = bid_depth * current_price if bid_depth and current_price else 0.0
    position_to_bid_depth_value = (market_value / bid_depth_value) * 100 if market_value and bid_depth_value else 0.0
    sellable_ratio = (sellable_quantity / quantity) * 100 if quantity else 0.0
    sellable_gap = 100.0 if quantity and sellable_quantity <= 0 else 0.0
    liquidity_risk = clamp(position_to_value * 2.0 + max(0.0, 1.0 - volume_ratio) * 18.0 + ask_pressure * 0.25 + sellable_gap * 0.25, 0.0, 100.0)
    slippage_risk = clamp(
        position_to_value * 1.4
        + position_to_bid_depth * 0.35
        + ask_pressure * 0.35
        + max(0.0, 0.8 - volume_ratio) * 20.0,
        0.0,
        100.0,
    )
    return {
        "hasMarketValue": bool(market_value),
        "hasTradingValue": bool(trading_value),
        "hasDailyVolume": bool(volume),
        "hasBidDepth": bool(bid_depth),
        "hasQuantity": bool(quantity),
        "hasSellableQuantity": bool(sellable_quantity),
        "positionToTradingValuePct": round(position_to_value, 2),
        "positionToDailyVolumePct": round(position_to_volume, 4),
        "positionToBidDepthPct": round(position_to_bid_depth, 4),
        "positionToBidDepthValuePct": round(position_to_bid_depth_value, 4),
        "bidDepthCoveragePct": round(bid_depth_coverage, 2),
        "bidDepthValue": round(bid_depth_value, 2),
        "sellableRatioPct": round(sellable_ratio, 2),
        "sellableBlocked": bool(quantity and sellable_quantity <= 0),
        "exitDaysAtTenPctADV": round(exit_days, 2),
        "liquidityRiskScore": round(liquidity_risk, 1),
        "slippageRiskScore": round(slippage_risk, 1),
        "volumeRatio": round(volume_ratio, 3),
        "bidAskImbalance": round(number(position.bid_ask_imbalance), 2),
    }

def add_execution_metric_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    display_name: str,
    liquidity: Dict[str, object],
    source: str,
) -> None:
    metric_rows = [
        ("positionToTradingValuePct", "보유금액/거래대금", "position-exit-exposure", ["hasMarketValue", "hasTradingValue"]),
        ("positionToDailyVolumePct", "보유수량/거래량", "position-exit-exposure", ["hasSellableQuantity", "hasDailyVolume"]),
        ("positionToBidDepthPct", "매도가능수량/매수호가잔량", "position-exit-exposure", ["hasSellableQuantity", "hasBidDepth"]),
        ("positionToBidDepthValuePct", "평가액/매수호가잔량가치", "position-exit-exposure", ["hasMarketValue", "hasBidDepth"]),
        ("bidDepthCoveragePct", "매수호가잔량 커버리지", "execution-capacity", ["hasSellableQuantity", "hasBidDepth"]),
        ("sellableRatioPct", "매도가능수량 비율", "execution-capacity", ["hasQuantity"]),
        ("sellableBlocked", "매도가능 제한", "execution-capacity", ["hasQuantity"]),
        ("exitDaysAtTenPctADV", "10% ADV 청산 일수", "execution-capacity", ["hasMarketValue", "hasTradingValue"]),
        ("liquidityRiskScore", "유동성 위험 원천 점수", "liquidity-profile", []),
        ("slippageRiskScore", "슬리피지 위험 원천 점수", "slippage-estimate", []),
    ]
    for field_name, label, metric_role, required_flags in metric_rows:
        if any(not liquidity.get(flag) for flag in required_flags):
            continue
        raw_value = liquidity.get(field_name)
        numeric_value = 1.0 if raw_value is True else 0.0 if raw_value is False else number(raw_value)
        if raw_value in (None, ""):
            continue
        metric_id = add_entity(graph, "execution-metric", symbol + ":" + field_name, display_name + " " + label, {
            "tboxClass": "ExecutionMetric",
            "tboxClasses": ["Observation", "FlowObservation", "ExecutionMetric", "TradeFlow"],
            "symbol": symbol,
            "field": field_name,
            "value": round(numeric_value, 4),
            "valueNumber": round(numeric_value, 4),
            "metricRole": metric_role,
            "source": source,
        })
        relation_props = {
            "source": source,
            "polarity": "context",
            "field": field_name,
            "metricRole": metric_role,
            "aiInfluenceLabel": label,
        }
        add_relation(graph, stock_id, metric_id, "HAS_OBSERVATION", weight=1.0, properties=relation_props)
        add_relation(graph, stock_id, metric_id, "HAS_EXECUTION_METRIC", weight=1.0, properties=relation_props)

def volume_profile(position: Position) -> Dict[str, object]:
    volume_pace = volume_pace_snapshot(
        position.market,
        position.volume_ratio,
        volume=position.volume,
        trading_value=position.trading_value,
        observed_at=position.updated_at,
    )
    return {
        "volume": round(number(position.volume), 2),
        "volumeRatio": round(number(position.volume_ratio), 3),
        "rawVolumeRatio": volume_pace.get("rawVolumeRatio"),
        "timeAdjustedVolumeRatio": volume_pace.get("timeAdjustedVolumeRatio"),
        "expectedVolumeRatioNow": volume_pace.get("expectedVolumeRatioNow"),
        "volumePaceStatus": volume_pace.get("volumePaceStatus"),
        "volumePaceLabel": volume_pace.get("volumePaceLabel"),
        "volumePaceSession": volume_pace.get("volumePaceSession"),
        "volumePaceSessionLabel": volume_pace.get("volumePaceSessionLabel"),
        "volumePaceElapsedPct": volume_pace.get("volumePaceElapsedPct"),
        "volumePaceBasis": volume_pace.get("volumePaceBasis"),
        "tradingValue": round(number(position.trading_value), 2),
        "tradeStrength": round(number(position.trade_strength), 2),
        "buyVolume": round(number(position.buy_volume), 2),
        "sellVolume": round(number(position.sell_volume), 2),
        "orderbookBidVolume": round(number(position.orderbook_bid_volume), 2),
        "orderbookAskVolume": round(number(position.orderbook_ask_volume), 2),
        "bidAskImbalance": round(number(position.bid_ask_imbalance), 2),
        "foreignNetVolume": round(number(position.foreign_net_volume), 2),
        "foreignNetAmount": round(number(position.foreign_net_amount), 2),
        "institutionNetVolume": round(number(position.institution_net_volume), 2),
        "institutionNetAmount": round(number(position.institution_net_amount), 2),
        "individualNetVolume": round(number(position.individual_net_volume), 2),
        "individualNetAmount": round(number(position.individual_net_amount), 2),
    }

def smart_money_joint_flow_profile(position: Position) -> Dict[str, object]:
    profile = investor_flow_psychology(position)
    foreign_net = number(profile.get("foreignNetVolume"))
    institution_net = number(profile.get("institutionNetVolume"))
    smart_money_net = number(profile.get("smartMoneyNetVolume"))
    joint_inflow = bool(profile.get("jointSmartMoneyInflow"))
    joint_outflow = bool(profile.get("jointSmartMoneyOutflow"))
    return {
        "field": "jointSmartMoneyInflow" if joint_inflow else "jointSmartMoneyOutflow" if joint_outflow else "mixedSmartMoneyFlow",
        "value": round(smart_money_net, 2),
        "foreignNetVolume": round(foreign_net, 2),
        "institutionNetVolume": round(institution_net, 2),
        "smartMoneyNetVolume": round(smart_money_net, 2),
        "jointSmartMoneyInflow": joint_inflow,
        "jointSmartMoneyOutflow": joint_outflow,
        "direction": "joint_inflow" if joint_inflow else "joint_outflow" if joint_outflow else "mixed",
    }

def investor_flow_psychology_profile(position: Position) -> Dict[str, object]:
    return investor_flow_psychology(position)

def missing_market_microstructure_fields(position: Position) -> List[Dict[str, str]]:
    missing: List[Dict[str, str]] = []
    symbol = str(position.symbol or position.name or "").upper().strip()
    if not expects_kr_microstructure_signals(position.market, position.currency, symbol):
        return missing
    if number(position.trade_strength) == 0:
        missing.append({"field": "tradeStrength", "label": "체결강도"})
    if number(position.buy_volume) == 0 and number(position.sell_volume) == 0:
        missing.extend([
            {"field": "buyVolume", "label": "매수 체결량"},
            {"field": "sellVolume", "label": "매도 체결량"},
        ])
    if number(position.orderbook_bid_volume) == 0 and number(position.orderbook_ask_volume) == 0:
        missing.extend([
            {"field": "orderbookBidVolume", "label": "매수호가 잔량"},
            {"field": "orderbookAskVolume", "label": "매도호가 잔량"},
            {"field": "bidAskImbalance", "label": "호가 불균형"},
        ])
    if (
        number(position.foreign_net_volume) == 0
        and number(position.foreign_net_amount) == 0
        and number(position.foreign_buy_volume) == 0
        and number(position.foreign_sell_volume) == 0
    ):
        missing.append({"field": "foreignNetVolume", "label": "외국인 순매수"})
    if (
        number(position.institution_net_volume) == 0
        and number(position.institution_net_amount) == 0
        and number(position.institution_buy_volume) == 0
        and number(position.institution_sell_volume) == 0
    ):
        missing.append({"field": "institutionNetVolume", "label": "기관 순매수"})
    if (
        number(position.individual_net_volume) == 0
        and number(position.individual_net_amount) == 0
        and number(position.individual_buy_volume) == 0
        and number(position.individual_sell_volume) == 0
    ):
        missing.append({"field": "individualNetVolume", "label": "개인 순매수"})
    return missing

def quote_staleness_reason(position: Position) -> str:
    text = " ".join([
        str(position.data_quality or ""),
        str(position.quote_status or ""),
        str(position.quote_message or ""),
    ]).lower()
    for token in ["stale", "cached", "fallback", "expired", "timeout", "old", "지연", "캐시"]:
        if token in text:
            return token
    return ""


def market_signal_stage(position: Position, stage: str) -> Dict[str, object]:
    coverage = position.market_signal_coverage if isinstance(position.market_signal_coverage, dict) else {}
    item = coverage.get(stage) if isinstance(coverage.get(stage), dict) else {}
    return dict(item or {})


def add_market_signal_latency_concepts(graph: PortfolioOntology, stock_id: str, position: Position, source: str) -> None:
    symbol = symbol_key(position)
    investor = market_signal_stage(position, "investor")
    if not investor:
        return
    status = str(investor.get("status") or "").strip()
    latency_status = str(investor.get("latencyStatus") or "").strip()
    if not (
        investor.get("realTime") is False
        or investor.get("aiUsableAsStrongEvidence") is False
        or latency_status
        or status in {"stale", "unknown"}
    ):
        return
    reason = str(
        investor.get("latencyReason")
        or investor.get("staleReason")
        or investor.get("reason")
        or "투자자별 수급은 장중 누적·지연 가능 데이터입니다."
    ).strip()
    latency_id = add_entity(graph, "data-latency", symbol + ":investor-flow", (position.name or symbol) + " 투자자 수급 지연 특성", {
        "tboxClass": "DataLatency",
        "tboxClasses": ["Observation", "DataQuality", "DataFreshness", "DataLatency", "DataQualitySignal"],
        "symbol": symbol,
        "stage": "investor",
        "status": status,
        "realTime": bool(investor.get("realTime")) if "realTime" in investor else None,
        "transport": str(investor.get("transport") or ""),
        "freshnessStatus": str(investor.get("freshnessStatus") or ""),
        "sourceAsOfConfidence": str(investor.get("sourceAsOfConfidence") or ""),
        "aiUsableAsStrongEvidence": bool(investor.get("aiUsableAsStrongEvidence")) if "aiUsableAsStrongEvidence" in investor else None,
        "cadence": str(investor.get("cadence") or ""),
        "latencyStatus": latency_status,
        "latencyLabel": str(investor.get("latencyLabel") or "투자자별 수급 지연 가능"),
        "reason": reason,
        "sourceFetchedAt": str(investor.get("fetchedAt") or ""),
        "sourceAsOf": str(investor.get("sourceAsOf") or ""),
        "unchangedCount": number(investor.get("unchangedCount")),
        "source": source,
    })
    properties = {
        "source": source,
        "polarity": "counter",
        "opinionImpact": 3.0 if status not in {"stale", "unknown"} else 8.0,
        "aiInfluenceLabel": str(investor.get("latencyLabel") or "투자자별 수급 지연 가능"),
        "dataScope": "market-microstructure",
        "scope": "investor-flow",
    }
    add_relation(graph, stock_id, latency_id, "HAS_DATA_QUALITY", weight=0.76, properties=properties)
    add_relation(graph, stock_id, latency_id, "HAS_DATA_FRESHNESS", weight=0.76, properties=properties)
    add_relation(graph, latency_id, stock_id, "WEIGHTED_BY_CONFIDENCE", weight=0.76, properties={**properties, "confidenceImpact": "decrease"})


def add_metric_concepts(graph: PortfolioOntology, stock_id: str, position: Position, source: str) -> None:
    symbol = symbol_key(position)
    for field_name, label, tbox_class, relation_type, kind, public_key in METRIC_CONCEPTS:
        value = metric_value(position, field_name)
        if value in (None, "", 0):
            continue
        metric_id = add_entity(graph, kind + "-metric", symbol + ":" + public_key, label, {
            "tboxClass": tbox_class,
            "tboxClasses": metric_tbox_classes(tbox_class, field_name),
            "field": public_key,
            "value": round(value, 4),
            "source": source,
        })
        properties = metric_relation_properties(field_name, value, source)
        add_relation(
            graph,
            stock_id,
            metric_id,
            "HAS_OBSERVATION",
            weight=1.0,
            properties={**properties, "observationField": public_key},
        )
        add_relation(
            graph,
            stock_id,
            metric_id,
            relation_type,
            weight=1.0,
            properties=properties,
        )
    trend_dynamic = trend_dynamic_facts(position)
    scenario_id = add_entity(graph, "trend-scenario", symbol, str(trend_dynamic.get("state") or "추세 시나리오"), {
        "tboxClass": "TrendSignal",
        "tboxClasses": ["Observation", "TechnicalObservation", "TrendSignal", "Scenario"],
        "source": source,
        **trend_dynamic,
    })
    trend_properties = {
        "source": source,
        "polarity": str(trend_dynamic.get("polarity") or "context"),
        "opinionImpact": number(trend_dynamic.get("opinionImpact")),
        "aiInfluenceLabel": "추세 동역학: " + str(trend_dynamic.get("state") or "중립 추세"),
    }
    add_relation(graph, stock_id, scenario_id, "HAS_OBSERVATION", weight=1.0, properties=trend_properties)
    add_relation(graph, stock_id, scenario_id, "HAS_TECHNICAL_INDICATOR", weight=1.0, properties=trend_properties)
    quality = data_quality_score(position)
    quality_id = add_entity(graph, "data-quality", symbol, "데이터 품질", {
        "tboxClass": "DataQuality",
        "tboxClasses": metric_tbox_classes("DataQuality", "dataQuality"),
        "qualityScore": round(quality, 2),
        "dataQuality": position.data_quality,
        "quoteStatus": position.quote_status,
    })
    quality_properties = {"field": "dataQuality", "source": source, "aiInfluenceLabel": "데이터 품질"}
    if quality < 60:
        quality_properties.update({"polarity": "risk", "opinionImpact": round((60 - quality) * 0.2, 2)})
    add_relation(graph, stock_id, quality_id, "HAS_OBSERVATION", weight=round(quality / 100, 4), properties=quality_properties)
    add_relation(graph, stock_id, quality_id, "HAS_DATA_QUALITY", weight=round(quality / 100, 4), properties=quality_properties)
    add_market_signal_latency_concepts(graph, stock_id, position, source)

def add_data_source_concept(graph: PortfolioOntology, stock_id: str, position: Position, source: str) -> None:
    label = str(position.quote_source or position.data_quality or source or "runtime-data")
    quality = data_quality_score(position)
    source_id = add_entity(graph, "data-source", label, label, {
        "tboxClass": "DataSource",
        "tboxClasses": ["DataSource", "Provenance"],
        "quoteStatus": position.quote_status,
        "quoteMessage": position.quote_message,
        "dataQuality": position.data_quality,
    })
    add_relation(graph, stock_id, source_id, "OBSERVED_FROM", weight=1.0, properties={"source": source, "basis": "quote-source"})
    add_relation(graph, stock_id, source_id, "HAS_PROVENANCE", weight=1.0, properties={"source": source, "basis": "quote-source"})
    reliability_id = add_entity(graph, "source-reliability", label, label + " 신뢰도", {
        "tboxClass": "SourceReliability",
        "tboxClasses": ["Provenance", "SourceReliability", "DataQuality"],
        "qualityScore": round(quality, 2),
        "quoteStatus": position.quote_status,
        "quoteMessage": position.quote_message,
    })
    props = {"source": source, "aiInfluenceLabel": label + " 신뢰도", "confidence": round(quality / 100, 3)}
    if quality < 60:
        props.update({"polarity": "risk", "opinionImpact": round((60 - quality) * 0.18, 2)})
    add_relation(graph, source_id, reliability_id, "HAS_SOURCE_RELIABILITY", weight=round(quality / 100, 4), properties=props)
    add_relation(graph, stock_id, reliability_id, "WEIGHTED_BY_CONFIDENCE", weight=round(quality / 100, 4), properties=props)

def add_legacy_model_score_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, legacy: Dict[str, object]) -> None:
    score_rows = [
        ("exitPressure", "관계 규칙 강도", number(legacy.get("exitPressure") or legacy.get("exit_pressure")), "risk"),
        ("profitTakePressure", "익절 관계 강도", number(legacy.get("profitTakePressure") or legacy.get("profit_take_pressure")), "risk"),
        ("lossCutPressure", "손실 관리 관계 강도", number(legacy.get("lossCutPressure") or legacy.get("loss_cut_pressure")), "risk"),
        ("buyScore", "매수 관계 점수", number(legacy.get("buyScore") or legacy.get("modelBuyScore")), "support"),
        ("sellScore", "매도 관계 점수", number(legacy.get("sellScore") or legacy.get("modelSellScore")), "risk"),
    ]
    for key, label, value, polarity in score_rows:
        if not value:
            continue
        score_id = add_entity(graph, "model-score", symbol + ":" + key, label, {
            "tboxClass": "ModelScore",
            "tboxClasses": ["Signal", "StrategySignal", "ModelScore"],
            "field": key,
            "value": round(value, 2),
            "modelRole": "relation-rule-score",
        })
        properties = {"field": key, "polarity": polarity, "aiInfluenceLabel": label, "source": "relation-rule"}
        if polarity == "risk" and value >= 55:
            properties["opinionImpact"] = min(18.0, (value - 45) * 0.35)
        if polarity == "support" and value >= 55:
            properties["supportImpact"] = min(14.0, (value - 45) * 0.35)
        add_relation(graph, stock_id, score_id, "HAS_MODEL_SCORE", weight=round(value / 100, 4), properties=properties)
        add_relation(graph, score_id, stock_id, "USED_AS_EVIDENCE", weight=round(value / 100, 4), properties={**properties, "source": "relation-rule"})

def add_price_level_and_liquidity_concepts(graph: PortfolioOntology, stock_id: str, position: Position, source: str) -> None:
    symbol = symbol_key(position)
    current_price = number(position.current_price)
    bar_id = ""
    if current_price:
        bar_id = add_entity(graph, "price-bar", symbol + ":latest", (position.name or symbol) + " 현재 가격 봉", {
            "tboxClass": "PriceBar",
            "tboxClasses": ["Observation", "PriceObservation", "PriceBar"],
            "symbol": symbol,
            "close": round(current_price, 4),
            "changeRate": round(number(position.change_rate), 2),
            "volume": round(number(position.volume), 2),
            "volumeRatio": round(number(position.volume_ratio), 3),
            "observedAt": position.updated_at,
        })
        add_relation(graph, stock_id, bar_id, "HAS_PRICE", weight=1.0, properties={"source": source, "aiInfluenceLabel": "현재 가격 봉"})
        add_relation(graph, stock_id, bar_id, "HAS_OBSERVATION", weight=1.0, properties={"source": source, "aiInfluenceLabel": "현재 가격 봉"})
        if position.updated_at:
            validity_id = add_entity(graph, "validity-interval", symbol + ":quote", (position.name or symbol) + " 시세 유효 구간", {
                "tboxClass": "ValidityInterval",
                "tboxClasses": ["Observation", "DataQuality", "ValidityInterval", "DataFreshness"],
                "symbol": symbol,
                "validFrom": position.updated_at,
                "observedAt": position.updated_at,
                "source": source,
            })
            add_relation(graph, bar_id, validity_id, "VALID_DURING", weight=1.0, properties={"source": source, "aiInfluenceLabel": "시세 유효 구간"})
            add_relation(graph, stock_id, validity_id, "HAS_DATA_QUALITY", weight=1.0, properties={"source": source, "aiInfluenceLabel": "시세 유효 구간"})
    flow = volume_profile(position)
    if any(number(value) for value in flow.values()):
        volume_id = add_entity(graph, "volume-profile", symbol, (position.name or symbol) + " 거래량 프로파일", {
            "tboxClass": "VolumeProfile",
            "tboxClasses": ["Observation", "VolumeObservation", "FlowObservation", "VolumeProfile", "TradeFlow", "FlowSignal"],
            "symbol": symbol,
            "source": source,
            **flow,
        })
        flow_props = {"source": source, "aiInfluenceLabel": "거래량/체결/호가 프로파일", "polarity": "context"}
        if number(flow.get("volumeRatio")) >= 1.5 or abs(number(flow.get("bidAskImbalance"))) >= 35:
            flow_props["opinionImpact"] = min(12.0, number(flow.get("volumeRatio")) * 3 + abs(number(flow.get("bidAskImbalance"))) * 0.08)
        add_relation(graph, stock_id, volume_id, "HAS_OBSERVATION", weight=1.0, properties=flow_props)
        add_relation(graph, stock_id, volume_id, "HAS_TRADE_FLOW", weight=1.0, properties=flow_props)
    smart_money_flow = smart_money_joint_flow_profile(position)
    if smart_money_flow.get("jointSmartMoneyInflow") or smart_money_flow.get("jointSmartMoneyOutflow"):
        polarity = "support" if smart_money_flow.get("jointSmartMoneyInflow") else "risk"
        field_name = str(smart_money_flow.get("field") or "")
        smart_money_id = add_entity(graph, "smart-money-flow", symbol + ":" + field_name, (position.name or symbol) + " 외국인·기관 동반 " + ("순매수" if polarity == "support" else "순매도"), {
            "tboxClass": "SmartMoneyJointInflow" if polarity == "support" else "SmartMoneyJointOutflow",
            "tboxClasses": ["Observation", "FlowObservation", "TradeFlow", "FlowSignal", "SmartMoneyFlow", "SmartMoneyJointInflow" if polarity == "support" else "SmartMoneyJointOutflow"],
            "symbol": symbol,
            "source": source,
            "polarity": polarity,
            **smart_money_flow,
        })
        smart_money_props = {
            "source": source,
            "field": field_name,
            "signalGroup": "smartMoney",
            "polarity": polarity,
            "supportImpact": 7.0 if polarity == "support" else 0.0,
            "riskImpact": 7.0 if polarity == "risk" else 0.0,
            "aiInfluenceLabel": "외국인·기관 동반 " + ("순매수" if polarity == "support" else "순매도"),
        }
        add_relation(graph, stock_id, smart_money_id, "HAS_OBSERVATION", weight=0.86, properties=smart_money_props)
        add_relation(graph, stock_id, smart_money_id, "HAS_TRADE_FLOW", weight=0.86, properties=smart_money_props)
    investor_psychology = investor_flow_psychology_profile(position)
    if investor_psychology.get("available"):
        polarity = str(investor_psychology.get("polarity") or "context")
        field_name = str(investor_psychology.get("field") or "mixedInvestorPsychology")
        sentiment_label = str(investor_psychology.get("sentimentLabel") or "투자자별 수급 심리")
        sentiment_id = add_entity(graph, "investor-flow-sentiment", symbol + ":" + field_name, (position.name or symbol) + " " + sentiment_label, {
            "tboxClass": str(investor_psychology.get("tboxClass") or "InvestorFlowSentiment"),
            "tboxClasses": list(investor_psychology.get("tboxClasses") or ["Observation", "FlowObservation", "InvestorFlowSentiment"]),
            "symbol": symbol,
            "source": source,
            "polarity": polarity,
            **investor_psychology,
        })
        sentiment_props = {
            "source": source,
            "field": field_name,
            "signalGroup": "investorPsychology",
            "polarity": polarity,
            "supportImpact": number(investor_psychology.get("supportImpact")),
            "riskImpact": number(investor_psychology.get("riskImpact")),
            "aiInfluenceLabel": sentiment_label,
        }
        weight = number(investor_psychology.get("weight")) or 0.7
        add_relation(graph, stock_id, sentiment_id, "HAS_OBSERVATION", weight=weight, properties=sentiment_props)
        add_relation(graph, stock_id, sentiment_id, "HAS_INVESTOR_FLOW_SENTIMENT", weight=weight, properties=sentiment_props)
    missing_fields = missing_market_microstructure_fields(position)
    if missing_fields:
        missing_id = add_entity(graph, "missing-data", symbol + ":market-microstructure", (position.name or symbol) + " 부족 데이터", {
            "tboxClass": "MissingData",
            "tboxClasses": ["Observation", "DataQuality", "MissingData", "DataQualitySignal"],
            "symbol": symbol,
            "source": source,
            "missingFields": [item["field"] for item in missing_fields],
            "missingLabels": [item["label"] for item in missing_fields],
            "missingCount": len(missing_fields),
            "scope": "market-microstructure",
            "dataScope": "market-microstructure",
        })
        missing_props = {
            "source": source,
            "polarity": "risk",
            "opinionImpact": min(10.0, len(missing_fields) * 0.8),
            "aiInfluenceLabel": "체결/호가/투자자별 수급 결측",
            "dataScope": "market-microstructure",
            "scope": "market-microstructure",
        }
        add_relation(graph, stock_id, missing_id, "HAS_DATA_QUALITY", weight=round(max(0.1, 1 - len(missing_fields) / 10), 4), properties=missing_props)
        for item in missing_fields:
            field = str(item.get("field") or "").strip()
            label = str(item.get("label") or field or "부족 데이터").strip()
            if not field:
                continue
            field_id = add_entity(graph, "missing-data", symbol + ":market-microstructure:" + field, (position.name or symbol) + " " + label + " 결측", {
                "tboxClass": "MissingData",
                "tboxClasses": ["Observation", "DataQuality", "MissingData", "DataQualitySignal"],
                "symbol": symbol,
                "source": source,
                "field": field,
                "label": label,
                "missingFields": [field],
                "missingLabels": [label],
                "missingCount": 1,
                "scope": "market-microstructure",
                "dataScope": "market-microstructure",
            })
            field_props = {
                "source": source,
                "polarity": "context",
                "field": field,
                "missingField": field,
                "aiInfluenceLabel": label + " 결측",
                "dataScope": "market-microstructure",
                "scope": "market-microstructure",
            }
            add_relation(graph, stock_id, field_id, "HAS_DATA_QUALITY", weight=0.42, properties=field_props)
            add_relation(graph, missing_id, field_id, "AFFECTS", weight=0.7, properties=field_props)
        risk_id = add_entity(graph, "risk", symbol + ":data-quality-risk", (position.name or symbol) + " 데이터 품질 리스크", {
            "tboxClass": "DataQualityRisk",
            "tboxClasses": ["Risk", "DataQualityRisk"],
            "symbol": symbol,
            "missingFields": [item["field"] for item in missing_fields],
            "riskScore": min(100, len(missing_fields) * 8),
        })
        add_relation(graph, stock_id, risk_id, "EXPOSED_TO", weight=round(min(1.0, len(missing_fields) / 10), 4), properties=missing_props)
        add_relation(graph, missing_id, risk_id, "AFFECTS", weight=round(min(1.0, len(missing_fields) / 10), 4), properties=missing_props)
    stale_reason = quote_staleness_reason(position)
    if stale_reason:
        stale_id = add_entity(graph, "staleness", symbol + ":quote", (position.name or symbol) + " 시세 노후화", {
            "tboxClass": "Staleness",
            "tboxClasses": ["Observation", "DataQuality", "DataFreshness", "Staleness", "DataQualitySignal"],
            "symbol": symbol,
            "reason": stale_reason,
            "dataQuality": position.data_quality,
            "quoteStatus": position.quote_status,
            "quoteMessage": position.quote_message,
            "observedAt": position.updated_at,
        })
        add_relation(graph, stock_id, stale_id, "HAS_DATA_QUALITY", weight=0.35, properties={"source": source, "polarity": "risk", "opinionImpact": 6.0, "aiInfluenceLabel": "시세 신선도 저하"})
    def technical_distance(level: float, explicit_distance: float) -> float:
        calculated = pct_distance_safe(current_price, level) if current_price and level else 0.0
        return calculated if calculated else number(explicit_distance)

    ma5_distance = technical_distance(number(position.ma5), getattr(position, "ma5_distance", 0.0))
    ma20_distance = technical_distance(number(position.ma20), position.ma20_distance)
    ma60_distance = technical_distance(number(position.ma60), position.ma60_distance)
    level_rows = [
        ("ma5", "5일선", number(position.ma5), ma5_distance, "SupportLevel" if ma5_distance >= -0.5 else "ResistanceLevel"),
        ("ma20", "20일선", number(position.ma20), ma20_distance, "SupportLevel" if ma20_distance >= -1 else "ResistanceLevel"),
        ("ma60", "60일선", number(position.ma60), ma60_distance, "SupportLevel" if ma60_distance >= -1 else "ResistanceLevel"),
        ("average", "평단가", number(position.average_price), pct_distance_safe(current_price, number(position.average_price)), "KeyLevel"),
    ]
    for key, label, level, distance, tbox_class in level_rows:
        if not level:
            continue
        level_id = add_entity(graph, "key-level", symbol + ":" + key, label + " " + compact_price(level), {
            "tboxClass": tbox_class,
            "tboxClasses": ["Observation", "TechnicalObservation", "KeyLevel", tbox_class],
            "symbol": symbol,
            "levelType": key,
            "price": round(level, 4),
            "distancePct": round(distance, 2),
        })
        add_relation(graph, stock_id, level_id, "HAS_TECHNICAL_INDICATOR", weight=1.0, properties={"source": source, "aiInfluenceLabel": label + " 위치"})
        if -1.0 <= distance <= 1.5:
            add_relation(graph, stock_id, level_id, "RETESTS_LEVEL", weight=0.82, properties={"source": source, "polarity": "context", "aiInfluenceLabel": label + " 재시험"})
        elif distance <= -5.0:
            add_relation(graph, stock_id, level_id, "BREAKS_LEVEL", weight=0.88, properties={"source": source, "polarity": "risk", "opinionImpact": min(14.0, abs(distance)), "aiInfluenceLabel": label + " 이탈"})
        elif distance >= 0 and number(position.change_rate) > 0:
            add_relation(graph, stock_id, level_id, "RECLAIMS_LEVEL", weight=0.74, properties={"source": source, "polarity": "support", "supportImpact": min(8.0, distance + 2), "aiInfluenceLabel": label + " 회복"})
    liquidity = liquidity_profile(position)
    liquidity_id = add_entity(graph, "liquidity-profile", symbol, (position.name or symbol) + " 유동성 프로파일", {
        "tboxClass": "LiquidityProfile",
        "tboxClasses": ["Risk", "LiquidityRisk", "LiquidityProfile"],
        **liquidity,
    })
    add_relation(
        graph,
        stock_id,
        liquidity_id,
        "HAS_LIQUIDITY_PROFILE",
        weight=1.0,
        properties={"source": source, "polarity": "context", "aiInfluenceLabel": "유동성 원천 프로파일"},
    )
    add_execution_metric_concepts(graph, stock_id, symbol, position.name or symbol, liquidity, source)
    risk_props = {"source": source, "aiInfluenceLabel": "유동성/실행 가능성", "polarity": "context"}
    liquidity_risk = number(liquidity.get("liquidityRiskScore"))
    slippage_risk = number(liquidity.get("slippageRiskScore"))
    if liquidity_risk >= 55:
        add_relation(
            graph,
            stock_id,
            liquidity_id,
            "LIMITED_BY_LIQUIDITY",
            weight=round(liquidity_risk / 100, 4),
            properties={
                **risk_props,
                "polarity": "risk",
                "opinionImpact": min(16.0, liquidity_risk * 0.18),
            },
        )
    capacity_id = add_entity(graph, "exit-capacity", symbol, (position.name or symbol) + " 청산 가능 용량", {
        "tboxClass": "ExitCapacity",
        "tboxClasses": ["Risk", "LiquidityRisk", "ExitCapacity", "ExecutionCapacity"],
        "sellableQuantity": round(number(position.sellable_quantity), 4),
        "positionValue": round(number(position.market_value), 2),
        "tradingValue": round(number(position.trading_value), 2),
        "positionToTradingValuePct": liquidity.get("positionToTradingValuePct"),
        "positionToDailyVolumePct": liquidity.get("positionToDailyVolumePct"),
        "positionToBidDepthPct": liquidity.get("positionToBidDepthPct"),
        "bidDepthCoveragePct": liquidity.get("bidDepthCoveragePct"),
        "sellableRatioPct": liquidity.get("sellableRatioPct"),
        "sellableBlocked": liquidity.get("sellableBlocked"),
        "exitDaysAtTenPctADV": liquidity.get("exitDaysAtTenPctADV"),
    })
    add_relation(graph, stock_id, capacity_id, "HAS_EXIT_CAPACITY", weight=1.0, properties={"source": source, "aiInfluenceLabel": "청산 가능 용량"})
    add_relation(graph, stock_id, capacity_id, "HAS_EXECUTION_CAPACITY", weight=1.0, properties={"source": source, "polarity": "context", "aiInfluenceLabel": "실행 가능 용량"})
    slippage_id = add_entity(graph, "slippage-estimate", symbol, (position.name or symbol) + " 슬리피지 추정", {
        "tboxClass": "SlippageEstimate",
        "tboxClasses": ["Risk", "ExecutionRisk", "SlippageEstimate"],
        "slippageRiskScore": liquidity.get("slippageRiskScore"),
        "bidAskImbalance": round(number(position.bid_ask_imbalance), 2),
        "volumeRatio": round(number(position.volume_ratio), 3),
        "positionToBidDepthPct": liquidity.get("positionToBidDepthPct"),
    })
    if slippage_risk >= 55:
        add_relation(
            graph,
            stock_id,
            slippage_id,
            "HAS_SLIPPAGE_RISK",
            weight=round(slippage_risk / 100, 4),
            properties={
                **risk_props,
                "polarity": "risk",
                "opinionImpact": min(16.0, slippage_risk * 0.18),
            },
        )
