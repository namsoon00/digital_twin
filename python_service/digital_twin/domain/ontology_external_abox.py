from typing import Dict, Iterable, List

from .market_data import number
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation
from .parsing import parse_assignments
from .portfolio import PortfolioSummary, Position


SENSITIVE_SIGNAL_TOKENS = ("secret", "token", "password", "clientid", "client_id", "accountseq", "account_seq", "chatid", "chat_id", "key")

RATE_SERIES_LABELS = {
    "DGS10": "미국 10년 국채금리",
    "DGS2": "미국 2년 국채금리",
    "DFF": "미국 실효 연방기금금리",
}


def unique_list(values: Iterable[str]) -> List[str]:
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


def is_watchlist_position(position: Position) -> bool:
    return str(getattr(position, "source", "") or "holding").strip().lower() == "watchlist"


def is_holding_position(position: Position) -> bool:
    return not is_watchlist_position(position) and (number(position.market_value) > 0 or number(position.quantity) > 0)


def safe_signal_value(key: str, value: object) -> object:
    lowered = str(key or "").replace("-", "").replace("_", "").lower()
    if any(token.replace("_", "") in lowered for token in SENSITIVE_SIGNAL_TOKENS):
        return "configured" if value not in (None, "", False) else ""
    text = str(value or "")
    return text[:1200] if len(text) > 1200 else value


def external_signal_classes(group: str) -> List[str]:
    text = str(group or "").lower()
    classes = ["Observation", "ExternalObservation", "ExternalSignal", "Signal"]
    if "dart" in text or "disclosure" in text or "filing" in text:
        classes.extend(["DisclosureEvent", "DisclosureSignal", "EventRisk"])
    if "overview" in text or "fundamental" in text:
        classes.extend(["FundamentalObservation", "ValuationSignal"])
    if "earning" in text:
        classes.extend(["EarningsEvent", "EarningsCalendarEvent", "ValuationSignal"])
    if "analyst" in text:
        classes.extend(["AnalystRevision", "ValuationSignal"])
    if "news" in text or "headline" in text:
        classes.extend(["NewsEvent", "EventRisk"])
    if "macro" in text or "rate" in text or "yield" in text:
        classes.extend(["MacroIndicator", "MacroSignal", "RateSignal", "RegimeRisk"])
    if "fx" in text or "currency" in text or "exchange" in text:
        classes.extend(["FXRateSignal", "CurrencyRisk", "MacroSignal"])
    if "credit" in text or "spread" in text:
        classes.extend(["CreditSpreadSignal", "MacroSignal", "RegimeRisk"])
    if "crypto" in text or "coin" in text or "btc" in text:
        classes.extend(["CryptoMarketSignal", "CryptoSignal"])
    if "earning" in text or "result" in text:
        classes.extend(["EarningsEvent", "ValuationSignal"])
    if "regulat" in text or "policy" in text:
        classes.extend(["RegulatoryEvent", "EventRisk"])
    return unique_list(classes)


CORPORATE_ACTION_TERMS = {
    "자기주식": "treasury-stock",
    "배당": "dividend",
    "유상증자": "rights-offering",
    "무상증자": "bonus-issue",
    "증자": "capital-increase",
    "감자": "capital-reduction",
    "합병": "merger",
    "분할": "spin-off",
    "주식교환": "share-swap",
    "주식이전": "share-transfer",
    "공개매수": "tender-offer",
    "전환사채": "convertible-bond",
    "신주인수권": "warrant",
}

REGULATORY_EVENT_TERMS = {
    "소송": "litigation",
    "제재": "sanction",
    "조사": "investigation",
    "과징금": "penalty",
    "벌금": "fine",
    "상장폐지": "delisting",
    "관리종목": "watchlist-designation",
    "불성실": "disclosure-violation",
    "감사의견": "audit-opinion",
    "횡령": "embezzlement",
    "배임": "breach-of-trust",
}

EARNINGS_EVENT_TERMS = ["10-K", "10-Q", "20-F", "40-F", "사업보고서", "분기보고서", "반기보고서", "영업실적", "실적", "earnings"]


def first_matching_term(text: str, term_map: Dict[str, str]) -> str:
    blob = str(text or "").lower()
    for term, value in term_map.items():
        if term.lower() in blob:
            return value
    return ""


def looks_like_earnings_event(text: str) -> bool:
    blob = str(text or "").lower()
    return any(term.lower() in blob for term in EARNINGS_EVENT_TERMS)


def first_fact(facts: Dict[str, object], keys: List[str]) -> Dict[str, object]:
    for key in keys:
        item = facts.get(key) if isinstance(facts.get(key), dict) else {}
        if item:
            return item
    return {}


def rate_series_label(series_id: str) -> str:
    normalized = str(series_id or "").upper().strip()
    return RATE_SERIES_LABELS.get(normalized, "FRED " + normalized)


def rate_series_kind(series_id: str) -> str:
    normalized = str(series_id or "").upper().strip()
    return "interest-rate" if normalized in RATE_SERIES_LABELS or normalized.startswith("DGS") else "macro-print"


def rate_series_classes(series_id: str) -> List[str]:
    normalized = str(series_id or "").upper().strip()
    classes = ["Observation", "ExternalObservation", "ExternalSignal", "MacroIndicator", "MacroSignal"]
    if rate_series_kind(normalized) == "interest-rate":
        classes.extend(["RateSignal", "InterestRate", "RegimeRisk"])
    else:
        classes.append("MacroPrint")
    return unique_list(classes)


