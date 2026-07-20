from typing import Dict, List

from .investment_ubiquitous_language import (
    investment_archetype_label,
    investment_archetype_labels,
    position_intent_label,
    position_intent_sentence,
)

from .market_data import known_stock, number
from .instrument_profiles import (
    InstrumentProfile,
    instrument_profile_for_position,
    is_market_proxy_profile,
    market_proxy_themes_for_profile,
    market_signal_profiles,
)
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation
from .ontology_threshold_policy import default_ontology_threshold_policy
from .portfolio import PortfolioSummary, Position
from .portfolio_ontology_catalog import FACTOR_BENCHMARKS, SECTOR_FACTORS
from .portfolio_ontology_market_concepts import symbol_key
from .portfolio_ontology_runtime_concepts import is_holding_position


def unique_list(values: List[str]) -> List[str]:
    seen = set()
    rows: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def position_weight(position: Position, portfolio: PortfolioSummary) -> float:
    base = number(portfolio.total) or number(portfolio.invested)
    return (number(position.market_value) / base) * 100 if base else 0.0


def factor_labels_for_position(position: Position) -> List[str]:
    labels = []
    sector = str(position.sector or "").strip()
    labels.extend(SECTOR_FACTORS.get(sector, []))
    currency = str(position.currency or "").upper().strip()
    market = str(position.market or "").upper().strip()
    symbol = str(position.symbol or "").upper().strip()
    if currency and currency != "KRW":
        labels.append(currency + " 환율")
    if market in {"US", "USA", "NASDAQ", "NYSE"}:
        labels.append("미국 주식 베타")
    if market in {"KR", "KOSPI", "KOSDAQ"} or currency == "KRW":
        labels.append("한국 시장 베타")
    if symbol in {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}:
        labels.append("비트코인 민감도")
    return unique_list(labels)


def profile_settings_from_runtime(runtime_context: Dict[str, object] = None) -> Dict[str, object]:
    if not isinstance(runtime_context, dict):
        return {}
    settings = runtime_context.get("settings")
    if isinstance(settings, dict):
        return settings
    return runtime_context


def profile_tbox_classes(profile: InstrumentProfile) -> List[str]:
    classes = ["InstrumentProfile"]
    if is_market_proxy_profile(profile):
        classes.append("MarketProxyInstrument")
    classes.extend(profile.archetypes or [])
    return unique_list(classes)


def market_proxy_instrument_classes(profile: InstrumentProfile) -> List[str]:
    classes = ["Instrument", "MarketProxyInstrument"]
    asset_type = str(known_stock(profile.symbol).get("assetType") or "").upper()
    if any(item in {"DailyLeveragedProduct"} for item in profile.archetypes or []):
        classes.append("LeveragedETF")
    if asset_type == "CRYPTO" or profile.symbol in {"BTC", "ETH"} or "CryptoAssetProfile" in (profile.archetypes or []):
        classes.append("CryptoAsset")
    elif asset_type == "ETF":
        classes.append("ETF")
        classes.append("MarketProxyETF")
    elif asset_type == "INDEX":
        classes.append("Index")
        classes.append("MarketProxyIndex")
    else:
        classes.extend(["Equity", "Stock"])
    classes.extend(profile.archetypes or [])
    return unique_list(classes)


def market_proxy_quotes_from_runtime(runtime_context: Dict[str, object] = None) -> Dict[str, Dict[str, object]]:
    if not isinstance(runtime_context, dict):
        return {}
    metadata = runtime_context.get("metadata") if isinstance(runtime_context.get("metadata"), dict) else {}
    quotes = metadata.get("marketProxyQuotes") or metadata.get("marketSignalProxyQuotes") or {}
    if not isinstance(quotes, dict):
        return {}
    return {
        str(symbol or "").upper(): dict(payload)
        for symbol, payload in quotes.items()
        if str(symbol or "").strip() and isinstance(payload, dict)
    }


def market_proxy_observation_id(profile: InstrumentProfile) -> str:
    return entity_id("market-proxy-observation", profile.symbol)


