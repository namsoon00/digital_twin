from typing import Dict, Iterable, List

from .market_data import number
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation
from .portfolio import Position
from .security_lines import SecurityLine, security_lines_for_symbol


def settings_from_runtime(runtime_context: Dict[str, object] = None) -> Dict[str, object]:
    if not isinstance(runtime_context, dict):
        return {}
    settings = runtime_context.get("settings")
    return settings if isinstance(settings, dict) else runtime_context


def position_by_symbol(positions: Iterable[Position]) -> Dict[str, Position]:
    return {
        str(position.symbol or "").upper().strip(): position
        for position in positions or []
        if str(position.symbol or "").strip()
    }


def equity_quote(external_signals: Dict[str, object], symbol: str) -> Dict[str, object]:
    quotes = external_signals.get("equityQuotes") if isinstance(external_signals, dict) else {}
    quote = quotes.get(str(symbol or "").upper().strip()) if isinstance(quotes, dict) else {}
    return quote if isinstance(quote, dict) else {}


def fx_usd_krw(external_signals: Dict[str, object], runtime_context: Dict[str, object] = None) -> float:
    rates = external_signals.get("fxRates") if isinstance(external_signals, dict) else {}
    if isinstance(rates, dict):
        for key in ["USDKRW", "USD/KRW", "USD"]:
            item = rates.get(key)
            if isinstance(item, dict):
                rate = number(item.get("rate") if item.get("rate") not in (None, "") else item.get("value"))
            else:
                rate = number(item)
            if rate > 0:
                return rate
    settings = settings_from_runtime(runtime_context)
    for raw_line in str(settings.get("fxRates") or "").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        if key.strip().upper() == "USD":
            return number(value)
    return 0.0


def line_tbox_classes(line: SecurityLine) -> List[str]:
    if line.is_local:
        return ["Instrument", "Security", "Equity", "Stock", "LocalOrdinaryShare", "SecurityLine"]
    if line.is_adr:
        return ["Instrument", "Security", "Equity", "DepositaryReceipt", "ADR", "SecurityLine"]
    if line.is_leveraged:
        classes = ["Instrument", "ETF", "SingleStockETF", "DailyResetLeverage", "SecurityLine"]
        if abs(line.leverage_factor) >= 2:
            classes.append("LeveragedETF")
        if line.leverage_factor < 0 or "inverse" in line.role:
            classes.append("InverseETF")
        return classes
    return ["Instrument", "Security", "SecurityLine"]


def line_tbox_class(line: SecurityLine) -> str:
    if line.is_local:
        return "LocalOrdinaryShare"
    if line.is_adr:
        return "ADR"
    if line.is_leveraged:
        if line.leverage_factor < 0 or "inverse" in line.role:
            return "InverseETF"
        return "LeveragedETF"
    return "SecurityLine"


def quote_price_for_line(line: SecurityLine, positions_by_symbol: Dict[str, Position], external_signals: Dict[str, object]) -> float:
    position = positions_by_symbol.get(line.symbol)
    if position and number(position.current_price) > 0:
        return number(position.current_price)
    quote = equity_quote(external_signals, line.symbol)
    return number(quote.get("price") or quote.get("currentPrice"))


def quote_volume_for_line(line: SecurityLine, positions_by_symbol: Dict[str, Position], external_signals: Dict[str, object]) -> float:
    position = positions_by_symbol.get(line.symbol)
    if position and number(position.volume) > 0:
        return number(position.volume)
    quote = equity_quote(external_signals, line.symbol)
    return number(quote.get("volume"))


def latest_trading_day_for_line(line: SecurityLine, external_signals: Dict[str, object]) -> str:
    quote = equity_quote(external_signals, line.symbol)
    return str(quote.get("latestTradingDay") or quote.get("observedAt") or quote.get("fetchedAt") or "")