def fx_rate_entries(external_signals: Dict[str, object], runtime_context: Dict[str, object] = None) -> Dict[str, Dict[str, object]]:
    entries: Dict[str, Dict[str, object]] = {}
    rates = external_signals.get("fxRates") if isinstance(external_signals, dict) else {}
    rates = rates if isinstance(rates, dict) else {}

    def add_entry(key: str, value: object, source: str = "externalSignals") -> None:
        if isinstance(value, dict):
            base = str(value.get("base") or value.get("baseCurrency") or "").upper().strip()
            quote = str(value.get("quote") or value.get("quoteCurrency") or "").upper().strip()
            rate = number(value.get("rate") if value.get("rate") not in (None, "") else value.get("value"))
            provider = str(value.get("provider") or source)
            fetched_at = str(value.get("fetchedAt") or value.get("observedAt") or "")
            source_type = str(value.get("sourceType") or value.get("source_type") or "")
            evidence_strength = str(value.get("evidenceStrength") or value.get("evidence_strength") or "")
            market_rate = number(value.get("marketRate"))
            valuation_rate = number(value.get("valuationRate"))
        else:
            base = str(key or "").upper().strip()
            quote = "KRW"
            rate = number(value)
            provider = source
            fetched_at = ""
            source_type = "fallback_setting" if source == "runtimeSettings" else ""
            evidence_strength = "fallback" if source == "runtimeSettings" else ""
            market_rate = 0.0
            valuation_rate = 0.0
        normalized_key = str(key or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()
        if not base and len(normalized_key) >= 6:
            base = normalized_key[:3]
        if not quote and len(normalized_key) >= 6:
            quote = normalized_key[3:6]
        if not quote:
            quote = "KRW"
        if not base or base == quote or rate <= 0:
            return
        pair = base + quote
        entries[pair] = {
            "pair": pair,
            "base": base,
            "quote": quote,
            "rate": rate,
            "provider": provider,
            "fetchedAt": fetched_at,
            "sourceType": source_type,
            "evidenceStrength": evidence_strength,
            "marketRate": market_rate,
            "valuationRate": valuation_rate,
        }

    for key, value in sorted(rates.items()):
        add_entry(str(key), value, "externalSignals")
    settings = runtime_context.get("settings") if isinstance(runtime_context, dict) else {}
    settings = settings if isinstance(settings, dict) else {}
    for currency, rate in parse_assignments(str(settings.get("fxRates") or ""), {}).items():
        base = str(currency or "").upper().strip()
        if base and base != "KRW" and base + "KRW" not in entries:
            add_entry(base, rate, "runtimeSettings")
    return entries


def interest_rate_signal_ids(external_signals: Dict[str, object]) -> Dict[str, str]:
    macro = external_signals.get("macro") if isinstance(external_signals, dict) and isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    ids = {}
    for series_id in series.keys():
        ids[str(series_id).upper()] = entity_id(rate_series_kind(str(series_id)), str(series_id))
    if "yieldSpread10y2y" in macro:
        ids["YIELDSPREAD10Y2Y"] = entity_id("yield-curve", "yieldSpread10y2y")
    return ids


def rate_sensitive_position(position: Position) -> bool:
    market = str(position.market or "").upper().strip()
    currency = str(position.currency or "").upper().strip()
    sector = str(position.sector or "").strip()
    symbol = str(position.symbol or "").upper().strip()
    return (
        market in {"US", "USA", "NASDAQ", "NYSE"}
        or currency == "USD"
        or any(token in sector for token in ["반도체", "AI", "플랫폼", "모빌리티", "디지털자산"])
        or symbol in {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}
    )


def crypto_sensitive_position(position: Position) -> bool:
    market = str(position.market or "").upper().strip()
    currency = str(position.currency or "").upper().strip()
    sector = str(position.sector or "").lower().strip()
    symbol = str(position.symbol or "").upper().strip()
    name = str(position.name or "").lower().strip()
    return (
        symbol in {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}
        or any(token in sector for token in ["디지털자산", "crypto", "bitcoin", "비트코인"])
        or any(token in name for token in ["strategy", "bitcoin", "crypto", "스트래티지", "비트코인"])
        or (market in {"US", "USA", "NASDAQ", "NYSE"} and currency == "USD" and "디지털" in sector)
    )


def macro_series_value(macro: Dict[str, object], series_id: str) -> float:
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    item = series.get(series_id) if isinstance(series.get(series_id), dict) else {}
    return number(item.get("value"))


def macro_delta_bp(macro: Dict[str, object], key: str) -> float:
    candidates = [
        key + "DeltaBp",
        key[0].lower() + key[1:] + "DeltaBp" if key else "",
    ]
    for candidate in candidates:
        if candidate and macro.get(candidate) not in (None, ""):
            return number(macro.get(candidate))
    return 0.0


def macro_regime_profile(macro: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(macro, dict):
        return {}
    dgs10 = macro_series_value(macro, "DGS10")
    dgs2 = macro_series_value(macro, "DGS2")
    dff = macro_series_value(macro, "DFF")
    spread = number(macro.get("yieldSpread10y2y")) if macro.get("yieldSpread10y2y") not in (None, "") else dgs10 - dgs2
    if not any([dgs10, dgs2, dff, spread]):
        return {}
    dgs10_delta = macro_delta_bp(macro, "dgs10")
    spread_delta = macro_delta_bp(macro, "yieldSpread10y2y")
    high_rate = bool(dgs10 and dgs10 >= 4.5)
    inverted_curve = bool(spread < 0)
    rate_shock = bool(max(abs(dgs10_delta), abs(spread_delta)) >= 15)
    if high_rate and inverted_curve:
        regime = "high-rate-inverted-curve"
        label = "고금리·역전 수익률곡선"
        polarity = "risk"
        impact = 9.0
    elif high_rate:
        regime = "high-rate"
        label = "고금리 레짐"
        polarity = "risk"
        impact = 7.0
    elif inverted_curve:
        regime = "inverted-curve"
        label = "역전 수익률곡선"
        polarity = "risk"
        impact = 6.5
    elif rate_shock:
        regime = "rate-shock"
        label = "금리 변화 확대"
        polarity = "risk"
        impact = 6.0
    else:
        regime = "rate-context"
        label = "금리 맥락"
        polarity = "context"
        impact = 0.0
    return {
        "regime": regime,
        "label": label,
        "polarity": polarity,
        "opinionImpact": impact,
        "dgs10": round(dgs10, 4),
        "dgs2": round(dgs2, 4),
        "dff": round(dff, 4),
        "yieldSpread10y2y": round(spread, 4),
        "dgs10DeltaBp": round(dgs10_delta, 2),
        "yieldSpreadDeltaBp": round(spread_delta, 2),
        "highRate": high_rate,
        "invertedCurve": inverted_curve,
        "rateShock": rate_shock,
    }


def add_external_signal_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    external_signals: Dict[str, object],
    runtime_context: Dict[str, object] = None,
) -> None:
    if not isinstance(external_signals, dict):
        return
    add_external_signal_quality_concepts(graph, portfolio_node_id, external_signals)
    add_portfolio_macro_and_cross_asset_concepts(graph, portfolio_node_id, external_signals, runtime_context)
    for key, value in sorted(external_signals.items()):
        if key in {"quality", "freshness", "provenance"}:
            continue
        signal_id = add_entity(graph, "external-signal", key, str(key), {
            "tboxClass": "ExternalSignal",
            "tboxClasses": external_signal_classes(str(key)),
            "key": str(key),
            "value": safe_signal_value(str(key), value),
        })
        properties = {"source": "external-signals", "aiInfluenceLabel": "외부 신호 " + str(key)}
        if isinstance(value, dict):
            magnitude = max([abs(number(item)) for item in value.values()] + [0.0])
            if magnitude >= 3:
                properties.update({"polarity": "risk", "opinionImpact": min(12.0, magnitude)})
        add_relation(graph, portfolio_node_id, signal_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=properties)
        add_relation(graph, portfolio_node_id, signal_id, "HAS_OBSERVATION", weight=1.0, properties=properties)


def add_portfolio_macro_and_cross_asset_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    external_signals: Dict[str, object],
    runtime_context: Dict[str, object] = None,
) -> None:
    macro = external_signals.get("macro") if isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    regime = macro_regime_profile(macro)
    if regime:
        regime_id = add_entity(graph, "macro-regime", "rates", str(regime.get("label") or "거시 레짐"), {
            "tboxClass": "MacroRegime",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "MacroIndicator", "MacroRegime", "MacroSignal", "RegimeRisk"],
            "source": "macro",
            **regime,
        })
        regime_props = {
            "source": "macro",
            "polarity": str(regime.get("polarity") or "context"),
            "opinionImpact": number(regime.get("opinionImpact")),
            "aiInfluenceLabel": str(regime.get("label") or "거시 레짐"),
            "dataScope": "macro",
        }
        add_relation(graph, portfolio_node_id, regime_id, "HAS_MACRO_REGIME", weight=0.78, properties=regime_props)
        add_relation(graph, regime_id, portfolio_node_id, "AFFECTS", weight=0.70, properties=regime_props)
    for series_id, item in sorted(series.items()):
        if not isinstance(item, dict):
            continue
        value = number(item.get("value"))
        macro_id = add_entity(graph, rate_series_kind(str(series_id)), str(series_id), rate_series_label(str(series_id)), {
            "tboxClass": "InterestRate" if rate_series_kind(str(series_id)) == "interest-rate" else "MacroPrint",
            "tboxClasses": rate_series_classes(str(series_id)),
            "seriesId": str(series_id),
            "provider": str(item.get("provider") or "FRED"),
            "date": str(item.get("date") or ""),
            "value": round(value, 4),
        })
        props = {"source": "macro", "polarity": "context", "aiInfluenceLabel": rate_series_label(str(series_id))}
        if str(series_id).upper() in {"DGS10", "DGS2", "DFF"}:
            props.update({"polarity": "risk" if value >= 4.5 else "context", "opinionImpact": 5.0 if value >= 4.5 else 0.0})
        add_relation(graph, portfolio_node_id, macro_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        if rate_series_kind(str(series_id)) == "interest-rate":
            add_relation(graph, portfolio_node_id, macro_id, "HAS_RATE_SENSITIVITY", weight=0.72, properties=props)
        add_relation(graph, macro_id, portfolio_node_id, "AFFECTS", weight=0.65, properties=props)
    if "yieldSpread10y2y" in macro:
        spread = number(macro.get("yieldSpread10y2y"))
        spread_id = add_entity(graph, "yield-curve", "yieldSpread10y2y", "10Y-2Y 금리 스프레드", {
            "tboxClass": "YieldCurve",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "MacroIndicator", "RateSignal", "YieldCurve", "CreditSpreadSignal", "RegimeRisk"],
            "value": round(spread, 4),
        })
        props = {"source": "macro", "polarity": "risk" if spread < 0 else "context", "opinionImpact": 6.0 if spread < 0 else 0.0, "aiInfluenceLabel": "금리 스프레드 레짐"}
        add_relation(graph, portfolio_node_id, spread_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, portfolio_node_id, spread_id, "HAS_RATE_SENSITIVITY", weight=0.74, properties=props)
        add_relation(graph, spread_id, portfolio_node_id, "AFFECTS", weight=0.72, properties=props)
    for pair, item in sorted(fx_rate_entries(external_signals, runtime_context).items()):
        base = str(item.get("base") or "").upper()
        quote = str(item.get("quote") or "").upper()
        rate = number(item.get("rate"))
        pair_label = base + "/" + quote
        pair_id = add_entity(graph, "fx-pair", base + ":" + quote, pair_label + " 환율쌍", {
            "tboxClass": "FXPair",
            "baseCurrency": base,
            "quoteCurrency": quote,
        })
        rate_id = add_entity(graph, "fx-rate", pair, pair_label + " 환율", {
            "tboxClass": "FXRateSignal",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "FXRateSignal", "MacroSignal", "CurrencyRisk"],
            "pair": pair,
            "baseCurrency": base,
            "quoteCurrency": quote,
            "rate": round(rate, 6),
            "provider": str(item.get("provider") or ""),
            "fetchedAt": str(item.get("fetchedAt") or ""),
            "sourceType": str(item.get("sourceType") or ""),
            "evidenceStrength": str(item.get("evidenceStrength") or ""),
            "marketRate": round(number(item.get("marketRate")), 6) if number(item.get("marketRate")) else 0.0,
            "valuationRate": round(number(item.get("valuationRate")), 6) if number(item.get("valuationRate")) else 0.0,
        })
        props = {
            "source": "fxRates",
            "polarity": "context",
            "aiInfluenceLabel": pair_label + " 환율",
            "sourceType": str(item.get("sourceType") or ""),
            "evidenceStrength": str(item.get("evidenceStrength") or ""),
        }
        if base == "USD" and quote == "KRW" and (rate >= 1450 or (rate and rate <= 1300)):
            props.update({"polarity": "risk", "opinionImpact": 4.0})
        add_relation(graph, pair_id, rate_id, "HAS_OBSERVATION", weight=1.0, properties=props)
        add_relation(graph, portfolio_node_id, rate_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, portfolio_node_id, rate_id, "HAS_FX_EXPOSURE", weight=0.7, properties=props)
        add_relation(graph, rate_id, portfolio_node_id, "AFFECTS", weight=0.64, properties=props)
    crypto = external_signals.get("cryptoMarkets") if isinstance(external_signals.get("cryptoMarkets"), dict) else {}
    for coin_id, item in sorted(crypto.items()):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or coin_id).upper().strip()
        name = str(item.get("name") or coin_id)
        change24h = number(item.get("change24h"))
        change7d = number(item.get("change7d"))
        price = number(item.get("price"))
        volume24h = number(item.get("volume24h"))
        asset_id = add_entity(graph, "crypto-asset", symbol or str(coin_id), name, {
            "tboxClass": "CryptoAsset",
            "tboxClasses": ["Instrument", "CryptoAsset"],
            "symbol": symbol,
            "coinId": str(coin_id),
            "provider": str(item.get("provider") or "CoinGecko"),
        })
        crypto_id = add_entity(graph, "crypto-market-signal", str(coin_id), str(item.get("name") or coin_id), {
            "tboxClass": "CryptoMarketSignal",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "CryptoMarketSignal", "CryptoSignal"],
            "provider": str(item.get("provider") or "CoinGecko"),
            "symbol": symbol,
            "price": round(price, 4),
            "change24h": round(change24h, 2),
            "change7d": round(change7d, 2),
            "volume24h": round(volume24h, 2),
            "marketCap": round(number(item.get("marketCap")), 2),
            "lastUpdated": str(item.get("lastUpdated") or ""),
        })
        magnitude = max(abs(change24h), abs(change7d))
        props = {
            "source": "cryptoMarkets",
            "symbol": symbol,
            "polarity": "risk" if magnitude >= 4 else "context",
            "opinionImpact": min(12.0, magnitude) if magnitude >= 4 else 0.0,
            "aiInfluenceLabel": name + " 변동성",
            "dataScope": "crypto",
        }
        price_id = add_entity(graph, "price-bar", symbol + ":crypto:latest", name + " 현재 가격", {
            "tboxClass": "PriceBar",
            "tboxClasses": ["Observation", "PriceObservation", "PriceBar"],
            "symbol": symbol,
            "coinId": str(coin_id),
            "close": round(price, 4),
            "changeRate": round(change24h, 2),
            "volume": round(volume24h, 2),
            "observedAt": str(item.get("lastUpdated") or ""),
            "provider": str(item.get("provider") or "CoinGecko"),
        })
        path_id = add_entity(graph, "price-path", symbol + ":crypto:1h-24h-7d", name + " 가격 경로", {
            "tboxClass": "PricePath",
            "tboxClasses": ["Observation", "PriceObservation", "PricePath", "CryptoMarketSignal"],
            "symbol": symbol,
            "coinId": str(coin_id),
            "change1h": round(number(item.get("change1h")), 2),
            "change24h": round(change24h, 2),
            "change7d": round(change7d, 2),
            "pathState": crypto_path_state(change24h, change7d),
            "provider": str(item.get("provider") or "CoinGecko"),
        })
        volume_id = add_entity(graph, "volume-profile", symbol + ":crypto", name + " 거래량 프로파일", {
            "tboxClass": "VolumeProfile",
            "tboxClasses": ["Observation", "VolumeObservation", "FlowObservation", "VolumeProfile", "TradeFlow", "FlowSignal"],
            "symbol": symbol,
            "coinId": str(coin_id),
            "volume24h": round(volume24h, 2),
            "marketCap": round(number(item.get("marketCap")), 2),
            "turnoverPct": round((volume24h / number(item.get("marketCap"))) * 100, 3) if volume24h and number(item.get("marketCap")) else 0.0,
            "provider": str(item.get("provider") or "CoinGecko"),
        })
        liquidity_id = add_entity(graph, "liquidity-profile", symbol + ":crypto", name + " 크립토 유동성", {
            "tboxClass": "LiquidityProfile",
            "tboxClasses": ["Risk", "LiquidityRisk", "LiquidityProfile"],
            "symbol": symbol,
            "coinId": str(coin_id),
            "volume24h": round(volume24h, 2),
            "marketCap": round(number(item.get("marketCap")), 2),
            "liquidityScore": round(min(100.0, (volume24h / 1000000000) * 8), 2) if volume24h else 0.0,
            "provider": str(item.get("provider") or "CoinGecko"),
        })
        quality_id = add_entity(graph, "data-quality", symbol + ":crypto", name + " 크립토 데이터 품질", {
            "tboxClass": "DataQuality",
            "tboxClasses": ["Observation", "DataQuality", "DataQualitySignal"],
            "symbol": symbol,
            "provider": str(item.get("provider") or "CoinGecko"),
            "hasPrice": bool(price),
            "hasVolume24h": bool(volume24h),
            "hasChange24h": item.get("change24h") not in (None, ""),
            "lastUpdated": str(item.get("lastUpdated") or ""),
            "dataScope": "crypto",
        })
        add_relation(graph, portfolio_node_id, crypto_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, portfolio_node_id, asset_id, "HAS_CRYPTO_EXPOSURE", weight=0.72, properties=props)
        add_relation(graph, asset_id, crypto_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, asset_id, price_id, "HAS_PRICE", weight=1.0, properties=props)
        add_relation(graph, asset_id, path_id, "HAS_PRICE_PATH", weight=1.0, properties=props)
        add_relation(graph, asset_id, volume_id, "HAS_TRADE_FLOW", weight=1.0, properties={**props, "polarity": "context"})
        add_relation(graph, asset_id, liquidity_id, "HAS_LIQUIDITY_PROFILE", weight=0.72, properties={**props, "polarity": "context"})
        add_relation(graph, asset_id, quality_id, "HAS_DATA_QUALITY", weight=0.84, properties={**props, "polarity": "context", "opinionImpact": 0.0})
        add_relation(graph, crypto_id, portfolio_node_id, "AFFECTS", weight=min(1.0, magnitude / 10), properties=props)
        add_relation(graph, path_id, crypto_id, "CONFIRMS_SIGNAL", weight=0.76, properties=props)


