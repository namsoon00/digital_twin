from typing import Dict, Iterable, List, Set

from .market_data import number
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation
from .portfolio import Position
from .portfolio_ontology_runtime_concepts import is_holding_position, is_watchlist_position
from .portfolio_ontology_market_concepts import symbol_key


CATEGORY_LABELS = {
    "price": "가격",
    "trendPath": "가격 경로",
    "tradeFlow": "거래/수급",
    "liquidity": "유동성",
    "execution": "실행 가능성",
    "dataQuality": "데이터 품질",
    "externalEvidence": "외부 근거",
    "valuation": "펀더멘털/밸류에이션",
    "macroRegime": "거시/환율 레짐",
    "cryptoExposure": "크립토 노출",
}


CATEGORY_RELATIONS = {
    "price": {"HAS_PRICE"},
    "trendPath": {"HAS_PRICE_PATH", "HAS_TREND_PHASE", "HAS_TREND_TRANSITION", "HAS_TECHNICAL_INDICATOR"},
    "tradeFlow": {"HAS_TRADE_FLOW"},
    "liquidity": {"HAS_LIQUIDITY_PROFILE"},
    "execution": {"HAS_EXECUTION_METRIC", "HAS_EXECUTION_CAPACITY", "HAS_EXIT_CAPACITY"},
    "dataQuality": {"HAS_DATA_QUALITY"},
    "externalEvidence": {
        "HAS_EXTERNAL_SIGNAL",
        "HAS_RESEARCH_EVIDENCE",
        "HAS_EVENT_EVIDENCE",
        "HAS_DISCLOSURE",
        "MENTIONS_INSTRUMENT",
        "MATERIAL_TO",
        "NEWS_CONTEXT_FOR",
        "NEWS_RISK_FOR",
        "NEWS_SUPPORTS_ENTRY",
    },
    "valuation": {"HAS_VALUATION", "HAS_REVENUE_EXPOSURE"},
    "macroRegime": {"HAS_MACRO_REGIME", "HAS_RATE_SENSITIVITY", "HAS_FX_EXPOSURE"},
    "cryptoExposure": {"HAS_CRYPTO_EXPOSURE"},
}


def required_coverage_categories(position: Position) -> List[str]:
    base = ["price", "trendPath", "tradeFlow", "dataQuality", "externalEvidence", "macroRegime"]
    if is_holding_position(position):
        base.extend(["liquidity", "execution", "valuation"])
    elif is_watchlist_position(position):
        base.append("valuation")
    if crypto_exposure_expected(position):
        base.append("cryptoExposure")
    return unique_list(base)


def crypto_exposure_expected(position: Position) -> bool:
    symbol = symbol_key(position)
    sector = str(position.sector or "").lower()
    name = str(position.name or "").lower()
    return (
        symbol in {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}
        or any(token in sector for token in ["디지털자산", "crypto", "bitcoin", "비트코인"])
        or any(token in name for token in ["bitcoin", "crypto", "비트코인", "스트래티지"])
    )


def add_coverage_gap_concepts(
    graph: PortfolioOntology,
    positions: Iterable[Position],
    portfolio_id: str,
) -> None:
    relation_types_by_symbol = relation_types_for_symbols(graph)
    for position in positions:
        symbol = symbol_key(position)
        if not symbol:
            continue
        stock_id = entity_id("stock", symbol)
        relation_types = relation_types_by_symbol.get(symbol, set())
        required = required_coverage_categories(position)
        present = [
            category
            for category in required
            if relation_types.intersection(CATEGORY_RELATIONS.get(category, set()))
        ]
        missing = [category for category in required if category not in set(present)]
        if not missing:
            continue
        coverage_ratio = len(present) / max(1, len(required))
        severity = coverage_severity(coverage_ratio, missing)
        impact = coverage_opinion_impact(severity, missing)
        label = (position.name or symbol) + " 온톨로지 커버리지 부족"
        gap_id = add_entity(graph, "coverage-gap", symbol, label, {
            "tboxClass": "CoverageGap",
            "tboxClasses": ["Observation", "DataQuality", "CoverageGap", "DataQualitySignal"],
            "symbol": symbol,
            "source": "ontology-coverage-gate",
            "portfolioId": portfolio_id,
            "targetRole": "watchlist" if is_watchlist_position(position) else "holding",
            "requiredCategories": required,
            "presentCategories": present,
            "missingCategories": missing,
            "missingLabels": [CATEGORY_LABELS.get(category, category) for category in missing],
            "coverageRatio": round(coverage_ratio, 3),
            "severity": severity,
            "missingCount": len(missing),
            "opinionImpact": impact,
            "dataScope": "ontology-coverage",
            "scope": "ontology-coverage",
        })
        properties = {
            "source": "ontology-coverage-gate",
            "polarity": "risk",
            "riskImpact": impact,
            "opinionImpact": impact,
            "confidenceImpact": "decrease",
            "aiInfluenceLabel": "온톨로지 커버리지 부족: " + ", ".join(CATEGORY_LABELS.get(category, category) for category in missing[:4]),
            "dataScope": "ontology-coverage",
            "scope": "ontology-coverage",
            "missingCategories": missing,
            "coverageRatio": round(coverage_ratio, 3),
            "severity": severity,
        }
        add_relation(graph, stock_id, gap_id, "HAS_COVERAGE_GAP", weight=round(max(0.12, 1 - coverage_ratio), 4), properties=properties)
        add_relation(graph, stock_id, gap_id, "HAS_DATA_QUALITY", weight=round(max(0.12, coverage_ratio), 4), properties=properties)


def relation_types_for_symbols(graph: PortfolioOntology) -> Dict[str, Set[str]]:
    rows: Dict[str, Set[str]] = {}
    entity_symbols = {
        item.entity_id: str((item.properties or {}).get("symbol") or "").upper().strip()
        for item in graph.entities
        if str((item.properties or {}).get("symbol") or "").strip()
    }
    for relation in graph.relations:
        props = relation.properties or {}
        symbol = str(props.get("symbol") or "").upper().strip()
        if not symbol:
            symbol = entity_symbols.get(relation.source) or entity_symbols.get(relation.target) or ""
        if not symbol:
            continue
        rows.setdefault(symbol, set()).add(str(relation.relation_type or "").upper().strip())
    return rows


def coverage_severity(coverage_ratio: float, missing: List[str]) -> str:
    critical = {"price", "trendPath", "dataQuality"}
    if critical.intersection(missing) or number(coverage_ratio) < 0.45:
        return "high"
    if number(coverage_ratio) < 0.72 or {"externalEvidence", "valuation"}.intersection(missing):
        return "medium"
    return "low"


def coverage_opinion_impact(severity: str, missing: List[str]) -> float:
    base = {"high": 9.0, "medium": 5.5, "low": 2.5}.get(str(severity or ""), 4.0)
    return round(min(14.0, base + len(missing) * 0.6), 2)


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
