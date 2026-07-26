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


MACRO_CONTEXT_KINDS = {
    "interest-rate",
    "yield-curve",
    "fx-rate",
    "fx-pair",
    "macro-indicator",
    "macro-regime",
}
MACRO_CONTEXT_RELATION_TYPES = {
    "HAS_EXTERNAL_SIGNAL",
    "HAS_RATE_SENSITIVITY",
    "HAS_FX_EXPOSURE",
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
    global_macro_context = has_global_macro_context(graph)
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
        # Rates and FX live once in the shared portfolio macro scope. Copying
        # them into every stock scope would make each macro refresh look like
        # a change to every security and expand native-rule work needlessly.
        # A direct stock relation is still required for a stock-specific
        # sensitivity rule; this only prevents a false data-coverage gap.
        if global_macro_context and "macroRegime" in required and "macroRegime" not in present:
            present.append("macroRegime")
        missing = [category for category in required if category not in set(present)]
        if not missing:
            continue
        coverage_ratio = len(present) / max(1, len(required))
        severity = coverage_severity(coverage_ratio, missing)
        data_state = "unavailable" if severity == "high" else "insufficient" if severity == "medium" else "partial"
        review_level = "blocked" if severity == "high" else "check" if severity == "medium" else "observe"
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
            "reviewLevel": review_level,
            "dataState": data_state,
            "evidenceRole": "blocking",
            "dataScope": "ontology-coverage",
            "scope": "ontology-coverage",
        })
        properties = {
            "source": "ontology-coverage-gate",
            "polarity": "blocking",
            "evidenceRole": "blocking",
            "reviewLevel": review_level,
            "dataState": data_state,
            "aiInfluenceLabel": "온톨로지 커버리지 부족: " + ", ".join(CATEGORY_LABELS.get(category, category) for category in missing[:4]),
            "dataScope": "ontology-coverage",
            "scope": "ontology-coverage",
            "missingCategories": missing,
            "coverageRatio": round(coverage_ratio, 3),
            "severity": severity,
        }
        add_relation(graph, stock_id, gap_id, "HAS_COVERAGE_GAP", weight=1.0, properties=properties)


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


def has_global_macro_context(graph: PortfolioOntology) -> bool:
    """Return whether the shared ABox includes usable macro or FX evidence.

    This checks raw, portfolio-level observations only. It deliberately does
    not create a per-stock ``HAS_MACRO_REGIME`` fact, which belongs to a
    TypeDB inference result once stock-specific sensitivity is established.
    """
    entities = {
        item.entity_id: item
        for item in graph.entities or []
        if str(item.entity_id or "").strip()
    }
    portfolio_ids = {
        entity_id_value
        for entity_id_value, item in entities.items()
        if str(item.kind or "").strip() == "portfolio"
        or str((item.properties or {}).get("tboxClass") or "").strip() == "Portfolio"
    }
    if not portfolio_ids:
        return False
    for relation in graph.relations or []:
        relation_type = str(relation.relation_type or "").upper().strip()
        if relation_type not in MACRO_CONTEXT_RELATION_TYPES:
            continue
        if relation.source in portfolio_ids:
            other_id = relation.target
        elif relation.target in portfolio_ids:
            other_id = relation.source
        else:
            continue
        other = entities.get(other_id)
        other_kind = str(getattr(other, "kind", "") or "").strip()
        properties = relation.properties or {}
        data_scope = str(properties.get("dataScope") or "").lower().strip()
        if other_kind in MACRO_CONTEXT_KINDS or data_scope in {"macro", "fx"}:
            return True
    return False


def coverage_severity(coverage_ratio: float, missing: List[str]) -> str:
    critical = {"price", "trendPath", "dataQuality"}
    if critical.intersection(missing) or number(coverage_ratio) < 0.45:
        return "high"
    if number(coverage_ratio) < 0.72 or {"externalEvidence", "valuation"}.intersection(missing):
        return "medium"
    return "low"


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