def crypto_path_state(change24h: float, change7d: float) -> str:
    if change24h <= -4 and change7d <= -8:
        return "selloff-continues"
    if change24h >= 4 and change7d >= 8:
        return "rally-continues"
    if change24h >= 3 and change7d < 0:
        return "rebound-after-weakness"
    if change24h <= -3 and change7d > 0:
        return "pullback-after-strength"
    return "mixed"


def add_external_signal_quality_concepts(graph: PortfolioOntology, portfolio_node_id: str, external_signals: Dict[str, object]) -> None:
    quality = external_signals.get("quality") if isinstance(external_signals.get("quality"), dict) else {}
    freshness = external_signals.get("freshness") if isinstance(external_signals.get("freshness"), dict) else {}
    provenance = external_signals.get("provenance") if isinstance(external_signals.get("provenance"), dict) else {}
    if quality:
        quality_id = add_entity(graph, "data-quality", "externalSignals", "외부 신호 품질", {
            "tboxClass": "DataQuality",
            "tboxClasses": ["Observation", "DataQuality", "DataQualitySignal", "Provenance"],
            "qualityScore": number(quality.get("score")),
            "coverageScore": number(quality.get("coverageScore")),
            "sourceHealthScore": number(quality.get("sourceHealthScore")),
            "errorCount": number(quality.get("errorCount")),
            "symbolCoverage": quality.get("symbolCoverage") if isinstance(quality.get("symbolCoverage"), dict) else {},
        })
        relation_props = {"source": "external-signal-quality", "aiInfluenceLabel": "외부 신호 품질"}
        if number(quality.get("score")) < 60:
            relation_props.update({"polarity": "risk", "opinionImpact": round((60 - number(quality.get("score"))) * 0.25, 2)})
        add_relation(graph, portfolio_node_id, quality_id, "HAS_DATA_QUALITY", weight=round(number(quality.get("score")) / 100, 4), properties=relation_props)
        add_relation(graph, portfolio_node_id, quality_id, "HAS_OBSERVATION", weight=round(number(quality.get("score")) / 100, 4), properties=relation_props)
    if freshness:
        freshness_id = add_entity(graph, "data-freshness", "externalSignals-runtime", "외부 신호 신선도", {
            "tboxClass": "DataFreshness",
            "fetchedAt": str(freshness.get("fetchedAt") or ""),
            "ageMinutes": number(freshness.get("ageMinutes")),
            "status": str(freshness.get("status") or ""),
        })
        relation_props = {"source": "external-signal-freshness", "aiInfluenceLabel": "외부 신호 신선도"}
        if str(freshness.get("status") or "") == "stale":
            relation_props.update({"polarity": "risk", "opinionImpact": 8.0})
        add_relation(graph, portfolio_node_id, freshness_id, "HAS_DATA_FRESHNESS", weight=1.0, properties=relation_props)
    if provenance:
        provenance_id = add_entity(graph, "provenance", "externalSignals", "외부 신호 출처", {
            "tboxClass": "Provenance",
            "sources": list(provenance.get("sources") or [])[:12],
            "unavailableSources": list(provenance.get("unavailableSources") or [])[:12],
        })
        add_relation(graph, portfolio_node_id, provenance_id, "HAS_PROVENANCE", weight=1.0, properties={"source": "external-signal-provenance", "aiInfluenceLabel": "외부 신호 출처"})