def market_proxy_observation_pressure(quote: Dict[str, object]) -> Dict[str, object]:
    policy = default_ontology_threshold_policy().market_proxy
    change_rate = number(quote.get("changeRate"))
    ma20_distance = number(quote.get("ma20Distance"))
    ma60_distance = number(quote.get("ma60Distance"))
    volume_ratio = number(quote.get("volumeRatio"))
    risk_score = 0.0
    support_score = 0.0
    if change_rate < 0:
        risk_score += abs(change_rate) * 2.0
    else:
        support_score += change_rate * 1.6
    if ma20_distance < 0:
        risk_score += abs(ma20_distance) * 0.8
    else:
        support_score += ma20_distance * 0.5
    if ma60_distance < 0:
        risk_score += abs(ma60_distance) * 0.5
    else:
        support_score += ma60_distance * 0.3
    if volume_ratio >= policy.volume_confirmation_ratio:
        if risk_score >= support_score:
            risk_score += 2.0
        else:
            support_score += 1.5
    if risk_score >= support_score + policy.directional_margin_score and risk_score >= policy.minimum_directional_score:
        polarity = "risk"
    elif support_score >= risk_score + policy.directional_margin_score and support_score >= policy.minimum_directional_score:
        polarity = "support"
    else:
        polarity = "context"
    return {
        "polarity": polarity,
        "riskImpact": round(min(14.0, risk_score), 2),
        "supportImpact": round(min(10.0, support_score), 2),
    }


def add_market_proxy_observation_concepts(
    graph: PortfolioOntology,
    proxy_id: str,
    profile: InstrumentProfile,
    quote: Dict[str, object] = None,
    source: str = "market-proxy-quote",
) -> str:
    if not isinstance(quote, dict) or not quote:
        return ""
    pressure = market_proxy_observation_pressure(quote)
    source_as_of = str(quote.get("sourceAsOf") or quote.get("updatedAt") or "")
    source_fetched_at = str(quote.get("sourceFetchedAt") or quote.get("updatedAt") or "")
    observation_id = add_entity(graph, "market-proxy-observation", profile.symbol, profile.label + " 시장 센서 관측", {
        "tboxClass": "MarketProxyObservation",
        "tboxClasses": ["Observation", "PriceObservation", "MarketProxyObservation", "MarketProxyInstrument"],
        "symbol": profile.symbol,
        "label": profile.label,
        "currentPrice": number(quote.get("currentPrice")),
        "changeRate": number(quote.get("changeRate")),
        "volume": number(quote.get("volume")),
        "volumeRatio": number(quote.get("volumeRatio")),
        "tradingValue": number(quote.get("tradingValue")),
        "ma20Distance": number(quote.get("ma20Distance")),
        "ma60Distance": number(quote.get("ma60Distance")),
        "ma20Slope": number(quote.get("ma20Slope")),
        "ma60Slope": number(quote.get("ma60Slope")),
        "quoteSource": quote.get("quoteSource") or "",
        "dataQuality": quote.get("dataQuality") or "",
        "updatedAt": quote.get("updatedAt") or "",
        "observationDomain": "quote",
        "freshnessRequired": True,
        "freshnessStatus": quote.get("freshnessStatus") or "unknown",
        "freshnessReason": quote.get("freshnessReason") or "",
        "freshnessAgeMinutes": quote.get("freshnessAgeMinutes"),
        "sourceAsOf": source_as_of,
        "sourceFetchedAt": source_fetched_at,
        "sourceTimestampPresent": bool(quote.get("sourceTimestampPresent", bool(source_as_of))),
        "maxAgeMinutes": quote.get("maxAgeMinutes") or 10,
        "judgementEvidenceUsable": bool(quote.get("judgementEvidenceUsable")),
        "collectionPurpose": quote.get("collectionPurpose") or "",
        "collectionTarget": quote.get("collectionTarget") or "",
        **pressure,
    })
    add_relation(graph, proxy_id, observation_id, "HAS_OBSERVATION", weight=0.72, properties={
        "source": source,
        "polarity": pressure["polarity"],
        "riskImpact": pressure["riskImpact"],
        "supportImpact": pressure["supportImpact"],
        "aiInfluenceLabel": profile.label + " 시장 센서 관측",
    })
    add_relation(graph, proxy_id, observation_id, "HAS_PRICE", weight=0.65, properties={
        "source": source,
        "polarity": pressure["polarity"],
        "riskImpact": pressure["riskImpact"],
        "supportImpact": pressure["supportImpact"],
        "aiInfluenceLabel": profile.label + " 가격 맥락",
    })
    return observation_id


