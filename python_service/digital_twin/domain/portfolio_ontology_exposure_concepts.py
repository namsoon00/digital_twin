from typing import Dict, List

from .market_data import number
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
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
) -> None:
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
        if currency in {"KRW", ""} or ratio < 10:
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
        if ratio < 35 and sector_positions.get(label, 0) < 2:
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