def add_position_macro_context_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Dict[str, object],
    runtime_context: Dict[str, object],
) -> None:
    weight = round(position_weight(position, portfolio) / 100, 4) if is_holding_position(position) else 0.15
    context_weight = max(0.15, weight or 0.15)
    currency = str(position.currency or "").upper().strip()
    for pair, item in sorted(fx_rate_entries(external_signals, runtime_context).items()):
        base = str(item.get("base") or "").upper()
        quote = str(item.get("quote") or "").upper()
        if not currency or currency != base:
            continue
        rate_id = entity_id("fx-rate", pair)
        pair_label = base + "/" + quote
        props = {
            "source": "fxRates",
            "polarity": "context",
            "aiInfluenceLabel": pair_label + " 환율 민감도",
            "rate": round(number(item.get("rate")), 6),
        }
        add_relation(graph, stock_id, rate_id, "HAS_FX_EXPOSURE", weight=context_weight, properties=props)
        add_relation(graph, rate_id, stock_id, "AFFECTS", weight=0.62, properties=props)
    if not rate_sensitive_position(position):
        add_position_crypto_exposure_concepts(graph, stock_id, position, external_signals, weight)
        return
    macro = external_signals.get("macro") if isinstance(external_signals, dict) and isinstance(external_signals.get("macro"), dict) else {}
    regime = macro_regime_profile(macro)
    if regime:
        regime_id = entity_id("macro-regime", "rates")
        props = {
            "source": "macro",
            "polarity": str(regime.get("polarity") or "context"),
            "opinionImpact": number(regime.get("opinionImpact")),
            "aiInfluenceLabel": str(regime.get("label") or "거시 레짐") + " 민감도",
            "dataScope": "macro",
        }
        add_relation(graph, stock_id, regime_id, "HAS_MACRO_REGIME", weight=context_weight, properties=props)
        add_relation(graph, regime_id, stock_id, "AFFECTS", weight=0.60, properties=props)
    for series_id, rate_id in sorted(interest_rate_signal_ids(external_signals).items()):
        is_curve = series_id == "YIELDSPREAD10Y2Y"
        props = {
            "source": "macro",
            "polarity": "context",
            "aiInfluenceLabel": "금리 스프레드 민감도" if is_curve else rate_series_label(series_id) + " 민감도",
        }
        if series_id in {"DGS10", "DGS2", "DFF"}:
            props["rateSeriesId"] = series_id
        add_relation(graph, stock_id, rate_id, "HAS_RATE_SENSITIVITY", weight=context_weight, properties=props)
        add_relation(graph, rate_id, stock_id, "AFFECTS", weight=0.58 if is_curve else 0.62, properties=props)
    add_position_crypto_exposure_concepts(graph, stock_id, position, external_signals, weight)


