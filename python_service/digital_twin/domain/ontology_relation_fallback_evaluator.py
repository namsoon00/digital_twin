from typing import Dict, List, Optional

from .market_data import number
from .ontology_relation_contracts import (
    ONTOLOGY_RULE_ENGINE_VERSION,
    OntologyRuleMatch,
)
from .ontology_relation_execution_plan import execution_plan_from_relation_context
from .ontology_relation_facts import (
    _bp_text,
    _has_numeric_fact,
    _number_text,
    moving_average_distance_text,
    position_signal_facts,
)
from .ontology_relation_matches import _match, decision_from_matches
from .ontology_relation_prompt_context import build_ai_prompt_context
from .ontology_relation_settings import _thresholds, relation_rule_definitions_from_settings
from .ontology_relation_decisions import strength_label
from .portfolio import PortfolioSummary, Position


def evaluate_position_relation_rules(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Optional[Dict[str, object]] = None,
    settings: Optional[Dict[str, object]] = None,
    legacy_model: Optional[Dict[str, object]] = None,
    prompt_id: str = "holdingTiming",
    previous_state: Optional[Dict[str, object]] = None,
    previous_decision: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    settings = settings or {}
    relation_definitions = relation_rule_definitions_from_settings(settings)
    thresholds = _thresholds(settings)
    facts = position_signal_facts(
        position,
        portfolio,
        external_signals,
        previous_state,
        previous_decision,
        settings=settings,
    )
    missing_labels = [str(item.get("label") or item.get("key") or "") for item in facts.get("missingData") or []]
    matches: List[OntologyRuleMatch] = []
    data_quality = float(facts.get("dataQualityScore") or 0)
    pnl = float(facts.get("profitLossRate") or 0)
    ma5_distance = float(facts.get("ma5Distance") or 0)
    ma20_distance = float(facts.get("ma20Distance") or 0)
    ma60_distance = float(facts.get("ma60Distance") or 0)
    sector_ratio = float(facts.get("sectorRatio") or 0)
    position_weight = float(facts.get("positionWeight") or 0)
    trend_score = float(facts.get("trendScore") or 0)
    flow_score = float(facts.get("investorFlowScore") or 0)
    btc_change24h = float(facts.get("btcChange24h") or 0)
    btc_change7d = float(facts.get("btcChange7d") or 0)
    source = str(facts.get("source") or "holding").strip()
    is_holding = bool(facts.get("isHolding"))
    volume_ratio = float(facts.get("volumeRatio") or 0)
    trade_strength = float(facts.get("tradeStrength") or 0)
    bid_ask_imbalance = float(facts.get("bidAskImbalance") or 0)
    ma20_slope = float(facts.get("ma20Slope") or 0)
    ma60_slope = float(facts.get("ma60Slope") or 0)
    price_change = float(facts.get("priceChangeRate") or 0)
    trend_curve = float(facts.get("trendCurve") or 0)
    trend_dynamic_risk = float(facts.get("trendDynamicRiskScore") or 0)
    support_retest = bool(facts.get("supportRetest"))
    recovery_attempt = bool(facts.get("recoveryAttempt"))
    breakdown_acceleration = bool(facts.get("breakdownAcceleration"))
    joint_smart_money_inflow = bool(facts.get("jointSmartMoneyInflow"))
    loss_recovery_signal_count = int(number(facts.get("lossRecoverySignalCount")))
    add_buy_stage = str(facts.get("addBuyEligibilityStage") or "")
    disclosure = facts.get("dartDisclosure")
    has_disclosure = isinstance(disclosure, dict) and bool(disclosure)
    news = facts.get("newsHeadlines")
    direct_news_count = int(number(facts.get("directNewsCount")))
    direct_risk_news_count = int(number(facts.get("directRiskNewsCount")))
    direct_support_news_count = int(number(facts.get("directSupportNewsCount")))
    peer_news_count = int(number(facts.get("peerNewsCount")))
    sector_news_count = int(number(facts.get("sectorNewsCount")))
    market_news_count = int(number(facts.get("marketNewsCount")))
    material_news_count = int(number(facts.get("materialNewsCount")))
    has_news = bool(
        (isinstance(news, dict) and bool(news.get("items") or news.get("count")))
        or material_news_count
        or direct_news_count
    )
    previous_ma20_distance = float(facts.get("previousMa20Distance") or 0)
    previous_ma60_distance = float(facts.get("previousMa60Distance") or 0)
    has_previous_state = bool(facts.get("hasPreviousState"))
    price_delta_previous = float(facts.get("priceDeltaFromPreviousPct") or 0)
    ma20_delta_previous = float(facts.get("ma20DistanceDeltaPct") or 0)
    ma60_delta_previous = float(facts.get("ma60DistanceDeltaPct") or 0)
    position_to_trading_value = float(facts.get("positionToTradingValuePct") or 0)
    liquidity_risk = float(facts.get("liquidityRiskScore") or 0)
    external_quality = float(facts.get("externalSignalQualityScore") or 0)
    external_errors = float(facts.get("externalSignalErrorCount") or 0)
    macro_spread = float(facts.get("macroYieldSpread10y2y") or 0)
    macro_dgs10 = float(facts.get("macroDgs10") or 0)
    macro_dgs2 = float(facts.get("macroDgs2") or 0)
    macro_dgs10_delta_bp = float(facts.get("macroDgs10DeltaBp") or 0)
    macro_dgs2_delta_bp = float(facts.get("macroDgs2DeltaBp") or 0)
    macro_dff_delta_bp = float(facts.get("macroDffDeltaBp") or 0)
    macro_spread_delta_bp = float(facts.get("macroYieldSpreadDeltaBp") or 0)
    currency = str(facts.get("currency") or "").upper().strip()
    usd_krw_rate = float(facts.get("usdKrwRate") or 0)
    usd_krw_delta_krw = float(facts.get("usdKrwDeltaKrw") or 0)
    usd_krw_delta_pct = float(facts.get("usdKrwDeltaPct") or 0)
    usd_krw_7d_delta_krw = float(facts.get("usdKrw7dDeltaKrw") or 0)
    usd_krw_7d_delta_pct = float(facts.get("usdKrw7dDeltaPct") or 0)
    fx_rate_to_krw = float(facts.get("fxRateToKrw") or 0)
    fx_exposure_ratio = float(facts.get("fxExposureRatio") or 0)
    has_rate_signals = bool(facts.get("hasInterestRateSignals"))
    has_rate_delta_signal = bool(facts.get("hasInterestRateDeltaSignal"))
    has_fx_rate_signal = bool(facts.get("hasFxRateSignal"))
    has_fx_delta_signal = bool(facts.get("hasFxDeltaSignal"))
    rate_high_threshold = float(thresholds.get("macroRateHighPct", 4.5) or 4.5)
    rate_low_threshold = float(thresholds.get("macroRateLowPct", 3.0) or 3.0)
    curve_inversion_threshold = float(thresholds.get("macroCurveInversionPct", 0.0) or 0.0)
    rate_delta_threshold = float(
        thresholds.get("macroRateDeltaBp", thresholds.get("externalMacroRateDeltaBp", 15.0)) or 0.0
    )
    usd_krw_high = float(thresholds.get("usdKrwHigh", 1450.0) or 1450.0)
    usd_krw_low = float(thresholds.get("usdKrwLow", 1300.0) or 1300.0)
    usd_krw_delta_krw_threshold = float(thresholds.get("usdKrwDeltaKrw", 15.0) or 0.0)
    usd_krw_delta_pct_threshold = float(thresholds.get("usdKrwDeltaPct", 1.0) or 0.0)
    usd_krw_7d_delta_krw_threshold = float(thresholds.get("usdKrw7dDeltaKrw", 30.0) or 0.0)
    usd_krw_7d_delta_pct_threshold = float(thresholds.get("usdKrw7dDeltaPct", 2.0) or 0.0)
    fx_exposure_review = float(thresholds.get("fxExposureReview", 5.0) or 5.0)
    fx_exposure_high = float(thresholds.get("fxExposureHigh", 10.0) or 10.0)
    macro_sensitive = any(token in str(facts.get("sector") or "") for token in ["반도체", "AI", "플랫폼", "디지털자산"]) or currency == "USD" or facts.get("isBtcSensitive")
    rate_sensitive = macro_sensitive or any(token in str(facts.get("sector") or "") for token in ["성장", "소프트웨어", "테크", "바이오"])
    high_rate_active = bool(has_rate_signals and macro_dgs10 and macro_dgs10 >= rate_high_threshold)
    low_rate_active = bool(has_rate_signals and macro_dgs10 and macro_dgs10 <= rate_low_threshold)
    inverted_curve_active = bool(has_rate_signals and macro_spread < curve_inversion_threshold)
    rate_delta_magnitude = max(
        abs(macro_dgs10_delta_bp),
        abs(macro_dgs2_delta_bp),
        abs(macro_dff_delta_bp),
        abs(macro_spread_delta_bp),
    )
    rate_delta_active = bool(
        has_rate_delta_signal
        and (not rate_delta_threshold or rate_delta_magnitude >= rate_delta_threshold)
    )
    fx_extreme_regime = bool(
        currency == "USD"
        and usd_krw_rate
        and (usd_krw_rate >= usd_krw_high or usd_krw_rate <= usd_krw_low)
    )
    fx_delta_active = bool(
        has_fx_delta_signal
        and (
            (not usd_krw_delta_krw_threshold or abs(usd_krw_delta_krw) >= usd_krw_delta_krw_threshold)
            or (not usd_krw_delta_pct_threshold or abs(usd_krw_delta_pct) >= usd_krw_delta_pct_threshold)
            or (not usd_krw_7d_delta_krw_threshold or abs(usd_krw_7d_delta_krw) >= usd_krw_7d_delta_krw_threshold)
            or (not usd_krw_7d_delta_pct_threshold or abs(usd_krw_7d_delta_pct) >= usd_krw_7d_delta_pct_threshold)
        )
    )
    fx_extreme_active = bool(fx_delta_active and fx_extreme_regime)
    fx_exposure_active = bool(fx_delta_active and has_fx_rate_signal and currency != "KRW" and fx_exposure_ratio >= fx_exposure_high)
    entry_macro_blocked = bool(rate_sensitive and (high_rate_active or inverted_curve_active or rate_delta_active))
    entry_fx_blocked = bool(currency == "USD" and has_fx_rate_signal and (fx_extreme_regime or fx_delta_active or fx_exposure_active))
    entry_macro_missing = bool(rate_sensitive and not has_rate_signals)
    entry_fx_missing = bool(currency == "USD" and not has_fx_rate_signal)
    facts["entryMacroBlocked"] = entry_macro_blocked
    facts["entryFxBlocked"] = entry_fx_blocked
    facts["entryMacroMissing"] = entry_macro_missing
    facts["entryFxMissing"] = entry_fx_missing

    if pnl >= 10 and (ma20_distance <= -2 or ma60_distance <= -5 or trend_score < -3):
        score = 55 + min(25, max(0, pnl - 10) * 1.2) + min(20, abs(min(ma20_distance, ma60_distance, trend_score)))
        matches.append(_match(
            "holding.profit_take.trend_weakness.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    news_relevance = float(facts.get("averageNewsRelevanceScore") or 0)
    news_reliability = float(facts.get("averageNewsSourceReliability") or 0)
    news_materiality = float(facts.get("averageNewsMaterialityScore") or 0)
    top_news_event_types = [str(item) for item in list(facts.get("topNewsEventTypes") or [])[:3] if str(item or "").strip()]
    news_confidence = min(data_quality, 55 + min(40, news_reliability * 45)) if news_reliability else data_quality
    news_fresh_max_age = float(thresholds.get("newsDirectFreshMaxAgeMinutes", 1440) or 1440)
    news_relevance_min = float(thresholds.get("newsDirectRelevanceMin", 75) or 75)
    news_materiality_min = float(thresholds.get("newsDirectMaterialityMin", 60) or 60)
    latest_direct_news_age = float(facts.get("latestDirectNewsAgeMinutes") or 0)
    direct_news_fresh = not latest_direct_news_age or latest_direct_news_age <= news_fresh_max_age
    direct_news_material = (
        direct_news_count
        and direct_news_fresh
        and news_relevance >= news_relevance_min
        and news_materiality >= news_materiality_min
    )
    if direct_news_material and direct_risk_news_count:
        score = (
            52
            + min(14, direct_risk_news_count * 4)
            + min(14, news_relevance * 0.14)
            + min(12, news_materiality * 0.1)
            + (6 if latest_direct_news_age and latest_direct_news_age <= 180 else 0)
        )
        matches.append(_match(
            "news.direct_risk.new_material.v1",
            score,
            news_confidence,
            [
                "새 직접 부정 뉴스 " + str(direct_risk_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "평균 중요도 " + ("%.1f" % news_materiality) + "점",
                "최신 직접 뉴스 " + ("%.1f" % latest_direct_news_age) + "분 전" if latest_direct_news_age else "최신 직접 뉴스 시각 미확인",
                "가격 확인 전 뉴스 단독 감지",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directRiskNewsTitles") or facts.get("topNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if direct_news_material and direct_support_news_count:
        score = (
            50
            + min(14, direct_support_news_count * 4)
            + min(14, news_relevance * 0.14)
            + min(12, news_materiality * 0.1)
            + (6 if latest_direct_news_age and latest_direct_news_age <= 180 else 0)
        )
        matches.append(_match(
            "news.direct_support.new_material.v1",
            score,
            news_confidence,
            [
                "새 직접 우호 뉴스 " + str(direct_support_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "평균 중요도 " + ("%.1f" % news_materiality) + "점",
                "최신 직접 뉴스 " + ("%.1f" % latest_direct_news_age) + "분 전" if latest_direct_news_age else "최신 직접 뉴스 시각 미확인",
                "가격 확인 전 뉴스 단독 감지",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directSupportNewsTitles") or facts.get("topNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    neutral_direct_news_count = max(0, direct_news_count - direct_risk_news_count - direct_support_news_count)
    if direct_news_material and neutral_direct_news_count:
        score = (
            48
            + min(12, neutral_direct_news_count * 3)
            + min(14, news_relevance * 0.14)
            + min(12, news_materiality * 0.1)
            + (6 if latest_direct_news_age and latest_direct_news_age <= 180 else 0)
        )
        matches.append(_match(
            "news.direct_material.new.v1",
            score,
            news_confidence,
            [
                "새 직접 중요 뉴스 " + str(neutral_direct_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "평균 중요도 " + ("%.1f" % news_materiality) + "점",
                "최신 직접 뉴스 " + ("%.1f" % latest_direct_news_age) + "분 전" if latest_direct_news_age else "최신 직접 뉴스 시각 미확인",
                "가격 확인 전 뉴스 단독 감지",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("topNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    risk_news_confirmation_count = sum(
        1
        for value in [
            price_change <= -1.0,
            ma20_distance < 0,
            volume_ratio >= 1.2,
            trend_score < -4,
            flow_score <= -10 or bid_ask_imbalance <= -10,
        ]
        if value
    )
    if direct_risk_news_count and risk_news_confirmation_count >= 1:
        score = (
            54
            + min(14, direct_risk_news_count * 4)
            + min(12, news_relevance * 0.12)
            + min(8, news_materiality * 0.08)
            + min(12, risk_news_confirmation_count * 4)
            + (6 if price_change <= -2 else 0)
            + (5 if ma20_distance <= -5 else 0)
        )
        matches.append(_match(
            "news.direct_risk.price_confirmed.v1",
            score,
            news_confidence,
            [
                "직접 부정 뉴스 " + str(direct_risk_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "평균 중요도 " + ("%.1f" % news_materiality) + "점" if news_materiality else "",
                "사건 유형 " + " · ".join(top_news_event_types) if top_news_event_types else "",
                "최신 직접 뉴스 " + ("%.1f" % float(facts.get("latestDirectNewsAgeMinutes") or 0)) + "분 전" if facts.get("latestDirectNewsAgeMinutes") else "",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "",
                moving_average_distance_text("20일선", ma20_distance),
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directRiskNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    support_news_confirmation_count = sum(
        1
        for value in [
            price_change >= 1.0,
            volume_ratio >= 1.2,
            trade_strength >= 100,
            ma20_distance >= -2,
            flow_score >= 10 or bid_ask_imbalance >= 5,
        ]
        if value
    )
    if direct_support_news_count and support_news_confirmation_count >= 1:
        score = (
            50
            + min(14, direct_support_news_count * 4)
            + min(12, news_relevance * 0.12)
            + min(8, news_materiality * 0.08)
            + min(12, support_news_confirmation_count * 4)
            + (6 if price_change >= 2 else 0)
        )
        matches.append(_match(
            "news.direct_support.price_confirmed.v1",
            score,
            news_confidence,
            [
                "직접 우호 뉴스 " + str(direct_support_news_count) + "건",
                "평균 관련성 " + ("%.1f" % news_relevance) + "점",
                "평균 중요도 " + ("%.1f" % news_materiality) + "점" if news_materiality else "",
                "사건 유형 " + " · ".join(top_news_event_types) if top_news_event_types else "",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "",
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("directSupportNewsTitles") or [])[:2]),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    sector_context_news_count = peer_news_count + sector_news_count + market_news_count
    sector_context_confirmed = sector_context_news_count > 0 and (
        abs(price_change) >= 1.5 or volume_ratio >= 1.2 or abs(trend_score) >= 4
    )
    if sector_context_news_count:
        score = (
            36
            + min(10, sector_context_news_count * 2.5)
            + min(10, news_relevance * 0.1)
            + min(6, news_materiality * 0.06)
            + (8 if sector_context_confirmed else 0)
        )
        matches.append(_match(
            "news.sector_peer_context.v1",
            score,
            news_confidence,
            [
                "피어 뉴스 " + str(peer_news_count) + "건",
                "섹터 뉴스 " + str(sector_news_count) + "건",
                "시장 뉴스 " + str(market_news_count) + "건",
                "사건 유형 " + " · ".join(top_news_event_types) if top_news_event_types else "",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "뉴스 제목 " + " / ".join(str(item) for item in list(facts.get("sectorNewsTitles") or [])[:2]),
            ],
            missing_labels,
            reference_only=not sector_context_confirmed,
            definitions=relation_definitions,
        ))

    entry_ma20_below = float(thresholds.get("entryPullbackMa20BelowPct", -2.0) or -2.0)
    entry_ma20_deep = float(thresholds.get("entryPullbackMa20DeepPct", -8.0) or -8.0)
    entry_ma5_min = float(thresholds.get("entryMa5TimingMinPct", -0.5) or -0.5)
    entry_momentum_ma20_min = float(thresholds.get("entryMomentumMa20MinPct", -0.5) or -0.5)
    entry_momentum_ma60_min = float(thresholds.get("entryMomentumMa60MinPct", 0.0) or 0.0)
    entry_ma60_support = float(thresholds.get("entryMa60SupportPct", -1.0) or -1.0)
    entry_volume_min = float(thresholds.get("entryVolumeMinRatio", 0.6) or 0.0)
    entry_volume_max = float(thresholds.get("entryVolumeMaxRatio", 1.8) or 0.0)
    entry_smart_money_min = float(thresholds.get("entrySmartMoneyMin", 10.0) or 0.0)
    entry_trade_strength_min = float(thresholds.get("entryTradeStrengthMin", 100.0) or 0.0)
    entry_orderbook_min = float(thresholds.get("entryOrderbookImbalanceMin", 5.0) or 0.0)
    entry_position_max = float(thresholds.get("entryMaxPositionWeight", 20.0) or 0.0)
    entry_sector_max = float(thresholds.get("entryMaxSectorWeight", 45.0) or 0.0)
    pullback_zone = entry_ma20_deep <= ma20_distance <= entry_ma20_below
    ma5_supports_entry = bool(facts.get("ma5")) and ma5_distance >= entry_ma5_min
    ma60_supports_entry = bool(facts.get("ma60")) and ma60_distance >= entry_ma60_support
    ma20_momentum_ready = bool(facts.get("ma20")) and ma20_distance >= entry_momentum_ma20_min
    ma60_momentum_ready = bool(facts.get("ma60")) and ma60_distance >= entry_momentum_ma60_min
    volume_is_usable = bool(volume_ratio) and volume_ratio >= entry_volume_min and (not entry_volume_max or volume_ratio <= entry_volume_max)
    smart_money_supports = bool(flow_score) and flow_score >= entry_smart_money_min
    execution_supports = bool(trade_strength) and trade_strength >= entry_trade_strength_min
    orderbook_supports = bool(bid_ask_imbalance) and bid_ask_imbalance >= entry_orderbook_min
    allocation_room = (not position_weight or position_weight <= entry_position_max) and (not sector_ratio or sector_ratio <= entry_sector_max)
    entry_support_count = sum(
        1
        for value in [
            ma5_supports_entry,
            volume_is_usable,
            smart_money_supports,
            execution_supports,
            orderbook_supports,
            direct_support_news_count > 0,
        ]
        if value
    )
    entry_data_blocked = bool((external_quality and external_quality < 60) or external_errors >= 2)
    entry_external_risk_blocked = bool(has_disclosure or direct_risk_news_count or entry_macro_blocked or entry_fx_blocked or entry_data_blocked)
    entry_required_data_missing = bool(not facts.get("ma5") or entry_macro_missing or entry_fx_missing)
    entry_block_reasons = [
        reason
        for reason in [
            "5일선 타이밍 미확인" if not facts.get("ma5") else "",
            "5일선보다 낮아 짧은 진입 타이밍 부족" if facts.get("ma5") and not ma5_supports_entry else "",
            "60일선 지지 부족" if not ma60_supports_entry else "",
            "거래량 확인 부족" if not volume_is_usable else "",
            "금리 부담 또는 금리 변화 확인 필요" if entry_macro_blocked else "",
            "금리 데이터 없음" if entry_macro_missing else "",
            "환율 부담 또는 환율 변화 확인 필요" if entry_fx_blocked else "",
            "환율 데이터 없음" if entry_fx_missing else "",
            "공시/부정 뉴스 리스크" if has_disclosure or direct_risk_news_count else "",
            "외부 데이터 품질 확인 필요" if entry_data_blocked else "",
            "보유 손실 구간이라 추가매수보다 손실 관리 우선" if is_holding and pnl < 0 else "",
        ]
        if reason
    ]
    facts["entryPullbackZone"] = pullback_zone
    facts["entryMa5TimingOk"] = ma5_supports_entry
    facts["entryMomentumTrendReady"] = ma20_momentum_ready and ma60_momentum_ready
    facts["entrySupportCount"] = entry_support_count
    facts["entryAllocationRoom"] = allocation_room
    facts["entryExternalRiskBlocked"] = entry_external_risk_blocked
    facts["entryRequiredDataMissing"] = entry_required_data_missing
    facts["entryBlockReasons"] = entry_block_reasons
    pullback_entry_ready = (
        pullback_zone
        and ma5_supports_entry
        and ma60_supports_entry
        and allocation_room
        and entry_support_count >= 3
        and not entry_external_risk_blocked
        and not entry_required_data_missing
        and (source == "watchlist" or pnl >= 0)
    )
    momentum_entry_ready = (
        source == "watchlist"
        and ma5_supports_entry
        and ma20_momentum_ready
        and ma60_momentum_ready
        and volume_is_usable
        and allocation_room
        and entry_support_count >= 3
        and not entry_external_risk_blocked
        and not entry_required_data_missing
    )
    if (
        pullback_entry_ready
    ):
        score = (
            52
            + min(14, entry_support_count * 4)
            + (8 if smart_money_supports else 0)
            + (6 if execution_supports or orderbook_supports else 0)
            + (6 if source == "watchlist" else 0)
        )
        matches.append(_match(
            "entry.pullback.supported.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("5일선", ma5_distance),
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "거래량 배율 미확인",
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%" if bid_ask_imbalance else "",
                "보유 비중 " + ("%.1f" % position_weight) + "%",
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if momentum_entry_ready:
        score = (
            58
            + min(16, entry_support_count * 4)
            + (8 if ma20_distance >= 0 else 0)
            + (8 if ma60_distance >= 0 else 0)
            + (6 if direct_support_news_count else 0)
        )
        matches.append(_match(
            "entry.momentum.confirmed.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("5일선", ma5_distance),
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "확인 신호 " + str(entry_support_count) + "/6",
                "금리·환율 진입 차단 없음",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    winner_add_support_count = sum(
        1
        for value in [
            bool(facts.get("ma5")) and ma5_distance >= max(entry_ma5_min, 0.0),
            bool(facts.get("ma20")) and ma20_distance >= 0,
            bool(facts.get("ma60")) and ma60_distance >= 0,
            volume_is_usable,
            smart_money_supports,
            execution_supports,
            orderbook_supports,
            direct_support_news_count > 0,
        ]
        if value
    )
    winner_add_buy_ready = (
        is_holding
        and pnl >= 3.0
        and bool(facts.get("allowAddOnStrength", True))
        and bool(facts.get("ma5"))
        and ma5_distance >= max(entry_ma5_min, 0.0)
        and bool(facts.get("ma20"))
        and ma20_distance >= 0
        and bool(facts.get("ma60"))
        and ma60_distance >= 0
        and allocation_room
        and winner_add_support_count >= 4
        and not entry_external_risk_blocked
        and not entry_required_data_missing
    )
    facts["winnerAddBuySupportCount"] = winner_add_support_count
    facts["winnerAddBuyReady"] = winner_add_buy_ready
    if winner_add_buy_ready:
        score = (
            62
            + min(18, winner_add_support_count * 3)
            + min(10, pnl * 0.3)
            + (6 if direct_support_news_count else 0)
        )
        matches.append(_match(
            "holding.winner_momentum.add_buy_review.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("5일선", ma5_distance),
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "거래량 배율 미확인",
                "확인 신호 " + str(winner_add_support_count) + "/8",
                "보유 비중 " + ("%.1f" % position_weight) + "%",
                "종목 타입 정책상 상승 구간 추가매수 검토 허용",
                "즉시 몰아사기보다 소액 분할 추가매수 검토",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    wait_for_entry_confirmation = (
        source == "watchlist"
        and not pullback_entry_ready
        and not momentum_entry_ready
        and (
            ma20_distance >= -2
            or direct_support_news_count > 0
            or has_news
            or entry_macro_blocked
            or entry_fx_blocked
            or entry_required_data_missing
            or volume_ratio < entry_volume_min
        )
    )
    if wait_for_entry_confirmation:
        score = 50 + min(12, len(entry_block_reasons) * 3) + (8 if direct_support_news_count else 0) + (6 if ma20_distance >= 0 else 0)
        matches.append(_match(
            "entry.wait_for_confirmation.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("5일선", ma5_distance) if facts.get("ma5") else "5일선 미확인",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x" if volume_ratio else "거래량 배율 미확인",
                "대기 사유 " + " · ".join(entry_block_reasons[:4]) if entry_block_reasons else "확인 조건 부족",
                "매수보다 5일선·60일선·거래량·금리·환율 재확인 우선",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    add_buy_risk = (
        is_holding
        and (
            (pnl < 0 and ma20_distance <= entry_ma20_below)
            or ma60_distance < entry_ma60_support
            or has_disclosure
            or has_news and pnl < 0 and ma20_distance < 0
            or entry_macro_blocked
            or entry_fx_blocked
        )
    )
    if add_buy_risk:
        score = 52
        if pnl < 0:
            score += min(12, abs(pnl) * 1.2)
        if ma20_distance <= entry_ma20_deep:
            score += 12
        elif ma20_distance <= entry_ma20_below:
            score += 7
        if ma60_distance < entry_ma60_support:
            score += 8
        if has_disclosure:
            score += 10
        if has_news:
            score += 4
        matches.append(_match(
            "entry.add_buy.blocked.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "신규 공시 있음" if has_disclosure else "",
                "관련 뉴스 있음" if has_news else "",
                "금리 부담 있음" if entry_macro_blocked else "",
                "환율 부담 있음" if entry_fx_blocked else "",
                "추가매수보다 회복 조건 확인 우선",
            ],
            missing_labels,
                definitions=relation_definitions,
        ))

    averaging_support_count = sum(
        1
        for value in [
            volume_ratio >= 1.0,
            trade_strength >= 100,
            bid_ask_imbalance >= 5,
            flow_score >= 10,
            recovery_attempt,
        ]
        if value
    )
    avg_loss_threshold = float(thresholds.get("lossRateLow", -8.0) or -8.0)
    avg_loss_buffer = abs(float(thresholds.get("lossRateBufferPct", 1.0) or 0.0))
    weak_near_loss_for_averaging = (
        pnl <= avg_loss_threshold
        and avg_loss_threshold - pnl <= avg_loss_buffer
        and ma60_distance > 0
        and volume_ratio < float(thresholds.get("lossGuardVolumeConfirmRatio", 0.8) or 0.8)
        and flow_score > -15
    )
    if is_holding and pnl < 0 and ma20_distance < 0 and averaging_support_count < 2 and not weak_near_loss_for_averaging:
        score = 55 + min(18, abs(pnl) * 1.2) + min(12, abs(ma20_distance) * 1.1) + (8 if ma60_distance < 0 else 0)
        matches.append(_match(
            "averaging_down.block.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                moving_average_distance_text("20일선", ma20_distance),
                "확인 신호 " + str(averaging_support_count) + "/5",
                "추가매수보다 보유 이유 재확인 우선",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    if is_holding and pnl < 0 and joint_smart_money_inflow:
        score = (
            52
            + min(18, max(0.0, flow_score) * 0.28)
            + min(10, abs(pnl) * 0.4)
            + min(10, loss_recovery_signal_count * 2)
        )
        matches.append(_match(
            "holding.loss_smart_money.defense.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "외국인·기관 동반 순매수",
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "회복 확인 신호 " + str(loss_recovery_signal_count) + "개",
                "투자 성향 " + str(facts.get("investmentStrategyProfileLabel") or facts.get("investmentStrategyProfile") or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if add_buy_stage == "ADD_BUY_WATCH":
        score = 55 + min(18, max(0.0, flow_score) * 0.24) + min(12, loss_recovery_signal_count * 3)
        matches.append(_match(
            "holding.loss_smart_money.reversal_watch.v1",
            score,
            data_quality,
            [
                "외국인·기관 동반 순매수",
                "회복 확인 신호 " + str(loss_recovery_signal_count) + "/" + str(facts.get("addBuyReviewSignalMin") or "-"),
                "아직 추가매수는 관찰 단계",
                "막는 이유 " + " · ".join(str(item) for item in list(facts.get("addBuyBlockedReasons") or [])[:3]) if facts.get("addBuyBlockedReasons") else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if add_buy_stage == "ADD_BUY_REVIEW":
        score = 68 + min(16, max(0.0, flow_score) * 0.18) + min(12, loss_recovery_signal_count * 2)
        matches.append(_match(
            "holding.loss_smart_money.add_buy_review.v1",
            score,
            data_quality,
            [
                "외국인·기관 동반 순매수",
                "회복 확인 신호 " + str(loss_recovery_signal_count) + "개",
                "직접 악재 뉴스 없음",
                "투자 성향 " + str(facts.get("investmentStrategyProfileLabel") or facts.get("investmentStrategyProfile") or "-"),
                "즉시 몰아사기보다 소액 분할 검토",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if is_holding and pnl < 0 and add_buy_stage == "ADD_BUY_BLOCKED":
        score = 54 + min(18, abs(pnl) * 0.9) + min(10, max(0.0, 3 - loss_recovery_signal_count) * 3)
        matches.append(_match(
            "holding.averaging_down.risk_guard.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "추가매수 판단 " + str(facts.get("addBuyEligibilityLabel") or "보류"),
                "막는 이유 " + " · ".join(str(item) for item in list(facts.get("addBuyBlockedReasons") or [])[:3]),
                "회복 확인 전 평균단가 낮추기 금지",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))

    loss_threshold = float(thresholds.get("lossRateLow", -8.0) or -8.0)
    loss_buffer = abs(float(thresholds.get("lossRateBufferPct", 1.0) or 0.0))
    volume_confirm_ratio = float(thresholds.get("lossGuardVolumeConfirmRatio", 0.8))
    ma60_support_threshold = float(thresholds.get("lossGuardMa60SupportPct", 0.0) or 0.0)
    weak_evidence_penalty = float(thresholds.get("lossGuardWeakEvidencePenalty", 30.0) or 0.0)
    facts["lossThreshold"] = loss_threshold
    facts["lossRateBufferPct"] = loss_buffer
    if pnl < 0 and (pnl <= loss_threshold or ma20_distance <= -5):
        volume_ratio = float(facts.get("volumeRatio") or 0)
        loss_depth = max(0.0, loss_threshold - pnl) if pnl <= loss_threshold else 0.0
        near_loss_threshold = pnl <= loss_threshold and loss_depth <= loss_buffer
        ma60_holds = bool(facts.get("ma60")) and ma60_distance > ma60_support_threshold
        volume_confirms = volume_ratio >= volume_confirm_ratio
        sell_flow_confirms = float(facts.get("sellShare") or 0) >= 56
        flow_confirms = flow_score <= -15
        slope_confirms = float(facts.get("ma20Slope") or 0) <= -1 or float(facts.get("ma60Slope") or 0) <= -0.5
        ma60_breaks = bool(facts.get("ma60")) and ma60_distance <= ma60_support_threshold
        confirmation_count = sum(1 for value in [ma60_breaks, volume_confirms, sell_flow_confirms, flow_confirms, slope_confirms] if value)
        weak_near_threshold = (
            near_loss_threshold
            and ma20_distance <= -5
            and ma60_holds
            and not volume_confirms
            and not sell_flow_confirms
            and not flow_confirms
        )
        score = 58 + min(24, abs(min(pnl, loss_threshold)) * 1.5) + (10 if ma20_distance <= -5 else 0)
        if weak_near_threshold:
            score -= weak_evidence_penalty
        if joint_smart_money_inflow:
            score -= min(14, 4 + loss_recovery_signal_count * 2)
        matches.append(_match(
            "holding.loss_guard.breakdown.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "손실 기준 " + ("%.1f" % loss_threshold) + "%",
                "손실 완충 " + ("%.1f" % loss_buffer) + "%p",
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "확인 신호 " + str(confirmation_count) + "/5",
                "외국인·기관 동반 순매수로 방어 강도 일부 완화" if joint_smart_money_inflow else "",
                ("약한 확인 신호 감점 -" + ("%.1f" % weak_evidence_penalty) + "점") if weak_near_threshold else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if support_retest:
        score = (
            50
            + min(16, abs(ma20_distance) * 1.1)
            + (7 if ma60_distance >= 0 else 3)
            + (4 if price_change >= 0 else 0)
            - (8 if breakdown_acceleration else 0)
        )
        matches.append(_match(
            "trend.support_retest.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "가격 모멘텀 " + str(facts.get("priceMomentumLabel") or "-") + " (" + ("%.1f" % price_change) + "%)",
                "기울기 " + str(facts.get("trendSlopeLabel") or "-"),
                "추세 커브 " + str(facts.get("trendCurveLabel") or "-") + " (" + ("%.1f" % trend_curve) + ")",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    support_confirmation_count = sum(
        1
        for value in [
            support_retest,
            price_change >= 0,
            trade_strength >= 100,
            bid_ask_imbalance >= 5,
            flow_score >= 10,
        ]
        if value
    )
    if support_retest and support_confirmation_count >= 3 and not breakdown_acceleration:
        score = 50 + min(20, support_confirmation_count * 5) + (5 if ma60_distance >= 0 else 0)
        matches.append(_match(
            "support.retest.confirmed.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("60일선", ma60_distance),
                "확인 신호 " + str(support_confirmation_count) + "/5",
                "체결강도 " + ("%.1f" % trade_strength) if trade_strength else "",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%" if bid_ask_imbalance else "",
            ],
            missing_labels,
            reference_only=True,
            definitions=relation_definitions,
        ))
    if recovery_attempt:
        score = (
            48
            + (8 if price_change >= 1.0 else 0)
            + min(8, max(0.0, ma20_slope) * 8.0)
            + min(8, max(0.0, trend_curve) * 5.0)
            + (5 if ma60_distance >= 0 else 0)
        )
        matches.append(_match(
            "trend.recovery_attempt.v1",
            score,
            data_quality,
            [
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "60일선 기울기 " + ("%.1f" % ma60_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
                moving_average_distance_text("60일선", ma60_distance),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if has_previous_state and previous_ma20_distance >= 0 and ma20_distance < 0 and (price_change <= -1.0 or volume_ratio >= 1.2 or float(facts.get("sellShare") or 0) >= 56):
        score = (
            60
            + min(16, abs(ma20_distance) * 1.5)
            + min(12, abs(min(0.0, price_change)) * 3.0)
            + (8 if volume_ratio >= 1.2 else 0)
            + (6 if float(facts.get("sellShare") or 0) >= 56 else 0)
        )
        matches.append(_match(
            "breakout.failure.v1",
            score,
            data_quality,
            [
                "이전 20일선 괴리 " + ("%.1f" % previous_ma20_distance) + "% -> 현재 " + ("%.1f" % ma20_distance) + "%",
                "가격 변화 " + ("%.1f" % price_delta_previous) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "매도 체결 비중 " + ("%.1f" % float(facts.get("sellShare") or 0)) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if has_previous_state and previous_ma60_distance >= -1.0 and ma60_distance < -1.0 and (price_change < 0 or trend_curve < 0 or ma60_delta_previous < -1.5):
        score = 56 + min(18, abs(ma60_distance) * 2.2) + min(12, abs(min(0.0, trend_curve)) * 4.0) + min(8, abs(min(0.0, ma60_delta_previous)) * 2.0)
        matches.append(_match(
            "support.retest.failed.v1",
            score,
            data_quality,
            [
                "이전 60일선 괴리 " + ("%.1f" % previous_ma60_distance) + "% -> 현재 " + ("%.1f" % ma60_distance) + "%",
                "60일선 괴리 변화 " + ("%.1f" % ma60_delta_previous) + "%p",
                "추세 커브 " + ("%.1f" % trend_curve),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if breakdown_acceleration:
        score = (
            60
            + min(20, trend_dynamic_risk * 0.35)
            + min(10, abs(min(0.0, price_change)) * 2.0)
            + min(10, abs(min(0.0, ma20_slope)) * 5.0)
            + (6 if ma60_distance < 0 else 0)
        )
        matches.append(_match(
            "trend.breakdown_acceleration.v1",
            score,
            data_quality,
            [
                moving_average_distance_text("20일선", ma20_distance),
                moving_average_distance_text("60일선", ma60_distance),
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "60일선 기울기 " + ("%.1f" % ma60_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
                "추세 동역학 리스크 " + ("%.1f" % trend_dynamic_risk) + "점",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if volume_ratio >= 1.2 and price_change > -1.5 and (flow_score <= -15 or float(facts.get("sellShare") or 0) >= 56 or bid_ask_imbalance <= -10):
        score = 52 + min(14, max(0.0, volume_ratio - 1.0) * 10) + min(16, abs(min(0.0, flow_score)) * 0.35) + min(10, abs(min(0.0, bid_ask_imbalance)) * 0.35)
        matches.append(_match(
            "distribution.detected.v1",
            score,
            data_quality,
            [
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "매도 체결 비중 " + ("%.1f" % float(facts.get("sellShare") or 0)) + "%",
                "호가 불균형 " + ("%.1f" % bid_ask_imbalance) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if pnl >= 10 and (volume_ratio >= 1.5 or abs(price_change) >= 2.0) and (ma20_slope <= 0.2 or trend_curve <= -0.4 or ma20_delta_previous <= -2.0):
        score = 54 + min(18, max(0.0, pnl - 10) * 1.0) + min(12, max(0.0, volume_ratio - 1.0) * 8.0) + min(12, abs(min(0.0, trend_curve)) * 5.0)
        matches.append(_match(
            "profit.protection.volatility.v1",
            score,
            data_quality,
            [
                "손익률 " + ("%.1f" % pnl) + "%",
                "거래량 배율 " + ("%.1f" % volume_ratio) + "x",
                "가격 변화율 " + ("%.1f" % price_change) + "%",
                "20일선 기울기 " + ("%.1f" % ma20_slope) + "%",
                "추세 커브 " + ("%.1f" % trend_curve),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if sector_ratio >= float(thresholds.get("sectorWeightHigh", 50.0) or 50.0) or position_weight >= float(thresholds.get("positionWeightHigh", 30.0) or 30.0):
        score = 50 + min(25, max(0, sector_ratio - 35) * 0.9) + min(25, max(0, position_weight - 20) * 1.1)
        matches.append(_match(
            "holding.concentration.rebalance.v1",
            score,
            data_quality,
            [
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
                "종목 비중 " + ("%.1f" % position_weight) + "%",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if is_holding and (liquidity_risk >= 45 or position_to_trading_value >= 5 or facts.get("sellableBlocked")):
        score = 48 + min(25, liquidity_risk * 0.35) + min(18, position_to_trading_value * 1.2) + (8 if facts.get("sellableBlocked") else 0)
        matches.append(_match(
            "liquidity.exit_capacity.v1",
            score,
            data_quality,
            [
                "포지션/거래대금 " + ("%.1f" % position_to_trading_value) + "%",
                "10% 거래대금 기준 청산 일수 " + ("%.1f" % float(facts.get("exitDaysAtTenPctADV") or 0)),
                "유동성 리스크 " + ("%.1f" % liquidity_risk) + "점",
                "매도 가능 수량 제한" if facts.get("sellableBlocked") else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if sector_ratio >= 45 and (position_weight >= 10 or trend_score < -4 or pnl < 0):
        score = 48 + min(26, max(0.0, sector_ratio - 35) * 0.8) + min(16, max(0.0, position_weight - 8) * 0.9) + (8 if trend_score < -4 else 0)
        matches.append(_match(
            "factor.crowding.v1",
            score,
            data_quality,
            [
                "업종 비중 " + ("%.1f" % sector_ratio) + "%",
                "종목 비중 " + ("%.1f" % position_weight) + "%",
                "시장/통화 " + str(facts.get("market") or "-") + "/" + str(facts.get("currency") or "-"),
                "추세 점수 " + ("%.1f" % trend_score),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if trend_score and flow_score and (trend_score > 4 and flow_score > 10 or trend_score < -4 and flow_score < -10):
        direction = "우호" if trend_score > 0 else "위험"
        score = 48 + min(24, abs(trend_score) * 1.2) + min(20, abs(flow_score) * 0.3)
        matches.append(_match(
            "holding.trend_flow.confirmation.v1",
            score,
            data_quality,
            [
                "추세 점수 " + ("%.1f" % trend_score),
                "투자자 수급 점수 " + ("%.1f" % flow_score),
                "공통 방향 " + direction,
            ],
            missing_labels,
            reference_only=trend_score > 0 and flow_score > 0,
            definitions=relation_definitions,
        ))
    data_conflict_active = (
        (external_quality and external_quality < 60)
        or external_errors >= 2
        or str(facts.get("externalSignalFreshnessStatus") or "") == "stale"
    )
    if data_conflict_active:
        score = 45 + min(22, max(0.0, 60 - external_quality) * 0.5 if external_quality else 8) + min(12, external_errors * 4) + min(12, len(missing_labels) * 3)
        matches.append(_match(
            "data.conflict.v1",
            score,
            data_quality,
            [
                "외부 신호 품질 " + ("%.1f" % external_quality) if external_quality else "외부 신호 품질 미확인",
                "외부 신호 오류 " + str(int(external_errors)) + "건",
                "신선도 " + str(facts.get("externalSignalFreshnessStatus") or "-"),
                "부족 데이터 " + ", ".join(missing_labels[:4]) if missing_labels else "",
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if rate_sensitive and rate_delta_active:
        score = (
            49
            + min(22, rate_delta_magnitude / max(1.0, rate_delta_threshold or 1.0) * 10.0)
            + (10 if high_rate_active else 0)
            + (6 if low_rate_active else 0)
            + (9 if inverted_curve_active else 0)
            + min(10, max(0.0, macro_dgs10 - rate_high_threshold) * 5.0 if high_rate_active else 0.0)
            + min(8, max(0.0, fx_exposure_ratio - fx_exposure_review) * 0.4)
        )
        matches.append(_match(
            "rates.interest_rate.sensitivity.v1",
            score,
            data_quality,
            [
                "10년 금리 " + ("%.2f" % macro_dgs10) + "%" if macro_dgs10 else "",
                "10년 금리 변화 " + _bp_text(macro_dgs10_delta_bp) if facts.get("hasMacroDgs10Delta") else "",
                "2년 금리 " + ("%.2f" % macro_dgs2) + "%" if macro_dgs2 else "",
                "2년 금리 변화 " + _bp_text(macro_dgs2_delta_bp) if facts.get("hasMacroDgs2Delta") else "",
                "10Y-2Y 스프레드 " + ("%.2f" % macro_spread) + "%p" if _has_numeric_fact(facts.get("macroYieldSpread10y2y")) else "",
                "10Y-2Y 스프레드 변화 " + _bp_text(macro_spread_delta_bp) if facts.get("hasMacroYieldSpreadDelta") else "",
                "금리 레짐 " + str(facts.get("rateRegime") or "-"),
                "민감 섹터/통화 " + str(facts.get("sector") or "-") + "/" + (currency or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if fx_extreme_active or fx_exposure_active:
        score = (
            48
            + (12 if fx_extreme_active else 0)
            + min(18, abs(usd_krw_delta_pct) / max(0.1, usd_krw_delta_pct_threshold or 1.0) * 8.0)
            + min(12, abs(usd_krw_delta_krw) / max(1.0, usd_krw_delta_krw_threshold or 1.0) * 5.0)
            + min(16, abs(usd_krw_rate - (usd_krw_high if usd_krw_rate >= usd_krw_high else usd_krw_low)) / 10.0 if fx_extreme_active else 0.0)
            + min(18, max(0.0, fx_exposure_ratio - fx_exposure_review) * 1.1)
        )
        matches.append(_match(
            "fx.usd_krw.exposure.v1",
            score,
            data_quality,
            [
                "환율 " + str(facts.get("fxRatePair") or "USDKRW") + " " + ("%.2f" % (usd_krw_rate or fx_rate_to_krw)),
                "환율 변화 " + _number_text(usd_krw_delta_krw, 2, signed=True) + "원"
                + (" (" + _number_text(usd_krw_delta_pct, 2, signed=True) + "%)" if _has_numeric_fact(facts.get("usdKrwDeltaPct")) else ""),
                "환율 레짐 " + str(facts.get("fxRegime") or "-"),
                "외화 노출 " + ("%.1f" % fx_exposure_ratio) + "%",
                "보유 통화 " + (currency or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    macro_risk_active = macro_sensitive and (
        rate_delta_active
        or abs(btc_change24h) >= float(thresholds.get("externalBitcoinChange24hPct", 3.0) or 3.0)
        or fx_extreme_active
    )
    if macro_risk_active:
        score = 50 + (8 if high_rate_active else 0) + (8 if inverted_curve_active else 0) + min(12, rate_delta_magnitude / max(1.0, rate_delta_threshold or 1.0) * 5.0) + min(18, abs(btc_change24h) * 2.0) + (6 if fx_extreme_active else 0)
        matches.append(_match(
            "macro.regime.shift.v1",
            score,
            data_quality,
            [
                "10년 금리 " + ("%.2f" % macro_dgs10) if macro_dgs10 else "",
                "10년 금리 변화 " + _bp_text(macro_dgs10_delta_bp) if facts.get("hasMacroDgs10Delta") else "",
                "10Y-2Y 스프레드 " + ("%.2f" % macro_spread) if macro_spread else "",
                "10Y-2Y 스프레드 변화 " + _bp_text(macro_spread_delta_bp) if facts.get("hasMacroYieldSpreadDelta") else "",
                "BTC 24h " + ("%.1f" % btc_change24h) + "%" if btc_change24h else "",
                "환율 " + ("%.2f" % usd_krw_rate) + " · 변화 " + _number_text(usd_krw_delta_krw, 2, signed=True) + "원" if fx_extreme_active else "",
                "민감 섹터/통화 " + str(facts.get("sector") or "-") + "/" + (currency or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    btc_threshold24h = float(thresholds.get("externalBitcoinChange24hPct", 3.0) or 3.0)
    btc_threshold7d = float(thresholds.get("externalBitcoinChange7dPct", 4.0) or 4.0)
    if facts.get("isBtcSensitive") and (abs(btc_change24h) >= btc_threshold24h or abs(btc_change7d) >= btc_threshold7d):
        score = 50 + min(25, abs(btc_change24h) / max(1, btc_threshold24h) * 10) + min(25, abs(btc_change7d) / max(1, btc_threshold7d) * 10)
        matches.append(_match(
            "external.crypto.btc_sensitivity.v1",
            score,
            data_quality,
            [
                "BTC 24h " + ("%.1f" % btc_change24h) + "%",
                "BTC 7d " + ("%.1f" % btc_change7d) + "%",
                "민감 종목 " + str(facts.get("symbol") or ""),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    disclosure = facts.get("dartDisclosure")
    if isinstance(disclosure, dict) and disclosure:
        matches.append(_match(
            "disclosure.material_event.v1",
            62,
            data_quality,
            [
                "공시 " + str(disclosure.get("reportName") or "-"),
                "접수일 " + str(disclosure.get("receiptDate") or "-"),
            ],
            missing_labels,
            definitions=relation_definitions,
        ))
    if missing_labels:
        matches.append(_match(
            "data.quality.guard.v1",
            max(35, 100 - data_quality),
            data_quality,
            ["부족 데이터 " + ", ".join(missing_labels[:5])],
            missing_labels,
            reference_only=True,
            definitions=relation_definitions,
        ))

    decision = decision_from_matches(facts, matches)
    execution_plan = execution_plan_from_relation_context(facts, decision, matches)
    prompt_context = build_ai_prompt_context(prompt_id, facts, matches, settings, execution_plan)
    active_matches = [item for item in matches if item.matched and not item.reference_only]
    max_strength = max([item.strength_score for item in active_matches], default=decision["score"])
    return {
        "engineVersion": ONTOLOGY_RULE_ENGINE_VERSION,
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "sector": facts.get("sector"),
        },
        "facts": facts,
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "activeRules": [item.to_dict() for item in active_matches],
        "referenceRules": [item.to_dict() for item in matches if item.reference_only],
        "missingData": list(facts.get("missingData") or []),
        "dominantSignals": [item.label for item in active_matches[:3]],
        "signalStrength": round(float(max_strength or 0), 1),
        "signalStrengthLabel": strength_label(max_strength),
        "confidence": round(data_quality, 1),
        "decision": decision,
        "executionPlan": execution_plan,
        "promptContext": prompt_context,
        "legacyModel": dict(legacy_model or {}),
    }