def add_market_proxy_profile_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    profile: InstrumentProfile,
    source_id: str = "",
    source: str = "market-proxy-universe",
    quote: Dict[str, object] = None,
) -> str:
    archetype_labels = investment_archetype_labels(profile.archetypes)
    intent_label = position_intent_label(profile.position_intent)
    proxy_id = add_entity(graph, "market-proxy-instrument", profile.symbol, profile.label, {
        "tboxClass": "MarketProxyInstrument",
        "tboxClasses": market_proxy_instrument_classes(profile),
        "symbol": profile.symbol,
        "label": profile.label,
        "archetypes": list(profile.archetypes),
        "archetypeLabels": archetype_labels,
        "positionIntent": profile.position_intent,
        "positionIntentLabel": intent_label,
        "positionIntentDescription": position_intent_sentence(profile.position_intent),
        "sensitivities": dict(profile.sensitivities),
        "source": source,
    })
    add_market_proxy_observation_concepts(graph, proxy_id, profile, quote, source)
    add_relation(graph, portfolio_node_id, proxy_id, "OBSERVES_MARKET_PROXY", weight=0.45, properties={
        "source": source,
        "symbol": profile.symbol,
        "aiInfluenceLabel": profile.label + " 관찰",
    })
    if source_id:
        add_relation(graph, source_id, proxy_id, "HAS_MARKET_PROXY_PROFILE", weight=1.0, properties={
            "source": source,
            "symbol": profile.symbol,
            "aiInfluenceLabel": profile.label + " 시장 프록시",
        })
    for theme in market_proxy_themes_for_profile(profile):
        theme_key = str(theme.get("key") or "").strip()
        if not theme_key:
            continue
        theme_label = str(theme.get("label") or theme_key)
        theme_id = add_entity(graph, "market-proxy-theme", theme_key, theme_label, {
            "tboxClass": "MarketProxyTheme",
            "tboxClasses": ["Factor", "MarketProxyTheme"],
            "theme": theme_key,
            "label": theme_label,
            "source": source,
            "level": theme.get("level") or "",
            "themeSource": theme.get("source") or "",
        })
        add_relation(graph, proxy_id, theme_id, "PROXIES_THEME", weight=0.75, properties={
            "source": source,
            "symbol": profile.symbol,
            "theme": theme_key,
            "level": theme.get("level") or "",
            "aiInfluenceLabel": profile.label + " -> " + theme_label,
        })
        add_relation(graph, portfolio_node_id, theme_id, "OBSERVES_MARKET_PROXY", weight=0.25, properties={
            "source": source,
            "symbol": profile.symbol,
            "theme": theme_key,
            "aiInfluenceLabel": theme_label + " 시장 센서",
        })
        if theme.get("source") == "factor-sensitivity":
            factor_id = add_entity(graph, "factor", theme_label, theme_label, {
                "tboxClass": "Factor",
                "tboxClasses": ["Factor", "FactorExposure", "MarketProxyTheme"],
                "label": theme_label,
                "factor": theme_key,
            })
            add_relation(graph, proxy_id, factor_id, "SENSITIVE_TO", weight=0.7, properties={
                "source": source,
                "factor": theme_key,
                "level": theme.get("level") or "",
                "aiInfluenceLabel": profile.label + " " + theme_label + " 민감도",
            })
    return proxy_id


def add_market_proxy_universe_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    runtime_context: Dict[str, object] = None,
) -> None:
    profiles = market_signal_profiles(profile_settings_from_runtime(runtime_context))
    quotes = market_proxy_quotes_from_runtime(runtime_context)
    for profile in profiles.values():
        add_market_proxy_profile_concepts(graph, portfolio_node_id, profile, quote=quotes.get(profile.symbol))


