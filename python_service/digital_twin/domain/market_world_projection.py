"""Projection helpers for the shared market observation world.

``PortfolioWorld`` is intentionally account-bound: it contains positions,
risk budgets, delivery preferences and decision episodes.  Those facts must
never become the shared market context merely because two accounts observe the
same ticker.  This module extracts the account-independent slice of an ABox
and gives the projection service a deterministic merge operation for the
shared ``MarketWorld``.

The account ABox does not carry a local read mirror. Native shared predicates
run once in a compact SharedPremiseWorld and PortfolioWorld stores only the
resulting premise references needed by private account rules.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from .ontology_change_impact import scope_symbol
from .ontology_worlds import OntologyWorld, world_metadata


ACCOUNT_ENTITY_KINDS = {
    "account",
    "portfolio",
    "position",
    "watchlist",
    "cash",
    "investment-strategy-profile",
    "risk-budget",
    "profit-policy",
    "account-delivery-profile",
    "decision",
    "decision-episode",
    "decision-outcome",
    "hypothesis",
    "hypothesis-proposal",
    "learning",
    "research-run",
    "investment-question",
    "execution-plan",
    "active-opinion",
    "action-candidate",
    "blocked-action",
    "notification-dispatch",
    "cooldown-policy",
    "suppression-policy",
    "novelty-policy",
    "importance-gate",
}

ACCOUNT_RELATION_TYPES = {
    "HOLDS",
    "WATCHES",
    "HAS_POSITION",
    "HAS_CASH",
    "HAS_RISK_BUDGET",
    "HAS_INVESTMENT_STRATEGY",
    "HAS_DELIVERY_PROFILE",
    "HAS_DECISION",
    "HAS_HYPOTHESIS",
    "HAS_EXECUTION_PLAN",
    "HAS_ACTIVE_OPINION",
    "USES_MARKET_WORLD",
}

ACCOUNT_PROPERTY_KEYS = {
    "accountId",
    "portfolioId",
    "isHolding",
    "isWatchlist",
    "averagePrice",
    "quantity",
    "sellableQuantity",
    "marketValue",
    "positionWeight",
    "positionAccountWeight",
    "profitLoss",
    "profitLossRate",
    "positionRole",
    "positionIntent",
    "investmentStrategyProfile",
    "investmentStrategyProfileLabel",
    "riskBudget",
    "deliveryProfile",
    "notificationPreference",
    "activeInvestmentOpinion",
    "executionPlan",
    "decisionStage",
}

# A shared world is a reusable real-world graph, never an account policy or
# cognitive workspace. Bump this value whenever the projection contract
# becomes stricter so existing shared Manifests are rebuilt rather than
# preserving a legacy property slice indefinitely.
SHARED_WORLD_PROJECTION_CONTRACT_VERSION = "shared-world-projection-v5"


def _property_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


_SHARED_WORLD_PRIVATE_PROPERTY_KEYS = {
    _property_key(value)
    for value in ACCOUNT_PROPERTY_KEYS
} | {
    "activerelationcontext",
    "activeinvestmentopinion",
    "actioncandidate",
    "blockedaction",
    "decisionbasis",
    "decisionstage",
    "deliveryprofile",
    "fairvalue",
    "holdingactionpolicy",
    "judgementevidenceusable",
    "marginofsafety",
    "notificationpreference",
    "positionintent",
    "positionrole",
    "relationrules",
    "relationrulescore",
    "targetpositionrole",
}

_SHARED_WORLD_PRIVATE_PROPERTY_PREFIXES = (
    "activetbox",
    "addbuy",
    "ai",
    "alert",
    "cooldown",
    "decision",
    "defaultholding",
    "executionplan",
    "hypothesis",
    "investmentstrategy",
    "model",
    "notification",
    "novelty",
    "profitpolicy",
    "prompt",
    "riskbudget",
    "strategy",
    "suppression",
    "targetposition",
)


def shared_world_property_allowed(key: object, relation: bool = False) -> bool:
    """Reject account policy and AI working state before a graph is shared."""
    raw = str(key or "")
    compact = _property_key(raw)
    if raw in ACCOUNT_PROPERTY_KEYS:
        return False
    if raw in MARKET_PROJECTION_LIFECYCLE_KEYS:
        return False
    if raw in SHARED_WORLD_HEAVY_PROPERTY_KEYS:
        return False
    if relation and raw in SHARED_WORLD_RELATION_EXCLUDED_PROPERTY_KEYS:
        return False
    if compact in _SHARED_WORLD_PRIVATE_PROPERTY_KEYS:
        return False
    return not any(compact.startswith(prefix) for prefix in _SHARED_WORLD_PRIVATE_PROPERTY_PREFIXES)

# MarketWorld is a current shared observation surface, not a copy of an
# account's complete cognitive history.  Keep market identity, observable
# state, external evidence metadata and data-quality facts only.  Decisions,
# hypothesis lifecycles, research workflows and valuation workpapers remain in
# their owning PortfolioWorld/MySQL history and may contribute a compact,
# verified relationship to KnowledgeWorld instead.
MARKET_WORLD_ENTITY_KINDS = {
    "company",
    "security",
    "security-line",
    "instrument",
    "stock",
    "etf",
    "adr",
    "depositary-receipt",
    "leveraged-etf",
    "inverse-etf",
    "single-stock-etf",
    "market",
    "market-index",
    "market-proxy",
    "market-proxy-instrument",
    "market-proxy-observation",
    "relative-performance-observation",
    "market-proxy-theme",
    "sector",
    "industry",
    "currency",
    "price-metric",
    "technical-metric",
    "technical-indicator",
    "price-path",
    "trend-scenario",
    "key-level",
    "flow-metric",
    "flow-observation",
    "investor-flow",
    "orderbook",
    "liquidity-metric",
    "liquidity-observation",
    "slippage-estimate",
    "execution-metric",
    "volume-metric",
    "temporal-window",
    "temporal-observation",
    "temporal-feature-snapshot-reference",
    "statistical-model-release",
    "statistical-model-signal",
    "signal-eligibility-assessment",
    "market-session-phase",
    "fact-change",
    "data-latency",
    "external-signal",
    "macro-indicator",
    "interest-rate",
    "yield-curve",
    "fx-rate",
    "crypto-asset",
    "crypto-market-signal",
    "crypto-exposure",
    "data-source",
    "data-quality",
    "data-freshness",
    "data-availability-assessment",
    "coverage-gap",
    "news-article",
    "disclosure-filing",
    "news-topic",
    "event-cluster",
}

# Keep current market observations in MarketWorld. Durable issuer, ADR,
# leverage and exposure topology belongs to KnowledgeWorld, which prevents a
# quote refresh from rewriting research/fundamental relationships.
MARKET_WORLD_RELATION_TYPES = {
    "AFFECTS",
    "BELONGS_TO",
    "CHANGES_FACT",
    "CONFIRMS_SIGNAL",
    "DENOMINATED_IN",
    "HAS_COVERAGE_GAP",
    "HAS_CRYPTO_EXPOSURE",
    "HAS_DATA_FRESHNESS",
    "HAS_DATA_QUALITY",
    "HAS_DATA_AVAILABILITY",
    "HAS_EVIDENCE",
    "HAS_EXECUTION_METRIC",
    "HAS_EXTERNAL_SIGNAL",
    "HAS_FX_EXPOSURE",
    "HAS_MARKET_PROXY_PROFILE",
    "HAS_OBSERVATION",
    "HAS_PRICE",
    "HAS_PRICE_PATH",
    "HAS_PROVENANCE",
    "HAS_RATE_SENSITIVITY",
    "HAS_SECURITY_LINE",
    "HAS_TECHNICAL_INDICATOR",
    "HAS_TEMPORAL_WINDOW",
    "HAS_MODEL_SIGNAL",
    "GENERATED_BY_MODEL_RELEASE",
    "BASED_ON_FEATURE_SNAPSHOT",
    "HAS_SIGNAL_ELIGIBILITY",
    "HAS_RELATIVE_PERFORMANCE",
    "HAS_TRADE_FLOW",
    "ISSUES",
    "ISSUES_SECURITY_LINE",
    "MENTIONS_INSTRUMENT",
    "OBSERVED_FROM",
    "OBSERVES_MARKET_PROXY",
    "OCCURS_IN_SESSION_PHASE",
    "PRECEDES",
    "COMPARES_WITH_MARKET_PROXY",
    "PROXIES_THEME",
    "REPRESENTS_ECONOMIC_CLAIM",
    "REPRESENTS_INSTRUMENT",
    "REPRESENTS_STOCK",
    "TRACKS_UNDERLYING",
    "TRADED_IN",
    "WEIGHTED_BY_DATA_STATE",
    "WINDOW_CONTAINS_OBSERVATION",
}

# Long article bodies, raw vendor payloads and AI working packets belong in
# their source repositories.  A shared graph needs stable provenance and a
# bounded summary, not a second multi-megabyte document store.
SHARED_WORLD_HEAVY_PROPERTY_KEYS = {
    "rawPayload",
    "sourcePayload",
    "payload",
    "content",
    "body",
    "html",
    "fullText",
    "articleText",
    "documentText",
    "rawContent",
    "aiPrompt",
    "aiResponse",
    "reasoningCards",
    "researchPlan",
    "hypothesisPacket",
    "sourceRows",
}

# Article/entity analysis is attached to the evidence node. Repeating its
# complete link map and quality packet on every edge multiplies both MySQL
# outbox size and TypeDB relation storage without adding a new graph fact.
SHARED_WORLD_RELATION_EXCLUDED_PROPERTY_KEYS = {
    "activeTboxEntityCount",
    "activeTboxRelationCount",
    "activeTboxSource",
    "activeTboxVersion",
    "aiAnalysisModel",
    "aiAnalysisVersion",
    "aiImpactLabelKo",
    "aiImpactPolarity",
    "aiInfluenceLabel",
    "aiNeedsReview",
    "analysisConflict",
    "analysisConflictAiPolarity",
    "analysisConflictExistingPolarity",
    "analysisConflictReasonKo",
    "analysisConflictSource",
    "analysisSummary",
    "analysisVersion",
    "entityLinks",
    "matchedAliases",
    "marketTopics",
    "mentionedPeers",
    "ontologyRelations",
    "qualityGate",
    "topicTags",
}

# A portfolio projection can already carry an immutable local ABox manifest
# when it is handed to the shared-world projector. Those identifiers describe
# the source PortfolioWorld, not the shared market fact, and must never become
# an explicit MarketWorld scope on the next projection.
MARKET_PROJECTION_LIFECYCLE_KEYS = {
    "aboxScopeId",
    "aboxScopeType",
    "aboxScopeFamily",
    "aboxSnapshotId",
    "scopeGenerationId",
    "scopeGenerationIds",
    "scopeFingerprints",
    "scopePlan",
    "scopedAboxManifestVersion",
    "snapshotId",
    "worldviewManifestId",
    "materialFingerprint",
    "projectionRunId",
    "persistenceMode",
    "scopeTopologyVersion",
    "scopeDelta",
    "inferenceImpactPlan",
    "inferenceTargetSymbols",
    "worldId",
    "worldType",
    "tenantId",
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _observed_at(value: object) -> str:
    return _clean(value).replace("+00:00", "Z")


def _observation_epoch(value: object) -> Optional[float]:
    stamp = _observed_at(value)
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _market_observed_at(properties: Dict[str, object]) -> str:
    values = dict(properties or {})
    for key in [
        "marketObservedAt",
        "sourceObservedAt",
        "sourceAsOf",
        "observedAt",
        "publishedAt",
        "publishedDate",
        "filingDate",
        "latestTradingDay",
        "lastUpdated",
        "updatedAt",
        "sourceFetchedAt",
        "fetchedAt",
        "asOf",
    ]:
        value = _observed_at(values.get(key))
        if value:
            return value
    return ""


def _graph_observed_at(graph: PortfolioOntology, fallback: object = "") -> str:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    declared = _observed_at(
        fallback
        or worldview.get("marketObservedAt")
        or worldview.get("asOf")
        or worldview.get("generatedAt")
    )
    if declared:
        return declared
    # A monitoring snapshot can be reconstructed from cached source facts
    # without a single top-level clock. Use the newest provider observation,
    # never the worker's projection time, so downstream freshness and
    # retention remain honest after a delayed queue retry.
    newest = (None, "")
    for item in list(getattr(graph, "entities", []) or []) + list(getattr(graph, "relations", []) or []) + list(getattr(graph, "evidence", []) or []):
        properties = getattr(item, "properties", None)
        if properties is None:
            properties = getattr(item, "value", None)
        stamp = _market_observed_at(properties or {})
        epoch = _observation_epoch(stamp)
        if epoch is not None and (newest[0] is None or epoch > newest[0]):
            newest = (epoch, stamp)
    return newest[1]


def is_account_entity(entity: OntologyEntity) -> bool:
    kind = _clean(entity.kind).lower()
    if kind in ACCOUNT_ENTITY_KINDS:
        return True
    return any(token in kind for token in ("position", "portfolio", "decision", "hypothesis", "execution", "learning"))


def is_market_entity(entity: OntologyEntity) -> bool:
    """Return whether one ABox node belongs in the latest shared market view."""
    if is_account_entity(entity):
        return False
    kind = _clean(entity.kind).lower()
    if kind in MARKET_WORLD_ENTITY_KINDS:
        return True
    values = dict(entity.properties or {})
    # New market concepts may arrive before this whitelist is extended. Only
    # an explicit source-side opt-in is allowed. ``marketObservationShared``
    # is deliberately not used here because it is stamped by this projection;
    # accepting it would feed a prior MarketWorld back into a PortfolioWorld
    # projection and gradually reintroduce unrelated facts.
    return values.get("shareToMarketWorld") is True


def is_market_relation(relation: OntologyRelation) -> bool:
    """Only explicit latest-observation relationships enter MarketWorld."""
    return _clean(relation.relation_type).upper() in MARKET_WORLD_RELATION_TYPES


def market_properties(
    properties: Dict[str, object],
    world: OntologyWorld,
    observed_at: object = "",
    relation: bool = False,
) -> Dict[str, object]:
    values = {
        key: deepcopy(value)
        for key, value in dict(properties or {}).items()
        if shared_world_property_allowed(key, relation=relation)
    }
    values.update(world_metadata(world))
    values["marketObservationShared"] = True
    values["marketOwnership"] = "shared"
    # ``observed_at`` is the portfolio projection clock. It proves that this
    # worker ran, but it does not prove every independent market fact changed.
    # Copying it into every fact made an unchanged snapshot advance every
    # MarketWorld scope generation. Only a source clock belongs to the fact;
    # the projection clock remains on the MarketWorld worldview.
    stamp = _market_observed_at(values)
    if stamp:
        values["marketObservedAt"] = stamp
    else:
        values.pop("marketObservedAt", None)
    return values


def market_entity(entity: OntologyEntity, world: OntologyWorld, observed_at: object = "") -> OntologyEntity:
    return OntologyEntity(
        entity.entity_id,
        entity.label,
        entity.kind,
        market_properties(entity.properties, world, observed_at),
    )


def market_relation(relation: OntologyRelation, world: OntologyWorld, observed_at: object = "") -> OntologyRelation:
    return OntologyRelation(
        relation.source,
        relation.target,
        relation.relation_type,
        relation.weight,
        list(relation.evidence_ids or []),
        market_properties(relation.properties, world, observed_at, relation=True),
    )


def market_evidence(evidence: OntologyEvidence, world: OntologyWorld, observed_at: object = "") -> OntologyEvidence:
    return OntologyEvidence(
        evidence.evidence_id,
        evidence.subject,
        evidence.kind,
        evidence.source,
        evidence.summary,
        market_properties(evidence.value, world, observed_at),
        evidence.evidence_role,
        evidence.data_state,
    )


def build_market_world_graph(
    source_graph: PortfolioOntology,
    world: OntologyWorld,
    observed_at: object = "",
) -> PortfolioOntology:
    """Extract shareable instruments, market observations and external evidence.

    The extractor is deliberately conservative.  A relation survives only
    when both endpoints are shareable, so a stock's market state can be shared
    while its account position and investor-specific decision context cannot.
    """
    observation_time = _graph_observed_at(source_graph, observed_at)
    source_entities = list(source_graph.entities or [])
    kept_entities = [
        market_entity(entity, world, observation_time)
        for entity in source_entities
        if is_market_entity(entity)
        and _clean((entity.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ]
    kept_ids = {item.entity_id for item in kept_entities}
    kept_relations = [
        market_relation(relation, world, observation_time)
        for relation in list(source_graph.relations or [])
        if relation.source in kept_ids
        and relation.target in kept_ids
        and is_market_relation(relation)
        and _clean(relation.relation_type).upper() not in ACCOUNT_RELATION_TYPES
        and _clean((relation.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ]
    kept_evidence = [
        market_evidence(evidence, world, observation_time)
        for evidence in list(source_graph.evidence or [])
        if evidence.subject in kept_ids
        and _clean((evidence.value or {}).get("ontologyBox") or "ABox") == "ABox"
    ]
    return PortfolioOntology(
        world.world_id,
        entities=kept_entities,
        relations=kept_relations,
        evidence=kept_evidence,
        worldview={
            **world_metadata(world),
            "marketWorldProjection": True,
            "marketWorldProjectionMode": "shared-market-observations",
            "sharedWorldProjectionContractVersion": SHARED_WORLD_PROJECTION_CONTRACT_VERSION,
            "marketContextMode": "shared-market-world-direct-premises",
            "sourcePortfolioWorldId": str((source_graph.worldview or {}).get("worldId") or ""),
            "marketObservedAt": observation_time,
            "marketProjectionFilter": {
                "version": "latest-observation-v2",
                "sourceEntityCount": len(source_entities),
                "includedEntityCount": len(kept_entities),
                "excludedEntityCount": len(source_entities) - len(kept_entities),
            },
        },
        prompt=source_graph.prompt,
    )


def relation_key(relation: OntologyRelation) -> Tuple[str, str, str]:
    return (str(relation.source), str(relation.target), str(relation.relation_type).upper())


def market_symbol(entity: OntologyEntity) -> str:
    return _clean((entity.properties or {}).get("symbol")).upper()


def market_item_observation_epoch(item) -> Optional[float]:
    properties = getattr(item, "properties", None)
    if properties is None:
        properties = getattr(item, "value", None)
    return _observation_epoch(_market_observed_at(properties or {}))


def compact_market_world_graph(
    graph: PortfolioOntology,
    retention_hours: float = 0,
    max_symbols: int = 0,
    observed_at: object = "",
) -> PortfolioOntology:
    """Bound shared-world growth without removing fresh observations.

    The shared market graph receives partial updates from independent account
    cycles.  It keeps the latest observation for an instrument, but stale
    symbols must not remain live forever solely because another account no
    longer watches them.  Pruning is based on source observation time rather
    than TypeDB write time, so a delayed worker cannot make stale facts look
    fresh merely by re-projecting them.
    """
    try:
        hours = max(0.0, float(retention_hours or 0))
    except (TypeError, ValueError):
        hours = 0.0
    try:
        symbol_limit = max(0, int(max_symbols or 0))
    except (TypeError, ValueError):
        symbol_limit = 0
    reference_epoch = _observation_epoch(observed_at) or _observation_epoch(
        dict(graph.worldview or {}).get("marketObservedAt")
    )
    if reference_epoch is None:
        known = [
            market_item_observation_epoch(item)
            for item in list(graph.entities or [])
        ]
        reference_epoch = max((value for value in known if value is not None), default=None)
    cutoff = reference_epoch - hours * 3600 if reference_epoch is not None and hours > 0 else None

    entity_rows = list(graph.entities or [])
    entity_epoch = {item.entity_id: market_item_observation_epoch(item) for item in entity_rows}
    stale_entity_ids = {
        item.entity_id
        for item in entity_rows
        if cutoff is not None
        and entity_epoch.get(item.entity_id) is not None
        and entity_epoch[item.entity_id] < cutoff
    }
    retained_entities = [item for item in entity_rows if item.entity_id not in stale_entity_ids]

    pruned_symbol_ids: Set[str] = set()
    if symbol_limit:
        symbol_epochs: Dict[str, Optional[float]] = {}
        for item in retained_entities:
            symbol = market_symbol(item)
            if not symbol:
                continue
            observed = entity_epoch.get(item.entity_id)
            if symbol not in symbol_epochs or (observed or float("-inf")) > (symbol_epochs[symbol] or float("-inf")):
                symbol_epochs[symbol] = observed
        ranked_symbols = sorted(
            symbol_epochs,
            key=lambda symbol: (symbol_epochs[symbol] is not None, symbol_epochs[symbol] or float("-inf"), symbol),
            reverse=True,
        )
        allowed_symbols = set(ranked_symbols[:symbol_limit])
        for item in retained_entities:
            symbol = market_symbol(item)
            if symbol and symbol not in allowed_symbols:
                pruned_symbol_ids.add(item.entity_id)
        retained_entities = [item for item in retained_entities if item.entity_id not in pruned_symbol_ids]

    valid_ids = {item.entity_id for item in retained_entities}
    relation_rows = list(graph.relations or [])
    evidence_rows = list(graph.evidence or [])
    stale_relation_keys = {
        relation_key(item)
        for item in relation_rows
        if cutoff is not None
        and market_item_observation_epoch(item) is not None
        and market_item_observation_epoch(item) < cutoff
    }
    stale_evidence_ids = {
        item.evidence_id
        for item in evidence_rows
        if cutoff is not None
        and market_item_observation_epoch(item) is not None
        and market_item_observation_epoch(item) < cutoff
    }
    retained_relations = [
        item for item in relation_rows
        if item.source in valid_ids
        and item.target in valid_ids
        and relation_key(item) not in stale_relation_keys
    ]
    retained_evidence = [
        item for item in evidence_rows
        if item.subject in valid_ids and item.evidence_id not in stale_evidence_ids
    ]
    removed_entity_count = len(entity_rows) - len(retained_entities)
    worldview = {
        **dict(graph.worldview or {}),
        "marketWorldRetention": {
            "retentionHours": hours,
            "maxSymbols": symbol_limit,
            "referenceObservedAt": _observed_at(observed_at) or _observed_at(dict(graph.worldview or {}).get("marketObservedAt")),
            "removedStaleEntityCount": len(stale_entity_ids),
            "removedStaleRelationCount": len(stale_relation_keys),
            "removedStaleEvidenceCount": len(stale_evidence_ids),
            "removedSymbolLimitEntityCount": len(pruned_symbol_ids),
            "removedEntityCount": removed_entity_count,
        },
    }
    return PortfolioOntology(
        graph.portfolio_id,
        entities=retained_entities,
        relations=retained_relations,
        evidence=retained_evidence,
        worldview=worldview,
        prompt=graph.prompt,
    )


def merge_market_world_graph(
    existing: Optional[PortfolioOntology],
    update: PortfolioOntology,
    retention_hours: float = 0,
    max_symbols: int = 0,
    observed_at: object = "",
) -> PortfolioOntology:
    """Merge a partial account observation into the durable MarketWorld.

    An account may only observe its own holdings/watchlist.  Replacing the
    whole shared world with that subset would erase another account's symbols,
    so incoming entities, relations and evidence replace matching identities
    while untouched observations remain active.
    """
    existing = existing or PortfolioOntology(update.portfolio_id)
    entities = {item.entity_id: item for item in list(existing.entities or [])}
    entities.update({item.entity_id: item for item in list(update.entities or [])})
    relations = {relation_key(item): item for item in list(existing.relations or [])}
    relations.update({relation_key(item): item for item in list(update.relations or [])})
    evidence = {item.evidence_id: item for item in list(existing.evidence or [])}
    evidence.update({item.evidence_id: item for item in list(update.evidence or [])})
    valid_ids: Set[str] = set(entities)
    relations = {
        key: item
        for key, item in relations.items()
        if item.source in valid_ids and item.target in valid_ids
    }
    evidence = {
        key: item
        for key, item in evidence.items()
        if item.subject in valid_ids
    }
    merged = PortfolioOntology(
        update.portfolio_id,
        entities=list(entities.values()),
        relations=list(relations.values()),
        evidence=list(evidence.values()),
        worldview={**dict(existing.worldview or {}), **dict(update.worldview or {})},
        prompt=update.prompt or existing.prompt,
    )
    return compact_market_world_graph(
        merged,
        retention_hours=retention_hours,
        max_symbols=max_symbols,
        observed_at=observed_at or _graph_observed_at(update),
    )


def market_scope_plan_with_observation_times(
    graph: PortfolioOntology,
    scope_plan: Iterable[object],
) -> List[Dict[str, object]]:
    """Attach the newest source observation time to each MarketWorld scope.

    Scope generations intentionally depend on market facts, not on collection
    time. The manifest still needs a source clock for retention and freshness,
    so keep that clock as non-material scope metadata. A scope without an
    explicit source clock remains untracked rather than being marked fresh
    merely because a worker replayed it.
    """
    latest: Dict[str, Tuple[float, str]] = {}

    def record(properties: Mapping[str, object]) -> None:
        values = dict(properties or {})
        scope_id = _clean(values.get("aboxScopeId"))
        stamp = _market_observed_at(values)
        epoch = _observation_epoch(stamp)
        if not scope_id or not stamp or epoch is None:
            return
        previous = latest.get(scope_id)
        if previous is None or epoch > previous[0]:
            latest[scope_id] = (epoch, stamp)

    for entity in list(graph.entities or []):
        record(entity.properties or {})
    for relation in list(graph.relations or []):
        record(relation.properties or {})
    for evidence in list(graph.evidence or []):
        record(evidence.value or {})

    enriched: List[Dict[str, object]] = []
    for item in scope_plan or []:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        scope_id = _clean(row.get("scopeId"))
        observed = latest.get(scope_id)
        if observed:
            row["observedAt"] = observed[1]
        else:
            row.pop("observedAt", None)
        enriched.append(row)
    return enriched


def merge_market_world_scope_manifest(
    active_metadata: Mapping[str, object],
    incoming_scope_plan: Iterable[object],
    observed_at: object = "",
    retention_hours: float = 0,
    max_symbols: int = 0,
) -> Dict[str, object]:
    """Merge a MarketWorld's manifest metadata without loading every live fact.

    MarketWorld is a shared, append-and-refresh observation surface.  Its
    scoped Manifest already records the active generation for each symbol
    family, so a new account observation only needs to replace the scopes it
    owns.  Re-reading all active nodes and relations for that merge makes
    every portfolio projection scale with historical MarketWorld size and can
    starve portfolio-native inference.

    This helper keeps previous scopes from the active Manifest, replaces the
    incoming scopes, and retires only observations with an explicit source
    timestamp.  Untimestamped legacy scopes are deliberately retained rather
    than guessed stale; they can be reconciled safely when their source sends
    a later observation.
    """
    active = dict(active_metadata or {})
    previous_plan = {
        _clean(item.get("scopeId")): dict(item)
        for item in active.get("scopePlan") or []
        if isinstance(item, Mapping) and _clean(item.get("scopeId"))
    }
    incoming = {
        _clean(item.get("scopeId")): dict(item)
        for item in incoming_scope_plan or []
        if isinstance(item, Mapping) and _clean(item.get("scopeId"))
    }
    merged = {**previous_plan, **incoming}

    raw_observed = active.get("marketScopeObservedAt")
    scope_observed_at = {
        _clean(scope_id): _observed_at(stamp)
        for scope_id, stamp in dict(raw_observed or {}).items()
        if _clean(scope_id) and _observed_at(stamp)
    }
    changed_incoming_scope_ids: List[str] = []
    reused_incoming_scope_ids: List[str] = []
    observation_refreshed_scope_ids: List[str] = []
    for scope_id, item in incoming.items():
        previous = previous_plan.get(scope_id) or {}
        changed = (
            not previous
            or _clean(previous.get("fingerprint")) != _clean(item.get("fingerprint"))
            or _clean(previous.get("generationId")) != _clean(item.get("generationId"))
        )
        if changed:
            changed_incoming_scope_ids.append(scope_id)
        else:
            reused_incoming_scope_ids.append(scope_id)

        source_stamp = _observed_at(item.get("observedAt"))
        source_epoch = _observation_epoch(source_stamp)
        if source_epoch is None:
            continue
        previous_stamp = scope_observed_at.get(scope_id, "")
        previous_epoch = _observation_epoch(previous_stamp)
        if previous_epoch is None or source_epoch > previous_epoch:
            scope_observed_at[scope_id] = source_stamp
            observation_refreshed_scope_ids.append(scope_id)

    stamp = _observed_at(observed_at)

    try:
        retention = max(0.0, float(retention_hours or 0))
    except (TypeError, ValueError):
        retention = 0.0
    try:
        symbol_limit = max(0, int(max_symbols or 0))
    except (TypeError, ValueError):
        symbol_limit = 0

    reference_epoch = _observation_epoch(stamp) or max(
        (epoch for epoch in (_observation_epoch(value) for value in scope_observed_at.values()) if epoch is not None),
        default=None,
    )
    cutoff = reference_epoch - retention * 3600 if reference_epoch is not None and retention > 0 else None
    scope_ids_by_symbol: Dict[str, Set[str]] = {}
    symbol_epochs: Dict[str, Optional[float]] = {}
    for scope_id in merged:
        symbol = scope_symbol(scope_id)
        if not symbol:
            continue
        scope_ids_by_symbol.setdefault(symbol, set()).add(scope_id)
        epoch = _observation_epoch(scope_observed_at.get(scope_id))
        if symbol not in symbol_epochs or (epoch is not None and (symbol_epochs[symbol] is None or epoch > symbol_epochs[symbol])):
            symbol_epochs[symbol] = epoch

    retired_symbols: Set[str] = set()
    if cutoff is not None:
        retired_symbols.update(
            symbol
            for symbol, epoch in symbol_epochs.items()
            if epoch is not None and epoch < cutoff
        )
    retained_symbols = [symbol for symbol in scope_ids_by_symbol if symbol not in retired_symbols]
    if symbol_limit and len(retained_symbols) > symbol_limit:
        # Only timestamped symbols are eligible for capacity retirement. An
        # untracked legacy fact is safer to retain than silently discard.
        capacity_candidates = sorted(
            [symbol for symbol in retained_symbols if symbol_epochs.get(symbol) is not None],
            key=lambda symbol: (symbol_epochs.get(symbol) or float("-inf"), symbol),
        )
        overflow = len(retained_symbols) - symbol_limit
        retired_symbols.update(capacity_candidates[:overflow])

    retired_scope_ids = sorted({
        scope_id
        for symbol in retired_symbols
        for scope_id in scope_ids_by_symbol.get(symbol, set())
        if scope_id not in incoming
    })
    for scope_id in retired_scope_ids:
        merged.pop(scope_id, None)
        scope_observed_at.pop(scope_id, None)

    scope_plan = [merged[scope_id] for scope_id in sorted(merged)]
    scope_generations = {
        scope_id: _clean(item.get("generationId"))
        for scope_id, item in merged.items()
        if _clean(item.get("generationId"))
    }
    scope_fingerprints = {
        scope_id: _clean(item.get("fingerprint"))
        for scope_id, item in merged.items()
        if _clean(item.get("fingerprint"))
    }
    family_counts: Dict[str, int] = {}
    for item in scope_plan:
        family = _clean(item.get("scopeFamily")) or "reference"
        family_counts[family] = family_counts.get(family, 0) + 1
    material_payload = {
        "scopeGenerations": dict(sorted(scope_generations.items())),
        "scopeFingerprints": dict(sorted(scope_fingerprints.items())),
    }
    material_fingerprint = hashlib.sha256(
        json.dumps(material_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "scopePlan": scope_plan,
        "scopeGenerationIds": scope_generations,
        "scopeFingerprints": scope_fingerprints,
        "scopeFamilyCounts": dict(sorted(family_counts.items())),
        "marketScopeObservedAt": dict(sorted(scope_observed_at.items())),
        "marketScopeObservedAtVersion": "source-item-v1",
        "materialFingerprint": material_fingerprint,
        "incomingScopeIds": sorted(incoming),
        "changedIncomingScopeIds": sorted(changed_incoming_scope_ids),
        "reusedIncomingScopeIds": sorted(reused_incoming_scope_ids),
        "observationRefreshedScopeIds": sorted(observation_refreshed_scope_ids),
        "retiredScopeIds": retired_scope_ids,
        "retiredSymbols": sorted(retired_symbols),
        "activeScopeCount": len(scope_plan),
        "activeSymbolCount": len({scope_symbol(scope_id) for scope_id in merged if scope_symbol(scope_id)}),
        "retentionHours": retention,
        "maxSymbols": symbol_limit,
        "incremental": True,
    }


def market_world_coverage(graph: PortfolioOntology) -> Dict[str, object]:
    symbols = sorted({
        _clean((entity.properties or {}).get("symbol")).upper()
        for entity in list(graph.entities or [])
        if _clean((entity.properties or {}).get("symbol"))
    })
    return {
        "entityCount": len(graph.entities or []),
        "relationCount": len(graph.relations or []),
        "evidenceCount": len(graph.evidence or []),
        "symbolCount": len(symbols),
        "symbols": symbols[:200],
        "retention": dict((graph.worldview or {}).get("marketWorldRetention") or {}),
    }