def add_cross_listing_coverage_gap(
    graph: PortfolioOntology,
    stock_id: str,
    local_symbol: str,
    label: str,
    field: str,
    description: str,
    observed_at: str = "",
) -> None:
    gap_id = add_entity(graph, "coverage-gap", local_symbol + ":cross-listing:" + field, label, {
        "tboxClass": "CoverageGap",
        "tboxClasses": ["Observation", "DataQuality", "CoverageGap", "MissingData"],
        "symbol": local_symbol,
        "field": field,
        "dataScope": "crossListedSecurity",
        "domainScope": "securityLine",
        "description": description,
        "impact": "ADR 프리미엄과 레버리지 수급 증폭 판단의 신뢰도를 낮춥니다.",
        "reviewLevel": "blocked",
        "dataState": "insufficient",
        "evidenceRole": "blocking",
        "source": "security-line-ontology",
        "observationDomain": "data-quality",
        "freshnessRequired": True,
        "freshnessStatus": "unknown",
        "sourceAsOf": observed_at,
        "sourceFetchedAt": observed_at,
        "sourceTimestampPresent": bool(observed_at),
        "maxAgeMinutes": 60,
    })
    add_relation(graph, stock_id, gap_id, "HAS_COVERAGE_GAP", weight=0.8, properties={
        "source": "security-line-ontology",
        "field": field,
        "dataScope": "crossListedSecurity",
        "polarity": "blocking",
        "reviewLevel": "blocked",
        "dataState": "insufficient",
        "evidenceRole": "blocking",
        "aiInfluenceLabel": label,
    })