def add_position_crypto_exposure_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    external_signals: Dict[str, object],
    weight: float,
) -> None:
    if not crypto_sensitive_position(position):
        return
    crypto = external_signals.get("cryptoMarkets") if isinstance(external_signals, dict) and isinstance(external_signals.get("cryptoMarkets"), dict) else {}
    if not crypto:
        return
    symbol = str(position.symbol or "").upper().strip()
    for coin_id, item in sorted(crypto.items()):
        if not isinstance(item, dict):
            continue
        coin_symbol = str(item.get("symbol") or coin_id).upper().strip()
        if coin_symbol not in {"BTC", "ETH"} and str(coin_id).lower() not in {"bitcoin", "ethereum"}:
            continue
        change24h = number(item.get("change24h"))
        change7d = number(item.get("change7d"))
        magnitude = max(abs(change24h), abs(change7d))
        exposure_id = add_entity(graph, "crypto-exposure", symbol + ":" + coin_symbol, (position.name or symbol) + " " + coin_symbol + " 노출", {
            "tboxClass": "CryptoExposure",
            "tboxClasses": ["Risk", "VolatilityRisk", "CryptoExposure"],
            "symbol": symbol,
            "cryptoSymbol": coin_symbol,
            "coinId": str(coin_id),
            "change24h": round(change24h, 2),
            "change7d": round(change7d, 2),
            "pathState": crypto_path_state(change24h, change7d),
            "exposureBasis": "business-model-or-sector",
        })
        props = {
            "source": "cryptoMarkets",
            "symbol": symbol,
            "cryptoSymbol": coin_symbol,
            "polarity": "risk" if magnitude >= 4 else "context",
            "opinionImpact": min(10.0, magnitude) if magnitude >= 4 else 0.0,
            "aiInfluenceLabel": coin_symbol + " 가격 경로 노출",
            "dataScope": "crypto",
        }
        add_relation(graph, stock_id, exposure_id, "HAS_CRYPTO_EXPOSURE", weight=max(0.18, weight or 0.15), properties=props)
        add_relation(graph, exposure_id, entity_id("crypto-asset", coin_symbol), "AFFECTS", weight=0.62, properties=props)