def add_stock_market_proxy_context_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    portfolio_node_id: str,
    profile: InstrumentProfile,
    runtime_context: Dict[str, object] = None,
) -> None:
    quotes = market_proxy_quotes_from_runtime(runtime_context)
    if not quotes:
        return
    stock_factors = {str(factor or "").strip() for factor in (profile.sensitivities or {}).keys() if str(factor or "").strip()}
    if not stock_factors:
        return
    proxy_profiles = market_signal_profiles(profile_settings_from_runtime(runtime_context))
    for proxy_profile in proxy_profiles.values():
        proxy_factors = {str(factor or "").strip() for factor in (proxy_profile.sensitivities or {}).keys() if str(factor or "").strip()}
        overlap = sorted(stock_factors.intersection(proxy_factors))
        quote = quotes.get(proxy_profile.symbol)
        if not overlap or not quote:
            continue
        proxy_id = add_market_proxy_profile_concepts(
            graph,
            portfolio_node_id,
            proxy_profile,
            source_id=stock_id,
            source="market-proxy-context",
            quote=quote,
        )
        observation_id = market_proxy_observation_id(proxy_profile)
        pressure = market_proxy_observation_pressure(quote)
        add_relation(graph, stock_id, proxy_id, "OBSERVES_MARKET_PROXY", weight=0.35, properties={
            "source": "market-proxy-context",
            "overlapFactors": overlap,
            "symbol": proxy_profile.symbol,
            "aiInfluenceLabel": profile.label + "가 " + proxy_profile.label + "와 같은 팩터를 봅니다.",
        })
        add_relation(graph, stock_id, observation_id, "HAS_OBSERVATION", weight=0.32, properties={
            "source": "market-proxy-context",
            "overlapFactors": overlap,
            "polarity": pressure["polarity"],
            "riskImpact": pressure["riskImpact"],
            "supportImpact": pressure["supportImpact"],
            "aiInfluenceLabel": profile.label + " 시장 프록시 관측",
        })


def add_instrument_profile_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    portfolio_node_id: str,
    position: Position,
    runtime_context: Dict[str, object] = None,
) -> None:
    profile = instrument_profile_for_position(position, profile_settings_from_runtime(runtime_context))
    symbol = symbol_key(position)
    archetype_labels = investment_archetype_labels(profile.archetypes)
    intent_label = position_intent_label(profile.position_intent)
    intent_description = position_intent_sentence(profile.position_intent)
    profile_id = add_entity(graph, "instrument-profile", symbol, profile.label, {
        "tboxClass": "InstrumentProfile",
        "tboxClasses": profile_tbox_classes(profile),
        "symbol": symbol,
        "label": profile.label,
        "archetypes": list(profile.archetypes),
        "archetypeLabels": archetype_labels,
        "positionIntent": profile.position_intent,
        "positionIntentLabel": intent_label,
        "positionIntentDescription": intent_description,
        "sensitivities": dict(profile.sensitivities),
        "policies": dict(profile.policies),
        "allowAddOnStrength": profile.allow_add_on_strength,
        "trimOnTrendBreak": profile.trim_on_trend_break,
        "avoidAveragingDown": profile.avoid_averaging_down,
        "source": profile.source,
    })
    add_relation(graph, stock_id, profile_id, "HAS_INSTRUMENT_PROFILE", weight=1.0, properties={
        "source": "instrument-profile",
        "aiInfluenceLabel": profile.label,
        "positionIntent": profile.position_intent,
        "positionIntentLabel": intent_label,
    })
    add_relation(graph, portfolio_node_id, profile_id, "HAS_INSTRUMENT_PROFILE", weight=0.5, properties={
        "source": "instrument-profile",
        "symbol": symbol,
    })

    intent_id = add_entity(graph, "position-intent", profile.position_intent, intent_label, {
        "tboxClass": "PositionIntent",
        "intent": profile.position_intent,
        "positionIntent": profile.position_intent,
        "positionIntentLabel": intent_label,
        "description": intent_description,
    })
    add_relation(graph, profile_id, intent_id, "HAS_POSITION_INTENT", weight=1.0, properties={"source": "instrument-profile"})

    for archetype in profile.archetypes:
        archetype_label = investment_archetype_label(archetype)
        archetype_id = add_entity(graph, "investment-archetype", archetype, archetype_label, {
            "tboxClass": "InvestmentArchetype",
            "tboxClasses": ["InvestmentArchetype", archetype],
            "archetype": archetype,
            "instrumentArchetype": archetype,
            "archetypeLabel": archetype_label,
        })
        add_relation(graph, profile_id, archetype_id, "HAS_ARCHETYPE", weight=1.0, properties={
            "source": "instrument-profile",
            "aiInfluenceLabel": archetype_label,
        })
        add_relation(graph, stock_id, archetype_id, "HAS_ARCHETYPE", weight=0.8, properties={
            "source": "instrument-profile",
            "aiInfluenceLabel": archetype_label,
        })

    for factor, level in sorted(profile.sensitivities.items()):
        factor_key = symbol + ":" + str(factor)
        label = str(factor) + " 민감도 " + str(level)
        sensitivity_id = add_entity(graph, "factor-sensitivity", factor_key, label, {
            "tboxClass": "FactorSensitivity",
            "tboxClasses": ["FactorSensitivity", "FactorExposure"],
            "symbol": symbol,
            "factor": factor,
            "level": level,
            "sensitivityLevel": level,
        })
        add_relation(graph, profile_id, sensitivity_id, "HAS_FACTOR_SENSITIVITY", weight=1.0, properties={
            "source": "instrument-profile",
            "factor": factor,
            "level": level,
            "aiInfluenceLabel": label,
        })
        add_relation(graph, stock_id, sensitivity_id, "HAS_FACTOR_SENSITIVITY", weight=0.75, properties={
            "source": "instrument-profile",
            "factor": factor,
            "level": level,
        })

    policy_id = add_entity(graph, "instrument-policy", symbol, profile.label + " 행동 정책", {
        "tboxClass": "ActionPolicy",
        "tboxClasses": ["ActionPolicy", "InvestorProfilePolicy"],
        "symbol": symbol,
        "allowAddOnStrength": profile.allow_add_on_strength,
        "trimOnTrendBreak": profile.trim_on_trend_break,
        "avoidAveragingDown": profile.avoid_averaging_down,
    })
    add_relation(graph, profile_id, policy_id, "USES_INSTRUMENT_POLICY", weight=1.0, properties={
        "source": "instrument-profile",
        "allowAddOnStrength": profile.allow_add_on_strength,
        "trimOnTrendBreak": profile.trim_on_trend_break,
        "avoidAveragingDown": profile.avoid_averaging_down,
    })
    if is_market_proxy_profile(profile):
        add_market_proxy_profile_concepts(graph, portfolio_node_id, profile, source_id=stock_id, source="instrument-profile")
    add_stock_market_proxy_context_concepts(graph, stock_id, portfolio_node_id, profile, runtime_context)


