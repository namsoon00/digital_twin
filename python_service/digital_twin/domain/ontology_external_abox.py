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
        else:
            base = str(key or "").upper().strip()
            quote = "KRW"
            rate = number(value)
            provider = source
            fetched_at = ""
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
        })
        props = {"source": "fxRates", "polarity": "context", "aiInfluenceLabel": pair_label + " 환율"}
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
        change24h = number(item.get("change24h"))
        change7d = number(item.get("change7d"))
        crypto_id = add_entity(graph, "crypto-market-signal", str(coin_id), str(item.get("name") or coin_id), {
            "tboxClass": "CryptoMarketSignal",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "CryptoMarketSignal", "CryptoSignal"],
            "provider": str(item.get("provider") or "CoinGecko"),
            "symbol": str(item.get("symbol") or "").upper(),
            "price": round(number(item.get("price")), 4),
            "change24h": round(change24h, 2),
            "change7d": round(change7d, 2),
            "volume24h": round(number(item.get("volume24h")), 2),
        })
        magnitude = max(abs(change24h), abs(change7d))
        props = {"source": "cryptoMarkets", "polarity": "risk" if magnitude >= 4 else "context", "opinionImpact": min(12.0, magnitude) if magnitude >= 4 else 0.0, "aiInfluenceLabel": str(item.get("name") or coin_id) + " 변동성"}
        add_relation(graph, portfolio_node_id, crypto_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, crypto_id, portfolio_node_id, "AFFECTS", weight=min(1.0, magnitude / 10), properties=props)


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
        add_relation(graph, stock_id, rate_id, "HAS_FX_EXPOSURE", weight=weight or 0.15, properties=props)
        add_relation(graph, rate_id, stock_id, "AFFECTS", weight=0.62, properties=props)
    if not rate_sensitive_position(position):
        return
    for series_id, rate_id in sorted(interest_rate_signal_ids(external_signals).items()):
        is_curve = series_id == "YIELDSPREAD10Y2Y"
        props = {
            "source": "macro",
            "polarity": "context",
            "aiInfluenceLabel": "금리 스프레드 민감도" if is_curve else rate_series_label(series_id) + " 민감도",
        }
        if series_id in {"DGS10", "DGS2", "DFF"}:
            props["rateSeriesId"] = series_id
        add_relation(graph, stock_id, rate_id, "HAS_RATE_SENSITIVITY", weight=weight or 0.15, properties=props)
        add_relation(graph, rate_id, stock_id, "AFFECTS", weight=0.58 if is_curve else 0.62, properties=props)


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