def symbol_external_signal_items(external_signals: Dict[str, object], symbol: str) -> List[Dict[str, object]]:
    if not isinstance(external_signals, dict):
        return []
    candidates = {str(symbol or "").upper(), str(symbol or "").lower(), str(symbol or "")}
    rows: List[Dict[str, object]] = []
    for group_key, group_value in sorted(external_signals.items()):
        if not isinstance(group_value, dict):
            continue
        matched_key = next((key for key in candidates if key in group_value), "")
        if not matched_key:
            continue
        rows.append({
            "group": str(group_key),
            "symbolKey": matched_key,
            "value": group_value.get(matched_key),
        })
    return rows


def external_signal_relation_properties(group: str, value: object) -> Dict[str, object]:
    properties = {"source": "external-signals", "signalGroup": group, "aiInfluenceLabel": "외부 신호 " + group}
    if isinstance(value, dict):
        count = number(value.get("count"))
        sentiment = number(value.get("sentiment") or value.get("score") or value.get("riskScore"))
        if count and group in {"newsHeadlines", "secFilings", "dartDisclosures"}:
            properties.update({"polarity": "context", "aiInfluenceLabel": group + " " + str(int(count)) + "건"})
        if sentiment < 0:
            properties.update({"polarity": "risk", "opinionImpact": min(12.0, abs(sentiment)), "aiInfluenceLabel": group + " 부정 신호"})
        elif sentiment > 0:
            properties.update({"polarity": "support", "supportImpact": min(10.0, sentiment), "aiInfluenceLabel": group + " 긍정 신호"})
    return properties


def add_symbol_external_signal_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, external_signals: Dict[str, object]) -> None:
    for row in symbol_external_signal_items(external_signals, symbol):
        group = str(row.get("group") or "")
        signal_id = add_entity(graph, "external-signal", symbol + ":" + group, group + " 외부 신호", {
            "tboxClass": "ExternalSignal",
            "tboxClasses": external_signal_classes(group),
            "symbol": symbol,
            "group": group,
            "value": safe_signal_value(group, row.get("value")),
        })
        add_relation(
            graph,
            stock_id,
            signal_id,
            "HAS_OBSERVATION",
            weight=1.0,
            properties=external_signal_relation_properties(group, row.get("value")),
        )
        add_relation(
            graph,
            stock_id,
            signal_id,
            "HAS_EXTERNAL_SIGNAL",
            weight=1.0,
            properties=external_signal_relation_properties(group, row.get("value")),
        )
        add_symbol_fundamental_event_concepts(graph, stock_id, symbol, group, row.get("value"))
        add_symbol_company_overview_concepts(graph, stock_id, symbol, group, row.get("value"))
        add_symbol_earnings_report_concepts(graph, stock_id, symbol, group, row.get("value"))