def benchmark_for_position(position: Position) -> (str, str):
    market = str(position.market or "").upper().strip()
    return FACTOR_BENCHMARKS.get(market, ("benchmark:MARKET", "시장 벤치마크"))

def add_market_exposure_concepts(graph: PortfolioOntology, portfolio_node_id: str, portfolio: PortfolioSummary) -> None:
    for market in portfolio.markets:
        key = str(market.get("key") or market.get("market") or market.get("label") or "").strip()
        if not key:
            continue
        label = str(market.get("label") or key)
        market_id = add_entity(graph, "market", key, label, {"tboxClass": "Market"})
        exposure_id = add_entity(graph, "market-exposure", graph.portfolio_id + ":" + key, label + " 시장 노출", {
            "tboxClass": "MarketExposure",
            "market": key,
            "invested": number(market.get("invested")),
            "cash": number(market.get("cash")),
            "total": number(market.get("total")),
            "cashRatio": number(market.get("cashRatio")),
        })
        add_relation(graph, portfolio_node_id, exposure_id, "HAS_MARKET_EXPOSURE", weight=1.0, properties={"basis": "portfolio-market-summary"})
        add_relation(graph, exposure_id, market_id, "AFFECTS", weight=1.0, properties={"polarity": "context", "aiInfluenceLabel": label + " 시장 노출"})


