"""Build the durable shared knowledge graph from a portfolio ABox.

MarketWorld is intentionally a fast, latest-observation read model.  It is
not the right place to retain a company's issuer relationship, ADR conversion
line, a single-stock leveraged product, or the provenance behind a research
claim.  KnowledgeWorld keeps those account-independent facts separately so a
future account can reuse the same real-world graph without importing another
account's holdings or decisions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, Set

from .ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology, entity_id
from .ontology_worlds import OntologyWorld, world_metadata
from .market_world_projection import (
    SHARED_WORLD_PROJECTION_CONTRACT_VERSION,
    _graph_observed_at,
    _market_observed_at,
    _clean,
    is_account_entity,
    shared_world_property_allowed,
)


# These relations establish reusable real-world identity, exposure, and
# evidence topology. Price/flow/technical values stay in MarketWorld because
# retaining every tick as durable knowledge would turn the knowledge graph
# into another time-series store.
KNOWLEDGE_RELATION_TYPES = {
    "ISSUES",
    "REPRESENTS_COMPANY",
    "HAS_LISTING",
    "LISTED_ON",
    "HAS_SHARE_CLASS",
    "AFFILIATED_WITH",
    "CONTROLS",
    "USES_MARKET_BENCHMARK",
    "ISSUES_SECURITY_LINE",
    "HAS_SECURITY_LINE",
    "REPRESENTS_ECONOMIC_CLAIM",
    "TRACKS_UNDERLYING",
    "COMPETES_WITH",
    "SUPPLIES_TO",
    "SELLS_TO",
    "HAS_PRODUCT_EXPOSURE",
    "HAS_SUPPLY_CHAIN_EXPOSURE",
    "HAS_CUSTOMER_EXPOSURE",
    "HAS_REVENUE_EXPOSURE",
    "REPRESENTS_STOCK",
    "REPRESENTS_INSTRUMENT",
    "HAS_MARKET_EXPOSURE",
    "BELONGS_TO",
    "TRADED_IN",
    "DENOMINATED_IN",
    "HAS_FX_EXPOSURE",
    "HAS_RATE_SENSITIVITY",
    "HAS_EVIDENCE_PROFILE",
    "HAS_CRYPTO_EXPOSURE",
    "HAS_ARBITRAGE_FRICTION",
    "HAS_LEVERAGED_PRODUCT",
    "HAS_ADR_PREMIUM",
    "HAS_LEVERAGED_FLOW_SIGNAL",
    "AMPLIFIES_DAILY_RETURN",
    "CREATES_REBALANCING_FLOW",
    "HAS_EXTERNAL_SIGNAL",
    "HAS_COVERAGE_GAP",
    "HAS_FINANCIAL_STATE",
    "HAS_GOVERNANCE_STATE",
    "HAS_EXECUTIVE_ROLE",
    "ROLE_HELD_BY",
    "HAS_OWNERSHIP_STAKE",
    "STAKE_HELD_BY",
    "HAS_CAPITAL_STRUCTURE",
    "HAS_CAPITAL_EVENT",
    "HAS_CORPORATE_ACTION",
    "APPLIES_TO_SECURITY",
    "HAS_CAPITAL_STATE",
    "HAS_BUSINESS_SEGMENT",
    "SEGMENT_REPORTS_FACT",
    "FILES_FINANCIAL_REPORT",
    "COVERS_REPORTING_PERIOD",
    "CONTAINS_FINANCIAL_STATEMENT",
    "CONTAINS_FINANCIAL_FACT",
    "USES_ACCOUNTING_SCOPE",
    "REVISES_FINANCIAL_REPORT",
    "DERIVED_FROM_FINANCIAL_FACT",
    "HAS_VALUATION_METRIC",
    "HAS_PROVENANCE",
    "HAS_ANALYSIS",
    "EXPLAINS",
    "HAS_SOURCE_RELIABILITY",
    "MENTIONS_INSTRUMENT",
    "MENTIONS_PEER",
    "HAS_TOPIC",
    "HAS_EVENT_TYPE",
    "AFFECTS_SECTOR",
    "OBSERVED_FROM",
    "STALE_AFTER",
    "CONFLICTS_WITH_SOURCE",
}

KNOWLEDGE_ENTITY_KINDS = {
    "company",
    "security",
    "security-line",
    "security-listing",
    "share-class",
    "stock",
    "market-index",
    "peer-group",
    "sector",
    "industry",
    "market",
    "currency",
    "adr",
    "leveraged-etf",
    "inverse-etf",
    "single-stock-etf",
    "supply-chain-exposure",
    "customer-exposure",
    "revenue-exposure",
    "data-source",
    "market-evidence-profile",
    "research-evidence",
    "news-article",
    "disclosure-filing",
    "news-topic",
    "event-cluster",
    "macro-indicator",
    "interest-rate",
    "fx-rate",
    "crypto-asset",
    "company-financial-state",
    "company-governance-state",
    "company-capital-state",
    "corporate-action",
    "company-valuation-state",
    "person",
    "executive-role",
    "company-coverage-gap",
}

# Account and point-in-time quote fields must not leak into the reusable
# knowledge graph. The original raw observations remain in MySQL time series
# and current MarketWorld; knowledge retains the fact topology and provenance.
VOLATILE_MARKET_PROPERTY_KEYS = {
    "currentPrice",
    "averagePrice",
    "quantity",
    "sellableQuantity",
    "marketValue",
    "profitLoss",
    "profitLossRate",
    "positionWeight",
    "positionAccountWeight",
    "changeRate",
    "priceChangeRate",
    "ma5",
    "ma20",
    "ma60",
    "ma5Distance",
    "ma20Distance",
    "ma60Distance",
    "ma20Slope",
    "ma60Slope",
    "volume",
    "volumeRatio",
    "rawVolumeRatio",
    "timeAdjustedVolumeRatio",
    "expectedVolumeRatioNow",
    "tradeStrength",
    "tradingValue",
    "reportedTradingValue",
    "estimatedTradingValue",
    "bidAskImbalance",
    "foreignNetVolume",
    "foreignNetAmount",
    "institutionNetVolume",
    "institutionNetAmount",
    "individualNetVolume",
    "individualNetAmount",
    "smartMoneyNetVolume",
}


def _knowledge_properties(
    properties: Dict[str, object],
    world: OntologyWorld,
    relation: bool = False,
) -> Dict[str, object]:
    values = {
        key: deepcopy(value)
        for key, value in dict(properties or {}).items()
        if key not in VOLATILE_MARKET_PROPERTY_KEYS
        and shared_world_property_allowed(key, relation=relation)
    }
    values.update(world_metadata(world))
    values["knowledgeObservationShared"] = True
    values["knowledgeOwnership"] = "shared"
    observed_at = _market_observed_at(values)
    if observed_at:
        values["knowledgeObservedAt"] = observed_at
    return values


def _knowledge_entity(entity: OntologyEntity, world: OntologyWorld) -> OntologyEntity:
    return OntologyEntity(
        entity.entity_id,
        entity.label,
        entity.kind,
        _knowledge_properties(entity.properties, world),
    )


def _knowledge_relation(relation: OntologyRelation, world: OntologyWorld) -> OntologyRelation:
    return OntologyRelation(
        relation.source,
        relation.target,
        relation.relation_type,
        relation.weight,
        list(relation.evidence_ids or []),
        _knowledge_properties(relation.properties, world, relation=True),
    )


def _knowledge_evidence(evidence: OntologyEvidence, world: OntologyWorld) -> OntologyEvidence:
    return OntologyEvidence(
        evidence.evidence_id,
        evidence.subject,
        evidence.kind,
        evidence.source,
        evidence.summary,
        _knowledge_properties(evidence.value, world),
        evidence.evidence_role,
        evidence.data_state,
    )


def _is_knowledge_candidate(entity: OntologyEntity) -> bool:
    if is_account_entity(entity):
        return False
    values = dict(entity.properties or {})
    if _clean(values.get("ontologyBox") or "ABox") != "ABox":
        return False
    return _clean(entity.kind).lower() in KNOWLEDGE_ENTITY_KINDS


def build_knowledge_world_graph(
    source_graph: PortfolioOntology,
    world: OntologyWorld,
    observed_at: object = "",
) -> PortfolioOntology:
    """Extract a privacy-safe, durable graph of real-world relationships."""
    candidates = {
        entity.entity_id: entity
        for entity in list(source_graph.entities or [])
        if not is_account_entity(entity)
        and _clean((entity.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    }
    relations = [
        relation
        for relation in list(source_graph.relations or [])
        if _clean(relation.relation_type).upper() in KNOWLEDGE_RELATION_TYPES
        and relation.source in candidates
        and relation.target in candidates
        and _clean((relation.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ]
    connected_ids: Set[str] = {
        endpoint
        for relation in relations
        for endpoint in (relation.source, relation.target)
    }
    kept_ids = {
        entity_id_value
        for entity_id_value, entity in candidates.items()
        if entity_id_value in connected_ids or _is_knowledge_candidate(entity)
    }
    entities = [_knowledge_entity(candidates[item_id], world) for item_id in sorted(kept_ids)]
    kept_relations = [
        _knowledge_relation(relation, world)
        for relation in relations
        if relation.source in kept_ids and relation.target in kept_ids
    ]
    evidence = [
        _knowledge_evidence(item, world)
        for item in list(source_graph.evidence or [])
        if item.subject in kept_ids
        and _clean((item.value or {}).get("ontologyBox") or "ABox") == "ABox"
    ]
    source_observed_at = _graph_observed_at(source_graph, observed_at)
    world_entity = OntologyEntity(
        entity_id("ontology-world", world.world_id),
        "지식 세계 " + world.market_id.upper(),
        "ontology-world",
        {
            "ontologyBox": "ABox",
            "tboxClass": "KnowledgeWorld",
            **world_metadata(world),
            "knowledgeObservationShared": True,
            "knowledgeOwnership": "shared",
            "knowledgeObservedAt": source_observed_at,
        },
    )
    return PortfolioOntology(
        world.world_id,
        entities=[world_entity, *entities],
        relations=kept_relations,
        evidence=evidence,
        worldview={
            **world_metadata(world),
            "knowledgeWorldProjection": True,
            "knowledgeWorldProjectionMode": "durable-semantic-relationship-graph",
            "sharedWorldProjectionContractVersion": SHARED_WORLD_PROJECTION_CONTRACT_VERSION,
            "sourcePortfolioWorldId": str((source_graph.worldview or {}).get("worldId") or ""),
            "sourceObservedAt": source_observed_at,
            "targetScopedManifestPatch": dict((source_graph.worldview or {}).get("targetScopedManifestPatch") or {}),
        },
        prompt=source_graph.prompt,
    )


def knowledge_world_coverage(graph: PortfolioOntology) -> Dict[str, object]:
    relation_types = sorted({str(item.relation_type or "").upper() for item in graph.relations or []})
    classes = sorted({
        str((item.properties or {}).get("tboxClass") or "")
        for item in graph.entities or []
        if str((item.properties or {}).get("tboxClass") or "")
    })
    return {
        "entityCount": len(graph.entities or []),
        "relationCount": len(graph.relations or []),
        "evidenceCount": len(graph.evidence or []),
        "relationTypes": relation_types,
        "tboxClasses": classes,
        "worldType": str((graph.worldview or {}).get("worldType") or "knowledge"),
    }