def add_security_line_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    observed_positions: Iterable[Position],
    external_signals: Dict[str, object] = None,
    runtime_context: Dict[str, object] = None,
) -> None:
    settings = settings_from_runtime(runtime_context)
    lines = security_lines_for_symbol(position.symbol, settings)
    if not lines:
        return
    external_signals = external_signals or {}
    positions_by_symbol = position_by_symbol(observed_positions)
    local_symbol = str(position.symbol or "").upper().strip()
    local_line = next((line for line in lines if line.local_symbol == local_symbol and line.is_local), None)
    if local_line is None:
        local_line = next((line for line in lines if line.is_local), None)
    company_name = (local_line.company_name if local_line else position.name) or position.name or local_symbol
    company_id = entity_id("company", local_symbol)
    usd_krw = fx_usd_krw(external_signals, runtime_context)
    observation_time = str((runtime_context or {}).get("asOf") or position.source_fetched_at or position.updated_at or "")
    local_price = number(position.current_price)
    local_line_id = ""
    adr_lines = [line for line in lines if line.is_adr]
    leveraged_lines = [line for line in lines if line.is_leveraged]

    for line in lines:
        line_id = add_entity(graph, "security-line", local_symbol + ":" + line.symbol, line.label, {
            "tboxClass": line_tbox_class(line),
            "tboxClasses": line_tbox_classes(line),
            "companyName": company_name,
            "localSymbol": line.local_symbol,
            "symbol": line.symbol,
            "securityLineRole": line.role,
            "market": line.market,
            "currency": line.currency,
            "exchange": line.exchange,
            "adrRatio": line.adr_ratio,
            "conversionStartDate": line.conversion_start_date,
            "listingDate": line.listing_date,
            "leverageFactor": line.leverage_factor,
            "underlyingSymbol": line.underlying_symbol,
            "sourceUrl": line.source_url,
            "source": "security-line-catalog:" + line.source,
        })
        add_relation(graph, company_id, line_id, "ISSUES_SECURITY_LINE", weight=0.92, properties={
            "source": "security-line-catalog",
            "securityLineRole": line.role,
            "aiInfluenceLabel": line.label,
        })
        add_relation(graph, stock_id, line_id, "HAS_SECURITY_LINE", weight=0.86, properties={
            "source": "security-line-catalog",
            "securityLineRole": line.role,
            "aiInfluenceLabel": line.label,
        })
        if line.is_local:
            local_line_id = line_id

    if local_line_id:
        for line in adr_lines:
            adr_line_id = entity_id("security-line", local_symbol + ":" + line.symbol)
            add_relation(graph, adr_line_id, local_line_id, "REPRESENTS_ECONOMIC_CLAIM", weight=0.9, properties={
                "source": "security-line-catalog",
                "adrRatio": line.adr_ratio,
                "conversionStartDate": line.conversion_start_date,
                "listingDate": line.listing_date,
                "aiInfluenceLabel": line.symbol + " ADR과 " + local_symbol + " 본주 연결",
            })
            friction_id = add_entity(graph, "arbitrage-friction", local_symbol + ":" + line.symbol, line.symbol + " ADR 차익거래 제약", {
                "tboxClass": "ArbitrageFriction",
                "tboxClasses": ["Risk", "MarketStructureRisk", "ArbitrageFriction"],
                "symbol": local_symbol,
                "adrSymbol": line.symbol,
                "conversionStartDate": line.conversion_start_date,
                "listingDate": line.listing_date,
                "field": "conversionWindow",
                "value": 1 if line.conversion_start_date else 0,
                "source": "security-line-catalog",
            })
            add_relation(graph, stock_id, friction_id, "HAS_ARBITRAGE_FRICTION", weight=0.74, properties={
                "source": "security-line-catalog",
                "polarity": "risk",
                "aiInfluenceLabel": line.symbol + " 전환·차익거래 제약",
            })

    for line in adr_lines:
        adr_price = quote_price_for_line(line, positions_by_symbol, external_signals)
        adr_volume = quote_volume_for_line(line, positions_by_symbol, external_signals)
        if not adr_price:
            add_cross_listing_coverage_gap(
                graph,
                stock_id,
                local_symbol,
                line.symbol + " ADR 시세 부족",
                "adrPrice",
                line.symbol + " ADR 가격이 없어 ADR 프리미엄을 계산하지 못합니다.",
                observation_time,
            )
            continue
        if not usd_krw:
            add_cross_listing_coverage_gap(
                graph,
                stock_id,
                local_symbol,
                "USD/KRW 환율 부족",
                "usdKrwRate",
                "ADR 달러 가격을 본주 원화 가치와 비교할 USD/KRW가 없습니다.",
                observation_time,
            )
            continue
        if not local_price:
            add_cross_listing_coverage_gap(
                graph,
                stock_id,
                local_symbol,
                local_symbol + " 본주 시세 부족",
                "localSharePrice",
                "본주 현재가가 없어 ADR 프리미엄을 계산하지 못합니다.",
                observation_time,
            )
            continue
        if not line.adr_ratio:
            add_cross_listing_coverage_gap(
                graph,
                stock_id,
                local_symbol,
                line.symbol + " ADR ratio 부족",
                "adrRatio",
                "ADR 1주가 본주 몇 주를 대표하는지 없어 가격 괴리를 계산하지 못합니다.",
                observation_time,
            )
            continue
        local_equivalent_krw = adr_price * usd_krw / line.adr_ratio
        premium_pct = ((local_equivalent_krw / local_price) - 1) * 100 if local_price else 0.0
        premium_source_as_of = latest_trading_day_for_line(line, external_signals) or position.source_as_of or position.updated_at
        premium_id = add_entity(graph, "cross-market-premium", local_symbol + ":" + line.symbol, line.symbol + " ADR 프리미엄", {
            "tboxClass": "ADRPremium",
            "tboxClasses": ["Observation", "PriceObservation", "CrossMarketPremium", "ADRPremium", "MarketStructureSignal"],
            "symbol": local_symbol,
            "adrSymbol": line.symbol,
            "field": "adrPremiumPct",
            "value": round(premium_pct, 2),
            "adrPriceUsd": round(adr_price, 4),
            "adrVolume": round(adr_volume, 2),
            "usdKrwRate": round(usd_krw, 4),
            "adrRatio": line.adr_ratio,
            "localPriceKrw": round(local_price, 4),
            "localEquivalentKrw": round(local_equivalent_krw, 2),
            "latestTradingDay": premium_source_as_of,
            "observationDomain": "quote",
            "freshnessRequired": True,
            "freshnessStatus": "unknown",
            "sourceAsOf": premium_source_as_of,
            "sourceFetchedAt": position.source_fetched_at or position.updated_at,
            "sourceTimestampPresent": bool(premium_source_as_of),
            "maxAgeMinutes": 1440,
            "source": "security-line-ontology",
        })
        add_relation(graph, stock_id, premium_id, "HAS_ADR_PREMIUM", weight=min(1.0, max(0.3, abs(premium_pct) / 50)), properties={
            "source": "security-line-ontology",
            "field": "adrPremiumPct",
            "polarity": "risk" if abs(premium_pct) >= 10 else "context",
            "aiInfluenceLabel": line.symbol + " ADR 프리미엄 " + str(round(premium_pct, 1)) + "%",
        })

    for line in leveraged_lines:
        etf_id = entity_id("security-line", local_symbol + ":" + line.symbol)
        underlying_id = entity_id("security-line", local_symbol + ":" + (line.underlying_symbol or line.symbol))
        add_relation(graph, etf_id, underlying_id, "TRACKS_UNDERLYING", weight=0.9, properties={
            "source": "security-line-catalog",
            "field": "leverageFactor",
            "value": abs(line.leverage_factor),
            "polarity": "risk" if abs(line.leverage_factor) >= 2 else "context",
            "aiInfluenceLabel": line.symbol + " -> " + (line.underlying_symbol or "underlying") + " 일일 추종",
        })
        add_relation(graph, stock_id, etf_id, "HAS_LEVERAGED_PRODUCT", weight=min(1.0, abs(line.leverage_factor) / 2.0), properties={
            "source": "security-line-catalog",
            "field": "leverageFactor",
            "value": abs(line.leverage_factor),
            "signalGroup": "leveragedProduct",
            "polarity": "risk",
            "aiInfluenceLabel": line.symbol + " 레버리지 상품",
        })
        quote_price = quote_price_for_line(line, positions_by_symbol, external_signals)
        quote_volume = quote_volume_for_line(line, positions_by_symbol, external_signals)
        if quote_price or quote_volume:
            flow_id = add_entity(graph, "leveraged-flow-signal", local_symbol + ":" + line.symbol, line.symbol + " 레버리지 수급 신호", {
                "tboxClass": "RebalancingFlow",
                "tboxClasses": ["Observation", "FlowObservation", "LeveragedETF", "DailyResetLeverage", "RebalancingFlow", "FlowAmplificationRisk"],
                "symbol": local_symbol,
                "etfSymbol": line.symbol,
                "underlyingSymbol": line.underlying_symbol,
                "field": "leverageFactor",
                "value": abs(line.leverage_factor),
                "leverageFactor": line.leverage_factor,
                "price": round(quote_price, 4),
                "volume": round(quote_volume, 2),
                "latestTradingDay": latest_trading_day_for_line(line, external_signals),
                "source": "security-line-ontology",
            })
            add_relation(graph, stock_id, flow_id, "HAS_LEVERAGED_FLOW_SIGNAL", weight=min(1.0, abs(line.leverage_factor) / 2.0), properties={
                "source": "security-line-ontology",
                "field": "leverageFactor",
                "signalGroup": "leveragedProduct",
                "polarity": "risk",
                "aiInfluenceLabel": line.symbol + " 일일 리셋 레버리지 수급",
            })
        else:
            add_cross_listing_coverage_gap(
                graph,
                stock_id,
                local_symbol,
                line.symbol + " 레버리지 ETF 시세 부족",
                "leveragedEtfQuote:" + line.symbol,
                line.symbol + " 가격·거래량이 없어 레버리지 ETF 수급 증폭 여부를 계산하지 못합니다.",
                observation_time,
            )