def add_portfolio_factor_exposure_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    portfolio: PortfolioSummary,
    observed_positions: List[Position],
    runtime_context: Dict[str, object] = None,
) -> None:
    policy = default_ontology_threshold_policy().market_proxy
    add_market_proxy_universe_concepts(graph, portfolio_node_id, runtime_context)
    total = number(portfolio.total) or number(portfolio.invested)
    if not total:
        return
    currency_exposure: Dict[str, float] = {}
    raw_position_total = sum(number(position.market_value) for position in observed_positions if is_holding_position(position))
    sector_positions: Dict[str, int] = {}
    for position in observed_positions:
        if not is_holding_position(position):
            continue
        currency = str(position.currency or "").upper().strip()
        sector = str(position.sector or "기타").strip() or "기타"
        if currency:
            currency_exposure[currency] = currency_exposure.get(currency, 0.0) + number(position.market_value)
        sector_positions[sector] = sector_positions.get(sector, 0) + 1
    for currency, value in sorted(currency_exposure.items()):
        ratio = (value / raw_position_total) * 100 if raw_position_total else 0.0
        if currency in {"KRW", ""} or ratio < policy.currency_exposure_min_pct:
            continue
        fx_id = add_entity(graph, "fx-pair", "KRW:" + currency, "KRW/" + currency + " 환율 노출", {
            "tboxClass": "FXPair",
            "currency": currency,
            "exposureValue": round(value, 2),
            "exposureRatio": round(ratio, 2),
        })
        risk_id = add_entity(graph, "risk", currency + "-currency-risk", currency + " 통화 리스크", {
            "tboxClass": "Risk",
            "tboxClasses": ["Risk", "CurrencyRisk"],
            "currency": currency,
            "exposureRatio": round(ratio, 2),
        })
        add_relation(graph, portfolio_node_id, fx_id, "HAS_MARKET_EXPOSURE", weight=round(ratio / 100, 4), properties={"source": "currency-exposure", "aiInfluenceLabel": currency + " 환율 노출"})
        add_relation(graph, portfolio_node_id, risk_id, "EXPOSED_TO", weight=round(ratio / 100, 4), properties={"source": "currency-exposure", "polarity": "context", "aiInfluenceLabel": currency + " 통화 리스크"})
        add_relation(graph, fx_id, risk_id, "AMPLIFIES_RISK", weight=round(ratio / 100, 4), properties={"source": "currency-exposure", "polarity": "context", "aiInfluenceLabel": currency + " 환율 민감도"})
    for sector in portfolio.sectors:
        label = str(sector.get("sector") or "기타")
        ratio = number(sector.get("ratio"))
        if ratio < policy.sector_exposure_min_pct and sector_positions.get(label, 0) < policy.sector_position_min_count:
            continue
        risk_id = add_entity(graph, "risk", label + "-correlation-risk", label + " 상관 리스크", {
            "tboxClass": "Risk",
            "tboxClasses": ["Risk", "ConcentrationRisk", "CorrelationRisk"],
            "sector": label,
            "sectorRatio": round(ratio, 2),
            "positionCount": sector_positions.get(label, 0),
        })
        add_relation(graph, portfolio_node_id, risk_id, "EXPOSED_TO", weight=round(ratio / 100, 4), properties={"source": "sector-correlation", "polarity": "context", "aiInfluenceLabel": label + " 섹터 상관 리스크"})









def add_position_factor_concepts(graph: PortfolioOntology, stock_id: str, portfolio_node_id: str, position: Position, portfolio: PortfolioSummary) -> None:
    symbol = symbol_key(position)
    benchmark_id, benchmark_label = benchmark_for_position(position)
    benchmark_entity_id = add_entity(graph, "benchmark-index", benchmark_id, benchmark_label, {
        "tboxClass": "BenchmarkIndex",
        "tboxClasses": ["BenchmarkIndex", "Factor"],
        "market": position.market,
    })
    add_relation(graph, stock_id, benchmark_entity_id, "HAS_BETA_TO", weight=0.6, properties={"source": "factor-map", "polarity": "context", "aiInfluenceLabel": benchmark_label + " 베타"})
    for label in factor_labels_for_position(position):
        factor_id = add_entity(graph, "factor", label, label, {
            "tboxClass": "Factor",
            "tboxClasses": ["Factor", "FactorExposure"],
            "label": label,
        })
        weight = round(position_weight(position, portfolio) / 100, 4) if is_holding_position(position) else 0.18
        props = {"source": "factor-map", "polarity": "context", "aiInfluenceLabel": label + " 팩터 노출"}
        add_relation(graph, stock_id, factor_id, "HAS_FACTOR_EXPOSURE", weight=weight or 0.18, properties=props)
        add_relation(graph, portfolio_node_id, factor_id, "HAS_FACTOR_EXPOSURE", weight=weight or 0.18, properties=props)