def add_symbol_fundamental_event_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, group: str, value: object) -> None:
    if not isinstance(value, dict) or group not in {"secFilings", "dartDisclosures"}:
        return
    latest = value.get("latestFiling") if isinstance(value.get("latestFiling"), dict) else {}
    facts = value.get("facts") if isinstance(value.get("facts"), dict) else {}
    label = "펀더멘털 이벤트"
    if group == "dartDisclosures":
        label = str(value.get("reportName") or "DART 공시 이벤트")
    elif latest:
        label = "SEC " + str(latest.get("form") or "filing") + " 이벤트"
    event_id = add_entity(graph, "fundamental-event", symbol + ":" + group, label, {
        "tboxClass": "FundamentalObservation",
        "tboxClasses": ["Observation", "ExternalObservation", "FundamentalObservation", "ExternalSignal", "DisclosureEvent", "EarningsEvent", "ValuationSignal"],
        "symbol": symbol,
        "group": group,
        "provider": str(value.get("provider") or ""),
        "latestFiling": latest,
        "facts": facts,
    })
    add_relation(graph, stock_id, event_id, "HAS_OBSERVATION", weight=1.0, properties={"source": group, "aiInfluenceLabel": label})
    add_relation(graph, stock_id, event_id, "HAS_VALUATION", weight=0.7, properties={"source": group, "polarity": "context", "aiInfluenceLabel": label})
    filing_id = add_entity(graph, "disclosure-filing", symbol + ":" + group, label, {
        "tboxClass": "DisclosureFiling",
        "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "DisclosureEvent", "DisclosureFiling", "EventRisk"],
        "symbol": symbol,
        "group": group,
        "provider": str(value.get("provider") or ""),
        "reportName": str(value.get("reportName") or value.get("report_name") or latest.get("form") or label),
        "receiptNo": str(value.get("receiptNo") or value.get("receipt_no") or latest.get("accessionNumber") or ""),
        "receiptDate": str(value.get("receiptDate") or value.get("receipt_date") or latest.get("filingDate") or ""),
        "latestFiling": latest,
    })
    props = {"source": group, "polarity": "context", "aiInfluenceLabel": label}
    add_relation(graph, stock_id, filing_id, "HAS_OBSERVATION", weight=1.0, properties=props)
    add_relation(graph, stock_id, filing_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
    add_relation(graph, filing_id, stock_id, "MENTIONS_INSTRUMENT", weight=0.78, properties=props)
    add_relation(graph, event_id, filing_id, "HAS_PROVENANCE", weight=1.0, properties=props)
    add_symbol_corporate_action_concept(graph, stock_id, event_id, symbol, group, value, label)
    add_symbol_regulatory_event_concept(graph, stock_id, event_id, symbol, group, value, label)
    add_symbol_earnings_event_from_filing(graph, stock_id, event_id, symbol, group, value, label)
    add_symbol_revenue_exposure_from_facts(graph, stock_id, symbol, group, value, label)


def report_text(group: str, value: Dict[str, object], label: str) -> str:
    latest = value.get("latestFiling") if isinstance(value.get("latestFiling"), dict) else {}
    return " ".join([
        str(group or ""),
        str(label or ""),
        str(value.get("reportName") or value.get("report_name") or ""),
        str(latest.get("form") or ""),
        str(latest.get("primaryDocument") or ""),
    ])


def add_symbol_corporate_action_concept(
    graph: PortfolioOntology,
    stock_id: str,
    event_id: str,
    symbol: str,
    group: str,
    value: Dict[str, object],
    label: str,
) -> None:
    action_type = first_matching_term(report_text(group, value, label), CORPORATE_ACTION_TERMS)
    if not action_type:
        return
    latest = value.get("latestFiling") if isinstance(value.get("latestFiling"), dict) else {}
    action_id = add_entity(graph, "corporate-action", symbol + ":" + group + ":" + action_type, label, {
        "tboxClass": "CorporateAction",
        "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "CorporateAction", "EventRisk"],
        "symbol": symbol,
        "group": group,
        "actionType": action_type,
        "provider": str(value.get("provider") or ""),
        "reportName": str(value.get("reportName") or value.get("report_name") or latest.get("form") or label),
        "receiptNo": str(value.get("receiptNo") or value.get("receipt_no") or latest.get("accessionNumber") or ""),
        "eventDate": str(value.get("receiptDate") or value.get("receipt_date") or latest.get("filingDate") or latest.get("reportDate") or ""),
    })
    props = {"source": group, "polarity": "context", "aiInfluenceLabel": "기업 액션: " + label, "actionType": action_type}
    add_relation(graph, stock_id, action_id, "HAS_OBSERVATION", weight=1.0, properties=props)
    add_relation(graph, stock_id, action_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
    add_relation(graph, action_id, stock_id, "AFFECTS", weight=0.72, properties=props)
    add_relation(graph, event_id, action_id, "HAS_PROVENANCE", weight=1.0, properties=props)


def add_symbol_regulatory_event_concept(
    graph: PortfolioOntology,
    stock_id: str,
    event_id: str,
    symbol: str,
    group: str,
    value: Dict[str, object],
    label: str,
) -> None:
    event_type = first_matching_term(report_text(group, value, label), REGULATORY_EVENT_TERMS)
    if not event_type:
        return
    latest = value.get("latestFiling") if isinstance(value.get("latestFiling"), dict) else {}
    regulatory_id = add_entity(graph, "regulatory-event", symbol + ":" + group + ":" + event_type, label, {
        "tboxClass": "RegulatoryEvent",
        "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "RegulatoryEvent", "EventRisk"],
        "symbol": symbol,
        "group": group,
        "eventType": event_type,
        "provider": str(value.get("provider") or ""),
        "reportName": str(value.get("reportName") or value.get("report_name") or latest.get("form") or label),
        "receiptNo": str(value.get("receiptNo") or value.get("receipt_no") or latest.get("accessionNumber") or ""),
        "eventDate": str(value.get("receiptDate") or value.get("receipt_date") or latest.get("filingDate") or latest.get("reportDate") or ""),
    })
    props = {"source": group, "polarity": "risk", "opinionImpact": 10.0, "aiInfluenceLabel": "규제 이벤트: " + label, "eventType": event_type}
    add_relation(graph, stock_id, regulatory_id, "HAS_OBSERVATION", weight=1.0, properties=props)
    add_relation(graph, stock_id, regulatory_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
    add_relation(graph, regulatory_id, stock_id, "AFFECTS", weight=0.85, properties=props)
    add_relation(graph, event_id, regulatory_id, "HAS_PROVENANCE", weight=1.0, properties=props)


def add_symbol_earnings_event_from_filing(
    graph: PortfolioOntology,
    stock_id: str,
    event_id: str,
    symbol: str,
    group: str,
    value: Dict[str, object],
    label: str,
) -> None:
    latest = value.get("latestFiling") if isinstance(value.get("latestFiling"), dict) else {}
    facts = value.get("facts") if isinstance(value.get("facts"), dict) else {}
    revenue = first_fact(facts, ["revenue", "netIncome"])
    text = report_text(group, value, label)
    if not looks_like_earnings_event(text) and not revenue:
        return
    period_end = str(revenue.get("end") or latest.get("reportDate") or "")
    reported_at = str(revenue.get("filed") or latest.get("filingDate") or value.get("receiptDate") or value.get("receipt_date") or "")
    earnings_id = add_entity(graph, "earnings-calendar-event", symbol + ":" + (period_end or reported_at or group), label, {
        "tboxClass": "EarningsCalendarEvent",
        "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "FundamentalObservation", "EarningsEvent", "EarningsCalendarEvent", "ValuationSignal"],
        "symbol": symbol,
        "group": group,
        "provider": str(value.get("provider") or ""),
        "eventStatus": "reported",
        "periodEnd": period_end,
        "reportedAt": reported_at,
        "form": str(latest.get("form") or ""),
        "revenue": number((facts.get("revenue") if isinstance(facts.get("revenue"), dict) else {}).get("value")),
        "netIncome": number((facts.get("netIncome") if isinstance(facts.get("netIncome"), dict) else {}).get("value")),
    })
    props = {"source": group, "polarity": "context", "aiInfluenceLabel": "실적 이벤트: " + label}
    add_relation(graph, stock_id, earnings_id, "HAS_OBSERVATION", weight=1.0, properties=props)
    add_relation(graph, stock_id, earnings_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
    add_relation(graph, stock_id, earnings_id, "HAS_VALUATION", weight=0.74, properties=props)
    add_relation(graph, event_id, earnings_id, "HAS_PROVENANCE", weight=1.0, properties=props)


def add_symbol_revenue_exposure_from_facts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    group: str,
    value: Dict[str, object],
    label: str,
) -> None:
    facts = value.get("facts") if isinstance(value.get("facts"), dict) else {}
    revenue = facts.get("revenue") if isinstance(facts.get("revenue"), dict) else {}
    if not revenue or revenue.get("value") in (None, ""):
        return
    exposure_id = add_entity(graph, "revenue-exposure", symbol + ":" + group + ":" + str(revenue.get("end") or revenue.get("filed") or "latest"), "매출 노출 " + symbol, {
        "tboxClass": "RevenueExposure",
        "tboxClasses": ["Observation", "ExternalObservation", "FundamentalObservation", "RevenueExposure", "ValuationSignal"],
        "symbol": symbol,
        "group": group,
        "provider": str(value.get("provider") or ""),
        "revenue": number(revenue.get("value")),
        "periodEnd": str(revenue.get("end") or ""),
        "filedAt": str(revenue.get("filed") or ""),
        "form": str(revenue.get("form") or ""),
    })
    props = {"source": group, "polarity": "context", "aiInfluenceLabel": "매출 노출: " + label}
    add_relation(graph, stock_id, exposure_id, "HAS_REVENUE_EXPOSURE", weight=0.82, properties=props)
    add_relation(graph, stock_id, exposure_id, "HAS_VALUATION", weight=0.68, properties=props)


def add_symbol_company_overview_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, group: str, value: object) -> None:
    if not isinstance(value, dict) or group != "companyOverviews":
        return
    label = str(value.get("name") or symbol) + " 펀더멘털 개요"
    valuation_fields = {
        "marketCapitalization": number(value.get("marketCapitalization")),
        "revenueTTM": number(value.get("revenueTTM")),
        "grossProfitTTM": number(value.get("grossProfitTTM")),
        "ebitda": number(value.get("ebitda")),
        "profitMargin": number(value.get("profitMargin")),
        "operatingMarginTTM": number(value.get("operatingMarginTTM")),
        "peRatio": number(value.get("peRatio")),
        "pegRatio": number(value.get("pegRatio")),
        "forwardPE": number(value.get("forwardPE")),
        "beta": number(value.get("beta")),
        "dividendYield": number(value.get("dividendYield")),
        "analystTargetPrice": number(value.get("analystTargetPrice")),
    }
    if any(valuation_fields.values()):
        assumption_id = add_entity(graph, "valuation-assumption", symbol + ":alpha-overview", label, {
            "tboxClass": "ValuationAssumption",
            "tboxClasses": ["ValuationAssumption", "ValuationSignal", "FundamentalObservation"],
            "symbol": symbol,
            "provider": str(value.get("provider") or "Alpha Vantage"),
            "latestQuarter": str(value.get("latestQuarter") or ""),
            **valuation_fields,
        })
        add_relation(graph, stock_id, assumption_id, "HAS_VALUATION", weight=0.8, properties={"source": group, "polarity": "context", "aiInfluenceLabel": label})
    if number(value.get("revenueTTM")):
        exposure_id = add_entity(graph, "revenue-exposure", symbol + ":alpha-overview", "매출 노출 " + symbol, {
            "tboxClass": "RevenueExposure",
            "tboxClasses": ["Observation", "ExternalObservation", "FundamentalObservation", "RevenueExposure", "ValuationSignal"],
            "symbol": symbol,
            "provider": str(value.get("provider") or "Alpha Vantage"),
            "revenueTTM": number(value.get("revenueTTM")),
            "grossProfitTTM": number(value.get("grossProfitTTM")),
            "latestQuarter": str(value.get("latestQuarter") or ""),
        })
        add_relation(graph, stock_id, exposure_id, "HAS_REVENUE_EXPOSURE", weight=0.82, properties={"source": group, "polarity": "context", "aiInfluenceLabel": "매출 노출"})
    analyst_total = sum(number(value.get(key)) for key in ["analystRatingStrongBuy", "analystRatingBuy", "analystRatingHold", "analystRatingSell", "analystRatingStrongSell"])
    if number(value.get("analystTargetPrice")) or analyst_total:
        analyst_id = add_entity(graph, "analyst-revision", symbol + ":alpha-overview", "애널리스트 컨센서스 " + symbol, {
            "tboxClass": "AnalystRevision",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "AnalystRevision", "ValuationSignal"],
            "symbol": symbol,
            "provider": str(value.get("provider") or "Alpha Vantage"),
            "revisionType": "analyst-consensus-snapshot",
            "targetPrice": number(value.get("analystTargetPrice")),
            "strongBuy": number(value.get("analystRatingStrongBuy")),
            "buy": number(value.get("analystRatingBuy")),
            "hold": number(value.get("analystRatingHold")),
            "sell": number(value.get("analystRatingSell")),
            "strongSell": number(value.get("analystRatingStrongSell")),
        })
        add_relation(graph, stock_id, analyst_id, "HAS_EXTERNAL_SIGNAL", weight=0.72, properties={"source": group, "polarity": "context", "aiInfluenceLabel": "애널리스트 컨센서스"})
        add_relation(graph, stock_id, analyst_id, "HAS_VALUATION", weight=0.74, properties={"source": group, "polarity": "context", "aiInfluenceLabel": "애널리스트 컨센서스"})
    industry = str(value.get("industry") or "").strip()
    if industry:
        industry_id = add_entity(graph, "industry", industry, industry, {
            "tboxClass": "Industry",
            "industry": industry,
            "sector": str(value.get("sector") or ""),
            "provider": str(value.get("provider") or "Alpha Vantage"),
        })
        add_relation(graph, stock_id, industry_id, "BELONGS_TO", weight=1.0, properties={"source": group, "aiInfluenceLabel": "산업 분류"})


def add_symbol_earnings_report_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, group: str, value: object) -> None:
    if not isinstance(value, dict) or group != "earningsReports":
        return
    latest = value.get("latestQuarter") if isinstance(value.get("latestQuarter"), dict) else {}
    if not latest:
        return
    label = symbol + " 실적 발표"
    earnings_id = add_entity(graph, "earnings-calendar-event", symbol + ":alpha:" + str(latest.get("fiscalDateEnding") or latest.get("reportedDate") or "latest"), label, {
        "tboxClass": "EarningsCalendarEvent",
        "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "FundamentalObservation", "EarningsEvent", "EarningsCalendarEvent", "ValuationSignal"],
        "symbol": symbol,
        "provider": str(value.get("provider") or "Alpha Vantage"),
        "eventStatus": "reported",
        "fiscalDateEnding": str(latest.get("fiscalDateEnding") or ""),
        "reportedDate": str(latest.get("reportedDate") or ""),
        "reportedEPS": number(latest.get("reportedEPS")),
        "estimatedEPS": number(latest.get("estimatedEPS")),
        "surprise": number(latest.get("surprise")),
        "surprisePercentage": number(latest.get("surprisePercentage")),
    })
    props = {"source": group, "polarity": "context", "aiInfluenceLabel": label}
    if abs(number(latest.get("surprisePercentage"))) >= 5:
        props.update({"polarity": "support" if number(latest.get("surprisePercentage")) > 0 else "risk", "opinionImpact": min(12.0, abs(number(latest.get("surprisePercentage"))) * 0.6)})
    add_relation(graph, stock_id, earnings_id, "HAS_OBSERVATION", weight=1.0, properties=props)
    add_relation(graph, stock_id, earnings_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
    add_relation(graph, stock_id, earnings_id, "HAS_VALUATION", weight=0.78, properties=props)
