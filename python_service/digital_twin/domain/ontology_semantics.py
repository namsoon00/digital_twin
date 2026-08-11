"""Strong semantic storage contract for the investment ontology.

The logical TBox has always described classes and relation names, but the
first TypeDB implementation stored every ABox fact as a generic
``ontology-node`` or ``ontology-assertion`` and kept the real type in JSON.
That made TypeDB a durable graph, not a strongly typed knowledge graph.

This module is the single mapping between the logical TBox and TypeDB's
physical type hierarchy.  It intentionally derives type declarations from
the TBox catalogue so adding a class or relation cannot silently create an
untyped persistence path.
"""

from __future__ import annotations

from functools import lru_cache
import re
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from .ontology_tbox import CLASS_DEFS, RELATION_DEFS, tbox_class_def


SEMANTIC_STORAGE_CONTRACT_VERSION = "typedb-semantic-storage-v2"


def _slug(value: object) -> str:
    """Return a stable TypeQL identifier component from a TBox name."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", str(value or "").strip())
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text or "unknown"


def typedb_class_type(class_name: object) -> str:
    return "ontology-class-" + _slug(class_name)


def typedb_relation_type(relation_name: object) -> str:
    return "ontology-relation-" + _slug(relation_name)


@lru_cache(maxsize=1)
def semantic_class_types() -> Dict[str, str]:
    return {definition.name: typedb_class_type(definition.name) for definition in CLASS_DEFS}


@lru_cache(maxsize=1)
def semantic_relation_types() -> Dict[str, str]:
    return {definition.name.upper(): typedb_relation_type(definition.name) for definition in RELATION_DEFS}


def primary_tbox_class(properties: Mapping[str, object] = None) -> str:
    values = dict(properties or {})
    primary = str(values.get("tboxClass") or "").strip()
    if primary in semantic_class_types():
        return primary
    for item in values.get("tboxClasses") or []:
        candidate = str(item or "").strip()
        if candidate in semantic_class_types():
            return candidate
    return ""


_KIND_TO_TBOX_CLASS = {
    "account": "Account",
    "portfolio": "Portfolio",
    "position": "Position",
    "watchlist": "Watchlist",
    "cash": "Cash",
    "company": "Company",
    "security": "Security",
    "stock": "Stock",
    "etf": "ETF",
    "sector": "Sector",
    "market": "Market",
    "currency": "Currency",
    "data-source": "DataSource",
    "price-metric": "PriceObservation",
    "technical-indicator": "TechnicalIndicator",
    "flow-observation": "FlowObservation",
    "external-signal": "ExternalSignal",
    "news-article": "NewsArticle",
    "disclosure-filing": "DisclosureFiling",
    "company-financial-state": "FinancialState",
    "company-governance-state": "GovernanceState",
    "company-capital-state": "CapitalState",
    "company-valuation-state": "ValuationMetric",
    "person": "Person",
    "executive-role": "ExecutiveRole",
    "macro-indicator": "MacroIndicator",
    "interest-rate": "InterestRate",
    "fx-rate": "FXRateSignal",
    "crypto-asset": "CryptoAsset",
    "risk": "Risk",
    "coverage-gap": "CoverageGap",
    "hypothesis": "Hypothesis",
    "investment-thesis": "InvestmentThesis",
    "decision": "Decision",
    "execution-plan": "ExecutionPlan",
    "notification-dispatch": "NotificationDispatch",
    "data-pipeline": "DataPipeline",
    "collection-schedule": "CollectionSchedule",
}


def class_for_kind(kind: object) -> str:
    candidate = _KIND_TO_TBOX_CLASS.get(str(kind or "").strip().lower(), "")
    return candidate if candidate in semantic_class_types() else ""


def entity_semantic_type(
    properties: Mapping[str, object] = None,
    kind: object = "",
    fallback: str = "ontology-entity",
) -> str:
    """Choose the TypeDB entity subtype without changing logical facts."""
    class_name = primary_tbox_class(properties) or class_for_kind(kind)
    return semantic_class_types().get(class_name, fallback)


def relation_semantic_type(relation_type: object, fallback: str = "ontology-assertion") -> str:
    return semantic_relation_types().get(str(relation_type or "").upper().strip(), fallback)


def semantic_typeql_schema(missing_type_names: Iterable[str] = None) -> str:
    """Generate additive TypeQL definitions for logical TBox concepts.

    A physical class follows its logical TBox parent when the parent exists.
    Relations share the stable ``source``/``target`` role interface inherited
    from ``ontology-assertion``.  Endpoint domain/range rules are enforced by
    the ontology validator as well, which gives clear diagnostics before a
    TypeDB write rather than only a low-level schema error.
    """
    requested = {str(item or "").strip() for item in (missing_type_names or []) if str(item or "").strip()}
    include_all = not requested
    statements: List[str] = []
    for definition in CLASS_DEFS:
        name = typedb_class_type(definition.name)
        if not include_all and name not in requested:
            continue
        parent = typedb_class_type(definition.parent) if definition.parent and definition.parent in semantic_class_types() else "ontology-entity"
        statements.append("entity " + name + ", sub " + parent + ";")
    for definition in RELATION_DEFS:
        name = typedb_relation_type(definition.name)
        if not include_all and name not in requested:
            continue
        statements.append("relation " + name + ", sub ontology-assertion;")
    return "define\n" + "\n".join(statements) if statements else ""


def semantic_storage_type_names() -> Set[str]:
    return set(semantic_class_types().values()) | set(semantic_relation_types().values()) | {"ontology-semantic-type"}


def _ancestor_classes(class_name: str) -> Set[str]:
    ancestors: Set[str] = set()
    current = str(class_name or "").strip()
    while current and current not in ancestors:
        ancestors.add(current)
        definition = tbox_class_def(current)
        current = str(definition.parent or "").strip() if definition else ""
    return ancestors


def entity_class_family(properties: Mapping[str, object] = None, kind: object = "") -> Set[str]:
    values = dict(properties or {})
    classes: List[str] = []
    primary = str(values.get("tboxClass") or "").strip()
    if primary:
        classes.append(primary)
    classes.extend(str(item or "").strip() for item in values.get("tboxClasses") or [])
    if not classes:
        fallback = class_for_kind(kind)
        if fallback:
            classes.append(fallback)
    result: Set[str] = set()
    for class_name in classes:
        if class_name in semantic_class_types():
            result.update(_ancestor_classes(class_name))
    return result


# These are deliberately high-value investment invariants.  The complete TBox
# remains extensible, while rules that establish issuer/security/position and
# market-evidence identity cannot be accidentally connected to a wrong class.
CORE_RELATION_ENDPOINTS: Dict[str, Tuple[Sequence[str], Sequence[str]]] = {
    "MANAGES_PORTFOLIO": (("Account",), ("Portfolio",)),
    "HOLDS": (("Portfolio",), ("Instrument",)),
    "WATCHES": (("Portfolio", "Watchlist"), ("Instrument",)),
    # ``HAS_POSITION`` also links a stock to normalized position metrics in
    # the current ABox, so both the owning container and the instrument view
    # are valid sources for the same semantic relation.
    "HAS_POSITION": (("Portfolio", "Watchlist", "Instrument", "Security"), ("Position",)),
    "REPRESENTS_STOCK": (("Position", "Security"), ("Stock", "Instrument")),
    "REPRESENTS_INSTRUMENT": (("Stock", "Instrument"), ("Security", "Instrument")),
    "ISSUES": (("Company",), ("Security",)),
    "REPRESENTS_COMPANY": (("Instrument", "Security"), ("Company",)),
    "ISSUES_SECURITY_LINE": (("Company",), ("SecurityLine", "Security")),
    "HAS_SECURITY_LINE": (("Stock", "Instrument", "Security"), ("SecurityLine", "Security")),
    "REPRESENTS_ECONOMIC_CLAIM": (("DepositaryReceipt", "Security"), ("Security", "Stock")),
    "TRACKS_UNDERLYING": (("ETF", "SecurityLine", "Security"), ("Security", "Stock", "Instrument")),
    "HAS_PRICE": (("Instrument", "Security"), ("PriceObservation", "Observation")),
    "HAS_TECHNICAL_INDICATOR": (("Instrument", "Security"), ("TechnicalIndicator", "Observation")),
    "HAS_TRADE_FLOW": (("Instrument", "Security"), ("FlowObservation", "Observation")),
    "HAS_EXTERNAL_SIGNAL": (("Instrument", "Security", "Portfolio"), ("ExternalSignal", "Observation")),
    "HAS_FINANCIAL_STATE": (("Company", "Instrument", "Security"), ("FinancialState", "FinancialFact")),
    "HAS_GOVERNANCE_STATE": (("Company", "Instrument", "Security"), ("GovernanceState",)),
    "HAS_CAPITAL_STATE": (("Company", "Instrument", "Security"), ("CapitalState", "CapitalStructureSnapshot")),
    "HAS_EXECUTIVE_ROLE": (("GovernanceState", "Company"), ("ExecutiveRole",)),
    "ROLE_HELD_BY": (("ExecutiveRole",), ("Person",)),
    "HAS_COVERAGE_GAP": (("Instrument", "Security", "Portfolio"), ("CoverageGap",)),
    "HAS_FX_EXPOSURE": (("Instrument", "Security", "Portfolio"), ("FXRateSignal", "ExternalSignal")),
    "HAS_RATE_SENSITIVITY": (("Instrument", "Security", "Portfolio"), ("RateSignal", "ExternalSignal")),
    "HAS_CRYPTO_EXPOSURE": (("Instrument", "Security", "Portfolio"), ("CryptoExposure", "CryptoMarketSignal", "CryptoAsset")),
    "HAS_EXECUTION_PLAN": (("Instrument", "Security", "InvestmentOpinion"), ("ExecutionPlan",)),
}


def relation_endpoint_contract(relation_type: object) -> Tuple[Sequence[str], Sequence[str]]:
    return CORE_RELATION_ENDPOINTS.get(str(relation_type or "").upper().strip(), ((), ()))


def endpoint_matches_family(classes: Set[str], expected: Sequence[str]) -> bool:
    return not expected or bool(set(expected) & set(classes))


def semantic_contract_summary(graph) -> Dict[str, object]:
    """Return bounded coverage telemetry without mutating the ABox."""
    entities = list(getattr(graph, "entities", []) or [])
    relations = list(getattr(graph, "relations", []) or [])
    entity_by_id = {str(item.entity_id): item for item in entities}
    typed_entities = 0
    generic_entities = 0
    for entity in entities:
        if entity_semantic_type(entity.properties, entity.kind) == "ontology-entity":
            generic_entities += 1
        else:
            typed_entities += 1
    typed_relations = 0
    generic_relations = 0
    checked_relations = 0
    endpoint_violations = 0
    for relation in relations:
        if relation_semantic_type(relation.relation_type) == "ontology-assertion":
            generic_relations += 1
        else:
            typed_relations += 1
        expected_source, expected_target = relation_endpoint_contract(relation.relation_type)
        source = entity_by_id.get(str(relation.source))
        target = entity_by_id.get(str(relation.target))
        if not expected_source or not expected_target or not source or not target:
            continue
        source_classes = entity_class_family(source.properties, source.kind)
        target_classes = entity_class_family(target.properties, target.kind)
        if not source_classes or not target_classes:
            continue
        checked_relations += 1
        if not endpoint_matches_family(source_classes, expected_source) or not endpoint_matches_family(target_classes, expected_target):
            endpoint_violations += 1
    return {
        "version": SEMANTIC_STORAGE_CONTRACT_VERSION,
        "typedEntityCount": typed_entities,
        "genericEntityCount": generic_entities,
        "typedRelationCount": typed_relations,
        "genericRelationCount": generic_relations,
        "endpointCheckedRelationCount": checked_relations,
        "endpointViolationCount": endpoint_violations,
        "physicalClassTypeCount": len(semantic_class_types()),
        "physicalRelationTypeCount": len(semantic_relation_types()),
    }
